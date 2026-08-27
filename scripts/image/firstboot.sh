#!/bin/bash
#
# OpenFlight first-boot provisioning.
#
# Runs once on a freshly written SD card, before the kiosk starts, and does
# the setup that can only happen on the owner's actual hardware:
#
#   1. Record what is plugged in, so support questions have an answer.
#   2. Save rolling-buffer mode to the OPS243-A's flash memory.
#   3. Power the unit off so the radar comes up in that mode (see below).
#   4. On the next boot, self-test and hand over to the kiosk.
#
# Why the power off. The OPS243-A has a firmware bug: switching into rolling
# buffer mode at runtime leaves the HOST_INT pin in the wrong mode, so the
# sound trigger never fires. OmniPreSense's workaround is to write the mode
# to flash and power cycle the radar. A reboot is not enough — the radar
# keeps its power across one — so the unit shuts down instead and asks the
# owner to switch it back on. That happens exactly once, on the first boot.
#
# Re-run it later with:
#     sudo rm /var/lib/openflight/firstboot.state
#     sudo systemctl start openflight-firstboot
# or by creating a file named "openflight-reprovision" on the boot partition,
# which is the version that works without a terminal.

set -uo pipefail

PROJECT_DIR="${OPENFLIGHT_PROJECT_DIR:-/opt/openflight}"
STATE_DIR="${OPENFLIGHT_STATE_DIR:-/var/lib/openflight}"
STATE_FILE="$STATE_DIR/firstboot.state"
REPORT_FILE="$STATE_DIR/hardware-report.txt"
CONFIG_DIR="${OPENFLIGHT_CONFIG_DIR:-/etc/openflight}"
HARDWARE_ENV="$CONFIG_DIR/hardware.env"
LOG_FILE="${OPENFLIGHT_FIRSTBOOT_LOG:-/var/log/openflight-firstboot.log}"

# Both are checked because the boot partition moved in Raspberry Pi OS
# Bookworm and older cards written by older tools still use the old path.
BOOT_DIRS=(/boot/firmware /boot)
REPROVISION_MARKER="openflight-reprovision"

log() {
    printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG_FILE"
}

run_logged() {
    # Run a command, sending everything to the log and the console. Never
    # fatal: first boot must always end with a usable machine.
    log "\$ $*"
    "$@" 2>&1 | tee -a "$LOG_FILE"
    return "${PIPESTATUS[0]}"
}

