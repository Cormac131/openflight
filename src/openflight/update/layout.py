"""The release layout on disk: the install link and the releases root beside it.

``~/openflight`` is a symlink into ``~/openflight-releases/<name>/``. The
``staged`` and ``previous`` links and the ``pending-confirm`` marker are the
only record of what is ready, what to roll back to, and whether the last
swap has been confirmed; nothing duplicates them into a status file.
"""

import errno
import fcntl
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

from ..release import (
    DEFAULT_REPO_ROOT,
    RELEASE_FILE_NAME,
    ReleaseInfo,
    load_release_info,
    source_release_info,
)
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


class UpdateError(Exception):
    """An updater failure with a short machine-readable ``kind``."""

    def __init__(self, kind: str, message: str):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


class UpdateBusy(UpdateError):
    """Another updater command holds the releases lock."""

    def __init__(self, lock_path: Path):
        super().__init__("busy", f"another update command holds {lock_path}")


@contextmanager
def locked(layout: InstallLayout, wait_s: float = 0.0) -> Iterator[None]:
    """Hold the releases lock for a mutation; raise :class:`UpdateBusy` when taken."""
    layout.releases_root.mkdir(parents=True, exist_ok=True)
    fd = os.open(layout.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise UpdateBusy(layout.lock_path) from error
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


def replace_symlink(link: Path, target: Path) -> None:
    """Point ``link`` at ``target`` atomically (readers see the old or the new target)."""
    temporary = link.with_name(f".{link.name}.tmp")
    temporary.unlink(missing_ok=True)
    os.symlink(target, temporary)
    os.replace(temporary, link)


def remove_symlink(link: Path) -> None:
    if link.is_symlink():
        link.unlink()


def find_uv() -> Optional[str]:
    """``uv`` on PATH or in the user-local dirs the installer uses (systemd omits them)."""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv"):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def migrate_install(
    layout: InstallLayout,
    *,
    running_root: Path = DEFAULT_REPO_ROOT,
    run=subprocess.run,
) -> Path:
    """Move a real install directory under the releases root and replace it with a symlink.

    The moved tree keeps working in place: renames keep inodes, and the
    editable package is reinstalled so its recorded path is the physical
    directory rather than the link (which will later point elsewhere).
    """
    link = layout.install_link
    if link.is_symlink():
        target = layout.current_target()
        if target is None or not target.is_dir():
            raise UpdateError("dangling", f"{link} points at a missing directory")
        return target
    if not link.is_dir():
        raise UpdateError("missing", f"{link} is not a directory")
    try:
        same_tree = link.resolve() == Path(running_root).resolve()
    except OSError:
        same_tree = False
    if not same_tree:
        raise UpdateError("mismatch", f"{link} is not the checkout this command runs from")

    info = layout.release_info_for(link)
    if info is not None and info.tag:
        name = info.tag
    else:
        commit = source_release_info(link).commit
        name = f"{SOURCE_PREFIX}{commit or 'unknown'}"
    dest = layout.release_dir(name)
    if dest.exists():
        raise UpdateError("exists", f"{dest} already exists; move it aside first")
    layout.releases_root.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(link, dest)
    except OSError as error:
        if error.errno == errno.EXDEV:
            raise UpdateError(
                "cross_device", f"{layout.releases_root} must be on the same filesystem as {link}"
            ) from error
        raise UpdateError("rename", str(error)) from error
    os.symlink(dest, link)

    if (dest / ".venv" / "bin" / "python").exists():
        uv = find_uv()
        if uv is None:
            raise UpdateError(
                "deps", "uv not found; run `uv sync --reinstall-package openflight` in " + str(dest)
            )
        result = run([uv, "sync", "--reinstall-package", "openflight"], cwd=str(dest), check=False)
        if result.returncode != 0:
            raise UpdateError(
                "deps",
                f"`uv sync --reinstall-package openflight` failed in {dest} (exit {result.returncode})",
            )
    return dest


def prune(layout: InstallLayout) -> List[Path]:
    """Delete release trees no link points at, and download leftovers. Keeps ``source-*``."""
    keep = set()
    for target in (layout.current_target(), layout.staged_target(), layout.previous_target()):
        if target is not None:
            try:
                keep.add(target.resolve())
            except OSError:
                continue
    removed: List[Path] = []
    if not layout.releases_root.is_dir():
        return removed
    for child in sorted(layout.releases_root.iterdir()):
        if child.is_symlink() or not child.is_dir():
            continue
        if child.name == DOWNLOADS_DIR:
            for leftover in sorted(child.iterdir()):
                shutil.rmtree(leftover) if leftover.is_dir() else leftover.unlink()
                removed.append(leftover)
            continue
        if not child.name.startswith("v") or child.resolve() in keep:
            continue
        shutil.rmtree(child)
        removed.append(child)
    return removed
