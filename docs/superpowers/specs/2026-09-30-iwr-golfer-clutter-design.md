# IWR6843 golfer-clutter robustness: design and first results

Base: `feat/iwr-calcs` (9b7cdea). Branch: `feat/iwr-golfer-clutter`.

## Problem

The golfer stands beside the ball and is a strong, moving return: on the
range-time map a hotspot a few bins short of the ball (bin 44 against a ball
at 49 on the 2026-09-16 recordings) that stays through the capture. The club
runs into it before impact, and the ball and club leave it afterwards. The
firmware's club track picks the most confident target near its prediction
(`l3_track_associate` weights `1 - confidence` and `1/snr`) and, after
impact, the strongest target in its follow window. So a hotspot brighter than
the club wins by amplitude.

The requirement is not to erase the hotspot at the expense of the club and
ball data. It is to stop the hotspot dominating detection and track
association.

## Scope delivered

Everything is host-side and offline, over the committed recordings and
synthetic dumps. The firmware C and the board are unchanged. Every stage
is scored by the same benchmark on the same dumps.

| Plan phase | Module | What it does |
|---|---|---|
| 0 Benchmark | `clutter_bench.py` | Per capture: hotspot dB (recorded and seen), club points before, in and after the hotspot, longest runs, first ball frame, false detections, trigger, SCR, bridged speeds, label coverage and score. `acceptance()` states the Phase 1 bar. |
| 1 Clutter map | `clutter_map.py`, `ReplayConfig.clutter` | `C(r)` of the MTI residual power per global bin, median of the last 16 learning frames or EMA (α = 1/16); `stat' = max(stat − β C, 0)` applied as a per-bin amplitude gain, so every stage sees it with phases untouched. |
| 1.3 Freeze | `ClutterPhaseMachine` | IDLE → BACKGROUND_LEARNING → CLUB_APPROACH_DETECTED → PRE_IMPACT_TRACKING → IMPACT_WINDOW → POST_IMPACT_TRACKING → SHOT_COMPLETE → BACKGROUND_LEARNING. Learning stops on a *plausible* approach: 3 rising club points at 8-70 m/s. It resumes after the shot, or after 8 club-less pre-impact frames (a waggle). |
| 2 Kinematic association | `association.py` | Constant-acceleration `TrackState`. Score = 0.30 range + 0.25 velocity (implied rate and aliased Doppler) + 0.15 acceleration + 0.15 angle + 0.10 history + 0.05 power. |
| 3 Clutter probability | `clutter_probability` | A logistic of: golfer region, closeness to the learned background, slowness and persistence, less the club prediction's agreement. Soft: scales the score by `1 − 0.8 P`. |
| 4 Range-angle map, FOV | `RangeAngleClutterMap`, `fov_weight` | `C(r, θ)` from each bin's Bartlett spectrum plus the power-weighted TX1 azimuth, which gives the golfer's learned direction. Soft field-of-view weights floored at 0.3. |
| 5 Shot-state FOV | `fov_weight`, `HostTracker` | Waiting: broad, golfer down-weighted. Club acquired: around the prediction. Impact window and after: club continuation, or anywhere a ball leaving the tee at impact could be. Track A is the club continued or re-acquired slower than the ball; track B is a new object confirmed by 3 consistent points faster than the club. |
| 6 Capon/MVDR | `beamforming.py` | Per-loop elevation snapshots mirroring `l3_angle_estimate` (parity-tested against the compiled C); Bartlett and loaded, forward-backward-averaged Capon; MAC costs. |
| 7, 8 Rig experiments | `rig_experiments.py` | Rig manifest → the orientation matrix and best orientation, and the A/B/C enclosure verdict (≥ 3 dB over A = multipath). |
| 9-11 Front, radome, hood | `radome.py`, `cad/iwr6843-rf-front/` | Slab transmission (TE/TM, angle, loss) over 60-64 GHz and ±30°, best printable thickness, standoff candidates, hood depth for a golfer-side cutoff. Parametric OpenSCAD front and yaw/pitch carrier. |
| 12 Impact bridge | `impact_bridge.py` | The club's last clean points and the ball's first ones solved for `r_club(t_i) = r_ball(t_i)`, speeds extrapolated to `t_i`. It falls back to the ball-range-anchored joint fit (`impact_eval.joint_fit_impact`). |

`scripts/analysis/evaluate_iwr_clutter.py` has five subcommands (`bench`,
`host`, `beam`, `rig`, `radome`).

## Design decisions

