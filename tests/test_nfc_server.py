"""Tests for the server wiring that turns tag taps into club selections."""

from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.launch_monitor import ClubType
from openflight.nfc import ClubTagRegistry, MockTagReader, NfcService, TagRead, TagScan


class FakeMonitor:
    """Captures set_club calls from the club pipeline."""

    def __init__(self):
        self.clubs = []

    def set_club(self, club):
        self.clubs.append(club)


@pytest.fixture(name="wired")
def fixture_wired(tmp_path, monkeypatch):
    """Server globals pointed at a mock reader and a temp registry."""
    emitted = []
    registry = ClubTagRegistry(tmp_path / "club_tags.json")
    reader = MockTagReader()
    monitor = FakeMonitor()
    service = NfcService(reader, registry, on_scan=server_module._on_nfc_scan)

    monkeypatch.setattr(server_module, "club_tag_registry", registry)
    monkeypatch.setattr(server_module, "nfc_service", service)
    monkeypatch.setattr(server_module, "nfc_runtime_config", {"enabled": True})
    monkeypatch.setattr(server_module, "monitor", monitor)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, payload=None: emitted.append((event, payload)),
    )

    def tap(uid, **kwargs):
        """Drive one tag presentation through the real scan handler."""
        return service.handle_tag(TagRead(uid=uid, **kwargs))

    return SimpleNamespace(
        tap=tap,
        emitted=emitted,
        registry=registry,
        reader=reader,
        monitor=monitor,
        service=service,
        events=lambda name: [payload for event, payload in emitted if event == name],
    )


class TestKnownTagSelectsClub:
    def test_a_learned_tag_changes_the_club_and_tells_the_ui(self, wired):
        wired.registry.assign("04A2B1C3", "7-iron")

        wired.tap("04A2B1C3")

        assert wired.monitor.clubs == [ClubType.IRON_7]
        assert wired.events("club_changed") == [{"club": "7-iron", "source": "nfc"}]

    def test_a_learned_tag_does_not_ask_the_ui_to_learn_it(self, wired):
        wired.registry.assign("04A2B1C3", "driver")

        wired.tap("04A2B1C3")

        assert wired.events("nfc_tag_unknown") == []

    def test_every_tap_is_reported_for_the_tag_view(self, wired):
        wired.registry.assign("04A2B1C3", "pw")

        wired.tap("04A2B1C3")

        assert wired.events("nfc_scan")[0]["uid_display"] == "04:A2:B1:C3"

    def test_a_club_removed_from_the_enum_falls_back_to_learning(self, wired, monkeypatch):
        wired.registry.assign("04A2B1C3", "driver")
        # Simulate a club id that no longer resolves, as if renamed in a release.
        monkeypatch.setattr(wired.registry, "club_for", lambda _uid: "mashie-niblick")

        wired.tap("04A2B1C3")

        assert wired.monitor.clubs == []
        assert len(wired.events("nfc_tag_unknown")) == 1


class TestUnknownTagPrompt:
    def test_an_unlearned_tag_asks_the_ui_to_assign_it(self, wired):
        wired.tap("04A2B1C3")

        prompts = wired.events("nfc_tag_unknown")
        assert prompts == [
            {
                "uid": "04A2B1C3",
                "uid_display": "04:A2:B1:C3",
                "timestamp": prompts[0]["timestamp"],
                "club": None,
                "known": False,
                "source": None,
                "blank": False,
                "writable": False,
            }
        ]

    def test_an_unlearned_tag_leaves_the_club_alone(self, wired):
        wired.tap("04A2B1C3")

        assert wired.monitor.clubs == []
        assert wired.events("club_changed") == []


