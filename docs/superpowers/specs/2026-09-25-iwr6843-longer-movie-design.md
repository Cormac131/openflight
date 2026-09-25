# IWR6843: Longer Capture Movie At 2 ms

**Date:** 2026-09-25
**Branch:** `feat/iwr-calcs`
**Status:** Design approved, pending implementation plan

## Goal

Extend the IWR6843 dense capture movie from 45 frames (90 ms) to 51 frames
(102 ms) while preserving the validated 2 ms frame cadence.

## Non-Goals

- Raising the frame rate to 1 ms. The RF geometry forbids it: 3 TX x 12 loops
  x 45 us/chirp = 1.62 ms of chirping, which does not fit a 1 ms frame.
- Reducing readback time. The selective readback is already under 1 s; this
  project must not regress it (see Readback Model).
- Changing the LCMF solve, the host parser wire format, or the K-LD7 path.

## Background

The capture path is HWA-driven, not DSP-driven. `firmware/Makefile:148` builds
an MSS-only image; the C674x DSS core is never loaded. The DFE triggers range
FFTs in the HWA, HWA param completion triggers EDMA copies of selected range
bins into L3, and the R4F only re-arms the chain between frames
(`l3_dump.c:12-18`).

L3 is organised as a hybrid buffer, not a uniform ring
(`l3_buildCapturePlan`, `l3_dump.c:596-780`):

- `preFrames` slots form a true circular ring
  (`gPreFramesCaptured % gCapturePlan.preFrames`, `l3_dump.c:1283`)
- impact and ball frames are a linear, one-shot region written after trigger

## The Controlling Constraint

L3 is at 99.8% capacity.

| Quantity | Value | Source |
|---|---:|---|
| `L3_RAM` region | 786,432 B | `l3_dump_mss.map:18` (`.l3ring` uses 100%) |
| IQ16 ping/pong scratch inside L3 | 98,304 B | `l3_dump.c:212-216` |
| Usable IQ8 arena | 688,128 B | `L3_IQ8_CAPTURE_BYTES` |
| Bytes/frame (36 chirps x 4 RX x 53 bins x 2 B) | 15,264 B | `l3_dump.c:647-649` |
| Current 45-frame profile | 686,880 B | **1,248 B spare** |

Extending the movie is therefore purely a byte-budget problem.

## Approach

Reclaim the 98,304 B the IQ16 scratch occupies inside L3 by relocating the
scratch to `DATA_RAM` (TCMB), which has 119,687 B unused
(`l3_dump_mss.map:17`). This yields a full 786,432 B capture arena.

| Lever | Frees | Result |
|---|---:|---|
| Relocate scratch to DATA_RAM | 98,304 B | 786,432 B arena, **51 frames / 102 ms** |
| Spare L3 bank, if any exists | 131,072 B/bank | +8.6 frames/bank (probe first) |
| Narrow pre-phase bins 53 to 32 | 48,384 B | +3 frames (deferred, opt-in) |

Frame allocation at 51 frames: 8 pre + 10 impact + 33 ball. The ball phase
grows from 27 to 33 frames, i.e. 54 ms to 66 ms.

## Related Work

`2026-09-25-iwr6843-onchip-solve-design.md` moves the launch-angle and
club-path solve onto the C674x DSP. If it lands, the readback constraints
below stop binding, because normal play transfers results rather than cells.
The L3 capacity work in this document survives either way: movie length still
bounds how long a record can be analysed. The two projects contend for the
same L3, which is why Phase 0 probes the DSS memory question.

## Readback Model

Readback is three-tier (`monitor.py:485-516`), and the full ring dump is the
last-resort fallback, not the shot path:

1. `l3track` - the firmware runs the tracker and cell selection on-chip and
   streams ILT1 + ILS1 cells with no host round trip (`l3_cli_track`,
   `l3_dump.c:3344`). Cells come from a per-frame 64-bit `cellMask`, so the
   count is bounded by the track, not by a request string.
2. `l3sparse` - the host plans cells and requests them by name. Bounded by
   `L3_SPARSE_REQUEST_MAX = 768` bytes (`dump_format.h:55`), giving at most 192
   cells (`cellFrames[L3_SPARSE_REQUEST_MAX / 4U]`, `l3_dump.c:3230`) and fewer
   once the ASCII encoding is counted.
3. `l3dump` - the whole ring, 686,880 B at 45 frames. Diagnostic only.

One ILS1 cell is `4 + n_loops * 2 * n_rx * 4` bytes = **388 B** at 12 loops and
4 RX (`slice_stride`, `sparse.py:69`). At ~104 kB/s, a sub-1 s readback allows
roughly **268 cells**.

Extending the movie scales cell count with frames, not with L3 size: 45 to 51
frames is ~13% more frames for a track-following selection, so a ~0.7 s
readback becomes ~0.8 s. It does not approach the full-dump figure.

