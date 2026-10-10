---
icon: lucide/crosshair
---

# Azimuth Bench Check

Measure how well the IWR6843 places a target sideways (lateral) and in
height, against positions you tape out, and calibrate the azimuth zero
offset. Run it before relying on a spatial gate around the tee: a gate is
only as tight as the lateral measurement underneath it.

The recorded swings cannot answer this. None of them holds a target whose
direction is known; even the ball on its tee shares its range with the mat,
the floor and the golfer. A reflector at a taped position, with an empty
capture subtracted, is that target.

## What You Need

- A small reflector: a corner reflector, or an empty drinks can, on a
  non-metal stick (wood or plastic) so you can hold or prop it at a height.
- Tape measure and floor tape.
- The kiosk stopped, so `scripts/iwr6843/l3dump.py` owns the radar's UART.
- Nobody within about 2 m of the tee while capturing, the person holding the
  stick included where possible (prop it instead).

## Mark Out The Positions

1. Mark the **target line**: the line from the radar through the tee.
2. At the tee's distance, mark five points across it: **-0.6, -0.3, 0,
   +0.3 and +0.6 m**. Positive is to the right of the target line, seen from
   behind the radar looking down the line.
3. Measure the forward distance from the radar's face to the tee along the
   target line. You will put it in the manifest as `forward_m`; it narrows
   the search for the reflector's range.

## Capture

Each capture is one `l3dump`, about 50 ms of radar frames. Take each one
with the scene completely still.

```bash
mkdir -p ~/bench/az_$(date +%Y%m%d)
cd ~/bench/az_$(date +%Y%m%d)
L3DUMP="uv run --project ~/openflight python ~/openflight/scripts/iwr6843/l3dump.py"

# 1. Empty scene: no reflector, nobody near the tee.
$L3DUMP --out empty.l3dump

# 2. Reflector low (about 0.1 m off the floor) at each lateral mark.
$L3DUMP --out s_m060_h010.l3dump   # -0.6 m
$L3DUMP --out s_m030_h010.l3dump   # -0.3 m
$L3DUMP --out s_000_h010.l3dump    #  0
$L3DUMP --out s_p030_h010.l3dump   # +0.3 m
$L3DUMP --out s_p060_h010.l3dump   # +0.6 m

# 3. The same five at about 0.5 m off the floor.
$L3DUMP --out s_m060_h050.l3dump
#   ... and so on for the other four

# 4. Moving: sweep the reflector along the target line through the tee,
#    low, at roughly club-swing speed, with --cue to time it into the window.
$L3DUMP --cue --count 3 --out sweep_000/
#    ... and the same 0.3 m to the right of the line.
$L3DUMP --cue --count 3 --out sweep_p030/
```

Re-take the empty capture if anything in the bay moves between captures
(a chair, a net, a bag): the check subtracts it from every static capture.

## Write The Manifest

`manifest.json` in the same folder, one entry per capture. `lateral_m` is
right of the target line, `height_m` above the floor, `forward_m` along the
target line from the radar (optional, but it makes the range search robust).

```json
{
  "empty": "empty.l3dump",
  "captures": [
    {"file": "s_m060_h010.l3dump", "kind": "static", "lateral_m": -0.6, "height_m": 0.1, "forward_m": 1.9},
    {"file": "s_000_h010.l3dump",  "kind": "static", "lateral_m":  0.0, "height_m": 0.1, "forward_m": 1.9},
    {"file": "s_p060_h010.l3dump", "kind": "static", "lateral_m":  0.6, "height_m": 0.1, "forward_m": 1.9},
    {"file": "sweep_000/iwr6843_..._001.l3dump", "kind": "moving", "lateral_m": 0.0, "height_m": 0.1}
  ]
}
```

## Run The Check

```bash
uv run python scripts/iwr6843/azimuth_check.py ~/bench/az_20261009/manifest.json
```

It prints, per static position, the taped and measured lateral offset and
height and the error in centimetres, and fits the **azimuth zero offset**
that lines the positions up (it needs at least three static captures at
three different lateral marks). Moving captures are run through the club
track, with its TDM motion correction, and report the share of track points
within 15 and 30 cm of their taped line. A report JSON is written beside
the manifest.

How to read it:

| Result | Meaning |
|---|---|
| Median lateral error a few cm, slope about 1 | The azimuth works; the fitted offset is its zero |
| Slope well away from 1, or negative | Scale or sign is wrong; an offset cannot fix it. Report it |
| Static good, moving points spread wide | The motion correction is the problem, not the antenna |
| Height errors large | Elevation or the radar height in the calibration is off |

To keep the fitted offset, save it as this board's calibration:

```bash
uv run python scripts/iwr6843/azimuth_check.py ~/bench/az_20261009/manifest.json --save
```

That writes `~/.config/openflight/iwr6843_calibration.json` (the shipped
reference with the fitted `azimuth_offset_rad` added), keeping any earlier
one as `iwr6843_calibration.json.prev`. The kiosk, `calibrate.py` and the
next bench check load it on their own; there is nothing to pass.
`--iwr6843-cal` (kiosk) and `--cal` (scripts) still override it, and without
the file they fall back to `config/iwr6843_calibration_reference.json`. The
kiosk logs which calibration it loaded at startup. `--save PATH` writes
somewhere else instead.

The check refuses to save when no offset was fitted or the slope says the
scale or sign is wrong. The board receives the offset as part of
`trackCfg cal` at every start.

Commit the bench folder (captures, manifest and report) under
`tests/radar/recordings/` so the numbers can be replayed after any change to
the angle code.

## Then: The Swing Zone

The lateral and height errors this check measures set the **swing zone**:
the corridor around the tee that a club point has to sit in
(`firmware/iwr6843/l3_zone.c`). It is a box in the golf frame, from
`short` metres short of the tee to `past` metres beyond it, `half width`
either side of the target line, between two heights above the floor. Until
the limits are measured it uses starting values: 1.2 m short, 0.3 m past,
±0.3 m, -0.1 to 1.2 m. It is not wired into the board's trigger yet; two
tools show what it would do first.

**Dump viewer.** The down-the-line view draws the zone as a box and marks
each club point outside it with a cross; hovering says which limit it broke.
The zone row on the page changes the limits; blank keeps the starting value.
The viewer uses this board's calibration, as the kiosk does (untick **board
calibration** to replay with identity elements).

**Zone report.** Every labelled recording through the zone, at the kiosk's
trigger settings:

```bash
uv run python scripts/iwr6843/zone_report.py
uv run python scripts/iwr6843/zone_report.py --half-width-m 0.4 --max-height-m 0.9
```

It counts three kinds of club-track point: `impact` (on the labelled club
within 4 frames of launch, which the zone must keep), `club` (earlier in the
approach) and `stray` (anything else the track took, which it should
refuse), with the share kept and why the rest were refused. A good zone
keeps nearly every impact point and few strays.
