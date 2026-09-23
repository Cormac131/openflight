"""Shadow-mode comparison of the camera trigger against the sound trigger.

In shadow mode the sound trigger still drives the OPS243; the camera address
monitor runs alongside but never sends ``S!``. This comparator pairs each
camera departure with the accepted sound-triggered shot at the same impact
and logs one outcome per event, so a session log answers:

* ``matched``      both fired; ``delta_ms`` = camera impact − sound impact
* ``camera_only``  camera fired, no accepted shot followed (false positive)
* ``sound_only``   accepted shot with no camera departure (camera miss)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from openflight.camera.address_monitor import CameraTriggerEvent

logger = logging.getLogger(__name__)


class ShadowTriggerComparator:
    """Pair camera departures with sound-trigger shots by impact time."""

    def __init__(
        self,
        log_fn: Callable[[dict], None],
        *,
        match_tolerance_s: float = 0.75,
        camera_expiry_s: float = 10.0,
        clock: Callable[[], float] = time.time,
    ):
        """
        Args:
            log_fn: Receives one dict per outcome (session logger sink).
            match_tolerance_s: Max |camera impact − sound impact| for a match.
            camera_expiry_s: How long a camera departure waits for its shot.
                Sound shots arrive seconds after impact (UART dump + DSP).
            clock: Host epoch clock (injectable for tests).
        """
        if match_tolerance_s <= 0 or camera_expiry_s <= 0:
            raise ValueError("tolerance and expiry must be positive")
        self._log_fn = log_fn
        self._tolerance_s = match_tolerance_s
        self._expiry_s = camera_expiry_s
        self._clock = clock
        self._pending: list[CameraTriggerEvent] = []
        self._lock = threading.Lock()
        self.counts = {"matched": 0, "camera_only": 0, "sound_only": 0}
        self._deltas_ms: list[float] = []

    def on_camera_trigger(self, event: CameraTriggerEvent) -> None:
        """Record a camera departure (camera callback thread)."""
        with self._lock:
            self._pending.append(event)
        self.flush()

    def on_sound_shot(self, impact_epoch: Optional[float]) -> None:
        """Record an accepted sound-triggered shot and pair it."""
        if impact_epoch is None:
            return
        self.flush()
        with self._lock:
            best = None
            for event in self._pending:
                delta = abs(event.impact_epoch - impact_epoch)
                if delta <= self._tolerance_s and (
                    best is None or delta < abs(best.impact_epoch - impact_epoch)
                ):
                    best = event
            if best is not None:
                self._pending.remove(best)
        if best is None:
            self._emit({"outcome": "sound_only", "sound_impact_epoch": impact_epoch})
            return
        delta_ms = (best.impact_epoch - impact_epoch) * 1000.0
        self._emit(
            {
                "outcome": "matched",
                "sound_impact_epoch": impact_epoch,
                "delta_ms": round(delta_ms, 3),
                "camera": best.to_dict(),
            }
        )

    def flush(self, *, final: bool = False) -> None:
        """Log camera departures that waited too long for a shot."""
        now = self._clock()
        with self._lock:
            expired = [
                event
                for event in self._pending
                if final or now - event.confirmed_epoch > self._expiry_s
            ]
            self._pending = [event for event in self._pending if event not in expired]
        for event in expired:
            self._emit({"outcome": "camera_only", "camera": event.to_dict()})

    def summary(self) -> dict:
        """Counts and timing agreement so far."""
        with self._lock:
            deltas = sorted(self._deltas_ms)
            counts = dict(self.counts)
        return {
            **counts,
            "median_delta_ms": deltas[len(deltas) // 2] if deltas else None,
            "max_abs_delta_ms": max((abs(d) for d in deltas), default=None),
        }

    def _emit(self, entry: dict) -> None:
        with self._lock:
            self.counts[entry["outcome"]] += 1
            if "delta_ms" in entry:
                self._deltas_ms.append(entry["delta_ms"])
        logger.info("[CAMERA-SHADOW] %s", entry)
        try:
            self._log_fn(entry)
        except Exception:  # pylint: disable=broad-except
            logger.warning("[CAMERA-SHADOW] Shadow log sink failed", exc_info=True)
