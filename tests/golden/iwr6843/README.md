# IWR6843 on-chip solve: golden vectors

This corpus backs the C99-vs-Python equivalence tests for the on-chip solve
port (`tests/test_iwr6843_solve_harness.py`, plus each stage's own
`test_iwr6843_solve_<stage>.py` once that stage is ported). Every `.npz`
holds one recorded input/output pair: the named input arrays that go into
one Python reference call, and the named output arrays that call returned.
A port's C implementation must reproduce the outputs from the inputs within
a stated tolerance.

Regenerate the whole corpus with:

```bash
uv run python scripts/dev/generate_golden_vectors.py
```

The script is deterministic (fixed seeds throughout) — regenerating
reproduces byte-identical **array values** every time (see "Reproducibility"
below for what "byte-identical" does and does not cover).

## What this corpus is, and is not

**There are no saved session dumps in this repo** — no
`~/openflight_sessions/`, no `.ild1` files, nothing but firmware binaries.
So every case here is **synthetic**: built by `synth_shot()` (adapted from
`tests/test_iwr6843_pipeline.py`) or `synth_club_dump()`
(`tests/iwr6843_synth.py`, shared verbatim with
`tests/test_iwr6843_club_path.py`'s own fixture — see that module for the
full derivation), then run through the real, unmodified Python reference
implementations in `src/openflight/iwr6843/{tracking,lcmf,late_window,
club}.py`.

This proves exactly one thing, and it is the thing this harness exists to
prove: **a ported C stage that reproduces these outputs from these inputs
agrees with the Python reference it was ported from.** That is the whole
job of a golden-vector equivalence test.

It does **not** prove the Python reference itself is accurate against real
golf shots. Synthetic captures cannot reproduce real-world SNR, multipath
geometry, clutter, unusual clubs, or sensor nonidealities. **End-to-end
accuracy validation against the real TrackMan corpus remains outstanding
and hardware-gated** (Plan Task 11, shadow validation). Do not read
"C matches Python on this corpus" as "the solve is validated for accuracy."
A future stage 5-8 port that passes every test here has proven equivalence,
not correctness against the real world.

## Stage boundaries

| Stage | Python entry point | Cleanly separable? |
|---|---|---|
| `tracking` | `tracking.find_ball(mti, geo, *, max_range_m, min_ball_ms)` -> `BallTrack \| None` | Yes. Mirrors exactly what `shot.process_dump` calls (MTI cube in, fitted range-walk out). `find_ball_from_power` one layer below is already covered on real R4F hardware by `firmware/iwr6843/track_select.c`; this stage targets the DSP path that computes MTI on-chip, so `find_ball` (taking the complex MTI cube rather than pre-reduced power) is the more useful DSP-side boundary. |
| `lcmf` | `lcmf.estimate_lcmf_v1(raw, cal, *, ball_speed_mph, ...)` -> `LCMFResult` | Yes, at the outer entry point named in the task brief (`lcmf.py:766`). Internally it fans out into many private helpers (`_channel_estimates`, `_fast_estimates`, `_tx2_horizontal_proxy`, ...) that are not separately recorded — the brief names the module's one public entry point, not its internals. |
| `late_window` | `late_window.plan_late_window(mode, ball_speed_mph, launch_angle_deg, spin_rpm, tee_range_m)` -> `LateWindowPlan` | Yes, and unlike the other three stages this one takes no radar capture at all — it is a pure launch-condition planner (ballistic apex time + two predicted look points). No `synth_shot()` case is needed or generated for it. |
| `club` | `club.estimate_club_path(raw, cal, *, ops_club_speed_mph, impact_t_s, tdm_sign)` -> `ClubPathResult` | Yes, but **`synth_shot()` cannot generate club cases** — it only ever simulates the ball, with no club return before impact, so every `synth_shot()` capture run through `estimate_club_path` deterministically rejects with `status="rejected_no_club_track"` (verified). Club cases instead use a separate synthetic generator (`synth_club_dump()` in `tests/iwr6843_synth.py`, shared with `tests/test_iwr6843_club_path.py`): a club head on a straight Cartesian line through the tee at the moment of impact, with exact per-TX TDM + Doppler phase. |

## Cases

### `tracking/` (15 cases) and `lcmf/` (18 cases)

Both stages share one flight-condition table (`FLIGHT_CASES` in the
generator, 11 cases) so the same synthetic dump backs both a
`tracking/<case>.npz` (MTI cube in, `BallTrack` out) and an
`lcmf/<case>.npz` (raw dump bytes + calibration + the OPS-measured ball
speed in, `LCMFResult` out). All use `n_tx=3` (so the 3TX-to-2TX vertical
projection path is always exercised) and `frame_period_us=6000` unless
noted. Beyond that shared table, each stage also has cases that only make
sense on its own side (a stage-specific clamp parameter, or a mismatched
`n_tx=2` capture) — those are generated directly in `main()`, not through
the shared table.

