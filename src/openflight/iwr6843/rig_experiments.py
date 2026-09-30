"""Rig experiments: radar orientation (yaw/pitch) and enclosure contribution.

Both are the same benchmark (:mod:`clutter_bench`) run over captures taken
under different physical conditions, grouped by condition:

* orientation: the same golfer/tee set-up captured at several radar yaw and
  pitch settings on an adjustable carrier; the row that maximises
  ``(club + ball response) / golfer response`` is the mounting angle for the
  next enclosure;
* enclosure: A the IWR exposed, B behind the current radar front, C the
  front plus the surrounding enclosure. A hotspot clearly stronger in B or C
  than in A is an enclosure/multipath problem on top of the golfer's direct
  reflection.

Which capture was taken under which condition is recorded in a rig manifest,
a JSON object mapping each dump's file name to its condition::

    {"iwr6843_..._001.l3dump": {"label": "yaw+10", "yaw_deg": 10, "pitch_deg": 0},
     "iwr6843_..._014.l3dump": {"label": "B", "enclosure": "B"}}
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openflight.iwr6843.clutter_bench import CaptureMetrics

ENCLOSURE_CONDITIONS = ("A", "B", "C")
ENCLOSURE_NAMES = {
    "A": "IWR exposed",
    "B": "current radar front",
    "C": "front + enclosure",
}
MULTIPATH_MARGIN_DB = 3.0
MIN_TRACK_POINTS = 3


@dataclass(frozen=True)
class RigCondition:
    """One physical set-up a capture was taken under."""

    label: str
    yaw_deg: float | None = None
    pitch_deg: float | None = None
    enclosure: str | None = None

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("a rig condition needs a label")
        if self.enclosure is not None and self.enclosure not in ENCLOSURE_CONDITIONS:
            raise ValueError(
                f"enclosure must be one of {ENCLOSURE_CONDITIONS}, got {self.enclosure!r}"
            )


def load_rig_manifest(path: Path) -> dict[str, RigCondition]:
    """The manifest's conditions by dump file name; ``ValueError`` on a bad entry."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("a rig manifest must be a JSON object of file name -> condition")
    conditions = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{name}: condition must be an object")
        unknown = set(entry) - {"label", "yaw_deg", "pitch_deg", "enclosure"}
        if unknown:
            raise ValueError(f"{name}: unknown keys {sorted(unknown)}")
        conditions[name] = RigCondition(
            label=str(entry.get("label", "")),
            yaw_deg=entry.get("yaw_deg"),
            pitch_deg=entry.get("pitch_deg"),
            enclosure=entry.get("enclosure"),
        )
    return conditions


@dataclass(frozen=True)
class RigRow:
    """One condition's medians over its captures."""

    label: str
    captures: int
    yaw_deg: float | None
    pitch_deg: float | None
    enclosure: str | None
    golfer_db: float | None
    club_scr_db: float | None
    ball_scr_db: float | None
    trigger_rate: float
    tracking_rate: float  # club >= 3 points before impact and a ball launch

    @property
    def combined_scr_db(self) -> float | None:
        """The mean of club and ball SCR: ``(club + ball) / golfer`` in dB."""
        values = [v for v in (self.club_scr_db, self.ball_scr_db) if v is not None]
        return statistics.fmean(values) if values else None


def _median(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return statistics.median(kept) if kept else None


def _tracked(m: CaptureMetrics) -> bool:
    return m.club_points_pre >= MIN_TRACK_POINTS and m.ball_speed_mps is not None


def rig_matrix(
    metrics: Sequence[CaptureMetrics], conditions: Mapping[str, RigCondition]
) -> list[RigRow]:
    """One row per condition label, in first-seen order; captures missing from
    the manifest are left out."""
    groups: dict[str, list[CaptureMetrics]] = {}
    first: dict[str, RigCondition] = {}
    for m in metrics:
        condition = conditions.get(m.name)
        if condition is None:
            continue
        groups.setdefault(condition.label, []).append(m)
        first.setdefault(condition.label, condition)
    rows = []
    for label, group in groups.items():
        condition = first[label]
        rows.append(
            RigRow(
                label=label,
                captures=len(group),
                yaw_deg=condition.yaw_deg,
                pitch_deg=condition.pitch_deg,
                enclosure=condition.enclosure,
                golfer_db=_median(m.hotspot_db for m in group),
                club_scr_db=_median(m.club_scr_db for m in group),
                ball_scr_db=_median(m.ball_scr_db for m in group),
                trigger_rate=sum(1 for m in group if m.triggered) / len(group),
                tracking_rate=sum(1 for m in group if _tracked(m)) / len(group),
            )
        )
    return rows


def best_orientation(rows: Sequence[RigRow]) -> RigRow | None:
    """The row with the largest combined SCR; ties go to the higher tracking rate."""
    scored = [r for r in rows if r.combined_scr_db is not None]
    if not scored:
        return None
    return max(scored, key=lambda r: (r.combined_scr_db, r.tracking_rate))


def enclosure_verdict(rows: Sequence[RigRow], margin_db: float = MULTIPATH_MARGIN_DB) -> str:
    """Whether the enclosure adds to the hotspot: B or C at least ``margin_db``
    above A says so; otherwise the golfer's direct return dominates."""
    by = {r.enclosure: r for r in rows if r.enclosure is not None and r.golfer_db is not None}
    if "A" not in by or not ({"B", "C"} & set(by)):
        return "incomplete: need condition A and at least one of B or C"
    exposed = by["A"].golfer_db
    worse = [
        f"{c} (+{by[c].golfer_db - exposed:.1f} dB)"
        for c in ("B", "C")
        if c in by and by[c].golfer_db - exposed >= margin_db
    ]
    if worse:
        return "enclosure/multipath contribution: " + ", ".join(worse) + " over A"
    return (
        f"no enclosure contribution above {margin_db:.1f} dB: "
        "the hotspot is the golfer's direct return"
    )


def _cell(value: float | None, fmt: str = "{:.1f}") -> str:
    return "" if value is None else fmt.format(value)


def format_matrix(rows: Sequence[RigRow]) -> str:
    """The plan's table: condition, golfer dB, club and ball SCR, trigger and
    tracking rates, as Markdown."""
    lines = [
        "| Condition | Captures | Golfer dB | Club SCR dB | Ball SCR dB | Trigger | Tracking |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.label} | {r.captures} | {_cell(r.golfer_db)} | {_cell(r.club_scr_db)} | "
            f"{_cell(r.ball_scr_db)} | {r.trigger_rate:.0%} | {r.tracking_rate:.0%} |"
        )
    return "\n".join(lines)
