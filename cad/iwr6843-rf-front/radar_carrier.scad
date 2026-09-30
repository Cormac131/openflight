// Adjustable IWR6843 carrier for the orientation experiment: a base with an
// indexed yaw plate (0-20 deg in 5 deg steps) and a pitch cradle
// (-10..+10 deg in 5 deg steps), pinned with an M3 bolt through the chosen
// index hole so each setting repeats. Lengths in mm.

yaw_steps = [0, 5, 10, 15, 20];
pitch_steps = [-10, -5, 0, 5, 10];
radar_yaw = 0;
radar_pitch = 0;
part = "all";  // "base", "plate" or "cradle" to export one printable part

base_w = 80;
base_d = 60;
base_t = 5;
plate_r = 34;
pivot_d = 5.2;
pin_d = 3.3;
index_r = 28;
cradle_w = 50;
cradle_h = 40;
cradle_t = 4;
cradle_arm = 30;

module index_holes(steps, radius) {
    for (a = steps)
        rotate([0, 0, a]) translate([radius, 0, -1]) cylinder(d = pin_d, h = 50, $fn = 20);
}

module base() {
    difference() {
        translate([-base_w / 2, -base_d / 2, 0]) cube([base_w, base_d, base_t]);
        translate([0, 0, -1]) cylinder(d = pivot_d, h = base_t + 2, $fn = 24);
        translate([0, 0, 0]) index_holes([0], index_r);
    }
}

module yaw_plate() {
    difference() {
        cylinder(r = plate_r, h = base_t, $fn = 96);
        translate([0, 0, -1]) cylinder(d = pivot_d, h = base_t + 2, $fn = 24);
        rotate([0, 0, 0]) index_holes([for (a = yaw_steps) -a], index_r);
    }
    for (x = [-1, 1])
        translate([x * (cradle_w / 2 + cradle_t) - cradle_t / 2, -cradle_t / 2, base_t])
            difference() {
                cube([cradle_t, cradle_t * 3, cradle_arm]);
                translate([-1, cradle_t * 1.5, cradle_arm - 6])
                    rotate([0, 90, 0]) cylinder(d = pivot_d, h = cradle_t + 2, $fn = 24);
            }
}

module pitch_cradle() {
    difference() {
        translate([-cradle_w / 2, -cradle_t / 2, -cradle_h / 2]) cube([cradle_w, cradle_t, cradle_h]);
        for (p = pitch_steps)
            rotate([0, -p, 0]) translate([cradle_w / 2 - 5, -cradle_t, 0])
                rotate([-90, 0, 0]) cylinder(d = pin_d, h = cradle_t * 3, $fn = 20);
    }
}

if (part == "base") base();
else if (part == "plate") yaw_plate();
else if (part == "cradle") pitch_cradle();
else {
    base();
    translate([0, 0, base_t]) rotate([0, 0, radar_yaw]) {
        yaw_plate();
        translate([0, cradle_t, base_t + cradle_arm - 6]) rotate([0, radar_pitch, 0]) pitch_cradle();
    }
}
