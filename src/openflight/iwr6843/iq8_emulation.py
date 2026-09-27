"""Exact IQ8 emulation of an IQ16 capture, with the firmware's own quantiser.

The board compresses to IQ8 three different ways (see ``l3_iq8.h``): a CPU
pack with a per-frame shift, an EDMA byte copy after a fixed HWA shift, and a
dump-time divide. Each rounds, clips or wraps differently, so an offline
"IQ8" made with a NumPy cast would measure a different radar than the one
in the enclosure. This module calls the same C (``l3_iq8_emulate_frame``,
compiled into the host library) on the int16 payload of an IQ16 ``.l3dump``
and writes the IQ8 dump the firmware would have produced, frame scales and
all. ``replay_dump`` then processes both and the difference is what IQ8
costs each measurement (``ab_compare``).

The HWA's output shift is hardware whose rounding this module cannot know
from source; ``Iq8Mode.hwa_rounding`` models it either way and
``hwa_rounding_from_bias`` reads the answer off a real IQ8 capture: a
truncating shift biases every component by minus half a scale step, a
rounding one does not.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import numpy as np

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.dump import (
    HEADER,
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    TIMED_FRAME_DESCRIPTOR,
    parse_dump,
    parse_header,
)

PATHS = ("cpu", "edma", "dump")
# HEADER is "<4sHHHBBHBBHH": magic, version, n_frames, chirps, n_tx, n_rx, n_samples, then sample_fmt.
_SAMPLE_FMT_OFFSET = 14
assert HEADER.size == 20


@dataclass(frozen=True)
class Iq8Mode:
    """Which firmware IQ8 path to reproduce, and the HWA shift it ran with."""

    path: str = "cpu"  # cpu, edma or dump
    hwa_shift: int | None = None  # None: the firmware default for the path
    hwa_rounding: bool = False
    sparse_stride: int | None = None  # cpu path: preview every Nth complex sample

    def to_c(self, lib: ctypes.CDLL) -> fw.Iq8Mode:
        if self.path not in fw.IQ8_PATH_NAMES:
            raise ValueError(f"path must be one of {PATHS}, got {self.path!r}")
        mode = fw.Iq8Mode()
        lib.l3_iq8_mode_defaults(ctypes.byref(mode), fw.IQ8_PATH_NAMES[self.path])
        if self.hwa_shift is not None:
            if not 0 <= self.hwa_shift <= 15:
                raise ValueError("hwa_shift must be 0..15")
            mode.hwaShift = self.hwa_shift
        mode.hwaRounding = 1 if self.hwa_rounding else 0
        if self.sparse_stride is not None:
            if self.sparse_stride < 0 or self.sparse_stride > 255:
                raise ValueError("sparse_stride must be 0..255")
            mode.sparseStride = self.sparse_stride
        return mode

    @property
    def label(self) -> str:
        return f"iq8:{self.path}"


@dataclass(frozen=True)
class FrameQuantisation:
    """What one frame lost: reconstruction error in IQ16 LSB and clipped components."""

    frame: int
    scale: int
    components: int
    clipped: int
    rms_error_lsb: float
    max_error_lsb: float

    @property
    def clipped_fraction(self) -> float:
        return self.clipped / self.components if self.components else 0.0


def _default_library() -> ctypes.CDLL:
    return fw.build_firmware_library()


def _iq16_frames(raw: bytes) -> tuple[dict, list[np.ndarray]]:
    """The dump's meta and its per-frame int16 payloads, exactly as stored."""
    meta = parse_header(raw)
    if meta["sample_fmt"] != SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED:
        raise ValueError(
            "IQ8 emulation needs a variable-width timed IQ16 dump (sample format 4); "
            f"got format {meta['sample_fmt']}"
        )
    meta, _cube = parse_dump(raw)
    offset = meta["header_nbytes"] + meta["frame_metadata_nbytes"]
    per_bin = meta["chirps_per_frame"] * meta["n_rx"] * 2
    frames: list[np.ndarray] = []
    for count in meta["range_bin_counts"]:
        words = per_bin * count
        frames.append(np.frombuffer(raw, dtype="<i2", offset=offset, count=words).copy())
        offset += words * 2
    return meta, frames


