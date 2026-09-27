"""OPS243 versus IWR6843 speed comparison, per shot and over a session (phase 27).

The OPS243 measures the two easiest radar quantities, ball speed and club
speed, independently of the IWR6843. Every shot that carries an onboard
result therefore yields two deltas for free, and over hundreds of shots
those deltas say what the IWR's speeds are worth: bias, mean absolute
error, RMS error, 95th percentile, and how each behaves against the IWR's
own confidence, the club and the capture format. The two are never
averaged: the OPS stays the validator.

``OpsComparison.from_shot`` builds the per-shot record the server writes to
the session log (``iwr_ops_comparison``); ``summarize`` and the grouping
helpers reduce a session's records; ``scripts/analysis/ops_validation.py``
runs them over session files.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

MPS_TO_MPH = 2.23694
CONFIDENCE_BANDS = ((0.9, 1.01, "high"), (0.7, 0.9, "medium"), (0.0, 0.7, "low"))


@dataclass(frozen=True)
class OpsComparison:
    """One shot: the OPS speeds beside the IWR's, with the IWR's confidence."""

    shot_number: int | None
    timestamp: str
    club: str | None
    capture_format: str | None
    verdict: str | None
    ops_ball_speed_mph: float | None
    ops_club_speed_mph: float | None
    iwr_ball_speed_mph: float | None
    iwr_ball_confidence: float | None
    iwr_club_speed_mph: float | None
    iwr_club_confidence: float | None
    impact_range_m: float | None

    @property
    def ball_delta_mph(self) -> float | None:
        """IWR minus OPS."""
        return _delta(self.iwr_ball_speed_mph, self.ops_ball_speed_mph)

    @property
    def club_delta_mph(self) -> float | None:
        return _delta(self.iwr_club_speed_mph, self.ops_club_speed_mph)

    @property
    def ball_percent(self) -> float | None:
        return _percent(self.iwr_ball_speed_mph, self.ops_ball_speed_mph)

    @property
    def club_percent(self) -> float | None:
        return _percent(self.iwr_club_speed_mph, self.ops_club_speed_mph)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.update(
            ball_delta_mph=self.ball_delta_mph,
            club_delta_mph=self.club_delta_mph,
            ball_percent=self.ball_percent,
            club_percent=self.club_percent,
        )
        return data

    @classmethod
    def from_dict(cls, data: dict) -> OpsComparison:
        names = {f for f in cls.__dataclass_fields__}  # pylint: disable=no-member
        return cls(**{key: data.get(key) for key in names})

    @classmethod
    def from_shot(cls, shot, onboard, *, capture_format: str | None = None) -> OpsComparison:
        """From a ``Shot`` and a parsed ``ShotResultPacket``."""
        ball = onboard["ball_speed"]
        club = onboard["club_speed"]
        rng = onboard["impact_range"]
        return cls(
            shot_number=getattr(shot, "shot_number", None),
            timestamp=shot.timestamp.isoformat()
            if hasattr(shot.timestamp, "isoformat")
            else str(shot.timestamp),
            club=getattr(getattr(shot, "club", None), "value", None),
            capture_format=capture_format,
            verdict=onboard.verdict,
            ops_ball_speed_mph=shot.ball_speed_mph,
            ops_club_speed_mph=shot.club_speed_mph,
            iwr_ball_speed_mph=(ball.value * MPS_TO_MPH) if ball.usable else None,
            iwr_ball_confidence=ball.confidence if ball.value is not None else None,
            iwr_club_speed_mph=(club.value * MPS_TO_MPH) if club.usable else None,
            iwr_club_confidence=club.confidence if club.value is not None else None,
            impact_range_m=rng.value if rng.value is not None else None,
        )


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a - b


def _percent(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return 100.0 * (a - b) / b


@dataclass(frozen=True)
class ErrorStats:
    """Bias, MAE, RMSE and P95 of a set of signed errors."""

    count: int
    bias: float | None
    mae: float | None
    rmse: float | None
    p95_abs: float | None
    max_abs: float | None

    @classmethod
    def of(cls, errors: Iterable[float]) -> ErrorStats:
        values = [float(e) for e in errors if e is not None and not math.isnan(e)]
        if not values:
            return cls(0, None, None, None, None, None)
        n = len(values)
        absolute = sorted(abs(v) for v in values)
        return cls(
            count=n,
            bias=sum(values) / n,
            mae=sum(absolute) / n,
            rmse=math.sqrt(sum(v * v for v in values) / n),
            p95_abs=absolute[min(n - 1, int(math.ceil(0.95 * n)) - 1)],
            max_abs=absolute[-1],
        )


@dataclass(frozen=True)
class Summary:
    shots: int
    with_result: int
    ball: ErrorStats
    club: ErrorStats
    ball_percent: ErrorStats
    club_percent: ErrorStats


def summarize(records: Iterable[OpsComparison]) -> Summary:
    rows = list(records)
    with_result = [r for r in rows if r.verdict is not None]
    return Summary(
        shots=len(rows),
        with_result=len(with_result),
        ball=ErrorStats.of(r.ball_delta_mph for r in rows if r.ball_delta_mph is not None),
        club=ErrorStats.of(r.club_delta_mph for r in rows if r.club_delta_mph is not None),
        ball_percent=ErrorStats.of(r.ball_percent for r in rows if r.ball_percent is not None),
        club_percent=ErrorStats.of(r.club_percent for r in rows if r.club_percent is not None),
    )


def group_by(records: Iterable[OpsComparison], key: str) -> dict[str, list[OpsComparison]]:
    """``key`` is "club", "capture_format", "verdict" or "ball_confidence" / "club_confidence" (bands)."""
    groups: dict[str, list[OpsComparison]] = {}
    for record in records:
        if key.endswith("_confidence"):
            confidence = getattr(record, f"iwr_{key}")
            name = (
                "none"
                if confidence is None
                else next(
                    (band for low, high, band in CONFIDENCE_BANDS if low <= confidence < high),
                    "low",
                )
            )
        else:
            value = getattr(record, key)
            name = "unknown" if value is None else str(value)
        groups.setdefault(name, []).append(record)
    return groups


def read_session(path: str | Path) -> Iterator[OpsComparison]:
    """The ``iwr_ops_comparison`` entries of one session JSONL file."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "iwr_ops_comparison":
                yield OpsComparison.from_dict(entry)


