"""Tests for the IWR6843 ball track, firmware/iwr6843/l3_ball_track.c.

A ball leaving the origin at a known velocity in the golf frame is observed
as (range, azimuth, elevation) per post-impact frame; the launch fit over the
earliest points must return that velocity: ball speed, horizontal launch
(positive right) and vertical launch (positive up), extrapolated to the
impact time. Decoys (the resting club at the origin, a slow mover, a target
short of the origin, an impossibly fast return) must be refused.
"""

from __future__ import annotations

import ctypes
import math

import pytest

from openflight.iwr6843 import firmware_host as fw

DEG = math.pi / 180.0
BIN_M = 6.0 / 128
FRAME_US = 3000
WHY = {name: index for index, name in enumerate(fw.BALL_TRACK_WHY_NAMES)}
ORIGIN_BIN = 47.0
ORIGIN = (ORIGIN_BIN * BIN_M, 0.0, 0.0)
IMPACT_US = 21_700


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


class Ball:
    def __init__(self, lib, **overrides):
        self.lib = lib
        cfg = fw.BallTrackCfg()
        lib.l3_ball_track_cfg_defaults(ctypes.byref(cfg))
        for name, value in overrides.items():
            setattr(cfg, name, value)
        self.track = fw.BallTrack()
        lib.l3_ball_track_init(ctypes.byref(self.track), ctypes.byref(cfg))

    def arm(self, origin_bin=ORIGIN_BIN, origin=ORIGIN, impact_us=IMPACT_US):
        self.lib.l3_ball_track_arm(
            ctypes.byref(self.track), origin_bin, ctypes.byref(fw.Vec3(*origin)), impact_us
        )

    def update(self, frame, targets, timestamp_us=None) -> bool:
        arr = (fw.TargetObs * max(1, len(targets)))(*targets)
        stamp = frame * FRAME_US if timestamp_us is None else timestamp_us
        return bool(
            self.lib.l3_ball_track_update(ctypes.byref(self.track), arr, len(targets), frame, stamp)
        )

    def set_angles(self, az, el, flags=fw.ANGLE_AZIMUTH | fw.ANGLE_ELEVATION) -> int:
        return self.lib.l3_ball_track_set_angles(ctypes.byref(self.track), az, el, flags)

    def launch(self):
        out = fw.Launch()
        used = self.lib.l3_ball_track_launch(ctypes.byref(self.track), ctypes.byref(out))
        return used, out

    @property
    def why(self) -> str:
        return fw.BALL_TRACK_WHY_NAMES[self.track.why]


def target(frame, range_bin, *, confidence=0.9, doppler=0.0) -> fw.TargetObs:
    t = fw.TargetObs()
    t.frame = frame
    t.timestampUs = frame * FRAME_US
    t.peakBin = int(round(range_bin))
    t.rangeBin = range_bin
    t.energy = 5000.0
    t.peak = 1250.0
    t.stat = t.peak
    t.snr = 20.0
    t.coherence = 0.9
    t.dopplerAliasMps = doppler
    t.confidence = confidence
    return t


def fly(
    lib, *, speed=60.0, hla_deg=0.0, vla_deg=12.0, frames=6, first_frame=8, angles=True, ball=None
):
    """A ball leaving ORIGIN at impact, seen once per frame from first_frame on.

    Impact is at IMPACT_US; frame f is at f * FRAME_US, so the first
    post-impact frame (8, 24 ms) sees the ball 2.3 ms into flight.
    """
    ball = ball or Ball(lib)
    ball.arm()
    vx = speed * math.cos(vla_deg * DEG) * math.cos(hla_deg * DEG)
    vy = speed * math.cos(vla_deg * DEG) * math.sin(hla_deg * DEG)
    vz = speed * math.sin(vla_deg * DEG)
    cal = ball.track.cfg.core.cal
    for frame in range(first_frame, first_frame + frames):
        s = (frame * FRAME_US - IMPACT_US) * 1e-6
        golf = fw.Vec3(ORIGIN[0] + vx * s, ORIGIN[1] + vy * s, ORIGIN[2] + vz * s)
        radar = fw.Vec3()
        lib.l3_frames_golf_to_radar(ctypes.byref(cal), ctypes.byref(golf), ctypes.byref(radar))
        sph = fw.Spherical()
        lib.l3_frames_to_spherical(ctypes.byref(radar), ctypes.byref(sph))
        appended = ball.update(frame, [target(frame, sph.rangeM / BIN_M)])
        assert appended, f"frame {frame} not appended ({ball.why})"
        if angles:
            assert ball.set_angles(sph.azimuthRad, sph.elevationRad) == 1
    return ball, (vx, vy, vz)


