"""Tests for SCORE, the live detector's DSS command, firmware/iwr6843/l3_dsp_ipc.c.

Phase 0 proved the link (ping, probe). SCORE moves the detector's bin scoring
to the DSS without moving anything else: the MSS sends the scan plan's spans
(l3_scan.h) of one ring frame as LOCAL bins; the DSS scores exactly those
bins with the code the MSS runs (l3_dsp_spans_score over l3_bin_score_iq16)
and leaves the observations in a result block in HS-RAM; the mailbox reply
only says it is ready. The MSS accepts a result only for the request it sent,
merges it into the detector's own arrays, or (verify) compares it bit for bit
against its own scoring of the same bins.

- l3_dsp_spans_localize: the plan's GLOBAL spans as a frame's LOCAL ones
- l3_dsp_spans_score: each bin once, clipped to the frame and to 64 bins
- l3_dsp_request_check / l3_dsp_serve: SCORE's refusals and its answer
- l3_dsp_result_check / merge / compare: what the MSS does with the answer
- the whole path over every committed recording, against the replay's
  numpy mirror and against the MSS's own per-bin scoring
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.firmware_replay import bin_observation_table, recording_configs

N_RX = 4
L3_BYTES = 768 * 1024
OFFSET = 4096
FIELDS = ("energy", "peak", "loop0", "r1Re", "r1Im")


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


# --- helpers -----------------------------------------------------------------


def frame_words(rng, loops: int, n_tx: int, bins: int) -> np.ndarray:
    """An IQ16 frame as the ring stores it: [loop][tx][rx][bin][Im, Re]."""
    return rng.integers(-2000, 2000, size=(loops, n_tx, N_RX, bins, 2), dtype=np.int16)


def as_cube(words: np.ndarray) -> np.ndarray:
    loops, n_tx, n_rx, bins, _ = words.shape
    cube = words[..., 1].astype(np.float64) + 1j * words[..., 0].astype(np.float64)
    return cube.reshape(1, loops * n_tx, n_rx, bins)


def arena_with(words: np.ndarray, offset: int = OFFSET) -> np.ndarray:
    arena = np.zeros(L3_BYTES // 2, dtype=np.int16)
    arena[offset // 2 : offset // 2 + words.size] = words.ravel()
    return arena


def spans(*pairs: tuple[int, int]) -> ctypes.Array:
    return (fw.Span * max(1, len(pairs)))(*(fw.Span(first, count) for first, count in pairs))


def score_request(words: np.ndarray, *local: tuple[int, int], **overrides) -> fw.DspRequest:
    loops, n_tx, _, bins, _ = words.shape
    request = fw.DspRequest(
        magic=fw.L3_DSP_MAGIC,
        cmd=fw.L3_DSP_CMD_SCORE,
        seq=7,
        frameOffset=OFFSET,
        binCount=bins,
        ntx=n_tx,
        loops=loops,
        epoch=1234,
        nSpans=len(local),
    )
    for k, (first, count) in enumerate(local[: fw.L3_DSP_MAX_SPANS]):
        request.spanFirst[k] = first
        request.spanCount[k] = count
    for name, value in overrides.items():
        setattr(request, name, value)
    return request


def serve(lib, request: fw.DspRequest, arena: np.ndarray | None):
    reply, result = fw.DspReply(), fw.DspResult()
    lib.l3_dsp_serve(
        ctypes.byref(request),
        None if arena is None else arena.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        L3_BYTES,
        ctypes.byref(reply),
        ctypes.byref(result),
    )
    return reply, result


def marked(bitmap) -> list[int]:
    return [b for b in range(fw.L3_DSP_MAX_BINS) if (bitmap[b >> 5] >> (b & 31)) & 1]


def iq16_scorer(lib) -> fw.BinScorer:
    return ctypes.cast(lib.l3_dsp_iq16_scorer, fw.BinScorer)


def mss_scores(lib, words: np.ndarray, local: list[int]) -> dict[int, fw.BinObs]:
    """What the MSS computes per bin: l3_bin_score_iq16 straight."""
    loops, n_tx, _, bins, _ = words.shape
    flat = np.ascontiguousarray(words)
    out = {}
    for b in local:
        obs = fw.BinObs()
        assert (
            lib.l3_bin_score_iq16(
                flat.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
                bins,
                b,
                n_tx,
                N_RX,
                loops,
                ctypes.byref(obs),
                None,
            )
            == 0
        )
        out[b] = obs
    return out


def bits(value: float) -> int:
    return int(np.float32(value).view(np.uint32))


# --- layouts -------------------------------------------------------------------


def test_the_result_layout_matches_the_c(lib):
    assert ctypes.sizeof(fw.DspResult) == lib.l3_dsp_result_size()
    assert ctypes.sizeof(fw.DspRequest) == lib.l3_dsp_request_size()


def test_the_result_block_fits_below_the_status_in_hs_ram():
    """Result and status share HS-RAM (32 KB); neither may overlap the other."""
    result_end = fw.L3_DSP_RESULT_HSRAM_OFFSET + ctypes.sizeof(fw.DspResult)
    assert result_end <= fw.L3_DSP_STATUS_HSRAM_OFFSET
    assert fw.L3_DSP_STATUS_HSRAM_OFFSET + ctypes.sizeof(fw.DspStatus) <= fw.HSRAM_BYTES


def test_the_result_block_starts_on_its_own_cache_line():
    """The DSS writes it back by lines (L2 128 B): no line shared with the status."""
    assert fw.L3_DSP_RESULT_HSRAM_OFFSET % 128 == 0
    last_line = (fw.L3_DSP_RESULT_HSRAM_OFFSET + ctypes.sizeof(fw.DspResult) - 1) // 128
    assert last_line < fw.L3_DSP_STATUS_HSRAM_OFFSET // 128


def test_the_request_fits_the_mailbox():
    """The xwr68xx mailbox carries ~2 KB a message; SCORE stays tiny."""
    assert ctypes.sizeof(fw.DspRequest) <= 128


# --- l3_dsp_spans_localize ---------------------------------------------------------


def localize(lib, bin_start: int, bin_count: int, *pairs: tuple[int, int]) -> list:
    out = spans(*([(0, 0)] * fw.L3_DSP_MAX_SPANS))
    n = lib.l3_dsp_spans_localize(bin_start, bin_count, spans(*pairs), len(pairs), out)
    return [(out[k].first, out[k].count) for k in range(n)]


def test_global_spans_become_local_bins(lib):
    assert localize(lib, 20, 53, (28, 16), (44, 4)) == [(8, 16), (24, 4)]


def test_spans_are_clipped_to_the_frame(lib):
    """Starting before the frame, running past it: only what is inside."""
    assert localize(lib, 20, 53, (10, 15), (70, 10)) == [(0, 5), (50, 3)]


def test_spans_are_clipped_to_the_detectors_64_bins(lib):
    assert localize(lib, 0, 100, (60, 10)) == [(60, 4)]


def test_empty_and_outside_spans_are_dropped_order_kept(lib):
    assert localize(lib, 20, 53, (30, 0), (0, 5), (90, 4), (25, 2)) == [(5, 2)]


def test_a_span_that_would_wrap_is_dropped(lib):
    assert localize(lib, 0, 53, (0xFFFFFFF0, 0x20)) == []


def test_more_spans_than_score_carries_localize_nothing(lib):
    """Callers pass fixed arrays of at most four; five is a bug, not a truncation."""
    pairs = [(20 + k, 1) for k in range(fw.L3_DSP_MAX_SPANS + 1)]
    assert localize(lib, 20, 53, *pairs) == []


# --- l3_dsp_spans_score -------------------------------------------------------------


def spans_score(lib, words: np.ndarray, *pairs, scored=None):
    loops, n_tx, _, bins, _ = words.shape
    flat = np.ascontiguousarray(words)
    ctx = fw.DspIq16Ctx(flat.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)), bins, n_tx, loops)
    obs = (fw.BinObs * fw.L3_DSP_MAX_BINS)()
    bitmap = scored if scored is not None else (ctypes.c_uint32 * fw.L3_DSP_BITMAP_WORDS)()
    newly = lib.l3_dsp_spans_score(
        spans(*pairs), len(pairs), bins, iq16_scorer(lib), ctypes.byref(ctx), obs, bitmap
    )
    return newly, obs, bitmap


def test_overlapping_spans_score_each_bin_once(lib):
    words = frame_words(np.random.default_rng(1), 8, 3, bins=40)
    newly, _, bitmap = spans_score(lib, words, (5, 10), (10, 10), (19, 2))
    assert newly == 16  # 5..20
    assert marked(bitmap) == list(range(5, 21))


def test_bins_already_scored_are_not_scored_again(lib):
    words = frame_words(np.random.default_rng(2), 8, 3, bins=40)
    bitmap = (ctypes.c_uint32 * fw.L3_DSP_BITMAP_WORDS)()
    bitmap[0] = 0b1111 << 8  # 8..11
    newly, obs, bitmap = spans_score(lib, words, (6, 8), scored=bitmap)
    assert newly == 4  # 6, 7, 12, 13
    assert marked(bitmap) == list(range(6, 14))
    assert obs[9].energy == 0.0, "a bin already marked is left alone"


def test_bins_past_the_frame_are_skipped(lib):
    words = frame_words(np.random.default_rng(3), 4, 2, bins=12)
    newly, _, bitmap = spans_score(lib, words, (10, 8))
    assert newly == 2 and marked(bitmap) == [10, 11]


def test_bins_past_64_are_skipped_on_a_wide_frame(lib):
    words = frame_words(np.random.default_rng(4), 2, 1, bins=80)
    newly, _, bitmap = spans_score(lib, words, (62, 10))
    assert newly == 2 and marked(bitmap) == [62, 63]


def test_a_bin_the_scorer_refuses_is_not_marked(lib):
    """A scorer returning nonzero leaves the bin for someone else."""
    calls = []

    @fw.BinScorer
    def refuse_odd(_ctx, local, out):
        calls.append(local)
        out.contents.energy = float(local)
        return local & 1

    obs = (fw.BinObs * fw.L3_DSP_MAX_BINS)()
    bitmap = (ctypes.c_uint32 * fw.L3_DSP_BITMAP_WORDS)()
    newly = lib.l3_dsp_spans_score(spans((0, 6)), 1, 10, refuse_odd, None, obs, bitmap)
    assert calls == [0, 1, 2, 3, 4, 5]
    assert newly == 3 and marked(bitmap) == [0, 2, 4]


def test_nothing_to_score_is_nothing_scored(lib):
    words = frame_words(np.random.default_rng(5), 2, 1, bins=8)
    newly, _, bitmap = spans_score(lib, words, (3, 0))
    assert newly == 0 and marked(bitmap) == []


# --- l3_dsp_request_check for SCORE ------------------------------------------------


@pytest.fixture
def words():
    return frame_words(np.random.default_rng(11), 16, 3, bins=53)


def test_a_score_inside_the_frame_is_accepted(lib, words):
    request = score_request(words, (8, 16), (24, 4))
    assert lib.l3_dsp_request_check(ctypes.byref(request), L3_BYTES) == fw.L3_DSP_OK


def test_score_needs_no_probe_bins(lib, words):
    """firstBin/nBins are PROBE's; a SCORE with them zero is fine."""
    request = score_request(words, (0, 1), firstBin=0, nBins=0)
    assert lib.l3_dsp_request_check(ctypes.byref(request), L3_BYTES) == fw.L3_DSP_OK


