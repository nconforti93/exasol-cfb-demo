"""Unit tests of `app/backend/console_catalog.py`: the four curated statements, with their expected
plans pinned against the catalog's slice-key code and the adapter's planning code."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.backend import console_catalog
from app.backend.console_catalog import CONSOLE_QUERIES, ConsoleQuery
from tests.unit import filter_fixtures as f
from vs.cfbd_vs.adapter import Adapter
from vs.cfbd_vs.catalog import Catalog
from vs.cfbd_vs.plan import BudgetError, build_slice_key

CATALOG_PATH = Path(__file__).resolve().parents[2] / "vs" / "catalog" / "catalog.json"
CATALOG = Catalog.from_json(CATALOG_PATH.read_text(encoding="utf-8"))
SCHEMA = "CFBD_VS"
FETCH_SCRIPT = "CFBD_FETCH"


def query(query_id: str) -> ConsoleQuery:
    return console_catalog.get(query_id)


def col(name: str, table: str) -> dict[str, Any]:
    return {"type": "column", "tableName": table, "columnNr": 0, "name": name}


def push(table: str, filter_node: dict[str, Any]) -> str:
    request = {
        "type": "pushdown",
        "pushdownRequest": {
            "type": "select",
            "from": {"type": "table", "name": table},
            "filter": filter_node,
        },
        "involvedTables": [{"name": table, "columns": []}],
        "schemaMetadataInfo": {"name": SCHEMA},
    }
    return str(Adapter(CATALOG).handle(request)["sql"])


def manifest(sql: str) -> dict[str, Any]:
    match = re.fullmatch(r"/\*CFBD (.*)\*/", sql.split("\n", 1)[0])
    assert match is not None
    return dict(json.loads(match.group(1)))


def team_years(years: tuple[int, int], *, same_year: bool = False) -> dict[str, Any]:
    year = col("P_YEAR", "GAMES_TEAMS")
    if same_year:
        years_filter = f.and_(f.eq(year, f.num(years[0])), f.eq(year, f.num(years[1])))
    else:
        years_filter = f.between(year, f.num(years[0]), f.num(years[1]))
    return f.and_(years_filter, f.eq(col("P_TEAM", "GAMES_TEAMS"), f.text("Michigan")))


def test_there_are_four_queries_in_the_documented_order() -> None:
    assert [item.id for item in CONSOLE_QUERIES] == [
        "fan-out",
        "contradiction",
        "stored",
        "over-budget",
    ]
    assert console_catalog.get("stored").id == "stored"
    with pytest.raises(KeyError):
        console_catalog.get("anything else")


def test_the_statements_are_the_documented_texts() -> None:
    games_teams = (
        'SELECT * FROM "CFBD_VS"."GAMES_TEAMS" WHERE "P_YEAR" {years} AND "P_TEAM" = \'Michigan\''
    )
    assert console_catalog.sql(query("fan-out"), SCHEMA) == games_teams.format(
        years="BETWEEN 2022 AND 2024"
    )
    assert console_catalog.sql(query("contradiction"), SCHEMA) == games_teams.format(
        years='= 2023 AND "P_YEAR" = 2024'
    )
    assert console_catalog.sql(query("over-budget"), SCHEMA) == games_teams.format(
        years="BETWEEN 2010 AND 2024"
    )
    assert console_catalog.sql(query("stored"), SCHEMA) == (
        'SELECT "REQ_SLICE", "REQ_SOURCE", "REQ_STATUS", "REQ_ATTEMPTS", "REQ_MS", "REQ_AT", '
        'COUNT(*) AS "REQ_ROWS" FROM "CFBD_VS"."GAMES" '
        'WHERE "P_YEAR" BETWEEN 1869 AND 2025 AND "REQ_SOURCE" = \'STORED\' '
        'GROUP BY "REQ_SLICE", "REQ_SOURCE", "REQ_STATUS", "REQ_ATTEMPTS", "REQ_MS", "REQ_AT" '
        'ORDER BY "REQ_SLICE"'
    )


def test_the_test_namespace_reads_its_own_virtual_schema() -> None:
    text = console_catalog.sql(query("fan-out"), "TEST_CFBD_VS")
    assert '"TEST_CFBD_VS"."GAMES_TEAMS"' in text and '"CFBD_VS"' not in text


@pytest.mark.parametrize("schema", ['X"; DROP', "a b", "", "lower", "X.Y", "1X"])
def test_a_schema_name_must_be_a_plain_upper_case_identifier(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        console_catalog.sql(query("fan-out"), schema)


@pytest.mark.parametrize("item", CONSOLE_QUERIES, ids=lambda item: item.id)
def test_no_statement_takes_input_or_leaves_an_identifier_unquoted(item: ConsoleQuery) -> None:
    rendered = console_catalog.sql(item, SCHEMA)
    assert "{" not in rendered and ";" not in rendered
    bare = re.findall(r"[A-Za-z_]+", re.sub(r"'[^']*'|\"[^\"]*\"", " ", rendered))
    assert set(bare) <= console_catalog.ALLOWED_WORDS


def test_the_expected_slice_keys_are_the_catalogs_canonical_keys() -> None:
    table = CATALOG.table("GAMES_TEAMS")
    canonical = tuple(
        build_slice_key(table, {"year": year, "team": "Michigan", "seasonType": "both"})
        for year in (2022, 2023, 2024)
    )
    assert canonical == query("fan-out").expected.slice_keys
    assert canonical[0] == "/games/teams?seasonType=both&team=Michigan&year=2022"
    games = CATALOG.table("GAMES")
    stored = tuple(
        build_slice_key(games, {"year": year, "seasonType": "both"}) for year in range(1869, 2026)
    )
    assert stored == query("stored").expected.slice_keys and len(stored) == 157


def test_the_games_teams_rule_keeps_the_team_filter_necessary() -> None:
    rules = {rule.id: rule for rule in CATALOG.table("GAMES_TEAMS").rules}
    rule = rules["games_teams.week_team_or_conference_with_year"]
    assert rule.type == "requires_any" and rule.params == ("year", "week", "team", "conference")
    assert rule.details == {"any_of": ["week", "team", "conference"], "if": "year"}
    for item in (query("fan-out"), query("contradiction"), query("over-budget")):
        assert "\"P_TEAM\" = 'Michigan'" in console_catalog.sql(item, SCHEMA)


def test_fan_out_plans_three_slices_against_the_budget() -> None:
    plan = manifest(push("GAMES_TEAMS", team_years((2022, 2024))))
    expected = query("fan-out").expected
    assert tuple(plan["slice_keys"]) == expected.slice_keys
    assert plan["planned_requests"] == 3 and plan["budget"] == expected.budget == 10
    assert expected.max_live_calls == 3 and expected.fetches


def test_the_contradiction_plans_no_request_and_no_source() -> None:
    plan = manifest(push("GAMES_TEAMS", team_years((2023, 2024), same_year=True)))
    expected = query("contradiction").expected
    assert plan["planned_requests"] == 0 and plan["slice_keys"] == []
    assert plan["sources"] == [] and expected.slice_keys == ()
    assert expected.max_live_calls == 0 and not expected.fetches


def test_the_stored_query_plans_no_fetch_and_no_budget() -> None:
    filter_node = f.and_(
        f.between(col("P_YEAR", "GAMES"), f.num(1869), f.num(2025)),
        f.eq(col("REQ_SOURCE", "GAMES"), f.text("STORED")),
    )
    sql = push("GAMES", filter_node)
    plan = manifest(sql)
    expected = query("stored").expected
    assert plan["budget"] is None and plan["sources"] == ["STORED"]
    assert tuple(plan["slice_keys"]) == expected.slice_keys and plan["planned_requests"] == 157
    assert FETCH_SCRIPT not in sql
    assert expected.max_live_calls == 0 and not expected.fetches and expected.budget is None


def test_over_budget_is_refused_at_plan_time_with_the_fixed_message() -> None:
    expected = query("over-budget").expected
    with pytest.raises(BudgetError) as caught:
        push("GAMES_TEAMS", team_years((2010, 2024)))
    assert expected.refused is not None and expected.refused in str(caught.value)
    assert str(caught.value).startswith("CFBD_VS.GAMES_TEAMS: 15 requests are planned")
    assert expected.max_live_calls == 0 and expected.slice_keys == ()


def test_each_query_states_how_many_calls_a_run_may_confirm() -> None:
    assert {item.id: item.expected.max_live_calls for item in CONSOLE_QUERIES} == {
        "fan-out": 3,
        "contradiction": 0,
        "stored": 0,
        "over-budget": 0,
    }


def test_the_help_text_points_at_the_pages_stored_history() -> None:
    assert "1897" in console_catalog.HELP_TEXT and "0 calls" in console_catalog.HELP_TEXT
    assert "no virtual table" in console_catalog.HELP_TEXT


def test_every_query_has_a_title_and_a_summary() -> None:
    for item in CONSOLE_QUERIES:
        assert item.title and item.summary
