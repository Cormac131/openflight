"""Tests for the bench azimuth check, src/openflight/iwr6843/azimuth_check.py.

Captures are synthesised from the firmware's own geometry: a reflector at a
taped golf-frame position is turned into radar azimuth and elevation by
l3_frames_golf_to_radar, then into channels with the elevation array's
steering, TX1 on the vertical pair's midpoint, and an azimuth zero offset
the fit must find. Static clutter stronger than the reflector sits in both
the scene and the empty capture, as the mat, floor and walls do.
"""

from __future__ import annotations

import ctypes
import json
import math
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843 import azimuth_check as ac, firmware_host as fw, firmware_replay as fr
from openflight.iwr6843.board_calibration import BoardCalibration
from openflight.iwr6843.music import steer
from openflight.iwr6843.tracking import CHIRP_PERIOD_S, RANGE_SPAN_M

NTX, NRX, LOOPS, FRAMES = 3, 4, 4, 6
FIRST_BIN, BINS = 10, 53
BIN_M = RANGE_SPAN_M / fr.DEFAULT_FFT_SIZE
FORWARD_M = 2.0
REFERENCE = "config/iwr6843_calibration_reference.json"
BOARD = BoardCalibration(
    pitch_deg=10.0,
    yaw_deg=0.0,
    roll_deg=0.0,
    az_offset_rad=0.0,
    el_offset_deg=0.0,
    range_bias_m=0.0,
    elem_phase_rad=(0.0,) * 8,
    elem_gain=(1.0,) * 8,
    radar_height_m=0.15,
)


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def radar_angles(lib, lateral_m: float, height_m: float, forward_m: float = FORWARD_M):
    """(range, azimuth, elevation) of a golf-frame point as BOARD sees it."""
    cal = fr._radar_cal(lib, fr.ReplayConfig(tee_bin=40, **BOARD.replay_overrides()))  # pylint: disable=protected-access
    golf = fw.Vec3(forward_m, lateral_m, height_m - BOARD.radar_height_m)
    radar = fw.Vec3()
    lib.l3_frames_golf_to_radar(ctypes.byref(cal), ctypes.byref(golf), ctypes.byref(radar))
    sph = fw.Spherical()
    lib.l3_frames_to_spherical(ctypes.byref(radar), ctypes.byref(sph))
    return sph.rangeM, sph.azimuthRad, sph.elevationRad


def reflector_channels(az: float, el: float, amp: float, offset_rad: float) -> np.ndarray:
    """[tx, rx] channels of a point target: the elevation array reversed into
    [txA.rx, txB.rx], TX1 on their midpoint carrying -pi sin(az) plus the
    board's unmeasured zero offset."""
    physical = amp * steer(el, 2 * NRX)
    logical = physical[::-1]
    out = np.zeros((NTX, NRX), dtype=complex)
    for rx in range(NRX):
        out[0, rx] = logical[rx]
        out[2, rx] = logical[NRX + rx]
        midpoint = (2 * NRX - 1 - rx) - NRX / 2
        out[1, rx] = amp * np.exp(
            1j * (math.pi * math.sin(el) * midpoint - math.pi * math.sin(az) + offset_rad)
        )
    return out


