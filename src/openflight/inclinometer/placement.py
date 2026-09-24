"""Detect when the enclosure has been put down and left still."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Literal, Protocol

from .models import StillnessState

logger = logging.getLogger(__name__)

PlacementReason = Literal["startup", "moved", "reoriented"]


class StillnessSource(Protocol):  # pylint: disable=unnecessary-ellipsis,too-few-public-methods
    """Subset of InclinometerService used by the monitor."""

    def stillness(self, now: float | None = None) -> StillnessState:
        """Report the current stationary period."""
        ...  # pylint: disable=unnecessary-ellipsis


class PlacementMonitor:
    """Call ``on_settled`` once the enclosure is still for ``settle_s`` after handling.

    Operators often hold the unit while it boots, so anything calibrated at
    startup (such as camera exposure) may see a different scene than the final
    position. The monitor starts pending, so it always fires once after the
    first settled period, then again whenever the unit is moved (the sensor
    reports motion) or ends up in a different orientation, once it has been
    left alone for ``settle_s``.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        source: StillnessSource,
        on_settled: Callable[[PlacementReason], object],
        *,
        settle_s: float = 5.0,
        reposition_deg: float = 2.0,
        poll_s: float = 0.25,
    ):
        if settle_s <= 0:
            raise ValueError("settle_s must be positive")
        if reposition_deg <= 0:
            raise ValueError("reposition_deg must be positive")
        if poll_s <= 0:
            raise ValueError("poll_s must be positive")
        self.source = source
        self.on_settled = on_settled
        self.settle_s = settle_s
        self.reposition_deg = reposition_deg
        self.poll_s = poll_s
        self._pending: PlacementReason | None = "startup"
        self._seen_motion_timestamp: float | None = None
        self._reference_pitch_deg: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def pending(self) -> PlacementReason | None:
        """Why the next settled period will fire, or None when idle."""
        return self._pending

    def start(self) -> None:
        """Start polling on a daemon thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="openflight-placement",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop polling; an in-progress callback is allowed to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(self.poll_s):
            try:
                self.poll()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[PLACEMENT] Placement check failed", exc_info=True)

    def poll(self, now: float | None = None) -> PlacementReason | None:
        """Check the sensor once and fire the callback when settled.

        Returns the reason the callback fired, or None.
        """
        state = self.source.stillness(now)
        if (
            state.last_motion_timestamp is not None
            and state.last_motion_timestamp != self._seen_motion_timestamp
        ):
            self._seen_motion_timestamp = state.last_motion_timestamp
            if self._pending is None:
                self._pending = "moved"
        if state.snapshot is None:
            return None

        pitch = state.snapshot.raw_pitch_deg
        if (
            self._pending is None
            and self._reference_pitch_deg is not None
            and abs(pitch - self._reference_pitch_deg) > self.reposition_deg
        ):
            self._pending = "reoriented"
        if self._pending is None or state.stationary_s < self.settle_s:
            return None

        reason = self._pending
        self._pending = None
        self._reference_pitch_deg = pitch
        logger.info(
            "[PLACEMENT] Enclosure settled for %.1fs at %+.2fdeg (%s)",
            state.stationary_s,
            pitch,
            reason,
        )
        try:
            self.on_settled(reason)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[PLACEMENT] Settled callback failed (%s)", reason, exc_info=True)
        return reason
