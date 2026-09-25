"""IWR6843 sparse transfers: only the complex samples on the track.

Two firmware paths feed the same cell packet (ILS1):

* ``l3sparse``: the firmware freezes the ring and sends the vertical MTI power
  the ball tracker searches (ILP1). The host names the (frame, local bin)
  cells on that track, and the firmware returns those cells.
* ``l3track``: the firmware runs the same tracker and cell selection itself
  (``firmware/iwr6843/track_select.c``). It sends a layout header and its
  track (ILT1), then the cells, with no power map and no round trip.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump
from openflight.iwr6843.tracking import (
    BallTrack,
    Geometry,
    detection_peaks,
    find_ball_from_power,
    mti_filter,
)


def power_packet_size(header: bytes) -> int:
    """Byte length of an ILP1 packet, or the header size when ``header`` is short."""
    if len(header) < _POWER_HEADER.size:
        return _POWER_HEADER.size
    _magic, frames, loops, bins, *_rest = _POWER_HEADER.unpack_from(header)
    return _POWER_HEADER.size + frames * 2 + frames * loops * bins * 4


def slice_stride(summary: CaptureLayout) -> int:
    """Bytes of one ILS1 cell, including its complex samples."""
    return _CELL.size + summary.n_loops * 2 * summary.n_rx * 4


def track_packet_size(header: bytes) -> int:
    """Byte length of an ILT1 packet, or the header size when ``header`` is short."""
    if len(header) < _POWER_HEADER.size:
        return _POWER_HEADER.size
    frames = _POWER_HEADER.unpack_from(header)[1]
    return _POWER_HEADER.size + frames * 2 + _TRACK_RECORD.size


POWER_MAGIC = b"ILP1"
TRACK_MAGIC = b"ILT1"
SLICE_MAGIC = b"ILS1"
_POWER_HEADER = struct.Struct("<4sHHHHHHHf")
# found, inliers, slope bins/s, intercept bins, rms bins, first s, last s
_TRACK_RECORD = struct.Struct("<HHfffff")
_SLICE_HEADER = struct.Struct("<4sH")
_CELL = struct.Struct("<HH")
# The club search stays near the tee, so it keeps one bin either side.
CLUB_CELL_MARGIN = 1
# Range FFT length behind the firmware's range bins.
RANGE_FFT_SIZE = 128


@dataclass(frozen=True)
class CaptureLayout:
    """What the host needs to rebuild a dump from ILS1 cells."""

    n_tx: int
    n_rx: int
    n_loops: int
    noise_power: float
    geometry: Geometry

    def header_bytes(self, magic: bytes) -> bytes:
        """Pack the shared ILP1/ILT1 header and per-frame window table."""
        geometry = self.geometry
        frames = geometry.n_frames
        header = _POWER_HEADER.pack(
            magic,
            frames,
            self.n_loops,
            geometry.n_samples,
            self.n_tx,
            self.n_rx,
            geometry.frame_bin_start(0) if frames else 0,
            int(round(geometry.frame_period_s * 1e6)),
            np.float32(self.noise_power),
        )
        table = bytearray()
        for frame in range(frames):
            table.append(geometry.frame_bin_start(frame) & 0xFF)
            table.append(geometry.frame_bin_count(frame) & 0xFF)
        return header + bytes(table)

    def pack_slices(self, cube: np.ndarray, cells: list[tuple[int, int]]) -> bytes:
        """Pack complex samples for each (frame, local bin) cell."""
        vertical = _vertical_cube(cube, self.n_tx)
        body = bytearray()
        for frame, local in cells:
            body += _CELL.pack(frame, local)
            column = np.asarray(vertical[frame, :, :, local])
            interleaved = np.empty(column.size * 2, dtype="<i2")
            flat = column.reshape(-1)
            interleaved[0::2] = np.clip(np.round(flat.imag), -32768, 32767)
            interleaved[1::2] = np.clip(np.round(flat.real), -32768, 32767)
            body += interleaved.tobytes()
        return _SLICE_HEADER.pack(SLICE_MAGIC, len(cells)) + bytes(body)


@dataclass(frozen=True)
class PowerSummary(CaptureLayout):
    """Vertical residual power in tracker order, plus the geometry that made it."""

    power: np.ndarray

    def to_bytes(self) -> bytes:
        """Pack the summary the firmware sends before any complex samples."""
        return (
            self.header_bytes(POWER_MAGIC) + np.ascontiguousarray(self.power, dtype="<f4").tobytes()
        )


@dataclass(frozen=True)
class OnboardTrack:
    """The range walk the firmware fitted before it chose cells."""

    found: bool
    n_inliers: int
    slope_bins: float
    intercept_bins: float
    rms_bins: float
    t_first: float
    t_last: float

    def to_bytes(self) -> bytes:
        """Pack the ILT1 track record."""
        return _TRACK_RECORD.pack(
            1 if self.found else 0,
            self.n_inliers,
            self.slope_bins,
            self.intercept_bins,
            self.rms_bins,
            self.t_first,
            self.t_last,
        )

    def ball_track(self, range_res_m: float) -> BallTrack | None:
        """The firmware walk as a BallTrack, or None when it found no ball.

        The firmware skips the quadratic refit, so ``quad_bins`` is None and
        the local speed is the line slope.
        """
        if not self.found:
            return None
        return BallTrack(
            speed_ms=self.slope_bins * range_res_m,
            slope_bins=self.slope_bins,
            intercept_bins=self.intercept_bins,
            rms_bins=self.rms_bins,
            n_inliers=self.n_inliers,
            t_first=self.t_first,
            t_last=self.t_last,
            low_confidence=bool(self.rms_bins >= 0.45 or self.t_last - self.t_first < 0.012),
        )


def _vertical_cube(cube: np.ndarray, n_tx: int) -> np.ndarray:
    """Keep the TX pair the vertical estimator uses."""
    if n_tx == 2:
        return cube
    if n_tx != 3:
        raise ValueError(f"unsupported TX count {n_tx}")
    frames, chirps, n_rx, bins = cube.shape
    loops = chirps // n_tx
    tdm = cube.reshape(frames, loops, n_tx, n_rx, bins)
    return tdm[:, :, [0, 2]].reshape(frames, loops * 2, n_rx, bins)


def vertical_loop_power(
    cube: np.ndarray,
    *,
    n_tx: int,
    geometry: Geometry | None = None,
) -> PowerSummary:
    """Burst-MTI residual power, matching ``tracking.loop_power``."""
    vertical = _vertical_cube(cube, n_tx)
    mti = mti_filter(vertical, range_domain=True)
    power = (np.abs(mti) ** 2).sum(axis=(1, 3))
    power = power.reshape(power.shape[0] * power.shape[1], power.shape[-1])
    frames, chirps, n_rx, bins = vertical.shape
    loops = chirps // 2
    if geometry is None:
        geometry = Geometry(
            n_frames=frames,
            chirps_per_frame=chirps,
            n_tx=2,
            n_rx=n_rx,
            n_samples=bins,
            frame_period_s=0.003,
            trigger_frame=0,
        )
    valid = np.abs(mti).reshape(-1) ** 2
    return PowerSummary(
        power=power.astype(np.float32),
        n_tx=n_tx,
        n_rx=n_rx,
        n_loops=loops,
        noise_power=float(np.median(valid)),
        geometry=geometry,
    )


def _parse_layout(raw: bytes, magic: bytes, label: str) -> tuple[CaptureLayout, int]:
    """Decode the shared ILP1/ILT1 header. Returns the layout and its byte length."""
    if len(raw) < _POWER_HEADER.size:
        raise ValueError(f"short IWR {label}")
    found, frames, loops, bins, n_tx, n_rx, bin_start, period_us, noise = _POWER_HEADER.unpack_from(
        raw
    )
    if found != magic:
        raise ValueError(f"{label} missing {magic.decode()}")
    table_end = _POWER_HEADER.size + frames * 2
    if len(raw) < table_end:
        raise ValueError(f"short IWR {label}")
    starts = []
    counts = []
    for frame in range(frames):
        starts.append(raw[_POWER_HEADER.size + frame * 2])
        counts.append(raw[_POWER_HEADER.size + frame * 2 + 1])
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
        range_fft_size=RANGE_FFT_SIZE,
        range_bin_starts=None if uniform else tuple(starts),
        range_bin_counts=None if uniform else tuple(counts),
    )
    layout = CaptureLayout(
        n_tx=n_tx,
        n_rx=n_rx,
        n_loops=loops,
        noise_power=float(noise),
        geometry=geometry,
    )
    return layout, table_end


def parse_power(raw: bytes) -> PowerSummary:
    """Decode one ILP1 power summary."""
    layout, table_end = _parse_layout(raw, POWER_MAGIC, "power summary")
    rows = layout.geometry.n_frames * layout.n_loops
    bins = layout.geometry.n_samples
    count = rows * bins
    payload = raw[table_end : table_end + count * 4]
    if len(payload) != count * 4:
        raise ValueError("short IWR power summary")
    power = np.frombuffer(payload, dtype="<f4").reshape(rows, bins)
    return PowerSummary(
        power=power.copy(),
        n_tx=layout.n_tx,
        n_rx=layout.n_rx,
        n_loops=layout.n_loops,
        noise_power=layout.noise_power,
        geometry=layout.geometry,
    )


def parse_track(raw: bytes) -> tuple[CaptureLayout, OnboardTrack]:
    """Decode one ILT1 packet: the capture layout and the firmware's track."""
    layout, table_end = _parse_layout(raw, TRACK_MAGIC, "track packet")
    record = raw[table_end : table_end + _TRACK_RECORD.size]
    if len(record) != _TRACK_RECORD.size:
        raise ValueError("short IWR track packet")
    found, inliers, slope, intercept, rms, t_first, t_last = _TRACK_RECORD.unpack(record)
    track = OnboardTrack(
        found=bool(found),
        n_inliers=int(inliers),
        slope_bins=float(slope),
        intercept_bins=float(intercept),
        rms_bins=float(rms),
        t_first=float(t_first),
        t_last=float(t_last),
    )
    return layout, track


