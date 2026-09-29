# IWR6843 Club After Impact, Automatic Tee Band, Ball Colour — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track the club after impact (coasting across the tee band, taking only returns slower than the ball, re-acquiring when lost), place the tee band automatically on the noisiest contiguous bins near the tee, draw the ball in blue, and make the replay's ball-track frame count match the board's.

**Architecture:** `l3_track_follow` gains an `l3_follow_ctx_t` describing the scene after impact (band edge, ball origin, impact time, approach and ball rates, the ball's claim, frame period); `NULL` keeps today's follow. After impact the ball tracker runs first each frame so its claim and rate are known to the club. `l3_band` gains a per-bin noise map (EMA of the trigger's MTI statistic on idle pre-impact frames) and `l3_band_place`, which picks the contiguous N-bin run with the most noise within a search window around the ball; the band is frozen while a club track exists. Board (`l3_dump.c`) and replay (`firmware_replay.py`) make identical calls.

**Tech Stack:** C99 (TI R4F firmware, host-built via `firmware_host.py`), Python 3.11 ctypes, pytest, `uv`, Plotly viewer.

**Spec:** `docs/superpowers/specs/2026-09-29-iwr-club-out-auto-band-design.md` (builds on `docs/superpowers/specs/2026-09-28-iwr-impact-back-interpolation-design.md`).

## Global Constraints

- Always run Python through `uv run` (`uv run pytest …`, `uv run pylint …`, `uv run ruff …`).
- Pure-C modules include no TI headers; new `.c` files go in both `firmware/iwr6843/makefile` `SOURCES` and `firmware_host.py` `HOST_SOURCES`.
- Every ctypes mirror in `firmware_host.py` matches its C struct field for field, in order; every new or changed C function gets a correct `_SIGNATURES` entry.
- Bins are GLOBAL range-FFT bins; downrange is a rising bin; bin width `6.0 / 128` m.
- Board and replay make identical calls in identical order; the replay is the behavioural reference and `tests/test_iwr6843_firmware_board_wiring.py` pins the board by source inspection.
- `bandBins` becomes the band's total width in bins (was a half-width); 0 = off. `bandSearchBins` default 10, last field of `l3_impact_fit_cfg_t`. Noise EMA constant 1/16. No-history threshold 8 updates.
- Club after impact: coast allowed for `(hiBin − lastBin) / followBinsPerS` seconds plus one frame when the band is valid and the club is short of its far edge; a candidate must be departing, within the existing reach cap, slower than the ball track's current rate (when known) and not the ball's claimed target; re-acquisition from returns beyond the band at a rate in (0, approach × 1.1] and below the ball's rate, strongest wins.
- Board image must still fit DATA_RAM: rebuild in the `openflight-iwr-sdk` Docker image after every task that changes board C and report used/free (last known: 923 B free).
- Existing replay expectations (manifest `expect`, replay/evaluator/viewer tests) are never edited to make tests pass; a change in them stops the task and is reported with evidence.
- The full suite has 56 pre-existing failures/errors on this machine (camera, desktop launcher, geekworm, compact_iq16, live selector, memory layout, self_trigger, cloud_config, serial_latency, sim_transport). A task is green when its tests pass and `uv run pytest tests/ -q -p no:cacheprovider 2>&1 | grep -E "^(FAILED|ERROR)" | sed 's/ - .*//' | sort` does not grow.
- Lint: `uv run pylint src/openflight/ --fail-under=9`; `uv run ruff check` and `uv run ruff format --check` on touched Python.
- Commit messages end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; stage only files the task changed; never push.

## Review Focus

1. **Club track already dead at impact** (dropped while crossing the band before impact) — the user expects the club to be picked up again after impact. Pinned in Task 3 (`test_a_dead_track_reacquires_the_slower_departing_mover`) and Task 4 (recordings with band on have club points after impact).
2. **Ball speed unknown in the first post frames** (ball track has < 2 points) — the club must still be followed/re-acquired, bounded only by its approach speed. Pinned in Task 3 (`test_unknown_ball_rate_bounds_by_approach_only`).
3. **Frame window moves between frames** (phased capture: pre window then impact window) — the noise map must not mix bins from different windows. Pinned in Task 5 (`test_noise_map_restarts_when_the_window_moves`).
4. **Band wider than the search window or the noise coverage** — placement must still give a valid band (centred fallback), never an out-of-window or empty band. Pinned in Task 5 (`test_width_wider_than_the_window_falls_back_to_centred`).
5. **Band placed on the club itself** (a swing starting while the map updates) — the map only updates on frames with no club track and the band freezes on acquisition. Pinned in Task 6 (`test_band_freezes_when_the_club_is_acquired_and_thaws_when_it_drops`).

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/iwr6843/dump_viewer.html` | Ball colour blue; host-peak colour grey |
| `src/openflight/iwr6843/firmware_replay.py` | `post_frame_count(meta)`; ball-first post frames with the follow context; per-frame band placement, noise map, freeze |
| `firmware/iwr6843/l3_club_track.h/.c` | `l3_follow_ctx_t`, `l3_track_follow(..., ctx)`, coast/slower/claim/re-acquire, `l3_track_recent_rate` |
| `firmware/iwr6843/l3_band.h/.c` | `l3_band_noise_t`, `l3_band_noise_reset/update`, `l3_band_place`; `l3_band_around` removed |
| `firmware/iwr6843/l3_impact_fit.h/.c` | `bandSearchBins` (last cfg field); `bandBins` documented as width |
| `firmware/iwr6843/l3_dump.c` | Board mirror of the replay |
| `src/openflight/iwr6843/firmware_host.py` | Mirrors, signatures |
| `src/openflight/server.py`, `monitor.py`, `docs/reference/cli.md`, `docs/changelog.md` | Band flag now a width |
| `tests/iwr6843_synth.py` | Optional post-impact club and a jittering ridge for `synth_shot_dump` |
| Tests | `tests/test_iwr6843_firmware_club_follow.py` (new), `tests/test_iwr6843_firmware_band.py`, `tests/test_iwr6843_firmware_replay.py`, `tests/test_iwr6843_firmware_board_wiring.py`, `tests/test_iwr6843_dump_viewer.py`, `tests/test_iwr6843_firmware_club_track.py`, `tests/test_iwr6843_firmware_impact_fit.py`, `tests/test_server.py`, `tests/test_iwr6843_monitor.py` |

---

### Task 1: Ball in blue, host peaks in grey

**Files:**
- Modify: `scripts/iwr6843/dump_viewer.html:13-14`
- Test: `tests/test_iwr6843_dump_viewer.py`

**Interfaces:** Produces the CSS tokens `--ball: #339af0` and `--py: #868e96`; nothing else changes.

- [ ] **Step 1: Write the failing test** (append to `tests/test_iwr6843_dump_viewer.py`)

```python
def _css_tokens() -> dict[str, str]:
    html = (Path(__file__).parents[1] / "scripts" / "iwr6843" / "dump_viewer.html").read_text(
        encoding="utf-8"
    )
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", html))


def test_the_ball_is_blue_and_nothing_else_uses_that_blue():
    tokens = _css_tokens()
    assert tokens["ball"].lower() == "#339af0"
    others = {name: value.lower() for name, value in tokens.items() if name != "ball"}
    assert "#339af0" not in others.values(), others


def test_host_approach_peaks_are_grey_not_blue():
    assert _css_tokens()["py"].lower() == "#868e96"
```

(add `import re` to the file's imports if missing.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_dump_viewer.py -k "blue or grey" -v` → FAIL (`#ff922b` / `#4dabf7`).

- [ ] **Step 3: Implement** — in `dump_viewer.html` line 13 change `--py: #4dabf7;` to `--py: #868e96;` and line 14 `--ball: #ff922b;` to `--ball: #339af0;`. Check the file for any other `:root` / theme block that redefines `--ball` or `--py` (`grep -n "\-\-ball\|\-\-py" scripts/iwr6843/dump_viewer.html`) and change those the same way.

- [ ] **Step 4: Run to verify pass** — same command → 2 passed; `uv run pytest tests/test_iwr6843_dump_viewer.py -q -p no:cacheprovider` all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/iwr6843/dump_viewer.html tests/test_iwr6843_dump_viewer.py
git commit -m "iwr: viewer draws the ball in blue and the host peaks in grey"
```

---

### Task 2: Replay ball-track frame count matches the board

**Files:**
- Modify: `src/openflight/iwr6843/firmware_replay.py` (`frame_window` area for the helper; `replay_dump` line ~875; `ReplayResult`)
- Test: `tests/test_iwr6843_firmware_replay.py`

**Interfaces:**
- Produces: `post_frame_count(meta: dict) -> int | None`; `ReplayResult.ball_track_frames: int`, `ReplayResult.ball_track_frames_known: bool`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_post_frames_come_from_the_retention_report():
    meta = {"n_frames": 47, "retention": {"reason": "complete", "pre_frames": 24, "planned_frames": 47}}
    assert fr.post_frame_count(meta) == 23


def test_post_frames_come_from_the_first_window_change():
    meta = {"n_frames": 24, "range_bin_starts": (20,) * 9 + (32,) * 7 + (47,) * 8}
    assert fr.post_frame_count(meta) == 15


def test_post_frames_are_unknown_without_windows_or_a_report():
    assert fr.post_frame_count({"n_frames": 18}) is None
    assert fr.post_frame_count({"n_frames": 24, "range_bin_starts": (20,) * 24}) is None


def test_replay_uses_the_post_frame_count_and_says_when_it_could_not(lib):
    from iwr6843_synth import synth_shot_dump

    result = fr.replay_dump(synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372),
                            fr.ReplayConfig(tee_bin=29, dest_bin=29, impact_armed=True), lib=lib)
    assert result.ball_track_frames_known is False
    assert result.ball_track_frames == result.shot.cfg.ballTrackFrames == 18
```

and one test on a phased dump (same builder shape as `_variable_dump` in `tests/test_iwr6843_dump_viewer.py`):

```python
def phased_dump(pre: int, post: int) -> bytes:
    """A timed variable-width IQ16 dump: `pre` frames at window 20, `post` at 32."""
    from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, pack_dump

    n_tx, loops, n_rx, width = 2, 4, 4, 16
    frames = pre + post
    cube = np.zeros((frames, loops * n_tx, n_rx, width), dtype=complex)
    cube[..., 9] += 3000.0
    return pack_dump(
        cube,
        n_tx=n_tx,
        version=6,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=(20,) * pre + (32,) * post,
        range_bin_counts=(width,) * frames,
        frame_time_offsets_us=[3000 * f for f in range(frames)],
    )


def test_replay_of_a_phased_dump_counts_only_its_post_frames(lib):
    raw = phased_dump(pre=3, post=2)
    result = fr.replay_dump(raw, fr.ReplayConfig(tee_bin=29), lib=lib)
    assert result.ball_track_frames_known is True
    assert result.ball_track_frames == 2
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_firmware_replay.py -k "post_frame" -v` → FAIL (`post_frame_count` missing).

- [ ] **Step 3: Implement**

```python
def post_frame_count(meta: dict) -> int | None:
    """Post-impact frames the board's capture plan held (impact + ball frames).

    From the adaptive retention report when present; else from the first
    frame whose window start differs from the pre-impact window's; None when
    the dump cannot tell (one window throughout, no report).
    """
    n_frames = int(meta["n_frames"])
    retention = meta.get("retention")
    if retention:
        return n_frames - int(retention["pre_frames"])
    starts = meta.get("range_bin_starts")
    if starts:
        for index, start in enumerate(starts):
            if start != starts[0]:
                return n_frames - index
    return None
```

In `replay_dump` replace `shot_cfg.ballTrackFrames = int(meta["n_frames"])` with

```python
    known_post = post_frame_count(meta)
    shot_cfg.ballTrackFrames = known_post if known_post is not None else int(meta["n_frames"])
```

and pass `ball_track_frames=int(shot_cfg.ballTrackFrames), ball_track_frames_known=known_post is not None` to `ReplayResult` (new fields with defaults `ball_track_frames: int = 0`, `ball_track_frames_known: bool = False`). Add `post_frame_count` to `__all__`.

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_iwr6843_firmware_replay.py tests/test_evaluate_iwr_tracking.py tests/test_iwr6843_dump_viewer.py -q -p no:cacheprovider`. If an existing expectation changes, stop and report it (Global Constraints).

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/firmware_replay.py tests/test_iwr6843_firmware_replay.py
git commit -m "iwr: replay forces SOLVE after the capture's post frames, as the board does"
```

---

### Task 3: `l3_track_follow` with the scene after impact

**Files:**
- Modify: `firmware/iwr6843/l3_club_track.h`, `firmware/iwr6843/l3_club_track.c`, `src/openflight/iwr6843/firmware_host.py`, callers that pass the old 5 arguments (`firmware/iwr6843/l3_dump.c:3655`, `src/openflight/iwr6843/firmware_replay.py:1512`, `tests/test_iwr6843_firmware_club_track.py:755,887`) — they pass `NULL`/`None` in this task
- Test: `tests/test_iwr6843_firmware_club_follow.py` (new)

**Interfaces:**
- Produces (C):

```c
/* The scene after impact, as l3_track_follow needs it. NULL: plain follow. */
typedef struct {
    uint8_t  bandValid;
    float    bandHiBin;          /* the tee band's far edge (global bin) */
    float    originBin;          /* the ball's bin at rest */
    uint32_t impactTimestampUs;
    float    approachBinsPerS;   /* the club's range rate arriving, > 0 when known */
    float    ballBinsPerS;       /* the ball track's current rate, 0 when unknown */
    uint32_t ballClaimIndex;     /* this frame's ball target, L3_TRACK_NO_TARGET for none */
    uint32_t frameUs;            /* nominal frame period */
} l3_follow_ctx_t;

int32_t l3_track_follow(l3_club_track_t *track, const l3_target_obs_t *targets, uint32_t n,
                        uint32_t frame, uint32_t timestampUs, const l3_follow_ctx_t *ctx);
/* Range rate (bins/s) over the newest points: the fit of up to four when three
 * or more are held, the two newest otherwise, 0 below two. */
float l3_track_recent_rate(const l3_club_track_t *track);
#define L3_TRACK_FOLLOW_MAX_RATIO 1.10F  /* after impact the club is no faster than it arrived */
```

- Produces (Python): `fw.FollowCtx` mirror; signatures for both functions.

- [ ] **Step 1: Write the failing tests** — `tests/test_iwr6843_firmware_club_follow.py`:

```python
"""The club after impact: l3_track_follow with l3_follow_ctx_t.

Scene: 3 ms frames, club approaching at 640 bins/s (30 m/s) from bin 20,
tee band 30..40, ball at rest at bin 35, impact at 18 000 us.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

FRAME_US = 3000
APPROACH = 640.0  # bins/s
NO = fw.TRACK_NO_TARGET


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def target(frame, bin_, *, stat=100.0, doppler=5.0):
    t = fw.TargetObs()
    t.frame, t.timestampUs = frame, frame * FRAME_US
    t.peakBin, t.rangeBin = int(round(bin_)), bin_
    t.energy, t.peak, t.stat, t.snr = stat * 4, stat, stat, 20.0
    t.coherence, t.dopplerAliasMps, t.confidence = 0.9, doppler, 0.9
    return t


def approached_track(lib, frames=6):
    """A club track built from frames 0..frames-1 at 640 bins/s from bin 20."""
    cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(cfg))
    for f in range(frames):
        arr = (fw.TargetObs * 1)(target(f, 20.0 + APPROACH * f * FRAME_US * 1e-6))
        lib.l3_track_update(ctypes.byref(track), arr, 1, f, f * FRAME_US)
    assert track.active == 1
    return track


def ctx(*, band=True, ball=1500.0, claim=NO, approach=APPROACH):
    c = fw.FollowCtx()
    c.bandValid, c.bandHiBin, c.originBin = 1 if band else 0, 40.0, 35.0
    c.impactTimestampUs, c.approachBinsPerS, c.ballBinsPerS = 18_000, approach, ball
    c.ballClaimIndex, c.frameUs = claim, FRAME_US
    return c


def follow(lib, track, frame, targets, c):
    arr = (fw.TargetObs * max(1, len(targets)))(*targets)
    return lib.l3_track_follow(
        ctypes.byref(track), arr, len(targets), frame, frame * FRAME_US,
        ctypes.byref(c) if c is not None else None,
    )


def test_coasts_across_the_band_instead_of_dropping(lib):
    track = approached_track(lib)  # last point frame 5, bin 29.6
    c = ctx()
    for f in range(6, 11):  # the band hides the club
        assert follow(lib, track, f, [], c) == 0
        assert track.active == 1, f
    assert follow(lib, track, 11, [target(11, 41.0)], c) == 1
    assert track.lastBin == pytest.approx(41.0)


def test_without_context_misses_still_drop(lib):
    track = approached_track(lib)
    for f in range(6, 10):
        follow(lib, track, f, [], None)
    assert track.active == 0


def test_band_off_keeps_the_miss_rule(lib):
    track = approached_track(lib)
    c = ctx(band=False)
    for f in range(6, 10):
        follow(lib, track, f, [], c)
    assert track.active == 0


def test_coast_ends_after_the_crossing_time_plus_a_frame(lib):
    track = approached_track(lib)  # (40 - 29.6) / 640 = 16.25 ms, + 3 ms
    c = ctx()
    for f in range(6, 14):  # t = 39 ms > 15 + 19.25 ms
        follow(lib, track, f, [], c)
    assert track.active == 0


def test_a_return_faster_than_the_ball_is_not_the_club(lib):
    track = approached_track(lib)
    c = ctx(ball=500.0)  # 41.0 from 29.6 over 18 ms is 633 bins/s
    for f in range(6, 11):
        follow(lib, track, f, [], c)
    assert follow(lib, track, 11, [target(11, 41.0)], c) == 0


def test_the_balls_claimed_target_is_not_the_club(lib):
    track = approached_track(lib)
    for f in range(6, 11):
        follow(lib, track, f, [], ctx())
    targets = [target(11, 41.0, stat=500.0), target(11, 40.6, stat=50.0)]
    assert follow(lib, track, 11, targets, ctx(claim=0)) == 1
    assert track.lastTargetIndex == 1
    assert track.lastBin == pytest.approx(40.6)


def test_unknown_ball_rate_bounds_by_approach_only(lib):
    track = approached_track(lib)
    c = ctx(ball=0.0)
    for f in range(6, 11):
        follow(lib, track, f, [], c)
    assert follow(lib, track, 11, [target(11, 41.0)], c) == 1


def fresh_track(lib):
    cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(cfg))
    return track


def test_a_dead_track_reacquires_the_slower_departing_mover(lib):
    track = fresh_track(lib)
    # t = 33 ms, 15 ms after impact: 41.0 is 400 bins/s from 35 (club),
    # 55.0 is 1333 bins/s (faster than the club arrived: not the club).
    assert follow(lib, track, 11, [target(11, 55.0, stat=500.0), target(11, 41.0)], ctx()) == 1
    assert track.active == 1 and track.following == 1
    assert track.lastBin == pytest.approx(41.0)
    assert track.lastTargetIndex == 1
    assert track.followBinsPerS == pytest.approx(APPROACH)


@pytest.mark.parametrize(
    "bin_, kwargs",
    [
        (39.0, {}),                      # inside the band
        (41.0, {"ball": 300.0}),         # 400 bins/s: faster than the ball
        (41.0, {"claim": 0}),            # the ball's target
        (41.0, {"approach": 0.0}),       # no approach speed: re-acquisition off
    ],
)
def test_reacquisition_refuses(lib, bin_, kwargs):
    track = fresh_track(lib)
    assert follow(lib, track, 11, [target(11, bin_)], ctx(**kwargs)) == 0
    assert track.active == 0


def test_reacquisition_needs_a_context(lib):
    track = fresh_track(lib)
    assert follow(lib, track, 11, [target(11, 41.0)], None) == 0


def test_recent_rate(lib):
    track = fresh_track(lib)
    assert lib.l3_track_recent_rate(ctypes.byref(track)) == 0.0
    track = approached_track(lib, frames=2)
    assert lib.l3_track_recent_rate(ctypes.byref(track)) == pytest.approx(APPROACH, rel=1e-3)
    track = approached_track(lib, frames=6)
    assert lib.l3_track_recent_rate(ctypes.byref(track)) == pytest.approx(APPROACH, rel=1e-3)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_firmware_club_follow.py -v` → FAIL (`FollowCtx` missing).

- [ ] **Step 3: Implement**

`l3_club_track.h`: add the struct, `L3_TRACK_FOLLOW_MAX_RATIO` and `l3_track_recent_rate` above; change the `l3_track_follow` prototype and its comment to describe the context (coast across the band for the crossing time plus a frame; only departing returns slower than the ball and not its claim; re-acquire when inactive).

`l3_club_track.c`:
1. `l3_track_associate` gains `const l3_follow_ctx_t *ctx` (pre-impact callers pass `NULL`). In the `following` branch, before the strongest-stat choice, add:

```c
            if (ctx != NULL) {
                if (i == ctx->ballClaimIndex) {
                    continue;
                }
                if (ctx->ballBinsPerS > 0.0F && dtS > 0.0F &&
                    (targets[i].rangeBin - track->lastBin) / dtS >= ctx->ballBinsPerS) {
                    continue;
                }
            }
```

and at `if (best == NULL) {` insert before the miss handling:

```c
        if (following && l3_track_coasting_across_band(track, ctx, timestampUs, last)) {
            track->lastTargetIndex = L3_TRACK_NO_TARGET;
            track->misses++;
            l3_track_note(track, L3_TRACK_WHY_COASTED);
            return 0;
        }
```

with

```c
/* After impact the tee band hides the club for as long as it takes to cross
 * it at its arriving speed; it is coasted, not dropped, until then plus a
 * frame. */
static int32_t l3_track_coasting_across_band(const l3_club_track_t *track,
                                             const l3_follow_ctx_t *ctx, uint32_t timestampUs,
                                             const l3_track_point_t *last)
{
    float crossUs;

    if (ctx == NULL || !ctx->bandValid || track->followBinsPerS <= 0.0F ||
        track->lastBin >= ctx->bandHiBin) {
        return 0;
    }
    crossUs = (ctx->bandHiBin - track->lastBin) / track->followBinsPerS * 1.0e6F +
              (float)ctx->frameUs;
    return ((float)(int32_t)(timestampUs - last->timestampUs) <= crossUs) ? 1 : 0;
}
```

2. Re-acquisition:

```c
/* After impact with no track: the club is a departing return beyond the band
 * (or the ball) whose rate from the ball at the impact time is positive, no
 * faster than it arrived and slower than the ball, and not the ball's target;
 * the strongest such return. */
static int32_t l3_track_reacquire(l3_club_track_t *track, const l3_target_obs_t *targets,
                                  uint32_t n, uint32_t frame, uint32_t timestampUs,
                                  const l3_follow_ctx_t *ctx)
{
    float dtS = (float)(int32_t)(timestampUs - ctx->impactTimestampUs) * 1.0e-6F;
    float edge = ctx->bandValid ? ctx->bandHiBin : ctx->originBin;
    float maxRate = ctx->approachBinsPerS * L3_TRACK_FOLLOW_MAX_RATIO;
    const l3_target_obs_t *best = NULL;
    uint32_t bestIndex = L3_TRACK_NO_TARGET;
    uint32_t i;

    if (dtS <= 0.0F || ctx->approachBinsPerS <= 0.0F) {
        return 0;
    }
    for (i = 0U; i < n; i++) {
        float rate = (targets[i].rangeBin - ctx->originBin) / dtS;

        if (i == ctx->ballClaimIndex || targets[i].rangeBin <= edge || rate <= 0.0F ||
            rate > maxRate || (ctx->ballBinsPerS > 0.0F && rate >= ctx->ballBinsPerS)) {
            continue;
        }
        if (best == NULL || targets[i].stat > best->stat) {
            best = &targets[i];
            bestIndex = i;
        }
    }
    if (best == NULL) {
        return 0;
    }
    track->active = 1U;
    track->misses = 0U;
    track->following = 1U;
    track->followBinsPerS = ctx->approachBinsPerS;
    track->velocityBinsPerFrame = 0.0F;
    track->lastFrame = frame;
    track->lastBin = best->rangeBin;
    track->lastTargetIndex = bestIndex;
    l3_track_append(track, best, 0.0F, 0.0F);
    l3_track_countBin(track, best->rangeBin, 1);
    l3_track_note(track, L3_TRACK_WHY_ACQUIRED);
    return 1;
}
```

3. `l3_track_follow(track, targets, n, frame, timestampUs, ctx)`: `lastTargetIndex = NO_TARGET`; if `!track->active` return `ctx != NULL ? l3_track_reacquire(...) : 0`; the existing first-follow fit block stays, and when its fitted `followBinsPerS` is 0 and `ctx != NULL && ctx->approachBinsPerS > 0` it takes `ctx->approachBinsPerS`; then `return l3_track_associate(track, targets, n, frame, timestampUs, 1, ctx);`. `l3_track_update` passes `NULL`.

4. `l3_track_recent_rate`:

```c
float l3_track_recent_rate(const l3_club_track_t *track)
{
    float slope = 0.0F;
    float residual = 0.0F;
    l3_track_point_t newest;
    l3_track_point_t previous;
    float dtS;

    if (l3_track_fit(track, 4U, &slope, &residual) != 0U) {
        return slope;
    }
    if (track->count < 2U || !l3_track_point(track, track->count - 1U, &newest) ||
        !l3_track_point(track, track->count - 2U, &previous)) {
        return 0.0F;
    }
    dtS = (float)(int32_t)(newest.timestampUs - previous.timestampUs) * 1.0e-6F;
    return (dtS > 0.0F) ? (newest.rangeBin - previous.rangeBin) / dtS : 0.0F;
}
```

`firmware_host.py`: `FollowCtx` mirror after `ClubTrack` (fields in the C order above, `ctypes.c_uint8` then floats/uint32 as declared); signatures `"l3_track_follow": ([_P(ClubTrack), _P(TargetObs), _U32, _U32, _U32, _P(FollowCtx)], ctypes.c_int32)` and `"l3_track_recent_rate": ([_P(ClubTrack)], _F32)`. Update the three existing callers to pass `NULL` / `None` (behaviour unchanged in this task).

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_iwr6843_firmware_club_follow.py tests/test_iwr6843_firmware_club_track.py tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_firmware_board_wiring.py -q -p no:cacheprovider` → all pass (update the board-wiring string that matches the old 5-argument `l3_track_follow(&gClubTrack, targets, found,` only if it no longer matches, keeping it meaningful). Rebuild the board in Docker; report DATA_RAM.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_club_track.h firmware/iwr6843/l3_club_track.c firmware/iwr6843/l3_dump.c src/openflight/iwr6843/firmware_host.py src/openflight/iwr6843/firmware_replay.py tests/test_iwr6843_firmware_club_follow.py tests/test_iwr6843_firmware_club_track.py tests/test_iwr6843_firmware_board_wiring.py
git commit -m "iwr: follow the club after impact across the band, slower than the ball, re-acquired when lost"
```

---

### Task 4: Wire the club after impact into replay and board; synthetic club-out

**Files:**
- Modify: `src/openflight/iwr6843/firmware_replay.py` (`_replay_post_frame`), `firmware/iwr6843/l3_dump.c` (`l3_considerBallTrack`), `tests/iwr6843_synth.py`
- Test: `tests/test_iwr6843_firmware_replay.py`, `tests/test_iwr6843_firmware_board_wiring.py`

**Interfaces:**
- Consumes: Task 3's `l3_follow_ctx_t`, `l3_track_follow(..., ctx)`, `l3_track_recent_rate`.
- Produces: `synth_shot_dump(..., club_out_speed_ms: float | None = None, club_amp: float | None = None)`; replay helper `_follow_ctx(shot, ball_track, band, destination, bin_width_m, frame_us) -> fw.FollowCtx`.

- [ ] **Step 1: Write the failing tests**

`tests/iwr6843_synth.py`: add `club_out_speed_ms=None` and `club_amp=None` to `synth_shot_dump` and document them. Restructure the per-(frame, loop) body so each scatterer is added by one helper instead of the single `velocity = club_v if s < 0 else ball_v` object:

```python
    def scatterers(s):
        """(velocity, amplitude) of every object at time s from impact."""
        if s < 0:
            return [(club_v, amp)]
        out = [(ball_v, amp)]
        if club_out_speed_ms is not None:
            scale = club_out_speed_ms / club_speed_ms
            out.append((tuple(scale * c for c in club_v), club_amp or amp))
        return out

    for frame in range(n_frames):
        for loop in range(loops):
            t = frame * FRAME_PERIOD_S + loop * TX2_LOOP_PERIOD_S
            s = t - t_impact
            for velocity, amplitude in scatterers(s):
                ...  # the existing position/angle/phase code for one object,
                # with `amp` replaced by `amplitude` and the final
                # `cube[frame, loop * n_tx + tx, :, bin_at] = value`
                # changed to `+=` so two objects in one bin add
```

(move the existing per-object code, unchanged apart from those two edits, into the inner loop; with `club_out_speed_ms=None` every existing caller gets the same cube as before.)

Append to `tests/test_iwr6843_firmware_replay.py`:

```python
def _club_after_impact(result):
    impact = result.shot.impactFrame
    return [p for p in result.points if p.frame > impact]


@pytest.mark.parametrize("band_bins", [5.0, 10.0])
def test_with_the_band_on_every_recording_has_the_club_after_impact(lib, band_bins):
    for path, config in fr.recording_configs():
        result = fr.replay_file(path, replace(config, band_bins=band_bins), lib=lib)
        if fw.SHOT_STATE_NAMES[result.shot.state] in fr.PRE_IMPACT_SHOT_STATES:
            continue
        club = _club_after_impact(result)
        assert len(club) >= 3, f"{path.name}: {len(club)} club points after impact"
        ball_keys = {(p.frame, round(p.range_bin, 3)) for p in result.ball_points}
        assert not ball_keys & {(p.frame, round(p.range_bin, 3)) for p in club}, path.name


def test_synthetic_club_after_impact_is_slower_than_the_ball_and_gives_club_out(lib):
    from iwr6843_synth import synth_shot_dump

    raw = synth_shot_dump(ball_speed_ms=60.0, club_out_speed_ms=20.0, tee_range_m=1.372)
    result = fr.replay_dump(
        raw, fr.ReplayConfig(tee_bin=29, dest_bin=29, impact_armed=True, band_bins=5.0), lib=lib
    )
    club = _club_after_impact(result)
    assert len(club) >= 3
    assert result.impact_fit is not None
    assert result.impact_fit.tracks["club_out"].why == "ok"
    assert result.impact_fit.tracks["club_out"].speed_mps < result.impact_fit.tracks["ball_out"].speed_mps
```

(the recordings test replaces the Task 5 note that club-out was always missing with the band on; keep the band-off expectations untouched.)

Append to `tests/test_iwr6843_firmware_board_wiring.py`:

```python
def test_post_impact_ball_runs_before_the_club_which_gets_the_scene():
    ball_track = body("l3_considerBallTrack")
    ball = ball_track.index("l3_ball_track_update_joint(&gBallTrack")
    club = ball_track.index("l3_track_follow(&gClubTrack")
    assert ball < club
    assert "L3_TRACK_NO_TARGET" in ball_track[ball:club]
    assert "&follow)" in ball_track[club:club + 200]
    assert "l3_track_recent_rate(&gBallTrack.core)" in ball_track
    assert "gBallTrack.lastTargetIndex" in ball_track
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_firmware_replay.py -k "club_after_impact" tests/test_iwr6843_firmware_board_wiring.py -v` → FAIL.

- [ ] **Step 3: Implement**

Replay, in `_replay_post_frame` (it needs `shot`, `band`, `destination`, `bin_width_m`, `frame_us` — add the missing parameters and pass them from both call sites):

```python
    # The ball first: its claim and rate tell the club what it is not.
    appended = lib.l3_ball_track_update_joint(
        ctypes.byref(ball_track), targets, found, frame, timestamp_us, fw.TRACK_NO_TARGET
    )
    ball_claim = ball_track.lastTargetIndex if appended else fw.TRACK_NO_TARGET
    follow = _follow_ctx(lib, shot, ball_track, band, destination, bin_width_m, frame_us, ball_claim)
    track_bin = None
    if lib.l3_track_follow(ctypes.byref(track), targets, found, frame, timestamp_us, ctypes.byref(follow)):
        ...  # unchanged point bookkeeping
    ...  # the existing ball-angle block now follows, using `appended` as before
```

with

```python
def _follow_ctx(  # pylint: disable=too-many-arguments
    lib, shot, ball_track, band, destination, bin_width_m, frame_us, ball_claim
) -> fw.FollowCtx:
    """The scene after impact for l3_track_follow, as l3_considerBallTrack builds it."""
    ctx = fw.FollowCtx()
    ctx.bandValid = band.valid
    ctx.bandHiBin = band.hiBin
    ctx.originBin = float(destination)
    ctx.impactTimestampUs = shot.impactTimestampUs
    delivery = shot.delivery
    ctx.approachBinsPerS = delivery.radialSpeedMps / bin_width_m if delivery.speedValid else 0.0
    ball_rate = float(lib.l3_track_recent_rate(ctypes.byref(ball_track.core)))
    ctx.ballBinsPerS = ball_rate if ball_rate > 0.0 else 0.0  # a receding "ball" is no rate
    ctx.ballClaimIndex = ball_claim
    ctx.frameUs = frame_us
    return ctx
```

(call it as `_follow_ctx(lib, shot, ball_track, band, destination, bin_width_m, frame_us, ball_claim)` in the block above.) `frame_us` is `int(meta.get("frame_period_us") or FALLBACK_FRAME_PERIOD_US)`.

Board, in `l3_considerBallTrack`, replace the follow + ball block's order:

```c
    /* The ball first: its claim and its rate tell the club what it is not. */
    ballAppended = l3_ball_track_update_joint(&gBallTrack, targets, found, frameIndex,
                                              gPostTimestampUs, L3_TRACK_NO_TARGET);
    {
        l3_follow_ctx_t follow;
        float ballRate = l3_track_recent_rate(&gBallTrack.core);

        follow.bandValid = gBand.valid;
        follow.bandHiBin = gBand.hiBin;
        follow.originBin = (float)gClubTrackDest;
        follow.impactTimestampUs = gShot.impactTimestampUs;
        follow.approachBinsPerS = gShot.delivery.speedValid
                                      ? gShot.delivery.radialSpeedMps / gClubTrack.cfg.binWidthM
                                      : 0.0F;
        follow.ballBinsPerS = (ballRate > 0.0F) ? ballRate : 0.0F;
        follow.ballClaimIndex = ballAppended ? gBallTrack.lastTargetIndex : L3_TRACK_NO_TARGET;
        follow.frameUs = gFramePeriodUs;
        (void)l3_track_follow(&gClubTrack, targets, found, frameIndex, gPostTimestampUs, &follow);
    }
    if (ballAppended && gBallTrack.lastTargetIndex < found && gBallTrack.core.count > 1U &&
        l3_track_point(&gBallTrack.core, gBallTrack.core.count - 1U, &newest)) {
        ...  /* the existing ball-angle block, unchanged */
```

(declare `int32_t ballAppended;`; update the comment above it: the ball no longer receives the club's claim, the club receives the ball's.)

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_firmware_board_wiring.py tests/test_evaluate_iwr_tracking.py tests/test_iwr6843_dump_viewer.py tests/test_iwr6843_firmware_club_follow.py -q -p no:cacheprovider`. The order swap may change existing ball expectations on recordings: if any pre-existing test fails, stop, do not edit expectations, and report each change with `fr.format_report(result)`. Rebuild the board in Docker; report DATA_RAM.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/firmware_replay.py firmware/iwr6843/l3_dump.c tests/iwr6843_synth.py tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_firmware_board_wiring.py
git commit -m "iwr: after impact the ball goes first and the club follows the scene it leaves"
```

---

### Task 5: Noise map and band placement in `l3_band`

**Files:**
- Modify: `firmware/iwr6843/l3_band.h`, `firmware/iwr6843/l3_band.c`, `firmware/iwr6843/l3_impact_fit.h/.c` (cfg), `src/openflight/iwr6843/firmware_host.py`
- Test: `tests/test_iwr6843_firmware_band.py`, `tests/test_iwr6843_firmware_impact_fit.py`

**Interfaces:**
- Produces (C):

```c
#define L3_BAND_NOISE_BINS        64U
#define L3_BAND_NOISE_SHIFT       4U    /* EMA constant 1/16 */
#define L3_BAND_NOISE_MIN_UPDATES 8U

typedef struct {
    uint32_t firstBin;                 /* global bin of avg[0] */
    uint32_t count;                    /* bins covered, 0 before the first update */
    uint32_t updates;
    float    avg[L3_BAND_NOISE_BINS];
} l3_band_noise_t;

void l3_band_noise_reset(l3_band_noise_t *noise);
/* One idle frame's observations over global bins [firstBin, firstBin + count):
 * EMA of l3_obs_stat(stat, ...); a different window restarts the map. */
void l3_band_noise_update(l3_band_noise_t *noise, uint32_t stat, uint32_t firstBin,
                          const l3_bin_obs_t *obs, uint32_t count);
/* The contiguous run of round(widthBins) bins with the largest summed noise,
 * inside [centre - searchBins, centre + searchBins] and the map; ties nearest
 * the centre. Centred on round(centreBin) without enough history or room.
 * widthBins < 0.5 gives an invalid band. */
void l3_band_place(const l3_band_noise_t *noise, float centreBin, float searchBins,
                   float widthBins, l3_band_t *out);
```

- `l3_impact_fit_cfg_t` gains `float bandSearchBins;` as its last field (default 10.0F); `bandBins` comment: "the tee band's total width in bins (0 = off)".
- `l3_band_around` is removed (no caller after Task 6); its tests and signature go with it in Task 6.
- Python: `fw.BandNoise` mirror; `fw.BAND_NOISE_BINS = 64`, `fw.BAND_NOISE_MIN_UPDATES = 8`; `ImpactFitCfg` gains `bandSearchBins` last.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_iwr6843_firmware_band.py`)

```python
def obs_row(values, stat="peak"):
    """l3_bin_obs_t per bin carrying `values` as the peak statistic."""
    arr = (fw.BinObs * len(values))()
    for i, v in enumerate(values):
        arr[i].peak = v
        arr[i].energy = v
    return arr


STAT = fw.STAT_NAMES["peak"]


def noisy_map(lib, first_bin, values, updates=8):
    noise = fw.BandNoise()
    lib.l3_band_noise_reset(ctypes.byref(noise))
    row = obs_row(values)
    for _ in range(updates):
        lib.l3_band_noise_update(ctypes.byref(noise), STAT, first_bin, row, len(values))
    return noise


def place(lib, noise, centre, width, search=10.0):
    out = fw.Band()
    lib.l3_band_place(ctypes.byref(noise), centre, search, width, ctypes.byref(out))
    return out


def test_noise_map_is_an_ema_of_the_statistic(lib):
    noise = fw.BandNoise()
    lib.l3_band_noise_reset(ctypes.byref(noise))
    lib.l3_band_noise_update(ctypes.byref(noise), STAT, 20, obs_row([16.0, 0.0]), 2)
    assert (noise.firstBin, noise.count, noise.updates) == (20, 2, 1)
    assert noise.avg[0] == pytest.approx(16.0)  # the first frame seeds the map
    lib.l3_band_noise_update(ctypes.byref(noise), STAT, 20, obs_row([0.0, 16.0]), 2)
    assert noise.avg[0] == pytest.approx(15.0) and noise.avg[1] == pytest.approx(1.0)


def test_noise_map_restarts_when_the_window_moves(lib):
    noise = noisy_map(lib, 20, [5.0] * 10)
    lib.l3_band_noise_update(ctypes.byref(noise), STAT, 32, obs_row([1.0] * 10), 10)
    assert (noise.firstBin, noise.updates) == (32, 1)
    assert noise.avg[0] == pytest.approx(1.0)


def test_placement_takes_the_noisiest_contiguous_run(lib):
    values = [1.0] * 53
    for b in range(24, 29):  # global bins 44..48 (first bin 20)
        values[b] = 50.0
    band = place(lib, noisy_map(lib, 20, values), centre=47.0, width=5.0)
    assert (band.valid, band.loBin, band.hiBin) == (1, 44.0, 48.0)


def test_placement_stays_in_the_search_window(lib):
    values = [1.0] * 53
    values[50] = 1000.0  # global 70: outside 47 +/- 10
    band = place(lib, noisy_map(lib, 20, values), centre=47.0, width=5.0)
    assert 37.0 <= band.loBin and band.hiBin <= 57.0


def test_ties_go_to_the_run_nearest_the_centre(lib):
    band = place(lib, noisy_map(lib, 20, [3.0] * 53), centre=47.0, width=5.0)
    assert (band.loBin, band.hiBin) == (45.0, 49.0)


def test_without_history_the_band_is_centred(lib):
    band = place(lib, noisy_map(lib, 20, [3.0] * 53, updates=7), centre=47.0, width=5.0)
    assert (band.loBin, band.hiBin) == (45.0, 49.0)


def test_width_wider_than_the_window_falls_back_to_centred(lib):
    band = place(lib, noisy_map(lib, 40, [3.0] * 6), centre=42.0, width=12.0, search=3.0)
    assert band.valid == 1
    assert band.hiBin - band.loBin == 11.0


def test_even_width_centred_is_deterministic(lib):
    # centred: lo = round(centre) - (width - 1) // 2 = 47 - 1
    band = place(lib, noisy_map(lib, 20, [3.0] * 53, updates=0), centre=47.0, width=4.0)
    assert (band.loBin, band.hiBin) == (46.0, 49.0)


def test_zero_width_is_no_band(lib):
    assert place(lib, noisy_map(lib, 20, [3.0] * 53), centre=47.0, width=0.0).valid == 0
```

and in `tests/test_iwr6843_firmware_impact_fit.py` extend `test_defaults_are_the_specs` with `assert c.bandSearchBins == 10.0`.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_impact_fit.py -k "noise or placement or ties or history or width or defaults" -v` → FAIL.

- [ ] **Step 3: Implement** (`l3_band.c`, append; `#include "l3_observation.h"` is already in the header)

```c
void l3_band_noise_reset(l3_band_noise_t *noise)
{
    memset(noise, 0, sizeof(*noise));
}

void l3_band_noise_update(l3_band_noise_t *noise, uint32_t stat, uint32_t firstBin,
                          const l3_bin_obs_t *obs, uint32_t count)
{
    uint32_t i;

    if (count > L3_BAND_NOISE_BINS) {
        count = L3_BAND_NOISE_BINS;
    }
    if (count == 0U) {
        return;
    }
    if (noise->updates == 0U || noise->firstBin != firstBin || noise->count != count) {
        noise->firstBin = firstBin;
        noise->count = count;
        noise->updates = 0U;
        for (i = 0U; i < count; i++) {
            noise->avg[i] = l3_obs_stat(stat, &obs[i]);
        }
    } else {
        for (i = 0U; i < count; i++) {
            float value = l3_obs_stat(stat, &obs[i]);

            noise->avg[i] += (value - noise->avg[i]) / (float)(1U << L3_BAND_NOISE_SHIFT);
        }
    }
    noise->updates++;
}

static void l3_band_span(int32_t lo, uint32_t width, l3_band_t *out)
{
    out->valid = 1U;
    out->loBin = (float)lo;
    out->hiBin = (float)(lo + (int32_t)width - 1);
}

void l3_band_place(const l3_band_noise_t *noise, float centreBin, float searchBins,
                   float widthBins, l3_band_t *out)
{
    uint32_t width;
    int32_t centre = (int32_t)floorf(centreBin + 0.5F);
    int32_t first;
    int32_t last;
    int32_t start;
    int32_t bestStart = 0;
    float bestSum = 0.0F;
    float bestGap = 0.0F;
    uint8_t found = 0U;

    memset(out, 0, sizeof(*out));
    if (!(widthBins >= 0.5F)) {
        return;
    }
    width = (uint32_t)(widthBins + 0.5F);
    first = (int32_t)ceilf(centreBin - searchBins);
    last = (int32_t)floorf(centreBin + searchBins) - (int32_t)width + 1;
    if (noise->updates >= L3_BAND_NOISE_MIN_UPDATES) {
        if (first < (int32_t)noise->firstBin) {
            first = (int32_t)noise->firstBin;
        }
        if (last > (int32_t)(noise->firstBin + noise->count) - (int32_t)width) {
            last = (int32_t)(noise->firstBin + noise->count) - (int32_t)width;
        }
        for (start = first; start <= last; start++) {
            float sum = 0.0F;
            float gap = fabsf((float)start + 0.5F * (float)(width - 1U) - centreBin);
            uint32_t k;

            for (k = 0U; k < width; k++) {
                sum += noise->avg[(uint32_t)start - noise->firstBin + k];
            }
            if (!found || sum > bestSum || (sum == bestSum && gap < bestGap)) {
                bestStart = start;
                bestSum = sum;
                bestGap = gap;
                found = 1U;
            }
        }
    }
    if (!found) {
        bestStart = centre - (int32_t)((width - 1U) / 2U);
    }
    l3_band_span(bestStart, width, out);
}
```

(`#include <math.h>` in `l3_band.c`.) `l3_impact_fit.c` defaults: `cfg->bandSearchBins = 10.0F;`. Mirrors and signatures:

```python
class BandNoise(ctypes.Structure):
    """``l3_band_noise_t``: per-bin EMA of the trigger statistic on idle frames."""

    _fields_ = [
        ("firstBin", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("updates", ctypes.c_uint32),
        ("avg", ctypes.c_float * BAND_NOISE_BINS),
    ]
```

```python
    "l3_band_noise_reset": ([_P(BandNoise)], None),
    "l3_band_noise_update": ([_P(BandNoise), _U32, _U32, _P(BinObs), _U32], None),
    "l3_band_place": ([_P(BandNoise), _F32, _F32, _F32, _P(Band)], None),
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_impact_fit.py -q -p no:cacheprovider`.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_band.h firmware/iwr6843/l3_band.c firmware/iwr6843/l3_impact_fit.h firmware/iwr6843/l3_impact_fit.c src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_impact_fit.py
git commit -m "iwr: a noise map of the idle frames and the noisiest contiguous band near the tee"
```

---

### Task 6: Place, freeze and use the automatic band in replay, board and Pi

**Files:**
- Modify: `src/openflight/iwr6843/firmware_replay.py`, `firmware/iwr6843/l3_dump.c`, `firmware/iwr6843/l3_band.h/.c` (remove `l3_band_around`), `src/openflight/iwr6843/firmware_host.py`, `src/openflight/server.py` (flag help and `init_iwr6843` docstring), `src/openflight/iwr6843/monitor.py` (docstring), `docs/reference/cli.md`, `docs/changelog.md`, `tests/iwr6843_synth.py`
- Test: `tests/test_iwr6843_firmware_replay.py`, `tests/test_iwr6843_firmware_band.py`, `tests/test_iwr6843_firmware_board_wiring.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: Task 5's `l3_band_noise_*`, `l3_band_place`, `bandSearchBins`.
- Produces: `synth_shot_dump(..., ridge_bins: tuple[int, ...] = (), ridge_amp: float | None = None, seed: int = 0)` — a return at each ridge bin with a random phase per loop (an MTI residual that persists); `ReplayResult.band_noise: tuple[float, ...]` (the map at the last placement) and `ReplayResult.band_frozen_frame: int | None`.

Rules (replay and board identical):
- `enabled = cfg.bandBins > 0`.
- Pre-impact frame, before extracting club targets: if enabled and not frozen → `l3_band_place(noise, destination, cfg.bandSearchBins, cfg.bandBins, &band)`; if not enabled → band invalid.
- Club targets: whole window through `l3_band_keep_short` when **enabled** (was: when the band is valid); the trigger region otherwise (unchanged).
- After the club track update: if enabled and the club track is inactive → `frozen = 0` and `l3_band_noise_update(noise, stat, window first bin, whole-window obs, count)`; if active → `frozen = 1`.
- Post-impact frames and the ball arm use the band as last placed. Rearm: `frozen = 0`; the noise map persists across shots.

- [ ] **Step 1: Write the failing tests**

`tests/iwr6843_synth.py`: add `ridge_bins=()`, `ridge_amp=None`, `seed=0` to `synth_shot_dump` (documented), and after the scatterer loops, before `pack_dump`:

```python
    # A ridge: returns whose phase is random from loop to loop, so the burst
    # MTI keeps a residual there on every frame, as the tee-band clutter does.
    rng = np.random.default_rng(seed)
    for frame in range(n_frames):
        for loop in range(loops):
            for bin_index in ridge_bins:
                phase = np.exp(1j * rng.uniform(0.0, 2.0 * np.pi))
                cube[frame, loop * n_tx : (loop + 1) * n_tx, :, bin_index] += (
                    (ridge_amp or amp) * phase
                )
```

(with `ridge_bins=()` nothing is drawn and existing callers get the same cube.)

Append to `tests/test_iwr6843_firmware_replay.py`:

```python
def test_the_band_lands_on_the_ridge_not_centred_on_the_tee(lib):
    from iwr6843_synth import synth_shot_dump

    # Impact at 100 ms: the club (22 m/s) is out of range before ~38 ms, so
    # frames 0..8 are idle and fill the noise map (8 updates needed).
    ridge = (33, 34, 35, 36, 37)  # beyond the tee at 29
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372, ridge_bins=ridge,
                          n_frames=36, t_impact_s=0.1)
    result = fr.replay_dump(
        raw, fr.ReplayConfig(tee_bin=29, dest_bin=29, impact_armed=True, band_bins=5.0), lib=lib
    )
    assert result.band == (33.0, 37.0)


def test_band_freezes_when_the_club_is_acquired_and_thaws_when_it_drops(lib):
    from iwr6843_synth import synth_shot_dump

    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372, ridge_bins=(33, 34, 35, 36, 37),
                          n_frames=36, t_impact_s=0.1)
    result = fr.replay_dump(
        raw, fr.ReplayConfig(tee_bin=29, dest_bin=29, impact_armed=True, band_bins=5.0), lib=lib
    )
    acquired = next(f.frame for f in result.frames if f.track_why == "acquired")
    assert result.band_frozen_frame == acquired


def test_band_off_places_nothing_and_keeps_the_trigger_view(lib):
    path, config = next(iter(fr.recording_configs()))
    assert fr.replay_file(path, config, lib=lib).band is None
```

Adjust the existing band tests that relied on a centred ±N band (e.g. `test_impact_arms_the_ball_tracker_at_the_band_edge_or_the_ball` expects `35.0` for ±6 around 29): recompute the expected far edge from the new width semantics (`band_bins=6.0` centred without history → 26..31, far edge 31.0; with history, from the noise) and say in the report which tests changed and why — this is a semantics change the spec mandates, not an expectation edited to pass.

Board wiring (append):

```python
def test_board_places_the_band_from_the_noise_map_until_frozen():
    self_trigger = body("l3_considerSelfTrigger")
    place = self_trigger.index("l3_band_place(&gBandNoise,")
    targets = self_trigger.index("l3_preImpactClubTargets(")
    assert place < targets
    assert "gBandFrozen" in self_trigger[:place + 200]
    assert "l3_band_noise_update(&gBandNoise," in self_trigger
    assert "gBandFrozen = 0U" in body("l3_trigRearm")
    assert "l3_band_around" not in SOURCE
```

Server: in `tests/test_server.py`, assert the `--iwr6843-tee-band-bins` help says "width".

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_iwr6843_firmware_replay.py -k "ridge or freezes or places_nothing" tests/test_iwr6843_firmware_board_wiring.py -v` → FAIL.

- [ ] **Step 3: Implement**

Replay (`replay_dump`): replace the one-off `lib.l3_band_around(...)` with `band = fw.Band()`, `noise = fw.BandNoise()`, `lib.l3_band_noise_reset(ctypes.byref(noise))`, `band_frozen = False`, `band_frozen_frame = None`, `band_enabled = fit_cfg.bandBins > 0`. In each pre-impact frame before `_pre_impact_club_targets`:

```python
        if band_enabled and not band_frozen:
            lib.l3_band_place(ctypes.byref(noise), float(destination), fit_cfg.bandSearchBins,
                              fit_cfg.bandBins, ctypes.byref(band))
```

`_pre_impact_club_targets` switches on `band_enabled` (pass it) instead of `band.valid`, and returns the whole-window observations and count it scored (`_banded_window_targets` already computes them: return them as well). After `l3_track_update`:

```python
        if band_enabled:
            if track.active:
                if not band_frozen:
                    band_frozen_frame = frame if band_frozen_frame is None else band_frozen_frame
                band_frozen = True
            else:
                band_frozen = False
                lib.l3_band_noise_update(ctypes.byref(noise), params.stat, window_start,
                                         window_obs, window_count)
```

(when the band is enabled but the frame was not scored over the whole window, skip the update.) `ball_arm_bin` is computed at the arm sites from the current band (`band.hiBin if band.valid else destination`), no longer once at setup. Report `band=(lo, hi)` from the band as last placed, `band_noise=tuple(noise.avg[:noise.count])`, `band_frozen_frame`.

Board (`l3_dump.c`): globals `static l3_band_noise_t gBandNoise; static uint8_t gBandFrozen;`; reset the map where `gImpactFitCfg` defaults are set once; in `l3_considerSelfTrigger` replace `l3_band_around((float)teeBin, gImpactFitCfg.bandBins, &gBand);` with

```c
        if (gImpactFitCfg.bandBins > 0.0F) {
            if (!gBandFrozen) {
                l3_band_place(&gBandNoise, (float)teeBin, gImpactFitCfg.bandSearchBins,
                              gImpactFitCfg.bandBins, &gBand);
            }
        } else {
            gBand.valid = 0U;
        }
```

`l3_preImpactClubTargets` switches on `gImpactFitCfg.bandBins > 0.0F` and reports the whole-window count it scored through an out parameter; after `l3_track_update`:

```c
        if (gImpactFitCfg.bandBins > 0.0F) {
            if (gClubTrack.active) {
                gBandFrozen = 1U;
            } else {
                gBandFrozen = 0U;
                if (windowCount > 0U) {
                    l3_band_noise_update(&gBandNoise, gTrigCfg.stat, frame.binStart, obs,
                                         windowCount);
                }
            }
        }
```

`l3_trigRearm`: `gBandFrozen = 0U;`. Remove `l3_band_around` from `l3_band.h/.c`, `_SIGNATURES` and its tests in `tests/test_iwr6843_firmware_band.py` (rewrite those tests' fixtures to build bands with `fw.Band(1, lo, hi)` directly).

Pi and docs: `--iwr6843-tee-band-bins` help: "Width in range bins of the tee band the club and ball trackers ignore, placed on the noisiest bins near the tee (0 = off; experimental)"; `init_iwr6843` and `IWR6843CaptureMonitor` docstrings say width; `docs/reference/cli.md` row and a `docs/changelog.md` entry: the value is now a width and the band is placed automatically; ball colour; club after impact.

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_board_wiring.py tests/test_iwr6843_firmware_club_follow.py tests/test_evaluate_iwr_tracking.py tests/test_iwr6843_dump_viewer.py tests/test_server.py tests/test_iwr6843_monitor.py -q -p no:cacheprovider`; full-suite failing set unchanged; lint. Rebuild the board in Docker; report DATA_RAM (expect roughly +268 B for the map and flag).

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/firmware_replay.py firmware/iwr6843/l3_dump.c firmware/iwr6843/l3_band.h firmware/iwr6843/l3_band.c src/openflight/iwr6843/firmware_host.py src/openflight/server.py src/openflight/iwr6843/monitor.py docs/reference/cli.md docs/changelog.md tests/iwr6843_synth.py tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_firmware_band.py tests/test_iwr6843_firmware_board_wiring.py tests/test_server.py
git commit -m "iwr: the tee band follows the noisiest idle bins near the tee and freezes on the swing"
```

---

### Task 7: Acceptance re-run and record

**Files:**
- Create: `docs/superpowers/specs/2026-09-29-impact-baseline-band-off.json`, `…-band5.json`, `…-band10.json`
- Modify: `docs/superpowers/specs/2026-09-29-iwr-club-out-auto-band-design.md` (append an "Acceptance results" section)

- [ ] **Step 1: Run the evaluator three times** (no code changes; each may take minutes — run in the background and wait):

```bash
uv run python scripts/analysis/evaluate_iwr_tracking.py tests/radar/recordings "C:/Users/corma/Desktop/OF Sessions/iwr6843" --impact --json docs/superpowers/specs/2026-09-29-impact-baseline-band-off.json
uv run python scripts/analysis/evaluate_iwr_tracking.py tests/radar/recordings "C:/Users/corma/Desktop/OF Sessions/iwr6843" --impact --band-bins 5 --json docs/superpowers/specs/2026-09-29-impact-baseline-band5.json
uv run python scripts/analysis/evaluate_iwr_tracking.py tests/radar/recordings "C:/Users/corma/Desktop/OF Sessions/iwr6843" --impact --band-bins 10 --json docs/superpowers/specs/2026-09-29-impact-baseline-band10.json
```

- [ ] **Step 2: Record** — append to the spec a table per run with: captures replayed; with an estimate / consistent over ball-visible captures; club-out `ok` over ball-visible captures (spec: ≥ half with the band on); median spread; club points in the band before impact; and the previous plan's band-off numbers (26/51 estimate, 18/51 consistent, 1077 µs) for comparison. Do not tune anything; list the failing lines with per-capture detail from the JSON.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-29-impact-baseline-band-off.json docs/superpowers/specs/2026-09-29-impact-baseline-band5.json docs/superpowers/specs/2026-09-29-impact-baseline-band10.json docs/superpowers/specs/2026-09-29-iwr-club-out-auto-band-design.md
git commit -m "iwr: acceptance for the club after impact and the automatic band"
```
