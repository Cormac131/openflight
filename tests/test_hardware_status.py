"""Tests for the runtime hardware fault registry.

The behaviour under test is the one that used to be missing entirely: a
device that fails to start must produce something the UI can render, and a
blocking fault must be distinguishable from a degraded one — that distinction
is what decides whether the kiosk covers the screen or shows a banner.
"""

import threading

import pytest

from openflight.hardware_status import HardwareFault, HardwareStatus, Severity
from openflight.provisioning.detect import DeviceKind


class TestHardwareFault:
    def test_carries_an_owner_facing_title_and_remedy(self):
        fault = HardwareFault(DeviceKind.OPS243, Severity.BLOCKING)
        assert fault.title == "Radar not found"
        assert "USB cable" in fault.remedy

    def test_unknown_device_still_produces_a_usable_title(self):
        """A device added later must not render as a blank headline."""
        fault = HardwareFault(DeviceKind.CAMERA, Severity.DEGRADED)
        assert fault.title
        assert fault.title != ""

    def test_to_dict_is_wire_shaped(self):
        fault = HardwareFault(DeviceKind.IWR6843, Severity.DEGRADED, detail="no CLI response")
        payload = fault.to_dict()
        assert payload["device"] == "iwr6843"
        assert payload["severity"] == "degraded"
        assert payload["detail"] == "no CLI response"
        assert payload["title"]
        assert payload["remedy"]


class TestRecording:
    def test_records_and_retrieves(self):
        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING, "cable unplugged")
        fault = status.get(DeviceKind.OPS243)
        assert fault is not None
        assert fault.detail == "cable unplugged"

    def test_recording_twice_replaces_rather_than_duplicates(self):
        """A monitor restart must not stack two faults for the same device."""
        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING, "first")
        status.record(DeviceKind.OPS243, Severity.BLOCKING, "second")
        assert len(status.faults) == 1
        assert status.faults[0].detail == "second"

    def test_clear_removes_one_device(self):
        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING)
        status.record(DeviceKind.IWR6843, Severity.DEGRADED)
        status.clear(DeviceKind.OPS243)
        assert status.get(DeviceKind.OPS243) is None
        assert status.get(DeviceKind.IWR6843) is not None

    def test_clear_is_safe_for_an_unrecorded_device(self):
        HardwareStatus().clear(DeviceKind.BATTERY)

    def test_clear_all(self):
        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING)
        status.record(DeviceKind.IWR6843, Severity.DEGRADED)
        status.clear_all()
        assert status.faults == ()

    def test_record_returns_the_fault(self):
        status = HardwareStatus()
        fault = status.record(DeviceKind.OPS243, Severity.BLOCKING, "why")
        assert fault.device is DeviceKind.OPS243
        assert fault.detail == "why"


class TestBlocking:
    def test_none_when_only_degraded(self):
        status = HardwareStatus()
        status.record(DeviceKind.IWR6843, Severity.DEGRADED)
        status.record(DeviceKind.BATTERY, Severity.DEGRADED)
        assert status.blocking is None

    def test_found_among_degraded_faults(self):
        status = HardwareStatus()
        status.record(DeviceKind.IWR6843, Severity.DEGRADED)
        status.record(DeviceKind.OPS243, Severity.BLOCKING)
        assert status.blocking is not None
        assert status.blocking.device is DeviceKind.OPS243

    def test_none_when_nothing_failed(self):
        assert HardwareStatus().blocking is None

    def test_blocking_faults_sort_first(self):
        """The UI takes faults[0] for its headline; it must be the worst one."""
        status = HardwareStatus()
        status.record(DeviceKind.BATTERY, Severity.DEGRADED)
        status.record(DeviceKind.IWR6843, Severity.DEGRADED)
        status.record(DeviceKind.OPS243, Severity.BLOCKING)
        assert status.faults[0].severity is Severity.BLOCKING


class TestToDict:
    def test_healthy_system(self):
        payload = HardwareStatus().to_dict(radar_connected=True)
        assert payload["ok"] is True
        assert payload["blocking"] is None
        assert payload["faults"] == []
        assert payload["radar_connected"] is True

    def test_missing_radar(self):
        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING, "no OPS243 found")
        payload = status.to_dict(radar_connected=False)
        assert payload["ok"] is False
        assert payload["radar_connected"] is False
        assert payload["blocking"]["device"] == "ops243"
        assert len(payload["faults"]) == 1

    def test_degraded_only(self):
        status = HardwareStatus()
        status.record(DeviceKind.IWR6843, Severity.DEGRADED)
        payload = status.to_dict(radar_connected=True)
        assert payload["ok"] is False
        assert payload["blocking"] is None
        assert len(payload["faults"]) == 1

    def test_not_ok_when_the_radar_dropped_without_a_recorded_fault(self):
        """An unplug after a good start leaves no fault, but is not "ok"."""
        payload = HardwareStatus().to_dict(radar_connected=False)
        assert payload["ok"] is False

    def test_payload_is_json_safe(self):
        import json

        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING, "detail")
        json.dumps(status.to_dict(radar_connected=False))


class TestConsoleSummary:
    def test_says_so_when_everything_started(self):
        assert "All requested hardware started" in HardwareStatus().console_summary()

    def test_labels_blocking_and_degraded_differently(self):
        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING)
        status.record(DeviceKind.IWR6843, Severity.DEGRADED)
        summary = status.console_summary()
        assert "ERROR:" in summary
        assert "WARNING:" in summary

    def test_includes_the_detail_when_present(self):
        status = HardwareStatus()
        status.record(DeviceKind.OPS243, Severity.BLOCKING, "port /dev/ttyACM0 vanished")
        assert "port /dev/ttyACM0 vanished" in status.console_summary()


class TestThreadSafety:
    def test_concurrent_records_do_not_lose_faults(self):
        """Start-up records from the main thread while readers are running."""
        status = HardwareStatus()
        devices = list(DeviceKind)

        def worker(device):
            for _ in range(200):
                status.record(device, Severity.DEGRADED)
                status.get(device)
                _ = status.faults

        threads = [threading.Thread(target=worker, args=(d,)) for d in devices]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(status.faults) == len(devices)


@pytest.mark.parametrize("device", list(DeviceKind))
def test_every_device_has_owner_facing_guidance(device):
    """A device with no remedy leaves the owner with a headline and no action."""
    fault = HardwareFault(device, Severity.DEGRADED)
    assert fault.title, f"{device} has no title"
    assert fault.remedy, f"{device} has no remedy"
