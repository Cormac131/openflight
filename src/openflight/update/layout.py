"""The release layout on disk: the install link and the releases root beside it.

``~/openflight`` is a symlink into ``~/openflight-releases/<name>/``. The
``staged`` and ``previous`` links and the ``pending-confirm`` marker are the
only record of what is ready, what to roll back to, and whether the last
swap has been confirmed; nothing duplicates them into a status file.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..release import DEFAULT_REPO_ROOT, RELEASE_FILE_NAME, ReleaseInfo, load_release_info
from ..startup_status import write_json_atomically

STAGED_LINK = "staged"
PREVIOUS_LINK = "previous"
PENDING_CONFIRM = "pending-confirm"
DOWNLOADS_DIR = ".downloads"
LOCK_FILE = ".lock"
SOURCE_PREFIX = "source-"


def _link_target(link: Path) -> Optional[Path]:
    if not link.is_symlink():
        return None
    target = Path(os.readlink(link))
    return target if target.is_absolute() else link.parent / target


@dataclass(frozen=True)
class InstallLayout:
    """Paths derived from the install link and the releases root."""

    install_link: Path
    releases_root: Path

    @property
    def downloads(self) -> Path:
        return self.releases_root / DOWNLOADS_DIR

    @property
    def staged_link(self) -> Path:
        return self.releases_root / STAGED_LINK

    @property
    def previous_link(self) -> Path:
        return self.releases_root / PREVIOUS_LINK

    @property
    def lock_path(self) -> Path:
        return self.releases_root / LOCK_FILE

    @property
    def pending_confirm_path(self) -> Path:
        return self.releases_root / PENDING_CONFIRM

    def release_dir(self, name: str) -> Path:
        return self.releases_root / name

    def current_target(self) -> Optional[Path]:
        return _link_target(self.install_link)

    def staged_target(self) -> Optional[Path]:
        return _link_target(self.staged_link)

    def previous_target(self) -> Optional[Path]:
        return _link_target(self.previous_link)

    def is_managed(self, running_root: Path = DEFAULT_REPO_ROOT) -> bool:
        """True when the install link is a symlink that resolves to the running tree."""
        target = self.current_target()
        if target is None or not target.is_dir():
            return False
        try:
            return target.resolve() == Path(running_root).resolve()
        except OSError:
            return False

    def release_info_for(self, tree: Optional[Path]) -> Optional[ReleaseInfo]:
        """Identity of a release tree by its ``release.json`` (source trees have none)."""
        if tree is None or not (tree / RELEASE_FILE_NAME).is_file():
            return None
        version_file = tree / "src" / "openflight" / "__init__.py"
        try:
            base_version = _read_base_version(version_file)
        except (OSError, ValueError):
            return None
        info = load_release_info(tree, base_version)
        return info if info.tag else None


def _read_base_version(version_file: Path) -> str:
    for line in version_file.read_text(encoding="utf-8").splitlines():
        if line.startswith('__version__ = "') and line.endswith('"'):
            return line[len('__version__ = "') : -1]
    raise ValueError(f"no __version__ in {version_file}")


def read_pending(layout: InstallLayout) -> Optional[dict]:
    """The pending-confirm marker, or None when the last swap was confirmed."""
    path = layout.pending_confirm_path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("tag"), str) else None


def write_pending(
    layout: InstallLayout, tag: str, previous: Optional[str], applied_at: str
) -> None:
    write_json_atomically(
        layout.pending_confirm_path,
        {"format_version": 1, "tag": tag, "previous": previous, "applied_at": applied_at},
        indent=2,
    )


def clear_pending(layout: InstallLayout) -> None:
    layout.pending_confirm_path.unlink(missing_ok=True)
