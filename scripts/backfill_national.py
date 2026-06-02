#!/usr/bin/env python3
"""One-off backfill of the national forecast+actual series.

Pulls /intensity/{from}/{to} over an arbitrary date range (chunked under the
14-day cap) into data/raw/national/.

Examples:
    # explicit range
    python3 scripts/backfill_national.py --from 2024-01-01 --to 2024-03-31
    # trailing N days up to now
    python3 scripts/backfill_national.py --days 30

Dates are UTC. Accept YYYY-MM-DD or full ISO ``YYYY-MM-DDTHH:MM``.
Carbon Intensity data (c) NESO, licensed under CC BY 4.0 (see ATTRIBUTION.md).
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carbon_aware_ai.collector import backfill_national  # noqa: E402


def _parse_dt(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"unrecognised datetime: {value!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", type=_parse_dt, help="start (UTC)")
    parser.add_argument("--to", dest="end", type=_parse_dt, help="end (UTC)")
    parser.add_argument(
        "--days", type=int, help="trailing N days up to now (alternative to --from/--to)"
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.days is not None:
        start, end = now - timedelta(days=args.days), now
    elif args.start and args.end:
        start, end = args.start, args.end
    else:
        parser.error("provide either --days N or both --from and --to")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    path = backfill_national(start, end)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
