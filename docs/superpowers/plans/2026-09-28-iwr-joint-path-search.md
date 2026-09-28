# IWR6843 Joint Club/Ball Path Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the post-impact ball tracker with a joint club/ball path search that keeps the 16
best explanations over an 8-frame window, scores motion (timestamped range and Doppler-resolved
speed, capped terms), and can revise earlier picks — first on the host replay, then on the board if
the 93-capture acceptance is met.

**Architecture:** A new pure-C module `firmware/iwr6843/l3_joint_search.c/.h` (host-built through
ctypes like every `l3_*.c`) holds a beam of joint explanations with parent links through a window of
frames. Frames all explanations agree on are written out (club points onto the club track, ball
points onto a short ball path the launch fit reads). The replay selects it with
`ReplayConfig.joint_search`; the viewer and evaluation expose it; only the last task switches
`l3_dump.c` and deletes `l3_ball_track.c`/`l3_ball_hyp.c`, and only if Acceptance holds.

**Tech Stack:** C99 (TI ARM CGT on the R4F; gcc/clang/zig cc on the host), Python 3 + ctypes,
pytest, numpy, Flask/Plotly (dump viewer).

**Spec:** `docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md`

## Global Constraints

- Always `uv run` for Python (pytest, python, ruff, pylint).
- Commit only this plan's files with explicit pathspecs (`git add <paths>` then
  `git commit -- <paths>`); never commit `.claude/launch.json`, `iwr-test-sessions*`, `untitled`.
- `L3_JOINT_BEAM` = 16, `L3_JOINT_WINDOW` = 8.
- Total `l3_joint_t` ≤ 3760 B (the `l3_ball_track_t` it replaces); pinned by a test.
- Per frame: ≤ 1500 pairings scored, ≤ 8 angle estimates requested.
- Time only from timestamps with wrap-safe differences `(int32_t)(newer - older)`; Δt ≤ 0 skips the
  frame (`L3_JOINT_COUNT_SKIPPED`).
- Ball: starts only in the start band (origin − 1 … origin + 10 bins + maxSpeed/binWidth × (t − gate));
  outward 10–100 m/s once it has a speed; ends after 2 missed frames; a 1-point ball that ends is a
  false start and reverts to unstarted.
- Club: −2…70 m/s (near-stationary allowed, noise below zero tolerated); coasts up to 3 frames.
- Club and ball never take the same target; a merged return is a ball miss.
- Strength: only `clubStrongerBonus` when the club's point outscores the ball's in the same frame.
- Every scoring term capped at `termCap`; only the range gate (3σ + coasting growth) is hard.
- Confirmation: ≥ 4 ball points, fitted outward 10–100 m/s, origin crossing within 15 ms of the gate,
  residual ≤ 1 bin RMS, and margin ≥ `confirmMargin` over the best rival (different first 4 ball
  points, or no ball).
- Tied scores break by club target, then ball target, then parent index (ascending; "none" = 0xFF
  sorts last) so board and host agree.
- Board and replay make the same calls in the same order: `l3_joint_update` →
  `l3_joint_angle_requests` → `l3_joint_set_angles` (each) → `l3_joint_finish_frame` →
  `l3_joint_launch`.
- Acceptance (Task 8): ball within 15 % of OPS > 15/93; no launch ≤ 13/93; club at impact ≥ 55/93;
  present captures ok ≥ 17/23; absent captures wrong ≤ legacy's wrong on those; every manifest holds.

## Review Focus

1. **Alias choice on the club's impact frame**: the club can lose ~10 m/s in one 3 ms frame, over
   half the 17.93 m/s span, so "nearest the predicted speed" can pick the wrong alias; the club path
   should then still hold (range misfit and capped acceleration) rather than jump to the ball.
   Test: Task 3 `test_club_keeps_path_through_impact_slowdown`.
2. **Ball point written out while its path is a single point** that later reverts: the written head
   would hold a false start. Test: Task 5 `test_single_point_ball_is_never_written_out`.
3. **Forced write-out dropping the eventual best**: after a forced write-out the survivors must all
   descend from the written frame and the launch must still come from the best remaining path.
   Test: Task 5 `test_forced_write_out_keeps_one_ancestry`.
4. **Timestamp wrap mid-capture** (u32 µs wraps every 71.6 min): prediction, gating and the start
   band must use wrapped differences. Test: Task 4 `test_wrap_mid_capture_matches_unwrapped`.
5. **A target with a NaN range** (a degenerate sub-bin fit): it must not poison the beam.
   Test: Task 4 `test_nonfinite_target_is_dropped_not_propagated`.

---

## File Structure

| File | Responsibility |
|---|---|
| `firmware/iwr6843/l3_launch.h/.c` (new) | `l3_launch_t`, `l3_launch_from_delivery`, `l3_launch_format` (moved out of `l3_ball_track`) |
| `firmware/iwr6843/l3_club_track.h/.c` | + `l3_delivery_fit` (point accessor), + `l3_track_append_point` |
| `firmware/iwr6843/l3_joint_search.h/.c` (new) | the joint path search |
| `firmware/iwr6843/makefile` | + `l3_launch.c`, `l3_joint_search.c` |
| `src/openflight/iwr6843/firmware_host.py` | mirrors, constants, signatures, `HOST_SOURCES` |
| `src/openflight/iwr6843/firmware_replay.py` | `ReplayConfig.joint_search`, `JointFrameSummary`, joint post-frame path |
| `src/openflight/iwr6843/dump_viewer.py`, `scripts/iwr6843/dump_viewer.html` | "joint" ball search, predictions, rejected targets, runner-up |
| `scripts/analysis/evaluate_iwr_tracking.py` | `--ball-search`, present/absent splits |
| `tests/test_iwr6843_firmware_launch.py` (new) | launch module |
| `tests/test_iwr6843_firmware_joint.py` (new) | joint search unit + scene tests |
| `tests/iwr6843_joint_runner.py` (new) | test helper: build, arm, feed scenes |
| `tests/iwr6843_twotrack.py` | + `ball_mps`-independent `second_ball` extra chain helper |
| `tests/radar/recordings/…` + `manifest.json` | + the 2026-08-09 capture, joint expectations |
| `tests/test_iwr6843_firmware_replay.py`, `tests/test_iwr6843_dump_viewer.py`, `tests/test_evaluate_iwr_tracking.py` | joint integration |
| `firmware/iwr6843/l3_dump.c`, `l3_result.c/.h` (Task 9 only) | switch the board |

---

### Task 0: Launch module and point-list delivery fit

Pure refactor plus two small additions the joint search needs. No behaviour change.

**Files:**
- Create: `firmware/iwr6843/l3_launch.h`, `firmware/iwr6843/l3_launch.c`
- Modify: `firmware/iwr6843/l3_ball_track.h` (remove `l3_launch_t`, `l3_launch_format`; include `l3_launch.h`), `firmware/iwr6843/l3_ball_track.c` (`l3_ball_track_launch` calls `l3_launch_from_delivery`; drop `l3_launch_format`)
- Modify: `firmware/iwr6843/l3_club_track.h/.c` (`l3_point_at_fn`, `l3_delivery_fit`, `l3_track_append_point`)
- Modify: `firmware/iwr6843/makefile` (`SOURCES` += `l3_launch.c`), `src/openflight/iwr6843/firmware_host.py` (`HOST_SOURCES` += `"l3_launch.c"` after `"l3_club_track.c"`; signatures)
- Test: `tests/test_iwr6843_firmware_launch.py` (new), `tests/test_iwr6843_firmware_club_track.py`

**Interfaces:**
- Produces:
  - `void l3_launch_from_delivery(const l3_delivery_t *fit, uint32_t impactTimestampUs, l3_launch_t *out);`
  - `int32_t l3_launch_format(const l3_launch_t *launch, char *out, uint32_t cap);` (moved, unchanged)
  - `typedef int32_t (*l3_point_at_fn)(const void *ctx, uint32_t index, l3_track_point_t *out);`
  - `uint32_t l3_delivery_fit(l3_point_at_fn pointAt, const void *ctx, uint32_t first, uint32_t last, uint32_t fullPoints, float binWidthM, float maxAngleResidualM, l3_delivery_t *out);`
  - `void l3_track_append_point(l3_club_track_t *track, const l3_track_point_t *point);`

- [ ] **Step 1: Write the failing tests**

`tests/test_iwr6843_firmware_launch.py`:

```python
"""Tests for firmware/iwr6843/l3_launch.c: the launch from a fitted delivery."""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def test_launch_walks_the_fit_back_to_impact(lib):
    fit = fw.Delivery()
    fit.points = 5
    fit.velocity = fw.Vec3(40.0, 3.0, 10.0)
    fit.position = fw.Vec3(2.0, 0.1, 0.5)
    fit.timestampUs = 10_000
    fit.speedMps = 41.34
    fit.radialSpeedMps = 40.5
    fit.residualM = 0.01
    fit.confidence = 0.8
    fit.speedValid = 1
    fit.pathRad = 0.075
    fit.pathValid = 1
    fit.attackRad = 0.245
    fit.attackValid = 1
    out = fw.Launch()
    lib.l3_launch_from_delivery(ctypes.byref(fit), 4_000, ctypes.byref(out))
    assert out.points == 5
    assert out.launchPosition.x == pytest.approx(2.0 - 40.0 * 0.006)
    assert out.launchPosition.z == pytest.approx(0.5 - 10.0 * 0.006)
    assert (out.hlaValid, out.vlaValid, out.speedValid) == (1, 1, 1)
    assert out.hlaRad == pytest.approx(0.075)
    assert out.vlaRad == pytest.approx(0.245)


def test_launch_without_angles_leaves_directions_invalid(lib):
    fit = fw.Delivery()
    fit.points = 4
    fit.speedValid = 1
    out = fw.Launch()
    lib.l3_launch_from_delivery(ctypes.byref(fit), 0, ctypes.byref(out))
    assert (out.hlaValid, out.vlaValid) == (0, 0)
```

Append to `tests/test_iwr6843_firmware_club_track.py` (it already has a `lib` fixture and a
`make_track(lib, **overrides)` helper; if the helper is named differently, use that file's own
helper that returns an initialised `fw.ClubTrack`):

```python
def test_append_point_locates_and_counts(lib):
    track = make_track(lib)
    point = fw.TrackPoint()
    point.frame = 7
    point.timestampUs = 21_000
    point.rangeBin = 40.0
    point.rangeM = 40.0 * track.cfg.binWidthM
    point.confidence = 0.9
    lib.l3_track_append_point(ctypes.byref(track), ctypes.byref(point))
    assert track.count == 1
    assert track.lastBin == pytest.approx(40.0)
    stored = fw.TrackPoint()
    assert lib.l3_track_point(ctypes.byref(track), 0, ctypes.byref(stored))
    assert stored.position.x != 0.0 or stored.position.y != 0.0 or stored.position.z != 0.0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_launch.py tests/test_iwr6843_firmware_club_track.py::test_append_point_locates_and_counts -v`
Expected: FAIL with `AttributeError: ... l3_launch_from_delivery` / `l3_track_append_point`.

- [ ] **Step 3: Implement**

`firmware/iwr6843/l3_launch.h`:

```c
/* IWR6843 launch: the ball's fitted departure, walked back to impact.
 * Pure C, no hardware. */
#ifndef L3_LAUNCH_H
#define L3_LAUNCH_H

#include <stdint.h>

#include "l3_club_track.h"
#include "l3_frames.h"

typedef struct {
    uint32_t  points;
    l3_vec3_t velocity;           /* m/s, golf frame */
    l3_vec3_t launchPosition;     /* the fitted line at the impact time */
    float     speedMps;
    float     radialSpeedMps;     /* range-only fit, for cross-checking */
    float     hlaRad;             /* horizontal launch, positive right */
    float     vlaRad;             /* vertical launch, positive up */
    float     residualM;
    float     confidence;
    uint8_t   speedValid;
    uint8_t   hlaValid;
    uint8_t   vlaValid;
} l3_launch_t;

/* The launch from a delivery fit over the ball's points: the fitted line is
 * anchored at its newest point, so it is walked back to impactTimestampUs. */
void l3_launch_from_delivery(const l3_delivery_t *fit, uint32_t impactTimestampUs,
                             l3_launch_t *out);
int32_t l3_launch_format(const l3_launch_t *launch, char *out, uint32_t cap);

#endif /* L3_LAUNCH_H */
```

`firmware/iwr6843/l3_launch.c`: move `l3_launch_format` verbatim from `l3_ball_track.c` (with the
includes it needs: `<stdio.h>`, `"l3_text.h"`), and add:

```c
void l3_launch_from_delivery(const l3_delivery_t *fit, uint32_t impactTimestampUs,
                             l3_launch_t *out)
{
    float dtS = (float)(int32_t)(impactTimestampUs - fit->timestampUs) * 1.0e-6F;

    memset(out, 0, sizeof(*out));
    out->points = fit->points;
    out->velocity = fit->velocity;
    out->launchPosition.x = fit->position.x + fit->velocity.x * dtS;
    out->launchPosition.y = fit->position.y + fit->velocity.y * dtS;
    out->launchPosition.z = fit->position.z + fit->velocity.z * dtS;
    out->speedMps = fit->speedMps;
    out->radialSpeedMps = fit->radialSpeedMps;
    out->residualM = fit->residualM;
    out->confidence = fit->confidence;
    out->speedValid = fit->speedValid;
    if (fit->pathValid) {
        out->hlaRad = fit->pathRad;
        out->hlaValid = 1U;
    }
    if (fit->attackValid) {
        out->vlaRad = fit->attackRad;
        out->vlaValid = 1U;
    }
}
```

(The old code used `(float)impact - (float)newest`; the wrap-safe difference is the same within a
capture and correct across a wrap.)

`l3_ball_track_launch` becomes:

```c
uint32_t l3_ball_track_launch(const l3_ball_track_t *track, l3_launch_t *out)
{
    l3_delivery_t fit;
    uint32_t used;

    memset(out, 0, sizeof(*out));
    if (!track->confirmed) {
        return 0U;
    }
    used = l3_track_delivery_range(&track->core, 0U, track->cfg.launchPoints,
                                   track->cfg.launchPoints, &fit);
    if (used == 0U) {
        return 0U;
    }
    l3_launch_from_delivery(&fit, track->impactTimestampUs, out);
    return used;
}
```

In `l3_club_track.h` add after `l3_delivery_t`:

```c
/* One point of a list the delivery fit reads, by index; 0 when out of range. */
typedef int32_t (*l3_point_at_fn)(const void *ctx, uint32_t index, l3_track_point_t *out);

/* The 3D fit of points [first, last) read through pointAt: what
 * l3_track_delivery_range does for a track, for any point list (the joint
 * search's ball path). Returns the points used, 0 below three. */
uint32_t l3_delivery_fit(l3_point_at_fn pointAt, const void *ctx, uint32_t first, uint32_t last,
                         uint32_t fullPoints, float binWidthM, float maxAngleResidualM,
                         l3_delivery_t *out);
/* Append a point made elsewhere (the joint search's written-out club point):
 * located with this track's calibration, lastBin and lastFrame updated. */
void l3_track_append_point(l3_club_track_t *track, const l3_track_point_t *point);
```

In `l3_club_track.c`: rename the body of `l3_track_delivery_range` into `l3_delivery_fit`, replacing
every `l3_track_point(track, i, &point)` with `pointAt(ctx, i, &point)`, `track->cfg.binWidthM`
with `binWidthM` and `track->cfg.maxAngleResidualM` with `maxAngleResidualM`; the range clamp
(`first >= track->count`, `last > track->count`) moves to the wrapper:

```c
static int32_t l3_track_point_at(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    return l3_track_point((const l3_club_track_t *)ctx, index, out);
}

uint32_t l3_track_delivery_range(const l3_club_track_t *track, uint32_t first, uint32_t count,
                                 uint32_t fullPoints, l3_delivery_t *out)
{
    uint32_t last = first + count;

    memset(out, 0, sizeof(*out));
    if (first >= track->count) {
        return 0U;
    }
    if (last > track->count) {
        last = track->count;
    }
    return l3_delivery_fit(l3_track_point_at, track, first, last, fullPoints,
                           track->cfg.binWidthM, track->cfg.maxAngleResidualM, out);
}
```

`l3_delivery_fit` starts with `memset(out, 0, sizeof(*out)); if (last < first || last - first < 3U) return 0U;`.

Split `l3_track_append` so both appends share the ring push:

```c
static void l3_track_push(l3_club_track_t *track)
{
    track->next = (track->next + 1U) % L3_TRACK_POINTS;
    if (track->count < L3_TRACK_POINTS) {
        track->count++;
    }
    track->total++;
}

void l3_track_append_point(l3_club_track_t *track, const l3_track_point_t *point)
{
    l3_track_point_t *slot = &track->points[track->next];

    *slot = *point;
    l3_track_locate(track, slot);
    track->lastBin = point->rangeBin;
    track->lastFrame = point->frame;
    l3_track_push(track);
}
```

and the last four lines of the static `l3_track_append` become `l3_track_push(track);`.

`firmware_host.py`: move nothing in Python (the `Launch` mirror is unchanged); add signatures:

```python
    # l3_launch.h
    "l3_launch_from_delivery": ([_P(Delivery), _U32, _P(Launch)], None),
    # l3_club_track.h (additions)
    "l3_track_append_point": ([_P(ClubTrack), _P(TrackPoint)], None),
```

`makefile`: add `l3_launch.c` to `SOURCES` next to `l3_ball_track.c`.

- [ ] **Step 4: Run the new tests and every delivery/launch test**

Run: `uv run pytest tests/test_iwr6843_firmware_launch.py tests/test_iwr6843_firmware_club_track.py tests/test_iwr6843_firmware_ball_track.py tests/test_iwr6843_firmware_replay.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_launch.h firmware/iwr6843/l3_launch.c firmware/iwr6843/l3_ball_track.h firmware/iwr6843/l3_ball_track.c firmware/iwr6843/l3_club_track.h firmware/iwr6843/l3_club_track.c firmware/iwr6843/makefile src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_launch.py tests/test_iwr6843_firmware_club_track.py
git commit -m "iwr: launch module and a delivery fit over any point list" -- firmware/iwr6843/l3_launch.h firmware/iwr6843/l3_launch.c firmware/iwr6843/l3_ball_track.h firmware/iwr6843/l3_ball_track.c firmware/iwr6843/l3_club_track.h firmware/iwr6843/l3_club_track.c firmware/iwr6843/makefile src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_launch.py tests/test_iwr6843_firmware_club_track.py
```

