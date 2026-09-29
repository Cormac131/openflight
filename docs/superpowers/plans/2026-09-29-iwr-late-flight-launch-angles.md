# IWR6843 Late-Flight Onboard Launch Angles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The IWR6843 board reports launch angles (VLA/HLA) fitted from the ball's late flight, with a continuous track-rate TDM correction and the Pi's calibration applied, scored against the host LCMF-v1 angle.

**Architecture:** Pure-C firmware modules (`firmware/iwr6843/l3_*.c`) change first and are host-built and tested through ctypes (`firmware_host.py`). The board (`l3_dump.c`) and the replay (`firmware_replay.py`) then make identical calls. A new Pi module, `board_calibration.py`, turns the loaded `Calibration` into `trackCfg cal`/`elem` lines for the monitor and into replay overrides for the replay, viewer and evaluator: one source for all of them.

**Tech Stack:** C99 (TI SDK on the board, host `cc` via ctypes in tests), Python 3.11 with `uv`, pytest, numpy, Flask (viewer).

**Spec:** `docs/superpowers/specs/2026-09-29-iwr-late-flight-launch-angles-design.md`

## Global Constraints

- Always `uv run` (pytest, ruff, pylint); `uv run pylint src/openflight/ --fail-under=9`.
- TDD: every behaviour gets a failing test first, run and seen to fail.
- Board and replay make identical calls in identical order; the board is pinned by source-inspection tests (`tests/test_iwr6843_firmware_board_wiring.py`).
- ctypes mirrors in `src/openflight/iwr6843/firmware_host.py` must match the C structs field for field.
- Launch **speed**, range-only fallback, `points`, residual and confidence stay today's early fit over the first `launchPoints`, unchanged.
- Launch **angles** come only from the late fit; fewer than 3 late points carrying angles, or a failed `maxAngleResidualM` check, means VLA/HLA invalid while the speed is still reported.
- `lateRangeM` starting default: `0.6F` metres beyond the ball's origin, measured as `(point.rangeBin - originBin) * binWidthM`.
- Continuous TDM only where a fitted track rate exists (the ball track, ball hypotheses with a fit); the club and rate-less points keep `l3_angle_chirp_phase` unchanged.
- Replay default calibration is identity, which is how the recordings were made (ruling 3A of 2026-09-29). The viewer defaults to the calibration file, matching the board.
- Board calibration mapping (a ruling; spec "offsets"): `pitch_deg = degrees(cal.tilt_rad)`, `yaw_deg = 0`, `roll_deg = 0`, `az_offset_rad = horizontal_phase_reference_rad or 0.0`, `el_offset_deg = 0`, `range_bias_m = cal.range_bias_m`, elements `phase = -angle(cal.elem_correction[i])`, `gain = 1 / |cal.elem_correction[i]|`. `--iwr6843-azimuth-offset-deg` is a host HLA correction, not a firmware phase offset, so it is not sent.
- Calibration is sent before `_apply_self_trigger` (the `triggerCfg` that copies `gRadarCal` into the track configs).
- Acceptance gate: median |onboard VLA − LCMF VLA| ≤ 3° and at least 70% within 5°, over ball-visible OF Sessions captures where both exist. A miss is recorded as FAIL; no tuning beyond the `lateRangeM` sweep.
- DATA_RAM checked in the `openflight-iwr-sdk` Docker image before the plan is done.

## Review Focus

1. A ball track with only 3–5 points (the slow or short tracks, 6 of 18 good ones under 10 points) must report the speed and no angles, never an early-point angle. Pinned in Task 2 (`test_a_short_flight_reports_speed_and_no_angles`).
2. A monitor restart after firmware without `trackCfg elem`: angles doubted for the session, capture unaffected. Pinned in Task 4 (`test_old_firmware_calibration_refusal_doubts_onboard_angles_and_continues`).
3. A track rate above ±63 m/s, beyond the branch search: continuous TDM must still correct it. Pinned in Task 1 (`test_continuous_tdm_corrects_beyond_the_branch_search`).
4. `run_on_other_profile` retunes and re-applies the self-trigger. The calibration persists in `gRadarCal` across `sensorStart`/`triggerCfg`, pinned in Task 3 (`test_track_cfg_cal_and_elem_survive_trigger_cfg_and_sensor_start`).
5. The inclinometer's per-shot effective tilt is applied to host angles only. The board uses the configured tilt, and the session log records the calibration sent so a mismatch is visible. Pinned in Task 4 (`test_session_config_records_the_board_calibration`).

---

### Task 1: Continuous TDM correction in `l3_angle`

**Files:**
- Modify: `firmware/iwr6843/l3_angle.h` (snapshot struct, new function)
- Modify: `firmware/iwr6843/l3_angle.c:59-81,184`
- Modify: `src/openflight/iwr6843/firmware_host.py:376-386` (`AngleSnapshot` mirror), the prototypes table near line 1343
- Test: `tests/test_iwr6843_firmware_angle.py`

**Interfaces:**
- Produces: `l3_angle_snapshot_t.continuousTdm` (`uint8_t`, last field, 0 from `l3_angle_snapshot_init`); `float l3_angle_motion_phase(float radialVelocityMps, float periodS)` = 4π·v·T/λ, unwrapped. Python mirror `fw.AngleSnapshot.continuousTdm`; binding `"l3_angle_motion_phase": ([_F32, _F32], _F32)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_iwr6843_firmware_angle.py`)

```python
# --- continuous TDM from a track rate (2026-09-29 late-flight spec) ---------------


def test_motion_phase_is_four_pi_v_t_over_lambda(lib):
    assert lib.l3_angle_motion_phase(30.0, 45e-6) == pytest.approx(
        4 * math.pi * 30.0 * 45e-6 / 0.00484, rel=1e-6
    )
    assert lib.l3_angle_motion_phase(-30.0, 45e-6) == pytest.approx(
        -4 * math.pi * 30.0 * 45e-6 / 0.00484, rel=1e-6
    )


def test_snapshot_init_leaves_continuous_tdm_off(lib):
    snap = fw.AngleSnapshot()
    snap.continuousTdm = 7
    lib.l3_angle_snapshot_init(ctypes.byref(snap), 3, 4)
    assert snap.continuousTdm == 0


@pytest.mark.parametrize("velocity", [5.0, 38.0, 61.0])
def test_continuous_tdm_uses_the_track_rate_not_the_lag1_branch(lib, velocity):
    """A lag-1 phase that points at the wrong branch cannot move a continuous
    correction: the chirp phase is exactly 4 pi v tau / lambda."""
    snap = fw.AngleSnapshot()
    lib.l3_angle_snapshot_init(ctypes.byref(snap), 3, 4)
    for i in range(12):
        snap.channel[i] = fw.Cpx(1.0, 0.0)
    snap.chirpPeriodS = 45e-6
    snap.radialVelocityMps = velocity
    snap.lag1PhaseRad = 2.5  # a branch nowhere near the track rate
    snap.continuousTdm = 1
    obs = fw.AngleObs()
    assert lib.l3_angle_estimate(ctypes.byref(identity_cal(lib)), ctypes.byref(snap), ctypes.byref(obs))
    assert obs.chirpPhaseRad == pytest.approx(lib.l3_angle_motion_phase(velocity, 45e-6), rel=1e-6)


def test_continuous_tdm_corrects_beyond_the_branch_search(lib):
    """Review focus 3: 70 m/s is past the +/-63 m/s the branch snap can reach."""
    snap = fw.AngleSnapshot()
    lib.l3_angle_snapshot_init(ctypes.byref(snap), 3, 4)
    for i in range(12):
        snap.channel[i] = fw.Cpx(1.0, 0.0)
    snap.chirpPeriodS = 45e-6
    snap.radialVelocityMps = 70.0
    snap.continuousTdm = 1
    obs = fw.AngleObs()
    lib.l3_angle_estimate(ctypes.byref(identity_cal(lib)), ctypes.byref(snap), ctypes.byref(obs))
    assert obs.chirpPhaseRad == pytest.approx(lib.l3_angle_motion_phase(70.0, 45e-6), rel=1e-6)


def test_without_continuous_tdm_the_branch_snap_is_unchanged(lib):
    snap = fw.AngleSnapshot()
    lib.l3_angle_snapshot_init(ctypes.byref(snap), 3, 4)
    for i in range(12):
        snap.channel[i] = fw.Cpx(1.0, 0.0)
    snap.chirpPeriodS = 45e-6
    snap.radialVelocityMps = 38.0
    snap.lag1PhaseRad = 2.5
    obs = fw.AngleObs()
    lib.l3_angle_estimate(ctypes.byref(identity_cal(lib)), ctypes.byref(snap), ctypes.byref(obs))
    assert obs.chirpPhaseRad == pytest.approx(lib.l3_angle_chirp_phase(2.5, 3, 38.0, 45e-6))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_angle.py -k "motion_phase or continuous" -v`
