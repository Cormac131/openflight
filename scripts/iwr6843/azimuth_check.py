#!/usr/bin/env python3
"""Bench check of the IWR6843's lateral and height measurement.

Reads a manifest of captures (an empty scene, a reflector at taped lateral
offsets and heights, optionally the reflector swept through the tee's range),
runs each through the firmware's own angle code and prints where the board
put the reflector against where it was taped. With enough static positions
it fits the azimuth zero offset; --write-cal saves it into a copy of the
calibration JSON. See docs/iwr6843/azimuth-check.md for the procedure.

    uv run python scripts/iwr6843/azimuth_check.py bench/manifest.json
    uv run python scripts/iwr6843/azimuth_check.py bench/manifest.json \\
        --write-cal config/iwr6843_calibration_board.json
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

DEFAULT_CAL = "config/iwr6843_calibration_reference.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", type=Path, help="bench manifest JSON")
    parser.add_argument("--cal", type=Path, default=Path(DEFAULT_CAL), help="calibration JSON")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="report JSON (default: azimuth_check_report.json beside the manifest)",
    )
    parser.add_argument(
        "--write-cal",
        type=Path,
        default=None,
        help="write the calibration JSON with the fitted azimuth offset here",
    )
    args = parser.parse_args(argv)

    manifest = Manifest.load(args.manifest)
    board = BoardCalibration.from_file(args.cal)
    report = run_check(manifest, board)
    print(format_report(report))

    report_path = args.report or args.manifest.parent / "azimuth_check_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(f"\nReport: {report_path}")

    if args.write_cal is not None:
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
        raw = json.loads(args.cal.read_text(encoding="utf-8"))
        out = calibration_with_offset(raw, report.fit, args.manifest)
        args.write_cal.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print(f"Calibration with azimuth_offset_rad {report.fit.offset_rad:+.4f}: {args.write_cal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
