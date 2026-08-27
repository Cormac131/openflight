"""Tests for altitude-aware carry wiring in the server shot pipeline."""

import argparse
import time
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.air_density import AirConditions
from openflight.ballistics import AIR_DENSITY_STD
from openflight.barometer import BarometerService, PressureSample
from openflight.launch_monitor import ClubType, Shot
from openflight.server import on_shot_detected, shot_to_dict

DENVER = AirConditions.from_elevation(elevation_ft=5280.0, temperature_c=20.0)


@pytest.fixture(name="quiet_pipeline")
def _quiet_pipeline(monkeypatch):
    """Neutralise every side effect of on_shot_detected except carry maths."""
    monkeypatch.setattr(server_module, "kld7_vertical", None)
    monkeypatch.setattr(server_module, "kld7_horizontal", None)
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "camera_tracker", None)
    monkeypatch.setattr(server_module, "camera_enabled", False)
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "debug_mode", False)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module, "barometer_service", None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)
    return monkeypatch


def _shot(**kwargs) -> Shot:
    defaults = dict(
        ball_speed_mph=167.0,
        club_speed_mph=113.0,
        timestamp=datetime.now(),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.5,
        spin_rpm=2600.0,
        spin_confidence=0.95,
    )
    defaults.update(kwargs)
    return Shot(**defaults)


class TestAirDiffersFromNormalization:
    def test_standard_conditions_do_not_differ(self, monkeypatch):
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        assert (
            server_module._air_differs_from_normalization(AirConditions.standard()) is False
        )

    def test_denver_differs(self, monkeypatch):
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        assert server_module._air_differs_from_normalization(DENVER) is True

    def test_sub_threshold_difference_is_ignored(self, monkeypatch):
        # 0.05% — far below anything visible in a rounded yardage.
        nudged = AIR_DENSITY_STD * 1.0005
        conditions = AirConditions.from_sensor(
            pressure_pa=nudged * 287.0528 * 288.15, temperature_c=15.0
        )
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        assert server_module._air_differs_from_normalization(conditions) is False


class TestBallisticCarryPath:
    def test_standard_conditions_emit_no_actual_carry(self, quiet_pipeline):
        quiet_pipeline.setattr(server_module, "air_conditions", AirConditions.standard())
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)

        shot = _shot()
        on_shot_detected(shot)

        assert shot.carry_spin_adjusted is not None
        assert shot.carry_actual_yards is None
        assert shot.air_density_kg_m3 is None
        assert shot.air_conditions_source is None

    def test_denver_adds_an_actual_carry_without_moving_the_headline(self, quiet_pipeline):
        quiet_pipeline.setattr(server_module, "air_conditions", AirConditions.standard())
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)
        baseline = _shot()
        on_shot_detected(baseline)

        quiet_pipeline.setattr(server_module, "air_conditions", DENVER)
        altitude_shot = _shot()
        on_shot_detected(altitude_shot)

        # The normalized carry is the stable, comparable number: unchanged.
        assert altitude_shot.carry_spin_adjusted == pytest.approx(
            baseline.carry_spin_adjusted
        )
        # The actual-conditions carry reflects thin Denver air.
        assert altitude_shot.carry_actual_yards == pytest.approx(
            baseline.carry_spin_adjusted + 14.0, abs=1.5
        )
        assert altitude_shot.air_density_kg_m3 == pytest.approx(DENVER.density_kg_m3)
        assert altitude_shot.air_conditions_source == "config"

    def test_cold_dense_air_shortens_the_actual_carry(self, quiet_pipeline):
        winter = AirConditions.from_elevation(elevation_ft=0.0, temperature_c=0.0)
        quiet_pipeline.setattr(server_module, "air_conditions", winter)
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)

        shot = _shot()
        on_shot_detected(shot)
        assert shot.carry_actual_yards < shot.carry_spin_adjusted

    def test_normalization_density_moves_the_headline_carry(self, quiet_pipeline):
        quiet_pipeline.setattr(server_module, "air_conditions", AirConditions.standard())
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)

        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        isa_shot = _shot()
        on_shot_detected(isa_shot)

        # TrackMan "Flat" normalization is warmer, hence thinner, hence longer.
        quiet_pipeline.setattr(server_module, "carry_normalization_density", 1.184)
        trackman_shot = _shot()
        on_shot_detected(trackman_shot)

        assert trackman_shot.carry_spin_adjusted > isa_shot.carry_spin_adjusted


