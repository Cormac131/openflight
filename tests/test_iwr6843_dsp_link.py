"""The host side of the MSS <-> DSS detect link diagnostics (Phase 0).

``trackCfg dsp ping`` and ``trackCfg dsp probe [bins]`` (firmware l3_dump.c)
prove on the board that the DSS answers and scores ring frames exactly as
the MSS does, and how much faster. These parse their replies.
"""

from __future__ import annotations

import re

import pytest

from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.dsp_link import (
    DspHw,
    DspLinkError,
    DspProbe,
    DspStatus,
    parse_dsp_hw,
    parse_dsp_pong,
    parse_dsp_probe,
    parse_dsp_status,
    summarize_probes,
)

PROBE = (
    "dsp probe slot=7 bins=27 mss_us=1971 dss_us=240 dss_cycles=144000 match=1 "
    "status=0 mss_energy=4b1c2a3f dss_energy=4b1c2a3f\nDone\n"
)


def test_a_pong_gives_the_round_trip():
    assert parse_dsp_pong("dsp pong seq=4 us=38\nDone\n") == 38


def test_a_probe_is_parsed_field_by_field():
    probe = parse_dsp_probe(PROBE)
    assert probe == DspProbe(
        slot=7,
        bins=27,
        mss_us=1971,
        dss_us=240,
        dss_cycles=144000,
        match=True,
        status=0,
        mss_energy="4b1c2a3f",
        dss_energy="4b1c2a3f",
    )


def test_a_mismatch_is_reported_not_raised():
    """A mismatch is the finding the probe exists for, not a failure to parse."""
    text = PROBE.replace("match=1", "match=0").replace("dss_energy=4b1c2a3f", "dss_energy=4b1c2a40")
    probe = parse_dsp_probe(text)
    assert probe.match is False and probe.dss_energy == "4b1c2a40"


@pytest.mark.parametrize(
    "text",
    [
        "Error: DSP did not answer (link closed)\n",
        "Error: dspProbe needs an IQ16 ring (captureFormat iq16)\n",
        "Error: trackCfg dsp ping | probe [bins]\n",
    ],
)
def test_a_firmware_error_is_raised_with_its_text(text):
    with pytest.raises(DspLinkError, match=re.escape(text.strip().removeprefix("Error: ")[:20])):
        parse_dsp_probe(text)
    with pytest.raises(DspLinkError):
        parse_dsp_pong(text)


@pytest.mark.parametrize("text", ["", "Done\n", "dsp probe slot=7 bins=27\nDone\n"])
def test_a_reply_without_the_line_or_its_fields_is_an_error(text):
    with pytest.raises(DspLinkError):
        parse_dsp_probe(text)


def test_firmware_without_the_link_is_an_error():
    """An older image answers trackCfg dsp with trackCfg's usage error."""
    with pytest.raises(DspLinkError):
        parse_dsp_pong("Error: trackCfg <loopPeriodS> ...\n")


def test_the_summary_is_the_median_speedup_and_every_mismatch():
    probes = [
        parse_dsp_probe(PROBE),
        parse_dsp_probe(
            PROBE.replace("mss_us=1971", "mss_us=2100").replace("dss_us=240", "dss_us=200")
        ),
        parse_dsp_probe(PROBE.replace("match=1", "match=0")),
    ]
    summary = summarize_probes(probes)
    assert summary.count == 3
    assert summary.mismatches == 1
    assert summary.mss_us_median == 1971
    assert summary.dss_us_median == 240
    assert summary.speedup == pytest.approx(1971 / 240)


def test_a_summary_needs_probes():
    with pytest.raises(ValueError):
        summarize_probes([])


class _Radar(IWR6843Radar):
    def __init__(self, reply: str):  # pylint: disable=super-init-not-called
        self.sent: list[tuple[str, float]] = []
        self._reply = reply

    def cmd(self, line, timeout=1.0):
        self.sent.append((line, timeout))
        return self._reply


def test_the_driver_sends_the_trackcfg_sub_mode():
    radar = _Radar("dsp pong seq=1 us=40\nDone\n")
    assert radar.dsp_ping() == 40
    assert radar.sent[0][0] == "trackCfg dsp ping"


@pytest.mark.parametrize(
    ("bins", "line"), [(None, "trackCfg dsp probe"), (27, "trackCfg dsp probe 27")]
)
def test_the_driver_probes_all_bins_or_the_first_few(bins, line):
    radar = _Radar(PROBE)
    assert radar.dsp_probe(bins).bins == 27
    assert radar.sent[0][0] == line


def test_the_driver_refuses_a_nonsense_bin_count():
    with pytest.raises(ValueError):
        _Radar(PROBE).dsp_probe(0)


