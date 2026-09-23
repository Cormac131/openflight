"""Tests for the camera-driven rolling-buffer trigger and camera impact timing."""

import json
import logging
import time

import pytest
from spin_synth import synth_capture

from openflight.camera.address_monitor import CameraTriggerEvent
from openflight.camera.address_trigger import BallCandidate
from openflight.rolling_buffer import RollingBufferMonitor
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import (
    TRIGGER_TYPES,
    CameraTrigger,
    SoundTrigger,
    SpeedTriggeredCapture,
    create_trigger,
)
from openflight.rolling_buffer.types import (
    ImpactEstimate,
    IQCapture,
    ProcessedCapture,
    SpeedTimeline,
)


def _dump_response(i_samples, q_samples) -> str:
    return "\n".join(
        [
            '{"sample_time": "964.003"}',
            '{"trigger_time": "964.105"}',
            json.dumps({"I": [int(v) for v in i_samples]}),
            json.dumps({"Q": [int(v) for v in q_samples]}),
        ]
    )


def swing_response() -> str:
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    return _dump_response(i_samples, q_samples)


def silent_response() -> str:
    i_samples, q_samples = synth_capture(rpm=3000, amplitude=0.0, noise_rms=1.0)
    return _dump_response(i_samples, q_samples)


def make_event(age_ms: float = 30.0, confirm_ms: float = 28.0) -> CameraTriggerEvent:
    now = time.time()
    return CameraTriggerEvent(
        impact_epoch=now - age_ms / 1000.0,
        impact_uncertainty_ms=1.7,
        confirmed_epoch=now - (age_ms - confirm_ms) / 1000.0,
        ball=BallCandidate(320.0, 240.0, 9.0),
        addressed=True,
    )


class FakeAddressMonitor:
    def __init__(self, calls, event=None):
        self.calls = calls
        self.event = event
        self.wait_kwargs = None

    def wait_for_trigger(self, timeout, cancel_event=None):
        self.wait_kwargs = {"timeout": timeout, "cancel_event": cancel_event}
        self.calls.append("wait")
        return self.event

    def rearm(self):
        self.calls.append("monitor_rearm")


class SoftwareRadar:
    """Mock radar for the S! path, recording serial-touching calls in order."""

    def __init__(self, calls, response: str):
        self.calls = calls
        self.response = response
        self.last_clock_sync = None
        self.last_software_trigger_write_timestamp = None
        self.last_software_trigger_first_byte_timestamp = None
        self.on_first_byte = None

    def trigger_capture(self, timeout=None, on_first_byte=None):
        self.calls.append("s_bang")
        self.on_first_byte = on_first_byte
        self.last_software_trigger_write_timestamp = time.time()
        if self.response:
            self.last_software_trigger_first_byte_timestamp = time.time() + 0.02
            if on_first_byte:
                on_first_byte()
        return self.response

    def wait_for_hardware_trigger(self, *args, **kwargs):  # pragma: no cover - must not be used
        raise AssertionError("camera trigger must not wait on HOST_INT")

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.calls.append(f"rearm:{pre_trigger_segments}")

    def read_clock_sync(self, samples=7, store=True, **kwargs):
        self.calls.append("clock_sync")
        return {"clock_sync_method": "no_valid_reads", "valid_samples": 0}


def build(response: str, event=None, **trigger_kwargs):
    calls = []
    monitor = FakeAddressMonitor(calls, event if event is not None else make_event())
    radar = SoftwareRadar(calls, response)
    trigger = CameraTrigger(address_monitor=monitor, **trigger_kwargs)
    trigger.add_trigger_observer(lambda epoch: calls.append(("observer", epoch)))
    return trigger, radar, monitor, calls


def run(trigger, radar, **kwargs):
    return trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0, **kwargs)


# ------------------------------------------------------------ registration