def plan_cells(
    summary: PowerSummary,
    *,
    max_range_m: float | None,
    club_gate_m: tuple[float, float] | None,
) -> list[tuple[int, int]]:
    """Cells LCMF and club path need: the ball walk plus club-gate peaks.

    ``firmware/iwr6843/track_select.c`` ports this function for ``l3track``.
    Change both together; the parity test compares them.
    """
    geometry = summary.geometry
    track = find_ball_from_power(summary.power, geometry, max_range_m=max_range_m)
    cells = track_cells(track, geometry) if track is not None else []
    if club_gate_m is None:
        return cells
    rows, bins = detection_peaks(summary.power, geometry, gates_m=(club_gate_m,))
    seen = set(cells)
    for row, absolute in zip(rows, bins):
        frame = int(row) // summary.n_loops
        center = int(round(float(absolute)))
        for offset in range(-CLUB_CELL_MARGIN, CLUB_CELL_MARGIN + 1):
            bin_index = center + offset
            if not geometry.contains_bin(bin_index, frame=frame):
                continue
            key = (frame, geometry.local_bin(bin_index, frame))
            if key not in seen:
                seen.add(key)
                cells.append(key)
    return cells


def track_cells(track: BallTrack, geometry: Geometry, margin: int = 1) -> list[tuple[int, int]]:
    """(frame, local bin) cells covering the fitted walk, including neighbors."""
    cells: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for frame in range(geometry.n_frames):
        for loop in range(geometry.n_loops):
            time_s = geometry.loop_time(frame, loop)
            if time_s < track.t_first - 2e-3 or time_s > track.t_last + 2e-3:
                continue
            absolute = int(round(track.bin_at(time_s)))
            for offset in range(-margin, margin + 1):
                bin_index = absolute + offset
                if not geometry.contains_bin(bin_index, frame=frame):
                    continue
                local = geometry.local_bin(bin_index, frame)
                key = (frame, local)
                if key in seen:
                    continue
                seen.add(key)
                cells.append(key)
    return cells


