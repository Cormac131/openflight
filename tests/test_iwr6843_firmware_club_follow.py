"""The club after impact: l3_track_follow with l3_follow_ctx_t.

Scene: 3 ms frames, club approaching at 640 bins/s (30 m/s) from bin 20,
tee band 30..40, ball at rest at bin 35, impact at 18 000 us.
"""

from __future__ import annotations

import ctypes
import re

import pytest

from openflight.iwr6843 import firmware_host as fw

FRAME_US = 3000
APPROACH = 640.0  # bins/s
NO = fw.TRACK_NO_TARGET


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def target(frame, bin_, *, stat=100.0, doppler=5.0):
    t = fw.TargetObs()
    t.frame, t.timestampUs = frame, frame * FRAME_US
    t.peakBin, t.rangeBin = int(round(bin_)), bin_
    t.energy, t.peak, t.stat, t.snr = stat * 4, stat, stat, 20.0
    t.coherence, t.dopplerAliasMps, t.confidence = 0.9, doppler, 0.9
    return t


def approached_track(lib, frames=6):
    """A club track built from frames 0..frames-1 at 640 bins/s from bin 20."""
    cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(cfg))
    for f in range(frames):
        arr = (fw.TargetObs * 1)(target(f, 20.0 + APPROACH * f * FRAME_US * 1e-6))
        lib.l3_track_update(ctypes.byref(track), arr, 1, f, f * FRAME_US)
    assert track.active == 1
    return track


def ctx(*, band=True, ball=1500.0, claim=NO, approach=APPROACH):
    c = fw.FollowCtx()
    c.bandValid, c.bandHiBin, c.originBin = 1 if band else 0, 40.0, 35.0
    c.impactTimestampUs, c.approachBinsPerS, c.ballBinsPerS = 18_000, approach, ball
    c.ballClaimIndex, c.frameUs = claim, FRAME_US
    return c


def follow(lib, track, frame, targets, c):
    arr = (fw.TargetObs * max(1, len(targets)))(*targets)
    return lib.l3_track_follow(
        ctypes.byref(track),
        arr,
        len(targets),
        frame,
        frame * FRAME_US,
        ctypes.byref(c) if c is not None else None,
    )


def test_coasts_across_the_band_instead_of_dropping(lib):
    track = approached_track(lib)  # last point frame 5, bin 29.6
    c = ctx()
    for f in range(6, 11):  # the band hides the club
        assert follow(lib, track, f, [], c) == 0
        assert track.active == 1, f
    assert follow(lib, track, 11, [target(11, 41.0)], c) == 1
    assert track.lastBin == pytest.approx(41.0)


def test_without_context_misses_still_drop(lib):
    track = approached_track(lib)
    for f in range(6, 10):
        follow(lib, track, f, [], None)
    assert track.active == 0


def test_band_off_keeps_the_miss_rule(lib):
    track = approached_track(lib)
    c = ctx(band=False)
    for f in range(6, 10):
        follow(lib, track, f, [], c)
    assert track.active == 0


def test_coast_ends_after_the_crossing_time_plus_a_frame(lib):
    track = approached_track(lib)  # (40 - 29.6) / 640 = 16.25 ms, + 3 ms
    c = ctx()
    for f in range(6, 14):  # t = 39 ms > 15 + 19.25 ms
        follow(lib, track, f, [], c)
    assert track.active == 0


def test_a_return_faster_than_the_ball_is_not_the_club(lib):
    track = approached_track(lib)
    c = ctx(ball=500.0)  # 41.0 from 29.6 over 18 ms is 633 bins/s
    for f in range(6, 11):
        follow(lib, track, f, [], c)
    assert follow(lib, track, 11, [target(11, 41.0)], c) == 0


def test_the_balls_claimed_target_is_not_the_club(lib):
    track = approached_track(lib)
    for f in range(6, 11):
        follow(lib, track, f, [], ctx())
    targets = [target(11, 41.0, stat=500.0), target(11, 40.6, stat=50.0)]
    assert follow(lib, track, 11, targets, ctx(claim=0)) == 1
    assert track.lastTargetIndex == 1
    assert track.lastBin == pytest.approx(40.6)


def test_unknown_ball_rate_bounds_by_approach_only(lib):
    track = approached_track(lib)
    c = ctx(ball=0.0)
    for f in range(6, 11):
        follow(lib, track, f, [], c)
    assert follow(lib, track, 11, [target(11, 41.0)], c) == 1


def fresh_track(lib):
    cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(cfg))
    return track


def test_a_dead_track_reacquires_the_slower_departing_mover(lib):
    track = fresh_track(lib)
    # t = 33 ms, 15 ms after impact: 41.0 is 400 bins/s from 35 (club),
    # 55.0 is 1333 bins/s (faster than the club arrived: not the club).
    assert follow(lib, track, 11, [target(11, 55.0, stat=500.0), target(11, 41.0)], ctx()) == 1
    assert track.active == 1 and track.following == 1
    assert track.lastBin == pytest.approx(41.0)
    assert track.lastTargetIndex == 1
    assert track.followBinsPerS == pytest.approx(APPROACH)


@pytest.mark.parametrize(
    "bin_, kwargs",
    [
        (39.0, {}),  # inside the band
        (41.0, {"ball": 300.0}),  # 400 bins/s: faster than the ball
        (41.0, {"claim": 0}),  # the ball's target
        (41.0, {"approach": 0.0}),  # no approach speed: re-acquisition off
    ],
)
def test_reacquisition_refuses(lib, bin_, kwargs):
    track = fresh_track(lib)
    assert follow(lib, track, 11, [target(11, bin_)], ctx(**kwargs)) == 0
    assert track.active == 0


