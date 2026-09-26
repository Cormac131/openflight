"""Unit tests for the IWR6843 firmware CLI check suite (no hardware)."""

from __future__ import annotations

import json

import pytest

from openflight.iwr6843 import firmware_checks as fc
from openflight.iwr6843.driver import UnsupportedCommand
from tests.iwr6843_fakes import ScriptedSerial, scripted_radar

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
