"""Tests for the DSS's gather, firmware/iwr6843/l3_dsp_ipc.c (l3_dsp_gather_*).

On the board (2026-09-30) the DSS scored the scan plan's 27 bins in 1,303 us,
48 us a bin: only 2.1x the R4F. It read each bin in place from L3, one
4-byte sample per (loop, tx, rx) row, through a 16 KB L1D and no L2 cache
(the L2 cache override was what kept the DSS from booting). The gather
copies just the window of bins a request reads, row by row, from L3 into
L2 SRAM in one EDMA transfer (AB-synchronised: aCount a row's bytes, bCount
the rows, the source stride the frame's row), and the DSS scores from that
copy with the same code, so the answer is bit for bit what scoring in place
gives.

- l3_dsp_gather_plan: the window (PROBE's bins, SCORE's spans) and the
  transfer's geometry, refused past the L2 buffer or the EDMA's limits
- l3_dsp_gather_copy: the CPU reference of the EDMA transfer
- l3_dsp_serve_gathered: PROBE and SCORE from the copy, bit for bit the
  in-place l3_dsp_probe_run / l3_dsp_serve
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.dump import parse_dump
from tests.test_iwr6843_firmware_dsp_score import (
    L3_BYTES,
    OFFSET,
    RECORDINGS,
    _as_words,
    _plan_spans,
    arena_with,
    frame_words,
    marked,
    score_request,
    serve,
)

N_RX = 4
GATHER_MAX = fw.L3_DSP_GATHER_MAX_BYTES


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def probe_request(words: np.ndarray, first: int, count: int, **overrides) -> fw.DspRequest:
    loops, n_tx, _, bins, _ = words.shape
    request = fw.DspRequest(
        magic=fw.L3_DSP_MAGIC,
        cmd=fw.L3_DSP_CMD_PROBE,
        seq=3,
        frameOffset=OFFSET,
        binCount=bins,
        firstBin=first,
        nBins=count,
        ntx=n_tx,
        loops=loops,
    )
    for name, value in overrides.items():
        setattr(request, name, value)
    return request


def plan(lib, request: fw.DspRequest, max_bytes: int = GATHER_MAX):
    gather = fw.DspGather()
    status = lib.l3_dsp_gather_plan(
        ctypes.byref(request), L3_BYTES, max_bytes, ctypes.byref(gather)
    )
    return status, gather


def gathered(lib, arena: np.ndarray, gather: fw.DspGather) -> np.ndarray:
    out = np.full(GATHER_MAX, 0x55, dtype=np.uint8)
    lib.l3_dsp_gather_copy(
        arena.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(gather),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
    )
    return out


def serve_gathered(lib, request, gather, copy: np.ndarray):
    reply, result = fw.DspReply(), fw.DspResult()
    lib.l3_dsp_serve_gathered(
        ctypes.byref(request),
        ctypes.byref(gather),
        copy.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(reply),
        ctypes.byref(result),
    )
    return reply, result


def assert_results_equal(a: fw.DspResult, b: fw.DspResult):
    assert (a.magic, a.seq, a.epoch, a.status, a.count) == (
        b.magic,
        b.seq,
        b.epoch,
        b.status,
        b.count,
    )
    assert marked(a.scored) == marked(b.scored)
    for local in marked(a.scored):
        assert bytes(a.obs[local]) == bytes(b.obs[local]), local  # bit for bit


# --- the plan ----------------------------------------------------------------


def test_a_probe_gathers_exactly_its_bins(lib):
    words = frame_words(np.random.default_rng(1), 16, 3, 53)
    status, gather = plan(lib, probe_request(words, 10, 27))
    assert status == fw.L3_DSP_OK
    assert (gather.lo, gather.width, gather.rows) == (10, 27, 16 * 3 * N_RX)
    assert gather.srcOffset == OFFSET + 10 * 4
    assert gather.srcStride == 53 * 4
    assert gather.rowBytes == 27 * 4
    assert gather.bytes == 16 * 3 * N_RX * 27 * 4


def test_a_score_gathers_the_window_its_spans_cover(lib):
    words = frame_words(np.random.default_rng(2), 16, 3, 53)
    status, gather = plan(lib, score_request(words, (30, 6), (12, 4), (40, 5)))
    assert status == fw.L3_DSP_OK
    assert (gather.lo, gather.width) == (12, 45 - 12)


def test_a_score_window_in_a_frame_wider_than_the_detector_strides_the_whole_row(lib):
    """Spans stop at the detector's 64 bins (the request check refuses any
    past it); the frame's rows are still binCount wide in L3."""
    words = frame_words(np.random.default_rng(3), 4, 1, 80)
    status, gather = plan(lib, score_request(words, (50, 14)))
    assert status == fw.L3_DSP_OK
    assert (gather.lo, gather.lo + gather.width) == (50, fw.L3_DSP_MAX_BINS)
    assert gather.srcStride == 80 * 4


