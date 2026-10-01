"""Spin research tooling: does the radar return from the ball carry a spin observable?

EXPERIMENTAL. Nothing here feeds a shot; it exists so the question can be
answered from recorded data before any spin number is promised. Two avenues
from the roadmap are instrumented:

1. Micro-Doppler. A translating ball gives one Doppler tone; a rotating one
   adds structure around it, because scatterers on the surface approach and
   recede at up to omega * R (about 6.7 m/s at 3000 rpm). Over a burst of
   loops at the ball's range bin the bulk phase progression is measured
   (lag-1, as the firmware does), removed, and the residual spectrum over
   the loops examined: its spectral spread (second moment) and the share of
   power off the bulk tone are the candidate observables.
2. Phase and amplitude structure across the virtual antennas and time, kept
   as the per-channel residual series for offline inspection.
3. Rotation rate. An asymmetric or marked ball swings its echo once per
   revolution. ``track_from_points`` takes the ball's range bin frame by
   frame from a reviewed label file or the firmware tracker (it recedes 2-3
   bins per frame, so a fixed bin loses it), and ``rotation_spectrum`` fits
   a sinusoid to the detrended echo power over every loop of every tracked
   frame. The window the ball spends in view
   (about 30-40 ms) bounds what this can see: a spin is only reported when
   the window holds at least 1.5 revolutions, so a driver's 2500 rpm in a
   30 ms window is reported as below the floor rather than guessed.

``ball_roi`` cuts the region of interest (frames x loops x tx x rx x a few
bins around the tracked ball) out of a dump so it can be stored on its own
after most processing moves onboard. ``classify_signature`` is a coarse
three-way label whose thresholds are placeholders until stationary, low-spin
and high-spin balls have been recorded; it says so in its output.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.firmware_replay import (
    frame_timestamps_us,
    frame_window,
    vertical_tx_indices,
)
from openflight.iwr6843.tracking import CHIRP_PERIOD_S, RANGE_SPAN_M, same_tx_loop_period_s

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

RANGE_BIN_M = RANGE_SPAN_M / 128  # every cfg's 128-point range FFT over a 6 m span

# Rotation line: a spin is only claimed when the window holds this many
# revolutions (fewer cannot be told apart from the range falloff trend).
ROTATION_MIN_REVOLUTIONS = 1.5
# Placeholder detection threshold on the best sinusoid: the share of the
# detrended log-power variance it explains. The 34 labelled swings in
# tests/radar/recordings (plain balls, no reference spin) reach 0.36 off the
# floor, so a claim needs more than that. A best sinusoid on the floor
# itself is never a detection: that is a line below the floor (or a range
# trend the quadratic does not hold) leaking up, not a measurement. Stated
# in the report until marked balls have been recorded with a reference.
ROTATION_MIN_FRACTION = 0.45
ROTATION_STATUS = "placeholder detection thresholds; record marked balls with a reference"


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


@dataclass(frozen=True)
class BallTrack:
    """The ball's range bin frame by frame, timed at the middle of each burst."""

    frames: tuple[int, ...]
    bins: tuple[float, ...]  # global, interpolated
    times_s: tuple[float, ...]  # mid-burst, from the dump's frame clock
    radial_mps: float  # least-squares slope of range over time: unaliased

    @property
    def span_s(self) -> float:
        return self.times_s[-1] - self.times_s[0]


def _mti_cube(frame_cube: np.ndarray, n_tx: int) -> np.ndarray:
    """[chirps, rx, bins] -> [loops, tx, rx, bins] with each bin's loop mean
    removed, so static clutter cancels and the moving ball stays."""
    chirps, n_rx, bins = frame_cube.shape
    loops = chirps // n_tx
    block = frame_cube[: loops * n_tx].reshape(loops, n_tx, n_rx, bins)
    return block - block.mean(axis=0, keepdims=True)


def track_from_points(raw: bytes, points: Iterable[tuple[int, float]]) -> BallTrack:
    """A ball track from (frame, global range bin) points: a reviewed label
    file's ball, or the firmware ball tracker's points from a replay.

    The spin probe does not find the ball itself: on recorded swings the ball
    is 5-10 dB over the window's median while the golfer and club, a few bins
    away, are 15-25 dB over it, and a peak follower jumps onto them.
    """
    meta, cube = parse_dump(raw)
    stamps_us = frame_timestamps_us(meta)
    half_burst_s = 0.5 * cube.shape[1] * CHIRP_PERIOD_S
    ordered = sorted((int(frame), float(b)) for frame, b in points)
    for frame, _ in ordered:
        if not 0 <= frame < cube.shape[0]:
            raise ValueError(f"frame {frame} is not in this {cube.shape[0]}-frame dump")
    if len({frame for frame, _ in ordered}) != len(ordered):
        raise ValueError("a ball track has one point per frame")
    if len(ordered) < 2:
        raise ValueError(f"{len(ordered)} ball point(s); a speed needs two frames")
    frames = tuple(frame for frame, _ in ordered)
    bins = tuple(b for _, b in ordered)
    times = tuple(stamps_us[frame] * 1e-6 + half_burst_s for frame in frames)
    slope = float(np.polyfit(np.asarray(times), np.asarray(bins), 1)[0])
    return BallTrack(frames, bins, times, slope * RANGE_BIN_M)


