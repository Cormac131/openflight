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
    DspLinkError,
    DspProbe,
    parse_dsp_pong,
    parse_dsp_probe,
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
