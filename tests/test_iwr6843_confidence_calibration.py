"""Confidence bands against real error, and the threshold that meets a bound."""

from __future__ import annotations

import pytest

from openflight.iwr6843.confidence_calibration import (
    ErrorPair,
    calibrate,
    format_calibration,
    threshold_for,
)


def pairs():
    out = []
    # High confidence: small errors; medium: moderate; low: large, with one outlier each.
    for i in range(30):
        out.append(ErrorPair(0.92 + 0.002 * i, 0.5 + 0.02 * i))
    out.append(ErrorPair(0.95, 3.5))
    for i in range(20):
        out.append(ErrorPair(0.72 + 0.008 * i, 1.0 + 0.05 * i))
    for i in range(10):
        out.append(ErrorPair(0.1 + 0.04 * i, 3.0 + 0.3 * i))
    return out


def test_calibration_reports_each_band_highest_first():
    rows = calibrate(pairs(), bound=2.0)
    assert [(r.low, round(r.high, 2)) for r in rows] == [
        (0.9, 1.0),
        (0.7, 0.9),
        (0.5, 0.7),
        (0.0, 0.5),
    ]
    high, medium, empty, low = rows
    assert high.count == 31 and high.p95 == pytest.approx(1.08, abs=0.01) and high.max == 3.5
    assert high.within_bound == pytest.approx(30 / 31)
    assert medium.count == 20 and medium.mae == pytest.approx(1.475, abs=1e-6)
    assert empty.count == 0 and empty.mae is None and empty.within_bound is None
    assert low.count == 10 and low.within_bound == 0.0
    text = format_calibration(rows, unit="deg", bound=2.0)
    assert "within 2deg" in text and "97%" in text and " 0%" in text


def test_threshold_for_finds_the_lowest_confidence_meeting_the_bound():
    threshold = threshold_for(pairs(), bound=2.0, coverage=0.95, min_count=20)
    assert threshold is not None
    assert 0.7 <= threshold <= 0.9, (
        "the medium band still meets 2 deg at 95%; the low band breaks it"
    )
    strict = threshold_for(pairs(), bound=1.0, coverage=0.95, min_count=20)
    assert strict is not None and strict >= 0.9
    assert threshold_for(pairs(), bound=0.1, coverage=0.95, min_count=20) is None
    assert threshold_for([], bound=1.0) is None
    assert threshold_for([ErrorPair(0.99, 0.1)] * 5, bound=1.0, min_count=20) is None, (
        "too few readings"
    )
