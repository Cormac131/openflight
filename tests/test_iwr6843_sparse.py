"""Sparse IWR dumps: residual power, then only the complex samples on the track."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from iwr6843_fakes import (
    FakeSparseSerial,
    power_packet,
    slice_packet,
    vertical_loop_power,
)

from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.iwr6843.shot import PreparedShotDump
from openflight.iwr6843.sparse import (
    SPARSE_REQUEST_MAX_BYTES,
    PowerSummary,
    SparsePlan,
    assemble_capture,
    expand_cells,
    fit_cell_request,
    format_cell_request,
    noise_cells,
    parse_power,
    sparse_noise_power,
    track_cells,
)
from openflight.iwr6843.tracking import Geometry, find_ball, loop_power, mti_filter

DUMP_FORMAT_H = Path(__file__).parents[1] / "firmware" / "iwr6843" / "dump_format.h"


def _cube(*, n_tx: int, frames: int = 6, loops: int = 4, n_rx: int = 4, bins: int = 16):
    rng = np.random.default_rng(1)
    chirps = loops * n_tx
    values = rng.normal(size=(frames, chirps, n_rx, bins)) + 1j * rng.normal(
        size=(frames, chirps, n_rx, bins)
    )
    return (values * 40).astype(np.complex64)


def _geometry(frames: int, loops: int, bins: int, *, start: int = 50) -> Geometry:
    return Geometry(
        n_frames=frames,
        chirps_per_frame=loops * 2,
        n_tx=2,
        n_rx=4,
        n_samples=bins,
        frame_period_s=0.003,
        trigger_frame=0,
        loop_period_s=90e-6,
        range_bin_start=start,
        range_fft_size=128,
    )


def _radar(serial) -> IWR6843Radar:
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = serial
    return radar


def _moving_ball_cube(frames: int = 8, loops: int = 6, bins: int = 28) -> np.ndarray:
    cube = _cube(n_tx=2, frames=frames, loops=loops, bins=bins)
    # Three bins per 3 ms frame is about 47 m/s, inside the tracker gate.
    for frame in range(frames):
        center = 4 + 3 * frame
        # One loop, so burst MTI does not cancel the peak as static clutter.
        cube[frame, 0, :, center] += 2000
        cube[frame, 1, :, center] += 2000
    return cube


# --- residual power ---------------------------------------------------------


def test_vertical_power_matches_two_tx_tracker():
    """The fake firmware's summary is the residual the ball tracker searches."""
    cube = _cube(n_tx=2)
    power = vertical_loop_power(cube, n_tx=2)
    expected = loop_power(mti_filter(cube, range_domain=True))

    np.testing.assert_allclose(power.power, expected, rtol=1e-5)


def test_vertical_power_uses_the_outer_transmitters():
    """Three-TX captures drop the middle TX before the vertical residual."""
    cube = _cube(n_tx=3)
    frames, chirps, n_rx, bins = cube.shape
    loops = chirps // 3
    pair = cube.reshape(frames, loops, 3, n_rx, bins)[:, :, [0, 2]].reshape(
        frames, loops * 2, n_rx, bins
    )
    power = vertical_loop_power(cube, n_tx=3)

    np.testing.assert_allclose(
        power.power, loop_power(mti_filter(pair, range_domain=True)), rtol=1e-5
    )


def test_power_packet_in_firmware_layout_round_trips():
    cube = _cube(n_tx=3)
    summary = vertical_loop_power(cube, n_tx=3)
    parsed = parse_power(power_packet(summary))

    np.testing.assert_array_equal(parsed.power, summary.power)
    assert parsed.n_tx == 3
    assert parsed.n_rx == 4
    assert parsed.n_loops == 4
    assert parsed.geometry.n_frames == 6
    assert parsed.geometry.frame_period_s == pytest.approx(0.003)


@pytest.mark.parametrize("cut", [0, 10, 25])
def test_short_power_packet_is_rejected(cut):
    raw = power_packet(vertical_loop_power(_cube(n_tx=2), n_tx=2))

    with pytest.raises(ValueError, match="short IWR power summary"):
        parse_power(raw[: cut if cut else 10] if cut else raw[:-4])


