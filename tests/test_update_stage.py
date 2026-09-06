"""Tests for staging a release from a real tarball (openflight.update.stage)."""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
from collections import namedtuple
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "release"))

import build_artifact as ba  # noqa: E402

from openflight.update import stage as st  # noqa: E402
from openflight.update.github import DownloadError, ReleaseAsset, RemoteRelease  # noqa: E402
from openflight.update.layout import InstallLayout  # noqa: E402
from openflight.update.version import parse_tag  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

Usage = namedtuple("Usage", "total used free")
PLENTY = lambda _path: Usage(10**12, 0, 10**12)  # noqa: E731


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def artifact(tmp_path):
    """A real release tarball for v0.3.1 plus its sidecar, built by build_artifact."""
    repo = tmp_path / "repo"
    for relative in st.REQUIRED_FILES:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("# updater present\n")
    (repo / "src" / "openflight" / "__init__.py").write_text('__version__ = "0.3.1"\n')
    (repo / "ui").mkdir(exist_ok=True)
    (repo / "ui" / "package-lock.json").write_text('{"lock": 1}')
    (repo / "README.md").write_text("readme\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "ui" / "dist").mkdir()
    (repo / "ui" / "dist" / "index.html").write_text("<html></html>")
    out = tmp_path / "out"
    tarball = ba.build_artifact(repo, "v0.3.1", "stable", "o/r", out)
    return tarball, tarball.with_name(tarball.name + ".sha256")


class FakeGitHub:
    """Serves local files for release assets."""

    def __init__(self, files):
        self.files = dict(files)
        self.downloads = []

    def download(self, asset, dest):
        self.downloads.append(asset.name)
        source = self.files[asset.name]
        if isinstance(source, Exception):
            raise source
        shutil.copy(source, dest)
        return hashlib.sha256(Path(source).read_bytes()).hexdigest()


class FakeRun:
    """Stands in for uv / npm / the smoke test."""

    def __init__(self, *, sync_ok=True, npm_ok=True, smoke_ok=True, raise_npm=False):
        self.calls = []
        self.sync_ok, self.npm_ok, self.smoke_ok, self.raise_npm = (
            sync_ok,
            npm_ok,
            smoke_ok,
            raise_npm,
        )

    def __call__(self, args, cwd=None, **kwargs):
        self.calls.append(args)
        if args[0] == "npm":
            if self.raise_npm:
                raise FileNotFoundError("npm")
            return subprocess.CompletedProcess(args, 0 if self.npm_ok else 1)
        if args[1] == "venv":
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(args, 0)
        if args[1] == "sync":
            if self.sync_ok:
                server = Path(cwd) / ".venv" / "bin" / "openflight-server"
                server.parent.mkdir(parents=True, exist_ok=True)
                server.write_text("#!/bin/sh\n")
            return subprocess.CompletedProcess(args, 0 if self.sync_ok else 1)
        if args[0].endswith("openflight-server"):
            if self.smoke_ok:
                return subprocess.CompletedProcess(
                    args, 0, stdout="openflight-server 0.3.1 (stable)\n"
                )
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")
        raise AssertionError(f"unexpected command {args}")


def _release(tarball, sidecar, tag="v0.3.1"):
    return RemoteRelease(
        tag=tag,
        version=parse_tag(tag),
        prerelease=False,
        assets=(
            ReleaseAsset(tarball.name, "https://d/t", tarball.stat().st_size),
            ReleaseAsset(sidecar.name, "https://d/s", sidecar.stat().st_size),
        ),
    )


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root = tmp_path / "openflight-releases"
    root.mkdir()
    current = root / "v0.3.0"
    (current / "ui" / "node_modules" / ".bin").mkdir(parents=True)
    (current / "ui" / "node_modules" / ".bin" / "electron").write_text("bin")
    (current / "ui" / "package-lock.json").write_text('{"lock": 1}')
    (current / ".venv").mkdir()
    (current / ".venv" / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    layout = InstallLayout(install_link=tmp_path / "openflight", releases_root=root)
    os.symlink(current, layout.install_link)
    monkeypatch.setattr(st, "find_uv", lambda: "/fake/uv")
    return layout


class TestStageRelease:
    def test_downloads_verifies_extracts_prepares_and_links(self, layout, artifact):
        tarball, sidecar = artifact
        github = FakeGitHub({tarball.name: tarball, sidecar.name: sidecar})
        run = FakeRun()
        states = []

        dest = st.stage_release(
            layout,
            _release(tarball, sidecar),
            github,
            run=run,
            on_state=states.append,
            disk_usage=PLENTY,
        )

        assert dest == layout.releases_root / "v0.3.1"
        assert layout.staged_target() == dest
        assert states == ["downloading", "staging"]
        assert (dest / "README.md").read_text() == "readme\n"
        assert (dest / "ui" / "dist" / "index.html").is_file()
        assert (dest / "release.json").is_file()
        assert (dest / ".venv" / "bin" / "openflight-server").is_file()
        assert (dest / "ui" / "node_modules" / ".bin" / "electron").read_text() == "bin"
        assert not any(call[0] == "npm" for call in run.calls)
        assert ["/fake/uv", "sync", "--locked"] in run.calls
        assert not list(layout.downloads.iterdir())

    def test_hardlinks_node_modules_and_runs_npm_ci_when_the_lockfile_changed(
        self, layout, artifact
    ):
        tarball, sidecar = artifact
        (layout.current_target() / "ui" / "package-lock.json").write_text('{"lock": 2}')
        github = FakeGitHub({tarball.name: tarball, sidecar.name: sidecar})
        run = FakeRun(npm_ok=False)

        dest = st.stage_release(
            layout, _release(tarball, sidecar), github, run=run, disk_usage=PLENTY
        )

        assert ["npm", "ci", "--no-audit", "--no-fund"] in run.calls
        assert layout.staged_target() == dest

    def test_missing_npm_is_not_fatal(self, layout, artifact):
        tarball, sidecar = artifact
        (layout.current_target() / "ui" / "package-lock.json").unlink()
        github = FakeGitHub({tarball.name: tarball, sidecar.name: sidecar})

        st.stage_release(
            layout,
            _release(tarball, sidecar),
            github,
            run=FakeRun(raise_npm=True),
            disk_usage=PLENTY,
        )

        assert layout.staged_target() is not None

    def test_camera_install_gets_a_system_site_packages_venv(self, layout, artifact):
        tarball, sidecar = artifact
        (layout.current_target() / ".venv" / "pyvenv.cfg").write_text(
            "include-system-site-packages = true\n"
        )
        github = FakeGitHub({tarball.name: tarball, sidecar.name: sidecar})
        run = FakeRun()

        st.stage_release(layout, _release(tarball, sidecar), github, run=run, disk_usage=PLENTY)

        assert run.calls[-3][:4] == ["/fake/uv", "venv", "--system-site-packages", "--python"]
        assert run.calls[-2] == ["/fake/uv", "sync", "--locked", "--extra", "camera"]

    def test_existing_complete_tree_is_relinked_without_downloading(self, layout, artifact):
        tarball, sidecar = artifact
        dest = layout.releases_root / "v0.3.1"
        (dest / ".venv" / "bin").mkdir(parents=True)
        (dest / ".venv" / "bin" / "openflight-server").write_text("")
        (dest / "release.json").write_text("{}")
        github = FakeGitHub({})

        assert (
            st.stage_release(
                layout, _release(tarball, sidecar), github, run=FakeRun(), disk_usage=PLENTY
            )
            == dest
        )
        assert github.downloads == []
        assert layout.staged_target() == dest

    @pytest.mark.parametrize("missing", ["tarball", "sidecar"])
    def test_release_without_the_assets_is_refused(self, layout, artifact, missing):
        tarball, sidecar = artifact
        release = _release(tarball, sidecar)
        kept = [a for a in release.assets if (missing == "tarball") == a.name.endswith(".sha256")]
        release = RemoteRelease(release.tag, release.version, False, tuple(kept))

        with pytest.raises(st.StageError) as info:
            st.stage_release(layout, release, FakeGitHub({}), run=FakeRun(), disk_usage=PLENTY)
        assert info.value.kind == "download"

    def test_not_enough_disk(self, layout, artifact):
        tarball, sidecar = artifact
        with pytest.raises(st.StageError) as info:
            st.stage_release(
                layout,
                _release(tarball, sidecar),
                FakeGitHub({}),
                run=FakeRun(),
                disk_usage=lambda _p: Usage(10, 0, 10),
            )
        assert info.value.kind == "disk"
        assert layout.staged_target() is None

    def test_download_failure(self, layout, artifact):
        tarball, sidecar = artifact
        github = FakeGitHub({tarball.name: DownloadError("reset"), sidecar.name: sidecar})
        with pytest.raises(st.StageError) as info:
            st.stage_release(
                layout, _release(tarball, sidecar), github, run=FakeRun(), disk_usage=PLENTY
            )
        assert info.value.kind == "download"

    def test_checksum_mismatch_discards_the_download(self, layout, artifact, tmp_path):
        tarball, sidecar = artifact
        bad = tmp_path / sidecar.name
        bad.write_text("0" * 64 + f"  {tarball.name}\n")
        github = FakeGitHub({tarball.name: tarball, sidecar.name: bad})

        with pytest.raises(st.StageError) as info:
            st.stage_release(
                layout, _release(tarball, sidecar), github, run=FakeRun(), disk_usage=PLENTY
            )

        assert info.value.kind == "checksum"
        assert not (layout.downloads / tarball.name).exists()
        assert not (layout.releases_root / "v0.3.1").exists()

    def test_sidecar_for_another_file_is_rejected(self, layout, artifact, tmp_path):
        tarball, sidecar = artifact
        bad = tmp_path / sidecar.name
        bad.write_text(hashlib.sha256(tarball.read_bytes()).hexdigest() + "  other.tar.gz\n")
        github = FakeGitHub({tarball.name: tarball, sidecar.name: bad})
        with pytest.raises(st.StageError) as info:
            st.stage_release(
                layout, _release(tarball, sidecar), github, run=FakeRun(), disk_usage=PLENTY
            )
        assert info.value.kind == "checksum"

    def test_truncated_archive(self, layout, artifact, tmp_path):
        tarball, sidecar = artifact
        broken = tmp_path / tarball.name
        broken.write_bytes(tarball.read_bytes()[: tarball.stat().st_size // 2])
        sha = tmp_path / sidecar.name
        sha.write_text(hashlib.sha256(broken.read_bytes()).hexdigest() + f"  {tarball.name}\n")
        github = FakeGitHub({tarball.name: broken, sidecar.name: sha})
        release = _release(broken, sha)

        with pytest.raises(st.StageError) as info:
            st.stage_release(layout, release, github, run=FakeRun(), disk_usage=PLENTY)
        assert info.value.kind == "archive"
        assert not (layout.releases_root / "v0.3.1").exists()

    def test_archive_with_the_wrong_top_directory(self, layout, tmp_path):
        wrong = tmp_path / "openflight-v0.3.1.tar.gz"
        with tarfile.open(wrong, "w:gz") as archive:
            src = tmp_path / "other"
            (src / "x").mkdir(parents=True)
            archive.add(src, arcname="other")
        sha = tmp_path / "openflight-v0.3.1.tar.gz.sha256"
        sha.write_text(hashlib.sha256(wrong.read_bytes()).hexdigest() + f"  {wrong.name}\n")
        github = FakeGitHub({wrong.name: wrong, sha.name: sha})

        with pytest.raises(st.StageError) as info:
            st.stage_release(layout, _release(wrong, sha), github, run=FakeRun(), disk_usage=PLENTY)
        assert info.value.kind == "archive"

    def test_release_without_the_updater_is_unsupported(self, layout, tmp_path):
        repo = tmp_path / "old"
        (repo / "src" / "openflight").mkdir(parents=True)
        (repo / "src" / "openflight" / "__init__.py").write_text('__version__ = "0.3.1"\n')
        _git(repo, "init", "-q")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "init")
        (repo / "ui" / "dist").mkdir(parents=True)
        (repo / "ui" / "dist" / "index.html").write_text("")
        tarball = ba.build_artifact(repo, "v0.3.1", "stable", "o/r", tmp_path / "out")
        sidecar = tarball.with_name(tarball.name + ".sha256")
        github = FakeGitHub({tarball.name: tarball, sidecar.name: sidecar})
        run = FakeRun()

        with pytest.raises(st.StageError) as info:
            st.stage_release(layout, _release(tarball, sidecar), github, run=run, disk_usage=PLENTY)

        assert info.value.kind == "unsupported_release"
        assert run.calls == []
        assert not (layout.releases_root / "v0.3.1").exists()

    @pytest.mark.parametrize("failure", ["sync", "smoke", "no_uv"])
    def test_dependency_failures_remove_the_tree_and_stage_nothing(
        self, layout, artifact, monkeypatch, failure
    ):
        tarball, sidecar = artifact
        github = FakeGitHub({tarball.name: tarball, sidecar.name: sidecar})
        if failure == "no_uv":
            monkeypatch.setattr(st, "find_uv", lambda: None)
        run = FakeRun(sync_ok=failure != "sync", smoke_ok=failure != "smoke")

        with pytest.raises(st.StageError) as info:
            st.stage_release(layout, _release(tarball, sidecar), github, run=run, disk_usage=PLENTY)

        assert info.value.kind == "deps"
        assert not (layout.releases_root / "v0.3.1").exists()
        assert layout.staged_target() is None
        assert not (layout.downloads / tarball.name).exists()
