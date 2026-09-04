"""Work out which NFC reader chip is actually wired up.

Both supported readers sit on the same SPI pins, so which one is attached is
answerable by asking each in turn for its identity and keeping whichever
replies: the PN5180 has a version pair in EEPROM, and the PN532 answers
GetFirmwareVersion with an IC byte of 0x32.

The PN5180 is probed first because its probe is passive -- an EEPROM read
over SPI, no RF field, no mode assumptions. The PN532's probe writes a
command frame and waits for an ACK, which is the slower and noisier of the
two when nothing answers.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .reader import NfcReaderError, TagReader

logger = logging.getLogger(__name__)

# Probed in this order; the name matches the --nfc-reader choice.
DETECT_ORDER = ("pn5180", "pn532")


def build_reader(chip: str, **settings) -> TagReader:
    """Construct (but do not open) the driver for one chip name.

    ``settings`` carries every reader's options; each driver is handed only
    the ones it understands, so one call site can stay chip-agnostic.
    """
    from .pn532 import PN532I2C  # pylint: disable=import-outside-toplevel
    from .pn5180 import Pn5180Spi  # pylint: disable=import-outside-toplevel

    if chip == "pn5180":
        return Pn5180Spi(
            spi_bus=settings.get("spi_bus", 0),
            spi_device=settings.get("spi_device", 0),
            busy_gpio=settings.get("busy_gpio", 23),
            reset_gpio=settings.get("reset_gpio", 24),
        )
    if chip == "pn532":
        return PN532I2C(
            interface=settings.get("interface", "spi"),
            spi_bus=settings.get("spi_bus", 0),
            spi_device=settings.get("spi_device", 0),
            irq_gpio=settings.get("irq_gpio", 22),
            bus_number=settings.get("bus_number", 1),
            address=settings.get("address", 0x24),
        )
    raise ValueError(f"Unknown NFC reader chip {chip!r}")


def detect_reader(
    *,
    order: tuple[str, ...] = DETECT_ORDER,
    factory: Optional[Callable[[str], TagReader]] = None,
    **settings,
) -> TagReader:
    """Open whichever supported reader answers, and return it.

    The returned reader is already open -- opening it is how it was
    identified, and opening twice would reset a chip that just proved itself.

    Raises:
        NfcReaderError: when no chip answered, with each chip's own failure
            quoted, since "nothing is attached" and "the PN5180 is attached
            but BUSY is not wired" need different fixes.
    """
    make = factory or (lambda chip: build_reader(chip, **settings))
    failures = []
    for chip in order:
        reader = None
        try:
            reader = make(chip)
            reader.open()
            logger.info("[NFC] Auto-detected a %s", chip)
            return reader
        except Exception as error:  # pylint: disable=broad-exception-caught
            failures.append(f"{chip}: {error}")
            logger.debug("[NFC] %s did not answer the probe: %s", chip, error)
            if reader is not None:
                try:
                    reader.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.debug("[NFC] Closing %s after a failed probe failed", chip)
    raise NfcReaderError("No NFC reader answered. Tried -- " + "; ".join(failures))
