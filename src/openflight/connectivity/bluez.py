"""BlueZ Bluetooth adapter over the system D-Bus, including a pairing agent.

API reference: https://github.com/bluez/bluez/tree/master/doc (org.bluez.*.rst)

The agent is registered with ``RegisterAgent`` but deliberately *not* made the
default agent: BlueZ routes prompts for a ``Pair()`` call to the caller's
agent, so pairing started from the kiosk prompts on the kiosk while incoming
requests keep going to the desktop's own agent.
"""

from __future__ import annotations

import logging
import os
import struct
from typing import Any, Optional

from .base import PairingBroker
from .dbus_client import DBUS_AVAILABLE, DBusClient, Variant, unwrap
from .models import (
    BluetoothDevice,
    BluetoothStatus,
    ConnectivityError,
    ErrorCode,
    PairingKind,
    validate_bt_address,
)

logger = logging.getLogger(__name__)

BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
IFACE_ADAPTER = "org.bluez.Adapter1"
IFACE_DEVICE = "org.bluez.Device1"
IFACE_AGENT_MANAGER = "org.bluez.AgentManager1"
AGENT_PATH = "/org/openflight/BluetoothAgent"
AGENT_CAPABILITY = "KeyboardDisplay"

PAIR_TIMEOUT_S = 90.0
CONNECT_TIMEOUT_S = 30.0

RFKILL_DEVICE = "/dev/rfkill"
RFKILL_TYPE_BLUETOOTH = 2
RFKILL_OP_CHANGE_ALL = 3

_PAIR_ERRORS = {
    "org.bluez.Error.AuthenticationFailed": ErrorCode.AUTH_FAILED,
    "org.bluez.Error.AuthenticationRejected": ErrorCode.AUTH_FAILED,
    "org.bluez.Error.AuthenticationCanceled": ErrorCode.CANCELLED,
    "org.bluez.Error.AuthenticationTimeout": ErrorCode.TIMEOUT,
    "org.bluez.Error.ConnectionAttemptFailed": ErrorCode.FAILED,
    "org.bluez.Error.InProgress": ErrorCode.BUSY,
    "org.bluez.Error.NotReady": ErrorCode.BLOCKED,
}


def _error_name(exc: ConnectivityError) -> str:
    return exc.detail.split(":", 1)[0]


def unblock_bluetooth_rfkill(device: str = RFKILL_DEVICE) -> bool:
    """Soft-unblock every Bluetooth radio via the kernel rfkill interface.

    Writes one ``struct rfkill_event`` (idx, type, op, soft, hard). Returns
    False instead of raising when the device node is not writable; the
    connectivity setup script grants the OpenFlight group write access.
    """
    event = struct.pack("IBBBB", 0, RFKILL_TYPE_BLUETOOTH, RFKILL_OP_CHANGE_ALL, 0, 0)
    try:
        fd = os.open(device, os.O_WRONLY)
    except OSError:
        return False
    try:
        return os.write(fd, event) == len(event)
    except OSError:
        return False
    finally:
        os.close(fd)


def device_from_props(props: dict) -> BluetoothDevice:
    """Build a device record from Device1 properties."""
    address = str(props.get("Address", ""))
    rssi = props.get("RSSI")
    return BluetoothDevice(
        address=address,
        name=str(props.get("Alias") or props.get("Name") or address),
        icon=props.get("Icon"),
        paired=bool(props.get("Paired", False)),
        trusted=bool(props.get("Trusted", False)),
        connected=bool(props.get("Connected", False)),
        rssi=int(rssi) if rssi is not None else None,
    )


def visible_devices(device_props: list[dict]) -> tuple[BluetoothDevice, ...]:
    """Hide anonymous unpaired beacons; order connected, paired, then by signal."""
    devices = [
        device_from_props(props)
        for props in device_props
        if props.get("Paired") or props.get("Name")
    ]
    return tuple(
        sorted(
            devices,
            key=lambda d: (
                not d.connected,
                not d.paired,
                -(d.rssi if d.rssi is not None else -999),
                d.name.lower(),
            ),
        )
    )


