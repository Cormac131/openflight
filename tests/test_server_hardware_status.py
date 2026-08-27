"""Tests for the server's hardware-fault reporting.

The regression these guard against is specific and was real: a missing radar
took the whole web server down with it, so the only thing that could have told
a kiosk owner what was wrong never started. The server must now come up, serve
the fault, and report the radar's actual state rather than its own.
"""

import json
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.hardware_status import HardwareStatus, Severity
from openflight.provisioning.detect import DeviceKind


@pytest.fixture(name="clean_status")
def _clean_status(monkeypatch):
    """Give each test its own fault registry and a known monitor state."""
    status = HardwareStatus()
    monkeypatch.setattr(server, "hardware_status", status)
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(server, "mock_mode", False)
    return status


def fake_monitor(*, is_connected=True, has_radar=True, legacy=False):
    """A monitor stand-in whose radar reports a given liveness."""
    if not has_radar:
        return SimpleNamespace()
    if legacy:
        # A radar object from before is_connected existed.
        return SimpleNamespace(radar=SimpleNamespace(port="/dev/ttyACM0"))
    return SimpleNamespace(radar=SimpleNamespace(is_connected=is_connected))


class TestRadarIsConnected:
    def test_false_without_a_monitor(self, clean_status):
        assert server.radar_is_connected() is False

    def test_true_in_mock_mode(self, clean_status, monkeypatch):
        """The simulated radar is working, as far as the UI is concerned."""
        monkeypatch.setattr(server, "monitor", object())
        monkeypatch.setattr(server, "mock_mode", True)
        assert server.radar_is_connected() is True

    def test_true_when_the_serial_handle_is_open(self, clean_status, monkeypatch):
        monkeypatch.setattr(server, "monitor", fake_monitor(is_connected=True))
        assert server.radar_is_connected() is True

    def test_false_when_the_radar_is_no_longer_open(self, clean_status, monkeypatch):
        """The old flag could never report this — it did not ask the radar."""
        monkeypatch.setattr(server, "monitor", fake_monitor(is_connected=False))
        assert server.radar_is_connected() is False

    def test_false_when_the_monitor_has_no_radar(self, clean_status, monkeypatch):
        monkeypatch.setattr(server, "monitor", fake_monitor(has_radar=False))
        assert server.radar_is_connected() is False

    def test_legacy_radar_without_the_property_is_assumed_connected(
        self, clean_status, monkeypatch
    ):
        """Match the old behaviour rather than reporting a false fault."""
        monkeypatch.setattr(server, "monitor", fake_monitor(legacy=True))
        assert server.radar_is_connected() is True


class TestHardwareStatusPayload:
    def test_healthy_payload(self, clean_status, monkeypatch):
        monkeypatch.setattr(server, "monitor", fake_monitor(is_connected=True))
        payload = server._get_hardware_status()
        assert payload["ok"] is True
        assert payload["blocking"] is None

    def test_missing_radar_payload(self, clean_status):
        clean_status.record(DeviceKind.OPS243, Severity.BLOCKING, "no radar")
        payload = server._get_hardware_status()
        assert payload["radar_connected"] is False
        assert payload["blocking"]["device"] == "ops243"
        assert payload["blocking"]["remedy"]

    def test_degraded_payload_does_not_block(self, clean_status, monkeypatch):
        monkeypatch.setattr(server, "monitor", fake_monitor(is_connected=True))
        clean_status.record(DeviceKind.IWR6843, Severity.DEGRADED, "no CLI")
        payload = server._get_hardware_status()
        assert payload["blocking"] is None
        assert payload["faults"][0]["device"] == "iwr6843"


class TestTriggerStatusReflectsRealState:
    def test_radar_connected_follows_the_serial_handle(self, clean_status, monkeypatch):
        monkeypatch.setattr(server, "monitor", fake_monitor(is_connected=False))
        assert server._get_trigger_status()["radar_connected"] is False

    def test_radar_connected_true_when_live(self, clean_status, monkeypatch):
        monkeypatch.setattr(server, "monitor", fake_monitor(is_connected=True))
        assert server._get_trigger_status()["radar_connected"] is True


class TestApiEndpoint:
    def test_serves_the_fault_as_json(self, clean_status):
        clean_status.record(DeviceKind.OPS243, Severity.BLOCKING, "unplugged")
        client = server.app.test_client()
        response = client.get("/api/hardware-status")
        assert response.status_code == 200
        payload = json.loads(response.data)
        assert payload["blocking"]["device"] == "ops243"
        assert payload["blocking"]["detail"] == "unplugged"

    def test_serves_a_clean_bill_of_health(self, clean_status, monkeypatch):
        monkeypatch.setattr(server, "monitor", fake_monitor(is_connected=True))
        response = server.app.test_client().get("/api/hardware-status")
        assert json.loads(response.data)["ok"] is True


class TestStartMonitorSurvivesAFailedRadar:
    def test_records_a_blocking_fault_instead_of_raising(self, clean_status, monkeypatch):
        """This is the whole point: no exception escapes to kill the server."""

        class DeadMonitor:
            def __init__(self, *args, **kwargs):
                self.radar = SimpleNamespace(is_connected=False)

            def connect(self):
                raise ConnectionError("No OPS243 radar found on USB")

        monkeypatch.setattr(
            "openflight.rolling_buffer.RollingBufferMonitor", DeadMonitor, raising=False
        )
        monkeypatch.setattr(server, "stop_monitor", lambda: None)

        server.start_monitor(port=None, mock=False, trigger_type="sound")

        fault = clean_status.get(DeviceKind.OPS243)
        assert fault is not None
        assert fault.severity is Severity.BLOCKING
        assert "No OPS243 radar found" in fault.detail

    def test_the_monitor_is_kept_so_the_ui_still_learns_the_mode(
        self, clean_status, monkeypatch
    ):
        class DeadMonitor:
            trigger_type = "sound"

            def __init__(self, *args, **kwargs):
                self.radar = SimpleNamespace(is_connected=False)

            def connect(self):
                raise ConnectionError("nope")

        monkeypatch.setattr(
            "openflight.rolling_buffer.RollingBufferMonitor", DeadMonitor, raising=False
        )
        monkeypatch.setattr(server, "stop_monitor", lambda: None)

        server.start_monitor(port=None, mock=False, trigger_type="sound")

        status = server._get_trigger_status()
        assert status["radar_connected"] is False
        assert status["mode"] == "rolling-buffer"

    def test_a_successful_connect_clears_a_previous_fault(self, clean_status, monkeypatch):
        """Restarting the monitor after fixing the cable must clear the screen."""
        clean_status.record(DeviceKind.OPS243, Severity.BLOCKING, "stale")

        class LiveMonitor:
            trigger_type = "sound"

            def __init__(self, *args, **kwargs):
                self.radar = SimpleNamespace(is_connected=True, baud=230400)

            def connect(self):
                return True

            def get_radar_info(self):
                return {}

            def start(self, **kwargs):
                return None

        monkeypatch.setattr(
            "openflight.rolling_buffer.RollingBufferMonitor", LiveMonitor, raising=False
        )
        monkeypatch.setattr(server, "stop_monitor", lambda: None)
        monkeypatch.setattr(server, "get_session_logger", lambda: None)

        server.start_monitor(port=None, mock=False, trigger_type="sound")

        assert clean_status.get(DeviceKind.OPS243) is None
