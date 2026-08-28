"""Tests for start-kiosk.sh's --auto-hardware wiring and boot config.

These drive the real script with --dry-run, which prints the server command
and exits, and substitute a canned detector via OPENFLIGHT_DETECT_CMD. That
covers the two rules the SD card image depends on and that no Python test can
reach: detected flags must be applied, and a flag typed by hand must still
beat a detected one.
"""

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from posix_shell import find_bash, posix_path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASH = find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None,
    reason="start-kiosk.sh needs Git Bash; WSL bash.exe drops Windows env vars",
)


def run_kiosk(*args, detect_flags=None, env=None, check=True):
    """Run start-kiosk.sh --dry-run and return the completed process.

    The script path is relative with cwd=repo, matching test_start_kiosk.py:
    Git Bash on Windows cannot exec a ``C:\\...`` path (backslashes vanish).
    """
    environment = dict(os.environ)
    # Keep the host's own site settings out of the test.
    for key in (
        "KLD7_MOUNT_TILT",
        "KLD7_ANGLE_OFFSET",
        "NET_DISTANCE",
        "SESSION_LOCATION",
        "OPENFLIGHT_DETECT_CMD",
        "OPENFLIGHT_BOOT_CONFIG",
        "OPS243_UART",
    ):
        environment.pop(key, None)
    if detect_flags is not None:
        # printf's format argument comes first, so operands starting with "--"
        # are passed through rather than parsed as printf options.
        operands = " ".join(shlex.quote(flag) for flag in detect_flags)
        environment["OPENFLIGHT_DETECT_CMD"] = f"printf '%s\\n' {operands}".strip()
    environment.update(env or {})
    if "OPENFLIGHT_BOOT_CONFIG" in environment:
        environment["OPENFLIGHT_BOOT_CONFIG"] = posix_path(environment["OPENFLIGHT_BOOT_CONFIG"])

    result = subprocess.run(
        [BASH, "scripts/start-kiosk.sh", "--dry-run", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        timeout=180,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def server_command(*args, **kwargs):
    """The server command line printed by --dry-run."""
    return run_kiosk(*args, **kwargs).stdout.strip().splitlines()[-1]


class TestAutoHardware:
    def test_detected_flags_reach_the_server_command(self):
        command = server_command(
            "--auto-hardware",
            detect_flags=["--radar-port", "/dev/ttyACM0", "--iwr6843", "--inclinometer"],
        )
        assert "--port /dev/ttyACM0" in command
        assert "--iwr6843" in command
        assert "--inclinometer" in command

    def test_explicit_flag_beats_a_detected_one(self):
        """Detected flags are prepended, so a hand-typed value is parsed last and wins."""
        command = server_command(
            "--auto-hardware",
            "--radar-port",
            "/dev/ttyAMA0",
            detect_flags=["--radar-port", "/dev/ttyACM0"],
        )
        assert "--port /dev/ttyAMA0" in command
        assert "/dev/ttyACM0" not in command

    def test_no_detected_flags_leaves_defaults_alone(self):
        command = server_command("--auto-hardware", detect_flags=[])
        assert "--trigger sound" in command
        assert "--iwr6843" not in command

    def test_mock_skips_detection(self):
        """Mock mode must never touch the buses, even with --auto-hardware set."""
        command = server_command("--auto-hardware", "--mock", detect_flags=["--iwr6843"])
        assert "--mock" in command
        assert "--iwr6843" not in command

    def test_auto_hardware_flag_is_not_forwarded_to_the_server(self):
        command = server_command("--auto-hardware", detect_flags=[])
        assert "--auto-hardware" not in command

    def test_detection_failure_is_not_fatal(self):
        """A broken detector must still leave the owner with a running UI."""
        command = server_command("--auto-hardware", env={"OPENFLIGHT_DETECT_CMD": "exit 3"})
        assert command.startswith("openflight-server")

    def test_absent_without_the_flag(self):
        command = server_command(detect_flags=["--iwr6843"])
        assert "--iwr6843" not in command


class TestBootConfig:
    def test_site_geometry_arrives_from_the_environment(self):
        """The boot config sets these as environment variables; they must survive init."""
        command = server_command(
            "--kld7",
            env={"KLD7_MOUNT_TILT": "12.5", "NET_DISTANCE": "3.0"},
        )
        assert "--kld7-mount-tilt 12.5" in command
        assert "--net-distance 3.0" in command

    def test_command_line_overrides_the_environment(self):
        command = server_command(
            "--kld7",
            "--kld7-mount-tilt",
            "8.0",
            env={"KLD7_MOUNT_TILT": "12.5"},
        )
        assert "--kld7-mount-tilt 8.0" in command
        assert "12.5" not in command

    def test_session_location_flows_through(self):
        command = server_command(env={"SESSION_LOCATION": "garage"})
        assert "--session-location garage" in command

    def test_kld7_without_mount_tilt_is_refused(self):
        """A guessed tilt silently biases launch angle, so the script must stop."""
        result = run_kiosk("--kld7", check=False)
        assert result.returncode != 0
        assert "mount tilt is unset" in result.stdout + result.stderr

    def test_file_on_the_boot_partition_is_honoured(self, tmp_path):
        conf = tmp_path / "openflight.conf"
        conf.write_text("SESSION_LOCATION=from-conf\n", encoding="utf-8")
        command = server_command(
            env={"OPENFLIGHT_BOOT_CONFIG": str(conf)},
        )
        assert "--session-location from-conf" in command

    def test_boot_config_cannot_install_a_detector_command(self, tmp_path):
        """The parser is the only thing between a FAT file and the kiosk user."""
        conf = tmp_path / "openflight.conf"
        conf.write_text(
            "SESSION_LOCATION=from-conf\nOPENFLIGHT_DETECT_CMD=printf '%s\\n' --iwr6843\n",
            encoding="utf-8",
        )
        command = server_command(
            "--auto-hardware",
            env={"OPENFLIGHT_BOOT_CONFIG": str(conf)},
        )
        assert "--session-location from-conf" in command
        assert "--iwr6843" not in command
