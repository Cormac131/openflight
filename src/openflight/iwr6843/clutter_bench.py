"""Golfer-clutter benchmark: per-capture metrics that every clutter, tracking
and orientation change is judged on, over exactly the same replayed dumps.

The question is not whether the range-time map looks cleaner but whether the
two physical trajectories are recovered: the club into the hotspot, the ball
(and the club) out of it. Per capture this reads the replay
(:func:`firmware_replay.replay_dump`) and the dump's own residual power:

* the hotspot: the strongest median idle residual power in the impact ROI
  (``roi_bins`` short of and beyond the destination) over the pre-impact
  frames, in dB, as recorded and as the trackers saw it after suppression;
* the club: points before impact, points through the hotspot, the longest
  consecutive run, its speed at impact;
* the ball: first frame, longest run, launch speed;
* false detections: extracted targets on no track;
* the trigger: whether impact was declared at all;
* ``SCR = P_club/ball - P_golfer`` in dB, each read from the power the
  trackers saw;
* the club and ball speeds at impact bridged across the hotspot
  (:mod:`impact_bridge`);
* the hand labels, when the dump has reviewed ones, scored as
  :mod:`label_scoring` does.

:func:`acceptance` states the plan's Phase 1 bar against a reference run.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from openflight.iwr6843 import firmware_replay as fr
from openflight.iwr6843.clutter_map import power_db
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.impact_bridge import bridge_impact
from openflight.iwr6843.label_scoring import score_object
from openflight.iwr6843.labels import load_labels
from openflight.iwr6843.tracking import RANGE_SPAN_M

ROI_SHORT_BINS = 12  # the club's approach through the golfer, short of the ball
ROI_LONG_BINS = 4
HOTSPOT_HALF_BINS = 1.5  # a point this close to the hotspot bin is "in" it
TRACK_MATCH_BINS = 1.5  # a target this close to a track point is that track's


@dataclass(frozen=True)
class CaptureMetrics:
    """One capture under one configuration."""

    name: str
    impact_frame: int | None
    triggered: bool  # the shot machine declared impact
    self_triggered: bool  # the club track's range-only impact fired, early or not
    hotspot_bin: int | None
    hotspot_db: float | None  # as recorded
    hotspot_seen_db: float | None  # after suppression, what the trackers saw
    club_points_pre: int
    club_points_in_hotspot: int
    club_points_post: int
    club_longest_run: int
    club_speed_mps: float | None
    ball_first_frame: int | None
    ball_points: int
    ball_longest_run: int
    ball_speed_mps: float | None
    false_detections: int
    club_scr_db: float | None
    ball_scr_db: float | None
    bridged_club_mps: float | None = None  # impact_bridge through the hotspot
    bridged_ball_mps: float | None = None
    bridged_plausible: bool = False
    club_label_coverage: float | None = None
    ball_label_coverage: float | None = None
    club_label_score: float | None = None
    ball_label_score: float | None = None


def longest_run(frames: Iterable[int]) -> int:
    """Most consecutive frames in a set of frame numbers."""
    best = run = 0
    previous = None
    for frame in sorted(set(frames)):
        run = run + 1 if previous is not None and frame == previous + 1 else 1
        best = max(best, run)
        previous = frame
    return best


def impact_frame(result: fr.ReplayResult) -> int | None:
    """The first post-impact frame: the recorded freeze, else the frame after the fire."""
    if result.config.post_from_frame is not None:
        return result.config.post_from_frame
    return None if result.fired_frame is None else result.fired_frame + 1


def frame_powers(raw: bytes, n_bins: int, stat: str) -> np.ndarray:
    """``[frames, global bins]`` of the recorded residual statistic; NaN
    where a frame's window does not reach a bin."""
    meta, cube = parse_dump(raw)
    n_tx = int(meta["n_tx"])
    powers = np.full((int(meta["n_frames"]), n_bins), np.nan)
    for frame in range(int(meta["n_frames"])):
        start, count = fr.frame_window(meta, frame)
        if count <= 0:
            continue
        table = fr.bin_observation_table(cube, frame, 0, count, n_tx)
        stop = min(start + count, n_bins)
        powers[frame, start:stop] = table[stat][: stop - start]
    return powers


def seen_powers(result: fr.ReplayResult, recorded: np.ndarray) -> np.ndarray:
    """The powers the trackers saw: the recorded ones after the replay's
    clutter suppression (the recorded ones when it ran without)."""
    if not result.clutter_seen_gains:
        return recorded
    seen = recorded.copy()
    for frame, (start, gains) in enumerate(result.clutter_seen_gains):
        if gains:
            stop = min(start + len(gains), seen.shape[1])
            seen[frame, start:stop] *= np.square(np.asarray(gains[: stop - start]))
    return seen


