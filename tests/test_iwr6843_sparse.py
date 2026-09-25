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


# --- l3track: firmware-planned cells --------------------------------------

import pytest  # noqa: E402

from openflight.iwr6843.driver import UnsupportedCommand  # noqa: E402
from openflight.iwr6843.sparse import (  # noqa: E402
    TRACK_MAGIC,
    CaptureLayout,
    OnboardTrack,
    parse_track,
    plan_cells,
    track_packet_size,
)

_TRACK = OnboardTrack(
    found=True,
    n_inliers=42,
    slope_bins=960.0,
    intercept_bins=49.5,
    rms_bins=0.25,
    t_first=0.001,
    t_last=0.030,
)


def _track_packet(summary, track=_TRACK) -> bytes:
    return summary.header_bytes(TRACK_MAGIC) + track.to_bytes()


class _StreamSerial:
    """Serves one fixed byte stream, whatever is written."""

    def __init__(self, stream: bytes):
        self._stream = stream
        self.written = b""

    @property
    def in_waiting(self) -> int:
        return len(self._stream)

    def read(self, count: int) -> bytes:
        chunk, self._stream = self._stream[:count], self._stream[count:]
        return chunk

    def write(self, data: bytes) -> None:
        self.written += data

    def reset_input_buffer(self) -> None:
        return None


def _radar(stream: bytes) -> IWR6843Radar:
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = _StreamSerial(stream)
    return radar


def test_track_packet_round_trip_keeps_windowed_layout():
    geometry = Geometry(
        n_frames=3,
        chirps_per_frame=8,
        n_tx=2,
        n_rx=4,
        n_samples=16,
        frame_period_s=0.003,
        trigger_frame=0,
        range_bin_start=20,
        range_fft_size=128,
        range_bin_starts=(20, 20, 32),
        range_bin_counts=(16, 16, 12),
    )
    layout = CaptureLayout(n_tx=3, n_rx=4, n_loops=4, noise_power=0.0, geometry=geometry)
    raw = _track_packet(layout)

    assert track_packet_size(raw) == len(raw)
    parsed, track = parse_track(raw)

    assert parsed.n_tx == 3 and parsed.n_loops == 4
    assert parsed.geometry.range_bin_starts == (20, 20, 32)
    assert parsed.geometry.range_bin_counts == (16, 16, 12)
    assert track.found and track.n_inliers == 42
    assert track.slope_bins == pytest.approx(960.0)
    assert track.t_last == pytest.approx(0.030)


def test_track_packet_size_before_header_arrives():
    assert track_packet_size(b"ILT1") == track_packet_size(b"")


def test_parse_track_rejects_power_packet_and_short_record():
    summary = vertical_loop_power(_cube(n_tx=2), n_tx=2)

    with pytest.raises(ValueError, match="missing ILT1"):
        parse_track(summary.to_bytes())
    with pytest.raises(ValueError, match="short"):
        parse_track(_track_packet(summary)[:-1])


def test_onboard_track_becomes_ball_track():
    res = 6.0 / 128
    track = _TRACK.ball_track(res)

    assert track.speed_ms == pytest.approx(960.0 * res)
    assert track.bin_at(0.01) == pytest.approx(960.0 * 0.01 + 49.5)
    assert track.quad_bins is None
    assert not track.low_confidence


def test_onboard_track_flags_short_or_ragged_walks():
    short = OnboardTrack(True, 9, 960.0, 49.5, 0.1, 0.001, 0.010)
    ragged = OnboardTrack(True, 30, 960.0, 49.5, 0.5, 0.001, 0.030)

    assert short.ball_track(0.05).low_confidence
    assert ragged.ball_track(0.05).low_confidence


def test_missing_onboard_track_is_none():
    assert OnboardTrack(False, 0, 0.0, 0.0, 0.0, 0.0, 0.0).ball_track(0.05) is None


