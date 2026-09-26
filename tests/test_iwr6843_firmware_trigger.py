"""Behavioural tests for the IWR6843 self-trigger detector, firmware/iwr6843/l3_trigger.c.

The detector is plain C with no hardware access, so it is built here with the
host C compiler and driven through ctypes with synthetic frames: a noise floor
across the watch region plus a "clubhead" whose range bin and Doppler are
chosen per frame. Every test states what a real swing (or non-swing) looks
like to the radar and asserts the detector's verdict and its telemetry.
"""

from __future__ import annotations

import ctypes
import math
import shutil
import subprocess
from pathlib import Path

import pytest

FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"
SOURCE = FIRMWARE_DIR / "l3_trigger.c"

# Mirror l3_trigger.h.
MAX_BINS = 64
LOG_DEPTH = 128
COUNT_TOTAL = 11
NO_BIN = 0xFF
STATE_IDLE, STATE_TRACKING, STATE_FIRED = 0, 1, 2
WHY = {
    name: index
    for index, name in enumerate(
        [
            "quiet",
            "acquired",
            "advanced",
            "jumped",
            "missed",
            "lost",
            "lowcoh",
            "young",
            "slow",
            "fired",
        ]
    )
}
COUNT = {
    name: index
    for index, name in enumerate(
        ["frames", "cand", "acq", "adv", "jump", "miss", "lost", "lowcoh", "young", "slow", "fired"]
    )
}
WAVELENGTH_M = 0.00484
LOOP_PERIOD_S = 135e-6  # 3 TX x (7 us idle + 38 us ramp) on the wide profile

# Fixture geometry: tee at local bin 20, 12 approach bins, gate +/- 3 -> the
# region is bins 8..23 and the gate is bins 17..23.
TEE = 20
APPROACH = 12
GATE = 3
NOISE = 100.0


class Cfg(ctypes.Structure):
    _fields_ = [
        ("teeBin", ctypes.c_uint32),
        ("snr", ctypes.c_float),
        ("trackFrames", ctypes.c_uint32),
        ("approachBins", ctypes.c_uint32),
        ("gateBins", ctypes.c_uint32),
        ("minCoherence", ctypes.c_float),
        ("minStepBins", ctypes.c_float),
    ]


class Obs(ctypes.Structure):
    _fields_ = [("energy", ctypes.c_float), ("r1Re", ctypes.c_float), ("r1Im", ctypes.c_float)]


class Record(ctypes.Structure):
    _fields_ = [
        ("frame", ctypes.c_uint32),
        ("gap", ctypes.c_uint16),
        ("state", ctypes.c_uint8),
        ("why", ctypes.c_uint8),
        ("bin", ctypes.c_uint8),
        ("age", ctypes.c_uint8),
        ("velocityCms", ctypes.c_int16),
        ("energy", ctypes.c_float),
        ("floor", ctypes.c_float),
        ("coherencePct", ctypes.c_uint8),
    ]


