# IWR6843 RF front and orientation carrier (prototypes)

Two parametric OpenSCAD parts for the golfer-clutter hardware experiments
(`docs/iwr6843/golfer-clutter.md`). Both render as single manifold solids
with OpenSCAD 2021.01. Neither has been printed or measured on the radar yet.

| File | What it is |
| --- | --- |
| `radar_front.scad` | Interchangeable radar front: an RF window at a set standoff plus an optional hood wall on each side (make the golfer's side deeper for an asymmetric hood). |
| `radar_carrier.scad` | Adjustable carrier: yaw plate indexed 0–20° in 5° steps, pitch cradle indexed −10…+10° in 5° steps, pinned with an M3 bolt so each setting repeats. |

The front's parameter names match `RadarFrontParams` in
`src/openflight/iwr6843/radome.py`, so one set of numbers drives both the RF
calculation and the part:

| Parameter | Unit | Meaning |
| --- | --- | --- |
| `radar_window_thickness` | mm | Window wall thickness; take it from the calculator for your material |
| `radar_window_standoff` | mm | Antenna face to the window's inner surface |
| `radar_window_width`, `radar_window_height` | mm | Clear window aperture |
| `hood_left_depth`, `hood_right_depth`, `hood_top_depth`, `hood_bottom_depth` | mm | Hood wall depth beyond the window on each side; 0 leaves that side open |
| `radar_yaw`, `radar_pitch` | deg | Carrier settings (`radar_carrier.scad`) |

Pick a thickness and hood depth:

```bash
uv run python scripts/analysis/evaluate_iwr_clutter.py radome --material PETG \
    --golfer-azimuth-deg -35
```

Export printable parts:

```bash
openscad -o front.stl radar_front.scad -D radar_window_thickness=1.52 -D hood_left_depth=43
openscad -o base.stl radar_carrier.scad -D 'part="base"'
openscad -o plate.stl radar_carrier.scad -D 'part="plate"'
openscad -o cradle.stl radar_carrier.scad -D 'part="cradle"'
```

Print the window region solid (100 % infill, no top/bottom pattern change),
with no paint and no metallic filament. The window thickness only holds
if the slicer lays it down as specified.
