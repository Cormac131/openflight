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
from .config import CONFIG_PATH, load_position, save_position
from .ds3502 import (
    DEFAULT_ADDRESS as DS3502_ADDRESS,
    DEFAULT_SERIES_OHMS as DS3502_SERIES_OHMS,
    DS3502,
    MockDS3502,
    validate_address as validate_ds3502_address,
)
from .envelope import EnvelopeMonitor, EnvelopePeak
from .mcp401x import (
    DEFAULT_ADDRESS as MCP401X_ADDRESS,
    DEFAULT_SERIES_OHMS as MCP401X_SERIES_OHMS,
    MCP401X,
    MockMCP401X,
    validate_address as validate_mcp401x_address,
)
from .models import SensitivityState
from .potentiometer import SEN14262_R3_OHMS, DigitalPotentiometer, ResistanceModel
from .service import (
    DEFAULT_R17_OHMS,
    SoundSensitivityService,
    clamp_position,
    disabled_state,
)

#: The pot drivers the server can be pointed at, and their mock twins. The
#: MCP4017 is preferred: at 100k it spans R17's range with no series resistor,
#: which also gives the closed gain loop enough travel to be worth running.
DEVICES = {
    "mcp401x": {
        "driver": MCP401X,
        "mock": MockMCP401X,
        "address": MCP401X_ADDRESS,
        "series_ohms": MCP401X_SERIES_OHMS,
        "validate_address": validate_mcp401x_address,
    },
    "ds3502": {
        "driver": DS3502,
        "mock": MockDS3502,
        "address": DS3502_ADDRESS,
        "series_ohms": DS3502_SERIES_OHMS,
        "validate_address": validate_ds3502_address,
    },
}

DEFAULT_DEVICE = "mcp401x"

# Retained for callers that predate device selection; both parts are 7-bit.
MAX_POSITION = 127

__all__ = [
    "ADS1115",
    "CONFIG_PATH",
    "DEFAULT_DEVICE",
    "DEFAULT_R17_OHMS",
    "DEVICES",
    "DS3502",
    "DS3502_ADDRESS",
    "DS3502_SERIES_OHMS",
    "ENVELOPE_DEFAULT_ADDRESS",
    "MAX_POSITION",
    "MCP401X",
    "MCP401X_ADDRESS",
    "MCP401X_SERIES_OHMS",
    "SEN14262_R3_OHMS",
    "AutoGainController",
    "AutoGainDecision",
    "DigitalPotentiometer",
    "EnvelopeMonitor",
    "EnvelopePeak",
    "MockADS1115",
    "MockDS3502",
    "MockMCP401X",
    "ResistanceModel",
    "SensitivityState",
    "SoundSensitivityService",
    "achievable_gain_range",
    "band_ratio",
    "clamp_position",
    "disabled_state",
    "has_authority",
    "load_position",
    "position_for_gain_ratio",
    "save_position",
    "validate_ds3502_address",
    "validate_envelope_address",
    "validate_mcp401x_address",
]
