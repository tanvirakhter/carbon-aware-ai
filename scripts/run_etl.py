#!/usr/bin/env python3
"""Consolidate raw Parquet snapshots into tidy processed tables.

Reads data/raw/{regional_fw48h,national}/*.parquet and writes de-duplicated
tidy Parquet + CSV to data/processed/.

    python3 scripts/run_etl.py

Carbon Intensity data (c) NESO, licensed under CC BY 4.0 (see ATTRIBUTION.md).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carbon_aware_ai.etl import consolidate  # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    written = consolidate()
    if not written:
        print("No raw data found yet - run the collector / backfill first.")
        return 0
    for name, path in written.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
