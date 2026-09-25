# IWR6843 Longer Capture Movie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the IWR6843 dense capture movie from 45 frames (90 ms) to 51 frames (102 ms) at an unchanged 2 ms cadence, by reclaiming the 98,304 B the IQ16 scratch occupies inside L3.

**Architecture:** The IQ16 ping/pong scratch currently overlays the top of the L3 capture arena via a compile-time offset macro. Relocate it to `DATA_RAM` (TCMB, 119,687 B unused) as a real typed array, freeing the whole 786,432 B L3 region for capture. Behaviour-preserving refactors land first so the functional change happens in readable code.

**Tech Stack:** C99 (TI ARM CGT for R4F, host `cc`/`gcc`/`clang` for tests), TI mmWave SDK, SYS/BIOS, Python 3 + pytest + ctypes, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-25-iwr6843-longer-movie-design.md`

## Global Constraints

- All Python commands run through `uv run`. Never bare `python`, `pip`, `pytest`.
- Lint gate: `uv run pylint src/openflight/ --fail-under=9` must hold.
- Format gate: `uv run ruff check src/openflight/` and `uv run ruff format --check src/openflight/`.
- **`make` is NOT installed on this dev host.** Every step below that says
  `make -C firmware docker-build` must instead be run as the raw equivalent,
  from the repo root in Git Bash:

  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --platform linux/amd64 \
    -v "$(pwd -W):/work" -w /work openflight-iwr-sdk:latest \
    make -C firmware build-native \
    RELEASE_NAME="l3_dump_configurable_capture_20260818.bin"
  ```

  `MSYS_NO_PATHCONV=1` is required: without it Git Bash rewrites `-w /work`
  into a Windows path and Docker rejects it. The `openflight-iwr-sdk:latest`
  image already exists and its layers are cached, so no TI download occurs.
- **Rebuilding overwrites `firmware/releases/l3_dump_configurable_capture_20260818.bin`.**
  That committed binary predates the current source, so a rebuild produces a
  different file. Unless a task explicitly ships a new release binary,
  `git checkout -- firmware/releases/` after building.
- **Test baseline is 28 failed / 1798 passed / 47 skipped**, not a green suite.
  Tasks that say "Expected: PASS" mean *no new failures against that baseline*
  plus the task's own new tests passing. 25 of the 28 are Linux/Pi-specific
  (file modes, bash syntax, udev, `.desktop` entries) failing on Windows; 3 are
  pre-existing in-flight work in `tests/test_server.py::TestIWR6843OnboardTracking`
  (`FakeCaptureMonitor` lacks a `self_trigger` attribute). **Do not fix these** —
  they are outside this plan's scope.
- Production firmware defines, from `firmware/Makefile:148`, must not change in this project: `N_TX=3 ENABLE_HWA_SMOKE=1 SNAPSHOT_DUMP=1 HWA_CHAINED_SNAPSHOT_RING=1 CONFIGURABLE_CAPTURE=1 HYBRID_CADENCE_CAPTURE=1 L3_RING_IQ8=1 L3_IQ8_EDMA_PACK=1`.
- Inter-frame budget: 380 us. No change may exceed it.
- Selective `l3track` readback must stay under 1.0 s.
- Capture geometry stays 3 TX, 12 loops, 4 RX, 53 bins, IQ8 (2 B/complex) = 15,264 B per frame.
- Host C test compilation uses `-std=c99 -O2 -Wall -Wextra -Werror`, matching `tests/test_iwr6843_detect_queue.py`.

## Review Focus

These are input classes the spec implies but no task's happy path exercises. Each has a test assigned to the task that owns the code.

1. **A capture plan that exactly fills the arena** (`usedBytes == capacity`) must be accepted, not rejected by an off-by-one. Pinned in Task 5.
2. **A plan that overflows by one byte** must be rejected with an error, not silently truncated. Pinned in Task 5.
3. **`preFrames` of 1** — the minimum viable ring — must not divide by zero or alias the write slot onto the read slot. Pinned in Task 5.
4. **IQ16 (wide) profile must still fit** after `L3_TOTAL_BYTES` becomes linker-derived; it uses the full arena with no scratch reservation. Pinned in Task 8.
5. **A stale or absent `l3_dump_mss.map`** must make the margin test fail loudly, never pass silently. Pinned in Task 7.

---

## File Structure

| File | Responsibility |
|---|---|
| `firmware/iwr6843/capture_plan.c` (create) | Pure C99 capture-plan arithmetic, host-testable |
| `firmware/iwr6843/capture_plan.h` (create) | Plan struct, limits, entry point |
| `firmware/iwr6843/l3_dump.c` (modify) | Scratch relocation, freeze predicate, delegation to `capture_plan.c` |
| `firmware/iwr6843/mss_linker.cmd` (modify) | Export the L3 region size symbol |
| `firmware/iwr6843/makefile` (modify) | Add `capture_plan.c` to `SOURCES` |
| `config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg` (create) | The 51-frame profile |
| `tests/test_iwr6843_capture_plan.py` (create) | ctypes tests + property test for the plan builder |
| `tests/test_iwr6843_memory_layout.py` (create) | Map-file DATA_RAM margin, scratch/arena invariants |
| `tests/test_iwr6843_firmware_rearm.py` (modify) | Budget literal + parsed cross-check, new profile |
| `src/openflight/iwr6843/monitor.py` (modify) | Dump-fallback timeout |
| `tests/test_iwr6843_monitor.py` (modify) | Timeout margin test |
| `scripts/hardware-test/iwr6843_cadence_soak.py` (create) | Acceptance soak |

---

## Task 1: Establish a Reproducible Build and Baseline

No code change. Everything after this depends on being able to build and diff the map file.

**Files:**
- Create: `firmware/iwr6843/baseline/l3_dump_mss.map.baseline`

- [ ] **Step 1: Build the container image**

```bash
make -C firmware docker-image
```

Expected: image builds. First run downloads TI installers and is slow.

- [ ] **Step 2: Build the firmware unchanged**

```bash
make -C firmware docker-build
```

Expected: `firmware/iwr6843/l3_dump.bin` and a printed sha256.

- [ ] **Step 3: Record the baseline map**

```bash
mkdir -p firmware/iwr6843/baseline
cp firmware/iwr6843/l3_dump_mss.map firmware/iwr6843/baseline/l3_dump_mss.map.baseline
sed -n '8,20p' firmware/iwr6843/baseline/l3_dump_mss.map.baseline
```

Expected output must show `L3_RAM ... 000c0000 000c0000 00000000` (region fully
used) and `DATA_RAM ... 00030000 00012e31 0001d1cf` — that is 119,247 B of
DATA_RAM free, which is the figure every later task's margin arithmetic uses.

- [ ] **Step 4: Run the existing test suite as a baseline**

```bash
uv run pytest tests/ -v
```

Expected: PASS. Record the count.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/baseline/l3_dump_mss.map.baseline
git commit -m "chore(iwr6843): record baseline map for the L3 layout work"
```

---

## Task 2: Probe the L3 Bank Count (Phase 0 Gate)

Throwaway build. The only artifact kept is a recorded finding in the spec.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-25-iwr6843-longer-movie-design.md`

- [ ] **Step 1: Build with a raised bank count**

```bash
make -C firmware docker-build MMWAVE_L3RAM_NUM_BANK=8
```

