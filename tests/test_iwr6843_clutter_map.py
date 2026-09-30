"""Golfer-clutter map, its freeze state machine and the replay hook (clutter_map.py)."""

from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import beamforming as bf, firmware_replay as fr
from openflight.iwr6843.clutter_map import (
    BackgroundEstimator,
    ClutterConfig,
    ClutterMap,
    ClutterPhaseMachine,
    ClutterSuppressor,
    RangeAngleClutterMap,
    ShotPhase,
    plausible_club_approach,
    power_db,
)
from openflight.iwr6843.dump import parse_dump


def step(machine, club=False, impact=False, done=False):
    return machine.step(club_active=club, impact=impact, shot_done=done)


class TestConfig:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"beta": -0.1},
            {"beta": 2.0},
            {"mode": "mean"},
            {"history": 0},
            {"alpha": 0.0},
            {"alpha": 1.5},
            {"min_updates": 0},
            {"release_frames": -1},
            {"impact_window_frames": -1},
            {"approach_points": 1},
            {"approach_max_gap_frames": 0},
            {"approach_min_mps": 0.0},
            {"approach_min_mps": 80.0},
        ],
    )
    def test_rejects(self, kwargs):
        with pytest.raises(ValueError):
            ClutterConfig(**kwargs)

    def test_defaults_are_valid(self):
        assert ClutterConfig().beta == 0.8


class TestPhaseMachine:
    def test_learns_until_a_club_approach(self):
        machine = ClutterPhaseMachine(ClutterConfig())
        assert machine.phase is ShotPhase.IDLE and machine.learning
        assert step(machine) is ShotPhase.BACKGROUND_LEARNING
        assert machine.learning
        assert step(machine, club=True) is ShotPhase.CLUB_APPROACH_DETECTED
        assert not machine.learning
        assert step(machine, club=True) is ShotPhase.PRE_IMPACT_TRACKING

    def test_whole_shot(self):
        machine = ClutterPhaseMachine(ClutterConfig(impact_window_frames=2))
        step(machine)
        step(machine, club=True)
        step(machine, club=True)
        assert step(machine, impact=True) is ShotPhase.IMPACT_WINDOW
        assert step(machine, impact=True) is ShotPhase.IMPACT_WINDOW
        assert step(machine, impact=True) is ShotPhase.POST_IMPACT_TRACKING
        assert not machine.learning
        assert step(machine, impact=True, done=True) is ShotPhase.SHOT_COMPLETE
        assert step(machine) is ShotPhase.BACKGROUND_LEARNING
        assert machine.learning

    def test_stays_frozen_through_a_short_club_dropout(self):
        machine = ClutterPhaseMachine(ClutterConfig(release_frames=3))
        step(machine)
        step(machine, club=True)
        assert step(machine) is ShotPhase.CLUB_APPROACH_DETECTED
        assert step(machine) is ShotPhase.CLUB_APPROACH_DETECTED
        assert step(machine, club=True) is ShotPhase.PRE_IMPACT_TRACKING
        assert not machine.learning

    def test_releases_after_a_waggle(self):
        machine = ClutterPhaseMachine(ClutterConfig(release_frames=2))
        step(machine)
        step(machine, club=True)
        step(machine)
        assert step(machine) is ShotPhase.BACKGROUND_LEARNING

    def test_release_zero_never_releases_before_impact(self):
        machine = ClutterPhaseMachine(ClutterConfig(release_frames=0))
        step(machine)
        step(machine, club=True)
        for _ in range(20):
            step(machine)
        assert machine.phase is ShotPhase.CLUB_APPROACH_DETECTED

    def test_impact_without_an_approach_still_freezes(self):
        machine = ClutterPhaseMachine(ClutterConfig())
        step(machine)
        assert step(machine, impact=True) is ShotPhase.IMPACT_WINDOW
        assert not machine.learning

    def test_done_while_learning_is_ignored(self):
        machine = ClutterPhaseMachine(ClutterConfig())
        step(machine)
        assert step(machine, done=True) is ShotPhase.BACKGROUND_LEARNING


