"""Tests for openflight.environment: air density for the flight model."""

from __future__ import annotations

import pytest

from openflight.environment import (
    STANDARD_DENSITY_KG_M3,
    STANDARD_ENVIRONMENT,
    Environment,
    air_density_kg_m3,
    pressure_at_altitude_hpa,
    saturation_vapour_pressure_hpa,
)


def test_standard_conditions_give_the_solver_default():
    assert air_density_kg_m3() == pytest.approx(STANDARD_DENSITY_KG_M3, rel=2e-3)
    assert STANDARD_ENVIRONMENT.air_density_kg_m3 == STANDARD_DENSITY_KG_M3, "exactly, by design"
    assert STANDARD_ENVIRONMENT.density_ratio == 1.0
    assert Environment(15.0, 1013.25, 0.0).air_density_kg_m3 == STANDARD_DENSITY_KG_M3


def test_hot_humid_air_is_thinner_and_cold_dry_air_denser():
    hot_humid = air_density_kg_m3(30.0, 1013.25, 0.8)
    cold_dry = air_density_kg_m3(-5.0, 1013.25, 0.0)
    assert hot_humid < STANDARD_DENSITY_KG_M3 < cold_dry
    assert hot_humid == pytest.approx(1.15, abs=0.01)
    assert cold_dry == pytest.approx(1.316, abs=0.005)
    # Humidity alone lowers density: water vapour is lighter than dry air.
    assert air_density_kg_m3(25.0, 1013.25, 1.0) < air_density_kg_m3(25.0, 1013.25, 0.0)


def test_altitude_supplies_the_pressure_when_there_is_no_barometer():
    assert pressure_at_altitude_hpa(0.0) == pytest.approx(1013.25)
    assert pressure_at_altitude_hpa(1500.0) == pytest.approx(845.6, abs=1.0)
    denver = Environment(temperature_c=15.0, pressure_hpa=None, altitude_m=1609.0)
    assert denver.air_density_kg_m3 == pytest.approx(1.01, abs=0.01)
    assert denver.density_ratio < 0.85
    with pytest.raises(ValueError):
        pressure_at_altitude_hpa(50_000.0)


def test_saturation_pressure_matches_the_reference_points():
    assert saturation_vapour_pressure_hpa(20.0) == pytest.approx(23.4, abs=0.2)
    assert saturation_vapour_pressure_hpa(0.0) == pytest.approx(6.1, abs=0.1)


def test_bad_inputs_are_refused():
    with pytest.raises(ValueError):
        air_density_kg_m3(20.0, 1000.0, 1.5)
    with pytest.raises(ValueError):
        air_density_kg_m3(90.0, 1000.0, 0.0)
    with pytest.raises(ValueError):
        air_density_kg_m3(20.0, 0.0, 0.0)


def test_describe_names_the_conditions_and_the_change_from_standard():
    text = Environment(28.0, 1005.0, 0.6).describe()
    assert text.startswith("28.0 C, 1005 hPa, 60% RH: 1.1")
    assert "% vs standard)" in text and "-" in text.split("(")[-1]
    assert "ISA at 1609 m" in Environment(15.0, None, 0.0, 1609.0).describe()