If the build fails, that itself is the answer: record it and move on. Do not chase SDK internals.

- [ ] **Step 2: Read the resulting region size**

```bash
sed -n '8,20p' firmware/iwr6843/l3_dump_mss.map
```

Compare `L3_RAM` length against the baseline `000c0000`. A larger value means spare banks exist.

- [ ] **Step 3: Restore the normal build**

```bash
make -C firmware docker-build
```

- [ ] **Step 4: Record the finding in the spec**

Add a line under "Risks And Open Questions" in the spec stating the observed `L3_RAM` length at 8 banks and whether extra L3 is available. State the number, not an impression.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-25-iwr6843-longer-movie-design.md
git commit -m "docs(iwr6843): record the L3 bank probe result"
```

**Gate:** if extra banks exist, the 51-frame target in this plan is a floor, not a ceiling. Do not change the target mid-plan; raise it in a follow-up once the soak passes.

---

## Task 3: Spike the TCMB Relocation (Phase 0 Gate)

Throwaway. The spec makes this a gate *before* the refactors, because a failure
here collapses the whole project from +6 frames to +2.4 and changes what is
worth refactoring. Do not skip it because Task 7 does the relocation properly.

**Files:** none kept. Work on a scratch branch and discard it.

- [ ] **Step 1: Branch for the throwaway**

```bash
git switch -c spike/tcmb-scratch
```

- [ ] **Step 2: Make the minimal relocation**

In `l3_dump.c`, change `L3_IQ8_CAPTURE_BYTES` to `(L3_TOTAL_BYTES)`, delete the
`g_iq16FrameScratch` macro, and declare the array in `DATA_RAM`:

```c
#pragma DATA_SECTION(g_iq16FrameScratch, ".dataScratch")
#pragma DATA_ALIGN(g_iq16FrameScratch, 8)
static int16_t g_iq16FrameScratch[2][L3_IQ16_SCRATCH_WORDS];
```

Add `.dataScratch : {} > DATA_RAM` to `mss_linker.cmd`. No tests, no cleanup —
this build is going in the bin.

- [ ] **Step 3: Build**

```bash
make -C firmware docker-build
```

If the link fails on DATA_RAM overflow, that is the gate failing. Record it and
stop.

- [ ] **Step 4: Soak it on hardware**

Flash and run the existing 45-frame dense profile for at least 50,000 frames,
then read `stats` and record `hwa_frames`, `hwa_missed`, `iq8_overrun` and
`iq8_waits`. Compare the miss rate against the recorded 0.0089% baseline.

Task 12 builds the scripted soak; here, drive the CLI by hand.

- [ ] **Step 5: Record the finding in the spec**

Write the measured miss rate, overrun count and wait count into the spec's
Phase 0 section, with the frame count they came from.

**Gate:** if EDMA-to-TCM contention pushes the miss rate above the baseline, the
scratch stays in L3 with a plan-derived offset, the target drops from 51 frames
to about 47, and Task 7 must be rewritten before it is attempted. Raise this
rather than deciding alone.

- [ ] **Step 6: Discard the spike**

```bash
git switch - && git branch -D spike/tcmb-scratch
git add docs/superpowers/specs/2026-09-25-iwr6843-longer-movie-design.md
git commit -m "docs(iwr6843): record the TCMB scratch spike result"
```

---

## Task 4: Delete Dead Build Variants

`LIVE_SNAPSHOT_RING` and the non-`CONFIGURABLE_CAPTURE` paths are never built by `firmware/Makefile:148`. Both IQ8 and IQ16 formats are live, so `L3_RING_IQ8` stays.

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c`
- Modify: `firmware/iwr6843/mss_linker.cmd:11-12` (the `.l3scratch` comment references live-snapshot builds)

**Interfaces:**
- Consumes: nothing.
- Produces: an `l3_dump.c` where `#ifdef CONFIGURABLE_CAPTURE` and `#ifdef LIVE_SNAPSHOT_RING` no longer appear. `#ifdef L3_RING_IQ8`, `#ifdef L3_IQ8_EDMA_PACK`, `#ifdef L3_IQ8_SPARSE_SCALE`, `#ifdef SNAPSHOT_DYNAMIC_WINDOWS` and `#ifdef ENABLE_HWA_SMOKE` all remain.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_iwr6843_firmware_rearm.py`:

```python
def test_dead_build_variants_are_gone():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "LIVE_SNAPSHOT_RING" not in source
    assert "CONFIGURABLE_CAPTURE" not in source
    # Live variants must survive the cleanup.
    assert "L3_RING_IQ8" in source
    assert "L3_IQ8_EDMA_PACK" in source
    assert "HWA_CHAINED_SNAPSHOT_RING" in source
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py::test_dead_build_variants_are_gone -v
```

Expected: FAIL, because both symbols are present.

- [ ] **Step 3: Delete the `LIVE_SNAPSHOT_RING` blocks**

Remove every `#ifdef LIVE_SNAPSHOT_RING` / `#endif` region and its contents, including the two scratch buffers declared around `l3_dump.c:299-304` and the `#error` guards at `l3_dump.c:136-143` and `:151-153`. Delete the `L3_SNAPSHOT_TASK_PRIORITY` define and the snapshot task if nothing else references them.

- [ ] **Step 4: Collapse the `CONFIGURABLE_CAPTURE` conditionals**

For every `#ifdef CONFIGURABLE_CAPTURE` block, keep the `#ifdef` arm and delete the `#else` arm and the directives. For every `#ifndef CONFIGURABLE_CAPTURE` guard that raises an error (for example in `l3_cli_track`, `l3_dump.c:3356-3358`), delete the guard and its error arm. Delete the `#error` at `l3_dump.c:191-193`.

- [ ] **Step 5: Verify the preprocessor density dropped**

```bash
grep -c "^[[:space:]]*#if\|^[[:space:]]*#ifdef\|^[[:space:]]*#ifndef\|^[[:space:]]*#else\|^[[:space:]]*#elif\|^[[:space:]]*#endif" firmware/iwr6843/l3_dump.c
```

Expected: materially below the baseline of 456.

- [ ] **Step 6: Build**

```bash
make -C firmware docker-build
```

Expected: links cleanly. Compare the map's `L3_RAM` and `DATA_RAM` rows against the baseline — they must be unchanged, because this task changes no layout.

- [ ] **Step 7: Run the tests**

```bash
uv run pytest tests/ -v
```

Expected: PASS, including the new test.

- [ ] **Step 8: Commit**

```bash
git add firmware/iwr6843/l3_dump.c firmware/iwr6843/mss_linker.cmd tests/test_iwr6843_firmware_rearm.py
git commit -m "refactor(iwr6843): delete unbuilt firmware variants"
```

---

## Task 5: Extract the Capture Plan Builder Into Host-Testable C99

This is the highest-value executable test in the firmware: pure arithmetic, no hardware, changed by every other task.

