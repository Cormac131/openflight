"""Tests for the build identity in openflight.release."""

import json
import logging
import shutil
import subprocess

import pytest

from openflight import __version__, release


@pytest.fixture(autouse=True)
def _fresh_cache():
    release.get_release_info.cache_clear()
    yield
    release.get_release_info.cache_clear()


def _valid_release(**overrides) -> dict:
    data = {
        "format_version": 1,
        "version": "0.3.0-dev.42",
        "base_version": __version__,
        "channel": "experimental",
        "tag": "v0.3.0-dev.42",
        "commit": "0123456789ab",
        "built_at": "2026-09-04T12:00:00+00:00",
        "repository": "open-flight/openflight",
    }
    data.update(overrides)
    return data


def _write_release(tmp_path, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data)
    (tmp_path / release.RELEASE_FILE_NAME).write_text(text, encoding="utf-8")


def _init_git_repo(path) -> str:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "file.txt").write_text("x\n", encoding="utf-8")
    env_args = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", *env_args, "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", *env_args, "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short=12", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


class TestReleaseInfo:
    def test_label_combines_version_and_channel(self):
        info = release.ReleaseInfo(version="0.3.0", base_version="0.3.0", channel="stable")

        assert info.label == "0.3.0 (stable)"

    def test_to_dict_carries_format_version_and_every_field(self):
        info = release.ReleaseInfo(
            version="0.3.0-dev.42",
            base_version="0.3.0",
            channel="experimental",
            tag="v0.3.0-dev.42",
            commit="abc",
            built_at="2026-09-04T12:00:00+00:00",
            repository="open-flight/openflight",
        )

        assert info.to_dict() == {
            "format_version": release.RELEASE_FORMAT_VERSION,
            "version": "0.3.0-dev.42",
            "base_version": "0.3.0",
            "channel": "experimental",
            "tag": "v0.3.0-dev.42",
            "commit": "abc",
            "built_at": "2026-09-04T12:00:00+00:00",
            "repository": "open-flight/openflight",
        }


