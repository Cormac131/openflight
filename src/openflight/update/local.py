"""Decide whether a socket request came from the kiosk on this device.

Update commands (change channel, check, restart to update) must never be
reachable from another machine on the LAN, nor from a web page of another
origin loaded on the Pi. Both are rejected here: the peer must be loopback
and, when the browser sends an ``Origin``, its host must be loopback too.
"""

import ipaddress
from typing import Optional
from urllib.parse import urlsplit


def is_loopback_address(address: Optional[str]) -> bool:
    """True for 127.0.0.0/8, ``::1`` and IPv4-mapped loopback such as ``::ffff:127.0.0.1``."""
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address.strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def is_local_kiosk_request(remote_addr: Optional[str], origin: Optional[str]) -> bool:
    """Loopback peer, and either no ``Origin`` header or one whose host is loopback."""
    if not is_loopback_address(remote_addr):
        return False
    if not origin:
        return True
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    if not host:
        return False
    return host == "localhost" or is_loopback_address(host)
