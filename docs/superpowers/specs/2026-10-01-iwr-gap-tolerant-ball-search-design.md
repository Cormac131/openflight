# IWR6843 gap-tolerant, kinematics-driven ball search — spec

Base: `feat/iwr-calcs` at `62ecedb4`. Direction: a proposal the user pasted on 2026-10-01
("make the tracker gap-tolerant and kinematics-driven"); design agreed in conversation the same
day. Builds on `2026-09-28-iwr-joint-club-ball-tracking.md` (the hypothesis search, R1–R8).

## Problem

At a 3 ms frame period a 116 mph (51.9 m/s) ball moves 15.6 cm, ~3.3 bins (4.7 cm), per frame.
Around impact the club, the golfer and the tee band produce strong returns and the ball is often
missing for several frames. The hypothesis search (`l3_ball_hyp.c`) exists but is off
(`useHypotheses = 0`): on 93 captures it cut "wrong" from 70 to 34 but raised "no launch" from 8 to
47. The two traced failures are what this spec targets:

- `20260927_144220_262_005`: two near-stationary returns hold two of four slots for eight frames
  and evict the departing ball. Cause: the spawn band
  `[origin − 1 bin, origin + 10 bins + v_max·Δt]` (`l3_ball_hyp.c:284`) has no lower edge, and a
  hypothesis whose rate fell to ~0 is never dropped.
- `20260927_183542_142_009`: the winning hypothesis mixes two objects (19.9 m/s; OPS 36.5 m/s).
  Nothing rejects a track whose speed changes by more than drag allows.

Three further gaps against the proposal:

- Coasting is `maxMisses = 2` frames everywhere; the ball's impact-region gap is longer.
- With a valid tee band the tracker is armed at the band's **far edge** (`l3_ballArmBin`,
  `l3_dump.c:3516`): one number serves as both where the ball was at impact and where points may
  start. Back-projection needs the former.
- No backward pass: points the forward search missed are never recovered once the ball is known.

The evaluator's own label shares the forward assumption: `ball_present`
(`scripts/analysis/evaluate_iwr_tracking.py:104`) needs targets in **consecutive frames**, so a
ball with an impact-region gap is labelled absent.

## Evidence the design must respect

From the 2026-09-28 spec, still binding:

- Per-point Doppler-continuity and range-rate/Doppler gates made the ball result worse at every
  setting tried. **Doppler is supporting evidence, never a per-point gate.**
- Strength as a hard filter made it worse. Strength is supporting evidence only.
- Frame spacing differs by profile (2, 3, 4, 6 ms); the dump format allows uneven spacing.

New in this spec:

- A drag-only ball loses ≤ ~1 m/s over 50 ms and departs from a straight line by ~2–3 cm (under
  half a bin), so a **linear** range-vs-time fit is kept; a free acceleration term would mostly
  give a two-object mix room to fit.
- Only 23/93 captures hold a ball-like chain under the strict label; weights are not tuned on so
  few.

## Requirements

### Forward search (`l3_ball_hyp.c`)

- **G1 Corridor gate (switchable).** With `corridorGate` on, a target may start or extend a
  hypothesis only if its implied launch velocity
  `v_i = (r_i − r_anchor) / (t_i − t_anchor)` lies in
  `[minDepartureMps − ε_i, maxSpeedMps + ε_i]`, `ε_i = (anchorRangeTolM + maxSpeedMps·anchorTolS) / Δt_i`.
  Skipped while `Δt_i` is under `framePeriodUs`. This replaces the spawn band's far edge
  (`spawnBeyondBins` is removed). Off: today's spawn band with its far edge kept, so the gate's
  effect is measured alone.
- **G2 Stalled hypotheses drop.** A hypothesis with ≥ 3 points whose fitted rate is under
  `minDepartureMps` is dropped and its slot freed (counted in `dropped`).
