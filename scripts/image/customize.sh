#!/bin/bash
#
# Turn a stock Raspberry Pi OS Desktop filesystem into an OpenFlight appliance.
#
# Runs INSIDE the chroot that build-image.sh sets up, as root, with the repo
# already staged at /opt/openflight and the generated branding assets at
# /tmp/openflight-branding. It is deliberately separate from build-image.sh:
# everything here is "what the image contains", everything there is "how to
# mount and pack it", and the two change for entirely different reasons.
#
# Not called directly. See scripts/image/build-image.sh.

set -euo pipefail

PROJECT_DIR=/opt/openflight
BRANDING_DIR=/tmp/openflight-branding
BOOT_DIR=/boot/firmware
THEME_DIR=/usr/share/plymouth/themes/openflight

log() { echo "[customize] $*"; }

# ──────────────────────────────────────────────────────────────────────
# Packages
# ──────────────────────────────────────────────────────────────────────
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
# chromium-browser is the kiosk; the rest are what start-kiosk.sh and the
# radar drivers need. plymouth-themes brings the script plugin our theme uses.
apt-get install -y --no-install-recommends \
    chromium-browser \
    curl \
    git \
    i2c-tools \
    nodejs \
    npm \
    plymouth \
    plymouth-themes \
    python3-dev \
    python3-venv \
    unclutter \
    xdotool
apt-get clean
rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────────────────────────────
# Python environment
# ──────────────────────────────────────────────────────────────────────
log "Installing uv"
# Pinned so a broken or unexpected installer cannot poison every card from
# this image. UV_INSTALL_DIR keeps it on PATH for root, the owner, and systemd.
export UV_INSTALL_DIR=/usr/local/bin
export UV_UNMANAGED_INSTALL=/usr/local/bin
curl -LsSf https://astral.sh/uv/0.8.22/install.sh | sh

log "Building the Python environment"
cd "$PROJECT_DIR"
# Pre-sync so first boot does not spend minutes compiling wheels in front of
# somebody who just wants to hit a golf ball. start-kiosk.sh re-syncs on every
# start, which is a no-op when nothing has changed.
uv sync --extra ui --quiet

log "Building the UI"
cd "$PROJECT_DIR/ui"
if [ -f dist/index.html ]; then
    log "UI already built on the host; skipping npm"
else
    npm ci --no-audit --no-fund
    npm run build
fi
# node_modules is 200+ MB and is not needed at runtime — ui/dist is what the
# server serves. start-kiosk.sh reinstalls it if a rebuild is ever needed.
rm -rf node_modules
cd "$PROJECT_DIR"

# ──────────────────────────────────────────────────────────────────────
# Hardware interfaces
# ──────────────────────────────────────────────────────────────────────
log "Enabling I2C and the GPIO UART"
CONFIG_TXT="$BOOT_DIR/config.txt"
# The inclinometer and battery gauge sit on I2C; the OPS243 can be wired to
# the 40-pin UART instead of USB. Both are cheap to leave on and impossible
# for a non-technical owner to enable later.
grep -q '^dtparam=i2c_arm=on' "$CONFIG_TXT" || echo 'dtparam=i2c_arm=on' >> "$CONFIG_TXT"
grep -q '^enable_uart=1' "$CONFIG_TXT" || echo 'enable_uart=1' >> "$CONFIG_TXT"
grep -q '^i2c-dev' /etc/modules || echo 'i2c-dev' >> /etc/modules

# A login console on the 40-pin UART transmits boot chatter into the radar's
# RxD pin, where it is parsed as API commands — 'A' followed by '!' is a
# flash write. Disable it before that can ever happen.
systemctl disable serial-getty@ttyAMA0.service 2>/dev/null || true
sed -i 's/console=serial0,[0-9]*//' "$BOOT_DIR/cmdline.txt"

# ──────────────────────────────────────────────────────────────────────
# Branding
# ──────────────────────────────────────────────────────────────────────
log "Installing the boot splash"
mkdir -p "$THEME_DIR"
install -m 644 "$PROJECT_DIR/scripts/image/plymouth/openflight.plymouth" "$THEME_DIR/"
install -m 644 "$PROJECT_DIR/scripts/image/plymouth/openflight.script" "$THEME_DIR/"
install -m 644 "$BRANDING_DIR/openflight-splash.png" "$THEME_DIR/splash.png"
install -m 644 "$BRANDING_DIR/openflight-dot.png" "$THEME_DIR/dot.png"
plymouth-set-default-theme openflight
update-initramfs -u 2>/dev/null || true

