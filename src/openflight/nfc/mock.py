"""In-memory tag reader used by automated tests."""

from __future__ import annotations

import queue
from typing import Optional

from .models import normalize_uid
from .reader import TagRead


class MockTagReader:
    """A reader whose "antenna" is a queue the caller pushes tags onto.

    Simulated tags keep their contents between presentations, so a tag that
    already carries an NDEF club still reads that way on the next tap.
    """

    name = "mock"

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._tags: dict[str, TagRead] = {}
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
        to re-present a tag exactly as it was last presented.
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

    def read_tag(self, timeout_s: float = 0.5) -> Optional[TagRead]:
        """Return the next queued tag, or None once the timeout expires."""
        try:
            uid = self._queue.get(timeout=max(timeout_s, 0.0))
        except queue.Empty:
            return None
        return self._tags[uid]

    def close(self) -> None:
        """Mark the reader released."""
        self.opened = False
        self.closed = True
