"""Where the updater keeps its files.

Every path resolves constructor argument, then environment variable, then the
user default (the same rule as ``profiles.py``), so tests and multiple
installs on one machine never share state.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

INSTALL_LINK_ENV = "OPENFLIGHT_INSTALL_LINK"
RELEASES_ROOT_ENV = "OPENFLIGHT_RELEASES_ROOT"
UPDATE_CONFIG_ENV = "OPENFLIGHT_UPDATE_CONFIG"
UPDATE_STATUS_ENV = "OPENFLIGHT_UPDATE_STATUS"

DEFAULT_INSTALL_LINK = Path.home() / "openflight"
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "openflight"

PathLike = Union[str, Path, None]


@dataclass(frozen=True)
class UpdatePaths:
    """The four locations the updater reads and writes."""

    install_link: Path
    releases_root: Path
    config: Path
    status: Path


def releases_root_for(install_link: Path) -> Path:
    """``~/openflight`` keeps its releases in ``~/openflight-releases``."""
    return install_link.with_name(install_link.name + "-releases")


def _pick(value: PathLike, env_name: str, default: Path) -> Path:
    if value is not None and str(value).strip():
        return Path(value).expanduser()
    env_value = (os.environ.get(env_name) or "").strip()
    if env_value:
        return Path(env_value).expanduser()
    return default


def resolve_update_paths(
    install_link: PathLike = None,
    releases_root: PathLike = None,
    config: PathLike = None,
    status: PathLike = None,
) -> UpdatePaths:
    """Resolve every updater path: argument, then environment, then default."""
    link = _pick(install_link, INSTALL_LINK_ENV, DEFAULT_INSTALL_LINK)
    return UpdatePaths(
        install_link=link,
        releases_root=_pick(releases_root, RELEASES_ROOT_ENV, releases_root_for(link)),
        config=_pick(config, UPDATE_CONFIG_ENV, DEFAULT_CONFIG_DIR / "update.json"),
        status=_pick(status, UPDATE_STATUS_ENV, DEFAULT_CONFIG_DIR / "update-status.json"),
    )


def optional_path(value: PathLike) -> Optional[Path]:
    """Normalise an optional CLI/env path value."""
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser()
