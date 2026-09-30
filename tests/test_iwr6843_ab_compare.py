"""The IQ16/IQ8 A/B table and its corpus aggregate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openflight.iwr6843.ab_compare import (
    aggregate,
    compare_replays,
    format_aggregate,
    format_table,
    rows_to_dict,
)


def _result(
    *,
    fired=4,
    impact_us=12000,
    points=5,
    ball=6,
    speed=31.0,
    delivery=True,
    launch=True,
    hla=-1.0,
):
    delivery_obj = (
        SimpleNamespace(speed_mps=32.5, path_deg=2.0, attack_deg=-3.0, residual_m=0.012)
        if delivery
        else None
    )
    launch_obj = (
        SimpleNamespace(speed_mps=60.0, hla_deg=hla, vla_deg=14.0, residual_m=0.02, confidence=0.8)
        if launch
        else None
    )
    return SimpleNamespace(
        fired_frame=fired,
        impact_timestamp_us=impact_us,
        points=[SimpleNamespace(range_bin=30.0 + i) for i in range(points)],
        acquisitions=1,
        speed_mps=speed,
        delivery=delivery_obj,
        ball_points=[SimpleNamespace(range_bin=50.0 + 2 * i) for i in range(ball)],
        launch=launch_obj,
    )


def test_rows_cover_trigger_club_and_ball_with_signed_deltas():
    rows = compare_replays(_result(), _result(fired=5, speed=30.4, hla=-1.6, ball=5))
    by_name = {(r.name, r.unit): r for r in rows}
    assert by_name[("Trigger fire frame", "frame")].delta == 1
    assert by_name[("Club speed (fit)", "m/s")].delta == pytest.approx(-0.6)
    assert by_name[("HLA", "deg")].delta == pytest.approx(-0.6)
    assert by_name[("Ball points", "points")].delta == -1
    assert by_name[("Ball speed", "mph")].a == pytest.approx(134.2, abs=0.05)
    assert ("Geometric impact frame", "frame") not in by_name, "the detector was removed"
    assert all(r.agrees for r in rows)


def test_a_measurement_only_one_path_produced_is_flagged_not_subtracted():
    rows = compare_replays(_result(), _result(launch=False))
    hla = next(r for r in rows if r.name == "HLA")
    assert hla.delta is None and not hla.agrees
    table = format_table(rows)
    assert "ONLY IQ16" in table
    assert table.splitlines()[0].startswith("Measurement")
    assert "Ball speed" in table and "mph" in table


def test_aggregate_counts_disagreements_and_averages_deltas():
    tables = [
        compare_replays(_result(), _result(speed=30.0)),
        compare_replays(_result(), _result(speed=33.0)),
        compare_replays(_result(), _result(launch=False)),
    ]
    agg = {a.name: a for a in aggregate(tables)}
    speed = agg["Club speed (fit)"]
    assert speed.captures == 3 and speed.disagreements == 0
    assert speed.mean_abs_delta == pytest.approx((1.0 + 2.0 + 0.0) / 3)
    assert speed.max_abs_delta == pytest.approx(2.0)
    assert speed.bias == pytest.approx((-1.0 + 2.0 + 0.0) / 3)
    hla = agg["HLA"]
    assert hla.captures == 2 and hla.disagreements == 1
    text = format_aggregate(list(agg.values()))
    assert "only-one" in text and "Club speed (fit)" in text
    assert rows_to_dict(tables[0])[0]["name"] == "Trigger fire frame"
    assert aggregate([]) == []
