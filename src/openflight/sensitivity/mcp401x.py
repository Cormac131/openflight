"""MCP4017/18/19 I2C digital potentiometer driver for the R17 gain pad.

The preferred part for this build. At 100 kOhm end-to-end it spans R17's whole
operating range with no series resistor, which the 10 kOhm DS3502 cannot do —
and that also gives the closed gain loop enough travel to be worth running.

Its I2C protocol is about as simple as I2C gets. There is no register address:
a write is the slave address followed by one data byte, and a read is the slave
address followed by one data byte back. Only the low 7 bits matter; the
datasheet calls the MSb "a don't care since the wiper register is only 7-bits
wide".

Two consequences of the part's design shape everything here:

* **The wiper is volatile.** There is no EEPROM. The datasheet specifies a
  "Power-on Default Wiper Setting (Mid-scale)", so an unconfigured part comes
  up at step 64 every time. :data:`PERSISTS_IN_HARDWARE` is False, and the
  service keeps the setting in a file and re-applies it at startup.
* **The address is fixed at 0x2f.** There are no address pins, so exactly one
  of these can share a bus.

**Buy the MCP4017, not the MCP4018.** They share this driver, this address and
this protocol, but not their terminal topology. Quoting the datasheet: "The
MCP4017 is a true rheostat, with terminal B and the wiper (W) of the variable
resistor available on pins. The MCP4018 device offers a voltage divider
(potentiometer) with terminal B internally connected to ground." R17 sits in
the preamp's feedback path, where neither end is at ground — so an MCP4018 or
MCP4019 would drag that node toward ground through its ladder. Only the
MCP4017's floating pair is usable here.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol

from .potentiometer import (
    ResistanceModel,
    position_for_resistance as _position_for_resistance,
    preamp_feedback_ohms as _preamp_feedback_ohms,
    resistance_ohms as _resistance_ohms,
    sensitivity_percent as _sensitivity_percent,
)

logger = logging.getLogger(__name__)

# 7-bit wiper: 127 resistors, 128 steps.
POSITION_COUNT = 128
MAX_POSITION = POSITION_COUNT - 1

# The -104 suffix. The family also ships 5k, 10k and 50k; pass end_to_end_ohms
# if you fitted one of those.
DEFAULT_END_TO_END_OHMS = 100_000.0

# "Low Wiper Resistance: 100 Ohm (typical)" -- what step 0 measures.
DEFAULT_WIPER_OHMS = 100.0

# A 100k part reaches R17's range unaided, so unlike the DS3502 there is
# normally nothing in series with it.
DEFAULT_SERIES_OHMS = 0.0

# Fixed by the silicon: slave address 0101111. No address pins.
DEFAULT_ADDRESS = 0x2F

# The datasheet's power-on default, and therefore what an unconfigured part
# reads back as.
POWER_ON_POSITION = POSITION_COUNT // 2

#: The wiper lives in RAM only. The service persists it instead.
PERSISTS_IN_HARDWARE = False


class SMBusLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of smbus2 used by the driver, allowing deterministic tests."""

    def read_byte(self, address: int) -> int:
        """Read one byte with no register address."""
        ...  # pylint: disable=unnecessary-ellipsis

    def write_byte(self, address: int, value: int) -> None:
        """Write one byte with no register address."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


def _constants(series_ohms, end_to_end_ohms, wiper_ohms) -> dict:
    return {
        "max_position": MAX_POSITION,
        "series_ohms": series_ohms,
        "wiper_ohms": wiper_ohms,
        "end_to_end_ohms": end_to_end_ohms,
    }


def resistance_ohms(
    position: int,
    series_ohms: float = DEFAULT_SERIES_OHMS,
    *,
    end_to_end_ohms: float = DEFAULT_END_TO_END_OHMS,
    wiper_ohms: float = DEFAULT_WIPER_OHMS,
) -> float:
    """Return what R17 presents at ``position``."""
    _validate_position(position)
    return _resistance_ohms(position, **_constants(series_ohms, end_to_end_ohms, wiper_ohms))


def preamp_feedback_ohms(
    position: int,
    series_ohms: float = DEFAULT_SERIES_OHMS,
    *,
    end_to_end_ohms: float = DEFAULT_END_TO_END_OHMS,
    wiper_ohms: float = DEFAULT_WIPER_OHMS,
) -> float:
    """Return the SEN-14262 preamp feedback resistance at ``position``."""
    return _preamp_feedback_ohms(
        resistance_ohms(
            position, series_ohms, end_to_end_ohms=end_to_end_ohms, wiper_ohms=wiper_ohms
        )
    )


def sensitivity_percent(position: int) -> float:
    """Return ``position`` as a 0-100% share of the wiper's travel."""
    _validate_position(position)
    return _sensitivity_percent(position, MAX_POSITION)


def position_for_resistance(
    ohms: float,
    series_ohms: float = DEFAULT_SERIES_OHMS,
    *,
    end_to_end_ohms: float = DEFAULT_END_TO_END_OHMS,
    wiper_ohms: float = DEFAULT_WIPER_OHMS,
) -> int:
    """Return the step whose R17 resistance is closest to ``ohms``. Clamps."""
    return _position_for_resistance(ohms, **_constants(series_ohms, end_to_end_ohms, wiper_ohms))


