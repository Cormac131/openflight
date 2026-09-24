"""Server integration for per-shot LIS3DH tilt compensation."""

import json
import math
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.inclinometer import OrientationSnapshot, SnapshotSelection
from openflight.iwr6843.calibration import Calibration
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger


class FakeInclinometer:
    def __init__(self, selection):
        self.selection = selection
        self.impact_timestamp = None

    def snapshot_for_impact(self, impact_timestamp):
        self.impact_timestamp = impact_timestamp
        return self.selection


def _stable_selection():
    return SnapshotSelection(
        snapshot=OrientationSnapshot(
            timestamp=99.8,
            x_g=0.0,
            y_g=0.0523,
            z_g=0.9986,
            gravity_g=1.0,
            raw_pitch_deg=3.0,
            calibrated_pitch_deg=4.5,
            pitch_std_deg=0.1,
            sample_count=8,
        ),
        status="stable",
        age_s=0.2,
    )


def test_server_passes_effective_preimpact_tilt_to_iwr(monkeypatch):
    sensor = FakeInclinometer(_stable_selection())
    calibration = Calibration.identity()
    calibration.tilt_rad = math.radians(11.5)
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 1.5})
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)
    server._process_iwr6843_angle(shot)

    assert sensor.impact_timestamp == 100.0
    assert shot.inclinometer["configured_iwr_tilt_deg"] == 11.5
    assert shot.inclinometer["effective_iwr_tilt_deg"] == 16.0
    assert shot.inclinometer["age_s"] == 0.2
    assert seen["tilt_deg"] == 16.0


def test_server_preserves_configured_tilt_without_stable_snapshot(monkeypatch):
    sensor = FakeInclinometer(
        SnapshotSelection(snapshot=None, status="no_stable_preimpact_reading")
    )
    calibration = Calibration.identity()
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 1.5})
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)
    server._process_iwr6843_angle(shot)

    assert shot.inclinometer["applied"] is False
    assert shot.inclinometer["status"] == "no_stable_preimpact_reading"
    assert seen["tilt_deg"] is None


def test_init_inclinometer_is_fail_soft(monkeypatch):
    class BrokenSensor:
        def __init__(self, **_kwargs):
            pass

    class BrokenService:
        def __init__(self, _sensor, **_kwargs):
            pass

        def start(self):
            raise OSError("I2C unavailable")

        def stop(self):
            pass

    monkeypatch.setattr("openflight.inclinometer.LIS3DH", BrokenSensor)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", BrokenService)
    monkeypatch.setattr(server, "log_session_error", lambda *_args, **_kwargs: None)

    assert server.init_inclinometer(zero_offset_deg=1.5) is False
    assert server.inclinometer_service is None
    assert server.inclinometer_runtime_config["requested"] is True
    assert server.inclinometer_runtime_config["error"] == "I2C unavailable"


def test_inclinometer_flag_requires_iwr6843(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["openflight-server", "--inclinometer"])

    with pytest.raises(SystemExit, match="2"):
        server.main()


def test_shot_dict_includes_inclinometer_provenance():
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    shot.inclinometer = {"applied": True, "effective_iwr_tilt_deg": 16.0}

    assert server.shot_to_dict(shot)["inclinometer"] == shot.inclinometer


