"""ETL: parse raw Carbon Intensity API JSON into tidy half-hourly tables.

The ``parse_*`` functions are pure: API JSON (Python dicts, exactly as the
endpoints return) in, a tidy :class:`pandas.DataFrame` out, one row per
period (national) or per period/region (regional). They have no network or
filesystem dependency, which is what lets the test-suite validate them
offline against mock payloads.

The :func:`consolidate` helper is the batch side: it reads the timestamped
Parquet snapshots the collector drops in ``data/raw/`` and writes
de-duplicated tidy Parquet + CSV to ``data/processed/``.

Tidy schemas
------------
national:    from, to, forecast, actual, index
regional:    from, to, regionid, dnoregion, shortname, forecast, index,
             [<fuel>_perc ... one column per generation-mix fuel]
factors:     fuel, gco2_per_kwh
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from . import config

UTC_TS_DTYPE = "datetime64[ns, UTC]"


# --------------------------------------------------------------------------- #
# datetime parsing
# --------------------------------------------------------------------------- #
def _to_utc_ts(value: Optional[str]) -> Optional[pd.Timestamp]:
    """Parse an API ``YYYY-MM-DDThh:mmZ`` string into a UTC pandas Timestamp."""
    if value is None:
        return None
    return pd.to_datetime(value, utc=True)


# --------------------------------------------------------------------------- #
# national
# --------------------------------------------------------------------------- #
def parse_national(payload: dict[str, Any]) -> pd.DataFrame:
    """Parse ``/intensity/{from}/{to}`` JSON into a tidy national table.

    Accepts either the raw endpoint dict ``{"data": [...]}`` or a bare list of
    period dicts (as returned by
    :meth:`~carbon_aware_ai.api_client.CarbonIntensityClient.national_intensity_range`).
    """
    periods = payload["data"] if isinstance(payload, dict) else payload
    rows = []
    for period in periods:
        intensity = period.get("intensity") or {}
        rows.append(
            {
                "from": _to_utc_ts(period.get("from")),
                "to": _to_utc_ts(period.get("to")),
                "forecast": intensity.get("forecast"),
                "actual": intensity.get("actual"),
                "index": intensity.get("index"),
            }
        )
    df = pd.DataFrame(rows, columns=["from", "to", "forecast", "actual", "index"])
    return _finalize_national(df)


def _finalize_national(df: pd.DataFrame) -> pd.DataFrame:
    if not df.empty:
        df["from"] = pd.to_datetime(df["from"], utc=True)
        df["to"] = pd.to_datetime(df["to"], utc=True)
        for col in ("forecast", "actual"):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        df = df.sort_values("from").drop_duplicates("from").reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# regional (snapshot + fw48h share the same period/regions shape)
# --------------------------------------------------------------------------- #
def _parse_region_entry(
    period_from: Optional[pd.Timestamp],
    period_to: Optional[pd.Timestamp],
    region: dict[str, Any],
) -> dict[str, Any]:
    intensity = region.get("intensity") or {}
    row: dict[str, Any] = {
        "from": period_from,
        "to": period_to,
        "regionid": region.get("regionid"),
        "dnoregion": region.get("dnoregion"),
        "shortname": region.get("shortname"),
        "forecast": intensity.get("forecast"),
        "index": intensity.get("index"),
    }
    for fuel in region.get("generationmix") or []:
        name = fuel.get("fuel")
        if name:
            row[f"{name}_perc"] = fuel.get("perc")
    return row


def parse_regional(payload: dict[str, Any]) -> pd.DataFrame:
    """Parse ``/regional`` or ``/regional/intensity/{from}/fw48h`` JSON.

    Both endpoints share the shape ``{"data": [{"from", "to", "regions": [...]}]}``
    where each region carries a forecast and a generation mix. Regional data is
    FORECAST ONLY - there is no ``actual`` column.
    """
    data = payload["data"]
    periods = data if isinstance(data, list) else [data]
    rows = []
    for period in periods:
        period_from = _to_utc_ts(period.get("from"))
        period_to = _to_utc_ts(period.get("to"))
        for region in period.get("regions") or []:
            rows.append(_parse_region_entry(period_from, period_to, region))

    df = pd.DataFrame(rows)
    return _finalize_regional(df)


def _finalize_regional(df: pd.DataFrame) -> pd.DataFrame:
    base_cols = ["from", "to", "regionid", "dnoregion", "shortname", "forecast", "index"]
    if df.empty:
        return pd.DataFrame(columns=base_cols)
    df["from"] = pd.to_datetime(df["from"], utc=True)
    df["to"] = pd.to_datetime(df["to"], utc=True)
    df["forecast"] = pd.to_numeric(df["forecast"], errors="coerce").astype("Int64")
    df["regionid"] = pd.to_numeric(df["regionid"], errors="coerce").astype("Int64")
    fuel_cols = sorted(c for c in df.columns if c.endswith("_perc"))
    df = df[base_cols + fuel_cols]
    return df.sort_values(["from", "regionid"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# carbon factors
# --------------------------------------------------------------------------- #
def parse_factors(payload: dict[str, Any]) -> pd.DataFrame:
    """Parse ``/intensity/factors`` JSON into a tidy (fuel, gco2_per_kwh) table."""
    data = payload["data"]
    record = data[0] if isinstance(data, list) else data
    rows = [
        {"fuel": fuel, "gco2_per_kwh": pd.to_numeric(value, errors="coerce")}
        for fuel, value in record.items()
    ]
    df = pd.DataFrame(rows, columns=["fuel", "gco2_per_kwh"])
    return df.sort_values("fuel").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# consolidation (raw snapshots -> processed tidy tables)
# --------------------------------------------------------------------------- #
def _read_parquet_dir(directory: Path) -> pd.DataFrame:
    if not directory.exists():
        return pd.DataFrame()
    frames = [pd.read_parquet(p) for p in sorted(directory.glob("*.parquet"))]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def consolidate(
    raw_regional_dir: Path = config.RAW_REGIONAL_FW48H_DIR,
    raw_national_dir: Path = config.RAW_NATIONAL_DIR,
    processed_dir: Path = config.PROCESSED_DIR,
) -> dict[str, Path]:
    """Read raw Parquet snapshots and write de-duplicated tidy outputs.

    Returns a mapping of logical name -> written path. National rows are
    de-duplicated on the period; regional forecast rows keep the *latest*
    forecast collected for each (period, region) so the processed table holds
    one row per period/region.
    """
    processed_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    national = _read_parquet_dir(raw_national_dir)
    if not national.empty:
        national = _finalize_national(national)
        written["national"] = _write_tidy(national, processed_dir / "national")

    regional = _read_parquet_dir(raw_regional_dir)
    if not regional.empty:
        # Latest forecast wins for each (period, region).
        sort_cols = ["from", "regionid"]
        if "collected_at" in regional.columns:
            regional = regional.sort_values("collected_at")
        regional = regional.drop_duplicates(sort_cols, keep="last")
        regional = regional.sort_values(sort_cols).reset_index(drop=True)
        written["regional"] = _write_tidy(regional, processed_dir / "regional_forecast")

    return written


def _write_tidy(df: pd.DataFrame, stem: Path) -> Path:
    """Write ``df`` as both Parquet and CSV; return the Parquet path."""
    parquet_path = stem.with_suffix(".parquet")
    df.to_parquet(parquet_path, index=False)
    df.to_csv(stem.with_suffix(".csv"), index=False)
    return parquet_path


def utc_now() -> datetime:
    """Current UTC time (wrapped for easy patching in tests)."""
    return datetime.now(timezone.utc)
