"""Tests for live putting: putter selection and camera-only putt shots in the server."""

from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server as server_module
from openflight.camera import ball_flight
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.rolling_buffer.monitor import RejectedTrigger
from openflight.server import (
    PUTT_SHOT_MODE,
    _build_putt_shot,
    _has_slow_shot_enrichment,
    _is_camera_putt,
    _measure_putt,
    _process_putt_trigger,
    handle_set_club,
    on_rejected_trigger,
    on_shot_detected,
)

TRIGGER_EPOCH = 1_700_000_000.25


def _wait_for_shot_finalization_idle(timeout_s: float = 2.0) -> None:
    with server_module._shot_finalization_condition:
        idle = server_module._shot_finalization_condition.wait_for(
            lambda: (
                not server_module._shot_finalization_order
                and not server_module._shot_finalization_running
            ),
            timeout=timeout_s,
        )
    assert idle, "shot finalization coordinator did not become idle"


def _rejected(club=ClubType.PUTTER, trigger_timestamp=TRIGGER_EPOCH, reason="processing_failed"):
    return RejectedTrigger(
        reason=reason,
        trigger_timestamp=trigger_timestamp,
        ball_speed_mph=None,
        club=club,
    )


def _estimate(**overrides):
    fields = {
        "status": "accepted_camera_only",
        "confidence_tier": "experimental",
        "horizontal_deg": 1.8,
        "vertical_deg": 0.2,
        "support": 12,
        "speed_mph": 6.4,
        "n_points": 30,
        "depth_source": "camera_size",
        "search_profile": "putt",
    }
    fields.update(overrides)
    return ball_flight.CameraBallEstimate(**fields)


class FakeCameraRuntime:
    def __init__(self, capture):
        self.capture = capture
        self.requests = []

    def capture_for_shot(self, impact_timestamp, timeout_s):
        self.requests.append((impact_timestamp, timeout_s))
        return self.capture


class FakeMonitor:
    def __init__(self):
        self._current_club = ClubType.PUTTER
        self.recorded = []

    def set_club(self, club):
        self._current_club = club

    def record_external_shot(self, shot):
        shot.shot_number = len(self.recorded) + 1
        self.recorded.append(shot)
        return shot

    @staticmethod
    def get_session_stats():
        return {"shot_count": 1}


class RecordingSessionLog:
    def __init__(self):
        self.stats = {"shots_detected": 0}
        self.shots = []
        self.camera_captures = []

    def log_shot(self, shot, pipeline_ms=None):
        del pipeline_ms
        self.stats["shots_detected"] += 1
        self.shots.append(shot.to_dict())

    def log_camera_capture(self, **capture_data):
        self.camera_captures.append(capture_data)


@pytest.fixture
def camera_config(monkeypatch):
    config = {
        "enabled": True,
        "mount_height_m": 0.20955,
        "lateral_offset_m": 0.0,
        "horizontal_offset_deg": 0.0,
        "roll_correction_deg": 0.0,
        "mirror_horizontal": False,
        "width": 640,
        "height": 400,
        "ball_forward_m": 1.5,
    }
    monkeypatch.setattr(server_module, "camera_capture_config", config)
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "camera_replay_manager", None)
    monkeypatch.setattr(server_module, "camera_ball_flight_reference_tracker", None)
    return config


@pytest.fixture
def saved_capture(tmp_path):
    np.savez(
        tmp_path / "frames.npz",
        frames=np.zeros((8, 4, 4), dtype=np.uint8),
        host_timestamp_ns=np.arange(8, dtype=np.int64) * 3_333_333,
        trigger_host_timestamp_ns=np.int64(3 * 3_333_333),
    )
    return SimpleNamespace(
        valid=True,
        path=tmp_path,
        trigger_timestamp=TRIGGER_EPOCH + 0.004,
        metadata={"fps": 300},
        error=None,
        sequence=7,
    )


class TestPutterSelection:
    def test_putter_is_a_selectable_club(self, monkeypatch):
        monitor = FakeMonitor()
        monitor.set_club(ClubType.DRIVER)
        emitted = []
        monkeypatch.setattr(server_module, "monitor", monitor)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda event, payload: emitted.append((event, payload))
        )

        handle_set_club({"club": "putter"})

        assert monitor._current_club is ClubType.PUTTER
        assert emitted == [("club_changed", {"club": "putter"})]
        assert server_module._current_club_id() == "putter"


