"""Contracts for the optional Raspberry Pi desktop launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_LAUNCHER = REPO_ROOT / "scripts/setup/run-openflight.example.sh"
INSTALLER = REPO_ROOT / "scripts/setup/install_desktop_launcher.sh"


pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="desktop launcher contract tests need bash",
)


def test_example_launcher_is_valid_and_enables_the_splash():
    subprocess.run(["bash", "-n", str(EXAMPLE_LAUNCHER)], check=True)

    launcher = EXAMPLE_LAUNCHER.read_text(encoding="utf-8")
    assert "flock -n" in launcher
    assert "--startup-splash" in launcher
    assert 'scripts/start-kiosk.sh "${openflight_args[@]}"' in launcher
    assert "lxterminal" not in launcher


def test_installer_creates_terminal_free_desktop_entry_and_preserves_launcher(tmp_path):
    home = tmp_path / "home"
    desktop = home / "Desktop"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "OPENFLIGHT_DESKTOP_DIR": str(desktop),
        "OPENFLIGHT_SKIP_DESKTOP_TRUST": "true",
    }

    subprocess.run(["bash", str(INSTALLER)], check=True, cwd=REPO_ROOT, env=env)

    launcher_path = home / "run-openflight.sh"
    desktop_path = desktop / "OpenFlight.desktop"
    assert launcher_path.exists()
    assert os.access(launcher_path, os.X_OK)
    desktop_entry = desktop_path.read_text(encoding="utf-8")
    assert f"Exec=/bin/bash -lc {launcher_path}" in desktop_entry
    assert "Terminal=false" in desktop_entry
    assert "StartupNotify=false" in desktop_entry
    assert "lxterminal" not in desktop_entry
    assert "/home/coleman" not in desktop_entry
    assert os.access(desktop_path, os.X_OK)

    launcher_path.write_text("#!/bin/bash\n# local calibration\n", encoding="utf-8")
    subprocess.run(["bash", str(INSTALLER)], check=True, cwd=REPO_ROOT, env=env)
    assert launcher_path.read_text(encoding="utf-8") == ("#!/bin/bash\n# local calibration\n")


def test_installer_requires_explicit_replace_and_backs_up_existing_desktop_entry(tmp_path):
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    desktop_path = desktop / "OpenFlight.desktop"
    existing_entry = "[Desktop Entry]\nName=My calibrated OpenFlight\n"
    desktop_path.write_text(existing_entry, encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(home),
        "OPENFLIGHT_DESKTOP_DIR": str(desktop),
        "OPENFLIGHT_SKIP_DESKTOP_TRUST": "true",
    }

    preserved = subprocess.run(
        ["bash", str(INSTALLER)],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert desktop_path.read_text(encoding="utf-8") == existing_entry
    assert "Existing desktop entry preserved" in preserved.stdout
    assert not list(desktop.glob("OpenFlight.desktop.backup-*"))

    subprocess.run(
        ["bash", str(INSTALLER), "--replace-desktop"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    assert "Terminal=false" in desktop_path.read_text(encoding="utf-8")
    backups = list(desktop.glob("OpenFlight.desktop.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == existing_entry


def test_main_setup_uses_the_terminal_free_launcher_installer():
    setup_script = (REPO_ROOT / "scripts/setup/setup.sh").read_text(encoding="utf-8")

    assert '"$SCRIPT_DIR/install_desktop_launcher.sh"' in setup_script
