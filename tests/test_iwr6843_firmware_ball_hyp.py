"""Tests for the IWR6843 ball hypotheses, firmware/iwr6843/l3_ball_hyp.c.

After impact the tracker keeps up to four candidate ball trajectories that
start near the origin, assigns each frame's targets to them jointly with the
club track's claim (never the claimed target), and decides later which one is
the ball. Scenes come from tests/iwr6843_twotrack.py.
"""

from __future__ import annotations

import ctypes

import pytest
from iwr6843_twotrack import BIN_M, NO_CLAIM, TwoTracks, obs

from openflight.iwr6843 import firmware_host as fw


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def make_hyps(lib, **overrides):
    cfg = fw.BallHypsCfg()
    lib.l3_ball_hyps_cfg_defaults(ctypes.byref(cfg))
    for name, value in overrides.items():
        setattr(cfg, name, value)
    hyps = fw.BallHyps()
    lib.l3_ball_hyps_init(ctypes.byref(hyps), ctypes.byref(cfg))
    return hyps


def arm(lib, hyps, origin_bin=46.0, gate_us=0):
    lib.l3_ball_hyps_arm(ctypes.byref(hyps), origin_bin, gate_us)


def feed(lib, hyps, frame, timestamp_us, targets, club_index=NO_CLAIM):
    arr = (fw.TargetObs * max(1, len(targets)))(*targets)
    return lib.l3_ball_hyps_update(
        ctypes.byref(hyps), arr, len(targets), frame, timestamp_us, club_index
    )


def run(lib, scene, **overrides):
    hyps = make_hyps(lib, **overrides)
    arm(lib, hyps, scene.origin_bin, scene.gate_us)
    frames = scene.build()
    for f in frames:
        feed(lib, hyps, f.frame, f.timestamp_us, f.targets, f.club_index)
    return hyps, frames


def active(hyps):
    return [hyps.hyp[i] for i in range(fw.BALL_HYP_MAX) if hyps.hyp[i].active]


def bins(hyp):
    return [round(hyp.points[i].rangeBin, 3) for i in range(hyp.count)]


def truth(frames):
    return [round(f.ball_bin, 3) for f in frames if f.ball_bin is not None]


def test_defaults(lib):
    cfg = fw.BallHypsCfg()
    lib.l3_ball_hyps_cfg_defaults(ctypes.byref(cfg))
    assert (cfg.spawnBehindBins, cfg.spawnBeyondBins, cfg.gateBins, cfg.gateMps) == (
        1.0,
        10.0,
        1.5,
        8.0,
    )
    assert (cfg.maxMisses, cfg.classifyPoints, cfg.impactToleranceUs) == (2, 4, 15000)
    assert (cfg.minDepartureMps, cfg.maxSpeedMps) == (10.0, 100.0)
    assert (cfg.maxResidualBins, cfg.dopplerToleranceMps) == (1.0, 2.5)
    assert cfg.binWidthM == pytest.approx(BIN_M)


def test_the_struct_layout_matches_the_c(lib):
    assert ctypes.sizeof(fw.BallHyps) == lib.l3_ball_hyps_struct_bytes()


def test_unarmed_hypotheses_ignore_targets(lib):
    hyps = make_hyps(lib)
    assert feed(lib, hyps, 1, 2000, [obs(1, 2000, 47.0, 1000.0, 40.0)]) == 0
    assert not active(hyps)


def test_the_ball_is_one_hypothesis_and_the_club_claim_none(lib):
    hyps, frames = run(lib, TwoTracks())
    got = active(hyps)
    assert len(got) == 1
    assert bins(got[0]) == truth(frames)
    assert hyps.spawned == 1


def test_only_targets_near_the_origin_start_a_hypothesis(lib):
    hyps = make_hyps(lib)
    arm(lib, hyps)
    targets = [obs(1, 2000, b, 900.0, 5.0) for b in (44.5, 45.2, 55.8, 56.4)]
    feed(lib, hyps, 1, 2000, targets)
    assert sorted(h.points[0].rangeBin for h in active(hyps)) == pytest.approx([45.2, 55.8])


