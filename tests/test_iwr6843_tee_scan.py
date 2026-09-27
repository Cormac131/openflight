"""Tests for openflight.iwr6843.tee_scan: static scans, ball status and setup advice."""

from __future__ import annotations

import pytest

from openflight.iwr6843.tee_scan import (
    TeeScan,
    average_scans,
    bin_range_m,
    classify_setup,
    detect_ball,
    parse_ball_status,
    parse_tee_scan,
)

REPLY = (
    "ball scan 8 4\nteescan frames=9 loops=12 first=8 count=4 start=20\n"
    "bin=8 power=1000\nbin=9 power=1200\nbin=10 power=800\nbin=11 power=950\nDone\nl3dump:/>"
)


def test_parse_reads_header_and_every_bin():
    scan = parse_tee_scan(REPLY)

    assert (scan.frames, scan.loops, scan.first, scan.count, scan.window_start) == (9, 12, 8, 4, 20)
    assert scan.power == {8: 1000.0, 9: 1200.0, 10: 800.0, 11: 950.0}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("ball scan 8 4\nError: ball scan count\nError -1\n", "no teescan header"),
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


def test_bin_range_inverts_the_tee_bin_conversion():
    from openflight.iwr6843.monitor import tee_global_bin

    config = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
    assert bin_range_m(tee_global_bin(1.575, config)) == pytest.approx(1.575, abs=0.0235)
    assert bin_range_m(48) == pytest.approx(2.25, abs=0.01), "where ten captures put the club"


STATUS = (
    "ball status\nball state=locked follow=1 bin=48 ratio=8.65 confidence=0.93 delta=7650000 "
    "background=1000000 age=120 locks=3 releases=2 reason=none window=20+53\n"
    "balldbg updates=1842 candidate=0/0 centroid=48.27 width=2 persistence=47/50 "
    "no_delta=900 too_wide=12 unstable=3 gone=40\nDone\nl3dump:/>"
)


def test_parse_ball_status_reads_both_lines():
    status = parse_ball_status(STATUS)

    assert status.locked and status.bin == 48 and status.follow is True
    assert status.ratio == pytest.approx(8.65) and status.confidence == pytest.approx(0.93)
    assert (status.locks, status.releases, status.reason) == (3, 2, "none")
    assert status.window == (20, 53)
    assert status.centroid == pytest.approx(48.27) and status.width == 2
    assert status.persistence == pytest.approx(47 / 50)
    assert status.range_m == pytest.approx(48.27 * 6.0 / 128)


def test_parse_ball_status_without_a_lock_has_no_bin():
    text = (
        "ball state=waiting follow=0 bin=0 ratio=0.00 confidence=0.00 delta=0 background=0 "
        "age=0 locks=0 releases=0 reason=no_delta window=20+53\nDone\n"
    )
    status = parse_ball_status(text)
    assert not status.locked and status.bin is None and status.reason == "no_delta"
    assert status.centroid is None and status.range_m is None
    with pytest.raises(ValueError, match="no ball status"):
        parse_ball_status("Done\n")


@pytest.mark.parametrize(
    ("range_m", "label", "ok", "fragment"),
    [
        (1.20, "too-close", False, "40 cm back"),
        (1.40, "close", True, "20 cm back"),
        (1.60, "ideal", True, "Ready"),
        (1.90, "far", True, "30 cm closer"),
        (2.25, "too-far", False, "65 cm closer"),
    ],
)
def test_classify_setup_names_the_band_and_the_move(range_m, label, ok, fragment):
    advice = classify_setup(range_m)
    assert advice.label == label and advice.ok is ok
    assert fragment in advice.message


def test_parse_ball_status_reads_the_ball_angle_line_with_its_two_valid_keys():
    text = (
        "ball state=locked follow=0 bin=34 ratio=8.0 confidence=0.9 delta=1 background=1 "
        "age=9 locks=1 releases=0 reason=none window=20+53\n"
        "balldbg updates=10 candidate=0/0 centroid=34.10 width=2 persistence=9/10\n"
        "ballangle az=1.20 el=-3.40 coh=0.91 peak=6.2 psi=0.35 conf=0.84 valid=ae valid=1\nDone\n"
    )
    status = parse_ball_status(text)
    angle = status.angle
    assert angle is not None
    assert angle.azimuth_deg == pytest.approx(1.2) and angle.elevation_deg == pytest.approx(-3.4)
    assert angle.azimuth_coherence == pytest.approx(0.91)
    assert angle.elevation_peak_ratio == pytest.approx(6.2)
    assert angle.confidence == pytest.approx(0.84) and angle.trusted is True
    # Elevation only, and the detector did not trust it.
    only_el = parse_ball_status(
        "ball state=locked follow=0 bin=34 ratio=8.0 confidence=0.9 delta=1 background=1 "
        "age=9 locks=1 releases=0 reason=none window=20+53\n"
        "ballangle az=0.00 el=2.00 coh=0.00 peak=2.1 psi=0.00 conf=0.22 valid=e valid=0\nDone\n"
    ).angle
    assert only_el.azimuth_deg is None and only_el.elevation_deg == pytest.approx(2.0)
    assert only_el.trusted is False
    none = parse_ball_status(
        "ball state=waiting follow=0 bin=0 ratio=0 confidence=0 delta=0 background=0 age=0 "
        "locks=0 releases=0 reason=no_delta window=20+53\n"
        "ballangle az=0.00 el=0.00 coh=0.00 peak=0.0 psi=0.00 conf=0.00 valid=none valid=0\nDone\n"
    ).angle
    assert none.azimuth_deg is None and none.elevation_deg is None
    assert parse_ball_status(STATUS).angle is None, "older firmware prints no angle line"
