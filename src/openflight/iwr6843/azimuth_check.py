"""Bench check of the IWR6843's direction measurement, against known positions.

A reflector placed at taped lateral offsets and heights at the tee's
distance, with an empty-scene capture subtracted, is the one target whose
direction is known: the recorded swings have none (even the ball on its tee
shares its range with the mat, the floor and the golfer). Each capture goes
through the firmware's own angle estimate (l3_angle_estimate) and golf-frame
conversion (l3_frames_observe), so the check measures what the board
computes.

Static captures give the lateral and height error at each position and fit
the azimuth zero offset, a phase the firmware subtracts from TX1's phase
against the vertical pair and which no calibration measured before. Moving
captures (the reflector swept through the tee's range) run the replay's club
track, whose angles need the TDM motion correction a static target does not,
and report where its points land.

The manifest (JSON, paths relative to it)::

    {
      "empty": "empty.l3dump",
      "captures": [
        {"file": "s_m030.l3dump", "kind": "static", "lateral_m": -0.3, "height_m": 0.1},
        {"file": "sweep_0.l3dump", "kind": "moving", "lateral_m": 0.0, "height_m": 0.1}
      ]
    }

``lateral_m`` is right of the target line (the golf frame's y, seen from
behind the radar); ``height_m`` is above the floor; ``forward_m`` (optional)
is along the target line from the radar and narrows the search for the
reflector's range bin.
"""

from __future__ import annotations

import ctypes
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr
from openflight.iwr6843.board_calibration import BoardCalibration
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.tracking import RANGE_SPAN_M, same_tx_loop_period_s

KINDS = ("static", "moving")
# Closer than this the antenna's own leakage outshines any reflector.
MIN_SEARCH_RANGE_M = 0.5
# With forward_m given, the reflector is searched this far either side of
# the range the tape implies.
FORWARD_SEARCH_M = 0.5
# The azimuth offset is fitted over this grid of phases (radians).
OFFSET_GRID_STEP_RAD = math.radians(0.25)
# Fewer static positions, or fewer distinct lateral offsets, fit nothing.
MIN_FIT_CAPTURES = 3
MIN_FIT_LATERALS = 3
# A fitted slope of measured against taped lateral outside this says the
# azimuth's scale or sign is wrong, not just its zero.
SLOPE_RANGE = (0.7, 1.3)
# A moving point counts as on its line within these lateral distances (m).
MOVING_TOLERANCES_M = (0.15, 0.30)


@dataclass(frozen=True)
class Placement:
    """One capture of the reflector at a taped position."""

    file: Path
    kind: str
    lateral_m: float
    height_m: float
    forward_m: float | None = None


@dataclass(frozen=True)
class Manifest:
    """The empty scene and every reflector placement of one bench session."""

    empty: Path
    placements: tuple[Placement, ...]

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        """Read and check a manifest; capture paths resolve against its folder."""
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        base = path.parent
        if not isinstance(raw.get("empty"), str):
            raise ValueError("manifest needs an 'empty' capture: the scene without the reflector")
        captures = raw.get("captures")
        if not isinstance(captures, list) or not captures:
            raise ValueError("manifest needs a non-empty 'captures' list")
        placements = []
        for index, entry in enumerate(captures):
            where = f"captures[{index}]"
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                raise ValueError(f"{where} needs a 'file'")
            kind = entry.get("kind")
            if kind not in KINDS:
                raise ValueError(f"{where}: kind must be one of {KINDS}, got {kind!r}")
            values = {}
            for name in ("lateral_m", "height_m", "forward_m"):
                value = entry.get(name)
                if value is None and name == "forward_m":
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{where}: {name} must be a number, got {value!r}")
                if not math.isfinite(value):
                    raise ValueError(f"{where}: {name} must be finite")
                values[name] = float(value)
            if values["height_m"] < 0.0:
                raise ValueError(f"{where}: height_m is above the floor, got {values['height_m']}")
            if values.get("forward_m") is not None and values["forward_m"] <= 0.0:
                raise ValueError(f"{where}: forward_m must be positive")
            placements.append(Placement(file=base / entry["file"], kind=kind, **values))
        return cls(empty=base / raw["empty"], placements=tuple(placements))