**Files:**
- Create: `firmware/iwr6843/capture_plan.h`
- Create: `firmware/iwr6843/capture_plan.c`
- Modify: `firmware/iwr6843/l3_dump.c` (delegate from `l3_buildCapturePlan`)
- Modify: `firmware/iwr6843/makefile:73` (`SOURCES`)
- Create: `tests/test_iwr6843_capture_plan.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `L3CapturePlan` struct (fields mirror `l3_capture_plan_t` in `l3_dump.c:236-259`).
  - `L3CaptureGeometry { uint32_t nTx; uint32_t nRx; uint32_t maxSamples; uint32_t maxCaptureFrames; uint32_t maxLoops; uint32_t minLoops; uint32_t maxBins; uint32_t maxPostStride; }`.
  - `L3CaptureTables { uint8_t *binStart; uint8_t *binCount; uint16_t *deltaUs; uint32_t *offset; uint32_t *bytes; }`.
  - `int32_t l3plan_build(L3CapturePlan *plan, const L3CaptureGeometry *geom, L3CaptureTables *tables, uint32_t loops, uint32_t framePeriodUs, uint32_t capacityBytes, uint32_t bytesPerComplex, char *err, uint32_t errLen);`
    Returns 0 on success, -1 on rejection with a message in `err`.

The error sink replaces `CLI_write`, so the function has no TI dependency.

- [ ] **Step 1: Write the header**

Create `firmware/iwr6843/capture_plan.h`:

```c
/* Capture-plan arithmetic, shared by the R4F build and the host tests.
 *
 * Pure C99 with no TI headers, so tests/test_iwr6843_capture_plan.py can
 * compile this file with the host compiler. l3_dump.c owns the globals and
 * the CLI; this file owns the byte budget and the per-frame layout.
 */
#ifndef L3_CAPTURE_PLAN_H
#define L3_CAPTURE_PLAN_H

#include <stdint.h>

typedef struct {
    uint8_t  preStart;
    uint8_t  preBins;
    uint8_t  postStart;
    uint8_t  postBins;
    uint8_t  lateStart;
    uint8_t  postFrames;
    uint8_t  postStride;
    uint8_t  preFrames;
    uint8_t  totalFrames;
    uint16_t loops;
    uint16_t chirpsPerFrame;
    uint32_t preFrameBytes;
    uint32_t postFrameBytes;
    uint32_t postBaseOffset;
    uint32_t usedBytes;
    uint8_t  phased;
    uint8_t  requestedPreFrames;
    uint8_t  impactStart;
    uint8_t  impactBins;
    uint8_t  impactFrames;
    uint8_t  ballFrames;
    uint32_t impactFrameBytes;
} L3CapturePlan;

typedef struct {
    uint32_t nTx;
    uint32_t nRx;
    uint32_t maxSamples;
    uint32_t maxCaptureFrames;
    uint32_t maxLoops;
    uint32_t minLoops;
    uint32_t maxBins;
    uint32_t maxPostStride;
} L3CaptureGeometry;

typedef struct {
    uint8_t  *binStart;
    uint8_t  *binCount;
    uint16_t *deltaUs;
    uint32_t *offset;
    uint32_t *bytes;
} L3CaptureTables;

/* Returns 0 and fills plan + tables, or -1 with a message written to err. */
int32_t l3plan_build(L3CapturePlan *plan,
                     const L3CaptureGeometry *geom,
                     L3CaptureTables *tables,
                     uint32_t loops,
                     uint32_t framePeriodUs,
                     uint32_t capacityBytes,
                     uint32_t bytesPerComplex,
                     char *err,
                     uint32_t errLen);

