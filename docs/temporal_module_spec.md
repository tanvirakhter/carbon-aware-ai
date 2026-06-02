# Build Spec: Temporal Analysis Module (`analysis/temporal.py`)

This specifies the rigorous, publishable core of the study on the **national**
series (which has actual intensity), per the locked methodology note.

---

## Goal

Quantify the real CO2 a flexible AI workload avoids by running at the greenest
half-hour within a flexible window, and measure how much of that saving a
**forecast-driven** scheduler captures versus a **perfect-hindsight oracle**.

## Inputs

- `data/processed/national.parquet` with at least:
  - `period_start` (UTC, half-hourly), `forecast` (gCO2/kWh), `actual` (gCO2/kWh).
- A workload definition: `energy_kwh` (float) - the fixed energy of the job.
  Provide 2-3 presets in a small dict (e.g. a 1M-query inference batch; one
  fine-tuning run). Energy figures sourced from published per-task numbers,
  cited in the paper - NOT measured by this module.
- A flexibility window `window_hours` (e.g. 6, 12, 24) - how long the job can be
  deferred.

## Pre-checks (fail loud, log clearly)

1. Drop any period missing `actual`; log count dropped and date range kept.
2. Assert half-hourly continuity; warn on gaps > 1 period.
3. Require at least `window_hours * 2` periods of history before running.

## Core definitions (per decision point t)

For each period `t` treated as a job-arrival time, define the candidate window
as the next `window_hours` (i.e. `window_hours * 2` half-hour slots from t).

- **Baseline** = run immediately at `t`. Emissions = `actual[t] * energy_kwh`.
- **Oracle** = slot in window with min **actual**. Emissions = `min(actual over window) * energy_kwh`.
- **Scheduler** = slot in window with min **forecast** *as known at t*; emissions
  scored by the **actual** at the slot the scheduler picked (this is the honest
  bit - you decide on forecast, you pay the real cost).

Use the forecast available at decision time. Since `national.parquet` keeps one
forecast per period, document this as a simplification: ideally the scheduler
should use the forecast vintage issued at/just before `t`. If a forecast-history
table exists later, swap it in. For now, note the limitation in the docstring.

## Metrics to compute (per window_hours, aggregated over all valid t)

1. `baseline_gco2`, `oracle_gco2`, `scheduler_gco2` (totals and per-job mean).
2. `oracle_saving_pct` = (baseline - oracle) / baseline * 100  - the theoretical max.
3. `scheduler_saving_pct` = (baseline - scheduler) / baseline * 100  - realised.
4. `capture_ratio` = scheduler_saving / oracle_saving  - **the headline number**:
   what fraction of achievable saving the forecast-driven rule captures.
5. Distributions (not just means): report median, IQR, and 10th/90th percentiles
   across decision points. Means alone hide the variance that makes this interesting.
6. A seasonal/time-of-day breakdown: saving by hour-of-day and by weekday/weekend.

## Functions (suggested signatures)

```python
def load_national(path: str) -> pd.DataFrame: ...
def valid_decision_points(df: pd.DataFrame, window_hours: int) -> pd.DatetimeIndex: ...
def score_window(df, t, window_hours, energy_kwh) -> dict:  # baseline/oracle/scheduler for one t
    ...
def run_temporal(df, window_hours: int, energy_kwh: float) -> pd.DataFrame:  # one row per t
    ...
def summarise(results: pd.DataFrame) -> dict:  # the aggregate metrics above
    ...
```

## Figures to output (save to `outputs/figures/`)

1. **Capture-ratio bar/line vs window length** (6/12/24h) - the key result:
   does more flexibility help, and how much does forecast error cost.
2. **Distribution of scheduler saving %** (histogram or violin) across decision points.
3. **Saving by hour-of-day** (line) - shows when shifting pays off most.
4. **Worked example day**: actual vs forecast intensity curve for one date, with
   baseline / oracle / scheduler slots marked. Great for the paper's intuition.

## Tests (extend the offline suite)

- Synthetic curve with a known minimum -> oracle picks it; saving math exact.
- Case where forecast min != actual min -> scheduler underperforms oracle by the
  expected amount; capture_ratio < 1.
- Window longer than available data -> handled gracefully (skip / warn).
- All-equal-intensity window -> all three equal, savings = 0, no divide-by-zero.

## Honest-framing reminders (put in docstrings)

- Savings are generation-only carbon; exclude embodied hardware carbon and water.
- Scheduler currently scored with one forecast value per period; refine with true
  forecast vintages when forecast history accumulates.
- This module makes no regional/spatial claims - those are descriptive only,
  handled separately per the methodology note.
```
