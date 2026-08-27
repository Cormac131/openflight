"""Tests for openflight-update command orchestration.

Exercises cmd_check/cmd_apply/cmd_rollback/cmd_status/cmd_bootstrap against a
fake GitHub client and the real deploy module operating on tmp_path (deploy's
own mechanics are covered by test_update_deploy.py; here we're testing the
policy/orchestration layer: idempotency, error handling leaving the active
release untouched, rollback bookkeeping, bootstrap safety).
"""

import subprocess
import tarfile
from io import BytesIO

import pytest

from openflight.update import commands, deploy
from openflight.update.client import (
    CheckResult,
    RateLimited,
    ReleaseInfo,
    UpdateNetworkError,
)
from openflight.update.config import UpdateConfig


def _make_tarball(files: dict, top_level: str = "owner-repo-abc") -> bytes:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel_path, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name=f"{top_level}/{rel_path}")
            info.size = len(data)
            tar.addfile(info, BytesIO(data))
    return buf.getvalue()


class FakeClient:
    """Stands in for GitHubReleaseClient: queued check results + asset bytes."""

    def __init__(self, check_result=None, check_error=None, assets=None):
        self._check_result = check_result
        self._check_error = check_error
        self._assets = assets or {}
        self.download_calls = []

    def get_latest_release(self, etag=None):
        if self._check_error:
            raise self._check_error
        return self._check_result

    def download(self, url):
        self.download_calls.append(url)
        return self._assets[url]


def _config(tmp_path, **overrides):
    defaults = dict(
        enabled=True,
        repo="o/r",
        releases_dir=str(tmp_path / "releases"),
        install_dir=str(tmp_path / "openflight"),
    )
    defaults.update(overrides)
    return UpdateConfig(**defaults)


def _release(tag="v0.2.0", ui=True):
    return ReleaseInfo(
        tag=tag,
        tarball_url=f"https://example.test/{tag}.tar.gz",
        notes="notes",
        published_at="2026-01-01T00:00:00Z",
        ui_dist_url=f"https://example.test/{tag}-ui.tar.gz" if ui else None,
    )


def _tarball_for(tag):
    return _make_tarball({"pyproject.toml": f'version = "{tag.lstrip("v")}"\n'})


def _ui_tarball():
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"<html></html>"
        info = tarfile.TarInfo(name="dist/index.html")
        info.size = len(data)
        tar.addfile(info, BytesIO(data))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _fake_uv_sync(monkeypatch):
    """None of these tests need a real uv/venv — stub install_deps + smoke_test
    at the subprocess boundary they actually use."""

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    def fake_smoke_test(release_dir):
        return None

    monkeypatch.setattr(deploy, "smoke_test", fake_smoke_test)