#endif /* L3_CAPTURE_PLAN_H */
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_iwr6843_capture_plan.py`:

```python
"""Host build of the firmware capture-plan arithmetic.

l3_buildCapturePlan decides the byte budget and every frame's offset in L3.
This file is plain C99 so the host compiler can check that arithmetic without
the TI toolchain, mirroring tests/test_iwr6843_detect_queue.py.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "firmware" / "iwr6843" / "capture_plan.c"
MAX_FRAMES = 64


class Plan(ctypes.Structure):
    _fields_ = [
        ("preStart", ctypes.c_uint8),
        ("preBins", ctypes.c_uint8),
        ("postStart", ctypes.c_uint8),
        ("postBins", ctypes.c_uint8),
        ("lateStart", ctypes.c_uint8),
        ("postFrames", ctypes.c_uint8),
        ("postStride", ctypes.c_uint8),
        ("preFrames", ctypes.c_uint8),
        ("totalFrames", ctypes.c_uint8),
        ("loops", ctypes.c_uint16),
        ("chirpsPerFrame", ctypes.c_uint16),
        ("preFrameBytes", ctypes.c_uint32),
        ("postFrameBytes", ctypes.c_uint32),
        ("postBaseOffset", ctypes.c_uint32),
        ("usedBytes", ctypes.c_uint32),
        ("phased", ctypes.c_uint8),
        ("requestedPreFrames", ctypes.c_uint8),
        ("impactStart", ctypes.c_uint8),
        ("impactBins", ctypes.c_uint8),
        ("impactFrames", ctypes.c_uint8),
        ("ballFrames", ctypes.c_uint8),
        ("impactFrameBytes", ctypes.c_uint32),
    ]


class Geometry(ctypes.Structure):
    _fields_ = [
        ("nTx", ctypes.c_uint32),
        ("nRx", ctypes.c_uint32),
        ("maxSamples", ctypes.c_uint32),
        ("maxCaptureFrames", ctypes.c_uint32),
        ("maxLoops", ctypes.c_uint32),
        ("minLoops", ctypes.c_uint32),
        ("maxBins", ctypes.c_uint32),
        ("maxPostStride", ctypes.c_uint32),
    ]


class Tables(ctypes.Structure):
    _fields_ = [
        ("binStart", ctypes.POINTER(ctypes.c_uint8)),
        ("binCount", ctypes.POINTER(ctypes.c_uint8)),
        ("deltaUs", ctypes.POINTER(ctypes.c_uint16)),
        ("offset", ctypes.POINTER(ctypes.c_uint32)),
        ("bytes", ctypes.POINTER(ctypes.c_uint32)),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no host C compiler to build capture_plan.c")
    suffix = ".dll" if sys.platform == "win32" else ".so"
    out = tmp_path_factory.mktemp("capture_plan") / f"libcapture_plan{suffix}"
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
    library.l3plan_build.argtypes = [
        ctypes.POINTER(Plan),
        ctypes.POINTER(Geometry),
        ctypes.POINTER(Tables),
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    library.l3plan_build.restype = ctypes.c_int32
    return library


def _geometry():
    return Geometry(3, 4, 128, MAX_FRAMES, 16, 2, 64, 16)


def _tables():
    store = {
        "binStart": (ctypes.c_uint8 * MAX_FRAMES)(),
        "binCount": (ctypes.c_uint8 * MAX_FRAMES)(),
        "deltaUs": (ctypes.c_uint16 * MAX_FRAMES)(),
        "offset": (ctypes.c_uint32 * MAX_FRAMES)(),
        "bytes": (ctypes.c_uint32 * MAX_FRAMES)(),
    }
    tables = Tables(
        store["binStart"],
        store["binCount"],
        store["deltaUs"],
        store["offset"],
        store["bytes"],
    )
    return tables, store


def _dense_plan():
    plan = Plan()
    plan.preStart, plan.preBins, plan.requestedPreFrames = 20, 53, 8
    plan.impactStart, plan.impactBins, plan.impactFrames = 32, 53, 10
    plan.postStart, plan.postBins, plan.lateStart = 47, 53, 64
    plan.ballFrames, plan.postStride = 33, 1
    plan.postFrames = plan.impactFrames + plan.ballFrames
    plan.phased = 1
    return plan


def _build(lib, plan, capacity, loops=12, period=2000, bpc=2):
    tables, store = _tables()
    err = ctypes.create_string_buffer(128)
    rc = lib.l3plan_build(
        ctypes.byref(plan),
        ctypes.byref(_geometry()),
        ctypes.byref(tables),
        loops,
        period,
        capacity,
        bpc,
        err,
        len(err),
    )
    return rc, store, err.value.decode()


def test_relocated_arena_fits_fifty_one_frames(lib):
    plan = _dense_plan()
    rc, _store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    assert plan.totalFrames == 51
    assert plan.usedBytes == 51 * 15_264


def test_plan_that_exactly_fills_the_arena_is_accepted(lib):
    plan = _dense_plan()
    rc, _store, err = _build(lib, plan, 51 * 15_264)
    assert rc == 0, err
    assert plan.usedBytes == 51 * 15_264


def test_plan_one_byte_short_is_rejected(lib):
    plan = _dense_plan()
    rc, _store, err = _build(lib, plan, 51 * 15_264 - 1)
    assert rc == -1
    assert err != ""


def test_single_pre_frame_ring_is_valid(lib):
    plan = _dense_plan()
    plan.requestedPreFrames = 1
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    assert plan.preFrames == 1
    assert store["offset"][0] == 0


def test_odd_loop_count_is_rejected(lib):
    plan = _dense_plan()
    rc, _store, _err = _build(lib, plan, 786_432, loops=11)
    assert rc == -1


def test_frame_offsets_never_overlap(lib):
    plan = _dense_plan()
    rc, store, err = _build(lib, plan, 786_432)
    assert rc == 0, err
    spans = [
        (store["offset"][i], store["offset"][i] + store["bytes"][i])
        for i in range(plan.totalFrames)
    ]
    for (_start_a, end_a), (start_b, _end_b) in zip(spans, spans[1:]):
        assert end_a <= start_b
    assert spans[-1][1] == plan.usedBytes


@pytest.mark.parametrize("pre_frames", range(1, 17))
@pytest.mark.parametrize("ball_frames", (1, 7, 19, 33))
def test_no_valid_plan_overlaps_or_overflows(lib, pre_frames, ball_frames):
    plan = _dense_plan()
    plan.requestedPreFrames = pre_frames
    plan.ballFrames = ball_frames
    plan.postFrames = plan.impactFrames + ball_frames
    rc, store, _err = _build(lib, plan, 786_432)
    if rc != 0:
        return
    cursor = 0
    for i in range(plan.totalFrames):
        assert store["offset"][i] == cursor
        cursor += store["bytes"][i]
    assert cursor == plan.usedBytes
    assert plan.usedBytes <= 786_432
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest tests/test_iwr6843_capture_plan.py -v
```

Expected: FAIL at compilation, because `capture_plan.c` does not exist.

- [ ] **Step 4: Write `capture_plan.c`**

Move the body of `l3_buildCapturePlan` (`l3_dump.c:596-780`) into `l3plan_build`, making three mechanical substitutions and no logic changes:

1. `gCapturePlan.X` becomes `plan->X`.
2. Compile-time limits (`L3_MIN_LOOPS`, `L3_MAX_LOOPS`, `L3_MAX_CAPTURE_FRAMES`, `L3_RING_MAX_BINS`, `L3_MAX_POST_STRIDE`, `N_SAMPLES`, `N_RX`, `N_TX`) become `geom->` fields.
3. Every `CLI_write("Error: ...")` becomes a write into `err` via `snprintf`, followed by `return -1`. Keep the message text identical so operators see no change.

The IQ8 bin-count guard (`l3_dump.c:610-618`) becomes unconditional, gated on `bytesPerComplex == 2U` rather than `#ifdef L3_RING_IQ8`.

Do not port the two `CLI_write` summary blocks at the end (`l3_dump.c:738-775`); they stay in `l3_dump.c` and read from the returned plan.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_iwr6843_capture_plan.py -v
```

Expected: PASS.

- [ ] **Step 6: Delegate from `l3_dump.c`**

Replace the body of `l3_buildCapturePlan` with a call to `l3plan_build`, passing `&gCapturePlan`, a static `L3CaptureGeometry` built from the existing defines, a `L3CaptureTables` pointing at `gFrameBinStart` / `gFrameBinCount` / `gFrameDeltaUs` / `gFrameOffset` / `gFrameBytes`, and a local `char err[128]`. On -1, `CLI_write("%s", err)` and return -1. On 0, run the existing summary `CLI_write` blocks and return 0.

Add `#include "capture_plan.h"` next to the existing `#include "detect_queue.h"` (`l3_dump.c:52`).

- [ ] **Step 7: Add the source to the build**

In `firmware/iwr6843/makefile:73`:

```make
SOURCES    = l3_dump.c track_select.c detect_queue.c capture_plan.c
```

- [ ] **Step 8: Build and run everything**

```bash
make -C firmware docker-build && uv run pytest tests/ -v
```

Expected: builds; all tests pass. The map's `L3_RAM` and `DATA_RAM` rows must still match the baseline.

- [ ] **Step 9: Commit**

```bash
git add firmware/iwr6843/capture_plan.c firmware/iwr6843/capture_plan.h \
        firmware/iwr6843/l3_dump.c firmware/iwr6843/makefile \
        tests/test_iwr6843_capture_plan.py
git commit -m "refactor(iwr6843): extract the capture plan builder into host-testable C99"
```

---

## Task 6: Extract the Freeze Predicate

The same condition is written at `l3_dump.c:1173`, `:1188`, and `:2217`. Task 7 edits this region, so collapse it first.

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c`
- Modify: `tests/test_iwr6843_firmware_rearm.py`

**Interfaces:**
- Produces: `static inline uint8_t l3_shouldFreezeNow(void)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_iwr6843_firmware_rearm.py`:

```python
def test_freeze_condition_is_written_once():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert source.count("gPostFramesCaptured >= gCapturePlan.postFrames") == 1
    assert "l3_shouldFreezeNow" in source
    assert source.count("l3_shouldFreezeNow()") >= 3
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py::test_freeze_condition_is_written_once -v
```

Expected: FAIL, the count is 3.

- [ ] **Step 3: Add the predicate**

Above `l3_hwaMaybeQueueRearm`, add:

```c
/* True when the post-trigger movie is complete and capture may stop.
 * CALLER MUST HOLD THE CRITICAL SECTION: every call site is already inside
 * Hwi_disable(), and the globals it reads are written from the EDMA and HWA
 * completion callbacks. */
static inline uint8_t l3_shouldFreezeNow(void)
{
    return (uint8_t)(gHwaFreezeRequested && gActiveFrameIsPost &&
                     gActiveFrameShouldKeep &&
                     gPostFramesCaptured >= gCapturePlan.postFrames);
}
```

- [ ] **Step 4: Replace the three call sites**

At each of the three sites, replace the four-line condition with `l3_shouldFreezeNow()`. Change only the condition. Leave the bodies, and leave the IQ8 arm's unconditional `gHwaRearmPending = 1U` at `l3_dump.c:1170` exactly as it is — see Step 6.

- [ ] **Step 5: Build and test**

```bash
make -C firmware docker-build && uv run pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 6: Record the unresolved discrepancy**

Add a comment immediately above the IQ8 arm's unconditional rearm:

```c
/* UNRESOLVED: this arm sets gHwaRearmPending unconditionally, unlike the
 * IQ16 arm below, which gates it on l3_shouldFreezeNow(). Whether that is
 * deliberate is not established. Do not collapse these two bodies until a
 * test pins the intended behaviour: see
 * docs/superpowers/specs/2026-09-25-iwr6843-longer-movie-design.md. */
```

- [ ] **Step 7: Commit**

```bash
git add firmware/iwr6843/l3_dump.c tests/test_iwr6843_firmware_rearm.py
git commit -m "refactor(iwr6843): extract the freeze-vs-rearm predicate"
```

---

## Task 7: Relocate the IQ16 Scratch Into DATA_RAM

The functional change. Frees 98,304 B of L3.

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c:212-219`, `:263-266`, `:1655`, `:1738`, `:1875`
- Create: `tests/test_iwr6843_memory_layout.py`

**Interfaces:**
- Produces: `g_iq16FrameScratch` as a real array, `static int16_t g_iq16FrameScratch[2][L3_IQ16_SCRATCH_WORDS]`, in section `.dataScratch`. `L3_IQ8_CAPTURE_BYTES` becomes `L3_TOTAL_BYTES`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_iwr6843_memory_layout.py`:

```python
"""The IQ16 scratch must live outside L3 so capture owns the whole arena."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIRMWARE = ROOT / "firmware" / "iwr6843" / "l3_dump.c"
MAP = ROOT / "firmware" / "iwr6843" / "l3_dump_mss.map"
MIN_DATA_RAM_FREE_BYTES = 16 * 1024


