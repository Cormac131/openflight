#!/usr/bin/env python3
"""Validate the onboard measurements against a reference launch monitor (phase 28).

Every labelled shot under ``tests/radar/datasets`` (or the directory given)
is replayed through the firmware modules and its ball speed, launch angles,
club speed, path and attack angle are compared with the sidecar's reference
values. The report gives bias, MAE, RMSE and P95 per field, overall and per
label, and first says which cells of the club x speed x shape matrix are
thin, because a hundred identical 7-irons validate nothing.

Usage::

    uv run python scripts/analysis/reference_validation.py
    uv run python scripts/analysis/reference_validation.py ~/datasets/september --min-per-cell 20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.datasets import (
    DATASETS_DIR,
    coverage,
    format_field_stats,
    load_dataset,
    measure_with_firmware,
    validate_dataset,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("directory", nargs="?", default=str(DATASETS_DIR))
    parser.add_argument("--min-per-cell", type=int, default=10, help="shots a matrix cell needs")
    args = parser.parse_args(argv)
    shots = load_dataset(Path(args.directory))
    if not shots:
        print(f"no labelled shots under {args.directory}", file=sys.stderr)
        return 1
    cov = coverage(shots)
    print(f"{len(shots)} shots; clubs {cov.clubs}; speeds {cov.speeds}; shapes {cov.shapes}")
    thin = cov.missing(args.min_per_cell)
    if thin:
        print(f"thin cells (< {args.min_per_cell}): " + ", ".join(thin))
    if cov.unclassified_clubs:
        print(f"clubs outside the matrix: {cov.unclassified_clubs}")
    lib = fw.build_firmware_library()
    results = validate_dataset(shots, lambda shot: measure_with_firmware(shot, lib=lib))
    for title, stats in results.items():
        print()
        print(format_field_stats(stats, title=title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
