"""NFC club-tag reading: PN532 driver, learned tag registry, and poll service."""

from .mock import MockTagReader
from .models import ClubTag, InvalidTagUidError, TagScan, format_uid, normalize_uid
from .pn532 import DEFAULT_I2C_ADDRESS, DEFAULT_I2C_BUS, PN532I2C
from .reader import NfcReaderError, TagReader
from .registry import DEFAULT_REGISTRY_PATH, ClubTagRegistry, UnknownClubError, validate_club_id
from .service import NfcService

__all__ = [
    "ClubTag",
    "ClubTagRegistry",
    "DEFAULT_I2C_ADDRESS",
    "DEFAULT_I2C_BUS",
    "DEFAULT_REGISTRY_PATH",
    "InvalidTagUidError",
    "MockTagReader",
    "NfcReaderError",
    "NfcService",
    "PN532I2C",
    "TagReader",
    "TagScan",
    "UnknownClubError",
    "format_uid",
    "normalize_uid",
    "validate_club_id",
]
