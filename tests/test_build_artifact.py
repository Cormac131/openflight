"""Tests for scripts/release/build_artifact.py against a real temporary git repo."""

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "release"))

import build_artifact as ba  # noqa: E402

from openflight import release  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src" / "openflight").mkdir(parents=True)
    (root / "big").mkdir()
    (root / "src" / "openflight" / "__init__.py").write_text('__version__ = "0.3.0"\n')
    (root / "README.md").write_text("readme\n")
    (root / "big" / "model.bin").write_bytes(b"\x00" * 64)
    (root / ".gitattributes").write_text("big/ export-ignore\n")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    (root / "ui" / "dist").mkdir(parents=True)
    (root / "ui" / "dist" / "index.html").write_text("<html></html>\n")
    return root


class TestBuildArtifact:
    def test_builds_tarball_with_tree_ui_dist_and_release_json(self, repo, tmp_path):
        output = tmp_path / "out"

        path = ba.build_artifact(
            repo,
            "v0.3.0-dev.7",
            "experimental",
            "open-flight/openflight",
            output,
            built_at="2026-09-05T00:00:00+00:00",
        )

        assert path == output / "openflight-v0.3.0-dev.7.tar.gz"
        with tarfile.open(path) as archive:
            names = archive.getnames()
            release_member = archive.extractfile("openflight-v0.3.0-dev.7/release.json")
            data = json.load(release_member)
        assert "openflight-v0.3.0-dev.7/README.md" in names
        assert "openflight-v0.3.0-dev.7/src/openflight/__init__.py" in names
        assert "openflight-v0.3.0-dev.7/ui/dist/index.html" in names
        assert not any("big/" in name for name in names)
        assert all(name.startswith("openflight-v0.3.0-dev.7") for name in names)
        assert data["version"] == "0.3.0-dev.7"
        assert data["channel"] == "experimental"
        assert data["repository"] == "open-flight/openflight"
        assert data["built_at"] == "2026-09-05T00:00:00+00:00"
        assert len(data["commit"]) == 12

    def test_release_json_round_trips_through_the_runtime_loader(self, repo, tmp_path):
        path = ba.build_artifact(repo, "v0.3.0", "stable", "o/r", tmp_path / "out")
        with tarfile.open(path) as archive:
            archive.extractall(tmp_path / "unpacked")

        info = release.load_release_info(tmp_path / "unpacked" / "openflight-v0.3.0", "0.3.0")

        assert info.channel == "stable"
        assert info.tag == "v0.3.0"
        assert info.version == "0.3.0"

    def test_writes_matching_sha256_file(self, repo, tmp_path):
        path = ba.build_artifact(repo, "v0.3.0", "stable", "o/r", tmp_path / "out")

        digest_line = (tmp_path / "out" / "openflight-v0.3.0.tar.gz.sha256").read_text()

        assert (
            digest_line
            == f"{hashlib.sha256(path.read_bytes()).hexdigest()}  openflight-v0.3.0.tar.gz\n"
        )

    def test_archives_the_requested_commit_not_the_working_tree(self, repo, tmp_path):
        first = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (repo / "README.md").write_text("changed later\n")
        _git(repo, "commit", "-q", "-am", "second")

        path = ba.build_artifact(repo, "v0.3.0", "stable", "o/r", tmp_path / "out", commit=first)

        with tarfile.open(path) as archive:
            readme = archive.extractfile("openflight-v0.3.0/README.md").read()
        assert readme == b"readme\n"

    @pytest.mark.parametrize(
        ("tag", "channel", "message"),
        [
            ("v0.3.0", "experimental", "does not belong"),
            ("v0.3.0-dev.7", "stable", "does not belong"),
            ("0.3.0", "stable", "is not vX.Y.Z"),
            ("v0.4.0", "stable", "does not match __version__"),
        ],
    )
    def test_rejects_inconsistent_tag_and_channel(self, repo, tmp_path, tag, channel, message):
        with pytest.raises(ba.ArtifactError, match=message):
            ba.build_artifact(repo, tag, channel, "o/r", tmp_path / "out")
        assert not (tmp_path / "out").exists()

    def test_requires_a_built_ui(self, repo, tmp_path):
        shutil.rmtree(repo / "ui")

        with pytest.raises(ba.ArtifactError, match="build the UI first"):
            ba.build_artifact(repo, "v0.3.0", "stable", "o/r", tmp_path / "out")

    def test_unknown_commit_fails(self, repo, tmp_path):
        with pytest.raises(ba.ArtifactError, match="git rev-parse"):
            ba.build_artifact(repo, "v0.3.0", "stable", "o/r", tmp_path / "out", commit="nope")

    def test_cli_prints_the_artifact_path(self, repo, tmp_path, capsys):
        code = ba.main(
            [
                "--tag",
                "v0.3.0",
                "--channel",
                "stable",
                "--repository",
                "o/r",
                "--output",
                str(tmp_path / "out"),
                "--repo-root",
                str(repo),
            ]
        )

        assert code == 0
        assert capsys.readouterr().out.strip().endswith("openflight-v0.3.0.tar.gz")

    def test_cli_reports_errors_without_traceback(self, repo, tmp_path, capsys):
        code = ba.main(
            [
                "--tag",
                "v9.9.9",
                "--channel",
                "stable",
                "--repository",
                "o/r",
                "--output",
                str(tmp_path / "out"),
                "--repo-root",
                str(repo),
            ]
        )

        assert code == 1
        assert "does not match" in capsys.readouterr().err


def test_repository_export_ignores_bulky_directories():
    """The real .gitattributes keeps models, CAD, and session logs out of the artifact."""
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")

    for path in ("models/", "cad/", "session_logs/", "docs/*.pdf"):
        assert f"{path} export-ignore" in attributes