@dataclass(frozen=True)
class StaticChannels:
    """A capture's stationary scene: every (tx, rx) channel averaged over the
    frames and loops, per global range bin from ``first_bin``."""

    first_bin: int
    n_tx: int
    n_rx: int
    values: np.ndarray  # complex [n_tx, n_rx, bins]
    chirp_period_s: float


@dataclass(frozen=True)
class StaticResult:  # pylint: disable=too-many-instance-attributes
    """Where the board put a static reflector, against where it was taped."""

    placement: Placement
    global_bin: int
    range_m: float
    azimuth_deg: float
    elevation_deg: float
    azimuth_coherence: float
    elevation_peak_ratio: float
    position: tuple[float, float, float]  # golf frame, metres from the antenna
    measured_height_m: float  # above the floor
    lateral_error_m: float
    height_error_m: float


@dataclass(frozen=True)
class OffsetFit:
    """The azimuth zero offset that best lines the static positions up."""

    offset_rad: float
    median_abs_error_m: float  # lateral, after the fit
    slope: float  # measured against taped lateral, after the fit
    captures: int

    @property
    def slope_ok(self) -> bool:
        """Only the zero was off: measured lateral tracks taped lateral."""
        return SLOPE_RANGE[0] <= self.slope <= SLOPE_RANGE[1]


@dataclass(frozen=True)
class MovingResult:
    """Where a swept reflector's track points landed against its taped line."""

    placement: Placement
    points: int  # track points with angles
    median_lateral_m: float | None
    median_height_error_m: float | None
    within: dict[float, float] = field(default_factory=dict)  # tolerance -> share of points


def static_channels(meta: dict, cube: np.ndarray) -> StaticChannels:
    """Average every channel over the frames that share the first frame's
    range window, and over their loops: what stands still survives."""
    n_tx = int(meta["n_tx"])
    n_rx = int(cube.shape[2])
    chirps = int(cube.shape[1])
    if n_tx < 1 or chirps % n_tx != 0:
        raise ValueError(f"{chirps} chirps a frame do not split into {n_tx} TX")
    first, count = fr.frame_window(meta, 0)
    frames = [f for f in range(cube.shape[0]) if fr.frame_window(meta, f) == (first, count)]
    loops = chirps // n_tx
    stacked = cube[frames, :, :, :count].reshape(len(frames), loops, n_tx, n_rx, count)
    values = stacked.mean(axis=(0, 1))
    chirp_period_s = same_tx_loop_period_s(n_tx) / n_tx
    return StaticChannels(first, n_tx, n_rx, values, chirp_period_s)


def subtract_background(scene: StaticChannels, empty: StaticChannels) -> StaticChannels:
    """The scene less the empty capture over the bins both cover."""
    if (scene.n_tx, scene.n_rx) != (empty.n_tx, empty.n_rx):
        raise ValueError("the empty capture was recorded with another TX/RX layout")
    lo = max(scene.first_bin, empty.first_bin)
    hi = min(scene.first_bin + scene.values.shape[2], empty.first_bin + empty.values.shape[2])
    if hi <= lo:
        raise ValueError("the empty capture shares no range bins with the scene")
    diff = (
        scene.values[:, :, lo - scene.first_bin : hi - scene.first_bin]
        - empty.values[:, :, lo - empty.first_bin : hi - empty.first_bin]
    )
    return replace(scene, first_bin=lo, values=diff)


def find_reflector_bin(
    channels: StaticChannels, bin_width_m: float, expected_m: float | None = None
) -> int:
    """The global bin with the most background-subtracted power, past the
    antenna's leakage and, with an expected range, within FORWARD_SEARCH_M of it."""
    power = np.sum(np.abs(channels.values) ** 2, axis=(0, 1))
    bins = channels.first_bin + np.arange(power.size)
    allowed = bins * bin_width_m >= MIN_SEARCH_RANGE_M
    if expected_m is not None:
        allowed &= np.abs(bins * bin_width_m - expected_m) <= FORWARD_SEARCH_M
    if not allowed.any():
        raise ValueError("no range bin left to search for the reflector")
    masked = np.where(allowed, power, -np.inf)
    return int(bins[int(np.argmax(masked))])


