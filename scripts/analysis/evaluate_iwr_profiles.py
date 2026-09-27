#!/usr/bin/env python3
"""Evaluate radar configurations by measurement quality, not frame rate.

Replays a capture through the firmware's trigger, club track and ball
tracker with fewer loops per frame (a 12-loop hardware capture answers what
6 or 8 loops would have measured: ``dump.select_tdm_loops`` keeps timing,
windows and TX order) and reports, per loop count, the club speed, path,
attack, ball speed, launch angles, the fit residuals and confidences, and
their differences from the full-loop replay or from the truth a manifest or
sidecar states. That is the data the roadmap asks for before changing the
waveform: speed error, angle error, target confidence per loop count.

Usage::

    uv run python scripts/analysis/evaluate_iwr_profiles.py capture.l3dump --tee-bin 34 --loops 12 8 6 4
    uv run python scripts/analysis/evaluate_iwr_profiles.py tests/radar/recordings --loops 12 6
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.dump import parse_header, select_tdm_loops
from openflight.iwr6843.firmware_replay import (
    DEFAULT_FFT_SIZE,
    ReplayConfig,
    ReplayResult,
    recording_configs,
    replay_dump,
)
from openflight.iwr6843.tracking import RANGE_SPAN_M

METRICS = (
    ("club speed m/s", lambda r: None if r.delivery is None else r.delivery.speed_mps),
    ("club path deg", lambda r: None if r.delivery is None else r.delivery.path_deg),
    ("attack deg", lambda r: None if r.delivery is None else r.delivery.attack_deg),
    ("club conf", lambda r: None if r.delivery is None else r.delivery.confidence),
    ("ball speed m/s", lambda r: None if r.launch is None else r.launch.speed_mps),
    ("hla deg", lambda r: None if r.launch is None else r.launch.hla_deg),
    ("vla deg", lambda r: None if r.launch is None else r.launch.vla_deg),
    ("ball conf", lambda r: None if r.launch is None else r.launch.confidence),
    ("club points", lambda r: float(len(r.points))),
    ("ball points", lambda r: float(len(r.ball_points))),
)


@dataclass(frozen=True)
class LoopEvaluation:
    loops: int
    fired: bool
    values: dict[str, float | None]


def evaluate_loops(
    raw: bytes, config: ReplayConfig, loop_counts: list[int], *, lib=None
) -> list[LoopEvaluation]:
    """Replay ``raw`` at each loop count (the first ``n`` loops of every frame)."""
    lib = lib or fw.build_firmware_library()
    meta = parse_header(raw)
    available = meta["chirps_per_frame"] // meta["n_tx"]
    out: list[LoopEvaluation] = []
    for loops in loop_counts:
        if loops > available:
            raise ValueError(f"{loops} loops requested but the capture has {available}")
        subset = raw if loops == available else select_tdm_loops(raw, start=0, count=loops)
        result: ReplayResult = replay_dump(subset, config, lib=lib)
        out.append(
            LoopEvaluation(
                loops,
                result.fired_frame is not None,
                {name: read(result) for name, read in METRICS},
            )
        )
    return out


def format_table(
    name: str, evaluations: list[LoopEvaluation], truth: dict[str, float] | None = None
) -> str:
    """One row per metric, one column per loop count, with the difference from
    the fullest replay (or the truth when given) in brackets."""
    if not evaluations:
        return f"{name}: nothing evaluated"
    reference = evaluations[0]
    header = f"{name}: " + "  ".join(
        f"{e.loops:>2d} loops{' (fired)' if e.fired else ' (no fire)'}" for e in evaluations
    )
    lines = [header]
    for metric, _ in METRICS:
        cells = []
        base = truth.get(metric) if truth and metric in truth else reference.values[metric]
        for e in evaluations:
            value = e.values[metric]
            if value is None:
                cells.append("      -        ")
                continue
            diff = "" if base is None else f" ({value - base:+.2f})"
            cells.append(f"{value:8.2f}{diff:>8s}")
        lines.append(f"  {metric:15s} " + "  ".join(cells))
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("paths", nargs="+", help=".l3dump files or directories with a manifest")
    tee = parser.add_mutually_exclusive_group()
    tee.add_argument("--tee-bin", type=int)
    tee.add_argument("--tee-range-m", type=float)
    parser.add_argument(
        "--loops",
        type=int,
        nargs="+",
        default=[12, 8, 6, 4],
        help="loop counts to evaluate, fullest first",
    )
    parser.add_argument("--fft-size", type=int, default=DEFAULT_FFT_SIZE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    tee_bin = args.tee_bin
    if args.tee_range_m is not None:
        tee_bin = int(args.tee_range_m / (RANGE_SPAN_M / args.fft_size))
    jobs: list[tuple[Path, ReplayConfig]] = []
    for text in args.paths:
        path = Path(text).expanduser()
        if path.is_dir():
            try:
                jobs.extend(recording_configs(path, default_tee_bin=tee_bin))
            except ValueError as error:
                raise SystemExit(str(error)) from error
        else:
            if tee_bin is None:
                raise SystemExit(f"{path}: give --tee-bin or --tee-range-m for a single file")
            jobs.append((path, ReplayConfig(tee_bin=tee_bin, fft_size=args.fft_size)))
    if not jobs:
        print("no .l3dump files found", file=sys.stderr)
        return 1
    lib = fw.build_firmware_library()
    loop_counts = sorted(set(args.loops), reverse=True)
    for path, config in jobs:
        raw = path.read_bytes()
        available = parse_header(raw)["chirps_per_frame"] // parse_header(raw)["n_tx"]
        counts = [n for n in loop_counts if n <= available]
        print(format_table(path.name, evaluate_loops(raw, config, counts, lib=lib)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
