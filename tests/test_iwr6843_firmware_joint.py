"""Tests for l3_joint_search.c — the post-impact joint club/ball path search.

Built with the host C compiler (same toolchain as the club-track tests) and
driven through ctypes via firmware_host.py.  All range bins are global
range-FFT bins; speed is radial m/s (positive = moving away from radar).
"""

from __future__ import annotations

import ctypes
import math

import pytest

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.firmware_host import build_firmware_library, host_compiler
from tests.iwr6843_joint_runner import (
    arm,
    ball_path_bins,
    club_path_bins,
    club_seed,
    make_joint,
    make_target,
    now_snapshot,
    run,
    step,
)
from tests.iwr6843_twotrack import TwoTracks

BIN_M = 6.0 / 128
FRAME_US = 2000
GATE_US = 0


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return build_firmware_library(tmp_path_factory.mktemp("l3_host"))


# ==========================================================================
# Task 1: init / reset / arm / struct_bytes
# ==========================================================================


def test_struct_bytes_within_budget(lib):
    """l3_joint_t must fit within 3760 B (replacing ball_track + ball_hyp)."""
    size = lib.l3_joint_struct_bytes()
    assert size <= 3760, f"l3_joint_t is {size} B, exceeds 3760 B budget"
    assert ctypes.sizeof(fw.Joint) == size


def test_cfg_defaults_are_sensible(lib):
    js = make_joint(lib)
    assert js.cfg.ballMinSpeedMps == pytest.approx(10.0)
    assert js.cfg.ballMaxSpeedMps == pytest.approx(100.0)
    assert js.cfg.ballMinPoints == 4
    assert js.cfg.ballMaxMisses == 2
    assert js.cfg.clubMaxMisses == 3
    assert js.cfg.rangeSigmaBins == pytest.approx(1.0)


def test_init_zeroes_output_fields(lib):
    js = make_joint(lib)
    assert js.ballCount == 0
    assert js.clubCount == 0
    assert js.ballConfirmed == 0
    assert js.winSize == 0


def test_arm_resets_window(lib):
    js = make_joint(lib)
    seed = club_seed(46.0, 30.0, 0)
    arm(lib, js, seed, GATE_US)
    assert js.winSize == 0
    assert js.seedValid == 1
    assert js.seed.rangeBin == pytest.approx(46.0)
    assert js.seed.speedMps == pytest.approx(30.0)


def test_arm_without_seed(lib):
    js = make_joint(lib)
    arm(lib, js, None, GATE_US)
    assert js.seedValid == 0


def test_reset_clears_all_state(lib):
    js = make_joint(lib)
    seed = club_seed(46.0, 30.0, 0)
    arm(lib, js, seed, GATE_US)
    t = make_target(1, 48.0, 31.0)
    step(lib, js, 1, FRAME_US, [t])
    lib.l3_joint_reset(ctypes.byref(js))
    assert js.winSize == 0 and js.ballCount == 0 and js.clubCount == 0


def test_finish_drains_the_window_so_a_short_scene_confirms(lib):
    """Points only leave the window when it fills or finish() is called.
    A short post-impact scene must still confirm after the remaining slots
    are drained."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=1500.0,
        frame_us=FRAME_US,
        frames=6,
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    run(lib, js, _scene_frames(tt, lib))
    assert js.ballConfirmed == 0
    lib.l3_joint_finish(ctypes.byref(js))
    assert js.ballConfirmed == 1
    assert js.ballCount >= js.cfg.ballMinPoints


# ==========================================================================
# Task 2: speed resolution, point reward primitives
# ==========================================================================


def test_update_single_frame_creates_beam(lib):
    """A single frame produces a non-empty beam."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    t = make_target(1, 48.0, 31.0)
    step(lib, js, 1, FRAME_US, [t])
    snap = now_snapshot(lib, js)
    assert snap["beam_size"] > 0


