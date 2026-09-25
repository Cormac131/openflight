"""Plan a pre-apex and post-apex IWR6843 look for open-flight shots.

Bay and net captures stay on the 72 ms impact movie. On course and on an
outdoor range the ball leaves the 6 m impact chirp before apex, so a later
pair of looks is the descent-angle measurement. This plans those looks from
the launch conditions. It does not reconfigure the radar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from openflight.ballistics import (
    M_TO_YD,
    LaunchConditions,
    simulate,
)

CaptureMode = Literal["net", "outdoor", "on_course"]

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

    @property
    def host_dump_can_make_it(self) -> bool:
        """True when a raw UART dump would finish before the first late look."""
        if not self.looks:
            return False
        return self.looks[0].t_s >= RAW_DUMP_S


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
