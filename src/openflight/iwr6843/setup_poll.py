"""Poll the IWR6843 ball-placement detector and publish setup guidance.

The kiosk shows a setup banner (ball seen at 1.61 m, ready; ball at 2.3 m,
move OpenFlight closer) from the firmware's ``ball status`` line. The radar
serial port belongs to the capture monitor's worker once it starts, so every
read goes through :meth:`IWR6843CaptureMonitor.submit`: a job is queued only
when the previous one has finished, so a slow capture never piles polls up
behind it, and a read that fails is reported in the payload rather than
raised. Nothing here depends on Flask: the poller publishes through a
callable, and :func:`setup_payload` is a pure function over the parsed status.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .tee_scan import BallStatus, classify_setup, parse_ball_status

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 1.0
BALL_DETECTOR_MODES = ("off", "on", "follow")

# Firmware detector states, kept as the wire vocabulary for the UI.
STATES = ("off", "building", "waiting", "candidate", "locked")


def setup_payload(
    status: BallStatus | None,
    *,
    error: str | None = None,
    enabled: bool = True,
    timestamp: float | None = None,
) -> dict[str, Any]:
    """The ``iwr_setup`` event body: detector state, range and placement advice.

    ``label``/``ok``/``move_cm``/``message`` are present only while the ball is
    locked; the UI keys its wording on ``state`` and ``label`` and uses
    ``message`` as the untranslated fallback.
    """
    payload: dict[str, Any] = {
        "enabled": enabled,
        "state": "error" if error else (status.state if status is not None else "off"),
        "follow": bool(status.follow) if status is not None else False,
        "bin": status.bin if status is not None else None,
        "range_m": None,
        "confidence": round(status.confidence, 3) if status is not None else 0.0,
        "ratio": round(status.ratio, 2) if status is not None else 0.0,
        "reason": status.reason if status is not None else None,
        "label": None,
        "ok": None,
        "move_cm": None,
        "message": None,
        "error": error,
        "timestamp": time.time() if timestamp is None else timestamp,
    }
    if status is not None and status.locked and status.range_m is not None:
        advice = classify_setup(status.range_m)
        payload.update(
            range_m=round(status.range_m, 3),
            label=advice.label,
            ok=advice.ok,
            move_cm=round(advice.move_m * 100.0),
            message=advice.message,
        )
    return payload


class SetupPoller:
    """Periodically read ``ball status`` through the monitor's job queue.

    ``submit(name, job)`` is the capture monitor's method (or any callable with
    its contract: False when the worker is not running). ``publish(payload)``
    receives every payload; the latest is kept for clients that connect later.
    """

    def __init__(
        self,
        submit: Callable[[str, Callable[[Any], None]], bool],
        publish: Callable[[dict[str, Any]], None],
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("setup poll interval must be positive")
        self._submit = submit
        self._publish = publish
        self.interval_s = float(interval_s)
        self._clock = clock
        self._pending = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.latest: dict[str, Any] | None = None
        self.polls = 0
        self.failures = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="iwr6843-setup-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.interval_s + 1.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and not self._stop.is_set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval_s)

    # -- one poll ----------------------------------------------------------
    def tick(self) -> bool:
        """Queue one read unless the last is still waiting on the worker.

        Returns True when a job was submitted.
        """
        with self._lock:
            if self._pending:
                return False
            self._pending = True
        if not self._submit("setup-poll", self._job):
            with self._lock:
                self._pending = False
            return False
        return True

    def _job(self, radar) -> None:
        try:
            self.poll(radar)
        finally:
            with self._lock:
                self._pending = False

    def poll(self, radar) -> dict[str, Any]:
        """Read, parse, classify and publish once. Never raises."""
        self.polls += 1
        try:
            status = parse_ball_status(radar.ball_status())
            payload = setup_payload(status, timestamp=self._clock())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.failures += 1
            logger.warning("[IWR6843] Setup poll failed: %s", exc)
            payload = setup_payload(None, error=str(exc), timestamp=self._clock())
        self.latest = payload
        try:
            self._publish(payload)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[IWR6843] Setup payload not published", exc_info=True)
        return payload


__all__ = [
    "BALL_DETECTOR_MODES",
    "DEFAULT_INTERVAL_S",
    "STATES",
    "SetupPoller",
    "setup_payload",
]
