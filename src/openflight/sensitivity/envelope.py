"""Rolling capture of the SEN-14262 envelope, for closed-loop gain control.

An impact envelope peaks within a few milliseconds. Starting an ADC read from
the trigger callback would race that peak and usually lose, so this samples
continuously into a ring buffer and looks *backwards* from the shot timestamp
instead — the same shape as ``InclinometerService``, and for the same reason.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

# How far either side of the impact timestamp to search for the peak. Wide
# enough to cover trigger-to-timestamp jitter and the envelope's decay, narrow
# enough not to catch a neighbouring strike.
DEFAULT_LOOKBACK_S = 0.15
DEFAULT_LOOKAHEAD_S = 0.15

# Retained history. Enough for a shot's peak to still be present by the time
# the shot pipeline finishes processing and asks for it.
DEFAULT_HISTORY_S = 3.0


class VoltageSource(Protocol):  # pylint: disable=unnecessary-ellipsis
    """The ADC contract the monitor needs; see :class:`~.ads1115.ADS1115`."""

    def open(self) -> None:
        """Make the device ready for use."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read_volts(self) -> float:
        """Return the most recent conversion in volts."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the device."""
        ...  # pylint: disable=unnecessary-ellipsis


@dataclass(frozen=True)
class EnvelopePeak:
    """The largest envelope sample found around one impact."""

    volts: float
    timestamp: float
    sample_count: int
    #: Fraction of the detector's own supply, which is where it clips --
    #: not a fraction of the ADC's range, which is wider.
    fraction_of_full_scale: float
    clipped: bool

    def to_dict(self) -> dict:
        """Return the WebSocket payload for this peak."""
        return {
            "volts": round(self.volts, 4),
            "fraction_of_full_scale": round(self.fraction_of_full_scale, 4),
            "sample_count": self.sample_count,
            "clipped": self.clipped,
        }


class EnvelopeMonitor:
    """Sample the envelope continuously and serve per-impact peaks."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        adc: VoltageSource,
        *,
        full_scale_volts: float,
        history_s: float = DEFAULT_HISTORY_S,
        lookback_s: float = DEFAULT_LOOKBACK_S,
        lookahead_s: float = DEFAULT_LOOKAHEAD_S,
        sample_interval_s: float = 0.0,
        clip_fraction: float = 0.98,
    ):
        if full_scale_volts <= 0:
            raise ValueError("full_scale_volts must be positive")
        self.adc = adc
        self.full_scale_volts = full_scale_volts
        self.lookback_s = lookback_s
        self.lookahead_s = lookahead_s
        self.sample_interval_s = sample_interval_s
        self.clip_fraction = clip_fraction
        # Sized from the ADC's rate rather than a guess, so history_s means
        # what it says regardless of how fast the part is configured to run.
        rate = getattr(adc, "data_rate", 860)
        self._samples: deque = deque(maxlen=max(64, int(history_s * rate)))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_error: Optional[str] = None

    @property
    def last_error(self) -> Optional[str]:
        """Most recent sampling error, cleared after the next good read."""
        with self._lock:
            return self._last_error

    def start(self) -> None:
        """Open the ADC and start the sampling thread."""
        self.adc.open()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sample_loop, name="openflight-envelope", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and release the ADC."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.adc.close()

    def add_sample(self, volts: float, timestamp: Optional[float] = None) -> None:
        """Record one sample. Exposed so tests can drive the buffer directly."""
        with self._lock:
            self._samples.append((timestamp if timestamp is not None else time.time(), volts))

    def latest_sample(self) -> Optional[EnvelopePeak]:
        """Return the most recent sample, or None if the buffer is empty.

        Used by the Debug Sound tab's live gauge. Distinct from
        ``peak_for_impact``, which searches a window around a shot.
        """
        with self._lock:
            if not self._samples:
                return None
            timestamp, volts = self._samples[-1]
        return self._peak(volts, timestamp, sample_count=1)

    def peak_for_impact(self, impact_timestamp: float) -> Optional[EnvelopePeak]:
        """Return the largest sample around ``impact_timestamp``, or None.

        None means the window held no samples at all — the monitor was not
        running, or the shot is older than the retained history. That is
        reported rather than guessed at, because a fabricated peak would steer
        the gain controller.
        """
        low = impact_timestamp - self.lookback_s
        high = impact_timestamp + self.lookahead_s
        with self._lock:
            window = [(ts, volts) for ts, volts in self._samples if low <= ts <= high]
        if not window:
            return None
        timestamp, volts = max(window, key=lambda item: item[1])
        return self._peak(volts, timestamp, sample_count=len(window))

    def _peak(self, volts: float, timestamp: float, sample_count: int) -> EnvelopePeak:
        fraction = volts / self.full_scale_volts
        return EnvelopePeak(
            volts=volts,
            timestamp=timestamp,
            sample_count=sample_count,
            fraction_of_full_scale=fraction,
            clipped=fraction >= self.clip_fraction,
        )

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                volts = self.adc.read_volts()
            except Exception as error:  # pylint: disable=broad-exception-caught
                with self._lock:
                    self._last_error = str(error)
                logger.warning("[SENSITIVITY] Envelope sample failed: %s", error)
                # Back off rather than spinning on a dead bus.
                self._stop_event.wait(0.5)
                continue
            with self._lock:
                self._samples.append((time.time(), volts))
                self._last_error = None
            if self.sample_interval_s:
                self._stop_event.wait(self.sample_interval_s)
