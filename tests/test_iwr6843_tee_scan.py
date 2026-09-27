"""Tests for openflight.iwr6843.tee_scan: teeScan parsing and stationary-ball detection."""

from __future__ import annotations

import pytest

from openflight.iwr6843.tee_scan import (
    TeeScan,
    average_scans,
    detect_ball,
    local_bin_range_m,
    parse_tee_scan,
)

REPLY = (
    "teeScan 8 4\nteescan frames=9 loops=12 first=8 count=4 start=20\n"
    "bin=8 power=1000\nbin=9 power=1200\nbin=10 power=800\nbin=11 power=950\nDone\nl3dump:/>"
)


def test_parse_reads_header_and_every_bin():
    scan = parse_tee_scan(REPLY)

    assert (scan.frames, scan.loops, scan.first, scan.count, scan.window_start) == (9, 12, 8, 4, 20)
    assert scan.power == {8: 1000.0, 9: 1200.0, 10: 800.0, 11: 950.0}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("teeScan 8 4\nError: teeScan count\nError -1\n", "no teescan header"),
        ("teescan frames=9 loops=12 first=8 count=4 start=20\nbin=8 power=1\nDone\n", "promised 4"),
    ],
)
def test_parse_rejects_a_refusal_or_a_short_reply(text, message):
    with pytest.raises(ValueError, match=message):
        parse_tee_scan(text)


def test_average_weights_scans_by_their_frames():
    first = TeeScan(9, 12, 8, 2, 20, {8: 100.0, 9: 200.0})
    second = TeeScan(3, 12, 8, 2, 20, {8: 500.0, 9: 200.0})

    averaged = average_scans([first, second])

    assert averaged[8] == pytest.approx((100.0 * 9 + 500.0 * 3) / 12)
    assert averaged[9] == pytest.approx(200.0)


def test_average_refuses_mismatched_bins_or_no_frames():
    with pytest.raises(ValueError, match="different bins"):
        average_scans([TeeScan(9, 12, 8, 1, 20, {8: 1.0}), TeeScan(9, 12, 9, 1, 20, {9: 1.0})])
    with pytest.raises(ValueError, match="no pre-trigger frames"):
        average_scans([TeeScan(0, 12, 8, 1, 20, {8: 1.0})])
    with pytest.raises(ValueError, match="no scans"):
        average_scans([])


def test_detect_finds_the_bin_that_grew_most_near_the_expected_one():
    """The tee range is measured by hand; the ball can sit a bin or two off the computed one."""
    baseline = {b: 1000.0 for b in range(8, 21)}
    occupied = dict(baseline)
    occupied[15] = 8650.0  # the ball
    occupied[19] = 1500.0  # something else moved a little

    found = detect_ball(baseline, occupied, expected_bin=14)

    assert found.detected_bin == 15
    assert found.offset_bins == 1
    assert found.ratio == pytest.approx(8.65)
    assert found.delta == pytest.approx(7650.0)
    assert found.search_bins == (8, 20)


def test_detect_search_is_bounded_and_needs_bins_in_range():
    baseline = {b: 1000.0 for b in range(8, 21)}
    occupied = dict(baseline)
    occupied[20] = 9000.0  # outside +/-4 of bin 14

    found = detect_ball(baseline, occupied, expected_bin=14, half_width=4)
    assert found.detected_bin != 20
    assert found.search_bins == (10, 18)
    with pytest.raises(ValueError, match="no scanned bins"):
        detect_ball(baseline, occupied, expected_bin=40)


def test_detect_ratio_floors_an_empty_baseline():
    found = detect_ball({14: 0.0}, {14: 300.0}, expected_bin=14)
    assert found.ratio == pytest.approx(300.0)


def test_local_bin_range_inverts_the_tee_bin_conversion():
    from openflight.iwr6843.monitor import tee_local_bin

    config = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
    local = tee_local_bin(1.575, config)
    assert local_bin_range_m(local, 20) == pytest.approx(1.575, abs=0.0235)
