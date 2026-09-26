"""Hardware checks for the IWR6843 L3-dump firmware CLI.

Everything the hardware-test script ``scripts/hardware-test/test_iwr_firmware.py``
decides lives here so it can be unit-tested against a scripted serial port.
The checks talk to the firmware through ``IWR6843Radar`` only.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from openflight.iwr6843.driver import IWR6843Radar, UnsupportedCommand
from openflight.iwr6843.dump import parse_header
from openflight.iwr6843.monitor import read_capture_config
from openflight.iwr6843.sparse import (
    POWER_MAGIC,
    RANGE_FFT_SIZE,
    SPARSE_REQUEST_MAX_BYTES,
    PowerSummary,
    SparsePlan,
    fit_cell_request,
    format_cell_request,
    parse_power,
    power_packet_size,
)
from openflight.iwr6843.tracking import LOOP_PRI_S, RANGE_SPAN_M

_INT_FIELD = re.compile(r"(\w+)=(\d+)")
_USED = re.compile(r"used=(\d+)/(\d+)")
_PLAN = re.compile(r"plan=(\d+)pre/(\d+)post")
_FORMAT = re.compile(r"format=(iq8|iq16)")
_DETECT = re.compile(r"detect dropped=(\d+) stale=(\d+)")


def parse_stats(text: str) -> dict[str, int]:
    """Every ``key=<digits>`` pair of a firmware ``stats`` response.

    Compound fields (``format=iq8``, ``plan=24pre/0post``, ``used=100/200``,
    ``calib=0x0``) partially match on their leading digits; ``parse_snapshot``
    reads those with their own patterns.
    """
    return {key: int(value) for key, value in _INT_FIELD.findall(text)}


def parse_trig(line: str) -> dict[str, str] | None:
    """Fields from a ``trig phase=...`` debug or stats line."""
    text = line.strip()
    marker = text.find("trig ")
    if marker < 0 or "phase=" not in text[marker:]:
        return None
    fields: dict[str, str] = {}
    for token in text[marker + len("trig ") :].split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value
    if "phase" not in fields or "tee" not in fields:
        return None
    return fields


@dataclass(frozen=True)
class StatsSnapshot:
    """Typed view of one ``stats`` reply; ``None`` where the image omits a field."""

    raw: str
    active: int | None = None
    frames: int | None = None
    pre_seen: int | None = None
    plan_pre: int | None = None
    plan_post: int | None = None
    freeze_req: int | None = None
    freeze_done: int | None = None
    format: str | None = None
    stride: int | None = None
    used: int | None = None
    capacity: int | None = None
    rearm_last_us: int | None = None
    rearm_max_us: int | None = None
    rearm_timed: int | None = None
    detect_dropped: int | None = None
    detect_stale: int | None = None
    phase: str | None = None
    tee: int | None = None
    latched: int | None = None
    enabled: int | None = None


def _trig_int(fields: dict[str, str] | None, key: str) -> int | None:
    if not fields or key not in fields:
        return None
    try:
        return int(float(fields[key]))
    except ValueError:
        return None


def parse_snapshot(text: str) -> StatsSnapshot:
    """Parse a whole ``stats`` reply into a ``StatsSnapshot``."""
    ints = parse_stats(text)
    used = _USED.search(text)
    plan = _PLAN.search(text)
    fmt = _FORMAT.search(text)
    detect = _DETECT.search(text)
    trig = None
    for line in text.splitlines():
        parsed = parse_trig(line)
        if parsed is not None:
            trig = parsed
    return StatsSnapshot(
        raw=text,
        active=ints.get("active"),
        frames=ints.get("frames"),
        pre_seen=ints.get("pre_seen"),
        plan_pre=int(plan.group(1)) if plan else None,
        plan_post=int(plan.group(2)) if plan else None,
        freeze_req=ints.get("freeze_req"),
        freeze_done=ints.get("freeze_done"),
        format=fmt.group(1) if fmt else None,
        stride=ints.get("stride"),
        used=int(used.group(1)) if used else None,
        capacity=int(used.group(2)) if used else None,
        rearm_last_us=ints.get("rearm_last_us"),
        rearm_max_us=ints.get("rearm_max_us"),
        rearm_timed=ints.get("rearm_timed"),
        detect_dropped=int(detect.group(1)) if detect else None,
        detect_stale=int(detect.group(2)) if detect else None,
        phase=trig.get("phase") if trig else None,
        tee=_trig_int(trig, "tee"),
        latched=_trig_int(trig, "latched"),
        enabled=_trig_int(trig, "enabled"),
    )


PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class CheckResult:
    """One printed line of the suite."""

    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Context:
    """Everything a check needs; callables are injected so tests never wait."""

    radar: IWR6843Radar
    config: str
    tee_m: float
    level: float | None
    hits: int
    wait_s: float
    shots: int
    profiles: tuple[str, ...]
    prompt: Callable[[str], None]
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    out: Callable[[str], None]


@dataclass(frozen=True)
class Check:
    """A named check; ``needs_swing`` ones run only with ``--swing``."""

    name: str
    run: Callable[[Context], CheckResult]
    needs_swing: bool = False


@dataclass(frozen=True)
class Section:
    """Checks that share a required sensor state: active, stopped, or any."""

    name: str
    sensor: str
    checks: tuple[Check, ...] = field(default_factory=tuple)


def passed(name: str, detail: str = "") -> CheckResult:
    """A PASS line."""
    return CheckResult(name, PASS, detail)


def failed(name: str, detail: str = "") -> CheckResult:
    """A FAIL line."""
    return CheckResult(name, FAIL, detail)


def skipped(name: str, detail: str = "") -> CheckResult:
    """A SKIP line; never fails the run."""
    return CheckResult(name, SKIP, detail)


def stats_snapshot(ctx: Context) -> StatsSnapshot:
    """One ``stats`` round trip."""
    return parse_snapshot(ctx.radar.stats())


def wait_until(
    ctx: Context, predicate: Callable[[], bool], timeout_s: float, poll_s: float = 0.1
) -> bool:
    """Poll ``predicate`` until it is true or ``timeout_s`` passes."""
    deadline = ctx.clock() + timeout_s
    while True:
        if predicate():
            return True
        if ctx.clock() >= deadline:
            return False
        ctx.sleep(poll_s)


def read_port_text(ctx: Context, seconds: float) -> str:
    """Raw CLI text that arrives within ``seconds`` (no command is sent)."""
    deadline = ctx.clock() + seconds
    collected = bytearray()
    while ctx.clock() < deadline:
        waiting = ctx.radar.ser.in_waiting
        chunk = ctx.radar.ser.read(waiting if waiting else 1)
        if chunk:
            collected.extend(chunk)
        else:
            ctx.sleep(0.02)
    return collected.decode(errors="replace")


def ensure_sensor(ctx: Context, state: str) -> None:
    """Bring the sensor to ``state`` ("active", "stopped", "any") if it is not there."""
    if state == "any":
        return
    active = stats_snapshot(ctx).active
    if state == "active" and active != 1:
        ctx.radar.send_config(ctx.config)
    elif state == "stopped" and active != 0:
        ctx.radar.stop_sensor()


def format_result(result: CheckResult) -> str:
    """``  PASS  name: detail`` — the line the operator reads."""
    suffix = f": {result.detail}" if result.detail else ""
    return f"  {result.status}  {result.name}{suffix}"


def select_sections(
    sections: tuple[Section, ...], only: tuple[str, ...] | None
) -> tuple[Section, ...]:
    """Sections named in ``only`` in catalogue order; all of them when ``only`` is None."""
    if only is None:
        return sections
    known = {section.name for section in sections}
    for name in only:
        if name not in known:
            raise ValueError(f"unknown section: {name} (choose from {', '.join(sorted(known))})")
    wanted = set(only)
    return tuple(section for section in sections if section.name in wanted)


def run_check(ctx: Context, check: Check) -> CheckResult:
    """Run one check under the suite's guard: exceptions become FAIL, unsupported commands SKIP."""
    started = ctx.clock()
    try:
        result = check.run(ctx)
    except UnsupportedCommand as exc:
        result = skipped(check.name, f"older firmware: {exc}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result = failed(check.name, f"{type(exc).__name__}: {exc}")
    return CheckResult(result.name, result.status, result.detail, ctx.clock() - started)


def _emit(ctx: Context, results: list[CheckResult], result: CheckResult) -> None:
    results.append(result)
    ctx.out(format_result(result))


def _summarise(ctx: Context, section: Section, results: list[CheckResult]) -> None:
    names = {check.name for check in section.checks}
    mine = [r for r in results if r.name in names]
    counts = {status: sum(1 for r in mine if r.status == status) for status in (PASS, FAIL, SKIP)}
    ctx.out(f"{section.name}: {counts[PASS]} pass, {counts[FAIL]} fail, {counts[SKIP]} skip")


def run(
    ctx: Context,
    sections: tuple[Section, ...],
    *,
    only: tuple[str, ...] | None = None,
    swing: bool = False,
    fail_fast: bool = False,
) -> list[CheckResult]:
    """Run the selected sections in catalogue order and return every result."""
    results: list[CheckResult] = []
    for section in select_sections(sections, only):
        try:
            ensure_sensor(ctx, section.sensor)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            for check in section.checks:
                _emit(ctx, results, failed(check.name, f"sensor not {section.sensor}: {exc}"))
            _summarise(ctx, section, results)
            if fail_fast:
                return results
            continue
        for check in section.checks:
            if check.needs_swing and not swing:
                _emit(ctx, results, skipped(check.name, "needs --swing"))
                continue
            _emit(ctx, results, run_check(ctx, check))
            if fail_fast and results[-1].status == FAIL:
                _summarise(ctx, section, results)
                return results
        _summarise(ctx, section, results)
    return results


def cleanup(ctx: Context) -> list[CheckResult]:
    """Leave the radar disarmed, quiet and stopped; report each step, never raise."""
    steps: tuple[tuple[str, Callable[[], None]], ...] = (
        ("cleanup/triggerCfg off", lambda: ctx.radar.cmd("triggerCfg 0 0 0", 2.0)),
        ("cleanup/debugCfg off", lambda: ctx.radar.cmd("debugCfg 0", 2.0)),
        ("cleanup/sensorStop", ctx.radar.stop_sensor),
    )
    results: list[CheckResult] = []
    for name, step in steps:
        started = ctx.clock()
        try:
            step()
            result = passed(name)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            result = failed(name, str(exc))
        _emit(
            ctx,
            results,
            CheckResult(result.name, result.status, result.detail, ctx.clock() - started),
        )
    return results


def exit_code(results: list[CheckResult]) -> int:
    """1 when any check failed, else 0. SKIP never fails the run."""
    return 1 if any(r.status == FAIL for r in results) else 0


def write_json(results: list[CheckResult], path: str | Path) -> None:
    """Persist results as a list of {name, status, detail, seconds}."""
    Path(path).write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")


_CONFIG_WHILE_ACTIVE = (
    "captureCfg 20 53 32 53 47 8",
    "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1",
    "captureFormat iq16",
    "iq8Scale 64",
)


def _check_config_accepted(ctx: Context) -> CheckResult:
    name = "lifecycle/config accepted"
    ctx.radar.send_config(ctx.config)
    snap = stats_snapshot(ctx)
    faults = parse_stats(snap.raw).get("rf_faults")
    if snap.active != 1 or faults != 0:
        return failed(name, f"active={snap.active} rf_faults={faults}")
    return passed(
        name,
        f"active=1 rf_faults=0 format={snap.format} plan={snap.plan_pre}pre/{snap.plan_post}post",
    )


def _check_frames_advance(ctx: Context) -> CheckResult:
    name = "lifecycle/frames advance"
    before = stats_snapshot(ctx).frames
    ctx.sleep(0.5)
    after = stats_snapshot(ctx).frames
    if before is None or after is None or after <= before:
        return failed(name, f"frames {before} -> {after} over 0.5 s")
    return passed(name, f"frames {before} -> {after} over 0.5 s")


def _check_config_refused_while_active(ctx: Context) -> CheckResult:
    name = "lifecycle/config commands refused while active"
    leaked = []
    for line in _CONFIG_WHILE_ACTIVE:
        reply = ctx.radar.cmd(line, 2.0)
        if "Error" not in reply or "stop the sensor" not in reply:
            leaked.append(f"{line.split()[0]}: {reply.strip()[:60]!r}")
    if leaked:
        return failed(name, "; ".join(leaked))
    return passed(name, f"{len(_CONFIG_WHILE_ACTIVE)} commands refused")


def _check_sensor_stop(ctx: Context) -> CheckResult:
    name = "lifecycle/sensorStop idles the sensor"
    try:
        ctx.radar.stop_sensor()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return failed(name, str(exc))
    snap = stats_snapshot(ctx)
    if snap.active != 0:
        return failed(name, f"active={snap.active} after sensorStop")
    return passed(name, "active=0")


def _check_restart_resets(ctx: Context) -> CheckResult:
    name = "lifecycle/restart resets counters"
    before = stats_snapshot(ctx).frames
    ctx.radar.send_config(ctx.config)
    after = stats_snapshot(ctx)
    if after.active != 1 or before is None or after.frames is None or after.frames >= before:
        return failed(name, f"active={after.active} frames {before} -> {after.frames}")
    return passed(name, f"frames {before} -> {after.frames}, latched={after.latched}")


def lifecycle_section() -> Section:
    """sensorStart/sensorStop/stats behave; the section ends with the sensor active."""
    return Section(
        "lifecycle",
        "active",
        (
            Check("lifecycle/config accepted", _check_config_accepted),
            Check("lifecycle/frames advance", _check_frames_advance),
            Check(
                "lifecycle/config commands refused while active", _check_config_refused_while_active
            ),
            Check("lifecycle/sensorStop idles the sensor", _check_sensor_stop),
            Check("lifecycle/restart resets counters", _check_restart_resets),
        ),
    )


# (line, accepted?) — the firmware rules are in l3_cli_captureCfg / l3_cli_phaseCaptureCfg /
# l3_cli_captureFormat / l3_cli_iq8Scale (firmware/iwr6843/l3_dump.c). N_SAMPLES is 128,
# L3_MAX_CAPTURE_FRAMES is 64, L3_MAX_POST_STRIDE is 16.
CAPTURE_CFG_CASES: tuple[tuple[str, bool], ...] = (
    ("captureCfg 20 53 32 53 47 8", True),
    ("captureCfg 20 53 32 53 47 8 2", True),
    ("captureCfg 20 53 32 53 47", False),
    ("captureCfg x 53 32 53 47 8", False),
    ("captureCfg 300 53 32 53 47 8", False),
    ("captureCfg 20 0 32 53 47 8", False),
    ("captureCfg 100 53 32 53 47 8", False),
    ("captureCfg 20 53 32 53 47 64", False),
    ("captureCfg 20 53 32 53 47 8 17", False),
)
PHASE_CAPTURE_CFG_CASES: tuple[tuple[str, bool], ...] = (
    ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1", True),
    ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8", False),
    ("phaseCaptureCfg 20 53 x 32 53 7 47 53 47 8 1", False),
    ("phaseCaptureCfg 20 53 0 32 53 7 47 53 47 8 1", False),
    ("phaseCaptureCfg 20 53 9 32 53 30 47 53 47 34 1", False),
    ("phaseCaptureCfg 20 53 9 32 53 7 100 53 47 8 1", False),
    ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 17", False),
)
CAPTURE_FORMAT_CASES: tuple[tuple[str, bool], ...] = (
    ("captureFormat iq16", True),
    ("captureFormat iq8", True),
    ("captureFormat iq32", False),
    ("captureFormat", False),
)
IQ8_SCALE_CASES: tuple[tuple[str, bool], ...] = (
    ("iq8Scale 16", True),
    ("iq8Scale 32", True),
    ("iq8Scale 64", True),
    ("iq8Scale 128", True),
    ("iq8Scale 256", True),
    ("iq8Scale 8", False),
    ("iq8Scale 512", False),
    ("iq8Scale 48", False),
    ("iq8Scale abc", False),
)


def _run_validation_table(
    ctx: Context, name: str, cases: tuple[tuple[str, bool], ...], echo: str | None = None
) -> CheckResult:
    """Send each line; accepted ones must not Error (and must ``echo``), refused ones must."""
    wrong: list[str] = []
    for line, expect_ok in cases:
        reply = ctx.radar.cmd(line, 2.0)
        refused = "Error" in reply
        if expect_ok and refused:
            wrong.append(f"refused {line!r}")
        elif not expect_ok and not refused:
            wrong.append(f"accepted {line!r}")
        elif expect_ok and echo is not None and echo not in reply:
            wrong.append(f"no {echo!r} echo for {line!r}")
    if wrong:
        return failed(name, "; ".join(wrong))
    return passed(name, f"{len(cases)} lines behaved")


def _check_capture_cfg(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "profiles/captureCfg validation", CAPTURE_CFG_CASES)


def _check_phase_capture_cfg(ctx: Context) -> CheckResult:
    return _run_validation_table(
        ctx, "profiles/phaseCaptureCfg validation", PHASE_CAPTURE_CFG_CASES
    )


def _check_capture_format(ctx: Context) -> CheckResult:
    return _run_validation_table(
        ctx, "profiles/captureFormat", CAPTURE_FORMAT_CASES, echo="Capture format:"
    )


def _check_iq8_scale(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "profiles/iq8Scale", IQ8_SCALE_CASES, echo="IQ8 fixed scale:")


def expected_profile_shape(cfg_path: str | Path) -> tuple[str | None, int | None]:
    """``(format, stride)`` a .cfg declares; stride None for a 6-value captureCfg."""
    fmt: str | None = None
    stride: int | None = None
    for raw_line in Path(cfg_path).read_text(encoding="utf-8").splitlines():
        fields = raw_line.split()
        if not fields:
            continue
        if fields[0] == "captureFormat" and len(fields) > 1:
            fmt = fields[1]
        elif fields[0] == "phaseCaptureCfg" and len(fields) == 12:
            stride = int(fields[11])
        elif fields[0] == "captureCfg" and len(fields) == 8:
            stride = int(fields[7])
    return fmt, stride


def _profile_check(cfg_path: str) -> Check:
    stem = Path(cfg_path).stem
    name = f"profiles/profile {stem} loads"

    def run(ctx: Context) -> CheckResult:
        want_fmt, want_stride = expected_profile_shape(cfg_path)
        ctx.radar.send_config(cfg_path)
        try:
            snap = stats_snapshot(ctx)
        finally:
            ctx.radar.stop_sensor()
        problems = []
        if snap.active != 1:
            problems.append(f"active={snap.active}")
        if want_fmt is not None and snap.format != want_fmt:
            problems.append(f"format={snap.format} want {want_fmt}")
        if want_stride is not None and snap.stride != want_stride:
            problems.append(f"stride={snap.stride} want {want_stride}")
        if snap.used is not None and snap.capacity is not None and snap.used > snap.capacity:
            problems.append(f"used={snap.used} > capacity={snap.capacity}")
        if problems:
            return failed(name, "; ".join(problems))
        return passed(
            name,
            f"format={snap.format} plan={snap.plan_pre}pre/{snap.plan_post}post used={snap.used}/{snap.capacity}",
        )

    return Check(name, run)


def profiles_section(profiles: tuple[str, ...]) -> Section:
    """Argument validation of the capture commands, then each shipped profile loads."""
    checks = [
        Check("profiles/captureCfg validation", _check_capture_cfg),
        Check("profiles/phaseCaptureCfg validation", _check_phase_capture_cfg),
        Check("profiles/captureFormat", _check_capture_format),
        Check("profiles/iq8Scale", _check_iq8_scale),
    ]
    checks.extend(_profile_check(path) for path in profiles)
    return Section("profiles", "stopped", tuple(checks))


# Mirrors L3_SPARSE_REQUEST_TIMEOUT_MS in firmware/iwr6843/dump_format.h.
SPARSE_REQUEST_TIMEOUT_S = 5.0

TRACK_CFG_CASES: tuple[tuple[str, bool], ...] = (
    ("trackCfg 9e-05 0.046875 0 0 0", True),
    ("trackCfg 9e-05 0.046875 0 0", False),
    ("trackCfg -1 0.046875 0 0 0", False),
    ("trackCfg 0 0.046875 0 0 0", False),
    ("trackCfg 9e-05 0 0 0 0", False),
)


def _every_cell(summary) -> list[tuple[int, int]]:
    geometry = summary.geometry
    return [
        (frame, local)
        for frame in range(geometry.n_frames)
        for local in range(geometry.frame_bin_count(frame))
    ]


def _check_l3dump(ctx: Context) -> CheckResult:
    name = "readback/l3dump streams a valid dump"
    plan = stats_snapshot(ctx)
    raw = ctx.radar.read_dump()
    meta = parse_header(raw)
    after = stats_snapshot(ctx)
    problems = []
    want_frames = (plan.plan_pre or 0) + (plan.plan_post or 0)
    if meta["n_frames"] != want_frames:
        problems.append(f"n_frames={meta['n_frames']} want {want_frames}")
    loops = parse_stats(plan.raw).get("loops")
    if loops is not None and meta["chirps_per_frame"] != meta["n_tx"] * loops:
        problems.append(
            f"chirps_per_frame={meta['chirps_per_frame']} want n_tx*loops={meta['n_tx'] * loops}"
        )
    if after.active != 1:
        problems.append(f"active={after.active} after l3dump")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, f"{len(raw)} bytes, v{meta['version']}, {meta['n_frames']} frames")


