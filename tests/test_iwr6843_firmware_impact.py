"""Tests for the IWR6843 range-only impact, firmware/iwr6843/l3_impact.c.

The self-trigger fires on it: the club-in line fitted to the club track
(l3_impact_fit_track) crosses the ball's range within a horizon of the current
frame's time. The geometric detector that judged the club's 3D line against
the ball's position was removed on 2026-09-30: nothing armed it on the kiosk
and it never fired on the recorded swings.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

WHY = {name: index for index, name in enumerate(fw.IMPACT_WHY_NAMES)}
FIT_WHY = {name: i for i, name in enumerate(fw.FIT_WHY_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def range_impact(lib, **overrides) -> fw.Impact:
    cfg = fw.ImpactCfg()
    lib.l3_impact_cfg_defaults(ctypes.byref(cfg))
    for name, value in overrides.items():
        setattr(cfg, name, value)
    impact = fw.Impact()
    lib.l3_impact_init(ctypes.byref(impact), ctypes.byref(cfg))
    return impact


def club_in_estimate(time_us: float, why: str = "ok") -> fw.FitEstimate:
    e = fw.FitEstimate()
    e.why, e.timeUs, e.sigmaUs, e.speedMps, e.points = FIT_WHY[why], time_us, 300.0, 30.0, 4
    return e


def update(lib, impact, estimate, now_us: int) -> int:
    ref = None if estimate is None else ctypes.byref(estimate)
    return lib.l3_impact_update_range(ctypes.byref(impact), ref, now_us)


def test_the_default_horizon_is_a_frame_and_some_slack():
    """One 3 ms frame plus scheduling slack: the only setting left."""
    assert [name for name, _type in fw.ImpactCfg._fields_] == ["horizonS"]


def test_the_default_horizon_value(lib):
    assert range_impact(lib).cfg.horizonS == pytest.approx(0.004)


def test_range_impact_waits_until_the_crossing_is_within_the_horizon(lib):
    impact = range_impact(lib)
    e = club_in_estimate(30_000)
    assert update(lib, impact, e, 20_000) == 0
    assert impact.why == WHY["pending"]
    assert update(lib, impact, e, 27_000) == 1
    assert impact.why == WHY["fired"]
    assert impact.impactTimestampUs == 30_000
    assert impact.offsetS == pytest.approx(0.003, abs=1e-6)


def test_a_crossing_just_passed_still_fires_and_dates_impact_before_the_frame(lib):
    impact = range_impact(lib)
    assert update(lib, impact, club_in_estimate(30_000), 32_000) == 1
    assert impact.impactTimestampUs == 30_000
    assert impact.offsetS == pytest.approx(-0.002, abs=1e-6)


def test_range_impact_long_past_is_passed_not_fired(lib):
    impact = range_impact(lib)
    assert update(lib, impact, club_in_estimate(30_000), 40_000) == 0
    assert impact.why == WHY["passed"]


@pytest.mark.parametrize("why", ["missing", "few_points", "speed_bounds"])
def test_range_impact_without_a_club_in_estimate_does_not_fire(lib, why):
    impact = range_impact(lib)
    assert update(lib, impact, club_in_estimate(30_000, why), 29_000) == 0
    assert impact.why == WHY["nodelivery"]
    assert update(lib, impact, None, 29_000) == 0
    assert impact.why == WHY["nodelivery"]


def test_a_wider_horizon_fires_earlier(lib):
    impact = range_impact(lib, horizonS=0.012)
    assert update(lib, impact, club_in_estimate(30_000), 20_000) == 1


def test_fires_once_until_rearmed_and_rearm_keeps_counters_and_config(lib):
    impact = range_impact(lib, horizonS=0.005)
    e = club_in_estimate(30_000)
    assert update(lib, impact, e, 29_000) == 1
    assert update(lib, impact, e, 30_000) == 0, "fired: later frames are ignored"
    lib.l3_impact_rearm(ctypes.byref(impact))
    assert impact.fired == 0 and impact.why == WHY["none"] and impact.impactTimestampUs == 0
    assert impact.counters[WHY["fired"]] == 1
    assert impact.cfg.horizonS == pytest.approx(0.005)
    assert update(lib, impact, e, 29_000) == 1


def test_range_impact_fires_across_the_uint32_wrap(lib):
    # The fitted crossing lies just past 2**32 (a float), now has wrapped to 0.
    impact = range_impact(lib)
    e = club_in_estimate(2.0**32 + 1024.0)
    assert lib.l3_impact_update_range(ctypes.byref(impact), ctypes.byref(e), 0) == 1
    assert impact.impactTimestampUs == 1024
    assert impact.offsetS == pytest.approx(0.001024, abs=1e-6)


def test_why_names_and_format(lib):
    assert fw.IMPACT_WHY_NAMES == ("none", "nodelivery", "pending", "passed", "fired")
    for index, name in enumerate(fw.IMPACT_WHY_NAMES):
        assert lib.l3_impact_why_name(index).decode() == name
    assert lib.l3_impact_why_name(99).decode() == "?"
    impact = range_impact(lib)
    update(lib, impact, club_in_estimate(30_000), 20_000)
    update(lib, impact, club_in_estimate(30_000), 27_500)
    text = fw.c_text(lib.l3_impact_format, ctypes.byref(impact), cap=240)
    assert text == "impact fired=1 why=fired offsetms=2.50 t=30000 pending=1 passed=0 fired_n=1"


def test_the_geometric_detector_is_gone(lib):
    for gone in ("l3_impact_update", "l3_impact_closest"):
        assert not hasattr(lib, gone), gone
    for gone in ("closestM", "contact", "velocity"):
        assert gone not in {name for name, _type in fw.Impact._fields_}
