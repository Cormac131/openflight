"""Tests for the openflight-detect-hardware CLI.

The contract that matters is the stdout/stderr split: start-kiosk.sh captures
stdout inside ``$(...)`` and splices it straight into the server command, so
anything conversational leaking onto stdout would be parsed as a flag.
"""

import json

import pytest

from openflight.provisioning import cli
from openflight.provisioning.detect import DetectedDevice, DeviceKind, HardwareProfile
from openflight.provisioning.flags import profile_to_flags


def fake_profile(*, ops243=True, iwr6843=False, kld7=False):
    devices = [
        DetectedDevice(DeviceKind.OPS243, ops243, "/dev/ttyACM0" if ops243 else None),
        DetectedDevice(DeviceKind.IWR6843, iwr6843, "/dev/ttyUSB0" if iwr6843 else None),
        DetectedDevice(DeviceKind.KLD7_VERTICAL, kld7, "/dev/kld7_vertical" if kld7 else None),
    ]
    return HardwareProfile(devices=tuple(devices))


# SiteConfig reads these from the real environment, so a developer who has
# any of them exported would otherwise see flags these tests do not expect.
SITE_ENV_KEYS = (
    "KLD7_MOUNT_TILT",
    "KLD7_ANGLE_OFFSET",
    "NET_DISTANCE",
    "SESSION_LOCATION",
    "IWR6843_TEE_M",
    "IWR6843_NET_M",
    "OPENFLIGHT_ENABLE_SIM",
)


@pytest.fixture(name="detected")
def _detected(monkeypatch):
    """Install a fake detection sweep, and let each test choose the profile."""
    for key in SITE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    holder = {"profile": fake_profile()}

    def _detect_hardware(**_kwargs):
        return holder["profile"]

    monkeypatch.setattr(cli, "detect_hardware", _detect_hardware)
    return holder


class TestFlagsOutput:
    def test_prints_one_flag_per_line_on_stdout(self, detected, capsys):
        detected["profile"] = fake_profile(iwr6843=True)
        assert cli.main([]) == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "--radar-port",
            "/dev/ttyACM0",
            "--iwr6843",
            "--iwr6843-port",
            "/dev/ttyUSB0",
        ]
        assert captured.err == ""

    def test_line_per_flag_keeps_spaces_intact(self, detected, capsys, monkeypatch):
        """A value with a space must arrive as one line, not two arguments."""
        monkeypatch.setenv("SESSION_LOCATION", "back garden")
        assert cli.main([]) == 0
        assert "back garden" in capsys.readouterr().out.splitlines()

    def test_flags_line_is_shell_quoted(self, detected, capsys, monkeypatch):
        monkeypatch.setenv("SESSION_LOCATION", "back garden")
        assert cli.main(["--flags-line"]) == 0
        assert "'back garden'" in capsys.readouterr().out

    def test_warnings_go_to_stderr(self, detected, capsys):
        detected["profile"] = fake_profile(ops243=False)
        cli.main([])
        captured = capsys.readouterr()
        assert "No OPS243-A radar found" in captured.err
        assert "No OPS243-A radar found" not in captured.out

    def test_empty_flags_prints_nothing(self, detected, capsys):
        detected["profile"] = HardwareProfile()
        assert cli.main([]) == 0
        assert capsys.readouterr().out == ""


class TestJsonOutput:
    def test_emits_a_parseable_document(self, detected, capsys):
        detected["profile"] = fake_profile(iwr6843=True)
        assert cli.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["flags"] == [
            "--radar-port",
            "/dev/ttyACM0",
            "--iwr6843",
            "--iwr6843-port",
            "/dev/ttyUSB0",
        ]
        kinds = {d["kind"]: d["present"] for d in payload["profile"]["devices"]}
        assert kinds == {"ops243": True, "iwr6843": True, "kld7_vertical": False}

    def test_includes_warnings(self, detected, capsys):
        detected["profile"] = fake_profile(ops243=False)
        cli.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["warnings"]


class TestReportOutput:
    def test_lists_found_and_absent_devices(self, detected, capsys):
        detected["profile"] = fake_profile(iwr6843=True)
        assert cli.main(["--report"]) == 0
        out = capsys.readouterr().out
        assert "[found]   OPS243-A Doppler radar" in out
        assert "[found]   IWR6843 60 GHz radar" in out
        assert "[absent]  K-LD7 vertical (deprecated)" in out

    def test_says_so_when_defaults_suffice(self, detected, capsys):
        detected["profile"] = HardwareProfile()
        cli.main(["--report"])
        assert "defaults are correct" in capsys.readouterr().out

    def test_shows_warnings(self, detected, capsys):
        detected["profile"] = fake_profile(ops243=False)
        cli.main(["--report"])
        assert "Warnings:" in capsys.readouterr().out


class TestRequireOps243:
    def test_exits_non_zero_without_a_radar(self, detected, capsys):
        detected["profile"] = fake_profile(ops243=False)
        assert cli.main(["--require-ops243"]) == 1
        capsys.readouterr()

    def test_exits_zero_with_a_radar(self, detected, capsys):
        assert cli.main(["--require-ops243"]) == 0
        capsys.readouterr()

    def test_missing_radar_alone_is_not_an_error(self, detected, capsys):
        """Without --require-ops243 the server still starts and shows the fault in the UI."""
        detected["profile"] = fake_profile(ops243=False)
        assert cli.main([]) == 0
        capsys.readouterr()


class TestWrite:
    def test_writes_a_sourceable_env_file(self, detected, tmp_path, capsys):
        target = tmp_path / "etc" / "openflight" / "hardware.env"
        detected["profile"] = fake_profile(iwr6843=True)
        assert cli.main(["--write", str(target)]) == 0
        capsys.readouterr()
        content = target.read_text(encoding="utf-8")
        assert "OPENFLIGHT_AUTO_FLAGS=" in content
        assert "OPENFLIGHT_DETECTED_DEVICES=ops243,iwr6843" in content

    def test_creates_missing_parent_directories(self, detected, tmp_path, capsys):
        target = tmp_path / "a" / "b" / "c" / "hardware.env"
        cli.main(["--write", str(target)])
        capsys.readouterr()
        assert target.exists()

    def test_reports_an_unwritable_path(self, detected, tmp_path, capsys):
        blocker = tmp_path / "hardware.env"
        blocker.write_text("", encoding="utf-8")
        assert cli.main(["--write", str(blocker / "nested.env")]) == 2
        assert "could not write" in capsys.readouterr().err

    def test_write_still_prints_flags(self, detected, tmp_path, capsys):
        target = tmp_path / "hardware.env"
        cli.main(["--write", str(target)])
        assert "--radar-port" in capsys.readouterr().out.splitlines()


class TestFormatReport:
    def test_renders_notes_section(self):
        detected = fake_profile(iwr6843=True)
        report = cli.format_report(detected, profile_to_flags(detected))
        assert "Configuration:" in report
        assert "IWR6843 on /dev/ttyUSB0" in report


class TestArgParsing:
    def test_output_modes_are_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            cli.parse_args(["--json", "--report"])
        with pytest.raises(SystemExit):
            cli.parse_args(["--flags", "--flags-line"])

    def test_defaults(self):
        args = cli.parse_args([])
        assert args.write is None
        assert args.probe_iwr6843 is False
        assert args.no_camera is False
        assert args.require_ops243 is False
