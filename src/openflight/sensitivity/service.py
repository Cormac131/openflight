"""Runtime control of the SEN-14262 preamp gain via an X9C104 digital pot.

The service owns three things the driver deliberately does not: the lock that
keeps concurrent WebSocket handlers from corrupting the tracked wiper position,
the JSON file that remembers the setting across restarts, and the translation
between a tap index and the numbers a user reasons about (percent, ohms).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional, Protocol

from .config import CONFIG_PATH, load_position, save_position
from .models import SensitivityState
from .x9c104 import (
    MAX_POSITION,
    position_for_resistance,
    preamp_feedback_ohms,
    resistance_ohms,
    sensitivity_percent,
)

logger = logging.getLogger(__name__)

# The wiring guide's long-standing advice for a hand-soldered R17. Starting the
# digipot at the same resistance means an existing build behaves the same on
# the first boot after fitting the pot.
DEFAULT_R17_OHMS = 47_000.0
DEFAULT_POSITION = position_for_resistance(DEFAULT_R17_OHMS)


class DigitalPotentiometer(Protocol):  # pylint: disable=unnecessary-ellipsis
    """The wiper contract the service needs; see :class:`~.x9c104.X9C104`."""

    @property
    def position(self) -> Optional[int]:
        """Last commanded tap, or None when uncalibrated."""
        ...  # pylint: disable=unnecessary-ellipsis

    def open(self) -> None:
        """Claim the control lines."""
        ...  # pylint: disable=unnecessary-ellipsis

    def calibrate(self) -> int:
        """Drive the wiper to a known position and return it."""
        ...  # pylint: disable=unnecessary-ellipsis

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Step the wiper to ``position`` and return it."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the control lines."""
        ...  # pylint: disable=unnecessary-ellipsis


def clamp_position(value) -> int:
    """Coerce a UI-supplied wiper position into 0..99.

    Clamping rather than rejecting is deliberate: a slider that runs past the
    end should saturate, and the state echoed back to the UI carries the tap
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
    """Apply, persist, and report the sound detector's preamp sensitivity."""

    def __init__(
        self,
        pot: DigitalPotentiometer,
        *,
        config_path: Optional[Path] = None,
        default_position: int = DEFAULT_POSITION,
        simulated: bool = False,
    ):
        self.pot = pot
        # Resolved here rather than as a default argument so the module-level
        # CONFIG_PATH stays overridable (tests, and an XDG-relocated home).
        self.config_path = Path(config_path if config_path is not None else CONFIG_PATH)
        self.default_position = clamp_position(default_position)
        self.simulated = simulated
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None

    def start(self, force_position=None) -> SensitivityState:
        """Claim the pot, calibrate it, and apply a tap.

        Calibration is not optional: the X9C104 restores an unknown wiper
        position from its own NVM at power-on, so stepping without first
        driving to a known end would leave the resistance anywhere.

        Args:
            force_position: Apply this tap instead of consulting the saved
                setting. Used by ``--sound-sensitivity-position``, which
                overrides the file for one run without rewriting it — a startup
                flag that silently changed what the UI saved would be a
                surprise the next time the server came up without it.

        Raises:
            Exception: whatever the driver raises. Startup failure is the
                caller's to decide on — the server logs it and carries on
                without sensitivity control.
        """
        with self._lock:
            self.pot.open()
            self.pot.calibrate()
            if force_position is not None:
                target = clamp_position(force_position)
                source = "forced"
            else:
                target = load_position(self.config_path)
                source = "saved"
                if target is None:
                    target = self.default_position
                    source = "default"
            self.pot.set_position(target)
            self._last_error = None
            logger.info(
                "[SENSITIVITY] Sound detector at position %d (~%.0f ohm R17, %s)",
                target,
                resistance_ohms(target),
                source,
            )
            return self._state_locked()

    def set_position(self, value) -> SensitivityState:
        """Move the wiper to ``value`` (clamped) and remember it.

        A failed save is reported in the returned state but does not undo the
        move: the detector really is at the new sensitivity, and pretending
        otherwise would be the more confusing lie.

        Raises:
            TypeError, ValueError: if ``value`` is not a usable position.
            Exception: whatever the driver raises if the wiper cannot be moved.
        """
        position = clamp_position(value)
        with self._lock:
            try:
                self.pot.set_position(position)
            except Exception as error:  # pylint: disable=broad-exception-caught
                self._last_error = str(error)
                logger.warning(
                    "[SENSITIVITY] Failed to move wiper to position %d: %s", position, error
                )
                raise
            self._last_error = None
            try:
                save_position(position, self.config_path)
            except OSError as error:
                self._last_error = f"Sensitivity applied but not saved: {error}"
                logger.warning("[SENSITIVITY] Could not save %s: %s", self.config_path, error)
            logger.info(
                "[SENSITIVITY] Sound detector at position %d (~%.0f ohm R17, preamp ~%.0f ohm)",
                position,
                resistance_ohms(position),
                preamp_feedback_ohms(position),
            )
            return self._state_locked()

    def recalibrate(self) -> SensitivityState:
        """Re-home the wiper at 0 and re-apply the current position.

        The tracked position is a Python-side model of a chip that cannot be
        read back, so it can drift if the pot browns out or a line glitches.
        This is the manual resync.
        """
        with self._lock:
            target = self.pot.position
            if target is None:
                target = load_position(self.config_path)
            if target is None:
                target = self.default_position
            self.pot.calibrate()
            self.pot.set_position(target)
            self._last_error = None
            logger.info("[SENSITIVITY] Recalibrated, wiper restored to position %d", target)
            return self._state_locked()

    def state(self) -> SensitivityState:
        """Return the current state without touching the hardware."""
        with self._lock:
            return self._state_locked()

    def stop(self) -> None:
        """Release the GPIO lines. Safe to call more than once."""
        with self._lock:
            try:
                self.pot.close()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("[SENSITIVITY] Failed to close the digital pot", exc_info=True)

    def _state_locked(self) -> SensitivityState:
        position = self.pot.position
        return SensitivityState(
            enabled=True,
            position=position,
            max_position=MAX_POSITION,
            default_position=self.default_position,
            sensitivity_percent=None if position is None else sensitivity_percent(position),
            resistance_ohms=None if position is None else resistance_ohms(position),
            preamp_feedback_ohms=None if position is None else preamp_feedback_ohms(position),
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
        default_position=DEFAULT_POSITION,
        sensitivity_percent=None,
        resistance_ohms=None,
        preamp_feedback_ohms=None,
        simulated=False,
        error=error,
    )