def test_power_packet_needs_its_magic():
    raw = power_packet(vertical_loop_power(_cube(n_tx=2), n_tx=2))

    with pytest.raises(ValueError, match="ILP1"):
        parse_power(b"XXXX" + raw[4:])


# --- cell planning ------------------------------------------------------------


def test_expand_cells_lists_every_center_before_any_neighbor():
    geometry = _geometry(frames=3, loops=2, bins=20)
    cells = expand_cells([(0, 55.0), (1, 58.0), (2, 61.0)], geometry, margin=1)

    assert cells[:3] == [(0, 5), (1, 8), (2, 11)]
    assert set(cells[3:]) == {(0, 4), (0, 6), (1, 7), (1, 9), (2, 10), (2, 12)}
    assert len(cells) == len(set(cells))


def test_expand_cells_drops_bins_outside_the_frame_window():
    geometry = _geometry(frames=1, loops=2, bins=20)

    assert expand_cells([(0, 50.0)], geometry, margin=1) == [(0, 0), (0, 1)]
    assert expand_cells([(0, 45.0)], geometry, margin=1) == []


def test_track_cells_cover_the_fitted_walk_centers_first():
    cube = _moving_ball_cube()
    geometry = _geometry(frames=8, loops=6, bins=28)
    track = find_ball(mti_filter(cube, range_domain=True), geometry, min_ball_ms=1.0)
    assert track is not None

    cells = track_cells(track, geometry)

    centers = {(frame, 4 + 3 * frame) for frame in range(8)}
    assert centers <= set(cells)
    first_neighbor = next(
        index for index, cell in enumerate(cells) if cell not in centers and cell[1] >= 0
    )
    assert all(cell in centers or index >= first_neighbor for index, cell in enumerate(cells))


def test_noise_cells_stay_clear_of_the_track_and_sit_near_the_median():
    cube = _moving_ball_cube()
    geometry = _geometry(frames=8, loops=6, bins=28)
    summary = vertical_loop_power(cube, n_tx=2, geometry=geometry)
    track = [(frame, 4 + 3 * frame) for frame in range(8)]

    chosen = noise_cells(summary, track, guard_bins=3)

    assert len(chosen) == 8
    for frame, local in chosen:
        assert abs(local - (4 + 3 * frame)) > 3
        per_bin = summary.power.reshape(8, 6, 28)[frame].sum(axis=0)
        assert per_bin[local] < 10 * np.median(per_bin)


def test_plan_puts_noise_cells_first_and_drops_duplicates():
    plan = SparsePlan(cells=((0, 1), (0, 2), (1, 3)), noise_cells=((0, 9), (0, 2)))

    assert plan.request_order() == [(0, 9), (0, 2), (0, 1), (1, 3)]


# --- request size -------------------------------------------------------------


def test_firmware_request_buffer_matches_the_host_limit():
    header = DUMP_FORMAT_H.read_text(encoding="utf-8")
    match = re.search(r"#define\s+L3_SPARSE_REQUEST_MAX\s+(\d+)U?", header)

    assert match is not None
    assert int(match.group(1)) == SPARSE_REQUEST_MAX_BYTES


def test_request_that_fits_is_sent_whole():
    cells = [(frame, 10) for frame in range(20)]
    request, sent = fit_cell_request(cells)

    assert sent == 20
    assert request == format_cell_request(cells)


def test_request_is_trimmed_from_the_tail_to_leave_room_for_the_newline():
    cells = [(frame % 24, 40 + frame % 13) for frame in range(300)]
    request, sent = fit_cell_request(cells)

    assert 0 < sent < 300
    assert len(request) <= SPARSE_REQUEST_MAX_BYTES - 1
    assert request == format_cell_request(cells[:sent])
    assert len(format_cell_request(cells[: sent + 1])) > SPARSE_REQUEST_MAX_BYTES - 1


