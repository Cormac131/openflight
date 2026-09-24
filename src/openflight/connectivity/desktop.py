"""Kiosk "Show desktop" control shared with ``scripts/start-kiosk.sh``.

The server never touches windows or processes itself. ``start-kiosk.sh``
creates a private control directory (``$XDG_RUNTIME_DIR/openflight-kiosk-<port>``)
only when it launched a kiosk browser into a graphical session, writes its PID
there, and polls for fixed request files:

* ``show-desktop`` (written here) -> close the kiosk browser, keep the server
  and radar running, and stay on the desktop until asked to return.
* ``return`` (written by ``scripts/return-to-openflight.sh``) -> relaunch the
  kiosk browser against the running server.

Only these fixed names are ever written, so no command or path from the HTTP
request reaches the shell.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

CONTROL_DIR_ENV = "OPENFLIGHT_KIOSK_CONTROL_DIR"
PID_FILE = "kiosk.pid"
MODE_FILE = "mode"
SHOW_DESKTOP_REQUEST = "show-desktop"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class DesktopControl:
    """Availability check and request writer for the kiosk control directory."""

    def __init__(self, control_dir: Optional[str | Path]):
        self.control_dir = Path(control_dir) if control_dir else None

    @classmethod
    def from_environment(cls) -> "DesktopControl":
        """Use the directory ``start-kiosk.sh`` exported, if any."""
        return cls(os.environ.get(CONTROL_DIR_ENV) or None)

    def _kiosk_pid(self) -> Optional[int]:
        if self.control_dir is None:
            return None
        try:
            return int((self.control_dir / PID_FILE).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def status(self) -> dict:
        """``available`` only while the kiosk supervisor is alive in a desktop session."""
        if self.control_dir is None:
            return {"available": False, "reason": "not_kiosk", "mode": None}
        pid = self._kiosk_pid()
        if pid is None or not _pid_alive(pid):
            return {"available": False, "reason": "no_desktop_session", "mode": None}
        try:
            mode = (self.control_dir / MODE_FILE).read_text(encoding="utf-8").strip() or "kiosk"
        except OSError:
            mode = "kiosk"
        return {"available": True, "reason": None, "mode": mode}

    def request_show_desktop(self) -> bool:
        """Atomically drop the request file. False when unavailable."""
        if not self.status()["available"] or self.control_dir is None:
            return False
        target = self.control_dir / SHOW_DESKTOP_REQUEST
        temporary = self.control_dir / f".{SHOW_DESKTOP_REQUEST}.{os.getpid()}"
        temporary.write_text("1\n", encoding="utf-8")
        os.replace(temporary, target)
        return True
