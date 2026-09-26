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
        ("isRangeSnapshot", ctypes.c_uint8),
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
    lib.solve_lcmf_debug_leave_one_channel_out_error.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.solve_lcmf_debug_leave_one_channel_out_error.restype = ctypes.c_uint32
    return lib


def _c_leave_one_channel_out_error(bound_lib, a: np.ndarray, y: np.ndarray) -> tuple[float, bool]:
    """Call solve_lcmf.c's TEST-ONLY wrapper around its normal-equations pinv."""
    k = a.shape[1]
    a_re = np.ascontiguousarray(np.real(a), dtype=np.float64)
    a_im = np.ascontiguousarray(np.imag(a), dtype=np.float64)
    y_re = (ctypes.c_double * N_ELEMENTS)(*np.real(y))
    y_im = (ctypes.c_double * N_ELEMENTS)(*np.imag(y))
    err = ctypes.c_double(0.0)
    singular = ctypes.c_int(0)
    bound_lib.solve_lcmf_debug_leave_one_channel_out_error(
        k,
        a_re.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        a_im.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        y_re,
        y_im,
        ctypes.byref(err),
        ctypes.byref(singular),
    )
    return err.value, bool(singular.value)


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
    from openflight.iwr6843.dump import is_range_snapshot
    from openflight.iwr6843.shot import process_dump

    is_range = is_range_snapshot(prepared.full_metadata)

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
        "is_range_snapshot": is_range,
    }


def _run(bound_lib, derived) -> Result:
    result, _ws = _run_with_workspace(bound_lib, derived)
    return result


def _run_with_workspace(bound_lib, derived) -> tuple[Result, "Workspace"]:
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
        isRangeSnapshot=1 if derived["is_range_snapshot"] else 0,
    )

    ws = Workspace()
    result = Result()
    status = bound_lib.solve_lcmf_estimate(ctypes.byref(inp), ctypes.byref(ws), ctypes.byref(result))
    assert status == 0, "solve_lcmf_estimate reported an input-size error"
    return result, ws


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
        "insufficient_late-flight_snapshots",
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


# --- the late-flight guard (lcmf.py:583-584), ported defensively ---------
#
# CRITICAL 1's review flagged this guard as a live divergence: an omitted
# stage's reject can still change `status` since it runs inside the same
# try/except (see solve_lcmf.c's banner). Closer analysis (see the guard's
# own comment in solve_lcmf.c) proves it cannot currently fire: once
# _channel_estimates' own guard has passed (nSelected >= 12, nUniqueFrames
# >= 3), MAX_PER_FRAME=4 forces the late half (>= 6 elements) to span >= 2
# frames. This is checked here against every corpus case that reaches
# "accepted" or "rejected_no_conditioned_channel" (i.e. every case that got
# past the channel guard) using the real `ws` the C port populated -- not
# only trusting the arithmetic proof.


