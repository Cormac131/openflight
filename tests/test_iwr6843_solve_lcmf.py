"""LCMF equivalence: the ported C must match lcmf.estimate_lcmf_v1()'s
vertical launch-angle decision.

solve_lcmf.c is a SCOPED port -- see its header banner and
firmware/iwr6843/solve/solve_lcmf.c's file banner for the audit. It covers
the channel-model vertical launch-angle decision only (status/angle_deg/
raw_angle_deg/channels_used/single_channel/component_std_deg/n_snapshots/
n_frames). It does not cover the fast-time diagnostic models or the
horizontal TX2 proxy -- traced AND measured (see below) to have zero
influence on the fields this test asserts.

The golden vectors under tests/golden/iwr6843/lcmf/ record only
``raw_bytes`` + calibration + call parameters + every LCMFResult field
(including the unported fast/horizontal ones) -- unlike tracking's golden
vectors, which record the already-parsed MTI cube/Geometry directly. This
is because lcmf.py's own inputs are raw dump bytes; the parsing chain
(parse_dump/project_tx_pair/prepare_shot_dump, and process_dump's call into
tracking.find_ball) is NOT one of the four Tasks 5-8 modules and is not
touched by this port. This test therefore re-derives this stage's actual
inputs (the parsed MTI cube, Geometry, and the already-found BallTrack) by
calling the untouched Python reference's own prepare_lcmf_capture/
process_dump -- exactly the same "drive the C entry point with reference-
derived inputs" pattern test_iwr6843_solve_tracking.py uses for its RANSAC
draw callback.
"""

from __future__ import annotations

import ctypes
import math

import numpy as np
import pytest

from tests.test_iwr6843_solve_harness import (
    SOLVE_DIR,
    assert_close,
    assert_text_equal,
    build_solve_lib,
    golden_cases,
    load_golden,
)

STAGE = "lcmf"

MAX_FRAMES = 64
MAX_LOOPS = 16
MAX_BINS = 128
N_RX = 4
N_ELEMENTS = 8

# --- per-field tolerances ----------------------------------------------
#
# angle_deg/raw_angle_deg: the grid search + parabolic refinement runs the
# SAME arithmetic (Newton trajectory iteration, spatial-dictionary
# exponentials, complex normal-equations pinv, median/log/mean objective)
# as the Python reference, in the same accumulation order (the dictionary
# is built and consumed per-snapshot, matching numpy's own elementwise/
# broadcast semantics exactly -- see solve_lcmf.c's candidate_trajectory
# comment). The only source of divergence is per-operation double rounding,
# which does not compound through a discrete grid argmin+3-point-parabola
# refinement the way an iterative solve would. TOL_ANGLE_DEG is set from
# the MEASURED worst-case delta across all 15 "accepted" corpus cases
# (see the task report), with a wide margin: measured worst case was
# ~3e-10 deg; 0.01 deg (the plan's own stated LCMF tolerance) is used here
# rather than tightening to the measured noise floor, so a real algorithmic
# regression is still caught while not chasing double-rounding dust.
TOL_ANGLE_DEG = 0.01

# channel_*_deg (diagnostic per-channel components): same reasoning and
# same measured order of magnitude as angle_deg above.
TOL_CHANNEL_DEG = 0.01

# component_std_deg: derived from the same two channel angles above by one
# subtraction/sqrt -- no new error source, same tolerance.
TOL_STD_DEG = 0.01


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return build_solve_lib(
        tmp_path_factory,
        [SOLVE_DIR / "solve_lcmf.c"],
        "solve_lcmf",
    )


class Layout(ctypes.Structure):
    _fields_ = [
        ("nFrames", ctypes.c_uint32),
        ("nLoops", ctypes.c_uint32),
        ("nBins", ctypes.c_uint32),
        ("nRx", ctypes.c_uint32),
        ("triggerFrame", ctypes.c_uint32),
        ("framePeriodS", ctypes.c_double),
        ("loopPeriodS", ctypes.c_double),
        ("rangeResM", ctypes.c_double),
        ("rangeBinStart", ctypes.c_int32),
        ("frameTimeOffsetsS", ctypes.POINTER(ctypes.c_double)),
    ]


