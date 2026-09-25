# IWR6843 On-Chip Solve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the launch-angle and club-path solve from the Raspberry Pi onto the IWR6843's C674x DSP core, so a normal shot transfers results instead of raw IQ cells.

**Architecture:** Add a DSS image alongside the existing MSS image. The DSS reads the frozen capture arena in place from L3 and caches it in the C674x L2 — it reserves no additional L3, so it does not contend with the capture arena. Solve intermediates live in L2 SRAM. MSS and DSS exchange a request and a result record over the SDK mailbox driver. The Python implementation stays the reference, the porting oracle, and the replay engine.

**Tech Stack:** C99 (TI C6000 CGT for the C674x, TI ARM CGT for the R4F, host `cc`/`gcc`/`clang` for tests), TI mmWave SDK, SYS/BIOS, mailbox driver, DSPLIB/MATHLIB C674x, Python 3 + numpy + pytest + ctypes, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-25-iwr6843-onchip-solve-design.md`

## Global Constraints

- All Python commands run through `uv run`. Never bare `python`, `pip`, `pytest`.
- Lint gate: `uv run pylint src/openflight/ --fail-under=9`.
- Firmware builds only via the container: `make -C firmware docker-build`.
- **The Python reference is never edited to make the C agree.** If they diverge, either the port is wrong or the tolerance is wrong. Changing `lcmf.py`, `club.py`, `tracking.py` or `late_window.py` to accommodate the port is a plan violation.
- This is a port, not a redesign. No algorithm changes.
- The DSS reserves **no resident L3 buffer**. It reads the capture arena in place and caches it in L2.
- 2 ms capture cadence is unaffected: `hwa_missed` and `iq8_overrun` stay at or below their bounds with the DSS running.
- Host C test compilation uses `-std=c99 -O2 -Wall -Wextra -Werror`.
- Raw cell transfer remains available behind `save_dumps` (`monitor.py:233`); it is not removed.

## Review Focus

Input classes the spec implies but no happy path exercises. Each is pinned to the task that owns the code.

1. **A shot where the tracker finds no ball** must produce a clean no-confidence result and fall back, not a garbage angle. Pinned in Task 9.
2. **A capture frozen with fewer frames than the plan expects** (early `sensorStop`, short movie) must not read past the valid frames. Pinned in Task 8.
3. **Denormal and NaN intermediates** — the C674x and x86 handle these differently; a NaN must propagate to no-confidence, not to a finite wrong angle. Pinned in Task 7.
4. **Mailbox timeout or a DSS that never answers** must fall back to the Python path within the shot deadline, not hang the shot pipeline. Pinned in Task 6.
5. **A `save_dumps` session** must still transfer raw cells and replay identically through `replay.py` after the cutover. Pinned in Task 10.

---

## File Structure

| File | Responsibility |
|---|---|
| `firmware/Makefile` (modify) | Re-enable the C6000 toolchain and DSP libraries |
| `firmware/Dockerfile` (modify) | Rebuild with the larger SDK component set |
| `firmware/iwr6843/dss/makefile` (create) | DSS build |
| `firmware/iwr6843/dss/dss.cfg` (create) | DSS RTSC/SYS-BIOS config, L2 SRAM/cache split |
| `firmware/iwr6843/dss/dss_linker.cmd` (create) | DSS sections, L2 SRAM placement |
| `firmware/iwr6843/dss/dss_main.c` (create) | DSS boot, mailbox listener, solve dispatch |
| `firmware/iwr6843/solve/solve_ipc.h` (create) | Request/result records shared by MSS and DSS |
| `firmware/iwr6843/solve/solve_tracking.{c,h}` (create) | Port of `tracking.py` |
| `firmware/iwr6843/solve/solve_lcmf.{c,h}` (create) | Port of `lcmf.py` |
| `firmware/iwr6843/solve/solve_late_window.{c,h}` (create) | Port of `late_window.py` |
| `firmware/iwr6843/solve/solve_club.{c,h}` (create) | Port of `club.py` |
| `firmware/iwr6843/l3_dump.c` (modify) | Mailbox request on freeze, result emission, `l3solve` CLI |
| `tests/golden/iwr6843/` (create) | Per-stage golden vectors |
| `tests/test_iwr6843_solve_harness.py` (create) | Shared ctypes build fixture and comparison helpers |
| `tests/test_iwr6843_solve_<stage>.py` (create, one per stage) | Per-stage equivalence tests |
| `src/openflight/iwr6843/runtime.py` (modify) | Consume on-chip results, fall back on no-confidence |
| `scripts/hardware-test/iwr6843_solve_shadow.py` (create) | Shadow-mode comparison |

---

## Task 1: Re-Enable the C6000 Toolchain

`firmware/Makefile:108` strips `TI_CGT_C6000`, `DSPLIB_C674x` and `MATHLIB_C674x` from the SDK install.

**Files:**
- Modify: `firmware/Makefile:108`
- Modify: `firmware/Dockerfile`
- Modify: `docs/development/firmware.md:261`

- [ ] **Step 1: Write the failing test**

Create `tests/test_iwr6843_dss_build.py`:

```python
"""The DSS solve needs the C6000 toolchain the SDK install used to strip."""