class Trig(ctypes.Structure):
    _fields_ = [
        ("cfg", Cfg),
        ("state", ctypes.c_uint8),
        ("floor", ctypes.c_float),
        ("loopPeriodS", ctypes.c_float),
        ("trackBin", ctypes.c_uint8),
        ("trackStartBin", ctypes.c_uint8),
        ("trackAge", ctypes.c_uint8),
        ("trackMisses", ctypes.c_uint8),
        ("trackStartFrame", ctypes.c_uint32),
        ("counters", ctypes.c_uint32 * COUNT_TOTAL),
        ("quietSince", ctypes.c_uint32),
        ("logNext", ctypes.c_uint32),
        ("logCount", ctypes.c_uint32),
        ("log", Record * LOG_DEPTH),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no C compiler for the firmware detector")
    out = tmp_path_factory.mktemp("l3_trigger") / "l3_trigger.so"
    subprocess.run(
        [
            compiler,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            "-fPIC",
            "-O1",
            "-o",
            str(out),
            str(SOURCE),
            "-lm",
        ],
        check=True,
        cwd=FIRMWARE_DIR,
    )
    library = ctypes.CDLL(str(out))
    library.l3_trig_cfg_defaults.argtypes = [ctypes.POINTER(Cfg)]
    library.l3_trig_cfg_check.argtypes = [ctypes.POINTER(Cfg)]
    library.l3_trig_cfg_check.restype = ctypes.c_int32
    library.l3_trig_init.argtypes = [ctypes.POINTER(Trig), ctypes.POINTER(Cfg), ctypes.c_float]
    library.l3_trig_rearm.argtypes = [ctypes.POINTER(Trig)]
    library.l3_trig_region.argtypes = [
        ctypes.POINTER(Cfg),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    library.l3_trig_region.restype = ctypes.c_int32
    library.l3_trig_update.argtypes = [
        ctypes.POINTER(Trig),
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(Obs),
        ctypes.c_uint32,
    ]
    library.l3_trig_update.restype = ctypes.c_int32
    library.l3_trig_log_count.argtypes = [ctypes.POINTER(Trig)]
    library.l3_trig_log_count.restype = ctypes.c_uint32
    library.l3_trig_log_get.argtypes = [
        ctypes.POINTER(Trig),
        ctypes.c_uint32,
        ctypes.POINTER(Record),
    ]
    library.l3_trig_log_get.restype = ctypes.c_int32
    for name in ("l3_trig_format_summary", "l3_trig_format_config"):
        getattr(library, name).argtypes = [ctypes.POINTER(Trig), ctypes.c_char_p, ctypes.c_uint32]
        getattr(library, name).restype = ctypes.c_int32
    library.l3_trig_format_record.argtypes = [
        ctypes.POINTER(Record),
        ctypes.c_char_p,
        ctypes.c_uint32,
    ]
    library.l3_trig_format_record.restype = ctypes.c_int32
    library.l3_trig_why_name.argtypes = [ctypes.c_uint8]
    library.l3_trig_why_name.restype = ctypes.c_char_p
    return library


def make_cfg(lib, **overrides) -> Cfg:
    cfg = Cfg()
    lib.l3_trig_cfg_defaults(ctypes.byref(cfg))
    values = {
        "teeBin": TEE,
        "snr": 6.0,
        "trackFrames": 2,
        "approachBins": APPROACH,
        "gateBins": GATE,
    }
    values.update(overrides)
    for name, value in values.items():
        setattr(cfg, name, value)
    return cfg


class Detector:
    """One detector instance plus a frame builder for its watch region."""

    def __init__(self, lib, cfg: Cfg, bin_count: int = 53, loop_period_s: float = LOOP_PERIOD_S):
        self.lib = lib
        self.cfg = cfg
        self.trig = Trig()
        lib.l3_trig_init(ctypes.byref(self.trig), ctypes.byref(cfg), loop_period_s)
        first = ctypes.c_uint32()
        count = ctypes.c_uint32()
        assert (
            lib.l3_trig_region(
                ctypes.byref(cfg), bin_count, ctypes.byref(first), ctypes.byref(count)
            )
            == 1
        )
        self.first = first.value
        self.count = count.value
        self.frame = 0

    def feed(
        self,
        targets: dict[int, float] | None = None,
        *,
        noise: float = NOISE,
        coherence: float = 0.9,
        velocity_mps: float = 0.0,
    ) -> bool:
        """One frame: noise everywhere, plus targets {local_bin: energy}.

        Noise varies by a few percent per bin so the median is exercised.
        The lag-1 autocorrelation of each target carries the coherence and
        the Doppler phase for velocity_mps at the fixture loop period.
        """
        obs = (Obs * self.count)()
        for index in range(self.count):
            obs[index].energy = noise * (1.0 + 0.04 * ((index * 7 + self.frame) % 5 - 2))
            obs[index].r1Re = 0.0
            obs[index].r1Im = 0.0
        phase = 4.0 * math.pi * velocity_mps * LOOP_PERIOD_S / WAVELENGTH_M
        for local_bin, energy in (targets or {}).items():
            index = local_bin - self.first
            assert 0 <= index < self.count, f"bin {local_bin} outside the region"
            obs[index].energy = energy
            obs[index].r1Re = coherence * energy * math.cos(phase)
            obs[index].r1Im = coherence * energy * math.sin(phase)
        self.frame += 1
        return bool(
            self.lib.l3_trig_update(
                ctypes.byref(self.trig), self.frame, self.first, obs, self.count
            )
        )

    def records(self) -> list[Record]:
        out = []
        for index in range(self.lib.l3_trig_log_count(ctypes.byref(self.trig))):
            record = Record()
            assert (
                self.lib.l3_trig_log_get(ctypes.byref(self.trig), index, ctypes.byref(record)) == 1
            )
            out.append(record)
        return out

    def whys(self) -> list[str]:
        return [self.lib.l3_trig_why_name(record.why).decode() for record in self.records()]

    def counter(self, name: str) -> int:
        return self.trig.counters[COUNT[name]]

    def summary(self) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.l3_trig_format_summary(ctypes.byref(self.trig), buf, len(buf))
        return buf.value.decode()

    def config_line(self) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.l3_trig_format_config(ctypes.byref(self.trig), buf, len(buf))
        return buf.value.decode()

    def record_line(self, record: Record) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.l3_trig_format_record(ctypes.byref(record), buf, len(buf))
        return buf.value.decode()


CLUB = 60.0 * NOISE  # comfortably above floor * snr


def detector(lib, **overrides) -> Detector:
    return Detector(lib, make_cfg(lib, **overrides))


# --- configuration -----------------------------------------------------------


def test_defaults_are_the_documented_ones_and_pass_the_check(lib):
    cfg = make_cfg(lib)
    assert (cfg.approachBins, cfg.gateBins) == (12, 3)
    assert cfg.minCoherence == 0.0
    assert cfg.minStepBins == pytest.approx(1.0)
    assert lib.l3_trig_cfg_check(ctypes.byref(cfg)) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"snr": 0.5},
        {"snr": float("nan")},
        {"trackFrames": 0},
        {"approachBins": 0},
        {"approachBins": MAX_BINS + 1},
        {"gateBins": APPROACH},  # gate must sit inside the watched approach
        {"minCoherence": 1.5},
        {"minCoherence": -0.1},
        {"minStepBins": -1.0},
        {"teeBin": 253, "gateBins": 3},  # record stores bins in a byte, 0xFF = none
    ],
)
def test_config_the_detector_cannot_run_is_rejected(lib, overrides):
    cfg = make_cfg(lib, **overrides)
    assert lib.l3_trig_cfg_check(ctypes.byref(cfg)) != 0