def _check_sparse_limit(ctx: Context) -> CheckResult:
    name = "readback/l3sparse returns every cell at the limit"
    seen: dict[str, int] = {}

    def plan(summary):
        cells = _every_cell(summary)
        _request, sent = fit_cell_request(cells)
        seen["sent"] = sent
        # The first cell doubles as the noise cell; the rest fill the budget.
        return SparsePlan(cells=tuple(cells[1:sent]), noise_cells=(cells[0],))

    capture = ctx.radar.read_sparse(plan)
    if capture is None:
        return failed(name, "firmware refused l3sparse before freezing")
    detail = f"{capture.sent_cells}/{seen['sent']} cells, noise {capture.noise_power:.1f}"
    if capture.sent_cells != seen["sent"] or not capture.noise_power or capture.noise_power <= 0:
        return failed(name, detail)
    return passed(name, detail)


def _start_sparse(ctx: Context) -> tuple[PowerSummary, bytes]:
    """Read the ILP1 power packet; also return whatever arrived right behind it.

    ``_read_packet`` reads everything the port already has waiting, which can
    include CLI text the firmware sent immediately after the packet (a scripted
    test posts it in the same batch; on hardware it is whatever beat the next
    poll). Callers that need that trailing text must not drop it.
    """
    ctx.radar.ser.reset_input_buffer()
    ctx.radar.ser.write(b"l3sparse\n")
    read = ctx.radar._read_packet(POWER_MAGIC, power_packet_size, 8.0)  # pylint: disable=protected-access
    if read is None:
        raise RuntimeError("firmware refused l3sparse")
    packet, rest = read
    return parse_power(packet), rest


