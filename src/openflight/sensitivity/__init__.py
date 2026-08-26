"""Software control of the SEN-14262 sound detector's preamp sensitivity."""

from .ads1115 import (
    ADS1115,
    DEFAULT_ADDRESS as ENVELOPE_DEFAULT_ADDRESS,
    MockADS1115,
    validate_address as validate_envelope_address,
)
from .autogain import (
    AutoGainController,
    AutoGainDecision,
    achievable_gain_range,
    band_ratio,
    has_authority,
    position_for_gain_ratio,
)
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
from .envelope import EnvelopeMonitor, EnvelopePeak
from .models import SensitivityState
from .service import (
    DEFAULT_R17_OHMS,
    SoundSensitivityService,
    clamp_position,
    disabled_state,
)

__all__ = [
    "ADS1115",
    "AutoGainController",
    "AutoGainDecision",
    "DEFAULT_ADDRESS",
    "ENVELOPE_DEFAULT_ADDRESS",
    "EnvelopeMonitor",
    "EnvelopePeak",
    "MockADS1115",
    "achievable_gain_range",
    "band_ratio",
    "has_authority",
    "position_for_gain_ratio",
    "validate_envelope_address",
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
