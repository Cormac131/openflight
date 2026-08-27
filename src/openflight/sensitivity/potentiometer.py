"""Resistance maths shared by every digipot fitted to the R17 pad.

Both supported parts present the same shape to the circuit — a fixed series
resistor, the wiper's own resistance, and a linear ladder — so the maths lives
here once rather than once per driver. A driver supplies the constants; this
supplies the arithmetic and the reverse lookup.
"""

from __future__ import annotations

from typing import Protocol

#: The SEN-14262's onboard feedback resistor, which the R17 pad parallels.
SEN14262_R3_OHMS = 100_000.0


def resistance_ohms(
    position: int,
    *,
    max_position: int,
    series_ohms: float,
    wiper_ohms: float,
    end_to_end_ohms: float,
) -> float:
    """Return what R17 presents at ``position``."""
    return series_ohms + wiper_ohms + end_to_end_ohms * position / max_position


def preamp_feedback_ohms(pot_ohms: float) -> float:
    """Return the preamp feedback resistance for an R17 value.

    R17 in parallel with R3. Lower means less gain, so a lower wiper step is a
    *less* sensitive detector.
    """
    return pot_ohms * SEN14262_R3_OHMS / (pot_ohms + SEN14262_R3_OHMS)


def sensitivity_percent(position: int, max_position: int) -> float:
    """Return ``position`` as a 0-100% share of the wiper's travel."""
    return 100.0 * position / max_position


def position_for_resistance(
    ohms: float,
    *,
    max_position: int,
    series_ohms: float,
    wiper_ohms: float,
    end_to_end_ohms: float,
) -> int:
    """Return the step whose R17 resistance is closest to ``ohms``. Clamps."""
    raw = round((ohms - series_ohms - wiper_ohms) * max_position / end_to_end_ohms)
    return max(0, min(max_position, int(raw)))


class ResistanceModel:
    """Mixin giving a driver its resistance maths from four constants.

    A driver sets ``max_position``, ``series_ohms``, ``wiper_ohms`` and
    ``end_to_end_ohms``; everything a caller needs follows from those, so the
    service and the gain controller can work against any pot without knowing
    which one is fitted.
    """

    max_position: int
    series_ohms: float
    wiper_ohms: float
    end_to_end_ohms: float

    def _constants(self) -> dict:
        return {
            "max_position": self.max_position,
            "series_ohms": self.series_ohms,
            "wiper_ohms": self.wiper_ohms,
            "end_to_end_ohms": self.end_to_end_ohms,
        }

    def resistance_at(self, position: int) -> float:
        """What R17 presents at ``position``."""
        return resistance_ohms(position, **self._constants())

    def preamp_at(self, position: int) -> float:
        """The preamp feedback resistance at ``position``."""
        return preamp_feedback_ohms(self.resistance_at(position))

    def percent_at(self, position: int) -> float:
        """``position`` as a share of the wiper's travel."""
        return sensitivity_percent(position, self.max_position)

    def step_for_resistance(self, ohms: float) -> int:
        """The step closest to an R17 value."""
        return position_for_resistance(ohms, **self._constants())

    def gain_range(self) -> float:
        """The gain ratio between the wiper's two end stops.

        This is the whole authority a closed loop has over the detector, and
        it is what decides whether auto gain can do anything useful.
        """
        return self.preamp_at(self.max_position) / self.preamp_at(0)


class DigitalPotentiometer(Protocol):  # pylint: disable=unnecessary-ellipsis
    """What the service needs from a pot driver: state, motion, and maths.

    Declared flat rather than inheriting :class:`ResistanceModel`, because a
    Protocol cannot inherit a concrete class. Drivers get the maths half by
    mixing that class in; this only describes the resulting surface.
    """

    #: False when the wiper is volatile, so the service must persist it.
    persists_in_hardware: bool
    max_position: int
    series_ohms: float

    @property
    def position(self):
        """Live wiper step, or None when the device is closed."""
        ...  # pylint: disable=unnecessary-ellipsis

    def open(self) -> None:
        """Make the device ready for use."""
        ...  # pylint: disable=unnecessary-ellipsis

    def set_position(self, position: int, *, store: bool = False) -> int:
        """Move the wiper to ``position`` and return it."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the device."""
        ...  # pylint: disable=unnecessary-ellipsis

    def resistance_at(self, position: int) -> float:
        """What R17 presents at ``position``."""
        ...  # pylint: disable=unnecessary-ellipsis

    def preamp_at(self, position: int) -> float:
        """The preamp feedback resistance at ``position``."""
        ...  # pylint: disable=unnecessary-ellipsis

    def percent_at(self, position: int) -> float:
        """``position`` as a share of the wiper's travel."""
        ...  # pylint: disable=unnecessary-ellipsis

    def step_for_resistance(self, ohms: float) -> int:
        """The step closest to an R17 value."""
        ...  # pylint: disable=unnecessary-ellipsis

    def gain_range(self) -> float:
        """The gain ratio between the wiper's two end stops."""
        ...  # pylint: disable=unnecessary-ellipsis
