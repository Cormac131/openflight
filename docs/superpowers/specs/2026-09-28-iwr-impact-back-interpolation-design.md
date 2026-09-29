# IWR6843 impact from the tracks either side of the tee band — design

Base: `feat/iwr-calcs`. Approved in conversation 2026-09-28; this document is
the written spec for review.

## Problem

Around the ball the MTI map carries a wide ridge (about 1.9–2.45 m on the
2026-09-28 capture, roughly 12 bins) that stays strong for the whole
capture. Impact cannot be detected reliably inside it: the range gate fires
on whatever enters the window around the ball, and the club track associates
to the ridge and sits in one bin (the "stuck track" charts). Outside the
band the returns are clean: the club approaching before it, the club and the
ball leaving after it.

## Goal

Establish impact from those clean tracks instead of from the band:

1. **Real time:** predict impact from the approaching club before it reaches
   the band, and fire on that prediction.
2. **After the capture:** refine the impact time from three independent
   estimates — club in, club out, ball out — each solved for the moment it
   passes the ball's range, with their agreement as the confidence.

Both halves run in the firmware (pure C, host-built and replayed through
`firmware_host` / `firmware_replay`), so the board's own tracks and result
packet carry them.

Out of scope: making `l3track` readback cell selection follow these tracks
(a follow-up once the tracks are trusted).

## Components and data flow

### 1. Tee band

A pure function giving `[ballBin − bandBins, ballBin + bandBins]` in global
bins, clipped to each frame's capture window. `bandBins` is configuration
(default 6, covering the ridge on the 2026-09-28 capture; tuned on the
replay set). Without a ball lock the configured tee stands in and the result
carries `no_lock`. A point exactly on the band edge is inside.

### 2. Club track kept out of the band

- Before impact, `l3_track_associate` (and acquisition) skip targets inside
  the band. The track coasts across the band on its prediction instead of
  associating to the ridge.
- After impact, `l3_track_follow` and the joint search's club and ball paths
  take only points outside the band.

### 3. Real-time prediction

`l3_impact` gains a range-only mode: fit the club-in points outside the band
(section "Track models"), solve for the time the line reaches the ball's
range, and fire when that time is within the existing horizon. It requires
at least 3 club-in points outside the band that pass the club-in speed
check. The existing closest-approach check and the range gate remain as
fallbacks; `impactSource` records which fired (new bit
`L3_SHOT_IMPACT_RANGE`).

### 4. `l3_impact_fit.c/.h` (new, pure C)

Input: three point lists (club-in, club-out, ball-out) as
`l3_point_at_fn` readers, the ball's range, the band and
`l3_impact_fit_cfg_t`. Output `l3_impact_fit_t`:

- per track: `timeUs`, `sigmaUs`, `points`, `speedMps`, `why`
  (`ok`, `missing`, `few_points`, `wrong_direction`, `speed_bounds`,
  `physics`, `nonfinite`, `dropped`);
- `impactUs` (fused), `spreadUs`, `verdict`
  (`consistent`, `single_track`, `inconsistent`, `none`), `droppedTrack`,
  `noLock`, `refinedMinusTriggerUs`.

### 5. Where it plugs in

Runs once at SOLVE. On a verdict other than `none` it replaces the frozen
`impactTimestampUs`; club speed at impact and smash are read at the refined
time. The whole `l3_impact_fit_t` goes into the result packet (`l3_result`)
and so to the Pi and the dump viewer.

Decision (final review, 2026-09-29): club speed and smash are **not** re-read
at the refined time — the club delivery fit already ends at the band edge (at
impact), so its speed is the speed arriving at the ball and re-reading it
would change nothing but the code path. An `inconsistent` verdict instead sets
the result's `impact_uncertain` quality bit (`L3_QUALITY_IMPACT_UNCERTAIN`),
which is how "shot metrics low-confidence" below is carried.

## Track models and the per-track solve

