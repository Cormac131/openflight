"""Club path from the IWR6843's pre-impact frames.

Club path is the horizontal direction the club head travels through impact.
It is a DIRECTION, so it needs at least two spatial samples over time; a
single azimuth reading cannot produce it.

This estimator converts each snapshot to a Cartesian position (x along
boresight, y cross-range) and fits BOTH components linearly against time,
rather than fitting the azimuth angle itself. For a target moving in a
straight line at constant velocity -- the club, over a ~24 ms pre-impact
window -- x(t) and y(t) are each exactly linear in time, while azimuth(t)
and range(t) individually are not. An angle-rate fit's slope is therefore a
window-averaged rate (through the fit's own weighted-mean time, not the
impact instant), and no choice of range-evaluation point can turn that
average back into the instantaneous rate at impact -- confirmed on this
module's own synthetic fixture (2026-07-25): a fitted azimuth rate of
106.4 deg/s against a true instantaneous rate of 64.1 deg/s at impact and
102.5 deg/s at the fit's own mean time. Fitting x and y directly sidesteps
the problem rather than correcting for it: path_deg = atan2(v_y, v_x) is
then exact for straight-line motion, independent of where in the window the
samples happen to sit.

Absolute azimuth enters additively via ``aim_offset_deg``, not as an
estimator input: v_x and v_y come from per-sample DIFFERENCES against the
same track's range, so a constant per-element phase error from the shipped
array calibration (measured on a different board) cancels out of the path
itself and only shifts azimuth's absolute origin -- which is exactly what
``--iwr6843-azimuth-offset-deg`` (a later task) calibrates out.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass

import numpy as np

from openflight.iwr6843 import doa, tracking
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.shot import (
    TX2_LOOP_PERIOD_S,
    geometry_from_header,
    is_range_snapshot,
    project_tx_pair,
)

logger = logging.getLogger(__name__)

MPH_PER_MS = 2.23694

# Search window around the tee. The club sweeps through a region rather than
# sitting at a point, so this is deliberately wider than the ball's gate.
CLUB_SEARCH_HALF_WIDTH_M = 0.6

# Radial speed bounds. Observed: 22 m/s radial for 66-79 mph clubs, i.e. a
# projection factor near 0.66.
CLUB_SPEED_BOUNDS_MS = (10.0, 45.0)

CLUB_MIN_FRAMES = 4
CLUB_MIN_SNAPSHOTS = 24

# The firmware retains six pre-impact frames plus the active trigger frame,
# and loop_time() makes slot-order 0 the oldest retained frame, so impact sits
# at slot-order 6. Verified against firmware/README.md's window schedule on
# all seven captures of session 20260725_140533.
PRE_IMPACT_FRAMES = 6

# Provisional. Cannot be derived analytically; set from the observed residual
# distribution across local captures during bring-up.
CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG = 0.5

# The track measures RADIAL speed = true club speed x an unknown projection
# factor, so the identity gate is a projection window, not a tolerance. A
# symmetric tolerance would either reject every valid shot or admit anything.
CLUB_SPEED_PROJECTION_RANGE = (0.4, 0.95)

# The true azimuth change across the window is ~0.04 rad. Anything near a
# wrap is a broken track, not a real measurement.
CLUB_MAX_PHASE_SWING_RAD = math.pi / 2


@dataclass
class ClubPathResult:
    """One club-path estimate with the evidence behind it."""

    status: str
    path_deg: float | None = None
    confidence: float | None = None
    azimuth_rate_dps: float | None = None
    range_rate_ms: float | None = None
    club_range_m: float | None = None
    n_frames: int = 0
    n_snapshots: int = 0
    fit_residual_deg: float | None = None
    track_rms_bins: float | None = None
    track_inliers: int | None = None
    track_span_s: float | None = None

    @property
    def accepted(self) -> bool:
        """Whether a club path was produced."""
        return self.path_deg is not None and self.status.startswith("accepted")

    def to_dict(self) -> dict:
        """JSON-safe diagnostics for the session log."""
        return asdict(self)


def pre_impact_window_s(geo: tracking.Geometry) -> tuple[float, float] | None:
    """Time window covering the retained pre-impact frames.

    ``loop_time`` makes slot-order 0 the oldest retained frame, and the
    firmware keeps six pre-impact frames plus the active trigger frame, so
    impact sits at slot-order ``PRE_IMPACT_FRAMES``. Returns None when the
    ring is too short to contain a pre-impact window at all.
    """
    if geo.n_frames <= PRE_IMPACT_FRAMES:
        return None
    return (0.0, PRE_IMPACT_FRAMES * geo.frame_period_s)


def find_club(
    mti: np.ndarray,
    geo: tracking.Geometry,
    *,
    tee_range_m: float,
    window_s: tuple[float, float],
):
    """Track the club's range walk inside the pre-impact window.

    The time window is load-bearing, not a refinement: the club's radial
    speed (~22 m/s) and the ball's (~40 m/s) both sit inside
    CLUB_SPEED_BOUNDS_MS, and the ball crosses the club's range window early
    in flight. Without the window this fits the ball.
    """
    lo = max(0.35, tee_range_m - CLUB_SEARCH_HALF_WIDTH_M)
    hi = tee_range_m + CLUB_SEARCH_HALF_WIDTH_M
    return tracking.find_ball(
        mti,
        geo,
        gates_m=((lo, hi),),
        speed_bounds_ms=CLUB_SPEED_BOUNDS_MS,
        min_ball_ms=CLUB_SPEED_BOUNDS_MS[0],
        time_window_s=window_s,
    )


def estimate_club_path(
    raw: bytes,
    cal: Calibration,
    *,
    ops_club_speed_mph: float,
    aim_offset_deg: float = 0.0,
    tdm_sign: int = 1,
) -> ClubPathResult:
    """Estimate club path from one dump's pre-impact frames."""
    meta0, _ = parse_dump(raw)
    if meta0.get("n_tx") != 3:
        return ClubPathResult(status="rejected_requires_three_tx")

    projected = project_tx_pair(raw, (0, 2))
    meta, cube = parse_dump(projected)
    geo = geometry_from_header(meta, loop_period_s=TX2_LOOP_PERIOD_S)
    res = geo.range_res_m

    window_s = pre_impact_window_s(geo)
    if window_s is None:
        return ClubPathResult(status="rejected_no_pre_impact_frames")

    mti = tracking.mti_filter(cube, range_domain=is_range_snapshot(meta), geometry=geo)
    track = find_club(mti, geo, tee_range_m=cal.tee_range_m, window_s=window_s)
    if track is None:
        return ClubPathResult(status="rejected_no_club_track")

    result = ClubPathResult(
        status="pending",
        range_rate_ms=track.slope_bins * res,
        track_rms_bins=track.rms_bins,
        track_inliers=track.n_inliers,
        track_span_s=track.t_last - track.t_first,
    )

    projection = abs(result.range_rate_ms) / max(ops_club_speed_mph / MPH_PER_MS, 1e-6)
    low, high = CLUB_SPEED_PROJECTION_RANGE
    if not low <= projection <= high:
        result.status = "rejected_club_speed_mismatch"
        return result

    # TX2 phase needs all three transmitters, so re-split the ORIGINAL dump.
    _meta3, cube3 = parse_dump(raw)
    n_frames, chirps, n_rx, n_samples = cube3.shape
    n_tx = 3
    loops = chirps // n_tx
    tdm = cube3.reshape(n_frames, loops, n_tx, n_rx, n_samples)
    # Range-snapshot dumps (production) are already range-domain; raw-ADC
    # dumps (replay of older captures) still need the range FFT. Mirror
    # lcmf._tx2_horizontal_proxy: FFTing an already-FFT'd range snapshot a
    # second time would flatten a real peak into noise.
    rfft = tdm if is_range_snapshot(_meta3) else np.fft.fft(tdm, axis=-1)
    tdm = rfft - rfft.mean(axis=1, keepdims=True)

    times: list[float] = []
    phases: list[float] = []
    weights: list[float] = []
    frames_seen: set[int] = set()
    for frame in range(geo.n_frames):
        for loop in range(geo.n_loops):
            t_s = geo.loop_time(frame, loop)
            if not track.t_first <= t_s <= track.t_last:
                continue
            absolute_bin = int(round(track.bin_at(t_s)))
            if not geo.contains_bin(absolute_bin, margin=1, frame=frame):
                continue
            local_bin = geo.local_bin(absolute_bin, frame)
            if not 0 <= local_bin < n_samples:
                continue
            sample = doa.tx2_phase_at(
                tdm,
                frame,
                loop,
                local_bin,
                velocity_ms=track.speed_ms_at(t_s, res),
                tdm_sign=tdm_sign,
                n_rx=n_rx,
            )
            if sample is None:
                continue
            phase, weight = sample
            times.append(t_s)
            phases.append(phase)
            weights.append(weight)
            frames_seen.add(frame)

    result.n_snapshots = len(times)
    result.n_frames = len(frames_seen)
    if result.n_frames < CLUB_MIN_FRAMES or result.n_snapshots < CLUB_MIN_SNAPSHOTS:
        result.status = "rejected_insufficient_snapshots"
        return result

    unwrapped = np.unwrap(np.asarray(phases))
    if float(np.ptp(unwrapped)) > CLUB_MAX_PHASE_SWING_RAD:
        result.status = "rejected_phase_wrap"
        return result

    # Phase -> azimuth (small-angle: az ~ phase / pi for half-wavelength spacing).
    azimuth_rad = np.arcsin(np.clip(unwrapped / math.pi, -1.0, 1.0))
    t_array = np.asarray(times)
    weight_array = np.asarray(weights)

    # Per-sample position in Cartesian coordinates: x along boresight, y
    # cross-range. r comes from the track's own range-vs-time fit (smooth;
    # de-noises the per-loop range), az from this sample's own phase.
    r_i = track.range_at(t_array, res)
    x_i = r_i * np.cos(azimuth_rad)
    y_i = r_i * np.sin(azimuth_rad)

    # x(t) and y(t) are each exactly linear in time for a target moving in
    # a straight line at constant velocity, so a weighted linear fit's
    # slope is v_x/v_y with no bias from where the samples sit in the
    # window -- unlike fitting the azimuth ANGLE, whose rate of change is
    # only constant for motion on a circular arc (see module docstring).
    design = np.vstack([t_array, np.ones(t_array.size)]).T
    weighted_design = design * np.sqrt(weight_array)[:, None]
    targets = np.stack([x_i, y_i], axis=1) * np.sqrt(weight_array)[:, None]
    (v_x, v_y), (_x0, y0) = np.linalg.lstsq(weighted_design, targets, rcond=None)[0]

    # Fit quality: cross-range residual is the direct, first-order-sensitive
    # signal for azimuth/phase noise (the x-residual is dominated by the
    # track's own range-fit smoothness and carries little independent
    # information). Expressed in degrees at the mean range so the existing,
    # separately-calibrated CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG threshold still
    # applies to a quantity of the same scale as before.
    mean_r = float(np.mean(r_i))
    cross_range_resid_m = y_i - (v_y * t_array + y0)
    result.fit_residual_deg = float(
        np.degrees(np.sqrt((cross_range_resid_m**2).mean()) / max(mean_r, 1e-9))
    )
    if result.fit_residual_deg > CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG:
        result.status = "rejected_azimuth_fit"
        return result

    result.club_range_m = mean_r
    # Diagnostic only (not a path input): the azimuth rate implied by the
    # cross-range velocity at the mean range.
    result.azimuth_rate_dps = float(np.degrees(v_y / max(mean_r, 1e-9)))

    path_deg = math.degrees(math.atan2(v_y, v_x))
    result.path_deg = path_deg + aim_offset_deg
    result.confidence = _confidence(result)
    result.status = "accepted"
    logger.info(
        "[CLUB] path=%.2f deg (rate %.1f deg/s, residual %.2f deg, %d frames)",
        result.path_deg,
        result.azimuth_rate_dps,
        result.fit_residual_deg,
        result.n_frames,
    )
    return result


def _confidence(result: ClubPathResult) -> float:
    """Confidence from fit residual and evidence count, never a constant."""
    residual = result.fit_residual_deg or CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG
    residual_score = max(0.0, 1.0 - residual / CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG)
    evidence_score = min(1.0, result.n_snapshots / (2.0 * CLUB_MIN_SNAPSHOTS))
    return round(0.2 + 0.7 * residual_score * evidence_score, 3)


__all__ = [
    "ClubPathResult",
    "estimate_club_path",
    "find_club",
    "pre_impact_window_s",
]