---

### Task 1: Joint search header, config, state and mirrors

**Files:**
- Create: `firmware/iwr6843/l3_joint_search.h`, `firmware/iwr6843/l3_joint_search.c` (init/reset/arm/defaults/struct size only)
- Modify: `firmware/iwr6843/makefile` (`SOURCES` += `l3_joint_search.c`), `src/openflight/iwr6843/firmware_host.py`
- Create: `tests/iwr6843_joint_runner.py`, `tests/test_iwr6843_firmware_joint.py`

**Interfaces:**
- Consumes: Task 0 `l3_launch.h`, `l3_track_append_point`, `l3_delivery_fit`.
- Produces (C): every type and prototype in the header below. Python: `fw.JointCfg`,
  `fw.JointKin`, `fw.JointPath`, `fw.JointNode`, `fw.JointPoint`, `fw.JointBallPoint`,
  `fw.JointLink`, `fw.JointFrame`, `fw.Joint`, `fw.JointAngleReq`, `fw.JointNow`; constants
  `fw.JOINT_BEAM`, `fw.JOINT_WINDOW`, `fw.JOINT_BALL_POINTS`, `fw.JOINT_NONE`,
  `fw.JOINT_CLUB`, `fw.JOINT_BALL`, `fw.JOINT_PATH_NAMES`, `fw.JOINT_TARGET_USE_NAMES`,
  `fw.JOINT_COUNTER_NAMES`. Test helper `joint_runner.make_joint(lib, **cfg)`,
  `joint_runner.arm(lib, js, origin_bin, gate_us, club=None)`, `joint_runner.run(lib, scene, club_seed=True, **cfg)`.

- [ ] **Step 1: Write the failing tests**

`tests/iwr6843_joint_runner.py`:

```python
"""Drive firmware/iwr6843/l3_joint_search.c from the scenes in iwr6843_twotrack.py.

Each post-impact frame runs the board's order: update, angle requests (answered
with the angles a scene gives, or none), finish. Returns the search and, per
frame, the best explanation's picks as (club bin | None, ball bin | None).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from iwr6843_twotrack import BIN_M, TwoTracks

from openflight.iwr6843 import firmware_host as fw


def make_joint(lib, **overrides):
    cfg = fw.JointCfg()
    lib.l3_joint_cfg_defaults(ctypes.byref(cfg))
    cfg.binWidthM = BIN_M
    for name, value in overrides.items():
        setattr(cfg, name, value)
    js = fw.Joint()
    lib.l3_joint_init(ctypes.byref(js), ctypes.byref(cfg))
    return js


def club_seed(scene: TwoTracks) -> fw.JointKin:
    """The pre-impact club track's last point at the gate, as l3_dump.c passes it."""
    bin_, speed = scene._club(-scene.impact_offset_us * 1e-6)  # where the club is at the gate
    kin = fw.JointKin()
    kin.rangeBin = bin_
    kin.speedMps = speed
    kin.timestampUs = scene.gate_us & 0xFFFFFFFF
    return kin


def arm(lib, js, origin_bin, gate_us, club=None):
    lib.l3_joint_arm(
        ctypes.byref(js), origin_bin, gate_us, ctypes.byref(club) if club is not None else None
    )


@dataclass
class Picks:
    club: float | None
    ball: float | None


def step(lib, js, frame, timestamp_us, targets, club_out=None, ball_out=None, angles=None):
    timestamp_us &= 0xFFFFFFFF  # the board's u32 microsecond clock (scenes may run past a wrap)
    for t in targets:
        t.timestampUs = timestamp_us
    arr = (fw.TargetObs * max(1, len(targets)))(*targets)
    survivors = lib.l3_joint_update(
        ctypes.byref(js), arr, len(targets), frame, timestamp_us,
        ctypes.byref(club_out) if club_out is not None else None,
        ctypes.byref(ball_out) if ball_out is not None else None,
    )
    requests = (fw.JointAngleReq * fw.OBS_MAX_TARGETS)()
    n = lib.l3_joint_angle_requests(ctypes.byref(js), requests, fw.OBS_MAX_TARGETS)
    for req in requests[:n]:
        if angles is not None and req.target < len(targets):
            az, el = angles(targets[req.target])
            lib.l3_joint_set_angles(ctypes.byref(js), req.target, az, el, 3)
    lib.l3_joint_finish_frame(
        ctypes.byref(js),
        ctypes.byref(club_out) if club_out is not None else None,
        ctypes.byref(ball_out) if ball_out is not None else None,
    )
    now = fw.JointNow()
    lib.l3_joint_now(ctypes.byref(js), ctypes.byref(now))
    club = targets[now.clubTarget].rangeBin if now.clubTarget != fw.JOINT_NONE else None
    ball = targets[now.ballTarget].rangeBin if now.ballTarget != fw.JOINT_NONE else None
    return survivors, n, Picks(club, ball)


def run(lib, scene: TwoTracks, *, seed=True, angles=None, **overrides):
    js = make_joint(lib, **overrides)
    arm(lib, js, scene.origin_bin, scene.gate_us, club_seed(scene) if seed else None)
    club_out = fw.ClubTrack()
    ball_out = fw.ClubTrack()
    track_cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(track_cfg))
    track_cfg.binWidthM = BIN_M
    lib.l3_track_init(ctypes.byref(club_out), ctypes.byref(track_cfg))
    lib.l3_track_init(ctypes.byref(ball_out), ctypes.byref(track_cfg))
    frames = scene.build()
    picks, requests = [], []
    for f in frames:
        _, n, p = step(lib, js, f.frame, f.timestamp_us, f.targets, club_out, ball_out, angles)
        picks.append(p)
        requests.append(n)
    return js, frames, picks, requests, club_out, ball_out


def ball_path_bins(lib, js):
    """The ball path the launch fit reads (written head + best chain)."""
    out = []
    point = fw.TrackPoint()
    for i in range(js.ballCount):
        assert lib.l3_joint_ball_point(ctypes.byref(js), i, ctypes.byref(point))
        out.append(point.rangeBin)
    return out
```

`tests/test_iwr6843_firmware_joint.py` (first part; later tasks append):

```python
"""Tests for the joint club/ball path search, firmware/iwr6843/l3_joint_search.c.

Design: docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md.
Scenes come from tests/iwr6843_twotrack.py; tests/iwr6843_joint_runner.py
drives the C in the board's call order.
"""

from __future__ import annotations

import ctypes
import math

import pytest
from iwr6843_joint_runner import arm, ball_path_bins, make_joint, run, step
from iwr6843_twotrack import BIN_M, SPAN_MPS, TwoTracks, obs

from openflight.iwr6843 import firmware_host as fw


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def test_struct_sizes_match_the_mirrors(lib):
    assert lib.l3_joint_struct_bytes() == ctypes.sizeof(fw.Joint)
    assert lib.l3_joint_cfg_struct_bytes() == ctypes.sizeof(fw.JointCfg)
    assert lib.l3_joint_node_struct_bytes() == ctypes.sizeof(fw.JointNode)
    assert lib.l3_joint_frame_struct_bytes() == ctypes.sizeof(fw.JointFrame)


def test_state_fits_in_what_the_ball_tracker_frees(lib):
    assert lib.l3_joint_struct_bytes() <= 3760


def test_defaults_are_the_spec_values(lib):
    cfg = fw.JointCfg()
    lib.l3_joint_cfg_defaults(ctypes.byref(cfg))
    assert (cfg.minBallMps, cfg.maxBallMps, cfg.minClubMps, cfg.maxClubMps) == (10, 100, -2, 70)
    assert (cfg.ballMaxMisses, cfg.clubMaxMisses, cfg.confirmPoints) == (2, 3, 4)
    assert cfg.impactToleranceUs == 15_000
    assert cfg.startBehindBins == 1.0 and cfg.startBeyondBins == 10.0
    assert cfg.maxResidualBins == 1.0


def test_arm_starts_one_explanation_from_the_club_seed(lib):
    js = make_joint(lib)
    seed = fw.JointKin(40.0, 30.0, 1000)
    arm(lib, js, 46.0, 1000, seed)
    assert js.armed == 1 and js.survivors == 1 and js.windowCount == 0
    node = js.nodes[js.bank][0]
    assert node.club.state == fw.JOINT_PATH_NAMES.index("active")
    assert node.club.speedKnown == 1 and node.club.last.rangeBin == pytest.approx(40.0)
    assert node.ball.state == fw.JOINT_PATH_NAMES.index("unstarted")


def test_arm_without_a_club_track_leaves_the_club_unstarted(lib):
    js = make_joint(lib)
    arm(lib, js, 46.0, 0, None)
    assert js.nodes[js.bank][0].club.state == fw.JOINT_PATH_NAMES.index("unstarted")


def test_reset_disarms(lib):
    js = make_joint(lib)
    arm(lib, js, 46.0, 0, None)
    lib.l3_joint_reset(ctypes.byref(js))
    assert js.armed == 0 and js.survivors == 0 and js.confirmed == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -v`
Expected: FAIL (`AttributeError: module ... has no attribute 'JointCfg'`).

- [ ] **Step 3: Implement the header, the state functions and the mirrors**

`firmware/iwr6843/l3_joint_search.h`:

```c
/* IWR6843 joint club/ball path search after impact.
 *
 * From the gate, up to L3_JOINT_BEAM joint explanations of the post-impact
 * targets are kept. Each says which target is the club and which is the ball
 * this frame (or that either is missing) and links to its parent explanation
 * in the previous frame. Every frame, every explanation is extended by every
 * allowed (club, ball) pairing and the best L3_JOINT_BEAM survive, so the
 * best explanation can revise picks made up to L3_JOINT_WINDOW frames ago.
 *
 * A point earns pointReward less its capped misfit to the path's motion:
 * range predicted from timestamps and the Doppler-resolved speed, and an
 * acceleration inside the path's band. A missed frame costs missCost; an
 * unassigned target costs nothing. Strength only breaks near-ties
 * (clubStrongerBonus). Club and ball never share a target.
 *
 * Frames every explanation agrees on (or the oldest, when the window is full)
 * are written out: club points onto the club track, ball points onto the ball
 * path the launch fit reads. Angles are estimated only for targets some
 * surviving explanation uses. Pure C, fixed size, no hardware.
 *
 * Design: docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md
 */
#ifndef L3_JOINT_SEARCH_H
#define L3_JOINT_SEARCH_H

#include <stdint.h>

#include "l3_club_track.h"
#include "l3_frames.h"
#include "l3_launch.h"
#include "l3_observation.h"

#define L3_JOINT_BEAM        16U
#define L3_JOINT_WINDOW      8U
#define L3_JOINT_BALL_POINTS 8U
#define L3_JOINT_NONE        0xFFU   /* no target: missed, or not started */

enum { L3_JOINT_CLUB = 0, L3_JOINT_BALL = 1 };

enum {
    L3_JOINT_PATH_UNSTARTED = 0,
    L3_JOINT_PATH_ACTIVE,
    L3_JOINT_PATH_ENDED
};

/* What became of each target in the newest frame, for the viewer. */
enum {
    L3_JOINT_TARGET_OUTSIDE = 0,    /* in no path's gate */
    L3_JOINT_TARGET_SCORED_LOWER,   /* in a gate, not on the best explanation */
    L3_JOINT_TARGET_CLUB,
    L3_JOINT_TARGET_BALL
};

enum {
    L3_JOINT_COUNT_FRAMES = 0,
    L3_JOINT_COUNT_PAIRINGS,        /* pairings scored, all frames */
    L3_JOINT_COUNT_ANGLE_REQUESTS,
    L3_JOINT_COUNT_WRITTEN,
    L3_JOINT_COUNT_FORCED,
    L3_JOINT_COUNT_SKIPPED,         /* frames not after the last one */
    L3_JOINT_COUNT_NONFINITE,
    L3_JOINT_COUNT_N
};

typedef struct {
    float    binWidthM;           /* copied from the club track's config */
    float    velocitySpanMps;     /* Doppler alias span */
    float    snr;                 /* extraction threshold over the floor after impact */
    float    rangeSigmaBins;      /* range misfit unit ... */
    float    coastSigmaGrowBins;  /* ... widened by this per coasted frame */
    float    gateSigmas;          /* the only hard gate */
    float    pointReward;         /* a perfect point */
    float    termCap;             /* the most one term takes off a point */
    float    missCost;
    float    ballAccelMps2;       /* ball: |a| up to this is free (geometry) */
    float    clubDecelMps2;       /* club: slowing up to this is free */
    float    clubAccelMps2;       /* club: speeding up to this is free */
    float    accelScaleMps2;      /* misfit = (excess / this)^2 */
    float    clubStrongerBonus;
    float    startBehindBins;     /* start band: origin - this ... */
    float    startBeyondBins;     /* ... to origin + this + maxSpeed x (t - gate) */
    float    minBallMps;
    float    maxBallMps;
    float    minClubMps;
    float    maxClubMps;
    uint32_t ballMaxMisses;
    uint32_t clubMaxMisses;
    uint32_t confirmPoints;
    uint32_t impactToleranceUs;
    float    maxResidualBins;
    float    confirmMargin;
    uint32_t launchPoints;
    float    maxAngleResidualM;   /* copied from the club track's config */
    l3_radar_cal_t cal;           /* copied from the club track's config */
} l3_joint_cfg_t;

typedef struct {
    float    rangeBin;            /* global, sub-bin */
    float    speedMps;            /* resolved radial speed, positive receding */
    uint32_t timestampUs;
} l3_joint_kin_t;

typedef struct {
    l3_joint_kin_t last;          /* the last real point */
    uint32_t startFrame;          /* frame of the path's first point */
    uint8_t  state;               /* L3_JOINT_PATH_* */
    uint8_t  misses;              /* consecutive frames without a point */
    uint8_t  points;              /* since it started, saturating at 255 */
    uint8_t  speedKnown;          /* 0 until a second point resolves the alias */
} l3_joint_path_t;

typedef struct {
    float           score;
    l3_joint_path_t club;
    l3_joint_path_t ball;
} l3_joint_node_t;

typedef struct {                  /* a target as the window keeps it */
    float    rangeBin;
    float    dopplerAliasMps;
    int16_t  azimuth1e4;          /* radians x 1e4 */
    int16_t  elevation1e4;
    uint8_t  confidence255;       /* confidence x 255 */
    uint8_t  anglesValid;         /* L3_OBS_ANGLE_* bits */
    uint8_t  pad[2];
} l3_joint_point_t;

typedef struct {
    uint32_t frame;
    uint32_t timestampUs;
    l3_joint_point_t point;
} l3_joint_ball_point_t;

typedef struct {
    uint8_t club;                 /* target index, L3_JOINT_NONE when none */
    uint8_t ball;
    uint8_t parent;               /* survivor index in the previous frame */
    uint8_t pad;
} l3_joint_link_t;

typedef struct {
    uint32_t frame;
    uint32_t timestampUs;
    uint8_t  count;               /* targets held */
    uint8_t  survivors;           /* links held */
    uint8_t  pad[2];
    l3_joint_point_t targets[L3_OBS_MAX_TARGETS];
    l3_joint_link_t  links[L3_JOINT_BEAM];
} l3_joint_frame_t;

typedef struct {
    uint8_t target;
    uint8_t pad[3];
    float   speedMps;             /* for the angle estimate's Doppler compensation */
} l3_joint_angle_req_t;

typedef struct {                  /* the best explanation now, for the viewer and retention */
    uint8_t clubTarget;           /* this frame's picks, L3_JOINT_NONE when none */
    uint8_t ballTarget;
    uint8_t clubState;
    uint8_t ballState;
    uint8_t clubPredicted;        /* 1 when *PredictedBin holds a prediction */
    uint8_t ballPredicted;
    uint8_t pad[2];
    float   clubBin;              /* each path's last real point */
    float   clubSpeedMps;
    float   ballBin;
    float   ballSpeedMps;
    float   clubPredictedBin;     /* where the parent explanation predicted this frame */
    float   ballPredictedBin;
    float   score;
} l3_joint_now_t;

typedef struct {
    l3_joint_cfg_t cfg;
    uint8_t  armed;
    uint8_t  confirmed;
    uint8_t  done;                /* confirmed and the ball path has ended */
    uint8_t  bank;                /* nodes[bank] are the survivors */
    uint32_t survivors;
    float    originBin;
    uint32_t gateTimestampUs;
    uint32_t windowCount;         /* frames held; the newest is window[newestSlot] */
    uint32_t newestSlot;
    l3_joint_frame_t window[L3_JOINT_WINDOW];
    l3_joint_node_t  nodes[2][L3_JOINT_BEAM];
    uint32_t ballWritten;         /* the head of ballPath that has been written out */
    uint32_t ballCount;           /* the best explanation's ball path, head + window */
    l3_joint_ball_point_t ballPath[L3_JOINT_BALL_POINTS];
    uint8_t  targetUse[L3_OBS_MAX_TARGETS];  /* newest frame: L3_JOINT_TARGET_* */
    uint8_t  gateMask[L3_OBS_MAX_TARGETS];   /* newest frame: bit per path kind */
    uint32_t counters[L3_JOINT_COUNT_N];
} l3_joint_t;

void l3_joint_cfg_defaults(l3_joint_cfg_t *cfg);
void l3_joint_init(l3_joint_t *js, const l3_joint_cfg_t *cfg);
void l3_joint_reset(l3_joint_t *js);
/* Start at the gate: every explanation's club path continues from club (the
 * pre-impact club track's last point and fitted speed), or starts like the
 * ball when club is NULL. */
void l3_joint_arm(l3_joint_t *js, float originBin, uint32_t gateTimestampUs,
                  const l3_joint_kin_t *club);
/* One post-impact frame's targets (strongest first). clubOut and ballOut,
 * either NULL, receive written-out points. Returns the survivors, 0 when
 * unarmed or the frame was skipped. */
uint32_t l3_joint_update(l3_joint_t *js, const l3_target_obs_t *targets, uint32_t n,
                         uint32_t frame, uint32_t timestampUs, l3_club_track_t *clubOut,
                         l3_club_track_t *ballOut);
/* The newest frame's targets some survivor uses, each once. */
uint32_t l3_joint_angle_requests(l3_joint_t *js, l3_joint_angle_req_t *out, uint32_t max);
int32_t l3_joint_set_angles(l3_joint_t *js, uint32_t targetIndex, float azimuthRad,
                            float elevationRad, uint8_t anglesValid);
/* After the angles: agreed frames written out, the ball path rebuilt,
 * confirmation tried. */
void l3_joint_finish_frame(l3_joint_t *js, l3_club_track_t *clubOut, l3_club_track_t *ballOut);
int32_t l3_joint_now(const l3_joint_t *js, l3_joint_now_t *out);
/* The ball path the launch fit reads, oldest first. */
int32_t l3_joint_ball_point(const l3_joint_t *js, uint32_t index, l3_track_point_t *out);
/* A survivor's ball points inside the window only, oldest first. */
uint32_t l3_joint_window_ball(const l3_joint_t *js, uint32_t survivor,
                              l3_joint_ball_point_t *out, uint32_t max);
/* The best survivor whose first confirmPoints ball points differ from the
 * best's (or who has fewer), -1 when none. */
int32_t l3_joint_rival(const l3_joint_t *js);
uint8_t l3_joint_target_use(const l3_joint_t *js, uint32_t targetIndex);
uint32_t l3_joint_launch(const l3_joint_t *js, l3_launch_t *out);

/* Kinematics, exported for the tests. */
float l3_joint_resolve_speed(float aliasMps, float predictedMps, float spanMps);
int32_t l3_joint_point_reward(const l3_joint_cfg_t *cfg, uint32_t kind,
                              const l3_joint_path_t *path, float rangeBin, float speedMps,
                              uint32_t timestampUs, float *reward);

uint32_t l3_joint_struct_bytes(void);
uint32_t l3_joint_cfg_struct_bytes(void);
uint32_t l3_joint_node_struct_bytes(void);
uint32_t l3_joint_frame_struct_bytes(void);

#endif /* L3_JOINT_SEARCH_H */
```