def test_a_ping_has_nothing_to_gather(lib):
    ping = fw.DspRequest(magic=fw.L3_DSP_MAGIC, cmd=fw.L3_DSP_CMD_PING, seq=1)
    assert plan(lib, ping)[0] == fw.L3_DSP_ERR_CMD


def test_a_refused_request_is_refused_with_its_own_status(lib):
    words = frame_words(np.random.default_rng(4), 16, 3, 53)
    assert plan(lib, probe_request(words, 10, 27, frameOffset=2))[0] == fw.L3_DSP_ERR_RANGE
    assert plan(lib, probe_request(words, 10, 27, loops=0))[0] == fw.L3_DSP_ERR_GEOMETRY


def test_a_window_bigger_than_the_buffer_is_refused(lib):
    """The DSS then scores in place, as before the gather."""
    words = frame_words(np.random.default_rng(5), 16, 3, 53)
    request = probe_request(words, 0, 53)
    status, gather = plan(lib, request)
    assert status == fw.L3_DSP_OK
    assert plan(lib, request, max_bytes=gather.bytes - 1)[0] == fw.L3_DSP_ERR_GATHER


def test_the_buffer_holds_the_largest_window_the_detector_asks_for():
    """16 loops, 3 TX, 4 RX, 64 bins of IQ16."""
    assert GATHER_MAX >= 16 * 3 * N_RX * fw.L3_DSP_MAX_BINS * 4


def test_the_plan_never_leaves_the_output_untouched_on_refusal(lib):
    words = frame_words(np.random.default_rng(6), 16, 3, 53)
    gather = fw.DspGather(lo=99, width=99, bytes=99)
    lib.l3_dsp_gather_plan(
        ctypes.byref(probe_request(words, 10, 27, loops=0)),
        L3_BYTES,
        GATHER_MAX,
        ctypes.byref(gather),
    )
    assert (gather.lo, gather.width, gather.bytes) == (0, 0, 0)


# --- the copy ----------------------------------------------------------------


def test_the_copy_is_each_rows_window_back_to_back(lib):
    words = frame_words(np.random.default_rng(7), 16, 3, 53)
    arena = arena_with(words)
    status, gather = plan(lib, probe_request(words, 10, 27))
    assert status == fw.L3_DSP_OK

    copy = gathered(lib, arena, gather)

    expected = np.ascontiguousarray(words[:, :, :, 10:37, :]).view(np.uint8).ravel()
    assert np.array_equal(copy[: gather.bytes], expected)
    assert np.all(copy[gather.bytes :] == 0x55), "nothing written past the window"


# --- scoring from the copy is scoring in place ------------------------------


@pytest.mark.parametrize(
    ("n_tx", "loops", "first", "count"), [(3, 16, 10, 27), (2, 8, 0, 53), (1, 4, 52, 1)]
)
def test_a_gathered_probe_is_the_in_place_probe(lib, n_tx, loops, first, count):
    words = frame_words(np.random.default_rng(8), loops, n_tx, 53)
    arena = arena_with(words)
    request = probe_request(words, first, count)
    status, gather = plan(lib, request)
    assert status == fw.L3_DSP_OK
    in_place = fw.DspReply()
    lib.l3_dsp_probe_run(
        ctypes.byref(request),
        arena.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        L3_BYTES,
        ctypes.byref(in_place),
    )

    reply, _ = serve_gathered(lib, request, gather, gathered(lib, arena, gather))

    assert (reply.status, reply.nBins, reply.seq) == (in_place.status, in_place.nBins, in_place.seq)
    for name in ("energySum", "r1ReSum", "r1ImSum"):
        assert getattr(reply, name) == getattr(in_place, name), name  # bit for bit


