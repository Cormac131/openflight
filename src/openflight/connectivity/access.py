"""Local-device access policy for system-management endpoints.

OpenFlight has no user accounts: the touchscreen *is* the operator. The web
server listens on all interfaces so phones and TVs can view shots, so system
management (Wi-Fi, Bluetooth, desktop) is restricted to requests that come
from the kiosk browser on the Pi itself:

* the TCP peer is a loopback address,
* the ``Host`` header names a loopback host (blocks DNS rebinding),
* any ``Origin`` header is a loopback origin (blocks cross-site requests from
  a page that happens to be open in the kiosk browser), and
* no proxy forwarding headers are present (a local reverse proxy would make
  every remote client look like loopback).

Hiding buttons is not the control; this check runs on every request.
"""

from __future__ import annotations

import ipaddress
from functools import wraps
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

from flask import jsonify, request

LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
FORBIDDEN_MESSAGE = "System settings are only available on the OpenFlight screen"
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP", "Forwarded")


def is_loopback_address(address: Optional[str]) -> bool:
    """True for 127.0.0.0/8, ::1 and IPv4-mapped loopback."""
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def is_loopback_host(host: Optional[str]) -> bool:
    """True when a Host/Origin hostname (port optional) is loopback."""
    if not host:
        return False
    parsed = urlsplit(f"//{host}")
    try:
        hostname = parsed.hostname
    except ValueError:
        return False
    if not hostname:
        return False
    hostname = hostname.rstrip(".").lower()
    return hostname in LOOPBACK_NAMES or is_loopback_address(hostname)


def is_loopback_origin(origin: Optional[str]) -> bool:
    """Absent Origin is allowed (same-origin GET, curl); ``null`` is not."""
    if origin is None or origin == "":
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return is_loopback_host(parsed.netloc)


def is_local_device_request(remote_addr: Optional[str], headers: Mapping[str, str]) -> bool:
    """Apply every rule in the module docstring."""
    if not is_loopback_address(remote_addr):
        return False
    if any(headers.get(name) for name in PROXY_HEADERS):
        return False
    if not is_loopback_host(headers.get("Host")):
        return False
    return is_loopback_origin(headers.get("Origin"))


def current_request_is_local() -> bool:
    """Evaluate the policy for the active Flask/Socket.IO request."""
    return is_local_device_request(request.remote_addr, request.headers)


def local_device_only(view: Callable) -> Callable:
    """Flask view decorator: 403 unless :func:`current_request_is_local`."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_request_is_local():
            error = {"code": "forbidden", "message": FORBIDDEN_MESSAGE}
            return jsonify({"error": error}), 403
        return view(*args, **kwargs)

    return wrapper