class TestBackgroundEstimator:
    def test_median_ignores_a_transient(self):
        est = BackgroundEstimator(ClutterConfig(mode="median", history=5, min_updates=1), n_bins=8)
        for value in (10.0, 10.0, 500.0, 10.0, 10.0):
            est.update(2, np.array([value, 1.0]))
        assert est.background(2, 2).tolist() == [10.0, 1.0]

    def test_median_ring_forgets_old_frames(self):
        est = BackgroundEstimator(ClutterConfig(mode="median", history=2, min_updates=1), n_bins=4)
        for value in (1.0, 1.0, 9.0, 9.0):
            est.update(0, np.array([value]))
        assert est.background(0, 1)[0] == 9.0

    def test_ema(self):
        est = BackgroundEstimator(ClutterConfig(mode="ema", alpha=0.5, min_updates=1), n_bins=4)
        est.update(1, np.array([8.0]))
        est.update(1, np.array([4.0]))
        assert est.background(1, 1)[0] == pytest.approx(6.0)

    def test_unlearned_bins_subtract_nothing(self):
        est = BackgroundEstimator(ClutterConfig(min_updates=3), n_bins=8)
        est.update(0, np.array([5.0, 5.0]))
        est.update(1, np.array([5.0]))
        est.update(1, np.array([5.0]))
        assert est.background(0, 3).tolist() == [0.0, 5.0, 0.0]

    def test_windows_move_between_frames(self):
        est = BackgroundEstimator(ClutterConfig(min_updates=1, history=4), n_bins=16)
        est.update(3, np.array([1.0, 2.0, 3.0]))
        est.update(5, np.array([7.0, 8.0]))
        assert est.background(3, 4).tolist() == [1.0, 2.0, 5.0, 8.0]

    @pytest.mark.parametrize("start, count", [(-1, 2), (7, 2), (0, 9)])
    def test_rejects_bins_outside_the_map(self, start, count):
        est = BackgroundEstimator(ClutterConfig(), n_bins=8)
        with pytest.raises(ValueError):
            est.update(start, np.zeros(count))

    def test_rejects_the_wrong_cell_shape(self):
        est = BackgroundEstimator(ClutterConfig(), cell_shape=(3,), n_bins=8)
        with pytest.raises(ValueError):
            est.update(0, np.zeros((2, 4)))

    @pytest.mark.parametrize("mode", ["median", "ema"])
    def test_cells(self, mode):
        est = BackgroundEstimator(
            ClutterConfig(mode=mode, min_updates=1), cell_shape=(3,), n_bins=8
        )
        est.update(2, np.array([[1.0, 2.0, 3.0]]))
        assert est.background(2, 1).tolist() == [[1.0, 2.0, 3.0]]
        assert est.background(3, 1).tolist() == [[0.0, 0.0, 0.0]]


class TestClutterMap:
    def learned(self, beta=0.8):
        cmap = ClutterMap(ClutterConfig(beta=beta, min_updates=1), n_bins=16)
        cmap.update(4, np.array([100.0, 10.0, 0.0]))
        return cmap

    def test_suppress(self):
        out = self.learned().suppress(4, np.array([200.0, 5.0, 3.0]))
        assert out.tolist() == pytest.approx([120.0, 0.0, 3.0])

    def test_gains_square_to_the_suppressed_power(self):
        cmap = self.learned()
        stats = np.array([200.0, 5.0, 3.0])
        assert np.square(cmap.gains(4, stats)) * stats == pytest.approx(cmap.suppress(4, stats))

    def test_zero_power_keeps_unit_gain(self):
        assert self.learned().gains(4, np.array([0.0, 0.0, 0.0])).tolist() == [1.0, 1.0, 1.0]

    def test_beta_zero_changes_nothing(self):
        stats = np.array([200.0, 5.0, 3.0])
        assert self.learned(beta=0.0).gains(4, stats).tolist() == [1.0, 1.0, 1.0]