def test_scratch_is_not_carved_out_of_the_capture_arena():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "#define L3_IQ8_CAPTURE_BYTES   (L3_TOTAL_BYTES)" in source
    assert "&g_ring[L3_IQ8_CAPTURE_BYTES]" not in source


def test_scratch_is_a_real_array_in_its_own_section():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert 'DATA_SECTION(g_iq16FrameScratch, ".dataScratch")' in source
    assert "static int16_t g_iq16FrameScratch[2][L3_IQ16_SCRATCH_WORDS];" in source


def _memory_rows(path: Path = MAP) -> dict[str, tuple[int, int]]:
    if not path.exists():
        pytest.skip(
            f"no linker map at {path}; this check needs a local firmware build "
            f"(see the plan's Global Constraints for the docker command). The "
            f"_Static_assert in l3_dump.c enforces the scratch size at build "
            f"time regardless, and test_baseline_map_geometry_is_intact below "
            f"always runs."
        )
    rows: dict[str, tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(
            r"\s+(\w+)\s+([0-9a-f]{8})\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)",
            line,
        )
        if match:
            rows.setdefault(match.group(1), (int(match.group(4), 16), int(match.group(5), 16)))
    if not rows:
        pytest.fail(f"could not parse MEMORY CONFIGURATION from {MAP}")
    return rows


def test_data_ram_keeps_a_working_margin():
    _used, unused = _memory_rows()["DATA_RAM"]
    assert unused >= MIN_DATA_RAM_FREE_BYTES, (
        f"DATA_RAM free margin fell to {unused} B, below {MIN_DATA_RAM_FREE_BYTES} B"
    )


def test_l3_is_fully_claimed_by_the_capture_ring():
    used, unused = _memory_rows()["L3_RAM"]
    assert unused == 0
    assert used == 786_432


def test_baseline_map_geometry_is_intact():
    """Always runs: the baseline map IS tracked, unlike the build output.

    Guards the parser itself and catches a corrupted or truncated baseline,
    so CI keeps real coverage even with no toolchain present.
    """
    rows = _memory_rows(BASELINE_MAP)
    assert rows["L3_RAM"] == (786_432, 0)
    baseline_data_ram_free = rows["DATA_RAM"][1]
    assert baseline_data_ram_free >= 98_304 + MIN_DATA_RAM_FREE_BYTES, (
        "the pre-relocation baseline no longer has room for a 98,304 B scratch "
        "plus the required margin; the relocation premise is broken"
    )
```

Add `BASELINE_MAP = ROOT / "firmware" / "iwr6843" / "baseline" / "l3_dump_mss.map.baseline"` beside `MAP`.

**Why skip, not fail, on a missing build map:** `firmware/iwr6843/l3_dump_mss.map` is
gitignored (`firmware/iwr6843/.gitignore`), so it exists only after a local
build. A `pytest.fail` there would redden the suite permanently for anyone
without the TI toolchain, including CI. The always-running
`test_baseline_map_geometry_is_intact` plus the `_Static_assert` in
`l3_dump.c` carry the coverage that matters; the skip message names the build
command so the gap is visible rather than silent.

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_memory_layout.py -v
```

Expected: the two source tests FAIL.

- [ ] **Step 3: Change the arena size**

At `l3_dump.c:219`:

```c
/* The scratch now lives in DATA_RAM (see g_iq16FrameScratch), so IQ8 capture
 * owns the entire L3 arena. */
#define L3_IQ8_CAPTURE_BYTES   (L3_TOTAL_BYTES)
```

- [ ] **Step 4: Replace the overlay macro with a real array**

Delete the macro at `l3_dump.c:264-266` and declare, next to `g_ring`:

```c
#pragma DATA_SECTION(g_iq16FrameScratch, ".dataScratch")
#pragma DATA_ALIGN(g_iq16FrameScratch, 8)
static int16_t g_iq16FrameScratch[2][L3_IQ16_SCRATCH_WORDS];
```

The three consumers at `l3_dump.c:1655`, `:1738` and `:1875` already index it as `g_iq16FrameScratch[scratch][0]`, so they need no change. Verify that by eye rather than assuming.

- [ ] **Step 5: Add the compile-time guard**

Below the declaration:

```c
/* DATA_RAM is 0x30000 B and is shared with .bss, .data and the stack. If this
 * fires, either the scratch or the rest of the image grew past the region. */
_Static_assert(sizeof(g_iq16FrameScratch) <= 0x18000U,
               "IQ16 scratch exceeds its DATA_RAM allowance");
```

- [ ] **Step 6: Place the section**

In `firmware/iwr6843/mss_linker.cmd`, inside `SECTIONS`:

```
    .dataScratch : {} > DATA_RAM
```

- [ ] **Step 7: Build**

```bash
make -C firmware docker-build
sed -n '8,20p' firmware/iwr6843/l3_dump_mss.map
```

Expected: `L3_RAM` still shows `000c0000` used with 0 unused, and `DATA_RAM`
used rises by 98,304 B from `00012e31` to `0002ae31`, leaving `000051cf`
(20,943 B) free — comfortably above the 16,384 B floor the test asserts.

**If the link fails on DATA_RAM overflow**, stop. The spec's fallback is to keep the scratch in L3 with a plan-derived offset, which yields 2.4 frames instead of 6.4. Record the failure in the spec and raise it before continuing.

- [ ] **Step 8: Run the tests**

```bash
uv run pytest tests/test_iwr6843_memory_layout.py tests/ -v
```

Expected: PASS. Note `test_l3_is_fully_claimed_by_the_capture_ring` passes trivially here because `g_ring` is statically `L3_TOTAL_BYTES`; it guards against a future change shrinking it.

- [ ] **Step 9: Commit**

```bash
git add firmware/iwr6843/l3_dump.c firmware/iwr6843/mss_linker.cmd tests/test_iwr6843_memory_layout.py
git commit -m "feat(iwr6843): move the IQ16 scratch into DATA_RAM to free 96 KB of L3"
```

---

## Task 8: Derive `L3_TOTAL_BYTES` From the Linker

`l3_dump.c:182` hardcodes `6U * 128U * 1024U`. It agrees with the linker by coincidence.

**Files:**
- Modify: `firmware/iwr6843/mss_linker.cmd`
- Modify: `firmware/iwr6843/l3_dump.c:182`
- Modify: `tests/test_iwr6843_firmware_rearm.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_iwr6843_firmware_rearm.py`:

```python
def test_l3_total_bytes_is_not_hardcoded():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "6U * 128U * 1024U" not in source
    assert "__l3ring_size" in source


def test_wide_iq16_profile_still_fits_the_arena():
    tx, loops, rx = 3, 12, 4
    wide_bytes = tx * loops * rx * 24 * 53 * 4
    assert wide_bytes == 732_672
    assert wide_bytes <= 786_432
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py::test_l3_total_bytes_is_not_hardcoded -v
```

Expected: FAIL.

- [ ] **Step 3: Export the region size**

In `mss_linker.cmd`, after the `SECTIONS` block:

```
/* The application sizes its capture arena from the region, so the two can
   never drift. */
__l3ring_size = size(L3_RAM);
```

- [ ] **Step 4: Consume it**

Replace the define at `l3_dump.c:182`:

```c
/* Exported by mss_linker.cmd as size(L3_RAM). Taking the address of the
 * linker symbol yields the value, which is the standard TI idiom. */
extern uint8_t __l3ring_size;
#define L3_TOTAL_BYTES ((uint32_t)(uintptr_t)&__l3ring_size)
```

Because this is no longer a constant expression, `static uint8_t g_ring[L3_TOTAL_BYTES]` will not compile. Keep `g_ring` sized by a separate compile-time `L3_RING_DECLARED_BYTES (6U * 128U * 1024U)` and add:

```c
/* g_ring must be declared at compile time, but the capture arena is sized
 * from the linker. If a bank-count change makes these disagree, the runtime
 * check in l3_initCapture() refuses to start rather than overrunning. */
```

Then in the sensor-start path, before the first `l3plan_build` call, reject a mismatch:

```c
if (L3_TOTAL_BYTES != L3_RING_DECLARED_BYTES) {
    CLI_write("Error: L3 region is %u B but g_ring is %u B; rebuild\n",
              (unsigned)L3_TOTAL_BYTES, (unsigned)L3_RING_DECLARED_BYTES);
    return -1;
}
```

- [ ] **Step 5: Build and test**

```bash
make -C firmware docker-build && uv run pytest tests/ -v
```

Expected: PASS; map unchanged from Task 7.

- [ ] **Step 6: Commit**

```bash
git add firmware/iwr6843/l3_dump.c firmware/iwr6843/mss_linker.cmd tests/test_iwr6843_firmware_rearm.py
git commit -m "fix(iwr6843): derive the L3 arena size from the linker region"
```

---

## Task 9: Cross-Check the Budget Test Against the Firmware Source

`tests/test_iwr6843_firmware_rearm.py:253` hardcodes `iq8_capacity = 688_128`, which Task 7 invalidated.

**Files:**
- Modify: `tests/test_iwr6843_firmware_rearm.py:248-263`

- [ ] **Step 1: Replace the budget test**

```python
def _parse_define(source: str, name: str) -> str:
    match = re.search(rf"^#define\s+{name}\s+(.+)$", source, re.MULTILINE)
    assert match, f"{name} not found in the firmware source"
    return match.group(1).strip()


def test_supported_profiles_fit_the_l3_capture_budget():
    tx, loops, rx = 3, 12, 4
    wide_bytes = tx * loops * rx * 24 * 53 * 4
    dense_bytes = tx * loops * rx * 51 * 53 * 2
    wide_late_bytes = tx * loops * rx * 36 * 53 * 2
    iq8_capacity = 786_432
    dense_frame_bytes = tx * loops * rx * 53 * 2
    dense_post_bytes = (10 + 33) * dense_frame_bytes

    assert wide_bytes == 732_672
    assert dense_bytes == 778_464
    assert wide_late_bytes == 549_504
    assert wide_bytes <= iq8_capacity
    assert dense_bytes <= iq8_capacity
    assert wide_late_bytes <= iq8_capacity
    assert 8 * dense_frame_bytes <= iq8_capacity - dense_post_bytes

    # The literal above must still describe the firmware.
    source = FIRMWARE.read_text(encoding="utf-8")
    assert _parse_define(source, "L3_IQ8_CAPTURE_BYTES") == "(L3_TOTAL_BYTES)", (
        "the IQ8 arena no longer equals the whole L3 region; update iq8_capacity"
    )
```

Add `import re` at the top of the file if absent.

- [ ] **Step 2: Run it**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_iwr6843_firmware_rearm.py
git commit -m "test(iwr6843): cross-check the L3 budget literal against the firmware"
```

---

## Task 10: Add the 51-Frame Profile

**Files:**
- Create: `config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg`
- Modify: `tests/test_iwr6843_firmware_rearm.py`
- Modify: `docs/iwr6843/index.md`, `docs/reference/configuration.md`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_iwr6843_firmware_rearm.py`, and add `DENSE_51_CONFIG = CONFIG_DIR / "iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg"` beside the other config constants:

```python
def test_dense_51_profile_extends_the_ball_phase_at_2ms():
    commands = {line.split()[0]: line.split() for line in _config_lines(DENSE_51_CONFIG)}
    frame = commands["frameCfg"]
    phase = commands["phaseCaptureCfg"]
    assert float(frame[5]) == 2.0
    assert int(frame[3]) == 12
    assert commands["captureFormat"][1] == "iq8"
    # preFrames, impactFrames, ballFrames
    assert [int(phase[3]), int(phase[6]), int(phase[10])] == [8, 10, 33]
    assert sum((int(phase[3]), int(phase[6]), int(phase[10]))) == 51
    # every window is 53 bins
    assert [int(phase[2]), int(phase[5]), int(phase[8])] == [53, 53, 53]
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py::test_dense_51_profile_extends_the_ball_phase_at_2ms -v
```

Expected: FAIL, file not found.

- [ ] **Step 3: Write the config**

Create `config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg`:

```
% Advanced profile: 2 ms sampling with the ball phase extended to 66 ms.
% 51 frames x 2 ms = 102 ms (8 pre, 10 impact, 33 ball), 3 TX, 12 loops,
% moving 53-bin windows, IQ8 storage. Stride stays 1. Requires firmware with
% the IQ16 scratch relocated to DATA_RAM, which frees the whole 768 KB L3
% arena; it does not fit the pre-relocation 688,128 B arena.
dfeDataOutputMode 1
channelCfg 15 7 0
adcCfg 2 1
profileCfg 0 60.0 7 3 38 0 0 100 1 128 4000 0 0 30
chirpCfg 0 0 0 0 0 0 0 1
chirpCfg 1 1 0 0 0 0 0 2
chirpCfg 2 2 0 0 0 0 0 4
frameCfg 0 2 12 0 2 1 0
captureFormat iq8
iq8Scale 128
phaseCaptureCfg 20 53 8 32 53 10 47 53 64 33 1
lowPower 0 0
sensorStart
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py -v
```

Expected: PASS.

- [ ] **Step 5: Verify the plan builder accepts it**

The Task 5 test `test_relocated_arena_fits_fifty_one_frames` already asserts this geometry. Re-run it:

```bash
uv run pytest tests/test_iwr6843_capture_plan.py -v
```

Expected: PASS.

- [ ] **Step 6: Update the docs**

In `docs/iwr6843/index.md`, add a column or row for the new profile: 51 frames at 2 ms, 53 bins, IQ8, complete dump 778,464 B, 102 ms movie, 66 ms ball phase. State plainly that it has not been validated against TrackMan. In `docs/reference/configuration.md:68`, add the matching table row.

- [ ] **Step 7: Commit**

```bash
git add config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg tests/test_iwr6843_firmware_rearm.py \
        docs/iwr6843/index.md docs/reference/configuration.md
git commit -m "feat(iwr6843): add the 51-frame 2 ms dense profile"
```

---

## Task 11: Give the Host Dump Fallback Enough Timeout

`monitor.py:599` allows 12.0 s. The `l3dump` fallback grows to 7.47 s at 51 frames and 9.38 s at the 64-frame cap.

**Files:**
- Modify: `src/openflight/iwr6843/monitor.py:599`
- Modify: `tests/test_iwr6843_monitor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_iwr6843_monitor.py`:

```python
BAUD = 1_041_667
BYTES_PER_SECOND = BAUD / 10  # 8N1
MAX_CAPTURE_FRAMES = 64
FRAME_BYTES = 3 * 12 * 4 * 53 * 2
REQUIRED_MARGIN_S = 2.0


def test_dump_fallback_timeout_covers_the_frame_cap():
    from openflight.iwr6843.monitor import IWR6843CaptureMonitor

    default = inspect.signature(
        IWR6843CaptureMonitor.capture_for_shot
    ).parameters["timeout_s"].default
    worst_case_s = MAX_CAPTURE_FRAMES * FRAME_BYTES / BYTES_PER_SECOND
    assert default >= worst_case_s + REQUIRED_MARGIN_S, (
        f"timeout {default}s leaves under {REQUIRED_MARGIN_S}s over a "
        f"{worst_case_s:.2f}s worst-case dump"
    )
```

Add `import inspect` at the top if absent.

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_monitor.py::test_dump_fallback_timeout_covers_the_frame_cap -v
```

Expected: FAIL. Worst case is 9.38 s and the margin requirement pushes the needed value to 11.38 s, which 12.0 s already meets — **if it passes, that is a valid result**: record it and skip to Step 4 without changing `monitor.py`.

- [ ] **Step 3: Raise the default if needed**

Only if Step 2 failed, set `timeout_s: float = 14.0` at `monitor.py:599` with a comment naming the worst-case dump it covers.

- [ ] **Step 4: Sweep for other read-path timeouts**

```bash
grep -rn "timeout" src/openflight/iwr6843/monitor.py src/openflight/iwr6843/driver.py
```

For each hit, confirm it is unrelated to a full-dump read, or raise it the same way. Record what you checked in the commit message.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_iwr6843_monitor.py -v && uv run pylint src/openflight/ --fail-under=9
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/openflight/iwr6843/monitor.py tests/test_iwr6843_monitor.py
git commit -m "test(iwr6843): pin the dump-fallback timeout against the frame cap"
```

---

## Task 12: Hardware Cadence Soak (Acceptance Gate)

Nothing else in this plan proves the relocation preserved the 2 ms cadence on silicon.

**Files:**
- Create: `scripts/hardware-test/iwr6843_cadence_soak.py`
- Modify: `docs/iwr6843/verify.md`

- [ ] **Step 1: Write the script**

Create `scripts/hardware-test/iwr6843_cadence_soak.py`:

```python
"""Cadence acceptance soak for the IWR6843 capture path.

Runs the sensor for a fixed number of frames and fails if the firmware
reports dropped frame starts, IQ8 pack overruns, or EDMA errors above the
stated bound. This is the only check that validates the DATA_RAM scratch
relocation against the 380 us inter-frame budget on real silicon.

Usage:
    uv run python scripts/hardware-test/iwr6843_cadence_soak.py \\
        --config config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg \\
        --frames 50000
"""

from __future__ import annotations

import argparse
import re
import sys
import time

from openflight.iwr6843.driver import IWR6843Driver

# 0.0089% is the recorded rate for the shipped 45-frame profile. The
# relocation must not make it worse.
MAX_MISS_RATE = 0.0001
STAT_FIELDS = ("hwa_frames", "hwa_missed", "iq8_overrun", "iq8_edma_errors")


def parse_stats(text: str) -> dict[str, int]:
    """Pull the integer counters out of a firmware `stats` response."""
    return {
        key: int(value)
        for key, value in re.findall(r"(\w+)=(\d+)", text)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--frames", type=int, default=50_000)
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    driver = IWR6843Driver(cli_port=args.port) if args.port else IWR6843Driver()
    driver.configure(args.config)
    driver.start()

    target_s = args.frames * 0.002
    print(f"soaking {args.frames} frames (~{target_s:.0f}s)")
    time.sleep(target_s)

    stats = parse_stats(driver.command("stats"))
    driver.stop()

    for field in STAT_FIELDS:
        if field not in stats:
            print(f"FAIL: firmware stats did not report {field}")
            return 1

    frames = stats["hwa_frames"]
    missed = stats["hwa_missed"]
    rate = missed / frames if frames else 1.0
    print(f"frames={frames} missed={missed} rate={rate:.6%} "
          f"iq8_overrun={stats['iq8_overrun']} "
          f"edma_errors={stats['iq8_edma_errors']}")

    if frames < args.frames * 0.9:
        print(f"FAIL: only {frames} frames captured, expected ~{args.frames}")
        return 1
    if rate > MAX_MISS_RATE:
        print(f"FAIL: miss rate {rate:.6%} exceeds {MAX_MISS_RATE:.6%}")
        return 1
    if stats["iq8_overrun"] or stats["iq8_edma_errors"]:
        print("FAIL: IQ8 pack overruns or EDMA errors occurred")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Before writing this, read `src/openflight/iwr6843/driver.py` and match the real constructor, `configure`, `start`, `stop` and command-response API. If the names differ, use the real ones — do not add a shim.

- [ ] **Step 2: Test the stats parser without hardware**

Add to `tests/test_iwr6843_monitor.py`:

```python
def test_cadence_soak_parses_firmware_stats():
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "hardware-test"))
    from iwr6843_cadence_soak import parse_stats

    line = ("hwa_frames=112345 hwa_out=112345 hwa_rearms=112344 "
            "hwa_rearm_err=0 hwa_missed=10 iq8_overrun=0 iq8_edma_errors=0")
    stats = parse_stats(line)
    assert stats["hwa_frames"] == 112345
    assert stats["hwa_missed"] == 10
    assert stats["iq8_overrun"] == 0