| Case | `speed_ms` | `launch_deg` | Other params | Condition covered |
|---|---|---|---|---|
| `slow_ball_shallow_launch_normal_tx` | 25.0 | 8.0 | `tx_order=normal` | Slow ball, shallow launch, normal TX order |
| `slow_ball_shallow_launch_reversed_tx` | 25.0 | 8.0 | `tx_order=reversed` | Same flight, reversed TX order (the other TX order the task requires) |
| `fast_ball_steep_launch_normal_tx` | 65.0 | 35.0 | `tx_order=normal` | Fast ball, steep launch, normal TX order |
| `fast_ball_steep_launch_reversed_tx` | 65.0 | 35.0 | `tx_order=reversed` | Same flight, reversed TX order |
| `driver_speed_mid_launch` | 55.0 | 12.0 | | Driver-like speed/launch combination |
| `wedge_speed_steep_launch` | 30.0 | 40.0 | | Wedge-like slow speed, steep launch |
| `low_amp_high_noise_no_ball_1` | 45.0 | 18.0 | `amp=1.0, noise=50.0, seed=11` | **No-confidence path**: signal amplitude far below the noise floor; `find_ball` returns `None` / `estimate_lcmf_v1` returns `status="rejected_by_ball_tracker"` (verified) |
| `short_capture_six_frames` | 45.0 | 18.0 | `n_frames=6, trigger_frame=2` | **Short capture**: 6 frames instead of the full 12-frame movie |
| `decelerating_ball` | 50.0 | 15.0 | `accel_ms2=-10.0` | Time-varying radial speed (deceleration) |
| `high_tilt_mount` | 40.0 | 20.0 | `tilt_deg=25.0` | Unusually steep radar mount tilt |
| `floor_image_multipath` | 45.0 | 18.0 | `image_gain=0.6, noise=4.0` | Floor-image multipath (tests the two-ray-relevant regime) |

Extra `tracking/`-only cases (`driver_speed_mid_launch`'s flight, i.e.
`speed_ms=55.0, launch_deg=12.0`, unless noted):

| Case | Params | Condition covered |
|---|---|---|
| `driver_speed_max_range_clamped` | `max_range_m=3.5` | Non-default `max_range_m`: track still found, but with far fewer inliers (73 vs. 160 unclamped) and `rms_bins=0.61 >= 0.45`, so this is also the corpus's only recorded `low_confidence=True`-but-`found` case |
| `driver_speed_min_ball_ms_override` | `min_ball_ms=55.0` | Non-default `min_ball_ms`: selects a measurably different candidate (55.0 m/s / 152 inliers vs. the unclamped 54.68 m/s / 160) |
| `driver_speed_n_tx2_direct` | `n_tx=2` | The capture already IS the vertical pair, so `_tracking_inputs`' 3TX-to-2TX projection never runs — the only tracking case exercising the no-projection path |
| `driver_speed_beyond_max_range_no_ball` | `max_range_m=2.0` | **Distinct no-ball mechanism** (Finding 7): normal SNR, but `max_range_m` clamps both `BALL_GATES_M` gates to an empty interval, so `find_ball` sees zero detections — a different failure mode than the SNR-driven `low_amp_high_noise_no_ball_1` |

Extra `lcmf/`-only cases (same `driver_speed_mid_launch` flight unless noted):

| Case | Params | Condition covered |
|---|---|---|
| `driver_speed_mid_launch_ref_calibration` | real cal (see below) | Real array calibration instead of identity |
| `wedge_speed_steep_launch_ref_calibration` | `wedge_speed_steep_launch` flight + real cal | Real array calibration instead of identity, second flight |
| `driver_speed_tdm_sign_negative` | `tdm_sign_policy="negative"` | `tdm_sign_used=-1` actually recorded (every other case is `+1`) |
| `driver_speed_n_tx2_direct` | `n_tx=2` | lcmf's twin of the tracking no-projection case above |
| `driver_speed_net_range_clamped` | `net_range_m=4.0` | Non-default `net_range_m`, still accepted but with a measurably different `angle_deg`/`track_inliers` than unclamped |
| `driver_speed_grid_step_coarse` | `grid_step_deg=2.0` | Non-default `grid_step_deg` measurably shifts `angle_deg` (~12.15 -> ~12.47 deg), proving the grid resolution is wired into the search |
| `driver_speed_beyond_net_range_no_ball` | `net_range_m=2.25` | lcmf's twin of `driver_speed_beyond_max_range_no_ball`: `rejected_by_ball_tracker` for the max-range reason, not the SNR reason |