class Track(ctypes.Structure):
    _fields_ = [
        ("slopeBins", ctypes.c_double),
        ("interceptBins", ctypes.c_double),
        ("tFirstS", ctypes.c_double),
        ("tLastS", ctypes.c_double),
    ]


class Calibration(ctypes.Structure):
    _fields_ = [
        ("elemCorrRe", ctypes.c_double * N_ELEMENTS),
        ("elemCorrIm", ctypes.c_double * N_ELEMENTS),
        ("tiltRad", ctypes.c_double),
        ("rangeBiasM", ctypes.c_double),
        ("teeRangeM", ctypes.c_double),
        ("teeBallHeightM", ctypes.c_double),
        ("radarHeightM", ctypes.c_double),
    ]


class Input(ctypes.Structure):
    _fields_ = [
        ("layout", Layout),
        ("mtiRe", ctypes.POINTER(ctypes.c_double)),
        ("mtiIm", ctypes.POINTER(ctypes.c_double)),
        ("noisePower", ctypes.c_double),
        ("trackFound", ctypes.c_uint8),
        ("qualityReject", ctypes.c_uint8),
        ("track", Track),
        ("cal", Calibration),
        ("ballSpeedMph", ctypes.c_double),
        ("txOrderReversed", ctypes.c_uint8),
        ("tdmSign", ctypes.c_int32),
        ("tdmTauS", ctypes.c_double),
        ("gridStepDeg", ctypes.c_double),
    ]


class Result(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_char * 40),
        ("angleDeg", ctypes.c_double),
        ("rawAngleDeg", ctypes.c_double),
        ("channelTwo8Deg", ctypes.c_double),
        ("channelFour4PathTdmDeg", ctypes.c_double),
        ("hasChannelTwo8", ctypes.c_uint8),
        ("hasChannelFour4PathTdm", ctypes.c_uint8),
        ("componentStdDeg", ctypes.c_double),
        ("singleChannel", ctypes.c_uint8),
        ("nChannelsUsed", ctypes.c_uint32),
        ("channelsUsed", (ctypes.c_char * 32) * 2),
        ("nSnapshots", ctypes.c_uint32),
        ("nFrames", ctypes.c_uint32),
    ]


MAX_SNAPSHOTS = MAX_FRAMES * MAX_LOOPS
MAX_SELECTED = MAX_FRAMES * 4


class Workspace(ctypes.Structure):
    _fields_ = [
        ("t", ctypes.c_double * MAX_SNAPSHOTS),
        ("frame", ctypes.c_uint16 * MAX_SNAPSHOTS),
        ("loopIdx", ctypes.c_uint16 * MAX_SNAPSHOTS),
        ("r", ctypes.c_double * MAX_SNAPSHOTS),
        ("snr", ctypes.c_double * MAX_SNAPSHOTS),
        ("vr", ctypes.c_double * MAX_SNAPSHOTS),
        ("vecRe", (ctypes.c_double * N_ELEMENTS) * MAX_SNAPSHOTS),
        ("vecIm", (ctypes.c_double * N_ELEMENTS) * MAX_SNAPSHOTS),
        ("nCache", ctypes.c_uint32),
        ("selected", ctypes.c_uint16 * MAX_SELECTED),
        ("nSelected", ctypes.c_uint32),
    ]


@pytest.fixture(scope="module")
def bound_lib(lib):
    lib.solve_lcmf_estimate.argtypes = [
        ctypes.POINTER(Input),
        ctypes.POINTER(Workspace),
        ctypes.POINTER(Result),
    ]
    lib.solve_lcmf_estimate.restype = ctypes.c_uint32
    return lib


