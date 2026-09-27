"""Tests for the shared IQ8 quantiser (firmware/iwr6843/l3_iq8.c) and the host
emulation built on it (openflight.iwr6843.iq8_emulation).

The C arithmetic is what the board runs; these pin every rounding, clip and
wrap so an emulated IQ8 dump is the board's, and check the emulated dump
parses back to int8 x scale within one scale step of the IQ16 truth.
"""

from __future__ import annotations

import ctypes

import numpy as np
import pytest

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    pack_dump,
    parse_dump,
)
from openflight.iwr6843.iq8_emulation import (
    Iq8Mode,
    emulate_dump,
    emulate_frame,
    hwa_rounding_from_bias,
    quantisation_report,
)


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


# --- the primitives -----------------------------------------------------------


@pytest.mark.parametrize(
    ("sample", "shift", "rounding", "expected"),
    [
        (100, 0, 0, 100),
        (100, 2, 0, 25),
        (101, 2, 0, 25),  # truncation floors
        (-101, 2, 0, -26),  # ... toward minus infinity, as a two's complement shifter
        (-100, 2, 0, -25),
        (102, 2, 1, 26),  # rounding half up
        (-102, 2, 1, -25),
        (32767, 1, 1, 16384),
        (-32768, 1, 0, -16384),
        (-32768, 15, 0, -1),
        (32767, 15, 0, 0),
    ],
)
def test_hwa_scale_models_an_arithmetic_shift_with_optional_rounding(
    lib, sample, shift, rounding, expected
):
    assert lib.l3_iq8_hwa_scale(sample, shift, rounding) == expected


@pytest.mark.parametrize(
    ("sample", "shift", "expected", "clips"),
    [
        (5, 0, 5, 0),
        (127, 0, 127, 0),
        (128, 0, 127, 1),
        (-129, 0, -128, 1),
        (7, 1, 4, 0),  # 3.5 rounds away from zero
        (-7, 1, -4, 0),
        (6, 1, 3, 0),
        (5, 1, 3, 0),  # 2.5 -> 3
        (-5, 1, -3, 0),
        (1000, 3, 125, 0),
        (1020, 3, 127, 1),  # 127.5 rounds to 128 and clips
        (1024, 3, 127, 1),
        (-1024, 3, -128, 0),
        (-1028, 3, -128, 1),  # -128.5 -> -129 clips
    ],
)
def test_quantize_shift_rounds_half_away_and_counts_clips(lib, sample, shift, expected, clips):
    counter = ctypes.c_uint32(0)
    assert lib.l3_iq8_quantize_shift(sample, shift, ctypes.byref(counter)) == expected
    assert counter.value == clips
    assert lib.l3_iq8_quantize_shift(sample, shift, None) == expected, "NULL counter is fine"


def test_pack_shift_is_the_smallest_that_fits_the_largest_component(lib):
    def shift_for(values, stride=1):
        arr = (ctypes.c_int16 * len(values))(*values)
        return lib.l3_iq8_pack_shift(arr, len(values), stride)

    assert shift_for([0, 0, 0]) == 0
    assert shift_for([127, -127]) == 0
    assert shift_for([128]) == 1
    assert shift_for([-128]) == 1
    assert shift_for([255]) == 2  # shift 1 would round 255 to 128 and clip; the loop steps again
    assert shift_for([254]) == 1
    assert shift_for([32767]) == 9  # 32767 -> 16384 -> ... -> 128 -> 64: nine halvings
    assert shift_for([-32768]) == 9
    # The sampled preview sees both components of every eighth complex sample.
    values = [0] * 64
    values[16], values[17] = 4000, -4000  # complex sample 8: seen with stride 8
    values[3] = 30000  # complex sample 1: skipped with stride 8
    assert shift_for(values, stride=8) == 5
    assert shift_for(values, stride=1) == 8


@pytest.mark.parametrize(
    ("sample", "scale", "expected"),
    [
        (0, 1, 0),
        (127, 1, 127),
        (128, 1, 127),
        (-129, 1, -128),
        (250, 2, 125),
        (251, 2, 126),
        (-251, 2, -126),
        (3, 2, 2),
        (-3, 2, -2),
        (5, 0, 5),
    ],
)
def test_quantize_scale_matches_the_dump_path(lib, sample, scale, expected):
    assert lib.l3_iq8_quantize_scale(sample, scale) == expected


