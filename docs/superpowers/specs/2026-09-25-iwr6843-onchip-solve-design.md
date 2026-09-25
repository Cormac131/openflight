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
| 18 | Sequencing | The DSS memory question is probed in the movie-extension project's Phase 0, before its memory layout is fixed. |
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
2. Memory probe: does the solve's working set fit the C674x L2, or does it need
   L3? Feeds the movie-extension project's Phase 0 gate.
3. Compute probe: port one representative stage (the `lcmf.py` FFT path) and
   measure it on-chip against the 1 s budget.

   *Gate:* if a single stage cannot meet its share of the budget, or the DSS
   demands enough L3 to undercut the movie extension, re-scope to the hybrid
   option (on-chip numerics, host decision logic) before porting further.

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
| DSS may need L3, competing with the movie extension | Probed in Phase 0 of both projects before either fixes a layout |
| Adding a running DSS core may perturb the 380 us inter-frame budget through shared-bus contention | Acceptance criterion 5; measured with the existing soak harness |
| C674x L2 capacity for the working set is assumed, not verified from this repo | Phase 0 memory probe |
| Re-enabling the C6000 toolchain changes install size and Docker build time | Phase 0 step 1 establishes the real cost before any porting |
| Numeric divergence may be legitimate rather than a bug (different FP ordering) | Tolerances stated per stage with reasons, not a single global epsilon |

## Prerequisites

- SDK reinstall with the C6000 compiler and DSP libraries enabled, and a
  rebuilt Docker image.
- A corpus of recorded sessions sufficient to extract per-stage golden vectors.
- Bench access to an IWR6843, and range access for Phase 4.