For the base-table `lcmf/` cases, `ball_speed_mph` passed to
`estimate_lcmf_v1` is the same synthetic `speed_ms` converted to mph (an
OPS243 in production measures this independently; here it is the ground
truth the synthetic ball was built from). Calibration is the same
`tee_range_m=1.5, tilt_deg=10.4` fixture calibration used throughout
`test_iwr6843_pipeline.py` (identity array correction) unless the case name
says `_ref_calibration`.

**Calibration matters, and the corpus now proves it (Finding 1).** Every
other case uses `Calibration.identity()` — an all-ones `elem_correction` —
so a C port that skips `Calibration.apply` (`calibration.py:81`, consumed at
`lcmf.py:592`) entirely is indistinguishable from a correct one on the rest
of this corpus. The two `_ref_calibration` cases load the real shipped
calibration (`config/iwr6843_calibration_reference.json`) instead. Measured
effect on `driver_speed_mid_launch`: `angle_deg` moves from `12.15` deg
(identity) to `16.85` deg (real cal) on the exact same capture — a
calibration-omitting C port fails this case's `angle_deg` field outright,
under any tolerance that would otherwise be sane for this stage.

Recorded statuses actually observed across the base 11 `lcmf/` cases:
`accepted` (10 cases) and `rejected_by_ball_tracker` (the one remaining
no-ball case, `low_amp_high_noise_no_ball_1`; its former seed-22 duplicate
was replaced per Finding 7 — see `driver_speed_beyond_net_range_no_ball`
above for the distinct failure mode that took its place) and
`rejected_track_quality` (`short_capture_six_frames` — a track that clears
`find_ball` but fails the quality gate). This gives the corpus both
acceptance and multiple distinct rejection paths, not just the happy path.

### `club/` (11 cases, `synth_club_dump()`)

All at `tee_range_m=1.372`, 18 frames / 12 loops unless noted, TDM sign +1
unless noted.

| Case | `path_deg` | `club_speed_ms` | Other params | Condition covered |
|---|---|---|---|---|
| `club_path_square_slow` | 0.0 | 18.0 | | Square path (no in-to-out/out-to-in bias), slow club |
| `club_path_square_fast` | 0.0 | 30.0 | | Square path, fast club |
| `club_path_in_to_out_shallow` | -8.0 | 20.0 | | Shallow in-to-out path |
| `club_path_out_to_in_shallow` | 8.0 | 20.0 | | Shallow out-to-in path (opposite sign convention check) |
| `club_path_in_to_out_steep` | -15.0 | 25.0 | | Steep in-to-out path |
| `club_path_out_to_in_steep` | 15.0 | 25.0 | | Steep out-to-in path |
| `club_no_track_empty_scene` | — | — | all-zero cube | **No-confidence path**: no club return anywhere; `status="rejected_no_club_track"` (verified) |
| `club_short_capture_ten_frames` | 6.0 | 22.0 | `n_frames=10` | **Short capture**: 10 frames instead of 18 |
| `club_path_square_slow_ref_calibration` | 0.0 | 18.0 | real cal | Real array calibration instead of identity (Finding 1) |
| `club_path_out_to_in_shallow_ref_calibration` | 8.0 | 20.0 | real cal | Real array calibration instead of identity, second path (Finding 1) |
| `club_path_tdm_sign_negative` | 4.0 | 20.0 | `tdm_sign=-1` | Non-default `tdm_sign` (every other club case is `+1`) |

`ops_club_speed_mph` passed to `estimate_club_path` is always the synthetic
`club_speed_ms` converted to mph (the ground truth used to build the
capture) — in production this comes from an independent OPS243 measurement.

**Calibration and club path.** `path_deg` itself is unaffected by array
calibration by design (see `tests/test_iwr6843_club_path.py`'s module
docstring: fitting Cartesian x/y cancels a constant per-element phase
error). What real calibration DOES move is the vertical attack-angle
candidate, which goes through `Calibration.apply` via `doa.angle_points` —
measured on `club_path_out_to_in_shallow`: `candidate_attack_angle_deg`
moves from `-31.72` deg (identity) to `-50.24` deg (real cal), and
`attack_fit_rms_m` from `0.073` to `0.174`, on the exact same capture. A
calibration-omitting C port fails those two fields on the
`_ref_calibration` cases even though `path_deg` legitimately does not move.

### `late_window/` (8 cases, pure parameters, no capture)