`firmware/iwr6843/l3_joint_search.c` (this task: everything except update/requests/finish/rival/
launch/kinematics, which later tasks add; stub those as returning 0 so the library links):

```c
#include "l3_joint_search.h"

#include <math.h>
#include <string.h>

#if L3_JOINT_WINDOW <= 3U
#error "the window must outlast a false-start ball path (ballMaxMisses + 1 frames)"
#endif

void l3_joint_cfg_defaults(l3_joint_cfg_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->binWidthM = 6.0F / 128.0F;
    cfg->velocitySpanMps = 17.93F;
    cfg->snr = 3.0F;                  /* the ball tracker's: the ball is a weak return */
    cfg->rangeSigmaBins = 1.0F;
    cfg->coastSigmaGrowBins = 0.75F;
    cfg->gateSigmas = 3.0F;
    cfg->pointReward = 4.0F;
    cfg->termCap = 3.0F;              /* one bad term still beats a miss: 4 - 3 > -1.5 */
    cfg->missCost = 1.5F;
    cfg->ballAccelMps2 = 400.0F;
    cfg->clubDecelMps2 = 6000.0F;     /* ~10 m/s lost across a 3 ms impact frame is free */
    cfg->clubAccelMps2 = 2000.0F;
    cfg->accelScaleMps2 = 2000.0F;
    cfg->clubStrongerBonus = 0.25F;
    cfg->startBehindBins = 1.0F;
    cfg->startBeyondBins = 10.0F;
    cfg->minBallMps = 10.0F;
    cfg->maxBallMps = 100.0F;
    cfg->minClubMps = -2.0F;
    cfg->maxClubMps = 70.0F;
    cfg->ballMaxMisses = 2U;
    cfg->clubMaxMisses = 3U;
    cfg->confirmPoints = 4U;
    cfg->impactToleranceUs = 15000U;
    cfg->maxResidualBins = 1.0F;
    cfg->confirmMargin = 2.0F;
    cfg->launchPoints = 6U;
    cfg->maxAngleResidualM = 0.0F;
    l3_cal_identity(&cfg->cal, 12U);
}

void l3_joint_init(l3_joint_t *js, const l3_joint_cfg_t *cfg)
{
    memset(js, 0, sizeof(*js));
    js->cfg = *cfg;
    if (js->cfg.ballMaxMisses + 2U > L3_JOINT_WINDOW) {
        js->cfg.ballMaxMisses = L3_JOINT_WINDOW - 2U;
    }
    if (js->cfg.confirmPoints > L3_JOINT_BALL_POINTS) {
        js->cfg.confirmPoints = L3_JOINT_BALL_POINTS;
    }
    if (js->cfg.launchPoints > L3_JOINT_BALL_POINTS) {
        js->cfg.launchPoints = L3_JOINT_BALL_POINTS;
    }
    l3_joint_reset(js);
}

void l3_joint_reset(l3_joint_t *js)
{
    l3_joint_cfg_t cfg = js->cfg;

    memset(js, 0, sizeof(*js));
    js->cfg = cfg;
    js->newestSlot = L3_JOINT_WINDOW - 1U;  /* the first frame lands in slot 0 */
}

void l3_joint_arm(l3_joint_t *js, float originBin, uint32_t gateTimestampUs,
                  const l3_joint_kin_t *club)
{
    l3_joint_node_t *node;

    l3_joint_reset(js);
    js->armed = 1U;
    js->originBin = originBin;
    js->gateTimestampUs = gateTimestampUs;
    js->survivors = 1U;
    node = &js->nodes[0][0];
    if (club != NULL) {
        node->club.last = *club;
        node->club.state = L3_JOINT_PATH_ACTIVE;
        node->club.points = 2U;       /* established: a miss run ends it, never reverts it */
        node->club.speedKnown = 1U;
    }
}

uint32_t l3_joint_struct_bytes(void) { return (uint32_t)sizeof(l3_joint_t); }
uint32_t l3_joint_cfg_struct_bytes(void) { return (uint32_t)sizeof(l3_joint_cfg_t); }
uint32_t l3_joint_node_struct_bytes(void) { return (uint32_t)sizeof(l3_joint_node_t); }
uint32_t l3_joint_frame_struct_bytes(void) { return (uint32_t)sizeof(l3_joint_frame_t); }
```