class TestTableCarryPath:
    def test_table_fallback_also_gets_an_actual_carry(self, quiet_pipeline):
        quiet_pipeline.setattr(server_module, "air_conditions", DENVER)
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", False)

        shot = _shot()
        on_shot_detected(shot)

        assert shot.carry_spin_adjusted is not None
        assert shot.carry_actual_yards is not None
        assert shot.carry_actual_yards > shot.carry_spin_adjusted
        assert shot.air_conditions_source == "config"

    def test_both_carry_paths_agree_on_the_density_effect(self, quiet_pipeline):
        """
        The whole point of routing the table path through density_carry_ratio:
        a Denver user must not see a 14 yd altitude gain with ballistics on and
        nothing at all with it off.
        """
        quiet_pipeline.setattr(server_module, "air_conditions", DENVER)
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)

        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)
        ballistic = _shot()
        on_shot_detected(ballistic)

        quiet_pipeline.setattr(server_module, "ballistics_enabled", False)
        tabular = _shot()
        on_shot_detected(tabular)

        ballistic_gain = ballistic.carry_actual_yards / ballistic.carry_spin_adjusted
        table_gain = tabular.carry_actual_yards / tabular.carry_spin_adjusted
        assert ballistic_gain == pytest.approx(table_gain, abs=0.01)

    def test_missing_launch_angle_still_gets_an_actual_carry(self, quiet_pipeline):
        quiet_pipeline.setattr(server_module, "air_conditions", DENVER)
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)

        shot = _shot(launch_angle_vertical=None)
        on_shot_detected(shot)
        assert shot.carry_actual_yards is not None


class TestShotPayload:
    def test_payload_carries_the_new_fields(self):
        shot = _shot(
            carry_spin_adjusted=260.5,
            carry_actual_yards=274.4,
            air_density_kg_m3=0.9912,
            air_conditions_source="config",
        )
        payload = shot_to_dict(shot)
        assert payload["carry_spin_adjusted"] == 260
        assert payload["carry_actual_yards"] == 274
        assert payload["air_density_kg_m3"] == pytest.approx(0.9912)
        assert payload["air_conditions_source"] == "config"

    def test_payload_defaults_to_null_under_standard_conditions(self):
        payload = shot_to_dict(_shot(carry_spin_adjusted=260.5))
        assert payload["carry_actual_yards"] is None
        assert payload["air_density_kg_m3"] is None
        assert payload["air_conditions_source"] is None