@pytest.mark.parametrize(
    ("local", "overrides", "status"),
    [
        ([], {}, fw.L3_DSP_ERR_SPANS),
        ([(0, 1)] * 5, {}, fw.L3_DSP_ERR_SPANS),
        ([(0, 0)], {}, fw.L3_DSP_ERR_SPANS),
        ([(53, 1)], {}, fw.L3_DSP_ERR_SPANS),
        ([(50, 4)], {}, fw.L3_DSP_ERR_SPANS),
        ([(1, 0xFFFFFFFF)], {}, fw.L3_DSP_ERR_SPANS),
        ([(8, 4)], {"loops": 0}, fw.L3_DSP_ERR_GEOMETRY),
        ([(8, 4)], {"ntx": 4}, fw.L3_DSP_ERR_GEOMETRY),
        ([(8, 4)], {"binCount": 0}, fw.L3_DSP_ERR_GEOMETRY),
        ([(8, 4)], {"frameOffset": 2}, fw.L3_DSP_ERR_RANGE),
        ([(8, 4)], {"frameOffset": L3_BYTES - 1024}, fw.L3_DSP_ERR_RANGE),
        ([(8, 4)], {"magic": 0}, fw.L3_DSP_ERR_MAGIC),
    ],
    ids=[
        "no spans",
        "five spans",
        "empty span",
        "span past the frame",
        "span running off the end",
        "count that would wrap",
        "no loops",
        "four tx",
        "empty frame",
        "misaligned",
        "frame past L3",
        "magic",
    ],
)
def test_a_bad_score_is_refused_with_its_reason(lib, words, local, overrides, status):
    # Five spans: the array holds four, so only the count says five.
    request = score_request(words, *local, **overrides)
    assert lib.l3_dsp_request_check(ctypes.byref(request), L3_BYTES) == status