@pytest.mark.parametrize("case", golden_cases(STAGE))
def test_late_flight_guard_is_unreachable_once_channel_guard_passes(bound_lib, case):
    derived = _derive_case(case)
    if str(derived["vectors"]["status"][0]) not in ("accepted", "rejected_no_conditioned_channel"):
        pytest.skip(f"{case}: never reaches the channel guard's pass side")

    result, ws = _run_with_workspace(bound_lib, derived)
    assert result.status.decode() != "insufficient_late-flight_snapshots"

    n_selected = ws.nSelected
    assert n_selected >= 12  # the channel guard already passed

    selected = list(ws.selected[:n_selected])
    ordered = sorted(selected, key=lambda idx: ws.t[idx])
    late_half = ordered[len(ordered) // 2 :]
    assert len(late_half) >= 6, f"{case}: late half smaller than lcmf.py's own 6-snapshot floor"
    late_frames = {ws.frame[idx] for idx in late_half}
    assert len(late_frames) >= 2, (
        f"{case}: late half concentrated in a single frame -- MAX_PER_FRAME's cap should "
        "prevent this once the channel guard has passed"
    )


# --- IMPORTANT 2: normal-equations pinv vs np.linalg.pinv under rank
# deficiency -------------------------------------------------------------
#
# Python's leave_one_channel_out_error (multipath.py:173) uses
# np.linalg.pinv -- SVD with a RELATIVE rcond cutoff -- and returns a finite
# minimum-norm answer even for a badly ill-conditioned dictionary. The C
# port's leave_one_channel_out_error (solve_lcmf.c) instead forms the normal
# equations (gram = A^H A, which SQUARES the condition number) and inverts
# via Gauss-Jordan with an ABSOLUTE 1e-24 pivot floor. These are different
# estimators, not different precisions of the same one, and this measures
# exactly where and how badly they diverge on a deliberately ill-conditioned
# two-column dictionary (the "two8" DD/GG model's own shape) -- the regime a
# real capture reaches when the direct and image multipath returns nearly
# coincide, i.e. a very low launch angle.
#
# Measured here (see the Task 6 report for the full sweep and the
# corpus-derived condition numbers): the two agree to within ~0.1% for
# cond(A) below ~2e6; C's normal equations already return the clip ceiling
# (1e3, "no information") by cond(A) ~ 3e6 -- squaring the condition number
# through A^H*A destroys enough precision in forming the Gram matrix that
# its computed pivot underflows 1e-24 there, well before the dictionary is
# anywhere near truly singular -- while Python's SVD pinv keeps returning a
# small, physically meaningful error all the way past cond(A) = 1e12. A
# scan of the actual 18-case golden corpus's own grid search (every
# angle/model/snapshot _channel_estimates evaluates) finds a REAL
# cond(A) = 5.14e6 in wedge_speed_steep_launch(_ref_calibration) -- past
# this divergence threshold already, at a grid-edge angle (0 deg) that
# happens not to be that case's winning angle. The margin between what the
# existing corpus already reaches and where the two solvers diverge is well
# under 2x, not the several orders of magnitude a "not exercised" claim
# would need -- see IMPORTANT 2's writeup in the Task 6 report for the
# production-plausibility judgment.


def _py_leave_one_channel_out_error(snapshot: np.ndarray, dictionary: np.ndarray) -> np.ndarray:
    """Lazily-imported wrapper: matches _derive_case's own pattern of adding
    src/ to sys.path before importing openflight, so this module still
    collects in environments without an installed openflight package."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from openflight.iwr6843.multipath import leave_one_channel_out_error

    return leave_one_channel_out_error(snapshot, dictionary)


def _ill_conditioned_two8_dictionary(eps: float, *, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """A synthetic 8x2 complex dictionary with two nearly-collinear columns.

    ``eps`` controls how collinear: as eps -> 0, cond(A) -> inf. This is not
    a re-derivation of the physical two8 model -- it directly manufactures
    the failure MODE (near-collinear DD/GG columns) that a near-zero launch
    angle produces there, so the test controls conditioning precisely
    instead of hunting for a (launch_deg, range_m) pair that happens to.
    """
    rng = np.random.default_rng(seed)
    col0 = rng.normal(size=N_ELEMENTS) + 1j * rng.normal(size=N_ELEMENTS)
    col0 /= np.abs(col0).max()
    direction = rng.normal(size=N_ELEMENTS) + 1j * rng.normal(size=N_ELEMENTS)
    direction /= np.abs(direction).max()
    col1 = col0 + eps * direction
    a = np.stack([col0, col1], axis=-1)
    coefficients = np.array([1.0 + 0.3j, 0.7 - 0.2j])
    noise = (rng.normal(size=N_ELEMENTS) + 1j * rng.normal(size=N_ELEMENTS)) * 0.01
    y = a @ coefficients + noise
    return a, y


@pytest.mark.parametrize("eps", [1.0, 1e-2, 1e-4, 1e-6])
def test_normal_equations_pinv_matches_svd_pinv_below_the_divergence_threshold(bound_lib, eps):
    """Below cond(A) ~ 2e6, the two estimators still agree closely."""
    a, y = _ill_conditioned_two8_dictionary(eps)
    cond = np.linalg.cond(a)
    py_error = float(_py_leave_one_channel_out_error(y[None, :], a[None, :, :])[0])
    c_error, c_singular = _c_leave_one_channel_out_error(bound_lib, a, y)
    assert cond < 2e6, f"fixture drifted: cond(A)={cond:.3e} is already past the safe zone"
    assert not c_singular
    assert c_error == pytest.approx(py_error, rel=0.01)


@pytest.mark.parametrize("eps", [1e-8, 1e-10, 1e-12, 0.0])
def test_normal_equations_pinv_diverges_from_svd_pinv_past_the_threshold(bound_lib, eps):
    """Past cond(A) ~ 3e6, the C port's normal equations report "singular"
    (clip ceiling, 1e3) while Python's SVD pinv still returns a small,
    finite, physically meaningful error. This is the divergence IMPORTANT 2
    asked to be measured, locked in as a regression: if a future change to
    solve_lcmf.c's inversion (e.g. switching to an SVD-based pinv) closes
    this gap, this test's failure is the intended signal to update it, not
    a sign something broke.
    """
    a, y = _ill_conditioned_two8_dictionary(eps)
    cond = np.linalg.cond(a)
    py_error = float(_py_leave_one_channel_out_error(y[None, :], a[None, :, :])[0])
    c_error, c_singular = _c_leave_one_channel_out_error(bound_lib, a, y)
    assert cond > 3e6, f"fixture drifted: cond(A)={cond:.3e} is not past the divergence threshold"
    assert c_singular
    assert c_error == pytest.approx(1e3)
    assert py_error < 1.0, (
        "the Python reference should still see a small, physically meaningful error"
    )


def test_corpus_grid_search_reaches_a_condition_number_past_the_divergence_threshold():
    """Empirical grounding for the divergence being production-plausible,
    not merely a synthetic worst case: scan every (model, grid angle,
    snapshot) the real 18-case golden corpus's own _channel_estimates grid
    search evaluates, and confirm the worst dictionary conditioning it
    reaches is past this file's measured ~3e6 divergence threshold.
    """
    from openflight.iwr6843 import lcmf as lcmf_mod
    from openflight.iwr6843.calibration import Calibration as PyCalibration
    from openflight.iwr6843.shot import process_dump

    worst_cond = 0.0
    checked_any = False
    for case in golden_cases(STAGE):
        vectors = load_golden(STAGE, case)
        if str(vectors["status"][0]) != "accepted":
            continue
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
        if shot.track is None or shot.quality == "reject":
            continue
        cache, _geo, _cube = lcmf_mod._snapshot_cache(
            raw,
            shot,
            cal,
            tx_order,
            shot.tdm_sign_used,
            prepared.tdm_tau_s,
            prepared.loop_period_s,
            phase_velocity_ms=ball_speed_mph / lcmf_mod.MPH_PER_MS,
            prepared=vertical,
        )
        indices = lcmf_mod._balanced_indices(cache)
        if len(indices) < 12:
            continue
        checked_any = True
        vertical_delta_m = cal.tee_ball_height_m - cal.radar_height_m
        tee_x_m = math.sqrt(max(cal.tee_range_m**2 - vertical_delta_m**2, 0.25))
        model_geometry = {
            "speed_ms": ball_speed_mph / lcmf_mod.MPH_PER_MS,
            "tee_x_m": tee_x_m,
            "ball_height_m": cal.tee_ball_height_m,
            "radar_height_m": cal.radar_height_m,
            "tilt_rad": cal.tilt_rad,
            "tx_order": tx_order,
            "tdm_tau_s": prepared.tdm_tau_s,
        }
        range_m = cache["r"][indices]
        grid_deg = np.arange(-5.0, 45.0 + grid_step_deg / 2.0, grid_step_deg)
        for model in ("two8", "four4_path_tdm"):
            for angle_deg in grid_deg:
                dictionary = lcmf_mod._spatial_dictionary(
                    model, math.radians(angle_deg), range_m, model_geometry, prepared.tdm_tau_s
                )
                worst_cond = max(worst_cond, float(np.max(np.linalg.cond(dictionary))))

    assert checked_any, "no accepted corpus case reached the channel guard -- corpus regressed"
    assert worst_cond > 3e6, (
        f"worst corpus cond(A)={worst_cond:.3e} no longer reaches the measured divergence "
        "threshold -- IMPORTANT 2's production-plausibility finding may need re-checking"
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