```

- [ ] **Step 3: Run it**

```bash
uv run pytest tests/test_iwr6843_monitor.py::test_cadence_soak_parses_firmware_stats -v
```

Expected: PASS.

- [ ] **Step 4: Run the soak on hardware, both profiles**

```bash
uv run python scripts/hardware-test/iwr6843_cadence_soak.py \
    --config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg --frames 50000
uv run python scripts/hardware-test/iwr6843_cadence_soak.py \
    --config config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg --frames 50000
```

Expected: PASS on both. **This is the acceptance gate.** A failure here means the DATA_RAM relocation broke the inter-frame budget; revert to the L3 fallback described in the spec rather than tuning around it.

- [ ] **Step 5: Measure selective readback**

Take at least 20 shots on the 51-frame profile and confirm `l3track` readback stays under 1.0 s, and that `l3sparse` truncation warnings (`monitor.py:510`) are no more frequent than on the 45-frame profile. Record both numbers.

- [ ] **Step 6: Document the procedure**

Add a section to `docs/iwr6843/verify.md` covering the soak command, the pass criteria, and the readback measurement.

- [ ] **Step 7: Commit**

```bash
git add scripts/hardware-test/iwr6843_cadence_soak.py tests/test_iwr6843_monitor.py docs/iwr6843/verify.md
git commit -m "test(iwr6843): add the cadence acceptance soak"
```

---

## Task 13: Adaptive Per-Frame IQ8 Scale (Optional, Droppable)

Spec Phase 4. The shipped build quantises every frame with one global
`iq8Scale 128`, but the frames this project adds are the weakest late-flight
echoes. `gFrameIq8Scale[]` (`l3_dump.c:255`) is already emitted per frame; only
the selection is compiled out. **Do this only after Task 12 passes**, and
measure it as a separate delta so a cadence regression stays attributable.

**Files:**
- Modify: `firmware/Makefile:148`
- Modify: `tests/test_iwr6843_firmware_rearm.py`

- [ ] **Step 1: Write the failing test**

```python
def test_production_build_selects_a_per_frame_iq8_scale():
    text = FIRMWARE_MAKEFILE.read_text(encoding="utf-8")
    build = [line for line in text.splitlines() if "L3_VARIANT_DEFS" in line]
    assert build, "expected the build-native recipe to set L3_VARIANT_DEFS"
    assert any("L3_IQ8_SPARSE_SCALE=1" in line for line in build)
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py::test_production_build_selects_a_per_frame_iq8_scale -v
```

Expected: FAIL. The define is absent from `firmware/Makefile:148`.

- [ ] **Step 3: Enable the define**

Add `--define=L3_IQ8_SPARSE_SCALE=1` to `L3_VARIANT_DEFS` in the `build-native`
recipe. The code it enables already exists at `l3_dump.c:205-207`, `:1568`,
`:1600`, `:1659-1676`.

- [ ] **Step 4: Build and soak**

```bash
make -C firmware docker-build
uv run python scripts/hardware-test/iwr6843_cadence_soak.py \
    --config config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg --frames 50000