def test_defaults_are_a_wide_gate_a_fast_departure_and_six_launch_points(lib):
    cfg = fw.BallTrackCfg()
    lib.l3_ball_track_cfg_defaults(ctypes.byref(cfg))
    assert cfg.core.gateBins == pytest.approx(6.0) and cfg.core.maxMisses == 1
    assert cfg.minDepartureMps == pytest.approx(10.0) and cfg.maxSpeedMps == pytest.approx(100.0)
    assert cfg.originGateBins == pytest.approx(8.0) and cfg.launchPoints == 6
    assert cfg.minDepartureBins == pytest.approx(1.0) and cfg.snr == pytest.approx(3.0)


def test_unarmed_track_ignores_everything(lib):
    ball = Ball(lib)
    assert ball.update(1, [target(1, 50.0)]) is False
    assert ball.why == "unarmed" and ball.track.core.count == 0


def test_a_departing_ball_is_acquired_confirmed_and_tracked(lib):
    ball, _ = fly(lib, frames=5)
    assert ball.track.confirmed == 1 and ball.track.done == 0
    assert ball.track.core.count == 5
    assert ball.track.counters[WHY["acquired"]] == 1
    assert ball.track.counters[WHY["confirmed"]] == 1
    assert ball.track.counters[WHY["tracked"]] == 3
    assert ball.why == "tracked"


@pytest.mark.parametrize("hla_deg,vla_deg", [(0.0, 12.0), (3.0, 10.0), (-4.5, 15.0), (1.2, 25.0)])
def test_launch_recovers_speed_hla_and_vla_with_the_documented_signs(lib, hla_deg, vla_deg):
    ball, (vx, vy, vz) = fly(lib, speed=60.0, hla_deg=hla_deg, vla_deg=vla_deg)
    used, launch = ball.launch()
    assert used == 6 and launch.points == 6
    assert (launch.velocity.x, launch.velocity.y, launch.velocity.z) == pytest.approx(
        (vx, vy, vz), abs=0.4
    )
    assert launch.speedMps == pytest.approx(60.0, abs=0.4)
    assert launch.hlaRad / DEG == pytest.approx(hla_deg, abs=0.3)
    assert launch.vlaRad / DEG == pytest.approx(vla_deg, abs=0.3)
    assert launch.speedValid and launch.hlaValid and launch.vlaValid
    assert launch.confidence > 0.8, "six points are a full launch fit"


def test_launch_position_is_extrapolated_back_to_the_impact_time(lib):
    ball, _ = fly(lib)
    _, launch = ball.launch()
    assert (
        launch.launchPosition.x,
        launch.launchPosition.y,
        launch.launchPosition.z,
    ) == pytest.approx(ORIGIN, abs=0.01)


def test_launch_fits_the_earliest_points_not_the_newest(lib):
    """Drag takes speed off from the first metre; the launch reads the start."""
    ball = Ball(lib, launchPoints=4)
    ball.arm()
    speed_bins = 4.0  # bins per frame at first
    rng = ORIGIN_BIN + 1.0
    for frame in range(8, 18):
        rng += speed_bins
        speed_bins *= 0.93  # slowing down the flight
        assert ball.update(frame, [target(frame, rng)])
    used, launch = ball.launch()
    assert used == 4
    early = 4.0 * 0.93 * BIN_M / (FRAME_US * 1e-6)
    late = 4.0 * 0.93**9 * BIN_M / (FRAME_US * 1e-6)
    assert late < launch.radialSpeedMps < early * 1.02
    assert launch.radialSpeedMps == pytest.approx(early * 0.9, rel=0.08)


def test_range_only_flight_gives_speed_but_no_angles(lib):
    ball, _ = fly(lib, angles=False)
    used, launch = ball.launch()
    assert used == 6 and launch.speedValid and not launch.hlaValid and not launch.vlaValid
    # Without angles the radial speed is the whole measurement.
    assert launch.speedMps == pytest.approx(launch.radialSpeedMps, abs=1e-3)


def test_launch_needs_a_confirmed_flight_with_three_points(lib):
    ball = Ball(lib)
    assert ball.launch()[0] == 0
    ball.arm()
    ball.update(8, [target(8, 49.0)])
    assert ball.launch()[0] == 0, "acquired only"
    ball.update(9, [target(9, 53.0)])
    assert ball.track.confirmed == 1 and ball.launch()[0] == 0, "two points"
    ball.update(10, [target(10, 57.0)])
    assert ball.launch()[0] == 3


