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


# --- the DSS's boot status, for when it does not answer -----------------------
#
# 2026-09-30 on the board: "trackCfg dsp ping: DSP did not answer (link open)".
# The MSS cannot tell a DSS that never booted from one stuck in SOC_init or
# one whose mailbox never opened. The DSS records each boot stage and a
# heartbeat in HS-RAM, which both cores map, and the MSS prints it.


def status(**fields) -> fw.DspStatus:
    out = fw.DspStatus(magic=fw.L3_DSP_STATUS_MAGIC)
    for name, value in fields.items():
        setattr(out, name, value)
    return out


def formatted(lib, value: fw.DspStatus | None) -> str:
    return fw.c_text(lib.l3_dsp_status_format, ctypes.byref(value) if value else None)


def test_the_status_layout_matches_the_c(lib):
    assert ctypes.sizeof(fw.DspStatus) == lib.l3_dsp_status_size()


def test_the_status_sits_at_the_top_of_hs_ram_on_both_cores():
    """One offset, each core's own base (MSS 0x52080000, DSS 0x21080000)."""
    assert 0 < fw.L3_DSP_STATUS_HSRAM_OFFSET <= 32 * 1024 - ctypes.sizeof(fw.DspStatus)


@pytest.mark.parametrize(
    ("stage", "name"),
    [
        (fw.L3_DSP_STAGE_MAIN, "main"),
        (fw.L3_DSP_STAGE_SOC, "soc_init"),
        (fw.L3_DSP_STAGE_TASK, "task"),
        (fw.L3_DSP_STAGE_MAILBOX, "mailbox_init"),
        (fw.L3_DSP_STAGE_LINK, "link_open"),
    ],
)
def test_each_boot_stage_is_named(lib, stage, name):
    text = formatted(lib, status(stage=stage, heartbeat=7, served=2))
    assert text == f"dsp status stage={name} err=0 beats=7 served=2"


def test_a_failed_stage_says_which_and_its_error(lib):
    text = formatted(lib, status(stage=fw.L3_DSP_STAGE_LINK | fw.L3_DSP_STAGE_FAILED, errCode=-3))
    assert text == "dsp status stage=link_open FAILED err=-3 beats=0 served=0"


def test_a_dss_that_never_wrote_its_status_says_so(lib):
    """Anything but the magic: the DSS never reached main (or HS-RAM is not shared)."""
    assert formatted(lib, fw.DspStatus(magic=0xDEADBEEF, stage=5)) == (
        "dsp status stage=never_booted magic=deadbeef"
    )
    assert formatted(lib, None) == "dsp status stage=never_booted magic=00000000"


def test_an_unknown_stage_is_shown_as_its_number(lib):
    assert formatted(lib, status(stage=42)).startswith("dsp status stage=42 ")


# --- the DSS seen from the MSS's side ------------------------------------------
#
# 2026-09-30 on the board: "stage=never_booted", beats 0. HS-RAM held no
# status: either the DSS never ran, or it ran and its HS-RAM writes are not
# what the MSS reads. The MSS reads what does not need the DSS's help: its
# halt bit and power state (DSSREG GEMPWRSMCFG4/3), the ROM self-test flag,
# the ESM error status, and a second channel the DSS mirrors its stage into
# (DSSREG DSSGPREG0, tagged); and checks HS-RAM itself with a write/read.


def hw(**fields) -> fw.DspHw:
    out = fw.DspHw()
    for name, value in fields.items():
        if name == "esm":
            for index, word in enumerate(value):
                out.esm[index] = word
        else:
            setattr(out, name, value)
    return out


def hw_text(lib, value: fw.DspHw) -> str:
    return fw.c_text(lib.l3_dsp_hw_format, ctypes.byref(value))


def test_the_hw_layout_matches_the_c(lib):
    assert ctypes.sizeof(fw.DspHw) == lib.l3_dsp_hw_size()


def test_a_running_dss_mirrors_its_stage_into_the_general_purpose_register(lib):
    text = hw_text(
        lib,
        hw(gpreg=fw.L3_DSP_GPREG_TAG | fw.L3_DSP_STAGE_LINK, halt=0, power=3, hsramOk=1),
    )
    assert text == (
        "dsp hw gpreg_stage=link_open halt=0 power=3 stc=0 "
        "esm=00000000,00000000,00000000,00000000 hsram=ok"
    )


def test_an_untagged_register_means_the_dss_never_wrote_it(lib):
    text = hw_text(lib, hw(gpreg=0, halt=1, power=0, hsramOk=0))
    assert text.startswith("dsp hw gpreg_stage=none(00000000) halt=1 power=0 ")
    assert text.endswith(" hsram=BAD")


def test_the_reset_hook_is_the_earliest_stage(lib):
    """Written before cinit and BIOS: stuck here, the DSS died before main."""
    text = hw_text(lib, hw(gpreg=fw.L3_DSP_GPREG_TAG | fw.L3_DSP_STAGE_RESET))
    assert "gpreg_stage=reset " in text


def test_a_failed_stage_in_the_register_is_marked(lib):
    gpreg = fw.L3_DSP_GPREG_TAG | fw.L3_DSP_STAGE_SOC | fw.L3_DSP_STAGE_FAILED
    assert "gpreg_stage=soc_init!FAILED " in hw_text(lib, hw(gpreg=gpreg))


def test_the_esm_words_are_printed_in_group_order(lib):
    text = hw_text(lib, hw(esm=[0x1, 0x20000000, 0x0, 0x80]))
    assert " esm=00000001,20000000,00000000,00000080 " in text


def test_the_status_names_the_reset_stage_too(lib):
    text = formatted(lib, status(stage=fw.L3_DSP_STAGE_RESET))
    assert text.startswith("dsp status stage=reset ")
