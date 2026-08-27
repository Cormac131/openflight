"""Turn a detected hardware profile into ``start-kiosk.sh`` arguments.

Detection answers "what is attached"; this module answers "what should we
therefore run". They are kept apart because the second question has judgement
in it — a K-LD7 that is plugged in but has no measured mount tilt must *not*
be enabled, and an attached camera must not be enabled at all — and that
judgement is much easier to test in isolation from the buses.

Two classes of setting exist:

* **Detectable** — which radars and sensors are on the buses. Handled here.
* **Site-specific** — the geometry of the owner's hitting bay: radar tilt,
  distance to the net. No probe can recover these, so they come from
  :class:`SiteConfig`, which the SD card image fills from a plain-text file
  on the boot partition that an owner can edit on any computer.

Emitted flags are always *prepended* to the owner's own command line, so an
explicit flag typed by hand still wins.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .detect import DeviceKind, HardwareProfile

# Boot-config keys, read from the environment after the boot partition's
# openflight.conf is sourced. Names match the variables start-kiosk.sh
# already understands so there is only one vocabulary to learn.
_ENV_KLD7_MOUNT_TILT = "KLD7_MOUNT_TILT"
_ENV_KLD7_ANGLE_OFFSET = "KLD7_ANGLE_OFFSET"
_ENV_NET_DISTANCE = "NET_DISTANCE"
_ENV_SESSION_LOCATION = "SESSION_LOCATION"
_ENV_IWR6843_TEE_M = "IWR6843_TEE_M"
_ENV_IWR6843_NET_M = "IWR6843_NET_M"
_ENV_ENABLE_SIM = "OPENFLIGHT_ENABLE_SIM"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class SiteConfig:
    """Install-specific values that cannot be probed for.

    Every field is optional. A missing value never guesses: it either leaves
    the corresponding flag off or, where the flag cannot be safely defaulted
    (K-LD7 mount tilt), disables the whole device with a warning.
    """

    kld7_mount_tilt_deg: Optional[str] = None
    kld7_angle_offset_deg: Optional[str] = None
    net_distance_m: Optional[str] = None
    session_location: Optional[str] = None
    iwr6843_tee_m: Optional[str] = None
    iwr6843_net_m: Optional[str] = None
    enable_sim: bool = False

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "SiteConfig":
        """Read the boot-config values out of the environment."""
        env = os.environ if env is None else env

        def value(key: str) -> Optional[str]:
            raw = (env.get(key) or "").strip()
            return raw or None

        return cls(
            kld7_mount_tilt_deg=value(_ENV_KLD7_MOUNT_TILT),
            kld7_angle_offset_deg=value(_ENV_KLD7_ANGLE_OFFSET),
            net_distance_m=value(_ENV_NET_DISTANCE),
            session_location=value(_ENV_SESSION_LOCATION),
            iwr6843_tee_m=value(_ENV_IWR6843_TEE_M),
            iwr6843_net_m=value(_ENV_IWR6843_NET_M),
            enable_sim=(value(_ENV_ENABLE_SIM) or "").lower() in _TRUTHY,
        )


class _Accumulator:
    """Mutable working state for the planners below.

    The planners each contribute flags, warnings, and notes. Passing one
    object rather than three lists keeps their signatures readable and makes
    it obvious that they append rather than replace.
    """

    def __init__(self) -> None:
        self.flags: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def flag(self, *values: str) -> None:
        """Append one flag, optionally with its value."""
        self.flags.extend(values)

    def warn(self, message: str) -> None:
        """Record something the owner needs to act on."""
        self.warnings.append(message)

    def note(self, message: str) -> None:
        """Record something worth reporting but needing no action."""
        self.notes.append(message)

    def freeze(self) -> "FlagPlan":
        """Convert to the immutable result callers see."""
        return FlagPlan(
            flags=tuple(self.flags),
            warnings=tuple(self.warnings),
            notes=tuple(self.notes),
        )


@dataclass(frozen=True)
class FlagPlan:
    """The arguments to run with, plus everything worth telling the owner."""

    flags: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def command_line(self) -> str:
        """Shell-quoted flags, ready to splice into a command."""
        return " ".join(shlex.quote(flag) for flag in self.flags)


def profile_to_flags(
    profile: HardwareProfile,
    site: Optional[SiteConfig] = None,
) -> FlagPlan:
    """Decide the ``start-kiosk.sh`` flags for a detected profile."""
    site = site or SiteConfig()
    plan = _Accumulator()

    _plan_ops243(profile, plan)
    used_iwr = _plan_iwr6843(profile, site, plan)
    _plan_kld7(profile, site, used_iwr, plan)
    _plan_i2c_peripherals(profile, plan)
    _plan_camera(profile, plan)

    if site.session_location:
        plan.flag("--session-location", site.session_location)
    if site.enable_sim:
        plan.flag("--sim")
        plan.note("Simulator connectors enabled from the boot config.")

    return plan.freeze()


def _plan_ops243(profile: HardwareProfile, plan: _Accumulator) -> None:
    """The OPS243 is not optional — without it there is no ball speed."""
    ops = profile.get(DeviceKind.OPS243)
    if not ops.present:
        plan.warn(
            "No OPS243-A radar found. Check its USB cable, or wire it to the "
            "GPIO UART and pass --radar-port /dev/ttyAMA0."
        )
        return
    # Pin the port explicitly. Auto-detect would usually pick the same device,
    # but recording it means the launch log says which radar was used. An
    # address-less "present" is not expected; passing an empty --radar-port
    # would be worse than falling back to the server's own auto-detect.
    if ops.address:
        plan.flag("--radar-port", ops.address)
    plan.note(f"OPS243-A on {ops.address or 'an unrecorded port'} ({ops.detail}).")


def _plan_iwr6843(profile: HardwareProfile, site: SiteConfig, plan: _Accumulator) -> bool:
    """Enable the 60 GHz radar when present. Returns whether it was enabled."""
    iwr = profile.get(DeviceKind.IWR6843)
    if not iwr.present:
        return False

    plan.flag("--iwr6843")
    if iwr.address:
        plan.flag("--iwr6843-port", iwr.address)
    if site.iwr6843_tee_m:
        plan.flag("--iwr6843-tee-m", site.iwr6843_tee_m)
    if site.iwr6843_net_m:
        plan.flag("--iwr6843-net-m", site.iwr6843_net_m)
    plan.note(f"IWR6843 on {iwr.address} — launch angle and club path enabled.")
    return True


def _plan_kld7(
    profile: HardwareProfile, site: SiteConfig, used_iwr: bool, plan: _Accumulator
) -> None:
    """Enable the legacy angle radars only when they are the best option and safe."""
    vertical = profile.get(DeviceKind.KLD7_VERTICAL)
    if not vertical.present:
        return

    if used_iwr:
        plan.warn(
            "K-LD7 radars detected alongside an IWR6843. The IWR6843 supersedes "
            "them, so the K-LD7s were left disabled. Unplug them, or start with "
            "--kld7 by hand to use them instead."
        )
        return

    if not site.kld7_mount_tilt_deg:
        # A wrong tilt silently biases every launch angle, so there is no safe
        # default to fall back on — better to run without angle data.
        plan.warn(
            "K-LD7 radars detected but KLD7_MOUNT_TILT is not set, so they were "
            "left disabled (a wrong tilt silently biases launch angle). Measure "
            "the radar face tilt and set KLD7_MOUNT_TILT in the boot config."
        )
        return

    plan.flag("--kld7")
    if vertical.address:
        plan.flag("--kld7-port", vertical.address)
    plan.flag("--kld7-mount-tilt", site.kld7_mount_tilt_deg)
    if site.kld7_angle_offset_deg:
        plan.flag("--kld7-angle-offset", site.kld7_angle_offset_deg)
    if site.net_distance_m:
        plan.flag("--net-distance", site.net_distance_m)

    horizontal = profile.get(DeviceKind.KLD7_HORIZONTAL)
    if horizontal.present:
        plan.flag("--kld7-horizontal")
        if horizontal.address:
            plan.flag("--kld7-horizontal-port", horizontal.address)
        plan.note(f"K-LD7 pair on {vertical.address} and {horizontal.address} (deprecated).")
    else:
        plan.note(f"K-LD7 vertical on {vertical.address} (deprecated, no horizontal found).")


def _plan_i2c_peripherals(profile: HardwareProfile, plan: _Accumulator) -> None:
    """Enable the two I2C peripherals, both of which are purely additive."""
    tilt = profile.get(DeviceKind.INCLINOMETER)
    if tilt.present:
        plan.flag("--inclinometer")
        plan.note(f"LIS3DH inclinometer at {tilt.address} — tilt compensation enabled.")

    battery = profile.get(DeviceKind.BATTERY)
    if battery.present:
        plan.flag("--battery", "geekworm")
        plan.note(f"Geekworm UPS fuel gauge at {battery.address} — battery display enabled.")


def _plan_camera(profile: HardwareProfile, plan: _Accumulator) -> None:
    """Report a camera but never enable it.

    The camera estimators are not on the production radar path and their
    dependencies are an optional extra, so auto-enabling one would turn a
    working image into a failing one the moment somebody plugs a camera in.
    """
    camera = profile.get(DeviceKind.CAMERA)
    if camera.present:
        plan.note(
            f"Camera detected ({camera.detail}) but left disabled — "
            "start with --camera-capture to use it."
        )


def render_env_file(profile: HardwareProfile, plan: FlagPlan) -> str:
    """Render the detection result as a shell-sourceable env file.

    Written to ``/etc/openflight/hardware.env`` at first boot so the result of
    a successful detection survives, and so support requests can be answered
    by reading one file instead of re-running probes.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Generated by openflight-detect-hardware. Do not edit by hand —",
        "# it is rewritten on every detection run.",
        f"OPENFLIGHT_DETECTED_AT={stamp}",
        f"OPENFLIGHT_DETECTED_DEVICES="
        f"{shlex.quote(','.join(k.value for k in profile.present_kinds()))}",
        f"OPENFLIGHT_AUTO_FLAGS={shlex.quote(plan.command_line)}",
    ]
    for warning in plan.warnings:
        lines.append(f"# WARNING: {warning}")
    return "\n".join(lines) + "\n"