- **The map is of power, not complex samples.** The plan proposed
  `X' = X − β C(r, a)` on complex samples. Every consumer reads the burst-MTI
  residual (samples less their mean over the frame's loops), and a complex
  background constant over a frame is removed by it already. So the complex
  subtraction is a no-op; a test proves the observations are identical.
  What survives the MTI is the golfer's *moving* return, whose phase does not
  repeat frame to frame, so its residual power is what the map learns.
- **Freeze on a plausible approach, not on an active track.** On 38 of the
  41 recordings the firmware club track appends its first point on frame 0
  or 1. On most of those the point is a standing return near the ball
  (bins 35-47), not the club, which is still around bin 21. "Freeze when the
  track is active" froze the map before it had learned anything. The approach test (3 rising points at a club's speed) is what
  the plan means by "a plausible club approach".
- **Median over EMA.** The recordings hold only 9-14 pre-impact frames, and
  the club is already moving in most of them. A per-bin median rejects the
  club's 1-2 frame passage through a bin; an EMA absorbs part of it.
- **Linear impact fits.** A quadratic club fit extrapolated 3-12 ms read
  −62 to 148 m/s on the recordings. Linear is the default; `club_degree=2`
  falls back to linear whenever its impact speed is out of bounds.
- **Split Doppler tolerance.** Association uses σ = 4.5 m/s on the aliased
  readout. The clutter probability's "agrees with the club" relief uses
  2.0 m/s, so a standing return at the club's range is not excused as the
  club unless it also moves like it. With one σ for both, 2.5 m/s helped the
  club and cost the ball 0.09 of label score.

## Results (committed recordings, 2026-09-30)

41 captures, 34 with reviewed hand labels. No tuning beyond what is stated
above. Label numbers are means over labelled objects (`label_scoring`).

### Phase 1: clutter map in the firmware replay (`bench`)

| Run | With launch | Ball label coverage | Ball label score | Club label score | Club points pre | Hotspot seen (median dB) |
|---|---|---|---|---|---|---|
| β = 0 (reference) | 38/41 | 0.621 | 0.590 | 0.243 | 238 | 79.7 |
| median β = 0.5 | 41/41 | 0.765 | 0.729 | 0.243 | 246 | 78.4 |
| median β = 0.8 | 41/41 | 0.791 | 0.752 | 0.227 | 247 | 78.2 |
| median β = 1.0 | 41/41 | 0.814 | 0.774 | 0.216 | 246 | 77.4 |
| EMA β = 0.8 | 40/41 | 0.793 | 0.755 | 0.245 | 248 | 78.4 |

Acceptance: **fails as written** at every β. The hotspot the trackers see
drops by only 0.5-1.8 dB (median) against the 3 dB bar, because the map
learns from so few frames. Per capture, 1-5 captures (median map) or 3-8
(EMA) fail a line, almost all by losing one pre-impact club point and one
(2026-09-16 005) by seeing the ball two frames later. In return, the ball
tracker recovers a launch on every capture (40/41 for EMA at β 0.8 and 0.9)
and ball label coverage rises by 0.14-0.19. Median β = 0.5 comes closest to
the bar: one capture loses one club point and the club label score is
unchanged. A
recordings regression test (`test_the_clutter_map_recovers_more_balls_on_the_recordings`)
holds the β = 0.8 result.

### Phases 2-5: host kinematic tracker (`host`, β = 0)

| Tracker | Club coverage | Club score | Ball coverage | Ball score | False points |
|---|---|---|---|---|---|
| Firmware replay | 0.403 | 0.243 | 0.621 | 0.590 | |
| Host, default weights | 0.527 | 0.281 | 0.692 | 0.648 | 186 |
| Host, power-dominated (0.8 power) | 0.559 | 0.200 | 0.636 | 0.590 | 284 |
| Host, no clutter probability | 0.538 | 0.257 | 0.718 | 0.672 | 218 |
| Host, no field of view | 0.523 | 0.272 | 0.686 | 0.645 | 193 |

Making power weak evidence is what helps. With power-dominated weights the
tracker covers more club frames but on the wrong returns: 98 more false
points and the lowest club score. The clutter probability trades ball
coverage for 32 fewer false points. The field of view adds a little to both.
Subtracting the clutter map as well (β = 0.8) helps the host tracker's ball
(score 0.655) but costs its club (0.236), so the host tracker runs best on
the raw frames.

Weight sweep (`--sweep-weights`, 27 sets over range 0.2/0.3/0.4, velocity
0.15/0.25/0.35 and power 0.05/0.2/0.5, the rest in the default proportions).
All four best sets keep power at 0.05. The best, range 0.20, velocity 0.15,
acceleration 0.225, angle 0.225, history 0.15, power 0.05, scores club 0.294
and ball 0.699 against the defaults' 0.281 and 0.648. That is within reach of
label noise on 34 dumps, so the defaults stay at the plan's starting weights
until more labelled captures confirm the sweep.

### Phase 6: Bartlett vs Capon (`beam --tracking`)

Rejection of the golfer's learned direction at the firmware club points
(197 points) is 21.6 dB median for Bartlett and 2.4 dB for Capon. This
measure is biased towards Bartlett: at most club points the golfer carries
little power in the club's bin, so MVDR has nothing to null, while
Bartlett's fixed beam always "rejects" a direction outside its main lobe.
The measure that decides is tracking. With Capon angles the host tracker's
label scores are club 0.282 (Bartlett 0.281) and ball 0.656 (0.648), a tie
within label noise. It costs about 11.6 k complex MACs per detection against
11.1 k for Bartlett over the same snapshots, and 1.3 k for the firmware's
coherent single-snapshot Bartlett. Not worth porting on this evidence.
(An earlier tracker version showed club 0.262 → 0.295 with Capon; that gain
did not survive the final tracker.)

### Phase 12: impact bridged across the hotspot

On the host tracker's tracks the bridged club speeds fall within 24-47 m/s
on the 2026-08-24 TrackMan session. On the firmware's tracks they are often
near 0, because its pre-impact points include standing hand returns. The
benchmark reports `bridged_plausible` per capture.

## Not done, and why

- **No firmware port.** Phases 1-6 and 12 run on the host replay only. The
  plan asks for the offline proof first; the C port and DATA_RAM checks
  follow once the numbers justify them.
- **Hardware phases 7-11 are tooling only.** The carrier and front are
  designed and render, but nothing was printed, captured or measured. The
  material permittivities are nominal.
- **Label tolerance.** The labels are one person's marks on the range-time
  map. With 34 labelled dumps, differences under about 0.03 of score are
  within noise.