@pytest.mark.parametrize(
    ("tee", "bin_count", "expected"),
    [
        (TEE, 53, (TEE - APPROACH, APPROACH + GATE + 1)),  # whole region fits
        (5, 53, (0, 5 + GATE + 1)),  # approach clipped at bin 0
        (50, 53, (38, 53 - 38)),  # gate clipped at the window end
        (52, 53, (40, 13)),  # tee on the last bin
    ],
)
def test_region_clips_to_the_capture_window(lib, tee, bin_count, expected):
    cfg = make_cfg(lib, teeBin=tee)
    first = ctypes.c_uint32()
    count = ctypes.c_uint32()
    assert (
        lib.l3_trig_region(ctypes.byref(cfg), bin_count, ctypes.byref(first), ctypes.byref(count))
        == 1
    )
    assert (first.value, count.value) == expected


@pytest.mark.parametrize("bin_count", [0, TEE, TEE - 3])
def test_region_is_empty_when_the_tee_is_outside_the_window(lib, bin_count):
    cfg = make_cfg(lib)
    first = ctypes.c_uint32()
    count = ctypes.c_uint32()
    assert (
        lib.l3_trig_region(ctypes.byref(cfg), bin_count, ctypes.byref(first), ctypes.byref(count))
        == 0
    )
    assert count.value == 0


# --- swings that must fire ---------------------------------------------------


def test_full_swing_fires_when_the_track_enters_the_gate(lib):
    """A driver's clubhead closes ~2.5 bins per frame; nothing is required after the gate."""
    det = detector(lib)
    path = [9, 11, 14, 16, 19]
    fired_at = None
    for local_bin in path:
        if det.feed({local_bin: CLUB}):
            fired_at = local_bin
            break
    assert fired_at == 19, "first bin inside the gate (>= 17) fires"
    assert det.trig.state == STATE_FIRED
    assert det.whys() == ["acquired", "advanced", "advanced", "advanced", "fired"]
    assert det.counter("fired") == 1
    last = det.records()[-1]
    assert (last.bin, last.age) == (19, 5)


