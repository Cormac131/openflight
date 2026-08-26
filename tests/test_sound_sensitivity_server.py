"""Server wiring for X9C104 sound-detector sensitivity control."""

import sys

import pytest

from openflight import server as server_module
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
        assert config["device"] == "ds3502"
        assert config["simulated"] is True

    def test_the_runtime_config_records_the_bus_address_and_series_resistor(self, monkeypatch):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.init_sound_sensitivity(
            bus_number=3, address=0x2A, series_ohms=39_000.0, simulated=True
        )

        config = server_module.sound_sensitivity_runtime_config
        assert config["i2c_bus"] == 3
        assert config["i2c_address"] == "0x2a"
        assert config["series_ohms"] == 39_000.0

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

    @pytest.mark.parametrize("address", ["0x27", "0x2c", "0x18"])
    def test_an_address_outside_the_jumper_range_is_refused(self, monkeypatch, capsys, address):
        error = self._run(
            monkeypatch, ["--sound-sensitivity", "--sound-sensitivity-address", address]
        )

        assert error.code == 2
        assert "DS3502 address must be within" in capsys.readouterr().err

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
