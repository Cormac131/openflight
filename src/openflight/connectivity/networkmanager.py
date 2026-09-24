"""NetworkManager Wi-Fi adapter over the system D-Bus.

API reference: https://networkmanager.dev/docs/api/latest/spec.html

Only the calls the kiosk needs are used: device/AP property reads,
``RequestScan``, ``AddAndActivateConnection``/``ActivateConnection``,
``Device.Disconnect`` and ``Settings.Connection.Delete``/``Update``. Polkit
decides whether this process may make them (see
``scripts/setup/setup_connectivity.sh``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .dbus_client import DBusClient, Variant, unwrap
from .models import (
    ConnectivityError,
    ErrorCode,
    InternetState,
    NetworkStatus,
    WifiNetwork,
    WifiSecurity,
    WifiStatus,
    validate_ssid,
    validate_wifi_password,
)

logger = logging.getLogger(__name__)

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
IFACE_NM = NM
IFACE_DEVICE = f"{NM}.Device"
IFACE_WIRELESS = f"{NM}.Device.Wireless"
IFACE_AP = f"{NM}.AccessPoint"
IFACE_ACTIVE = f"{NM}.Connection.Active"
IFACE_SETTINGS = f"{NM}.Settings"
IFACE_CONNECTION = f"{NM}.Settings.Connection"
IFACE_IP4 = f"{NM}.IP4Config"

DEVICE_TYPE_WIFI = 2

# NMDeviceState
DEVICE_UNMANAGED = 10
DEVICE_UNAVAILABLE = 20
DEVICE_DISCONNECTED = 30
DEVICE_ACTIVATED = 100
DEVICE_FAILED = 120

# NMActiveConnectionState
ACTIVE_ACTIVATED = 2
ACTIVE_DEACTIVATED = 4

# NMState: CONNECTED_LOCAL and above means an interface has an address.
NM_STATE_CONNECTED_LOCAL = 50

# NMConnectivityState
CONNECTIVITY_MAP = {
    1: InternetState.OFFLINE,
    2: InternetState.PORTAL,
    3: InternetState.LIMITED,
    4: InternetState.ONLINE,
}

# NMDeviceStateReason values that mean "wrong or missing password".
AUTH_FAILURE_REASONS = {7, 8, 10, 11}
SSID_NOT_FOUND_REASON = 53

# NM80211ApFlags / NM80211ApSecurityFlags
AP_FLAGS_PRIVACY = 0x1
SEC_KEY_MGMT_PSK = 0x100
SEC_KEY_MGMT_802_1X = 0x200
SEC_KEY_MGMT_SAE = 0x400
SEC_KEY_MGMT_OWE = 0x800
SEC_KEY_MGMT_OWE_TM = 0x1000
SEC_KEY_MGMT_EAP_SUITE_B_192 = 0x2000

ACTIVATION_TIMEOUT_S = 45.0
SCAN_TIMEOUT_S = 12.0
POLL_INTERVAL_S = 0.5


def security_from_flags(flags: int, wpa_flags: int, rsn_flags: int) -> WifiSecurity:
    """Classify an access point from its NM flag words."""
    combined = wpa_flags | rsn_flags
    if combined & (SEC_KEY_MGMT_802_1X | SEC_KEY_MGMT_EAP_SUITE_B_192):
        return WifiSecurity.ENTERPRISE
    if combined & SEC_KEY_MGMT_PSK:
        # WPA2/WPA3 transition networks also accept PSK.
        return WifiSecurity.WPA_PSK
    if combined & SEC_KEY_MGMT_SAE:
        return WifiSecurity.SAE
    if combined & (SEC_KEY_MGMT_OWE | SEC_KEY_MGMT_OWE_TM):
        return WifiSecurity.OWE
    if flags & AP_FLAGS_PRIVACY:
        return WifiSecurity.WEP
    return WifiSecurity.OPEN


def decode_ssid(raw: Any) -> str:
    """NM exposes SSIDs as byte arrays; decode leniently for display."""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, list):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw or "")


def device_state_name(state: int) -> str:
    """Collapse NMDeviceState into the four states the UI renders."""
    if state == DEVICE_ACTIVATED:
        return "connected"
    if 40 <= state < DEVICE_ACTIVATED:
        return "connecting"
    if state in (DEVICE_UNMANAGED, DEVICE_UNAVAILABLE) or state < DEVICE_UNMANAGED:
        return "unavailable"
    return "disconnected"


def build_connection_settings(
    ssid: str, security: WifiSecurity, password: Optional[str], hidden: bool
) -> dict:
    """Settings dict (a{sa{sv}}) for AddAndActivateConnection."""
    wireless: dict = {
        "ssid": Variant("ay", ssid.encode("utf-8")),
        "mode": Variant("s", "infrastructure"),
    }
    if hidden:
        wireless["hidden"] = Variant("b", True)
    settings: dict = {
        "connection": {
            "id": Variant("s", ssid),
            "type": Variant("s", "802-11-wireless"),
            "autoconnect": Variant("b", True),
        },
        "802-11-wireless": wireless,
        "ipv4": {"method": Variant("s", "auto")},
        "ipv6": {"method": Variant("s", "auto")},
    }
    if security == WifiSecurity.OWE:
        settings["802-11-wireless-security"] = {"key-mgmt": Variant("s", "owe")}
    elif security in (WifiSecurity.WPA_PSK, WifiSecurity.SAE):
        settings["802-11-wireless-security"] = {
            "key-mgmt": Variant("s", security.value),
            "psk": Variant("s", password or ""),
        }
    return settings


def merge_networks(
    access_points: list[dict], saved_ssids: set[str], active_ssid: Optional[str]
) -> tuple[WifiNetwork, ...]:
    """One entry per SSID (strongest AP wins), hidden SSIDs dropped, sorted for display."""
    best: dict[str, WifiNetwork] = {}
    for ap in access_points:
        ssid = decode_ssid(ap.get("Ssid"))
        if not ssid:
            continue
        network = WifiNetwork(
            ssid=ssid,
            signal=int(ap.get("Strength", 0)),
            security=security_from_flags(
                int(ap.get("Flags", 0)), int(ap.get("WpaFlags", 0)), int(ap.get("RsnFlags", 0))
            ),
            frequency_mhz=int(ap["Frequency"]) if ap.get("Frequency") else None,
            saved=ssid in saved_ssids,
            active=ssid == active_ssid,
        )
        current = best.get(ssid)
        if current is None or network.signal > current.signal:
            best[ssid] = network
    return tuple(
        sorted(best.values(), key=lambda n: (not n.active, not n.saved, -n.signal, n.ssid.lower()))
    )


class NetworkManagerWifi:
    """:class:`~openflight.connectivity.base.WifiBackend` backed by NetworkManager."""

    def __init__(self, client: DBusClient):
        self._client = client
        self._scanning = False

    # -- helpers -------------------------------------------------------------

    async def _get(self, path: str, iface: str, prop: str) -> Any:
        return await self._client.get(NM, path, iface, prop)

    async def _wifi_device(self) -> Optional[str]:
        body = await self._client.call(NM, NM_PATH, IFACE_NM, "GetDevices")
        for path in body[0]:
            if await self._get(path, IFACE_DEVICE, "DeviceType") == DEVICE_TYPE_WIFI:
                return path
        return None

    async def _require_device(self) -> str:
        device = await self._wifi_device()
        if device is None:
            raise ConnectivityError(ErrorCode.NO_ADAPTER, "No Wi-Fi device found")
        return device

    async def _access_points(self, device: str) -> list[tuple[str, dict]]:
        body = await self._client.call(NM, device, IFACE_WIRELESS, "GetAllAccessPoints")
        result = []
        for ap_path in body[0]:
            try:
                result.append((ap_path, await self._client.get_all(NM, ap_path, IFACE_AP)))
            except ConnectivityError as exc:
                if exc.code != ErrorCode.NOT_FOUND:  # AP vanished mid-read: skip it
                    raise
        return result

    async def _saved_connections(self) -> list[tuple[str, str, dict]]:
        """(path, ssid, raw settings) for every saved Wi-Fi profile."""
        body = await self._client.call(NM, NM_SETTINGS_PATH, IFACE_SETTINGS, "ListConnections")
        saved = []
        for path in body[0]:
            try:
                raw = (await self._client.call(NM, path, IFACE_CONNECTION, "GetSettings"))[0]
            except ConnectivityError as exc:
                if exc.code != ErrorCode.NOT_FOUND:
                    raise
                continue
            plain = unwrap(raw)
            if plain.get("connection", {}).get("type") != "802-11-wireless":
                continue
            ssid = decode_ssid(plain.get("802-11-wireless", {}).get("ssid"))
            if ssid:
                saved.append((path, ssid, raw))
        return saved

    # -- WifiBackend ---------------------------------------------------------

    async def status(self) -> WifiStatus:
        """Read device, active AP, IP and nearby networks."""
        enabled = bool(await self._get(NM_PATH, IFACE_NM, "WirelessEnabled"))
        device = await self._wifi_device()
        if device is None:
            return WifiStatus(available=False, reason=ErrorCode.NO_ADAPTER, enabled=enabled)
        props = await self._client.get_all(NM, device, IFACE_DEVICE)
        state = device_state_name(int(props.get("State", 0)))
        ssid = signal = ip4 = None
        active_ap = await self._get(device, IFACE_WIRELESS, "ActiveAccessPoint")
        if active_ap and active_ap != "/" and state in ("connected", "connecting"):
            try:
                ap = await self._client.get_all(NM, active_ap, IFACE_AP)
                ssid = decode_ssid(ap.get("Ssid")) or None
                signal = int(ap.get("Strength", 0))
            except ConnectivityError as exc:
                if exc.code != ErrorCode.NOT_FOUND:
                    raise
        ip4_path = props.get("Ip4Config")
        if state == "connected" and ip4_path and ip4_path != "/":
            try:
                addresses = await self._get(ip4_path, IFACE_IP4, "AddressData")
                if addresses:
                    ip4 = addresses[0].get("address")
            except ConnectivityError:
                ip4 = None
        saved = await self._saved_connections()
        saved_ssids = {entry[1] for entry in saved}
        networks = ()
        if enabled and state != "unavailable":
            networks = merge_networks(
                [ap for _path, ap in await self._access_points(device)],
                saved_ssids,
                ssid if state == "connected" else None,
            )
        return WifiStatus(
            available=True,
            enabled=enabled,
            interface=props.get("Interface"),
            state=state if enabled else "disconnected",
            ssid=ssid,
            signal=signal,
            ip4_address=ip4,
            scanning=self._scanning,
            networks=networks,
            saved_networks=tuple(sorted(saved_ssids, key=str.lower)),
        )

    async def network_status(self) -> NetworkStatus:
        """NM's global state; internet UNKNOWN when NM's own check is off."""
        props = await self._client.get_all(NM, NM_PATH, IFACE_NM)
        local = int(props.get("State", 0)) >= NM_STATE_CONNECTED_LOCAL
        if not local:
            return NetworkStatus(False, InternetState.OFFLINE, "networkmanager")
        check_enabled = props.get("ConnectivityCheckEnabled", False)
        check_available = props.get("ConnectivityCheckAvailable", False)
        if not (check_enabled and check_available):
            # NM reports FULL whenever a default route exists if checking is
            # disabled, which says nothing about the internet.
            return NetworkStatus(True, InternetState.UNKNOWN, "networkmanager")
        internet = CONNECTIVITY_MAP.get(int(props.get("Connectivity", 0)), InternetState.UNKNOWN)
        return NetworkStatus(True, internet, "networkmanager")

    async def set_enabled(self, enabled: bool) -> None:
        """Toggle the Wi-Fi radio (polkit: enable-disable-wifi)."""
        await self._client.set(NM, NM_PATH, IFACE_NM, "WirelessEnabled", Variant("b", enabled))

    async def scan(self) -> None:
        """RequestScan, then wait for LastScan to advance (or time out quietly)."""
        device = await self._require_device()
        self._scanning = True
        try:
            before = await self._last_scan(device)
            await self._client.call(
                NM,
                device,
                IFACE_WIRELESS,
                "RequestScan",
                "a{sv}",
                [{}],
                default_error=ErrorCode.FAILED,
            )
            deadline = time.monotonic() + SCAN_TIMEOUT_S
            while time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL_S)
                if await self._last_scan(device) != before:
                    return
        finally:
            self._scanning = False

    async def _last_scan(self, device: str) -> int:
        try:
            return int(await self._get(device, IFACE_WIRELESS, "LastScan"))
        except ConnectivityError:
            return -1  # NM < 1.12 has no LastScan; the scan loop then just waits.

    async def connect(self, ssid: str, password: Optional[str], hidden: bool) -> None:
        """Join a network, reusing a saved profile when no new password is given."""
        ssid = validate_ssid(ssid)
        device = await self._require_device()
        ap_path = "/"
        if hidden:
            security = WifiSecurity.WPA_PSK if password else WifiSecurity.OPEN
        else:
            match = [
                (path, ap)
                for path, ap in await self._access_points(device)
                if decode_ssid(ap.get("Ssid")) == ssid
            ]
            if not match:
                raise ConnectivityError(ErrorCode.NOT_FOUND, "Network is out of range")
            ap_path, ap = max(match, key=lambda item: int(item[1].get("Strength", 0)))
            security = security_from_flags(
                int(ap.get("Flags", 0)), int(ap.get("WpaFlags", 0)), int(ap.get("RsnFlags", 0))
            )
        saved = [entry for entry in await self._saved_connections() if entry[1] == ssid]

        if saved and not password:
            path = saved[0][0]
            active = (
                await self._client.call(
                    NM, NM_PATH, IFACE_NM, "ActivateConnection", "ooo", [path, device, ap_path]
                )
            )[0]
            await self._wait_for_activation(device, active, created_path=None)
            return

        clean_password = validate_wifi_password(security, password)
        if saved:
            # New password for a saved network: update in place so any custom
            # settings (static IP, metered, ...) survive.
            path, _ssid, raw = saved[0]
            raw.setdefault("802-11-wireless-security", {})
            raw["802-11-wireless-security"]["psk"] = Variant("s", clean_password or "")
            if "key-mgmt" not in raw["802-11-wireless-security"]:
                raw["802-11-wireless-security"]["key-mgmt"] = Variant("s", security.value)
            await self._client.call(NM, path, IFACE_CONNECTION, "Update", "a{sa{sv}}", [raw])
            active = (
                await self._client.call(
                    NM, NM_PATH, IFACE_NM, "ActivateConnection", "ooo", [path, device, ap_path]
                )
            )[0]
            await self._wait_for_activation(device, active, created_path=None)
            return

        settings = build_connection_settings(ssid, security, clean_password, hidden)
        created_path, active = await self._client.call(
            NM,
            NM_PATH,
            IFACE_NM,
            "AddAndActivateConnection",
            "a{sa{sv}}oo",
            [settings, device, ap_path],
        )
        await self._wait_for_activation(device, active, created_path=created_path)

    async def _wait_for_activation(
        self, device: str, active_path: str, created_path: Optional[str]
    ) -> None:
        """Poll the active connection; on failure map the device state reason.

        A profile we just created is deleted again on failure so a mistyped
        password is never saved.
        """
        reasons: list[int] = []
        rule = (
            f"type='signal',sender='{NM}',path='{device}',"
            f"interface='{IFACE_DEVICE}',member='StateChanged'"
        )

        def on_message(message: Any) -> None:
            if (
                getattr(message, "member", None) == "StateChanged"
                and getattr(message, "path", None) == device
                and getattr(message, "interface", None) == IFACE_DEVICE
                and len(message.body) == 3
                and message.body[0] == DEVICE_FAILED
            ):
                reasons.append(int(message.body[2]))

        self._client.add_message_handler(on_message)
        await self._client.add_match(rule)
        try:
            deadline = time.monotonic() + ACTIVATION_TIMEOUT_S
            while True:
                try:
                    state = int(await self._get(active_path, IFACE_ACTIVE, "State"))
                except ConnectivityError as exc:
                    if exc.code != ErrorCode.NOT_FOUND:
                        raise
                    state = ACTIVE_DEACTIVATED  # object removed == activation ended
                if state == ACTIVE_ACTIVATED:
                    return
                if state == ACTIVE_DEACTIVATED:
                    break
                if time.monotonic() >= deadline:
                    await self._deactivate(active_path)
                    await self._delete_quietly(created_path)
                    raise ConnectivityError(ErrorCode.TIMEOUT, "Timed out joining the network")
                await asyncio.sleep(POLL_INTERVAL_S)
        finally:
            self._client.remove_message_handler(on_message)
            await self._client.remove_match(rule)

        if not reasons:
            try:
                state_reason = await self._get(device, IFACE_DEVICE, "StateReason")
                reasons.append(int(state_reason[1]))
            except ConnectivityError:
                pass
        await self._delete_quietly(created_path)
        reason = reasons[-1] if reasons else 0
        if reason in AUTH_FAILURE_REASONS:
            raise ConnectivityError(
                ErrorCode.AUTH_FAILED, f"Authentication failed (reason {reason})"
            )
        if reason == SSID_NOT_FOUND_REASON:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Network is out of range")
        raise ConnectivityError(ErrorCode.FAILED, f"Connection failed (reason {reason})")

    async def _deactivate(self, active_path: str) -> None:
        try:
            await self._client.call(
                NM, NM_PATH, IFACE_NM, "DeactivateConnection", "o", [active_path]
            )
        except ConnectivityError:
            pass

    async def _delete_quietly(self, path: Optional[str]) -> None:
        if not path:
            return
        try:
            await self._client.call(NM, path, IFACE_CONNECTION, "Delete")
        except ConnectivityError as exc:
            logger.warning("[connectivity] could not remove failed Wi-Fi profile: %s", exc.detail)

    async def disconnect(self) -> None:
        """Disconnect the Wi-Fi device; already-disconnected is not an error."""
        device = await self._require_device()
        try:
            await self._client.call(NM, device, IFACE_DEVICE, "Disconnect")
        except ConnectivityError as exc:
            if "NotActive" not in exc.detail:
                raise

    async def forget(self, ssid: str) -> None:
        """Delete every saved profile for ``ssid``."""
        ssid = validate_ssid(ssid)
        matches = [path for path, saved, _raw in await self._saved_connections() if saved == ssid]
        if not matches:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Network is not saved")
        for path in matches:
            await self._client.call(NM, path, IFACE_CONNECTION, "Delete")

    async def close(self) -> None:
        """The service owns the shared bus connection."""
