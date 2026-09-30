"""Behavioural tests for the IWR6843 self-trigger front end, firmware/iwr6843/l3_trigger.c.

The front end no longer fires anything: it places the watch region around the
destination bin, keeps the adaptive noise floor the club track's targets are
extracted against (threshold = floor x snr), and keeps the raw-input trace
and per-bin maximum that answer "did the radar see anything at all". The club
track's range-only impact (l3_impact.c) is what fires the capture.

The module is plain C, built here with the host C compiler
(openflight.iwr6843.firmware_host, which also holds the ctypes mirrors) and
driven through ctypes with synthetic frames: a noise floor across the watch
region plus "clubhead" returns placed per frame.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843.firmware_host import (
    STAT_ENERGY,
    STAT_PEAK,
    TRIG_MAX_BINS,
    TRIG_TRACE_DEPTH,
    BinObs as Obs,
    Trig,
    TrigCfg as Cfg,
    TrigTrace as Trace,
    build_firmware_library,
    host_compiler,
)

# Mirror l3_trigger.h.
MAX_BINS = TRIG_MAX_BINS
TRACE_DEPTH = TRIG_TRACE_DEPTH
TRACE_RATIO = 2.0

# Fixture geometry: tee at local bin 20, 12 approach bins, 3 bins past it ->
# the region is bins 8..23.
TEE = 20
APPROACH = 12
PAST = 3
NOISE = 100.0
# Strongest loop as a fraction of the 12-loop energy for a target present in
# every loop, and for noise: 1/12 each, a little more for the maximum.
PEAK_FRACTION = 0.25
# Loop 0 alone: one loop of twelve.
LOOP0_FRACTION = 1.0 / 12.0
CLUB = 60.0 * NOISE


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def make_cfg(lib, **overrides) -> Cfg:
    cfg = Cfg()
    lib.l3_trig_cfg_defaults(ctypes.byref(cfg))
    values = {"teeBin": TEE, "snr": 6.0, "approachBins": APPROACH, "pastBins": PAST}
    values.update(overrides)
    for name, value in values.items():
        setattr(cfg, name, value)
    return cfg


class FrontEnd:
    """One front-end instance plus a frame builder for its watch region.

    Bins are global. The window starts at global bin 0 unless
    ``window_start`` says otherwise, so local offsets equal global bins.
    """

    def __init__(self, lib, cfg: Cfg, bin_count: int = 53, window_start: int = 0, tee=None):
        self.lib = lib
        self.cfg = cfg
        self.tee = cfg.teeBin if tee is None else tee
        self.trig = Trig()
        lib.l3_trig_init(ctypes.byref(self.trig), ctypes.byref(cfg))
        first = ctypes.c_uint32()
        count = ctypes.c_uint32()
        assert (
            lib.l3_trig_region(
                ctypes.byref(cfg),
                self.tee,
                window_start,
                bin_count,
                ctypes.byref(first),
                ctypes.byref(count),
            )
            == 1
        )
        self.first_local = first.value
        self.first = window_start + first.value  # global bin of obs[0]
        self.count = count.value
        self.frame = 0

    def feed(
        self,
        targets: dict[int, float] | None = None,
        *,
        noise: float = NOISE,
        coherence: float = 0.9,
        target_peak_fraction: float = PEAK_FRACTION,
    ) -> None:
        """One frame: noise everywhere (a few percent per-bin ripple, so the
        median is exercised), plus targets {global_bin: energy}."""
        obs = (Obs * self.count)()
        for index in range(self.count):
            obs[index].energy = noise * (1.0 + 0.04 * ((index * 7 + self.frame) % 5 - 2))
            obs[index].peak = obs[index].energy * PEAK_FRACTION
            obs[index].loop0 = obs[index].energy * LOOP0_FRACTION
        for global_bin, energy in (targets or {}).items():
            index = global_bin - self.first
            assert 0 <= index < self.count, f"bin {global_bin} outside the region"
            obs[index].energy = energy
            obs[index].peak = energy * target_peak_fraction
            obs[index].loop0 = energy * LOOP0_FRACTION
            obs[index].r1Re = coherence * energy
        self.frame += 1
        self.lib.l3_trig_observe(
            ctypes.byref(self.trig), self.frame, self.tee, self.first, obs, self.count
        )

    def traces(self) -> list[Trace]:
        out = []
        for index in range(self.lib.l3_trig_trace_count(ctypes.byref(self.trig))):
            entry = Trace()
            assert (
                self.lib.l3_trig_trace_get(ctypes.byref(self.trig), index, ctypes.byref(entry)) == 1
            )
            out.append(entry)
        return out

    def text(self, fn, *args) -> str:
        buf = ctypes.create_string_buffer(256)
        fn(*args, buf, len(buf))
        return buf.value.decode()


def front_end(lib, **overrides) -> FrontEnd:
    return FrontEnd(lib, make_cfg(lib, **overrides))


# --- configuration -----------------------------------------------------------


def test_defaults_are_the_documented_ones_and_pass_the_check(lib):
    cfg = make_cfg(lib)
    fresh = Cfg()
    lib.l3_trig_cfg_defaults(ctypes.byref(fresh))
    assert (fresh.approachBins, fresh.pastBins) == (12, 3)
    assert fresh.stat == STAT_PEAK
    assert lib.l3_trig_cfg_check(ctypes.byref(cfg)) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"snr": 0.5},
        {"snr": float("nan")},
        {"approachBins": 0},
        {"approachBins": MAX_BINS + 1},
        {"pastBins": APPROACH},  # the region must reach short of the tee
        {"stat": 2},
        {"teeBin": 253, "pastBins": 3},  # the trace stores bins in a byte
    ],
)
def test_config_the_front_end_cannot_run_is_rejected(lib, overrides):
    cfg = make_cfg(lib, **overrides)
    assert lib.l3_trig_cfg_check(ctypes.byref(cfg)) != 0


def test_the_gate_is_gone_from_the_configuration():
    """The range gate's tracker and its thresholds were removed (2026-09-30):
    the club track fires. Nothing is left to tune them."""
    names = {name for name, _type in Cfg._fields_}
    assert names == {"teeBin", "snr", "approachBins", "pastBins", "stat"}
    assert not any(name.startswith("state") for name, _type in Trig._fields_)
    assert "trackBin" not in {name for name, _type in Trig._fields_}


# --- watch region ------------------------------------------------------------


@pytest.mark.parametrize(
    ("tee", "bin_count", "expected"),
    [
        (TEE, 53, (TEE - APPROACH, APPROACH + PAST + 1)),  # whole region fits
        (5, 53, (0, 5 + PAST + 1)),  # approach clipped at bin 0
        (50, 53, (38, 53 - 38)),  # clipped at the window end
        (52, 53, (40, 13)),  # tee on the last bin
    ],
)
def test_region_clips_to_the_capture_window(lib, tee, bin_count, expected):
    cfg = make_cfg(lib, teeBin=tee)
    first = ctypes.c_uint32()
    count = ctypes.c_uint32()
    assert (
        lib.l3_trig_region(
            ctypes.byref(cfg), tee, 0, bin_count, ctypes.byref(first), ctypes.byref(count)
        )
        == 1
    )
    assert (first.value, count.value) == expected


@pytest.mark.parametrize("bin_count", [0, TEE, TEE - 3])
def test_region_is_empty_when_the_tee_is_outside_the_window(lib, bin_count):
    cfg = make_cfg(lib)
    first = ctypes.c_uint32()
    count = ctypes.c_uint32()
    assert (
        lib.l3_trig_region(
            ctypes.byref(cfg), TEE, 0, bin_count, ctypes.byref(first), ctypes.byref(count)
        )
        == 0
    )
    assert count.value == 0


def test_region_converts_a_global_tee_into_the_windows_offsets(lib):
    """Wide profile: window 20..72, tee global bin 34 -> local 14, region local 2..17."""
    cfg = make_cfg(lib, teeBin=34)
    first = ctypes.c_uint32()
    count = ctypes.c_uint32()
    assert (
        lib.l3_trig_region(ctypes.byref(cfg), 34, 20, 53, ctypes.byref(first), ctypes.byref(count))
        == 1
    )
    assert (first.value, count.value) == (2, 16)
    # The same tee against the late window (47..99) is not visible.
    assert (
        lib.l3_trig_region(ctypes.byref(cfg), 34, 47, 53, ctypes.byref(first), ctypes.byref(count))
        == 0
    )
    # A ball found at global bin 48 (2.25 m) in the pre window: local 28, region 16..31.
    assert (
        lib.l3_trig_region(ctypes.byref(cfg), 48, 20, 53, ctypes.byref(first), ctypes.byref(count))
        == 1
    )
    assert (first.value, count.value) == (16, 16)


def test_trace_reports_global_bins_and_the_destination_in_a_windowed_frame(lib):
    """Following the ball detector: the region moves to where the ball is."""
    det = FrontEnd(lib, make_cfg(lib, teeBin=34), window_start=20, tee=48)
    assert det.first == 36
    det.feed({43: CLUB})
    entry = det.traces()[-1]
    assert (entry.bin, entry.dest) == (43, 48)
    assert det.trig.maxFirstBin == 36


# --- adaptive floor (the club track's threshold) -----------------------------


def test_floor_follows_the_room_and_the_threshold_with_it(lib):
    """Fixed absolute levels break with enclosure, mounting and gain; the floor adapts."""
    det = front_end(lib, snr=4.0)
    for _ in range(20):
        det.feed()
    assert det.trig.floor == pytest.approx(NOISE * PEAK_FRACTION, rel=0.05)
    assert lib.l3_trig_threshold(ctypes.byref(det.trig)) == pytest.approx(4.0 * det.trig.floor)
    for _ in range(60):
        det.feed(noise=10.0 * NOISE)
    assert det.trig.floor == pytest.approx(10.0 * NOISE * PEAK_FRACTION, rel=0.05)


def test_floor_ignores_the_club_occupying_a_few_bins(lib):
    det = front_end(lib)
    for _ in range(20):
        det.feed()
    for local_bin in [9, 10, 11, 12]:
        det.feed({local_bin: CLUB, local_bin + 1: CLUB, local_bin + 2: CLUB})
    assert det.trig.floor == pytest.approx(NOISE * PEAK_FRACTION, rel=0.05)


def test_floor_never_drops_to_zero(lib):
    det = front_end(lib)
    for _ in range(50):
        det.feed(noise=0.0)
    assert det.trig.floor == pytest.approx(1.0)


def test_energy_statistic_is_selectable_and_floors_in_its_own_units(lib):
    det = front_end(lib, stat=STAT_ENERGY)
    for _ in range(20):
        det.feed()
    assert det.trig.floor == pytest.approx(NOISE, rel=0.05)
    peak = front_end(lib, stat=STAT_PEAK)
    for _ in range(20):
        peak.feed()
    assert peak.trig.floor == pytest.approx(NOISE * PEAK_FRACTION, rel=0.05)
    assert "stat=energy" in det.text(lib.l3_trig_format_config, ctypes.byref(det.trig))
    assert "stat=peak" in peak.text(lib.l3_trig_format_config, ctypes.byref(peak.trig))


def test_every_observed_frame_counts(lib):
    det = front_end(lib)
    for _ in range(7):
        det.feed()
    assert det.trig.frames == 7


# --- raw-input trace ---------------------------------------------------------


def test_trace_records_the_strongest_bin_with_its_readings(lib):
    det = front_end(lib, snr=6.0)
    for _ in range(10):
        det.feed()
    for local_bin in [10, 12, 15]:
        det.feed({local_bin: 3.0 * NOISE})
    traces = det.traces()
    assert [entry.bin for entry in traces] == [10, 12, 15]
    assert traces[0].gap == 10
    assert traces[0].energy == pytest.approx(3.0 * NOISE)
    assert traces[0].peak == pytest.approx(3.0 * NOISE * PEAK_FRACTION)
    assert traces[0].loop0 == pytest.approx(3.0 * NOISE * LOOP0_FRACTION)
    assert traces[0].floor == pytest.approx(NOISE * PEAK_FRACTION, rel=0.05)
    assert traces[0].threshold == pytest.approx(traces[0].floor * 6.0), "floor x snr in force"
    assert traces[0].coherencePct == 90


def test_trace_bar_is_twice_the_floor_so_noise_stays_out(lib):
    det = front_end(lib)
    for _ in range(50):
        det.feed()
    assert det.traces() == []
    det.feed({12: 1.9 * NOISE})
    assert det.traces() == []
    det.feed({12: 2.2 * NOISE})
    assert [entry.bin for entry in det.traces()] == [12]


def test_max_hold_keeps_the_largest_statistic_per_bin_with_its_frame(lib):
    det = front_end(lib)
    for _ in range(5):
        det.feed()
    det.feed({12: 4.0 * NOISE})  # frame 6
    det.feed({12: 3.0 * NOISE, 13: 2.6 * NOISE})  # frame 7
    trig = det.trig
    assert trig.maxFirstBin == det.first
    assert trig.maxBins == det.count
    index = 12 - det.first
    assert trig.maxStat[index] == pytest.approx(4.0 * NOISE * PEAK_FRACTION)
    assert trig.maxFrame[index] == 6
    assert trig.maxStat[index + 1] == pytest.approx(2.6 * NOISE * PEAK_FRACTION)
    assert trig.maxFrame[index + 1] == 7
    line = det.text(lib.l3_trig_format_maxhold, ctypes.byref(trig), index, 2)
    assert (
        line
        == f"trigmax 12:{4.0 * NOISE * PEAK_FRACTION:.0f}@6 13:{2.6 * NOISE * PEAK_FRACTION:.0f}@7"
    )


def test_trace_clear_empties_trace_and_maxima_but_keeps_the_floor(lib):
    det = front_end(lib)
    for _ in range(20):
        det.feed()
    det.feed({12: CLUB})
    floor = det.trig.floor
    lib.l3_trig_trace_clear(ctypes.byref(det.trig))
    assert det.traces() == []
    assert det.trig.maxBins == 0
    assert det.trig.floor == floor
    det.feed({15: CLUB})
    assert [entry.bin for entry in det.traces()] == [15]


def test_trace_keeps_the_newest_frames_when_full(lib):
    det = front_end(lib)
    for _ in range(TRACE_DEPTH + 10):
        det.feed({12: CLUB})
    traces = det.traces()
    assert len(traces) == TRACE_DEPTH
    assert traces[0].frame == 11
    assert traces[-1].frame == TRACE_DEPTH + 10


# --- text output (integer-only printf) --------------------------------------


def test_trace_header_and_lines_read_without_float_printf(lib):
    det = front_end(lib)
    for _ in range(3):
        det.feed()
    det.feed({12: 4.0 * NOISE})
    header = det.text(lib.l3_trig_format_trace_header, ctypes.byref(det.trig))
    assert header.startswith("trigtrace stat=peak floor=")
    assert f"bar=2.0x frames=4 region={det.first}+{det.count} entries=1" in header
    line = det.text(lib.l3_trig_format_trace, ctypes.byref(det.traces()[0]))
    assert line.startswith(
        "t frame=4 gap=3 bin=12 dest=20 dist=8 energy=400 peak=100 loop0=33 floor="
    )
    # floor ~25 (peak units): threshold ~150, energy/floor ~16, peak/floor ~4.
    assert " thr=" in line and " e/f=16." in line and " p/f=4.0 coh=90" in line


def test_summary_line_reports_frames_floor_and_threshold(lib):
    det = front_end(lib, snr=4.0)
    for _ in range(20):
        det.feed()
    summary = det.text(lib.l3_trig_format_summary, ctypes.byref(det.trig))
    assert summary.startswith("trig frames=20 floor=25.")
    assert " thr=100." in summary
    assert len(summary) < 100


def test_config_line_echoes_the_arming_parameters(lib):
    det = front_end(lib, snr=6.5)
    line = det.text(lib.l3_trig_format_config, ctypes.byref(det.trig))
    assert line == "trigcfg tee=20 snr=6.50 approach=12 past=3 stat=peak"
