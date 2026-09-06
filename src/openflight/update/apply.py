"""Swap the install link to the staged release, confirm it, or roll it back.

``apply`` writes the pending-confirm marker before touching any link, so a
power loss mid-way leaves a state the next start can finish (re-apply is
idempotent for the same tag) and a failed start can undo (rollback).
"""

import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

from .check import utc_now_iso
from .layout import (
    InstallLayout,
    UpdateError,
    clear_pending,
    locked,
    prune,
    read_pending,
    remove_symlink,
    replace_symlink,
    write_pending,
)
from .status import read_check_status, write_check_status

logger = logging.getLogger(__name__)

CARRIED_FILES = ("config/sim.json",)


def _carry_over(current: Optional[Path], new: Path) -> None:
    if current is None:
        return
    for relative in CARRIED_FILES:
        source = current / relative
        target = new / relative
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def apply_staged(layout: InstallLayout, *, now: Callable[[], str] = utc_now_iso) -> Optional[str]:
    """Point the install link at the staged tree; return its name, or None when nothing is staged."""
    with locked(layout):
        staged = layout.staged_target()
        if staged is None:
            return None
        current = layout.current_target()
        if current is None:
            raise UpdateError("unmanaged", f"{layout.install_link} is not a symlink")
        if not staged.is_dir():
            remove_symlink(layout.staged_link)
            raise UpdateError("dangling", f"staged link points at missing {staged}")
        if staged.resolve() == current.resolve():
            remove_symlink(layout.staged_link)
            return None
        pending = read_pending(layout)
        if pending is not None and pending.get("tag") != staged.name:
            raise UpdateError("pending", f"{pending.get('tag')} is still awaiting confirmation")
        if pending is None:
            write_pending(layout, staged.name, current.name, now())
        _carry_over(current, staged)
        replace_symlink(layout.previous_link, current)
        replace_symlink(layout.install_link, staged)
        remove_symlink(layout.staged_link)
        logger.info("Applied %s (previous: %s)", staged.name, current.name)
        return staged.name


def confirm_applied(layout: InstallLayout) -> bool:
    """Clear the pending marker after a successful start; True when there was one."""
    with locked(layout):
        if read_pending(layout) is None:
            return False
        clear_pending(layout)
        prune(layout)
        return True


def rollback_pending(
    layout: InstallLayout,
    status_path: Path,
    *,
    reason: str,
    force: bool = False,
) -> Optional[str]:
    """Return to ``previous`` and remember the failed tag; None when nothing was pending."""
    with locked(layout):
        pending = read_pending(layout)
        if pending is None and not force:
            return None
        previous = layout.previous_target()
        if previous is None or not previous.is_dir():
            raise UpdateError("no_previous", "no previous release to roll back to")
        current = layout.current_target()
        failed = pending["tag"] if pending else (current.name if current else "unknown")
        replace_symlink(layout.install_link, previous)
        remove_symlink(layout.previous_link)
        status = read_check_status(status_path)
        status.mark_bad(failed)
        status.state = "failed"
        status.error = f"startup_failed: {reason}"
        write_check_status(status, status_path)
        clear_pending(layout)
        logger.warning("Rolled back %s to %s: %s", failed, previous.name, reason)
        return failed