```

**Gate:** the scale-selection pass runs inside the 380 us inter-frame budget
alongside the relocated scratch. If the soak fails, **drop this task** and
revert the define. The relocation is load-bearing; this is the payoff and is
explicitly expendable.

- [ ] **Step 5: Confirm the late frames improved**

Take at least 20 shots and compare the per-frame `gFrameIq8Scale` values across
the movie. The late ball frames should select a smaller divisor than the impact
frames. If every frame picks the same scale, the pass is not doing anything and
the define should be reverted.

- [ ] **Step 6: Commit**

```bash
git add firmware/Makefile tests/test_iwr6843_firmware_rearm.py
git commit -m "feat(iwr6843): select a per-frame IQ8 scale for the dense profile"
```

---

## Task 14: Narrowed Pre-Phase Profile (Optional, Opt-In)

Spec Phase 4 and decision 3/11. Narrowing `preBins` from 53 to 32 saves
48,384 B — about 3 more frames — with **zero firmware change**, because the plan
and wire format already carry per-phase bin counts. It is the only lever that
can degrade *trigger* reliability, so it ships opt-in and is promoted only after
an on-range session.

**Files:**
- Create: `config/iwr6843_l3dump_dense_54f2ms_32prebin_iq8.cfg`
- Modify: `tests/test_iwr6843_firmware_rearm.py`
- Modify: `docs/iwr6843/index.md`

- [ ] **Step 1: Write the failing test**

```python
DENSE_54_CONFIG = CONFIG_DIR / "iwr6843_l3dump_dense_54f2ms_32prebin_iq8.cfg"


