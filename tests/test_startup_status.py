import json

import pytest

from openflight.startup_status import (
    StartupComponent,
    StartupStatusReporter,
    configured_startup_components,
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
        ("monitor", "Shot simulator")
    ]


def test_reporter_can_mark_optional_component_skipped(tmp_path):
    status_path = tmp_path / "status.json"
    reporter = StartupStatusReporter(status_path, [StartupComponent("camera", "Camera")])

    reporter.skip("camera", "Camera unavailable; continuing")

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["components"][0]["state"] == "skipped"
