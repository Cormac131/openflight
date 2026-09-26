"""One-off diagnostic: compare the C port's normal-equations pinv against
np.linalg.pinv on a deliberately ill-conditioned LCMF dictionary.

Not a test -- a throwaway probe for Task 6 IMPORTANT 2's numeric findings.
Run inside the openflight-pytest-env container (needs numpy + a C compiler):

    docker run --rm -v "$(pwd -W):/work" -w /work openflight-pytest-env:latest \
        python scripts/analysis/_lcmf_pinv_probe.py
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.iwr6843.multipath import leave_one_channel_out_error  # noqa: E402


def build_lib() -> ctypes.CDLL:
    out = Path(tempfile.mkdtemp()) / "liblcmf_probe.so"
    cmd = [
        "gcc",
        "-std=c99",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-shared",
        "-fPIC",
        "-o",
        str(out),
        str(ROOT / "firmware/iwr6843/solve/solve_lcmf.c"),
        "-lm",
    ]
    subprocess.run(cmd, check=True)
    lib = ctypes.CDLL(str(out))
    lib.solve_lcmf_debug_leave_one_channel_out_error.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.solve_lcmf_debug_leave_one_channel_out_error.restype = ctypes.c_uint32
    return lib


def c_error(lib, a: np.ndarray, y: np.ndarray) -> tuple[float, bool]:
    k = a.shape[1]
    a_re = np.ascontiguousarray(np.real(a), dtype=np.float64)
    a_im = np.ascontiguousarray(np.imag(a), dtype=np.float64)
    y_re = (ctypes.c_double * 8)(*np.real(y))
    y_im = (ctypes.c_double * 8)(*np.imag(y))
    err = ctypes.c_double(0.0)
    singular = ctypes.c_int(0)
    lib.solve_lcmf_debug_leave_one_channel_out_error(
        k,
        a_re.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        a_im.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        y_re,
        y_im,
        ctypes.byref(err),
        ctypes.byref(singular),
    )
    return err.value, bool(singular.value)


def python_error(a: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    cond = np.linalg.cond(a)
    error = leave_one_channel_out_error(y[None, :], a[None, :, :])[0]
    return float(error), float(cond)


def main() -> None:
    lib = build_lib()
    rng = np.random.default_rng(0)
    col0 = rng.normal(size=8) + 1j * rng.normal(size=8)
    col0 /= np.abs(col0).max()
    col1_direction = rng.normal(size=8) + 1j * rng.normal(size=8)
    col1_direction /= np.abs(col1_direction).max()
    coeffs = np.array([1.0 + 0.3j, 0.7 - 0.2j])
    noise = (rng.normal(size=8) + 1j * rng.normal(size=8)) * 0.01

    print(
        f"{'eps (col separation)':>22} {'cond(A)':>14} {'py_error':>14} "
        f"{'c_error':>14} {'c_singular':>10} {'rel_diff':>10}"
    )
    for eps in [
        1.0,
        0.3,
        0.1,
        3e-2,
        1e-2,
        3e-3,
        1e-3,
        3e-4,
        1e-4,
        1e-5,
        1e-6,
        1e-8,
        1e-10,
        1e-12,
        0.0,
    ]:
        # col1 = col0 + eps * (orthogonal-ish direction): as eps -> 0 the two
        # dictionary columns become collinear -- the two8 model's own
        # DD/GG failure mode when direct and image paths nearly coincide.
        col1 = col0 + eps * col1_direction
        a = np.stack([col0, col1], axis=-1)
        y = a @ coeffs + noise

        py_err, cond = python_error(a, y)
        c_err, c_sing = c_error(lib, a, y)
        rel = abs(c_err - py_err) / max(abs(py_err), 1e-12)
        print(
            f"{eps:>22.3e} {cond:>14.4e} {py_err:>14.6e} {c_err:>14.6e} {str(c_sing):>10} {rel:>10.4f}"
        )


if __name__ == "__main__":
    main()
