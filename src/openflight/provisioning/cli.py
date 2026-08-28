"""``openflight-detect-hardware`` — probe the buses and report what to run.

Three output modes, one for each consumer:

* ``--flags`` (default) prints the ``start-kiosk.sh`` arguments one per line
  on stdout, so a shell can read them with ``mapfile -t``. One flag per line
  rather than one quoted line keeps the caller free of ``eval``: a value
  containing a space (a session location, say) cannot re-split. Everything
  human-readable goes to stderr, which keeps that capture clean.
* ``--flags-line`` prints the same flags as one shell-quoted line, for
  pasting into a terminal or into documentation.
* ``--json`` prints the full profile for logs, bug reports, and the UI.
* ``--report`` prints the friendly summary an owner sees on first boot.

``--write PATH`` additionally saves a shell-sourceable env file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .detect import DeviceKind, HardwareProfile, detect_hardware
from .flags import FlagPlan, SiteConfig, profile_to_flags, render_env_file

_HUMAN_NAMES = {
    DeviceKind.OPS243: "OPS243-A Doppler radar",
    DeviceKind.IWR6843: "IWR6843 60 GHz radar",
    DeviceKind.KLD7_VERTICAL: "K-LD7 vertical (deprecated)",
    DeviceKind.KLD7_HORIZONTAL: "K-LD7 horizontal (deprecated)",
    DeviceKind.INCLINOMETER: "LIS3DH inclinometer",
    DeviceKind.BATTERY: "Geekworm UPS battery gauge",
    DeviceKind.CAMERA: "CSI camera",
}


def format_report(profile: HardwareProfile, plan: FlagPlan) -> str:
    """Render the summary shown on the screen after a first boot."""
    lines = ["OpenFlight hardware detection", "=" * 40]
    for device in profile.devices:
        name = _HUMAN_NAMES.get(device.kind, device.kind.value)
        if device.present:
            where = device.address or device.detail or "detected"
            lines.append(f"  [found]   {name:<32} {where}")
        else:
            lines.append(f"  [absent]  {name}")

    lines.append("")
    if plan.notes:
        lines.append("Configuration:")
        lines.extend(f"  - {note}" for note in plan.notes)
        lines.append("")
    if plan.warnings:
        lines.append("Warnings:")
        lines.extend(f"  ! {warning}" for warning in plan.warnings)
        lines.append("")
    lines.append(f"Start flags: {plan.command_line or '(none — defaults are correct)'}")
    return "\n".join(lines)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse the CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="openflight-detect-hardware",
        description="Detect attached OpenFlight hardware and print the matching start flags.",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--flags",
        action="store_true",
        help="Print the start-kiosk.sh flags, one per line (default).",
    )
    output.add_argument(
        "--flags-line",
        action="store_true",
        help="Print the flags as a single shell-quoted line.",
    )
    output.add_argument("--json", action="store_true", help="Print the full profile as JSON.")
    output.add_argument("--report", action="store_true", help="Print a human-readable summary.")
    parser.add_argument(
        "--write",
        metavar="PATH",
        default=None,
        help="Also write a shell-sourceable env file (e.g. /etc/openflight/hardware.env).",
    )
    parser.add_argument(
        "--probe-iwr6843",
        action="store_true",
        help=(
            "Confirm the IWR6843 by asking its CLI, instead of trusting the USB ID. "
            "Slower, and not safe while another process holds the port."
        ),
    )
    parser.add_argument(
        "--no-camera",
        action="store_true",
        help="Skip camera detection (it shells out to libcamera and can be slow).",
    )
    parser.add_argument(
        "--require-ops243",
        action="store_true",
        help="Exit non-zero when no OPS243-A is found.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns 0 unless a required device is missing."""
    args = parse_args(argv)
    site = SiteConfig.from_env()

    profile = detect_hardware(
        probe_iwr6843=args.probe_iwr6843,
        include_camera=not args.no_camera,
        include_uart=site.ops243_uart,
    )
    plan = profile_to_flags(profile, site)

    if args.write:
        path = Path(args.write)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_env_file(profile, plan), encoding="utf-8")
        except OSError as exc:
            print(f"could not write {path}: {exc}", file=sys.stderr)
            return 2

    if args.json:
        payload = {
            "profile": profile.to_dict(),
            "flags": list(plan.flags),
            "warnings": list(plan.warnings),
            "notes": list(plan.notes),
        }
        print(json.dumps(payload, indent=2))
    elif args.report:
        print(format_report(profile, plan))
    else:
        # Flags on stdout, commentary on stderr: the caller is capturing us.
        for warning in plan.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if args.flags_line:
            print(plan.command_line)
        else:
            for flag in plan.flags:
                print(flag)

    if args.require_ops243 and not profile.has(DeviceKind.OPS243):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main(sys.argv[1:]))
