---
icon: lucide/user-x
---

# Golfer Clutter: Benchmark and Rig Experiments

The golfer stands beside the ball and is a strong, moving radar return of their
own: the red hotspot on the range-time map just short of the ball. The club
runs into it before impact, and the ball and club come out of it afterwards.
The goal is not to erase the hotspot. It is to keep the hotspot from deciding
detection and track association, so the club can be tracked into it, bridged
through it and the club and ball picked up out of it.

Everything here is judged by one benchmark over the same recorded dumps. Run it
before and after every software or hardware change.

## Run the Benchmark

```bash
# Clutter map sweep (Phase 1): beta 0.5-1.0, median and EMA, against beta 0
uv run python scripts/analysis/evaluate_iwr_clutter.py bench --json bench.json

# Host kinematic tracker against the hand labels (Phases 2-5)
uv run python scripts/analysis/evaluate_iwr_clutter.py host --ablations --sweep-weights

# Bartlett vs Capon (Phase 6)
uv run python scripts/analysis/evaluate_iwr_clutter.py beam --tracking
```

Each command reads the committed recordings by default. Pass one or more
folders of your own `.l3dump` files instead. Each folder needs a
`manifest.json` like the one in `tests/radar/recordings`, or pass `--tee-bin`.

Per capture, the benchmark records:

- the hotspot's power in the impact ROI, before and after suppression;
- club points before impact, through the hotspot, and after impact;
- the first ball frame and how long the ball track runs;
- club and ball speed at impact, bridged across the hotspot;
- detections on no track;
- whether impact was declared;
- `SCR = P_club/ball - P_golfer` in dB;
- label coverage, where the dump has reviewed labels.

## Orientation Experiment (Phase 7)

Do this before changing the enclosure CAD. Print the adjustable carrier in
`cad/iwr6843-rf-front/` and capture the same golfer and tee set-up at each
setting:

| Yaw | Pitch |
|---|---|
| 0, 5, 10, 15, 20 degrees | -10, -5, 0, +5, +10 degrees |

1. Keep the tee, the golfer's stance, the mat and the net fixed. Mark them.
2. At each setting, hit at least five shots. Use the same club throughout.
3. Record which dump was taken at which setting in a rig manifest:

    ```json
    {
      "iwr6843_20261005_101200_001.l3dump": {"label": "yaw0 pitch0", "yaw_deg": 0, "pitch_deg": 0},
      "iwr6843_20261005_101530_006.l3dump": {"label": "yaw10 pitch0", "yaw_deg": 10, "pitch_deg": 0}
    }
    ```

4. Build the matrix:

    ```bash
    uv run python scripts/analysis/evaluate_iwr_clutter.py rig SESSION_DIR --rig-manifest rig.json
    ```

The table lists golfer dB, club SCR, ball SCR, trigger rate and tracking rate
per setting. The row with the largest combined SCR,
`(club + ball response) / golfer response`, is the mounting angle for the next
enclosure. The tee does not have to be on boresight.

## Enclosure Contribution (Phase 8)

With the same golfer and tee, capture:

- **A**: the IWR completely exposed;
- **B**: the IWR behind the current radar front;
- **C**: the front plus the surrounding enclosure.

Label them `"enclosure": "A"`, `"B"` or `"C"` in the rig manifest and run the
`rig` command. If B or C shows a hotspot at least 3 dB stronger than A, the
enclosure is adding multipath to the golfer's direct reflection. Fix the
enclosure before tuning the software further.

## Radar Front and Radome (Phases 9-11)

The window thickness depends on the material, not on the free-space
wavelength. At 62 GHz, λ₀ ≈ 4.84 mm, but inside a plastic with εr ≈ 2.7 the
half wavelength is about 1.47 mm. Get a thickness and its worst-case
round-trip loss over 60-64 GHz and ±30°:

```bash
uv run python scripts/analysis/evaluate_iwr_clutter.py radome
uv run python scripts/analysis/evaluate_iwr_clutter.py radome --material PETG --golfer-azimuth-deg -35
```

The material values are nominal and vary with filament and colour. Treat them
as a starting point: an A-vs-B capture with the printed window is the real
measurement. TI's *mmWave Radar Radome Design Guide* (SWRA705) is the
reference for:

- low permittivity and low loss;
- uniform thickness and smooth surfaces;
- no metallic paint;
- the antenna-to-window standoff.

The calculator lists half-wavelength standoffs as candidates to test, not a
recommendation.

The asymmetric hood (Phase 11) uses a deeper wall on the golfer's side. With
`--golfer-azimuth-deg` (from the clutter map's learned direction, or
measured), the calculator gives the wall depth that shades the antenna beyond
that angle. Build it as an interchangeable front, not a new enclosure, and
compare fronts with the enclosure experiment. A 3-10 dB drop in golfer power
for 1-2 dB of target loss is already worthwhile.

`radar_front.scad` exposes these parameters, with names that match
`RadarFrontParams` in `radome.py`:

- `radar_window_thickness`, `radar_window_standoff`;
- `radar_window_width`, `radar_window_height`;
- `hood_left_depth`, `hood_right_depth`, `hood_top_depth`, `hood_bottom_depth`.

`radar_carrier.scad` sets `radar_yaw` and `radar_pitch`.

## What Is Validated

The software stages were validated only by replaying the committed
recordings and synthetic dumps. None of them runs on the board yet. The CAD
parts render in OpenSCAD but have not been printed or measured, and none of
the hardware experiments above has been run. The results so far are in
`docs/superpowers/specs/2026-09-30-iwr-golfer-clutter-design.md`.
