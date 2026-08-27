"""Tests for openflight-update's filesystem/subprocess deploy mechanics.

Real-filesystem integration tests (extract, symlink swap, prune) plus
subprocess-mocked tests for uv/venv-touching steps, per the plan's decision
to keep this module's side effects individually testable without hardware
or a real Pi.
"""

import subprocess
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

from openflight.update import deploy


def _make_tarball(files: dict, top_level: str = "owner-repo-abc123") -> bytes:
    """Build an in-memory .tar.gz with every path prefixed by ``top_level/``,
    matching the shape of a real GitHub source tarball."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel_path, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name=f"{top_level}/{rel_path}")
            info.size = len(data)
            tar.addfile(info, BytesIO(data))
    return buf.getvalue()


class TestExtractRelease:
    def test_strips_top_level_dir(self, tmp_path):
        tarball = _make_tarball({"pyproject.toml": 'version = "0.3.0"\n', "src/a.py": "x = 1\n"})
        dest = tmp_path / "release"

        deploy.extract_release(tarball, dest)

        assert (dest / "pyproject.toml").read_text() == 'version = "0.3.0"\n'
        assert (dest / "src" / "a.py").read_text() == "x = 1\n"

    def test_raises_and_cleans_up_on_corrupted_tarball(self, tmp_path):
        dest = tmp_path / "release"

        with pytest.raises(deploy.DeployError):
            deploy.extract_release(b"not a real tarball", dest)

        assert not dest.exists()

    def test_raises_on_empty_tarball(self, tmp_path):
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz"):
            pass
        dest = tmp_path / "release"

        with pytest.raises(deploy.DeployError):
            deploy.extract_release(buf.getvalue(), dest)
        assert not dest.exists()

    def test_raises_without_touching_preexisting_dest(self, tmp_path):
        dest = tmp_path / "release"
        dest.mkdir()
        (dest / "sentinel.txt").write_text("keep me")
        tarball = _make_tarball({"a.txt": "new"})

        with pytest.raises(deploy.DeployError):
            deploy.extract_release(tarball, dest)

        # A pre-existing directory must never be touched, let alone deleted.
        assert (dest / "sentinel.txt").read_text() == "keep me"

    def test_rejects_path_traversal_member(self, tmp_path):
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            top = tarfile.TarInfo(name="top")
            top.type = tarfile.DIRTYPE
            tar.addfile(top)
            evil = tarfile.TarInfo(name="top/../../evil.txt")
            data = b"pwned"
            evil.size = len(data)
            tar.addfile(evil, BytesIO(data))
        dest = tmp_path / "release"

        with pytest.raises(deploy.DeployError):
            deploy.extract_release(buf.getvalue(), dest)

        assert not (tmp_path / "evil.txt").exists()


class TestLocalConfigPaths:
    def test_finds_literal_config_path(self, tmp_path):
        (tmp_path / ".gitignore").write_text("config/sim.json\nui/dist/\n")
        assert deploy.local_config_paths(tmp_path) == ["config/sim.json"]

    def test_excludes_example_templates(self, tmp_path):
        (tmp_path / ".gitignore").write_text("config/sim.json\nconfig/credentials.env.example\n")
        assert deploy.local_config_paths(tmp_path) == ["config/sim.json"]

    def test_excludes_wildcard_patterns(self, tmp_path):
        (tmp_path / ".gitignore").write_text("config/*.local.json\n")
        assert deploy.local_config_paths(tmp_path) == []

    def test_no_gitignore_returns_empty(self, tmp_path):
        assert deploy.local_config_paths(tmp_path) == []


class TestCopyLocalConfig:
    def test_copies_existing_local_file(self, tmp_path):
        from_dir = tmp_path / "old"
        to_dir = tmp_path / "new"
        (to_dir).mkdir(parents=True)
        (from_dir / "config").mkdir(parents=True)
        (from_dir / ".gitignore").write_text("config/sim.json\n")
        (to_dir / ".gitignore").write_text("config/sim.json\n")
        (from_dir / "config" / "sim.json").write_text('{"a": 1}')

        copied = deploy.copy_local_config(from_dir, to_dir)

        assert copied == ["config/sim.json"]
        assert (to_dir / "config" / "sim.json").read_text() == '{"a": 1}'

    def test_skips_missing_source_gracefully(self, tmp_path):
        from_dir = tmp_path / "old"
        to_dir = tmp_path / "new"
        from_dir.mkdir()
        to_dir.mkdir()
        (to_dir / ".gitignore").write_text("config/sim.json\n")

        assert deploy.copy_local_config(from_dir, to_dir) == []


class TestInstallUi:
    def test_returns_false_when_no_asset(self, tmp_path):
        assert deploy.install_ui(None, tmp_path / "release") is False

    def test_extracts_dist_directory(self, tmp_path):
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"<html></html>"
            info = tarfile.TarInfo(name="dist/index.html")
            info.size = len(data)
            tar.addfile(info, BytesIO(data))
        release_dir = tmp_path / "release"
        release_dir.mkdir()

        result = deploy.install_ui(buf.getvalue(), release_dir)

        assert result is True
        assert (release_dir / "ui" / "dist" / "index.html").read_text() == "<html></html>"

    def test_raises_on_corrupted_asset(self, tmp_path):
        release_dir = tmp_path / "release"
        release_dir.mkdir()
        with pytest.raises(deploy.DeployError):
            deploy.install_ui(b"garbage", release_dir)


class TestInstallDeps:
    def test_success(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(deploy.subprocess, "run", fake_run)
        deploy.install_deps(tmp_path)
        assert calls[0][0] == ["uv", "sync", "--quiet"]
        assert calls[0][1]["cwd"] == tmp_path

    def test_uv_missing_raises(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("uv")

        monkeypatch.setattr(deploy.subprocess, "run", fake_run)
        with pytest.raises(deploy.DeployError):
            deploy.install_deps(tmp_path)

    def test_timeout_raises(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

        monkeypatch.setattr(deploy.subprocess, "run", fake_run)
        with pytest.raises(deploy.DeployError):
            deploy.install_deps(tmp_path)

    def test_nonzero_exit_raises(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="disk full")

        monkeypatch.setattr(deploy.subprocess, "run", fake_run)
        with pytest.raises(deploy.DeployError, match="disk full"):
            deploy.install_deps(tmp_path)


class TestSmokeTest:
    def _make_fake_venv(self, release_dir: Path) -> Path:
        python = release_dir / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.touch()
        return python

    def test_raises_when_venv_missing(self, tmp_path):
        with pytest.raises(deploy.DeployError, match="no venv python"):
            deploy.smoke_test(tmp_path / "release")

    def test_success(self, tmp_path, monkeypatch):
        release_dir = tmp_path / "release"
        release_dir.mkdir()
        self._make_fake_venv(release_dir)
        monkeypatch.setattr(
            deploy.subprocess,
            "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
        )
        deploy.smoke_test(release_dir)  # does not raise

    def test_import_failure_raises(self, tmp_path, monkeypatch):
        release_dir = tmp_path / "release"
        release_dir.mkdir()
        self._make_fake_venv(release_dir)

        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, stderr="ImportError: no module")

        monkeypatch.setattr(deploy.subprocess, "run", fake_run)
        with pytest.raises(deploy.DeployError, match="ImportError"):
            deploy.smoke_test(release_dir)


class TestSwapActive:
    def test_points_symlink_at_release_dir(self, tmp_path):
        release_dir = tmp_path / "releases" / "v1.0.0"
        release_dir.mkdir(parents=True)
        install_dir = tmp_path / "openflight"

        deploy.swap_active(install_dir, release_dir)

        assert install_dir.is_symlink()
        assert install_dir.resolve() == release_dir.resolve()

    def test_replaces_existing_symlink(self, tmp_path):
        old_dir = tmp_path / "releases" / "v1.0.0"
        new_dir = tmp_path / "releases" / "v1.1.0"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        install_dir = tmp_path / "openflight"
        install_dir.symlink_to(old_dir)

        deploy.swap_active(install_dir, new_dir)

        assert install_dir.resolve() == new_dir.resolve()

    def test_raises_when_release_dir_missing(self, tmp_path):
        with pytest.raises(deploy.DeployError):
            deploy.swap_active(tmp_path / "openflight", tmp_path / "releases" / "v9.9.9")


class TestPrune:
    def test_removes_dirs_not_in_keep_set(self, tmp_path):
        releases_dir = tmp_path / "releases"
        for tag in ("v1.0.0", "v1.1.0", "v1.2.0"):
            (releases_dir / tag).mkdir(parents=True)

        removed = deploy.prune(releases_dir, keep_tags={"v1.2.0"})

        assert sorted(removed) == ["v1.0.0", "v1.1.0"]
        remaining = sorted(p.name for p in releases_dir.iterdir())
        assert remaining == ["v1.2.0"]

    def test_never_removes_kept_tags(self, tmp_path):
        releases_dir = tmp_path / "releases"
        for tag in ("v1.0.0", "v1.1.0"):
            (releases_dir / tag).mkdir(parents=True)

        deploy.prune(releases_dir, keep_tags={"v1.0.0", "v1.1.0"})

        assert sorted(p.name for p in releases_dir.iterdir()) == ["v1.0.0", "v1.1.0"]

    def test_missing_releases_dir_returns_empty(self, tmp_path):
        assert deploy.prune(tmp_path / "nope", keep_tags=set()) == []

    def test_ignores_non_directory_entries(self, tmp_path):
        releases_dir = tmp_path / "releases"
        releases_dir.mkdir()
        (releases_dir / "stray-file.txt").write_text("x")

        assert deploy.prune(releases_dir, keep_tags=set()) == []
        assert (releases_dir / "stray-file.txt").exists()
