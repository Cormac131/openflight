"""Kiosk Wi-Fi (NetworkManager) and Bluetooth (BlueZ) management.

See ``docs/using/connections.md`` for the user-facing behaviour and
``scripts/setup/setup_connectivity.sh`` for the system permissions.
"""

from .models import ConnectivityError, ErrorCode

__all__ = ["ConnectivityError", "ErrorCode"]
