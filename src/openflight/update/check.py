"""``openflight-update check``: find the release the channel should be on.

This module decides; it never swaps. A later stage (``stage.py``) downloads
and prepares the candidate, and the launcher applies it on the next start.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Tuple

from ..release import ReleaseInfo, get_release_info
from .config import load_update_config
from .github import GitHubReleases, ReleaseLookupError, RemoteRelease
from .paths import UpdatePaths
from .status import read_check_status, write_check_status
from .version import parse_version

logger = logging.getLogger(__name__)

WHY_NONE = "none"
WHY_CURRENT = "current"
WHY_HELD_BACK = "held_back"
WHY_NOT_NEWER = "not_newer"
WHY_NEWER = "newer"
WHY_CHANNEL_SWITCH = "channel_switch"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def select_candidate(
    installed: ReleaseInfo,
    channel: str,
    remote: Optional[RemoteRelease],
    bad_tags: Iterable[str],
) -> Tuple[Optional[RemoteRelease], str]:
    """Decide whether ``remote`` should replace the installed build.

    On the same channel only a strictly newer version qualifies. After a
    channel switch (or from a source checkout) the channel's latest qualifies
    even when it sorts lower, which is how a Pi moves from experimental back
    to stable.
    """
    if remote is None:
        return None, WHY_NONE
    if remote.tag == installed.tag:
        return None, WHY_CURRENT
    if remote.tag in set(bad_tags):
        return None, WHY_HELD_BACK
    if installed.channel != channel:
        return remote, WHY_CHANNEL_SWITCH
    current = parse_version(installed.version)
    if current is not None and remote.version <= current:
        return None, WHY_NOT_NEWER
    return remote, WHY_NEWER


def _available(remote: RemoteRelease) -> dict:
    return {
        "tag": remote.tag,
        "version": str(remote.version),
        "size": sum(asset.size for asset in remote.assets),
    }


def run_check(
    paths: UpdatePaths,
    *,
    installed: Optional[ReleaseInfo] = None,
    github_factory: Callable[[str], GitHubReleases] = GitHubReleases,
    now: Callable[[], str] = utc_now_iso,
) -> int:
    """Record what is available for the configured channel; exit code for the CLI."""
    installed = installed or get_release_info()
    config = load_update_config(paths.config, installed)
    status = read_check_status(paths.status)
    status.channel = config.channel
    status.error = None
    status.available = None

    if not config.enabled:
        status.state = "up_to_date"
        write_check_status(status, paths.status)
        logger.info("Automatic updates are off; nothing to check")
        return 0

    status.state = "checking"
    status.last_check_at = now()
    write_check_status(status, paths.status)
    try:
        remote = github_factory(config.repository).latest(config.channel)
    except ReleaseLookupError as error:
        status.state = "failed"
        status.error = str(error)
        write_check_status(status, paths.status)
        logger.warning("Release lookup failed: %s", error)
        return 0

    candidate, why = select_candidate(installed, config.channel, remote, status.bad_tags)
    if candidate is None:
        status.state = "held_back" if why == WHY_HELD_BACK else "up_to_date"
        if why == WHY_HELD_BACK and remote is not None:
            status.available = _available(remote)
        write_check_status(status, paths.status)
        logger.info("No update to stage (%s)", why)
        return 0

    status.state = "up_to_date"
    status.available = _available(candidate)
    write_check_status(status, paths.status)
    logger.info("%s is available on the %s channel (%s)", candidate.tag, config.channel, why)
    return 0