from __future__ import annotations

from pathlib import Path

FIRMWARE_MAKEFILE = Path(__file__).parents[1] / "firmware" / "Makefile"


def test_sdk_install_keeps_the_c6000_toolchain():
    text = FIRMWARE_MAKEFILE.read_text(encoding="utf-8")
    disabled = [
        line for line in text.splitlines() if "--disable-components" in line
    ]
    assert disabled, "expected an SDK install line with --disable-components"
    for line in disabled:
        assert "TI_CGT_C6000" not in line
        assert "DSPLIB_C674x" not in line
        assert "MATHLIB_C674x" not in line
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_iwr6843_dss_build.py -v
```

Expected: FAIL, all three are currently disabled.

- [ ] **Step 3: Edit the install line**

At `firmware/Makefile:108`, remove `TI_CGT_C6000`, `DSPLIB_C674x` and `MATHLIB_C674x` from `--disable-components`. Leave `TI_CGT_ARM`, `DSPLIB_C64Px`, `XDCtools` and `SYS_BIOS` exactly as they are — they are disabled for other reasons.

- [ ] **Step 4: Rebuild the container image**

```bash
make -C firmware docker-image
```

Expected: succeeds. This is slower and larger than before; record the image size and build time in the commit message, because they are the real cost of this decision.

- [ ] **Step 5: Confirm the compiler is present**

```bash
docker run --rm openflight-iwr-sdk sh -c 'ls $TI_ROOT/ti-cgt-c6000*/bin/cl6x'
```

Expected: the binary exists. If the variable name differs, read the Dockerfile for the real path.

- [ ] **Step 6: Confirm the MSS build is unaffected**

```bash
make -C firmware docker-build && uv run pytest tests/ -v
```

Expected: the existing firmware builds byte-identically or close, and all tests pass.

- [ ] **Step 7: Correct the documentation**

`docs/development/firmware.md:261` states the application "does not require the C674x DSP compiler". Update it to say the DSS solve does require it, and note the larger image.

- [ ] **Step 8: Commit**

```bash
git add firmware/Makefile firmware/Dockerfile docs/development/firmware.md tests/test_iwr6843_dss_build.py
git commit -m "build(iwr6843): re-enable the C6000 toolchain for the DSS solve"
```

---

## Task 2: Boot a Minimal DSS Image

Prove the three-image meta-image works before porting anything.

**Files:**
- Create: `firmware/iwr6843/dss/dss.cfg`, `dss_linker.cmd`, `dss_main.c`, `makefile`
- Modify: `firmware/iwr6843/makefile:97-101` (meta-image generation)

**Interfaces:**
- Produces: `l3_dump_dss.xe674`, and a `l3_dump.bin` meta-image containing MSS + DSS + RADARSS.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_iwr6843_dss_build.py`:

```python
FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"


def test_meta_image_includes_a_dss_image():
    makefile = (FIRMWARE_DIR / "makefile").read_text(encoding="utf-8")
    assert "$(DSS_OUT)" in makefile
    assert (FIRMWARE_DIR / "dss" / "dss_main.c").exists()
    assert (FIRMWARE_DIR / "dss" / "dss_linker.cmd").exists()
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_dss_build.py::test_meta_image_includes_a_dss_image -v
```

