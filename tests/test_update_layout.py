"""Tests for the release layout mutations: lock, symlinks, migrate, prune."""

import errno
import fcntl
import json
import os
import subprocess
from pathlib import Path

import pytest

from openflight.update import layout as lay


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "openflight-releases"
    root.mkdir()
    return lay.InstallLayout(install_link=tmp_path / "openflight", releases_root=root)


def _tree(root: Path, name: str, version="0.3.0", tag=None) -> Path:
    tree = root / name
    (tree / "src" / "openflight").mkdir(parents=True)
    (tree / "src" / "openflight" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    if tag:
        (tree / "release.json").write_text(
            json.dumps(
                {"version": tag[1:], "base_version": version, "channel": "stable", "tag": tag}
            )
        )
    return tree


class TestSymlinks:
    def test_replace_symlink_is_atomic_and_repointable(self, layout, tmp_path):
        a, b = _tree(layout.releases_root, "v1"), _tree(layout.releases_root, "v2")

        lay.replace_symlink(layout.staged_link, a)
        lay.replace_symlink(layout.staged_link, b)

        assert layout.staged_target() == b
        assert not (layout.releases_root / ".staged.tmp").exists()
        lay.remove_symlink(layout.staged_link)
        assert layout.staged_target() is None
        lay.remove_symlink(layout.staged_link)

    def test_relative_link_targets_resolve_against_the_link_directory(self, layout):
        tree = _tree(layout.releases_root, "v1")
        os.symlink("v1", layout.previous_link)

        assert layout.previous_target() == tree

    def test_is_managed_requires_a_link_to_the_running_tree(self, layout):
        tree = _tree(layout.releases_root, "v1")
        assert not layout.is_managed(tree)
        os.symlink(tree, layout.install_link)
        assert layout.is_managed(tree)
        assert not layout.is_managed(layout.releases_root)

    def test_release_info_for_reads_release_json_or_none(self, layout):
        release = _tree(layout.releases_root, "v0.3.0", tag="v0.3.0")
        source = _tree(layout.releases_root, "source-abc")

        assert layout.release_info_for(release).tag == "v0.3.0"
        assert layout.release_info_for(source) is None
        assert layout.release_info_for(None) is None


class TestLock:
    def test_second_holder_gets_busy(self, layout):
        with lay.locked(layout):
            fd = os.open(layout.lock_path, os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)

    def test_busy_when_another_process_holds_it(self, layout):
        holder = subprocess.Popen(
            [
                "python3",
                "-c",
                "import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT);"
                "fcntl.flock(fd,fcntl.LOCK_EX);print('locked',flush=True);time.sleep(5)",
                str(layout.lock_path),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "locked"
            with pytest.raises(lay.UpdateBusy):
                with lay.locked(layout):
                    pass
        finally:
            holder.kill()
            holder.wait()

    def test_lock_is_released_after_the_block(self, layout):
        with lay.locked(layout):
            pass
        with lay.locked(layout):
            pass


class TestPending:
    def test_marker_round_trip(self, layout):
        assert lay.read_pending(layout) is None
        lay.write_pending(layout, "v0.3.1", "v0.3.0", "2026-09-06T00:00:00+00:00")
        assert lay.read_pending(layout)["tag"] == "v0.3.1"
        lay.clear_pending(layout)
        assert lay.read_pending(layout) is None
        lay.clear_pending(layout)

    def test_corrupt_marker_reads_as_none(self, layout):
        layout.pending_confirm_path.write_text("{oops")
        assert lay.read_pending(layout) is None


class FakeRun:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, self.returncode)


class TestMigrate:
    def test_moves_a_checkout_and_reinstalls_the_editable_package(self, layout, monkeypatch):
        checkout = layout.install_link
        (checkout / "src" / "openflight").mkdir(parents=True)
        (checkout / "src" / "openflight" / "__init__.py").write_text('__version__ = "0.3.0"\n')
        (checkout / ".venv" / "bin").mkdir(parents=True)
        (checkout / ".venv" / "bin" / "python").write_text("")
        monkeypatch.setattr(lay, "find_uv", lambda: "/fake/uv")
        monkeypatch.setattr(
            lay, "source_release_info", lambda path: type("R", (), {"commit": "abc123"})()
        )
        run = FakeRun()

        target = lay.migrate_install(layout, running_root=checkout, run=run)

        assert target == layout.releases_root / "source-abc123"
        assert checkout.is_symlink() and layout.current_target() == target
        assert (target / "src" / "openflight" / "__init__.py").is_file()
        assert run.calls[0][0] == ["/fake/uv", "sync", "--reinstall-package", "openflight"]
        assert run.calls[0][1]["cwd"] == str(target)

    def test_names_a_release_tree_by_its_tag_and_skips_reinstall_without_a_venv(self, layout):
        checkout = _tree(layout.install_link.parent, "openflight", tag="v0.3.0")
        run = FakeRun()

        target = lay.migrate_install(layout, running_root=checkout, run=run)

        assert target == layout.releases_root / "v0.3.0"
        assert run.calls == []

    def test_already_migrated_is_idempotent(self, layout):
        tree = _tree(layout.releases_root, "v0.3.0")
        os.symlink(tree, layout.install_link)

        assert lay.migrate_install(layout, running_root=tree, run=FakeRun()) == tree

    def test_refuses_a_different_checkout_than_the_running_one(self, layout, tmp_path):
        _tree(layout.install_link.parent, "openflight")
        other = _tree(tmp_path, "elsewhere")

        with pytest.raises(lay.UpdateError, match="not the checkout"):
            lay.migrate_install(layout, running_root=other, run=FakeRun())
        assert layout.install_link.is_dir() and not layout.install_link.is_symlink()

    def test_refuses_when_the_destination_exists(self, layout):
        checkout = _tree(layout.install_link.parent, "openflight", tag="v0.3.0")
        (layout.releases_root / "v0.3.0").mkdir()

        with pytest.raises(lay.UpdateError, match="already exists"):
            lay.migrate_install(layout, running_root=checkout, run=FakeRun())

    def test_missing_directory_and_dangling_link(self, layout):
        with pytest.raises(lay.UpdateError, match="not a directory"):
            lay.migrate_install(layout, running_root=layout.releases_root, run=FakeRun())
        os.symlink(layout.releases_root / "gone", layout.install_link)
        with pytest.raises(lay.UpdateError, match="missing directory"):
            lay.migrate_install(layout, running_root=layout.releases_root, run=FakeRun())

    def test_cross_device_rename_is_reported(self, layout, monkeypatch):
        checkout = _tree(layout.install_link.parent, "openflight")

        def exdev(*_a, **_k):
            raise OSError(errno.EXDEV, "cross-device")

        monkeypatch.setattr(lay.os, "rename", exdev)
        with pytest.raises(lay.UpdateError, match="same filesystem"):
            lay.migrate_install(layout, running_root=checkout, run=FakeRun())

    def test_failed_reinstall_is_an_error_after_the_move(self, layout, monkeypatch):
        checkout = _tree(layout.install_link.parent, "openflight")
        (checkout / ".venv" / "bin").mkdir(parents=True)
        (checkout / ".venv" / "bin" / "python").write_text("")
        monkeypatch.setattr(lay, "find_uv", lambda: "/fake/uv")
        monkeypatch.setattr(
            lay, "source_release_info", lambda path: type("R", (), {"commit": None})()
        )

        with pytest.raises(lay.UpdateError, match="uv sync --reinstall-package"):
            lay.migrate_install(layout, running_root=checkout, run=FakeRun(returncode=1))
        assert layout.current_target() == layout.releases_root / "source-unknown"


class TestPrune:
    def test_keeps_linked_trees_and_source_trees_only(self, layout):
        current = _tree(layout.releases_root, "v0.3.1")
        previous = _tree(layout.releases_root, "v0.3.0")
        staged = _tree(layout.releases_root, "v0.3.2")
        orphan = _tree(layout.releases_root, "v0.2.0")
        source = _tree(layout.releases_root, "source-abc")
        layout.downloads.mkdir()
        leftover = layout.downloads / "openflight-v0.3.2.tar.gz.part"
        leftover.write_bytes(b"x")
        (layout.downloads / "extract-v0.3.2").mkdir()
        os.symlink(current, layout.install_link)
        os.symlink(previous, layout.previous_link)
        os.symlink(staged, layout.staged_link)

        removed = lay.prune(layout)

        assert set(removed) == {orphan, leftover, layout.downloads / "extract-v0.3.2"}
        assert current.is_dir() and previous.is_dir() and staged.is_dir() and source.is_dir()
        assert layout.downloads.is_dir()

    def test_missing_root_is_a_no_op(self, tmp_path):
        assert lay.prune(lay.InstallLayout(tmp_path / "x", tmp_path / "nowhere")) == []


def test_find_uv_checks_user_local_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(lay.shutil, "which", lambda _name: None)
    monkeypatch.setattr(lay.Path, "home", classmethod(lambda cls: tmp_path))
    assert lay.find_uv() is None
    uv = tmp_path / ".local" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("")
    uv.chmod(0o755)
    assert lay.find_uv() == str(uv)
