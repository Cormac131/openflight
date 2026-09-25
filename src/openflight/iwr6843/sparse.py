"""Two-step IWR6843 transfer: residual power, then track samples.

The firmware freezes the ring and sends the vertical MTI power the ball
tracker already searches. The host names the (frame, local bin) cells on
that track. The firmware returns complex samples for those cells only.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np

from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump
from openflight.iwr6843.tracking import BallTrack, Geometry

POWER_MAGIC = b"ILP1"
SLICE_MAGIC = b"ILS1"
# Must match L3_SPARSE_REQUEST_MAX in firmware/iwr6843/dump_format.h. The
# firmware reads the cell request into a buffer this size, including the NUL.
SPARSE_REQUEST_MAX_BYTES = 768
# Cells sampled away from the track so LCMF gets a per-sample MTI noise floor.
NOISE_CELLS_PER_FRAME = 1
# Keep noise cells this many bins away from any track or club cell.
NOISE_CELL_GUARD_BINS = 3

_POWER_HEADER = struct.Struct("<4sHHHHHHHf")
_SLICE_HEADER = struct.Struct("<4sH")
_CELL = struct.Struct("<HH")

Cell = tuple[int, int]


def power_packet_size(header: bytes) -> int:
    """Byte length of an ILP1 packet, or the header size when ``header`` is short."""
    if len(header) < _POWER_HEADER.size:
        return _POWER_HEADER.size
    _magic, frames, loops, bins, *_rest = _POWER_HEADER.unpack_from(header)
    return _POWER_HEADER.size + frames * 2 + frames * loops * bins * 4


def slice_packet_size(header: bytes, summary: PowerSummary) -> int:
    """Byte length of an ILS1 packet, or the header size when ``header`` is short."""
    if len(header) < _SLICE_HEADER.size:
        return _SLICE_HEADER.size
    _magic, count = _SLICE_HEADER.unpack_from(header)
    return _SLICE_HEADER.size + count * slice_stride(summary)


def slice_stride(summary: PowerSummary) -> int:
    """Bytes of one ILS1 cell, including its complex samples."""
    return _CELL.size + summary.n_loops * 2 * summary.n_rx * 4


@dataclass(frozen=True)
class PowerSummary:
    """Vertical residual power in tracker order, plus the geometry that made it."""

    power: np.ndarray
    n_tx: int
    n_rx: int
    n_loops: int
    geometry: Geometry


@dataclass(frozen=True)
class SparsePlan:
    """Cells to request, most important first, and the cells used for noise."""

    cells: tuple[Cell, ...]
    noise_cells: tuple[Cell, ...] = ()

    def request_order(self) -> list[Cell]:
        """Noise cells lead: without them the sparse capture has no SNR scale."""
        seen: set[Cell] = set()
        ordered: list[Cell] = []
        for cell in (*self.noise_cells, *self.cells):
            if cell not in seen:
                seen.add(cell)
                ordered.append(cell)
        return ordered


SlicePlanner = Callable[[PowerSummary], SparsePlan]


@dataclass(frozen=True)
class SparseCapture:
    """A range-snapshot dump rebuilt from the requested cells."""

    raw: bytes
    noise_power: float | None
    requested_cells: int
    sent_cells: int

    @property
    def truncated(self) -> bool:
        """True when the request limit dropped low-priority cells."""
        return self.sent_cells < self.requested_cells


def parse_power(raw: bytes) -> PowerSummary:
    """Decode one ILP1 power summary."""
    if len(raw) < _POWER_HEADER.size:
        raise ValueError("short IWR power summary")
    # The trailing float is reserved. Firmware sends 0; the host measures the
    # noise floor from dedicated noise cells instead.
    magic, frames, loops, bins, n_tx, n_rx, bin_start, period_us, _reserved = (
        _POWER_HEADER.unpack_from(raw)
    )
    if magic != POWER_MAGIC:
        raise ValueError("power summary missing ILP1")
    table_end = _POWER_HEADER.size + frames * 2
    if len(raw) < table_end:
        raise ValueError("short IWR power summary")
    starts = []
    counts = []
    for frame in range(frames):
        starts.append(raw[_POWER_HEADER.size + frame * 2])
        counts.append(raw[_POWER_HEADER.size + frame * 2 + 1])
    count = frames * loops * bins
    payload = raw[table_end : table_end + count * 4]
    if len(payload) != count * 4:
        raise ValueError("short IWR power summary")
    power = np.frombuffer(payload, dtype="<f4").reshape(frames * loops, bins)
    uniform = all(start == bin_start and width == bins for start, width in zip(starts, counts))
    geometry = Geometry(
        n_frames=frames,
        chirps_per_frame=loops * 2,
        n_tx=2,
        n_rx=n_rx,
        n_samples=bins,
        frame_period_s=period_us / 1e6 if period_us else 0.003,
        trigger_frame=0,
        range_bin_start=bin_start,
        range_fft_size=128,
        range_bin_starts=None if uniform else tuple(starts),
        range_bin_counts=None if uniform else tuple(counts),
    )
    return PowerSummary(
        power=power.copy(),
        n_tx=n_tx,
        n_rx=n_rx,
        n_loops=loops,
        geometry=geometry,
    )


def expand_cells(
    peaks: Iterable[tuple[int, float]],
    geometry: Geometry,
    *,
    margin: int = 1,
) -> list[Cell]:
    """(frame, absolute bin) peaks -> unique (frame, local bin) cells.

    Every center comes before any neighbor, so trimming the tail of the list
    drops the ``margin`` bins before the peaks themselves.
    """
    centers: list[tuple[int, int]] = [
        (int(frame), int(round(float(absolute)))) for frame, absolute in peaks
    ]
    cells: list[Cell] = []
    seen: set[Cell] = set()
    offsets = [0] + [sign * step for step in range(1, margin + 1) for sign in (-1, 1)]
    for offset in offsets:
        for frame, center in centers:
            bin_index = center + offset
            if not geometry.contains_bin(bin_index, frame=frame):
                continue
            key = (frame, geometry.local_bin(bin_index, frame))
            if key not in seen:
                seen.add(key)
                cells.append(key)
    return cells


def track_cells(track: BallTrack, geometry: Geometry, margin: int = 1) -> list[Cell]:
    """(frame, local bin) cells covering the fitted walk, centers first."""
    peaks = []
    for frame in range(geometry.n_frames):
        for loop in range(geometry.n_loops):
            time_s = geometry.loop_time(frame, loop)
            if time_s < track.t_first - 2e-3 or time_s > track.t_last + 2e-3:
                continue
            peaks.append((frame, track.bin_at(time_s)))
    return expand_cells(peaks, geometry, margin=margin)


def noise_cells(
    summary: PowerSummary,
    exclude: Iterable[Cell],
    *,
    per_frame: int = NOISE_CELLS_PER_FRAME,
    guard_bins: int = NOISE_CELL_GUARD_BINS,
) -> list[Cell]:
    """Per frame, the bins whose residual power sits nearest that frame's median.

    The median bin is the typical ball-free cell. Bins within ``guard_bins``
    of a track or club cell are skipped so the ball cannot inflate the floor.
    """
    geometry = summary.geometry
    blocked: dict[int, set[int]] = {}
    for frame, local in exclude:
        blocked.setdefault(frame, set()).update(range(local - guard_bins, local + guard_bins + 1))
    power = summary.power.reshape(geometry.n_frames, summary.n_loops, -1)
    chosen: list[Cell] = []
    for frame in range(geometry.n_frames):
        count = geometry.frame_bin_count(frame)
        per_bin = power[frame, :, :count].sum(axis=0)
        candidates = [local for local in range(count) if local not in blocked.get(frame, ())]
        if not candidates:
            continue
        median = float(np.median(per_bin[candidates]))
        distance = np.abs(per_bin - median)
        candidates.sort(key=lambda local, gap=distance: (float(gap[local]), local))
        chosen.extend((frame, local) for local in candidates[:per_frame])
    return chosen


def fit_cell_request(
    cells: list[Cell],
    max_bytes: int = SPARSE_REQUEST_MAX_BYTES,
) -> tuple[bytes, int]:
    """Encode as many leading cells as the firmware buffer holds.

    Returns the request line and how many cells it carries. The line plus its
    newline stays at least one byte under ``max_bytes`` so the firmware reads
    the newline instead of treating a full buffer as the end of the line.
    """
    budget = max_bytes - 1
    count = len(cells)
    while True:
        request = format_cell_request(cells[:count])
        if len(request) <= budget or count == 0:
            return request, count
        count -= 1


def format_cell_request(cells: list[Cell]) -> bytes:
    """ASCII request the firmware reads after the power summary."""
    parts = ["cells", str(len(cells))]
    for frame, local in cells:
        parts.append(str(frame))
        parts.append(str(local))
    return (" ".join(parts) + "\n").encode("ascii")


def _decode_slices(summary: PowerSummary, packet: bytes) -> dict[Cell, np.ndarray]:
    """ILS1 packet -> {(frame, local): complex samples [chirps, rx]}."""
    if len(packet) < _SLICE_HEADER.size:
        raise ValueError("short IWR slice packet")
    magic, count = _SLICE_HEADER.unpack_from(packet)
    if magic != SLICE_MAGIC:
        raise ValueError("slice packet missing ILS1")
    stride = slice_stride(summary)
    if len(packet) < _SLICE_HEADER.size + count * stride:
        raise ValueError("short IWR slice packet")
    chirps = summary.n_loops * 2
    columns: dict[Cell, np.ndarray] = {}
    offset = _SLICE_HEADER.size
    for _ in range(count):
        frame, local = _CELL.unpack_from(packet, offset)
        if frame >= summary.geometry.n_frames or local >= summary.geometry.frame_bin_count(frame):
            raise ValueError(f"IWR slice cell ({frame}, {local}) is outside the capture")
        samples = np.frombuffer(
            packet[offset + _CELL.size : offset + stride],
            dtype="<i2",
        )
        column = samples[1::2].astype(np.float32) + 1j * samples[0::2].astype(np.float32)
        columns[(int(frame), int(local))] = column.reshape(chirps, summary.n_rx)
        offset += stride
    return columns


def sparse_noise_power(columns: dict[Cell, np.ndarray], cells: Iterable[Cell]) -> float:
    """Median burst-MTI power per sample over the noise cells.

    Same estimator as ``PreparedShotDump.noise_power`` on a full cube: each
    TX's loop mean is removed, then the median of |x|^2 is taken.
    """
    samples = []
    for cell in cells:
        column = columns.get(cell)
        if column is None:
            continue
        chirps, n_rx = column.shape
        tdm = column.reshape(chirps // 2, 2, n_rx)
        mti = tdm - tdm.mean(axis=0, keepdims=True)
        samples.append((np.abs(mti) ** 2).reshape(-1))
    if not samples:
        raise ValueError("sparse capture returned no noise cells")
    noise = float(np.median(np.concatenate(samples)))
    if not math.isfinite(noise) or noise <= 0.0:
        raise ValueError(f"sparse noise floor must be positive, got {noise}")
    return noise


def assemble_capture(
    summary: PowerSummary,
    packet: bytes,
    plan: SparsePlan,
    *,
    requested_cells: int,
) -> SparseCapture:
    """Build a range-snapshot dump and its noise floor from an ILS1 packet."""
    columns = _decode_slices(summary, packet)
    frames = summary.geometry.n_frames
    bins = summary.power.shape[1]
    cube = np.zeros((frames, summary.n_loops * 2, summary.n_rx, bins), dtype=np.complex64)
    for (frame, local), column in columns.items():
        cube[frame, :, :, local] = column
    raw = pack_dump(
        cube,
        n_tx=2,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        version=4,
        range_bin_start=summary.geometry.range_bin_start,
        range_bin_starts=summary.geometry.range_bin_starts,
        range_bin_counts=summary.geometry.range_bin_counts,
        frame_period_us=int(round(summary.geometry.frame_period_s * 1e6)),
    )
    return SparseCapture(
        raw=raw,
        noise_power=sparse_noise_power(columns, plan.noise_cells),
        requested_cells=requested_cells,
        sent_cells=len(columns),
    )
