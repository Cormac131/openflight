"""Host build of the pure-C firmware decision modules, driven through ctypes.

``firmware/iwr6843/l3_observation.c`` (per-bin residual observations to
ranked targets), ``l3_trigger.c`` (the self-trigger) and ``l3_club_track.c``
(the persistent club trajectory) have no hardware dependencies, so the same
sources the R4F runs are compiled with the host C compiler and called from
Python. The ctypes structures here mirror the C headers field for field; a
layout change on one side without the other reads garbage (or crashes), which
is exactly what the ctypes test suites catch.

The replay harness (``firmware_replay``) and the firmware test suites share
this one binding so a signature or layout change is made in one place.
"""

# ctypes mirrors are data, not behaviour.
# pylint: disable=too-few-public-methods
from __future__ import annotations

import ctypes
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

FIRMWARE_DIR = Path(__file__).resolve().parents[3] / "firmware" / "iwr6843"
HOST_SOURCES = ("l3_observation.c", "l3_trigger.c", "l3_club_track.c")

# l3_observation.h
OBS_MAX_BINS = 64
OBS_MAX_TARGETS = 8
OBS_WAVELENGTH_M = 0.00484
OBS_FLOOR_MIN = 1.0
STAT_ENERGY, STAT_PEAK = 0, 1
STAT_NAMES = {"energy": STAT_ENERGY, "peak": STAT_PEAK}

# l3_trigger.h
TRIG_MAX_BINS = 64
TRIG_LOG_DEPTH = 128
TRIG_TRACE_DEPTH = 64
TRIG_COUNT_TOTAL = 12
TRIG_NO_BIN = 0xFF
TRIG_STATE_IDLE, TRIG_STATE_TRACKING, TRIG_STATE_FIRED = 0, 1, 2
TRIG_STATE_NAMES = ("idle", "tracking", "fired")

# l3_club_track.h
TRACK_POINTS = 32
TRACK_WHY_NAMES = ("none", "acquired", "associated", "coasted", "dropped", "idle")


class BinObs(ctypes.Structure):
    """One range bin of one frame: ``l3_bin_obs_t``."""

    _fields_ = [
        ("energy", ctypes.c_float),
        ("peak", ctypes.c_float),
        ("loop0", ctypes.c_float),
        ("r1Re", ctypes.c_float),
        ("r1Im", ctypes.c_float),
    ]


class ObsParams(ctypes.Structure):
    """``l3_obs_params_t``."""

    _fields_ = [("stat", ctypes.c_uint32), ("snr", ctypes.c_float), ("loopPeriodS", ctypes.c_float)]


class TargetObs(ctypes.Structure):
    """``l3_target_obs_t``: one extracted target, global sub-bin range."""

    _fields_ = [
        ("frame", ctypes.c_uint32),
        ("timestampUs", ctypes.c_uint32),
        ("peakBin", ctypes.c_uint8),
        ("rangeBin", ctypes.c_float),
        ("energy", ctypes.c_float),
        ("peak", ctypes.c_float),
        ("loop0", ctypes.c_float),
        ("stat", ctypes.c_float),
        ("snr", ctypes.c_float),
        ("coherence", ctypes.c_float),
        ("r1Re", ctypes.c_float),
        ("r1Im", ctypes.c_float),
        ("dopplerPhaseRad", ctypes.c_float),
        ("dopplerAliasMps", ctypes.c_float),
        ("azimuthRad", ctypes.c_float),
        ("elevationRad", ctypes.c_float),
        ("anglesValid", ctypes.c_uint8),
        ("confidence", ctypes.c_float),
    ]


class TrigCfg(ctypes.Structure):
    """``l3_trig_cfg_t``."""

    _fields_ = [
        ("teeBin", ctypes.c_uint32),
        ("snr", ctypes.c_float),
        ("trackFrames", ctypes.c_uint32),
        ("approachBins", ctypes.c_uint32),
        ("gateBins", ctypes.c_uint32),
        ("minCoherence", ctypes.c_float),
        ("minStepBins", ctypes.c_float),
        ("stat", ctypes.c_uint32),
        ("minSpeedMps", ctypes.c_float),
    ]


