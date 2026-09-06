"""Authorization matrix for update commands: only the kiosk on this device may issue them."""

import pytest

from openflight.update.local import is_local_kiosk_request, is_loopback_address


@pytest.mark.parametrize(
    "address,expected",
    [
        ("127.0.0.1", True),
        ("127.0.0.53", True),
        ("::1", True),
        ("[::1]", True),
        ("::ffff:127.0.0.1", True),
        ("192.168.1.20", False),
        ("::ffff:192.168.1.20", False),
        ("10.0.0.1", False),
        ("localhost", False),
        ("", False),
        (None, False),
        ("not-an-ip", False),
    ],
)
def test_loopback_addresses(address, expected):
    assert is_loopback_address(address) is expected


@pytest.mark.parametrize(
    "remote_addr,origin,expected",
    [
        ("127.0.0.1", None, True),
        ("127.0.0.1", "", True),
        ("127.0.0.1", "http://localhost:8080", True),
        ("127.0.0.1", "http://127.0.0.1:8080", True),
        ("127.0.0.1", "http://[::1]:8080", True),
        ("::1", "http://localhost:8080", True),
        ("::ffff:127.0.0.1", "http://localhost:8080", True),
        ("127.0.0.1", "http://openflight.local:8080", False),
        ("127.0.0.1", "http://192.168.1.5:8080", False),
        ("127.0.0.1", "https://evil.example", False),
        ("127.0.0.1", "null", False),
        ("127.0.0.1", "http://", False),
        ("192.168.1.20", None, False),
        ("192.168.1.20", "http://localhost:8080", False),
        ("192.168.1.20", "http://192.168.1.20:8080", False),
        (None, None, False),
    ],
)
def test_local_kiosk_request_matrix(remote_addr, origin, expected):
    assert is_local_kiosk_request(remote_addr, origin) is expected
