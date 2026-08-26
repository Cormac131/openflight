"""Tests for the atmospheric model behind altitude-aware carry."""

import math

import pytest

from openflight.air_density import (
    MAX_PRESSURE_PA,
    MIN_PRESSURE_PA,
    PA_PER_HPA,
    STANDARD_AIR_DENSITY,
    STANDARD_PRESSURE_PA,
    STANDARD_TEMPERATURE_C,
    AirConditions,
    AirConditionsError,
    air_density,
    pressure_altitude_m,
    saturation_vapour_pressure_pa,
    station_pressure_pa,
    temperature_at_elevation_c,
)


class TestAirDensity:
    def test_isa_sea_level_is_the_textbook_value(self):
        assert air_density(STANDARD_PRESSURE_PA, STANDARD_TEMPERATURE_C) == pytest.approx(
            1.225, abs=0.001
        )

    def test_standard_constant_matches_the_formula(self):
        # The constant is derived, not written down; this pins it to 1.225 so a
        # change to the gas constant cannot silently move every carry.
        assert STANDARD_AIR_DENSITY == pytest.approx(1.225, abs=0.001)

    def test_density_falls_as_temperature_rises(self):
        cold = air_density(STANDARD_PRESSURE_PA, 0.0)
        warm = air_density(STANDARD_PRESSURE_PA, 35.0)
        assert cold > warm
        # Roughly 11% across that span — the seasonal swing worth ~9 yd.
        assert (cold - warm) / cold == pytest.approx(0.114, abs=0.01)

    def test_density_falls_as_pressure_falls(self):
        assert air_density(90000.0, 15.0) < air_density(101325.0, 15.0)

    def test_humid_air_is_less_dense_than_dry_air(self):
        # Counter-intuitive but correct: water vapour is lighter than the
        # nitrogen and oxygen it displaces.
        dry = air_density(STANDARD_PRESSURE_PA, 30.0, relative_humidity=0.0)
        humid = air_density(STANDARD_PRESSURE_PA, 30.0, relative_humidity=1.0)
        assert humid < dry

    @pytest.mark.parametrize(
        "temperature_c, expected_reduction",
        [(20.0, 0.0087), (30.0, 0.0158), (35.0, 0.0210)],
    )
    def test_humidity_effect_is_small_and_temperature_dependent(
        self, temperature_c, expected_reduction
    ):
        # Pins the magnitude so the "safe to omit" guidance stays honest: the
        # worst realistic case is ~2% density, about 1.4 yd of driver carry.
        dry = air_density(STANDARD_PRESSURE_PA, temperature_c, relative_humidity=0.0)
        humid = air_density(STANDARD_PRESSURE_PA, temperature_c, relative_humidity=1.0)
        assert (dry - humid) / dry == pytest.approx(expected_reduction, abs=0.001)

    def test_zero_humidity_matches_the_dry_path(self):
        assert air_density(
            STANDARD_PRESSURE_PA, 20.0, relative_humidity=0.0
        ) == pytest.approx(air_density(STANDARD_PRESSURE_PA, 20.0), rel=1e-12)

    @pytest.mark.parametrize(
        "pressure, temperature",
        [
            (1013.25, 15.0),  # hPa mistakenly passed as Pa
            (MIN_PRESSURE_PA - 1.0, 15.0),
            (MAX_PRESSURE_PA + 1.0, 15.0),
            (STANDARD_PRESSURE_PA, -273.15),
            (STANDARD_PRESSURE_PA, 100.0),
            (float("nan"), 15.0),
            (float("inf"), 15.0),
            (STANDARD_PRESSURE_PA, float("nan")),
        ],
    )
    def test_rejects_implausible_inputs(self, pressure, temperature):
        with pytest.raises(AirConditionsError):
            air_density(pressure, temperature)

    @pytest.mark.parametrize("humidity", [-0.01, 1.01, float("nan")])
    def test_rejects_humidity_outside_unit_interval(self, humidity):
        with pytest.raises(AirConditionsError):
            air_density(STANDARD_PRESSURE_PA, 15.0, relative_humidity=humidity)

    def test_error_message_names_the_offending_quantity(self):
        with pytest.raises(AirConditionsError, match="pressure"):
            air_density(10.0, 15.0)


