#!/usr/bin/env python3
"""Score the firmware's club and ball tracking on recorded captures against OPS.

    uv run python scripts/analysis/evaluate_iwr_tracking.py iwr-test-sessions \
        [--json out.json] [--compare baseline.json] [--allow-more-none N]

Every ``.l3dump`` whose session log (``dump_viewer.session_context``) gives an
OPS ball speed and a ``triggerCfg`` tee bin is replayed through the compiled
firmware as the board ran it: the session's trigger configuration, and ball
tracking from the recorded freeze frame when the dump carries a retention
report. Over those captures it counts:

* club at impact: the club track's last points before the split approach the
  ball at >= 0.5 bins/frame ("club"), do not ("stuck"), or are too few;
* ball vs OPS: the launch speed within 15 % of the OPS ball speed ("ok"),
  outside it ("wrong"), or no launch ("none");
* ball present: the saved post-impact targets hold a chain of >= 3 points in
  consecutive frames moving at 0.7-1.1 x the OPS speed -- whether the data
  contains the ball at all.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from openflight.iwr6843 import firmware_replay as fr
from openflight.iwr6843.dump import parse_header
from openflight.iwr6843.dump_viewer import (
    ViewerOptions,
    bin_width_m,
    session_context,
    tee_bin_for,
)

MPH_TO_MPS = 0.44704
BALL_TOLERANCE = 0.15
CLUB_MIN_STEP_BINS = 0.5
CLUB_POINTS = 5
CHAIN_RATE_BOUNDS = (0.7, 1.1)
CHAIN_MIN_POINTS = 3


@dataclass(frozen=True)
class Case:
    """One capture to replay: where it is, how the board ran it, and the truth."""

    path: Path
    config: fr.ReplayConfig
    ops_mps: float


@dataclass(frozen=True)
class Outcome:
    """How the firmware did on one capture."""

    name: str
    club: str  # "club", "stuck" or "few"
    ball: str  # "ok", "wrong" or "none"
    ball_present: bool
    launch_mps: float | None
    ops_mps: float


def club_verdict(points: Sequence, split_frame: int | None) -> str:
    """ "club" when the club track's last points before the split close on the ball."""
    before = [p for p in points if split_frame is None or p.frame < split_frame][-CLUB_POINTS:]
    if len(before) < 3:
        return "few"
    slope = float(np.polyfit([p.frame for p in before], [p.range_bin for p in before], 1)[0])
    return "club" if slope >= CLUB_MIN_STEP_BINS else "stuck"


def ball_verdict(
    launch_mps: float | None, ops_mps: float, tolerance: float = BALL_TOLERANCE
) -> str:
    """The launch speed against OPS: "ok", "wrong", or "none" without a launch."""
    if launch_mps is None:
        return "none"
    return "ok" if abs(launch_mps - ops_mps) <= tolerance * ops_mps else "wrong"


def ball_present(frames: Sequence, ops_mps: float, bin_m: float) -> bool:
    """A chain of targets across consecutive frames moving at the OPS speed."""
    lo, hi = (bound * ops_mps for bound in CHAIN_RATE_BOUNDS)
    previous: list[tuple[float, int]] = []  # (range bin, chain length) in the last frame
    last_frame = last_us = None
    for frame in frames:
        current: list[tuple[float, int]] = []
        consecutive = last_frame is not None and frame.frame == last_frame + 1
        dt_s = (frame.timestamp_us - last_us) * 1e-6 if consecutive else 0.0
        for target in frame.targets:
            length = 1
            if consecutive and dt_s > 0.0:
                for range_bin, chain in previous:
                    rate = (target.range_bin - range_bin) * bin_m / dt_s
                    if lo <= rate <= hi:
                        length = max(length, chain + 1)
            if length >= CHAIN_MIN_POINTS:
                return True
            current.append((target.range_bin, length))
        previous, last_frame, last_us = current, frame.frame, frame.timestamp_us
    return False


def split_frame(result) -> int | None:
    """The first post-impact frame: the recorded freeze, else the frame after the gate."""
    if result.config.post_from_frame is not None:
        return result.config.post_from_frame
    return None if result.fired_frame is None else result.fired_frame + 1


