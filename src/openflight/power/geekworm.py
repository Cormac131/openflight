"""Low-level reader for Geekworm X1202 and X1206 UPS boards."""

from __future__ import annotations

from typing import Protocol

from .models import PowerSample


class SMBusLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of smbus2 used by the MAX17043 fuel gauge reader."""

    def read_i2c_block_data(self, address: int, register: int, length: int) -> list[int]:
        """Read a contiguous register block."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


class DigitalInputLike(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Subset of gpiozero DigitalInputDevice used for AC detection."""

    @property
    def value(self) -> int:
        """Return the current digital input value."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the input."""
        ...  # pylint: disable=unnecessary-ellipsis


class GeekwormPowerReader:
    """Read battery state shared by the X1202 and X1206.

    Both boards expose a MAX17043-compatible fuel gauge at I2C address 0x36
    and assert GPIO6 high while an external power adapter is present.
    """

    DEFAULT_ADDRESS = 0x36
    AC_DETECT_PIN = 6
    VCELL_REGISTER = 0x02
    SOC_REGISTER = 0x04

    def __init__(
        self,
        *,
        bus_number: int = 1,
        address: int = DEFAULT_ADDRESS,
        bus: SMBusLike | None = None,
        ac_input: DigitalInputLike | None = None,
    ):
        owns_bus = bus is None
        gpiozero_pull_up = ac_input is None
        if bus is None:
            from smbus2 import SMBus  # pylint: disable=import-error,import-outside-toplevel

            bus = SMBus(bus_number)
        if ac_input is None:
            try:
                from gpiozero import (  # pylint: disable=import-error,import-outside-toplevel
                    DigitalInputDevice,
                )

                from ..gpio_factory import ensure_lgpio_pin_factory

                ensure_lgpio_pin_factory()
                # GPIO6 is electrically high when the adapter is present. gpiozero
                # represents pull-up inputs as active-low, which is normalized in read().
                ac_input = DigitalInputDevice(self.AC_DETECT_PIN, pull_up=True)
            except Exception:
                if owns_bus:
                    bus.close()
                raise

        self.bus = bus
        self.ac_input = ac_input
        self._gpiozero_pull_up = gpiozero_pull_up
        self.address = address
        self._closed = False

    def _read_register(self, register: int) -> tuple[int, int]:
        data = self.bus.read_i2c_block_data(self.address, register, 2)
        if len(data) != 2:
            raise OSError(
                f"Geekworm fuel gauge register 0x{register:02x} returned "
                f"{len(data)} bytes; expected 2"
            )
        return data[0], data[1]

    def read(self) -> PowerSample:
        """Read and validate battery percentage, voltage, and adapter state."""
        voltage_msb, voltage_lsb = self._read_register(self.VCELL_REGISTER)
        soc_msb, soc_lsb = self._read_register(self.SOC_REGISTER)

        voltage_raw = (voltage_msb << 4) | (voltage_lsb >> 4)
        voltage_v = voltage_raw * 0.00125
        battery_percent = soc_msb + (soc_lsb / 256.0)

        if not 0.0 <= voltage_v <= 5.0:
            raise OSError(f"Geekworm battery voltage is invalid: {voltage_v:.3f}V")
        if not 0.0 <= battery_percent <= 110.0:
            raise OSError(f"Geekworm battery percentage is invalid: {battery_percent:.2f}%")

        input_value = bool(self.ac_input.value)
        external_power = not input_value if self._gpiozero_pull_up else input_value
        return PowerSample(
            battery_percent=min(100.0, battery_percent),
            battery_voltage_v=voltage_v,
            external_power=external_power,
        )

    def close(self) -> None:
        """Release the I2C and GPIO resources."""
        if self._closed:
            return
        self._closed = True
        try:
            self.ac_input.close()
        finally:
            self.bus.close()