| Case | `mode` | `ball_speed_mph` | `launch_angle_deg` | `spin_rpm` | Condition covered |
|---|---|---|---|---|---|
| `net_mode_disabled` | `net` | 134.0 | 15.0 | 2500.0 | Net/bay mode: late window always disabled (`reason="net"`) |
| `outdoor_slow_shallow_apex_too_soon` | `outdoor` | 30.0 | 5.0 | 6000.0 | Apex arrives before `LATE_OFFSET_S`: disabled (`reason="apex-too-soon"`) |
| `outdoor_mid_speed_mid_launch` | `outdoor` | 134.0 | 15.0 | 2500.0 | Typical enabled case, two looks scheduled |
| `outdoor_fast_steep_launch` | `outdoor` | 168.0 | 35.0 | 2000.0 | Fast ball, steep launch, enabled |
| `on_course_mid_speed` | `on_course` | 134.0 | 15.0 | 2500.0 | `on_course` mode (the other enabled planner mode besides `outdoor`) |
| `on_course_high_launch` | `on_course` | 100.0 | 45.0 | 3500.0 | Very high launch angle, non-default `tee_range_m=1.4` |
| `outdoor_no_flight_zero_angle` | `outdoor` | 134.0 | 0.0 | 2500.0 | **No-flight path**: zero launch angle, disabled (`reason="no-flight"`) |
| `outdoor_no_flight_zero_speed` | `outdoor` | 0.0 | 15.0 | 2500.0 | **No-flight path**: zero ball speed, disabled (`reason="no-flight"`) |

`late_window` takes no radar capture and has no `net_range_m`/`max_range_m`/
`min_ball_ms`/`grid_step_deg`/`tdm_sign` parameters to vary, so it is
unaffected by Finding 6 and unchanged from the original 8 cases.

## Reproducibility

`scripts/dev/generate_golden_vectors.py` was run repeatedly (once to
`tests/golden/iwr6843`, again to a separate scratch directory) and every
array in every case compared equal (strings via `np.array_equal`, numeric
arrays via exact match after NaN normalization) — 52/52 files, 0 mismatches.
All RNG use (the RANSAC seed in `tracking.find_ball`, the noise seed in
`synth_shot`) is fixed per case, so this is expected and will remain true as
long as neither the generator nor the Python reference changes.

Two caveats on what "reproducible" guarantees here:

- **Byte-identity of the `.npz` files themselves is NOT guaranteed** and is
  not what this corpus relies on. `.npz` is a zip container written by
  `numpy.savez_compressed`; its exact bytes depend on the zlib compressor's
  version/settings and on NumPy's `default_rng` bit-stream implementation,
  either of which can change across NumPy/zlib versions without changing a
  single recorded number. **Array VALUE identity — what every test in this
  suite actually asserts on (`np.load(...)["field"]` compared against a
  tolerance or exactly) — is the durable guarantee**, and is what the
  repeated-run check above verifies.
- The corpus is not stored as full `float64` for size reasons in one known
  place (`tracking/*.npz`'s MTI cube; see "Storage note" below) — that
  tradeoff is deliberate and documented, not an accident of compression.

## Storage note

`tracking/*.npz` stores the recorded MTI cube (`mti_re`/`mti_im`) as
`float32` rather than `float64`. This is a deliberate size tradeoff: the MTI
cube is an **input** to the ported stage, not a value under test, its
~1e-7 relative rounding is far below the real ADC's 16-bit resolution and
the RANSAC gates' bin-scale tolerances, and it roughly halves the corpus
size. All `.npz` files are written with `numpy.savez_compressed`.

The corpus is ~26 MB despite that halving because two stages record full
raw captures rather than reduced summaries: `tracking/` stores the
complete MTI cube for every case (consumed in full by `find_ball`'s
per-loop detection sweep, not just a few samples of it), and `lcmf/`
additionally stores `raw_bytes` — the full undecoded dump — for those same
captures, since `estimate_lcmf_v1` re-derives its own MTI/projection from
the raw bytes rather than accepting a pre-computed cube. Both are genuine
inputs to the stage under test, not incidental bulk.

## Outstanding / hardware-gated

- End-to-end MAE against the real 59-shot TrackMan corpus (Plan acceptance
  criterion 2) — needs real recorded dumps and a range session.
- Any case built from a real saved capture, once one exists (there
  currently are none in this repo to extract from).
- A `low_confidence=True` case with `estimate_lcmf_v1`'s own
  `status="accepted"` (as opposed to tracking's `find_ball` alone) was not
  found within this wave's search budget; `driver_speed_max_range_clamped`
  gives the corpus a `low_confidence=True`-but-found case at the tracking
  stage instead (see the `tracking/` table above).
