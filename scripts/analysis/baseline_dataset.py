#!/usr/bin/env python3
"""Freeze the current firmware's per-shot numbers as a baseline dataset (phase 0).

Reads OpenFlight session logs and writes one CSV row per shot: OPS ball and
club speed, the IWR6843 onboard result (verdict, every metric with its
confidence, point counts, impact time and source), the host LCMF launch
angle and club path, and the capture cost. Record the firmware SHA so the
rows can be told from a later build's.

Usage::

    uv run python scripts/analysis/baseline_dataset.py ~/openflight_sessions \\
        --firmware-sha $(git -C firmware rev-parse --short HEAD) --out iq8-baseline.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openflight.iwr6843.baseline import collect_sessions, summarize, write_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("paths", nargs="+", help="session_*.jsonl files or directories of them")
    parser.add_argument("--firmware-sha", default=None, help="recorded in every row")
    parser.add_argument("--out", type=Path, default=Path("iq8-baseline.csv"))
    args = parser.parse_args(argv)

    rows = collect_sessions(args.paths, firmware_sha=args.firmware_sha)
    count = write_csv(rows, args.out)
    summary = summarize(rows)
    print(f"{count} shots -> {args.out}")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