Expected: FAIL.

- [ ] **Step 3: Write the minimal DSS main**

Create `firmware/iwr6843/dss/dss_main.c`:

```c
/* DSS (C674x) image for the on-chip solve.
 *
 * Milestone 1: boot, register with the mailbox, and answer a ping. The solve
 * stages land on top of this in later tasks. The DSS reads the capture arena
 * in place from L3 and caches it in L2; it owns no resident L3 buffer.
 */
#include <stdint.h>
#include <xdc/std.h>
#include <ti/sysbios/BIOS.h>
#include <ti/sysbios/knl/Task.h>
#include <ti/drivers/soc/soc.h>
#include <ti/drivers/esm/esm.h>
#include <ti/drivers/mailbox/mailbox.h>

#include "../solve/solve_ipc.h"

static void dss_solveTask(UArg arg0, UArg arg1)
{
    (void)arg0;
    (void)arg1;
    while (1) {
        /* Milestone 1: no work yet. Task 6 replaces this with the mailbox
         * listener. Yield so BIOS is demonstrably scheduling us. */
        Task_sleep(100U);
    }
}

int main(void)
{
    Task_Params taskParams;
    SOC_Handle socHandle;
    int32_t errCode;
    SOC_Cfg socCfg;

    memset((void *)&socCfg, 0, sizeof(SOC_Cfg));
    socCfg.clockCfg = SOC_SysClock_INIT;
    socHandle = SOC_init(&socCfg, &errCode);
    if (socHandle == NULL) {
        return -1;
    }

    Task_Params_init(&taskParams);
    taskParams.priority = 2;
    taskParams.stackSize = 4 * 1024;
    Task_create(dss_solveTask, &taskParams, NULL);

    BIOS_start();
    return 0;
}
```

- [ ] **Step 4: Write the IPC header**

Create `firmware/iwr6843/solve/solve_ipc.h`:

```c
/* MSS <-> DSS solve protocol.
 *
 * The MSS freezes a capture and asks the DSS to solve it. The DSS reads the
 * capture arena in place from L3 and replies with a result record, or with
 * L3_SOLVE_NO_CONFIDENCE so the MSS can fall back to transferring cells.
 */
#ifndef L3_SOLVE_IPC_H
#define L3_SOLVE_IPC_H

#include <stdint.h>

#define L3_SOLVE_MAGIC        0x4C534F31U   /* "LSO1" */
#define L3_SOLVE_OK           0U
#define L3_SOLVE_NO_CONFIDENCE 1U
#define L3_SOLVE_ERROR        2U

typedef struct {
    uint32_t magic;
    uint32_t arenaAddr;      /* L3 address of frame 0 */
    uint32_t totalFrames;
    uint32_t loops;
    uint32_t nTx;
    uint32_t nRx;
    uint32_t framePeriodUs;
    uint32_t bytesPerComplex;
    uint32_t binStartsAddr;  /* per-frame tables, in MSS memory */
    uint32_t binCountsAddr;
    uint32_t frameOffsetsAddr;
    float    ballSpeedMph;
    float    rangeResM;
    float    tiltRad;
    float    teeRangeM;
} L3SolveRequest;

typedef struct {
    uint32_t magic;
    uint32_t status;         /* L3_SOLVE_OK / NO_CONFIDENCE / ERROR */
    float    launchAngleDeg;
    float    rawAngleDeg;
    float    clubPathDeg;
    float    attackAngleDeg;
    uint32_t nSnapshots;
    uint32_t nFrames;
    float    componentStdDeg;
    uint32_t trackerQuality;
} L3SolveResult;

#endif /* L3_SOLVE_IPC_H */
```

The float fields mirror `LCMFResult` (`lcmf.py:73-85`). Read that dataclass and add any field the host needs that is missing here before proceeding.

- [ ] **Step 5: Write the DSS linker command file**

Create `firmware/iwr6843/dss/dss_linker.cmd`. Place `.text`, `.const`, `.bss`, `.data` and `.stack` in L2 SRAM. Add a `.solveScratch` section, also in L2 SRAM, for the solve intermediates. Do **not** create any L3 section — the DSS must not reserve L3.

