"""The launcher's update hooks: apply at start, confirm/rollback, restart on exit 75.

Behavioural tests source ``scripts/kiosk-update.sh`` into a harness with a
fake ``openflight-update`` CLI and a fake relaunch target, so the exec path,
the environment it carries and the lock descriptor it drops are all observed
from the process that inherits them.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from openflight.update import RESTART_EXIT_CODE

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt", reason="launcher tests need bash"
)


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


# --- Contract with start-kiosk.sh -----------------------------------------


def test_restart_status_matches_the_server_constant():
    assert f"OPENFLIGHT_UPDATE_RESTART_STATUS={RESTART_EXIT_CODE}" in _read(
        "scripts/kiosk-update.sh"
    )


def test_original_args_are_captured_before_parsing_consumes_them():
    script = _read("scripts/start-kiosk.sh")
    assert script.index('ORIGINAL_ARGS=("$@")') < script.index("while [[ $# -gt 0 ]]; do")


def test_staged_update_is_applied_right_after_the_lock_and_before_side_effects():
    script = _read("scripts/start-kiosk.sh")
    lock_idx = script.index("\nacquire_instance_lock\n")
    apply_idx = script.index("\napply_staged_update\n")
    ensure_idx = script.index("\nensure_kiosk_ui\n")
    assert lock_idx < apply_idx < ensure_idx
    assert 'source "$SCRIPT_DIR/kiosk-update.sh"' in script


def test_failure_rolls_back_and_readiness_confirms():
    script = _read("scripts/start-kiosk.sh")
    failure_fn = script[
        script.index("show_startup_failure() {") : script.index("shutdown_server\n")
    ]
    assert 'rollback_pending_update "$message"' in failure_fn
    assert script.index('log "Server is running!"') < script.index("\nconfirm_applied_update\n")


def test_server_exit_status_drives_restart_and_cleanup():
    script = _read("scripts/start-kiosk.sh")
    assert "wait $SERVER_PID\ncleanup\n" not in script
    tail = script[script.index('wait "$SERVER_PID" || SERVER_EXIT_STATUS=$?') :]
    assert '-eq "$OPENFLIGHT_UPDATE_RESTART_STATUS"' in tail
    assert "restart_into_staged_update" in tail
    assert 'cleanup "$SERVER_EXIT_STATUS"' in tail


def test_relaunch_waits_for_the_previous_instance_to_drop_the_lock():
    script = _read("scripts/start-kiosk.sh")
    guard = script[
        script.index("acquire_instance_lock() {") : script.index("release_instance_lock() {")
    ]
    assert 'if [ "${OPENFLIGHT_UPDATE_RESTART:-}" = 1 ]; then' in guard
    assert "flock_args=(-w 15)" in guard
    release = script[
        script.index("release_instance_lock() {") : script.index("ensure_uv_on_path() {")
    ]
    assert "exec {INSTANCE_LOCK_FD}>&-" in release


def test_kiosk_update_helper_has_valid_syntax_and_unix_newlines():
    subprocess.run(["bash", "-n", "scripts/kiosk-update.sh"], cwd=REPO_ROOT, check=True)
    assert b"\r" not in (SCRIPTS / "kiosk-update.sh").read_bytes()


# --- Behaviour ----------------------------------------------------------------


class Harness:
    """Runs one hook from kiosk-update.sh with fakes for everything around it."""

    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.project = tmp_path / "openflight"
        self.calls = tmp_path / "cli-calls.log"
        self.relaunch = tmp_path / "relaunch.log"
        self.lock = tmp_path / "kiosk.lock"
        (self.project / "scripts").mkdir(parents=True)
        (self.project / ".venv" / "bin").mkdir(parents=True)
        self.cli = self.project / ".venv" / "bin" / "openflight-update"
        _write_executable(
            self.cli,
            "#!/usr/bin/env bash\n"
            f'printf \'%s\\n\' "$*" >> "{self.calls}"\n'
            'case "$3" in\n'
            '  apply) exit "${FAKE_APPLY_EXIT:-2}" ;;\n'
            '  confirm) exit "${FAKE_CONFIRM_EXIT:-2}" ;;\n'
            '  rollback) exit "${FAKE_ROLLBACK_EXIT:-2}" ;;\n'
            "esac\nexit 1\n",
        )
        _write_executable(
            self.project / "scripts" / "start-kiosk.sh",
            "#!/usr/bin/env bash\n"
            "{\n"
            "  printf 'argv=%s\\n' \"$*\"\n"
            "  printf 'applied=%s\\n' \"${OPENFLIGHT_UPDATE_APPLIED:-unset}\"\n"
            "  printf 'restart=%s\\n' \"${OPENFLIGHT_UPDATE_RESTART:-unset}\"\n"
            "  printf 'lock_open=%s\\n' \"$(ls -l /proc/self/fd 2>/dev/null | grep -c kiosk.lock)\"\n"
            f'}} > "{self.relaunch}"\n',
        )
        _write_executable(
            tmp_path / "harness.sh",
            "#!/usr/bin/env bash\n"
            "set -e\n"
            f'PROJECT_DIR="{self.project}"\n'
            "ORIGINAL_ARGS=(--mock --port 9090)\n"
            "log() { printf 'LOG %s\\n' \"$1\"; }\n"
            "warn() { printf 'WARN %s\\n' \"$1\"; }\n"
            f'exec {{INSTANCE_LOCK_FD}}>>"{self.lock}"\n'
            "release_instance_lock() { exec {INSTANCE_LOCK_FD}>&-; INSTANCE_LOCK_FD=''; echo LOCK_RELEASED; }\n"
            "stop_startup_splash_server() { echo STOP_SPLASH; }\n"
            "stop_kiosk_browser() { echo STOP_BROWSER; }\n"
            f'source "{SCRIPTS / "kiosk-update.sh"}"\n'
            '"$@"\n'
            "echo CONTINUED\n",
        )

    def run(self, hook: str, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self.tmp_path / "harness.sh"), hook, *args],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def cli_calls(self) -> list[str]:
        if not self.calls.exists():
            return []
        return self.calls.read_text(encoding="utf-8").splitlines()

    def relaunched(self) -> dict:
        if not self.relaunch.exists():
            return {}
        pairs = {}
        for line in self.relaunch.read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            pairs[key] = value
        return pairs


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


class TestApplyStagedUpdate:
    def test_applied_release_relaunches_with_the_original_arguments(self, harness):
        result = harness.run("apply_staged_update", FAKE_APPLY_EXIT="0")

        assert result.returncode == 0, result.stderr
        assert "CONTINUED" not in result.stdout
        assert "LOCK_RELEASED" in result.stdout
        assert harness.cli_calls() == [f"--install-link {harness.project} apply --if-staged"]
        relaunch = harness.relaunched()
        assert relaunch["argv"] == "--mock --port 9090"
        assert relaunch["applied"] == "1"
        assert relaunch["restart"] == "unset"
        assert relaunch["lock_open"] == "0"

    def test_nothing_staged_continues_quietly(self, harness):
        result = harness.run("apply_staged_update")

        assert result.returncode == 0, result.stderr
        assert "CONTINUED" in result.stdout
        assert "WARN" not in result.stdout
        assert harness.relaunched() == {}

    def test_apply_error_warns_and_starts_the_current_release(self, harness):
        result = harness.run("apply_staged_update", FAKE_APPLY_EXIT="1")

        assert result.returncode == 0, result.stderr
        assert "CONTINUED" in result.stdout
        assert "Could not apply the staged update (exit 1)" in result.stdout
        assert harness.relaunched() == {}

    def test_relaunched_instance_does_not_apply_again(self, harness):
        result = harness.run(
            "apply_staged_update", FAKE_APPLY_EXIT="0", OPENFLIGHT_UPDATE_APPLIED="1"
        )

        assert result.returncode == 0, result.stderr
        assert "CONTINUED" in result.stdout
        assert harness.cli_calls() == []

    def test_missing_cli_is_silent(self, harness):
        harness.cli.unlink()

        result = harness.run("apply_staged_update", FAKE_APPLY_EXIT="0")

        assert result.returncode == 0, result.stderr
        assert "CONTINUED" in result.stdout
        assert "WARN" not in result.stdout

    def test_install_link_override_is_forwarded(self, harness):
        harness.run("apply_staged_update", OPENFLIGHT_INSTALL_LINK="/srv/openflight")

        assert harness.cli_calls() == ["--install-link /srv/openflight apply --if-staged"]


class TestConfirmAndRollback:
    def test_confirm_logs_only_when_something_was_pending(self, harness):
        quiet = harness.run("confirm_applied_update")
        confirmed = harness.run("confirm_applied_update", FAKE_CONFIRM_EXIT="0")
        failed = harness.run("confirm_applied_update", FAKE_CONFIRM_EXIT="1")

        assert "Update confirmed" not in quiet.stdout and "WARN" not in quiet.stdout
        assert "Update confirmed" in confirmed.stdout
        assert "Could not confirm the applied update (exit 1)" in failed.stdout
        assert all("CONTINUED" in r.stdout for r in (quiet, confirmed, failed))

    def test_rollback_passes_the_failure_reason(self, harness):
        result = harness.run("rollback_pending_update", "server timed out", FAKE_ROLLBACK_EXIT="0")

        assert result.returncode == 0, result.stderr
        assert "Rolled the update back" in result.stdout
        assert harness.cli_calls() == [
            f"--install-link {harness.project} rollback --reason server timed out"
        ]

    def test_rollback_without_a_pending_release_is_quiet(self, harness):
        result = harness.run("rollback_pending_update", "x")

        assert "WARN" not in result.stdout
        assert "CONTINUED" in result.stdout

    def test_missing_cli_never_blocks_startup_or_failure_handling(self, harness):
        harness.cli.unlink()

        for hook in ("confirm_applied_update", "rollback_pending_update"):
            result = harness.run(hook, "reason")
            assert result.returncode == 0, result.stderr
            assert "CONTINUED" in result.stdout
            assert "WARN" not in result.stdout


class TestRestartIntoStagedUpdate:
    def test_stops_the_kiosk_drops_the_lock_and_relaunches_for_apply(self, harness):
        result = harness.run("restart_into_staged_update", OPENFLIGHT_UPDATE_APPLIED="1")

        assert result.returncode == 0, result.stderr
        assert "CONTINUED" not in result.stdout
        stdout = result.stdout
        assert (
            stdout.index("STOP_SPLASH")
            < stdout.index("STOP_BROWSER")
            < stdout.index("LOCK_RELEASED")
        )
        relaunch = harness.relaunched()
        assert relaunch["argv"] == "--mock --port 9090"
        assert relaunch["applied"] == "unset"
        assert relaunch["restart"] == "1"
        assert relaunch["lock_open"] == "0"
