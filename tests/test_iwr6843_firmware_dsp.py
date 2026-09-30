"""Tests for the ARM <-> DSP detect link, firmware/iwr6843/l3_bin_score.c and
l3_dsp_ipc.c.

The trigger scores a range bin in ~73 us on the R4F and the pre-impact scan
plan scores 27 of them a frame (~2 ms of a 3 ms frame). The detect task is
moving to the C674x DSS. Phase 0 proves the link on the board: the MSS asks
the DSS to score bins of a frame in the shared L3 ring and both cores run the
SAME scoring code (l3_bin_score), so their answers must agree bit for bit.

- l3_bin_score: one bin's burst-MTI observation from an IQ16 frame, what
  l3_verticalResidual computes (and firmware_replay.bin_observation_table
  mirrors in numpy)
- l3_dsp_ipc: the mailbox messages. The two cores see L3 at different
  addresses (MSS 0x51000000, DSS 0x20000000), so a frame travels as its L3
  offset, and the DSS refuses any request that would read outside L3.
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.firmware_replay import bin_observation_table

N_RX = 4
L3_BYTES = 768 * 1024


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def frame_words(rng, loops: int, n_tx: int, bins: int) -> np.ndarray:
    """An IQ16 frame as the ring stores it: [loop][tx][rx][bin][Im, Re]."""
    return rng.integers(-2000, 2000, size=(loops, n_tx, N_RX, bins, 2), dtype=np.int16)


def as_cube(words: np.ndarray) -> np.ndarray:
    """The frame as a one-frame dump cube, [1, chirps, rx, bins], chirp = loop * n_tx + tx."""
    loops, n_tx, n_rx, bins, _ = words.shape
    cube = words[..., 1].astype(np.float64) + 1j * words[..., 0].astype(np.float64)
    return cube.reshape(1, loops * n_tx, n_rx, bins)


def score(lib, words: np.ndarray, local_bin: int, per_loop=None) -> fw.BinObs:
    loops, n_tx, n_rx, bins, _ = words.shape
    flat = np.ascontiguousarray(words)
    out = fw.BinObs()
    status = lib.l3_bin_score_iq16(
        flat.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
        bins,
        local_bin,
        n_tx,
        n_rx,
        loops,
        ctypes.byref(out),
        per_loop,
    )
    assert status == 0
    return out


# --- l3_bin_score: the scoring both cores run --------------------------------


@pytest.mark.parametrize(("n_tx", "loops"), [(3, 16), (2, 8), (1, 4), (3, 2)])
def test_a_bin_scores_what_the_replay_mirror_computes(lib, n_tx, loops):
    rng = np.random.default_rng(7)
    words = frame_words(rng, loops, n_tx, bins=12)
    expected = bin_observation_table(as_cube(words), 0, 0, 12, n_tx)

    for local in (0, 5, 11):
        got = score(lib, words, local)
        row = expected[local]
        for name in ("energy", "peak", "loop0", "r1Re", "r1Im"):
            assert getattr(got, name) == pytest.approx(float(row[name]), rel=1e-5, abs=1e-3), name


def test_the_per_loop_powers_are_what_l3sparse_streams(lib):
    """l3sparse's power map rows: each loop's residual power over the
    vertical TX pair and every RX; its largest is the bin's peak."""
    rng = np.random.default_rng(5)
    words = frame_words(rng, 16, 3, bins=6)
    per_loop = (ctypes.c_float * 16)()

    got = score(lib, words, 4, per_loop)

    data = as_cube(words)[0, :, :, 4].reshape(16, 3, N_RX)[:, [0, 2]]
    residual = data - data.mean(axis=0, keepdims=True)
    expected = (np.abs(residual) ** 2).sum(axis=(1, 2))
    assert list(per_loop) == pytest.approx(list(expected), rel=1e-5)
    assert max(per_loop) == got.peak and per_loop[0] == got.loop0


def test_the_azimuth_element_of_a_three_tx_loop_is_not_scored(lib):
    """TX1 of a three-TX loop is the azimuth element: noise there changes nothing."""
    rng = np.random.default_rng(3)
    words = frame_words(rng, 8, 3, bins=4)
    quiet = score(lib, words, 2)
    words[:, 1] = rng.integers(-30000, 30000, size=words[:, 1].shape, dtype=np.int16)
    assert score(lib, words, 2).energy == quiet.energy


def test_a_stationary_bin_has_no_residual(lib):
    words = np.zeros((16, 3, N_RX, 4, 2), dtype=np.int16)
    words[..., 2, :] = 1234  # the same sample every loop
    got = score(lib, words, 2)
    assert (got.energy, got.peak, got.r1Re, got.r1Im) == (0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("n_tx", "n_rx", "loops", "local"),
    [(0, 4, 8, 0), (4, 4, 8, 0), (3, 0, 8, 0), (3, 4, 0, 0), (3, 4, 17, 0), (3, 4, 8, 4)],
)
def test_bad_geometry_is_refused(lib, n_tx, n_rx, loops, local):
    words = np.zeros(8 * 3 * 4 * 4 * 2, dtype=np.int16)
    out = fw.BinObs()
    status = lib.l3_bin_score_iq16(
        words.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
        4,
        local,
        n_tx,
        n_rx,
        loops,
        ctypes.byref(out),
        None,
    )
    assert status == -1


