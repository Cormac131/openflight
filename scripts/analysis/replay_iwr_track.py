#!/usr/bin/env python3
"""Replay recorded IWR6843 captures through the firmware's trigger and club track.

Each ``.l3dump`` is reduced to the per-bin observations the board computes
and fed, frame by frame, to the compiled C self-trigger, target extraction
and club track (``openflight.iwr6843.firmware_replay``). The report says
whether the trigger would have fired, how many trajectory points the club
track held, how continuous they were (longest run, acquisitions, coasts,
drops), which way they moved and the fitted club speed.

Usage::

    uv run python scripts/analysis/replay_iwr_track.py capture.l3dump --tee-range-m 1.575
    uv run python scripts/analysis/replay_iwr_track.py ~/openflight_sessions --tee-bin 34 --points
    uv run python scripts/analysis/replay_iwr_track.py tests/radar/recordings

A directory is every ``.l3dump`` in it; with a ``manifest.json`` beside them
(see tests/radar/recordings/README.md) the per-file tee and destination bins
come from there and ``--tee-bin`` only fills in files the manifest omits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.firmware_replay import (
    DEFAULT_FFT_SIZE,
    DEFAULT_SNR,
    DEFAULT_TRACK_FRAMES,
    ReplayConfig,
    ReplayResult,
    format_report,
    recording_configs,
    recording_expectations,
    replay_file,
)
from openflight.iwr6843.tracking import RANGE_SPAN_M


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("paths", nargs="+", help=".l3dump files or directories of them")
    tee = parser.add_mutually_exclusive_group()
    tee.add_argument("--tee-bin", type=int, help="global range bin of the tee")
    tee.add_argument("--tee-range-m", type=float, help="tee range in metres (converted to a bin)")
    parser.add_argument("--fft-size", type=int, default=DEFAULT_FFT_SIZE)
    parser.add_argument("--points", action="store_true", help="print every trajectory point")
    # Replay options a manifest may also set: absent from the namespace unless
    # given, so a manifest entry is overridden only by an explicit flag.
    parser.add_argument(
        "--dest-bin",
        type=int,
        default=argparse.SUPPRESS,
        help="locked ball bin to use as the destination",
    )
    parser.add_argument(
        "--snr", type=float, default=argparse.SUPPRESS, help=f"triggerCfg snr ({DEFAULT_SNR})"
    )
    parser.add_argument(
        "--track-frames",
        type=int,
        default=argparse.SUPPRESS,
        help=f"triggerCfg track frames ({DEFAULT_TRACK_FRAMES})",
    )
    parser.add_argument(
        "--stat",
        choices=sorted(fw.STAT_NAMES),
        default=argparse.SUPPRESS,
        help="peak (default) or energy",
    )
    parser.add_argument(
        "--loop-period-us",
        type=float,
        default=argparse.SUPPRESS,
        help="same-TX chirp interval; default n_tx x 45 us",
    )
    parser.add_argument(
        "--stop-at-fire",
        action="store_true",
        default=argparse.SUPPRESS,
        help="ignore frames after the trigger fires, as the board does",
    )
    parser.add_argument(
        "--post-from",
        dest="post_from_frame",
        type=int,
        default=argparse.SUPPRESS,
        help="treat this frame as the first post-impact one (sound-triggered captures: the "
        "plan's pre frame count, 9 on the wide profile) so the ball tracker is judged alone",
    )
    return parser


def _overrides(args: argparse.Namespace) -> dict:
    """ReplayConfig fields the command line set explicitly."""
    chosen = {
        key: getattr(args, key)
        for key in ("dest_bin", "snr", "track_frames", "stat", "stop_at_fire", "post_from_frame")
        if hasattr(args, key)
    }
    if hasattr(args, "loop_period_us"):
        chosen["loop_period_s"] = args.loop_period_us * 1e-6
    chosen["fft_size"] = args.fft_size
    return chosen


def _collect(args: argparse.Namespace) -> list[tuple[Path, ReplayConfig]]:
    tee_bin = args.tee_bin
    if args.tee_range_m is not None:
        tee_bin = int(args.tee_range_m / (RANGE_SPAN_M / args.fft_size))
    overrides = _overrides(args)
    jobs: list[tuple[Path, ReplayConfig]] = []
    for text in args.paths:
        path = Path(text).expanduser()
        if path.is_dir():
            try:
                configs = recording_configs(path, default_tee_bin=tee_bin)
            except ValueError as error:
                raise SystemExit(str(error)) from error
            for file, config in configs:
                jobs.append((file, ReplayConfig(**{**config.__dict__, **overrides})))
        else:
            if tee_bin is None:
                raise SystemExit(f"{path}: give --tee-bin or --tee-range-m for a single file")
            jobs.append((path, ReplayConfig(tee_bin=tee_bin, **overrides)))
    return jobs


def _summary_line(name: str, result: ReplayResult) -> str:
    fired = "-" if result.fired_frame is None else str(result.fired_frame)
    return (
        f"{name:40s} fire={fired:>3s} points={len(result.points):3d} "
        f"run={result.longest_run:3d} acq={result.acquisitions:2d} "
        f"coast={result.track_counters['coasted']:2d} drop={result.track_counters['dropped']:2d} "
        f"approach={100.0 * result.approach_fraction:3.0f}% speed={result.speed_mps:5.1f}"
    )


def main(argv: list[str] | None = None) -> int:
    """Replay every requested capture; 0 on success, 1 when nothing was found,
    2 when a recording failed the expectations its manifest states."""
    args = _parser().parse_args(argv)
    jobs = _collect(args)
    if not jobs:
        print("no .l3dump files found", file=sys.stderr)
        return 1
    lib = fw.build_firmware_library()
    expectations = {}
    for text in args.paths:
        path = Path(text).expanduser()
        if path.is_dir():
            expectations.update(recording_expectations(path))
    summaries = []
    failed = 0
    for path, config in jobs:
        result = replay_file(path, config, lib=lib)
        print(format_report(result, name=path.name, points=args.points))
        expectation = expectations.get(path.name)
        if expectation is not None:
            failures = expectation.check(result)
            failed += bool(failures)
            print("  expectations: " + ("ok" if not failures else "FAIL " + "; ".join(failures)))
        summaries.append(_summary_line(path.name, result))
    if len(summaries) > 1:
        print()
        print("\n".join(summaries))
    if failed:
        print(f"\n{failed} of {len(jobs)} recordings failed their expectations", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