class TestLoadReleaseFile:
    def test_valid_experimental_release(self, tmp_path):
        _write_release(tmp_path, _valid_release())

        info = release.load_release_info(tmp_path)

        assert info == release.ReleaseInfo(
            version="0.3.0-dev.42",
            base_version=__version__,
            channel="experimental",
            tag="v0.3.0-dev.42",
            commit="0123456789ab",
            built_at="2026-09-04T12:00:00+00:00",
            repository="open-flight/openflight",
        )

    def test_valid_stable_release(self, tmp_path):
        _write_release(
            tmp_path, _valid_release(version=__version__, channel="stable", tag=f"v{__version__}")
        )

        info = release.load_release_info(tmp_path)

        assert info.channel == "stable"
        assert info.version == __version__
        assert info.tag == f"v{__version__}"

    def test_optional_fields_default_to_none_and_unknown_keys_are_ignored(self, tmp_path):
        data = _valid_release(extra="ignored")
        del data["commit"]
        del data["built_at"]
        data["repository"] = ""
        _write_release(tmp_path, data)

        info = release.load_release_info(tmp_path)

        assert info.channel == "experimental"
        assert info.commit is None
        assert info.built_at is None
        assert info.repository is None

    def test_absent_file_falls_back_to_source_without_warning(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="openflight.release"):
            info = release.load_release_info(tmp_path)

        assert info.channel == release.SOURCE_CHANNEL
        assert info.base_version == __version__
        assert info.tag is None
        assert caplog.records == []

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param("{not json", id="malformed-json"),
            pytest.param([1, 2], id="top-level-list"),
            pytest.param({k: v for k, v in _valid_release().items() if k != "tag"}, id="no-tag"),
            pytest.param(_valid_release(version=""), id="empty-version"),
            pytest.param(_valid_release(channel="source"), id="source-is-not-a-release-channel"),
            pytest.param(_valid_release(channel="nightly"), id="unknown-channel"),
            pytest.param(_valid_release(base_version="99.0.0"), id="stale-base-version"),
            pytest.param(
                {k: v for k, v in _valid_release().items() if k != "base_version"},
                id="missing-base-version",
            ),
        ],
    )
    def test_invalid_file_warns_once_and_falls_back_to_source(self, tmp_path, caplog, data):
        _write_release(tmp_path, data)

        with caplog.at_level(logging.WARNING, logger="openflight.release"):
            info = release.load_release_info(tmp_path)

        assert info.channel == release.SOURCE_CHANNEL
        assert info.base_version == __version__
        assert len(caplog.records) == 1
        assert release.RELEASE_FILE_NAME in caplog.records[0].getMessage()

    def test_unreadable_file_warns_and_falls_back_to_source(self, tmp_path, caplog, monkeypatch):
        _write_release(tmp_path, _valid_release())

        def _boom(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(release.Path, "read_text", _boom)
        with caplog.at_level(logging.WARNING, logger="openflight.release"):
            info = release.load_release_info(tmp_path)

        assert info.channel == release.SOURCE_CHANNEL
        assert len(caplog.records) == 1

    def test_base_version_argument_overrides_package_version(self, tmp_path):
        _write_release(tmp_path, _valid_release(base_version="9.9.9"))

        info = release.load_release_info(tmp_path, base_version="9.9.9")

        assert info.channel == "experimental"
        assert info.base_version == "9.9.9"


class TestSourceFallback:
    @needs_git
    def test_git_checkout_reports_short_commit(self, tmp_path):
        commit = _init_git_repo(tmp_path)

        info = release.source_release_info(tmp_path, base_version="0.3.0")

        assert info.commit == commit
        assert info.version == f"0.3.0+{commit}"
        assert info.channel == release.SOURCE_CHANNEL
        assert info.tag is None

    def test_without_git_dir_reports_base_version_only(self, tmp_path):
        info = release.source_release_info(tmp_path, base_version="0.3.0")

        assert info.commit is None
        assert info.version == "0.3.0"

    def test_git_file_worktree_is_attempted(self, tmp_path, monkeypatch):
        (tmp_path / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        calls = []

        def _fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="abcdef123456\n", stderr="")

        monkeypatch.setattr(release.shutil, "which", lambda _name: "/usr/bin/git")
        monkeypatch.setattr(release.subprocess, "run", _fake_run)

        info = release.source_release_info(tmp_path, base_version="0.3.0")

        assert calls == [["git", "rev-parse", "--short=12", "HEAD"]]
        assert info.version == "0.3.0+abcdef123456"

    def test_missing_git_binary_skips_subprocess(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(release.shutil, "which", lambda _name: None)

        def _unexpected(*_args, **_kwargs):
            raise AssertionError("git must not be invoked")

        monkeypatch.setattr(release.subprocess, "run", _unexpected)

        assert release.source_release_info(tmp_path, base_version="0.3.0").commit is None

    @pytest.mark.parametrize(
        "error",
        [OSError("no such file"), subprocess.TimeoutExpired(cmd="git", timeout=5)],
        ids=["oserror", "timeout"],
    )
    def test_git_failures_never_raise(self, tmp_path, monkeypatch, error):
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(release.shutil, "which", lambda _name: "/usr/bin/git")

        def _raise(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(release.subprocess, "run", _raise)

        info = release.source_release_info(tmp_path, base_version="0.3.0")

        assert info.commit is None
        assert info.version == "0.3.0"

    def test_git_nonzero_exit_reports_no_commit(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(release.shutil, "which", lambda _name: "/usr/bin/git")
        monkeypatch.setattr(
            release.subprocess,
            "run",
            lambda args, **kwargs: subprocess.CompletedProcess(args, 128, stdout="", stderr="bad"),
        )

        assert release.source_release_info(tmp_path, base_version="0.3.0").commit is None


class TestGetReleaseInfo:
    def test_resolves_once_and_caches(self, monkeypatch):
        calls = []

        def _load(*args, **kwargs):
            calls.append((args, kwargs))
            return release.ReleaseInfo(version="1.0.0", base_version="1.0.0", channel="stable")

        monkeypatch.setattr(release, "load_release_info", _load)

        first = release.get_release_info()
        second = release.get_release_info()

        assert first is second
        assert len(calls) == 1

    def test_default_lookup_uses_the_repository_root(self):
        info = release.get_release_info()

        assert info.base_version == __version__
        assert info.channel in (*release.RELEASE_CHANNELS, release.SOURCE_CHANNEL)
        assert (release.DEFAULT_REPO_ROOT / "pyproject.toml").is_file()
