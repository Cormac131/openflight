# IWR6843 physics-guided club/ball tracking — combined design

Base: `feat/iwr-calcs`. Approved 2026-09-28.

## Problem (screenshots)

The dump viewer's green club track gets stuck in one range bin while the
moving MTI ridge (correctly marked by the host approach peaks) moves outward.
The underlying cause is a two-part failure in the firmware:

**Pre-impact:** `l3_track_associate` scores candidates with only range error,
wrapped Doppler difference and `(1 − confidence)`. There is no MTI residual
(`stat`/SNR) term. A strong static or near-static body in the approach lane
can outscore the real club when the club briefly aliases near zero Doppler.
The same-bin release fires after the bad point is already appended, so
immediate reacquisition picks the same return.

**Post-impact:** per-frame choices are final (no revision), and nothing
prevents the ball from inheriting club ridge points. This is covered by the
existing joint path-search spec (`2026-09-28-iwr-joint-path-search-design.md`)
and is changed here only in the ball physics rules.

## Phase 1 — pre-impact club track hardening

Files: `firmware/iwr6843/l3_club_track.h/.c`,
       `src/openflight/iwr6843/firmware_host.py`,
       `tests/test_iwr6843_firmware_club_track.py`.

### Score change

Add `weightStrength` to `l3_track_cfg_t` (default 0.5) and add one term to
the association score (minimize):

```
score = wR * rangeMisfit
      + wV * velocityMisfit    // wrapped Doppler vs last Doppler / span
      + wQ * (1 − confidence)
      + wS * (1 / snr)         // NEW: prefer higher MTI-residual targets
```

All terms are already capped by the gate; the SNR term is small for strong
moving targets (snr ≥ 30 → term ≤ 0.017) and meaningfully penalizes weaker
returns.

### Same-bin fix

Reject same-bin candidates *before* scoring (in `l3_track_associate`), not
after appending (in `l3_track_update`). When every in-gate candidate is a
same-bin repeat, the track coasts rather than appending-then-releasing, which
prevents the same-frame reacquisition from picking the same static return.

`l3_track_update`'s post-append same-bin check and release are removed.

### Tests

- Regression for the stuck-track failure: a moving ridge and a strong static
  return coexist in the gate → track follows the ridge.
- New snr-weighted score test: high-snr target wins over equally close
  low-snr target when `weightStrength > 0`.
- Same-bin pre-reject: a same-bin return never increments sameBinCount past
  the limit, track coasts instead.

## Phase 2 — post-impact joint club/ball path search

Design: `2026-09-28-iwr-joint-path-search-design.md`, with these deltas:

### Delta 1 — ball from rest

Ball path: the first point may be at near-zero radial speed (the ball at rest
just after contact). The `startBehindBins` / `startBeyondBins` band already
handles this; the only change is that a stationary return in the start band
does not immediately cost an acceleration penalty. This is already covered by
`ballAccelMps2` being wide (400 m/s²) — no code change needed.

### Delta 2 — tangent/neutral soft preference

When both paths have angle estimates (post-confirmation, the delivery fit has
azimuth), a small bonus (`tangentBonus`) is added when the ball launch
direction is within `tangentToleranceRad` of the direction perpendicular to
the club path at impact (tangent shot). A smaller `neutralBonus` is added
when the ball launch matches the club path (neutral/straight). This is applied
in `l3_joint_finish_frame` after confirmation, informational only — it does
not gate anything. Implementation deferred until the joint search confirms on
recorded captures (Task 8); the config fields are reserved in `l3_joint_cfg_t`
as zero-defaulted placeholders.

### Delta 3 — pre-impact in scope

Phase 1 above changes the pre-impact club track. The joint search spec treats
pre-impact as "unchanged" — that wording is superseded here. The joint search
is seeded by the hardened Phase-1 club state at the gate.

## Rollout

1. Phase 1: implement + test + commit.
2. Phase 2 Tasks 1–5: pure C, host-built, tested on `iwr6843_twotrack.py` scenes.
3. Phase 2 Task 6: host replay `ReplayConfig.joint_search`; dump viewer shows
   joint paths / runner-up.
4. Phase 2 Task 8: `evaluate_iwr_tracking.py` acceptance gates.
5. Phase 2 Task 9: board switch + delete legacy ball/hyps only if acceptance met.

## Acceptance

- **Pre-impact:** club track follows the moving MTI ridge; does not sit in one
  bin across frames when a forward mover exists.
- **Post-impact:** ball not built from club ridge; launch within 15 % of OPS
  on > 15/93 captures; club-at-impact rate ≥ 55/93.
