"""Host replay of the firmware ball-leave detector.

``l3_considerSelfTrigger`` in ``firmware/iwr6843/l3_dump.c`` watches loop-0
vertical residual power. This steps the same state machine over a saved dump
so a swing can be checked without the board.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openflight.iwr6843.calibration import DEFAULT_TEE_RANGE_M
from openflight.iwr6843.dump import parse_dump, range_data
from openflight.iwr6843.shot import geometry_from_header
from openflight.iwr6843.sparse import vertical_loop_power

# Clubhead is short of the ball. Twelve bins is about 0.6 m on the wide profile.
APPROACH_BINS = 12
# Same names ``l3_triggerPhaseName`` prints on the debug UART.
PHASES = (
    "off",
    "no-frame",
    "bin-outside",
    "tee-low",
    "occupying",
    "watching",
    "no-approach",
    "toward",
    "away",
    "fired",
)
# Production ``triggerCfg`` defaults used with the wide profile.
DEFAULT_LEVEL = 1000.0
DEFAULT_HITS = 2
# Above any residual in the saved dumps, so a startup sample cannot latch.
FLOOR_PROBE_LEVEL = 1.0e9
FLOOR_PERCENTILE = 95.0
# Empty-lane tee power moves by about this much over a couple of seconds.
FLOOR_MARGIN = 1.5
FLOOR_MIN_SAMPLES = 8
FLOOR_SAMPLE_S = 2.0
FLOOR_PAUSE_S = 0.05


@dataclass(frozen=True)
class TriggerObservation:
    """One frame of the detector, matching a ``trig phase=...`` debug line."""

    frame: int
    phase: str
    tee: float
    approach: float
    ready: bool
    toward: bool
    away: bool
    run: int
    peak_bin: int
    have_peak: bool

    @property
    def fired(self) -> bool:
        """True on the frame that latches the freeze."""
        return self.phase == "fired"


class BallLeaveDetector:
    """Per-frame state machine from ``l3_considerSelfTrigger``."""

    def __init__(
        self,
        level: float = DEFAULT_LEVEL,
        hits: int = DEFAULT_HITS,
        approach_bins: int = APPROACH_BINS,
    ) -> None:
        if level <= 0.0:
            raise ValueError(f"self-trigger level must be > 0, got {level}")
        if hits < 1:
            raise ValueError(f"self-trigger hits must be >= 1, got {hits}")
        if approach_bins < 1:
            raise ValueError(f"approach bins must be >= 1, got {approach_bins}")
        self.level = float(level)
        self.hits = int(hits)
        self.approach_bins = int(approach_bins)
        self._tee = 0.0
        self._clear()
        self._latched = False
        self._approach_power = 0.0

    def _clear(self) -> None:
        self._ready = False
        self._toward = False
        self._away = False
        self._run = 0
        self._peak_bin = 0
        self._have_peak = False

    def step(
        self,
        frame: int,
        power: np.ndarray,
        tee_local: int | None,
        valid_bins: int,
    ) -> TriggerObservation:
        """Advance one frame. ``tee_local`` is None when that bin was not stored."""
        if self._latched:
            return self._observe(frame, "fired", float(self._tee), self._approach_power)
        if tee_local is None or tee_local < 0 or tee_local >= valid_bins or tee_local >= len(power):
            return self._observe(frame, "bin-outside", 0.0, 0.0)
        tee = float(power[tee_local])
        self._tee = tee
        if tee < self.level:
            self._clear()
            self._approach_power = 0.0
            return self._observe(frame, "tee-low", tee, 0.0)
        self._run += 1
        if not self._ready:
            if self._run >= self.hits:
                self._ready = True
            phase = "watching" if self._ready else "occupying"
            return self._observe(frame, phase, tee, 0.0)
        first = tee_local - self.approach_bins if tee_local > self.approach_bins else 0
        have_peak = False
        peak = 0.0
        peak_bin = 0
        for bin_index in range(first, tee_local):
            if bin_index >= valid_bins:
                break
            value = float(power[bin_index])
            if value >= self.level and (not have_peak or value > peak):
                peak = value
                peak_bin = bin_index
                have_peak = True
        if not have_peak:
            return self._observe(frame, "no-approach", tee, 0.0)
        if self._have_peak and peak_bin > self._peak_bin:
            self._toward = True
        elif self._toward and self._have_peak and peak_bin < self._peak_bin:
            self._away = True
        self._peak_bin = peak_bin
        self._have_peak = True
        self._approach_power = peak
        if self._away or self._departed(power, tee_local, valid_bins, peak):
            self._latch()
            return self._observe(frame, "fired", tee, peak)
        phase = "toward" if self._toward else "watching"
        return self._observe(frame, phase, tee, peak)

    def _departed(
        self, power: np.ndarray, tee_local: int, valid_bins: int, approach_peak: float
    ) -> bool:
        """Ball energy has moved past the tee while the tee bin is still occupied."""
        if not self._toward:
            return False
        last = min(valid_bins, len(power), tee_local + 1 + self.approach_bins)
        if tee_local + 1 >= last:
            return False
        past = float(np.max(power[tee_local + 1 : last]))
        return past >= self.level and past > approach_peak

    def _latch(self) -> None:
        self._clear()
        self._latched = True

    def _observe(self, frame: int, phase: str, tee: float, approach: float) -> TriggerObservation:
        return TriggerObservation(
            frame=frame,
            phase=phase,
            tee=tee,
            approach=approach,
            ready=self._ready,
            toward=self._toward,
            away=self._away,
            run=self._run,
            peak_bin=self._peak_bin,
            have_peak=self._have_peak,
        )


def loop0_vertical_power(cube: np.ndarray, n_tx: int) -> np.ndarray:
    """Loop-0 vertical residual power, one row per memory-order frame."""
    summary = vertical_loop_power(cube, n_tx=n_tx)
    frames = cube.shape[0]
    return np.asarray(summary.power).reshape(frames, summary.n_loops, -1)[:, 0, :]


def replay_dump(
    raw: bytes,
    *,
    tee_range_m: float = DEFAULT_TEE_RANGE_M,
    level: float = DEFAULT_LEVEL,
    hits: int = DEFAULT_HITS,
) -> list[TriggerObservation]:
    """Run the detector over a dump in capture order.

    Configurable-capture dumps are already oldest-first and store
    ``trigger_frame`` 0. Older rings store the oldest slot in
    ``trigger_frame``; both become time order here. Each frame watches the
    tee's absolute bin when that frame stored it.
    """
    meta, cube = parse_dump(raw)
    ranged = range_data(meta, cube)
    geometry = geometry_from_header(meta)
    absolute = int(round(tee_range_m / geometry.range_res_m))
    power = loop0_vertical_power(ranged, meta["n_tx"])
    detector = BallLeaveDetector(level=level, hits=hits)
    observations: list[TriggerObservation] = []
    n_frames = meta["n_frames"]
    origin = meta["trigger_frame"] % n_frames
    for time_index in range(n_frames):
        slot = (origin + time_index) % n_frames
        start = geometry.frame_bin_start(slot)
        count = geometry.frame_bin_count(slot)
        local = absolute - start
        tee_local = local if 0 <= local < count else None
        observations.append(detector.step(time_index, power[slot], tee_local, count))
    return observations


def tee_power_from_stats(text: str) -> float | None:
    """Tee residual from a stats ``trig`` line, or None when that line is absent."""
    latest: float | None = None
    for line in text.splitlines():
        marker = line.find("trig ")
        if marker < 0:
            continue
        for token in line[marker:].split():
            if not token.startswith("tee="):
                continue
            try:
                latest = float(token.split("=", 1)[1])
            except ValueError:
                continue
    return latest


def level_above_floor(samples: list[float]) -> tuple[float, float]:
    """Return ``(p95 floor, armed level)`` from empty-lane tee samples."""
    values = [float(sample) for sample in samples if math.isfinite(sample) and sample > 0.0]
    if len(values) < FLOOR_MIN_SAMPLES:
        raise ValueError(f"need at least {FLOOR_MIN_SAMPLES} background samples, got {len(values)}")
    floor = float(np.percentile(values, FLOOR_PERCENTILE))
    if not math.isfinite(floor) or floor <= 0.0:
        raise ValueError(f"background floor must be > 0, got {floor}")
    return floor, floor * FLOOR_MARGIN


def iter_dump_files(directory: Path) -> list[Path]:
    """ILD1 captures under ``directory``, sorted by name."""
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            if handle.read(4) == b"ILD1":
                found.append(path)
    return found