def read_sessions(paths: Iterable[str | Path]) -> list[OpsComparison]:
    records: list[OpsComparison] = []
    for text in paths:
        path = Path(text)
        if not path.exists():
            continue
        files = sorted(path.glob("session_*.jsonl")) if path.is_dir() else [path]
        for file in files:
            records.extend(read_session(file))
    return records


def _fmt(value: float | None, decimals: int = 2) -> str:
    return "-" if value is None else f"{value:+.{decimals}f}"


def _fmt_abs(value: float | None, decimals: int = 2) -> str:
    return "-" if value is None else f"{value:.{decimals}f}"


def format_stats(name: str, stats: ErrorStats, unit: str) -> str:
    return (
        f"{name:<14} n={stats.count:<4} bias={_fmt(stats.bias):>7} mae={_fmt_abs(stats.mae):>6} "
        f"rmse={_fmt_abs(stats.rmse):>6} p95={_fmt_abs(stats.p95_abs):>6} max={_fmt_abs(stats.max_abs):>6} {unit}"
    )


def format_summary(summary: Summary, *, title: str = "all shots") -> str:
    lines = [f"== {title}: {summary.shots} shots, {summary.with_result} with an onboard result"]
    lines.append(format_stats("ball speed", summary.ball, "mph"))
    lines.append(format_stats("ball speed", summary.ball_percent, "%"))
    lines.append(format_stats("club speed", summary.club, "mph"))
    lines.append(format_stats("club speed", summary.club_percent, "%"))
    return "\n".join(lines)


__all__ = [
    "CONFIDENCE_BANDS",
    "MPS_TO_MPH",
    "ErrorStats",
    "OpsComparison",
    "Summary",
    "format_stats",
    "format_summary",
    "group_by",
    "read_session",
    "read_sessions",
    "summarize",
]
