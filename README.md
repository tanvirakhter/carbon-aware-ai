# carbon-aware-ai

How much CO2 can an AI workload avoid by running it at the **greenest time and
region in Great Britain**? This project builds the data pipeline to find out,
using the **NESO Carbon Intensity API**.

The plan: capture both *what actually happened* (national forecast **and**
actual carbon intensity) and *what was knowable in advance* (regional 48-hour
**forecasts**, snapshotted over time), then measure the carbon saving available
from shifting a workload in **time**, in **space (region)**, and from a
realistic **forecast-driven scheduler** versus a perfect-hindsight oracle.

**What it measures.** On the GB national series, the analysis quantifies how much
of the achievable (perfect-hindsight oracle) carbon saving a realistic
forecast-driven scheduler captures across flexibility windows of 6, 12, and 24
hours. See [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) for the method. Numerical
results are reported in the accompanying paper; regenerate the figures locally
with the command below.

> Carbon Intensity data provided by the National Energy System Operator (NESO),
> licensed under CC BY 4.0. See [ATTRIBUTION.md](ATTRIBUTION.md). **Always
> attribute NESO.**

## Data source

[`https://api.carbonintensity.org.uk`](https://api.carbonintensity.org.uk) - no
authentication. Key facts the code is built around:

| Endpoint | Returns | Notes |
|---|---|---|
| `/intensity/{from}/{to}` | National forecast **+ actual** gCO2/kWh | **14-day** cap per call -> chunked automatically |
| `/intensity/factors` | Per-fuel gCO2/kWh factors | - |
| `/regional` | Current regional forecast | **Forecast only** |
| `/regional/intensity/{from}/fw48h` | 48h-ahead regional forecast | **Forecast only**, 14 DNO regions + England/Scotland/Wales aggregates |

All timestamps are **UTC**, half-hourly. The API datetime format is
`YYYY-MM-DDThh:mmZ`.

## Project layout

```
carbon-aware-ai/
+-- src/carbon_aware_ai/
|   +-- config.py          # paths, API constants, region reference table
|   +-- api_client.py      # polite client: backoff + 14-day chunking
|   +-- collector.py       # build raw history (regional fw48h + national backfill)
|   +-- etl.py             # parse raw JSON -> tidy tables; consolidate raw -> processed
|   +-- analysis/          # temporal.py (implemented); spatial.py, scheduler.py (scaffold/TODO)
+-- scripts/
|   +-- collect_regional.py   # cron entrypoint: append a regional fw48h snapshot
|   +-- backfill_national.py  # one-off national forecast+actual backfill
|   +-- run_etl.py            # consolidate raw -> processed tidy Parquet/CSV
|   +-- diagnose_national.py  # sanity-check national history coverage/gaps
|   +-- run_temporal.py       # run temporal analysis; print metrics; write figures
+-- tests/                 # offline pytest suite + mock JSON fixtures
+-- data/                  # all collected/derived data is git-ignored
|   +-- raw/               # timestamped Parquet snapshots accumulate here (git-ignored)
|   +-- processed/         # tidy, de-duplicated half-hourly tables (git-ignored)
+-- docs/                  # METHODOLOGY.md, temporal_module_spec.md
+-- requirements.txt
+-- ATTRIBUTION.md
+-- LICENSE
+-- README.md
```

## Setup

```bash
cd carbon-aware-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

### 1. Start the collector (do this first - data accumulates over time)

Each run appends one timestamped Parquet snapshot of the **regional 48-hour
forecast** to `data/raw/regional_fw48h/`, tagged with `collected_at`. Running
it repeatedly builds the *forecast history* needed to evaluate a scheduler
against an oracle.

```bash
python3 scripts/collect_regional.py
```

Schedule it (e.g. every 30 minutes) via cron:

```cron
*/30 * * * * cd /path/to/carbon-aware-ai && /path/to/.venv/bin/python scripts/collect_regional.py >> collector.log 2>&1
```

### 2. Backfill national history (one-off)

National data includes **actuals**, so backfill as far as you need. The 14-day
cap is handled by chunking.

```bash
python3 scripts/backfill_national.py --days 30
# or an explicit UTC range:
python3 scripts/backfill_national.py --from 2024-01-01 --to 2024-03-31
```

### 3. Consolidate into tidy tables

```bash
python3 scripts/run_etl.py
```

Writes de-duplicated, half-hourly **Parquet + CSV** to `data/processed/`:

- `national.parquet` / `.csv` - `from, to, forecast, actual, index`
- `regional_forecast.parquet` / `.csv` - `from, to, regionid, dnoregion,
  shortname, forecast, index, <fuel>_perc...` (one row per period/region, latest
  forecast retained)

## Tests

The suite runs fully **offline** against mock JSON matching the API's exact
schema, so parsing is validated without network access.

```bash
pytest
```

## Analysis

See `src/carbon_aware_ai/analysis/` and [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

### Temporal saving + forecast-scheduler vs oracle (implemented)

**`temporal.py`** runs on the **national series only** (which has actuals).
For each half-hour treated as a job arrival, over windows of 6/12/24h, it
compares: *baseline* (run now), *oracle* (min actual in window), and a
*scheduler* that picks the min-**forecast** slot but pays the **actual** there.
Headline metric: **capture ratio** = sum of scheduler saving / sum of oracle saving.

```bash
# first sanity-check the national history:
python3 scripts/diagnose_national.py
# then run the analysis (prints the metrics table, writes figures):
python3 scripts/run_temporal.py
```

#### Reproducing the paper figures

`outputs/` is git-ignored (figures are derived, not source). **All four paper
figures regenerate with a single command:**

```bash
python3 scripts/run_temporal.py   # writes outputs/figures/fig1..fig4 .png
```

They are: (1) capture-ratio vs window, (2) scheduler-saving distribution,
(3) saving by hour-of-day, and (4) a worked-example day. Per-query energy
values (0.34 Wh, 0.43 Wh, 4.3 Wh) are illustrative constants drawn from the
cited literature; all headline results are ratios and are independent of this
value, which affects only absolute gCO2 figures.

Fresh clone with no data? The national series carries actuals and is re-pullable
from the public NESO API, so backfill it and run the analysis:

```bash
python3 scripts/backfill_national.py --days 30   # or --from/--to a UTC range
python3 scripts/run_etl.py
python3 scripts/run_temporal.py
```

The figures use the national series over the 20-day window **12 May - 1 June
2026 (UTC)**; reproduce exactly that range with
`python3 scripts/backfill_national.py --from 2026-05-12 --to 2026-06-01`.

### Spatial (scaffolded - TODOs)

- **`spatial.py`** - greenest *region* and spatial spread across GB regions
  (forecast only - explicitly caveated, no actuals to settle against).
- **`scheduler.py`** - spatial/combined forecast-vs-oracle scaffolding, using
  the accumulating regional forecast history.

## Data and code

- **Code** in this repository is released under the **MIT License** - see
  [`LICENSE`](LICENSE).
- **Carbon-intensity data** is provided by the National Energy System Operator
  (NESO) via the Carbon Intensity API and is licensed under **CC BY 4.0**. Any
  use of the data must attribute NESO - see [`ATTRIBUTION.md`](ATTRIBUTION.md).

These are distinct: the MIT licence covers the source code only, not the data.
