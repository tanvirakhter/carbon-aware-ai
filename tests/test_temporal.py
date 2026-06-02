"""Offline tests for the temporal analysis, on synthetic national series.

These pin the methodology with hand-constructed curves where the right answer
is known by inspection.
"""

import numpy as np
import pandas as pd
import pytest

from carbon_aware_ai.analysis import temporal


def _series(actual, forecast=None, period_col="period_start"):
    """Build a contiguous half-hourly national frame from intensity arrays.

    ``period_col`` lets a test exercise load_national's column normalisation
    (the processed ETL output uses "from").
    """
    n = len(actual)
    start = pd.Timestamp("2024-01-01T00:00Z")
    idx = pd.date_range(start, periods=n, freq="30min", tz="UTC")
    if forecast is None:
        forecast = actual
    return pd.DataFrame(
        {
            period_col: idx,
            "to": idx + pd.Timedelta(minutes=30),
            "forecast": pd.array(forecast, dtype="Int64"),
            "actual": pd.array(actual, dtype="Int64"),
            "index": ["x"] * n,
        }
    )


# --------------------------------------------------------------------------- #
# prepare / pre-checks
# --------------------------------------------------------------------------- #
def test_prepare_drops_forecast_only_tail():
    df = _series(actual=[100, 90, 80, None, None], forecast=[100, 90, 80, 70, 60])
    clean, n_dropped = temporal.prepare(df)
    assert n_dropped == 2
    assert len(clean) == 3
    assert clean["actual"].notna().all()


def test_prepare_warns_on_interior_gap_but_does_not_raise(caplog):
    df = _series(actual=[100, 90, 80, 70], forecast=[100, 90, 80, 70])
    # punch an interior hole -> dropping it leaves a 30-min discontinuity
    df.loc[1, "actual"] = None
    with caplog.at_level("WARNING"):
        clean, n_dropped = temporal.prepare(df)
    assert n_dropped == 1
    assert len(clean) == 3
    assert any("gap" in r.message.lower() for r in caplog.records)


def test_prepare_normalises_from_column():
    # processed ETL output uses "from"; prepare/load should normalise it.
    df = _series(actual=[100, 90, 80], period_col="from")
    clean, _ = temporal.prepare(df)
    assert temporal.PERIOD_COL in clean.columns
    assert "from" not in clean.columns


def test_simulate_is_gap_aware():
    # 12 contiguous slots would give one 6h-window arrival; remove an interior
    # period entirely so no contiguous 12-slot window exists -> no scoring.
    df = _series(actual=list(range(100, 100 + 12)))
    df = df.drop(index=6).reset_index(drop=True)  # leaves an interior time gap
    arr = temporal.simulate(df, window_hours=6)
    assert arr.empty  # the only candidate window spans the gap -> skipped


def test_valid_decision_points_and_score_window():
    actual = [100, 95, 90, 85, 80, 50, 80, 85, 90, 95, 100, 105]
    df = _series(actual=actual)
    pts = temporal.valid_decision_points(df, window_hours=6)
    assert len(pts) == 1
    res = temporal.score_window(df, pts[0], window_hours=6, energy_kwh=2.0)
    assert res["baseline"] == 100 and res["oracle"] == 50
    assert res["oracle_gco2"] == 100.0  # 50 * 2.0 kWh


# --------------------------------------------------------------------------- #
# known-minimum synthetic curve: oracle exact, forecast == actual -> capture 1
# --------------------------------------------------------------------------- #
def test_oracle_exact_and_perfect_forecast_captures_all():
    # A 6h window = 12 slots. Min actual is 50 at slot 5; baseline (slot 0) = 100.
    actual = [100, 95, 90, 85, 80, 50, 80, 85, 90, 95, 100, 105]
    df = _series(actual=actual)  # forecast == actual
    arr = temporal.simulate(df, window_hours=6)
    assert len(arr) == 1  # exactly 12 slots -> one arrival
    row = arr.iloc[0]
    assert row["baseline"] == 100
    assert row["oracle"] == 50
    # perfect forecast -> scheduler picks the true minimum
    assert row["scheduler"] == 50
    assert row["oracle_saving_pct"] == pytest.approx(50.0)
    assert row["scheduler_saving_pct"] == pytest.approx(50.0)
    assert temporal.capture_ratio(arr) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# forecast-min != actual-min -> capture_ratio < 1
