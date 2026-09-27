"""Air density from the environment, for the ball-flight solver.

Drag and lift both scale with air density, and density moves by several
percent across the conditions a launch monitor sees: a cold sea-level range
is about 1.29 kg/m3, a hot humid one about 1.15, a range at 1500 m about
1.06. The standard value the solver otherwise assumes is 1.225 (15 C,
1013.25 hPa, dry). This module turns the readings the planned environmental
sensors provide (temperature, pressure, relative humidity) into a density,
falling back to the ISA pressure at a known altitude when there is no
barometer, so carry is corrected for the day rather than for a standard one.

The formula is the CIPM-style mixture of dry air and water vapour with the
Tetens saturation pressure: accurate to well under a percent over golfing
conditions, which is far below the solver's own uncertainty.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

STANDARD_TEMPERATURE_C = 15.0
STANDARD_PRESSURE_HPA = 1013.25
STANDARD_DENSITY_KG_M3 = 1.225
R_DRY_AIR = 287.058  # J/(kg K)
R_WATER_VAPOUR = 461.495  # J/(kg K)
ISA_LAPSE_RATE_K_M = 0.0065
ISA_EXPONENT = 5.2559  # g M / (R L)


def saturation_vapour_pressure_hpa(temperature_c: float) -> float:
    """Tetens: saturation pressure of water vapour over liquid water."""
    return 6.1078 * math.exp(17.27 * temperature_c / (temperature_c + 237.3))


def pressure_at_altitude_hpa(
    altitude_m: float, sea_level_hpa: float = STANDARD_PRESSURE_HPA
) -> float:
    """ISA pressure at an altitude; what a range at 1500 m sees without a barometer."""
    ratio = 1.0 - ISA_LAPSE_RATE_K_M * altitude_m / (STANDARD_TEMPERATURE_C + 273.15)
    if ratio <= 0.0:
        raise ValueError(f"altitude {altitude_m} m is above the ISA troposphere model")
    return sea_level_hpa * ratio**ISA_EXPONENT


def air_density_kg_m3(
    temperature_c: float = STANDARD_TEMPERATURE_C,
    pressure_hpa: float | None = STANDARD_PRESSURE_HPA,
    relative_humidity: float = 0.0,
    altitude_m: float | None = None,
) -> float:
    """Moist-air density. ``relative_humidity`` is 0..1; ``pressure_hpa`` is
    the station pressure, or None to take the ISA pressure at ``altitude_m``."""
    if not 0.0 <= relative_humidity <= 1.0:
        raise ValueError(f"relative humidity must be 0..1, got {relative_humidity}")
    if temperature_c < -60.0 or temperature_c > 60.0:
        raise ValueError(f"temperature {temperature_c} C is outside the model's range")
    if pressure_hpa is None:
        pressure_hpa = pressure_at_altitude_hpa(altitude_m or 0.0)
    if pressure_hpa <= 0.0:
        raise ValueError(f"pressure must be positive, got {pressure_hpa} hPa")
    kelvin = temperature_c + 273.15
    vapour_pa = relative_humidity * saturation_vapour_pressure_hpa(temperature_c) * 100.0
    dry_pa = pressure_hpa * 100.0 - vapour_pa
    if dry_pa <= 0.0:
        raise ValueError("vapour pressure exceeds the station pressure")
    return dry_pa / (R_DRY_AIR * kelvin) + vapour_pa / (R_WATER_VAPOUR * kelvin)


@dataclass(frozen=True)
class Environment:
    """What the environmental sensors (or the operator) report about the range."""

    temperature_c: float = STANDARD_TEMPERATURE_C
    pressure_hpa: float | None = STANDARD_PRESSURE_HPA  # None: from altitude
    relative_humidity: float = 0.0
    altitude_m: float | None = None

    @property
    def air_density_kg_m3(self) -> float:
        """The standard environment returns the solver's 1.225 exactly, so a
        server that was never told about the air computes what it always did."""
        if self == STANDARD_ENVIRONMENT:
            return STANDARD_DENSITY_KG_M3
        return air_density_kg_m3(
            self.temperature_c, self.pressure_hpa, self.relative_humidity, self.altitude_m
        )

    @property
    def density_ratio(self) -> float:
        """Density over the standard the solver assumes; 1.0 changes nothing."""
        return self.air_density_kg_m3 / STANDARD_DENSITY_KG_M3

    def describe(self) -> str:
        pressure = (
            f"{self.pressure_hpa:.0f} hPa"
            if self.pressure_hpa is not None
            else f"ISA at {self.altitude_m or 0.0:.0f} m"
        )
        return (
            f"{self.temperature_c:.1f} C, {pressure}, {100 * self.relative_humidity:.0f}% RH: "
            f"{self.air_density_kg_m3:.3f} kg/m3 ({100 * (self.density_ratio - 1):+.1f}% vs standard)"
        )


STANDARD_ENVIRONMENT = Environment()

__all__ = [
    "STANDARD_DENSITY_KG_M3",
    "STANDARD_ENVIRONMENT",
    "STANDARD_PRESSURE_HPA",
    "STANDARD_TEMPERATURE_C",
    "Environment",
    "air_density_kg_m3",
    "pressure_at_altitude_hpa",
    "saturation_vapour_pressure_hpa",
]