@pytest.mark.parametrize(
    "local",
    [
        ((30, 6), (12, 4), (40, 5)),  # gaps between spans
        ((20, 16), (28, 16), (35, 3), (0, 2)),  # overlaps, four spans
        ((52, 1),),
    ],
)
def test_a_gathered_score_is_the_in_place_score(lib, local):
    words = frame_words(np.random.default_rng(9), 16, 3, 53)
    arena = arena_with(words)
    request = score_request(words, *local)
    status, gather = plan(lib, request)
    assert status == fw.L3_DSP_OK
    _, in_place = serve(lib, request, arena)

    reply, result = serve_gathered(lib, request, gather, gathered(lib, arena, gather))

    assert reply.nBins == in_place.count
    assert_results_equal(result, in_place)


def test_bins_outside_the_gathered_window_are_never_scored(lib):
    """A plan narrower than the request (a caller's mistake) scores only
    what was copied, never bytes outside it."""
    words = frame_words(np.random.default_rng(10), 16, 3, 53)
    arena = arena_with(words)
    status, narrow = plan(lib, score_request(words, (20, 4)))
    assert status == fw.L3_DSP_OK
    request = score_request(words, (18, 10))

    _, result = serve_gathered(lib, request, narrow, gathered(lib, arena, narrow))

    assert marked(result.scored) == [20, 21, 22, 23]


def test_a_refused_request_is_refused_from_the_copy_too(lib):
    words = frame_words(np.random.default_rng(11), 16, 3, 53)
    arena = arena_with(words)
    _, gather = plan(lib, score_request(words, (20, 4)))
    bad = score_request(words, (20, 4), magic=0)

    reply, result = serve_gathered(lib, bad, gather, gathered(lib, arena, gather))

    assert reply.status == fw.L3_DSP_ERR_MAGIC and result.count == 0


@pytest.mark.skipif(not RECORDINGS, reason="no committed recordings")
@pytest.mark.parametrize(("path", "config"), RECORDINGS[:3], ids=lambda v: getattr(v, "name", ""))
def test_a_gathered_score_over_a_recording_is_the_in_place_score(lib, path, config):
    meta, cube = parse_dump(path.read_bytes())
    n_tx = meta["n_tx"]
    starts = meta.get("range_bin_starts") or (meta.get("range_bin_start", 0),) * cube.shape[0]
    checked = 0
    for frame in range(0, cube.shape[0], max(1, cube.shape[0] // 4)):
        words = _as_words(cube, frame, n_tx)
        bins = words.shape[3]
        arena = arena_with(words)
        tee = min(max(config.tee_bin, starts[frame] + 4), starts[frame] + bins - 5)
        for scan in _plan_spans(lib, starts[frame], bins, tee):
            local = (fw.Span * fw.L3_DSP_MAX_SPANS)()
            n = lib.l3_dsp_spans_localize(
                starts[frame], bins, (fw.Span * len(scan))(*scan), len(scan), local
            )
            if n == 0:
                continue
            request = score_request(words, *((local[k].first, local[k].count) for k in range(n)))
            status, gather = plan(lib, request)
            assert status == fw.L3_DSP_OK
            _, in_place = serve(lib, request, arena)
            _, result = serve_gathered(lib, request, gather, gathered(lib, arena, gather))
            assert_results_equal(result, in_place)
            checked += 1
    assert checked > 0


def test_the_gather_layout_matches_the_c(lib):
    assert ctypes.sizeof(fw.DspGather) == lib.l3_dsp_gather_size()
    assert ctypes.sizeof(fw.DspReply) == lib.l3_dsp_reply_size()