Expected: FAIL (`AttributeError: ... l3_angle_motion_phase` / no field `continuousTdm`).

- [ ] **Step 3: Implement**

`l3_angle.h`: add the last field to `l3_angle_snapshot_t` and the prototype.

```c
    float    chirpPeriodS;       /* TDM tau */
    /* 1: the per-chirp TDM phase is l3_angle_motion_phase(radialVelocityMps,
     * chirpPeriodS), a fitted track rate; 0: the lag-1 branch snap. */
    uint8_t  continuousTdm;
} l3_angle_snapshot_t;
```

```c
/* 4 pi v T / lambda, unwrapped: the phase a radial velocity v (positive away)
 * turns in T seconds. */
float l3_angle_motion_phase(float radialVelocityMps, float periodS);
```

`l3_angle.c`: define it next to `l3_angle_chirp_phase` and use it in `l3_angle_estimate`. `l3_angle_snapshot_init` already memsets, so `continuousTdm` starts at 0.

```c
float l3_angle_motion_phase(float radialVelocityMps, float periodS)
{
    return 4.0F * L3_ANGLE_PI * radialVelocityMps * periodS / L3_ANGLE_WAVELENGTH_M;
}
```

In `l3_angle_estimate`, replace the `out->chirpPhaseRad = l3_angle_chirp_phase(...)` statement:

```c
    out->chirpPhaseRad = snapshot->continuousTdm
                             ? l3_angle_motion_phase(snapshot->radialVelocityMps,
                                                     snapshot->chirpPeriodS)
                             : l3_angle_chirp_phase(snapshot->lag1PhaseRad, ntx,
                                                    snapshot->radialVelocityMps,
                                                    snapshot->chirpPeriodS);
```

Also use `l3_angle_motion_phase` for `expected` inside `l3_angle_chirp_phase` (DRY):
`float expected = l3_angle_motion_phase(radialVelocityMps, chirpPeriodS);`

`firmware_host.py`: add `("continuousTdm", ctypes.c_uint8)` as the last `AngleSnapshot` field, and `"l3_angle_motion_phase": ([_F32, _F32], _F32),` beside `l3_angle_chirp_phase`.

- [ ] **Step 4: Run the angle tests**

Run: `uv run pytest tests/test_iwr6843_firmware_angle.py -v`
Expected: all PASS (the existing ones unchanged).

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_angle.h firmware/iwr6843/l3_angle.c src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_angle.py
git commit -m "iwr: angle snapshots can take a continuous TDM correction from a track rate"
```

---

### Task 2: Late-flight launch angles in `l3_ball_track_launch`

**Files:**
- Modify: `firmware/iwr6843/l3_ball_track.h` (cfg field), `firmware/iwr6843/l3_ball_track.c` (defaults, `l3_ball_track_launch`, status line)
- Modify: `firmware/iwr6843/l3_launch.h` (`lateFrom`), `firmware/iwr6843/l3_launch.c` (format)
- Modify: `src/openflight/iwr6843/firmware_host.py:831-846,868-884` (`BallTrackCfg`, `Launch`)
- Test: `tests/test_iwr6843_firmware_ball_track.py`

**Interfaces:**
- Produces: `l3_ball_track_cfg_t.lateRangeM` (`float`, placed after `skipClubClaim`, before the `#if L3_BALL_HYPOTHESES` block; the spec's "last field" is ruled to mean the last unconditional field), default `0.6F`. `l3_launch_t.lateFrom` (`uint8_t`, after `vlaValid`, `L3_LAUNCH_NO_LATE` = `0xFFU` when no late fit). Python: `fw.BallTrackCfg.lateRangeM`, `fw.Launch.lateFrom`, `fw.LAUNCH_NO_LATE = 0xFF`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_iwr6843_firmware_ball_track.py`)

```python
# --- late-flight launch angles (2026-09-29 spec) ------------------------------------


BOTH = fw.ANGLE_AZIMUTH | fw.ANGLE_ELEVATION


def _set_point_angles(lib, ball, *, vla_deg, hla_deg, speed, flip_first):
    """Rewrite each point's angles from the true geometry; the first ``flip_first``
    read the floor image (elevation mirrored, azimuth thrown 40 deg), as the
    captures near launch show."""
    core = ball.track.core
    cal = core.cfg.cal
    vx = speed * math.cos(vla_deg * DEG) * math.cos(hla_deg * DEG)
    vy = speed * math.cos(vla_deg * DEG) * math.sin(hla_deg * DEG)
    vz = speed * math.sin(vla_deg * DEG)
    for index in range(core.count):
        point = fw.TrackPoint()
        lib.l3_track_point(ctypes.byref(core), index, ctypes.byref(point))
        s = (point.timestampUs - IMPACT_US) * 1e-6
        golf = fw.Vec3(ORIGIN[0] + vx * s, ORIGIN[1] + vy * s, ORIGIN[2] + vz * s)
        radar = fw.Vec3()
        lib.l3_frames_golf_to_radar(ctypes.byref(cal), ctypes.byref(golf), ctypes.byref(radar))
        sph = fw.Spherical()
        lib.l3_frames_to_spherical(ctypes.byref(radar), ctypes.byref(sph))
        az, el = sph.azimuthRad, sph.elevationRad
        if index < flip_first:
            az, el = az + 40.0 * DEG, -el
        assert lib.l3_track_set_point_angles(ctypes.byref(core), index, az, el, BOTH) == 1


def test_defaults_put_the_late_window_0p6_m_past_the_ball(lib):
    cfg = fw.BallTrackCfg()
    lib.l3_ball_track_cfg_defaults(ctypes.byref(cfg))
    assert cfg.lateRangeM == pytest.approx(0.6)


def test_launch_angles_come_from_the_late_points_when_the_early_ones_flip(lib):
    ball, _ = fly(lib, speed=45.0, vla_deg=14.0, hla_deg=2.0, frames=14, angles=False)
    _set_point_angles(lib, ball, vla_deg=14.0, hla_deg=2.0, speed=45.0, flip_first=4)
    used, launch = ball.launch()
    assert launch.vlaValid and launch.hlaValid
    assert launch.vlaRad / DEG == pytest.approx(14.0, abs=0.5)
    assert launch.hlaRad / DEG == pytest.approx(2.0, abs=0.5)
    assert launch.lateFrom != fw.LAUNCH_NO_LATE
    first = fw.TrackPoint()
    lib.l3_track_point(ctypes.byref(ball.track.core), launch.lateFrom, ctypes.byref(first))
    assert (first.rangeBin - ORIGIN_BIN) * BIN_M >= 0.6


def test_the_speed_is_still_the_early_fit(lib):
    """Speed, points, residual and confidence do not move with the late window."""
    ball, _ = fly(lib, speed=45.0, vla_deg=14.0, frames=14, angles=False)
    early = fw.Launch()
    ref = fw.Delivery()
    lib.l3_track_delivery_range(ctypes.byref(ball.track.core), 0, 6, 6, ctypes.byref(ref))
    lib.l3_launch_from_delivery(ctypes.byref(ref), IMPACT_US, ctypes.byref(early))
    _, launch = ball.launch()
    assert launch.points == early.points == 6
    assert launch.speedMps == pytest.approx(early.speedMps)
    assert launch.radialSpeedMps == pytest.approx(early.radialSpeedMps)
    assert launch.confidence == pytest.approx(early.confidence)


def test_a_short_flight_reports_speed_and_no_angles(lib):
    """Review focus 1: 5 points at 45 m/s never reach 0.6 m with three of them."""
    ball, _ = fly(lib, speed=45.0, vla_deg=14.0, frames=5)
    used, launch = ball.launch()
    assert used > 0 and launch.speedValid
    assert not launch.vlaValid and not launch.hlaValid
    assert launch.lateFrom == fw.LAUNCH_NO_LATE


def test_late_points_without_angles_give_no_angles(lib):
    ball, _ = fly(lib, speed=45.0, vla_deg=14.0, frames=14, angles=False)
    _, launch = ball.launch()
    assert launch.speedValid and not launch.vlaValid and not launch.hlaValid


def test_scattered_late_angles_are_still_rejected(lib):
    ball, _ = fly(lib, speed=45.0, vla_deg=14.0, frames=14, angles=False)
    core = ball.track.core
    for index in range(core.count):
        jitter = 25.0 * DEG if index % 2 else -25.0 * DEG
        lib.l3_track_set_point_angles(ctypes.byref(core), index, jitter, jitter, BOTH)
    _, launch = ball.launch()
    assert launch.speedValid and not launch.vlaValid


def test_the_late_window_is_measured_from_the_origin(lib):
    """A larger lateRangeM starts the late fit further out, or not at all."""
    near = fly(lib, speed=45.0, vla_deg=14.0, frames=14, ball=Ball(lib, lateRangeM=0.3))[0]
    far = fly(lib, speed=45.0, vla_deg=14.0, frames=14, ball=Ball(lib, lateRangeM=3.0))[0]
    assert near.launch()[1].lateFrom < 6
    assert far.launch()[1].lateFrom == fw.LAUNCH_NO_LATE
```