def test_request_at_exactly_the_budget_is_kept():
    cells = [(0, 1)] * 5
    budget = len(format_cell_request(cells))
    request, sent = fit_cell_request(cells, max_bytes=budget + 1)

    assert sent == 5
    assert len(request) == budget


# --- noise floor ---------------------------------------------------------------


def test_sparse_noise_matches_the_full_cube_estimator():
    """Noise cells give the same burst-MTI median the full dump would."""
    cube = _cube(n_tx=2, frames=6, loops=8, bins=16)
    geometry = _geometry(frames=6, loops=8, bins=16, start=0)
    columns = {
        (frame, local): cube[frame, :, :, local] for frame in range(6) for local in range(16)
    }
    full = PreparedShotDump(metadata={}, cube=cube, geometry=geometry, range_domain=True)

    sparse = sparse_noise_power(columns, columns.keys())

    assert sparse == pytest.approx(full.noise_power("burst"), rel=1e-5)


def test_sparse_noise_without_noise_cells_is_an_error():
    with pytest.raises(ValueError, match="no noise cells"):
        sparse_noise_power({}, [(0, 1)])


def test_sparse_noise_of_zero_is_an_error():
    columns = {(0, 1): np.zeros((8, 4), dtype=np.complex64)}

    with pytest.raises(ValueError, match="must be positive"):
        sparse_noise_power(columns, [(0, 1)])


@pytest.mark.parametrize("noise", [0.0, -1.0, float("nan")])
def test_prepared_dump_refuses_a_non_positive_noise_floor(noise):
    prepared = PreparedShotDump(
        metadata={},
        cube=np.ones((1, 4, 4, 8), dtype=np.complex64),
        geometry=_geometry(frames=1, loops=2, bins=8, start=0),
        range_domain=True,
    )

    with pytest.raises(ValueError, match="noise floor must be positive"):
        prepared.set_noise_power(noise)


def test_sparse_cube_without_a_noise_override_fails_loudly():
    """The branch bug: a mostly-zero cube has a median noise of zero."""
    cube = np.zeros((4, 8, 4, 16), dtype=np.complex64)
    cube[0, :, :, 3] = 100 + 50j
    cube[0, 0, :, 3] = 400
    prepared = PreparedShotDump(
        metadata={},
        cube=cube,
        geometry=_geometry(frames=4, loops=4, bins=16, start=0),
        range_domain=True,
    )

    with pytest.raises(ValueError, match="noise floor must be positive"):
        prepared.noise_power("burst")


# --- assembly -------------------------------------------------------------------


def test_assembled_capture_keeps_requested_cells_and_zeros_elsewhere():
    cube = _cube(n_tx=2, frames=4, loops=4, bins=16)
    summary = vertical_loop_power(cube, n_tx=2)
    plan = SparsePlan(cells=((0, 3), (2, 7)), noise_cells=((1, 12),))
    cells = plan.request_order()

    capture = assemble_capture(summary, slice_packet(cube, 2, cells), plan, requested_cells=3)

    _meta, rebuilt = parse_dump(capture.raw)
    for frame, local in cells:
        np.testing.assert_array_equal(
            rebuilt[frame, :, :, local], np.round(cube[frame, :, :, local])
        )
    mask = np.ones(rebuilt.shape, dtype=bool)
    for frame, local in cells:
        mask[frame, :, :, local] = False
    assert not np.any(rebuilt[mask])
    assert capture.noise_power > 0.0
    assert capture.sent_cells == 3 and not capture.truncated


def test_slice_cell_outside_the_capture_is_rejected():
    cube = _cube(n_tx=2, frames=2, loops=4, bins=16)
    summary = vertical_loop_power(cube, n_tx=2)
    packet = bytearray(slice_packet(cube, 2, [(1, 3)]))
    packet[6:8] = (9).to_bytes(2, "little")

    with pytest.raises(ValueError, match="outside the capture"):
        assemble_capture(summary, bytes(packet), SparsePlan(cells=()), requested_cells=1)


