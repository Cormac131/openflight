"""ADS1115 driver for reading the SEN-14262 envelope output.

The detector's `GATE` output goes straight to the radar's `HOST_INT` and is a
bare digital "something loud happened". Its `ENVELOPE` output is the analogue
amplitude the preamp actually saw, which is what closed-loop gain control needs
— and the Pi has no analogue input, hence this ADC.

The part runs in **continuous** conversion mode rather than single-shot: an
impact envelope is a transient that peaks within a few milliseconds, and a
single-shot read started from a GPIO callback would routinely miss it. Free
running means :class:`~.envelope.EnvelopeMonitor` can keep a rolling history
and look *backwards* from the shot timestamp instead of racing it.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

DEFAULT_ADDRESS = 0x48
# The ADDR pin selects one of four addresses.
ADDRESS_RANGE = (0x48, 0x4B)

REG_CONVERSION = 0x00
REG_CONFIG = 0x01

# Config register fields (see the TI datasheet's register map).
CONFIG_MUX_OFFSET = 12
# MUX 100b..111b select AIN0..AIN3 against GND.
_SINGLE_ENDED_MUX = {0: 0x04, 1: 0x05, 2: 0x06, 3: 0x07}
CONFIG_MODE_CONTINUOUS = 0x0000
# Comparator disabled; we poll rather than use ALERT/RDY.
CONFIG_COMPARATOR_OFF = 0x0003

# Full-scale range per PGA setting, and its config bits.
PGA_SETTINGS = {
    6.144: 0x0000,
    4.096: 0x0200,
    2.048: 0x0400,
    1.024: 0x0600,
    0.512: 0x0800,
    0.256: 0x0A00,
}

DATA_RATES = {
    8: 0x0000,
    16: 0x0020,
    32: 0x0040,
    64: 0x0060,
    128: 0x0080,
    250: 0x00A0,
    475: 0x00C0,
    860: 0x00E0,
}

# 4.096 V covers a 3.3 V envelope with headroom while keeping resolution;
# 860 SPS is the fastest the part offers, and an impact envelope needs it.
DEFAULT_FULL_SCALE_VOLTS = 4.096
DEFAULT_DATA_RATE = 860

# Signed 16-bit conversion result.
_FULL_SCALE_COUNTS = 32767


class SMBusLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of smbus2 used by the driver, allowing deterministic tests."""

    def read_i2c_block_data(self, address: int, register: int, length: int) -> list:
        """Read a register block."""
        ...  # pylint: disable=unnecessary-ellipsis

    def write_i2c_block_data(self, address: int, register: int, data: list) -> None:
        """Write a register block."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


def validate_address(address: int) -> int:
    """Return ``address`` if the ADDR pin can actually select it.

    Raises:
        ValueError: for an address outside 0x48..0x4b.
    """
    low, high = ADDRESS_RANGE
    if not low <= address <= high:
        raise ValueError(
            f"ADS1115 address must be within 0x{low:02x}..0x{high:02x}, got 0x{address:02x}"
        )
    return address


def build_config(*, channel: int, full_scale_volts: float, data_rate: int) -> int:
    """Assemble the config register word for free-running single-ended reads.

    Raises:
        ValueError: for a channel, range, or rate the part does not offer.
    """
    if channel not in _SINGLE_ENDED_MUX:
        raise ValueError(f"channel must be 0..3, got {channel}")
    if full_scale_volts not in PGA_SETTINGS:
        raise ValueError(
            f"full_scale_volts must be one of {sorted(PGA_SETTINGS)}, got {full_scale_volts}"
        )
    if data_rate not in DATA_RATES:
        raise ValueError(f"data_rate must be one of {sorted(DATA_RATES)}, got {data_rate}")
    return (
        (_SINGLE_ENDED_MUX[channel] << CONFIG_MUX_OFFSET)
        | PGA_SETTINGS[full_scale_volts]
        | CONFIG_MODE_CONTINUOUS
        | DATA_RATES[data_rate]
        | CONFIG_COMPARATOR_OFF
    )


def counts_to_volts(counts: int, full_scale_volts: float) -> float:
    """Convert a signed conversion result to volts."""
    return counts * full_scale_volts / _FULL_SCALE_COUNTS


class ADS1115:
    """Free-running single-channel reads from an ADS1115."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        channel: int = 0,
        full_scale_volts: float = DEFAULT_FULL_SCALE_VOLTS,
        data_rate: int = DEFAULT_DATA_RATE,
        bus: Optional[SMBusLike] = None,
    ):
        self.bus_number = bus_number
        self.address = validate_address(address)
        self.channel = channel
        self.full_scale_volts = full_scale_volts
        self.data_rate = data_rate
        # Validate eagerly so a bad setting fails at construction rather than
        # on the first sample inside a background thread.
        self._config = build_config(
            channel=channel, full_scale_volts=full_scale_volts, data_rate=data_rate
        )
        self._bus = bus
        self._owns_bus = bus is None
        self._ready = False

    @property
    def is_open(self) -> bool:
        """True once the device has been opened and configured."""
        return self._ready

    def open(self) -> None:
        """Open the bus and start continuous conversions. Idempotent.

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
            self._bus.write_i2c_block_data(
                self.address, REG_CONFIG, [(self._config >> 8) & 0xFF, self._config & 0xFF]
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.close()
            raise RuntimeError(
                f"No ADS1115 responding at 0x{self.address:02x} on I2C bus "
                f"{self.bus_number} ({error}). Check wiring and 'i2cdetect -y "
                f"{self.bus_number}', and the ADDR pin."
            ) from error
        self._ready = True
        logger.info(
            "[SENSITIVITY] ADS1115 ready at 0x%02x on i2c-%d (A%d, +/-%.3fV, %d SPS)",
            self.address,
            self.bus_number,
            self.channel,
            self.full_scale_volts,
            self.data_rate,
        )

    def read_volts(self) -> float:
        """Return the most recent conversion in volts.

        Raises:
            RuntimeError: if the device is not open.
        """
        if not self._ready or self._bus is None:
            raise RuntimeError("ADS1115 is not open; call open() first")
        high, low = self._bus.read_i2c_block_data(self.address, REG_CONVERSION, 2)
        counts = (high << 8) | low
        if counts > _FULL_SCALE_COUNTS:
            counts -= 65536
        return counts_to_volts(counts, self.full_scale_volts)

    def close(self) -> None:
        """Release the bus if this driver opened it. Idempotent."""
        self._ready = False
        bus, self._bus = self._bus, None
        if bus is not None and self._owns_bus:
            try:
                bus.close()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("[SENSITIVITY] Failed to close the I2C bus", exc_info=True)


class MockADS1115:
    """In-memory stand-in used by ``--mock``.

    ``volts`` is writable so tests and mock shots can drive a chosen envelope.
    """

    def __init__(self, *, volts: float = 0.0, **kwargs):
        self.volts = volts
        self.full_scale_volts = kwargs.get("full_scale_volts", DEFAULT_FULL_SCALE_VOLTS)
        self.data_rate = kwargs.get("data_rate", DEFAULT_DATA_RATE)
        self._ready = False

    @property
    def is_open(self) -> bool:
        """True while the mock is 'open'."""
        return self._ready

    def open(self) -> None:
        """Mark the mock ready."""
        self._ready = True

    def read_volts(self) -> float:
        """Return whatever ``volts`` currently holds."""
        if not self._ready:
            raise RuntimeError("ADS1115 is not open; call open() first")
        return self.volts

    def close(self) -> None:
        """Mark the mock closed."""
        self._ready = False