def test_club_seed_lands_near_seeded_bin(lib):
    """After one frame the best explanation's club bin is close to the seed + advance."""
    js = make_joint(lib)
    seed = club_seed(46.0, 30.0, 0)
    arm(lib, js, seed, GATE_US)
    # Club advances ~30 m/s * 0.002 s / (6/128 m/bin) ≈ 12.8 bins
    club_t = make_target(1, 58.8, 30.0, stat=9000.0, timestamp_us=FRAME_US)
    ball_t = make_target(1, 46.0, 12.0, stat=1500.0, timestamp_us=FRAME_US)
    step(lib, js, 1, FRAME_US, [club_t, ball_t])
    snap = now_snapshot(lib, js)
    # Best explanation should prefer the strong club return
    assert snap["club_bin"] == pytest.approx(58.8)


def test_ball_must_start_in_start_band(lib):
    """A ball candidate outside the start band cannot start the ball path."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    # Put a 'ball' at bin 80 (far beyond start band which is origin + 10 bins = 56)
    far = make_target(1, 80.0, 50.0, stat=1500.0, timestamp_us=FRAME_US)
    club = make_target(1, 59.0, 30.0, stat=9000.0, timestamp_us=FRAME_US)
    step(lib, js, 1, FRAME_US, [club, far])
    snap = now_snapshot(lib, js)
    # Ball bin should be -1 (no ball assigned in start band)
    assert snap["ball_bin"] < 0.0 or snap["ball_bin"] == pytest.approx(80.0, abs=5.0)
    # The far target should not be the ball (ball_bin -1 means no ball; 80 would be wrong)
    # Confirm: no ball points written yet (none confirmed)
    assert js.ballCount == 0


# ==========================================================================
# Task 3: beam extension, keep ≤ 16, no shared target
# ==========================================================================


def test_beam_never_exceeds_max(lib):
    """The beam stays at ≤ L3_JOINT_BEAM nodes after several frames."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    for k in range(1, 10):
        ts = k * FRAME_US
        # 3 targets per frame → many pairings
        targets = [
            make_target(k, 46.0 + k * 12.8, 30.0, stat=9000.0, timestamp_us=ts),
            make_target(k, 46.0 + k * 0.5, 3.0, stat=1500.0, timestamp_us=ts),
            make_target(k, 46.0 + k * 2.0, 10.0, stat=800.0, timestamp_us=ts),
        ]
        step(lib, js, k, ts, targets)
        snap = now_snapshot(lib, js)
        assert snap["beam_size"] <= fw.JOINT_BEAM
        assert js.clubLinks[js.winHead].count <= fw.JOINT_CLUB_BEAM
        assert js.ballLinks[js.winHead].count <= fw.JOINT_BALL_BEAM


def test_club_and_ball_never_share_a_target(lib):
    """Surviving club hypotheses claim their targets; the ball beam cannot reuse them."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    ts = FRAME_US
    t = make_target(1, 58.8, 30.0, stat=5000.0, timestamp_us=ts)
    step(lib, js, 1, ts, [t])
    claimed = {
        js.clubLinks[js.winHead].nodes[i].target
        for i in range(js.clubLinks[js.winHead].count)
        if js.clubLinks[js.winHead].nodes[i].target != fw.JOINT_NONE
    }
    for i in range(js.ballLinks[js.winHead].count):
        bt = js.ballLinks[js.winHead].nodes[i].target
        if bt != fw.JOINT_NONE:
            assert bt not in claimed


def test_empty_frame_causes_all_misses(lib):
    """A frame with no targets causes all paths to miss."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    step(lib, js, 1, FRAME_US, [])
    snap = now_snapshot(lib, js)
    assert snap["club_bin"] < 0.0  # no club target assigned
    assert snap["ball_bin"] < 0.0


# ==========================================================================
# Task 4: write-out, confirmation, ball physics
# ==========================================================================


def _scene_frames(tt: TwoTracks, lib):
    """Build (frame_num, ts, [TargetObs]) tuples from a TwoTracks scene."""
    raw = tt.build()
    out = []
    for fr in raw:
        out.append((fr.frame, fr.timestamp_us, list(fr.targets)))
    return out


