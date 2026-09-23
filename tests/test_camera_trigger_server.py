"""Server wiring for the camera ball-at-address trigger."""

import argparse
import sys

import pytest

from openflight import server as server_module
from openflight.camera.address_trigger import AddressTriggerConfig, BallCandidate
from openflight.launch_monitor import Shot


def args_for(**overrides) -> argparse.Namespace:
    values = {
        "trigger": "sound",
        "camera_trigger_shadow": False,
        "camera_trigger_gone_frames": 9,
        "camera_trigger_departure_ms": 20.0,
        "camera_trigger_no_require_address": False,
        "sound_pre_trigger": None,
        "mock": False,
        "swing_speed": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class FakeRuntime:
    def __init__(self):
        self.observers = []
        self.notified = []

    def add_frame_observer(self, observer):
        self.observers.append(observer)

    def notify_trigger(self, timestamp=None):
        self.notified.append(timestamp)
        return True


@pytest.fixture
def clean_camera_globals(monkeypatch):
    for name, value in {
        "camera_capture_runtime": None,
        "camera_address_monitor": None,
        "camera_trigger_config": {"enabled": False},
        "camera_shadow_comparator": None,
        "iwr6843_runtime": None,
    }.items():
        monkeypatch.setattr(server_module, name, value)
    monkeypatch.setattr(server_module, "log_session_error", lambda *_a, **_k: None)
    yield
    if server_module.camera_address_monitor is not None:
        server_module.camera_address_monitor.stop()


# ------------------------------------------------------------- CLI guards


class TestCliValidation:
    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"trigger": "camera", "mock": True}, "cannot be used with --mock"),
            ({"trigger": "camera", "swing_speed": True}, "cannot be used with --mock"),
            ({"camera_trigger_shadow": True, "mock": True}, "cannot be used with --mock"),
            ({"trigger": "camera", "camera_trigger_shadow": True}, "drop one"),
            ({"trigger": "speed", "camera_trigger_shadow": True}, "requires --trigger sound"),
            ({"camera_trigger_gone_frames": 0}, "at least 1"),
            ({"camera_trigger_departure_ms": 0.0}, "must be positive"),
            ({"sound_pre_trigger": 33}, "between 0 and 32"),
            ({"sound_pre_trigger": -1}, "between 0 and 32"),
        ],
    )
    def test_invalid_combinations(self, overrides, message):
        assert message in server_module._camera_trigger_arg_error(args_for(**overrides))

    @pytest.mark.parametrize(
        "overrides",
        [
            {},
            {"trigger": "camera"},
            {"camera_trigger_shadow": True},
            {"trigger": "camera", "sound_pre_trigger": 32},
            {"sound_pre_trigger": 0},
        ],
    )
    def test_valid_combinations(self, overrides):
        assert server_module._camera_trigger_arg_error(args_for(**overrides)) is None

    def test_main_rejects_camera_trigger_with_mock(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["openflight-server", "--trigger", "camera", "--mock"])
        with pytest.raises(SystemExit) as exc_info:
            server_module.main()
        assert exc_info.value.code == 2
        assert "camera trigger cannot be used with --mock" in capsys.readouterr().err

    def test_main_rejects_shadow_with_camera_trigger(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sys,
            "argv",
            ["openflight-server", "--trigger", "camera", "--camera-trigger-shadow"],
        )
        with pytest.raises(SystemExit) as exc_info:
            server_module.main()
        assert exc_info.value.code == 2
        assert "drop one" in capsys.readouterr().err


class TestPreTriggerResolution:
    def test_sound_default_is_balanced(self):
        assert server_module._resolve_pre_trigger_segments(args_for()) == 16

    def test_camera_default_is_pre_heavy(self):
        assert server_module._resolve_pre_trigger_segments(args_for(trigger="camera")) == 28

    @pytest.mark.parametrize("trigger", ["sound", "camera", "speed"])
    def test_explicit_value_wins(self, trigger):
        resolved = server_module._resolve_pre_trigger_segments(
            args_for(trigger=trigger, sound_pre_trigger=20)
        )
        assert resolved == 20


def test_config_from_args():
    config = server_module._camera_trigger_config_from_args(
        args_for(
            camera_trigger_gone_frames=6,
            camera_trigger_departure_ms=15.0,
            camera_trigger_no_require_address=True,
        )
    )
    assert isinstance(config, AddressTriggerConfig)
    assert config.gone_frames == 6
    assert config.max_departure_ms == 15.0
    assert config.require_address is False


# ---------------------------------------------------------- initialization


