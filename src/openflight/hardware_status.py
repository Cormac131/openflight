"""Runtime record of hardware that failed to start, and what to do about it.

Until this existed, every hardware failure at start-up was fatal: the server
printed a traceback and exited before Flask ever bound a port. On a developer's
laptop that is readable. On a kiosk with no keyboard it is a blank screen —
the owner is told nothing at all, because the thing that would have told them
is the thing that failed to start.

So the server now records failures here and starts anyway. The UI reads the
result and says what is wrong. That is the whole purpose of this module: turn
"the app did not appear" into "the radar is not plugged in".

Faults are :class:`Severity.BLOCKING` when no shot can be measured without the
device (only the OPS243-A qualifies — it is the ball-speed source) and
:class:`Severity.DEGRADED` when the session still works with less data, which
is every optional radar and sensor. The distinction is what lets the UI cover
the screen for one and show a dismissible banner for the other.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from openflight.provisioning.detect import DeviceKind


class Severity(str, Enum):
    """How much of the product a fault takes away."""

    # No shot can be measured. The UI covers the screen.
    BLOCKING = "blocking"
    # Shots still work, with less data than the build is capable of.
    DEGRADED = "degraded"


# Owner-facing guidance per device. Kept here rather than in the UI so the
# console, the first-boot report, and the screen all say the same thing, and
# so it can be tested without rendering React.
_REMEDIES: dict[DeviceKind, str] = {
    DeviceKind.OPS243: (
        "Check that the radar's USB cable is connected at both ends, then "
        "switch the unit off and on again."
    ),
    DeviceKind.IWR6843: (
        "Check the 60 GHz radar's USB cable. Ball speed still works without "
        "it; launch angle and club path do not."
    ),
    DeviceKind.KLD7_VERTICAL: (
        "Check the angle radar's USB cable. Ball speed still works without "
        "it; launch angle does not."
    ),
    DeviceKind.KLD7_HORIZONTAL: (
        "Check the second angle radar's USB cable. Club path is unavailable "
        "without it."
    ),
    DeviceKind.INCLINOMETER: (
        "Tilt compensation is unavailable; the configured radar tilt is used "
        "instead. Check the sensor's I2C wiring."
    ),
    DeviceKind.BATTERY: "Battery level is unavailable. Check the UPS board's I2C wiring.",
    DeviceKind.CAMERA: "Camera features are unavailable. Check the ribbon cable seating.",
}

# Short titles, so the UI never has to map a device id to English itself.
_TITLES: dict[DeviceKind, str] = {
    DeviceKind.OPS243: "Radar not found",
    DeviceKind.IWR6843: "60 GHz radar unavailable",
    DeviceKind.KLD7_VERTICAL: "Angle radar unavailable",
    DeviceKind.KLD7_HORIZONTAL: "Second angle radar unavailable",
    DeviceKind.INCLINOMETER: "Inclinometer unavailable",
    DeviceKind.BATTERY: "Battery monitoring unavailable",
    DeviceKind.CAMERA: "Camera unavailable",
}


@dataclass(frozen=True)
class HardwareFault:
    """One device that was asked for and could not be brought up."""

    device: DeviceKind
    severity: Severity
    detail: str = ""

    @property
    def title(self) -> str:
        """Short owner-facing headline."""
        return _TITLES.get(self.device, f"{self.device.value} unavailable")

    @property
    def remedy(self) -> str:
        """What the owner should try."""
        return _REMEDIES.get(self.device, "")

    def to_dict(self) -> dict[str, Any]:
        """Wire form for the ``hardware_status`` WebSocket event."""
        return {
            "device": self.device.value,
            "severity": self.severity.value,
            "title": self.title,
            "remedy": self.remedy,
            # The exception text. Shown small, for a support request rather
            # than for the owner to act on.
            "detail": self.detail,
        }


class HardwareStatus:
    """Thread-safe collection of the faults recorded this run.

    Start-up records faults from the main thread while the monitor's reader
    threads may already be running, and Socket.IO serves reads from yet
    another, so the lock is not decoration.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._faults: dict[DeviceKind, HardwareFault] = {}

    def record(
        self,
        device: DeviceKind,
        severity: Severity,
        detail: str = "",
    ) -> HardwareFault:
        """Record (or replace) the fault for ``device`` and return it."""
        fault = HardwareFault(device=device, severity=severity, detail=detail)
        with self._lock:
            self._faults[device] = fault
        return fault

    def clear(self, device: DeviceKind) -> None:
        """Forget any fault for ``device``, e.g. after a successful restart."""
        with self._lock:
            self._faults.pop(device, None)

    def clear_all(self) -> None:
        """Forget every fault. Used when the monitor is restarted from the UI."""
        with self._lock:
            self._faults.clear()

    @property
    def faults(self) -> tuple[HardwareFault, ...]:
        """Every recorded fault, blocking ones first so the UI can take [0]."""
        with self._lock:
            values = list(self._faults.values())
        return tuple(sorted(values, key=lambda f: f.severity is not Severity.BLOCKING))

    @property
    def blocking(self) -> Optional[HardwareFault]:
        """The fault that makes the product unusable, if there is one."""
        for fault in self.faults:
            if fault.severity is Severity.BLOCKING:
                return fault
        return None

    def get(self, device: DeviceKind) -> Optional[HardwareFault]:
        """The fault recorded for ``device``, if any."""
        with self._lock:
            return self._faults.get(device)

    def to_dict(self, *, radar_connected: bool) -> dict[str, Any]:
        """Wire form for the ``hardware_status`` WebSocket event.

        ``radar_connected`` is passed in rather than inferred: this module
        knows what failed to *start*, while liveness is the monitor's to
        answer, and conflating the two is how the old status flag came to
        report "connected" for a radar that had been unplugged.
        """
        faults = self.faults
        return {
            "radar_connected": radar_connected,
            "ok": radar_connected and not faults,
            "blocking": self.blocking.to_dict() if self.blocking else None,
            "faults": [fault.to_dict() for fault in faults],
        }

    def console_summary(self) -> str:
        """Multi-line summary for the server's own stdout."""
        faults = self.faults
        if not faults:
            return "All requested hardware started."
        lines = []
        for fault in faults:
            marker = "ERROR" if fault.severity is Severity.BLOCKING else "WARNING"
            lines.append(f"{marker}: {fault.title} — {fault.remedy}")
            if fault.detail:
                lines.append(f"       {fault.detail}")
        return "\n".join(lines)
