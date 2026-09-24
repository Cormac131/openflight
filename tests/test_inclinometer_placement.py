"""Stillness reporting and placement detection for camera re-calibration."""

import math
import time

import pytest

from openflight.inclinometer import (
    AccelerationSample,
    InclinometerService,
    OrientationSnapshot,
    PlacementMonitor,
    StillnessState,
)


def level_sample(timestamp: float, pitch_deg: float = 0.0) -> AccelerationSample:
    angle = math.radians(pitch_deg)
    return AccelerationSample(timestamp, 0.0, math.sin(angle), math.cos(angle))


def shaking_sample(timestamp: float) -> AccelerationSample:
    """A hand-held reading: pitch swings far enough to fail the stability test."""
    return level_sample(timestamp, pitch_deg=10.0 if round(timestamp * 10) % 2 else -10.0)


def feed(service, start: float, stop: float, factory=level_sample, step: float = 0.1):
    count = round((stop - start) / step)
    for index in range(count + 1):
        service.add_sample(factory(round(start + index * step, 3)))


def make_service(**kwargs) -> InclinometerService:
    options = {"window_samples": 4, "max_snapshot_age_s": 2.0}
    options.update(kwargs)
    return InclinometerService(sensor=None, **options)


def snapshot(pitch_deg: float = 0.0, timestamp: float = 0.0) -> OrientationSnapshot:
    return OrientationSnapshot(
        timestamp=timestamp,
        x_g=0.0,
        y_g=0.0,
        z_g=1.0,
        gravity_g=1.0,
        raw_pitch_deg=pitch_deg,
        calibrated_pitch_deg=pitch_deg,
        pitch_std_deg=0.0,
        sample_count=4,
    )


# --- InclinometerService.stillness ------------------------------------------


def test_stillness_is_zero_before_any_stable_window():
    service = make_service()
    service.add_sample(level_sample(1.0))

    state = service.stillness(now=1.0)

    assert state == StillnessState(snapshot=None, stationary_s=0.0, last_motion_timestamp=None)


def test_stillness_measures_time_since_first_stable_snapshot():
    service = make_service()
    feed(service, 10.0, 16.0)

    state = service.stillness(now=16.0)

    assert state.snapshot is not None
    assert state.snapshot.timestamp == pytest.approx(16.0)
    # The first full 4-sample window completes at 10.3s.
    assert state.stationary_s == pytest.approx(5.7)
    assert state.last_motion_timestamp is None


def test_stillness_restarts_after_motion():
    service = make_service()
    feed(service, 10.0, 16.0)
    feed(service, 16.1, 17.0, factory=shaking_sample)
    feed(service, 17.1, 19.0)

    state = service.stillness(now=19.0)

    assert state.last_motion_timestamp is not None
    assert 17.0 <= state.last_motion_timestamp < 19.0
    assert state.stationary_s < 2.0


def test_stillness_is_zero_while_moving():
    service = make_service()
    feed(service, 10.0, 16.0)
    feed(service, 16.1, 17.0, factory=shaking_sample)

    state = service.stillness(now=17.0)

    assert state.snapshot is None
    assert state.stationary_s == 0.0


def test_stillness_restarts_after_out_of_range_gravity():
    service = make_service()
    feed(service, 10.0, 16.0)
    # Being lifted: gravity magnitude stays out of range for a whole window.
    feed(service, 16.1, 16.5, factory=lambda ts: AccelerationSample(ts, 0.0, 0.0, 1.6))
    feed(service, 16.6, 18.0)

    state = service.stillness(now=18.0)

    assert state.last_motion_timestamp is not None
    assert 16.1 <= state.last_motion_timestamp < 18.0
    assert state.stationary_s < 2.0


def test_stillness_is_zero_when_latest_snapshot_is_stale():
    service = make_service()
    feed(service, 10.0, 16.0)

    state = service.stillness(now=18.5)

    assert state.snapshot is None
    assert state.stationary_s == 0.0


def test_stillness_restarts_after_a_sampling_gap():
    service = make_service()
    feed(service, 10.0, 14.0)
    # Sensor stalled for 3s (longer than the 2s snapshot age) then recovered.
    feed(service, 17.0, 19.0)

    state = service.stillness(now=19.0)

    # The first snapshot after the gap (17.0s) starts a new stationary period.
    assert state.stationary_s == pytest.approx(2.0)


def test_stillness_is_zero_after_a_sensor_error_newer_than_the_latest_snapshot():
    service = make_service()
    feed(service, 10.0, 16.0)
    service._last_error = "I2C timeout"
    service._last_error_timestamp = 16.05

    state = service.stillness(now=16.1)

    assert state.snapshot is None
    assert state.stationary_s == 0.0


def test_stillness_defaults_now_to_wall_clock(monkeypatch):
    service = make_service()
    feed(service, 10.0, 16.0)
    monkeypatch.setattr("openflight.inclinometer.service.time.time", lambda: 16.0)

    assert service.stillness().stationary_s == pytest.approx(5.7)


# --- PlacementMonitor --------------------------------------------------------


class FakeSource:
    def __init__(self):
        self.state = StillnessState(None, 0.0, None)

    def set(self, *, stationary_s, pitch_deg=0.0, motion=None, stable=True):
        self.state = StillnessState(
            snapshot(pitch_deg) if stable else None,
            stationary_s,
            motion,
        )

    def stillness(self, now=None):
        return self.state


def make_monitor(source, **kwargs):
    calls = []
    monitor = PlacementMonitor(source, calls.append, **kwargs)
    return monitor, calls


