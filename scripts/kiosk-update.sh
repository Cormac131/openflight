# Sourced by start-kiosk.sh. The launcher owns every swap of the install
# link: it applies a staged release before anything else starts, confirms it
# once the server answers, rolls it back when the server never does, and
# relaunches itself when the server asks for a restart by exiting with
# OPENFLIGHT_UPDATE_RESTART_STATUS (openflight.update.RESTART_EXIT_CODE).
#
# Expects from the caller: PROJECT_DIR, ORIGINAL_ARGS, log(), warn(),
# release_instance_lock(), stop_startup_splash_server(), stop_kiosk_browser().

OPENFLIGHT_UPDATE_RESTART_STATUS=75

# The CLI ships inside every managed release tree; a checkout that predates
# the updater simply has none, and every hook below is then a no-op.
_update_cli() {
    local cli="$PROJECT_DIR/.venv/bin/openflight-update"
    [ -x "$cli" ] || return 127
    "$cli" --install-link "${OPENFLIGHT_INSTALL_LINK:-$PROJECT_DIR}" "$@"
}

_relaunch_kiosk() {
    exec bash "$PROJECT_DIR/scripts/start-kiosk.sh" "${ORIGINAL_ARGS[@]}"
}

apply_staged_update() {
    if [ "${OPENFLIGHT_UPDATE_APPLIED:-}" = 1 ]; then
        return 0
    fi
    local status=0
    _update_cli apply --if-staged || status=$?
    case "$status" in
        0)
            log "Update applied; relaunching from $PROJECT_DIR"
            release_instance_lock
            export OPENFLIGHT_UPDATE_APPLIED=1
            _relaunch_kiosk
            ;;
        2|127)
            return 0
            ;;
        *)
            warn "Could not apply the staged update (exit $status); starting the current release"
            return 0
            ;;
    esac
}

confirm_applied_update() {
    local status=0
    _update_cli confirm >/dev/null || status=$?
    if [ "$status" -eq 0 ]; then
        log "Update confirmed: this release is now the fallback for the next one"
    elif [ "$status" -ne 2 ] && [ "$status" -ne 127 ]; then
        warn "Could not confirm the applied update (exit $status)"
    fi
    return 0
}

rollback_pending_update() {
    local reason="$1"
    local status=0
    _update_cli rollback --reason "$reason" >/dev/null || status=$?
    if [ "$status" -eq 0 ]; then
        warn "Rolled the update back; the previous release runs on the next start"
    elif [ "$status" -ne 2 ] && [ "$status" -ne 127 ]; then
        warn "Could not roll the update back (exit $status)"
    fi
    return 0
}

restart_into_staged_update() {
    log "Restarting to apply the staged update..."
    trap - SIGINT SIGTERM
    stop_startup_splash_server
    stop_kiosk_browser
    release_instance_lock
    unset OPENFLIGHT_UPDATE_APPLIED
    export OPENFLIGHT_UPDATE_RESTART=1
    _relaunch_kiosk
}
