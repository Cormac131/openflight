"""Reader contract shared by the PN532 driver and the mock reader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


class NfcReaderError(RuntimeError):
    """Raised when the reader cannot be reached or answers incorrectly."""


class TagWriteError(NfcReaderError):
    """Raised when a tag could not be written, with a reason fit for the UI."""


@dataclass(frozen=True)
class TagRead:
    """One tag as found on the antenna: who it is, and what it carries."""

    uid: str

    text: Optional[str] = None
    """Text of the tag's NDEF record, when it holds one we can read."""

    blank: bool = False
    """True when the tag's user memory has never been written."""

    writable: bool = False
    """True for a tag this reader knows how to write (NFC Forum Type 2)."""

    @property
    def foreign(self) -> bool:
        """True when the tag holds data, but not a record we can read."""
        return not self.blank and self.text is None


class TagReader(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Contract the NFC service needs from a reader.

    Reads are one operation, not two: the PN532 selects a target and exchanges
    data with it in the same session, so asking for the UID and the tag's
    contents separately would mean selecting the tag twice and racing a player
    lifting the club away between the two.
    """

    name: str

    def open(self) -> None:
        """Initialize the reader and verify it responds."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read_tag(self, timeout_s: float) -> Optional[TagRead]:
        """Return the tag in the field, or None if none appeared."""
        ...  # pylint: disable=unnecessary-ellipsis

    def write_text(self, uid: str, text: str, timeout_s: float) -> None:
        """Write text to the tag with this UID, raising TagWriteError if not.

        Takes the UID rather than writing to whatever is on the antenna: the
        player confirms the club seconds after the tag was read, and by then a
        different club may be resting on the reader.
        """
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the bus."""
        ...  # pylint: disable=unnecessary-ellipsis
