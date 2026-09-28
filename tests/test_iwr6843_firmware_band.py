"""Tests for the tee band, firmware/iwr6843/l3_band.c."""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def band(lib, centre: float, half: float) -> fw.Band:
    out = fw.Band()
    lib.l3_band_around(centre, half, ctypes.byref(out))
    return out


def targets(*bins: float):
    arr = (fw.TargetObs * len(bins))()
    for i, b in enumerate(bins):
        arr[i].rangeBin = b
        arr[i].snr = 100.0 - i  # strongest first, as l3_obs_extract ranks them
    return arr


def test_band_spans_half_width_either_side_of_the_centre(lib):
    b = band(lib, 47.0, 6.0)
    assert (b.valid, b.loBin, b.hiBin) == (1, 41.0, 53.0)


def test_zero_or_negative_half_width_disables_the_band(lib):
    for half in (0.0, -1.0):
        b = band(lib, 47.0, half)
        assert b.valid == 0
        assert lib.l3_band_contains(ctypes.byref(b), 47.0) == 0


def test_edges_are_inside(lib):
    b = band(lib, 47.0, 6.0)
    assert lib.l3_band_contains(ctypes.byref(b), 41.0) == 1
    assert lib.l3_band_contains(ctypes.byref(b), 53.0) == 1
    assert lib.l3_band_contains(ctypes.byref(b), 40.99) == 0
    assert lib.l3_band_contains(ctypes.byref(b), 53.01) == 0


def test_filter_removes_every_in_band_target_and_keeps_order(lib):
    b = band(lib, 47.0, 6.0)
    arr = targets(47.0, 30.0, 41.0, 60.0, 52.9, 35.5)

    kept = lib.l3_band_filter(ctypes.byref(b), arr, 6)

    assert kept == 3
    assert [arr[i].rangeBin for i in range(kept)] == [30.0, 60.0, 35.5]
    assert [arr[i].snr for i in range(kept)] == [99.0, 97.0, 95.0]


def test_filter_with_everything_in_band_keeps_nothing(lib):
    b = band(lib, 47.0, 6.0)
    arr = targets(44.0, 47.0, 50.0)
    assert lib.l3_band_filter(ctypes.byref(b), arr, 3) == 0


def test_disabled_band_filters_nothing(lib):
    b = band(lib, 47.0, 0.0)
    arr = targets(47.0, 48.0)
    assert lib.l3_band_filter(ctypes.byref(b), arr, 2) == 2