def _cli_reply(ctx: Context, window_s: float, prefix: bytes = b"") -> str:
    deadline = ctx.clock() + window_s
    reply = bytearray(prefix)
    while ctx.clock() < deadline:
        if b"Error" in reply and b"\n" in reply.split(b"Error", 1)[1]:
            break
        waiting = ctx.radar.ser.in_waiting
        reply += ctx.radar.ser.read(waiting if waiting else 1)
        if not waiting:
            ctx.sleep(0.02)
    return bytes(reply).decode(errors="replace")


def _check_sparse_oversized(ctx: Context) -> CheckResult:
    name = "readback/l3sparse refuses an oversized request"
    summary, rest = _start_sparse(ctx)
    request = format_cell_request(_every_cell(summary) * 4)
    if len(request) <= SPARSE_REQUEST_MAX_BYTES:
        return failed(name, f"test request only {len(request)} bytes; cannot exceed the limit")
    ctx.radar.ser.write(request)
    reply = _cli_reply(ctx, 3.0, prefix=rest)
    health = ctx.radar.stats()
    problems = []
    if "longer than" not in reply:
        problems.append(f"not refused: {reply.strip()[-60:]!r}")
    if "Done" not in health or "not recognized" in health:
        problems.append(f"CLI dirty afterwards: {health.strip()[-60:]!r}")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, f"{len(request)}-byte request refused, CLI clean")


