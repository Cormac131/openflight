"""Tests for the camera address monitor runtime glue and frame observer hook."""

import threading

import numpy as np
import pytest
from camera_frames import (
    ADDRESS,
    BALL,
    EMPTY,
    FRAME_NS,
    FrameFactory,
    candidate_for,
)

from openflight.camera.address_monitor import (
    CameraAddressMonitor,
    CameraTriggerEvent,
    SensorClockMapper,
)
from openflight.camera.address_trigger import AddressState, BallCandidate
from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
from openflight.camera.triggered_buffer import CameraFrame

EPOCH_NS = 1_700_000_000_000_000_000


class FakeClock:
    """Controllable monotonic clock (seconds)."""

    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def fixed_mapper() -> SensorClockMapper:
    """Mapper whose sensor clock == 'mono' clock pinned at 2 s, epoch pinned."""
    return SensorClockMapper(
        clocks={"mono": lambda: 2_000_000_000},
        epoch_ns=lambda: EPOCH_NS,
    )


def make_frame(image: np.ndarray, sensor_ns: int, exposure_us=1000, gain=2.0) -> CameraFrame:
    return CameraFrame(
        image=image,
        sensor_timestamp_ns=sensor_ns,
        host_timestamp_ns=sensor_ns,
        exposure_us=exposure_us,
        analogue_gain=gain,
    )


class Harness:
    """Drives a monitor with synthetic frames and a scripted acquirer."""

    def __init__(self, **monitor_kwargs):
        self.factory = FrameFactory()
        self.clock = FakeClock()
        self.next_candidate = None
        monitor_kwargs.setdefault("clock_mapper", fixed_mapper())
        self.monitor = CameraAddressMonitor(
            acquire_fn=lambda _image: self.next_candidate,
            monotonic=self.clock,
            **monitor_kwargs,
        )
        self.sensor_ns = 1_000_000_000

    def frame(self, scene, **kwargs):
        self.sensor_ns += FRAME_NS
        self.clock.now += FRAME_NS / 1e9
        self.monitor.on_frame(make_frame(self.factory.render(scene), self.sensor_ns, **kwargs))

    def lock(self):
        self.next_candidate = None
        for _ in range(2):
            self.frame(EMPTY)
            self.monitor.acquire_once()
        self.next_candidate = candidate_for(BALL)
        for _ in range(self.monitor.machine.config.stable_acquisitions):
            self.frame(BALL)
            self.monitor.acquire_once()
        assert self.monitor.machine.state == AddressState.BALL_PRESENT

    def shot(self):
        for scene, count in [(BALL, 10), (ADDRESS, 20), (BALL, 5), (EMPTY, 9)]:
            for _ in range(count):
                self.frame(scene)


# ------------------------------------------------------------ clock mapping


class TestSensorClockMapper:
    def test_picks_clock_where_frame_is_recent(self):
        mapper = SensorClockMapper(
            clocks={
                "far": lambda: 900_000_000_000,
                "near": lambda: 5_010_000_000,
            },
            epoch_ns=lambda: EPOCH_NS,
        )
        epoch = mapper.to_epoch(5_000_000_000)
        assert mapper.clock_name == "near"
        assert epoch == pytest.approx((EPOCH_NS - 10_000_000) / 1e9)

    def test_rejects_future_sensor_timestamps(self):
        mapper = SensorClockMapper(
            clocks={"behind": lambda: 4_000_000_000, "ok": lambda: 5_000_000_100},
            epoch_ns=lambda: EPOCH_NS,
        )
        mapper.to_epoch(5_000_000_000)
        assert mapper.clock_name == "ok"

    def test_unknown_clock_assumes_arrival_now(self):
        mapper = SensorClockMapper(clocks={"x": lambda: 1}, epoch_ns=lambda: EPOCH_NS)
        assert mapper.to_epoch(123_456_789_000) == pytest.approx(EPOCH_NS / 1e9)
        assert mapper.clock_name == "assumed_now"
        # Later frames keep the same fixed offset.
        assert mapper.to_epoch(123_456_789_000 + 10_000_000) == pytest.approx(EPOCH_NS / 1e9 + 0.01)

    def test_conversion_tracks_wall_clock_steps(self):
        epoch = [EPOCH_NS]
        mapper = SensorClockMapper(clocks={"m": lambda: 5_000_000_000}, epoch_ns=lambda: epoch[0])
        first = mapper.to_epoch(4_990_000_000)
        epoch[0] += 2_000_000_000  # NTP step
        assert mapper.to_epoch(4_990_000_000) == pytest.approx(first + 2.0)

    def test_default_clocks_map_monotonic_frames(self):
        import time  # pylint: disable=import-outside-toplevel

        mapper = SensorClockMapper()
        sensor_ns = time.monotonic_ns() - 5_000_000
        assert abs(mapper.to_epoch(sensor_ns) - (time.time() - 0.005)) < 0.05


