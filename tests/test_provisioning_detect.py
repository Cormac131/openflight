"""Tests for hardware probing.

Every probe is exercised through an injected fake bus rather than real
hardware, so the interesting cases — an IWR6843 and a K-LD7 on adjacent
ttyUSB nodes, a CP2105's two interfaces, an I2C address that answers with
the wrong identity — are all reachable.
"""

from types import SimpleNamespace

import pytest

from openflight.provisioning.detect import (
    DetectedDevice,
    DeviceKind,
    HardwareProfile,
    detect_geekworm_battery,
    detect_hardware,
    detect_inclinometer,
    detect_iwr6843_port,
    detect_kld7_ports,
    detect_ops243_port,
    detect_pi_camera,
)

# Real USB IDs for the boards OpenFlight supports.
CP2105 = (0x10C4, 0xEA70)  # IWR6843 EVM
XDS110 = (0x0451, 0xBEF3)  # IWR6843 EVM debug probe
FT232R = (0x0403, 0x6001)  # K-LD7 EVAL
CP2102 = (0x10C4, 0xEA60)  # K-LD7 EVAL (later revisions)


def port(device, *, usb_id=(None, None), description="", manufacturer="", location=None):
    """Build a stand-in for pyserial's ListPortInfo."""
    return SimpleNamespace(
        device=device,
        vid=usb_id[0],
        pid=usb_id[1],
        description=description,
        manufacturer=manufacturer,
        location=location,
    )


def comports_of(*ports):
    return lambda: list(ports)


class FakeBus:
    """An SMBus whose contents the test declares.

    ``registers`` maps (address, register) to the byte read back. Anything
    not in the map raises OSError, the way a real bus does for an address
    nobody answers on.
    """

    def __init__(self, registers=None, *, open_error=None):
        self.registers = registers or {}
        self.open_error = open_error
        self.closed = False

    def read_byte_data(self, address, register):
        try:
            return self.registers[(address, register)]
        except KeyError:
            raise OSError(121, "Remote I/O error") from None

    def close(self):
        self.closed = True


def bus_factory_for(bus):
    def factory(_bus_number):
        if bus.open_error is not None:
            raise bus.open_error
        return bus

    return factory


class TestDetectOps243Port:
    def test_finds_cdc_acm_device(self):
        found = detect_ops243_port(comports_of(port("/dev/ttyACM0")))
        assert found == "/dev/ttyACM0"

    def test_finds_macos_usbmodem(self):
        found = detect_ops243_port(comports_of(port("/dev/cu.usbmodem14301")))
        assert found == "/dev/cu.usbmodem14301"

    def test_ignores_usb_serial_bridges(self):
        found = detect_ops243_port(
            comports_of(port("/dev/ttyUSB0", usb_id=FT232R)),
            path_exists=lambda _p: False,
        )
        assert found is None

    def test_falls_back_to_gpio_uart_when_no_usb_radar(self):
        found = detect_ops243_port(
            comports_of(),
            path_exists=lambda p: p == "/dev/ttyAMA0",
        )
        assert found == "/dev/ttyAMA0"

    def test_usb_radar_wins_over_uart_node(self):
        """A present /dev/ttyAMA0 must not shadow a real enumerated radar."""
        found = detect_ops243_port(
            comports_of(port("/dev/ttyACM0")),
            path_exists=lambda _p: True,
        )
        assert found == "/dev/ttyACM0"

    def test_include_uart_false_restricts_to_usb(self):
        found = detect_ops243_port(
            comports_of(),
            include_uart=False,
            path_exists=lambda _p: True,
        )
        assert found is None

    def test_returns_none_when_nothing_attached(self):
        assert detect_ops243_port(comports_of(), path_exists=lambda _p: False) is None

    def test_survives_a_broken_serial_stack(self):
        def explode():
            raise OSError("no serial subsystem")

        assert detect_ops243_port(explode, path_exists=lambda _p: False) is None