- [ ] **Step 6: Set the L2 SRAM/cache split**

In `firmware/iwr6843/dss/dss.cfg`, configure the C674x cache so that part of L2 is SRAM for code and intermediates and the remainder is cache over L3, which is how the DSS reads the capture arena without copying it. Start with a 32 KB L2 cache and the rest SRAM, and record the chosen split in a comment. Task 4 measures whether it is right.

- [ ] **Step 7: Build the DSS and the three-image meta-image**

Write `firmware/iwr6843/dss/makefile` following the MSS makefile's structure but with `R4F_*` replaced by the C674x equivalents from `mmwave_sdk.mak`. In `firmware/iwr6843/makefile`, change the `bin` target to pass the DSS image to `$(GENERATE_METAIMAGE)`, which the comment at `makefile:97-99` notes is the documented argument order.

- [ ] **Step 8: Build and flash**

```bash
make -C firmware docker-build
```

Flash the image and confirm the MSS still boots, the CLI still responds, and a capture still runs. The DSS does nothing yet; the point is that its presence breaks nothing.

- [ ] **Step 9: Run the cadence soak with the DSS resident**

```bash
uv run python scripts/hardware-test/iwr6843_cadence_soak.py \
    --config config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg --frames 50000
```

Expected: PASS. **Gate:** if merely having the DSS booted regresses the cadence, stop and investigate before porting anything.

- [ ] **Step 10: Commit**

```bash
git add firmware/iwr6843/dss firmware/iwr6843/solve/solve_ipc.h firmware/iwr6843/makefile tests/test_iwr6843_dss_build.py
git commit -m "feat(iwr6843): boot a minimal DSS image in the meta-image"
```

---

## Task 3: Build the Golden-Vector Harness

Every port task depends on this. Build it once, properly.

**Files:**
- Create: `tests/test_iwr6843_solve_harness.py`
- Create: `scripts/dev/extract_golden_vectors.py`
- Create: `tests/golden/iwr6843/README.md`

**Interfaces:**
- Produces:
  - `build_solve_lib(tmp_path_factory, sources: list[Path], name: str) -> ctypes.CDLL`
  - `assert_close(actual, expected, *, tol: float, label: str)` — reports the worst element and its index on failure, not just "arrays differ".
  - Golden vectors as `.npz` files under `tests/golden/iwr6843/<stage>/<case>.npz`, each holding named input and output arrays.

- [ ] **Step 1: Write the harness**

Create `tests/test_iwr6843_solve_harness.py`:

```python
"""Shared scaffolding for the on-chip solve equivalence tests.

Each ported stage is plain C99 so the host compiler can build it and ctypes
can drive it against vectors recorded from the Python reference. The Python
implementation is the reference: when the two disagree, the C is wrong or the
tolerance is wrong. Never edit the reference to make the C agree.
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
```

- [ ] **Step 2: Run the harness self-tests**

```bash
uv run pytest tests/test_iwr6843_solve_harness.py -v
```

Expected: PASS. The harness is tested before anything depends on it.

- [ ] **Step 3: Write the extractor**

Create `scripts/dev/extract_golden_vectors.py`. It replays saved dumps through the Python reference and records each stage's inputs and outputs as `.npz`. Structure:

```python
"""Record per-stage golden vectors from saved IWR6843 dumps.

Usage:
    uv run python scripts/dev/extract_golden_vectors.py \\
        --dumps ~/openflight_sessions/dumps --out tests/golden/iwr6843
"""
```

For each stage, wrap the reference entry point, capture its arguments and return value, and save them. Use at least 12 dumps spanning the conditions the spec cares about: fast and slow ball speeds, both clubs in the TrackMan corpus, and at least two where the tracker fails to find a ball.

- [ ] **Step 4: Extract and commit the vectors**

```bash
uv run python scripts/dev/extract_golden_vectors.py --dumps <path> --out tests/golden/iwr6843
```

Write `tests/golden/iwr6843/README.md` naming the source session for each case and the conditions it covers, so a future reader knows what the corpus does and does not represent.

- [ ] **Step 5: Commit**

