"""DS3502 I2C digital potentiometer driver for the sound detector's R17 gain.

The SEN-14262 sets preamp gain with R3 (100 kOhm) and whatever is fitted to the
empty ``R17`` pad in parallel with it. A DS3502 there makes that resistance
software-controlled, so sensitivity is a UI slider rather than a soldering job.

Two properties of this part shape the design, and both are improvements on the
three-wire pots it replaces:

* **The wiper reads back.** Register 0x00 returns the live position, so there is
  no calibration dance and no Python-side model of the hardware to drift out of
  sync with it.
* **The wiper is non-volatile.** Writes can optionally commit to EEPROM, so the
  chip itself remembers the setting across a power cycle. Nothing on the Pi has
  to re-apply it at startup.

**The DS3502 is 10 kOhm end-to-end, which cannot reach R17's operating point on
its own.** The wiring guide's recommended R17 is 47 kOhm; 10 kOhm in parallel
with R3 gives a 9.1 kOhm preamp leg against 32 kOhm at that baseline, so every
setting would be far less sensitive than the documented starting point. A fixed
series resistor shifts the 10 kOhm span up to where it is useful, and
``series_ohms`` must match the one actually fitted or every reported figure is
wrong. See :data:`DEFAULT_SERIES_OHMS`.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Protocol

from .potentiometer import (
    ResistanceModel,
    position_for_resistance as _position_for_resistance,
    preamp_feedback_ohms as _preamp_feedback_ohms,
    resistance_ohms as _resistance_ohms,
    sensitivity_percent as _sensitivity_percent,
)

logger = logging.getLogger(__name__)

# 7-bit wiper: 128 positions, 0..127.
POSITION_COUNT = 128
MAX_POSITION = POSITION_COUNT - 1

# End-to-end resistance of the DS3502.
END_TO_END_OHMS = 10_000.0

# Fixed resistor in series with the wiper, in the R17 path. 33k puts the bottom
# of the span on the wiring guide's "noisy environment" value and the top near
# its recommended 47k, so the adjustable range covers the documented span.
DEFAULT_SERIES_OHMS = 33_000.0

# The onboard SMD feedback resistor the R17 pad parallels.
SEN14262_R3_OHMS = 100_000.0

DEFAULT_ADDRESS = 0x28
# A0/A1 select one of four addresses.
ADDRESS_RANGE = (0x28, 0x2B)

REG_WIPER = 0x00
REG_CONTROL = 0x02

# Control register bit 7: 1 = writes land in the volatile wiper register only,
# 0 = they also commit to the EEPROM initial-value register.
CONTROL_VOLATILE_ONLY = 0x80

# EEPROM commit time before the part accepts another instruction.
EEPROM_WRITE_DELAY_S = 0.1


class SMBusLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of smbus2 used by the driver, allowing deterministic tests."""

    def read_byte_data(self, address: int, register: int) -> int:
        """Read one register byte."""
        ...  # pylint: disable=unnecessary-ellipsis

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        """Write one register byte."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


def _constants(series_ohms: float) -> dict:
    return {
        "max_position": MAX_POSITION,
        "series_ohms": series_ohms,
        # The DS3502's own wiper resistance is negligible beside the series
        # resistor this part needs, so it is not modelled separately.
        "wiper_ohms": 0.0,
        "end_to_end_ohms": END_TO_END_OHMS,
    }


def resistance_ohms(position: int, series_ohms: float = DEFAULT_SERIES_OHMS) -> float:
    """Return what R17 presents at ``position``: series resistor plus wiper."""
    _validate_position(position)
    return _resistance_ohms(position, **_constants(series_ohms))


def preamp_feedback_ohms(position: int, series_ohms: float = DEFAULT_SERIES_OHMS) -> float:
    """Return the SEN-14262 preamp feedback resistance at ``position``.

    R17 in parallel with the board's 100 kOhm R3. Lower means less gain, so a
    lower wiper position is a *less* sensitive detector.
    """
    return _preamp_feedback_ohms(resistance_ohms(position, series_ohms))


def sensitivity_percent(position: int) -> float:
    """Return ``position`` as a 0-100% share of the wiper's travel."""
    _validate_position(position)
    return _sensitivity_percent(position, MAX_POSITION)


def position_for_resistance(ohms: float, series_ohms: float = DEFAULT_SERIES_OHMS) -> int:
    """Return the wiper step whose R17 resistance is closest to ``ohms``. Clamps."""
    return _position_for_resistance(ohms, **_constants(series_ohms))


def _validate_position(position: int) -> None:
    if not isinstance(position, int) or isinstance(position, bool):
        raise TypeError(f"position must be an int, got {type(position).__name__}")
    if not 0 <= position <= MAX_POSITION:
        raise ValueError(f"position must be within 0..{MAX_POSITION}, got {position}")


def validate_address(address: int) -> int:
    """Return ``address`` if the A0/A1 jumpers can actually select it.

    Raises:
        ValueError: for an address outside 0x28..0x2b. A typo here would
            otherwise present as a bus error at an unrelated device.
    """
    low, high = ADDRESS_RANGE
    if not low <= address <= high:
        raise ValueError(
            f"DS3502 address must be within 0x{low:02x}..0x{high:02x}, got 0x{address:02x}"
        )
    return address