def clutter(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 40.0 * (rng.normal(size=(NTX, NRX, BINS)) + 1j * rng.normal(size=(NTX, NRX, BINS)))


def capture(
    static: np.ndarray, *, reflector: tuple[int, np.ndarray] | None = None, noise_seed: int = 1
):
    """(meta, cube) of a static scene: the same channels every loop and frame,
    plus a little noise, as parse_dump returns them."""
    values = static.copy()
    if reflector is not None:
        global_bin, channels = reflector
        values[:, :, global_bin - FIRST_BIN] += channels
    rng = np.random.default_rng(noise_seed)
    cube = np.zeros((FRAMES, NTX * LOOPS, NRX, BINS), dtype=complex)
    for frame in range(FRAMES):
        for loop in range(LOOPS):
            for tx in range(NTX):
                cube[frame, loop * NTX + tx] = values[tx] + 0.5 * (
                    rng.normal(size=(NRX, BINS)) + 1j * rng.normal(size=(NRX, BINS))
                )
    meta = {"n_tx": NTX, "n_frames": FRAMES, "range_bin_start": FIRST_BIN, "n_samples": BINS}
    return meta, cube


def placement(lateral_m: float, height_m: float, kind: str = "static", name: str = "x"):
    return ac.Placement(file=Path(name), kind=kind, lateral_m=lateral_m, height_m=height_m)


def scene_for(lib, lateral_m, height_m, *, offset_rad=0.0, amp=30.0, seed=7):
    """The background-subtracted channels of a reflector at a taped position."""
    rng_m, az, el = radar_angles(lib, lateral_m, height_m)
    global_bin = int(round(rng_m / BIN_M))
    background = clutter(seed)
    scene = ac.static_channels(
        *capture(background, reflector=(global_bin, reflector_channels(az, el, amp, offset_rad)))
    )
    empty = ac.static_channels(*capture(background, noise_seed=2))
    return ac.subtract_background(scene, empty), global_bin


# --- capture handling ----------------------------------------------------------------


def test_static_channels_average_frames_and_loops_per_tx(lib):
    background = clutter()
    channels = ac.static_channels(*capture(background))
    assert (channels.first_bin, channels.n_tx, channels.n_rx) == (FIRST_BIN, NTX, NRX)
    assert channels.values.shape == (NTX, NRX, BINS)
    assert np.allclose(channels.values, background, atol=1.0)
    assert channels.chirp_period_s == pytest.approx(CHIRP_PERIOD_S)


def test_frames_with_another_range_window_are_left_out():
    meta, cube = capture(clutter())
    cube[-1] += 1e6  # a post-trigger frame on another window
    meta["range_bin_starts"] = [FIRST_BIN] * (FRAMES - 1) + [FIRST_BIN + 4]
    meta["range_bin_counts"] = [BINS] * FRAMES
    channels = ac.static_channels(meta, cube)
    assert np.abs(channels.values).max() < 1e3


def test_chirps_that_do_not_split_into_the_tx_count_are_refused():
    meta, cube = capture(clutter())
    with pytest.raises(ValueError, match="do not split"):
        ac.static_channels(meta, cube[:, :-1])


def test_the_background_is_subtracted_over_the_shared_bins():
    background = clutter()
    scene = ac.static_channels(*capture(background))
    meta, cube = capture(background)
    meta["range_bin_start"] = FIRST_BIN + 3
    shifted = ac.static_channels(meta, cube)
    diff = ac.subtract_background(scene, shifted)
    assert diff.first_bin == FIRST_BIN + 3
    assert diff.values.shape[2] == BINS - 3


def test_a_background_from_another_layout_or_window_is_refused():
    scene = ac.static_channels(*capture(clutter()))
    with pytest.raises(ValueError, match="layout"):
        ac.subtract_background(scene, ac.StaticChannels(FIRST_BIN, 2, NRX, scene.values[:2], 1e-5))
    far = ac.StaticChannels(FIRST_BIN + BINS, NTX, NRX, scene.values, 1e-5)
    with pytest.raises(ValueError, match="no range bins"):
        ac.subtract_background(scene, far)


def test_the_reflector_is_found_under_stronger_clutter(lib):
    diff, global_bin = scene_for(lib, 0.0, 0.3)
    assert ac.find_reflector_bin(diff, BIN_M) == global_bin


def test_the_search_skips_the_antennas_leakage_and_honours_the_taped_range(lib):
    diff, global_bin = scene_for(lib, 0.0, 0.3)
    leak = int(0.3 / BIN_M)
    if leak >= FIRST_BIN:
        diff.values[:, :, leak - FIRST_BIN] += 1e4
    assert ac.find_reflector_bin(diff, BIN_M) == global_bin
    with pytest.raises(ValueError, match="no range bin"):
        ac.find_reflector_bin(diff, BIN_M, expected_m=50.0)


# --- static positions -----------------------------------------------------------------


@pytest.mark.parametrize("lateral_m", [-0.6, -0.3, 0.0, 0.3, 0.6])
@pytest.mark.parametrize("height_m", [0.05, 0.5])
def test_static_positions_land_on_their_tape(lib, lateral_m, height_m):
    diff, _ = scene_for(lib, lateral_m, height_m)
    result = ac.measure_static(lib, BOARD, placement(lateral_m, height_m), diff, 0.0, BIN_M)
    assert result.lateral_error_m == pytest.approx(0.0, abs=0.03)
    assert result.height_error_m == pytest.approx(0.0, abs=0.03)
    assert result.azimuth_coherence > 0.95


def test_an_unmeasured_offset_throws_the_positions_sideways(lib):
    diff, _ = scene_for(lib, 0.0, 0.05, offset_rad=0.6)
    result = ac.measure_static(lib, BOARD, placement(0.0, 0.05), diff, 0.0, BIN_M)
    assert abs(result.lateral_error_m) > 0.3


def test_the_fit_recovers_the_offset_and_puts_every_position_back(lib):
    offset = 0.6
    statics = []
    for lateral in (-0.6, -0.3, 0.0, 0.3, 0.6):
        diff, _ = scene_for(lib, lateral, 0.05, offset_rad=offset)
        statics.append((placement(lateral, 0.05), diff))
    fit = ac.fit_azimuth_offset(lib, BOARD, statics, BIN_M)
    assert fit is not None and fit.captures == 5
    assert fit.offset_rad == pytest.approx(offset, abs=math.radians(0.5))
    assert fit.median_abs_error_m < 0.02
    assert fit.slope == pytest.approx(1.0, abs=0.05) and fit.slope_ok


def test_a_flipped_sign_is_flagged_by_the_slope(lib):
    statics = []
    for lateral in (-0.6, -0.3, 0.0, 0.3, 0.6):
        diff, _ = scene_for(lib, lateral, 0.05)
        statics.append((placement(-lateral, 0.05), diff))  # the tape read the other way
    fit = ac.fit_azimuth_offset(lib, BOARD, statics, BIN_M)
    assert fit is not None and fit.slope < 0.0 and not fit.slope_ok


@pytest.mark.parametrize(
    "laterals", [(0.0, 0.3), (0.3, 0.3, 0.3, 0.3)], ids=["two captures", "one lateral"]
)
def test_too_few_positions_fit_nothing(lib, laterals):
    statics = [(placement(lat, 0.05), scene_for(lib, lat, 0.05)[0]) for lat in laterals]
    assert ac.fit_azimuth_offset(lib, BOARD, statics, BIN_M) is None


# --- moving captures ------------------------------------------------------------------


def point(y: float, z: float, valid: bool = True) -> fr.PointSummary:
    return fr.PointSummary(
        frame=0,
        timestamp_us=0,
        range_bin=40.0,
        range_m=1.9,
        doppler_mps=0.0,
        confidence=1.0,
        position=(1.9, y, z),
        angles_valid=valid,
    )


def test_moving_points_are_summarised_against_their_line():
    p = placement(0.3, 0.15, kind="moving")
    points = [
        point(0.3, 0.0),
        point(0.4, 0.0),
        point(0.55, 0.1),
        point(-0.5, 0.0),
        point(9.0, 9.0, False),
    ]
    m = ac.summarise_moving(p, points, radar_height_m=0.15)
    assert m.points == 4
    assert m.median_lateral_m == pytest.approx(0.35)
    assert m.median_height_error_m == pytest.approx(0.0)
    assert m.within[0.15] == pytest.approx(0.5)
    assert m.within[0.30] == pytest.approx(0.75)


def test_a_sweep_without_angled_points_says_so():
    m = ac.summarise_moving(placement(0.0, 0.1, kind="moving"), [point(0.0, 0.0, False)], 0.15)
    assert m.points == 0 and m.median_lateral_m is None and m.within == {}


# --- manifest, run, outputs ------------------------------------------------------------


def write_manifest(tmp_path, captures, empty="empty.l3dump"):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"empty": empty, "captures": captures}), encoding="utf-8")
    return path


