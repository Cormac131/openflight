"""Host build of the firmware capture-plan arithmetic.

l3_buildCapturePlan decides the byte budget and every frame's offset in L3.
This file is plain C99 so the host compiler can check that arithmetic without
the TI toolchain, mirroring tests/test_iwr6843_detect_queue.py.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "firmware" / "iwr6843" / "capture_plan.c"
MAX_FRAMES = 64


class Plan(ctypes.Structure):
    _fields_ = [
        ("preStart", ctypes.c_uint8),
        ("preBins", ctypes.c_uint8),
        ("postStart", ctypes.c_uint8),
        ("postBins", ctypes.c_uint8),
        ("lateStart", ctypes.c_uint8),
        ("postFrames", ctypes.c_uint8),
        ("postStride", ctypes.c_uint8),
        ("preFrames", ctypes.c_uint8),
        ("totalFrames", ctypes.c_uint8),
        ("loops", ctypes.c_uint16),
        ("chirpsPerFrame", ctypes.c_uint16),
        ("preFrameBytes", ctypes.c_uint32),
        ("postFrameBytes", ctypes.c_uint32),
        ("postBaseOffset", ctypes.c_uint32),
        ("usedBytes", ctypes.c_uint32),
        ("phased", ctypes.c_uint8),
        ("requestedPreFrames", ctypes.c_uint8),
        ("impactStart", ctypes.c_uint8),
        ("impactBins", ctypes.c_uint8),
        ("impactFrames", ctypes.c_uint8),
        ("ballFrames", ctypes.c_uint8),
        ("impactFrameBytes", ctypes.c_uint32),
    ]


class Geometry(ctypes.Structure):
    _fields_ = [
        ("nTx", ctypes.c_uint32),
        ("nRx", ctypes.c_uint32),
        ("maxSamples", ctypes.c_uint32),
        ("maxCaptureFrames", ctypes.c_uint32),
        ("maxLoops", ctypes.c_uint32),
        ("minLoops", ctypes.c_uint32),
        ("maxBins", ctypes.c_uint32),
        ("maxPostStride", ctypes.c_uint32),
    ]


class Tables(ctypes.Structure):
    _fields_ = [
        ("binStart", ctypes.POINTER(ctypes.c_uint8)),
        ("binCount", ctypes.POINTER(ctypes.c_uint8)),
        ("deltaUs", ctypes.POINTER(ctypes.c_uint16)),
        ("offset", ctypes.POINTER(ctypes.c_uint32)),
        ("bytes", ctypes.POINTER(ctypes.c_uint32)),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no host C compiler to build capture_plan.c")
    suffix = ".dll" if sys.platform == "win32" else ".so"
    out = tmp_path_factory.mktemp("capture_plan") / f"libcapture_plan{suffix}"
    cmd = [
        compiler,
        "-std=c99",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-shared",
        "-o",
        str(out),
        str(SOURCE),
    ]
    if sys.platform != "win32":
        cmd.insert(6, "-fPIC")
    subprocess.run(cmd, check=True)
    library = ctypes.CDLL(str(out))
    library.l3plan_build.argtypes = [
        ctypes.POINTER(Plan),
        ctypes.POINTER(Geometry),
        ctypes.POINTER(Tables),
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    library.l3plan_build.restype = ctypes.c_int32
    library.l3plan_sizeof_plan.argtypes = []
    library.l3plan_sizeof_plan.restype = ctypes.c_uint32
    return library


def test_ctypes_plan_matches_c_layout(lib):
    """Guard against a silent field-layout mismatch between the ctypes Plan
    mirror and the compiled L3CapturePlan: ask the C side for its own
    sizeof() rather than assuming natural alignment agrees with Python's
    guess. A mismatch here would otherwise show up as every other test's
    field assertions reading garbage instead of failing loudly."""
    assert ctypes.sizeof(Plan) == lib.l3plan_sizeof_plan()


def _geometry():
    return Geometry(3, 4, 128, MAX_FRAMES, 16, 2, 64, 16)


def _tables():
    store = {
        "binStart": (ctypes.c_uint8 * MAX_FRAMES)(),
        "binCount": (ctypes.c_uint8 * MAX_FRAMES)(),
        "deltaUs": (ctypes.c_uint16 * MAX_FRAMES)(),
        "offset": (ctypes.c_uint32 * MAX_FRAMES)(),
        "bytes": (ctypes.c_uint32 * MAX_FRAMES)(),
    }
    tables = Tables(
        store["binStart"],
        store["binCount"],
        store["deltaUs"],
        store["offset"],
        store["bytes"],
    )
    return tables, store


def _dense_plan():
    plan = Plan()
    plan.preStart, plan.preBins, plan.requestedPreFrames = 20, 53, 8
    plan.impactStart, plan.impactBins, plan.impactFrames = 32, 53, 10
    plan.postStart, plan.postBins, plan.lateStart = 47, 53, 64
    plan.ballFrames, plan.postStride = 33, 1
    plan.postFrames = plan.impactFrames + plan.ballFrames
    plan.phased = 1
    return plan


def _build(lib, plan, capacity, loops=12, period=2000, bpc=2):
    tables, store = _tables()
    err = ctypes.create_string_buffer(128)
    rc = lib.l3plan_build(
        ctypes.byref(plan),
        ctypes.byref(_geometry()),
        ctypes.byref(tables),
        loops,
        period,
        capacity,
        bpc,
        err,
        len(err),
    )
    return rc, store, err.value.decode()


def test_relocated_arena_fits_fifty_one_frames(lib):
    plan = _dense_plan()
    rc, _store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    assert plan.totalFrames == 51
    assert plan.usedBytes == 51 * 15_264


def test_plan_that_exactly_fills_the_arena_is_accepted(lib):
    plan = _dense_plan()
    rc, _store, err = _build(lib, plan, 51 * 15_264)
    assert rc == 0, err
    assert plan.usedBytes == 51 * 15_264


def test_plan_one_byte_short_is_rejected(lib):
    plan = _dense_plan()
    rc, _store, err = _build(lib, plan, 51 * 15_264 - 1)
    assert rc == -1
    assert err != ""


def test_single_pre_frame_ring_is_valid(lib):
    plan = _dense_plan()
    plan.requestedPreFrames = 1
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    assert plan.preFrames == 1
    assert store["offset"][0] == 0


def test_odd_loop_count_is_rejected(lib):
    plan = _dense_plan()
    rc, _store, _err = _build(lib, plan, 786_432, loops=11)
    assert rc == -1


def test_frame_offsets_never_overlap(lib):
    plan = _dense_plan()
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    spans = [
        (store["offset"][i], store["offset"][i] + store["bytes"][i])
        for i in range(plan.totalFrames)
    ]
    for (_start_a, end_a), (start_b, _end_b) in zip(spans, spans[1:]):
        assert end_a <= start_b
    assert spans[-1][1] == plan.usedBytes


@pytest.mark.parametrize("pre_frames", range(1, 17))
@pytest.mark.parametrize("ball_frames", (1, 7, 19, 33))
def test_no_valid_plan_overlaps_or_overflows(lib, pre_frames, ball_frames):
    plan = _dense_plan()
    plan.requestedPreFrames = pre_frames
    plan.ballFrames = ball_frames
    plan.postFrames = plan.impactFrames + ball_frames
    rc, store, _err = _build(lib, plan, 786_432)
    if rc != 0:
        return
    cursor = 0
    for i in range(plan.totalFrames):
        assert store["offset"][i] == cursor
        cursor += store["bytes"][i]
    assert cursor == plan.usedBytes
    assert plan.usedBytes <= 786_432


def _non_phased_plan(post_frames=16):
    plan = Plan()
    plan.preStart, plan.preBins = 20, 53
    plan.postStart, plan.postBins, plan.lateStart = 47, 53, 64
    plan.postFrames = post_frames
    plan.postStride = 1
    plan.phased = 0
    return plan


def test_phased_binStart_and_binCount_routing(lib):
    """Behavioural coverage of the bin-start/bin-count routing tables: the
    only other place this arithmetic was checked was a source-text grep in
    test_iwr6843_firmware_rearm.py, which cannot see whether the C actually
    puts the right value in the right slot."""
    plan = _dense_plan()
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    assert plan.preFrames == 8
    assert plan.impactFrames == 10
    assert plan.ballFrames == 33

    for i in range(0, 8):
        assert store["binStart"][i] == 20
        assert store["binCount"][i] == 53
    for i in range(8, 18):
        assert store["binStart"][i] == 32
        assert store["binCount"][i] == 53
    for i in range(18, 34):
        assert store["binStart"][i] == 47
        assert store["binCount"][i] == 53
    for i in range(34, 51):
        assert store["binStart"][i] == 64
        assert store["binCount"][i] == 53


@pytest.mark.parametrize("ball_frames", (6, 7, 20, 21))
def test_early_late_split_boundary_is_exact_for_phased_plan(lib, ball_frames):
    plan = _dense_plan()
    plan.ballFrames = ball_frames
    plan.postFrames = plan.impactFrames + ball_frames
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err

    boundary = plan.preFrames + plan.impactFrames + (ball_frames // 2) - 1
    assert store["binStart"][boundary] == 47
    assert store["binStart"][boundary + 1] == 64


def test_non_phased_binStart_and_binCount_routing(lib):
    plan = _non_phased_plan()
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    assert plan.preFrames == 35
    assert plan.totalFrames == 51

    for i in range(0, 35):
        assert store["binStart"][i] == 20
        assert store["binCount"][i] == 53
    for i in range(35, 43):
        assert store["binStart"][i] == 47
        assert store["binCount"][i] == 53
    for i in range(43, 51):
        assert store["binStart"][i] == 64
        assert store["binCount"][i] == 53


@pytest.mark.parametrize("post_frames", (6, 7, 20, 21))
def test_early_late_split_boundary_is_exact_for_non_phased_plan(lib, post_frames):
    plan = _non_phased_plan(post_frames=post_frames)
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err

    boundary = plan.preFrames + (post_frames // 2) - 1
    assert store["binStart"][boundary] == 47
    assert store["binStart"][boundary + 1] == 64
