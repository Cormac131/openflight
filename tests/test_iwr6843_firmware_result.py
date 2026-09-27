"""Tests for the IWR6843 shot result, firmware/iwr6843/l3_result.c, and its
host parser, openflight.iwr6843.shot_result.

The result is built from explicit shot, ball-track and launch structures so
each validation rule and each flag is exercised on its own; the packet the C
serialises is parsed by the host and every field compared.
"""

from __future__ import annotations

import ctypes
import json
import math

import pytest

from openflight.iwr6843 import firmware_host as fw, shot_result

DEG = math.pi / 180.0
M = {name: index for index, name in enumerate(fw.RESULT_METRIC_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def make_shot(lib, *, state="result", club_points=7, source=fw.SHOT_IMPACT_GEOMETRY, **delivery):
    shot = fw.Shot()
    cfg = fw.ShotCfg()
    lib.l3_shot_cfg_defaults(ctypes.byref(cfg))
    lib.l3_shot_init(ctypes.byref(shot), ctypes.byref(cfg))
    shot.state = fw.SHOT_STATE_NAMES.index(state)
    shot.impactTimestampUs = 23218
    shot.impactSource = source
    shot.clubPoints = club_points
    shot.ballOrigin = fw.Vec3(1.36, 0.0, 0.0)
    d = shot.delivery
    d.points = delivery.get("points", club_points)
    d.speedMps = delivery.get("speed", 40.0)
    d.pathRad = delivery.get("path_deg", 2.0) * DEG
    d.attackRad = delivery.get("attack_deg", -3.0) * DEG
    d.residualM = delivery.get("residual", 0.01)
    d.confidence = delivery.get("confidence", 0.8)
    d.speedValid = 1 if delivery.get("valid", True) else 0
    d.pathValid = 1 if delivery.get("angles", True) else 0
    d.attackValid = 1 if delivery.get("angles", True) else 0
    return shot


def make_ball(lib, *, confirmed=True, points=6, coasted=0):
    ball = fw.BallTrack()
    cfg = fw.BallTrackCfg()
    lib.l3_ball_track_cfg_defaults(ctypes.byref(cfg))
    lib.l3_ball_track_init(ctypes.byref(ball), ctypes.byref(cfg))
    ball.confirmed = 1 if confirmed else 0
    ball.core.count = points
    ball.counters[fw.BALL_TRACK_WHY_NAMES.index("coasted")] = coasted
    return ball


def make_launch(
    *,
    speed=60.0,
    hla_deg=1.0,
    vla_deg=12.0,
    points=6,
    residual=0.005,
    confidence=0.9,
    angles=True,
    valid=True,
):
    launch = fw.Launch()
    launch.points = points
    launch.speedMps = speed
    launch.radialSpeedMps = speed
    launch.hlaRad = hla_deg * DEG
    launch.vlaRad = vla_deg * DEG
    launch.residualM = residual
    launch.confidence = confidence
    launch.speedValid = 1 if valid else 0
    launch.hlaValid = 1 if (angles and valid) else 0
    launch.vlaValid = 1 if (angles and valid) else 0
    return launch


def build(lib, shot, ball, launch, *, shot_id=3, locked=True) -> fw.ShotResult:
    out = fw.ShotResult()
    lib.l3_result_build(
        ctypes.byref(shot),
        ctypes.byref(ball),
        ctypes.byref(launch),
        shot_id,
        1 if locked else 0,
        ctypes.byref(out),
    )
    return out


def quality(result) -> set[str]:
    return {name for name, bit in fw.QUALITY_FLAGS.items() if result.qualityFlags & bit}


def test_a_complete_shot_is_valid_with_every_core_metric_measured(lib):
    result = build(lib, make_shot(lib), make_ball(lib), make_launch())
    assert result.version == 1 and result.shotId == 3
    assert fw.RESULT_VERDICT_NAMES[result.verdict] == "valid"
    for name in (
        "ball_speed",
        "vertical_launch",
        "horizontal_launch",
        "club_speed",
        "club_path",
        "angle_of_attack",
        "impact_range",
    ):
        m = result.metric[M[name]]
        assert m.flags & fw.MEAS_VALID and m.flags & fw.MEAS_MEASURED, name
        assert not (m.flags & fw.MEAS_IMPLAUSIBLE), name
        assert result.validFlags & (1 << M[name])
    for name in ("spin_rate", "spin_axis"):
        assert not (result.metric[M[name]].flags & fw.MEAS_VALID)
        assert not (result.validFlags & (1 << M[name]))
    assert result.metric[M["ball_speed"]].value == pytest.approx(60.0)
    assert result.metric[M["vertical_launch"]].value == pytest.approx(12.0 * DEG)
    assert result.metric[M["club_path"]].value == pytest.approx(2.0 * DEG)
    assert result.metric[M["impact_range"]].value == pytest.approx(1.36)
    assert result.smash == pytest.approx(1.5)
    assert result.impactTimestampUs == 23218 and result.impactSource == fw.SHOT_IMPACT_GEOMETRY
    assert result.clubPoints == 7 and result.ballPoints == 6
    assert quality(result) == set(fw.QUALITY_FLAGS)


def test_ball_flight_without_a_club_is_partial_and_club_without_flight_too(lib):
    no_club = build(lib, make_shot(lib, valid=False), make_ball(lib), make_launch())
    assert fw.RESULT_VERDICT_NAMES[no_club.verdict] == "partial"
    assert not (no_club.validFlags & (1 << M["club_speed"]))
    assert no_club.smash == 0.0
    no_ball = build(
        lib, make_shot(lib), make_ball(lib, confirmed=False, points=0), make_launch(valid=False)
    )
    assert fw.RESULT_VERDICT_NAMES[no_ball.verdict] == "partial"
    assert not (no_ball.validFlags & (1 << M["ball_speed"]))
    assert "ball_from_origin" not in quality(no_ball)


def test_nothing_measured_is_invalid(lib):
    result = build(
        lib,
        make_shot(lib, state="ready", valid=False),
        make_ball(lib, confirmed=False, points=0),
        make_launch(valid=False),
    )
    assert fw.RESULT_VERDICT_NAMES[result.verdict] == "invalid"
    assert result.validFlags == 0 and "impact_identified" not in quality(result)


def test_impossible_smash_doubts_both_speeds(lib):
    result = build(lib, make_shot(lib, speed=20.0), make_ball(lib), make_launch(speed=60.0))
    assert result.smash == pytest.approx(3.0)
    assert result.metric[M["ball_speed"]].flags & fw.MEAS_IMPLAUSIBLE
    assert result.metric[M["club_speed"]].flags & fw.MEAS_IMPLAUSIBLE
    assert "smash_plausible" not in quality(result) and "speeds_plausible" not in quality(result)
    assert fw.RESULT_VERDICT_NAMES[result.verdict] == "invalid"


@pytest.mark.parametrize(
    "kwargs,metric",
    [
        ({"speed": 130.0}, "ball_speed"),
        ({"vla_deg": 70.0}, "vertical_launch"),
        ({"hla_deg": -50.0}, "horizontal_launch"),
    ],
)
def test_out_of_bounds_launch_values_are_flagged_not_clipped(lib, kwargs, metric):
    result = build(lib, make_shot(lib), make_ball(lib), make_launch(**kwargs))
    m = result.metric[M[metric]]
    assert m.flags & fw.MEAS_IMPLAUSIBLE
    assert m.value == pytest.approx(
        list(kwargs.values())[0] * (DEG if "deg" in list(kwargs)[0] else 1.0)
    )
    assert fw.RESULT_VERDICT_NAMES[result.verdict] != "valid"


def test_out_of_bounds_club_values_are_flagged(lib):
    result = build(
        lib, make_shot(lib, path_deg=40.0, attack_deg=25.0), make_ball(lib), make_launch()
    )
    assert result.metric[M["club_path"]].flags & fw.MEAS_IMPLAUSIBLE
    assert result.metric[M["angle_of_attack"]].flags & fw.MEAS_IMPLAUSIBLE
    assert "angles_plausible" not in quality(result)


def test_range_only_speeds_are_marked_radial(lib):
    result = build(lib, make_shot(lib, angles=False), make_ball(lib), make_launch(angles=False))
    assert result.metric[M["ball_speed"]].flags & fw.MEAS_RADIAL_ONLY
    assert result.metric[M["club_speed"]].flags & fw.MEAS_RADIAL_ONLY
    assert not (result.validFlags & (1 << M["club_path"]))
    assert not (result.validFlags & (1 << M["vertical_launch"]))
    assert fw.RESULT_VERDICT_NAMES[result.verdict] == "partial", "no launch angles"


def test_the_tee_standing_in_for_the_ball_is_recorded(lib):
    result = build(lib, make_shot(lib), make_ball(lib), make_launch(), locked=False)
    assert result.metric[M["ball_speed"]].flags & fw.MEAS_FALLBACK
    assert result.metric[M["impact_range"]].flags & fw.MEAS_FALLBACK
    assert not (result.metric[M["impact_range"]].flags & fw.MEAS_MEASURED)
    assert result.metric[M["impact_range"]].confidence == pytest.approx(0.5)
    assert "ball_locked" not in quality(result)
    assert fw.RESULT_VERDICT_NAMES[result.verdict] == "valid", "a fallback is still a shot"


def test_residuals_coasts_and_short_tracks_show_in_the_quality_flags(lib):
    scattered = build(lib, make_shot(lib, residual=0.1), make_ball(lib), make_launch())
    assert "residuals_ok" not in quality(scattered)
    assert fw.RESULT_VERDICT_NAMES[scattered.verdict] == "partial"
    coasted = build(lib, make_shot(lib), make_ball(lib, coasted=1), make_launch())
    assert "ball_continuous" not in quality(coasted) and "ball_from_origin" in quality(coasted)
    short = build(lib, make_shot(lib, club_points=2, points=2), make_ball(lib), make_launch())
    assert not (short.validFlags & (1 << M["club_speed"]))
    assert "club_track" not in quality(short)
    gate = build(lib, make_shot(lib, source=fw.SHOT_IMPACT_GATE), make_ball(lib), make_launch())
    assert "geometric_impact" not in quality(gate) and "impact_identified" in quality(gate)


def test_names_and_text_formats(lib):
    for index, name in enumerate(fw.RESULT_METRIC_NAMES):
        assert lib.l3_result_metric_name(index).decode() == name
    assert lib.l3_result_metric_name(9).decode() == "?"
    for index, name in enumerate(fw.RESULT_VERDICT_NAMES):
        assert lib.l3_result_verdict_name(index).decode() == name
    result = build(lib, make_shot(lib), make_ball(lib), make_launch())
    text = fw.c_text(lib.l3_result_format, ctypes.byref(result), cap=240)
    assert text.startswith("result v1 shot=3 verdict=valid valid=0x")
    assert " impact=23218 source=geometry club=7 ball=6 smash=1.50" in text
    speed = fw.c_text(lib.l3_result_format_metric, ctypes.byref(result), M["ball_speed"])
    assert speed == "  ball_speed=60.00 conf=0.90 flags=measured"
    vla = fw.c_text(lib.l3_result_format_metric, ctypes.byref(result), M["vertical_launch"])
    assert vla == "  vertical_launch=12.00deg conf=0.90 flags=measured"
    spin = fw.c_text(lib.l3_result_format_metric, ctypes.byref(result), M["spin_rate"])
    assert spin == "  spin_rate=- conf=0.00 flags=none"
    fallback = build(lib, make_shot(lib, angles=False), make_ball(lib), make_launch(), locked=False)
    club = fw.c_text(lib.l3_result_format_metric, ctypes.byref(fallback), M["club_speed"])
    assert club.endswith("flags=measured,radial")
    rng = fw.c_text(lib.l3_result_format_metric, ctypes.byref(fallback), M["impact_range"])
    assert rng.endswith("flags=inferred,tee")


def test_packet_is_one_hundred_little_endian_bytes_the_host_parses_back(lib):
    result = build(lib, make_shot(lib), make_ball(lib), make_launch(hla_deg=-1.5), shot_id=7)
    buffer = ctypes.create_string_buffer(fw.RESULT_PACKET_BYTES)
    assert lib.l3_result_serialize(ctypes.byref(result), buffer, fw.RESULT_PACKET_BYTES) == 100
    assert lib.l3_result_serialize(ctypes.byref(result), buffer, 99) == 0
    packet = shot_result.parse_packet(buffer.raw)
    assert packet.version == 1 and packet.shot_id == 7 and packet.verdict == "valid"
    assert packet.impact_timestamp_us == 23218 and packet.impact_source == "geometry"
    assert packet.club_points == 7 and packet.ball_points == 6
    assert packet.smash == pytest.approx(1.5)
    assert packet["ball_speed"].value == pytest.approx(60.0)
    assert packet["horizontal_launch"].value == pytest.approx(-1.5, abs=1e-4), "degrees on the host"
    assert packet["club_path"].value == pytest.approx(2.0, abs=1e-4)
    assert packet["impact_range"].value == pytest.approx(1.36)
    assert packet["spin_rate"].value is None and packet["spin_rate"].label == "-"
    assert packet["ball_speed"].label == "MEASURED" and packet["ball_speed"].usable
    assert packet.quality == set(fw.QUALITY_FLAGS)
    assert packet["ball_speed"].confidence == pytest.approx(0.9)
    # The hex line the CLI prints round-trips through the same parser.
    hex_text = fw.c_text(lib.l3_result_format_hex, ctypes.byref(result), cap=240)
    assert len(hex_text) == 200
    assert shot_result.parse_hex(f"packet {hex_text}") == packet
    reply = (
        f"result v1 shot=7 ...\n  ball_speed=60.00 ...\npacket {hex_text[:100]}\n"
        f"packet+ {hex_text[100:]}\nDone\n"
    )
    assert shot_result.parse_result_reply(reply) == packet
    assert shot_result.parse_result_reply("Done\n") is None


def test_host_parser_reads_implausible_radial_and_fallback_from_the_packet(lib):
    result = build(
        lib,
        make_shot(lib, speed=20.0, angles=False),
        make_ball(lib),
        make_launch(speed=60.0),
        locked=False,
    )
    buffer = ctypes.create_string_buffer(fw.RESULT_PACKET_BYTES)
    lib.l3_result_serialize(ctypes.byref(result), buffer, fw.RESULT_PACKET_BYTES)
    packet = shot_result.parse_packet(buffer.raw)
    assert packet.verdict == "invalid"
    assert packet["ball_speed"].implausible and packet["club_speed"].implausible
    assert not packet["ball_speed"].usable
    assert packet["club_speed"].radial_only and not packet["ball_speed"].radial_only
    assert packet["ball_speed"].fallback and "ball_locked" not in packet.quality


def test_packet_to_dict_keeps_provenance_beside_every_metric(lib):
    result = build(lib, make_shot(lib), make_ball(lib), make_launch(hla_deg=-1.5), shot_id=7)
    buffer = ctypes.create_string_buffer(fw.RESULT_PACKET_BYTES)
    lib.l3_result_serialize(ctypes.byref(result), buffer, fw.RESULT_PACKET_BYTES)
    packet = shot_result.parse_packet(buffer.raw)

    payload = packet.to_dict()

    assert payload["version"] == 1 and payload["shot_id"] == 7 and payload["verdict"] == "valid"
    assert payload["impact_source"] == "geometry" and payload["impact_timestamp_us"] == 23218
    assert payload["club_points"] == 7 and payload["ball_points"] == 6
    assert payload["smash"] == pytest.approx(1.5)
    assert payload["quality"] == sorted(fw.QUALITY_FLAGS)
    assert set(payload["metrics"]) == set(fw.RESULT_METRIC_NAMES)
    ball = payload["metrics"]["ball_speed"]
    assert ball["value"] == pytest.approx(60.0) and ball["label"] == "MEASURED"
    assert ball["measured"] and ball["usable"] and not ball["radial_only"]
    spin = payload["metrics"]["spin_rate"]
    assert spin["value"] is None and spin["label"] == "-" and not spin["usable"]
    assert json.dumps(payload), "the record is JSON-serialisable as it stands"


def test_host_parser_rejects_wrong_sizes_versions_and_bad_hex():
    with pytest.raises(ValueError, match="bytes"):
        shot_result.parse_packet(b"\x00" * 10)
    bad_version = bytearray(100)
    bad_version[0] = 2
    with pytest.raises(ValueError, match="version"):
        shot_result.parse_packet(bytes(bad_version))
    with pytest.raises(ValueError, match="hex"):
        shot_result.parse_hex("packet zz")
