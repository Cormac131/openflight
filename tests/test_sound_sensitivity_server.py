"""Server wiring for X9C104 sound-detector sensitivity control."""

import sys
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.launch_monitor import Shot
from openflight.sensitivity import MAX_POSITION, MockDS3502, SoundSensitivityService


@pytest.fixture(name="emitted")
def fixture_emitted(monkeypatch):
    """Capture socket emissions as ``(event, payload)`` pairs."""
    events = []
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None, **_kwargs: events.append((event, payload)),
    )
    return events


@pytest.fixture(name="service")
def fixture_service(monkeypatch):
    """Install a mock-backed, started sensitivity service on the server."""
    service = SoundSensitivityService(MockDS3502())
    service.start()
    monkeypatch.setattr(server_module, "sound_sensitivity_service", service)
    monkeypatch.setattr(
        server_module, "sound_sensitivity_runtime_config", {"enabled": True, "device": "ds3502"}
    )
    return service


def only(events, name):
    """Return the payloads emitted under ``name``."""
    return [payload for event, payload in events if event == name]


class TestReadState:
    def test_get_returns_the_live_position(self, service, emitted):
        server_module.handle_get_sound_sensitivity()

        payload = only(emitted, "sound_sensitivity")[-1]
        assert payload["enabled"] is True
        assert payload["position"] == service.state().position

    def test_get_without_hardware_reports_a_disabled_control(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)
        monkeypatch.setattr(server_module, "sound_sensitivity_runtime_config", {"enabled": False})

        server_module.handle_get_sound_sensitivity()

        payload = only(emitted, "sound_sensitivity")[-1]
        assert payload["enabled"] is False
        assert payload["position"] is None
        assert payload["max_position"] == MAX_POSITION
        assert payload["error"] is None

    def test_a_failed_startup_surfaces_its_reason(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)
        monkeypatch.setattr(
            server_module,
            "sound_sensitivity_runtime_config",
            {"enabled": False, "requested": True, "error": "No DS3502 responding at 0x28"},
        )

        server_module.handle_get_sound_sensitivity()

        assert only(emitted, "sound_sensitivity")[-1]["error"].startswith("No DS3502")


class TestSetPosition:
    def test_setting_a_position_applies_and_broadcasts_it(self, service, emitted):
        server_module.handle_set_sound_sensitivity({"position": 70})

        assert service.state().position == 70
        assert only(emitted, "sound_sensitivity")[-1]["position"] == 70

    def test_an_over_range_request_is_clamped_and_echoed(self, service, emitted):
        server_module.handle_set_sound_sensitivity({"position": 400})

        assert only(emitted, "sound_sensitivity")[-1]["position"] == MAX_POSITION
        assert only(emitted, "sound_sensitivity_error") == []

    def test_a_missing_position_is_rejected_without_moving_the_wiper(self, service, emitted):
        server_module.handle_set_sound_sensitivity({})

        assert service.state().position == MAX_POSITION // 2
        assert only(emitted, "sound_sensitivity_error")[-1]["error"] == "No position provided"

    def test_a_none_payload_is_rejected(self, service, emitted):
        server_module.handle_set_sound_sensitivity(None)

        assert only(emitted, "sound_sensitivity_error")[-1]["error"] == "No position provided"

    def test_an_unusable_position_reports_an_error_and_resends_the_truth(self, service, emitted):
        server_module.handle_set_sound_sensitivity({"position": "loud"})

        assert only(emitted, "sound_sensitivity_error")
        # The UI's optimistic slider has to be pulled back to the real tap.
        assert only(emitted, "sound_sensitivity")[-1]["position"] == MAX_POSITION // 2

    def test_a_hardware_failure_reports_an_error(self, service, emitted, monkeypatch):
        def boom(_position, *, store=False):
            raise OSError("i2c write failed")

        monkeypatch.setattr(service.pot, "set_position", boom)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

        server_module.handle_set_sound_sensitivity({"position": 12})

        assert only(emitted, "sound_sensitivity_error")[-1]["error"] == "i2c write failed"

    def test_setting_without_hardware_explains_why(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.handle_set_sound_sensitivity({"position": 10})

        assert "not enabled" in only(emitted, "sound_sensitivity_error")[-1]["error"]

    def test_applying_a_position_refreshes_the_session_config(self, service, emitted):
        server_module.handle_set_sound_sensitivity({"position": 21})

        assert server_module.sound_sensitivity_runtime_config["state"]["position"] == 21


class TestInit:
    def test_mock_init_enables_a_simulated_control(self, monkeypatch):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)
        monkeypatch.setattr(server_module, "sound_sensitivity_runtime_config", {"enabled": False})

        assert server_module.init_sound_sensitivity(simulated=True) is True

        config = server_module.sound_sensitivity_runtime_config
        assert config["enabled"] is True
        assert config["device"] == "mcp401x"
        assert config["simulated"] is True

    def test_the_runtime_config_records_the_bus_address_and_series_resistor(self, monkeypatch):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.init_sound_sensitivity(
            device="ds3502", bus_number=3, address=0x2A, series_ohms=39_000.0, simulated=True
        )

        config = server_module.sound_sensitivity_runtime_config
        assert config["device"] == "ds3502"
        assert config["i2c_bus"] == 3
        assert config["i2c_address"] == "0x2a"
        assert config["series_ohms"] == 39_000.0

    def test_the_mcp401x_is_the_default_device(self, monkeypatch):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.init_sound_sensitivity(simulated=True)

        assert server_module.sound_sensitivity_runtime_config["device"] == "mcp401x"

    def test_an_explicit_position_is_applied(self, monkeypatch):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.init_sound_sensitivity(position=15, simulated=True)

        assert server_module.sound_sensitivity_service.state().position == 15

    def test_a_failed_init_leaves_the_server_running_without_control(self, monkeypatch):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        # No I2C bus on a dev box, so the real driver path fails to open.
        assert server_module.init_sound_sensitivity() is False

        assert server_module.sound_sensitivity_service is None
        assert server_module.sound_sensitivity_runtime_config["enabled"] is False
        assert server_module.sound_sensitivity_runtime_config["error"]

    def test_the_session_config_snapshot_carries_the_control(self, monkeypatch):
        monkeypatch.setattr(
            server_module,
            "sound_sensitivity_runtime_config",
            {"enabled": True, "device": "ds3502"},
        )

        assert server_module._session_start_config()["sound_sensitivity"]["device"] == "ds3502"


