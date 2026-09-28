# IWR6843 joint club/ball tracking after impact — spec

Base: `feat/iwr-calcs` at `776c266` plus the uncommitted 2026-09-27/28 work (dump viewer,
club-track rules, `l3_track_follow`). Author of the rules: the user; review input: an external
code review pasted on 2026-09-28.

## Problem

After the gate fires, `l3_ball_track.c` offers every target 1–8 bins beyond the origin and the
shared track core acquires the most confident one; two points at 10–100 m/s confirm it as the
ball. The club carrying on through impact is usually the most confident target, and its
follow-through passes that test, so the ball track follows the club (recordings 2026-09-27
14:43:41 shot 13, 2026-08-24 12:04:08 shot 1). The comment at `l3_ball_track.c:88` says only
the farthest candidate is offered; the code offers all of them.

## Rules (from the user)

1. The club moves into ascending bins, never more than two consecutive points in one bin.
   *(Implemented 2026-09-28 in `l3_club_track.c`: `ascendingOnly`, `maxSameBinPoints`.)*
2. After impact two tracks are visible: the club (the stronger return) and the ball (the weaker).
   *(The club half is implemented: `l3_track_follow` carries the club track on. The ball half —
   using that to identify the ball — is this spec.)*

## Evidence the design must respect (2026-09-28, 93 captures with an OPS ball speed)

- Baseline: club track follows the club at impact on 55/93; ball launch within 15 % of OPS on
  15/93, no launch on 8/93; a ball-like chain (>=3 points at 0.7–1.1 x OPS) is present in the
  saved post-impact data on only 23/93.
- Using strength as a hard filter ("the ball is weaker than the club's claimed target") lowered
  the ball result to 8/93; excluding only the club's claimed target gave 11/93. Strength and the
  club's claim are **supporting evidence**, not gates.
- Doppler-continuity and range-rate/Doppler gates applied per point made the ball result worse at
  every setting tried (0–10/93). Doppler is **supporting evidence** at classification only.
- Every capture on disk has even frame spacing within the capture (2, 3, 4 or 6 ms by profile),
  but the timed dump format allows uneven spacing and per-frame rules mean different speeds per
  profile.

## Requirements

- **R1 Bounded ball hypotheses.** From the frame after arming, keep up to 4 candidate ball
  trajectories that start near the origin (1 bin short of it to 10 bins beyond). Each target is
  assigned to at most one hypothesis per frame; unassigned targets in the start band spawn new
  hypotheses into free slots, and a full set evicts only a hypothesis with a single point.
- **R2 Delayed classification.** No hypothesis becomes the ball until it holds 4 points. It must
  then: move outward at 10–100 m/s (fitted over its points, by timestamp); cross the origin within
  15 ms of the gate time (the gate is not the exact impact); and fit a line within 1 bin RMS.
  Among qualifying hypotheses the best score wins, where the score rewards a tight fit, Doppler
  agreeing with the fitted rate, and points weaker than the club's return that frame.