def test_short_slice_packet_is_rejected():
    cube = _cube(n_tx=2, frames=2, loops=4, bins=16)
    summary = vertical_loop_power(cube, n_tx=2)
    packet = slice_packet(cube, 2, [(1, 3)])

    with pytest.raises(ValueError, match="short IWR slice packet"):
        assemble_capture(summary, packet[:-2], SparsePlan(cells=()), requested_cells=1)


# --- driver ---------------------------------------------------------------------


def _plan_for(cells, noise=((0, 12),)):
    return lambda _summary: SparsePlan(cells=tuple(cells), noise_cells=tuple(noise))


def test_driver_reads_firmware_bytes_and_measures_noise_from_noise_cells():
    cube = _cube(n_tx=2)
    summary = vertical_loop_power(cube, n_tx=2)
    serial = FakeSparseSerial(cube=cube, n_tx=2, summary=summary)

    capture = _radar(serial).read_sparse(_plan_for([(0, 1), (2, 4)]))

    assert capture is not None
    assert serial.requested_cells == [(0, 12), (0, 1), (2, 4)]
    assert serial.written[0] == b"l3sparse\n"
    _meta, rebuilt = parse_dump(capture.raw)
    for frame, local in serial.requested_cells:
        np.testing.assert_array_equal(
            rebuilt[frame, :, :, local], np.round(cube[frame, :, :, local])
        )
    column = np.round(cube[0, :, :, 12]).reshape(4, 2, 4)
    expected_noise = np.median(np.abs(column - column.mean(axis=0)) ** 2)
    assert capture.noise_power == pytest.approx(expected_noise, rel=1e-5)


@pytest.mark.parametrize(
    "reply",
    [b"'l3sparse' is not recognized as a CLI command\n", b"Error: sparse dump requires IQ16\n"],
)
def test_driver_reports_a_rejection_before_the_freeze_as_none(reply):
    serial = FakeSparseSerial(cube=None, n_tx=2, summary=None, before_power=reply)

    assert _radar(serial).read_sparse(_plan_for([]), timeout_s=0.5) is None
    assert serial.written == [b"l3sparse\n"]


def test_error_bytes_inside_the_binary_power_are_not_a_rejection():
    cube = _cube(n_tx=2)
    summary = vertical_loop_power(cube, n_tx=2)
    power = summary.power.copy()
    power.reshape(-1)[:2] = np.frombuffer(b"Error!!!", dtype="<f4")
    summary = PowerSummary(
        power=power,
        n_tx=summary.n_tx,
        n_rx=summary.n_rx,
        n_loops=summary.n_loops,
        geometry=summary.geometry,
    )
    serial = FakeSparseSerial(cube=cube, n_tx=2, summary=summary, chunk=7)

    assert _radar(serial).read_sparse(_plan_for([(0, 1)])) is not None


def test_power_that_stops_midway_times_out_instead_of_falling_back():
    summary = vertical_loop_power(_cube(n_tx=2), n_tx=2)
    serial = FakeSparseSerial(cube=None, n_tx=2, summary=None)
    serial._buffer += power_packet(summary)[:-100]  # pylint: disable=protected-access

    with pytest.raises(TimeoutError, match="ILP1"):
        _radar(serial).read_sparse(_plan_for([]), timeout_s=0.2)


def test_rejected_cell_request_after_the_freeze_raises():
    cube = _cube(n_tx=2)
    serial = FakeSparseSerial(
        cube=cube,
        n_tx=2,
        summary=vertical_loop_power(cube, n_tx=2),
        after_request=b"Error: sparse cell request missing\n",
    )

    with pytest.raises(RuntimeError, match="after freezing"):
        _radar(serial).read_sparse(_plan_for([(0, 1)]), timeout_s=0.5)


def test_restart_failure_after_the_slices_raises():
    cube = _cube(n_tx=2)
    serial = FakeSparseSerial(
        cube=cube,
        n_tx=2,
        summary=vertical_loop_power(cube, n_tx=2),
        trailer=b"Error: RF restart failed\n",
    )

    with pytest.raises(RuntimeError, match="restart failed"):
        _radar(serial).read_sparse(_plan_for([(0, 1)]))


