"""Unit tests of `app/backend/sqlcatalog.py`: the fixed read statements, their lints, and the
repository scan that keeps `GAME_PREVIEW` inside `app/backend/live.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backend import sqlcatalog
from app.backend.sqlcatalog import CATALOG, Statement

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "app" / "backend"
SCHEMAS = {"api_schema": "TEST_CFB_API", "api_live_schema": "TEST_CFB_API_LIVE"}
LIVE_ONLY = "GAME" + "_PREVIEW"


@pytest.mark.parametrize("name", sorted(CATALOG))
def test_every_catalog_statement_passes_the_lints(name: str) -> None:
    assert sqlcatalog.lint(CATALOG[name]) == []


def test_the_catalog_names_the_statements_the_routes_need() -> None:
    assert {
        "health",
        "current_point",
        "as_of_seasons",
        "as_of_points",
        "teams",
        "team",
        "odds_rows",
        "published_run",
        "analysis_settings",
        "whatif_run",
        "whatif_games",
        "rooting_guide_top",
        "rooting_guide_coming",
        "predictions_for_week",
        "predictions_graded_in_week",
        "scorecard_week",
        "scorecard_season",
        "scorecard_calibration",
        "upsets_week",
        "upsets_all_time",
        "upsets_line_era",
        "matchup_series",
        "matchup_games",
        "matchup_next",
        "quota",
        "timeline_latest_run",
        "timeline_run",
        "fetches_for_run",
        "week_games",
    } <= set(CATALOG)


def test_the_catalog_reads_only_serving_views() -> None:
    names = {name for statement in CATALOG.values() for _, name in sqlcatalog.sources(statement)}
    assert names <= sqlcatalog.SERVING_VIEWS | sqlcatalog.LIVE_VIEWS
    assert LIVE_ONLY not in names and "CURRENT_POINT" in names
    live = {
        name
        for statement in CATALOG.values()
        for schema, name in sqlcatalog.sources(statement)
        if schema == sqlcatalog.API_LIVE_SCHEMA
    }
    assert live == {"QUOTA"}


def test_the_odds_statement_renders_with_the_page_parameters() -> None:
    rendered = sqlcatalog.render(CATALOG["odds_rows"], season=2026, week_seq=9, **SCHEMAS)
    assert rendered == (
        'SELECT * FROM "TEST_CFB_API"."PLAYOFF_ODDS" '
        'WHERE "AS_OF_SEASON" = 2026 AND "AS_OF_WEEK_SEQ" = 9 ORDER BY "ODDS_RANK"'
    )


def test_a_text_value_is_quoted_as_a_literal() -> None:
    statement = CATALOG["fetches_for_run"]
    assert "'x'' OR 1=1 --'" in sqlcatalog.render(statement, context="x' OR 1=1 --", **SCHEMAS)


def test_a_number_that_is_not_one_is_refused_by_the_formatter() -> None:
    statement = CATALOG["as_of_points"]
    with pytest.raises(ValueError):
        sqlcatalog.render(statement, season="2026; DROP", **SCHEMAS)


def test_parameters_demand_exactly_the_statements_names() -> None:
    statement = CATALOG["odds_rows"]
    with pytest.raises(ValueError, match="week_seq"):
        sqlcatalog.parameters(statement, season=2026, **SCHEMAS)
    with pytest.raises(ValueError, match="extra"):
        sqlcatalog.parameters(statement, season=2026, week_seq=9, extra=1, **SCHEMAS)


def test_the_schema_names_are_always_supplied_by_the_caller() -> None:
    with pytest.raises(ValueError, match="api_schema"):
        sqlcatalog.parameters(CATALOG["teams"], api_live_schema="X")


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        ('SELECT * FROM {api_schema!q}."TEAMS" ORDER BY SCHOOL', "SCHOOL"),
        ('SELECT * FROM {api_schema!q}."TEAMS" ORDER BY "SCHOOL" LIMIT 5; SELECT 1', ";"),
        ('SELECT * FROM "CFB_API"."TEAMS"', "hard-coded"),
        ('SELECT * FROM {api_schema!q}."TEAMS" WHERE "TEAM_ID" = {team_id!r}', "!r"),
        ('SELECT * FROM {api_schema!q}."TEAMS" WHERE "TEAM_ID" = {team_id!i}', "!i"),
        ('SELECT * FROM {api_schema!q}."TEAMS" WHERE "TEAM_ID" = {team_id!q}', "!q"),
        ('SELECT * FROM {api_schema!i}."TEAMS"', "api_schema"),
        ('SELECT * FROM CFB_SIM."SIM_RUN"', "CFB_SIM"),
        ('SELECT * FROM {api_schema!q}."SIM_RUN"', "SIM_RUN"),
        ('SELECT * FROM {api_schema!q}."CFBD_VS"', "virtual"),
        (f'SELECT * FROM {{api_live_schema!q}}."{LIVE_ONLY}"', LIVE_ONLY),
        ('SELECT * FROM {api_live_schema!q}."TEAMS"', "TEAMS"),
        ('INSERT INTO {api_schema!q}."TEAMS" VALUES (1)', "INSERT"),
        ('SELECT * FROM {api_schema!q}."TEAMS" WHERE "A" = {a!d} + {b!d}', "b"),
    ],
)
def test_the_lints_catch_bad_statements(sql: str, fragment: str) -> None:
    found = sqlcatalog.lint(Statement("bad", sql, ("a", "team_id")))
    assert found and any(fragment in message for message in found), found


def test_a_statement_with_a_placeholder_it_does_not_declare_is_refused() -> None:
    found = sqlcatalog.lint(
        Statement("bad", 'SELECT * FROM {api_schema!q}."TEAMS" WHERE {x!d}', ())
    )
    assert any("x" in message for message in found)


def test_a_declared_parameter_that_is_never_used_is_refused() -> None:
    found = sqlcatalog.lint(Statement("bad", 'SELECT * FROM {api_schema!q}."TEAMS"', ("unused",)))
    assert any("unused" in message for message in found)


def test_the_live_schema_may_only_name_the_quota_view() -> None:
    good = Statement("ok", 'SELECT * FROM {api_live_schema!q}."QUOTA"', ())
    assert sqlcatalog.lint(good) == []


def test_game_preview_only_in_live() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted(BACKEND.rglob("*.py"))
        if path.name != "live.py" and LIVE_ONLY in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize("name", sorted(CATALOG))
def test_no_request_value_can_leave_its_literal(name: str) -> None:
    statement = CATALOG[name]
    payload = "x'; DROP SCHEMA S; --"
    values = {
        param: payload if param in sqlcatalog.TEXT_PARAMS else 7 for param in statement.params
    }
    rendered = sqlcatalog.render(statement, **values, **SCHEMAS)
    assert "'; DROP" not in rendered.replace("''", "")
    if any(param in sqlcatalog.TEXT_PARAMS for param in statement.params):
        assert "x''; DROP SCHEMA S; --" in rendered
