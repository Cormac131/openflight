"""The range-FFT window, firmware/iwr6843/l3_window.c.

The HWA multiplies each chirp's ADC samples by a window held in its window
RAM before the range FFT. With HWA_FFT_WINDOW_SYMMETRIC it holds the first
half and mirrors it, in the Q17 format TI's demos use (mathUtils_genWindow,
DPC_OBJDET_QFORMAT_RANGEFFT_WINDOW). The board ran with no window until
2026-10-01; the replay suggested a Hann window keeps the golfer's leakage off
the club, but a window applied after the FFT is not the board's (a symmetric
window and the HWA's fixed point differ), so the board records it itself.
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from openflight.iwr6843 import firmware_host as fw

Q17 = 1 << 17


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def half_window(lib, n: int):
    out = (ctypes.c_int32 * max(1, n // 2))()
    written = lib.l3_window_hann_q17(out, n)
    return written, [out[i] for i in range(written)]


def test_the_half_window_is_a_symmetric_hann_in_q17(lib):
    written, coeffs = half_window(lib, 128)
    assert written == 64
    want = np.round(np.hanning(128)[:64] * Q17)
    assert np.max(np.abs(np.asarray(coeffs) - want)) <= 1


def test_mirrored_it_is_the_whole_window(lib):
    _, coeffs = half_window(lib, 128)
    whole = np.concatenate([coeffs, coeffs[::-1]]) / Q17
    assert np.allclose(whole, np.hanning(128), atol=2.0 / Q17)


def test_it_starts_at_zero_and_rises_to_nearly_one(lib):
    _, coeffs = half_window(lib, 128)
    assert coeffs[0] == 0
    assert all(b >= a for a, b in zip(coeffs, coeffs[1:]))
    assert coeffs[-1] == pytest.approx(Q17, rel=1e-3)


@pytest.mark.parametrize("n", [0, 2, 127, fw.WINDOW_MAX_SAMPLES + 2])
def test_a_size_the_hwa_cannot_take_writes_nothing(lib, n):
    out = (ctypes.c_int32 * 4)(7, 7, 7, 7)
    assert lib.l3_window_hann_q17(out, n) == 0
    assert list(out) == [7, 7, 7, 7]


@pytest.mark.parametrize(
    ("text", "window"),
    [(b"none", fw.RANGE_WINDOW_NONE), (b"hann", fw.RANGE_WINDOW_HANN)],
)
def test_names_parse_and_round_trip(lib, text, window):
    out = ctypes.c_uint8(99)
    assert lib.l3_window_parse(text, ctypes.byref(out)) == 0
    assert out.value == window
    assert lib.l3_window_name(window) == text


@pytest.mark.parametrize("text", [b"", b"Hann", b"blackman", b"hann "])
def test_other_names_are_refused(lib, text):
    out = ctypes.c_uint8(99)
    assert lib.l3_window_parse(text, ctypes.byref(out)) != 0
    assert out.value == 99


def test_an_unknown_window_has_a_placeholder_name(lib):
    assert lib.l3_window_name(200) == b"?"
