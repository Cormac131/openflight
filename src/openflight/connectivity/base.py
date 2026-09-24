"""Backend interfaces shared by the D-Bus adapters and the mock backend."""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Callable, Optional, Protocol

from .models import (
    BluetoothStatus,
    ConnectivityError,
    ErrorCode,
    InternetState,
    NetworkStatus,
    PairingKind,
    PairingRequest,
    WifiStatus,
    validate_pairing_value,
)

logger = logging.getLogger(__name__)

# BlueZ cancels an unanswered agent request after ~30 s; answer (or reject)
# a little earlier so the user sees our message instead of a generic failure.
PAIRING_PROMPT_TIMEOUT_S = 25.0


class WifiBackend(Protocol):
    """Wi-Fi operations. Every method may raise :class:`ConnectivityError`."""

    async def status(self) -> WifiStatus:
        """Current Wi-Fi state including the last scan results."""

    async def network_status(self) -> NetworkStatus:
        """Local link state and (when known) internet reachability."""

    async def set_enabled(self, enabled: bool) -> None:
        """Turn the Wi-Fi radio on or off."""

    async def scan(self) -> None:
        """Request a scan and return once fresh results are available."""

    async def connect(self, ssid: str, password: Optional[str], hidden: bool) -> None:
        """Join ``ssid``; ``password`` is validated here and never stored by us."""

    async def disconnect(self) -> None:
        """Disconnect the Wi-Fi interface."""

    async def forget(self, ssid: str) -> None:
        """Delete every saved profile for ``ssid``."""

    async def close(self) -> None:
        """Release bus resources."""


class BluetoothBackend(Protocol):
    """Bluetooth operations. Every method may raise :class:`ConnectivityError`."""

    async def status(self) -> BluetoothStatus:
        """Adapter state plus paired and nearby devices."""

    async def set_powered(self, powered: bool) -> None:
        """Power the adapter on or off."""

    async def set_discovery(self, enabled: bool) -> None:
        """Start or stop scanning for nearby devices."""

    async def pair(self, address: str) -> None:
        """Pair (prompting through the broker when needed), trust, then connect."""

    async def connect(self, address: str) -> None:
        """Connect a paired device."""

    async def disconnect(self, address: str) -> None:
        """Disconnect a device."""

    async def forget(self, address: str) -> None:
        """Remove the pairing."""

    async def close(self) -> None:
        """Release bus resources and unregister the agent."""


class UnavailableWifi:
    """Stand-in when Wi-Fi management cannot work here (reason says why)."""

    def __init__(self, reason: ErrorCode, detail: str = ""):
        self.reason = reason
        self.detail = detail or reason.value

    async def status(self) -> WifiStatus:
        """Report the unavailable reason."""
        return WifiStatus(available=False, reason=self.reason)

    async def network_status(self) -> NetworkStatus:
        """Unknown local state; the service falls back to an internet probe."""
        return NetworkStatus(local_network=False, internet=InternetState.UNKNOWN)

    async def _fail(self, *_args, **_kwargs) -> None:
        raise ConnectivityError(self.reason, self.detail)

    set_enabled = scan = connect = disconnect = forget = _fail

    async def close(self) -> None:
        """Nothing to release."""


class UnavailableBluetooth:
    """Stand-in when Bluetooth management cannot work here."""

    def __init__(self, reason: ErrorCode, detail: str = ""):
        self.reason = reason
        self.detail = detail or reason.value

    async def status(self) -> BluetoothStatus:
        """Report the unavailable reason."""
        return BluetoothStatus(available=False, reason=self.reason)

    async def _fail(self, *_args, **_kwargs) -> None:
        raise ConnectivityError(self.reason, self.detail)

    set_powered = set_discovery = pair = connect = disconnect = forget = _fail

    async def close(self) -> None:
        """Nothing to release."""


