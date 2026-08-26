"""X9C104 digital potentiometer driver for the sound detector's R17 gain pad.

The SEN-14262 sets its preamp gain with R3 (100 kOhm, surface mount) and the
empty ``R17`` pad wired in parallel with it. Soldering a fixed resistor into
R17 lowers the parallel resistance, which lowers the gain, which makes the
detector less sensitive. Fitting an X9C104 there instead makes that resistance
software-controlled, so sensitivity can be tuned from the UI rather than with
a soldering iron.

Protocol (Renesas/Intersil X9C104 datasheet)::

    CS   ‾‾‾\\______________________________/‾‾‾
    U/D  ---<  direction, set before CS falls  >---
    INC  ‾‾‾‾‾‾\\__/‾‾\\__/‾‾\\__/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾

* ``U/D`` high steps the wiper toward ``RH`` (more resistance), low toward
  ``RL`` (less resistance).
* The wiper moves on each **falling** edge of ``INC`` while ``CS`` is low.
* Raising ``CS`` while ``INC`` is **high** commits the wiper position to the
  chip's non-volatile memory; raising it while ``INC`` is **low** does not.
  OpenFlight leaves the NVM alone (see ``store`` below).

The chip has no readback path, so the wiper position after power-on is
whatever was last committed to NVM — unknowable from software. :meth:`calibrate`
resolves that by driving more decrements than there are taps: the wiper
saturates at ``RL`` and position 0 becomes a known state.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)

# The X9C104 is a 99-element array with 100 tap points (0..99). Position 0 puts
# the wiper at RL, position 99 at RH.
TAP_COUNT = 100
MAX_POSITION = TAP_COUNT - 1

# End-to-end resistance of the -104 variant, and the wiper's own series
# resistance, which is what you measure at position 0 rather than a dead short.
END_TO_END_OHMS = 100_000.0
WIPER_OHMS = 40.0

# The surface-mount feedback resistor R17 sits in parallel with on the
# SEN-14262. Only used to report the resulting preamp resistance.
SEN14262_R3_OHMS = 100_000.0

# tIL/tIH (INC low/high period) are 1us minimums; tCI (CS setup) is 100ns. A
# 50us half-period clears all of them by orders of magnitude and still sweeps
# the full 100 taps in ~10ms, which is imperceptible behind a UI slider.
DEFAULT_STEP_DELAY_S = 50e-6

# tCPH: the NVM store cycle needs 10ms typical, 20ms max, before the chip
# accepts another instruction.
DEFAULT_STORE_DELAY_S = 0.025


class DigitalOutput(Protocol):  # pylint: disable=unnecessary-ellipsis
    """The slice of ``gpiozero.DigitalOutputDevice`` this driver needs."""

    def on(self) -> None:
        """Drive the line high."""
        ...  # pylint: disable=unnecessary-ellipsis

    def off(self) -> None:
        """Drive the line low."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the line."""
        ...  # pylint: disable=unnecessary-ellipsis


OutputFactory = Callable[[int], DigitalOutput]


def resistance_ohms(position: int) -> float:
    """Return the wiper-to-RL resistance, i.e. what R17 measures.

    Position 0 is the wiper resistance alone; each further tap adds one of the
    99 resistive elements.
    """
    _validate_position(position)
    return WIPER_OHMS + END_TO_END_OHMS * position / MAX_POSITION


def preamp_feedback_ohms(position: int) -> float:
    """Return the SEN-14262 preamp feedback resistance at ``position``.

    That is the digipot in parallel with the board's 100 kOhm R3. Lower means
    less gain, so a lower wiper position is a *less* sensitive detector.
    """
    pot_ohms = resistance_ohms(position)
    return pot_ohms * SEN14262_R3_OHMS / (pot_ohms + SEN14262_R3_OHMS)


def sensitivity_percent(position: int) -> float:
    """Return ``position`` as a 0-100% share of the wiper's travel."""
    _validate_position(position)
    return 100.0 * position / MAX_POSITION