class TestCmdCheck:
    def test_prepares_newer_release(self, tmp_path):
        release = _release("v0.2.0")
        client = FakeClient(
            check_result=CheckResult(release=release, etag='"e1"'),
            assets={
                release.tarball_url: _tarball_for(release.tag),
                release.ui_dist_url: _ui_tarball(),
            },
        )
        config = _config(tmp_path, active_tag="v0.1.0")

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["prepared"] == "v0.2.0"
        assert config.pending_tag == "v0.2.0"
        assert config.pending_notes == "notes"
        assert config.etag == '"e1"'
        assert (config.releases_path() / "v0.2.0" / "pyproject.toml").exists()
        assert (config.releases_path() / "v0.2.0" / "ui" / "dist" / "index.html").exists()

    def test_skipped_tag_is_not_re_prepared(self, tmp_path):
        release = _release("v0.2.0")
        client = FakeClient(check_result=CheckResult(release=release, etag='"e1"'))
        config = _config(tmp_path, active_tag="v0.1.0", skipped_tag="v0.2.0")

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["prepared"] is None
        assert result["up_to_date"] is False
        assert config.pending_tag == ""
        assert client.download_calls == []

    def test_up_to_date_when_tag_matches_active(self, tmp_path):
        client = FakeClient(check_result=CheckResult(release=_release("v0.1.0"), etag='"e"'))
        config = _config(tmp_path, active_tag="v0.1.0")

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["up_to_date"] is True
        assert config.pending_tag == ""

    def test_not_modified_is_up_to_date(self, tmp_path):
        client = FakeClient(check_result=CheckResult(release=None, etag='"e"', not_modified=True))
        config = _config(tmp_path, active_tag="v0.1.0", etag='"e"')

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["up_to_date"] is True

    def test_idempotent_when_already_prepared(self, tmp_path):
        release = _release("v0.2.0")
        client = FakeClient(check_result=CheckResult(release=release, etag='"e1"'))
        config = _config(tmp_path, active_tag="v0.1.0", pending_tag="v0.2.0")
        (config.releases_path() / "v0.2.0").mkdir(parents=True)

        commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        # No download attempted — nothing to redo.
        assert client.download_calls == []

    def test_disabled_is_a_noop(self, tmp_path):
        client = FakeClient(check_result=CheckResult(release=_release(), etag='"e"'))
        config = _config(tmp_path, enabled=False)

        commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert config.pending_tag == ""
        assert client.download_calls == []

    def test_older_release_than_active_is_skipped(self, tmp_path):
        client = FakeClient(check_result=CheckResult(release=_release("v0.1.0"), etag='"e"'))
        config = _config(tmp_path, active_tag="v0.5.0")

        commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert config.pending_tag == ""

    def test_rate_limited_records_error_and_does_not_crash(self, tmp_path):
        client = FakeClient(check_error=RateLimited("123"))
        config = _config(tmp_path)

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["error"] is not None
        assert config.last_error != ""

    def test_network_error_records_error_and_does_not_crash(self, tmp_path):
        client = FakeClient(check_error=UpdateNetworkError("offline"))
        config = _config(tmp_path)

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["error"] is not None

    def test_deploy_failure_leaves_active_release_untouched(self, tmp_path, monkeypatch):
        release = _release("v0.2.0", ui=False)
        client = FakeClient(
            check_result=CheckResult(release=release, etag='"e1"'),
            assets={release.tarball_url: _tarball_for(release.tag)},
        )
        config = _config(tmp_path, active_tag="v0.1.0")
        active_dir = config.releases_path() / "v0.1.0"
        active_dir.mkdir(parents=True)
        (active_dir / "marker.txt").write_text("still here")

        def fail_install_deps(release_dir):
            raise deploy.DeployError("disk full")

        monkeypatch.setattr(deploy, "install_deps", fail_install_deps)

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["error"] == "disk full"
        assert config.pending_tag == ""
        assert config.active_tag == "v0.1.0"
        assert (active_dir / "marker.txt").exists()
        assert not (config.releases_path() / "v0.2.0").exists()

    def test_missing_ui_asset_fails_prepare_instead_of_falling_back(self, tmp_path):
        # A release with no ui-dist.tar.gz asset must never be prepared/applied:
        # start-kiosk.sh's on-device `npm run build` fallback is exactly what
        # auto-update must never trigger on a Pi.
        release = _release("v0.2.0", ui=False)
        client = FakeClient(
            check_result=CheckResult(release=release, etag='"e1"'),
            assets={release.tarball_url: _tarball_for(release.tag)},
        )
        config = _config(tmp_path, active_tag="v0.1.0")

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["error"] is not None
        assert "ui-dist.tar.gz" in result["error"]
        assert config.pending_tag == ""
        assert not (config.releases_path() / "v0.2.0").exists()

    def test_dry_run_does_not_write_state_or_download(self, tmp_path):
        release = _release("v0.2.0")
        client = FakeClient(check_result=CheckResult(release=release, etag='"e1"'))
        config = _config(tmp_path, active_tag="v0.1.0")

        result = commands.cmd_check(
            config, tmp_path / "update.json", client, out=lambda _m: None, dry_run=True
        )

        assert result["prepared"] == "v0.2.0"
        assert client.download_calls == []
        assert not (tmp_path / "update.json").exists()