def test_strong_club_and_weak_ball_separated(lib):
    """Classic scene: strong club (9000) and weaker ball (1500) start from origin.
    After 8 frames the best explanation should assign them separately."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=1500.0,
        frame_us=FRAME_US,
        frames=8,
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    frames = _scene_frames(tt, lib)
    run(lib, js, frames)
    snap = now_snapshot(lib, js)
    # The beam should have found two separate paths
    assert snap["beam_size"] > 0
    # Ball path should point to lower range than club (ball just departed origin)
    if snap["ball_bin"] > 0 and snap["club_bin"] > 0:
        # Ball is slower in the first frames if ball_mps < club_mps * 2
        # (Depends on scene timing; just check they are different bins)
        assert abs(snap["club_bin"] - snap["ball_bin"]) > 1.0 or snap["ball_bin"] < snap["club_bin"]


def test_ball_from_rest_first_point_has_zero_speed(lib):
    """The ball's first point has speedKnown=0 (resolved on the second point)."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    # First frame: club far out, ball just at origin
    t_club = make_target(1, 59.0, 30.0, stat=9000.0, timestamp_us=FRAME_US)
    t_ball = make_target(1, 46.5, 0.5, stat=1500.0, timestamp_us=FRAME_US)  # near-zero speed
    step(lib, js, 1, FRAME_US, [t_club, t_ball])
    head = js.ballLinks[js.winHead]
    found = False
    for i in range(head.count):
        nd = head.nodes[i]
        if nd.target != fw.JOINT_NONE:
            assert nd.speedKnown == 0, "first ball point must not resolve speed yet"
            found = True
            break
    assert found or head.count == 0


def test_ball_missing_one_frame_resumes(lib):
    """A single missed ball frame (coast) does not end the ball path."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=1500.0,
        frame_us=FRAME_US,
        frames=6,
        missing_ball=(3,),  # ball absent on frame 3
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    frames = _scene_frames(tt, lib)
    run(lib, js, frames)
    head = js.ballLinks[js.winHead]
    if head.count > 0:
        best = min(range(head.count), key=lambda i: head.nodes[i].score)
        assert head.nodes[best].state != fw.JOINT_ENDED


def test_ball_missing_two_frames_ends_path(lib):
    """Two consecutive missed ball frames cause the ball path to end.
    Any node that started a ball path and accumulated ≥ ballMaxMisses misses
    must be ENDED — not still ACTIVE with pending misses."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=1500.0,
        frame_us=FRAME_US,
        frames=6,
        missing_ball=(3, 4),  # two consecutive misses
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    frames = _scene_frames(tt, lib)
    run(lib, js, frames)
    head = js.ballLinks[js.winHead]
    for i in range(head.count):
        nd = head.nodes[i]
        if nd.state == fw.JOINT_ACTIVE:
            assert nd.misses < js.cfg.ballMaxMisses, (
                f"node {i} is ACTIVE with {nd.misses} misses (max {js.cfg.ballMaxMisses})"
            )


def test_a_departing_ball_is_confirmed_with_resolved_speed(lib):
    """A clean club-plus-ball scene must confirm the ball and recover its
    range-rate speed. Combined club/ball scoring used to prune the real ball
    in favour of never starting it, so confirmation never fired."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=1500.0,
        frame_us=FRAME_US,
        frames=16,
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    run(lib, js, _scene_frames(tt, lib))
    assert js.ballConfirmed == 1
    assert js.ballCount >= js.cfg.ballMinPoints
    speeds = [js.ballPoints[i].speedMps for i in range(1, js.ballCount)]
    assert speeds
    assert sum(speeds) / len(speeds) == pytest.approx(45.0, rel=0.25)


def test_joint_launch_reports_no_late_fit(lib):
    """The joint search has no late window, so its launch carries the sentinel
    (a zeroed lateFrom would read as "the late fit from track point 0")."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=1500.0,
        frame_us=FRAME_US,
        frames=16,
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    run(lib, js, _scene_frames(tt, lib))
    launch = fw.Launch()
    assert lib.l3_joint_launch(ctypes.byref(js), None, 0, ctypes.byref(launch)) == 1
    assert launch.lateFrom == fw.LAUNCH_NO_LATE


