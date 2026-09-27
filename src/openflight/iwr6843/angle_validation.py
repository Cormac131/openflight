"""Angular validation against known reflector positions (roadmap phases 14 and 15).

Static: a corner reflector is placed at known azimuth and elevation angles
and the firmware's ball detector, which measures a stationary return's
direction (``ball status`` -> ``ballangle``), is read many times per
position. Moving: a reflector swings through the lane and recorded
captures are replayed so every tracked point's angle can be compared with
the truth at that instant. This module holds the arithmetic both share:
per-position bias, standard deviation and repeatability, the error as a
function of speed and of the estimator's own quality numbers, and the A/B
of two processing paths (IQ16 against emulated IQ8) over the same samples.
The scripts under ``scripts/hardware-test`` and ``scripts/analysis`` collect
the samples; nothing here touches hardware.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AngleSample:
    """One measurement of a reflector whose true direction is known."""

    truth_azimuth_deg: float
    truth_elevation_deg: float
    azimuth_deg: float | None
    elevation_deg: float | None
    coherence: float = 0.0  # azimuth coherence 0..1
    peak_ratio: float = 0.0  # elevation beamformer peak over mean
    confidence: float = 0.0
    speed_mps: float = 0.0  # radial speed at the sample; 0 for a static reflector
    label: str = ""  # path or format the sample came from ("iq16", "iq8:edma", ...)

    @property
    def azimuth_error_deg(self) -> float | None:
        """Measured minus true azimuth, or None when withheld."""
        return None if self.azimuth_deg is None else self.azimuth_deg - self.truth_azimuth_deg

    @property
    def elevation_error_deg(self) -> float | None:
        """Measured minus true elevation, or None when withheld."""
        return None if self.elevation_deg is None else self.elevation_deg - self.truth_elevation_deg


@dataclass(frozen=True)
class AngleStats:
    """Bias and spread of one angle over a group of samples."""

    count: int  # samples with a measurement
    missing: int  # samples without one (the estimator withheld it)
    bias_deg: float | None
    std_deg: float | None
    mean_abs_deg: float | None
    max_abs_deg: float | None
    p95_abs_deg: float | None


def _stats(errors: Sequence[float], missing: int) -> AngleStats:
    if not errors:
        return AngleStats(0, missing, None, None, None, None, None)
    n = len(errors)
    mean = sum(errors) / n
    var = sum((e - mean) ** 2 for e in errors) / (n - 1) if n > 1 else 0.0
    absolute = sorted(abs(e) for e in errors)
    p95 = absolute[min(n - 1, int(math.ceil(0.95 * n)) - 1)]
    return AngleStats(
        count=n,
        missing=missing,
        bias_deg=mean,
        std_deg=math.sqrt(var),
        mean_abs_deg=sum(absolute) / n,
        max_abs_deg=absolute[-1],
        p95_abs_deg=p95,
    )


def angle_stats(samples: Iterable[AngleSample], which: str) -> AngleStats:
    """``which`` is "azimuth" or "elevation"."""
    errors: list[float] = []
    missing = 0
    for sample in samples:
        error = sample.azimuth_error_deg if which == "azimuth" else sample.elevation_error_deg
        if error is None:
            missing += 1
        else:
            errors.append(error)
    return _stats(errors, missing)


@dataclass(frozen=True)
class PositionSummary:
    truth_azimuth_deg: float
    truth_elevation_deg: float
    azimuth: AngleStats
    elevation: AngleStats
    mean_coherence: float
    mean_peak_ratio: float
    mean_confidence: float


def summarize_positions(samples: Iterable[AngleSample]) -> list[PositionSummary]:
    """Per truth position (static protocol): bias, spread, quality, in the order first seen."""
    groups: dict[tuple[float, float], list[AngleSample]] = {}
    for sample in samples:
        groups.setdefault((sample.truth_azimuth_deg, sample.truth_elevation_deg), []).append(sample)
    out = []
    for (az, el), group in groups.items():
        n = len(group)
        out.append(
            PositionSummary(
                truth_azimuth_deg=az,
                truth_elevation_deg=el,
                azimuth=angle_stats(group, "azimuth"),
                elevation=angle_stats(group, "elevation"),
                mean_coherence=sum(s.coherence for s in group) / n,
                mean_peak_ratio=sum(s.peak_ratio for s in group) / n,
                mean_confidence=sum(s.confidence for s in group) / n,
            )
        )
    return out


@dataclass(frozen=True)
class Repeatability:
    """How the same position reads across repeated placements (session to session)."""

    azimuth_spread_deg: float | None  # std of the per-repeat means
    elevation_spread_deg: float | None
    repeats: int


def repeatability(runs: Sequence[Sequence[AngleSample]]) -> Repeatability:
    """``runs`` are repeated placements of ONE position; the spread of their means."""
    az_means = []
    el_means = []
    for run in runs:
        az = angle_stats(run, "azimuth")
        el = angle_stats(run, "elevation")
        if az.bias_deg is not None:
            az_means.append(az.bias_deg)
        if el.bias_deg is not None:
            el_means.append(el.bias_deg)

    def spread(values: list[float]) -> float | None:
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))

    return Repeatability(spread(az_means), spread(el_means), len(runs))


@dataclass(frozen=True)
class SpeedBin:
    low_mps: float
    high_mps: float
    azimuth: AngleStats
    elevation: AngleStats
    mean_coherence: float


def error_by_speed(samples: Iterable[AngleSample], edges_mps: Sequence[float]) -> list[SpeedBin]:
    """Moving protocol: the angle error binned by radial speed (edges ascending)."""
    edges = list(edges_mps)
    bins: list[list[AngleSample]] = [[] for _ in range(len(edges) - 1)]
    for sample in samples:
        speed = abs(sample.speed_mps)
        for index in range(len(edges) - 1):
            if edges[index] <= speed < edges[index + 1]:
                bins[index].append(sample)
                break
    out = []
    for index, group in enumerate(bins):
        coherence = sum(s.coherence for s in group) / len(group) if group else 0.0
        out.append(
            SpeedBin(
                edges[index],
                edges[index + 1],
                angle_stats(group, "azimuth"),
                angle_stats(group, "elevation"),
                coherence,
            )
        )
    return out


@dataclass(frozen=True)
class PathComparison:
    """Two processing paths over the same reflector positions or captures."""

    label_a: str
    label_b: str
    azimuth_a: AngleStats
    azimuth_b: AngleStats
    elevation_a: AngleStats
    elevation_b: AngleStats

    @property
    def elevation_std_ratio(self) -> float | None:
        if self.elevation_a.std_deg is None or not self.elevation_b.std_deg:
            return None
        return self.elevation_b.std_deg / self.elevation_a.std_deg


def compare_paths(a: Sequence[AngleSample], b: Sequence[AngleSample]) -> PathComparison:
    label_a = a[0].label if a else "a"
    label_b = b[0].label if b else "b"
    return PathComparison(
        label_a,
        label_b,
        angle_stats(a, "azimuth"),
        angle_stats(b, "azimuth"),
        angle_stats(a, "elevation"),
        angle_stats(b, "elevation"),
    )


def _fmt(value: float | None, decimals: int = 2) -> str:
    return "-" if value is None else f"{value:+.{decimals}f}" if decimals else f"{value:.0f}"


def _fmt_abs(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def format_positions(summaries: Sequence[PositionSummary]) -> str:
    """Fixed-width table: one row per truth position."""
    header = (
        f"{'truth az':>9} {'truth el':>9}  {'n':>3} {'az bias':>8} {'az std':>7} {'az p95':>7}  "
        f"{'el bias':>8} {'el std':>7} {'el p95':>7}  {'coh':>5} {'peak':>5} {'conf':>5}"
    )
    lines = [header]
    for s in summaries:
        az = s.azimuth
        el = s.elevation
        lines.append(
            f"{s.truth_azimuth_deg:>+9.1f} {s.truth_elevation_deg:>+9.1f}  {el.count:>3} "
            f"{_fmt(az.bias_deg):>8} {_fmt_abs(az.std_deg):>7} {_fmt_abs(az.p95_abs_deg):>7}  "
            f"{_fmt(el.bias_deg):>8} {_fmt_abs(el.std_deg):>7} {_fmt_abs(el.p95_abs_deg):>7}  "
            f"{s.mean_coherence:>5.2f} {s.mean_peak_ratio:>5.1f} {s.mean_confidence:>5.2f}"
        )
    return "\n".join(lines)


def format_speed_bins(bins: Sequence[SpeedBin]) -> str:
    """Fixed-width table: one row per speed bin."""
    header = (
        f"{'speed m/s':>12}  {'n':>3} {'az std':>7} {'az |e|':>7}  {'el std':>7} {'el |e|':>7}  "
        f"{'coh':>5}"
    )
    lines = [header]
    for b in bins:
        lines.append(
            f"{b.low_mps:>5.1f}-{b.high_mps:<5.1f}  {b.elevation.count:>3} "
            f"{_fmt_abs(b.azimuth.std_deg):>7} {_fmt_abs(b.azimuth.mean_abs_deg):>7}  "
            f"{_fmt_abs(b.elevation.std_deg):>7} {_fmt_abs(b.elevation.mean_abs_deg):>7}  "
            f"{b.mean_coherence:>5.2f}"
        )
    return "\n".join(lines)


def format_comparison(c: PathComparison) -> str:
    return "\n".join(
        [
            f"{'':10} {c.label_a:>12} {c.label_b:>12}",
            f"{'az bias':10} {_fmt(c.azimuth_a.bias_deg):>12} {_fmt(c.azimuth_b.bias_deg):>12}",
            f"{'az std':10} {_fmt_abs(c.azimuth_a.std_deg):>12} {_fmt_abs(c.azimuth_b.std_deg):>12}",
            f"{'el bias':10} {_fmt(c.elevation_a.bias_deg):>12} {_fmt(c.elevation_b.bias_deg):>12}",
            f"{'el std':10} {_fmt_abs(c.elevation_a.std_deg):>12} {_fmt_abs(c.elevation_b.std_deg):>12}",
            f"{'el missing':10} {c.elevation_a.missing:>12} {c.elevation_b.missing:>12}",
        ]
    )


# --- persistence -------------------------------------------------------------------


@dataclass
class ValidationSet:
    """Samples plus how they were taken, as one JSON file."""

    protocol: str  # "static" or "moving"
    firmware_sha: str | None = None
    notes: str = ""
    samples: list[AngleSample] = field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "protocol": self.protocol,
                    "firmware_sha": self.firmware_sha,
                    "notes": self.notes,
                    "samples": [asdict(s) for s in self.samples],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> ValidationSet:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            protocol=data["protocol"],
            firmware_sha=data.get("firmware_sha"),
            notes=data.get("notes", ""),
            samples=[AngleSample(**s) for s in data.get("samples", [])],
        )


STATIC_AZIMUTHS_DEG = (-20.0, -15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 20.0)
STATIC_ELEVATIONS_DEG = (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0)

__all__ = [
    "STATIC_AZIMUTHS_DEG",
    "STATIC_ELEVATIONS_DEG",
    "AngleSample",
    "AngleStats",
    "PathComparison",
    "PositionSummary",
    "Repeatability",
    "SpeedBin",
    "ValidationSet",
    "angle_stats",
    "compare_paths",
    "error_by_speed",
    "format_comparison",
    "format_positions",
    "format_speed_bins",
    "repeatability",
    "summarize_positions",
]
