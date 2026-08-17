import json
import sys

import pytest

from openflight.startup_status import (
    StartupComponent,
    StartupStatusReporter,
    complete_startup_status,
    configured_startup_components,
    fail_startup_status,
)


def test_reporter_writes_versioned_status_and_omits_disabled_components(tmp_path):
    status_path = tmp_path / "status.json"
    reporter = StartupStatusReporter(
        status_path,
        [
            StartupComponent("ops", "OPS radar"),
            StartupComponent("camera", "Camera"),
        ],
    )

    reporter.start("ops", "Connecting OPS radar")
    reporter.ready("ops", "OPS radar connected")

    assert json.loads(status_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "overall": "starting",
        "message": "OPS radar connected",
        "components": [
            {"id": "ops", "label": "OPS radar", "state": "ready"},
            {"id": "camera", "label": "Camera", "state": "waiting"},
        ],
    }


def test_reporter_rejects_unknown_components(tmp_path):
    reporter = StartupStatusReporter(
        tmp_path / "status.json", [StartupComponent("ops", "OPS radar")]
    )

    with pytest.raises(KeyError, match="camera"):
        reporter.start("camera")


def test_reporter_marks_overall_ready_after_components_are_initialized(tmp_path):
    status_path = tmp_path / "status.json"
    reporter = StartupStatusReporter(status_path, [StartupComponent("ops", "OPS radar")])

    reporter.start("ops")
    reporter.ready("ops")
    reporter.finish("OpenFlight is ready")

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["overall"] == "ready"
    assert payload["message"] == "OpenFlight is ready"


def test_reporter_replaces_status_atomically(tmp_path, monkeypatch):
    status_path = tmp_path / "status.json"
    replacements = []

    def record_replace(source, destination):
        replacements.append((source, destination, source.read_text(encoding="utf-8")))

    monkeypatch.setattr("openflight.startup_status.os.replace", record_replace)
    reporter = StartupStatusReporter(status_path, [StartupComponent("ops", "OPS radar")])

    reporter.start("ops")

    assert replacements
    source, destination, content = replacements[-1]
    assert source.parent == status_path.parent
    assert destination == status_path
    assert json.loads(content)["components"][0]["state"] == "starting"


def test_configured_components_hide_disabled_hardware():
    components = configured_startup_components(
        mock=False,
        camera=False,
        iwr6843=True,
        inclinometer=True,
        kld7=False,
        kld7_horizontal=False,
        battery=False,
        simulators=False,
    )

    assert [(item.component_id, item.label) for item in components] == [
        ("ti", "TI radar"),
        ("inclinometer", "Inclinometer"),
        ("ops", "OPS radar"),
        ("server", "OpenFlight server"),
    ]


def test_mock_mode_replaces_ops_with_shot_simulator():
    components = configured_startup_components(
        mock=True,
        camera=False,
        iwr6843=False,
        inclinometer=False,
        kld7=False,
        kld7_horizontal=False,
        battery=False,
        simulators=False,
    )

    assert [(item.component_id, item.label) for item in components] == [
        ("monitor", "Shot simulator"),
        ("server", "OpenFlight server"),
    ]


def test_shell_readiness_boundary_marks_server_and_overall_ready(tmp_path):
    status_path = tmp_path / "status.json"
    reporter = StartupStatusReporter(status_path, [StartupComponent("server", "OpenFlight server")])
    reporter.start("server", "Starting OpenFlight server")

    complete_startup_status(status_path)

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["overall"] == "ready"
    assert payload["message"] == "OpenFlight is ready"
    assert payload["components"] == [
        {"id": "server", "label": "OpenFlight server", "state": "ready"}
    ]


def test_failure_identifies_component_recovery_and_log_location(tmp_path):
    status_path = tmp_path / "status.json"
    reporter = StartupStatusReporter(
        status_path,
        [
            StartupComponent("ti", "TI radar"),
            StartupComponent("ops", "OPS radar"),
        ],
    )
    reporter.start("ti")

    fail_startup_status(
        status_path,
        component_id="ti",
        message="TI radar failed to initialize",
        recovery="Check the TI radar USB and power connections, then relaunch OpenFlight.",
        log_path="/home/pacinoj/openflight_sessions/terminal_logs/",
    )

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["overall"] == "error"
    assert payload["message"] == "TI radar failed to initialize"
    assert payload["components"][0]["state"] == "error"
    assert payload["error"] == {
        "recovery": "Check the TI radar USB and power connections, then relaunch OpenFlight.",
        "log_path": "/home/pacinoj/openflight_sessions/terminal_logs/",
    }


def test_generic_failure_preserves_an_existing_component_error(tmp_path):
    status_path = tmp_path / "status.json"
    StartupStatusReporter(status_path, [StartupComponent("ops", "OPS radar")])
    fail_startup_status(
        status_path,
        component_id="ops",
        message="OPS radar failed to initialize",
        recovery="Reconnect the OPS radar.",
        log_path="/logs",
    )

    changed = fail_startup_status(
        status_path,
        component_id=None,
        message="OpenFlight server exited during startup",
        recovery="Relaunch OpenFlight.",
        log_path="/logs",
        preserve_existing=True,
    )

    assert changed is False
    assert json.loads(status_path.read_text(encoding="utf-8"))["message"] == (
        "OPS radar failed to initialize"
    )


def test_reporter_can_mark_optional_component_skipped(tmp_path):
    status_path = tmp_path / "status.json"
    reporter = StartupStatusReporter(status_path, [StartupComponent("camera", "Camera")])

    reporter.skip("camera", "Camera unavailable; continuing")

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["components"][0]["state"] == "skipped"


def test_server_publishes_camera_kld7_and_ops_success(tmp_path, monkeypatch):
    """The real sequential startup boundaries should publish their success events."""
    from openflight import server

    status_path = tmp_path / "status.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--no-logging",
            "--kld7",
            "--kld7-mount-tilt",
            "10",
            "--startup-status-file",
            str(status_path),
        ],
    )
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "init_camera", lambda **_kwargs: True)
    monkeypatch.setattr(server, "start_camera_thread", lambda: None)
    monkeypatch.setattr(server, "init_kld7", lambda **_kwargs: True)
    monkeypatch.setattr(server, "start_monitor", lambda **_kwargs: None)
    monkeypatch.setattr(server.socketio, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)

    server.main()

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    states = {component["id"]: component["state"] for component in payload["components"]}
    assert payload["overall"] == "starting"
    assert states == {
        "camera": "ready",
        "kld7_vertical": "ready",
        "ops": "ready",
        "server": "starting",
    }


def test_server_publishes_ops_failure_and_cleans_up(tmp_path, monkeypatch):
    from openflight import server

    status_path = tmp_path / "status.json"
    cleanup_calls = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--no-camera",
            "--no-logging",
            "--startup-status-file",
            str(status_path),
        ],
    )
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)

    def fail_monitor(**_kwargs):
        raise RuntimeError("serial port unavailable")

    monkeypatch.setattr(server, "start_monitor", fail_monitor)
    monkeypatch.setattr(
        server, "_cleanup_hardware_for_shutdown", lambda: cleanup_calls.append("cleanup")
    )

    with pytest.raises(RuntimeError, match="serial port unavailable"):
        server.main()

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    states = {component["id"]: component["state"] for component in payload["components"]}
    assert payload["overall"] == "error"
    assert payload["message"] == "OPS radar failed to initialize"
    assert states["ops"] == "error"
    assert cleanup_calls == ["cleanup"]