def test_a_fired_detector_ignores_further_frames_until_rearmed(lib):
    det = detector(lib)
    for local_bin in [12, 15, 18]:
        det.feed({local_bin: CLUB})
    assert det.trig.state == STATE_FIRED
    frames_before = det.counter("frames")
    assert det.feed({21: CLUB}) is False
    assert det.counter("frames") == frames_before
    lib.l3_trig_rearm(ctypes.byref(det.trig))
    assert det.trig.state == STATE_IDLE
    assert det.counter("fired") == 1, "rearm keeps the counters"
    assert len(det.records()) == 3, "and the log"
    assert det.feed({10: CLUB}) is False
    assert det.feed({13: CLUB}) is False
    assert det.feed({17: CLUB}) is True
    assert det.counter("fired") == 2


def test_fast_club_seen_twice_fires_on_the_second_frame(lib):
    """Two frames of consistent approach are enough by default; no long history needed."""
    det = detector(lib)
    assert det.feed({12: CLUB}) is False
    assert det.feed({18: CLUB}) is True
    assert det.whys() == ["acquired", "fired"]


def test_club_first_seen_inside_the_gate_waits_one_frame_then_fires(lib):
    """Too young on first sight, not rejected for good."""
    det = detector(lib)
    assert det.feed({18: CLUB}) is False
    assert det.whys() == ["young"]
    assert det.feed({21: CLUB}) is True


def test_track_frames_of_one_fires_on_first_sight_in_the_gate(lib):
    det = detector(lib, trackFrames=1)
    assert det.feed({18: CLUB}) is True
    assert det.whys() == ["fired"]


def test_one_missing_frame_does_not_break_the_track(lib):
    """A weak frame mid-downswing is bridged rather than restarting the track."""
    det = detector(lib)
    for local_bin in [10, 12]:
        assert det.feed({local_bin: CLUB}) is False
    assert det.feed() is False  # club invisible this frame
    assert det.trig.state == STATE_TRACKING
    assert det.feed({15: CLUB}) is False
    assert det.feed({18: CLUB}) is True
    assert det.whys() == ["acquired", "advanced", "missed", "advanced", "fired"]
    assert det.records()[-1].age == 4, "misses do not count as observations"


def test_waggle_then_backswing_then_downswing_fires_on_the_downswing(lib):
    """Moving away restarts the track; the toward-away-toward order is not required."""
    det = detector(lib)
    for local_bin in [16, 14, 12]:  # away from the tee, 2 bins per frame
        assert det.feed({local_bin: CLUB}) is False
    assert det.whys() == ["acquired", "jumped", "jumped"]
    assert det.feed({14: CLUB}) is False
    assert det.feed({17: CLUB}) is True
    assert det.whys()[-2:] == ["advanced", "fired"]


def test_one_bin_of_scatterer_wander_toward_the_radar_keeps_the_track(lib):
    det = detector(lib)
    for local_bin in [10, 13, 12]:  # -1 bin is jitter, not a retreat
        assert det.feed({local_bin: CLUB}) is False
    assert det.whys() == ["acquired", "advanced", "advanced"]


def test_the_ball_bin_needs_no_motion_for_the_trigger_to_arm(lib):
    """MTI suppresses the stationary ball; the tee bin sits at the noise floor throughout."""
    det = detector(lib)
    tee_index = TEE - det.first
    for local_bin in [11, 14, 17]:
        fired = det.feed({local_bin: CLUB})
    assert fired is True
    # The frame builder never raised the tee bin above noise.
    assert all(record.bin != TEE for record in det.records())
    assert tee_index < det.count


# --- non-swings that must not fire -------------------------------------------


def test_noise_alone_never_fires_and_logs_nothing(lib):
    det = detector(lib)
    for _ in range(200):
        assert det.feed() is False
    assert det.trig.state == STATE_IDLE
    assert det.counter("frames") == 200
    assert det.counter("cand") == 0
    assert det.records() == []
    assert det.trig.floor == pytest.approx(NOISE, rel=0.05)


def test_a_person_walking_up_to_the_ball_is_too_slow(lib):
    """~1 bin per 5 frames (~3 m/s radial) reaches the gate but is not a clubhead."""
    det = detector(lib)
    local_bin = 10
    fired = False
    for frame in range(60):
        fired |= det.feed({local_bin: CLUB})
        if frame % 5 == 4:
            local_bin += 1
    assert fired is False
    assert det.counter("slow") > 0
    assert det.counter("fired") == 0
    assert det.trig.state == STATE_TRACKING