def test_reacquisition_needs_a_context(lib):
    track = fresh_track(lib)
    assert follow(lib, track, 11, [target(11, 41.0)], None) == 0


def test_recent_rate(lib):
    track = fresh_track(lib)
    assert lib.l3_track_recent_rate(ctypes.byref(track)) == 0.0
    track = approached_track(lib, frames=2)
    assert lib.l3_track_recent_rate(ctypes.byref(track)) == pytest.approx(APPROACH, rel=1e-3)
    track = approached_track(lib, frames=6)
    assert lib.l3_track_recent_rate(ctypes.byref(track)) == pytest.approx(APPROACH, rel=1e-3)


def test_unknown_approach_mirrors_the_header():
    header = (fw.FIRMWARE_DIR / "l3_club_track.h").read_text(encoding="utf-8")
    match = re.search(r"#define L3_TRACK_FOLLOW_UNKNOWN_APPROACH_MPS ([0-9.]+)F", header)
    assert match is not None
    assert float(match.group(1)) == fw.TRACK_FOLLOW_UNKNOWN_APPROACH_MPS == 70.0


def test_the_unknown_approach_ceiling_reacquires_what_a_measured_one_refuses(lib):
    """No delivery at impact: the context's approach is the fastest club, so
    a fast departing club (1333 bins/s, slower than the 1500 bins/s ball) is
    re-acquired; a measured 30 m/s approach refuses it as too fast."""
    cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(cfg))
    ceiling = fw.TRACK_FOLLOW_UNKNOWN_APPROACH_MPS / cfg.binWidthM
    track = fresh_track(lib)
    assert follow(lib, track, 11, [target(11, 55.0)], ctx(approach=APPROACH)) == 0
    assert follow(lib, track, 11, [target(11, 55.0)], ctx(approach=ceiling)) == 1
    assert track.lastBin == pytest.approx(55.0)
    assert track.followBinsPerS == pytest.approx(ceiling)


# --- a club the band hides: re-emerging beyond it while the track coasts ---------


def dwelling_track(lib):
    """A club that dwells just short of the band (hi 40): four points 0.1 bin
    apart (two per rounded bin, so no same-bin release), frames 2..5: the
    fitted follow speed is ~33 bins/s and the follow window barely moves."""
    cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(cfg))
    for f, bin_ in zip(range(2, 6), (38.3, 38.4, 38.5, 38.6), strict=True):
        arr = (fw.TargetObs * 1)(target(f, bin_))
        lib.l3_track_update(ctypes.byref(track), arr, 1, f, f * FRAME_US)
    assert track.active == 1 and track.count == 4
    return track


def test_a_dwelling_club_re_emerging_beyond_the_band_is_taken_at_once(lib):
    """Frame 11 (15 ms after impact): 41.0 is 400 bins/s from the ball at 35
    -- positive, under 1.1 x approach, slower than the ball -- but beyond the
    follow window (38.6 + 33 bins/s x 18 ms + 0.5 = 39.7). It is appended to
    the same track on the first frame it shows."""
    track = dwelling_track(lib)
    c = ctx()
    for f in range(6, 11):
        assert follow(lib, track, f, [], c) == 0
        assert track.active == 1, f
    total = track.total
    assert follow(lib, track, 11, [target(11, 41.0)], c) == 1
    assert track.active == 1 and track.following == 1
    assert track.misses == 0
    assert track.total == total + 1, "the same track, one point more"
    assert track.lastBin == pytest.approx(41.0)
    assert track.lastFrame == 11
    assert track.lastTargetIndex == 0
    assert (track.sameBin, track.sameBinCount) == (41, 1)
    assert fw.TRACK_WHY_NAMES[track.why] == "associated"


def test_the_strongest_qualifying_return_beyond_the_band_wins(lib):
    track = dwelling_track(lib)
    c = ctx()
    for f in range(6, 11):
        follow(lib, track, f, [], c)
    targets = [
        target(11, 41.0, stat=50.0),
        target(11, 42.0, stat=200.0),
        target(11, 60.0, stat=900.0),  # 1667 bins/s: faster than approach x 1.1
    ]
    assert follow(lib, track, 11, targets, c) == 1
    assert track.lastTargetIndex == 1
    assert track.lastBin == pytest.approx(42.0)


@pytest.mark.parametrize(
    "bin_, kwargs",
    [
        (41.0, {"ball": 300.0}),  # 400 bins/s from the ball: faster than the ball
        (41.0, {"approach": 300.0}),  # 400 > 1.1 x 300
        (41.0, {"claim": 0}),  # the ball's target
        (39.8, {}),  # inside the band: not beyond it (and outside the follow window)
    ],
)
def test_a_dwelling_club_refuses_what_re_acquisition_refuses(lib, bin_, kwargs):
    track = dwelling_track(lib)
    for f in range(6, 11):
        follow(lib, track, f, [], ctx())
    assert follow(lib, track, 11, [target(11, bin_)], ctx(**kwargs)) == 0
    assert fw.TRACK_WHY_NAMES[track.why] == "coasted"


def test_band_off_a_dwelling_club_does_not_jump_to_a_distant_return(lib):
    track = dwelling_track(lib)
    c = ctx(band=False)
    follow(lib, track, 6, [], c)
    assert follow(lib, track, 7, [target(7, 41.0)], c) == 0