class DS3502(ResistanceModel):
    """Wiper control for one DS3502 on an I2C bus.

    Not thread-safe; :class:`SoundSensitivityService` owns the lock.
    """

    #: The wiper survives a power cycle in the chip's own EEPROM.
    persists_in_hardware = True
    max_position = MAX_POSITION
    wiper_ohms = 0.0
    end_to_end_ohms = END_TO_END_OHMS

    def __init__(
        self,
        *,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        series_ohms: float = DEFAULT_SERIES_OHMS,
        bus: Optional[SMBusLike] = None,
        sleep=time.sleep,
    ):
        self.bus_number = bus_number
        self.address = validate_address(address)
        self.series_ohms = series_ohms
        self._bus = bus
        self._owns_bus = bus is None
        self._sleep = sleep
        # Separate from holding a bus handle: a caller-injected bus is not a
        # configured device until open() has put the part in volatile mode.
        self._ready = False

    @property
    def is_open(self) -> bool:
        """True once the device has been opened and configured."""
        return self._ready

    def open(self) -> None:
        """Open the bus and put the part in volatile-write mode. Idempotent.

        Volatile mode is the default for ordinary moves: EEPROM endurance is
        finite, and :meth:`set_position` opts into a commit only when asked.

        Raises:
            RuntimeError: if the bus or the device cannot be reached — both
                actionable setup problems rather than something to retry past.
        """
        if self._bus is None:
            try:
                from smbus2 import SMBus  # pylint: disable=import-outside-toplevel,import-error

                self._bus = SMBus(self.bus_number)
            except Exception as error:  # pylint: disable=broad-exception-caught
                raise RuntimeError(
                    f"Could not open I2C bus {self.bus_number} ({error}). Enable I2C with "
                    "'sudo raspi-config' and check the user is in the 'i2c' group."
                ) from error
        try:
            self._bus.write_byte_data(self.address, REG_CONTROL, CONTROL_VOLATILE_ONLY)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.close()
            raise RuntimeError(
                f"No DS3502 responding at 0x{self.address:02x} on I2C bus "
                f"{self.bus_number} ({error}). Check wiring and 'i2cdetect -y "
                f"{self.bus_number}', and the A0/A1 address jumpers."
            ) from error
        self._ready = True
        logger.info(
            "[SENSITIVITY] DS3502 ready at 0x%02x on i2c-%d (series %.0f ohm)",
            self.address,
            self.bus_number,
            self.series_ohms,
        )

    @property
    def position(self) -> Optional[int]:
        """The live wiper position read back from the chip, or None if closed.

        Unlike the three-wire parts this replaced, the DS3502 reports its own
        wiper, so this is a measurement rather than a remembered value.
        """
        if not self._ready or self._bus is None:
            return None
        return self._bus.read_byte_data(self.address, REG_WIPER)

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Move the wiper to ``position`` and return it.

        Args:
            position: Target step, 0..127.
            store: Also commit to the chip's EEPROM, so the setting survives a
                power cycle. The commit costs ~100 ms and consumes one of a
                finite number of write cycles, so it is opt-in.

        Raises:
            TypeError: if ``position`` is not an int.
            ValueError: if ``position`` is outside 0..127.
            RuntimeError: if the bus is not open.
        """
        _validate_position(position)
        bus = self._require_bus()
        bus.write_byte_data(self.address, REG_WIPER, position)
        if store:
            # Clearing the volatile-only bit makes the *next* wiper write land
            # in EEPROM too, so the position is rewritten while it is clear.
            bus.write_byte_data(self.address, REG_CONTROL, 0x00)
            bus.write_byte_data(self.address, REG_WIPER, position)
            self._sleep(EEPROM_WRITE_DELAY_S)
            bus.write_byte_data(self.address, REG_CONTROL, CONTROL_VOLATILE_ONLY)
        logger.debug(
            "[SENSITIVITY] DS3502 wiper at %d (~%.0f ohm R17)%s",
            position,
            resistance_ohms(position, self.series_ohms),
            ", stored to EEPROM" if store else "",
        )
        return position

    def close(self) -> None:
        """Release the bus if this driver opened it. Idempotent."""
        self._ready = False
        bus, self._bus = self._bus, None
        if bus is not None and self._owns_bus:
            try:
                bus.close()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("[SENSITIVITY] Failed to close the I2C bus", exc_info=True)

    def _require_bus(self) -> SMBusLike:
        if not self._ready or self._bus is None:
            raise RuntimeError("DS3502 bus is not open; call open() first")
        return self._bus


class MockDS3502(ResistanceModel):
    """In-memory stand-in used by ``--mock`` so the UI control works off-Pi."""

    persists_in_hardware = True
    max_position = MAX_POSITION
    wiper_ohms = 0.0
    end_to_end_ohms = END_TO_END_OHMS

    def __init__(self, *, series_ohms: float = DEFAULT_SERIES_OHMS, **_kwargs):
        self.series_ohms = series_ohms
        self.stored: Optional[int] = None
        self._position: Optional[int] = None

    @property
    def is_open(self) -> bool:
        """True while the mock is 'open'."""
        return self._position is not None

    def open(self) -> None:
        """Come up at mid-scale, standing in for a chip's remembered wiper."""
        if self._position is None:
            self._position = self.stored if self.stored is not None else MAX_POSITION // 2

    @property
    def position(self) -> Optional[int]:
        """The remembered wiper position."""
        return self._position

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Record ``position`` after the same validation the real chip gets."""
        _validate_position(position)
        self._position = position
        if store:
            self.stored = position
        return position

    def close(self) -> None:
        """Forget the live wiper, keeping anything committed to 'EEPROM'."""
        self._position = None