class TestArgumentValidation:
    """A mistyped address or series resistor must fail at the CLI: the first
    would talk to whatever else is on the bus, the second would silently make
    every resistance the UI reports wrong. ``code == 2`` pins each assertion to
    ``parser.error()`` rather than a later ``SystemExit(1)``."""

    def _run(self, monkeypatch, arguments):
        monkeypatch.setattr(sys, "argv", ["openflight-server", *arguments])
        with pytest.raises(SystemExit) as exc_info:
            server_module.main()
        return exc_info.value

    @pytest.mark.parametrize("address", ["0x28", "0x2e", "0x48"])
    def test_an_address_the_mcp401x_cannot_use_is_refused(self, monkeypatch, capsys, address):
        # Fixed at 0x2f: an override could only ever reach another device.
        error = self._run(
            monkeypatch, ["--sound-sensitivity", "--sound-sensitivity-address", address]
        )

        assert error.code == 2
        assert "MCP401X address is fixed" in capsys.readouterr().err

    @pytest.mark.parametrize("address", ["0x27", "0x2c"])
    def test_an_address_outside_the_ds3502_range_is_refused(self, monkeypatch, capsys, address):
        error = self._run(
            monkeypatch,
            [
                "--sound-sensitivity",
                "--sound-sensitivity-device",
                "ds3502",
                "--sound-sensitivity-address",
                address,
            ],
        )

        assert error.code == 2
        assert "DS3502 address must be within" in capsys.readouterr().err

    def test_each_device_defaults_to_its_own_address_and_series_resistor(self, monkeypatch):
        from openflight.sensitivity import DEVICES

        assert DEVICES["mcp401x"]["address"] == 0x2F
        assert DEVICES["mcp401x"]["series_ohms"] == 0.0
        assert DEVICES["ds3502"]["address"] == 0x28
        assert DEVICES["ds3502"]["series_ohms"] == 33_000.0

    def test_a_negative_series_resistor_is_refused(self, monkeypatch, capsys):
        error = self._run(
            monkeypatch, ["--sound-sensitivity", "--sound-sensitivity-series-ohms", "-1"]
        )

        assert error.code == 2
        assert "series-ohms cannot be negative" in capsys.readouterr().err

    @pytest.mark.parametrize("bad", ["-1", "128", "500"])
    def test_an_out_of_range_startup_position_is_refused(self, monkeypatch, capsys, bad):
        error = self._run(monkeypatch, ["--sound-sensitivity", "--sound-sensitivity-position", bad])

        assert error.code == 2
        assert "--sound-sensitivity-position must be within" in capsys.readouterr().err