class TestResolveAirConditions:
    @staticmethod
    def _args(**kwargs):
        defaults = dict(
            elevation_ft=None,
            air_temp_c=None,
            sea_level_pressure_hpa=None,
            relative_humidity_pct=None,
            carry_normalization_density=None,
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_no_arguments_reproduces_previous_behaviour(self):
        conditions, normalization = server_module._resolve_air_conditions(self._args())
        assert conditions.source == "standard"
        assert normalization == AIR_DENSITY_STD
        assert conditions.density_kg_m3 == pytest.approx(AIR_DENSITY_STD)

    def test_elevation_alone_is_enough(self):
        conditions, _ = server_module._resolve_air_conditions(
            self._args(elevation_ft=5280.0)
        )
        assert conditions.source == "config"
        assert conditions.elevation_ft == pytest.approx(5280.0, abs=0.1)

    def test_temperature_alone_is_enough(self):
        # Sea-level default elevation, but a real temperature still matters.
        conditions, _ = server_module._resolve_air_conditions(self._args(air_temp_c=30.0))
        assert conditions.source == "config"
        assert conditions.density_kg_m3 < AIR_DENSITY_STD

    def test_normalization_density_is_passed_through(self):
        _, normalization = server_module._resolve_air_conditions(
            self._args(carry_normalization_density=1.184)
        )
        assert normalization == 1.184

    @pytest.mark.parametrize("value", [0.2, 3.0])
    def test_rejects_implausible_normalization_density(self, value):
        with pytest.raises(SystemExit, match="carry-normalization-density"):
            server_module._resolve_air_conditions(
                self._args(carry_normalization_density=value)
            )

    def test_reports_bad_elevation_readably(self):
        with pytest.raises(SystemExit, match="Invalid air conditions"):
            server_module._resolve_air_conditions(self._args(elevation_ft=30000.0))

    def test_reports_transposed_pressure_units_readably(self):
        # 101325 entered where hPa was expected.
        with pytest.raises(SystemExit, match="Invalid air conditions"):
            server_module._resolve_air_conditions(
                self._args(elevation_ft=0.0, sea_level_pressure_hpa=101325.0)
            )


class _StubBarometerSensor:
    """Barometer stub returning one fixed pressure/temperature."""

    def __init__(self, pressure_pa, temperature_c):
        self.pressure_pa = pressure_pa
        self.temperature_c = temperature_c

    def initialize(self):
        """No hardware to configure."""

    def read(self, *, timestamp=None):
        return PressureSample(
            timestamp=time.time() if timestamp is None else timestamp,
            pressure_pa=self.pressure_pa,
            temperature_c=self.temperature_c,
        )

    def close(self):
        """No hardware to release."""


def _loaded_barometer(pressure_pa, temperature_c, **kwargs):
    """A service already holding one usable reading, without starting a thread."""
    service = BarometerService(
        _StubBarometerSensor(pressure_pa, temperature_c), window_samples=1, **kwargs
    )
    service.add_sample(service.sensor.read())
    return service


class TestSensorPreferredOverConfig:
    def test_sensor_reading_overrides_configured_conditions(self, monkeypatch):
        monkeypatch.setattr(server_module, "air_conditions", DENVER)
        # Denver elevation but a live 98000 Pa reading: the measurement wins.
        monkeypatch.setattr(
            server_module, "barometer_service", _loaded_barometer(98000.0, 15.0)
        )

        conditions = server_module.current_air_conditions()
        assert conditions.source == "sensor"
        assert conditions.pressure_pa == pytest.approx(98000.0)

    def test_falls_back_to_config_when_no_sensor_is_fitted(self, monkeypatch):
        monkeypatch.setattr(server_module, "air_conditions", DENVER)
        monkeypatch.setattr(server_module, "barometer_service", None)

        assert server_module.current_air_conditions() is DENVER

    def test_stale_reading_is_remembered_rather_than_discarded(self, monkeypatch):
        stale = _loaded_barometer(98000.0, 15.0, max_reading_age_s=0.001)
        time.sleep(0.01)
        monkeypatch.setattr(server_module, "air_conditions", DENVER)
        monkeypatch.setattr(server_module, "barometer_service", stale)

        conditions = server_module.current_air_conditions()
        assert conditions.source == "sensor_stale"
        assert conditions.pressure_pa == pytest.approx(98000.0)

    def test_falls_back_to_config_when_the_sensor_never_read(self, monkeypatch):
        empty = BarometerService(_StubBarometerSensor(98000.0, 15.0), window_samples=3)
        monkeypatch.setattr(server_module, "air_conditions", DENVER)
        monkeypatch.setattr(server_module, "barometer_service", empty)

        assert server_module.current_air_conditions() is DENVER

    def test_shot_carries_sensor_provenance(self, quiet_pipeline):
        quiet_pipeline.setattr(server_module, "air_conditions", AirConditions.standard())
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)
        quiet_pipeline.setattr(
            server_module, "barometer_service", _loaded_barometer(83400.0, 20.0)
        )

        shot = _shot()
        on_shot_detected(shot)

        assert shot.air_conditions_source == "sensor"
        assert shot.carry_actual_yards > shot.carry_spin_adjusted

    def test_temperature_offset_changes_the_resulting_carry(self, quiet_pipeline):
        quiet_pipeline.setattr(server_module, "air_conditions", AirConditions.standard())
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)

        quiet_pipeline.setattr(
            server_module, "barometer_service", _loaded_barometer(101325.0, 25.0)
        )
        uncorrected = _shot()
        on_shot_detected(uncorrected)

        # A die reading 5 C warm makes the air look thinner than it is.
        quiet_pipeline.setattr(
            server_module,
            "barometer_service",
            _loaded_barometer(101325.0, 25.0, temperature_offset_c=-5.0),
        )
        corrected = _shot()
        on_shot_detected(corrected)

        assert corrected.carry_actual_yards < uncorrected.carry_actual_yards


class TestBarometerAloneNeedsNoConfiguration:
    """
    A fitted sensor is a complete configuration on its own.

    These pin the behaviour the flags exist to make optional: plug the sensor
    in, set nothing, and an altitude rig still scores correctly — including
    after the sensor stops responding.
    """

    def test_unconfigured_rig_scores_correctly_from_the_sensor_alone(self, quiet_pipeline):
        # Nothing configured: air_conditions is bare standard sea level.
        quiet_pipeline.setattr(server_module, "air_conditions", AirConditions.standard())
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)
        quiet_pipeline.setattr(
            server_module, "barometer_service", _loaded_barometer(83400.0, 20.0)
        )

        shot = _shot()
        on_shot_detected(shot)

        assert shot.air_conditions_source == "sensor"
        assert shot.carry_actual_yards == pytest.approx(
            shot.carry_spin_adjusted + 14.0, abs=1.5
        )

    def test_a_dead_sensor_does_not_drop_an_unconfigured_rig_to_sea_level(
        self, quiet_pipeline
    ):
        """The 14 yd cliff this tier exists to prevent."""
        dead = _loaded_barometer(83400.0, 20.0, max_reading_age_s=0.001)
        time.sleep(0.01)
        quiet_pipeline.setattr(server_module, "air_conditions", AirConditions.standard())
        quiet_pipeline.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        quiet_pipeline.setattr(server_module, "ballistics_enabled", True)
        quiet_pipeline.setattr(server_module, "barometer_service", dead)

        shot = _shot()
        on_shot_detected(shot)

        assert shot.air_conditions_source == "sensor_stale"
        assert shot.carry_actual_yards == pytest.approx(
            shot.carry_spin_adjusted + 14.0, abs=1.5
        )

    def test_sensor_that_never_read_falls_through_to_configuration(self, monkeypatch):
        # Only genuine no-data falls through; a wired-but-silent sensor on an
        # unconfigured rig has nothing better to offer than standard air.
        never_read = BarometerService(_StubBarometerSensor(83400.0, 20.0), window_samples=3)
        monkeypatch.setattr(server_module, "air_conditions", DENVER)
        monkeypatch.setattr(server_module, "barometer_service", never_read)

        assert server_module.current_air_conditions() is DENVER


