"""Generate per-stage golden vectors from SYNTHETIC IWR6843 captures.

Usage:
    uv run python scripts/dev/generate_golden_vectors.py [--out tests/golden/iwr6843]

There are no saved session dumps in this repo (no ``~/openflight_sessions/``,
no ``.ild1`` files) to extract vectors from. Every case here is instead built
by ``synth_shot()`` / a local ``synth_club_dump()`` (both adapted from the
existing test fixtures in ``tests/test_iwr6843_pipeline.py`` and
``tests/test_iwr6843_club_path.py``) and run through the REAL Python
reference implementations in ``src/openflight/iwr6843/{tracking,lcmf,
late_window,club}.py``. Nothing here is extracted from real hardware.

This proves C-vs-Python numerical equivalence once each stage is ported to
C99 -- that is the harness's whole job. It does NOT prove real-world
representativeness (true SNR, multipath, unusual clubs). End-to-end accuracy
validation against the real TrackMan corpus remains outstanding and
hardware-gated; see tests/golden/iwr6843/README.md.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from iwr6843_synth import FRAME_PERIOD_S as CLUB_FRAME_PERIOD_S, synth_club_dump  # noqa: E402
from test_iwr6843_pipeline import synth_shot  # noqa: E402

from openflight.iwr6843 import (  # noqa: E402
    club,
    late_window as lw,  # noqa: E402
    lcmf as lcmf_mod,  # noqa: E402
    tracking,
)
from openflight.iwr6843.calibration import Calibration  # noqa: E402
from openflight.iwr6843.dump import (  # noqa: E402
    SAMPLE_RANGE_FFT_IQ16,
    pack_dump,
    parse_dump,
    project_tx_pair,
)
from openflight.iwr6843.shot import prepare_shot_dump  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "golden" / "iwr6843"

# --- generic (de)serialization helpers --------------------------------------


def _scalar(value, dtype=np.float64) -> np.ndarray:
    """A 0-d/1-elem numeric field. ``None`` becomes NaN (no pickled objects)."""
    return np.array([np.nan if value is None else value], dtype=dtype)


def _flag(value) -> np.ndarray:
    return np.array([1 if value else 0], dtype=np.int64)


def _text(value: str | None, maxlen: int = 64) -> np.ndarray:
    return np.array([value if value is not None else ""], dtype=f"<U{maxlen}")


def _text_list(values, maxlen: int = 32) -> np.ndarray:
    values = list(values)
    if not values:
        return np.zeros((0,), dtype=f"<U{maxlen}")
    return np.array(values, dtype=f"<U{maxlen}")


def _arr(value, dtype=np.float64) -> np.ndarray:
    if value is None:
        return np.zeros((0,), dtype=dtype)
    return np.asarray(value, dtype=dtype)


def _cal_fields(cal: Calibration, prefix: str = "cal_") -> dict[str, np.ndarray]:
    return {
        f"{prefix}elem_correction_re": np.real(cal.elem_correction).astype(np.float64),
        f"{prefix}elem_correction_im": np.imag(cal.elem_correction).astype(np.float64),
        f"{prefix}tilt_rad": _scalar(cal.tilt_rad),
        f"{prefix}range_bias_m": _scalar(cal.range_bias_m),
        f"{prefix}tee_range_m": _scalar(cal.tee_range_m),
        f"{prefix}tee_ball_height_m": _scalar(cal.tee_ball_height_m),
        f"{prefix}radar_height_m": _scalar(cal.radar_height_m),
    }


def _reference_cal(*, tee_range_m: float = 1.5, tilt_deg: float = 10.4) -> Calibration:
    """The fixture calibration shared by ``test_iwr6843_pipeline.py``."""
    cal = Calibration.identity()
    cal.tilt_rad = math.radians(tilt_deg)
    cal.tee_range_m = tee_range_m
    radar_height_m = 0.152
    cal.tee_ball_height_m = radar_height_m
    cal.meta["radar_height_m"] = radar_height_m
    return cal


def _ref_cal(*, tee_range_m: float = 1.5) -> Calibration:
    """The real shipped array calibration (config/iwr6843_calibration_reference.json).

    Every other case in this corpus uses ``Calibration.identity()``, whose
    ``elem_correction`` is all-ones -- so a C port that omits the per-element
    calibration correction entirely (``lcmf.py:592``, consumed via
    ``Calibration.apply`` at ``calibration.py:81``) is indistinguishable from
    a correct one on every one of those cases. The ``*_ref_calibration``
    cases built with this loader close that gap: see their generation sites
    for the measured before/after deltas this was confirmed to catch.
    """
    cal = Calibration.load()
    cal.tee_range_m = tee_range_m
    return cal


def _write(out_dir: Path, case: str, arrays: dict[str, np.ndarray]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case}.npz"
    np.savez_compressed(path, **arrays)
    return path


# --- tracking stage: tracking.find_ball(mti, geo, ...) ----------------------
#
# This mirrors exactly what shot.process_dump does before calling find_ball:
# project a 3TX capture down to the 2TX vertical pair, then build the MTI
# cube from the decoded dump. find_ball is tracking.py's own headline entry
# point (see its module docstring); find_ball_from_power one layer below is
# already covered on real hardware by firmware/iwr6843/track_select.c.


def _tracking_inputs(raw: bytes, *, tx_order: str = "normal"):
    meta0, _ = parse_dump(raw)
    loop_period_s = tracking.LOOP_PRI_S
    if meta0["n_tx"] == 3:
        raw = project_tx_pair(raw, (0, 2))
        loop_period_s = lcmf_mod.TX2_LOOP_PERIOD_S
    prepared = prepare_shot_dump(raw, loop_period_s=loop_period_s)
    return prepared.mti(), prepared.geometry


def _geo_fields(geo: tracking.Geometry) -> dict[str, np.ndarray]:
    return {
        "geo_n_frames": _scalar(geo.n_frames, np.int64),
        "geo_chirps_per_frame": _scalar(geo.chirps_per_frame, np.int64),
        "geo_n_tx": _scalar(geo.n_tx, np.int64),
        "geo_n_rx": _scalar(geo.n_rx, np.int64),
        "geo_n_samples": _scalar(geo.n_samples, np.int64),
        "geo_frame_period_s": _scalar(geo.frame_period_s),
        "geo_trigger_frame": _scalar(geo.trigger_frame, np.int64),
        "geo_loop_period_s": _scalar(geo.loop_period_s),
        "geo_range_bin_start": _scalar(geo.range_bin_start, np.int64),
        "geo_range_fft_size": _scalar(geo.range_fft_size, np.float64),
        "geo_range_bin_starts": _arr(geo.range_bin_starts, np.int64),
        "geo_range_bin_counts": _arr(geo.range_bin_counts, np.int64),
        "geo_frame_time_offsets_s": _arr(geo.frame_time_offsets_s),
    }


def _track_fields(track: tracking.BallTrack | None) -> dict[str, np.ndarray]:
    if track is None:
        return {
            "found": _flag(False),
            "speed_ms": _scalar(None),
            "slope_bins": _scalar(None),
            "intercept_bins": _scalar(None),
            "rms_bins": _scalar(None),
            "n_inliers": _scalar(None),
            "t_first": _scalar(None),
            "t_last": _scalar(None),
            "low_confidence": _flag(False),
            "quad_bins": _arr(None),
        }
    return {
        "found": _flag(True),
        "speed_ms": _scalar(track.speed_ms),
        "slope_bins": _scalar(track.slope_bins),
        "intercept_bins": _scalar(track.intercept_bins),
        "rms_bins": _scalar(track.rms_bins),
        "n_inliers": _scalar(track.n_inliers, np.int64),
        "t_first": _scalar(track.t_first),
        "t_last": _scalar(track.t_last),
        "low_confidence": _flag(track.low_confidence),
        "quad_bins": _arr(track.quad_bins) if track.quad_bins is not None else np.full(3, np.nan),
    }


def generate_tracking_case(
    out_dir: Path, case: str, *, max_range_m=None, min_ball_ms=None, **synth_kw
):
    raw = synth_shot(**synth_kw)
    tx_order = synth_kw.get("tx_order", "normal")
    mti, geo = _tracking_inputs(raw, tx_order=tx_order)
    kwargs = {}
    if min_ball_ms is not None:
        kwargs["min_ball_ms"] = min_ball_ms
    track = tracking.find_ball(mti, geo, max_range_m=max_range_m, **kwargs)
    # float32 storage: the mti cube is a C-port INPUT, not a value under test,
    # and its ~1e-7 relative rounding is far below the real ADC's 16-bit
    # resolution and the RANSAC gates' bin-scale tolerances -- harmless for
    # equivalence testing, and it roughly halves this stage's corpus size.
    arrays = {
        "mti_re": np.real(mti).astype(np.float32),
        "mti_im": np.imag(mti).astype(np.float32),
        "max_range_m": _scalar(max_range_m),
        "min_ball_ms": _scalar(min_ball_ms if min_ball_ms is not None else tracking.FAST_TRACK_MS),
        **_geo_fields(geo),
        **_track_fields(track),
    }
    return _write(out_dir, case, arrays)


# --- lcmf stage: lcmf.estimate_lcmf_v1(raw, cal, ball_speed_mph=...) --------


def _lcmf_result_fields(result: lcmf_mod.LCMFResult) -> dict[str, np.ndarray]:
    names = sorted(result.components_deg)
    values = [result.components_deg[name] for name in names]
    return {
        "status": _text(result.status),
        "angle_deg": _scalar(result.angle_deg),
        "raw_angle_deg": _scalar(result.raw_angle_deg),
        "component_names": _text_list(names),
        "component_values_deg": _arr(values) if values else np.zeros((0,)),
        "n_snapshots": _scalar(result.n_snapshots, np.int64),
        "n_frames": _scalar(result.n_frames, np.int64),
        "component_std_deg": _scalar(result.component_std_deg),
        "channels_used": _text_list(result.channels_used),
        "single_channel": _flag(result.single_channel),
        "tracker_quality": _text(result.tracker_quality),
        "track_speed_mph": _scalar(result.track_speed_mph),
        "track_rms_bins": _scalar(result.track_rms_bins),
        "track_inliers": _scalar(result.track_inliers, np.float64),
        "track_span_s": _scalar(result.track_span_s),
        "impact_t_s": _scalar(result.impact_t_s),
        "tdm_sign_used": _scalar(result.tdm_sign_used, np.float64),
        "horizontal_deg": _scalar(result.horizontal_deg),
        "horizontal_raw_deg": _scalar(result.horizontal_raw_deg),
        "horizontal_confidence": _scalar(result.horizontal_confidence),
        "horizontal_status": _text(result.horizontal_status),
        "effective_tdm_tau_s": _scalar(result.effective_tdm_tau_s),
        "effective_loop_period_s": _scalar(result.effective_loop_period_s),
    }


def generate_lcmf_case(
    out_dir: Path,
    case: str,
    *,
    ball_speed_mph: float,
    club_name: str | None = None,
    net_range_m: float | None = None,
    tx_order: str = "normal",
    tdm_sign_policy: str = "positive",
    grid_step_deg: float = 0.5,
    cal: Calibration | None = None,
    **synth_kw,
):
    raw = synth_shot(tx_order=tx_order, **synth_kw)
    cal = cal or _reference_cal()
    result = lcmf_mod.estimate_lcmf_v1(
        raw,
        cal,
        ball_speed_mph=ball_speed_mph,
        club=club_name,
        net_range_m=net_range_m,
        tx_order=tx_order,
        tdm_sign_policy=tdm_sign_policy,
        grid_step_deg=grid_step_deg,
    )
    arrays = {
        "raw_bytes": np.frombuffer(raw, dtype=np.uint8).copy(),
        "ball_speed_mph": _scalar(ball_speed_mph),
        "net_range_m": _scalar(net_range_m),
        "tx_order": _text(tx_order),
        "tdm_sign_policy": _text(tdm_sign_policy),
        "grid_step_deg": _scalar(grid_step_deg),
        "club": _text(club_name),
        **_cal_fields(cal),
        **_lcmf_result_fields(result),
    }
    return _write(out_dir, case, arrays)


# --- club stage: club.estimate_club_path(raw, cal, ...) ---------------------
#
# synth_shot() only ever simulates the ball -- it has no club return at all,
# so it cannot exercise estimate_club_path's positive path (there is nothing
# pre-impact for find_club to lock onto; every synth_shot capture rejects
# with "rejected_no_club_track"). club.py needs its own synthetic capture, so
# this uses tests/iwr6843_synth.py's ``synth_club_dump`` -- the SAME function
# tests/test_iwr6843_club_path.py runs its assertions against (straight-line
# Cartesian club motion crossing the tee at the moment of impact, exact TDM +
# Doppler phase per TX) -- rather than a second, driftable copy of it.


def _club_result_fields(result: club.ClubPathResult) -> dict[str, np.ndarray]:
    return {
        "status": _text(result.status),
        "path_deg": _scalar(result.path_deg),
        "candidate_path_deg": _scalar(result.candidate_path_deg),
        "candidate_path_status": _text(result.candidate_path_status),
        "candidate_path_fit_residual_deg": _scalar(result.candidate_path_fit_residual_deg),
        "candidate_attack_angle_deg": _scalar(result.candidate_attack_angle_deg),
        "attack_angle_status": _text(result.attack_angle_status),
        "attack_fit_rms_m": _scalar(result.attack_fit_rms_m),
        "attack_n_points": _scalar(result.attack_n_points, np.int64),
        "confidence": _scalar(result.confidence),
        "azimuth_rate_dps": _scalar(result.azimuth_rate_dps),
        "range_rate_ms": _scalar(result.range_rate_ms),
        "club_range_m": _scalar(result.club_range_m),
        "n_frames": _scalar(result.n_frames, np.int64),
        "n_snapshots": _scalar(result.n_snapshots, np.int64),
        "n_rejected_snapshots": _scalar(result.n_rejected_snapshots, np.int64),
        "phase_span_rad": _scalar(result.phase_span_rad),
        "fit_residual_deg": _scalar(result.fit_residual_deg),
        "track_rms_bins": _scalar(result.track_rms_bins),
        "track_inliers": _scalar(result.track_inliers, np.float64),
        "track_span_s": _scalar(result.track_span_s),
        "ops_club_speed_mph": _scalar(result.ops_club_speed_mph),
        "track_speed_ratio": _scalar(result.track_speed_ratio),
        "track_impact_error_m": _scalar(result.track_impact_error_m),
        "track_selection_mode": _text(result.track_selection_mode),
        "attack_pre_frames": _scalar(result.attack_pre_frames, np.int64),
        "attack_post_frames": _scalar(result.attack_post_frames, np.int64),
        "attack_post_speed_scale": _scalar(result.attack_post_speed_scale),
        "path_pre_frames": _scalar(result.path_pre_frames, np.int64),
        "path_post_frames": _scalar(result.path_post_frames, np.int64),
        "path_post_speed_scale": _scalar(result.path_post_speed_scale),
    }


def generate_club_case(  # pylint: disable=too-many-arguments
    out_dir: Path,
    case: str,
    *,
    path_deg: float,
    club_speed_ms: float,
    tee_range_m: float = 1.372,
    impact_t_s: float | None = None,
    tdm_sign: int = 1,
    empty: bool = False,
    n_frames: int = 18,
    cal: Calibration | None = None,
):
    """Record one club-stage golden vector from a synthetic club capture.

    ``cal`` defaults to identity (no-op array correction) when unset. Pass
    the real reference calibration (``Calibration.load(...)``) for a case
    that must catch a C port omitting the per-element correction entirely --
    see the ``*_ref_calibration`` cases below, which record
    ``candidate_attack_angle_deg``/``attack_fit_rms_m`` values that visibly
    differ from the identity-calibration run of the same capture.
    """
    cal = cal or Calibration.identity()
    cal.tee_range_m = tee_range_m
    t_impact = (club.PRE_IMPACT_FRAMES * CLUB_FRAME_PERIOD_S) if impact_t_s is None else impact_t_s
    if empty:
        cube = np.zeros((n_frames, 12 * 3, 4, 128), dtype=complex)
        raw = pack_dump(
            cube,
            n_tx=3,
            version=3,
            frame_period_us=4000,
            trigger_frame=0,
            sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        )
    else:
        raw = synth_club_dump(
            path_deg,
            club_speed_ms=club_speed_ms,
            tee_range_m=tee_range_m,
            t_impact_s=t_impact,
            n_frames=n_frames,
        )
    ops_mph = club_speed_ms * 2.23694
    result = club.estimate_club_path(
        raw,
        cal,
        ops_club_speed_mph=ops_mph,
        impact_t_s=t_impact,
        tdm_sign=tdm_sign,
    )
    arrays = {
        "raw_bytes": np.frombuffer(raw, dtype=np.uint8).copy(),
        "ops_club_speed_mph": _scalar(ops_mph),
        "impact_t_s": _scalar(t_impact),
        "aim_offset_deg": _scalar(0.0),
        "tdm_sign": _scalar(tdm_sign, np.int64),
        **_cal_fields(cal),
        **_club_result_fields(result),
    }
    return _write(out_dir, case, arrays)


# --- late_window stage: late_window.plan_late_window(...) -------------------
#
# Pure launch-condition planner; no radar capture involved at all.


def generate_late_window_case(
    out_dir: Path,
    case: str,
    *,
    mode: str,
    ball_speed_mph: float,
    launch_angle_deg: float,
    spin_rpm: float,
    tee_range_m: float = 1.5,
):
    """Record one late_window-stage golden vector (no radar capture)."""
    plan = lw.plan_late_window(mode, ball_speed_mph, launch_angle_deg, spin_rpm, tee_range_m)
    arrays = {
        "mode": _text(mode),
        "ball_speed_mph": _scalar(ball_speed_mph),
        "launch_angle_deg": _scalar(launch_angle_deg),
        "spin_rpm": _scalar(spin_rpm),
        "tee_range_m": _scalar(tee_range_m),
        "enabled": _flag(plan.enabled),
        "reason": _text(plan.reason),
        "apex_t_s": _scalar(plan.apex_t_s),
        "look_t_s": _arr([look.t_s for look in plan.looks]),
        "look_downrange_m": _arr([look.downrange_m for look in plan.looks]),
        "look_height_m": _arr([look.height_m for look in plan.looks]),
        "look_slant_range_m": _arr([look.slant_range_m for look in plan.looks]),
    }
    return _write(out_dir, case, arrays)


# --- case tables -------------------------------------------------------------
#
# Shared synth_shot() flight conditions used by BOTH the tracking and lcmf
# stages (n_tx=3 throughout, so every case also exercises the 3TX projection
# path). See tests/golden/iwr6843/README.md for what each one covers.

FLIGHT_CASES: dict[str, dict] = {
    "slow_ball_shallow_launch_normal_tx": dict(
        speed_ms=25.0,
        launch_deg=8.0,
        tx_order="normal",
        noise=6.0,
        amp=400.0,
    ),
    "slow_ball_shallow_launch_reversed_tx": dict(
        speed_ms=25.0,
        launch_deg=8.0,
        tx_order="reversed",
        noise=6.0,
        amp=400.0,
    ),
    "fast_ball_steep_launch_normal_tx": dict(
        speed_ms=65.0,
        launch_deg=35.0,
        tx_order="normal",
        noise=6.0,
        amp=400.0,
    ),
    "fast_ball_steep_launch_reversed_tx": dict(
        speed_ms=65.0,
        launch_deg=35.0,
        tx_order="reversed",
        noise=6.0,
        amp=400.0,
    ),
    "driver_speed_mid_launch": dict(
        speed_ms=55.0,
        launch_deg=12.0,
        noise=6.0,
        amp=400.0,
    ),
    "wedge_speed_steep_launch": dict(
        speed_ms=30.0,
        launch_deg=40.0,
        noise=6.0,
        amp=400.0,
    ),
    "low_amp_high_noise_no_ball_1": dict(
        speed_ms=45.0,
        launch_deg=18.0,
        amp=1.0,
        noise=50.0,
        seed=11,
    ),
    # A second seed of the SAME low-amp/high-noise no-ball case used to live
    # here (seed=22 vs seed=11) -- identical failure mode, recorded twice.
    # Finding 7's genuinely distinct no-ball mechanism ("ball beyond
    # max_range_m") needs a stage-specific clamp param that this shared
    # synth_shot() table cannot carry for both stages at once (tracking's
    # max_range_m and lcmf's net_range_m are different parameters with no
    # common synth_shot()-only equivalent) -- see the standalone
    # "driver_speed_beyond_{max,net}_range_no_ball" cases added in main()
    # below instead, which cover both stages without warping this table.
    "short_capture_six_frames": dict(
        speed_ms=45.0,
        launch_deg=18.0,
        n_frames=6,
        trigger_frame=2,
        noise=6.0,
        amp=400.0,
    ),
    "decelerating_ball": dict(
        speed_ms=50.0,
        launch_deg=15.0,
        accel_ms2=-10.0,
        noise=6.0,
        amp=400.0,
    ),
    "high_tilt_mount": dict(
        speed_ms=40.0,
        launch_deg=20.0,
        tilt_deg=25.0,
        noise=6.0,
        amp=400.0,
    ),
    "floor_image_multipath": dict(
        speed_ms=45.0,
        launch_deg=18.0,
        image_gain=0.6,
        noise=4.0,
        amp=400.0,
    ),
}
# n_tx=3 unless the case says otherwise; keeps one dump shape shared by
# tracking and lcmf, and lets tx_order vary meaningfully everywhere.
for _kwargs in FLIGHT_CASES.values():
    _kwargs.setdefault("n_tx", 3)
    _kwargs.setdefault("tx_order", "normal")

CLUB_CASES: dict[str, dict] = {
    "club_path_square_slow": dict(path_deg=0.0, club_speed_ms=18.0),
    "club_path_square_fast": dict(path_deg=0.0, club_speed_ms=30.0),
    "club_path_in_to_out_shallow": dict(path_deg=-8.0, club_speed_ms=20.0),
    "club_path_out_to_in_shallow": dict(path_deg=8.0, club_speed_ms=20.0),
    "club_path_in_to_out_steep": dict(path_deg=-15.0, club_speed_ms=25.0),
    "club_path_out_to_in_steep": dict(path_deg=15.0, club_speed_ms=25.0),
    "club_no_track_empty_scene": dict(path_deg=0.0, club_speed_ms=20.0, empty=True),
    "club_short_capture_ten_frames": dict(path_deg=6.0, club_speed_ms=22.0, n_frames=10),
}

LATE_WINDOW_CASES: dict[str, dict] = {
    "net_mode_disabled": dict(
        mode="net", ball_speed_mph=134.0, launch_angle_deg=15.0, spin_rpm=2500.0
    ),
    "outdoor_slow_shallow_apex_too_soon": dict(
        mode="outdoor",
        ball_speed_mph=30.0,
        launch_angle_deg=5.0,
        spin_rpm=6000.0,
    ),
    "outdoor_mid_speed_mid_launch": dict(
        mode="outdoor",
        ball_speed_mph=134.0,
        launch_angle_deg=15.0,
        spin_rpm=2500.0,
    ),
    "outdoor_fast_steep_launch": dict(
        mode="outdoor",
        ball_speed_mph=168.0,
        launch_angle_deg=35.0,
        spin_rpm=2000.0,
    ),
    "on_course_mid_speed": dict(
        mode="on_course",
        ball_speed_mph=134.0,
        launch_angle_deg=15.0,
        spin_rpm=2500.0,
    ),
    "on_course_high_launch": dict(
        mode="on_course",
        ball_speed_mph=100.0,
        launch_angle_deg=45.0,
        spin_rpm=3500.0,
        tee_range_m=1.4,
    ),
    "outdoor_no_flight_zero_angle": dict(
        mode="outdoor",
        ball_speed_mph=134.0,
        launch_angle_deg=0.0,
        spin_rpm=2500.0,
    ),
    "outdoor_no_flight_zero_speed": dict(
        mode="outdoor",
        ball_speed_mph=0.0,
        launch_angle_deg=15.0,
        spin_rpm=2500.0,
    ),
}


def main() -> None:
    """Generate every stage's golden vectors under ``--out``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    written: list[Path] = []

    tracking_dir = args.out / "tracking"
    lcmf_dir = args.out / "lcmf"
    club_dir = args.out / "club"
    late_window_dir = args.out / "late_window"

    for name, kwargs in FLIGHT_CASES.items():
        written.append(generate_tracking_case(tracking_dir, name, **kwargs))

    for name, kwargs in FLIGHT_CASES.items():
        speed_ms = kwargs["speed_ms"]
        lcmf_kwargs = {k: v for k, v in kwargs.items() if k != "speed_ms"}
        written.append(
            generate_lcmf_case(lcmf_dir, name, ball_speed_mph=speed_ms * 2.23694, **lcmf_kwargs)
        )

    for name, kwargs in CLUB_CASES.items():
        written.append(generate_club_case(club_dir, name, **kwargs))

    for name, kwargs in LATE_WINDOW_CASES.items():
        written.append(generate_late_window_case(late_window_dir, name, **kwargs))

    # --- extra cases: real calibration + previously-unvaried parameters ----
    #
    # See the module docstrings on _ref_cal / generate_club_case's ``cal``
    # param, and the review findings this corpus wave closed, for why each of
    # these exists and what it proves the corpus can now catch.

    # Finding 1: at least two lcmf and two club cases run with the real
    # shipped array calibration instead of identity, so a C port that omits
    # Calibration.apply entirely produces a visibly wrong angle_deg /
    # candidate_attack_angle_deg rather than an accidentally-correct one.
    written.append(
        generate_lcmf_case(
            lcmf_dir,
            "driver_speed_mid_launch_ref_calibration",
            ball_speed_mph=55.0 * 2.23694,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            cal=_ref_cal(),
        )
    )
    written.append(
        generate_lcmf_case(
            lcmf_dir,
            "wedge_speed_steep_launch_ref_calibration",
            ball_speed_mph=30.0 * 2.23694,
            launch_deg=40.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            cal=_ref_cal(),
        )
    )
    written.append(
        generate_club_case(
            club_dir,
            "club_path_square_slow_ref_calibration",
            path_deg=0.0,
            club_speed_ms=18.0,
            cal=_ref_cal(tee_range_m=1.372),
        )
    )
    written.append(
        generate_club_case(
            club_dir,
            "club_path_out_to_in_shallow_ref_calibration",
            path_deg=8.0,
            club_speed_ms=20.0,
            cal=_ref_cal(tee_range_m=1.372),
        )
    )

    # Finding 6: parameter dimensions that were plumbed but never varied.
    written.append(
        # tdm_sign_policy="negative" -> tdm_sign_used=-1 at lcmf.py:813/843,
        # flowing through _tx2_horizontal_proxy and _snapshot_cache with the
        # opposite Doppler-phase sign from every other lcmf case.
        generate_lcmf_case(
            lcmf_dir,
            "driver_speed_tdm_sign_negative",
            ball_speed_mph=55.0 * 2.23694,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            tdm_sign_policy="negative",
        )
    )
    written.append(
        # n_tx=2: the capture already IS the vertical pair, so process_dump's
        # 3TX-to-2TX projection (project_tx_pair, lcmf.py:165) never runs --
        # the only case in the corpus that exercises the no-projection path.
        generate_lcmf_case(
            lcmf_dir,
            "driver_speed_n_tx2_direct",
            ball_speed_mph=55.0 * 2.23694,
            launch_deg=12.0,
            n_tx=2,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
        )
    )
    written.append(
        # net_range_m=4.0 clamps tracking.track_max_range_m to 3.75 m, well
        # inside this flight's ~4.7 m of travel: still accepted, but with a
        # different angle_deg/track_inliers than the unclamped case above --
        # proof the clamp is actually reaching the RANSAC gate, not just
        # being accepted and ignored.
        generate_lcmf_case(
            lcmf_dir,
            "driver_speed_net_range_clamped",
            ball_speed_mph=55.0 * 2.23694,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            net_range_m=4.0,
        )
    )
    written.append(
        # grid_step_deg=2.0 (vs. the 0.5 every other case uses) coarsens the
        # LCMF search grid; angle_deg measurably shifts (~12.15 -> ~12.45 deg
        # in manual verification), proving the grid resolution is actually
        # wired into the search rather than dead-plumbed.
        generate_lcmf_case(
            lcmf_dir,
            "driver_speed_grid_step_coarse",
            ball_speed_mph=55.0 * 2.23694,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            grid_step_deg=2.0,
        )
    )
    written.append(
        # max_range_m=3.5 clamps the second BALL_GATES_M gate well short of
        # this flight's full travel: track is still found but with far fewer
        # inliers (73 vs 160 unclamped) and rms_bins >= 0.45, so
        # low_confidence=True is recorded here too -- the corpus's only
        # low-confidence-but-found case (see README).
        generate_tracking_case(
            tracking_dir,
            "driver_speed_max_range_clamped",
            speed_ms=55.0,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            max_range_m=3.5,
        )
    )
    written.append(
        # min_ball_ms=55.0 (vs. the FAST_TRACK_MS=26.5 default) sits just
        # above this flight's ~54.7 m/s "fastest-credible" candidate, so the
        # find_ball selection logic actually picks a different candidate
        # (55.0 m/s / 152 inliers vs 54.68 m/s / 160 unclamped) rather than
        # silently reproducing the default.
        generate_tracking_case(
            tracking_dir,
            "driver_speed_min_ball_ms_override",
            speed_ms=55.0,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            min_ball_ms=55.0,
        )
    )
    written.append(
        # n_tx=2 direct capture for the tracking stage: _tracking_inputs
        # skips project_tx_pair entirely (generate_golden_vectors.py's own
        # n_tx==3 branch), the tracking-side twin of the lcmf n_tx=2 case
        # above.
        generate_tracking_case(
            tracking_dir,
            "driver_speed_n_tx2_direct",
            speed_ms=55.0,
            launch_deg=12.0,
            n_tx=2,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
        )
    )
    written.append(
        # Finding 7: a genuinely distinct no-ball failure mode from the
        # amp/noise-driven low_amp_high_noise_no_ball_1 case -- normal SNR,
        # but max_range_m=2.0 clamps BOTH BALL_GATES_M gates to an empty
        # (lo > hi) interval, so there are zero detections at all. Verified:
        # find_ball returns None here for a reason that has nothing to do
        # with signal quality.
        generate_tracking_case(
            tracking_dir,
            "driver_speed_beyond_max_range_no_ball",
            speed_ms=55.0,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            max_range_m=2.0,
        )
    )
    written.append(
        # lcmf's twin of the above: net_range_m=2.25 clamps
        # tracking.track_max_range_m to 2.0 via the same path, giving
        # estimate_lcmf_v1 a clean "rejected_by_ball_tracker" for the
        # distinct max-range reason (confirmed: process_dump's find_ball
        # call sees zero detections, not a broken/low-quality track).
        generate_lcmf_case(
            lcmf_dir,
            "driver_speed_beyond_net_range_no_ball",
            ball_speed_mph=55.0 * 2.23694,
            launch_deg=12.0,
            n_tx=3,
            tx_order="normal",
            noise=6.0,
            amp=400.0,
            net_range_m=2.25,
        )
    )
    written.append(
        # tdm_sign=-1 for the club stage: synth_club_dump's phase model is
        # built assuming tdm_sign=+1 (see its docstring), so telling the
        # estimator to remove the OPPOSITE Doppler-phase sign exercises the
        # tdm_sign=-1 branch of doa.tx2_reference_phases_at/tx2_phase_at with
        # a deliberately mismatched, verifiably-different result.
        generate_club_case(
            club_dir,
            "club_path_tdm_sign_negative",
            path_deg=4.0,
            club_speed_ms=20.0,
            tdm_sign=-1,
        )
    )

    def _display(path: Path) -> Path:
        try:
            return path.relative_to(ROOT)
        except ValueError:
            return path

    for path in written:
        print(f"wrote {_display(path)}")
    print(f"{len(written)} golden vectors written under {_display(args.out)}")


if __name__ == "__main__":
    main()