def test_targets_at_or_short_of_the_origin_are_never_the_ball(lib):
    """Recorded shots: the impact echo sits at the origin with the strongest
    return of the whole capture; the ball is already a bin or more out."""
    ball = Ball(lib)
    ball.arm()
    assert ball.update(8, [target(8, 44.0), target(8, 40.0)]) is False
    assert ball.why == "nocandidate"
    assert ball.update(8, [target(8, ORIGIN_BIN + 0.4, confidence=0.99)]) is False
    assert ball.why == "nocandidate"
    assert ball.update(9, [target(9, ORIGIN_BIN + 1.2)]) is True


def test_targets_far_beyond_the_origin_cannot_start_a_track(lib):
    ball = Ball(lib)
    ball.arm()
    assert ball.update(8, [target(8, ORIGIN_BIN + 9.0)]) is False
    assert ball.why == "nocandidate"
    assert ball.update(8, [target(8, ORIGIN_BIN + 7.0)]) is True


def test_the_resting_club_at_the_origin_is_excluded_and_a_slow_mover_dropped(lib):
    """The club at rest sits within a bin of the origin: never offered. A
    return a bin out that then crawls (a bin over two frames is 8 m/s) is
    acquired, coasts, and is dropped as too slow when it finally moves."""
    ball = Ball(lib)
    ball.arm()
    assert ball.update(8, [target(8, 47.5)]) is False
    assert ball.why == "nocandidate" and ball.track.core.count == 0
    assert ball.update(9, [target(9, 48.2)]) is True and ball.why == "acquired"
    assert ball.update(10, [target(10, 48.3)]) is False and ball.why == "coasted"
    assert ball.update(11, [target(11, 49.3)]) is False
    assert ball.why == "tooslow" and ball.track.core.count == 0 and ball.track.confirmed == 0
    # The search restarts and a real departure is still taken.
    assert ball.update(12, [target(12, 50.0)]) is True and ball.why == "acquired"
    assert ball.update(13, [target(13, 54.0)]) is True and ball.why == "confirmed"


def test_an_impossibly_fast_return_is_dropped(lib):
    """The association gate bounds the jump first, so the ceiling is tested
    with a lower one: 5 bins in 3 ms is 78 m/s against a 60 m/s ceiling."""
    ball = Ball(lib, maxSpeedMps=60.0)
    ball.arm()
    ball.update(8, [target(8, 48.0)])
    assert ball.update(9, [target(9, 53.0)]) is False
    assert ball.why == "toofast" and ball.track.core.count == 0


def test_a_confirmed_flight_that_leaves_the_window_is_done(lib):
    ball, _ = fly(lib, frames=4)
    assert ball.update(12, []) is False and ball.why == "coasted"
    assert ball.update(13, []) is False and ball.why == "lost"
    assert ball.track.done == 1
    assert ball.update(14, [target(14, 80.0)]) is False and ball.why == "lost"
    # The launch is still readable from what was gathered.
    assert ball.launch()[0] == 4


def test_the_strongest_return_does_not_steal_a_confirmed_flight(lib):
    ball, _ = fly(lib, frames=3)
    last = ball.track.core.lastBin
    step = ball.track.core.velocityBinsPerFrame
    decoy = target(11, ORIGIN_BIN + 0.5, confidence=0.99)  # the club, still at the tee
    real = target(11, last + step, confidence=0.6)
    assert ball.update(11, [decoy, real]) is True
    assert ball.track.core.lastBin == pytest.approx(last + step)


def test_reset_and_rearm_forget_the_flight_but_keep_the_counters(lib):
    ball, _ = fly(lib, frames=4)
    lib.l3_ball_track_reset(ctypes.byref(ball.track))
    assert ball.track.armed == 0 and ball.track.core.count == 0 and ball.track.confirmed == 0
    assert ball.track.counters[WHY["acquired"]] == 1
    ball.arm(origin_bin=50.0, impact_us=99)
    assert (
        ball.track.armed == 1
        and ball.track.originBin == 50.0
        and ball.track.impactTimestampUs == 99
    )
    assert ball.track.cfg.launchPoints == 6


