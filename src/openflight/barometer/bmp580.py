"""
Minimal BMP580 I2C driver for station-pressure and temperature readings.

Register map, scaling, and start-up sequence follow the Bosch BMP581
datasheet (BST-BMP581-DS004). The BMP580, BMP581, and BMP585 share one
register map and differ only in chip ID, so this driver accepts all three.

The sensor is driven in FORCED mode: each `read()` triggers a single
conversion and the part returns to standby afterwards. OpenFlight samples air
density on a timescale of minutes, so a continuously-running measurement loop
would burn power producing readings nobody consumes.

Oversampling is set high (x16 pressure, x2 temperature) not because carry needs
the precision — 1 hPa of pressure error is about 0.3 yd on a driver, so any
barometer is over-specified for this — but because at one reading every couple
of seconds the extra conversion time is free.
"""

from __future__ import annotations

import time
from typing import Protocol

from .models import PressureSample


class SMBusLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of smbus2 used by the driver, allowing deterministic tests."""

    def read_byte_data(self, address: int, register: int) -> int:
        """Read one register byte."""
        ...  # pylint: disable=unnecessary-ellipsis

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        """Write one register byte."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read_i2c_block_data(self, address: int, register: int, length: int) -> list[int]:
        """Read a contiguous register block."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


class BMP580IdentityError(RuntimeError):
    """Raised when the expected BMP580 chip identity is not present."""


class BMP580NotReadyError(RuntimeError):
    """Raised when a conversion does not complete within the expected time."""


class BMP580:
    """Read a BMP580/581/585 configured for forced-mode pressure sampling."""

    DEFAULT_ADDRESS = 0x47  # SDO high (Adafruit default); 0x46 with SDO low.
    ALTERNATE_ADDRESS = 0x46

    CHIP_ID = 0x01
    REV_ID = 0x02
    INT_SOURCE = 0x15
    INT_STATUS = 0x27
    STATUS = 0x28
    OSR_CONFIG = 0x36
    ODR_CONFIG = 0x37
    TEMP_DATA_XLSB = 0x1D
    CMD = 0x7E

    # CHIP_ID values: BMP580 and BMP581 both report 0x50, BMP585 reports 0x51.
    CHIP_IDS = {0x50: "BMP580/BMP581", 0x51: "BMP585"}

    SOFT_RESET = 0xB6
    # INT_SOURCE bits. INT_STATUS.drdy is only populated when this is set
    # (BST-BMP581-DS004 4.7.1).
    DRDY_DATA_REG_EN = 0x01
    # INT_STATUS bits.
    DRDY_DATA_REG = 0x01
    POR_COMPLETE = 0x10
    # STATUS bits.
    NVM_READY = 0x02
    NVM_ERROR = 0x04

    # ODR_CONFIG.pwr_mode (bits 1:0) and deep_dis (bit 7). After reset the
    # part is in deep standby; forced conversions do not run until deep_dis
    # is set (BST-BMP581-DS004 4.3.2).
    MODE_STANDBY = 0b00
    MODE_FORCED = 0b10
    DEEP_DIS = 1 << 7
    # OSR_CONFIG: press_en (bit 6) | osr_p x16 (bits 5:3) | osr_t x2 (bits 2:0).
    OSR_CONFIG_VALUE = (1 << 6) | (0b100 << 3) | 0b001

    # A x16/x2 conversion completes in a few ms; this bounds a stuck sensor
    # without making a healthy read wait.
    CONVERSION_TIMEOUT_S = 0.5
    POLL_INTERVAL_S = 0.002
    RESET_SETTLE_S = 0.01
    # Datasheet t_standby: wait after leaving deep standby before forced mode.
    MODE_SETTLE_S = 0.003

    def __init__(
        self,
        *,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        bus: SMBusLike | None = None,
    ):
        if bus is None:
            from smbus2 import SMBus  # pylint: disable=import-outside-toplevel,import-error

            bus = SMBus(bus_number)
        self.bus = bus
        self.bus_number = bus_number
        self.address = address
        self.chip_name: str | None = None
        self._closed = False

    def initialize(self) -> None:
        """Soft reset, verify identity and NVM health, then configure sampling."""
        self.bus.write_byte_data(self.address, self.CMD, self.SOFT_RESET)
        time.sleep(self.RESET_SETTLE_S)

        identity = self.bus.read_byte_data(self.address, self.CHIP_ID)
        if identity not in self.CHIP_IDS:
            expected = ", ".join(f"0x{value:02x}" for value in sorted(self.CHIP_IDS))
            raise BMP580IdentityError(
                f"BMP580 CHIP_ID expected one of {expected}, got 0x{identity:02x}"
            )
        self.chip_name = self.CHIP_IDS[identity]

        # The datasheet's post-power-up check: NVM must be ready and error-free
        # before the trim values behind every reading can be trusted.
        status = self.bus.read_byte_data(self.address, self.STATUS)
        if not status & self.NVM_READY or status & self.NVM_ERROR:
            raise BMP580IdentityError(
                f"BMP580 NVM not ready or reporting an error (STATUS=0x{status:02x})"
            )

        self.bus.write_byte_data(self.address, self.INT_SOURCE, self.DRDY_DATA_REG_EN)
        self.bus.write_byte_data(self.address, self.OSR_CONFIG, self.OSR_CONFIG_VALUE)
        self._set_power_mode(self.MODE_STANDBY)
        time.sleep(self.MODE_SETTLE_S)

    @staticmethod
    def _raw24(xlsb: int, lsb: int, msb: int) -> int:
        """Assemble one little-endian 24-bit register triple."""
        return (msb << 16) | (lsb << 8) | xlsb

    @classmethod
    def _temperature_c(cls, xlsb: int, lsb: int, msb: int) -> float:
        """Decode the signed 24-bit temperature; datasheet scaling is 2^-16 °C."""
        raw = cls._raw24(xlsb, lsb, msb)
        if raw & 0x800000:
            raw -= 1 << 24
        return raw / 65536.0

    @classmethod
    def _pressure_pa(cls, xlsb: int, lsb: int, msb: int) -> float:
        """Decode the unsigned 24-bit pressure; datasheet scaling is 2^-6 Pa."""
        return cls._raw24(xlsb, lsb, msb) / 64.0

    def _set_power_mode(self, mode: int) -> None:
        """Write ODR_CONFIG with deep standby disabled so conversions can run."""
        self.bus.write_byte_data(self.address, self.ODR_CONFIG, self.DEEP_DIS | mode)

    def _wait_for_data(self) -> None:
        """Poll INT_STATUS until the forced conversion reports data ready."""
        deadline = time.monotonic() + self.CONVERSION_TIMEOUT_S
        while time.monotonic() < deadline:
            # INT_STATUS is clear-on-read, so this must be read once per poll
            # and acted on immediately.
            if self.bus.read_byte_data(self.address, self.INT_STATUS) & self.DRDY_DATA_REG:
                return
            time.sleep(self.POLL_INTERVAL_S)
        raise BMP580NotReadyError(
            f"BMP580 conversion did not complete within {self.CONVERSION_TIMEOUT_S:g}s"
        )

    def read(self, *, timestamp: float | None = None) -> PressureSample:
        """Trigger one forced conversion and return station pressure with temperature."""
        self._set_power_mode(self.MODE_FORCED)
        self._wait_for_data()

        # Temperature and pressure are six contiguous registers from 0x1D, so
        # one block read keeps both halves from the same conversion.
        data = self.bus.read_i2c_block_data(self.address, self.TEMP_DATA_XLSB, 6)
        if len(data) != 6:
            raise OSError(f"BMP580 returned {len(data)} data bytes; expected 6")
        return PressureSample(
            timestamp=time.time() if timestamp is None else timestamp,
            pressure_pa=self._pressure_pa(data[3], data[4], data[5]),
            temperature_c=self._temperature_c(data[0], data[1], data[2]),
        )

    def close(self) -> None:
        """Return the sensor to standby and close the owned I2C bus."""
        if self._closed:
            return
        try:
            self._set_power_mode(self.MODE_STANDBY)
        except OSError:
            # A sensor that has already gone away must not block shutdown.
            pass
        self.bus.close()
        self._closed = True