class TestDetectIwr6843Port:
    def test_finds_cp2105(self):
        found = detect_iwr6843_port(comports_of(port("/dev/ttyUSB0", usb_id=CP2105)))
        assert found == "/dev/ttyUSB0"

    def test_finds_xds110(self):
        found = detect_iwr6843_port(comports_of(port("/dev/ttyACM1", usb_id=XDS110)))
        assert found == "/dev/ttyACM1"

    def test_prefers_the_cp2105_enhanced_interface(self):
        """Only interface 0 carries the L3 dump; enumeration order is not a guide."""
        found = detect_iwr6843_port(
            comports_of(
                port("/dev/ttyUSB0", usb_id=CP2105, location="1-1.2:1.1"),
                port("/dev/ttyUSB1", usb_id=CP2105, location="1-1.2:1.0"),
            )
        )
        assert found == "/dev/ttyUSB1"

    def test_falls_back_to_lowest_device_without_location_info(self):
        found = detect_iwr6843_port(
            comports_of(
                port("/dev/ttyUSB1", usb_id=CP2105),
                port("/dev/ttyUSB0", usb_id=CP2105),
            )
        )
        assert found == "/dev/ttyUSB0"

    def test_ignores_kld7_bridges(self):
        found = detect_iwr6843_port(
            comports_of(
                port("/dev/ttyUSB0", usb_id=FT232R, description="FTDI USB-Serial"),
            )
        )
        assert found is None

    def test_returns_none_when_nothing_attached(self):
        assert detect_iwr6843_port(comports_of()) is None

    def test_probe_picks_the_port_that_answers(self, monkeypatch):
        """Two boards on the bus: only the one running our firmware counts."""
        monkeypatch.setattr(
            "openflight.provisioning.detect._probe_iwr6843_cli",
            lambda: "/dev/ttyUSB1",
        )
        found = detect_iwr6843_port(
            comports_of(
                port("/dev/ttyUSB0", usb_id=CP2105, location="1-1.2:1.0"),
                port("/dev/ttyUSB1", usb_id=CP2105, location="1-1.3:1.0"),
            ),
            probe=True,
        )
        assert found == "/dev/ttyUSB1"

    def test_probe_rejects_an_answer_from_an_unrecognised_port(self, monkeypatch):
        """An answer from outside the USB-ID list means the ID list is wrong."""
        monkeypatch.setattr(
            "openflight.provisioning.detect._probe_iwr6843_cli",
            lambda: "/dev/ttyUSB9",
        )
        found = detect_iwr6843_port(comports_of(port("/dev/ttyUSB0", usb_id=CP2105)), probe=True)
        assert found is None

    def test_probe_rejects_a_usb_id_match_that_does_not_answer(self, monkeypatch):
        """A CP2105 that is not running our firmware must not be reported."""
        monkeypatch.setattr("openflight.provisioning.detect._probe_iwr6843_cli", lambda: None)
        found = detect_iwr6843_port(
            comports_of(port("/dev/ttyUSB0", usb_id=CP2105)),
            probe=True,
        )
        assert found is None


class TestDetectKld7Ports:
    def test_udev_symlinks_win(self):
        found = detect_kld7_ports(
            comports_of(port("/dev/ttyUSB0", usb_id=FT232R)),
            path_exists=lambda p: p in ("/dev/kld7_vertical", "/dev/kld7_horizontal"),
        )
        assert found == ["/dev/kld7_vertical", "/dev/kld7_horizontal"]

    def test_single_udev_symlink(self):
        found = detect_kld7_ports(
            comports_of(),
            path_exists=lambda p: p == "/dev/kld7_vertical",
        )
        assert found == ["/dev/kld7_vertical"]

    def test_finds_ftdi_by_usb_id(self):
        found = detect_kld7_ports(
            comports_of(port("/dev/ttyUSB0", usb_id=FT232R)),
            path_exists=lambda _p: False,
        )
        assert found == ["/dev/ttyUSB0"]

    def test_finds_cp2102_by_usb_id(self):
        found = detect_kld7_ports(
            comports_of(port("/dev/ttyUSB1", usb_id=CP2102)),
            path_exists=lambda _p: False,
        )
        assert found == ["/dev/ttyUSB1"]

    def test_falls_back_to_description(self):
        found = detect_kld7_ports(
            comports_of(port("/dev/ttyUSB0", description="FTDI USB-Serial")),
            path_exists=lambda _p: False,
        )
        assert found == ["/dev/ttyUSB0"]

    def test_falls_back_to_manufacturer(self):
        found = detect_kld7_ports(
            comports_of(port("/dev/ttyUSB0", manufacturer="Silicon Labs")),
            path_exists=lambda _p: False,
        )
        assert found == ["/dev/ttyUSB0"]

    def test_excludes_the_iwr6843_sharing_the_bus(self):
        """The CP2105 also says "CP210x"; the USB ID has to win over the string."""
        found = detect_kld7_ports(
            comports_of(
                port(
                    "/dev/ttyUSB0",
                    usb_id=CP2105,
                    description="CP2105 Dual USB to UART Bridge",
                ),
                port("/dev/ttyUSB1", usb_id=FT232R, description="FT232R USB UART"),
            ),
            path_exists=lambda _p: False,
        )
        assert found == ["/dev/ttyUSB1"]

    def test_excludes_the_ops243(self):
        found = detect_kld7_ports(
            comports_of(port("/dev/ttyACM0", description="OPS243 UART")),
            path_exists=lambda _p: False,
        )
        assert found == []

    def test_returns_sorted_pair(self):
        found = detect_kld7_ports(
            comports_of(
                port("/dev/ttyUSB1", usb_id=FT232R),
                port("/dev/ttyUSB0", usb_id=FT232R),
            ),
            path_exists=lambda _p: False,
        )
        assert found == ["/dev/ttyUSB0", "/dev/ttyUSB1"]

    def test_returns_empty_when_nothing_attached(self):
        assert detect_kld7_ports(comports_of(), path_exists=lambda _p: False) == []


