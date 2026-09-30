// Interchangeable IWR6843 radar front: an RF window at a set standoff and an
// asymmetric hood. Parameter names match RadarFrontParams (radome.py); take
// the window thickness from `evaluate_iwr_clutter.py radome` for the material.
// All lengths in mm, angles in degrees.

radar_window_thickness = 1.52;
radar_window_standoff = 4.84;
radar_window_width = 40;
radar_window_height = 40;
hood_left_depth = 0;
hood_right_depth = 0;
hood_top_depth = 0;
hood_bottom_depth = 0;

wall = 2.4;
frame_depth = 3;
board_clearance = 1.5;
flange = 8;
screw_d = 3.2;

outer_w = radar_window_width + 2 * wall;
outer_h = radar_window_height + 2 * wall;
body_depth = radar_window_standoff + board_clearance + frame_depth;

// The window spans the whole front so it bonds to the walls; only its
// central radar_window_width x radar_window_height is in the radar's view.
module window() {
    translate([-outer_w / 2, -outer_h / 2, body_depth - 0.01])
        cube([outer_w, outer_h, radar_window_thickness + 0.01]);
}

module body() {
    difference() {
        translate([-outer_w / 2, -outer_h / 2, 0])
            cube([outer_w, outer_h, body_depth]);
        translate([-radar_window_width / 2, -radar_window_height / 2, -1])
            cube([radar_window_width, radar_window_height, body_depth + 2]);
    }
    difference() {
        translate([-outer_w / 2 - flange, -outer_h / 2 - flange, 0])
            cube([outer_w + 2 * flange, outer_h + 2 * flange, frame_depth]);
        translate([-outer_w / 2, -outer_h / 2, -1])
            cube([outer_w, outer_h, frame_depth + 2]);
        for (x = [-1, 1], y = [-1, 1])
            translate([x * (outer_w / 2 + flange / 2), y * (outer_h / 2 + flange / 2), -1])
                cylinder(d = screw_d, h = frame_depth + 2, $fn = 24);
    }
}

// One hood wall on each side with a depth; 0 leaves that side open.
module hood() {
    z0 = body_depth + radar_window_thickness - 0.01;
    if (hood_left_depth > 0)
        translate([-outer_w / 2, -outer_h / 2, z0]) cube([wall, outer_h, hood_left_depth]);
    if (hood_right_depth > 0)
        translate([outer_w / 2 - wall, -outer_h / 2, z0]) cube([wall, outer_h, hood_right_depth]);
    if (hood_top_depth > 0)
        translate([-outer_w / 2, outer_h / 2 - wall, z0]) cube([outer_w, wall, hood_top_depth]);
    if (hood_bottom_depth > 0)
        translate([-outer_w / 2, -outer_h / 2, z0]) cube([outer_w, wall, hood_bottom_depth]);
}

union() {
    body();
    window();
    hood();
}
