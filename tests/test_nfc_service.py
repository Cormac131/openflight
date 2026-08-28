"""Tests for the NFC polling service."""

import threading
import time

import pytest

from openflight.nfc import ClubTagRegistry, MockTagReader, NfcService, TagRead
from openflight.nfc.reader import NfcReaderError, TagWriteError


class ScriptedReader:
    """Replays a fixed sequence of read_tag outcomes; exceptions are raised."""

    name = "scripted"

    def __init__(self, results):
        self._results = list(results)
        self.opens = 0
        self.closes = 0
        self.exhausted = threading.Event()

    def open(self):
        self.opens += 1

    def read_tag(self, timeout_s=0.5):  # pylint: disable=unused-argument
        if not self._results:
            self.exhausted.set()
            return None
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return TagRead(uid=result) if isinstance(result, str) else result

    def write_text(self, uid, text, timeout_s=3.0):
        raise TagWriteError("scripted reader cannot write")

    def close(self):
        self.closes += 1


def tap(service, uid, **kwargs):
    """Drive one tag presentation through the service."""
    return service.handle_tag(TagRead(uid=uid, **kwargs))


@pytest.fixture(name="registry")
def fixture_registry(tmp_path):
    return ClubTagRegistry(tmp_path / "club_tags.json")


def _service(reader, registry, scans, **kwargs):
    return NfcService(reader, registry, on_scan=scans.append, **kwargs)


