#!/usr/bin/env bash
# Install or refresh a terminal-free Raspberry Pi desktop launcher.

set -euo pipefail

replace_desktop=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --replace-desktop)
            replace_desktop=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--replace-desktop]"
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            echo "Usage: $0 [--replace-desktop]" >&2
            exit 2
            ;;
    esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$(dirname "$script_dir")")"
example_launcher="$script_dir/run-openflight.example.sh"
launcher_path="${OPENFLIGHT_LAUNCHER_PATH:-$HOME/run-openflight.sh}"

if [[ -n "${OPENFLIGHT_DESKTOP_DIR:-}" ]]; then
    desktop_dir="$OPENFLIGHT_DESKTOP_DIR"
elif command -v xdg-user-dir >/dev/null 2>&1; then
    desktop_dir="$(xdg-user-dir DESKTOP)"
else
    desktop_dir="$HOME/Desktop"
fi
desktop_path="${OPENFLIGHT_DESKTOP_FILE:-$desktop_dir/OpenFlight.desktop}"

if [[ "$launcher_path" == *[[:space:]]* ]]; then
    echo "ERROR: launcher path cannot contain whitespace: $launcher_path" >&2
    exit 1
fi

mkdir -p "$desktop_dir"
temporary_entry="$(mktemp "$desktop_dir/.OpenFlight.desktop.XXXXXX")"
trap 'rm -f "$temporary_entry"' EXIT
{
    printf '%s\n' '[Desktop Entry]'
    printf '%s\n' 'Version=1.0'
    printf '%s\n' 'Type=Application'
    printf '%s\n' 'Name=OpenFlight'
    printf '%s\n' 'Comment=OpenFlight golf launch monitor'
    printf 'Exec=/bin/bash -lc %s\n' "$launcher_path"
    printf 'Path=%s\n' "$project_dir"
    printf 'Icon=%s\n' "$project_dir/ui/public/openflightlogo.svg"
    printf '%s\n' 'Terminal=false'
    printf '%s\n' 'StartupNotify=false'
    printf '%s\n' 'Categories=Game;Sports;'
} > "$temporary_entry"

if [[ -e "$desktop_path" ]] && ! cmp -s "$temporary_entry" "$desktop_path"; then
    if [[ "$replace_desktop" != true ]]; then
        echo "Existing desktop entry preserved: $desktop_path"
        echo "Rerun with --replace-desktop to replace it after creating a backup."
        exit 0
    fi
    backup_path="$(mktemp "$desktop_path.backup-$(date +%Y%m%d-%H%M%S).XXXXXX")"
    cp -p "$desktop_path" "$backup_path"
    echo "Backed up the existing desktop entry to $backup_path"
fi

mkdir -p "$(dirname "$launcher_path")"
if [[ ! -e "$launcher_path" ]]; then
    install -m 755 "$example_launcher" "$launcher_path"
    echo "Installed the example local launcher at $launcher_path"
else
    chmod +x "$launcher_path"
    echo "Preserved the existing local launcher at $launcher_path"
fi

install -m 755 "$temporary_entry" "$desktop_path"
rm -f "$temporary_entry"
trap - EXIT

if [[ "${OPENFLIGHT_SKIP_DESKTOP_TRUST:-false}" != true ]] && command -v gio >/dev/null 2>&1; then
    if ! gio set "$desktop_path" metadata::trusted true; then
        echo "WARNING: Desktop trust metadata was unavailable; the launcher is still executable." >&2
    fi
fi

echo "Installed terminal-free desktop launcher at $desktop_path"
echo "Edit $launcher_path to configure hardware specific to this Pi."