def _hotspot(pre: np.ndarray, lo: int, hi: int) -> tuple[int | None, float | None]:
    if pre.shape[0] == 0 or hi <= lo:
        return None, None
    region = pre[:, lo:hi]
    if np.isnan(region).all():
        return None, None
    medians = np.nanmedian(np.where(np.isnan(region).all(axis=0), 0.0, region), axis=0)
    index = int(np.argmax(medians))
    return lo + index, float(medians[index])


def _point_power(powers: np.ndarray, frame: int, range_bin: float) -> float | None:
    if not 0 <= frame < powers.shape[0]:
        return None
    b = int(round(range_bin))
    if not 0 <= b < powers.shape[1] or np.isnan(powers[frame, b]):
        return None
    return float(powers[frame, b])


def _scr_db(powers: np.ndarray, points: Sequence, clutter: float | None) -> float | None:
    values = [v for p in points if (v := _point_power(powers, p.frame, p.range_bin)) is not None]
    if not values or clutter is None or clutter <= 0.0:
        return None
    return power_db(statistics.median(values)) - power_db(clutter)


def _false_detections(result: fr.ReplayResult) -> int:
    tracked: dict[int, list[float]] = {}
    for point in [*result.points, *result.ball_points]:
        tracked.setdefault(point.frame, []).append(point.range_bin)
    false = 0
    for frame in result.frames:
        bins = tracked.get(frame.frame, [])
        false += sum(
            1 for t in frame.targets if all(abs(t.range_bin - b) > TRACK_MATCH_BINS for b in bins)
        )
    return false


def capture_metrics(  # pylint: disable=too-many-arguments
    name: str,
    raw: bytes,
    result: fr.ReplayResult,
    *,
    labels=None,
    roi_short_bins: int = ROI_SHORT_BINS,
    roi_long_bins: int = ROI_LONG_BINS,
) -> CaptureMetrics:
    """The benchmark's numbers for one replayed capture."""
    config = result.config
    split = impact_frame(result)
    recorded = frame_powers(raw, config.fft_size, config.stat)
    seen = seen_powers(result, recorded)
    pre_frames = recorded.shape[0] if split is None else min(split, recorded.shape[0])
    lo = max(config.destination - roi_short_bins, 0)
    hi = min(config.destination + roi_long_bins + 1, config.fft_size)
    hotspot_bin, hotspot_power = _hotspot(recorded[:pre_frames], lo, hi)
    seen_power = None
    if hotspot_bin is not None:
        column = seen[:pre_frames, hotspot_bin]
        column = column[~np.isnan(column)]
        seen_power = float(np.median(column)) if column.size else None
    club_pre = [p for p in result.points if split is None or p.frame < split]
    club_post = [p for p in result.points if split is not None and p.frame >= split]
    in_hotspot = [
        p
        for p in result.points
        if hotspot_bin is not None and abs(p.range_bin - hotspot_bin) <= HOTSPOT_HALF_BINS
    ]
    ball = result.ball_points
    bridge = bridge_impact(
        [(p.timestamp_us, p.range_m) for p in club_pre],
        [(p.timestamp_us, p.range_m) for p in ball],
        club_out=[(p.timestamp_us, p.range_m) for p in club_post],
        ball_range_m=config.destination * RANGE_SPAN_M / config.fft_size,
    )
    metrics = CaptureMetrics(
        name=name,
        impact_frame=split,
        triggered=result.frozen_impact_timestamp_us is not None,
        self_triggered=result.range_frame is not None,
        hotspot_bin=hotspot_bin,
        hotspot_db=None if hotspot_power is None else power_db(hotspot_power),
        hotspot_seen_db=None if seen_power is None else power_db(seen_power),
        club_points_pre=len(club_pre),
        club_points_in_hotspot=len(in_hotspot),
        club_points_post=len(club_post),
        club_longest_run=longest_run(p.frame for p in club_pre),
        club_speed_mps=result.speed_mps if club_pre else None,
        ball_first_frame=min((p.frame for p in ball), default=None),
        ball_points=len(ball),
        ball_longest_run=longest_run(p.frame for p in ball),
        ball_speed_mps=None if result.launch is None else result.launch.speed_mps,
        false_detections=_false_detections(result),
        club_scr_db=_scr_db(seen, club_pre, seen_power),
        ball_scr_db=_scr_db(seen, ball, seen_power),
        bridged_club_mps=None if bridge is None else bridge.club_speed_mps,
        bridged_ball_mps=None if bridge is None else bridge.ball_speed_mps,
        bridged_plausible=bridge is not None and bridge.plausible,
    )
    if labels is None or not labels.reviewed:
        return metrics
    club_score = score_object(labels.club, result.points, labels.tolerances)
    ball_score = score_object(labels.ball, result.ball_points, labels.tolerances)
    return _with_labels(metrics, club_score, ball_score)