def follow_rois(raw: bytes, track: BallTrack, *, half_width: int = 2) -> list[BallRoi]:
    """One single-frame ROI per tracked frame, centred on the ball's bin. A
    frame whose window does not hold the whole ROI is left out (the ball at
    the window's edge); compare the count with the track's."""
    meta = parse_dump(raw)[0]
    rois = []
    for frame, b in zip(track.frames, track.bins):
        centre = int(round(b))
        start, count = frame_window(meta, frame)
        if start <= centre - half_width and centre + half_width < start + count:
            rois.append(ball_roi(raw, frames=[frame], center_bin=centre, half_width=half_width))
    return rois


@dataclass(frozen=True)
class RotationSpectrum:
    """The once-per-revolution line in the ball's echo power over the followed frames."""

    freqs_hz: np.ndarray  # scanned, from the floor up
    fraction: np.ndarray  # detrended log-power variance each sinusoid explains, 0..1
    peak_hz: float
    peak_fraction: float
    peak_to_median: float  # reported, not gated: the floor edge inflates it
    span_s: float  # first to last power sample
    samples: int
    at_floor: bool  # the best sinusoid is the lowest scanned frequency
    detected: bool
    status: str = ROTATION_STATUS

    @property
    def peak_rpm(self) -> float:
        return 60.0 * self.peak_hz

    @property
    def floor_hz(self) -> float:
        return ROTATION_MIN_REVOLUTIONS / self.span_s

    @property
    def floor_rpm(self) -> float:
        return 60.0 * self.floor_hz

    @property
    def resolution_rpm(self) -> float:
        """One Fourier cell of the window; a peak is located finer than this at good SNR."""
        return 60.0 / self.span_s

    @property
    def rotations(self) -> float:
        return self.peak_hz * self.span_s


def _echo_power_series(
    raw: bytes, track: BallTrack, half_width: int
) -> tuple[np.ndarray, np.ndarray]:
    """(times, power) per loop: MTI power summed over every channel and the
    bins around the ball. Summing power over the bins (not the complex values)
    keeps the series flat while the ball walks across them inside a burst."""
    meta, cube = parse_dump(raw)
    n_tx = int(meta["n_tx"])
    loop_s = same_tx_loop_period_s(n_tx)
    half_burst_s = 0.5 * cube.shape[1] * CHIRP_PERIOD_S
    times: list[np.ndarray] = []
    powers: list[np.ndarray] = []
    for frame, b, t_mid in zip(track.frames, track.bins, track.times_s):
        window_start, count = frame_window(meta, frame)
        centre = int(round(b)) - window_start
        lo, hi = max(0, centre - half_width), min(count, centre + half_width + 1)
        block = _mti_cube(cube[frame, ..., :count], n_tx)[..., lo:hi]
        powers.append((np.abs(block) ** 2).sum(axis=(1, 2, 3)))
        times.append(t_mid - half_burst_s + loop_s * np.arange(block.shape[0]))
    return np.concatenate(times), np.concatenate(powers)


