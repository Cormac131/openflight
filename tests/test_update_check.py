"""Tests for candidate selection and the check command."""

import fcntl
import os

import pytest

from openflight.release import ReleaseInfo
from openflight.update import check
from openflight.update.config import UpdateConfig, save_update_config
from openflight.update.github import ReleaseAsset, ReleaseLookupError, RemoteRelease
from openflight.update.layout import InstallLayout, replace_symlink
from openflight.update.paths import UpdatePaths
from openflight.update.stage import StageError
from openflight.update.status import CheckStatus, read_check_status, write_check_status
from openflight.update.version import parse_tag


def _remote(tag, size=10):
    return RemoteRelease(
        tag=tag,
        version=parse_tag(tag),
        prerelease=parse_tag(tag).channel == "experimental",
        assets=(ReleaseAsset(f"openflight-{tag}.tar.gz", "https://d/x", size),),
    )


def _installed(tag, channel=None):
    version = tag[1:]
    return ReleaseInfo(version, version.split("-")[0], channel or parse_tag(tag).channel, tag=tag)


SOURCE = ReleaseInfo("0.3.0+abc", "0.3.0", "source")


class TestSelectCandidate:
    def test_nothing_remote(self):
        assert check.select_candidate(_installed("v0.3.0"), "stable", None, []) == (None, "none")

    def test_same_tag_is_current(self):
        remote = _remote("v0.3.0")
        assert check.select_candidate(_installed("v0.3.0"), "stable", remote, []) == (
            None,
            "current",
        )

    def test_newer_on_same_channel(self):
        remote = _remote("v0.3.1")
        assert check.select_candidate(_installed("v0.3.0"), "stable", remote, []) == (
            remote,
            "newer",
        )

    @pytest.mark.parametrize("remote_tag", ["v0.2.9", "v0.3.0-dev.5"])
    def test_older_on_same_channel_is_not_newer(self, remote_tag):
        installed = _installed("v0.3.0") if remote_tag == "v0.2.9" else _installed("v0.3.0-dev.9")
        channel = installed.channel
        assert check.select_candidate(installed, channel, _remote(remote_tag), []) == (
            None,
            "not_newer",
        )

    def test_channel_switch_takes_the_channel_latest_even_when_lower(self):
        remote = _remote("v0.3.0")
        installed = _installed("v0.3.1-dev.4")
        assert check.select_candidate(installed, "stable", remote, []) == (remote, "channel_switch")

    def test_source_checkout_takes_the_channel_latest(self):
        remote = _remote("v0.2.0")
        assert check.select_candidate(SOURCE, "stable", remote, []) == (remote, "channel_switch")

    def test_bad_tags_are_held_back(self):
        remote = _remote("v0.3.1")
        assert check.select_candidate(_installed("v0.3.0"), "stable", remote, ["v0.3.1"]) == (
            None,
            "held_back",
        )


@pytest.fixture
def paths(tmp_path):
    return UpdatePaths(
        install_link=tmp_path / "openflight",
        releases_root=tmp_path / "openflight-releases",
        config=tmp_path / "update.json",
        status=tmp_path / "update-status.json",
    )


class FakeGitHub:
    def __init__(self, outcome):
        self.outcome = outcome
        self.repositories = []

    def factory(self, repository):
        self.repositories.append(repository)
        return self

    def latest(self, channel):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class TestRunCheck:
    def test_off_channel_records_up_to_date_without_network(self, paths):
        github = FakeGitHub(_remote("v9.9.9"))

        code = check.run_check(paths, installed=SOURCE, github_factory=github.factory)

        assert code == 0
        assert github.repositories == []
        assert read_check_status(paths.status).state == "up_to_date"

    def test_records_the_available_release(self, paths):
        save_update_config(UpdateConfig("stable", "me/fork"), paths.config)
        github = FakeGitHub(_remote("v0.3.1", size=1234))

        code = check.run_check(
            paths,
            installed=_installed("v0.3.0"),
            github_factory=github.factory,
            now=lambda: "2026-09-06T10:00:00+00:00",
        )

        status = read_check_status(paths.status)
        assert code == 0
        assert github.repositories == ["me/fork"]
        assert status.state == "up_to_date"
        assert status.available == {"tag": "v0.3.1", "version": "0.3.1", "size": 1234}
        assert status.last_check_at == "2026-09-06T10:00:00+00:00"
        assert status.channel == "stable"
        assert status.error is None

    def test_lookup_failure_is_recorded_and_exits_zero(self, paths):
        save_update_config(UpdateConfig("stable", "me/fork"), paths.config)
        github = FakeGitHub(ReleaseLookupError("offline", "no route"))

        code = check.run_check(paths, installed=_installed("v0.3.0"), github_factory=github.factory)

        status = read_check_status(paths.status)
        assert code == 0
        assert status.state == "failed"
        assert status.error == "offline: no route"

    def test_held_back_release_stays_visible(self, paths):
        save_update_config(UpdateConfig("stable", "me/fork"), paths.config)
        write_check_status(CheckStatus(bad_tags=["v0.3.1"]), paths.status)
        github = FakeGitHub(_remote("v0.3.1"))

        check.run_check(paths, installed=_installed("v0.3.0"), github_factory=github.factory)

        status = read_check_status(paths.status)
        assert status.state == "held_back"
        assert status.available["tag"] == "v0.3.1"
        assert status.bad_tags == ["v0.3.1"]

    def test_stale_availability_is_cleared_when_up_to_date(self, paths):
        save_update_config(UpdateConfig("stable", "me/fork"), paths.config)
        write_check_status(CheckStatus(available={"tag": "v0.3.1"}, error="x"), paths.status)
        github = FakeGitHub(_remote("v0.3.0"))

        check.run_check(paths, installed=_installed("v0.3.0"), github_factory=github.factory)

        status = read_check_status(paths.status)
        assert status.state == "up_to_date"
        assert status.available is None
        assert status.error is None