def _validate_position(position: int) -> None:
    if not isinstance(position, int) or isinstance(position, bool):
        raise TypeError(f"position must be an int, got {type(position).__name__}")
    if not 0 <= position <= MAX_POSITION:
        raise ValueError(f"position must be within 0..{MAX_POSITION}, got {position}")


def validate_address(address: int) -> int:
    """Return ``address`` if the part can actually answer to it.

    Raises:
        ValueError: for anything but 0x2f. The family has no address pins, so
            an override here would only ever talk to a different device.
    """
    if address != DEFAULT_ADDRESS:
        raise ValueError(
            f"MCP401X address is fixed at 0x{DEFAULT_ADDRESS:02x} (no address pins), "
            f"got 0x{address:02x}"
        )
    return address


class MCP401X(ResistanceModel):
    """Wiper control for one MCP4017/18/19 on an I2C bus.

    Not thread-safe; :class:`SoundSensitivityService` owns the lock.
    """

    persists_in_hardware = PERSISTS_IN_HARDWARE
    max_position = MAX_POSITION

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        series_ohms: float = DEFAULT_SERIES_OHMS,
        end_to_end_ohms: float = DEFAULT_END_TO_END_OHMS,
        wiper_ohms: float = DEFAULT_WIPER_OHMS,
        bus: Optional[SMBusLike] = None,
    ):
        self.bus_number = bus_number
        self.address = validate_address(address)
        self.series_ohms = series_ohms
        self.end_to_end_ohms = end_to_end_ohms
        self.wiper_ohms = wiper_ohms
        self._bus = bus
        self._owns_bus = bus is None
        self._ready = False

    @property
    def is_open(self) -> bool:
        """True once the device has been opened and answered."""
        return self._ready

    def open(self) -> None:
        """Open the bus and confirm the part answers. Idempotent.

        There is no configuration register to write, so this reads the wiper
        back as its presence check.

        Raises:
            RuntimeError: if the bus or the device cannot be reached.
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
            self._bus.read_byte(self.address)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.close()
            raise RuntimeError(
                f"No MCP401X responding at 0x{self.address:02x} on I2C bus "
                f"{self.bus_number} ({error}). Check wiring and 'i2cdetect -y "
                f"{self.bus_number}'; this family has no address pins, so 0x"
                f"{DEFAULT_ADDRESS:02x} is the only address it can use."
            ) from error
        self._ready = True
        logger.info(
            "[SENSITIVITY] MCP401X ready at 0x%02x on i2c-%d (%.0f ohm end to end)",
            self.address,
            self.bus_number,
            self.end_to_end_ohms,
        )

    @property
    def position(self) -> Optional[int]:
        """The live wiper step read back from the chip, or None if closed."""
        if not self._ready or self._bus is None:
            return None
        # Only the low 7 bits are the wiper; the MSb is a don't care.
        return self._bus.read_byte(self.address) & MAX_POSITION

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Write the wiper register and return the step.

        Args:
            position: Target step, 0..127.
            store: Accepted for interface compatibility and ignored — this part
                has no non-volatile memory. The service persists the setting
                itself; see :data:`PERSISTS_IN_HARDWARE`.

        Raises:
            TypeError, ValueError: if ``position`` is not a usable step.
            RuntimeError: if the bus is not open.
        """
        _validate_position(position)
        if not self._ready or self._bus is None:
            raise RuntimeError("MCP401X bus is not open; call open() first")
        if store:
            logger.debug("[SENSITIVITY] MCP401X has no EEPROM; ignoring store request")
        self._bus.write_byte(self.address, position)
        logger.debug(
            "[SENSITIVITY] MCP401X wiper at %d (~%.0f ohm R17)",
            position,
            resistance_ohms(
                position,
                self.series_ohms,
                end_to_end_ohms=self.end_to_end_ohms,
                wiper_ohms=self.wiper_ohms,
            ),
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


class MockMCP401X(ResistanceModel):
    """In-memory stand-in used by ``--mock``, volatile like the real part."""

    persists_in_hardware = PERSISTS_IN_HARDWARE
    max_position = MAX_POSITION

    def __init__(self, *, series_ohms: float = DEFAULT_SERIES_OHMS, **kwargs):
        self.series_ohms = series_ohms
        self.end_to_end_ohms = kwargs.get("end_to_end_ohms", DEFAULT_END_TO_END_OHMS)
        self.wiper_ohms = kwargs.get("wiper_ohms", DEFAULT_WIPER_OHMS)
        self._position: Optional[int] = None

    @property
    def is_open(self) -> bool:
        """True while the mock is 'open'."""
        return self._position is not None

    def open(self) -> None:
        """Come up at mid-scale, as the datasheet says the real part does."""
        if self._position is None:
            self._position = POWER_ON_POSITION

    @property
    def position(self) -> Optional[int]:
        """The current wiper step."""
        return self._position

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Record ``position`` after the same validation the real chip gets."""
        _validate_position(position)
        del store
        self._position = position
        return position

    def close(self) -> None:
        """Forget the wiper, as a power cycle would."""
        self._position = None
