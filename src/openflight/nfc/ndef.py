"""NDEF encoding for club tags, scoped to a single UTF-8 text record.

OpenFlight reads the club id from a plain NDEF text record when one is present,
so a bag tagged from a phone NFC app still selects the right club. The rig
never writes tags; learning is always a UID mapping stored on disk.

Only what that needs is implemented. Short records, one record per message, no
chunking, no ID field -- a club id is a dozen bytes and will never approach the
255-byte short-record limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# NDEF record header bits for a lone short record with no ID field.
_MESSAGE_BEGIN = 0x80
_MESSAGE_END = 0x40
_SHORT_RECORD = 0x10
_TNF_WELL_KNOWN = 0x01
TEXT_RECORD_HEADER = _MESSAGE_BEGIN | _MESSAGE_END | _SHORT_RECORD | _TNF_WELL_KNOWN
RECORD_TYPE_TEXT = 0x54

# Type 2 tag TLV block tags.
TLV_NULL = 0x00
TLV_NDEF = 0x03
TLV_PROPRIETARY = 0xFD
TLV_TERMINATOR = 0xFE
_TLV_THREE_BYTE_LENGTH = 0xFF

DEFAULT_LANGUAGE = "en"
MAX_SHORT_PAYLOAD = 0xFF - 1


class NdefError(ValueError):
    """Raised when a tag's contents are not NDEF we can work with."""


@dataclass(frozen=True)
class TagContent:
    """What a tag's user memory turned out to hold."""

    text: Optional[str]
    """Text of the NDEF text record, when the tag holds exactly one."""

    blank: bool
    """True when nothing has been written to the tag at all."""

    @property
    def foreign(self) -> bool:
        """True when the tag holds something, but not a text record we can read."""
        return not self.blank and self.text is None


def encode_text_record(text: str, *, language: str = DEFAULT_LANGUAGE) -> bytes:
    """Encode one UTF-8 NDEF text record as a complete NDEF message."""
    language_bytes = language.encode("ascii")
    if not 1 <= len(language_bytes) <= 0x3F:
        raise NdefError(f"Language code must be 1-63 bytes, got {len(language_bytes)}")
    text_bytes = text.encode("utf-8")
    # Status byte: bit 7 clear marks UTF-8; the low six bits hold the language
    # code length, so a reader knows where the text starts.
    payload = bytes([len(language_bytes)]) + language_bytes + text_bytes
    if len(payload) > MAX_SHORT_PAYLOAD:
        raise NdefError(f"Text record payload must fit a short record, got {len(payload)}")
    return bytes([TEXT_RECORD_HEADER, 0x01, len(payload), RECORD_TYPE_TEXT]) + payload


def decode_text_record(  # pylint: disable=too-many-return-statements
    message: bytes,
) -> Optional[str]:
    """Return the text of a single-record NDEF text message, else None.

    Anything else -- a URI record, a multi-record message, a truncated one --
    is somebody else's data and is reported as unreadable rather than guessed at.
    """
    if len(message) < 4:
        return None
    header, type_length, payload_length, record_type = (
        message[0],
        message[1],
        message[2],
        message[3],
    )
    if (
        (header & ~_MESSAGE_END) != (_MESSAGE_BEGIN | _SHORT_RECORD | _TNF_WELL_KNOWN)
        or type_length != 1
        or record_type != RECORD_TYPE_TEXT
    ):
        return None
    record_length = 4 + payload_length
    if not (header & _MESSAGE_END) or len(message) != record_length:
        return None
    payload = message[4:record_length]
    if not payload:
        return None
    status = payload[0]
    if status & 0x80:
        # UTF-16 is legal NDEF but nothing writes club tags that way.
        return None
    language_length = status & 0x3F
    if len(payload) < 1 + language_length:
        return None
    try:
        return payload[1 + language_length :].decode("utf-8")
    except UnicodeDecodeError:
        return None


def wrap_tlv(message: bytes) -> bytes:
    """Wrap an NDEF message in the Type 2 NDEF TLV, terminator included."""
    if len(message) >= _TLV_THREE_BYTE_LENGTH:
        length = bytes([_TLV_THREE_BYTE_LENGTH, len(message) >> 8, len(message) & 0xFF])
    else:
        length = bytes([len(message)])
    return bytes([TLV_NDEF]) + length + message + bytes([TLV_TERMINATOR])


def find_ndef_message(memory: bytes) -> Optional[bytes]:
    """Walk a Type 2 tag's TLV blocks and return the NDEF message, if any.

    Returns None when no NDEF TLV is present, and ``b""`` for an NDEF TLV of
    length zero, which is how some vendors ship "formatted but empty" tags.
    """
    index = 0
    while index < len(memory):
        tag = memory[index]
        if tag == TLV_TERMINATOR:
            return None
        if tag == TLV_NULL:
            index += 1
            continue
        if index + 1 >= len(memory):
            return None
        length = memory[index + 1]
        value_start = index + 2
        if length == _TLV_THREE_BYTE_LENGTH:
            if index + 3 >= len(memory):
                return None
            length = (memory[index + 2] << 8) | memory[index + 3]
            value_start = index + 4
        if tag == TLV_NDEF:
            value = memory[value_start : value_start + length]
            return value if len(value) == length else None
        index = value_start + length
    return None


def read_tag_content(memory: bytes) -> TagContent:
    """Classify a Type 2 tag's user memory as blank, ours, or somebody else's."""
    if not memory or not any(memory):
        return TagContent(text=None, blank=True)
    message = find_ndef_message(memory)
    if message is None:
        return TagContent(text=None, blank=False)
    if not message:
        # An NDEF TLV with no message: formatted at the factory, never written.
        return TagContent(text=None, blank=True)
    return TagContent(text=decode_text_record(message), blank=False)
