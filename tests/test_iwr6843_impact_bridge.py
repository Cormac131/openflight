"""Impact bridged across the hotspot's contaminated frames (impact_bridge.py)."""

from __future__ import annotations

import pytest

from openflight.iwr6843.impact_bridge import ImpactBridge, bridge_impact

TEE_M = 2.0
IMPACT_US = 30_000.0


def line(speed, times_us, *, t0=IMPACT_US, r0=TEE_M, decel=0.0):
    return [
        (t, r0 + speed * (t - t0) * 1e-6 + 0.5 * decel * ((t - t0) * 1e-6) ** 2) for t in times_us
    ]


CLUB_IN = line(30.0, [12_000, 15_000, 18_000, 21_000, 24_000])
BALL_OUT = line(45.0, [39_000, 42_000, 45_000, 48_000])


class TestBridge:
    def test_meets_at_impact_across_the_gap(self):
        bridge = bridge_impact(CLUB_IN, BALL_OUT)
        assert bridge.impact_us == pytest.approx(IMPACT_US, abs=1.0)
        assert bridge.club_speed_mps == pytest.approx(30.0)
        assert bridge.ball_speed_mps == pytest.approx(45.0)
        assert bridge.gap_us == 15_000
        assert bridge.method == "intersection"
        assert (bridge.club_points, bridge.ball_points) == (5, 4)
        assert bridge.plausible

    def test_uses_only_the_last_club_and_first_ball_points(self):
        early_noise = [(1_000, 0.2), (2_000, 3.0)]
        late_noise = [(80_000, 0.5)]
        bridge = bridge_impact(early_noise + CLUB_IN, BALL_OUT + late_noise)
        assert bridge.impact_us == pytest.approx(IMPACT_US, abs=1.0)

    def test_unsorted_input(self):
        bridge = bridge_impact(list(reversed(CLUB_IN)), list(reversed(BALL_OUT)))
        assert bridge.impact_us == pytest.approx(IMPACT_US, abs=1.0)

    def test_quadratic_club_recovers_the_speed_at_impact(self):
        club = line(30.0, [12_000, 15_000, 18_000, 21_000, 24_000], decel=-400.0)
        bridge = bridge_impact(club, BALL_OUT, club_degree=2)
        assert bridge.impact_us == pytest.approx(IMPACT_US, abs=5.0)
        assert bridge.club_speed_mps == pytest.approx(30.0, abs=0.05)
        linear = bridge_impact(club, BALL_OUT)
        assert abs(linear.club_speed_mps - 30.0) > abs(bridge.club_speed_mps - 30.0)

    def test_quadratic_falls_back_to_the_line_when_implausible(self):
        wild = [(12_000, 1.40), (15_000, 1.55), (18_000, 1.65), (21_000, 1.70), (24_000, 1.72)]
        bridge = bridge_impact(wild, BALL_OUT, club_degree=2, ball_range_m=TEE_M)
        linear = bridge_impact(wild, BALL_OUT, ball_range_m=TEE_M)
        assert bridge.club_speed_mps == pytest.approx(linear.club_speed_mps)
        assert 10.0 <= bridge.club_speed_mps <= 70.0

    def test_rejects_other_degrees(self):
        with pytest.raises(ValueError):
            bridge_impact(CLUB_IN, BALL_OUT, club_degree=3)

    def test_anchored_when_the_lines_do_not_meet_in_the_window(self):
        parallel_ball = line(30.0, [39_000, 42_000, 45_000], r0=TEE_M + 0.3)
        assert bridge_impact(CLUB_IN, parallel_ball) is None
        bridge = bridge_impact(CLUB_IN, parallel_ball, ball_range_m=TEE_M)
        assert bridge.method == "anchored"

    @pytest.mark.parametrize("club, ball", [(CLUB_IN[:2], BALL_OUT), (CLUB_IN, BALL_OUT[:2])])
    def test_needs_three_points_each(self, club, ball):
        assert bridge_impact(club, ball, ball_range_m=TEE_M) is None

    def test_club_out_residual(self):
        club_out = line(20.0, [36_000, 39_000, 42_000])
        bridge = bridge_impact(CLUB_IN, BALL_OUT, club_out=club_out)
        assert bridge.club_out_residual_m == pytest.approx(0.0, abs=1e-6)
        missed = line(20.0, [36_000, 39_000], r0=TEE_M + 0.1)
        assert bridge_impact(CLUB_IN, BALL_OUT, club_out=missed).club_out_residual_m == (
            pytest.approx(0.1, abs=1e-6)
        )


class TestPlausible:
    def make(self, club, ball):
        return ImpactBridge(0.0, club, ball, 0.0, "intersection", 5, 4)

    @pytest.mark.parametrize(
        "club, ball, expected",
        [
            (30.0, 45.0, True),
            (5.0, 45.0, False),
            (30.0, 95.0, False),
            (40.0, 35.0, False),
            (None, 45.0, False),
        ],
    )
    def test_bounds(self, club, ball, expected):
        assert self.make(club, ball).plausible is expected
