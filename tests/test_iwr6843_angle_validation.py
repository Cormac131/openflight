"""The angular validation arithmetic: per-position statistics, repeatability,
error against speed, the A/B of two paths, and the JSON round trip."""

from __future__ import annotations

import math

import pytest

from openflight.iwr6843.angle_validation import (
    STATIC_AZIMUTHS_DEG,
    STATIC_ELEVATIONS_DEG,
    AngleSample,
    ValidationSet,
    angle_stats,
    compare_paths,
    error_by_speed,
    format_comparison,
    format_positions,
    format_speed_bins,
    repeatability,
    summarize_positions,
)


def sample(truth_az, truth_el, az, el, **kw):
    return AngleSample(truth_az, truth_el, az, el, **kw)


def test_angle_stats_report_bias_spread_and_missing_measurements():
    samples = [
        sample(10.0, 0.0, 10.5, 0.2),
        sample(10.0, 0.0, 11.5, -0.2),
        sample(10.0, 0.0, 9.0, 0.0),
        sample(10.0, 0.0, None, 0.1),
    ]
    az = angle_stats(samples, "azimuth")
    assert az.count == 3 and az.missing == 1
    assert az.bias_deg == pytest.approx((0.5 + 1.5 - 1.0) / 3)
    errors = [0.5, 1.5, -1.0]
    mean = sum(errors) / 3
    assert az.std_deg == pytest.approx(math.sqrt(sum((e - mean) ** 2 for e in errors) / 2))
    assert az.mean_abs_deg == pytest.approx(1.0) and az.max_abs_deg == 1.5
    assert az.p95_abs_deg == 1.5
    el = angle_stats(samples, "elevation")
    assert el.count == 4 and el.missing == 0 and el.bias_deg == pytest.approx(0.025)
    empty = angle_stats([sample(0, 0, None, None)], "azimuth")
    assert empty.count == 0 and empty.missing == 1 and empty.bias_deg is None


def test_positions_are_summarised_in_first_seen_order_with_their_quality():
    samples = [
        sample(0.0, 0.0, 0.3, -0.1, coherence=0.9, peak_ratio=6.0, confidence=0.9),
        sample(0.0, 0.0, -0.3, 0.1, coherence=0.7, peak_ratio=4.0, confidence=0.6),
        sample(10.0, 5.0, 10.8, 5.4, coherence=0.8, peak_ratio=5.0, confidence=0.8),
    ]
    summaries = summarize_positions(samples)
    assert [(s.truth_azimuth_deg, s.truth_elevation_deg) for s in summaries] == [
        (0.0, 0.0),
        (10.0, 5.0),
    ]
    first = summaries[0]
    assert first.azimuth.bias_deg == pytest.approx(0.0) and first.elevation.count == 2
    assert first.mean_coherence == pytest.approx(0.8) and first.mean_peak_ratio == 5.0
    assert summaries[1].azimuth.bias_deg == pytest.approx(0.8)
    table = format_positions(summaries)
    assert (
        table.splitlines()[0].strip().startswith("truth az")
        and "+10.0" in table
        and "+0.80" in table
    )


def test_repeatability_is_the_spread_of_the_repeats_means():
    runs = [
        [sample(5.0, 0.0, 5.2, 0.0), sample(5.0, 0.0, 5.4, 0.0)],  # mean error +0.3
        [sample(5.0, 0.0, 4.9, 0.0), sample(5.0, 0.0, 4.7, 0.0)],  # mean error -0.2
        [sample(5.0, 0.0, 5.0, 0.0)],  # 0.0
    ]
    r = repeatability(runs)
    means = [0.3, -0.2, 0.0]
    mean = sum(means) / 3
    assert r.azimuth_spread_deg == pytest.approx(math.sqrt(sum((m - mean) ** 2 for m in means) / 2))
    assert r.elevation_spread_deg == pytest.approx(0.0)
    assert r.repeats == 3
    assert repeatability([runs[0]]).azimuth_spread_deg is None


def test_error_by_speed_bins_the_moving_samples():
    samples = [
        sample(0.0, 0.0, 0.1, 0.1, speed_mps=2.0, coherence=0.9),
        sample(0.0, 0.0, -0.1, 0.2, speed_mps=-3.0, coherence=0.9),
        sample(0.0, 0.0, 1.0, 0.8, speed_mps=12.0, coherence=0.5),
        sample(0.0, 0.0, None, None, speed_mps=25.0),
        sample(0.0, 0.0, 0.0, 0.0, speed_mps=99.0),  # beyond the edges: ignored
    ]
    bins = error_by_speed(samples, (0.0, 5.0, 15.0, 30.0))
    assert [(b.low_mps, b.high_mps) for b in bins] == [(0.0, 5.0), (5.0, 15.0), (15.0, 30.0)]
    assert bins[0].azimuth.count == 2 and bins[0].azimuth.mean_abs_deg == pytest.approx(0.1)
    assert bins[0].mean_coherence == pytest.approx(0.9)
    assert bins[1].elevation.count == 1 and bins[1].elevation.bias_deg == pytest.approx(0.8)
    assert bins[2].azimuth.count == 0 and bins[2].azimuth.missing == 1
    text = format_speed_bins(bins)
    assert "speed m/s" in text and " 5.0-15.0" in text


def test_compare_paths_puts_iq16_beside_iq8():
    iq16 = [sample(0.0, 0.0, 0.2, 0.1, label="iq16"), sample(0.0, 0.0, -0.2, -0.1, label="iq16")]
    iq8 = [
        sample(0.0, 0.0, 0.9, 0.6, label="iq8:edma"),
        sample(0.0, 0.0, -0.9, None, label="iq8:edma"),
    ]
    c = compare_paths(iq16, iq8)
    assert (c.label_a, c.label_b) == ("iq16", "iq8:edma")
    assert c.elevation_b.missing == 1 and c.elevation_a.missing == 0
    assert c.azimuth_b.std_deg > c.azimuth_a.std_deg
    assert c.elevation_std_ratio is None, "one iq8 elevation is not a spread"
    text = format_comparison(c)
    assert "iq8:edma" in text and "el missing" in text
    assert compare_paths([], []).label_a == "a"


def test_validation_set_round_trips_through_json(tmp_path):
    vs = ValidationSet("static", firmware_sha="abc", notes="reflector at 1.6 m")
    vs.samples.append(
        sample(0.0, 0.0, 0.1, -0.2, coherence=0.9, peak_ratio=6.1, confidence=0.9, label="iq16")
    )
    path = tmp_path / "static.json"
    vs.save(path)
    loaded = ValidationSet.load(path)
    assert loaded.protocol == "static" and loaded.firmware_sha == "abc" and loaded.notes == vs.notes
    assert loaded.samples == vs.samples
    assert len(STATIC_AZIMUTHS_DEG) == 9 and len(STATIC_ELEVATIONS_DEG) == 7
