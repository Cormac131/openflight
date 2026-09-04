#!/usr/bin/env bash
# Persist hardware-setup answers into a local run-openflight.sh wrapper.

_rewrite_launcher_args() {
    local file="$1"
    local mode="$2"
    local flag="$3"
    local desired="$4"
    local tmp

    tmp="$(mktemp)"
    awk -v mode="$mode" -v flag="$flag" -v desired="$desired" '
        BEGIN { in_args = 0; found = 0 }
        { sub(/\r$/, "") }
        /^openflight_args=\(/ {
            in_args = 1
            print
            next
        }
        in_args && /^\)/ {
            if (mode == "ensure" && !found) {
                print desired
            }
            in_args = 0
            print
            next
        }
        in_args {
            stripped = $0
            sub(/^[[:space:]]+/, "", stripped)
            is_comment = (stripped ~ /^#/)
            body = stripped
            if (is_comment) {
                sub(/^#[[:space:]]*/, "", body)
            }
            matches = (body ~ ("^" flag "([[:space:]]|$)"))
            if (mode == "remove" && matches && !is_comment) {
                next
            }
            if (mode == "ensure" && matches) {
                if (!found) {
                    print desired
                    found = 1
                }
                next
            }
            if (mode == "ensure" && !found && $0 ~ /Add additional flags here/) {
                print desired
                found = 1
            }
            print
            next
        }
        { print }
    ' "$file" > "$tmp"
    cat "$tmp" > "$file"
    rm -f "$tmp"
}

_ensure_launcher_arg() {
    local file="$1"
    local flag="$2"
    local value="${3:-}"
    local desired

    if [[ -n "$value" ]]; then
        desired="    ${flag} ${value}"
    else
        desired="    ${flag}"
    fi
    _rewrite_launcher_args "$file" ensure "$flag" "$desired"
}

_remove_launcher_arg() {
    _rewrite_launcher_args "$1" remove "$2" ""
}

apply_hardware_launcher_flags() {
    local launcher="$1"
    local kld7_tilt="${2:-}"
    local battery="${3:-false}"
    local mock="${4:-false}"

    if [[ ! -f "$launcher" ]]; then
        echo "ERROR: launcher not found: $launcher" >&2
        return 1
    fi

    if [[ "$mock" == true ]]; then
        _ensure_launcher_arg "$launcher" --mock
    else
        _remove_launcher_arg "$launcher" --mock
    fi

    if [[ -n "$kld7_tilt" ]]; then
        _ensure_launcher_arg "$launcher" --kld7
        _ensure_launcher_arg "$launcher" --kld7-mount-tilt "$kld7_tilt"
    fi

    if [[ "$battery" == true ]]; then
        _ensure_launcher_arg "$launcher" --battery geekworm
    fi
}
