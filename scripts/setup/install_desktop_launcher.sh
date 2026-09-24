#!/usr/bin/env bash
# Install or refresh a terminal-free Raspberry Pi desktop launcher.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(dirname "$(dirname "$script_dir")")"
example_launcher="$script_dir/run-openflight.example.sh"
project_name="$(basename "$project_dir")"

if [[ "$project_name" == openflight ]]; then
    install_suffix=""
elif [[ "$project_name" == openflight-* ]]; then
    install_suffix="${project_name#openflight-}"
else
    install_suffix="$project_name"
fi

if [[ -n "$install_suffix" ]] && [[ ! "$install_suffix" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "ERROR: checkout folder suffix contains unsupported characters: $install_suffix" >&2
    exit 1
fi

if [[ -n "$install_suffix" ]]; then
    launcher_name="run-openflight-$install_suffix.sh"
    desktop_name="OpenFlight-$install_suffix.desktop"
    application_name="OpenFlight ($install_suffix)"
    return_desktop_name="Return-to-OpenFlight-$install_suffix.desktop"
    return_application_name="Return to OpenFlight ($install_suffix)"
else
    launcher_name="run-openflight.sh"
    desktop_name="OpenFlight.desktop"
    application_name="OpenFlight"
    return_desktop_name="Return-to-OpenFlight.desktop"
    return_application_name="Return to OpenFlight"
fi

launcher_path="${OPENFLIGHT_LAUNCHER_PATH:-$HOME/$launcher_name}"

if [[ -n "${OPENFLIGHT_DESKTOP_DIR:-}" ]]; then
    desktop_dir="$OPENFLIGHT_DESKTOP_DIR"
elif command -v xdg-user-dir >/dev/null 2>&1; then
    desktop_dir="$(xdg-user-dir DESKTOP)"
else
    desktop_dir="$HOME/Desktop"
fi
desktop_path="${OPENFLIGHT_DESKTOP_FILE:-$desktop_dir/$desktop_name}"

if [[ "$launcher_path" == *[[:space:]]* ]]; then
    echo "ERROR: launcher path cannot contain whitespace: $launcher_path" >&2
    exit 1
fi
if [[ "$project_dir" == *[[:space:]]* ]]; then
    echo "ERROR: checkout path cannot contain whitespace: $project_dir" >&2
    exit 1
fi

applications_dir="${OPENFLIGHT_APPLICATIONS_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/applications}"

# "Return to OpenFlight" reopens the kiosk after the in-app Show desktop
# action (or starts OpenFlight if it is not running). It is generated from
# this checkout, so it is always refreshed without prompting.
install_return_entry() {
    local target_dir="$1"
    local target="$target_dir/$return_desktop_name"
    local temporary
    mkdir -p "$target_dir"
    temporary="$(mktemp "$target_dir/.$return_desktop_name.XXXXXX")"
    {
        printf '%s\n' '[Desktop Entry]'
        printf '%s\n' 'Version=1.0'
        printf '%s\n' 'Type=Application'
        printf 'Name=%s\n' "$return_application_name"
        printf '%s\n' 'Comment=Go back to the OpenFlight kiosk screen'
        printf 'Exec=/bin/bash -ilc "%s --launcher %s"\n' \
            "$project_dir/scripts/return-to-openflight.sh" "$launcher_path"
        printf 'Path=%s\n' "$project_dir"
        printf 'Icon=%s\n' "$project_dir/ui/public/openflight-icon-black.png"
        printf '%s\n' 'Terminal=false'
        printf '%s\n' 'StartupNotify=false'
        printf '%s\n' 'Categories=Game;Sports;'
    } > "$temporary"
    install -m 755 "$temporary" "$target"
    rm -f "$temporary"
    if [[ "${OPENFLIGHT_SKIP_DESKTOP_TRUST:-false}" != true ]] && command -v gio >/dev/null 2>&1; then
        gio set "$target" metadata::trusted true 2>/dev/null || true
    fi
}

install_return_entry "$desktop_dir"
install_return_entry "$applications_dir"
echo "Installed 'Return to OpenFlight' launcher at $desktop_dir/$return_desktop_name"

mkdir -p "$desktop_dir"
temporary_entry="$(mktemp "$desktop_dir/.$desktop_name.XXXXXX")"
temporary_launcher=""
cleanup() {
    rm -f "$temporary_entry"
    if [[ -n "$temporary_launcher" ]]; then
        rm -f "$temporary_launcher"
    fi
}
trap cleanup EXIT
{
    printf '%s\n' '[Desktop Entry]'
    printf '%s\n' 'Version=1.0'
    printf '%s\n' 'Type=Application'
    printf 'Name=%s\n' "$application_name"
    printf '%s\n' 'Comment=OpenFlight golf launch monitor'
    printf 'Exec=/bin/bash -ilc %s\n' "$launcher_path"
    printf 'Path=%s\n' "$project_dir"
    printf 'Icon=%s\n' "$project_dir/ui/public/openflight-icon-black.png"
    printf '%s\n' 'Terminal=false'
    printf '%s\n' 'StartupNotify=false'
    printf '%s\n' 'Categories=Game;Sports;'
} > "$temporary_entry"

if [[ -e "$desktop_path" ]]; then
    replacement_answer=""
    echo "A desktop entry already exists: $desktop_path" >&2
    echo "Replacing it will first create a timestamped backup beside it." >&2
    printf "Replace it? [y/N] " >&2
    if ! read -r replacement_answer; then
        replacement_answer=""
    fi
    case "$replacement_answer" in
        y|Y|[yY][eE][sS])
            ;;
        *)
            echo "Existing desktop entry preserved: $desktop_path"
            exit 0
            ;;
    esac
    if [[ ! -r "$desktop_path" ]]; then
        echo "Existing desktop entry preserved: $desktop_path"
        echo "ERROR: the existing entry is not readable, so no backup can be created." >&2
        exit 1
    fi
    backup_path="$(mktemp "$desktop_path.backup-$(date +%Y%m%d-%H%M%S).XXXXXX")"
    cp -p "$desktop_path" "$backup_path"
    echo "Backed up the existing desktop entry to $backup_path"
fi

mkdir -p "$(dirname "$launcher_path")"
if [[ ! -e "$launcher_path" ]]; then
    temporary_launcher="$(mktemp "$(dirname "$launcher_path")/.$launcher_name.XXXXXX")"
    {
        IFS= read -r shebang < "$example_launcher"
        printf '%s\n' "$shebang"
        printf 'openflight_installed_dir=%q\n' "$project_dir"
        tail -n +2 "$example_launcher"
    } > "$temporary_launcher"
    install -m 755 "$temporary_launcher" "$launcher_path"
    rm -f "$temporary_launcher"
    temporary_launcher=""
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
