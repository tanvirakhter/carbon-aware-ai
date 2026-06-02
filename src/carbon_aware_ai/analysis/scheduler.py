"""Forecast-driven scheduler vs perfect-hindsight oracle (SCAFFOLD).

Question
--------
A realistic scheduler only sees the *forecast* available at decision time; an
oracle sees the *actuals*. How much of the achievable carbon saving does a
forecast-only planner actually capture (the "regret")?

This is why the collector snapshots the fw48h regional forecast with a
``collected_at`` timestamp: it reconstructs exactly what was knowable at each
decision point.

Inputs:
    * data/raw/regional_fw48h/*.parquet - forecast history (what was knowable)
    * data/processed/national.parquet   - actuals (ground truth, national)

Planned API
-----------
    forecast_schedule(forecast_history, job, decision_time) -> chosen_slot
    oracle_schedule(actuals, job) -> best_possible_slot
    regret(forecast_choice, oracle_choice) -> gCO2/kWh left on the table

TODO:
    * Define the decision protocol (when the planner commits, time+region).
    * Join forecast-at-decision-time to realised intensity for the chosen slot.
    * Compute oracle optimum over the same flexibility window.
    * Report capture ratio = (baseline - forecast) / (baseline - oracle).
    * Caveat: regional has no actuals; oracle for the spatial dimension may
      need a proxy or be restricted to the national/temporal dimension.
"""

from __future__ import annotations

# TODO: implement after temporal & spatial analyses and enough forecast history.
