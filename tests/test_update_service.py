"""Contracts for the updater's systemd units and their installation by setup.sh."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE = REPO_ROOT / "scripts/setup/openflight-update.service"
TIMER = REPO_ROOT / "scripts/setup/openflight-update.timer"
SETUP = REPO_ROOT / "scripts/setup/setup.sh"


def _section(path: Path, name: str) -> list[str]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    start = lines.index(f"[{name}]") + 1
    body = []
    for line in lines[start:]:
        if line.startswith("["):
            break
        if line and not line.startswith("#"):
            body.append(line)
    return body


def test_service_runs_the_check_through_the_install_link():
    service = _section(SERVICE, "Service")

    assert "Type=oneshot" in service
    assert "ExecStart=/home/coleman/openflight/.venv/bin/openflight-update check" in service
    assert "WorkingDirectory=/home/coleman/openflight" in service
    assert "Nice=10" in service
    assert "After=network-online.target" in _section(SERVICE, "Unit")


def test_timer_is_gentle_and_survives_downtime():
    timer = _section(TIMER, "Timer")

    assert "OnBootSec=5min" in timer
    assert "OnUnitActiveSec=6h" in timer
    assert "RandomizedDelaySec=30min" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in _section(TIMER, "Install")


def test_every_home_path_is_rewritten_by_setup():
    setup = SETUP.read_text(encoding="utf-8")
    assert "s|/home/coleman/openflight|$PROJECT_DIR|g" in setup
    for path in (SERVICE, TIMER):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "/home/coleman" in line:
                assert "/home/coleman/openflight" in line, line


def test_setup_offers_updates_after_migrating_and_choosing_a_channel():
    setup = SETUP.read_text(encoding="utf-8")
    block = setup[setup.index("Automatic updates (opt-in)") :]

    assert 'openflight-update migrate --install-link "$PROJECT_DIR"' in block
    assert 'openflight-update set-channel "$channel"' in block
    assert "openflight-update.service openflight-update.timer" in block
    assert "sudo systemctl enable --now openflight-update.timer" in block
    assert block.index("migrate") < block.index("systemctl enable --now openflight-update.timer")
