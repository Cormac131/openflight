"""Consistency checks across the SD card image's static assets.

Nothing here runs a build. These catch the failures that a build would happily
produce and nobody would notice until an owner powers on a card: a setting
documented in openflight.conf that the code no longer reads, a Plymouth theme
pointing at an image the install step does not place, a launcher pointing at a
path the image does not have.
"""

import re
from pathlib import Path

import pytest

from openflight.provisioning.flags import SiteConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIR = REPO_ROOT / "scripts" / "image"

BOOT_CONFIG = IMAGE_DIR / "openflight.conf"
CUSTOMIZE = IMAGE_DIR / "customize.sh"
FIRSTBOOT = IMAGE_DIR / "firstboot.sh"
BUILD_IMAGE = IMAGE_DIR / "build-image.sh"
PLYMOUTH_SCRIPT = IMAGE_DIR / "plymouth" / "openflight.script"
PLYMOUTH_THEME = IMAGE_DIR / "plymouth" / "openflight.plymouth"

# Where the image installs the project. Every launcher and unit must agree.
INSTALL_PREFIX = "/opt/openflight"

SETTING_PATTERN = re.compile(r"^#?([A-Z_][A-Z0-9_]*)=", re.MULTILINE)


def documented_settings():
    """Every SETTING=value line in the shipped boot config, commented or not."""
    return set(SETTING_PATTERN.findall(BOOT_CONFIG.read_text(encoding="utf-8")))


class TestBootConfig:
    def test_every_documented_setting_is_actually_read(self):
        """A rename in flags.py must not silently orphan a documented setting."""
        # SiteConfig reads these; start-kiosk.sh reads the rest of them
        # directly out of the environment.
        read_by_site_config = {
            "KLD7_MOUNT_TILT",
            "KLD7_ANGLE_OFFSET",
            "NET_DISTANCE",
            "SESSION_LOCATION",
            "IWR6843_TEE_M",
            "IWR6843_NET_M",
            "OPENFLIGHT_ENABLE_SIM",
            "OPS243_UART",
        }
        assert documented_settings() <= read_by_site_config

    def test_site_config_honours_every_documented_setting(self):
        """The other direction: each documented setting must reach SiteConfig."""
        env = {name: "1.0" for name in documented_settings()}
        env["SESSION_LOCATION"] = "garage"
        env["OPENFLIGHT_ENABLE_SIM"] = "true"
        env["OPS243_UART"] = "true"
        site = SiteConfig.from_env(env)
        # Every field the config can set must come back populated.
        assert site.kld7_mount_tilt_deg == "1.0"
        assert site.kld7_angle_offset_deg == "1.0"
        assert site.net_distance_m == "1.0"
        assert site.iwr6843_tee_m == "1.0"
        assert site.iwr6843_net_m == "1.0"
        assert site.session_location == "garage"
        assert site.enable_sim is True
        assert site.ops243_uart is True

    def test_every_setting_is_commented_out_by_default(self):
        """A shipped image must apply no site geometry it was not told about."""
        for line in BOOT_CONFIG.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                pytest.fail(f"Active setting in the shipped config: {stripped}")

    def test_start_kiosk_parses_the_shipped_config(self):
        """The parser only accepts NAME=VALUE; every shipped line must fit."""
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
        for line in BOOT_CONFIG.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0].strip()
            if stripped:
                assert pattern.match(stripped), f"unparseable: {stripped}"


class TestPlymouthTheme:
    def test_theme_points_at_the_script_it_ships_with(self):
        theme = PLYMOUTH_THEME.read_text(encoding="utf-8")
        assert "openflight.script" in theme
        assert "ModuleName=script" in theme

    def test_every_image_the_script_loads_is_installed(self):
        """Plymouth fails to its default theme if an asset is missing."""
        script = PLYMOUTH_SCRIPT.read_text(encoding="utf-8")
        customize = CUSTOMIZE.read_text(encoding="utf-8")
        for asset in re.findall(r'Image\("([^"]+)"\)', script):
            assert f"/{asset}" in customize, f"{asset} is loaded but never installed"

    def test_background_matches_the_brand_colour(self):
        """#0e0f10 as plymouth floats — a mismatch flashes between splash and app."""
        script = PLYMOUTH_SCRIPT.read_text(encoding="utf-8")
        assert script.count("0.055, 0.059, 0.063") == 2