```bash
git add tests/test_iwr6843_solve_harness.py scripts/dev/extract_golden_vectors.py tests/golden/iwr6843
git commit -m "test(iwr6843): add the solve golden-vector harness"
```

---

## Task 4: Compute Probe (Phase 0 Gate)

Port one representative stage and measure it. This decides whether the full port is viable.

**Files:**
- Create: `firmware/iwr6843/solve/solve_fft.c`, `solve_fft.h`
- Modify: `docs/superpowers/specs/2026-09-25-iwr6843-onchip-solve-design.md`

- [ ] **Step 1: Port the LCMF FFT path only**

`lcmf.py:502` and `:547` apply a window and take an FFT over the snapshot axis. Port just that: window application plus complex FFT, using DSPLIB's C674x FFT rather than a hand-rolled one.

- [ ] **Step 2: Write the equivalence test**

Create `tests/test_iwr6843_solve_fft.py` using `build_solve_lib` and `assert_close` from the harness, comparing against `np.fft.fft` on the same windowed input. Tolerance: 1e-5 relative on the magnitude spectrum. State the tolerance and why in a comment.

- [ ] **Step 3: Run it**

```bash
uv run pytest tests/test_iwr6843_solve_fft.py -v
```

Expected: PASS.

- [ ] **Step 4: Measure it on-chip**

Add a temporary `l3fft` CLI command that runs the ported FFT over one frozen capture's worth of snapshots on the DSS and reports elapsed microseconds. Flash, run, record.

- [ ] **Step 5: Record the finding and gate**

Write the measured time into the spec's Phase 0 section, alongside the share of the 1 s budget it represents.

**Gate:** if this stage alone consumes a large fraction of the budget, stop and re-scope to the hybrid option — on-chip numerics with host decision logic — before porting `tracking`, `lcmf`, `late_window` or `club`. Raise it rather than deciding alone.

- [ ] **Step 6: Confirm the L2 split**

Report DSS L2 SRAM usage from the DSS map file and confirm the capture arena is being read through cache with no resident L3 buffer. If the split chosen in Task 2 Step 6 is wrong, change it here and record the new value.

- [ ] **Step 7: Commit**

```bash
git add firmware/iwr6843/solve tests/test_iwr6843_solve_fft.py docs/superpowers/specs/2026-09-25-iwr6843-onchip-solve-design.md
git commit -m "feat(iwr6843): port the LCMF FFT stage and measure it on the DSS"
```

---

## Tasks 5-8: Port Each Stage

**These four tasks share one recipe.** Do them in this order, because each consumes the previous stage's output:

| Task | Stage | Reference | Entry point | Tolerance |
|---|---|---|---|---|
| 5 | tracking | `src/openflight/iwr6843/tracking.py` (425 lines) | detections and track fit | bin positions exact; fitted slope/intercept 1e-4 relative |
| 6 | lcmf | `src/openflight/iwr6843/lcmf.py` (962 lines) | `estimate_lcmf_v1` (`lcmf.py:766`) | angle 0.01 deg absolute |
| 7 | late_window | `src/openflight/iwr6843/late_window.py` (366 lines) | window selection | selected frame indices exact |
| 8 | club | `src/openflight/iwr6843/club.py` (946 lines) | club path and gating | path angle 0.01 deg absolute |

Tolerances are starting points. If a stage cannot meet its tolerance, **do not loosen it silently** — record why the divergence is legitimate (different FP ordering, a different library FFT) and raise the new tolerance with its reason in the spec.

### The Recipe (apply to each of Tasks 5, 6, 7, 8)

- [ ] **Step 1: Read the reference module end to end**

Do not start porting from a skim. Note every branch, every early return, and every implicit numpy broadcast — broadcasts are where ports silently diverge.

- [ ] **Step 2: Write the failing equivalence test**

Create `tests/test_iwr6843_solve_<stage>.py`:

