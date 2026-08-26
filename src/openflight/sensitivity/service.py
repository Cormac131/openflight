"""Runtime control of the SEN-14262 preamp gain via a DS3502 digital pot.

The service owns the lock that keeps concurrent WebSocket handlers from issuing
overlapping I2C transactions, and the translation between a wiper step and the
numbers a user reasons about (percent, ohms).

It deliberately does *not* own a persistence file. The DS3502 reads its own
wiper back and can commit it to EEPROM, so the chip is the single source of
truth for the current setting — a mirror on the Pi could only disagree with it.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Protocol

from .ds3502 import (
    DEFAULT_SERIES_OHMS,
    MAX_POSITION,
    position_for_resistance,
    preamp_feedback_ohms,
    resistance_ohms,
    sensitivity_percent,
)
from .models import SensitivityState

logger = logging.getLogger(__name__)

# The wiring guide's long-standing advice for a hand-soldered R17.
DEFAULT_R17_OHMS = 47_000.0


class DigitalPotentiometer(Protocol):  # pylint: disable=unnecessary-ellipsis
    """The wiper contract the service needs; see :class:`~.ds3502.DS3502`."""

    series_ohms: float

    @property
    def position(self) -> Optional[int]:
        """Live wiper position, or None when the device is closed."""
        ...  # pylint: disable=unnecessary-ellipsis

    def open(self) -> None:
        """Make the device ready for use."""
        ...  # pylint: disable=unnecessary-ellipsis

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Move the wiper to ``position`` and return it."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the device."""
        ...  # pylint: disable=unnecessary-ellipsis


def clamp_position(value) -> int:
    """Coerce a UI-supplied wiper position into 0..127.

    Clamping rather than rejecting is deliberate: a slider that runs past the
    end should saturate, and the state echoed back to the UI carries the step
    that was actually applied.

    Raises:
        TypeError: for a bool, which ``int()`` would silently accept as 0/1.
        ValueError: for anything else ``int()`` cannot convert.
    """
    if isinstance(value, bool):
        raise TypeError("position must be a number, not a bool")
    position = int(value)
    return max(0, min(MAX_POSITION, position))


class SoundSensitivityService:
    """Apply and report the sound detector's preamp sensitivity."""

    def __init__(
        self,
        pot: DigitalPotentiometer,
        *,
        simulated: bool = False,
    ):
        self.pot = pot
        self.simulated = simulated
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None

    @property
    def series_ohms(self) -> float:
        """The fixed resistor in series with the wiper, in the R17 path."""
        return getattr(self.pot, "series_ohms", DEFAULT_SERIES_OHMS)

    def start(self, force_position=None) -> SensitivityState:
        """Open the device and report where its wiper already is.

        Nothing is written unless ``force_position`` asks for it: the DS3502
        restores its own wiper from EEPROM at power-on, so whatever the user
        last set is already in effect and re-applying it would be busywork.

        Args:
            force_position: Apply this step instead of accepting the chip's
                own. Used by ``--sound-sensitivity-position``, which steers one
                run without committing to EEPROM.

        Raises:
            Exception: whatever the driver raises. Startup failure is the
                caller's to decide on — the server logs it and carries on
                without sensitivity control.
        """
        with self._lock:
            self.pot.open()
            if force_position is not None:
                self.pot.set_position(clamp_position(force_position))
            self._last_error = None
            position = self.pot.position
            logger.info(
                "[SENSITIVITY] Sound detector at step %s (~%.0f ohm R17, %s)",
                position,
                resistance_ohms(position, self.series_ohms) if position is not None else 0.0,
                "forced" if force_position is not None else "as found",
            )
            return self._state_locked()

    def set_position(self, value, *, store: bool = True) -> SensitivityState:
        """Move the wiper to ``value`` (clamped) and return the new state.

        Args:
            value: Requested step; clamped into range.
            store: Commit to the chip's EEPROM so the setting survives a power
                cycle. On by default because a UI change is a deliberate,
                human-paced act — one EEPROM cycle per adjustment is nowhere
                near the part's endurance, and it is what makes the setting
                stick with no file on the Pi.

        Raises:
            TypeError, ValueError: if ``value`` is not a usable position.
            Exception: whatever the driver raises if the wiper cannot be moved.
        """
        position = clamp_position(value)
        with self._lock:
            try:
                self.pot.set_position(position, store=store)
            except Exception as error:  # pylint: disable=broad-exception-caught
                self._last_error = str(error)
                logger.warning("[SENSITIVITY] Failed to move wiper to step %d: %s", position, error)
                raise
            self._last_error = None
            logger.info(
                "[SENSITIVITY] Sound detector at step %d (~%.0f ohm R17, preamp ~%.0f ohm)%s",
                position,
                resistance_ohms(position, self.series_ohms),
                preamp_feedback_ohms(position, self.series_ohms),
                ", stored" if store else "",
            )
            return self._state_locked()

    def state(self) -> SensitivityState:
        """Return the current state, reading the wiper back from the chip."""
        with self._lock:
            return self._state_locked()

    def stop(self) -> None:
        """Release the I2C bus. Safe to call more than once."""
        with self._lock:
            try:
                self.pot.close()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("[SENSITIVITY] Failed to close the digital pot", exc_info=True)

    def _state_locked(self) -> SensitivityState:
        try:
            position = self.pot.position
        except Exception as error:  # pylint: disable=broad-exception-caught
            # A bus error while reading must not take the Debug page down with
            # it; report the control as present but unreadable.
            self._last_error = str(error)
            logger.warning("[SENSITIVITY] Could not read the wiper back: %s", error)
            position = None
        series = self.series_ohms
        return SensitivityState(
            enabled=True,
            position=position,
            max_position=MAX_POSITION,
            default_position=position_for_resistance(DEFAULT_R17_OHMS, series),
            sensitivity_percent=None if position is None else sensitivity_percent(position),
            resistance_ohms=None if position is None else resistance_ohms(position, series),
            preamp_feedback_ohms=(
                None if position is None else preamp_feedback_ohms(position, series)
            ),
            series_ohms=series,
            simulated=self.simulated,
            error=self._last_error,
        )


def disabled_state(error: Optional[str] = None) -> SensitivityState:
    """Return the state the UI should render when there is no working digipot.

    ``error`` is None when no pot was asked for and carries the failure text
    when one was asked for and could not be brought up.
    """
    return SensitivityState(
        enabled=False,
        position=None,
        max_position=MAX_POSITION,
        default_position=position_for_resistance(DEFAULT_R17_OHMS),
        sensitivity_percent=None,
        resistance_ohms=None,
        preamp_feedback_ohms=None,
        series_ohms=DEFAULT_SERIES_OHMS,
        simulated=False,
        error=error,
    )
