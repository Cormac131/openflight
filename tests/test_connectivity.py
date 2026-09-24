"""Connectivity: validation, local-device access control, service, and routes."""

from __future__ import annotations

import logging
import os
import struct
import threading
import time
from pathlib import Path

import pytest
from flask import Flask

from openflight.connectivity import bluez as bluez_module
from openflight.connectivity.access import (
    is_local_device_request,
    is_loopback_address,
    is_loopback_host,
    is_loopback_origin,
)
from openflight.connectivity.base import PairingBroker, UnavailableBluetooth, UnavailableWifi
from openflight.connectivity.desktop import DesktopControl
from openflight.connectivity.mock import MOCK_PASSKEY, MOCK_PASSWORD, MockBluetooth, MockWifi
from openflight.connectivity.models import (
    ConnectivityError,
    ErrorCode,
    InternetState,
    NetworkStatus,
    PairingKind,
    WifiSecurity,
    validate_bt_address,
    validate_pairing_value,
    validate_ssid,
    validate_wifi_password,
)
from openflight.connectivity.networkmanager import (
    build_connection_settings,
    decode_ssid,
    device_state_name,
    merge_networks,
    security_from_flags,
)
from openflight.connectivity.routes import ConnectivityRuntime, create_blueprint
from openflight.connectivity.service import ConnectivityService, backend_factory_for

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
SECRET = "hunter2-hunter2"


# --- models / validation --------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("ssid", ["a", "Home Wi-Fi", "x" * 32, "Café"])
    def test_valid_ssids(self, ssid):
        assert validate_ssid(ssid) == ssid

    @pytest.mark.parametrize("ssid", ["", None, 42, "x" * 33, "é" * 17, "bad\x00ssid"])
    def test_invalid_ssids(self, ssid):
        with pytest.raises(ConnectivityError) as info:
            validate_ssid(ssid)
        assert info.value.code == ErrorCode.INVALID_REQUEST

    @pytest.mark.parametrize(
        "password", ["12345678", "x" * 63, "a" * 64, "ABCDEF0123" * 6 + "abcd"]
    )
    def test_valid_psk(self, password):
        assert validate_wifi_password(WifiSecurity.WPA_PSK, password) == password

    @pytest.mark.parametrize(
        "password", [None, "", "1234567", "x" * 65, "g" * 64, "pässwörd1", 12345678]
    )
    def test_invalid_psk_never_echoes_the_value(self, password):
        with pytest.raises(ConnectivityError) as info:
            validate_wifi_password(WifiSecurity.SAE, password)
        assert info.value.code == ErrorCode.INVALID_REQUEST
        if isinstance(password, str) and password:
            assert password not in str(info.value)

    def test_open_and_owe_networks_ignore_password(self):
        assert validate_wifi_password(WifiSecurity.OPEN, "whatever") is None
        assert validate_wifi_password(WifiSecurity.OWE, None) is None

    @pytest.mark.parametrize("security", [WifiSecurity.WEP, WifiSecurity.ENTERPRISE])
    def test_unsupported_security(self, security):
        with pytest.raises(ConnectivityError) as info:
            validate_wifi_password(security, "longenough")
        assert info.value.code == ErrorCode.UNSUPPORTED_SECURITY

    def test_bluetooth_address_normalised(self):
        assert validate_bt_address(" aa:bb:cc:dd:ee:ff ") == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.parametrize("address", ["", None, "AA:BB:CC:DD:EE", "AA-BB-CC-DD-EE-FF", "../x"])
    def test_bluetooth_address_rejected(self, address):
        with pytest.raises(ConnectivityError):
            validate_bt_address(address)

    def test_pairing_values(self):
        assert validate_pairing_value(PairingKind.ENTER_PASSKEY, "012345") == "012345"
        assert validate_pairing_value(PairingKind.ENTER_PIN, "0000") == "0000"
        assert validate_pairing_value(PairingKind.CONFIRM, "ignored") is None
        for kind, value in [
            (PairingKind.ENTER_PASSKEY, "1234567"),
            (PairingKind.ENTER_PASSKEY, "12a"),
            (PairingKind.ENTER_PASSKEY, None),
            (PairingKind.ENTER_PIN, ""),
            (PairingKind.ENTER_PIN, "x" * 17),
            (PairingKind.ENTER_PIN, "12 34"),
        ]:
            with pytest.raises(ConnectivityError):
                validate_pairing_value(kind, value)