def test_a_span_past_64_bins_is_refused_on_a_wide_frame(lib):
    wide = frame_words(np.random.default_rng(12), 2, 1, bins=80)
    request = score_request(wide, (60, 8))
    assert lib.l3_dsp_request_check(ctypes.byref(request), L3_BYTES) == fw.L3_DSP_ERR_SPANS


# --- l3_dsp_serve -------------------------------------------------------------------


def test_serve_scores_exactly_the_requested_bins(lib, words):
    arena = arena_with(words)
    reply, result = serve(lib, score_request(words, (8, 16), (20, 8), (40, 2)), arena)

    expected = sorted(set(range(8, 28)) | {40, 41})
    assert (reply.magic, reply.cmd, reply.seq, reply.status) == (
        fw.L3_DSP_MAGIC,
        fw.L3_DSP_CMD_SCORE,
        7,
        fw.L3_DSP_OK,
    )
    assert (result.magic, result.seq, result.epoch, result.status) == (
        fw.L3_DSP_RESULT_MAGIC,
        7,
        1234,
        fw.L3_DSP_OK,
    )
    assert marked(result.scored) == expected
    assert result.count == reply.nBins == len(expected)


def test_serve_matches_the_mss_bit_for_bit(lib, words):
    """The same scoring code on the same bytes: identical to the last bit."""
    arena = arena_with(words)
    _, result = serve(lib, score_request(words, (8, 16), (30, 5)), arena)
    mss = mss_scores(lib, words, marked(result.scored))
    for b, obs in mss.items():
        for name in FIELDS:
            assert bits(getattr(result.obs[b], name)) == bits(getattr(obs, name)), (b, name)


