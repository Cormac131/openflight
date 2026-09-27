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


__all__ = [
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