class TestNetworkManagerHelpers:
    @pytest.mark.parametrize(
        ("flags", "wpa", "rsn", "expected"),
        [
            (0, 0, 0, WifiSecurity.OPEN),
            (1, 0, 0, WifiSecurity.WEP),
            (1, 0x100, 0, WifiSecurity.WPA_PSK),
            (1, 0, 0x100 | 0x400, WifiSecurity.WPA_PSK),  # WPA2/WPA3 transition
            (1, 0, 0x400, WifiSecurity.SAE),
            (0, 0, 0x800, WifiSecurity.OWE),
            (1, 0, 0x200, WifiSecurity.ENTERPRISE),
            (1, 0, 0x2000 | 0x100, WifiSecurity.ENTERPRISE),
        ],
    )
    def test_security_from_flags(self, flags, wpa, rsn, expected):
        assert security_from_flags(flags, wpa, rsn) == expected

    def test_decode_ssid_is_lenient(self):
        assert decode_ssid(b"Home") == "Home"
        assert decode_ssid([72, 105]) == "Hi"
        assert decode_ssid(b"\xff") == "�"
        assert decode_ssid(None) == ""

    @pytest.mark.parametrize(
        ("state", "name"),
        [
            (0, "unavailable"),
            (20, "unavailable"),
            (30, "disconnected"),
            (50, "connecting"),
            (100, "connected"),
            (110, "disconnected"),
            (120, "disconnected"),
        ],
    )
    def test_device_state_name(self, state, name):
        assert device_state_name(state) == name

    def test_merge_networks_dedupes_and_orders(self):
        aps = [
            {"Ssid": b"B", "Strength": 90},
            {"Ssid": b"A", "Strength": 30},
            {"Ssid": b"A", "Strength": 60},
            {"Ssid": b"", "Strength": 99},
            {"Ssid": b"S", "Strength": 10},
        ]
        networks = merge_networks(aps, saved_ssids={"S"}, active_ssid="A")
        assert [(n.ssid, n.signal) for n in networks] == [("A", 60), ("S", 10), ("B", 90)]

    def test_connection_settings_security_sections(self):
        open_settings = build_connection_settings("Cafe", WifiSecurity.OPEN, None, False)
        assert "802-11-wireless-security" not in open_settings
        assert "hidden" not in open_settings["802-11-wireless"]
        sae = build_connection_settings("Home", WifiSecurity.SAE, "longpassword", True)
        assert sae["802-11-wireless-security"]["key-mgmt"].value == "sae"
        assert sae["802-11-wireless"]["hidden"].value is True
        assert sae["802-11-wireless"]["ssid"].value == b"Home"
        owe = build_connection_settings("Hotel", WifiSecurity.OWE, None, False)
        assert owe["802-11-wireless-security"] == {
            "key-mgmt": owe["802-11-wireless-security"]["key-mgmt"]
        }


class TestRfkill:
    def test_writes_bluetooth_unblock_event(self, tmp_path):
        node = tmp_path / "rfkill"
        node.write_bytes(b"")
        assert bluez_module.unblock_bluetooth_rfkill(str(node)) is True
        assert struct.unpack("IBBBB", node.read_bytes()) == (0, 2, 3, 0, 0)

    def test_unwritable_node_returns_false(self, tmp_path):
        assert bluez_module.unblock_bluetooth_rfkill(str(tmp_path / "missing")) is False


# --- access control ---------------------------------------------------------------


class TestAccessPolicy:
    @pytest.mark.parametrize("address", ["127.0.0.1", "127.8.9.1", "::1", "::ffff:127.0.0.1"])
    def test_loopback_addresses(self, address):
        assert is_loopback_address(address)

    @pytest.mark.parametrize(
        "address",
        [
            "",
            None,
            "192.168.1.9",
            "10.0.0.1",
            "::ffff:10.0.0.1",
            "fe80::1",
            "0.0.0.0",
            "localhost",
            "garbage",
        ],
    )
    def test_non_loopback_addresses(self, address):
        assert not is_loopback_address(address)

    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "localhost:8080",
            "127.0.0.1:8080",
            "[::1]:8080",
            "LOCALHOST.",
            "localhost:5173",
        ],
    )
    def test_loopback_hosts(self, host):
        assert is_loopback_host(host)

    @pytest.mark.parametrize(
        "host",
        [
            "",
            None,
            "openflight.local:8080",
            "192.168.1.5",
            "evil.example",
            "localhost.evil.example",
            "127.0.0.1.nip.io",
        ],
    )
    def test_non_loopback_hosts(self, host):
        assert not is_loopback_host(host)

    def test_origins(self):
        assert is_loopback_origin(None)
        assert is_loopback_origin("http://localhost:5173")
        assert is_loopback_origin("http://127.0.0.1:8080")
        assert not is_loopback_origin("null")
        assert not is_loopback_origin("https://evil.example")
        assert not is_loopback_origin("file://")
        assert not is_loopback_origin("http://192.168.1.10:8080")

    def test_full_policy(self):
        ok = {"Host": "localhost:8080", "Origin": "http://localhost:8080"}
        assert is_local_device_request("127.0.0.1", ok)
        assert not is_local_device_request("192.168.1.20", ok)
        # DNS rebinding: loopback peer but attacker hostname.
        assert not is_local_device_request("127.0.0.1", {"Host": "evil.example:8080"})
        # Cross-site page in the kiosk browser.
        assert not is_local_device_request(
            "127.0.0.1", {"Host": "localhost:8080", "Origin": "https://evil.example"}
        )
        # A reverse proxy on the Pi forwards remote clients from loopback.
        assert not is_local_device_request(
            "127.0.0.1", {"Host": "localhost:8080", "X-Forwarded-For": "203.0.113.9"}
        )
        assert not is_local_device_request("127.0.0.1", {"Host": "localhost", "Forwarded": "for=x"})


