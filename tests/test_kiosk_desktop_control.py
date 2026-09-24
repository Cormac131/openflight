"""Show desktop / Return to OpenFlight: server request -> kiosk supervisor -> launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from openflight.connectivity.desktop import DesktopControl

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROL_LIB = REPO_ROOT / "scripts/kiosk-control.sh"
START_KIOSK = REPO_ROOT / "scripts/start-kiosk.sh"
RETURN_SCRIPT = REPO_ROOT / "scripts/return-to-openflight.sh"
INSTALLER = REPO_ROOT / "scripts/setup/install_desktop_launcher.sh"
EXAMPLE_LAUNCHER = REPO_ROOT / "scripts/setup/run-openflight.example.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run_lib(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -eo pipefail\n. "{CONTROL_LIB}"\n{script}'],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestControlLibrary:
    def test_dir_uses_runtime_dir_and_port(self):
        result = run_lib("kiosk_control_dir 8123", {"XDG_RUNTIME_DIR": "/run/user/1000"})
        assert result.stdout.strip() == "/run/user/1000/openflight-kiosk-8123"

    def test_prepare_creates_private_dir_and_clears_stale_requests(self, tmp_path):
        control = tmp_path / "ctl"
        control.mkdir()
        (control / "show-desktop").write_text("", encoding="utf-8")
        result = run_lib(f'kiosk_control_prepare "{control}" 4242')
        assert result.returncode == 0, result.stderr
        assert oct(control.stat().st_mode & 0o777) == "0o700"
        assert (control / "kiosk.pid").read_text().strip() == "4242"
        assert (control / "mode").read_text().strip() == "kiosk"
        assert not (control / "show-desktop").exists()

    def test_prepare_refuses_symlink(self, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = tmp_path / "ctl"
        link.symlink_to(target)
        assert run_lib(f'kiosk_control_prepare "{link}" 1').returncode == 1
        assert not (target / "kiosk.pid").exists()

    def test_symlinked_dir_is_never_a_live_supervisor(self, tmp_path):
        real = tmp_path / "real"
        run_lib(f'kiosk_control_prepare "{real}" {os.getpid()}')
        (tmp_path / "ctl").symlink_to(real)
        assert run_lib(f'kiosk_control_request "{tmp_path / "ctl"}" return').returncode == 1
        assert not (real / "return").exists()

    def test_request_needs_live_supervisor(self, tmp_path):
        control = tmp_path / "ctl"
        assert run_lib(f'kiosk_control_request "{control}" return').returncode == 1
        run_lib(f'kiosk_control_prepare "{control}" 999999999')
        assert run_lib(f'kiosk_control_request "{control}" return').returncode == 1
        run_lib(f'kiosk_control_prepare "{control}" {os.getpid()}')
        assert run_lib(f'kiosk_control_request "{control}" return').returncode == 0
        assert (control / "return").exists()
        assert run_lib(f'kiosk_control_take_request "{control}" return').returncode == 0
        assert run_lib(f'kiosk_control_take_request "{control}" return').returncode == 1

    def test_cleanup_removes_everything(self, tmp_path):
        control = tmp_path / "ctl"
        run_lib(f'kiosk_control_prepare "{control}" 1')
        assert run_lib(f'kiosk_control_cleanup "{control}"').returncode == 0
        assert not control.exists()
        assert run_lib('kiosk_control_cleanup ""').returncode == 0

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            ({"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}, 0),
            ({"DISPLAY": "", "WAYLAND_DISPLAY": "wayland-0"}, 0),
        ],
    )
    def test_desktop_session_detection(self, env, expected):
        assert run_lib("desktop_session_available", env).returncode == expected


def supervisor_harness(tmp_path: Path, control: Path) -> subprocess.Popen:
    """Run start-kiosk.sh's real supervisor functions with fake processes."""
    script = START_KIOSK.read_text(encoding="utf-8")
    start = script.index("close_kiosk_browser() {")
    end = script.index("\ncleanup() {", start)
    close_fn = script[start:end]
    start = script.index("show_desktop() {")
    end = script.index("\nstart_alloy() {", start)
    supervisor_fns = script[start:end]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "pkill").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
    (fake_bin / "pkill").chmod(0o755)
    launches = tmp_path / "launches"
    harness = f"""
set -eo pipefail
. "{CONTROL_LIB}"
log() {{ printf '%s\\n' "$1"; }}
warn() {{ printf 'warn %s\\n' "$1"; }}
HOST=localhost
WEB_PORT=8080
launch_kiosk_browser() {{
    printf '%s\\n' "$1" >> "{launches}"
    sleep 60 >/dev/null 2>&1 &
    BROWSER_PID=$!
    printf '%s\\n' "$BROWSER_PID" > "{tmp_path}/browser.pid"
    BROWSER_LAUNCHED=true
}}
{close_fn}
{supervisor_fns}
KIOSK_CONTROL_DIR="{control}"
kiosk_control_prepare "$KIOSK_CONTROL_DIR" "$$"
launch_kiosk_browser "http://$HOST:$WEB_PORT"
sleep 60 >/dev/null 2>&1 &
SERVER_PID=$!
printf '%s\\n' "$SERVER_PID" > "{tmp_path}/server.pid"
supervise_kiosk
printf 'supervisor-exited\\n'
"""
    return subprocess.Popen(
        ["bash", "-c", harness],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Killed grandchildren may linger as zombies; treat <defunct> as dead.
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split()[2]
    except OSError:
        return False
    return status != "Z"


class TestSupervisorRoundTrip:
    def test_show_desktop_then_return(self, tmp_path):
        control = tmp_path / "openflight-kiosk-8080"  # what the launcher computes
        proc = supervisor_harness(tmp_path, control)
        try:
            assert wait_until(lambda: (tmp_path / "server.pid").exists())
            first_browser = int((tmp_path / "browser.pid").read_text())

            # 1. The server (Python) asks for the desktop.
            desktop = DesktopControl(control)
            assert desktop.status() == {"available": True, "reason": None, "mode": "kiosk"}
            assert desktop.request_show_desktop()
            assert wait_until(lambda: not pid_alive(first_browser))
            assert wait_until(lambda: desktop.status()["mode"] == "desktop")

            # 2. Nothing relaunches the kiosk by itself: the desktop stays usable.
            time.sleep(1.2)
            assert (tmp_path / "launches").read_text().count("\n") == 1

            # 3. The desktop launcher brings the kiosk back.
            subprocess.run(
                ["bash", str(RETURN_SCRIPT), "--no-start"],
                env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)},
                check=True,
            )
            assert wait_until(lambda: (tmp_path / "launches").read_text().count("\n") == 2)
            assert wait_until(lambda: DesktopControl(control).status()["mode"] == "kiosk")
            assert (tmp_path / "launches").read_text().splitlines()[-1] == "http://localhost:8080"

            # 4. Server exit ends supervision.
            os.kill(int((tmp_path / "server.pid").read_text()), 15)
            output, _ = proc.communicate(timeout=5)
            assert "supervisor-exited" in output
        finally:
            if proc.poll() is None:
                proc.kill()
            for name in ("browser.pid", "server.pid"):
                path = tmp_path / name
                if path.exists():
                    try:
                        os.kill(int(path.read_text()), 9)
                    except ProcessLookupError:
                        pass


