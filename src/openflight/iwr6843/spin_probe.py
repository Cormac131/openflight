"""Spin research tooling: does the radar return from the ball carry a spin observable?

EXPERIMENTAL. Nothing here feeds a shot; it exists so the question can be
answered from recorded data before any spin number is promised. Two avenues
from the roadmap are instrumented:

1. Micro-Doppler. A translating ball gives one Doppler tone; a rotating one
   adds structure around it, because scatterers on the surface approach and
   recede at up to omega * R (about 2 m/s at 3000 rpm). Over a burst of
   loops at the ball's range bin the bulk phase progression is measured
   (lag-1, as the firmware does), removed, and the residual spectrum over
   the loops examined: its spectral spread (second moment) and the share of
   power off the bulk tone are the candidate observables.
2. Phase and amplitude structure across the virtual antennas and time, kept
   as the per-channel residual series for offline inspection.

``ball_roi`` cuts the region of interest (frames x loops x tx x rx x a few
bins around the tracked ball) out of a dump so it can be stored on its own
after most processing moves onboard. ``classify_signature`` is a coarse
three-way label whose thresholds are placeholders until stationary, low-spin
and high-spin balls have been recorded; it says so in its output.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.firmware_replay import frame_window, vertical_tx_indices
from openflight.iwr6843.tracking import RANGE_SPAN_M, same_tx_loop_period_s

WAVELENGTH_M = 0.00484
BALL_RADIUS_M = 0.02135

# Placeholder thresholds on the spectral spread (m/s of equivalent velocity)
# until reference balls have been recorded. Stated in the report.
# Thresholds are in units of the burst's own Doppler resolution (wavelength /
# (2 T N) for N loops T apart: 0.75 m/s at 24 loops of 135 us), because a
# windowed single tone already spreads over about one resolution cell.
SPREAD_STATIONARY_CELLS = 1.0
SPREAD_LOW_SPIN_CELLS = 2.5
THRESHOLDS_STATUS = "placeholder thresholds; record stationary, low-spin and high-spin balls"


@dataclass(frozen=True)
class BallRoi:
    """The ball's region of interest across frames: [frames, loops, tx, rx, bins]."""

    frames: tuple[int, ...]
    first_bin: int  # global bin of the ROI's first column
    data: np.ndarray
    loop_period_s: float

    @property
    def bins(self) -> int:
        return self.data.shape[-1]


def ball_roi(
    raw: bytes,
    *,
    frames: list[int] | tuple[int, ...] | range,
    center_bin: int,
    half_width: int = 2,
    loop_period_s: float | None = None,
) -> BallRoi:
    """Cut the ROI around a global range bin from a dump's frames.

    The window must hold every requested bin in every requested frame; the
    ROI keeps every TX so the azimuth element's phase is available too.
    """
    meta, cube = parse_dump(raw)
    n_tx = int(meta["n_tx"])
    loops = cube.shape[1] // n_tx
    first, last = center_bin - half_width, center_bin + half_width
    blocks = []
    for frame in frames:
        start, count = frame_window(meta, frame)
        if first < start or last >= start + count:
            raise ValueError(
                f"frame {frame}: bins {first}..{last} outside its window {start}+{count}"
            )
        block = cube[frame, :, :, first - start : last - start + 1]
        blocks.append(block.reshape(loops, n_tx, cube.shape[2], -1))
    return BallRoi(
        frames=tuple(int(f) for f in frames),
        first_bin=first,
        data=np.stack(blocks),
        loop_period_s=loop_period_s or same_tx_loop_period_s(n_tx),
    )


@dataclass(frozen=True)
class MicroDoppler:
    """One frame's residual Doppler structure at the ball's bin."""

    frame: int
    bulk_velocity_mps: float  # from the lag-1 phase, aliased like the firmware's
    spread_mps: float  # RMS velocity spread of the residual spectrum
    off_bulk_fraction: float  # power outside the bulk tone's bin, 0..1
    spectrum: np.ndarray  # |FFT| over loops after bulk removal, zero-frequency centred
    velocity_axis: np.ndarray
    resolution_mps: float  # one Doppler cell of the burst

    @property
    def spread_cells(self) -> float:
        return self.spread_mps / self.resolution_mps if self.resolution_mps > 0 else 0.0


