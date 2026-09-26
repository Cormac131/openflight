"""Shared scaffolding for the on-chip solve equivalence tests.

Each ported stage is plain C99 so the host compiler can build it and ctypes
can drive it against vectors recorded from the Python reference. The Python
implementation is the reference: when the two disagree, the C is wrong or the
tolerance is wrong. Never edit the reference to make the C agree.

The golden vectors under ``tests/golden/iwr6843/`` are synthetic: every case
is built by ``scripts/dev/generate_golden_vectors.py`` from
``synth_shot()``/``synth_club_dump()`` synthetic captures run through the
real Python reference implementations. This proves C-vs-Python numerical
equivalence, which is the harness's job. It proves nothing about real-world
representativeness (SNR, multipath, unusual clubs, TrackMan-corpus accuracy)
-- see ``tests/golden/iwr6843/README.md`` for what remains outstanding and
hardware-gated.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).parents[1]
SOLVE_DIR = ROOT / "firmware" / "iwr6843" / "solve"
GOLDEN_DIR = ROOT / "tests" / "golden" / "iwr6843"


def build_solve_lib(tmp_path_factory, sources: list[Path], name: str) -> ctypes.CDLL:
    """Compile solve sources into a shared library for ctypes."""
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip(f"no host C compiler to build {name}")
    missing = [str(s) for s in sources if not s.exists()]
    if missing:
        pytest.skip(f"solve sources not ported yet: {', '.join(missing)}")
    suffix = ".dll" if sys.platform == "win32" else ".so"
    out = tmp_path_factory.mktemp(name) / f"lib{name}{suffix}"
    cmd = [compiler, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared"]
    if sys.platform != "win32":
        cmd.append("-fPIC")
    cmd += ["-o", str(out), *[str(s) for s in sources], "-lm"]
    subprocess.run(cmd, check=True)
    return ctypes.CDLL(str(out))


def load_golden(stage: str, case: str) -> dict[str, np.ndarray]:
    """Load one recorded input/output vector for a stage."""
    path = GOLDEN_DIR / stage / f"{case}.npz"
    if not path.exists():
        pytest.skip(f"no golden vector at {path}; run scripts/dev/generate_golden_vectors.py")
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def golden_cases(stage: str) -> list[str]:
    """Every recorded case for a stage, or an empty list if none exist."""
    stage_dir = GOLDEN_DIR / stage
    if not stage_dir.exists():
        return []
    return sorted(p.stem for p in stage_dir.glob("*.npz"))


def assert_close(actual, expected, *, tol: float, label: str) -> None:
    """Compare with a stated tolerance, reporting the worst element.

    NaN, +inf and -inf are compared as three DISTINCT categories (not lumped
    together under "non-finite"): a port that returns +inf where the
    reference returned NaN, or +inf where the reference returned -inf, is
    wrong and must fail here even though both sides are "non-finite".

    The worst-element index is computed over the FULL array shape (not a
    flattened, non-finite-compacted view), via ``np.unravel_index`` into
    ``expected_arr.shape`` -- so for a multi-dimensional array (e.g. the 4-D
    MTI cube) or any array with a NaN/inf hole, the reported index is a real
    coordinate into the actual array, not an offset into a filtered 1-D copy.
    """
    actual_arr = np.atleast_1d(np.asarray(actual, dtype=np.float64))
    expected_arr = np.atleast_1d(np.asarray(expected, dtype=np.float64))
    assert actual_arr.shape == expected_arr.shape, (
        f"{label}: shape {actual_arr.shape} != reference {expected_arr.shape}"
    )
    assert np.array_equal(np.isnan(actual_arr), np.isnan(expected_arr)), (
        f"{label}: NaN pattern differs from the reference"
    )
    assert np.array_equal(np.isposinf(actual_arr), np.isposinf(expected_arr)), (
        f"{label}: +inf pattern differs from the reference"
    )
    assert np.array_equal(np.isneginf(actual_arr), np.isneginf(expected_arr)), (
        f"{label}: -inf pattern differs from the reference"
    )
    finite = np.isfinite(expected_arr)
    if not finite.any():
        return
    # Non-finite positions get -inf delta so they never win the argmax; the
    # patterns above already proved actual/expected agree there.
    delta = np.where(finite, np.abs(actual_arr - expected_arr), -np.inf)
    worst = tuple(int(i) for i in np.unravel_index(int(np.argmax(delta)), expected_arr.shape))
    worst_delta = float(delta[worst])
    assert worst_delta <= tol, (
        f"{label}: worst delta {worst_delta:.6g} exceeds tol {tol:.6g} "
        f"at index {worst} (got {actual_arr[worst]:.6g}, "
        f"reference {expected_arr[worst]:.6g})"
    )


def assert_text_equal(actual, expected, *, label: str) -> None:
    """Compare string scalars/arrays exactly, reporting the first mismatch.

    ``assert_close`` is numeric-only (it casts through ``float64``) and
    raises ``ValueError`` on the string dtypes the corpus uses for
    ``status``/``reason``/``tracker_quality``/``horizontal_status``/
    ``candidate_path_status``/``track_selection_mode``/``component_names``/
    ``channels_used`` -- leaving no assertion path for the fields that ARE
    the accept/reject decision. This is that path.
    """
    actual_arr = np.atleast_1d(np.asarray(actual))
    expected_arr = np.atleast_1d(np.asarray(expected))
    assert actual_arr.shape == expected_arr.shape, (
        f"{label}: shape {actual_arr.shape} != reference {expected_arr.shape}"
    )
    mismatches = np.flatnonzero(actual_arr != expected_arr)
    if mismatches.size == 0:
        return
    idx = tuple(int(i) for i in np.unravel_index(int(mismatches[0]), expected_arr.shape))
    assert False, (
        f"{label}: text differs at index {idx} "
        f"(got {actual_arr[idx]!r}, reference {expected_arr[idx]!r})"
    )


def test_harness_reports_the_worst_element():
    with pytest.raises(AssertionError, match=r"index \(2,\)"):
        assert_close([1.0, 1.0, 5.0], [1.0, 1.0, 1.0], tol=0.5, label="probe")


def test_harness_reports_a_true_multidimensional_index_past_a_nan_hole():
    """A NaN hole must not shift the reported index of a later element.

    The naive flatten-and-compact ``delta[finite]`` approach reindexes
    around the hole, so the worst element (the (1, 2) corner, off by 5.0)
    would be misreported as a lower flat index. ``np.unravel_index`` into
    the real 2-D shape must report the true (1, 2) coordinate instead.
    """
    expected = [[1.0, float("nan"), 1.0], [1.0, 1.0, 1.0]]
    actual = [[1.0, float("nan"), 1.0], [1.0, 1.0, 6.0]]
    with pytest.raises(AssertionError, match=r"index \(1, 2\)"):
        assert_close(actual, expected, tol=0.5, label="probe")


def test_harness_catches_a_nan_mismatch():
    with pytest.raises(AssertionError, match="NaN pattern"):
        assert_close([1.0, float("nan")], [1.0, 2.0], tol=1.0, label="probe")


def test_harness_distinguishes_positive_infinity_from_nan():
    """A port returning +inf where the reference returned NaN must fail.

    Caught by the NaN-pattern check here (actual isn't NaN where the
    reference is) -- the point under test is that treating NaN and +inf as
    interchangeable "non-finite" no longer passes, not which specific
    category check reports it first.
    """
    with pytest.raises(AssertionError, match=r"(NaN|\+inf) pattern"):
        assert_close([1.0, float("inf")], [1.0, float("nan")], tol=1.0, label="probe")


def test_harness_distinguishes_positive_from_negative_infinity():
    """A port returning +inf where the reference returned -inf must fail."""
    with pytest.raises(AssertionError, match=r"(\+inf|-inf) pattern"):
        assert_close([1.0, float("inf")], [1.0, float("-inf")], tol=1.0, label="probe")


def test_harness_catches_a_shape_mismatch():
    with pytest.raises(AssertionError, match="shape"):
        assert_close([1.0, 2.0, 3.0], [1.0, 2.0], tol=1.0, label="probe")


def test_text_helper_catches_a_scalar_mismatch():
    with pytest.raises(AssertionError, match="text differs"):
        assert_text_equal("rejected_no_ball", "accepted", label="probe")


def test_text_helper_catches_an_array_mismatch_and_reports_its_index():
    with pytest.raises(AssertionError, match=r"index \(2,\)"):
        assert_text_equal(
            ["a", "b", "WRONG"],
            ["a", "b", "c"],
            label="probe",
        )


def test_text_helper_accepts_matching_arrays():
    assert_text_equal(["a", "b", "c"], ["a", "b", "c"], label="probe")  # no raise


def test_golden_cases_returns_empty_for_an_unknown_stage():
    assert golden_cases("no_such_stage_at_all") == []
