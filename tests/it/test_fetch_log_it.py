"""Scenarios of `cfbd-vs/fetch-log` against the `TEST_` namespace and the mock (zero CFBD calls).

The scenario "A stored-only query logs only the slices it read" is a unit test
(`tests/unit/test_fetchlog.py`, `test_stored_only_query_logs_the_stored_slices_...`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.it.fetch_helpers import STAGED_TABLE, as_datetime, games_query, games_slice, target_of
from tests.it.harness import HARNESS_SCHEMA, Harness, Mock
from tests.it.support import where_clause
from tests.synthetic import fake
from vs.host import fetchlog

pytestmark = pytest.mark.it

RATINGS = "RATINGS_SP"
CALENDAR = "CALENDAR"
RATINGS_SLICE = "/ratings/sp?year=2024"
TWO_YEARS_ONE_WEEK = "P_YEAR IN (2019, 2020) AND P_WEEK = 8"
RANGE_AND_IN = "P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)"
CAPTURE_CONTEXT = "fetch-log integration test"
EMPTY_YEAR = "2020"


def ratings_query(harness: Harness) -> str:
    where = where_clause(RATINGS, fake.minimal_filter(RATINGS))
    return f"SELECT * FROM {harness.vs}.{RATINGS} WHERE {where}"


@contextmanager
def staged_table(harness: Harness, query: str) -> Iterator[tuple[str, str]]:
    """The result of `query` staged into a table of the harness schema, dropped afterwards."""
    name = (HARNESS_SCHEMA, STAGED_TABLE)
    harness.query(f"DROP TABLE IF EXISTS {HARNESS_SCHEMA}.{STAGED_TABLE}")
    harness.query(f"CREATE TABLE {HARNESS_SCHEMA}.{STAGED_TABLE} AS {query}")
    try:
        yield name
    finally:
        harness.query(f"DROP TABLE IF EXISTS {HARNESS_SCHEMA}.{STAGED_TABLE}")


def test_rows_carry_request_metadata(harness: Harness, mock: Mock) -> None:
    """Scenario: Every row carries its request metadata."""
    rows = harness.query_dicts(ratings_query(harness))
    assert rows
    assert {row["REQ_SLICE"] for row in rows} == {RATINGS_SLICE}
    assert {row["REQ_SOURCE"] for row in rows} == {"LIVE"}
    assert {row["REQ_STATUS"] for row in rows} == {200}
    assert {row["REQ_ATTEMPTS"] for row in rows} == {1}
    assert all(row["REQ_MS"] is not None and row["REQ_MS"] >= 0 for row in rows)
    assert all(row["REQ_AT"] is not None for row in rows)
    assert all(as_datetime(row["REQ_AT"]) for row in rows)
    assert {row["REQ_ROWS"] for row in rows} == {len(rows)}
    assert harness.mock_hits(RATINGS_SLICE.partition("?")[0]) == 1


def test_slice_key_equals_sent_request(harness: Harness, mock: Mock) -> None:
    """Scenario: The slice key is exactly the request that was sent."""
    rows = harness.query_dicts(games_query(harness, TWO_YEARS_ONE_WEEK))
    assert rows
    received = {target_of(entry) for entry in mock.log()}
    slices = {row["REQ_SLICE"] for row in rows}
    assert slices == received == {games_slice(2019, 8), games_slice(2020, 8)}
    for slice_key in slices:
        assert not slice_key.startswith("http")
        assert "127.0.0.1" not in slice_key
        assert harness.marker_key not in slice_key


def test_explain_manifest_no_calls(harness: Harness, mock: Mock) -> None:
    """Scenario: EXPLAIN VIRTUAL shows the planned requests without calling the API."""
    query = games_query(harness, RANGE_AND_IN)
    pushdown = fetchlog.explain_pushdown_sql(harness.owner, query)
    manifest = fetchlog.parse_manifest(pushdown)
    raw = json.loads(pushdown.lstrip()[len(fetchlog.MANIFEST_OPEN) : pushdown.find("*/")])
    expected = [games_slice(year, week) for year in (2019, 2020, 2021) for week in (8, 9)]
    assert manifest.table == "GAMES"
    assert manifest.fetch_mode == "live"
    assert manifest.read_through is True
    assert manifest.budget == int(harness.properties().get("MAX_CALLS_PER_QUERY", 10))
    assert manifest.planned_requests == 6
    assert list(manifest.slice_keys) == expected
    assert raw["planned_requests"] == 6
    assert harness.mock_hits() == 0


def test_staged_result_captured_into_fetch_log(harness: Harness, mock: Mock) -> None:
    """Scenario: A staged result is captured into the fetch log."""
    mock.script("/games", query={"year": EMPTY_YEAR}, body=[])
    query = games_query(harness, TWO_YEARS_ONE_WEEK)
    pushdown = fetchlog.explain_pushdown_sql(harness.owner, query)
    manifest = fetchlog.parse_manifest(pushdown)
    with staged_table(harness, query) as staged:
        captured = fetchlog.capture_fetch_log(
            harness.owner,
            staged_table=staged,
            pushdown_sql=pushdown,
            meta_schema=harness.meta,
            context=CAPTURE_CONTEXT,
        )
        metadata = {
            row["REQ_SLICE"]: row
            for row in harness.query_dicts(
                "SELECT REQ_SLICE, REQ_SOURCE, REQ_STATUS, REQ_ATTEMPTS, REQ_MS, REQ_ROWS, "
                f"REQ_AT, COUNT(*) AS N FROM {HARNESS_SCHEMA}.{STAGED_TABLE} "
                "GROUP BY REQ_SLICE, REQ_SOURCE, REQ_STATUS, REQ_ATTEMPTS, REQ_MS, REQ_ROWS, REQ_AT"
            )
        }
    logged = harness.query_dicts(
        f"SELECT * FROM {harness.meta}.FETCH_LOG ORDER BY SLICE_KEY",
    )
    assert len(captured) == len(logged) == manifest.planned_requests == 2
    assert sorted(row["SLICE_KEY"] for row in logged) == sorted(manifest.slice_keys)
    assert {row["TABLE_NAME"] for row in logged} == {"GAMES"}
    assert {row["CONTEXT"] for row in logged} == {CAPTURE_CONTEXT}
    by_slice = {row["SLICE_KEY"]: row for row in logged}
    filled = by_slice[games_slice(2019, 8)]
    staged_row = metadata[games_slice(2019, 8)]
    assert filled["REQ_SOURCE"] == staged_row["REQ_SOURCE"] == "LIVE"
    assert filled["HTTP_STATUS"] == staged_row["REQ_STATUS"] == 200
    assert filled["ATTEMPTS"] == staged_row["REQ_ATTEMPTS"]
    assert filled["MS"] == staged_row["REQ_MS"]
    assert filled["ROWS_RETURNED"] == staged_row["REQ_ROWS"] == staged_row["N"]
    assert filled["REQUESTED_AT"] == staged_row["REQ_AT"]
    empty = by_slice[games_slice(int(EMPTY_YEAR), 8)]
    assert games_slice(int(EMPTY_YEAR), 8) not in metadata
    assert empty["ROWS_RETURNED"] == 0
    assert empty["REQ_SOURCE"] == "LIVE"
    assert empty["HTTP_STATUS"] is None


def test_stored_rows_report_origin(harness: Harness, mock: Mock) -> None:
    """Scenario: Stored rows report their origin."""
    slice_keys = harness.stage_slices(CALENDAR)
    assert len(slice_keys) == 1
    original = harness.query_dicts(
        f"SELECT DISTINCT REQ_SLICE, REQ_AT FROM {harness.core}.{CALENDAR}"
    )
    assert len(original) == 1
    where = where_clause(CALENDAR, fake.minimal_filter(CALENDAR))
    rows = harness.query_dicts(f"SELECT * FROM {harness.vs}.{CALENDAR} WHERE {where}")
    assert rows
    assert {row["REQ_SOURCE"] for row in rows} == {"STORED"}
    assert {row["REQ_STATUS"] for row in rows} == {None}
    assert {row["REQ_MS"] for row in rows} == {None}
    assert {row["REQ_ATTEMPTS"] for row in rows} == {0}
    assert {row["REQ_AT"] for row in rows} == {original[0]["REQ_AT"]}
    assert original[0]["REQ_AT"] is not None
    assert harness.mock_hits() == 0
