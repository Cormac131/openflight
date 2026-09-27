"""Replay recorded IWR6843 captures through the firmware's own trigger and club track.

A ``.l3dump`` holds the range-FFT ring the firmware froze around a swing:
every pre-trigger frame the self-trigger scored, with the same bins, loops
and ordering. This module reduces each frame to the per-bin observations
``l3_dump.c`` computes on the R4F (``l3_verticalResidual``: burst-MTI
residual energy, strongest loop, loop 0 and the lag-1 loop autocorrelation,
over the vertical TX pair and every RX) and feeds them, frame by frame, to
the compiled C observation layer, self-trigger and club track. What comes
back is what the board would have decided and logged for that capture, so a
change to the C can be judged against every recorded swing before it is
flashed.

Bins are GLOBAL range-FFT bins throughout, as in the firmware. The residual
port accumulates in float64 where the firmware uses float32; the observations
agree to float32 rounding, which no threshold in the detector resolves.
"""

from __future__ import annotations

import ctypes
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.dump import is_range_snapshot, parse_dump
from openflight.iwr6843.tracking import RANGE_SPAN_M, same_tx_loop_period_s

# The firmware's defaults for the CLI-configured trigger parameters
# (monitor.SelfTriggerConfig); the C owns the rest.
DEFAULT_SNR = 6.0
DEFAULT_TRACK_FRAMES = 2
DEFAULT_FFT_SIZE = 128
# The lag-1 Doppler readout aliases at wavelength / (4 T); at 135 us that
# is about +/- 9 m/s, so a clubhead reads as a speed uniformly over the span.
FLOOR_SHIFT = 2  # L3_TRIG_FLOOR_SHIFT

_OBS_DTYPE = np.dtype(
    [("energy", "<f4"), ("peak", "<f4"), ("loop0", "<f4"), ("r1Re", "<f4"), ("r1Im", "<f4")]
)


def vertical_tx_indices(n_tx: int) -> tuple[int, ...]:
    """The transmitters ``l3_verticalResidual`` sums: all of them, less the
    azimuth element (TX1) of a three-TX loop."""
    if n_tx < 1:
        raise ValueError(f"a loop needs at least one transmitter, got {n_tx}")
    return tuple(tx for tx in range(n_tx) if not (n_tx == 3 and tx == 1))


def bin_observations(
    cube: np.ndarray, frame: int, first_local: int, count: int, n_tx: int
) -> ctypes.Array:
    """``l3_verticalResidual`` for ``count`` bins from local bin ``first_local``.

    ``cube`` is a parsed dump, ``[frames, chirps, rx, bins]`` with chirp c
    from transmitter ``c % n_tx`` of loop ``c // n_tx``. Returns a ctypes
    array of ``count`` :class:`BinObs`, ready for ``l3_trig_update`` and
    ``l3_obs_extract``.
    """
    chirps = cube.shape[1]
    if chirps % n_tx:
        raise ValueError(f"{chirps} chirps per frame is not a whole number of {n_tx}-TX loops")
    loops = chirps // n_tx
    if count <= 0 or first_local < 0 or first_local + count > cube.shape[-1]:
        raise ValueError(f"bins {first_local}..{first_local + count - 1} outside the frame")
    data = cube[frame, :, :, first_local : first_local + count]
    data = data.reshape(loops, n_tx, cube.shape[2], count)[:, list(vertical_tx_indices(n_tx))]
    residual = data - data.mean(axis=0, keepdims=True)
    power = residual.real**2 + residual.imag**2
    loop_power = power.sum(axis=(1, 2))  # [loops, bins]
    lag1 = (residual[1:] * np.conj(residual[:-1])).sum(axis=(0, 1, 2)) if loops > 1 else 0.0
    table = np.zeros(count, dtype=_OBS_DTYPE)
    table["energy"] = loop_power.sum(axis=0)
    table["peak"] = loop_power.max(axis=0)
    table["loop0"] = loop_power[0]
    table["r1Re"] = np.real(lag1)
    table["r1Im"] = np.imag(lag1)
    return (fw.BinObs * count).from_buffer_copy(table.tobytes())


def frame_window(meta: dict, frame: int) -> tuple[int, int]:
    """(global bin of local bin 0, valid bins) of one frame of a parsed dump."""
    starts = meta.get("range_bin_starts")
    counts = meta.get("range_bin_counts")
    start = starts[frame] if starts else meta.get("range_bin_start", 0)
    count = counts[frame] if counts else meta["n_samples"]
    return int(start), int(count)


