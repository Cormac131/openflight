"""Software control of the SEN-14262 sound detector's preamp sensitivity."""

from .config import CONFIG_PATH, load_position, save_position
from .models import SensitivityState
from .service import (
    DEFAULT_POSITION,
    DEFAULT_R17_OHMS,
    SoundSensitivityService,
    clamp_position,
    disabled_state,
)
from .x9c104 import (
    MAX_POSITION,
    TAP_COUNT,
    X9C104,
    MockX9C104,
    position_for_resistance,
    preamp_feedback_ohms,
    resistance_ohms,
    sensitivity_percent,
)

__all__ = [
    "CONFIG_PATH",
    "DEFAULT_POSITION",
    "DEFAULT_R17_OHMS",
    "MAX_POSITION",
    "MockX9C104",
    "SensitivityState",
    "SoundSensitivityService",
    "TAP_COUNT",
    "X9C104",
    "clamp_position",
    "disabled_state",
    "load_position",
    "position_for_resistance",
    "preamp_feedback_ohms",
    "resistance_ohms",
    "save_position",
    "sensitivity_percent",
]