def _bulk_phase(series: np.ndarray) -> float:
    lag1 = np.sum(series[1:] * np.conj(series[:-1]))
    return float(np.angle(lag1)) if lag1 != 0 else 0.0


def micro_doppler(
    roi: BallRoi, *, bin_offset: int | None = None, pad: int = 4
) -> list[MicroDoppler]:
    """Per frame: remove the loop mean and the bulk phase progression, then the
    spectrum over the loops of the vertical channels summed coherently.

    ``bin_offset`` selects the ROI column (default the centre); ``pad``
    zero-pads the loop FFT for a smoother spectrum. Velocity axis is the
    unambiguous span +/- wavelength / (4 T) of one loop period.
    """
    frames, loops, n_tx, _, bins = roi.data.shape
    column = bins // 2 if bin_offset is None else bin_offset
    vertical = list(vertical_tx_indices(n_tx))
    n_fft = loops * pad
    resolution = WAVELENGTH_M / (2.0 * roi.loop_period_s * loops)
    velocity_axis = np.fft.fftshift(np.fft.fftfreq(n_fft, d=roi.loop_period_s)) * WAVELENGTH_M / 2.0
    out: list[MicroDoppler] = []
    for index in range(frames):
        channels = roi.data[index, :, vertical, :, column]  # [tx, loops, rx]
        channels = np.moveaxis(channels, 0, 1)  # [loops, tx, rx]
        residual = channels - channels.mean(axis=0, keepdims=True)
        combined = residual.reshape(loops, -1).sum(axis=1)
        phase = _bulk_phase(combined)
        bulk_velocity = phase * WAVELENGTH_M / (4.0 * np.pi * roi.loop_period_s)
        demodulated = combined * np.exp(-1j * phase * np.arange(loops))
        window = np.hanning(loops) if loops > 2 else np.ones(loops)
        spectrum = np.abs(np.fft.fftshift(np.fft.fft(demodulated * window, n=n_fft)))
        power = spectrum**2
        total = float(power.sum())
        if total <= 0.0:
            out.append(
                MicroDoppler(
                    roi.frames[index], bulk_velocity, 0.0, 0.0, spectrum, velocity_axis, resolution
                )
            )
            continue
        centre = int(np.argmax(power))
        spread = float(
            np.sqrt(np.sum(power * (velocity_axis - velocity_axis[centre]) ** 2) / total)
        )
        main_lobe = max(1, pad)  # bins of the zero-padded FFT that one loop bin covers
        keep = slice(max(0, centre - main_lobe), min(n_fft, centre + main_lobe + 1))
        off_bulk = float(1.0 - power[keep].sum() / total)
        out.append(
            MicroDoppler(
                roi.frames[index],
                bulk_velocity,
                spread,
                off_bulk,
                spectrum,
                velocity_axis,
                resolution,
            )
        )
    return out


def surface_speed_mps(spin_rpm: float, radius_m: float = BALL_RADIUS_M) -> float:
    """Tangential speed of the ball's surface: the largest micro-Doppler a scatterer can show."""
    return spin_rpm * 2.0 * np.pi / 60.0 * radius_m


@dataclass(frozen=True)
class SpinSignature:
    label: str  # stationary, low-spin, high-spin
    median_spread_mps: float
    median_spread_cells: float
    median_off_bulk: float
    frames: int
    status: str = THRESHOLDS_STATUS


def classify_signature(results: list[MicroDoppler]) -> SpinSignature:
    """A coarse label from the median spectral spread in Doppler cells; the
    thresholds are placeholders."""
    if not results:
        raise ValueError("no micro-Doppler frames to classify")
    spread = float(np.median([r.spread_mps for r in results]))
    cells = float(np.median([r.spread_cells for r in results]))
    off_bulk = float(np.median([r.off_bulk_fraction for r in results]))
    if cells < SPREAD_STATIONARY_CELLS:
        label = "stationary"
    elif cells < SPREAD_LOW_SPIN_CELLS:
        label = "low-spin"
    else:
        label = "high-spin"
    return SpinSignature(label, spread, cells, off_bulk, len(results))