def _snapshot(lib, channels: StaticChannels, global_bin: int) -> fw.AngleSnapshot:
    snap = fw.AngleSnapshot()
    lib.l3_angle_snapshot_init(ctypes.byref(snap), channels.n_tx, channels.n_rx)
    local = global_bin - channels.first_bin
    for tx in range(snap.ntx):
        for rx in range(snap.nrx):
            value = channels.values[tx, rx, local]
            snap.channel[tx * snap.nrx + rx] = fw.Cpx(float(value.real), float(value.imag))
    snap.lag1PhaseRad = 0.0
    snap.radialVelocityMps = 0.0
    snap.chirpPeriodS = channels.chirp_period_s
    return snap


def _replay_config(board: BoardCalibration, tee_bin: int, offset_rad: float) -> fr.ReplayConfig:
    overrides = board.replay_overrides()
    overrides["azimuth_offset_rad"] = offset_rad
    return fr.ReplayConfig(tee_bin=tee_bin, **overrides)


def expected_range_m(placement: Placement, radar_height_m: float) -> float | None:
    """The slant range the tape implies, when the forward distance was taped."""
    if placement.forward_m is None:
        return None
    return math.sqrt(
        placement.forward_m**2 + placement.lateral_m**2 + (placement.height_m - radar_height_m) ** 2
    )


def measure_static(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    lib,
    board: BoardCalibration,
    placement: Placement,
    channels: StaticChannels,
    offset_rad: float,
    bin_width_m: float,
) -> StaticResult:
    """One static position through l3_angle_estimate and l3_frames_observe."""
    global_bin = find_reflector_bin(
        channels, bin_width_m, expected_range_m(placement, board.radar_height_m)
    )
    cal = fr._radar_cal(lib, _replay_config(board, global_bin, offset_rad))  # pylint: disable=protected-access
    obs = fw.AngleObs()
    if not lib.l3_angle_estimate(
        ctypes.byref(cal), ctypes.byref(_snapshot(lib, channels, global_bin)), ctypes.byref(obs)
    ):
        raise ValueError(f"{placement.file.name}: the angle estimate refused the snapshot")
    if not obs.azimuthValid:
        raise ValueError(f"{placement.file.name}: no azimuth (a two-TX capture?)")
    range_m = global_bin * bin_width_m
    golf = fw.Vec3()
    lib.l3_frames_observe(
        ctypes.byref(cal), range_m, obs.azimuthRad, obs.elevationRad, ctypes.byref(golf)
    )
    height_above_radar = placement.height_m - board.radar_height_m
    return StaticResult(
        placement=placement,
        global_bin=global_bin,
        range_m=range_m,
        azimuth_deg=math.degrees(obs.azimuthRad),
        elevation_deg=math.degrees(obs.elevationRad),
        azimuth_coherence=float(obs.azimuthCoherence),
        elevation_peak_ratio=float(obs.elevationPeakRatio),
        position=(float(golf.x), float(golf.y), float(golf.z)),
        measured_height_m=float(golf.z) + board.radar_height_m,
        lateral_error_m=float(golf.y) - placement.lateral_m,
        height_error_m=float(golf.z) - height_above_radar,
    )


