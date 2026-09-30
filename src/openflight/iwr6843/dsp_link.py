"""Parse the MSS <-> DSS detect link diagnostics (firmware ``trackCfg dsp``).

The detect task is moving from the R4F (MSS) to the C674x (DSS). Phase 0
proves the link on the board: ``trackCfg dsp ping`` (the DSS answers over
the mailbox) and ``trackCfg dsp probe [bins]`` (the newest ring frame scored
on both cores with the same code, l3_bin_score.c, timed and compared bit
for bit).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

_PONG = re.compile(r"^dsp pong seq=(\d+) us=(\d+)\s*$", re.MULTILINE)
_PROBE = re.compile(
    r"^dsp probe slot=(?P<slot>\d+) bins=(?P<bins>\d+) mss_us=(?P<mss_us>\d+) "
    r"dss_us=(?P<dss_us>\d+) dss_cycles=(?P<dss_cycles>\d+) match=(?P<match>[01]) "
    r"status=(?P<status>\d+) mss_energy=(?P<mss_energy>[0-9a-f]{8}) "
    r"dss_energy=(?P<dss_energy>[0-9a-f]{8})\s*$",
    re.MULTILINE,
)
_ERROR = re.compile(r"^Error:\s*(.*)$", re.MULTILINE)
_STATUS = re.compile(
    r"^dsp status stage=(?P<stage>\w+)(?P<failed> FAILED)? err=(?P<err>-?\d+) "
    r"beats=(?P<beats>\d+) served=(?P<served>\d+)"
    r"(?: exc_pc=(?P<exc_pc>[0-9a-f]{8}) exc_efr=(?P<exc_efr>[0-9a-f]{8}))?\s*$",
    re.MULTILINE,
)
_HW = re.compile(
    r"^dsp hw gpreg_stage=(?P<stage>\S+) halt=(?P<halt>\d+) power=(?P<power>\d+) "
    r"stc=(?P<stc>\d+) esm=(?P<esm>[0-9a-f]{8}(?:,[0-9a-f]{8}){3}) "
    r"hsram=(?P<hsram>ok|BAD)\s*$",
    re.MULTILINE,
)
_NEVER_BOOTED = re.compile(r"^dsp status stage=never_booted magic=[0-9a-f]{8}\s*$", re.MULTILINE)


class DspLinkError(RuntimeError):
    """The firmware refused the command, the DSS did not answer, or the
    image has no detect link (an older image refuses ``trackCfg dsp``)."""


@dataclass(frozen=True)
class DspProbe:
    """One ``trackCfg dsp probe``: the same frame scored on both cores."""

    slot: int
    bins: int
    mss_us: int
    dss_us: int
    dss_cycles: int
    match: bool  # every sum bit for bit equal
    status: int  # the DSS's L3_DSP_* status
    mss_energy: str  # the energy sum's float bits, hex
    dss_energy: str


@dataclass(frozen=True)
class DspStatus:
    """``trackCfg dsp status``: the boot stage the DSS reached (HS-RAM).

    Stages in order: reset, startup_first, startup_last (the xdc/BIOS
    module startups run between those two), main, soc_init, task,
    mailbox_init, link_open (serving); exception when the DSS took one
    (``exc_pc`` the interrupted program counter, ``exc_efr`` the flags);
    never_booted when the DSS never wrote its status. ``beats`` rises while
    the link task waits for requests, so a rising count means BIOS runs.
    """

    stage: str
    failed: bool
    err: int
    beats: int
    served: int
    exc_pc: int | None = None
    exc_efr: int | None = None

    @property
    def booted(self) -> bool:
        return self.stage != "never_booted"


@dataclass(frozen=True)
class DspHw:
    """``trackCfg dsp hw``: the DSS as the MSS reads it without the DSS's help.

    ``gpreg_stage`` is the stage the DSS mirrored into DSSGPREG0 (``reset``
    is its earliest, before C init and BIOS), or ``none(XXXXXXXX)`` when it
    never wrote it; ``esm`` the ESMSR1..3 and ESMSR4 error flags.
    """

    gpreg_stage: str
    halt: int
    power: int
    stc: int
    esm: tuple[int, int, int, int]
    hsram_ok: bool

    @property
    def halted(self) -> bool:
        return self.halt == 1

    @property
    def powered(self) -> bool:
        return self.power == 3


@dataclass(frozen=True)
class DspProbeSummary:
    count: int
    mismatches: int
    mss_us_median: float
    dss_us_median: float

    @property
    def speedup(self) -> float:
        """How many times faster the DSS scored the same bins."""
        return self.mss_us_median / self.dss_us_median if self.dss_us_median else float("inf")


def _raise_on_error(text: str, what: str) -> None:
    error = _ERROR.search(text)
    if error:
        raise DspLinkError(f"{what}: {error.group(1).strip()}")


def parse_dsp_pong(text: str) -> int:
    """The ping's round trip in microseconds."""
    _raise_on_error(text, "trackCfg dsp ping")
    match = _PONG.search(text)
    if match is None:
        raise DspLinkError(f"trackCfg dsp ping: no pong in {text!r}")
    return int(match.group(2))


