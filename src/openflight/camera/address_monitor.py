"""Runtime glue between the camera frame stream and the address state machine.

``CameraAddressMonitor`` is registered as a frame observer on
``CameraCaptureRuntime``. It does the cheap per-frame ROI work on the camera's
callback thread and runs the expensive whole-frame ball acquisition on its own
low-rate worker, fed from a single latest-frame slot (never a queue).

A confirmed departure becomes a :class:`CameraTriggerEvent` with host-epoch
timestamps, which ``CameraTrigger`` (the rolling-buffer trigger strategy)
waits on before sending ``S!`` to the OPS243.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

import numpy as np

from openflight.camera.address_trigger import (
    AddressEvent,
    AddressTrigger,
    AddressTriggerConfig,
    BallAddressStateMachine,
    BallCandidate,
)
from openflight.camera.triggered_buffer import CameraFrame

logger = logging.getLogger(__name__)

AcquireFn = Callable[[np.ndarray], Optional[BallCandidate]]


@dataclass(frozen=True)
class CameraTriggerEvent:
    """A confirmed ball departure in host-epoch seconds."""

    impact_epoch: float
    impact_uncertainty_ms: float
    confirmed_epoch: float
    ball: BallCandidate
    addressed: bool

    def to_dict(self) -> dict:
        """JSON-safe summary for diagnostics and session logs."""
        return {
            "impact_epoch": self.impact_epoch,
            "impact_uncertainty_ms": round(self.impact_uncertainty_ms, 3),
            "confirmed_epoch": self.confirmed_epoch,
            "confirm_delay_ms": round((self.confirmed_epoch - self.impact_epoch) * 1000.0, 3),
            "ball_x": self.ball.x,
            "ball_y": self.ball.y,
            "ball_radius": self.ball.radius,
            "addressed": self.addressed,
        }


def _boottime_ns() -> int:
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)  # type: ignore[attr-defined]


def _default_clocks() -> dict[str, Callable[[], int]]:
    clocks: dict[str, Callable[[], int]] = {"monotonic": time.monotonic_ns}
    if hasattr(time, "CLOCK_BOOTTIME"):
        clocks["boottime"] = _boottime_ns
    return clocks


class SensorClockMapper:  # pylint: disable=too-few-public-methods
    """Map libcamera ``SensorTimestamp`` nanoseconds to host epoch seconds.

    libcamera documents the sensor timestamp against a kernel clock that has
    varied between releases (MONOTONIC vs BOOTTIME). Rather than assume, the
    first frame picks whichever candidate clock places the frame a plausible
    moment in the past. Conversion re-reads that clock and the wall clock each
    time, so NTP slews never accumulate.
    """

    MAX_PLAUSIBLE_AGE_NS = 1_000_000_000

    def __init__(
        self,
        clocks: Optional[dict[str, Callable[[], int]]] = None,
        epoch_ns: Callable[[], int] = time.time_ns,
    ):
        self._clocks = clocks if clocks is not None else _default_clocks()
        self._epoch_ns = epoch_ns
        self.clock_name: Optional[str] = None
        self._assumed_offset_ns: Optional[int] = None

    def to_epoch(self, sensor_ns: int) -> float:
        """Return host epoch seconds for a sensor timestamp."""
        if self.clock_name is None:
            self._calibrate(sensor_ns)
        if self.clock_name in self._clocks:
            clock_now = self._clocks[self.clock_name]()
            epoch_now = self._epoch_ns()
            return (sensor_ns + (epoch_now - clock_now)) / 1e9
        assert self._assumed_offset_ns is not None
        return (sensor_ns + self._assumed_offset_ns) / 1e9

    def _calibrate(self, sensor_ns: int) -> None:
        for name, clock in self._clocks.items():
            age = clock() - sensor_ns
            if 0 <= age <= self.MAX_PLAUSIBLE_AGE_NS:
                self.clock_name = name
                logger.info("[CAMERA-TRIGGER] Sensor timestamps use the %s clock", name)
                return
        # Unknown clock: treat the calibrating frame as having just arrived.
        # Accurate to the camera pipeline latency (a few ms).
        self.clock_name = "assumed_now"
        self._assumed_offset_ns = self._epoch_ns() - sensor_ns
        logger.warning("[CAMERA-TRIGGER] Sensor clock not recognised; assuming frame arrival time")


def ball_detector_acquirer(detector_config=None) -> AcquireFn:
    """Adapt the Hough-circle ``BallDetector`` to the acquisition interface."""
    # pylint: disable=import-outside-toplevel
    from openflight.camera.capture import CapturedFrame
    from openflight.camera.detector import BallDetector

    detector = BallDetector(detector_config)
    frame_number = 0

    def acquire(image: np.ndarray) -> Optional[BallCandidate]:
        nonlocal frame_number
        frame_number += 1
        found = detector.detect(
            CapturedFrame(data=image, timestamp=time.time(), frame_number=frame_number)
        )
        if found is None:
            return None
        return BallCandidate(found.x, found.y, found.radius)

    return acquire


def _brightness_scale(frame: CameraFrame) -> float:
    scale = float(frame.exposure_us) * float(frame.analogue_gain)
    return scale if scale > 0 else 1.0


class CameraAddressMonitor:  # pylint: disable=too-many-instance-attributes
    """Feed camera frames to the address state machine and publish triggers."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        config: Optional[AddressTriggerConfig] = None,
        *,
        acquire_fn: AcquireFn,
        acquisition_interval_s: float = 0.1,
        watchdog_timeout_s: float = 0.5,
        auto_rearm: bool = False,
        clock_mapper: Optional[SensorClockMapper] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if acquisition_interval_s <= 0:
            raise ValueError("acquisition_interval_s must be positive")
        if watchdog_timeout_s <= 0:
            raise ValueError("watchdog_timeout_s must be positive")
        self.machine = BallAddressStateMachine(config)
        self._acquire_fn = acquire_fn
        self._acquisition_interval_s = acquisition_interval_s
        self._watchdog_timeout_s = watchdog_timeout_s
        self._auto_rearm = auto_rearm
        self._clock = clock_mapper or SensorClockMapper()
        self._monotonic = monotonic

        self._lock = threading.Lock()
        self._trigger_ready = threading.Event()
        self._pending: Optional[CameraTriggerEvent] = None
        self._latest: Optional[CameraFrame] = None
        self._last_frame_monotonic: Optional[float] = None
        self._frames_seen = 0
        self._triggers = 0
        self._last_trigger: Optional[CameraTriggerEvent] = None
        self._last_event: Optional[str] = None
        self._callback_durations_us: Deque[float] = deque(maxlen=900)
        self._listeners: list[Callable[[CameraTriggerEvent], None]] = []
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._stall_reported = False

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start the background acquisition worker."""
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._acquisition_loop,
            name="camera-address-acquire",
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        """Stop the acquisition worker."""
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None

    def add_listener(self, listener: Callable[[CameraTriggerEvent], None]) -> None:
        """Call ``listener`` (outside the lock) on every confirmed trigger."""
        self._listeners.append(listener)

    # --------------------------------------------------------- frame thread

    def on_frame(self, frame: CameraFrame) -> None:
        """Frame observer: cheap ROI work only (camera callback thread)."""
        started = time.perf_counter()
        trigger_event: Optional[CameraTriggerEvent] = None
        with self._lock:
            self._latest = frame
            self._last_frame_monotonic = self._monotonic()
            self._frames_seen += 1
            self._stall_reported = False
            events = self.machine.feed(
                frame.image,
                frame.sensor_timestamp_ns,
                _brightness_scale(frame),
            )
            for event in events:
                self._log_event(event)
                if event.trigger is not None:
                    trigger_event = self._to_camera_event(event.trigger)
            if trigger_event is not None:
                self._pending = trigger_event
                self._last_trigger = trigger_event
                self._triggers += 1
                self._trigger_ready.set()
                if self._auto_rearm:
                    self.machine.rearm()
        self._callback_durations_us.append((time.perf_counter() - started) * 1e6)

        if trigger_event is not None:
            for listener in list(self._listeners):
                try:
                    listener(trigger_event)
                except Exception:  # pylint: disable=broad-except
                    logger.warning("[CAMERA-TRIGGER] Trigger listener failed", exc_info=True)

    def _to_camera_event(self, trigger: AddressTrigger) -> CameraTriggerEvent:
        return CameraTriggerEvent(
            impact_epoch=self._clock.to_epoch(trigger.impact_sensor_ns),
            impact_uncertainty_ms=trigger.impact_uncertainty_ns / 1e6,
            confirmed_epoch=self._clock.to_epoch(trigger.confirmed_sensor_ns),
            ball=trigger.ball,
            addressed=trigger.addressed,
        )

    def _log_event(self, event: AddressEvent) -> None:
        self._last_event = event.kind
        if event.kind in ("trigger", "locked", "lost", "rejected_not_addressed", "stall"):
            logger.info("[CAMERA-TRIGGER] %s (%s) %s", event.kind, event.state.value, event.detail)
        else:
            logger.debug("[CAMERA-TRIGGER] %s (%s) %s", event.kind, event.state.value, event.detail)

    # ---------------------------------------------------------- acquisition

    def _acquisition_loop(self) -> None:
        while not self._stop.wait(self._acquisition_interval_s):
            self.acquire_once()

    def acquire_once(self) -> list[AddressEvent]:
        """Run the whole-frame detector on the latest frame if IDLE."""
        with self._lock:
            frame = self._latest
            if frame is None or not self.machine.needs_acquisition:
                return []
        try:
            candidate = self._acquire_fn(frame.image)
        except Exception:  # pylint: disable=broad-except
            logger.warning("[CAMERA-TRIGGER] Ball acquisition failed", exc_info=True)
            return []
        with self._lock:
            events = self.machine.acquire(
                frame.image,
                frame.sensor_timestamp_ns,
                candidate,
                _brightness_scale(frame),
            )
            for event in events:
                self._log_event(event)
        return events

    # -------------------------------------------------------- trigger side

    def wait_for_trigger(
        self,
        timeout: float,
        cancel_event: Optional[threading.Event] = None,
    ) -> Optional[CameraTriggerEvent]:
        """Block until a departure is confirmed, cancelled, or timeout."""
        # Real time for the deadline; the injected clock is only for frame health.
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if self._trigger_ready.wait(min(0.05, remaining)):
                with self._lock:
                    event = self._pending
                    self._pending = None
                    self._trigger_ready.clear()
                if event is not None:
                    return event
            if cancel_event is not None and cancel_event.is_set():
                return None
            if not self.is_healthy() and not self._stall_reported:
                self._stall_reported = True
                logger.warning(
                    "[CAMERA-TRIGGER] No camera frames for >%.1fs — trigger unarmed",
                    self._watchdog_timeout_s,
                )

    def rearm(self) -> None:
        """Require a new ball at address before the next trigger."""
        with self._lock:
            self.machine.rearm()
            self._pending = None
            self._trigger_ready.clear()

    def is_healthy(self) -> bool:
        """True while frames are arriving within the watchdog timeout."""
        last = self._last_frame_monotonic
        return last is not None and self._monotonic() - last <= self._watchdog_timeout_s

    def status(self) -> dict:
        """Snapshot for ``trigger_status`` / diagnostics."""
        with self._lock:
            ball = self.machine.locked_ball
            state = self.machine.state.value
            addressed = self.machine.addressed
            last_trigger = self._last_trigger
            durations = list(self._callback_durations_us)
        healthy = self.is_healthy()
        return {
            "state": state,
            "armed": healthy,
            "healthy": healthy,
            "addressed": addressed,
            "ball": (
                {"x": ball.x, "y": ball.y, "radius": ball.radius} if ball is not None else None
            ),
            "frames_seen": self._frames_seen,
            "triggers": self._triggers,
            "last_event": self._last_event,
            "last_trigger": last_trigger.to_dict() if last_trigger else None,
            "sensor_clock": self._clock.clock_name,
            "callback_us_p50": (
                round(float(np.percentile(durations, 50)), 1) if durations else None
            ),
            "callback_us_p99": (
                round(float(np.percentile(durations, 99)), 1) if durations else None
            ),
        }
