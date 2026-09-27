# Labelled calibration datasets

Recorded shots with a trusted launch monitor's numbers beside them, so the
six core measurements (ball speed, vertical and horizontal launch, club
speed, club path, angle of attack) can be validated against a reference
by looping over a corpus. The directory ships empty; the repository holds no
session data.

Layout: one directory per label, a `.l3dump` capture and a `.json` sidecar
per shot with the same stem.

```
tests/radar/datasets/
  driver/   shot_001.l3dump  shot_001.json
  7iron/
  wedge/
  straight/  left/  right/  slow/  medium/  fast/
```

A shot may carry several labels (`["driver", "straight", "fast"]`); it lives
under one of them. The sidecar schema is `openflight.iwr6843.datasets.ShotRecord`:

```json
{
  "schema_version": 1,
  "capture": "shot_001.l3dump",
  "firmware_commit": "1ed49fb",
  "radar_config": "iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
  "radar_position": {"behind_ball_m": 1.575, "right_of_ball_m": 0.0, "height_m": 0.152,
                     "pitch_deg": 10.4, "yaw_deg": 0.0, "roll_deg": 0.0},
  "club": "driver",
  "labels": ["driver", "straight", "fast"],
  "tee_bin": 34,
  "reference": {"monitor": "trackman", "ball_speed_mph": 152.3, "vertical_launch_deg": 11.7,
                "horizontal_launch_deg": 1.2, "club_speed_mph": 104.0, "club_path_deg": 2.1,
                "angle_of_attack_deg": -1.4, "spin_rpm": 2650, "spin_axis_deg": 3.0,
                "carry_yd": 241},
  "environment": {"temperature_c": 18.5, "pressure_hpa": 1009, "relative_humidity": 0.55},
  "notes": "range session, mat, 20 mph tailwind"
}
```

`load_dataset()` validates every sidecar and checks its capture exists;
`reference_error()` compares a replayed or solved result against the
reference. Aim for 30 to 50 shots per club at several speeds before treating
any metric as calibrated.
