"""Project configuration: paths, API constants, and region reference data.

All timestamps used against the API are UTC and half-hourly. The Carbon
Intensity API expects/returns the format ``YYYY-MM-DDThh:mmZ``.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
PACKAGE_ROOT = Path(__file__).resolve().parent
# project root = repo root (....../carbon-aware-ai)
PROJECT_ROOT = PACKAGE_ROOT.parents[1]

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# Raw landing zones (one per stream). Files are timestamped so a history
# accumulates over repeated collector runs.
RAW_REGIONAL_FW48H_DIR = RAW_DIR / "regional_fw48h"
RAW_NATIONAL_DIR = RAW_DIR / "national"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
BASE_URL = "https://api.carbonintensity.org.uk"

# Identify ourselves politely; the API is unauthenticated but we should still
# be a well-behaved client.
USER_AGENT = "carbon-aware-ai/0.1 (research; +https://api.carbonintensity.org.uk)"

# The national /intensity/{from}/{to} endpoint is capped at a 14-day range per
# call, so longer pulls must be chunked. We use 13 days to stay safely inside
# the inclusive boundary.
NATIONAL_MAX_CHUNK_DAYS = 13

# Politeness / resilience knobs.
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.0      # exponential backoff base
BACKOFF_MAX_SECONDS = 60.0
INTER_CHUNK_PAUSE_SECONDS = 0.5  # gentle pause between chunked calls

# Datetime format the API uses for path params and payload fields.
API_DATETIME_FORMAT = "%Y-%m-%dT%H:%MZ"


# --------------------------------------------------------------------------- #
# Region reference (14 DNO regions + England/Scotland/Wales aggregates)
# --------------------------------------------------------------------------- #
# Regional data is FORECAST ONLY (no actuals). The parser reads names straight
# from the payload; this table is for reference, joins, and validation.
REGION_ID_TO_SHORTNAME = {
    1: "North Scotland",
    2: "South Scotland",
    3: "North West England",
    4: "North East England",
    5: "Yorkshire",
    6: "North Wales",
    7: "South Wales",
    8: "West Midlands",
    9: "East Midlands",
    10: "East England",
    11: "South West England",
    12: "South England",
    13: "London",
    14: "South East England",
    15: "England",
    16: "Scotland",
    17: "Wales",
    18: "GB",
}

# regionids 1-14 are the Distribution Network Operator (DNO) license areas;
# 15-17 are country aggregates; 18 (when present) is GB-wide.
DNO_REGION_IDS = tuple(range(1, 15))
AGGREGATE_REGION_IDS = (15, 16, 17, 18)
