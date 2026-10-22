"""Scenarios of `cfbd-vs/pushdown`, plus the empty-SET-group guard cases of decision [21].

Planning checks use `EXPLAIN VIRTUAL` (no API call). Queries that must be rejected run for real
with the `mock` fixture, which proves that the rejection happens before any request.
"""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter

import pytest

from tests.it.harness import HARNESS_SCHEMA, Harness, Mock
from tests.it.plan_helpers import (
    GAMES,
    GAMES_2024_ROWS,
    GAMES_FIRST_ID,
    error_of,
    explain_error_of,
    games_slice_key,
    keys_of,
    replay_games_2024,
    rule_ids,
    table_query,
)
from tests.it.support import where_clause
from tests.synthetic import fake
from vs.cfbd_vs.pushdown_sql import SOURCE_STORED

pytestmark = pytest.mark.it

RATINGS_SP = "RATINGS_SP"
CALENDAR = "CALENDAR"
AGGREGATE_CALL = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(")
REQ_SLICE_GUARD = '"REQ_SLICE" IS NOT NULL'


def sp_query(harness: Harness, where: str) -> str:
    return table_query(harness, RATINGS_SP, where)


def games_query(harness: Harness, where: str, columns: str = "*") -> str:
    return table_query(harness, GAMES, where, columns)


def test_equality_becomes_request_parameter(harness: Harness) -> None:
    """pushdown: Equality on a parameter column becomes a request parameter."""
    assert keys_of(harness, sp_query(harness, "P_YEAR = 2024")) == ("/ratings/sp?year=2024",)


def test_rows_echo_request_parameters(harness: Harness, mock: Mock) -> None:
    """pushdown: Rows echo the parameters of their request."""
    replay_games_2024(harness)
    rows = harness.query_dicts(
        games_query(harness, "P_YEAR = 2024", "P_YEAR, P_SEASON_TYPE, P_TEAM")
    )
    assert len(rows) == len(GAMES_2024_ROWS)
    assert {row["P_YEAR"] for row in rows} == {2024}
    assert {row["P_SEASON_TYPE"] for row in rows} == {"both"}
    assert {row["P_TEAM"] for row in rows} == {None}
    assert harness.mock_hits() == 0


def test_bounded_ranges_are_enumerated(harness: Harness) -> None:
    """pushdown: Bounded integer ranges are enumerated."""
    expected = tuple(f"/ratings/sp?year={year}" for year in (2019, 2020, 2021))
    assert keys_of(harness, sp_query(harness, "P_YEAR BETWEEN 2019 AND 2021")) == expected
    assert keys_of(harness, sp_query(harness, "P_YEAR >= 2019 AND P_YEAR < 2022")) == expected


def test_open_ranges_rejected(harness: Harness, mock: Mock) -> None:
    """pushdown: Open-ended ranges are rejected."""
    message = error_of(harness, sp_query(harness, "P_YEAR >= 2020"))
    assert "P_YEAR" in message
    assert "bounded range" in message
    assert "IN list" in message
    assert harness.mock_hits() == 0


def test_in_list_one_request_per_value(harness: Harness) -> None:
    """pushdown: IN lists produce one request per value."""
    keys = keys_of(harness, sp_query(harness, "P_TEAM IN ('Michigan', 'Ohio State')"))
    assert set(keys) == {"/ratings/sp?team=Michigan", "/ratings/sp?team=Ohio%20State"}
    assert len(keys) == 2


def test_same_parameter_or_acts_like_in(harness: Harness) -> None:
    """pushdown: OR on one parameter acts like IN."""
    keys = keys_of(harness, sp_query(harness, "P_YEAR = 2020 OR P_YEAR BETWEEN 2022 AND 2023"))
    assert set(keys) == {f"/ratings/sp?year={year}" for year in (2020, 2022, 2023)}
    assert len(keys) == 3


def test_cross_parameter_or_rejected(harness: Harness, mock: Mock) -> None:
    """pushdown: OR across parameters is rejected."""
    for where in ("P_YEAR = 2024 OR P_TEAM = 'Michigan'", "P_YEAR = 2024 OR HOME_POINTS > 50"):
        message = error_of(harness, games_query(harness, where))
        assert "OR is supported only between predicates on one and the same parameter" in message
        assert "UNION ALL" in message
    assert harness.mock_hits() == 0


@pytest.mark.parametrize(
    ("predicate", "column"),
    [
        ("P_TEAM LIKE 'Mich%'", "P_TEAM"),
        ("P_TEAM <> 'Michigan'", "P_TEAM"),
        ("P_TEAM IS NULL", "P_TEAM"),
        ("NOT (P_WEEK = 3)", "P_WEEK"),
        ("P_TEAM < 'M'", "P_TEAM"),
    ],
)
def test_unsupported_parameter_predicates_rejected(
    harness: Harness, mock: Mock, predicate: str, column: str
) -> None:
    """pushdown: Unsupported predicates on parameter columns are rejected."""
    message = error_of(harness, games_query(harness, f"P_YEAR = 2024 AND {predicate}"))
    assert f"parameter column {column}" in message
    assert f"supported for {column}:" in message
    assert harness.mock_hits() == 0


def test_parameter_values_must_be_literals(harness: Harness, mock: Mock) -> None:
    """pushdown: Parameter values must be literals."""
    table = f"{HARNESS_SCHEMA}.S_YEARS"
    harness.query(f"CREATE OR REPLACE TABLE {table} (Y DECIMAL(4,0))")
    try:
        harness.query(f"INSERT INTO {table} VALUES (2024)")
        message = error_of(
            harness,
            f"SELECT * FROM {harness.vs}.{RATINGS_SP} R JOIN {table} S ON R.P_YEAR = S.Y",
        )
    finally:
        harness.query(f"DROP TABLE IF EXISTS {table}")
    assert rule_ids(message) == ["ratings_sp.year_or_team"]
    assert "P_YEAR" in message
    assert harness.mock_hits() == 0


def test_response_predicates_are_residual(harness: Harness, mock: Mock) -> None:
    """pushdown: Predicates on response columns filter rows without changing requests."""
    replay_games_2024(harness)
    where = "P_YEAR = 2024 AND (HOME_POINTS > 50 OR AWAY_POINTS > 50) AND VENUE LIKE 'M%'"
    assert keys_of(harness, games_query(harness, where)) == (games_slice_key(),)
    rows = harness.query(games_query(harness, where, "ID"))
    expected = {
        GAMES_FIRST_ID + index
        for index, (home, away, venue, _) in enumerate(GAMES_2024_ROWS)
        if (home > 50 or away > 50) and venue.startswith("M")
    }
    assert expected
    assert len(expected) < len(GAMES_2024_ROWS)
    assert {int(row[0]) for row in rows} == expected
    assert len(rows) == len(expected)


@pytest.mark.parametrize(
    "where",
    [
        "P_YEAR = 2024 AND P_YEAR = 2025",
        "P_YEAR BETWEEN 2025 AND 2020",
        "P_YEAR IN (2019, 2020) AND P_YEAR > 2021",
        "P_YEAR > 2024 AND P_YEAR < 2025",
    ],
)
def test_contradictions_return_no_rows_no_calls(harness: Harness, mock: Mock, where: str) -> None:
    """pushdown: Contradictory filters return no rows and make no calls."""
    reference = harness.owner.execute(
        games_query(harness, "P_YEAR = 2024 AND REQ_SOURCE = 'STORED'")
    )
    reference.fetchall()
    statement = harness.owner.execute(games_query(harness, where))
    assert statement.fetchall() == []
    assert statement.columns() == reference.columns()
    manifest = harness.explain(games_query(harness, where))
    assert manifest.slice_keys == ()
    assert manifest.sources == ()
    assert harness.mock_hits() == 0


def test_contradiction_before_rules(harness: Harness) -> None:
    """pushdown: Contradictions are decided before requirement rules."""
    sql = table_query(harness, "PLAYS", "P_YEAR = 2020 AND P_YEAR = 2021")
    assert harness.query(sql) == []


def test_enum_values_validated(harness: Harness, mock: Mock) -> None:
    """pushdown: Enum parameter values are validated."""
    message = error_of(harness, games_query(harness, "P_YEAR = 2024 AND P_SEASON_TYPE = 'Regular'"))
    assert "P_SEASON_TYPE" in message
    for allowed in ("regular", "postseason", "both", "allstar", "spring_regular"):
        assert allowed in message
    assert harness.mock_hits() == 0


def test_parameter_values_passed_safely(harness: Harness, mock: Mock) -> None:
    """pushdown: Parameter values are passed safely."""
    hostile = "O'Brien; DROP TABLE X--"
    expected_targets = {
        "Texas A&M": "/ratings/sp?team=Texas%20A%26M",
        hostile: "/ratings/sp?team=O%27Brien%3B%20DROP%20TABLE%20X--",
    }
    for value, target in expected_targets.items():
        literal = "'" + value.replace("'", "''") + "'"
        where = f"P_TEAM = {literal}"
        rows = harness.query(table_query(harness, RATINGS_SP, where, "P_TEAM"))
        assert rows
        assert {row[0] for row in rows} == {value}
        sql = harness.pushdown_sql(sp_query(harness, where))
        assert f"'{target}'" in sql
        assert "DROP TABLE" not in sql
        assert ";" not in sql
    targets = [entry["target"] for entry in mock.log()]
    assert sorted(targets) == sorted(expected_targets.values())
    for target in targets:
        assert set(urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)) == {"team"}


