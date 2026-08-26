"""Sensitivity service: calibration on start, clamping, persistence, recovery."""

import json

import pytest

from openflight.sensitivity import (
    DEFAULT_POSITION,
    DEFAULT_R17_OHMS,
    MAX_POSITION,
    MockX9C104,
    SoundSensitivityService,
    clamp_position,
    disabled_state,
    load_position,
    position_for_resistance,
    save_position,
)


class SpyPot(MockX9C104):
    """A mock pot that records the calls the service makes."""

    def __init__(self, *, fail_on_set=None):
        super().__init__()
        self.calls = []
        self.fail_on_set = fail_on_set

    def open(self):
        self.calls.append(("open",))

    def calibrate(self):
        self.calls.append(("calibrate",))
        return super().calibrate()

    def set_position(self, position, *, store=False):
        self.calls.append(("set_position", position, store))
        if self.fail_on_set is not None and position == self.fail_on_set:
            raise OSError("wiper line stuck")
        return super().set_position(position, store=store)

    def close(self):
        self.calls.append(("close",))
        super().close()


@pytest.fixture(name="config_path")
def fixture_config_path(tmp_path):
    return tmp_path / "sound_sensitivity.json"


def build_service(config_path, **kwargs):
    pot = SpyPot()
    return SoundSensitivityService(pot, config_path=config_path, **kwargs), pot


class TestDefaults:
    def test_default_position_matches_the_documented_47k_resistor(self):
        assert DEFAULT_POSITION == position_for_resistance(DEFAULT_R17_OHMS)

    def test_default_position_is_inside_the_wiper_range(self):
        assert 0 <= DEFAULT_POSITION <= MAX_POSITION