def test_the_dss_status_line_is_parsed():
    status = parse_dsp_status("dsp status stage=link_open err=0 beats=412 served=0\nDone\n")
    assert status == DspStatus(stage="link_open", failed=False, err=0, beats=412, served=0)
    assert status.booted


def test_a_failed_stage_is_parsed():
    status = parse_dsp_status("dsp status stage=soc_init FAILED err=-3 beats=0 served=0\n")
    assert (status.stage, status.failed, status.err) == ("soc_init", True, -3)


def test_a_dss_that_never_booted_is_parsed():
    status = parse_dsp_status("dsp status stage=never_booted magic=00000000\nDone\n")
    assert status.stage == "never_booted" and not status.booted


def test_status_is_read_even_when_the_reply_also_carries_an_error():
    """A failed ping prints the status line and then its Error line."""
    text = "dsp status stage=task err=0 beats=0 served=0\nError: DSP did not answer (link open)\n"
    assert parse_dsp_status(text).stage == "task"


def test_a_reply_without_a_status_line_is_an_error():
    with pytest.raises(DspLinkError):
        parse_dsp_status("Error: trackCfg <loopPeriodS> ...\n")


def test_the_driver_asks_for_the_status():
    radar = _Radar("dsp status stage=link_open err=0 beats=3 served=1\nDone\n")
    assert radar.dsp_status().served == 1
    assert radar.sent[0][0] == "trackCfg dsp status"


HW = (
    "dsp hw gpreg_stage=reset halt=0 power=3 stc=1 "
    "esm=00000000,20000000,00000000,00000000 hsram=ok\nDone\n"
)


def test_the_dss_hardware_line_is_parsed():
    hw = parse_dsp_hw(HW)
    assert hw == DspHw(
        gpreg_stage="reset",
        halt=0,
        power=3,
        stc=1,
        esm=(0x0, 0x20000000, 0x0, 0x0),
        hsram_ok=True,
    )
    assert hw.powered and not hw.halted


def test_an_untagged_register_and_a_bad_hs_ram_are_parsed():
    hw = parse_dsp_hw(
        HW.replace("gpreg_stage=reset", "gpreg_stage=none(00000000)").replace(
            "hsram=ok", "hsram=BAD"
        )
    )
    assert hw.gpreg_stage == "none(00000000)" and not hw.hsram_ok


def test_a_failed_register_stage_is_kept_verbatim():
    assert parse_dsp_hw(HW.replace("=reset", "=soc_init!FAILED")).gpreg_stage == "soc_init!FAILED"


def test_the_hw_line_is_found_after_a_failed_ping():
    text = (
        "dsp status stage=never_booted magic=00000000\n"
        + HW.replace("Done\n", "")
        + ("Error: DSP did not answer (link open)\n")
    )
    assert parse_dsp_hw(text).power == 3


def test_a_reply_without_a_hw_line_is_an_error():
    with pytest.raises(DspLinkError):
        parse_dsp_hw("Error: trackCfg dsp ping | probe [bins] | status\n")


def test_the_driver_asks_for_the_hardware_state():
    radar = _Radar(HW)
    assert radar.dsp_hw().stc == 1
    assert radar.sent[0][0] == "trackCfg dsp hw"


def test_an_exception_status_carries_its_program_counter_and_flags():
    status = parse_dsp_status(
        "dsp status stage=exception err=0 beats=0 served=0 exc_pc=007e1234 exc_efr=00000002\n"
    )
    assert (status.stage, status.exc_pc, status.exc_efr) == ("exception", 0x007E1234, 0x2)


def test_a_status_without_an_exception_has_none():
    status = parse_dsp_status("dsp status stage=startup_first err=0 beats=0 served=0\n")
    assert status.stage == "startup_first" and status.exc_pc is None and status.exc_efr is None


GATHERED = PROBE.replace("\nDone\n", " dss_prep_us=35 gathered=1\nDone\n")


def test_a_probe_reports_the_gather_and_its_cost():
    probe = parse_dsp_probe(GATHERED)
    assert (probe.dss_prep_us, probe.gathered) == (35, True)
    assert probe.dss_total_us == 240 + 35


def test_an_older_probe_line_has_no_gather_fields():
    probe = parse_dsp_probe(PROBE)
    assert probe.dss_prep_us is None and probe.gathered is None
    assert probe.dss_total_us == 240


def test_the_speedup_counts_the_gather_against_the_dss():
    """The copy is part of what the DSS spends a frame: the speedup is the
    MSS against the DSS's preparing and scoring together."""
    probes = [
        parse_dsp_probe(GATHERED),
        parse_dsp_probe(GATHERED.replace("gathered=1", "gathered=0")),
    ]
    summary = summarize_probes(probes)
    assert summary.dss_total_us_median == 275
    assert summary.speedup == pytest.approx(1971 / 275)
    assert summary.gathered == 1