def test_a_merged_first_return_is_a_missed_frame_not_a_point(lib):
    hyps, frames = run(lib, TwoTracks(merged=(1,)))
    (hyp,) = active(hyps)
    assert hyp.points[0].frame == 2
    assert bins(hyp) == truth(frames)


def test_the_ball_coasts_over_two_missing_frames_and_is_picked_up(lib):
    hyps, frames = run(lib, TwoTracks(missing_ball=(3, 4)))
    (hyp,) = active(hyps)
    assert [hyp.points[i].frame for i in range(hyp.count)] == [1, 2, 5, 6, 7, 8]
    assert bins(hyp) == truth(frames)


def test_three_missing_frames_drop_it(lib):
    hyps, _ = run(lib, TwoTracks(missing_ball=(3, 4, 5)))
    assert hyps.dropped == 1
    assert not active(hyps)  # by frame 6 the ball is past the start band


def test_a_ball_return_the_club_claims_is_never_a_ball_point(lib):
    """Filling a gap with the club's return is the failure this module exists to stop."""
    hyps = make_hyps(lib)
    arm(lib, hyps)
    feed(lib, hyps, 1, 2000, [obs(1, 2000, 47.8, 1500.0, 42.0)])
    feed(lib, hyps, 2, 4000, [obs(2, 4000, 49.6, 1500.0, 42.0)])
    feed(lib, hyps, 3, 6000, [obs(3, 6000, 51.4, 9000.0, 42.0)], club_index=0)
    (hyp,) = active(hyps)
    assert (hyp.count, hyp.misses) == (2, 1)


def test_a_full_set_evicts_only_a_single_point_hypothesis(lib):
    hyps = make_hyps(lib)
    arm(lib, hyps)
    first = [obs(1, 2000, b, 1000.0, 30.0) for b in (46.2, 50.0, 53.0, 55.8, 47.5)]
    feed(lib, hyps, 1, 2000, first)  # four slots: the fifth target starts nothing
    assert sorted(round(h.points[0].rangeBin, 1) for h in active(hyps)) == [46.2, 50.0, 53.0, 55.8]
    # 0.5 ms later: 46.2 and 50.0 continue; a new return at 45.3 evicts the
    # oldest single-point hypothesis that missed (53.0), never a two-point one.
    second = [obs(2, 2500, b, 1000.0, 30.0) for b in (46.9, 50.7, 45.3)]
    feed(lib, hyps, 2, 2500, second)
    got = {round(h.points[0].rangeBin, 1): h.count for h in active(hyps)}
    assert got == {46.2: 2, 50.0: 2, 55.8: 1, 45.3: 1}
    assert hyps.dropped == 1


def test_uneven_frame_spacing_is_followed_by_time(lib):
    hyps, frames = run(lib, TwoTracks(timestamps_us=[1000, 2500, 6500, 8000, 12000], frames=5))
    (hyp,) = active(hyps)
    assert bins(hyp) == truth(frames)


def test_set_angles_marks_the_newest_point_this_frame_only(lib):
    hyps = make_hyps(lib)
    arm(lib, hyps)
    feed(lib, hyps, 1, 2000, [obs(1, 2000, 47.8, 1500.0, 42.0)])
    index = next(i for i in range(fw.BALL_HYP_MAX) if hyps.hyp[i].active)
    both = fw.ANGLE_AZIMUTH | fw.ANGLE_ELEVATION
    assert lib.l3_ball_hyps_set_angles(ctypes.byref(hyps), index, 0.1, 0.2, both) == 1
    point = hyps.hyp[index].points[0]
    assert (point.azimuthRad, point.elevationRad) == pytest.approx((0.1, 0.2))
    assert point.anglesValid == both
    feed(lib, hyps, 2, 4000, [])  # nothing appended this frame
    assert lib.l3_ball_hyps_set_angles(ctypes.byref(hyps), index, 0.3, 0.3, both) == 0
    assert lib.l3_ball_hyps_set_angles(ctypes.byref(hyps), fw.BALL_HYP_MAX, 0.3, 0.3, both) == 0


