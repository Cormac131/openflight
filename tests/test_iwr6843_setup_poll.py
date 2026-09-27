"""The setup poller: ball status in, iwr_setup payloads out, through the job queue."""

from __future__ import annotations

import threading

import pytest

from openflight.iwr6843.setup_poll import SetupPoller, setup_payload
from openflight.iwr6843.tee_scan import parse_ball_status

LOCKED = (
    "ball status\nball state=locked follow=1 bin=34 ratio=8.65 confidence=0.93 delta=7650000 "
    "background=1000000 age=120 locks=3 releases=2 reason=none window=20+53\n"
    "balldbg updates=1842 candidate=0/0 centroid=34.30 width=2 persistence=47/50\nDone\n"
)
FAR = (
    "ball state=locked follow=0 bin=49 ratio=6.0 confidence=0.8 delta=1 background=1 "
    "age=5 locks=1 releases=0 reason=none window=20+53\nDone\n"
)
WAITING = (
    "ball state=waiting follow=0 bin=0 ratio=0.00 confidence=0.00 delta=0 background=0 "
    "age=0 locks=0 releases=0 reason=no_delta window=20+53\nDone\n"
)


class _Radar:
    def __init__(self, replies):
        self.replies = list(replies)
        self.reads = 0

    def ball_status(self) -> str:
        self.reads += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_locked_ball_in_the_ideal_band_reads_ready():
    payload = setup_payload(parse_ball_status(LOCKED), timestamp=5.0)
    assert payload["enabled"] and payload["state"] == "locked" and payload["follow"]
    assert payload["bin"] == 34
    assert payload["range_m"] == pytest.approx(34.30 * 6.0 / 128, abs=1e-3)
    assert payload["label"] == "ideal" and payload["ok"] is True
    assert payload["move_cm"] == 0 and payload["message"] == "Ready"
    assert payload["confidence"] == pytest.approx(0.93) and payload["reason"] == "none"
    assert payload["error"] is None and payload["timestamp"] == 5.0


def test_a_ball_beyond_the_envelope_says_how_far_to_move():
    payload = setup_payload(parse_ball_status(FAR))
    assert payload["range_m"] == pytest.approx(49 * 6.0 / 128, abs=1e-3)
    assert payload["label"] == "too-far" and payload["ok"] is False
    assert payload["move_cm"] == 70, "2.30 m back to the 1.60 m ideal"
    assert "closer" in payload["message"]


def test_without_a_lock_there_is_no_advice():
    payload = setup_payload(parse_ball_status(WAITING))
    assert payload["state"] == "waiting" and payload["bin"] is None
    assert payload["range_m"] is None and payload["label"] is None and payload["ok"] is None
    assert payload["reason"] == "no_delta"


def test_error_and_disabled_payloads():
    error = setup_payload(None, error="serial timeout")
    assert error["state"] == "error" and error["error"] == "serial timeout"
    assert error["enabled"] and error["label"] is None
    off = setup_payload(None, enabled=False)
    assert off["state"] == "off" and not off["enabled"]


def test_tick_submits_one_job_at_a_time_and_the_job_publishes():
    jobs = []
    published = []
    poller = SetupPoller(lambda name, job: jobs.append((name, job)) or True, published.append)
    radar = _Radar([LOCKED, WAITING])

    assert poller.tick() is True
    assert poller.tick() is False, "the first job has not run yet"
    assert len(jobs) == 1 and jobs[0][0] == "setup-poll"

    jobs[0][1](radar)
    assert published[-1]["state"] == "locked" and poller.latest is published[-1]
    assert poller.tick() is True, "pending cleared once the job ran"
    jobs[1][1](radar)
    assert published[-1]["state"] == "waiting" and poller.polls == 2


def test_a_refused_submit_leaves_the_poller_ready_for_the_next_tick():
    poller = SetupPoller(lambda name, job: False, lambda payload: None)
    assert poller.tick() is False
    submitted = []
    poller._submit = lambda name, job: submitted.append(job) or True  # noqa: SLF001
    assert poller.tick() is True and len(submitted) == 1


def test_poll_reports_read_failures_and_keeps_publishing():
    published = []
    poller = SetupPoller(lambda name, job: True, published.append, clock=lambda: 7.0)
    radar = _Radar([RuntimeError("port closed"), "Done\n", LOCKED])

    assert poller.poll(radar)["state"] == "error"
    assert "port closed" in published[0]["error"]
    assert poller.poll(radar)["state"] == "error", "a reply without a ball line"
    assert poller.poll(radar)["state"] == "locked"
    assert poller.failures == 2 and poller.polls == 3
    assert all(p["timestamp"] == 7.0 for p in published)


def test_a_failing_publisher_does_not_break_the_poll():
    def explode(_payload):
        raise RuntimeError("socket gone")

    poller = SetupPoller(lambda name, job: True, explode)
    payload = poller.poll(_Radar([LOCKED]))
    assert payload["state"] == "locked" and poller.latest is payload


def test_job_clears_pending_even_when_the_radar_raises():
    jobs = []
    poller = SetupPoller(lambda name, job: jobs.append(job) or True, lambda payload: None)
    poller.tick()
    jobs[0](_Radar([RuntimeError("boom")]))
    assert poller.latest["state"] == "error"
    assert poller.tick() is True


def test_thread_ticks_at_the_interval_and_stops():
    ticked = threading.Event()
    poller = SetupPoller(
        lambda name, job: ticked.set() or True, lambda payload: None, interval_s=0.01
    )
    poller.start()
    assert ticked.wait(1.0)
    assert poller.running
    poller.stop()
    assert not poller.running
    poller.stop()  # idempotent


def test_interval_must_be_positive():
    with pytest.raises(ValueError):
        SetupPoller(lambda name, job: True, lambda payload: None, interval_s=0)
