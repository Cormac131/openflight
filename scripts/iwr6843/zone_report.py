#!/usr/bin/env python3
"""How a swing zone treats the labelled recordings.

Replays every reviewed recording at the kiosk's trigger settings with this
board's calibration and reports, for the club points near impact, the rest of
the club's approach and every stray point the club track took, how many the
zone around the tee keeps and why it refuses the rest. Blank zone options
keep the firmware's starting values (l3_zone.h).

    uv run python scripts/iwr6843/zone_report.py
    uv run python scripts/iwr6843/zone_report.py --dir tests/radar/recordings/golfer_2026-09 \\
        --half-width-m 0.4 --max-height-m 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from openflight.iwr6843 import firmware_replay as fr
from openflight.iwr6843.board_calibration import BoardCalibration
from openflight.iwr6843.calibration import resolve_calibration_path
from openflight.iwr6843.swing_zone import SwingZone
from openflight.iwr6843.zone_report import format_totals, run_report, totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dir",
        type=Path,
        action="append",
        default=None,
        help="recordings folder (repeatable; default: tests/radar/recordings and golfer_2026-09)",
    )
    parser.add_argument(
        "--cal", type=Path, default=None, help="calibration (default: this board's)"
    )
    for name, help_text in (
        ("short-m", "corridor start, metres short of the tee"),
        ("past-m", "corridor end, metres past the tee"),
        ("half-width-m", "either side of the centre line"),
        ("lateral-m", "centre line, metres right of the target line"),
        ("min-height-m", "lowest point above the floor"),
        ("max-height-m", "highest point above the floor"),
    ):
        parser.add_argument(f"--{name}", type=float, default=None, help=help_text)
    parser.add_argument("--json", type=Path, default=None, help="write per-recording results here")
    args = parser.parse_args(argv)

    directories = args.dir or [fr.RECORDINGS_DIR, fr.RECORDINGS_DIR / "golfer_2026-09"]
    cal_path = resolve_calibration_path(args.cal)
    print(f"Calibration: {cal_path}")
    zone = SwingZone(
        short_m=args.short_m,
        past_m=args.past_m,
        half_width_m=args.half_width_m,
        lateral_m=args.lateral_m,
        min_height_m=args.min_height_m,
        max_height_m=args.max_height_m,
    )
    results = run_report(directories, zone, BoardCalibration.from_file(cal_path))
    print(format_totals(results))
    if args.json is not None:
        payload = {
            "calibration": str(cal_path),
            "zone": asdict(zone),
            "totals": {k: asdict(v) for k, v in totals(results).items()},
            "recordings": [
                {"name": r.name, **{k: asdict(v) for k, v in r.tallies.items()}} for r in results
            ],
        }
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Results: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