def _derive_case(case: str):
    """Re-derive this stage's real inputs from the recorded raw capture.

    Uses the untouched Python reference (prepare_lcmf_capture/process_dump)
    to get to the same point in the pipeline estimate_lcmf_v1() itself is
    at just before _snapshot_cache runs -- the parsed MTI cube, Geometry,
    and already-found BallTrack. Everything AFTER that point is what
    solve_lcmf.c actually ports and this test checks.
    """
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from openflight.iwr6843 import lcmf as lcmf_mod
    from openflight.iwr6843.calibration import Calibration as PyCalibration

    vectors = load_golden(STAGE, case)
    raw = bytes(vectors["raw_bytes"])
    cal = PyCalibration(
        elem_correction=vectors["cal_elem_correction_re"] + 1j * vectors["cal_elem_correction_im"],
        tilt_rad=float(vectors["cal_tilt_rad"][0]),
        range_bias_m=float(vectors["cal_range_bias_m"][0]),
        tee_range_m=(
            float(vectors["cal_tee_range_m"][0])
            if not math.isnan(vectors["cal_tee_range_m"][0])
            else None
        ),
        tee_ball_height_m=float(vectors["cal_tee_ball_height_m"][0]),
    )
    cal.meta["radar_height_m"] = float(vectors["cal_radar_height_m"][0])
    ball_speed_mph = float(vectors["ball_speed_mph"][0])
    net_range_m = (
        float(vectors["net_range_m"][0]) if not math.isnan(vectors["net_range_m"][0]) else None
    )
    tx_order = str(vectors["tx_order"][0])
    tdm_sign_policy = str(vectors["tdm_sign_policy"][0])
    grid_step_deg = float(vectors["grid_step_deg"][0])
    club_name = str(vectors["club"][0]) or None

    prepared = lcmf_mod.prepare_lcmf_capture(raw)
    vertical = prepared.vertical
    from openflight.iwr6843.shot import process_dump

    shot = process_dump(
        raw,
        cal,
        club=club_name,
        net_range_m=net_range_m,
        tx_order=tx_order,
        tdm_sign_policy=tdm_sign_policy,
        loop_period_s=prepared.loop_period_s,
        tdm_tau_s=prepared.tdm_tau_s,
        prepared=vertical,
    )

    scope = "window" if shot.notch_recovered else "burst"
    mti = vertical.mti(scope)
    noise = vertical.noise_power(scope)
    geo = vertical.geometry

    return {
        "vectors": vectors,
        "geo": geo,
        "mti": mti,
        "noise": noise,
        "shot": shot,
        "cal": cal,
        "ball_speed_mph": ball_speed_mph,
        "tx_order": tx_order,
        "grid_step_deg": grid_step_deg,
        "_tdm_tau_s": prepared.tdm_tau_s,
    }


def _run(bound_lib, derived) -> Result:
    geo = derived["geo"]
    mti = derived["mti"]
    shot = derived["shot"]
    cal = derived["cal"]

    layout = Layout(
        nFrames=geo.n_frames,
        nLoops=geo.n_loops,
        nBins=geo.n_samples,
        nRx=geo.n_rx,
        triggerFrame=geo.trigger_frame,
        framePeriodS=geo.frame_period_s,
        loopPeriodS=geo.loop_period_s,
        rangeResM=geo.range_res_m,
        rangeBinStart=geo.range_bin_start,
        frameTimeOffsetsS=None,
    )

    mti_re = np.ascontiguousarray(np.real(mti), dtype=np.float64)
    mti_im = np.ascontiguousarray(np.imag(mti), dtype=np.float64)
    mti_re_ptr = mti_re.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    mti_im_ptr = mti_im.ctypes.data_as(ctypes.POINTER(ctypes.c_double))

    track = shot.track
    track_struct = Track(
        slopeBins=track.slope_bins if track is not None else 0.0,
        interceptBins=track.intercept_bins if track is not None else 0.0,
        tFirstS=track.t_first if track is not None else 0.0,
        tLastS=track.t_last if track is not None else 0.0,
    )

    elem_re = (ctypes.c_double * N_ELEMENTS)(*np.real(cal.elem_correction))
    elem_im = (ctypes.c_double * N_ELEMENTS)(*np.imag(cal.elem_correction))
    cal_struct = Calibration(
        elemCorrRe=elem_re,
        elemCorrIm=elem_im,
        tiltRad=cal.tilt_rad,
        rangeBiasM=cal.range_bias_m,
        teeRangeM=cal.tee_range_m,
        teeBallHeightM=cal.tee_ball_height_m,
        radarHeightM=cal.radar_height_m,
    )

    tdm_sign = shot.tdm_sign_used if shot.tdm_sign_used is not None else 0

    inp = Input(
        layout=layout,
        mtiRe=mti_re_ptr,
        mtiIm=mti_im_ptr,
        noisePower=derived["noise"],
        trackFound=1 if track is not None else 0,
        qualityReject=1 if shot.quality == "reject" else 0,
        track=track_struct,
        cal=cal_struct,
        ballSpeedMph=derived["ball_speed_mph"],
        txOrderReversed=1 if derived["tx_order"] == "reversed" else 0,
        tdmSign=tdm_sign,
        tdmTauS=derived["_tdm_tau_s"],
        gridStepDeg=derived["grid_step_deg"],
    )

    ws = Workspace()
    result = Result()
    status = bound_lib.solve_lcmf_estimate(ctypes.byref(inp), ctypes.byref(ws), ctypes.byref(result))
    assert status == 0, "solve_lcmf_estimate reported an input-size error"
    return result


