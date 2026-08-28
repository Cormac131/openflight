"""NFC club tags: PN532 driver, NDEF codec, learned tag registry, and poll service."""

from . import ndef
from .mock import MockTagReader
from .models import ClubTag, InvalidTagUidError, TagScan, format_uid, normalize_uid
from .pn532 import DEFAULT_I2C_ADDRESS, DEFAULT_I2C_BUS, PN532I2C
from .reader import NfcReaderError, TagRead, TagReader, TagWriteError
from .registry import DEFAULT_REGISTRY_PATH, ClubTagRegistry, UnknownClubError, validate_club_id
from .service import NfcService

__all__ = [
    "ndef",
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
    "TagRead",
    "TagScan",
    "TagWriteError",
    "UnknownClubError",
    "format_uid",
    "normalize_uid",
    "validate_club_id",
]
