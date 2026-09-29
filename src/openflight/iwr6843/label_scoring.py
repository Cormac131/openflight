"""Score the firmware's ball and club tracks against hand labels.

One definition of "how right is the firmware on this dump", used by the
labelled-replay tests and by the constants sweep so they cannot disagree.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openflight.iwr6843 import firmware_replay as fr
from openflight.iwr6843.labels import OBJECTS, LabelPoint, Labels, Tolerances, load_labels

BASELINE_NAME = "label_baseline.json"
# score = coverage - ERROR_WEIGHT * (mean error / tolerance) - FALSE_POINT_WEIGHT * false / labelled
ERROR_WEIGHT = 0.25
FALSE_POINT_WEIGHT = 0.5
_BASELINE_SLACK = 1e-9


@dataclass(frozen=True)
class ObjectScore:
    """One object on one dump."""

    labelled: int
    tracked: int
    matched: int
    false_points: int
    mean_abs_error_bins: float | None
    coverage: float
    score: float


def _within(label: LabelPoint, point, tol: Tolerances) -> bool:
    if abs(point.range_bin - label.range_bin) > tol.range_bins:
        return False
    if tol.doppler_mps is not None and label.doppler_mps is not None:
        return abs(point.doppler_mps - label.doppler_mps) <= tol.doppler_mps
    return True


def score_object(points: Sequence[LabelPoint], tracked: Sequence, tol: Tolerances) -> ObjectScore:
    """Match the firmware's points to the labels by frame."""
    by_frame = {p.frame: p for p in points}
    closest: dict[int, object] = {}
    for point in tracked:
        label = by_frame.get(point.frame)
        if label is None or not _within(label, point, tol):
            continue
        best = closest.get(point.frame)
        if best is None or abs(point.range_bin - label.range_bin) < abs(
            best.range_bin - label.range_bin
        ):
            closest[point.frame] = point
    matched = len(closest)
    errors = [abs(p.range_bin - by_frame[f].range_bin) for f, p in closest.items()]
    mean_error = sum(errors) / len(errors) if errors else None
    false_points = len(tracked) - matched
    labelled = len(points)
    coverage = 1.0 if labelled == 0 else matched / labelled
    error_term = 0.0 if mean_error is None else mean_error / tol.range_bins
    score = (
        coverage - ERROR_WEIGHT * error_term - FALSE_POINT_WEIGHT * false_points / max(labelled, 1)
    )
    return ObjectScore(labelled, len(tracked), matched, false_points, mean_error, coverage, score)


def score_labels(labels: Labels, result: fr.ReplayResult) -> dict[str, ObjectScore]:
    """Both objects: the club track is ``result.points``, the ball track ``result.ball_points``."""
    tracked = {"club": result.points, "ball": result.ball_points}
    return {
        obj: score_object(labels.points(obj), tracked[obj], labels.tolerances) for obj in OBJECTS
    }


def dump_score(scores: Mapping[str, ObjectScore]) -> float:
    """One number per dump: the mean of the object scores."""
    return sum(s.score for s in scores.values()) / len(scores)


def check_labels(labels: Labels, scores: Mapping[str, ObjectScore]) -> list[str]:
    """Why the firmware fails these labels; an empty list is a pass."""
    failures = []
    for obj in OBJECTS:
        s = scores[obj]
        if s.labelled == 0:
            if s.tracked:
                msg = (
                    f"{obj}: {s.tracked} firmware point(s) but the object is labelled "
                    "as not tracked"
                )
                failures.append(msg)
        elif s.coverage < labels.tolerances.min_coverage:
            failures.append(
                f"{obj}: coverage {s.coverage:.2f} below {labels.tolerances.min_coverage:.2f}"
            )
    return failures


def check_against_baseline(
    name: str, labels: Labels, scores: Mapping[str, ObjectScore], baseline: Mapping[str, float]
) -> list[str]:
    """Every reason one dump fails the labelled-replay test; an empty list is a pass."""
    failures = check_labels(labels, scores)
    if name not in baseline:
        failures.append(
            f"{name} has no baseline score; run "
            "`uv run python scripts/analysis/fit_constants.py --update-baseline`"
        )
        return failures
    score = dump_score(scores)
    if score < baseline[name] - _BASELINE_SLACK:
        failures.append(f"{name}: score {score:.4f} fell below the baseline {baseline[name]:.4f}")
    return failures


def reviewed_recordings(
    directory: Path = fr.RECORDINGS_DIR,
) -> list[tuple[Path, fr.ReplayConfig, Labels]]:
    """Recordings with reviewed labels, each with its manifest replay configuration.

    A label file whose dump changed raises ``LabelError``: a stale label must
    fail loudly, not be skipped.
    """
    out = []
    for path, config in fr.recording_configs(directory):
        labels = load_labels(path)
        if labels is not None and labels.reviewed:
            out.append((path, config, labels))
    return out


def load_baseline(directory: Path = fr.RECORDINGS_DIR) -> dict[str, float]:
    """The committed per-dump scores the firmware must not fall below."""
    path = Path(directory) / BASELINE_NAME
    if not path.exists():
        return {}
    return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def write_baseline(directory: Path, scores: Mapping[str, float]) -> None:
    """Replace the baseline; dump names sorted so the diff is stable."""
    path = Path(directory) / BASELINE_NAME
    # unrounded: json round-trips floats exactly, so a fresh baseline always passes
    body = {name: float(scores[name]) for name in sorted(scores)}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
