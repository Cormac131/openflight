"""Parse the MSS <-> DSS detect link diagnostics (firmware ``trackCfg dsp``).

The detect task is moving from the R4F (MSS) to the C674x (DSS). Phase 0
proves the link on the board: ``trackCfg dsp ping`` (the DSS answers over
the mailbox) and ``trackCfg dsp probe [bins]`` (the newest ring frame scored
on both cores with the same code, l3_bin_score.c, timed and compared bit
for bit).

Then the live detector: ``trackCfg detectCore [mss|dss|verify]`` chooses the
core that scores each frame's bins (firmware l3_detect_core.h) and prints
the ``detect core=...`` line; ``triggerLog perf`` and ``triggerLog timing``
print it with the detect timing (l3_timing.h), which keeps latency and
throughput apart.
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
    r"beats=(?P<beats>\d+) served=(?P<served>\d+)\s*$",
    re.MULTILINE,
)
_NEVER_BOOTED = re.compile(r"^dsp status stage=never_booted magic=[0-9a-f]{8}\s*$", re.MULTILINE)
_CORES = r"(?:mss|dss|verify)"
_DETECT = re.compile(
    rf"^detect core=(?P<requested>{_CORES}) active=(?P<active>{_CORES}) "
    r"latched=(?P<latched>[01]) mss=(?P<mss>\d+) dss=(?P<dss>\d+) verify=(?P<verify>\d+) "
    r"ineligible=(?P<ineligible>\d+) failures=(?P<failures>\d+) fallbacks=(?P<fallbacks>\d+) "
    r"streak=(?P<streak>\d+) latches=(?P<latches>\d+) mismatches=(?P<mismatches>\d+) "
    r"dss_inv_us=(?P<inv_last>\d+)/(?P<inv_max>\d+) "
    r"dss_score_us=(?P<score_last>\d+)/(?P<score_max>\d+)"
    r"(?: first_mismatch=(?P<mm_slot>\d+):(?P<mm_bin>\d+):(?P<mm_field>\d+))?\s*$",
    re.MULTILINE,
)
_TIMING_SUMMARY = re.compile(
    r"^timing frames=(?P<frames>\d+) budget_us=(?P<budget>\d+) "
    r"over_budget=(?P<over>\d+) depth_max=(?P<depth>\d+) ring=(?P<ring>\d+) "
    r"margin_last_us=(?P<margin_last>-?\d+|-) margin_min_us=(?P<margin_min>-?\d+|-) "
    r"margin_negative=(?P<negative>\d+)\s*$",
    re.MULTILINE,
)
_TIMING_NONE = re.compile(r"^timing frames=0 \(no frame decided yet\)\s*$", re.MULTILINE)
_TIMING_STAT = re.compile(
    r"^timing (?P<name>wait|score|service|latency|arrival) n=(?P<n>\d+) last=(?P<last>\d+) "
    r"min=(?P<min>\d+) mean=(?P<mean>\d+) max=(?P<max>\d+)\s*$",
    re.MULTILINE,
)
_TIMELINE = re.compile(
    r"^timeline slot=(?P<slot>\d+) epoch=(?P<epoch>\d+|post) "
    r"core=(?P<core>mss|dss|verify|fallback) wait_us=(?P<wait>\d+) score_us=(?P<score>\d+|-) "
    r"service_us=(?P<service>\d+) latency_us=(?P<latency>\d+) depth=(?P<depth>\d+) "
    r"flags=(?P<flags>[a-z|]+|-)\s*$",
    re.MULTILINE,
)
DETECT_CORES = ("mss", "dss", "verify")
# l3_dsp_ipc.h L3_DSP_FIELD_*: which field of a bin two cores disagreed on.
MISMATCH_FIELDS = ("energy", "peak", "loop0", "r1Re", "r1Im", "set")


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

    Stages in order: main, soc_init, task, mailbox_init, link_open (serving);
    never_booted when the DSS never wrote its status. ``beats`` rises while
    the link task waits for requests, so a rising count means BIOS runs.
    """

    stage: str
    failed: bool
    err: int
    beats: int
    served: int

    @property
    def booted(self) -> bool:
        return self.stage != "never_booted"


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


@dataclass(frozen=True)
class DetectMismatch:
    """The first frame ``verify`` found the cores disagreeing on."""

    slot: int
    bin: int  # local bin
    field: str  # MISMATCH_FIELDS


@dataclass(frozen=True)
class DetectCoreStatus:
    """The ``detect core=...`` line: which core scores, and how it went.

    ``requested`` is what was chosen; ``active`` is where frames go now,
    ``mss`` once three DSS failures in a row ``latched`` it. ``ineligible``
    frames (not an IQ16 ring frame, or the link busy with a CLI command) and
    ``fallbacks`` (the DSS failed the frame) were scored on the MSS.
    """

    requested: str
    active: str
    latched: bool
    mss: int
    dss: int
    verify: int
    ineligible: int
    failures: int
    fallbacks: int
    streak: int
    latches: int
    mismatches: int
    dss_inv_us_last: int
    dss_inv_us_max: int
    dss_score_us_last: int
    dss_score_us_max: int
    first_mismatch: DetectMismatch | None