class TestSaturationVapourPressure:
    @pytest.mark.parametrize(
        "temperature_c, expected_pa",
        [(0.0, 611.2), (20.0, 2339.0), (30.0, 4245.0)],
    )
    def test_matches_published_values(self, temperature_c, expected_pa):
        assert saturation_vapour_pressure_pa(temperature_c) == pytest.approx(
            expected_pa, rel=0.01
        )

    def test_increases_monotonically(self):
        values = [saturation_vapour_pressure_pa(t) for t in range(-20, 50, 5)]
        assert values == sorted(values)


class TestStationPressure:
    def test_sea_level_returns_the_reference_pressure(self):
        assert station_pressure_pa(0.0) == pytest.approx(STANDARD_PRESSURE_PA)

    def test_denver_matches_published_station_pressure(self):
        # 5280 ft ≈ 1609 m; standard station pressure there is ~83.4 kPa.
        assert station_pressure_pa(1609.0) == pytest.approx(83400.0, rel=0.01)

    def test_pressure_falls_with_elevation(self):
        pressures = [station_pressure_pa(e) for e in (0, 500, 1000, 2000)]
        assert pressures == sorted(pressures, reverse=True)

    def test_sea_level_pressure_scales_the_result(self):
        low = station_pressure_pa(500.0, sea_level_pressure_pa=98000.0)
        high = station_pressure_pa(500.0, sea_level_pressure_pa=103500.0)
        assert low < high

    def test_below_sea_level_is_supported(self):
        assert station_pressure_pa(-100.0) > STANDARD_PRESSURE_PA

    @pytest.mark.parametrize("elevation", [-1000.0, 7000.0, float("nan")])
    def test_rejects_implausible_elevation(self, elevation):
        with pytest.raises(AirConditionsError):
            station_pressure_pa(elevation)


class TestTemperatureAtElevation:
    def test_sea_level_is_the_reference_temperature(self):
        assert temperature_at_elevation_c(0.0) == pytest.approx(STANDARD_TEMPERATURE_C)

    def test_cools_with_the_standard_lapse_rate(self):
        # 6.5 K per km.
        assert temperature_at_elevation_c(1000.0) == pytest.approx(8.5, abs=0.01)


class TestPressureAltitude:
    def test_round_trips_with_station_pressure(self):
        for elevation in (0.0, 300.0, 1609.0, 3000.0):
            assert pressure_altitude_m(station_pressure_pa(elevation)) == pytest.approx(
                elevation, abs=0.5
            )

    def test_reported_altitude_moves_with_weather_at_a_fixed_site(self):
        # The documented trap: a stationary sensor's derived altitude wanders
        # by well over 1500 ft as pressure systems pass, which is exactly why
        # the density path never routes through this function.
        low = pressure_altitude_m(980.0 * PA_PER_HPA)
        high = pressure_altitude_m(1035.0 * PA_PER_HPA)
        assert low > high
        spread_ft = (low - high) * 3.280839895
        assert spread_ft > 1500.0

    def test_density_is_unaffected_by_the_same_weather_swing(self):
        # Same two pressures, straight to density: both exactly right.
        for hpa in (980.0, 1035.0):
            assert air_density(hpa * PA_PER_HPA, 15.0) == pytest.approx(
                hpa * PA_PER_HPA / (287.0528 * 288.15), rel=1e-9
            )