`l3_track_set_point_angles` doesn't exist yet; Step 3 adds it: `int32_t l3_track_set_point_angles(l3_club_track_t *track, uint32_t index, float azimuthRad, float elevationRad, uint8_t anglesValid)` writes one stored point's angles and recomputes its position exactly as `l3_track_set_angles` does for the newest point.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_ball_track.py -k "late or short_flight or early_fit or scattered_late" -v`
Expected: FAIL (no `lateRangeM` / `lateFrom` / `l3_track_set_point_angles`).

- [ ] **Step 3: Implement**

`l3_club_track.h/.c`: generalise the newest-point angle setter. Keep `l3_track_set_angles(track, az, el, valid)` as a call to `l3_track_set_point_angles(track, track->count - 1U, az, el, valid)` so there's one implementation. Returns 1 when the index is stored, else 0.

`l3_launch.h`:

```c
    uint8_t   vlaValid;
    /* Index of the first ball point the angles were fitted from (the late
     * window), L3_LAUNCH_NO_LATE when no late fit gave angles. */
    uint8_t   lateFrom;
} l3_launch_t;

#define L3_LAUNCH_NO_LATE 0xFFU
```

`l3_launch_from_delivery` sets `out->lateFrom = L3_LAUNCH_NO_LATE` after its memset. `l3_launch_format` appends ` late=<n>` (or `late=-`).

`l3_ball_track.h`: after `uint32_t skipClubClaim;`

```c
    /* Launch angles are fitted only from points at least this far beyond the
     * ball's origin (metres): near launch the floor image flips them
     * (2026-09-29 stage comparison). The speed keeps the earliest points. */
    float    lateRangeM;
```

`l3_ball_track.c`, defaults: `cfg->lateRangeM = 0.6F;`. Then `l3_ball_track_launch`:

```c
uint32_t l3_ball_track_launch(const l3_ball_track_t *track, l3_launch_t *out)
{
    l3_delivery_t fit;
    l3_delivery_t late;
    uint32_t used;
    uint32_t first;

    memset(out, 0, sizeof(*out));
    out->lateFrom = L3_LAUNCH_NO_LATE;
    if (!track->confirmed) {
        return 0U;
    }
    used = l3_track_delivery_range(&track->core, 0U, track->cfg.launchPoints,
                                   track->cfg.launchPoints, &fit);
    if (used == 0U) {
        return 0U;
    }
    l3_launch_from_delivery(&fit, track->impactTimestampUs, out);
    /* Angles only from the late window. */
    out->hlaValid = 0U;
    out->vlaValid = 0U;
    out->hlaRad = 0.0F;
    out->vlaRad = 0.0F;
    first = l3_ball_track_late_first(track);
    if (first < track->core.count &&
        l3_track_delivery_range(&track->core, first, track->cfg.launchPoints,
                                track->cfg.launchPoints, &late) != 0U) {
        if (late.pathValid) {
            out->hlaRad = late.pathRad;
            out->hlaValid = 1U;
        }
        if (late.attackValid) {
            out->vlaRad = late.attackRad;
            out->vlaValid = 1U;
        }
        if (out->hlaValid || out->vlaValid) {
            out->lateFrom = (uint8_t)((first > 0xFEU) ? 0xFEU : first);
        }
    }
    return used;
}
```

with the static helper:

```c
/* The first point at least lateRangeM beyond the origin; count when none. */
static uint32_t l3_ball_track_late_first(const l3_ball_track_t *track)
{
    uint32_t i;
    l3_track_point_t point;

    for (i = 0U; i < track->core.count; i++) {
        if (l3_track_point(&track->core, i, &point) &&
            (point.rangeBin - track->originBin) * track->core.cfg.binWidthM >=
                track->cfg.lateRangeM) {
            return i;
        }
    }
    return track->core.count;
}
```

`l3_delivery_fit` already needs 3 angled points and applies `maxAngleResidualM`, so the "under 3" and "scattered" cases fall out. `l3_ball_track_format_status` gains nothing: the late index is in `l3_launch_format`.

`firmware_host.py`: `("lateRangeM", ctypes.c_float)` after `skipClubClaim` in `BallTrackCfg`; `("lateFrom", ctypes.c_uint8)` after `vlaValid` in `Launch`; `LAUNCH_NO_LATE = 0xFF`; binding `"l3_track_set_point_angles": ([_P(ClubTrack), _U32, _F32, _F32, _U8], ctypes.c_int32)`.

- [ ] **Step 4: Run the ball-track, launch and result tests**

Run: `uv run pytest tests/test_iwr6843_firmware_ball_track.py tests/test_iwr6843_firmware_launch.py tests/test_iwr6843_firmware_result.py -v`
Expected: all PASS. `test_launch_recovers_speed_hla_and_vla_with_the_documented_signs` must still pass: its six 60 m/s points reach 0.68–1.04 m, three late points. If it doesn't, stop and report; don't loosen it.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_ball_track.h firmware/iwr6843/l3_ball_track.c firmware/iwr6843/l3_launch.h firmware/iwr6843/l3_launch.c firmware/iwr6843/l3_club_track.h firmware/iwr6843/l3_club_track.c src/openflight/iwr6843/firmware_host.py tests/test_iwr6843_firmware_ball_track.py
git commit -m "iwr: launch angles are fitted from the ball's late flight; speed keeps the early points"
```

---

### Task 3: Board and replay use the track rate; the replay takes element calibration

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c` (ball-track and hypothesis angle blocks near 3693-3755; `l3_channelSnapshot` callers)
- Modify: `src/openflight/iwr6843/firmware_replay.py` (`ReplayConfig`, `_radar_cal`, `_estimate_angles`, `_hypothesis_angles`, `channel_snapshot` call, ball cfg `lateRangeM`)
- Test: `tests/test_iwr6843_firmware_board_wiring.py`, `tests/test_iwr6843_firmware_replay.py`

**Interfaces:**
- Consumes: Task 1 `continuousTdm`, `l3_angle_motion_phase`; Task 2 `lateRangeM`.
- Produces: `ReplayConfig.elem_phase_rad: tuple[float, ...] | None = None`, `ReplayConfig.elem_gain: tuple[float, ...] | None = None`, `ReplayConfig.late_range_m: float | None = None`; `_estimate_angles(..., chirp_period_s, *, track_rate_mps: float | None = None)`.

- [ ] **Step 1: Write the failing tests**

Board wiring (append to `tests/test_iwr6843_firmware_board_wiring.py`):

```python
def test_ball_angles_use_the_ball_tracks_rate_continuously():
    ball = body("l3_considerBallTrack")
    # l3_track_recent_rate(&gBallTrack.core) already feeds the club follow;
    # pin the angle use itself.
    assert (
        "float rateMps = l3_track_recent_rate(&gBallTrack.core) * gBallTrack.core.cfg.binWidthM;"
        in ball
    )
    assert "l3_angle_motion_phase(rateMps, gTrigLoopPeriodS)" in ball
    assert "snapshot.continuousTdm = 1U;" in ball


