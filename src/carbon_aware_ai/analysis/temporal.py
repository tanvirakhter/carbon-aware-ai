"""Temporal carbon-saving analysis (national series only).

Locked methodology (see ``docs/METHODOLOGY.md`` and ``docs/temporal_module_spec.md``):
this module runs ONLY on the national series, which carries ``actual``
gCO2/kWh alongside ``forecast``. It makes **no regional or spatial claims**.

Model
-----
Every half-hourly period ``t`` is treated as a job arrival with a flexibility
window of the next ``window_hours`` (i.e. ``W = window_hours * 2`` half-hour
slots, slot 0 being "run immediately"). For each arrival we compare three
policies, all *scored on the realised ACTUAL intensity*:

* **Baseline**  - run immediately at ``t``:            cost = actual[t]
* **Oracle**    - perfect hindsight: the window slot with the minimum ACTUAL.
* **Scheduler** - realistic: pick the slot with the minimum FORECAST known at
                  ``t``, but pay the ACTUAL intensity of that chosen slot
                  (decide on forecast, pay the real cost).

Energy (kWh) cancels in percentage savings, so the per-arrival simulation works
in intensity (gCO2/kWh); workloads only scale the absolute gCO2 numbers.

Headline metric
    capture_ratio = sum of scheduler_saving / sum of oracle_saving
    - the fraction of the achievable (oracle) saving a realistic, forecast-only
    planner actually captures.

Honest-framing caveats (carry these into any write-up)
------------------------------------------------------
* **Generation-only carbon.** Intensity is operational generation gCO2/kWh; it
  EXCLUDES embodied hardware carbon and water. Savings here are operational only.
* **Single forecast vintage.** ``national.parquet`` keeps ONE forecast value per
  period, so the scheduler is scored against that single value rather than the
  forecast *vintage* actually issued at/just before ``t``. This is a documented
  simplification; swap in a forecast-history table (the regional collector is
  already accumulating one) to refine it.
* **No regional/spatial claims.** Regional data is forecast-only; spatial
  questions are descriptive and handled separately (``analysis/spatial.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .. import config

logger = logging.getLogger(__name__)

HALF_HOUR = pd.Timedelta(minutes=30)
HALF_HOUR_NP = np.timedelta64(30, "m")
DEFAULT_WINDOWS = (6, 12, 24)

# Worked-example arrival used for Fig. 4 (the representative day in the write-up):
# a 24h-window early-morning arrival sitting at the daily high-carbon level. We
# pin it so the figure is reproducible run-to-run rather than tracking whichever
# arrival happens to have the largest oracle saving in a given pull; if the
# timestamp is absent (e.g. a different date range) the analysis falls back to
# the max-oracle-saving arrival automatically.
WORKED_EXAMPLE_ARRIVAL = "2026-05-18 04:30"

# Canonical period-start column used throughout the module. The processed ETL
# output uses "from"; the spec/paper vocabulary is "period_start". load_national
# normalises any of these to PERIOD_COL.
PERIOD_COL = "period_start"
_PERIOD_ALIASES = ("period_start", "from", "period", "datetime", "timestamp")


# --------------------------------------------------------------------------- #
# Workload presets
# --------------------------------------------------------------------------- #
# Per-query energy cases used ONLY to scale the percentage / capture results
# into absolute gCO2 for illustration. These are cited, published per-query
# figures (NOT measured by this module); the percentage and capture metrics are
# independent of them, so they affect only the absolute gCO2 numbers.
WORKLOADS: dict[str, dict] = {
    "optimised_frontier_query": {
        "energy_kwh": 0.34e-3,  # 0.34 Wh - optimised frontier-scale inference (Oviedo et al.)
        "note": "Single optimised frontier-scale inference query (0.34 Wh).",
    },
    "short_query": {
        "energy_kwh": 0.43e-3,  # 0.43 Wh - short query (Jegham et al.)
        "note": "Single short inference query (0.43 Wh).",
    },
    "long_reasoning_query": {
        "energy_kwh": 4.3e-3,  # ~4.3 Wh - long / reasoning query (Oviedo et al.)
        "note": "Single long / reasoning inference query (~4.3 Wh).",
    },
}


# --------------------------------------------------------------------------- #
# Loading / pre-checks
# --------------------------------------------------------------------------- #
def load_national(path: Optional[Path | str] = None) -> pd.DataFrame:
    """Load the tidy national table, normalised to use ``period_start``.

    Accepts a processed file whose period column is any of
    ``period_start`` / ``from`` / ``period`` / ``datetime`` / ``timestamp``.
    """
    path = Path(path) if path else (config.PROCESSED_DIR / "national.parquet")
    df = pd.read_parquet(path)
    return _normalise_period_col(df)


def _normalise_period_col(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if PERIOD_COL not in df.columns:
        for alias in _PERIOD_ALIASES:
            if alias in df.columns:
                df = df.rename(columns={alias: PERIOD_COL})
                break
        else:
            raise KeyError(
                f"national frame has no period column; expected one of {_PERIOD_ALIASES}"
            )
    df[PERIOD_COL] = pd.to_datetime(df[PERIOD_COL], utc=True)
    return df.sort_values(PERIOD_COL).reset_index(drop=True)


def prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop forecast-only periods, check continuity, return (clean, n_dropped).

    Periods without an ``actual`` (typically recent, unsettled half-hours at the
    tail) are removed because every policy is scored on actuals. Continuity is
    checked and any gaps are logged loudly as warnings - but they are NOT fatal:
    :func:`simulate` is gap-aware and never scores a window that spans a gap, so
    a single hole degrades coverage gracefully instead of aborting the run.
    """
    df = _normalise_period_col(df)
    n_before = len(df)
    clean = (
        df[df["actual"].notna()].sort_values(PERIOD_COL).reset_index(drop=True)
    )
    n_dropped = n_before - len(clean)

    if clean.empty:
        logger.warning("prepare: no settled periods (all rows missing 'actual').")
        return clean, n_dropped

    kept_lo, kept_hi = clean[PERIOD_COL].iloc[0], clean[PERIOD_COL].iloc[-1]
    logger.info(
        "prepare: dropped %d forecast-only period(s); kept %d settled, %s -> %s.",
        n_dropped, len(clean), kept_lo, kept_hi,
    )

    if len(clean) >= 2:
        deltas = clean[PERIOD_COL].diff().dropna()
        gaps = deltas[deltas != HALF_HOUR]
        if not gaps.empty:
            largest = gaps.max()
            logger.warning(
                "prepare: %d gap(s) in half-hourly continuity (largest %s); "
                "windows spanning a gap will be skipped.",
                len(gaps), largest,
            )
    return clean, n_dropped