def position_for_resistance(ohms: float) -> int:
    """Return the tap whose resistance is closest to ``ohms``.

    Lets the setup guides keep talking in resistor values ("start at 47k")
    while the UI works in taps.
    """
    raw = round((ohms - WIPER_OHMS) * MAX_POSITION / END_TO_END_OHMS)
    return max(0, min(MAX_POSITION, int(raw)))


def _validate_position(position: int) -> None:
    if not isinstance(position, int) or isinstance(position, bool):
        raise TypeError(f"position must be an int, got {type(position).__name__}")
    if not 0 <= position <= MAX_POSITION:
        raise ValueError(f"position must be within 0..{MAX_POSITION}, got {position}")


def _gpiozero_output_factory(pin: int) -> DigitalOutput:
    """Build a gpiozero output line, idle high, on the Pi 5-safe pin factory."""
    from gpiozero import (  # pylint: disable=import-error,import-outside-toplevel
        DigitalOutputDevice,
    )

    # Must precede the first gpiozero device: on a Pi 5 gpiozero's own pin
    # factory auto-detection fails outright. See gpio_factory.
    from ..gpio_factory import (  # pylint: disable=import-outside-toplevel
        ensure_lgpio_pin_factory,
    )

    ensure_lgpio_pin_factory()
    return DigitalOutputDevice(pin, initial_value=True)


