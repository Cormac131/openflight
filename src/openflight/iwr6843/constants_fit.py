"""Offline sweep of firmware constants against hand-labelled dumps.

Coordinate descent: one constant at a time, over a small grid around its
current value, keeping the best. It reports; it never edits the firmware.
The report shows how flat each optimum is, because with few labelled dumps a
flat score curve means "the labels cannot tell", not "any value is fine".
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from openflight.iwr6843 import firmware_replay as fr, label_scoring as ls
from openflight.iwr6843.labels import OBJECTS, Labels
from openflight.iwr6843.tunables import Tunable

GRID_STEPS = 3
_EPS = 1e-9


@dataclass(frozen=True)
class SweepRow:
    """One constant's outcome."""

    name: str
    default: float
    suggested: float
    gain: float  # score with the suggestion minus score with this constant at its default
    grid: tuple[tuple[float, float], ...]  # (value, score) from the last pass

    @property
    def changed(self) -> bool:
        return self.suggested != self.default

    @property
    def flat(self) -> int:
        """How many grid values score within 1e-9 of the best: all of them is a flat curve."""
        best = max(score for _, score in self.grid)
        return sum(1 for _, score in self.grid if abs(score - best) <= _EPS)


def candidate_values(t: Tunable, current: float) -> list[float]:
    """``current`` and ``GRID_STEPS`` steps either side, inside the bounds."""
    values = set()
    for k in range(-GRID_STEPS, GRID_STEPS + 1):
        if k == 0:
            continue  # ``current`` is added as given: a float32 default must not appear twice
        value = min(max(current + k * t.step, t.low), t.high)
        values.add(int(round(value)) if t.kind == "int" else round(value, 6))
    values.add(int(round(current)) if t.kind == "int" else current)
    return sorted(values)


def coordinate_descent(
    evaluate: Callable[[Mapping[str, float]], float],
    tunables: Sequence[Tunable],
    defaults: Mapping[str, float],
    passes: int = 2,
) -> list[SweepRow]:
    """Sweep each constant in turn, ``passes`` times, and return one row per constant."""
    cache: dict[tuple, float] = {}

    def score(settings: Mapping[str, float]) -> float:
        key = tuple(sorted(settings.items()))
        if key not in cache:
            cache[key] = evaluate(dict(settings))
        return cache[key]

    current = {t.name: defaults[t.name] for t in tunables}
    grids: dict[str, tuple[tuple[float, float], ...]] = {}
    for _ in range(passes):
        for t in tunables:
            scored = [
                (v, score({**current, t.name: v})) for v in candidate_values(t, current[t.name])
            ]
            grids[t.name] = tuple(scored)
            here = score(current)
            best_value, best_score = max(scored, key=lambda item: item[1])
            if best_score > here + _EPS:
                current[t.name] = best_value
    final = score(current)
    rows = []
    for t in tunables:
        reset = score({**current, t.name: defaults[t.name]})
        rows.append(
            SweepRow(
                name=t.name,
                default=defaults[t.name],
                suggested=current[t.name],
                gain=0.0 if current[t.name] == defaults[t.name] else final - reset,
                grid=grids[t.name],
            )
        )
    return rows


def evaluate_recordings(
    recordings: Sequence[tuple[bytes, fr.ReplayConfig, Labels]],
    overrides: Mapping[str, float],
) -> float:
    """Mean dump score over the labelled dumps; -inf when the firmware rejects the settings."""
    total = 0.0
    for raw, config, labels in recordings:
        try:
            result = fr.replay_dump(raw, replace(config, overrides=dict(overrides)))
        except ValueError:
            return -math.inf
        total += ls.dump_score(ls.score_labels(labels, result))
    return total / len(recordings)


def _shown(value: float) -> str:
    """A constant as a person writes it: float32 defaults read back as 0.2, not 0.200000003."""
    return str(round(value, 6))


def format_report(
    rows: Sequence[SweepRow],
    baseline_score: float,
    final_score: float,
    n_dumps: int,
    n_points: int,
) -> str:
    """A table a person reads before editing the C defaults."""
    lines = [
        f"fit on {n_dumps} dumps, {n_points} labelled points",
        f"score at the firmware defaults {baseline_score:.3f}, at the suggestions {final_score:.3f}",
        "",
        f"{'constant':32} {'default':>9} {'suggested':>9} {'gain':>8}  flat",
    ]
    for row in sorted(rows, key=lambda r: -r.gain):
        mark = "*" if row.changed else " "
        lines.append(
            f"{row.name:32} {_shown(row.default):>9} {_shown(row.suggested):>9} {row.gain:>+8.3f}  "
            f"{row.flat}/{len(row.grid)} {mark}"
        )
    lines += [
        "",
        "flat n/n: the labels cannot tell these values apart; do not change the constant.",
        "* marks a suggested change; apply it by hand in firmware/iwr6843/*.c and re-run the tests.",
    ]
    return "\n".join(lines)


def count_points(recordings: Sequence[tuple[bytes, fr.ReplayConfig, Labels]]) -> int:
    """Labelled points across the fit set, for the report's header."""
    return sum(len(labels.points(obj)) for _, _, labels in recordings for obj in OBJECTS)
