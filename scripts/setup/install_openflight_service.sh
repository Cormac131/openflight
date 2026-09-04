#!/usr/bin/env bash
# Install openflight.service for the current user. Do not sudo-copy the
# template: User=, WorkingDirectory=, and ExecStart= are filled in here.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$(dirname "$script_dir")")"
template="$script_dir/openflight.service"
unit_out="${OPENFLIGHT_SYSTEMD_UNIT_OUT:-/etc/systemd/system/openflight.service}"

if [[ "$(id -u)" -eq 0 ]]; then
    echo "ERROR: run as the OpenFlight user (not root). The script sudoes only for systemd." >&2
    exit 1
fi

if [[ ! -f "$template" ]]; then
    echo "ERROR: missing unit template: $template" >&2
    exit 1
fi

OPENFLIGHT_SKIP_DESKTOP_ENTRY=true OPENFLIGHT_SKIP_DESKTOP_TRUST=true \
    "$script_dir/install_desktop_launcher.sh"
launcher_path="$("$script_dir/install_desktop_launcher.sh" --print-launcher-path)"

rendered="$(
    sed -e "s|^User=.*|User=$USER|" \
        -e "s|/home/coleman/openflight|$project_dir|g" \
        -e "s|^ExecStart=/home/coleman/run-openflight.sh\$|ExecStart=$launcher_path|" \
        "$template"
)"

if [[ "$unit_out" == /etc/systemd/system/openflight.service ]]; then
    printf '%s\n' "$rendered" | sudo tee "$unit_out" > /dev/null
    sudo systemctl daemon-reload
    if [[ "${OPENFLIGHT_SYSTEMD_ENABLE:-true}" == true ]]; then
        sudo systemctl enable openflight
    fi
    echo "Installed $unit_out for user $USER"
    echo "ExecStart=$launcher_path"
    echo "Manage with: sudo systemctl {start|stop|status} openflight"
else
    mkdir -p "$(dirname "$unit_out")"
    printf '%s\n' "$rendered" > "$unit_out"
    echo "Wrote $unit_out for user $USER"
fi