- **R3 Joint assignment with the club.** The ball tracker is told each frame which target the club
  track claimed (`l3_track_follow`'s `lastTargetIndex`). No hypothesis takes that target: an
  initially merged return, or a ball return the club claimed, is a missed frame for the
  hypothesis, not a point. Once the ball is confirmed, the ball track skips the club's claimed
  target while another candidate is in its gate.
- **R4 Missing returns.** A hypothesis coasts up to 2 frames without a point, its gate widening
  with elapsed time, then is dropped; it never jumps onto the club's return to fill a gap.
- **R5 Time-based prediction.** Hypothesis prediction, gates and fitted rates use timestamps, not
  frame counts. `l3_track_follow`'s speed cap uses the club's fitted rate in bins per second.
- **R6 Angles.** Each hypothesis point gets its angle estimate when it arrives (the board cannot
  go back to earlier frames), so the chosen ball keeps angles on its early points for the launch
  fit.
- **R7 Same code on the board and in the replay.** `l3_dump.c` and `firmware_replay.py` make the
  same calls in the same order; the ctypes mirrors match the C layouts, checked by a size test.
- **R8 Switchable, judged on data.** `useHypotheses` in the ball-track config selects the new
  path; its default is set by the evaluation. It becomes 1 only if, on the same 93 captures and
  the repo recordings: ball within 15 % of OPS rises above 15/93, "no launch" does not exceed
  13/93, club at impact stays at 55/93 or better, and every recording's manifest expectations
  hold.

## Out of scope

- The retained post-impact capture window (70/93 captures lack the ball in the saved data).
- Horizontal/vertical launch accuracy beyond keeping angles on the early points.
- Changing the club-track rules of 2026-09-28.

## Acceptance

`scripts/analysis/evaluate_iwr_tracking.py` over `iwr-test-sessions/` reports the R8 numbers,
and `--compare` against the recorded baseline fails on any regression; the 2026-08-24 12:04:08
capture, added to `tests/radar/recordings/`, launches at 40–50 m/s (OPS 45.1 m/s).

## Results (2026-09-28)

Measured with `scripts/analysis/evaluate_iwr_tracking.py iwr-test-sessions` (93 captures with an
OPS ball speed and a `triggerCfg` tee), after the plan's Tasks 0–7:

| search | club at impact | ball within 15 % | wrong | no launch | ball present |
|---|---|---|---|---|---|
| legacy (`--ball-hypotheses off`) | 55 | 15 | 70 | 8 | 23 |
| hypotheses (`--ball-hypotheses on`) | 55 | 10 | 36 | 47 | 23 |

Default: `useHypotheses = 0`, because R8 is not met (ball within 15 % fell from 15 to 10 and
"no launch" rose from 8 to 47).

What changed, capture by capture (legacy -> hypotheses): 36 wrong -> none (the search no longer
calls the club's follow-through the ball), 7 wrong -> ok (fast shots, OPS 34–52 m/s, e.g.
`190148_765_007` 28.4 -> 51.7 m/s against 51.4), 12 ok -> not ok. Of those 12, 9 have no ball in the
saved data (the legacy "ok" was the club moving at about the OPS speed; several are chips at
14–21 m/s where the search found a 44–60 m/s return instead). 10 captures hold a ball-like chain but
classify nothing.

Why, on the two captures traced (with the ball present):

- `20260927_144220_262_005` (OPS 20.6 m/s): two near-stationary returns (bins 46.2 and 50.9) are
  extended every frame, never coast out, and hold two of the four slots for eight frames; the
  departing hypothesis (48.0 -> 52.6) is evicted. A hypothesis whose fitted rate is already below
  `minDepartureMps` after three points could be dropped to free its slot.
- `20260927_183542_142_009` (OPS 36.5 m/s): the club track is inactive after impact (no claim to
  separate the returns), the recorded freeze is ~12 ms after impact, and the winning hypothesis
  (19.9 m/s, residual 0.51 bins) mixes two objects; a 28.9 m/s hypothesis also qualified.

Board: the whole tree compiles with the TI compiler (warnings as errors); the R4F link fails on a
DATA_RAM overflow that predates this work (776c266 plus the compile fix: 0x2b bytes free, the
0x800 FIQ stack cannot be placed). The hypotheses add 1432 B of DATA_RAM.

After the final review's fixes (a start band that moves out with the time since the gate; adoption
that bypasses the core's frame-counted gate; no hypothesis claims a target once the ball is
confirmed), the hypothesis search measures 55 / 11 ok / 34 wrong / 48 none / 23 present — still
short of R8, so `useHypotheses` stays 0.

## The Pi detector's rules as switches (2026-09-28)

The Pi's Python detector (`tracking.find_ball_from_power`, `shot.process_dump`,
`lcmf._tx2_horizontal_proxy`) never assigned club and ball per frame: it fitted one
range-vs-time line through a whole capture's worth of per-chirp peaks, preferred the fastest
credible line over the one with the most points, and read the horizontal angle along that line
late in the flight. Three of its rules are now firmware switches, all off by default until judged
on the 93 captures:

| rule | switch | replay / evaluator |
|---|---|---|
| Fastest credible: a hypothesis at or above the floor, with at least 0.55 of the most points any qualifying hypothesis holds, beats a slower best-scoring one; a slow winner waits while a fast hypothesis is still gathering points | `l3_ball_hyps_cfg_t.fastBallMps`, `fastSupportFraction` | `BallTuning.fast_ball_mps`, `--fast-ball <m/s>` or `--fast-ball club` (`shot.CLUB_MIN_BALL_MS` for the session's club) |
| Hard speed floor | `minDepartureMps` (both searches; existing) | `BallTuning.min_departure_mps`, `--min-departure-mps` |
| Far window: nothing short of origin + N bins is a hypothesis point | `l3_ball_hyps_cfg_t.farWindowBins` | `BallTuning.far_window_bins`, `--far-window-bins` |

On the repo recordings (`tests/radar/recordings/`): `fastBallMps = 30` changes nothing (no slow
competitor qualifies); `farWindowBins = 3` moves the 2026-08-24 launch from 47.1 to 44.1 m/s
(OPS 45.1) and 20260916_185013 from 35.1 to 39.2 m/s. With the hypotheses on, five 2026-09-16
recordings fail `ball_origin_bin` whatever the switches (already the case without them). A legacy
floor of 20 m/s turns 20260927_144341's 13 m/s hand return into a 3-point launch the launch fit
reads as 658.9 m/s: `l3_ball_track_launch` does not bound the 3D speed by `maxSpeedMps`.

Not ported: the late-flight horizontal launch. A 3D fit over the newest points was tried and fails
the angle-residual gate (`maxAngleResidualM`) on every recording: per-frame azimuths scatter by
10-30 degrees, where the Pi averaged TX2 phase coherently over every chirp of the last eight frames
(with an OPS-speed TDM correction and a 0.90 coherence gate). The board equivalent is a coherent
cross-frame average of the azimuth phase along the ball track, which needs HLA truth to judge.

To judge the switches:

    uv run python scripts/analysis/evaluate_iwr_tracking.py iwr-test-sessions --ball-hypotheses on \
        --fast-ball club --far-window-bins 3 --compare <baseline.json>