- **Model:** range against time, straight line, least squares over the K
  points nearest the band — club-in: its last K before entering; club-out and
  ball-out: their first K after leaving. `K = 4` (about 12 ms at 3 ms frames);
  minimum 3.
- **Crossing:** `t_k = t̄ + (r_ball − r(t̄)) / v`.
- **Uncertainty:** `σ_k` = the fit's prediction standard error at the
  crossing divided by `|v|`, with a floor of `binWidth / √12 / |v|`. It grows
  with extrapolation distance by construction.
- **Physics checks** (failure drops the track with its `why`):
  - club-in toward the ball (rising bin), 10–70 m/s;
  - club-out away from the ball, no faster than club-in × 1.10;
  - ball-out away from the ball, 15–90 m/s, faster than club-out
    (smash > 1).
- **Fusion:** inverse-variance weighted mean. An estimate agrees when
  `|t_k − t̂| ≤ 3 · max(σ_k, 0.5 ms)`. Three tracks with one disagreeing: drop
  it and fuse the remaining two, verdict `consistent` with `droppedTrack`. Two tracks
  disagreeing: `inconsistent`.
- All thresholds live in `l3_impact_fit_cfg_t` with these defaults.

## Failure modes

| Situation | Behaviour | Verdict / flag |
|---|---|---|
| No ball lock | Band around the configured tee; all computed | `noLock` |
| Club-in missing | Solve from club-out and/or ball-out | per-track `missing` |
| Ball-out missing | Club-in + club-out | per-track `missing` |
| One usable track | That track's time and σ | `single_track` |
| Two disagree | Smaller-σ track's time; shot metrics low-confidence | `inconsistent` |
| Three, one outlier | Drop it, fuse the other two | `consistent`, `droppedTrack` |
| No usable track | Frozen gate/geometric time kept | `none` |
| Non-finite fit | Track rejected, counter incremented | per-track `nonfinite` |

- Real time: when the club reaches the band without the range-only
  prediction firing, the range gate still fires.
- `refinedMinusTriggerUs` is always reported; a large value fails nothing
  and is the direct measure of trigger lateness.
- Every fit uses each point's own timestamp (ball frames can be strided).

## Testing

TDD: each behaviour's test is written and seen failing first.

- **Unit (C via `firmware_host`, pytest):** synthetic scenes
  (`tests/iwr6843_twotrack.py`) — exact crossing on clean lines and under
  noise; every failure-mode row; each physics check; the 3σ gate and its
  0.5 ms floor; σ growing with extrapolation; strided timestamps; NaN input.
- **Band regressions:** a moving club beside a strong static ridge inside
  the band — the track coasts across and never takes a band point; the
  range-only prediction fires at the expected frame on a clean approach and
  does not fire with fewer than 3 outside points.
- **Replay and viewer:** `firmware_replay` reports the three estimates,
  fused time, verdict and `refinedMinusTriggerUs`; the dump viewer draws the
  band, the three fitted lines extended to the ball's range and the impact
  time.

## Method gate: A vs C

`scripts/analysis/evaluate_iwr_tracking.py` gains an impact section:
availability of each estimate, the `t_ci`/`t_co`/`t_bo` spread, verdict
rates and the `refinedMinusTriggerUs` distribution. Method C (one joint
piecewise fit sharing `t_impact`: grid search on `t_impact`, least squares
for the rest) is implemented in Python in that script only, as a
comparator. C replaces A only if it cuts the median spread by ≥ 30 % without
raising the `none` or `inconsistent` rate.

Data: the 7 committed recordings, the user's session folders, and 10–20 new
`--iwr6843-full-capture` captures on the current setup (with the ridge band).

## Acceptance

- No pre-impact club point inside the band on any capture.
- On captures with a visible swing: at least one estimate on ≥ 90 %,
  `consistent` on ≥ 70 %.
- Median spread ≤ 1.5 ms when `consistent`.
- No regression against the existing `evaluate_iwr_tracking.py --compare`
  baseline.
