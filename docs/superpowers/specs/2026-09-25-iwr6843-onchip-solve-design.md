# IWR6843: On-Chip Solve On The C674x DSP

**Date:** 2026-09-25
**Branch:** `feat/iwr-calcs`
**Status:** Design approved, pending implementation plan

## Goal

Move the launch-angle and club-path solve from the Raspberry Pi onto the
IWR6843's C674x DSP core, so that a normal shot transfers results rather than
raw IQ cells.

## Why

Selective readback is ~0.7 s per shot today (`l3track`, ~180 cells at 388 B).
That cost exists only because the Pi needs the samples. If the solve runs
where the samples already are, the shot path transfers a handful of floats and
the cell budget, the `l3sparse` 768-byte request cap, and the readback timeout
all stop binding.

## Non-Goals

- Changing the LCMF-v1 algorithm. This is a port, not a redesign.
- Removing the Python implementation. It remains the reference, the porting
  oracle, and the replay/tuning engine.
- Extending the capture movie. That is
  `2026-09-25-iwr6843-longer-movie-design.md`.

## Current State

The C674x is idle and deliberately excluded from the toolchain:

- `firmware/Makefile:108` installs the SDK with
  `--disable-components TI_CGT_C6000,...,DSPLIB_C674x,MATHLIB_C674x,...`
- `docs/development/firmware.md:261` states the application is MSS/R4F-only and
  "does not require the C674x DSP compiler"
- `firmware/iwr6843/makefile:97-99` builds a meta-image without a DSS image

The production solve path, from `runtime.py` imports, is ~2,700 lines of numpy:

| Module | Lines | Role |
|---|---:|---|
| `lcmf.py` | 962 | LCMF-v1 launch angle |
| `club.py` | 946 | Club path and club gating |
| `tracking.py` | 425 | Ball detection and track fitting |
| `late_window.py` | 366 | Late-flight window selection |

Numerically this is tractable for a DSP. A scan for heavy linear algebra found
only `np.fft.fft` (4 sites in `lcmf.py`). No SVD, `lstsq`, or `pinv`.
`np.linalg.eigh` appears only in `music.py`, which the production runtime does
not import.

## Precedent

`firmware/iwr6843/track_select.c` (507 lines) already ports the host cell
planner to pure C99, with Python as the stated reference and
`tests/test_iwr6843_track_select.py` compiling it with the host compiler to
assert it names the same cells. This project is the same pattern at roughly
five times the scale.

## Approach

Add a DSS image running the ported solve. The DSS reads capture data from
shared L3 directly, so no on-chip transfer is needed; MSS and DSS communicate
results over the existing mailbox driver. The MSS keeps CLI, UART, capture
control, and the self-trigger.

### Decisions

| # | Decision | Choice |
|---|---|---|
| 17 | Scope | Full on-chip solve on the DSS: `lcmf`, `club`, `tracking`, `late_window`. |
| 18 | Sequencing | The DSS memory question is confirmed in the movie-extension project's Phase 0, before its memory layout is fixed. |
| 22 | DSS memory | The DSS reads the capture arena **in place** from L3 and caches it in the C674x L2; it reserves no additional L3. Solve intermediates live in L2 SRAM. The L2 SRAM/cache split is set in Phase 0 and is the only tunable here. |
| 19 | Verification | Per-stage golden vectors with stated tolerance bands, via the `track_select.c` ctypes pattern, plus end-to-end MAE against the 59 TrackMan-matched shots. |
| 20 | Failure path | On-chip solve first; on no-confidence, fall back to cell transfer and the Python solve, mirroring the existing `l3track` -> `l3sparse` -> `l3dump` tiering. |
| 21 | Raw transfer | Debug-mode-only. Normal play transfers results; raw cell transfer stays available behind `save_dumps` (`monitor.py:233`) for calibration, tuning, and replay. |

### Why Per-Stage Verification Matters Here

The algorithms are under active development: `club.py` and `late_window.py`
were modified 2026-09-25, `lcmf.py` on 2026-08-25. A full port puts every
future tuning change behind a C rewrite and a firmware flash. The verification
harness is therefore not just a correctness gate but the mechanism that keeps
porting a tuning change cheap: a per-stage golden-vector suite localises drift
to the stage that caused it, instead of surfacing it as a wrong final angle
across 2,700 lines.

## Phasing