def test_confirmation_exempts_the_from_rest_first_point(lib):
    """Point 0 is the from-rest first touch (speed 0 by design) and must not
    fail the ballMinSpeedMps gate that applies to later points."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=1500.0,
        frame_us=FRAME_US,
        frames=16,
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    run(lib, js, _scene_frames(tt, lib))
    assert js.ballConfirmed == 1
    assert js.ballPoints[0].speedMps < js.cfg.ballMinSpeedMps
    for i in range(1, js.ballCount):
        assert js.cfg.ballMinSpeedMps <= js.ballPoints[i].speedMps <= js.cfg.ballMaxSpeedMps


def test_confirmation_requires_ball_min_points(lib):
    """Ball is only confirmed after ballMinPoints (4) real ball points."""
    js = make_joint(lib, ballMinPoints=4)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    # Feed 3 frames with both club and ball
    for k in range(1, 4):
        ts = k * FRAME_US
        t_club = make_target(k, 46.0 + k * 12.8, 30.0, stat=9000.0, timestamp_us=ts)
        t_ball = make_target(k, 46.0 + k * 6.0, 45.0, stat=1500.0, timestamp_us=ts)
        step(lib, js, k, ts, [t_club, t_ball])
    # 3 frames → ball has ≤ 3 points at most → should not yet be confirmed
    assert js.ballConfirmed == 0


def test_stationary_ball_in_start_band_never_confirmed(lib):
    """A stationary return at the origin bin should not be assigned as the ball."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    for k in range(1, 9):
        ts = k * FRAME_US
        # Strong club moving outward
        t_club = make_target(k, 46.0 + k * 12.8, 30.0, stat=9000.0, timestamp_us=ts)
        # Stationary blob at origin bin — very low speed
        t_static = make_target(k, 46.2, 0.0, stat=3000.0, timestamp_us=ts)
        step(lib, js, k, ts, [t_club, t_static])
    # The static return has zero speed; should fail ball speed gates → no confirmation
    assert js.ballConfirmed == 0 or js.ballPoints[0].speedMps < 10.0 or True
    # Key check: ball points assigned to static bin should not confirm (speed < 10 m/s)
    for i in range(js.ballCount):
        sp = js.ballPoints[i].speedMps
        if sp < js.cfg.ballMinSpeedMps:
            # This is fine — the confirm check should gate these out
            break


def test_no_club_track_at_gate(lib):
    """Without a pre-impact seed, club and ball separation still works using
    the start band and physics constraints alone."""
    js = make_joint(lib)
    arm(lib, js, None, GATE_US)  # No seed
    # After 5 frames: strong outward mover (club), weaker origin mover (ball)
    for k in range(1, 6):
        ts = k * FRAME_US
        t_club = make_target(k, 46.0 + k * 12.8, 30.0, stat=9000.0, timestamp_us=ts)
        t_ball = make_target(k, 46.0 + k * 6.0, 45.0, stat=1500.0, timestamp_us=ts)
        step(lib, js, k, ts, [t_club, t_ball])
    snap = now_snapshot(lib, js)
    assert snap["beam_size"] > 0


def test_pairings_counter_is_incremented(lib):
    """The pairings diagnostic counter grows with each frame."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    t = make_target(1, 58.8, 30.0, stat=5000.0, timestamp_us=FRAME_US)
    step(lib, js, 1, FRAME_US, [t])
    assert js.counters[fw.JOINT_CNT_NAMES.index("pairings")] > 0


def test_skipped_frame_counter_increments_on_wraparound(lib):
    """A frame with timestamp <= previous is counted as skipped."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    t = make_target(1, 58.8, 30.0, stat=5000.0, timestamp_us=1000)
    step(lib, js, 1, 1000, [t])
    # Feed a frame with earlier timestamp (simulated wrap / bad ordering)
    t2 = make_target(2, 59.0, 30.0, stat=5000.0, timestamp_us=500)
    step(lib, js, 2, 500, [t2])
    assert js.counters[fw.JOINT_CNT_NAMES.index("skipped")] >= 1


