"""Setup answers must persist hardware flags into the local launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_LAUNCHER = REPO_ROOT / "scripts/setup/run-openflight.example.sh"
HELPER = REPO_ROOT / "scripts/setup/launcher_args.sh"
SETUP = REPO_ROOT / "scripts/setup/setup.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="launcher arg helper tests need bash",
)


def _bash_env(values: dict[str, str], path_keys: set[str]) -> dict[str, str]:
    """Pass selected variables into WSL bash; /p translates Windows paths."""
    env = {**os.environ, **values}
    wslenv = [part for part in env.get("WSLENV", "").split(":") if part]
    for name in values:
        marker = f"{name}/p" if name in path_keys else name
        if marker not in wslenv:
            wslenv.append(marker)
    env["WSLENV"] = ":".join(wslenv)
    return env


def _active_args(text: str) -> list[str]:
    """Return uncommented entries inside openflight_args=(...)."""
    inside = False
    args: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("openflight_args=("):
            inside = True
            continue
        if inside and line.startswith(")"):
            break
        if not inside or not line or line.startswith("#"):
            continue
        args.extend(line.split())
    return args


def _apply(
    launcher: Path,
    kld7_tilt: str = "",
    battery: str = "false",
    mock: str = "false",
) -> subprocess.CompletedProcess[str]:
    env = _bash_env(
        {
            "OPENFLIGHT_HELPER": str(HELPER.resolve()),
            "OPENFLIGHT_LAUNCHER": str(launcher.resolve()),
            "OPENFLIGHT_KLD7_TILT": kld7_tilt,
            "OPENFLIGHT_BATTERY": battery,
            "OPENFLIGHT_MOCK": mock,
        },
        {"OPENFLIGHT_HELPER", "OPENFLIGHT_LAUNCHER"},
    )
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$OPENFLIGHT_HELPER"; apply_hardware_launcher_flags "$OPENFLIGHT_LAUNCHER" "$OPENFLIGHT_KLD7_TILT" "$OPENFLIGHT_BATTERY" "$OPENFLIGHT_MOCK"',
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_helper_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-c", 'bash -n "$OPENFLIGHT_HELPER"'],
        check=False,
        capture_output=True,
        text=True,
        env=_bash_env({"OPENFLIGHT_HELPER": str(HELPER.resolve())}, {"OPENFLIGHT_HELPER"}),
    )
    assert result.returncode == 0, result.stderr


def test_geekworm_answer_uncomments_battery_flag(tmp_path):
    launcher = tmp_path / "run-openflight.sh"
    launcher.write_text(EXAMPLE_LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")

    result = _apply(launcher, battery="true")

    assert result.returncode == 0, result.stderr
    args = _active_args(launcher.read_text(encoding="utf-8"))
    assert args[args.index("--battery") + 1] == "geekworm"
    assert "# --battery geekworm" not in launcher.read_text(encoding="utf-8")


def test_kld7_answer_adds_required_mount_tilt(tmp_path):
    launcher = tmp_path / "run-openflight.sh"
    launcher.write_text(EXAMPLE_LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")

    result = _apply(launcher, kld7_tilt="10.5")

    assert result.returncode == 0, result.stderr
    args = _active_args(launcher.read_text(encoding="utf-8"))
    assert "--kld7" in args
    assert args[args.index("--kld7-mount-tilt") + 1] == "10.5"
    assert args.count("--kld7") == 1
    assert args.count("--kld7-mount-tilt") == 1
    assert "--mock" not in args


def test_reapplying_flags_is_idempotent_and_updates_tilt(tmp_path):
    launcher = tmp_path / "run-openflight.sh"
    launcher.write_text(EXAMPLE_LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")

    first = _apply(launcher, kld7_tilt="8", battery="true")
    second = _apply(launcher, kld7_tilt="12", battery="true")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    args = _active_args(launcher.read_text(encoding="utf-8"))
    assert args.count("--kld7") == 1
    assert args.count("--battery") == 1
    assert args[args.index("--kld7-mount-tilt") + 1] == "12"
    assert args[args.index("--battery") + 1] == "geekworm"


def test_ops_no_adds_mock_flag(tmp_path):
    launcher = tmp_path / "run-openflight.sh"
    launcher.write_text(EXAMPLE_LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")

    result = _apply(launcher, mock="true")

    assert result.returncode == 0, result.stderr
    args = _active_args(launcher.read_text(encoding="utf-8"))
    assert args.count("--mock") == 1


def test_ops_yes_removes_mock_flag(tmp_path):
    launcher = tmp_path / "run-openflight.sh"
    launcher.write_text(EXAMPLE_LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")

    added = _apply(launcher, mock="true")
    removed = _apply(launcher, mock="false")

    assert added.returncode == 0, added.stderr
    assert removed.returncode == 0, removed.stderr
    assert "--mock" not in _active_args(launcher.read_text(encoding="utf-8"))


def test_setup_applies_hardware_answers_to_the_launcher():
    setup = SETUP.read_text(encoding="utf-8")

    assert 'source "$SCRIPT_DIR/launcher_args.sh"' in setup
    assert "apply_hardware_launcher_flags" in setup
    assert "ENABLE_MOCK=true" in setup
    assert "KLD7_MOUNT_TILT" in setup
    assert 'confirm "Configure the OPS243-A radar now? (it must be plugged in)"' in setup
    assert 'confirm "Do you have K-LD7 angle radars to set up?"' in setup
    assert 'confirm "Do you have a Geekworm X1202 or X1206 UPS to set up?"' in setup