- **G3 Impact-region coast.** A hypothesis whose newest point is short of
  `anchor + farWindowM` may coast `impactCoastUs` (default 18 000 µs); beyond it, `coastUs`
  (default 6 000 µs, today's two frames at 3 ms). Both replace the frame-counted `maxMisses`. The
  association gate keeps widening by `gateMps · Δt`.
- **G4 Deceleration reject.** At classification (≥ `classifyPoints`, so ≥ 4 points), fit the
  older and newer halves (≥ 2 points each; the odd point goes to the newer half); if the rate
  drops by more than `maxDecelMps2 · Δt_mid + 2·σ_Δ` the hypothesis does not qualify. `Δt_mid` is
  the time between the halves' mean times; `σ_Δ` combines each half's rate uncertainty from a
  fixed range noise of `maxResidualBins · binWidthM` (not the half's own residual, which is zero
  for two points). With few points σ_Δ is large and the test is weak by construction; it bites on
  longer tracks. 0 disables.
- **G5 Metric configuration.** `gateBins → gateM`, `spawnBehindBins → spawnBehindM`,
  `farWindowBins → farWindowM`, `maxMisses → coastUs`; converted to bins once in
  `l3_ball_hyps_init` from `binWidthM`. Points stay in bins internally (targets arrive in bins).
  No setting assumes a frame period.

### Anchor and score

- **A1 Anchor apart from acceptance.** `l3_ball_hyps_arm` / `l3_ball_track_arm` take an anchor
  (`anchorBin`, `anchorUs`, `anchorTolUs`) and `acceptFromBin`. The anchor is the tee's sub-bin
  range; `acceptFromBin` is the band's far edge when valid, else `anchorBin + farWindowM`
  (whichever is further). No point short of `acceptFromBin` joins a hypothesis or is recovered.
  The ball track's launch origin is the anchor.
- **A2 Club-predicted impact time.** At arm, `l3_impact_fit_track(L3_FIT_CLUB_IN)` over the club
  track's newest points against the tee range. `why == OK` and `sigmaUs ≤ anchorMaxSigmaUs`
  (default 3 000): `anchorUs` is its time, `anchorTolUs = max(3σ, 2 000)`. Otherwise the gate /
  range-crossing time as today with `anchorTolUs = impactToleranceUs` (15 000). The verdict
  records `anchorSource` (club fit / gate) and `anchorSigmaUs`.
- **A3 Track score.** Hard gates first (speed bounds, origin within `anchorTolUs`, residual,
  G4). Then
  `S = wBack·S_back + wVel·S_vel + wResid·S_resid + wDoppler·S_doppler + wCoherence·S_coherence + wWeaker·S_weaker`,
  defaults 3, 2, 1, 1, 0.5, 0.5, each term in 0..1:
  `S_back = 1 − |originOffsetUs| / anchorTolUs`;
  `S_vel = 1 − min(1, std(v_i) / v_fit)` over the points' implied velocities;
  `S_resid = 1 − residual / maxResidualBins`;
  `S_doppler`, `S_weaker` as today; `S_coherence` = mean point `coherence` (new point field).
  Raw power is not a term. Weights are configuration, not tuned on the captures.
- **A4 Fastest credible** (`fastBallMps`) unchanged and off.

### Backward recovery (`l3_ball_recover.c`, new)

- **B1 History.** `l3_ball_history_t`: `L3_BALL_HISTORY_FRAMES = 24` frames × up to
  `L3_BALL_HISTORY_TARGETS = 6` targets (`rangeBin`, `dopplerAliasMps`, `stat`, `coherence`, a
  club-claimed flag), plus each frame's number and timestamp; a ring, oldest overwritten. Filled
  every post-impact frame from arming with the same targets the hypotheses see (after the band and
  clutter filters). `historySnr` selects the extraction threshold; default equal to the ball
  track's `snr` (no extra extraction). Build switch `L3_BALL_RECOVER` (host 1, board 0).
- **B2 One pass at adoption.** When `l3_ball_track_adopt` takes the winning hypothesis: fit its
  line; for every history frame from arming to its newest point without a hypothesis point, take
  the candidates within `recoverGateM` (default 0.6 bin, ≈ 2.8 cm) of the line, beyond
  `acceptFromBin`, not club-claimed. Several: the nearest; equal distance (within 0.1 bin): the one
  whose Doppler agrees with the rate (**tie-break only**, per the evidence above). Merge in time
  order, refit; keep the merge only if the residual stays within `maxResidualBins`, otherwise
  adopt the hypothesis points alone. Seed the core in time order as today. Recovered points carry
  no angles (`anglesValid = 0`).
- **B3 Visibility.** The verdict gains `recovered`; `l3_ball_track_format_status` prints it; the
  replay output marks recovered points so the viewer can draw them apart.

### Evaluation (`scripts/analysis/evaluate_iwr_tracking.py`)

- **E1 Label first.** `ball_present` becomes gap-tolerant (frames up to `impactCoastUs` apart
  join a chain) and its chain must back-project to the tee within the anchor tolerance. The old
  definition stays as `ball_present_strict`. Hand labels (`2026-09-29-track-labels-design.md`)
  override the heuristic where a capture has them. Measured and committed, with a re-run legacy
  baseline, **before** any tracker change.
- **E2 Split acceptance (replaces R8 for `useHypotheses`).** On the same 93 captures and the repo
  recordings, against legacy re-run under E1:
  ball-present `ok` (within 15 % of OPS) higher than legacy's;
  ball-absent `none` higher and `wrong` lower than legacy's;
  club at impact ≥ 55/93;
  every repo recording's manifest expectation holds.
  `summarize` / `--compare` report the split.
- **E3 Net diagnostic.** Optional `--net-range-m`: for each `ok` capture, whether the confirmed
  line reaches that range within ±2 frame periods of the time its speed predicts. A report column
  only; no firmware code knows the net.
- **E4 Ablation.** `BallTuning` and the CLI expose `--corridor-gate on|off`,
  `--impact-coast-ms`, `--max-decel`, `--recover on|off`, `--recover-gate-m`, `--history-snr`;
  the Results section reports each change's effect alone and together.

### Parity and rollout

- **P1 Same code on board and replay (R7).** New config fields and structs get ctypes mirrors in
  `firmware_host.py` with size tests; `firmware_replay.py` makes the history and recovery calls
  in the same order as `l3_dump.c`.
- **P2 Defaults.** `useHypotheses` stays 0 until E2 passes. The board keeps
  `L3_BALL_HYPOTHESES = 0` and `L3_BALL_RECOVER = 0`; the DATA_RAM added by each is measured and
  recorded here. Fitting them on the board is a separate decision.

## Tests (written first, through `firmware_host`, in the existing ball-track test style)

Forward: a stationary pair cannot start a hypothesis once Δt ≥ one frame (144220 regression); a
hypothesis decelerating to 0 drops at 3 points; a ball absent 5 frames inside the far window
survives, the same gap beyond it drops; the same metric scene gives the same verdict at 2 ms and
3 ms periods; a 35 → 20 m/s two-object mix fails G4; implied velocities just inside and just
outside each corridor edge; the timestamp wrap across 2³² µs; `corridorGate` off reproduces
today's spawn behaviour.

Anchor and score: a clean club approach anchors from the club fit; a noisy or two-point club
falls back to the gate; with a band the anchor is the tee and points short of the far edge are
refused; an origin crossing 12 ms off loses to one within 1 ms despite a better residual; each
score term at its 0 and 1 edges.

Recovery: the ring wraps; club-flagged targets are never recovered; frames 2, 3 and 5 missing
from a synthetic ball but present in the history are recovered in order; a nearer decoy wins over
a farther agreeing one, an equal-distance decoy loses on Doppler; a merge that breaks the residual
is undone whole; nothing short of `acceptFromBin` is recovered; ctypes size checks.

Evaluation: gap-tolerant `ball_present` on synthetic frames with a 4-frame gap (true) and a
stationary pair (false); the split summary and comparison.

## Out of scope

Lower-threshold re-detection from the L3 IQ16; the batch anchored line search at RESULT (the
alternative to forward association, worth adding on the same history if this falls short);
fitting the board's DATA_RAM; angles for recovered points; any club-tracker change.

## Results

To be filled in by the evaluation, capture by capture, as in the 2026-09-28 spec.