def test_hypothesis_angles_use_their_fitted_rate_continuously():
    ball = body("l3_considerBallTrack")
    hyps = ball[ball.index("l3_ball_hyp_fit(") :]
    assert "snapshot.continuousTdm = (radial != 0.0F) ? 1U : 0U;" in hyps


def test_track_cfg_cal_and_elem_survive_trigger_cfg_and_sensor_start():
    """Review focus 4: only l3_ensureRadarCal initialises gRadarCal, and only
    when it has never been set; nothing else overwrites it."""
    assert SOURCE.count("l3_cal_identity(&gRadarCal") == 1
    ensure = body("l3_ensureRadarCal")
    assert "if (gRadarCal.virtualElements == 0U) {" in ensure
    for handler in ("l3_cli_triggerCfg", "l3_cli_sensorStart"):
        assert "gRadarCal =" not in body(handler)
        assert "memset(&gRadarCal" not in body(handler)
```

Replay (append to `tests/test_iwr6843_firmware_replay.py`):

```python
def test_replay_applies_element_calibration_through_the_firmware_setter(lib):
    phases = (0.28, 0.38, 0.43, 0.31, -0.43, -0.32, -0.24, -0.36)
    gains = (0.95, 0.86, 0.99, 1.13, 1.01, 0.92, 1.04, 1.09)
    config = ReplayConfig(tee_bin=TEE_BIN, elem_phase_rad=phases, elem_gain=gains)
    cal = fr._radar_cal(lib, config)  # pylint: disable=protected-access
    for i, (phase, gain) in enumerate(zip(phases, gains)):
        assert cal.correctionRe[i] == pytest.approx(math.cos(-phase) / gain, rel=1e-5)
        assert cal.correctionIm[i] == pytest.approx(math.sin(-phase) / gain, rel=1e-5)


def test_replay_without_element_calibration_is_identity(lib):
    cal = fr._radar_cal(lib, ReplayConfig(tee_bin=TEE_BIN))  # pylint: disable=protected-access
    assert [cal.correctionRe[i] for i in range(8)] == [1.0] * 8


@pytest.mark.parametrize("phases, gains", [((0.1,) * 7, (1.0,) * 8), ((0.1,) * 8, (1.0,) * 8 + (1.0,))])
def test_element_calibration_needs_eight_of_each(phases, gains):
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    with pytest.raises(ValueError, match="8 element"):
        replay_dump(raw, ReplayConfig(tee_bin=TEE_BIN, elem_phase_rad=phases, elem_gain=gains))


def test_replay_ball_angles_use_the_track_rate(lib, monkeypatch):
    seen = []
    original = fr._estimate_angles  # pylint: disable=protected-access

    def spy(*args, track_rate_mps=None, **kwargs):
        seen.append(track_rate_mps)
        return original(*args, track_rate_mps=track_rate_mps, **kwargs)

    monkeypatch.setattr(fr, "_estimate_angles", spy)
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    replay_dump(raw, ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN), lib=lib)
    assert any(rate is not None and rate > 0.0 for rate in seen)


def test_synthetic_shot_late_flight_vla_matches_its_launch(lib):
    """End to end: the synthesized 12 deg launch is read back from the late
    points. The synthetic scene has no floor, so this pins the chain, not the
    multipath fix. If the synthetic generator does not model the array phase
    (the ball points carry no valid angles), report BLOCKED with that finding
    rather than loosening the assertion."""
    raw = synth_shot_dump(ball_speed_ms=60.0, vla_deg=12.0, hla_deg=0.0, tee_range_m=TEE_RANGE_M, n_frames=24)
    result = replay_dump(raw, ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN, late_range_m=0.3), lib=lib)
    assert result.launch is not None and result.launch.vla_deg is not None
    assert result.launch.vla_deg == pytest.approx(12.0, abs=2.0)


def test_replay_late_range_reaches_the_ball_track(lib):
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    result = replay_dump(raw, ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN, late_range_m=0.9), lib=lib)
    assert result.ball_track.cfg.lateRangeM == pytest.approx(0.9)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_firmware_board_wiring.py tests/test_iwr6843_firmware_replay.py -k "track_rate or continuously or elem or late_range or survive" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Board, `l3_considerBallTrack`, the ball-track angle block (currently `l3_channelSnapshot(..., hit->dopplerPhaseRad, newest.radialVelocityMps, &snapshot)`):

```c
        {
            l3_angle_obs_t angle;
            /* The fitted range rate, not the two-point difference: continuous
             * TDM and loop summing from it (late-flight spec, 2026-09-29). */
            float rateMps = l3_track_recent_rate(&gBallTrack.core) * gBallTrack.core.cfg.binWidthM;
            float loopPhase = l3_angle_motion_phase(rateMps, gTrigLoopPeriodS);

            if (rateMps != 0.0F) {
                l3_channelSnapshot(&frame, (uint32_t)hit->peakBin - frame.binStart,
                                   atan2f(sinf(loopPhase), cosf(loopPhase)), rateMps, &snapshot);
                snapshot.continuousTdm = 1U;
            } else {
                l3_channelSnapshot(&frame, (uint32_t)hit->peakBin - frame.binStart,
                                   hit->dopplerPhaseRad, newest.radialVelocityMps, &snapshot);
            }
            if (l3_angle_estimate(&gRadarCal, &snapshot, &angle)) {
```

(the remainder of the block is unchanged). In the hypothesis block, after `l3_channelSnapshot(... radial, &snapshot);` use the fitted `radial` for the loop phase too:

```c
            if (radial != 0.0F) {
                float loopPhase = l3_angle_motion_phase(radial, gTrigLoopPeriodS);

                l3_channelSnapshot(&frame, (uint32_t)hit->peakBin - frame.binStart,
                                   atan2f(sinf(loopPhase), cosf(loopPhase)), radial, &snapshot);
            } else {
                l3_channelSnapshot(&frame, (uint32_t)hit->peakBin - frame.binStart,
                                   hit->dopplerPhaseRad, radial, &snapshot);
            }
            snapshot.continuousTdm = (radial != 0.0F) ? 1U : 0U;
```

Check `l3_track_recent_rate`'s units before wiring: it returns bins/s (see `l3_club_track.c:597`), hence the `* binWidthM`. The hypothesis `radial` is already m/s.

Replay (`firmware_replay.py`):
- `ReplayConfig`: add after `ball_snr`:

```python
    # Element calibration ("trackCfg elem"), physical order: None is identity,
    # as the recordings were made. board_calibration.replay_overrides fills it.
    elem_phase_rad: tuple[float, ...] | None = None
    elem_gain: tuple[float, ...] | None = None
    # The ball tracker's late window for launch angles (lateRangeM); None: firmware default.
    late_range_m: float | None = None
```

- In `replay_dump`, beside the other config checks: raise `ValueError("element calibration needs 8 element phases and 8 gains")` unless both are None or both have length 8.
- `_radar_cal`: after the attitude fields,

```python
    if config.elem_phase_rad is not None and config.elem_gain is not None:
        for index, (phase, gain) in enumerate(zip(config.elem_phase_rad, config.elem_gain)):
            if lib.l3_cal_set_element(ctypes.byref(cal), index, gain, phase) != 0:
                raise ValueError(f"element {index}: gain must be positive, got {gain}")
```

- Ball cfg setup (beside `ball_snr`): `if config.late_range_m is not None: ball_cfg.lateRangeM = config.late_range_m`.
- `_estimate_angles(..., chirp_period_s, *, track_rate_mps=None)`: when `track_rate_mps` is truthy, `loop = lib.l3_angle_motion_phase(track_rate_mps, chirp_period_s * n_tx)`, call `channel_snapshot(..., lag1_phase_rad=math.atan2(math.sin(loop), math.cos(loop)), radial_velocity_mps=track_rate_mps, ...)`, and set `snapshot.continuousTdm = 1`. Otherwise it behaves as today.
- Ball-track caller: `track_rate_mps = lib.l3_track_recent_rate(ctypes.byref(ball_track.core)) * bin_width_m`, passed as `track_rate_mps=... or None`.
- `_hypothesis_angles`: pass `track_rate_mps=radial or None`.

