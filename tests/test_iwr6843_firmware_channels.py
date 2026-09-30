"""The per-channel loops over a detect frame, firmware/iwr6843/l3_channels.c.

Four loops in l3_dump.c walked the same (loop, tx, rx, bin) frame layout
(the burst-MTI residual's float path, the static power the ball detector
reads, and the two angle snapshots) and ran only on the board. They now
live in one host-built module the board calls, checked here against the
replay's numpy mirrors, so optimising them (each sample read once from the
frame) is proved on the host.

Frame layout: [loop][tx][rx][bin][Im, Re], cb bytes a component (2: IQ16,
1: IQ8 with the frame's scale).
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.firmware_replay import (
    bin_observation_table,
    channel_snapshot,
    static_channel_snapshot,
)

N_RX = 4
LOOP_PERIOD_S = 135e-6


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def words(rng, loops: int, n_tx: int, bins: int, cb: int) -> np.ndarray:
    dtype, top = (np.int16, 2000) if cb == 2 else (np.int8, 120)
    return rng.integers(-top, top, size=(loops, n_tx, N_RX, bins, 2), dtype=dtype)


def cube_of(frame: np.ndarray, scale: float = 1.0) -> np.ndarray:
    loops, n_tx, n_rx, bins, _ = frame.shape
    cube = frame[..., 1].astype(np.float64) + 1j * frame[..., 0].astype(np.float64)
    return (cube * scale).reshape(1, loops * n_tx, n_rx, bins)


class Frame:
    """An l3_channel_frame_t over a numpy frame (kept alive with it)."""

    def __init__(self, frame: np.ndarray, scale: float = 1.0):
        loops, n_tx, n_rx, bins, _ = frame.shape
        self.data = np.ascontiguousarray(frame)
        self.c = fw.ChannelFrame(
            base=self.data.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            binCount=bins,
            cb=self.data.itemsize,
            scale=scale,
            ntx=n_tx,
            nrx=n_rx,
            loops=loops,
            loopPeriodS=LOOP_PERIOD_S,
        )

    @property
    def ref(self):
        return ctypes.byref(self.c)


# --- the residual --------------------------------------------------------------


def test_an_iq16_residual_is_the_shared_integer_scoring_bit_for_bit(lib):
    frame = words(np.random.default_rng(1), 16, 3, 20, 2)
    f = Frame(frame)
    for local in (0, 7, 19):
        got, ref = fw.BinObs(), fw.BinObs()
        lib.l3_channels_residual(f.ref, local, None, ctypes.byref(got))
        lib.l3_bin_score_iq16(
            f.data.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
            20,
            local,
            3,
            N_RX,
            16,
            ctypes.byref(ref),
            None,
        )
        assert bytes(got) == bytes(ref)


@pytest.mark.parametrize(("n_tx", "loops"), [(3, 16), (2, 8), (1, 4)])
def test_an_iq8_residual_is_the_replay_mirror_scaled(lib, n_tx, loops):
    frame = words(np.random.default_rng(2), loops, n_tx, 12, 1)
    f = Frame(frame, scale=16.0)
    table = bin_observation_table(cube_of(frame, 16.0), 0, 0, 12, n_tx)
    for local in (0, 5, 11):
        got = fw.BinObs()
        per_loop = (ctypes.c_float * 16)()
        lib.l3_channels_residual(f.ref, local, per_loop, ctypes.byref(got))
        for name in ("energy", "peak", "loop0", "r1Re", "r1Im"):
            assert getattr(got, name) == pytest.approx(
                float(table[local][name]), rel=1e-4, abs=1e-2
            )
        assert max(per_loop[:loops]) == got.peak


# --- the static power ----------------------------------------------------------


@pytest.mark.parametrize("step", [1, 4])
def test_the_static_power_is_the_mean_sample_power_of_the_vertical_pair(lib, step):
    frame = words(np.random.default_rng(3), 16, 3, 10, 2)
    f = Frame(frame)
    data = cube_of(frame)[0, :, :, 6].reshape(16, 3, N_RX)[::step][:, [0, 2]]
    expected = float(np.mean(np.abs(data) ** 2))
    assert lib.l3_channels_static_power(f.ref, 6, step) == pytest.approx(expected, rel=1e-5)


def test_the_static_power_scales_an_iq8_frame(lib):
    frame = words(np.random.default_rng(4), 8, 2, 6, 1)
    plain = lib.l3_channels_static_power(Frame(frame).ref, 3, 1)
    scaled = lib.l3_channels_static_power(Frame(frame, scale=4.0).ref, 3, 1)
    assert scaled == pytest.approx(16.0 * plain, rel=1e-6)


# --- the angle snapshots ---------------------------------------------------------


def assert_snapshots_close(got: fw.AngleSnapshot, want: fw.AngleSnapshot):
    assert (got.ntx, got.nrx) == (want.ntx, want.nrx)
    for k in range(got.ntx * got.nrx):
        assert got.channel[k].re == pytest.approx(want.channel[k].re, rel=1e-4, abs=1e-2)
        assert got.channel[k].im == pytest.approx(want.channel[k].im, rel=1e-4, abs=1e-2)
    assert got.lag1PhaseRad == pytest.approx(want.lag1PhaseRad)
    assert got.radialVelocityMps == pytest.approx(want.radialVelocityMps)
    assert got.chirpPeriodS == pytest.approx(want.chirpPeriodS)


def test_a_moving_targets_snapshot_is_the_replay_mirror(lib):
    frame = words(np.random.default_rng(5), 16, 3, 9, 2)
    got = fw.AngleSnapshot()
    lib.l3_channels_snapshot(Frame(frame).ref, 4, 0.7, 31.5, ctypes.byref(got))
    want = channel_snapshot(
        cube_of(frame),
        0,
        4,
        3,
        lag1_phase_rad=0.7,
        radial_velocity_mps=31.5,
        chirp_period_s=LOOP_PERIOD_S / 3,
    )
    assert_snapshots_close(got, want)


def test_a_static_targets_snapshot_is_the_replay_mirror(lib):
    frame = words(np.random.default_rng(6), 16, 3, 9, 2)
    got = fw.AngleSnapshot()
    lib.l3_channels_snapshot_static(Frame(frame).ref, 2, ctypes.byref(got))
    want = static_channel_snapshot(cube_of(frame), 0, 2, 3, chirp_period_s=LOOP_PERIOD_S / 3)
    assert_snapshots_close(got, want)


# --- refusals -------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value", [("loops", 0), ("loops", 17), ("ntx", 0), ("ntx", 4), ("nrx", 5), ("cb", 3)]
)
def test_bad_geometry_yields_nothing_rather_than_reading_past_the_frame(lib, field, value):
    frame = words(np.random.default_rng(7), 4, 1, 4, 2)
    f = Frame(frame)
    setattr(f.c, field, value)
    obs = fw.BinObs(energy=5.0)
    lib.l3_channels_residual(f.ref, 1, None, ctypes.byref(obs))
    assert (obs.energy, obs.peak) == (0.0, 0.0)
    assert lib.l3_channels_static_power(f.ref, 1, 1) == 0.0
    snap = fw.AngleSnapshot()
    lib.l3_channels_snapshot_static(f.ref, 1, ctypes.byref(snap))
    assert snap.ntx * snap.nrx == 0 or all(snap.channel[k].re == 0.0 for k in range(12))


def test_a_bin_past_the_frame_yields_nothing(lib):
    frame = words(np.random.default_rng(8), 4, 1, 4, 2)
    obs = fw.BinObs(energy=5.0)
    lib.l3_channels_residual(Frame(frame).ref, 4, None, ctypes.byref(obs))
    assert obs.energy == 0.0


# --- the board uses the module, not its own copies --------------------------------


def test_the_board_calls_the_module_for_every_channel_loop():
    from pathlib import Path  # pylint: disable=import-outside-toplevel

    root = Path(__file__).parents[1] / "firmware" / "iwr6843"
    source = (root / "l3_dump.c").read_text(encoding="utf-8")
    for fn in (
        "l3_channels_residual(",
        "l3_channels_static_power(",
        "l3_channels_snapshot(",
        "l3_channels_snapshot_static(",
    ):
        assert fn in source, fn
    assert "l3_ringComponent(sample" not in source, "no channel loop left in l3_dump.c"
    makefile = (root / "makefile").read_text(encoding="utf-8")
    assert "l3_channels.c" in makefile
    assert "l3_channels.c" in fw.HOST_SOURCES