# --------------------------------------------------------------------------- #
# Core simulation
# --------------------------------------------------------------------------- #
def _window_is_contiguous(starts: np.ndarray) -> bool:
    """True if a slice of period starts is contiguous at 30-min spacing."""
    if len(starts) < 2:
        return True
    return bool((np.diff(starts) == HALF_HOUR_NP).all())


def valid_decision_points(df: pd.DataFrame, window_hours: int) -> pd.DatetimeIndex:
    """Arrival times whose full window is contiguous and fully populated.

    A decision point ``t`` is valid iff the ``window_hours*2`` slots from ``t``
    are 30-min contiguous and every slot has a non-null ``forecast`` and
    ``actual``.
    """
    df = _normalise_period_col(df)
    W = window_hours * 2
    n = len(df)
    if n < W:
        return pd.DatetimeIndex([], tz="UTC")

    actual = df["actual"].to_numpy(dtype="float64")
    forecast = df["forecast"].to_numpy(dtype="float64")
    starts = df[PERIOD_COL].to_numpy(dtype="datetime64[ns]")

    valid = []
    for i in range(n - W + 1):
        seg = starts[i : i + W]
        if not _window_is_contiguous(seg):
            continue
        if np.isnan(actual[i : i + W]).any() or np.isnan(forecast[i : i + W]).any():
            continue
        valid.append(starts[i])
    return pd.DatetimeIndex(valid, tz="UTC")


