# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **IWR6843 self-trigger: the ball leaving fires it when the club rules miss.**
  In the early 2026-08-09 captures the club is invisible for the 3-5 frames
  before launch, so neither club rule fired on four swings. `l3_leave.c` fires
  on the first return beyond the tee band's far edge that steps outward at a
  ball's speed on the next frame, read against the median of the bins beyond
  the band (the trigger's floor sits above the leaving ball), armed only just
  after the club reached the band, and only from a return that did not stand
  there the frame before. Replayed at the kiosk's settings every one of the 34
  labelled swings now fires, none more than three frames after launch (30
  within two, from 27); the fallback decides 7, never ahead of a club rule
  that would have fired first. `triggerLog track` gains a `leave` line. On the
  four rescued swings the post-fire launch speed is still poor (17-19 m/s
  against 27-30 labelled on two, none on one). Needs a firmware rebuild and
  reflash.

### Fixed
- **IWR6843 self-trigger still missed swings on the kiosk: it now also fires
  when the club's approach ends near the ball.** The club track's crossing of
  the ball's range was checked with the manifest's hand-set tee bins (about 5
  bins short of the ball), but the kiosk aims 2 bins short of the ball, and
  the hand labels show the club's radar range at launch is 3-12 bins (median
  7.4) short of the ball's. Replayed at the kiosk's settings it fired within
  two frames of the labelled launch on 19 of 34 swings, no better than the
  range gate it replaced (17). The range impact now also fires on the first
  frame an approaching club track takes no point after one within `endM`
  (0.40 m) of the ball, dated to that last point: 27 of 34 within two frames,
  29 from four frames early (the launch still lands in the 16 post frames) to
  two late. `tests/test_iwr6843_labelled_replay.py` now replays every labelled
  swing at the kiosk's own settings. `trackCfg impact
  <horizonS> [endM]`; the `range impact` line gains `cause=` and `armed=`.
  Needs a firmware rebuild and reflash.
- **IWR6843 self-trigger recognised no swings: the club track fires it now.**
  The range gate in `l3_trigger.c` fired from its own short track entering a
  gate around the tee. It held the tee's standing clutter (hands, body, a mat
  edge) until the club had passed: replayed at the kiosk's settings (tee bin
  38, snr 1, 6-bin band) it fired on 10 of the 20 2026-08-24 swings, and its
  standing/stall limits going from 3/4 to 8/8 frames on 2026-09-30 made it
  worse. The club track's range-only impact (`l3_impact_update_range`, the
  club-in line crossing the tee's range within 4 ms) fires on 19 of those 20,
  18 within three frames of the recorded freeze. It already ran on every
  frame, recording only; it is now what freezes the capture.

### Removed
- **The IWR6843 range gate.** Its tracker, state machine, flight recorder and
  thresholds are gone from `l3_trigger.c`, which keeps the watch region, the
  noise floor the club targets are extracted against, and the raw-input trace.
  `triggerCfg` is now `<bin> <snr> <on> [approach past stat]` (the old
  `<frames>` is read as on/off, so existing arming lines still work; a line
  with the gate's `minCoh minStep minSpeed minApproach` is refused). Plain
  `triggerLog` prints the floor and configuration; `triggerLog track` is the
  place to look after a missed swing. `--iwr6843-self-trigger-frames` and the
  `--hits` options of `swing_trigger.py`, `watch_trigger.py` and
  `test_iwr_firmware.py` went with it; those tools now report the club track
  that fired. Bit 0 (`gate`) of the shot's impact source is reserved so older
  result packets still decode. Needs a firmware rebuild and reflash.
- **The IWR6843 geometric impact detector.** It judged the club's fitted 3D
  line against the ball's position and only fired once armed
  (`trackCfg impact ... armed 1`), which the kiosk never did; it never fired
  on the recorded swings. `trackCfg impact` is now `<horizonS>` for the
  range-only impact (the old five-value line is refused), `triggerLog track`
  prints only the `range impact` line, and the replay loses
  `geometry_armed` and `geometric_frame` (the viewer its checkbox, the A/B
  compare its row). The `geometry` source bit and the `geometric_impact`
  quality bit stay reserved for older result packets.
- **The host ball-leave detector** (`BallLeaveDetector` and its replay in
  `self_trigger.py`). It replayed a firmware trigger that no longer exists and
  only fed the dump viewer's "host trigger" panel, which is gone with it
  (`py_level` and `py_hits` options too). `self_trigger.py` keeps the shared
  self-trigger defaults and the empty-lane floor sample the monitor arms with.
  The old C leave-detector harness, `tests/test_iwr6843_trigger_firmware.py`,
  had skipped every case since `l3_trigger.c` replaced it; its two
  `l3_dump.c` checks moved to `test_iwr6843_firmware_sparse.py`.

### Changed
- **IWR6843 tee and net distances are measured from the enclosure front; the
  software adds the array depth.** `--iwr6843-net-m` is measured from the front
  too (the ball-track clamp at the net is 0.25 m short of it, in `net` flight).
  `--iwr6843-tee-m` is now the tape reading from the front of
  the enclosure to the centre of the ball (not the golfer's feet). The antenna
  array sits 0.30 m behind the front (`ARRAY_DEPTH_M`, `iwr6843/calibration.py`)
  and that is added internally wherever the distance becomes a range or a bin:
  the calibration (shot geometry, club gate), the monitor (capture windows,
  trigger) and the dump viewer. Before, the tape reading was used as if it were
  from the antenna and put the tee about 6.4 bins short of the ball: on the
  2026-08-24 session `--iwr6843-tee-m` was 1.524 m and the ball rested at bins
  39-41 (1.83-1.92 m). The session log's `tee_slant_range_m` and `net_range_m` keep
  the tape readings (a new `array_depth_m` records the depth), so logs from before this
  change mean the same thing and the viewer converts them the same way.
- **IWR6843 self-trigger bin follows the tee again: two bins short of the ball.**
  `--iwr6843-self-trigger-bin` defaults to the bin of the tee (the tape reading
  plus the array depth) minus 2, not a fixed 42. Bin 42
  had replaced a tee-derived default that landed 6 bins short for want of the
  array depth. On 38 labelled swings the range gate fired on 37 of 38 at a
  tee 1.88 m from the array and 29 of 38 at 42, and the ball search, which only
  accepts a return within its origin gate of where it is armed, tracked 470 of
  552 labelled ball points 2 bins short of the ball and lost the ball from 4-5
  bins short.
- **IWR6843 defaults: trigger bin 42, trigger and ball snr 1, tee band 6.**
  `--iwr6843-self-trigger-bin` defaulted to 42 (1.97 m, superseded above) and is checked against the cfg's first capture window;
  `--iwr6843-self-trigger-snr` defaults to 1 (was 6);
  `--iwr6843-tee-band-bins` defaults to 6 (was off) and the firmware's own
  default band is 6; the firmware ball tracker's snr defaults to 1 (was 3).
  The dump viewer starts from the same values (served from `/api/defaults`).
  Replays of recorded captures (`firmware_replay`, `evaluate_iwr_tracking`)
  keep the settings the recordings were made with: trigger snr 6, ball snr 3,
  no band, the tee bin from the slant range. Rebuild and flash the firmware.
- **IWR6843 onboard ball angles.** The firmware picks the TDM branch for
  ball angles with the ball track's fitted range rate (the lag-1 phase
  stays the loop rotor), and fits launch angles only from ball points at
  least 0.6 m past the ball (`lateRangeM`); the launch speed keeps the early
  points. The late fit is not reliable: on most recordings the residual
  check rejects it, but on some it passes with angles that disagree with
  LCMF-v1 (recording 20260916_190937: onboard HLA +22 to +24 deg against
  -0.9 deg from the early fit). The Pi therefore never uses onboard launch
  angles, with or without `--iwr6843-onboard-metrics`; the host's LCMF-v1
  is the only launch-angle source.
- **The Pi sends the board its calibration.** At every start the monitor
  sends `trackCfg cal` (tilt, range bias, horizontal phase reference) and
  `trackCfg elem` (8 element phases and gains) from the same calibration
  file the host uses, identity included so a restart clears stale values.
  A calibrated board now shifts its onboard geometry: club positions, club
  path and attack include the tilt, and the range bias is applied. Replays
  (`firmware_replay`, `evaluate_iwr_tracking`) default to identity, as older
  recordings were made, so a replay of a new capture differs from what the
  board reported unless the session's recorded `board_calibration` is
  applied. The inclinometer's live tilt is not sent to the board. The
  azimuth phase offset is sent as 0. Firmware without these commands logs a
  warning, and its onboard angles (launch, club path, attack) are not used
  that session. Update the Pi and the firmware together; rebuild
  `l3_dump.bin` before flashing.

### Fixed
- **IWR6843 self-trigger stuck on a return standing near the tee.** The
  trigger kept tracking whatever it saw first, so a static ridge or a hand at
  the tee held it while the club approached, and the gate never fired (or
  fired late). A track that has not approached at `minStepBins` per step
  for `L3_TRIG_STALL_FRAMES` (4) sightings now gives way to the strongest
  return short of it. Recording 20260927 now takes the club at frame 5 and its
  impact fit is consistent.
- **Dump viewer "post = freeze frame" on captures without a retention
  report** uses the frame where the capture plan switches windows.

### Added
- **IWR6843 track labels.** The dump viewer gains an annotate mode: click the
  ball or club onto the range-time map, seed from the firmware's points, mark
  the object reviewed and save `<dump>.l3dump.labels.json` (tied to the dump
  by SHA-256). Labelled dumps are replayed by a new test that scores the
  firmware's tracks against the labels and fails below the committed
  `label_baseline.json` (`fit_constants.py --update-baseline` accepts a
  deliberate change). `ReplayConfig.overrides` lets a replay change firmware
  config constants, and `scripts/analysis/fit_constants.py` sweeps the
  runtime constants in `iwr6843/tunables.py` against the labels and prints a
  report without editing anything.
- **`ball_slower_than_club` quality bit (4096).** The firmware flags a ball
  whose launch speed is under the club's approach range rate; the Pi then
  treats the onboard ball speed and launch angles as implausible (the club's
  measurements stay trusted) and the shot is never `valid`. On the 51
  ball-visible captures it flags 14 of 32 wrong ball tracks and none of the
  18 good ones.
- **Separate ball-tracker snr.** `--iwr6843-ball-snr` / `trackCfg ballSnr
  <snr>` set the firmware ball tracker's threshold apart from the trigger's;
  the viewer has a "ball snr" box.
- **IWR6843 impact from the tracks either side of the tee band.** The
  firmware fits the club approaching, the club carrying on and the ball
  leaving as straight lines in range and fuses where they cross the ball
  (`l3_impact_fit.c`, `l3_band.c`). A verdict other than `none` replaces the
  shot's impact time with the refined one on the board and in the replay. A
  range-only impact prediction (impact source `range`) fires from the club's
  approach alone. `--iwr6843-tee-band-bins` (experimental, off by default)
  sets the band the trackers ignore via `trackCfg impactFit <bins>`; the Pi
  sends it at every start, 0 included. ±6 bins failed acceptance on the
  recorded sessions.
- **Result packet version 2 (164 bytes)** carries the impact fit. The Pi
  still reads version 1; older Pi code refuses version 2, so update the Pi
  before flashing the new firmware.
- **`impact_uncertain` quality bit** on the onboard result when the impact
  fit's tracks disagree (verdict `inconsistent`).
- **Spin probe A/B and the IQ16 roadmap status.** `scripts/analysis/spin_probe.py
  --iq8` runs the micro-Doppler probe on the firmware-exact IQ8 of the same
  capture and prints the frame-by-frame spread, off-bulk and spectrum
  correlation differences. The firmware guide gains a phase-by-phase status
  table and the rig protocols (compact formats on hardware, HWA rounding,
  angles, shots, cadence, HWA/DSP, spin and face) the remaining phases wait on.
- **OPS-versus-IWR validation and confidence calibration.** Every shot with an
  onboard result logs an `iwr_ops_comparison` entry (OPS and IWR speeds side by
  side, never averaged); `scripts/analysis/ops_validation.py` and
  `scripts/analysis/reference_validation.py` give bias, MAE, RMSE and P95 over
  sessions and labelled datasets (with the thin cells of the club x speed x
  shape matrix named), and `openflight.iwr6843.confidence_calibration` turns
  confidence-versus-error pairs into per-band bounds and rejection thresholds.
  The host packet reports a per-domain confidence.
- **Angle confidence, readable calibration and angular validation tooling.**
  Angle estimates carry a 0..1 confidence from the beam's sharpness and the
  azimuth coherence; `triggerLog cal` prints the calibration in force and the
  host parses the ball detector's measured direction. A static reflector
  protocol (`scripts/hardware-test/iwr6843_angle_static.py`) and a moving
  reflector replay (`scripts/analysis/iwr6843_angle_moving.py`, with an IQ16
  versus IQ8 comparison) produce per-position bias, spread, repeatability and
  error against speed through `openflight.iwr6843.angle_validation`.
- **Exact IQ16 observation statistics and a log-parabolic sub-bin range.**
  `firmware/iwr6843/l3_iq16_stats.c` accumulates the residual energy, per-loop
  power and lag-1 autocorrelation in integers with one float conversion at the
  end, and targets read their sub-bin range from the log parabola through the
  peak and its neighbours (`trackCfg subbin`). The estimator's bias on the
  unwindowed range lobe is measured and pinned.
- **Compact IQ16 capture formats in the firmware.** `captureFormat compact16`
  and `adaptive16` process every frame at full IQ16 precision from the
  accelerator's scratch and store only the retained window in L3 (16/24/16
  bins by default, `captureCfg retain`), placed per frame by the retention
  policy in adaptive16. A stale-scratch guard drops a frame the HWA reused
  before the detect task finished, `stats` and `triggerLog frames` report the
  compaction and the stored frames, and
  `config/iwr6843_l3dump_adaptive_47f3ms_53bin_a16.cfg` fits a 141 ms movie
  into the memory that held 72 ms of wide IQ16. Firmware built and flashed
  from this source is needed; nothing here has run on a board yet.
- **Retention policy for adaptive IQ16 capture.** `firmware/iwr6843/l3_retain.c`
  separates the processing region (what the detect task reads) from the
  retention region (what L3 stores) and chooses each frame's stored window
  from the shot state, the predicted club and the predicted ball, with a
  priority and reason per frame, a priority-ordered L3 budget and a stored
  frame descriptor. The replay harness mirrors it (`--retain`) and on the
  recorded swings keeps every tracked point in about 40% of the bins.
- **Exact IQ8 emulation and an IQ16-vs-IQ8 A/B tool.** The firmware's three
  IQ8 quantisers now live in `firmware/iwr6843/l3_iq8.c`, compiled into the
  board and the host library, so `scripts/analysis/ab_iq16_iq8.py` can turn
  an IQ16 recording into the exact IQ8 the board would store and replay both
  through the onboard pipeline with a measurement-by-measurement delta table
  and a corpus summary. `scripts/analysis/baseline_dataset.py` freezes the
  current per-shot numbers from session logs as the reference dataset, and
  `scripts/hardware-test/iwr6843_iq8_hwa_probe.py` settles the HWA's shift
  rounding on a board.
- **The IWR6843 firmware's shot result reaches the shot and the kiosk.** After
  a self-triggered capture the server reads `triggerLog result`, keeps the
  100-byte packet on the shot as `iwr6843_onboard`, logs it beside the OPS and
  host numbers, and the Live view shows a "TI onboard" strip with MEASURED /
  ESTIMATED and confidence on each metric. `--iwr6843-onboard-metrics` lets
  the firmware's usable launch angles, club path and attack angle replace the
  host pipeline's; ball speed stays the OPS measurement.
- **Setup banner from the ball-placement detector.** `--iwr6843-ball-detector`
  (default `on`) turns the firmware detector on at startup and the server
  polls `ball status` every `--iwr6843-setup-poll-s` seconds, emitting
  `iwr_setup`. The kiosk shows the ball range and how far to move OpenFlight
  (`too-close` to `too-far`), or asks for a ball on the tee.
- **IQ8 profiles get the onboard detect path.** The trigger, club track and
  ball track read int8 rings with the frame scale, so the dense IQ8 profiles
  can self-trigger; the monitor no longer refuses them.
- **The IWR6843 can pick its own track cells (`l3track`).** The firmware now
  runs the ball tracker and cell selection the Pi ran for `l3sparse`
  (`firmware/iwr6843/track_select.c`). It streams only the chosen cells, so
  the ~61 KB residual-power map and the host round trip are gone. At startup
  the server sends the rig limits with `trackCfg`. Older firmware answers
  "not recognized", and the Pi keeps planning cells as before.
  `--no-iwr6843-onboard-track` forces host planning. Launch-angle maths still
  runs on the Pi. `tests/test_iwr6843_track_select.py` builds the C file with
  the host compiler and checks it names the same cells as the Python planner.
  The prebuilt image in `firmware/releases/` has not been rebuilt yet.

### Changed
- **IWR6843 tee band: the value is now a width and the band is placed
  automatically.** `--iwr6843-tee-band-bins N` (and `trackCfg impactFit N`)
  is the band's total width in bins, no longer a half width, so an old `6`
  (13 bins) is now roughly `13`. The firmware keeps a noise map of the MTI
  residual over idle pre-impact frames and places the band on the noisiest
  contiguous run within 10 bins of the ball (centred until 8 idle frames are
  seen); it freezes while a club track is active and is released at the
  rearm. The replay (`firmware_replay`) does the same and reports the map.
- **IWR6843 club after impact.** After impact the ball tracker runs first
  and the club follows the scene it leaves: it coasts across the band,
  slower than the ball, and is re-acquired beyond it when lost.
- **Dump viewer ball colour.** The ball track, ball points and the fitted
  `ball_out` line are blue; the host approach-peak markers are grey, so blue
  only means the ball.
- **IWR6843 club path and attack need three angled points.** A delivery with
  fewer than three angled points used to mix boresight and angled positions
  and skip the residual guard; it now falls back to the radial-only delivery,
  so path and attack are reported invalid rather than wrong.
- **Chromium fallback is reachable during Electron upgrades.** If `ui/dist`
  already exists, a missing Electron install no longer requires Node 22.12 and
  a successful `npm install` before the kiosk can start. Old Node or a failed
  install warns and continues to system Chromium. A missing UI still requires
  Node 22.12+ and a successful build.
- **First switch from Chromium to Electron resets browser-local UI state.**
  Electron persists its own session under `~/.config/openflight-ui` (Linux),
  not the system Chromium profile. Units, language, theme, pinned Live metric,
  and validation annotations in `localStorage` do not carry over. Export the
  Shots CSV on Chromium before switching. Profiles and shot logs are
  server-owned and unaffected. See
  [Electron Kiosk Shell](electron-kiosk-shell.md#browser-local-state-breaking-on-first-electron-launch).

### Fixed
- **Self-trigger fired on a hand placing the ball.** The impact gate judged
  the approach rate from the track's nearest point to the radar, and a return
  standing in the gate moved that point to the current frame every frame, so
  zero elapsed frames skipped the rate test and it fired (`triggerLog` on the
  rig: bin 40 twice, then `fired`). Zero elapsed now reads as no approach
  (`slow`), and a new `minApproach` (`triggerCfg`'s tenth optional value,
  default 3 bins) requires a gate's width of approach before firing, which
  catches an arm whose strongest scatterer wanders two bins in two frames
  (`short`). The counters, `triggerLog` summary and diagnosis name the new
  reason.
- **Ball detector stayed `waiting` with a ball on the tee.** The hand placing
  it was a wide rise, and while that was rejected the background learned the
  ball underneath at the quiet-lane rate, leaving nothing to lock on. Bins
  that have clearly risen now learn at a rate of minutes, and a rise too wide
  to be a ball is set aside so the compact rise beside it (the ball next to a
  standing player) is still found.
- **`host replay agrees` disagreed with every real swing.** The hardware suite
  replayed an older host detector on the computed tee bin while the board ran
  the firmware detector on the bin ball-detect observed. It now runs the same
  C detector, armed as the board was, over the frozen ring.
- **A crash-looping boot service no longer kills the desktop kiosk.** Every
  launcher exit ran a `pkill` that matched the Electron binary path, so an
  `openflight.service` that failed at startup (for example because systemd's
  PATH hides `~/.local/bin/uv`) restarted every 5 s and killed whichever
  kiosk was on screen; Chromium then died with "GPU process isn't usable.
  Goodbye." `start-kiosk.sh` now launches the browser in its own process
  group and stops only that group (`scripts/kiosk-browser.sh`), refuses to
  start while another instance holds `/tmp/openflight-kiosk-<port>.lock`
  (exit 3, `OPENFLIGHT_KIOSK_LOCK_FILE` overrides the path), finds `uv` in
  `~/.local/bin` / `~/.cargo/bin` when PATH omits them, and prints the
  recovery hint to the terminal and journal. The unit file stops retrying
  after five failures in five minutes and never retries exit 3. Re-copy
  `scripts/setup/openflight.service` (or rerun `scripts/setup/setup.sh`) on
  existing Pis to pick up the unit changes.
- **Kiosk startup no longer rebuilds the UI after Electron has already launched.**
  `ensure_kiosk_ui` now runs before the splash browser. The helper is also
  stored with Unix line endings so a Windows checkout cannot make `ui/dist`
  look missing (a CR in the path) and run `npm install` over a live Electron
  GPU process.
- **On-screen keyboard for profile names.** Adding or renaming a profile on the
  Pi kiosk now shows a full-screen keyboard. Chromium in `--kiosk` mode does not
  surface a system keyboard, so the native text field was unusable on the
  touchscreen.
- **Clear-session confirmation is a modal again.** The overlay, scrim, and
  centered dialog styles were missing after the class-name rename, so at the
  800×480 kiosk size the prompt rendered as inline page content.
- **Attack angle no longer inflated by 1/cos(club path).** The camera club
  delivery divided vertical speed by the forward component alone instead of the
  full horizontal speed, overstating attack angle on any shot with club path.

### Added
- **Electron kiosk shell.** `scripts/start-kiosk.sh` now opens the UI in a pinned
  Electron window (`electron@44`) instead of whichever system browser happens to
  be installed. Chromium remains a fallback if Electron is not installed (including
  when Node is older than 22.12 or `npm install` fails and `ui/dist` already
  exists). Installing Electron needs **Node.js 22.12 or newer**. See
  [Electron Kiosk Shell](electron-kiosk-shell.md).
- **PAR-TEE connector.** `"type": "partee"` in `config/sim.json` streams shots
  to the [PAR-TEE](https://playpartee.com) iPhone app over OpenConnect V1 on the
  phone's Wi-Fi address (port 921 by default). Same shared codec as GSPro and
  OpenGolfSim; the header pill and "Sent to" panel read PAR-TEE.
- **Profiles replace players.** Shots are now attributed to a server-owned profile
  (a person *or* a place) with a stable id, persisted to
  `~/.config/openflight/profiles.json` (override with `OPENFLIGHT_PROFILES_PATH`
  or `--profiles-path`). Profiles can be renamed without orphaning their shots.
  Removing a profile is refused while it still has session rows. The socket
  exposes a single authoritative `profiles` snapshot plus
  `set_active_profile` / `add_profile` / `rename_profile` / `remove_profile`.
  Breaking: `set_player` / `player_changed` are gone, `Shot.player_name` is replaced
  by `profile_id` + `profile_name`, and existing browser-local player rosters are
  discarded.
- **Automatic OV9281 exposure control.** High-speed camera capture now measures
  the impact area every five seconds, restores the last known-good setting at
  startup, and selects a shutter/gain combination that preserves club contrast
  without excessive clipping or motion blur. Large lighting changes re-enter
  fast convergence while smaller changes require confirmation. Camera-derived
  shot analysis is withheld when lighting is unsuitable, but radar processing
  and shot display continue normally with an operator-facing lighting warning.
- **Instrument-panel kiosk UI.** The dashboard is a tabbed shell (Live, Stats,
  Shots, Camera, Profiles, Debug) instead of the previous stacked shot and stats
  views. Tap a Live metric to pin it top-left while keeping all ten metrics
  visible. The footer logo opens units, dark/light theme, language, simulator,
  and ball-detection status; a persistent footer power button opens the shutdown
  confirmation. Club (or training implement) selection is a Live header action.
  See the [UI README](https://github.com/jewbetcha/openflight/blob/main/ui/README.md).
- **Kiosk languages.** English, Spanish, French, and Portuguese. Choice is
  stored in `localStorage` (`openflight.locale:v1`).
- **Dark and light themes.** Toggle in the footer menu; stored as
  `openflight.theme` (default dark).
- **Synchronized OV9281 high-speed camera capture.** OpenFlight can now retain
  pre- and post-impact camera frames from the shared sound trigger, align them
  with OPS243 and IWR6843 captures, and use camera-assisted or camera-only
  fallbacks for horizontal launch, club path, and angle of attack. The Camera
  tab adds live alignment, crop, orientation, and lighting controls while the
  rolling buffer remains armed. See [OV9281 Camera](camera/README.md).
- **On-demand camera shot replay.** Camera-backed shots can open a 60 FPS
  slow-motion impact player from Live or Shots, with touch controls, scrubbing,
  and a trigger-frame impact marker. MP4 conversion starts only after a manual
  Replay selection, caches the result beside the raw capture, and reports
  retryable preparation or playback failures without affecting shot results.
- **Battery and external-power status for Raspberry Pi UPS boards.** OpenFlight
  can now display charging state and battery percentage, issue dismissible 20%
  and 10% warnings while discharging, and record throttled power telemetry in
  session logs. Enable the initial Geekworm X1202/X1206 provider with
  `--battery geekworm`; monitoring remains disabled when no provider is
  selected. The accompanying Pi setup installs native Linux power-supply
  telemetry and optional taskbar capacity support without enabling automatic
  shutdown or charging control. See [Battery Monitoring](using/battery.md).
- **System Prerequisites:** Documented missing binary dependencies (`swig`, `liblgpio-dev`, `python3-dev`) required prior to executing `./scripts/setup/setup.sh`.
- **Environment Reload Guidance:** Added instructions for reloading terminal environment variables (`source ~/.bashrc`) when installed dependencies or scripts (`setup.sh`, `start-kiosk.sh`) are not recognized in the current terminal session.
- **Configurable IWR6843 capture compression.** One firmware image can now
  switch at runtime between the recommended 24-frame, 3 ms, 53-bin IQ16
  profile and an advanced 36-frame, 2 ms, 32-bin IQ8 profile. Both retain the
  same 72 ms shot movie and all 3 TX x 4 RX channels. Per-frame range windows,
  timing, and IQ8 scales are carried in the dump so offline and live processing
  use the actual capture geometry. The dense profile uses a sparse scale
  preview to sustain the 2 ms HWA rearm budget and exposes missed frames,
  overruns, and clipped components through firmware `stats`.
- **OPS243 over the Raspberry Pi GPIO UART.** The radar can now run on the J3
  header instead of USB, which frees the Pi's USB power budget for the TI angle
  radar. Baud is the real wire rate on that transport and the factory default of
  19,200 would stretch a 40.6KB dump to 21 seconds, so the driver probes for the
  rate the board is actually using and raises it to 230,400 (`I5`), bringing a
  dump down to ~1.8 seconds. Every dump timeout now scales with the negotiated
  rate, so a link that settles low runs slowly instead of truncating captures.
  Pass `--radar-port /dev/ttyAMA0` (and optionally `--ops-baud`); USB behaviour
  is unchanged. `diagnose.py --ops-port` adds a preflight for the three
  UART-only failures that all look like an unresponsive radar — missing device
  node, a login console holding the port, and the OPS USB cable still plugged in
  (which silences the UART). See
  [Moving the OPS243 from USB to the Pi GPIO UART](build/ops243-uart.md).
- **Flash IWR6843 firmware directly from a Raspberry Pi.** Contributors no
  longer need an Intel Mac, UniFlash, or TI Cloud Agent for routine firmware
  updates. The guided terminal workflow verifies the image hash, offers a
  non-destructive bootloader probe, erases the existing image, transfers the
  replacement in acknowledged chunks, and requires the radar's ROM bootloader
  to verify the completed image. The current IWR6843LEVM still requires its
  physical flash-mode switch and reset button.
- **Experimental three-transmitter capture for horizontal launch direction.**
  The TX2 firmware variant captures all three transmitters while retaining the
  TX1/TX3 vertical array, giving the offline and live pipelines the antenna
  diversity needed to begin measuring left/right start direction.
- **On-chip range snapshots for smaller IWR6843 shot captures.** The radar uses
  its hardware accelerator and EDMA to retain 53 selected complex range-FFT
  bins in moving early, middle, and late windows instead of every raw ADC
  sample. The production ring keeps 18 frames at 4 ms spacing in a 549,542-byte
  dump while preserving vertical and horizontal processing inputs.
- **`--kld7` now delivers the full launch-angle pipeline by default.** Enabling
  the K-LD7 radars turns on the **two-ray multipath vertical launch-angle
  estimator** (per-frame demodulation that separates the ball from its floor
  reflection to recover true elevation instead of averaging across the
  multipath) plus the **ball-speed cosine correction** (OPS radial → true
  speed). Each shot is graded into a tour-derived Tier-1/Tier-2 confidence with
  a tour-average boost for suppressed reads; measurements that clear the
  physics guard but trip a soft consistency guard are shown as **marginal
  (one-dot) confidence** rather than silently replaced by the club estimate.
  Far-net flights are de-aliased past the FSK range wrap (`--net-distance`).
- `--kld7-mount-tilt` is **required** with `--kld7` (measure with a phone
  inclinometer — no safe default). `--kld7-angle-offset` defaults to the
  calibrated `1.5`.
- `--calculated-spin` (opt-in, off by default): replaces radar spin with the
  kinematic estimate `170·v·sin(LA)^1.2`; the measured value is retained in
  `spin_rpm_measured` for scoring.
- `--kld7-vertical-raw` test mode surfaces the raw radar angle for every shot
  (all display guards bypassed).
- **Club path from the IWR6843's pre-impact frames.** `Shot.club_path_deg` has
  been wired end to end since the K-LD7 era but unpopulated since that radar
  was deprecated. It now comes from the six pre-impact frames the L3-dump
  firmware already retains. The estimator fits `x(t)` and `y(t)` in Cartesian
  coordinates and reports `path = atan2(v_y, v_x)`; absolute azimuth enters
  additively rather than cancelling out, so a constant per-element phase error
  from the shipped array calibration (measured on a different board) shifts
  the reported path by a constant rather than the estimator itself — which is
  what `--iwr6843-azimuth-offset-deg` is for. Measured on a first-principles
  fixture across ±12°, absolute error grows with angle (0.034° at 4° to
  0.303° at −12°, roughly symmetric), so it separates deliberate in-to-out
  from out-to-in swings but does not support degree-level claims. Ships
  experimental; validate with `scripts/iwr6843/club_path_report.py` before
  trusting it.

### Changed
- Club physics and simulation defaults now lives in one immutable registry
- Core server, session logging, kiosk startup, and UI code now share canonical
  shot/session helpers and omit redundant compatibility paths.
- Session JSONL format version is now 2 after consolidating trigger and shot
  entries and removing obsolete entry types.
- Display mode (`/display`) now uses the same metric cards and theme tokens as
  the kiosk Live view.
- The vertical estimator is now a fixed cascade (two_ray → geometry →
  single-frame geometry → naive); it is no longer user-selectable. Launch-angle
  source and confidence semantics changed accordingly.

### Removed
- Deprecated integrated-camera detection, obsolete K-LD7 experiment/replay
  tooling, unused session-log writers, and their redundant tests and controls.
- `--kld7-vertical-estimator` (estimator is a fixed cascade), `--kld7-geometry`
  (kiosk preset), and `--ball-speed-cosine-correction` (folded into `--kld7`).
  `--kld7-bypass-vertical-gate` renamed to `--kld7-vertical-raw`.

### Fixed
- Kiosk startup no longer exits when the optional Alloy service is unavailable,
  and it rebuilds the UI bundle before launching the server.
- **Graceful IWR6843 shutdown.** Kiosk shutdown now asks the server to finish
  hardware cleanup before escalating to process signals. An active TI dump is
  allowed to complete, capture firmware is stopped and verified inactive, and
  the serial port is then closed. This prevents an interrupted L3 transfer or
  failed GPIO setup from leaving the radar unresponsive on the next startup.
- **Raspberry Pi 5 & OS Compatibility:** Updated UART configuration documentation to support Debian Bookworm and Raspberry Pi 5 hardware using `dtparam=uart0=on` alongside legacy `enable_uart=1`.
- **UART Diagnostic Commands:** Simplified UART verification instructions in `docs/iwr6843/README.md` and `docs/ops243-uart-migration.md` using `grep -E` to check for both legacy and modern device-tree parameters simultaneously across config paths.
- Session logging: serialize all access to the session JSONL file with a
  lock. The `log_*` methods are called concurrently from the OPS243
  capture thread, the K-LD7 stream thread, and Flask-SocketIO handlers;
  without synchronization, large entries (e.g. `rolling_buffer_capture`
  with 2×4096 samples) could interleave and corrupt JSONL lines, and a
  write could race `end_session()` closing the file (`AttributeError` /
  `ValueError: I/O operation on closed file`). Corrupt lines break the
  offline replay tooling that depends on these logs.
- IWR6843 runtime: the ball-estimate call passed a hardcoded
  `tdm_sign_policy="positive"` instead of the runtime's configurable field
  (the club-path fallback already honored the field, and offline replay
  plumbs a caller-supplied policy end to end). Any non-default policy
  silently produced different live-vs-replay answers for the same capture.
  Live behavior with the default is unchanged.
- **GPIO startup on a Raspberry Pi 5.** Anything using the sound-trigger GPIO —
  the IWR6843 capture monitor and the GPIO sound trigger — died with
  `BadPinFactory: Unable to load any default pin factory!`. The cause is
  upstream: gpiozero 2.0.1.post2 (the latest release) calls `os.path.exists`
  in its lgpio chip auto-detection without importing `os`, and that code path
  only runs on a Pi 5, so gpiozero swallows the `NameError` and every remaining
  backend then fails for its own reason. OpenFlight now selects the lgpio
  factory itself with an explicit gpiochip, which skips the broken branch.
  `OPENFLIGHT_GPIO_CHIP` overrides the chip if a kernel update renumbers the
  header. Note that `GPIOZERO_PIN_FACTORY=lgpio` was never a workaround — it
  forces the same failing call.
- IWR6843 range-snapshot capture now freezes only at a completed ring boundary
  and correctly rearms the HWA/EDMA chain, preventing partial or one-shot-only
  captures during repeated shots.
- K-LD7 tracker: shots could silently lose their launch angle when the
  stream thread appended a frame while the shot path iterated the ring
  buffer (`snapshot_buffer` / `_radc_frames_for_extraction`). CPython
  raises `RuntimeError: deque mutated during iteration` for this, and
  the server's broad K-LD7 exception handler swallowed it, so the shot
  was reported without an angle and no error was visible. Buffer reads
  now copy under a lock; appends and resets take the same lock.
- **One collapsed channel no longer drags the launch angle down.** The vertical
  estimator averaged its two channel estimates unweighted. On a 0.229 m, 5.5°
  mount the `two8` channel collapsed to about 0° on five of six shots while
  `four4_path_tdm` read 15.3–22.0°, so five 7-irons that actually launched near
  17° were reported at 7–9°, each stamped with 0.95 confidence while
  `component_std_deg` sat at 8–10° in the log. Across a seven-shot session the
  plain mean read 10.9° with one channel collapsed; channel selection recovers
  18.3°. Channels that disagree beyond 8° now resolve to the better-supported
  one with reduced confidence; channels that agree are still averaged.
- **Launch-angle confidence is derived instead of hardcoded.** Both the vertical
  and horizontal angles reported a constant 0.95. The horizontal case computed
  HLCMF-v0 coherence, logged it, and then discarded it in favour of the
  constant, so five estimates whose own channels disagreed by 8–10° were
  presented as high confidence. Vertical confidence now follows channel
  agreement and corroboration, horizontal follows coherence, and `spin_axis_deg`
  gates on the horizontal leg rather than appearing the moment club path exists.
- **Track-span floor relaxed from 18 ms to 15 ms, and the span is now logged.**
  Recovers usable captures — one range session went from 6/7 accepted at a
  10.9° mean to 7/7 at 18.3°, and the 18 ms → 15 ms change is what recovered
  the seventh shot. The span is now recorded, since it was the gate rejecting
  most shots and was invisible without an offline replay.
- **A channel that measured nothing can no longer win the channel selection.**
  Objective curvature scored 0 both when a channel's minimum was genuinely
  flat and when its minimum sat on the edge of the −5° to 45° search grid —
  two different things, since an edge minimum means the true angle lies
  outside the searched range and a real launch above 45° pins a perfectly
  healthy channel there. Curvature now returns "no measurement" for an edge
  minimum, and such a channel takes no part in selection, in the spread
  comparison, or in the reported `component_std_deg`. When the channels
  disagree and none has positive curvature — all flat, all off-grid, or
  unscored — the shot is rejected as `rejected_no_conditioned_channel`
  instead of returning whichever channel came first in the dictionary, which
  was `two8`, the one that collapses.
- **The `fast_*` estimates no longer veto corroboration they cannot win.** All
  five components fed the agreement comparison while only the two channel
  models could be selected, so one `fast_*` outlier pushed the spread past
  the 8° gate and cut two channels agreeing to 0.4° down to a single channel
  flagged as uncorroborated and derated. They are diagnostic-only: still
  logged in `components_deg`, now excluded from the selection decision and
  from `component_std_deg`. Affects raw-ADC captures only — range-snapshot
  captures never computed the fast-time models.

### Known Limitations

Deferred pending a session paired with a reference instrument. See
[the IWR6843 operator guide](iwr6843/calibration.md#launch-angle-estimator-limitations).

- **The calibration tilt sweep cannot recommend a tilt.** It minimises
  `component_std_deg`, which is monotonic in tilt across the swept window, so
  its minimum lands on a window edge instead of the mount angle: on the
  2026-07-25 session, with the mount measured at 5.5°, a ±3° sweep returned
  2.5° on two shots and 8.5° on two others. Set tilt by physical measurement.
- **The curvature criterion is not scale-normalised.** `four4_path_tdm`'s
  objective range is 2–4× larger than `two8`'s, so most of the "3.7–10.7×
  sharper" margin is model scale — on one shot the true margin is 1.14×. It is
  validated as a degeneracy detector, not an accuracy ranker, and it is
  one-sided: a collapsed `four4_path_tdm` would likely still win.
- **Selecting is worse than averaging when both channels are healthy but
  disagree.** Monte Carlo at 6° of noise: 4.26° RMS averaging against 5.79°
  selecting, 7.93° on the disagreeing subset. The 8° gate's justification is a
  gap between one shot at 4.59° and six at 15.9–20.2°, from a single session,
  club, geometry and tilt.

### Changed
- Spin detection: drop the autocorrelation override branch. The autocorr
  peak inside the envelope search region often lands at minimum lag
  (~12000 RPM / upper rail) by spectral coincidence, which previously
  flipped legitimate mid-range FFT seam picks to the upper rail and got
  them rejected as bandpass-shoulder noise. The autocorr fallback still
  *confirms* the FFT pick when the two agree within 10%; disagreements
  are now logged for diagnostics but never replace the FFT result.
- Spin detection: lower `SPIN_SNR_MIN` from 3.0 → 2.5 so marginal but
  real seam tones are reported at low confidence instead of dropped.

### Added
- `scripts/analysis/replay_club_speed.py`: offline replay of a proposed
  MEDIAN club-speed picker against any session log. Builds the same
  candidate set the production picker uses, applies a 30 % magnitude
  floor, and reports the median speed for each `rolling_buffer_capture`
  alongside the originally logged (magnitude-pick) value, with smash
  factors as a physical sanity check. The script is exploratory and
  does not change production behaviour — it lets us inspect what a
  median-based picker would have produced before committing to a code
  change.
- `scripts/analysis/plot_spin_debug.py`: 4-panel diagnostic for a single
  `rolling_buffer_capture` (speed timeline, raw I/Q, bandpass envelope,
  envelope FFT spectrum) to inspect what the spin algorithm saw and why
  it accepted or rejected a shot.
- K-LD7 shot-correlation analysis workflow and theory writeup
  - `scripts/analyze_kld7.py --pair-shots` for offline club-to-ball pairing on `.pkl` captures
  - `docs/kld7-ball-detection-theory.md` with capture findings and detection rationale
- Persistent rolling buffer mode workaround for OPS243-A HOST_INT pin bug (per OmniPreSense)
  - `persist_rolling_buffer_mode()` method saves settings to flash memory
  - `test_rolling_buffer_persist.py` script for one-time radar setup and verification
  - Rolling buffer + sound trigger is now the default operating mode
- Grafana Alloy integration for shipping session logs to Grafana Cloud Loki
  - Setup script (`scripts/setup_alloy.sh`) and config (`config/alloy.alloy`)
  - Auto-starts with `start-kiosk.sh` when credentials are configured
  - Observability documentation with LogQL query examples
- Launch angle estimation from club type and ball speed (fallback when camera unavailable)
- Tunable Hough circle detection with all 5 parameters as CLI args (`--hough-param1`, `--hough-param2`, `--hough-min-radius`, `--hough-max-radius`, `--hough-min-dist`)
- Interactive `--tune` mode in `test_launch_angle.py` with live OpenCV trackbar sliders
- Mock mode now simulates realistic spin and launch angle data (TrackMan-based per-club averages)
- Sound trigger wiring guide with MOSFET circuit design (`docs/sound-trigger-wiring.md`)
- Camera integration with real-time ball detection in UI
- Ball detection indicator in header (shows detection status)
- Camera tab with live MJPEG stream and detection overlay
- Hough circle transform as default ball detector (replaces YOLO dependency)
- ByteTrack object tracking for persistent ball identification
- Club speed detection and smash factor calculation
- Rolling buffer mode for experimental spin rate detection
- Session logging to JSONL files (`~/openflight_sessions/`)
- I/Q streaming mode with FFT and 2D CFAR noise rejection
- `--mode rolling-buffer` flag for spin detection
- `--session-location` and `--log-dir` flags for session logging
- Roboflow API integration as optional detection backend
- YOLO performance tuning documentation for Raspberry Pi
- ONNX model export support for faster inference
- Threaded camera capture for improved FPS
- Rolling buffer spin detection documentation

### Changed
- K-LD7 launch-angle processing now uses OPS243 impact timestamps for live correlation
- K-LD7 ball-burst selection now prefers coherent far-target paths instead of averaging all far PDAT detections
- Live K-LD7 vertical launch angles now fall back to the existing club-and-speed estimate when the radar result is an obvious false positive
- Spin detection improved: Hann windowing, zero-padding to 256 points, band-limited search
- All shot metrics (spin, launch angle, club speed, carry) always shown in UI
- Shot logging unified — all metrics in single `shot_detected` entry
- Shot `mode` and `readings_data` are now proper dataclass fields (no more monkey-patching)
- Session logging enabled in mock mode for testing Alloy integration
- Default ball detection uses Hough circles instead of YOLO (no ML model required)
- Camera enabled by default in kiosk mode (use `--no-camera` to disable)
- Dropped Python 3.9 support (requires >=3.10)
- Updated Raspberry Pi setup guide with camera UI and observability instructions

## [0.2.0] - 2024-12-01

### Added
- Web UI with React frontend and Flask-SocketIO backend
- Real-time shot display with ball speed, carry distance, smash factor
- Session statistics view with per-club filtering
- Shot history with pagination
- Debug panel for radar tuning and raw readings
- Mock mode for development without hardware
- Kiosk mode script for Raspberry Pi deployment
- Systemd service for auto-start on boot
- Camera module for launch angle detection (experimental)
- Camera-based ball tracking for launch angle
- Club type selection (Driver through PW)

### Changed
- Migrated from CDM324/HB100 radar to OPS243-A
- Improved carry distance estimation model

## [0.1.0] - 2024-10-01

### Added
- Initial OPS243-A radar driver
- Basic launch monitor with shot detection
- CLI interface for monitoring shots
- Python API for integration
- Carry distance estimation based on ball speed

[Unreleased]: https://github.com/jewbetcha/openflight/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jewbetcha/openflight/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jewbetcha/openflight/releases/tag/v0.1.0
