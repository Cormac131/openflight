# IWR6843 Impact Back-Interpolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish impact from the clean club and ball tracks either side of the cluttered tee band — predicted in real time from the approaching club, refined after the capture from club-in, club-out and ball-out — instead of detecting it inside the band.

**Architecture:** A tiny `l3_band` module drops in-band targets from each frame's target list before any tracker sees them, so the club track, its follow-through, the ball track and the joint search all avoid the ridge with no struct changes; the ball tracker is armed at the band's far edge. A new pure-C `l3_impact_fit` fits each track (range vs time, least squares), solves the time it passes the ball's range with an uncertainty, applies physics checks and fuses the estimates with an agreement verdict. A second `l3_impact_t` instance fires in real time on the club-in estimate. The board (`l3_dump.c`), the host replay (`firmware_replay.py`), the result packet (v2) and the dump viewer carry it; an evaluator section compares it against a joint-fit comparator.

**Tech Stack:** C99 (TI R4F firmware, host-built with the zig/cc compiler via `firmware_host.py`), Python 3.11 with ctypes, pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-09-28-iwr-impact-back-interpolation-design.md`

## Global Constraints

- Always run Python through `uv run` (`uv run pytest …`, `uv run pylint …`, `uv run ruff …`).
- Pure-C modules include no TI headers; they are listed in `firmware/iwr6843/makefile` `SOURCES` **and** `src/openflight/iwr6843/firmware_host.py` `HOST_SOURCES`.
- Every ctypes mirror in `firmware_host.py` matches its C struct field for field, in order; every new C function gets a `_SIGNATURES` entry.
- Bins are GLOBAL range-FFT bins; toward the ball (downrange) is a rising bin; bin width `6.0 / 128` m.
- Defaults (verbatim from the spec): `bandBins = 6`, `K = 4` fit points, minimum 3; club-in 10–70 m/s; club-out ≤ club-in × 1.10; ball-out 15–90 m/s and faster than club-out; gate `3σ` with a `0.5 ms` floor; σ floor `binWidth / √12 / |v|`.
- Verdicts: `consistent`, `single_track`, `inconsistent`, `none`. Per-track whys: `ok`, `missing`, `few_points`, `wrong_direction`, `speed_bounds`, `physics`, `nonfinite`, `dropped`.
- Every fit uses each point's own `timestampUs`, never frame numbers.
- Lint: `uv run pylint src/openflight/ --fail-under=9`; `uv run ruff check` and `uv run ruff format --check` on touched Python files.
- The full test suite has 56 pre-existing failures/errors on this Windows machine (camera, desktop launcher, geekworm, memory layout, live selector, compact_iq16, …). A task is green when its own tests pass and that set does not grow: compare with `uv run pytest tests/ -q -p no:cacheprovider 2>&1 | grep -E "^(FAILED|ERROR)" | sed 's/ - .*//' | sort`.

## Review Focus

1. **Ball tracker starved by the band** — filtering in-band targets removes the ball's first departing points; a user expects the ball still to be acquired. Pinned in Task 5 (`test_ball_track_armed_at_band_edge_acquires_a_departing_ball`).
2. **Club sits inside the band at the first frame** (trigger late, club first seen in the band) — expect no club-in estimate, not a crash or a bogus one. Pinned in Task 3 (`test_club_in_missing_uses_the_outgoing_tracks`) and Task 1 (`test_filter_removes_every_in_band_target_and_keeps_order`).
3. **Two points at the same timestamp** (strided or duplicated frames) — the fit must not divide by zero. Pinned in Task 2 (`test_points_that_do_not_spread_in_time_are_nonfinite`).
4. **Ball return weak, only the club either side** — expect a `consistent` two-track verdict from club-in and club-out. Pinned in Task 3 (`test_ball_missing_fuses_the_club_either_side`).
5. **Old firmware sending a v1 result packet to new Pi code** — expect it still parsed, with no impact fit. Pinned in Task 7 (`test_v1_packet_still_parses_without_an_impact_fit`).

---

## File Structure

| File | Responsibility |
|---|---|
| `firmware/iwr6843/l3_band.h/.c` (new) | The tee band: build around a bin, test membership, filter a target list in place |
| `firmware/iwr6843/l3_impact_fit.h/.c` (new) | Per-track range-vs-time fit and crossing, physics checks, fusion, verdict, point readers, text format |
| `firmware/iwr6843/l3_impact.h/.c` | + `l3_impact_update_range`: real-time fire on a club-in estimate |
| `firmware/iwr6843/l3_shot.h/.c` | + `L3_SHOT_IMPACT_RANGE` source bit, `rangeFired` input |
| `firmware/iwr6843/l3_result.h/.c` | Packet v2: the impact fit appended; `l3_result_build` takes the fit |
| `firmware/iwr6843/l3_dump.c` | Board wiring: band filter, range impact, ball armed at band edge, fit at SOLVE |
| `firmware/iwr6843/makefile` | + `l3_band.c l3_impact_fit.c` |
| `src/openflight/iwr6843/firmware_host.py` | ctypes mirrors, constants, signatures, `HOST_SOURCES` |
| `src/openflight/iwr6843/firmware_replay.py` | Replay mirror of the board wiring; `ImpactFitSummary` on `ReplayResult` |
| `src/openflight/iwr6843/shot_result.py` | Parse packet v1 and v2 |
| `src/openflight/iwr6843/dump_viewer.py`, `scripts/iwr6843/dump_viewer.html` | Band, fitted lines, impact time |
| `src/openflight/iwr6843/impact_eval.py` (new) | Method-C comparator and impact metrics (pure Python) |
| `scripts/analysis/evaluate_iwr_tracking.py` | `--impact` section |
| Tests: `tests/test_iwr6843_firmware_band.py`, `tests/test_iwr6843_firmware_impact_fit.py`, `tests/test_iwr6843_impact_eval.py` (new); edits to `tests/test_iwr6843_firmware_result.py`, `tests/test_iwr6843_firmware_replay.py`, `tests/test_iwr6843_firmware_shot.py` (or wherever `l3_shot_update` is tested — `grep -ln l3_shot_update tests/`) |

---

### Task 1: `l3_band` — the tee band and target filter

**Files:**
- Create: `firmware/iwr6843/l3_band.h`, `firmware/iwr6843/l3_band.c`
- Modify: `firmware/iwr6843/makefile` (SOURCES line), `src/openflight/iwr6843/firmware_host.py` (HOST_SOURCES, `Band` mirror, signatures)
- Test: `tests/test_iwr6843_firmware_band.py`

**Interfaces:**
- Produces (C): `typedef struct { uint8_t valid; float loBin; float hiBin; } l3_band_t;`
  `void l3_band_around(float centreBin, float halfWidthBins, l3_band_t *out);`
  `int32_t l3_band_contains(const l3_band_t *band, float bin);`
  `uint32_t l3_band_filter(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n);`
- Produces (Python): `fw.Band` ctypes structure with fields `valid`, `loBin`, `hiBin`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the tee band, firmware/iwr6843/l3_band.c."""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def band(lib, centre: float, half: float) -> fw.Band:
    out = fw.Band()
    lib.l3_band_around(centre, half, ctypes.byref(out))
    return out


def targets(*bins: float):
    arr = (fw.TargetObs * len(bins))()
    for i, b in enumerate(bins):
        arr[i].rangeBin = b
        arr[i].snr = 100.0 - i  # strongest first, as l3_obs_extract ranks them
    return arr


def test_band_spans_half_width_either_side_of_the_centre(lib):
    b = band(lib, 47.0, 6.0)
    assert (b.valid, b.loBin, b.hiBin) == (1, 41.0, 53.0)


def test_zero_or_negative_half_width_disables_the_band(lib):
    for half in (0.0, -1.0):
        b = band(lib, 47.0, half)
        assert b.valid == 0
        assert lib.l3_band_contains(ctypes.byref(b), 47.0) == 0


def test_edges_are_inside(lib):
    b = band(lib, 47.0, 6.0)
    assert lib.l3_band_contains(ctypes.byref(b), 41.0) == 1
    assert lib.l3_band_contains(ctypes.byref(b), 53.0) == 1
    assert lib.l3_band_contains(ctypes.byref(b), 40.99) == 0
    assert lib.l3_band_contains(ctypes.byref(b), 53.01) == 0


def test_filter_removes_every_in_band_target_and_keeps_order(lib):
    b = band(lib, 47.0, 6.0)
    arr = targets(47.0, 30.0, 41.0, 60.0, 52.9, 35.5)

    kept = lib.l3_band_filter(ctypes.byref(b), arr, 6)

    assert kept == 3
    assert [arr[i].rangeBin for i in range(kept)] == [30.0, 60.0, 35.5]
    assert [arr[i].snr for i in range(kept)] == [99.0, 97.0, 95.0]


def test_filter_with_everything_in_band_keeps_nothing(lib):
    b = band(lib, 47.0, 6.0)
    arr = targets(44.0, 47.0, 50.0)
    assert lib.l3_band_filter(ctypes.byref(b), arr, 3) == 0


def test_disabled_band_filters_nothing(lib):
    b = band(lib, 47.0, 0.0)
    arr = targets(47.0, 48.0)
    assert lib.l3_band_filter(ctypes.byref(b), arr, 2) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_band.py -v`
Expected: FAIL — `AttributeError: module 'openflight.iwr6843.firmware_host' has no attribute 'Band'`.

- [ ] **Step 3: Implement**

`firmware/iwr6843/l3_band.h`:

```c
/* IWR6843 tee band: the bins around the ball where the MTI map carries a
 * ridge for the whole capture, so impact cannot be read there. Targets inside
 * it are dropped before any tracker sees them; the impact is established from
 * the tracks either side (l3_impact_fit.h). Pure C, no hardware.
 */
#ifndef L3_BAND_H
#define L3_BAND_H

#include <stdint.h>

#include "l3_observation.h"

typedef struct {
    uint8_t valid;    /* 0: no band, nothing is inside */
    float   loBin;    /* global bins, both edges inside */
    float   hiBin;
} l3_band_t;

/* [centre - halfWidth, centre + halfWidth]; halfWidth <= 0 disables it. */
void l3_band_around(float centreBin, float halfWidthBins, l3_band_t *out);
/* 1 when bin lies inside a valid band. */
int32_t l3_band_contains(const l3_band_t *band, float bin);
/* Drop the targets inside the band, keeping the others in their order
 * (strongest first). Returns how many are kept. */
uint32_t l3_band_filter(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n);

#endif /* L3_BAND_H */
```

`firmware/iwr6843/l3_band.c`:

```c
/* See l3_band.h. */
#include <string.h>

#include "l3_band.h"

void l3_band_around(float centreBin, float halfWidthBins, l3_band_t *out)
{
    memset(out, 0, sizeof(*out));
    if (!(halfWidthBins > 0.0F)) {
        return;
    }
    out->valid = 1U;
    out->loBin = centreBin - halfWidthBins;
    out->hiBin = centreBin + halfWidthBins;
}

int32_t l3_band_contains(const l3_band_t *band, float bin)
{
    return (band->valid && bin >= band->loBin && bin <= band->hiBin) ? 1 : 0;
}

uint32_t l3_band_filter(const l3_band_t *band, l3_target_obs_t *targets, uint32_t n)
{
    uint32_t kept = 0U;
    uint32_t i;

    for (i = 0U; i < n; i++) {
        if (l3_band_contains(band, targets[i].rangeBin)) {
            continue;
        }
        if (kept != i) {
            targets[kept] = targets[i];
        }
        kept++;
    }
    return kept;
}
```

In `firmware_host.py`: add `"l3_band.c",` to `HOST_SOURCES` right after `"l3_observation.c",`; add the mirror after `class TargetObs`:

```python
class Band(ctypes.Structure):
    """``l3_band_t``: the tee band, both edges inside."""

    _fields_ = [
        ("valid", ctypes.c_uint8),
        ("loBin", ctypes.c_float),
        ("hiBin", ctypes.c_float),
    ]
```

and in `_SIGNATURES` after the `# l3_observation.h` block:

```python
    # l3_band.h
    "l3_band_around": ([_F32, _F32, _P(Band)], None),
    "l3_band_contains": ([_P(Band), _F32], ctypes.c_int32),
    "l3_band_filter": ([_P(Band), _P(TargetObs), _U32], _U32),
```