def rotation_spectrum(
    raw: bytes,
    track: BallTrack,
    *,
    half_width: int = 3,
    f_min_hz: float = 15.0,
    f_max_hz: float = 250.0,
    oversample: int = 10,
) -> RotationSpectrum:
    """Scan for a sinusoid in the ball's detrended log echo power.

    The range falloff (and any slow aspect change) is a quadratic in time on
    the log power; each candidate frequency is scored by the variance a
    sinusoid explains beyond that quadratic, both fitted together. The scan
    starts at the larger of ``f_min_hz`` and the 1.5-revolution floor of the
    window. A mark seen twice per revolution (a stripe) reads at twice the
    spin; the report says so.
    """
    if not 0.0 < f_min_hz < f_max_hz:
        raise ValueError(f"band {f_min_hz}..{f_max_hz} Hz is empty")
    times, power = _echo_power_series(raw, track, half_width)
    t = times - times.mean()
    span = float(times[-1] - times[0])
    y = np.log(np.maximum(power, np.finfo(float).tiny))
    trend = np.vander(t, 3)
    base = y - trend @ np.linalg.lstsq(trend, y, rcond=None)[0]
    total = float(base @ base)
    lo = max(f_min_hz, ROTATION_MIN_REVOLUTIONS / span)
    freqs = np.arange(lo, f_max_hz, 1.0 / (oversample * span)) if lo < f_max_hz else np.empty(0)
    fraction = np.zeros(freqs.size)
    for index, f in enumerate(freqs):
        design = np.column_stack((trend, np.cos(2.0 * np.pi * f * t), np.sin(2.0 * np.pi * f * t)))
        resid = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
        fraction[index] = 1.0 - float(resid @ resid) / total if total > 0.0 else 0.0
    if freqs.size == 0:
        return RotationSpectrum(freqs, fraction, 0.0, 0.0, 0.0, span, int(t.size), False, False)
    peak = int(np.argmax(fraction))
    peak_hz = float(freqs[peak])
    if 0 < peak < freqs.size - 1:
        a, b, c = fraction[peak - 1], fraction[peak], fraction[peak + 1]
        denom = a - 2.0 * b + c
        if denom < 0.0:
            peak_hz += 0.5 * float(a - c) / float(denom) * float(freqs[1] - freqs[0])
    median = float(np.median(fraction))
    peak_fraction = float(fraction[peak])
    ratio = peak_fraction / median if median > 0.0 else float("inf")
    at_floor = peak == 0
    detected = peak_fraction >= ROTATION_MIN_FRACTION and not at_floor
    return RotationSpectrum(
        freqs, fraction, peak_hz, peak_fraction, ratio, span, int(t.size), at_floor, detected
    )


def format_track(track: BallTrack) -> str:
    return (
        f"ball track: {len(track.frames)} frames {track.frames[0]}-{track.frames[-1]}, "
        f"bins {track.bins[0]:.1f} -> {track.bins[-1]:.1f}, radial {track.radial_mps:.1f} m/s "
        f"over {1000.0 * track.span_s:.1f} ms"
    )


def format_rotation_report(result: RotationSpectrum, *, reference_rpm: float | None = None) -> str:
    if result.detected:
        verdict = f"line at {result.peak_rpm:.0f} rpm"
    elif not result.freqs_hz.size:
        verdict = "no line (the whole band is below the floor)"
    elif result.at_floor:
        verdict = "no line (the best fit sits on the floor: a line below the floor leaks up)"
    else:
        verdict = f"no line (best {result.peak_rpm:.0f} rpm)"
    lines = [
        f"spin rotation: {verdict}, explains {100 * result.peak_fraction:.0f}% of the detrended "
        f"power, {result.peak_to_median:.1f}x the scan median",
        f"  window {1000.0 * result.span_s:.1f} ms, {result.samples} samples, "
        f"{result.rotations:.1f} revolutions at the peak, "
        f"resolution {result.resolution_rpm:.0f} rpm, "
        f"floor {result.floor_rpm:.0f} rpm (slower spin is not observable in this window)",
        "  a mark seen twice per revolution (a stripe) reads at twice the spin",
        f"  {result.status}",
    ]
    if reference_rpm is not None:
        if reference_rpm < result.floor_rpm:
            lines.append(f"  reference {reference_rpm:.0f} rpm is below the floor of this window")
        elif result.detected:
            error = result.peak_rpm - reference_rpm
            half = result.peak_rpm / 2.0 - reference_rpm
            lines.append(
                f"  reference {reference_rpm:.0f} rpm: error {error:+.0f} rpm "
                f"(half the line: {half:+.0f} rpm)"
            )
        else:
            lines.append(f"  reference {reference_rpm:.0f} rpm: no line to compare")
    return "\n".join(lines)


def bin_of_range(range_m: float, fft_size: int = 128) -> int:
    return int(range_m / (RANGE_SPAN_M / fft_size))


__all__ = [
    "BALL_RADIUS_M",
    "RANGE_BIN_M",
    "ROTATION_MIN_REVOLUTIONS",
    "ROTATION_STATUS",
    "SPREAD_LOW_SPIN_CELLS",
    "SPREAD_STATIONARY_CELLS",
    "THRESHOLDS_STATUS",
    "WAVELENGTH_M",
    "BallRoi",
    "BallTrack",
    "RotationSpectrum",
    "MicroDoppler",
    "PathDifference",
    "SpinSignature",
    "ball_roi",
    "bin_of_range",
    "classify_signature",
    "compare_paths",
    "follow_rois",
    "format_comparison",
    "format_report",
    "format_rotation_report",
    "format_track",
    "micro_doppler",
    "rotation_spectrum",
    "track_from_points",
    "surface_speed_mps",
]