def _with_labels(metrics: CaptureMetrics, club, ball) -> CaptureMetrics:
    values = asdict(metrics)
    values.update(
        club_label_coverage=club.coverage if club.labelled else None,
        ball_label_coverage=ball.coverage if ball.labelled else None,
        club_label_score=club.score if club.labelled else None,
        ball_label_score=ball.score if ball.labelled else None,
    )
    return CaptureMetrics(**values)


def benchmark_file(path: Path, config: fr.ReplayConfig, *, lib=None) -> CaptureMetrics:
    """Replay one dump under ``config`` and measure it (labels when present)."""
    raw = Path(path).read_bytes()
    result = fr.replay_dump(raw, config, lib=lib)
    return capture_metrics(Path(path).name, raw, result, labels=load_labels(Path(path)))


def _mean(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return statistics.fmean(kept) if kept else None


def _median(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return statistics.median(kept) if kept else None


def summarize(metrics: Sequence[CaptureMetrics]) -> dict:
    """One configuration's totals and medians over every capture."""
    return {
        "captures": len(metrics),
        "triggered": sum(1 for m in metrics if m.triggered),
        "self_triggered": sum(1 for m in metrics if m.self_triggered),
        "median_hotspot_db": _median(m.hotspot_db for m in metrics),
        "median_hotspot_seen_db": _median(m.hotspot_seen_db for m in metrics),
        "club_points_pre": sum(m.club_points_pre for m in metrics),
        "club_points_in_hotspot": sum(m.club_points_in_hotspot for m in metrics),
        "club_points_post": sum(m.club_points_post for m in metrics),
        "with_ball": sum(1 for m in metrics if m.ball_points),
        "with_launch": sum(1 for m in metrics if m.ball_speed_mps is not None),
        "mean_ball_first_frame": _mean(m.ball_first_frame for m in metrics),
        "ball_points": sum(m.ball_points for m in metrics),
        "false_detections": sum(m.false_detections for m in metrics),
        "median_club_scr_db": _median(m.club_scr_db for m in metrics),
        "median_ball_scr_db": _median(m.ball_scr_db for m in metrics),
        "bridged_plausible": sum(1 for m in metrics if m.bridged_plausible),
        "mean_club_label_coverage": _mean(m.club_label_coverage for m in metrics),
        "mean_ball_label_coverage": _mean(m.ball_label_coverage for m in metrics),
        "mean_club_label_score": _mean(m.club_label_score for m in metrics),
        "mean_ball_label_score": _mean(m.ball_label_score for m in metrics),
    }


def acceptance(
    candidate: Sequence[CaptureMetrics],
    reference: Sequence[CaptureMetrics],
    *,
    min_hotspot_drop_db: float = 3.0,
) -> list[str]:
    """The plan's Phase 1 bar, per capture pair (same dumps, same order):
    the hotspot the trackers see drops by ``min_hotspot_drop_db`` (median),
    no capture loses pre-impact club points, none sees the ball later or
    loses it, and no capture that triggered stops triggering. Returns one
    readable line per failure; empty passes."""
    if [m.name for m in candidate] != [m.name for m in reference]:
        raise ValueError("candidate and reference must cover the same captures in order")
    problems = []
    drops = [
        r.hotspot_seen_db - c.hotspot_seen_db
        for c, r in zip(candidate, reference, strict=True)
        if c.hotspot_seen_db is not None and r.hotspot_seen_db is not None
    ]
    median_drop = statistics.median(drops) if drops else 0.0
    if median_drop < min_hotspot_drop_db:
        problems.append(f"hotspot drop {median_drop:.1f} dB < {min_hotspot_drop_db:.1f} dB")
    for c, r in zip(candidate, reference, strict=True):
        if c.club_points_pre < r.club_points_pre:
            problems.append(f"{c.name}: club points {c.club_points_pre} < {r.club_points_pre}")
        if r.ball_first_frame is not None and (
            c.ball_first_frame is None or c.ball_first_frame > r.ball_first_frame
        ):
            problems.append(
                f"{c.name}: first ball frame {c.ball_first_frame} later than {r.ball_first_frame}"
            )
        if r.triggered and not c.triggered:
            problems.append(f"{c.name}: no longer triggers")
    return problems