if DBUS_AVAILABLE:
    from dbus_fast import DBusError
    from dbus_fast.annotations import DBusObjectPath, DBusStr, DBusUInt16, DBusUInt32
    from dbus_fast.service import ServiceInterface, dbus_method

    class PairingAgent(ServiceInterface):
        """org.bluez.Agent1 implementation that forwards prompts to the kiosk UI.

        Methods without a reply have no return annotation: dbus-fast derives
        the D-Bus signature from annotations and rejects ``-> None``.
        """

        def __init__(self, backend: "BluezBluetooth", broker: PairingBroker):
            super().__init__("org.bluez.Agent1")
            self._backend = backend
            self._broker = broker

        async def _ask(self, device: str, kind: PairingKind, passkey: Optional[str] = None):
            address, name = await self._backend.describe(device)
            try:
                return await self._broker.prompt(address, name, kind, passkey)
            except ConnectivityError as exc:
                if exc.code == ErrorCode.CANCELLED:
                    raise DBusError("org.bluez.Error.Rejected", "Rejected on kiosk") from exc
                raise DBusError("org.bluez.Error.Canceled", exc.detail) from exc

        @dbus_method()
        def Release(self):
            """BlueZ dropped the agent (e.g. bluetoothd restarted)."""
            self._backend.agent_released()

        @dbus_method()
        async def RequestPinCode(self, device: DBusObjectPath) -> DBusStr:
            """Legacy pairing: ask for a PIN."""
            return await self._ask(device, PairingKind.ENTER_PIN)

        @dbus_method()
        async def DisplayPinCode(self, device: DBusObjectPath, pincode: DBusStr):
            """Show a PIN to type on the device."""
            address, name = await self._backend.describe(device)
            self._broker.show(address, name, PairingKind.DISPLAY_PIN, pincode)

        @dbus_method()
        async def RequestPasskey(self, device: DBusObjectPath) -> DBusUInt32:
            """Ask for the six-digit passkey shown on the device."""
            return int(await self._ask(device, PairingKind.ENTER_PASSKEY))

        @dbus_method()
        async def DisplayPasskey(
            self, device: DBusObjectPath, passkey: DBusUInt32, entered: DBusUInt16
        ):
            """Show a passkey to type on the device (called again per keypress)."""
            if entered:
                return
            address, name = await self._backend.describe(device)
            self._broker.show(address, name, PairingKind.DISPLAY_PASSKEY, f"{passkey:06d}")

        @dbus_method()
        async def RequestConfirmation(self, device: DBusObjectPath, passkey: DBusUInt32):
            """Numeric comparison: confirm both screens show the same passkey."""
            await self._ask(device, PairingKind.CONFIRM, f"{passkey:06d}")

        @dbus_method()
        async def RequestAuthorization(self, device: DBusObjectPath):
            """Just-works pairing that still needs a yes."""
            await self._ask(device, PairingKind.AUTHORIZE)

        @dbus_method()
        async def AuthorizeService(self, device: DBusObjectPath, uuid: DBusStr):
            """Only services for the device the kiosk is pairing are accepted."""
            del uuid
            address, _name = await self._backend.describe(device)
            if address != self._backend.pairing_address:
                raise DBusError("org.bluez.Error.Rejected", "Not pairing this device")

        @dbus_method()
        def Cancel(self):
            """BlueZ cancelled the outstanding request."""
            self._broker.cancel_all()

else:  # pragma: no cover - non-Linux dev machines
    PairingAgent = None  # type: ignore


