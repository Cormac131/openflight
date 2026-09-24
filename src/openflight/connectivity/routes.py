"""HTTP + Socket.IO wiring for the kiosk Connections panel.

Commands are REST (per-request access check, real status codes). Progress,
status changes and pairing prompts are pushed over Socket.IO to a room only
local-device sockets join, so remote viewers never receive SSIDs or device
names.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from flask import Blueprint, Response, has_request_context, jsonify, request
from flask_socketio import join_room

from .access import current_request_is_local, local_device_only
from .desktop import DesktopControl
from .models import ConnectivityError, ErrorCode
from .service import ConnectivityService, backend_factory_for

logger = logging.getLogger(__name__)

LOCAL_ROOM = "system-local"
STARTUP_WAIT_S = 2.0

_STATUS_FOR_CODE = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNSUPPORTED_SECURITY: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.BUSY: 409,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.UNSUPPORTED_PLATFORM: 503,
    ErrorCode.DISABLED: 503,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.NO_ADAPTER: 503,
}

BT_DEVICE_ACTIONS = {
    "pair": "bluetooth_pair",
    "connect": "bluetooth_connect",
    "disconnect": "bluetooth_disconnect",
    "forget": "bluetooth_forget",
}


def _error_response(exc: ConnectivityError) -> tuple[Response, int]:
    return jsonify({"error": exc.to_dict()}), _STATUS_FOR_CODE.get(exc.code, 500)


def _json_body() -> dict:
    """POST bodies must be JSON objects (this also forces a CORS preflight)."""
    if not request.is_json:
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Expected application/json")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Expected a JSON object")
    return body


def join_local_room() -> bool:
    """Call from the Socket.IO ``connect`` handler; joins only local sockets."""
    if has_request_context() and current_request_is_local():
        join_room(LOCAL_ROOM)
        return True
    return False


class ConnectivityRuntime:
    """Owns the (lazily started) service behind the once-registered routes.

    The blueprint is mounted at import time; ``main()`` only configures the
    mode. Nothing touches D-Bus or the network until the kiosk browser first
    asks, so tests and headless runs that never use the panel stay inert.
    """

    def __init__(self, desktop: Optional[DesktopControl] = None):
        self.desktop = desktop or DesktopControl(None)
        self._factory: Optional[Callable[[], ConnectivityService]] = None
        self._service: Optional[ConnectivityService] = None
        self._lock = threading.Lock()

    @classmethod
    def for_service(
        cls, service: ConnectivityService, desktop: Optional[DesktopControl] = None
    ) -> "ConnectivityRuntime":
        """Wrap an already-built service (tests, embedding)."""
        runtime = cls(desktop)
        runtime.configure(lambda: service, runtime.desktop)
        return runtime

    def configure(
        self, factory: Callable[[], ConnectivityService], desktop: DesktopControl
    ) -> None:
        """Set how to build the service; stops any previously running one."""
        self.stop()
        with self._lock:
            self._factory = factory
            self.desktop = desktop

    def configure_mode(self, mode: str, emit: Callable[[str, dict], None]) -> None:
        """Production wiring for ``--connectivity``."""
        self.configure(
            lambda: ConnectivityService(emit, backend_factory=backend_factory_for(mode)),
            DesktopControl.from_environment(),
        )

    def service(self) -> ConnectivityService:
        """The running service, started on first use."""
        with self._lock:
            if self._service is None:
                if self._factory is None:
                    raise ConnectivityError(ErrorCode.DISABLED, "Connectivity is not configured")
                self._service = self._factory()
                # Backends are normally ready in milliseconds; never hold a
                # socket connect for long if the system bus is slow.
                self._service.start(timeout_s=STARTUP_WAIT_S)
            return self._service

    def ensure_started(self) -> None:
        """Start in the background of a local socket connect (never raises)."""
        try:
            self.service()
        except ConnectivityError:
            pass

    def stop(self) -> None:
        """Stop the service if it was started."""
        with self._lock:
            service, self._service = self._service, None
        if service is not None:
            service.stop()


def create_blueprint(runtime: ConnectivityRuntime) -> Blueprint:
    """Build the ``/api/system`` blueprint around one runtime."""
    bp = Blueprint("connectivity", __name__, url_prefix="/api/system")

    def payload(snapshot: dict) -> dict:
        return {**snapshot, "desktop": runtime.desktop.status(), "local": True}

    def operation(kind: str, **params: Any):
        return jsonify({"operation": runtime.service().start_operation(kind, **params)}), 202

    @bp.errorhandler(ConnectivityError)
    def handle_connectivity_error(exc: ConnectivityError):
        return _error_response(exc)

    @bp.get("/connectivity")
    @local_device_only
    def get_connectivity():
        refresh = request.args.get("refresh") in ("1", "true")
        return jsonify(payload(runtime.service().snapshot(refresh=refresh)))

    @bp.post("/wifi/scan")
    @local_device_only
    def wifi_scan():
        return operation("wifi_scan")

    @bp.post("/wifi/enabled")
    @local_device_only
    def wifi_enabled():
        body = _json_body()
        return operation("wifi_enable", enabled=body.get("enabled"))

    @bp.post("/wifi/connect")
    @local_device_only
    def wifi_connect():
        body = _json_body()
        # The password is passed straight to the backend and never logged.
        return operation(
            "wifi_connect",
            ssid=body.get("ssid"),
            password=body.get("password"),
            hidden=body.get("hidden", False),
        )

    @bp.post("/wifi/disconnect")
    @local_device_only
    def wifi_disconnect():
        return operation("wifi_disconnect")

    @bp.post("/wifi/forget")
    @local_device_only
    def wifi_forget():
        body = _json_body()
        return operation("wifi_forget", ssid=body.get("ssid"))

    @bp.post("/bluetooth/power")
    @local_device_only
    def bluetooth_power():
        body = _json_body()
        return operation("bluetooth_power", enabled=body.get("enabled"))

    @bp.post("/bluetooth/discovery")
    @local_device_only
    def bluetooth_discovery():
        body = _json_body()
        return operation("bluetooth_discovery", enabled=body.get("enabled"))

    @bp.post("/bluetooth/devices/<address>/<action>")
    @local_device_only
    def bluetooth_device(address: str, action: str):
        kind = BT_DEVICE_ACTIONS.get(action)
        if kind is None:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Unknown action")
        return operation(kind, address=address)

    @bp.post("/bluetooth/pairing/<request_id>")
    @local_device_only
    def bluetooth_pairing(request_id: str):
        body = _json_body()
        runtime.service().respond_pairing(request_id, body.get("accept"), body.get("value"))
        return jsonify({"status": "ok"})

    @bp.post("/desktop/show")
    @local_device_only
    def show_desktop():
        if not runtime.desktop.request_show_desktop():
            raise ConnectivityError(ErrorCode.SERVICE_UNAVAILABLE, "No desktop session")
        logger.info("[connectivity] show-desktop requested from the kiosk")
        return jsonify({"status": "showing_desktop"}), 202

    return bp