In `firmware/iwr6843/makefile` SOURCES, add `l3_band.c` after `l3_observation.c`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_iwr6843_firmware_band.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_band.h firmware/iwr6843/l3_band.c firmware/iwr6843/makefile src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_band.py
git commit -m "iwr: the tee band and an in-place target filter"
```

---

### Task 2: `l3_impact_fit` — the per-track solve

**Files:**
- Create: `firmware/iwr6843/l3_impact_fit.h`, `firmware/iwr6843/l3_impact_fit.c`
- Modify: `firmware/iwr6843/makefile`, `src/openflight/iwr6843/firmware_host.py`
- Test: `tests/test_iwr6843_firmware_impact_fit.py`

**Interfaces:**
- Consumes: `l3_track_point_t`, `l3_club_track_t`, `l3_track_point()`, `l3_point_at_fn` from `l3_club_track.h`.
- Produces (C, used by Tasks 3–6):
  - enums `L3_FIT_CLUB_IN=0, L3_FIT_CLUB_OUT=1, L3_FIT_BALL_OUT=2, L3_FIT_TRACKS=3`; `L3_FIT_NO_TRACK 0xFFU`; `L3_FIT_MAX_POINTS 8U`
  - why enum `L3_FIT_WHY_OK=0, _MISSING, _FEW_POINTS, _WRONG_DIRECTION, _SPEED_BOUNDS, _PHYSICS, _NONFINITE, _DROPPED, L3_FIT_WHY_COUNT`
  - verdict enum `L3_FIT_VERDICT_NONE=0, _SINGLE, _CONSISTENT, _INCONSISTENT, L3_FIT_VERDICT_COUNT`
  - `l3_impact_fit_cfg_t { float binWidthM; float bandBins; uint32_t fitPoints; uint32_t minPoints; float clubMinMps; float clubMaxMps; float clubOutMaxRatio; float ballMinMps; float ballMaxMps; float gateSigmas; float minSigmaUs; }`
  - `l3_fit_estimate_t { uint8_t why; uint32_t points; float timeUs; float sigmaUs; float speedMps; }`
  - `l3_impact_fit_t { l3_fit_estimate_t track[3]; uint8_t verdict; uint8_t droppedTrack; uint8_t noLock; float impactUs; float spreadUs; float refinedMinusTriggerUs; }`
  - `l3_fit_list_t { const l3_track_point_t *points; uint32_t count; }`, `l3_fit_span_t { const l3_club_track_t *track; uint32_t first; uint32_t count; }`
  - `void l3_impact_fit_cfg_defaults(l3_impact_fit_cfg_t *cfg);`
  - `void l3_impact_fit_reset(l3_impact_fit_t *fit);`
  - `int32_t l3_fit_list_point(const void *ctx, uint32_t index, l3_track_point_t *out);`
  - `int32_t l3_fit_span_point(const void *ctx, uint32_t index, l3_track_point_t *out);`
  - `void l3_fit_span_after(const l3_club_track_t *track, uint32_t afterFrame, l3_fit_span_t *out);`
  - `void l3_impact_fit_track(const l3_impact_fit_cfg_t *cfg, uint8_t which, l3_point_at_fn pointAt, const void *ctx, uint32_t count, float ballRangeM, l3_fit_estimate_t *out);`
- Produces (Python): `fw.ImpactFitCfg`, `fw.FitEstimate`, `fw.ImpactFit`, `fw.FitList`, `fw.FitSpan`; constants `fw.FIT_TRACK_NAMES = ("club_in", "club_out", "ball_out")`, `fw.FIT_WHY_NAMES = ("ok", "missing", "few_points", "wrong_direction", "speed_bounds", "physics", "nonfinite", "dropped")`, `fw.FIT_VERDICT_NAMES = ("none", "single_track", "consistent", "inconsistent")`, `fw.FIT_NO_TRACK = 0xFF`, `fw.FIT_MAX_POINTS = 8`.

- [ ] **Step 1: Write the failing tests**

`tests/test_iwr6843_firmware_impact_fit.py` (Task 3 appends to this file):

```python
"""Tests for impact from the tracks either side of the tee band,
firmware/iwr6843/l3_impact_fit.c.

Scene used throughout: ball at rest at 2.20 m, impact at t = 30 000 us,
band +/- 6 bins (+/- 0.281 m). Club in at 30 m/s, club out at 25 m/s, ball
out at 60 m/s; every point lies outside the band.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

BIN_M = 6.0 / 128
BALL_M = 2.20
IMPACT_US = 30_000.0
CLUB_IN, CLUB_OUT, BALL_OUT = 0, 1, 2
WHY = {name: i for i, name in enumerate(fw.FIT_WHY_NAMES)}
VERDICT = {name: i for i, name in enumerate(fw.FIT_VERDICT_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def cfg(lib, **overrides) -> fw.ImpactFitCfg:
    c = fw.ImpactFitCfg()
    lib.l3_impact_fit_cfg_defaults(ctypes.byref(c))
    for name, value in overrides.items():
        setattr(c, name, value)
    return c


def line(speed_mps: float, times_us, *, noise_m=()) -> list[tuple[float, float]]:
    """(t_us, r_m) on the line through (IMPACT_US, BALL_M) at speed_mps."""
    noise = list(noise_m) + [0.0] * len(times_us)
    return [(t, BALL_M + speed_mps * (t - IMPACT_US) * 1e-6 + noise[i]) for i, t in enumerate(times_us)]


def point_list(samples) -> fw.FitList:
    arr = (fw.TrackPoint * max(1, len(samples)))()
    for i, (t, r) in enumerate(samples):
        arr[i].frame = i
        arr[i].timestampUs = int(round(t))
        arr[i].rangeM = r
        arr[i].rangeBin = r / BIN_M
    out = fw.FitList(ctypes.cast(arr, ctypes.POINTER(fw.TrackPoint)), len(samples))
    out.keep = arr  # keep the array alive as long as the list
    return out


def estimate(lib, which, samples, **overrides) -> fw.FitEstimate:
    lst = point_list(samples)
    out = fw.FitEstimate()
    lib.l3_impact_fit_track(
        ctypes.byref(cfg(lib, **overrides)),
        which,
        fw.fit_reader(lib),
        ctypes.byref(lst),
        len(samples),
        BALL_M,
        ctypes.byref(out),
    )
    return out


CLUB_IN_T = (9_000, 12_000, 15_000, 18_000)
CLUB_OUT_T = (42_000, 45_000, 48_000, 51_000)
BALL_OUT_T = (36_000, 39_000, 42_000, 45_000)


def test_defaults_are_the_specs(lib):
    c = cfg(lib)
    assert c.bandBins == 6.0 and c.fitPoints == 4 and c.minPoints == 3
    assert (c.clubMinMps, c.clubMaxMps, c.clubOutMaxRatio) == (10.0, 70.0, pytest.approx(1.10))
    assert (c.ballMinMps, c.ballMaxMps) == (15.0, 90.0)
    assert (c.gateSigmas, c.minSigmaUs) == (3.0, 500.0)
    assert c.binWidthM == pytest.approx(BIN_M)


@pytest.mark.parametrize(
    "which, speed, times",
    [(CLUB_IN, 30.0, CLUB_IN_T), (CLUB_OUT, 25.0, CLUB_OUT_T), (BALL_OUT, 60.0, BALL_OUT_T)],
)
def test_clean_line_crosses_the_ball_at_impact(lib, which, speed, times):
    e = estimate(lib, which, line(speed, times))
    assert e.why == WHY["ok"]
    assert e.points == 4
    assert e.speedMps == pytest.approx(speed, rel=1e-4)
    assert e.timeUs == pytest.approx(IMPACT_US, abs=2.0)


def test_sigma_floor_is_a_bin_of_quantisation_over_speed(lib):
    e = estimate(lib, CLUB_IN, line(30.0, CLUB_IN_T))
    floor_us = BIN_M / 12**0.5 / 30.0 * 1e6
    assert e.sigmaUs == pytest.approx(floor_us, rel=1e-3)


def test_sigma_grows_with_extrapolation_distance(lib):
    noise = (0.01, -0.01, 0.012, -0.008)
    near = estimate(lib, BALL_OUT, line(60.0, BALL_OUT_T, noise_m=noise))
    far_t = tuple(t + 15_000 for t in BALL_OUT_T)
    far = estimate(lib, BALL_OUT, line(60.0, far_t, noise_m=noise))
    assert near.why == far.why == WHY["ok"]
    assert far.sigmaUs > near.sigmaUs


def test_club_in_uses_its_last_k_points_and_outs_their_first_k(lib):
    # A stray early club-in point and a stray late ball point, both far off the line.
    club = [(0.0, 0.5)] + line(30.0, CLUB_IN_T)
    ball = line(60.0, BALL_OUT_T) + [(60_000.0, 9.0)]
    e_in = estimate(lib, CLUB_IN, club)
    e_out = estimate(lib, BALL_OUT, ball)
    assert e_in.timeUs == pytest.approx(IMPACT_US, abs=2.0)
    assert e_out.timeUs == pytest.approx(IMPACT_US, abs=2.0)


def test_strided_timestamps_are_honoured(lib):
    e = estimate(lib, BALL_OUT, line(60.0, (36_000, 42_000, 48_000, 54_000)))
    assert e.timeUs == pytest.approx(IMPACT_US, abs=2.0)


def test_no_points_is_missing(lib):
    assert estimate(lib, CLUB_IN, []).why == WHY["missing"]


def test_two_points_are_too_few(lib):
    e = estimate(lib, CLUB_IN, line(30.0, CLUB_IN_T[:2]))
    assert e.why == WHY["few_points"] and e.points == 2


def test_points_that_do_not_spread_in_time_are_nonfinite(lib):
    same = [(12_000.0, 1.6), (12_000.0, 1.7), (12_000.0, 1.8)]
    assert estimate(lib, CLUB_IN, same).why == WHY["nonfinite"]


@pytest.mark.parametrize("which, times", [(CLUB_IN, CLUB_IN_T), (CLUB_OUT, CLUB_OUT_T), (BALL_OUT, BALL_OUT_T)])
def test_moving_toward_the_radar_is_the_wrong_direction(lib, which, times):
    assert estimate(lib, which, line(-20.0, times)).why == WHY["wrong_direction"]


@pytest.mark.parametrize(
    "which, speed, times",
    [
        (CLUB_IN, 9.0, CLUB_IN_T),
        (CLUB_IN, 71.0, CLUB_IN_T),
        (CLUB_OUT, 71.0, CLUB_OUT_T),
        (BALL_OUT, 14.0, BALL_OUT_T),
        (BALL_OUT, 91.0, BALL_OUT_T),
    ],
)
def test_speeds_outside_the_bounds_are_rejected(lib, which, speed, times):
    assert estimate(lib, which, line(speed, times)).why == WHY["speed_bounds"]


def test_slow_club_out_is_accepted(lib):
    # After impact the club only slows; there is no lower bound but "moving away".
    assert estimate(lib, CLUB_OUT, line(3.0, CLUB_OUT_T)).why == WHY["ok"]


def test_span_after_reads_only_points_appended_after_a_frame(lib):
    track_cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(track_cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(track_cfg))
    for frame, (t, r) in enumerate(line(30.0, (3_000, 6_000, 9_000, 12_000, 15_000))):
        p = fw.TrackPoint()
        p.frame, p.timestampUs, p.rangeM, p.rangeBin = frame, int(t), r, r / BIN_M
        lib.l3_track_append_point(ctypes.byref(track), ctypes.byref(p))

    span = fw.FitSpan()
    lib.l3_fit_span_after(ctypes.byref(track), 2, ctypes.byref(span))

    assert (span.first, span.count) == (3, 2)
    out = fw.TrackPoint()
    assert lib.l3_fit_span_point(ctypes.byref(span), 0, ctypes.byref(out)) == 1
    assert out.frame == 3
    assert lib.l3_fit_span_point(ctypes.byref(span), 2, ctypes.byref(out)) == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_impact_fit.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'FIT_WHY_NAMES'`.

- [ ] **Step 3: Implement**

`firmware/iwr6843/l3_impact_fit.h`:

```c
/* IWR6843 impact from the tracks either side of the tee band.
 *
 * Inside the band (l3_band.h) the MTI ridge hides impact; outside it three
 * clean tracks remain: the club approaching (club in), the club carrying on
 * (club out) and the ball leaving (ball out). Each is fitted as a straight
 * line in range against time over the K points nearest the band and solved
 * for the moment it passes the ball's range, with an uncertainty that grows
 * with the extrapolation. Physics checks drop a track that cannot be what it
 * claims; the survivors are fused by inverse variance and their agreement is
 * the confidence. See docs/superpowers/specs/
 * 2026-09-28-iwr-impact-back-interpolation-design.md. Pure C, no hardware.
 */
#ifndef L3_IMPACT_FIT_H
#define L3_IMPACT_FIT_H

#include <stdint.h>

#include "l3_club_track.h"

#define L3_FIT_MAX_POINTS 8U
#define L3_FIT_NO_TRACK   0xFFU

enum { L3_FIT_CLUB_IN = 0, L3_FIT_CLUB_OUT, L3_FIT_BALL_OUT, L3_FIT_TRACKS };

enum {
    L3_FIT_WHY_OK = 0,
    L3_FIT_WHY_MISSING,          /* no points */
    L3_FIT_WHY_FEW_POINTS,       /* under minPoints */
    L3_FIT_WHY_WRONG_DIRECTION,  /* not moving downrange */
    L3_FIT_WHY_SPEED_BOUNDS,     /* outside this track's speed bounds */
    L3_FIT_WHY_PHYSICS,          /* contradicts another track (smash, club slowing) */
    L3_FIT_WHY_NONFINITE,        /* times do not spread, or the fit overflowed */
    L3_FIT_WHY_DROPPED,          /* the outlier of three */
    L3_FIT_WHY_COUNT
};

enum {
    L3_FIT_VERDICT_NONE = 0,
    L3_FIT_VERDICT_SINGLE,
    L3_FIT_VERDICT_CONSISTENT,
    L3_FIT_VERDICT_INCONSISTENT,
    L3_FIT_VERDICT_COUNT
};

typedef struct {
    float    binWidthM;
    float    bandBins;          /* the tee band's half width */
    uint32_t fitPoints;         /* K nearest the band */
    uint32_t minPoints;
    float    clubMinMps;        /* club in */
    float    clubMaxMps;        /* club in and club out */
    float    clubOutMaxRatio;   /* club out no faster than club in times this */
    float    ballMinMps;
    float    ballMaxMps;
    float    gateSigmas;        /* agreement gate ... */
    float    minSigmaUs;        /* ... on at least this sigma */
} l3_impact_fit_cfg_t;

typedef struct {
    uint8_t  why;               /* L3_FIT_WHY_* */
    uint32_t points;            /* points fitted (or offered, when too few) */
    float    timeUs;            /* when the line passes the ball's range */
    float    sigmaUs;
    float    speedMps;          /* range rate, positive downrange */
} l3_fit_estimate_t;

typedef struct {
    l3_fit_estimate_t track[L3_FIT_TRACKS];
    uint8_t  verdict;           /* L3_FIT_VERDICT_* */
    uint8_t  droppedTrack;      /* L3_FIT_NO_TRACK or the dropped index */
    uint8_t  noLock;            /* the configured tee stood in for the ball */
    float    impactUs;          /* 0 with verdict none */
    float    spreadUs;          /* max - min of the estimates kept */
    float    refinedMinusTriggerUs;
} l3_impact_fit_t;

/* A point list read by index: an array ... */
typedef struct {
    const l3_track_point_t *points;
    uint32_t count;
} l3_fit_list_t;
/* ... or a run of a track's held points, oldest first. */
typedef struct {
    const l3_club_track_t *track;
    uint32_t first;
    uint32_t count;
} l3_fit_span_t;

void l3_impact_fit_cfg_defaults(l3_impact_fit_cfg_t *cfg);
/* Every track missing, verdict none, nothing dropped. */
void l3_impact_fit_reset(l3_impact_fit_t *fit);
/* l3_point_at_fn readers for the two kinds of list. */
int32_t l3_fit_list_point(const void *ctx, uint32_t index, l3_track_point_t *out);
int32_t l3_fit_span_point(const void *ctx, uint32_t index, l3_track_point_t *out);
/* The track's points appended after afterFrame (its follow-through). */
void l3_fit_span_after(const l3_club_track_t *track, uint32_t afterFrame, l3_fit_span_t *out);
/* One track: the last fitPoints of count for club in, the first fitPoints for
 * club out and ball out; out is fully written. */
void l3_impact_fit_track(const l3_impact_fit_cfg_t *cfg, uint8_t which, l3_point_at_fn pointAt,
                         const void *ctx, uint32_t count, float ballRangeM,
                         l3_fit_estimate_t *out);

#endif /* L3_IMPACT_FIT_H */
```

`firmware/iwr6843/l3_impact_fit.c` (Task 3 appends the fusion to this file):

```c
/* See l3_impact_fit.h. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "l3_impact_fit.h"
#include "l3_text.h"

void l3_impact_fit_cfg_defaults(l3_impact_fit_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->binWidthM = 6.0F / 128.0F;
    cfg->bandBins = 6.0F;         /* the ridge on the 2026-09-28 capture */
    cfg->fitPoints = 4U;          /* about 12 ms at 3 ms frames */
    cfg->minPoints = 3U;          /* a line and a residual */
    cfg->clubMinMps = 10.0F;
    cfg->clubMaxMps = 70.0F;
    cfg->clubOutMaxRatio = 1.10F; /* after impact the club only slows */
    cfg->ballMinMps = 15.0F;
    cfg->ballMaxMps = 90.0F;
    cfg->gateSigmas = 3.0F;
    cfg->minSigmaUs = 500.0F;
}

void l3_impact_fit_reset(l3_impact_fit_t *fit)
{
    uint32_t i;

    memset(fit, 0, sizeof(*fit));
    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        fit->track[i].why = L3_FIT_WHY_MISSING;
    }
    fit->droppedTrack = L3_FIT_NO_TRACK;
}

int32_t l3_fit_list_point(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    const l3_fit_list_t *list = (const l3_fit_list_t *)ctx;

    if (index >= list->count) {
        return 0;
    }
    *out = list->points[index];
    return 1;
}

int32_t l3_fit_span_point(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    const l3_fit_span_t *span = (const l3_fit_span_t *)ctx;

    if (index >= span->count) {
        return 0;
    }
    return l3_track_point(span->track, span->first + index, out);
}

void l3_fit_span_after(const l3_club_track_t *track, uint32_t afterFrame, l3_fit_span_t *out)
{
    l3_track_point_t point;
    uint32_t i;

    out->track = track;
    out->first = track->count;
    out->count = 0U;
    for (i = 0U; i < track->count; i++) {
        (void)l3_track_point(track, i, &point);
        if (point.frame > afterFrame) {
            out->first = i;
            out->count = track->count - i;
            return;
        }
    }
}

/* This track's speed bounds; club out has no floor beyond moving downrange. */
static void l3_fit_bounds(const l3_impact_fit_cfg_t *cfg, uint8_t which, float *lo, float *hi)
{
    if (which == L3_FIT_BALL_OUT) {
        *lo = cfg->ballMinMps;
        *hi = cfg->ballMaxMps;
    } else if (which == L3_FIT_CLUB_IN) {
        *lo = cfg->clubMinMps;
        *hi = cfg->clubMaxMps;
    } else {
        *lo = 0.0F;
        *hi = cfg->clubMaxMps;
    }
}

void l3_impact_fit_track(const l3_impact_fit_cfg_t *cfg, uint8_t which, l3_point_at_fn pointAt,
                         const void *ctx, uint32_t count, float ballRangeM,
                         l3_fit_estimate_t *out)
{
    float t[L3_FIT_MAX_POINTS];
    float r[L3_FIT_MAX_POINTS];
    uint32_t want = (cfg->fitPoints < L3_FIT_MAX_POINTS) ? cfg->fitPoints : L3_FIT_MAX_POINTS;
    uint32_t n;
    uint32_t first;
    uint32_t i;
    float t0;
    float tMean = 0.0F;
    float rMean = 0.0F;
    float stt = 0.0F;
    float str = 0.0F;
    float rss = 0.0F;
    float v;
    float lo;
    float hi;
    float tk;
    float se;
    float floorM;
    l3_track_point_t point;

    memset(out, 0, sizeof(*out));
    out->why = L3_FIT_WHY_MISSING;
    if (pointAt == NULL || count == 0U) {
        return;
    }
    n = (count < want) ? count : want;
    first = (which == L3_FIT_CLUB_IN) ? count - n : 0U;
    out->points = n;
    if (n < 3U || n < cfg->minPoints) {
        out->why = L3_FIT_WHY_FEW_POINTS;
        return;
    }
    (void)pointAt(ctx, first, &point);
    t0 = (float)point.timestampUs;
    for (i = 0U; i < n; i++) {
        (void)pointAt(ctx, first + i, &point);
        t[i] = ((float)point.timestampUs - t0) * 1.0e-6F;
        r[i] = point.rangeM;
        tMean += t[i];
        rMean += r[i];
    }
    tMean /= (float)n;
    rMean /= (float)n;
    for (i = 0U; i < n; i++) {
        float dt = t[i] - tMean;

        stt += dt * dt;
        str += dt * (r[i] - rMean);
    }
    if (!(stt > 0.0F)) {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    v = str / stt;
    for (i = 0U; i < n; i++) {
        float e = r[i] - (rMean + v * (t[i] - tMean));

        rss += e * e;
    }
    out->speedMps = v;
    if (!isfinite(v) || !isfinite(rss)) {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    if (!(v > 0.0F)) {
        out->why = L3_FIT_WHY_WRONG_DIRECTION;
        return;
    }
    l3_fit_bounds(cfg, which, &lo, &hi);
    if (v < lo || v > hi) {
        out->why = L3_FIT_WHY_SPEED_BOUNDS;
        return;
    }
    tk = tMean + (ballRangeM - rMean) / v;
    se = sqrtf(rss / (float)(n - 2U)) *
         sqrtf(1.0F / (float)n + (tk - tMean) * (tk - tMean) / stt);
    floorM = cfg->binWidthM / sqrtf(12.0F);
    if (se < floorM) {
        se = floorM;
    }
    out->timeUs = t0 + tk * 1.0e6F;
    out->sigmaUs = se / v * 1.0e6F;
    if (!isfinite(out->timeUs) || !isfinite(out->sigmaUs)) {
        out->why = L3_FIT_WHY_NONFINITE;
        return;
    }
    out->why = L3_FIT_WHY_OK;
}
```

In `firmware_host.py`: add `"l3_impact_fit.c",` to `HOST_SOURCES` after `"l3_club_track.c",`; constants after the `# l3_impact.h` block:

```python
# l3_impact_fit.h
FIT_MAX_POINTS = 8
FIT_NO_TRACK = 0xFF
FIT_TRACK_NAMES = ("club_in", "club_out", "ball_out")
FIT_WHY_NAMES = (
    "ok",
    "missing",
    "few_points",
    "wrong_direction",
    "speed_bounds",
    "physics",
    "nonfinite",
    "dropped",
)
FIT_VERDICT_NAMES = ("none", "single_track", "consistent", "inconsistent")
```

mirrors after `class Impact`:

```python
class ImpactFitCfg(ctypes.Structure):
    """``l3_impact_fit_cfg_t``."""

    _fields_ = [
        ("binWidthM", ctypes.c_float),
        ("bandBins", ctypes.c_float),
        ("fitPoints", ctypes.c_uint32),
        ("minPoints", ctypes.c_uint32),
        ("clubMinMps", ctypes.c_float),
        ("clubMaxMps", ctypes.c_float),
        ("clubOutMaxRatio", ctypes.c_float),
        ("ballMinMps", ctypes.c_float),
        ("ballMaxMps", ctypes.c_float),
        ("gateSigmas", ctypes.c_float),
        ("minSigmaUs", ctypes.c_float),
    ]


class FitEstimate(ctypes.Structure):
    """``l3_fit_estimate_t``: one track's impact estimate."""

    _fields_ = [
        ("why", ctypes.c_uint8),
        ("points", ctypes.c_uint32),
        ("timeUs", ctypes.c_float),
        ("sigmaUs", ctypes.c_float),
        ("speedMps", ctypes.c_float),
    ]