class TestOnRejectedTrigger:
    @pytest.fixture(autouse=True)
    def _background(self, monkeypatch):
        self.started = []
        monkeypatch.setattr(
            server_module.socketio,
            "start_background_task",
            lambda target, *args: self.started.append((target, args)),
        )
        monkeypatch.setattr(server_module, "camera_capture_runtime", object())

    def test_ignores_triggers_when_a_flight_club_is_selected(self):
        on_rejected_trigger(_rejected(club=ClubType.IRON_7))

        assert self.started == []

    def test_ignores_triggers_without_a_camera(self, monkeypatch):
        monkeypatch.setattr(server_module, "camera_capture_runtime", None)

        on_rejected_trigger(_rejected())

        assert self.started == []

    def test_ignores_triggers_without_a_hardware_timestamp(self):
        on_rejected_trigger(_rejected(trigger_timestamp=None))

        assert self.started == []

    @pytest.mark.parametrize("reason", ["processing_failed", "shot_validation_failed"])
    def test_putter_triggers_run_the_putt_estimator_off_the_capture_thread(self, reason):
        rejected = _rejected(reason=reason)

        on_rejected_trigger(rejected)

        assert self.started == [(_process_putt_trigger, (rejected,))]


class TestMeasurePutt:
    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch):
        monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
        monkeypatch.setattr(server_module, "monitor", None)

    def test_returns_none_when_no_capture_matches(self, monkeypatch, camera_config):
        runtime = FakeCameraRuntime(None)
        monkeypatch.setattr(server_module, "camera_capture_runtime", runtime)

        assert _measure_putt(_rejected()) is None
        assert runtime.requests == [(TRIGGER_EPOCH, server_module._PUTT_CAMERA_CAPTURE_TIMEOUT_S)]

    def test_returns_none_for_a_failed_capture(self, monkeypatch, camera_config):
        capture = SimpleNamespace(valid=False, path=None, error="dropped", sequence=3)
        monkeypatch.setattr(server_module, "camera_capture_runtime", FakeCameraRuntime(capture))

        assert _measure_putt(_rejected()) is None

    def test_returns_none_without_ball_distance_or_tee_calibration(
        self, monkeypatch, camera_config, saved_capture
    ):
        camera_config["ball_forward_m"] = None
        monkeypatch.setattr(
            server_module, "camera_capture_runtime", FakeCameraRuntime(saved_capture)
        )
        monkeypatch.setattr(
            ball_flight, "estimate_camera_putt", lambda *a, **k: pytest.fail("must not run")
        )

        assert _measure_putt(_rejected()) is None

    def test_returns_none_when_frames_are_missing(self, monkeypatch, camera_config, tmp_path):
        capture = SimpleNamespace(
            valid=True, path=tmp_path / "missing", trigger_timestamp=0.0, metadata={}, error=None
        )
        monkeypatch.setattr(server_module, "camera_capture_runtime", FakeCameraRuntime(capture))

        assert _measure_putt(_rejected()) is None

    @pytest.mark.parametrize(
        "estimate",
        [
            _estimate(status="rejected_no_stable_path", confidence_tier="withheld", speed_mph=None),
            _estimate(confidence_tier="withheld"),
            _estimate(speed_mph=None),
        ],
    )
    def test_returns_none_when_the_estimate_is_withheld(
        self, monkeypatch, camera_config, saved_capture, estimate
    ):
        monkeypatch.setattr(
            server_module, "camera_capture_runtime", FakeCameraRuntime(saved_capture)
        )
        monkeypatch.setattr(ball_flight, "estimate_camera_putt", lambda *a, **k: estimate)

        assert _measure_putt(_rejected()) is None

    def test_builds_a_putt_shot_from_the_matched_clip(
        self, monkeypatch, camera_config, saved_capture
    ):
        estimate_call = {}

        def fake_estimate(frames, timestamps_ns, **kwargs):
            estimate_call["frames_shape"] = frames.shape
            estimate_call["timestamps"] = timestamps_ns.tolist()
            estimate_call.update(kwargs)
            return _estimate()

        monitor = FakeMonitor()
        session_log = RecordingSessionLog()
        tracker = object()
        monkeypatch.setattr(server_module, "monitor", monitor)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: session_log)
        monkeypatch.setattr(server_module, "camera_ball_flight_reference_tracker", tracker)
        monkeypatch.setattr(
            server_module, "camera_capture_runtime", FakeCameraRuntime(saved_capture)
        )
        monkeypatch.setattr(ball_flight, "estimate_camera_putt", fake_estimate)

        shot = _measure_putt(_rejected())

        assert estimate_call["frames_shape"] == (8, 4, 4)
        assert estimate_call["trigger_ns"] == 3 * 3_333_333
        assert estimate_call["geometry"].ball_forward_m == pytest.approx(1.5)
        assert estimate_call["geometry"].radar_height_m == pytest.approx(0.20955)
        assert estimate_call["ball_tracker"] is tracker

        assert shot is not None
        assert shot.mode == PUTT_SHOT_MODE
        assert shot.club is ClubType.PUTTER
        assert shot.ball_speed_mph == pytest.approx(6.4)
        assert shot.impact_timestamp == TRIGGER_EPOCH
        assert shot.launch_angle_horizontal == pytest.approx(1.8)
        assert shot.launch_angle_horizontal_source == "camera_only_experimental"
        assert shot.launch_angle_horizontal_confidence == pytest.approx(0.30)
        assert shot.experimental_camera_horizontal_status == "camera_only_experimental"
        assert shot.launch_angle_vertical == 0.0
        assert shot.launch_angle_vertical_source == "assumed"
        assert shot.carry_spin_adjusted == 0.0
        assert shot.spin_rpm is None
        assert monitor.recorded == [shot]
        assert shot.shot_number == 1
        assert session_log.camera_captures == [
            {
                "shot_number": 1,
                "shot_timestamp": TRIGGER_EPOCH,
                "trigger_timestamp": saved_capture.trigger_timestamp,
                "capture_path": str(saved_capture.path),
                "metadata": {"fps": 300},
                "capture_error": None,
            }
        ]

    def test_rendered_putt_clip_yields_speed_and_start_line(
        self, monkeypatch, camera_config, tmp_path
    ):
        """End to end: real frames through the real estimator and server geometry."""
        from tests.test_camera_putt import _putt_geometry, _render_putt_capture

        frames, timestamps_ns, trigger_ns = _render_putt_capture(
            speed_mph=6.0, start_line_deg=2.0, geometry=_putt_geometry()
        )
        np.savez(
            tmp_path / "frames.npz",
            frames=frames,
            host_timestamp_ns=timestamps_ns,
            trigger_host_timestamp_ns=np.int64(trigger_ns),
        )
        capture = SimpleNamespace(
            valid=True,
            path=tmp_path,
            trigger_timestamp=TRIGGER_EPOCH,
            metadata={},
            error=None,
            sequence=1,
        )
        monkeypatch.setattr(server_module, "camera_capture_runtime", FakeCameraRuntime(capture))

        shot = _measure_putt(_rejected())

        assert shot is not None
        assert shot.ball_speed_mph == pytest.approx(6.0, rel=0.1)
        assert shot.launch_angle_horizontal == pytest.approx(2.0, abs=1.0)


