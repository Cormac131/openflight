"""Impact through the hotspot: bridge the contaminated frames, don't detect them.

The club track runs into the golfer/impact hotspot and the ball (and club)
come out of it a few frames later. Rather than require a clean detection at
impact, fit the approaching club and the departing ball on their own clean
points and solve for when the two trajectories meet:

    r_club(t_i) = r_ball(t_i)

Both fits are linear by default (the ball's speed hardly changes over
milliseconds; the club's deceleration is not resolvable on five noisy points:
on the recordings a quadratic extrapolated 3-12 ms read -62 to 148 m/s).
``club_degree=2`` fits the club quadratically and falls back to the line
whenever the quadratic's speed at impact leaves the club's bounds. The
speeds reported are each fit's rate at ``t_i``: ``v_club(t_i)`` and
``v_ball(t_i)``, extrapolated back through the bridged frames. When the fits
do not meet within the window, the ball's range at the tee anchors a shared
impact time instead (:func:`impact_eval.joint_fit_impact`, method C).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.impact_eval import joint_fit_impact

Track = Sequence[tuple[float, float]]  # (t_us, range_m)
CLUB_FIT_POINTS = 5
BALL_FIT_POINTS = 4
MIN_POINTS = 3
QUADRATIC_MIN_POINTS = 4
SEARCH_MARGIN_US = 10_000.0
CLUB_SPEED_BOUNDS_MPS = (10.0, 70.0)  # l3_impact_fit's bounds
BALL_SPEED_BOUNDS_MPS = (15.0, 90.0)


@dataclass(frozen=True)
class ImpactBridge:
    """The bridged impact: when, how fast each object was going, how."""

    impact_us: float
    club_speed_mps: float | None
    ball_speed_mps: float | None
    gap_us: float  # between the last clean club point and the first ball point
    method: str  # "intersection" or "anchored"
    club_points: int
    ball_points: int
    club_out_residual_m: float | None = None  # the club-out line's miss at (t_i, r_i)

    @property
    def plausible(self) -> bool:
        """Both speeds inside the impact fit's bounds and the ball faster."""
        if self.club_speed_mps is None or self.ball_speed_mps is None:
            return False
        club_lo, club_hi = CLUB_SPEED_BOUNDS_MPS
        ball_lo, ball_hi = BALL_SPEED_BOUNDS_MPS
        return (
            club_lo <= self.club_speed_mps <= club_hi
            and ball_lo <= self.ball_speed_mps <= ball_hi
            and self.ball_speed_mps > self.club_speed_mps
        )


def _fit(track: Track, degree: int, t0_us: float) -> np.poly1d:
    t = np.array([p[0] for p in track], dtype=float)
    r = np.array([p[1] for p in track], dtype=float)
    return np.poly1d(np.polyfit((t - t0_us) * 1e-6, r, degree))


def _intersection(club: np.poly1d, ball: np.poly1d, lo_s: float, hi_s: float, mid_s: float):
    roots = np.roots((club - ball).coeffs) if (club - ball).order > 0 else np.array([])
    real = [float(r.real) for r in np.atleast_1d(roots) if abs(r.imag) < 1e-9]
    inside = [r for r in real if lo_s <= r <= hi_s]
    return min(inside, key=lambda r: abs(r - mid_s)) if inside else None


def bridge_impact(  # pylint: disable=too-many-arguments
    club_in: Track,
    ball_out: Track,
    *,
    club_out: Track = (),
    ball_range_m: float | None = None,
    search_margin_us: float = SEARCH_MARGIN_US,
    club_degree: int = 1,
) -> ImpactBridge | None:
    """Impact from the club's last clean points and the ball's first ones.

    ``club_in`` and ``ball_out`` are ``(t_us, range_m)`` in time order; the
    last ``CLUB_FIT_POINTS`` and first ``BALL_FIT_POINTS`` are fitted. The
    meeting time must lie between ``search_margin_us`` before the last club
    point and after the first ball point. ``club_out`` (optional) is checked
    against the result, not fitted into it. None without ``MIN_POINTS`` on
    both tracks, or when the fits neither meet nor can be anchored.
    """
    if club_degree not in (1, 2):
        raise ValueError(f"club_degree must be 1 or 2, got {club_degree}")
    club = sorted(club_in)[-CLUB_FIT_POINTS:]
    ball = sorted(ball_out)[:BALL_FIT_POINTS]
    if len(club) < MIN_POINTS or len(ball) < MIN_POINTS:
        return None
    last_club_us, first_ball_us = club[-1][0], ball[0][0]
    t0 = last_club_us
    ball_fit = _fit(ball, 1, t0)
    lo_s = (min(last_club_us, first_ball_us) - search_margin_us - t0) * 1e-6
    hi_s = (max(last_club_us, first_ball_us) + search_margin_us - t0) * 1e-6
    mid_s = 0.5 * (last_club_us + first_ball_us - 2.0 * t0) * 1e-6
    degrees = (2, 1) if club_degree == 2 and len(club) >= QUADRATIC_MIN_POINTS else (1,)
    for degree in degrees:
        club_fit = _fit(club, degree, t0)
        t_i = _intersection(club_fit, ball_fit, lo_s, hi_s, mid_s)
        method = "intersection"
        if t_i is None:
            if ball_range_m is None:
                continue
            anchored = joint_fit_impact(club, (), ball, ball_range_m)
            if anchored is None:
                continue
            t_i, method = (anchored - t0) * 1e-6, "anchored"
        lo, hi = CLUB_SPEED_BOUNDS_MPS
        if degree == 1 or lo <= float(club_fit.deriv()(t_i)) <= hi:
            break
    else:
        return None
    if t_i is None:
        return None
    club_speed = float(club_fit.deriv()(t_i))
    ball_speed = float(ball_fit.deriv()(t_i))
    residual = None
    out = sorted(club_out)[:BALL_FIT_POINTS]
    if len(out) >= 2:
        out_fit = _fit(out, 1, t0)
        residual = float(out_fit(t_i) - club_fit(t_i))
    return ImpactBridge(
        impact_us=t0 + t_i * 1e6,
        club_speed_mps=club_speed,
        ball_speed_mps=ball_speed,
        gap_us=first_ball_us - last_club_us,
        method=method,
        club_points=len(club),
        ball_points=len(ball),
        club_out_residual_m=residual,
    )
