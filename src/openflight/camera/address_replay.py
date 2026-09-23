"""Offline replay of saved camera clips through the address state machine.

Every sound-triggered shot already saves a ``frames.npz`` clip (see
``CameraCaptureRuntime._save_capture``) with ~150 ms of frames before the sound
edge. Replaying those clips answers, from real lighting and real clubs:

* does the camera trigger fire on this shot, and
* how far is its impact estimate from the sound trigger (``delta_ms``)?

Clips are short, so the ball is acquired from the clip's own first frames and
the club-at-address requirement is off by default (address happened long
before the clip starts). Waggle false-positives cannot be measured this way;
use ``--camera-trigger-shadow`` for that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from openflight.camera.address_trigger import (
    AddressTriggerConfig,
    BallAddressStateMachine,
    BallCandidate,
)

AcquireFn = Callable[[np.ndarray], Optional[BallCandidate]]


@dataclass(frozen=True)
class ClipReplayResult:
    """Outcome of replaying one clip."""

    path: str
    frames: int
    locked: bool
    triggered: bool
    delta_ms: Optional[float]
    impact_uncertainty_ms: Optional[float]
    events: tuple[str, ...]

    def to_dict(self) -> dict:
        """JSON-safe form for reports."""
        return {
            "path": self.path,
            "frames": self.frames,
            "locked": self.locked,
            "triggered": self.triggered,
            "delta_ms": self.delta_ms,
            "impact_uncertainty_ms": self.impact_uncertainty_ms,
            "events": list(self.events),
        }


def replay_frames(  # pylint: disable=too-many-arguments,too-many-locals
    frames: np.ndarray,
    sensor_ns: np.ndarray,
    *,
    acquire_fn: AcquireFn,
    config: Optional[AddressTriggerConfig] = None,
    brightness: Optional[np.ndarray] = None,
    sound_trigger_index: Optional[int] = None,
    acquire_frames: int = 10,
    path: str = "",
) -> ClipReplayResult:
    """Replay a frame stack.

    Args:
        frames: (N, H, W) luma frames.
        sensor_ns: (N,) sensor timestamps.
        acquire_fn: Whole-frame ball detector.
        config: State-machine config (``require_address`` defaults off here).
        brightness: Optional (N,) exposure*gain per frame.
        sound_trigger_index: Frame index of the sound edge, for ``delta_ms``.
        acquire_frames: Leading frames offered to the acquisition detector.
        path: Label for the result.
    """
    if frames.ndim != 3 or len(frames) != len(sensor_ns):
        raise ValueError("frames must be (N, H, W) with one timestamp per frame")
    machine = BallAddressStateMachine(config or AddressTriggerConfig(require_address=False))
    scales = brightness if brightness is not None else np.ones(len(frames))
    events: list[str] = []
    trigger = None
    locked = False

    for index, (frame, ts, scale) in enumerate(zip(frames, sensor_ns, scales)):
        ts = int(ts)
        if machine.needs_acquisition and index < acquire_frames:
            for event in machine.acquire(frame, ts, acquire_fn(frame), float(scale)):
                events.append(event.kind)
                locked = locked or event.kind == "locked"
            continue
        for event in machine.feed(frame, ts, float(scale)):
            events.append(event.kind)
            if event.trigger is not None:
                trigger = event.trigger
        if trigger is not None:
            break

    delta_ms = None
    uncertainty_ms = None
    if trigger is not None:
        uncertainty_ms = trigger.impact_uncertainty_ns / 1e6
        if sound_trigger_index is not None and 0 <= sound_trigger_index < len(sensor_ns):
            delta_ms = (trigger.impact_sensor_ns - int(sensor_ns[sound_trigger_index])) / 1e6

    return ClipReplayResult(
        path=path,
        frames=len(frames),
        locked=locked,
        triggered=trigger is not None,
        delta_ms=delta_ms,
        impact_uncertainty_ms=uncertainty_ms,
        events=tuple(events),
    )


def replay_clip(
    npz_path: str | Path,
    *,
    acquire_fn: AcquireFn,
    config: Optional[AddressTriggerConfig] = None,
    acquire_frames: int = 10,
) -> ClipReplayResult:
    """Replay one saved ``frames.npz`` clip."""
    with np.load(npz_path) as archive:
        frames = archive["frames"]
        sensor_ns = archive["sensor_timestamp_ns"]
        brightness = None
        if "exposure_us" in archive and "analogue_gain" in archive:
            brightness = archive["exposure_us"].astype(np.float64) * archive[
                "analogue_gain"
            ].astype(np.float64)
            brightness = np.where(brightness > 0, brightness, 1.0)
        sound_index = (
            int(archive["pre_trigger_count"]) - 1 if "pre_trigger_count" in archive else None
        )
    return replay_frames(
        frames,
        sensor_ns,
        acquire_fn=acquire_fn,
        config=config,
        brightness=brightness,
        sound_trigger_index=sound_index,
        acquire_frames=acquire_frames,
        path=str(npz_path),
    )


def fixed_ball_acquirer(ball: BallCandidate) -> AcquireFn:
    """Acquirer that always reports a known ball position (no OpenCV needed)."""
    return lambda _frame: ball


def summarize(results: list[ClipReplayResult]) -> dict:
    """Aggregate replay results for a report."""
    deltas = sorted(r.delta_ms for r in results if r.delta_ms is not None)
    return {
        "clips": len(results),
        "locked": sum(r.locked for r in results),
        "triggered": sum(r.triggered for r in results),
        "missed": [r.path for r in results if r.locked and not r.triggered],
        "not_locked": [r.path for r in results if not r.locked],
        "median_delta_ms": deltas[len(deltas) // 2] if deltas else None,
        "max_abs_delta_ms": max((abs(d) for d in deltas), default=None),
    }
