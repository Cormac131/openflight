"""Bit-exactness of solve_numpy_rng.c against numpy's actual draw sequence.

tracking.find_ball_from_power() draws its RANSAC pairs via
``np.random.default_rng(seed).choice(n, 2, replace=False)`` (tracking.py:
369-370). firmware/iwr6843/track_select.c's l3track_rng_pair (xorshift32)
does NOT reproduce this sequence -- see solve_tracking.c's file banner, item
1, for the empirical finding. solve_numpy_rng.c is a from-scratch port of
numpy's SeedSequence + PCG64 + Generator.choice(n, 2, replace=False) that
DOES reproduce it, verified here bit-for-bit.

Pinned dependency: verified against numpy 2.4.6 (this repo's .venv). numpy's
choice()/SeedSequence/PCG64 source was read directly from the numpy/numpy
GitHub repo at tag v2.4.0 (unchanged in 2.4.6 -- confirmed by the exact
match below); the RNG streams these ports depend on are a numpy
*compatibility guarantee* (PCG64's own docstring: "makes a guarantee that a
fixed seed will always produce the same random integer stream"), but
Generator.choice()'s ALGORITHM (as opposed to the underlying bit stream) is
not under that same guarantee -- a future numpy could change the Floyd's-
algorithm/hash-set/shuffle mechanics without breaking PCG64's own promise.
test_pinned_numpy_version below fails loudly, with an explicit
re-verification instruction, the moment the installed numpy stops being the
version this port was checked against -- rather than relying on
test_matches_numpy_choice_sequence's failure message (which would still
catch a stream change, just less legibly) to carry that signal.
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from tests.test_iwr6843_solve_harness import SOLVE_DIR, build_solve_lib

PINNED_NUMPY_VERSION = "2.4.6"

# n values spanning solve_tracking's real corpus range (nOrder -- the total
# detection count RANSAC draws pair indices from, read directly off
# ws->nOrder for every tracking/*.npz case: minimum 78 among found=True
# cases, maximum 249) plus deliberate edge cases: the smallest n a pair draw
# is ever made for (2), a small n well below the corpus, and an n an order
# of magnitude above SOLVE_TRACKING_MAX_DETECTIONS (2048) to prove the port
# does not quietly degrade outside the corpus's own range.
N_VALUES = [2, 3, 8, 16, 50, 78, 108, 127, 128, 186, 223, 238, 249, 2048, 5000]
SEEDS = [1, 2, 3, 42, 12345, 0, 2**33 + 5]


class SolveNumpyRng(ctypes.Structure):
    _fields_ = [
        ("stateHi", ctypes.c_uint64),
        ("stateLo", ctypes.c_uint64),
        ("incHi", ctypes.c_uint64),
        ("incLo", ctypes.c_uint64),
        ("hasUint32", ctypes.c_uint8),
        ("uinteger", ctypes.c_uint32),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    lib = build_solve_lib(tmp_path_factory, [SOLVE_DIR / "solve_numpy_rng.c"], "solve_numpy_rng")
    lib.solve_numpy_rng_seed.argtypes = [ctypes.POINTER(SolveNumpyRng), ctypes.c_uint64]
    lib.solve_numpy_rng_seed.restype = None
    lib.solve_numpy_rng_pair.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    lib.solve_numpy_rng_pair.restype = None
    return lib


def test_pinned_numpy_version():
    """This port's bit-exactness claim was verified against this exact numpy
    version (see the module docstring). A version bump here means: rerun
    test_matches_numpy_choice_sequence and test_matches_numpy_choice_full_ransac_sequence
    below, and if either fails, re-derive solve_numpy_rng.c against the new
    numpy source (numpy/random/_generator.pyx's choice(),
    numpy/random/bit_generator.pyx's SeedSequence, numpy/random/src/pcg64/
    pcg64.h) before bumping this constant."""
    assert np.__version__ == PINNED_NUMPY_VERSION, (
        f"numpy is {np.__version__}, but solve_numpy_rng.c's bit-exactness was only "
        f"verified against {PINNED_NUMPY_VERSION}. Re-verify against the new version's "
        "choice()/SeedSequence/PCG64 source before updating this pin."
    )


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("n", N_VALUES)
def test_matches_numpy_choice_sequence(lib, seed, n):
    """A single draw: solve_numpy_rng_pair(seed, n) must equal
    np.random.default_rng(seed).choice(n, 2, replace=False) exactly."""
    rng = SolveNumpyRng()
    lib.solve_numpy_rng_seed(ctypes.byref(rng), seed)
    i = ctypes.c_uint32()
    j = ctypes.c_uint32()
    lib.solve_numpy_rng_pair(ctypes.byref(rng), n, ctypes.byref(i), ctypes.byref(j))

    npy_rng = np.random.default_rng(seed)
    expected_first, expected_second = npy_rng.choice(n, 2, replace=False)

    assert (i.value, j.value) == (int(expected_first), int(expected_second)), (
        f"seed={seed} n={n}: C drew ({i.value}, {j.value}), "
        f"numpy drew ({int(expected_first)}, {int(expected_second)})"
    )


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("n", [78, 128, 238, 249])
def test_matches_numpy_choice_full_ransac_sequence(lib, seed, n):
    """The draw sequence must stay bit-exact across a FULL RANSAC run's
    worth of consecutive draws on the same Generator/rng instance (2500 --
    find_ball's default `iterations`), not just the first draw: PCG64's
    state advances differently than a fresh-instance comparison would catch
    (e.g. an off-by-one in when solve_numpy_rng_pair consumes bits would
    only show up after many draws, not the first)."""
    rng = SolveNumpyRng()
    lib.solve_numpy_rng_seed(ctypes.byref(rng), seed)
    npy_rng = np.random.default_rng(seed)

    for iteration in range(2500):
        i = ctypes.c_uint32()
        j = ctypes.c_uint32()
        lib.solve_numpy_rng_pair(ctypes.byref(rng), n, ctypes.byref(i), ctypes.byref(j))
        expected_first, expected_second = npy_rng.choice(n, 2, replace=False)
        assert (i.value, j.value) == (int(expected_first), int(expected_second)), (
            f"seed={seed} n={n} iteration={iteration}: C drew ({i.value}, {j.value}), "
            f"numpy drew ({int(expected_first)}, {int(expected_second)})"
        )