class TestAssignment:
    def test_assigning_persists_and_selects_the_club(self, wired):
        server_module.handle_assign_club_tag({"uid": "04:a2:b1:c3", "club": "8-iron"})

        assert wired.registry.club_for("04A2B1C3") == "8-iron"
        assert wired.monitor.clubs == [ClubType.IRON_8]
        assert wired.events("club_changed") == [{"club": "8-iron", "source": "nfc-learn"}]

    def test_assignment_survives_a_server_restart(self, wired):
        server_module.handle_assign_club_tag({"uid": "04A2B1C3", "club": "gw"})

        reloaded = ClubTagRegistry(wired.registry.path)
        assert reloaded.club_for("04A2B1C3") == "gw"

    def test_the_tag_list_is_broadcast_after_learning(self, wired):
        server_module.handle_assign_club_tag({"uid": "04A2B1C3", "club": "driver"})

        assert wired.events("club_tags")[-1]["tags"][0]["club"] == "driver"

    def test_learning_clears_suppression_so_the_next_tap_counts(self, wired):
        wired.tap("04A2B1C3")
        server_module.handle_assign_club_tag({"uid": "04A2B1C3", "club": "5-iron"})

        wired.tap("04A2B1C3")

        assert wired.monitor.clubs == [ClubType.IRON_5, ClubType.IRON_5]

    def test_an_unknown_club_is_refused_without_persisting(self, wired):
        server_module.handle_assign_club_tag({"uid": "04A2B1C3", "club": "spoon"})

        assert len(wired.registry) == 0
        assert len(wired.events("club_tag_error")) == 1

    def test_an_unusable_uid_is_refused(self, wired):
        server_module.handle_assign_club_tag({"uid": "nope", "club": "driver"})

        assert len(wired.registry) == 0
        assert len(wired.events("club_tag_error")) == 1

    def test_a_write_failure_is_reported_not_raised(self, wired, monkeypatch):
        def fail(*_args, **_kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(wired.registry, "assign", fail)

        server_module.handle_assign_club_tag({"uid": "04A2B1C3", "club": "driver"})

        assert "read-only filesystem" in wired.events("club_tag_error")[0]["error"]

    def test_assignment_without_a_reader_is_reported(self, monkeypatch):
        emitted = []
        monkeypatch.setattr(server_module, "club_tag_registry", None)
        monkeypatch.setattr(
            server_module.socketio, "emit", lambda event, payload=None: emitted.append(event)
        )

        server_module.handle_assign_club_tag({"uid": "04A2B1C3", "club": "driver"})

        assert emitted == ["club_tag_error"]


class TestForgetting:
    def test_forgetting_removes_the_mapping_and_persists(self, wired):
        wired.registry.assign("04A2B1C3", "driver")

        server_module.handle_forget_club_tag({"uid": "04A2B1C3"})

        assert ClubTagRegistry(wired.registry.path).club_for("04A2B1C3") is None
        assert wired.events("club_tags")[-1]["tags"] == []

    def test_forgetting_an_unknown_tag_still_refreshes_the_list(self, wired):
        server_module.handle_forget_club_tag({"uid": "04A2B1C3"})

        assert len(wired.events("club_tags")) == 1

    def test_a_forgotten_tag_prompts_again_on_the_next_tap(self, wired):
        wired.registry.assign("04A2B1C3", "driver")
        server_module.handle_forget_club_tag({"uid": "04A2B1C3"})

        wired.tap("04A2B1C3")

        assert len(wired.events("nfc_tag_unknown")) == 1

    def test_resetting_tags_clears_suppression_for_an_unlearned_uid(self, wired):
        wired.tap("04A2B1C3")
        assert len(wired.events("nfc_tag_unknown")) == 1

        server_module.handle_forget_club_tag({})
        wired.tap("04A2B1C3")

        assert len(wired.events("nfc_tag_unknown")) == 2


class TestManualSelectionStillWorks:
    def test_the_picker_path_shares_the_club_pipeline(self, wired):
        server_module.handle_set_club({"club": "3-wood"})

        assert wired.monitor.clubs == [ClubType.WOOD_3]
        assert wired.events("club_changed") == [{"club": "3-wood", "source": "ui"}]

    def test_an_unknown_club_from_the_picker_is_ignored(self, wired):
        server_module.handle_set_club({"club": "spoon"})

        assert wired.monitor.clubs == []
        assert wired.events("club_changed") == []

    def test_a_missing_club_defaults_to_driver(self, wired):
        server_module.handle_set_club({})

        assert wired.monitor.clubs == [ClubType.DRIVER]


class TestMockScanEndpoint:
    def test_simulating_a_scan_does_not_wait_for_the_poll_thread(self, wired):
        server_module.handle_simulate_nfc_scan({"uid": "04A2B1C3"})

        assert [event["uid"] for event in wired.events("nfc_scan")] == ["04A2B1C3"]
        assert len(wired.events("nfc_tag_unknown")) == 1

    def test_a_simulated_tag_can_carry_a_club_and_be_unwritable(self, wired):
        server_module.handle_simulate_nfc_scan(
            {"uid": "04A2B1C3", "text": "7-iron", "writable": False}
        )

        assert wired.events("club_changed") == [{"club": "7-iron", "source": "nfc"}]
        assert wired.events("nfc_scan")[0]["writable"] is False

    def test_a_bad_uid_in_a_simulated_scan_is_reported(self, wired):
        server_module.handle_simulate_nfc_scan({"uid": "nope"})

        assert len(wired.events("club_tag_error")) == 1

    def test_simulating_without_a_mock_reader_is_reported(self, wired, monkeypatch):
        monkeypatch.setattr(server_module, "nfc_service", None)

        server_module.handle_simulate_nfc_scan({"uid": "04A2B1C3"})

        assert len(wired.events("club_tag_error")) == 1


class TestInitNfcReaderChoice:
    def _install_fake_readers(self, monkeypatch):
        class FakePn532:
            name = "pn532"

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def open(self):
                return None

            def close(self):
                return None

            def read_tag(self, timeout_s=0.5):
                return None

        class FakePn5180:
            name = "pn5180"

            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def open(self):
                return None

            def close(self):
                return None

            def read_tag(self, timeout_s=0.5):
                return None

        monkeypatch.setattr("openflight.nfc.PN532I2C", FakePn532)
        monkeypatch.setattr("openflight.nfc.Pn5180Spi", FakePn5180)

    def _cleanup(self):
        if server_module.nfc_service is not None:
            server_module.nfc_service.stop()
        server_module.nfc_service = None
        server_module.club_tag_registry = None
        server_module.nfc_runtime_config = {"enabled": False}

    def test_nfc_always_opens_the_pn532(self, tmp_path, monkeypatch):
        self._install_fake_readers(monkeypatch)
        monkeypatch.delenv("OPENFLIGHT_NFC_MOCK", raising=False)
        try:
            assert server_module.init_nfc(tags_path=str(tmp_path / "club_tags.json"))
            assert server_module.nfc_service.reader.name == "pn532"
        finally:
            self._cleanup()

    def test_playwright_env_is_the_only_in_memory_reader(self, tmp_path, monkeypatch):
        self._install_fake_readers(monkeypatch)
        monkeypatch.setenv("OPENFLIGHT_NFC_MOCK", "1")
        try:
            assert server_module.init_nfc(tags_path=str(tmp_path / "club_tags.json"))
            assert server_module.nfc_service.reader.name == "mock"
        finally:
            self._cleanup()

    def test_nfc_reader_pn5180_opens_the_pn5180_instead(self, tmp_path, monkeypatch):
        self._install_fake_readers(monkeypatch)
        monkeypatch.delenv("OPENFLIGHT_NFC_MOCK", raising=False)
        try:
            assert server_module.init_nfc(
                reader_chip="pn5180", tags_path=str(tmp_path / "club_tags.json")
            )
            assert server_module.nfc_service.reader.name == "pn5180"
            assert server_module.nfc_runtime_config["reader"] == "pn5180"
        finally:
            self._cleanup()

    def test_nfc_reader_pn5180_forwards_its_own_gpio_settings(self, tmp_path, monkeypatch):
        self._install_fake_readers(monkeypatch)
        monkeypatch.delenv("OPENFLIGHT_NFC_MOCK", raising=False)
        try:
            server_module.init_nfc(
                reader_chip="pn5180",
                spi_bus=1,
                spi_device=2,
                busy_gpio=27,
                reset_gpio=17,
                tags_path=str(tmp_path / "club_tags.json"),
            )
            kwargs = server_module.nfc_service.reader.kwargs
            assert kwargs == {
                "spi_bus": 1,
                "spi_device": 2,
                "busy_gpio": 27,
                "reset_gpio": 17,
            }
            assert server_module.nfc_runtime_config["busy_gpio"] == 27
            assert server_module.nfc_runtime_config["reset_gpio"] == 17
            assert "i2c_address" not in server_module.nfc_runtime_config
        finally:
            self._cleanup()

    def test_playwright_env_overrides_pn5180_too(self, tmp_path, monkeypatch):
        self._install_fake_readers(monkeypatch)
        monkeypatch.setenv("OPENFLIGHT_NFC_MOCK", "1")
        try:
            assert server_module.init_nfc(
                reader_chip="pn5180", tags_path=str(tmp_path / "club_tags.json")
            )
            assert server_module.nfc_service.reader.name == "mock"
        finally:
            self._cleanup()

    def test_a_pn5180_failure_is_reported_under_its_own_name(self, tmp_path, monkeypatch):
        class BrokenPn5180:
            name = "pn5180"

            def __init__(self, **_kwargs):
                raise OSError("PN5180 BUSY line stuck high")

        monkeypatch.setattr("openflight.nfc.Pn5180Spi", BrokenPn5180)
        monkeypatch.delenv("OPENFLIGHT_NFC_MOCK", raising=False)
        monkeypatch.setattr(server_module, "log_session_error", lambda *_args, **_kwargs: None)
        try:
            assert (
                server_module.init_nfc(
                    reader_chip="pn5180", tags_path=str(tmp_path / "club_tags.json")
                )
                is False
            )
            assert server_module.nfc_runtime_config["reader"] == "pn5180"
            assert server_module.nfc_runtime_config["error"] == "PN5180 BUSY line stuck high"
        finally:
            self._cleanup()

    def test_a_reader_failure_still_loads_learned_tags(self, tmp_path, monkeypatch):
        tags_path = tmp_path / "club_tags.json"
        ClubTagRegistry(tags_path).assign("04A2B1C3", "7-iron")

        class BrokenPn532:
            name = "pn532"

            def __init__(self, **_kwargs):
                raise OSError("PN532 not found")

        monkeypatch.setattr("openflight.nfc.PN532I2C", BrokenPn532)
        monkeypatch.delenv("OPENFLIGHT_NFC_MOCK", raising=False)
        monkeypatch.setattr(server_module, "log_session_error", lambda *_args, **_kwargs: None)
        try:
            assert server_module.init_nfc(tags_path=str(tags_path)) is False
            assert server_module.nfc_service is None
            assert server_module.club_tag_registry.club_for("04A2B1C3") == "7-iron"
            assert server_module.nfc_runtime_config["requested"] is True
            assert server_module.nfc_runtime_config["enabled"] is False
            assert server_module.nfc_runtime_config["error"] == "PN532 not found"
        finally:
            self._cleanup()

    def test_club_tags_broadcast_keeps_requested_separate_from_the_reader(
        self, tmp_path, monkeypatch
    ):
        tags_path = tmp_path / "club_tags.json"
        ClubTagRegistry(tags_path).assign("04A2B1C3", "7-iron")

        class BrokenPn532:
            name = "pn532"

            def __init__(self, **_kwargs):
                raise OSError("PN532 not found")

        emitted = []
        monkeypatch.setattr("openflight.nfc.PN532I2C", BrokenPn532)
        monkeypatch.delenv("OPENFLIGHT_NFC_MOCK", raising=False)
        monkeypatch.setattr(server_module, "log_session_error", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            server_module.socketio,
            "emit",
            lambda event, payload=None: emitted.append((event, payload)),
        )
        try:
            server_module.init_nfc(tags_path=str(tags_path))
            server_module._emit_club_tags()
            payload = [body for event, body in emitted if event == "club_tags"][-1]
            assert payload["requested"] is True
            assert payload["enabled"] is False
            assert payload["error"] == "PN532 not found"
            assert payload["tags"][0]["club"] == "7-iron"
        finally:
            self._cleanup()


class TestRuntimeConfig:
    def test_the_session_log_records_the_nfc_configuration(self, wired, monkeypatch):
        monkeypatch.setattr(
            server_module, "nfc_runtime_config", {"enabled": True, "reader": "pn532"}
        )

        config = server_module._session_start_config()

        assert config["nfc"] == {"enabled": True, "reader": "pn532"}

    def test_scan_payloads_round_trip_through_the_scan_type(self):
        scan = TagScan(uid="04A2B1C3", timestamp=1.5, club_id="driver", source="tag")

        assert scan.to_dict() == {
            "uid": "04A2B1C3",
            "uid_display": "04:A2:B1:C3",
            "timestamp": 1.5,
            "club": "driver",
            "known": True,
            "source": "tag",
            "blank": False,
            "writable": False,
        }


class TestUnknownTagRouting:
    def test_a_blank_tag_uses_the_learn_prompt(self, wired):
        wired.tap("04A2B1C3", blank=True, writable=True)

        assert len(wired.events("nfc_tag_unknown")) == 1
        assert wired.events("nfc_tag_unknown")[0]["uid"] == "04A2B1C3"

    def test_a_tag_holding_other_data_uses_the_learn_prompt(self, wired):
        wired.tap("04A2B1C3", text="https://example.com", writable=True)

        assert len(wired.events("nfc_tag_unknown")) == 1

    def test_a_tag_that_already_carries_a_club_selects_it(self, wired):
        wired.tap("04A2B1C3", text="7-iron", writable=True)

        assert wired.monitor.clubs == [ClubType.IRON_7]
        assert wired.events("nfc_tag_unknown") == []
