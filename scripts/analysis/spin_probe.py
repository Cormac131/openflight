#!/usr/bin/env python3
"""EXPERIMENTAL: probe recorded IWR6843 captures for a spin observable.

Cuts the ball's region of interest out of a dump (frames x loops x antennas x
a few range bins around the ball), removes the bulk Doppler and reports the
residual spectral spread per frame with a coarse stationary / low-spin /
high-spin label. The thresholds are placeholders until reference balls have
been recorded; the report says so. Nothing here feeds a shot.

Usage::

    uv run python scripts/analysis/spin_probe.py capture.l3dump --frames 10-17 --bin 52
    uv run python scripts/analysis/spin_probe.py capture.l3dump --frames 10-17 --range-m 2.4 --save roi.npz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from openflight.iwr6843.spin_probe import (
    ball_roi,
    bin_of_range,
    classify_signature,
    format_report,
    micro_doppler,
)


def _frames(text: str) -> range:
    if "-" in text:
        lo, hi = text.split("-", 1)
        return range(int(lo), int(hi) + 1)
    return range(int(text), int(text) + 1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("dump", help=".l3dump capture")
    parser.add_argument("--frames", required=True, type=_frames, help="frame or lo-hi range")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--bin", type=int, help="global range bin of the ball")
    where.add_argument("--range-m", type=float, help="range of the ball in metres")
    parser.add_argument("--half-width", type=int, default=2, help="bins either side to keep")
    parser.add_argument(
        "--loop-period-us", type=float, help="same-TX loop period; default n_tx x 45 us"
    )
    parser.add_argument("--save", help="write the ROI and spectra to this .npz for offline work")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    raw = Path(args.dump).read_bytes()
    center = args.bin if args.bin is not None else bin_of_range(args.range_m)
    roi = ball_roi(
        raw,
        frames=args.frames,
        center_bin=center,
        half_width=args.half_width,
        loop_period_s=None if args.loop_period_us is None else args.loop_period_us * 1e-6,
    )
    results = micro_doppler(roi)
    signature = classify_signature(results)
    print(format_report(results, signature))
    if args.save:
        np.savez_compressed(
            args.save,
            roi=roi.data,
            frames=np.array(roi.frames),
            first_bin=roi.first_bin,
            loop_period_s=roi.loop_period_s,
            spectra=np.stack([r.spectrum for r in results]),
            velocity_axis=results[0].velocity_axis,
        )
        print(f"saved {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