def _check_sparse_late(ctx: Context) -> CheckResult:
    name = "readback/l3sparse refuses a late request, then works"
    _summary, rest = _start_sparse(ctx)
    ctx.sleep(SPARSE_REQUEST_TIMEOUT_S + 0.5)
    reply = _cli_reply(ctx, 2.0, prefix=rest)
    capture = ctx.radar.read_sparse(
        lambda summary: SparsePlan(cells=(), noise_cells=(_every_cell(summary)[0],))
    )
    problems = []
    if "request missing" not in reply:
        problems.append(f"late request not refused: {reply.strip()[-60:]!r}")
    if capture is None or not capture.noise_power or capture.noise_power <= 0:
        problems.append("next l3sparse did not work")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, "late request refused, next exchange worked")


def _check_track_needs_cfg(ctx: Context) -> CheckResult:
    name = "readback/l3track without trackCfg is refused"
    before = stats_snapshot(ctx)
    reply = ctx.radar.cmd("l3track", 3.0)
    after = stats_snapshot(ctx)
    problems = []
    if "needs trackCfg" not in reply:
        problems.append(f"reply {reply.strip()[:60]!r}")
    if before.freeze_req != after.freeze_req:
        problems.append(f"freeze_req moved {before.freeze_req} -> {after.freeze_req}")
    if after.active != 1:
        problems.append(f"active={after.active}")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, "refused without freezing")


