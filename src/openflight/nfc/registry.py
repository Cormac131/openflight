"""Persistent UID -> club mappings learned from tapped club tags."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from ..launch_monitor import ClubType
from .models import ClubTag, InvalidTagUidError, normalize_uid, utc_now_iso

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path.home() / ".openflight" / "club_tags.json"
SCHEMA_VERSION = 1


class UnknownClubError(ValueError):
    """Raised when a mapping names a club the launch monitor does not know."""


def validate_club_id(club_id: object) -> str:
    """Return the canonical ClubType value for a UI/stored club id."""
    try:
        return ClubType(str(club_id)).value
    except ValueError as error:
        raise UnknownClubError(f"Unknown club: {club_id!r}") from error


class ClubTagRegistry:
    """Learned club tags, persisted to disk on every change.

    Every mutation writes the whole file immediately. The map is small (one
    entry per club in the bag) and is edited only when a human taps a new tag,
    so an incremental format would buy nothing and risk a partial write on a
    kiosk that is routinely powered off by unplugging it.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
        self._lock = threading.Lock()
        self._tags: dict[str, ClubTag] = {}
        self._load()

    # ------------------------------------------------------------------ load

    def _load(self) -> None:
        """Read the file into memory, tolerating every failure mode."""
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self._quarantine(f"unreadable club tag registry: {error}")
            return

        entries = raw.get("tags") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            self._quarantine("club tag registry has no 'tags' object")
            return

        loaded: dict[str, ClubTag] = {}
        for raw_uid, entry in entries.items():
            tag = self._parse_entry(raw_uid, entry)
            if tag is not None:
                loaded[tag.uid] = tag
        self._tags = loaded
        logger.info("[NFC] Loaded %d club tag(s) from %s", len(loaded), self.path)

    @staticmethod
    def _parse_entry(raw_uid: object, entry: object) -> Optional[ClubTag]:
        """Convert one stored row, dropping it when it cannot be trusted.

        A single bad row must not cost the user the rest of the bag, so parse
        failures are skipped individually rather than failing the whole load.
        """
        if not isinstance(entry, dict):
            logger.warning("[NFC] Dropping club tag %r: entry is not an object", raw_uid)
            return None
        try:
            uid = normalize_uid(raw_uid)
            club_id = validate_club_id(entry.get("club"))
        except (InvalidTagUidError, UnknownClubError) as error:
            logger.warning("[NFC] Dropping club tag %r: %s", raw_uid, error)
            return None
        return ClubTag(
            uid=uid,
            club_id=club_id,
            learned_at=str(entry.get("learned_at") or utc_now_iso()),
            last_seen_at=(str(entry["last_seen_at"]) if entry.get("last_seen_at") else None),
        )

    def _quarantine(self, reason: str) -> None:
        """Move a corrupt registry aside so the kiosk still starts."""
        logger.warning("[NFC] %s; starting with an empty registry", reason)
        backup = self.path.with_suffix(self.path.suffix + ".corrupt")
        try:
            os.replace(self.path, backup)
            logger.warning("[NFC] Previous registry saved to %s", backup)
        except OSError as error:
            logger.warning("[NFC] Could not preserve corrupt registry: %s", error)

    # ------------------------------------------------------------------ save

    def _save_locked(self, tags: dict[str, ClubTag] | None = None) -> None:
        """Atomically rewrite the file; callers must hold the lock."""
        payload_tags = self._tags if tags is None else tags
        payload = {
            "version": SCHEMA_VERSION,
            "tags": {tag.uid: tag.to_dict() for tag in payload_tags.values()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file and rename, so a power cut during the
        # write leaves the previous registry intact instead of a truncated one.
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=self.path.name,
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    # ----------------------------------------------------------------- reads

    def club_for(self, uid: str) -> Optional[str]:
        """Return the club mapped to a UID, or None when it is unlearned."""
        try:
            key = normalize_uid(uid)
        except InvalidTagUidError:
            return None
        with self._lock:
            tag = self._tags.get(key)
        return tag.club_id if tag else None

    def entries(self) -> list[ClubTag]:
        """All mappings, ordered by club so the UI list is stable."""
        with self._lock:
            tags = list(self._tags.values())
        return sorted(tags, key=lambda tag: (tag.club_id, tag.uid))

    def to_payload(self) -> list[dict]:
        """The tag list as emitted to the UI."""
        return [tag.to_dict() for tag in self.entries()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._tags)

    # -------------------------------------------------------------- mutation

    def assign(self, uid: str, club_id: str) -> ClubTag:
        """Learn (or re-point) a tag and persist immediately.

        Re-assigning keeps the original ``learned_at`` so the UI can still show
        when the tag entered the bag. Other tags already pointing at the same
        club are left alone: two tags on one club (grip and shaft) is a
        legitimate build, not a conflict to resolve.
        """
        key = normalize_uid(uid)
        club = validate_club_id(club_id)
        with self._lock:
            existing = self._tags.get(key)
            tag = ClubTag(
                uid=key,
                club_id=club,
                learned_at=existing.learned_at if existing else utc_now_iso(),
                last_seen_at=existing.last_seen_at if existing else None,
            )
            next_tags = dict(self._tags)
            next_tags[key] = tag
            self._save_locked(next_tags)
            self._tags = next_tags
        logger.info("[NFC] Learned tag %s -> %s", key, club)
        return tag

    def forget(self, uid: str) -> bool:
        """Remove a mapping. Returns False when the UID was not mapped."""
        try:
            key = normalize_uid(uid)
        except InvalidTagUidError:
            return False
        with self._lock:
            if key not in self._tags:
                return False
            next_tags = dict(self._tags)
            del next_tags[key]
            self._save_locked(next_tags)
            self._tags = next_tags
        logger.info("[NFC] Forgot tag %s", key)
        return True

    def touch(self, uid: str) -> None:
        """Record that a known tag was just seen; best effort, never raises.

        Persisting last-seen keeps the UI list useful for spotting a tag that
        has stopped reading, but it must never break a scan, so disk errors are
        swallowed here rather than propagated into the reader thread.
        """
        try:
            key = normalize_uid(uid)
        except InvalidTagUidError:
            return
        with self._lock:
            tag = self._tags.get(key)
            if tag is None:
                return
            self._tags[key] = ClubTag(
                uid=tag.uid,
                club_id=tag.club_id,
                learned_at=tag.learned_at,
                last_seen_at=utc_now_iso(),
            )
            try:
                self._save_locked()
            except OSError as error:
                logger.debug("[NFC] Could not persist last-seen for %s: %s", key, error)