class TestAirStatusPayload:
    """The diagnostics Air tab's data contract."""

    def test_reports_assumed_conditions_when_nothing_is_fitted(self, monkeypatch):
        monkeypatch.setattr(server_module, "air_conditions", AirConditions.standard())
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        monkeypatch.setattr(server_module, "barometer_service", None)
        monkeypatch.setattr(server_module, "barometer_runtime_config", {"enabled": False})

        payload = server_module._air_status_payload()
        assert payload["source"] == "standard"
        assert payload["density_delta_pct"] == 0.0
        assert payload["driver_carry_delta_yards"] == 0.0
        assert payload["sensor"]["enabled"] is False
        # No sensor means no reading block to render.
        assert "reading" not in payload

    def test_expresses_the_density_difference_as_driver_carry(self, monkeypatch):
        monkeypatch.setattr(server_module, "air_conditions", DENVER)
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        monkeypatch.setattr(server_module, "barometer_service", None)

        payload = server_module._air_status_payload()
        assert payload["source"] == "config"
        assert payload["density_delta_pct"] < 0
        # The same +14 yd that shows up everywhere else for Denver.
        assert payload["driver_carry_delta_yards"] == pytest.approx(14.0, abs=1.5)

    def test_includes_the_sensor_reading_when_one_exists(self, monkeypatch):
        monkeypatch.setattr(server_module, "air_conditions", AirConditions.standard())
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        monkeypatch.setattr(
            server_module, "barometer_service", _loaded_barometer(83400.0, 20.0)
        )

        payload = server_module._air_status_payload()
        assert payload["source"] == "sensor"
        assert payload["reading"]["status"] == "ok"
        assert payload["reading"]["pressure_hpa"] == pytest.approx(834.0, abs=0.1)

    def test_exposes_raw_and_corrected_temperature(self, monkeypatch):
        """Self-heating has to stay visible rather than hidden by the offset."""
        monkeypatch.setattr(server_module, "air_conditions", AirConditions.standard())
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        monkeypatch.setattr(
            server_module,
            "barometer_service",
            _loaded_barometer(101325.0, 26.2, temperature_offset_c=-4.7),
        )

        reading = server_module._air_status_payload()["reading"]
        assert reading["raw_temperature_c"] == pytest.approx(26.2, abs=0.01)
        assert reading["temperature_c"] == pytest.approx(21.5, abs=0.01)

    def test_carry_effect_is_cached_across_repeated_payloads(self, monkeypatch):
        """
        The effect runs two RK4 integrations, far too slow to repeat for every
        barometer sample on a Pi. Densities are rounded before lookup, so a
        steady sensor computes it once.
        """
        monkeypatch.setattr(server_module, "air_conditions", DENVER)
        monkeypatch.setattr(server_module, "carry_normalization_density", AIR_DENSITY_STD)
        monkeypatch.setattr(server_module, "barometer_service", None)

        server_module._carry_effect_yards.cache_clear()
        for _ in range(5):
            server_module._air_status_payload()
        info = server_module._carry_effect_yards.cache_info()
        assert info.misses == 1
        assert info.hits == 4


class TestBarometerReadingObserver:
    def test_each_accepted_reading_is_published(self):
        published = []
        service = BarometerService(
            _StubBarometerSensor(101325.0, 20.0),
            window_samples=1,
            on_reading=published.append,
        )
        service.add_sample(service.sensor.read())
        service.add_sample(service.sensor.read())
        assert len(published) == 2

    def test_rejected_readings_are_not_published(self):
        published = []
        service = BarometerService(
            _StubBarometerSensor(0.0, 20.0), window_samples=1, on_reading=published.append
        )
        service.add_sample(service.sensor.read())
        assert published == []

    def test_a_throwing_observer_does_not_break_sampling(self):
        """A broken debug display must never stop the rig measuring air."""

        def explode(_snapshot):
            raise RuntimeError("socket gone")

        service = BarometerService(
            _StubBarometerSensor(101325.0, 20.0), window_samples=1, on_reading=explode
        )
        assert service.add_sample(service.sensor.read()) is not None
        assert service.current_conditions() is not None