def test_dump_scale_and_max_abs_match_the_firmware_integer_arithmetic(lib):
    assert lib.l3_iq8_dump_scale(0) == 1
    assert lib.l3_iq8_dump_scale(127) == 1
    assert lib.l3_iq8_dump_scale(128) == 2
    assert lib.l3_iq8_dump_scale(254) == 2
    assert lib.l3_iq8_dump_scale(255) == 3
    assert lib.l3_iq8_dump_scale(32768) == 259
    values = (ctypes.c_int16 * 4)(3, -32768, 12, -5)
    assert lib.l3_iq8_max_abs(values, 4) == 32768
    assert lib.l3_iq8_max_abs(values, 0) == 1, "never below one, so the scale stays finite"


@pytest.mark.parametrize(
    ("sample", "expected"),
    [(0, 0), (127, 127), (128, -128), (-129, 127), (256, 0), (-1, -1), (300, 44)],
)
def test_low_byte_wraps_like_the_edma_byte_copy(lib, sample, expected):
    assert lib.l3_iq8_low_byte(sample) == expected


def test_mode_defaults_are_the_firmware_build_settings(lib):
    mode = fw.Iq8Mode()
    lib.l3_iq8_mode_defaults(ctypes.byref(mode), fw.IQ8_PATH_CPU)
    assert (mode.path, mode.hwaShift, mode.hwaRounding, mode.sparseStride) == (0, 4, 0, 8)
    lib.l3_iq8_mode_defaults(ctypes.byref(mode), fw.IQ8_PATH_EDMA)
    assert (mode.path, mode.hwaShift, mode.sparseStride) == (1, 7, 1), "iq8Scale 128"
    lib.l3_iq8_mode_defaults(ctypes.byref(mode), fw.IQ8_PATH_DUMP)
    assert (mode.path, mode.hwaShift) == (2, 0)


# --- whole frames -------------------------------------------------------------


def frame_values(seed: int = 1, size: int = 4 * 12 * 16 * 2, amplitude: int = 3000) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(-amplitude, amplitude + 1, size=size).astype(np.int16)


def test_cpu_path_emulation_is_hwa_shift_then_pack_shift_then_rounding(lib):
    values = frame_values()
    values[0] = 20000  # forces a pack shift beyond the HWA's 4
    out, scale, clipped = emulate_frame(values, Iq8Mode("cpu", sparse_stride=1), lib=lib)
    shifted = np.array([lib.l3_iq8_hwa_scale(int(v), 4, 0) for v in values], dtype=np.int16)
    pack_shift = lib.l3_iq8_pack_shift(
        (ctypes.c_int16 * shifted.size)(*shifted.tolist()), shifted.size, 1
    )
    assert scale == 16 << pack_shift
    expected = [lib.l3_iq8_quantize_shift(int(v), pack_shift, None) for v in shifted]
    assert out.tolist() == expected
    assert clipped == 0


def test_cpu_path_with_the_sparse_preview_can_clip_a_component_the_preview_missed(lib):
    values = np.zeros(4 * 8 * 2, dtype=np.int16)
    values[3] = 32000  # complex sample 1, invisible to a stride-8 preview
    out, scale, clipped = emulate_frame(values, Iq8Mode("cpu"), lib=lib)
    assert scale == 16, "the preview saw nothing above 127 after the HWA shift"
    assert out[3] == 127 and clipped == 1
    out_full, scale_full, clipped_full = emulate_frame(
        values, Iq8Mode("cpu", sparse_stride=1), lib=lib
    )
    assert scale_full == 16 << 4 and clipped_full == 0 and out_full[3] == 125


def test_edma_path_emulation_wraps_instead_of_clipping(lib):
    values = np.array([0, 127 << 7, 128 << 7, -(129 << 7), 3 << 7, (3 << 7) + 64], dtype=np.int16)
    out, scale, wrapped = emulate_frame(values, Iq8Mode("edma"), lib=lib)
    assert scale == 128
    assert out.tolist() == [0, 127, -128, 127, 3, 3], "truncation: 3.5 -> 3, and 128 wraps"
    assert wrapped == 2
    out_round, _scale, _ = emulate_frame(values, Iq8Mode("edma", hwa_rounding=True), lib=lib)
    assert out_round[5] == 4


def test_dump_path_emulation_scales_to_the_frame_maximum(lib):
    values = np.array([1270, -1270, 5, -5, 635, 636], dtype=np.int16)
    out, scale, clipped = emulate_frame(values, Iq8Mode("dump"), lib=lib)
    assert scale == 10
    assert out.tolist() == [127, -127, 1, -1, 64, 64]  # 63.5 -> 64, 63.6 -> 64
    assert clipped == 0


def test_mode_validation():
    with pytest.raises(ValueError, match="path"):
        Iq8Mode("fast").to_c(fw.build_firmware_library())
    with pytest.raises(ValueError, match="hwa_shift"):
        Iq8Mode("cpu", hwa_shift=16).to_c(fw.build_firmware_library())


# --- whole dumps --------------------------------------------------------------