read_stage() {
    if [ -r "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo "configure"
    fi
}

write_stage() {
    mkdir -p "$STATE_DIR"
    printf '%s\n' "$1" > "$STATE_FILE"
}

# An owner with no terminal can reset provisioning by dropping a file on the
# boot partition from their laptop.
check_reprovision_marker() {
    local dir
    for dir in "${BOOT_DIRS[@]}"; do
        if [ -f "$dir/$REPROVISION_MARKER" ]; then
            log "Found $dir/$REPROVISION_MARKER — re-running provisioning"
            rm -f "$dir/$REPROVISION_MARKER"
            write_stage "configure"
            return 0
        fi
    done
    return 0
}

# The Raspberry Pi Imager creates the owner's account at first boot, so the
# image cannot know its name in advance. The kiosk runs as that user and needs
# to write to the project tree (uv syncs the venv, npm writes ui/dist), so
# hand the tree over to it once the account exists. UID 1000 is the account
# Imager creates; anything else on the machine is a later addition.
hand_project_to_owner() {
    local owner
    owner="$(getent passwd 1000 | cut -d: -f1)"
    if [ -z "$owner" ]; then
        log "No user account at UID 1000 yet; leaving $PROJECT_DIR owned by root"
        return 1
    fi
    if [ "$(stat -c '%U' "$PROJECT_DIR")" = "$owner" ]; then
        return 0
    fi
    log "Handing $PROJECT_DIR to $owner"
    chown -R "$owner:$owner" "$PROJECT_DIR"
    # The owner also needs group access to the radars' serial ports and the
    # I2C bus the inclinometer and battery gauge sit on.
    usermod -aG dialout,i2c,gpio,video "$owner" 2>&1 | tee -a "$LOG_FILE" || true
}

detect_hardware() {
    mkdir -p "$CONFIG_DIR" "$STATE_DIR"
    log "Detecting attached hardware..."
    if ! (cd "$PROJECT_DIR" && uv run --quiet python -m openflight.provisioning \
            --report --write "$HARDWARE_ENV" > "$REPORT_FILE" 2>>"$LOG_FILE"); then
        log "Hardware detection failed; see $LOG_FILE"
        return 1
    fi
    tee -a "$LOG_FILE" < "$REPORT_FILE"
    return 0
}

ops243_present() {
    # hardware.env records the detected device list; grep beats re-probing.
    grep -q '^OPENFLIGHT_DETECTED_DEVICES=.*ops243' "$HARDWARE_ENV" 2>/dev/null
}

configure_radar_flash() {
    log "Saving rolling-buffer mode to the OPS243-A's flash memory..."
    (cd "$PROJECT_DIR" && run_logged uv run --quiet python \
        scripts/hardware-test/test_rolling_buffer_persist.py --setup)
}

install_ftdi_latency_rule() {
    # Only matters for the deprecated K-LD7 radars, and is a no-op without
    # them. Non-interactive, unlike the full device-naming wizard, which
    # needs the owner to plug the radars in one at a time.
    if [ -x "$PROJECT_DIR/scripts/setup/setup_kld7_latency.sh" ] \
        && grep -q '^OPENFLIGHT_DETECTED_DEVICES=.*kld7' "$HARDWARE_ENV" 2>/dev/null; then
        log "K-LD7 detected — installing the FTDI low-latency udev rule"
        run_logged "$PROJECT_DIR/scripts/setup/setup_kld7_latency.sh" --all-ftdi
    fi
}

self_test() {
    log "Running the hardware self-test..."
    (cd "$PROJECT_DIR" && run_logged uv run --quiet python \
        scripts/hardware-test/diagnose.py --no-interactive)
}

announce() {
    # Written where the kiosk UI and a curious owner can both find it.
    printf '%s\n' "$1" > "$STATE_DIR/status.txt"
    log "$1"
}

main() {
    mkdir -p "$STATE_DIR" "$CONFIG_DIR"
    touch "$LOG_FILE"
    check_reprovision_marker

    local stage
    stage="$(read_stage)"
    log "OpenFlight first-boot provisioning — stage: $stage"

    case "$stage" in
        configure)
            hand_project_to_owner
            detect_hardware
            install_ftdi_latency_rule

            if ! ops243_present; then
                # Nothing to persist and nothing to power cycle for. Let the
                # owner reach the UI, which will show the missing radar far
                # more clearly than a console message they will never see.
                announce "No OPS243-A radar found. Check its USB cable, then reboot."
                write_stage "done"
                return 0
            fi

            if configure_radar_flash; then
                write_stage "verify"
                announce "Setup complete. This unit will now switch off — switch it back on to start."
                log "Powering off so the radar restarts in rolling-buffer mode"
                sync
                systemctl poweroff
            else
                # The radar answered detection but not configuration. Carry on
                # to the kiosk: ball speed still works, only the sound trigger
                # is affected, and the self-test will say so.
                announce "Could not configure the radar. OpenFlight will start anyway; see $LOG_FILE."
                write_stage "verify"
            fi
            ;;

        verify)
            hand_project_to_owner
            detect_hardware
            if self_test; then
                announce "Hardware self-test passed."
            else
                announce "Hardware self-test reported problems. See $REPORT_FILE."
            fi
            write_stage "done"
            ;;

        done)
            log "Provisioning already complete; refreshing the hardware record"
            detect_hardware
            ;;

        *)
            log "Unrecognised stage '$stage'; restarting provisioning"
            write_stage "configure"
            ;;
    esac
    return 0
}

main "$@"