def frame_timestamps_us(meta: dict) -> tuple[int, ...]:
    """Microseconds of each frame from the oldest retained one, as the firmware counts them."""
    offsets = meta.get("frame_time_offsets_us")
    if offsets:
        return tuple(int(offset) for offset in offsets)
    period = int(meta.get("frame_period_us", 0))
    return tuple(frame * period for frame in range(meta["n_frames"]))


@dataclass(frozen=True)
class ReplayConfig:
    """What ``triggerCfg`` and the capture profile would have told the board."""

    tee_bin: int  # global; the destination without a locked ball
    snr: float = DEFAULT_SNR
    track_frames: int = DEFAULT_TRACK_FRAMES
    stat: str = "peak"  # "peak" or "energy"
    dest_bin: int | None = None  # a locked ball's global bin; None uses the tee
    loop_period_s: float | None = None  # None: n_tx x the shipped chirp period
    fft_size: int = DEFAULT_FFT_SIZE
    stop_at_fire: bool = False  # True: ignore frames after the trigger fires, as the board does

    @property
    def destination(self) -> int:
        """The global bin the club is judged against: the locked ball, else the tee."""
        return self.tee_bin if self.dest_bin is None else self.dest_bin


@dataclass(frozen=True)
class TargetSummary:
    """One extracted target, copied out of the C structure."""

    range_bin: float
    peak_bin: int
    snr: float
    coherence: float
    doppler_mps: float
    confidence: float


@dataclass(frozen=True)
class ReplayFrame:
    """What one frame did to the trigger and the track."""

    frame: int
    timestamp_us: int
    first_bin: int  # global bin of the first scored observation
    count: int  # observations scored; 0 when the window missed the tee
    floor: float  # the trigger's floor after this frame
    trig_state: str
    fired: bool
    targets: tuple[TargetSummary, ...]
    track_why: str
    track_bin: float | None  # the point appended this frame, global sub-bin


@dataclass(frozen=True)
class PointSummary:
    """One appended trajectory point, copied out of the C structure."""

    frame: int
    timestamp_us: int
    range_bin: float
    range_m: float
    doppler_mps: float
    confidence: float


@dataclass
class ReplayResult:
    """One capture through the firmware modules, with the whole trajectory kept."""

    config: ReplayConfig
    frames: list[ReplayFrame]
    points: list[PointSummary]  # every appended point, beyond the C ring's depth
    fired_frame: int | None
    track_counters: dict[str, int]
    trig_counters: dict[str, int]
    speed_mps: float
    fit_slope_bins_per_s: float
    fit_residual_bins: float
    status: str  # l3_track_format_status at the end of the replay
    trigger_summary: str  # l3_trig_format_summary at the end of the replay
    trig: fw.Trig = field(repr=False)
    track: fw.ClubTrack = field(repr=False)

    @property
    def acquisitions(self) -> int:
        """Times the track started from nothing; one per swing is the goal."""
        return self.track_counters["acquired"]

    @property
    def longest_run(self) -> int:
        """Most consecutive frames that each appended a point: the trajectory's continuity."""
        best = run = 0
        previous = None
        for point in self.points:
            run = run + 1 if previous is not None and point.frame == previous + 1 else 1
            best = max(best, run)
            previous = point.frame
        return best

    @property
    def approach_fraction(self) -> float:
        """Share of consecutive point pairs that moved toward the destination (rising bin)."""
        pairs = list(zip(self.points, self.points[1:], strict=False))
        if not pairs:
            return 0.0
        return sum(1 for a, b in pairs if b.range_bin > a.range_bin) / len(pairs)


_TRIG_COUNTERS = (
    "frames",
    "cand",
    "acq",
    "adv",
    "jump",
    "miss",
    "lost",
    "lowcoh",
    "slowdop",
    "young",
    "slow",
    "fired",
)


def _target_summary(target: fw.TargetObs) -> TargetSummary:
    return TargetSummary(
        range_bin=float(target.rangeBin),
        peak_bin=int(target.peakBin),
        snr=float(target.snr),
        coherence=float(target.coherence),
        doppler_mps=float(target.dopplerAliasMps),
        confidence=float(target.confidence),
    )


def _point_summary(point: fw.TrackPoint) -> PointSummary:
    return PointSummary(
        frame=int(point.frame),
        timestamp_us=int(point.timestampUs),
        range_bin=float(point.rangeBin),
        range_m=float(point.rangeM),
        doppler_mps=float(point.dopplerAliasMps),
        confidence=float(point.confidence),
    )