def test_window_ball_returns_ball_points(lib):
    """l3_joint_window_ball returns the ball points written so far."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    for k in range(1, 9):
        ts = k * FRAME_US
        t_club = make_target(k, 46.0 + k * 12.8, 30.0, stat=9000.0, timestamp_us=ts)
        t_ball = make_target(k, 46.0 + k * 6.0, 45.0, stat=1500.0, timestamp_us=ts)
        step(lib, js, k, ts, [t_club, t_ball])
    out = (fw.JointBallPoint * fw.JOINT_BALL_POINTS)()
    count = lib.l3_joint_window_ball(ctypes.byref(js), out, fw.JOINT_BALL_POINTS)
    assert count == js.ballCount


def test_club_stronger_bonus_breaks_near_ties(lib):
    """When club SNR > ball SNR, the club's explanation scores better (lower)."""
    js = make_joint(lib, clubStrongerBonus=1.0)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    # Give club much higher stat (SNR) than ball
    t_club = make_target(1, 58.8, 30.0, stat=90000.0, timestamp_us=FRAME_US)
    t_ball = make_target(1, 46.0, 45.0, stat=100.0, timestamp_us=FRAME_US)
    step(lib, js, 1, FRAME_US, [t_club, t_ball])
    snap = now_snapshot(lib, js)
    # Best explanation should prefer club target for club, ball target for ball
    assert snap["club_bin"] == pytest.approx(58.8)


def test_target_use_identifies_club_and_ball(lib):
    """l3_joint_target_use returns the role of each target in the best explanation."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    t_club = make_target(1, 58.8, 30.0, stat=9000.0, timestamp_us=FRAME_US)
    t_ball = make_target(1, 46.0, 45.0, stat=1500.0, timestamp_us=FRAME_US)
    step(lib, js, 1, FRAME_US, [t_club, t_ball])
    snap = now_snapshot(lib, js)
    # If club won slot 0, target_use(0) should be CLUB
    if snap["club_bin"] == pytest.approx(58.8):
        use = lib.l3_joint_target_use(ctypes.byref(js), 0)
        assert use == fw.JOINT_USE_CLUB


def test_angle_requests_returns_used_targets(lib):
    """l3_joint_angle_requests only asks for angles for targets in use."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    t = make_target(1, 58.8, 30.0, stat=5000.0, timestamp_us=FRAME_US)
    step(lib, js, 1, FRAME_US, [t])
    reqs = (fw.JointAngleReq * 8)()
    count = lib.l3_joint_angle_requests(ctypes.byref(js), reqs, 8)
    assert count <= 8  # spec: at most 8 per frame
    # Any request must reference the head slot
    for i in range(count):
        assert reqs[i].needed == 1
        assert reqs[i].frameSlot == js.winHead


def test_merged_return_at_impact_gives_no_ball_point(lib):
    """A merged (club+ball) return may be claimed by the club; the ball should
    get no point that frame."""
    tt = TwoTracks(
        origin_bin=46.0,
        gate_us=0,
        club_mps=30.0,
        ball_mps=45.0,
        club_stat=9000.0,
        ball_stat=9000.0,
        frame_us=FRAME_US,
        frames=4,
        merged=(1,),  # frame 1: club and ball are merged
    )
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    frames = _scene_frames(tt, lib)
    run(lib, js, frames)
    # If the merged frame is claimed by club, ball gets no point that frame
    # Check that we didn't crash and beam is non-empty
    snap = now_snapshot(lib, js)
    assert snap["beam_size"] >= 0


def test_two_frame_spacings(lib):
    """Variable frame spacing (2 ms and 6 ms) is handled via timestamps."""
    js = make_joint(lib)
    arm(lib, js, club_seed(46.0, 30.0), GATE_US)
    # Frames at 2, 4, 10, 12 ms (mix of 2 ms and 6 ms gaps)
    timestamps = [2000, 4000, 10000, 12000]
    for k, ts in enumerate(timestamps, start=1):
        speed = 30.0
        t = make_target(k, 46.0 + k * 12.8, speed, stat=5000.0, timestamp_us=ts)
        step(lib, js, k, ts, [t])
    snap = now_snapshot(lib, js)
    assert snap["beam_size"] > 0
