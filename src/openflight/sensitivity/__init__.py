"""Software control of the SEN-14262 sound detector's preamp sensitivity."""

from .ds3502 import (
    DEFAULT_ADDRESS,
    DEFAULT_SERIES_OHMS,
    DS3502,
    END_TO_END_OHMS,
    MAX_POSITION,
    POSITION_COUNT,
    MockDS3502,
    position_for_resistance,
    preamp_feedback_ohms,
    resistance_ohms,
    sensitivity_percent,
    validate_address,
)
from .models import SensitivityState
from .service import (
    DEFAULT_R17_OHMS,
    SoundSensitivityService,
    clamp_position,
    disabled_state,
)

__all__ = [
    "DEFAULT_ADDRESS",
    "DEFAULT_R17_OHMS",
    "DEFAULT_SERIES_OHMS",
    "DS3502",
    "END_TO_END_OHMS",
    "MAX_POSITION",
    "MockDS3502",
    "POSITION_COUNT",
    "SensitivityState",
    "SoundSensitivityService",
    "clamp_position",
    "disabled_state",
    "position_for_resistance",
    "preamp_feedback_ohms",
    "resistance_ohms",
    "sensitivity_percent",
    "validate_address",
]
