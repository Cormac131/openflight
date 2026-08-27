"""In-memory tag reader used by --mock and by tests."""

from __future__ import annotations

import queue
from typing import Optional

from .models import normalize_uid


class MockTagReader:
    """A reader whose "antenna" is a queue the caller pushes UIDs onto.

    Lets the whole learn-and-select path -- unknown-tag prompt, persistence,
    club change -- be exercised on a laptop with no PN532 attached.
    """

    name = "mock"

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self.opened = False
        self.closed = False

    def open(self) -> None:
        """Mark the reader available."""
        self.opened = True

    def present_tag(self, uid: str) -> str:
        """Queue a tag as if it had been tapped on the antenna."""
        canonical = normalize_uid(uid)
        self._queue.put(canonical)
        return canonical

    def read_uid(self, timeout_s: float = 0.5) -> Optional[str]:
        """Return the next queued UID, or None once the timeout expires."""
        try:
            return self._queue.get(timeout=max(timeout_s, 0.0))
        except queue.Empty:
            return None

    def close(self) -> None:
        """Mark the reader released."""
        self.opened = False
        self.closed = True