class TestAirConditions:
    def test_standard_reproduces_isa(self):
        conditions = AirConditions.standard()
        assert conditions.density_kg_m3 == pytest.approx(1.225, abs=0.001)
        assert conditions.source == "standard"
        assert conditions.elevation_ft is None

    def test_from_elevation_at_denver(self):
        conditions = AirConditions.from_elevation(elevation_ft=5280.0, temperature_c=20.0)
        assert conditions.density_kg_m3 == pytest.approx(0.991, abs=0.005)
        assert conditions.source == "config"
        assert conditions.elevation_ft == pytest.approx(5280.0, abs=0.1)

    def test_from_elevation_without_temperature_uses_the_lapse_rate(self):
        conditions = AirConditions.from_elevation(elevation_ft=5280.0)
        assert conditions.temperature_c == pytest.approx(4.5, abs=0.5)

    def test_explicit_temperature_wins_over_the_lapse_rate(self):
        lapsed = AirConditions.from_elevation(elevation_ft=5280.0)
        explicit = AirConditions.from_elevation(elevation_ft=5280.0, temperature_c=25.0)
        assert explicit.temperature_c == 25.0
        assert explicit.density_kg_m3 < lapsed.density_kg_m3

    def test_from_elevation_accepts_a_local_sea_level_pressure(self):
        standard = AirConditions.from_elevation(elevation_ft=0.0, temperature_c=15.0)
        stormy = AirConditions.from_elevation(
            elevation_ft=0.0, temperature_c=15.0, sea_level_pressure_hpa=980.0
        )
        assert stormy.density_kg_m3 < standard.density_kg_m3

    def test_from_elevation_converts_humidity_percent_to_fraction(self):
        conditions = AirConditions.from_elevation(
            elevation_ft=0.0, temperature_c=25.0, relative_humidity_pct=50.0
        )
        assert conditions.relative_humidity == pytest.approx(0.5)

    def test_sea_level_config_reproduces_standard_density(self):
        conditions = AirConditions.from_elevation(elevation_ft=0.0, temperature_c=15.0)
        assert conditions.density_kg_m3 == pytest.approx(STANDARD_AIR_DENSITY, abs=0.001)

    def test_from_sensor_uses_raw_pressure_not_derived_altitude(self):
        # A sensor at sea level during a deep low reports ~980 hPa. The right
        # density comes straight from that pressure; the elevation it is told
        # about is metadata and must not change the answer.
        with_meta = AirConditions.from_sensor(
            pressure_pa=98000.0, temperature_c=15.0, elevation_m=0.0
        )
        without_meta = AirConditions.from_sensor(pressure_pa=98000.0, temperature_c=15.0)
        assert with_meta.density_kg_m3 == pytest.approx(without_meta.density_kg_m3)
        assert with_meta.source == "sensor"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"elevation_ft": 30000.0},
            {"elevation_ft": -3000.0},
            {"elevation_ft": 0.0, "temperature_c": -100.0},
            {"elevation_ft": 0.0, "sea_level_pressure_hpa": 101325.0},
            {"elevation_ft": 0.0, "relative_humidity_pct": 150.0},
        ],
    )
    def test_from_elevation_rejects_implausible_configuration(self, kwargs):
        with pytest.raises(AirConditionsError):
            AirConditions.from_elevation(**kwargs)

    def test_construction_validates_eagerly(self):
        # A bad config should fail at startup, not on the first shot.
        with pytest.raises(AirConditionsError):
            AirConditions(pressure_pa=10.0, temperature_c=15.0)

    def test_is_immutable(self):
        conditions = AirConditions.standard()
        with pytest.raises(Exception):
            conditions.pressure_pa = 90000.0  # type: ignore[misc]

    def test_to_dict_is_json_serialisable_and_labelled(self):
        payload = AirConditions.from_elevation(
            elevation_ft=1000.0, temperature_c=18.0, relative_humidity_pct=40.0
        ).to_dict()
        assert payload["source"] == "config"
        assert payload["elevation_ft"] == pytest.approx(1000.0, abs=0.1)
        assert payload["relative_humidity_pct"] == pytest.approx(40.0)
        assert math.isfinite(payload["density_kg_m3"])

    def test_to_dict_reports_none_humidity_when_unset(self):
        payload = AirConditions.standard().to_dict()
        assert payload["relative_humidity_pct"] is None
        assert payload["elevation_ft"] is None
