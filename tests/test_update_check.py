"""Tests for candidate selection and the check command."""

import pytest

from openflight.release import ReleaseInfo
from openflight.update import check
from openflight.update.config import UpdateConfig, save_update_config
from openflight.update.github import ReleaseAsset, ReleaseLookupError, RemoteRelease
from openflight.update.paths import UpdatePaths
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
