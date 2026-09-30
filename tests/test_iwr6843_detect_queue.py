"""Host build of the firmware completed-slot queue.

The self-trigger reads a ring slot only after capture has stored it, and only
while the writer is still on a later slot. This file is plain C99 so the host
compiler can check that contract without the TI toolchain.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "firmware" / "iwr6843" / "detect_queue.c"
HEADER = SOURCE.with_suffix(".h")
DEPTH = 64


class Queue(ctypes.Structure):
    _fields_ = [
        ("slot", ctypes.c_uint16 * DEPTH),
        ("epoch", ctypes.c_uint32 * DEPTH),
        ("stamp", ctypes.c_uint32 * DEPTH),
        ("head", ctypes.c_uint32),
        ("tail", ctypes.c_uint32),
        ("dropped", ctypes.c_uint32),
        ("published", ctypes.c_uint32),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no host C compiler to build detect_queue.c")
    suffix = ".dll" if sys.platform == "win32" else ".so"
    out = tmp_path_factory.mktemp("detect_queue") / f"libdetect_queue{suffix}"
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
    library.l3detect_init.argtypes = [ctypes.POINTER(Queue)]
    library.l3detect_init.restype = None
    library.l3detect_publish.argtypes = [
        ctypes.POINTER(Queue),
        ctypes.c_uint16,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    library.l3detect_publish.restype = ctypes.c_int32
    library.l3detect_pop.argtypes = [
        ctypes.POINTER(Queue),
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    library.l3detect_pop.restype = ctypes.c_int32
    library.l3detect_depth.argtypes = [ctypes.POINTER(Queue)]
    library.l3detect_depth.restype = ctypes.c_uint32
    library.l3detect_slot_live.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    library.l3detect_slot_live.restype = ctypes.c_int32
    return library


def _queue(lib) -> Queue:
    queue = Queue()
    lib.l3detect_init(ctypes.byref(queue))
    return queue


def _publish(lib, queue: Queue, slot: int, epoch: int, stamp: int = 0) -> int:
    return lib.l3detect_publish(ctypes.byref(queue), slot, epoch, stamp)


def _pop(lib, queue: Queue) -> tuple[int, int, int] | None:
    slot = ctypes.c_uint16()
    epoch = ctypes.c_uint32()
    stamp = ctypes.c_uint32()
    got = lib.l3detect_pop(
        ctypes.byref(queue), ctypes.byref(slot), ctypes.byref(epoch), ctypes.byref(stamp)
    )
    return (slot.value, epoch.value, stamp.value) if got == 1 else None


def test_publish_and_pop_keep_capture_order(lib):
    queue = _queue(lib)

    assert _publish(lib, queue, 2, 3, 1000) == 0
    assert _publish(lib, queue, 3, 4, 2000) == 0
    assert _pop(lib, queue) == (2, 3, 1000)
    assert _pop(lib, queue) == (3, 4, 2000)
    assert _pop(lib, queue) is None
    assert queue.published == 2
    assert queue.dropped == 0


def test_the_acquisition_stamp_travels_with_its_slot(lib):
    """l3_timing measures latency from the stamp the writer published."""
    queue = _queue(lib)
    _publish(lib, queue, 5, 9, 0xFFFFFFF0)  # just before the cycle counter wraps
    _publish(lib, queue, 6, 10, 0x00000010)
    assert _pop(lib, queue)[2] == 0xFFFFFFF0
    assert _pop(lib, queue)[2] == 0x00000010


def test_depth_counts_what_waits(lib):
    queue = _queue(lib)
    assert lib.l3detect_depth(ctypes.byref(queue)) == 0
    for index in range(3):
        _publish(lib, queue, index, index + 1)
    assert lib.l3detect_depth(ctypes.byref(queue)) == 3
    _pop(lib, queue)
    assert lib.l3detect_depth(ctypes.byref(queue)) == 2


def test_depth_survives_the_indices_wrapping(lib):
    queue = _queue(lib)
    queue.head = queue.tail = 0xFFFFFFFE
    for index in range(4):
        assert _publish(lib, queue, index, index + 1) == 0
    assert lib.l3detect_depth(ctypes.byref(queue)) == 4
    assert [_pop(lib, queue)[0] for _ in range(4)] == [0, 1, 2, 3]
    assert lib.l3detect_depth(ctypes.byref(queue)) == 0


def test_full_queue_drops_the_new_slot_and_keeps_the_oldest(lib):
    queue = _queue(lib)

    for index in range(DEPTH):
        assert _publish(lib, queue, index, index + 1) == 0
    assert _publish(lib, queue, 99, 1000) == -1
    assert queue.dropped == 1
    assert lib.l3detect_depth(ctypes.byref(queue)) == DEPTH
    assert _pop(lib, queue)[:2] == (0, 1)
    assert _publish(lib, queue, 7, 70) == 0


def test_slot_stays_live_until_the_writer_is_about_to_reuse_it(lib):
    assert lib.l3detect_slot_live(1, 1, 8) == 1
    assert lib.l3detect_slot_live(1, 7, 8) == 1
    assert lib.l3detect_slot_live(1, 8, 8) == 0
    assert lib.l3detect_slot_live(5, 4, 8) == 0
    assert lib.l3detect_slot_live(1, 1, 1) == 0


# --- the re-check once a slot has been read ----------------------------------
#
# Liveness used to be checked only when the detect task popped a slot. A
# read that takes longer (the DSS: an IPC round trip, a cache invalidate, a
# late answer) can straddle the writer reaching the slot, and the scores are
# then of two frames mixed. The detect task checks again after the read with
# the same rule; the writer only moves forward, so live after means live
# throughout.

RING = 8


def _read(lib, epoch: int, captured_at_pop: int, captured_after_read: int) -> str:
    """What the detect task does with a slot: skip, discard, or use."""
    if not lib.l3detect_slot_live(epoch, captured_at_pop, RING):
        return "stale_at_pop"
    if not lib.l3detect_slot_live(epoch, captured_after_read, RING):
        return "stale_after_read"
    return "used"


def test_a_quick_read_of_a_fresh_slot_is_used(lib):
    assert _read(lib, 10, 10, 11) == "used"


def test_a_read_the_writer_caught_up_with_is_discarded(lib):
    """Live when popped (6 frames behind of 8), the writer reached it mid-read."""
    assert _read(lib, 10, 16, 17) == "stale_after_read"


def test_the_last_live_frame_is_still_used(lib):
    """current - epoch == ring - 2: the writer is on the slot before this one."""
    assert _read(lib, 10, 10, 10 + RING - 2) == "used"
    assert _read(lib, 10, 10, 10 + RING - 1) == "stale_after_read"


def test_a_slot_already_reused_is_never_read(lib):
    assert _read(lib, 10, 10 + RING - 1, 10 + RING) == "stale_at_pop"


def test_a_ring_too_small_to_overlap_is_never_read(lib):
    for ring in (0, 1):
        assert lib.l3detect_slot_live(5, 5, ring) == 0


def test_a_session_reset_under_a_read_is_discarded(lib):
    """sensorStart zeroes the capture count: current < epoch reads stale."""
    assert lib.l3detect_slot_live(10, 0, RING) == 0


def test_header_matches_the_compiled_contract():
    header = HEADER.read_text(encoding="utf-8")

    assert "L3_DETECT_QUEUE_DEPTH 64U" in header
    assert "int32_t l3detect_publish(" in header
    assert "uint32_t l3detect_depth(" in header
    assert "int32_t l3detect_slot_live(" in header