def test_serve_matches_the_replay_mirror(lib, words):
    arena = arena_with(words)
    _, result = serve(lib, score_request(words, (10, 27)), arena)
    table = bin_observation_table(as_cube(words), 0, 10, 27, 3)
    for k in range(27):
        for name in FIELDS:
            got = getattr(result.obs[10 + k], name)
            assert got == pytest.approx(float(table[name][k]), rel=1e-5, abs=1e-2), (k, name)


def test_a_refused_score_marks_nothing_and_says_why(lib, words):
    reply, result = serve(lib, score_request(words, (60, 4), seq=9, epoch=5), arena_with(words))
    assert reply.status == result.status == fw.L3_DSP_ERR_SPANS
    assert (result.magic, result.seq, result.epoch) == (fw.L3_DSP_RESULT_MAGIC, 9, 5)
    assert result.count == reply.nBins == 0 and marked(result.scored) == []


def test_a_score_without_l3_is_refused(lib, words):
    reply, result = serve(lib, score_request(words, (8, 4)), None)
    assert reply.status == result.status == fw.L3_DSP_ERR_RANGE
    assert result.count == 0


def test_a_score_without_a_result_block_is_refused(lib, words):
    reply = fw.DspReply()
    arena = arena_with(words)
    lib.l3_dsp_serve(
        ctypes.byref(score_request(words, (8, 4))),
        arena.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        L3_BYTES,
        ctypes.byref(reply),
        None,
    )
    assert reply.status == fw.L3_DSP_ERR_RANGE and reply.nBins == 0


