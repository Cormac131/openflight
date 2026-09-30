"""Tests for openflight.iwr6843.datasets and the replay manifest expectations."""

from __future__ import annotations

import json

import pytest

from openflight.iwr6843.datasets import (
    LABELS,
    RadarPosition,
    Reference,
    ShotRecord,
    load_dataset,
    reference_error,
)
from openflight.iwr6843.firmware_replay import (
    Expectation,
    recording_configs,
    recording_expectations,
)


def record(**overrides) -> ShotRecord:
    base = dict(
        capture="shot_001.l3dump",
        firmware_commit="1ed49fb",
        radar_config="iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
        radar_position=RadarPosition(behind_ball_m=1.575, height_m=0.152, pitch_deg=10.4),
        club="driver",
        reference=Reference(
            "trackman", ball_speed_mph=152.3, vertical_launch_deg=11.7, club_speed_mph=104.0
        ),
        labels=("driver", "straight"),
        environment={"temperature_c": 18.5},
        tee_bin=34,
    )
    base.update(overrides)
    return ShotRecord(**base)


def test_record_round_trips_through_json():
    text = record().to_json()
    back = ShotRecord.from_json(text)
    assert back == record()
    assert json.loads(text)["schema_version"] == 1


def test_from_json_validates_labels_required_keys_and_the_schema():
    raw = json.loads(record().to_json())
    raw["labels"] = ["driver", "banana"]
    with pytest.raises(ValueError, match="unknown label"):
        ShotRecord.from_json(json.dumps(raw))
    raw = json.loads(record().to_json())
    del raw["reference"]
    with pytest.raises(ValueError, match="reference"):
        ShotRecord.from_json(json.dumps(raw))
    raw = json.loads(record().to_json())
    raw["schema_version"] = 7
    with pytest.raises(ValueError, match="schema"):
        ShotRecord.from_json(json.dumps(raw))


def test_load_dataset_reads_labelled_directories_and_checks_captures(tmp_path):
    driver = tmp_path / "driver"
    driver.mkdir()
    (driver / "shot_001.l3dump").write_bytes(b"x")
    (driver / "shot_001.json").write_text(record().to_json())
    shots = load_dataset(tmp_path)
    assert len(shots) == 1 and shots[0].record.club == "driver"
    assert shots[0].capture_path == driver / "shot_001.l3dump"
    (driver / "shot_002.json").write_text(record(capture="shot_002.l3dump").to_json())
    with pytest.raises(FileNotFoundError, match="shot_002"):
        load_dataset(tmp_path)
    (driver / "shot_002.json").unlink()
    wedge = tmp_path / "wedge"
    wedge.mkdir()
    (wedge / "shot_003.l3dump").write_bytes(b"x")
    (wedge / "shot_003.json").write_text(record(capture="shot_003.l3dump").to_json())
    with pytest.raises(ValueError, match="not labelled wedge"):
        load_dataset(tmp_path)
    assert load_dataset(tmp_path / "missing") == []
    assert all(label in LABELS for label in ("driver", "7iron", "wedge", "fast"))


def test_reference_error_compares_only_the_metrics_both_sides_have():
    error = reference_error(
        record(),
        {
            "ball_speed_mph": 150.0,
            "vertical_launch_deg": 12.2,
            "club_path_deg": 1.0,
            "spin_rpm": None,
        },
    )
    assert error.errors == {
        "ball_speed_mph": pytest.approx(-2.3),
        "vertical_launch_deg": pytest.approx(0.5),
    }
    assert error.summary().startswith("ball_speed_mph -2.30, vertical_launch_deg +0.50")
    assert reference_error(record(), {}).summary() == "no metric in common with the reference"


class _Result:
    """The slice of a ReplayResult the expectations read."""

    def __init__(self, **kw):
        self.fired_frame = kw.get("fired_frame", 11)
        self.points = [object()] * kw.get("points", 8)
        self.approach_fraction = kw.get("approach", 1.0)
        self.acquisitions = kw.get("acquisitions", 1)
        self.ball_points = kw.get("ball_points", [type("P", (), {"range_bin": 47.5})()])
        self.launch = kw.get("launch", type("L", (), {"speed_mps": 62.0})())
        self.delivery = kw.get("delivery", type("D", (), {"speed_mps": 42.0})())


