#!/usr/bin/env python3
"""Diagnostic on data/processed/national.parquet.

Reports how much usable history the temporal analysis has:
- total periods and the date range covered
- non-null `actual` vs forecast-only counts
- the most recent period that HAS an actual (recent unsettled periods being
  forecast-only is expected)
- gaps in half-hourly continuity

    python3 scripts/diagnose_national.py
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from carbon_aware_ai import config  # noqa: E402

HALF_HOUR = pd.Timedelta(minutes=30)


def main() -> int:
    path = config.PROCESSED_DIR / "national.parquet"
    if not path.exists():
        print(f"Not found: {path}\nRun the backfill + ETL first.")
        return 1

    df = pd.read_parquet(path).sort_values("from").reset_index(drop=True)
    n = len(df)
    has_actual = df["actual"].notna()
    n_actual = int(has_actual.sum())
    n_forecast_only = n - n_actual

    print("=" * 64)
    print("NATIONAL BACKFILL DIAGNOSTIC - data/processed/national.parquet")
    print("=" * 64)

    if n == 0:
        print("No periods. Backfill is empty.")
        return 1

    span_start, span_end = df["from"].iloc[0], df["from"].iloc[-1]
    span_days = (span_end - span_start) / pd.Timedelta(days=1)
    print(f"Total periods        : {n:,}")
    print(f"Date range (UTC)     : {span_start}  ->  {span_end}")
    print(f"                       ({span_days:.1f} days; {n} half-hours)")
    print()
    print(f"With actual (settled): {n_actual:,}  ({n_actual / n:.1%})")
    print(f"Forecast-only        : {n_forecast_only:,}  ({n_forecast_only / n:.1%})")

    if n_actual:
        last_actual = df.loc[has_actual, "from"].max()
        lag = span_end - last_actual
        print(f"Most recent actual   : {last_actual}  "
              f"({lag / pd.Timedelta(hours=1):.1f}h before series end)")
        # Sanity: are the forecast-only periods all at the tail (as expected)?
        tail_only = bool(df.loc[df["from"] > last_actual, "actual"].isna().all())
        interior_missing = int((~has_actual & (df["from"] <= last_actual)).sum())
        print(f"Forecast-only at tail: {tail_only}  "
              f"(interior missing actuals: {interior_missing})")
    else:
        last_actual = None
        print("Most recent actual   : NONE - no settled periods at all")

    # Half-hourly continuity over the full series.
    deltas = df["from"].diff().dropna()
    gaps = deltas[deltas != HALF_HOUR]
    print()
    print(f"Continuity (30-min)  : {'OK - no gaps' if gaps.empty else f'{len(gaps)} break(s)'}")
    if not gaps.empty:
        missing_total = 0
        for idx, delta in gaps.items():
            prev = df["from"].iloc[idx - 1]
            cur = df["from"].iloc[idx]
            missing = int(delta / HALF_HOUR) - 1
            missing_total += max(missing, 0)
            kind = "overlap/dup" if delta < HALF_HOUR else f"missing {missing} period(s)"
            print(f"   {prev} -> {cur}  (gap {delta}, {kind})")
        print(f"   total missing half-hours: {missing_total}")

    # Usable history for the temporal analysis = the settled, contiguous span.
    print()
    if n_actual:
        usable = df[has_actual]
        u_deltas = usable["from"].diff().dropna()
        u_contig = bool((u_deltas == HALF_HOUR).all())
        print(f"Usable (settled) span: {usable['from'].min()} -> {usable['from'].max()}")
        print(f"   settled periods   : {n_actual:,}  "
              f"(contiguous: {u_contig})")
        for w in (6, 12, 24):
            ok = n_actual >= w * 2
            print(f"   window {w:>2}h needs >= {w*2} settled periods: "
                  f"{'OK' if ok else 'INSUFFICIENT'}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