class BluezBluetooth:
    """:class:`~openflight.connectivity.base.BluetoothBackend` backed by BlueZ."""

    def __init__(self, client: DBusClient, broker: PairingBroker):
        self._client = client
        self._broker = broker
        self._agent: Any = PairingAgent(self, broker) if PairingAgent else None
        self._agent_exported = False
        self._agent_registered = False
        self.pairing_address: Optional[str] = None

    # -- helpers -------------------------------------------------------------

    async def _objects(self) -> dict:
        body = await self._client.call(BLUEZ, "/", OBJECT_MANAGER, "GetManagedObjects")
        return unwrap(body[0])

    @staticmethod
    def _adapter_path(objects: dict) -> Optional[str]:
        adapters = sorted(path for path, ifaces in objects.items() if IFACE_ADAPTER in ifaces)
        return adapters[0] if adapters else None

    async def _require_adapter(self) -> tuple[str, dict]:
        objects = await self._objects()
        adapter = self._adapter_path(objects)
        if adapter is None:
            raise ConnectivityError(ErrorCode.NO_ADAPTER, "No Bluetooth adapter found")
        return adapter, objects

    async def _device_path(self, address: str) -> str:
        address = validate_bt_address(address)
        adapter, objects = await self._require_adapter()
        for path, ifaces in objects.items():
            props = ifaces.get(IFACE_DEVICE)
            if props and path.startswith(adapter + "/") and props.get("Address") == address:
                return path
        raise ConnectivityError(ErrorCode.NOT_FOUND, "Bluetooth device not found")

    async def describe(self, device_path: str) -> tuple[str, str]:
        """(address, display name) for an agent callback's device path."""
        try:
            props = await self._client.get_all(BLUEZ, device_path, IFACE_DEVICE)
        except ConnectivityError:
            return device_path.rsplit("dev_", 1)[-1].replace("_", ":"), "Bluetooth device"
        device = device_from_props(props)
        return device.address, device.name

    def agent_released(self) -> None:
        """BlueZ released the agent; re-register before the next pairing."""
        self._agent_registered = False

    async def _ensure_agent(self) -> None:
        if self._agent is None:
            raise ConnectivityError(ErrorCode.UNSUPPORTED_PLATFORM, "Pairing agent unavailable")
        if not self._agent_exported:
            self._client.export(AGENT_PATH, self._agent)
            self._agent_exported = True
        try:
            await self._client.call(
                BLUEZ,
                "/org/bluez",
                IFACE_AGENT_MANAGER,
                "RegisterAgent",
                "os",
                [AGENT_PATH, AGENT_CAPABILITY],
            )
        except ConnectivityError as exc:
            if _error_name(exc) != "org.bluez.Error.AlreadyExists":
                raise
        self._agent_registered = True

    async def _device_call(self, address: str, member: str, *, timeout_s: float) -> str:
        path = await self._device_path(address)
        try:
            await self._client.call(BLUEZ, path, IFACE_DEVICE, member, timeout_s=timeout_s)
        except ConnectivityError as exc:
            mapped = _PAIR_ERRORS.get(_error_name(exc))
            if mapped:
                raise ConnectivityError(mapped, exc.detail) from exc
            raise
        return path

    # -- BluetoothBackend ----------------------------------------------------

    async def status(self) -> BluetoothStatus:
        """Adapter power/discovery plus visible devices."""
        objects = await self._objects()
        adapter = self._adapter_path(objects)
        if adapter is None:
            return BluetoothStatus(available=False, reason=ErrorCode.NO_ADAPTER)
        props = objects[adapter][IFACE_ADAPTER]
        device_props = [
            ifaces[IFACE_DEVICE]
            for path, ifaces in objects.items()
            if IFACE_DEVICE in ifaces and path.startswith(adapter + "/")
        ]
        return BluetoothStatus(
            available=True,
            powered=bool(props.get("Powered", False)),
            discovering=bool(props.get("Discovering", False)),
            adapter_name=props.get("Alias") or props.get("Name"),
            devices=visible_devices(device_props),
        )

    async def set_powered(self, powered: bool) -> None:
        """Power the adapter; retry once after an rfkill soft-unblock."""
        adapter, _objects = await self._require_adapter()
        try:
            await self._client.set(BLUEZ, adapter, IFACE_ADAPTER, "Powered", Variant("b", powered))
        except ConnectivityError as exc:
            blocked = "rfkill" in exc.detail.lower() or "Blocked" in exc.detail
            if not (powered and blocked):
                raise
            if not unblock_bluetooth_rfkill():
                raise ConnectivityError(
                    ErrorCode.BLOCKED, "Bluetooth is blocked by rfkill"
                ) from exc
            await self._client.set(BLUEZ, adapter, IFACE_ADAPTER, "Powered", Variant("b", True))

    async def set_discovery(self, enabled: bool) -> None:
        """Start/stop discovery; stopping an idle adapter is not an error."""
        adapter, objects = await self._require_adapter()
        if not objects[adapter][IFACE_ADAPTER].get("Powered"):
            raise ConnectivityError(ErrorCode.BLOCKED, "Bluetooth is off")
        member = "StartDiscovery" if enabled else "StopDiscovery"
        try:
            await self._client.call(BLUEZ, adapter, IFACE_ADAPTER, member)
        except ConnectivityError as exc:
            name = _error_name(exc)
            if enabled and name == "org.bluez.Error.InProgress":
                return
            if not enabled and name in ("org.bluez.Error.Failed", "org.bluez.Error.NotReady"):
                return
            raise

    async def pair(self, address: str) -> None:
        """Pair via our agent, mark trusted, then connect (best effort)."""
        address = validate_bt_address(address)
        await self._ensure_agent()
        self.pairing_address = address
        try:
            try:
                path = await self._device_call(address, "Pair", timeout_s=PAIR_TIMEOUT_S)
            except ConnectivityError as exc:
                if _error_name(exc) != "org.bluez.Error.AlreadyExists":
                    raise
                path = await self._device_path(address)
            await self._client.set(BLUEZ, path, IFACE_DEVICE, "Trusted", Variant("b", True))
        finally:
            self.pairing_address = None
            self._broker.close_for(address)
        await self._device_call(address, "Connect", timeout_s=CONNECT_TIMEOUT_S)

    async def connect(self, address: str) -> None:
        """Connect a known device."""
        await self._device_call(address, "Connect", timeout_s=CONNECT_TIMEOUT_S)

    async def disconnect(self, address: str) -> None:
        """Disconnect a device."""
        await self._device_call(address, "Disconnect", timeout_s=CONNECT_TIMEOUT_S)

    async def forget(self, address: str) -> None:
        """Remove the device and its pairing keys."""
        path = await self._device_path(address)
        adapter = path.rsplit("/", 1)[0]
        await self._client.call(BLUEZ, adapter, IFACE_ADAPTER, "RemoveDevice", "o", [path])

    async def close(self) -> None:
        """Unregister and unexport the agent."""
        if self._agent_registered:
            try:
                await self._client.call(
                    BLUEZ, "/org/bluez", IFACE_AGENT_MANAGER, "UnregisterAgent", "o", [AGENT_PATH]
                )
            except ConnectivityError:
                pass
            self._agent_registered = False
        if self._agent_exported:
            self._client.unexport(AGENT_PATH, self._agent)
            self._agent_exported = False
