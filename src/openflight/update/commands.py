"""Command orchestration for the openflight-update CLI.

Each function takes its dependencies explicitly (config, client, deploy
module, clock, output sink) so the logic is testable without the network,
subprocess, or a real filesystem layout. This mirrors ``cloud/commands.py``.
"""

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import deploy as deploy_module
from .client import RateLimited, ReleaseSchemaError, UpdateNetworkError, is_newer
from .config import UpdateConfig, save_config

OutFn = Callable[[str], None]
NowFn = Callable[[], str]

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _keep_tags(config: UpdateConfig) -> set:
    return {tag for tag in (config.active_tag, config.previous_tag, config.pending_tag) if tag}


def _prune(config: UpdateConfig, deploy, out: OutFn) -> None:
    removed = deploy.prune(config.releases_path(), _keep_tags(config))
    if removed:
        out(f"Pruned old release(s): {', '.join(sorted(removed))}")


def cmd_check(
    config: UpdateConfig,
    config_path: Path,
    client,
    deploy=deploy_module,
    now_fn: NowFn = _now_iso,
    out: OutFn = print,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Check GitHub for a newer release and, if found, prepare it in the
    background (download, deps, UI, smoke test) — never touches the live
    install. Idempotent: re-running with an already-prepared candidate is a
    cheap no-op."""
    summary: Dict[str, Any] = {"prepared": None, "up_to_date": False, "error": None}

    if not config.enabled and not dry_run:
        out("Auto-update disabled (no ~/.config/openflight/update.json, or enabled=false).")
        return summary

    try:
        result = client.get_latest_release(etag=config.etag or None)
    except (RateLimited, UpdateNetworkError, ReleaseSchemaError) as exc:
        out(f"Release check failed: {exc}")
        config.last_error = str(exc)
        config.last_check_at = now_fn()
        if not dry_run:
            save_config(config, config_path)
        summary["error"] = str(exc)
        return summary

    config.last_check_at = now_fn()
    if result.etag:
        config.etag = result.etag

    if result.not_modified or result.release is None:
        out("Up to date (no new release).")
        summary["up_to_date"] = True
        if not dry_run:
            save_config(config, config_path)
        return summary

    candidate_tag = result.release.tag

    if candidate_tag == config.active_tag:
        out(f"Up to date (active release is already {candidate_tag}).")
        summary["up_to_date"] = True
        if not dry_run:
            save_config(config, config_path)
        return summary

    if not is_newer(candidate_tag, config.active_tag):
        out(
            f"Latest release {candidate_tag} is not newer than "
            f"active {config.active_tag!r}; skipping."
        )
        if not dry_run:
            save_config(config, config_path)
        return summary

    dest = config.releases_path() / candidate_tag
    if candidate_tag == config.pending_tag and dest.is_dir():
        out(f"{candidate_tag} is already prepared and pending apply.")
        summary["prepared"] = candidate_tag
        if not dry_run:
            save_config(config, config_path)
        return summary

    if dry_run:
        out(f"Would prepare release {candidate_tag} (dry run).")
        summary["prepared"] = candidate_tag
        return summary

    out(f"Preparing release {candidate_tag}...")
    try:
        tarball = client.download(result.release.tarball_url)
        deploy.extract_release(tarball, dest)

        active_dir = config.releases_path() / config.active_tag if config.active_tag else None
        if active_dir and active_dir.is_dir():
            carried = deploy.copy_local_config(active_dir, dest)
            if carried:
                out(f"Carried forward local config: {', '.join(carried)}")

        deploy.install_deps(dest)

        if result.release.ui_dist_url:
            ui_bytes = client.download(result.release.ui_dist_url)
            deploy.install_ui(ui_bytes, dest)

        deploy.smoke_test(dest)
    except (UpdateNetworkError, deploy_module.DeployError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        out(f"Failed to prepare {candidate_tag}: {exc}")
        config.last_error = str(exc)
        save_config(config, config_path)
        summary["error"] = str(exc)
        return summary

    config.pending_tag = candidate_tag
    config.last_error = ""
    save_config(config, config_path)
    _prune(config, deploy, out)
    out(f"{candidate_tag} prepared and pending apply (at next restart).")
    summary["prepared"] = candidate_tag
    return summary


def cmd_apply(
    config: UpdateConfig,
    config_path: Path,
    deploy=deploy_module,
    out: OutFn = print,
    tag: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Swap the prepared (or explicitly given) release in as active. Meant to
    be called once, early, on every restart — a no-op when nothing is
    pending."""
    target_tag = tag or config.pending_tag
    summary: Dict[str, Any] = {"swapped": False, "tag": None, "error": None}

    if not target_tag:
        return summary

    candidate_dir = config.releases_path() / target_tag
    if not candidate_dir.is_dir():
        message = f"pending release {target_tag} missing on disk"
        out(f"Pending release {target_tag} has no prepared directory; clearing.")
        config.last_error = message
        if target_tag == config.pending_tag:
            config.pending_tag = ""
        if not dry_run:
            save_config(config, config_path)
        summary["error"] = message
        return summary

    if dry_run:
        out(f"Would apply {target_tag} (was {config.active_tag or '(none)'}) (dry run).")
        summary["swapped"] = True
        summary["tag"] = target_tag
        return summary

    previous_tag = config.active_tag
    deploy.swap_active(config.install_path(), candidate_dir)

    config.previous_tag = previous_tag
    config.active_tag = target_tag
    if target_tag == config.pending_tag:
        config.pending_tag = ""
    config.last_error = ""
    save_config(config, config_path)
    _prune(config, deploy, out)

    out(f"Applied {target_tag} (was {previous_tag or '(none)'}).")
    summary["swapped"] = True
    summary["tag"] = target_tag
    return summary


def cmd_rollback(
    config: UpdateConfig,
    config_path: Path,
    deploy=deploy_module,
    out: OutFn = print,
) -> bool:
    """Swap back to the previous release. Called by start-kiosk.sh when the
    server fails its startup health check right after an apply."""
    if not config.previous_tag:
        out("No previous release recorded; cannot roll back.")
        return False

    prev_dir = config.releases_path() / config.previous_tag
    if not prev_dir.is_dir():
        out(f"Previous release {config.previous_tag} is missing on disk; cannot roll back.")
        return False

    failed_tag = config.active_tag
    deploy.swap_active(config.install_path(), prev_dir)

    config.active_tag = config.previous_tag
    config.previous_tag = ""
    config.last_error = f"rolled back from {failed_tag} (failed post-update health check)"
    save_config(config, config_path)
    _prune(config, deploy, out)

    out(f"Rolled back to {config.active_tag} ({failed_tag} failed to start).")
    return True


def cmd_status(config: UpdateConfig, out: OutFn = print) -> Dict[str, Any]:
    """Report current auto-update state."""
    out(f"Repo:      {config.repo}")
    out(f"Enabled:   {'yes' if config.enabled else 'no'}")
    out(f"Active:    {config.active_tag or '(none)'}")
    out(f"Pending:   {config.pending_tag or '(none)'}")
    out(f"Previous:  {config.previous_tag or '(none)'} (rollback target)")
    out(f"Last check: {config.last_check_at or 'never'}")
    if config.last_error:
        out(f"Last error: {config.last_error}")
    return {
        "repo": config.repo,
        "enabled": config.enabled,
        "active_tag": config.active_tag,
        "pending_tag": config.pending_tag,
        "previous_tag": config.previous_tag,
        "last_check_at": config.last_check_at,
        "last_error": config.last_error,
    }


def _read_pyproject_version(pyproject_path: Path) -> str:
    match = _VERSION_RE.search(pyproject_path.read_text())
    if not match:
        raise ValueError(f"could not find version in {pyproject_path}")
    return match.group(1)


def _git_is_clean(repo_dir: Path, run_fn=subprocess.run) -> bool:
    try:
        result = run_fn(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() == ""


def cmd_bootstrap(
    config: UpdateConfig,
    config_path: Path,
    out: OutFn = print,
    run_fn=subprocess.run,
) -> bool:
    """One-time migration of an existing plain ``git clone`` install into the
    release-directory + symlink layout auto-update needs. Idempotent; aborts
    rather than discarding anything unexpected."""
    install_dir = config.install_path()

    if install_dir.is_symlink():
        out(f"Already bootstrapped (active={config.active_tag or 'unknown'}).")
        return True

    if not install_dir.is_dir():
        out(f"Install dir does not exist: {install_dir}")
        return False

    if not (install_dir / ".git").exists():
        out(f"{install_dir} is not a git checkout; refusing to guess how to bootstrap it.")
        return False

    if not _git_is_clean(install_dir, run_fn):
        out(
            f"{install_dir} has uncommitted changes (git status is not clean). "
            "Commit or stash them before enabling auto-update, so bootstrap "
            "never silently discards work."
        )
        return False

    try:
        version = _read_pyproject_version(install_dir / "pyproject.toml")
    except (OSError, ValueError) as exc:
        out(f"Could not determine current version: {exc}")
        return False

    tag = f"v{version}"
    dest = config.releases_path() / tag
    if dest.exists():
        out(f"Release dir already exists, refusing to overwrite: {dest}")
        return False

    config.releases_path().mkdir(parents=True, exist_ok=True)
    shutil.move(str(install_dir), str(dest))
    install_dir.symlink_to(dest, target_is_directory=True)

    config.active_tag = tag
    config.pending_tag = ""
    config.previous_tag = ""
    config.enabled = True
    config.last_error = ""
    save_config(config, config_path)

    out(f"Bootstrapped: {install_dir} -> {dest} (active={tag}). Auto-update enabled.")
    return True