def test_session_log_records_orientation_used_for_shot(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    orientation = {
        "applied": True,
        "calibrated_pitch_deg": 4.5,
        "effective_iwr_tilt_deg": 16.0,
    }

    shot = Shot(
        ball_speed_mph=100.0,
        club_speed_mph=70.0,
        timestamp=datetime.now(),
        peak_magnitude=10.0,
        inclinometer=orientation,
    )
    logger.log_shot(shot)

    entry = json.loads(logger.session_path.read_text().strip().splitlines()[-1])
    assert entry["type"] == "shot_detected"
    assert entry["inclinometer"] == orientation
    logger.end_session()


class FakePlacementCamera:
    def __init__(self, *, auto_exposure=True):
        self.settings = SimpleNamespace(auto_exposure=auto_exposure)
        self.reasons = []

    def recalibrate_exposure(self, reason):
        self.reasons.append(reason)
        return {"status": "ready", "exposure_us": 500, "gain": 12.0}


def _patch_placement(monkeypatch, *, camera, service):
    started = []

    class FakeMonitor:
        def __init__(self, source, on_settled, *, settle_s):
            self.source = source
            self.on_settled = on_settled
            self.settle_s = settle_s

        def start(self):
            started.append(self)

        def stop(self):
            pass

    monkeypatch.setattr("openflight.inclinometer.PlacementMonitor", FakeMonitor)
    monkeypatch.setattr(server, "camera_capture_runtime", camera)
    monkeypatch.setattr(server, "inclinometer_service", service)
    monkeypatch.setattr(server, "camera_capture_config", {"enabled": True})
    monkeypatch.setattr(server, "placement_monitor", None)
    return started


def test_placement_recalibration_wires_inclinometer_to_camera(monkeypatch):
    camera = FakePlacementCamera()
    service = SimpleNamespace(history_seconds=15.0)
    started = _patch_placement(monkeypatch, camera=camera, service=service)

    assert server.init_camera_placement_recalibration(settle_s=5.0) is True

    (monitor,) = started
    assert monitor.source is service
    assert monitor.settle_s == 5.0
    assert server.placement_monitor is monitor
    assert server.camera_capture_config["placement_recalibration_settle_s"] == 5.0
    monitor.on_settled("startup")
    monitor.on_settled("moved")
    assert camera.reasons == ["placement_startup", "placement_moved"]


@pytest.mark.parametrize(
    ("camera", "service", "settle_s"),
    [
        (FakePlacementCamera(), SimpleNamespace(history_seconds=15.0), 0.0),
        (FakePlacementCamera(), SimpleNamespace(history_seconds=15.0), -1.0),
        (None, SimpleNamespace(history_seconds=15.0), 5.0),
        (FakePlacementCamera(), None, 5.0),
        (FakePlacementCamera(auto_exposure=False), SimpleNamespace(history_seconds=15.0), 5.0),
        (FakePlacementCamera(), SimpleNamespace(history_seconds=15.0), 15.0),
    ],
    ids=["disabled", "negative", "no-camera", "no-inclinometer", "manual-exposure", "too-long"],
)
def test_placement_recalibration_is_skipped_when_not_applicable(
    monkeypatch, camera, service, settle_s
):
    started = _patch_placement(monkeypatch, camera=camera, service=service)

    assert server.init_camera_placement_recalibration(settle_s=settle_s) is False

    assert started == []
    assert server.placement_monitor is None


def test_camera_recalibrate_settle_defaults_to_five_seconds(monkeypatch):
    captured = {}

    def stop_after_parse(self, args=None, namespace=None):
        parsed = original(self, args, namespace)
        captured["args"] = parsed
        raise SystemExit(0)

    import argparse

    original = argparse.ArgumentParser.parse_args
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", stop_after_parse)
    monkeypatch.setattr(sys, "argv", ["openflight-server"])

    with pytest.raises(SystemExit):
        server.main()

    assert captured["args"].camera_recalibrate_settle_s == 5.0


def test_shutdown_stops_placement_monitor_before_inclinometer(monkeypatch):
    order = []
    monkeypatch.setattr(server, "shutdown_cleanup_started", False)
    monkeypatch.setattr(
        server, "placement_monitor", SimpleNamespace(stop=lambda: order.append("placement"))
    )
    monkeypatch.setattr(
        server, "inclinometer_service", SimpleNamespace(stop=lambda: order.append("inclinometer"))
    )
    for name in ("kld7_vertical", "kld7_horizontal", "iwr6843_runtime", "power_monitor"):
        monkeypatch.setattr(server, name, None)
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "stop_monitor", lambda: None)

    server._cleanup_hardware_for_shutdown()

    assert order[:2] == ["placement", "inclinometer"]