# ------------------------------------------------------------ monitor flow


class TestMonitorFlow:
    def test_invalid_intervals_rejected(self):
        with pytest.raises(ValueError):
            CameraAddressMonitor(acquire_fn=lambda _i: None, acquisition_interval_s=0)
        with pytest.raises(ValueError):
            CameraAddressMonitor(acquire_fn=lambda _i: None, watchdog_timeout_s=0)

    def test_shot_publishes_trigger_event(self):
        h = Harness()
        h.lock()
        h.shot()
        event = h.monitor.wait_for_trigger(timeout=0.1)
        assert isinstance(event, CameraTriggerEvent)
        assert event.addressed
        # Impact is half a frame after the last BALL frame, 9 frames before confirm.
        assert event.confirmed_epoch - event.impact_epoch == pytest.approx(
            8.5 * FRAME_NS / 1e9, abs=1e-6
        )
        assert event.impact_uncertainty_ms == pytest.approx(FRAME_NS / 2 / 1e6)
        assert h.monitor.status()["triggers"] == 1

    def test_event_consumed_once(self):
        h = Harness()
        h.lock()
        h.shot()
        assert h.monitor.wait_for_trigger(timeout=0.05) is not None
        assert h.monitor.wait_for_trigger(timeout=0.05) is None

    def test_triggered_until_rearm(self):
        h = Harness()
        h.lock()
        h.shot()
        assert h.monitor.machine.state == AddressState.TRIGGERED
        h.monitor.rearm()
        assert h.monitor.machine.state == AddressState.IDLE

    def test_rearm_discards_pending_event(self):
        h = Harness()
        h.lock()
        h.shot()
        h.monitor.rearm()
        assert h.monitor.wait_for_trigger(timeout=0.05) is None

    def test_auto_rearm_for_shadow_mode(self):
        h = Harness(auto_rearm=True)
        h.lock()
        h.shot()
        assert h.monitor.machine.state == AddressState.IDLE
        h.lock()
        h.shot()
        assert h.monitor.status()["triggers"] == 2

    def test_listeners_notified_and_failures_isolated(self):
        h = Harness()
        seen = []

        def broken(_event):
            raise RuntimeError("boom")

        h.monitor.add_listener(broken)
        h.monitor.add_listener(seen.append)
        h.lock()
        h.shot()
        assert len(seen) == 1

    def test_acquisition_skipped_when_locked(self):
        h = Harness()
        h.lock()
        calls = []
        h.monitor._acquire_fn = lambda image: calls.append(image)  # pylint: disable=protected-access
        assert h.monitor.acquire_once() == []
        assert calls == []

    def test_acquisition_without_frames_is_noop(self):
        monitor = CameraAddressMonitor(acquire_fn=lambda _i: BallCandidate(1, 1, 5))
        assert monitor.acquire_once() == []

    def test_acquisition_failure_is_logged_not_raised(self, caplog):
        h = Harness()

        def failing(_image):
            raise RuntimeError("detector exploded")

        h.monitor._acquire_fn = failing  # pylint: disable=protected-access
        h.frame(BALL)
        assert h.monitor.acquire_once() == []
        assert "Ball acquisition failed" in caplog.text

    def test_brightness_scale_from_frame_controls(self):
        h = Harness()
        h.lock()
        # Exposure doubled and reported via frame metadata: scene 2x brighter.
        for scene, count in [(BALL, 10), (ADDRESS, 20), (BALL, 5)]:
            for _ in range(count):
                h.frame(scene)
        from dataclasses import replace  # pylint: disable=import-outside-toplevel

        for _ in range(9):
            h.frame(replace(EMPTY, gain=1.5), exposure_us=1500)
        assert h.monitor.wait_for_trigger(timeout=0.05) is not None

    def test_zero_exposure_metadata_treated_as_unit_scale(self):
        h = Harness()
        h.lock()
        h.frame(BALL, exposure_us=0, gain=0.0)
        assert h.monitor.machine.state == AddressState.BALL_PRESENT


# ----------------------------------------------------------- health / wait


