"""Tests for the check status file and the composed update status."""

import json
import os

import pytest

from openflight.release import ReleaseInfo
from openflight.update import status as st
from openflight.update.config import UpdateConfig, save_update_config
from openflight.update.layout import InstallLayout, write_pending
from openflight.update.paths import UpdatePaths

STABLE = ReleaseInfo("0.3.0", "0.3.0", "stable", tag="v0.3.0", repository="o/r")
SOURCE = ReleaseInfo("0.3.0+abc", "0.3.0", "source")


@pytest.fixture
def paths(tmp_path):
    root = tmp_path / "openflight-releases"
    root.mkdir()
    return UpdatePaths(
        install_link=tmp_path / "openflight",
        releases_root=root,
        config=tmp_path / "cfg" / "update.json",
        status=tmp_path / "cfg" / "update-status.json",
    )


def _managed(paths, tree_name="v0.3.0"):
    """Make the install link point at a release tree that is also the running tree."""
    tree = paths.releases_root / tree_name
    (tree / "src" / "openflight").mkdir(parents=True)
    (tree / "src" / "openflight" / "__init__.py").write_text('__version__ = "0.3.0"\n')
    (tree / "release.json").write_text(
        json.dumps(
            {
                "version": "0.3.0",
                "base_version": "0.3.0",
                "channel": "stable",
                "tag": "v0.3.0",
                "repository": "o/r",
            }
        )
    )
    os.symlink(tree, paths.install_link)
    return tree


class TestCheckStatusFile:
    def test_round_trip_is_atomic_and_indented(self, paths):
        status = st.CheckStatus(
            state="failed", channel="stable", error="offline: x", bad_tags=["v1"]
        )
        st.write_check_status(status, paths.status)

        assert st.read_check_status(paths.status) == status
        assert not any(name.startswith(".") for name in os.listdir(paths.status.parent))
        assert json.loads(paths.status.read_text())["format_version"] == 1

    @pytest.mark.parametrize("text", ["", "{bad", "[]", '{"state": "weird"}'])
    def test_missing_or_corrupt_reads_as_fresh(self, paths, text):
        if text:
            paths.status.parent.mkdir(parents=True)
            paths.status.write_text(text)

        assert st.read_check_status(paths.status) == st.CheckStatus()

    def test_mark_bad_dedupes_and_keeps_the_newest_ten(self):
        status = st.CheckStatus()
        for n in range(12):
            status.mark_bad(f"v{n}")
        status.mark_bad("v5")

        assert len(status.bad_tags) == st.MAX_BAD_TAGS
        assert status.bad_tags[-1] == "v5"
        assert status.bad_tags.count("v5") == 1


class TestComposeStatus:
    def test_unmanaged_when_the_install_link_is_missing(self, paths):
        status = st.compose_update_status(paths, SOURCE)

        assert status["managed"] is False
        assert status["state"] == "unmanaged"
        assert status["current"] == {"tag": None, "version": "0.3.0+abc", "channel": "source"}
        assert status["staged"] is None

    def test_unmanaged_when_the_link_points_at_another_tree(self, paths, monkeypatch):
        tree = _managed(paths)
        monkeypatch.setattr(InstallLayout, "is_managed", lambda self, running_root=None: False)

        assert st.compose_update_status(paths, STABLE)["state"] == "unmanaged"
        assert tree.is_dir()

    def test_disabled_until_a_channel_is_chosen(self, paths, monkeypatch):
        _managed(paths)
        monkeypatch.setattr(InstallLayout, "is_managed", lambda self, running_root=None: True)

        assert st.compose_update_status(paths, SOURCE)["state"] == "disabled"

    def test_check_state_shows_through_when_nothing_is_staged(self, paths, monkeypatch):
        _managed(paths)
        monkeypatch.setattr(InstallLayout, "is_managed", lambda self, running_root=None: True)
        st.write_check_status(
            st.CheckStatus(state="held_back", channel="stable", available={"tag": "v0.3.1"}),
            paths.status,
        )

        status = st.compose_update_status(paths, STABLE)

        assert status["state"] == "held_back"
        assert status["available"] == {"tag": "v0.3.1"}
        assert status["channel"] == "stable"

    def test_staged_and_previous_identities_come_from_the_links(self, paths, monkeypatch):
        _managed(paths)
        monkeypatch.setattr(InstallLayout, "is_managed", lambda self, running_root=None: True)
        staged = paths.releases_root / "v0.3.1"
        (staged / "src" / "openflight").mkdir(parents=True)
        (staged / "src" / "openflight" / "__init__.py").write_text('__version__ = "0.3.1"\n')
        (staged / "release.json").write_text(
            json.dumps(
                {"version": "0.3.1", "base_version": "0.3.1", "channel": "stable", "tag": "v0.3.1"}
            )
        )
        os.symlink(staged, paths.releases_root / "staged")
        source_tree = paths.releases_root / "source-abc"
        source_tree.mkdir()
        os.symlink(source_tree, paths.releases_root / "previous")

        status = st.compose_update_status(paths, STABLE)

        assert status["state"] == "staged"
        assert status["staged"] == {
            "tag": "v0.3.1",
            "version": "0.3.1",
            "channel": "stable",
            "name": "v0.3.1",
        }
        assert status["previous"] == {
            "tag": None,
            "version": None,
            "channel": "source",
            "name": "source-abc",
        }

    def test_pending_confirm_outranks_staged(self, paths, monkeypatch):
        _managed(paths)
        monkeypatch.setattr(InstallLayout, "is_managed", lambda self, running_root=None: True)
        layout = InstallLayout(paths.install_link, paths.releases_root)
        write_pending(layout, "v0.3.0", "source-abc", "2026-09-06T00:00:00+00:00")

        status = st.compose_update_status(paths, STABLE)

        assert status["state"] == "pending_confirm"
        assert status["pending_confirm"] is True

    def test_preference_file_is_reflected(self, paths, monkeypatch):
        _managed(paths)
        monkeypatch.setattr(InstallLayout, "is_managed", lambda self, running_root=None: True)
        save_update_config(UpdateConfig("experimental", "me/fork"), paths.config)

        status = st.compose_update_status(paths, STABLE)

        assert status["channel"] == "experimental"
        assert status["repository"] == "me/fork"
