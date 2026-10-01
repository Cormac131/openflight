#!/usr/bin/env python3
"""EXPERIMENTAL: probe recorded IWR6843 captures for a spin observable.

Cuts the ball's region of interest out of a dump (frames x loops x antennas x
a few range bins around the ball), removes the bulk Doppler and reports the
residual spectral spread per frame with a coarse stationary / low-spin /
high-spin label. The thresholds are placeholders until reference balls have
been recorded; the report says so. Nothing here feeds a shot.

With ``--labels`` the probe follows the ball along the dump's reviewed label
file (``<dump>.labels.json``, marked in the dump viewer; it recedes 2-3
bins per frame, so a fixed bin loses it), reports the spread at the ball in
every labelled frame, and scans the echo power for a once-per-revolution
line (``--reference-rpm`` compares it to a launch monitor's number).
``--frames`` then narrows the labelled frames.

Usage::

    uv run python scripts/analysis/spin_probe.py capture.l3dump --frames 10-17 --bin 52
    uv run python scripts/analysis/spin_probe.py capture.l3dump --frames 10-17 --range-m 2.4 --save roi.npz
    uv run python scripts/analysis/spin_probe.py capture.l3dump --labels --reference-rpm 6200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from openflight.iwr6843.labels import load_labels
from openflight.iwr6843.spin_probe import (
    ball_roi,
    bin_of_range,
    classify_signature,
    follow_rois,
    format_report,
    format_rotation_report,
    format_track,
    micro_doppler,
    rotation_spectrum,
    track_from_points,
)


def _frames(text: str) -> range:
    if "-" in text:
        lo, hi = text.split("-", 1)
        return range(int(lo), int(hi) + 1)
    return range(int(text), int(text) + 1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("dump", help=".l3dump capture")
    parser.add_argument(
        "--frames", type=_frames, help="frame or lo-hi range (required without --labels)"
    )
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--bin", type=int, help="global range bin of the ball")
    where.add_argument("--range-m", type=float, help="range of the ball in metres")
    parser.add_argument("--half-width", type=int, default=2, help="bins either side to keep")
    parser.add_argument(
        "--loop-period-us", type=float, help="same-TX loop period; default n_tx x 45 us"
    )
    parser.add_argument(
        "--labels",
        action="store_true",
        help="follow the ball along <dump>.labels.json and scan for a rotation line",
    )
    parser.add_argument(
        "--reference-rpm", type=float, help="a launch monitor's spin for the same shot (--labels)"
    )
    parser.add_argument("--save", help="write the ROI and spectra to this .npz for offline work")
    parser.add_argument(
        "--iq8",
        nargs="?",
        const="edma",
        default=None,
        choices=("cpu", "edma", "dump"),
        metavar="PATH",
        help="also run the probe on the firmware-exact IQ8 of this capture (default path edma) "
        "and print the frame-by-frame difference",
    )
    return parser


def _follow_labels(args: argparse.Namespace, dump: Path, raw: bytes) -> int:
    labels = load_labels(dump)
    if labels is None:
        print(
            f"{dump.name}: no label file; mark the ball in the dump viewer first", file=sys.stderr
        )
        return 2
    points = [
        (p.frame, p.range_bin) for p in labels.ball if args.frames is None or p.frame in args.frames
    ]
    track = track_from_points(raw, points)
    if not labels.reviewed:
        print("note: these labels are not marked reviewed")
    print(format_track(track))
    rois = follow_rois(raw, track, half_width=args.half_width)
    results = [r for roi in rois for r in micro_doppler(roi)]
    if len(rois) < len(track.frames):
        print(
            f"note: {len(track.frames) - len(rois)} of {len(track.frames)} frames have the ball "
            f"within {args.half_width} bins of their window's edge; the spread leaves them out"
        )
    if results:
        print(format_report(results, classify_signature(results)))
    rotation = rotation_spectrum(raw, track)
    print(format_rotation_report(rotation, reference_rpm=args.reference_rpm))
    if args.save:
        spread = (
            {
                "roi": np.concatenate([roi.data for roi in rois]),
                "roi_frames": np.array([roi.frames[0] for roi in rois]),
                "first_bins": np.array([roi.first_bin for roi in rois]),
                "loop_period_s": rois[0].loop_period_s,
                "spectra": np.stack([r.spectrum for r in results]),
                "velocity_axis": results[0].velocity_axis,
            }
            if rois
            else {}
        )
        np.savez_compressed(
            args.save,
            **spread,
            frames=np.array(track.frames),
            track_bins=np.array(track.bins),
            track_times_s=np.array(track.times_s),
            rotation_freqs_hz=rotation.freqs_hz,
            rotation_fraction=rotation.fraction,
        )
        print(f"saved {args.save}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.labels:
        if args.bin is not None or args.range_m is not None:
            parser.error("--labels takes the ball's bins from the label file, not --bin/--range-m")
        if args.iq8 is not None:
            parser.error("--iq8 compares a fixed-bin probe; it does not combine with --labels")
    else:
        if args.reference_rpm is not None:
            parser.error("--reference-rpm needs --labels")
        if args.frames is None or (args.bin is None and args.range_m is None):
            parser.error("a fixed-bin probe needs --frames and one of --bin/--range-m")
    raw = Path(args.dump).read_bytes()
    if args.labels:
        return _follow_labels(args, Path(args.dump), raw)
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
    if args.iq8 is not None:
        from openflight.iwr6843.iq8_emulation import Iq8Mode, emulate_dump
        from openflight.iwr6843.spin_probe import compare_paths, format_comparison

        mode = Iq8Mode(args.iq8)
        emulated = emulate_dump(raw, mode)
        roi8 = ball_roi(
            emulated,
            frames=args.frames,
            center_bin=center,
            half_width=args.half_width,
            loop_period_s=roi.loop_period_s,
        )
        results8 = micro_doppler(roi8)
        print(
            format_report(results8, classify_signature(results8)).replace(
                "spin probe:", f"spin probe ({mode.label}):", 1
            )
        )
        print(format_comparison(compare_paths(results, results8), label_b=mode.label))
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