**Phase 0 - Feasibility**

1. Re-enable `TI_CGT_C6000`, `DSPLIB_C674x`, `MATHLIB_C674x` in the SDK
   install; rebuild the Docker image. Confirm a trivial DSS image links and
   boots alongside the existing MSS image.
2. Memory: configure the L2 SRAM/cache split, confirm the DSS reads the capture
   arena in place from L3 with no resident L3 buffer of its own, and confirm
   the solve intermediates fit L2 SRAM. Feeds the movie-extension Phase 0 gate.
3. Compute probe: port one representative stage (the `lcmf.py` FFT path) and
   measure it on-chip against the 1 s budget.

   *Gate:* if a single stage cannot meet its share of the budget, or the DSS
   demands enough L3 to undercut the movie extension, re-scope to the hybrid
   option (on-chip numerics, host decision logic) before porting further.

### Phase 0 Findings (Task 4: Compute Probe)

**What this task did.** Ported the window+FFT primitive behind
`lcmf.py:502` (`_prepared_fft`) and `lcmf.py:547` (`_fast_design`) -- both
apply `np.hanning` then `np.fft.fft(..., n=n_fft, axis=-1)` -- to
`firmware/iwr6843/solve/solve_fft.{c,h}`. Wired it into the DSS build
(`firmware/iwr6843/dss/makefile`), which now also links TI's DSPLIB
(`dsplib.ae674`) and defines `SOLVE_USE_DSPLIB` for that build only.

**The seam.** `solve_fft.c` splits into a portable part (windowing,
zero-padding, per-row iteration -- pure C99, host-buildable) and a raw
N-point complex DFT behind `solve_fft_transform()`, selected at compile
time by the `SOLVE_USE_DSPLIB` macro: a double-precision reference
Cooley-Tukey radix-2 DFT when undefined (every host build, including the
pytest harness), or TI's `DSPF_sp_fftSPxSP` when defined (the DSS build
only). This exists because DSPLIB is C674x/cl6x-only and the harness
builds solve sources with the host compiler for ctypes -- a `solve_fft.c`
that called DSPLIB directly would have no host build and no equivalence
test at all.

**What is verified and what is not.** `tests/test_iwr6843_solve_fft.py`
compiles `solve_fft.c` with the host compiler and checks the windowing +
zero-pad + reference DFT against `np.fft.fft` to 1e-5 relative-to-peak on
the magnitude spectrum (see that test file for why relative-to-peak rather
than strict per-bin relative). That test exercises ONLY the portable
reference path. `solve_fft_transform_dsplib()` -- the actual DSPLIB call
that will run on the DSS -- is exercised by nothing in this repository. It
is unverified until it runs against a known input on the C674x and is
checked against the Python reference there. A green host test run is
evidence the port's algorithm (window, pad, per-row iteration) is correct;
it is not evidence the DSPLIB FFT call is correct.

**Tolerance warning for Tasks 5-8 (added in the pre-Task-5 hardening
round):** 1e-5 relative-to-peak is the right metric for *this* test because
Task 4's only job is proving the FFT primitive itself, and it deliberately
does not care about low-magnitude bins. It is not automatically the right
metric for a later stage. `lcmf`'s angle fit reads spectral structure, and
the bins carrying angle information are often exactly the low-magnitude
ones near a null, not the peak -- a relative-to-peak metric can hide a
large relative error in one of those bins while still passing comfortably.
Whichever task ports `lcmf` must justify its own equivalence metric against
what that stage actually needs to preserve, rather than reusing "1e-5
relative to peak" because that is what Task 4 used. See the same note in
`tests/test_iwr6843_solve_fft.py::_assert_magnitude_close`.

**On-chip measurement: PARKED, not run.** Step 4 of the Task 4 brief (add a
temporary `l3fft` CLI command, flash it, and measure elapsed microseconds
on real silicon) requires hardware access this session did not have. No
timing number is recorded here, and none should be inferred from the build
succeeding -- a working build says the code compiles and links, not that it
runs correctly or how fast. Consequently **the Phase 0 gate in this section
cannot yet be evaluated**: whether this stage (or the full solve) fits the
1 s budget remains open. This is the first thing to do with bench access
before porting `tracking`, `lcmf`, `late_window`, or `club` (Tasks 5-8).

