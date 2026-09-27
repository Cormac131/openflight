"""Labelled calibration datasets: recorded shots with reference-monitor truth.

Six months from now a directory of ``.l3dump`` files whose provenance is
uncertain is worthless, so every recorded shot carries a sidecar JSON with
what produced it (firmware commit, radar config, radar position), what was
hit (club), what a trusted monitor measured, and the environment. The layout
under ``tests/radar/datasets/`` is one directory per label:

    datasets/
      driver/            shot_001.l3dump  shot_001.json  ...
      7iron/
      wedge/
      straight/  left/  right/  slow/  medium/  fast/

``ShotRecord`` is the sidecar's schema; ``load_dataset`` reads and validates a
directory; ``reference_error`` compares a replayed or solved result against
the reference so validation against the commercial monitor (roadmap step 14)
is a loop over the corpus, not a spreadsheet.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATASETS_DIR = Path(__file__).resolve().parents[3] / "tests" / "radar" / "datasets"
SCHEMA_VERSION = 1
LABELS = ("driver", "7iron", "wedge", "straight", "left", "right", "slow", "medium", "fast")
REFERENCE_FIELDS = (
    "ball_speed_mph",
    "vertical_launch_deg",
    "horizontal_launch_deg",
    "club_speed_mph",
    "club_path_deg",
    "angle_of_attack_deg",
    "spin_rpm",
    "spin_axis_deg",
    "carry_yd",
)


@dataclass(frozen=True)
class RadarPosition:
    """Where the radar sat relative to the ball, metres, golf frame conventions."""

    behind_ball_m: float
    right_of_ball_m: float = 0.0
    height_m: float = 0.15
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    roll_deg: float = 0.0


@dataclass(frozen=True)
class Reference:
    """What the trusted launch monitor reported; None where it did not."""

    monitor: str  # "trackman", "gcquad", ...
    ball_speed_mph: float | None = None
    vertical_launch_deg: float | None = None
    horizontal_launch_deg: float | None = None
    club_speed_mph: float | None = None
    club_path_deg: float | None = None
    angle_of_attack_deg: float | None = None
    spin_rpm: float | None = None
    spin_axis_deg: float | None = None
    carry_yd: float | None = None


@dataclass(frozen=True)
class ShotRecord:
    """The sidecar for one recorded shot."""

    capture: str  # file name of the .l3dump beside the sidecar
    firmware_commit: str
    radar_config: str  # the .cfg file name
    radar_position: RadarPosition
    club: str
    reference: Reference
    labels: tuple[str, ...] = ()
    environment: dict = field(default_factory=dict)  # temperature_c, pressure_hpa, ...
    tee_bin: int | None = None  # global bin the trigger was armed with
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> ShotRecord:
        raw = json.loads(text)
        if raw.get("schema_version", 1) != SCHEMA_VERSION:
            raise ValueError(f"dataset schema {raw.get('schema_version')} is not {SCHEMA_VERSION}")
        for key in (
            "capture",
            "firmware_commit",
            "radar_config",
            "radar_position",
            "club",
            "reference",
        ):
            if key not in raw:
                raise ValueError(f"shot record lacks {key!r}")
        position = RadarPosition(**raw["radar_position"])
        reference = Reference(**raw["reference"])
        for label in raw.get("labels", ()):
            if label not in LABELS:
                raise ValueError(f"unknown label {label!r}; use {LABELS}")
        return cls(
            capture=raw["capture"],
            firmware_commit=raw["firmware_commit"],
            radar_config=raw["radar_config"],
            radar_position=position,
            club=raw["club"],
            reference=reference,
            labels=tuple(raw.get("labels", ())),
            environment=dict(raw.get("environment", {})),
            tee_bin=raw.get("tee_bin"),
            notes=raw.get("notes", ""),
        )


@dataclass(frozen=True)
class DatasetShot:
    record: ShotRecord
    sidecar: Path

    @property
    def capture_path(self) -> Path:
        return self.sidecar.with_name(self.record.capture)


def load_dataset(directory: str | Path = DATASETS_DIR) -> list[DatasetShot]:
    """Every sidecar under ``directory`` (recursively), validated, with its capture present."""
    directory = Path(directory)
    shots: list[DatasetShot] = []
    for sidecar in sorted(directory.rglob("*.json")):
        if sidecar.name == "manifest.json":
            continue
        record = ShotRecord.from_json(sidecar.read_text(encoding="utf-8"))
        shot = DatasetShot(record, sidecar)
        if not shot.capture_path.exists():
            raise FileNotFoundError(f"{sidecar.name} names {record.capture}, which is missing")
        label = sidecar.parent.name
        if directory != sidecar.parent and label in LABELS and label not in record.labels:
            raise ValueError(f"{sidecar.name} sits under {label}/ but is not labelled {label}")
        shots.append(shot)
    return shots


@dataclass(frozen=True)
class ReferenceError:
    """Measured minus reference for each metric both sides have."""

    errors: dict[str, float]

    def summary(self) -> str:
        if not self.errors:
            return "no metric in common with the reference"
        return ", ".join(f"{name} {value:+.2f}" for name, value in sorted(self.errors.items()))


def reference_error(record: ShotRecord, measured: dict[str, float | None]) -> ReferenceError:
    """``measured`` uses the reference field names (mph, degrees, rpm, yards)."""
    errors: dict[str, float] = {}
    for name in REFERENCE_FIELDS:
        truth = getattr(record.reference, name)
        value = measured.get(name)
        if truth is not None and value is not None:
            errors[name] = float(value) - float(truth)
    return ReferenceError(errors)


# The shot matrix a validation session must cover (roadmap phase 28): a
# hundred identical 7-irons exercise nothing. Speed and shape labels are the
# dataset LABELS; the club groups are matched on the record's club name.
CLUB_GROUPS = {
    "wedge": ("wedge", "pitching_wedge", "gap_wedge", "sand_wedge", "lob_wedge"),
    "mid iron": ("7iron", "7_iron", "6_iron", "8_iron"),
    "long iron": ("4_iron", "5_iron", "3_iron", "hybrid"),
    "driver": ("driver", "3_wood", "5_wood"),
}
SPEED_LABELS = ("slow", "medium", "fast")
SHAPE_LABELS = ("straight", "left", "right")


@dataclass(frozen=True)
class Coverage:
    """How many shots each cell of the validation matrix holds."""

    clubs: dict[str, int]
    speeds: dict[str, int]
    shapes: dict[str, int]
    unclassified_clubs: dict[str, int]

    def missing(self, minimum: int = 10) -> list[str]:
        """Cells with fewer than `minimum` shots, as "club: driver (3)"."""
        out = []
        for kind, table, names in (
            ("club", self.clubs, CLUB_GROUPS),
            ("speed", self.speeds, SPEED_LABELS),
            ("shape", self.shapes, SHAPE_LABELS),
        ):
            for name in names:
                count = table.get(name, 0)
                if count < minimum:
                    out.append(f"{kind}: {name} ({count})")
        return out


def coverage(shots: Iterable[DatasetShot]) -> Coverage:
    clubs: dict[str, int] = {}
    speeds: dict[str, int] = {}
    shapes: dict[str, int] = {}
    unclassified: dict[str, int] = {}
    for shot in shots:
        club = shot.record.club.lower().replace(" ", "_")
        group = next((g for g, names in CLUB_GROUPS.items() if club in names), None)
        if group is None:
            unclassified[club] = unclassified.get(club, 0) + 1
        else:
            clubs[group] = clubs.get(group, 0) + 1
        for label in shot.record.labels:
            if label in SPEED_LABELS:
                speeds[label] = speeds.get(label, 0) + 1
            elif label in SHAPE_LABELS:
                shapes[label] = shapes.get(label, 0) + 1
    return Coverage(clubs, speeds, shapes, unclassified)


@dataclass(frozen=True)
class FieldStats:
    """Signed error statistics of one reference field over a set of shots."""

    field: str
    count: int
    bias: float | None
    mae: float | None
    rmse: float | None
    p95_abs: float | None


def field_stats(errors: Iterable[ReferenceError]) -> list[FieldStats]:
    """Per reference field, over every shot whose error carried it."""
    by_field: dict[str, list[float]] = {name: [] for name in REFERENCE_FIELDS}
    for error in errors:
        for name, value in error.errors.items():
            by_field.setdefault(name, []).append(value)
    out = []
    for name in REFERENCE_FIELDS:
        values = by_field.get(name, [])
        if not values:
            out.append(FieldStats(name, 0, None, None, None, None))
            continue
        n = len(values)
        absolute = sorted(abs(v) for v in values)
        out.append(
            FieldStats(
                name,
                n,
                sum(values) / n,
                sum(absolute) / n,
                math.sqrt(sum(v * v for v in values) / n),
                absolute[min(n - 1, int(math.ceil(0.95 * n)) - 1)],
            )
        )
    return out


def validate_dataset(
    shots: Iterable[DatasetShot], measure: Callable[[DatasetShot], dict[str, float | None]]
) -> dict[str, list[FieldStats]]:
    """Error statistics overall and per label: ``measure`` returns the measured
    reference-field values (mph, degrees, rpm, yards) for one shot."""
    errors_all: list[ReferenceError] = []
    by_label: dict[str, list[ReferenceError]] = {}
    for shot in shots:
        error = reference_error(shot.record, measure(shot))
        errors_all.append(error)
        for label in shot.record.labels:
            by_label.setdefault(label, []).append(error)
    out = {"all": field_stats(errors_all)}
    for label in LABELS:
        if label in by_label:
            out[label] = field_stats(by_label[label])
    return out


def measure_with_firmware(shot: DatasetShot, *, lib=None) -> dict[str, float | None]:
    """Replay the shot's capture through the firmware modules and report its
    measurements in the reference fields' units. The tee bin comes from the
    sidecar, else from the radar position."""
    from openflight.iwr6843.firmware_replay import (  # pylint: disable=import-outside-toplevel
        ReplayConfig,
        replay_file,
    )
    from openflight.iwr6843.tracking import RANGE_SPAN_M  # pylint: disable=import-outside-toplevel

    record = shot.record
    tee_bin = record.tee_bin
    if tee_bin is None:
        tee_bin = round(record.radar_position.behind_ball_m / (RANGE_SPAN_M / 128))
    config = ReplayConfig(
        tee_bin=tee_bin,
        dest_bin=tee_bin,
        pitch_deg=record.radar_position.pitch_deg,
        yaw_deg=record.radar_position.yaw_deg,
        roll_deg=record.radar_position.roll_deg,
    )
    result = replay_file(shot.capture_path, config, lib=lib)
    mph = 2.23694
    measured: dict[str, float | None] = {name: None for name in REFERENCE_FIELDS}
    if result.launch is not None:
        measured["ball_speed_mph"] = result.launch.speed_mps * mph
        measured["vertical_launch_deg"] = result.launch.vla_deg
        measured["horizontal_launch_deg"] = result.launch.hla_deg
    if result.delivery is not None and result.delivery.points > 0:
        measured["club_speed_mph"] = result.delivery.speed_mps * mph
        measured["club_path_deg"] = result.delivery.path_deg
        measured["angle_of_attack_deg"] = result.delivery.attack_deg
    return measured


def format_field_stats(stats: Sequence[FieldStats], *, title: str) -> str:
    lines = [f"== {title}", f"{'field':<24} {'n':>4} {'bias':>8} {'mae':>7} {'rmse':>7} {'p95':>7}"]
    for s in stats:
        if s.count == 0:
            continue
        lines.append(
            f"{s.field:<24} {s.count:>4} {s.bias:>+8.2f} {s.mae:>7.2f} {s.rmse:>7.2f} {s.p95_abs:>7.2f}"
        )
    if len(lines) == 2:
        lines.append("(no field in common with the reference)")
    return "\n".join(lines)


__all__ = [
    "CLUB_GROUPS",
    "SHAPE_LABELS",
    "SPEED_LABELS",
    "Coverage",
    "FieldStats",
    "coverage",
    "field_stats",
    "format_field_stats",
    "measure_with_firmware",
    "validate_dataset",
    "DATASETS_DIR",
    "LABELS",
    "REFERENCE_FIELDS",
    "SCHEMA_VERSION",
    "DatasetShot",
    "RadarPosition",
    "Reference",
    "ReferenceError",
    "ShotRecord",
    "load_dataset",
    "reference_error",
]
