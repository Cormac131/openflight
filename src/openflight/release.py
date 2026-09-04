"""Identity of the running OpenFlight build: version, release channel, origin.

A release artifact ships ``release.json`` at the repository root, written by
``scripts/release/build_artifact.py`` from :class:`ReleaseInfo`. A plain git
checkout has no such file and reports the ``source`` channel with the base
version plus the short commit hash. Reading never raises: any problem with
the file is logged once and the source fallback is used, so the server, the
cloud uploader, and the session logger always get a usable answer.
"""

import functools
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import __version__

logger = logging.getLogger(__name__)

RELEASE_FILE_NAME = "release.json"
RELEASE_FORMAT_VERSION = 1
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_CHANNELS = ("stable", "experimental")
SOURCE_CHANNEL = "source"
GIT_TIMEOUT_S = 5


@dataclass(frozen=True)
class ReleaseInfo:
    """What build this process is, as shown to users and recorded in logs."""

    version: str
    base_version: str
    channel: str
    tag: Optional[str] = None
    commit: Optional[str] = None
    built_at: Optional[str] = None
    repository: Optional[str] = None

    @property
    def label(self) -> str:
        """Human-readable form, e.g. ``0.3.0-dev.42 (experimental)``."""
        return f"{self.version} ({self.channel})"

    def to_dict(self) -> dict:
        """Serializable form shared by ``release.json`` and the socket event."""
        return {
            "format_version": RELEASE_FORMAT_VERSION,
            "version": self.version,
            "base_version": self.base_version,
            "channel": self.channel,
            "tag": self.tag,
            "commit": self.commit,
            "built_at": self.built_at,
            "repository": self.repository,
        }


def _git_short_head(repo_root: Path) -> Optional[str]:
    if not (repo_root / ".git").exists() or shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def source_release_info(
    repo_root: Path = DEFAULT_REPO_ROOT, base_version: str = __version__
) -> ReleaseInfo:
    """Identity of a git checkout that was not produced by a release."""
    commit = _git_short_head(Path(repo_root))
    version = f"{base_version}+{commit}" if commit else base_version
    return ReleaseInfo(
        version=version, base_version=base_version, channel=SOURCE_CHANNEL, commit=commit
    )


def _optional_str(data: dict, key: str) -> Optional[str]:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _parse_release_file(data: Any, base_version: str) -> Optional[ReleaseInfo]:
    if not isinstance(data, dict):
        return None
    version = _optional_str(data, "version")
    channel = _optional_str(data, "channel")
    tag = _optional_str(data, "tag")
    if version is None or tag is None or channel not in RELEASE_CHANNELS:
        return None
    # A release.json left behind next to newer code must not claim the old
    # release; the base version pins the file to the code it was built from.
    if data.get("base_version") != base_version:
        return None
    return ReleaseInfo(
        version=version,
        base_version=base_version,
        channel=channel,
        tag=tag,
        commit=_optional_str(data, "commit"),
        built_at=_optional_str(data, "built_at"),
        repository=_optional_str(data, "repository"),
    )


def load_release_info(
    repo_root: Path = DEFAULT_REPO_ROOT, base_version: str = __version__
) -> ReleaseInfo:
    """Read ``release.json`` under ``repo_root``; fall back to the source identity."""
    repo_root = Path(repo_root)
    release_file = repo_root / RELEASE_FILE_NAME
    if not release_file.is_file():
        return source_release_info(repo_root, base_version)
    try:
        data = json.loads(release_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Ignoring unreadable %s: %s", release_file, error)
        return source_release_info(repo_root, base_version)
    info = _parse_release_file(data, base_version)
    if info is None:
        logger.warning(
            "Ignoring %s: not a valid release for base version %s", release_file, base_version
        )
        return source_release_info(repo_root, base_version)
    return info


@functools.lru_cache(maxsize=1)
def get_release_info() -> ReleaseInfo:
    """Identity of this process, resolved once."""
    return load_release_info()
