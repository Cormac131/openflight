"""Tests for the IWR6843 swing zone, firmware/iwr6843/l3_zone.c.

A box in the golf frame around the tee: x along the target line, y right of
it, z up from the antenna, heights judged above the floor. Each limit a point
breaks is its own reason bit, so a replay or a log can say why a track left
the corridor.
"""

from __future__ import annotations

import ctypes
import math

import pytest

from openflight.iwr6843 import firmware_host as fw

TEE_X = 2.0
RADAR_H = 0.15
BOTH = fw.ANGLE_AZIMUTH | fw.ANGLE_ELEVATION
BIT = {name: 1 << i for i, name in enumerate(fw.ZONE_REASON_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def zone(lib, **overrides) -> fw.ZoneCfg:
    cfg = fw.ZoneCfg()
    lib.l3_zone_cfg_defaults(ctypes.byref(cfg), TEE_X, RADAR_H)
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def check(lib, cfg, x, y, height_above_floor, angles=BOTH) -> set[str]:
    """The reason names for a point at (x, y) and a height above the floor."""
    golf = fw.Vec3(x, y, height_above_floor - RADAR_H)
    bits = lib.l3_zone_check(ctypes.byref(cfg), ctypes.byref(golf), angles)
    return {name for name, bit in BIT.items() if bits & bit}


def test_the_reason_names_match_the_c(lib):
    assert len(fw.ZONE_REASON_NAMES) == 7
    for name, bit in BIT.items():
        assert lib.l3_zone_reason_name(bit).decode() == name
    assert lib.l3_zone_reason_name(0).decode() == "?"
    assert lib.l3_zone_reason_name(3).decode() == "?"
    assert lib.l3_zone_reason_name(1 << 7).decode() == "?"


def test_the_defaults_are_the_starting_corridor(lib):
    cfg = zone(lib)
    assert cfg.teeForwardM == pytest.approx(TEE_X) and cfg.radarHeightM == pytest.approx(RADAR_H)
    assert cfg.lateralM == 0.0
    assert (cfg.shortM, cfg.pastM, cfg.halfWidthM) == pytest.approx((1.2, 0.3, 0.3))
    assert (cfg.minHeightM, cfg.maxHeightM) == pytest.approx((-0.1, 1.2))
    assert cfg.requireAngles == 1
    assert lib.l3_zone_cfg_check(ctypes.byref(cfg)) == 0


def test_the_club_at_the_ball_is_inside(lib):
    assert check(lib, zone(lib), TEE_X - 0.1, 0.05, 0.02) == set()


@pytest.mark.parametrize(
    "x, y, h, reasons",
    [
        (TEE_X - 1.3, 0.0, 0.1, {"short"}),
        (TEE_X + 0.4, 0.0, 0.1, {"past"}),
        (TEE_X, -0.35, 0.1, {"left"}),
        (TEE_X, 0.35, 0.1, {"right"}),
        (TEE_X, 0.0, -0.2, {"low"}),
        (TEE_X, 0.0, 1.5, {"high"}),
        (TEE_X - 1.5, 0.9, 1.6, {"short", "right", "high"}),  # a bystander's head
    ],
)
def test_every_broken_limit_is_named(lib, x, y, h, reasons):
    assert check(lib, zone(lib), x, y, h) == reasons


@pytest.mark.parametrize(
    "x, y, h",
    [
        (TEE_X - 1.2, 0.0, 0.1),
        (TEE_X + 0.3, 0.0, 0.1),
        (TEE_X, -0.3, 0.1),
        (TEE_X, 0.3, 0.1),
        (TEE_X, 0.0, -0.1),
        (TEE_X, 0.0, 1.2),
    ],
)
def test_the_limits_themselves_are_inside(lib, x, y, h):
    assert check(lib, zone(lib), x, y, h) == set()


def test_the_corridor_follows_its_centre_line(lib):
    """A tee off the target line (or a radar aimed off it) moves the corridor."""
    cfg = zone(lib, lateralM=0.5)
    assert check(lib, cfg, TEE_X, 0.5, 0.1) == set()
    assert check(lib, cfg, TEE_X, 0.0, 0.1) == {"left"}


def test_without_both_angles_only_the_range_is_judged(lib):
    """A point without angles sits on boresight: its lateral offset and height
    are not measured, so they are not judged, and the point is refused when
    the zone needs angles."""
    cfg = zone(lib)
    for angles in (0, fw.ANGLE_AZIMUTH, fw.ANGLE_ELEVATION):
        assert check(lib, cfg, TEE_X, 5.0, 9.0, angles) == {"no_angles"}
        assert check(lib, cfg, TEE_X - 2.0, 5.0, 9.0, angles) == {"no_angles", "short"}
    relaxed = zone(lib, requireAngles=0)
    assert check(lib, relaxed, TEE_X, 5.0, 9.0, 0) == set()
    assert check(lib, relaxed, TEE_X + 1.0, 5.0, 9.0, 0) == {"past"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"shortM": -0.1},
        {"pastM": -0.1},
        {"halfWidthM": -0.1},
        {"minHeightM": 1.0, "maxHeightM": 0.5},
        {"teeForwardM": math.nan},
        {"halfWidthM": math.inf},
    ],
)
def test_a_corridor_that_cannot_exist_is_refused(lib, overrides):
    assert lib.l3_zone_cfg_check(ctypes.byref(zone(lib, **overrides))) == -1
