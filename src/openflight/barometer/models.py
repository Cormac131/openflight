"""Value objects shared by barometer drivers and consumers."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..air_density import AirConditions


@dataclass(frozen=True)
class PressureSample:
    """One timestamped BMP580 station-pressure and temperature reading."""

    timestamp: float
    pressure_pa: float
    temperature_c: float


@dataclass(frozen=True)
class AirSnapshot:
    """
    Filtered atmospheric state produced from a sample window.

    `temperature_c` is already offset-corrected; `raw_temperature_c` keeps the
    uncorrected die reading so a self-heating problem stays visible in the logs
    rather than being silently absorbed into the calibration.
    """

    timestamp: float
    pressure_pa: float
    temperature_c: float
    raw_temperature_c: float
    density_kg_m3: float
    pressure_std_pa: float
    sample_count: int

    def to_dict(self) -> dict:
        """Return JSON-safe rounded diagnostic fields."""
        data = asdict(self)
        data["pressure_pa"] = round(data["pressure_pa"], 1)
        data["pressure_hpa"] = round(data["pressure_pa"] / 100.0, 2)
        data["pressure_std_pa"] = round(data["pressure_std_pa"], 2)
        for key in ("temperature_c", "raw_temperature_c"):
            data[key] = round(data[key], 2)
        data["density_kg_m3"] = round(data["density_kg_m3"], 4)
        return data

    def to_air_conditions(self, elevation_m: float | None = None) -> AirConditions:
        """
        Convert to the atmospheric state the carry model consumes.

        Uses the measured station pressure directly. `elevation_m` is carried
        as metadata only — a barometer's derived altitude is an assumption
        about sea-level pressure, not a measurement, and must never re-enter
        the density calculation. See `openflight.air_density`.
        """
        return AirConditions.from_sensor(
            pressure_pa=self.pressure_pa,
            temperature_c=self.temperature_c,
            elevation_m=elevation_m,
        )


@dataclass(frozen=True)
class ReadingSelection:
    """Result of selecting a usable atmospheric reading for one shot."""

    snapshot: AirSnapshot | None
    status: str
    age_s: float | None = None

    def to_dict(self) -> dict:
        """Return selection diagnostics suitable for shot/session logging."""
        data = {
            "applied": self.snapshot is not None,
            "status": self.status,
            "age_s": round(self.age_s, 1) if self.age_s is not None else None,
        }
        if self.snapshot is not None:
            data.update(self.snapshot.to_dict())
        return data