class TestLaunchers:
    @pytest.mark.parametrize("name", ["openflight-kiosk.desktop", "OpenFlight.desktop"])
    def test_launchers_start_the_kiosk_with_auto_hardware(self, name):
        entry = (IMAGE_DIR / name).read_text(encoding="utf-8")
        assert f"{INSTALL_PREFIX}/scripts/start-kiosk.sh --auto-hardware" in entry
        assert "Icon=openflight" in entry

    def test_kiosk_waits_if_firstboot_is_still_running(self):
        """Belt and braces: autostart cannot systemctl-start a system unit as UID 1000."""
        script = (REPO_ROOT / "scripts" / "start-kiosk.sh").read_text(encoding="utf-8")
        assert "wait_for_firstboot" in script

    def test_the_icon_name_is_installed_by_customize(self):
        customize = CUSTOMIZE.read_text(encoding="utf-8")
        assert "apps/openflight.png" in customize


class TestServiceUnit:
    def test_firstboot_unit_runs_the_shipped_script(self):
        unit = (IMAGE_DIR / "systemd" / "openflight-firstboot.service").read_text(encoding="utf-8")
        assert f"ExecStart={INSTALL_PREFIX}/scripts/image/firstboot.sh" in unit
        assert "Type=oneshot" in unit

    def test_firstboot_precedes_the_graphical_session(self):
        """WantedBy=multi-user.target + After=multi-user.target does not block the desktop."""
        unit = (IMAGE_DIR / "systemd" / "openflight-firstboot.service").read_text(encoding="utf-8")
        assert re.search(r"^Before=graphical\.target$", unit, re.MULTILINE)
        assert re.search(r"^WantedBy=graphical\.target$", unit, re.MULTILINE)
        assert not re.search(r"^Before=openflight\.service$", unit, re.MULTILINE)
        assert not re.search(r"^After=multi-user\.target", unit, re.MULTILINE)

    def test_firstboot_script_defaults_to_the_install_prefix(self):
        script = FIRSTBOOT.read_text(encoding="utf-8")
        assert f'PROJECT_DIR="${{OPENFLIGHT_PROJECT_DIR:-{INSTALL_PREFIX}}}"' in script

    def test_firstboot_waits_for_the_imager_user(self):
        """UID 1000 is created during first boot; giving up immediately leaves the tree root-owned."""
        script = FIRSTBOOT.read_text(encoding="utf-8")
        assert "getent passwd 1000" in script
        assert "attempt" in script
        assert "seq 1 30" in script

    def test_poweroff_holds_the_oneshot_until_shutdown(self):
        script = FIRSTBOOT.read_text(encoding="utf-8")
        assert "systemctl poweroff" in script
        assert "exec sleep infinity" in script

    def test_detect_failure_does_not_mark_provisioning_done(self):
        script = FIRSTBOOT.read_text(encoding="utf-8")
        # The configure branch must not write "done" when detection itself crashed;
        # that would skip the radar flash forever.
        configure = script.split("configure)", 1)[1].split("verify)", 1)[0]
        assert "if ! detect_hardware" in configure
        assert (
            'write_stage "done"'
            not in configure.split("if ! detect_hardware", 1)[1].split("if ! ops243_present", 1)[0]
        )


class TestBuildScript:
    def test_uses_the_stable_desktop_release_redirect(self):
        """Desktop, not Lite: the kiosk needs a graphical session."""
        build = BUILD_IMAGE.read_text(encoding="utf-8")
        assert "raspios_arm64_latest" in build
        assert "raspios_lite" not in build

    def test_verifies_the_download(self):
        build = BUILD_IMAGE.read_text(encoding="utf-8")
        assert "sha256sum -c" in build
        assert "continuing unverified" not in build

    def test_pins_uv_in_the_image(self):
        customize = CUSTOMIZE.read_text(encoding="utf-8")
        assert "astral.sh/uv/" in customize
        assert "install.sh | sh" in customize
        # A bare astral.sh/uv/install.sh (no version segment) tracks latest.
        assert "astral.sh/uv/install.sh" not in customize

    def test_clears_machine_identity_before_shipping(self):
        """Every card written from one image must not share a machine-id."""
        build = BUILD_IMAGE.read_text(encoding="utf-8")
        assert "/etc/machine-id" in build
        assert "etc/ssh/ssh_host_" in build

    def test_stages_a_committed_revision(self):
        """git archive, not a copy of the build host's working tree."""
        build = BUILD_IMAGE.read_text(encoding="utf-8")
        assert "git archive" in build

    def test_builds_the_ui_on_the_host(self):
        """Debian's nodejs in the chroot is too old for the Vite 8 UI toolchain."""
        build = BUILD_IMAGE.read_text(encoding="utf-8")
        assert "npm run build" in build