class X9C104:
    """Three-wire driver for one X9C104 digital potentiometer.

    Not thread-safe on its own: the wiper position is tracked in Python and a
    concurrent move would corrupt it. :class:`SoundSensitivityService` owns the
    lock.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        cs_pin: int,
        inc_pin: int,
        ud_pin: int,
        output_factory: Optional[OutputFactory] = None,
        step_delay_s: float = DEFAULT_STEP_DELAY_S,
        store_delay_s: float = DEFAULT_STORE_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cs_pin = cs_pin
        self.inc_pin = inc_pin
        self.ud_pin = ud_pin
        self.step_delay_s = step_delay_s
        self.store_delay_s = store_delay_s
        self._output_factory = output_factory or _gpiozero_output_factory
        self._sleep = sleep
        self._cs: Optional[DigitalOutput] = None
        self._inc: Optional[DigitalOutput] = None
        self._ud: Optional[DigitalOutput] = None
        self._position: Optional[int] = None

    @property
    def position(self) -> Optional[int]:
        """Last commanded tap, or None until :meth:`calibrate` has run."""
        return self._position

    @property
    def is_open(self) -> bool:
        """True once the GPIO lines are claimed."""
        return self._cs is not None

    def open(self) -> None:
        """Claim the three GPIO lines. Idempotent.

        Every line idles high: CS high leaves the chip deselected, and INC high
        means the next :meth:`set_position` starts from a clean falling edge.

        Raises:
            RuntimeError: if a line cannot be claimed. Any line opened before
                the failure is released, so a retry starts from a clean slate.
        """
        if self._cs is not None:
            return
        opened: list[DigitalOutput] = []
        try:
            for pin in (self.cs_pin, self.inc_pin, self.ud_pin):
                opened.append(self._output_factory(pin))
        except Exception as error:  # pylint: disable=broad-exception-caught
            for line in opened:
                _close_quietly(line)
            raise RuntimeError(
                f"Could not claim X9C104 GPIO lines "
                f"(CS=BCM{self.cs_pin}, INC=BCM{self.inc_pin}, U/D=BCM{self.ud_pin}): {error}"
            ) from error
        self._cs, self._inc, self._ud = opened[0], opened[1], opened[2]
        logger.info(
            "[SENSITIVITY] X9C104 lines claimed (CS=BCM%d, INC=BCM%d, U/D=BCM%d)",
            self.cs_pin,
            self.inc_pin,
            self.ud_pin,
        )

    def calibrate(self) -> int:
        """Drive the wiper to a known position 0 and return it.

        The chip cannot be read back, so this issues one more decrement than
        there are taps. Extra decrements at the RL end are no-ops, so the wiper
        lands on 0 from wherever NVM left it at power-on.
        """
        self.open()
        self._run_cycle(steps=TAP_COUNT, increment=False, store=False)
        self._position = 0
        logger.info("[SENSITIVITY] X9C104 calibrated to position 0 (wiper at RL)")
        return 0

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Step the wiper to ``position`` and return it.

        Args:
            position: Target tap, 0..99.
            store: Commit the position to the chip's NVM so it survives a power
                cycle. Off by default: the NVM is rated for 100k stores and
                OpenFlight re-applies the saved position at startup anyway, so
                writing it on every slider move would burn the part for nothing.

        Raises:
            TypeError: if ``position`` is not an int.
            ValueError: if ``position`` is outside 0..99.
        """
        _validate_position(position)
        if self._position is None:
            self.calibrate()
        assert self._position is not None  # calibrate() always sets it
        delta = position - self._position
        if delta == 0 and not store:
            return position
        self._run_cycle(steps=abs(delta), increment=delta > 0, store=store)
        self._position = position
        logger.debug(
            "[SENSITIVITY] X9C104 wiper at position %d (~%.0f ohm)%s",
            position,
            resistance_ohms(position),
            ", stored to NVM" if store else "",
        )
        return position

    def close(self) -> None:
        """Release the GPIO lines. Idempotent, and safe to call after a failure."""
        for line in (self._cs, self._inc, self._ud):
            if line is not None:
                _close_quietly(line)
        self._cs = self._inc = self._ud = None
        self._position = None

    def _run_cycle(self, *, steps: int, increment: bool, store: bool) -> None:
        """Emit one CS-low instruction: ``steps`` wiper moves, then deselect."""
        self.open()
        cs, inc, ud = self._cs, self._inc, self._ud
        assert cs is not None and inc is not None and ud is not None  # open() guarantees

        # U/D must be settled before CS falls (tDI); INC starts high so the
        # first pulse below is a clean falling edge.
        (ud.on if increment else ud.off)()
        inc.on()
        cs.off()
        self._sleep(self.step_delay_s)

        for step in range(steps):
            inc.off()  # falling edge: the wiper moves here
            self._sleep(self.step_delay_s)
            if step < steps - 1:
                inc.on()
                self._sleep(self.step_delay_s)

        if store:
            # CS rising while INC is high starts the NVM write.
            inc.on()
            self._sleep(self.step_delay_s)
            cs.on()
            self._sleep(self.store_delay_s)
        else:
            # CS rising while INC is low discards the write, leaving the wiper
            # where it is. INC only returns to idle once CS is safely high.
            cs.on()
            self._sleep(self.step_delay_s)
            inc.on()


class MockX9C104:
    """In-memory stand-in used by ``--mock`` so the UI control works off-Pi."""

    def __init__(self, **_kwargs):
        self._position: Optional[int] = None
        self.closed = False

    @property
    def position(self) -> Optional[int]:
        """Last commanded tap, or None until :meth:`calibrate` has run."""
        return self._position

    @property
    def is_open(self) -> bool:
        """True unless :meth:`close` has been called."""
        return not self.closed

    def open(self) -> None:
        """No hardware to claim."""

    def calibrate(self) -> int:
        """Pretend to drive the wiper to the RL end."""
        self._position = 0
        return 0

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Record ``position`` after the same validation the real chip gets."""
        _validate_position(position)
        del store
        self._position = position
        return position

    def close(self) -> None:
        """Mark the mock closed."""
        self.closed = True
        self._position = None


def _close_quietly(line: DigitalOutput) -> None:
    try:
        line.close()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("[SENSITIVITY] Failed to close a X9C104 GPIO line", exc_info=True)