- [ ] **Step 4: Run the wiring, replay and angle suites**

Run: `uv run pytest tests/test_iwr6843_firmware_board_wiring.py tests/test_iwr6843_firmware_replay.py tests/test_iwr6843_firmware_angle.py tests/test_iwr6843_firmware_sparse.py -v`
Expected: PASS. Recorded-capture expectations that move because the ball angles changed are reported, not re-baselined silently: write the old and new values into the task report, and update the expectation only when the change is the intended angle change.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_dump.c src/openflight/iwr6843/firmware_replay.py tests/test_iwr6843_firmware_board_wiring.py tests/test_iwr6843_firmware_replay.py
git commit -m "iwr: ball angles use the track rate on the board and in the replay; the replay takes element calibration"
```

---

### Task 4: The Pi sends the board calibration

**Files:**
- Create: `src/openflight/iwr6843/board_calibration.py`
- Modify: `src/openflight/iwr6843/driver.py` (after `set_ball_snr`), `src/openflight/iwr6843/monitor.py` (`__init__`, `start`, the onboard result read near line 695), `src/openflight/iwr6843/shot_result.py` (`ShotResultPacket`), `src/openflight/server.py` (`init_iwr6843` near 1228-1310)
- Test: `tests/test_iwr6843_board_calibration.py` (create), `tests/test_iwr6843_driver.py`, `tests/test_iwr6843_monitor.py`, `tests/test_server.py`, `tests/test_gpio_pin_factory.py` and `tests/test_iwr6843_tee_windows.py` (fake radars gain the two methods)

**Interfaces:**
- Produces:
  - `BoardCalibration` (frozen dataclass: `pitch_deg`, `yaw_deg`, `roll_deg`, `az_offset_rad`, `el_offset_deg`, `range_bias_m`: float; `elem_phase_rad`, `elem_gain`: `tuple[float, ...]` of 8), with:
    - `BoardCalibration.identity()`;
    - `BoardCalibration.from_calibration(cal: Calibration, *, horizontal_phase_reference_rad: float | None = None)`;
    - `BoardCalibration.from_file(path: str | Path)`;
    - `.cal_args -> tuple[float, ...]` (6 values in `trackCfg cal` order);
    - `.replay_overrides() -> dict` (`pitch_deg`, `yaw_deg`, `roll_deg`, `azimuth_offset_rad`, `elevation_offset_deg`, `range_bias_m`, `elem_phase_rad`, `elem_gain`), keys matching `ReplayConfig`;
    - `.to_dict()` for the session log.
  - `IWR6843Radar.set_radar_cal(args: tuple[float, ...], *, identity: bool) -> bool` and `IWR6843Radar.set_elements(phases, gains, *, identity: bool) -> bool`.
  - `IWR6843CaptureMonitor(board_calibration: BoardCalibration | None = None)`, `.calibration_applied: bool`.
  - `ShotResultPacket.with_launch_angles_doubted() -> ShotResultPacket`.
  - `init_iwr6843(..., )` records `iwr6843_runtime_config["board_calibration"]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_iwr6843_board_calibration.py`:

```python
"""The one mapping from the loaded Calibration to what the board and the
replay are told (late-flight spec, 2026-09-29)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.iwr6843.board_calibration import BoardCalibration
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.firmware_replay import ReplayConfig

REFERENCE = "config/iwr6843_calibration_reference.json"


def test_identity_is_zero_attitude_and_unit_elements():
    ident = BoardCalibration.identity()
    assert ident.cal_args == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert ident.elem_phase_rad == (0.0,) * 8 and ident.elem_gain == (1.0,) * 8


def test_from_calibration_maps_tilt_bias_and_elements():
    cal = Calibration.load(REFERENCE)
    board = BoardCalibration.from_calibration(cal, horizontal_phase_reference_rad=0.12)
    assert board.pitch_deg == pytest.approx(math.degrees(cal.tilt_rad))
    assert (board.yaw_deg, board.roll_deg, board.el_offset_deg) == (0.0, 0.0, 0.0)
    assert board.az_offset_rad == pytest.approx(0.12)
    assert board.range_bias_m == pytest.approx(cal.range_bias_m)
    rebuilt = np.exp(-1j * np.array(board.elem_phase_rad)) / np.array(board.elem_gain)
    assert rebuilt == pytest.approx(cal.elem_correction, rel=1e-9)


def test_file_values_round_trip_to_the_json():
    raw = Calibration.load(REFERENCE).meta
    board = BoardCalibration.from_file(REFERENCE)
    assert board.elem_phase_rad == pytest.approx(tuple(raw["elem_phase_rad"]))
    assert board.elem_gain == pytest.approx(tuple(raw["elem_gain"]))


def test_replay_overrides_are_replay_config_fields():
    overrides = BoardCalibration.from_file(REFERENCE).replay_overrides()
    config = ReplayConfig(tee_bin=34, **overrides)
    assert config.elem_gain == BoardCalibration.from_file(REFERENCE).elem_gain


@pytest.mark.parametrize("gains", [(1.0,) * 7, (1.0,) * 7 + (0.0,), (1.0,) * 7 + (float("nan"),)])
def test_bad_elements_are_refused(gains):
    with pytest.raises(ValueError, match="element"):
        BoardCalibration(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (0.0,) * len(gains), gains)
```

Driver (append to `tests/test_iwr6843_driver.py`):

```python
def test_set_radar_cal_and_elements_send_the_track_cfg_sub_modes(monkeypatch):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    calls = []
    monkeypatch.setattr(radar, "cmd", lambda command, window: calls.append(command) or "Done\n")

    assert radar.set_radar_cal((10.4, 0.0, 0.0, 0.12, 0.0, 0.066), identity=False) is True
    assert radar.set_elements((0.28, -0.43) + (0.0,) * 6, (0.95, 1.01) + (1.0,) * 6, identity=False)

    assert calls[0] == "trackCfg cal 10.4 0 0 0.12 0 0.066"
    assert calls[1:] == [
        "trackCfg elem 0 0.28 0.95",
        "trackCfg elem 1 -0.43 1.01",
        *[f"trackCfg elem {i} 0 1" for i in range(2, 8)],
    ]


@pytest.mark.parametrize(
    "reply", ["Error: trackCfg <loopPeriodS> ...\n", "'trackCfg' is not recognized as a CLI command\n"]
)
def test_identity_calibration_on_firmware_without_it_is_not_an_error(monkeypatch, reply):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "cmd", lambda *_a, **_k: reply)
    assert radar.set_radar_cal((0.0,) * 6, identity=True) is False
    assert radar.set_elements((0.0,) * 8, (1.0,) * 8, identity=True) is False
    with pytest.raises(RuntimeError):
        radar.set_radar_cal((10.4, 0, 0, 0, 0, 0), identity=False)


def test_calibration_on_a_silent_board_fails(monkeypatch):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    monkeypatch.setattr(radar, "cmd", lambda *_a, **_k: "")
    with pytest.raises(RuntimeError, match="did not acknowledge"):
        radar.set_elements((0.0,) * 8, (1.0,) * 8, identity=True)
```

Monitor (append to `tests/test_iwr6843_monitor.py`; `TeeBandRadar` gains recording `set_radar_cal`/`set_elements` that append `("cal", args)` / `("elem", phases)` to `self.events` and return `self.cal_supported`, default True, and its `__init__` takes `cal_supported: bool = True`):

```python
def test_board_calibration_is_sent_after_band_and_ball_snr_before_the_trigger(tmp_path):
    radar = TeeBandRadar(_raw_dump())
    board = BoardCalibration.from_file("config/iwr6843_calibration_reference.json")
    monitor = _tee_band_monitor(tmp_path, radar, board_calibration=board)
    monitor.start(armed=False)
    try:
        kinds = [kind for kind, _ in radar.events]
        assert kinds.index("band") < kinds.index("cal") < kinds.index("elem")
        assert radar.events[kinds.index("cal")][1] == board.cal_args
        assert monitor.calibration_applied is True
    finally:
        monitor.stop()


def test_no_calibration_sends_identity_so_a_restart_clears_it(tmp_path):
    radar = TeeBandRadar(_raw_dump())
    monitor = _tee_band_monitor(tmp_path, radar)
    monitor.start(armed=False)
    try:
        cal = next(args for kind, args in radar.events if kind == "cal")
        assert cal == BoardCalibration.identity().cal_args
    finally:
        monitor.stop()


def test_old_firmware_calibration_refusal_doubts_onboard_angles_and_continues(tmp_path, caplog):
    radar = TeeBandRadar(_raw_dump(), cal_supported=False)
    monitor = _tee_band_monitor(tmp_path, radar)
    with caplog.at_level(logging.WARNING, logger="openflight.iwr6843.monitor"):
        monitor.start(armed=False)
    try:
        assert monitor._running  # pylint: disable=protected-access
        assert monitor.calibration_applied is False
        assert any("calibration" in r.getMessage() for r in caplog.records)
    finally:
        monitor.stop()
```

The monitor's onboard result read applies `with_launch_angles_doubted()` when `calibration_applied` is False. Test it where `_read_onboard_result` (the method near line 695) is exercised: a fake `shot_result()` returning a packet with usable VLA gives `vertical_launch.usable is False` after the read when uncalibrated, and True when calibrated.

`shot_result` (append to `tests/test_iwr6843_firmware_result.py`):

```python
def test_doubting_launch_angles_leaves_everything_else(lib):
    packet = _parsed(lib, build(lib, make_shot(lib), make_ball(lib), make_launch()))
    doubted = packet.with_launch_angles_doubted()
    assert not doubted["vertical_launch"].usable and not doubted["horizontal_launch"].usable
    for name in ("ball_speed", "club_speed", "club_path", "angle_of_attack"):
        assert doubted[name].usable == packet[name].usable, name
```

Server (in `TestIWR6843ShotIntegration`, `tests/test_server.py`):

```python
    def test_init_iwr6843_sends_the_calibration_the_host_uses(self, monkeypatch, tmp_path):
        captured = self._init_capturing_monitor_kwargs(monkeypatch, tmp_path, tilt_deg=12.0)
        board = captured["board_calibration"]
        assert board.pitch_deg == pytest.approx(12.0), "--iwr6843-tilt-deg reaches the board"
        assert server_module.iwr6843_runtime.calibration.tilt_rad == pytest.approx(math.radians(12.0))
        server_module.iwr6843_runtime = None

    def test_session_config_records_the_board_calibration(self, monkeypatch, tmp_path):
        self._init_capturing_monitor_kwargs(monkeypatch, tmp_path)
        recorded = server_module.iwr6843_runtime_config["board_calibration"]
        assert set(recorded) >= {"pitch_deg", "elem_phase_rad", "elem_gain"}
        server_module.iwr6843_runtime = None
```

(`_init_capturing_monitor_kwargs` loads `cal.json` through a monkeypatched `Calibration.load`. Make that fake return `Calibration.load(REFERENCE)` so the elements are real.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_board_calibration.py tests/test_iwr6843_driver.py tests/test_iwr6843_monitor.py tests/test_server.py tests/test_iwr6843_firmware_result.py -k "calibration or cal_ or elements or doubt" -v`
Expected: FAIL (module missing, methods missing).

- [ ] **Step 3: Implement**

`board_calibration.py`:

```python
"""What the board (``trackCfg cal`` / ``trackCfg elem``) and the replay are
told about the array: one mapping from the loaded ``Calibration``, so the
onboard angles and the host LCMF share a source (late-flight spec 2026-09-29)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from openflight.iwr6843.calibration import Calibration

