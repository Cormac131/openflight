"""The swing zone around the tee, for replays, the dump viewer and reports.

The corridor itself is the firmware's (firmware/iwr6843/l3_zone.c, called
through the host build), so a replay judges points exactly as the board
will once the zone is wired into its trigger. This module places the
corridor from the tee's slant range and judges a replay's track points
against it.

Every SwingZone field left as None keeps the firmware default
(l3_zone_cfg_defaults): those are starting values, to be replaced by limits
measured with the bench azimuth check and the recorded swings.
"""

from __future__ import annotations

import ctypes
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, fields

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr

# A point that carries angles has both on the 3-TX profiles the replay runs;
# PointSummary keeps only "angles or not".
_BOTH_ANGLES = fw.ANGLE_AZIMUTH | fw.ANGLE_ELEVATION


def tee_forward_m(tee_range_m: float, radar_height_m: float, ball_height_m: float) -> float:
    """The tee's distance along the target line (golf x) from its slant range
    from the antenna, the ball on the target line."""
    drop = ball_height_m - radar_height_m
    if not tee_range_m > abs(drop):
        raise ValueError(
            f"a tee {tee_range_m} m from the antenna cannot sit {abs(drop):.3f} m below it"
        )
    return math.sqrt(tee_range_m**2 - drop**2)


@dataclass(frozen=True)
class SwingZone:  # pylint: disable=too-many-instance-attributes
    """Overrides of the firmware's corridor; None keeps its default."""

    short_m: float | None = None
    past_m: float | None = None
    half_width_m: float | None = None
    lateral_m: float | None = None
    min_height_m: float | None = None
    max_height_m: float | None = None
    require_angles: bool | None = None

    _C_FIELDS = {
        "short_m": "shortM",
        "past_m": "pastM",
        "half_width_m": "halfWidthM",
        "lateral_m": "lateralM",
        "min_height_m": "minHeightM",
        "max_height_m": "maxHeightM",
        "require_angles": "requireAngles",
    }

    def cfg(self, lib, tee_forward: float, radar_height_m: float) -> fw.ZoneCfg:
        """The l3_zone_cfg_t for a tee at ``tee_forward`` (golf x); raises
        when the corridor cannot exist (l3_zone_cfg_check)."""
        cfg = fw.ZoneCfg()
        lib.l3_zone_cfg_defaults(ctypes.byref(cfg), tee_forward, radar_height_m)
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None:
                setattr(cfg, self._C_FIELDS[field.name], value)
        if lib.l3_zone_cfg_check(ctypes.byref(cfg)) != 0:
            raise ValueError(f"the swing zone cannot exist: {self}")
        return cfg


@dataclass(frozen=True)
class ZoneVerdict:
    """One point against the corridor: no reasons is inside."""

    reasons: tuple[str, ...]

    @property
    def inside(self) -> bool:
        """No limit broken."""
        return not self.reasons


def zone_cfg_dict(cfg: fw.ZoneCfg) -> dict:
    """The corridor's values by their C names, for JSON."""
    return {name: getattr(cfg, name) for name, _type in fw.ZoneCfg._fields_}  # pylint: disable=protected-access


def check_point(lib, cfg: fw.ZoneCfg, point: fr.PointSummary) -> ZoneVerdict:
    """l3_zone_check on one replayed track point."""
    position = point.position or (0.0, 0.0, 0.0)
    golf = fw.Vec3(*position)
    angles = _BOTH_ANGLES if point.angles_valid else 0
    bits = lib.l3_zone_check(ctypes.byref(cfg), ctypes.byref(golf), angles)
    return ZoneVerdict(
        tuple(name for i, name in enumerate(fw.ZONE_REASON_NAMES) if bits & (1 << i))
    )


def check_points(lib, cfg: fw.ZoneCfg, points: Iterable[fr.PointSummary]) -> list[ZoneVerdict]:
    """check_point over a replay's points, in order."""
    return [check_point(lib, cfg, p) for p in points]


def zone_summary(verdicts: Iterable[ZoneVerdict]) -> dict:
    """How many points were inside, and how often each limit was broken (a
    point can break several)."""
    verdicts = list(verdicts)
    reasons = Counter(reason for v in verdicts for reason in v.reasons)
    return {
        "points": len(verdicts),
        "inside": sum(v.inside for v in verdicts),
        "reasons": {name: reasons.get(name, 0) for name in fw.ZONE_REASON_NAMES},
    }
