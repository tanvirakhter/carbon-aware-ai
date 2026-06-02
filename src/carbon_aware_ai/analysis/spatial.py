"""Spatial saving: greenest REGION at a given time (SCAFFOLD).

Question
--------
At a fixed time, how much CO2 is avoided by placing the workload in the
greenest of the GB regions versus a default/home region? How wide is the
spatial spread (max - min intensity across regions) and how does it vary by
hour and season?

Inputs (from data/processed/):
    * regional_forecast.parquet - per period/region forecast (FORECAST ONLY)

Planned API
-----------
    spatial_spread(regional_df) -> per-period (min, max, greenest_region)
    spatial_saving(regional_df, home_region) -> grams CO2 avoided per kWh

TODO:
    * Pivot regional forecast to period x region intensity matrix.
    * Exclude/flag aggregate regions (England/Scotland/Wales/GB) vs DNO areas.
    * Quantify spread distribution; identify consistently-green regions.
    * Note: regional series is forecast-only - there is no actual to compare
      against, so spatial "realised" saving must be caveated.
"""

from __future__ import annotations

# TODO: implement once regional forecast history has accumulated.
