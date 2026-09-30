"""A/B comparison of one capture processed two ways (roadmap phase 18).

Given the same swing replayed from its IQ16 recording and from the IQ8 the
firmware would have stored (``iq8_emulation``), ``compare_replays`` lines up
every measurement the firmware modules produce: when the trigger fired,
what the club track and delivery found, what the ball track and launch fit
found. ``format_table`` prints the ``Measurement / A / B / delta`` table and
``aggregate`` sums the deltas over a corpus so the question "does IQ16 buy
accuracy" has a number per measurement instead of an impression.

The comparison is between two processing paths of one capture, so every
delta is quantisation and nothing else: same swing, same frames, same
configuration.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from openflight.iwr6843.firmware_replay import ReplayResult

MPS_TO_MPH = 2.23694


@dataclass(frozen=True)
class Row:
    """One measurement under both paths. ``None`` is 'not produced'."""

    name: str
    a: float | None
    b: float | None
    unit: str
    decimals: int = 2

    @property
    def delta(self) -> float | None:
        if self.a is None or self.b is None:
            return None
        return self.b - self.a

    @property
    def agrees(self) -> bool:
        """Both absent, or both present."""
        return (self.a is None) == (self.b is None)


def _first_ball_bin(result: ReplayResult) -> float | None:
    return result.ball_points[0].range_bin if result.ball_points else None


def _delivery(result: ReplayResult, attribute: str) -> float | None:
    if result.delivery is None:
        return None
    return getattr(result.delivery, attribute)


def _launch(result: ReplayResult, attribute: str) -> float | None:
    if result.launch is None:
        return None
    return getattr(result.launch, attribute)


def _mps_to_mph(value: float | None) -> float | None:
    return None if value is None else value * MPS_TO_MPH


def _optional_int(value: int | None) -> float | None:
    return None if value is None else float(value)


def compare_replays(a: ReplayResult, b: ReplayResult) -> list[Row]:
    """Every measurement of interest, A beside B."""
    return [
        Row(
            "Trigger fire frame",
            _optional_int(a.fired_frame),
            _optional_int(b.fired_frame),
            "frame",
            0,
        ),
        Row(
            "Impact time",
            _optional_int(a.impact_timestamp_us),
            _optional_int(b.impact_timestamp_us),
            "us",
            0,
        ),
        Row("Club points", float(len(a.points)), float(len(b.points)), "points", 0),
        Row("Club acquisitions", float(a.acquisitions), float(b.acquisitions), "", 0),
        Row("Club speed (fit)", a.speed_mps, b.speed_mps, "m/s"),
        Row("Club speed (3D)", _delivery(a, "speed_mps"), _delivery(b, "speed_mps"), "m/s"),
        Row("Club path", _delivery(a, "path_deg"), _delivery(b, "path_deg"), "deg"),
        Row("Angle of attack", _delivery(a, "attack_deg"), _delivery(b, "attack_deg"), "deg"),
        Row(
            "Club fit residual",
            _delivery(a, "residual_m"),
            _delivery(b, "residual_m"),
            "m",
            4,
        ),
        Row("Ball points", float(len(a.ball_points)), float(len(b.ball_points)), "points", 0),
        Row("First ball bin", _first_ball_bin(a), _first_ball_bin(b), "bin"),
        Row("Ball speed", _launch(a, "speed_mps"), _launch(b, "speed_mps"), "m/s"),
        Row(
            "Ball speed",
            _mps_to_mph(_launch(a, "speed_mps")),
            _mps_to_mph(_launch(b, "speed_mps")),
            "mph",
            1,
        ),
        Row("HLA", _launch(a, "hla_deg"), _launch(b, "hla_deg"), "deg"),
        Row("VLA", _launch(a, "vla_deg"), _launch(b, "vla_deg"), "deg"),
        Row("Ball fit residual", _launch(a, "residual_m"), _launch(b, "residual_m"), "m", 4),
        Row("Launch confidence", _launch(a, "confidence"), _launch(b, "confidence"), ""),
    ]


def _fmt(value: float | None, decimals: int) -> str:
    if value is None:
        return "-"
    if decimals == 0:
        return f"{value:.0f}"
    return f"{value:.{decimals}f}"


def format_table(rows: Sequence[Row], *, a_label: str = "IQ16", b_label: str = "IQ8") -> str:
    """Fixed-width text table with a signed delta column."""
    width = max(len(row.name) for row in rows) if rows else 11
    lines = [f"{'Measurement':<{width}}  {a_label:>10}  {b_label:>10}  {'delta':>10}  unit"]
    for row in rows:
        delta = row.delta
        delta_text = (
            "-"
            if delta is None
            else (f"{delta:+.{row.decimals}f}" if row.decimals else f"{delta:+.0f}")
        )
        if not row.agrees:
            delta_text = "ONLY " + (a_label if row.a is not None else b_label)
        lines.append(
            f"{row.name:<{width}}  {_fmt(row.a, row.decimals):>10}  {_fmt(row.b, row.decimals):>10}  "
            f"{delta_text:>10}  {row.unit}"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class Aggregate:
    """One measurement over a corpus: how often B differed from A and by how much."""

    name: str
    unit: str
    captures: int  # captures where both paths produced the measurement
    disagreements: int  # captures where only one path produced it
    mean_abs_delta: float | None
    max_abs_delta: float | None
    bias: float | None  # mean signed delta (B - A)


def aggregate(tables: Iterable[Sequence[Row]]) -> list[Aggregate]:
    """Per measurement over many captures: how often B differed and by how much."""
    by_name: dict[tuple[str, str], list[Row]] = {}
    order: list[tuple[str, str]] = []
    for rows in tables:
        for row in rows:
            key = (row.name, row.unit)
            if key not in by_name:
                by_name[key] = []
                order.append(key)
            by_name[key].append(row)
    out: list[Aggregate] = []
    for key in order:
        rows = by_name[key]
        deltas = [row.delta for row in rows if row.delta is not None]
        disagreements = sum(1 for row in rows if not row.agrees)
        out.append(
            Aggregate(
                name=key[0],
                unit=key[1],
                captures=len(deltas),
                disagreements=disagreements,
                mean_abs_delta=(sum(abs(d) for d in deltas) / len(deltas)) if deltas else None,
                max_abs_delta=max((abs(d) for d in deltas), default=None),
                bias=(sum(deltas) / len(deltas)) if deltas else None,
            )
        )
    return out


def format_aggregate(aggregates: Sequence[Aggregate], *, b_label: str = "IQ8") -> str:
    """Fixed-width text table of :func:`aggregate`."""
    width = max(len(a.name) for a in aggregates) if aggregates else 11
    lines = [
        f"{'Measurement':<{width}}  {'n':>3}  {'only-one':>8}  {'mean|d|':>10}  {'max|d|':>10}  "
        f"{'bias ' + b_label:>10}  unit"
    ]
    for a in aggregates:
        lines.append(
            f"{a.name:<{width}}  {a.captures:>3}  {a.disagreements:>8}  "
            f"{_fmt(a.mean_abs_delta, 3):>10}  {_fmt(a.max_abs_delta, 3):>10}  "
            f"{('-' if a.bias is None else f'{a.bias:+.3f}'):>10}  {a.unit}"
        )
    return "\n".join(lines)


def rows_to_dict(rows: Sequence[Row]) -> list[dict]:
    """JSON-ready rows (NaN never leaks: None stays None)."""
    out = []
    for row in rows:
        delta = row.delta
        out.append(
            {
                "name": row.name,
                "unit": row.unit,
                "a": row.a,
                "b": row.b,
                "delta": None if delta is None or math.isnan(delta) else delta,
            }
        )
    return out


__all__ = [
    "Aggregate",
    "Row",
    "aggregate",
    "compare_replays",
    "format_aggregate",
    "format_table",
    "rows_to_dict",
]