class TestInitCameraAddressTrigger:
    def test_requires_running_camera(self, clean_camera_globals):
        assert not server_module.init_camera_address_trigger(
            config=AddressTriggerConfig(), shadow=False, acquire_fn=lambda _i: None
        )
        assert server_module.camera_address_monitor is None
        assert "not running" in server_module.camera_trigger_config["error"]

    def test_active_mode_registers_frame_observer(self, clean_camera_globals, monkeypatch):
        runtime = FakeRuntime()
        monkeypatch.setattr(server_module, "camera_capture_runtime", runtime)
        assert server_module.init_camera_address_trigger(
            config=AddressTriggerConfig(), shadow=False, acquire_fn=lambda _i: None
        )
        monitor = server_module.camera_address_monitor
        assert runtime.observers == [monitor.on_frame]
        assert server_module.camera_shadow_comparator is None
        assert server_module.camera_trigger_config["mode"] == "active"

    def test_shadow_mode_creates_comparator(self, clean_camera_globals, monkeypatch):
        monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime())
        assert server_module.init_camera_address_trigger(
            config=AddressTriggerConfig(), shadow=True, acquire_fn=lambda _i: None
        )
        assert server_module.camera_shadow_comparator is not None
        assert server_module.camera_trigger_config["mode"] == "shadow"

    def test_default_acquirer_failure_is_reported(self, clean_camera_globals, monkeypatch):
        monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime())

        def no_cv2():
            raise RuntimeError("OpenCV not available")

        monkeypatch.setattr(
            "openflight.camera.address_monitor.ball_detector_acquirer", lambda *_a: no_cv2()
        )
        assert not server_module.init_camera_address_trigger(
            config=AddressTriggerConfig(), shadow=False
        )
        assert "OpenCV" in server_module.camera_trigger_config["error"]


class TestObserversAndStatus:
    def test_observers_prefer_iwr_fanout(self, clean_camera_globals, monkeypatch):
        class FakeCaptureMonitor:
            def notify_trigger(self, timestamp=None):
                return True

        class FakeIwr:
            capture_monitor = FakeCaptureMonitor()

        iwr = FakeIwr()
        monkeypatch.setattr(server_module, "iwr6843_runtime", iwr)
        monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime())
        observers = server_module.camera_trigger_observers()
        assert observers == [iwr.capture_monitor.notify_trigger]

    def test_observers_fall_back_to_camera_clip(self, clean_camera_globals, monkeypatch):
        runtime = FakeRuntime()
        monkeypatch.setattr(server_module, "camera_capture_runtime", runtime)
        assert server_module.camera_trigger_observers() == [runtime.notify_trigger]

    def test_no_observers_without_sensors(self, clean_camera_globals):
        assert server_module.camera_trigger_observers() == []

    def test_trigger_status_without_camera(self, clean_camera_globals):
        assert server_module._get_trigger_status()["camera_trigger"] is None

    def test_trigger_status_reports_camera_state(self, clean_camera_globals, monkeypatch):
        monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime())
        server_module.init_camera_address_trigger(
            config=AddressTriggerConfig(), shadow=True, acquire_fn=lambda _i: None
        )
        status = server_module._get_trigger_status()["camera_trigger"]
        assert status["mode"] == "shadow"
        assert status["state"] == "idle"
        assert status["armed"] is False  # no frames yet
        assert status["shadow"]["matched"] == 0


class TestShadowShotHook:
    def test_shot_is_paired_in_shadow_mode(self, clean_camera_globals, monkeypatch):
        seen = []

        class Comparator:
            def on_sound_shot(self, impact_epoch):
                seen.append(impact_epoch)

        monkeypatch.setattr(server_module, "camera_shadow_comparator", Comparator())
        shot = Shot(ball_speed_mph=100.0, timestamp=None, impact_timestamp=1234.5)
        server_module._notify_camera_shadow(shot)
        assert seen == [1234.5]

    def test_noop_without_shadow(self, clean_camera_globals):
        server_module._notify_camera_shadow(
            Shot(ball_speed_mph=100.0, timestamp=None, impact_timestamp=1.0)
        )

    def test_comparator_error_is_contained(self, clean_camera_globals, monkeypatch, caplog):
        class Broken:
            def on_sound_shot(self, _impact):
                raise RuntimeError("boom")

        monkeypatch.setattr(server_module, "camera_shadow_comparator", Broken())
        server_module._notify_camera_shadow(
            Shot(ball_speed_mph=100.0, timestamp=None, impact_timestamp=1.0)
        )
        assert "Camera shadow comparison failed" in caplog.text

    def test_shot_handler_calls_shadow_hook_first(self, clean_camera_globals, monkeypatch):
        class Stop(Exception):
            pass

        calls = []
        monkeypatch.setattr(server_module, "_assign_shot_number", lambda s: calls.append("n"))

        def hook(_shot):
            calls.append("shadow")
            raise Stop

        monkeypatch.setattr(server_module, "_notify_camera_shadow", hook)
        with pytest.raises(Stop):
            server_module._handle_shot_detected(
                Shot(ball_speed_mph=100.0, timestamp=None, impact_timestamp=1.0)
            )
        assert calls == ["n", "shadow"]


def test_camera_candidate_type_is_exported():
    assert BallCandidate(1, 2, 3).radius == 3
