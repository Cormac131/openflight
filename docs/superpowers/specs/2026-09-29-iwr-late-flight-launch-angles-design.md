# IWR6843 late-flight onboard launch angles — design

Base: `feat/iwr-calcs`. Approved in conversation 2026-09-29 (option 8A of the
angle investigation); this is the written spec for review.

## Intent

The board should report launch angles (VLA/HLA) that can be trusted, taken
from the part of the ball's flight where the floor reflection no longer flips
them. Launch speed keeps today's early fit (0.87–1.13× OPS on the good
tracks). Success is agreement with the host's LCMF-v1 angle, and an explicit
"no angles" when a track has too few late points. The Pi keeps preferring its
own LCMF angle by default; this makes the onboard angle usable and honest.

## Problem (measured 2026-09-29)

On 51 ball-visible captures the firmware never produces launch angles: the
launch fit's angle-residual check rejects them on every one, correctly.

- Per-point ball angles scatter (median SD over the first 6 points: azimuth
  27°, elevation 12°) while the range walk is tight (0.024 m).
- Stage-by-stage against the host chain (18 good tracks, 7 where both
  pipelines follow the same return): the host chain itself flips in the
  firmware's launch window (median detrended elevation scatter 5.1°) and is
  clean later (1.1°). The firmware's own chain: 8.0° on points 1–5, 1.5° on
  points 6+. Cause: near launch the ball is low and close, so the direct path
  and its floor image arrive together and the estimate jumps between them;
  the firmware fits launch angles from the first `launchPoints` (6) points.
- Secondary, from swapping one host stage into a firmware replica: TDM and
  loop phase from the range walk instead of the lag-1 branch (scatter 9.1° →
  7.4°, bias −6.0° → −1.8°); element calibration (bias −3.9°); MUSIC-high
  (6.9°). The Pi sends neither `trackCfg cal` nor `trackCfg elem`, so the
  board also runs with radar pitch 0 on a rig tilted ~10.4°.
- On 9 of the 18 good-speed tracks the firmware and host ball tracks are
  4–22 bins apart: different returns.

## A. Firmware

Files: `l3_ball_track.h/.c`, `l3_club_track.h/.c` (fit helper, if needed),
`l3_angle.h/.c`, `l3_dump.c`, tests.

- **Late-flight launch fit.** `l3_ball_track_launch`:
  - Speed and the range-only fallback: today's fit over the first
    `launchPoints`, unchanged (same speed, residual and confidence).
  - Angles: a second `l3_delivery_fit` over only the points whose range is
    at least `lateRangeM` beyond the ball's origin range (the range the
    tracker was armed at). `lateRangeM` is a new `l3_ball_track_cfg_t` field
    (last field), default chosen from the captures in the plan's sweep,
    starting value 0.6 m.
  - VLA/HLA come from that fit's velocity direction. They are valid only when
    the late fit has at least 3 points carrying the angles it reads and the
    existing angle-residual check (`maxAngleResidualM`) passes; otherwise
    they are invalid while the speed stays reported.
  - `l3_launch_t` gains `lateFrom` (index of the first point used, or 0xFF
    when no late fit), shown in the ball status line.
- **TDM from the track rate.** `l3_angle_snapshot_t` gains
  `uint8_t continuousTdm` (0 by `l3_angle_snapshot_init`). When set, the
  per-TX correction is
  4π·v·τ/λ with v the caller's radial velocity, not snapped to a lag-1
  branch. Ball points pass v = the ball track's fitted range rate
  (`l3_track_recent_rate(&gBallTrack.core)`), and the loop-summing phase in
  the channel snapshot uses the same v (3 chirps per loop). Callers without a
  track rate (the club, the first ball point) keep today's branch snap, so
  club behaviour is unchanged.
- **Calibration.** `trackCfg cal` and `trackCfg elem` already exist and
  persist across `triggerCfg` and `sensorStart` (checked by tests, fixed if
  not).
- Board and replay make identical calls. DATA_RAM checked in the
  `openflight-iwr-sdk` Docker image.

## B. Pi

Files: `driver.py`, `monitor.py`, `server.py`, `shot_result.py`, docs.

- **Driver.** `set_radar_cal(pitch_deg, yaw_deg, roll_deg, az_offset_rad,
  el_offset_deg)` sends `trackCfg cal …`; `set_elements(phases, gains)` sends
  `trackCfg elem <i> <phaseRad> <gain>` for i = 0..7. Both use
  `_set_track_cfg_sub_mode`: old firmware refusing the identity value returns
  False; any other refusal, and silence, raise.
