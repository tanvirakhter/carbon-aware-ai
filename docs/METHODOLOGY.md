# Methodology Note: Handling the Forecast-Only Regional Data

**Project:** carbon-aware-ai
**Status:** Decision locked before analysis implementation
**Data source:** National Energy System Operator (NESO) Carbon Intensity API, CC BY 4.0

---

## The core data asymmetry

The NESO API exposes two series with different guarantees:

| Series | Forecast intensity | Actual intensity | Spatial detail |
|---|---|---|---|
| **National** (`/intensity/...`) | Yes | **Yes** | GB-wide only |
| **Regional** (`/regional/...`) | Yes | **No** | 14 DNO regions + England/Scotland/Wales |

This asymmetry is the single most important methodological constraint in the
study. Any claim about *avoided emissions* requires a ground-truth ("actual")
value to measure the decision against. The national series has this; the
regional series does not.

## The trap we are explicitly avoiding

If an "oracle" (perfect-hindsight best slot) and a "scheduler" (forecast-driven
best slot) are both computed on the **regional forecast** series, they read the
same numbers. Any apparent "saving" is then forecast-vs-forecast - it measures
the shape of the forecast curve, not real CO2 avoided. Presenting this as a
saving would be misleading. We do not do this.

## Decision: split claims by axis (Option A)

**1. Temporal analysis - rigorous, quantitative core.**
Run entirely on the **national** series, which has actuals.
- *Oracle:* greenest half-hour by **actual** intensity within the flexible window.
- *Scheduler:* greenest half-hour by the **forecast available at decision time**.
- *Baseline:* run immediately at the window's start.
- Headline metrics are real, verifiable CO2 savings. This is the publishable
  contribution.

**2. Spatial analysis - descriptive only, clearly labelled.**
Run on the **regional forecast** series.
- Reports how much **forecast** carbon intensity varies across GB regions at the
  same moment (range, spread, a map).
- Framed as "regional forecast variation," never as a verified saving.
- Provides the memorable visual and motivates the idea, without overclaiming.

## What this buys us

- One rigorous, defensible quantitative claim (temporal, on actuals).
- One strong descriptive figure (spatial map of forecast variation).
- The limitation becomes a single honest sentence in Methods that *strengthens*
  credibility rather than a hole a reviewer finds later.

## Rejected alternatives (recorded for transparency)

- **National-derived proxy for regional actuals** (Option B): only acceptable as
  a clearly-labelled sensitivity/bounding exercise; too easy to misread; not used
  in the main analysis.
- **Forecast-quality-only reframing** (Option C): legitimate but a narrower, less
  compelling story than verified temporal savings; not adopted.

## Practical prerequisite before building the temporal module

Confirm the national `actual` field is reliably populated in the backfill.
The most recent (unsettled) half-hour periods may carry forecast only until they
settle. The temporal analysis must drop any period lacking an actual value, and
should log how many were dropped.
