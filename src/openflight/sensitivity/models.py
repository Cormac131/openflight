"""State reported by the sound-detector sensitivity service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SensitivityState:
    """A snapshot of the X9C104 wiper and what it means for the detector.

    ``position`` is None when the wiper has never been driven to a known tap —
    the chip has no readback, so an uncalibrated wiper genuinely has no value
    the server can report.
    """

    enabled: bool
    position: Optional[int]
    max_position: int
    default_position: int
    sensitivity_percent: Optional[float]
    resistance_ohms: Optional[float]
    preamp_feedback_ohms: Optional[float]
    series_ohms: float
    simulated: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Return the WebSocket payload for this state."""
        return {
            "enabled": self.enabled,
            "position": self.position,
            "max_position": self.max_position,
            "default_position": self.default_position,
            "sensitivity_percent": (
                round(self.sensitivity_percent, 1) if self.sensitivity_percent is not None else None
            ),
            "resistance_ohms": (
                round(self.resistance_ohms) if self.resistance_ohms is not None else None
            ),
            "preamp_feedback_ohms": (
                round(self.preamp_feedback_ohms) if self.preamp_feedback_ohms is not None else None
            ),
            "series_ohms": round(self.series_ohms),
            "simulated": self.simulated,
            "error": self.error,
        }
