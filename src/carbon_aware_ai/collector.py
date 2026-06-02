"""Collection routines that build a raw data history under ``data/raw/``.

Two jobs:

1. :func:`collect_regional_fw48h` - fetch the 48-hour-ahead regional forecast
   and append it to ``data/raw/regional_fw48h/`` as a timestamped Parquet file.
   Run this on a cron/schedule (e.g. every half hour) so a *forecast history*
   accumulates: each snapshot records what the model predicted, tagged with the
   ``collected_at`` wall-clock time, which later lets us evaluate a
   forecast-driven scheduler against a perfect-hindsight oracle.

2. :func:`backfill_national` - a one-off pull of the national forecast+actual
   series over an arbitrary date range, chunked under the 14-day cap, written
   to ``data/raw/national/``.

Filenames are UTC-timestamped and safe for filesystems (no colons).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config, etl
from .api_client import CarbonIntensityClient

logger = logging.getLogger(__name__)


def _timestamp_slug(dt: datetime) -> str:
    """UTC timestamp slug for filenames, e.g. ``20260601T1230Z``."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def collect_regional_fw48h(
    client: CarbonIntensityClient | None = None,
    out_dir: Path = config.RAW_REGIONAL_FW48H_DIR,
) -> Path:
    """Fetch the regional 48h forecast and append a timestamped Parquet snapshot.

    Returns the path written.
    """
    client = client or CarbonIntensityClient()
    out_dir.mkdir(parents=True, exist_ok=True)

    collected_at = etl.utc_now()
    payload = client.regional_fw48h(start=collected_at)
    df = etl.parse_regional(payload)

    # Tag every row with the moment we collected it; this column is the spine
    # of the forecast-history analysis later on.
    df.insert(0, "collected_at", pd.Timestamp(collected_at))

    path = out_dir / f"regional_fw48h_{_timestamp_slug(collected_at)}.parquet"
    df.to_parquet(path, index=False)
    logger.info("Wrote %d regional forecast rows to %s", len(df), path)
    return path


def backfill_national(
    start: datetime,
    end: datetime,
    client: CarbonIntensityClient | None = None,
    out_dir: Path = config.RAW_NATIONAL_DIR,
) -> Path:
    """One-off national forecast+actual backfill over [start, end].

    Chunking under the 14-day cap is handled by the client. Returns the path
    of the single Parquet file written for this backfill run.
    """
    client = client or CarbonIntensityClient()
    out_dir.mkdir(parents=True, exist_ok=True)

    periods = client.national_intensity_range(start, end)
    df = etl.parse_national(periods)

    slug = f"{_timestamp_slug(start)}__{_timestamp_slug(end)}"
    path = out_dir / f"national_{slug}.parquet"
    df.to_parquet(path, index=False)
    logger.info("Wrote %d national periods to %s", len(df), path)
    return path