# Raspberry Pi OS shows its own rainbow square and kernel messages before
# plymouth starts. Suppressing them makes the splash the first thing on
# screen, which is the difference between an appliance and a computer.
grep -q '^disable_splash=1' "$CONFIG_TXT" || echo 'disable_splash=1' >> "$CONFIG_TXT"
CMDLINE="$(cat "$BOOT_DIR/cmdline.txt")"
for option in quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0; do
    case "$CMDLINE" in
        *"$option"*) ;;
        *) CMDLINE="$CMDLINE $option" ;;
    esac
done
# cmdline.txt must stay a single line or the kernel silently drops the rest.
echo "$CMDLINE" | tr -s ' ' | tr -d '\n' > "$BOOT_DIR/cmdline.txt"

log "Installing the wallpaper and icons"
install -D -m 644 "$BRANDING_DIR/openflight-wallpaper.png" \
    /usr/share/rpd-wallpaper/openflight.png
for size in 16 24 32 48 64 128 256; do
    install -D -m 644 "$BRANDING_DIR/icons/openflight-$size.png" \
        "/usr/share/icons/hicolor/${size}x${size}/apps/openflight.png"
done
gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

# The desktop reads its wallpaper from a per-user file that does not exist
# until somebody logs in. /etc/xdg is the system-wide skeleton those are
# seeded from, so writing it here brands the very first login.
mkdir -p /etc/xdg/pcmanfm/LXDE-pi
cat > /etc/xdg/pcmanfm/LXDE-pi/desktop-items-0.conf <<'DESKTOP_ITEMS'
[*]
wallpaper_mode=crop
wallpaper=/usr/share/rpd-wallpaper/openflight.png
desktop_bg=#0e0f10
desktop_fg=#ffffff
desktop_shadow=#0e0f10
show_documents=0
show_trash=0
show_mounts=0
DESKTOP_ITEMS

# labwc (the Wayland compositor on Bookworm and later) paints its own root
# surface before pcmanfm draws the wallpaper. Without this the first second
# of the desktop is labwc's default grey.
mkdir -p /etc/xdg/labwc/rc.xml.d
cat > /etc/xdg/labwc/rc.xml.d/10-openflight-background.xml <<'LABWC'
<?xml version="1.0"?>
<labwc_config>
  <theme><color name="background">#0e0f10</color></theme>
</labwc_config>
LABWC

log "Installing the application launchers"
install -D -m 644 "$PROJECT_DIR/scripts/image/openflight-kiosk.desktop" \
    /etc/xdg/autostart/openflight-kiosk.desktop
install -D -m 644 "$PROJECT_DIR/scripts/image/OpenFlight.desktop" \
    /usr/share/applications/OpenFlight.desktop

# ──────────────────────────────────────────────────────────────────────
# Services and configuration
# ──────────────────────────────────────────────────────────────────────
log "Installing the first-boot provisioning service"
install -D -m 644 "$PROJECT_DIR/scripts/image/systemd/openflight-firstboot.service" \
    /etc/systemd/system/openflight-firstboot.service
systemctl enable openflight-firstboot.service

log "Placing openflight.conf on the boot partition"
# The boot partition is FAT and mounts on any computer, so this is the one
# file an owner can edit without ever opening a terminal.
install -m 644 "$PROJECT_DIR/scripts/image/openflight.conf" "$BOOT_DIR/openflight.conf"

log "Creating runtime directories"
install -d -m 755 /etc/openflight /var/lib/openflight

# Session logs land in the owner's home; the tree itself is handed to that
# account on first boot, once Imager has created it.
chmod -R a+rX "$PROJECT_DIR"

# ──────────────────────────────────────────────────────────────────────
# Kiosk behaviour
# ──────────────────────────────────────────────────────────────────────
log "Configuring kiosk behaviour"
# Blanking the screen mid-session looks like a crash to somebody standing
# over a golf mat, and there is no keyboard to wake it with.
mkdir -p /etc/xdg/autostart
cat > /etc/xdg/autostart/openflight-no-blank.desktop <<'NOBLANK'
[Desktop Entry]
Type=Application
Name=OpenFlight display settings
Comment=Keep the screen awake for the launch monitor
Exec=sh -c "xset s off -dpms 2>/dev/null; unclutter -idle 3 -root 2>/dev/null &"
Terminal=false
NoDisplay=true
NOBLANK

# Chromium's restore prompt after an unclean shutdown blocks the kiosk behind
# a dialog nobody can dismiss without a keyboard.
mkdir -p /etc/chromium/policies/managed
cat > /etc/chromium/policies/managed/openflight.json <<'POLICY'
{
  "RestoreOnStartup": 1,
  "HomepageLocation": "http://localhost:8080",
  "BookmarkBarEnabled": false,
  "DefaultBrowserSettingEnabled": false,
  "MetricsReportingEnabled": false,
  "PromotionalTabsEnabled": false,
  "TranslateEnabled": false
}
POLICY

log "Done"
