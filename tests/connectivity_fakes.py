"""Fake NetworkManager and BlueZ services on a private dbus-daemon.

The real D-Bus adapters (``openflight.connectivity.networkmanager`` and
``.bluez``) talk to these over a genuine bus connection, so the tests cover
message signatures, Variant handling, error replies, signals and the BlueZ
agent call-back path, not just Python-level mocks. Only behaviour the adapters
rely on is modelled.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from contextlib import contextmanager
from typing import Any, Callable, Optional

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

PROPS = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
WIFI_DEVICE = f"{NM_PATH}/Devices/2"
ETH_DEVICE = f"{NM_PATH}/Devices/1"


class FakeError(Exception):
    """Raise from a fake method to send a D-Bus error reply."""

    def __init__(self, name: str, text: str = ""):
        super().__init__(text)
        self.name = name
        self.text = text


@contextmanager
def private_bus():
    """Start a throwaway dbus-daemon and yield its address."""
    daemon = shutil.which("dbus-daemon")
    if daemon is None:
        raise RuntimeError("dbus-daemon is not installed")
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        [daemon, "--session", "--nofork", "--nopidfile", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        address = process.stdout.readline().strip()
        yield address
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(5)


class FakeService:
    """Generic object tree with property and method dispatch."""

    def __init__(self, name: str):
        self.name = name
        self.bus: Optional[MessageBus] = None
        self.objects: dict[str, dict[str, dict[str, Variant]]] = {}
        self.methods: dict[tuple[str, str], Callable[[Message], Any]] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.deny: set[str] = set()
        self.deny_error = "org.freedesktop.DBus.Error.AccessDenied"

    async def start(self, address: str) -> None:
        self.bus = await MessageBus(bus_address=address).connect()
        self.bus.add_message_handler(self._handle)
        await self.bus.request_name(self.name)

    def stop(self) -> None:
        if self.bus:
            self.bus.disconnect()

    def prop(self, path: str, iface: str, name: str) -> Any:
        return self.objects[path][iface][name].value

    def set_prop(self, path: str, iface: str, name: str, value: Any) -> None:
        current = self.objects[path][iface][name]
        self.objects[path][iface][name] = Variant(current.signature, value)

    def emit(self, path: str, iface: str, member: str, signature: str, body: list) -> None:
        self.bus.send(Message.new_signal(path, iface, member, signature, body))

    def _handle(self, msg: Message):
        if msg.message_type != MessageType.METHOD_CALL:
            return None
        self.calls.append((msg.path, msg.interface, msg.member))
        try:
            if msg.member in self.deny:
                raise FakeError(self.deny_error, "denied by test")
            if msg.interface == PROPS:
                return self._properties(msg)
            if msg.interface == OBJECT_MANAGER and msg.member == "GetManagedObjects":
                return Message.new_method_return(msg, "a{oa{sa{sv}}}", [self.objects])
            handler = self.methods.get((msg.interface, msg.member))
            if handler is None or msg.path not in self.objects:
                raise FakeError("org.freedesktop.DBus.Error.UnknownMethod", msg.member)
            result = handler(msg)
            if result is True:  # handler replies asynchronously
                return True
            signature, body = result if result else ("", [])
            return Message.new_method_return(msg, signature, body)
        except FakeError as exc:
            return Message.new_error(msg, exc.name, exc.text)

    def _properties(self, msg: Message):
        obj = self.objects.get(msg.path)
        if obj is None:
            raise FakeError("org.freedesktop.DBus.Error.UnknownObject", msg.path)
        iface = obj.get(msg.body[0])
        if iface is None:
            raise FakeError("org.freedesktop.DBus.Error.UnknownInterface", msg.body[0])
        if msg.member == "GetAll":
            return Message.new_method_return(msg, "a{sv}", [iface])
        if msg.member == "Get":
            if msg.body[1] not in iface:
                raise FakeError("org.freedesktop.DBus.Error.UnknownProperty", msg.body[1])
            return Message.new_method_return(msg, "v", [iface[msg.body[1]]])
        if msg.member == "Set":
            setter = self.methods.get((PROPS, f"Set:{msg.body[1]}"))
            if setter:
                setter(msg)
            iface[msg.body[1]] = msg.body[2]
            return Message.new_method_return(msg)
        raise FakeError("org.freedesktop.DBus.Error.UnknownMethod", msg.member)


def _ap(ssid: str, strength: int, flags: int = 0, wpa: int = 0, rsn: int = 0) -> dict:
    return {
        f"{NM}.AccessPoint": {
            "Ssid": Variant("ay", ssid.encode()),
            "Strength": Variant("y", strength),
            "Flags": Variant("u", flags),
            "WpaFlags": Variant("u", wpa),
            "RsnFlags": Variant("u", rsn),
            "Frequency": Variant("u", 2437),
            "HwAddress": Variant("s", "00:11:22:33:44:55"),
        }
    }


class FakeNetworkManager(FakeService):
    """NetworkManager with one Wi-Fi device, three APs and one saved profile."""

    CORRECT_PASSWORD = "correct-horse"

    def __init__(self):
        super().__init__(NM)
        self.deny_error = f"{NM}.PermissionDenied"
        self._next = 10
        self.secrets: dict[str, Optional[str]] = {}
        self.failure_reason = 7  # NM_DEVICE_STATE_REASON_NO_SECRETS
        self.objects = {
            NM_PATH: {
                NM: {
                    "WirelessEnabled": Variant("b", True),
                    "State": Variant("u", 70),
                    "Connectivity": Variant("u", 4),
                    "ConnectivityCheckEnabled": Variant("b", True),
                    "ConnectivityCheckAvailable": Variant("b", True),
                }
            },
            ETH_DEVICE: {f"{NM}.Device": {"DeviceType": Variant("u", 1)}},
            WIFI_DEVICE: {
                f"{NM}.Device": {
                    "DeviceType": Variant("u", 2),
                    "State": Variant("u", 100),
                    "Interface": Variant("s", "wlan0"),
                    "Ip4Config": Variant("o", f"{NM_PATH}/IP4Config/1"),
                    "StateReason": Variant("(uu)", [100, 0]),
                },
                f"{NM}.Device.Wireless": {
                    "ActiveAccessPoint": Variant("o", f"{NM_PATH}/AccessPoint/1"),
                    "LastScan": Variant("x", 1000),
                },
            },
            f"{NM_PATH}/IP4Config/1": {
                f"{NM}.IP4Config": {
                    "AddressData": Variant(
                        "aa{sv}",
                        [{"address": Variant("s", "192.168.1.40"), "prefix": Variant("u", 24)}],
                    )
                }
            },
            f"{NM_PATH}/AccessPoint/1": _ap("Home", 80, 1, 0, 0x100),
            f"{NM_PATH}/AccessPoint/2": _ap("Home", 40, 1, 0, 0x100),
            f"{NM_PATH}/AccessPoint/3": _ap("Cafe", 55),
            f"{NM_PATH}/AccessPoint/4": _ap("Corp", 70, 1, 0, 0x200),
            f"{NM_PATH}/AccessPoint/5": _ap("", 90),  # hidden SSID
            f"{NM_PATH}/Settings": {f"{NM}.Settings": {}},
        }
        self.saved: dict[str, dict] = {}
        self._add_saved("Home", "correct-horse")
        m = self.methods
        m[(NM, "GetDevices")] = lambda _msg: ("ao", [[ETH_DEVICE, WIFI_DEVICE]])
        m[(f"{NM}.Device.Wireless", "GetAllAccessPoints")] = lambda _msg: (
            "ao",
            [[p for p in self.objects if "/AccessPoint/" in p]],
        )
        m[(f"{NM}.Device.Wireless", "RequestScan")] = self._request_scan
        m[(f"{NM}.Settings", "ListConnections")] = lambda _msg: ("ao", [list(self.saved)])
        m[(f"{NM}.Settings.Connection", "GetSettings")] = self._get_settings
        m[(f"{NM}.Settings.Connection", "Delete")] = self._delete
        m[(f"{NM}.Settings.Connection", "Update")] = self._update
        m[(NM, "AddAndActivateConnection")] = self._add_and_activate
        m[(NM, "ActivateConnection")] = self._activate
        m[(NM, "DeactivateConnection")] = lambda _msg: None
        m[(f"{NM}.Device", "Disconnect")] = self._disconnect

    def _path(self, kind: str) -> str:
        self._next += 1
        return f"{NM_PATH}/{kind}/{self._next}"

    def _add_saved(self, ssid: str, psk: Optional[str]) -> str:
        path = self._path("Settings")
        settings = {
            "connection": {
                "id": Variant("s", ssid),
                "type": Variant("s", "802-11-wireless"),
            },
            "802-11-wireless": {"ssid": Variant("ay", ssid.encode())},
        }
        if psk is not None:
            settings["802-11-wireless-security"] = {"key-mgmt": Variant("s", "wpa-psk")}
        self.saved[path] = settings
        self.secrets[path] = psk
        self.objects[path] = {f"{NM}.Settings.Connection": {}}
        return path

    def add_access_point(self, ssid: str, strength: int, *, secured: bool) -> None:
        rsn = 0x100 if secured else 0
        self.objects[self._path("AccessPoint")] = _ap(ssid, strength, int(secured), 0, rsn)

    def set_nm(self, name: str, value: Any) -> None:
        self.set_prop(NM_PATH, NM, name, value)

    def saved_ssids(self) -> list[str]:
        return [s["802-11-wireless"]["ssid"].value.decode() for s in self.saved.values()]

    def _request_scan(self, _msg):
        last = self.prop(WIFI_DEVICE, f"{NM}.Device.Wireless", "LastScan")
        self.set_prop(WIFI_DEVICE, f"{NM}.Device.Wireless", "LastScan", last + 1)

    def _get_settings(self, msg):
        return "a{sa{sv}}", [self.saved[msg.path]]

    def _delete(self, msg):
        self.saved.pop(msg.path, None)
        self.secrets.pop(msg.path, None)
        self.objects.pop(msg.path, None)

    def _update(self, msg):
        settings = msg.body[0]
        psk = settings.get("802-11-wireless-security", {}).get("psk")
        self.secrets[msg.path] = psk.value if psk else None
        settings.get("802-11-wireless-security", {}).pop("psk", None)
        self.saved[msg.path] = settings

    def _start_activation(self, connection: str) -> str:
        active = self._path("ActiveConnection")
        self.objects[active] = {f"{NM}.Connection.Active": {"State": Variant("u", 1)}}
        settings = self.saved[connection]
        needs_secret = "802-11-wireless-security" in settings
        ok = not needs_secret or self.secrets.get(connection) == self.CORRECT_PASSWORD
        asyncio.get_running_loop().call_later(0.05, self._finish_activation, active, ok)
        return active

    def _finish_activation(self, active: str, ok: bool) -> None:
        if ok:
            self.set_prop(active, f"{NM}.Connection.Active", "State", 2)
            self.set_prop(WIFI_DEVICE, f"{NM}.Device", "State", 100)
            return
        self.emit(
            WIFI_DEVICE, f"{NM}.Device", "StateChanged", "uuu", [120, 50, self.failure_reason]
        )
        self.set_prop(WIFI_DEVICE, f"{NM}.Device", "State", 30)
        self.objects.pop(active, None)  # NM removes failed active connections

    def _add_and_activate(self, msg):
        settings, _device, _ap = msg.body
        ssid = settings["802-11-wireless"]["ssid"].value.decode()
        security = settings.get("802-11-wireless-security")
        psk = security.pop("psk").value if security and "psk" in security else None
        path = self._add_saved(ssid, psk)
        self.saved[path] = settings  # keep exactly what the client sent (minus the secret)
        return "oo", [path, self._start_activation(path)]

    def _activate(self, msg):
        connection, _device, _ap = msg.body
        if connection not in self.saved:
            raise FakeError(f"{NM}.UnknownConnection", connection)
        return "o", [self._start_activation(connection)]

    def _disconnect(self, _msg):
        if self.prop(WIFI_DEVICE, f"{NM}.Device", "State") != 100:
            raise FakeError(f"{NM}.Device.NotActive", "This device is not active")
        self.set_prop(WIFI_DEVICE, f"{NM}.Device", "State", 30)
        self.set_prop(WIFI_DEVICE, f"{NM}.Device.Wireless", "ActiveAccessPoint", "/")


BLUEZ = "org.bluez"
ADAPTER = "/org/bluez/hci0"
DEVICE_PAIRED = f"{ADAPTER}/dev_AA_BB_CC_00_00_01"
DEVICE_NEW = f"{ADAPTER}/dev_AA_BB_CC_00_00_02"
DEVICE_ANON = f"{ADAPTER}/dev_AA_BB_CC_00_00_03"


def _device(address: str, name: Optional[str], paired: bool, connected: bool, rssi: int) -> dict:
    props = {
        "Address": Variant("s", address),
        "Alias": Variant("s", name or address.replace(":", "-")),
        "Paired": Variant("b", paired),
        "Trusted": Variant("b", paired),
        "Connected": Variant("b", connected),
        "RSSI": Variant("n", rssi),
        "Icon": Variant("s", "audio-headphones"),
    }
    if name:
        props["Name"] = Variant("s", name)
    return {"org.bluez.Device1": props}


class FakeBluez(FakeService):
    """BlueZ with one adapter, a paired headset, a new speaker and a beacon."""

    def __init__(self):
        super().__init__(BLUEZ)
        self.agent: Optional[tuple[str, str]] = None
        self.agent_reply: Optional[list] = None
        self.pair_method = "RequestConfirmation"
        self.rfkill_blocked = False
        self.objects = {
            "/": {},
            "/org/bluez": {"org.bluez.AgentManager1": {}},
            ADAPTER: {
                "org.bluez.Adapter1": {
                    "Powered": Variant("b", True),
                    "Discovering": Variant("b", False),
                    "Alias": Variant("s", "openflight"),
                }
            },
            DEVICE_PAIRED: _device("AA:BB:CC:00:00:01", "Headset", True, True, -50),
            DEVICE_NEW: _device("AA:BB:CC:00:00:02", "Speaker", False, False, -60),
            DEVICE_ANON: _device("AA:BB:CC:00:00:03", None, False, False, -40),
        }
        m = self.methods
        m[("org.bluez.AgentManager1", "RegisterAgent")] = self._register_agent
        m[("org.bluez.AgentManager1", "UnregisterAgent")] = self._unregister_agent
        m[("org.bluez.Adapter1", "StartDiscovery")] = lambda _m: self.set_prop(
            ADAPTER, "org.bluez.Adapter1", "Discovering", True
        )
        m[("org.bluez.Adapter1", "StopDiscovery")] = self._stop_discovery
        m[("org.bluez.Adapter1", "RemoveDevice")] = self._remove_device
        m[("org.bluez.Device1", "Pair")] = self._pair
        m[("org.bluez.Device1", "Connect")] = lambda msg: self.set_prop(
            msg.path, "org.bluez.Device1", "Connected", True
        )
        m[("org.bluez.Device1", "Disconnect")] = lambda msg: self.set_prop(
            msg.path, "org.bluez.Device1", "Connected", False
        )
        m[(PROPS, "Set:Powered")] = self._set_powered

    def _set_powered(self, msg):
        if self.rfkill_blocked and msg.body[2].value:
            raise FakeError("org.bluez.Error.Failed", "Blocked through rfkill")

    def _register_agent(self, msg):
        if self.agent is not None:
            raise FakeError("org.bluez.Error.AlreadyExists", "Already Exists")
        self.agent = (msg.sender, msg.body[0])

    def _unregister_agent(self, _msg):
        self.agent = None

    def _stop_discovery(self, _msg):
        if not self.prop(ADAPTER, "org.bluez.Adapter1", "Discovering"):
            raise FakeError("org.bluez.Error.Failed", "No discovery started")
        self.set_prop(ADAPTER, "org.bluez.Adapter1", "Discovering", False)

    def _remove_device(self, msg):
        if msg.body[0] not in self.objects:
            raise FakeError("org.bluez.Error.DoesNotExist", "Does Not Exist")
        del self.objects[msg.body[0]]

    def _pair(self, msg):
        if self.prop(msg.path, "org.bluez.Device1", "Paired"):
            raise FakeError("org.bluez.Error.AlreadyExists", "Already Exists")
        asyncio.get_running_loop().create_task(self._pair_async(msg))
        return True

    async def _pair_async(self, msg: Message) -> None:
        if self.agent is None:
            self.bus.send(
                Message.new_error(msg, "org.bluez.Error.AuthenticationFailed", "no agent")
            )
            return
        sender, agent_path = self.agent
        signature, body = {
            "RequestConfirmation": ("ou", [msg.path, 42]),
            "RequestPasskey": ("o", [msg.path]),
            "RequestPinCode": ("o", [msg.path]),
            "RequestAuthorization": ("o", [msg.path]),
        }[self.pair_method]
        reply = await self.bus.call(
            Message(
                destination=sender,
                path=agent_path,
                interface="org.bluez.Agent1",
                member=self.pair_method,
                signature=signature,
                body=body,
            )
        )
        if reply.message_type == MessageType.ERROR:
            self.bus.send(
                Message.new_error(msg, "org.bluez.Error.AuthenticationRejected", reply.error_name)
            )
            return
        self.agent_reply = reply.body
        self.set_prop(msg.path, "org.bluez.Device1", "Paired", True)
        self.bus.send(Message.new_method_return(msg))


def env_has_dbus_daemon() -> bool:
    """Skip marker helper."""
    return shutil.which("dbus-daemon") is not None and os.name == "posix"
