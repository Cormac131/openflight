"""Late-window planning for outdoor range and on-course shots."""

import pytest

from openflight.clubs.physics import CLUB_PHYSICS
from openflight.clubs.types import ClubType
from openflight.iwr6843.late_window import (
    CHIRP_MAX_RANGE_M,
    IMPACT_WINDOW_S,
    RAW_DUMP_S,
    plan_late_window,
)

# Amateur TrackMan averages. Driver is the longest flight, not the timing limit.
_DRIVER = dict(ball_speed_mph=160.0, launch_angle_deg=12.0, spin_rpm=2500.0)
# l3sparse power cube plus track cells at 1,041,667 baud. A CP2105 stall is extra.
_SPARSE_DUMP_S = 1.0


def test_net_skips_the_late_window():
    plan = plan_late_window("net", **_DRIVER)

    assert plan.enabled is False
    assert plan.reason == "net"
    assert plan.looks == ()


@pytest.mark.parametrize("mode", ["outdoor", "on_course"])
def test_open_flight_schedules_looks_around_apex(mode):
    plan = plan_late_window(mode, **_DRIVER)

    assert plan.enabled is True
    assert plan.apex_t_s is not None
    pre, post = plan.looks
    assert pre.t_s < plan.apex_t_s < post.t_s
    assert post.downrange_m > pre.downrange_m
    assert pre.t_s > IMPACT_WINDOW_S
    assert pre.slant_range_m > CHIRP_MAX_RANGE_M
    assert post.slant_range_m > CHIRP_MAX_RANGE_M
    assert plan.visible_on_impact_chirp is False
    assert plan.host_dump_can_make_it is False
    assert pre.t_s < RAW_DUMP_S


def test_every_club_leaves_time_to_arm_after_the_sparse_dump():
    """The lob wedge apexes first. A driver-only check hides that budget."""
    tightest = None
    for club, physics in CLUB_PHYSICS.items():
        if club is ClubType.UNKNOWN:
            continue
        plan = plan_late_window(
            "outdoor",
            physics.average_ball_speed_mph,
            physics.optimal_launch_deg,
            physics.typical_spin_rpm,
        )
        assert plan.enabled is True
        pre, post = plan.looks
        assert pre.t_s < plan.apex_t_s < post.t_s
        assert pre.slant_range_m > CHIRP_MAX_RANGE_M
        spare_s = pre.t_s - (IMPACT_WINDOW_S + _SPARSE_DUMP_S)
        assert spare_s > 0.0, club.name
        if tightest is None or spare_s < tightest[0]:
            tightest = (spare_s, club)

    spare_s, club = tightest
    assert club in {ClubType.SW, ClubType.LW}
    assert spare_s < 1.0
