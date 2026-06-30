#!/usr/bin/env python3
"""Run the temporal carbon-saving analysis on data/processed/national.parquet.

Prints the summary metrics table, the absolute gCO2-avoided per workload, and
writes the four figures to outputs/figures/.

    python3 scripts/run_temporal.py

National series only (the locked methodology); scored on actual gCO2/kWh.
Carbon Intensity data (c) NESO, CC BY 4.0 (see ATTRIBUTION.md).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carbon_aware_ai.analysis import temporal  # noqa: E402


def main() -> int:
    path = temporal.config.PROCESSED_DIR / "national.parquet"
    if not path.exists():
        print(f"Not found: {path}\nRun the backfill + ETL first.")
        return 1

    df = temporal.load_national(path)
    results = temporal.run_analysis(df, windows=temporal.DEFAULT_WINDOWS)

    print("=" * 78)
    print("TEMPORAL CARBON-SAVING ANALYSIS  (national series, scored on actuals)")
    print("=" * 78)
    print(temporal.format_summary_table(results))

    # Per-job capture distribution and weekend/weekday split for the 24h window.
    headline_w = max(results.windows)
    s = results.windows[headline_w]
    if s["n_jobs"]:
        pj = s["per_job_capture"]
        print(f"\nPer-job capture ({headline_w}h, where oracle saving > 0): "
              f"median {pj['median']:.2f}, IQR [{pj['p25']:.2f}, {pj['p75']:.2f}]")
        print(f"\nWeekday/weekend mean saving % ({headline_w}h window):")
        print(s["by_weekend"].to_string(index=False))

    # Absolute gCO2 avoided per workload, using cited per-query energy cases.
    print("\nAbsolute gCO2 avoided by the scheduler (cited per-query energies):")
    arr = results.arrivals[headline_w]
    if not arr.empty:
        for name, spec in temporal.WORKLOADS.items():
            em = temporal.workload_emissions(arr, spec["energy_kwh"])
            print(f"  {name:>24} (energy={spec['energy_kwh']} kWh/job): "
                  f"{em['scheduler_avoided_gco2']:,.1f} g avoided "
                  f"(oracle {em['oracle_avoided_gco2']:,.1f} g) over {len(arr)} jobs")

    figs = temporal.make_figures(results)
    print("\nFigures written:")
    for p in figs:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