@pytest.mark.parametrize("case", golden_cases(STAGE))
def test_matches_the_python_reference(bound_lib, case):
    derived = _derive_case(case)
    result = _run(bound_lib, derived)

    vectors = derived["vectors"]
    expected_status = str(vectors["status"][0])
    ported_statuses = {
        "rejected_by_ball_tracker",
        "rejected_track_quality",
        "rejected_missing_tdm_sign",
        "insufficient_channel_snapshots",
        "rejected_no_conditioned_channel",
        "accepted",
    }
    assert expected_status in ported_statuses, (
        f"{case}: reference status {expected_status!r} is outside this scoped port's "
        "covered set -- see solve_lcmf.h's banner for what is deferred"
    )
    assert_text_equal(result.status.decode(), expected_status, label=f"{STAGE}/{case}/status")

    if expected_status != "accepted":
        return

    assert_close(result.angleDeg, vectors["angle_deg"], tol=TOL_ANGLE_DEG, label=f"{STAGE}/{case}/angle_deg")
    assert_close(
        result.rawAngleDeg, vectors["raw_angle_deg"], tol=TOL_ANGLE_DEG, label=f"{STAGE}/{case}/raw_angle_deg"
    )
    assert_close(
        result.componentStdDeg,
        vectors["component_std_deg"],
        tol=TOL_STD_DEG,
        label=f"{STAGE}/{case}/component_std_deg",
    )
    assert result.singleChannel == int(vectors["single_channel"][0]), (
        f"{STAGE}/{case}: single_channel mismatch"
    )
    assert result.nSnapshots == int(vectors["n_snapshots"][0]), f"{STAGE}/{case}: n_snapshots mismatch"
    assert result.nFrames == int(vectors["n_frames"][0]), f"{STAGE}/{case}: n_frames mismatch"

    expected_used = list(vectors["channels_used"])
    actual_used = [result.channelsUsed[i].value.decode() for i in range(result.nChannelsUsed)]
    assert actual_used == expected_used, (
        f"{STAGE}/{case}: channels_used mismatch (got {actual_used}, reference {expected_used})"
    )

    expected_components = dict(zip(vectors["component_names"], vectors["component_values_deg"]))
    if "channel_two8_deg" in expected_components:
        assert_close(
            result.channelTwo8Deg,
            expected_components["channel_two8_deg"],
            tol=TOL_CHANNEL_DEG,
            label=f"{STAGE}/{case}/channel_two8_deg",
        )
    if "channel_four4_path_tdm_deg" in expected_components:
        assert_close(
            result.channelFour4PathTdmDeg,
            expected_components["channel_four4_path_tdm_deg"],
            tol=TOL_CHANNEL_DEG,
            label=f"{STAGE}/{case}/channel_four4_path_tdm_deg",
        )


# --- bounds: test this stage's own guards -------------------------------


def _minimal_accepted_derived():
    """The corpus's smallest/simplest accepted case, reused as a base for
    the bounds tests below (they mutate layout sizes, not the physics)."""
    for case in golden_cases(STAGE):
        derived = _derive_case(case)
        if str(derived["vectors"]["status"][0]) == "accepted":
            return derived
    pytest.skip("no accepted lcmf case in the corpus")