class TrigTrace(ctypes.Structure):
    """``l3_trig_trace_t``: the region's strongest bin of one frame."""

    _fields_ = [
        ("frame", ctypes.c_uint32),
        ("gap", ctypes.c_uint16),
        ("bin", ctypes.c_uint8),
        ("state", ctypes.c_uint8),
        ("energy", ctypes.c_float),
        ("peak", ctypes.c_float),
        ("loop0", ctypes.c_float),
        ("floor", ctypes.c_float),
        ("threshold", ctypes.c_float),
        ("coherencePct", ctypes.c_uint8),
        ("dest", ctypes.c_uint8),
    ]


class TrigRecord(ctypes.Structure):
    """``l3_trig_record_t``: one logged detector frame."""

    _fields_ = [
        ("frame", ctypes.c_uint32),
        ("gap", ctypes.c_uint16),
        ("state", ctypes.c_uint8),
        ("why", ctypes.c_uint8),
        ("bin", ctypes.c_uint8),
        ("age", ctypes.c_uint8),
        ("velocityCms", ctypes.c_int16),
        ("energy", ctypes.c_float),
        ("peak", ctypes.c_float),
        ("floor", ctypes.c_float),
        ("coherencePct", ctypes.c_uint8),
        ("dest", ctypes.c_uint8),
    ]


class Trig(ctypes.Structure):
    """``l3_trig_t``: the self-trigger detector."""

    _fields_ = [
        ("cfg", TrigCfg),
        ("state", ctypes.c_uint8),
        ("floor", ctypes.c_float),
        ("loopPeriodS", ctypes.c_float),
        ("trackBin", ctypes.c_uint8),
        ("trackStartBin", ctypes.c_uint8),
        ("trackAge", ctypes.c_uint8),
        ("trackMisses", ctypes.c_uint8),
        ("trackStartFrame", ctypes.c_uint32),
        ("counters", ctypes.c_uint32 * TRIG_COUNT_TOTAL),
        ("quietSince", ctypes.c_uint32),
        ("logNext", ctypes.c_uint32),
        ("logCount", ctypes.c_uint32),
        ("log", TrigRecord * TRIG_LOG_DEPTH),
        ("traceQuiet", ctypes.c_uint32),
        ("traceNext", ctypes.c_uint32),
        ("traceCount", ctypes.c_uint32),
        ("trace", TrigTrace * TRIG_TRACE_DEPTH),
        ("maxFirstBin", ctypes.c_uint32),
        ("maxBins", ctypes.c_uint32),
        ("maxStat", ctypes.c_float * TRIG_MAX_BINS),
        ("maxFrame", ctypes.c_uint32 * TRIG_MAX_BINS),
    ]


class TrackCfg(ctypes.Structure):
    """``l3_track_cfg_t``."""

    _fields_ = [
        ("binWidthM", ctypes.c_float),
        ("gateBins", ctypes.c_float),
        ("maxMisses", ctypes.c_uint32),
        ("minConfidence", ctypes.c_float),
        ("weightRange", ctypes.c_float),
        ("weightVelocity", ctypes.c_float),
        ("weightQuality", ctypes.c_float),
        ("velocitySpanMps", ctypes.c_float),
    ]


class TrackPoint(ctypes.Structure):
    """``l3_track_point_t``: one held observation of the clubhead."""

    _fields_ = [
        ("frame", ctypes.c_uint32),
        ("timestampUs", ctypes.c_uint32),
        ("rangeBin", ctypes.c_float),
        ("rangeM", ctypes.c_float),
        ("radialVelocityMps", ctypes.c_float),
        ("dopplerAliasMps", ctypes.c_float),
        ("azimuthRad", ctypes.c_float),
        ("elevationRad", ctypes.c_float),
        ("anglesValid", ctypes.c_uint8),
        ("energy", ctypes.c_float),
        ("coherence", ctypes.c_float),
        ("confidence", ctypes.c_float),
    ]