# --- service ------------------------------------------------------------------------


def mock_factory(wifi_delay=0.0, bt_delay=0.0):
    async def create(broker):
        return MockWifi(delay_s=wifi_delay), MockBluetooth(broker, delay_s=bt_delay)

    return create


class Recorder:
    """Thread-safe event sink with a wait helper."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self._cond = threading.Condition()

    def __call__(self, event: str, payload: dict) -> None:
        with self._cond:
            self.events.append((event, payload))
            self._cond.notify_all()

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for event, payload in self.events:
                    if predicate(event, payload):
                        return payload
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"event not seen; got {[e for e, _ in self.events]}")
                self._cond.wait(remaining)

    def finished(self, op_id, timeout=5.0):
        return self.wait_for(
            lambda e, p: (
                e == "connectivity_operation" and p["id"] == op_id and p["state"] != "pending"
            ),
            timeout,
        )


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def service(recorder):
    svc = ConnectivityService(
        recorder,
        backend_factory=mock_factory(),
        refresh_interval_s=60,
        internet_probe=lambda: _async_value(True),
        discovery_duration_s=0.2,
    )
    svc.start()
    yield svc
    svc.stop()


async def _async_value(value):
    return value


class TestServiceOperations:
    def test_snapshot_shape(self, service):
        snapshot = service.snapshot(refresh=True)
        assert set(snapshot) == {"wifi", "bluetooth", "network", "operations", "pairing"}
        assert snapshot["wifi"]["ssid"] == "OpenFlight Range"
        assert snapshot["network"] == {
            "local_network": True,
            "internet": "online",
            "source": "mock",
        }

    def test_connect_success_emits_pending_then_succeeded_then_status(self, service, recorder):
        op = service.start_operation("wifi_connect", ssid="Pro Shop", password=MOCK_PASSWORD)
        assert op["state"] == "pending" and op["target"] == "Pro Shop"
        done = recorder.finished(op["id"])
        assert done["state"] == "succeeded" and done["error"] is None
        status = recorder.wait_for(
            lambda e, p: e == "connectivity_status" and p["wifi"]["ssid"] == "Pro Shop"
        )
        assert "Pro Shop" in status["wifi"]["saved_networks"]

    def test_wrong_password_fails_with_auth_failed(self, service, recorder):
        op = service.start_operation("wifi_connect", ssid="Pro Shop", password="wrong-password")
        done = recorder.finished(op["id"])
        assert done["error"]["code"] == "auth_failed"

    def test_operation_payloads_never_contain_password(self, service, recorder, caplog):
        caplog.set_level(logging.DEBUG)
        op = service.start_operation("wifi_connect", ssid="Pro Shop", password=SECRET)
        recorder.finished(op["id"])
        assert SECRET not in repr(recorder.events)
        assert SECRET not in caplog.text

    def test_same_group_is_busy_other_group_is_not(self, recorder):
        svc = ConnectivityService(recorder, backend_factory=mock_factory(wifi_delay=0.5))
        svc.start()
        try:
            first = svc.start_operation("wifi_scan")
            with pytest.raises(ConnectivityError) as info:
                svc.start_operation("wifi_disconnect")
            assert info.value.code == ErrorCode.BUSY
            bt = svc.start_operation("bluetooth_connect", address="AA:BB:CC:00:00:01")
            assert bt["state"] == "pending"
            assert svc.snapshot(refresh=True)["operations"][0]["kind"] == "wifi_scan"
            recorder.finished(first["id"])
            svc.start_operation("wifi_disconnect")  # free again
        finally:
            svc.stop()

    @pytest.mark.parametrize(
        ("kind", "params"),
        [
            ("nope", {}),
            ("wifi_connect", {"ssid": ""}),
            ("wifi_connect", {"ssid": "x", "password": 5}),
            ("wifi_connect", {"ssid": "x", "password": "p" * 65}),
            ("wifi_connect", {"ssid": "x", "hidden": "yes"}),
            ("wifi_forget", {}),
            ("wifi_enable", {"enabled": "true"}),
            ("bluetooth_power", {}),
            ("bluetooth_pair", {"address": "nope"}),
        ],
    )
    def test_invalid_requests_rejected_synchronously(self, service, recorder, kind, params):
        with pytest.raises(ConnectivityError) as info:
            service.start_operation(kind, **params)
        assert info.value.code == ErrorCode.INVALID_REQUEST
        assert not [e for e, _ in recorder.events if e == "connectivity_operation"]

    def test_backend_crash_reports_failed_and_frees_group(self, service, recorder):
        async def boom():
            raise RuntimeError("bug")

        service.wifi.scan = boom
        op = service.start_operation("wifi_scan")
        assert recorder.finished(op["id"])["error"]["code"] == "failed"
        service.start_operation("wifi_disconnect")

    def test_discovery_auto_stops(self, service, recorder):
        service.start_operation("bluetooth_discovery", enabled=True)
        recorder.wait_for(lambda e, p: e == "connectivity_status" and p["bluetooth"]["discovering"])
        recorder.wait_for(
            lambda e, p: (
                e == "connectivity_status"
                and not p["bluetooth"]["discovering"]
                and any(
                    ev == "connectivity_status" and pl["bluetooth"]["discovering"]
                    for ev, pl in recorder.events
                )
            ),
            timeout=3,
        )


class TestServicePairing:
    def _pair(self, service, recorder, address):
        op = service.start_operation("bluetooth_pair", address=address)
        prompt = recorder.wait_for(lambda e, p: e == "bluetooth_pairing_request")
        return op, prompt

    def test_confirm_flow(self, service, recorder):
        op, prompt = self._pair(service, recorder, "AA:BB:CC:00:00:02")
        assert prompt["kind"] == "confirm" and prompt["passkey"] == MOCK_PASSKEY
        assert service.snapshot(refresh=True)["pairing"][0]["id"] == prompt["id"]
        service.respond_pairing(prompt["id"], True, None)
        assert recorder.finished(op["id"])["state"] == "succeeded"
        recorder.wait_for(lambda e, p: e == "bluetooth_pairing_closed" and p["id"] == prompt["id"])

    def test_reject_flow(self, service, recorder):
        op, prompt = self._pair(service, recorder, "AA:BB:CC:00:00:02")
        service.respond_pairing(prompt["id"], False, None)
        assert recorder.finished(op["id"])["error"]["code"] == "cancelled"

    def test_passkey_entry_validates_value(self, service, recorder):
        op, prompt = self._pair(service, recorder, "AA:BB:CC:00:00:03")
        assert prompt["kind"] == "enter_passkey"
        with pytest.raises(ConnectivityError):
            service.respond_pairing(prompt["id"], True, "abc")
        service.respond_pairing(prompt["id"], True, MOCK_PASSKEY)
        assert recorder.finished(op["id"])["state"] == "succeeded"

    def test_unknown_request_and_bad_types(self, service):
        with pytest.raises(ConnectivityError) as info:
            service.respond_pairing("pair-999", True, None)
        assert info.value.code == ErrorCode.NOT_FOUND
        with pytest.raises(ConnectivityError):
            service.respond_pairing("pair-1", "yes", None)
        with pytest.raises(ConnectivityError):
            service.respond_pairing("pair-1", True, 123456)

    def test_broker_timeout_and_cancel(self):
        import asyncio

        closed = []

        async def scenario():
            broker = PairingBroker(lambda _r: None, closed.append, timeout_s=0.05)
            with pytest.raises(ConnectivityError) as timeout:
                await broker.prompt("AA:BB:CC:00:00:02", "X", PairingKind.AUTHORIZE)
            task = asyncio.ensure_future(
                broker.prompt("AA:BB:CC:00:00:02", "X", PairingKind.CONFIRM)
            )
            await asyncio.sleep(0)
            broker.cancel_all()
            with pytest.raises(ConnectivityError) as cancelled:
                await task
            broker.show("AA:BB:CC:00:00:02", "X", PairingKind.DISPLAY_PASSKEY, "000001")
            broker.close_for("AA:BB:CC:00:00:02")
            return timeout.value, cancelled.value, broker.pending()

        timeout, cancelled, pending = asyncio.run(scenario())
        assert timeout.code == ErrorCode.TIMEOUT
        assert cancelled.code == ErrorCode.CANCELLED
        assert pending == [] and len(closed) == 3


class TestServiceDegradation:
    def test_unsupported_platform(self, recorder, monkeypatch):
        monkeypatch.setattr("sys.platform", "darwin")
        svc = ConnectivityService(
            recorder,
            backend_factory=backend_factory_for("auto"),
            internet_probe=lambda: _async_value(False),
        )
        svc.start()
        try:
            snapshot = svc.snapshot(refresh=True)
            assert snapshot["wifi"]["available"] is False
            assert snapshot["wifi"]["reason"] == "unsupported_platform"
            assert snapshot["bluetooth"]["reason"] == "unsupported_platform"
            assert snapshot["network"]["source"] == "probe"
            op = svc.start_operation("wifi_scan")
            assert recorder.finished(op["id"])["error"]["code"] == "unsupported_platform"
        finally:
            svc.stop()

    def test_off_mode_reports_disabled_and_ops_fail_cleanly(self, recorder):
        svc = ConnectivityService(
            recorder,
            backend_factory=backend_factory_for("off"),
            internet_probe=lambda: _async_value(False),
        )
        svc.start()
        try:
            assert svc.snapshot(refresh=True)["wifi"]["reason"] == "disabled"
            op = svc.start_operation("wifi_scan")
            assert recorder.finished(op["id"])["error"]["code"] == "disabled"
        finally:
            svc.stop()

    def test_factory_exception_degrades_to_unavailable(self, recorder):
        async def broken(_broker):
            raise OSError("no bus")

        svc = ConnectivityService(
            recorder, backend_factory=broken, internet_probe=lambda: _async_value(False)
        )
        svc.start()
        try:
            snapshot = svc.snapshot(refresh=True)
            assert snapshot["wifi"]["reason"] == "service_unavailable"
            assert snapshot["bluetooth"]["reason"] == "service_unavailable"
        finally:
            svc.stop()

    def test_stopped_service_returns_placeholder_and_refuses_ops(self, recorder):
        svc = ConnectivityService(recorder, backend_factory=mock_factory())
        assert svc.snapshot()["wifi"]["available"] is False
        with pytest.raises(ConnectivityError) as info:
            svc.start_operation("wifi_scan")
        assert info.value.code == ErrorCode.SERVICE_UNAVAILABLE
        # A refused op must not leave the Wi-Fi group stuck busy.
        svc.start()
        try:
            svc.start_operation("wifi_scan")
        finally:
            svc.stop()


class TestInternetFallback:
    def _service(self, recorder, wifi_network_status, probe_result, calls):
        async def factory(broker):
            wifi = MockWifi(delay_s=0)

            async def network_status():
                return wifi_network_status

            wifi.network_status = network_status
            return wifi, UnavailableBluetooth(ErrorCode.NO_ADAPTER)

        async def probe():
            calls.append(1)
            return probe_result

        return ConnectivityService(recorder, backend_factory=factory, internet_probe=probe)

    @pytest.mark.parametrize(
        ("nm_status", "probe", "expected", "probed"),
        [
            (
                NetworkStatus(True, InternetState.ONLINE, "networkmanager"),
                False,
                (True, "online", "networkmanager"),
                False,
            ),
            (
                NetworkStatus(True, InternetState.PORTAL, "networkmanager"),
                True,
                (True, "portal", "networkmanager"),
                False,
            ),
            (
                NetworkStatus(False, InternetState.OFFLINE, "networkmanager"),
                True,
                (False, "offline", "networkmanager"),
                False,
            ),
            (
                NetworkStatus(True, InternetState.UNKNOWN, "networkmanager"),
                True,
                (True, "online", "probe"),
                True,
            ),
            (
                NetworkStatus(True, InternetState.UNKNOWN, "networkmanager"),
                False,
                (True, "limited", "probe"),
                True,
            ),
            (NetworkStatus(False, InternetState.UNKNOWN), True, (True, "online", "probe"), True),
            (NetworkStatus(False, InternetState.UNKNOWN), False, (False, "unknown", "probe"), True),
        ],
    )
    def test_local_vs_internet(self, recorder, nm_status, probe, expected, probed):
        calls = []
        svc = self._service(recorder, nm_status, probe, calls)
        svc.start()
        try:
            network = svc.snapshot(refresh=True)["network"]
            svc.snapshot(refresh=True)
        finally:
            svc.stop()
        assert (network["local_network"], network["internet"], network["source"]) == expected
        # Probe results are cached, so repeated refreshes probe at most once.
        assert len(calls) == (1 if probed else 0)


# --- routes ----------------------------------------------------------------------


@pytest.fixture
def desktop_dir(tmp_path):
    control = tmp_path / "kiosk"
    control.mkdir()
    (control / "kiosk.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (control / "mode").write_text("kiosk\n", encoding="utf-8")
    return control


@pytest.fixture
def client(service, desktop_dir):
    app = Flask("connectivity-test")
    app.register_blueprint(
        create_blueprint(ConnectivityRuntime.for_service(service, DesktopControl(desktop_dir)))
    )
    return app.test_client()


ALL_ROUTES = [
    ("get", "/api/system/connectivity", None),
    ("post", "/api/system/wifi/scan", {}),
    ("post", "/api/system/wifi/enabled", {"enabled": True}),
    ("post", "/api/system/wifi/connect", {"ssid": "Pro Shop", "password": MOCK_PASSWORD}),
    ("post", "/api/system/wifi/disconnect", {}),
    ("post", "/api/system/wifi/forget", {"ssid": "OpenFlight Range"}),
    ("post", "/api/system/bluetooth/power", {"enabled": True}),
    ("post", "/api/system/bluetooth/discovery", {"enabled": False}),
    ("post", "/api/system/bluetooth/devices/AA:BB:CC:00:00:01/disconnect", {}),
    ("post", "/api/system/bluetooth/pairing/pair-1", {"accept": False}),
    ("post", "/api/system/desktop/show", {}),
]


class TestRouteAccessControl:
    def test_every_system_route_is_covered(self, client):
        rules = {
            rule.rule
            for rule in client.application.url_map.iter_rules()
            if rule.rule.startswith("/api/system")
        }
        assert len(rules) == len(ALL_ROUTES)

    @pytest.mark.parametrize(("method", "url", "body"), ALL_ROUTES)
    @pytest.mark.parametrize(
        "environ",
        [
            {"REMOTE_ADDR": "192.168.1.50"},
            {"REMOTE_ADDR": "127.0.0.1", "base_url": "http://attacker.example:8080"},
            {"REMOTE_ADDR": "127.0.0.1", "HTTP_ORIGIN": "https://attacker.example"},
            {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "203.0.113.4"},
        ],
    )
    def test_non_local_requests_are_forbidden(
        self, client, desktop_dir, method, url, body, environ
    ):
        environ = dict(environ)
        base_url = environ.pop("base_url", "http://localhost:8080")
        response = getattr(client, method)(url, json=body, environ_base=environ, base_url=base_url)
        assert response.status_code == 403
        assert response.json["error"]["code"] == "forbidden"
        assert not (desktop_dir / "show-desktop").exists()

    def test_local_request_allowed(self, client):
        response = client.get(
            "/api/system/connectivity",
            environ_base=LOCAL,
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.status_code == 200
        assert response.json["local"] is True
        assert response.json["desktop"] == {"available": True, "reason": None, "mode": "kiosk"}


class TestRoutes:
    def test_connect_returns_202_and_completes(self, client, recorder):
        response = client.post(
            "/api/system/wifi/connect",
            json={"ssid": "Clubhouse Guest"},
            environ_base=LOCAL,
        )
        assert response.status_code == 202
        op = response.json["operation"]
        assert recorder.finished(op["id"])["state"] == "succeeded"
        network = client.get("/api/system/connectivity?refresh=1", environ_base=LOCAL).json[
            "network"
        ]
        assert network == {"local_network": True, "internet": "portal", "source": "mock"}

    def test_non_json_body_is_400(self, client):
        response = client.post(
            "/api/system/wifi/connect",
            data="ssid=x",
            environ_base=LOCAL,
            content_type="application/x-www-form-urlencoded",
        )
        assert response.status_code == 400
        assert response.json["error"]["code"] == "invalid_request"
        response = client.post("/api/system/wifi/forget", json=["x"], environ_base=LOCAL)
        assert response.status_code == 400

    def test_unknown_bluetooth_action_is_404(self, client):
        response = client.post(
            "/api/system/bluetooth/devices/AA:BB:CC:00:00:01/explode", json={}, environ_base=LOCAL
        )
        assert response.status_code == 404

    def test_busy_is_409(self, recorder, desktop_dir):
        svc = ConnectivityService(recorder, backend_factory=mock_factory(wifi_delay=0.5))
        svc.start()
        try:
            app = Flask("busy")
            app.register_blueprint(
                create_blueprint(ConnectivityRuntime.for_service(svc, DesktopControl(desktop_dir)))
            )
            c = app.test_client()
            assert c.post("/api/system/wifi/scan", json={}, environ_base=LOCAL).status_code == 202
            response = c.post("/api/system/wifi/scan", json={}, environ_base=LOCAL)
            assert response.status_code == 409
            assert response.json["error"]["code"] == "busy"
        finally:
            svc.stop()

    def test_pairing_response_errors(self, client):
        response = client.post(
            "/api/system/bluetooth/pairing/pair-404", json={"accept": True}, environ_base=LOCAL
        )
        assert response.status_code == 404

    def test_password_not_logged_by_route(self, client, recorder, caplog):
        caplog.set_level(logging.DEBUG)
        response = client.post(
            "/api/system/wifi/connect",
            json={"ssid": "Pro Shop", "password": SECRET},
            environ_base=LOCAL,
        )
        recorder.finished(response.json["operation"]["id"])
        assert SECRET not in caplog.text
        assert SECRET not in response.get_data(as_text=True)

    def test_show_desktop_writes_request(self, client, desktop_dir):
        response = client.post("/api/system/desktop/show", json={}, environ_base=LOCAL)
        assert response.status_code == 202
        assert (desktop_dir / "show-desktop").exists()

    def test_show_desktop_unavailable_is_503(self, service):
        app = Flask("no-desktop")
        app.register_blueprint(
            create_blueprint(ConnectivityRuntime.for_service(service, DesktopControl(None)))
        )
        response = app.test_client().post("/api/system/desktop/show", json={}, environ_base=LOCAL)
        assert response.status_code == 503


# --- desktop control ----------------------------------------------------------------


class TestDesktopControl:
    def test_not_kiosk_without_control_dir(self):
        assert DesktopControl(None).status() == {
            "available": False,
            "reason": "not_kiosk",
            "mode": None,
        }
        assert DesktopControl(None).request_show_desktop() is False

    def test_missing_or_dead_supervisor(self, tmp_path):
        control = DesktopControl(tmp_path)
        assert control.status()["reason"] == "no_desktop_session"
        (tmp_path / "kiosk.pid").write_text("not-a-pid", encoding="utf-8")
        assert control.status()["available"] is False
        (tmp_path / "kiosk.pid").write_text("999999999", encoding="utf-8")
        assert control.status()["available"] is False
        assert control.request_show_desktop() is False
        assert not (tmp_path / "show-desktop").exists()

    def test_live_supervisor_and_mode(self, desktop_dir):
        control = DesktopControl(desktop_dir)
        (desktop_dir / "mode").write_text("desktop\n", encoding="utf-8")
        assert control.status() == {"available": True, "reason": None, "mode": "desktop"}
        assert control.request_show_desktop() is True
        assert [p.name for p in Path(desktop_dir).iterdir() if p.name.startswith(".")] == []

    def test_from_environment(self, monkeypatch, desktop_dir):
        monkeypatch.setenv("OPENFLIGHT_KIOSK_CONTROL_DIR", str(desktop_dir))
        assert DesktopControl.from_environment().status()["available"] is True
        monkeypatch.delenv("OPENFLIGHT_KIOSK_CONTROL_DIR")
        assert DesktopControl.from_environment().status()["available"] is False


class TestUnavailableBackends:
    def test_unavailable_backends_report_and_raise(self):
        import asyncio

        async def scenario():
            wifi = UnavailableWifi(ErrorCode.NO_ADAPTER)
            bt = UnavailableBluetooth(ErrorCode.SERVICE_UNAVAILABLE, "bluetoothd down")
            status = (await wifi.status(), await bt.status())
            errors = []
            for call in (wifi.scan(), wifi.connect("x", None, False), bt.pair("AA:BB:CC:DD:EE:FF")):
                with pytest.raises(ConnectivityError) as info:
                    await call
                errors.append(info.value.code)
            return status, errors

        (wifi_status, bt_status), errors = asyncio.run(scenario())
        assert wifi_status.reason == ErrorCode.NO_ADAPTER
        assert bt_status.reason == ErrorCode.SERVICE_UNAVAILABLE
        assert errors == [ErrorCode.NO_ADAPTER, ErrorCode.NO_ADAPTER, ErrorCode.SERVICE_UNAVAILABLE]


# --- server wiring ------------------------------------------------------------------


@pytest.fixture
def server_runtime(recorder):
    """Point the server's runtime at the mock backend; restore afterwards."""
    from openflight import server as server_module

    runtime = server_module.connectivity_runtime
    previous = (runtime._factory, runtime.desktop)  # pylint: disable=protected-access
    started = []

    def factory():
        svc = ConnectivityService(recorder, backend_factory=mock_factory())
        started.append(svc)
        return svc

    runtime.configure(factory, DesktopControl(None))
    yield server_module, started
    runtime.stop()
    runtime._factory, runtime.desktop = previous  # pylint: disable=protected-access


