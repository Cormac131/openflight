"""Late-window planning for outdoor range and on-course shots."""

import pytest

from openflight.clubs.physics import CLUB_PHYSICS
from openflight.clubs.types import ClubType
import numpy as np

from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.monitor import tee_local_bin
from openflight.iwr6843.late_window import (
    CHIRP_MAX_RANGE_M,
    IMPACT_WINDOW_S,
    RAW_DUMP_S,
    capture_late_window,
    descent_angle_deg,
    late_window_record,
    long_range_cfg,
    long_range_span_m,
    measured_ranges,
    net_gate_m,
    plan_late_window,
)

# Amateur TrackMan averages. Driver is the longest flight, not the timing limit.
_DRIVER = dict(ball_speed_mph=160.0, launch_angle_deg=12.0, spin_rpm=2500.0)
# l3sparse power cube plus track cells at 1,041,667 baud. A CP2105 stall is extra.
_SPARSE_DUMP_S = 1.0


def test_enable_flag_bin_comes_from_the_tee_distance():
    cfg = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
    assert tee_local_bin(1.575, cfg) == 14


def test_net_keeps_the_gate_and_skips_the_record():
    assert net_gate_m("net", 4.6) == 4.6
    assert (
        late_window_record(
            "net",
            ball_speed_mph=100.0,
            launch_angle_deg=20.0,
            spin_rpm=6500.0,
            tee_range_m=1.5,
        )
        is None
    )


@pytest.mark.parametrize("flight", ["range", "course"])
def test_open_flight_drops_the_net_gate_and_records_looks(flight):
    assert net_gate_m(flight, 4.6) is None
    record = late_window_record(
        flight,
        ball_speed_mph=100.0,
        launch_angle_deg=20.0,
        spin_rpm=6500.0,
        tee_range_m=1.5,
    )
    assert record is not None
    assert record["enabled"] is True
    assert len(record["looks"]) == 2


def test_late_dump_peak_is_the_measured_slant():
    cube = np.zeros((1, 2, 4, 16), dtype=np.complex128)
    cube[0, :, :, 10] = 1.0
    raw = pack_dump(cube, n_tx=2)
    ranges = measured_ranges(raw, span_m=128.0)
    assert ranges[0]["slant_range_m"] == pytest.approx(10.0)


def test_capture_skips_when_the_first_look_has_passed():
    plan = plan_late_window("outdoor", **_DRIVER)

    class Radar:
        def send_config(self, _path):
            raise AssertionError("retune")

    assert (
        capture_late_window(
            Radar(),
            plan,
            impact_timestamp=0.0,
            tee_range_m=1.5,
            restore_cfg="impact.cfg",
            now=lambda: plan.looks[0].t_s,
            sleep=lambda _seconds: None,
        )
        is None
    )


def test_capture_retunes_dumps_and_restores():
    plan = plan_late_window("outdoor", **_DRIVER)
    clock = {"t": 0.0}

    class Radar:
        def __init__(self):
            self.configs = []

        def send_config(self, path):
            self.configs.append(path)

        def read_dump(self):
            cube = np.zeros((1, 2, 4, 16), dtype=np.complex128)
            cube[0, :, :, 4] = 1.0
            return pack_dump(cube, n_tx=2)

    radar = Radar()
    measured = capture_late_window(
        radar,
        plan,
        impact_timestamp=0.0,
        tee_range_m=1.5,
        restore_cfg="impact.cfg",
        now=lambda: clock["t"],
        sleep=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
    )
    assert measured is not None
    assert measured["span_m"] == pytest.approx(long_range_span_m(plan.looks[-1].slant_range_m))
    assert measured["ranges"][0]["slant_range_m"] > 0.0
    assert radar.configs[-1] == "impact.cfg"
    assert len(radar.configs) == 2


def test_late_profile_keeps_a_pretrigger_ring_across_both_looks():
    plan = plan_late_window("outdoor", **_DRIVER)
    cfg = long_range_cfg(plan)
    phase = next(line for line in cfg.splitlines() if line.startswith("phaseCaptureCfg"))
    pre_frames = int(phase.split()[3])
    assert pre_frames >= 8
    assert "captureFormat iq16" in cfg


def test_descent_is_the_downward_chord_of_the_two_looks():
    plan = plan_late_window("outdoor", **_DRIVER)
    before, after = plan.looks
    ranges = [
        {"t_s": before.t_s, "slant_range_m": before.slant_range_m},
        {"t_s": after.t_s, "slant_range_m": after.slant_range_m * 0.98},
    ]
    descent = descent_angle_deg(plan, ranges, tee_range_m=1.5)
    assert descent is not None
    assert descent > 0.0


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