def test_expectations_pass_ranges_and_name_every_failure():
    expect = Expectation.from_manifest(
        {
            "impact_frame": [10, 12],
            "club_points_min": 7,
            "club_direction": "approaching",
            "acquisitions_max": 1,
            "ball_origin_bin": [47, 49],
            "ball_speed_mps": [55, 75],
            "club_speed_mps": [35, 50],
            "fires": True,
        }
    )
    assert expect.check(_Result()) == []
    failures = expect.check(
        _Result(
            fired_frame=None, points=3, approach=0.5, acquisitions=3, ball_points=[], launch=None
        )
    )
    assert any(f.startswith("fires: False") for f in failures)
    assert any(f.startswith("impact_frame: none") for f in failures)
    assert any(f.startswith("club_points: 3 < 7") for f in failures)
    assert any(f.startswith("club_direction") for f in failures)
    assert any(f.startswith("acquisitions: 3 > 1") for f in failures)
    assert any(f.startswith("ball_origin_bin: none") for f in failures)
    assert any(f.startswith("ball_speed_mps: none") for f in failures)
    assert len(failures) == 7
    with pytest.raises(ValueError, match="unknown expectation"):
        Expectation.from_manifest({"impact_frames": [1, 2]})


def test_manifest_expectations_merge_default_and_per_file_and_stay_out_of_the_config(tmp_path):
    for name in ("a.l3dump", "b.l3dump"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "default": {
                    "tee_bin": 34,
                    "expect": {"club_direction": "approaching", "acquisitions_max": 2},
                },
                "b.l3dump": {
                    "dest_bin": 46,
                    "expect": {"acquisitions_max": 1, "impact_frame": [9, 11]},
                },
            }
        )
    )
    expectations = recording_expectations(tmp_path)
    assert expectations["a.l3dump"] == Expectation(club_direction="approaching", acquisitions_max=2)
    assert expectations["b.l3dump"] == Expectation(
        club_direction="approaching", acquisitions_max=1, impact_frame=(9, 11)
    )
    configs = dict((p.name, c) for p, c in recording_configs(tmp_path))
    assert configs["b.l3dump"].dest_bin == 46 and configs["a.l3dump"].tee_bin == 34
    assert recording_expectations(tmp_path / "missing") == {}


def test_coverage_counts_the_validation_matrix_and_names_thin_cells(tmp_path):
    from openflight.iwr6843.datasets import DatasetShot, coverage

    def shot(club, labels):
        return DatasetShot(record(club=club, labels=labels), tmp_path / "x.json")

    shots = [
        shot("driver", ("driver", "fast", "straight")),
        shot("7_iron", ("7iron", "medium", "left")),
        shot("7_iron", ("7iron", "medium", "right")),
        shot("putter", ()),
    ]
    cov = coverage(shots)
    assert cov.clubs == {"driver": 1, "mid iron": 2}
    assert cov.speeds == {"fast": 1, "medium": 2} and cov.shapes == {
        "straight": 1,
        "left": 1,
        "right": 1,
    }
    assert cov.unclassified_clubs == {"putter": 1}
    thin = cov.missing(minimum=2)
    assert "club: driver (1)" in thin and "club: wedge (0)" in thin and "speed: slow (0)" in thin
    assert "club: mid iron (2)" not in thin


def test_validate_dataset_reports_per_field_and_per_label_statistics(tmp_path):
    from openflight.iwr6843.datasets import (
        DatasetShot,
        field_stats,
        format_field_stats,
        validate_dataset,
    )

    shots = [
        DatasetShot(record(labels=("driver", "fast")), tmp_path / "a.json"),
        DatasetShot(record(labels=("driver", "slow")), tmp_path / "b.json"),
    ]
    measured = iter(
        [
            {"ball_speed_mph": 154.3, "vertical_launch_deg": 11.2, "club_speed_mph": None},
            {"ball_speed_mph": 151.3, "vertical_launch_deg": 12.7, "club_speed_mph": 103.0},
        ]
    )
    results = validate_dataset(shots, lambda shot: next(measured))
    assert set(results) == {"all", "driver", "fast", "slow"}
    by_field = {s.field: s for s in results["all"]}
    assert by_field["ball_speed_mph"].count == 2 and by_field[
        "ball_speed_mph"
    ].bias == pytest.approx(0.5)
    assert by_field["ball_speed_mph"].mae == pytest.approx(1.5)
    assert by_field["vertical_launch_deg"].bias == pytest.approx(0.25)
    assert by_field["club_speed_mph"].count == 1 and by_field[
        "club_speed_mph"
    ].bias == pytest.approx(-1.0)
    assert by_field["spin_rpm"].count == 0
    assert {s.field: s.count for s in results["fast"]}["ball_speed_mph"] == 1
    text = format_field_stats(results["all"], title="all")
    assert "ball_speed_mph" in text and "spin_rpm" not in text
    assert "(no field in common" in format_field_stats(field_stats([]), title="empty")
