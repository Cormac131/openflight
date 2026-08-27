"""Tests for start-kiosk.sh's --auto-hardware wiring and boot config.

These drive the real script with --dry-run, which prints the server command
and exits, and substitute a canned detector via OPENFLIGHT_DETECT_CMD. That
covers the two rules the SD card image depends on and that no Python test can
reach: detected flags must be applied, and a flag typed by hand must still
beat a detected one.
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
START_KIOSK = REPO_ROOT / "scripts" / "start-kiosk.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="start-kiosk.sh needs bash")


def run_kiosk(*args, detect_flags=None, env=None):
    """Run start-kiosk.sh --dry-run and return the printed server command."""
    environment = dict(os.environ)
    # Keep the host's own site settings out of the test.
    for key in ("KLD7_MOUNT_TILT", "KLD7_ANGLE_OFFSET", "NET_DISTANCE", "SESSION_LOCATION"):
        environment.pop(key, None)
    if detect_flags is not None:
        # printf's format argument comes first, so operands starting with "--"
        # are passed through rather than parsed as printf options.
        operands = " ".join(shlex.quote(flag) for flag in detect_flags)
        environment["OPENFLIGHT_DETECT_CMD"] = f"printf '%s\\n' {operands}".strip()
    environment.update(env or {})

    result = subprocess.run(
        ["bash", str(START_KIOSK), "--dry-run", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    # --dry-run prints the command as the last line of stdout.
    return result.stdout.strip().splitlines()[-1]


class TestAutoHardware:
    def test_detected_flags_reach_the_server_command(self):
        command = run_kiosk(
            "--auto-hardware",
            detect_flags=["--radar-port", "/dev/ttyACM0", "--iwr6843", "--inclinometer"],
        )
        assert "--port /dev/ttyACM0" in command
        assert "--iwr6843" in command
        assert "--inclinometer" in command

    def test_explicit_flag_beats_a_detected_one(self):
        """Detected flags are prepended, so a hand-typed value is parsed last and wins."""
        command = run_kiosk(
            "--auto-hardware",
            "--radar-port",
            "/dev/ttyAMA0",
            detect_flags=["--radar-port", "/dev/ttyACM0"],
        )
        assert "--port /dev/ttyAMA0" in command
        assert "/dev/ttyACM0" not in command

    def test_no_detected_flags_leaves_defaults_alone(self):
        command = run_kiosk("--auto-hardware", detect_flags=[])
        assert "--trigger sound" in command
        assert "--iwr6843" not in command

    def test_mock_skips_detection(self):
        """Mock mode must never touch the buses, even with --auto-hardware set."""
        command = run_kiosk("--auto-hardware", "--mock", detect_flags=["--iwr6843"])
        assert "--mock" in command
        assert "--iwr6843" not in command

    def test_auto_hardware_flag_is_not_forwarded_to_the_server(self):
        command = run_kiosk("--auto-hardware", detect_flags=[])
        assert "--auto-hardware" not in command

    def test_detection_failure_is_not_fatal(self):
        """A broken detector must still leave the owner with a running UI."""
        command = run_kiosk("--auto-hardware", env={"OPENFLIGHT_DETECT_CMD": "exit 3"})
        assert command.startswith("openflight-server")

    def test_absent_without_the_flag(self):
        command = run_kiosk(detect_flags=["--iwr6843"])
        assert "--iwr6843" not in command


class TestBootConfig:
    def test_site_geometry_arrives_from_the_environment(self):
        """The boot config sets these as environment variables; they must survive init."""
        command = run_kiosk(
            "--kld7",
            env={"KLD7_MOUNT_TILT": "12.5", "NET_DISTANCE": "3.0"},
        )
        assert "--kld7-mount-tilt 12.5" in command
        assert "--net-distance 3.0" in command

    def test_command_line_overrides_the_environment(self):
        command = run_kiosk(
            "--kld7",
            "--kld7-mount-tilt",
            "8.0",
            env={"KLD7_MOUNT_TILT": "12.5"},
        )
        assert "--kld7-mount-tilt 8.0" in command
        assert "12.5" not in command

    def test_session_location_flows_through(self):
        command = run_kiosk(env={"SESSION_LOCATION": "garage"})
        assert "--session-location garage" in command

    def test_kld7_without_mount_tilt_is_refused(self):
        """A guessed tilt silently biases launch angle, so the script must stop."""
        result = subprocess.run(
            ["bash", str(START_KIOSK), "--dry-run", "--kld7"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={k: v for k, v in os.environ.items() if k != "KLD7_MOUNT_TILT"},
            check=False,
            timeout=180,
        )
        assert result.returncode != 0
        assert "mount tilt is unset" in result.stdout + result.stderr
