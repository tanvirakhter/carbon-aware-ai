#!/usr/bin/env python3
"""Cron-friendly entrypoint: snapshot the regional 48h forecast.

Appends one timestamped Parquet file to data/raw/regional_fw48h/ per run.
Schedule it (e.g. every 30 minutes) to accumulate a forecast history.

Example crontab (every half hour):
    */30 * * * * cd /path/to/carbon-aware-ai && \
        /usr/bin/env python3 scripts/collect_regional.py >> collector.log 2>&1

Carbon Intensity data (c) NESO, licensed under CC BY 4.0 (see ATTRIBUTION.md).
"""

import logging
import sys
from pathlib import Path

# Make the src/ layout importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carbon_aware_ai.collector import collect_regional_fw48h  # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    path = collect_regional_fw48h()
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