## Budgets

- **Selective readback: stays under 1.0 s.** This is a hard outcome. The
  budget is a *cell* budget, not a movie-length budget: at 388 B/cell, the
  ceiling is ~268 cells, and the design must cap cells-per-frame rather than
  frames.
- **`l3sparse` request headroom:** the 768-byte request cap does not grow with
  the movie, so more frames means more cells competing for the same request
  string and a higher chance of `truncated` (`monitor.py:510`).
- **Full `l3dump`:** grows from 6.59 s to 7.47 s. Acceptable, because it is a
  diagnostic fallback rather than the shot path.
- **TCMB margin:** a static scratch of 98,304 B leaves 21,383 B of DATA_RAM.
- **Inter-frame budget:** 2000 us frame minus 1620 us chirping = **380 us** for
  re-arm and IQ8 packing. No change may exceed it.
- **Frame cap:** `L3_MAX_CAPTURE_FRAMES = 64` (`l3_dump.c:181`) is not binding
  at 51 or 60 frames.

## Decisions

| # | Decision | Choice |
|---|---|---|
| 1/6 | IQ16 scratch | Statically-sized array relocated to `DATA_RAM` in its own `DATA_SECTION`; replaces the `g_iq16FrameScratch` offset-cast macro (3 call sites: `l3_dump.c:1655`, `:1738`, `:1875`). Right-sizing is dropped: once the buffer leaves L3 it no longer buys movie length. |
| 2 | `L3_TOTAL_BYTES` | Probe a higher `MMWAVE_L3RAM_NUM_BANK`; regardless of outcome, derive the constant from a linker-exported symbol so `l3_dump.c:182` cannot drift from the linker. |
| 3/11 | Pre-phase bins | Deferred. 53-bin dense stays the default; a narrowed profile ships opt-in, promoted only after on-range trigger validation. |
| 4/16 | Readback | Selective readback (`l3track`) must stay under 1.0 s. Enforced as a cell budget (~268 cells at 388 B), not as a frame-count cap. The full `l3dump` fallback moving 6.59 s to 7.47 s is accepted. |
| 5 | Freeze predicate | Extract one `l3_shouldFreezeNow()` used by all three sites (`l3_dump.c:1173`, `:1188`, `:2217`), documented as "caller holds the critical section". |
| 7 | Budget test | Keep an explicit expected literal AND cross-check it against constants parsed from `l3_dump.c`. |
| 8 | Dead variants | Delete `LIVE_SNAPSHOT_RING` and the non-`CONFIGURABLE_CAPTURE` paths (~60 refs) as a standalone build-verified commit, before the functional work. |
| 9 | Plan builder | Extract `l3_buildCapturePlan` into standalone C99, test via `ctypes` (the `detect_queue.c` pattern), and property-test the overlap/capacity invariant. |
| 10 | Layout invariant | `_Static_assert` on scratch size, plus a test parsing `MEMORY CONFIGURATION` from `l3_dump_mss.map` asserting a minimum DATA_RAM margin. Must skip loudly when no map exists. |
| 12 | Acceptance gate | Scripted hardware soak reading `stats`; fails if `hwa_missed` / `iq8_overrun` / EDMA errors exceed a stated bound. |
| 14 | Adaptive IQ8 scale | Enable `L3_IQ8_SPARSE_SCALE` last, measured as a separate delta. Droppable if the 380 us budget will not take it alongside the relocation. |
| 15 | Host timeout | `monitor.py:599` (12.0 s) is not under pressure from the selective path. Raise it only enough to cover the `l3dump` fallback at the 64-frame ceiling, and add a test asserting every shipped profile leaves a stated margin. Sweep the read path for other timeouts. |

## Phasing

Phase 0 is a gate. Its outcome can invalidate the rest of the plan.

**Phase 0 - Probes (throwaway, no code kept)**

1. Bank probe: build at a higher `MMWAVE_L3RAM_NUM_BANK`, read the map
   `MEMORY CONFIGURATION` block. Determines whether extra L3 exists.
2. TCMB spike: build with the scratch relocated, run the soak, read
   `hwa_missed` / `iq8_overrun` / `iq8_waits` (`l3_dump.c:3569`).
3. DSS memory confirmation: the on-chip solve
   (`2026-09-25-iwr6843-onchip-solve-design.md`) reads the capture arena in
   place from L3 and caches it in the C674x L2, reserving **no additional
   L3**. Confirm this holds once a DSS image exists, so the capture arena can
   claim all 786,432 B.

*Gate:* if EDMA-to-TCM contention breaks the 380 us budget, the scratch stays
in L3, the gain drops from +6 frames to +2.4 (right-sizing only), and the
target movie length must be revised before proceeding. If the DSS turns out to
need a resident L3 buffer after all, the capture arena must be sized around
that reservation.

