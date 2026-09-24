"""Plain data types, error codes, and input validation for connectivity management.

Everything here is backend-agnostic: the NetworkManager/BlueZ adapters and the
mock backend all speak these types, and the HTTP layer serialises them with
``to_dict()``. Nothing in this module may ever hold on to a Wi-Fi password
beyond the lifetime of a single request.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    """Stable, UI-translatable failure reasons."""

    UNSUPPORTED_PLATFORM = "unsupported_platform"
    DISABLED = "disabled"
    SERVICE_UNAVAILABLE = "service_unavailable"
    NO_ADAPTER = "no_adapter"
    PERMISSION_DENIED = "permission_denied"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_SECURITY = "unsupported_security"
    AUTH_FAILED = "auth_failed"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    BUSY = "busy"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ConnectivityError(Exception):
    """A user-facing failure with a stable code and a short English detail.

    ``detail`` is for logs and the debug tooltip. The UI shows a translated
    message chosen by ``code``. Never put secrets in ``detail``.
    """

    def __init__(self, code: ErrorCode, detail: str = ""):
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail or code.value

    def to_dict(self) -> dict:
        """Serialise for API responses and operation events."""
        return {"code": self.code.value, "message": self.detail}


# --- Wi-Fi -------------------------------------------------------------------


class WifiSecurity(str, Enum):
    """Security families the kiosk can (or explicitly cannot) join."""

    OPEN = "open"
    WPA_PSK = "wpa-psk"
    SAE = "sae"
    OWE = "owe"
    WEP = "wep"
    ENTERPRISE = "enterprise"

    @property
    def needs_password(self) -> bool:
        """True when joining requires an on-screen password."""
        return self in (WifiSecurity.WPA_PSK, WifiSecurity.SAE, WifiSecurity.WEP)

    @property
    def supported(self) -> bool:
        """WEP and 802.1X need credentials the kiosk keyboard does not collect."""
        return self not in (WifiSecurity.WEP, WifiSecurity.ENTERPRISE)


@dataclass(frozen=True)
class WifiNetwork:
    """One SSID as seen by the scanner, merged with saved-profile state."""

    ssid: str
    signal: int  # 0-100
    security: WifiSecurity
    frequency_mhz: Optional[int] = None
    saved: bool = False
    active: bool = False

    def to_dict(self) -> dict:
        """Serialise for the UI."""
        data = asdict(self)
        data["security"] = self.security.value
        data["supported"] = self.security.supported
        data["needs_password"] = self.security.needs_password
        return data


@dataclass(frozen=True)
class WifiStatus:
    """Current Wi-Fi state."""

    available: bool
    reason: Optional[ErrorCode] = None
    enabled: bool = False
    interface: Optional[str] = None
    state: str = "unavailable"  # unavailable|disconnected|connecting|connected
    ssid: Optional[str] = None
    signal: Optional[int] = None
    ip4_address: Optional[str] = None
    scanning: bool = False
    networks: tuple[WifiNetwork, ...] = ()
    saved_networks: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """Serialise for the UI."""
        return {
            "available": self.available,
            "reason": self.reason.value if self.reason else None,
            "enabled": self.enabled,
            "interface": self.interface,
            "state": self.state,
            "ssid": self.ssid,
            "signal": self.signal,
            "ip4_address": self.ip4_address,
            "scanning": self.scanning,
            "networks": [network.to_dict() for network in self.networks],
            "saved_networks": list(self.saved_networks),
        }


class InternetState(str, Enum):
    """Internet reachability, deliberately separate from the local link."""

    ONLINE = "online"
    PORTAL = "portal"  # Captive portal: local link works, sign-in needed.
    LIMITED = "limited"  # Local network only.
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NetworkStatus:
    """Link-layer vs internet availability across all interfaces."""

    local_network: bool
    internet: InternetState
    source: str = "none"  # networkmanager|probe|mock|none

    def to_dict(self) -> dict:
        """Serialise for the UI."""
        return {
            "local_network": self.local_network,
            "internet": self.internet.value,
            "source": self.source,
        }


# --- Bluetooth ---------------------------------------------------------------


@dataclass(frozen=True)
class BluetoothDevice:
    """A paired or discovered Bluetooth device."""

    address: str
    name: str
    icon: Optional[str] = None
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    rssi: Optional[int] = None

    def to_dict(self) -> dict:
        """Serialise for the UI."""
        return asdict(self)


@dataclass(frozen=True)
class BluetoothStatus:
    """Current Bluetooth adapter state and known devices."""

    available: bool
    reason: Optional[ErrorCode] = None
    powered: bool = False
    discovering: bool = False
    adapter_name: Optional[str] = None
    devices: tuple[BluetoothDevice, ...] = ()

    def to_dict(self) -> dict:
        """Serialise for the UI."""
        return {
            "available": self.available,
            "reason": self.reason.value if self.reason else None,
            "powered": self.powered,
            "discovering": self.discovering,
            "adapter_name": self.adapter_name,
            "devices": [device.to_dict() for device in self.devices],
        }


class PairingKind(str, Enum):
    """What the BlueZ agent is asking the user to do."""

    CONFIRM = "confirm"  # Compare and confirm a six-digit passkey.
    ENTER_PASSKEY = "enter_passkey"  # Type the passkey shown on the device.
    ENTER_PIN = "enter_pin"  # Legacy PIN code.
    DISPLAY_PASSKEY = "display_passkey"  # Type this passkey on the device.
    DISPLAY_PIN = "display_pin"
    AUTHORIZE = "authorize"  # Accept pairing without a code.


@dataclass
class PairingRequest:
    """One outstanding prompt from the BlueZ agent."""

    request_id: str
    address: str
    name: str
    kind: PairingKind
    passkey: Optional[str] = None  # Six digits (zero-padded) or a PIN to display.
    expires_in_s: float = 30.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise for the UI."""
        return {
            "id": self.request_id,
            "address": self.address,
            "name": self.name,
            "kind": self.kind.value,
            "passkey": self.passkey,
            "expires_in_s": self.expires_in_s,
        }


