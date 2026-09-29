# Track labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the IWR6843 dump viewer label ball and club track points, save them beside the dump, verify firmware changes against them in tests, and fit firmware constants against them offline.

**Architecture:** A pure-Python `labels.py` owns the sidecar file format. `label_scoring.py` compares a `ReplayResult` to labels and is shared by the tests and the sweep. The viewer's Flask app gets `GET`/`PUT /api/labels`, and its page gets an annotate mode. `tunables.py` plus `ReplayConfig.overrides` let Python override firmware config constants, and `constants_fit.py` runs a coordinate-descent sweep and prints a report.

**Tech Stack:** Python 3 (`uv run`), Flask, ctypes host build of `firmware/iwr6843/l3_*.c`, Plotly page, pytest, pylint (>= 9.0), ruff.

**Spec:** `docs/superpowers/specs/2026-09-29-track-labels-design.md`

## Global Constraints

- Always run Python tools through `uv run` (`uv run pytest`, `uv run pylint`, `uv run ruff`); never bare `python`/`pytest`/`pip`.
- New Python dependencies go in `pyproject.toml`. This plan adds none (stdlib, Flask, numpy already present).
- Labels live in `<dump filename>.labels.json` beside the dump (for example `shot_001.l3dump.labels.json`), never inside `manifest.json`; replay settings (`tee_bin`, `dest_bin`, `post_from_frame`, ...) stay in `manifest.json`.
- Label points are `{frame, range_bin, doppler_mps?}`: `frame` is the frame index, `range_bin` is the fractional global range bin (same units as `PointSummary.range_bin`).
- Schema `version` is `1`. Unknown keys are errors (as `Expectation.from_manifest` does).
- A reviewed object with zero points means "the firmware must not track it".
- The sweep never edits `firmware/iwr6843/*.c`. The constants are applied by hand.
- Lint gate: `uv run pylint src/openflight/ --fail-under=9`, `uv run ruff check src/openflight/`, `uv run ruff format --check src/openflight/`.
- Tests that need the C host build use the existing `needs_compiler` marker pattern (`fw.host_compiler() is None`).

## Review Focus

Failure modes the spec implies that no requirement spells out. Each one is pinned by a test in the task named after it.

1. **Dump replaced after labelling.** The label file's SHA no longer matches. Expected: `load_labels` raises `LabelError` naming the file, and the labelled-replay test fails loudly rather than scoring against the wrong data. (Task 1, Task 6)
2. **Label frame beyond the dump.** A saved point on frame 500 of an 18-frame dump. Expected: the `PUT` is refused with 400, so the tests never see it. (Task 3)
3. **Torn or failed save.** The process dies or `os.replace` fails mid-save. Expected: the previous label file is intact and no temp file is left behind. (Task 1)
4. **Reviewed dump where nothing should be tracked.** Both objects have zero points. Expected: coverage is 1.0 with no divide by zero, a firmware point on either object is a failure. (Task 2)
5. **A candidate constant the firmware rejects.** `l3_trig_cfg_check` fails for a swept value. Expected: that candidate scores `-inf` and the sweep continues. (Task 7)

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/openflight/iwr6843/labels.py` (new) | Label dataclasses, JSON schema validation, hashing, atomic load/save |
| `src/openflight/iwr6843/label_scoring.py` (new) | Match a `ReplayResult` to `Labels`; scores, pass/fail reasons, reviewed-recording discovery, baseline file |
| `src/openflight/iwr6843/tunables.py` (new) | Registry of overridable firmware constants; apply/validate overrides; read firmware defaults |
| `src/openflight/iwr6843/constants_fit.py` (new) | Coordinate descent, ablation, report formatting |
| `src/openflight/iwr6843/firmware_replay.py` (modify) | `ReplayConfig.overrides`, applied inside `replay_dump` |
| `scripts/iwr6843/dump_viewer.py` (modify) | `GET`/`PUT /api/labels` |
| `scripts/iwr6843/dump_viewer.html` (modify) | Annotate mode UI |
| `scripts/analysis/fit_constants.py` (new) | CLI wrapper: sweep report and `--update-baseline` |
| `tests/test_iwr6843_labels.py` (new) | Label schema, hash, save tests |
| `tests/test_iwr6843_label_scoring.py` (new) | Scoring, discovery, baseline, synthetic end-to-end |
| `tests/test_iwr6843_dump_viewer.py` (modify) | Labels endpoints and page ids |
| `tests/test_iwr6843_tunables.py` (new) | Registry and overrides |
| `tests/test_iwr6843_constants_fit.py` (new) | Sweep logic with a fake evaluator |
| `tests/test_iwr6843_labelled_replay.py` (new) | Firmware vs committed labels, with baseline ratchet |
| `tests/radar/recordings/README.md`, `docs/changelog.md`, `docs/reference/cli.md` (modify) | Document the workflow |

---

### Task 1: Label file format (`labels.py`)

**Files:**
- Create: `src/openflight/iwr6843/labels.py`
- Test: `tests/test_iwr6843_labels.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `LABELS_VERSION: int = 1`, `OBJECTS: tuple[str, str] = ("ball", "club")`, `SUFFIX: str = ".labels.json"`
  - `class LabelError(ValueError)`
  - `@dataclass(frozen=True) LabelPoint(frame: int, range_bin: float, doppler_mps: float | None = None)`
  - `@dataclass(frozen=True) Tolerances(range_bins: float = 1.0, min_coverage: float = 0.8, doppler_mps: float | None = None)`
  - `@dataclass(frozen=True) Labels(dump: str, dump_sha256: str, reviewed: bool = False, ball: tuple[LabelPoint, ...] = (), club: tuple[LabelPoint, ...] = (), tolerances: Tolerances = Tolerances(), notes: str = "")` with `.points(obj) -> tuple[LabelPoint, ...]`, `.to_json() -> dict`, `Labels.from_json(raw: dict) -> Labels`
  - `dump_sha256(raw: bytes) -> str`, `labels_path_for(dump_path: Path) -> Path`, `empty_labels(dump_path: Path) -> Labels`, `load_labels(dump_path: Path) -> Labels | None`, `save_labels(dump_path: Path, labels: Labels) -> None`, `labels_from_payload(payload: dict, dump_path: Path) -> Labels`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_iwr6843_labels.py`:

```python
"""The label sidecar: what a person marked as the true ball and club tracks in one dump."""

from __future__ import annotations

import json
import os

import pytest

from openflight.iwr6843 import labels as lb

DUMP_BYTES = b"not a real dump, only its hash matters here"


@pytest.fixture
def dump(tmp_path):
    path = tmp_path / "shot_001.l3dump"
    path.write_bytes(DUMP_BYTES)
    return path


def _labels(dump, **overrides):
    base = lb.empty_labels(dump)
    fields = {
        "reviewed": True,
        "ball": (lb.LabelPoint(41, 52.5, 33.0), lb.LabelPoint(42, 55.0)),
        "club": (lb.LabelPoint(10, 30.0),),
        "notes": "clean",
    }
    fields.update(overrides)
    return lb.Labels(
        dump=base.dump,
        dump_sha256=base.dump_sha256,
        tolerances=lb.Tolerances(range_bins=1.5, min_coverage=0.7),
        **fields,
    )


def test_sidecar_sits_next_to_the_dump_and_is_not_a_capture(dump):
    path = lb.labels_path_for(dump)
    assert path == dump.with_name("shot_001.l3dump.labels.json")
    assert path.suffix != ".l3dump"


def test_save_then_load_round_trips(dump):
    labels = _labels(dump)
    lb.save_labels(dump, labels)
    assert lb.load_labels(dump) == labels


def test_load_without_a_file_is_none(dump):
    assert lb.load_labels(dump) is None


def test_empty_labels_carry_the_dump_name_and_hash(dump):
    labels = lb.empty_labels(dump)
    assert labels.dump == "shot_001.l3dump"
    assert labels.dump_sha256 == lb.dump_sha256(DUMP_BYTES)
    assert not labels.reviewed
    assert labels.ball == () and labels.club == ()


def test_points_are_sorted_by_frame_on_load():
    raw = _minimal_json(ball=[{"frame": 5, "range_bin": 1.0}, {"frame": 2, "range_bin": 2.0}])
    assert [p.frame for p in lb.Labels.from_json(raw).ball] == [2, 5]


def test_a_reviewed_object_with_no_points_round_trips(dump):
    labels = _labels(dump, ball=(), club=())
    lb.save_labels(dump, labels)
    loaded = lb.load_labels(dump)
    assert loaded.reviewed and loaded.ball == () and loaded.club == ()


def test_changed_dump_is_refused_on_load(dump):
    lb.save_labels(dump, _labels(dump))
    dump.write_bytes(DUMP_BYTES + b"!")
    with pytest.raises(lb.LabelError, match="shot_001.l3dump"):
        lb.load_labels(dump)


def test_save_refuses_labels_for_another_dump(dump):
    stale = _labels(dump)
    dump.write_bytes(DUMP_BYTES + b"!")
    with pytest.raises(lb.LabelError, match="hash"):
        lb.save_labels(dump, stale)
    assert not lb.labels_path_for(dump).exists()


def _minimal_json(**objects):
    raw = {"version": 1, "dump": "d.l3dump", "dump_sha256": "ab" * 32}
    for name, points in objects.items():
        raw[name] = {"points": points}
    return raw


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(version=2), "version"),
        (lambda r: r.update(extra=1), "unknown"),
        (lambda r: r.update(ball={"points": [{"frame": -1, "range_bin": 1.0}]}), "frame"),
        (lambda r: r.update(ball={"points": [{"frame": 1.5, "range_bin": 1.0}]}), "frame"),
        (lambda r: r.update(ball={"points": [{"frame": True, "range_bin": 1.0}]}), "frame"),
        (lambda r: r.update(ball={"points": [{"frame": 1, "range_bin": float("nan")}]}), "range_bin"),
        (lambda r: r.update(ball={"points": [{"frame": 1, "range_bin": -1.0}]}), "range_bin"),
        (lambda r: r.update(ball={"points": [{"frame": 1, "range_bin": 1.0, "doppler_mps": "x"}]}), "doppler"),
        (
            lambda r: r.update(
                ball={"points": [{"frame": 1, "range_bin": 1.0}, {"frame": 1, "range_bin": 2.0}]}
            ),
            "duplicate",
        ),
        (lambda r: r.update(ball={"points": [{"frame": 1}]}), "range_bin"),
        (lambda r: r.update(ball=[]), "ball"),
        (lambda r: r.update(tolerances={"range_bins": 0}), "range_bins"),
        (lambda r: r.update(tolerances={"min_coverage": 1.5}), "min_coverage"),
        (lambda r: r.update(tolerances={"doppler_mps": -1}), "doppler_mps"),
        (lambda r: r.update(tolerances={"nope": 1}), "unknown"),
        (lambda r: r.update(reviewed="yes"), "reviewed"),
    ],
)
def test_invalid_files_are_refused_with_a_reason(mutate, message):
    raw = _minimal_json()
    mutate(raw)
    with pytest.raises(lb.LabelError, match=message):
        lb.Labels.from_json(raw)