def test_min_step_of_zero_lets_a_slow_target_fire(lib):
    """The speed test is a tunable, so a putt-speed target can be admitted deliberately."""
    det = detector(lib, minStepBins=0.0)
    local_bin = 14
    fired = False
    for frame in range(40):
        fired |= det.feed({local_bin: CLUB})
        if fired:
            break
        if frame % 5 == 4:
            local_bin += 1
    assert fired is True


def test_a_return_that_jumps_more_than_eight_bins_restarts_the_track(lib):
    """Hands, shaft and clubhead swapping as the strongest bin must not be one target."""
    det = detector(lib)
    assert det.feed({9: CLUB}) is False
    assert det.feed({19: CLUB}) is False, "10-bin jump lands in the gate but with age 1"
    assert det.whys() == ["acquired", "young"]
    assert det.trig.trackStartBin == 19


def test_two_missing_frames_drop_the_track(lib):
    det = detector(lib)
    det.feed({10: CLUB})
    det.feed()
    assert det.trig.state == STATE_TRACKING
    det.feed()
    assert det.trig.state == STATE_IDLE
    assert det.whys() == ["acquired", "missed", "lost"]
    assert det.counter("lost") == 1


def test_candidates_below_snr_times_floor_are_not_candidates(lib):
    det = detector(lib, snr=6.0)
    for local_bin in [11, 14, 17]:
        assert det.feed({local_bin: 5.5 * NOISE}) is False
    assert det.counter("cand") == 0
    for local_bin in [11, 14, 17]:
        fired = det.feed({local_bin: 6.5 * NOISE})
    assert fired is True


# --- adaptive floor ----------------------------------------------------------


def test_floor_follows_the_room_and_the_threshold_with_it(lib):
    """Fixed absolute levels break with enclosure, mounting and gain; the floor adapts."""
    det = detector(lib, snr=4.0)
    for _ in range(20):
        det.feed()
    assert det.trig.floor == pytest.approx(NOISE, rel=0.05)
    assert det.feed({12: 5.0 * NOISE}) is False
    assert det.counter("cand") == 1, "5x the quiet floor is a candidate"
    for _ in range(60):
        det.feed(noise=10.0 * NOISE)
    assert det.trig.floor == pytest.approx(10.0 * NOISE, rel=0.05)
    # The step itself reads as a candidate for the few frames the floor
    # takes to catch up (a 10x jump clears 4x the old floor); the range
    # track, not the threshold, is what keeps that from firing.
    settled = det.counter("cand")
    assert settled <= 8
    assert det.counter("fired") == 0
    det.feed({12: 5.0 * NOISE}, noise=10.0 * NOISE)
    assert det.counter("cand") == settled, "the same energy is under the raised floor"


def test_floor_ignores_the_club_occupying_a_few_bins(lib):
    det = detector(lib)
    for _ in range(20):
        det.feed()
    for local_bin in [9, 10, 11, 12]:
        det.feed({local_bin: CLUB, local_bin + 1: CLUB, local_bin + 2: CLUB})
    assert det.trig.floor == pytest.approx(NOISE, rel=0.05)


def test_floor_never_drops_to_zero(lib):
    det = detector(lib)
    for _ in range(50):
        det.feed(noise=0.0)
    assert det.trig.floor == pytest.approx(1.0)


# --- Doppler telemetry -------------------------------------------------------


def test_coherence_gate_rejects_an_incoherent_strong_bin_and_logs_why(lib):
    det = detector(lib, minCoherence=0.5)
    assert det.feed({12: CLUB}, coherence=0.1) is False
    assert det.trig.state == STATE_IDLE
    assert det.whys() == ["lowcoh"]
    assert det.records()[0].bin == 12, "the rejected bin is still logged for tuning"
    assert det.records()[0].coherencePct == 10
    assert det.feed({12: CLUB}, coherence=0.8) is False
    assert det.whys()[-1] == "acquired"


def test_coherence_rejection_outranks_the_miss_it_causes(lib):
    det = detector(lib, minCoherence=0.5)
    det.feed({12: CLUB})
    det.feed({14: CLUB}, coherence=0.2)
    det.feed({16: CLUB}, coherence=0.2)
    assert det.whys() == ["acquired", "lowcoh", "lowcoh"]
    assert [record.state for record in det.records()] == [
        STATE_TRACKING,
        STATE_TRACKING,
        STATE_IDLE,
    ]