def test_the_manifest_resolves_paths_next_to_it(tmp_path):
    path = write_manifest(
        tmp_path,
        [
            {"file": "a.l3dump", "kind": "static", "lateral_m": -0.3, "height_m": 0.1},
            {"file": "b.l3dump", "kind": "moving", "lateral_m": 0, "height_m": 0.1, "forward_m": 2},
        ],
    )
    m = ac.Manifest.load(path)
    assert m.empty == tmp_path / "empty.l3dump"
    assert [p.file for p in m.placements] == [tmp_path / "a.l3dump", tmp_path / "b.l3dump"]
    assert m.placements[1].forward_m == 2.0 and m.placements[0].forward_m is None


@pytest.mark.parametrize(
    "captures, message",
    [
        ([], "non-empty"),
        ([{"kind": "static", "lateral_m": 0, "height_m": 0}], "file"),
        ([{"file": "a", "kind": "swing", "lateral_m": 0, "height_m": 0}], "kind"),
        ([{"file": "a", "kind": "static", "height_m": 0}], "lateral_m"),
        ([{"file": "a", "kind": "static", "lateral_m": "0", "height_m": 0}], "lateral_m"),
        ([{"file": "a", "kind": "static", "lateral_m": True, "height_m": 0}], "lateral_m"),
        ([{"file": "a", "kind": "static", "lateral_m": float("nan"), "height_m": 0}], "finite"),
        ([{"file": "a", "kind": "static", "lateral_m": 0, "height_m": -0.1}], "above the floor"),
        (
            [{"file": "a", "kind": "static", "lateral_m": 0, "height_m": 0, "forward_m": 0}],
            "positive",
        ),
    ],
)
def test_a_bad_manifest_is_refused(tmp_path, captures, message):
    with pytest.raises(ValueError, match=message):
        ac.Manifest.load(write_manifest(tmp_path, captures))


