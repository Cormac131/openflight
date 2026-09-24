"""In-memory connectivity backends for browser-based development and e2e tests.

Enabled with ``openflight-server --connectivity mock``. Secured mock networks
accept the password ``openflight``; ``Garage Printer`` needs a passkey
confirmation and ``Range Speaker`` asks for a passkey to be typed.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Optional

from .base import PairingBroker
from .models import (
    BluetoothDevice,
    BluetoothStatus,
    ConnectivityError,
    ErrorCode,
    InternetState,
    NetworkStatus,
    PairingKind,
    WifiNetwork,
    WifiSecurity,
    WifiStatus,
    validate_bt_address,
    validate_ssid,
    validate_wifi_password,
)

MOCK_PASSWORD = "openflight"
MOCK_PASSKEY = "123456"


class MockWifi:
    """Fake NetworkManager with a handful of nearby networks."""

    def __init__(self, delay_s: float = 0.6):
        self._delay_s = delay_s
        self._enabled = True
        self._scanning = False
        self._networks = {
            "OpenFlight Range": WifiNetwork("OpenFlight Range", 82, WifiSecurity.WPA_PSK, 5180),
            "Clubhouse Guest": WifiNetwork("Clubhouse Guest", 61, WifiSecurity.OPEN, 2437),
            "Pro Shop": WifiNetwork("Pro Shop", 38, WifiSecurity.SAE, 5745),
            "Members Only": WifiNetwork("Members Only", 22, WifiSecurity.ENTERPRISE, 2412),
        }
        self._saved: set[str] = {"OpenFlight Range"}
        self._active: Optional[str] = "OpenFlight Range"

    async def _pause(self) -> None:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)

    async def status(self) -> WifiStatus:
        """Current fake state."""
        networks = (
            tuple(
                sorted(
                    (
                        replace(n, saved=n.ssid in self._saved, active=n.ssid == self._active)
                        for n in self._networks.values()
                    ),
                    key=lambda n: (not n.active, not n.saved, -n.signal),
                )
            )
            if self._enabled
            else ()
        )
        active = self._networks.get(self._active) if self._active else None
        return WifiStatus(
            available=True,
            enabled=self._enabled,
            interface="wlan0",
            state="connected" if active else "disconnected",
            ssid=active.ssid if active else None,
            signal=active.signal if active else None,
            ip4_address="192.168.4.23" if active else None,
            scanning=self._scanning,
            networks=networks,
            saved_networks=tuple(sorted(self._saved)),
        )

    async def network_status(self) -> NetworkStatus:
        """The guest network is local-only, which exercises the offline badge."""
        if not self._active:
            return NetworkStatus(False, InternetState.OFFLINE, "mock")
        if self._active == "Clubhouse Guest":
            return NetworkStatus(True, InternetState.PORTAL, "mock")
        return NetworkStatus(True, InternetState.ONLINE, "mock")

    async def set_enabled(self, enabled: bool) -> None:
        """Toggle the fake radio."""
        await self._pause()
        self._enabled = enabled
        if not enabled:
            self._active = None

    async def scan(self) -> None:
        """Pretend to scan."""
        self._scanning = True
        try:
            await self._pause()
        finally:
            self._scanning = False

    async def connect(self, ssid: str, password: Optional[str], hidden: bool) -> None:
        """Join a fake network; only ``openflight`` is a correct password."""
        ssid = validate_ssid(ssid)
        network = self._networks.get(ssid)
        if network is None and not hidden:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Network is out of range")
        security = network.security if network else WifiSecurity.WPA_PSK
        if not (ssid in self._saved and not password):
            validate_wifi_password(security, password)
        await self._pause()
        if security.needs_password and password and password != MOCK_PASSWORD:
            raise ConnectivityError(ErrorCode.AUTH_FAILED, "Wrong password")
        if network is None:
            self._networks[ssid] = WifiNetwork(ssid, 55, security)
        self._saved.add(ssid)
        self._active = ssid

    async def disconnect(self) -> None:
        """Drop the fake link."""
        await self._pause()
        self._active = None

    async def forget(self, ssid: str) -> None:
        """Forget a fake saved network."""
        ssid = validate_ssid(ssid)
        if ssid not in self._saved:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Network is not saved")
        await self._pause()
        self._saved.discard(ssid)
        if self._active == ssid:
            self._active = None

    async def close(self) -> None:
        """Nothing to release."""


class MockBluetooth:
    """Fake BlueZ adapter that exercises every pairing prompt style."""

    def __init__(self, broker: PairingBroker, delay_s: float = 0.6):
        self._broker = broker
        self._delay_s = delay_s
        self._powered = True
        self._discovering = False
        self._devices = {
            "AA:BB:CC:00:00:01": BluetoothDevice(
                "AA:BB:CC:00:00:01",
                "Range Headphones",
                "audio-headphones",
                paired=True,
                trusted=True,
                connected=True,
                rssi=-48,
            ),
            "AA:BB:CC:00:00:02": BluetoothDevice(
                "AA:BB:CC:00:00:02",
                "Garage Printer",
                "printer",
                rssi=-60,
            ),
            "AA:BB:CC:00:00:03": BluetoothDevice(
                "AA:BB:CC:00:00:03",
                "Range Speaker",
                "audio-card",
                rssi=-71,
            ),
        }

    async def _pause(self) -> None:
        if self._delay_s:
            await asyncio.sleep(self._delay_s)

    def _get(self, address: str) -> BluetoothDevice:
        address = validate_bt_address(address)
        device = self._devices.get(address)
        if device is None:
            raise ConnectivityError(ErrorCode.NOT_FOUND, "Bluetooth device not found")
        return device

    def _require_powered(self) -> None:
        if not self._powered:
            raise ConnectivityError(ErrorCode.BLOCKED, "Bluetooth is off")

    async def status(self) -> BluetoothStatus:
        """Current fake state."""
        return BluetoothStatus(
            available=True,
            powered=self._powered,
            discovering=self._discovering,
            adapter_name="openflight",
            devices=tuple(self._devices.values()) if self._powered else (),
        )

    async def set_powered(self, powered: bool) -> None:
        """Toggle the fake adapter."""
        await self._pause()
        self._powered = powered
        if not powered:
            self._discovering = False
            for address, device in self._devices.items():
                self._devices[address] = replace(device, connected=False)

    async def set_discovery(self, enabled: bool) -> None:
        """Toggle fake discovery."""
        self._require_powered()
        self._discovering = enabled

    async def pair(self, address: str) -> None:
        """Pair with a prompt that depends on the device."""
        self._require_powered()
        device = self._get(address)
        if device.name == "Range Speaker":
            value = await self._broker.prompt(
                device.address, device.name, PairingKind.ENTER_PASSKEY
            )
            if value != MOCK_PASSKEY:
                raise ConnectivityError(ErrorCode.AUTH_FAILED, "Passkey did not match")
        else:
            await self._broker.prompt(
                device.address, device.name, PairingKind.CONFIRM, MOCK_PASSKEY
            )
        await self._pause()
        self._devices[device.address] = replace(device, paired=True, trusted=True, connected=True)

    async def connect(self, address: str) -> None:
        """Connect a paired fake device."""
        self._require_powered()
        device = self._get(address)
        await self._pause()
        self._devices[device.address] = replace(device, connected=True)

    async def disconnect(self, address: str) -> None:
        """Disconnect a fake device."""
        device = self._get(address)
        await self._pause()
        self._devices[device.address] = replace(device, connected=False)

    async def forget(self, address: str) -> None:
        """Unpair a fake device (it stays nearby)."""
        device = self._get(address)
        await self._pause()
        self._devices[device.address] = replace(
            device, paired=False, trusted=False, connected=False
        )

    async def close(self) -> None:
        """Nothing to release."""