class TestRegistration:
    def test_factory_creates_camera_trigger_with_pre_heavy_default(self):
        trigger = create_trigger("camera")
        assert isinstance(trigger, CameraTrigger)
        assert trigger.pre_trigger_segments == 28
        assert trigger.pre_trigger_window_ms == pytest.approx(28 * 128 / 30.0)

    def test_registry_lists_all_types(self):
        assert set(TRIGGER_TYPES) == {"sound", "speed", "camera"}

    @pytest.mark.parametrize(
        ("cls", "cancel", "started", "persisted"),
        [
            (SoundTrigger, True, True, True),
            (CameraTrigger, True, True, True),
            (SpeedTriggeredCapture, False, False, False),
        ],
    )
    def test_capabilities(self, cls, cancel, started, persisted):
        assert cls.supports_cancel is cancel
        assert cls.emits_capture_started is started
        assert cls.uses_persisted_rolling_buffer is persisted

    def test_monitor_passes_cancel_and_started_for_camera(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="camera")
        assert isinstance(monitor.trigger, CameraTrigger)
        assert monitor._trigger_capability("supports_cancel")  # pylint: disable=protected-access
        assert monitor._trigger_capability("emits_capture_started")  # pylint: disable=protected-access

    def test_monitor_capture_loop_forwards_kwargs_to_camera_trigger(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="camera")
        received = {}

        class Recorder(CameraTrigger):
            def wait_for_trigger(self, radar, processor, timeout=30.0, **kwargs):
                received.update(kwargs)
                monitor._running = False  # pylint: disable=protected-access
                return None

        monitor.trigger = Recorder()
        monitor._running = True  # pylint: disable=protected-access
        monitor._capture_loop()  # pylint: disable=protected-access
        assert received["cancel_event"] is monitor._stop_event  # pylint: disable=protected-access
        assert callable(received["capture_started_callback"])

    def test_speed_trigger_skips_persisted_prepare(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="speed")
        assert not monitor._trigger_capability("uses_persisted_rolling_buffer")  # pylint: disable=protected-access


# --------------------------------------------------------------- ordering


