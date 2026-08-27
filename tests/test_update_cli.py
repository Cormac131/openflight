"""Tests for the openflight-update CLI argument wiring."""

import pytest

from openflight.update import cli


class TestArgParsing:
    def test_requires_subcommand(self):
        rc = cli.main([])
        assert rc != 0

    def test_unknown_subcommand_errors(self):
        with pytest.raises(SystemExit):
            cli.main(["frobnicate"])


class TestDispatch:
    def test_check_dispatches_and_passes_dry_run(self, monkeypatch, tmp_path):
        captured = {}

        def fake_check(config, config_path, client, dry_run=False, out=print):
            captured["dry_run"] = dry_run
            return {}

        monkeypatch.setattr(cli.commands, "cmd_check", fake_check)
        rc = cli.main(["check", "--dry-run", "--config", str(tmp_path / "c.json")])
        assert rc == 0
        assert captured["dry_run"] is True

    def test_check_returns_nonzero_on_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_check", lambda *a, **k: {"error": "boom"})
        rc = cli.main(["check", "--config", str(tmp_path / "c.json")])
        assert rc != 0

    def test_apply_passes_tag_and_dry_run(self, monkeypatch, tmp_path):
        captured = {}

        def fake_apply(config, config_path, tag=None, dry_run=False, out=print):
            captured.update(tag=tag, dry_run=dry_run)
            return {"swapped": True, "tag": tag, "error": None}

        monkeypatch.setattr(cli.commands, "cmd_apply", fake_apply)
        rc = cli.main(
            ["apply", "--tag", "v9.9.9", "--dry-run", "--config", str(tmp_path / "c.json")]
        )
        assert rc == 0
        assert captured == {"tag": "v9.9.9", "dry_run": True}

    def test_apply_noop_is_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli.commands,
            "cmd_apply",
            lambda *a, **k: {"swapped": False, "tag": None, "error": None},
        )
        rc = cli.main(["apply", "--config", str(tmp_path / "c.json")])
        assert rc == 0

    def test_apply_returns_nonzero_on_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli.commands, "cmd_apply", lambda *a, **k: {"swapped": False, "error": "missing"}
        )
        rc = cli.main(["apply", "--tag", "v9.9.9", "--config", str(tmp_path / "c.json")])
        assert rc != 0

    def test_rollback_dispatches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_rollback", lambda *a, **k: True)
        rc = cli.main(["rollback", "--config", str(tmp_path / "c.json")])
        assert rc == 0

    def test_rollback_returns_nonzero_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_rollback", lambda *a, **k: False)
        rc = cli.main(["rollback", "--config", str(tmp_path / "c.json")])
        assert rc != 0

    def test_status_dispatches(self, monkeypatch, tmp_path):
        called = {}

        def fake_status(config, out=print):
            called["ran"] = True
            return {}

        monkeypatch.setattr(cli.commands, "cmd_status", fake_status)
        rc = cli.main(["status", "--config", str(tmp_path / "c.json")])
        assert rc == 0
        assert called["ran"] is True

    def test_bootstrap_dispatches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_bootstrap", lambda *a, **k: True)
        rc = cli.main(["bootstrap", "--config", str(tmp_path / "c.json")])
        assert rc == 0

    def test_bootstrap_returns_nonzero_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_bootstrap", lambda *a, **k: False)
        rc = cli.main(["bootstrap", "--config", str(tmp_path / "c.json")])
        assert rc != 0

    def test_bootstrap_install_dir_overrides_config(self, monkeypatch, tmp_path):
        captured = {}

        def fake_bootstrap(config, config_path, out=print, run_fn=None):
            captured["install_dir"] = config.install_dir
            return True

        monkeypatch.setattr(cli.commands, "cmd_bootstrap", fake_bootstrap)
        rc = cli.main(
            [
                "bootstrap",
                "--install-dir",
                str(tmp_path / "custom"),
                "--config",
                str(tmp_path / "c.json"),
            ]
        )
        assert rc == 0
        assert captured["install_dir"] == str(tmp_path / "custom")