```python
"""<Stage> equivalence: the ported C must match the Python reference."""

from __future__ import annotations

import pytest

from tests.test_iwr6843_solve_harness import (
    SOLVE_DIR,
    assert_close,
    build_solve_lib,
    golden_cases,
    load_golden,
)

STAGE = "<stage>"
TOLERANCE = 0.01  # see the plan's tolerance table; state the reason here


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    return build_solve_lib(
        tmp_path_factory,
        [SOLVE_DIR / f"solve_{STAGE}.c"],
        f"solve_{STAGE}",
    )


@pytest.mark.parametrize("case", golden_cases(STAGE))
def test_matches_the_python_reference(lib, case):
    vectors = load_golden(STAGE, case)
    # Drive the C entry point with vectors["input_*"], then:
    assert_close(actual, vectors["expected"], tol=TOLERANCE, label=f"{STAGE}/{case}")
```

`golden_cases` returns an empty list until Task 3's vectors exist, so the parametrize collapses to zero tests rather than erroring — which is why Task 3 comes first.

- [ ] **Step 3: Run it to verify it fails**

```bash
uv run pytest tests/test_iwr6843_solve_<stage>.py -v
```

Expected: skip (sources not ported), which counts as not-yet-passing.

- [ ] **Step 4: Port the module to C99**

Create `firmware/iwr6843/solve/solve_<stage>.{c,h}`. Rules:

- Pure C99. No TI headers in the `.c`, so the host compiler can build it.
- Structs in, structs out. No globals.
- Fixed-size buffers sized from the limits in `solve_ipc.h`. No `malloc`.
- Every early return in the Python becomes an explicit status code, not a sentinel float.
- Keep the Python function and variable names where they survive translation, so a reader can diff the two by eye.

- [ ] **Step 5: Run the test until it passes**

```bash
uv run pytest tests/test_iwr6843_solve_<stage>.py -v
```

When a case fails, `assert_close` names the worst element and its index. Fix the C. Do not adjust the reference.

- [ ] **Step 6: Add the stage's Review Focus test**

- Task 5 (tracking): a capture with fewer frames than the plan expects must not read past the valid frames. Add a golden case with a short movie.
- Task 6 (lcmf): a mailbox timeout must fall back within the shot deadline. Add a host-side test that a non-responding DSS produces the Python result.
- Task 7 (late_window): a NaN intermediate must propagate to no-confidence, not a finite wrong angle. Add a case with a NaN input and assert the status, not the value.
- Task 8 (club): a shot where the tracker found no ball must produce no-confidence cleanly.

- [ ] **Step 7: Wire the stage into the DSS build**

Add the source to the DSS makefile and call it from `dss_main.c`'s solve dispatch.

- [ ] **Step 8: Build and run everything**

```bash
make -C firmware docker-build && uv run pytest tests/ -v
```

- [ ] **Step 9: Commit**

```bash
git add firmware/iwr6843/solve tests/test_iwr6843_solve_<stage>.py
git commit -m "feat(iwr6843): port <stage> to the DSS solve"
```

---

## Task 9: Mailbox Protocol and the Result-Only Shot Path

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c`, `firmware/iwr6843/dss/dss_main.c`
- Modify: `src/openflight/iwr6843/runtime.py`, `src/openflight/iwr6843/monitor.py`
- Modify: `tests/test_iwr6843_monitor.py`

**Interfaces:**
- Consumes: `L3SolveRequest`, `L3SolveResult` from `solve_ipc.h`.
- Produces: an `l3solve` CLI command that freezes, asks the DSS, and writes an `ILR1` result packet; host-side parsing of `ILR1`.

- [ ] **Step 1: Write the failing host test**

Add to `tests/test_iwr6843_monitor.py` a test that a parsed `ILR1` result packet produces a shot with the expected angle, and that a `L3_SOLVE_NO_CONFIDENCE` status makes the runtime fall back to the cell path.

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_monitor.py -k solve -v
```

- [ ] **Step 3: Implement the MSS side**

On freeze, populate an `L3SolveRequest` and post it to the DSS. Wait with a bounded timeout — **shorter than the cell-transfer path it replaces**, so the fallback is never slower than today. On `L3_SOLVE_OK`, write an `ILR1` packet. On anything else, including timeout, fall through to the existing `l3track` path unchanged.

- [ ] **Step 4: Implement the DSS side**

Replace the `Task_sleep` loop in `dss_main.c` with a mailbox listener that runs the four stages in order and replies.

- [ ] **Step 5: Implement the host side**

