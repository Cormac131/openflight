---
icon: lucide/scan-eye
---

# Camera trigger (replaces the sound trigger)

The camera trigger does the sound trigger's job without the SEN-14262. The
high-speed camera inside the unit watches the ball at address. When the ball
leaves, OpenFlight sends a software `S!` to the OPS243 and the radar dumps its
rolling buffer.

It is **experimental**. Run it in [shadow mode](#rollout-shadow-mode-first)
next to the sound trigger before removing the sound sensor.

## How it works

The camera sits in the unit on the target line (down the line). Two things
follow from that position:

- After impact the ball flies *away* from the camera. It stays near the centre
  of the image and only gets smaller. So OpenFlight watches the small area where
  the ball sat at address. It does not ask whether a ball is anywhere in the frame.
- At address the clubhead sits between the camera and the ball. A hidden ball
  counts as **occluded**, not **gone**.

Each frame, the area around the ball is classified as one of:

| Class | Meaning |
|-------|---------|
| present | The ball template matches, or matches after a small nudge. A nudge moves the lock to the new position. |
| empty | No ball nearby, and the area looks like bare mat. |
| occluded | No ball visible, but the area is not bare mat (club, hand, foot). |

A shot is a **fast departure**. All of these must happen:

1. A ball stays in one place long enough to lock (about 0.3 s).
2. The club is seen at address. That means it appears beside the ball or covers
   the ball for 15 frames. This check can be turned off with
   `--camera-trigger-no-require-address`.
3. Bare mat appears within **20 ms** of the last frame that showed the ball
   (`--camera-trigger-departure-ms`). A hand lifting the ball is much slower
   than this, so a pickup never triggers.
4. **9** bare-mat frames follow, about 30 ms at 300 fps
   (`--camera-trigger-gone-frames`), without the ball reappearing.

The impact time is the midpoint between the last frame with the ball and the
first frame without it, so it is accurate to within ±1.7 ms at 300 fps. That
time is passed to the IWR6843, the camera clip and K-LD7 correlation, and is
used as the fallback impact time in the rolling buffer.

After a trigger, a **new ball** has to be placed before the camera will trigger
again.

### Timing budget

The camera trigger always fires after impact, so the radar buffer is weighted
towards history. The default is `S#28`: about 120 ms before the trigger and
17 ms after. Every shot logs `impact_to_s_bang_ms`. OpenFlight warns when that
value uses more than 70% of the before-trigger window.

If the capture thread was still busy with the previous shot, the impact may
already have scrolled out of the buffer by the time the trigger fires. That
trigger is dropped (`stale_camera_trigger`) rather than wasting a 2 s dump.

## Wiring

1. Remove the SEN-14262, or disconnect its `GATE` wire from OPS J3 pin 3
   (`HOST_INT`).
2. **Tie `HOST_INT` to GND through a 10 kΩ resistor.** A floating `HOST_INT`
   pin fires random dumps and blinds the radar for about 2 s each time.
3. Leave BCM17 unconnected. In camera mode, OpenFlight does not listen on it
   (for either the IWR6843 or the camera clip).

Confirm the result with the idle check:

```bash
uv run python scripts/hardware-test/test_camera_trigger.py --host-int --seconds 60
uv run python scripts/hardware-test/test_camera_trigger.py --s-bang
```

`--s-bang` checks that the radar, in its saved rolling-buffer mode, accepts a
software `S!` without switching modes.

## Rollout: shadow mode first

Shadow mode keeps the sound trigger in charge. The camera runs alongside it and
logs what it *would* have done:

```bash
scripts/start-kiosk.sh --camera-trigger-shadow
```

Each outcome is written to the session log as a `camera_trigger_shadow` entry:

| outcome | meaning |
|---------|---------|
| `matched` | Both fired. `delta_ms` is the camera impact time minus the sound impact time. |
| `camera_only` | The camera fired and no accepted shot followed: a false trigger. |
| `sound_only` | There was an accepted shot but no camera trigger: a miss. |

`trigger_status` also reports a running summary. Switch over once misses and
false triggers are both near zero over a real session.

You can also replay clips saved in earlier sessions:

```bash
uv run python scripts/vision/replay_address_trigger.py ~/openflight_sessions/range/camera
```

## Running with the camera trigger

```bash
scripts/start-kiosk.sh --trigger camera
scripts/start-kiosk.sh --trigger camera --iwr6843
```

`--trigger camera` starts the high-speed camera by itself; you don't also need
`--camera-capture`. If the camera or its detector fails, OpenFlight exits with
an error. It never falls back to the sound trigger silently.

Watch it live, without the kiosk:

```bash
uv run python scripts/hardware-test/test_camera_trigger.py --live --with-radar
```

## Tuning and limits

- Ball acquisition uses the Hough-circle `BallDetector`, which needs OpenCV
  (`uv sync --extra camera`; `start-kiosk.sh` does this automatically). It is
  tuned for an IR-lit ball on a darker mat.
- Camera exposure is set once at startup and not changed while a ball is locked.
  Manual exposure changes are fine: every frame carries its exposure and gain,
  and the empty-mat comparison is normalized by them.
- A ball kicked hard enough to leave its area within one frame looks like a
  shot. The radar check afterwards rejects captures with no outbound reading of
  15 mph or more, as it does for sound.
- Heavy debris or a divot covering the ball's area for longer than 80 ms after
  impact makes the camera miss the shot. Shadow mode will show whether this
  happens at your range.
