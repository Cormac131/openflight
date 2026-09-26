"""Argument handling of scripts/hardware-test/test_iwr_firmware.py (no hardware)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "test_iwr_firmware.py"


def _load():
    spec = importlib.util.spec_from_file_location("test_iwr_firmware_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_list_prints_every_section_and_check_without_a_port(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)  # opening a port would raise TypeError

    code = script.main(["--list"])

    out = capsys.readouterr().out
    assert code == 0
    assert "lifecycle (sensor: active)" in out
    assert "trigger-swing (sensor: active, needs --swing)" in out
    assert "  trigger/fresh session untriggered" in out


def test_cli_rejects_unknown_only_before_opening_a_port(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)

    code = script.main(["--only", "lifecycle,zzz"])

    assert code == 2
    assert "unknown section: zzz" in capsys.readouterr().err


def test_swing_with_only_that_excludes_the_swing_section_is_an_error(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)

    code = script.main(["--swing", "--only", "lifecycle"])

    assert code == 2
    assert "trigger-swing" in capsys.readouterr().err


def test_windows_port_name_is_refused_on_the_pi(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)
    monkeypatch.setattr(script.sys, "platform", "linux")

    code = script.main(["--port", "COM5"])

    assert code == 2
    assert "leave --port off" in capsys.readouterr().err
