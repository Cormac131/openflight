"""Helper functions for driving l3_joint_search from Python tests.

All functions take a compiled library (from build_firmware_library) and
speak directly to the ctypes API.  They return Python dicts / lists so
tests can assert without touching ctypes internals.
"""
from __future__ import annotations

import ctypes

from openflight.iwr6843 import firmware_host as fw

BIN_M = 6.0 / 128
SPAN_MPS = 2 * fw.OBS_WAVELENGTH_M / (4 * 135e-6)
FRAME_US = 2000


def make_joint(lib, **overrides) -> fw.Joint:
    """Return an initialised Joint with optional config overrides."""
    cfg = fw.JointCfg()
    lib.l3_joint_cfg_defaults(ctypes.byref(cfg))
    cfg.binWidthM = BIN_M
    cfg.velocitySpanMps = SPAN_MPS
    for name, value in overrides.items():
        setattr(cfg, name, value)
    js = fw.Joint()
    lib.l3_joint_init(ctypes.byref(js), ctypes.byref(cfg))
    return js


def club_seed(range_bin: float, speed_mps: float, timestamp_us: int = 0) -> fw.JointKin:
    """Build a kinematic seed from the pre-impact club track."""
    kin = fw.JointKin()
    kin.rangeBin = range_bin
    kin.speedMps = speed_mps
    kin.timestampUs = timestamp_us
    return kin


def arm(lib, js: fw.Joint, seed: fw.JointKin | None, gate_us: int = 0) -> None:
    """Arm the joint search at the gate timestamp."""
    if seed is not None:
        lib.l3_joint_arm(ctypes.byref(js), ctypes.byref(seed), gate_us)
    else:
        lib.l3_joint_arm(ctypes.byref(js), None, gate_us)


def step(
    lib,
    js: fw.Joint,
    frame: int,
    timestamp_us: int,
    targets: list[fw.TargetObs],
) -> int:
    """Feed one frame and return 1 if the ball is confirmed."""
    n = len(targets)
    if n == 0:
        arr = (fw.TargetObs * 1)()
        n = 0
    else:
        arr = (fw.TargetObs * n)(*targets)
    return int(lib.l3_joint_update(ctypes.byref(js), frame, timestamp_us, arr, n))


def run(
    lib,
    js: fw.Joint,
    frames: list,  # list of (frame_num, timestamp_us, [TargetObs])
) -> int:
    """Run all frames; return confirmed after the last frame."""
    confirmed = 0
    for frame_num, ts, targets in frames:
        confirmed = step(lib, js, frame_num, ts, targets)
    return confirmed


def ball_path_bins(js: fw.Joint) -> list[float]:
    """Return the range bins of written ball points."""
    out = []
    for i in range(js.ballCount):
        out.append(js.ballPoints[i].rangeBin)
    return out


def club_path_bins(js: fw.Joint) -> list[float]:
    """Return the range bins of written club points."""
    out = []
    for i in range(js.clubCount):
        out.append(js.clubPoints[i].rangeBin)
    return out


def now_snapshot(lib, js: fw.Joint) -> dict:
    """Return the current best-explanation snapshot as a plain dict."""
    snap = lib.l3_joint_now(ctypes.byref(js))
    return {
        "club_bin": snap.clubBin,
        "ball_bin": snap.ballBin,
        "club_pred": snap.clubPredBin,
        "ball_pred": snap.ballPredBin,
        "best_score": snap.bestScore,
        "beam_size": snap.beamSize,
        "confirmed": bool(snap.ballConfirmed),
    }


def make_target(
    frame: int,
    range_bin: float,
    speed_mps: float,
    stat: float = 5000.0,
    timestamp_us: int | None = None,
) -> fw.TargetObs:
    """Convenience: build one TargetObs for the joint search tests."""
    t = fw.TargetObs()
    t.frame = frame
    t.timestampUs = timestamp_us if timestamp_us is not None else frame * FRAME_US
    t.peakBin = int(round(range_bin))
    t.rangeBin = range_bin
    t.energy = 4.0 * stat
    t.peak = stat
    t.stat = stat
    t.snr = stat / 100.0
    t.coherence = 0.9
    # Alias speed onto [-span/2, span/2)
    span = SPAN_MPS
    aliased = ((speed_mps + span / 2) % span) - span / 2
    t.dopplerAliasMps = aliased
    t.confidence = 0.9
    return t
