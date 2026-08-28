"""Probe the buses for OpenFlight hardware and describe what is attached.

Every probe here is read-only and non-destructive: the point is to answer
"what did the owner actually plug in?" during first boot and on every start,
without disturbing a device that another process may be about to open.

Discriminating the USB-serial devices is the only subtle part. The IWR6843
EVM and the K-LD7 EVAL board both land on ``/dev/ttyUSB*``, so the port name
alone says nothing. They are told apart by USB vendor/product ID:

    IWR6843 EVM   CP2105 dual-port bridge     10c4:ea70
                  XDS110 debug probe          0451:bef3
    K-LD7 EVAL    FT232R                      0403:6001 / 0403:6014
                  CP2102 single-port bridge   10c4:ea60

The CP2105 exposes two interfaces; only the Enhanced one (interface 0) speaks
the L3-dump firmware protocol, so we prefer the port whose USB location ends
in ``:1.0``. When identification is not conclusive an optional active probe
(``probe=True``) asks each candidate's CLI for ``sensorStart``, which only the
IWR6843 firmware answers.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from openflight.inclinometer.lis3dh import LIS3DH
from openflight.ops243 import is_uart_port
from openflight.power.providers.geekworm import GeekwormPowerReader

# USB IDs, lowercase hex without the 0x prefix, as pyserial reports them via
# ``hwid``. Matched on (vid, pid) integers so formatting differences between
# pyserial versions cannot cause a miss.
_IWR6843_USB_IDS = frozenset({(0x10C4, 0xEA70), (0x0451, 0xBEF3)})
_KLD7_USB_IDS = frozenset({(0x0403, 0x6001), (0x0403, 0x6014), (0x10C4, 0xEA60)})

# Not every platform fills in vid/pid, and USB-serial adapters are swapped
# often enough that the ID list will never be complete. These strings, matched
# against the adapter's description and manufacturer, are the backstop.
_KLD7_DESCRIPTION_KEYWORDS = ("ftdi",)

# The CP2105 Enhanced interface — the one the L3-dump firmware talks on.
_CP2105_ENHANCED_LOCATION_SUFFIX = ":1.0"

# udev names installed by scripts/setup/setup_kld7_devices.sh. When present
# they are authoritative: the owner explicitly mapped each radar's role.
_KLD7_VERTICAL_LINK = "/dev/kld7_vertical"
_KLD7_HORIZONTAL_LINK = "/dev/kld7_horizontal"

# The Pi 5 40-pin header UART. /dev/ttyAMA10 is the debug UART and is never
# the radar, so it is deliberately not a candidate.
_UART_OPS243_CANDIDATES = ("/dev/ttyAMA0",)

_I2C_BUS = 1
_LIS3DH_ADDRESSES = (LIS3DH.DEFAULT_ADDRESS, LIS3DH.DEFAULT_ADDRESS + 1)  # 0x18, 0x19

_CAMERA_TOOLS = ("rpicam-hello", "libcamera-hello")


class DeviceKind(str, Enum):
    """The hardware roles OpenFlight can start with."""

    OPS243 = "ops243"
    IWR6843 = "iwr6843"
    KLD7_VERTICAL = "kld7_vertical"
    KLD7_HORIZONTAL = "kld7_horizontal"
    INCLINOMETER = "inclinometer"
    BATTERY = "battery"
    CAMERA = "camera"


@dataclass(frozen=True)
class DetectedDevice:
    """One hardware role and where (or whether) it was found.

    ``address`` is the port path for serial devices and the I2C address in
    ``0x..`` form for I2C devices, so a profile reads the same either way.
    """

    kind: DeviceKind
    present: bool
    address: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form for logs and the first-boot report."""
        return {
            "kind": self.kind.value,
            "present": self.present,
            "address": self.address,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HardwareProfile:
    """Everything a single detection sweep found."""

    devices: tuple[DetectedDevice, ...] = field(default_factory=tuple)

    def get(self, kind: DeviceKind) -> DetectedDevice:
        """Return the entry for ``kind``, or an absent placeholder."""
        for device in self.devices:
            if device.kind is kind:
                return device
        return DetectedDevice(kind=kind, present=False, detail="not probed")

    def has(self, kind: DeviceKind) -> bool:
        """True when ``kind`` was detected."""
        return self.get(kind).present

    def present_kinds(self) -> tuple[DeviceKind, ...]:
        """Kinds that were detected, in probe order."""
        return tuple(d.kind for d in self.devices if d.present)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form for ``--json`` output and the boot report."""
        return {"devices": [d.to_dict() for d in self.devices]}


class _PortLike(Protocol):  # pylint: disable=too-few-public-methods
    """The subset of ``serial.tools.list_ports_common.ListPortInfo`` used here."""

    device: str
    description: Optional[str]
    manufacturer: Optional[str]
    vid: Optional[int]
    pid: Optional[int]
    location: Optional[str]


ComportsFn = Callable[[], Iterable[_PortLike]]
BusFactory = Callable[[int], Any]


def _default_comports() -> Iterable[_PortLike]:
    """Import pyserial lazily so a missing serial stack degrades to "nothing found"."""
    import serial.tools.list_ports  # pylint: disable=import-outside-toplevel

    return serial.tools.list_ports.comports()


def _default_bus_factory(bus_number: int) -> Any:
    """Open an SMBus. Raises on non-Linux hosts, which callers treat as absent."""
    from smbus2 import SMBus  # pylint: disable=import-error,import-outside-toplevel

    return SMBus(bus_number)


def _usb_id(port: _PortLike) -> tuple[Optional[int], Optional[int]]:
    return (getattr(port, "vid", None), getattr(port, "pid", None))


def _describes(port: _PortLike, keywords: Sequence[str]) -> bool:
    """Fallback string match for adapters whose VID/PID pyserial did not fill in."""
    haystack = f"{getattr(port, 'description', '') or ''} {getattr(port, 'manufacturer', '') or ''}"
    haystack = haystack.lower()
    return any(keyword in haystack for keyword in keywords)


def detect_ops243_port(
    comports: Optional[ComportsFn] = None,
    *,
    include_uart: bool = False,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> Optional[str]:
    """Find the OPS243-A.

    Over USB the radar enumerates as CDC-ACM (``/dev/ttyACM*``) on Linux or a
    usbmodem on macOS. The K-LD7 and IWR6843 bridges never do, so the device
    name alone identifies it.

    The GPIO-UART wiring has no USB descriptors to match on. ``/dev/ttyAMA0``
    exists on every Pi with UART enabled, so treating the node as a radar
    would claim an OPS243 on USB-only kits whose cable was unplugged. UART
    is therefore opt-in (``include_uart=True`` / ``OPS243_UART`` in the boot
    config). USB still wins when both are present.
    """
    comports = comports or _default_comports
    try:
        ports = list(comports())
    except Exception:  # pragma: no cover - defensive: broken serial stack
        ports = []

    for port in ports:
        device = getattr(port, "device", "") or ""
        if "ACM" in device or "usbmodem" in device:
            return device

    if include_uart:
        for candidate in _UART_OPS243_CANDIDATES:
            if path_exists(candidate):
                return candidate
    return None


def _iwr6843_candidates(ports: Sequence[_PortLike]) -> list[_PortLike]:
    return [p for p in ports if _usb_id(p) in _IWR6843_USB_IDS]


def _probe_iwr6843_cli() -> Optional[str]:
    """Return the port whose CLI answers ``help`` with an L3-dump command.

    The driver's own scan is reused rather than reimplemented: it knows the
    baud, the DTR/RTS handling the TI EVM needs, and the response to look
    for. It scans every candidate itself, so this is called once for the
    whole sweep rather than once per candidate.
    """
    from openflight.iwr6843.driver import (  # pylint: disable=import-outside-toplevel
        IWR6843Radar,
    )

    try:
        return IWR6843Radar.detect_port()
    except Exception:  # pragma: no cover - probing must never break detection
        return None


def detect_iwr6843_port(
    comports: Optional[ComportsFn] = None,
    *,
    probe: bool = False,
) -> Optional[str]:
    """Find the IWR6843's CLI/dump port.

    With ``probe=False`` (the default, and what boot uses) identification is
    by USB ID only: opening a 1 Mbaud port costs seconds and can upset a
    K-LD7 that is mid-stream. ``probe=True`` additionally confirms the port
    answers the L3-dump CLI, which is what ``diagnose`` and setup want.
    """
    comports = comports or _default_comports
    try:
        ports = list(comports())
    except Exception:  # pragma: no cover - defensive: broken serial stack
        return None

    candidates = _iwr6843_candidates(ports)
    if not candidates:
        return None

    # CP2105 exposes Standard and Enhanced interfaces; only Enhanced carries
    # the dump. Prefer it explicitly rather than trusting enumeration order.
    enhanced = [
        p
        for p in candidates
        if (getattr(p, "location", None) or "").endswith(_CP2105_ENHANCED_LOCATION_SUFFIX)
    ]
    ordered = enhanced or sorted(candidates, key=lambda p: getattr(p, "device", "") or "")

    if probe:
        answered = _probe_iwr6843_cli()
        # Only trust the probe when it agrees with the USB ID. A port that
        # answers but is not a candidate would mean our ID list is wrong,
        # which is worth failing loudly on rather than papering over.
        candidates_by_device = {getattr(p, "device", "") for p in candidates}
        return answered if answered in candidates_by_device else None

    return getattr(ordered[0], "device", None) or None


def detect_kld7_ports(
    comports: Optional[ComportsFn] = None,
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> list[str]:
    """Find K-LD7 angle radars (deprecated hardware, kept for existing builds).

    The udev symlinks win when installed, because they encode which radar the
    owner mounted vertically and which horizontally — an ordering that raw
    enumeration cannot recover. Otherwise fall back to USB ID, excluding any
    port already claimed by the IWR6843.
    """
    comports = comports or _default_comports

    linked = [link for link in (_KLD7_VERTICAL_LINK, _KLD7_HORIZONTAL_LINK) if path_exists(link)]
    if linked:
        return linked

    try:
        ports = list(comports())
    except Exception:  # pragma: no cover - defensive: broken serial stack
        return []

    iwr_devices = {getattr(p, "device", "") for p in _iwr6843_candidates(ports)}
    found: list[str] = []
    for port in ports:
        device = getattr(port, "device", "") or ""
        if not device or device in iwr_devices:
            continue
        # An OPS243 on CDC-ACM is never a K-LD7, whatever it calls itself.
        if "ACM" in device or "usbmodem" in device:
            continue
        if _usb_id(port) in _KLD7_USB_IDS or _describes(port, _KLD7_DESCRIPTION_KEYWORDS):
            found.append(device)
    return sorted(found)


def _i2c_responds(bus_factory: BusFactory, address: int, register: int) -> Optional[int]:
    """Read one register, returning None when the bus or device is absent."""
    bus = None
    try:
        bus = bus_factory(_I2C_BUS)
        return bus.read_byte_data(address, register)
    except Exception:
        return None
    finally:
        if bus is not None:
            try:
                bus.close()
            except Exception:  # pragma: no cover - close failures are not interesting
                pass


def detect_inclinometer(bus_factory: Optional[BusFactory] = None) -> Optional[int]:
    """Return the LIS3DH's I2C address, or None.

    The SDO/SA0 pin selects 0x18 or 0x19, so both are tried. Identity is
    confirmed against WHO_AM_I rather than a bare ACK, because plenty of
    unrelated parts answer at those addresses.
    """
    bus_factory = bus_factory or _default_bus_factory
    for address in _LIS3DH_ADDRESSES:
        value = _i2c_responds(bus_factory, address, LIS3DH.WHO_AM_I)
        if value == LIS3DH.WHO_AM_I_VALUE:
            return address
    return None


def detect_geekworm_battery(bus_factory: Optional[BusFactory] = None) -> Optional[int]:
    """Return the Geekworm fuel gauge's I2C address, or None.

    The MAX17043 has no identity register, so presence is inferred from a
    plausible cell voltage: a 1S lithium pack reads 2.5-4.5 V, which rules
    out both an empty bus (reads fail) and a stuck-high/stuck-low bus.
    """
    bus_factory = bus_factory or _default_bus_factory
    address = GeekwormPowerReader.DEFAULT_ADDRESS
    msb = _i2c_responds(bus_factory, address, GeekwormPowerReader.VCELL_REGISTER)
    if msb is None:
        return None
    # VCELL is 12-bit left-aligned across two bytes at 1.25 mV/LSB; the MSB
    # alone is enough to bound the voltage to within 20 mV.
    voltage_v = (msb << 4) * 0.00125
    if 2.5 <= voltage_v <= 4.5:
        return address
    return None


def detect_pi_camera(
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    which: Callable[[str], Optional[str]] = shutil.which,
    video_glob: Callable[[str], list[str]] = glob.glob,
) -> Optional[str]:
    """Return a description of an attached CSI camera, or None.

    Reported for the first-boot summary only — the camera pipeline is not
    part of the production radar path, so detection never enables it. See
    ``flags.py`` for why.
    """
    for tool in _CAMERA_TOOLS:
        if which(tool) is None:
            continue
        try:
            result = run(
                [tool, "--list-cameras"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = (result.stdout or "") + (result.stderr or "")
        if "no cameras available" in output.lower():
            return None
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("0 :"):
                return stripped
    # No libcamera tooling (a non-Pi host, or a slimmed image): a /dev/video
    # node is weak evidence but better than claiming nothing is attached.
    nodes = sorted(video_glob("/dev/video*"))
    if nodes:
        return f"video node {nodes[0]} (libcamera tooling unavailable)"
    return None


def detect_hardware(
    *,
    comports: Optional[ComportsFn] = None,
    bus_factory: Optional[BusFactory] = None,
    path_exists: Callable[[str], bool] = os.path.exists,
    probe_iwr6843: bool = False,
    include_camera: bool = True,
    include_uart: bool = False,
) -> HardwareProfile:
    """Run every probe once and return the combined profile.

    ``comports`` is called once and shared across the serial probes so a
    single enumeration describes one consistent moment; probing the bus
    repeatedly could otherwise report a device that vanished mid-sweep.
    """
    comports = comports or _default_comports
    try:
        ports = list(comports())
    except Exception:  # pragma: no cover - defensive: broken serial stack
        ports = []

    def _snapshot() -> Iterable[_PortLike]:
        return ports

    devices: list[DetectedDevice] = []

    ops_port = detect_ops243_port(_snapshot, path_exists=path_exists, include_uart=include_uart)
    devices.append(
        DetectedDevice(
            kind=DeviceKind.OPS243,
            present=ops_port is not None,
            address=ops_port,
            detail=(
                ""
                if ops_port is None
                else ("GPIO UART" if is_uart_port(ops_port) else "USB CDC-ACM")
            ),
        )
    )

    iwr_port = detect_iwr6843_port(_snapshot, probe=probe_iwr6843)
    devices.append(
        DetectedDevice(
            kind=DeviceKind.IWR6843,
            present=iwr_port is not None,
            address=iwr_port,
            detail=""
            if iwr_port is None
            else ("CLI confirmed" if probe_iwr6843 else "USB ID match"),
        )
    )

    kld7_ports = detect_kld7_ports(_snapshot, path_exists=path_exists)
    vertical = kld7_ports[0] if kld7_ports else None
    horizontal = kld7_ports[1] if len(kld7_ports) > 1 else None
    kld7_detail = (
        "udev-mapped" if vertical and vertical.startswith("/dev/kld7_") else "USB ID match"
    )
    devices.append(
        DetectedDevice(
            kind=DeviceKind.KLD7_VERTICAL,
            present=vertical is not None,
            address=vertical,
            detail="" if vertical is None else kld7_detail,
        )
    )
    devices.append(
        DetectedDevice(
            kind=DeviceKind.KLD7_HORIZONTAL,
            present=horizontal is not None,
            address=horizontal,
            detail="" if horizontal is None else kld7_detail,
        )
    )

    tilt_address = detect_inclinometer(bus_factory)
    devices.append(
        DetectedDevice(
            kind=DeviceKind.INCLINOMETER,
            present=tilt_address is not None,
            address=None if tilt_address is None else f"0x{tilt_address:02x}",
            detail="" if tilt_address is None else "LIS3DH WHO_AM_I confirmed",
        )
    )

    battery_address = detect_geekworm_battery(bus_factory)
    devices.append(
        DetectedDevice(
            kind=DeviceKind.BATTERY,
            present=battery_address is not None,
            address=None if battery_address is None else f"0x{battery_address:02x}",
            detail="" if battery_address is None else "Geekworm MAX17043 fuel gauge",
        )
    )

    camera = detect_pi_camera() if include_camera else None
    devices.append(
        DetectedDevice(
            kind=DeviceKind.CAMERA,
            present=camera is not None,
            address=None,
            detail=camera or "",
        )
    )

    return HardwareProfile(devices=tuple(devices))