class TestAutoGain:
    """The closed loop runs from the shot pipeline, so its failure modes must
    never reach the shot: a sensitivity trim is worth less than a captured
    shot, every time."""

    @pytest.fixture(name="auto_service")
    def fixture_auto_service(self, monkeypatch):
        from openflight.sensitivity import (
            AutoGainController,
            EnvelopeMonitor,
            MockADS1115,
            MockDS3502,
        )

        monitor = EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3)
        service = SoundSensitivityService(
            MockDS3502(),
            envelope=monitor,
            controller=AutoGainController(),
            auto_enabled=True,
        )
        service.pot.open()
        monkeypatch.setattr(server_module, "sound_sensitivity_service", service)
        monkeypatch.setattr(server_module, "sound_sensitivity_runtime_config", {"enabled": True})
        return service

    def _shot(self, timestamp=1000.0, mode="rolling-buffer"):
        shot = Shot(ball_speed_mph=120.0, timestamp=datetime.now(), impact_timestamp=timestamp)
        shot.mode = mode
        return shot

    def test_a_shot_feeds_its_envelope_peak_to_the_loop(self, auto_service, emitted):
        auto_service.envelope.add_sample(2.3, timestamp=1000.0)

        server_module._trim_sound_sensitivity_for_shot(self._shot())

        assert auto_service.state().last_peak["fraction_of_full_scale"] == pytest.approx(
            0.697, rel=1e-2
        )
        assert only(emitted, "sound_sensitivity")

    def test_a_shot_with_no_envelope_samples_is_a_no_op(self, auto_service, emitted):
        server_module._trim_sound_sensitivity_for_shot(self._shot())

        assert only(emitted, "sound_sensitivity") == []

    def test_mock_shots_are_ignored(self, auto_service, emitted):
        # A simulated shot has no real envelope behind it; letting it steer the
        # gain would detune a working detector from the Debug page.
        auto_service.envelope.add_sample(2.3, timestamp=1000.0)

        server_module._trim_sound_sensitivity_for_shot(self._shot(mode="mock"))

        assert only(emitted, "sound_sensitivity") == []

    def test_a_failing_loop_never_raises_into_the_shot_pipeline(self, auto_service, emitted):
        auto_service.envelope.add_sample(2.3, timestamp=1000.0)
        auto_service.pot.set_position = lambda *a, **k: (_ for _ in ()).throw(OSError("bus gone"))

        server_module._trim_sound_sensitivity_for_shot(self._shot())

    def test_the_loop_does_nothing_while_switched_off(self, auto_service, emitted):
        auto_service.set_auto_enabled(False)
        auto_service.envelope.add_sample(2.3, timestamp=1000.0)

        server_module._trim_sound_sensitivity_for_shot(self._shot())

        assert only(emitted, "sound_sensitivity") == []

    def test_the_toggle_turns_the_loop_on_and_off(self, auto_service, emitted):
        server_module.handle_set_sound_sensitivity_auto({"enabled": False})

        assert only(emitted, "sound_sensitivity")[-1]["auto_enabled"] is False

        server_module.handle_set_sound_sensitivity_auto({"enabled": True})

        assert only(emitted, "sound_sensitivity")[-1]["auto_enabled"] is True

    def test_enabling_without_the_adc_explains_why(self, service, emitted):
        # `service` is the plain fixture: a pot with no envelope monitor.
        server_module.handle_set_sound_sensitivity_auto({"enabled": True})

        assert "--sound-sensitivity-auto" in only(emitted, "sound_sensitivity_error")[-1]["error"]

    def test_a_manual_move_stands_the_loop_down(self, auto_service, emitted):
        # Otherwise the loop would quietly undo the override on the next shot.
        server_module.handle_set_sound_sensitivity({"position": 20})

        payload = only(emitted, "sound_sensitivity")[-1]
        assert payload["position"] == 20
        assert payload["auto_enabled"] is False

    def test_the_state_advertises_whether_auto_is_available(self, auto_service):
        assert auto_service.state().auto_available is True

    def test_a_pot_without_an_adc_says_auto_is_unavailable(self, service):
        assert service.state().auto_available is False
