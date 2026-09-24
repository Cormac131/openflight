#!/usr/bin/env bash
#
# Grant the OpenFlight user the narrow system permissions the kiosk
# Connections panel needs (Wi-Fi via NetworkManager, Bluetooth via BlueZ).
#
# OpenFlight itself never runs as root. This script (run once with sudo):
#   - installs NetworkManager, BlueZ, polkit and acl if missing
#   - enables the bluetooth service
#   - creates the `openflight-network` group and adds the user to it and to
#     `bluetooth` (BlueZ's D-Bus policy already allows that group)
#   - installs a polkit rule granting that group only the NetworkManager
#     actions the panel uses (see connectivity/50-openflight-network.rules)
#   - installs a udev rule letting the group soft-unblock rfkill
#
# Usage:
#   sudo ./scripts/setup/setup_connectivity.sh [--user NAME]
#   sudo ./scripts/setup/setup_connectivity.sh --uninstall
#
# Log out and back in (or reboot) afterwards so the new groups apply.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES_DIR="$SCRIPT_DIR/connectivity"
GROUP="openflight-network"
POLKIT_RULE="/etc/polkit-1/rules.d/50-openflight-network.rules"
UDEV_RULE="/etc/udev/rules.d/70-openflight-rfkill.rules"
TARGET_USER="${SUDO_USER:-}"
UNINSTALL=false
# Tests point these at a temporary root.
ROOT="${OPENFLIGHT_SETUP_ROOT:-}"

log() { printf '[OpenFlight] %s\n' "$1"; }
warn() { printf '[OpenFlight] WARNING: %s\n' "$1" >&2; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            TARGET_USER="${2:?--user requires a name}"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --help|-h)
            awk 'NR>1 && !/^#/{exit} NR>1{sub(/^# ?/,""); print}' "$0"
            exit 0
            ;;
        *)
            warn "Unknown option: $1"
            exit 2
            ;;
    esac
done

if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
    warn "Run with sudo: sudo $0"
    exit 1
fi

if [ "$UNINSTALL" = true ]; then
    rm -f "$ROOT$POLKIT_RULE" "$ROOT$UDEV_RULE"
    log "Removed OpenFlight polkit and udev rules (group $GROUP left in place)."
    exit 0
fi

if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = root ]; then
    warn "Pass the account that runs OpenFlight: sudo $0 --user <name>"
    exit 1
fi

install_packages() {
    command -v apt-get >/dev/null 2>&1 || {
        warn "apt-get not found; install NetworkManager, BlueZ, polkit and acl manually"
        return 0
    }
    local missing=()
    local package
    for package in network-manager bluez acl; do
        dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
    done
    # Debian 12+ ships polkitd; older releases policykit-1.
    if ! dpkg -s polkitd >/dev/null 2>&1 && ! dpkg -s policykit-1 >/dev/null 2>&1; then
        missing+=(polkitd)
    fi
    if [ "${#missing[@]}" -gt 0 ]; then
        log "Installing ${missing[*]}"
        apt-get install -y "${missing[@]}"
    fi
}

if [ -z "$ROOT" ]; then
    install_packages
    if ! systemctl is-active --quiet NetworkManager; then
        warn "NetworkManager is not running. Raspberry Pi OS Bookworm and later use it by"
        warn "default; on older images switch with: sudo raspi-config (Advanced > Network Config)."
        warn "Wi-Fi controls stay hidden until NetworkManager manages the Wi-Fi interface."
    fi
    systemctl enable --now bluetooth >/dev/null 2>&1 || warn "Could not enable the bluetooth service"
    getent group "$GROUP" >/dev/null || groupadd --system "$GROUP"
    usermod -aG "$GROUP" "$TARGET_USER"
    if getent group bluetooth >/dev/null; then
        usermod -aG bluetooth "$TARGET_USER"
    fi
fi

install -D -m 644 "$RULES_DIR/50-openflight-network.rules" "$ROOT$POLKIT_RULE"
install -D -m 644 "$RULES_DIR/70-openflight-rfkill.rules" "$ROOT$UDEV_RULE"

if [ -z "$ROOT" ]; then
    udevadm control --reload-rules >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=misc --sysname-match=rfkill >/dev/null 2>&1 || true
    # polkitd watches rules.d, but older builds only reload on restart.
    systemctl try-restart polkit >/dev/null 2>&1 || true
fi

log "Connectivity permissions installed for '$TARGET_USER'."
log "Log out and back in (or reboot) so the $GROUP and bluetooth groups apply."