def socket_client(server_module, remote_addr="127.0.0.1", headers=None):
    http = server_module.app.test_client()
    http.environ_base["REMOTE_ADDR"] = remote_addr
    return server_module.socketio.test_client(
        server_module.app, headers=headers, flask_test_client=http
    )


class TestServerWiring:
    def test_connectivity_events_reach_only_local_sockets(self, server_runtime):
        from openflight.connectivity.routes import LOCAL_ROOM

        server_module, _started = server_runtime
        local = socket_client(server_module)
        remote = socket_client(server_module, "192.168.1.8")
        cross_site = socket_client(server_module, headers={"Origin": "https://attacker.example"})
        try:
            for client in (local, remote, cross_site):
                client.get_received()
            server_module.socketio.emit("connectivity_status", {"probe": 1}, to=LOCAL_ROOM)
            assert [m["name"] for m in local.get_received()] == ["connectivity_status"]
            assert remote.get_received() == []
            assert cross_site.get_received() == []
        finally:
            for client in (local, remote, cross_site):
                client.disconnect()

    def test_only_a_local_socket_starts_the_service(self, server_runtime):
        server_module, started = server_runtime
        remote = socket_client(server_module, "192.168.1.8")
        assert started == []
        local = socket_client(server_module)
        assert len(started) == 1
        remote.disconnect()
        local.disconnect()

    def test_routes_are_mounted_once_and_the_cli_configures_the_mode(self):
        import inspect

        from openflight import server as server_module

        rules = [r.rule for r in server_module.app.url_map.iter_rules()]
        assert rules.count("/api/system/connectivity") == 1
        source = inspect.getsource(server_module.main)
        assert '"--connectivity"' in source
        assert "connectivity_runtime.configure_mode(" in source


