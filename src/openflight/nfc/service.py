"""Background polling service that turns tag taps into club selections."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .models import InvalidTagUidError, TagScan, normalize_uid
from .reader import NfcReaderError, TagRead, TagReader
from .registry import ClubTag, ClubTagRegistry, UnknownClubError, validate_club_id

logger = logging.getLogger(__name__)

# A tag left resting on the antenna reads continuously. Suppress repeats of the
# same UID for this long so one tap is one club change, not a stream of them.
DEFAULT_REPEAT_SUPPRESSION_S = 3.0
DEFAULT_POLL_INTERVAL_S = 0.15
DEFAULT_READ_TIMEOUT_S = 0.5
# Consecutive read failures tolerated before the reader is reopened. A loose
# STEMMA cable produces a burst of errors that a reopen usually clears.
_ERRORS_BEFORE_REOPEN = 5
_MAX_ERROR_BACKOFF_S = 5.0
# How long a write waits for the named tag to be back on the antenna. The
# player confirms the club on screen first, then holds the club to the reader.
DEFAULT_WRITE_TIMEOUT_S = 6.0


class NfcService:
    """Poll a reader, resolve UIDs against the registry, and report scans."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        reader: TagReader,
        registry: ClubTagRegistry,
        *,
        on_scan: Callable[[TagScan], None],
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        read_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
        repeat_suppression_s: float = DEFAULT_REPEAT_SUPPRESSION_S,
        write_timeout_s: float = DEFAULT_WRITE_TIMEOUT_S,
    ):
        if poll_interval_s < 0 or read_timeout_s <= 0:
            raise ValueError("poll_interval_s must be >= 0 and read_timeout_s must be > 0")
        self.reader = reader
        self.registry = registry
        self.on_scan = on_scan
        self.poll_interval_s = poll_interval_s
        self.read_timeout_s = read_timeout_s
        self.repeat_suppression_s = repeat_suppression_s
        self.write_timeout_s = write_timeout_s

        self._lock = threading.Lock()
        # The poll thread and a write from a socket handler share one I2C bus.
        # Without this they interleave frames on it and corrupt each other.
        self._reader_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_uid: Optional[str] = None
        self._last_uid_at: float = 0.0
        self._last_scan: Optional[TagScan] = None
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[float] = None
        self._scan_count = 0
        self._error_count = 0

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Open the reader and begin polling. Raises if the reader is absent."""
        with self._reader_lock:
            self.reader.open()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="openflight-nfc",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop polling and release the reader."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            # Long enough for an in-flight read to return on its own; the reader
            # is configured for short reads so this should not be hit.
            thread.join(timeout=self.read_timeout_s + 2.0)
        self._thread = None
        try:
            with self._reader_lock:
                self.reader.close()
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.debug("[NFC] Reader close failed: %s", error)

    # ------------------------------------------------------------------ loop

    def _poll_loop(self) -> None:
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                with self._reader_lock:
                    tag = self.reader.read_tag(self.read_timeout_s)
                consecutive_errors = 0
                self._clear_error()
                if tag:
                    self.handle_tag(tag)
            except Exception as error:  # pylint: disable=broad-exception-caught
                consecutive_errors += 1
                self._record_error(error)
                if consecutive_errors >= _ERRORS_BEFORE_REOPEN:
                    consecutive_errors = 0
                    self._reopen_reader()
                # Back off so a disconnected reader does not spin the CPU or
                # flood the log, while a transient glitch still recovers fast.
                backoff = min(self.poll_interval_s * (2**consecutive_errors), _MAX_ERROR_BACKOFF_S)
                self._stop_event.wait(max(backoff, 0.1))
                continue
            self._stop_event.wait(self.poll_interval_s)

    def _reopen_reader(self) -> None:
        """Bounce the reader after a run of failures."""
        logger.warning("[NFC] Reopening reader after repeated read failures")
        try:
            with self._reader_lock:
                self.reader.close()
                self.reader.open()
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._record_error(error)

    # --------------------------------------------------------------- scanning

    def handle_tag(self, tag: TagRead) -> Optional[TagScan]:
        """Resolve one tag and report it. Returns None when suppressed.

        Exposed directly (not only via the poll thread) so the mock path and the
        tests can drive a tap without timing games.
        """
        try:
            uid = normalize_uid(tag.uid)
        except InvalidTagUidError as error:
            logger.warning("[NFC] Ignoring unusable tag UID %r: %s", tag.uid, error)
            return None

        now = time.time()
        with self._lock:
            if uid == self._last_uid and now - self._last_uid_at < self.repeat_suppression_s:
                # Refresh the timestamp so a tag resting on the antenna stays
                # suppressed instead of re-firing every repeat_suppression_s.
                self._last_uid_at = now
                return None
            self._last_uid = uid
            self._last_uid_at = now

        club_id, source = self._resolve_club(uid, tag)
        scan = TagScan(
            uid=uid,
            timestamp=now,
            club_id=club_id,
            source=source,
            blank=tag.blank,
            writable=tag.writable,
        )
        with self._lock:
            self._last_scan = scan
            self._scan_count += 1

        try:
            self.on_scan(scan)
        except Exception as error:  # pylint: disable=broad-exception-caught
            # A UI/emit failure must not kill the poll thread.
            logger.warning("[NFC] Scan handler failed for %s: %s", uid, error, exc_info=True)
        return scan

    def _resolve_club(self, uid: str, tag: TagRead) -> tuple[Optional[str], Optional[str]]:
        """Decide which club a tag names, and where that came from.

        The tag's own record wins over the registry, and the registry is
        corrected to match. A club written onto the tag travels with the club
        between rigs, so when the two disagree the tag is the one that was
        deliberately set.
        """
        if tag.text:
            try:
                club_id = validate_club_id(tag.text)
            except UnknownClubError:
                # Somebody else's text record, or a club id from a newer
                # release. Fall through to whatever this rig learned by UID.
                logger.info("[NFC] Tag %s holds unrecognized text %r", uid, tag.text)
            else:
                if self.registry.club_for(uid) != club_id:
                    self.registry.assign(uid, club_id)
                else:
                    self.registry.touch(uid)
                return club_id, "tag"

        club_id = self.registry.club_for(uid)
        if club_id is None:
            return None, None
        self.registry.touch(uid)
        return club_id, "registry"

    def write_club_tag(self, uid: str, club_id: str) -> ClubTag:
        """Write a club onto a tag, then mirror it into the registry.

        The registry entry is only made once the tag reports the write back, so
        a failed write never leaves the rig claiming a club the tag does not
        carry.
        """
        club = validate_club_id(club_id)
        key = normalize_uid(uid)
        with self._reader_lock:
            self.reader.write_text(key, club, self.write_timeout_s)
        tag = self.registry.assign(key, club)
        # The player will lift the club away and tap it again to check.
        self.forget_recent(key)
        logger.info("[NFC] Wrote %s to tag %s", club, key)
        return tag

    def forget_recent(self, uid: str) -> None:
        """Clear repeat suppression for a UID so the next tap reports again.

        Used after a tag is learned or forgotten: the user's very next tap is a
        deliberate confirmation and should take effect immediately.
        """
        try:
            key = normalize_uid(uid)
        except InvalidTagUidError:
            return
        with self._lock:
            if self._last_uid == key:
                self._last_uid = None
                self._last_uid_at = 0.0

    # ----------------------------------------------------------------- status

    def _record_error(self, error: BaseException) -> None:
        with self._lock:
            self._last_error = str(error)
            self._last_error_at = time.time()
            self._error_count += 1
        level = logging.WARNING if isinstance(error, NfcReaderError) else logging.ERROR
        logger.log(level, "[NFC] Read failed: %s", error)

    def _clear_error(self) -> None:
        with self._lock:
            self._last_error = None
            self._last_error_at = None

    def status(self) -> dict:
        """Snapshot for the debug UI, session log, and startup banner."""
        with self._lock:
            last_scan = self._last_scan
            return {
                "reader": getattr(self.reader, "name", type(self.reader).__name__),
                "running": self._thread is not None and self._thread.is_alive(),
                "known_tags": len(self.registry),
                "scan_count": self._scan_count,
                "error_count": self._error_count,
                "last_error": self._last_error,
                "last_error_at": self._last_error_at,
                "last_scan": last_scan.to_dict() if last_scan else None,
            }