def test_narrow_pre_profile_is_opt_in_and_fits():
    commands = {line.split()[0]: line.split() for line in _config_lines(DENSE_54_CONFIG)}
    phase = commands["phaseCaptureCfg"]
    assert int(phase[2]) == 32, "pre window should be narrowed"
    assert [int(phase[5]), int(phase[8])] == [53, 53], "impact and ball stay wide"
    assert sum((int(phase[3]), int(phase[6]), int(phase[10]))) == 54
    tx, loops, rx = 3, 12, 4
    pre = tx * loops * rx * 8 * 32 * 2
    rest = tx * loops * rx * (10 + 36) * 53 * 2
    assert pre + rest <= 786_432
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_iwr6843_firmware_rearm.py::test_narrow_pre_profile_is_opt_in_and_fits -v
```

Expected: FAIL, file not found.

- [ ] **Step 3: Write the config**

Copy the 51-frame profile and change only the `phaseCaptureCfg` line:

```
phaseCaptureCfg 20 32 8 32 53 10 47 53 64 36 1
```

Head the file with a comment stating it is experimental, that the narrowed pre
window reduces the leave detector's range coverage, and that it must not become
the default until a range session confirms trigger reliability.

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_iwr6843_capture_plan.py tests/test_iwr6843_firmware_rearm.py -v
```

- [ ] **Step 5: Validate the trigger on range**

Take at least 40 shots on this profile. Compare the trigger rate and any missed
triggers against the 51-frame profile over a comparable session. **Do not
promote it to default.** Record both rates.

- [ ] **Step 6: Document it as experimental**

Add it to the `docs/iwr6843/index.md` profile table, explicitly marked
experimental and not TrackMan-validated, alongside the measured trigger rate.

- [ ] **Step 7: Commit**

```bash
git add config/iwr6843_l3dump_dense_54f2ms_32prebin_iq8.cfg \
        tests/test_iwr6843_firmware_rearm.py docs/iwr6843/index.md
git commit -m "feat(iwr6843): add the experimental narrowed pre-phase profile"
```

---

## Task 15: Final Verification

- [ ] **Step 1: Full suite**

```bash
uv run pytest tests/ -v
```

- [ ] **Step 2: Lint and format**

```bash
uv run pylint src/openflight/ --fail-under=9
uv run ruff check src/openflight/
uv run ruff format --check src/openflight/
```

- [ ] **Step 3: Clean firmware build**

```bash
make -C firmware docker-build
sha256sum firmware/releases/*.bin
```

- [ ] **Step 4: Confirm the acceptance criteria**

Against the spec: 51 frames at 2 ms; soak within bounds; `l3track` under 1.0 s; `l3sparse` truncation not worse; tests and lint green; firmware builds. State each as pass or fail with its evidence. Do not report completion on any criterion you did not run.

- [ ] **Step 5: Commit any documentation updates**

```bash
git add -A && git commit -m "docs(iwr6843): record the 51-frame validation results"
```