def test_serve_still_answers_ping_and_probe_without_touching_the_result(lib, words):
    arena = arena_with(words)
    result = fw.DspResult(magic=0xABCD)
    for request in (
        fw.DspRequest(magic=fw.L3_DSP_MAGIC, cmd=fw.L3_DSP_CMD_PING, seq=3),
        score_request(words, cmd=fw.L3_DSP_CMD_PROBE, firstBin=0, nBins=5),
    ):
        reply = fw.DspReply()
        lib.l3_dsp_serve(
            ctypes.byref(request),
            arena.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            L3_BYTES,
            ctypes.byref(reply),
            ctypes.byref(result),
        )
        assert reply.status == fw.L3_DSP_OK and reply.cmd == request.cmd
    assert result.magic == 0xABCD


def test_serve_a_null_request_is_refused(lib):
    reply = fw.DspReply()
    lib.l3_dsp_serve(None, None, L3_BYTES, ctypes.byref(reply), None)
    assert reply.status == fw.L3_DSP_ERR_MAGIC


def test_serve_leaves_nothing_of_a_previous_answer(lib, words):
    """The block is reused every frame: a smaller answer must not keep old bins."""
    arena = arena_with(words)
    result = fw.DspResult()
    reply = fw.DspReply()
    for local, seq in (((0, 30),), 1), (((40, 2),), 2):
        lib.l3_dsp_serve(
            ctypes.byref(score_request(words, *local, seq=seq)),
            arena.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            L3_BYTES,
            ctypes.byref(reply),
            ctypes.byref(result),
        )
    assert marked(result.scored) == [40, 41] and result.seq == 2


# --- l3_dsp_result_check ------------------------------------------------------------


@pytest.fixture
def answered(lib, words):
    _, result = serve(lib, score_request(words, (8, 16), seq=21, epoch=300), arena_with(words))
    return result


def check(lib, result, seq=21, epoch=300) -> int:
    return lib.l3_dsp_result_check(ctypes.byref(result), seq, epoch)


def test_the_answer_to_this_request_is_accepted(lib, answered):
    assert check(lib, answered) == fw.L3_DSP_OK


@pytest.mark.parametrize(
    ("field", "value"),
    [("seq", 20), ("epoch", 299), ("magic", 0)],
    ids=["a late answer to the last request", "another frame's", "never written"],
)
def test_an_answer_to_something_else_is_stale(lib, answered, field, value):
    setattr(answered, field, value)
    assert check(lib, answered) == fw.L3_DSP_ERR_STALE


def test_a_count_that_disagrees_with_the_bins_is_stale(lib, answered):
    """A torn or corrupt block is never merged."""
    answered.count += 1
    assert check(lib, answered) == fw.L3_DSP_ERR_STALE


def test_a_refusal_passes_its_status_through(lib, answered):
    answered.status = fw.L3_DSP_ERR_GEOMETRY
    assert check(lib, answered) == fw.L3_DSP_ERR_GEOMETRY


def test_a_missing_result_is_stale(lib):
    assert lib.l3_dsp_result_check(None, 1, 1) == fw.L3_DSP_ERR_STALE


