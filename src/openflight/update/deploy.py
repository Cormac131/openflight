"""Filesystem/subprocess mechanics for applying a release.

Release-directory + symlink-swap layout::

    ~/openflight-releases/v0.3.0/   <- extracted release, own .venv, own ui/dist
    ~/openflight-releases/v0.2.0/   <- previous release, kept for rollback
    ~/openflight -> ~/openflight-releases/v0.3.0   <- symlink systemd/start-kiosk.sh use

``commands.py`` owns policy (which tag is newer, which to keep, when to swap);
this module only does the mechanical, individually-testable steps. Every
function here is a *tool*, not an orchestrator — none of them read or write
``UpdateConfig`` state.
"""

import re
import shutil
import subprocess
import tarfile
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Set

_LOCAL_CONFIG_RE = re.compile(r"^config/(?!.*[*])(?!.*\.example)\S+$")

SMOKE_TEST_TIMEOUT_S = 15
UV_SYNC_TIMEOUT_S = 600


class DeployError(Exception):
    """Raised for any failure while preparing or applying a release.

    Always safe to treat as "the candidate release did not become active" —
    functions in this module never mutate the active install on failure.
    """


def local_config_paths(repo_dir: Path) -> List[str]:
    """Repo-relative paths under ``config/`` that are gitignored (and not a
    ``*.example.*`` template) — the local, per-device files that must be
    carried forward across a release swap instead of coming from the tarball.

    Parsed statically from ``.gitignore`` (not ``git status``): release
    tarballs never include ``.git``, so this must work without one.
    """
    gitignore = repo_dir / ".gitignore"
    if not gitignore.exists():
        return []
    paths = []
    for line in gitignore.read_text().splitlines():
        line = line.strip()
        if _LOCAL_CONFIG_RE.match(line):
            paths.append(line)
    return paths


def copy_local_config(from_dir: Path, to_dir: Path) -> List[str]:
    """Copy forward local config files from the active release into a newly
    extracted candidate. Missing sources (e.g. first-ever release) are
    skipped, not an error. Returns the paths actually copied."""
    copied = []
    for rel_path in local_config_paths(to_dir):
        src = from_dir / rel_path
        if not src.exists():
            continue
        dest = to_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(rel_path)
    return copied


def _safe_extract(tar: tarfile.TarFile, dest: Path, members=None) -> None:
    """Extract ``tar`` into ``dest``, rejecting members that would escape it.

    ``filter="data"`` (3.12+) additionally strips devices/setuid bits etc.;
    older Pythons fall back to a plain extract after the same path check.
    """
    members = tar.getmembers() if members is None else members
    dest = dest.resolve()
    for member in members:
        target = (dest / member.name).resolve()
        if target != dest and dest not in target.parents:
            raise DeployError(f"refusing to extract unsafe path: {member.name}")
    try:
        tar.extractall(dest, members=members, filter="data")  # type: ignore[call-arg]
    except TypeError:
        tar.extractall(dest, members=members)


def extract_release(tarball_bytes: bytes, dest_dir: Path) -> None:
    """Extract a GitHub source tarball into ``dest_dir``, stripping the single
    top-level directory GitHub always adds (``{owner}-{repo}-{sha}/``).

    Raises DeployError on a corrupted/truncated download, a disk-full mid
    extract, or if ``dest_dir`` already exists. On any failure after
    ``dest_dir`` is created, it is removed again so a partial extract never
    lingers as a candidate for a future swap.
    """
    if dest_dir.exists():
        raise DeployError(f"release dir already exists: {dest_dir}")
    dest_dir.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=BytesIO(tarball_bytes), mode="r:gz") as tar:
            members = tar.getmembers()
            if not members:
                raise DeployError("release tarball is empty")
            top = members[0].name.split("/", 1)[0]
            for member in members:
                if not member.name.startswith(f"{top}/") and member.name != top:
                    raise DeployError(f"unexpected top-level entry in tarball: {member.name}")
                member.name = member.name[len(top) + 1 :] or "."
            members = [m for m in members if m.name != "."]
            _safe_extract(tar, dest_dir, members=members)
    except (DeployError, tarfile.TarError, EOFError, OSError) as exc:
        shutil.rmtree(dest_dir, ignore_errors=True)
        if isinstance(exc, DeployError):
            raise
        raise DeployError(f"failed to extract release tarball: {exc}") from exc


def install_ui(ui_dist_bytes: Optional[bytes], release_dir: Path) -> bool:
    """Unpack a prebuilt ``ui-dist.tar.gz`` (containing a top-level ``dist/``)
    into ``release_dir/ui/``. Returns False (no-op) if no asset was published
    for this release — start-kiosk.sh already falls back to building the UI
    on-device when ``ui/dist`` is missing, so this degrades gracefully."""
    if ui_dist_bytes is None:
        return False
    ui_dir = release_dir / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=BytesIO(ui_dist_bytes), mode="r:gz") as tar:
            _safe_extract(tar, ui_dir)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise DeployError(f"failed to extract ui-dist.tar.gz: {exc}") from exc
    return True


def install_deps(release_dir: Path) -> None:
    """Run ``uv sync`` inside ``release_dir`` to build its own ``.venv``."""
    try:
        subprocess.run(
            ["uv", "sync", "--quiet"],
            cwd=release_dir,
            check=True,
            capture_output=True,
            timeout=UV_SYNC_TIMEOUT_S,
            text=True,
        )
    except FileNotFoundError as exc:
        raise DeployError("uv not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeployError(f"uv sync timed out after {UV_SYNC_TIMEOUT_S}s") from exc
    except subprocess.CalledProcessError as exc:
        raise DeployError(f"uv sync failed: {exc.stderr or exc.stdout}") from exc


def smoke_test(release_dir: Path) -> None:
    """Lightweight, hardware-free validation that the candidate release's
    package and server module at least import cleanly.

    Deliberately does *not* touch the radar/serial hardware: the running
    (still-active) server holds those devices open, so a real health check
    can only happen after the symlink swap + restart, from start-kiosk.sh.
    """
    python = release_dir / ".venv" / "bin" / "python"
    if not python.exists():
        raise DeployError(f"no venv python at {python}")
    try:
        subprocess.run(
            [str(python), "-c", "import openflight.server"],
            cwd=release_dir,
            check=True,
            capture_output=True,
            timeout=SMOKE_TEST_TIMEOUT_S,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeployError("smoke test timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise DeployError(f"smoke test failed: {exc.stderr or exc.stdout}") from exc


def swap_active(install_dir: Path, release_dir: Path) -> None:
    """Atomically point ``install_dir`` (a symlink) at ``release_dir``."""
    if not release_dir.is_dir():
        raise DeployError(f"release dir does not exist: {release_dir}")
    tmp_link = install_dir.with_name(install_dir.name + ".tmp-swap")
    tmp_link.unlink(missing_ok=True)
    try:
        tmp_link.symlink_to(release_dir, target_is_directory=True)
        tmp_link.replace(install_dir)
    except OSError as exc:
        tmp_link.unlink(missing_ok=True)
        raise DeployError(f"failed to swap active release: {exc}") from exc


def prune(releases_dir: Path, keep_tags: Set[str]) -> List[str]:
    """Delete release directories under ``releases_dir`` not in ``keep_tags``.

    Never deletes anything ``keep_tags`` names, no matter how that set was
    computed — the caller (commands.py) is responsible for always including
    the active (and, while one exists, previous) release.
    """
    if not releases_dir.is_dir():
        return []
    removed = []
    for entry in releases_dir.iterdir():
        if entry.is_dir() and entry.name not in keep_tags:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed
