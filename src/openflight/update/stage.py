"""Download, verify and prepare a release beside the current install.

A staged tree is complete only once its own ``.venv`` runs
``openflight-server --version``; the ``staged`` link is created last, so a
tree without that link is garbage the next prune removes. Everything is
built in the tree's final directory because ``uv`` records absolute paths.
"""

import logging
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Callable, Optional

from ..release import DEFAULT_REPO_ROOT, RELEASE_FILE_NAME
from .github import DownloadError, GitHubReleases, RemoteRelease
from .layout import InstallLayout, UpdateError, find_uv, replace_symlink

logger = logging.getLogger(__name__)

MIN_FREE_BYTES = 1024**3
FREE_SPACE_FACTOR = 3
# A release older than the updater cannot confirm or roll back a swap; never stage one.
REQUIRED_FILES = ("src/openflight/update/__init__.py", "scripts/kiosk-update.sh")
SYSTEM_PYTHON = "/usr/bin/python3"


class StageError(UpdateError):
    """Staging failed; the partial tree has been removed."""


def artifact_names(tag: str):
    tarball = f"openflight-{tag}.tar.gz"
    return tarball, tarball + ".sha256"


def is_complete(tree: Path) -> bool:
    """A tree the launcher could start: identity file plus a working venv script."""
    return (tree / RELEASE_FILE_NAME).is_file() and (
        tree / ".venv" / "bin" / "openflight-server"
    ).exists()


def _parse_sidecar(text: str, tarball_name: str) -> str:
    parts = text.split()
    if len(parts) < 2 or parts[1].lstrip("*") != tarball_name or len(parts[0]) != 64:
        raise StageError("checksum", f"unexpected checksum file for {tarball_name}")
    return parts[0].lower()


def _safe_members(archive: tarfile.TarFile, prefix: str):
    for member in archive.getmembers():
        name = member.name
        inside = name.startswith(prefix) or name == prefix.rstrip("/")
        if name.startswith("/") or ".." in Path(name).parts or not inside:
            raise StageError("archive", f"refusing archive member {name!r}")
        if member.issym() or member.islnk():
            raise StageError("archive", f"refusing link member {name!r}")
        yield member


def _extract(tarball: Path, extract_dir: Path, prefix: str) -> Path:
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    try:
        with tarfile.open(tarball) as archive:
            members = list(_safe_members(archive, prefix))
            if hasattr(tarfile, "data_filter"):
                archive.extractall(extract_dir, members=members, filter="data")
            else:  # pragma: no cover - Python < 3.12
                archive.extractall(extract_dir, members=members)
    except (tarfile.TarError, EOFError, OSError) as error:
        raise StageError("archive", f"{tarball.name}: {error}") from error
    entries = list(extract_dir.iterdir())
    if len(entries) != 1 or entries[0].name != prefix.rstrip("/") or not entries[0].is_dir():
        raise StageError(
            "archive", f"{tarball.name} does not unpack to a single {prefix} directory"
        )
    return entries[0]


def _lockfiles_match(current: Optional[Path], new: Path) -> bool:
    if current is None:
        return False
    old_lock = current / "ui" / "package-lock.json"
    new_lock = new / "ui" / "package-lock.json"
    try:
        return old_lock.read_bytes() == new_lock.read_bytes()
    except OSError:
        return False


def _prepare_node_modules(current: Optional[Path], new: Path, run) -> None:
    source = current / "ui" / "node_modules" if current else None
    if source is not None and source.is_dir() and _lockfiles_match(current, new):
        shutil.copytree(
            source, new / "ui" / "node_modules", symlinks=True, copy_function=_link_or_copy
        )
        return
    try:
        result = run(["npm", "ci", "--no-audit", "--no-fund"], cwd=str(new / "ui"), check=False)
    except OSError as error:
        logger.warning(
            "npm unavailable (%s); Electron stays uninstalled until the launcher installs it", error
        )
        return
    if result.returncode != 0:
        logger.warning(
            "npm ci failed (exit %s); the kiosk falls back to Chromium", result.returncode
        )


def _link_or_copy(src: str, dst: str) -> None:
    try:
        shutil.os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _uses_system_site_packages(current: Optional[Path]) -> bool:
    if current is None:
        return False
    try:
        text = (current / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.replace(" ", "").lower() == "include-system-site-packages=true"
        for line in text.splitlines()
    )


def _prepare_venv(current: Optional[Path], new: Path, run) -> None:
    uv = find_uv()
    if uv is None:
        raise StageError("deps", "uv not found on PATH, ~/.local/bin or ~/.cargo/bin")
    sync = [uv, "sync", "--locked"]
    env = None
    if _uses_system_site_packages(current):
        created = run(
            [uv, "venv", "--system-site-packages", "--python", SYSTEM_PYTHON, str(new / ".venv")],
            cwd=str(new),
            check=False,
        )
        if created.returncode != 0:
            raise StageError("deps", f"uv venv failed (exit {created.returncode})")
        sync += ["--extra", "camera"]
        env = {"UV_PYTHON": SYSTEM_PYTHON}
    result = run(sync, cwd=str(new), check=False, env=env)
    if result.returncode != 0:
        raise StageError("deps", f"uv sync --locked failed (exit {result.returncode})")


def _smoke_test(new: Path, version: str, run) -> None:
    server = new / ".venv" / "bin" / "openflight-server"
    try:
        result = run(
            [str(server), "--version"], cwd=str(new), check=False, capture_output=True, text=True
        )
    except OSError as error:
        raise StageError("deps", f"{server} could not run: {error}") from error
    output = (result.stdout or "") + (result.returncode and (result.stderr or "") or "")
    if result.returncode != 0 or version not in output:
        raise StageError(
            "deps", f"{server.name} --version failed (exit {result.returncode}): {output.strip()}"
        )


def stage_release(
    layout: InstallLayout,
    release: RemoteRelease,
    github: GitHubReleases,
    *,
    run: Callable = subprocess.run,
    on_state: Callable[[str], None] = lambda state: None,
    disk_usage: Callable[[str], object] = shutil.disk_usage,
    running_root: Path = DEFAULT_REPO_ROOT,
) -> Path:
    """Make ``release`` ready to apply and point the ``staged`` link at it."""
    tag = release.tag
    tarball_name, sidecar_name = artifact_names(tag)
    tarball_asset = release.asset(tarball_name)
    sidecar_asset = release.asset(sidecar_name)
    if tarball_asset is None or sidecar_asset is None:
        raise StageError("download", f"{tag} has no {tarball_name} + .sha256 assets")

    dest = layout.release_dir(tag)
    if is_complete(dest):
        replace_symlink(layout.staged_link, dest)
        return dest

    layout.downloads.mkdir(parents=True, exist_ok=True)
    usage = disk_usage(str(layout.releases_root))
    needed = tarball_asset.size * FREE_SPACE_FACTOR + MIN_FREE_BYTES
    if usage.free < needed:
        raise StageError("disk", f"{usage.free} bytes free, {needed} needed for {tag}")

    on_state("downloading")
    tarball = layout.downloads / tarball_name
    sidecar = layout.downloads / sidecar_name
    try:
        digest = github.download(tarball_asset, tarball)
        github.download(sidecar_asset, sidecar)
    except DownloadError as error:
        raise StageError("download", str(error)) from error
    expected = _parse_sidecar(sidecar.read_text(encoding="utf-8"), tarball_name)
    if digest != expected:
        tarball.unlink(missing_ok=True)
        raise StageError("checksum", f"{tarball_name} sha256 {digest} != {expected}")

    on_state("staging")
    current = layout.current_target()
    unpacked = _extract(tarball, layout.downloads / f"extract-{tag}", f"openflight-{tag}/")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(unpacked), str(dest))
    shutil.rmtree(unpacked.parent, ignore_errors=True)
    try:
        missing = [name for name in REQUIRED_FILES if not (dest / name).exists()]
        if missing:
            raise StageError(
                "unsupported_release", f"{tag} predates the updater (missing {missing[0]})"
            )
        _prepare_node_modules(current, dest, run)
        _prepare_venv(current, dest, run)
        _smoke_test(dest, str(release.version), run)
    except StageError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        tarball.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)

    replace_symlink(layout.staged_link, dest)
    logger.info("Staged %s at %s (running from %s)", tag, dest, running_root)
    return dest