def iter_cases(roots: Iterable[Path]) -> Iterator[Case]:
    """Captures under the roots with an OPS ball speed and the gate the board ran."""
    for root in roots:
        for path in sorted(Path(root).rglob("*.l3dump")):
            context = session_context(path)
            if not context or not context.get("ball_speed_mph"):
                continue
            defaults = context["defaults"]
            if "tee_bin" not in defaults:
                continue  # no triggerCfg: the gate the board ran is unknown
            try:
                meta = parse_header(path.read_bytes())
            except ValueError:
                continue
            options = ViewerOptions.from_mapping(defaults)
            config = fr.ReplayConfig(
                tee_bin=tee_bin_for(options),
                snr=options.snr,
                track_frames=options.track_frames,
                pitch_deg=options.pitch_deg,
                post_from_frame=(meta.get("retention") or {}).get("pre_frames"),
            )
            yield Case(path, config, float(context["ball_speed_mph"]) * MPH_TO_MPS)


def evaluate(case: Case, *, lib=None, ball_hypotheses: bool | None = None) -> Outcome:
    """Replay one capture and judge its club and ball tracks."""
    config = case.config
    if ball_hypotheses is not None:
        config = replace(config, ball_hypotheses=ball_hypotheses)
    result = fr.replay_dump(case.path.read_bytes(), config, lib=lib)
    split = split_frame(result)
    post = [f for f in result.frames if split is not None and f.frame >= split]
    launch = None if result.launch is None else float(result.launch.speed_mps)
    return Outcome(
        name=case.path.name,
        club=club_verdict(result.points, split),
        ball=ball_verdict(launch, case.ops_mps),
        ball_present=ball_present(post, case.ops_mps, bin_width_m()),
        launch_mps=launch,
        ops_mps=case.ops_mps,
    )


def summarize(outcomes: Iterable[Outcome]) -> dict:
    """Counts per category, the numbers the spec's acceptance is written in."""
    outcomes = list(outcomes)

    def count(field: str, values: tuple[str, ...]) -> dict[str, int]:
        return {v: sum(1 for o in outcomes if getattr(o, field) == v) for v in values}

    return {
        "captures": len(outcomes),
        "club": count("club", ("club", "stuck", "few")),
        "ball": count("ball", ("ok", "wrong", "none")),
        "ball_present": sum(1 for o in outcomes if o.ball_present),
    }


def compare(summary: dict, baseline: dict, *, allow_more_none: int = 0) -> list[str]:
    """Regressions against a baseline summary, one readable line each."""
    if summary["captures"] != baseline["captures"]:
        return [f"captures: {summary['captures']} vs baseline {baseline['captures']}"]
    problems = []
    if summary["club"]["club"] < baseline["club"]["club"]:
        problems.append(
            f"club at impact: {summary['club']['club']} < baseline {baseline['club']['club']}"
        )
    if summary["ball"]["ok"] < baseline["ball"]["ok"]:
        problems.append(f"ball ok: {summary['ball']['ok']} < baseline {baseline['ball']['ok']}")
    if summary["ball"]["none"] > baseline["ball"]["none"] + allow_more_none:
        problems.append(
            f"no launch: {summary['ball']['none']} > baseline {baseline['ball']['none']}"
            f" + {allow_more_none}"
        )
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("roots", nargs="+", type=Path, help="Folders searched for .l3dump files")
    parser.add_argument("--json", type=Path, help="Write the summary and every outcome here")
    parser.add_argument("--compare", type=Path, help="A baseline written by --json")
    parser.add_argument("--allow-more-none", type=int, default=0)
    args = parser.parse_args(argv)
    outcomes = [evaluate(case) for case in iter_cases(args.roots)]
    summary = summarize(outcomes)
    print(json.dumps(summary, indent=2))
    if args.json is not None:
        args.json.write_text(
            json.dumps({"summary": summary, "outcomes": [asdict(o) for o in outcomes]}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    if args.compare is not None:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))["summary"]
        problems = compare(summary, baseline, allow_more_none=args.allow_more_none)
        for problem in problems:
            print(f"REGRESSION {problem}")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