class TestCameraTriggerFlow:
    def test_accepted_capture_ordering(self):
        trigger, radar, _monitor, calls = build(swing_response())
        capture = run(trigger, radar)
        assert capture is not None
        kinds = [c if isinstance(c, str) else c[0] for c in calls]
        assert kinds == ["wait", "observer", "s_bang", "clock_sync", "rearm:28", "monitor_rearm"]

    def test_s_bang_sent_exactly_once(self):
        trigger, radar, _monitor, calls = build(swing_response())
        run(trigger, radar)
        assert calls.count("s_bang") == 1

    def test_capture_carries_camera_impact_and_timestamps(self):
        event = make_event()
        trigger, radar, _monitor, _calls = build(swing_response(), event=event)
        capture = run(trigger, radar)
        assert capture.camera_impact_epoch == pytest.approx(event.impact_epoch)
        assert capture.first_byte_timestamp == pytest.approx(
            radar.last_software_trigger_first_byte_timestamp
        )
        assert capture.trigger_timestamp is not None

    def test_observers_receive_impact_epoch(self):
        event = make_event()
        trigger, radar, _monitor, calls = build(swing_response(), event=event)
        run(trigger, radar)
        observed = [c for c in calls if isinstance(c, tuple)]
        assert observed == [("observer", event.impact_epoch)]

    def test_failing_observer_does_not_block_s_bang(self, caplog):
        trigger, radar, _monitor, calls = build(swing_response())

        def broken(_epoch):
            raise RuntimeError("iwr busy")

        trigger._trigger_observers.insert(0, broken)  # pylint: disable=protected-access
        assert run(trigger, radar) is not None
        assert "s_bang" in calls
        assert "observer failed" in caplog.text

    def test_capture_started_callback_forwarded(self):
        trigger, radar, _monitor, _calls = build(swing_response())
        started = []
        run(trigger, radar, capture_started_callback=lambda: started.append(True))
        assert started == [True]

    def test_cancel_event_forwarded_to_monitor(self):
        import threading  # pylint: disable=import-outside-toplevel

        trigger, radar, monitor, _calls = build(swing_response())
        cancel = threading.Event()
        run(trigger, radar, cancel_event=cancel)
        assert monitor.wait_kwargs["cancel_event"] is cancel

    def test_accepted_diagnostic_has_camera_latency(self):
        trigger, radar, _monitor, _calls = build(swing_response(), event=make_event(age_ms=30))
        run(trigger, radar)
        diagnostics = trigger.drain_diagnostics()
        assert len(diagnostics) == 1
        diag = diagnostics[0]
        assert diag["accepted"] is True
        camera = diag["camera"]
        assert camera["impact_to_s_bang_ms"] == pytest.approx(30.0, abs=15.0)
        assert camera["pre_trigger_window_ms"] == pytest.approx(119.467, abs=0.01)
        assert camera["addressed"] is True
        assert diag["trigger_latency_ms"] == pytest.approx(camera["impact_to_s_bang_ms"], abs=1e-3)

    def test_rejected_capture_rearms_without_clock_sync(self):
        trigger, radar, _monitor, calls = build(silent_response())
        assert run(trigger, radar) is None
        assert "clock_sync" not in calls
        assert calls[-2:] == ["rearm:28", "monitor_rearm"]
        diag = trigger.drain_diagnostics()[0]
        assert diag["reason"] == "no_outbound_speed"
        assert "camera" in diag

    def test_empty_dump_is_parse_failure_and_rearms(self):
        trigger, radar, _monitor, calls = build("")
        assert run(trigger, radar) is None
        assert calls[-2:] == ["rearm:28", "monitor_rearm"]
        assert trigger.drain_diagnostics()[0]["reason"] == "parse_failed"

    def test_timeout_sends_nothing(self):
        calls = []
        monitor = FakeAddressMonitor(calls, None)
        radar = SoftwareRadar(calls, swing_response())
        trigger = CameraTrigger(address_monitor=monitor)
        assert run(trigger, radar) is None
        assert calls == ["wait"]

    def test_stale_event_is_dropped_without_s_bang(self):
        trigger, radar, _monitor, calls = build(
            swing_response(), event=make_event(age_ms=200.0, confirm_ms=28.0)
        )
        assert run(trigger, radar) is None
        assert "s_bang" not in calls
        assert not any(c.startswith("rearm") for c in calls if isinstance(c, str))
        assert calls[-1] == "monitor_rearm"
        diag = trigger.drain_diagnostics()[0]
        assert diag["reason"] == "stale_camera_trigger"
        assert diag["accepted"] is False

    def test_slow_trigger_logs_latency_warning(self, caplog):
        caplog.set_level(logging.WARNING)
        trigger, radar, _monitor, _calls = build(swing_response(), event=make_event(age_ms=95.0))
        run(trigger, radar)
        assert "uses >70%" in caplog.text

    def test_fast_trigger_does_not_warn(self, caplog):
        caplog.set_level(logging.WARNING)
        trigger, radar, _monitor, _calls = build(swing_response(), event=make_event(age_ms=30.0))
        run(trigger, radar)
        assert "uses >70%" not in caplog.text

    def test_monitor_rearmed_even_when_capture_raises(self):
        trigger, radar, _monitor, calls = build(swing_response())

        def exploding(**_kwargs):
            raise ConnectionError("radar unplugged")

        radar.trigger_capture = exploding
        with pytest.raises(ConnectionError):
            run(trigger, radar)
        assert calls[-1] == "monitor_rearm"

    def test_missing_monitor_fails_loudly(self):
        trigger = CameraTrigger()
        with pytest.raises(RuntimeError, match="no camera address monitor"):
            run(trigger, SoftwareRadar([], swing_response()))

    def test_attach_monitor_later(self):
        calls = []
        trigger = CameraTrigger()
        trigger.attach_address_monitor(FakeAddressMonitor(calls, None))
        assert run(trigger, SoftwareRadar(calls, "")) is None

    def test_reset_rearms_monitor(self):
        calls = []
        trigger = CameraTrigger(address_monitor=FakeAddressMonitor(calls))
        trigger.reset()
        assert calls == ["monitor_rearm"]
        CameraTrigger().reset()  # no monitor: no error


# ------------------------------------------------------------ impact timing


def _capture(**kwargs) -> IQCapture:
    return IQCapture(
        sample_time=100.000,
        trigger_time=100.119,
        i_samples=[0] * 4096,
        q_samples=[0] * 4096,
        **kwargs,
    )


