"""The host kinematic club/ball tracker (association.py)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import association as asc, firmware_replay as fr
from openflight.iwr6843.clutter_map import ClutterConfig, ShotPhase

BIN_M = 6.0 / 128
FRAME_US = 3000
SPAN = 2.0 * asc.WAVELENGTH_M / (4.0 * 135e-6)


def det(frame, range_bin, *, snr=50.0, doppler=0.0, elevation=None, azimuth=None, local=0):
    return asc.Detection(
        frame=frame,
        timestamp_us=frame * FRAME_US,
        range_bin=range_bin,
        range_m=range_bin * BIN_M,
        power=snr,
        snr=snr,
        doppler_mps=doppler,
        lag1_phase_rad=0.0,
        local_bin=local,
        elevation_deg=elevation,
        azimuth_deg=azimuth,
    )


def track_of(*points, velocity=0.0, cls=asc.TrackClass.TENTATIVE):
    first = points[0]
    track = asc.TrackState(first.range_m, 0.0, 0.0, first.timestamp_us, first.frame, cls)
    for p in points:
        track.update(p)
    if velocity:
        track.velocity_mps = velocity
    return track


class TestWeights:
    def test_normalised_sums_to_one(self):
        assert sum(asc.ScoreWeights(1, 1, 1, 1, 1, 1).normalised().as_tuple()) == pytest.approx(1)

    def test_default_puts_power_last(self):
        w = asc.ScoreWeights()
        assert w.power == min(w.as_tuple())

    @pytest.mark.parametrize("values", [(-1, 1, 1, 1, 1, 1), (0, 0, 0, 0, 0, 0)])
    def test_rejects(self, values):
        with pytest.raises(ValueError):
            asc.ScoreWeights(*values)

    def test_combine(self):
        terms = dict.fromkeys(
            ("range", "velocity", "acceleration", "angle", "history", "power"), 1.0
        )
        assert asc.combine(terms, asc.ScoreWeights()) == pytest.approx(1.0)
        terms["power"] = 0.0
        assert asc.combine(terms, asc.ScoreWeights()) == pytest.approx(0.95)


class TestTrackerConfig:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"snr": 0.0},
            {"gate_bins": 0.0},
            {"max_detections": 0},
            {"fov_floor": 1.5},
            {"clutter_weight": -0.1},
            {"max_coast_frames": 3, "clutter_coast_frames": 2},
            {"beamformer": "music"},
        ],
    )
    def test_rejects(self, kwargs):
        with pytest.raises(ValueError):
            asc.TrackerConfig(**kwargs)


class TestTrackState:
    def test_prediction_is_constant_acceleration(self):
        track = asc.TrackState(1.0, 10.0, 100.0, 0, 0)
        rng, vel = track.predict(10_000)
        assert rng == pytest.approx(1.0 + 0.1 + 0.005)
        assert vel == pytest.approx(11.0)

    def test_update_learns_the_rate(self):
        track = track_of(det(0, 20.0), det(1, 22.0))
        assert track.velocity_mps == pytest.approx(2 * BIN_M / 0.003)
        assert track.misses == 0 and len(track.points) == 2

    def test_acceleration_is_clipped(self):
        track = track_of(det(0, 20.0), det(1, 22.0), det(2, 40.0))
        assert abs(track.acceleration_mps2) <= track.max_accel

    def test_fitted_speed(self):
        points = [det(f, 20.0 + 2.0 * f) for f in range(5)]
        assert asc.fitted_speed(points) == pytest.approx(2 * BIN_M / 0.003)
        assert asc.fitted_speed(points[:1]) is None
        assert asc.fitted_speed([det(3, 20.0), det(3, 21.0)]) is None


class TestEvidence:
    config = asc.TrackerConfig()

    def terms(self, track, d, peak=100.0):
        return asc.evidence(
            track, d, self.config, bin_m=BIN_M, velocity_span_mps=SPAN, frame_peak_power=peak
        )

    def test_a_detection_on_the_prediction_scores_high(self):
        track = track_of(det(0, 20.0), det(1, 22.0), det(2, 24.0))
        terms = self.terms(track, det(3, 26.0, snr=5.0))
        assert terms["range"] > 0.9 and terms["velocity"] > 0.4 and terms["acceleration"] > 0.9

    def test_power_is_weak_evidence(self):
        track = track_of(det(0, 20.0), det(1, 22.0), det(2, 24.0))
        on_track_weak = asc.combine(self.terms(track, det(3, 26.0, snr=3.0)), asc.ScoreWeights())
        off_track_strong = asc.combine(
            self.terms(track, det(3, 27.5, snr=100.0)), asc.ScoreWeights()
        )
        assert on_track_weak > off_track_strong

    def test_outside_the_gate(self):
        track = track_of(det(0, 20.0), det(1, 22.0))
        assert self.terms(track, det(2, 30.0)) is None

    def test_same_timestamp_is_rejected(self):
        track = track_of(det(0, 20.0), det(1, 22.0))
        assert self.terms(track, det(1, 22.0)) is None

    def test_one_point_track_gates_on_its_speed_bounds(self):
        track = track_of(det(0, 40.0))
        track.speed_bounds = (15.0, 90.0)
        assert self.terms(track, det(1, 43.0)) is not None  # 47 m/s
        assert self.terms(track, det(1, 40.5)) is None  # 8 m/s: too slow for a ball
        assert self.terms(track, det(1, 38.0)) is None  # receding

    def test_angles_count_when_both_are_known(self):
        track = track_of(det(0, 20.0, elevation=5.0), det(1, 22.0, elevation=5.0))
        near = self.terms(track, det(2, 24.0, elevation=6.0))["angle"]
        far = self.terms(track, det(2, 24.0, elevation=30.0))["angle"]
        unknown = self.terms(track, det(2, 24.0))["angle"]
        assert near > unknown > far

    def test_alias_velocity(self):
        assert asc.alias_velocity(0.5 * SPAN + 1.0, SPAN) == pytest.approx(-0.5 * SPAN + 1.0)
        assert asc.alias_velocity(3.0, SPAN) == pytest.approx(3.0)


class TestClutterProbability:
    def ev(self, **kwargs):
        base = dict(region=0.0, background=0.0, slow=0.0, persistent=0.0, club_agreement=0.0)
        base.update(kwargs)
        return asc.ClutterEvidence(**base)

    def test_golfer_like_is_clutter(self):
        p = asc.clutter_probability(self.ev(region=1, background=1, slow=1, persistent=1))
        assert p > 0.95

    def test_a_transient_fast_return_is_not(self):
        assert asc.clutter_probability(self.ev()) < 0.1

    def test_each_feature_raises_it(self):
        base = asc.clutter_probability(self.ev())
        for name in ("region", "background", "slow", "persistent"):
            assert asc.clutter_probability(self.ev(**{name: 1.0})) > base

    def test_club_agreement_overrides_the_golfer_region(self):
        golfer = self.ev(region=1, background=1, slow=0, persistent=1)
        on_club = self.ev(region=1, background=1, slow=0, persistent=1, club_agreement=1)
        assert asc.clutter_probability(on_club) < 0.5 < asc.clutter_probability(golfer)

    def test_golfer_membership(self):
        geometry = asc.SceneGeometry(1.8, golfer_elevation_deg=-10.0, golfer_azimuth_deg=-30.0)
        inside = det(0, 38.0, elevation=-10.0, azimuth=-30.0)
        outside = det(0, 38.0, elevation=15.0, azimuth=5.0)
        assert asc.golfer_membership(inside, geometry) == pytest.approx(1.0)
        assert asc.golfer_membership(outside, geometry) < 0.05
        assert asc.golfer_membership(det(0, 38.0), geometry) == 0.0
        assert asc.golfer_membership(inside, asc.SceneGeometry(1.8)) == 0.0


class TestFieldOfView:
    geometry = asc.SceneGeometry(tee_range_m=40 * BIN_M)
    config = asc.TrackerConfig()

    def weight(self, d, phase, **kwargs):
        return asc.fov_weight(d, phase, self.geometry, self.config, bin_m=BIN_M, **kwargs)

    def test_waiting_is_broad_but_down_weights_the_golfer(self):
        d = det(0, 30.0)
        assert self.weight(d, ShotPhase.BACKGROUND_LEARNING) == 1.0
        golfer = self.weight(d, ShotPhase.BACKGROUND_LEARNING, golfer=1.0)
        assert golfer == pytest.approx(self.config.fov_floor)

    def test_never_zero(self):
        club = track_of(det(0, 20.0), det(1, 22.0))
        far = det(2, 60.0)
        for phase in ShotPhase:
            assert self.weight(far, phase, club=club, golfer=1.0) >= self.config.fov_floor

    def test_club_acquired_looks_at_the_prediction(self):
        club = track_of(det(0, 20.0), det(1, 22.0))
        near = self.weight(det(2, 24.0), ShotPhase.PRE_IMPACT_TRACKING, club=club)
        far = self.weight(det(2, 34.0), ShotPhase.PRE_IMPACT_TRACKING, club=club)
        assert near > 0.95 and far < 0.5

    def test_after_impact_the_ball_corridor(self):
        impact_us = 9 * FRAME_US
        reachable = det(11, 46.0)
        behind = det(11, 25.0)
        too_far = det(11, 70.0)
        for phase in (ShotPhase.IMPACT_WINDOW, ShotPhase.POST_IMPACT_TRACKING):
            assert self.weight(reachable, phase, impact_us=impact_us) == 1.0
            assert self.weight(behind, phase, impact_us=impact_us) == self.config.fov_floor
            assert self.weight(too_far, phase, impact_us=impact_us) == self.config.fov_floor

    def test_ball_corridor_direction_when_configured(self):
        geometry = asc.SceneGeometry(40 * BIN_M, ball_elevation_deg=12.0)
        up = det(11, 46.0, elevation=12.0)
        down = det(11, 46.0, elevation=-40.0)
        weights = [
            asc.fov_weight(
                d,
                ShotPhase.POST_IMPACT_TRACKING,
                geometry,
                self.config,
                bin_m=BIN_M,
                impact_us=9 * FRAME_US,
            )
            for d in (up, down)
        ]
        assert weights[0] > 0.95 > weights[1]

    def test_ball_reachable(self):
        assert asc.ball_reachable(
            det(9, 35.0), self.geometry, self.config, bin_m=BIN_M, impact_us=9 * FRAME_US
        )
        assert not asc.ball_reachable(
            det(9, 33.0), self.geometry, self.config, bin_m=BIN_M, impact_us=9 * FRAME_US
        )


class TestExtraction:
    def table(self, values):
        out = np.zeros(len(values), dtype=fr._OBS_DTYPE)  # pylint: disable=protected-access
        out["peak"] = values
        out["r1Re"] = 1.0
        return out

    def test_local_maxima_over_the_floor_strongest_first(self):
        values = [1, 1, 1, 50, 20, 1, 1, 80, 1, 1, 1, 1]
        found = asc.extract_detections(
            self.table(values),
            10,
            3,
            9000,
            stat="peak",
            snr=6.0,
            loop_period_s=135e-6,
            bin_m=BIN_M,
            max_detections=8,
        )
        assert [round(d.range_bin) for d in found] == [17, 13]
        assert found[0].snr == pytest.approx(80.0)
        assert found[0].local_bin == 7 and found[0].frame == 3

    def test_sub_bin_range_is_parabolic(self):
        found = asc.extract_detections(
            self.table([1, 1, 40, 80, 40, 1, 1]),
            0,
            0,
            0,
            stat="peak",
            snr=6.0,
            loop_period_s=135e-6,
            bin_m=BIN_M,
            max_detections=8,
        )
        assert found[0].range_bin == pytest.approx(3.0)

    def test_limit_and_tiny_windows(self):
        values = [1, 50, 1, 60, 1, 70, 1, 1, 1]
        found = asc.extract_detections(
            self.table(values),
            0,
            0,
            0,
            stat="peak",
            snr=6.0,
            loop_period_s=135e-6,
            bin_m=BIN_M,
            max_detections=2,
        )
        assert len(found) == 2
        assert (
            asc.extract_detections(
                self.table([5, 9]),
                0,
                0,
                0,
                stat="peak",
                snr=6.0,
                loop_period_s=135e-6,
                bin_m=BIN_M,
                max_detections=2,
            )
            == []
        )


GOLFER_BIN = 33.3


def scene(frames=20, impact=9, golfer_bin=GOLFER_BIN):
    """Club 20 -> 36 at 2 bins/frame, a bright standing golfer at 33.3, the ball
    leaving 40 at 3 bins/frame and the club at 1 bin/frame after impact."""
    out = []
    for f in range(frames):
        dets = [det(f, golfer_bin, snr=400.0, doppler=0.3)]
        if f < impact:
            dets.append(det(f, 20.0 + 2.0 * f, snr=60.0, doppler=5.0))
        else:
            k = f - impact
            dets.append(det(f, 40.0 + 3.0 * k, snr=30.0, doppler=-6.0))
            dets.append(det(f, 39.0 + 1.0 * k, snr=40.0, doppler=2.0))
        out.append(dets)
    return out


class TestHostTracker:
    def run(self, frames, *, impact=9, weights=None):
        config = asc.TrackerConfig(weights=weights or asc.ScoreWeights())
        tracker = asc.HostTracker(
            config,
            asc.SceneGeometry(tee_range_m=40 * BIN_M),
            bin_m=BIN_M,
            velocity_span_mps=SPAN,
            hotspot_bin=GOLFER_BIN,
            external_impact=True,
        )
        for f, dets in enumerate(frames):
            tracker.step(f, f * FRAME_US, dets, [0.0] * len(dets), impact_now=f == impact)
        return tracker

    def test_club_through_the_golfer_then_ball_and_club_out(self):
        tracker = self.run(scene())
        club_bins = [p.range_bin for p in tracker.club_points]
        ball_bins = [p.range_bin for p in tracker.ball_points]
        assert GOLFER_BIN not in club_bins and GOLFER_BIN not in ball_bins
        assert [p.frame for p in tracker.club_points if p.frame < 9] == list(range(9))
        assert ball_bins[:5] == [40.0, 43.0, 46.0, 49.0, 52.0]
        assert len(tracker.ball_points) == 11
        assert any(p.frame >= 12 for p in tracker.club_points)
        assert all(p.range_bin == 39.0 + (p.frame - 9) for p in tracker.club_points if p.frame >= 9)
        assert tracker.phase is ShotPhase.POST_IMPACT_TRACKING

    def test_club_coasts_through_frames_the_golfer_hides_it(self):
        frames = scene()
        for f in (7, 8):
            frames[f] = [d for d in frames[f] if d.range_bin == GOLFER_BIN]
        tracker = self.run(frames)
        assert tracker.club is not None
        assert [p.frame for p in tracker.club_points if p.frame < 9] == list(range(7))
        assert GOLFER_BIN not in [p.range_bin for p in tracker.club_points]
        assert len(tracker.ball_points) >= 8

    def test_amplitude_alone_would_follow_the_golfer(self):
        power_only = asc.ScoreWeights(0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        frames = scene()
        frames[6].append(det(6, 32.5, snr=5000.0, doppler=0.2))
        tracked = self.run(frames)
        assert 32.5 not in [p.range_bin for p in tracked.club_points]
        assert power_only.power == 1.0

    def test_the_impact_is_declared_from_the_club_without_a_trigger(self):
        config = asc.TrackerConfig()
        tracker = asc.HostTracker(
            config,
            asc.SceneGeometry(tee_range_m=38.5 * BIN_M),
            bin_m=BIN_M,
            velocity_span_mps=SPAN,
        )
        for f, dets in enumerate(scene()):
            tracker.step(f, f * FRAME_US, dets, [0.0] * len(dets))
        assert tracker.impact_frame is not None and tracker.impact_frame <= 9

    def test_velocity_hint_prefers_a_track_prediction(self):
        tracker = self.run(scene()[:5], impact=99)
        near = det(5, 30.0, doppler=-3.0)
        far = det(5, 60.0, doppler=-3.0)
        assert tracker.velocity_hint(near) == pytest.approx(
            tracker.club.predict(near.timestamp_us)[1]
        )
        assert tracker.velocity_hint(far) == -3.0


@pytest.fixture(scope="module")
def dump():
    return synth_shot_dump(
        n_frames=24,
        t_impact_s=12 * 4e-3,
        golfer_bin=27,
        golfer_amp=3000.0,
        ball_speed_ms=45.0,
        club_out_speed_ms=15.0,
        club_amp=600.0,
    )


class TestTrackDump:
    @pytest.mark.parametrize("beta", [0.0, 1.0])
    @pytest.mark.parametrize("impact", [12, None])
    def test_recovers_club_and_ball_through_the_golfer(self, dump, beta, impact):
        result = asc.track_dump(
            dump,
            asc.SceneGeometry(tee_range_m=1.372),
            clutter=ClutterConfig(beta=beta),
            impact_frame=impact,
        )
        club_pre = [p for p in result.club_points if p.frame < 12]
        assert len(club_pre) >= 10
        speeds = np.diff([p.range_m for p in club_pre]) / 0.004
        assert np.median(speeds) == pytest.approx(22.0, rel=0.15)
        ball = result.ball_points
        assert len(ball) >= 8
        ball_speed = asc.fitted_speed(ball)
        assert ball_speed == pytest.approx(45.0 * math.cos(math.radians(12.0)), rel=0.1)
        assert len(result.phases) == 24

    def test_capon_angles(self, dump):
        result = asc.track_dump(
            dump,
            asc.SceneGeometry(tee_range_m=1.372),
            config=asc.TrackerConfig(beamformer="capon"),
            impact_frame=12,
        )
        assert len(result.ball_points) >= 8

    def test_needs_a_range_snapshot(self):
        from openflight.iwr6843.dump import pack_dump  # pylint: disable=import-outside-toplevel

        raw = pack_dump(np.zeros((2, 6, 4, 16), dtype=complex), n_tx=3)
        with pytest.raises(ValueError):
            asc.track_dump(raw, asc.SceneGeometry(tee_range_m=1.0))
