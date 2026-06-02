"""Client tests: chunking, datetime formatting, and backoff/retry - all offline.

No real HTTP is performed; a fake ``requests.Session`` records the URLs the
client would call and returns canned JSON.
"""

from datetime import datetime, timezone

import pytest

from carbon_aware_ai import api_client, config
from carbon_aware_ai.api_client import (
    CarbonIntensityClient,
    CarbonIntensityError,
    _iter_chunks,
    format_api_datetime,
)


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {"data": []}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("raise_for_status on error in test")


class FakeSession:
    """Returns queued responses in order; records requested URLs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.headers = {}
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make backoff/inter-chunk pauses instant so tests are fast."""
    monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)


# --------------------------------------------------------------------------- #
# datetime helpers
# --------------------------------------------------------------------------- #
def test_format_api_datetime_naive_assumed_utc():
    dt = datetime(2026, 6, 1, 9, 30)
    assert format_api_datetime(dt) == "2026-06-01T09:30Z"


def test_format_api_datetime_aware_converted_to_utc():
    dt = datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc)
    assert format_api_datetime(dt) == "2026-06-01T09:30Z"


# --------------------------------------------------------------------------- #
# 14-day chunking
# --------------------------------------------------------------------------- #
def test_iter_chunks_respects_max_days():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 2, 1, tzinfo=timezone.utc)  # 31 days
    chunks = list(_iter_chunks(start, end, config.NATIONAL_MAX_CHUNK_DAYS))
    # 31 days / 13-day chunks -> 3 windows, none wider than 13 days
    assert len(chunks) == 3
    for frm, to in chunks:
        assert (to - frm).days <= config.NATIONAL_MAX_CHUNK_DAYS
    assert chunks[0][0] == start
    assert chunks[-1][1] == end


def test_iter_chunks_rejects_reversed_range():
    start = datetime(2024, 2, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        list(_iter_chunks(start, end, 13))


def test_national_range_chunks_and_dedups():
    # Two overlapping chunks both reporting the boundary period.
    chunk_a = {"data": [
        {"from": "2024-01-01T00:00Z", "to": "2024-01-01T00:30Z",
         "intensity": {"forecast": 100, "actual": 99, "index": "low"}},
    ]}
    chunk_b = {"data": [
        {"from": "2024-01-01T00:00Z", "to": "2024-01-01T00:30Z",  # duplicate boundary
         "intensity": {"forecast": 100, "actual": 99, "index": "low"}},
        {"from": "2024-01-14T00:00Z", "to": "2024-01-14T00:30Z",
         "intensity": {"forecast": 120, "actual": 118, "index": "moderate"}},
    ]}
    session = FakeSession([FakeResponse(payload=chunk_a), FakeResponse(payload=chunk_b)])
    client = CarbonIntensityClient(session=session)

    periods = client.national_intensity_range(
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 20, tzinfo=timezone.utc),
    )
    # two chunk calls, boundary period de-duplicated
    assert len(session.urls) == 2
    assert "/intensity/" in session.urls[0]
    assert len(periods) == 2


# --------------------------------------------------------------------------- #
# backoff / retry
# --------------------------------------------------------------------------- #
def test_get_retries_on_429_then_succeeds():
    session = FakeSession([
        FakeResponse(status_code=429, headers={"Retry-After": "1"}),
        FakeResponse(status_code=503),
        FakeResponse(status_code=200, payload={"data": [{"ok": True}]}),
    ])
    client = CarbonIntensityClient(session=session, max_retries=5)
    out = client.regional_snapshot()
    assert out == {"data": [{"ok": True}]}
    assert len(session.urls) == 3


def test_get_raises_after_exhausting_retries():
    session = FakeSession([FakeResponse(status_code=500) for _ in range(4)])
    client = CarbonIntensityClient(session=session, max_retries=3)
    with pytest.raises(CarbonIntensityError):
        client.carbon_factors()
    assert len(session.urls) == 4  # initial + 3 retries


def test_endpoints_build_expected_paths():
    session = FakeSession([FakeResponse() for _ in range(2)])
    client = CarbonIntensityClient(session=session)
    client.carbon_factors()
    client.regional_fw48h(start=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))
    assert session.urls[0].endswith("/intensity/factors")
    assert session.urls[1].endswith("/regional/intensity/2026-06-01T09:00Z/fw48h")