class ClubTrack(ctypes.Structure):
    """``l3_club_track_t``."""

    _fields_ = [
        ("cfg", TrackCfg),
        ("active", ctypes.c_uint8),
        ("why", ctypes.c_uint8),
        ("next", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("total", ctypes.c_uint32),
        ("misses", ctypes.c_uint32),
        ("lastFrame", ctypes.c_uint32),
        ("lastBin", ctypes.c_float),
        ("velocityBinsPerFrame", ctypes.c_float),
        ("predictedBin", ctypes.c_float),
        ("points", TrackPoint * TRACK_POINTS),
        ("counters", ctypes.c_uint32 * len(TRACK_WHY_NAMES)),
    ]


_U32 = ctypes.c_uint32
_F32 = ctypes.c_float
_P = ctypes.POINTER
_TEXT = (ctypes.c_char_p, _U32)

# name -> (argtypes, restype); None restype is the C void.
_SIGNATURES: dict[str, tuple[list, object]] = {
    # l3_observation.h
    "l3_obs_stat": ([_U32, _P(BinObs)], _F32),
    "l3_obs_median": ([_U32, _P(BinObs), _U32], _F32),
    "l3_obs_floor_update": ([_P(_F32), _U32, _P(BinObs), _U32, _U32], None),
    "l3_obs_velocity": ([_F32, _F32, _F32], _F32),
    "l3_obs_extract": (
        [_P(ObsParams), _U32, _U32, _U32, _P(BinObs), _U32, _F32, _P(TargetObs), _U32],
        _U32,
    ),
    "l3_obs_format_target": ([_P(TargetObs), *_TEXT], ctypes.c_int32),
    # l3_trigger.h
    "l3_trig_cfg_defaults": ([_P(TrigCfg)], None),
    "l3_trig_cfg_check": ([_P(TrigCfg)], ctypes.c_int32),
    "l3_trig_init": ([_P(Trig), _P(TrigCfg), _F32], None),
    "l3_trig_rearm": ([_P(Trig)], None),
    "l3_trig_region": ([_P(TrigCfg), _U32, _U32, _U32, _P(_U32), _P(_U32)], ctypes.c_int32),
    "l3_trig_update": ([_P(Trig), _U32, _U32, _U32, _P(BinObs), _U32], ctypes.c_int32),
    "l3_trig_log_count": ([_P(Trig)], _U32),
    "l3_trig_log_get": ([_P(Trig), _U32, _P(TrigRecord)], ctypes.c_int32),
    "l3_trig_format_summary": ([_P(Trig), *_TEXT], ctypes.c_int32),
    "l3_trig_format_config": ([_P(Trig), *_TEXT], ctypes.c_int32),
    "l3_trig_format_record": ([_P(TrigRecord), *_TEXT], ctypes.c_int32),
    "l3_trig_why_name": ([ctypes.c_uint8], ctypes.c_char_p),
    "l3_trig_trace_clear": ([_P(Trig)], None),
    "l3_trig_trace_count": ([_P(Trig)], _U32),
    "l3_trig_trace_get": ([_P(Trig), _U32, _P(TrigTrace)], ctypes.c_int32),
    "l3_trig_format_trace_header": ([_P(Trig), *_TEXT], ctypes.c_int32),
    "l3_trig_format_trace": ([_P(TrigTrace), *_TEXT], ctypes.c_int32),
    "l3_trig_format_maxhold": ([_P(Trig), _U32, _U32, *_TEXT], ctypes.c_int32),
    # l3_club_track.h
    "l3_track_cfg_defaults": ([_P(TrackCfg)], None),
    "l3_track_init": ([_P(ClubTrack), _P(TrackCfg)], None),
    "l3_track_reset": ([_P(ClubTrack)], None),
    "l3_track_update": ([_P(ClubTrack), _P(TargetObs), _U32, _U32, _U32], ctypes.c_int32),
    "l3_track_point": ([_P(ClubTrack), _U32, _P(TrackPoint)], ctypes.c_int32),
    "l3_track_fit": ([_P(ClubTrack), _U32, _P(_F32), _P(_F32)], _U32),
    "l3_track_speed_mps": ([_P(ClubTrack), _U32], _F32),
    "l3_track_why_name": ([ctypes.c_uint8], ctypes.c_char_p),
    "l3_track_format_status": ([_P(ClubTrack), _U32, *_TEXT], ctypes.c_int32),
    "l3_track_format_point": ([_P(TrackPoint), _U32, *_TEXT], ctypes.c_int32),
}


def host_compiler() -> str | None:
    """The first host C compiler on PATH, or None."""
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _source_digest(sources: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for source in sources:
        digest.update(source.read_bytes())
        for header in sorted(source.parent.glob("l3_*.h")):
            digest.update(header.read_bytes())
    return digest.hexdigest()[:16]


def build_firmware_library(
    out_dir: str | Path | None = None, *, firmware_dir: Path = FIRMWARE_DIR
) -> ctypes.CDLL:
    """Compile the host-testable firmware modules into one shared library and bind it.

    Without ``out_dir`` the build lands in a temp directory named after the
    sources' digest, so repeated replays skip the compile. Raises RuntimeError
    without a compiler; the C compile's own errors propagate.
    """
    compiler = host_compiler()
    if compiler is None:
        raise RuntimeError("no host C compiler (cc, gcc or clang) for the firmware modules")
    sources = tuple(firmware_dir / name for name in HOST_SOURCES)
    if out_dir is None:
        out_dir = Path(tempfile.gettempdir()) / f"openflight-l3-host-{_source_digest(sources)}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    library_path = out_dir / "l3_host.so"
    if not library_path.exists():
        build = out_dir / "l3_host.build.so"
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
                str(build),
                *(str(source) for source in sources),
                "-lm",
            ],
            check=True,
            cwd=firmware_dir,
        )
        build.replace(library_path)  # atomic against a parallel build
    library = ctypes.CDLL(str(library_path))
    for name, (argtypes, restype) in _SIGNATURES.items():
        function = getattr(library, name)
        function.argtypes = argtypes
        function.restype = restype
    return library


