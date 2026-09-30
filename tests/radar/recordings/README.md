# IWR6843 swing recordings

Recorded `.l3dump` captures from a real rig, replayed through the firmware's
own trigger, observation layer and club track by
`tests/test_iwr6843_firmware_replay.py` and
`scripts/analysis/replay_iwr_track.py`. The replay computes exactly the
per-bin observations the R4F computes (`l3_verticalResidual` in
`firmware/iwr6843/l3_dump.c`) and feeds them to the compiled C modules, so a
change to `l3_trigger.c`, `l3_observation.c` or `l3_club_track.c` can be
judged against every swing here before it is flashed.

The captures here are the swings the trackers were tuned on (see
`manifest.json` for what each one holds and what a replay must reproduce).
To add captures, copy them from the Pi's session directory
(`~/openflight_sessions/iwr6843_<timestamp>_<seq>.l3dump`) and describe them
in `manifest.json`:

```json
{
  "default": {"tee_bin": 34, "snr": 6.0, "track_frames": 2, "stat": "peak"},
  "iwr6843_20260920_181204_003.l3dump": {"dest_bin": 46, "notes": "ball locked at 2.16 m"},
  "iwr6843_20260920_181330_004.l3dump": {"notes": "practice swing, no ball"}
}
```

An `expect` entry (per file or in `default`) states ranges the replay must
land in; the test asserts them and the script prints `expectations: ok` or
the failures and exits 2:

```json
{
  "iwr6843_20260920_181204_003.l3dump": {
    "dest_bin": 46,
    "expect": {"impact_frame": [10, 12], "club_direction": "approaching",
               "club_points_min": 7, "acquisitions_max": 1,
               "ball_origin_bin": [47, 49], "ball_speed_mps": [55, 75]}
  }
}
```

For captures the sound trigger froze (everything recorded before the
self-trigger), set `post_from_frame` to the plan's pre frame count (9 on the
wide profile): the recorded freeze is the impact, so the ball tracker is
judged on the true post frames whatever the range gate did earlier. Set
`dest_bin` to where the ball actually was when the configured tee was wrong.

Keys: `fires`, `impact_frame` (the self-trigger's fire), `club_points_min`,
`club_direction` ("approaching"), `acquisitions_max`, `ball_origin_bin`,
`ball_speed_mps`, `club_speed_mps`.

Every key other than `notes` and `expect` is a `ReplayConfig` field
(`openflight.iwr6843.firmware_replay`). `tee_bin` and `dest_bin` are GLOBAL
range-FFT bins (bin = range / (6 m / 128) on the shipped profiles; bin 34 is
1.59 m). `dest_bin` is the ball detector's locked bin when the firmware was
following it; without it the tee bin is the destination, as on the board.

Run the replay by hand with:

```bash
uv run python scripts/analysis/replay_iwr_track.py tests/radar/recordings --points
```

The acceptance criterion for the club track is a continuous approach
trajectory: one acquisition per swing, a longest run covering the approach,
and a fitted speed in the range a clubhead reaches. The test asserts the
weaker, capture-independent form (a track exists and did not reacquire more
than once); read the report for the rest.

## Labelled dumps

A dump can carry hand labels: the frames where a person can see the ball or
the club on the range-time map. The replay tests score the firmware's tracks
against them, and `scripts/analysis/fit_constants.py` uses them to judge
config constants.

1. Label a dump. Run
   `uv run python scripts/iwr6843/dump_viewer.py --dir tests/radar/recordings`,
   choose the capture from the list, tick **Annotate tracks**, pick ball or
   club, and click the range-time map once per frame where the object is
   visible. Clicking the same frame again moves that point (there is no
   dragging); shift-click removes it. **Seed from firmware** starts from the
   firmware's own points for the chosen object. Tick **reviewed**, then
   **Save labels**. Annotate does not work on an uploaded file, only on a
   capture chosen from the list.
2. A reviewed object with no points means "the firmware must not track this".
3. The sidecar is `<dump>.l3dump.labels.json`, committed with the dump. It is
   tied to the dump by SHA-256, so replacing a dump invalidates its labels
   (the tests fail with "changed since it was labelled").
4. Replay settings for the dump still come from `manifest.json`; add a
   per-file entry if the default `tee_bin`/`dest_bin` are wrong for it.
5. Accept a deliberate score change with
   `uv run python scripts/analysis/fit_constants.py --update-baseline` and
   commit `label_baseline.json`. The labelled-replay test fails if a dump has
   no baseline entry or scores below it.
6. Fit constants with
   `uv run python scripts/analysis/fit_constants.py [--dir DIR] [--passes N] [--only PREFIX]`.
   It only prints a report and edits nothing. Rows with flat n/n should be
   left alone. It sweeps the runtime config fields in
   `src/openflight/iwr6843/tunables.py`, not the `#define`s in the C headers.

Limits. Labels are ground truth as marked by one person, so they carry that
person's judgement. With few labelled dumps the fit tells you little; the
report prints the number of dumps and points it was based on, so read that
before trusting a row.