class TestComplexBackgroundIsRedundant:
    """Why the map is of power, not samples: subtracting any complex
    background constant over a frame's loops leaves the burst-MTI residual,
    and so every observation, exactly as it was."""

    def test_observations_unchanged(self):
        rng = np.random.default_rng(3)
        cube = rng.normal(size=(2, 36, 4, 20)) + 1j * rng.normal(size=(2, 36, 4, 20))
        background = rng.normal(size=(1, 3, 4, 20)) + 1j * rng.normal(size=(1, 3, 4, 20))
        cleaned = cube - np.tile(background, (1, 12, 1, 1))
        before = fr.bin_observation_table(cube, 1, 0, 20, 3)
        after = fr.bin_observation_table(cleaned, 1, 0, 20, 3)
        for name in ("energy", "peak", "loop0", "r1Re", "r1Im"):
            assert after[name] == pytest.approx(before[name])


def point(frame, range_m, frame_us=3000):
    return SimpleNamespace(frame=frame, timestamp_us=frame * frame_us, range_m=range_m)


class TestPlausibleClubApproach:
    config = ClutterConfig()

    def test_rising_at_club_speed(self):
        points = [point(f, 1.0 + 0.09 * f) for f in range(3)]  # 30 m/s
        assert plausible_club_approach(points, 3, self.config)

    def test_too_few_points(self):
        assert not plausible_club_approach([point(0, 1.0), point(1, 1.09)], 2, self.config)

    def test_a_standing_return(self):
        assert not plausible_club_approach([point(f, 1.8) for f in range(3)], 3, self.config)

    def test_receding(self):
        points = [point(f, 1.5 - 0.09 * f) for f in range(3)]
        assert not plausible_club_approach(points, 3, self.config)

    def test_faster_than_a_club(self):
        points = [point(f, 1.0 + 0.3 * f) for f in range(3)]  # 100 m/s
        assert not plausible_club_approach(points, 3, self.config)

    def test_stale(self):
        points = [point(f, 1.0 + 0.09 * f) for f in range(3)]
        assert not plausible_club_approach(points, 6, self.config)

    def test_gap_too_long(self):
        points = [point(0, 1.0), point(4, 1.36), point(5, 1.45)]
        assert not plausible_club_approach(points, 6, self.config)


class TestRangeAngleMap:
    def make(self):
        grid = np.radians([-20.0, 0.0, 20.0])
        ra = RangeAngleClutterMap(ClutterConfig(min_updates=1), grid, n_bins=8)
        ra.update(3, np.array([[1.0, 2.0, 8.0], [0.0, 0.0, 0.0]]), [complex(0, -1), 0j])
        return ra

    def test_angular_fraction(self):
        ra = self.make()
        assert ra.angular_fraction(3, math.radians(19.0)) == 1.0
        assert ra.angular_fraction(3, 0.0) == pytest.approx(0.25)
        assert ra.angular_fraction(4, 0.0) == 0.0

    def test_dominant_direction(self):
        elevation, azimuth = self.make().dominant_direction(3, 2)
        assert math.degrees(elevation) == pytest.approx(20.0)
        assert math.degrees(azimuth) == pytest.approx(30.0)

    def test_unlearned_has_no_direction(self):
        assert self.make().dominant_direction(5, 2) is None


