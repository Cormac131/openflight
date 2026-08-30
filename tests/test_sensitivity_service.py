"""Sensitivity service: readback, clamping, EEPROM commits, failure reporting."""

import pytest

from openflight.sensitivity import (
    DS3502_SERIES_OHMS as DEFAULT_SERIES_OHMS,
    MAX_POSITION,
    MockDS3502,
    SoundSensitivityService,
    clamp_position,
    disabled_state,
)


class SpyPot(MockDS3502):
    """A mock pot that records the calls the service makes."""

    def __init__(self, *, fail_on_set=None, fail_on_read=False, **kwargs):
        super().__init__(**kwargs)
        self.calls = []
        self.fail_on_set = fail_on_set
        self.fail_on_read = fail_on_read

    def open(self):
        self.calls.append(("open",))
        super().open()

    @property
    def position(self):
        if self.fail_on_read:
            raise OSError("i2c read failed")
        return super().position

    def set_position(self, position, *, store=False):
        self.calls.append(("set_position", position, store))
        if self.fail_on_set is not None and position == self.fail_on_set:
            raise OSError("i2c write failed")
        return super().set_position(position, store=store)

    def close(self):
        self.calls.append(("close",))
        super().close()


def build_service(**kwargs):
    pot = SpyPot(**kwargs)
    return SoundSensitivityService(pot), pot


class TestClamping:
    @pytest.mark.parametrize(
        "value,expected",
        [(-40, 0), (0, 0), (64, 64), (127, 127), (250, 127), (64.0, 64), ("64", 64)],
    )
    def test_values_are_coerced_into_range(self, value, expected):
        assert clamp_position(value) == expected

    def test_bools_are_rejected_rather_than_read_as_zero_or_one(self):
        with pytest.raises(TypeError):
            clamp_position(True)

    @pytest.mark.parametrize("value", ["loud", None, [1]])
    def test_unusable_values_raise(self, value):
        with pytest.raises((TypeError, ValueError)):
            clamp_position(value)


class TestStart:
    def test_start_opens_and_reports_the_chips_own_position(self):
        service, pot = build_service()

        state = service.start()

        assert pot.calls == [("open",)]
        assert state.position == MAX_POSITION // 2
        assert state.enabled is True

    def test_start_writes_nothing_by_default(self):
        # The DS3502 restores its own wiper from EEPROM, so re-applying a
        # remembered value would be busywork -- and an extra write.
        service, pot = build_service()

        service.start()

        assert not [call for call in pot.calls if call[0] == "set_position"]

    def test_a_forced_position_is_applied(self):
        service, _ = build_service()

        assert service.start(force_position=88).position == 88

    def test_a_forced_position_is_clamped(self):
        service, _ = build_service()

        assert service.start(force_position=4000).position == MAX_POSITION

    def test_a_forced_position_is_not_committed_to_eeprom(self):
        # --sound-sensitivity-position steers one run; it must not overwrite
        # what the user deliberately stored on the chip.
        service, pot = build_service()

        service.start(force_position=20)

        assert [call for call in pot.calls if call[0] == "set_position"] == [
            ("set_position", 20, False)
        ]


class TestSetPosition:
    def test_setting_a_position_commits_to_eeprom_by_default(self):
        service, pot = build_service()
        service.start()

        service.set_position(80)

        assert ("set_position", 80, True) in pot.calls

    def test_a_stored_position_survives_a_power_cycle(self):
        service, pot = build_service()
        service.start()
        service.set_position(80)

        pot.close()
        pot.open()

        assert pot.position == 80

    def test_state_reports_percent_series_and_both_resistances(self):
        service, _ = build_service()
        service.start()

        state = service.set_position(MAX_POSITION)

        assert state.sensitivity_percent == pytest.approx(100.0)
        assert state.resistance_ohms == pytest.approx(43_000, rel=1e-3)
        assert state.preamp_feedback_ohms == pytest.approx(30_070, rel=1e-3)
        assert state.series_ohms == DEFAULT_SERIES_OHMS

    def test_an_over_range_request_saturates_instead_of_failing(self):
        service, _ = build_service()
        service.start()

        assert service.set_position(4000).position == MAX_POSITION

    def test_a_hardware_failure_propagates_and_is_recorded(self):
        service, _ = build_service(fail_on_set=90)
        service.start()

        with pytest.raises(OSError):
            service.set_position(90)

        assert service.state().error == "i2c write failed"

    def test_a_later_success_clears_the_recorded_error(self):
        service, _ = build_service(fail_on_set=90)
        service.start()
        with pytest.raises(OSError):
            service.set_position(90)

        assert service.set_position(20).error is None

    def test_a_custom_series_resistor_shifts_every_reported_figure(self):
        pot = SpyPot(series_ohms=39_000.0)
        service = SoundSensitivityService(pot)
        service.start()

        state = service.set_position(0)

        assert state.resistance_ohms == pytest.approx(39_000)
        assert state.series_ohms == 39_000.0

    def test_the_payload_rounds_for_the_ui(self):
        service, _ = build_service()
        service.start()

        payload = service.set_position(33).to_dict()

        assert payload["position"] == 33
        assert payload["max_position"] == MAX_POSITION
        assert isinstance(payload["resistance_ohms"], int)
        assert isinstance(payload["series_ohms"], int)
        assert payload["enabled"] is True


class TestReadFailures:
    def test_a_bus_error_while_reading_does_not_take_the_page_down(self):
        service, pot = build_service()
        service.start()
        pot.fail_on_read = True

        state = service.state()

        assert state.enabled is True
        assert state.position is None
        assert state.error == "i2c read failed"

    def test_derived_figures_are_absent_when_the_position_is_unknown(self):
        service, pot = build_service()
        service.start()
        pot.fail_on_read = True

        state = service.state()

        assert state.resistance_ohms is None
        assert state.preamp_feedback_ohms is None
        assert state.sensitivity_percent is None


