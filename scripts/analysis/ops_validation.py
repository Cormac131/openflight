#!/usr/bin/env python3
"""OPS243 versus IWR6843 speeds over recorded sessions (phase 27).

Every shot with an onboard result leaves an ``iwr_ops_comparison`` entry in
the session log. This sums them: bias, MAE, RMSE and 95th percentile of the
IWR's ball and club speed against the OPS, overall and grouped by club,
capture format, verdict and the IWR's own confidence band, so the confidence
can later be calibrated against real error (``confidence_calibration``).

Usage::

    uv run python scripts/analysis/ops_validation.py ~/openflight_sessions
    uv run python scripts/analysis/ops_validation.py session_2026*.jsonl --by club --by ball_confidence
"""

from __future__ import annotations

import argparse
import sys

from openflight.iwr6843.ops_compare import format_summary, group_by, read_sessions, summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("paths", nargs="+", help="session_*.jsonl files or directories of them")
    parser.add_argument(
        "--by",
        action="append",
        default=None,
        choices=("club", "capture_format", "verdict", "ball_confidence", "club_confidence"),
        help="also group by this (repeatable)",
    )
    args = parser.parse_args(argv)
    records = read_sessions(args.paths)
    if not records:
        print("no iwr_ops_comparison entries found", file=sys.stderr)
        return 1
    print(format_summary(summarize(records)))
    for key in args.by or ("capture_format", "ball_confidence"):
        for name, group in sorted(group_by(records, key).items()):
            print()
            print(format_summary(summarize(group), title=f"{key}={name}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
