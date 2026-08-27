"""Background sampling and reading selection for a barometer."""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import replace
from typing import Optional, Protocol

from ..air_density import AirConditions, AirConditionsError, air_density
from .models import AirSnapshot, PressureSample, ReadingSelection

logger = logging.getLogger(__name__)


class Barometer(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Sensor contract required by the service."""

    def initialize(self) -> None:
        """Configure and verify the sensor."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read(self, *, timestamp: float | None = None) -> PressureSample:
        """Read one pressure/temperature sample."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release sensor resources."""
        ...  # pylint: disable=unnecessary-ellipsis


class BarometerService:
    """
    Sample a barometer and expose the current atmospheric state.

    Deliberately much simpler than the inclinometer service. Enclosure pitch can
    change between one shot and the next, so that service must pick a snapshot
    timestamped before impact. Air density does not: pressure and temperature
    move over minutes and hours. A reading a minute old is as good as one taken
    at impact, so this samples slowly, medians a short window against I2C
    glitches, and hands out the latest value.

    `temperature_offset_c` is the most important setting here. A sensor die
    sharing an enclosure with a Raspberry Pi reads several degrees warm, and
    3 °C of temperature error is about 1% density — roughly a yard of driver
    carry, which is the entire benefit of measuring pressure in the first
    place. Calibrate it against a separate thermometer.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        sensor: Barometer,
        *,
        temperature_offset_c: float = 0.0,
        elevation_m: Optional[float] = None,
        sample_hz: float = 0.5,
        window_samples: int = 5,
        max_reading_age_s: float = 300.0,
    ):
        if sample_hz <= 0:
            raise ValueError("sample_hz must be positive")
        if window_samples < 1:
            raise ValueError("window_samples must be at least 1")
        if max_reading_age_s <= 0:
            raise ValueError("max_reading_age_s must be positive")
        self.sensor = sensor
        self.temperature_offset_c = temperature_offset_c
        self.elevation_m = elevation_m
        self.sample_hz = sample_hz
        self.window_samples = window_samples
        self.max_reading_age_s = max_reading_age_s
        self._samples: deque[PressureSample] = deque(maxlen=window_samples)
        self._latest: AirSnapshot | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        """Initialize hardware and start the sampling thread."""
        self.sensor.initialize()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sample_loop,
            name="openflight-barometer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and release the I2C bus."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.sensor.close()

    def _sample_loop(self) -> None:
        interval_s = 1.0 / self.sample_hz
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.add_sample(self.sensor.read())
                with self._lock:
                    self._last_error = None
            except Exception as error:  # pylint: disable=broad-exception-caught
                with self._lock:
                    self._last_error = str(error)
                logger.warning("BMP580 sample failed: %s", error)
            delay = max(0.0, interval_s - (time.monotonic() - started))
            self._stop_event.wait(delay)

    def add_sample(self, sample: PressureSample) -> AirSnapshot | None:
        """
        Add one sample and return the updated snapshot.

        Returns None until the window has filled, and again for any window whose
        median falls outside the atmosphere model's plausible range — a
        disconnected sensor reading zeroes must not be turned into a density.
        """
        with self._lock:
            self._samples.append(sample)
            if len(self._samples) < self.window_samples:
                return None
            samples = list(self._samples)
            pressures = [item.pressure_pa for item in samples]
            pressure_pa = statistics.median(pressures)
            raw_temperature_c = statistics.median(item.temperature_c for item in samples)
            temperature_c = raw_temperature_c + self.temperature_offset_c
            try:
                density = air_density(pressure_pa, temperature_c)
            except AirConditionsError as error:
                self._last_error = str(error)
                logger.warning("BMP580 reading rejected as implausible: %s", error)
                return None
            snapshot = AirSnapshot(
                timestamp=sample.timestamp,
                pressure_pa=pressure_pa,
                temperature_c=temperature_c,
                raw_temperature_c=raw_temperature_c,
                density_kg_m3=density,
                pressure_std_pa=statistics.pstdev(pressures),
                sample_count=len(samples),
            )
            self._latest = snapshot
            return snapshot

    def reading_for_shot(self, now: Optional[float] = None) -> ReadingSelection:
        """Select the current atmospheric reading, or explain why there is none."""
        reference = time.time() if now is None else now
        with self._lock:
            snapshot = self._latest
            last_error = self._last_error
        if snapshot is None:
            return ReadingSelection(
                snapshot=None,
                status="sensor_error" if last_error else "no_reading",
            )
        age_s = max(0.0, reference - snapshot.timestamp)
        if age_s > self.max_reading_age_s:
            return ReadingSelection(snapshot=None, status="stale", age_s=age_s)
        return ReadingSelection(snapshot=snapshot, status="ok", age_s=age_s)

    def current_conditions(self, now: Optional[float] = None) -> Optional[AirConditions]:
        """Return conditions from a fresh reading, or None if none is fresh."""
        selection = self.reading_for_shot(now)
        if selection.snapshot is None:
            return None
        return selection.snapshot.to_air_conditions(elevation_m=self.elevation_m)

    def last_known_conditions(self) -> Optional[AirConditions]:
        """
        Return the most recent reading regardless of age, or None if never read.

        This is the fallback when the sensor stops responding. Air density at a
        fixed site moves within a few percent over days, so even a very old
        measurement is far closer to the truth than a textbook assumption —
        and crucially it still carries the site's elevation implicitly, which
        is worth up to 14 yd of driver carry to get wrong. Labelled
        `sensor_stale` so the distinction survives into the logs.
        """
        with self._lock:
            snapshot = self._latest
        if snapshot is None:
            return None
        measured = snapshot.to_air_conditions(elevation_m=self.elevation_m)
        return replace(measured, source="sensor_stale")

    def wait_for_reading(self, timeout_s: float = 15.0) -> ReadingSelection:
        """
        Wait for the first filled sample window, for a startup diagnostic.

        The default timeout allows for a full window at the default 0.5 Hz;
        a caller that shortens `sample_hz` should shorten this too.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            selection = self.reading_for_shot()
            if selection.snapshot is not None:
                return selection
            time.sleep(min(0.05, 1.0 / self.sample_hz))
        return self.reading_for_shot()

    @property
    def last_error(self) -> str | None:
        """Most recent sensor error, cleared after the next valid read."""
        with self._lock:
            return self._last_error
