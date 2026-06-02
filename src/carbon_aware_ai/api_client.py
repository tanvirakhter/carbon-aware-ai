"""A polite client for the NESO Carbon Intensity API.

https://api.carbonintensity.org.uk  - no authentication required.
Carbon Intensity data (c) NESO, licensed under CC BY 4.0 (see ATTRIBUTION.md).

Design notes
------------
* Exponential backoff with jitter-free, deterministic delays, honouring any
  ``Retry-After`` header the server sends (429 / 5xx).
* The national ``/intensity/{from}/{to}`` endpoint is capped at a 14-day range
  per call, so :meth:`CarbonIntensityClient.national_intensity_range` chunks
  longer pulls automatically and concatenates the results.
* All datetimes are UTC. Helpers accept timezone-aware or naive
  ``datetime`` objects (naive is assumed UTC) and format them as the API's
  ``YYYY-MM-DDThh:mmZ``.

The client returns the API's raw JSON (Python dicts/lists). Turning that into
tidy tables is the job of :mod:`carbon_aware_ai.etl`, which keeps parsing
testable offline against mock payloads.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import requests

from . import config

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# datetime helpers
# --------------------------------------------------------------------------- #
def _as_utc(dt: datetime) -> datetime:
    """Return ``dt`` as a timezone-aware UTC datetime (naive assumed UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_api_datetime(dt: datetime) -> str:
    """Format a datetime as the API path/param format ``YYYY-MM-DDThh:mmZ``."""
    return _as_utc(dt).strftime(config.API_DATETIME_FORMAT)


def _iter_chunks(
    start: datetime, end: datetime, max_days: int
) -> Iterator[tuple[datetime, datetime]]:
    """Yield ``(from, to)`` windows no wider than ``max_days`` covering [start, end]."""
    start, end = _as_utc(start), _as_utc(end)
    if end < start:
        raise ValueError("end must be >= start")
    step = timedelta(days=max_days)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + step, end)
        yield cursor, chunk_end
        cursor = chunk_end


class CarbonIntensityError(RuntimeError):
    """Raised when the API cannot be reached after exhausting retries."""


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #
class CarbonIntensityClient:
    """Thin, polite wrapper over the Carbon Intensity REST API."""

    def __init__(
        self,
        base_url: str = config.BASE_URL,
        *,
        session: Optional[requests.Session] = None,
        timeout: int = config.REQUEST_TIMEOUT_SECONDS,
        max_retries: int = config.MAX_RETRIES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": config.USER_AGENT}
        )

    # -- low-level GET with exponential backoff ----------------------------- #
    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:  # network-level failure
                last_exc = exc
                self._sleep_for_retry(attempt)
                continue

            # Retry on rate-limit / transient server errors.
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = CarbonIntensityError(
                    f"{resp.status_code} from {url}"
                )
                self._sleep_for_retry(attempt, resp)
                continue

            resp.raise_for_status()
            return resp.json()

        raise CarbonIntensityError(
            f"GET {url} failed after {self.max_retries + 1} attempts"
        ) from last_exc

    def _sleep_for_retry(
        self, attempt: int, resp: Optional[requests.Response] = None
    ) -> None:
        """Sleep using ``Retry-After`` if present, else exponential backoff."""
        delay = config.BACKOFF_BASE_SECONDS * (2 ** attempt)
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = float(retry_after)
        delay = min(delay, config.BACKOFF_MAX_SECONDS)
        logger.warning("Retrying after %.1fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)

    # -- national intensity (forecast + actual) ---------------------------- #
    def national_intensity_range(
        self, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Return half-hourly national periods for [start, end].

        Each period contains both ``forecast`` and ``actual`` gCO2/kWh. The
        14-day cap is handled transparently by chunking; the returned list is
        the concatenation of every chunk's ``data`` array, de-duplicated on the
        period ``from`` timestamp (chunk boundaries overlap by one period).
        """
        periods: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk_start, chunk_end in _iter_chunks(
            start, end, config.NATIONAL_MAX_CHUNK_DAYS
        ):
            frm = format_api_datetime(chunk_start)
            to = format_api_datetime(chunk_end)
            payload = self._get(f"/intensity/{frm}/{to}")
            for period in payload.get("data", []):
                key = period.get("from")
                if key in seen:
                    continue
                seen.add(key)
                periods.append(period)
            time.sleep(config.INTER_CHUNK_PAUSE_SECONDS)
        return periods

    # -- carbon factors ----------------------------------------------------- #
    def carbon_factors(self) -> dict[str, Any]:
        """Return per-fuel carbon factors (gCO2/kWh). Endpoint: /intensity/factors."""
        return self._get("/intensity/factors")

    # -- regional snapshot (current) --------------------------------------- #
    def regional_snapshot(self) -> dict[str, Any]:
        """Return the current half-hour's regional forecast for all regions."""
        return self._get("/regional")

    # -- regional 48-hour forecast ----------------------------------------- #
    def regional_fw48h(self, start: Optional[datetime] = None) -> dict[str, Any]:
        """Return the 48-hour-ahead regional forecast for all regions.

        ``start`` defaults to "now" (UTC). Endpoint:
        ``/regional/intensity/{from}/fw48h``. Regional data is FORECAST ONLY.
        """
        if start is None:
            start = datetime.now(timezone.utc)
        frm = format_api_datetime(start)
        return self._get(f"/regional/intensity/{frm}/fw48h")
