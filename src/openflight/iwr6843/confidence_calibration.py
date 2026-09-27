"""Turn confidence numbers into error bounds from real validation data (phase 29).

The firmware's confidences are heuristics until they are checked against
error: "confidence above 0.9 means 95% of path readings are within X
degrees" is a measured statement, not a design one. Given pairs of
(confidence, absolute error) for one metric, ``calibrate`` reports the error
distribution per confidence band and ``threshold_for`` finds the lowest
confidence at which a chosen share of readings meets an error bound, which
is the number the shot validation should reject below. Pairs come from the
OPS comparison (speeds) and the reference-monitor validation (angles).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

DEFAULT_BANDS = ((0.9, 1.0001), (0.7, 0.9), (0.5, 0.7), (0.0, 0.5))


@dataclass(frozen=True)
class ErrorPair:
    confidence: float
    abs_error: float


@dataclass(frozen=True)
class BandCalibration:
    low: float
    high: float
    count: int
    mae: float | None
    p95: float | None
    max: float | None
    within_bound: float | None  # share of readings inside `bound`, when a bound was given


def _percentile(sorted_values: Sequence[float], share: float) -> float:
    index = min(len(sorted_values) - 1, max(0, int(math.ceil(share * len(sorted_values))) - 1))
    return sorted_values[index]


def calibrate(
    pairs: Iterable[ErrorPair],
    *,
    bands: Sequence[tuple[float, float]] = DEFAULT_BANDS,
    bound: float | None = None,
) -> list[BandCalibration]:
    """Error distribution per confidence band, highest band first."""
    rows = list(pairs)
    out = []
    for low, high in bands:
        errors = sorted(p.abs_error for p in rows if low <= p.confidence < high)
        if not errors:
            out.append(BandCalibration(low, high, 0, None, None, None, None))
            continue
        within = None
        if bound is not None:
            within = sum(1 for e in errors if e <= bound) / len(errors)
        out.append(
            BandCalibration(
                low,
                high,
                len(errors),
                sum(errors) / len(errors),
                _percentile(errors, 0.95),
                errors[-1],
                within,
            )
        )
    return out


def threshold_for(
    pairs: Iterable[ErrorPair], *, bound: float, coverage: float = 0.95, min_count: int = 20
) -> float | None:
    """The lowest observed confidence at or above which `coverage` of readings are within `bound`.

    Candidate thresholds are the distinct confidences seen, highest first;
    the scan stops at the first that fails, since lower thresholds only add
    readings to the same set. None when no threshold with at least
    `min_count` readings reaches the coverage.
    """
    rows = sorted(pairs, key=lambda p: p.confidence, reverse=True)
    if not rows:
        return None
    best = None
    for threshold in sorted({p.confidence for p in rows}, reverse=True):
        selected = [p.abs_error for p in rows if p.confidence >= threshold]
        if len(selected) < min_count:
            continue
        share = sum(1 for e in selected if e <= bound) / len(selected)
        if share >= coverage:
            best = threshold
        else:
            break
    return best


def format_calibration(
    rows: Sequence[BandCalibration], *, unit: str, bound: float | None = None
) -> str:
    header = f"{'confidence':>12}  {'n':>4} {'mae':>7} {'p95':>7} {'max':>7}"
    if bound is not None:
        header += f"  {'within ' + f'{bound:g}{unit}':>14}"
    lines = [header]
    for r in rows:
        line = (
            f"{r.low:>5.2f}-{r.high if r.high <= 1.0 else 1.0:<5.2f}  {r.count:>4} "
            f"{'-' if r.mae is None else f'{r.mae:.2f}':>7} {'-' if r.p95 is None else f'{r.p95:.2f}':>7} "
            f"{'-' if r.max is None else f'{r.max:.2f}':>7}"
        )
        if bound is not None:
            line += f"  {'-' if r.within_bound is None else f'{100 * r.within_bound:.0f}%':>14}"
        lines.append(line)
    return "\n".join(lines)


__all__ = [
    "DEFAULT_BANDS",
    "BandCalibration",
    "ErrorPair",
    "calibrate",
    "format_calibration",
    "threshold_for",
]
