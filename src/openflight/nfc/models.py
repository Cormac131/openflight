"""Data types shared by the NFC club-tag reader, registry, and service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# NFC UIDs are 4, 7, or 10 bytes for ISO14443A tags. Accept anything in that
# range rather than the three exact lengths so an unusual tag is still learnable.
_MIN_UID_BYTES = 4
_MAX_UID_BYTES = 10
_UID_SEPARATORS = re.compile(r"[\s:\-_]")


class InvalidTagUidError(ValueError):
    """Raised when a scanned or user-supplied UID is not usable as a key."""


def normalize_uid(raw: object) -> str:
    """Return the canonical storage form of a tag UID.

    Readers and hand-entered values disagree on case and separators ("04:a2:b1"
    vs "04A2B1"), and the registry is keyed by UID, so every entry point funnels
    through here to keep one tag from learning two mappings.
    """
    text = _UID_SEPARATORS.sub("", str(raw or "")).upper()
    if not text:
        raise InvalidTagUidError("Tag UID is empty")
    if len(text) % 2 != 0:
        raise InvalidTagUidError(f"Tag UID has an odd digit count: {text}")
    if not all(char in "0123456789ABCDEF" for char in text):
        raise InvalidTagUidError(f"Tag UID is not hexadecimal: {text}")
    length_bytes = len(text) // 2
    if not _MIN_UID_BYTES <= length_bytes <= _MAX_UID_BYTES:
        raise InvalidTagUidError(
            f"Tag UID must be {_MIN_UID_BYTES}-{_MAX_UID_BYTES} bytes, got {length_bytes}"
        )
    return text


def format_uid(uid: str) -> str:
    """Return a UID grouped for display, e.g. "04A2B1C3" -> "04:A2:B1:C3"."""
    canonical = normalize_uid(uid)
    return ":".join(canonical[index : index + 2] for index in range(0, len(canonical), 2))


def utc_now_iso() -> str:
    """Timestamp used for learned-at bookkeeping."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TagScan:
    """One tag presentation, resolved against the tag itself and the registry."""

    uid: str
    timestamp: float
    club_id: Optional[str] = None

    source: Optional[str] = None
    """Where the club came from: "tag" for the tag's own record, else "registry"."""

    blank: bool = False
    """True when the tag's user memory has never been written."""

    writable: bool = False
    """True when the tag's NDEF memory could be read (NFC Forum Type 2)."""

    @property
    def known(self) -> bool:
        """True when the tag already maps to a club."""
        return self.club_id is not None

    def to_dict(self) -> dict:
        """Serialize for WebSocket emission and session logging."""
        return {
            "uid": self.uid,
            "uid_display": format_uid(self.uid),
            "timestamp": self.timestamp,
            "club": self.club_id,
            "known": self.known,
            "source": self.source,
            "blank": self.blank,
            "writable": self.writable,
        }


@dataclass(frozen=True)
class ClubTag:
    """A persisted UID -> club mapping."""

    uid: str
    club_id: str
    learned_at: str
    last_seen_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize for storage and for the UI's tag list."""
        return {
            "uid": self.uid,
            "uid_display": format_uid(self.uid),
            "club": self.club_id,
            "learned_at": self.learned_at,
            "last_seen_at": self.last_seen_at,
        }