**Phase 1 - Behaviour-preserving refactors** (each build-verified, own commit)

3. Delete dead build variants.
4. Extract `l3_shouldFreezeNow()`.
5. Extract the plan builder into host-testable C99.

**Phase 2 - Functional change**

6. Relocate the scratch; derive `L3_TOTAL_BYTES` from the linker symbol.
7. Extend the dense profile to 51 frames.

**Phase 3 - Guards**

8. Budget test with parsed cross-check; map-margin test; host timeout and
   margin test; property tests for the plan builder.
9. Hardware soak harness.

**Phase 4 - Optional, droppable**

10. Adaptive per-frame IQ8 scale, measured as a separate delta.
11. Narrowed pre-phase profile, opt-in, pending range validation.

## Testing Strategy

The existing firmware suite is source-text assertion over `l3_dump.c`; only
`tests/test_iwr6843_detect_queue.py` executes code. For a memory-layout change
that is the weakest possible guard, so this project adds executable coverage:

- **Plan builder (`ctypes`):** capacity rejection, offset monotonicity, phase
  non-overlap, `usedBytes <= capacity`, per-phase bin counts, the odd-loops and
  `L3_MIN_LOOPS` guards.
- **Property test:** for any valid plan, no two frame byte ranges overlap and
  none exceeds capacity.
- **Layout:** `_Static_assert` plus map-file DATA_RAM margin.
- **Budget:** expected literal cross-checked against parsed `#define`s.
- **Host:** every shipped profile computed dump time leaves the stated margin
  under the `monitor.py` deadline.
- **Hardware (acceptance):** soak reporting `hwa_missed`, `iq8_overrun`,
  `iq8_waits`, EDMA errors.

## Acceptance Criteria

1. The dense profile records 51 frames at 2 ms (102 ms movie).
2. The hardware soak shows `hwa_missed` and `iq8_overrun` at or below the
   stated bound, no worse than the current 0.0089% miss rate.
3. Selective `l3track` readback stays under 1.0 s at 51 frames, measured on
   hardware rather than computed.
4. `l3sparse` truncation (`monitor.py:510`) does not become more frequent at
   51 frames than at 45.
5. `uv run pytest tests/ -v` passes and
   `uv run pylint src/openflight/ --fail-under=9` holds.
6. The firmware builds via `make -C firmware docker-build`.

## Risks And Open Questions

| Risk | Handling |
|---|---|
| EDMA-to-TCM bandwidth is unquantified; it is the largest unknown in the plan | Phase 0 gate: spike before committing |
| `l3_dump.c:1170` sets `gHwaRearmPending` unconditionally in the IQ8 arm, unlike the other two copies | Recorded as a finding; resolve with a targeted test before collapsing the bodies. Do not guess in ISR-adjacent state. |
| Fixed `iq8Scale 128` may under-resolve the weak late-flight frames this project adds | Phase 4 adaptive scale. Inferred from the architecture and the physics, not from measured late-frame SNR. |
| 21,383 B of TCMB margin could be silently eroded by stack or heap growth | Map-margin test |
| Dead-variant deletion is unverifiable without a working build | `TI_ROOT` is unset; Docker 28.5.1 is present. Phase 1 requires `make -C firmware docker-image` first, with a first-run cost to fetch TI installers. |
| Other host read-path timeouts may exist beyond `monitor.py:599` | Spec requires a sweep, not an assumption |
| More frames means more cells competing for the fixed 768-byte `l3sparse` request, raising truncation risk on the fallback path | Acceptance criterion 4; if truncation worsens, the request encoding or cap needs revisiting as separate work |
| Cell count on the `l3track` path scales with frame count, so the sub-1 s outcome erodes as the movie grows | Measured, not computed, at 51 frames. The budget is ~268 cells; cap cells-per-frame if approached. |
| L3 bank headroom beyond the 6 banks (768 KB) currently allocated was unknown | Probed: building with `MMWAVE_L3RAM_NUM_BANK=8` succeeds and the linker map reports `L3_RAM 51000000 00100000 000c0000 00040000` — a 1 MB (8-bank) region vs. the baseline 768 KB (6-bank) `000c0000`, with `DATA_RAM`, `HWA_RAM`, `HS_RAM`, and `PROG_RAM` all unchanged. Spare L3 exists (256 KB / 2 banks free at 8, not taken from another region); this is a floor-not-ceiling finding for a follow-up, and the 51-frame target in this plan is unchanged. |

## Prerequisites

- Working firmware toolchain via the container path
  (`make -C firmware docker-image`, then `docker-build`).
- Bench access to an IWR6843 for the Phase 0 spike and the Phase 3 soak.