def test_unparseable_json_is_a_label_error(dump):
    lb.labels_path_for(dump).write_text("{not json", encoding="utf-8")
    with pytest.raises(lb.LabelError, match="shot_001.l3dump.labels.json"):
        lb.load_labels(dump)


def test_failed_save_keeps_the_old_file_and_leaves_no_temp(dump, monkeypatch):
    lb.save_labels(dump, _labels(dump, notes="first"))

    def boom(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk went away"):
        lb.save_labels(dump, _labels(dump, notes="second"))
    monkeypatch.undo()
    assert lb.load_labels(dump).notes == "first"
    assert sorted(p.name for p in dump.parent.iterdir()) == [
        "shot_001.l3dump",
        "shot_001.l3dump.labels.json",
    ]


def test_payload_from_the_page_gets_the_servers_name_and_hash(dump):
    payload = {
        "version": 1,
        "dump": "whatever.l3dump",
        "dump_sha256": "stale",
        "reviewed": True,
        "ball": {"points": [{"frame": 3, "range_bin": 50.0}]},
    }
    labels = lb.labels_from_payload(payload, dump)
    assert labels.dump == "shot_001.l3dump"
    assert labels.dump_sha256 == lb.dump_sha256(DUMP_BYTES)
    assert labels.ball == (lb.LabelPoint(3, 50.0),)


def test_saved_file_is_readable_json_with_a_trailing_newline(dump):
    lb.save_labels(dump, _labels(dump))
    text = lb.labels_path_for(dump).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text)["ball"]["points"][0] == {
        "frame": 41,
        "range_bin": 52.5,
        "doppler_mps": 33.0,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_labels.py -v`
Expected: collection error, `ImportError: cannot import name 'labels'`.

- [ ] **Step 3: Write the implementation**

Create `src/openflight/iwr6843/labels.py`:

```python
"""Hand-placed ground truth for the tracks in one IWR6843 dump.

A ``<dump>.labels.json`` sidecar holds, per object (``ball``, ``club``), the
frames on which the object is visible and where it is: ``range_bin`` is the
fractional *global* range bin, the same units as ``PointSummary.range_bin``.
A reviewed object with no points means "the firmware must not track it".
``dump_sha256`` ties the file to the exact capture it was drawn on.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

LABELS_VERSION = 1
OBJECTS = ("ball", "club")
SUFFIX = ".labels.json"

_TOP_KEYS = {"version", "dump", "dump_sha256", "reviewed", *OBJECTS, "tolerances", "notes"}
_POINT_KEYS = {"frame", "range_bin", "doppler_mps"}
_TOLERANCE_KEYS = {"range_bins", "min_coverage", "doppler_mps"}


class LabelError(ValueError):
    """A label file, or a payload for one, that cannot be trusted."""


@dataclass(frozen=True)
class LabelPoint:
    """One object on one frame."""

    frame: int
    range_bin: float
    doppler_mps: float | None = None


@dataclass(frozen=True)
class Tolerances:
    """How closely the firmware must match, and how much of the track it must cover."""

    range_bins: float = 1.0
    min_coverage: float = 0.8
    doppler_mps: float | None = None


@dataclass(frozen=True)
class Labels:
    """Everything marked on one dump."""

    dump: str
    dump_sha256: str
    reviewed: bool = False
    ball: tuple[LabelPoint, ...] = ()
    club: tuple[LabelPoint, ...] = ()
    tolerances: Tolerances = field(default_factory=Tolerances)
    notes: str = ""

    def points(self, obj: str) -> tuple[LabelPoint, ...]:
        """The points of ``"ball"`` or ``"club"``."""
        if obj not in OBJECTS:
            raise LabelError(f"object must be one of {OBJECTS}, got {obj!r}")
        return self.ball if obj == "ball" else self.club

    def to_json(self) -> dict:
        """The file's content."""

        def points(items: tuple[LabelPoint, ...]) -> dict:
            out = []
            for p in items:
                row = {"frame": p.frame, "range_bin": p.range_bin}
                if p.doppler_mps is not None:
                    row["doppler_mps"] = p.doppler_mps
                out.append(row)
            return {"points": out}

        return {
            "version": LABELS_VERSION,
            "dump": self.dump,
            "dump_sha256": self.dump_sha256,
            "reviewed": self.reviewed,
            "ball": points(self.ball),
            "club": points(self.club),
            "tolerances": {
                "range_bins": self.tolerances.range_bins,
                "min_coverage": self.tolerances.min_coverage,
                "doppler_mps": self.tolerances.doppler_mps,
            },
            "notes": self.notes,
        }

    @classmethod
    def from_json(cls, raw: dict) -> Labels:
        """Validate ``raw`` and build the labels; every failure is a ``LabelError``."""
        if not isinstance(raw, dict):
            raise LabelError("labels must be a JSON object")
        unknown = sorted(set(raw) - _TOP_KEYS)
        if unknown:
            raise LabelError(f"unknown keys {unknown}; known: {sorted(_TOP_KEYS)}")
        if raw.get("version") != LABELS_VERSION:
            raise LabelError(f"unsupported labels version {raw.get('version')!r}")
        reviewed = raw.get("reviewed", False)
        if not isinstance(reviewed, bool):
            raise LabelError(f"reviewed must be true or false, got {reviewed!r}")
        for key in ("dump", "dump_sha256"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise LabelError(f"{key} must be a non-empty string")
        return cls(
            dump=raw["dump"],
            dump_sha256=raw["dump_sha256"],
            reviewed=reviewed,
            ball=_parse_points(raw.get("ball"), "ball"),
            club=_parse_points(raw.get("club"), "club"),
            tolerances=_parse_tolerances(raw.get("tolerances")),
            notes=str(raw.get("notes", "")),
        )


def _number(value, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LabelError(f"{name} must be a finite number, got {value!r}")
    if minimum is not None and value < minimum:
        raise LabelError(f"{name} must be at least {minimum}, got {value!r}")
    return float(value)


def _parse_points(raw, obj: str) -> tuple[LabelPoint, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict) or set(raw) - {"points"}:
        raise LabelError(f"{obj} must be an object with a points list")
    points: dict[int, LabelPoint] = {}
    for row in raw.get("points", []):
        if not isinstance(row, dict) or set(row) - _POINT_KEYS:
            raise LabelError(f"{obj}: a point is {{frame, range_bin, doppler_mps?}}, got {row!r}")
        frame = row.get("frame")
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise LabelError(f"{obj}: frame must be a non-negative integer, got {frame!r}")
        if frame in points:
            raise LabelError(f"{obj}: duplicate frame {frame}")
        if "range_bin" not in row:
            raise LabelError(f"{obj} frame {frame}: range_bin is missing")
        doppler = row.get("doppler_mps")
        points[frame] = LabelPoint(
            frame=frame,
            range_bin=_number(row["range_bin"], f"{obj} frame {frame} range_bin", minimum=0.0),
            doppler_mps=None
            if doppler is None
            else _number(doppler, f"{obj} frame {frame} doppler_mps"),
        )
    return tuple(points[f] for f in sorted(points))


def _parse_tolerances(raw) -> Tolerances:
    if raw is None:
        return Tolerances()
    if not isinstance(raw, dict):
        raise LabelError("tolerances must be an object")
    unknown = sorted(set(raw) - _TOLERANCE_KEYS)
    if unknown:
        raise LabelError(f"unknown tolerances {unknown}; known: {sorted(_TOLERANCE_KEYS)}")
    default = Tolerances()
    range_bins = _number(raw.get("range_bins", default.range_bins), "range_bins")
    if range_bins <= 0:
        raise LabelError(f"range_bins must be positive, got {range_bins}")
    coverage = _number(raw.get("min_coverage", default.min_coverage), "min_coverage")
    if not 0.0 <= coverage <= 1.0:
        raise LabelError(f"min_coverage must be within 0..1, got {coverage}")
    doppler = raw.get("doppler_mps", default.doppler_mps)
    if doppler is not None:
        doppler = _number(doppler, "doppler_mps")
        if doppler <= 0:
            raise LabelError(f"doppler_mps must be positive, got {doppler}")
    return Tolerances(range_bins=range_bins, min_coverage=coverage, doppler_mps=doppler)


def dump_sha256(raw: bytes) -> str:
    """The hash that ties a label file to its capture."""
    return hashlib.sha256(raw).hexdigest()


def labels_path_for(dump_path: Path) -> Path:
    """``shot.l3dump`` -> ``shot.l3dump.labels.json``, beside the dump."""
    return dump_path.with_name(dump_path.name + SUFFIX)


def empty_labels(dump_path: Path) -> Labels:
    """No points and not reviewed, hashed against the dump on disk."""
    return Labels(dump=dump_path.name, dump_sha256=dump_sha256(dump_path.read_bytes()))


def load_labels(dump_path: Path) -> Labels | None:
    """The dump's labels, None when it has none; a changed dump is a ``LabelError``."""
    path = labels_path_for(dump_path)
    if not path.exists():
        return None
    try:
        labels = Labels.from_json(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise LabelError(f"{path.name}: not valid JSON ({exc})") from exc
    except LabelError as exc:
        raise LabelError(f"{path.name}: {exc}") from exc
    if labels.dump_sha256 != dump_sha256(dump_path.read_bytes()):
        raise LabelError(
            f"{path.name}: {dump_path.name} changed since it was labelled (hash mismatch); "
            "relabel it or restore the original capture"
        )
    return labels


def save_labels(dump_path: Path, labels: Labels) -> None:
    """Write the sidecar atomically; labels for another version of the dump are refused."""
    if labels.dump_sha256 != dump_sha256(dump_path.read_bytes()):
        raise LabelError(f"labels do not match {dump_path.name} (hash mismatch)")
    path = labels_path_for(dump_path)
    text = json.dumps(labels.to_json(), indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def labels_from_payload(payload: dict, dump_path: Path) -> Labels:
    """Labels from the page: the dump name and hash are the server's, not the client's."""
    if not isinstance(payload, dict):
        raise LabelError("labels must be a JSON object")
    trusted = {**payload, "dump": dump_path.name, "dump_sha256": dump_sha256(dump_path.read_bytes())}
    return Labels.from_json(trusted)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_labels.py -v`
Expected: all PASS. If `test_failed_save_keeps_the_old_file_and_leaves_no_temp` fails on the leftover temp, check the `except BaseException` cleanup.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src/openflight/iwr6843/labels.py tests/test_iwr6843_labels.py; uv run ruff format src/openflight/iwr6843/labels.py tests/test_iwr6843_labels.py; uv run pylint src/openflight/iwr6843/labels.py --fail-under=9`

```bash
git add src/openflight/iwr6843/labels.py tests/test_iwr6843_labels.py
git commit -m "iwr: label sidecar format for hand-marked ball and club tracks" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Scoring labels against a replay (`label_scoring.py`)

**Files:**
- Create: `src/openflight/iwr6843/label_scoring.py`
- Test: `tests/test_iwr6843_label_scoring.py`

**Interfaces:**
- Consumes: `Labels`, `LabelPoint`, `Tolerances`, `OBJECTS`, `load_labels`, `LabelError` from Task 1; `firmware_replay.ReplayResult` (fields `points`, `ball_points`, each a `PointSummary` with `frame`, `range_bin`, `doppler_mps`), `firmware_replay.recording_configs`, `firmware_replay.RECORDINGS_DIR`.
- Produces:
  - `@dataclass(frozen=True) ObjectScore(labelled: int, tracked: int, matched: int, false_points: int, mean_abs_error_bins: float | None, coverage: float, score: float)`
  - `score_object(points: Sequence[LabelPoint], tracked: Sequence[PointSummary], tol: Tolerances) -> ObjectScore`
  - `score_labels(labels: Labels, result: ReplayResult) -> dict[str, ObjectScore]` (keys `"ball"`, `"club"`)
  - `dump_score(scores: Mapping[str, ObjectScore]) -> float` (mean of the two object scores)
  - `check_labels(labels: Labels, scores: Mapping[str, ObjectScore]) -> list[str]` (failure reasons, empty is a pass)
  - `reviewed_recordings(directory: Path = RECORDINGS_DIR) -> list[tuple[Path, ReplayConfig, Labels]]`
  - `BASELINE_NAME = "label_baseline.json"`, `load_baseline(directory) -> dict[str, float]`, `write_baseline(directory, scores: Mapping[str, float]) -> None`
  - Weights `ERROR_WEIGHT = 0.25`, `FALSE_POINT_WEIGHT = 0.5`

Score definition (implement exactly this): `coverage = matched / labelled` (1.0 when `labelled == 0`); `error_term = mean_abs_error_bins / tol.range_bins` (0 when nothing matched); `score = coverage - ERROR_WEIGHT * error_term - FALSE_POINT_WEIGHT * false_points / max(labelled, 1)`. A firmware point is *matched* when a label exists on its frame and `|Δrange_bin| <= tol.range_bins` (and `|Δdoppler| <= tol.doppler_mps` when both the tolerance and the label's doppler are set). Every unmatched firmware point is a *false point*, whether it sits on an unlabelled frame or misses on a labelled one. If the firmware has several points on one frame, only the closest can match; the rest are false.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_iwr6843_label_scoring.py`:

```python
"""Scoring the firmware's tracks against hand labels."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openflight.iwr6843 import label_scoring as ls, labels as lb


def _fw(frame, range_bin, doppler=0.0):
    return SimpleNamespace(frame=frame, range_bin=range_bin, doppler_mps=doppler)


def _lab(frame, range_bin, doppler=None):
    return lb.LabelPoint(frame, range_bin, doppler)


TOL = lb.Tolerances(range_bins=1.0, min_coverage=0.8)


def test_perfect_match_scores_one():
    s = ls.score_object([_lab(1, 10.0), _lab(2, 12.0)], [_fw(1, 10.0), _fw(2, 12.0)], TOL)
    assert (s.matched, s.false_points, s.coverage, s.score) == (2, 0, 1.0, 1.0)
    assert s.mean_abs_error_bins == 0.0


def test_within_tolerance_matches_and_costs_error():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 10.5)], TOL)
    assert s.matched == 1
    assert s.mean_abs_error_bins == pytest.approx(0.5)
    assert s.score == pytest.approx(1.0 - ls.ERROR_WEIGHT * 0.5)


def test_outside_tolerance_is_a_miss_and_a_false_point():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 12.5)], TOL)
    assert (s.matched, s.false_points, s.coverage) == (0, 1, 0.0)
    assert s.mean_abs_error_bins is None
    assert s.score == pytest.approx(0.0 - ls.FALSE_POINT_WEIGHT * 1)


def test_missing_frames_lower_coverage():
    s = ls.score_object([_lab(1, 10.0), _lab(2, 11.0)], [_fw(1, 10.0)], TOL)
    assert s.coverage == 0.5
    assert s.false_points == 0


def test_a_point_on_an_unlabelled_frame_is_false():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 10.0), _fw(7, 40.0)], TOL)
    assert (s.matched, s.false_points) == (1, 1)


def test_only_the_closest_point_on_a_frame_can_match():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 10.8), _fw(1, 10.1)], TOL)
    assert (s.matched, s.false_points) == (1, 1)
    assert s.mean_abs_error_bins == pytest.approx(0.1)


def test_reviewed_object_with_no_labels_is_perfect_when_nothing_is_tracked():
    s = ls.score_object([], [], TOL)
    assert (s.labelled, s.coverage, s.score) == (0, 1.0, 1.0)


def test_reviewed_object_with_no_labels_is_penalised_for_any_point():
    s = ls.score_object([], [_fw(3, 20.0)], TOL)
    assert s.false_points == 1
    assert s.score == pytest.approx(1.0 - ls.FALSE_POINT_WEIGHT)


def test_doppler_tolerance_applies_only_when_label_has_a_doppler():
    tol = lb.Tolerances(range_bins=1.0, min_coverage=0.8, doppler_mps=2.0)
    assert ls.score_object([_lab(1, 10.0, 30.0)], [_fw(1, 10.0, 34.0)], tol).matched == 0
    assert ls.score_object([_lab(1, 10.0, 30.0)], [_fw(1, 10.0, 31.0)], tol).matched == 1
    assert ls.score_object([_lab(1, 10.0)], [_fw(1, 10.0, 99.0)], tol).matched == 1


def _labels(ball=(), club=(), tol=TOL):
    return lb.Labels(dump="d.l3dump", dump_sha256="x", reviewed=True, ball=ball, club=club, tolerances=tol)


def test_score_labels_reads_club_and_ball_points_from_the_result():
    result = SimpleNamespace(points=[_fw(1, 30.0)], ball_points=[_fw(5, 50.0)])
    scores = ls.score_labels(_labels(ball=(_lab(5, 50.0),), club=(_lab(1, 30.0),)), result)
    assert set(scores) == {"ball", "club"}
    assert scores["ball"].matched == 1 and scores["club"].matched == 1
    assert ls.dump_score(scores) == 1.0


def test_check_labels_fails_on_low_coverage():
    result = SimpleNamespace(points=[], ball_points=[_fw(5, 50.0)])
    labels = _labels(ball=(_lab(5, 50.0),), club=(_lab(1, 30.0),))
    failures = ls.check_labels(labels, ls.score_labels(labels, result))
    assert failures == ["club: coverage 0.00 below 0.80"]


def test_check_labels_fails_on_a_point_for_an_object_labelled_empty():
    result = SimpleNamespace(points=[], ball_points=[_fw(5, 50.0)])
    labels = _labels(ball=(), club=())
    failures = ls.check_labels(labels, ls.score_labels(labels, result))
    assert failures == ["ball: 1 firmware point(s) but the object is labelled as not tracked"]


def test_check_labels_passes_when_everything_matches():
    result = SimpleNamespace(points=[_fw(1, 30.0)], ball_points=[])
    labels = _labels(club=(_lab(1, 30.0),))
    assert ls.check_labels(labels, ls.score_labels(labels, result)) == []


def test_baseline_round_trip_and_missing_file(tmp_path):
    assert ls.load_baseline(tmp_path) == {}
    ls.write_baseline(tmp_path, {"b.l3dump": 0.5, "a.l3dump": 0.9})
    assert ls.load_baseline(tmp_path) == {"a.l3dump": 0.9, "b.l3dump": 0.5}
    text = (tmp_path / ls.BASELINE_NAME).read_text(encoding="utf-8")
    assert list(json.loads(text)) == ["a.l3dump", "b.l3dump"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_label_scoring.py -v`
Expected: collection error, `cannot import name 'label_scoring'`.

- [ ] **Step 3: Write the implementation**

Create `src/openflight/iwr6843/label_scoring.py`:

```python
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


def score_object(
    points: Sequence[LabelPoint], tracked: Sequence, tol: Tolerances
) -> ObjectScore:
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
        coverage
        - ERROR_WEIGHT * error_term
        - FALSE_POINT_WEIGHT * false_points / max(labelled, 1)
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
                failures.append(
                    f"{obj}: {s.tracked} firmware point(s) but the object is labelled as not tracked"
                )
        elif s.coverage < labels.tolerances.min_coverage:
            failures.append(
                f"{obj}: coverage {s.coverage:.2f} below {labels.tolerances.min_coverage:.2f}"
            )
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
    body = {name: round(scores[name], 6) for name in sorted(scores)}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_label_scoring.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff format src/openflight/iwr6843/label_scoring.py tests/test_iwr6843_label_scoring.py; uv run ruff check src/openflight/iwr6843/label_scoring.py; uv run pylint src/openflight/iwr6843/label_scoring.py --fail-under=9`

```bash
git add src/openflight/iwr6843/label_scoring.py tests/test_iwr6843_label_scoring.py
git commit -m "iwr: score firmware tracks against hand labels" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Viewer label endpoints

**Files:**
- Modify: `scripts/iwr6843/dump_viewer.py` (imports at l.26; add routes after `context` at l.68-70)
- Modify: `src/openflight/iwr6843/labels.py` (add the frame-range check to `labels_from_payload`)
- Test: `tests/test_iwr6843_dump_viewer.py` (append to the server section after `test_server_refuses_paths_outside_the_folder_or_not_captures`, l.510)

**Interfaces:**
- Consumes: `load_labels`, `empty_labels`, `save_labels`, `labels_from_payload`, `LabelError`, `Labels.to_json` from Task 1; the existing `resolve()` helper and `ValueError` handler in `create_app`; the test fixtures `client`, `_variable_dump()` (4 frames).
- Produces: `GET /api/labels?path=<rel>` returns the sidecar JSON, or an empty template (with the dump's hash) when none exists. `PUT /api/labels?path=<rel>` takes the labels JSON, validates it, saves the sidecar and returns the saved JSON. Both use `resolve()`, so traversal and non-captures are 400. `labels_from_payload` additionally refuses any `frame >= n_frames` (via `parse_dump(raw)[0]["n_frames"]`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_iwr6843_dump_viewer.py` (after the traversal test; `client` fixture writes `sub/a.l3dump` with 4 frames):

```python
# --- labels ------------------------------------------------------------------


def _label_payload(**over):
    body = {
        "version": 1,
        "reviewed": True,
        "ball": {"points": [{"frame": 2, "range_bin": 41.5, "doppler_mps": 30.0}]},
        "club": {"points": []},
        "tolerances": {"range_bins": 1.0, "min_coverage": 0.8},
        "notes": "n",
    }
    body.update(over)
    return body


def test_get_labels_without_a_file_is_an_empty_unreviewed_template(client):
    body = client.get("/api/labels", query_string={"path": "sub/a.l3dump"}).get_json()
    assert body["dump"] == "a.l3dump"
    assert body["reviewed"] is False
    assert body["ball"] == {"points": []} and body["club"] == {"points": []}
    assert len(body["dump_sha256"]) == 64


def test_put_labels_saves_a_sidecar_next_to_the_dump_and_get_returns_it(client, tmp_path):
    response = client.put(
        "/api/labels", query_string={"path": "sub/a.l3dump"}, json=_label_payload()
    )
    assert response.status_code == 200, response.get_json()
    assert (tmp_path / "sub" / "a.l3dump.labels.json").is_file()
    body = client.get("/api/labels", query_string={"path": "sub/a.l3dump"}).get_json()
    assert body["ball"]["points"] == [{"frame": 2, "range_bin": 41.5, "doppler_mps": 30.0}]
    assert body["reviewed"] is True
    # The sidecar is not itself listed as a capture.
    assert [f["path"] for f in client.get("/api/files").get_json()["files"]] == ["sub/a.l3dump"]


def test_put_labels_ignores_a_stale_client_hash(client):
    body = _label_payload(dump="x.l3dump", dump_sha256="stale")
    response = client.put("/api/labels", query_string={"path": "sub/a.l3dump"}, json=body)
    assert response.status_code == 200
    assert response.get_json()["dump"] == "a.l3dump"


def test_put_labels_refuses_a_frame_beyond_the_dump(client, tmp_path):
    body = _label_payload(ball={"points": [{"frame": 4, "range_bin": 41.0}]})  # dump has 4 frames: 0..3
    response = client.put("/api/labels", query_string={"path": "sub/a.l3dump"}, json=body)
    assert response.status_code == 400
    assert "frame 4" in response.get_json()["error"]
    assert not (tmp_path / "sub" / "a.l3dump.labels.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        _label_payload(version=9),
        _label_payload(ball={"points": [{"frame": 1, "range_bin": float("nan")}]}),
        _label_payload(bogus=1),
        [],
    ],
)
def test_put_labels_reports_invalid_labels_as_400(client, body):
    response = client.put("/api/labels", query_string={"path": "sub/a.l3dump"}, json=body)
    assert response.status_code == 400


@pytest.mark.parametrize("method", ["get", "put"])
@pytest.mark.parametrize("path", ["notes.txt", "sub/missing.l3dump", "", "/etc/passwd", "../x.l3dump"])
def test_labels_endpoints_refuse_paths_outside_the_folder_or_not_captures(client, method, path):
    kwargs = {"json": _label_payload()} if method == "put" else {}
    response = getattr(client, method)("/api/labels", query_string={"path": path}, **kwargs)
    assert response.status_code == 400


def test_get_labels_reports_a_changed_dump_as_400(client, tmp_path):
    client.put("/api/labels", query_string={"path": "sub/a.l3dump"}, json=_label_payload())
    dump = tmp_path / "sub" / "a.l3dump"
    dump.write_bytes(dump.read_bytes() + b"\0")
    response = client.get("/api/labels", query_string={"path": "sub/a.l3dump"})
    assert response.status_code == 400
    assert "changed since it was labelled" in response.get_json()["error"]
```

Note: `float("nan")` through Flask's `json=` serialises as `NaN`, which the server's JSON parser accepts as a float, and `Labels.from_json` then rejects it. If your Flask version rejects `NaN` at parse time it is still a 400.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py -k labels -v`
Expected: FAIL, 404 for `/api/labels`.

- [ ] **Step 3: Implement**

In `src/openflight/iwr6843/labels.py`, replace the last function so it also checks the frame range:

```python
def labels_from_payload(payload: dict, dump_path: Path) -> Labels:
    """Labels from the page: the dump name and hash are the server's, not the client's.

    A point on a frame the dump does not have is refused here, so a bad click
    can never reach a file the tests trust.
    """
    if not isinstance(payload, dict):
        raise LabelError("labels must be a JSON object")
    raw = dump_path.read_bytes()
    labels = Labels.from_json({**payload, "dump": dump_path.name, "dump_sha256": dump_sha256(raw)})
    n_frames = int(parse_dump(raw)[0]["n_frames"])
    for obj in OBJECTS:
        for point in labels.points(obj):
            if point.frame >= n_frames:
                raise LabelError(
                    f"{obj}: frame {point.frame} is beyond the dump's {n_frames} frames"
                )
    return labels
```

and add `from openflight.iwr6843.dump import parse_dump` to its imports. (`test_payload_from_the_page_gets_the_servers_name_and_hash` in Task 1 uses a dump of junk bytes: change that test to build a real dump with `pack_dump`, or, simpler, monkeypatch `lb.parse_dump` to return `({"n_frames": 10}, None)`. Do the monkeypatch in that test.)

In `scripts/iwr6843/dump_viewer.py` change the import and add routes:

```python
from openflight.iwr6843.dump_viewer import ViewerOptions, analyze_dump, session_context
from openflight.iwr6843.labels import (
    LabelError,
    empty_labels,
    labels_from_payload,
    load_labels,
    save_labels,
)
```

```python
    @app.get("/api/labels")
    def get_labels():
        path = resolve(request.args.get("path", ""))
        return jsonify((load_labels(path) or empty_labels(path)).to_json())

    @app.put("/api/labels")
    def put_labels():
        path = resolve(request.args.get("path", ""))
        body = request.get_json(force=True, silent=False)
        if not isinstance(body, dict):
            raise LabelError("labels must be a JSON object")
        labels = labels_from_payload(body, path)
        save_labels(path, labels)
        return jsonify(labels.to_json())
```

`LabelError` subclasses `ValueError`, so the existing `bad_request` handler returns 400 with `{"error": ...}`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py tests/test_iwr6843_labels.py -v`
Expected: all PASS, including the existing viewer tests.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/openflight/iwr6843/labels.py tests/test_iwr6843_dump_viewer.py scripts/iwr6843/dump_viewer.py
uv run ruff check src/openflight/iwr6843/labels.py
git add src/openflight/iwr6843/labels.py scripts/iwr6843/dump_viewer.py tests/test_iwr6843_dump_viewer.py tests/test_iwr6843_labels.py
git commit -m "iwr: dump viewer serves and saves track labels" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Annotate mode in the viewer page

**Files:**
- Modify: `scripts/iwr6843/dump_viewer.html` (sidebar panel near l.112-115; `renderMap` at l.455-519; new state and functions in the script)
- Test: `tests/test_iwr6843_dump_viewer.py` (static page checks)

**Interfaces:**
- Consumes: `GET`/`PUT /api/labels` from Task 3. Existing page helpers used as-is: `$`, `D` (current analysis), `fw()` (returns the firmware section; `.points`, `.ball_points`, each `{frame, range_bin, doppler_mps, ...}`), `binM(bin)` (bin to metres), `T(frame)` (frame to ms), `frameAt(ms)` (ms to frame), `current` (the selected path, `null` for an upload), `renderMap()`, `setFrame(f)`, `C.club`/`C.ball` colours.
- Produces: element ids `annotate` (toggle), `ann-ball`, `ann-club` (object radios, name `ann-obj`), `ann-seed`, `ann-clear`, `ann-reviewed`, `ann-range-tol`, `ann-min-cov`, `ann-notes`, `ann-save`, `ann-status`. JS state `ann = {on, obj, labels, dirty}`.

Behaviour to implement:
- Toggle **Annotate** on: fetch `/api/labels?path=current`, store in `ann.labels` (`{ball:{points}, club:{points}, ...}` exactly as served), show the panel. Disabled with a hint when `current` is null (an uploaded file has no path to save beside).
- **Click on the range-time map** while on: convert the pixel to data coordinates with the plot's own axes (`xa.p2d(px - margin.l)` gives range in metres, `ya.p2d(py - margin.t)` gives ms), then `range_bin = metres / D.bin_width_m` rounded to 0.1 bin, `frame = frameAt(ms)`. Set or replace the active object's point on that frame (a second click on the same frame moves the point). **Shift-click** removes the active object's point on that frame. Mark dirty. Do not call `setFrame` in this mode's clicks.
- Labels draw on the map as unfilled diamonds in the object's colour with a white outline (`name: "ball label"` / `"club label"`), on top of the firmware tracks.
- **Seed from firmware**: replace the active object's points with the firmware's (`fw().ball_points` for ball, `fw().points` for club), `{frame, range_bin, doppler_mps}` only.
- **Clear object**: remove all of the active object's points.
- **Save** (`PUT`) sends `ann.labels` with `reviewed`, `tolerances` and `notes` from the form; on 400 show the server's `error` in `ann-status`; on success replace `ann.labels` with the response and clear dirty.
- Leaving the capture (or toggling off) with unsaved changes asks via `confirm()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_iwr6843_dump_viewer.py`:

```python
ANNOTATE_IDS = [
    "annotate", "ann-ball", "ann-club", "ann-seed", "ann-clear", "ann-reviewed",
    "ann-range-tol", "ann-min-cov", "ann-notes", "ann-save", "ann-status",
]


def test_page_has_the_annotate_controls(client):
    page = client.get("/").data.decode()
    for name in ANNOTATE_IDS:
        assert f'id="{name}"' in page, name
    assert "/api/labels" in page


def test_annotate_marks_are_not_the_ball_blue_reused_for_something_else(client):
    """The ball keeps its colour: labels use the object colours, no new blue."""
    page = client.get("/").data.decode()
    assert "ball label" in page and "club label" in page
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py -k annotate -v`
Expected: FAIL (`id="annotate"` missing).

- [ ] **Step 3: Implement the page changes**

Read `dump_viewer.html` l.108-190 and l.297-330 first to match its style (the `.row`/`label` markup, `const $ =`, `C` colours, `base`/`PLOT_CFG`). Then:

1. In the sidebar after the Run/Reset row (l.112-115) add a panel:

```html
<div class="opts" id="annPanel">
  <label class="chk"><input type="checkbox" id="annotate"> Annotate tracks</label>
  <div id="annBody" hidden>
    <div class="row">
      <label class="chk"><input type="radio" name="ann-obj" id="ann-ball" checked> ball</label>
      <label class="chk"><input type="radio" name="ann-obj" id="ann-club"> club</label>
    </div>
    <div class="row">
      <button id="ann-seed" title="Replace this object's points with the firmware's">Seed from firmware</button>
      <button id="ann-clear">Clear object</button>
    </div>
    <div class="row hint">click the map: place or move a point on that frame &middot; shift-click: remove it</div>
    <label class="chk"><input type="checkbox" id="ann-reviewed"> reviewed (empty object = must not be tracked)</label>
    <label>range tol (bins) <input type="number" id="ann-range-tol" step="0.25" min="0.25" value="1"></label>
    <label>min coverage <input type="number" id="ann-min-cov" step="0.05" min="0" max="1" value="0.8"></label>
    <label>notes <input type="text" id="ann-notes"></label>
    <div class="row"><button class="primary" id="ann-save">Save labels</button></div>
    <div id="ann-status" class="mono"></div>
  </div>
</div>
```

2. Add the script (inside the existing `<script>`, before `renderMap`):

```js
// ---------- annotate ----------
const ann = { on: false, obj: "ball", labels: null, dirty: false };
const annPoints = () => ann.labels[ann.obj].points;
const annColor = () => (ann.obj === "ball" ? C.ball : C.club);

function annStatus(msg, bad) { const s = $("#ann-status"); s.textContent = msg; s.style.color = bad ? "#ff6b6b" : ""; }

async function annLoad() {
  if (!current) { $("#annotate").checked = false; annStatus("Annotate needs a capture from the list, not an upload", true); return; }
  const r = await fetch("/api/labels?path=" + encodeURIComponent(current));
  const body = await r.json();
  if (!r.ok) { $("#annotate").checked = false; annStatus(body.error, true); return; }
  ann.labels = body; ann.dirty = false; ann.on = true;
  $("#annBody").hidden = false;
  $("#ann-reviewed").checked = body.reviewed;
  $("#ann-range-tol").value = body.tolerances.range_bins;
  $("#ann-min-cov").value = body.tolerances.min_coverage;
  $("#ann-notes").value = body.notes || "";
  annStatus(`${body.ball.points.length} ball, ${body.club.points.length} club points`);
  renderMap();
}

function annOff() {
  if (ann.dirty && !confirm("Discard unsaved label changes?")) { $("#annotate").checked = true; return false; }
  ann.on = false; ann.labels = null; ann.dirty = false; $("#annBody").hidden = true; annStatus(""); renderMap();
  return true;
}

function annSet(frame, bin, remove) {
  const pts = annPoints().filter((p) => p.frame !== frame);
  if (!remove) pts.push({ frame, range_bin: bin });
  pts.sort((a, b) => a.frame - b.frame);
  ann.labels[ann.obj].points = pts; ann.dirty = true;
  annStatus(`${ann.labels.ball.points.length} ball, ${ann.labels.club.points.length} club points (unsaved)`);
  renderMap();
}

function annClick(ev, el) {
  const rect = el.getBoundingClientRect(), L = el._fullLayout;
  const metres = L.xaxis.p2d(ev.event.clientX - rect.left - L.margin.l);
  const ms = L.yaxis.p2d(ev.event.clientY - rect.top - L.margin.t);
  const bin = Math.max(0, Math.round((metres / D.bin_width_m) * 10) / 10);
  annSet(frameAt(ms), bin, ev.event.shiftKey);
}

async function annSave() {
  const L = ann.labels;
  L.reviewed = $("#ann-reviewed").checked;
  L.tolerances = { range_bins: +$("#ann-range-tol").value, min_coverage: +$("#ann-min-cov").value, doppler_mps: null };
  L.notes = $("#ann-notes").value;
  const r = await fetch("/api/labels?path=" + encodeURIComponent(current), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(L) });
  const body = await r.json();
  if (!r.ok) { annStatus(body.error, true); return; }
  ann.labels = body; ann.dirty = false; annStatus("saved"); renderMap();
}

$("#annotate").addEventListener("change", (e) => (e.target.checked ? annLoad() : annOff()));
document.querySelectorAll('input[name="ann-obj"]').forEach((r) => r.addEventListener("change", () => (ann.obj = $("#ann-ball").checked ? "ball" : "club")));
$("#ann-seed").addEventListener("click", () => {
  const F = fw(); if (!F) return;
  const src = ann.obj === "ball" ? F.ball_points : F.points;
  ann.labels[ann.obj].points = src.map((p) => ({ frame: p.frame, range_bin: p.range_bin, doppler_mps: p.doppler_mps }));
  ann.dirty = true; annStatus("seeded from firmware (unsaved)"); renderMap();
});
$("#ann-clear").addEventListener("click", () => { ann.labels[ann.obj].points = []; ann.dirty = true; annStatus("cleared (unsaved)"); renderMap(); });
$("#ann-save").addEventListener("click", annSave);
```

3. In `renderMap` (l.455): before the final `Plotly.react`, append label traces when `ann.on`:

```js
  if (ann.on && ann.labels) {
    [["ball", C.ball], ["club", C.club]].forEach(([obj, color]) => {
      const pts = ann.labels[obj].points;
      if (!pts.length) return;
      traces.push({ type: "scatter", mode: "markers", name: `${obj} label`,
        x: pts.map((p) => binM(p.range_bin)), y: pts.map((p) => T(p.frame)),
        marker: { symbol: "diamond", size: 11, color, line: { color: "#ffffff", width: 1.5 } },
        hovertemplate: `${obj} label %{x:.3f} m @ %{y:.1f} ms<extra></extra>` });
    });
  }
```

and replace the map's click hookup (l.518) with:

```js
  if (!el._click) { el._click = true; el.on("plotly_click", (ev) => (ann.on ? annClick(ev, el) : setFrame(frameAt(ev.points[0].y)))); }
```

4. Where the page switches capture (`selectFile`, l.240) call `if (ann.on && !annOff()) return;` first, and after the new capture loads, if the box is still ticked call `annLoad()`. Add `window.addEventListener("beforeunload", (e) => { if (ann.dirty) { e.preventDefault(); e.returnValue = ""; } });`.

- [ ] **Step 4: Run the tests, then verify in the browser**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py -v`
Expected: all PASS.

Manual verification (the page has no JS test harness): start the viewer against a scratch copy of a committed dump so nothing in `tests/radar/recordings/` is touched:

```bash
mkdir -p "$TEMP/labelling" && cp tests/radar/recordings/iwr6843_20260916_184748_410_001.l3dump "$TEMP/labelling/"
uv run python scripts/iwr6843/dump_viewer.py --dir "$TEMP/labelling" --no-browser
```

Open `http://127.0.0.1:5057/` in the browser pane, then check: tick Annotate; click the map to place a ball point (a diamond appears at the click); click the same frame at a different range (it moves); shift-click (it disappears); Seed from firmware (diamonds appear on the firmware track); tick reviewed and Save (status says saved, `<dump>.labels.json` appears in the scratch folder); reload and re-tick Annotate (points come back); check the browser console for errors. Report what you saw; do not claim the UI works without doing this.

- [ ] **Step 5: Commit**

```bash
git add scripts/iwr6843/dump_viewer.html tests/test_iwr6843_dump_viewer.py
git commit -m "iwr: annotate mode in the dump viewer" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Overridable firmware constants (`tunables.py`, `ReplayConfig.overrides`)

**Files:**
- Create: `src/openflight/iwr6843/tunables.py`
- Modify: `src/openflight/iwr6843/firmware_replay.py` (`ReplayConfig` l.289-343; `replay_dump` l.878-950)
- Test: `tests/test_iwr6843_tunables.py`

**Interfaces:**
- Consumes: the ctypes cfg structs in `firmware_host` (`TrigCfg` l.402, `TrackCfg` l.485, `ImpactFitCfg` l.637, `BallHypsCfg` l.775, `BallTrackCfg` l.829) and the C `*_cfg_defaults` functions used at `firmware_replay.py` l.879, 890, 904, 939.
- Produces:
  - `@dataclass(frozen=True) Tunable(name: str, root: str, path: str, kind: str, low: float, high: float, step: float)` where `root` is one of `"trig"`, `"club"`, `"fit"`, `"ball"`, `path` the dotted field path inside that struct (for example `hyps.gateBins`), `name = f"{root}.{path}"`, `kind` is `"int"` or `"float"`.
  - `TUNABLES: tuple[Tunable, ...]`, `BY_NAME: dict[str, Tunable]`
  - `check_overrides(overrides: Mapping[str, float]) -> None` raises `ValueError` naming unknown constants and out-of-bounds values.
  - `apply_overrides(overrides: Mapping[str, float], root: str, cfg: ctypes.Structure) -> None` sets the fields of that root only (ints rounded).
  - `read_defaults(lib: ctypes.CDLL) -> dict[str, float]`
  - `ReplayConfig.overrides: Mapping[str, float] = field(default_factory=dict)`

Registry (exact contents; bounds are the sweep's search box, generous but sane; C default in brackets from the exploration):

| name | kind | low | high | step |
|------|------|-----|------|------|
| `trig.approachBins` | int | 4 | 24 | 2 |
| `trig.gateBins` | int | 1 | 8 | 1 |
| `trig.minApproachBins` | int | 1 | 8 | 1 |
| `club.gateBins` | float | 1.0 | 6.0 | 0.5 |
| `club.maxMisses` | int | 0 | 5 | 1 |
| `club.minConfidence` | float | 0.0 | 0.8 | 0.1 |
| `club.minAcquireDopplerMps` | float | 0.0 | 4.0 | 0.5 |
| `club.maxSameBinPoints` | int | 1 | 5 | 1 |
| `ball.minDepartureMps` | float | 5.0 | 25.0 | 2.5 |
| `ball.originGateBins` | float | 2.0 | 16.0 | 2.0 |
| `ball.minDepartureBins` | float | 0.5 | 3.0 | 0.5 |
| `ball.launchPoints` | int | 3 | 10 | 1 |
| `ball.core.gateBins` | float | 2.0 | 12.0 | 1.0 |
| `ball.core.maxMisses` | int | 0 | 4 | 1 |
| `ball.hyps.spawnBehindBins` | float | 0.0 | 4.0 | 1.0 |
| `ball.hyps.spawnBeyondBins` | float | 4.0 | 16.0 | 2.0 |
| `ball.hyps.gateBins` | float | 0.5 | 3.0 | 0.5 |
| `ball.hyps.gateMps` | float | 2.0 | 16.0 | 2.0 |
| `ball.hyps.maxMisses` | int | 0 | 4 | 1 |
| `ball.hyps.classifyPoints` | int | 2 | 8 | 1 |
| `ball.hyps.maxResidualBins` | float | 0.25 | 2.5 | 0.25 |
| `ball.hyps.dopplerToleranceMps` | float | 1.0 | 6.0 | 0.5 |
| `ball.hyps.fastSupportFraction` | float | 0.3 | 0.9 | 0.05 |
| `fit.fitPoints` | int | 3 | 8 | 1 |
| `fit.minPoints` | int | 2 | 5 | 1 |
| `fit.ballMinMps` | float | 5.0 | 30.0 | 2.5 |

Not registered on purpose: values `replay_dump` already sets from `ReplayConfig` (`teeBin`, `snr`, `trackFrames`, `binWidthM`, `velocitySpanMps`, `cal`, `bandBins`, ball `snr`) and the `#define`s in `l3_trigger.h`/`l3_club_track.h` (compile-time, not runtime configurable; changing them means editing C).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_iwr6843_tunables.py`:

```python
"""The registry of firmware constants a sweep may override, and how overrides reach the C configs."""

from __future__ import annotations

import ctypes
from dataclasses import replace

import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr, tunables as tn

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)

TEE_BIN = int(1.372 / (6.0 / 128))


def _resolve(cfg, path):
    *parents, leaf = path.split(".")
    for name in parents:
        cfg = getattr(cfg, name)
    return cfg, leaf


def test_names_are_unique_and_match_root_and_path():
    names = [t.name for t in tn.TUNABLES]
    assert len(names) == len(set(names))
    assert all(t.name == f"{t.root}.{t.path}" for t in tn.TUNABLES)
    assert set(tn.BY_NAME) == set(names)


def test_bounds_and_steps_are_sane():
    for t in tn.TUNABLES:
        assert t.low < t.high, t.name
        assert 0 < t.step <= (t.high - t.low), t.name
        assert t.kind in ("int", "float"), t.name


_STRUCT_FOR_ROOT = {
    "trig": fw.TrigCfg,
    "club": fw.TrackCfg,
    "fit": fw.ImpactFitCfg,
    "ball": fw.BallTrackCfg,
}


@pytest.mark.parametrize("tunable", tn.TUNABLES, ids=lambda t: t.name)
def test_every_tunable_is_a_field_of_its_struct(tunable):
    cfg, leaf = _resolve(_STRUCT_FOR_ROOT[tunable.root](), tunable.path)
    assert hasattr(cfg, leaf)


@needs_compiler
def test_firmware_defaults_lie_inside_the_search_bounds():
    defaults = tn.read_defaults(fw.load_library())
    assert set(defaults) == set(tn.BY_NAME)
    for name, value in defaults.items():
        t = tn.BY_NAME[name]
        assert t.low <= value <= t.high, f"{name}: default {value} outside {t.low}..{t.high}"


def test_unknown_and_out_of_bounds_overrides_are_named():
    with pytest.raises(ValueError, match="nope.gateBins"):
        tn.check_overrides({"nope.gateBins": 1})
    with pytest.raises(ValueError, match="club.gateBins"):
        tn.check_overrides({"club.gateBins": 99.0})
    tn.check_overrides({"club.gateBins": 2.5, "ball.hyps.maxMisses": 2})


def test_apply_overrides_touches_only_its_root_and_rounds_ints():
    cfg = fw.BallTrackCfg()
    tn.apply_overrides(
        {"ball.hyps.gateBins": 2.0, "ball.launchPoints": 5.4, "club.gateBins": 9.0}, "ball", cfg
    )
    assert cfg.hyps.gateBins == 2.0
    assert cfg.launchPoints == 5
    club = fw.TrackCfg()
    tn.apply_overrides({"ball.launchPoints": 5}, "club", club)
    assert club.gateBins == 0.0  # untouched (a fresh struct)


def test_replay_config_overrides_default_to_empty_and_are_replaceable():
    config = fr.ReplayConfig(tee_bin=TEE_BIN)
    assert dict(config.overrides) == {}
    assert dict(replace(config, overrides={"club.gateBins": 2.0}).overrides) == {"club.gateBins": 2.0}


def test_replay_refuses_an_unknown_override():
    raw = synth_shot_dump(ball_speed_ms=60.0)
    with pytest.raises(ValueError, match="nope.x"):
        fr.replay_dump(raw, fr.ReplayConfig(tee_bin=TEE_BIN, overrides={"nope.x": 1}))


@needs_compiler
def test_an_override_reaches_the_firmware_and_changes_the_track():
    """A ball tracker that must see 10 launch points cannot confirm a 6-frame flight
    the way the default can; the override, not the config, made the difference."""
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372)
    base = fr.ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN)
    default = fr.replay_dump(raw, base)
    altered = fr.replay_dump(raw, replace(base, overrides={"ball.originGateBins": 2.0}))
    assert default.ball_points, "the synthetic ball is tracked by default"
    assert [(p.frame, p.range_bin) for p in altered.ball_points] != [
        (p.frame, p.range_bin) for p in default.ball_points
    ] or altered.launch != default.launch


@needs_compiler
def test_empty_overrides_reproduce_the_default_replay_exactly():
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372)
    base = fr.ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN)
    a = fr.replay_dump(raw, base)
    b = fr.replay_dump(raw, replace(base, overrides={}))
    assert [(p.frame, p.range_bin) for p in a.ball_points] == [
        (p.frame, p.range_bin) for p in b.ball_points
    ]
```

Notes for the implementer: (a) `fw.load_library()` — confirm the actual name of the function that returns the compiled host library (the existing code calls `_default_library()` inside `firmware_replay`; use whichever public accessor `firmware_host` exposes, or import `firmware_replay._default_library`). (b) The "override reaches the firmware" test must actually differ; if `ball.originGateBins: 2.0` does not change the synthetic ball's track, pick a constant that does (try `ball.core.gateBins: 2.0` or `ball.minDepartureMps: 25.0` with a slower `ball_speed_ms`), and keep the assertion strict (not an `or`). Decide by running it, then tighten. (c) Drop the unused `import ctypes` from the test file if nothing uses it.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_iwr6843_tunables.py -v`
Expected: collection error, `cannot import name 'tunables'`.

- [ ] **Step 3: Implement**

Create `src/openflight/iwr6843/tunables.py`:

```python
"""Firmware config constants a sweep may override, and how they reach the C configs.

Each entry names a field of one of the ``*_cfg_t`` structs the replay builds
from the C ``*_cfg_defaults()``. The defaults are never copied here: they are
read from the firmware (``read_defaults``), so the C stays the one source.
"""

from __future__ import annotations

import ctypes
import math
from collections.abc import Mapping
from dataclasses import dataclass

from openflight.iwr6843 import firmware_host as fw


@dataclass(frozen=True)
class Tunable:
    """One overridable constant and the box a sweep may search."""

    root: str  # "trig", "club", "fit" or "ball": which config it lives in
    path: str  # dotted field path inside that config
    kind: str  # "int" or "float"
    low: float
    high: float
    step: float

    @property
    def name(self) -> str:
        """``root.path``: how overrides and reports name it."""
        return f"{self.root}.{self.path}"


def _t(root: str, path: str, kind: str, low: float, high: float, step: float) -> Tunable:
    return Tunable(root, path, kind, low, high, step)


TUNABLES: tuple[Tunable, ...] = (
    _t("trig", "approachBins", "int", 4, 24, 2),
    _t("trig", "gateBins", "int", 1, 8, 1),
    _t("trig", "minApproachBins", "int", 1, 8, 1),
    _t("club", "gateBins", "float", 1.0, 6.0, 0.5),
    _t("club", "maxMisses", "int", 0, 5, 1),
    _t("club", "minConfidence", "float", 0.0, 0.8, 0.1),
    _t("club", "minAcquireDopplerMps", "float", 0.0, 4.0, 0.5),
    _t("club", "maxSameBinPoints", "int", 1, 5, 1),
    _t("ball", "minDepartureMps", "float", 5.0, 25.0, 2.5),
    _t("ball", "originGateBins", "float", 2.0, 16.0, 2.0),
    _t("ball", "minDepartureBins", "float", 0.5, 3.0, 0.5),
    _t("ball", "launchPoints", "int", 3, 10, 1),
    _t("ball", "core.gateBins", "float", 2.0, 12.0, 1.0),
    _t("ball", "core.maxMisses", "int", 0, 4, 1),
    _t("ball", "hyps.spawnBehindBins", "float", 0.0, 4.0, 1.0),
    _t("ball", "hyps.spawnBeyondBins", "float", 4.0, 16.0, 2.0),
    _t("ball", "hyps.gateBins", "float", 0.5, 3.0, 0.5),
    _t("ball", "hyps.gateMps", "float", 2.0, 16.0, 2.0),
    _t("ball", "hyps.maxMisses", "int", 0, 4, 1),
    _t("ball", "hyps.classifyPoints", "int", 2, 8, 1),
    _t("ball", "hyps.maxResidualBins", "float", 0.25, 2.5, 0.25),
    _t("ball", "hyps.dopplerToleranceMps", "float", 1.0, 6.0, 0.5),
    _t("ball", "hyps.fastSupportFraction", "float", 0.3, 0.9, 0.05),
    _t("fit", "fitPoints", "int", 3, 8, 1),
    _t("fit", "minPoints", "int", 2, 5, 1),
    _t("fit", "ballMinMps", "float", 5.0, 30.0, 2.5),
)
BY_NAME: dict[str, Tunable] = {t.name: t for t in TUNABLES}

_DEFAULTS_FN = {
    "trig": ("l3_trig_cfg_defaults", fw.TrigCfg),
    "club": ("l3_track_cfg_defaults", fw.TrackCfg),
    "fit": ("l3_impact_fit_cfg_defaults", fw.ImpactFitCfg),
    "ball": ("l3_ball_track_cfg_defaults", fw.BallTrackCfg),
}


def check_overrides(overrides: Mapping[str, float]) -> None:
    """Refuse unknown constants and values outside the registry's bounds."""
    unknown = sorted(set(overrides) - set(BY_NAME))
    if unknown:
        raise ValueError(f"unknown constants {unknown}; known: {sorted(BY_NAME)}")
    for name, value in overrides.items():
        t = BY_NAME[name]
        if not (isinstance(value, (int, float)) and math.isfinite(value)):
            raise ValueError(f"{name}: {value!r} is not a finite number")
        if not t.low <= value <= t.high:
            raise ValueError(f"{name}: {value} outside {t.low}..{t.high}")


def _field_owner(cfg: ctypes.Structure, path: str) -> tuple[ctypes.Structure, str]:
    *parents, leaf = path.split(".")
    for name in parents:
        cfg = getattr(cfg, name)
    return cfg, leaf


def apply_overrides(overrides: Mapping[str, float], root: str, cfg: ctypes.Structure) -> None:
    """Write the overrides that belong to ``root`` into ``cfg``; integer fields round."""
    for name, value in overrides.items():
        t = BY_NAME.get(name)
        if t is None or t.root != root:
            continue
        owner, leaf = _field_owner(cfg, t.path)
        setattr(owner, leaf, int(round(value)) if t.kind == "int" else float(value))


def read_defaults(lib: ctypes.CDLL) -> dict[str, float]:
    """Every registered constant's firmware default, read from the C ``*_cfg_defaults``."""
    configs = {}
    for root, (function, struct) in _DEFAULTS_FN.items():
        cfg = struct()
        getattr(lib, function)(ctypes.byref(cfg))
        configs[root] = cfg
    out = {}
    for t in TUNABLES:
        owner, leaf = _field_owner(configs[t.root], t.path)
        out[t.name] = float(getattr(owner, leaf))
    return out
```

In `src/openflight/iwr6843/firmware_replay.py`:
- add `from openflight.iwr6843 import tunables` (place with the other package imports) and `from collections.abc import Mapping` if not already imported;
- add to `ReplayConfig` after `ball_snr` (l.343): `overrides: Mapping[str, float] = field(default_factory=dict)` with the comment `# Firmware config constants (tunables.py) set over the defaults; applied last, so they win.`;
- in `replay_dump`: right after the `config.ball_snr` check (l.873) call `tunables.check_overrides(config.overrides)`; then call `tunables.apply_overrides(config.overrides, "trig", trig_cfg)` immediately before `lib.l3_trig_cfg_check` (l.884), `... "club", track_cfg)` immediately before `lib.l3_track_init` (l.896), `... "fit", fit_cfg)` immediately after `fit_cfg.bandBins = ...` (l.906), and `... "ball", ball_cfg)` immediately before `lib.l3_ball_track_init` (l.950, after the `ball_cfg.snr` line so overrides win).
- Confirm `field` is imported from `dataclasses` in that module.

- [ ] **Step 4: Run tests, including the existing replay suites**

Run: `uv run pytest tests/test_iwr6843_tunables.py tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_replay_defaults.py tests/test_iwr6843_dump_viewer.py -v`
Expected: all PASS (empty overrides must not change any existing replay result; `asdict(config)` in the viewer still serialises).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/openflight/iwr6843/tunables.py src/openflight/iwr6843/firmware_replay.py tests/test_iwr6843_tunables.py
uv run ruff check src/openflight/iwr6843/
uv run pylint src/openflight/iwr6843/tunables.py src/openflight/iwr6843/firmware_replay.py --fail-under=9
git add src/openflight/iwr6843/tunables.py src/openflight/iwr6843/firmware_replay.py tests/test_iwr6843_tunables.py
git commit -m "iwr: override firmware config constants from a replay" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Labelled-replay test and baseline ratchet

**Files:**
- Create: `tests/test_iwr6843_labelled_replay.py`
- Modify: `tests/test_iwr6843_label_scoring.py` (append the synthetic end-to-end tests)
- Modify: `tests/radar/recordings/README.md` (short section, see Task 8)

**Interfaces:**
- Consumes: `reviewed_recordings`, `score_labels`, `dump_score`, `check_labels`, `load_baseline`, `write_baseline` from Task 2; `fr.replay_dump`, `fr.ReplayConfig`, `fr.RECORDINGS_DIR`; `synth_shot_dump`; `Labels`, `LabelPoint`, `save_labels`, `empty_labels`, `LabelError` from Task 1.
- Produces: `tests/test_iwr6843_labelled_replay.py::test_firmware_tracks_match_the_labels` parametrized over every reviewed recording (skipped while there are none); the baseline mechanism proven on a synthetic dump.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_iwr6843_label_scoring.py`:

```python
# --- end to end on a synthetic recording ---------------------------------------

from iwr6843_synth import synth_shot_dump  # noqa: E402  (grouped with the tests that use it)

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr  # noqa: E402

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)
TEE_BIN = int(1.372 / (6.0 / 128))


def _recordings_dir(tmp_path):
    """A recordings folder with one synthetic dump and a manifest entry for it."""
    (tmp_path / "synth.l3dump").write_bytes(
        synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372)
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps({"default": {"tee_bin": TEE_BIN, "dest_bin": TEE_BIN}}), encoding="utf-8"
    )
    return tmp_path


def _labels_from_result(dump, result, *, shift=0.0, drop_ball_frames=(), reviewed=True):
    base = lb.empty_labels(dump)
    ball = tuple(
        lb.LabelPoint(p.frame, p.range_bin + shift)
        for p in result.ball_points
        if p.frame not in drop_ball_frames
    )
    club = tuple(lb.LabelPoint(p.frame, p.range_bin) for p in result.points)
    return lb.Labels(base.dump, base.dump_sha256, reviewed, ball=ball, club=club)


@needs_compiler
def test_reviewed_recordings_pairs_each_labelled_dump_with_its_manifest_config(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump = directory / "synth.l3dump"
    assert ls.reviewed_recordings(directory) == []  # no labels yet
    result = fr.replay_dump(dump.read_bytes(), fr.recording_configs(directory)[0][1])
    lb.save_labels(dump, _labels_from_result(dump, result, reviewed=False))
    assert ls.reviewed_recordings(directory) == []  # not reviewed
    lb.save_labels(dump, _labels_from_result(dump, result))
    (found,) = ls.reviewed_recordings(directory)
    assert found[0] == dump and found[1].dest_bin == TEE_BIN and found[2].reviewed


@needs_compiler
def test_a_dump_that_changed_after_labelling_fails_loudly(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump = directory / "synth.l3dump"
    result = fr.replay_dump(dump.read_bytes(), fr.recording_configs(directory)[0][1])
    lb.save_labels(dump, _labels_from_result(dump, result))
    dump.write_bytes(dump.read_bytes() + b"\0")
    with pytest.raises(lb.LabelError, match="changed since it was labelled"):
        ls.reviewed_recordings(directory)


@needs_compiler
def test_labels_equal_to_the_firmware_track_score_one_and_pass(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump, config = fr.recording_configs(directory)[0]
    result = fr.replay_dump(dump.read_bytes(), config)
    assert result.ball_points, "the synthetic ball must be tracked for this test to mean anything"
    labels = _labels_from_result(dump, result)
    scores = ls.score_labels(labels, result)
    assert ls.dump_score(scores) == pytest.approx(1.0)
    assert ls.check_labels(labels, scores) == []


@needs_compiler
def test_a_wrong_label_lowers_the_score_and_a_missing_one_fails_coverage(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump, config = fr.recording_configs(directory)[0]
    result = fr.replay_dump(dump.read_bytes(), config)
    frames = [p.frame for p in result.ball_points]
    shifted = _labels_from_result(dump, result, shift=5.0)
    shifted_scores = ls.score_labels(shifted, result)
    assert ls.dump_score(shifted_scores) < 1.0
    assert ls.check_labels(shifted, shifted_scores)  # coverage collapsed
    sparse = _labels_from_result(dump, result, drop_ball_frames=frames[1:])
    assert any("ball: coverage" in f for f in ls.check_labels(sparse, ls.score_labels(sparse, result)))
```

Create `tests/test_iwr6843_labelled_replay.py`:

```python
"""The firmware's tracks against the hand labels committed beside the recordings.

Every ``*.l3dump`` in ``tests/radar/recordings`` that has a reviewed
``<dump>.labels.json`` is replayed with its manifest configuration. The
firmware must cover the labelled frames within the file's tolerances, track
nothing on an object labelled empty, and not score below the committed
baseline. To accept a deliberate change run
``uv run python scripts/analysis/fit_constants.py --update-baseline``.
"""

from __future__ import annotations

import pytest

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr, label_scoring as ls

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)

_RECORDINGS = ls.reviewed_recordings(fr.RECORDINGS_DIR) if fr.RECORDINGS_DIR.exists() else []
_BASELINE = ls.load_baseline(fr.RECORDINGS_DIR) if fr.RECORDINGS_DIR.exists() else {}


@needs_compiler
@pytest.mark.parametrize(("path", "config", "labels"), _RECORDINGS, ids=[r[0].name for r in _RECORDINGS])
def test_firmware_tracks_match_the_labels(path, config, labels):
    result = fr.replay_dump(path.read_bytes(), config)
    scores = ls.score_labels(labels, result)
    assert ls.check_labels(labels, scores) == [], f"{path.name}: {scores}"
    assert path.name in _BASELINE, (
        f"{path.name} has no baseline score; run "
        "`uv run python scripts/analysis/fit_constants.py --update-baseline`"
    )
    score = ls.dump_score(scores)
    assert score >= _BASELINE[path.name] - 1e-9, (
        f"{path.name}: score {score:.4f} fell below the baseline {_BASELINE[path.name]:.4f}; "
        f"{scores}"
    )
```

- [ ] **Step 2: Run to verify the state**

Run: `uv run pytest tests/test_iwr6843_label_scoring.py tests/test_iwr6843_labelled_replay.py -v`
Expected: the new end-to-end tests PASS (they only depend on Tasks 1-2; if one fails, fix the test helper, not the scorer), and `test_firmware_tracks_match_the_labels` reports as skipped because no dump is labelled yet. If the synthetic "ball must be tracked" assertion fails, adjust `synth_shot_dump` arguments in `_recordings_dir` (the existing `test_ball_snr_*` tests use `ball_speed_ms=60.0, tee_range_m=1.372` with `dest_bin=TEE_BIN` and track a ball).

- [ ] **Step 3: Confirm the ratchet actually bites (temporary check, do not commit)**

In a scratch copy of the synthetic recordings folder from `_recordings_dir`, save labels made with `_labels_from_result(...)`, run `fit_constants.py --dir <scratch> --update-baseline`, then edit `label_baseline.json` to `1.5` for that dump and run `uv run pytest tests/test_iwr6843_labelled_replay.py` with `fr.RECORDINGS_DIR` pointed at the scratch folder (temporarily edit the constant, or run the test body in a REPL). Expected: the test fails with "fell below the baseline". Revert every temporary edit. Report what you saw.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/test_iwr6843_label_scoring.py tests/test_iwr6843_labelled_replay.py -v`
Expected: PASS and one skip.

```bash
uv run ruff format tests/test_iwr6843_label_scoring.py tests/test_iwr6843_labelled_replay.py
git add tests/test_iwr6843_label_scoring.py tests/test_iwr6843_labelled_replay.py
git commit -m "iwr: replay tests against hand labels with a score baseline" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Constants sweep (`constants_fit.py`, `fit_constants.py`)

**Files:**
- Create: `src/openflight/iwr6843/constants_fit.py`
- Create: `scripts/analysis/fit_constants.py`
- Test: `tests/test_iwr6843_constants_fit.py`

**Interfaces:**
- Consumes: `Tunable`, `TUNABLES`, `read_defaults` (Task 5); `score_labels`, `dump_score`, `reviewed_recordings`, `write_baseline` (Task 2); `fr.replay_dump`, `fr.ReplayConfig(overrides=...)`.
- Produces:
  - `candidate_values(t: Tunable, current: float) -> list[float]`: `current + k*step` for `k` in `-3..3`, clipped to `[low, high]`, rounded for ints, de-duplicated, ascending, always containing `current`.
  - `@dataclass(frozen=True) SweepRow(name: str, default: float, suggested: float, gain: float, grid: tuple[tuple[float, float], ...])` with `.flat -> int` (grid points scoring within `1e-9` of the best) and `.changed -> bool`.
  - `coordinate_descent(evaluate: Callable[[Mapping[str, float]], float], tunables: Sequence[Tunable], defaults: Mapping[str, float], passes: int = 2) -> list[SweepRow]`. `evaluate` receives the *full* override mapping, and may return `-inf` for a candidate it cannot run. For each tunable in order it scores every candidate with the others at their current best, moves to the best candidate only when it beats the current value's score by more than `1e-9`, and repeats for `passes`. After the last pass, `gain` is the score at the final settings minus the score with that one constant reset to its default.
  - `evaluate_recordings(recordings: Sequence[tuple[bytes, fr.ReplayConfig, Labels]], overrides: Mapping[str, float]) -> float`: mean `dump_score` over dumps; a `ValueError` from the replay (firmware rejects the configuration) returns `-inf`.
  - `format_report(rows, baseline_score: float, final_score: float, n_dumps: int, n_points: int) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_iwr6843_constants_fit.py`:

```python
"""The constants sweep: coordinate descent over a score, exercised with a fake score."""

from __future__ import annotations

import math

import pytest

from openflight.iwr6843 import constants_fit as cf, tunables as tn

T_INT = tn.Tunable("club", "maxMisses", "int", 0, 5, 1)
T_FLOAT = tn.Tunable("club", "gateBins", "float", 1.0, 6.0, 0.5)


def test_candidates_step_around_the_current_value_clipped_and_deduplicated():
    assert cf.candidate_values(T_INT, 2) == [0, 1, 2, 3, 4, 5]
    assert cf.candidate_values(T_INT, 1) == [0, 1, 2, 3, 4]
    assert cf.candidate_values(T_FLOAT, 3.0) == [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
    assert 1.0 in cf.candidate_values(T_FLOAT, 1.0) and min(cf.candidate_values(T_FLOAT, 1.0)) == 1.0


def _peak_at(target):
    """A score with a single peak: club.gateBins at ``target``, the other constants ignored."""

    def evaluate(overrides):
        return -abs(overrides.get("club.gateBins", 3.0) - target)

    return evaluate


def test_descent_walks_to_the_peak_and_reports_the_gain():
    defaults = {"club.gateBins": 3.0, "club.maxMisses": 2}
    rows = cf.coordinate_descent(_peak_at(4.5), [T_FLOAT, T_INT], defaults, passes=2)
    by_name = {r.name: r for r in rows}
    assert by_name["club.gateBins"].suggested == 4.5
    assert by_name["club.gateBins"].changed
    assert by_name["club.gateBins"].gain == pytest.approx(1.5)
    assert not by_name["club.maxMisses"].changed
    assert by_name["club.maxMisses"].gain == 0.0
    # every candidate scored the same for maxMisses: the optimum is completely flat
    assert by_name["club.maxMisses"].flat == len(by_name["club.maxMisses"].grid)


def test_ties_keep_the_default():
    rows = cf.coordinate_descent(lambda _o: 1.0, [T_INT], {"club.maxMisses": 2})
    assert rows[0].suggested == 2 and not rows[0].changed


def test_a_candidate_the_firmware_rejects_is_skipped_not_fatal():
    def evaluate(overrides):
        if overrides.get("club.maxMisses", 2) == 3:
            return -math.inf
        return -abs(overrides.get("club.maxMisses", 2) - 3)

    rows = cf.coordinate_descent(evaluate, [T_INT], {"club.maxMisses": 2})
    assert rows[0].suggested == 4  # 3 is unrunnable, 4 is the next best
    assert dict(rows[0].grid)[3] == -math.inf


def test_evaluations_are_cached_per_override_set():
    calls = []

    def evaluate(overrides):
        calls.append(dict(overrides))
        return 0.0

    cf.coordinate_descent(evaluate, [T_INT], {"club.maxMisses": 2}, passes=2)
    keys = [tuple(sorted(c.items())) for c in calls]
    assert len(keys) == len(set(keys))


def test_report_lists_each_constant_with_default_suggestion_gain_and_flatness():
    rows = cf.coordinate_descent(_peak_at(4.5), [T_FLOAT], {"club.gateBins": 3.0})
    text = cf.format_report(rows, baseline_score=0.5, final_score=0.7, n_dumps=3, n_points=41)
    assert "3 dumps" in text and "41" in text
    assert "club.gateBins" in text and "3.0" in text and "4.5" in text
    assert "0.500" in text and "0.700" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_iwr6843_constants_fit.py -v`
Expected: collection error, `cannot import name 'constants_fit'`.

- [ ] **Step 3: Implement**

Create `src/openflight/iwr6843/constants_fit.py`:

```python
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
            scored = [(v, score({**current, t.name: v})) for v in candidate_values(t, current[t.name])]
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
            f"{row.name:32} {row.default:>9g} {row.suggested:>9g} {row.gain:>+8.3f}  "
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
```

Create `scripts/analysis/fit_constants.py`:

```python
#!/usr/bin/env python3
"""Fit firmware constants against hand-labelled IWR6843 dumps, or refresh the test baseline.

    uv run python scripts/analysis/fit_constants.py [--dir DIR] [--passes N] [--only PREFIX]
    uv run python scripts/analysis/fit_constants.py --update-baseline

The sweep prints a report and changes nothing. ``--update-baseline`` rewrites
``label_baseline.json`` with the scores at the firmware's current defaults:
run it deliberately, after a firmware change you accept.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from openflight.iwr6843 import (
    constants_fit as cf,
    firmware_replay as fr,
    label_scoring as ls,
    tunables as tn,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--dir", type=Path, default=fr.RECORDINGS_DIR, help="recordings folder")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--only", default="", help="only constants whose name starts with this")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()

    reviewed = ls.reviewed_recordings(args.dir)
    if not reviewed:
        raise SystemExit(f"no reviewed labels under {args.dir}: label dumps in the viewer first")

    if args.update_baseline:
        scores = {}
        for path, config, labels in reviewed:
            result = fr.replay_dump(path.read_bytes(), config)
            scores[path.name] = ls.dump_score(ls.score_labels(labels, result))
        ls.write_baseline(args.dir, scores)
        for name, score in scores.items():
            print(f"{name}: {score:.4f}")
        print(f"wrote {args.dir / ls.BASELINE_NAME}")
        return 0

    recordings = [(path.read_bytes(), config, labels) for path, config, labels in reviewed]
    defaults = tn.read_defaults(fr._default_library())  # pylint: disable=protected-access
    tunables = [t for t in tn.TUNABLES if t.name.startswith(args.only)]
    if not tunables:
        raise SystemExit(f"no constant starts with {args.only!r}")

    def evaluate(overrides):
        return cf.evaluate_recordings(recordings, overrides)

    rows = cf.coordinate_descent(evaluate, tunables, defaults, passes=args.passes)
    final = {r.name: r.suggested for r in rows if r.changed}
    print(
        cf.format_report(
            rows,
            baseline_score=evaluate({}),
            final_score=evaluate(final),
            n_dumps=len(recordings),
            n_points=cf.count_points(recordings),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and a smoke test of the script**

Run: `uv run pytest tests/test_iwr6843_constants_fit.py -v`
Expected: all PASS.

Smoke: `uv run python scripts/analysis/fit_constants.py --dir tests/radar/recordings`
Expected with no labelled dumps yet: exits with `no reviewed labels under ...`. Then repeat against a scratch folder that holds the synthetic recording plus labels made from its own replay (the helper in `test_iwr6843_label_scoring.py` shows how): the report should print a table whose baseline and final scores are equal, with `changed` empty, since labels equal to the firmware's own track leave nothing to improve. Report both outputs.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format src/openflight/iwr6843/constants_fit.py scripts/analysis/fit_constants.py tests/test_iwr6843_constants_fit.py
uv run ruff check src/openflight/iwr6843/constants_fit.py
uv run pylint src/openflight/iwr6843/constants_fit.py --fail-under=9
git add src/openflight/iwr6843/constants_fit.py scripts/analysis/fit_constants.py tests/test_iwr6843_constants_fit.py
git commit -m "iwr: offline sweep of firmware constants against hand labels" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Documentation and full verification

**Files:**
- Modify: `tests/radar/recordings/README.md`
- Modify: `docs/reference/cli.md` (the section that documents the dump viewer; search for `dump_viewer`)
- Modify: `docs/changelog.md` (top unreleased entry, matching its existing format)

**Interfaces:**
- Consumes: everything above. Produces: docs only.

- [ ] **Step 1: Write the docs**

In `tests/radar/recordings/README.md` add a "Labelled dumps" section stating, in this order:
1. Label a dump: `uv run python scripts/iwr6843/dump_viewer.py --dir tests/radar/recordings`, tick **Annotate**, pick ball or club, click the range-time map once per frame where the object is visible (shift-click removes; **Seed from firmware** starts from the firmware's own points), tick **reviewed**, **Save labels**.
2. A reviewed object with no points means "the firmware must not track this".
3. The sidecar is `<dump>.l3dump.labels.json`, committed with the dump; it is tied to the dump by SHA-256, so replacing a dump invalidates its labels (the tests fail with "changed since it was labelled").
4. Replay settings for the dump still come from `manifest.json`; add a per-file entry if the default `tee_bin`/`dest_bin` are wrong for it.
5. Accept a deliberate score change with `uv run python scripts/analysis/fit_constants.py --update-baseline` and commit `label_baseline.json`.
6. Fit constants with `uv run python scripts/analysis/fit_constants.py`; it only prints a report. "flat n/n" rows should be left alone.

In `docs/reference/cli.md` add the two commands (`--update-baseline` and the sweep flags `--dir`, `--passes`, `--only`) beside the dump viewer entry. In `docs/changelog.md` add one entry: dump viewer annotate mode and label files, labelled-replay tests with score baseline, `ReplayConfig.overrides` and the constants sweep.

- [ ] **Step 2: Run the full verification**

```bash
uv run pytest tests/ -q
uv run pylint src/openflight/ --fail-under=9
uv run ruff check src/openflight/
uv run ruff format --check src/openflight/
```

Expected: the whole suite passes (the working tree already has unrelated modified tests; if a failure is in a file this plan did not touch, report it rather than fixing it), lint scores 9.0 or higher, ruff clean. Report the actual counts.

- [ ] **Step 3: Commit**

```bash
git add tests/radar/recordings/README.md docs/reference/cli.md docs/changelog.md
git commit -m "docs: labelled dumps, label baseline and the constants sweep" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review (done)

- **Spec coverage:** schema and hash (Task 1); atomic save (Task 1); endpoints with traversal protection (Task 3); annotate UI including seed, reviewed, tolerances, notes, and existing labels drawn (Task 4); scoring and empty-object rule (Task 2); labelled-replay test, baseline ratchet, `--update-baseline`, synthetic dump for CI (Tasks 2, 6, 7); `tunables.py` registry and `ReplayConfig.overrides` with `BallTuning` untouched (Task 5); sweep with report and flatness (Task 7); docs (Task 8). Spec deviation: the UI moves a point by clicking the same frame again instead of dragging it (Plotly's built-in drag would pan the map); noted for the reviewer.
- **Placeholders:** none. Task 5 has explicit implementer notes: the name of the library accessor, choosing an override that visibly changes the synthetic track, and removing one throwaway test that the notes flag.
- **Type consistency:** `Labels`, `LabelPoint`, `Tolerances`, `ObjectScore`, `Tunable`, `SweepRow` and the function names are used identically across tasks. `PointSummary.range_bin` is global and unit-compatible with `LabelPoint.range_bin`.
