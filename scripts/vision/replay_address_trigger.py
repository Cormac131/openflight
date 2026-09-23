#!/usr/bin/env python3
"""Replay saved camera clips through the camera ball-at-address trigger.

Every sound-triggered shot saved with --camera-capture has a frames.npz clip.
This replays them offline and reports whether the camera trigger would have
fired and how its impact estimate compares with the sound trigger.

Usage:
    uv run python scripts/vision/replay_address_trigger.py ~/openflight_sessions/range/camera
    uv run python scripts/vision/replay_address_trigger.py CLIPS --ball 320,240,9 --json
    uv run python scripts/vision/replay_address_trigger.py CLIPS --gone-frames 6

Without --ball the Hough-circle BallDetector (OpenCV, `uv sync --extra camera`)
finds the ball in each clip's first frames.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from openflight.camera.address_replay import (  # noqa: E402
    fixed_ball_acquirer,
    replay_clip,
    summarize,
)
from openflight.camera.address_trigger import AddressTriggerConfig, BallCandidate  # noqa: E402


def parse_ball(text: str) -> BallCandidate:
    """Parse X,Y,R pixel coordinates."""
    try:
        x, y, r = (float(part) for part in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--ball must be X,Y,R") from exc
    return BallCandidate(x, y, r)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path, help="frames.npz files or directories")
    parser.add_argument("--ball", type=parse_ball, help="Known ball position X,Y,R (skips OpenCV)")
    parser.add_argument("--gone-frames", type=int, default=9)
    parser.add_argument("--departure-ms", type=float, default=20.0)
    parser.add_argument("--acquire-frames", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    args = parser.parse_args()

    clips = []
    for path in args.paths:
        clips.extend(sorted(path.rglob("frames.npz")) if path.is_dir() else [path])
    if not clips:
        print("No frames.npz clips found", file=sys.stderr)
        return 1

    if args.ball is not None:
        acquire = fixed_ball_acquirer(args.ball)
    else:
        from openflight.camera.address_monitor import ball_detector_acquirer  # noqa: PLC0415

        acquire = ball_detector_acquirer()

    config = AddressTriggerConfig(
        gone_frames=args.gone_frames,
        max_departure_ms=args.departure_ms,
        require_address=False,
    )
    results = [
        replay_clip(clip, acquire_fn=acquire, config=config, acquire_frames=args.acquire_frames)
        for clip in clips
    ]
    summary = summarize(results)

    if args.json:
        print(json.dumps({"results": [r.to_dict() for r in results], "summary": summary}, indent=2))
        return 0

    for result in results:
        delta = f"{result.delta_ms:+7.1f}ms" if result.delta_ms is not None else "     n/a"
        status = "TRIGGER" if result.triggered else ("locked " if result.locked else "no-ball")
        print(f"{status}  {delta}  {result.path}")
    print()
    print(
        f"{summary['triggered']}/{summary['clips']} triggered, "
        f"{summary['locked']} locked, median delta {summary['median_delta_ms']} ms, "
        f"max |delta| {summary['max_abs_delta_ms']} ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
