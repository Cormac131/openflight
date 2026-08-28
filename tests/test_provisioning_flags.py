"""Tests for turning a detected profile into start-kiosk.sh flags.

The decisions worth pinning down are the refusals: a K-LD7 without a measured
mount tilt, a K-LD7 shadowed by an IWR6843, and a camera that is present but
must stay off. Each one is a case where "enable what we found" would produce
silently wrong numbers or a server that will not start.
"""

from openflight.provisioning.detect import DetectedDevice, DeviceKind, HardwareProfile
from openflight.provisioning.flags import (
    FlagPlan,
    SiteConfig,
    profile_to_flags,
    render_env_file,
)


def profile(**kinds):
    """Build a profile from ``kind=address`` pairs; absent kinds are omitted."""
    devices = []
    for name, address in kinds.items():
        kind = DeviceKind[name.upper()]
        devices.append(DetectedDevice(kind=kind, present=address is not None, address=address))
    return HardwareProfile(devices=tuple(devices))


def flag_value(flags, name):
    """Return the argument following ``name``, or None when absent."""
    flags = list(flags)
    if name not in flags:
        return None
    index = flags.index(name)
    return flags[index + 1] if index + 1 < len(flags) else None


class TestOps243:
    def test_pins_the_detected_port(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0"))
        assert flag_value(plan.flags, "--radar-port") == "/dev/ttyACM0"
        assert not plan.warnings

    def test_pins_a_uart_port(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyAMA0"))
        assert flag_value(plan.flags, "--radar-port") == "/dev/ttyAMA0"

    def test_warns_when_missing(self):
        plan = profile_to_flags(profile(ops243=None))
        assert "--radar-port" not in plan.flags
        assert any("No OPS243-A radar found" in w for w in plan.warnings)


class TestIwr6843:
    def test_enables_with_explicit_port(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0", iwr6843="/dev/ttyUSB0"))
        assert "--iwr6843" in plan.flags
        assert flag_value(plan.flags, "--iwr6843-port") == "/dev/ttyUSB0"

    def test_omitted_when_absent(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0", iwr6843=None))
        assert "--iwr6843" not in plan.flags

    def test_applies_site_geometry(self):
        plan = profile_to_flags(
            profile(ops243="/dev/ttyACM0", iwr6843="/dev/ttyUSB0"),
            SiteConfig(iwr6843_tee_m="1.4", iwr6843_net_m="3.2"),
        )
        assert flag_value(plan.flags, "--iwr6843-tee-m") == "1.4"
        assert flag_value(plan.flags, "--iwr6843-net-m") == "3.2"

    def test_leaves_geometry_to_server_defaults_when_unset(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0", iwr6843="/dev/ttyUSB0"))
        assert "--iwr6843-tee-m" not in plan.flags
        assert "--iwr6843-net-m" not in plan.flags


class TestKld7:
    def test_enabled_when_mount_tilt_is_known(self):
        plan = profile_to_flags(
            profile(ops243="/dev/ttyACM0", kld7_vertical="/dev/kld7_vertical"),
            SiteConfig(kld7_mount_tilt_deg="12.5"),
        )
        assert "--kld7" in plan.flags
        assert flag_value(plan.flags, "--kld7-port") == "/dev/kld7_vertical"
        assert flag_value(plan.flags, "--kld7-mount-tilt") == "12.5"
        assert not plan.warnings

    def test_disabled_without_mount_tilt(self):
        """A guessed tilt silently biases every launch angle, so refuse instead."""
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0", kld7_vertical="/dev/kld7_vertical"))
        assert "--kld7" not in plan.flags
        assert any("KLD7_MOUNT_TILT" in w for w in plan.warnings)

    def test_horizontal_added_when_detected(self):
        plan = profile_to_flags(
            profile(
                ops243="/dev/ttyACM0",
                kld7_vertical="/dev/kld7_vertical",
                kld7_horizontal="/dev/kld7_horizontal",
            ),
            SiteConfig(kld7_mount_tilt_deg="12.5"),
        )
        assert "--kld7-horizontal" in plan.flags
        assert flag_value(plan.flags, "--kld7-horizontal-port") == "/dev/kld7_horizontal"

    def test_horizontal_omitted_when_only_one_radar(self):
        plan = profile_to_flags(
            profile(ops243="/dev/ttyACM0", kld7_vertical="/dev/kld7_vertical"),
            SiteConfig(kld7_mount_tilt_deg="12.5"),
        )
        assert "--kld7-horizontal" not in plan.flags

    def test_optional_site_values_are_passed_through(self):
        plan = profile_to_flags(
            profile(ops243="/dev/ttyACM0", kld7_vertical="/dev/kld7_vertical"),
            SiteConfig(
                kld7_mount_tilt_deg="12.5",
                kld7_angle_offset_deg="1.5",
                net_distance_m="3.0",
            ),
        )
        assert flag_value(plan.flags, "--kld7-angle-offset") == "1.5"
        assert flag_value(plan.flags, "--net-distance") == "3.0"

    def test_iwr6843_supersedes_kld7(self):
        plan = profile_to_flags(
            profile(
                ops243="/dev/ttyACM0",
                iwr6843="/dev/ttyUSB0",
                kld7_vertical="/dev/kld7_vertical",
            ),
            SiteConfig(kld7_mount_tilt_deg="12.5"),
        )
        assert "--iwr6843" in plan.flags
        assert "--kld7" not in plan.flags
        assert any("supersedes" in w for w in plan.warnings)


class TestPeripherals:
    def test_inclinometer_enabled_when_present(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0", inclinometer="0x18"))
        assert "--inclinometer" in plan.flags

    def test_battery_provider_selected_when_present(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0", battery="0x36"))
        assert flag_value(plan.flags, "--battery") == "geekworm"

    def test_camera_is_reported_but_never_enabled(self):
        """The camera path is not in the production build; enabling it would break startup."""
        detected = HardwareProfile(
            devices=(
                DetectedDevice(DeviceKind.OPS243, True, "/dev/ttyACM0"),
                DetectedDevice(DeviceKind.CAMERA, True, None, "0 : imx296"),
            )
        )
        plan = profile_to_flags(detected)
        assert "--camera-capture" not in plan.flags
        assert any("imx296" in note for note in plan.notes)


class TestSiteConfig:
    def test_from_env_reads_every_key(self):
        site = SiteConfig.from_env(
            {
                "KLD7_MOUNT_TILT": "12.5",
                "KLD7_ANGLE_OFFSET": "1.5",
                "NET_DISTANCE": "3.0",
                "SESSION_LOCATION": "garage",
                "IWR6843_TEE_M": "1.4",
                "IWR6843_NET_M": "3.2",
                "OPENFLIGHT_ENABLE_SIM": "true",
                "OPS243_UART": "true",
            }
        )
        assert site.kld7_mount_tilt_deg == "12.5"
        assert site.session_location == "garage"
        assert site.iwr6843_net_m == "3.2"
        assert site.enable_sim is True
        assert site.ops243_uart is True

    def test_uart_opt_in_defaults_off(self):
        assert SiteConfig.from_env({}).ops243_uart is False

    def test_from_env_reads_a_boot_config_file(self, tmp_path, monkeypatch):
        conf = tmp_path / "openflight.conf"
        conf.write_text("OPS243_UART=true\nSESSION_LOCATION=bay\n", encoding="utf-8")
        monkeypatch.setenv("OPENFLIGHT_BOOT_CONFIG", str(conf))
        monkeypatch.delenv("OPS243_UART", raising=False)
        monkeypatch.delenv("SESSION_LOCATION", raising=False)
        site = SiteConfig.from_env()
        assert site.ops243_uart is True
        assert site.session_location == "bay"

    def test_blank_values_are_treated_as_unset(self):
        """An owner who deletes a value but leaves the key must not set an empty flag."""
        site = SiteConfig.from_env({"KLD7_MOUNT_TILT": "   ", "SESSION_LOCATION": ""})
        assert site.kld7_mount_tilt_deg is None
        assert site.session_location is None

    def test_sim_defaults_off(self):
        assert SiteConfig.from_env({}).enable_sim is False

    def test_sim_accepts_common_truthy_spellings(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            assert SiteConfig.from_env({"OPENFLIGHT_ENABLE_SIM": value}).enable_sim is True

    def test_sim_rejects_other_values(self):
        for value in ("0", "false", "no", "maybe"):
            assert SiteConfig.from_env({"OPENFLIGHT_ENABLE_SIM": value}).enable_sim is False

    def test_session_location_becomes_a_flag(self):
        plan = profile_to_flags(
            profile(ops243="/dev/ttyACM0"), SiteConfig(session_location="garage")
        )
        assert flag_value(plan.flags, "--session-location") == "garage"

    def test_sim_becomes_a_flag(self):
        plan = profile_to_flags(profile(ops243="/dev/ttyACM0"), SiteConfig(enable_sim=True))
        assert "--sim" in plan.flags


class TestCommandLine:
    def test_quotes_values_containing_spaces(self):
        plan = profile_to_flags(
            profile(ops243="/dev/ttyACM0"), SiteConfig(session_location="back garden")
        )
        assert "'back garden'" in plan.command_line

    def test_empty_plan_renders_as_empty_string(self):
        assert FlagPlan().command_line == ""


class TestRenderEnvFile:
    def test_records_devices_and_flags(self):
        detected = profile(ops243="/dev/ttyACM0", iwr6843="/dev/ttyUSB0")
        plan = profile_to_flags(detected)
        rendered = render_env_file(detected, plan)
        assert "OPENFLIGHT_DETECTED_AT=" in rendered
        assert "OPENFLIGHT_DETECTED_DEVICES=ops243,iwr6843" in rendered
        assert "--iwr6843" in rendered
        assert rendered.endswith("\n")

    def test_warnings_are_commented_out(self):
        """The file is sourced by the shell — a warning must not become a command."""
        detected = profile(ops243=None)
        plan = profile_to_flags(detected)
        rendered = render_env_file(detected, plan)
        warning_lines = [
            line for line in rendered.splitlines() if "No OPS243-A radar found" in line
        ]
        assert warning_lines
        assert all(line.startswith("#") for line in warning_lines)

    def test_every_non_comment_line_is_an_assignment(self):
        detected = profile(ops243="/dev/ttyACM0", inclinometer="0x18")
        rendered = render_env_file(detected, profile_to_flags(detected))
        for line in rendered.splitlines():
            if line and not line.startswith("#"):
                assert "=" in line
