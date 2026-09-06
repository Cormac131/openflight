"""``openflight-update check``: find the release the channel should be on.

This module decides; it never swaps. A later stage (``stage.py``) downloads
and prepares the candidate, and the launcher applies it on the next start.
"""

import logging
import subprocess
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Tuple

from ..release import ReleaseInfo, get_release_info
from .config import load_update_config
from .github import GitHubReleases, ReleaseLookupError, RemoteRelease
from .layout import InstallLayout, UpdateBusy, locked, prune, remove_symlink
from .paths import UpdatePaths
from .stage import StageError, stage_release
from .status import CheckStatus, read_check_status, write_check_status
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
    run: Callable = subprocess.run,
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
    layout = InstallLayout(paths.install_link, paths.releases_root)
    managed = layout.is_managed()
    if candidate is None:
        status.state = "held_back" if why == WHY_HELD_BACK else "up_to_date"
        if why == WHY_HELD_BACK and remote is not None:
            status.available = _available(remote)
        write_check_status(status, paths.status)
        if managed:
            _drop_stale_staged(layout, keep=None)
        logger.info("No update to stage (%s)", why)
        return 0

    status.available = _available(candidate)
    if not managed:
        status.state = "up_to_date"
        write_check_status(status, paths.status)
        logger.info("%s is available but this install is not managed; not staging", candidate.tag)
        return 0
    return _stage(layout, paths, status, candidate, config.repository, github_factory, run)


def _drop_stale_staged(layout: InstallLayout, keep: Optional[str]) -> None:
    staged = layout.staged_target()
    if staged is None or staged.name == keep:
        return
    try:
        with locked(layout):
            remove_symlink(layout.staged_link)
            prune(layout)
    except UpdateBusy:
        return
    logger.info("Dropped stale staged release %s", staged.name)


def _stage(layout, paths, status: CheckStatus, candidate, repository, github_factory, run) -> int:
    staged = layout.staged_target()
    if staged is not None and staged.name == candidate.tag and staged.is_dir():
        status.state = "up_to_date"
        write_check_status(status, paths.status)
        logger.info("%s is already staged", candidate.tag)
        return 0
    _drop_stale_staged(layout, keep=candidate.tag)

    def on_state(state: str) -> None:
        status.state = state
        write_check_status(status, paths.status)

    try:
        with locked(layout):
            stage_release(layout, candidate, github_factory(repository), run=run, on_state=on_state)
    except UpdateBusy as error:
        logger.info("Skipping: %s", error)
        status.state = "up_to_date"
        write_check_status(status, paths.status)
        return 0
    except StageError as error:
        status.state = "failed"
        status.error = str(error)
        if error.kind == "unsupported_release":
            status.mark_bad(candidate.tag)
        write_check_status(status, paths.status)
        logger.error("Staging %s failed: %s", candidate.tag, error)
        return 1
    status.state = "up_to_date"
    write_check_status(status, paths.status)
    logger.info("%s is staged and will be applied on the next start", candidate.tag)
    return 0
