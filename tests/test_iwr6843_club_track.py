"""Gate parameterisation: one fitter, two targets.

The club is slower and closer than the ball. find_ball's own docstring notes
the club steals the track when gates are wrong, so the fitter finds clubs
already — it just needs pointing at one deliberately.
"""

import numpy as np
import pytest

from openflight.iwr6843 import tracking
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.shot import geometry_from_header


def _synth(range_m_at, speed_ms, *, n_frames=18, loops=12, n_rx=4, n_samples=128):
    """Pack a single mover with a known range walk into the wire format."""
    from openflight.iwr6843.dump import pack_dump

    res = 6.0 / n_samples
    cube = np.zeros((n_frames, loops * 2, n_rx, n_samples), dtype=complex)
    for frame in range(n_frames):
        for loop in range(loops * 2):
            t = frame * 4e-3 + (loop // 2) * 90e-6
            bin_at = (range_m_at + speed_ms * t) / res
            lo = int(bin_at)
            if 0 <= lo < n_samples - 1:
                frac = bin_at - lo
                cube[frame, loop, :, lo] = 1000.0 * (1 - frac)
                cube[frame, loop, :, lo + 1] = 1000.0 * frac
    return pack_dump(cube, n_tx=2, version=3, frame_period_us=4000)


def _track(raw, **kwargs):
    meta, cube = parse_dump(raw)
    geo = geometry_from_header(meta)
    # range_domain=True: this fixture writes the target directly onto the
    # range-bin axis (see _synth), so mti_filter must not re-FFT it — doing
    # so would flatten the spike into a broadband, non-localized spectrum.
    mti = tracking.mti_filter(cube, range_domain=True, geometry=geo)
    return tracking.find_ball(mti, geo, **kwargs)


def test_club_gates_find_a_slow_close_mover():
    """A 22 m/s mover at 1.6 m is invisible to ball gates, found with club gates."""
    # n_frames=6 (not the 18-frame default): over 18 frames a 22 m/s radial
    # walk from 1.6 m crosses into the ball gate (>= 2.25 m) and IS found by
    # the ball search, defeating the point of this test. 6 frames caps the
    # walk under 2.1 m, safely inside club range for the whole capture.
    raw = _synth(1.6, 22.0, n_frames=6)

    with_ball_gates = _track(raw, min_ball_ms=26.5)
    assert with_ball_gates is None, (
        "ball gates (2.25-5.5 m, 20-90 m/s) must not claim a club-range mover"
    )

    with_club_gates = _track(
        raw,
        gates_m=((1.0, 2.4),),
        speed_bounds_ms=(10.0, 45.0),
        min_ball_ms=10.0,
    )
    assert with_club_gates is not None, "club gates failed to find the synthetic club"
    assert with_club_gates.speed_ms == pytest.approx(22.0, abs=2.0)


def test_ball_behaviour_unchanged_by_defaults():
    """Defaults must reproduce today's behaviour exactly."""
    raw = _synth(2.6, 45.0)
    explicit = _track(
        raw, gates_m=tracking.BALL_GATES_M, speed_bounds_ms=tracking.SPEED_BOUNDS_MS
    )
    implicit = _track(raw)
    assert (explicit is None) == (implicit is None)
    if explicit is not None:
        assert explicit.speed_ms == implicit.speed_ms
        assert explicit.n_inliers == implicit.n_inliers


def test_time_window_excludes_a_later_mover():
    """The club search must not be able to claim the ball.

    Two movers: a slow one early, a fast one late. Both are inside the club
    speed bounds, so only the time window separates them.
    """
    # n_frames=6: same club-range constraint as test_club_gates_find_a_slow_close_mover.
    raw_early = _synth(1.6, 22.0, n_frames=6)
    early = _track(
        raw_early,
        gates_m=((1.0, 2.4),),
        speed_bounds_ms=(10.0, 45.0),
        min_ball_ms=10.0,
        time_window_s=(0.0, 0.024),
    )
    assert early is not None
    assert early.t_last <= 0.024 + 1e-9, "window must bound the fitted track"

    late_only = _track(
        raw_early,
        gates_m=((1.0, 2.4),),
        speed_bounds_ms=(10.0, 45.0),
        min_ball_ms=10.0,
        time_window_s=(0.050, 0.072),
    )
    if late_only is not None:
        assert late_only.t_first >= 0.050 - 1e-9