class TestDetectInclinometer:
    def test_finds_lis3dh_at_default_address(self):
        bus = FakeBus({(0x18, 0x0F): 0x33})
        assert detect_inclinometer(bus_factory_for(bus)) == 0x18

    def test_finds_lis3dh_at_alternate_address(self):
        bus = FakeBus({(0x19, 0x0F): 0x33})
        assert detect_inclinometer(bus_factory_for(bus)) == 0x19

    def test_rejects_a_different_chip_at_the_same_address(self):
        """Plenty of parts answer at 0x18 — only WHO_AM_I identifies a LIS3DH."""
        bus = FakeBus({(0x18, 0x0F): 0x41})
        assert detect_inclinometer(bus_factory_for(bus)) is None

    def test_returns_none_on_empty_bus(self):
        assert detect_inclinometer(bus_factory_for(FakeBus())) is None

    def test_returns_none_when_no_i2c_bus_exists(self):
        bus = FakeBus(open_error=FileNotFoundError("/dev/i2c-1"))
        assert detect_inclinometer(bus_factory_for(bus)) is None

    def test_closes_the_bus(self):
        bus = FakeBus({(0x18, 0x0F): 0x33})
        detect_inclinometer(bus_factory_for(bus))
        assert bus.closed


class TestDetectGeekwormBattery:
    @pytest.mark.parametrize("msb, expected", [(0xD0, 0x36), (0xC8, 0x36)])
    def test_finds_gauge_at_plausible_cell_voltage(self, msb, expected):
        # 0xD0 << 4 = 3328 counts * 1.25 mV = 4.16 V; 0xC8 -> 4.00 V.
        bus = FakeBus({(0x36, 0x02): msb})
        assert detect_geekworm_battery(bus_factory_for(bus)) == expected

    def test_rejects_a_stuck_low_bus(self):
        bus = FakeBus({(0x36, 0x02): 0x00})
        assert detect_geekworm_battery(bus_factory_for(bus)) is None

    def test_rejects_a_stuck_high_bus(self):
        bus = FakeBus({(0x36, 0x02): 0xFF})
        assert detect_geekworm_battery(bus_factory_for(bus)) is None

    def test_returns_none_on_empty_bus(self):
        assert detect_geekworm_battery(bus_factory_for(FakeBus())) is None


class TestDetectPiCamera:
    def _run_returning(self, stdout="", stderr=""):
        def run(_cmd, **_kwargs):
            return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0)

        return run

    def test_reports_a_listed_camera(self):
        listing = "Available cameras\n-----------------\n0 : imx296 [1456x1088] (/base/axi/pcie@0/rp1/i2c@88000/imx296@1a)"
        found = detect_pi_camera(
            run=self._run_returning(stdout=listing),
            which=lambda tool: f"/usr/bin/{tool}",
            video_glob=lambda _pattern: [],
        )
        assert found is not None
        assert "imx296" in found

    def test_returns_none_when_libcamera_reports_none(self):
        found = detect_pi_camera(
            run=self._run_returning(stderr="No cameras available!"),
            which=lambda tool: f"/usr/bin/{tool}",
            video_glob=lambda _pattern: ["/dev/video0"],
        )
        assert found is None

    def test_falls_back_to_a_video_node(self):
        found = detect_pi_camera(
            run=self._run_returning(),
            which=lambda _tool: None,
            video_glob=lambda _pattern: ["/dev/video1", "/dev/video0"],
        )
        assert found is not None
        assert "/dev/video0" in found

    def test_returns_none_with_no_tooling_and_no_nodes(self):
        found = detect_pi_camera(
            run=self._run_returning(),
            which=lambda _tool: None,
            video_glob=lambda _pattern: [],
        )
        assert found is None

    def test_survives_a_tool_that_cannot_run(self):
        def run(_cmd, **_kwargs):
            raise OSError("Exec format error")

        found = detect_pi_camera(
            run=run,
            which=lambda tool: f"/usr/bin/{tool}",
            video_glob=lambda _pattern: [],
        )
        assert found is None


