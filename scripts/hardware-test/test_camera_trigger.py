#!/usr/bin/env python3
"""
Hardware checks for the camera ball-at-address trigger (replaces the sound trigger).

Three independent checks, run on the Pi with the unit assembled:

  --s-bang       OPS243 in its *persisted* rolling-buffer mode accepts a
                 software S! (no GS/GC mode switch) and dumps a parseable
                 buffer. Reports S!-write -> first-byte latency.

  --host-int     With the SEN-14262 removed, HOST_INT must not float. Waits
                 with no trigger source and fails on any unsolicited dump.
                 (Tie HOST_INT / J3 pin 3 to GND through 10k if this fails.)

  --live         Runs the camera address state machine and prints every state
                 change. With --with-radar, each confirmed departure fires S!
                 and reports impact -> S! latency and the dump's peak speed.

Usage:
    uv run python scripts/hardware-test/test_camera_trigger.py --s-bang
    uv run python scripts/hardware-test/test_camera_trigger.py --host-int --seconds 60
    uv run python scripts/hardware-test/test_camera_trigger.py --live
    uv run python scripts/hardware-test/test_camera_trigger.py --live --with-radar
"""

import argparse
import sys
import time

sys.path.insert(0, "src")

from openflight.ops243 import OPS243Radar  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.trigger import CameraTrigger  # noqa: E402


def connect_radar(port, pre_trigger):
    """Connect and arm the persisted rolling buffer exactly as the server does."""
    radar = OPS243Radar(port=port)
    radar.connect()
    radar.prepare_persisted_rolling_buffer(pre_trigger_segments=pre_trigger, sample_rate_ksps=30)
    print(f"  OPS243 on {radar.port}, S#{pre_trigger}")
    return radar


def peak_outbound_mph(processor, response):
    """Parse a dump and return (parsed, peak outbound mph)."""
    capture = processor.parse_capture(response)
    if capture is None:
        return False, 0.0
    timeline = processor.process_standard(capture)
    return True, max((r.speed_mph for r in timeline.readings if r.is_outbound), default=0.0)


def check_s_bang(args) -> bool:
    """S! must work in persisted mode without a runtime mode switch."""
    print("=" * 60)
    print("  S! in persisted rolling-buffer mode")
    print("=" * 60)
    radar = connect_radar(args.port, args.pre_trigger)
    processor = RollingBufferProcessor()
    ok = True
    try:
        for attempt in range(1, args.count + 1):
            response = radar.trigger_capture()
            parsed, peak = peak_outbound_mph(processor, response)
            write_ts = radar.last_software_trigger_write_timestamp
            first_ts = radar.last_software_trigger_first_byte_timestamp
            latency = (
                f"{(first_ts - write_ts) * 1000:.1f}ms"
                if write_ts is not None and first_ts is not None
                else "n/a"
            )
            status = "OK " if parsed else "FAIL"
            print(
                f"  [{status}] #{attempt}: {len(response)} bytes, "
                f"S!->first byte {latency}, peak {peak:.1f} mph"
            )
            ok = ok and parsed
            radar.rearm_rolling_buffer(args.pre_trigger)
            time.sleep(0.5)
    finally:
        radar.disconnect()
    print("  PASS" if ok else "  FAIL: S! did not produce a parseable dump in persisted mode")
    return ok


def check_host_int(args) -> bool:
    """No sound sensor: HOST_INT must stay quiet."""
    print("=" * 60)
    print(f"  HOST_INT idle check ({args.seconds:.0f}s, keep the hitting area still)")
    print("=" * 60)
    radar = connect_radar(args.port, args.pre_trigger)
    try:
        response = radar.wait_for_hardware_trigger(timeout=args.seconds)
    finally:
        radar.disconnect()
    if response:
        print(f"  FAIL: unsolicited dump ({len(response)} bytes) — HOST_INT is floating.")
        print("        Tie OPS J3 pin 3 to GND through a 10k resistor.")
        return False
    print("  PASS: no unsolicited dumps")
    return True


def run_live(args) -> bool:
    """Print state changes; optionally fire S! on each departure."""
    # pylint: disable=import-outside-toplevel
    from openflight.camera.address_monitor import CameraAddressMonitor, ball_detector_acquirer
    from openflight.camera.address_trigger import AddressTriggerConfig
    from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings

    runtime = CameraCaptureRuntime(
        output_dir="/tmp/openflight-camera-trigger-test",
        settings=CameraCaptureSettings(fps=args.fps),
        use_gpio_trigger=False,
    )
    monitor = CameraAddressMonitor(
        AddressTriggerConfig(
            gone_frames=args.gone_frames,
            require_address=not args.no_require_address,
        ),
        acquire_fn=ball_detector_acquirer(),
    )
    radar = connect_radar(args.port, args.pre_trigger) if args.with_radar else None
    trigger = CameraTrigger(pre_trigger_segments=args.pre_trigger, address_monitor=monitor)
    processor = RollingBufferProcessor()

    runtime.add_frame_observer(monitor.on_frame)
    runtime.start()
    monitor.start()
    print("  Place a ball, address it, and hit. Ctrl+C to stop.")
    last_state = None
    try:
        while True:
            status = monitor.status()
            if status["state"] != last_state:
                last_state = status["state"]
                print(
                    f"  state={last_state:14s} ball={status['ball']} "
                    f"callback p99={status['callback_us_p99']}us healthy={status['healthy']}"
                )
            if radar is None:
                event = monitor.wait_for_trigger(timeout=0.1)
                if event is not None:
                    print(f"  TRIGGER {event.to_dict()}")
                    monitor.rearm()
                continue
            capture = trigger.wait_for_trigger(radar, processor, timeout=0.1)
            for diag in trigger.drain_diagnostics():
                camera = diag.get("camera", {})
                print(
                    f"  TRIGGER {diag['reason']}: impact->S! "
                    f"{camera.get('impact_to_s_bang_ms')}ms of "
                    f"{camera.get('pre_trigger_window_ms')}ms window, "
                    f"peak {diag.get('peak_outbound_mph', 0):.1f} mph"
                )
            if capture is not None:
                print(f"  capture accepted (camera impact {capture.camera_impact_epoch:.3f})")
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        runtime.stop()
        if radar is not None:
            radar.disconnect()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--s-bang", action="store_true", help="Verify S! in persisted mode")
    mode.add_argument("--host-int", action="store_true", help="Verify HOST_INT does not float")
    mode.add_argument("--live", action="store_true", help="Live camera state machine")
    parser.add_argument("--port", default=None, help="OPS243 serial port (auto-detect)")
    parser.add_argument(
        "--pre-trigger",
        type=int,
        default=CameraTrigger.DEFAULT_PRE_TRIGGER_SEGMENTS,
        help="S#n pre-trigger segments (default: 28)",
    )
    parser.add_argument("--count", type=int, default=3, help="S! attempts for --s-bang")
    parser.add_argument("--seconds", type=float, default=30.0, help="Idle time for --host-int")
    parser.add_argument("--with-radar", action="store_true", help="--live: fire S! on departure")
    parser.add_argument("--fps", type=float, default=300.0)
    parser.add_argument("--gone-frames", type=int, default=9)
    parser.add_argument("--no-require-address", action="store_true")
    args = parser.parse_args()

    if args.s_bang:
        ok = check_s_bang(args)
    elif args.host_int:
        ok = check_host_int(args)
    else:
        ok = run_live(args)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