def test_why_names_and_formats(lib):
    for index, name in enumerate(fw.BALL_TRACK_WHY_NAMES):
        assert lib.l3_ball_track_why_name(index).decode() == name
    ball, _ = fly(lib, hla_deg=2.0, vla_deg=12.0)
    status = fw.c_text(lib.l3_ball_track_format_status, ctypes.byref(ball.track))
    assert status.startswith(
        "balltrack armed=1 confirmed=1 done=0 why=tracked count=6 origin=47.00"
    )
    assert f" impact={IMPACT_US} acq=1 slow=0 fast=0 lost=0" in status
    _, launch = ball.launch()
    text = fw.c_text(lib.l3_launch_format, ctypes.byref(launch))
    assert text.startswith("launch points=6 speed=60.") or text.startswith(
        "launch points=6 speed=59."
    )
    assert " hla=2.0" in text and " vla=12.0" in text and text.endswith(" valid=shv")
    empty = fw.Launch()
    assert fw.c_text(lib.l3_launch_format, ctypes.byref(empty)).endswith(" valid=none")


def test_the_departure_band_excludes_the_follow_through_behind_the_ball(lib):
    """Recorded shot 001: after impact the clubhead carried on at 15 m/s
    (bins 48.6, 49.9, 50.9) while the ball was at 51.7 then 54.1. Only
    candidates a bin or more beyond the origin, then beyond the first point,
    are offered, so the club is never the more confident choice."""
    ball = Ball(lib)
    ball.arm(origin_bin=49.0)
    assert (
        ball.update(10, [target(10, 48.6, confidence=0.64), target(10, 51.7, confidence=0.55)])
        is True
    )
    assert ball.track.core.lastBin == pytest.approx(51.7)
    assert (
        ball.update(11, [target(11, 48.4, confidence=0.86), target(11, 54.1, confidence=0.82)])
        is True
    )
    assert ball.why == "confirmed" and ball.track.core.lastBin == pytest.approx(54.1)
    # Once flying, nothing behind the last point is offered to the track, so
    # the static return at the origin cannot pull it back when the ball fades.
    assert (
        ball.update(12, [target(12, 49.9, confidence=0.84), target(12, 56.0, confidence=0.40)])
        is True
    )
    assert ball.track.core.lastBin == pytest.approx(56.0)
    assert ball.update(13, [target(13, 49.0, confidence=0.9)]) is False
    assert ball.why == "coasted"
    assert ball.update(14, [target(14, 61.2, confidence=0.3)]) is True
    assert ball.why == "tracked"


def test_scattered_launch_angles_fall_back_to_the_radial_speed(lib):
    """Shot 003 gave 40 m/s radial with azimuths that swung by 30 degrees; the
    launch must report the radial speed and no angles, not a 54 m/s ball at
    -27 degrees."""
    ball = Ball(lib)
    ball.arm()
    for frame, (b, az) in enumerate(
        [(50.7, 0.1), (53.3, -0.4), (55.8, 0.5), (58.4, -0.5), (61.1, 0.4), (63.7, -0.3)], start=9
    ):
        assert ball.update(frame, [target(frame, b)])
        ball.set_angles(az, 0.2)
    used, launch = ball.launch()
    assert used == 6 and launch.speedValid
    assert not launch.hlaValid and not launch.vlaValid
    assert launch.speedMps == pytest.approx(launch.radialSpeedMps)
    assert launch.speedMps == pytest.approx(2.6 * BIN_M / (FRAME_US * 1e-6), rel=0.05)


def test_the_confirmation_frame_offers_only_the_band_beyond_the_first_point(lib):
    """Recorded shot 002: one point at 52.2 predicts 52.2; the nearest candidate
    was a static return at 51.9 while the ball had moved on to 54.6. From a
    single point only departure can be predicted, so only candidates a bin or
    more beyond it are offered for confirmation."""
    ball = Ball(lib)
    ball.arm(origin_bin=49.0)
    assert (
        ball.update(9, [target(9, 48.3, confidence=0.95), target(9, 52.2, confidence=0.5)]) is True
    )
    assert (
        ball.update(
            10,
            [
                target(10, 48.4, confidence=0.9),
                target(10, 54.6, confidence=0.4),
                target(10, 51.9, confidence=0.3),
            ],
        )
        is True
    )
    assert ball.why == "confirmed" and ball.track.core.lastBin == pytest.approx(54.6)
    # A single point with nothing departing beyond it coasts rather than
    # confirming on the static return.
    ball = Ball(lib)
    ball.arm(origin_bin=49.0)
    ball.update(9, [target(9, 52.2)])
    assert ball.update(10, [target(10, 51.9), target(10, 52.4)]) is False
    assert ball.why == "coasted"
