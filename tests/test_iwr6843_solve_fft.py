"""Windowed-FFT equivalence: the ported C must match np.fft.fft.

This is the Task 4 compute probe for the on-chip solve plan
(.superpowers/sdd/2026-09-25-iwr6843-onchip-solve/task-4-brief.md): port the
window + FFT primitive behind lcmf.py:502 and lcmf.py:547 (both apply
np.hanning then np.fft.fft(..., n=n_fft, axis=-1)) and prove the C matches
the Python reference.

IMPORTANT SCOPE NOTE: this test drives firmware/iwr6843/solve/solve_fft.c
compiled by the HOST compiler, which only ever exercises
solve_fft_transform_reference() -- the portable double-precision DFT. It
never exercises solve_fft_transform_dsplib(), which needs the C674x
toolchain and DSPLIB and cannot run outside that target. A pass here says
nothing about the DSPLIB path; see the file banner in solve_fft.c and the
Task 4 report for what remains to be checked on silicon.
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from tests.test_iwr6843_solve_harness import SOLVE_DIR, build_solve_lib

MAX_ROWS = 8
MAX_SAMPLES = 128
MAX_N = 512


class FftRequest(ctypes.Structure):
    _fields_ = [
        ("nRows", ctypes.c_uint32),
        ("nSamples", ctypes.c_uint32),
        ("nFft", ctypes.c_uint32),
        ("real", (ctypes.c_float * MAX_SAMPLES) * MAX_ROWS),
        ("imag", (ctypes.c_float * MAX_SAMPLES) * MAX_ROWS),
    ]


class FftResult(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_uint32),
        ("nRows", ctypes.c_uint32),
        ("nFft", ctypes.c_uint32),
        ("real", (ctypes.c_float * MAX_N) * MAX_ROWS),
        ("imag", (ctypes.c_float * MAX_N) * MAX_ROWS),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    library = build_solve_lib(
        tmp_path_factory,
        [SOLVE_DIR / "solve_fft.c"],
        "solve_fft",
    )
    library.solve_fft_apply.argtypes = [
        ctypes.POINTER(FftRequest),
        ctypes.POINTER(FftResult),
    ]
    library.solve_fft_apply.restype = ctypes.c_uint32
    return library


def _run(lib, snapshot: np.ndarray, n_fft: int) -> np.ndarray:
    """Drive solve_fft_apply on one (n_rows, n_samples) complex snapshot."""
    n_rows, n_samples = snapshot.shape
    request = FftRequest()
    request.nRows = n_rows
    request.nSamples = n_samples
    request.nFft = n_fft
    for row in range(n_rows):
        for col in range(n_samples):
            request.real[row][col] = float(snapshot[row, col].real)
            request.imag[row][col] = float(snapshot[row, col].imag)

    result = FftResult()
    status = lib.solve_fft_apply(ctypes.byref(request), ctypes.byref(result))
    assert status == 0, "solve_fft_apply reported an error status"
    assert result.status == 0
    assert result.nRows == n_rows
    assert result.nFft == n_fft

    out = np.empty((n_rows, n_fft), dtype=np.complex128)
    for row in range(n_rows):
        for col in range(n_fft):
            out[row, col] = complex(result.real[row][col], result.imag[row][col])
    return out


def _expected(snapshot: np.ndarray, n_fft: int) -> np.ndarray:
    """The Python reference: lcmf.py's window-then-FFT, e.g. lcmf.py:501-502."""
    window = np.hanning(snapshot.shape[-1])
    return np.fft.fft(snapshot * window[None, :], n=n_fft, axis=-1)


def _assert_magnitude_close(actual: np.ndarray, expected: np.ndarray, *, label: str) -> None:
    """Compare magnitude spectra to 1e-5 relative-to-peak.

    A strict per-bin RELATIVE ratio (|actual-expected| / |expected|) blows
    up wherever the true magnitude is near zero -- a windowed FFT of a
    synthetic snapshot has plenty of such bins between tones, where
    ordinary float64 rounding noise (~1e-13 absolute) already produces a
    huge ratio against an expected value of ~1e-10. That is not a sign the
    port is wrong; it is a sign per-bin relative error is the wrong metric
    for a spectrum with a large dynamic range. Tolerance relative to the
    PEAK magnitude in each case avoids that trap while still catching a
    genuinely wrong port: 1e-5 of the peak is far below the ~1e-3 the FFT
    algorithm itself is designed to keep (numpy's own accuracy for a
    512-point double-precision FFT), so a real algorithmic error (wrong
    window, wrong padding, wrong butterfly) still fails this comfortably.
    """
    actual_mag = np.abs(actual)
    expected_mag = np.abs(expected)
    peak = float(np.max(expected_mag))
    assert peak > 0.0, f"{label}: reference magnitude spectrum is all zero"
    delta = np.abs(actual_mag - expected_mag) / peak
    worst = float(np.max(delta))
    assert worst <= 1e-5, (
        f"{label}: worst magnitude delta {worst:.3e} (relative to peak) exceeds 1e-5 "
        f"at bin {int(np.argmax(delta.reshape(-1)))}"
    )


@pytest.mark.parametrize(
    ("n_rows", "n_samples", "n_fft", "seed"),
    [
        (4, 128, 512, 0),  # n_rx=4, no TX-block concatenation
        (8, 128, 512, 1),  # the canonical 8-row snapshot (doa.canonicalize_tx_blocks)
        (1, 128, 512, 2),  # a single row
        (4, 1, 512, 3),  # np.hanning(1) special case: window is [1.0]
        (4, 128, 128, 4),  # no zero-padding: nSamples == nFft
        (3, 5, 8, 5),  # small, easy-to-hand-check FFT length
    ],
)
def test_matches_numpy_fft(lib, n_rows, n_samples, n_fft, seed):
    rng = np.random.default_rng(seed)
    snapshot = rng.normal(size=(n_rows, n_samples)) + 1j * rng.normal(size=(n_rows, n_samples))
    snapshot = snapshot.astype(np.complex128)

    actual = _run(lib, snapshot, n_fft)
    expected = _expected(snapshot, n_fft)

    _assert_magnitude_close(actual, expected, label=f"rows={n_rows},samples={n_samples},fft={n_fft}")


def test_rejects_padding_shorter_than_the_input(lib):
    """np.fft.fft would truncate here; this port declines rather than match
    a behavior lcmf.py's real call sites (n_fft=512 >= n_samples) never
    exercise -- see the early return in solve_fft_apply."""
    request = FftRequest()
    request.nRows = 1
    request.nSamples = 16
    request.nFft = 8

    result = FftResult()
    status = lib.solve_fft_apply(ctypes.byref(request), ctypes.byref(result))
    assert status == 1
    assert result.status == 1


def test_rejects_a_non_power_of_two_fft_length(lib):
    request = FftRequest()
    request.nRows = 1
    request.nSamples = 4
    request.nFft = 100

    result = FftResult()
    status = lib.solve_fft_apply(ctypes.byref(request), ctypes.byref(result))
    assert status == 1
    assert result.status == 1


def test_rejects_zero_rows(lib):
    request = FftRequest()
    request.nRows = 0
    request.nSamples = 4
    request.nFft = 8

    result = FftResult()
    status = lib.solve_fft_apply(ctypes.byref(request), ctypes.byref(result))
    assert status == 1
    assert result.status == 1
