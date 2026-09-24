"""Connectivity service: runs the D-Bus backends on a private asyncio loop.

Flask handlers run on worker threads, while dbus-fast is asyncio-based. The
service owns one event loop thread; request handlers submit short reads with
:meth:`ConnectivityService.snapshot` and start long-running work (scan,
connect, pair) with :meth:`ConnectivityService.start_operation`, which returns
immediately. Progress is published through the ``emit`` callback so the HTTP
request never blocks on a radio.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .base import PairingBroker, UnavailableBluetooth, UnavailableWifi
from .models import (
    BluetoothStatus,
    ConnectivityError,
    ErrorCode,
    InternetState,
    NetworkStatus,
    PairingRequest,
    WifiStatus,
    validate_bt_address,
    validate_ssid,
)

logger = logging.getLogger(__name__)

EVENT_STATUS = "connectivity_status"
EVENT_OPERATION = "connectivity_operation"
EVENT_PAIRING_REQUEST = "bluetooth_pairing_request"
EVENT_PAIRING_CLOSED = "bluetooth_pairing_closed"

MODES = ("auto", "mock", "off")
DISCOVERY_DURATION_S = 30.0
INTERNET_PROBE_TTL_S = 30.0
INTERNET_PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443))
SNAPSHOT_TIMEOUT_S = 8.0
MAX_PASSWORD_CHARS = 64

Emit = Callable[[str, dict], None]
BackendFactory = Callable[[PairingBroker], Awaitable[tuple[Any, Any]]]


async def probe_internet(
    targets: tuple[tuple[str, int], ...] = INTERNET_PROBE_TARGETS, timeout_s: float = 2.0
) -> bool:
    """True if any well-known anycast address accepts a TCP connection.

    Plain IPs avoid depending on DNS, so a working local network with no
    upstream is reported as "local only" rather than hanging.
    """

    async def attempt(host: str, port: int) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout_s)
        except (OSError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True

    tasks = [asyncio.ensure_future(attempt(host, port)) for host, port in targets]
    try:
        for finished in asyncio.as_completed(tasks):
            if await finished:
                return True  # first success is enough; don't wait on slow targets
        return False
    finally:
        for task in tasks:
            task.cancel()


def backend_factory_for(mode: str, *, bus_address: Optional[str] = None) -> BackendFactory:
    """Build the (wifi, bluetooth) backends for ``mode``."""

    async def create(broker: PairingBroker) -> tuple[Any, Any]:
        if mode == "off":
            return UnavailableWifi(ErrorCode.DISABLED), UnavailableBluetooth(ErrorCode.DISABLED)
        if mode == "mock":
            from .mock import MockBluetooth, MockWifi

            return MockWifi(), MockBluetooth(broker)
        if not sys.platform.startswith("linux"):
            return (
                UnavailableWifi(ErrorCode.UNSUPPORTED_PLATFORM),
                UnavailableBluetooth(ErrorCode.UNSUPPORTED_PLATFORM),
            )
        from .bluez import BluezBluetooth
        from .dbus_client import DBusClient
        from .networkmanager import NetworkManagerWifi

        try:
            client = await DBusClient.connect_system(bus_address)
        except ConnectivityError as exc:
            return UnavailableWifi(exc.code, exc.detail), UnavailableBluetooth(exc.code, exc.detail)
        # A stopped NetworkManager/bluetoothd surfaces as SERVICE_UNAVAILABLE on
        # each call, so the backends recover on their own once it starts.
        return NetworkManagerWifi(client), BluezBluetooth(client, broker)

    return create


@dataclass
class Operation:
    """A long-running request the UI is waiting on."""

    op_id: str
    kind: str
    target: Optional[str]
    state: str = "pending"  # pending|succeeded|failed
    error: Optional[dict] = None

    def to_dict(self) -> dict:
        """Serialise for the UI (never includes request secrets)."""
        return {
            "id": self.op_id,
            "kind": self.kind,
            "target": self.target,
            "state": self.state,
            "error": self.error,
        }


# Operation kind -> mutual-exclusion group (None: may overlap anything).
OPERATION_GROUPS = {
    "wifi_scan": "wifi",
    "wifi_connect": "wifi",
    "wifi_disconnect": "wifi",
    "wifi_forget": "wifi",
    "wifi_enable": "wifi",
    "bluetooth_power": "bluetooth",
    "bluetooth_discovery": None,
    "bluetooth_pair": "bluetooth",
    "bluetooth_connect": "bluetooth",
    "bluetooth_disconnect": "bluetooth",
    "bluetooth_forget": "bluetooth",
}


class ConnectivityService:
    """Thread-safe facade over the Wi-Fi and Bluetooth backends."""

    def __init__(
        self,
        emit: Emit,
        *,
        backend_factory: BackendFactory,
        refresh_interval_s: float = 15.0,
        internet_probe: Callable[[], Awaitable[bool]] = probe_internet,
        discovery_duration_s: float = DISCOVERY_DURATION_S,
    ):
        self._emit = emit
        self._backend_factory = backend_factory
        self._refresh_interval_s = refresh_interval_s
        self._internet_probe = internet_probe
        self._discovery_duration_s = discovery_duration_s
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._busy: dict[str, Operation] = {}
        self._operations: dict[str, Operation] = {}
        self._snapshot: Optional[dict] = None
        self._probe_cache: tuple[float, bool] = (0.0, False)
        self._discovery_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self.wifi: Any = UnavailableWifi(ErrorCode.SERVICE_UNAVAILABLE)
        self.bluetooth: Any = UnavailableBluetooth(ErrorCode.SERVICE_UNAVAILABLE)
        self.broker = PairingBroker(self._on_pairing_prompt, self._on_pairing_closed)

    # -- lifecycle -------------------------------------------------------------

    def start(self, timeout_s: float = 10.0) -> None:
        """Start the loop thread and create backends (never raises for missing hardware)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="connectivity", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout_s):
            logger.warning("[connectivity] backends did not initialise within %.0fs", timeout_s)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        loop.create_task(self._startup())
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _startup(self) -> None:
        await self._create_backends()
        self._ready.set()
        self._refresh_task = asyncio.ensure_future(self._refresh_forever())

    async def _create_backends(self) -> None:
        try:
            self.wifi, self.bluetooth = await self._backend_factory(self.broker)
        except Exception as exc:  # hardware/platform problems must not stop the server
            logger.warning("[connectivity] backend setup failed: %s", exc)
            self.wifi = UnavailableWifi(ErrorCode.SERVICE_UNAVAILABLE, str(exc))
            self.bluetooth = UnavailableBluetooth(ErrorCode.SERVICE_UNAVAILABLE, str(exc))

    def stop(self, timeout_s: float = 5.0) -> None:
        """Close backends and stop the loop thread."""
        loop = self._loop
        if loop is None or self._thread is None:
            return

        async def shutdown() -> None:
            for task in (self._refresh_task, self._discovery_task):
                if task:
                    task.cancel()
            for backend in (self.bluetooth, self.wifi):
                try:
                    await backend.close()
                except Exception:
                    logger.exception("[connectivity] backend close failed")

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout_s)
        except Exception:
            logger.exception("[connectivity] shutdown did not complete cleanly")
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout_s)
        self._thread = None
        self._loop = None

    def _submit(
        self, make_coro: Callable[[], Awaitable], timeout_s: float = SNAPSHOT_TIMEOUT_S
    ) -> Any:
        if self._loop is None:
            raise ConnectivityError(ErrorCode.SERVICE_UNAVAILABLE, "Connectivity service stopped")
        return asyncio.run_coroutine_threadsafe(make_coro(), self._loop).result(timeout_s)

    # -- status ----------------------------------------------------------------

    def snapshot(self, refresh: bool = False) -> dict:
        """Latest status; ``refresh`` forces a fresh read (bounded by a timeout)."""
        if refresh or self._snapshot is None:
            try:
                return self._submit(lambda: self._refresh(emit_if_changed=True))
            except Exception as exc:
                logger.warning("[connectivity] status refresh failed: %s", exc)
                if self._snapshot is None:
                    return self._unavailable_snapshot()
        return self._snapshot

    def _unavailable_snapshot(self) -> dict:
        return {
            "wifi": WifiStatus(available=False, reason=ErrorCode.SERVICE_UNAVAILABLE).to_dict(),
            "bluetooth": BluetoothStatus(
                available=False, reason=ErrorCode.SERVICE_UNAVAILABLE
            ).to_dict(),
            "network": NetworkStatus(False, InternetState.UNKNOWN).to_dict(),
            "operations": [],
            "pairing": [],
        }

    async def _refresh_forever(self) -> None:
        while True:
            try:
                await self._refresh(emit_if_changed=True)
            except Exception:  # CancelledError is a BaseException and still propagates
                logger.exception("[connectivity] periodic refresh failed")
            await asyncio.sleep(self._refresh_interval_s)

    async def _refresh(self, *, emit_if_changed: bool) -> dict:
        wifi = await self._safe_wifi_status()
        bluetooth = await self._safe_bluetooth_status()
        network = await self._network_status()
        snapshot = {
            "wifi": wifi.to_dict(),
            "bluetooth": bluetooth.to_dict(),
            "network": network.to_dict(),
            "operations": self._busy_operations(),
            "pairing": [request.to_dict() for request in self.broker.pending()],
        }
        changed = snapshot != self._snapshot
        self._snapshot = snapshot
        if emit_if_changed and changed:
            self._safe_emit(EVENT_STATUS, snapshot)
        return snapshot

    def _busy_operations(self) -> list[dict]:
        # HTTP threads mutate _busy; copy under the lock before iterating.
        with self._lock:
            return [op.to_dict() for op in self._busy.values()]

    async def _safe_wifi_status(self) -> WifiStatus:
        try:
            return await self.wifi.status()
        except ConnectivityError as exc:
            return WifiStatus(available=False, reason=exc.code)

    async def _safe_bluetooth_status(self) -> BluetoothStatus:
        try:
            return await self.bluetooth.status()
        except ConnectivityError as exc:
            return BluetoothStatus(available=False, reason=exc.code)

    async def _network_status(self) -> NetworkStatus:
        try:
            status = await self.wifi.network_status()
        except ConnectivityError:
            status = NetworkStatus(False, InternetState.UNKNOWN)
        if status.internet != InternetState.UNKNOWN:
            return status
        if status.source != "none" and not status.local_network:
            return status
        online = await self._cached_probe()
        if status.source == "none":
            # No NetworkManager: the probe is the only signal we have.
            return NetworkStatus(
                online, InternetState.ONLINE if online else InternetState.UNKNOWN, "probe"
            )
        return NetworkStatus(
            True, InternetState.ONLINE if online else InternetState.LIMITED, "probe"
        )

    async def _cached_probe(self) -> bool:
        checked_at, online = self._probe_cache
        if time.monotonic() - checked_at < INTERNET_PROBE_TTL_S and checked_at:
            return online
        try:
            online = bool(await self._internet_probe())
        except Exception:
            online = False
        self._probe_cache = (time.monotonic(), online)
        return online

    # -- operations ------------------------------------------------------------

    def start_operation(self, kind: str, **params: Any) -> dict:
        """Validate, then run ``kind`` in the background. Returns the pending op.

        Raises INVALID_REQUEST for bad input and BUSY when another operation in
        the same group (Wi-Fi or Bluetooth) is still running.
        """
        if kind not in OPERATION_GROUPS:
            raise ConnectivityError(ErrorCode.INVALID_REQUEST, f"Unknown operation {kind}")
        target, runner = self._prepare(kind, params)
        group = OPERATION_GROUPS[kind]
        operation = Operation(op_id=f"op-{next(self._ids)}", kind=kind, target=target)
        with self._lock:
            if group and group in self._busy:
                raise ConnectivityError(ErrorCode.BUSY, f"{group} is busy")
            if group:
                self._busy[group] = operation
            self._operations[operation.op_id] = operation
        if self._loop is None:
            self._finish(operation, group, ConnectivityError(ErrorCode.SERVICE_UNAVAILABLE))
            raise ConnectivityError(ErrorCode.SERVICE_UNAVAILABLE, "Connectivity service stopped")
        logger.info("[connectivity] %s started (target=%s)", kind, target)
        self._safe_emit(EVENT_OPERATION, operation.to_dict())
        asyncio.run_coroutine_threadsafe(self._run_operation(operation, group, runner), self._loop)
        return operation.to_dict()

    def _prepare(  # pylint: disable=too-many-return-statements
        self, kind: str, params: dict
    ) -> tuple[Optional[str], Callable[[], Awaitable]]:
        """Synchronous validation; returns (display target, coroutine factory)."""
        if kind == "wifi_scan":
            return None, self.wifi.scan
        if kind == "wifi_disconnect":
            return None, self.wifi.disconnect
        if kind in ("wifi_enable", "bluetooth_power", "bluetooth_discovery"):
            enabled = params.get("enabled")
            if not isinstance(enabled, bool):
                raise ConnectivityError(ErrorCode.INVALID_REQUEST, "enabled must be true or false")
            if kind == "wifi_enable":
                return None, lambda: self.wifi.set_enabled(enabled)
            if kind == "bluetooth_power":
                return None, lambda: self.bluetooth.set_powered(enabled)
            return None, lambda: self._set_discovery(enabled)
        if kind in ("wifi_connect", "wifi_forget"):
            ssid = validate_ssid(params.get("ssid"))
            if kind == "wifi_forget":
                return ssid, lambda: self.wifi.forget(ssid)
            password = params.get("password")
            hidden = params.get("hidden", False)
            if password is not None and (
                not isinstance(password, str) or len(password) > MAX_PASSWORD_CHARS
            ):
                raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Invalid password")
            if not isinstance(hidden, bool):
                raise ConnectivityError(ErrorCode.INVALID_REQUEST, "hidden must be true or false")
            return ssid, lambda: self.wifi.connect(ssid, password or None, hidden)
        address = validate_bt_address(params.get("address"))
        method = {
            "bluetooth_pair": "pair",
            "bluetooth_connect": "connect",
            "bluetooth_disconnect": "disconnect",
            "bluetooth_forget": "forget",
        }[kind]
        return address, lambda: getattr(self.bluetooth, method)(address)

    async def _set_discovery(self, enabled: bool) -> None:
        if self._discovery_task:
            self._discovery_task.cancel()
            self._discovery_task = None
        await self.bluetooth.set_discovery(enabled)
        if enabled:
            self._discovery_task = asyncio.ensure_future(self._stop_discovery_later())

    async def _stop_discovery_later(self) -> None:
        await asyncio.sleep(self._discovery_duration_s)
        self._discovery_task = None
        try:
            await self.bluetooth.set_discovery(False)
        except ConnectivityError as exc:
            logger.info("[connectivity] stopping discovery failed: %s", exc.detail)
        await self._refresh(emit_if_changed=True)

    async def _run_operation(
        self, operation: Operation, group: Optional[str], runner: Callable[[], Awaitable]
    ) -> None:
        error: Optional[ConnectivityError] = None
        try:
            await runner()
        except ConnectivityError as exc:
            error = exc
        except Exception as exc:  # unexpected backend bug: report, never crash the loop
            logger.exception("[connectivity] %s crashed", operation.kind)
            error = ConnectivityError(ErrorCode.FAILED, type(exc).__name__)
        self._finish(operation, group, error)
        try:
            await self._refresh(emit_if_changed=True)
        except Exception:
            logger.exception("[connectivity] refresh after %s failed", operation.kind)

    def _finish(
        self, operation: Operation, group: Optional[str], error: Optional[ConnectivityError]
    ) -> None:
        operation.state = "failed" if error else "succeeded"
        operation.error = error.to_dict() if error else None
        with self._lock:
            if group and self._busy.get(group) is operation:
                del self._busy[group]
            self._operations.pop(operation.op_id, None)
        if error:
            logger.info(
                "[connectivity] %s failed (target=%s): %s",
                operation.kind,
                operation.target,
                error.code.value,
            )
        else:
            logger.info("[connectivity] %s succeeded (target=%s)", operation.kind, operation.target)
        self._safe_emit(EVENT_OPERATION, operation.to_dict())

    # -- pairing ---------------------------------------------------------------

    def respond_pairing(self, request_id: str, accept: bool, value: Optional[str]) -> None:
        """Answer an agent prompt from the UI thread."""
        if not isinstance(accept, bool):
            raise ConnectivityError(ErrorCode.INVALID_REQUEST, "accept must be true or false")
        if value is not None and not isinstance(value, str):
            raise ConnectivityError(ErrorCode.INVALID_REQUEST, "value must be text")
        self.broker.respond(request_id, accept, value)

    def _on_pairing_prompt(self, request: PairingRequest) -> None:
        self._safe_emit(EVENT_PAIRING_REQUEST, request.to_dict())

    def _on_pairing_closed(self, request_id: str) -> None:
        self._safe_emit(EVENT_PAIRING_CLOSED, {"id": request_id})

    def _safe_emit(self, event: str, payload: dict) -> None:
        try:
            self._emit(event, payload)
        except Exception:
            logger.exception("[connectivity] emitting %s failed", event)