def emulate_frame(
    samples: np.ndarray, mode: Iq8Mode, *, lib: ctypes.CDLL | None = None
) -> tuple[np.ndarray, int, int]:
    """Quantise one frame's int16 components. Returns (int8 components, scale, clipped)."""
    lib = lib or _default_library()
    source = np.ascontiguousarray(samples, dtype=np.int16)
    out = np.zeros(source.size, dtype=np.int8)
    scale = ctypes.c_uint16(0)
    c_mode = mode.to_c(lib)
    clipped = lib.l3_iq8_emulate_frame(
        ctypes.byref(c_mode),
        source.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int8)),
        source.size,
        ctypes.byref(scale),
    )
    return out, int(scale.value), int(clipped)


def emulate_dump(raw: bytes, mode: Iq8Mode, *, lib: ctypes.CDLL | None = None) -> bytes:
    """The IQ8 dump the firmware would have stored for this IQ16 capture.

    Header, temperature report and frame descriptors are the original's;
    the sample format becomes 5 and a uint16 scale per frame precedes the
    int8 payload, as ``dump_format.h`` specifies.
    """
    lib = lib or _default_library()
    meta, frames = _iq16_frames(raw)
    header = bytearray(raw[: HEADER.size])
    header[_SAMPLE_FMT_OFFSET] = SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
    metadata = raw[HEADER.size : meta["header_nbytes"] + meta["frame_metadata_nbytes"]]
    scales = np.zeros(len(frames), dtype="<u2")
    chunks: list[bytes] = []
    for index, frame in enumerate(frames):
        out, scale, _clipped = emulate_frame(frame, mode, lib=lib)
        scales[index] = scale
        chunks.append(out.tobytes())
    return bytes(header) + metadata + scales.tobytes() + b"".join(chunks)


def quantisation_report(
    raw: bytes, mode: Iq8Mode, *, lib: ctypes.CDLL | None = None
) -> list[FrameQuantisation]:
    """Per frame: the reconstruction error of int8 x scale against the IQ16 truth."""
    lib = lib or _default_library()
    _meta, frames = _iq16_frames(raw)
    report: list[FrameQuantisation] = []
    for index, frame in enumerate(frames):
        out, scale, clipped = emulate_frame(frame, mode, lib=lib)
        error = out.astype(np.float64) * scale - frame.astype(np.float64)
        report.append(
            FrameQuantisation(
                frame=index,
                scale=scale,
                components=int(frame.size),
                clipped=clipped,
                rms_error_lsb=float(np.sqrt(np.mean(error**2))) if frame.size else 0.0,
                max_error_lsb=float(np.max(np.abs(error))) if frame.size else 0.0,
            )
        )
    return report


@dataclass(frozen=True)
class RoundingVerdict:
    rounding: str  # "round", "truncate" or "unclear"
    bias_lsb: float  # mean int8 component, in int8 steps
    components: int


def hwa_rounding_from_bias(
    int8_components: np.ndarray, *, min_components: int = 4096
) -> RoundingVerdict:
    """Read the HWA's shift rounding off real IQ8 samples.

    A range-FFT component of a static scene has zero mean over enough
    chirps. Truncation (floor) shifts every component down by half a step on
    average, so the int8 mean sits near -0.5; a rounding shift leaves it near
    0. The verdict is unclear between -0.35 and -0.15 or with too few samples.
    """
    values = np.asarray(int8_components, dtype=np.float64).reshape(-1)
    if values.size < min_components:
        return RoundingVerdict(
            "unclear", float(values.mean()) if values.size else 0.0, int(values.size)
        )
    bias = float(values.mean())
    if bias <= -0.35:
        return RoundingVerdict("truncate", bias, int(values.size))
    if bias >= -0.15:
        return RoundingVerdict("round", bias, int(values.size))
    return RoundingVerdict("unclear", bias, int(values.size))


def descriptor_count(raw: bytes) -> int:
    """Frames in a dump, from its header (for reports that never parse the payload)."""
    return parse_header(raw)["n_frames"]


_ = TIMED_FRAME_DESCRIPTOR  # the descriptor layout the emulated dump copies unchanged

__all__ = [
    "PATHS",
    "FrameQuantisation",
    "Iq8Mode",
    "RoundingVerdict",
    "descriptor_count",
    "emulate_dump",
    "emulate_frame",
    "hwa_rounding_from_bias",
    "quantisation_report",
]
