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
    ]
    library.l3detect_publish.restype = ctypes.c_int32
    library.l3detect_pop.argtypes = [
        ctypes.POINTER(Queue),
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    library.l3detect_pop.restype = ctypes.c_int32
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


def test_publish_and_pop_keep_capture_order(lib):
    queue = _queue(lib)
    slot = ctypes.c_uint16()
    epoch = ctypes.c_uint32()

    assert lib.l3detect_publish(ctypes.byref(queue), 2, 3) == 0
    assert lib.l3detect_publish(ctypes.byref(queue), 3, 4) == 0
    assert lib.l3detect_pop(ctypes.byref(queue), ctypes.byref(slot), ctypes.byref(epoch)) == 1
    assert (slot.value, epoch.value) == (2, 3)
    assert lib.l3detect_pop(ctypes.byref(queue), ctypes.byref(slot), ctypes.byref(epoch)) == 1
    assert (slot.value, epoch.value) == (3, 4)
    assert lib.l3detect_pop(ctypes.byref(queue), ctypes.byref(slot), ctypes.byref(epoch)) == 0
    assert queue.published == 2
    assert queue.dropped == 0


def test_full_queue_drops_the_new_slot_and_keeps_the_oldest(lib):
    queue = _queue(lib)
    slot = ctypes.c_uint16()
    epoch = ctypes.c_uint32()

    for index in range(DEPTH):
        assert lib.l3detect_publish(ctypes.byref(queue), index, index + 1) == 0
    assert lib.l3detect_publish(ctypes.byref(queue), 99, 1000) == -1
    assert queue.dropped == 1
    assert lib.l3detect_pop(ctypes.byref(queue), ctypes.byref(slot), ctypes.byref(epoch)) == 1
    assert (slot.value, epoch.value) == (0, 1)
    assert lib.l3detect_publish(ctypes.byref(queue), 7, 70) == 0


def test_slot_stays_live_until_the_writer_is_about_to_reuse_it(lib):
    assert lib.l3detect_slot_live(1, 1, 8) == 1
    assert lib.l3detect_slot_live(1, 7, 8) == 1
    assert lib.l3detect_slot_live(1, 8, 8) == 0
    assert lib.l3detect_slot_live(5, 4, 8) == 0
    assert lib.l3detect_slot_live(1, 1, 1) == 0


def test_header_matches_the_compiled_contract():
    header = HEADER.read_text(encoding="utf-8")

    assert "L3_DETECT_QUEUE_DEPTH 64U" in header
    assert "int32_t l3detect_publish(" in header
    assert "int32_t l3detect_slot_live(" in header