Add `ILR1` parsing beside the existing `ILT1` and `ILS1` handling in `monitor.py`, and make `runtime.py` prefer the on-chip result, falling back on no-confidence — mirroring the existing `l3track` to `l3sparse` to `l3dump` tiering at `monitor.py:485-516`.

- [ ] **Step 6: Run the tests**

```bash
uv run pytest tests/ -v && uv run pylint src/openflight/ --fail-under=9
```

- [ ] **Step 7: Commit**

```bash
git add firmware/iwr6843 src/openflight/iwr6843 tests/test_iwr6843_monitor.py
git commit -m "feat(iwr6843): add the result-only shot path with a cell-transfer fallback"
```

---

## Task 10: Gate Raw Transfer Behind `save_dumps`

**Files:**
- Modify: `src/openflight/iwr6843/runtime.py`, `src/openflight/iwr6843/monitor.py`
- Modify: `tests/test_iwr6843_monitor.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_normal_play_transfers_results_only(monkeypatch):
    """With save_dumps off and a confident on-chip result, no cells move."""


def test_save_dumps_session_still_transfers_and_replays_raw_cells(tmp_path):
    """save_dumps sessions keep the raw path so replay.py still works."""
```

Fill both in against the real `IWR6843CaptureMonitor` API.

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_monitor.py -k "save_dumps or results_only" -v
```

- [ ] **Step 3: Implement**

When `save_dumps` is false and the on-chip result is confident, skip the cell transfer. When `save_dumps` is true, transfer cells as well as the result, so the session remains replayable and the reference stays cross-checkable.

- [ ] **Step 4: Verify replay still works end to end**

```bash
uv run python -m openflight.iwr6843.replay <a save_dumps session>
```

Expected: the Python solve produces the same angles as before the port.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843 tests/test_iwr6843_monitor.py
git commit -m "feat(iwr6843): transfer raw cells only in save_dumps sessions"
```

---

## Task 11: Shadow Validation on Hardware

**Files:**
- Create: `scripts/hardware-test/iwr6843_solve_shadow.py`

- [ ] **Step 1: Write the shadow script**

For each shot, capture both the on-chip result and the raw cells, run the Python reference over the cells, and record both angles plus their difference to CSV.

- [ ] **Step 2: Run a range session**

At least 40 shots across both clubs in the TrackMan corpus.

- [ ] **Step 3: Check the agreement**

Assert the on-chip and Python angles agree within the Task 6 tolerance on every shot. Investigate every outlier individually — a single disagreement is a bug, not noise.

- [ ] **Step 4: Check end-to-end MAE**

Compare the on-chip angles against the TrackMan reference. MAE must not regress against the recorded 0.86 degree baseline.

- [ ] **Step 5: Run the cadence soak once more**

```bash
uv run python scripts/hardware-test/iwr6843_cadence_soak.py \
    --config config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg --frames 50000
```

Expected: PASS with the DSS solving.

- [ ] **Step 6: Record the results**

Write the shadow comparison and the MAE into `docs/iwr6843/index.md` beside the existing figures. State the sample size.

- [ ] **Step 7: Commit**

```bash
git add scripts/hardware-test/iwr6843_solve_shadow.py docs/iwr6843/index.md
git commit -m "test(iwr6843): validate the on-chip solve against the Python reference"
```

---

## Task 12: Final Verification

- [ ] **Step 1: Full suite, lint, format**

```bash
uv run pytest tests/ -v
uv run pylint src/openflight/ --fail-under=9
uv run ruff check src/openflight/ && uv run ruff format --check src/openflight/
```

- [ ] **Step 2: Confirm the reference was never edited**

```bash
git diff --stat main -- src/openflight/iwr6843/lcmf.py src/openflight/iwr6843/club.py \
                        src/openflight/iwr6843/tracking.py src/openflight/iwr6843/late_window.py
```

Expected: **empty**. Any change here violates the global constraint and must be justified or reverted.

- [ ] **Step 3: Confirm the acceptance criteria**

Against the spec: results-only shot path; per-stage tolerances met; MAE not regressed; no-confidence fallback matches today; `save_dumps` replay works; cadence unaffected; tests and lint green. State each as pass or fail with evidence. Do not report completion on any criterion you did not run.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs(iwr6843): record the on-chip solve validation results"
```