**DSS build: compiles and links for the C674x.** Verified via
`MSYS_NO_PATHCONV=1 docker run ... make -C firmware build-native` (the
container's `openflight-iwr-sdk:latest` image already had `DSPLIB_C674x`
installed from an earlier task). Both `l3_dump_mss.xer4f` (MSS) and
`l3_dump_dss.xe674` (DSS, now including `solve_fft.oe674`) built and linked
cleanly against `dsplib.ae674`, and the three-image meta-image
(`l3_dump.bin`) still assembles. `solve_fft.c` also compiles clean under
the host build's `-std=c99 -O2 -Wall -Wextra -Werror` used by the pytest
harness. The portable reference path was additionally spot-checked outside
pytest (no host compiler exists on this Windows box) by compiling and
running two small C drivers with the container's `gcc` against vectors
computed by the Python reference: an 8-row, 128-sample, 512-point case
matched `np.fft.fft` to a worst relative-to-peak delta of ~5.1e-8, and a
2-row, 5-sample, 8-point case matched to ~7.5e-8 absolute -- both far
inside the 1e-5 tolerance the pytest suite enforces (that suite itself
skips cleanly on this host and will run for real in CI, which has a host
compiler).

**MSS image: unchanged.** `firmware/iwr6843/l3_dump_mss.map` (gitignored
build output) still shows the Task-2 invariant rows exactly:
`L3_RAM` used/unused `000c0000`/`00000000` and `DATA_RAM` used/unused
`0002ae2d`/`000051d3`. Expected, since this task touched only the DSS side
(`firmware/iwr6843/dss/makefile` and the new `../solve/solve_fft.c`) --
recorded here as confirmation, not because the change plausibly could have
moved it. (Per the Task 4 instructions, the printed "Binary CRC32" is
deliberately NOT used as the invariant: it covers the whole three-image
flash blob and legitimately differs run to run.)

**DSS L3/L2: still no resident L3 buffer; the Task 2 split holds.** The DSS
map's `L3SRAM` row reads used/unused `00000000`/`000c0000` -- no section
placed in L3, unchanged from Task 2/3. Adding `solve_fft.c` grew L2 SRAM
usage as expected but nowhere near the budget: `L2SRAM_UMAP0` used
`0x00014cb8` (~83.9 KiB) of `0x00020000` (128 KiB, `0x8000`/32 KiB of which
is the `.cacheReserve` carve-out from Task 2 -- so ~83.9 KiB of the
remaining ~96 KiB SRAM), and `L2SRAM_UMAP1` used `0x0000e000` (~56 KiB) of
its own `0x00020000`. The Task 2 32 KB-cache/rest-SRAM split (`dss.cfg`,
`dss_linker.cmd`) is unchanged and remains adequate for this stage; nothing
here required moving it.

**FIXED (pre-Task-5 hardening round, see `.superpowers/sdd/2026-09-25-iwr6843-onchip-solve/task-4-report.md`
for the full writeup): `dss_solveTask`'s stack was 4 KiB.** The item
recorded below is what that round closed, kept here verbatim as the
original finding:

`dss_main.c`'s `dss_solveTask` is created with a 4 KiB stack
(`taskParams.stackSize = 4 * 1024`, set in Task 2 before any solve stage
existed). `solve_fft_apply`'s per-row locals alone are two
`double[SOLVE_FFT_MAX_N]` arrays (8 KiB), and `solve_fft_transform_dsplib`
nests three more `float[2*SOLVE_FFT_MAX_N]` arrays (12 KiB) on top of that
when it is called -- comfortably more than 4 KiB before counting anything
else on the call stack. This is inert today because nothing in `dss_main.c`
calls into `solve_fft_apply` yet (Task 4 ported and build-wired the stage;
wiring it into the mailbox dispatch is Task 6's job per the recipe). It
will not stay inert once a solve stage is actually invoked from a BIOS
task: `dss_solveTask`'s stack size needs raising (or these buffers need to
move off the call stack) before then, and should be checked again once the
`l3fft` measurement step actually runs.