class TestSuppressor:
    def stats(self, cube, frame, count, n_tx):
        return fr.bin_observation_table(cube, frame, 0, count, n_tx)["peak"]

    def test_a_frame_is_learned_only_when_the_next_step_confirms_no_club(self):
        rng = np.random.default_rng(0)
        cube = rng.normal(size=(4, 36, 4, 10)) + 1j * rng.normal(size=(4, 36, 4, 10))
        config = ClutterConfig(min_updates=1)
        sup = ClutterSuppressor(config, self.stats, n_bins=32)
        sup.frame(cube, 0, 5, 10, 3, club_active=False, impact=False, shot_done=False)
        assert sup.map.estimator.updates.sum() == 0
        sup.frame(cube, 1, 5, 10, 3, club_active=True, impact=False, shot_done=False)
        assert sup.map.estimator.updates.sum() == 0  # frame 0 held the club: discarded
        assert sup.phases == ["background_learning", "club_approach_detected"]

    def test_learned_frames_suppress_later_ones_in_place(self):
        rng = np.random.default_rng(1)
        base = rng.normal(size=(1, 36, 4, 10)) + 1j * rng.normal(size=(1, 36, 4, 10))
        cube = np.repeat(base, 5, axis=0)
        sup = ClutterSuppressor(ClutterConfig(beta=1.0, min_updates=2), self.stats, n_bins=32)
        before = self.stats(cube, 4, 10, 3).copy()
        for frame in range(5):
            sup.frame(cube, frame, 0, 10, 3, club_active=False, impact=False, shot_done=False)
        assert np.all(self.stats(cube, 4, 10, 3) < 1e-6 * before)
        assert len(sup.gains) == 5 and sup.gains[0][1] == (1.0,) * 10

    def test_an_empty_window_records_no_gains(self):
        sup = ClutterSuppressor(ClutterConfig(), self.stats, n_bins=32)
        sup.frame(
            np.zeros((1, 36, 4, 4), dtype=complex),
            0,
            7,
            0,
            3,
            club_active=False,
            impact=False,
            shot_done=False,
        )
        assert sup.gains == [(7, ())] and sup.pending is None


@pytest.fixture(scope="module")
def golfer_shot():
    return synth_shot_dump(
        n_frames=24,
        t_impact_s=12 * 4e-3,
        golfer_bin=27,
        golfer_amp=3000.0,
        ball_speed_ms=45.0,
    )


class TestReplayHook:
    def test_beta_zero_replays_exactly_as_without(self, golfer_shot):
        config = fr.ReplayConfig(tee_bin=29)
        plain = fr.replay_dump(golfer_shot, config)
        zero = fr.replay_dump(golfer_shot, replace(config, clutter=ClutterConfig(beta=0.0)))
        assert [(p.frame, p.range_bin) for p in zero.points] == [
            (p.frame, p.range_bin) for p in plain.points
        ]
        assert zero.fired_frame == plain.fired_frame
        assert len(zero.clutter_phases) == len(zero.frames)
        assert all(g == 1.0 for _, gains in zero.clutter_seen_gains for g in gains)

    def test_suppression_lowers_the_golfer_the_trackers_see(self, golfer_shot):
        config = fr.ReplayConfig(tee_bin=29, clutter=ClutterConfig(beta=1.0, min_updates=2))
        result = fr.replay_dump(golfer_shot, config)
        start, gains = result.clutter_seen_gains[6]
        assert gains[27 - start] < 0.5
        assert len(result.clutter_background) == 128
        assert result.clutter_background[27] > 10 * np.median(result.clutter_background)

    def test_does_not_modify_the_callers_dump(self, golfer_shot):
        _, cube_before = parse_dump(golfer_shot)
        fr.replay_dump(golfer_shot, fr.ReplayConfig(tee_bin=29, clutter=ClutterConfig(beta=1.0)))
        _, cube_after = parse_dump(golfer_shot)
        assert np.array_equal(cube_before, cube_after)

    def test_golfer_direction_is_learned(self, golfer_shot):
        meta, cube = parse_dump(golfer_shot)
        ra = RangeAngleClutterMap(ClutterConfig(min_updates=1), bf.GRID_RAD, 128)
        for frame in range(6):
            residual = bf.residual_at(cube, frame, 27, 3)
            chirp = bf.chirp_phase_rad(bf.lag1_phase_rad(residual), 3, 0.0)
            elements, phasors = bf.elevation_snapshots(residual, chirp)
            ra.update(27, bf.bartlett_spectrum(elements)[None, :], [complex(phasors.sum())])
        elevation, azimuth = ra.dominant_direction(27, 1)
        assert math.degrees(elevation) == pytest.approx(-15.0, abs=1.5)
        assert math.degrees(azimuth) == pytest.approx(-30.0, abs=3.0)


def test_power_db_floors():
    assert power_db(100.0) == pytest.approx(20.0)
    assert math.isfinite(power_db(0.0))
