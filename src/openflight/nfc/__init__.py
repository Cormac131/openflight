"""NFC club tags: PN532/PN5180 drivers, NDEF codec, learned tag registry, and poll service."""

from . import ndef
from .mock import MockTagReader
from .models import ClubTag, InvalidTagUidError, TagScan, format_uid, normalize_uid
from .pn532 import (
    DEFAULT_CS_GPIO,
    DEFAULT_I2C_ADDRESS,
    DEFAULT_I2C_BUS,
    DEFAULT_IRQ_GPIO,
    DEFAULT_SPI_BUS,
    DEFAULT_SPI_DEVICE,
    PN532I2C,
)
from .pn5180 import DEFAULT_BUSY_GPIO, DEFAULT_RESET_GPIO, Pn5180Spi
from .reader import NfcReaderError, TagRead, TagReader
from .registry import DEFAULT_REGISTRY_PATH, ClubTagRegistry, UnknownClubError, validate_club_id
from .service import NfcService

__all__ = [
    "ndef",
    "ClubTag",
    "ClubTagRegistry",
    "DEFAULT_BUSY_GPIO",
    "DEFAULT_CS_GPIO",
    "DEFAULT_I2C_ADDRESS",
    "DEFAULT_I2C_BUS",
    "DEFAULT_IRQ_GPIO",
    "DEFAULT_RESET_GPIO",
    "DEFAULT_SPI_BUS",
    "DEFAULT_SPI_DEVICE",
    "DEFAULT_REGISTRY_PATH",
    "InvalidTagUidError",
    "MockTagReader",
    "NfcReaderError",
    "NfcService",
    "PN532I2C",
    "Pn5180Spi",
    "TagReader",
    "TagRead",
    "TagScan",
    "UnknownClubError",
    "format_uid",
    "normalize_uid",
    "validate_club_id",
]
