"""
Air density modelling for altitude- and weather-aware carry.

Carry scales with air density: thinner air means less drag *and* less Magnus
lift, and for a golf ball the drag term dominates, so a given launch flies
further as density drops. The ballistic model in `ballistics.py` takes
`air_density` directly, so everything here exists to produce one number —
kilograms per cubic metre — from whatever the operator can tell us or measure.

Density comes from the ideal gas law, ρ = p / (R_specific · T). Note what that
does *not* involve: altitude. Altitude is not a physical input to density, it
is only a convenient stand-in for "what is the pressure where I am". A
barometer measures station pressure directly, which is the quantity we
actually want.

This is worth stating plainly because barometer breakouts (BMP280/BMP390/
BMP580 and friends) advertise an `altitude` reading, and it is tempting to
treat it as the useful output. It is the opposite. That reading is computed
from the measured pressure by assuming a sea-level reference pressure — the
drivers hardcode the ISA 1013.25 hPa — so a sensor that never moves reports an
altitude that wanders by well over a thousand feet as weather systems pass.
Feeding that back through an ISA model to recover pressure reintroduces
exactly the error the sensor was meant to eliminate. When a sensor is
present, use `AirConditions.from_sensor` with the raw pressure and ignore any
altitude the driver offers.

Elevation is still useful when there is no sensor: it is a fixed property of
the installation, and `AirConditions.from_elevation` turns it into a pressure
estimate via the ISA barometric formula. That estimate carries the weather
error the sensor would have removed — worth roughly ±1 to 2.5 yd of driver
carry at pressure extremes, and about ±1 yd on a typical day.

References:
    ISA / ICAO Standard Atmosphere (ISO 2533:1975) for the barometric formula.
    Buck (1981), "New Equations for Computing Vapor Pressure and Enhancement
    Factor", J. Appl. Meteorol. 20, for saturation vapour pressure.
    CIPM-2007 for the moist-air correction, applied here in its simple
    partial-pressure form; the full compressibility treatment refines a term
    already worth under 1.5 yd, so it is not warranted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

# Specific gas constants, J/(kg·K).
R_DRY_AIR = 287.0528
R_WATER_VAPOUR = 461.495

# ISA sea-level reference conditions.
STANDARD_PRESSURE_PA = 101325.0
STANDARD_TEMPERATURE_C = 15.0
# Tropospheric lapse rate, K/m. Valid to ~11 km, far beyond any golf course.
ISA_LAPSE_RATE_K_PER_M = 0.0065
GRAVITY_M_S2 = 9.80665
MOLAR_MASS_AIR_KG_MOL = 0.0289644
UNIVERSAL_GAS_CONSTANT = 8.31447

# Exponent of the ISA barometric formula, g·M/(R·L) ≈ 5.2559.
_BAROMETRIC_EXPONENT = (
    GRAVITY_M_S2 * MOLAR_MASS_AIR_KG_MOL / (UNIVERSAL_GAS_CONSTANT * ISA_LAPSE_RATE_K_PER_M)
)

# Density of dry air at ISA sea level, 1.225 kg/m³. Kept as a derived value
# rather than a literal so the constant can never drift from the formula.
STANDARD_AIR_DENSITY = STANDARD_PRESSURE_PA / (R_DRY_AIR * (STANDARD_TEMPERATURE_C + 273.15))

FEET_PER_METRE = 3.280839895
PA_PER_HPA = 100.0

# Plausibility bounds. These reject transposed units and typos (hPa entered as
# Pa, feet entered as metres) rather than trying to police physics: the limits
# are generous enough to admit anywhere golf is played and then some.
MIN_ELEVATION_M = -500.0  # Below the Dead Sea shore.
MAX_ELEVATION_M = 6000.0  # Above any course on earth.
MIN_PRESSURE_PA = 40000.0  # ~7000 m; below this the ISA model is out of scope.
MAX_PRESSURE_PA = 115000.0  # Beyond the strongest recorded sea-level highs.
MIN_TEMPERATURE_C = -60.0
MAX_TEMPERATURE_C = 60.0

# "sensor_stale" is a real measurement that has aged past the freshness limit.
# It is kept distinct from "sensor" so a log reader can tell a current reading
# from a remembered one, and ranked above "config" because yesterday's measured
# pressure at this site beats a textbook assumption about it.
ConditionsSource = Literal["standard", "config", "sensor", "sensor_stale"]


class AirConditionsError(ValueError):
    """Raised when supplied atmospheric inputs are outside plausible bounds."""


def _require_range(value: float, low: float, high: float, name: str, unit: str) -> float:
    """Validate a scalar input, raising with the offending value named."""
    if not math.isfinite(value):
        raise AirConditionsError(f"{name} must be a finite number, got {value!r}")
    if not low <= value <= high:
        raise AirConditionsError(
            f"{name} of {value:g} {unit} is outside the supported range {low:g} to {high:g} {unit}"
        )
    return float(value)


def saturation_vapour_pressure_pa(temperature_c: float) -> float:
    """
    Saturation vapour pressure of water over liquid, in pascals.

    Buck (1981) equation, accurate to better than 0.1% from -30 to +50 °C.
    """
    temperature_c = _require_range(
        temperature_c, MIN_TEMPERATURE_C, MAX_TEMPERATURE_C, "temperature", "°C"
    )
    return 611.21 * math.exp(
        (18.678 - temperature_c / 234.5) * (temperature_c / (257.14 + temperature_c))
    )


def air_density(
    pressure_pa: float,
    temperature_c: float,
    relative_humidity: Optional[float] = None,
) -> float:
    """
    Air density in kg/m³ from station pressure and temperature.

    Args:
        pressure_pa: Absolute (station) pressure, not sea-level-corrected.
        temperature_c: Ambient air temperature in °C.
        relative_humidity: Optional 0.0-1.0 fraction. Moist air is *less*
            dense than dry air at the same pressure, because water vapour is
            lighter than the nitrogen and oxygen it displaces. The effect is
            modest and strongly temperature-dependent: 0.9% density (0.7 yd of
            driver carry) at 20 °C saturated, rising to 2.1% (1.4 yd) at 35 °C.
            Omitting it in temperate conditions is entirely reasonable.

    Returns:
        Density in kg/m³.

    Raises:
        AirConditionsError: If any input is outside its plausible range.
    """
    pressure_pa = _require_range(pressure_pa, MIN_PRESSURE_PA, MAX_PRESSURE_PA, "pressure", "Pa")
    temperature_c = _require_range(
        temperature_c, MIN_TEMPERATURE_C, MAX_TEMPERATURE_C, "temperature", "°C"
    )
    kelvin = temperature_c + 273.15

    if relative_humidity is None:
        return pressure_pa / (R_DRY_AIR * kelvin)

    relative_humidity = _require_range(relative_humidity, 0.0, 1.0, "relative_humidity", "fraction")
    # Partial pressures: vapour takes its share, dry air holds the remainder.
    vapour_pa = relative_humidity * saturation_vapour_pressure_pa(temperature_c)
    dry_pa = pressure_pa - vapour_pa
    return dry_pa / (R_DRY_AIR * kelvin) + vapour_pa / (R_WATER_VAPOUR * kelvin)


def station_pressure_pa(
    elevation_m: float,
    sea_level_pressure_pa: float = STANDARD_PRESSURE_PA,
    sea_level_temperature_c: float = STANDARD_TEMPERATURE_C,
) -> float:
    """
    Estimate absolute pressure at an elevation via the ISA barometric formula.

    This is the no-sensor path: elevation is a fixed, known property of the
    installation, so it captures the large and permanent part of the density
    error. What it cannot capture is the weather — pass a current sea-level
    pressure reading if one is available, otherwise the ISA default carries a
    typical ±1 yd of driver-carry uncertainty.
    """
    elevation_m = _require_range(elevation_m, MIN_ELEVATION_M, MAX_ELEVATION_M, "elevation", "m")
    sea_level_pressure_pa = _require_range(
        sea_level_pressure_pa,
        MIN_PRESSURE_PA,
        MAX_PRESSURE_PA,
        "sea_level_pressure",
        "Pa",
    )
    sea_level_temperature_c = _require_range(
        sea_level_temperature_c,
        MIN_TEMPERATURE_C,
        MAX_TEMPERATURE_C,
        "sea_level_temperature",
        "°C",
    )
    sea_level_kelvin = sea_level_temperature_c + 273.15
    lapsed = 1.0 - ISA_LAPSE_RATE_K_PER_M * elevation_m / sea_level_kelvin
    return sea_level_pressure_pa * lapsed**_BAROMETRIC_EXPONENT


def temperature_at_elevation_c(
    elevation_m: float, sea_level_temperature_c: float = STANDARD_TEMPERATURE_C
) -> float:
    """
    ISA temperature at an elevation, used only when no temperature is supplied.

    A guess from the standard lapse rate is better than assuming sea-level
    15 °C at 5000 ft, but it is still a guess: real temperature swings dominate
    it, which is why an explicit `temperature_c` always wins.
    """
    elevation_m = _require_range(elevation_m, MIN_ELEVATION_M, MAX_ELEVATION_M, "elevation", "m")
    return sea_level_temperature_c - ISA_LAPSE_RATE_K_PER_M * elevation_m


def pressure_altitude_m(
    pressure_pa: float, sea_level_pressure_pa: float = STANDARD_PRESSURE_PA
) -> float:
    """
    Invert the barometric formula to get pressure altitude, for diagnostics only.

    This is the calculation barometer libraries expose as `altitude`, and it is
    deliberately *not* used in the density path. It assumes a sea-level
    reference pressure, so with the ISA default a stationary sensor reports an
    altitude that moves by more than 1500 ft as weather passes. Density needs
    the pressure that was already measured; routing it through here and back
    would only add the error this module exists to avoid.
    """
    pressure_pa = _require_range(pressure_pa, MIN_PRESSURE_PA, MAX_PRESSURE_PA, "pressure", "Pa")
    sea_level_pressure_pa = _require_range(
        sea_level_pressure_pa,
        MIN_PRESSURE_PA,
        MAX_PRESSURE_PA,
        "sea_level_pressure",
        "Pa",
    )
    ratio = (pressure_pa / sea_level_pressure_pa) ** (1.0 / _BAROMETRIC_EXPONENT)
    return (STANDARD_TEMPERATURE_C + 273.15) / ISA_LAPSE_RATE_K_PER_M * (1.0 - ratio)


@dataclass(frozen=True)
class AirConditions:
    """
    A resolved atmospheric state, and where it came from.

    `source` is carried through to the shot payload and session log so a later
    reader can tell a measured density from an assumed one — the difference
    between the two is up to 14 yd of driver carry, which is far too large to
    leave implicit.
    """

    pressure_pa: float
    temperature_c: float
    relative_humidity: Optional[float] = None
    elevation_m: Optional[float] = None
    source: ConditionsSource = "standard"

    def __post_init__(self) -> None:
        # Validate eagerly so a bad config fails at startup, not on the first
        # shot of a session.
        air_density(self.pressure_pa, self.temperature_c, self.relative_humidity)
        if self.elevation_m is not None:
            _require_range(self.elevation_m, MIN_ELEVATION_M, MAX_ELEVATION_M, "elevation", "m")

    @property
    def density_kg_m3(self) -> float:
        """Air density for this state, in kg/m³."""
        return air_density(self.pressure_pa, self.temperature_c, self.relative_humidity)

    @property
    def elevation_ft(self) -> Optional[float]:
        """Configured elevation in feet, if one was supplied."""
        if self.elevation_m is None:
            return None
        return self.elevation_m * FEET_PER_METRE

    @classmethod
    def standard(cls) -> "AirConditions":
        """ISA sea level, 15 °C, dry — the model's historical default."""
        return cls(
            pressure_pa=STANDARD_PRESSURE_PA,
            temperature_c=STANDARD_TEMPERATURE_C,
            source="standard",
        )

    @classmethod
    def from_elevation(
        cls,
        elevation_ft: float,
        temperature_c: Optional[float] = None,
        sea_level_pressure_hpa: Optional[float] = None,
        relative_humidity_pct: Optional[float] = None,
    ) -> "AirConditions":
        """
        Build conditions from operator-supplied configuration.

        Args:
            elevation_ft: Site elevation above sea level, in feet.
            temperature_c: Ambient temperature. Falls back to the ISA
                temperature for the elevation, which is a weak assumption —
                a real reading is worth up to 5 yd on a driver.
            sea_level_pressure_hpa: Current sea-level (QNH) pressure, if
                known from a local forecast. Defaults to ISA 1013.25.
            relative_humidity_pct: Optional 0-100 humidity.
        """
        elevation_m = (
            _require_range(
                elevation_ft,
                MIN_ELEVATION_M * FEET_PER_METRE,
                MAX_ELEVATION_M * FEET_PER_METRE,
                "elevation",
                "ft",
            )
            / FEET_PER_METRE
        )

        sea_level_pa = (
            STANDARD_PRESSURE_PA
            if sea_level_pressure_hpa is None
            else _require_range(
                sea_level_pressure_hpa,
                MIN_PRESSURE_PA / PA_PER_HPA,
                MAX_PRESSURE_PA / PA_PER_HPA,
                "sea_level_pressure",
                "hPa",
            )
            * PA_PER_HPA
        )

        resolved_temp = (
            temperature_at_elevation_c(elevation_m) if temperature_c is None else temperature_c
        )
        humidity = (
            None
            if relative_humidity_pct is None
            else _require_range(relative_humidity_pct, 0.0, 100.0, "relative_humidity", "%") / 100.0
        )
        return cls(
            pressure_pa=station_pressure_pa(elevation_m, sea_level_pa),
            temperature_c=resolved_temp,
            relative_humidity=humidity,
            elevation_m=elevation_m,
            source="config",
        )

    @classmethod
    def from_sensor(
        cls,
        pressure_pa: float,
        temperature_c: float,
        relative_humidity_pct: Optional[float] = None,
        elevation_m: Optional[float] = None,
    ) -> "AirConditions":
        """
        Build conditions from a live barometer reading.

        Pass the sensor's raw station pressure — not a sea-level-corrected
        value, and not its derived `altitude`. `elevation_m` is metadata only;
        it never enters the density calculation when a real pressure is in hand.

        The accuracy limit here is almost never the pressure channel. A
        barometer die sharing an enclosure with a Raspberry Pi reads several
        degrees warm from self-heating, and 3 °C of temperature error is about
        1% density — comparable to the entire benefit of measuring pressure at
        all. Mount the sensor in moving ambient air, or calibrate the offset.
        """
        return cls(
            pressure_pa=pressure_pa,
            temperature_c=temperature_c,
            relative_humidity=(
                None
                if relative_humidity_pct is None
                else _require_range(relative_humidity_pct, 0.0, 100.0, "relative_humidity", "%")
                / 100.0
            ),
            elevation_m=elevation_m,
            source="sensor",
        )

    def to_dict(self) -> dict:
        """Serialisable summary for the shot payload and session log."""
        return {
            "density_kg_m3": round(self.density_kg_m3, 4),
            "pressure_pa": round(self.pressure_pa, 1),
            "temperature_c": round(self.temperature_c, 2),
            "relative_humidity_pct": (
                None if self.relative_humidity is None else round(self.relative_humidity * 100.0, 1)
            ),
            "elevation_ft": (None if self.elevation_ft is None else round(self.elevation_ft, 1)),
            "source": self.source,
        }