# --------------------------------------------------------------------------- #
def test_misleading_forecast_gives_capture_below_one():
    # True actual min is 50 at slot 5. But the forecast is lowest at slot 2,
    # whose actual is 90 -> scheduler saves only 10 of the available 50.
    actual = [100, 95, 90, 85, 80, 50, 80, 85, 90, 95, 100, 105]
    forecast = [100, 95, 10, 85, 80, 99, 80, 85, 90, 95, 100, 105]
    df = _series(actual=actual, forecast=forecast)
    arr = temporal.simulate(df, window_hours=6)
    row = arr.iloc[0]
    assert row["oracle"] == 50
    assert row["scheduler"] == 90  # actual at the forecast-chosen slot 2
    assert row["oracle_saving"] == 50
    assert row["scheduler_saving"] == 10
    cap = temporal.capture_ratio(arr)
    assert cap == pytest.approx(10 / 50)
    assert cap < 1.0


def test_misleading_forecast_can_pick_worse_than_now():
    # Forecast lowest at the last slot, whose actual (130) is worse than now (100)
    # -> negative scheduler saving, capture ratio negative.
    actual = [100, 95, 90, 85, 80, 70, 80, 85, 90, 95, 100, 130]
    forecast = [100, 95, 90, 85, 80, 99, 80, 85, 90, 95, 100, 1]
    df = _series(actual=actual, forecast=forecast)
    arr = temporal.simulate(df, window_hours=6)
    row = arr.iloc[0]
    assert row["scheduler"] == 130
    assert row["scheduler_saving"] == -30
    assert temporal.capture_ratio(arr) < 0


# --------------------------------------------------------------------------- #
# window longer than data -> graceful empty result
# --------------------------------------------------------------------------- #
def test_window_longer_than_data_is_graceful():
    df = _series(actual=[100, 90, 80, 70])  # only 4 slots
    arr = temporal.simulate(df, window_hours=6)  # needs 12
    assert arr.empty

    results = temporal.run_analysis(df, windows=(6, 12))
    assert 6 in results.skipped and 12 in results.skipped
    assert results.windows[6]["n_jobs"] == 0
    # summary table renders without error
    assert "no data" in temporal.format_summary_table(results)


# --------------------------------------------------------------------------- #
# all-equal window -> zero savings, no divide-by-zero
# --------------------------------------------------------------------------- #
def test_all_equal_window_zero_savings_no_div_zero():
    actual = [200] * 12
    df = _series(actual=actual)
    arr = temporal.simulate(df, window_hours=6)
    row = arr.iloc[0]
    assert row["oracle_saving"] == 0
    assert row["scheduler_saving"] == 0
    assert row["oracle_saving_pct"] == 0.0
    assert row["scheduler_saving_pct"] == 0.0
    # capture_ratio is NaN (0/0) rather than raising
    cap = temporal.capture_ratio(arr)
    assert np.isnan(cap)
    # summarize also survives
    s = temporal.summarize(arr)
    assert s["n_jobs"] == 1
    assert np.isnan(s["capture_ratio"])


def test_zero_baseline_does_not_divide_by_zero():
    # baseline (slot 0) is 0 gCO2/kWh -> pct undefined, coerced to 0.0
    actual = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110]
    df = _series(actual=actual)
    arr = temporal.simulate(df, window_hours=6)
    row = arr.iloc[0]
    assert row["oracle_saving_pct"] == 0.0
    assert row["scheduler_saving_pct"] == 0.0


# --------------------------------------------------------------------------- #
# aggregation / breakdowns
# --------------------------------------------------------------------------- #
def test_summarize_has_distributions_and_breakdowns():
    # 48h of data -> many 6h-window arrivals across hours of day
    rng = np.arange(96)
    actual = (100 + 30 * np.sin(rng / 96 * 2 * np.pi * 2)).round().astype(int)
    df = _series(actual=actual.tolist())
    results = temporal.run_analysis(df, windows=(6,))
    s = results.windows[6]
    assert s["n_jobs"] > 0
    for key in ("mean", "median", "p10", "p90", "iqr"):
        assert key in s["scheduler_saving_pct"]
    assert set(s["by_hour"]["hour"]).issubset(set(range(24)))
    assert "is_weekend" in s["by_weekend"].columns


def test_run_temporal_adds_gco2_and_summarise_alias():
    actual = [100, 95, 90, 85, 80, 50, 80, 85, 90, 95, 100, 105]
    df = _series(actual=actual)
    arr = temporal.run_temporal(df, window_hours=6, energy_kwh=10.0)
    assert {"baseline_gco2", "oracle_gco2", "scheduler_gco2"} <= set(arr.columns)
    assert arr["oracle_gco2"].iloc[0] == 500.0  # 50 * 10 kWh
    # summarise (British alias) surfaces the gCO2 aggregates
    s = temporal.summarise(arr)
    assert s["oracle_gco2_total"] == 500.0
    assert s["baseline_gco2_per_job_mean"] == 1000.0
