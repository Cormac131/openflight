"""Tests for shadow-mode camera-vs-sound trigger comparison."""

import pytest

from openflight.camera.address_monitor import CameraTriggerEvent
from openflight.camera.address_trigger import BallCandidate
from openflight.camera.shadow import ShadowTriggerComparator


def event(impact: float) -> CameraTriggerEvent:
    return CameraTriggerEvent(
        impact_epoch=impact,
        impact_uncertainty_ms=1.7,
        confirmed_epoch=impact + 0.03,
        ball=BallCandidate(1.0, 2.0, 6.0),
        addressed=True,
    )


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def make(**kwargs):
    entries = []
    clock = Clock()
    comparator = ShadowTriggerComparator(entries.append, clock=clock, **kwargs)
    return comparator, entries, clock


def test_invalid_configuration_rejected():
    with pytest.raises(ValueError):
        ShadowTriggerComparator(lambda _e: None, match_tolerance_s=0)
    with pytest.raises(ValueError):
        ShadowTriggerComparator(lambda _e: None, camera_expiry_s=0)


def test_matched_shot_logs_delta():
    comparator, entries, _clock = make()
    comparator.on_camera_trigger(event(999.990))
    comparator.on_sound_shot(1000.000)
    assert entries[0]["outcome"] == "matched"
    assert entries[0]["delta_ms"] == pytest.approx(-10.0)
    assert entries[0]["camera"]["addressed"] is True
    assert comparator.summary()["matched"] == 1
    assert comparator.summary()["median_delta_ms"] == pytest.approx(-10.0)


def test_nearest_camera_event_wins():
    comparator, entries, _clock = make()
    comparator.on_camera_trigger(event(999.5))
    comparator.on_camera_trigger(event(999.98))
    comparator.on_sound_shot(1000.0)
    assert entries[0]["delta_ms"] == pytest.approx(-20.0)
    comparator.flush(final=True)
    assert [e["outcome"] for e in entries] == ["matched", "camera_only"]


def test_sound_without_camera_is_a_miss():
    comparator, entries, _clock = make()
    comparator.on_sound_shot(1000.0)
    assert entries == [{"outcome": "sound_only", "sound_impact_epoch": 1000.0}]


def test_camera_outside_tolerance_is_not_matched():
    comparator, entries, _clock = make(match_tolerance_s=0.75)
    comparator.on_camera_trigger(event(999.0))
    comparator.on_sound_shot(1000.0)
    assert entries[0]["outcome"] == "sound_only"


def test_unmatched_camera_expires_as_false_positive():
    comparator, entries, clock = make(camera_expiry_s=10.0)
    comparator.on_camera_trigger(event(1000.0))
    clock.now = 1005.0
    comparator.flush()
    assert entries == []
    clock.now = 1011.0
    comparator.flush()
    assert entries[0]["outcome"] == "camera_only"
    assert comparator.summary()["camera_only"] == 1


def test_missing_sound_impact_is_ignored():
    comparator, entries, _clock = make()
    comparator.on_sound_shot(None)
    assert entries == []


def test_log_sink_failure_is_contained(caplog):
    def broken(_entry):
        raise OSError("disk full")

    comparator = ShadowTriggerComparator(broken, clock=Clock())
    comparator.on_sound_shot(1000.0)
    assert comparator.summary()["sound_only"] == 1
    assert "Shadow log sink failed" in caplog.text


def test_session_logger_writes_shadow_entries(tmp_path):
    from openflight.session_logger import SessionLogger  # pylint: disable=import-outside-toplevel

    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session()
    logger.log_camera_trigger_shadow({"outcome": "matched", "delta_ms": -3.0})
    logger.end_session()
    text = "".join(path.read_text() for path in tmp_path.rglob("*.jsonl"))
    assert '"camera_trigger_shadow"' in text
    assert '"delta_ms": -3.0' in text