def fit_azimuth_offset(
    lib,
    board: BoardCalibration,
    statics: list[tuple[Placement, StaticChannels]],
    bin_width_m: float,
) -> OffsetFit | None:
    """The phase offset (radians, within +/-pi) whose static positions land
    nearest their taped lateral offsets (least total absolute error); None
    with too few positions to tell an offset from a scale."""
    laterals = {round(p.lateral_m, 3) for p, _ in statics}
    if len(statics) < MIN_FIT_CAPTURES or len(laterals) < MIN_FIT_LATERALS:
        return None
    grid = np.arange(-math.pi, math.pi, OFFSET_GRID_STEP_RAD)
    best_offset = 0.0
    best_cost = math.inf
    for offset in grid:
        cost = sum(
            abs(measure_static(lib, board, p, c, float(offset), bin_width_m).lateral_error_m)
            for p, c in statics
        )
        if cost < best_cost:
            best_cost, best_offset = cost, float(offset)
    results = [measure_static(lib, board, p, c, best_offset, bin_width_m) for p, c in statics]
    taped = np.array([r.placement.lateral_m for r in results])
    measured = np.array([r.position[1] for r in results])
    slope = float(np.polyfit(taped, measured, 1)[0])
    return OffsetFit(
        offset_rad=best_offset,
        median_abs_error_m=float(np.median(np.abs(measured - taped))),
        slope=slope,
        captures=len(results),
    )


def summarise_moving(
    placement: Placement, points: list[fr.PointSummary], radar_height_m: float
) -> MovingResult:
    """Where a swept reflector's track points landed against its taped line."""
    located = [p.position for p in points if p.angles_valid and p.position is not None]
    if not located:
        return MovingResult(placement, 0, None, None, {})
    lateral = np.array([y for _x, y, _z in located])
    height = np.array([z for _x, _y, z in located])
    off_line = np.abs(lateral - placement.lateral_m)
    return MovingResult(
        placement=placement,
        points=len(located),
        median_lateral_m=float(np.median(lateral)),
        median_height_error_m=float(np.median(height - (placement.height_m - radar_height_m))),
        within={tol: float(np.mean(off_line <= tol)) for tol in MOVING_TOLERANCES_M},
    )


def measure_moving(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    lib,
    board: BoardCalibration,
    placement: Placement,
    raw: bytes,
    tee_bin: int,
    offset_rad: float,
) -> MovingResult:
    """Run the capture through the replay's club track (the board's path,
    TDM motion correction included) and summarise its points."""
    result = fr.replay_dump(raw, _replay_config(board, tee_bin, offset_rad), lib=lib)
    return summarise_moving(placement, list(result.points), board.radar_height_m)


@dataclass(frozen=True)
class Report:
    """One bench session's results."""

    statics: tuple[StaticResult, ...]
    fit: OffsetFit | None
    applied_offset_rad: float
    moving: tuple[MovingResult, ...]

    def to_dict(self) -> dict:
        """The report as JSON-ready values, for the file beside the manifest."""
        return {
            "applied_azimuth_offset_rad": self.applied_offset_rad,
            "fit": None
            if self.fit is None
            else {
                "azimuth_offset_rad": self.fit.offset_rad,
                "median_abs_lateral_error_m": self.fit.median_abs_error_m,
                "slope": self.fit.slope,
                "slope_ok": self.fit.slope_ok,
                "captures": self.fit.captures,
            },
            "static": [
                {
                    "file": r.placement.file.name,
                    "lateral_m": r.placement.lateral_m,
                    "height_m": r.placement.height_m,
                    "range_m": r.range_m,
                    "azimuth_deg": r.azimuth_deg,
                    "elevation_deg": r.elevation_deg,
                    "azimuth_coherence": r.azimuth_coherence,
                    "elevation_peak_ratio": r.elevation_peak_ratio,
                    "position_m": list(r.position),
                    "measured_height_m": r.measured_height_m,
                    "lateral_error_m": r.lateral_error_m,
                    "height_error_m": r.height_error_m,
                }
                for r in self.statics
            ],
            "moving": [
                {
                    "file": m.placement.file.name,
                    "lateral_m": m.placement.lateral_m,
                    "height_m": m.placement.height_m,
                    "points": m.points,
                    "median_lateral_m": m.median_lateral_m,
                    "median_height_error_m": m.median_height_error_m,
                    "within": {f"{tol:.2f}": share for tol, share in m.within.items()},
                }
                for m in self.moving
            ],
        }