class TestProcessPuttTrigger:
    def test_reports_processing_then_publishes_the_putt(self, monkeypatch):
        states = []
        published = []
        putt = _build_putt_shot(_estimate(), TRIGGER_EPOCH)
        monkeypatch.setattr(server_module, "on_shot_processing", states.append)
        monkeypatch.setattr(server_module, "_measure_putt", lambda rejected: putt)
        monkeypatch.setattr(server_module, "on_shot_detected", published.append)

        _process_putt_trigger(_rejected())

        assert states == ["calculating"]
        assert published == [putt]

    def test_reports_failure_when_nothing_was_measured(self, monkeypatch):
        states = []
        published = []
        monkeypatch.setattr(server_module, "on_shot_processing", states.append)
        monkeypatch.setattr(server_module, "_measure_putt", lambda rejected: None)
        monkeypatch.setattr(server_module, "on_shot_detected", published.append)

        _process_putt_trigger(_rejected())

        assert states == ["calculating", "failed"]
        assert published == []

    def test_estimator_errors_are_contained(self, monkeypatch):
        states = []
        errors = []

        def explode(rejected):
            raise RuntimeError("bad frames")

        monkeypatch.setattr(server_module, "on_shot_processing", states.append)
        monkeypatch.setattr(server_module, "_measure_putt", explode)
        monkeypatch.setattr(server_module, "on_shot_detected", lambda shot: pytest.fail("no shot"))
        monkeypatch.setattr(
            server_module, "log_session_error", lambda *args, **kwargs: errors.append(kwargs)
        )

        _process_putt_trigger(_rejected())

        assert states == ["calculating", "failed"]
        assert errors[0]["context"]["stage"] == "putt"