def parse_dsp_probe(text: str) -> DspProbe:
    """A probe line. A mismatch is data (``match`` False), not an error."""
    _raise_on_error(text, "trackCfg dsp probe")
    match = _PROBE.search(text)
    if match is None:
        raise DspLinkError(f"trackCfg dsp probe: no probe line in {text!r}")
    fields = match.groupdict()
    return DspProbe(
        slot=int(fields["slot"]),
        bins=int(fields["bins"]),
        mss_us=int(fields["mss_us"]),
        dss_us=int(fields["dss_us"]),
        dss_cycles=int(fields["dss_cycles"]),
        match=fields["match"] == "1",
        status=int(fields["status"]),
        mss_energy=fields["mss_energy"],
        dss_energy=fields["dss_energy"],
    )


def parse_dsp_status(text: str) -> DspStatus:
    """The status line; also found in a failed ping's or probe's reply,
    which prints it before its Error line."""
    if _NEVER_BOOTED.search(text):
        return DspStatus(stage="never_booted", failed=False, err=0, beats=0, served=0)
    match = _STATUS.search(text)
    if match is None:
        _raise_on_error(text, "trackCfg dsp status")
        raise DspLinkError(f"trackCfg dsp status: no status line in {text!r}")
    return DspStatus(
        stage=match.group("stage"),
        failed=match.group("failed") is not None,
        err=int(match.group("err")),
        beats=int(match.group("beats")),
        served=int(match.group("served")),
        exc_pc=None if match.group("exc_pc") is None else int(match.group("exc_pc"), 16),
        exc_efr=None if match.group("exc_efr") is None else int(match.group("exc_efr"), 16),
    )


def parse_dsp_hw(text: str) -> DspHw:
    """The hardware line; also found in a failed ping's or probe's reply."""
    match = _HW.search(text)
    if match is None:
        _raise_on_error(text, "trackCfg dsp hw")
        raise DspLinkError(f"trackCfg dsp hw: no hw line in {text!r}")
    esm = tuple(int(word, 16) for word in match.group("esm").split(","))
    return DspHw(
        gpreg_stage=match.group("stage"),
        halt=int(match.group("halt")),
        power=int(match.group("power")),
        stc=int(match.group("stc")),
        esm=(esm[0], esm[1], esm[2], esm[3]),
        hsram_ok=match.group("hsram") == "ok",
    )


def summarize_probes(probes: list[DspProbe]) -> DspProbeSummary:
    """Median timings over repeated probes, and how many disagreed."""
    if not probes:
        raise ValueError("no probes to summarize")
    return DspProbeSummary(
        count=len(probes),
        mismatches=sum(not probe.match for probe in probes),
        mss_us_median=statistics.median(probe.mss_us for probe in probes),
        dss_us_median=statistics.median(probe.dss_us for probe in probes),
    )
