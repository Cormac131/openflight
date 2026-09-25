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

CaptureMode = Literal["net", "outdoor", "on_course"]
FlightMode = Literal["net", "range", "course"]
_PLANNER_MODE: dict[str, CaptureMode] = {"range": "outdoor", "course": "on_course"}

IMPACT_WINDOW_S = 0.072
LATE_OFFSET_S = 0.2
CHIRP_MAX_RANGE_M = 6.0
RAW_DUMP_S = 7.0
_OPEN_MODES = frozenset({"outdoor", "on_course"})


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

    @property
    def visible_on_impact_chirp(self) -> bool:
        """True when every late look still falls inside the 6 m impact chirp."""
        return bool(self.looks) and all(
            look.slant_range_m <= CHIRP_MAX_RANGE_M for look in self.looks
        )

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

    @property
    def host_dump_can_make_it(self) -> bool:
        """True when a raw UART dump would finish before the first late look."""
        if not self.looks:
            return False
        return self.looks[0].t_s >= RAW_DUMP_S


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


def late_window_record(
    flight: str,
    *,
    ball_speed_mph: float,
    launch_angle_deg: float | None,
    spin_rpm: float,
    tee_range_m: float,
) -> dict | None:
    """Plan the late looks for an open flight, or None in a net."""
    mode = planner_mode(flight)
    if mode is None or launch_angle_deg is None:
        return None
    plan = plan_late_window(
        mode,
        ball_speed_mph,
        launch_angle_deg,
        spin_rpm,
        tee_range_m,
    )
    if not plan.enabled:
        return None
    return plan.to_dict()


def plan_late_window(
    mode: CaptureMode,
    ball_speed_mph: float,
    launch_angle_deg: float,
    spin_rpm: float,
    tee_range_m: float = 1.5,
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


def measured_ranges(raw: bytes, span_m: float) -> list[dict]:
    """Peak slant range in each frame of a late-window dump."""
    from openflight.iwr6843.dump import parse_dump

    meta, cube = parse_dump(raw)
    power = np.abs(cube) ** 2
    starts = meta.get("range_bin_starts") or (0,) * power.shape[0]
    counts = meta.get("range_bin_counts") or (power.shape[-1],) * power.shape[0]
    bin_m = span_m / 128.0
    ranges = []
    for frame in range(power.shape[0]):
        count = int(counts[frame])
        profile = power[frame, ..., :count].sum(axis=(0, 1))
        local = int(np.argmax(profile))
        ranges.append(
            {
                "frame": frame,
                "slant_range_m": (int(starts[frame]) + local) * bin_m,
            }
        )
    return ranges


def late_period_s(plan: LateWindowPlan) -> float:
    """Frame period that puts several samples between the two looks."""
    gap_s = plan.looks[-1].t_s - plan.looks[0].t_s
    return max(0.05, gap_s / 3.0)


def descent_angle_deg(
    plan: LateWindowPlan,
    ranges: list[dict],
    tee_range_m: float,
) -> float | None:
    """Downward chord between the samples nearest the two planned looks.

    Slant range is the measurement. Each look's predicted elevation places
    that range on a ray from the radar.
    """
    if len(plan.looks) < 2 or len(ranges) < 2:
        return None
    if any("t_s" not in sample for sample in ranges):
        return None
    points = []
    for look in (plan.looks[0], plan.looks[-1]):
        sample = min(ranges, key=lambda item: abs(item["t_s"] - look.t_s))
        ground = tee_range_m + look.downrange_m
        elevation = math.atan2(look.height_m, ground)
        slant = sample["slant_range_m"]
        points.append((slant * math.cos(elevation) - tee_range_m, slant * math.sin(elevation)))
    (downrange_before, height_before), (downrange_after, height_after) = points
    travel = downrange_after - downrange_before
    if travel <= 0.0:
        return None
    return math.degrees(math.atan2(-(height_after - height_before), travel))


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

    Returns None when the first look is already too close to arm.
    """
    if not plan.enabled or len(plan.looks) < 2:
        return None
    period_s = late_period_s(plan)
    arm_at = impact_timestamp + plan.looks[0].t_s - (2.0 * period_s)
    if now() > arm_at:
        return None
    span = long_range_span_m(max(look.slant_range_m for look in plan.looks))
    with tempfile.NamedTemporaryFile(
        "w", suffix=".cfg", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(long_range_cfg(plan))
        late_cfg = handle.name
    try:
        radar.send_config(late_cfg)
        sleep(max(0.0, impact_timestamp + plan.looks[-1].t_s + period_s - now()))
        raw = radar.read_dump()
        dump_t = now() - impact_timestamp
    finally:
        radar.send_config(restore_cfg)
        Path(late_cfg).unlink(missing_ok=True)
    ranges = measured_ranges(raw, span)
    for index, sample in enumerate(ranges):
        sample["t_s"] = dump_t - (len(ranges) - 1 - index) * period_s
    return {
        "span_m": span,
        "ranges": ranges,
        "descent_deg": descent_angle_deg(plan, ranges, tee_range_m),
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
