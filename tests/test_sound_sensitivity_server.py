"""Server wiring for X9C104 sound-detector sensitivity control."""

import sys
from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.sensitivity import (
    DEFAULT_POSITION,
    MAX_POSITION,
    MockX9C104,
    SoundSensitivityService,
)


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
def fixture_service(monkeypatch, tmp_path):
    """Install a mock-backed, started sensitivity service on the server."""
    service = SoundSensitivityService(MockX9C104(), config_path=tmp_path / "sound_sensitivity.json")
    service.start()
    monkeypatch.setattr(server_module, "sound_sensitivity_service", service)
    monkeypatch.setattr(
        server_module, "sound_sensitivity_runtime_config", {"enabled": True, "device": "x9c104"}
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
            {"enabled": False, "requested": True, "error": "Could not claim X9C104 GPIO lines"},
        )

        server_module.handle_get_sound_sensitivity()

        assert only(emitted, "sound_sensitivity")[-1]["error"].startswith("Could not claim")


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

        assert service.state().position == DEFAULT_POSITION
        assert only(emitted, "sound_sensitivity_error")[-1]["error"] == "No position provided"

    def test_a_none_payload_is_rejected(self, service, emitted):
        server_module.handle_set_sound_sensitivity(None)

        assert only(emitted, "sound_sensitivity_error")[-1]["error"] == "No position provided"

    def test_an_unusable_position_reports_an_error_and_resends_the_truth(self, service, emitted):
        server_module.handle_set_sound_sensitivity({"position": "loud"})

        assert only(emitted, "sound_sensitivity_error")
        # The UI's optimistic slider has to be pulled back to the real tap.
        assert only(emitted, "sound_sensitivity")[-1]["position"] == DEFAULT_POSITION

    def test_a_hardware_failure_reports_an_error(self, service, emitted, monkeypatch):
        def boom(_position, *, store=False):
            raise OSError("wiper line stuck")

        monkeypatch.setattr(service.pot, "set_position", boom)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

        server_module.handle_set_sound_sensitivity({"position": 12})

        assert only(emitted, "sound_sensitivity_error")[-1]["error"] == "wiper line stuck"

    def test_setting_without_hardware_explains_why(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.handle_set_sound_sensitivity({"position": 10})

        assert "not enabled" in only(emitted, "sound_sensitivity_error")[-1]["error"]

    def test_applying_a_position_refreshes_the_session_config(self, service, emitted):
        server_module.handle_set_sound_sensitivity({"position": 21})

        assert server_module.sound_sensitivity_runtime_config["state"]["position"] == 21


class TestRecalibrate:
    def test_recalibrate_rehomes_and_broadcasts(self, service, emitted):
        server_module.handle_set_sound_sensitivity({"position": 55})
        emitted.clear()

        server_module.handle_recalibrate_sound_sensitivity()

        assert only(emitted, "sound_sensitivity")[-1]["position"] == 55

    def test_recalibrate_without_hardware_explains_why(self, monkeypatch, emitted):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.handle_recalibrate_sound_sensitivity()

        assert "not enabled" in only(emitted, "sound_sensitivity_error")[-1]["error"]

    def test_a_failing_recalibration_reports_an_error(self, service, emitted, monkeypatch):
        def boom():
            raise OSError("CS line stuck low")

        monkeypatch.setattr(service.pot, "calibrate", boom)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

        server_module.handle_recalibrate_sound_sensitivity()

        assert only(emitted, "sound_sensitivity_error")[-1]["error"] == "CS line stuck low"


class TestInit:
    def test_mock_init_enables_a_simulated_control(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)
        monkeypatch.setattr(server_module, "sound_sensitivity_runtime_config", {"enabled": False})
        monkeypatch.setattr("openflight.sensitivity.service.CONFIG_PATH", tmp_path / "s.json")

        assert (
            server_module.init_sound_sensitivity(cs_pin=22, inc_pin=23, ud_pin=24, simulated=True)
            is True
        )

        config = server_module.sound_sensitivity_runtime_config
        assert config["enabled"] is True
        assert config["simulated"] is True
        assert config["state"]["position"] == DEFAULT_POSITION

    def test_an_explicit_position_overrides_the_saved_setting(self, monkeypatch, tmp_path):
        from openflight.sensitivity import load_position, save_position

        config_path = tmp_path / "s.json"
        save_position(80, config_path)
        monkeypatch.setattr("openflight.sensitivity.service.CONFIG_PATH", config_path)
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)

        server_module.init_sound_sensitivity(
            cs_pin=22, inc_pin=23, ud_pin=24, position=15, simulated=True
        )

        assert server_module.sound_sensitivity_service.state().position == 15
        # The flag steers this run; it must not silently rewrite what the UI saved.
        assert load_position(config_path) == 80

    def test_a_failed_init_leaves_the_server_running_without_control(self, monkeypatch):
        monkeypatch.setattr(server_module, "sound_sensitivity_service", None)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        # No gpiozero on a dev box, so the real driver path fails to claim lines.
        assert server_module.init_sound_sensitivity(cs_pin=22, inc_pin=23, ud_pin=24) is False

        assert server_module.sound_sensitivity_service is None
        assert server_module.sound_sensitivity_runtime_config["enabled"] is False
        assert server_module.sound_sensitivity_runtime_config["error"]

    def test_the_session_config_snapshot_carries_the_control(self, monkeypatch):
        monkeypatch.setattr(
            server_module,
            "sound_sensitivity_runtime_config",
            {"enabled": True, "device": "x9c104"},
        )

        assert server_module._session_start_config()["sound_sensitivity"]["device"] == "x9c104"