class ImpactFit(ctypes.Structure):
    """``l3_impact_fit_t``: the three estimates, fused."""

    _fields_ = [
        ("track", FitEstimate * len(FIT_TRACK_NAMES)),
        ("verdict", ctypes.c_uint8),
        ("droppedTrack", ctypes.c_uint8),
        ("noLock", ctypes.c_uint8),
        ("impactUs", ctypes.c_float),
        ("spreadUs", ctypes.c_float),
        ("refinedMinusTriggerUs", ctypes.c_float),
    ]


class FitList(ctypes.Structure):
    """``l3_fit_list_t``: an array of points."""

    _fields_ = [("points", ctypes.POINTER(TrackPoint)), ("count", ctypes.c_uint32)]


class FitSpan(ctypes.Structure):
    """``l3_fit_span_t``: a run of a track's held points."""

    _fields_ = [
        ("track", ctypes.POINTER(ClubTrack)),
        ("first", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
    ]
```

signatures after the `# l3_impact.h` block (the readers are passed as C function pointers, so they are fetched as `c_void_p` too):

```python
    # l3_impact_fit.h
    "l3_impact_fit_cfg_defaults": ([_P(ImpactFitCfg)], None),
    "l3_impact_fit_reset": ([_P(ImpactFit)], None),
    "l3_fit_list_point": ([ctypes.c_void_p, _U32, _P(TrackPoint)], ctypes.c_int32),
    "l3_fit_span_point": ([ctypes.c_void_p, _U32, _P(TrackPoint)], ctypes.c_int32),
    "l3_fit_span_after": ([_P(ClubTrack), _U32, _P(FitSpan)], None),
    "l3_impact_fit_track": (
        [_P(ImpactFitCfg), ctypes.c_uint8, ctypes.c_void_p, ctypes.c_void_p, _U32, _F32,
         _P(FitEstimate)],
        None,
    ),
```

The C API takes an `l3_point_at_fn`, so tests and the replay pass the address of a C reader. Add this module-level helper to `firmware_host.py` (below `build_firmware_library`):

```python
def fit_reader(lib: ctypes.CDLL, name: str = "l3_fit_list_point") -> ctypes.c_void_p:
    """The address of a C point reader (l3_fit_list_point / l3_fit_span_point),
    to pass where the C API takes an l3_point_at_fn."""
    return ctypes.cast(getattr(lib, name), ctypes.c_void_p)
```

In `makefile` SOURCES add `l3_impact_fit.c` after `l3_club_track.c`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_iwr6843_firmware_impact_fit.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_impact_fit.h firmware/iwr6843/l3_impact_fit.c firmware/iwr6843/makefile src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_impact_fit.py
git commit -m "iwr: per-track impact estimate at the ball's range"
```

---

### Task 3: `l3_impact_fit` — physics across tracks, fusion, verdict, format

**Files:**
- Modify: `firmware/iwr6843/l3_impact_fit.h`, `firmware/iwr6843/l3_impact_fit.c`, `src/openflight/iwr6843/firmware_host.py`
- Test: `tests/test_iwr6843_firmware_impact_fit.py` (append)

**Interfaces:**
- Consumes: Task 2's types and `l3_impact_fit_track`.
- Produces (C):
  - `void l3_impact_fit_solve(const l3_impact_fit_cfg_t *cfg, l3_impact_fit_t *fit, uint32_t triggerUs);` — reads `fit->track[]`, writes everything else.
  - `void l3_impact_fit_run(const l3_impact_fit_cfg_t *cfg, const l3_fit_list_t *clubIn, const l3_fit_span_t *clubOut, const l3_fit_span_t *ballOut, float ballRangeM, uint8_t noLock, uint32_t triggerUs, l3_impact_fit_t *fit);` — any argument list may be NULL (that track is missing).
  - `const char *l3_impact_fit_why_name(uint8_t why);`, `const char *l3_impact_fit_verdict_name(uint8_t verdict);`
  - `int32_t l3_impact_fit_format(const l3_impact_fit_t *fit, char *out, uint32_t cap);` — `"impactfit verdict=consistent t=30000 spreadus=12 dtrigus=-2500 dropped=- nolock=0 club_in=ok:30000+-120 club_out=ok:30001+-140 ball_out=ok:29999+-60"`

- [ ] **Step 1: Write the failing tests** (append)

```python
def solved(lib, estimates: dict[int, tuple[str, float, float, float]], trigger_us=27_500, **overrides):
    """estimates: track -> (why, timeUs, sigmaUs, speedMps); others missing."""
    fit = fw.ImpactFit()
    lib.l3_impact_fit_reset(ctypes.byref(fit))
    for which, (why, t, sigma, speed) in estimates.items():
        e = fit.track[which]
        e.why, e.timeUs, e.sigmaUs, e.speedMps, e.points = WHY[why], t, sigma, speed, 4
    lib.l3_impact_fit_solve(ctypes.byref(cfg(lib, **overrides)), ctypes.byref(fit), trigger_us)
    return fit


def test_three_agreeing_tracks_are_consistent_and_weighted_by_inverse_variance(lib):
    fit = solved(
        lib,
        {CLUB_IN: ("ok", 30_000, 400, 30), CLUB_OUT: ("ok", 30_300, 400, 25), BALL_OUT: ("ok", 30_100, 200, 60)},
    )
    assert fit.verdict == VERDICT["consistent"]
    w = [1 / 400**2, 1 / 400**2, 1 / 200**2]
    expected = (30_000 * w[0] + 30_300 * w[1] + 30_100 * w[2]) / sum(w)
    assert fit.impactUs == pytest.approx(expected, abs=0.5)
    assert fit.spreadUs == pytest.approx(300)
    assert fit.droppedTrack == fw.FIT_NO_TRACK
    assert fit.refinedMinusTriggerUs == pytest.approx(expected - 27_500, abs=0.5)


def test_gate_uses_the_half_millisecond_floor(lib):
    # sigmas of 50 us would gate at 150 us; the 500 us floor gates at 1.5 ms.
    fit = solved(lib, {CLUB_IN: ("ok", 30_000, 50, 30), BALL_OUT: ("ok", 31_000, 50, 60)})
    assert fit.verdict == VERDICT["consistent"]


def test_one_outlier_of_three_is_dropped_and_the_rest_fused(lib):
    fit = solved(
        lib,
        {CLUB_IN: ("ok", 30_000, 300, 30), CLUB_OUT: ("ok", 38_000, 300, 25), BALL_OUT: ("ok", 30_200, 300, 60)},
    )
    assert fit.verdict == VERDICT["consistent"]
    assert fit.droppedTrack == CLUB_OUT
    assert fit.track[CLUB_OUT].why == WHY["dropped"]
    assert fit.impactUs == pytest.approx(30_100, abs=0.5)
    assert fit.spreadUs == pytest.approx(200)


def test_two_disagreeing_tracks_are_inconsistent_and_take_the_smaller_sigma(lib):
    fit = solved(lib, {CLUB_IN: ("ok", 30_000, 600, 30), BALL_OUT: ("ok", 36_000, 200, 60)})
    assert fit.verdict == VERDICT["inconsistent"]
    assert fit.impactUs == pytest.approx(36_000)


def test_three_all_disagreeing_are_inconsistent(lib):
    fit = solved(
        lib,
        {CLUB_IN: ("ok", 20_000, 300, 30), CLUB_OUT: ("ok", 30_000, 200, 25), BALL_OUT: ("ok", 40_000, 300, 60)},
    )
    assert fit.verdict == VERDICT["inconsistent"]
    assert fit.impactUs == pytest.approx(30_000)


def test_one_track_is_single(lib):
    fit = solved(lib, {BALL_OUT: ("ok", 30_000, 200, 60)})
    assert fit.verdict == VERDICT["single_track"]
    assert fit.impactUs == pytest.approx(30_000)
    assert fit.spreadUs == 0.0


def test_no_track_is_none_and_reports_nothing(lib):
    fit = solved(lib, {})
    assert fit.verdict == VERDICT["none"]
    assert (fit.impactUs, fit.refinedMinusTriggerUs) == (0.0, 0.0)


def test_club_out_faster_than_club_in_breaks_physics(lib):
    fit = solved(lib, {CLUB_IN: ("ok", 30_000, 300, 30), CLUB_OUT: ("ok", 30_000, 300, 34)})
    assert fit.track[CLUB_OUT].why == WHY["physics"]
    assert fit.verdict == VERDICT["single_track"]


def test_ball_not_faster_than_club_out_breaks_physics(lib):
    fit = solved(lib, {CLUB_OUT: ("ok", 30_000, 300, 25), BALL_OUT: ("ok", 30_000, 300, 25)})
    assert fit.track[BALL_OUT].why == WHY["physics"]
    assert fit.verdict == VERDICT["single_track"]


def run(lib, club_in=(), club_out=(), ball_out=(), no_lock=0, trigger_us=27_500) -> fw.ImpactFit:
    """l3_impact_fit_run with club out and ball out as tracks (spans), club in as a list."""

    def span_of(samples):
        track_cfg = fw.TrackCfg()
        lib.l3_track_cfg_defaults(ctypes.byref(track_cfg))
        track = fw.ClubTrack()
        lib.l3_track_init(ctypes.byref(track), ctypes.byref(track_cfg))
        for frame, (t, r) in enumerate(samples):
            p = fw.TrackPoint()
            p.frame, p.timestampUs, p.rangeM, p.rangeBin = frame, int(t), r, r / BIN_M
            lib.l3_track_append_point(ctypes.byref(track), ctypes.byref(p))
        span = fw.FitSpan(ctypes.pointer(track), 0, len(samples))
        span.keep = track
        return span

    fit = fw.ImpactFit()
    lst = point_list(club_in)
    lib.l3_impact_fit_run(
        ctypes.byref(cfg(lib)),
        ctypes.byref(lst) if club_in else None,
        ctypes.byref(span_of(club_out)) if club_out else None,
        ctypes.byref(span_of(ball_out)) if ball_out else None,
        BALL_M,
        no_lock,
        trigger_us,
        ctypes.byref(fit),
    )
    return fit


def test_run_on_the_clean_scene_recovers_impact(lib):
    fit = run(
        lib,
        club_in=line(30.0, CLUB_IN_T),
        club_out=line(25.0, CLUB_OUT_T),
        ball_out=line(60.0, BALL_OUT_T),
    )
    assert fit.verdict == VERDICT["consistent"]
    assert fit.impactUs == pytest.approx(IMPACT_US, abs=5.0)
    assert fit.refinedMinusTriggerUs == pytest.approx(IMPACT_US - 27_500, abs=5.0)


def test_club_in_missing_uses_the_outgoing_tracks(lib):
    fit = run(lib, club_out=line(25.0, CLUB_OUT_T), ball_out=line(60.0, BALL_OUT_T))
    assert fit.track[CLUB_IN].why == WHY["missing"]
    assert fit.verdict == VERDICT["consistent"]
    assert fit.impactUs == pytest.approx(IMPACT_US, abs=5.0)


def test_ball_missing_fuses_the_club_either_side(lib):
    fit = run(lib, club_in=line(30.0, CLUB_IN_T), club_out=line(25.0, CLUB_OUT_T))
    assert fit.track[BALL_OUT].why == WHY["missing"]
    assert fit.verdict == VERDICT["consistent"]


def test_no_lock_is_carried(lib):
    assert run(lib, ball_out=line(60.0, BALL_OUT_T), no_lock=1).noLock == 1


def test_format_names_the_verdict_and_every_track(lib):
    fit = run(lib, club_in=line(30.0, CLUB_IN_T), ball_out=line(60.0, BALL_OUT_T))
    text = fw.c_text(lib.l3_impact_fit_format, ctypes.byref(fit), cap=240)
    assert text.startswith("impactfit verdict=consistent t=30000 ")
    assert "club_in=ok:" in text and "club_out=missing" in text and "ball_out=ok:" in text
    assert "dropped=- nolock=0" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_impact_fit.py -v`
Expected: new tests FAIL — `AttributeError: function 'l3_impact_fit_solve' not found` (the signature table references a missing symbol only once added; add the signatures in Step 3).

- [ ] **Step 3: Implement**

Append to `l3_impact_fit.h` before `#endif`:

```c
/* Physics across the tracks, then fusion: inverse-variance mean; each kept
 * estimate must lie within gateSigmas * max(sigma, minSigmaUs) of it. Three
 * with one outlier: drop it and fuse the two. Two that disagree, or three
 * that still disagree: inconsistent, impact from the smallest sigma. */
void l3_impact_fit_solve(const l3_impact_fit_cfg_t *cfg, l3_impact_fit_t *fit, uint32_t triggerUs);
/* All three tracks and the solve. A NULL list or span is a missing track. */
void l3_impact_fit_run(const l3_impact_fit_cfg_t *cfg, const l3_fit_list_t *clubIn,
                       const l3_fit_span_t *clubOut, const l3_fit_span_t *ballOut,
                       float ballRangeM, uint8_t noLock, uint32_t triggerUs,
                       l3_impact_fit_t *fit);
const char *l3_impact_fit_why_name(uint8_t why);
const char *l3_impact_fit_verdict_name(uint8_t verdict);
/* "impactfit verdict=consistent t=30000 spreadus=12 dtrigus=-2500 dropped=- nolock=0
 *  club_in=ok:30000+-120 club_out=ok:30001+-140 ball_out=ok:29999+-60" */
int32_t l3_impact_fit_format(const l3_impact_fit_t *fit, char *out, uint32_t cap);
```

Append to `l3_impact_fit.c`:

```c
static const char *const kWhyNames[L3_FIT_WHY_COUNT] = {
    "ok", "missing", "few_points", "wrong_direction", "speed_bounds", "physics", "nonfinite",
    "dropped"
};
static const char *const kVerdictNames[L3_FIT_VERDICT_COUNT] = {
    "none", "single_track", "consistent", "inconsistent"
};
static const char *const kTrackNames[L3_FIT_TRACKS] = { "club_in", "club_out", "ball_out" };

static int32_t l3_fit_ok(const l3_fit_estimate_t *e)
{
    return (e->why == L3_FIT_WHY_OK) ? 1 : 0;
}

/* Inverse-variance mean of the kept estimates; returns how many. */
static uint32_t l3_fit_mean(const l3_impact_fit_t *fit, float *mean)
{
    float weights = 0.0F;
    float sum = 0.0F;
    uint32_t used = 0U;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        const l3_fit_estimate_t *e = &fit->track[i];
        float w;

        if (!l3_fit_ok(e)) {
            continue;
        }
        w = 1.0F / (e->sigmaUs * e->sigmaUs);
        weights += w;
        sum += w * e->timeUs;
        used++;
    }
    *mean = (used > 0U) ? sum / weights : 0.0F;
    return used;
}

/* The kept estimate furthest outside its gate around mean, L3_FIT_NO_TRACK
 * when every one agrees. */
static uint8_t l3_fit_worst(const l3_impact_fit_cfg_t *cfg, const l3_impact_fit_t *fit,
                            float mean)
{
    uint8_t worst = L3_FIT_NO_TRACK;
    float worstRatio = 1.0F;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        const l3_fit_estimate_t *e = &fit->track[i];
        float sigma;
        float ratio;

        if (!l3_fit_ok(e)) {
            continue;
        }
        sigma = (e->sigmaUs > cfg->minSigmaUs) ? e->sigmaUs : cfg->minSigmaUs;
        ratio = fabsf(e->timeUs - mean) / (cfg->gateSigmas * sigma);
        if (ratio > worstRatio) {
            worstRatio = ratio;
            worst = (uint8_t)i;
        }
    }
    return worst;
}

static float l3_fit_sharpest(const l3_impact_fit_t *fit)
{
    const l3_fit_estimate_t *best = NULL;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        const l3_fit_estimate_t *e = &fit->track[i];

        if (l3_fit_ok(e) && (best == NULL || e->sigmaUs < best->sigmaUs)) {
            best = e;
        }
    }
    return (best != NULL) ? best->timeUs : 0.0F;
}

static float l3_fit_spread(const l3_impact_fit_t *fit)
{
    float lo = 0.0F;
    float hi = 0.0F;
    uint32_t seen = 0U;
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        const l3_fit_estimate_t *e = &fit->track[i];

        if (!l3_fit_ok(e)) {
            continue;
        }
        if (seen == 0U || e->timeUs < lo) {
            lo = e->timeUs;
        }
        if (seen == 0U || e->timeUs > hi) {
            hi = e->timeUs;
        }
        seen++;
    }
    return hi - lo;
}

void l3_impact_fit_solve(const l3_impact_fit_cfg_t *cfg, l3_impact_fit_t *fit, uint32_t triggerUs)
{
    l3_fit_estimate_t *in = &fit->track[L3_FIT_CLUB_IN];
    l3_fit_estimate_t *co = &fit->track[L3_FIT_CLUB_OUT];
    l3_fit_estimate_t *bo = &fit->track[L3_FIT_BALL_OUT];
    uint32_t used;
    uint8_t worst;
    float mean;

    fit->verdict = L3_FIT_VERDICT_NONE;
    fit->droppedTrack = L3_FIT_NO_TRACK;
    fit->impactUs = 0.0F;
    fit->spreadUs = 0.0F;
    fit->refinedMinusTriggerUs = 0.0F;
    if (l3_fit_ok(in) && l3_fit_ok(co) && co->speedMps > in->speedMps * cfg->clubOutMaxRatio) {
        co->why = L3_FIT_WHY_PHYSICS;
    }
    if (l3_fit_ok(co) && l3_fit_ok(bo) && bo->speedMps <= co->speedMps) {
        bo->why = L3_FIT_WHY_PHYSICS;
    }
    used = l3_fit_mean(fit, &mean);
    if (used == 0U) {
        return;
    }
    if (used == 1U) {
        fit->verdict = L3_FIT_VERDICT_SINGLE;
        fit->impactUs = mean;
    } else {
        worst = l3_fit_worst(cfg, fit, mean);
        if (worst != L3_FIT_NO_TRACK && used == 3U) {
            fit->track[worst].why = L3_FIT_WHY_DROPPED;
            fit->droppedTrack = worst;
            (void)l3_fit_mean(fit, &mean);
            worst = l3_fit_worst(cfg, fit, mean);
        }
        if (worst == L3_FIT_NO_TRACK) {
            fit->verdict = L3_FIT_VERDICT_CONSISTENT;
            fit->impactUs = mean;
        } else {
            fit->verdict = L3_FIT_VERDICT_INCONSISTENT;
            fit->impactUs = l3_fit_sharpest(fit);
        }
    }
    fit->spreadUs = l3_fit_spread(fit);
    fit->refinedMinusTriggerUs = fit->impactUs - (float)triggerUs;
}

void l3_impact_fit_run(const l3_impact_fit_cfg_t *cfg, const l3_fit_list_t *clubIn,
                       const l3_fit_span_t *clubOut, const l3_fit_span_t *ballOut,
                       float ballRangeM, uint8_t noLock, uint32_t triggerUs,
                       l3_impact_fit_t *fit)
{
    l3_impact_fit_reset(fit);
    fit->noLock = noLock;
    if (clubIn != NULL) {
        l3_impact_fit_track(cfg, L3_FIT_CLUB_IN, l3_fit_list_point, clubIn, clubIn->count,
                            ballRangeM, &fit->track[L3_FIT_CLUB_IN]);
    }
    if (clubOut != NULL) {
        l3_impact_fit_track(cfg, L3_FIT_CLUB_OUT, l3_fit_span_point, clubOut, clubOut->count,
                            ballRangeM, &fit->track[L3_FIT_CLUB_OUT]);
    }
    if (ballOut != NULL) {
        l3_impact_fit_track(cfg, L3_FIT_BALL_OUT, l3_fit_span_point, ballOut, ballOut->count,
                            ballRangeM, &fit->track[L3_FIT_BALL_OUT]);
    }
    l3_impact_fit_solve(cfg, fit, triggerUs);
}

const char *l3_impact_fit_why_name(uint8_t why)
{
    return (why < L3_FIT_WHY_COUNT) ? kWhyNames[why] : "?";
}

const char *l3_impact_fit_verdict_name(uint8_t verdict)
{
    return (verdict < L3_FIT_VERDICT_COUNT) ? kVerdictNames[verdict] : "?";
}

int32_t l3_impact_fit_format(const l3_impact_fit_t *fit, char *out, uint32_t cap)
{
    char tracks[L3_FIT_TRACKS][40];
    uint32_t i;

    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        const l3_fit_estimate_t *e = &fit->track[i];

        if (e->why == L3_FIT_WHY_OK || e->why == L3_FIT_WHY_DROPPED) {
            (void)snprintf(tracks[i], sizeof(tracks[i]), "%s=%s:%d+-%d", kTrackNames[i],
                           l3_impact_fit_why_name(e->why), (int)(e->timeUs + 0.5F),
                           (int)(e->sigmaUs + 0.5F));
        } else {
            (void)snprintf(tracks[i], sizeof(tracks[i]), "%s=%s", kTrackNames[i],
                           l3_impact_fit_why_name(e->why));
        }
    }
    return snprintf(out, cap,
                    "impactfit verdict=%s t=%d spreadus=%d dtrigus=%d dropped=%s nolock=%u "
                    "%s %s %s",
                    l3_impact_fit_verdict_name(fit->verdict), (int)(fit->impactUs + 0.5F),
                    (int)(fit->spreadUs + 0.5F),
                    (int)((fit->refinedMinusTriggerUs >= 0.0F) ? fit->refinedMinusTriggerUs + 0.5F
                                                               : fit->refinedMinusTriggerUs - 0.5F),
                    (fit->droppedTrack < L3_FIT_TRACKS) ? kTrackNames[fit->droppedTrack] : "-",
                    (unsigned)fit->noLock, tracks[0], tracks[1], tracks[2]);
}
```

Signatures in `firmware_host.py` (extend the `# l3_impact_fit.h` block):

```python
    "l3_impact_fit_solve": ([_P(ImpactFitCfg), _P(ImpactFit), _U32], None),
    "l3_impact_fit_run": (
        [_P(ImpactFitCfg), _P(FitList), _P(FitSpan), _P(FitSpan), _F32, ctypes.c_uint8, _U32,
         _P(ImpactFit)],
        None,
    ),
    "l3_impact_fit_why_name": ([ctypes.c_uint8], ctypes.c_char_p),
    "l3_impact_fit_verdict_name": ([ctypes.c_uint8], ctypes.c_char_p),
    "l3_impact_fit_format": ([_P(ImpactFit), *_TEXT], ctypes.c_int32),
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_iwr6843_firmware_impact_fit.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_impact_fit.h firmware/iwr6843/l3_impact_fit.c src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_impact_fit.py
git commit -m "iwr: fuse the three impact estimates with an agreement verdict"
```

---

### Task 4: Real-time range-only impact and the shot's `range` source

**Files:**
- Modify: `firmware/iwr6843/l3_impact.h`, `firmware/iwr6843/l3_impact.c`, `firmware/iwr6843/l3_shot.h`, `firmware/iwr6843/l3_shot.c`, `firmware/iwr6843/l3_result.c` (source name), `src/openflight/iwr6843/firmware_host.py`
- Test: `tests/test_iwr6843_firmware_impact_fit.py` (append), the shot tests file (`grep -ln "l3_shot_update" tests/` — append there)

**Interfaces:**
- Consumes: `l3_fit_estimate_t` (Task 2).
- Produces:
  - `int32_t l3_impact_update_range(l3_impact_t *impact, const l3_fit_estimate_t *clubIn, uint32_t nowUs);` — the board and replay run it on a **second** `l3_impact_t` instance, so its counters and `why` are its own.
  - `#define L3_SHOT_IMPACT_RANGE 4U`; `l3_shot_input_t` gains `uint8_t rangeFired;` as its **last** field; `fw.ShotInput` gains `("rangeFired", ctypes.c_uint8)` last; `fw.SHOT_IMPACT_RANGE = 4`.
  - Source text everywhere (`l3_shot_format`, `l3_result_format`): `none`, `gate`, `geometry`, `both` as today for bits 0–3; with the range bit, the `+`-joined names of the set bits in order gate, geometry, range (e.g. `range`, `gate+range`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_iwr6843_firmware_impact_fit.py`:

```python
IMPACT_WHY = {name: i for i, name in enumerate(fw.IMPACT_WHY_NAMES)}


def range_impact(lib) -> fw.Impact:
    c = fw.ImpactCfg()
    lib.l3_impact_cfg_defaults(ctypes.byref(c))
    impact = fw.Impact()
    lib.l3_impact_init(ctypes.byref(impact), ctypes.byref(c))
    return impact


def club_in_estimate(time_us: float, why: str = "ok") -> fw.FitEstimate:
    e = fw.FitEstimate()
    e.why, e.timeUs, e.sigmaUs, e.speedMps, e.points = WHY[why], time_us, 300.0, 30.0, 4
    return e


def test_range_impact_waits_until_the_crossing_is_within_the_horizon(lib):
    impact = range_impact(lib)
    e = club_in_estimate(30_000)
    assert lib.l3_impact_update_range(ctypes.byref(impact), ctypes.byref(e), 20_000) == 0
    assert impact.why == IMPACT_WHY["pending"]
    assert lib.l3_impact_update_range(ctypes.byref(impact), ctypes.byref(e), 27_000) == 1
    assert impact.why == IMPACT_WHY["fired"]
    assert impact.impactTimestampUs == 30_000
    assert impact.offsetS == pytest.approx(0.003, abs=1e-6)


def test_range_impact_fires_once(lib):
    impact = range_impact(lib)
    e = club_in_estimate(30_000)
    assert lib.l3_impact_update_range(ctypes.byref(impact), ctypes.byref(e), 29_000) == 1
    assert lib.l3_impact_update_range(ctypes.byref(impact), ctypes.byref(e), 30_000) == 0


def test_range_impact_long_past_is_passed_not_fired(lib):
    impact = range_impact(lib)
    e = club_in_estimate(30_000)
    assert lib.l3_impact_update_range(ctypes.byref(impact), ctypes.byref(e), 40_000) == 0
    assert impact.why == IMPACT_WHY["passed"]


def test_range_impact_without_a_club_in_estimate_does_not_fire(lib):
    impact = range_impact(lib)
    for why in ("missing", "few_points", "speed_bounds"):
        e = club_in_estimate(30_000, why)
        assert lib.l3_impact_update_range(ctypes.byref(impact), ctypes.byref(e), 29_000) == 0
        assert impact.why == IMPACT_WHY["nodelivery"]
    assert lib.l3_impact_update_range(ctypes.byref(impact), None, 29_000) == 0
```

Append to the shot tests file (it already has a `lib` fixture and builds `fw.ShotInput`; follow its local helper for driving the machine to `club_track` — read the file first and reuse its helper names; the assertions below are what must hold):

```python
def test_range_fire_enters_impact_with_the_range_source(lib):
    shot = ready_club_tracking_shot(lib)  # the file's existing helper that reaches club_track
    shot_in = fw.ShotInput()
    shot_in.ballLocked = 1
    shot_in.clubActive = 1
    shot_in.clubPoints = 5
    shot_in.rangeFired = 1
    shot_in.impactTimestampUs = 30_000
    state = lib.l3_shot_update(ctypes.byref(shot), ctypes.byref(shot_in), 10)
    assert fw.SHOT_STATE_NAMES[state] == "impact"
    assert shot.impactSource == fw.SHOT_IMPACT_RANGE
    assert shot.impactTimestampUs == 30_000
    text = fw.c_text(lib.l3_shot_format, ctypes.byref(shot), cap=240)
    assert "source=range" in text
```

If the shot tests file has no helper that reaches `club_track`, add one there:

```python
def ready_club_tracking_shot(lib) -> fw.Shot:
    cfg = fw.ShotCfg()
    lib.l3_shot_cfg_defaults(ctypes.byref(cfg))
    shot = fw.Shot()
    lib.l3_shot_init(ctypes.byref(shot), ctypes.byref(cfg))
    for frame, points in enumerate((0, 1, 3)):
        shot_in = fw.ShotInput()
        shot_in.ballLocked = 1
        shot_in.clubActive = 1 if points else 0
        shot_in.clubPoints = points
        lib.l3_shot_update(ctypes.byref(shot), ctypes.byref(shot_in), frame)
    assert fw.SHOT_STATE_NAMES[shot.state] == "club_track"
    return shot
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_impact_fit.py -k range_impact -v` and the shot test.
Expected: FAIL — `function 'l3_impact_update_range' not found` / `ShotInput has no field rangeFired`.

- [ ] **Step 3: Implement**

`l3_impact.h`: add `#include "l3_impact_fit.h"` after `#include "l3_frames.h"`, and before `const char *l3_impact_why_name`:

```c
/* Range only: fire on the club-in estimate (l3_impact_fit_track) when its
 * crossing of the ball's range is within the horizon of nowUs, the current
 * frame's time -- the club coasts across the tee band, so the newest point
 * stops advancing and the frame clock must. A missing or rejected estimate
 * is nodelivery. Run on its own l3_impact_t so its verdicts stay apart. */
int32_t l3_impact_update_range(l3_impact_t *impact, const l3_fit_estimate_t *clubIn,
                               uint32_t nowUs);
```

`l3_impact.c`, after `l3_impact_update`:

```c
int32_t l3_impact_update_range(l3_impact_t *impact, const l3_fit_estimate_t *clubIn,
                               uint32_t nowUs)
{
    float offset;

    if (impact->fired) {
        return 0;
    }
    if (clubIn == NULL || clubIn->why != L3_FIT_WHY_OK) {
        return l3_impact_note(impact, L3_IMPACT_WHY_NO_DELIVERY);
    }
    offset = (clubIn->timeUs - (float)nowUs) * 1.0e-6F;
    impact->offsetS = offset;
    impact->closestM = 0.0F;
    if (offset > impact->cfg.horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PENDING);
    }
    if (offset < -impact->cfg.horizonS) {
        return l3_impact_note(impact, L3_IMPACT_WHY_PASSED);
    }
    impact->fired = 1U;
    impact->impactTimestampUs = (clubIn->timeUs > 0.0F) ? (uint32_t)(clubIn->timeUs + 0.5F) : 0U;
    return l3_impact_note(impact, L3_IMPACT_WHY_FIRED);
}
```

`l3_shot.h`: after `#define L3_SHOT_IMPACT_GEOMETRY  2U` add `#define L3_SHOT_IMPACT_RANGE     4U`; in `l3_shot_input_t` after `uint8_t solved;` add `uint8_t rangeFired;          /* range-only impact fired this frame */`.

`l3_shot.c`: line 57–58 becomes

```c
    shot->impactSource = (uint8_t)((in->gateFired ? L3_SHOT_IMPACT_GATE : 0U) |
                                   (in->geometricFired ? L3_SHOT_IMPACT_GEOMETRY : 0U) |
                                   (in->rangeFired ? L3_SHOT_IMPACT_RANGE : 0U));
```

and line 79 `uint8_t fired = (uint8_t)(in->gateFired || in->geometricFired || in->rangeFired);`.

Both `l3_shot_format` (around line 156) and `l3_result_format` (l3_result.c around line 249) switch on the source; replace each switch with a call to one shared helper added to `l3_shot.h/.c`:

```c
/* "none", "gate", "geometry", "both" (gate+geometry), else the '+'-joined
 * names of the bits set, in the order gate, geometry, range. */
const char *l3_shot_source_name(uint8_t source, char *buf, uint32_t cap);
```

```c
const char *l3_shot_source_name(uint8_t source, char *buf, uint32_t cap)
{
    static const char *const kLegacy[4] = { "none", "gate", "geometry", "both" };
    static const char *const kBits[3] = { "gate", "geometry", "range" };
    uint32_t used = 0U;
    uint32_t i;

    if (source < 4U) {
        return kLegacy[source];
    }
    buf[0] = '\0';
    for (i = 0U; i < 3U; i++) {
        if (source & (1U << i)) {
            int32_t n = snprintf(buf + used, cap - used, "%s%s", used ? "+" : "", kBits[i]);

            if (n < 0 || (uint32_t)n >= cap - used) {
                break;
            }
            used += (uint32_t)n;
        }
    }
    return buf;
}
```

(each caller declares `char sourceText[24];` and passes `l3_shot_source_name(x->impactSource, sourceText, sizeof(sourceText))` where the switch's `source` string was used.)

`firmware_host.py`: `SHOT_IMPACT_GATE, SHOT_IMPACT_GEOMETRY, SHOT_IMPACT_RANGE = 1, 2, 4`; append `("rangeFired", ctypes.c_uint8)` as the last `ShotInput` field; signatures:

```python
    "l3_impact_update_range": ([_P(Impact), _P(FitEstimate), _U32], ctypes.c_int32),
    "l3_shot_source_name": ([ctypes.c_uint8, ctypes.c_char_p, _U32], ctypes.c_char_p),
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_iwr6843_firmware_impact_fit.py tests/ -k "shot or impact" -q -p no:cacheprovider`
Expected: all pass (existing shot/impact/result tests keep passing — the legacy source names are unchanged).

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_impact.h firmware/iwr6843/l3_impact.c firmware/iwr6843/l3_shot.h firmware/iwr6843/l3_shot.c firmware/iwr6843/l3_result.c src/openflight/iwr6843/firmware_host.py tests/
git commit -m "iwr: real-time impact from the club-in estimate, as its own shot source"
```

---

### Task 5: Replay wiring — band filter, range impact, ball armed at the band edge, fit at the end

**Files:**
- Modify: `src/openflight/iwr6843/firmware_replay.py`
- Test: `tests/test_iwr6843_firmware_replay.py` (append), `tests/test_iwr6843_firmware_band.py` (append the ball-tracker test)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `ReplayConfig.band_bins: float | None = None` — `None` uses `l3_impact_fit_cfg_defaults`' `bandBins`; `0` disables the band.
  - `@dataclass(frozen=True) class ImpactFitSummary: verdict: str; impact_us: float | None; spread_us: float; refined_minus_trigger_us: float | None; dropped: str | None; no_lock: bool; tracks: dict[str, TrackEstimateSummary]` and `TrackEstimateSummary(why: str, points: int, time_us: float | None, sigma_us: float | None, speed_mps: float)`.
  - `ReplayResult.band: tuple[float, float] | None` (lo, hi bins; None when disabled), `ReplayResult.range_frame: int | None`, `ReplayResult.impact_fit: ImpactFitSummary | None` (None when no impact was declared), `ReplayResult.impact_fit_status: str` (`l3_impact_fit_format`).
  - `format_report` prints `impact_fit_status` after `impact_status`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_iwr6843_firmware_band.py`:

```python
def test_ball_track_armed_at_band_edge_acquires_a_departing_ball(lib):
    """The ball's first points beyond the band are inside the tracker's
    origin gate only when it is armed at the band's far edge."""
    b = band(lib, 47.0, 6.0)
    cfg = fw.BallTrackCfg()
    lib.l3_ball_track_cfg_defaults(ctypes.byref(cfg))
    ball = fw.BallTrack()
    lib.l3_ball_track_init(ctypes.byref(ball), ctypes.byref(cfg))
    origin = fw.Vec3()
    lib.l3_ball_track_arm(ctypes.byref(ball), b.hiBin, ctypes.byref(origin), 0)
    acquired = False
    # 2.5 bins per 3 ms frame (39 m/s): inside the core's 3-bin association gate.
    for frame, bin_ in enumerate((54.5, 57.0, 59.5, 62.0), start=1):
        arr = targets(bin_)
        arr[0].frame, arr[0].timestampUs, arr[0].confidence = frame, frame * 3000, 0.9
        arr[0].dopplerAliasMps = 5.0
        n = lib.l3_band_filter(ctypes.byref(b), arr, 1)
        acquired |= bool(lib.l3_ball_track_update_joint(ctypes.byref(ball), arr, n, frame, frame * 3000, fw.TRACK_NO_TARGET))
    assert acquired
    assert ball.core.count >= 3
```

Append to `tests/test_iwr6843_firmware_replay.py` (it already loads committed recordings and has a `lib` fixture; reuse its recording/config helpers — `fr.recording_configs()` yields `(path, ReplayConfig)` pairs):

```python
def test_no_pre_impact_club_point_lies_inside_the_band_on_any_recording(lib):
    for path, config in fr.recording_configs():
        result = fr.replay_file(path, config, lib=lib)
        assert result.band is not None
        lo, hi = result.band
        impact_frame = result.fired_frame if result.fired_frame is not None else 10**9
        inside = [p for p in result.points if p.frame <= impact_frame and lo <= p.range_bin <= hi]
        assert not inside, f"{path.name}: club points in the band {inside}"


def test_band_zero_disables_the_filter(lib):
    path, config = next(iter(fr.recording_configs()))
    result = fr.replay_file(path, replace(config, band_bins=0.0), lib=lib)
    assert result.band is None


PRE_IMPACT_STATES = ("waiting_for_ball", "ready", "club_acquire", "club_track")


def test_impact_fit_is_reported_exactly_when_impact_is_declared(lib):
    declared_any = False
    for path, config in fr.recording_configs():
        result = fr.replay_file(path, config, lib=lib)
        declared = fw.SHOT_STATE_NAMES[result.shot.state] not in PRE_IMPACT_STATES
        declared_any |= declared
        assert (result.impact_fit is not None) == declared, path.name
        assert result.impact_fit_status.startswith("impactfit verdict=")
        assert "impactfit verdict=" in fr.format_report(result)
        if declared:
            assert result.impact_fit.verdict in fw.FIT_VERDICT_NAMES
            assert set(result.impact_fit.tracks) == set(fw.FIT_TRACK_NAMES)
    assert declared_any, "no recording reached impact: the test proves nothing"


def test_synthetic_shot_impact_lands_on_the_synthesized_time(lib):
    """tests/iwr6843_synth.py: club at 22 m/s to a known impact, then the ball
    leaving at 60 m/s. Point timestamps are frame starts, so allow half a
    4 ms frame of integration offset plus the 0.5 ms gate floor."""
    from iwr6843_synth import IMPACT_S, synth_shot_dump

    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372)
    config = fr.ReplayConfig(tee_bin=29, dest_bin=29, impact_armed=True)
    result = fr.replay_dump(raw, config, lib=lib)

    assert result.impact_fit is not None
    assert result.impact_fit.verdict in ("consistent", "single_track")
    assert result.impact_fit.tracks["ball_out"].why == "ok"
    assert result.impact_fit.impact_us == pytest.approx(IMPACT_S * 1e6, abs=2_500)
```

If the synthetic test misses by more than 2.5 ms, do not widen the tolerance: print `fr.format_report(result, points=True)` and find which estimate is off and why (a frame-timestamp convention difference is the first suspect).

(add `from dataclasses import replace` and `from openflight.iwr6843 import firmware_host as fw` to the file's imports if missing.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_replay.py -q -p no:cacheprovider`
Expected: the new replay tests FAIL (`ReplayResult has no attribute band`); the ball-track test passes or fails depending on the tracker — it must pass before moving on (it pins the arming rule the replay change relies on).

- [ ] **Step 3: Implement** (in `firmware_replay.py`)

1. `ReplayConfig`: add after `joint_search`:

```python
    # The tee band's half width in bins (l3_band.h): targets inside it are
    # dropped before any tracker sees them. None: the firmware default
    # (l3_impact_fit_cfg_defaults); 0 disables it.
    band_bins: float | None = None
```

2. Summaries, next to `LaunchSummary`:

```python
@dataclass(frozen=True)
class TrackEstimateSummary:
    """One track's impact estimate (l3_fit_estimate_t)."""

    why: str
    points: int
    time_us: float | None  # None unless the estimate was kept or dropped
    sigma_us: float | None
    speed_mps: float


@dataclass(frozen=True)
class ImpactFitSummary:
    """The three estimates and their fusion (l3_impact_fit_t)."""

    verdict: str
    impact_us: float | None
    spread_us: float
    refined_minus_trigger_us: float | None
    dropped: str | None
    no_lock: bool
    tracks: dict[str, TrackEstimateSummary]


def _impact_fit_summary(fit: fw.ImpactFit) -> ImpactFitSummary:
    tracks = {}
    for index, name in enumerate(fw.FIT_TRACK_NAMES):
        e = fit.track[index]
        why = fw.FIT_WHY_NAMES[e.why]
        timed = why in ("ok", "dropped")
        tracks[name] = TrackEstimateSummary(
            why,
            int(e.points),
            float(e.timeUs) if timed else None,
            float(e.sigmaUs) if timed else None,
            float(e.speedMps),
        )
    verdict = fw.FIT_VERDICT_NAMES[fit.verdict]
    decided = verdict != "none"
    return ImpactFitSummary(
        verdict=verdict,
        impact_us=float(fit.impactUs) if decided else None,
        spread_us=float(fit.spreadUs),
        refined_minus_trigger_us=float(fit.refinedMinusTriggerUs) if decided else None,
        dropped=fw.FIT_TRACK_NAMES[fit.droppedTrack] if fit.droppedTrack < 3 else None,
        no_lock=bool(fit.noLock),
        tracks=tracks,
    )
```

3. `ReplayResult`: add fields (with defaults, after `joint_confirmed`):

```python
    band: tuple[float, float] | None = None
    range_frame: int | None = None  # the range-only impact's fire
    impact_fit: ImpactFitSummary | None = None
    impact_fit_status: str = ""
```

4. In `replay_dump`, after the `impact` setup:

```python
    fit_cfg = fw.ImpactFitCfg()
    lib.l3_impact_fit_cfg_defaults(ctypes.byref(fit_cfg))
    fit_cfg.binWidthM = RANGE_SPAN_M / config.fft_size
    if config.band_bins is not None:
        fit_cfg.bandBins = config.band_bins
    band = fw.Band()
    lib.l3_band_around(float(config.destination), fit_cfg.bandBins, ctypes.byref(band))
    range_impact = fw.Impact()
    lib.l3_impact_init(ctypes.byref(range_impact), ctypes.byref(impact_cfg))
    range_frame: int | None = None
    ball_arm_bin = float(band.hiBin) if band.valid else float(destination)
```

(`destination` is assigned further down — move `destination = config.destination` up above this block.)

5. Pre-impact frame, immediately after `found = lib.l3_obs_extract(...)` (the call that feeds `l3_track_update`): `found = lib.l3_band_filter(ctypes.byref(band), targets, found)`.

6. After `geometric = lib.l3_impact_update(...)`:

```python
        club_span = fw.FitSpan(ctypes.pointer(track), 0, track.count)
        club_in = fw.FitEstimate()
        lib.l3_impact_fit_track(
            ctypes.byref(fit_cfg), 0, fw.fit_reader(lib, "l3_fit_span_point"),
            ctypes.byref(club_span), track.count, destination * bin_width_m, ctypes.byref(club_in),
        )
        ranged = lib.l3_impact_update_range(ctypes.byref(range_impact), ctypes.byref(club_in), timestamp_us)
        if ranged and range_frame is None:
            range_frame = frame
```

and in the shot input: `shot_in.rangeFired = 1 if (ranged and config.impact_armed) else 0`; the impact time becomes

```python
        if geometric and config.impact_armed:
            shot_in.impactTimestampUs = int(impact.impactTimestampUs)
        elif ranged and config.impact_armed:
            shot_in.impactTimestampUs = int(range_impact.impactTimestampUs)
        else:
            shot_in.impactTimestampUs = timestamp_us
```

and `ended` at the loop top also ends on `config.impact_armed and range_frame is not None`.

7. Both `lib.l3_ball_track_arm(...)` calls: replace `float(destination)` with `ball_arm_bin`.

8. `_replay_post_frame` and `_joint_post_frame`: add a `band` parameter and, right after each `l3_obs_extract` there, `found = lib.l3_band_filter(ctypes.byref(band), targets, found)` (joint: on `joint_targets`); pass `band` from both call sites.

9. Before `return ReplayResult(...)`:

```python
    fit = fw.ImpactFit()
    impact_declared = fw.SHOT_STATE_NAMES[shot.state] not in ("waiting_for_ball", "ready", "club_acquire", "club_track")
    if impact_declared:
        club_in_list = fw.FitList(
            ctypes.cast(shot.clubTrajectory, ctypes.POINTER(fw.TrackPoint)), shot.clubPoints
        )
        club_out = fw.FitSpan()
        lib.l3_fit_span_after(ctypes.byref(track), shot.impactFrame, ctypes.byref(club_out))
        ball_out = fw.FitSpan(ctypes.pointer(ball_track.core), 0, ball_track.core.count)
        lib.l3_impact_fit_run(
            ctypes.byref(fit_cfg), ctypes.byref(club_in_list), ctypes.byref(club_out),
            ctypes.byref(ball_out), destination * bin_width_m,
            0 if config.dest_bin is not None else 1, shot.impactTimestampUs, ctypes.byref(fit),
        )
    else:
        lib.l3_impact_fit_reset(ctypes.byref(fit))
```

and pass `band=(float(band.loBin), float(band.hiBin)) if band.valid else None, range_frame=range_frame, impact_fit=_impact_fit_summary(fit) if impact_declared else None, impact_fit_status=fw.c_text(lib.l3_impact_fit_format, ctypes.byref(fit), cap=240)` to `ReplayResult`.

10. `format_report`: after `f"  {result.impact_status}",` add `f"  {result.impact_fit_status}",`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_replay.py tests/test_evaluate_iwr_tracking.py -q -p no:cacheprovider`
Expected: all pass. If an existing replay expectation (manifest `expect` values) now differs because the band changed a track, do **not** loosen it silently: print both reports (`fr.format_report`) for that recording, and bring the difference to the user before editing the manifest.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/firmware_replay.py tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_replay.py
git commit -m "iwr: replay keeps the trackers out of the tee band and fits impact either side"
```

---

### Task 6: Board wiring in `l3_dump.c`

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c`
- Test: `tests/test_iwr6843_firmware_board_wiring.py` (new; source-inspection, as `tests/test_iwr6843_firmware_sparse.py:676` already does for `l3_dump.c`, which the host cannot compile)

**Interfaces:**
- Consumes: Tasks 1–4; the replay (Task 5) is the behavioural reference — the board must make the same calls in the same order.
- Produces: globals `gImpactFitCfg`, `gBand`, `gRangeImpact`, `gImpactFit`; the `impactfit` line in the status output.

- [ ] **Step 1: Write the failing test**

```python
"""l3_dump.c wires the band, the range-only impact and the impact fit the
way firmware_replay does. The board file needs TI headers, so this reads its
source; the behaviour is tested through the replay."""

from __future__ import annotations

import re

from openflight.iwr6843.firmware_host import FIRMWARE_DIR

SOURCE = (FIRMWARE_DIR / "l3_dump.c").read_text(encoding="utf-8")


def body(name: str) -> str:
    match = re.search(rf"static \w+ {name}\([^)]*\)\s*\{{(.*?)\n\}}", SOURCE, re.S)
    assert match, name
    return match.group(1)


def test_pre_impact_targets_are_band_filtered_before_the_club_track():
    self_trigger = body("l3_considerSelfTrigger")
    assert self_trigger.index("l3_band_filter(&gBand, targets, found)") < self_trigger.index("l3_track_update(")


def test_range_impact_runs_every_pre_impact_frame_and_feeds_the_shot():
    self_trigger = body("l3_considerSelfTrigger")
    assert "l3_impact_update_range(&gRangeImpact, &clubIn," in self_trigger
    observe = body("l3_shotObserve")
    assert "in.rangeFired" in observe


def test_post_impact_targets_are_band_filtered():
    assert "l3_band_filter(&gBand, targets, found)" in body("l3_considerBallTrack")


def test_ball_tracker_is_armed_at_the_band_edge():
    assert "l3_ball_track_arm(&gBallTrack, l3_ballArmBin(teeBin)," in body("l3_shotObserve")


def test_impact_fit_runs_before_the_result_is_built():
    ball_track = body("l3_considerBallTrack")
    assert ball_track.index("l3_impactFitRun()") < ball_track.index("l3_result_build(")


def test_rearm_forgets_the_range_impact():
    assert "l3_impact_rearm(&gRangeImpact);" in body("l3_trigRearm")


def test_new_modules_are_in_the_board_image():
    makefile = (FIRMWARE_DIR / "makefile").read_text(encoding="utf-8")
    assert "l3_band.c" in makefile and "l3_impact_fit.c" in makefile
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_board_wiring.py -v`
Expected: FAIL on every test but the makefile one.

- [ ] **Step 3: Implement** (in `l3_dump.c`)

1. Includes: `#include "l3_band.h"` and `#include "l3_impact_fit.h"` beside `#include "l3_impact.h"`.
2. Globals beside `gImpact`:

```c
static l3_impact_fit_cfg_t gImpactFitCfg;
static l3_band_t           gBand;
static l3_impact_t         gRangeImpact;   /* range-only fire (l3_impact_update_range) */
static l3_impact_fit_t     gImpactFit;
```

3. Where `l3_impact_init(&gImpact, &gImpactCfg);` runs (≈ line 3447): add

```c
    l3_impact_fit_cfg_defaults(&gImpactFitCfg);
    gImpactFitCfg.binWidthM = cfg.binWidthM;
    l3_impact_init(&gRangeImpact, &gImpactCfg);
    l3_impact_fit_reset(&gImpactFit);
```

4. `l3_trigRearm`: after `l3_impact_rearm(&gImpact);` add `l3_impact_rearm(&gRangeImpact);` and `l3_impact_fit_reset(&gImpactFit);`.
5. `l3_considerSelfTrigger`, after `found = l3_obs_extract(...)` and before `gClubTrackDest = teeBin;`:

```c
        l3_band_around((float)teeBin, gImpactFitCfg.bandBins, &gBand);
        found = l3_band_filter(&gBand, targets, found);
```

and after `geometric = l3_impact_update(&gImpact, &gDelivery, &gBallPosition, 1U);`:

```c
        {
            l3_fit_span_t clubSpan = { &gClubTrack, 0U, gClubTrack.count };
            l3_fit_estimate_t clubIn;

            l3_impact_fit_track(&gImpactFitCfg, L3_FIT_CLUB_IN, l3_fit_span_point, &clubSpan,
                                gClubTrack.count, (float)teeBin * gClubTrack.cfg.binWidthM,
                                &clubIn);
            ranged = l3_impact_update_range(&gRangeImpact, &clubIn,
                                            gPreFramesCaptured * (uint32_t)gFramePeriodUs);
        }
```

declare `int32_t ranged = 0;` with the function's other locals; change the fire bookkeeping to include it:

```c
    gTrigFireSource = (uint8_t)((fired ? 1U : 0U) | (geometric ? 2U : 0U) | (ranged ? 4U : 0U));
    l3_shotObserve(teeBin, fired, geometric && gImpactArmed, ranged && gImpactArmed);
    if ((geometric || ranged) && gImpactArmed) {
        fired = 1;
    }
```

6. `l3_shotObserve` gains `int32_t ranged`: `in.rangeFired = (uint8_t)(ranged ? 1U : 0U);`; the impact time:

```c
    if (geometric && gImpact.fired) {
        in.impactTimestampUs = gImpact.impactTimestampUs;
    } else if (ranged && gRangeImpact.fired) {
        in.impactTimestampUs = gRangeImpact.impactTimestampUs;
    } else {
        in.impactTimestampUs = frameUs;
    }
```

and the arm call becomes `l3_ball_track_arm(&gBallTrack, l3_ballArmBin(teeBin), &gBallPosition, in.impactTimestampUs);` with, above `l3_shotObserve`:

```c
/* The ball tracker acquires 1..originGateBins beyond its origin; with the
 * tee band dropping every target inside it, the origin is the band's far
 * edge, else the ball's bin. */
static float l3_ballArmBin(uint32_t teeBin)
{
    return gBand.valid ? gBand.hiBin : (float)teeBin;
}
```

7. `l3_considerBallTrack`: after `found = l3_obs_extract(...)`: `found = l3_band_filter(&gBand, targets, found);`. Before `l3_result_build(...)`: `l3_impactFitRun();`, with, above the function:

```c
/* SOLVE: impact from the tracks either side of the band; a verdict other
 * than none replaces the frozen impact time. */
static void l3_impactFitRun(void)
{
    l3_fit_list_t clubIn = { gShot.clubTrajectory, gShot.clubPoints };
    l3_fit_span_t clubOut;
    l3_fit_span_t ballOut = { &gBallTrack.core, 0U, gBallTrack.core.count };

    l3_fit_span_after(&gClubTrack, gShot.impactFrame, &clubOut);
    l3_impact_fit_run(&gImpactFitCfg, &clubIn, &clubOut, &ballOut,
                      (float)gClubTrackDest * gClubTrack.cfg.binWidthM,
                      (uint8_t)(gTrigDestBall ? 0U : 1U), gShot.impactTimestampUs, &gImpactFit);
    if (gImpactFit.verdict != L3_FIT_VERDICT_NONE && gImpactFit.impactUs > 0.0F) {
        gShot.impactTimestampUs = (uint32_t)(gImpactFit.impactUs + 0.5F);
    }
}
```

8. Status output: where `l3_impact_format(&gImpact, line, sizeof(line))` is printed (grep `l3_impact_format(` in `l3_dump.c`), print `l3_impact_fit_format(&gImpactFit, line, sizeof(line))` on the next line the same way.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_iwr6843_firmware_board_wiring.py tests/test_iwr6843_firmware_sparse.py -v -p no:cacheprovider`
Expected: all pass. Then build the board image (TI toolchain, on the machine that has it) and confirm `DATA_RAM` still fits: `make -C firmware/iwr6843` and check the linker's `DATA_RAM` usage in `l3_dump_mss.map`; report the before/after bytes in the commit message.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_dump.c tests/test_iwr6843_firmware_board_wiring.py
git commit -m "iwr: board keeps the trackers out of the tee band and refines impact at SOLVE"
```

---

### Task 7: Result packet v2 carries the impact fit

**Files:**
- Modify: `firmware/iwr6843/l3_result.h`, `firmware/iwr6843/l3_result.c`, `firmware/iwr6843/l3_dump.c` (the one `l3_result_build` call), `src/openflight/iwr6843/firmware_host.py`, `src/openflight/iwr6843/shot_result.py`
- Test: `tests/test_iwr6843_firmware_result.py` (update calls, append), `tests/test_iwr6843_shot_result.py` (or wherever `parse_packet` is tested — `grep -ln parse_packet tests/`; append there), `tests/test_iwr6843_driver.py:658` (uses `fw.RESULT_PACKET_BYTES`; must keep passing)

**Interfaces:**
- Consumes: `l3_impact_fit_t` (Task 2/3).
- Produces:
  - `L3_RESULT_VERSION 2U`, `L3_RESULT_PACKET_BYTES 164U`, `L3_RESULT_V1_PACKET_BYTES 100U`; `l3_shot_result_t` gains `l3_impact_fit_t impactFit;` as its last field.
  - `void l3_result_build(const l3_shot_t *shot, const l3_ball_track_t *ball, const l3_launch_t *launch, const l3_impact_fit_t *fit, uint32_t shotId, uint8_t ballLocked, l3_shot_result_t *out);` — `fit` may be NULL (reset fit).
  - Wire layout appended after the v1 100 bytes: `u8 verdict, u8 droppedTrack, u8 noLock, u8 pad; f32 impactUs, f32 spreadUs, f32 refinedMinusTriggerUs;` then per track (club_in, club_out, ball_out) `u8 why, u8 points, u16 pad, f32 timeUs, f32 sigmaUs, f32 speedMps` — 64 bytes.
  - Python: `ShotResultPacket.impact_fit: dict | None` — `{"verdict", "impact_us", "spread_us", "refined_minus_trigger_us", "dropped", "no_lock", "tracks": {name: {"why", "points", "time_us", "sigma_us", "speed_mps"}}}`; `None` for a v1 packet. `to_dict()` includes `"impact_fit"`. `parse_packet` accepts v1 (100 bytes) and v2 (164 bytes).

- [ ] **Step 1: Write the failing tests**

In `tests/test_iwr6843_firmware_result.py` update every `lib.l3_result_build(` call to pass `None` as the new fourth argument (after the launch pointer), then append:

```python
def test_packet_v2_carries_the_impact_fit(lib):
    fit = fw.ImpactFit()
    lib.l3_impact_fit_reset(ctypes.byref(fit))
    fit.verdict = fw.FIT_VERDICT_NAMES.index("consistent")
    fit.impactUs, fit.spreadUs, fit.refinedMinusTriggerUs = 30_000.0, 210.0, -2_500.0
    fit.track[2].why, fit.track[2].points = 0, 4
    fit.track[2].timeUs, fit.track[2].sigmaUs, fit.track[2].speedMps = 29_990.0, 60.0, 61.2
    shot = fw.Shot()
    ball = fw.BallTrack()
    launch = fw.Launch()
    result = fw.ShotResult()
    lib.l3_result_build(ctypes.byref(shot), ctypes.byref(ball), ctypes.byref(launch), ctypes.byref(fit), 7, 1, ctypes.byref(result))
    buffer = ctypes.create_string_buffer(fw.RESULT_PACKET_BYTES)
    assert lib.l3_result_serialize(ctypes.byref(result), buffer, fw.RESULT_PACKET_BYTES) == 164
    raw = buffer.raw

    parsed = shot_result.parse_packet(raw)

    assert parsed.version == 2
    assert parsed.impact_fit["verdict"] == "consistent"
    assert parsed.impact_fit["impact_us"] == pytest.approx(30_000.0)
    assert parsed.impact_fit["refined_minus_trigger_us"] == pytest.approx(-2_500.0)
    assert parsed.impact_fit["dropped"] is None
    ball_out = parsed.impact_fit["tracks"]["ball_out"]
    assert (ball_out["why"], ball_out["points"]) == ("ok", 4)
    assert ball_out["speed_mps"] == pytest.approx(61.2)
    assert parsed.impact_fit["tracks"]["club_in"]["why"] == "missing"
    assert parsed.to_dict()["impact_fit"]["verdict"] == "consistent"


def test_null_fit_serialises_as_verdict_none(lib):
    result = fw.ShotResult()
    lib.l3_result_build(ctypes.byref(fw.Shot()), ctypes.byref(fw.BallTrack()), ctypes.byref(fw.Launch()), None, 1, 0, ctypes.byref(result))
    buffer = ctypes.create_string_buffer(fw.RESULT_PACKET_BYTES)
    lib.l3_result_serialize(ctypes.byref(result), buffer, fw.RESULT_PACKET_BYTES)
    assert shot_result.parse_packet(buffer.raw).impact_fit["verdict"] == "none"
```

(import `from openflight.iwr6843 import shot_result` at the top if missing.) In the parse tests file append:

```python
def test_v1_packet_still_parses_without_an_impact_fit():
    v1 = struct.pack("<II9fII9fIBBBBf", 1, 3, *([0.0] * 9), 0, 0, *([0.0] * 9), 12345, 2, 1, 5, 4, 1.4)
    parsed = shot_result.parse_packet(v1)
    assert parsed.version == 1
    assert parsed.impact_fit is None
    assert parsed.impact_timestamp_us == 12345
    assert parsed.to_dict()["impact_fit"] is None


def test_wrong_size_or_version_is_refused():
    with pytest.raises(ValueError, match="bytes"):
        shot_result.parse_packet(b"\x02\x00\x00\x00" + bytes(96))
    with pytest.raises(ValueError, match="version"):
        shot_result.parse_packet(struct.pack("<I", 9) + bytes(160))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_result.py tests/ -k "packet or result" -q -p no:cacheprovider`
Expected: FAIL (argument count, size 100 ≠ 164, no `impact_fit`).

- [ ] **Step 3: Implement**

`l3_result.h`: `#include "l3_impact_fit.h"`; version/size defines as above; add `l3_impact_fit_t impactFit;` after `float smash;`; update the `l3_result_build` prototype and the header comment ("Version 2: version 1's 100 bytes and the impact fit, 64 bytes").

`l3_result.c`: in `l3_result_build`, first lines after the existing `memset(out, …)`:

```c
    if (fit != NULL) {
        out->impactFit = *fit;
    } else {
        l3_impact_fit_reset(&out->impactFit);
    }
```

and in `l3_result_serialize`, change the cap check to `L3_RESULT_PACKET_BYTES` (already) and append after the smash:

```c
    *p++ = result->impactFit.verdict;
    *p++ = result->impactFit.droppedTrack;
    *p++ = result->impactFit.noLock;
    *p++ = 0U;
    p = l3_result_putF32(p, result->impactFit.impactUs);
    p = l3_result_putF32(p, result->impactFit.spreadUs);
    p = l3_result_putF32(p, result->impactFit.refinedMinusTriggerUs);
    for (i = 0U; i < L3_FIT_TRACKS; i++) {
        const l3_fit_estimate_t *e = &result->impactFit.track[i];

        *p++ = e->why;
        *p++ = (uint8_t)((e->points > 255U) ? 255U : e->points);
        *p++ = 0U;
        *p++ = 0U;
        p = l3_result_putF32(p, e->timeUs);
        p = l3_result_putF32(p, e->sigmaUs);
        p = l3_result_putF32(p, e->speedMps);
    }
```

`l3_dump.c`: `l3_result_build(&gShot, &gBallTrack, &gLaunch, &gImpactFit, ++gShotId, gTrigDestBall, &gShotResult);` (and update the Task 6 wiring test string if it matched the old call).

`firmware_host.py`: `RESULT_VERSION = 2`; `RESULT_PACKET_BYTES = 164`; `RESULT_V1_PACKET_BYTES = 100`; `ShotResult._fields_` gains `("impactFit", ImpactFit)` last (move `ImpactFit` and its dependencies above `ShotResult` if the class order requires); `l3_result_build` signature gains `_P(ImpactFit)` after `_P(Launch)`.

`shot_result.py`:

```python
PACKET_V1 = struct.Struct("<II9fII9fIBBBBf")
IMPACT_FIT = struct.Struct("<BBBBfff" + "BBHfff" * 3)
PACKET_V2_SIZE = PACKET_V1.size + IMPACT_FIT.size
assert PACKET_V1.size == fw.RESULT_V1_PACKET_BYTES
assert PACKET_V2_SIZE == fw.RESULT_PACKET_BYTES
_SIZES = {1: PACKET_V1.size, 2: PACKET_V2_SIZE}
```

(replace the old `PACKET` uses; `IMPACT_SOURCES` gains the range names: `{0: "none", 1: "gate", 2: "geometry", 3: "both", 4: "range", 5: "gate+range", 6: "geometry+range", 7: "gate+geometry+range"}`.) `ShotResultPacket` gains `impact_fit: dict | None = None` (last field, defaulted) and `to_dict` adds `"impact_fit": self.impact_fit`. `parse_packet`:

```python
def parse_packet(raw: bytes) -> ShotResultPacket:
    """Decode one packet, version 1 or 2; raises ValueError on a wrong size or version."""
    if len(raw) < 4:
        raise ValueError(f"shot result packet is {len(raw)} bytes")
    (version,) = struct.unpack_from("<I", raw)
    if version not in _SIZES:
        raise ValueError(f"shot result version {version}, this host reads {sorted(_SIZES)}")
    if len(raw) != _SIZES[version]:
        raise ValueError(f"shot result v{version} packet is {len(raw)} bytes, expected {_SIZES[version]}")
    fields = PACKET_V1.unpack_from(raw)
    ...  # the existing v1 decoding, unchanged, building `packet`
    impact_fit = _impact_fit(IMPACT_FIT.unpack_from(raw, PACKET_V1.size)) if version >= 2 else None
    return replace(packet, impact_fit=impact_fit)
```

with

```python
def _impact_fit(fields: tuple) -> dict:
    verdict, dropped, no_lock, _pad, impact_us, spread_us, dtrig_us = fields[:7]
    tracks = {}
    for index, name in enumerate(fw.FIT_TRACK_NAMES):
        why, points, _pad2, time_us, sigma_us, speed = fields[7 + 6 * index : 13 + 6 * index]
        why_name = fw.FIT_WHY_NAMES[why] if why < len(fw.FIT_WHY_NAMES) else "?"
        timed = why_name in ("ok", "dropped")
        tracks[name] = {
            "why": why_name,
            "points": points,
            "time_us": time_us if timed else None,
            "sigma_us": sigma_us if timed else None,
            "speed_mps": speed,
        }
    verdict_name = fw.FIT_VERDICT_NAMES[verdict] if verdict < len(fw.FIT_VERDICT_NAMES) else "?"
    decided = verdict_name not in ("none", "?")
    return {
        "verdict": verdict_name,
        "impact_us": impact_us if decided else None,
        "spread_us": spread_us,
        "refined_minus_trigger_us": dtrig_us if decided else None,
        "dropped": fw.FIT_TRACK_NAMES[dropped] if dropped < len(fw.FIT_TRACK_NAMES) else None,
        "no_lock": bool(no_lock),
        "tracks": tracks,
    }
```

(import `replace` from `dataclasses`; restructure the existing body so the v1 decode builds `packet` instead of returning directly.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_iwr6843_firmware_result.py tests/test_iwr6843_driver.py tests/ -k "result or packet or driver" -q -p no:cacheprovider`
Expected: all pass (the driver test builds a `fw.RESULT_PACKET_BYTES` packet: if it fills fields assuming 100 bytes, extend it so byte 0 is version 2 — keep its assertions).

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_result.h firmware/iwr6843/l3_result.c firmware/iwr6843/l3_dump.c src/openflight/iwr6843/firmware_host.py src/openflight/iwr6843/shot_result.py tests/
git commit -m "iwr: result packet v2 carries the impact fit; the Pi reads v1 and v2"
```

---

### Task 8: Dump viewer — band, fitted lines, impact time

**Files:**
- Modify: `src/openflight/iwr6843/dump_viewer.py`, `scripts/iwr6843/dump_viewer.html`
- Test: `tests/test_iwr6843_dump_viewer.py` (append; `grep -ln dump_viewer tests/` for the existing file)

**Interfaces:**
- Consumes: `ReplayResult.band`, `.impact_fit`, `.range_frame` (Task 5).
- Produces: the viewer JSON's firmware object gains `"band": [lo, hi] | null`, `"range_frame": int | null`, `"impact_fit": {...} | null` where `impact_fit` is `dataclasses.asdict(result.impact_fit)` plus, per kept track, `"line": [[t_us, range_m], [t_us, range_m]]` — the fitted line from its first fitted point's time to its crossing, at the ball's range at the crossing.

- [ ] **Step 1: Write the failing test** (append to `tests/test_iwr6843_dump_viewer.py`, beside `test_a_whole_shot_carries_the_gate_the_tracks_and_their_3d_points`, reusing its imports, `needs_compiler`, `TEE_BIN` and `TEE_RANGE_M`):

```python
@needs_compiler
def test_a_whole_shot_carries_the_band_and_the_impact_fit():
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    data = dv.analyze_dump(
        raw, dv.ViewerOptions(tee_bin=TEE_BIN, dest_bin=TEE_BIN, tee_range_m=TEE_RANGE_M)
    )
    json.dumps(data, allow_nan=False)
    firmware = data["firmware"]
    assert firmware["ok"], firmware.get("error")
    lo, hi = firmware["band"]
    assert lo < TEE_BIN < hi
    fit = firmware["impact_fit"]
    assert fit is not None and fit["verdict"] in fw.FIT_VERDICT_NAMES
    ball_m = firmware["ball_range_m"]
    assert ball_m == pytest.approx(TEE_BIN * 6.0 / 128)
    drawn = 0
    for track in fit["tracks"].values():
        if track["why"] != "ok":
            assert track["line"] is None
            continue
        (t0, r0), (t1, r1) = track["line"]
        assert t1 == pytest.approx(track["time_us"])
        assert r1 == pytest.approx(ball_m)
        assert r0 == pytest.approx(ball_m + track["speed_mps"] * (t0 - t1) * 1e-6)
        drawn += 1
    assert drawn >= 1
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_dump_viewer.py -k band -v` → FAIL (`KeyError: 'band'`).

- [ ] **Step 3: Implement** — in `firmware_section` (`dump_viewer.py:203`), add to the returned dict:

```python
        "band": list(result.band) if result.band is not None else None,
        "range_frame": result.range_frame,
        "ball_range_m": result.config.destination * bin_width_m(result.config.fft_size),
        "impact_fit": _impact_fit_json(result),
```

with, above `firmware_section`:

```python
# The drawn segment of a fitted track: from this long before its crossing.
FIT_LINE_SPAN_US = 12_000.0  # the K = 4 points at 3 ms frames


def _impact_fit_json(result: fr.ReplayResult) -> dict | None:
    """The impact fit with, per kept track, the fitted line as two (t_us, range_m) points."""
    if result.impact_fit is None:
        return None
    ball_m = result.config.destination * bin_width_m(result.config.fft_size)
    out = _jsonable(result.impact_fit)
    for name, track in result.impact_fit.tracks.items():
        line = None
        if track.why == "ok" and track.time_us is not None:
            t0 = track.time_us - FIT_LINE_SPAN_US
            line = [[t0, ball_m + track.speed_mps * (t0 - track.time_us) * 1e-6], [track.time_us, ball_m]]
        out["tracks"][name]["line"] = line
    return out
```

(`_jsonable` must turn the dataclass into a dict, as it does for `result.launch`.) In `dump_viewer.html` `renderMap()`, after the ball-track traces:

```javascript
    if (F.band) shapes.push({ type: "rect", xref: "x", yref: "paper", x0: binM(F.band[0]), x1: binM(F.band[1]), y0: 0, y1: 1, fillcolor: "rgba(255,105,180,.10)", line: { width: 0 } });
    if (F.impact_fit) {
      const colors = { club_in: C.club, club_out: C.jclub, ball_out: C.ball };
      Object.entries(F.impact_fit.tracks).forEach(([name, t]) => {
        if (!t.line) return;
        traces.push({ type: "scatter", mode: "lines", name: `${name} fit`, x: t.line.map((p) => p[1]), y: t.line.map((p) => p[0] / 1000),
          line: { color: colors[name], width: 2, dash: "dash" }, hovertemplate: `${name} ${t.why} %{y:.2f} ms<extra></extra>` });
      });
      if (F.impact_fit.impact_us != null) shapes.push({ type: "line", xref: "paper", yref: "y", x0: 0, x1: 1,
        y0: F.impact_fit.impact_us / 1000, y1: F.impact_fit.impact_us / 1000, line: { color: "#ffd43b", width: 2 } });
    }
```

(the `shapes` array is declared later in `renderMap` — move its declaration above the firmware block.)

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_iwr6843_dump_viewer.py -v` → pass. Then open the viewer on `C:\Users\corma\Desktop\OF Sessions\iwr6843\iwr6843_20260928_170404_762_001.l3dump` with the project's viewer command (see the module docstring of `dump_viewer.py`) and screenshot the Range × time panel.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/dump_viewer.py scripts/iwr6843/dump_viewer.html tests/test_iwr6843_dump_viewer.py
git commit -m "iwr: viewer draws the tee band, the fitted tracks and the impact time"
```

---

### Task 9: Impact evaluation — method C comparator, metrics, acceptance

**Files:**
- Create: `src/openflight/iwr6843/impact_eval.py`
- Modify: `scripts/analysis/evaluate_iwr_tracking.py`
- Test: `tests/test_iwr6843_impact_eval.py`

**Interfaces:**
- Consumes: `ReplayResult.impact_fit`, `.points`, `.ball_points`, `.band`, `.config` (Task 5).
- Produces:
  - `joint_fit_impact(club_in, club_out, ball_out, ball_range_m, *, step_us=50.0) -> float | None` — method C: tracks as `[(t_us, r_m)]` (K nearest the band, same selection as A); grid over `t_i`; each track's slope by least squares through the anchor `(t_i, ball_range_m)`; returns the `t_i` with the smallest total squared residual, `None` with fewer than two tracks of ≥ 3 points.
  - `leave_one_out_spread_us(club_in, club_out, ball_out, ball_range_m) -> float | None` — for captures with all three tracks: `max - min` of `joint_fit_impact` over the three two-track subsets (method C's counterpart to A's spread).
  - `@dataclass ImpactOutcome(name: str, verdict: str, tracks_ok: tuple[str, ...], spread_us: float | None, refined_minus_trigger_us: float | None, c_spread_us: float | None, club_points_in_band: int)`
  - `impact_outcome(name, result) -> ImpactOutcome`; `summarize_impact(outcomes) -> dict` with keys `captures`, `with_estimate`, `consistent`, `inconsistent`, `none`, `median_spread_us`, `median_c_spread_us`, `median_refined_minus_trigger_us`, `club_points_in_band`, `c_wins` (bool: `median_c_spread_us <= 0.7 * median_spread_us` and C available on at least as many captures).
  - `evaluate_iwr_tracking.py --impact` prints the summary and, with `--json`, writes it under `"impact"`.

- [ ] **Step 1: Write the failing tests**

```python
"""Impact evaluation: method C and the A-vs-C metrics (impact_eval.py)."""

from __future__ import annotations

import pytest

from openflight.iwr6843 import impact_eval as ie

BALL_M = 2.20
IMPACT_US = 30_000.0


def line(speed, times, offset_us=0.0):
    return [(t, BALL_M + speed * (t - IMPACT_US - offset_us) * 1e-6) for t in times]


CLUB_IN = line(30.0, (9_000, 12_000, 15_000, 18_000))
CLUB_OUT = line(25.0, (42_000, 45_000, 48_000, 51_000))
BALL_OUT = line(60.0, (36_000, 39_000, 42_000, 45_000))


def test_joint_fit_recovers_impact_on_clean_tracks():
    assert ie.joint_fit_impact(CLUB_IN, CLUB_OUT, BALL_OUT, BALL_M) == pytest.approx(IMPACT_US, abs=50)


def test_joint_fit_needs_two_tracks_of_three_points():
    assert ie.joint_fit_impact(CLUB_IN, [], [], BALL_M) is None
    assert ie.joint_fit_impact(CLUB_IN, CLUB_OUT[:2], [], BALL_M) is None
    assert ie.joint_fit_impact(CLUB_IN, [], BALL_OUT, BALL_M) == pytest.approx(IMPACT_US, abs=50)


def test_leave_one_out_spread_is_small_when_tracks_agree_and_large_when_not():
    agree = ie.leave_one_out_spread_us(CLUB_IN, CLUB_OUT, BALL_OUT, BALL_M)
    assert agree is not None and agree <= 100
    skewed = line(60.0, (36_000, 39_000, 42_000, 45_000), offset_us=6_000)
    assert ie.leave_one_out_spread_us(CLUB_IN, CLUB_OUT, skewed, BALL_M) > 2_000


def test_leave_one_out_spread_needs_all_three_tracks():
    assert ie.leave_one_out_spread_us(CLUB_IN, CLUB_OUT, [], BALL_M) is None


def outcome(verdict, spread, c_spread, dtrig=-3_000.0, in_band=0, tracks=("club_in", "ball_out")):
    return ie.ImpactOutcome("x", verdict, tracks, spread, dtrig, c_spread, in_band)


def test_summary_counts_verdicts_and_medians():
    s = ie.summarize_impact(
        [
            outcome("consistent", 200.0, 150.0),
            outcome("consistent", 400.0, 250.0),
            outcome("inconsistent", 5_000.0, None),
            outcome("none", None, None, dtrig=None, tracks=()),
        ]
    )
    assert s["captures"] == 4 and s["with_estimate"] == 3
    assert (s["consistent"], s["inconsistent"], s["none"]) == (2, 1, 1)
    assert s["median_spread_us"] == pytest.approx(300.0)  # consistent captures only
    assert s["median_c_spread_us"] == pytest.approx(200.0)
    assert s["club_points_in_band"] == 0


def test_c_wins_only_with_a_thirty_percent_cut():
    better = ie.summarize_impact([outcome("consistent", 1_000.0, 690.0)])
    worse = ie.summarize_impact([outcome("consistent", 1_000.0, 710.0)])
    assert better["c_wins"] is True and worse["c_wins"] is False
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_impact_eval.py -v` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `src/openflight/iwr6843/impact_eval.py`:

```python
"""Impact evaluation over replayed captures: method A (the firmware's
l3_impact_fit, read from the replay) against method C, one joint fit sharing
the impact time, implemented here only as a comparator.

See docs/superpowers/specs/2026-09-28-iwr-impact-back-interpolation-design.md,
"Method gate: A vs C".
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np

Track = Sequence[tuple[float, float]]  # (t_us, range_m)
FIT_POINTS = 4
MIN_POINTS = 3
SEARCH_MARGIN_US = 20_000.0
C_WIN_FACTOR = 0.7  # C must cut the median spread by at least 30 %


def _anchored_rss(track: Track, t_i: float, ball_range_m: float) -> float:
    dt = np.array([t - t_i for t, _ in track]) * 1e-6
    dr = np.array([r - ball_range_m for _, r in track])
    denominator = float(dt @ dt)
    if denominator <= 0.0:
        return float(dr @ dr)
    slope = float(dt @ dr) / denominator
    residual = dr - slope * dt
    return float(residual @ residual)


def joint_fit_impact(
    club_in: Track, club_out: Track, ball_out: Track, ball_range_m: float, *, step_us: float = 50.0
) -> float | None:
    """Method C: the impact time shared by every track, each a line through
    (t_i, ball range); None with fewer than two tracks of MIN_POINTS."""
    tracks = [
        list(club_in)[-FIT_POINTS:],
        list(club_out)[:FIT_POINTS],
        list(ball_out)[:FIT_POINTS],
    ]
    tracks = [t for t in tracks if len(t) >= MIN_POINTS]
    if len(tracks) < 2:
        return None
    times = [t for track in tracks for t, _ in track]
    grid = np.arange(min(times) - SEARCH_MARGIN_US, max(times) + step_us, step_us)
    costs = [sum(_anchored_rss(track, t_i, ball_range_m) for track in tracks) for t_i in grid]
    return float(grid[int(np.argmin(costs))])


def leave_one_out_spread_us(
    club_in: Track, club_out: Track, ball_out: Track, ball_range_m: float
) -> float | None:
    """Method C's spread: max - min of the fit on each two-track subset;
    None unless all three tracks have MIN_POINTS."""
    tracks = (club_in, club_out, ball_out)
    if any(len(t) < MIN_POINTS for t in tracks):
        return None
    fits = []
    for keep in combinations(range(3), 2):
        subset = [tracks[i] if i in keep else [] for i in range(3)]
        fits.append(joint_fit_impact(*subset, ball_range_m))
    return max(fits) - min(fits)


@dataclass(frozen=True)
class ImpactOutcome:
    name: str
    verdict: str
    tracks_ok: tuple[str, ...]
    spread_us: float | None
    refined_minus_trigger_us: float | None
    c_spread_us: float | None
    club_points_in_band: int


def _median(values: Iterable[float | None]) -> float | None:
    kept = [v for v in values if v is not None]
    return statistics.median(kept) if kept else None


def summarize_impact(outcomes: Sequence[ImpactOutcome]) -> dict:
    consistent = [o for o in outcomes if o.verdict == "consistent"]
    median_a = _median(o.spread_us for o in consistent)
    median_c = _median(o.c_spread_us for o in consistent)
    a_available = sum(1 for o in outcomes if o.verdict != "none")
    c_available = sum(1 for o in outcomes if o.c_spread_us is not None)
    return {
        "captures": len(outcomes),
        "with_estimate": a_available,
        "consistent": len(consistent),
        "inconsistent": sum(1 for o in outcomes if o.verdict == "inconsistent"),
        "none": sum(1 for o in outcomes if o.verdict == "none"),
        "median_spread_us": median_a,
        "median_c_spread_us": median_c,
        "median_refined_minus_trigger_us": _median(o.refined_minus_trigger_us for o in outcomes),
        "club_points_in_band": sum(o.club_points_in_band for o in outcomes),
        "c_wins": bool(
            median_a is not None
            and median_c is not None
            and median_c <= C_WIN_FACTOR * median_a
            and c_available >= len(consistent)
        ),
    }


def impact_outcome(name: str, result) -> ImpactOutcome:
    """One replayed capture's impact outcome (firmware_replay.ReplayResult)."""
    fit = result.impact_fit
    bin_m = 6.0 / result.config.fft_size
    ball_m = result.config.destination * bin_m
    impact_frame = result.shot.impactFrame if fit is not None else None
    in_band = 0
    if result.band is not None:
        lo, hi = result.band
        in_band = sum(
            1
            for p in result.points
            if (impact_frame is None or p.frame <= impact_frame) and lo <= p.range_bin <= hi
        )
    if fit is None:
        return ImpactOutcome(name, "none", (), None, None, None, in_band)
    club_in = [(p.timestamp_us, p.range_m) for p in result.points if p.frame <= impact_frame]
    club_out = [(p.timestamp_us, p.range_m) for p in result.points if p.frame > impact_frame]
    ball_out = [(p.timestamp_us, p.range_m) for p in result.ball_points]
    return ImpactOutcome(
        name,
        fit.verdict,
        tuple(n for n, t in fit.tracks.items() if t.why == "ok"),
        fit.spread_us if fit.verdict != "none" else None,
        fit.refined_minus_trigger_us,
        leave_one_out_spread_us(club_in, club_out, ball_out, ball_m),
        in_band,
    )
```

In `scripts/analysis/evaluate_iwr_tracking.py`: add `parser.add_argument("--impact", action="store_true", help="Also report impact from the tracks either side of the tee band (A vs C)")`; where each case is replayed, when `args.impact`, collect `impact_eval.impact_outcome(case_name, result)`; after the existing summary, print `json.dumps(impact_eval.summarize_impact(outcomes), indent=2)` under a heading `impact:` and add it to the `--json` output as `"impact"`.

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_iwr6843_impact_eval.py tests/test_evaluate_iwr_tracking.py -v` → pass.

- [ ] **Step 5: Acceptance run and report** (no code change)

```bash
uv run python scripts/analysis/evaluate_iwr_tracking.py tests/radar/recordings "C:/Users/corma/Desktop/OF Sessions/iwr6843" --impact --json docs/superpowers/specs/2026-09-28-impact-baseline.json
```

Check against the spec's acceptance and write the numbers into the commit message: `club_points_in_band == 0`; `with_estimate / captures ≥ 0.90` and `consistent / captures ≥ 0.70` over captures with a visible swing; `median_spread_us ≤ 1500`; no regression with `--compare docs/superpowers/specs/2026-09-28-tracking-baseline.json`; `c_wins` — if `true`, stop and bring the numbers to the user before any further work (the spec says switch to C). If any acceptance line fails, report it with the per-capture outcomes rather than tuning defaults silently.

- [ ] **Step 6: Commit**

```bash
git add src/openflight/iwr6843/impact_eval.py scripts/analysis/evaluate_iwr_tracking.py tests/test_iwr6843_impact_eval.py docs/superpowers/specs/2026-09-28-impact-baseline.json
git commit -m "iwr: impact evaluation, method C comparator and the acceptance baseline"
```
