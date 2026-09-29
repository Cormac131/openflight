# IWR6843 club after impact, automatic tee band, ball colour — design

Base: `feat/iwr-calcs` (after the impact back-interpolation work, 60fd8d6d).
Approved in conversation 2026-09-29; this is the written spec for review.
Builds on `2026-09-28-iwr-impact-back-interpolation-design.md`.

## Problem

1. **No club after impact with the band on.** On every committed recording,
   `band_bins=5` leaves zero club points after impact (band off: 1–6). The
   band hides the club for the ~5 frames it takes to cross it; the track
   drops after `maxMisses = 2` and `l3_track_follow` never re-acquires, so
   the club-out estimate of the impact fit is always missing. Even with the
   band off the follow-through is short and nothing uses the physics that
   the club always leaves slower than the ball.
2. **The band is placed blindly.** `bandBins` is a half-width (±N) centred
   on the ball lock or configured tee. The ridge the band exists for is not
   centred on the ball (2026-09-28 chart: 1.9–2.45 m around a ball near
   2.2 m), so a centred band wastes clean bins on one side and misses ridge
   on the other.
3. **The ball track is hard to see.** It is orange next to red MTI cells.
4. **Replay undercounts the fit.** The replay sets the shot machine's
   `ballTrackFrames` to the dump's frame count; the board uses the capture
   plan's post-impact frame count and is forced to SOLVE there. 24 local
   captures therefore stop in `ball_track` in the replay and never run the
   impact fit, so the acceptance numbers understate the board.

## A. Club after impact

Files: `l3_club_track.h/.c`, `l3_dump.c`, `firmware_replay.py`, tests.

- **Coast across the band.** After impact (`l3_track_follow`), when the band
  is valid and the club's last point is short of the band's far edge, the
  track may coast for `ceil((hiBin − lastBin) / followBinsPerS / frameS) + 1`
  frames without being dropped (time-based: `followBinsPerS` is the club's
  fitted approach speed). With the band off, today's miss rule stands.
- **Only a slower departing return is the club.** A candidate is accepted
  only when it is (all of):
  - departing and within the existing reach cap (≤ approach speed × 1.1,
    `L3_TRACK_FOLLOW_LEAD_BINS` as today);
  - slower than the ball track: its implied range rate from the club's last
    point is below the ball track's current fitted speed, when the ball
    track has one;
  - not the target the ball tracker claimed this frame.
- **Re-acquire.** If the club track is inactive after impact, it may
  re-acquire once per post frame from departing returns beyond the band
  whose position is consistent with leaving the ball's range at the impact
  time at a speed in (0, approach × 1.1] and below the ball's speed; the
  strongest such return wins. The re-acquired track is marked `following`.
- **Order.** The ball tracker runs first each post frame so its claim and
  current speed are known; the club follows. (Today the club runs first and
  the ball tracker skips the club's claim — the claim direction reverses;
  `skipClubClaim` stays for the ball tracker's own rule and is re-checked.)
- Board and replay make identical calls in identical order.

## B. Automatic tee band

Files: `l3_band.h/.c`, `l3_impact_fit.h` (cfg), `l3_dump.c`,
`firmware_replay.py`, `firmware_host.py`, Pi driver/monitor/server (flag
meaning), docs, tests.

- **Meaning of N.** `bandBins` becomes the band's total width in bins (was a
  half-width). `--iwr6843-tee-band-bins N` and `trackCfg impactFit N` keep
  their names; docs and changelog say the meaning changed. 0 = off.
- **Noise map.** `l3_band_noise_t` holds a per-global-bin exponential moving
  average of the trigger's detection statistic (the MTI residual the
  trigger already computes), over `L3T_MAX_BINS`-style fixed storage
  (≤ 64 floats, ~256 B). It is updated only on pre-impact frames with no
  active club track, from the whole frame window (the band-on path already
  computes whole-window observations). Averaging constant: 1/16 per frame.
- **Placement.** `l3_band_place(noise, centreBin, searchBins, widthBins,
  out)`: the contiguous run of `widthBins` bins, fully inside
  `[centre − searchBins, centre + searchBins]` and the noise map's coverage,
  with the largest summed average; ties go to the run whose centre is
  nearest `centreBin`. `centreBin` is the ball lock, else the configured
  tee. `searchBins` is a new cfg field (`bandSearchBins`, default 10, last
  field of `l3_impact_fit_cfg_t`). With no noise history (fewer than 8
  updates) the band is centred on `centreBin`.
- **When.** Re-placed every idle pre-impact frame; frozen when a club track
  is acquired; unfrozen if that track drops before impact; kept through
  impact and the post frames; released at rearm. The ball tracker is armed
  at the frozen band's far edge (as today).
- **Visible.** The replay reports the placed band (already `result.band`) and
  the noise map; the viewer draws the band where it was placed.

## C. Colours

`scripts/iwr6843/dump_viewer.html`: the ball track, ball points and the
`ball_out` fitted line use a blue from the file's palette; the "host
approach peak" markers change from blue to a neutral grey so blue only
means the ball. Club colours unchanged.

## D. Replay ball-track frame count

`firmware_replay.py`: `ShotCfg.ballTrackFrames` equals the capture's
post-impact frame count as the board's plan defines it (impact frames plus
ball frames), derived from the dump's header/frame metadata; falls back to
today's value only when the dump carries no way to tell, and says so in the
result. The band-off and band-on acceptance runs are repeated afterwards and
recorded.

## Testing

TDD throughout.

- **A:** C unit tests for coast-through-band, slower-than-ball acceptance,
  the ball-claim exclusion and re-acquisition; replay: every committed
  recording with the band on has club points after impact and none of them
  is a ball point; the synthetic shot gains a post-impact club (extend
  `tests/iwr6843_synth.py` with an optional slower club leaving the tee) and
  its club-out estimate is `ok`.
- **B:** C unit tests for the noise map (EMA, idle-only updates, window
  coverage) and placement (max-sum contiguous run, window clipping, ties
  nearest centre, no-history fallback, width wider than the window);
  freezing/unfreezing on track acquire/drop; replay places the band on the
  2026-09-28 full capture where the MTI ridge is; Pi flag/monitor/server
  tests for the meaning change; board source-inspection tests; DATA_RAM
  checked in the `openflight-iwr-sdk` Docker image.
- **C:** viewer test that the ball trace colour differs from the host-peak
  colour and is the blue token.
- **D:** replay test that `ballTrackFrames` equals the post-frame count on a
  phased dump and the synthetic shot reaches RESULT.

## Acceptance

- With the band on (N = 5 and 10), every committed recording has ≥ 3 club
  points after impact and no club point shared with the ball track.
- The club-out estimate is `ok` on at least half of the ball-visible local
  captures with the band on.
- Band-off acceptance lines from the previous spec re-run after D and
  recorded (no tuning).
- No new failures in the full suite beyond the 56 pre-existing.
