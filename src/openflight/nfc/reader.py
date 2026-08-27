"""Reader contract shared by the PN532 driver and the mock reader."""

from __future__ import annotations

from typing import Optional, Protocol


class NfcReaderError(RuntimeError):
    """Raised when the reader cannot be reached or answers incorrectly."""


class TagReader(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Minimal contract the NFC service needs from a reader.

    Deliberately narrow: OpenFlight only ever asks "is a tag on the antenna
    right now, and what is its UID". Keeping NDEF reads and writes out of the
    interface means a different reader IC can be dropped in without touching
    the registry, the service, or the server.
    """

    name: str

    def open(self) -> None:
        """Initialize the reader and verify it responds."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read_uid(self, timeout_s: float) -> Optional[str]:
        """Return the UID of a tag in the field, or None if none appeared."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the bus."""
        ...  # pylint: disable=unnecessary-ellipsis