def test_monitor_fires_once_after_startup_settle():
    source = FakeSource()
    monitor, calls = make_monitor(source, settle_s=5.0)

    source.set(stationary_s=4.9)
    assert monitor.poll() is None
    source.set(stationary_s=5.0)
    assert monitor.poll() == "startup"
    source.set(stationary_s=30.0)
    assert monitor.poll() is None

    assert calls == ["startup"]
    assert monitor.pending is None


def test_monitor_waits_while_the_unit_is_held_at_startup():
    source = FakeSource()
    monitor, calls = make_monitor(source, settle_s=5.0)

    for motion in (1.0, 2.0, 3.0):
        source.set(stationary_s=0.0, motion=motion, stable=False)
        assert monitor.poll() is None

    assert calls == []
    assert monitor.pending == "startup"


def test_monitor_keeps_startup_reason_if_moved_before_first_settle():
    source = FakeSource()
    monitor, calls = make_monitor(source, settle_s=5.0)

    source.set(stationary_s=1.0, motion=2.0)
    monitor.poll()
    source.set(stationary_s=6.0, motion=2.0)
    monitor.poll()

    assert calls == ["startup"]


def test_monitor_fires_again_after_being_moved_and_settling():
    source = FakeSource()
    monitor, calls = make_monitor(source, settle_s=5.0)
    source.set(stationary_s=5.0)
    monitor.poll()

    source.set(stationary_s=0.0, motion=100.0, stable=False)
    assert monitor.poll() is None
    assert monitor.pending == "moved"
    source.set(stationary_s=3.0, motion=100.0)
    assert monitor.poll() is None
    source.set(stationary_s=5.0, motion=100.0)
    assert monitor.poll() == "moved"

    assert calls == ["startup", "moved"]


def test_monitor_fires_once_per_motion_event():
    source = FakeSource()
    monitor, calls = make_monitor(source, settle_s=5.0)
    source.set(stationary_s=5.0, motion=100.0)
    monitor.poll()
    source.set(stationary_s=5.0, motion=200.0)
    monitor.poll()

    source.set(stationary_s=20.0, motion=200.0)
    monitor.poll()
    monitor.poll()

    assert calls == ["startup", "moved"]


def test_monitor_detects_motion_seen_only_between_polls():
    """A quick bump shows up as a new motion timestamp even if every poll is stable."""
    source = FakeSource()
    monitor, calls = make_monitor(source, settle_s=5.0)
    source.set(stationary_s=5.0)
    monitor.poll()

    source.set(stationary_s=1.0, motion=50.0)
    monitor.poll()
    source.set(stationary_s=5.0, motion=50.0)
    monitor.poll()

    assert calls == ["startup", "moved"]


def test_monitor_fires_when_reoriented_without_detected_motion():
    source = FakeSource()
    monitor, calls = make_monitor(source, settle_s=5.0, reposition_deg=2.0)
    source.set(stationary_s=5.0, pitch_deg=1.0)
    monitor.poll()

    source.set(stationary_s=10.0, pitch_deg=2.9)
    assert monitor.poll() is None
    source.set(stationary_s=10.0, pitch_deg=3.5)
    assert monitor.poll() == "reoriented"
    source.set(stationary_s=20.0, pitch_deg=3.5)
    assert monitor.poll() is None

    assert calls == ["startup", "reoriented"]


def test_monitor_survives_callback_failure_without_retry_loop():
    source = FakeSource()
    attempts = []

    def failing(reason):
        attempts.append(reason)
        raise RuntimeError("camera capture is not running")

    monitor = PlacementMonitor(source, failing, settle_s=5.0)
    source.set(stationary_s=5.0)

    assert monitor.poll() == "startup"
    assert monitor.poll() is None
    assert attempts == ["startup"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"settle_s": 0.0}, "settle_s"),
        ({"settle_s": -1.0}, "settle_s"),
        ({"reposition_deg": 0.0}, "reposition_deg"),
        ({"poll_s": 0.0}, "poll_s"),
    ],
)
def test_monitor_rejects_invalid_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        PlacementMonitor(FakeSource(), lambda _reason: None, **kwargs)


def test_monitor_thread_polls_and_stops():
    source = FakeSource()
    source.set(stationary_s=10.0)
    fired = []
    monitor = PlacementMonitor(source, fired.append, settle_s=5.0, poll_s=0.01)

    monitor.start()
    deadline = time.monotonic() + 2.0
    while not fired and time.monotonic() < deadline:
        time.sleep(0.01)
    monitor.stop()

    assert fired == ["startup"]
    assert monitor._thread is None


def test_monitor_with_real_service_handles_held_startup_then_placement():
    """End to end: held while booting, set down, left still for the settle period."""
    service = make_service()
    fired = []
    monitor = PlacementMonitor(service, fired.append, settle_s=5.0)

    feed(service, 0.0, 3.0, factory=shaking_sample)
    assert monitor.poll(now=3.0) is None
    feed(service, 3.1, 7.0)
    assert monitor.poll(now=7.0) is None
    feed(service, 7.1, 9.0)
    assert monitor.poll(now=9.0) == "startup"

    # Picked up and put back down again.
    feed(service, 9.1, 10.0, factory=shaking_sample)
    assert monitor.poll(now=10.0) is None
    feed(service, 10.1, 13.0)
    assert monitor.poll(now=13.0) is None
    feed(service, 13.1, 16.0)
    assert monitor.poll(now=16.0) == "moved"

    assert fired == ["startup", "moved"]