def parse_slices(raw: bytes) -> list[tuple[int, int]]:
    """Return the (frame, local bin) cells stored in an ILS1 packet."""
    if len(raw) < _SLICE_HEADER.size:
        raise ValueError("short IWR slice packet")
    magic, count = _SLICE_HEADER.unpack_from(raw)
    if magic != SLICE_MAGIC:
        raise ValueError("slice packet missing ILS1")
    body = len(raw) - _SLICE_HEADER.size
    if count == 0:
        return []
    if body % count != 0:
        raise ValueError("uneven IWR slice packet")
    stride = body // count
    cells: list[tuple[int, int]] = []
    offset = _SLICE_HEADER.size
    for _ in range(count):
        frame, local = _CELL.unpack_from(raw, offset)
        cells.append((int(frame), int(local)))
        offset += stride
    return cells


def format_cell_request(cells: list[tuple[int, int]]) -> bytes:
    """ASCII request the firmware reads after the power summary."""
    parts = ["cells", str(len(cells))]
    for frame, local in cells:
        parts.append(str(frame))
        parts.append(str(local))
    return (" ".join(parts) + "\n").encode("ascii")


def assemble_dump(summary: CaptureLayout, packet: bytes) -> bytes:
    """Build a range-snapshot dump from an ILS1 cell packet."""
    frames = summary.geometry.n_frames
    chirps = summary.n_loops * 2
    n_rx = summary.n_rx
    bins = summary.geometry.n_samples
    cube = np.zeros((frames, chirps, n_rx, bins), dtype=np.complex64)
    if len(packet) < _SLICE_HEADER.size:
        raise ValueError("short IWR slice packet")
    magic, count = _SLICE_HEADER.unpack_from(packet)
    if magic != SLICE_MAGIC:
        raise ValueError("slice packet missing ILS1")
    stride = _CELL.size + chirps * n_rx * 4
    offset = _SLICE_HEADER.size
    for _ in range(count):
        frame, local = _CELL.unpack_from(packet, offset)
        samples = np.frombuffer(
            packet[offset + _CELL.size : offset + stride],
            dtype="<i2",
        )
        column = samples[1::2].astype(np.float32) + 1j * samples[0::2].astype(np.float32)
        cube[frame, :, :, local] = column.reshape(chirps, n_rx)
        offset += stride
    return pack_dump(
        cube,
        n_tx=2,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        version=4,
        range_bin_start=summary.geometry.range_bin_start,
        range_bin_starts=summary.geometry.range_bin_starts,
        range_bin_counts=summary.geometry.range_bin_counts,
        frame_period_us=int(round(summary.geometry.frame_period_s * 1e6)),
    )


def reconstruct_dump(
    cube: np.ndarray,
    cells: list[tuple[int, int]],
    summary: PowerSummary,
) -> bytes:
    """Build a range-snapshot dump that preserves MTI on the requested cells."""
    vertical = _vertical_cube(cube, summary.n_tx)
    narrow = np.zeros_like(vertical)
    for frame, local in cells:
        narrow[frame, :, :, local] = vertical[frame, :, :, local]
    return pack_dump(
        narrow,
        n_tx=2,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
        version=4,
        range_bin_start=summary.geometry.range_bin_start,
        frame_period_us=int(round(summary.geometry.frame_period_s * 1e6)),
    )