def run_check(
    manifest: Manifest,
    board: BoardCalibration,
    *,
    lib=None,
    fft_size: int = fr.DEFAULT_FFT_SIZE,
) -> Report:
    """Static positions (and the offset fit when they allow one), then the
    moving captures with the fitted offset, else the calibration's."""
    lib = lib or fr._default_library()  # pylint: disable=protected-access
    bin_width_m = RANGE_SPAN_M / fft_size
    empty = static_channels(*parse_dump(manifest.empty.read_bytes()))
    statics = [
        (p, subtract_background(static_channels(*parse_dump(p.file.read_bytes())), empty))
        for p in manifest.placements
        if p.kind == "static"
    ]
    fit = fit_azimuth_offset(lib, board, statics, bin_width_m)
    offset = fit.offset_rad if fit is not None else board.az_offset_rad
    results = tuple(measure_static(lib, board, p, c, offset, bin_width_m) for p, c in statics)
    if results:
        tee_bin = int(round(float(np.median([r.global_bin for r in results]))))
    else:
        tee_bin = None
    moving = []
    for placement in (p for p in manifest.placements if p.kind == "moving"):
        raw = placement.file.read_bytes()
        bin_for = tee_bin
        if bin_for is None:
            expected = expected_range_m(placement, board.radar_height_m)
            if expected is None:
                raise ValueError(
                    f"{placement.file.name}: a moving capture needs static positions or forward_m "
                    "to place the tee"
                )
            bin_for = int(round(expected / bin_width_m))
        moving.append(measure_moving(lib, board, placement, raw, bin_for, offset))
    return Report(results, fit, offset, tuple(moving))


def calibration_with_offset(calibration_json: dict, fit: OffsetFit, manifest_path: Path) -> dict:
    """The calibration JSON with the fitted offset and a note of where it came from."""
    out = dict(calibration_json)
    out["azimuth_offset_rad"] = fit.offset_rad
    out["azimuth_check"] = {
        "manifest": str(manifest_path),
        "captures": fit.captures,
        "median_abs_lateral_error_m": fit.median_abs_error_m,
        "slope": fit.slope,
    }
    return out


def format_report(report: Report) -> str:
    """The report as a terminal table."""
    lines = []
    if report.fit is None:
        lines.append(
            f"No offset fit: need {MIN_FIT_CAPTURES}+ static captures at "
            f"{MIN_FIT_LATERALS}+ lateral offsets. Using {report.applied_offset_rad:+.3f} rad."
        )
    else:
        f = report.fit
        lines.append(
            f"Azimuth offset {f.offset_rad:+.3f} rad over {f.captures} positions: "
            f"median lateral error {f.median_abs_error_m * 100:.1f} cm, slope {f.slope:.2f}"
            + ("" if f.slope_ok else "  <-- scale or sign wrong, not just the zero")
        )
    if report.statics:
        lines += ["", "static          taped lat/h (m)  measured lat/h (m)  error lat/h (cm)  coh"]
        for r in report.statics:
            p = r.placement
            lines.append(
                f"{p.file.name[:15]:15} {p.lateral_m:+.2f} / {p.height_m:.2f}"
                f"     {r.position[1]:+.2f} / {r.measured_height_m:+.2f}"
                f"     {r.lateral_error_m * 100:+6.1f} / {r.height_error_m * 100:+6.1f}"
                f"   {r.azimuth_coherence:.2f}"
            )
    if report.moving:
        lines += ["", "moving          taped lat (m)  points  median lat (m)  within 15/30 cm"]
        for m in report.moving:
            name = f"{m.placement.file.name[:15]:15} {m.placement.lateral_m:+.2f}"
            if m.points == 0:
                lines.append(f"{name}               0  (no track points)")
                continue
            shares = " / ".join(f"{m.within[t] * 100:.0f}%" for t in MOVING_TOLERANCES_M)
            lines.append(
                f"{name}          {m.points:6d}  {m.median_lateral_m:+.2f}            {shares}"
            )
    return "\n".join(lines)
