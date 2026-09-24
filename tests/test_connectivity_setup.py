"""Least-privilege contract for the kiosk Wi-Fi/Bluetooth system permissions."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP = REPO_ROOT / "scripts/setup/setup_connectivity.sh"
POLKIT = REPO_ROOT / "scripts/setup/connectivity/50-openflight-network.rules"
UDEV = REPO_ROOT / "scripts/setup/connectivity/70-openflight-rfkill.rules"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

EXPECTED_ACTIONS = {
    "org.freedesktop.NetworkManager.network-control",
    "org.freedesktop.NetworkManager.wifi.scan",
    "org.freedesktop.NetworkManager.enable-disable-wifi",
    "org.freedesktop.NetworkManager.settings.modify.system",
}


def test_polkit_rule_grants_only_the_needed_actions_to_one_group():
    text = POLKIT.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    actions = set(re.findall(r'"(org\.freedesktop\.[^"]+)"', code))
    assert actions == EXPECTED_ACTIONS
    assert 'subject.isInGroup("openflight-network")' in code
    assert code.count("polkit.Result.YES") == 1
    # No wildcard grants, no root/sudo shortcuts.
    assert 'indexOf("org.freedesktop.NetworkManager.")' not in code
    assert "startsWith" not in code and "AUTH_ADMIN" not in code


def test_udev_rule_only_adds_group_acl_to_rfkill():
    rules = [line for line in UDEV.read_text().splitlines() if line and not line.startswith("#")]
    assert rules == [
        'KERNEL=="rfkill", SUBSYSTEM=="misc", '
        'RUN+="/usr/bin/setfacl -m g:openflight-network:rw /dev/rfkill"'
    ]


def test_setup_script_never_grants_sudo_or_runs_the_app_as_root():
    text = SETUP.read_text(encoding="utf-8")
    assert "sudoers" not in text
    assert "NOPASSWD" not in text
    assert "setcap" not in text
    assert "chmod 666" not in text and "chmod 777" not in text


def run_setup(tmp_path, *args):
    return subprocess.run(
        ["bash", str(SETUP), *args],
        env={**os.environ, "OPENFLIGHT_SETUP_ROOT": str(tmp_path), "SUDO_USER": "pi"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_installs_and_uninstalls_rules_into_root(tmp_path):
    result = run_setup(tmp_path)
    assert result.returncode == 0, result.stderr
    polkit = tmp_path / "etc/polkit-1/rules.d/50-openflight-network.rules"
    udev = tmp_path / "etc/udev/rules.d/70-openflight-rfkill.rules"
    assert polkit.read_text() == POLKIT.read_text()
    assert udev.read_text() == UDEV.read_text()
    assert oct(polkit.stat().st_mode & 0o777) == "0o644"
    assert run_setup(tmp_path, "--uninstall").returncode == 0
    assert not polkit.exists() and not udev.exists()


def test_requires_a_non_root_target_user(tmp_path):
    result = subprocess.run(
        ["bash", str(SETUP), "--user", "root"],
        env={**os.environ, "OPENFLIGHT_SETUP_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


def test_main_setup_offers_connectivity_permissions():
    text = (REPO_ROOT / "scripts/setup/setup.sh").read_text(encoding="utf-8")
    assert 'sudo "$SCRIPT_DIR/setup_connectivity.sh" --user "$USER"' in text


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_polkit_rule_decisions_when_executed():
    harness = r"""
const fs = require('fs');
let rule;
const polkit = { Result: { YES: 'yes', NOT_HANDLED: 'nh' }, addRule: (f) => { rule = f; } };
new Function('polkit', fs.readFileSync(process.argv[1], 'utf8'))(polkit);
const member = { isInGroup: (g) => g === 'openflight-network' };
const outsider = { isInGroup: () => false };
const out = {};
for (const id of JSON.parse(process.argv[2])) {
  out[id] = [rule({ id }, member), rule({ id }, outsider)];
}
console.log(JSON.stringify(out));
"""
    import json

    probe = sorted(EXPECTED_ACTIONS) + [
        "org.freedesktop.NetworkManager.settings.modify.hostname",
        "org.freedesktop.NetworkManager.enable-disable-network",
        "org.freedesktop.NetworkManager.wifi.share.open",
        "org.freedesktop.login1.reboot",
    ]
    result = subprocess.run(
        ["node", "-e", harness, str(POLKIT), json.dumps(probe)],
        capture_output=True,
        text=True,
        check=True,
    )
    decisions = json.loads(result.stdout)
    for action in probe:
        expected_member = "yes" if action in EXPECTED_ACTIONS else "nh"
        assert decisions[action] == [expected_member, "nh"], action
