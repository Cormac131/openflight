"""Tests for the detect timing, firmware/iwr6843/l3_timing.c.

Two deadlines, kept apart because they were conflated before:

- throughput: the detect task's service time (dequeued -> decided) must
  average below the frame interval (3000 us), or the queue grows for ever
- latency: a frame may take longer than one interval (acquired -> decided)
  as long as its ring slot is not reused first: the reuse margin,
  (ringFrames - 2) intervals minus the latency

Every frame the detect task finishes carries cycle stamps (acquired,
dequeued, scoreStart, scoreEnd, decided); the last 16 are kept whole.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

TICKS_PER_US = 200  # the R4F at 200 MHz
BUDGET_US = 3000
RING = 12
STAT = {name: index for index, name in enumerate(fw.TIMING_STAT_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def timing(lib, ticks=TICKS_PER_US, budget=BUDGET_US, ring=RING) -> fw.Timing:
    out = fw.Timing()
    lib.l3_timing_init(ctypes.byref(out), ticks, budget, ring)
    return out


def event(
    epoch: int = 1,
    acquired: int = 0,
    wait_us: int = 100,
    score_us: int | None = 500,
    service_us: int = 1000,
    *,
    slot: int = 0,
    flags: int = 0,
    core: int = fw.DETECT_CORE_MSS,
    depth: int = 0,
) -> fw.TimingEvent:
    mask = 0xFFFFFFFF
    dequeued = (acquired + wait_us * TICKS_PER_US) & mask
    out = fw.TimingEvent(
        slot=slot,
        epoch=epoch,
        acquired=acquired & mask,
        dequeued=dequeued,
        decided=(dequeued + service_us * TICKS_PER_US) & mask,
        core=core,
        flags=flags,
        depth=depth,
    )
    if score_us is not None:
        out.scoreStart = (dequeued + 10 * TICKS_PER_US) & mask
        out.scoreEnd = (out.scoreStart + score_us * TICKS_PER_US) & mask
        out.flags |= fw.TIMING_FLAG_SCORED
    return out


def record(lib, t: fw.Timing, *events: fw.TimingEvent) -> None:
    for e in events:
        lib.l3_timing_record(ctypes.byref(t), ctypes.byref(e))


def stat(t: fw.Timing, name: str) -> fw.TimingStat:
    return t.stat[STAT[name]]


def summary(lib, t: fw.Timing) -> str:
    return fw.c_text(lib.l3_timing_format_summary, ctypes.byref(t), cap=200)


def stat_line(lib, t: fw.Timing, name: str) -> str:
    return fw.c_text(lib.l3_timing_format_stat, ctypes.byref(t), STAT[name], cap=200)


def timeline_line(lib, t: fw.Timing, e: fw.TimingEvent) -> str:
    return fw.c_text(lib.l3_timing_format_event, ctypes.byref(t), ctypes.byref(e), cap=200)


# --- one frame ---------------------------------------------------------------------


def test_a_frame_measures_wait_score_service_and_latency(lib):
    t = timing(lib)
    record(lib, t, event(wait_us=150, score_us=600, service_us=1200))
    assert (stat(t, "wait").lastUs, stat(t, "score").lastUs) == (150, 600)
    assert (stat(t, "service").lastUs, stat(t, "latency").lastUs) == (1200, 1350)
    assert t.frames == 1


def test_an_unscored_frame_records_no_score(lib):
    """An early return (the trigger disabled, the ring not yet full) scores nothing."""
    t = timing(lib)
    record(lib, t, event(score_us=None))
    assert stat(t, "score").count == 0 and stat(t, "service").count == 1


def test_stamps_survive_the_cycle_counter_wrapping(lib):
    """At 200 MHz the 32-bit counter wraps every ~21 s, mid-frame sometimes."""
    t = timing(lib)
    record(lib, t, event(acquired=0xFFFFFFFF - 50 * TICKS_PER_US, wait_us=100, service_us=900))
    assert (stat(t, "wait").lastUs, stat(t, "latency").lastUs) == (100, 1000)


def test_a_zero_clock_is_taken_as_one_tick_per_us(lib):
    t = timing(lib, ticks=0)
    assert t.ticksPerUs == 1


# --- throughput -----------------------------------------------------------------------


def test_service_over_the_interval_is_counted(lib):
    t = timing(lib)
    record(lib, t, event(service_us=2999), event(service_us=3000), event(service_us=3001))
    assert t.overBudget == 1


def test_min_mean_max(lib):
    t = timing(lib)
    record(lib, t, *(event(epoch=k + 1, service_us=us) for k, us in enumerate((1000, 3000, 2000))))
    s = stat(t, "service")
    assert (s.count, s.minUs, s.maxUs, s.lastUs) == (3, 1000, 3000, 2000)
    assert lib.l3_timing_mean_us(ctypes.byref(t), STAT["service"]) == 2000


def test_the_sum_saturates_and_says_so(lib):
    t = timing(lib)
    stat(t, "service").sumUs = 0xFFFFFFFF - 10
    stat(t, "service").count = 1
    record(lib, t, event(service_us=1000))
    assert stat(t, "service").sumUs == 0xFFFFFFFF and stat(t, "service").sumOverflow == 1


def test_mean_of_nothing_is_zero(lib):
    t = timing(lib)
    assert lib.l3_timing_mean_us(ctypes.byref(t), STAT["latency"]) == 0
    assert lib.l3_timing_mean_us(ctypes.byref(t), 99) == 0


def test_the_deepest_queue_is_kept(lib):
    t = timing(lib)
    record(lib, t, event(depth=2), event(depth=5), event(depth=1))
    assert t.depthMax == 5


# --- latency and the reuse margin -------------------------------------------------------


def test_the_margin_is_the_ring_less_two_frames_less_the_latency(lib):
    t = timing(lib)
    record(lib, t, event(wait_us=0, service_us=2000))
    assert t.marginLastUs == (RING - 2) * BUDGET_US - 2000
    assert t.marginMinUs == t.marginLastUs and t.marginNegative == 0


def test_a_frame_longer_than_one_interval_can_still_have_margin(lib):
    """Latency past 3 ms is not a failure: the slot lives ring - 2 intervals."""
    t = timing(lib)
    record(lib, t, event(wait_us=4000, service_us=2500))
    assert stat(t, "latency").lastUs == 6500 and t.marginLastUs > 0


def test_a_frame_past_its_slots_life_is_a_negative_margin(lib):
    t = timing(lib)
    record(lib, t, event(wait_us=(RING - 2) * BUDGET_US, service_us=1))
    assert t.marginLastUs == -1 and t.marginNegative == 1


def test_the_tightest_margin_is_kept(lib):
    t = timing(lib)
    record(lib, t, event(epoch=1, service_us=500), event(epoch=2, service_us=9000))
    record(lib, t, event(epoch=3, service_us=100))
    assert t.marginMinUs == (RING - 2) * BUDGET_US - 9100


def test_post_frames_have_no_margin(lib):
    """Post-impact slots are never reused while the ring is frozen."""
    t = timing(lib)
    record(lib, t, event(flags=fw.TIMING_FLAG_POST, service_us=99999))
    assert (t.marginCount, t.marginNegative) == (0, 0)
    assert stat(t, "latency").count == 1


@pytest.mark.parametrize("ring", [0, 1])
def test_a_ring_too_small_to_overlap_has_no_margin(lib, ring):
    t = timing(lib, ring=ring)
    record(lib, t, event())
    assert t.marginCount == 0
    assert lib.l3_timing_margin_us(ctypes.byref(t), 0) < -(2**29)


def test_the_margin_of_a_ring_of_two_is_only_the_latency(lib):
    t = timing(lib, ring=2)
    assert lib.l3_timing_margin_us(ctypes.byref(t), 10) == -10


def test_a_huge_latency_clamps_the_margin_instead_of_wrapping(lib):
    t = timing(lib, ring=2)
    assert lib.l3_timing_margin_us(ctypes.byref(t), 0xFFFFFFFF) == -(2**31) // 2


def test_a_huge_ring_does_not_overflow_the_margin(lib):
    t = timing(lib, ring=0xFFFFFFFF, budget=0xFFFFFFFF)
    assert lib.l3_timing_margin_us(ctypes.byref(t), 0) == 2**31 - 1


# --- arrival -----------------------------------------------------------------------------


def test_arrival_is_between_consecutive_frames(lib):
    t = timing(lib)
    record(lib, t, event(epoch=5, acquired=0), event(epoch=6, acquired=3000 * TICKS_PER_US))
    assert stat(t, "arrival").count == 1 and stat(t, "arrival").lastUs == 3000


def test_a_gap_in_the_epochs_is_not_an_arrival(lib):
    """A frame dropped or stale in between would read as one long interval."""
    t = timing(lib)
    record(lib, t, event(epoch=5, acquired=0), event(epoch=7, acquired=6000 * TICKS_PER_US))
    assert stat(t, "arrival").count == 0


def test_a_session_restart_is_not_an_arrival(lib):
    t = timing(lib)
    record(lib, t, event(epoch=500, acquired=0), event(epoch=1, acquired=10))
    assert stat(t, "arrival").count == 0


def test_post_frames_do_not_break_the_arrival_chain(lib):
    t = timing(lib)
    record(lib, t, event(epoch=5, acquired=0))
    record(lib, t, event(epoch=0xFFFFFFFF, acquired=100, flags=fw.TIMING_FLAG_POST))
    record(lib, t, event(epoch=6, acquired=3000 * TICKS_PER_US))
    assert stat(t, "arrival").lastUs == 3000


# --- the timeline ---------------------------------------------------------------------------


def events_kept(lib, t: fw.Timing) -> list[int]:
    out, index, e = [], 0, fw.TimingEvent()
    while lib.l3_timing_event(ctypes.byref(t), index, ctypes.byref(e)) == 0:
        out.append(e.epoch)
        index += 1
    return out


def test_the_timeline_keeps_frames_oldest_first(lib):
    t = timing(lib)
    record(lib, t, *(event(epoch=k) for k in (1, 2, 3)))
    assert events_kept(lib, t) == [1, 2, 3]


def test_the_timeline_keeps_the_last_sixteen(lib):
    t = timing(lib)
    record(lib, t, *(event(epoch=k) for k in range(1, 41)))
    assert events_kept(lib, t) == list(range(25, 41))


def test_the_timeline_exactly_full(lib):
    t = timing(lib)
    record(lib, t, *(event(epoch=k) for k in range(1, fw.TIMING_TIMELINE_DEPTH + 1)))
    assert events_kept(lib, t) == list(range(1, fw.TIMING_TIMELINE_DEPTH + 1))


def test_no_such_event(lib):
    t = timing(lib)
    assert lib.l3_timing_event(ctypes.byref(t), 0, ctypes.byref(fw.TimingEvent())) == -1
    record(lib, t, event())
    assert lib.l3_timing_event(ctypes.byref(t), 0, None) == -1


# --- reset -------------------------------------------------------------------------------------


def test_reset_keeps_the_clock_budget_and_ring(lib):
    t = timing(lib)
    record(lib, t, *(event(epoch=k) for k in range(1, 5)))
    lib.l3_timing_reset(ctypes.byref(t))
    assert (t.ticksPerUs, t.budgetUs, t.ringFrames) == (TICKS_PER_US, BUDGET_US, RING)
    assert (t.frames, t.timelineCount, t.marginCount, t.havePrevious) == (0, 0, 0, 0)
    assert all(t.stat[k].count == 0 for k in range(len(fw.TIMING_STAT_NAMES)))


# --- formats -----------------------------------------------------------------------------------


def test_the_summary_before_any_pre_impact_frame(lib):
    t = timing(lib)
    assert summary(lib, t) == (
        "timing frames=0 budget_us=3000 over_budget=0 depth_max=0 ring=12 "
        "margin_last_us=- margin_min_us=- margin_negative=0"
    )


def test_the_summary(lib):
    t = timing(lib)
    record(lib, t, event(epoch=1, wait_us=0, service_us=3500, depth=3))
    record(lib, t, event(epoch=2, wait_us=0, service_us=1000))
    assert summary(lib, t) == (
        "timing frames=2 budget_us=3000 over_budget=1 depth_max=3 ring=12 "
        "margin_last_us=29000 margin_min_us=26500 margin_negative=0"
    )


def test_a_negative_margin_is_printed_signed(lib):
    t = timing(lib, ring=2)
    record(lib, t, event(wait_us=0, service_us=40))
    assert "margin_last_us=-40 margin_min_us=-40 margin_negative=1" in summary(lib, t)


def test_a_stat_line(lib):
    t = timing(lib)
    record(lib, t, event(service_us=1000), event(epoch=2, service_us=2000))
    assert (
        stat_line(lib, t, "service") == "timing service n=2 last=2000 min=1000 mean=1500 max=2000"
    )


def test_an_unknown_stat_line(lib):
    t = timing(lib)
    assert fw.c_text(lib.l3_timing_format_stat, ctypes.byref(t), 99, cap=64) == "timing ? n=0"
    assert lib.l3_timing_stat_name(99) == b"?"


def test_a_timeline_line(lib):
    t = timing(lib)
    e = event(
        epoch=41,
        slot=5,
        wait_us=120,
        score_us=640,
        service_us=1800,
        depth=2,
        core=fw.DETECT_CORE_DSS,
        flags=fw.TIMING_FLAG_BEHIND | fw.TIMING_FLAG_FIRED,
    )
    assert timeline_line(lib, t, e) == (
        "timeline slot=5 epoch=41 core=dss wait_us=120 score_us=640 service_us=1800 "
        "latency_us=1920 depth=2 flags=behind|fired"
    )


def test_a_post_frame_that_fell_back_and_was_not_scored(lib):
    t = timing(lib)
    e = event(
        epoch=0xFFFFFFFF,
        slot=14,
        score_us=None,
        flags=fw.TIMING_FLAG_POST,
        core=fw.TIMING_CORE_FALLBACK,
    )
    assert timeline_line(lib, t, e) == (
        "timeline slot=14 epoch=post core=fallback wait_us=100 score_us=- service_us=1000 "
        "latency_us=1100 depth=0 flags=post"
    )


def test_every_flag_at_once_and_none(lib):
    t = timing(lib)
    every = (
        fw.TIMING_FLAG_POST | fw.TIMING_FLAG_BEHIND | fw.TIMING_FLAG_STALE | fw.TIMING_FLAG_FIRED
    )
    assert timeline_line(lib, t, event(flags=every)).endswith("flags=post|behind|stale|fired")
    assert timeline_line(lib, t, event(score_us=None)).endswith("flags=-")


@pytest.mark.parametrize(("core", "name"), [(0, "mss"), (1, "dss"), (2, "verify"), (9, "?")])
def test_each_core_is_named(lib, core, name):
    t = timing(lib)
    assert f" core={name} " in timeline_line(lib, t, event(core=core))


def test_the_widest_lines_fit_the_boards_buffer(lib):
    """ "triggerLog" prints them from its 192-byte line."""
    t = timing(lib, ticks=1, budget=0xFFFFFFFF, ring=0xFFFFFFFF)
    for name in ("frames", "overBudget", "depthMax", "marginNegative"):
        setattr(t, name, 0xFFFFFFFF)
    t.marginCount, t.marginLastUs, t.marginMinUs = 1, -(2**31), -(2**31)
    for k in range(len(fw.TIMING_STAT_NAMES)):
        for name in ("count", "lastUs", "minUs", "maxUs", "sumUs"):
            setattr(t.stat[k], name, 0xFFFFFFFF)
    widest = fw.TimingEvent(
        slot=0xFFFFFFFF,
        epoch=0xFFFFFFFF,
        dequeued=0xFFFFFFFF,
        scoreEnd=0xFFFFFFFF,
        decided=0xFFFFFFFE,
        core=fw.TIMING_CORE_FALLBACK,
        flags=0x1E,
        depth=255,
    )
    lines = [fw.c_text(lib.l3_timing_format_summary, ctypes.byref(t), cap=400)]
    lines += [
        fw.c_text(lib.l3_timing_format_stat, ctypes.byref(t), k, cap=400)
        for k in range(len(fw.TIMING_STAT_NAMES))
    ]
    lines.append(
        fw.c_text(lib.l3_timing_format_event, ctypes.byref(t), ctypes.byref(widest), cap=400)
    )
    assert max(len(line) for line in lines) < 192