# --- Validation --------------------------------------------------------------

_BT_ADDRESS = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_PIN = re.compile(r"^[0-9A-Za-z]{1,16}$")


def validate_ssid(ssid: object) -> str:
    """Return a valid SSID or raise INVALID_REQUEST (1-32 bytes of UTF-8)."""
    if not isinstance(ssid, str) or not ssid:
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "SSID is required")
    if len(ssid.encode("utf-8")) > 32:
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "SSID is longer than 32 bytes")
    if "\x00" in ssid:
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "SSID contains a NUL byte")
    return ssid


def validate_wifi_password(security: WifiSecurity, password: object) -> Optional[str]:
    """Validate a password for ``security``. Never echoes the value in errors."""
    if not security.supported:
        raise ConnectivityError(
            ErrorCode.UNSUPPORTED_SECURITY,
            f"{security.value} networks cannot be joined from the kiosk",
        )
    if not security.needs_password:
        return None
    if password is None or password == "":
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Password is required")
    if not isinstance(password, str):
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Password must be text")
    if _HEX64.match(password):
        return password
    if not 8 <= len(password) <= 63:
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Password must be 8 to 63 characters")
    if any(ord(char) < 32 or ord(char) > 126 for char in password):
        raise ConnectivityError(
            ErrorCode.INVALID_REQUEST, "Password may only use printable ASCII characters"
        )
    return password


def validate_bt_address(address: object) -> str:
    """Normalise ``AA:BB:CC:DD:EE:FF`` or raise INVALID_REQUEST."""
    if not isinstance(address, str):
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Bluetooth address is required")
    normalized = address.strip().upper()
    if not _BT_ADDRESS.match(normalized):
        raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Invalid Bluetooth address")
    return normalized


def validate_pairing_value(kind: PairingKind, value: object) -> Optional[str]:
    """Validate the user's reply to a passkey/PIN prompt."""
    if kind == PairingKind.ENTER_PASSKEY:
        if not isinstance(value, str) or not value.isdigit() or len(value) > 6:
            raise ConnectivityError(ErrorCode.INVALID_REQUEST, "Passkey must be up to 6 digits")
        return value
    if kind == PairingKind.ENTER_PIN:
        if not isinstance(value, str) or not _PIN.match(value):
            raise ConnectivityError(
                ErrorCode.INVALID_REQUEST, "PIN must be 1 to 16 letters or digits"
            )
        return value
    return None
