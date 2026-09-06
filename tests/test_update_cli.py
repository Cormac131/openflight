"""Tests for the openflight-update command-line entry point."""

import json

import pytest

from openflight.release import ReleaseInfo
from openflight.update import cli
from openflight.update.config import load_update_config

SOURCE = ReleaseInfo("0.3.0+abc", "0.3.0", "source")


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENFLIGHT_INSTALL_LINK", str(tmp_path / "openflight"))
    monkeypatch.setenv("OPENFLIGHT_RELEASES_ROOT", str(tmp_path / "releases"))
    monkeypatch.setenv("OPENFLIGHT_UPDATE_CONFIG", str(tmp_path / "update.json"))
    monkeypatch.setenv("OPENFLIGHT_UPDATE_STATUS", str(tmp_path / "update-status.json"))
    monkeypatch.setattr(cli, "get_release_info", lambda: SOURCE)
    return tmp_path


def test_no_command_prints_help_and_fails(capsys):
    assert cli.main([]) == cli.EXIT_ERROR
    assert "usage:" in capsys.readouterr().out


def test_status_prints_a_summary(capsys):
    assert cli.main(["status"]) == cli.EXIT_OK

    out = capsys.readouterr().out
    assert "State:      unmanaged" in out
    assert "Channel:    off (open-flight/openflight)" in out
    assert "Current:    0.3.0+abc (source)" in out
    assert "Last check: never" in out


def test_status_json_is_the_composed_payload(capsys):
    assert cli.main(["status", "--json"]) == cli.EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unmanaged"
    assert payload["current"]["channel"] == "source"


def test_set_channel_persists_and_off_clears(tmp_path, capsys):
    assert cli.main(["set-channel", "experimental", "--repository", "me/fork"]) == cli.EXIT_OK
    assert load_update_config(tmp_path / "update.json", SOURCE).channel == "experimental"
    assert "Channel: experimental (me/fork)" in capsys.readouterr().out

    assert cli.main(["set-channel", "off"]) == cli.EXIT_OK
    config = load_update_config(tmp_path / "update.json", SOURCE)
    assert config.channel is None
    assert config.repository == "me/fork"


def test_set_channel_rejects_unknown_values():
    with pytest.raises(SystemExit):
        cli.main(["set-channel", "nightly"])


def test_check_runs_and_reports_exit_code(monkeypatch):
    calls = []

    def fake_run_check(paths, installed=None):
        calls.append((paths.config, installed))
        return 0

    monkeypatch.setattr("openflight.update.check.run_check", fake_run_check)

    assert cli.main(["check"]) == 0
    assert calls[0][1] is SOURCE


def test_install_link_option_overrides_the_environment(tmp_path, capsys):
    other = tmp_path / "other"
    assert cli.main(["--install-link", str(other), "status", "--json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["managed"] is False