def test_driver_reads_firmware_track_then_cells():
    cube = _cube(n_tx=2)
    summary = vertical_loop_power(cube, n_tx=2)
    cells = [(0, 1), (2, 4)]
    stream = b"l3track\n" + _track_packet(summary) + summary.pack_slices(cube, cells) + b"Done\n"
    radar = _radar(stream)

    raw, noise, track = radar.read_tracked()

    assert radar.ser.written == b"l3track\n"
    assert track == parse_track(_track_packet(summary))[1]
    assert noise == summary.noise_power
    _meta, rebuilt = parse_dump(raw)
    for frame, local in cells:
        expected = np.clip(np.round(cube[frame, :, :, local]), -32768, 32767)
        np.testing.assert_allclose(rebuilt[frame, :, :, local], expected)
    untouched = np.ones(rebuilt.shape[-1], dtype=bool)
    untouched[[1, 4]] = False
    assert not np.any(rebuilt[1][..., untouched])


def test_driver_track_with_zero_cells_is_an_empty_dump():
    summary = vertical_loop_power(_cube(n_tx=2), n_tx=2)
    no_ball = OnboardTrack(False, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    radar = _radar(_track_packet(summary, no_ball) + b"ILS1\x00\x00")

    raw, _noise, track = radar.read_tracked()

    assert not track.found
    _meta, rebuilt = parse_dump(raw)
    assert not np.any(rebuilt)


def test_old_firmware_without_l3track_raises_unsupported():
    radar = _radar(b"l3track\n'l3track' is not recognized as a CLI command\n")

    with pytest.raises(UnsupportedCommand):
        radar.read_tracked(timeout_s=0.2)


def test_firmware_refusal_before_streaming_returns_none():
    radar = _radar(b"l3track\nError: l3track needs trackCfg\n")

    assert radar.read_tracked(timeout_s=0.2) is None


def test_track_stream_that_breaks_mid_cells_raises():
    """The ring is already rearmed, so the caller must not fall back."""
    cube = _cube(n_tx=2)
    summary = vertical_loop_power(cube, n_tx=2)
    cells_packet = summary.pack_slices(cube, [(0, 1), (2, 4)])
    radar = _radar(_track_packet(summary) + cells_packet[:-10])

    with pytest.raises(RuntimeError, match="cell packet ended early"):
        radar.read_tracked(timeout_s=0.2)


def test_track_stream_that_breaks_in_header_raises():
    summary = vertical_loop_power(_cube(n_tx=2), n_tx=2)
    radar = _radar(_track_packet(summary)[:-3])

    with pytest.raises(RuntimeError, match="packet ended early"):
        radar.read_tracked(timeout_s=0.2)


def test_read_sparse_still_returns_none_on_old_firmware():
    radar = _radar(b"'l3sparse' is not recognized as a CLI command\n")

    assert radar.read_sparse(lambda _summary: [], timeout_s=0.2) is None


def test_plan_cells_without_club_gate_is_the_track_walk():
    cube = _cube(n_tx=2, frames=8, loops=6, bins=28)
    for frame in range(8):
        cube[frame, 0, :, 4 + 3 * frame] += 80
        cube[frame, 1, :, 4 + 3 * frame] += 80
    geometry = Geometry(
        n_frames=8,
        chirps_per_frame=12,
        n_tx=2,
        n_rx=4,
        n_samples=28,
        frame_period_s=0.003,
        trigger_frame=0,
        range_bin_start=50,
        range_fft_size=128,
    )
    summary = vertical_loop_power(cube, n_tx=2, geometry=geometry)
    track = find_ball(mti_filter(cube, range_domain=True), geometry)

    assert track is not None
    assert plan_cells(summary, max_range_m=None, club_gate_m=None) == track_cells(track, geometry)


def test_error_bytes_inside_the_cell_payload_are_not_a_cli_error():
    """Binary samples can spell 'Error'; only text before the magic counts."""
    cube = _cube(n_tx=2)
    summary = vertical_loop_power(cube, n_tx=2)
    cells_packet = bytearray(summary.pack_slices(cube, [(0, 1)]))
    cells_packet[10:15] = b"Error"
    radar = _radar(_track_packet(summary) + bytes(cells_packet))

    raw, _noise, _track = radar.read_tracked(timeout_s=0.5)

    assert raw.startswith(b"ILD1")
