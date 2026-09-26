"""Unit tests for the IWR6843 firmware CLI check suite (no hardware)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from openflight.iwr6843 import firmware_checks as fc
from openflight.iwr6843.driver import IWR6843Radar, UnsupportedCommand
from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.sparse import OnboardTrack
from tests.iwr6843_fakes import (
    ScriptedSerial,
    parse_cell_request,
    power_packet,
    scripted_radar,
    slice_packet,
    vertical_loop_power,
)

# Verbatim shape of the four lines l3_cli_stats writes (firmware/iwr6843/l3_dump.c).
STATS_ACTIVE = (
    "frames=112345 wraps=4 active=1 calib=0x0 rf_faults=0 "
    "hwa_frames=112345 hwa_out=112345 hwa_rearms=112344 hwa_rearm_err=0 "
    "hwa_missed=10 freeze_req=2 freeze_done=2 freeze_to=0 "
    "format=iq16 plan=16pre/8post loops=12 used=737280/786432\n"
    "iq8_packed=0 iq8_overrun=0 iq8_clipped=0 pending=0 pre_seen=112345 "
    "post_kept=0 post_seen=0 stride=1 iq8_edma_done=0 iq8_edma_err=0 "
    "iq8_edma_wait=0 iq8_busy=0/1 iq8_scale=128\n"
    "trig phase=tee-low tee=412 latched=0 enabled=1\n"
    "detect dropped=0 stale=0\n"
    "rearm_last_us=120 rearm_max_us=310 rearm_timed=112344\n"
    "Done\n"
)


def test_parse_stats_reads_every_integer_field():
    stats = fc.parse_stats(STATS_ACTIVE)

    assert stats["frames"] == 112345
    assert stats["hwa_missed"] == 10
    assert stats["freeze_done"] == 2
    assert stats["rearm_max_us"] == 310


def test_parse_trig_reads_stats_and_debug_lines():
    stats = fc.parse_trig("trig phase=watching tee=1800 latched=0 enabled=1")
    debug = fc.parse_trig(
        "trig phase=toward tee=10 approach=4 ready=1 toward=1 away=0 "
        "run=3 peak=8 have=1 bin=14 level=1000 latched=0"
    )

    assert stats == {"phase": "watching", "tee": "1800", "latched": "0", "enabled": "1"}
    assert debug["phase"] == "toward"
    assert debug["level"] == "1000"
    assert fc.parse_trig("frames=1 active=1") is None


def test_snapshot_parses_all_four_real_stats_lines():
    snap = fc.parse_snapshot(STATS_ACTIVE)

    assert snap.active == 1
    assert snap.frames == 112345
    assert snap.pre_seen == 112345
    assert (snap.plan_pre, snap.plan_post) == (16, 8)
    assert (snap.freeze_req, snap.freeze_done) == (2, 2)
    assert snap.format == "iq16"
    assert snap.stride == 1
    assert (snap.used, snap.capacity) == (737280, 786432)
    assert (snap.rearm_last_us, snap.rearm_max_us, snap.rearm_timed) == (120, 310, 112344)
    assert (snap.detect_dropped, snap.detect_stale) == (0, 0)
    assert (snap.phase, snap.tee, snap.latched, snap.enabled) == ("tee-low", 412, 0, 1)
    assert snap.raw == STATS_ACTIVE


def test_snapshot_reports_missing_fields_as_none():
    snap = fc.parse_snapshot("frames=5 wraps=0 active=0 calib=0x0 rf_faults=0\nDone\n")

    assert snap.active == 0
    assert snap.plan_pre is None
    assert snap.rearm_max_us is None
    assert snap.phase is None
    assert snap.latched is None


STATS_STOPPED = "frames=0 wraps=0 active=0 calib=0x0 rf_faults=0\ntrig phase=off tee=0 latched=0 enabled=0\nDone\n"


def _ctx(radar, **overrides) -> fc.Context:
    ticks = {"now": 0.0}

    def clock():
        ticks["now"] += 0.05
        return ticks["now"]

    fields = dict(
        radar=radar,
        config="config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
        tee_m=1.575,
        level=1000.0,
        hits=2,
        wait_s=5.0,
        shots=2,
        profiles=("config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",),
        prompt=lambda _text: None,
        sleep=lambda _s: None,
        clock=clock,
        out=lambda _line: None,
    )
    fields.update(overrides)
    return fc.Context(**fields)


def test_scripted_serial_matches_full_line_then_first_token():
    port = ScriptedSerial({"triggerCfg 0 0 0": b"Done\n", "triggerCfg": b"Error: trigger bin\n"})

    port.write(b"triggerCfg 0 0 0\n")
    off = port.read(port.in_waiting)
    port.write(b"triggerCfg x 1 2\n")
    bad = port.read(port.in_waiting)
    port.write(b"bogus\n")
    unknown = port.read(port.in_waiting)

    assert off == b"Done\n"
    assert bad == b"Error: trigger bin\n"
    assert b"not recognized" in unknown
    assert port.written == ["triggerCfg 0 0 0", "triggerCfg x 1 2", "bogus"]


def test_scripted_serial_callable_replies_count_sends_and_inject_precedes_reads():
    port = ScriptedSerial({"stats": lambda n: f"frames={n * 100} active=1\nDone\n".encode()})

    port.inject(b"Triggered\n")
    port.write(b"stats\n")
    first = port.read(port.in_waiting)
    port.write(b"stats\n")
    second = port.read(port.in_waiting)

    assert first == b"Triggered\nframes=0 active=1\nDone\n"
    assert second == b"frames=100 active=1\nDone\n"


def test_scripted_serial_reset_drops_unread_bytes():
    port = ScriptedSerial({})
    port.inject(b"stale")

    port.reset_input_buffer()

    assert port.in_waiting == 0


def test_stats_snapshot_sends_stats_and_parses_the_reply():
    radar = scripted_radar({"stats": STATS_ACTIVE.encode()})

    snap = fc.stats_snapshot(_ctx(radar))

    assert radar.ser.written == ["stats"]
    assert snap.active == 1 and snap.phase == "tee-low"


def test_wait_until_polls_until_true_or_deadline():
    calls = {"n": 0}

    def predicate():
        calls["n"] += 1
        return calls["n"] >= 3

    ctx = _ctx(scripted_radar({}))

    assert fc.wait_until(ctx, predicate, timeout_s=5.0) is True
    assert calls["n"] == 3
    assert fc.wait_until(ctx, lambda: False, timeout_s=0.2) is False


def test_read_port_text_collects_bytes_until_the_window_closes():
    radar = scripted_radar({})
    radar.ser.inject(b"trig phase=tee-low tee=5\n")

    text = fc.read_port_text(_ctx(radar), seconds=0.3)

    assert "phase=tee-low" in text


def test_ensure_sensor_stops_an_active_sensor_and_starts_a_stopped_one(monkeypatch):
    calls: list[str] = []
    radar = scripted_radar(
        {"stats": lambda n: (STATS_ACTIVE if n == 0 else STATS_STOPPED).encode()}
    )
    monkeypatch.setattr(radar, "stop_sensor", lambda: calls.append("stop"))
    monkeypatch.setattr(radar, "send_config", lambda cfg: calls.append(f"start:{cfg}"))
    ctx = _ctx(radar, config="wide.cfg")

    fc.ensure_sensor(ctx, "stopped")
    fc.ensure_sensor(ctx, "active")
    fc.ensure_sensor(ctx, "any")

    assert calls == ["stop", "start:wide.cfg"]


def test_ensure_sensor_is_a_no_op_when_already_in_state(monkeypatch):
    calls: list[str] = []
    radar = scripted_radar({"stats": STATS_ACTIVE.encode()})
    monkeypatch.setattr(radar, "send_config", lambda cfg: calls.append("start"))

    fc.ensure_sensor(_ctx(radar), "active")

    assert calls == []


def _section(name, sensor="any", *checks):
    return fc.Section(name, sensor, tuple(checks))


def _check(name, result=None, exc=None, needs_swing=False):
    def run(_ctx):
        if exc is not None:
            raise exc
        return result if result is not None else fc.passed(name)

    return fc.Check(name, run, needs_swing)


def _stoppedish_radar():
    return scripted_radar(
        {
            "stats": STATS_STOPPED.encode(),
            "triggerCfg": b"Done\n",
            "debugCfg": b"Done\n",
            "sensorStop": b"Done\n",
        }
    )


def test_run_reports_results_in_catalogue_order_and_prints_them():
    lines: list[str] = []
    sections = (
        _section("a", "any", _check("a/one"), _check("a/two", fc.failed("a/two", "boom"))),
        _section("b", "any", _check("b/one")),
    )

    results = fc.run(_ctx(_stoppedish_radar(), out=lines.append), sections)

    assert [(r.name, r.status) for r in results] == [
        ("a/one", "PASS"),
        ("a/two", "FAIL"),
        ("b/one", "PASS"),
    ]
    assert "  PASS  a/one" in lines
    assert "  FAIL  a/two: boom" in lines
    assert "a: 1 pass, 1 fail, 0 skip" in lines
    assert fc.exit_code(results) == 1


def test_run_only_keeps_catalogue_order_and_rejects_unknown_section_names():
    sections = (_section("a", "any", _check("a/one")), _section("b", "any", _check("b/one")))

    results = fc.run(_ctx(_stoppedish_radar()), sections, only=("b", "a"))
    assert [r.name for r in results] == ["a/one", "b/one"]

    with pytest.raises(ValueError, match="unknown section: zzz"):
        fc.select_sections(sections, ("zzz",))


def test_swing_checks_skip_without_the_flag():
    sections = (_section("t", "any", _check("t/swing", needs_swing=True)),)

    without = fc.run(_ctx(_stoppedish_radar()), sections)
    with_flag = fc.run(_ctx(_stoppedish_radar()), sections, swing=True)

    assert (without[0].status, without[0].detail) == ("SKIP", "needs --swing")
    assert with_flag[0].status == "PASS"
    assert fc.exit_code(without) == 0


def test_unsupported_command_becomes_skip_and_other_exceptions_become_fail():
    sections = (
        _section(
            "a",
            "any",
            _check("a/old", exc=UnsupportedCommand("'l3track' is not recognized")),
            _check("a/broken", exc=RuntimeError("wedged")),
            _check("a/after"),
        ),
    )

    results = fc.run(_ctx(_stoppedish_radar()), sections)

    assert results[0].status == "SKIP" and "older firmware" in results[0].detail
    assert results[1].status == "FAIL" and "wedged" in results[1].detail
    assert results[2].status == "PASS"


def test_fail_fast_stops_after_the_first_fail():
    sections = (_section("a", "any", _check("a/bad", fc.failed("a/bad")), _check("a/never")),)

    results = fc.run(_ctx(_stoppedish_radar()), sections, fail_fast=True)

    assert [r.name for r in results] == ["a/bad"]


def test_sections_reconcile_sensor_state_before_running(monkeypatch):
    calls: list[str] = []
    radar = _stoppedish_radar()
    monkeypatch.setattr(radar, "send_config", lambda cfg: calls.append("start"))
    sections = (_section("needs-active", "active", _check("needs-active/x")),)

    fc.run(_ctx(radar), sections)

    assert calls == ["start"]


def test_reconciliation_failure_fails_every_check_in_the_section(monkeypatch):
    radar = _stoppedish_radar()

    def explode(_cfg):
        raise RuntimeError("did not enter active capture mode")

    monkeypatch.setattr(radar, "send_config", explode)
    sections = (_section("s", "active", _check("s/one"), _check("s/two")),)

    results = fc.run(_ctx(radar), sections)

    assert [r.status for r in results] == ["FAIL", "FAIL"]
    assert "did not enter active" in results[1].detail


def test_cleanup_runs_after_a_raising_check(monkeypatch):
    radar = _stoppedish_radar()
    stopped: list[bool] = []
    monkeypatch.setattr(radar, "stop_sensor", lambda: stopped.append(True))

    results = fc.cleanup(_ctx(radar))

    assert radar.ser.written[:2] == ["triggerCfg 0 0 0", "debugCfg 0"]
    assert stopped == [True]
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_cleanup_failure_forces_exit_1(monkeypatch):
    radar = _stoppedish_radar()

    def explode():
        raise RuntimeError("remained active")

    monkeypatch.setattr(radar, "stop_sensor", explode)

    results = fc.cleanup(_ctx(radar))

    assert results[-1] == fc.CheckResult(
        "cleanup/sensorStop", "FAIL", "remained active", results[-1].seconds
    )
    assert fc.exit_code(results) == 1


def test_write_json_records_name_status_detail_seconds(tmp_path):
    path = tmp_path / "out.json"

    fc.write_json([fc.passed("a/one", "ok"), fc.skipped("b/two", "why")], path)

    assert json.loads(path.read_text()) == [
        {"name": "a/one", "status": "PASS", "detail": "ok", "seconds": 0.0},
        {"name": "b/two", "status": "SKIP", "detail": "why", "seconds": 0.0},
    ]


def _stats_counting(active=1, start=1000, step=500, phase="off", enabled=0, latched=0):
    def reply(n):
        return (
            f"frames={start + n * step} wraps=0 active={active} calib=0x0 rf_faults=0 "
            "hwa_frames=1 hwa_out=1 hwa_rearms=1 hwa_rearm_err=0 hwa_missed=0 "
            "freeze_req=0 freeze_done=0 freeze_to=0 format=iq16 plan=16pre/8post "
            "loops=12 used=100/200\n"
            "iq8_packed=0 iq8_overrun=0 iq8_clipped=0 pending=0 pre_seen=999 post_kept=0 "
            "post_seen=0 stride=1\n"
            f"trig phase={phase} tee=0 latched={latched} enabled={enabled}\n"
            "detect dropped=0 stale=0\nrearm_last_us=1 rearm_max_us=2 rearm_timed=3\nDone\n"
        ).encode()

    return reply


def _lifecycle_radar(**overrides):
    replies = {
        "stats": _stats_counting(),
        "captureCfg": b"Error: stop the sensor before captureCfg\n",
        "phaseCaptureCfg": b"Error: stop the sensor before phaseCaptureCfg\n",
        "captureFormat": b"Error: stop the sensor before captureFormat\n",
        "iq8Scale": b"Error: stop the sensor before iq8Scale\n",
        "sensorStop": b"Done\n",
    }
    replies.update(overrides)
    return scripted_radar(replies)


def _names(section):
    return [check.name for check in section.checks]


def test_lifecycle_section_names_match_the_spec():
    assert _names(fc.lifecycle_section()) == [
        "lifecycle/config accepted",
        "lifecycle/frames advance",
        "lifecycle/config commands refused while active",
        "lifecycle/sensorStop idles the sensor",
        "lifecycle/restart resets counters",
    ]


def test_lifecycle_config_accepted_reads_active_and_faults(monkeypatch):
    radar = _lifecycle_radar()
    monkeypatch.setattr(radar, "send_config", lambda cfg: None)
    check = fc.lifecycle_section().checks[0]

    assert check.run(_ctx(radar)).status == "PASS"

    faulty = _lifecycle_radar(stats=lambda n: b"frames=1 active=1 rf_faults=3\nDone\n")
    monkeypatch.setattr(faulty, "send_config", lambda cfg: None)
    result = check.run(_ctx(faulty))
    assert result.status == "FAIL" and "rf_faults=3" in result.detail


def test_lifecycle_frames_advance_fails_when_the_counter_is_stuck():
    moving = fc.lifecycle_section().checks[1].run(_ctx(_lifecycle_radar()))
    stuck = (
        fc.lifecycle_section().checks[1].run(_ctx(_lifecycle_radar(stats=_stats_counting(step=0))))
    )

    assert moving.status == "PASS"
    assert stuck.status == "FAIL"


def test_lifecycle_config_commands_must_be_refused_while_active():
    check = fc.lifecycle_section().checks[2]

    assert check.run(_ctx(_lifecycle_radar())).status == "PASS"

    leaky = _lifecycle_radar(captureFormat=b"Capture format: iq16\nDone\n")
    result = check.run(_ctx(leaky))
    assert result.status == "FAIL" and "captureFormat" in result.detail


def test_lifecycle_sensor_stop_requires_active_zero(monkeypatch):
    check = fc.lifecycle_section().checks[3]
    radar = _lifecycle_radar(stats=_stats_counting(active=0))
    monkeypatch.setattr(radar, "stop_sensor", lambda: None)
    assert check.run(_ctx(radar)).status == "PASS"

    still = _lifecycle_radar()

    def refuse():
        raise RuntimeError("IWR6843 remained active after sensorStop")

    monkeypatch.setattr(still, "stop_sensor", refuse)
    result = check.run(_ctx(still))
    assert result.status == "FAIL" and "remained active" in result.detail


def test_lifecycle_restart_resets_counters(monkeypatch):
    check = fc.lifecycle_section().checks[4]
    # First stats: high frame count from the old session; after send_config the count restarts low.
    radar = _lifecycle_radar(stats=lambda n: _stats_counting(start=50000 if n == 0 else 10)(0))
    monkeypatch.setattr(radar, "send_config", lambda cfg: None)
    assert check.run(_ctx(radar)).status == "PASS"

    same = _lifecycle_radar(stats=_stats_counting(start=50000, step=100))
    monkeypatch.setattr(same, "send_config", lambda cfg: None)
    assert check.run(_ctx(same)).status == "FAIL"


def _validating(prefix, ok_reply):
    """Reply Error for lines the firmware would refuse, ok_reply otherwise (table-driven)."""
    table = {
        "captureCfg": fc.CAPTURE_CFG_CASES,
        "phaseCaptureCfg": fc.PHASE_CAPTURE_CFG_CASES,
        "captureFormat": fc.CAPTURE_FORMAT_CASES,
        "iq8Scale": fc.IQ8_SCALE_CASES,
    }[prefix]
    expected = dict(table)

    class Port(ScriptedSerial):
        def write(self, data):
            line = data.decode().strip()
            self.written.append(line)
            if line.startswith(prefix):
                good = expected.get(line, False)
                self.inject(ok_reply(line) if good else f"Error: {prefix} rejected\n".encode())
            else:
                self.inject(b"Done\n")

    from openflight.iwr6843.driver import IWR6843Radar

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = Port({})
    radar.port = "scripted"
    radar._trigger_pending = b""
    return radar


def test_profiles_section_names_match_the_spec():
    section = fc.profiles_section(("config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",))

    assert _names(section) == [
        "profiles/captureCfg validation",
        "profiles/phaseCaptureCfg validation",
        "profiles/captureFormat",
        "profiles/iq8Scale",
        "profiles/profile iwr6843_l3dump_wide_24f3ms_53bin_iq16 loads",
    ]
    assert section.sensor == "stopped"


def test_capture_cfg_cases_cover_the_spec_table():
    lines = dict(fc.CAPTURE_CFG_CASES)
    assert lines["captureCfg 20 53 32 53 47 8"] is True
    assert lines["captureCfg 20 53 32 53 47 8 2"] is True
    assert lines["captureCfg 20 53 32 53 47"] is False  # wrong count
    assert lines["captureCfg x 53 32 53 47 8"] is False  # non-integer
    assert lines["captureCfg 300 53 32 53 47 8"] is False  # above 255
    assert lines["captureCfg 20 0 32 53 47 8"] is False  # zero pre bins
    assert lines["captureCfg 100 53 32 53 47 8"] is False  # window past 128
    assert lines["captureCfg 20 53 32 53 47 64"] is False  # post frames at the cap


def test_capture_cfg_validation_passes_and_fails_by_table():
    check = fc.profiles_section(()).checks[0]

    good = check.run(_ctx(_validating("captureCfg", lambda _l: b"Done\n")))
    assert good.status == "PASS"

    lax = scripted_radar({"captureCfg": b"Done\n"})  # accepts every line, even the bad ones
    result = check.run(_ctx(lax))
    assert result.status == "FAIL" and "accepted" in result.detail


def test_capture_format_and_iq8_scale_echo_their_values():
    fmt = fc.profiles_section(()).checks[2]
    scale = fc.profiles_section(()).checks[3]

    fmt_radar = _validating(
        "captureFormat", lambda line: f"Capture format: {line.split()[1]}\nDone\n".encode()
    )
    scale_radar = _validating(
        "iq8Scale",
        lambda line: f"IQ8 fixed scale: {line.split()[1]} (HWA shift 6)\nDone\n".encode(),
    )

    assert fmt.run(_ctx(fmt_radar)).status == "PASS"
    assert scale.run(_ctx(scale_radar)).status == "PASS"

    silent = _validating("captureFormat", lambda _l: b"Done\n")
    result = fmt.run(_ctx(silent))
    assert result.status == "FAIL" and "echo" in result.detail


def test_expected_profile_shape_reads_the_cfg(tmp_path):
    cfg = tmp_path / "p.cfg"
    cfg.write_text(
        "captureFormat iq8\niq8Scale 128\nphaseCaptureCfg 20 53 8 32 53 10 47 53 64 27 1\nsensorStart\n"
    )
    plain = tmp_path / "q.cfg"
    plain.write_text("captureFormat iq16\ncaptureCfg 20 53 32 53 47 8\nsensorStart\n")

    assert fc.expected_profile_shape(cfg) == ("iq8", 1)
    assert fc.expected_profile_shape(plain) == ("iq16", None)


def test_profile_load_check_compares_format_stride_and_capacity(tmp_path, monkeypatch):
    cfg = tmp_path / "wide.cfg"
    cfg.write_text(
        "captureFormat iq16\nphaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1\nsensorStart\n"
    )
    check = fc.profiles_section((str(cfg),)).checks[-1]

    def radar_with(fmt, stride, used, cap):
        radar = scripted_radar(
            {
                "stats": f"frames=9 active=1 format={fmt} plan=16pre/8post used={used}/{cap}\nstride={stride}\nDone\n".encode()
            }
        )
        monkeypatch.setattr(radar, "send_config", lambda p: None)
        monkeypatch.setattr(radar, "stop_sensor", lambda: None)
        return radar

    assert check.run(_ctx(radar_with("iq16", 1, 100, 200))).status == "PASS"
    assert check.run(_ctx(radar_with("iq8", 1, 100, 200))).status == "FAIL"
    assert check.run(_ctx(radar_with("iq16", 2, 100, 200))).status == "FAIL"
    assert check.run(_ctx(radar_with("iq16", 1, 300, 200))).status == "FAIL"


WIDE_CFG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"


def _cube(frames=4, loops=2, n_tx=3, n_rx=4, bins=6):
    rng = np.random.default_rng(1)
    shape = (frames, loops * n_tx, n_rx, bins)
    return rng.normal(size=shape) + 1j * rng.normal(size=shape)


def _summary(cube, n_tx=3):
    return vertical_loop_power(cube, n_tx=n_tx)


def _dump_bytes(cube, n_tx=3):
    return pack_dump(cube, n_tx=n_tx, version=3)


def _stats_for(cube, n_tx=3, freeze=(1, 1), fmt="iq16"):
    frames, chirps = cube.shape[0], cube.shape[1]

    def reply(_n):
        return (
            f"frames=100 active=1 rf_faults=0 freeze_req={freeze[0]} freeze_done={freeze[1]} "
            f"format={fmt} plan={frames - 1}pre/1post loops={chirps // n_tx} used=1/2\n"
            "stride=1\ntrig phase=off tee=0 latched=0 enabled=0\nDone\n"
        ).encode()

    return reply


def test_readback_section_names_match_the_spec():
    assert _names(fc.readback_section()) == [
        "readback/l3dump streams a valid dump",
        "readback/l3sparse returns every cell at the limit",
        "readback/l3sparse refuses an oversized request",
        "readback/l3sparse refuses a late request, then works",
        "readback/l3track without trackCfg is refused",
        "readback/trackCfg validation",
        "readback/l3track streams the tracked cells",
    ]


def test_l3dump_check_validates_the_header_against_the_plan():
    cube = _cube()
    good = scripted_radar({"l3dump": _dump_bytes(cube) + b"Done\n", "stats": _stats_for(cube)})
    check = fc.readback_section().checks[0]

    assert check.run(_ctx(good)).status == "PASS"

    wrong_plan = scripted_radar(
        {"l3dump": _dump_bytes(cube) + b"Done\n", "stats": _stats_for(_cube(frames=9))}
    )
    result = check.run(_ctx(wrong_plan))
    assert result.status == "FAIL" and "n_frames" in result.detail


def _sparse_radar(cube, *, after_request=None, trailer=b"Done\n", late_first=False):
    """Plays l3sparse exchanges: ILP1 power, then the cells the host asks for.

    ``after_request`` replaces the ILS1 reply (to script a refusal).
    ``late_first`` makes the first l3sparse time out with the firmware's
    "request missing" error instead of waiting for cells.
    """
    summary = _summary(cube)
    stats = _stats_for(cube)
    state = {"sparse": 0}

    class Port(ScriptedSerial):
        def write(self, data):
            line = data.decode(errors="replace").strip()
            self.written.append(line)
            if line == "l3sparse":
                state["sparse"] += 1
                self.inject(b"l3sparse\n" + power_packet(summary))
                if late_first and state["sparse"] == 1:
                    self.inject(b"Error: sparse cell request missing\nDone\n")
            elif line.startswith("cells"):
                if after_request is not None:
                    self.inject(after_request)
                else:
                    self.inject(slice_packet(cube, 3, parse_cell_request(data)) + trailer)
            elif line == "stats":
                self.inject(stats(0))
            else:
                self.inject(b"Done\n")

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = Port({})
    radar.port = "scripted"
    radar._trigger_pending = b""
    return radar


def test_l3sparse_limit_check_requires_every_cell_and_a_noise_floor():
    check = fc.readback_section().checks[1]

    assert check.run(_ctx(_sparse_radar(_cube()))).status == "PASS"

    empty = _sparse_radar(_cube(), after_request=b"ILS1\x00\x00Done\n")
    assert fc.run_check(_ctx(empty), check).status == "FAIL"  # driver may raise on missing cells


def test_l3sparse_oversized_check_expects_the_firmware_refusal():
    check = fc.readback_section().checks[2]
    refusing = _sparse_radar(
        _cube(frames=8, bins=40),
        after_request=b"Error: sparse cell request longer than L3_SPARSE_REQUEST_MAX\nDone\n",
    )

    assert check.run(_ctx(refusing)).status == "PASS"

    lenient = _sparse_radar(_cube(frames=8, bins=40), after_request=b"Done\n")
    assert check.run(_ctx(lenient)).status == "FAIL"


def test_l3sparse_late_check_waits_out_the_timeout_then_reads_again():
    slept: list[float] = []
    radar = _sparse_radar(_cube(), late_first=True)
    check = fc.readback_section().checks[3]

    result = check.run(_ctx(radar, sleep=slept.append))

    assert result.status == "PASS", result.detail
    assert any(s >= fc.SPARSE_REQUEST_TIMEOUT_S for s in slept)
    assert radar.ser.written.count("l3sparse") == 2


def test_l3track_without_trackcfg_must_be_refused():
    cube = _cube()
    check = fc.readback_section().checks[4]
    refusing = scripted_radar(
        {"l3track": b"Error: l3track needs trackCfg\n", "stats": _stats_for(cube)}
    )
    assert check.run(_ctx(refusing)).status == "PASS"

    # Wrong error text AND freeze_req climbing on every stats call: the ring froze.
    freezing = scripted_radar(
        {
            "l3track": b"Error: something else\n",
            "stats": lambda n: _stats_for(cube, freeze=(1 + n, 1 + n))(0),
        }
    )
    result = check.run(_ctx(freezing))
    assert result.status == "FAIL" and "freeze_req moved" in result.detail


def test_track_cfg_cases_and_command_builder():
    lines = dict(fc.TRACK_CFG_CASES)
    assert lines["trackCfg 9e-05 0.046875 0 0 0"] is True
    assert lines["trackCfg 9e-05 0.046875 0 0"] is False
    assert lines["trackCfg -1 0.046875 0 0 0"] is False
    assert lines["trackCfg 0 0.046875 0 0 0"] is False
    assert lines["trackCfg 9e-05 0 0 0 0"] is False

    command = fc.track_config_command(WIDE_CFG)
    assert command.startswith("trackCfg ")
    fields = command.split()[1:]
    assert len(fields) == 5 and float(fields[1]) == 6.0 / 128 and fields[2:] == ["0", "0", "0"]


def test_l3track_streams_and_rearms():
    cube = _cube()
    summary = _summary(cube)
    track = OnboardTrack(True, 3, 1.0, 2.0, 0.1, 0.0, 0.01)
    packet = (
        summary.header_bytes(b"ILT1")
        + track.to_bytes()
        + slice_packet(cube, 3, [(0, 1), (1, 2)])
        + b"Done\n"
    )
    radar = scripted_radar(
        {
            "trackCfg": b"Done\n",
            "l3track": packet,
            "stats": lambda n: _stats_for(cube, freeze=(1 + (n > 0), 1 + (n > 0)))(0),
        }
    )
    check = fc.readback_section().checks[6]

    result = check.run(_ctx(radar))

    assert result.status == "PASS", result.detail
    assert "found=True" in result.detail

    iq8 = scripted_radar(
        {
            "trackCfg": b"Done\n",
            "l3track": b"Error: l3track needs IQ16\n",
            "stats": _stats_for(cube, fmt="iq8"),
        }
    )
    assert check.run(_ctx(iq8)).status == "SKIP"


def _trigger_radar(
    *,
    phases=("tee-low",),
    enabled_after_arm=1,
    latched=0,
    pre_seen=999,
    plan_pre=16,
    debug_lines=None,
):
    """A radar whose trig state follows the last triggerCfg it was sent."""
    state = {"enabled": 0, "phase": "off", "bin": 0, "level": 0, "n": 0}

    def stats(_n):
        phase = state["phase"] if state["enabled"] else "off"
        text = (
            f"frames={1000 + state['n'] * 10} active=1 rf_faults=0 freeze_req=0 freeze_done=0 "
            f"format=iq16 plan={plan_pre}pre/8post loops=12 used=1/2\npre_seen={pre_seen} stride=1\n"
            f"trig phase={phase} tee={412 if state['enabled'] else 0} latched={latched} enabled={state['enabled']}\nDone\n"
        )
        state["n"] += 1
        return text.encode()

    def trigger_cfg(line):
        fields = line.split()
        if (
            len(fields) != 4
            or not fields[1].isdigit()
            or not fields[3].isdigit()
            or fields[2].startswith("-")
        ):
            return b"Error: triggerCfg <localBin> <power> <hits>\n"
        hits = int(fields[3])
        state["enabled"] = enabled_after_arm if hits else 0
        state["phase"] = phases[0]
        state["bin"], state["level"] = int(fields[1]), int(float(fields[2]))
        return b"Done\n"

    def debug_cfg(line):
        if line.split()[1] not in ("0", "1"):
            return b"Error: debugCfg <0|1>\n"
        if line.endswith("1"):
            lines = debug_lines or [
                f"trig phase={state['phase']} tee=412 approach=0 ready=1 toward=0 away=0 "
                f"run=0 peak=0 have=0 bin={state['bin']} level={state['level']} latched=0\n"
            ]
            return "".join(lines).encode() + b"Done\n"
        return b"Done\n"

    class Port(ScriptedSerial):
        def write(self, data):
            line = data.decode(errors="replace").strip()
            self.written.append(line)
            if line.startswith("triggerCfg"):
                self.inject(trigger_cfg(line))
            elif line.startswith("debugCfg"):
                self.inject(debug_cfg(line))
            elif line == "stats":
                self.inject(stats(0))
            else:
                self.inject(b"Done\n")

    from openflight.iwr6843.driver import IWR6843Radar

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = Port({})
    radar.port = "scripted"
    radar._trigger_pending = b""
    radar.send_config = lambda cfg: state.update(enabled=0, phase="off")  # type: ignore[method-assign]
    return radar


def test_trigger_section_names_match_the_spec():
    assert _names(fc.trigger_section()) == [
        "trigger/fresh session untriggered",
        "trigger/triggerCfg validation",
        "trigger/arming starts the detector",
        "trigger/triggerCfg 0 0 0 disarms",
        "trigger/debugCfg streams parsable lines",
        "trigger/debug lines only change on phase change",
        "trigger/floor measurement",
        "trigger/reconfigure clears a previous arm",
    ]


def test_fresh_session_must_report_off_and_unlatched():
    check = fc.trigger_section().checks[0]

    assert check.run(_ctx(_trigger_radar())).status == "PASS"

    stale = _trigger_radar(latched=1)
    stale.send_config = lambda cfg: None  # a firmware that forgets to clear the latch
    result = check.run(_ctx(stale))
    assert result.status == "FAIL" and "latched=1" in result.detail


def test_trigger_cfg_validation_table():
    lines = dict(fc.TRIGGER_CFG_CASES)
    assert lines["triggerCfg 10 1000 2"] is True
    assert lines["triggerCfg 10 1000"] is False
    assert lines["triggerCfg x 1000 2"] is False
    assert lines["triggerCfg 10 -5 2"] is False
    assert lines["triggerCfg 10 1000 y"] is False

    assert fc.trigger_section().checks[1].run(_ctx(_trigger_radar())).status == "PASS"


def test_arming_needs_enabled_live_phase_full_ring_and_tee_power():
    check = fc.trigger_section().checks[2]

    good = check.run(_ctx(_trigger_radar()))
    assert good.status == "PASS", good.detail
    assert "tee=412" in good.detail

    not_enabled = check.run(_ctx(_trigger_radar(enabled_after_arm=0)))
    assert not_enabled.status == "FAIL" and "enabled=0" in not_enabled.detail

    short_ring = check.run(_ctx(_trigger_radar(pre_seen=3, plan_pre=16)))
    assert short_ring.status == "FAIL" and "pre_seen" in short_ring.detail

    stuck = check.run(_ctx(_trigger_radar(phases=("no-frame",))))
    assert stuck.status == "FAIL" and "no-frame" in stuck.detail


def test_disarm_returns_to_off():
    check = fc.trigger_section().checks[3]
    radar = _trigger_radar()

    assert check.run(_ctx(radar)).status == "PASS"
    assert radar.ser.written[0] == "triggerCfg 0 0 0"


def test_debug_cfg_lines_parse_and_echo_the_armed_values():
    check = fc.trigger_section().checks[4]

    good = check.run(_ctx(_trigger_radar()))
    assert good.status == "PASS", good.detail

    missing_field = _trigger_radar(debug_lines=["trig phase=tee-low tee=1 latched=0\n"])
    result = check.run(_ctx(missing_field))
    assert result.status == "FAIL" and "fields" in result.detail


def test_debug_stream_must_not_repeat_the_same_phase():
    check = fc.trigger_section().checks[5]

    quiet = _trigger_radar()
    assert check.run(_ctx(quiet)).status == "PASS"

    line = "trig phase=tee-low tee=1 approach=0 ready=1 toward=0 away=0 run=0 peak=0 have=0 bin=14 level=5 latched=0\n"
    chatty = _trigger_radar(debug_lines=[line] * 3)  # same phase written three times
    result = check.run(_ctx(chatty))
    assert result.status == "FAIL" and "repeated" in result.detail


def test_floor_measurement_uses_the_runtime_helper(monkeypatch):
    check = fc.trigger_section().checks[6]
    monkeypatch.setattr(
        fc, "measure_trigger_level", lambda radar, local_bin, hits, clock, pause: (300.0, 450.0)
    )
    assert check.run(_ctx(_trigger_radar())).status == "PASS"

    def latched(*_a, **_k):
        raise RuntimeError("background sample latched the trigger")

    monkeypatch.setattr(fc, "measure_trigger_level", latched)
    result = check.run(_ctx(_trigger_radar()))
    assert result.status == "FAIL" and "latched" in result.detail


def test_reconfigure_must_clear_a_previous_arm():
    check = fc.trigger_section().checks[7]

    assert check.run(_ctx(_trigger_radar())).status == "PASS"

    sticky = _trigger_radar()
    sticky.send_config = lambda cfg: None
    result = check.run(_ctx(sticky))
    assert result.status == "FAIL" and "enabled=1" in result.detail