class PairingBroker:
    """Bridges BlueZ agent callbacks (asyncio loop) to UI prompts (HTTP thread).

    ``prompt`` publishes a request and waits for :meth:`respond`. ``show``
    publishes a display-only prompt. Every prompt is closed with an
    ``on_close`` notification so the UI never shows a stale dialog.
    """

    def __init__(
        self,
        on_prompt: Callable[[PairingRequest], None],
        on_close: Callable[[str], None],
        *,
        timeout_s: float = PAIRING_PROMPT_TIMEOUT_S,
    ):
        self._on_prompt = on_prompt
        self._on_close = on_close
        self._timeout_s = timeout_s
        self._ids = itertools.count(1)
        self._pending: dict[str, tuple[PairingRequest, Optional[asyncio.Future]]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def pending(self) -> list[PairingRequest]:
        """Outstanding prompts (e.g. for a UI that reconnects mid-pairing)."""
        return [request for request, _future in self._pending.values()]

    def _new_request(
        self, address: str, name: str, kind: PairingKind, passkey: Optional[str]
    ) -> PairingRequest:
        request_id = f"pair-{next(self._ids)}"
        return PairingRequest(
            request_id=request_id,
            address=address,
            name=name,
            kind=kind,
            passkey=passkey,
            expires_in_s=self._timeout_s,
        )

    async def prompt(
        self, address: str, name: str, kind: PairingKind, passkey: Optional[str] = None
    ) -> Optional[str]:
        """Ask the user and return their value; raise CANCELLED on reject/timeout."""
        self._loop = asyncio.get_running_loop()
        request = self._new_request(address, name, kind, passkey)
        future: asyncio.Future = self._loop.create_future()
        self._pending[request.request_id] = (request, future)
        self._on_prompt(request)
        try:
            accepted, value = await asyncio.wait_for(future, self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise ConnectivityError(ErrorCode.TIMEOUT, "Pairing prompt timed out") from exc
        finally:
            self._close(request.request_id)
        if not accepted:
            raise ConnectivityError(ErrorCode.CANCELLED, "Pairing rejected on the kiosk")
        return value

    def show(self, address: str, name: str, kind: PairingKind, passkey: str) -> None:
        """Publish a display-only prompt (replaces any earlier one for ``address``)."""
        self.close_for(address)
        request = self._new_request(address, name, kind, passkey)
        self._pending[request.request_id] = (request, None)
        self._on_prompt(request)

    def respond(self, request_id: str, accept: bool, value: Optional[str] = None) -> None:
        """Answer a prompt. Thread-safe; validates the value for its kind."""
        entry = self._pending.get(request_id)
        if entry is None:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Pairing request is no longer active")
        request, future = entry
        clean_value = validate_pairing_value(request.kind, value) if accept else None
        if future is None:
            # Display-only prompts: "Cancel" aborts, "Done" just dismisses.
            self._close(request_id)
            return
        loop = self._loop
        if loop is None:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Pairing request is no longer active")

        def _resolve() -> None:
            if not future.done():
                future.set_result((accept, clean_value))

        loop.call_soon_threadsafe(_resolve)

    def cancel_all(self) -> None:
        """BlueZ cancelled the exchange (device timeout, user cancelled on device)."""
        for request_id, (_request, future) in list(self._pending.items()):
            if future is not None and not future.done():
                future.set_exception(
                    ConnectivityError(ErrorCode.CANCELLED, "Pairing cancelled by the device")
                )
            else:
                self._close(request_id)

    def close_for(self, address: str) -> None:
        """Close every display-only prompt for ``address`` (pairing finished)."""
        for request_id, (request, future) in list(self._pending.items()):
            if request.address == address and future is None:
                self._close(request_id)

    def _close(self, request_id: str) -> None:
        if self._pending.pop(request_id, None) is not None:
            try:
                self._on_close(request_id)
            except Exception:  # never let a UI emit failure break pairing
                logger.exception("[connectivity] pairing close notification failed")
