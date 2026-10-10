"""Tests for src/openflight/iwr6843/swing_zone.py: the firmware's corridor
placed from the tee's slant range and applied to replayed track points."""

from __future__ import annotations

import math

import pytest

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr
from openflight.iwr6843.swing_zone import (
    SwingZone,
    check_point,
    check_points,
    tee_forward_m,
    zone_cfg_dict,
    zone_summary,
)

RADAR_H = 0.15


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def point(x, y, h, angles=True) -> fr.PointSummary:
    """A replayed track point at (x, y) and a height above the floor."""
    return fr.PointSummary(
        frame=0,
        timestamp_us=0,
        range_bin=40.0,
        range_m=x,
        doppler_mps=0.0,
        confidence=1.0,
        position=(x, y, h - RADAR_H),
        angles_valid=angles,
    )


def test_the_tee_forward_distance_comes_from_its_slant_range():
    assert tee_forward_m(2.0, RADAR_H, 0.04) == pytest.approx(math.sqrt(4.0 - 0.11**2))
    assert tee_forward_m(1.5, 0.04, 0.04) == pytest.approx(1.5)


@pytest.mark.parametrize("tee_range", [0.05, 0.0, -1.0, math.nan])
def test_a_tee_nearer_than_its_drop_is_refused(tee_range):
    with pytest.raises(ValueError, match="cannot sit"):
        tee_forward_m(tee_range, RADAR_H, 0.04)


def test_unset_fields_keep_the_firmware_defaults(lib):
    cfg = SwingZone().cfg(lib, 2.0, RADAR_H)
    defaults = fw.ZoneCfg()
    lib.l3_zone_cfg_defaults(defaults, 2.0, RADAR_H)
    assert zone_cfg_dict(cfg) == zone_cfg_dict(defaults)


def test_set_fields_override_their_c_value(lib):
    cfg = SwingZone(half_width_m=0.2, lateral_m=0.1, max_height_m=0.8, require_angles=False).cfg(
        lib, 2.0, RADAR_H
    )
    assert cfg.halfWidthM == pytest.approx(0.2) and cfg.lateralM == pytest.approx(0.1)
    assert cfg.maxHeightM == pytest.approx(0.8) and cfg.requireAngles == 0
    assert cfg.shortM == pytest.approx(1.2), "an unset field keeps the default"


def test_every_override_names_a_c_field():
    names = {name for name, _ in fw.ZoneCfg._fields_}
    assert set(SwingZone._C_FIELDS.values()) <= names
    assert set(SwingZone._C_FIELDS) == {f for f in SwingZone.__dataclass_fields__}


def test_a_corridor_that_cannot_exist_is_refused(lib):
    with pytest.raises(ValueError, match="cannot exist"):
        SwingZone(min_height_m=1.0, max_height_m=0.5).cfg(lib, 2.0, RADAR_H)


def test_points_are_judged_with_their_reasons(lib):
    cfg = SwingZone().cfg(lib, 2.0, RADAR_H)
    assert check_point(lib, cfg, point(1.9, 0.0, 0.05)).inside
    assert check_point(lib, cfg, point(1.9, 0.6, 1.5)).reasons == ("right", "high")
    assert check_point(lib, cfg, point(1.9, 0.0, 0.05, angles=False)).reasons == ("no_angles",)


def test_a_point_without_a_position_is_judged_at_the_antenna(lib):
    cfg = SwingZone().cfg(lib, 2.0, RADAR_H)
    bare = fr.PointSummary(
        frame=0, timestamp_us=0, range_bin=0.0, range_m=0.0, doppler_mps=0.0, confidence=0.0
    )
    assert check_point(lib, cfg, bare).reasons == ("no_angles", "short")


def test_the_summary_counts_points_inside_and_each_broken_limit(lib):
    cfg = SwingZone().cfg(lib, 2.0, RADAR_H)
    verdicts = check_points(
        lib,
        cfg,
        [point(1.9, 0.0, 0.05), point(1.9, 0.6, 1.5), point(0.3, 0.6, 0.05), point(1.9, 0.0, 0.0)],
    )
    summary = zone_summary(verdicts)
    assert summary["points"] == 4 and summary["inside"] == 2
    assert summary["reasons"]["right"] == 2 and summary["reasons"]["high"] == 1
    assert summary["reasons"]["short"] == 1 and summary["reasons"]["left"] == 0
    assert list(summary["reasons"]) == list(fw.ZONE_REASON_NAMES)
