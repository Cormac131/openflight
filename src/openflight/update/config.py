"""The user's update preference: which channel to follow, from which repository.

Stored at ``~/.config/openflight/update.json``. ``channel`` is ``None`` until
someone picks one (the kiosk menu or ``openflight-update set-channel``), which
keeps auto-update off for plain git checkouts.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..release import RELEASE_CHANNELS, ReleaseInfo
from ..startup_status import write_json_atomically
from . import DEFAULT_REPOSITORY

logger = logging.getLogger(__name__)

CONFIG_FORMAT_VERSION = 1


@dataclass(frozen=True)
class UpdateConfig:
    """Persisted preference."""

    channel: Optional[str]
    repository: str

    @property
    def enabled(self) -> bool:
        return self.channel in RELEASE_CHANNELS

    def to_dict(self) -> dict:
        return {
            "format_version": CONFIG_FORMAT_VERSION,
            "channel": self.channel,
            "repository": self.repository,
        }


def default_update_config(installed: ReleaseInfo) -> UpdateConfig:
    """Follow the installed build's channel when it came from a release, else stay off."""
    channel = installed.channel if installed.channel in RELEASE_CHANNELS else None
    return UpdateConfig(channel=channel, repository=installed.repository or DEFAULT_REPOSITORY)


def validate_channel(channel: Optional[str]) -> Optional[str]:
    """Return ``channel`` when it is a release channel or ``None``; raise otherwise."""
    if channel is None or channel in RELEASE_CHANNELS:
        return channel
    raise ValueError(f"channel must be one of {', '.join(RELEASE_CHANNELS)} or off")


def load_update_config(path: Path, installed: ReleaseInfo) -> UpdateConfig:
    """Read the preference; an absent or invalid file yields the defaults."""
    defaults = default_update_config(installed)
    path = Path(path)
    if not path.is_file():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        channel = validate_channel(data.get("channel"))
        repository = data.get("repository")
        if not isinstance(repository, str) or not repository.strip():
            repository = defaults.repository
    except (OSError, ValueError) as error:
        logger.warning("Ignoring invalid %s: %s", path, error)
        return defaults
    return UpdateConfig(channel=channel, repository=repository.strip())


def save_update_config(config: UpdateConfig, path: Path) -> None:
    """Persist the preference atomically."""
    validate_channel(config.channel)
    if not config.repository.strip():
        raise ValueError("repository must not be empty")
    write_json_atomically(Path(path), config.to_dict(), indent=2)