def test_a_null_frame_is_refused(lib):
    assert lib.l3_bin_score_iq16(None, 4, 0, 3, 4, 8, ctypes.byref(fw.BinObs()), None) == -1


# --- l3_dsp_ipc: the messages ------------------------------------------------


def test_the_message_layouts_match_the_c(lib):
    """Both cores and the host mirror agree on every byte of a message."""
    assert ctypes.sizeof(fw.DspRequest) == lib.l3_dsp_request_size()
    assert ctypes.sizeof(fw.DspReply) == lib.l3_dsp_reply_size()


def probe(**overrides) -> fw.DspRequest:
    request = fw.DspRequest(
        magic=fw.L3_DSP_MAGIC,
        cmd=fw.L3_DSP_CMD_PROBE,
        seq=1,
        frameOffset=4096,
        binCount=53,
        firstBin=10,
        nBins=27,
        ntx=3,
        loops=16,
    )
    for name, value in overrides.items():
        setattr(request, name, value)
    return request


def test_a_probe_inside_l3_is_accepted(lib):
    assert lib.l3_dsp_request_check(ctypes.byref(probe()), L3_BYTES) == fw.L3_DSP_OK


def test_a_ping_needs_no_frame(lib):
    ping = fw.DspRequest(magic=fw.L3_DSP_MAGIC, cmd=fw.L3_DSP_CMD_PING, seq=9)
    assert lib.l3_dsp_request_check(ctypes.byref(ping), L3_BYTES) == fw.L3_DSP_OK


def test_the_frame_size_is_every_loop_tx_rx_and_bin(lib):
    assert lib.l3_dsp_frame_bytes(3, 4, 53, 16) == 16 * 3 * 4 * 53 * 4


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"magic": 0}, "magic"),
        ({"cmd": 99}, "cmd"),
        ({"loops": 0}, "loops"),
        ({"loops": 17}, "loops"),
        ({"ntx": 0}, "ntx"),
        ({"ntx": 4}, "ntx"),
        ({"nBins": 0}, "no bins"),
        ({"binCount": 0}, "empty frame"),
        ({"firstBin": 40, "nBins": 14}, "bins past the frame"),
        ({"frameOffset": 2}, "misaligned"),
        ({"frameOffset": L3_BYTES - 1024}, "frame past L3"),
        ({"frameOffset": 0xFFFFF000}, "offset wraps"),
    ],
)
def test_a_bad_probe_is_refused(lib, overrides, why):
    del why
    assert lib.l3_dsp_request_check(ctypes.byref(probe(**overrides)), L3_BYTES) != fw.L3_DSP_OK


def test_a_probe_sums_the_bins_it_scored(lib):
    """What the MSS and the DSS each run on the same frame; the reply's sums
    are the check that they read the same bytes and scored them the same."""
    rng = np.random.default_rng(11)
    words = frame_words(rng, 16, 3, bins=53)
    arena = np.zeros(L3_BYTES // 2, dtype=np.int16)
    offset = 4096
    arena[offset // 2 : offset // 2 + words.size] = words.ravel()
    request = probe(frameOffset=offset, firstBin=10, nBins=27)
    reply = fw.DspReply()

    lib.l3_dsp_probe_run(
        ctypes.byref(request),
        arena.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        L3_BYTES,
        ctypes.byref(reply),
    )

    table = bin_observation_table(as_cube(words), 0, 10, 27, 3)
    assert (reply.magic, reply.cmd, reply.seq, reply.status) == (
        fw.L3_DSP_MAGIC,
        fw.L3_DSP_CMD_PROBE,
        1,
        fw.L3_DSP_OK,
    )
    assert reply.nBins == 27
    assert reply.energySum == pytest.approx(float(table["energy"].sum()), rel=1e-5)
    assert reply.r1ReSum == pytest.approx(float(table["r1Re"].sum()), rel=1e-4, abs=1.0)
    assert reply.r1ImSum == pytest.approx(float(table["r1Im"].sum()), rel=1e-4, abs=1.0)


def test_a_refused_probe_scores_nothing_and_says_why(lib):
    reply = fw.DspReply()
    lib.l3_dsp_probe_run(ctypes.byref(probe(loops=0, seq=5)), None, L3_BYTES, ctypes.byref(reply))
    assert (reply.magic, reply.seq, reply.nBins) == (fw.L3_DSP_MAGIC, 5, 0)
    assert reply.status != fw.L3_DSP_OK


def test_a_ping_is_answered_with_its_sequence(lib):
    reply = fw.DspReply()
    ping = fw.DspRequest(magic=fw.L3_DSP_MAGIC, cmd=fw.L3_DSP_CMD_PING, seq=42)
    lib.l3_dsp_probe_run(ctypes.byref(ping), None, L3_BYTES, ctypes.byref(reply))
    assert (reply.cmd, reply.seq, reply.status, reply.nBins) == (
        fw.L3_DSP_CMD_PING,
        42,
        fw.L3_DSP_OK,
        0,
    )
