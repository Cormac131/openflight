"""Barometric pressure sensing for altitude- and weather-aware carry."""

from .bmp580 import BMP580, BMP580IdentityError, BMP580NotReadyError
from .models import AirSnapshot, PressureSample, ReadingSelection
from .service import BarometerService

__all__ = [
    "AirSnapshot",
    "BMP580",
    "BMP580IdentityError",
    "BMP580NotReadyError",
    "BarometerService",
    "PressureSample",
    "ReadingSelection",
]