def _check_track_cfg_validation(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "readback/trackCfg validation", TRACK_CFG_CASES)


def track_config_command(cfg_path: str | Path) -> str:
    """``trackCfg`` with this cfg's loop period, the shared range resolution, and no clamps."""
    loop_period = read_capture_config(cfg_path).loop_period_s or LOOP_PRI_S
    fields = (loop_period, RANGE_SPAN_M / RANGE_FFT_SIZE, 0.0, 0.0, 0.0)
    return "trackCfg " + " ".join(f"{value:.17g}" for value in fields)


def _check_track_streams(ctx: Context) -> CheckResult:
    name = "readback/l3track streams the tracked cells"
    reply = ctx.radar.cmd(track_config_command(ctx.config), 2.0)
    if "Done" not in reply or "Error" in reply:
        return failed(name, f"trackCfg rejected: {reply.strip()[:60]!r}")
    before = stats_snapshot(ctx)
    started = ctx.clock()
    result = ctx.radar.read_tracked()
    elapsed = ctx.clock() - started
    if result is None:
        if before.format == "iq8":
            return skipped(name, "IQ8 profile: l3track not available")
        return failed(name, "firmware refused l3track before streaming")
    raw, noise, track = result
    after = stats_snapshot(ctx)
    problems = []
    if (
        after.freeze_done is None
        or before.freeze_done is None
        or after.freeze_done != before.freeze_done + 1
    ):
        problems.append(f"freeze_done {before.freeze_done} -> {after.freeze_done}")
    if after.active != 1:
        problems.append(f"active={after.active}")
    detail = (
        f"{len(raw)} bytes, noise {noise:.1f}, "
        f"found={track.found} inliers={track.n_inliers}, {elapsed:.2f}s"
    )
    if problems:
        return failed(name, detail + "; " + "; ".join(problems))
    return passed(name, detail)


def readback_section() -> Section:
    """l3dump, l3sparse and l3track all stream and rearm on the default profile."""
    return Section(
        "readback",
        "active",
        (
            Check("readback/l3dump streams a valid dump", _check_l3dump),
            Check("readback/l3sparse returns every cell at the limit", _check_sparse_limit),
            Check("readback/l3sparse refuses an oversized request", _check_sparse_oversized),
            Check("readback/l3sparse refuses a late request, then works", _check_sparse_late),
            Check("readback/l3track without trackCfg is refused", _check_track_needs_cfg),
            Check("readback/trackCfg validation", _check_track_cfg_validation),
            Check("readback/l3track streams the tracked cells", _check_track_streams),
        ),
    )