class TestHealthAndWaiting:
    def test_unhealthy_before_first_frame(self):
        h = Harness()
        assert not h.monitor.is_healthy()
        assert h.monitor.status()["armed"] is False

    def test_watchdog_marks_stalled_camera_unarmed(self):
        h = Harness(watchdog_timeout_s=0.5)
        h.frame(EMPTY)
        assert h.monitor.is_healthy()
        h.clock.now += 0.6
        assert not h.monitor.is_healthy()
        assert h.monitor.status()["healthy"] is False

    def test_wait_reports_stall_once(self, caplog):
        h = Harness(watchdog_timeout_s=0.5)
        h.frame(EMPTY)
        h.clock.now += 1.0
        assert h.monitor.wait_for_trigger(timeout=0.2) is None
        assert caplog.text.count("No camera frames") == 1

    def test_wait_honours_cancel_event(self):
        h = Harness()
        cancel = threading.Event()
        cancel.set()
        assert h.monitor.wait_for_trigger(timeout=5.0, cancel_event=cancel) is None

    def test_wait_wakes_on_trigger_from_other_thread(self):
        h = Harness()
        h.lock()
        thread = threading.Thread(target=h.shot)
        results = []
        waiter = threading.Thread(
            target=lambda: results.append(h.monitor.wait_for_trigger(timeout=5.0))
        )
        waiter.start()
        thread.start()
        thread.join()
        waiter.join(timeout=5.0)
        assert results and results[0] is not None

    def test_status_fields(self):
        h = Harness()
        h.lock()
        h.frame(BALL)
        status = h.monitor.status()
        assert status["state"] == "ball_present"
        assert status["ball"]["radius"] == pytest.approx(6.0)
        assert status["callback_us_p50"] is not None
        assert status["callback_us_p99"] >= status["callback_us_p50"]
        assert status["sensor_clock"] is None  # no trigger converted yet

    def test_background_worker_acquires(self):
        h = Harness(acquisition_interval_s=0.01)
        h.next_candidate = candidate_for(BALL)
        h.frame(BALL)
        h.monitor.start()
        try:
            for _ in range(200):
                if h.monitor.machine.state == AddressState.BALL_PRESENT:
                    break
                threading.Event().wait(0.01)
        finally:
            h.monitor.stop()
        assert h.monitor.machine.state == AddressState.BALL_PRESENT


# ------------------------------------------------------ runtime observer hook


class FakeRequest:
    def __init__(self, raw: np.ndarray, sensor_ns: int):
        self._raw = raw
        self._sensor_ns = sensor_ns

    def get_metadata(self):
        return {"SensorTimestamp": self._sensor_ns, "ExposureTime": 800, "AnalogueGain": 2.0}

    def make_array(self, _name):
        return self._raw


class TestRuntimeFrameObserver:
    def make_runtime(self, tmp_path):
        return CameraCaptureRuntime(
            output_dir=tmp_path,
            settings=CameraCaptureSettings(width=8, height=4, fps=300.0),
            use_gpio_trigger=False,
        )

    def test_observer_receives_every_frame(self, tmp_path):
        runtime = self.make_runtime(tmp_path)
        seen = []
        runtime.add_frame_observer(seen.append)
        for index in range(3):
            runtime._on_frame(FakeRequest(np.full((4, 8), index, np.uint8), 10 + index))  # pylint: disable=protected-access
        assert [frame.sensor_timestamp_ns for frame in seen] == [10, 11, 12]
        assert seen[0].exposure_us == 800
        assert seen[0].image.shape == (4, 8)

    def test_failing_observer_does_not_break_ring_or_others(self, tmp_path, caplog):
        runtime = self.make_runtime(tmp_path)
        seen = []

        def broken(_frame):
            raise RuntimeError("observer bug")

        runtime.add_frame_observer(broken)
        runtime.add_frame_observer(seen.append)
        runtime._on_frame(FakeRequest(np.zeros((4, 8), np.uint8), 1))  # pylint: disable=protected-access
        assert len(seen) == 1
        assert runtime._ring.buffered_frames == 1  # pylint: disable=protected-access
        assert "Frame observer failed" in caplog.text

    def test_bad_frame_not_forwarded(self, tmp_path):
        runtime = self.make_runtime(tmp_path)
        seen = []
        runtime.add_frame_observer(seen.append)
        runtime._on_frame(FakeRequest(np.zeros((1, 2), np.uint8), 1))  # pylint: disable=protected-access
        assert seen == []
