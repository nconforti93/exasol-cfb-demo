"""Scenarios of `cfbd-vs/fan-out`: request expansion, the call budget and parallel fetching.

Decision [18]: one SET group, `MAX_PARALLEL_FETCHES` threads. The concurrency test scripts a delay
in the mock and compares the `REQ_AT` / `REQ_MS` spans of the rows.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from tests.it.harness import Harness, Mock
from tests.it.plan_helpers import (
    GAMES,
    error_of,
    explain_manifests,
    games_keys,
    keys_of,
    table_query,
)

pytestmark = pytest.mark.it

BUDGET_ERROR = "MAX_CALLS_PER_QUERY"
DELAY_MS = 2000
REQUESTS = 6
HALF_SUMMED_LATENCY_SECONDS = REQUESTS * DELAY_MS / 1000 / 2
SEASONS_2000_2024 = tuple(range(2000, 2025))
SIX_SEASONS = "P_YEAR BETWEEN 2019 AND 2024"


def parse_timestamp(value: object) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def games_query(harness: Harness, where: str, columns: str = "*") -> str:
    return table_query(harness, GAMES, where, columns)


def test_range_and_in_fan_out_product(harness: Harness) -> None:
    """fan-out: A range and an IN list fan out into their product."""
    sql = games_query(harness, "P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)")
    manifest = harness.explain(sql)
    assert manifest.planned_requests == REQUESTS
    assert len(manifest.slice_keys) == REQUESTS
    assert set(manifest.slice_keys) == games_keys((2019, 2020, 2021), (8, 9))
    assert all("seasonType=both" in key for key in manifest.slice_keys)
    assert manifest.budget == 10


def test_duplicate_values_requested_once(harness: Harness) -> None:
    """fan-out: Duplicate values are requested once."""
    where = "P_YEAR IN (2020, 2020, 2021) AND P_YEAR BETWEEN 2019 AND 2020 AND P_WEEK IN (8, 8)"
    assert keys_of(harness, games_query(harness, where)) == (
        "/games?seasonType=both&week=8&year=2020",
    )


def test_over_budget_rejected(harness: Harness, mock: Mock) -> None:
    """fan-out: An over-budget query is rejected before any call."""
    message = error_of(harness, games_query(harness, "P_YEAR BETWEEN 2000 AND 2024"))
    assert "25 requests" in message
    assert f"{BUDGET_ERROR} is 10" in message
    assert "narrower filter" in message
    assert harness.mock_hits() == 0


def test_at_budget_allowed(harness: Harness) -> None:
    """fan-out: A query exactly at the budget is allowed."""
    manifest = harness.explain(games_query(harness, "P_YEAR BETWEEN 2015 AND 2024"))
    assert manifest.planned_requests == 10
    assert set(manifest.slice_keys) == games_keys(range(2015, 2025))
    assert manifest.budget == 10


def test_stored_slices_count_toward_budget(harness: Harness, mock: Mock) -> None:
    """fan-out: Stored slices count toward the budget."""
    for key in sorted(games_keys(SEASONS_2000_2024)):
        harness.register_slice(GAMES, key, 0)
    message = error_of(harness, games_query(harness, "P_YEAR BETWEEN 2000 AND 2024"))
    assert "25 requests" in message
    assert BUDGET_ERROR in message
    assert harness.mock_hits() == 0


def test_stored_only_query_is_exempt_from_the_budget(harness: Harness) -> None:
    """fan-out: a query restricted to stored data never calls the API, so it has no budget."""
    sql = games_query(harness, "P_YEAR BETWEEN 2000 AND 2024 AND REQ_SOURCE = 'STORED'")
    manifest = harness.explain(sql)
    assert manifest.planned_requests == len(SEASONS_2000_2024)
    assert manifest.budget is None
    assert manifest.sources == ("STORED",)


def test_budget_per_table_access(harness: Harness, mock: Mock) -> None:
    """fan-out: The budget applies to each virtual-table access."""
    union = (
        table_query(harness, "RATINGS_SP", SIX_SEASONS, "REQ_SLICE")
        + " UNION ALL "
        + table_query(harness, "RATINGS_SRS", SIX_SEASONS, "REQ_SLICE")
    )
    manifests = explain_manifests(harness, union)
    assert sorted(manifest.table for manifest in manifests) == ["RATINGS_SP", "RATINGS_SRS"]
    assert [manifest.planned_requests for manifest in manifests] == [REQUESTS, REQUESTS]
    assert len(harness.query(union)) > 0
    assert harness.mock_hits("/ratings/sp") == REQUESTS
    assert harness.mock_hits("/ratings/srs") == REQUESTS


def test_requests_run_concurrently(harness: Harness, mock: Mock) -> None:
    """fan-out: The requests of one query run concurrently."""
    mock.script("/games", delay_ms=DELAY_MS)
    sql = games_query(
        harness,
        "P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)",
        "REQ_SLICE, REQ_AT, REQ_MS",
    )
    started = time.monotonic()
    rows = harness.query(sql)
    elapsed = time.monotonic() - started
    latencies = {str(slice_key): timedelta(milliseconds=float(ms)) for slice_key, _, ms in rows}
    starts = {str(slice_key): parse_timestamp(requested_at) for slice_key, requested_at, _ in rows}
    ends = {key: starts[key] + latency for key, latency in latencies.items()}
    assert len(starts) == REQUESTS
    assert harness.mock_hits("/games") == REQUESTS
    first_end_after_last_start = (min(ends.values()) - max(starts.values())).total_seconds()
    span = (max(ends.values()) - min(starts.values())).total_seconds()
    summed = sum(latency.total_seconds() for latency in latencies.values())
    print(
        f"\nfan-out measured: wall {elapsed:.2f}s, request span {span:.2f}s, "
        f"summed request latency {summed:.2f}s, "
        f"all requests overlap for {first_end_after_last_start:.2f}s"
    )
    assert elapsed < HALF_SUMMED_LATENCY_SECONDS
    assert span < HALF_SUMMED_LATENCY_SECONDS
    assert first_end_after_last_start > 0