def score_window(
    df: pd.DataFrame, t, window_hours: int, energy_kwh: float = 1.0
) -> dict:
    """Baseline / oracle / scheduler outcome for a single arrival ``t``.

    Returns intensities (gCO2/kWh) and energy-scaled emissions (gCO2). Raises
    ``ValueError`` if ``t`` has no valid full window.
    """
    df = _normalise_period_col(df)
    t = pd.Timestamp(t)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    W = window_hours * 2
    locs = df.index[df[PERIOD_COL] == t]
    if len(locs) == 0:
        raise ValueError(f"arrival {t} not found in series")
    i = int(locs[0])

    starts = df[PERIOD_COL].to_numpy(dtype="datetime64[ns]")[i : i + W]
    a = df["actual"].to_numpy(dtype="float64")[i : i + W]
    f = df["forecast"].to_numpy(dtype="float64")[i : i + W]
    if len(a) < W or not _window_is_contiguous(starts) or np.isnan(a).any() or np.isnan(f).any():
        raise ValueError(f"arrival {t} has no valid contiguous {window_hours}h window")

    baseline = float(a[0])
    oracle = float(a.min())
    pick = int(np.argmin(f))
    scheduler = float(a[pick])
    return {
        "arrival": t,
        "pick_offset": pick,
        "baseline": baseline,
        "oracle": oracle,
        "scheduler": scheduler,
        "baseline_gco2": baseline * energy_kwh,
        "oracle_gco2": oracle * energy_kwh,
        "scheduler_gco2": scheduler * energy_kwh,
    }


def simulate(df: pd.DataFrame, window_hours: int) -> pd.DataFrame:
    """Run the baseline/oracle/scheduler comparison for one window length.

    Gap-aware: only scores windows that are 30-min contiguous and fully
    populated (see :func:`valid_decision_points`). Returns one row per valid
    arrival; empty if no valid window exists (e.g. series shorter than the
    window) - graceful, not an error.
    """
    df = _normalise_period_col(df)
    W = window_hours * 2
    n = len(df)
    if n < W:
        return _empty_arrivals()

    actual = df["actual"].to_numpy(dtype="float64")
    forecast = df["forecast"].to_numpy(dtype="float64")
    starts = df[PERIOD_COL].to_numpy(dtype="datetime64[ns]")

    records = []
    for i in range(n - W + 1):
        seg = starts[i : i + W]
        a = actual[i : i + W]
        f = forecast[i : i + W]
        if not _window_is_contiguous(seg):
            continue
        if np.isnan(a).any() or np.isnan(f).any():
            continue
        baseline = a[0]
        oracle = a.min()
        pick = int(np.argmin(f))  # ties -> earliest slot
        records.append((starts[i], baseline, oracle, a[pick], pick))

    cols = ["arrival", "baseline", "oracle", "scheduler", "pick_offset"]
    arr = pd.DataFrame.from_records(records, columns=cols)
    if arr.empty:
        return _empty_arrivals()

    arr["arrival"] = pd.to_datetime(arr["arrival"], utc=True)
    arr["hour"] = arr["arrival"].dt.hour
    arr["weekday"] = arr["arrival"].dt.weekday
    arr["is_weekend"] = arr["weekday"] >= 5

    arr["oracle_saving"] = arr["baseline"] - arr["oracle"]
    arr["scheduler_saving"] = arr["baseline"] - arr["scheduler"]
    # Percentages guard a zero baseline (no saving definable against 0 gCO2/kWh).
    safe_base = arr["baseline"].replace(0, np.nan)
    arr["oracle_saving_pct"] = (arr["oracle_saving"] / safe_base * 100).fillna(0.0)
    arr["scheduler_saving_pct"] = (arr["scheduler_saving"] / safe_base * 100).fillna(0.0)
    return arr


def run_temporal(
    df: pd.DataFrame, window_hours: int, energy_kwh: float = 1.0
) -> pd.DataFrame:
    """Per-arrival results for one window, with energy-scaled gCO2 columns.

    Thin wrapper over :func:`simulate` matching the spec's signature; adds
    ``baseline_gco2`` / ``oracle_gco2`` / ``scheduler_gco2`` = intensity x
    ``energy_kwh``.
    """
    arr = simulate(df, window_hours)
    if not arr.empty:
        for pol in ("baseline", "oracle", "scheduler"):
            arr[f"{pol}_gco2"] = arr[pol] * energy_kwh
    return arr


