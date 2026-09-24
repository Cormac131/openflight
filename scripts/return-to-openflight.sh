#!/usr/bin/env bash
# "Return to OpenFlight" desktop launcher.
#
# If start-kiosk.sh is still supervising (the user chose Show desktop, or the
# kiosk browser was closed), ask it to reopen the kiosk browser against the
# running server. Otherwise start OpenFlight the normal way.
#
# Usage: return-to-openflight.sh [--port PORT] [--launcher PATH] [--no-start]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=scripts/kiosk-control.sh
. "$SCRIPT_DIR/kiosk-control.sh"

port="${OPENFLIGHT_WEB_PORT:-8080}"
launcher=""
start_if_stopped=true

while [ "$#" -gt 0 ]; do
    case "$1" in
        --port)
            port="${2:?--port requires a value}"
            shift 2
            ;;
        --launcher)
            launcher="${2:?--launcher requires a value}"
            shift 2
            ;;
        --no-start)
            start_if_stopped=false
            shift
            ;;
        *)
            printf 'return-to-openflight: unknown option %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if kiosk_control_request "$(kiosk_control_dir "$port")" return; then
    exit 0
fi

if [ "$start_if_stopped" != true ]; then
    exit 1
fi

if [ -n "$launcher" ] && [ -x "$launcher" ]; then
    exec "$launcher"
fi
exec "$PROJECT_DIR/scripts/start-kiosk.sh" --startup-splash --port "$port"