def replay_dump(
    raw: bytes, config: ReplayConfig, *, lib: ctypes.CDLL | None = None
) -> ReplayResult:
    """Run one capture's frames through the trigger, target extraction and club track.

    Mirrors ``l3_considerSelfTrigger``: per frame the watch region around the
    destination, one observation per bin, ``l3_trig_update`` (which adapts the
    floor), then the same observations as ranked targets into
    ``l3_track_update``. The dump must be a range-FFT snapshot.
    """
    lib = lib or fw.build_firmware_library()
    meta, cube = parse_dump(raw)
    if not is_range_snapshot(meta):
        raise ValueError("replay needs a range-FFT snapshot dump, not raw ADC samples")
    if config.stat not in fw.STAT_NAMES:
        raise ValueError(f"stat must be one of {sorted(fw.STAT_NAMES)}, got {config.stat!r}")
    n_tx = int(meta["n_tx"])
    loop_period_s = config.loop_period_s or same_tx_loop_period_s(n_tx)
    timestamps = frame_timestamps_us(meta)

    trig_cfg = fw.TrigCfg()
    lib.l3_trig_cfg_defaults(ctypes.byref(trig_cfg))
    trig_cfg.teeBin = config.tee_bin
    trig_cfg.snr = config.snr
    trig_cfg.trackFrames = config.track_frames
    trig_cfg.stat = fw.STAT_NAMES[config.stat]
    if lib.l3_trig_cfg_check(ctypes.byref(trig_cfg)) != 0:
        raise ValueError(f"the firmware rejects this trigger configuration: {config}")
    trig = fw.Trig()
    lib.l3_trig_init(ctypes.byref(trig), ctypes.byref(trig_cfg), loop_period_s)

    track_cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(track_cfg))
    track_cfg.binWidthM = RANGE_SPAN_M / config.fft_size
    track_cfg.velocitySpanMps = 2.0 * fw.OBS_WAVELENGTH_M / (4.0 * loop_period_s)
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(track_cfg))

    params = fw.ObsParams(trig_cfg.stat, trig_cfg.snr, loop_period_s)
    targets = (fw.TargetObs * fw.OBS_MAX_TARGETS)()
    first_local = ctypes.c_uint32()
    count = ctypes.c_uint32()
    destination = config.destination

    frames: list[ReplayFrame] = []
    points: list[PointSummary] = []
    fired_frame: int | None = None
    for frame in range(int(meta["n_frames"])):
        if config.stop_at_fire and fired_frame is not None:
            break
        window_start, window_bins = frame_window(meta, frame)
        timestamp_us = timestamps[frame]
        in_window = lib.l3_trig_region(
            ctypes.byref(trig_cfg),
            destination,
            window_start,
            window_bins,
            ctypes.byref(first_local),
            ctypes.byref(count),
        )
        if not in_window:
            frames.append(
                ReplayFrame(
                    frame,
                    timestamp_us,
                    window_start,
                    0,
                    float(trig.floor),
                    fw.TRIG_STATE_NAMES[trig.state],
                    False,
                    (),
                    fw.TRACK_WHY_NAMES[track.why],
                    None,
                )
            )
            continue
        first_bin = window_start + first_local.value
        obs = bin_observations(cube, frame, first_local.value, count.value, n_tx)
        fired = bool(
            lib.l3_trig_update(ctypes.byref(trig), frame, destination, first_bin, obs, count.value)
        )
        found = lib.l3_obs_extract(
            ctypes.byref(params),
            frame,
            timestamp_us,
            first_bin,
            obs,
            count.value,
            trig.floor,
            targets,
            fw.OBS_MAX_TARGETS,
        )
        appended = lib.l3_track_update(ctypes.byref(track), targets, found, frame, timestamp_us)
        track_bin = None
        if appended:
            newest = fw.TrackPoint()
            lib.l3_track_point(ctypes.byref(track), track.count - 1, ctypes.byref(newest))
            points.append(_point_summary(newest))
            track_bin = float(newest.rangeBin)
        if fired:
            fired_frame = frame
        frames.append(
            ReplayFrame(
                frame,
                timestamp_us,
                first_bin,
                count.value,
                float(trig.floor),
                fw.TRIG_STATE_NAMES[trig.state],
                fired,
                tuple(_target_summary(targets[i]) for i in range(found)),
                fw.TRACK_WHY_NAMES[track.why],
                track_bin,
            )
        )

    slope = ctypes.c_float()
    residual = ctypes.c_float()
    used = lib.l3_track_fit(
        ctypes.byref(track), fw.TRACK_POINTS, ctypes.byref(slope), ctypes.byref(residual)
    )
    return ReplayResult(
        config=config,
        frames=frames,
        points=points,
        fired_frame=fired_frame,
        track_counters={name: int(track.counters[i]) for i, name in enumerate(fw.TRACK_WHY_NAMES)},
        trig_counters={name: int(trig.counters[i]) for i, name in enumerate(_TRIG_COUNTERS)},
        speed_mps=float(lib.l3_track_speed_mps(ctypes.byref(track), fw.TRACK_POINTS)),
        fit_slope_bins_per_s=float(slope.value) if used else 0.0,
        fit_residual_bins=float(residual.value) if used else 0.0,
        status=fw.c_text(lib.l3_track_format_status, ctypes.byref(track), destination),
        trigger_summary=fw.c_text(lib.l3_trig_format_summary, ctypes.byref(trig), cap=400),
        trig=trig,
        track=track,
    )