def test_aggregates_computed_by_exasol(harness: Harness, mock: Mock) -> None:
    """pushdown: Aggregates over a virtual table are computed by Exasol."""
    replay_games_2024(harness)
    plain = harness.query(games_query(harness, "P_YEAR = 2024", "WEEK, HOME_POINTS"))
    totals = harness.query(games_query(harness, "P_YEAR = 2024", "COUNT(*), SUM(HOME_POINTS)"))
    assert [(int(count), int(total)) for count, total in totals] == [
        (len(plain), sum(int(row[1]) for row in plain))
    ]
    grouped_sql = (
        f"SELECT WEEK, COUNT(*) FROM {harness.vs}.{GAMES} WHERE P_YEAR = 2024 GROUP BY WEEK"
    )
    grouped = harness.query(grouped_sql)
    assert {int(week): int(count) for week, count in grouped} == dict(
        Counter(int(row[0]) for row in plain)
    )
    assert harness.query("SELECT 1") == [(1,)]
    totals_sql = games_query(harness, "P_YEAR = 2024", "COUNT(*), SUM(HOME_POINTS)")
    for sql in (totals_sql, grouped_sql):
        assert AGGREGATE_CALL.search(harness.pushdown_sql(sql)) is None


def test_all_stored_query_has_no_extra_null_row(harness: Harness, mock: Mock) -> None:
    """pushdown: [21] a query whose every slice is stored returns exactly the stored rows."""
    slice_keys = harness.stage_slices(CALENDAR)
    harness.replay_area.clear()
    harness.wait_for_files([harness.replay_area.udf_file(key) for key in slice_keys], present=False)
    where = where_clause(CALENDAR, fake.minimal_filter(CALENDAR))
    sql = table_query(harness, CALENDAR, where)
    stored_rows = harness.query(f"SELECT COUNT(*) FROM {harness.core}.{CALENDAR}")[0][0]
    assert stored_rows > 0
    manifest = harness.explain(sql)
    assert manifest.sources == (SOURCE_STORED, "LIVE")
    pushdown = harness.pushdown_sql(sql)
    assert REQ_SLICE_GUARD in pushdown
    assert "NOT EXISTS" in pushdown
    rows = harness.query_dicts(table_query(harness, CALENDAR, where, "REQ_SLICE, REQ_SOURCE"))
    assert len(rows) == stored_rows
    assert all(row["REQ_SLICE"] is not None for row in rows)
    assert {row["REQ_SOURCE"] for row in rows} == {SOURCE_STORED}
    assert harness.query(table_query(harness, CALENDAR, where, "COUNT(*)")) == [(stored_rows,)]
    assert harness.mock_hits() == 0


def test_stored_empty_slice_returns_zero_rows(harness: Harness, mock: Mock) -> None:
    """pushdown: [21] a stored slice without rows returns 0 rows, not an all-NULL row."""
    slice_key = fake.slice_key_for(CALENDAR)
    harness.register_slice(CALENDAR, slice_key, 0)
    where = where_clause(CALENDAR, fake.minimal_filter(CALENDAR))
    sql = table_query(harness, CALENDAR, where)
    assert harness.explain(sql).sources == (SOURCE_STORED, "LIVE")
    assert REQ_SLICE_GUARD in harness.pushdown_sql(sql)
    assert harness.query(sql) == []
    assert harness.query(table_query(harness, CALENDAR, where, "COUNT(*)")) == [(0,)]
    assert harness.mock_hits() == 0


def test_explain_of_a_rejected_query_fails_like_the_query(harness: Harness) -> None:
    """pushdown: every rejection happens at planning, so EXPLAIN VIRTUAL shows it too."""
    sql = sp_query(harness, "P_YEAR >= 2020")
    assert "bounded range" in explain_error_of(harness, sql)