def c_text(function, *args, cap: int = 200) -> str:
    """Call a firmware ``format`` function into a fresh buffer and return the text."""
    buffer = ctypes.create_string_buffer(cap)
    function(*args, buffer, cap)
    return buffer.value.decode("ascii")


__all__ = [
    "FIRMWARE_DIR",
    "HOST_SOURCES",
    "OBS_FLOOR_MIN",
    "OBS_MAX_BINS",
    "OBS_MAX_TARGETS",
    "OBS_WAVELENGTH_M",
    "STAT_ENERGY",
    "STAT_NAMES",
    "STAT_PEAK",
    "TRACK_POINTS",
    "TRACK_WHY_NAMES",
    "TRIG_COUNT_TOTAL",
    "TRIG_LOG_DEPTH",
    "TRIG_MAX_BINS",
    "TRIG_NO_BIN",
    "TRIG_STATE_FIRED",
    "TRIG_STATE_IDLE",
    "TRIG_STATE_NAMES",
    "TRIG_STATE_TRACKING",
    "TRIG_TRACE_DEPTH",
    "BinObs",
    "ClubTrack",
    "ObsParams",
    "TargetObs",
    "TrackCfg",
    "TrackPoint",
    "Trig",
    "TrigCfg",
    "TrigRecord",
    "TrigTrace",
    "build_firmware_library",
    "c_text",
    "host_compiler",
]
