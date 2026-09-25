"""Parser and ring-replay report for scripts/iwr6843/swing_trigger.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "swing_trigger.py"
spec = importlib.util.spec_from_file_location("swing_trigger", SCRIPT)
swing_trigger = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = swing_trigger
spec.loader.exec_module(swing_trigger)

TEE_BIN = 14
LEVEL = 1000.0


def _row(peaks: dict[int, float], bins: int = 53) -> np.ndarray:
    power = np.zeros(bins, dtype=np.float64)
    for index, value in peaks.items():
        power[index] = value
    return power


def _leave_power(n_loops: int = 1) -> np.ndarray:
    frames = [
        _row({TEE_BIN: LEVEL}),
        _row({TEE_BIN: LEVEL}),
        _row({TEE_BIN: LEVEL, 2: LEVEL}),
        _row({TEE_BIN: LEVEL, 6: LEVEL + 1}),
        _row({TEE_BIN: LEVEL, 3: LEVEL + 2}),
    ]
    rows = []
    for frame in frames:
        rows.append(frame)
        for _loop in range(1, n_loops):
            rows.append(np.zeros_like(frame))
    return np.stack(rows)


def test_windows_port_name_is_rejected_on_the_pi():
    message = swing_trigger.port_name_error("COM5", "linux")

    assert message is not None
    assert "leave --port off" in message
    assert swing_trigger.port_name_error("COM5", "win32") is None
    assert swing_trigger.port_name_error("/dev/ttyUSB0", "linux") is None
    assert swing_trigger.port_name_error(None, "linux") is None


def test_parse_trig_reads_stats_and_debug_lines():
    stats = swing_trigger.parse_trig("trig phase=watching tee=1800 latched=0 enabled=1")
    debug = swing_trigger.parse_trig(
        "trig phase=toward tee=10 approach=4 ready=1 toward=1 away=0 "
        "run=3 peak=8 have=1 bin=14 level=1000 latched=0"
    )

    assert stats == {"phase": "watching", "tee": "1800", "latched": "0", "enabled": "1"}
    assert debug["phase"] == "toward"
    assert debug["peak"] == "8"
    assert swing_trigger.parse_trig("frames=1 active=1") is None


def test_released_fired_phase_is_not_a_new_swing():
    released = swing_trigger.parse_trig("trig phase=fired tee=1000 latched=0 enabled=1")
    held = swing_trigger.parse_trig("trig phase=fired tee=1000 latched=1 enabled=1")
    legacy = swing_trigger.parse_trig("trig phase=fired tee=1000")

    assert not swing_trigger.is_latched(released)
    assert swing_trigger.is_latched(held)
    assert swing_trigger.is_latched(legacy)


def test_format_status_names_an_occupied_tee():
    fields = swing_trigger.parse_trig("trig phase=watching tee=1800 latched=0 enabled=1")

    text = swing_trigger.format_status(fields, LEVEL)

    assert "watching" in text
    assert "tee=1800" in text
    assert "ball" in text
    assert "latched=0" in text


def test_replay_uses_loop0_and_reports_the_fire_frame():
    observations = swing_trigger.replay_loop0(_leave_power(n_loops=2), 2, TEE_BIN, LEVEL, 2)

    assert observations[-1].fired
    assert observations[-1].frame == 4
    text = swing_trigger.format_swing(observations)
    assert "PASS  replay fired at frame 4" in text
    assert "toward" in text


def test_fire_frame_names_the_loud_bins():
    row = _row({TEE_BIN: LEVEL, 8: LEVEL * 3, TEE_BIN + 4: LEVEL * 5})

    text = swing_trigger.format_hotspot(row, TEE_BIN, LEVEL)

    assert f"tee bin {TEE_BIN}={LEVEL:.0f}" in text
    assert "approach bin 8=" in text
    assert f"past bin {TEE_BIN + 4}=" in text
    assert "bins >=" in text


def test_quiet_tee_does_not_pass():
    power = np.stack([_row({TEE_BIN: LEVEL}) for _frame in range(4)])

    observations = swing_trigger.replay_loop0(power, 1, TEE_BIN, LEVEL, 2)

    assert not any(obs.fired for obs in observations)
    assert "FAIL" in swing_trigger.format_swing(observations)


class _ArmRadar:
    def __init__(self):
        self.commands: list[str] = []

    def send_config(self, path: str) -> None:
        self.commands.append(path)

    def cmd(self, line: str, window: float = 1.5) -> str:
        del window
        self.commands.append(line)
        return "Done\n"


def test_omitted_level_samples_the_lane_and_arms_above_it(monkeypatch):
    def measure(_radar, tee_bin, hits):
        assert (tee_bin, hits) == (TEE_BIN, 2)
        return 200000.0, 300000.0

    monkeypatch.setattr(swing_trigger, "measure_trigger_level", measure)
    radar = _ArmRadar()

    level = swing_trigger._arm(radar, "cfg", TEE_BIN, None, 2)

    assert level == 300000.0
    assert radar.commands[-1] == "triggerCfg 14 300000 2"


def test_explicit_level_is_armed_without_sampling(monkeypatch):
    def measure(*_args, **_kwargs):
        raise AssertionError("explicit level must not sample the lane")

    monkeypatch.setattr(swing_trigger, "measure_trigger_level", measure)
    radar = _ArmRadar()

    level = swing_trigger._arm(radar, "cfg", TEE_BIN, 250.0, 2)

    assert level == 250.0
    assert radar.commands[-1] == "triggerCfg 14 250 2"
