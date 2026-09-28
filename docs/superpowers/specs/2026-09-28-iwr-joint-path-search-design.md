# IWR6843 joint club/ball path search after impact — design

Base: `feat/iwr-calcs` at `53bbdf29`. Supersedes the ball-search half of
`2026-09-28-iwr-joint-club-ball-tracking.md` (its hypothesis search stays off and is replaced here).
Input: an external review pasted on 2026-09-28 and the design agreed section by section the same day.

## Problem

On 2026-08-09 11:21:07 shot 4 (OPS 112.1 mph = 50.1 m/s) the ball's first five points (12–24 ms,
bins 34.3 → 47.6) are club returns: their aliased Doppler is about +1.9 (the club's 38 m/s), while
the ball ridge from 24 ms reads about −3.8 (50 m/s). The club track was coasting at 12–15 ms, so it
claimed nothing and the ball took the club's returns. The legacy tracker launches at 54.3 m/s
(residual 0.64 m); the hypothesis search confirms a candidate made of three club points and one ball
point. The weak ridge itself is detected on every frame (at most 7 targets per frame, cap 8).

Today's structure causes this: the club claims first, per-frame choices are final, the ball is
chosen once and handed to the old tracker, and nothing checks that a track's Doppler stays in one
family.

## Decisions (from the user)

- **Approach B:** a best-path search over a window, jointly for club and ball, that can revise
  earlier picks. Implemented incrementally as a bounded set of joint explanations (below).
- **RAM net-neutral:** the new module replaces `l3_ball_track.c` and `l3_ball_hyp.c`
  (`l3_ball_track_t`, 3760 B including the 1276 B hypothesis set) and must fit in what they free.
  The pre-existing DATA_RAM overflow is out of scope and reported, not fixed.
- The pre-impact club track (`l3_club_track.c`, 2156 B) and the user's rules (ascending bins, at
  most two consecutive points in one bin) are unchanged. The new search owns both club and ball
  from the gate; the post-impact use of `l3_track_follow` goes.

## Design

### Joint explanations

A new pure-C module `firmware/iwr6843/l3_joint_search.c/.h` keeps up to **16** joint explanations
(`L3_JOINT_BEAM`). Each holds, for the current frame:

- the club's choice: a target index or *missing*;
- the ball's choice: a target index or *missing*, never the club's target;
- a parent link to an explanation in the previous frame;
- frames since each path's last real point, and whether the ball path has started or ended;
- the running score.

Each frame every explanation is extended by every allowed (club, ball) pairing of the frame's
targets plus *missing*, and the best 16 survive. The best explanation at frame N may use different
points at N−5 than the best explanation at N−5 did, so earlier picks are revised.

At the gate every explanation starts from the pre-impact club track's last point and fitted speed;
the ball path is empty. If the club track is inactive at the gate, the club path may start like the
ball, from a target in the start band.

### Window and write-out

The module stores the last **8** frames (`L3_JOINT_WINDOW`): explanation nodes (16 × 8 × ~12 B) and
the frame's targets (8 × 8 × ~20 B: range bin, aliased Doppler, strength, azimuth, elevation,
angle flags) plus each frame's timestamp. When every surviving explanation agrees on a frame's club
and ball choices, that frame is written out to the finished club and ball point lists (ball list
sized for the launch fit). When a frame must leave the window while explanations still disagree,
the best explanation's choice is written out, disagreeing explanations are dropped, and
`forcedWriteOuts` is incremented.

Total state must be ≤ 3760 B; a size test pins it.

### Angles

Each frame, after pruning, angles are estimated only for targets that some surviving explanation
uses. A dropped explanation cannot return, so a target without angles never appears on a final
path. At most 8 estimates per frame.

### Scoring

The score is a sum over frames and over the two paths:

- a point that fits the path's motion earns a reward that shrinks with its misfit;
- a missed frame costs a fixed penalty (`missCost`);
- an unassigned target costs nothing;
- a frame before the ball path has started is not a miss.

**Speed from Doppler.** A target's aliased Doppler becomes a speed by choosing the alias nearest
the path's predicted speed. The alias span comes from the capture's loop period. A ball path's
first point has no speed; its second point chooses the alias nearest the range rate between the
two points. The club path starts with the pre-impact club track's fitted speed.

**Fit of a new point**, from the path's last real point, timestamps only (wrap-safe differences):

- predicted range = last range + ½(old speed + new speed) × Δt;
- range misfit = (measured − predicted) / σ, σ = `rangeSigmaBins` (≈1 bin) widened per coasted
  frame by `coastSigmaGrowBins`;
- acceleration = (new speed − old speed) / Δt, judged against the path's allowed band: the ball's
  is narrow around zero plus a geometry allowance (`ballAccelMps2`), since sideways motion changes
  radial speed at constant true speed; the club's allows hard slowing after impact and mild
  speeding up (`clubDecelMps2`, `clubAccelMps2`).

**Every term is capped** (`termCap`), so one bad Doppler or range value cannot veto a path. The
only hard gate is range, at 3σ plus coasting growth, to bound CPU.

**Path rules.**

- Ball: starts only from a target in the start band (origin −1 to +10 bins, plus
  `maxSpeed / binWidth × (t − gate)`); once it has a speed, outward at 10–100 m/s; ends after 2
  missed frames (the explanation continues with no ball).