The fix: a dedicated 32 KiB static stack buffer for `dss_solveTask`
(`dss_solveTaskStack[]` in `dss_main.c`, passed via `taskParams.stack`/
`stackSize` rather than left for `Task_create` to allocate from the 32 KiB
`systemHeap` -- doing that would have tried to hand the entire heap to one
task's stack). 32 KiB carries ~10 KiB of headroom over the ~22.4 KiB derived
(not silicon-measured) worst case for the FFT stage, and the resulting DSS
L2 usage still leaves ~84.8 KiB of headroom under the 229,376 B
`.cacheReserve` ceiling. Tasks 5-8 must re-derive this number for their own
stack-resident buffers before landing -- see the stack-size comment in
`dss_main.c` and the porting recipe in the Task 4 report.

**Phase 1 - Infrastructure**

4. DSS build: RTSC config, linker command file, three-image meta-image.
5. MSS-DSS mailbox protocol: a request naming the frozen capture, a result
   record, and a no-confidence status.
6. Golden-vector harness: recorded per-stage inputs and outputs extracted from
   existing sessions, with the ctypes comparison scaffolding.

**Phase 2 - Port, stage by stage** (each stage lands with its golden-vector
test, and Python stays the reference)

7. `tracking` - ball detection and track fit.
8. `lcmf` - LCMF-v1 launch angle.
9. `late_window` - late-flight window selection.
10. `club` - club path and gating.

**Phase 3 - Integration**

11. Result-only shot path, with the cell-transfer fallback on no-confidence.
12. `save_dumps` gated raw transfer for calibration and tuning sessions.

**Phase 4 - Validation**

13. Shadow comparison over a range session: on-chip result versus Python result
    per shot.
14. End-to-end MAE against the TrackMan corpus.

## Testing Strategy

- **Per-stage (`ctypes`):** each ported stage compiled with the host compiler
  and fed recorded inputs, asserting outputs within a stated tolerance. Extends
  the `test_iwr6843_track_select.py` pattern.
- **Tolerance policy:** stated per stage, not global. Floating point will not
  be bit-identical across x86, R4F, and C674x; the spec for each stage names
  the tolerance and the reason.
- **End-to-end:** on-chip angle within the existing 0.86 degree MAE envelope
  across the 59 matched 9-iron and 7-iron shots.
- **Fallback:** a forced no-confidence result must produce the same shot output
  as today's Python path.
- **Regression:** the Python reference keeps its existing tests unchanged; the
  port never edits the reference to make the C agree.

## Acceptance Criteria

1. A normal shot transfers results only, with no raw cell transfer.
2. On-chip launch angle matches the Python reference within the stated
   per-stage tolerances, and end-to-end MAE does not regress against the
   TrackMan corpus.
3. The no-confidence fallback produces today's behaviour.
4. `save_dumps` sessions still capture raw cells and replay correctly through
   `replay.py`.
5. The 2 ms capture cadence is unaffected: `hwa_missed` and `iq8_overrun` stay
   at or below their current bounds with the DSS running.
6. `uv run pytest tests/ -v` passes and
   `uv run pylint src/openflight/ --fail-under=9` holds.

## Risks And Open Questions

| Risk | Handling |
|---|---|
| ~2,700 lines of numeric port is roughly 5x the `track_select.c` precedent | Staged per-module porting, each with its own golden-vector gate |
| Active algorithm development moves behind a C rewrite plus firmware flash | Per-stage harness to keep re-porting cheap; Python stays the reference and the replay engine |
| DSS may need L3, competing with the movie extension | Resolved by decision 22: reads in place, caches in L2, reserves no L3. Confirmed in Phase 0 once a DSS image exists. |
| Caching the capture arena from the DSS adds L3 read traffic while the HWA and EDMAs are writing it | Acceptance criterion 5. The solve runs post-freeze, when capture is halted, so the overlap should be limited to the rearm boundary. |
| Adding a running DSS core may perturb the 380 us inter-frame budget through shared-bus contention | Acceptance criterion 5; measured with the existing soak harness |
| C674x L2 capacity and the SRAM/cache split are assumed from the device family, not verified from this repo | Phase 0 step 2 sets and confirms the split |
| Re-enabling the C6000 toolchain changes install size and Docker build time | Phase 0 step 1 establishes the real cost before any porting |
| Numeric divergence may be legitimate rather than a bug (different FP ordering) | Tolerances stated per stage with reasons, not a single global epsilon |

## Prerequisites

- SDK reinstall with the C6000 compiler and DSP libraries enabled, and a
  rebuilt Docker image.
- A corpus of recorded sessions sufficient to extract per-stage golden vectors.
- Bench access to an IWR6843, and range access for Phase 4.