(Check `l3_cal_identity`'s element count against how `l3_dump.c` builds `gRadarCal`; the joint
search's cal is overwritten by the caller anyway — the tests only need a valid identity.)

`firmware_host.py` — constants after the ball-hyp constants:

```python
# l3_joint_search.h
JOINT_BEAM = 16
JOINT_WINDOW = 8
JOINT_BALL_POINTS = 8
JOINT_NONE = 0xFF
JOINT_CLUB, JOINT_BALL = 0, 1
JOINT_PATH_NAMES = ("unstarted", "active", "ended")
JOINT_TARGET_USE_NAMES = ("outside gate", "scored lower", "club", "ball")
JOINT_COUNTER_NAMES = (
    "frames", "pairings", "angle_requests", "written", "forced", "skipped", "nonfinite",
)
```

Mirrors (after `BallTrack`):

```python
class JointCfg(ctypes.Structure):
    """``l3_joint_cfg_t``."""

    _fields_ = [
        ("binWidthM", ctypes.c_float),
        ("velocitySpanMps", ctypes.c_float),
        ("snr", ctypes.c_float),
        ("rangeSigmaBins", ctypes.c_float),
        ("coastSigmaGrowBins", ctypes.c_float),
        ("gateSigmas", ctypes.c_float),
        ("pointReward", ctypes.c_float),
        ("termCap", ctypes.c_float),
        ("missCost", ctypes.c_float),
        ("ballAccelMps2", ctypes.c_float),
        ("clubDecelMps2", ctypes.c_float),
        ("clubAccelMps2", ctypes.c_float),
        ("accelScaleMps2", ctypes.c_float),
        ("clubStrongerBonus", ctypes.c_float),
        ("startBehindBins", ctypes.c_float),
        ("startBeyondBins", ctypes.c_float),
        ("minBallMps", ctypes.c_float),
        ("maxBallMps", ctypes.c_float),
        ("minClubMps", ctypes.c_float),
        ("maxClubMps", ctypes.c_float),
        ("ballMaxMisses", ctypes.c_uint32),
        ("clubMaxMisses", ctypes.c_uint32),
        ("confirmPoints", ctypes.c_uint32),
        ("impactToleranceUs", ctypes.c_uint32),
        ("maxResidualBins", ctypes.c_float),
        ("confirmMargin", ctypes.c_float),
        ("launchPoints", ctypes.c_uint32),
        ("maxAngleResidualM", ctypes.c_float),
        ("cal", RadarCal),
    ]


class JointKin(ctypes.Structure):
    """``l3_joint_kin_t``: a path's last real point."""

    _fields_ = [
        ("rangeBin", ctypes.c_float),
        ("speedMps", ctypes.c_float),
        ("timestampUs", ctypes.c_uint32),
    ]


class JointPath(ctypes.Structure):
    """``l3_joint_path_t``."""

    _fields_ = [
        ("last", JointKin),
        ("startFrame", ctypes.c_uint32),
        ("state", ctypes.c_uint8),
        ("misses", ctypes.c_uint8),
        ("points", ctypes.c_uint8),
        ("speedKnown", ctypes.c_uint8),
    ]


class JointNode(ctypes.Structure):
    """``l3_joint_node_t``: one explanation's score and both paths."""

    _fields_ = [("score", ctypes.c_float), ("club", JointPath), ("ball", JointPath)]


class JointPoint(ctypes.Structure):
    """``l3_joint_point_t``: a target as the window keeps it."""

    _fields_ = [
        ("rangeBin", ctypes.c_float),
        ("dopplerAliasMps", ctypes.c_float),
        ("azimuth1e4", ctypes.c_int16),
        ("elevation1e4", ctypes.c_int16),
        ("confidence255", ctypes.c_uint8),
        ("anglesValid", ctypes.c_uint8),
        ("pad", ctypes.c_uint8 * 2),
    ]


class JointBallPoint(ctypes.Structure):
    """``l3_joint_ball_point_t``."""

    _fields_ = [("frame", ctypes.c_uint32), ("timestampUs", ctypes.c_uint32), ("point", JointPoint)]


class JointLink(ctypes.Structure):
    """``l3_joint_link_t``."""

    _fields_ = [
        ("club", ctypes.c_uint8),
        ("ball", ctypes.c_uint8),
        ("parent", ctypes.c_uint8),
        ("pad", ctypes.c_uint8),
    ]


class JointFrame(ctypes.Structure):
    """``l3_joint_frame_t``: one window frame's targets and survivor links."""

    _fields_ = [
        ("frame", ctypes.c_uint32),
        ("timestampUs", ctypes.c_uint32),
        ("count", ctypes.c_uint8),
        ("survivors", ctypes.c_uint8),
        ("pad", ctypes.c_uint8 * 2),
        ("targets", JointPoint * OBS_MAX_TARGETS),
        ("links", JointLink * JOINT_BEAM),
    ]


class JointAngleReq(ctypes.Structure):
    """``l3_joint_angle_req_t``."""

    _fields_ = [("target", ctypes.c_uint8), ("pad", ctypes.c_uint8 * 3), ("speedMps", ctypes.c_float)]


class JointNow(ctypes.Structure):
    """``l3_joint_now_t``."""

    _fields_ = [
        ("clubTarget", ctypes.c_uint8),
        ("ballTarget", ctypes.c_uint8),
        ("clubState", ctypes.c_uint8),
        ("ballState", ctypes.c_uint8),
        ("clubPredicted", ctypes.c_uint8),
        ("ballPredicted", ctypes.c_uint8),
        ("pad", ctypes.c_uint8 * 2),
        ("clubBin", ctypes.c_float),
        ("clubSpeedMps", ctypes.c_float),
        ("ballBin", ctypes.c_float),
        ("ballSpeedMps", ctypes.c_float),
        ("clubPredictedBin", ctypes.c_float),
        ("ballPredictedBin", ctypes.c_float),
        ("score", ctypes.c_float),
    ]


class Joint(ctypes.Structure):
    """``l3_joint_t``: the joint club/ball path search."""

    _fields_ = [
        ("cfg", JointCfg),
        ("armed", ctypes.c_uint8),
        ("confirmed", ctypes.c_uint8),
        ("done", ctypes.c_uint8),
        ("bank", ctypes.c_uint8),
        ("survivors", ctypes.c_uint32),
        ("originBin", ctypes.c_float),
        ("gateTimestampUs", ctypes.c_uint32),
        ("windowCount", ctypes.c_uint32),
        ("newestSlot", ctypes.c_uint32),
        ("window", JointFrame * JOINT_WINDOW),
        ("nodes", (JointNode * JOINT_BEAM) * 2),
        ("ballWritten", ctypes.c_uint32),
        ("ballCount", ctypes.c_uint32),
        ("ballPath", JointBallPoint * JOINT_BALL_POINTS),
        ("targetUse", ctypes.c_uint8 * OBS_MAX_TARGETS),
        ("gateMask", ctypes.c_uint8 * OBS_MAX_TARGETS),
        ("counters", ctypes.c_uint32 * len(JOINT_COUNTER_NAMES)),
    ]
```

(If the cal mirror is named something other than `RadarCal`, use the existing name — the replay
already passes `fw.RadarCal` to `_estimate_angles`.)

Signatures:

```python
    # l3_joint_search.h
    "l3_joint_cfg_defaults": ([_P(JointCfg)], None),
    "l3_joint_init": ([_P(Joint), _P(JointCfg)], None),
    "l3_joint_reset": ([_P(Joint)], None),
    "l3_joint_arm": ([_P(Joint), _F32, _U32, _P(JointKin)], None),
    "l3_joint_update": (
        [_P(Joint), _P(TargetObs), _U32, _U32, _U32, _P(ClubTrack), _P(ClubTrack)],
        _U32,
    ),
    "l3_joint_angle_requests": ([_P(Joint), _P(JointAngleReq), _U32], _U32),
    "l3_joint_set_angles": ([_P(Joint), _U32, _F32, _F32, ctypes.c_uint8], ctypes.c_int32),
    "l3_joint_finish_frame": ([_P(Joint), _P(ClubTrack), _P(ClubTrack)], None),
    "l3_joint_now": ([_P(Joint), _P(JointNow)], ctypes.c_int32),
    "l3_joint_ball_point": ([_P(Joint), _U32, _P(TrackPoint)], ctypes.c_int32),
    "l3_joint_window_ball": ([_P(Joint), _U32, _P(JointBallPoint), _U32], _U32),
    "l3_joint_rival": ([_P(Joint)], ctypes.c_int32),
    "l3_joint_target_use": ([_P(Joint), _U32], ctypes.c_uint8),
    "l3_joint_launch": ([_P(Joint), _P(Launch)], _U32),
    "l3_joint_resolve_speed": ([_F32, _F32, _F32], _F32),
    "l3_joint_point_reward": (
        [_P(JointCfg), _U32, _P(JointPath), _F32, _F32, _U32, _P(ctypes.c_float)],
        ctypes.c_int32,
    ),
    "l3_joint_struct_bytes": ([], _U32),
    "l3_joint_cfg_struct_bytes": ([], _U32),
    "l3_joint_node_struct_bytes": ([], _U32),
    "l3_joint_frame_struct_bytes": ([], _U32),
```

`HOST_SOURCES` += `"l3_joint_search.c"` after `"l3_launch.c"`. `makefile` `SOURCES` +=
`l3_joint_search.c`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -v`
Expected: PASS (6 tests). If `test_state_fits_in_what_the_ball_tracker_frees` fails, print
`lib.l3_joint_struct_bytes()` and reduce `L3_JOINT_BALL_POINTS` first (never below
`launchPoints` = 6); ledger the ruling.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_joint_search.h firmware/iwr6843/l3_joint_search.c firmware/iwr6843/makefile src/openflight/iwr6843/firmware_host.py tests/iwr6843_joint_runner.py tests/test_iwr6843_firmware_joint.py
git commit -m "iwr: joint path search state, config and mirrors" -- firmware/iwr6843/l3_joint_search.h firmware/iwr6843/l3_joint_search.c firmware/iwr6843/makefile src/openflight/iwr6843/firmware_host.py tests/iwr6843_joint_runner.py tests/test_iwr6843_firmware_joint.py
```

---

### Task 2: Kinematics — alias choice and the capped point reward

**Files:**
- Modify: `firmware/iwr6843/l3_joint_search.c`
- Test: `tests/test_iwr6843_firmware_joint.py`

**Interfaces:**
- Produces: `l3_joint_resolve_speed`, `l3_joint_point_reward` (header from Task 1); static
  `l3_joint_dt_s(uint32_t newer, uint32_t older)` used by every later task.

- [ ] **Step 1: Write the failing tests** (append)

```python
def reward(lib, cfg, kind, path, range_bin, speed, ts):
    out = ctypes.c_float(0.0)
    ok = lib.l3_joint_point_reward(
        ctypes.byref(cfg), kind, ctypes.byref(path), range_bin, speed, ts, ctypes.byref(out)
    )
    return ok, out.value


def defaults(lib, **overrides):
    cfg = fw.JointCfg()
    lib.l3_joint_cfg_defaults(ctypes.byref(cfg))
    cfg.binWidthM = BIN_M
    for name, value in overrides.items():
        setattr(cfg, name, value)
    return cfg


def path(bin_, speed, ts, known=1, misses=0):
    p = fw.JointPath()
    p.last = fw.JointKin(bin_, speed, ts)
    p.state = 1
    p.points = 2
    p.speedKnown = known
    p.misses = misses
    return p


@pytest.mark.parametrize(
    "alias_mps, predicted, expected",
    [(1.9, 38.0, 1.9 + 2 * SPAN_MPS), (-3.8, 45.0, -3.8 + 3 * SPAN_MPS), (2.0, 2.5, 2.0),
     (8.9, -9.0, 8.9 - SPAN_MPS)],
)
def test_resolve_speed_takes_the_alias_nearest_the_prediction(lib, alias_mps, predicted, expected):
    assert lib.l3_joint_resolve_speed(alias_mps, predicted, SPAN_MPS) == pytest.approx(expected, abs=1e-4)


def test_a_point_on_the_prediction_earns_the_full_reward(lib):
    cfg = defaults(lib)
    p = path(40.0, 30.0, 0)
    bin_ = 40.0 + 30.0 * 0.003 / BIN_M
    ok, value = reward(lib, cfg, fw.JOINT_BALL, p, bin_, 30.0, 3000)
    assert ok and value == pytest.approx(cfg.pointReward, abs=1e-3)


def test_range_misfit_costs_half_its_square(lib):
    cfg = defaults(lib)
    p = path(40.0, 30.0, 0)
    bin_ = 40.0 + 30.0 * 0.003 / BIN_M + 2.0  # two sigma out
    ok, value = reward(lib, cfg, fw.JOINT_BALL, p, bin_, 30.0, 3000)
    assert ok and value == pytest.approx(cfg.pointReward - 2.0, abs=1e-3)


def test_outside_the_range_gate_is_not_an_option(lib):
    cfg = defaults(lib)
    p = path(40.0, 30.0, 0)
    ok, _ = reward(lib, cfg, fw.JOINT_BALL, p, 40.0 + 30.0 * 0.003 / BIN_M + 3.5, 30.0, 3000)
    assert not ok


def test_coasting_widens_the_gate(lib):
    cfg = defaults(lib)
    p = path(40.0, 30.0, 0, misses=1)  # sigma 1.75 bins
    ok, _ = reward(lib, cfg, fw.JOINT_BALL, p, 40.0 + 30.0 * 0.003 / BIN_M + 3.5, 30.0, 3000)
    assert ok


def test_one_bad_term_still_beats_a_miss_two_do_not(lib):
    cfg = defaults(lib)
    p = path(40.0, 30.0, 0)
    ok, one_bad = reward(lib, cfg, fw.JOINT_BALL, p, 40.0 + 30.0 * 0.003 / BIN_M, 90.0, 3000)
    assert ok and one_bad == pytest.approx(cfg.pointReward - cfg.termCap, abs=1e-3)
    assert one_bad > -cfg.missCost
    # the range misfit is also large (2.9 sigma, capped at termCap) as well as the acceleration
    far = 40.0 + 0.5 * (30.0 + 90.0) * 0.003 / BIN_M + 2.9
    ok, two_bad = reward(lib, cfg, fw.JOINT_BALL, p, far, 90.0, 3000)
    assert ok and two_bad < -cfg.missCost


def test_club_slowdown_inside_its_band_is_free(lib):
    cfg = defaults(lib)
    p = path(40.0, 38.0, 0)
    speed = 38.0 - 5000.0 * 0.003  # 5000 m/s^2, inside clubDecelMps2
    bin_ = 40.0 + 0.5 * (38.0 + speed) * 0.003 / BIN_M
    ok, value = reward(lib, cfg, fw.JOINT_CLUB, p, bin_, speed, 3000)
    assert ok and value == pytest.approx(cfg.pointReward, abs=1e-3)


def test_same_slowdown_costs_the_ball(lib):
    cfg = defaults(lib)
    p = path(40.0, 38.0, 0)
    speed = 38.0 - 5000.0 * 0.003
    bin_ = 40.0 + 0.5 * (38.0 + speed) * 0.003 / BIN_M
    ok, value = reward(lib, cfg, fw.JOINT_BALL, p, bin_, speed, 3000)
    assert ok and value < cfg.pointReward - 1.0


def test_unknown_speed_predicts_with_the_new_speed_only(lib):
    cfg = defaults(lib)
    p = path(46.0, 0.0, 0, known=0)
    speed = 45.0
    ok, value = reward(lib, cfg, fw.JOINT_BALL, p, 46.0 + speed * 0.003 / BIN_M, speed, 3000)
    assert ok and value == pytest.approx(cfg.pointReward, abs=1e-3)


def test_non_positive_elapsed_time_is_not_an_option(lib):
    cfg = defaults(lib)
    p = path(40.0, 30.0, 5000)
    assert reward(lib, cfg, fw.JOINT_BALL, p, 41.0, 30.0, 5000)[0] == 0
    assert reward(lib, cfg, fw.JOINT_BALL, p, 41.0, 30.0, 4000)[0] == 0


def test_elapsed_time_is_wrap_safe(lib):
    cfg = defaults(lib)
    start = 0xFFFFF000
    p = path(40.0, 30.0, start)
    ts = (start + 3000) & 0xFFFFFFFF
    ok, value = reward(lib, cfg, fw.JOINT_BALL, p, 40.0 + 30.0 * 0.003 / BIN_M, 30.0, ts)
    assert ok and value == pytest.approx(cfg.pointReward, abs=1e-3)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -k "resolve or reward or misfit or gate or bad_term or slowdown or unknown_speed or elapsed" -v`
Expected: FAIL (stubs return 0).

- [ ] **Step 3: Implement** (replace the two stubs)

```c
/* Microseconds from older to newer as seconds; wrap-safe within 35 minutes. */
static float l3_joint_dt_s(uint32_t newer, uint32_t older)
{
    return (float)(int32_t)(newer - older) * 1.0e-6F;
}

static float l3_joint_cap(float value, float cap)
{
    return (value > cap) ? cap : value;
}

float l3_joint_resolve_speed(float aliasMps, float predictedMps, float spanMps)
{
    if (spanMps <= 0.0F) {
        return aliasMps;
    }
    return aliasMps + floorf((predictedMps - aliasMps) / spanMps + 0.5F) * spanMps;
}

int32_t l3_joint_point_reward(const l3_joint_cfg_t *cfg, uint32_t kind,
                              const l3_joint_path_t *path, float rangeBin, float speedMps,
                              uint32_t timestampUs, float *reward)
{
    float dtS = l3_joint_dt_s(timestampUs, path->last.timestampUs);
    float sigma = cfg->rangeSigmaBins + cfg->coastSigmaGrowBins * (float)path->misses;
    float meanSpeed;
    float misfit;
    float total;

    if (dtS <= 0.0F || sigma <= 0.0F || cfg->binWidthM <= 0.0F) {
        return 0;
    }
    /* Without a speed yet (a ball's second point), the new point's own speed
     * predicts it: the check is then that Doppler agrees with the range walk. */
    meanSpeed = path->speedKnown ? 0.5F * (path->last.speedMps + speedMps) : speedMps;
    misfit = (rangeBin - (path->last.rangeBin + meanSpeed * dtS / cfg->binWidthM)) / sigma;
    if (!(fabsf(misfit) <= cfg->gateSigmas)) {   /* also false for NaN */
        return 0;
    }
    total = cfg->pointReward - l3_joint_cap(0.5F * misfit * misfit, cfg->termCap);
    if (path->speedKnown && cfg->accelScaleMps2 > 0.0F) {
        float accel = (speedMps - path->last.speedMps) / dtS;
        float low = (kind == L3_JOINT_BALL) ? -cfg->ballAccelMps2 : -cfg->clubDecelMps2;
        float high = (kind == L3_JOINT_BALL) ? cfg->ballAccelMps2 : cfg->clubAccelMps2;
        float excess = (accel > high) ? (accel - high) : ((accel < low) ? (low - accel) : 0.0F);
        float scaled = excess / cfg->accelScaleMps2;

        total -= l3_joint_cap(scaled * scaled, cfg->termCap);
    }
    *reward = total;
    return 1;
}
```

Note `test_elapsed_time_is_wrap_safe`: the prediction uses `dtS` only, never absolute times.
Note on the NaN guard: `!(fabsf(misfit) <= gate)` rejects NaN, so a NaN range never becomes an
option (Review Focus 5 is completed in Task 3's beam test).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_joint_search.c tests/test_iwr6843_firmware_joint.py
git commit -m "iwr: joint search kinematics: alias choice and capped point reward" -- firmware/iwr6843/l3_joint_search.c tests/test_iwr6843_firmware_joint.py
```

---

### Task 3: The frame step — options, pairings, beam and links

**Files:**
- Modify: `firmware/iwr6843/l3_joint_search.c`, `tests/iwr6843_twotrack.py`
- Test: `tests/test_iwr6843_firmware_joint.py`

**Interfaces:**
- Consumes: Task 2 kinematics.
- Produces: working `l3_joint_update` (without write-out, which Task 4 adds; the window simply
  refuses a 9th frame until then — Task 4 replaces that), `l3_joint_now`, `l3_joint_target_use`,
  `l3_joint_angle_requests`, `l3_joint_set_angles`, a `l3_joint_finish_frame` that only rebuilds
  `ballCount`/`ballPath` from the window (confirmation comes in Task 4).
- `TwoTracks.second_ball(bin0, mps, stat)` helper → an extra moving return appended to `extras`
  per frame (extras today are static; add `movers: list` of (start bin, m/s, stat) listed from the
  first post frame).

- [ ] **Step 1: Extend the scene helper and write the failing tests**

In `tests/iwr6843_twotrack.py`, add a field and its use in `build()`:

```python
    movers: list = field(default_factory=list)  # (bin at the gate, m/s, stat): moving returns
```

inside the frame loop, after the `extras` loop:

```python
            for bin0, mps, stat in self.movers:
                t_gate = (ts - self.gate_us) * 1e-6
                entries.append((bin0 + mps * t_gate / BIN_M, stat, mps, "extra"))
```

Append to `tests/test_iwr6843_firmware_joint.py`:

```python
def scene(**kw):
    base = dict(origin_bin=46.0, club_mps=38.0, club_decel_mps2=3000.0, ball_mps=50.0,
                club_stat=9000.0, ball_stat=1500.0, frame_us=3000, frames=8)
    base.update(kw)
    return TwoTracks(**base)


def near(values, target, tol=0.05):
    return any(v is not None and abs(v - target) < tol for v in values)


def ball_frames_ok(frames, picks, from_frame=3):
    """The best explanation's ball pick is the scene's ball on every frame from from_frame on."""
    return all(
        p.ball is not None and abs(p.ball - f.ball_bin) < 0.05
        for f, p in zip(frames, picks)
        if f.frame >= from_frame and f.ball_bin is not None
    )


def test_weak_ball_beside_strong_club(lib):
    js, frames, picks, *_ = run(lib, scene())
    assert ball_frames_ok(frames, picks)
    club_bins = [f.targets[f.club_index].rangeBin for f in frames]
    assert all(p.club is not None and abs(p.club - c) < 0.05 for p, c in zip(picks, club_bins))


def test_club_and_ball_never_share_a_target(lib):
    js, frames, picks, *_ = run(lib, scene(merged=(1, 2)))
    for p in picks:
        assert p.club is None or p.ball is None or p.club != p.ball


def test_merged_return_starts_the_ball_after_the_split(lib):
    js, frames, picks, *_ = run(lib, scene(merged=(1, 2)))
    assert picks[0].ball is None and picks[1].ball is None
    assert ball_frames_ok(frames, picks, from_frame=4)


@pytest.mark.parametrize("gain", [1.0, 4.0, 16.0])
def test_club_strength_does_not_change_identities(lib, gain):
    base = run(lib, scene())[2]
    scaled = run(lib, scene(club_stat=9000.0 * gain))[2]
    assert [(p.club, p.ball) for p in base] == [(p.club, p.ball) for p in scaled]


def test_ball_missing_two_frames_coasts_and_does_not_take_the_club(lib):
    js, frames, picks, *_ = run(lib, scene(frames=8, missing_ball=(5, 6)))
    for f, p in zip(frames, picks):
        if f.frame in (5, 6):
            assert p.ball is None
    assert ball_frames_ok(frames, picks, from_frame=7)


def test_ball_path_ends_after_three_missed_frames(lib):
    # 8 frames: Task 3's window refuses a 9th until Task 4 adds the write-out
    js, *_ = run(lib, scene(frames=8, missing_ball=(5, 6, 7, 8)))
    assert js.nodes[js.bank][0].ball.state == fw.JOINT_PATH_NAMES.index("ended")


def test_stationary_return_in_the_start_band_never_becomes_the_ball(lib):
    js, frames, picks, *_ = run(lib, scene(extras=[(47.0, 3000.0, 0.0)]))
    assert not near([p.ball for p in picks], 47.0)
    assert ball_frames_ok(frames, picks)


def test_no_club_track_at_the_gate_still_separates(lib):
    js, frames, picks, *_ = run(lib, scene(), seed=False)
    assert ball_frames_ok(frames, picks, from_frame=4)


def test_gate_twelve_ms_after_impact_still_finds_the_ball(lib):
    js, frames, picks, *_ = run(lib, scene(impact_offset_us=-12_000, frames=8))
    assert ball_frames_ok(frames, picks, from_frame=2)


@pytest.mark.parametrize("frame_us", [2000, 3000, 6000])
def test_frame_spacing_gives_the_same_identities(lib, frame_us):
    js, frames, picks, *_ = run(lib, scene(frame_us=frame_us, frames=8))
    assert ball_frames_ok(frames, picks, from_frame=3)


def test_club_keeps_path_through_impact_slowdown(lib):
    # the club loses 12 m/s in the first 3 ms frame: more than half the alias span
    js, frames, picks, *_ = run(lib, scene(club_decel_mps2=4000.0))
    club_bins = [f.targets[f.club_index].rangeBin for f in frames]
    assert sum(p.club is not None and abs(p.club - c) < 0.05 for p, c in zip(picks, club_bins)) >= 7


def test_beam_is_bounded_and_counted(lib):
    js, frames, picks, requests, *_ = run(lib, scene(extras=[(50.0, 800.0, 5.0), (44.0, 700.0, 1.0)]))
    assert js.survivors <= fw.JOINT_BEAM
    frames_scored = js.counters[fw.JOINT_COUNTER_NAMES.index("frames")]
    pairings = js.counters[fw.JOINT_COUNTER_NAMES.index("pairings")]
    assert frames_scored == len(frames)
    assert pairings <= 1500 * frames_scored
    assert max(requests) <= fw.OBS_MAX_TARGETS


def test_same_input_same_links(lib):
    a = run(lib, scene(extras=[(50.0, 800.0, 5.0)]))[0]
    b = run(lib, scene(extras=[(50.0, 800.0, 5.0)]))[0]
    assert bytes(a) == bytes(b)


def test_repeated_timestamp_is_skipped(lib):
    js = make_joint(lib)
    arm(lib, js, 46.0, 0, None)
    t = [obs(1, 3000, 49.0, 1500.0, 45.0)]
    step(lib, js, 1, 3000, t)
    before = bytes(js)
    survivors, *_ = step(lib, js, 2, 3000, t)
    assert survivors == 0
    assert js.counters[fw.JOINT_COUNTER_NAMES.index("skipped")] == 1
    assert js.windowCount == 1


def test_frame_without_targets_is_a_miss_for_every_path(lib):
    js = make_joint(lib)
    arm(lib, js, 46.0, 0, fw.JointKin(44.0, 30.0, 0))
    step(lib, js, 1, 3000, [])
    assert js.nodes[js.bank][0].club.misses == 1


def test_wrap_mid_capture_matches_unwrapped(lib):
    plain = run(lib, scene(gate_us=0))[2]
    wrapped = run(lib, scene(gate_us=0xFFFFFFFF - 7000))[2]
    assert [(p.club, p.ball) for p in plain] == [(p.club, p.ball) for p in wrapped]


def test_nonfinite_target_is_dropped_not_propagated(lib):
    s = scene()
    js = make_joint(lib)
    arm(lib, js, s.origin_bin, s.gate_us, fw.JointKin(46.0, 38.0, 0))
    for f in s.build():
        bad = obs(f.frame, f.timestamp_us, float("nan"), 5000.0, 40.0)
        step(lib, js, f.frame, f.timestamp_us, [*f.targets, bad])
    assert math.isfinite(js.nodes[js.bank][0].score)
    assert js.survivors >= 1


def test_target_use_labels_the_newest_frame(lib):
    s = scene(extras=[(80.0, 500.0, 0.0)])
    js, frames, picks, *_ = run(lib, s)
    last = frames[-1]
    uses = [fw.JOINT_TARGET_USE_NAMES[lib.l3_joint_target_use(ctypes.byref(js), i)]
            for i in range(len(last.targets))]
    kinds = {round(t.rangeBin, 2): u for t, u in zip(last.targets, uses)}
    assert kinds[round(last.targets[last.club_index].rangeBin, 2)] == "club"
    assert kinds[round(last.ball_bin, 2)] == "ball"
    assert kinds[80.0] == "outside gate"


def test_angle_requests_cover_every_survivor_pick_once(lib):
    js, frames, picks, requests, *_ = run(lib, scene(extras=[(50.0, 800.0, 5.0)]))
    reqs = (fw.JointAngleReq * fw.OBS_MAX_TARGETS)()
    n = lib.l3_joint_angle_requests(ctypes.byref(js), reqs, fw.OBS_MAX_TARGETS)
    targets = [r.target for r in reqs[:n]]
    assert len(targets) == len(set(targets))
    newest = js.window[js.newestSlot]
    used = {l.club for l in newest.links[: newest.survivors]} | {l.ball for l in newest.links[: newest.survivors]}
    assert set(targets) == used - {fw.JOINT_NONE}
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -v`
Expected: the new tests FAIL (update is a stub returning 0).

- [ ] **Step 3: Implement the frame step**

Add to `l3_joint_search.c` (above the public functions):

```c
typedef struct {
    float   reward;
    float   speedMps;
    uint8_t target;               /* L3_JOINT_NONE: the miss (or waiting) option */
    uint8_t speedKnown;
    uint8_t starts;               /* this option starts the path */
    uint8_t pad;
} l3_joint_option_t;

typedef struct {
    float   score;
    uint8_t parent;
    uint8_t pad[3];
    l3_joint_option_t club;
    l3_joint_option_t ball;
} l3_joint_candidate_t;

static int32_t l3_joint_finite(float value)
{
    return (value == value) && (fabsf(value) < 1.0e30F);
}

static int32_t l3_joint_in_start_band(const l3_joint_t *js, float rangeBin, uint32_t timestampUs,
                                      float maxMps)
{
    float sinceGate = l3_joint_dt_s(timestampUs, js->gateTimestampUs);
    float beyond = js->cfg.startBeyondBins;

    if (sinceGate > 0.0F) {
        beyond += maxMps * sinceGate / js->cfg.binWidthM;   /* a late gate finds it out */
    }
    return (rangeBin >= js->originBin - js->cfg.startBehindBins) &&
           (rangeBin <= js->originBin + beyond);
}

/* One path's options this frame: the miss first (free before the path starts
 * and after it ends), then every target the path may take. Marks the targets
 * that were in this kind's gate. */
static uint32_t l3_joint_options(l3_joint_t *js, const l3_joint_path_t *path, uint32_t kind,
                                 const l3_target_obs_t *targets, uint32_t n, uint32_t timestampUs,
                                 l3_joint_option_t *out)
{
    const l3_joint_cfg_t *cfg = &js->cfg;
    float minMps = (kind == L3_JOINT_BALL) ? cfg->minBallMps : cfg->minClubMps;
    float maxMps = (kind == L3_JOINT_BALL) ? cfg->maxBallMps : cfg->maxClubMps;
    uint32_t count = 1U;
    uint32_t i;

    memset(&out[0], 0, sizeof(out[0]));
    out[0].target = L3_JOINT_NONE;
    out[0].reward = (path->state == L3_JOINT_PATH_ACTIVE) ? -cfg->missCost : 0.0F;
    if (path->state == L3_JOINT_PATH_ENDED) {
        return 1U;
    }
    for (i = 0U; i < n; i++) {
        const l3_target_obs_t *target = &targets[i];
        l3_joint_option_t *opt = &out[count];

        memset(opt, 0, sizeof(*opt));
        opt->target = (uint8_t)i;
        if (!l3_joint_finite(target->rangeBin) || !l3_joint_finite(target->dopplerAliasMps)) {
            continue;
        }
        if (path->state == L3_JOINT_PATH_UNSTARTED) {
            if (!l3_joint_in_start_band(js, target->rangeBin, timestampUs, maxMps)) {
                continue;
            }
            opt->starts = 1U;
            opt->speedMps = target->dopplerAliasMps;   /* until a second point resolves it */
        } else {
            float predicted = path->last.speedMps;

            if (!path->speedKnown) {
                float dtS = l3_joint_dt_s(timestampUs, path->last.timestampUs);

                if (dtS <= 0.0F) {
                    continue;
                }
                predicted = (target->rangeBin - path->last.rangeBin) * cfg->binWidthM / dtS;
            }
            opt->speedMps = l3_joint_resolve_speed(target->dopplerAliasMps, predicted,
                                                   cfg->velocitySpanMps);
            opt->speedKnown = 1U;
            if (opt->speedMps < minMps || opt->speedMps > maxMps) {
                continue;
            }
            if (!l3_joint_point_reward(cfg, kind, path, target->rangeBin, opt->speedMps,
                                       timestampUs, &opt->reward)) {
                continue;
            }
        }
        js->gateMask[i] |= (uint8_t)(1U << kind);
        count++;
    }
    return count;
}

/* Ordering of candidates: higher score first; ties by club target, ball
 * target, parent, ascending (none sorts last) -- the same on board and host. */
static int32_t l3_joint_before(const l3_joint_candidate_t *a, const l3_joint_candidate_t *b)
{
    if (a->score != b->score) {
        return a->score > b->score;
    }
    if (a->club.target != b->club.target) {
        return a->club.target < b->club.target;
    }
    if (a->ball.target != b->ball.target) {
        return a->ball.target < b->ball.target;
    }
    return a->parent < b->parent;
}

/* Insert into the sorted best list, dropping the worst when full. */
static uint32_t l3_joint_keep(l3_joint_candidate_t *best, uint32_t kept,
                              const l3_joint_candidate_t *cand)
{
    uint32_t slot = kept;

    while (slot > 0U && l3_joint_before(cand, &best[slot - 1U])) {
        if (slot < L3_JOINT_BEAM) {
            best[slot] = best[slot - 1U];
        }
        slot--;
    }
    if (slot < L3_JOINT_BEAM) {
        best[slot] = *cand;
        if (kept < L3_JOINT_BEAM) {
            kept++;
        }
    }
    return kept;
}

static void l3_joint_path_advance(const l3_joint_cfg_t *cfg, uint32_t kind, l3_joint_path_t *path,
                                  const l3_joint_option_t *opt, const l3_target_obs_t *targets,
                                  uint32_t frame, uint32_t timestampUs)
{
    uint32_t maxMisses = (kind == L3_JOINT_BALL) ? cfg->ballMaxMisses : cfg->clubMaxMisses;

    if (opt->target == L3_JOINT_NONE) {
        if (path->state != L3_JOINT_PATH_ACTIVE) {
            return;
        }
        path->misses++;
        if (path->misses > maxMisses) {
            if (path->points < 2U) {
                /* Never confirmed by a second point: a false start. */
                memset(path, 0, sizeof(*path));
            } else {
                path->state = L3_JOINT_PATH_ENDED;
            }
        }
        return;
    }
    if (opt->starts) {
        path->state = L3_JOINT_PATH_ACTIVE;
        path->startFrame = frame;
        path->points = 0U;
    }
    path->last.rangeBin = targets[opt->target].rangeBin;
    path->last.speedMps = opt->speedMps;
    path->last.timestampUs = timestampUs;
    path->speedKnown = opt->speedKnown;
    path->misses = 0U;
    if (path->points < 255U) {
        path->points++;
    }
}

static int16_t l3_joint_quantize_angle(float rad)
{
    float scaled = rad * 1.0e4F;

    if (!(scaled == scaled)) {
        return 0;
    }
    if (scaled > 32767.0F) {
        scaled = 32767.0F;
    }
    if (scaled < -32767.0F) {
        scaled = -32767.0F;
    }
    return (int16_t)((scaled >= 0.0F) ? (scaled + 0.5F) : (scaled - 0.5F));
}

static void l3_joint_store_target(l3_joint_point_t *out, const l3_target_obs_t *target)
{
    float confidence = target->confidence;

    memset(out, 0, sizeof(*out));
    out->rangeBin = target->rangeBin;
    out->dopplerAliasMps = target->dopplerAliasMps;
    if (confidence < 0.0F) {
        confidence = 0.0F;
    }
    if (confidence > 1.0F) {
        confidence = 1.0F;
    }
    out->confidence255 = (uint8_t)(confidence * 255.0F + 0.5F);
}
```

`l3_joint_update` (Task 4 inserts the forced write-out where marked):

```c
uint32_t l3_joint_update(l3_joint_t *js, const l3_target_obs_t *targets, uint32_t n,
                         uint32_t frame, uint32_t timestampUs, l3_club_track_t *clubOut,
                         l3_club_track_t *ballOut)
{
    l3_joint_option_t clubOpts[L3_OBS_MAX_TARGETS + 1U];
    l3_joint_option_t ballOpts[L3_OBS_MAX_TARGETS + 1U];
    l3_joint_candidate_t best[L3_JOINT_BEAM];
    const l3_joint_node_t *cur;
    l3_joint_node_t *next;
    l3_joint_frame_t *slot;
    uint32_t slotIndex;
    uint32_t kept = 0U;
    uint32_t p;
    uint32_t k;
    uint32_t i;

    if (!js->armed || js->survivors == 0U) {
        return 0U;
    }
    if (n > L3_OBS_MAX_TARGETS) {
        n = L3_OBS_MAX_TARGETS;
    }
    if (js->windowCount > 0U &&
        l3_joint_dt_s(timestampUs, js->window[js->newestSlot].timestampUs) <= 0.0F) {
        js->counters[L3_JOINT_COUNT_SKIPPED]++;
        return 0U;
    }
    /* TASK 4: forced write-out of the oldest frame goes here. Until then a
     * full window refuses the frame. */
    if (js->windowCount == L3_JOINT_WINDOW) {
        (void)clubOut;
        (void)ballOut;
        return 0U;
    }
    js->counters[L3_JOINT_COUNT_FRAMES]++;
    memset(js->gateMask, 0, sizeof(js->gateMask));
    cur = js->nodes[js->bank];
    for (p = 0U; p < js->survivors; p++) {
        uint32_t nc = l3_joint_options(js, &cur[p].club, L3_JOINT_CLUB, targets, n, timestampUs,
                                       clubOpts);
        uint32_t nb = l3_joint_options(js, &cur[p].ball, L3_JOINT_BALL, targets, n, timestampUs,
                                       ballOpts);
        uint32_t c;
        uint32_t b;

        for (c = 0U; c < nc; c++) {
            for (b = 0U; b < nb; b++) {
                l3_joint_candidate_t cand;

                if (clubOpts[c].target != L3_JOINT_NONE &&
                    clubOpts[c].target == ballOpts[b].target) {
                    continue;
                }
                cand.score = cur[p].score + clubOpts[c].reward + ballOpts[b].reward;
                if (clubOpts[c].target != L3_JOINT_NONE && ballOpts[b].target != L3_JOINT_NONE &&
                    targets[clubOpts[c].target].stat > targets[ballOpts[b].target].stat) {
                    cand.score += js->cfg.clubStrongerBonus;
                }
                js->counters[L3_JOINT_COUNT_PAIRINGS]++;
                if (!l3_joint_finite(cand.score)) {
                    js->counters[L3_JOINT_COUNT_NONFINITE]++;
                    continue;
                }
                cand.parent = (uint8_t)p;
                cand.pad[0] = cand.pad[1] = cand.pad[2] = 0U;
                cand.club = clubOpts[c];
                cand.ball = ballOpts[b];
                kept = l3_joint_keep(best, kept, &cand);
            }
        }
    }
    if (kept == 0U) {
        return 0U;   /* every pairing was non-finite: keep the survivors as they were */
    }
    slotIndex = (js->newestSlot + 1U) % L3_JOINT_WINDOW;
    slot = &js->window[slotIndex];
    memset(slot, 0, sizeof(*slot));
    slot->frame = frame;
    slot->timestampUs = timestampUs;
    slot->count = (uint8_t)n;
    slot->survivors = (uint8_t)kept;
    for (i = 0U; i < n; i++) {
        l3_joint_store_target(&slot->targets[i], &targets[i]);
    }
    next = js->nodes[js->bank ^ 1U];
    for (k = 0U; k < kept; k++) {
        next[k] = cur[best[k].parent];
        next[k].score = best[k].score;
        l3_joint_path_advance(&js->cfg, L3_JOINT_CLUB, &next[k].club, &best[k].club, targets,
                              frame, timestampUs);
        l3_joint_path_advance(&js->cfg, L3_JOINT_BALL, &next[k].ball, &best[k].ball, targets,
                              frame, timestampUs);
        slot->links[k].club = best[k].club.target;
        slot->links[k].ball = best[k].ball.target;
        slot->links[k].parent = best[k].parent;
    }
    js->bank ^= 1U;
    js->survivors = kept;
    js->newestSlot = slotIndex;
    js->windowCount++;
    for (i = 0U; i < L3_OBS_MAX_TARGETS; i++) {
        js->targetUse[i] = (i < n && js->gateMask[i] != 0U) ? L3_JOINT_TARGET_SCORED_LOWER
                                                           : L3_JOINT_TARGET_OUTSIDE;
    }
    if (slot->links[0].club != L3_JOINT_NONE) {
        js->targetUse[slot->links[0].club] = L3_JOINT_TARGET_CLUB;
    }
    if (slot->links[0].ball != L3_JOINT_NONE) {
        js->targetUse[slot->links[0].ball] = L3_JOINT_TARGET_BALL;
    }
    return kept;
}
```

Angle requests, set angles, now, target use, the window chain and a Task 3 finish:

```c
uint32_t l3_joint_angle_requests(l3_joint_t *js, l3_joint_angle_req_t *out, uint32_t max)
{
    const l3_joint_frame_t *f = &js->window[js->newestSlot];
    const l3_joint_node_t *nodes = js->nodes[js->bank];
    uint8_t listed[L3_OBS_MAX_TARGETS];
    uint32_t count = 0U;
    uint32_t s;

    if (!js->armed || js->windowCount == 0U) {
        return 0U;
    }
    memset(listed, 0, sizeof(listed));
    for (s = 0U; s < f->survivors; s++) {
        uint8_t picks[2];
        float speeds[2];
        uint32_t j;

        picks[0] = f->links[s].club;
        picks[1] = f->links[s].ball;
        speeds[0] = nodes[s].club.last.speedMps;   /* the path took the pick this frame */
        speeds[1] = nodes[s].ball.last.speedMps;
        for (j = 0U; j < 2U; j++) {
            if (picks[j] == L3_JOINT_NONE || picks[j] >= f->count || listed[picks[j]] ||
                count >= max) {
                continue;
            }
            listed[picks[j]] = 1U;
            out[count].target = picks[j];
            out[count].pad[0] = out[count].pad[1] = out[count].pad[2] = 0U;
            out[count].speedMps = speeds[j];
            count++;
        }
    }
    js->counters[L3_JOINT_COUNT_ANGLE_REQUESTS] += count;
    return count;
}

int32_t l3_joint_set_angles(l3_joint_t *js, uint32_t targetIndex, float azimuthRad,
                            float elevationRad, uint8_t anglesValid)
{
    l3_joint_point_t *point;

    if (!js->armed || js->windowCount == 0U ||
        targetIndex >= js->window[js->newestSlot].count) {
        return 0;
    }
    point = &js->window[js->newestSlot].targets[targetIndex];
    point->azimuth1e4 = l3_joint_quantize_angle(azimuthRad);
    point->elevation1e4 = l3_joint_quantize_angle(elevationRad);
    point->anglesValid = anglesValid;
    return 1;
}

static uint32_t l3_joint_oldest_slot(const l3_joint_t *js)
{
    return (js->newestSlot + L3_JOINT_WINDOW + 1U - js->windowCount) % L3_JOINT_WINDOW;
}

uint32_t l3_joint_window_ball(const l3_joint_t *js, uint32_t survivor,
                              l3_joint_ball_point_t *out, uint32_t max)
{
    uint8_t targets[L3_JOINT_WINDOW];
    uint32_t slots[L3_JOINT_WINDOW];
    const l3_joint_path_t *ball;
    uint32_t index = survivor;
    uint32_t slot = js->newestSlot;
    uint32_t depth;
    uint32_t count = 0U;

    if (survivor >= js->survivors || js->windowCount == 0U) {
        return 0U;
    }
    ball = &js->nodes[js->bank][survivor].ball;
    for (depth = 0U; depth < js->windowCount; depth++) {
        slots[depth] = slot;
        targets[depth] = js->window[slot].links[index].ball;
        index = js->window[slot].links[index].parent;
        slot = (slot + L3_JOINT_WINDOW - 1U) % L3_JOINT_WINDOW;
    }
    if (ball->state == L3_JOINT_PATH_UNSTARTED) {
        return 0U;   /* a reverted false start leaves picks in the links; they are not the ball */
    }
    for (depth = js->windowCount; depth-- > 0U && count < max;) {
        const l3_joint_frame_t *f = &js->window[slots[depth]];

        if (targets[depth] == L3_JOINT_NONE || f->frame < ball->startFrame) {
            continue;
        }
        out[count].frame = f->frame;
        out[count].timestampUs = f->timestampUs;
        out[count].point = f->targets[targets[depth]];
        count++;
    }
    return count;
}

/* ballPath = the written head + the best survivor's window chain. */
static void l3_joint_rebuild_ball_path(l3_joint_t *js)
{
    js->ballCount = js->ballWritten +
                    l3_joint_window_ball(js, 0U, &js->ballPath[js->ballWritten],
                                         L3_JOINT_BALL_POINTS - js->ballWritten);
}

void l3_joint_finish_frame(l3_joint_t *js, l3_club_track_t *clubOut, l3_club_track_t *ballOut)
{
    if (!js->armed || js->survivors == 0U) {
        return;
    }
    (void)clubOut;
    (void)ballOut;
    l3_joint_rebuild_ball_path(js);
}

int32_t l3_joint_now(const l3_joint_t *js, l3_joint_now_t *out)
{
    const l3_joint_node_t *best;
    const l3_joint_frame_t *f;
    const l3_joint_node_t *parent;
    float dtS;

    memset(out, 0, sizeof(*out));
    out->clubTarget = L3_JOINT_NONE;
    out->ballTarget = L3_JOINT_NONE;
    if (!js->armed || js->survivors == 0U || js->windowCount == 0U) {
        return 0;
    }
    best = &js->nodes[js->bank][0];
    f = &js->window[js->newestSlot];
    parent = &js->nodes[js->bank ^ 1U][f->links[0].parent];
    out->clubTarget = f->links[0].club;
    out->ballTarget = f->links[0].ball;
    out->clubState = best->club.state;
    out->ballState = best->ball.state;
    out->clubBin = best->club.last.rangeBin;
    out->clubSpeedMps = best->club.last.speedMps;
    out->ballBin = best->ball.last.rangeBin;
    out->ballSpeedMps = best->ball.last.speedMps;
    out->score = best->score;
    if (parent->club.state == L3_JOINT_PATH_ACTIVE && parent->club.speedKnown) {
        dtS = l3_joint_dt_s(f->timestampUs, parent->club.last.timestampUs);
        out->clubPredictedBin = parent->club.last.rangeBin +
                                parent->club.last.speedMps * dtS / js->cfg.binWidthM;
        out->clubPredicted = 1U;
    }
    if (parent->ball.state == L3_JOINT_PATH_ACTIVE && parent->ball.speedKnown) {
        dtS = l3_joint_dt_s(f->timestampUs, parent->ball.last.timestampUs);
        out->ballPredictedBin = parent->ball.last.rangeBin +
                                parent->ball.last.speedMps * dtS / js->cfg.binWidthM;
        out->ballPredicted = 1U;
    }
    return 1;
}

uint8_t l3_joint_target_use(const l3_joint_t *js, uint32_t targetIndex)
{
    return (targetIndex < L3_OBS_MAX_TARGETS) ? js->targetUse[targetIndex]
                                              : (uint8_t)L3_JOINT_TARGET_OUTSIDE;
}
```

Note on `l3_joint_now`'s parent: after the update, `nodes[bank ^ 1]` still holds the previous
frame's survivors, which `links[].parent` index — valid until the next update.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -v`
Expected: PASS. The scenes are 8 frames, so the full-window refusal is never hit. If a scene test
fails on identities, debug with `superpowers:systematic-debugging` — print each frame's
`js.window[slot].links[:survivors]` and node scores — and fix the cause; the numeric defaults may be
tuned only with a ledgered ruling that names the scene and the value.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_joint_search.c tests/iwr6843_twotrack.py tests/test_iwr6843_firmware_joint.py
git commit -m "iwr: joint search frame step: options, pairings, beam and window links" -- firmware/iwr6843/l3_joint_search.c tests/iwr6843_twotrack.py tests/test_iwr6843_firmware_joint.py
```

---

### Task 4: Write-out, confirmation and launch

**Files:**
- Modify: `firmware/iwr6843/l3_joint_search.c`
- Test: `tests/test_iwr6843_firmware_joint.py`

**Interfaces:**
- Consumes: Task 3.
- Produces: forced and agreed write-out (club points → `clubOut`, ball points → `ballPath` head
  and `ballOut`), `l3_joint_rival`, confirmation with the drop of rivals, `done`,
  `l3_joint_ball_point`, `l3_joint_launch`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def az_el(target):
    """A ball leaving 4 degrees right and 12 up; the club straight on."""
    return (math.radians(4.0), math.radians(12.0)) if target.stat < 5000 else (0.0, 0.0)


def test_long_capture_runs_past_the_window(lib):
    js, frames, picks, *_ = run(lib, scene(frames=20))
    assert js.counters[fw.JOINT_COUNTER_NAMES.index("frames")] == 20
    assert js.windowCount <= fw.JOINT_WINDOW


def test_agreed_frames_write_club_points_out(lib):
    js, frames, picks, _, club_out, ball_out = run(lib, scene(frames=12))
    assert js.counters[fw.JOINT_COUNTER_NAMES.index("written")] >= 4
    assert club_out.count >= 4
    assert ball_out.count == js.ballWritten


def test_single_point_ball_is_never_written_out(lib):
    # a stationary return in the band starts false ball paths; none may reach the written head
    js, frames, picks, _, _, ball_out = run(lib, scene(frames=14, extras=[(47.0, 3000.0, 0.0)]))
    bins = ball_path_bins(lib, js)
    assert all(abs(b - 47.0) > 0.05 for b in bins)
    for i in range(ball_out.count):
        point = fw.TrackPoint()
        lib.l3_track_point(ctypes.byref(ball_out), i, ctypes.byref(point))
        assert abs(point.rangeBin - 47.0) > 0.05


def test_forced_write_out_keeps_one_ancestry(lib):
    # two equally good balls from the origin at 45 and 52 m/s stay ambiguous past the window
    s = scene(frames=14, movers=[(46.0, 52.0, 1500.0)])
    js, *_ = run(lib, s)
    assert js.counters[fw.JOINT_COUNTER_NAMES.index("forced")] >= 1
    depth = js.windowCount - 1
    ancestors = set()
    for survivor in range(js.survivors):
        index, slot = survivor, js.newestSlot
        for _ in range(depth):
            index = js.window[slot].links[index].parent
            slot = (slot - 1) % fw.JOINT_WINDOW
        ancestors.add(index)
    assert len(ancestors) == 1


def test_three_ball_points_do_not_confirm(lib):
    js, *_ = run(lib, scene(frames=3))
    assert js.confirmed == 0


def test_clean_ball_confirms_and_launches(lib):
    js, *_ = run(lib, scene(frames=10), angles=az_el)
    assert js.confirmed == 1
    launch = fw.Launch()
    used = lib.l3_joint_launch(ctypes.byref(js), ctypes.byref(launch))
    assert used >= 4
    assert launch.speedMps == pytest.approx(50.0, rel=0.1)


def test_an_equal_rival_blocks_confirmation(lib):
    js, *_ = run(lib, scene(frames=7, movers=[(46.0, 52.0, 1500.0)]))
    assert lib.l3_joint_rival(ctypes.byref(js)) >= 0
    assert js.confirmed == 0


def test_confirmation_drops_rivals(lib):
    js, *_ = run(lib, scene(frames=10))
    assert js.confirmed == 1
    assert lib.l3_joint_rival(ctypes.byref(js)) == -1


def test_ball_leaving_too_slowly_never_confirms(lib):
    js, *_ = run(lib, scene(frames=10, ball_mps=8.0))
    assert js.confirmed == 0


def test_done_after_the_confirmed_ball_ends(lib):
    js, *_ = run(lib, scene(frames=14, missing_ball=(10, 11, 12, 13, 14)))
    assert js.confirmed == 1 and js.done == 1


def test_ball_path_holds_the_early_points(lib):
    s = scene(frames=14)
    js, frames, *_ = run(lib, s)
    bins = ball_path_bins(lib, js)
    expected = [f.ball_bin for f in frames if f.ball_bin is not None][: len(bins)]
    assert bins == pytest.approx(expected, abs=0.05)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -v`
Expected: the new tests FAIL (window refuses frame 9; nothing confirms; launch stub returns 0).

- [ ] **Step 3: Implement**

Add:

```c
/* The survivor's ancestor index at depth frames back from the newest. */
static uint32_t l3_joint_ancestor(const l3_joint_t *js, uint32_t survivor, uint32_t depth)
{
    uint32_t slot = js->newestSlot;
    uint32_t index = survivor;

    while (depth-- > 0U) {
        index = js->window[slot].links[index].parent;
        slot = (slot + L3_JOINT_WINDOW - 1U) % L3_JOINT_WINDOW;
    }
    return index;
}

/* Keep the survivors with keep[s] set, in order; the newest links follow. */
static void l3_joint_keep_marked(l3_joint_t *js, const uint8_t *keep)
{
    l3_joint_node_t *nodes = js->nodes[js->bank];
    l3_joint_frame_t *f = &js->window[js->newestSlot];
    uint32_t kept = 0U;
    uint32_t s;

    for (s = 0U; s < js->survivors; s++) {
        if (!keep[s]) {
            continue;
        }
        nodes[kept] = nodes[s];
        f->links[kept] = f->links[s];
        kept++;
    }
    js->survivors = kept;
    f->survivors = (uint8_t)kept;
}

static void l3_joint_to_track_point(const l3_joint_cfg_t *cfg, uint32_t frame,
                                    uint32_t timestampUs, const l3_joint_point_t *point,
                                    l3_track_point_t *out)
{
    float azimuth;
    float elevation;

    memset(out, 0, sizeof(*out));
    out->frame = frame;
    out->timestampUs = timestampUs;
    out->rangeBin = point->rangeBin;
    out->rangeM = point->rangeBin * cfg->binWidthM;
    out->radialVelocityMps = 0.0F;   /* not kept by the window */
    out->dopplerAliasMps = point->dopplerAliasMps;
    out->azimuthRad = (float)point->azimuth1e4 * 1.0e-4F;
    out->elevationRad = (float)point->elevation1e4 * 1.0e-4F;
    out->anglesValid = point->anglesValid;
    out->confidence = (float)point->confidence255 / 255.0F;
    out->coherence = 1.0F;
    azimuth = (out->anglesValid & L3_OBS_ANGLE_AZIMUTH) ? out->azimuthRad : 0.0F;
    elevation = (out->anglesValid & L3_OBS_ANGLE_ELEVATION) ? out->elevationRad : 0.0F;
    l3_frames_observe(&cfg->cal, out->rangeM, azimuth, elevation, &out->position);
}

/* The best survivor's ball path counts a point at `frame`: started by then
 * and past its false-start stage (two points), or it would not be settled. */
static int32_t l3_joint_ball_settled(const l3_joint_t *js, uint32_t frame)
{
    const l3_joint_path_t *ball = &js->nodes[js->bank][0].ball;

    return ball->state == L3_JOINT_PATH_UNSTARTED || frame < ball->startFrame ||
           ball->points >= 2U;
}

/* Write the oldest window frame out. Unforced, only when every survivor
 * descends from one explanation there and the ball path is settled; forced
 * (the window is full), the best's ancestor wins and the others are dropped. */
static int32_t l3_joint_write_oldest(l3_joint_t *js, l3_club_track_t *clubOut,
                                     l3_club_track_t *ballOut, int32_t force)
{
    uint32_t depth = js->windowCount - 1U;
    uint32_t oldest = l3_joint_oldest_slot(js);
    uint32_t keepIndex = l3_joint_ancestor(js, 0U, depth);
    const l3_joint_frame_t *f = &js->window[oldest];
    const l3_joint_path_t *ball;
    uint8_t keep[L3_JOINT_BEAM];
    uint32_t disputed = 0U;
    uint32_t s;
    l3_joint_link_t link;
    l3_track_point_t point;

    for (s = 0U; s < js->survivors; s++) {
        keep[s] = (uint8_t)(l3_joint_ancestor(js, s, depth) == keepIndex);
        disputed += keep[s] ? 0U : 1U;
    }
    if (!force && (disputed > 0U || !l3_joint_ball_settled(js, f->frame))) {
        return 0;
    }
    if (disputed > 0U) {
        l3_joint_keep_marked(js, keep);
        js->counters[L3_JOINT_COUNT_FORCED]++;
    }
    link = f->links[keepIndex];
    ball = &js->nodes[js->bank][0].ball;
    if (link.club != L3_JOINT_NONE && clubOut != NULL) {
        l3_joint_to_track_point(&js->cfg, f->frame, f->timestampUs, &f->targets[link.club],
                                &point);
        l3_track_append_point(clubOut, &point);
    }
    /* A single-point ball can be forced out only if the window is shorter
     * than a false start lasts, which the #error above rules out. */
    if (link.ball != L3_JOINT_NONE && ball->state != L3_JOINT_PATH_UNSTARTED &&
        f->frame >= ball->startFrame && ball->points >= 2U) {
        if (js->ballWritten < L3_JOINT_BALL_POINTS) {
            js->ballPath[js->ballWritten].frame = f->frame;
            js->ballPath[js->ballWritten].timestampUs = f->timestampUs;
            js->ballPath[js->ballWritten].point = f->targets[link.ball];
            js->ballWritten++;
        }
        if (ballOut != NULL) {
            l3_joint_to_track_point(&js->cfg, f->frame, f->timestampUs, &f->targets[link.ball],
                                    &point);
            l3_track_append_point(ballOut, &point);
        }
    }
    js->windowCount--;
    js->counters[L3_JOINT_COUNT_WRITTEN]++;
    return 1;
}
```

Replace the Task 3 placeholder in `l3_joint_update`:

```c
    if (js->windowCount == L3_JOINT_WINDOW) {
        (void)l3_joint_write_oldest(js, clubOut, ballOut, 1);
    }
```

Fit, rival, confirmation, finish, ball point and launch:

```c
/* Least squares of range against time from referenceUs over the points:
 * rate in bins per second, the fitted range at the reference, RMS residual. */
static int32_t l3_joint_fit(const l3_joint_ball_point_t *points, uint32_t n, uint32_t referenceUs,
                            float *rateBinsPerS, float *binAtReference, float *residualBins)
{
    float sumT = 0.0F;
    float sumR = 0.0F;
    float sumTT = 0.0F;
    float sumTR = 0.0F;
    float squares = 0.0F;
    float denominator;
    uint32_t i;

    if (n < 2U) {
        return 0;
    }
    for (i = 0U; i < n; i++) {
        float t = l3_joint_dt_s(points[i].timestampUs, referenceUs);

        sumT += t;
        sumR += points[i].point.rangeBin;
        sumTT += t * t;
        sumTR += t * points[i].point.rangeBin;
    }
    denominator = (float)n * sumTT - sumT * sumT;
    if (denominator <= 0.0F) {
        return 0;
    }
    *rateBinsPerS = ((float)n * sumTR - sumT * sumR) / denominator;
    *binAtReference = (sumR - *rateBinsPerS * sumT) / (float)n;
    for (i = 0U; i < n; i++) {
        float t = l3_joint_dt_s(points[i].timestampUs, referenceUs);
        float error = points[i].point.rangeBin - (*binAtReference + *rateBinsPerS * t);

        squares += error * error;
    }
    *residualBins = sqrtf(squares / (float)n);
    return 1;
}

/* A survivor's first `count` ball points: the written head, then its chain. */
static uint32_t l3_joint_first_ball(const l3_joint_t *js, uint32_t survivor,
                                    l3_joint_ball_point_t *out, uint32_t count)
{
    uint32_t head = (js->ballWritten < count) ? js->ballWritten : count;

    memcpy(out, js->ballPath, head * sizeof(out[0]));
    return head + l3_joint_window_ball(js, survivor, &out[head], count - head);
}

int32_t l3_joint_rival(const l3_joint_t *js)
{
    l3_joint_ball_point_t mine[L3_JOINT_BALL_POINTS];
    l3_joint_ball_point_t theirs[L3_JOINT_BALL_POINTS];
    uint32_t want = js->cfg.confirmPoints;
    uint32_t have;
    uint32_t s;

    if (js->survivors == 0U) {
        return -1;
    }
    have = l3_joint_first_ball(js, 0U, mine, want);
    for (s = 1U; s < js->survivors; s++) {
        uint32_t other = l3_joint_first_ball(js, s, theirs, want);
        uint32_t i;

        if (other != have) {
            return (int32_t)s;
        }
        for (i = 0U; i < have; i++) {
            /* Same frame, same range: the same target (local maxima never share a range). */
            if (mine[i].frame != theirs[i].frame ||
                mine[i].point.rangeBin != theirs[i].point.rangeBin) {
                return (int32_t)s;
            }
        }
    }
    return -1;
}

static void l3_joint_try_confirm(l3_joint_t *js)
{
    const l3_joint_cfg_t *cfg = &js->cfg;
    const l3_joint_node_t *nodes = js->nodes[js->bank];
    uint8_t keep[L3_JOINT_BEAM];
    float rate;
    float atGate;
    float residual;
    float rateMps;
    float crossUs;
    int32_t rival;
    uint32_t s;

    if (js->ballCount < cfg->confirmPoints ||
        !l3_joint_fit(js->ballPath, js->ballCount, js->gateTimestampUs, &rate, &atGate,
                      &residual)) {
        return;
    }
    rateMps = rate * cfg->binWidthM;
    if (rateMps < cfg->minBallMps || rateMps > cfg->maxBallMps || residual > cfg->maxResidualBins) {
        return;
    }
    crossUs = (js->originBin - atGate) / rate * 1.0e6F;   /* origin crossing minus the gate */
    if (fabsf(crossUs) > (float)cfg->impactToleranceUs) {
        return;
    }
    rival = l3_joint_rival(js);
    if (rival >= 0 && nodes[0].score - nodes[rival].score < cfg->confirmMargin) {
        return;
    }
    js->confirmed = 1U;
    /* Rivals disagree on the first ball points; the launch must not flip. */
    while ((rival = l3_joint_rival(js)) >= 0) {
        for (s = 0U; s < js->survivors; s++) {
            keep[s] = (uint8_t)(s != (uint32_t)rival);
        }
        l3_joint_keep_marked(js, keep);
    }
}

void l3_joint_finish_frame(l3_joint_t *js, l3_club_track_t *clubOut, l3_club_track_t *ballOut)
{
    if (!js->armed || js->survivors == 0U) {
        return;
    }
    while (js->windowCount > 1U && l3_joint_write_oldest(js, clubOut, ballOut, 0)) {
    }
    l3_joint_rebuild_ball_path(js);
    if (!js->confirmed) {
        l3_joint_try_confirm(js);
        l3_joint_rebuild_ball_path(js);
    }
    js->done = (uint8_t)(js->confirmed &&
                         js->nodes[js->bank][0].ball.state == L3_JOINT_PATH_ENDED);
}

int32_t l3_joint_ball_point(const l3_joint_t *js, uint32_t index, l3_track_point_t *out)
{
    const l3_joint_ball_point_t *p;

    if (index >= js->ballCount) {
        return 0;
    }
    p = &js->ballPath[index];
    l3_joint_to_track_point(&js->cfg, p->frame, p->timestampUs, &p->point, out);
    return 1;
}

static int32_t l3_joint_ball_point_at(const void *ctx, uint32_t index, l3_track_point_t *out)
{
    return l3_joint_ball_point((const l3_joint_t *)ctx, index, out);
}

uint32_t l3_joint_launch(const l3_joint_t *js, l3_launch_t *out)
{
    l3_delivery_t fit;
    uint32_t last = (js->ballCount < js->cfg.launchPoints) ? js->ballCount : js->cfg.launchPoints;
    uint32_t used;

    memset(out, 0, sizeof(*out));
    if (!js->confirmed) {
        return 0U;
    }
    used = l3_delivery_fit(l3_joint_ball_point_at, js, 0U, last, js->cfg.launchPoints,
                           js->cfg.binWidthM, js->cfg.maxAngleResidualM, &fit);
    if (used == 0U) {
        return 0U;
    }
    l3_launch_from_delivery(&fit, js->gateTimestampUs, out);
    return used;
}
```

Remove the Task 3 `l3_joint_finish_frame` and the stubs these replace.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_joint.py -v`
Expected: PASS. `test_forced_write_out_keeps_one_ancestry` depends on the two balls staying
ambiguous; if the 52 m/s mover is resolved by the scores before the window fills (so nothing is
forced), make the two chains closer (e.g. 50 and 50.5 m/s, 0.2 bins apart) rather than weakening the
assertion, and ledger it.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_joint_search.c tests/test_iwr6843_firmware_joint.py
git commit -m "iwr: joint search write-out, confirmation and launch" -- firmware/iwr6843/l3_joint_search.c tests/test_iwr6843_firmware_joint.py
```

---

### Task 5: Replay runs the joint search

**Files:**
- Modify: `src/openflight/iwr6843/firmware_replay.py`
- Test: `tests/test_iwr6843_firmware_replay.py`

**Interfaces:**
- Consumes: Tasks 0–4 C API.
- Produces:
  - `ReplayConfig.joint_search: bool = False`
  - `@dataclass(frozen=True) class JointFrameSummary: club_bin: float | None; ball_bin: float | None; club_predicted_bin: float | None; ball_predicted_bin: float | None; target_use: tuple[str, ...]; runner_up: tuple[tuple[int, float], ...]  # (timestamp_us, bin)`
  - `ReplayFrame.joint: JointFrameSummary | None = None`
  - `ReplayResult.joint_counters: dict[str, int] | None` (None when not run)
  - With `joint_search`, `ReplayResult.ball_points` is the final ball path: the written-out points
    (`ballOut`) followed by the best survivor's window chain at the end; `points` gains the
    written-out club points.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_iwr6843_firmware_replay.py`,
  which already has `RECORDINGS`, a `lib` fixture and a helper that loads a manifest entry into a
  `ReplayConfig` — reuse those names as they are in the file)

```python
def _twotrack_config(**extra):
    entry = _manifest_entry("iwr6843_20260824_120408_601_001.l3dump")
    return replace(_config_from_entry(entry), **extra)


def test_joint_search_replays_the_two_track_capture(lib):
    raw = (RECORDINGS / "iwr6843_20260824_120408_601_001.l3dump").read_bytes()
    result = fr.replay_dump(raw, _twotrack_config(joint_search=True), lib=lib)
    assert result.joint_counters is not None
    assert result.joint_counters["frames"] > 0
    assert result.launch is not None and 40.0 <= result.launch.speed_mps <= 50.0
    post = [f for f in result.frames if f.joint is not None]
    assert post and all(len(f.joint.target_use) == len(f.targets) for f in post)


def test_joint_search_off_leaves_the_frames_without_joint_fields(lib):
    raw = (RECORDINGS / "iwr6843_20260824_120408_601_001.l3dump").read_bytes()
    result = fr.replay_dump(raw, _twotrack_config(), lib=lib)
    assert result.joint_counters is None
    assert all(f.joint is None for f in result.frames)


def test_joint_ball_points_are_the_final_path(lib):
    raw = (RECORDINGS / "iwr6843_20260824_120408_601_001.l3dump").read_bytes()
    result = fr.replay_dump(raw, _twotrack_config(joint_search=True), lib=lib)
    times = [p.timestamp_us for p in result.ball_points]
    assert times == sorted(times) and len(times) == len(set(times))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_replay.py -k joint -v`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'joint_search'`).

- [ ] **Step 3: Implement**

In `ReplayConfig` after `ball_hypotheses`:

```python
    # The joint club/ball path search (l3_joint_search.c) after impact instead
    # of the club follow and the ball tracker; host only until the board switches.
    joint_search: bool = False
```

Add `JointFrameSummary` next to `HypothesisSummary`, `ReplayFrame.joint`, and
`ReplayResult.joint_counters: dict[str, int] | None = None` (keyword default, after the existing
fields).

In `replay_dump`, where `ball_track` is initialised:

```python
    joint = None
    joint_club_out = joint_ball_out = None
    if config.joint_search:
        joint_cfg = fw.JointCfg()
        lib.l3_joint_cfg_defaults(ctypes.byref(joint_cfg))
        joint_cfg.binWidthM = track_cfg.binWidthM
        joint_cfg.velocitySpanMps = track_cfg.velocitySpanMps
        joint_cfg.maxAngleResidualM = track_cfg.maxAngleResidualM
        joint_cfg.cal = track_cfg.cal
        joint = fw.Joint()
        lib.l3_joint_init(ctypes.byref(joint), ctypes.byref(joint_cfg))
        joint_ball_out = fw.ClubTrack()
        lib.l3_track_init(ctypes.byref(joint_ball_out), ctypes.byref(track_cfg))
```

(`track_cfg` is the club track's config variable in `replay_dump`; use its actual name.)

At both `l3_ball_track_arm` sites add, right after:

```python
            if joint is not None:
                _arm_joint(lib, joint, float(destination), impact_us, track)
```

with

```python
def _arm_joint(lib, joint, origin_bin: float, gate_us: int, track) -> None:
    """``l3_joint_arm`` as l3_dump.c calls it: the club continues from the pre-impact
    club track's last point and fitted speed, or starts like the ball without one."""
    seed = None
    if track.active and track.count > 0:
        newest = fw.TrackPoint()
        lib.l3_track_point(ctypes.byref(track), track.count - 1, ctypes.byref(newest))
        seed = fw.JointKin(
            float(newest.rangeBin),
            float(lib.l3_track_speed_mps(ctypes.byref(track), fw.TRACK_FOLLOW_FIT_POINTS)),
            int(newest.timestampUs),
        )
    lib.l3_joint_arm(
        ctypes.byref(joint), origin_bin, gate_us, ctypes.byref(seed) if seed is not None else None
    )
```

(`impact_us` is whatever timestamp the same site passes to `l3_ball_track_arm`; add
`TRACK_FOLLOW_FIT_POINTS = 4` to `firmware_host.py` if it is not mirrored yet, matching
`L3_TRACK_FOLLOW_FIT_POINTS`.)

Pass `joint` and `joint_ball_out` into `_replay_post_frame`, and at its top branch:

```python
    if joint is not None:
        return _replay_joint_frame(
            lib, cube, frame, timestamp_us, window_start, count, n_tx, targets, found, floor,
            cal, chirp_period_s, joint, track, joint_ball_out, launch, shot, ball_position,
            points, trig_state,
        )
```

placed after `found` is computed, using `joint.cfg.snr` instead of `ball_track.cfg.snr` for
`ball_params` when `joint` is set. `_replay_joint_frame` follows the board's order:

```python
def _replay_joint_frame(  # pylint: disable=too-many-arguments,too-many-locals
    lib, cube, frame, timestamp_us, window_start, count, n_tx, targets, found, floor, cal,
    chirp_period_s, joint, track, ball_out, launch, shot, ball_position, points, trig_state,
) -> ReplayFrame:
    """``l3_considerBallTrack`` with the joint search: update, angles for the
    targets survivors use, finish (write-out, confirmation), launch, shot machine."""
    club_before = track.count
    lib.l3_joint_update(
        ctypes.byref(joint), targets, found, frame, timestamp_us,
        ctypes.byref(track), ctypes.byref(ball_out),
    )
    requests = (fw.JointAngleReq * fw.OBS_MAX_TARGETS)()
    wanted = lib.l3_joint_angle_requests(ctypes.byref(joint), requests, fw.OBS_MAX_TARGETS)
    angle = None
    now = fw.JointNow()
    for req in requests[:wanted]:
        obs_angle, flags = _estimate_angles(
            lib, cal, cube, frame, window_start, n_tx, targets[req.target], float(req.speedMps),
            chirp_period_s,
        )
        if obs_angle is not None:
            lib.l3_joint_set_angles(
                ctypes.byref(joint), req.target, obs_angle.azimuthRad, obs_angle.elevationRad, flags
            )
    lib.l3_joint_finish_frame(ctypes.byref(joint), ctypes.byref(track), ctypes.byref(ball_out))
    lib.l3_joint_now(ctypes.byref(joint), ctypes.byref(now))
    for index in range(club_before, track.count):
        point = fw.TrackPoint()
        lib.l3_track_point(ctypes.byref(track), index, ctypes.byref(point))
        points.append(_point_summary(point))
    lib.l3_joint_launch(ctypes.byref(joint), ctypes.byref(launch))
    shot_in = fw.ShotInput()
    shot_in.ballPosition = ball_position
    shot_in.postFrame = 1
    shot_in.ballTrackDone = joint.done
    if lib.l3_shot_update(
        ctypes.byref(shot), ctypes.byref(shot_in), frame
    ) == fw.SHOT_STATE_NAMES.index("solve"):
        shot_in.solved = 1
        lib.l3_shot_update(ctypes.byref(shot), ctypes.byref(shot_in), frame)
    return ReplayFrame(
        frame, timestamp_us, window_start, count, float(floor), trig_state, False,
        tuple(_target_summary(targets[i]) for i in range(found)),
        "joint", _pick_bin(targets, now.clubTarget), angle, None, "none",
        fw.SHOT_STATE_NAMES[shot.state],
        "confirmed" if joint.confirmed else "searching",
        _pick_bin(targets, now.ballTarget),
        retain=None,
        joint=_joint_summary(lib, joint, now, found),
    )


def _pick_bin(targets, index: int) -> float | None:
    return float(targets[index].rangeBin) if index != fw.JOINT_NONE else None


def _joint_summary(lib, joint, now, found: int) -> JointFrameSummary:
    rival = lib.l3_joint_rival(ctypes.byref(joint))
    runner_up: tuple[tuple[int, float], ...] = ()
    if rival >= 0:
        chain = (fw.JointBallPoint * fw.JOINT_WINDOW)()
        n = lib.l3_joint_window_ball(ctypes.byref(joint), rival, chain, fw.JOINT_WINDOW)
        runner_up = tuple((int(p.timestampUs), float(p.point.rangeBin)) for p in chain[:n])
    return JointFrameSummary(
        club_bin=float(now.clubBin) if now.clubState else None,
        ball_bin=float(now.ballBin) if now.ballState else None,
        club_predicted_bin=float(now.clubPredictedBin) if now.clubPredicted else None,
        ball_predicted_bin=float(now.ballPredictedBin) if now.ballPredicted else None,
        target_use=tuple(
            fw.JOINT_TARGET_USE_NAMES[lib.l3_joint_target_use(ctypes.byref(joint), i)]
            for i in range(found)
        ),
        runner_up=runner_up,
    )
```

After the frame loop in `replay_dump`, when `joint` is set, build the final ball path and counters:

```python
    if joint is not None:
        ball_points = _joint_final_ball(lib, joint, joint_ball_out)
        joint_counters = dict(zip(fw.JOINT_COUNTER_NAMES, joint.counters))
```

```python
def _joint_final_ball(lib, joint, ball_out) -> list[PointSummary]:
    """The written-out ball points, then the best survivor's window chain."""
    out = []
    for index in range(ball_out.count):
        point = fw.TrackPoint()
        lib.l3_track_point(ctypes.byref(ball_out), index, ctypes.byref(point))
        out.append(_point_summary(point))
    chain = (fw.JointBallPoint * fw.JOINT_WINDOW)()
    n = lib.l3_joint_window_ball(ctypes.byref(joint), 0, chain, fw.JOINT_WINDOW)
    for p in chain[:n]:
        point = fw.TrackPoint()
        point.frame, point.timestampUs = p.frame, p.timestampUs
        point.rangeBin = p.point.rangeBin
        point.rangeM = p.point.rangeBin * joint.cfg.binWidthM
        point.dopplerAliasMps = p.point.dopplerAliasMps
        point.confidence = p.point.confidence255 / 255.0
        out.append(_point_summary(point))
    return out
```

Pass `joint_counters` (None otherwise) into `ReplayResult`. The launch summary already reads
`launch`, so it now comes from `l3_joint_launch`.

- [ ] **Step 4: Run the replay tests**

Run: `uv run pytest tests/test_iwr6843_firmware_replay.py -q`
Expected: PASS (existing tests unchanged: `joint_search` defaults to False).

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/firmware_replay.py src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_replay.py
git commit -m "iwr: replay runs the joint path search when asked" -- src/openflight/iwr6843/firmware_replay.py src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_replay.py
```

---

### Task 6: Viewer shows the joint search

**Files:**
- Modify: `src/openflight/iwr6843/dump_viewer.py`, `scripts/iwr6843/dump_viewer.html`
- Test: `tests/test_iwr6843_dump_viewer.py`

**Interfaces:**
- Consumes: Task 5 `ReplayConfig.joint_search`, `ReplayFrame.joint`, `ReplayResult.joint_counters`.
- Produces: `ViewerOptions.ball_search: str | None` replacing `ball_hypotheses` — values
  `None` (firmware default), `"legacy"`, `"hypotheses"`, `"joint"`; the select `#ball_search`;
  firmware section frames carry `joint` (dict or null) and the section carries `joint_counters`.

- [ ] **Step 1: Write the failing tests** (append; the file has a `RECORDING` path fixture and uses
  `analyze_dump` — reuse its names)

```python
def test_joint_ball_search_reaches_the_frames():
    raw = TWOTRACK.read_bytes()
    out = analyze_dump(raw, ViewerOptions(tee_bin=39, pitch_deg=10.5, ball_search="joint"))
    fw_section = out["firmware"]
    assert fw_section["joint_counters"]["frames"] > 0
    joint_frames = [f for f in fw_section["frames"] if f["joint"] is not None]
    assert joint_frames
    assert set(joint_frames[-1]["joint"]) == {
        "club_bin", "ball_bin", "club_predicted_bin", "ball_predicted_bin", "target_use", "runner_up",
    }


@pytest.mark.parametrize("value, hyp, joint", [
    (None, None, False), ("legacy", False, False), ("hypotheses", True, False), ("joint", None, True),
])
def test_ball_search_maps_to_the_replay_config(value, hyp, joint):
    config = replay_config_for(ViewerOptions(tee_bin=39, ball_search=value))
    assert (config.ball_hypotheses, config.joint_search) == (hyp, joint)


def test_unknown_ball_search_is_rejected():
    with pytest.raises(ValueError):
        ViewerOptions.from_mapping({"ball_search": "sideways"})
```

(`TWOTRACK = RECORDINGS / "iwr6843_20260824_120408_601_001.l3dump"`; `replay_config_for` is the
config construction currently inline in `firmware_section`, extracted so the mapping is testable.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py -v`
Expected: FAIL (`ball_search` unknown).

- [ ] **Step 3: Implement**

`dump_viewer.py`:

```python
BALL_SEARCHES = {None: (None, False), "legacy": (False, False),
                 "hypotheses": (True, False), "joint": (None, True)}
```

`ViewerOptions.ball_search: str | None = None`, with `from_mapping` raising `ValueError` for a
value not in `BALL_SEARCHES`. Extract:

```python
def replay_config_for(options: ViewerOptions) -> fr.ReplayConfig:
    """The replay configuration the page's options select."""
    hypotheses, joint = BALL_SEARCHES[options.ball_search]
    return fr.ReplayConfig(
        tee_bin=tee_bin_for(options),
        # ... every field firmware_section passes today, unchanged ...
        ball_hypotheses=hypotheses,
        joint_search=joint,
    )
```

`firmware_section` calls it, adds `"joint_counters": result.joint_counters` and each frame dict
gets `"joint": asdict(frame.joint) if frame.joint else None` (use the existing frame
serialisation — `_jsonable`/`asdict` as the file does). Update `__all__` and
`scripts/analysis/evaluate_iwr_tracking.py`'s import if it used `ball_hypotheses` via options (it
does not; it uses `ReplayConfig` directly).

`dump_viewer.html`:
- select: `<label>ball search <select id="ball_search"><option value="">firmware default</option><option value="legacy">legacy</option><option value="hypotheses">hypotheses</option><option value="joint">joint</option></select></label>`, and `"ball_hypotheses"` → `"ball_search"` in the option-id list.
- range × time map, when frames carry `joint`: two marker traces "club predicted" / "ball predicted"
  (open circles, `C.club`/`C.ball`) at `(bin→m, t)` of `club_predicted_bin` / `ball_predicted_bin`;
  targets whose `target_use[i]` is `"outside gate"` drawn as grey `x`, `"scored lower"` as grey
  open diamonds; the last frame's `runner_up` as a dotted `C.ball` line named "runner-up ball".
- frame inspector: a "joint" row listing each target's use and the frame's picks (the choice made
  at that frame), and `joint_counters` in the firmware summary chips when present.

Manual check (the viewer page has no JS tests): start the `dump-viewer` preview, load
`tests/radar/recordings/iwr6843_20260824_120408_601_001.l3dump` with ball search "joint", tee bin
39, pitch 10.5; confirm the predicted markers, rejected targets and runner-up line render and the
browser console has no errors.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/dump_viewer.py scripts/iwr6843/dump_viewer.html tests/test_iwr6843_dump_viewer.py
git commit -m "iwr: dump viewer shows the joint search's predictions, rejects and runner-up" -- src/openflight/iwr6843/dump_viewer.py scripts/iwr6843/dump_viewer.html tests/test_iwr6843_dump_viewer.py
```

---

### Task 7: Evaluation — ball search choice and present/absent splits

**Files:**
- Modify: `scripts/analysis/evaluate_iwr_tracking.py`
- Test: `tests/test_evaluate_iwr_tracking.py`

**Interfaces:**
- Consumes: Task 5 `ReplayConfig.joint_search`.
- Produces: `evaluate(case, *, lib=None, ball_search: str | None = None)`; CLI
  `--ball-search {firmware,legacy,hypotheses,joint}` (replaces `--ball-hypotheses`);
  `summarize()` adds `"present": {"ok", "wrong", "none"}` and `"absent": {"ok", "wrong", "none"}`.

- [ ] **Step 1: Write the failing tests** (append; the file builds `Outcome`s directly)

```python
def outcome(ball, present):
    return ev.Outcome(name="x", club="club", ball=ball, ball_present=present,
                      launch_mps=None, ops_mps=40.0)


def test_summary_splits_by_ball_present():
    s = ev.summarize([outcome("ok", True), outcome("wrong", True), outcome("wrong", False),
                      outcome("none", False)])
    assert s["present"] == {"ok": 1, "wrong": 1, "none": 0}
    assert s["absent"] == {"ok": 0, "wrong": 1, "none": 1}


def test_ball_search_flag_maps_to_the_config(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ev, "iter_cases", lambda roots: [object()])
    monkeypatch.setattr(ev, "evaluate", lambda case, **kw: seen.append(kw) or outcome("ok", True))
    assert ev.main([str(tmp_path), "--ball-search", "joint"]) == 0
    assert seen == [{"ball_search": "joint"}]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_evaluate_iwr_tracking.py -v`
Expected: FAIL (`KeyError: 'present'`, unknown `--ball-search`).

- [ ] **Step 3: Implement**

```python
SEARCHES = {"firmware": None, "legacy": "legacy", "hypotheses": "hypotheses", "joint": "joint"}


def evaluate(case: Case, *, lib=None, ball_search: str | None = None) -> Outcome:
    config = case.config
    if ball_search == "legacy":
        config = replace(config, ball_hypotheses=False)
    elif ball_search == "hypotheses":
        config = replace(config, ball_hypotheses=True)
    elif ball_search == "joint":
        config = replace(config, joint_search=True)
    # ... the rest unchanged ...
```

`summarize` adds:

```python
    def split(present: bool) -> dict[str, int]:
        return {v: sum(1 for o in outcomes if o.ball_present == present and o.ball == v)
                for v in ("ok", "wrong", "none")}

    ...
        "present": split(True),
        "absent": split(False),
```

`main`: replace the `--ball-hypotheses` argument with
`parser.add_argument("--ball-search", choices=tuple(SEARCHES), default="firmware", help="...")`
and `outcomes = [evaluate(case, ball_search=SEARCHES[args.ball_search]) for case in ...]`.
Update the module docstring's usage line and the spec reference in
`docs/superpowers/specs/2026-09-28-iwr-joint-club-ball-tracking.md` Results (`--ball-hypotheses on/off`
→ `--ball-search hypotheses/legacy`).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_evaluate_iwr_tracking.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/analysis/evaluate_iwr_tracking.py tests/test_evaluate_iwr_tracking.py docs/superpowers/specs/2026-09-28-iwr-joint-club-ball-tracking.md
git commit -m "iwr: evaluation chooses the ball search and splits by ball present" -- scripts/analysis/evaluate_iwr_tracking.py tests/test_evaluate_iwr_tracking.py docs/superpowers/specs/2026-09-28-iwr-joint-club-ball-tracking.md
```

---

### Task 8: Recordings, strength invariance and the acceptance run

**Files:**
- Add: `tests/radar/recordings/iwr6843_20260809_112107_663_004.l3dump` (copied from
  `E:/Users/corma/Downloads/openflight_trackman_sessions_2026-07-14_to_2026-08-24/openflight_trackman_sessions/sessions/2026-08-09/openflight/iwr6843/`)
- Modify: `tests/radar/recordings/manifest.json`, `tests/test_iwr6843_firmware_replay.py`,
  `docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md` (Results section)
- Possibly modify: `firmware/iwr6843/l3_joint_search.c` (`l3_joint_cfg_defaults` values only)

**Interfaces:**
- Consumes: Tasks 5 and 7.
- Produces: manifest entry with `"joint"` expectations; test helper
  `amplify_bins(raw: bytes, frame_bins: dict[int, list[int]], gain: float) -> bytes` in
  `tests/iwr6843_recording_tools.py` (new); the Results section.

- [ ] **Step 1: Add the recording and write the failing tests**

Copy the capture (it is ~ the size of the other recordings; check `ls -l` and ledger its size).
Manifest entry (the 2026-08-09 session gives tee 1.524 m and pitch 12°; the replay's other
entries show the key names — mirror them):

```json
"iwr6843_20260809_112107_663_004.l3dump": {
  "notes": "2026-08-09 11:21:07 shot 4, OPS 112.1 mph (50.1 m/s). The ball ridge (aliased Doppler about -3.8, 50 m/s) separates from the club (+1.9, 38 m/s) at ~24 ms; before the joint search the ball's first five points were club returns.",
  "config": {"tee_range_m": 1.524, "pitch_deg": 12.0, "stop_at_fire": true, "impact_armed": true},
  "expect": {"fires": true},
  "joint": {"ball_speed_mps": [42.6, 57.6], "ball_point_speed_mps": [40.0, 60.0], "club_ridge_doppler_mps": [0.5, 3.5]}
}
```

`tests/iwr6843_recording_tools.py`:

```python
"""Edit a recorded capture's IQ for tests: never a production switch."""

from __future__ import annotations

import numpy as np

from openflight.iwr6843.dump import parse_dump, write_dump


def amplify_bins(raw: bytes, frame_bins: dict[int, list[int]], gain: float) -> bytes:
    """Scale the IQ of the listed local bins of the listed frames by `gain` (amplitude)."""
    meta, cube = parse_dump(raw)
    cube = np.array(cube, copy=True)
    for frame, bins in frame_bins.items():
        for local in bins:
            cube[frame, ..., local] = cube[frame, ..., local] * gain
    return write_dump(meta, cube)
```

If `openflight.iwr6843.dump` has no writer, write the cube back into a copy of `raw` at the same
offsets `parse_dump` read it from (add `dump.cube_offsets(meta)` returning the per-frame byte
offsets, with its own test in `tests/test_iwr6843_dump.py` that round-trips an unmodified capture
byte-for-byte). The cube axis order is what `parse_dump` returns — read it before indexing and fix
the `cube[frame, ..., local]` expression to that order.

Tests (append to `tests/test_iwr6843_firmware_replay.py`):

```python
JOINT_RECORDINGS = [
    "iwr6843_20260809_112107_663_004.l3dump",
    "iwr6843_20260824_120408_601_001.l3dump",
    "iwr6843_20260927_144341_257_013.l3dump",
]


@pytest.mark.parametrize("name", JOINT_RECORDINGS)
def test_joint_search_meets_the_manifest(lib, name):
    entry = _manifest_entry(name)
    raw = (RECORDINGS / name).read_bytes()
    result = fr.replay_dump(raw, replace(_config_from_entry(entry), joint_search=True), lib=lib)
    _check_expectations(result, entry["expect"])   # the existing checker
    joint = entry.get("joint", {})
    if "ball_speed_mps" in joint:
        low, high = joint["ball_speed_mps"]
        assert result.launch is not None and low <= result.launch.speed_mps <= high


def test_joint_ball_points_on_the_ball_ridge_only(lib):
    name = "iwr6843_20260809_112107_663_004.l3dump"
    entry = _manifest_entry(name)
    raw = (RECORDINGS / name).read_bytes()
    result = fr.replay_dump(raw, replace(_config_from_entry(entry), joint_search=True), lib=lib)
    low, high = entry["joint"]["club_ridge_doppler_mps"]
    assert result.ball_points
    assert not any(low <= p.doppler_mps <= high for p in result.ball_points)


@pytest.mark.parametrize("name", JOINT_RECORDINGS)
def test_joint_identities_survive_a_stronger_club(lib, name):
    entry = _manifest_entry(name)
    raw = (RECORDINGS / name).read_bytes()
    config = replace(_config_from_entry(entry), joint_search=True)
    base = fr.replay_dump(raw, config, lib=lib)
    club_bins = {}
    for p in base.points:
        f = next(fr_ for fr_ in base.frames if fr_.frame == p.frame)
        club_bins.setdefault(p.frame, []).append(int(round(p.range_bin)) - f.first_bin)
    louder = fr.replay_dump(amplify_bins(raw, club_bins, 2.0), config, lib=lib)  # x4 in power
    assert [round(p.range_bin, 1) for p in louder.ball_points] == [
        round(p.range_bin, 1) for p in base.ball_points
    ]
```

(Use the file's existing names for the manifest loader, config builder and expectation checker —
`_manifest_entry`, `_config_from_entry`, `_check_expectations` are placeholders for those; if the
checker does not exist, assert the three manifest keys inline.)

- [ ] **Step 2: Run to see where the defaults stand**

Run: `uv run pytest tests/test_iwr6843_firmware_replay.py -k joint -v`
Expected: some may FAIL; that is the tuning input for Step 3.

- [ ] **Step 3: Measure on the 93 captures and tune once**

Run:

```bash
uv run python scripts/analysis/evaluate_iwr_tracking.py iwr-test-sessions --ball-search legacy --json .superpowers/joint-legacy.json
uv run python scripts/analysis/evaluate_iwr_tracking.py iwr-test-sessions --ball-search joint --json .superpowers/joint-default.json
```

Expected: both print a summary; record both.

If Acceptance (Global Constraints) is not met, tune only these `l3_joint_cfg_defaults` values, one
at a time, in this order, re-running the joint evaluation after each and keeping a change only when
"ball ok" rises without "none" or "club" breaching the bar: `confirmMargin` {1.0, 2.0, 3.0},
`missCost` {1.0, 1.5, 2.5}, `termCap` {2.0, 3.0}, `rangeSigmaBins` {0.75, 1.0, 1.5},
`clubDecelMps2` {4000, 6000, 9000}. At most one pass through the list. Each kept value is a ledger
ruling with the before/after summary. The Task 2–4 unit tests must stay green after every change
(they encode the design, not the tuning; a test that breaks on a tuned value is a finding to
investigate, not to edit).

- [ ] **Step 4: Run the full suite and write the Results**

Run: `uv run pytest tests/ -q -p no:cacheprovider > .superpowers/joint-suite.txt 2>&1; tail -5 .superpowers/joint-suite.txt`
Expected: no failures beyond the 56 recorded before this plan (camera, cloud, geekworm, desktop
launcher, serial latency, sim transport, `compact_iq16`/`live_selector` compiler, `self_trigger`,
`memory_layout`); list any new one by name and fix it.

Append to the spec:

```markdown
## Results (<date>)

| search | club at impact | ball ok | wrong | none | present ok / 23 | absent wrong / 70 |
|---|---|---|---|---|---|---|
| legacy | … | … | … | … | … | … |
| joint | … | … | … | … | … | … |

Tuning kept: <values, or "defaults">. Acceptance: <met / not met, which bar>.
```

- [ ] **Step 5: Commit**

```bash
git add tests/radar/recordings/iwr6843_20260809_112107_663_004.l3dump tests/radar/recordings/manifest.json tests/iwr6843_recording_tools.py tests/test_iwr6843_firmware_replay.py docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md firmware/iwr6843/l3_joint_search.c
git commit -m "iwr: judge the joint path search on the recordings and the 93 captures" -- tests/radar/recordings/iwr6843_20260809_112107_663_004.l3dump tests/radar/recordings/manifest.json tests/iwr6843_recording_tools.py tests/test_iwr6843_firmware_replay.py docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md firmware/iwr6843/l3_joint_search.c
```

(Add `src/openflight/iwr6843/dump.py` and `tests/test_iwr6843_dump.py` if Step 1 needed the writer.)

**Gate:** if Acceptance is not met after Step 3, STOP here. Report the Results table and the
per-capture changes (legacy → joint) to the user; do not start Task 9.

---

### Task 9: Switch the board and delete the old ball tracker (only if Acceptance is met)

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c`, `firmware/iwr6843/l3_result.h/.c`, `firmware/iwr6843/makefile`
- Delete: `firmware/iwr6843/l3_ball_track.c/.h`, `firmware/iwr6843/l3_ball_hyp.c/.h`,
  `tests/test_iwr6843_firmware_ball_track.py`, `tests/test_iwr6843_firmware_ball_hyp.py`
- Modify: `src/openflight/iwr6843/firmware_host.py` (drop the ball-track/hyp mirrors, signatures,
  constants and `HOST_SOURCES` entries), `src/openflight/iwr6843/firmware_replay.py` (the joint
  path becomes the only post-impact path: remove `ball_hypotheses`, `joint_search`, the legacy
  post-frame body, `_hypothesis_angles`, `_hypothesis_summaries`, `HypothesisSummary`,
  `ReplayFrame.ball_hypotheses`), `src/openflight/iwr6843/dump_viewer.py` + `.html` (ball search
  select removed; the hypotheses drawing removed), `scripts/analysis/evaluate_iwr_tracking.py`
  (`--ball-search` removed), `tests/test_iwr6843_firmware_sparse.py` (source-text asserts),
  `tests/test_iwr6843_firmware_result.py`, `tests/test_iwr6843_firmware_replay.py`,
  `tests/test_iwr6843_dump_viewer.py`, `tests/test_evaluate_iwr_tracking.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `void l3_result_build(const l3_shot_t *shot, uint32_t ballPoints, const l3_launch_t *launch, ...)`
  (the `const l3_ball_track_t *ball` parameter becomes `uint32_t ballPoints`; the rest of the
  signature unchanged).

- [ ] **Step 1: Write the failing tests**

In `tests/test_iwr6843_firmware_sparse.py` (source-text checks of `l3_dump.c`), replace the ball
tracker asserts with:

```python
def test_board_runs_the_joint_search_in_the_replay_order():
    source = (FIRMWARE / "l3_dump.c").read_text(encoding="utf-8")
    body = source[source.index("static void l3_considerBallTrack"):]
    body = body[: body.index("\n}\n")]
    order = [body.index(call) for call in (
        "l3_joint_update(", "l3_joint_angle_requests(", "l3_joint_set_angles(",
        "l3_joint_finish_frame(", "l3_joint_launch(",
    )]
    assert order == sorted(order)
    assert "l3_track_follow(" not in body
    assert "l3_ball_track" not in source
```

In `tests/test_iwr6843_firmware_result.py`, change the `l3_result_build` calls to pass a point count
(`3` where the test built a ball track with three points) and add:

```python
def test_result_reports_the_ball_point_count(lib):
    ...  # build as the file's other tests do, with ballPoints=300
    assert result.ballPoints == 255
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_sparse.py tests/test_iwr6843_firmware_result.py -v`
Expected: FAIL.

- [ ] **Step 3: Switch `l3_dump.c`**

- Globals: replace `gBallTrackCfg`/`gBallTrack` with `static l3_joint_cfg_t gJointCfg; static l3_joint_t gJoint;`.
- Init (where `l3_ball_track_cfg_defaults` ran): `l3_joint_cfg_defaults(&gJointCfg); gJointCfg.binWidthM = cfg.binWidthM; gJointCfg.velocitySpanMps = cfg.velocitySpanMps; gJointCfg.maxAngleResidualM = cfg.maxAngleResidualM; gJointCfg.cal = gRadarCal; l3_joint_init(&gJoint, &gJointCfg);`
- Reset: `l3_joint_reset(&gJoint);`
- Arm (the `L3_SHOT_IMPACT` site): build the seed exactly as the replay's `_arm_joint` does —
  `gClubTrack.active && gClubTrack.count > 0` → newest point's `rangeBin`, `timestampUs`, and
  `l3_track_speed_mps(&gClubTrack, L3_TRACK_FOLLOW_FIT_POINTS)` — then
  `l3_joint_arm(&gJoint, (float)teeBin, in.impactTimestampUs, seedOrNull);`
- `l3_considerBallTrack`: guard on `gJoint.armed`; `params.snr = gJointCfg.snr`; replace the
  follow/ball-track/hypothesis-angle block with:

```c
    (void)l3_joint_update(&gJoint, targets, found, frameIndex, gPostTimestampUs, &gClubTrack, NULL);
    {
        l3_joint_angle_req_t requests[L3_OBS_MAX_TARGETS];
        uint32_t wanted = l3_joint_angle_requests(&gJoint, requests, L3_OBS_MAX_TARGETS);
        uint32_t r;

        for (r = 0U; r < wanted; r++) {
            const l3_target_obs_t *hit = &targets[requests[r].target];
            l3_angle_obs_t angle;
            uint8_t flags;

            /* the same snapshot + estimate the ball point used, with the
             * request's speed for the Doppler compensation */
            if (l3_estimateTargetAngles(&frame, hit, requests[r].speedMps, &angle, &flags)) {
                (void)l3_joint_set_angles(&gJoint, requests[r].target, angle.azimuthRad,
                                          angle.elevationRad, flags);
            }
        }
    }
    l3_joint_finish_frame(&gJoint, &gClubTrack, NULL);
    (void)l3_joint_launch(&gJoint, &gLaunch);
    ...
    in.ballTrackDone = gJoint.done;
```

  `l3_estimateTargetAngles` is the snapshot + `l3_angle_*` block that `l3_considerBallTrack`
  repeats today for the ball point and each hypothesis point (deferred minor #11): extract it once
  as a static function with that signature and use it here.
- Retention state: `state->ballTrackConfirmed = gJoint.confirmed;` and the ball/club bins from
  `l3_joint_now` (current best explanation): `state->ballTrackBin = now.ballState ? now.ballBin + now.ballSpeedMps * frameDtS / gJointCfg.binWidthM : state->ballBin;` and, when the joint search
  is armed, `state->clubBin` likewise from `now.clubBin`/`now.clubSpeedMps` (frameDtS is the
  frame period the retention predict already uses).
- `l3_result_build(&gShot, gJoint.ballCount, &gLaunch, ...)`.
- Status print: replace the ball-track status/points with `l3_joint_now` and the ball path via
  `l3_joint_ball_point`.

`l3_result.h/.c`: `#include "l3_launch.h"` instead of `l3_ball_track.h`; parameter
`uint32_t ballPoints`; `out->ballPoints = (uint8_t)((ballPoints > 255U) ? 255U : ballPoints);`.

Delete the four old C files and their tests; remove their mirrors/signatures/`HOST_SOURCES`
entries; `makefile` `SOURCES` −= `l3_ball_track.c l3_ball_hyp.c`. In the replay, make the joint
path unconditional and delete the legacy post-frame body and hypothesis helpers; the replay test
`test_joint_search_off_leaves_the_frames_without_joint_fields` and the ball-hypotheses replay tests
are deleted with the option; `club_last_bin`/`ball_speed_mps` manifest expectations now run through
the joint path. Remove `ball_search` from the viewer and `--ball-search` from the evaluation
(their tests go with them).

- [ ] **Step 4: Verify host and board**

Run: `uv run pytest tests/ -q -p no:cacheprovider > .superpowers/joint-switch-suite.txt 2>&1; tail -5 .superpowers/joint-switch-suite.txt`
Expected: no new failures beyond the 56.

Run: `uv run python scripts/analysis/evaluate_iwr_tracking.py iwr-test-sessions`
Expected: the same numbers as Task 8's joint row.

Run (Git Bash):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --platform linux/amd64 -v "C:/Users/corma/Documents/GitHub/openflight:/work" -w /work openflight-iwr-sdk:latest make -C firmware build-native RELEASE_DIR=/tmp/release RELEASE_NAME=check.bin
```

Expected: every file compiles with warnings as errors; the link still fails on the pre-existing
DATA_RAM overflow. Compare the map's DATA_RAM `unused` against the value at `53bbdf29`
(recorded in the old spec: 0x233 with the hypotheses): it must be the same or larger. Record both
numbers in the spec's Results.

- [ ] **Step 5: Commit**

```bash
git add -A firmware/iwr6843/l3_dump.c firmware/iwr6843/l3_result.h firmware/iwr6843/l3_result.c firmware/iwr6843/makefile firmware/iwr6843/l3_ball_track.c firmware/iwr6843/l3_ball_track.h firmware/iwr6843/l3_ball_hyp.c firmware/iwr6843/l3_ball_hyp.h src/openflight/iwr6843/firmware_host.py src/openflight/iwr6843/firmware_replay.py src/openflight/iwr6843/dump_viewer.py scripts/iwr6843/dump_viewer.html scripts/analysis/evaluate_iwr_tracking.py tests/test_iwr6843_firmware_ball_track.py tests/test_iwr6843_firmware_ball_hyp.py tests/test_iwr6843_firmware_sparse.py tests/test_iwr6843_firmware_result.py tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_dump_viewer.py tests/test_evaluate_iwr_tracking.py docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md
git commit -m "iwr: the board runs the joint path search; the old ball tracker goes" -- firmware/iwr6843/l3_dump.c firmware/iwr6843/l3_result.h firmware/iwr6843/l3_result.c firmware/iwr6843/makefile firmware/iwr6843/l3_ball_track.c firmware/iwr6843/l3_ball_track.h firmware/iwr6843/l3_ball_hyp.c firmware/iwr6843/l3_ball_hyp.h src/openflight/iwr6843/firmware_host.py src/openflight/iwr6843/firmware_replay.py src/openflight/iwr6843/dump_viewer.py scripts/iwr6843/dump_viewer.html scripts/analysis/evaluate_iwr_tracking.py tests/test_iwr6843_firmware_ball_track.py tests/test_iwr6843_firmware_ball_hyp.py tests/test_iwr6843_firmware_sparse.py tests/test_iwr6843_firmware_result.py tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_dump_viewer.py tests/test_evaluate_iwr_tracking.py docs/superpowers/specs/2026-09-28-iwr-joint-path-search-design.md
```

(`git add -A <paths>` stages the deletions of the listed files; the explicit pathspec keeps
everything else out.)
