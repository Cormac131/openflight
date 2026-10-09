#!/usr/bin/env python3
"""Bench check of the IWR6843's lateral and height measurement.

Reads a manifest of captures (an empty scene, a reflector at taped lateral
offsets and heights, optionally the reflector swept through the tee's range),
runs each through the firmware's own angle code and prints where the board
put the reflector against where it was taped. With enough static positions
it fits the azimuth zero offset; --save makes it this board's calibration
(~/.config/openflight/iwr6843_calibration.json), which the kiosk then loads
without being told. See docs/iwr6843/azimuth-check.md for the procedure.

    uv run python scripts/iwr6843/azimuth_check.py bench/manifest.json
    uv run python scripts/iwr6843/azimuth_check.py bench/manifest.json --save
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openflight.iwr6843.azimuth_check import (
    Manifest,
    calibration_with_offset,
    format_report,
    run_check,
)
from openflight.iwr6843.board_calibration import BoardCalibration
from openflight.iwr6843.calibration import BOARD_CAL_PATH, resolve_calibration_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", type=Path, help="bench manifest JSON")
    parser.add_argument(
        "--cal",
        type=Path,
        default=None,
        help="calibration to start from (default: this board's when measured, else the reference)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="report JSON (default: azimuth_check_report.json beside the manifest)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        nargs="?",
        const=BOARD_CAL_PATH,
        default=None,
        help=f"save the calibration with the fitted azimuth offset as this board's ({BOARD_CAL_PATH}), "
        "or to the path given; an existing file is kept as <name>.prev",
    )
    args = parser.parse_args(argv)

    manifest = Manifest.load(args.manifest)
    cal_path = resolve_calibration_path(args.cal)
    print(f"Calibration: {cal_path}")
    board = BoardCalibration.from_file(cal_path)
    report = run_check(manifest, board)
    print(format_report(report))

    report_path = args.report or args.manifest.parent / "azimuth_check_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(f"\nReport: {report_path}")

    if args.save is not None:
        if report.fit is None:
            print("Not writing a calibration: no offset was fitted.", file=sys.stderr)
            return 1
        if not report.fit.slope_ok:
            print(
                f"Not writing a calibration: slope {report.fit.slope:.2f} says the lateral "
                "scale or sign is wrong, which an offset cannot fix.",
                file=sys.stderr,
            )
            return 1
        raw = json.loads(cal_path.read_text(encoding="utf-8"))
        out = calibration_with_offset(raw, report.fit, args.manifest)
        args.save.parent.mkdir(parents=True, exist_ok=True)
        if args.save.exists():
            previous = args.save.with_name(args.save.name + ".prev")
            previous.write_bytes(args.save.read_bytes())
            print(f"Previous calibration kept as {previous}")
        args.save.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print(f"Calibration with azimuth_offset_rad {report.fit.offset_rad:+.4f}: {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
