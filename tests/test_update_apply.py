"""Tests for apply / confirm / rollback (openflight.update.apply)."""

import os

import pytest

from openflight.update import apply as ap
from openflight.update.layout import InstallLayout, UpdateError, read_pending, write_pending
from openflight.update.status import CheckStatus, read_check_status, write_check_status


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "openflight-releases"
    root.mkdir()
    return InstallLayout(install_link=tmp_path / "openflight", releases_root=root)


@pytest.fixture
def status_path(tmp_path):
    return tmp_path / "update-status.json"


def _tree(layout, name):
    tree = layout.releases_root / name
    (tree / "config").mkdir(parents=True)
    return tree


def _managed(layout, current="v0.3.0", staged="v0.3.1"):
    cur = _tree(layout, current)
    os.symlink(cur, layout.install_link)
    new = _tree(layout, staged) if staged else None
    if new is not None:
        os.symlink(new, layout.staged_link)
    return cur, new


class TestApply:
    def test_swaps_links_and_records_the_pending_marker(self, layout):
        cur, new = _managed(layout)
        (cur / "config" / "sim.json").write_text('{"targets": []}')

        applied = ap.apply_staged(layout, now=lambda: "2026-09-06T00:00:00+00:00")

        assert applied == "v0.3.1"
        assert layout.current_target() == new
        assert layout.previous_target() == cur
        assert layout.staged_target() is None
        assert read_pending(layout) == {
            "format_version": 1,
            "tag": "v0.3.1",
            "previous": "v0.3.0",
            "applied_at": "2026-09-06T00:00:00+00:00",
        }
        assert (new / "config" / "sim.json").read_text() == '{"targets": []}'

    def test_does_not_overwrite_an_existing_sim_config(self, layout):
        cur, new = _managed(layout)
        (cur / "config" / "sim.json").write_text("old")
        (new / "config" / "sim.json").write_text("new")

        ap.apply_staged(layout)

        assert (new / "config" / "sim.json").read_text() == "new"

    def test_nothing_staged_returns_none(self, layout):
        _managed(layout, staged=None)
        assert ap.apply_staged(layout) is None
        assert read_pending(layout) is None

    def test_staged_equal_to_current_is_cleaned_up(self, layout):
        cur, _ = _managed(layout, staged=None)
        os.symlink(cur, layout.staged_link)

        assert ap.apply_staged(layout) is None
        assert layout.staged_target() is None

    def test_dangling_staged_link_is_removed_and_reported(self, layout):
        _managed(layout, staged=None)
        os.symlink(layout.releases_root / "gone", layout.staged_link)

        with pytest.raises(UpdateError, match="missing"):
            ap.apply_staged(layout)
        assert layout.staged_target() is None

    def test_unmanaged_install_is_refused(self, layout):
        layout.install_link.mkdir()
        os.symlink(_tree(layout, "v0.3.1"), layout.staged_link)

        with pytest.raises(UpdateError, match="not a symlink"):
            ap.apply_staged(layout)

    def test_pending_marker_for_another_tag_blocks_apply(self, layout):
        _managed(layout)
        write_pending(layout, "v0.2.9", "v0.2.8", "x")

        with pytest.raises(UpdateError, match="awaiting confirmation"):
            ap.apply_staged(layout)

    def test_reapply_with_the_same_marker_is_idempotent(self, layout):
        cur, new = _managed(layout)
        write_pending(layout, "v0.3.1", "v0.3.0", "earlier")

        assert ap.apply_staged(layout) == "v0.3.1"
        assert read_pending(layout)["applied_at"] == "earlier"
        assert layout.current_target() == new


class TestConfirm:
    def test_clears_marker_and_prunes(self, layout):
        cur, new = _managed(layout)
        orphan = _tree(layout, "v0.1.0")
        ap.apply_staged(layout)

        assert ap.confirm_applied(layout) is True
        assert read_pending(layout) is None
        assert not orphan.exists()
        assert cur.is_dir() and new.is_dir()

    def test_nothing_pending(self, layout):
        _managed(layout, staged=None)
        assert ap.confirm_applied(layout) is False


class TestRollback:
    def test_returns_to_previous_and_remembers_the_bad_tag(self, layout, status_path):
        cur, new = _managed(layout)
        ap.apply_staged(layout)
        write_check_status(CheckStatus(state="up_to_date", channel="stable"), status_path)

        failed = ap.rollback_pending(layout, status_path, reason="server timed out")

        assert failed == "v0.3.1"
        assert layout.current_target() == cur
        assert layout.previous_target() is None
        assert read_pending(layout) is None
        status = read_check_status(status_path)
        assert status.state == "failed"
        assert status.error == "startup_failed: server timed out"
        assert status.bad_tags == ["v0.3.1"]

    def test_nothing_pending_without_force(self, layout, status_path):
        _managed(layout, staged=None)
        assert ap.rollback_pending(layout, status_path, reason="x") is None

    def test_force_rolls_back_the_current_release(self, layout, status_path):
        cur, new = _managed(layout)
        ap.apply_staged(layout)
        ap.confirm_applied(layout)

        failed = ap.rollback_pending(layout, status_path, reason="manual", force=True)

        assert failed == "v0.3.1"
        assert layout.current_target() == cur

    def test_no_previous_is_an_error(self, layout, status_path):
        _managed(layout, staged=None)
        write_pending(layout, "v0.3.1", None, "x")

        with pytest.raises(UpdateError, match="no previous"):
            ap.rollback_pending(layout, status_path, reason="x")
