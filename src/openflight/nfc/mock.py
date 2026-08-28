"""In-memory tag reader used by --mock and by tests."""

from __future__ import annotations

import queue
from typing import Optional

from .models import normalize_uid
from .reader import TagRead, TagWriteError


class MockTagReader:
    """A reader whose "antenna" is a queue the caller pushes tags onto.

    Simulated tags keep their contents between presentations, so writing a club
    to one and tapping it again behaves like real hardware: the whole
    blank-tag write flow can be exercised on a laptop with no PN532 attached.
    """

    name = "mock"

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._tags: dict[str, TagRead] = {}
        self._write_failure: Optional[str] = None
        self.opened = False
        self.closed = False

    def open(self) -> None:
        """Mark the reader available."""
        self.opened = True

    def present_tag(
        self,
        uid: str,
        *,
        text: Optional[str] = None,
        writable: bool = True,
        blank: Optional[bool] = None,
    ) -> str:
        """Queue a tag as if it had been tapped on the antenna.

        Contents given here replace whatever the simulated tag held. Omit them
        to re-present a tag exactly as it was last written.
        """
        canonical = normalize_uid(uid)
        known = self._tags.get(canonical)
        if text is not None or blank is not None or known is None:
            self._tags[canonical] = TagRead(
                uid=canonical,
                text=text,
                blank=text is None if blank is None else blank,
                writable=writable,
            )
        self._queue.put(canonical)
        return canonical

    def set_write_failure(self, message: Optional[str]) -> None:
        """Make the next writes fail, so the error path can be exercised."""
        self._write_failure = message

    def read_tag(self, timeout_s: float = 0.5) -> Optional[TagRead]:
        """Return the next queued tag, or None once the timeout expires."""
        try:
            uid = self._queue.get(timeout=max(timeout_s, 0.0))
        except queue.Empty:
            return None
        return self._tags[uid]

    def write_text(  # pylint: disable=unused-argument
        self, uid: str, text: str, timeout_s: float = 3.0
    ) -> None:
        """Write text into a simulated tag's memory."""
        canonical = normalize_uid(uid)
        if self._write_failure:
            raise TagWriteError(self._write_failure)
        tag = self._tags.get(canonical)
        if tag is None:
            raise TagWriteError("Tag not on the reader")
        if not tag.writable:
            raise TagWriteError("This tag type cannot be written")
        self._tags[canonical] = TagRead(uid=canonical, text=text, blank=False, writable=True)

    def close(self) -> None:
        """Mark the reader released."""
        self.opened = False
        self.closed = True
