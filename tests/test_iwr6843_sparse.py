"""Sparse IWR dumps: residual power, then only the complex samples on the track."""

from __future__ import annotations

import numpy as np

from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.sparse import (
    parse_power,
    parse_slices,
    reconstruct_dump,
    track_cells,
    vertical_loop_power,
)
from openflight.iwr6843.tracking import Geometry, find_ball, loop_power, mti_filter


def _cube(*, n_tx: int, frames: int = 6, loops: int = 4, n_rx: int = 4, bins: int = 16):
    rng = np.random.default_rng(1)
    chirps = loops * n_tx
    values = rng.normal(size=(frames, chirps, n_rx, bins)) + 1j * rng.normal(
        size=(frames, chirps, n_rx, bins)
    )
    return values.astype(np.complex64)


def test_vertical_power_matches_two_tx_tracker():
    """The summary is the same residual the ball tracker already searches."""
    cube = _cube(n_tx=2)
    power = vertical_loop_power(cube, n_tx=2)
    expected = loop_power(mti_filter(cube, range_domain=True))

    np.testing.assert_allclose(power.power, expected)


def test_vertical_power_uses_the_outer_transmitters():
    """Three-TX captures drop the middle TX before the vertical residual."""
    cube = _cube(n_tx=3)
    frames, chirps, n_rx, bins = cube.shape
    loops = chirps // 3
    pair = cube.reshape(frames, loops, 3, n_rx, bins)[:, :, [0, 2]].reshape(
        frames, loops * 2, n_rx, bins
    )
    power = vertical_loop_power(cube, n_tx=3)

    np.testing.assert_allclose(power.power, loop_power(mti_filter(pair, range_domain=True)))


def test_power_packet_round_trip():
    cube = _cube(n_tx=2)
    packet = vertical_loop_power(cube, n_tx=2)
    raw = packet.to_bytes() if hasattr(packet, "to_bytes") else None
    assert raw is not None
    parsed = parse_power(raw)

    np.testing.assert_allclose(parsed.power, packet.power)
    assert parsed.n_tx == 2
    assert parsed.n_loops == 4


def test_reconstructed_cells_keep_tracker_residual():
    """Complex samples on the track bins survive; the rest of the cube is absent."""
    cube = _cube(n_tx=2, frames=8, loops=6, bins=28)
    frames, chirps, _n_rx, bins = cube.shape
    # Three bins per 3 ms frame is about 47 m/s, inside the tracker gate.
    for frame in range(frames):
        center = 4 + 3 * frame
        # One loop, so burst MTI does not cancel the peak as static clutter.
        cube[frame, 0, :, center] += 80
        cube[frame, 1, :, center] += 80
    geometry = Geometry(
        n_frames=frames,
        chirps_per_frame=chirps,
        n_tx=2,
        n_rx=4,
        n_samples=bins,
        frame_period_s=0.003,
        trigger_frame=0,
        loop_period_s=90e-6,
        range_bin_start=50,
        range_fft_size=128,
    )
    summary = vertical_loop_power(cube, n_tx=2, geometry=geometry)
    track = find_ball(mti_filter(cube, range_domain=True), geometry, min_ball_ms=1.0)
    assert track is not None
    cells = track_cells(track, geometry)
    rebuilt = reconstruct_dump(cube, cells, summary)
    _meta, narrow = parse_dump(rebuilt)
    for frame, local in cells:
        expected = np.clip(np.round(cube[frame, :, :, local]), -32768, 32767)
        np.testing.assert_allclose(narrow[frame, :, :, local], expected)


class _FakeSerial:
    def __init__(self, first: bytes, follow: bytes):
        self._pending = [first]
        self._follow = follow
        self.written = b""

    @property
    def in_waiting(self) -> int:
        return len(self._pending[0]) if self._pending else 0

    def read(self, count: int) -> bytes:
        if not self._pending:
            return b""
        chunk = self._pending[0][:count]
        self._pending[0] = self._pending[0][count:]
        if not self._pending[0]:
            self._pending.pop(0)
        return chunk

    def write(self, data: bytes) -> None:
        self.written += data
        if data.startswith(b"cells"):
            self._pending.append(self._follow)

    def reset_input_buffer(self) -> None:
        return None


def test_driver_reads_power_then_requested_cells():
    """The host asks for track cells and rebuilds a dump from the reply."""
    cube = _cube(n_tx=2)
    summary = vertical_loop_power(cube, n_tx=2)
    cells = [(0, 1), (2, 4)]
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = _FakeSerial(b"echo\n" + summary.to_bytes(), summary.pack_slices(cube, cells))

    raw, noise = radar.read_sparse(lambda _summary: cells)

    assert raw.startswith(b"ILD1")
    assert noise == summary.noise_power
    _meta, rebuilt = parse_dump(raw)
    for frame, local in cells:
        expected = np.clip(np.round(cube[frame, :, :, local]), -32768, 32767)
        np.testing.assert_allclose(rebuilt[frame, :, :, local], expected)


def test_slice_packet_round_trip():
    cube = _cube(n_tx=2)
    cells = [(0, 2), (1, 3)]
    summary = vertical_loop_power(cube, n_tx=2)
    payload = summary.pack_slices(cube, cells)
    parsed = parse_slices(payload)

    assert parsed == cells or set(parsed) == set(cells)
