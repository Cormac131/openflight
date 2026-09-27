"""Tests for the environment-aware flight solver in openflight.ballistics."""

from __future__ import annotations

import pytest

from openflight.ballistics import LaunchConditions, simulate, solve_flight
from openflight.environment import Environment

DRIVE = LaunchConditions(
    ball_speed_mph=160.0,
    launch_angle_v=12.0,
    launch_angle_h=1.5,
    spin_rpm=2600.0,
    spin_axis_deg=2.0,
    spin_source="club_typical",
)


def test_thin_air_carries_further_and_lands_flatter():
    standard = solve_flight(DRIVE)
    denver = solve_flight(
        DRIVE, Environment(temperature_c=25.0, pressure_hpa=None, altitude_m=1609.0)
    )
    assert denver.air_density < standard.air_density
    assert denver.carry_yards > standard.carry_yards + 8.0
    assert denver.total_yards > standard.total_yards
    assert denver.flight_time_s > 0 and standard.apex_yards > 0
    assert standard.carry_yards == pytest.approx(simulate(DRIVE).carry_yards, rel=1e-3)


def test_curve_is_positive_right_for_a_positive_axis_and_start():
    result = solve_flight(DRIVE)
    assert result.curve_yards > 0
    left = solve_flight(
        LaunchConditions(160.0, 12.0, -1.5, 2600.0, -2.0, "club_typical"),
    )
    assert left.curve_yards < 0


def test_measured_and_estimated_are_kept_apart():
    result = solve_flight(DRIVE)
    assert "ball speed mph" in result.measured and "launch deg" in result.measured
    assert "spin rpm" in result.estimated, "club-typical spin is an estimate"
    for name in ("carry yd", "total yd", "apex yd", "flight s", "curve yd", "landing deg"):
        assert name in result.estimated
    measured_spin = solve_flight(
        LaunchConditions(160.0, 12.0, 1.5, 2600.0, 2.0, "measured"),
        measured_horizontal_launch=False,
    )
    assert "spin rpm" in measured_spin.measured
    assert "direction deg" in measured_spin.estimated
    lines = result.lines()
    assert lines[0] == "MEASURED" and "ESTIMATED" in lines
    assert lines.index("ESTIMATED") > lines.index("MEASURED")
    assert lines[-1].strip().startswith("air density") and "1.22" in lines[-1]
