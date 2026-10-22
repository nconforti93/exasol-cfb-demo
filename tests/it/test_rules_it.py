"""Scenarios of `cfbd-vs/requirement-rules`: rule violations fail while the SQL is planned."""

from __future__ import annotations

import pytest

from tests.it.harness import Harness, Mock
from tests.it.plan_helpers import (
    adapter_line,
    error_of,
    explain_error_of,
    keys_of,
    rule_ids,
    table_query,
)
from tests.synthetic import fake

pytestmark = pytest.mark.it


def test_missing_hard_required_rejected(harness: Harness, mock: Mock) -> None:
    """requirement-rules: A missing hard-required parameter is rejected."""
    message = error_of(harness, table_query(harness, "PLAYS", "P_YEAR = 2024"))
    assert "plays.spec_required" in message
    assert "P_WEEK" in message
    assert harness.mock_hits() == 0


def test_required_unless_accepts_either(harness: Harness) -> None:
    """requirement-rules: A "required unless" rule accepts either parameter."""
    message = error_of(harness, table_query(harness, "RATINGS_SP"))
    assert rule_ids(message) == ["ratings_sp.year_or_team"]
    assert "P_YEAR" in message
    assert "P_TEAM" in message
    sql = table_query(harness, "RATINGS_SP", "P_TEAM = 'Michigan'")
    assert keys_of(harness, sql) == ("/ratings/sp?team=Michigan",)


def test_conditional_requirement_with_trigger(harness: Harness) -> None:
    """requirement-rules: A conditional requirement applies when its trigger is set."""
    message = error_of(harness, table_query(harness, "GAMES_TEAMS", "P_YEAR = 2024"))
    assert "games_teams.week_team_or_conference_with_year" in message
    for column in ("P_WEEK", "P_TEAM", "P_CONFERENCE"):
        assert column in message
    with_week = table_query(harness, "GAMES_TEAMS", "P_YEAR = 2024 AND P_WEEK = 8")
    assert keys_of(harness, with_week) == ("/games/teams?seasonType=both&week=8&year=2024",)
    by_id = table_query(harness, "GAMES_TEAMS", "P_ID = 401628374")
    assert keys_of(harness, by_id) == ("/games/teams?id=401628374&seasonType=both",)


def test_mutually_exclusive_rejected(harness: Harness, mock: Mock) -> None:
    """requirement-rules: Mutually exclusive parameters are rejected."""
    sql = table_query(harness, "CONFERENCES_AFFILIATIONS", "P_YEAR = 2024 AND P_MIN_YEAR = 2000")
    message = error_of(harness, sql)
    assert "conferences_affiliations.year_excludes_year_range" in message
    assert "P_YEAR" in message
    assert "P_MIN_YEAR" in message
    assert harness.mock_hits() == 0


def test_value_dependent_requirement(harness: Harness) -> None:
    """requirement-rules: A value-dependent requirement is enforced."""
    no_poll = table_query(harness, "RANKINGS", "P_YEAR = 2024 AND P_LATEST = TRUE")
    assert "rankings.latest_needs_cfp" in error_of(harness, no_poll)
    with_final = table_query(
        harness,
        "RANKINGS",
        "P_YEAR = 2024 AND P_POLL = 'cfp' AND P_LATEST = TRUE AND P_FINAL = TRUE",
    )
    assert "rankings.latest_excludes_final" in error_of(harness, with_final)
    accepted = table_query(
        harness, "RANKINGS", "P_YEAR = 2024 AND P_POLL = 'cfp' AND P_LATEST = TRUE"
    )
    assert keys_of(harness, accepted) == (
        "/rankings?latest=true&poll=cfp&seasonType=both&year=2024",
    )


def test_parameter_bounds_enforced(harness: Harness, mock: Mock) -> None:
    """requirement-rules: Parameter bounds are enforced."""
    message = error_of(harness, table_query(harness, "INFO_USAGE", "P_DAYS = 60"))
    assert "info_usage.days_bounds" in message
    assert "P_DAYS" in message
    assert "31" in message
    assert harness.mock_hits() == 0


def test_hidden_defaults_sent_explicitly(harness: Harness) -> None:
    """requirement-rules: Hidden defaults are sent explicitly."""
    table = "STATS_SEASON"
    assert keys_of(harness, table_query(harness, table, "P_YEAR = 2024")) == (
        "/stats/season?classification=fbs&year=2024",
    )
    harness.write_recording(fake.slice_key_for(table))
    harness.use_replay()
    rows = harness.query(table_query(harness, table, "P_YEAR = 2024", "P_CLASSIFICATION"))
    assert rows
    assert {row[0] for row in rows} == {"fbs"}
    override = table_query(harness, table, "P_YEAR = 2024 AND P_CLASSIFICATION = 'fcs'")
    assert keys_of(harness, override) == ("/stats/season?classification=fcs&year=2024",)


def test_hidden_default_becomes_requirement(harness: Harness) -> None:
    """requirement-rules: A hidden default can become a requirement."""
    message = error_of(harness, table_query(harness, "ROSTER", "P_TEAM = 'Michigan'"))
    assert "roster.year_required" in message
    assert "P_YEAR" in message
    assert "2025" in message


def test_row_capped_requires_completeness_param(harness: Harness) -> None:
    """requirement-rules: Row-capped endpoints require their completeness parameter."""
    message = error_of(harness, table_query(harness, "PLAYS_STATS", "P_YEAR = 2024 AND P_WEEK = 8"))
    assert "plays_stats.row_cap" in message
    assert "2,000" in message
    assert "P_GAME_ID" in message
    accepted = table_query(harness, "PLAYS_STATS", "P_GAME_ID = 401628374")
    assert keys_of(harness, accepted) == ("/plays/stats?gameId=401628374&seasonType=both",)


def test_child_tables_enforce_endpoint_rules(harness: Harness) -> None:
    """requirement-rules: Child tables enforce the rules of their endpoint."""
    parent = rule_ids(error_of(harness, table_query(harness, "RANKINGS")))
    child = rule_ids(error_of(harness, table_query(harness, "RANKINGS_POLLS_RANKS")))
    assert parent
    assert child == parent


def test_rule_violation_in_explain_zero_calls(harness: Harness, mock: Mock) -> None:
    """requirement-rules: Rule violations are visible in EXPLAIN VIRTUAL at zero calls."""
    sql = table_query(harness, "PLAYS", "P_YEAR = 2024")
    query_error = adapter_line(error_of(harness, sql))
    explain_error = adapter_line(explain_error_of(harness, sql))
    assert explain_error == query_error
    assert rule_ids(explain_error) == ["plays.spec_required"]
    assert harness.mock_hits() == 0
