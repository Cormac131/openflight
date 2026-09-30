"""Radar-front (radome) and RF-hood geometry for the IWR6843 enclosure.

The window's thickness depends on the material, not on the free-space
wavelength: inside a dielectric of relative permittivity ``eps_r`` the
wavelength is ``lambda_0 / sqrt(eps_r)``, and a lossless slab is transparent
at normal incidence when it is a whole number of half *material*
wavelengths thick. :func:`slab_transmission` is the single-layer slab model
(TE/TM, any incidence angle, dielectric loss), so a thickness can be judged
across the 60-64 GHz sweep and across the field of view rather than at one
point. TI's mmWave Radar Radome Design Guide (SWRA705) is the reference for
material choice (low ``eps_r`` and loss, uniform thickness, smooth surfaces,
no metallic paint) and treats the antenna-to-window standoff separately.

The material table holds nominal values for common print materials near
60 GHz. They vary with filament brand, colourant and infill, so treat them
as a starting point and measure a printed coupon when a design depends on
them (a naked-vs-window capture in the benchmark does this directly).

:class:`RadarFrontParams` names the model parameters the plan asks the CAD
to expose, so an RF test can change one at a time; ``parameter_rows`` gives
them as rows for a CAD parameter table.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import asdict, dataclass

C_MPS = 299_792_458.0
BAND_HZ = (60.0e9, 64.0e9)
CENTRE_HZ = 62.0e9


@dataclass(frozen=True)
class Material:
    """Relative permittivity and loss tangent near 60 GHz."""

    eps_r: float
    loss_tangent: float
    note: str = ""


MATERIALS = {
    "PTFE": Material(2.05, 0.0003, "reference low-loss plastic"),
    "PP": Material(2.25, 0.0005, "polypropylene"),
    "ABS": Material(2.5, 0.005),
    "ASA": Material(2.6, 0.008),
    "PETG": Material(2.7, 0.008),
    "PLA": Material(2.7, 0.010),
    "PC": Material(2.8, 0.006, "polycarbonate"),
}


def wavelength_m(freq_hz: float, eps_r: float = 1.0) -> float:
    """``lambda = c / (f sqrt(eps_r))``."""
    if freq_hz <= 0.0 or eps_r <= 0.0:
        raise ValueError("frequency and permittivity must be positive")
    return C_MPS / (freq_hz * math.sqrt(eps_r))


def half_wave_thicknesses_mm(
    eps_r: float, *, freq_hz: float = CENTRE_HZ, count: int = 4
) -> list[float]:
    """``n lambda_m / 2`` for n = 1..count, in millimetres: the lossless
    normal-incidence transparent thicknesses."""
    half = 0.5 * wavelength_m(freq_hz, eps_r) * 1e3
    return [n * half for n in range(1, count + 1)]


def slab_transmission(
    thickness_m: float,
    material: Material,
    *,
    freq_hz: float = CENTRE_HZ,
    angle_deg: float = 0.0,
    polarization: str = "TE",
) -> float:
    """One-way power transmission ``|T|^2`` of a flat slab in air.

    ``T = (1 - G^2) e^{-j d} / (1 - G^2 e^{-2 j d})`` with ``G`` the
    air-to-slab interface reflection for the polarisation and ``d`` the
    slab's normal phase thickness, ``eps_r`` complex with the loss tangent.
    """
    if thickness_m < 0.0:
        raise ValueError(f"thickness must be >= 0, got {thickness_m}")
    if polarization not in ("TE", "TM"):
        raise ValueError(f"polarization must be 'TE' or 'TM', got {polarization!r}")
    if not 0.0 <= angle_deg < 90.0:
        raise ValueError(f"angle must be in [0, 90) degrees, got {angle_deg}")
    k0 = 2.0 * math.pi * freq_hz / C_MPS
    eps = complex(material.eps_r, -material.eps_r * material.loss_tangent)
    sin2 = math.sin(math.radians(angle_deg)) ** 2
    kz0 = k0 * math.cos(math.radians(angle_deg))
    kz1 = k0 * cmath.sqrt(eps - sin2)
    if polarization == "TE":
        gamma = (kz0 - kz1) / (kz0 + kz1)
    else:
        gamma = (eps * kz0 - kz1) / (eps * kz0 + kz1)
    delta = kz1 * thickness_m
    t = (1.0 - gamma**2) * cmath.exp(-1j * delta) / (1.0 - gamma**2 * cmath.exp(-2j * delta))
    return float(abs(t) ** 2)


def two_way_loss_db(thickness_m: float, material: Material, **kwargs) -> float:
    """The radar's round-trip loss through the window, in dB (positive)."""
    return -20.0 * math.log10(max(slab_transmission(thickness_m, material, **kwargs), 1e-30))