class TestLifecycle:
    def test_stop_closes_the_pot(self):
        service, pot = build_service()
        service.start()

        service.stop()

        assert ("close",) in pot.calls

    def test_stop_swallows_a_failing_close(self):
        service, pot = build_service()
        service.start()
        pot.close = lambda: (_ for _ in ()).throw(OSError("gone"))

        service.stop()

    def test_simulated_services_say_so(self):
        service = SoundSensitivityService(MockDS3502(), simulated=True)

        assert service.start().to_dict()["simulated"] is True


class TestLiveEnvelope:
    def test_state_includes_the_latest_envelope_sample(self):
        from openflight.sensitivity import EnvelopeMonitor, MockADS1115

        monitor = EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3)
        monitor.add_sample(1.65, timestamp=1.0)
        service = SoundSensitivityService(SpyPot(), envelope=monitor)
        # Open the pot without starting the envelope thread, which would
        # overwrite the scripted sample with the mock ADC's idle 0 V.
        service.pot.open()

        payload = service.state().to_dict()

        assert payload["live_envelope"]["fraction_of_full_scale"] == pytest.approx(0.5)
        assert payload["live_envelope"]["volts"] == pytest.approx(1.65)

    def test_state_includes_the_auto_gain_target_band(self):
        from openflight.sensitivity import AutoGainController, EnvelopeMonitor, MockADS1115

        service = SoundSensitivityService(
            SpyPot(),
            envelope=EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3),
            controller=AutoGainController(target_low=0.55, target_high=0.75),
        )
        service.start()

        payload = service.state().to_dict()

        assert payload["target_low"] == pytest.approx(0.55)
        assert payload["target_high"] == pytest.approx(0.75)

    def test_a_pot_without_an_adc_has_no_live_envelope(self):
        service, _ = build_service()
        service.start()

        payload = service.state().to_dict()

        assert payload["live_envelope"] is None
        assert payload["target_low"] is None
        assert payload["target_high"] is None


class TestDisabledState:
    def test_disabled_state_has_no_position_but_keeps_the_ui_bounds(self):
        payload = disabled_state().to_dict()

        assert payload["enabled"] is False
        assert payload["position"] is None
        assert payload["max_position"] == MAX_POSITION
        assert payload["error"] is None
        assert payload["live_envelope"] is None

    def test_disabled_state_carries_a_failure_reason(self):
        assert disabled_state("no device at 0x28").to_dict()["error"] == "no device at 0x28"


class TestVolatilePersistence:
    """The MCP401X has no EEPROM and comes up at mid-scale, so the service has
    to keep the setting itself. The DS3502 keeps its own and must not get a
    redundant file."""

    def build(self, tmp_path, **kwargs):
        from openflight.sensitivity import MockMCP401X

        pot = MockMCP401X(**kwargs)
        return (
            SoundSensitivityService(pot, config_path=tmp_path / "sound_sensitivity.json"),
            pot,
        )

    def test_a_volatile_pot_saves_the_setting_to_a_file(self, tmp_path):
        from openflight.sensitivity import load_position

        service, _ = self.build(tmp_path)
        service.start()

        service.set_position(90)

        assert load_position(tmp_path / "sound_sensitivity.json") == 90

    def test_a_volatile_pot_restores_the_saved_setting_at_startup(self, tmp_path):
        from openflight.sensitivity import save_position

        save_position(90, tmp_path / "sound_sensitivity.json")
        service, _ = self.build(tmp_path)

        assert service.start().position == 90

    def test_a_volatile_pot_with_nothing_saved_keeps_its_power_on_value(self, tmp_path):
        from openflight.sensitivity.mcp401x import POWER_ON_POSITION

        service, _ = self.build(tmp_path)

        assert service.start().position == POWER_ON_POSITION

    def test_a_self_persisting_pot_gets_no_file(self, tmp_path):
        service = SoundSensitivityService(
            MockDS3502(), config_path=tmp_path / "sound_sensitivity.json"
        )
        service.start()

        service.set_position(90)

        assert not (tmp_path / "sound_sensitivity.json").exists()

    def test_a_self_persisting_pot_is_left_alone_at_startup(self, tmp_path):
        pot = MockDS3502()
        pot.open()
        pot.set_position(90, store=True)
        pot.close()
        service = SoundSensitivityService(pot, config_path=tmp_path / "s.json")

        assert service.start().position == 90

    def test_an_unwritable_file_reports_but_keeps_the_applied_position(self, tmp_path):
        blocked = tmp_path / "sound_sensitivity.json"
        blocked.mkdir()
        service, _ = self.build(tmp_path)
        service.start()

        state = service.set_position(64)

        assert state.position == 64
        assert "not saved" in state.error

    def test_the_service_reports_which_kind_of_pot_is_fitted(self, tmp_path):
        volatile, _ = self.build(tmp_path)
        persistent = SoundSensitivityService(MockDS3502(), config_path=tmp_path / "s.json")

        assert volatile.persists_in_hardware is False
        assert persistent.persists_in_hardware is True

    def test_bounds_come_from_the_fitted_pot(self, tmp_path):
        service, pot = self.build(tmp_path)
        service.start()

        assert service.state().max_position == pot.max_position

    def test_resistances_come_from_the_fitted_pot(self, tmp_path):
        # A 100k MCP401X and a 10k DS3502 report very different numbers for the
        # same step; the service must not assume either.
        service, pot = self.build(tmp_path)
        service.start()

        state = service.set_position(64)

        assert state.resistance_ohms == pytest.approx(pot.resistance_at(64))
        assert state.preamp_feedback_ohms == pytest.approx(pot.preamp_at(64))