class TestScanResolution:
    def test_a_learned_tag_resolves_to_its_club(self, registry):
        registry.assign("04A2B1C3", "7-iron")
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04:a2:b1:c3")

        assert len(scans) == 1
        assert scans[0].club_id == "7-iron"
        assert scans[0].known is True

    def test_an_unlearned_tag_reports_no_club(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3")

        assert scans[0].club_id is None
        assert scans[0].known is False

    def test_a_learned_tag_records_last_seen(self, registry):
        registry.assign("04A2B1C3", "driver")
        service = _service(MockTagReader(), registry, [])

        tap(service, "04A2B1C3")

        assert registry.entries()[0].last_seen_at is not None

    def test_an_unusable_uid_is_dropped_without_reporting(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans)

        assert tap(service, "not-a-uid") is None
        assert scans == []


class TestRepeatSuppression:
    def test_a_tag_resting_on_the_antenna_fires_once(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans, repeat_suppression_s=60.0)

        tap(service, "04A2B1C3")
        tap(service, "04A2B1C3")
        tap(service, "04A2B1C3")

        assert len(scans) == 1

    def test_a_different_tag_reports_immediately(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans, repeat_suppression_s=60.0)

        tap(service, "04A2B1C3")
        tap(service, "04A2B1C4")

        assert [scan.uid for scan in scans] == ["04A2B1C3", "04A2B1C4"]

    def test_suppression_expires(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans, repeat_suppression_s=0.01)

        tap(service, "04A2B1C3")
        time.sleep(0.02)
        tap(service, "04A2B1C3")

        assert len(scans) == 2

    def test_learning_a_tag_lets_the_next_tap_through(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans, repeat_suppression_s=60.0)
        tap(service, "04A2B1C3")

        registry.assign("04A2B1C3", "driver")
        service.forget_recent("04:a2:b1:c3")
        tap(service, "04A2B1C3")

        assert [scan.club_id for scan in scans] == [None, "driver"]

    def test_forget_recent_ignores_other_tags(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans, repeat_suppression_s=60.0)
        tap(service, "04A2B1C3")

        service.forget_recent("04A2B1C4")
        tap(service, "04A2B1C3")

        assert len(scans) == 1


class TestFailureHandling:
    def test_a_handler_exception_does_not_propagate(self, registry):
        def explode(_scan):
            raise RuntimeError("ui gone")

        service = NfcService(MockTagReader(), registry, on_scan=explode)

        assert tap(service, "04A2B1C3") is not None

    def test_read_errors_are_recorded_and_polling_continues(self, registry):
        reader = ScriptedReader([NfcReaderError("bus glitch"), "04A2B1C3"])
        scans = []
        service = _service(reader, registry, scans, poll_interval_s=0.0, read_timeout_s=0.01)

        service.start()
        try:
            deadline = time.monotonic() + 2.0
            while not scans and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            service.stop()

        assert [scan.uid for scan in scans] == ["04A2B1C3"]
        assert service.status()["error_count"] >= 1

    def test_a_run_of_failures_reopens_the_reader(self, registry):
        reader = ScriptedReader([NfcReaderError("gone")] * 5)
        service = _service(reader, registry, [], poll_interval_s=0.0, read_timeout_s=0.01)

        service.start()
        try:
            assert reader.exhausted.wait(timeout=3.0)
        finally:
            service.stop()

        assert reader.opens >= 2

    def test_start_propagates_an_unreachable_reader(self, registry):
        class DeadReader(ScriptedReader):
            def open(self):
                raise NfcReaderError("no PN532 on the bus")

        service = _service(DeadReader([]), registry, [])

        with pytest.raises(NfcReaderError):
            service.start()

    def test_invalid_timings_are_refused(self, registry):
        with pytest.raises(ValueError):
            NfcService(MockTagReader(), registry, on_scan=lambda _s: None, read_timeout_s=0)


class TestLifecycleAndStatus:
    def test_the_mock_reader_drives_a_full_scan(self, registry):
        registry.assign("04A2B1C3", "5-iron")
        reader = MockTagReader()
        scans = []
        service = _service(reader, registry, scans, poll_interval_s=0.0, read_timeout_s=0.05)

        service.start()
        try:
            reader.present_tag("04A2B1C3")
            deadline = time.monotonic() + 2.0
            while not scans and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            service.stop()

        assert scans[0].club_id == "5-iron"
        assert reader.closed is True

    def test_status_reports_reader_and_counts(self, registry):
        registry.assign("04A2B1C3", "driver")
        service = _service(MockTagReader(), registry, [])
        tap(service, "04A2B1C3")

        status = service.status()

        assert status["reader"] == "mock"
        assert status["known_tags"] == 1
        assert status["scan_count"] == 1
        assert status["last_scan"]["club"] == "driver"
        assert status["last_error"] is None

    def test_stop_without_start_still_closes_the_reader(self, registry):
        reader = MockTagReader()
        _service(reader, registry, []).stop()

        assert reader.closed is True


class TestTagContentsWinOverTheRegistry:
    """A club written onto the tag travels with the club between rigs."""

    def test_a_club_on_the_tag_is_used(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3", text="7-iron")

        assert scans[0].club_id == "7-iron"
        assert scans[0].source == "tag"

    def test_the_registry_learns_a_club_it_had_never_seen(self, registry):
        service = _service(MockTagReader(), registry, [])

        tap(service, "04A2B1C3", text="pw")

        assert registry.club_for("04A2B1C3") == "pw"

    def test_a_disagreeing_registry_is_corrected_to_match_the_tag(self, registry):
        registry.assign("04A2B1C3", "8-iron")
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3", text="7-iron")

        assert scans[0].club_id == "7-iron"
        assert registry.club_for("04A2B1C3") == "7-iron"

    def test_an_unreadable_tag_falls_back_to_the_registry(self, registry):
        registry.assign("04A2B1C3", "driver")
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3", text="mashie-niblick")

        assert scans[0].club_id == "driver"
        assert scans[0].source == "registry"

    def test_a_tag_with_no_record_uses_the_registry(self, registry):
        registry.assign("04A2B1C3", "gw")
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3")

        assert scans[0].source == "registry"


class TestBlankTags:
    def test_a_blank_writable_tag_asks_to_be_written(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3", blank=True, writable=True)

        assert scans[0].needs_write is True
        assert scans[0].known is False

    def test_a_blank_tag_this_reader_cannot_write_does_not(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3", blank=True, writable=False)

        assert scans[0].needs_write is False

    def test_a_tag_holding_someone_elses_data_is_not_offered_for_writing(self, registry):
        scans = []
        service = _service(MockTagReader(), registry, scans)

        # Not blank and not ours: overwriting it is not this flow's decision.
        tap(service, "04A2B1C3", text="https://example.com", writable=True)

        assert scans[0].needs_write is False
        assert scans[0].known is False

    def test_a_learned_tag_is_never_offered_for_writing(self, registry):
        registry.assign("04A2B1C3", "driver")
        scans = []
        service = _service(MockTagReader(), registry, scans)

        tap(service, "04A2B1C3", blank=True, writable=True)

        assert scans[0].needs_write is False


class TestWritingAClubToATag:
    def test_writing_puts_the_club_on_the_tag_and_in_the_registry(self, registry):
        reader = MockTagReader()
        service = _service(reader, registry, [])
        reader.present_tag("04A2B1C3")
        reader.read_tag(0.1)

        service.write_club_tag("04:a2:b1:c3", "7-iron")

        assert registry.club_for("04A2B1C3") == "7-iron"
        reader.present_tag("04A2B1C3")
        assert reader.read_tag(0.1).text == "7-iron"

    def test_the_written_tag_then_resolves_from_the_tag_itself(self, registry):
        reader = MockTagReader()
        scans = []
        service = _service(reader, registry, scans)
        reader.present_tag("04A2B1C3")
        reader.read_tag(0.1)
        service.write_club_tag("04A2B1C3", "5-iron")

        reader.present_tag("04A2B1C3")
        service.handle_tag(reader.read_tag(0.1))

        assert scans[-1].source == "tag"
        assert scans[-1].club_id == "5-iron"

    def test_a_failed_write_leaves_the_registry_untouched(self, registry):
        reader = MockTagReader()
        service = _service(reader, registry, [])
        reader.present_tag("04A2B1C3")
        reader.read_tag(0.1)
        reader.set_write_failure("Tag not on the reader")

        with pytest.raises(TagWriteError):
            service.write_club_tag("04A2B1C3", "7-iron")

        assert len(registry) == 0

    def test_an_unknown_club_is_refused_before_touching_the_tag(self, registry):
        reader = MockTagReader()
        service = _service(reader, registry, [])
        reader.present_tag("04A2B1C3")
        reader.read_tag(0.1)

        with pytest.raises(ValueError):
            service.write_club_tag("04A2B1C3", "spoon")

        reader.present_tag("04A2B1C3")
        assert reader.read_tag(0.1).text is None

    def test_writing_clears_suppression_so_a_confirming_tap_registers(self, registry):
        reader = MockTagReader()
        scans = []
        service = _service(reader, registry, scans, repeat_suppression_s=60.0)
        reader.present_tag("04A2B1C3")
        service.handle_tag(reader.read_tag(0.1))

        service.write_club_tag("04A2B1C3", "9-iron")
        reader.present_tag("04A2B1C3")
        service.handle_tag(reader.read_tag(0.1))

        assert [scan.club_id for scan in scans] == [None, "9-iron"]

    def test_a_write_does_not_run_while_the_poll_thread_holds_the_reader(self, registry):
        """The reader is one I2C device; interleaved frames corrupt each other."""
        reader = MockTagReader()
        service = _service(reader, registry, [], poll_interval_s=0.0, read_timeout_s=0.05)
        reader.present_tag("04A2B1C3")

        service.start()
        try:
            # Completes without raising: the write waits for the poll to release.
            service.write_club_tag("04A2B1C3", "3-wood")
        finally:
            service.stop()

        assert registry.club_for("04A2B1C3") == "3-wood"
