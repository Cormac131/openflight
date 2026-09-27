"""Native tests for lossless IQ16 range-window compaction (firmware/iwr6843/compact_iq16.c).

The compaction is the retention engine's one data-moving primitive: a full
HWA frame in, only the chosen bins out, every retained I/Q pair bit for bit
the value the HWA wrote. Nothing here may change a sample.
"""

from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "firmware" / "iwr6843" / "compact_iq16.c"


@pytest.fixture(scope="module")
def compact(tmp_path_factory: pytest.TempPathFactory):
    library = tmp_path_factory.mktemp("compact-iq16") / "compact_iq16.so"
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(library),
            str(SOURCE),
        ],
        check=True,
    )
    function = ctypes.CDLL(str(library)).l3_compact_iq16
    function.argtypes = [
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint16,
    ]
    function.restype = ctypes.c_int32
    return function


def frame(chirps: int, receivers: int, bins: int, seed: int = 1) -> np.ndarray:
    """Random full-range int16 [chirps, rx, bins, (Im, Re)] with every value distinct enough."""
    rng = np.random.default_rng(seed)
    values = rng.integers(-32768, 32768, size=(chirps, receivers, bins, 2), dtype=np.int64)
    values[0, 0, 0] = (-32768, 32767)  # the extremes survive too
    return values.astype(np.int16)


def run(compact, source: np.ndarray, bin_start: int, bin_count: int) -> np.ndarray:
    chirps, receivers, bins, _ = source.shape
    flat = np.ascontiguousarray(source.reshape(-1))
    output = np.full(chirps * receivers * bin_count * 2, 0x5A5A, dtype=np.int16)
    status = compact(
        flat.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
        chirps,
        receivers,
        bins,
        bin_start,
        bin_count,
    )
    assert status == 0
    return output.reshape(chirps, receivers, bin_count, 2)


@pytest.mark.parametrize(
    ("bin_start", "bin_count"),
    [
        (0, 1),  # the first bin alone
        (127, 1),  # the last bin alone
        (47, 1),  # a single bin mid-range
        (44, 8),
        (40, 16),
        (32, 32),
        (0, 128),  # the whole frame
        (96, 32),  # the window ending on the last bin
    ],
)
def test_every_window_is_the_source_cropped_bit_for_bit(compact, bin_start, bin_count):
    source = frame(chirps=36, receivers=4, bins=128)
    output = run(compact, source, bin_start, bin_count)
    np.testing.assert_array_equal(output, source[:, :, bin_start : bin_start + bin_count])


@pytest.mark.parametrize("chirps", [1, 2, 3, 12, 24, 36])
def test_all_chirps_and_all_rx_are_kept_in_tdm_order(compact, chirps):
    source = frame(chirps=chirps, receivers=4, bins=64, seed=chirps)
    output = run(compact, source, 20, 16)
    for chirp in range(chirps):
        for rx in range(4):
            np.testing.assert_array_equal(output[chirp, rx], source[chirp, rx, 20:36])


def test_iq_component_order_is_preserved(compact):
    source = np.zeros((2, 4, 8, 2), dtype=np.int16)
    source[..., 0] = -7  # Im first, as the HWA writes (TI ImRe)
    source[..., 1] = 9
    output = run(compact, source, 2, 4)
    assert set(output[..., 0].reshape(-1).tolist()) == {-7}
    assert set(output[..., 1].reshape(-1).tolist()) == {9}


def test_compaction_is_lossless_against_the_source_values(compact):
    """The retained window has exactly the source's bytes: no scaling, no rounding, no clip."""
    source = frame(chirps=6, receivers=4, bins=128, seed=7)
    output = run(compact, source, 30, 24)
    assert output.tobytes() == np.ascontiguousarray(source[:, :, 30:54]).tobytes()


def test_destination_is_dense_with_no_padding_between_receivers(compact):
    chirps, receivers, bins = 3, 4, 32
    source = frame(chirps, receivers, bins, seed=3)
    flat = np.ascontiguousarray(source.reshape(-1))
    output = np.zeros(chirps * receivers * 8 * 2 + 4, dtype=np.int16)  # 4 words of guard
    output[-4:] = 0x7777
    assert (
        compact(
            flat.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
            output.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
            chirps,
            receivers,
            bins,
            10,
            8,
        )
        == 0
    )
    assert output[-4:].tolist() == [0x7777] * 4, "nothing written past the compact frame"
    expected = source[:, :, 10:18].reshape(-1)
    np.testing.assert_array_equal(output[:-4], expected)


@pytest.mark.parametrize(
    ("chirps", "receivers", "bins", "bin_start", "bin_count"),
    [
        (1, 4, 128, 128, 1),  # start past the frame
        (1, 4, 128, 120, 9),  # window past the frame
        (1, 4, 128, 0, 0),  # empty window
        (0, 4, 128, 0, 8),  # no chirps
        (1, 0, 128, 0, 8),  # no receivers
        (1, 4, 0, 0, 8),  # no bins
        (1, 4, 128, 0, 129),  # wider than the frame
    ],
)
def test_rejects_invalid_geometry_without_touching_destination(
    compact, chirps, receivers, bins, bin_start, bin_count
):
    source = (ctypes.c_int16 * (4 * 128 * 2))()
    output = (ctypes.c_int16 * 16)(*([1234] * 16))

    assert compact(source, output, chirps, receivers, bins, bin_start, bin_count) == -1
    assert list(output) == [1234] * 16


def test_null_pointers_are_rejected(compact):
    output = (ctypes.c_int16 * 16)()
    source = (ctypes.c_int16 * 16)()
    assert compact(None, output, 1, 1, 8, 0, 1) == -1
    assert compact(source, None, 1, 1, 8, 0, 1) == -1
