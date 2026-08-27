"""Persistent config/state for the openflight-update auto-updater.

Stored at ``~/.config/openflight/update.json``. Written by ``bootstrap``,
``check`` and ``apply``; read by every command. When the file is absent (or
``enabled`` is false) the updater is a no-op — this is how a plain developer
git clone stays untouched by default.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

DEFAULT_REPO = "jewbetcha/openflight"

CONFIG_PATH = Path.home() / ".config" / "openflight" / "update.json"
DEFAULT_RELEASES_DIR = Path.home() / "openflight-releases"
DEFAULT_INSTALL_DIR = Path.home() / "openflight"
DEFAULT_KEEP_RELEASES = 2


@dataclass
class UpdateConfig:
    """Auto-updater configuration + state persisted to disk."""

    enabled: bool = False
    repo: str = DEFAULT_REPO
    releases_dir: str = str(DEFAULT_RELEASES_DIR)
    install_dir: str = str(DEFAULT_INSTALL_DIR)
    keep_releases: int = DEFAULT_KEEP_RELEASES
    etag: str = ""
    active_tag: str = ""
    pending_tag: str = ""
    previous_tag: str = ""
    last_check_at: str = ""
    last_error: str = ""

    def releases_path(self) -> Path:
        return Path(self.releases_dir)

    def install_path(self) -> Path:
        return Path(self.install_dir)


def load_config(path: Path = CONFIG_PATH) -> Optional[UpdateConfig]:
    """Load config from ``path``; return None if the file is absent."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    defaults = UpdateConfig()
    return UpdateConfig(
        enabled=data.get("enabled", defaults.enabled),
        repo=data.get("repo", defaults.repo),
        releases_dir=data.get("releases_dir", defaults.releases_dir),
        install_dir=data.get("install_dir", defaults.install_dir),
        keep_releases=data.get("keep_releases", defaults.keep_releases),
        etag=data.get("etag", ""),
        active_tag=data.get("active_tag", ""),
        pending_tag=data.get("pending_tag", ""),
        previous_tag=data.get("previous_tag", ""),
        last_check_at=data.get("last_check_at", ""),
        last_error=data.get("last_error", ""),
    )


def save_config(config: UpdateConfig, path: Path = CONFIG_PATH) -> None:
    """Write config to ``path``, creating parent dirs as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, indent=2)
        handle.write("\n")
    os.chmod(path, 0o600)