def test_oversized_plan_is_trimmed_and_reported():
    cube = _cube(n_tx=2, frames=24, loops=4, bins=16)
    summary = vertical_loop_power(cube, n_tx=2)
    cells = [(frame, local) for frame in range(24) for local in range(16)]
    serial = FakeSparseSerial(cube=cube, n_tx=2, summary=summary)

    capture = _radar(serial).read_sparse(_plan_for(cells, noise=((0, 0),)))

    request = serial.written[1]
    assert len(request) <= SPARSE_REQUEST_MAX_BYTES - 1
    assert serial.requested_cells[0] == (0, 0)
    assert capture.truncated


def test_release_requests_no_cells():
    cube = _cube(n_tx=2)
    serial = FakeSparseSerial(cube=cube, n_tx=2, summary=vertical_loop_power(cube, n_tx=2))

    _radar(serial).release_sparse_freeze()

    assert serial.written == [b"l3sparse\n", b"cells 0\n"]


def test_release_on_firmware_without_sparse_raises():
    serial = FakeSparseSerial(
        cube=None, n_tx=2, summary=None, before_power=b"'l3sparse' is not recognized\n"
    )

    with pytest.raises(RuntimeError, match="not released"):
        _radar(serial).release_sparse_freeze(timeout_s=0.5)


# --- runtime planner ---------------------------------------------------------------


def test_runtime_plan_orders_ball_then_club_and_keeps_noise_clear():
    frames, loops, bins = 8, 6, 28
    cube = _moving_ball_cube(frames, loops, bins)
    # A slower club return short of the tee, in a different loop than the ball.
    for frame in range(3):
        cube[frame, 6, :, 10 + frame] += 2000
        cube[frame, 7, :, 10 + frame] += 2000
    geometry = _geometry(frames, loops, bins)
    summary = vertical_loop_power(cube, n_tx=2, geometry=geometry)
    runtime = IWR6843Runtime(
        capture_monitor=SimpleNamespace(),
        calibration=SimpleNamespace(tee_range_m=3.0),
        net_range_m=4.6,
    )

    plan = runtime.plan_sparse_cells(summary)

    ball = {(frame, 4 + 3 * frame) for frame in range(frames)}
    club = {(frame, 10 + frame) for frame in range(3)}
    cells = list(plan.cells)
    assert ball <= set(cells)
    assert club <= set(cells)
    assert max(cells.index(cell) for cell in ball) < min(cells.index(cell) for cell in club)
    assert len(cells) == len(set(cells))
    assert len(plan.noise_cells) == frames
    for frame, local in plan.noise_cells:
        assert all(abs(local - other) > 3 for f, other in cells if f == frame)


def test_runtime_plan_without_a_track_still_names_noise_cells():
    cube = _cube(n_tx=2, frames=4, loops=4, bins=16)
    summary = vertical_loop_power(cube, n_tx=2, geometry=_geometry(4, 4, 16))
    runtime = IWR6843Runtime(
        capture_monitor=SimpleNamespace(),
        calibration=SimpleNamespace(tee_range_m=None),
        net_range_m=4.6,
    )

    plan = runtime.plan_sparse_cells(summary)

    assert plan.cells == ()
    assert len(plan.noise_cells) == 4


def test_planner_failure_still_answers_the_frozen_firmware():
    """Otherwise the firmware reads the next CLI command as its cell request."""
    cube = _cube(n_tx=2)
    serial = FakeSparseSerial(cube=cube, n_tx=2, summary=vertical_loop_power(cube, n_tx=2))

    def failing_planner(_summary):
        raise ValueError("RANSAC blew up")

    with pytest.raises(ValueError, match="RANSAC"):
        _radar(serial).read_sparse(failing_planner)
    assert serial.written == [b"l3sparse\n", b"cells 0\n"]