@dataclass(frozen=True)
class TimingStat:
    """One ``timing <name>`` line, microseconds."""

    count: int
    last: int
    min: int
    mean: int
    max: int


@dataclass(frozen=True)
class TimelineEvent:
    """One ``timeline`` line: a frame's life on the detect task."""

    slot: int
    epoch: int | None  # None for a post-impact frame
    core: str  # mss, dss, verify, or fallback
    wait_us: int
    score_us: int | None  # None when the frame was not scored
    service_us: int
    latency_us: int
    depth: int
    flags: frozenset[str]


@dataclass(frozen=True)
class DetectTiming:
    """``triggerLog perf`` / ``triggerLog timing``: the two deadlines.

    Throughput: ``service`` must average below ``budget_us`` (the frame
    interval); ``over_budget`` counts frames whose service did not.
    Latency: a frame may take longer than the interval as long as its ring
    slot is not reused first; ``margin_min_us`` is the tightest that came,
    ``margin_negative`` the frames that ran past it (None before any).
    """

    frames: int
    budget_us: int
    over_budget: int
    depth_max: int
    ring: int
    margin_last_us: int | None
    margin_min_us: int | None
    margin_negative: int
    stats: dict[str, TimingStat]
    timeline: tuple[TimelineEvent, ...]

    @property
    def keeps_up(self) -> bool:
        """Mean service within the frame interval and no slot overrun."""
        service = self.stats.get("service")
        return (
            service is not None
            and service.count > 0
            and service.mean < self.budget_us
            and self.margin_negative == 0
        )


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


def parse_detect_core(text: str) -> DetectCoreStatus:
    """The ``detect core=...`` line of ``trackCfg detectCore``, ``triggerLog
    perf`` or ``triggerLog timing``."""
    _raise_on_error(text, "trackCfg detectCore")
    match = _DETECT.search(text)
    if match is None:
        raise DspLinkError(f"trackCfg detectCore: no detect line in {text!r}")
    fields = match.groupdict()
    mismatch = None
    if fields["mm_slot"] is not None:
        field = int(fields["mm_field"])
        mismatch = DetectMismatch(
            slot=int(fields["mm_slot"]),
            bin=int(fields["mm_bin"]),
            field=MISMATCH_FIELDS[field] if field < len(MISMATCH_FIELDS) else str(field),
        )
    return DetectCoreStatus(
        requested=fields["requested"],
        active=fields["active"],
        latched=fields["latched"] == "1",
        mss=int(fields["mss"]),
        dss=int(fields["dss"]),
        verify=int(fields["verify"]),
        ineligible=int(fields["ineligible"]),
        failures=int(fields["failures"]),
        fallbacks=int(fields["fallbacks"]),
        streak=int(fields["streak"]),
        latches=int(fields["latches"]),
        mismatches=int(fields["mismatches"]),
        dss_inv_us_last=int(fields["inv_last"]),
        dss_inv_us_max=int(fields["inv_max"]),
        dss_score_us_last=int(fields["score_last"]),
        dss_score_us_max=int(fields["score_max"]),
        first_mismatch=mismatch,
    )


def _margin(value: str) -> int | None:
    return None if value == "-" else int(value)


def parse_detect_timing(text: str) -> DetectTiming | None:
    """The timing lines of ``triggerLog perf`` or ``triggerLog timing``;
    None when no frame has been decided yet this session."""
    _raise_on_error(text, "triggerLog timing")
    if _TIMING_NONE.search(text):
        return None
    summary = _TIMING_SUMMARY.search(text)
    if summary is None:
        raise DspLinkError(f"triggerLog timing: no timing summary in {text!r}")
    stats = {
        match.group("name"): TimingStat(
            count=int(match.group("n")),
            last=int(match.group("last")),
            min=int(match.group("min")),
            mean=int(match.group("mean")),
            max=int(match.group("max")),
        )
        for match in _TIMING_STAT.finditer(text)
    }
    timeline = tuple(
        TimelineEvent(
            slot=int(match.group("slot")),
            epoch=None if match.group("epoch") == "post" else int(match.group("epoch")),
            core=match.group("core"),
            wait_us=int(match.group("wait")),
            score_us=None if match.group("score") == "-" else int(match.group("score")),
            service_us=int(match.group("service")),
            latency_us=int(match.group("latency")),
            depth=int(match.group("depth")),
            flags=frozenset()
            if match.group("flags") == "-"
            else frozenset(match.group("flags").split("|")),
        )
        for match in _TIMELINE.finditer(text)
    )
    return DetectTiming(
        frames=int(summary.group("frames")),
        budget_us=int(summary.group("budget")),
        over_budget=int(summary.group("over")),
        depth_max=int(summary.group("depth")),
        ring=int(summary.group("ring")),
        margin_last_us=_margin(summary.group("margin_last")),
        margin_min_us=_margin(summary.group("margin_min")),
        margin_negative=int(summary.group("negative")),
        stats=stats,
        timeline=timeline,
    )
