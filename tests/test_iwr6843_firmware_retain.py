"""Tests for the retention policy, firmware/iwr6843/l3_retain.c.

Processing region in, the window each frame keeps out: around the tee or
the locked ball while waiting, around the predicted club while it
approaches, spanning club and ball once they are close, around the ball on
the impact frames, from the origin outward while the departing ball is
sought, ahead of the prediction once it is confirmed. Plus the L3 budget
that spends bytes in priority order and the stored-frame descriptor.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

STATE = {name: index for index, name in enumerate(fw.SHOT_STATE_NAMES)}
PRIO = {name: index for index, name in enumerate(fw.RETAIN_PRIORITY_NAMES)}
WHY = {name: index for index, name in enumerate(fw.RETAIN_WHY_NAMES)}
PROCESS_START, PROCESS_BINS = 20, 53  # the wide profile's pre window


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def cfg(lib, **overrides) -> fw.RetainCfg:
    c = fw.RetainCfg()
    lib.l3_retain_cfg_defaults(ctypes.byref(c))
    for key, value in overrides.items():
        setattr(c, key, value)
    return c


def state(**overrides) -> fw.RetainState:
    s = fw.RetainState()
    s.shotState = STATE["waiting_for_ball"]
    s.ballBin = 34.0
    for key, value in overrides.items():
        setattr(s, key, STATE[value] if key == "shotState" else value)
    return s


def window(lib, c, s, retain=16, process=(PROCESS_START, PROCESS_BINS)) -> fw.RetainWindow:
    out = fw.RetainWindow()
    lib.l3_retain_window(
        ctypes.byref(c), ctypes.byref(s), process[0], process[1], retain, ctypes.byref(out)
    )
    return out


def test_defaults_are_valid_and_the_checker_rejects_nonsense(lib):
    c = cfg(lib)
    assert (c.enabled, c.approachBins, c.approachMarginBins, c.impactBiasBins) == (1, 12, 3, 4)
    assert (c.ballSearchLeadBins, c.ballFollowLeadBins, c.spinFrames) == (2, 4, 16)
    assert lib.l3_retain_cfg_check(ctypes.byref(c)) == 0
    assert lib.l3_retain_cfg_check(ctypes.byref(cfg(lib, approachBins=0))) == -1
    assert lib.l3_retain_cfg_check(ctypes.byref(cfg(lib, approachBins=65))) == -1
    assert lib.l3_retain_cfg_check(ctypes.byref(cfg(lib, impactBiasBins=33))) == -1


def test_disabled_policy_centres_the_window_in_the_processing_region(lib):
    w = window(lib, cfg(lib, enabled=0), state(shotState="club_track", clubActive=1, clubBin=25.0))
    assert (w.start, w.bins) == (20 + (53 - 16) // 2, 16)
    assert w.priority == PRIO["low"] and w.why == WHY["centred"]


def test_waiting_keeps_the_tee_and_ready_keeps_the_locked_ball(lib):
    w = window(lib, cfg(lib), state(shotState="waiting_for_ball", ballBin=34.0))
    assert w.start == 34 - 8 + 1 and w.bins == 16  # centred on 34: bins 27..42
    assert w.priority == PRIO["low"] and w.why == WHY["tee"]
    w = window(lib, cfg(lib), state(shotState="ready", ballLocked=1, ballBin=49.0))
    assert 41 <= w.start <= 42 and w.why == WHY["ball"]


def test_an_approaching_club_far_from_the_ball_is_followed(lib):
    s = state(shotState="club_track", ballLocked=1, ballBin=49.0, clubActive=1, clubBin=30.0)
    w = window(lib, cfg(lib), s)
    assert w.start == 30 - 8 + 1 and w.priority == PRIO["track"] and w.why == WHY["club"]
    assert w.start <= 30 < w.start + w.bins


def test_a_club_within_reach_of_the_ball_spans_both_with_a_margin(lib):
    s = state(shotState="club_track", ballLocked=1, ballBin=49.0, clubActive=1, clubBin=41.0)
    w = window(lib, cfg(lib), s, retain=24)
    assert w.priority == PRIO["impact"] and w.why == WHY["approach"]
    assert w.start <= 41 - 3 and 49 + 3 < w.start + w.bins, "club and ball with the margin inside"
    # Too narrow a slot to hold both: still centred between them, clipped.
    narrow = window(lib, cfg(lib), s, retain=8)
    assert narrow.bins == 8 and narrow.start <= 45 <= narrow.start + 8


def test_impact_frames_sit_on_the_ball_biased_toward_the_arriving_club(lib):
    s = state(shotState="impact", ballLocked=1, ballBin=49.0, postFrame=1, postIndex=0)
    w = window(lib, cfg(lib), s, retain=24, process=(32, 53))
    assert w.why == WHY["impact"]
    assert w.priority == PRIO["spin"], "the first post frames are tagged for the spin research"
    centred = 49 - 12 + 1
    assert w.start == centred - 4
    later = window(
        lib,
        cfg(lib, spinFrames=4),
        state(shotState="impact", ballLocked=1, ballBin=49.0, postFrame=1, postIndex=6),
        retain=24,
        process=(32, 53),
    )
    assert later.priority == PRIO["impact"]


def test_ball_search_starts_just_short_of_the_origin_and_follow_runs_ahead(lib):
    search = window(
        lib,
        cfg(lib, spinFrames=4),
        state(shotState="ball_track", ballLocked=1, ballBin=49.0, postFrame=1, postIndex=5),
        retain=16,
        process=(32, 53),
    )
    assert search.start == 49 - 2 and search.why == WHY["ballsearch"]
    assert search.priority == PRIO["impact"]
    follow = window(
        lib,
        cfg(lib, spinFrames=4),
        state(
            shotState="ball_track",
            ballLocked=1,
            ballBin=49.0,
            postFrame=1,
            postIndex=8,
            ballTrackConfirmed=1,
            ballTrackBin=66.4,
        ),
        retain=16,
        process=(47, 53),
    )
    assert (
        follow.start == 66 - 4
        and follow.why == WHY["ballfollow"]
        and follow.priority == PRIO["ball"]
    )
    assert follow.start + follow.bins > 66 + 8, "most of the window lies ahead of the flight"


def test_confirmed_flight_frames_inside_the_spin_window_are_tagged_spin(lib):
    """The spin research needs the flight frames most of all: a confirmed ball
    frame keeps its follow window but carries the spin tag until spinFrames."""
    flight = dict(
        shotState="ball_track",
        ballLocked=1,
        ballBin=49.0,
        postFrame=1,
        ballTrackConfirmed=1,
        ballTrackBin=66.4,
    )
    inside = window(lib, cfg(lib), state(postIndex=15, **flight), retain=16, process=(47, 53))
    assert inside.priority == PRIO["spin"] and inside.why == WHY["ballfollow"]
    assert inside.start == 66 - 4, "the tag never moves the window"
    after = window(lib, cfg(lib), state(postIndex=16, **flight), retain=16, process=(47, 53))
    assert after.priority == PRIO["ball"] and after.why == WHY["ballfollow"]
    off = window(
        lib, cfg(lib, spinFrames=0), state(postIndex=0, **flight), retain=16, process=(47, 53)
    )
    assert off.priority == PRIO["ball"], "spinFrames 0 turns the tag off"


def test_the_default_spin_window_covers_the_balls_time_in_view(lib):
    """About 35 ms for a ball to cross the 53-bin region: 16 frames is 32 ms at
    2 ms frames and 48 ms at 3 ms; the checker still caps it at 32."""
    assert cfg(lib).spinFrames * 2 >= 30
    assert lib.l3_retain_cfg_check(ctypes.byref(cfg(lib, spinFrames=32))) == 0
    assert lib.l3_retain_cfg_check(ctypes.byref(cfg(lib, spinFrames=33))) == -1


def test_windows_never_leave_the_processing_region(lib):
    low = window(
        lib, cfg(lib), state(shotState="club_track", clubActive=1, clubBin=3.0, ballBin=60.0)
    )
    assert low.start == PROCESS_START
    high = window(
        lib,
        cfg(lib),
        state(
            shotState="ball_track",
            postFrame=1,
            ballTrackConfirmed=1,
            ballTrackBin=120.0,
            ballBin=49.0,
        ),
        retain=16,
        process=(47, 53),
    )
    assert high.start + high.bins == 47 + 53
    whole = window(lib, cfg(lib), state(), retain=0)
    assert (whole.start, whole.bins) == (PROCESS_START, PROCESS_BINS), "0 keeps everything"
    wider = window(lib, cfg(lib), state(), retain=99)
    assert (wider.start, wider.bins) == (PROCESS_START, PROCESS_BINS)


def test_roi_and_format_and_predict(lib):
    w = window(
        lib,
        cfg(lib),
        state(shotState="club_track", clubActive=1, clubBin=30.0, ballBin=49.0, ballLocked=1),
    )
    roi = fw.Roi()
    lib.l3_retain_roi(PROCESS_START, PROCESS_BINS, ctypes.byref(w), ctypes.byref(roi))
    assert (roi.processStart, roi.processBins, roi.retainStart, roi.retainBins) == (20, 53, 23, 16)
    assert (
        fw.c_text(lib.l3_retain_format, ctypes.byref(w))
        == "retain start=23 bins=16 prio=track why=club"
    )
    assert lib.l3_retain_predict(30.0, 2.5) == pytest.approx(32.5)
    assert lib.l3_retain_priority_name(PRIO["spin"]) == b"spin"
    assert lib.l3_retain_why_name(99) == b"?"


def request(**overrides) -> fw.RetainRequest:
    r = fw.RetainRequest()
    r.bytesPerBin = 36 * 4 * 4  # 3 TX x 12 loops x 4 RX x IQ16
    r.capacityBytes = 768 * 1024
    r.maxFrames = 64
    r.preBins, r.impactBins, r.ballBins = 16, 32, 16
    r.preFrames, r.impactFrames, r.ballFrames = 40, 7, 16
    for key, value in overrides.items():
        setattr(r, key, value)
    return r


def budget(lib, r) -> tuple[int, fw.RetainBudget]:
    out = fw.RetainBudget()
    return lib.l3_retain_budget(ctypes.byref(r), ctypes.byref(out)), out


def test_budget_grants_a_request_that_fits(lib):
    status, b = budget(lib, request())
    assert status == 0
    assert (b.preFrames, b.impactFrames, b.ballFrames, b.cutPre, b.cutBall) == (40, 7, 16, 0, 0)
    per_bin = 36 * 4 * 4
    assert b.usedBytes == per_bin * (40 * 16 + 7 * 32 + 16 * 16)
    assert b.freeBytes == 768 * 1024 - b.usedBytes
    text = fw.c_text(lib.l3_retain_format_budget, ctypes.byref(b))
    assert text.startswith("budget pre=40 impact=7 ball=16 cut=0/0 used=")


def test_budget_cuts_old_club_history_before_ball_frames_and_never_impact(lib):
    per_bin = 36 * 4 * 4
    # Room for the impact frames, 16 ball frames and only 20 pre frames.
    capacity = per_bin * (7 * 32 + 16 * 16 + 20 * 16)
    status, b = budget(lib, request(capacityBytes=capacity))
    assert status == 0
    assert (b.preFrames, b.impactFrames, b.ballFrames) == (20, 7, 16)
    assert (b.cutPre, b.cutBall) == (20, 0) and b.freeBytes == 0
    # Tighter still: pre falls to the ball count, then both give way together.
    capacity = per_bin * (7 * 32 + 10 * 16 + 10 * 16)
    status, b = budget(lib, request(capacityBytes=capacity))
    assert status == 0 and b.impactFrames == 7
    assert b.preFrames + b.ballFrames == 20 and abs(b.preFrames - b.ballFrames) <= 1
    assert b.usedBytes <= capacity


def test_budget_respects_the_descriptor_table_and_refuses_an_impossible_core(lib):
    status, b = budget(lib, request(maxFrames=30))
    assert status == 0 and b.impactFrames + b.preFrames + b.ballFrames <= 30
    assert b.impactFrames == 7
    per_bin = 36 * 4 * 4
    status, _ = budget(lib, request(capacityBytes=per_bin * (7 * 32 + 16 + 16) - 1))
    assert status == -1, "the impact frames plus one pre and one ball frame do not fit"
    status, _ = budget(lib, request(maxFrames=8))
    assert status == -1, "seven impact frames plus two leave no descriptor"
    status, _ = budget(lib, request(preFrames=0))
    assert status == -1
    status, b = budget(lib, request(impactFrames=0, impactBins=0))
    assert status == 0 and b.impactFrames == 0, "an unphased plan has no impact frames"


def test_frame_descriptor_formats_what_the_ring_holds(lib):
    d = fw.FrameDesc()
    d.frame, d.timestampUs, d.globalBinStart, d.binCount = 12, 36000, 40, 16
    d.processStart, d.processBins, d.shotState = 20, 53, STATE["club_track"]
    d.priority, d.why, d.isPost = PRIO["track"], WHY["club"], 0
    text = fw.c_text(lib.l3_frame_desc_format, ctypes.byref(d))
    assert (
        text == "frame 12 t=36000us bins 40+16 of 20+53 state=club_track prio=track why=club post=0"
    )
