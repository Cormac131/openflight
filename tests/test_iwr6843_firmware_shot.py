"""Tests for the IWR6843 shot state machine, firmware/iwr6843/l3_shot.c.

Every transition in l3_shot.h is driven with explicit inputs, and the IMPACT
freeze is checked to copy the ball origin, delivery, impact time and club
trajectory so nothing after impact reads a live tracker.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

STATE = {name: index for index, name in enumerate(fw.SHOT_STATE_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


class Machine:
    def __init__(self, lib, **overrides):
        self.lib = lib
        cfg = fw.ShotCfg()
        lib.l3_shot_cfg_defaults(ctypes.byref(cfg))
        for name, value in overrides.items():
            setattr(cfg, name, value)
        self.shot = fw.Shot()
        lib.l3_shot_init(ctypes.byref(self.shot), ctypes.byref(cfg))
        self.frame = 0

    def step(self, **inputs) -> str:
        self.frame += 1
        inp = fw.ShotInput()
        for name, value in inputs.items():
            setattr(inp, name, value)
        state = self.lib.l3_shot_update(ctypes.byref(self.shot), ctypes.byref(inp), self.frame)
        return fw.SHOT_STATE_NAMES[state]

    @property
    def state(self) -> str:
        return fw.SHOT_STATE_NAMES[self.shot.state]


def club_track(lib, points: int) -> fw.ClubTrack:
    cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(cfg))
    for frame in range(1, points + 1):
        target = fw.TargetObs()
        target.frame = frame
        target.timestampUs = frame * 3000
        target.rangeBin = 20.0 + 2.0 * frame
        target.peakBin = int(target.rangeBin)
        target.confidence = 0.9
        lib.l3_track_update(ctypes.byref(track), ctypes.byref(target), 1, frame, frame * 3000)
    return track


def test_defaults_let_the_tee_stand_in_and_wait_a_post_movie(lib):
    cfg = fw.ShotCfg()
    lib.l3_shot_cfg_defaults(ctypes.byref(cfg))
    assert cfg.requireBall == 0 and cfg.ballTrackFrames == 16
    m = Machine(lib)
    assert m.state == "waiting_for_ball" and m.shot.entries[STATE["waiting_for_ball"]] == 1


def test_the_full_sequence_of_a_shot(lib):
    m = Machine(lib, requireBall=1)
    assert m.step() == "waiting_for_ball"
    assert m.step(ballLocked=1) == "ready"
    assert m.step(ballLocked=1, clubActive=1, clubPoints=1) == "club_acquire"
    assert m.step(ballLocked=1, clubActive=1, clubPoints=2) == "club_track"
    assert (
        m.step(ballLocked=1, clubActive=1, clubPoints=6, rangeFired=1, impactTimestampUs=12345)
        == "impact"
    )
    assert lib.l3_shot_wants_departing(ctypes.byref(m.shot)) == 1
    assert m.step() == "impact", "no post frame yet"
    assert m.step(postFrame=1) == "ball_track"
    assert m.step(postFrame=1) == "ball_track"
    assert m.step(postFrame=1, ballTrackDone=1) == "solve"
    assert m.step() == "solve"
    assert m.step(solved=1) == "result"
    assert m.step(solved=1) == "result", "holds until rearm"
    assert m.shot.transitions == 7
    assert m.shot.impactSource == fw.SHOT_IMPACT_RANGE and m.shot.impactTimestampUs == 12345
    assert m.shot.postFrames == 3


def test_before_impact_the_club_is_the_target_and_after_it_the_ball(lib):
    m = Machine(lib)
    for _ in range(3):
        assert lib.l3_shot_wants_departing(ctypes.byref(m.shot)) == 0
        m.step(clubActive=1, clubPoints=3)
    m.step(clubActive=1, clubPoints=4, rangeFired=1)
    assert lib.l3_shot_wants_departing(ctypes.byref(m.shot)) == 1
    assert m.shot.impactSource == fw.SHOT_IMPACT_RANGE


def test_without_a_required_ball_the_tee_stands_in_and_ready_is_immediate(lib):
    m = Machine(lib)
    assert m.step() == "ready"
    assert m.step(clubActive=1, clubPoints=1) == "club_acquire"


def test_losing_the_ball_or_the_club_falls_back(lib):
    m = Machine(lib, requireBall=1)
    m.step(ballLocked=1)
    m.step(ballLocked=1, clubActive=1, clubPoints=3)
    assert m.state == "club_track"
    assert m.step(ballLocked=1, clubActive=0) == "ready", "a dropped track"
    m.step(ballLocked=1, clubActive=1, clubPoints=1)
    assert m.step(ballLocked=0, clubActive=1, clubPoints=2) == "waiting_for_ball", "the ball left"
    assert m.shot.entries[STATE["ready"]] == 2


def test_impact_can_fire_from_ready_or_acquire(lib):
    m = Machine(lib)
    m.step()
    assert m.step(rangeFired=1) == "impact"
    m = Machine(lib)
    m.step()
    m.step(clubActive=1, clubPoints=1)
    assert m.step(clubActive=1, clubPoints=1, rangeFired=1) == "impact"
    assert m.shot.impactSource == fw.SHOT_IMPACT_RANGE


def test_impact_freezes_origin_delivery_time_and_club_trajectory(lib):
    m = Machine(lib)
    m.step()
    track = club_track(lib, 7)
    delivery = fw.Delivery()
    delivery.speedMps = 40.5
    delivery.speedValid = 1
    delivery.points = 7
    origin = fw.Vec3(1.36, 0.02, -0.11)
    state = m.step(
        clubActive=1,
        clubPoints=7,
        rangeFired=1,
        impactTimestampUs=23218,
        ballPosition=origin,
        delivery=ctypes.pointer(delivery),
        club=ctypes.pointer(track),
    )
    assert state == "impact" and m.shot.impactFrame == 2
    assert (m.shot.ballOrigin.x, m.shot.ballOrigin.y, m.shot.ballOrigin.z) == pytest.approx(
        (1.36, 0.02, -0.11)
    )
    assert m.shot.delivery.speedMps == pytest.approx(40.5) and m.shot.delivery.points == 7
    assert m.shot.clubPoints == 7
    assert [p.rangeBin for p in m.shot.clubTrajectory[:7]] == [22.0 + 2.0 * i for i in range(7)]
    # The live track moving on afterwards does not touch the frozen copy.
    target = fw.TargetObs()
    target.frame, target.timestampUs, target.rangeBin, target.peakBin, target.confidence = (
        8,
        24000,
        36.0,
        36,
        0.9,
    )
    lib.l3_track_update(ctypes.byref(track), ctypes.byref(target), 1, 8, 24000)
    assert m.shot.clubPoints == 7 and m.shot.clubTrajectory[6].rangeBin == 34.0


def test_ball_track_ends_when_the_post_movie_runs_out(lib):
    m = Machine(lib, ballTrackFrames=4)
    m.step()
    m.step(rangeFired=1)
    for _ in range(3):
        m.step(postFrame=1)
    assert m.state == "ball_track" and m.shot.postFrames == 3
    assert m.step(postFrame=1) == "solve"


def test_rearm_returns_to_waiting_and_keeps_the_history(lib):
    m = Machine(lib)
    m.step()
    m.step(rangeFired=1, impactTimestampUs=99)
    lib.l3_shot_rearm(ctypes.byref(m.shot))
    assert m.state == "waiting_for_ball"
    assert m.shot.impactTimestampUs == 0 and m.shot.impactSource == 0
    assert m.shot.entries[STATE["waiting_for_ball"]] == 2
    assert m.shot.entries[STATE["impact"]] == 1
    assert m.shot.transitions == 3
    assert m.shot.cfg.ballTrackFrames == 16


def test_state_names_and_format(lib):
    for index, name in enumerate(fw.SHOT_STATE_NAMES):
        assert lib.l3_shot_state_name(index).decode() == name
    assert lib.l3_shot_state_name(42).decode() == "?"
    m = Machine(lib)
    m.step()
    text = fw.c_text(lib.l3_shot_format, ctypes.byref(m.shot))
    assert text.startswith(
        "shot state=ready since=1 impact=- source=none origin=0.00,0.00,0.00 club=0"
    )
    m.step(rangeFired=1, impactTimestampUs=23218, ballPosition=fw.Vec3(1.36, 0.0, 0.0))
    text = fw.c_text(lib.l3_shot_format, ctypes.byref(m.shot))
    assert "state=impact since=2 impact=23218 source=range origin=1.36,0.00,0.00" in text
    assert text.endswith("post=0 transitions=2")


def ready_club_tracking_shot(lib) -> fw.Shot:
    cfg = fw.ShotCfg()
    lib.l3_shot_cfg_defaults(ctypes.byref(cfg))
    shot = fw.Shot()
    lib.l3_shot_init(ctypes.byref(shot), ctypes.byref(cfg))
    for frame, points in enumerate((0, 1, 3)):
        shot_in = fw.ShotInput()
        shot_in.ballLocked = 1
        shot_in.clubActive = 1 if points else 0
        shot_in.clubPoints = points
        lib.l3_shot_update(ctypes.byref(shot), ctypes.byref(shot_in), frame)
    assert fw.SHOT_STATE_NAMES[shot.state] == "club_track"
    return shot


def test_range_fire_enters_impact_with_the_range_source(lib):
    shot = ready_club_tracking_shot(lib)  # the file's existing helper that reaches club_track
    shot_in = fw.ShotInput()
    shot_in.ballLocked = 1
    shot_in.clubActive = 1
    shot_in.clubPoints = 5
    shot_in.rangeFired = 1
    shot_in.impactTimestampUs = 30_000
    state = lib.l3_shot_update(ctypes.byref(shot), ctypes.byref(shot_in), 10)
    assert fw.SHOT_STATE_NAMES[state] == "impact"
    assert shot.impactSource == fw.SHOT_IMPACT_RANGE
    assert shot.impactTimestampUs == 30_000
    text = fw.c_text(lib.l3_shot_format, ctypes.byref(shot), cap=240)
    assert "source=range" in text


def test_the_shot_machine_has_only_the_range_impact_input():
    """The range gate and the geometric detector were removed (2026-09-30);
    their impactSource bits (1 and 2) stay reserved so result packets from
    older firmware still decode."""
    inputs = {name for name, _type in fw.ShotInput._fields_}
    assert "rangeFired" in inputs
    assert not inputs & {"gateFired", "geometricFired"}
    assert (fw.SHOT_IMPACT_GATE, fw.SHOT_IMPACT_GEOMETRY, fw.SHOT_IMPACT_RANGE) == (1, 2, 4)
    assert not hasattr(fw, "FIRE_MODE_NAMES")


def test_there_is_no_fire_source_choice_left(lib):
    assert not hasattr(lib, "l3_shot_fire_sources")