class TestReservedPins:
    """Only pins the configured build really drives are refused. A Pi with the
    OPS243 on USB, no UPS, and no inclinometer genuinely has I2C, UART, and
    BCM6 free, and blocking them would rule out a valid wiring choice."""

    def _args(self, **overrides):
        defaults = {
            "iwr6843_trigger_pin": 17,
            "inclinometer": False,
            "battery": None,
            "port": None,
        }
        return SimpleNamespace(**{**defaults, **overrides})

    def test_the_trigger_pin_is_always_reserved(self):
        assert 17 in server_module.reserved_gpio_pins(self._args())

    def test_a_remapped_trigger_pin_moves_the_reservation(self):
        reserved = server_module.reserved_gpio_pins(self._args(iwr6843_trigger_pin=27))

        assert 27 in reserved
        assert 17 not in reserved

    def test_a_bare_build_leaves_i2c_uart_and_the_ups_pins_free(self):
        reserved = server_module.reserved_gpio_pins(self._args())

        assert set(reserved) == {17}

    def test_the_inclinometer_reserves_i2c(self):
        reserved = server_module.reserved_gpio_pins(self._args(inclinometer=True))

        assert {2, 3} <= set(reserved)

    def test_the_geekworm_ups_reserves_i2c_ac_detect_and_charge_control(self):
        reserved = server_module.reserved_gpio_pins(self._args(battery="geekworm"))

        assert {2, 3, 6, 16} <= set(reserved)

    def test_the_gpio_uart_reserves_the_serial_pair(self):
        reserved = server_module.reserved_gpio_pins(self._args(port="/dev/ttyAMA0"))

        assert {14, 15} <= set(reserved)

    def test_a_usb_radar_leaves_the_serial_pair_free(self):
        reserved = server_module.reserved_gpio_pins(self._args(port="/dev/ttyUSB0"))

        assert 14 not in reserved
        assert 15 not in reserved

    def test_every_reservation_explains_itself(self):
        reserved = server_module.reserved_gpio_pins(
            self._args(battery="geekworm", inclinometer=True, port="/dev/ttyAMA0")
        )

        assert all(isinstance(reason, str) and reason for reason in reserved.values())


class TestArgumentValidation:
    """The digipot claims three GPIOs; a collision or a bad tap must fail loudly
    at the CLI rather than silently stealing the trigger line or leaving the
    wiper somewhere the UI never asked for. ``code == 2`` pins each assertion to
    ``parser.error()`` rather than a later ``SystemExit(1)`` from hardware init
    failing in a test environment."""

    def _run(self, monkeypatch, arguments):
        monkeypatch.setattr(sys, "argv", ["openflight-server", *arguments])
        with pytest.raises(SystemExit) as exc_info:
            server_module.main()
        return exc_info.value

    def test_reusing_the_trigger_pin_is_refused(self, monkeypatch, capsys):
        error = self._run(monkeypatch, ["--sound-sensitivity", "--sound-sensitivity-cs-pin", "17"])

        assert error.code == 2
        assert "carries the shared sound-trigger edge" in capsys.readouterr().err

    def test_a_ups_pin_is_refused_only_when_the_ups_is_enabled(self, monkeypatch, capsys):
        error = self._run(
            monkeypatch,
            ["--sound-sensitivity", "--sound-sensitivity-cs-pin", "6", "--battery", "geekworm"],
        )

        assert error.code == 2
        assert "Geekworm AC-detect" in capsys.readouterr().err

    def test_duplicate_digipot_pins_are_refused(self, monkeypatch, capsys):
        error = self._run(
            monkeypatch,
            [
                "--sound-sensitivity",
                "--sound-sensitivity-cs-pin",
                "23",
                "--sound-sensitivity-inc-pin",
                "23",
            ],
        )

        assert error.code == 2
        assert "three distinct BCM GPIOs" in capsys.readouterr().err

    @pytest.mark.parametrize("bad", ["-1", "100", "500"])
    def test_an_out_of_range_startup_position_is_refused(self, monkeypatch, capsys, bad):
        error = self._run(monkeypatch, ["--sound-sensitivity", "--sound-sensitivity-position", bad])

        assert error.code == 2
        assert "--sound-sensitivity-position must be within" in capsys.readouterr().err