- Club: outward at 0–70 m/s, may slow to near-stationary; coasts up to 3 frames.
- Club and ball never share a target. A merged return means the ball has no point that frame.

**Strength** breaks near-ties only: a small bonus (`clubStrongerBonus`) when the club's point in a
frame is stronger than the ball's. Nothing else uses strength, so scaling the club's returns up
cannot flip identities.

All weights and limits live in one config struct (`l3_joint_cfg_t`), mirrored in
`firmware_host.py`, set once from the recordings.

### Confirmation

The ball is confirmed when all three hold:

1. the best explanation's ball path has ≥ 4 points;
2. it passes today's classification: fitted outward speed 10–100 m/s, origin crossing within
   15 ms of the gate, fit residual ≤ 1 bin RMS;
3. its score beats the best competitor (the best explanation whose first 4 ball points differ, or
   that has no ball) by `confirmMargin`.

After confirmation, explanations disagreeing on those first 4 ball points are dropped. Later ball
points may still be revised.

### Consumers

- Shot machine: the same "ball confirmed" event as today.
- Launch fit: re-run each frame on the best explanation's ball points that have angles; the board
  reports it at the end as now.
- Club speed and path: unchanged, fixed at impact from the pre-impact points.
- Retention: each frame places its window around the current best explanation's club and ball
  points; that frame's decision stands if later revised.
- `l3_dump.c` (board) and `firmware_replay.py` (host) make the same calls in the same order.

### Viewer

New per-frame replay fields, shown in the dump viewer:

- each path's point in the best explanation at that frame, and its predicted range;
- rejected targets, labelled *outside gate* or *scored lower*;
- the runner-up's ball path (dotted).

The range × time map draws the final, revised paths; the frame inspector shows the choice made at
that frame.

### Edge cases

| Situation | Behaviour |
|---|---|
| No club track at the gate | Club path may start from the start band; speed and the strength tie-break separate club and ball |
| Frame with no targets | Every path misses |
| Δt ≤ 0 or timer wrap | Wrap-safe differences; Δt ≤ 0 skips extension, `skippedFrames` counted |
| Oldest frame still disputed | Best explanation's choice written out, others dropped, `forcedWriteOuts` counted |
| Tied scores | Fixed order: club target, ball target, parent index |
| Non-finite score | Explanation dropped, `droppedNonFinite` counted |
| Re-arm | Full reset |

Counters (confirmed, forced write-outs, skipped frames, non-finite drops, pairings evaluated, angle
estimates) are exported to the replay and viewer like `track_counters`.

## Testing

**Unit (host-built C via ctypes, TDD):** alias choice (nearest prediction; ball second point from
range rate; span from loop period); fit (average-speed prediction, coasting widening, uneven
spacing, timer wrap); capping (a path with one bad point beats a path that misses that frame);
extend/prune (exactly 16 kept, deterministic ties, no shared target, ball start band, ball ends
after 2 misses); write-out (agreement and forced); confirmation (4 points, classification, margin,
post-confirmation drop); struct sizes match mirrors and total ≤ 3760 B; per-frame bounds
≤ 1500 pairings and ≤ 8 angle estimates.

**Synthetic scenes** (`tests/iwr6843_twotrack.py`): weak ball beside strong club; ball missing 1–2
frames; merged return at impact; club strength ×1/×4/×16 gives identical identities; no club track
at the gate; gate 12 ms after impact; stationary return in the start band never becomes the ball;
aliased Doppler on both paths; 2, 3 and 6 ms frame spacing.

**Recordings** (`tests/radar/recordings/`, manifest expectations):

- add `iwr6843_20260809_112107_663_004.l3dump`: no ball point before the split lies on the club
  ridge; every ball point's speed 40–60 m/s; launch within 15 % of 50.1 m/s;
- `iwr6843_20260824_120408_601_001.l3dump`: launch 40–50 m/s; club continues 37.8 → ≥ 43;
- `iwr6843_20260927_144341_257_013.l3dump`: club last bin 37–41;
- each replayed again with the final club path's range bins amplified ×4 in the IQ cube (a test
  helper, not a production switch): identities unchanged.

**Board:** TI build in `openflight-iwr-sdk:latest` compiles with warnings as errors; the link map
shows DATA_RAM no larger than at `53bbdf29`.

## Acceptance

`scripts/analysis/evaluate_iwr_tracking.py iwr-test-sessions` must show:

- ball within 15 % of OPS > 15/93;
- no launch ≤ 13/93;
- club at impact ≥ 55/93;
- on the 23 captures with a ball-like chain present: within 15 % on ≥ 17/23;
- on the 70 without: no more wrong launches than the legacy tracker's count on those 70;
- every recording's manifest expectations hold.

The evaluation script gains the two ball-present splits.

## Rollout

The board cannot hold both trackers, so the old `l3_ball_track.c`/`l3_ball_hyp.c` are deleted only
in the plan's last task, and only if Acceptance is met; `l3_dump.c` switches to the new search in
that same task. Until then the replay runs the new search alongside the old one (host only, selected
by a `ReplayConfig` field and the viewer's ball-search select). If Acceptance is not met, work stops with the numbers reported
before anything is deleted.

## Out of scope

- The pre-existing DATA_RAM overflow.
- The retained post-impact capture window (70/93 captures lack the ball in the saved data).
- The pre-impact club-track rules.
- Measuring R4F CPU time on hardware (bounded here by counted pairings and angle estimates).
