"""Plan a pre-apex and post-apex IWR6843 look for open-flight shots.

Bay and net captures stay on the 72 ms impact movie. On course and on an
outdoor range the ball leaves the 6 m impact chirp before apex, so a later
pair of looks is the descent-angle measurement. This plans those looks from
the launch conditions. It does not reconfigure the radar.
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from openflight.ballistics import (
    M_TO_YD,
    LaunchConditions,
    simulate,
)
from openflight.iwr6843.calibration import DEFAULT_TEE_RANGE_M
from openflight.iwr6843.dump import parse_dump

CaptureMode = Literal["net", "outdoor", "on_course"]
FlightMode = Literal["net", "range", "course"]
_PLANNER_MODE: dict[str, CaptureMode] = {"range": "outdoor", "course": "on_course"}

IMPACT_WINDOW_S = 0.072
LATE_OFFSET_S = 0.2
CHIRP_MAX_RANGE_M = 6.0
_OPEN_MODES = frozenset({"outdoor", "on_course"})
# Half-width of the range gate around each predicted slant range. The model
# places the ball; anything farther from it is clutter or a different target.
LATE_GATE_MIN_M = 1.5
LATE_GATE_FRAC = 0.10
# The late look scales slant range by the model's elevation, so the descent
# angle is partly predicted, not purely measured.
LATE_METHOD = "model_assisted"


@dataclass(frozen=True)
class LateLook:
    """One predicted sample: time from impact, and where the ball should be."""

    t_s: float
    downrange_m: float
    height_m: float
    slant_range_m: float


@dataclass(frozen=True)
class LateWindowPlan:
    """Whether a late pair should be captured, and the two predicted points."""

    enabled: bool
    reason: str
    apex_t_s: float | None = None
    looks: tuple[LateLook, ...] = ()

    def to_dict(self) -> dict:
        """JSON-ready looks for the shot record."""
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "apex_t_s": self.apex_t_s,
            "looks": [
                {
                    "t_s": look.t_s,
                    "downrange_m": look.downrange_m,
                    "height_m": look.height_m,
                    "slant_range_m": look.slant_range_m,
                }
                for look in self.looks
            ],
        }


def planner_mode(flight: str) -> CaptureMode | None:
    """Map a startup flight setting onto the late-window planner.

    ``"net"`` keeps the bay clamp. ``"range"`` and ``"course"`` are the open
    flights that may use returns past the net.
    """
    return _PLANNER_MODE.get(flight)


def net_gate_m(flight: str, net_range_m: float | None) -> float | None:
    """Return the net clamp, or None when the ball is allowed to fly past it."""
    if planner_mode(flight) is not None:
        return None
    return net_range_m


def plan_late_window(
    mode: CaptureMode,
    ball_speed_mph: float,
    launch_angle_deg: float,
    spin_rpm: float,
    tee_range_m: float = DEFAULT_TEE_RANGE_M,
) -> LateWindowPlan:
    """Return the pre-apex and post-apex looks for an open-flight shot.

    ``mode`` is ``"net"`` in a bay. ``"outdoor"`` and ``"on_course"`` are the
    only modes that schedule a late window. ``tee_range_m`` is the radar-to-ball
    distance at impact; the ball's predicted position is added downrange of that.
    """
    if mode not in _OPEN_MODES:
        return LateWindowPlan(enabled=False, reason="net")
    if ball_speed_mph <= 0.0 or launch_angle_deg <= 0.0:
        return LateWindowPlan(enabled=False, reason="no-flight")

    trajectory = simulate(
        LaunchConditions(
            ball_speed_mph=ball_speed_mph,
            launch_angle_v=launch_angle_deg,
            launch_angle_h=0.0,
            spin_rpm=spin_rpm,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
    )
    apex = max(trajectory.points, key=lambda point: point.z)
    if apex.t <= LATE_OFFSET_S:
        return LateWindowPlan(enabled=False, reason="apex-too-soon", apex_t_s=apex.t)

    looks = tuple(
        _look_at(trajectory.points, apex.t + offset_s, tee_range_m)
        for offset_s in (-LATE_OFFSET_S, LATE_OFFSET_S)
    )
    return LateWindowPlan(
        enabled=True,
        reason=mode,
        apex_t_s=apex.t,
        looks=looks,
    )


# span_m = 600 / slope_MHz_per_us at 4 Msps. Same relation as the 6 m / 100 MHz/us impact chirp.
_ADC_RATE_KSPS = 4000
_RANGE_SPAN_PER_SLOPE = 600.0
_LATE_BINS = 53
# l3dump with no trigger returns only the rolling pre-trigger ring.
_PRE_FRAMES = 8


def long_range_span_m(far_slant_m: float) -> float:
    """Unambiguous range that still contains the farther late look."""
    return max(20.0, far_slant_m * 1.15)


def long_range_cfg(plan: LateWindowPlan) -> str:
    """IQ16 profile aimed at the predicted looks. Same 3 TX and 128 samples."""
    far = max(look.slant_range_m for look in plan.looks)
    span = long_range_span_m(far)
    slope = _RANGE_SPAN_PER_SLOPE / span
    mid = sum(look.slant_range_m for look in plan.looks) / len(plan.looks)
    start = int(round(mid / (span / 128.0) - _LATE_BINS / 2))
    start = min(max(start, 0), 128 - _LATE_BINS)
    period_ms = late_period_s(plan) * 1000.0
    return "\n".join(
        (
            "dfeDataOutputMode 1",
            "channelCfg 15 7 0",
            "adcCfg 2 1",
            f"profileCfg 0 60.0 7 3 38 0 0 {slope:.4f} 1 128 {_ADC_RATE_KSPS} 0 0 30",
            "chirpCfg 0 0 0 0 0 0 0 1",
            "chirpCfg 1 1 0 0 0 0 0 2",
            "chirpCfg 2 2 0 0 0 0 0 4",
            f"frameCfg 0 2 12 0 {period_ms:.0f} 1 0",
            "captureFormat iq16",
            f"phaseCaptureCfg {start} {_LATE_BINS} {_PRE_FRAMES} {start} {_LATE_BINS} 1 "
            f"{start} {_LATE_BINS} {start} 2 1",
            "lowPower 0 0",
            "sensorStart",
            "",
        )
    )


def late_period_s(plan: LateWindowPlan) -> float:
    """Frame period that puts several samples between the two looks."""
    gap_s = plan.looks[-1].t_s - plan.looks[0].t_s
    return max(0.05, gap_s / 3.0)


def expected_slant_m(plan: LateWindowPlan, t_s: float) -> float:
    """Predicted slant range at ``t_s``, linear between the looks, clamped outside."""
    times = [look.t_s for look in plan.looks]
    slants = [look.slant_range_m for look in plan.looks]
    return float(np.interp(t_s, times, slants))


def late_gate_m(expected_m: float) -> float:
    """Half-width of the search gate around one predicted range."""
    return max(LATE_GATE_MIN_M, LATE_GATE_FRAC * expected_m)


def frame_times_s(meta: dict, n_frames: int, *, freeze_t_s: float, period_s: float) -> list[float]:
    """Time since impact of each frame, with the newest frame at the freeze.

    The firmware stops at the frame boundary after ``l3dump`` arrives, so the
    newest frame ends at the freeze, not when the transfer finishes.
    """
    offsets = meta.get("frame_time_offsets_us")
    if offsets:
        newest = offsets[-1]
        return [freeze_t_s - (newest - offset) / 1e6 for offset in offsets]
    return [freeze_t_s - (n_frames - 1 - index) * period_s for index in range(n_frames)]


def measured_ranges(
    raw: bytes,
    span_m: float,
    plan: LateWindowPlan,
    *,
    freeze_t_s: float,
    period_s: float,
) -> list[dict]:
    """Moving-target peak near the predicted slant range in each frame.

    Returns in every chirp and frame (ground, net, trees) are removed by
    subtracting each sample's mean across frames; the ball is in a different
    bin every frame and survives. The peak is only searched inside the gate
    around the range the model predicts for that frame's time.
    """
    meta, cube = parse_dump(raw)
    n_frames = cube.shape[0]
    starts = meta.get("range_bin_starts") or (meta.get("range_bin_start", 0),) * n_frames
    counts = meta.get("range_bin_counts") or (cube.shape[-1],) * n_frames
    if len(set(starts)) != 1 or len(set(counts)) != 1:
        raise ValueError("late-window clutter removal needs one range window for every frame")
    start = int(starts[0])
    count = int(counts[0])
    moving = cube[..., :count] - cube[..., :count].mean(axis=0, keepdims=True)
    power = (np.abs(moving) ** 2).sum(axis=(1, 2))
    bin_m = span_m / 128.0
    bin_ranges = (start + np.arange(count)) * bin_m
    ranges = []
    for frame, t_s in enumerate(
        frame_times_s(meta, n_frames, freeze_t_s=freeze_t_s, period_s=period_s)
    ):
        expected = expected_slant_m(plan, t_s)
        gate = np.abs(bin_ranges - expected) <= late_gate_m(expected)
        sample = {"frame": frame, "t_s": t_s, "expected_slant_m": expected, "slant_range_m": None}
        if gate.any():
            profile = np.where(gate, power[frame], -np.inf)
            local = int(np.argmax(profile))
            floor = float(np.median(power[frame]))
            sample["slant_range_m"] = float(bin_ranges[local])
            sample["peak_to_median"] = float(power[frame, local] / floor) if floor > 0 else None
        ranges.append(sample)
    return ranges


def descent_angle_deg(
    plan: LateWindowPlan,
    ranges: list[dict],
    tee_range_m: float,
    *,
    max_time_error_s: float,
) -> float | None:
    """Downward chord between the samples nearest the two planned looks.

    Slant range is the measurement. Each look's predicted elevation places
    that range on a ray from the radar, so the result is model-assisted.
    Returns None when no measured sample lies within ``max_time_error_s``
    of a look.
    """
    if len(plan.looks) < 2:
        return None
    usable = [sample for sample in ranges if sample.get("slant_range_m") is not None]
    if len(usable) < 2:
        return None
    before = _place_look(plan.looks[0], usable, tee_range_m, max_time_error_s)
    after = _place_look(plan.looks[-1], usable, tee_range_m, max_time_error_s)
    if before is None or after is None:
        return None
    downrange_before, height_before = before
    downrange_after, height_after = after
    travel = downrange_after - downrange_before
    if travel <= 0.0:
        return None
    return math.degrees(math.atan2(-(height_after - height_before), travel))


def _place_look(
    look: LateLook,
    usable: list[dict],
    tee_range_m: float,
    max_time_error_s: float,
) -> tuple[float, float] | None:
    """(downrange, height) of the measured sample nearest ``look``, on its ray."""
    sample = min(usable, key=lambda item: abs(item["t_s"] - look.t_s))
    if abs(sample["t_s"] - look.t_s) > max_time_error_s:
        return None
    elevation = math.atan2(look.height_m, tee_range_m + look.downrange_m)
    slant = sample["slant_range_m"]
    return slant * math.cos(elevation) - tee_range_m, slant * math.sin(elevation)


def capture_late_window(
    radar,
    plan: LateWindowPlan,
    *,
    impact_timestamp: float,
    tee_range_m: float,
    restore_cfg: str,
    now,
    sleep,
) -> dict | None:
    """Retune, sample both looks, dump, and restore the impact profile.

    Returns None when the first look is already too close to arm. The result
    records how long each phase held the radar.
    """
    if not plan.enabled or len(plan.looks) < 2:
        return None
    period_s = late_period_s(plan)
    arm_at = impact_timestamp + plan.looks[0].t_s - (2.0 * period_s)
    if now() > arm_at:
        return None
    span = long_range_span_m(max(look.slant_range_m for look in plan.looks))
    timing: dict[str, float] = {}
    with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False, encoding="utf-8") as handle:
        handle.write(long_range_cfg(plan))
        late_cfg = handle.name
    try:
        mark = now()
        radar.send_config(late_cfg)
        timing["retune_s"] = now() - mark
        mark = now()
        sleep(max(0.0, impact_timestamp + plan.looks[-1].t_s + period_s - now()))
        timing["wait_s"] = now() - mark
        freeze_t_s = now() - impact_timestamp
        raw = radar.read_dump()
        timing["dump_s"] = now() - impact_timestamp - freeze_t_s
    finally:
        mark = now()
        radar.send_config(restore_cfg)
        timing["restore_s"] = now() - mark
        Path(late_cfg).unlink(missing_ok=True)
    ranges = measured_ranges(raw, span, plan, freeze_t_s=freeze_t_s, period_s=period_s)
    return {
        "method": LATE_METHOD,
        "span_m": span,
        "freeze_t_s": freeze_t_s,
        "ranges": ranges,
        "descent_deg": descent_angle_deg(plan, ranges, tee_range_m, max_time_error_s=period_s),
        "timing_s": timing,
    }


def _look_at(points, t_s: float, tee_range_m: float) -> LateLook:
    point = min(points, key=lambda candidate: abs(candidate.t - t_s))
    downrange_m = point.x / M_TO_YD
    height_m = point.z / M_TO_YD
    slant_range_m = ((tee_range_m + downrange_m) ** 2 + height_m**2) ** 0.5
    return LateLook(
        t_s=point.t,
        downrange_m=downrange_m,
        height_m=height_m,
        slant_range_m=slant_range_m,
    )