def _empty_arrivals() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "arrival", "baseline", "oracle", "scheduler", "pick_offset",
            "hour", "weekday", "is_weekend",
            "oracle_saving", "scheduler_saving",
            "oracle_saving_pct", "scheduler_saving_pct",
        ]
    )


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _dist(series: pd.Series) -> dict[str, float]:
    """Distribution summary: mean, median, IQR, 10th/90th percentiles."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {k: float("nan") for k in ("mean", "median", "p10", "p25", "p75", "p90", "iqr")}
    p25, p75 = float(s.quantile(0.25)), float(s.quantile(0.75))
    return {
        "mean": float(s.mean()),
        "median": float(s.median()),
        "p10": float(s.quantile(0.10)),
        "p25": p25,
        "p75": p75,
        "p90": float(s.quantile(0.90)),
        "iqr": p75 - p25,
    }


def capture_ratio(arrivals: pd.DataFrame) -> float:
    """Headline capture ratio = sum of scheduler_saving / sum of oracle_saving.

    Returns NaN when no oracle saving is available (e.g. an all-equal window),
    which avoids any divide-by-zero.
    """
    denom = float(arrivals["oracle_saving"].sum())
    if denom == 0:
        return float("nan")
    return float(arrivals["scheduler_saving"].sum()) / denom


def summarize(arrivals: pd.DataFrame) -> dict:
    """Aggregate metrics for one window's arrivals.

    Includes distributions (median, IQR, 10th/90th percentiles - not just
    means) and breakdowns by hour-of-day and weekday/weekend. If the frame
    carries ``*_gco2`` columns (from :func:`run_temporal`), gCO2 totals and
    per-job means are included too.
    """
    n = len(arrivals)
    summary: dict = {
        "n_jobs": int(n),
        "capture_ratio": float("nan"),
        "oracle_saving_pct": _dist(pd.Series(dtype="float64")),
        "scheduler_saving_pct": _dist(pd.Series(dtype="float64")),
        "per_job_capture": _dist(pd.Series(dtype="float64")),
        "by_hour": pd.DataFrame(),
        "by_weekend": pd.DataFrame(),
    }
    if n == 0:
        return summary

    summary["capture_ratio"] = capture_ratio(arrivals)
    summary["oracle_saving_pct"] = _dist(arrivals["oracle_saving_pct"])
    summary["scheduler_saving_pct"] = _dist(arrivals["scheduler_saving_pct"])

    # Per-job capture only where an oracle saving exists (avoid 0/0 per row).
    realisable = arrivals[arrivals["oracle_saving"] > 0]
    if not realisable.empty:
        per_job = realisable["scheduler_saving"] / realisable["oracle_saving"]
        summary["per_job_capture"] = _dist(per_job)

    summary["by_hour"] = (
        arrivals.groupby("hour")[["oracle_saving_pct", "scheduler_saving_pct"]]
        .mean()
        .reset_index()
    )
    summary["by_weekend"] = (
        arrivals.groupby("is_weekend")[["oracle_saving_pct", "scheduler_saving_pct"]]
        .mean()
        .reset_index()
    )

    # Optional absolute gCO2 metrics when energy has been applied.
    for pol in ("baseline", "oracle", "scheduler"):
        col = f"{pol}_gco2"
        if col in arrivals.columns:
            summary[f"{pol}_gco2_total"] = float(arrivals[col].sum())
            summary[f"{pol}_gco2_per_job_mean"] = float(arrivals[col].mean())
    return summary


# British-spelling alias to match the spec's suggested signature.
summarise = summarize


def workload_emissions(arrivals: pd.DataFrame, energy_kwh: float) -> dict[str, float]:
    """Absolute gCO2 totals/avoided for a workload of ``energy_kwh`` per job."""
    if arrivals.empty:
        return {"baseline_gco2": 0.0, "oracle_gco2": 0.0, "scheduler_gco2": 0.0,
                "scheduler_avoided_gco2": 0.0, "oracle_avoided_gco2": 0.0}
    base = float(arrivals["baseline"].sum()) * energy_kwh
    orac = float(arrivals["oracle"].sum()) * energy_kwh
    sched = float(arrivals["scheduler"].sum()) * energy_kwh
    return {
        "baseline_gco2": base,
        "oracle_gco2": orac,
        "scheduler_gco2": sched,
        "scheduler_avoided_gco2": base - sched,
        "oracle_avoided_gco2": base - orac,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclass
class TemporalResults:
    """Container for a full multi-window run."""

    n_dropped_forecast_only: int
    n_settled: int
    windows: dict[int, dict] = field(default_factory=dict)        # window_hours -> summary
    arrivals: dict[int, pd.DataFrame] = field(default_factory=dict)  # window_hours -> per-arrival
    skipped: list[int] = field(default_factory=list)              # windows with too little data
    settled: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())  # clean series used


def run_analysis(
    df: pd.DataFrame,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
) -> TemporalResults:
    """Run :func:`prepare` + :func:`simulate` + :func:`summarize` per window."""
    clean, n_dropped = prepare(df)
    results = TemporalResults(
        n_dropped_forecast_only=n_dropped, n_settled=len(clean), settled=clean
    )
    for w in windows:
        if len(clean) < w * 2:
            results.skipped.append(w)
            results.windows[w] = summarize(_empty_arrivals())
            results.arrivals[w] = _empty_arrivals()
            continue
        arr = simulate(clean, w)
        if arr.empty:
            results.skipped.append(w)
        results.arrivals[w] = arr
        results.windows[w] = summarize(arr)
    return results


def format_summary_table(results: TemporalResults) -> str:
    """Render a compact, human-readable metrics table."""
    lines = []
    lines.append(
        f"settled periods: {results.n_settled}  "
        f"(dropped {results.n_dropped_forecast_only} forecast-only)"
    )
    if results.skipped:
        lines.append(f"skipped windows (insufficient/empty data): {results.skipped}")
    header = (
        f"{'window':>7} | {'jobs':>5} | {'capture':>7} | "
        f"{'sched% (med, p10-p90)':>26} | {'oracle% (med, p10-p90)':>26}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for w, s in results.windows.items():
        if s["n_jobs"] == 0:
            lines.append(f"{w:>5}h | {'-':>5} | {'-':>7} | {'(no data)':>26} | {'':>26}")
            continue
        sp, op = s["scheduler_saving_pct"], s["oracle_saving_pct"]
        cap = s["capture_ratio"]
        cap_s = "nan" if cap != cap else f"{cap:6.1%}"
        sched_s = f"{sp['median']:5.1f}  [{sp['p10']:5.1f},{sp['p90']:5.1f}]"
        orac_s = f"{op['median']:5.1f}  [{op['p10']:5.1f},{op['p90']:5.1f}]"
        lines.append(
            f"{w:>5}h | {s['n_jobs']:>5} | {cap_s:>7} | {sched_s:>26} | {orac_s:>26}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #
def _figures_dir() -> Path:
    d = config.PROJECT_ROOT / "outputs" / "figures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_figures(
    results: TemporalResults,
    out_dir: Optional[Path] = None,
    worked_example_arrival: Optional[pd.Timestamp | str] = WORKED_EXAMPLE_ARRIVAL,
) -> list[Path]:
    """Write the four analysis figures. Returns the paths written.

    ``worked_example_arrival`` pins Fig. 4 to a specific decision point for a
    reproducible figure; pass ``None`` to let it pick the max-oracle-saving day.
    Uses the non-interactive Agg backend so it runs headless.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = out_dir or _figures_dir()
    written: list[Path] = []
    active = [w for w in results.windows if results.windows[w]["n_jobs"] > 0]
    if not active:
        return written

    # (1) capture ratio vs window length
    fig, ax = plt.subplots(figsize=(6, 4))
    ws = sorted(active)
    caps = [results.windows[w]["capture_ratio"] * 100 for w in ws]
    ax.plot(ws, caps, "o-", color="#2a7", lw=2)
    ax.set_xlabel("flexibility window (hours)")
    ax.set_ylabel("capture ratio (%)")
    ax.set_title("Forecast scheduler captures of oracle saving, by window")
    ax.set_xticks(ws)
    ax.grid(alpha=0.3)
    p = out_dir / "fig1_capture_ratio_vs_window.png"
    fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig); written.append(p)

    # (2) distribution of scheduler saving %
    fig, ax = plt.subplots(figsize=(6, 4))
    for w in ws:
        vals = results.arrivals[w]["scheduler_saving_pct"]
        ax.hist(vals, bins=40, histtype="step", lw=1.8, label=f"{w}h")
    ax.set_xlabel("scheduler saving (%)")
    ax.set_ylabel("number of job arrivals")
    ax.set_title("Distribution of scheduler saving %")
    ax.legend(title="window")
    ax.grid(alpha=0.3)
    p = out_dir / "fig2_scheduler_saving_distribution.png"
    fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig); written.append(p)

    # (3) saving by hour-of-day (longest window)
    w = max(ws)
    by_hour = results.windows[w]["by_hour"]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(by_hour["hour"], by_hour["oracle_saving_pct"], "o--",
            color="#888", label="oracle")
    ax.plot(by_hour["hour"], by_hour["scheduler_saving_pct"], "o-",
            color="#2a7", label="scheduler")
    ax.set_xlabel("hour of day (UTC) at job arrival")
    ax.set_ylabel("mean saving (%)")
    ax.set_title(f"Saving by hour of day ({w}h window)")
    ax.set_xticks(range(0, 24, 2))
    ax.legend(); ax.grid(alpha=0.3)
    p = out_dir / "fig3_saving_by_hour.png"
    fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig); written.append(p)

    # (4) worked-example day: actual vs forecast curves + policy slots
    p4 = _worked_example_figure(results, out_dir, plt, worked_example_arrival)
    if p4 is not None:
        written.append(p4)
    return written


