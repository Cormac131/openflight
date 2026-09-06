"""What the last check learned, and the composed status the kiosk shows.

``update-status.json`` holds only check results. Layout facts (current,
staged, previous, pending confirmation) are read from the links themselves,
so the composed status can never disagree with the disk.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from ..release import ReleaseInfo
from ..startup_status import write_json_atomically
from .config import load_update_config
from .layout import InstallLayout, read_pending
from .paths import UpdatePaths

STATUS_FORMAT_VERSION = 1
CHECK_STATES = ("checking", "downloading", "staging", "up_to_date", "held_back", "failed")
MAX_BAD_TAGS = 10


@dataclass
class CheckStatus:
    """Result of the most recent ``openflight-update check``."""

    state: str = "up_to_date"
    channel: Optional[str] = None
    available: Optional[dict] = None
    last_check_at: Optional[str] = None
    error: Optional[str] = None
    bad_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["format_version"] = STATUS_FORMAT_VERSION
        return data

    def mark_bad(self, tag: str) -> None:
        self.bad_tags = [existing for existing in self.bad_tags if existing != tag] + [tag]
        self.bad_tags = self.bad_tags[-MAX_BAD_TAGS:]


def read_check_status(path: Path) -> CheckStatus:
    """Missing or corrupt files read as a fresh status."""
    path = Path(path)
    if not path.is_file():
        return CheckStatus()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckStatus()
    if not isinstance(data, dict) or data.get("state") not in CHECK_STATES:
        return CheckStatus()
    bad_tags = data.get("bad_tags")
    available = data.get("available")
    return CheckStatus(
        state=data["state"],
        channel=data.get("channel") if isinstance(data.get("channel"), str) else None,
        available=available if isinstance(available, dict) else None,
        last_check_at=data.get("last_check_at")
        if isinstance(data.get("last_check_at"), str)
        else None,
        error=data.get("error") if isinstance(data.get("error"), str) else None,
        bad_tags=[tag for tag in bad_tags if isinstance(tag, str)]
        if isinstance(bad_tags, list)
        else [],
    )


def write_check_status(status: CheckStatus, path: Path) -> None:
    write_json_atomically(Path(path), status.to_dict(), indent=2)


def _identity(layout: InstallLayout, tree: Optional[Path]) -> Optional[dict]:
    if tree is None:
        return None
    info = layout.release_info_for(tree)
    if info is None:
        return {"tag": None, "version": None, "channel": "source", "name": tree.name}
    return {"tag": info.tag, "version": info.version, "channel": info.channel, "name": tree.name}


def compose_update_status(paths: UpdatePaths, installed: ReleaseInfo) -> dict:
    """The one status payload shared by ``openflight-update status`` and the kiosk."""
    layout = InstallLayout(paths.install_link, paths.releases_root)
    config = load_update_config(paths.config, installed)
    check = read_check_status(paths.status)
    managed = layout.is_managed()
    pending = read_pending(layout)
    staged = layout.staged_target() if managed else None
    previous = layout.previous_target() if managed else None

    if not managed:
        state = "unmanaged"
    elif not config.enabled:
        state = "disabled"
    elif pending is not None:
        state = "pending_confirm"
    elif staged is not None:
        state = "staged"
    else:
        state = check.state

    return {
        "format_version": STATUS_FORMAT_VERSION,
        "managed": managed,
        "state": state,
        "channel": config.channel,
        "repository": config.repository,
        "current": {
            "tag": installed.tag,
            "version": installed.version,
            "channel": installed.channel,
        },
        "available": check.available,
        "staged": _identity(layout, staged),
        "previous": _identity(layout, previous),
        "pending_confirm": pending is not None,
        "last_check_at": check.last_check_at,
        "error": check.error,
    }