class TestClamping:
    @pytest.mark.parametrize(
        "value,expected", [(-40, 0), (0, 0), (46, 46), (99, 99), (250, 99), (46.0, 46), ("46", 46)]
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
    def test_start_calibrates_before_applying_a_position(self, config_path):
        service, pot = build_service(config_path)

        service.start()

        assert pot.calls[0] == ("open",)
        assert pot.calls[1] == ("calibrate",)
        assert pot.calls[2][0] == "set_position"

    def test_start_uses_the_default_position_with_no_saved_file(self, config_path):
        service, _ = build_service(config_path)

        state = service.start()

        assert state.position == DEFAULT_POSITION
        assert state.enabled is True

    def test_start_restores_the_saved_position(self, config_path):
        save_position(72, config_path)
        service, _ = build_service(config_path)

        state = service.start()

        assert state.position == 72

    def test_start_falls_back_to_the_default_for_a_corrupt_file(self, config_path):
        config_path.write_text("{not json", encoding="utf-8")
        service, _ = build_service(config_path)

        state = service.start()

        assert state.position == DEFAULT_POSITION

    def test_start_falls_back_to_the_default_for_an_out_of_range_file(self, config_path):
        config_path.write_text(json.dumps({"position": 5000}), encoding="utf-8")
        service, _ = build_service(config_path)

        state = service.start()

        assert state.position == DEFAULT_POSITION

    def test_start_never_writes_the_chip_nvm(self, config_path):
        service, pot = build_service(config_path)

        service.start()

        assert all(call[2] is False for call in pot.calls if call[0] == "set_position")


class TestSetPosition:
    def test_setting_a_position_persists_it(self, config_path):
        service, _ = build_service(config_path)
        service.start()

        service.set_position(80)

        assert load_position(config_path) == 80

    def test_state_reports_percent_and_both_resistances(self, config_path):
        service, _ = build_service(config_path)
        service.start()

        state = service.set_position(MAX_POSITION)

        assert state.sensitivity_percent == pytest.approx(100.0)
        assert state.resistance_ohms == pytest.approx(100_040, rel=1e-3)
        assert state.preamp_feedback_ohms == pytest.approx(50_010, rel=1e-3)

    def test_an_over_range_request_saturates_instead_of_failing(self, config_path):
        service, _ = build_service(config_path)
        service.start()

        state = service.set_position(4000)

        assert state.position == MAX_POSITION
        assert load_position(config_path) == MAX_POSITION

    def test_a_hardware_failure_propagates_and_is_not_persisted(self, config_path):
        pot = SpyPot(fail_on_set=90)
        service = SoundSensitivityService(pot, config_path=config_path)
        service.start()

        with pytest.raises(OSError):
            service.set_position(90)

        # start() only reads the file; nothing is written until a move lands.
        assert load_position(config_path) is None
        assert service.state().error == "wiper line stuck"

    def test_a_later_success_clears_the_recorded_error(self, config_path):
        pot = SpyPot(fail_on_set=90)
        service = SoundSensitivityService(pot, config_path=config_path)
        service.start()
        with pytest.raises(OSError):
            service.set_position(90)

        state = service.set_position(20)

        assert state.error is None

    def test_an_unwritable_config_reports_but_keeps_the_applied_position(self, tmp_path):
        # A directory where the file should be: the write fails, the wiper move
        # already happened, and the UI needs to hear both halves of that.
        blocked = tmp_path / "sound_sensitivity.json"
        blocked.mkdir()
        service, _ = build_service(blocked)
        service.pot.calibrate()

        state = service.set_position(64)

        assert state.position == 64
        assert "not saved" in state.error

    def test_the_payload_rounds_for_the_ui(self, config_path):
        service, _ = build_service(config_path)
        service.start()

        payload = service.set_position(33).to_dict()

        assert payload["position"] == 33
        assert payload["max_position"] == MAX_POSITION
        assert isinstance(payload["resistance_ohms"], int)
        assert payload["enabled"] is True
        assert payload["simulated"] is False


class TestRecalibrate:
    def test_recalibrate_rehomes_and_restores_the_position(self, config_path):
        service, pot = build_service(config_path)
        service.start()
        service.set_position(70)
        pot.calls.clear()

        state = service.recalibrate()

        assert ("calibrate",) in pot.calls
        assert state.position == 70

    def test_recalibrate_before_start_uses_the_saved_position(self, config_path):
        save_position(12, config_path)
        service, _ = build_service(config_path)

        state = service.recalibrate()

        assert state.position == 12

    def test_recalibrate_with_nothing_saved_uses_the_default(self, config_path):
        service, _ = build_service(config_path)

        state = service.recalibrate()

        assert state.position == DEFAULT_POSITION


class TestLifecycle:
    def test_stop_closes_the_pot(self, config_path):
        service, pot = build_service(config_path)
        service.start()

        service.stop()

        assert ("close",) in pot.calls

    def test_stop_swallows_a_failing_close(self, config_path):
        service, pot = build_service(config_path)
        service.start()
        pot.close = lambda: (_ for _ in ()).throw(OSError("gone"))

        service.stop()

    def test_state_after_close_reports_no_position(self, config_path):
        service, _ = build_service(config_path)
        service.start()
        service.stop()

        assert service.state().position is None

    def test_simulated_services_say_so(self, config_path):
        service = SoundSensitivityService(MockX9C104(), config_path=config_path, simulated=True)

        assert service.start().to_dict()["simulated"] is True


class TestDisabledState:
    def test_disabled_state_has_no_position_but_keeps_the_ui_bounds(self):
        payload = disabled_state().to_dict()

        assert payload["enabled"] is False
        assert payload["position"] is None
        assert payload["max_position"] == MAX_POSITION
        assert payload["default_position"] == DEFAULT_POSITION
        assert payload["error"] is None

    def test_disabled_state_carries_a_failure_reason(self):
        assert disabled_state("GPIO 22 busy").to_dict()["error"] == "GPIO 22 busy"


class TestConfigFile:
    def test_saving_creates_missing_parent_directories(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "sound_sensitivity.json"

        save_position(19, path)

        assert load_position(path) == 19

    def test_missing_file_reads_as_no_preference(self, tmp_path):
        assert load_position(tmp_path / "absent.json") is None

    @pytest.mark.parametrize("payload", ['{"position": "40"}', '{"position": true}', "[]", "null"])
    def test_non_integer_payloads_read_as_no_preference(self, tmp_path, payload):
        path = tmp_path / "sound_sensitivity.json"
        path.write_text(payload, encoding="utf-8")

        assert load_position(path) is None


class TestForcedStartPosition:
    """``--sound-sensitivity-position`` overrides the file for one run only."""

    def test_a_forced_position_is_applied(self, config_path):
        service, _ = build_service(config_path)

        state = service.start(force_position=88)

        assert state.position == 88

    def test_a_forced_position_beats_the_saved_one(self, config_path):
        save_position(12, config_path)
        service, _ = build_service(config_path)

        assert service.start(force_position=88).position == 88

    def test_a_forced_position_does_not_rewrite_the_saved_one(self, config_path):
        save_position(12, config_path)
        service, _ = build_service(config_path)

        service.start(force_position=88)

        assert load_position(config_path) == 12

    def test_a_forced_position_is_clamped(self, config_path):
        service, _ = build_service(config_path)

        assert service.start(force_position=4000).position == MAX_POSITION

    def test_a_later_ui_change_still_persists(self, config_path):
        service, _ = build_service(config_path)
        service.start(force_position=88)

        service.set_position(20)

        assert load_position(config_path) == 20