def iq16_dump(
    seed: int = 3, frames: int = 4, counts=(53, 53, 47, 47), amplitude: float = 900.0
) -> bytes:
    rng = np.random.default_rng(seed)
    cpf, n_rx, n_samples = 36, 4, 128
    cube = np.zeros((frames, cpf, n_rx, n_samples), dtype=np.complex128)
    for frame, count in enumerate(counts):
        cube[frame, ..., :count] = rng.normal(0.0, amplitude, (cpf, n_rx, count)) + 1j * rng.normal(
            0.0, amplitude, (cpf, n_rx, count)
        )
    cube[1, 3, 2, 10] = 30000 + 0j  # one strong return
    return pack_dump(
        np.round(cube),
        n_tx=3,
        version=7,
        frame_period_us=3000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=(20, 20, 32, 32),
        range_bin_counts=counts,
        frame_time_offsets_us=(0, 3000, 6000, 9000),
        temperature_report={k: 30 for k in _temp_keys()},
    )


def _temp_keys():
    from openflight.iwr6843.dump import TEMP_REPORT_KEYS

    return TEMP_REPORT_KEYS


@pytest.mark.parametrize("path", ["cpu", "edma", "dump"])
def test_emulated_dump_parses_as_iq8_and_reconstructs_within_one_scale_step(lib, path):
    raw = iq16_dump()
    meta16, cube16 = parse_dump(raw)
    emulated = emulate_dump(raw, Iq8Mode(path, hwa_shift=6 if path == "edma" else None), lib=lib)
    meta8, cube8 = parse_dump(emulated)
    assert meta8["sample_fmt"] == SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
    assert meta8["version"] == 7 and meta8["temperature_report"] == meta16["temperature_report"]
    assert meta8["range_bin_starts"] == meta16["range_bin_starts"]
    assert meta8["range_bin_counts"] == meta16["range_bin_counts"]
    assert meta8["frame_time_offsets_us"] == meta16["frame_time_offsets_us"]
    assert len(meta8["iq8_scales"]) == 4
    for frame, count in enumerate(meta16["range_bin_counts"]):
        truth = cube16[frame, ..., :count]
        got = cube8[frame, ..., :count]
        scale = meta8["iq8_scales"][frame]
        # Everything that did not clip or wrap is within one scale step.
        clipped = (np.abs(truth.real) >= 127 * scale) | (np.abs(truth.imag) >= 127 * scale)
        error = np.abs(got - truth)
        assert np.all(error[~clipped] <= scale * np.sqrt(2) + 1e-9), (path, frame)
    assert len(emulated) < len(raw)


def test_quantisation_report_shows_the_error_growing_with_the_scale(lib):
    raw = iq16_dump()
    report = quantisation_report(raw, Iq8Mode("cpu", sparse_stride=1), lib=lib)
    assert [r.frame for r in report] == [0, 1, 2, 3]
    assert report[1].scale > report[0].scale, "the strong return forces a bigger shift on frame 1"
    assert report[1].rms_error_lsb > report[0].rms_error_lsb
    assert all(r.clipped == 0 for r in report)
    # Half a pack step from the rounding plus up to 15 LSB from the HWA's truncating shift.
    assert all(r.max_error_lsb <= r.scale / 2 + 15 + 1e-9 for r in report)
    edma = quantisation_report(raw, Iq8Mode("edma", hwa_shift=2), lib=lib)
    assert edma[1].clipped > 0, "the 30000 return wraps at shift 2"
    assert edma[1].clipped_fraction > 0


def test_emulation_refuses_dumps_that_are_not_iq16_range_snapshots(lib):
    raw = iq16_dump()
    emulated = emulate_dump(raw, Iq8Mode("cpu"), lib=lib)
    with pytest.raises(ValueError, match="format 4"):
        emulate_dump(emulated, Iq8Mode("cpu"), lib=lib)


def test_hwa_rounding_verdict_reads_the_truncation_bias(lib):
    rng = np.random.default_rng(5)
    samples = rng.normal(0.0, 1500.0, 40000)  # well inside int8 after the shift: no wraps
    raw16 = (samples - samples.mean()).astype(np.int16)  # a static scene: zero mean
    truncated, _, _ = emulate_frame(raw16, Iq8Mode("edma", hwa_shift=6), lib=lib)
    rounded, _, _ = emulate_frame(raw16, Iq8Mode("edma", hwa_shift=6, hwa_rounding=True), lib=lib)
    assert hwa_rounding_from_bias(truncated).rounding == "truncate"
    assert hwa_rounding_from_bias(rounded).rounding == "round"
    assert hwa_rounding_from_bias(truncated[:100]).rounding == "unclear"
    assert hwa_rounding_from_bias(np.zeros(0, dtype=np.int8)).components == 0