def test_coherence_gate_is_off_by_default(lib):
    det = detector(lib)
    assert det.feed({12: CLUB}, coherence=0.0) is False
    assert det.whys() == ["acquired"]


@pytest.mark.parametrize("velocity", [0.0, 2.5, -4.0, 8.0])
def test_unaliased_velocity_is_read_back_in_cm_per_s(lib, velocity):
    det = detector(lib)
    det.feed({12: CLUB}, velocity_mps=velocity)
    assert det.records()[0].velocityCms == pytest.approx(velocity * 100.0, abs=3)


def test_velocity_beyond_the_unambiguous_span_aliases(lib):
    """+/- lambda / (4 T) is ~9 m/s here: a 12 m/s club reads as 12 - 17.9 m/s.

    That is why the trigger gates on range rate across frames, not on the
    Doppler sign, and why velocity is a readout rather than a condition.
    """
    span = WAVELENGTH_M / (2.0 * LOOP_PERIOD_S)
    det = detector(lib)
    det.feed({12: CLUB}, velocity_mps=12.0)
    assert det.records()[0].velocityCms == pytest.approx((12.0 - span) * 100.0, abs=3)


def test_no_loop_period_means_no_velocity(lib):
    det = Detector(lib, make_cfg(lib), loop_period_s=0.0)
    det.feed({12: CLUB}, velocity_mps=5.0)
    assert det.records()[0].velocityCms == 0


# --- flight recorder ---------------------------------------------------------


def test_records_carry_the_quiet_gap_before_them(lib):
    det = detector(lib)
    for _ in range(10):
        det.feed()
    det.feed({12: CLUB})
    for _ in range(3):
        det.feed()  # missed, lost, quiet
    det.feed({9: CLUB})
    gaps = [record.gap for record in det.records()]
    assert gaps == [10, 0, 0, 1]


def test_log_keeps_the_newest_frames_when_full(lib):
    det = detector(lib, minStepBins=0.0, trackFrames=200)
    for _ in range(LOG_DEPTH + 20):
        det.feed({12: CLUB})  # every frame logs, never fires (too young)
    records = det.records()
    assert len(records) == LOG_DEPTH
    assert records[0].frame == 21
    assert records[-1].frame == LOG_DEPTH + 20
    assert "records=128" in det.summary()


def test_record_carries_energy_floor_state_and_age(lib):
    det = detector(lib)
    for _ in range(8):
        det.feed()
    det.feed({12: CLUB})
    det.feed({15: CLUB})
    record = det.records()[-1]
    assert record.energy == pytest.approx(CLUB)
    assert record.floor == pytest.approx(NOISE, rel=0.05)
    assert (record.state, record.age, record.bin) == (STATE_TRACKING, 2, 15)


# --- text output (integer-only printf) --------------------------------------


def test_summary_line_reports_state_floor_and_counters(lib):
    det = detector(lib)
    for local_bin in [11, 14, 17]:
        det.feed({local_bin: CLUB})
    summary = det.summary()
    assert summary.startswith("trig state=fired floor=")
    for token in ("frames=3", "cand=3", "acq=1", "adv=1", "fired=1", "records=3"):
        assert token in summary
    assert len(summary) < 160


def test_config_line_echoes_the_arming_parameters(lib):
    det = detector(lib, snr=6.5, minStepBins=1.25)
    line = det.config_line()
    assert line == (
        "trigcfg tee=20 snr=6.50 track=2 approach=12 gate=3 mincoh=0.00 minstep=1.25 loopus=135.0"
    )


def test_record_line_is_human_readable_without_float_printf(lib):
    det = detector(lib)
    for _ in range(4):
        det.feed()
    det.feed({12: CLUB}, velocity_mps=2.5)
    line = det.record_line(det.records()[0])
    assert line.startswith(
        "frame=5 gap=4 state=tracking why=acquired bin=12 age=1 energy=6000 floor="
    )
    assert " snr=" in line and " v=2.5" in line and line.endswith("coh=90")


def test_record_line_shows_a_dash_when_no_bin_was_seen(lib):
    det = detector(lib)
    det.feed({12: CLUB})
    det.feed()
    line = det.record_line(det.records()[1])
    assert "why=missed bin=- age=1" in line


def test_why_names_cover_every_reason(lib):
    for name, code in WHY.items():
        assert lib.l3_trig_why_name(code).decode() == name
    assert lib.l3_trig_why_name(len(WHY)).decode() == "?"
