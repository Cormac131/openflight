"""Golfer-clutter map: a pre-swing background of the detection statistic.

Why the statistic and not the complex samples: every consumer of a frame
(trigger, club track, ball track, angles) reads the burst-MTI residual, the
bin's samples less their mean over the frame's loops. A complex background
``C(r, a)`` that is constant across one frame's loops is removed by that
residual already, so subtracting it changes nothing downstream
(``tests/test_iwr6843_clutter_map.py`` proves it). What survives the MTI and
dominates is the golfer's *moving* return, whose phase is not coherent from
frame to frame; its residual *power* is what persists. So the map holds, per
global range bin (and per elevation cell for :class:`RangeAngleClutterMap`),
the idle-frame residual power, and suppression subtracts ``beta`` of it:

    stat'(r) = max(stat(r) - beta * C(r), 0)

applied as a real per-bin gain ``sqrt(stat' / stat)`` on the frame's samples,
so every downstream stage sees the suppressed power while the Doppler and
angle phases are untouched.

The map learns only while :class:`ClutterPhaseMachine` says so: before a club
approach, never from a frame holding a club, and again after the shot.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

GLOBAL_BINS = 128  # the range FFT size on the shipped profiles
MODES = ("median", "ema")


class ShotPhase(enum.Enum):
    """Where the clutter map is in one shot; it learns only in the first two."""

    IDLE = "idle"
    BACKGROUND_LEARNING = "background_learning"
    CLUB_APPROACH_DETECTED = "club_approach_detected"
    PRE_IMPACT_TRACKING = "pre_impact_tracking"
    IMPACT_WINDOW = "impact_window"
    POST_IMPACT_TRACKING = "post_impact_tracking"
    SHOT_COMPLETE = "shot_complete"


LEARNING_PHASES = (ShotPhase.IDLE, ShotPhase.BACKGROUND_LEARNING)
PRE_IMPACT_PHASES = (
    ShotPhase.IDLE,
    ShotPhase.BACKGROUND_LEARNING,
    ShotPhase.CLUB_APPROACH_DETECTED,
    ShotPhase.PRE_IMPACT_TRACKING,
)


@dataclass(frozen=True)
class ClutterConfig:
    """How the map learns and how hard it subtracts.

    ``beta`` 0 learns but subtracts nothing (the benchmark's reference run).
    ``release_frames``: pre-impact frames with no club after an approach
    froze the map before it learns again (a waggle or a false acquisition,
    not a shot); 0 never releases before impact.
    """

    beta: float = 0.8
    mode: str = "median"
    history: int = 16  # median: frames kept per bin
    alpha: float = 1.0 / 16.0  # ema: weight of the newest frame
    min_updates: int = 3  # a bin subtracts nothing until learned this often
    release_frames: int = 8
    impact_window_frames: int = 3
    approach_points: int = 3  # a plausible approach: this many rising club points
    approach_min_mps: float = 8.0
    approach_max_mps: float = 70.0
    approach_max_gap_frames: int = 2

    def __post_init__(self) -> None:
        if not 0.0 <= self.beta <= 1.5:
            raise ValueError(f"beta must be in [0, 1.5], got {self.beta}")
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.history < 1:
            raise ValueError(f"history must be >= 1, got {self.history}")
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError(f"alpha must be in (0, 1], got {self.alpha}")
        if self.min_updates < 1:
            raise ValueError(f"min_updates must be >= 1, got {self.min_updates}")
        if self.release_frames < 0 or self.impact_window_frames < 0:
            raise ValueError("release_frames and impact_window_frames must be >= 0")
        if self.approach_points < 2 or self.approach_max_gap_frames < 1:
            raise ValueError("an approach needs >= 2 points and a gap of >= 1 frame")
        if not 0.0 < self.approach_min_mps < self.approach_max_mps:
            raise ValueError("approach speeds must satisfy 0 < min < max")


def plausible_club_approach(points: Sequence, next_frame: int, config: ClutterConfig) -> bool:
    """Whether a club track's latest points are a club closing on the tee:
    ``approach_points`` points (``frame``, ``timestamp_us``, ``range_m``
    attributes), each step at most ``approach_max_gap_frames`` frames apart
    and rising at a club-like speed, the newest no older than that gap before
    ``next_frame``. A single return or a standing hand never qualifies, so a
    body return the track grabs does not freeze the map."""
    recent = list(points)[-config.approach_points :]
    if len(recent) < config.approach_points:
        return False
    if next_frame - recent[-1].frame > config.approach_max_gap_frames:
        return False
    for older, newer in zip(recent, recent[1:], strict=False):
        gap = newer.frame - older.frame
        dt_s = (newer.timestamp_us - older.timestamp_us) * 1e-6
        if not 0 < gap <= config.approach_max_gap_frames or dt_s <= 0.0:
            return False
        speed = (newer.range_m - older.range_m) / dt_s
        if not config.approach_min_mps <= speed <= config.approach_max_mps:
            return False
    return True


class ClutterPhaseMachine:
    """The shot's phases as far as the clutter map cares.

    ``step`` is called once per frame with the state *after the previous
    frame*: whether a club track is active, whether impact has been declared,
    and whether the shot is finished. Once a club approach is seen the map is
    frozen until the shot completes (or, before impact, until
    ``release_frames`` frames pass without a club).
    """

    def __init__(self, config: ClutterConfig) -> None:
        self.config = config
        self.phase = ShotPhase.IDLE
        self._since_club = 0
        self._since_impact = 0

    @property
    def learning(self) -> bool:
        """Whether the map may learn from the frame just stepped into."""
        return self.phase in LEARNING_PHASES

    def step(self, *, club_active: bool, impact: bool, shot_done: bool) -> ShotPhase:
        """Advance one frame and return the new phase."""
        phase = self.phase
        if phase is ShotPhase.SHOT_COMPLETE:
            phase = ShotPhase.BACKGROUND_LEARNING
        elif shot_done and phase not in LEARNING_PHASES:
            phase = ShotPhase.SHOT_COMPLETE
        elif impact and phase in PRE_IMPACT_PHASES:
            phase = ShotPhase.IMPACT_WINDOW
            self._since_impact = 0
        elif phase is ShotPhase.IMPACT_WINDOW:
            self._since_impact += 1
            if self._since_impact >= self.config.impact_window_frames:
                phase = ShotPhase.POST_IMPACT_TRACKING
        elif phase in LEARNING_PHASES:
            phase = (
                ShotPhase.CLUB_APPROACH_DETECTED if club_active else ShotPhase.BACKGROUND_LEARNING
            )
            self._since_club = 0
        elif phase in (ShotPhase.CLUB_APPROACH_DETECTED, ShotPhase.PRE_IMPACT_TRACKING):
            if club_active:
                phase = ShotPhase.PRE_IMPACT_TRACKING
                self._since_club = 0
            else:
                self._since_club += 1
                if self.config.release_frames and self._since_club >= self.config.release_frames:
                    phase = ShotPhase.BACKGROUND_LEARNING
        self.phase = phase
        return phase


class BackgroundEstimator:
    """Per-global-bin background of a non-negative power, each bin a cell
    array of ``cell_shape`` (``()`` for a scalar per bin): the median of the
    last ``history`` updates, or an exponential moving average."""

    def __init__(
        self, config: ClutterConfig, cell_shape: tuple[int, ...] = (), n_bins: int = GLOBAL_BINS
    ) -> None:
        self.config = config
        self.cell_shape = tuple(cell_shape)
        self.n_bins = n_bins
        self.updates = np.zeros(n_bins, dtype=int)
        if config.mode == "median":
            self._ring = np.full((config.history, n_bins, *self.cell_shape), np.nan)
            self._slot = np.zeros(n_bins, dtype=int)
        else:
            self._ema = np.zeros((n_bins, *self.cell_shape))

    def _span(self, global_start: int, count: int) -> slice:
        if global_start < 0 or count < 0 or global_start + count > self.n_bins:
            raise ValueError(
                f"bins {global_start}..{global_start + count - 1} outside 0..{self.n_bins - 1}"
            )
        return slice(global_start, global_start + count)

    def update(self, global_start: int, values: np.ndarray) -> None:
        """Learn one frame's values for the bins from ``global_start``."""
        values = np.asarray(values, dtype=float)
        if values.shape[1:] != self.cell_shape:
            raise ValueError(f"cells must be {self.cell_shape}, got {values.shape[1:]}")
        span = self._span(global_start, values.shape[0])
        bins = np.arange(span.start, span.stop)
        if self.config.mode == "median":
            self._ring[self._slot[bins] % self.config.history, bins] = values
            self._slot[bins] += 1
        else:
            fresh = self.updates[bins] == 0
            alpha = self.config.alpha
            blended = (1.0 - alpha) * self._ema[bins] + alpha * values
            self._ema[bins] = np.where(
                fresh.reshape(-1, *([1] * len(self.cell_shape))), values, blended
            )
        self.updates[bins] += 1

    def background(self, global_start: int, count: int) -> np.ndarray:
        """The learned background for ``count`` bins; 0 where a bin has fewer
        than ``min_updates`` updates, so an unlearned bin subtracts nothing."""
        span = self._span(global_start, count)
        if self.config.mode == "median":
            ring = self._ring[:, span]
            empty = np.isnan(ring).all(axis=0)
            values = np.nanmedian(np.where(empty[None], 0.0, ring), axis=0)
        else:
            values = self._ema[span].copy()
        learned = self.updates[span] >= self.config.min_updates
        return np.where(learned.reshape(-1, *([1] * len(self.cell_shape))), values, 0.0)


class ClutterMap:
    """``C(r)``: the idle residual power per global bin, and its suppression."""

    def __init__(self, config: ClutterConfig, n_bins: int = GLOBAL_BINS) -> None:
        self.config = config
        self.estimator = BackgroundEstimator(config, (), n_bins)

    def update(self, global_start: int, stats: np.ndarray) -> None:
        """Learn one frame's statistic for the bins from ``global_start``."""
        self.estimator.update(global_start, stats)

    def background(self, global_start: int, count: int) -> np.ndarray:
        """``C(r)`` for ``count`` bins (0 where unlearned)."""
        return self.estimator.background(global_start, count)

    def suppress(self, global_start: int, stats: np.ndarray) -> np.ndarray:
        """``max(stat - beta C, 0)`` for the bins from ``global_start``."""
        stats = np.asarray(stats, dtype=float)
        background = self.background(global_start, len(stats))
        return np.maximum(stats - self.config.beta * background, 0.0)

    def gains(self, global_start: int, stats: np.ndarray) -> np.ndarray:
        """The per-bin amplitude gain that turns ``stats`` into :meth:`suppress`'s
        values (power scales with the square of amplitude); 1 where a bin has
        no power."""
        stats = np.asarray(stats, dtype=float)
        suppressed = self.suppress(global_start, stats)
        safe = np.where(stats > 0.0, stats, 1.0)
        return np.where(stats > 0.0, np.sqrt(suppressed / safe), 1.0)


class ClutterSuppressor:  # pylint: disable=too-few-public-methods
    """A clutter map run over a capture's frames in order: each frame is
    suppressed in place with the map as it stood, and learned only once the
    next frame's phase step confirms it held no club (the trackers read a
    frame before they decide whether it held one). ``stats`` gives a frame's
    per-bin statistic, ``(cube, frame, count, n_tx) -> [count]``."""

    def __init__(
        self,
        config: ClutterConfig,
        stats: Callable[[np.ndarray, int, int, int], np.ndarray],
        n_bins: int = GLOBAL_BINS,
    ) -> None:
        self.map = ClutterMap(config, n_bins)
        self.machine = ClutterPhaseMachine(config)
        self.stats = stats
        self.pending: tuple[int, np.ndarray] | None = None
        self.phases: list[str] = []
        self.gains: list[tuple[int, tuple[float, ...]]] = []

    def frame(  # pylint: disable=too-many-arguments
        self,
        cube: np.ndarray,
        frame: int,
        window_start: int,
        window_bins: int,
        n_tx: int,
        *,
        club_active: bool,
        impact: bool,
        shot_done: bool,
    ) -> None:
        """Step the phase with the previous frame's outcome, learn the
        previous frame if still learning, then suppress this one in place."""
        self.machine.step(club_active=club_active, impact=impact, shot_done=shot_done)
        if self.pending is not None and self.machine.learning:
            self.map.update(*self.pending)
        self.pending = None
        self.phases.append(self.machine.phase.value)
        if window_bins <= 0:
            self.gains.append((window_start, ()))
            return
        stats = np.asarray(self.stats(cube, frame, window_bins, n_tx), dtype=float)
        gains = self.map.gains(window_start, stats)
        cube[frame, :, :, :window_bins] *= gains[None, None, :]
        self.gains.append((window_start, tuple(float(g) for g in gains)))
        self.pending = (window_start, stats)


class RangeAngleClutterMap:
    """``C(r, theta)``: the idle residual power per global bin and elevation
    cell, from the Bartlett spectrum of each bin's loops. A golfer at 1.9 m
    then marks *that direction* at 1.9 m, not the whole bin, and the ball
    leaving the same bin in another direction is not clutter."""

    def __init__(
        self, config: ClutterConfig, grid_rad: np.ndarray, n_bins: int = GLOBAL_BINS
    ) -> None:
        self.config = config
        self.grid_rad = np.asarray(grid_rad, dtype=float)
        self.estimator = BackgroundEstimator(config, (len(self.grid_rad),), n_bins)
        self.azimuth_sum = np.zeros(n_bins, dtype=complex)

    def update(
        self,
        global_start: int,
        spectra: np.ndarray,
        azimuth_phasors: Sequence[complex] | None = None,
    ) -> None:
        """Learn ``[bins, grid]`` spectra; ``azimuth_phasors`` (one unit-ish
        TX1 phasor per bin, power weighted) accumulate each bin's clutter
        azimuth."""
        self.estimator.update(global_start, spectra)
        if azimuth_phasors is not None:
            span = slice(global_start, global_start + len(azimuth_phasors))
            self.azimuth_sum[span] += np.asarray(azimuth_phasors, dtype=complex)

    def background(self, global_start: int, count: int) -> np.ndarray:
        """``C(r, theta)`` for ``count`` bins (0 where unlearned)."""
        return self.estimator.background(global_start, count)

    def angular_fraction(self, global_bin: int, elevation_rad: float) -> float:
        """How much of the bin's clutter lies at this elevation: the map at the
        nearest cell over the bin's strongest cell; 0 for an unlearned bin."""
        cells = self.background(global_bin, 1)[0]
        peak = float(cells.max())
        if peak <= 0.0:
            return 0.0
        index = int(np.argmin(np.abs(self.grid_rad - elevation_rad)))
        return float(cells[index]) / peak

    def dominant_direction(
        self, global_start: int, count: int
    ) -> tuple[float, float | None] | None:
        """The clutter's (elevation, azimuth) over a span of bins: the strongest
        learned cell, and the power-weighted TX1 azimuth; None when unlearned."""
        cells = self.background(global_start, count)
        if cells.size == 0 or float(cells.max()) <= 0.0:
            return None
        cell = int(np.argmax(cells)) % cells.shape[1]
        summed = complex(self.azimuth_sum[global_start : global_start + count].sum())
        azimuth = None
        if abs(summed) > 0.0:
            azimuth = -math.asin(float(np.clip(np.angle(summed) / math.pi, -1.0, 1.0)))
        return float(self.grid_rad[cell]), azimuth


def power_db(value: float, floor: float = 1e-12) -> float:
    """``10 log10`` with a floor, so an empty bin reads as a finite level."""
    return 10.0 * math.log10(max(float(value), floor))
