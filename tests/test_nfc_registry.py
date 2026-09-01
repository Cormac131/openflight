"""Tests for the persisted club-tag registry."""

import json

import pytest

from openflight.nfc import ClubTagRegistry, InvalidTagUidError, UnknownClubError
from openflight.nfc.models import format_uid, normalize_uid


@pytest.fixture(name="registry_path")
def fixture_registry_path(tmp_path):
    return tmp_path / "nested" / "club_tags.json"


class TestUidNormalization:
    @pytest.mark.parametrize(
        "raw",
        ["04a2b1c3", "04:A2:B1:C3", "04-a2-b1-c3", " 04 A2 B1 C3 ", "04_a2_b1_c3"],
    )
    def test_equivalent_spellings_share_one_key(self, raw):
        assert normalize_uid(raw) == "04A2B1C3"

    @pytest.mark.parametrize(
        "raw",
        ["", None, "zzzz1234", "04A2B1C", "04A2B1", "04" * 11],
    )
    def test_unusable_uids_are_rejected(self, raw):
        with pytest.raises(InvalidTagUidError):
            normalize_uid(raw)

    def test_display_form_is_byte_grouped(self):
        assert format_uid("04a2b1c3") == "04:A2:B1:C3"


class TestPersistence:
    def test_assignment_survives_a_restart(self, registry_path):
        ClubTagRegistry(registry_path).assign("04:a2:b1:c3", "7-iron")

        assert ClubTagRegistry(registry_path).club_for("04A2B1C3") == "7-iron"

    def test_file_is_created_with_parent_directories(self, registry_path):
        ClubTagRegistry(registry_path).assign("04A2B1C3", "driver")

        stored = json.loads(registry_path.read_text(encoding="utf-8"))
        assert stored["version"] == 1
        assert stored["tags"]["04A2B1C3"]["club"] == "driver"

    def test_lookup_is_separator_and_case_insensitive(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C3", "pw")

        assert registry.club_for("04:a2:b1:c3") == "pw"

    def test_reassignment_repoints_the_tag_and_keeps_learned_at(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        first = registry.assign("04A2B1C3", "7-iron")
        second = registry.assign("04A2B1C3", "8-iron")

        assert second.club_id == "8-iron"
        assert second.learned_at == first.learned_at
        assert len(registry) == 1

    def test_two_tags_may_point_at_one_club(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C3", "driver")
        registry.assign("04A2B1C4", "driver")

        assert len(registry) == 2
        assert registry.club_for("04A2B1C4") == "driver"

    def test_forget_removes_the_mapping_and_persists(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C3", "sw")

        assert registry.forget("04:a2:b1:c3") is True
        assert registry.club_for("04A2B1C3") is None
        assert ClubTagRegistry(registry_path).club_for("04A2B1C3") is None

    def test_forget_reports_unknown_tags(self, registry_path):
        assert ClubTagRegistry(registry_path).forget("04A2B1C3") is False

    def test_forget_ignores_unusable_uids(self, registry_path):
        assert ClubTagRegistry(registry_path).forget("not-a-uid") is False

    def test_touch_records_last_seen_without_changing_the_club(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C3", "gw")
        registry.touch("04A2B1C3")

        reloaded = ClubTagRegistry(registry_path).entries()[0]
        assert reloaded.club_id == "gw"
        assert reloaded.last_seen_at is not None

    def test_touch_on_an_unknown_tag_is_a_no_op(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        registry.touch("04A2B1C3")

        assert len(registry) == 0

    def test_entries_are_ordered_by_club_then_uid(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C4", "driver")
        registry.assign("04A2B1C3", "driver")
        registry.assign("04A2B1C5", "3-wood")

        assert [tag.uid for tag in registry.entries()] == ["04A2B1C5", "04A2B1C3", "04A2B1C4"]


class TestValidation:
    def test_unknown_clubs_are_refused(self, registry_path):
        with pytest.raises(UnknownClubError):
            ClubTagRegistry(registry_path).assign("04A2B1C3", "spoon")

    def test_unusable_uids_are_refused(self, registry_path):
        with pytest.raises(InvalidTagUidError):
            ClubTagRegistry(registry_path).assign("nope", "driver")

    def test_a_refused_assignment_writes_nothing(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        with pytest.raises(UnknownClubError):
            registry.assign("04A2B1C3", "spoon")

        assert len(registry) == 0
        assert not registry_path.exists()

    def test_a_failed_save_does_not_learn_the_tag_in_memory(self, registry_path, monkeypatch):
        registry = ClubTagRegistry(registry_path)

        def fail(*_args, **_kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(registry, "_save_locked", fail)

        with pytest.raises(OSError, match="read-only filesystem"):
            registry.assign("04A2B1C3", "7-iron")

        assert registry.club_for("04A2B1C3") is None
        assert len(registry) == 0

    def test_a_failed_save_keeps_the_previous_club_in_memory(self, registry_path, monkeypatch):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C3", "7-iron")

        def fail(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(registry, "_save_locked", fail)

        with pytest.raises(OSError, match="disk full"):
            registry.assign("04A2B1C3", "driver")

        assert registry.club_for("04A2B1C3") == "7-iron"

    def test_a_failed_save_does_not_forget_the_tag_in_memory(self, registry_path, monkeypatch):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C3", "sw")

        def fail(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(registry, "_save_locked", fail)

        with pytest.raises(OSError, match="disk full"):
            registry.forget("04A2B1C3")

        assert registry.club_for("04A2B1C3") == "sw"
        assert ClubTagRegistry(registry_path).club_for("04A2B1C3") == "sw"

    def test_club_lookup_of_an_unusable_uid_returns_none(self, registry_path):
        assert ClubTagRegistry(registry_path).club_for("") is None


class TestCorruptFiles:
    def test_unparsable_json_is_quarantined_not_fatal(self, registry_path):
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("{not json", encoding="utf-8")

        registry = ClubTagRegistry(registry_path)

        assert len(registry) == 0
        assert registry_path.with_suffix(".json.corrupt").exists()

    def test_a_registry_without_tags_is_quarantined(self, registry_path):
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps({"version": 1}), encoding="utf-8")

        assert len(ClubTagRegistry(registry_path)) == 0

    def test_one_bad_row_does_not_cost_the_rest_of_the_bag(self, registry_path):
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "tags": {
                        "04A2B1C3": {"club": "7-iron"},
                        "04A2B1C4": {"club": "spoon"},
                        "garbage": {"club": "driver"},
                        "04A2B1C5": "not-an-object",
                    },
                }
            ),
            encoding="utf-8",
        )

        registry = ClubTagRegistry(registry_path)

        assert len(registry) == 1
        assert registry.club_for("04A2B1C3") == "7-iron"

    def test_a_missing_file_is_simply_an_empty_registry(self, registry_path):
        assert len(ClubTagRegistry(registry_path)) == 0


class TestPayload:
    def test_payload_carries_display_uid_and_club(self, registry_path):
        registry = ClubTagRegistry(registry_path)
        registry.assign("04A2B1C3", "9-iron")

        payload = registry.to_payload()

        assert payload == [
            {
                "uid": "04A2B1C3",
                "uid_display": "04:A2:B1:C3",
                "club": "9-iron",
                "learned_at": payload[0]["learned_at"],
                "last_seen_at": None,
            }
        ]
