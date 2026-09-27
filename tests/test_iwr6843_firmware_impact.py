"""Tests for the IWR6843 geometric impact detector, firmware/iwr6843/l3_impact.c.

Deliveries are built directly (a position, a velocity, a confidence in the
golf frame) so each rule is exercised on its own: closest approach and its
time, the tolerance, the horizon on either side, the speed and confidence
floors, the missing ball, and the once-only fire until rearm.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

WHY = {name: index for index, name in enumerate(fw.IMPACT_WHY_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def detector(lib, **overrides) -> fw.Impact:
    cfg = fw.ImpactCfg()
    lib.l3_impact_cfg_defaults(ctypes.byref(cfg))
    for name, value in overrides.items():
        setattr(cfg, name, value)
    impact = fw.Impact()
    lib.l3_impact_init(ctypes.byref(impact), ctypes.byref(cfg))
    return impact


def delivery(
    position=(1.40, 0.0, -0.10),
    velocity=(40.0, 0.0, 0.0),
    *,
    confidence: float = 0.9,
    points: int = 8,
    timestamp_us: int = 30_000,
    valid: bool = True,
) -> fw.Delivery:
    d = fw.Delivery()
    d.points = points
    d.position = fw.Vec3(*position)
    d.velocity = fw.Vec3(*velocity)
    d.speedMps = sum(v * v for v in velocity) ** 0.5
    d.confidence = confidence
    d.timestampUs = timestamp_us
    d.speedValid = 1 if valid else 0
    return d


def update(lib, impact, d, ball=(1.50, 0.0, -0.10), ball_valid=True) -> int:
    return lib.l3_impact_update(
        ctypes.byref(impact), ctypes.byref(d), ctypes.byref(fw.Vec3(*ball)), 1 if ball_valid else 0
    )


def why(impact) -> str:
    return fw.IMPACT_WHY_NAMES[impact.why]


def test_defaults_are_a_clubhead_sized_tolerance_and_a_frame_of_horizon(lib):
    cfg = fw.ImpactCfg()
    lib.l3_impact_cfg_defaults(ctypes.byref(cfg))
    assert cfg.toleranceM == pytest.approx(0.15)
    assert cfg.horizonS == pytest.approx(0.004)
    assert cfg.minSpeedMps == pytest.approx(5.0)
    assert cfg.minConfidence == pytest.approx(0.2)


def test_closest_approach_geometry(lib):
    position, velocity, ball = (
        fw.Vec3(0.0, 0.0, 0.0),
        fw.Vec3(10.0, 0.0, 0.0),
        fw.Vec3(2.0, 0.3, -0.4),
    )
    distance, offset, contact = ctypes.c_float(), ctypes.c_float(), fw.Vec3()
    lib.l3_impact_closest(
        ctypes.byref(position),
        ctypes.byref(velocity),
        ctypes.byref(ball),
        ctypes.byref(distance),
        ctypes.byref(offset),
        ctypes.byref(contact),
    )
    assert offset.value == pytest.approx(0.2)
    assert distance.value == pytest.approx(0.5)
    assert (contact.x, contact.y, contact.z) == pytest.approx((2.0, 0.0, 0.0))
    # A ball already passed sits at a negative offset.
    lib.l3_impact_closest(
        ctypes.byref(fw.Vec3(3.0, 0.0, 0.0)),
        ctypes.byref(velocity),
        ctypes.byref(ball),
        ctypes.byref(distance),
        ctypes.byref(offset),
        ctypes.byref(contact),
    )
    assert offset.value == pytest.approx(-0.1)
    # Zero velocity: the distance to the position itself.
    lib.l3_impact_closest(
        ctypes.byref(fw.Vec3(2.0, 0.0, 0.0)),
        ctypes.byref(fw.Vec3()),
        ctypes.byref(ball),
        ctypes.byref(distance),
        ctypes.byref(offset),
        ctypes.byref(contact),
    )
    assert offset.value == 0.0 and distance.value == pytest.approx(0.5)


def test_a_club_line_through_the_ball_fires_with_the_interpolated_impact_time(lib):
    impact = detector(lib)
    # 10 cm short of the ball at 40 m/s: contact in 2.5 ms, inside the horizon.
    assert update(lib, impact, delivery(position=(1.40, 0.0, -0.10))) == 1
    assert impact.fired == 1 and why(impact) == "fired"
    assert impact.closestM == pytest.approx(0.0, abs=1e-4)
    assert impact.offsetS == pytest.approx(0.0025, abs=1e-5)
    assert impact.impactTimestampUs == 32_500
    assert (impact.velocity.x, impact.velocity.y, impact.velocity.z) == (40.0, 0.0, 0.0)
    assert (impact.contact.x, impact.contact.y, impact.contact.z) == pytest.approx((1.5, 0.0, -0.1))


def test_impact_just_passed_still_fires_and_dates_impact_before_the_frame(lib):
    impact = detector(lib)
    assert update(lib, impact, delivery(position=(1.55, 0.0, -0.10), timestamp_us=33_000)) == 1
    assert impact.offsetS == pytest.approx(-0.00125, abs=1e-5)
    assert impact.impactTimestampUs == 31_750


def test_contact_beyond_the_horizon_is_pending_and_fires_on_a_later_frame(lib):
    impact = detector(lib)
    assert update(lib, impact, delivery(position=(1.10, 0.0, -0.10), timestamp_us=27_000)) == 0
    assert why(impact) == "pending" and impact.fired == 0
    assert impact.offsetS == pytest.approx(0.01, abs=1e-5)
    assert update(lib, impact, delivery(position=(1.22, 0.0, -0.10), timestamp_us=30_000)) == 0
    assert why(impact) == "pending"
    assert update(lib, impact, delivery(position=(1.40, 0.0, -0.10), timestamp_us=33_000)) == 1
    assert impact.counters[WHY["pending"]] == 2 and impact.counters[WHY["fired"]] == 1


def test_a_practice_swing_that_misses_the_ball_never_fires(lib):
    impact = detector(lib)
    # The club line passes 30 cm to the left of and above the ball.
    assert update(lib, impact, delivery(position=(1.40, -0.25, 0.10))) == 0
    assert why(impact) == "far" and impact.fired == 0
    assert impact.closestM == pytest.approx((0.25**2 + 0.2**2) ** 0.5, abs=1e-4)
    # Inside the tolerance it does.
    assert update(lib, impact, delivery(position=(1.40, -0.08, -0.05))) == 1


def test_a_swing_that_went_by_without_firing_is_recorded_as_passed(lib):
    impact = detector(lib)
    assert update(lib, impact, delivery(position=(1.90, 0.0, -0.10))) == 0
    assert why(impact) == "passed" and impact.offsetS == pytest.approx(-0.01, abs=1e-5)


def test_slow_or_unsure_deliveries_and_missing_fits_do_not_fire(lib):
    impact = detector(lib)
    assert update(lib, impact, delivery(velocity=(3.0, 0.0, 0.0))) == 0
    assert why(impact) == "slow"
    assert update(lib, impact, delivery(confidence=0.1)) == 0
    assert why(impact) == "unsure"
    assert update(lib, impact, delivery(valid=False)) == 0
    assert why(impact) == "nodelivery"
    assert update(lib, impact, delivery(points=2)) == 0
    assert why(impact) == "nodelivery"
    assert update(lib, impact, delivery(), ball_valid=False) == 0
    assert why(impact) == "noball"
    assert impact.fired == 0
    assert impact.counters[WHY["slow"]] == 1 and impact.counters[WHY["nodelivery"]] == 2


def test_fires_once_until_rearmed_and_rearm_keeps_counters_and_config(lib):
    impact = detector(lib, toleranceM=0.2)
    assert update(lib, impact, delivery()) == 1
    assert update(lib, impact, delivery()) == 0, "latched"
    assert impact.counters[WHY["fired"]] == 1
    lib.l3_impact_rearm(ctypes.byref(impact))
    assert impact.fired == 0 and why(impact) == "none" and impact.impactTimestampUs == 0
    assert impact.cfg.toleranceM == pytest.approx(0.2)
    assert update(lib, impact, delivery()) == 1
    assert impact.counters[WHY["fired"]] == 2


def test_a_body_walking_through_the_lane_is_too_slow_even_on_the_ball(lib):
    impact = detector(lib)
    assert (
        update(lib, impact, delivery(position=(1.499, 0.0, -0.10), velocity=(1.0, 0.0, 0.0))) == 0
    )
    assert why(impact) == "slow"


def test_why_names_and_format(lib):
    for index, name in enumerate(fw.IMPACT_WHY_NAMES):
        assert lib.l3_impact_why_name(index).decode() == name
    assert lib.l3_impact_why_name(99).decode() == "?"
    impact = detector(lib)
    update(lib, impact, delivery(position=(1.40, 0.02, -0.10)))
    text = fw.c_text(lib.l3_impact_format, ctypes.byref(impact), cap=240)
    assert text.startswith(
        "impact fired=1 why=fired closestcm=2.00 offsetms=2.50 t=32500 contact=1.50,0.02,-0.10"
    )
    assert text.endswith(" far=0 pending=0 passed=0 fired_n=1")