class TestCameraImpactTiming:
    def test_camera_impact_offset(self):
        capture = _capture(trigger_timestamp=1000.0, camera_impact_epoch=999.970)
        assert capture.camera_impact_offset_ms == pytest.approx(119.0 - 30.0)

    def test_camera_offset_requires_both_epochs(self):
        assert _capture(camera_impact_epoch=999.97).camera_impact_offset_ms is None
        assert _capture(trigger_timestamp=1000.0).camera_impact_offset_ms is None

    def test_estimate_impact_falls_back_to_camera(self):
        processor = RollingBufferProcessor()
        capture = _capture(trigger_timestamp=1000.0, camera_impact_epoch=999.970)
        timeline = SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=capture)
        estimate = processor.estimate_impact(timeline, 100.0, capture=capture)
        assert estimate.source == "camera_trigger"
        assert estimate.timestamp_ms == pytest.approx(89.0)

    def test_estimate_impact_sound_fallback_unchanged(self):
        processor = RollingBufferProcessor()
        capture = _capture(trigger_timestamp=1000.0)
        timeline = SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=capture)
        estimate = processor.estimate_impact(timeline, 100.0, capture=capture)
        assert estimate.source == "sound_trigger"
        assert estimate.timestamp_ms == pytest.approx(119.0)

    def test_estimate_impact_without_capture(self):
        processor = RollingBufferProcessor()
        timeline = SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=None)
        estimate = processor.estimate_impact(timeline, 100.0, capture=None)
        assert estimate.source == "unavailable"
        assert estimate.timestamp_ms is None

    def test_shot_impact_timestamps_use_camera_epoch(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="camera")
        capture = _capture(trigger_timestamp=1000.0, camera_impact_epoch=999.970)
        processed = ProcessedCapture(
            timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=capture),
            ball_speed_mph=100.0,
            ball_timestamp_ms=100.0,
            club_speed_mph=75.0,
            capture=capture,
            impact=ImpactEstimate(timestamp_ms=89.0, source="camera_trigger"),
        )
        shot = monitor._create_shot(processed)  # pylint: disable=protected-access
        assert shot.impact_timestamp == pytest.approx(999.970)
        assert shot.impact_timestamp_kld7 == pytest.approx(999.970)

    def test_ops_transition_still_wins_for_kld7(self):
        monitor = RollingBufferMonitor(port=None, trigger_type="camera")
        capture = _capture(trigger_timestamp=1000.0, camera_impact_epoch=999.970)
        processed = ProcessedCapture(
            timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=capture),
            ball_speed_mph=100.0,
            ball_timestamp_ms=100.0,
            club_speed_mph=75.0,
            capture=capture,
            impact=ImpactEstimate(timestamp_ms=91.0, source="ops_transition"),
        )
        shot = monitor._create_shot(processed)  # pylint: disable=protected-access
        assert shot.impact_timestamp == pytest.approx(999.970)
        assert shot.impact_timestamp_kld7 == pytest.approx(999.972)


# ---------------------------------------------------------- OPS S! timing


class ScriptedSerial:
    """Serial fake: the dump becomes readable only after S! is written."""

    is_open = True

    def __init__(self, response: bytes):
        self._pending = response
        self._buffer = b""
        self.writes = []
        self.write_times = []

    @property
    def in_waiting(self):
        return len(self._buffer)

    def reset_input_buffer(self):
        self._buffer = b""

    def write(self, data):
        self.writes.append(data)
        self.write_times.append(time.time())
        self._buffer, self._pending = self._pending, b""

    def flush(self):
        pass

    def read(self, byte_count):
        chunk, self._buffer = self._buffer[:byte_count], self._buffer[byte_count:]
        return chunk


class TestSoftwareTriggerTiming:
    def make_radar(self, response: bytes):
        from openflight.ops243 import OPS243Radar  # pylint: disable=import-outside-toplevel

        radar = OPS243Radar(port="/dev/null")
        radar.serial = ScriptedSerial(response)
        return radar

    def test_records_write_and_first_byte_timestamps(self):
        radar = self.make_radar(
            b'{"sample_time": 1.0}\r\n{"trigger_time": 1.1}\r\n{"I": [1]}\r\n{"Q": [1]}'
        )
        started = []
        response = radar.trigger_capture(timeout=1.0, on_first_byte=lambda: started.append(1))
        assert response.endswith('{"Q": [1]}')
        assert radar.serial.writes == [b"S!\r"]
        assert radar.last_software_trigger_write_timestamp >= radar.serial.write_times[0]
        assert (
            radar.last_software_trigger_first_byte_timestamp
            >= radar.last_software_trigger_write_timestamp
        )
        assert started == [1]

    def test_no_response_leaves_first_byte_unset(self):
        radar = self.make_radar(b"")
        radar.last_software_trigger_first_byte_timestamp = 123.0  # stale from last shot
        radar.trigger_capture(timeout=0.05)
        assert radar.last_software_trigger_first_byte_timestamp is None
        assert radar.last_software_trigger_write_timestamp is not None

    def test_failing_first_byte_callback_does_not_abort_dump(self, caplog):
        radar = self.make_radar(
            b'{"sample_time": 1.0}\r\n{"trigger_time": 1.1}\r\n{"I": [1]}\r\n{"Q": [1]}'
        )

        def broken():
            raise RuntimeError("ui gone")

        assert radar.trigger_capture(timeout=1.0, on_first_byte=broken).endswith('{"Q": [1]}')
        assert "First-byte callback failed" in caplog.text