def test_rejects_over_limit_frames_without_truncating(bound_lib):
    derived = _minimal_accepted_derived()
    geo = derived["geo"]
    mti = derived["mti"]
    cal = derived["cal"]
    track = derived["shot"].track

    layout = Layout(
        nFrames=MAX_FRAMES + 1,  # over limit
        nLoops=geo.n_loops,
        nBins=geo.n_samples,
        nRx=geo.n_rx,
        triggerFrame=geo.trigger_frame,
        framePeriodS=geo.frame_period_s,
        loopPeriodS=geo.loop_period_s,
        rangeResM=geo.range_res_m,
        rangeBinStart=geo.range_bin_start,
        frameTimeOffsetsS=None,
    )
    mti_re = np.ascontiguousarray(np.real(mti), dtype=np.float64)
    mti_im = np.ascontiguousarray(np.imag(mti), dtype=np.float64)
    elem_re = (ctypes.c_double * N_ELEMENTS)(*np.real(cal.elem_correction))
    elem_im = (ctypes.c_double * N_ELEMENTS)(*np.imag(cal.elem_correction))
    inp = Input(
        layout=layout,
        mtiRe=mti_re.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        mtiIm=mti_im.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        noisePower=derived["noise"],
        trackFound=1,
        qualityReject=0,
        track=Track(
            slopeBins=track.slope_bins,
            interceptBins=track.intercept_bins,
            tFirstS=track.t_first,
            tLastS=track.t_last,
        ),
        cal=Calibration(
            elemCorrRe=elem_re,
            elemCorrIm=elem_im,
            tiltRad=cal.tilt_rad,
            rangeBiasM=cal.range_bias_m,
            teeRangeM=cal.tee_range_m,
            teeBallHeightM=cal.tee_ball_height_m,
            radarHeightM=cal.radar_height_m,
        ),
        ballSpeedMph=derived["ball_speed_mph"],
        txOrderReversed=0,
        tdmSign=derived["shot"].tdm_sign_used or 1,
        tdmTauS=derived["_tdm_tau_s"],
        gridStepDeg=derived["grid_step_deg"],
    )
    ws = Workspace()
    result = Result()
    # Pre-seed sentinel values -- a truncating bug would leave these intact
    # under a "success" status rather than a zeroed/error result.
    result.nSnapshots = 999
    result.angleDeg = 12345.0
    status = bound_lib.solve_lcmf_estimate(ctypes.byref(inp), ctypes.byref(ws), ctypes.byref(result))
    assert status == 1  # SOLVE_LCMF_ERROR
    assert result.nSnapshots == 0
    assert result.status == b""


def test_accepts_exactly_at_limit_frames(bound_lib):
    """nFrames at the literal ceiling (with an all-zero MTI cube) must not
    error -- only over-limit sizes should be rejected."""
    layout = Layout(
        nFrames=MAX_FRAMES,
        nLoops=MAX_LOOPS,
        nBins=MAX_BINS,
        nRx=N_RX,
        triggerFrame=0,
        framePeriodS=4e-3,
        loopPeriodS=90e-6,
        rangeResM=6.0 / MAX_BINS,
        rangeBinStart=0,
        frameTimeOffsetsS=None,
    )
    size = MAX_FRAMES * 2 * MAX_LOOPS * N_RX * MAX_BINS
    zeros = np.zeros(size, dtype=np.float64)
    elem_re = (ctypes.c_double * N_ELEMENTS)(*([1.0] * N_ELEMENTS))
    elem_im = (ctypes.c_double * N_ELEMENTS)(*([0.0] * N_ELEMENTS))
    inp = Input(
        layout=layout,
        mtiRe=zeros.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        mtiIm=zeros.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        noisePower=1.0,
        trackFound=1,
        qualityReject=0,
        track=Track(slopeBins=0.1, interceptBins=10.0, tFirstS=0.0, tLastS=1e-3),
        cal=Calibration(
            elemCorrRe=elem_re,
            elemCorrIm=elem_im,
            tiltRad=0.0,
            rangeBiasM=0.0,
            teeRangeM=1.5,
            teeBallHeightM=0.152,
            radarHeightM=0.152,
        ),
        ballSpeedMph=100.0,
        txOrderReversed=0,
        tdmSign=1,
        tdmTauS=45e-6,
        gridStepDeg=0.5,
    )
    ws = Workspace()
    result = Result()
    status = bound_lib.solve_lcmf_estimate(ctypes.byref(inp), ctypes.byref(ws), ctypes.byref(result))
    assert status == 0
    # An all-zero cube has no snapshots inside the (bogus) track window at
    # this rangeResM/slope -- what matters is that the call did not error.
    assert result.status.decode() in (
        "insufficient_channel_snapshots",
        "accepted",
        "rejected_no_conditioned_channel",
    )
