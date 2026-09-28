"""Tests for firmware/iwr6843/l3_launch.c: the launch from a fitted delivery."""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def test_launch_walks_the_fit_back_to_impact(lib):
    fit = fw.Delivery()
    fit.points = 5
    fit.velocity = fw.Vec3(40.0, 3.0, 10.0)
    fit.position = fw.Vec3(2.0, 0.1, 0.5)
    fit.timestampUs = 10_000
    fit.speedMps = 41.34
    fit.radialSpeedMps = 40.5
    fit.residualM = 0.01
    fit.confidence = 0.8
    fit.speedValid = 1
    fit.pathRad = 0.075
    fit.pathValid = 1
    fit.attackRad = 0.245
    fit.attackValid = 1
    out = fw.Launch()
    lib.l3_launch_from_delivery(ctypes.byref(fit), 4_000, ctypes.byref(out))
    assert out.points == 5
    assert out.launchPosition.x == pytest.approx(2.0 - 40.0 * 0.006)
    assert out.launchPosition.z == pytest.approx(0.5 - 10.0 * 0.006)
    assert (out.hlaValid, out.vlaValid, out.speedValid) == (1, 1, 1)
    assert out.hlaRad == pytest.approx(0.075)
    assert out.vlaRad == pytest.approx(0.245)


def test_launch_without_angles_leaves_directions_invalid(lib):
    fit = fw.Delivery()
    fit.points = 4
    fit.speedValid = 1
    out = fw.Launch()
    lib.l3_launch_from_delivery(ctypes.byref(fit), 0, ctypes.byref(out))
    assert (out.hlaValid, out.vlaValid) == (0, 0)