class TestHardwareProfile:
    def test_get_returns_placeholder_for_unprobed_kind(self):
        profile = HardwareProfile()
        entry = profile.get(DeviceKind.OPS243)
        assert entry.present is False
        assert entry.detail == "not probed"

    def test_present_kinds_preserves_probe_order(self):
        profile = HardwareProfile(
            devices=(
                DetectedDevice(DeviceKind.OPS243, True, "/dev/ttyACM0"),
                DetectedDevice(DeviceKind.IWR6843, False),
                DetectedDevice(DeviceKind.BATTERY, True, "0x36"),
            )
        )
        assert profile.present_kinds() == (DeviceKind.OPS243, DeviceKind.BATTERY)

    def test_to_dict_is_json_shaped(self):
        profile = HardwareProfile(
            devices=(DetectedDevice(DeviceKind.OPS243, True, "/dev/ttyACM0", "USB CDC-ACM"),)
        )
        assert profile.to_dict() == {
            "devices": [
                {
                    "kind": "ops243",
                    "present": True,
                    "address": "/dev/ttyACM0",
                    "detail": "USB CDC-ACM",
                }
            ]
        }


class TestDetectHardware:
    def test_full_iwr6843_build(self):
        profile = detect_hardware(
            comports=comports_of(
                port("/dev/ttyACM0", description="OPS243"),
                port("/dev/ttyUSB0", usb_id=CP2105, location="1-1.2:1.0"),
            ),
            bus_factory=bus_factory_for(FakeBus({(0x18, 0x0F): 0x33, (0x36, 0x02): 0xD0})),
            path_exists=lambda _p: False,
            include_camera=False,
        )
        assert profile.get(DeviceKind.OPS243).address == "/dev/ttyACM0"
        assert profile.get(DeviceKind.OPS243).detail == "USB CDC-ACM"
        assert profile.get(DeviceKind.IWR6843).address == "/dev/ttyUSB0"
        assert profile.has(DeviceKind.INCLINOMETER)
        assert profile.has(DeviceKind.BATTERY)
        assert not profile.has(DeviceKind.KLD7_VERTICAL)
        assert not profile.has(DeviceKind.CAMERA)

    def test_legacy_kld7_pair(self):
        profile = detect_hardware(
            comports=comports_of(
                port("/dev/ttyACM0"),
                port("/dev/ttyUSB0", usb_id=FT232R),
                port("/dev/ttyUSB1", usb_id=FT232R),
            ),
            bus_factory=bus_factory_for(FakeBus()),
            path_exists=lambda _p: False,
            include_camera=False,
        )
        assert profile.get(DeviceKind.KLD7_VERTICAL).address == "/dev/ttyUSB0"
        assert profile.get(DeviceKind.KLD7_HORIZONTAL).address == "/dev/ttyUSB1"
        assert profile.get(DeviceKind.KLD7_VERTICAL).detail == "USB ID match"

    def test_udev_mapped_kld7_is_labelled_as_such(self):
        profile = detect_hardware(
            comports=comports_of(port("/dev/ttyACM0")),
            bus_factory=bus_factory_for(FakeBus()),
            path_exists=lambda p: p.startswith("/dev/kld7_"),
            include_camera=False,
        )
        assert profile.get(DeviceKind.KLD7_VERTICAL).address == "/dev/kld7_vertical"
        assert profile.get(DeviceKind.KLD7_VERTICAL).detail == "udev-mapped"

    def test_bare_ops243_only_build(self):
        profile = detect_hardware(
            comports=comports_of(port("/dev/ttyACM0")),
            bus_factory=bus_factory_for(FakeBus()),
            path_exists=lambda _p: False,
            include_camera=False,
        )
        assert profile.present_kinds() == (DeviceKind.OPS243,)

    def test_uart_wiring_is_labelled(self):
        profile = detect_hardware(
            comports=comports_of(),
            bus_factory=bus_factory_for(FakeBus()),
            path_exists=lambda p: p == "/dev/ttyAMA0",
            include_camera=False,
        )
        ops = profile.get(DeviceKind.OPS243)
        assert ops.address == "/dev/ttyAMA0"
        assert ops.detail == "GPIO UART"

    def test_nothing_attached(self):
        profile = detect_hardware(
            comports=comports_of(),
            bus_factory=bus_factory_for(FakeBus()),
            path_exists=lambda _p: False,
            include_camera=False,
        )
        assert profile.present_kinds() == ()
        # Every kind is still reported, so a report can say "absent" rather
        # than silently omitting hardware the owner expected to see.
        assert len(profile.devices) == len(DeviceKind)