# --- the board acceptance run (scripts/hardware-test/iwr6843_dsp_probe.py) -------------

from openflight.iwr6843.dsp_link import (  # noqa: E402
    DetectCoreStatus,
    DetectTiming,
    TimingStat,
    evaluate_acceptance,
    parse_angle_queue,
    parse_detect_health,
)

STATS = "frames=900 wraps=0 active=1\ndetect dropped=0 stale=0 notice_dropped=0 shed=3 stale_read=0\nDone\n"
PERF = "perf frames=900\nangles queued=40 done=38 stale=1 failed=1 dropped=0 pending=0\nDone\n"


def core(**overrides) -> DetectCoreStatus:
    fields = dict(
        requested="verify",
        active="verify",
        latched=False,
        mss=0,
        dss=0,
        verify=400,
        ineligible=0,
        failures=0,
        fallbacks=0,
        streak=0,
        latches=0,
        mismatches=0,
        dss_inv_us_last=30,
        dss_inv_us_max=40,
        dss_score_us_last=300,
        dss_score_us_max=400,
        first_mismatch=None,
    )
    fields.update(overrides)
    return DetectCoreStatus(**fields)


def timing(mean: int = 1800, negative: int = 0) -> DetectTiming:
    return DetectTiming(
        frames=900,
        budget_us=3000,
        over_budget=0,
        depth_max=1,
        ring=24,
        margin_last_us=60000,
        margin_min_us=50000,
        margin_negative=negative,
        stats={"service": TimingStat(count=900, last=mean, min=100, mean=mean, max=2900)},
        timeline=(),
    )


def verdict(**kwargs):
    args = dict(
        verify=core(),
        dss=core(requested="dss", active="dss", verify=0, dss=400),
        timing=timing(),
        stats_text=STATS,
        perf_text=PERF,
    )
    args.update(kwargs)
    return {check.name: check for check in evaluate_acceptance(**args)}


def test_the_detect_health_line_is_parsed():
    health = parse_detect_health(STATS)
    assert (health.dropped, health.stale, health.stale_read, health.shed) == (0, 0, 0, 3)


def test_the_angle_queue_line_is_parsed():
    queue = parse_angle_queue(PERF)
    assert (queue.queued, queue.done, queue.stale, queue.failed, queue.dropped, queue.pending) == (
        40,
        38,
        1,
        1,
        0,
        0,
    )


def test_a_clean_run_passes_every_check():
    checks = verdict()
    assert all(check.passed for check in checks.values()), [
        (c.name, c.detail) for c in checks.values() if not c.passed
    ]
    assert set(checks) == {
        "verify_scored",
        "verify_mismatches",
        "verify_failures",
        "dss_scored",
        "dss_fallbacks",
        "dss_not_latched",
        "detect_dropped_stale",
        "keeps_up",
        "angles_not_dropped",
    }


@pytest.mark.parametrize(
    ("kwargs", "failing"),
    [
        ({"verify": core(verify=0)}, "verify_scored"),
        ({"verify": core(mismatches=2)}, "verify_mismatches"),
        ({"verify": core(failures=1)}, "verify_failures"),
        ({"dss": core(requested="dss", active="dss", verify=0, dss=0)}, "dss_scored"),
        (
            {"dss": core(requested="dss", active="dss", verify=0, dss=400, fallbacks=3)},
            "dss_fallbacks",
        ),
        ({"dss": core(requested="dss", active="mss", latched=True, dss=10)}, "dss_not_latched"),
        ({"stats_text": STATS.replace("stale_read=0", "stale_read=2")}, "detect_dropped_stale"),
        ({"timing": timing(mean=3200)}, "keeps_up"),
        ({"timing": timing(negative=1)}, "keeps_up"),
        (
            {"perf_text": PERF.replace("dropped=0 pending", "dropped=4 pending")},
            "angles_not_dropped",
        ),
    ],
)
def test_each_failure_fails_its_own_check_only(kwargs, failing):
    checks = verdict(**kwargs)
    assert not checks[failing].passed
    assert [n for n, c in checks.items() if not c.passed] == [failing]
    assert checks[failing].detail


def test_missing_board_lines_fail_rather_than_pass():
    """An older image without the health or angle lines is not a pass."""
    checks = verdict(stats_text="frames=1\nDone\n", perf_text="perf frames=0\nDone\n", timing=None)
    assert not checks["detect_dropped_stale"].passed
    assert not checks["angles_not_dropped"].passed
    assert not checks["keeps_up"].passed
