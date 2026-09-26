#!/usr/bin/env python3
"""Exercise every CLI command of the IWR6843 L3-dump firmware on a connected board.

Stop the kiosk first; this owns the TI UART. Each check prints PASS, FAIL or
SKIP and the script exits 1 if any check fails. SKIP never fails the run.

Sections (see --list): lifecycle, profiles, readback, trigger, trigger-swing,
solve. The trigger-swing section needs --swing and prompts you to place a
ball and swing; the others run hands-off.

    uv run python scripts/hardware-test/test_iwr_firmware.py
    uv run python scripts/hardware-test/test_iwr_firmware.py --only trigger
    uv run python scripts/hardware-test/test_iwr_firmware.py --swing --tee-m 1.575 --shots 2
    uv run python scripts/hardware-test/test_iwr_firmware.py --json /tmp/fw.json
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "src")

from openflight.iwr6843 import firmware_checks as fc  # noqa: E402
from openflight.iwr6843.calibration import DEFAULT_TEE_RANGE_M  # noqa: E402
from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402

DEFAULT_CONFIG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"


def port_name_error(port: str | None, platform: str) -> str | None:
    """Windows ``COMn`` names are not device paths on the Pi."""
    if not port or platform == "win32":
        return None
    suffix = port[3:]
    if port.upper().startswith("COM") and suffix.isdigit():
        return (
            f"{port} is a Windows port name. On this machine leave --port off, "
            "or pass a device path such as /dev/ttyUSB0."
        )
    return None


def build_parser() -> argparse.ArgumentParser:
    """Command-line flags."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1])
    parser.add_argument("--port", default=None, help="serial port (default: auto-detect)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="profile for the default sections")
    parser.add_argument("--only", default=None, help="comma-separated section names to run")
    parser.add_argument("--list", action="store_true", help="print the catalogue and exit")
    parser.add_argument("--swing", action="store_true", help="run the prompted swing checks")
    parser.add_argument("--shots", type=int, default=2, help="swings to validate with --swing")
    parser.add_argument("--tee-m", type=float, default=DEFAULT_TEE_RANGE_M)
    parser.add_argument(
        "--level", type=float, default=None, help="trigger level; default measures the floor"
    )
    parser.add_argument("--hits", type=int, default=2)
    parser.add_argument(
        "--wait-s", type=float, default=60.0, help="deadline for prompted and polled steps"
    )
    parser.add_argument("--json", default=None, help="write results to this JSON file")
    parser.add_argument("--fail-fast", action="store_true", help="stop at the first FAIL")
    return parser


def print_catalogue(sections: tuple[fc.Section, ...]) -> None:
    """``--list`` output: one line per section, indented check names."""
    for section in sections:
        needs = ", needs --swing" if any(c.needs_swing for c in section.checks) else ""
        print(f"{section.name} (sensor: {section.sensor}{needs})")
        for check in section.checks:
            print(f"  {check.name}")


def _prompt(text: str) -> None:
    input(f"\n>>> {text} ")


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    sections = fc.build_sections(fc.default_profiles(), args.shots)
    if args.list:
        print_catalogue(sections)
        return 0
    only = tuple(name.strip() for name in args.only.split(",")) if args.only else None
    try:
        selected = fc.select_sections(sections, only)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.swing and not any(s.name == "trigger-swing" for s in selected):
        print("error: --swing needs the trigger-swing section in --only", file=sys.stderr)
        return 2
    problem = port_name_error(args.port, sys.platform)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    radar = IWR6843Radar(args.port)
    ctx = fc.Context(
        radar=radar,
        config=args.config,
        tee_m=args.tee_m,
        level=args.level,
        hits=args.hits,
        wait_s=args.wait_s,
        shots=args.shots,
        profiles=fc.default_profiles(),
        prompt=_prompt,
        sleep=time.sleep,
        clock=time.monotonic,
        out=print,
    )
    print(f"IWR6843 on {radar.port}, default profile {args.config}")
    results: list[fc.CheckResult] = []
    interrupted = False
    try:
        results = fc.run(ctx, sections, only=only, swing=args.swing, fail_fast=args.fail_fast)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        interrupted = True
    results.extend(fc.cleanup(ctx))
    radar.close()
    if args.json:
        fc.write_json(results, args.json)
    if interrupted:
        return 130
    failed = sum(1 for r in results if r.status == fc.FAIL)
    print("ALL PASS" if failed == 0 else f"{failed} FAILURE(S) ABOVE")
    return fc.exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
