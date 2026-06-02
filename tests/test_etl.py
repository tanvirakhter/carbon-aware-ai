"""Parsing tests: validate the ETL against mock JSON matching the API schema."""

import pandas as pd

from carbon_aware_ai import etl


# --------------------------------------------------------------------------- #
# national
# --------------------------------------------------------------------------- #
def test_parse_national_shape_and_types(national_json):
    df = etl.parse_national(national_json)
    assert list(df.columns) == ["from", "to", "forecast", "actual", "index"]
    assert len(df) == 3
    # timestamps are tz-aware UTC (resolution may be ns or us depending on pandas)
    assert isinstance(df["from"].dtype, pd.DatetimeTZDtype)
    assert str(df["from"].dtype.tz) == "UTC"
    assert df["from"].iloc[0] == pd.Timestamp("2024-01-20T12:00Z")
    # both forecast and actual are present; nullable Int64 handles missing actual
    assert df["forecast"].iloc[0] == 266
    assert df["actual"].iloc[0] == 263
    assert pd.isna(df["actual"].iloc[2])  # null actual in fixture


def test_parse_national_accepts_bare_list(national_json):
    # national_intensity_range returns a bare list of period dicts
    df = etl.parse_national(national_json["data"])
    assert len(df) == 3


def test_parse_national_dedups_and_sorts():
    periods = [
        {"from": "2024-01-20T12:30Z", "to": "2024-01-20T13:00Z",
         "intensity": {"forecast": 2, "actual": 2, "index": "low"}},
        {"from": "2024-01-20T12:00Z", "to": "2024-01-20T12:30Z",
         "intensity": {"forecast": 1, "actual": 1, "index": "low"}},
        {"from": "2024-01-20T12:00Z", "to": "2024-01-20T12:30Z",
         "intensity": {"forecast": 1, "actual": 1, "index": "low"}},
    ]
    df = etl.parse_national(periods)
    assert len(df) == 2
    assert df["from"].is_monotonic_increasing


def test_parse_national_empty():
    df = etl.parse_national({"data": []})
    assert df.empty
    assert list(df.columns) == ["from", "to", "forecast", "actual", "index"]


# --------------------------------------------------------------------------- #
# regional
# --------------------------------------------------------------------------- #
def test_parse_regional_current(regional_current_json):
    df = etl.parse_regional(regional_current_json)
    # one row per period/region - 1 period x 2 regions
    assert len(df) == 2
    base = ["from", "to", "regionid", "dnoregion", "shortname", "forecast", "index"]
    assert base == list(df.columns)[: len(base)]
    # regional is forecast-only - no actual column
    assert "actual" not in df.columns
    # generation mix expanded into <fuel>_perc columns
    assert "wind_perc" in df.columns
    scotland = df[df["regionid"] == 1].iloc[0]
    assert scotland["shortname"] == "North Scotland"
    assert scotland["forecast"] == 53
    assert scotland["wind_perc"] == 85.8


def test_parse_regional_fw48h(regional_fw48h_json):
    df = etl.parse_regional(regional_fw48h_json)
    # 2 periods x 2 regions
    assert len(df) == 4
    assert df["from"].nunique() == 2
    assert set(df["regionid"].unique()) == {1, 13}
    # sorted by (from, regionid)
    assert df.sort_values(["from", "regionid"]).equals(df)


def test_parse_regional_empty():
    df = etl.parse_regional({"data": []})
    assert df.empty


# --------------------------------------------------------------------------- #
# factors
# --------------------------------------------------------------------------- #
def test_parse_factors(factors_json):
    df = etl.parse_factors(factors_json)
    assert list(df.columns) == ["fuel", "gco2_per_kwh"]
    coal = df[df["fuel"] == "Coal"].iloc[0]
    assert coal["gco2_per_kwh"] == 937
    assert df[df["fuel"] == "Wind"].iloc[0]["gco2_per_kwh"] == 0


# --------------------------------------------------------------------------- #
# consolidation (raw snapshots -> processed tidy tables)
# --------------------------------------------------------------------------- #
def test_consolidate_roundtrip(tmp_path, national_json, regional_fw48h_json):
    raw_national = tmp_path / "raw" / "national"
    raw_regional = tmp_path / "raw" / "regional"
    processed = tmp_path / "processed"
    raw_national.mkdir(parents=True)
    raw_regional.mkdir(parents=True)

    etl.parse_national(national_json).to_parquet(raw_national / "n.parquet")

    reg = etl.parse_regional(regional_fw48h_json)
    reg.insert(0, "collected_at", pd.Timestamp("2024-01-20T11:00Z"))
    reg.to_parquet(raw_regional / "r.parquet")

    written = etl.consolidate(
        raw_regional_dir=raw_regional,
        raw_national_dir=raw_national,
        processed_dir=processed,
    )

    assert (processed / "national.parquet").exists()
    assert (processed / "national.csv").exists()
    assert (processed / "regional_forecast.parquet").exists()

    nat = pd.read_parquet(written["national"])
    assert len(nat) == 3
    regout = pd.read_parquet(written["regional"])
    assert len(regout) == 4  # one row per period/region


def test_consolidate_keeps_latest_forecast(tmp_path, regional_fw48h_json):
    raw_regional = tmp_path / "raw" / "regional"
    processed = tmp_path / "processed"
    raw_regional.mkdir(parents=True)

    early = etl.parse_regional(regional_fw48h_json)
    early.insert(0, "collected_at", pd.Timestamp("2024-01-20T08:00Z"))
    early.to_parquet(raw_regional / "early.parquet")

    late = etl.parse_regional(regional_fw48h_json)
    late["forecast"] = late["forecast"] + 100  # a newer, different forecast
    late.insert(0, "collected_at", pd.Timestamp("2024-01-20T10:00Z"))
    late.to_parquet(raw_regional / "late.parquet")

    written = etl.consolidate(
        raw_regional_dir=raw_regional,
        raw_national_dir=tmp_path / "raw" / "national",  # absent -> skipped
        processed_dir=processed,
    )
    regout = pd.read_parquet(written["regional"]).sort_values(["from", "regionid"])
    # one row per (period, region), holding the latest collected forecast
    assert len(regout) == 4
    scotland_first = regout[regout["regionid"] == 1].iloc[0]
    assert scotland_first["forecast"] == 48 + 100
