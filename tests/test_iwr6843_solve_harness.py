"""Shared scaffolding for the on-chip solve equivalence tests.

Each ported stage is plain C99 so the host compiler can build it and ctypes
can drive it against vectors recorded from the Python reference. The Python
implementation is the reference: when the two disagree, the C is wrong or the
tolerance is wrong. Never edit the reference to make the C agree.

The golden vectors under ``tests/golden/iwr6843/`` are synthetic: every case
is built by ``scripts/dev/generate_golden_vectors.py`` from
``synth_shot()``/``_synth_club_dump()`` synthetic captures run through the
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
        pytest.skip(f"no golden vector at {path}; run scripts/dev/extract_golden_vectors.py")
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


def golden_cases(stage: str) -> list[str]:
    """Every recorded case for a stage, or an empty list if none exist."""
    stage_dir = GOLDEN_DIR / stage
    if not stage_dir.exists():
        return []
    return sorted(p.stem for p in stage_dir.glob("*.npz"))


def assert_close(actual, expected, *, tol: float, label: str) -> None:
    """Compare with a stated tolerance, reporting the worst element."""
    actual_arr = np.atleast_1d(np.asarray(actual, dtype=np.float64))
    expected_arr = np.atleast_1d(np.asarray(expected, dtype=np.float64))
    assert actual_arr.shape == expected_arr.shape, (
        f"{label}: shape {actual_arr.shape} != reference {expected_arr.shape}"
    )
    finite = np.isfinite(expected_arr)
    assert np.array_equal(np.isfinite(actual_arr), finite), (
        f"{label}: NaN/inf pattern differs from the reference"
    )
    if not finite.any():
        return
    delta = np.abs(actual_arr[finite] - expected_arr[finite])
    worst = int(np.argmax(delta))
    assert delta[worst] <= tol, (
        f"{label}: worst delta {delta[worst]:.6g} exceeds tol {tol:.6g} "
        f"at index {worst} (got {actual_arr[finite][worst]:.6g}, "
        f"reference {expected_arr[finite][worst]:.6g})"
    )


def test_harness_reports_the_worst_element():
    with pytest.raises(AssertionError, match="index 2"):
        assert_close([1.0, 1.0, 5.0], [1.0, 1.0, 1.0], tol=0.5, label="probe")


def test_harness_catches_a_nan_mismatch():
    with pytest.raises(AssertionError, match="NaN/inf pattern"):
        assert_close([1.0, float("nan")], [1.0, 2.0], tol=1.0, label="probe")