def test_the_fit_reads_the_rate_and_the_range_at_a_reference_time(lib):
    hyps, _ = run(lib, TwoTracks(frames=4))
    (hyp,) = active(hyps)
    rate, at, residual = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    assert lib.l3_ball_hyp_fit(
        ctypes.byref(hyp), 0, ctypes.byref(rate), ctypes.byref(at), ctypes.byref(residual)
    )
    assert rate.value * BIN_M == pytest.approx(42.0, rel=1e-3)
    assert at.value == pytest.approx(46.0, abs=0.01)  # the origin at the gate time
    assert residual.value == pytest.approx(0.0, abs=1e-3)


def verdict(lib, hyps):
    out = fw.BallHypVerdict()
    lib.l3_ball_hyps_classify(ctypes.byref(hyps), ctypes.byref(out))
    return out


def test_no_verdict_before_four_points(lib):
    hyps, _ = run(lib, TwoTracks(frames=3))
    assert verdict(lib, hyps).index == -1


def test_the_ball_hypothesis_is_classified_with_its_speed(lib):
    hyps, _ = run(lib, TwoTracks(frames=6))
    v = verdict(lib, hyps)
    assert v.index >= 0
    assert bins(hyps.hyp[v.index])[0] == pytest.approx(46.0 + 42.0 * 0.002 / BIN_M, abs=0.01)
    assert v.points == 6
    assert v.rateMps == pytest.approx(42.0, rel=0.02)
    assert (v.weakerFraction, v.dopplerAgreement) == (1.0, 1.0)
    assert abs(v.originOffsetUs) < 200.0


def test_a_stationary_return_near_the_origin_is_never_the_ball(lib):
    """The strong stall beside the ball (2026-08-24: bins 38.2-38.4, SNR up to 970)."""
    scene = TwoTracks(missing_ball=tuple(range(1, 9)), extras=[(48.0, 20000.0, 0.8)])
    hyps, _ = run(lib, scene)
    assert active(hyps)  # it is followed as a hypothesis ...
    assert verdict(lib, hyps).index == -1  # ... but it never leaves: not the ball


@pytest.mark.parametrize("offset_us", [-6000, 6000])
def test_the_gate_need_not_be_the_exact_impact(lib, offset_us):
    hyps, _ = run(lib, TwoTracks(frames=10, impact_offset_us=offset_us))
    v = verdict(lib, hyps)
    assert v.index >= 0
    assert v.originOffsetUs == pytest.approx(offset_us, abs=300.0)


def test_a_ball_leaving_far_from_the_gate_time_is_not_the_ball(lib):
    hyps, _ = run(lib, TwoTracks(frames=24, impact_offset_us=30000))
    assert active(hyps)
    assert verdict(lib, hyps).index == -1


def test_the_tighter_of_two_ball_like_hypotheses_wins(lib):
    hyps = make_hyps(lib)
    arm(lib, hyps)
    step = 42.0 * 0.002 / BIN_M
    jitter = [0.0, 0.6, -0.6, 0.6, -0.6, 0.6]
    for k in range(1, 7):
        ts = 2000 * k
        clean = obs(k, ts, 46.0 + step * k, 1500.0, 42.0)
        noisy = obs(k, ts, 50.0 + step * k + jitter[k - 1], 1500.0, 42.0)
        feed(lib, hyps, k, ts, [clean, noisy])
    v = verdict(lib, hyps)
    assert v.index >= 0
    assert bins(hyps.hyp[v.index])[0] == pytest.approx(46.0 + step, abs=0.01)


def test_unarmed_hypotheses_give_no_verdict(lib):
    assert verdict(lib, make_hyps(lib)).index == -1