def _worked_example_figure(results, out_dir, plt, arrival=None):
    """One arrival's window: actual & forecast curves, baseline/oracle/scheduler.

    ``arrival`` pins the example to a specific decision point; when it is None or
    not a valid decision point in this dataset we fall back to the arrival with
    the largest oracle saving (the most illustrative day available).
    """
    w = max(w for w in results.windows if results.windows[w]["n_jobs"] > 0)
    arr = results.arrivals[w]
    if arr.empty:
        return None
    pick_row = None
    if arrival is not None:
        t = pd.Timestamp(arrival)
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        match = arr[arr["arrival"] == t]
        if not match.empty:
            pick_row = match.iloc[0]
        else:
            logger.warning(
                "worked-example arrival %s is not a valid %dh decision point; "
                "falling back to the max-oracle-saving arrival.", t, w,
            )
    if pick_row is None:
        # Pick an arrival with a meaningful oracle saving so the example is illustrative.
        pick_row = arr.loc[arr["oracle_saving"].idxmax()]
    return _plot_worked_example(results, w, pick_row, out_dir, plt)


def _plot_worked_example(results, window_hours, row, out_dir, plt):
    settled = results.settled
    if settled is None or settled.empty:
        return None
    W = window_hours * 2
    idx = settled.index[settled[PERIOD_COL] == row["arrival"]]
    if len(idx) == 0:
        return None
    i = int(idx[0])
    window = settled.iloc[i : i + W]
    offsets = np.arange(len(window)) * 0.5  # hours from arrival

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(offsets, window["actual"], "o-", color="#333", label="actual", ms=3)
    ax.plot(offsets, window["forecast"], "s--", color="#c93", label="forecast", ms=3)

    base_x, base_y = 0.0, float(window["actual"].iloc[0])
    o_off = int(np.argmin(window["actual"].to_numpy()))
    s_off = int(np.argmin(window["forecast"].to_numpy()))
    ax.scatter([base_x], [base_y], s=140, marker="v", color="#666",
               zorder=5, label=f"baseline (now)={base_y:.0f}")
    ax.scatter([o_off * 0.5], [float(window["actual"].iloc[o_off])], s=160,
               marker="*", color="#2a7", zorder=5,
               label=f"oracle (min actual)={window['actual'].iloc[o_off]:.0f}")
    ax.scatter([s_off * 0.5], [float(window["actual"].iloc[s_off])], s=120,
               marker="D", facecolors="none", edgecolors="#06c", lw=2, zorder=5,
               label=f"scheduler (min forecast -> actual={window['actual'].iloc[s_off]:.0f})")

    ax.set_xlabel(f"hours after arrival ({window_hours}h window)")
    ax.set_ylabel("carbon intensity (gCO2/kWh)")
    ax.set_title(f"Worked example - arrival {row['arrival']:%Y-%m-%d %H:%M UTC}")
    ax.legend(fontsize=8, loc="best")
    ax.grid(alpha=0.3)
    p = out_dir / "fig4_worked_example_day.png"
    fig.tight_layout(); fig.savefig(p, dpi=120); plt.close(fig)
    return p