# --- l3_dsp_result_merge -------------------------------------------------------------


def test_merge_copies_the_answer_into_the_detectors_arrays(lib, answered):
    obs = (fw.BinObs * fw.L3_DSP_MAX_BINS)()
    bitmap = (ctypes.c_uint32 * fw.L3_DSP_BITMAP_WORDS)()
    assert lib.l3_dsp_result_merge(ctypes.byref(answered), obs, bitmap) == 16
    assert marked(bitmap) == list(range(8, 24))
    for b in range(8, 24):
        assert bits(obs[b].energy) == bits(answered.obs[b].energy)
    assert obs[7].energy == 0.0 and obs[24].energy == 0.0


def test_merge_keeps_what_the_detector_already_scored(lib, answered):
    obs = (fw.BinObs * fw.L3_DSP_MAX_BINS)()
    bitmap = (ctypes.c_uint32 * fw.L3_DSP_BITMAP_WORDS)()
    bitmap[0] = 1 << 10
    obs[10].energy = -1.0  # what the detector holds for bin 10
    assert lib.l3_dsp_result_merge(ctypes.byref(answered), obs, bitmap) == 15
    assert obs[10].energy == -1.0


def test_merge_of_nothing_is_nothing(lib):
    obs = (fw.BinObs * fw.L3_DSP_MAX_BINS)()
    bitmap = (ctypes.c_uint32 * fw.L3_DSP_BITMAP_WORDS)()
    assert lib.l3_dsp_result_merge(None, obs, bitmap) == 0


# --- l3_dsp_result_compare -------------------------------------------------------------


def mss_arrays(lib, words, local):
    obs = (fw.BinObs * fw.L3_DSP_MAX_BINS)()
    bitmap = (ctypes.c_uint32 * fw.L3_DSP_BITMAP_WORDS)()
    for b, value in mss_scores(lib, words, local).items():
        obs[b] = value
        bitmap[b >> 5] |= 1 << (b & 31)
    return obs, bitmap


def compare(lib, result, obs, bitmap):
    b, field = ctypes.c_uint32(99), ctypes.c_uint32(99)
    same = lib.l3_dsp_result_compare(
        ctypes.byref(result), obs, bitmap, ctypes.byref(b), ctypes.byref(field)
    )
    return same, b.value, field.value


def test_identical_scoring_compares_equal(lib, words, answered):
    obs, bitmap = mss_arrays(lib, words, list(range(8, 24)))
    assert compare(lib, answered, obs, bitmap)[0] == 1


@pytest.mark.parametrize("field", range(5))
def test_one_bit_in_any_field_is_a_mismatch_named(lib, words, answered, field):
    obs, bitmap = mss_arrays(lib, words, list(range(8, 24)))
    name = FIELDS[field]
    value = np.float32(getattr(obs[13], name))
    setattr(obs[13], name, float(np.nextafter(value, np.float32(np.inf))))
    assert compare(lib, answered, obs, bitmap) == (0, 13, field)


def test_a_bin_only_one_core_scored_is_a_mismatch(lib, words, answered):
    obs, bitmap = mss_arrays(lib, words, list(range(8, 25)))  # one more
    assert compare(lib, answered, obs, bitmap) == (0, 24, fw.L3_DSP_FIELD_NAMES.index("set"))


def test_the_first_differing_bin_is_the_one_reported(lib, words, answered):
    obs, bitmap = mss_arrays(lib, words, list(range(8, 24)))
    obs[20].peak *= 2.0  # scaled, not offset: +1 vanishes in a large float32
    obs[11].r1Im *= 2.0
    assert compare(lib, answered, obs, bitmap)[:2] == (0, 11)


def test_compare_needs_both_sides(lib, answered):
    assert lib.l3_dsp_result_compare(ctypes.byref(answered), None, None, None, None) == 0


