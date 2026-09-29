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

from openflight.iwr6843.dump import parse_dump

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
    rows = raw.get("points", [])
    if not isinstance(rows, list):
        raise LabelError(f"{obj}: points must be a list, got {rows!r}")
    points: dict[int, LabelPoint] = {}
    for row in rows:
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