- **Monitor.** New `board_calibration: BoardCalibration | None` (frozen
  dataclass: pitch/yaw/roll degrees, azimuth offset rad, elevation offset
  degrees, 8 phases, 8 gains; identity when None). Sent at every start after
  the band and ball snr, identity included, so a restart clears stale values.
  When firmware predates the commands: warning, start continues, and the
  monitor records `calibration_applied = False`.
- **Uncalibrated angles are never used.** With `calibration_applied` False,
  the onboard VLA/HLA are treated as implausible for that session
  (`shot_result`/server), so `_apply_onboard_metrics` never copies them.
- **Server.** `init_iwr6843` builds `BoardCalibration` from the same
  `Calibration` object it already loads and adjusts (`--iwr6843-tilt-deg`,
  `--iwr6843-azimuth-offset-deg`), so board and host LCMF share one source.
  No new flags. The session log records the calibration sent.

## C. Replay, viewer, evaluator

Files: `firmware_replay.py`, `dump_viewer.py`, `scripts/iwr6843/dump_viewer.*`,
`scripts/analysis/evaluate_iwr_tracking.py`, tests.

- `ReplayConfig` gains `elem_phase_rad` / `elem_gain` (None = identity, as
  the recordings were made) and `late_range_m` (None = firmware default). One
  helper loads the calibration file into these (and pitch), shared by the
  viewer, the evaluator and the server's `BoardCalibration`.
- The replay makes the same TDM-from-track-rate call as the board.
- Viewer defaults to the board's behaviour: calibration file applied; new
  `late range (m)` box; the launch angles and `lateFrom` shown.
- Evaluator `--angles`: replays with the calibration file and compares
  onboard VLA/HLA with host LCMF-v1 per ball-visible capture; reports median
  |ΔVLA|, share within 5°, HLA vs LCMF horizontal, tracks with no angles, and
  TrackMan comparisons where the session carries TrackMan values.

## Testing

TDD throughout; each bullet is a failing test first.

- **Late fit (C, host-built):** early points flipping ±15° with clean later
  points give the clean launch angle; fewer than 3 late points, or late
  points without angles, give no angles with the speed still reported; the
  residual check still rejects scattered late points; `lateRangeM` measured
  from the origin range; speed identical to today's early fit.
- **TDM (C):** with a track rate the correction is continuous, including
  beyond the ±63 m/s the branch search reaches; without one the branch snap
  is unchanged; replay snapshot and C agree to float precision.
- **Board wiring (source inspection):** the ball angle call passes the track
  rate; the launch fit is configured with the late range; `trackCfg cal` and
  `elem` persist across `triggerCfg`/`sensorStart`.
- **Pi:** exact `trackCfg cal`/`elem` lines; old-firmware refusal and silence;
  the monitor always sends the calibration after band and ball snr, identity
  included; a refusal marks angles not applied and the parser/server then
  treat onboard VLA/HLA as unusable; the server builds `BoardCalibration`
  from the same object LCMF uses and `--iwr6843-tilt-deg` reaches both.
- **Replay/viewer:** calibration file loads into `ReplayConfig`; None is
  identity; viewer defaults to calibrated; the synthetic shot's late-flight
  VLA matches its synthesized launch angle.

## Acceptance

Replay the ball-visible OF Sessions captures with the calibration file.

- Gate: median |onboard VLA − LCMF VLA| ≤ 3° and at least 70% within 5°,
  over captures where both exist.
- Reported alongside: HLA vs LCMF horizontal; tracks with no angles; results
  against TrackMan where available; the `lateRangeM` sweep used to choose the
  default.
- A miss is recorded as FAIL with the numbers; no tuning beyond that sweep.
- No new failures in the full suite beyond the recorded baseline; DATA_RAM
  checked.

## Risks

- Short tracks (6 of the 18 good tracks have under 10 points) will honestly
  report no angles.
- Where the firmware follows a different return from the host (9 of 18), its
  angles disagree with LCMF; these count against the gate, not excluded.
- Firmware and Pi must update together: old firmware on a new Pi warns and
  shows no onboard angles; a new board on an old Pi runs uncalibrated. The
  changelog says so.
