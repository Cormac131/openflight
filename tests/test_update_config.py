"""Tests for the openflight-update config module."""

import json
import stat

from openflight.update import config as cfg


class TestLoadConfig:
    def test_returns_none_when_file_absent(self, tmp_path):
        assert cfg.load_config(tmp_path / "update.json") is None

    def test_loads_all_fields(self, tmp_path):
        path = tmp_path / "update.json"
        path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "repo": "someone/fork",
                    "releases_dir": "/releases",
                    "install_dir": "/install",
                    "keep_releases": 3,
                    "etag": '"e"',
                    "active_tag": "v1.0.0",
                    "pending_tag": "v1.1.0",
                    "previous_tag": "v0.9.0",
                    "last_check_at": "2026-01-01T00:00:00Z",
                    "last_error": "boom",
                }
            )
        )
        loaded = cfg.load_config(path)
        assert loaded == cfg.UpdateConfig(
            enabled=True,
            repo="someone/fork",
            releases_dir="/releases",
            install_dir="/install",
            keep_releases=3,
            etag='"e"',
            active_tag="v1.0.0",
            pending_tag="v1.1.0",
            previous_tag="v0.9.0",
            last_check_at="2026-01-01T00:00:00Z",
            last_error="boom",
        )

    def test_defaults_missing_fields(self, tmp_path):
        path = tmp_path / "update.json"
        path.write_text(json.dumps({"active_tag": "v1.0.0"}))
        loaded = cfg.load_config(path)
        assert loaded.enabled is False
        assert loaded.repo == cfg.DEFAULT_REPO
        assert loaded.keep_releases == cfg.DEFAULT_KEEP_RELEASES
        assert loaded.active_tag == "v1.0.0"


class TestSaveConfig:
    def test_writes_file_with_0600_permissions(self, tmp_path):
        path = tmp_path / "nested" / "update.json"
        cfg.save_config(cfg.UpdateConfig(active_tag="v1.0.0"), path)
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_round_trips_through_load(self, tmp_path):
        path = tmp_path / "update.json"
        config = cfg.UpdateConfig(
            enabled=True,
            repo="o/r",
            active_tag="v1.0.0",
            pending_tag="v1.1.0",
        )
        cfg.save_config(config, path)
        assert cfg.load_config(path) == config


class TestPaths:
    def test_releases_path_and_install_path_are_paths(self, tmp_path):
        config = cfg.UpdateConfig(
            releases_dir=str(tmp_path / "releases"), install_dir=str(tmp_path / "install")
        )
        assert config.releases_path() == tmp_path / "releases"
        assert config.install_path() == tmp_path / "install"