def test_a_manifest_without_an_empty_capture_is_refused(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"captures": [{"file": "a"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        ac.Manifest.load(path)


def test_run_check_fits_then_reports_every_capture(lib, tmp_path, monkeypatch):
    offset = -0.8
    background = clutter()
    dumps = {"empty.l3dump": capture(background, noise_seed=2)}
    captures = []
    for i, lateral in enumerate((-0.6, -0.3, 0.0, 0.3, 0.6)):
        rng_m, az, el = radar_angles(lib, lateral, 0.05)
        dumps[f"s{i}.l3dump"] = capture(
            background,
            reflector=(int(round(rng_m / BIN_M)), reflector_channels(az, el, 30.0, offset)),
        )
        captures.append(
            {"file": f"s{i}.l3dump", "kind": "static", "lateral_m": lateral, "height_m": 0.05}
        )
    for name in dumps:
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(ac, "parse_dump", lambda raw: dumps[raw.decode()])
    report = ac.run_check(ac.Manifest.load(write_manifest(tmp_path, captures)), BOARD, lib=lib)
    assert report.fit is not None
    assert report.applied_offset_rad == report.fit.offset_rad
    assert report.fit.offset_rad == pytest.approx(offset, abs=math.radians(0.5))
    assert len(report.statics) == 5 and report.moving == ()
    assert all(abs(r.lateral_error_m) < 0.03 for r in report.statics)
    as_dict = report.to_dict()
    assert as_dict["fit"]["slope_ok"] is True and len(as_dict["static"]) == 5
    text = ac.format_report(report)
    assert "Azimuth offset" in text and "s4.l3dump" in text


def test_a_moving_capture_without_statics_or_a_taped_range_is_refused(lib, tmp_path, monkeypatch):
    dumps = {"empty.l3dump": capture(clutter()), "m.l3dump": capture(clutter())}
    for name in dumps:
        (tmp_path / name).write_bytes(name.encode())
    monkeypatch.setattr(ac, "parse_dump", lambda raw: dumps[raw.decode()])
    manifest = ac.Manifest.load(
        write_manifest(
            tmp_path, [{"file": "m.l3dump", "kind": "moving", "lateral_m": 0, "height_m": 0.1}]
        )
    )
    with pytest.raises(ValueError, match="forward_m"):
        ac.run_check(manifest, BOARD, lib=lib)


def test_the_written_calibration_carries_the_offset_and_where_it_came_from(tmp_path):
    fit = ac.OffsetFit(offset_rad=0.25, median_abs_error_m=0.01, slope=1.02, captures=5)
    out = ac.calibration_with_offset({"tilt_deg": 10.0}, fit, tmp_path / "manifest.json")
    assert out["tilt_deg"] == 10.0 and out["azimuth_offset_rad"] == 0.25
    assert out["azimuth_check"]["captures"] == 5
    board_raw = json.loads(Path(REFERENCE).read_text(encoding="utf-8"))
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(ac.calibration_with_offset(board_raw, fit, path)), encoding="utf-8")
    assert BoardCalibration.from_file(path).az_offset_rad == pytest.approx(0.25)


def test_without_a_fit_the_report_says_what_it_used(lib):
    report = ac.Report(statics=(), fit=None, applied_offset_rad=0.1, moving=())
    assert "No offset fit" in ac.format_report(report)
    assert report.to_dict()["fit"] is None


# --- the command line -----------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """scripts/iwr6843/azimuth_check.py with run_check stubbed to a given report."""
    import importlib.util  # pylint: disable=import-outside-toplevel

    spec = importlib.util.spec_from_file_location(
        "azimuth_check_cli", Path("scripts/iwr6843/azimuth_check.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = write_manifest(
        tmp_path, [{"file": "a.l3dump", "kind": "static", "lateral_m": 0, "height_m": 0.1}]
    )

    def run(fit, *extra):
        report = ac.Report(statics=(), fit=fit, applied_offset_rad=0.0, moving=())
        monkeypatch.setattr(module, "run_check", lambda manifest, board: report)
        return module.main([str(manifest), "--cal", REFERENCE, *extra])

    return run, tmp_path


def test_the_cli_writes_the_report_and_a_calibration_with_the_offset(cli):
    run, tmp_path = cli
    out = tmp_path / "cal.json"
    fit = ac.OffsetFit(offset_rad=-0.3, median_abs_error_m=0.02, slope=0.98, captures=5)
    assert run(fit, "--write-cal", str(out)) == 0
    assert json.loads((tmp_path / "azimuth_check_report.json").read_text())["fit"]["slope_ok"]
    assert BoardCalibration.from_file(out).az_offset_rad == pytest.approx(-0.3)


@pytest.mark.parametrize(
    "fit",
    [None, ac.OffsetFit(offset_rad=0.1, median_abs_error_m=0.3, slope=-0.9, captures=5)],
    ids=["no fit", "wrong sign"],
)
def test_the_cli_refuses_to_write_a_calibration_it_cannot_trust(cli, fit):
    run, tmp_path = cli
    out = tmp_path / "cal.json"
    assert run(fit, "--write-cal", str(out)) == 1
    assert not out.exists()
