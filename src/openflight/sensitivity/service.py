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
from pathlib import Path
from typing import Optional

from .autogain import AutoGainController
from .config import CONFIG_PATH, load_position, save_position
from .envelope import EnvelopeMonitor
from .models import SensitivityState
from .potentiometer import DigitalPotentiometer

logger = logging.getLogger(__name__)

# The wiring guide's long-standing advice for a hand-soldered R17.
DEFAULT_R17_OHMS = 47_000.0


def clamp_position(value, max_position: int = 127) -> int:
    """Coerce a UI-supplied wiper position into range.

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
    return max(0, min(max_position, position))


class SoundSensitivityService:
    """Apply and report the sound detector's preamp sensitivity."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        pot: DigitalPotentiometer,
        *,
        simulated: bool = False,
        envelope: Optional[EnvelopeMonitor] = None,
        controller: Optional[AutoGainController] = None,
        auto_enabled: bool = False,
        config_path: Optional[Path] = None,
    ):
        self.pot = pot
        # Resolved here rather than as a default argument so the module-level
        # CONFIG_PATH stays overridable.
        self.config_path = Path(config_path if config_path is not None else CONFIG_PATH)
        self.simulated = simulated
        self.envelope = envelope
        self.controller = controller
        # Auto-gain needs an envelope to measure and a controller to decide;
        # asking for it without both is a configuration error, not a mode.
        self._auto_enabled = auto_enabled and envelope is not None and controller is not None
        if controller is not None and getattr(controller, "model", None) is None:
            # Give the controller the fitted pot's own resistance model, so its
            # corrections and its authority warning are about the real part.
            controller.model = pot
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        self._last_decision = None
        self._last_peak = None

    @property
    def auto_available(self) -> bool:
        """True when an envelope monitor and controller are both present."""
        return self.envelope is not None and self.controller is not None

    @property
    def auto_enabled(self) -> bool:
        """True when the loop is allowed to move the wiper."""
        return self._auto_enabled

    def set_auto_enabled(self, enabled: bool) -> SensitivityState:
        """Turn the closed loop on or off.

        Raises:
            RuntimeError: if asked to enable it without the hardware for it.
        """
        if enabled and not self.auto_available:
            raise RuntimeError(
                "Auto gain needs the envelope ADC; start the server with --sound-sensitivity-auto"
            )
        with self._lock:
            self._auto_enabled = bool(enabled)
            if self.controller is not None:
                # Whatever was learned before is about a gain the user may
                # since have overridden by hand.
                self.controller.reset()
            logger.info("[SENSITIVITY] Auto gain %s", "enabled" if enabled else "disabled")
            return self._state_locked()

    def observe_shot(self, impact_timestamp: float):
        """Fold one shot's envelope peak into the loop and act on it.

        Returns the decision taken, or None when there is nothing to act on --
        no auto-gain configured, the loop switched off, or no envelope samples
        around that impact. Never raises into the shot pipeline: a sensitivity
        adjustment failing must not cost the user their shot.
        """
        if not self._auto_enabled or self.envelope is None or self.controller is None:
            return None
        try:
            peak = self.envelope.peak_for_impact(impact_timestamp)
            if peak is None:
                return None
            with self._lock:
                self._last_peak = peak
                position = self.pot.position
                if position is None:
                    return None
                decision = self.controller.observe(
                    peak.fraction_of_full_scale, position, clipped=peak.clipped
                )
                self._last_decision = decision
                if decision.changed:
                    # Volatile: at an EEPROM write per adjustment the part's
                    # endurance would not last a season. The controller asks
                    # for a commit separately, once a setting has settled.
                    self.pot.set_position(decision.next_position, store=False)
                elif decision.commit:
                    self.pot.set_position(decision.position, store=True)
                logger.info("[SENSITIVITY] Auto gain: %s", decision.reason)
                return decision
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[SENSITIVITY] Auto gain step failed: %s", error, exc_info=True)
            with self._lock:
                self._last_error = str(error)
            return None

    @property
    def series_ohms(self) -> float:
        """The fixed resistor in series with the wiper, in the R17 path."""
        return getattr(self.pot, "series_ohms", 0.0)

    @property
    def persists_in_hardware(self) -> bool:
        """True when the pot remembers its own wiper across a power cycle."""
        return bool(getattr(self.pot, "persists_in_hardware", False))

    def start(self, force_position=None) -> SensitivityState:
        """Open the device and report where its wiper already is.

        A pot that keeps its own wiper is left alone: whatever the user last
        set is already in effect, and re-applying it would be busywork. A
        volatile one comes up at mid-scale, so the saved setting is restored
        here instead.

        Args:
            force_position: Apply this step instead of accepting the chip's
                own. Used by ``--sound-sensitivity-position``, which steers one
                run without committing to EEPROM.

        Raises:
            Exception: whatever the driver raises. Startup failure is the
                caller's to decide on — the server logs it and carries on
                without sensitivity control.
        """
        if self.envelope is not None:
            self.envelope.start()
        with self._lock:
            self.pot.open()
            source = "as found"
            target = None
            if force_position is not None:
                target = clamp_position(force_position, self.pot.max_position)
                source = "forced"
            elif not self.persists_in_hardware:
                saved = load_position(self.config_path, max_position=self.pot.max_position)
                if saved is not None:
                    target = saved
                    source = "restored"
            if target is not None:
                self.pot.set_position(target)
            self._last_error = None
            position = self.pot.position
            logger.info(
                "[SENSITIVITY] Sound detector at step %s (~%.0f ohm R17, %s)",
                position,
                self.pot.resistance_at(position) if position is not None else 0.0,
                source,
            )
            return self._state_locked()

    def set_position(self, value, *, store: bool = True) -> SensitivityState:
        """Move the wiper to ``value`` (clamped) and return the new state.

        Args:
            value: Requested step; clamped into range.
            store: Make the setting survive a power cycle — in the chip's own
                EEPROM where it has one, in a file on the Pi where it does not.
                On by default because a UI change is a deliberate, human-paced
                act, and one write per adjustment is nowhere near any part's
                endurance.

        Raises:
            TypeError, ValueError: if ``value`` is not a usable position.
            Exception: whatever the driver raises if the wiper cannot be moved.
        """
        position = clamp_position(value, self.pot.max_position)
        with self._lock:
            try:
                self.pot.set_position(position, store=store)
            except Exception as error:  # pylint: disable=broad-exception-caught
                self._last_error = str(error)
                logger.warning("[SENSITIVITY] Failed to move wiper to step %d: %s", position, error)
                raise
            self._last_error = None
            if store and not self.persists_in_hardware:
                try:
                    save_position(position, self.config_path)
                except OSError as error:
                    # The wiper really did move; saying otherwise would be the
                    # more confusing lie.
                    self._last_error = f"Sensitivity applied but not saved: {error}"
                    logger.warning("[SENSITIVITY] Could not save %s: %s", self.config_path, error)
            logger.info(
                "[SENSITIVITY] Sound detector at step %d (~%.0f ohm R17, preamp ~%.0f ohm)%s",
                position,
                self.pot.resistance_at(position),
                self.pot.preamp_at(position),
                ", stored" if store else "",
            )
            return self._state_locked()

    def state(self) -> SensitivityState:
        """Return the current state, reading the wiper back from the chip."""
        with self._lock:
            return self._state_locked()

    def stop(self) -> None:
        """Release the I2C bus and stop sampling. Safe to call more than once."""
        if self.envelope is not None:
            try:
                self.envelope.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("[SENSITIVITY] Failed to stop the envelope monitor", exc_info=True)
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
        return SensitivityState(
            enabled=True,
            position=position,
            max_position=self.pot.max_position,
            default_position=self.pot.step_for_resistance(DEFAULT_R17_OHMS),
            sensitivity_percent=None if position is None else self.pot.percent_at(position),
            resistance_ohms=None if position is None else self.pot.resistance_at(position),
            preamp_feedback_ohms=None if position is None else self.pot.preamp_at(position),
            series_ohms=self.series_ohms,
            simulated=self.simulated,
            auto_available=self.auto_available,
            auto_enabled=self._auto_enabled,
            last_peak=self._last_peak.to_dict() if self._last_peak is not None else None,
            last_decision=(
                self._last_decision.to_dict() if self._last_decision is not None else None
            ),
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
        max_position=127,
        default_position=0,
        sensitivity_percent=None,
        resistance_ohms=None,
        preamp_feedback_ohms=None,
        series_ohms=0.0,
        simulated=False,
        auto_available=False,
        auto_enabled=False,
        last_peak=None,
        last_decision=None,
        error=error,
    )