class TestReturnLauncher:
    def test_starts_launcher_when_openflight_is_not_running(self, tmp_path):
        marker = tmp_path / "started"
        launcher = tmp_path / "run-openflight.sh"
        launcher.write_text(f'#!/bin/bash\ntouch "{marker}"\n', encoding="utf-8")
        launcher.chmod(0o755)
        subprocess.run(
            ["bash", str(RETURN_SCRIPT), "--launcher", str(launcher)],
            env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)},
            check=True,
        )
        assert marker.exists()

    def test_rejects_unknown_options(self):
        result = subprocess.run(
            ["bash", str(RETURN_SCRIPT), "--rm-rf"], capture_output=True, text=True, check=False
        )
        assert result.returncode == 2


class TestLauncherInstall:
    def test_installer_adds_return_entry_to_desktop_and_menu(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "OPENFLIGHT_DESKTOP_DIR": str(home / "Desktop"),
            "OPENFLIGHT_APPLICATIONS_DIR": str(home / "apps"),
            "OPENFLIGHT_SKIP_DESKTOP_TRUST": "true",
        }
        subprocess.run(["bash", str(INSTALLER)], check=True, cwd=REPO_ROOT, env=env)
        for directory in (home / "Desktop", home / "apps"):
            entries = list(directory.glob("Return-to-OpenFlight*.desktop"))
            assert len(entries) == 1
            text = entries[0].read_text(encoding="utf-8")
            assert "Name=Return to OpenFlight" in text
            assert f"{REPO_ROOT}/scripts/return-to-openflight.sh --launcher {home}/" in text
            assert "Terminal=false" in text
            assert os.access(entries[0], os.X_OK)

    def test_example_launcher_returns_to_running_kiosk(self):
        text = EXAMPLE_LAUNCHER.read_text(encoding="utf-8")
        assert 'scripts/return-to-openflight.sh" --no-start' in text

    def test_start_kiosk_supervises_instead_of_bare_wait(self):
        text = START_KIOSK.read_text(encoding="utf-8")
        assert "prepare_kiosk_control\n" in text
        assert 'supervise_kiosk\nwait "$SERVER_PID"' in text
        assert 'kiosk_control_cleanup "$KIOSK_CONTROL_DIR"' in text
