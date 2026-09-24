#!/usr/bin/env bash
# Shared "Show desktop" / "Return to OpenFlight" control-directory protocol.
#
# Sourced by start-kiosk.sh (the supervisor) and return-to-openflight.sh (the
# desktop launcher). The OpenFlight server only ever creates the fixed
# `show-desktop` request file; see src/openflight/connectivity/desktop.py.
#
#   <dir>/kiosk.pid      PID of the start-kiosk.sh supervisor
#   <dir>/mode           "kiosk" or "desktop"
#   <dir>/show-desktop   request: close the kiosk browser, keep the server
#   <dir>/return         request: relaunch the kiosk browser

# The systemd service may run without XDG_RUNTIME_DIR while the desktop
# launcher has it, so both fall back to the same per-user runtime directory.
kiosk_control_dir() {
    local port="${1:-8080}"
    local base="${XDG_RUNTIME_DIR:-}"
    if [ -z "$base" ] && [ -d "/run/user/$(id -u)" ]; then
        base="/run/user/$(id -u)"
    fi
    printf '%s/openflight-kiosk-%s\n' "${base:-/tmp}" "$port"
}

# Create the directory privately. Refuses a pre-existing directory that is a
# symlink or owned by someone else (matters when XDG_RUNTIME_DIR is unset and
# the fallback is the shared /tmp).
kiosk_control_prepare() {
    local dir="$1"
    local pid="$2"
    if [ -L "$dir" ]; then
        return 1
    fi
    if [ -e "$dir" ] && { [ ! -d "$dir" ] || [ ! -O "$dir" ]; }; then
        return 1
    fi
    mkdir -p -m 700 "$dir" || return 1
    chmod 700 "$dir" || return 1
    rm -f "$dir/show-desktop" "$dir/return"
    printf '%s\n' "$pid" > "$dir/kiosk.pid"
    kiosk_control_set_mode "$dir" kiosk
}

kiosk_control_set_mode() {
    printf '%s\n' "$2" > "$1/mode"
}

kiosk_control_mode() {
    cat "$1/mode" 2>/dev/null || printf 'kiosk\n'
}

kiosk_control_supervisor_alive() {
    local dir="$1"
    local pid
    [ -d "$dir" ] && [ ! -L "$dir" ] && [ -O "$dir" ] || return 1
    pid="$(cat "$dir/kiosk.pid" 2>/dev/null)" || return 1
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$pid" 2>/dev/null
}

# Consume a request file. Succeeds only if the request was pending.
kiosk_control_take_request() {
    local path="$1/$2"
    [ -e "$path" ] || return 1
    rm -f "$path"
}

# Leave a request for a live supervisor. Fails when nothing is supervising.
kiosk_control_request() {
    local dir="$1"
    local name="$2"
    kiosk_control_supervisor_alive "$dir" || return 1
    : > "$dir/$name"
}

kiosk_control_cleanup() {
    local dir="$1"
    [ -n "$dir" ] && [ -d "$dir" ] && [ ! -L "$dir" ] || return 0
    rm -f "$dir/kiosk.pid" "$dir/mode" "$dir/show-desktop" "$dir/return"
    rmdir "$dir" 2>/dev/null || true
}

desktop_session_available() {
    [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ] || [ -S /tmp/.X11-unix/X0 ]
}