def replay_file(
    path: str | Path, config: ReplayConfig, *, lib: ctypes.CDLL | None = None
) -> ReplayResult:
    """:func:`replay_dump` on a ``.l3dump`` file."""
    return replay_dump(Path(path).read_bytes(), config, lib=lib)


RECORDINGS_DIR = Path(__file__).resolve().parents[3] / "tests" / "radar" / "recordings"
MANIFEST_NAME = "manifest.json"


def recording_configs(
    directory: str | Path = RECORDINGS_DIR, *, default_tee_bin: int | None = None
) -> list[tuple[Path, ReplayConfig]]:
    """Every ``.l3dump`` in a recordings directory with its replay configuration.

    ``manifest.json`` beside the dumps maps each file name to the keyword
    arguments of :class:`ReplayConfig` (``tee_bin`` at least, ``dest_bin``
    when a ball was locked); a ``"default"`` entry supplies the rest. A dump
    without an entry and without a default tee bin is an error, because a
    guessed tee watches the wrong stretch of air.
    """
    directory = Path(directory)
    manifest: dict = {}
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    default = dict(manifest.get("default", {}))
    if default_tee_bin is not None:
        default["tee_bin"] = default_tee_bin
    configs: list[tuple[Path, ReplayConfig]] = []
    for path in sorted(directory.glob("*.l3dump")):
        entry = {**default, **manifest.get(path.name, {})}
        entry.pop("notes", None)
        if "tee_bin" not in entry:
            raise ValueError(f"{path.name}: no tee_bin in {MANIFEST_NAME} and no default given")
        configs.append((path, ReplayConfig(**entry)))
    return configs


def format_report(result: ReplayResult, *, name: str = "", points: bool = False) -> str:
    """A human-readable verdict on one capture: continuity first, then the detail."""
    fired = "no fire" if result.fired_frame is None else f"fired frame {result.fired_frame}"
    lines = [
        f"{name or 'capture'}: {fired}, {len(result.points)} points, "
        f"longest run {result.longest_run}, acquisitions {result.acquisitions}, "
        f"coasted {result.track_counters['coasted']}, dropped {result.track_counters['dropped']}, "
        f"approach {100.0 * result.approach_fraction:.0f}%, "
        f"speed {result.speed_mps:.1f} m/s, fit residual {result.fit_residual_bins:.2f} bins",
        f"  {result.status}",
        f"  {result.trigger_summary}",
    ]
    if points:
        distance = result.config.destination
        for point in result.points:
            lines.append(
                f"  p frame={point.frame} t={point.timestamp_us / 1000.0:.1f}ms "
                f"bin={point.range_bin:.2f} dist={distance - point.range_bin:.1f} "
                f"range={point.range_m:.3f} vd={point.doppler_mps:.2f} "
                f"conf={point.confidence:.2f}"
            )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_FFT_SIZE",
    "MANIFEST_NAME",
    "RECORDINGS_DIR",
    "DEFAULT_SNR",
    "DEFAULT_TRACK_FRAMES",
    "PointSummary",
    "ReplayConfig",
    "ReplayFrame",
    "ReplayResult",
    "TargetSummary",
    "bin_observations",
    "format_report",
    "frame_timestamps_us",
    "frame_window",
    "recording_configs",
    "replay_dump",
    "replay_file",
    "vertical_tx_indices",
]