class TestRuntime:
    def test_service_is_built_lazily_once_and_replaced_on_reconfigure(self, recorder):
        built = []

        def factory():
            svc = ConnectivityService(recorder, backend_factory=mock_factory())
            built.append(svc)
            return svc

        runtime = ConnectivityRuntime()
        runtime.configure(factory, DesktopControl(None))
        assert built == []
        first = runtime.service()
        assert runtime.service() is first and len(built) == 1
        runtime.configure(factory, DesktopControl(None))  # stops the old one
        assert first._loop is None  # pylint: disable=protected-access
        second = runtime.service()
        assert second is not first
        runtime.stop()
        runtime.stop()  # idempotent

    def test_unconfigured_runtime_is_disabled(self):
        runtime = ConnectivityRuntime()
        with pytest.raises(ConnectivityError) as info:
            runtime.service()
        assert info.value.code == ErrorCode.DISABLED
        runtime.ensure_started()  # never raises
        app = Flask("unconfigured")
        app.register_blueprint(create_blueprint(runtime))
        response = app.test_client().get("/api/system/connectivity", environ_base=LOCAL)
        assert response.status_code == 503
        assert response.json["error"]["code"] == "disabled"

    def test_join_local_room_outside_a_request_is_a_no_op(self):
        from openflight.connectivity.routes import join_local_room

        assert join_local_room() is False


class TestProbe:
    def test_probe_returns_on_first_success_and_false_when_all_fail(self):
        import asyncio
        import socket

        from openflight.connectivity.service import probe_internet

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        try:
            started = time.monotonic()
            # 192.0.2.1 (TEST-NET-1) never answers; the local listener does.
            ok = asyncio.run(probe_internet((("192.0.2.1", 9), ("127.0.0.1", port)), timeout_s=3.0))
            elapsed = time.monotonic() - started
            refused = asyncio.run(probe_internet((("127.0.0.1", closed_port),), timeout_s=1.0))
        finally:
            listener.close()
        assert ok is True and elapsed < 1.5
        assert refused is False