# --- the whole path over the committed recordings -------------------------------------
#
# Each recording's frames through the detector's scan plan (l3_scan_pre with a
# band around the tee, l3_scan_post as after impact), localized, served by
# SCORE, and merged, against the MSS's per-bin scoring (bit for bit) and the
# replay's numpy mirror (to float tolerance). Real returns, real span layouts.


def _recordings():
    try:
        return recording_configs()
    except (FileNotFoundError, ValueError):
        return []


RECORDINGS = _recordings()


def _as_words(cube: np.ndarray, frame: int, n_tx: int) -> np.ndarray:
    chirps, n_rx, bins = cube.shape[1:]
    data = cube[frame].reshape(chirps // n_tx, n_tx, n_rx, bins)
    assert np.all(np.abs(data.real) <= 32767) and np.all(np.abs(data.imag) <= 32767)
    words = np.empty(data.shape + (2,), dtype=np.int16)
    words[..., 0] = np.round(data.imag).astype(np.int16)
    words[..., 1] = np.round(data.real).astype(np.int16)
    return words


def _plan_spans(lib, window_first: int, window_count: int, tee: int):
    cfg = fw.ScanCfg()
    lib.l3_scan_cfg_defaults(ctypes.byref(cfg))
    band = fw.Band()
    band.valid, band.loBin, band.hiBin = 1, tee - 2.5, tee + 2.5
    region, club, leave = fw.Span(), fw.Span(), fw.Span()
    lib.l3_scan_pre(
        ctypes.byref(cfg),
        window_first,
        window_count,
        window_first,
        window_count,
        ctypes.byref(band),
        ctypes.byref(region),
        ctypes.byref(club),
        ctypes.byref(leave),
    )
    ball, club_post = fw.Span(), fw.Span()
    lib.l3_scan_post(
        ctypes.byref(cfg),
        window_first,
        window_count,
        ctypes.byref(band),
        1,
        float(tee + 6),
        1,
        float(tee + 3),
        ctypes.byref(ball),
        ctypes.byref(club_post),
    )
    return [region, club, leave], [ball, club_post]


@pytest.mark.skipif(not RECORDINGS, reason="no committed recordings")
@pytest.mark.parametrize(
    ("path", "config"), RECORDINGS[:6], ids=[p.name for p, _ in RECORDINGS[:6]]
)
def test_score_over_a_recording_is_the_mss_and_the_replay(lib, path, config):
    meta, cube = parse_dump(path.read_bytes())
    n_tx = meta["n_tx"]
    starts = meta.get("range_bin_starts") or (meta.get("range_bin_start", 0),) * cube.shape[0]
    frames = range(0, cube.shape[0], max(1, cube.shape[0] // 6))
    checked = 0
    for frame in frames:
        words = _as_words(cube, frame, n_tx)
        bins = words.shape[3]
        tee = min(max(config.tee_bin, starts[frame] + 4), starts[frame] + bins - 5)
        for plan in _plan_spans(lib, starts[frame], bins, tee):
            local = (fw.Span * fw.L3_DSP_MAX_SPANS)()
            n = lib.l3_dsp_spans_localize(
                starts[frame], bins, (fw.Span * len(plan))(*plan), len(plan), local
            )
            if n == 0:
                continue
            pairs = [(local[k].first, local[k].count) for k in range(n)]
            request = score_request(words, *pairs)
            reply, result = serve(lib, request, arena_with(words))
            assert reply.status == fw.L3_DSP_OK, (path.name, frame, pairs)
            want = sorted({b for first, count in pairs for b in range(first, first + count)})
            assert marked(result.scored) == want
            mss = mss_scores(lib, words, want)
            table = bin_observation_table(as_cube(words), 0, 0, bins, n_tx)
            for b in want:
                for name in FIELDS:
                    got = getattr(result.obs[b], name)
                    assert bits(got) == bits(getattr(mss[b], name)), (path.name, frame, b, name)
                    assert got == pytest.approx(float(table[name][b]), rel=1e-4, abs=1.0)
            checked += len(want)
    assert checked > 0