class TestPuttShotPipeline:
    @pytest.fixture(autouse=True)
    def _reset_pipeline(self):
        server_module._reset_shot_sequence()
        yield
        _wait_for_shot_finalization_idle()

    @pytest.fixture
    def pipeline(self, monkeypatch, camera_config):
        emitted = []
        session_log = RecordingSessionLog()
        forwarded = []
        monkeypatch.setattr(server_module, "monitor", FakeMonitor())
        monkeypatch.setattr(server_module, "kld7_vertical", None)
        monkeypatch.setattr(server_module, "kld7_horizontal", None)
        monkeypatch.setattr(server_module, "camera_capture_runtime", object())
        monkeypatch.setattr(server_module, "ball_speed_correction_enabled", True)
        monkeypatch.setattr(server_module, "calculated_spin_enabled", True)
        monkeypatch.setattr(server_module, "ballistics_enabled", True)
        monkeypatch.setattr(server_module, "debug_mode", False)
        monkeypatch.setattr(server_module, "sim_connectors", [])
        monkeypatch.setattr(server_module, "get_session_logger", lambda: session_log)
        monkeypatch.setattr(server_module, "_forward_shot_to_simulators", forwarded.append)
        monkeypatch.setattr(
            server_module,
            "_enrich_shot_from_optional_hardware",
            lambda shot: pytest.fail("putts must not re-run radar or camera enrichment"),
        )
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload: emitted.append((event, payload)),
        )
        return SimpleNamespace(emitted=emitted, session_log=session_log, forwarded=forwarded)

    def test_is_camera_putt_reads_the_shot_mode(self):
        assert _is_camera_putt(_build_putt_shot(_estimate(), TRIGGER_EPOCH))
        assert not _is_camera_putt(Shot(ball_speed_mph=120.0, timestamp=datetime.now()))

    def test_putts_never_wait_on_slow_hardware(self, monkeypatch):
        monkeypatch.setattr(server_module, "camera_capture_runtime", object())
        monkeypatch.setattr(server_module, "iwr6843_runtime", object())

        assert not _has_slow_shot_enrichment(_build_putt_shot(_estimate(), TRIGGER_EPOCH))
        assert _has_slow_shot_enrichment(Shot(ball_speed_mph=120.0, timestamp=datetime.now()))

    def test_putt_is_published_once_with_camera_values_untouched(self, pipeline):
        putt = _build_putt_shot(_estimate(), TRIGGER_EPOCH)
        putt.shot_number = 1

        on_shot_detected(putt)
        _wait_for_shot_finalization_idle()

        events = [event for event, _payload in pipeline.emitted]
        assert events == ["shot"]
        payload = pipeline.emitted[0][1]
        assert "pending" not in payload
        shot_data = payload["shot"]
        assert shot_data["club"] == "putter"
        assert shot_data["ball_speed_mph"] == pytest.approx(6.4)
        assert shot_data["ball_speed_raw_mph"] is None, "no radial cosine correction for putts"
        assert shot_data["launch_angle_horizontal"] == pytest.approx(1.8)
        assert shot_data["launch_angle_horizontal_source"] == "camera_only_experimental"
        assert shot_data["launch_angle_vertical"] == 0.0
        assert shot_data["launch_angle_vertical_source"] == "assumed"
        assert shot_data["spin_rpm"] is None
        assert shot_data["carry_spin_adjusted"] == 0.0
        assert pipeline.forwarded == [putt]
        assert len(pipeline.session_log.shots) == 1
        assert pipeline.session_log.shots[0]["mode"] == PUTT_SHOT_MODE
        assert pipeline.session_log.shots[0]["club"] == "putter"

    def test_flight_shots_still_get_flight_physics(self, pipeline, monkeypatch):
        monkeypatch.setattr(server_module, "camera_capture_runtime", None)
        monkeypatch.setattr(server_module, "_enrich_shot_from_optional_hardware", lambda shot: None)
        shot = Shot(
            ball_speed_mph=120.0,
            timestamp=datetime.now(),
            impact_timestamp=TRIGGER_EPOCH,
            club=ClubType.IRON_7,
        )

        on_shot_detected(shot)
        _wait_for_shot_finalization_idle()

        shot_data = pipeline.emitted[0][1]["shot"]
        assert shot_data["launch_angle_vertical_source"] == "estimated"
        assert shot_data["carry_spin_adjusted"] > 0