N_ELEMENTS = 8


@dataclass(frozen=True)
class BoardCalibration:  # pylint: disable=too-many-instance-attributes
    """``trackCfg cal`` values in the firmware's units, and the 8 elements."""

    pitch_deg: float
    yaw_deg: float
    roll_deg: float
    az_offset_rad: float
    el_offset_deg: float
    range_bias_m: float
    elem_phase_rad: tuple[float, ...]
    elem_gain: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.elem_phase_rad) != N_ELEMENTS or len(self.elem_gain) != N_ELEMENTS:
            raise ValueError(f"board calibration needs {N_ELEMENTS} element phases and gains")
        if not all(math.isfinite(g) and g > 0.0 for g in self.elem_gain):
            raise ValueError("every element gain must be finite and positive")
        if not all(math.isfinite(v) for v in (*self.cal_args, *self.elem_phase_rad)):
            raise ValueError("board calibration values must be finite")

    @classmethod
    def identity(cls) -> BoardCalibration:
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (0.0,) * N_ELEMENTS, (1.0,) * N_ELEMENTS)

    @classmethod
    def from_calibration(
        cls, cal: Calibration, *, horizontal_phase_reference_rad: float | None = None
    ) -> BoardCalibration:
        correction = np.asarray(cal.elem_correction, dtype=complex)
        return cls(
            pitch_deg=math.degrees(cal.tilt_rad),
            yaw_deg=0.0,
            roll_deg=0.0,
            az_offset_rad=float(horizontal_phase_reference_rad or 0.0),
            el_offset_deg=0.0,
            range_bias_m=float(cal.range_bias_m),
            elem_phase_rad=tuple(float(-np.angle(c)) for c in correction),
            elem_gain=tuple(float(1.0 / abs(c)) for c in correction),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> BoardCalibration:
        return cls.from_calibration(Calibration.load(str(path)))

    @property
    def is_identity(self) -> bool:
        return self == BoardCalibration.identity()

    @property
    def cal_args(self) -> tuple[float, ...]:
        """``trackCfg cal <pitchDeg> <yawDeg> <rollDeg> <azOffsetRad> <elOffsetDeg> <rangeBiasM>``."""
        return (
            self.pitch_deg,
            self.yaw_deg,
            self.roll_deg,
            self.az_offset_rad,
            self.el_offset_deg,
            self.range_bias_m,
        )

    def replay_overrides(self) -> dict:
        """The same values as ``ReplayConfig`` fields."""
        return {
            "pitch_deg": self.pitch_deg,
            "yaw_deg": self.yaw_deg,
            "roll_deg": self.roll_deg,
            "azimuth_offset_rad": self.az_offset_rad,
            "elevation_offset_deg": self.el_offset_deg,
            "range_bias_m": self.range_bias_m,
            "elem_phase_rad": self.elem_phase_rad,
            "elem_gain": self.elem_gain,
        }

    def to_dict(self) -> dict:
        return asdict(self)
```

Driver, beside `set_ball_snr`:

```python
    def set_radar_cal(self, args: tuple[float, ...], *, identity: bool) -> bool:
        """``trackCfg cal``: the attitude and baseline zeros (``BoardCalibration.cal_args``).

        Kept by the firmware across ``triggerCfg`` and ``sensorStart``; the next
        ``triggerCfg`` copies it into the track configs. False when firmware
        without it refuses the identity; any other refusal, and silence, raise.
        """
        text = " ".join(f"{value:g}" for value in args)
        return self._set_track_cfg_sub_mode(f"trackCfg cal {text}", identity)

    def set_elements(self, phases, gains, *, identity: bool) -> bool:
        """``trackCfg elem i phase gain`` for every element, physical order."""
        applied = True
        for index, (phase, gain) in enumerate(zip(phases, gains)):
            applied = (
                self._set_track_cfg_sub_mode(
                    f"trackCfg elem {index} {phase:g} {gain:g}", identity
                )
                and applied
            )
        return applied
```

Monitor: `board_calibration: BoardCalibration | None = None` (stored as `self.board_calibration = board_calibration or BoardCalibration.identity()`); `self.calibration_applied = False`. In `start`, after the ball-snr block and before `_configure_onboard_tracking` / `_apply_self_trigger`:

```python
            # Always sent, identity included: the firmware keeps it across
            # sensorStart. Sent before triggerCfg, which copies it into the tracks.
            board = self.board_calibration
            applied = self.radar.set_radar_cal(board.cal_args, identity=board.is_identity)
            applied = (
                self.radar.set_elements(
                    board.elem_phase_rad, board.elem_gain, identity=board.is_identity
                )
                and applied
            )
            self.calibration_applied = applied
            if not applied:
                logger.warning(
                    "[IWR6843] Firmware has no trackCfg cal/elem: onboard launch angles "
                    "are uncalibrated and will not be used this session"
                )
```

In the onboard result read (`result = self.radar.shot_result()`), after the None check: `if not self.calibration_applied: result = result.with_launch_angles_doubted()`.

`shot_result.py`, on `ShotResultPacket`:

```python
    def with_launch_angles_doubted(self) -> ShotResultPacket:
        """The same packet with the ball's launch angles marked implausible:
        the board ran without the calibration the Pi uses."""
        metrics = dict(self.metrics)
        for name in ("vertical_launch", "horizontal_launch"):
            if metrics[name].value is not None:
                metrics[name] = replace(metrics[name], implausible=True)
        return replace(self, metrics=metrics)
```

Server `init_iwr6843`, after `calibration` is loaded and the tilt applied, before the monitor is built:

```python
        board_calibration = BoardCalibration.from_calibration(
            calibration, horizontal_phase_reference_rad=horizontal_phase_reference_rad
        )
```

The existing monitor tests that pin the exact event list (`test_tee_band_is_sent_after_the_config`, `test_tee_band_off_is_still_sent_so_a_restart_clears_a_stale_band`, `test_tee_band_is_on_at_the_firmware_default_width_by_default`) compare `radar.events[:2]` (and `events[1]` for the band) once calibration events follow; their intent is unchanged.

Pass `board_calibration=board_calibration` to `IWR6843CaptureMonitor(...)` and add `"board_calibration": board_calibration.to_dict()` to `iwr6843_runtime_config`. The fake radars in `tests/test_iwr6843_monitor.py` (`FakeRadar`), `tests/test_gpio_pin_factory.py` and `tests/test_iwr6843_tee_windows.py` gain `set_radar_cal(self, args, *, identity)` and `set_elements(self, phases, gains, *, identity)` returning True.

- [ ] **Step 4: Run the Pi suites**

Run: `uv run pytest tests/test_iwr6843_board_calibration.py tests/test_iwr6843_driver.py tests/test_iwr6843_monitor.py tests/test_server.py tests/test_iwr6843_firmware_result.py tests/test_gpio_pin_factory.py tests/test_iwr6843_tee_windows.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/board_calibration.py src/openflight/iwr6843/driver.py src/openflight/iwr6843/monitor.py src/openflight/iwr6843/shot_result.py src/openflight/server.py tests/test_iwr6843_board_calibration.py tests/test_iwr6843_driver.py tests/test_iwr6843_monitor.py tests/test_server.py tests/test_iwr6843_firmware_result.py tests/test_gpio_pin_factory.py tests/test_iwr6843_tee_windows.py
git commit -m "iwr: the Pi sends the board the calibration the host uses; uncalibrated onboard angles are doubted"
```

---

### Task 5: Viewer and evaluator use the calibration; the evaluator scores angles against LCMF

**Files:**
- Modify: `src/openflight/iwr6843/dump_viewer.py` (`ViewerOptions`, `firmware_section`, launch summary), `scripts/iwr6843/dump_viewer.html` (two form boxes, launch readout)
- Modify: `scripts/analysis/evaluate_iwr_tracking.py` (`--angles`, `--calibration`)
- Modify: `src/openflight/iwr6843/datasets.py:292` (`measure_with_firmware(shot, *, lib=None, calibration: BoardCalibration | None = None)`)
- Test: `tests/test_iwr6843_dump_viewer.py`, `tests/test_evaluate_iwr_tracking.py`, `tests/test_iwr6843_datasets.py` (if present, else in the evaluator test)

**Interfaces:**
- Consumes: Task 4 `BoardCalibration.from_file(...).replay_overrides()`; Task 3 `ReplayConfig.late_range_m`.
- Produces:
  - `ViewerOptions.calibration: bool = True` and `ViewerOptions.late_range_m: float | None = None`;
  - `evaluate_iwr_tracking.angle_outcome(case, result, lcmf_result) -> dict` with keys `vla`, `hla`, `lcmf_vla`, `lcmf_hla`, `d_vla`, `d_hla`, `late_from`;
  - `summarize_angles(outcomes) -> dict` with keys `both`, `median_abs_d_vla`, `within_5_share`, `no_angles`, `median_abs_d_hla`, `gate_pass`.

- [ ] **Step 1: Write the failing tests**

Viewer:

```python
def test_viewer_defaults_to_the_boards_calibration(monkeypatch):
    seen = {}

    def capture(_raw, config):
        seen["config"] = config
        raise RuntimeError("stop")

    monkeypatch.setattr(dv.fr, "replay_dump", capture)
    dv.analyze_dump(_variable_dump(), dv.ViewerOptions())
    board = BoardCalibration.from_file(dv.DEFAULT_CALIBRATION_PATH)
    assert seen["config"].elem_gain == board.elem_gain
    assert seen["config"].pitch_deg == pytest.approx(board.pitch_deg)


def test_viewer_without_calibration_replays_identity_and_keeps_the_pitch_box(monkeypatch):
    seen = {}
    monkeypatch.setattr(dv.fr, "replay_dump", lambda _r, c: seen.setdefault("config", c) and _raise())
    dv.analyze_dump(_variable_dump(), dv.ViewerOptions(calibration=False, pitch_deg=10.5))
    assert seen["config"].elem_gain is None and seen["config"].pitch_deg == 10.5


def test_late_range_reaches_the_replay(monkeypatch):
    seen = {}
    monkeypatch.setattr(dv.fr, "replay_dump", lambda _r, c: seen.setdefault("config", c) and _raise())
    dv.analyze_dump(_variable_dump(), dv.ViewerOptions.from_mapping({"late_range_m": "0.8"}))
    assert seen["config"].late_range_m == 0.8
```

`test_page_has_a_box_for_every_option` (already present) then requires `id="calibration"` and `id="late_range_m"` in the page.

Evaluator:

```python
def test_angle_outcome_compares_onboard_with_lcmf():
    result = SimpleNamespace(launch=SimpleNamespace(vla_deg=14.0, hla_deg=1.5, late_from=6))
    lcmf_result = SimpleNamespace(angle_deg=12.5, horizontal_deg=2.0, accepted=True)
    out = ev.angle_outcome(None, result, lcmf_result)
    assert out["d_vla"] == pytest.approx(1.5) and out["d_hla"] == pytest.approx(-0.5)


def test_angle_summary_applies_the_gate():
    rows = [{"d_vla": d, "d_hla": 0.0, "vla": 1.0, "lcmf_vla": 1.0} for d in (1, 2, -2, 4, 6)]
    rows.append({"d_vla": None, "d_hla": None, "vla": None, "lcmf_vla": 10.0})
    summary = ev.summarize_angles(rows)
    assert summary["both"] == 5 and summary["no_angles"] == 1
    assert summary["median_abs_d_vla"] == pytest.approx(2.0)
    assert summary["within_5_share"] == pytest.approx(0.8)
    assert summary["gate_pass"] is True


def test_angle_gate_fails_on_a_wide_spread():
    rows = [{"d_vla": d, "d_hla": 0.0, "vla": 1.0, "lcmf_vla": 1.0} for d in (4, 5, -6, 7)]
    assert ev.summarize_angles(rows)["gate_pass"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py tests/test_evaluate_iwr_tracking.py -k "calibration or late_range or angle" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`dump_viewer.py`: `DEFAULT_CALIBRATION_PATH = "config/iwr6843_calibration_reference.json"` (resolved from the repo root, as the server's default is). New fields `calibration: bool = True` and `late_range_m: float | None = None`. In `firmware_section`: build the `ReplayConfig` as today, then when `options.calibration` is set, `config = replace(config, **{k: v for k, v in BoardCalibration.from_file(path).replay_overrides().items() if k != "pitch_deg"})`, and set `pitch_deg` from the calibration only when the page's `pitch_deg` is the default 0.0 (an explicit pitch box wins). Pass `late_range_m`. `ViewerOptions.for_recording` adds `"calibration": False` (recording-era identity, ruling 3A). Page: a `calibration` checkbox and a `late range (m)` number box in `OPTS`, and the launch readout shows `late from point N` or `no late angles`.

Evaluator: `--angles` and `--calibration PATH` (default the reference file). Per case with `--angles`: `config = replace(case.config, **BoardCalibration.from_file(args.calibration).replay_overrides())`, replay, then `lcmf.estimate_lcmf_v1(raw, cal, ball_speed_mph=case.ops_mps / MPH_TO_MPS)` with `cal = Calibration.load(args.calibration)` and `cal.tee_range_m` from the session context (`tee_range_m`, else `DEFAULT_TEE_RANGE_M`). Collect `angle_outcome` rows, print `summarize_angles`, and add both to the `--json` output under `"angles"`.

```python
ANGLE_GATE_MEDIAN_DEG = 3.0
ANGLE_GATE_WITHIN_DEG = 5.0
ANGLE_GATE_SHARE = 0.70


def angle_outcome(case, result, lcmf_result) -> dict:
    """Onboard launch angles against LCMF-v1 for one capture; None where absent."""
    launch = result.launch
    vla = None if launch is None else launch.vla_deg
    hla = None if launch is None else launch.hla_deg
    lcmf_vla = lcmf_result.angle_deg if lcmf_result is not None and lcmf_result.accepted else None
    lcmf_hla = lcmf_result.horizontal_deg if lcmf_result is not None and lcmf_result.accepted else None
    return {
        "name": None if case is None else case.path.name,
        "vla": vla,
        "hla": hla,
        "lcmf_vla": lcmf_vla,
        "lcmf_hla": lcmf_hla,
        "d_vla": None if vla is None or lcmf_vla is None else vla - lcmf_vla,
        "d_hla": None if hla is None or lcmf_hla is None else hla - lcmf_hla,
        "late_from": None if launch is None else getattr(launch, "late_from", None),
    }


def summarize_angles(rows) -> dict:
    """The spec's gate: median |dVLA| <= 3 deg and >= 70% within 5 deg."""
    rows = list(rows)
    both = [abs(r["d_vla"]) for r in rows if r["d_vla"] is not None]
    hla = [abs(r["d_hla"]) for r in rows if r.get("d_hla") is not None]
    median = statistics.median(both) if both else None
    share = sum(1 for d in both if d <= ANGLE_GATE_WITHIN_DEG) / len(both) if both else None
    return {
        "both": len(both),
        "no_angles": sum(1 for r in rows if r["vla"] is None and r["lcmf_vla"] is not None),
        "median_abs_d_vla": median,
        "within_5_share": share,
        "median_abs_d_hla": statistics.median(hla) if hla else None,
        "gate_pass": bool(both)
        and median <= ANGLE_GATE_MEDIAN_DEG
        and share >= ANGLE_GATE_SHARE,
    }
```

`LaunchSummary` (the replay's launch summary dataclass) gains `late_from: int | None` from `Launch.lateFrom` (None when `fw.LAUNCH_NO_LATE`). `datasets.measure_with_firmware` takes `calibration: BoardCalibration | None = None` and, when given, applies `replay_overrides()` over the radar-position attitude.

- [ ] **Step 4: Run the viewer, evaluator and dataset suites**

Run: `uv run pytest tests/test_iwr6843_dump_viewer.py tests/test_evaluate_iwr_tracking.py tests/test_iwr6843_datasets.py -v`
Expected: PASS (a missing `tests/test_iwr6843_datasets.py` is fine; say so in the report).

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/dump_viewer.py scripts/iwr6843/dump_viewer.html scripts/analysis/evaluate_iwr_tracking.py src/openflight/iwr6843/datasets.py src/openflight/iwr6843/firmware_replay.py tests/test_iwr6843_dump_viewer.py tests/test_evaluate_iwr_tracking.py
git commit -m "iwr: the viewer and evaluator replay with the board's calibration; --angles scores onboard angles against LCMF"
```

---

### Task 6: Choose `lateRangeM`, run acceptance, docs, DATA_RAM

**Files:**
- Modify (only if the sweep picks a different default): `firmware/iwr6843/l3_ball_track.c` default, `tests/test_iwr6843_firmware_ball_track.py::test_defaults_put_the_late_window_0p6_m_past_the_ball`
- Modify: `docs/superpowers/specs/2026-09-29-iwr-late-flight-launch-angles-design.md` (append "Acceptance results"), `docs/changelog.md`, `docs/reference/cli.md` (the calibration the Pi now sends; no new flags)
- Create: `docs/superpowers/specs/2026-09-29-late-flight-angles-sweep.json`, `docs/superpowers/specs/2026-09-29-late-flight-angles-acceptance.json`

- [ ] **Step 1: Sweep `lateRangeM`**

For each of 0.3, 0.45, 0.6, 0.8, 1.0, 1.2 m, run the evaluator's `--angles` over the OF Sessions captures with `late_range_m` set. Add a `--late-range-m` option to the evaluator for this (it maps to `ReplayConfig.late_range_m`), with a test like Task 5's.

Run (per value): `uv run python scripts/analysis/evaluate_iwr_tracking.py tests/radar/recordings "C:/Users/corma/Desktop/OF Sessions/iwr6843" --angles --late-range-m <v> --json <tmp>/sweep_<v>.json`

Collect `median_abs_d_vla`, `within_5_share`, `both` and `no_angles` per value into `2026-09-29-late-flight-angles-sweep.json`. Choose the default as the value with the smallest `median_abs_d_vla` among those keeping `both` at least 60% of the 0.3 m count; ties go to the smaller value. That rule is fixed before looking. If the choice isn't 0.6, change the C default and its test, and run `uv run pytest tests/test_iwr6843_firmware_ball_track.py`.

- [ ] **Step 2: Acceptance run**

Run: `uv run python scripts/analysis/evaluate_iwr_tracking.py tests/radar/recordings "C:/Users/corma/Desktop/OF Sessions/iwr6843" --angles --json docs/superpowers/specs/2026-09-29-late-flight-angles-acceptance.json`

Append to the spec, under `## Acceptance results`: the table (median |ΔVLA|, share within 5°, captures with both, no-angle count, median |ΔHLA|), the gate line as PASS or FAIL, the sweep table, and "TrackMan: no labelled reference shots in `tests/radar/datasets/` (README only); not measured." No tuning after the sweep.

- [ ] **Step 3: Full suite and lint**

Run: `uv run pytest tests/ -q -p no:cacheprovider`
Expected: no failures beyond the recorded baseline (5 failed, 30 errors in the IWR/server set; unrelated camera, cloud, launcher, geekworm, serial and sim failures). List any new ones and fix them before continuing.
Run: `uv run pylint src/openflight/ --fail-under=9` and `uv run ruff check src/openflight/` and `uv run ruff format --check src/openflight/`

- [ ] **Step 4: DATA_RAM**

Build `l3_dump.bin` in the `openflight-iwr-sdk` Docker image as done for the 2026-09-29 band work, and record the DATA_RAM free bytes (651 B before this plan) in the spec's results. More than 64 B lost, or negative headroom, is a stop-and-report.

- [ ] **Step 5: Docs and commit**

`docs/changelog.md` under `[Unreleased]` → `### Changed`: onboard launch angles come from the late flight (`lateRangeM`, default N m) with a continuous TDM correction from the ball track rate. The Pi sends `trackCfg cal` and `trackCfg elem` from the calibration file at every start. Old firmware logs a warning and its onboard angles aren't used. The firmware and the Pi must be updated together. `docs/reference/cli.md`: a note under the IWR6843 section that `--iwr6843-calibration`, `--iwr6843-tilt-deg` and `--iwr6843-horizontal-phase-reference-rad` now also configure the board.

```bash
git add docs/superpowers/specs/2026-09-29-iwr-late-flight-launch-angles-design.md docs/superpowers/specs/2026-09-29-late-flight-angles-sweep.json docs/superpowers/specs/2026-09-29-late-flight-angles-acceptance.json docs/changelog.md docs/reference/cli.md scripts/analysis/evaluate_iwr_tracking.py tests/test_evaluate_iwr_tracking.py
git commit -m "iwr: late-flight angle sweep and acceptance against LCMF"
```