@pytest.fixture
def managed(paths, monkeypatch):
    """A managed layout: install link -> releases_root/v0.3.0, treated as the running tree."""
    paths.releases_root.mkdir()
    current = paths.releases_root / "v0.3.0"
    current.mkdir()
    paths.install_link.symlink_to(current)
    monkeypatch.setattr(check.InstallLayout, "is_managed", lambda self, running_root=None: True)
    save_update_config(UpdateConfig("stable", "me/fork"), paths.config)
    return InstallLayout(paths.install_link, paths.releases_root)


class FakeStage:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.seen_states = []

    def __call__(self, layout, release, github, *, run, on_state):
        self.calls.append(release.tag)
        for state in ("downloading", "staging"):
            on_state(state)
            self.seen_states.append(
                read_check_status(layout.releases_root.parent / "update-status.json").state
            )
        if self.error is not None:
            raise self.error
        dest = layout.release_dir(release.tag)
        dest.mkdir()
        replace_symlink(layout.staged_link, dest)
        return dest


class TestRunCheckStaging:
    def test_stages_the_candidate_and_reports_progress(self, paths, managed, monkeypatch):
        stage = FakeStage()
        monkeypatch.setattr(check, "stage_release", stage)

        code = check.run_check(
            paths,
            installed=_installed("v0.3.0"),
            github_factory=FakeGitHub(_remote("v0.3.1")).factory,
        )

        assert code == 0
        assert stage.calls == ["v0.3.1"]
        assert stage.seen_states == ["downloading", "staging"]
        assert managed.staged_target() == paths.releases_root / "v0.3.1"
        status = read_check_status(paths.status)
        assert status.state == "up_to_date"
        assert status.available["tag"] == "v0.3.1"

    def test_already_staged_release_is_not_downloaded_again(self, paths, managed, monkeypatch):
        (paths.releases_root / "v0.3.1").mkdir()
        replace_symlink(managed.staged_link, paths.releases_root / "v0.3.1")
        stage = FakeStage()
        monkeypatch.setattr(check, "stage_release", stage)

        code = check.run_check(
            paths,
            installed=_installed("v0.3.0"),
            github_factory=FakeGitHub(_remote("v0.3.1")).factory,
        )

        assert code == 0
        assert stage.calls == []
        assert managed.staged_target() == paths.releases_root / "v0.3.1"

    def test_stale_staged_release_is_dropped_before_staging_the_newer_one(
        self, paths, managed, monkeypatch
    ):
        old = paths.releases_root / "v0.3.1"
        old.mkdir()
        replace_symlink(managed.staged_link, old)
        monkeypatch.setattr(check, "stage_release", FakeStage())

        check.run_check(
            paths,
            installed=_installed("v0.3.0"),
            github_factory=FakeGitHub(_remote("v0.3.2")).factory,
        )

        assert managed.staged_target() == paths.releases_root / "v0.3.2"
        assert not old.exists()

    def test_stale_staged_release_is_dropped_when_nothing_qualifies(self, paths, managed):
        old = paths.releases_root / "v0.3.1"
        old.mkdir()
        replace_symlink(managed.staged_link, old)

        check.run_check(
            paths,
            installed=_installed("v0.3.0"),
            github_factory=FakeGitHub(_remote("v0.3.0")).factory,
        )

        assert managed.staged_target() is None
        assert not old.exists()

    def test_busy_lock_leaves_everything_untouched(self, paths, managed, monkeypatch):
        stage = FakeStage()
        monkeypatch.setattr(check, "stage_release", stage)
        holder = os.open(managed.lock_path, os.O_RDWR | os.O_CREAT)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            code = check.run_check(
                paths,
                installed=_installed("v0.3.0"),
                github_factory=FakeGitHub(_remote("v0.3.1")).factory,
            )
        finally:
            os.close(holder)

        assert code == 0
        assert stage.calls == []
        assert managed.staged_target() is None
        status = read_check_status(paths.status)
        assert status.state == "up_to_date"
        assert status.available["tag"] == "v0.3.1"

    @pytest.mark.parametrize("kind,bad_tags", [("deps", []), ("unsupported_release", ["v0.3.1"])])
    def test_stage_failure_is_recorded_and_exits_one(
        self, paths, managed, monkeypatch, kind, bad_tags
    ):
        monkeypatch.setattr(check, "stage_release", FakeStage(StageError(kind, "boom")))

        code = check.run_check(
            paths,
            installed=_installed("v0.3.0"),
            github_factory=FakeGitHub(_remote("v0.3.1")).factory,
        )

        assert code == 1
        status = read_check_status(paths.status)
        assert status.state == "failed"
        assert status.error == f"{kind}: boom"
        assert status.bad_tags == bad_tags
        assert managed.staged_target() is None
