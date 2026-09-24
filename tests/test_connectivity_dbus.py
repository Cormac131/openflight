"""NetworkManager/BlueZ adapters against fake services on a real private bus."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("dbus_fast")

from connectivity_fakes import (  # noqa: E402
    DEVICE_NEW,
    DEVICE_PAIRED,
    ETH_DEVICE,
    NM,
    WIFI_DEVICE,
    FakeBluez,
    FakeNetworkManager,
    env_has_dbus_daemon,
    private_bus,
)

from openflight.connectivity import bluez as bluez_module  # noqa: E402
from openflight.connectivity.base import PairingBroker  # noqa: E402
from openflight.connectivity.bluez import BluezBluetooth  # noqa: E402
from openflight.connectivity.dbus_client import DBusClient  # noqa: E402
from openflight.connectivity.models import (  # noqa: E402
    ConnectivityError,
    ErrorCode,
    InternetState,
    PairingKind,
    WifiSecurity,
)
from openflight.connectivity.networkmanager import NetworkManagerWifi  # noqa: E402

pytestmark = pytest.mark.skipif(not env_has_dbus_daemon(), reason="needs dbus-daemon")


@pytest.fixture(scope="module")
def bus_address():
    with private_bus() as address:
        yield address


def run_nm(bus_address, scenario):
    """Run ``scenario(fake, wifi)`` with a fresh fake NetworkManager."""

    async def main():
        fake = FakeNetworkManager()
        await fake.start(bus_address)
        client = await DBusClient.connect_system(bus_address)
        try:
            return await scenario(fake, NetworkManagerWifi(client))
        finally:
            client.disconnect()
            fake.stop()

    return asyncio.run(main())


def run_bluez(bus_address, scenario, broker=None):
    """Run ``scenario(fake, bt, broker)`` with a fresh fake BlueZ."""

    async def main():
        fake = FakeBluez()
        await fake.start(bus_address)
        client = await DBusClient.connect_system(bus_address)
        prompts = []
        active_broker = broker or PairingBroker(prompts.append, lambda _id: None, timeout_s=2)
        backend = BluezBluetooth(client, active_broker)
        try:
            return await scenario(fake, backend, active_broker, prompts)
        finally:
            await backend.close()
            client.disconnect()
            fake.stop()

    return asyncio.run(main())


# --- NetworkManager -----------------------------------------------------------


class TestNetworkManagerStatus:
    def test_reports_current_network_signal_ip_and_merged_scan(self, bus_address):
        async def scenario(_fake, wifi):
            return await wifi.status()

        status = run_nm(bus_address, scenario)
        assert status.available and status.enabled
        assert (status.interface, status.state, status.ssid) == ("wlan0", "connected", "Home")
        assert status.signal == 80
        assert status.ip4_address == "192.168.1.40"
        ssids = [n.ssid for n in status.networks]
        assert ssids == ["Home", "Corp", "Cafe"]  # active first, hidden SSID dropped
        home = status.networks[0]
        assert home.active and home.saved and home.signal == 80  # strongest AP wins
        assert home.security == WifiSecurity.WPA_PSK
        assert status.networks[1].security == WifiSecurity.ENTERPRISE
        assert status.networks[2].security == WifiSecurity.OPEN
        assert status.saved_networks == ("Home",)

    def test_missing_wifi_device_is_no_adapter(self, bus_address):
        async def scenario(fake, wifi):
            fake.methods[(NM, "GetDevices")] = lambda _m: ("ao", [[ETH_DEVICE]])
            return await wifi.status()

        status = run_nm(bus_address, scenario)
        assert not status.available and status.reason == ErrorCode.NO_ADAPTER

    def test_network_status_distinguishes_local_link_from_internet(self, bus_address):
        async def scenario(fake, wifi):
            results = [await wifi.network_status()]
            fake.set_nm("Connectivity", 2)
            results.append(await wifi.network_status())
            fake.set_nm("ConnectivityCheckEnabled", False)
            results.append(await wifi.network_status())
            fake.set_nm("State", 20)
            results.append(await wifi.network_status())
            return results

        online, portal, unchecked, down = run_nm(bus_address, scenario)
        assert (online.local_network, online.internet) == (True, InternetState.ONLINE)
        assert (portal.local_network, portal.internet) == (True, InternetState.PORTAL)
        # Disabled NM checks must not be read as "online".
        assert (unchecked.local_network, unchecked.internet) == (True, InternetState.UNKNOWN)
        assert (down.local_network, down.internet) == (False, InternetState.OFFLINE)


class TestNetworkManagerOperations:
    def test_scan_waits_for_last_scan_to_advance(self, bus_address):
        async def scenario(fake, wifi):
            await wifi.scan()
            return fake.prop(WIFI_DEVICE, f"{NM}.Device.Wireless", "LastScan")

        assert run_nm(bus_address, scenario) == 1001

    def test_connect_open_network_creates_profile_without_security(self, bus_address):
        async def scenario(fake, wifi):
            await wifi.connect("Cafe", None, hidden=False)
            return fake

        fake = run_nm(bus_address, scenario)
        assert "Cafe" in fake.saved_ssids()
        cafe = [s for s in fake.saved.values() if s["connection"]["id"].value == "Cafe"][0]
        assert "802-11-wireless-security" not in cafe

    def test_connect_new_secured_network_with_correct_password(self, bus_address):
        async def scenario(fake, wifi):
            fake.add_access_point("Range", 75, secured=True)
            await wifi.connect("Range", FakeNetworkManager.CORRECT_PASSWORD, hidden=False)
            return fake

        fake = run_nm(bus_address, scenario)
        assert "Range" in fake.saved_ssids()

    def test_wrong_password_is_auth_failed_and_profile_is_not_kept(self, bus_address):
        async def scenario(fake, wifi):
            fake.add_access_point("Range", 75, secured=True)
            with pytest.raises(ConnectivityError) as info:
                await wifi.connect("Range", "wrong-password", hidden=False)
            return fake, info.value

        fake, error = run_nm(bus_address, scenario)
        assert error.code == ErrorCode.AUTH_FAILED
        assert "wrong-password" not in error.detail
        assert "Range" not in fake.saved_ssids()

    def test_ssid_not_found_reason_maps_to_not_found(self, bus_address):
        async def scenario(fake, wifi):
            fake.failure_reason = 53
            with pytest.raises(ConnectivityError) as info:
                await wifi.connect("Home", "another-password", hidden=False)
            return info.value

        assert run_nm(bus_address, scenario).code == ErrorCode.NOT_FOUND

    def test_saved_network_reconnects_without_password(self, bus_address):
        async def scenario(fake, wifi):
            await wifi.connect("Home", None, hidden=False)
            return fake.calls

        calls = run_nm(bus_address, scenario)
        members = [member for _p, _i, member in calls]
        assert "ActivateConnection" in members
        assert "AddAndActivateConnection" not in members

    def test_new_password_for_saved_network_updates_in_place(self, bus_address):
        async def scenario(fake, wifi):
            await wifi.connect("Home", FakeNetworkManager.CORRECT_PASSWORD, hidden=False)
            return fake

        fake = run_nm(bus_address, scenario)
        members = [member for _p, _i, member in fake.calls]
        assert "Update" in members and "AddAndActivateConnection" not in members
        assert fake.saved_ssids().count("Home") == 1

    def test_out_of_range_network_is_not_found(self, bus_address):
        async def scenario(_fake, wifi):
            with pytest.raises(ConnectivityError) as info:
                await wifi.connect("Nowhere", "whatever-pass", hidden=False)
            return info.value

        assert run_nm(bus_address, scenario).code == ErrorCode.NOT_FOUND

    def test_hidden_network_connects_without_matching_ap(self, bus_address):
        async def scenario(fake, wifi):
            await wifi.connect("Secret", FakeNetworkManager.CORRECT_PASSWORD, hidden=True)
            return fake

        fake = run_nm(bus_address, scenario)
        secret = [s for s in fake.saved.values() if s["connection"]["id"].value == "Secret"][0]
        assert secret["802-11-wireless"]["hidden"].value is True

    def test_enterprise_network_is_rejected_before_touching_nm(self, bus_address):
        async def scenario(fake, wifi):
            with pytest.raises(ConnectivityError) as info:
                await wifi.connect("Corp", "irrelevant-pw", hidden=False)
            return fake, info.value

        fake, error = run_nm(bus_address, scenario)
        assert error.code == ErrorCode.UNSUPPORTED_SECURITY
        assert "AddAndActivateConnection" not in [m for _p, _i, m in fake.calls]

    def test_short_password_is_invalid_request(self, bus_address):
        async def scenario(_fake, wifi):
            with pytest.raises(ConnectivityError) as info:
                await wifi.connect("Home", "short", hidden=False)
            return info.value

        assert run_nm(bus_address, scenario).code == ErrorCode.INVALID_REQUEST

    def test_disconnect_and_disconnect_again(self, bus_address):
        async def scenario(fake, wifi):
            await wifi.disconnect()
            await wifi.disconnect()  # NotActive is ignored
            return await wifi.status()

        status = run_nm(bus_address, scenario)
        assert status.state == "disconnected" and status.ssid is None

    def test_forget_deletes_saved_profiles(self, bus_address):
        async def scenario(fake, wifi):
            await wifi.forget("Home")
            with pytest.raises(ConnectivityError) as info:
                await wifi.forget("Home")
            return fake, info.value

        fake, error = run_nm(bus_address, scenario)
        assert "Home" not in fake.saved_ssids()
        assert error.code == ErrorCode.NOT_FOUND

    def test_polkit_denial_maps_to_permission_denied(self, bus_address):
        async def scenario(fake, wifi):
            fake.deny.add("AddAndActivateConnection")
            with pytest.raises(ConnectivityError) as info:
                await wifi.connect("Cafe", None, hidden=False)
            return info.value

        assert run_nm(bus_address, scenario).code == ErrorCode.PERMISSION_DENIED

    def test_stopped_networkmanager_is_service_unavailable(self, bus_address):
        async def main():
            client = await DBusClient.connect_system(bus_address)
            try:
                with pytest.raises(ConnectivityError) as info:
                    await NetworkManagerWifi(client).status()
                return info.value
            finally:
                client.disconnect()

        assert asyncio.run(main()).code == ErrorCode.SERVICE_UNAVAILABLE


# --- BlueZ --------------------------------------------------------------------


class TestBluezStatus:
    def test_lists_named_devices_connected_first(self, bus_address):
        async def scenario(_fake, bt, _broker, _prompts):
            return await bt.status()

        status = run_bluez(bus_address, scenario)
        assert status.available and status.powered and not status.discovering
        assert [d.name for d in status.devices] == ["Headset", "Speaker"]  # beacon hidden
        assert status.devices[0].connected and status.devices[0].paired

    def test_no_adapter(self, bus_address):
        async def scenario(fake, bt, _broker, _prompts):
            del fake.objects["/org/bluez/hci0"]
            status = await bt.status()
            with pytest.raises(ConnectivityError) as info:
                await bt.set_powered(True)
            return status, info.value

        status, error = run_bluez(bus_address, scenario)
        assert not status.available and status.reason == ErrorCode.NO_ADAPTER
        assert error.code == ErrorCode.NO_ADAPTER


class TestBluezOperations:
    def test_power_off_and_discovery(self, bus_address):
        async def scenario(fake, bt, _broker, _prompts):
            await bt.set_discovery(True)
            discovering = fake.prop("/org/bluez/hci0", "org.bluez.Adapter1", "Discovering")
            await bt.set_discovery(False)
            await bt.set_discovery(False)  # already stopped: not an error
            await bt.set_powered(False)
            with pytest.raises(ConnectivityError) as info:
                await bt.set_discovery(True)
            return discovering, fake, info.value

        discovering, fake, error = run_bluez(bus_address, scenario)
        assert discovering is True
        assert fake.prop("/org/bluez/hci0", "org.bluez.Adapter1", "Powered") is False
        assert error.code == ErrorCode.BLOCKED

    def test_rfkill_block_without_permission_is_blocked(self, bus_address, monkeypatch):
        monkeypatch.setattr(bluez_module, "unblock_bluetooth_rfkill", lambda: False)

        async def scenario(fake, bt, _broker, _prompts):
            fake.rfkill_blocked = True
            with pytest.raises(ConnectivityError) as info:
                await bt.set_powered(True)
            return info.value

        assert run_bluez(bus_address, scenario).code == ErrorCode.BLOCKED

    def test_rfkill_unblock_then_retry(self, bus_address, monkeypatch):
        def unblock():
            state["fake"].rfkill_blocked = False
            return True

        state = {}
        monkeypatch.setattr(bluez_module, "unblock_bluetooth_rfkill", unblock)

        async def scenario(fake, bt, _broker, _prompts):
            state["fake"] = fake
            fake.rfkill_blocked = True
            await bt.set_powered(True)

        run_bluez(bus_address, scenario)

    def test_pair_with_numeric_comparison_accepted(self, bus_address):
        async def scenario(fake, bt, broker, prompts):
            task = asyncio.ensure_future(bt.pair("aa:bb:cc:00:00:02"))
            while not prompts:
                await asyncio.sleep(0.01)
            broker.respond(prompts[0].request_id, True)
            await task
            return fake, prompts, fake.agent

        fake, prompts, agent = run_bluez(bus_address, scenario)
        prompt = prompts[0]
        assert (prompt.kind, prompt.passkey, prompt.name) == (
            PairingKind.CONFIRM,
            "000042",
            "Speaker",
        )
        device = fake.objects[DEVICE_NEW]["org.bluez.Device1"]
        assert device["Paired"].value and device["Trusted"].value and device["Connected"].value
        assert agent is not None and agent[1] == "/org/openflight/BluetoothAgent"

    def test_pair_rejected_on_kiosk_is_auth_failed(self, bus_address):
        async def scenario(fake, bt, broker, prompts):
            task = asyncio.ensure_future(bt.pair("AA:BB:CC:00:00:02"))
            while not prompts:
                await asyncio.sleep(0.01)
            broker.respond(prompts[0].request_id, False)
            with pytest.raises(ConnectivityError) as info:
                await task
            return fake, info.value

        fake, error = run_bluez(bus_address, scenario)
        assert error.code == ErrorCode.AUTH_FAILED
        assert fake.objects[DEVICE_NEW]["org.bluez.Device1"]["Paired"].value is False

    def test_pair_with_typed_passkey(self, bus_address):
        async def scenario(fake, bt, broker, prompts):
            fake.pair_method = "RequestPasskey"
            task = asyncio.ensure_future(bt.pair("AA:BB:CC:00:00:02"))
            while not prompts:
                await asyncio.sleep(0.01)
            with pytest.raises(ConnectivityError):
                broker.respond(prompts[0].request_id, True, "12ab")  # not digits
            broker.respond(prompts[0].request_id, True, "004321")
            await task
            return fake.agent_reply, prompts[0]

        reply, prompt = run_bluez(bus_address, scenario)
        assert prompt.kind == PairingKind.ENTER_PASSKEY
        assert reply == [4321]

    def test_pair_with_pin(self, bus_address):
        async def scenario(fake, bt, broker, prompts):
            fake.pair_method = "RequestPinCode"
            task = asyncio.ensure_future(bt.pair("AA:BB:CC:00:00:02"))
            while not prompts:
                await asyncio.sleep(0.01)
            broker.respond(prompts[0].request_id, True, "0000")
            await task
            return fake.agent_reply

        assert run_bluez(bus_address, scenario) == ["0000"]

    def test_unanswered_prompt_times_out(self, bus_address):
        async def scenario(_fake, bt, _broker, prompts):
            with pytest.raises(ConnectivityError) as info:
                await bt.pair("AA:BB:CC:00:00:02")
            return prompts, info.value

        prompts, error = run_bluez(bus_address, scenario)
        assert prompts and error.code == ErrorCode.AUTH_FAILED

    def test_already_paired_device_still_trusts_and_connects(self, bus_address):
        async def scenario(fake, bt, _broker, _prompts):
            fake.set_prop(DEVICE_PAIRED, "org.bluez.Device1", "Connected", False)
            await bt.pair("AA:BB:CC:00:00:01")
            return fake

        fake = run_bluez(bus_address, scenario)
        assert fake.prop(DEVICE_PAIRED, "org.bluez.Device1", "Connected") is True

    def test_connect_disconnect_forget(self, bus_address):
        async def scenario(fake, bt, _broker, _prompts):
            await bt.disconnect("AA:BB:CC:00:00:01")
            connected_after_disconnect = fake.prop(DEVICE_PAIRED, "org.bluez.Device1", "Connected")
            await bt.connect("AA:BB:CC:00:00:01")
            await bt.forget("AA:BB:CC:00:00:01")
            with pytest.raises(ConnectivityError) as info:
                await bt.connect("AA:BB:CC:00:00:01")
            return connected_after_disconnect, fake, info.value

        connected, fake, error = run_bluez(bus_address, scenario)
        assert connected is False
        assert DEVICE_PAIRED not in fake.objects
        assert error.code == ErrorCode.NOT_FOUND

    def test_invalid_address_never_reaches_bluez(self, bus_address):
        async def scenario(fake, bt, _broker, _prompts):
            with pytest.raises(ConnectivityError) as info:
                await bt.connect("../../org/bluez")
            return fake.calls, info.value

        calls, error = run_bluez(bus_address, scenario)
        assert error.code == ErrorCode.INVALID_REQUEST
        assert calls == []

    def test_close_unregisters_agent(self, bus_address):
        async def scenario(fake, bt, broker, prompts):
            task = asyncio.ensure_future(bt.pair("AA:BB:CC:00:00:02"))
            while not prompts:
                await asyncio.sleep(0.01)
            broker.respond(prompts[0].request_id, True)
            await task
            await bt.close()
            return fake.agent

        assert run_bluez(bus_address, scenario) is None