class TestCmdApply:
    def test_swaps_pending_release_in(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.1.0", pending_tag="v0.2.0")
        (config.releases_path() / "v0.1.0").mkdir(parents=True)
        (config.releases_path() / "v0.2.0").mkdir(parents=True)

        result = commands.cmd_apply(config, tmp_path / "update.json", out=lambda _m: None)

        assert result["swapped"] is True
        assert config.active_tag == "v0.2.0"
        assert config.previous_tag == "v0.1.0"
        assert config.pending_tag == ""
        assert config.install_path().resolve() == (config.releases_path() / "v0.2.0").resolve()

    def test_noop_when_nothing_pending(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.1.0")
        result = commands.cmd_apply(config, tmp_path / "update.json", out=lambda _m: None)
        assert result["swapped"] is False
        assert result.get("error") is None

    def test_missing_prepared_dir_clears_pending_and_errors(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.1.0", pending_tag="v0.2.0")
        result = commands.cmd_apply(config, tmp_path / "update.json", out=lambda _m: None)
        assert result["error"] is not None
        assert config.pending_tag == ""

    def test_dry_run_does_not_swap(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.1.0", pending_tag="v0.2.0")
        (config.releases_path() / "v0.2.0").mkdir(parents=True)

        commands.cmd_apply(config, tmp_path / "update.json", out=lambda _m: None, dry_run=True)

        assert config.active_tag == "v0.1.0"
        assert not config.install_path().exists()


class TestCmdRollback:
    def test_rolls_back_to_previous(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.2.0", previous_tag="v0.1.0")
        (config.releases_path() / "v0.1.0").mkdir(parents=True)
        (config.releases_path() / "v0.2.0").mkdir(parents=True)
        config.install_path().symlink_to(config.releases_path() / "v0.2.0")

        ok = commands.cmd_rollback(config, tmp_path / "update.json", out=lambda _m: None)

        assert ok is True
        assert config.active_tag == "v0.1.0"
        assert config.previous_tag == ""
        assert "rolled back" in config.last_error
        assert config.install_path().resolve() == (config.releases_path() / "v0.1.0").resolve()

    def test_no_previous_tag_fails_cleanly(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.2.0")
        ok = commands.cmd_rollback(config, tmp_path / "update.json", out=lambda _m: None)
        assert ok is False

    def test_missing_previous_dir_fails_cleanly(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.2.0", previous_tag="v0.1.0")
        ok = commands.cmd_rollback(config, tmp_path / "update.json", out=lambda _m: None)
        assert ok is False


class TestCmdSkip:
    def test_dismisses_pending_tag(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.1.0", pending_tag="v0.2.0", pending_notes="notes")
        (config.releases_path() / "v0.1.0").mkdir(parents=True)
        (config.releases_path() / "v0.2.0").mkdir(parents=True)

        ok = commands.cmd_skip(config, tmp_path / "update.json", "v0.2.0", out=lambda _m: None)

        assert ok is True
        assert config.skipped_tag == "v0.2.0"
        assert config.pending_tag == ""
        assert config.pending_notes == ""
        # Freed immediately rather than waiting for the next check cycle.
        assert not (config.releases_path() / "v0.2.0").exists()
        assert (config.releases_path() / "v0.1.0").exists()

    def test_mismatched_tag_is_ignored(self, tmp_path):
        config = _config(tmp_path, pending_tag="v0.2.0")

        ok = commands.cmd_skip(config, tmp_path / "update.json", "v0.3.0", out=lambda _m: None)

        assert ok is False
        assert config.skipped_tag == ""
        assert config.pending_tag == "v0.2.0"

    def test_a_later_release_is_not_suppressed(self, tmp_path):
        # "Never" only dismisses the exact tag; a newer one should still
        # prepare normally on a later cmd_check.
        release = _release("v0.3.0")
        client = FakeClient(
            check_result=CheckResult(release=release, etag='"e2"'),
            assets={
                release.tarball_url: _tarball_for(release.tag),
                release.ui_dist_url: _ui_tarball(),
            },
        )
        config = _config(tmp_path, active_tag="v0.1.0", skipped_tag="v0.2.0")

        result = commands.cmd_check(config, tmp_path / "update.json", client, out=lambda _m: None)

        assert result["prepared"] == "v0.3.0"
        assert config.pending_tag == "v0.3.0"


class TestCmdStatus:
    def test_reports_state(self, tmp_path):
        config = _config(tmp_path, active_tag="v0.2.0", pending_tag="", last_error="oops")
        result = commands.cmd_status(config, out=lambda _m: None)
        assert result["active_tag"] == "v0.2.0"
        assert result["last_error"] == "oops"


class TestCmdBootstrap:
    def _make_git_clone(self, path, version="0.2.0", dirty=False):
        path.mkdir(parents=True)
        (path / ".git").mkdir()
        (path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
        if dirty:
            (path / "scratch.txt").write_text("uncommitted")

    def test_migrates_clean_clone_into_releases_layout(self, tmp_path):
        install_dir = tmp_path / "openflight"
        self._make_git_clone(install_dir)
        config = _config(tmp_path)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        ok = commands.cmd_bootstrap(
            config, tmp_path / "update.json", out=lambda _m: None, run_fn=fake_run
        )

        assert ok is True
        assert config.active_tag == "v0.2.0"
        assert config.enabled is True
        assert install_dir.is_symlink()
        assert (config.releases_path() / "v0.2.0" / "pyproject.toml").exists()

    def test_aborts_on_dirty_git_status(self, tmp_path):
        install_dir = tmp_path / "openflight"
        self._make_git_clone(install_dir, dirty=True)
        config = _config(tmp_path)

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=" M scratch.txt\n", stderr="")

        ok = commands.cmd_bootstrap(
            config, tmp_path / "update.json", out=lambda _m: None, run_fn=fake_run
        )

        assert ok is False
        assert install_dir.is_dir()
        assert not install_dir.is_symlink()

    def test_idempotent_when_already_bootstrapped(self, tmp_path):
        release_dir = tmp_path / "releases" / "v0.2.0"
        release_dir.mkdir(parents=True)
        install_dir = tmp_path / "openflight"
        install_dir.symlink_to(release_dir)
        config = _config(tmp_path, active_tag="v0.2.0")

        ok = commands.cmd_bootstrap(config, tmp_path / "update.json", out=lambda _m: None)

        assert ok is True

    def test_not_a_git_checkout_refuses(self, tmp_path):
        install_dir = tmp_path / "openflight"
        install_dir.mkdir()
        (install_dir / "pyproject.toml").write_text('version = "0.2.0"\n')
        config = _config(tmp_path)

        ok = commands.cmd_bootstrap(config, tmp_path / "update.json", out=lambda _m: None)

        assert ok is False
