"""The label sidecar: what a person marked as the true ball and club tracks in one dump."""

from __future__ import annotations

import json
import os

import pytest

from openflight.iwr6843 import labels as lb

DUMP_BYTES = b"not a real dump, only its hash matters here"


@pytest.fixture
def dump(tmp_path):
    path = tmp_path / "shot_001.l3dump"
    path.write_bytes(DUMP_BYTES)
    return path


def _labels(dump, **overrides):
    base = lb.empty_labels(dump)
    fields = {
        "reviewed": True,
        "ball": (lb.LabelPoint(41, 52.5, 33.0), lb.LabelPoint(42, 55.0)),
        "club": (lb.LabelPoint(10, 30.0),),
        "notes": "clean",
    }
    fields.update(overrides)
    return lb.Labels(
        dump=base.dump,
        dump_sha256=base.dump_sha256,
        tolerances=lb.Tolerances(range_bins=1.5, min_coverage=0.7),
        **fields,
    )


def test_sidecar_sits_next_to_the_dump_and_is_not_a_capture(dump):
    path = lb.labels_path_for(dump)
    assert path == dump.with_name("shot_001.l3dump.labels.json")
    assert path.suffix != ".l3dump"


def test_save_then_load_round_trips(dump):
    labels = _labels(dump)
    lb.save_labels(dump, labels)
    assert lb.load_labels(dump) == labels


def test_load_without_a_file_is_none(dump):
    assert lb.load_labels(dump) is None


def test_empty_labels_carry_the_dump_name_and_hash(dump):
    labels = lb.empty_labels(dump)
    assert labels.dump == "shot_001.l3dump"
    assert labels.dump_sha256 == lb.dump_sha256(DUMP_BYTES)
    assert not labels.reviewed
    assert labels.ball == () and labels.club == ()


def test_points_are_sorted_by_frame_on_load():
    raw = _minimal_json(ball=[{"frame": 5, "range_bin": 1.0}, {"frame": 2, "range_bin": 2.0}])
    assert [p.frame for p in lb.Labels.from_json(raw).ball] == [2, 5]


def test_a_reviewed_object_with_no_points_round_trips(dump):
    labels = _labels(dump, ball=(), club=())
    lb.save_labels(dump, labels)
    loaded = lb.load_labels(dump)
    assert loaded.reviewed and loaded.ball == () and loaded.club == ()


def test_changed_dump_is_refused_on_load(dump):
    lb.save_labels(dump, _labels(dump))
    dump.write_bytes(DUMP_BYTES + b"!")
    with pytest.raises(lb.LabelError, match="shot_001.l3dump"):
        lb.load_labels(dump)


def test_save_refuses_labels_for_another_dump(dump):
    stale = _labels(dump)
    dump.write_bytes(DUMP_BYTES + b"!")
    with pytest.raises(lb.LabelError, match="hash"):
        lb.save_labels(dump, stale)
    assert not lb.labels_path_for(dump).exists()


def _minimal_json(**objects):
    raw = {"version": 1, "dump": "d.l3dump", "dump_sha256": "ab" * 32}
    for name, points in objects.items():
        raw[name] = {"points": points}
    return raw


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(version=2), "version"),
        (lambda r: r.update(extra=1), "unknown"),
        (lambda r: r.update(ball={"points": [{"frame": -1, "range_bin": 1.0}]}), "frame"),
        (lambda r: r.update(ball={"points": [{"frame": 1.5, "range_bin": 1.0}]}), "frame"),
        (lambda r: r.update(ball={"points": [{"frame": True, "range_bin": 1.0}]}), "frame"),
        (
            lambda r: r.update(ball={"points": [{"frame": 1, "range_bin": float("nan")}]}),
            "range_bin",
        ),
        (lambda r: r.update(ball={"points": [{"frame": 1, "range_bin": -1.0}]}), "range_bin"),
        (
            lambda r: r.update(
                ball={"points": [{"frame": 1, "range_bin": 1.0, "doppler_mps": "x"}]}
            ),
            "doppler",
        ),
        (
            lambda r: r.update(
                ball={"points": [{"frame": 1, "range_bin": 1.0}, {"frame": 1, "range_bin": 2.0}]}
            ),
            "duplicate",
        ),
        (lambda r: r.update(ball={"points": [{"frame": 1}]}), "range_bin"),
        (lambda r: r.update(ball=[]), "ball"),
        (lambda r: r.update(tolerances={"range_bins": 0}), "range_bins"),
        (lambda r: r.update(tolerances={"min_coverage": 1.5}), "min_coverage"),
        (lambda r: r.update(tolerances={"doppler_mps": -1}), "doppler_mps"),
        (lambda r: r.update(tolerances={"nope": 1}), "unknown"),
        (lambda r: r.update(reviewed="yes"), "reviewed"),
    ],
)
def test_invalid_files_are_refused_with_a_reason(mutate, message):
    raw = _minimal_json()
    mutate(raw)
    with pytest.raises(lb.LabelError, match=message):
        lb.Labels.from_json(raw)


def test_unparseable_json_is_a_label_error(dump):
    lb.labels_path_for(dump).write_text("{not json", encoding="utf-8")
    with pytest.raises(lb.LabelError, match="shot_001.l3dump.labels.json"):
        lb.load_labels(dump)


def test_failed_save_keeps_the_old_file_and_leaves_no_temp(dump, monkeypatch):
    lb.save_labels(dump, _labels(dump, notes="first"))

    def boom(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk went away"):
        lb.save_labels(dump, _labels(dump, notes="second"))
    monkeypatch.undo()
    assert lb.load_labels(dump).notes == "first"
    assert sorted(p.name for p in dump.parent.iterdir()) == [
        "shot_001.l3dump",
        "shot_001.l3dump.labels.json",
    ]


def test_payload_from_the_page_gets_the_servers_name_and_hash(dump, monkeypatch):
    monkeypatch.setattr(lb, "parse_dump", lambda raw: ({"n_frames": 10}, None))
    payload = {
        "version": 1,
        "dump": "whatever.l3dump",
        "dump_sha256": "stale",
        "reviewed": True,
        "ball": {"points": [{"frame": 3, "range_bin": 50.0}]},
    }
    labels = lb.labels_from_payload(payload, dump)
    assert labels.dump == "shot_001.l3dump"
    assert labels.dump_sha256 == lb.dump_sha256(DUMP_BYTES)
    assert labels.ball == (lb.LabelPoint(3, 50.0),)


def test_saved_file_is_readable_json_with_a_trailing_newline(dump):
    lb.save_labels(dump, _labels(dump))
    text = lb.labels_path_for(dump).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text)["ball"]["points"][0] == {
        "frame": 41,
        "range_bin": 52.5,
        "doppler_mps": 33.0,
    }


@pytest.mark.parametrize("points", [5, None, "x", {"frame": 1}])
def test_a_points_value_that_is_not_a_list_is_a_label_error(points):
    raw = {"version": 1, "dump": "a.l3dump", "dump_sha256": "h", "ball": {"points": points}}
    with pytest.raises(lb.LabelError, match="points"):
        lb.Labels.from_json(raw)


def test_ball_and_club_must_be_objects():
    raw = {"version": 1, "dump": "a.l3dump", "dump_sha256": "h", "club": [1]}
    with pytest.raises(lb.LabelError):
        lb.Labels.from_json(raw)
