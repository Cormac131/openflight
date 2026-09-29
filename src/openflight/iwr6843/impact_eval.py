"""Impact evaluation over replayed captures: method A (the firmware's
l3_impact_fit, read from the replay) against method C, one joint fit sharing
the impact time, implemented here only as a comparator.

See docs/superpowers/specs/2026-09-28-iwr-impact-back-interpolation-design.md,
"Method gate: A vs C".
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np

Track = Sequence[tuple[float, float]]  # (t_us, range_m)
FIT_POINTS = 4
MIN_POINTS = 3
SEARCH_MARGIN_US = 20_000.0
C_WIN_FACTOR = 0.7  # C must cut the median spread by at least 30 %
RANGE_SPAN_M = 6.0  # every cfg keeps a 6 m span over its range FFT


def _anchored_rss(track: Track, t_i: float, ball_range_m: float) -> float:
    """Squared residual of the least-squares line through (t_i, ball range)."""
    dt = np.array([t - t_i for t, _ in track]) * 1e-6
    dr = np.array([r - ball_range_m for _, r in track])
    denominator = float(dt @ dt)
    if denominator <= 0.0:
        return float(dr @ dr)
    slope = float(dt @ dr) / denominator
    residual = dr - slope * dt
    return float(residual @ residual)


def joint_fit_impact(
    club_in: Track, club_out: Track, ball_out: Track, ball_range_m: float, *, step_us: float = 50.0
) -> float | None:
    """Method C: the impact time shared by every track, each a line through
    (t_i, ball range); None with fewer than two tracks of MIN_POINTS."""
    tracks = [
        list(club_in)[-FIT_POINTS:],
        list(club_out)[:FIT_POINTS],
        list(ball_out)[:FIT_POINTS],
    ]
    tracks = [t for t in tracks if len(t) >= MIN_POINTS]
    if len(tracks) < 2:
        return None
    times = [t for track in tracks for t, _ in track]
    grid = np.arange(min(times) - SEARCH_MARGIN_US, max(times) + step_us, step_us)
    costs = [sum(_anchored_rss(track, t_i, ball_range_m) for track in tracks) for t_i in grid]
    return float(grid[int(np.argmin(costs))])


def leave_one_out_spread_us(
    club_in: Track, club_out: Track, ball_out: Track, ball_range_m: float
) -> float | None:
    """Method C's spread: max - min of the fit on each two-track subset;
    None unless all three tracks have MIN_POINTS."""
    tracks = (club_in, club_out, ball_out)
    if any(len(t) < MIN_POINTS for t in tracks):
        return None
    fits = []
    for keep in combinations(range(3), 2):
        subset = [tracks[i] if i in keep else [] for i in range(3)]
        fits.append(joint_fit_impact(*subset, ball_range_m))
    return max(fits) - min(fits)


@dataclass(frozen=True)
class ImpactOutcome:
    """One capture's impact: method A's verdict and spread, method C's spread."""

    name: str
    verdict: str
    tracks_ok: tuple[str, ...]
    spread_us: float | None
    refined_minus_trigger_us: float | None
    c_spread_us: float | None
    club_points_in_band: int


def _median(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return statistics.median(kept) if kept else None


def summarize_impact(outcomes: Sequence[ImpactOutcome]) -> dict:
    """The counts and medians the spec's impact acceptance is written in."""
    consistent = [o for o in outcomes if o.verdict == "consistent"]
    median_a = _median(o.spread_us for o in consistent)
    median_c = _median(o.c_spread_us for o in consistent)
    a_available = sum(1 for o in outcomes if o.verdict != "none")
    c_available = sum(1 for o in outcomes if o.c_spread_us is not None)
    return {
        "captures": len(outcomes),
        "with_estimate": a_available,
        "consistent": len(consistent),
        "inconsistent": sum(1 for o in outcomes if o.verdict == "inconsistent"),
        "single_track": sum(1 for o in outcomes if o.verdict == "single_track"),
        "none": sum(1 for o in outcomes if o.verdict == "none"),
        "median_spread_us": median_a,
        "median_c_spread_us": median_c,
        "median_refined_minus_trigger_us": _median(o.refined_minus_trigger_us for o in outcomes),
        "club_points_in_band": sum(o.club_points_in_band for o in outcomes),
        "c_wins": bool(
            median_a is not None
            and median_c is not None
            and median_c <= C_WIN_FACTOR * median_a
            and c_available >= len(consistent)
        ),
    }


def impact_outcome(name: str, result) -> ImpactOutcome:
    """One replayed capture's impact outcome (firmware_replay.ReplayResult)."""
    fit = result.impact_fit
    bin_m = RANGE_SPAN_M / result.config.fft_size
    ball_m = result.config.destination * bin_m
    impact_frame = result.shot.impactFrame if fit is not None else None
    in_band = 0
    if result.band is not None:
        lo, hi = result.band
        in_band = sum(
            1
            for p in result.points
            if (impact_frame is None or p.frame <= impact_frame) and lo <= p.range_bin <= hi
        )
    if fit is None:
        return ImpactOutcome(name, "none", (), None, None, None, in_band)
    club_in = [(p.timestamp_us, p.range_m) for p in result.points if p.frame <= impact_frame]
    club_out = [(p.timestamp_us, p.range_m) for p in result.points if p.frame > impact_frame]
    ball_out = [(p.timestamp_us, p.range_m) for p in result.ball_points]
    return ImpactOutcome(
        name,
        fit.verdict,
        tuple(n for n, t in fit.tracks.items() if t.why == "ok"),
        fit.spread_us if fit.verdict != "none" else None,
        fit.refined_minus_trigger_us,
        leave_one_out_spread_us(club_in, club_out, ball_out, ball_m),
        in_band,
    )