def format_report(results: list[MicroDoppler], signature: SpinSignature) -> str:
    lines = [
        f"spin probe: {signature.label} (median spread {signature.median_spread_mps:.2f} m/s = "
        f"{signature.median_spread_cells:.1f} cells, off-bulk {100 * signature.median_off_bulk:.0f}%, {signature.frames} frames)",
        f"  {signature.status}",
    ]
    for r in results:
        lines.append(
            f"  frame={r.frame} bulk={r.bulk_velocity_mps:+.2f} m/s spread={r.spread_mps:.2f} m/s "
            f"off-bulk={100 * r.off_bulk_fraction:.0f}%"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class PathDifference:
    """One frame's micro-Doppler numbers under two processing paths (IQ16 and emulated IQ8)."""

    frame: int
    bulk_velocity_a_mps: float
    bulk_velocity_b_mps: float
    spread_a_mps: float
    spread_b_mps: float
    off_bulk_a: float
    off_bulk_b: float
    spectrum_correlation: float  # 0..1 between the two normalised spectra

    @property
    def spread_ratio(self) -> float | None:
        return self.spread_b_mps / self.spread_a_mps if self.spread_a_mps > 0 else None


def compare_paths(
    results_a: list[MicroDoppler], results_b: list[MicroDoppler]
) -> list[PathDifference]:
    """Frame by frame, the same ROI processed two ways (roadmap phase 23: does the
    spin observable survive IQ8?). Frames are matched by number."""
    by_frame = {r.frame: r for r in results_b}
    out: list[PathDifference] = []
    for a in results_a:
        b = by_frame.get(a.frame)
        if b is None:
            continue
        sa = a.spectrum / (np.linalg.norm(a.spectrum) or 1.0)
        sb = b.spectrum / (np.linalg.norm(b.spectrum) or 1.0)
        out.append(
            PathDifference(
                frame=a.frame,
                bulk_velocity_a_mps=a.bulk_velocity_mps,
                bulk_velocity_b_mps=b.bulk_velocity_mps,
                spread_a_mps=a.spread_mps,
                spread_b_mps=b.spread_mps,
                off_bulk_a=a.off_bulk_fraction,
                off_bulk_b=b.off_bulk_fraction,
                spectrum_correlation=float(np.clip(np.dot(sa, sb), 0.0, 1.0)),
            )
        )
    return out


def format_comparison(
    differences: list[PathDifference], *, label_a: str = "iq16", label_b: str = "iq8"
) -> str:
    if not differences:
        return "spin probe A/B: no frames in common"
    ratios = [d.spread_ratio for d in differences if d.spread_ratio is not None]
    corr = float(np.median([d.spectrum_correlation for d in differences]))
    lines = [
        f"spin probe A/B {label_a} vs {label_b}: {len(differences)} frames, median spread ratio "
        f"{(np.median(ratios) if ratios else float('nan')):.2f}, median spectrum correlation {corr:.3f}",
        f"  {'frame':>5} {'bulk ' + label_a:>10} {'bulk ' + label_b:>10} {'spread ' + label_a:>12} "
        f"{'spread ' + label_b:>12} {'off-bulk ' + label_a:>13} {'off-bulk ' + label_b:>13} {'corr':>6}",
    ]
    for d in differences:
        lines.append(
            f"  {d.frame:>5} {d.bulk_velocity_a_mps:>+10.2f} {d.bulk_velocity_b_mps:>+10.2f} "
            f"{d.spread_a_mps:>12.2f} {d.spread_b_mps:>12.2f} {100 * d.off_bulk_a:>12.0f}% "
            f"{100 * d.off_bulk_b:>12.0f}% {d.spectrum_correlation:>6.3f}"
        )
    return "\n".join(lines)


def bin_of_range(range_m: float, fft_size: int = 128) -> int:
    return int(range_m / (RANGE_SPAN_M / fft_size))


__all__ = [
    "BALL_RADIUS_M",
    "SPREAD_LOW_SPIN_CELLS",
    "SPREAD_STATIONARY_CELLS",
    "THRESHOLDS_STATUS",
    "WAVELENGTH_M",
    "BallRoi",
    "MicroDoppler",
    "PathDifference",
    "SpinSignature",
    "ball_roi",
    "bin_of_range",
    "classify_signature",
    "compare_paths",
    "format_comparison",
    "format_report",
    "micro_doppler",
    "surface_speed_mps",
]
