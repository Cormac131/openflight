"""Minimal typed wrapper around a dbus-fast system-bus connection.

The NetworkManager and BlueZ adapters only need method calls and property
access. Calling by explicit (service, path, interface, member, signature)
avoids an introspection round-trip per object and keeps the adapters easy to
read against the upstream D-Bus API documentation.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from .models import ConnectivityError, ErrorCode

try:  # dbus-fast is Linux-only in pyproject; import lazily so macOS dev still works.
    from dbus_fast import BusType, DBusError, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus

    DBUS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on non-Linux dev machines
    DBUS_AVAILABLE = False
    BusType = DBusError = Message = MessageType = Variant = MessageBus = None  # type: ignore

PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
DEFAULT_TIMEOUT_S = 10.0

_PERMISSION_ERRORS = (
    "org.freedesktop.DBus.Error.AccessDenied",
    "org.freedesktop.DBus.Error.AuthFailed",
    "org.freedesktop.DBus.Error.InteractiveAuthorizationRequired",
    "org.freedesktop.NetworkManager.PermissionDenied",
    "org.freedesktop.NetworkManager.Settings.PermissionDenied",
    "org.bluez.Error.NotAuthorized",
    "org.bluez.Error.NotPermitted",
)
_SERVICE_ERRORS = (
    "org.freedesktop.DBus.Error.ServiceUnknown",
    "org.freedesktop.DBus.Error.NameHasNoOwner",
    "org.freedesktop.DBus.Error.Spawn.ServiceNotFound",
    "org.freedesktop.DBus.Error.Disconnected",
)
_NOT_FOUND_ERRORS = (
    "org.freedesktop.DBus.Error.UnknownObject",
    "org.freedesktop.DBus.Error.UnknownMethod",
    "org.freedesktop.DBus.Error.UnknownInterface",
    "org.freedesktop.DBus.Error.UnknownProperty",
    "org.bluez.Error.DoesNotExist",
    "org.freedesktop.NetworkManager.UnknownConnection",
    "org.freedesktop.NetworkManager.UnknownDevice",
)
_TIMEOUT_ERRORS = (
    "org.freedesktop.DBus.Error.NoReply",
    "org.freedesktop.DBus.Error.Timeout",
    "org.freedesktop.DBus.Error.TimedOut",
)


def translate_dbus_error(
    name: str, text: str = "", default: ErrorCode = ErrorCode.FAILED
) -> ConnectivityError:
    """Map a D-Bus error name to a stable user-facing code."""
    detail = f"{name}: {text}" if text else name
    if name in _PERMISSION_ERRORS:
        return ConnectivityError(ErrorCode.PERMISSION_DENIED, detail)
    if name in _SERVICE_ERRORS:
        return ConnectivityError(ErrorCode.SERVICE_UNAVAILABLE, detail)
    if name in _NOT_FOUND_ERRORS:
        return ConnectivityError(ErrorCode.NOT_FOUND, detail)
    if name in _TIMEOUT_ERRORS:
        return ConnectivityError(ErrorCode.TIMEOUT, detail)
    return ConnectivityError(default, detail)


def unwrap(value: Any) -> Any:
    """Recursively replace dbus-fast ``Variant`` wrappers with plain values."""
    if Variant is not None and isinstance(value, Variant):
        return unwrap(value.value)
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unwrap(item) for item in value]
    return value


class DBusClient:
    """Explicit method/property calls over one connected ``MessageBus``."""

    def __init__(self, bus: Any):
        self.bus = bus

    @classmethod
    async def connect_system(cls, bus_address: Optional[str] = None) -> "DBusClient":
        """Connect to the system bus (or ``bus_address`` in tests)."""
        if not DBUS_AVAILABLE:
            raise ConnectivityError(ErrorCode.UNSUPPORTED_PLATFORM, "dbus-fast is not installed")
        try:
            if bus_address:
                bus = MessageBus(bus_address=bus_address)
            else:
                bus = MessageBus(bus_type=BusType.SYSTEM)
            await asyncio.wait_for(bus.connect(), DEFAULT_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError) as exc:
            raise ConnectivityError(
                ErrorCode.SERVICE_UNAVAILABLE, f"System D-Bus unavailable: {exc}"
            ) from exc
        return cls(bus)

    def disconnect(self) -> None:
        """Close the bus connection."""
        self.bus.disconnect()

    async def call(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        service: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: Optional[list] = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        default_error: ErrorCode = ErrorCode.FAILED,
    ) -> list:
        """Call a method and return its (still Variant-wrapped) reply body."""
        message = Message(
            destination=service,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        )
        try:
            reply = await asyncio.wait_for(self.bus.call(message), timeout_s)
        except asyncio.TimeoutError as exc:
            raise ConnectivityError(ErrorCode.TIMEOUT, f"{interface}.{member} timed out") from exc
        except DBusError as exc:  # pragma: no cover - dbus-fast returns ERROR replies
            raise translate_dbus_error(exc.type, exc.text, default_error) from exc
        except (EOFError, OSError) as exc:
            raise ConnectivityError(ErrorCode.SERVICE_UNAVAILABLE, str(exc)) from exc
        if reply is None:
            return []
        if reply.message_type == MessageType.ERROR:
            text = reply.body[0] if reply.body and isinstance(reply.body[0], str) else ""
            raise translate_dbus_error(reply.error_name or "", text, default_error)
        return list(reply.body)

    async def get(self, service: str, path: str, interface: str, prop: str) -> Any:
        """Read one property as a plain value."""
        body = await self.call(service, path, PROPERTIES_IFACE, "Get", "ss", [interface, prop])
        return unwrap(body[0])

    async def get_all(self, service: str, path: str, interface: str) -> dict:
        """Read every property of an interface as plain values."""
        body = await self.call(service, path, PROPERTIES_IFACE, "GetAll", "s", [interface])
        return unwrap(body[0])

    async def set(self, service: str, path: str, interface: str, prop: str, value: Any) -> None:
        """Write one property; ``value`` must already be a ``Variant``."""
        await self.call(service, path, PROPERTIES_IFACE, "Set", "ssv", [interface, prop, value])

    async def add_match(self, rule: str) -> None:
        """Subscribe to signals matching ``rule``."""
        await self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "AddMatch",
            "s",
            [rule],
        )

    async def remove_match(self, rule: str) -> None:
        """Drop a subscription added with :meth:`add_match` (best effort)."""
        try:
            await self.call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "RemoveMatch",
                "s",
                [rule],
            )
        except ConnectivityError:
            pass

    def add_message_handler(self, handler: Callable[[Any], Any]) -> None:
        """Receive every incoming message (signals included)."""
        self.bus.add_message_handler(handler)

    def remove_message_handler(self, handler: Callable[[Any], Any]) -> None:
        """Stop receiving messages on ``handler``."""
        self.bus.remove_message_handler(handler)

    def export(self, path: str, interface: Any) -> None:
        """Publish a ``ServiceInterface`` (e.g. the BlueZ pairing agent)."""
        self.bus.export(path, interface)

    def unexport(self, path: str, interface: Any = None) -> None:
        """Withdraw an exported interface."""
        self.bus.unexport(path, interface)