def worst_two_way_loss_db(
    thickness_m: float,
    material: Material,
    *,
    band_hz: tuple[float, float] = BAND_HZ,
    max_angle_deg: float = 30.0,
    steps: int = 9,
) -> float:
    """The largest round-trip loss over the chirp band and the field of view,
    both polarisations: a thickness is only as good as its worst corner."""
    freqs = [band_hz[0] + (band_hz[1] - band_hz[0]) * i / (steps - 1) for i in range(steps)]
    angles = [max_angle_deg * i / (steps - 1) for i in range(steps)]
    return max(
        two_way_loss_db(thickness_m, material, freq_hz=f, angle_deg=a, polarization=p)
        for f in freqs
        for a in angles
        for p in ("TE", "TM")
    )


def best_thickness_mm(
    material: Material,
    *,
    min_mm: float = 0.6,
    max_mm: float = 6.0,
    step_mm: float = 0.02,
    max_angle_deg: float = 30.0,
) -> tuple[float, float]:
    """(thickness, worst round-trip loss dB) minimising the worst loss over
    the band and field of view within a printable range."""
    if not 0.0 < min_mm < max_mm or step_mm <= 0.0:
        raise ValueError("need 0 < min_mm < max_mm and a positive step")
    best = (min_mm, math.inf)
    n = int(round((max_mm - min_mm) / step_mm))
    for i in range(n + 1):
        mm = min_mm + i * step_mm
        loss = worst_two_way_loss_db(mm * 1e-3, material, max_angle_deg=max_angle_deg)
        if loss < best[1]:
            best = (mm, loss)
    return best


def standoff_candidates_mm(count: int = 6, *, freq_hz: float = CENTRE_HZ) -> list[float]:
    """Antenna-to-window distances at whole half free-space wavelengths, where
    the window's reflection returns to the antenna in phase: candidates to
    test against each other, not a recommendation."""
    half = 0.5 * wavelength_m(freq_hz) * 1e3
    return [n * half for n in range(1, count + 1)]


def hood_cutoff_deg(depth_mm: float, half_opening_mm: float) -> float:
    """Off-boresight angle beyond which a hood wall of ``depth`` shades the
    antenna centre, for an opening ``half_opening`` from the centre to that wall."""
    if depth_mm <= 0.0 or half_opening_mm <= 0.0:
        raise ValueError("depth and half opening must be positive")
    return math.degrees(math.atan2(half_opening_mm, depth_mm))


def hood_depth_for_cutoff_mm(half_opening_mm: float, cutoff_deg: float) -> float:
    """The wall depth that shades everything beyond ``cutoff`` on its side."""
    if not 0.0 < cutoff_deg < 90.0 or half_opening_mm <= 0.0:
        raise ValueError("cutoff must be in (0, 90) degrees and the half opening positive")
    return half_opening_mm / math.tan(math.radians(cutoff_deg))


@dataclass(frozen=True)
class RadarFrontParams:
    """The radar front's CAD parameters (millimetres and degrees), one per
    physical knob an RF test varies. The hood depths are per side, so the
    golfer's side can be made deeper (an asymmetric hood)."""

    radar_window_thickness: float
    radar_window_standoff: float
    radar_window_width: float = 40.0
    radar_window_height: float = 40.0
    radar_yaw: float = 0.0
    radar_pitch: float = 0.0
    hood_left_depth: float = 0.0
    hood_right_depth: float = 0.0
    hood_top_depth: float = 0.0
    hood_bottom_depth: float = 0.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name in ("radar_yaw", "radar_pitch"):
                continue
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if self.radar_window_width <= 0.0 or self.radar_window_height <= 0.0:
            raise ValueError("the window must have a positive width and height")

    def parameter_rows(self) -> list[tuple[str, float, str]]:
        """(name, value, unit) rows for a CAD user-parameter table."""
        return [
            (name, value, "deg" if name in ("radar_yaw", "radar_pitch") else "mm")
            for name, value in asdict(self).items()
        ]

    def hood_cutoffs_deg(self) -> dict[str, float | None]:
        """Each side's shading angle (None where that side has no wall)."""
        half = {
            "left": 0.5 * self.radar_window_width,
            "right": 0.5 * self.radar_window_width,
            "top": 0.5 * self.radar_window_height,
            "bottom": 0.5 * self.radar_window_height,
        }
        depths = {
            "left": self.hood_left_depth,
            "right": self.hood_right_depth,
            "top": self.hood_top_depth,
            "bottom": self.hood_bottom_depth,
        }
        return {
            side: hood_cutoff_deg(depths[side], opening) if depths[side] > 0.0 else None
            for side, opening in half.items()
        }
