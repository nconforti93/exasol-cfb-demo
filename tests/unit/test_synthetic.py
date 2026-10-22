import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.synthetic import fake
from vs.cfbd_vs.catalog import Catalog, Table
from vs.cfbd_vs.flatten import FlattenError, data_columns, flatten
from vs.cfbd_vs.plan import plan_requests
from vs.cfbd_vs.rules import KNOWN_FEATURES, RuleViolation, evaluate

CATALOG = Catalog.from_json(Path("vs/catalog/catalog.json").read_text(encoding="utf-8"))
TABLE_NAMES = [t.name for t in CATALOG.tables]
GATED = [t.name for t in CATALOG.tables if t.gated_by]
ENDPOINT_TABLES = [t.name for t in CATALOG.tables if t.kind == "endpoint"]
NO_FEATURES: frozenset[str] = frozenset()
OFFSET_TIMESTAMP = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}(Z|[+-]\d{2}:\d{2})")


def table(name: str) -> Table:
    return CATALOG.table(name)


def body_of(name: str) -> Any:
    path, query = fake.request_for(table(name))
    return fake.body_for(path, query)


def column_names(name: str) -> list[str]:
    return [c.name for c in data_columns(table(name))]


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_every_table_flattens_into_rows_aligned_with_its_data_columns(name: str) -> None:
    rows = flatten(table(name), body_of(name))
    width = len(column_names(name))
    assert rows
    assert all(len(row) == width for row in rows)


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_every_data_column_has_a_value_in_some_row(name: str) -> None:
    rows = flatten(table(name), body_of(name))
    empty = [
        column
        for index, column in enumerate(column_names(name))
        if all(row[index] is None for row in rows)
    ]
    assert empty == []


@pytest.mark.parametrize("name", [t.name for t in CATALOG.tables if t.kind == "child"])
def test_child_tables_get_at_least_two_rows(name: str) -> None:
    assert len(flatten(table(name), body_of(name))) >= 2


def test_every_catalog_endpoint_has_a_response() -> None:
    assert set(fake.endpoints()) == {t.endpoint for t in CATALOG.tables}


@pytest.mark.parametrize("name", ENDPOINT_TABLES)
def test_array_responses_hold_two_elements(name: str) -> None:
    body = body_of(name)
    if isinstance(body, list):
        assert len(body) == 2


def test_nested_arrays_hold_two_elements_each() -> None:
    body = fake.body_for("/playoffs/cfp", {"year": "2024"})
    rounds = body["rounds"]
    assert len(rounds) == 2
    assert all(len(r["matchups"]) == 2 for r in rounds)
    assert all(len(m["slots"]) == 2 for r in rounds for m in r["matchups"])


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_responses_are_deterministic(name: str) -> None:
    path, query = fake.request_for(table(name))
    assert fake.response_for(path, query) == fake.response_for(path, query)
    assert json.loads(fake.response_for(path, query)) == fake.body_for(path, query)


def test_bodies_are_independent_copies() -> None:
    first = fake.body_for("/games", {"year": "2024"})
    first[0]["id"] = -1
    assert fake.body_for("/games", {"year": "2024"})[0]["id"] != -1


@pytest.mark.parametrize(
    "name",
    [t.name for t in CATALOG.tables if any(c.sql_type == "TIMESTAMP" for c in data_columns(t))],
)
def test_timestamps_carry_an_offset(name: str) -> None:
    text = fake.response_for(*fake.request_for(table(name))).decode("utf-8")
    assert OFFSET_TIMESTAMP.search(text)


def test_games_fixture_has_offset_timestamps_that_convert_to_utc() -> None:
    rows = flatten(table("GAMES"), fake.body_for("/games", {"year": "2024", "week": "3"}))
    names = column_names("GAMES")
    starts = [row[names.index("START_DATE")] for row in rows]
    assert all(isinstance(s, datetime) and s.tzinfo is None for s in starts)
    raw = fake.body_for("/games", {"year": "2024", "week": "3"})
    offsets = {item["startDate"][-6:] for item in raw}
    assert len(offsets) == 2


def test_games_fixture_echoes_year_and_week() -> None:
    rows = flatten(table("GAMES"), fake.body_for("/games", {"year": "2023", "week": "5"}))
    names = column_names("GAMES")
    assert {row[names.index("SEASON")] for row in rows} == {2023}
    assert {row[names.index("WEEK")] for row in rows} == {5}


def test_rankings_fixture_nests_polls_and_ranks() -> None:
    body = fake.body_for("/rankings", {"year": "2024"})
    assert len(body) == 2
    assert [p["poll"] for p in body[0]["polls"]] == ["AP Top 25", "Coaches Poll"]
    assert [r["rank"] for r in body[0]["polls"][0]["ranks"]] == [1, 2]


def test_games_teams_fixture_joins_game_ids_to_team_stats() -> None:
    body = fake.body_for("/games/teams", {"year": "2024", "week": "1"})
    assert [t["homeAway"] for t in body[0]["teams"]] == ["home", "away"]
    assert len(body[0]["teams"][0]["stats"]) == 2
    rows = flatten(table("GAMES_TEAMS"), body)
    assert [row[column_names("GAMES_TEAMS").index("GAME_ID")] for row in rows] == [
        body[0]["id"],
        body[0]["id"],
        body[1]["id"],
        body[1]["id"],
    ]


def test_matchup_fixture_echoes_the_teams() -> None:
    body = fake.body_for("/teams/matchup", {"team1": "Georgia", "team2": "Alabama"})
    assert (body["team1"], body["team2"]) == ("Georgia", "Alabama")
    assert len(body["games"]) == 2


def test_preview_fixture_echoes_the_game_id_from_the_path() -> None:
    body = fake.body_for("/games/401628319/preview", {})
    assert body["game"]["id"] == 401628319
    row = flatten(table("GAMES_PREVIEW"), body)[0]
    names = column_names("GAMES_PREVIEW")
    assert row[names.index("GAME_ID")] == 401628319
    assert json.loads(row[names.index("RAW_JSON")])["game"]["id"] == 401628319


def test_unknown_endpoint_is_rejected() -> None:
    with pytest.raises(fake.UnknownEndpoint):
        fake.body_for("/no/such/endpoint", {})


def test_path_parameters_resolve_to_the_template() -> None:
    assert fake.resolve_path("/games/77/preview/adjusted") == (
        "/games/{gameId}/preview/adjusted",
        {"gameId": "77"},
    )
    assert fake.resolve_path("/games/teams") == ("/games/teams", {})


def test_literal_segments_win_over_placeholders() -> None:
    template, params = fake.resolve_path("/teams/matchup")
    assert (template, params) == ("/teams/matchup", {})


@pytest.mark.parametrize("name", [n for n in TABLE_NAMES if n not in GATED])
def test_minimal_filter_satisfies_the_rules(name: str) -> None:
    evaluate(table(name), _as_sets(fake.minimal_filter(name)), NO_FEATURES)


@pytest.mark.parametrize("name", GATED)
def test_gated_tables_need_their_feature(name: str) -> None:
    t = table(name)
    assert t.gated_by in KNOWN_FEATURES
    with pytest.raises(RuleViolation, match="gating"):
        evaluate(t, _as_sets(fake.minimal_filter(name)), NO_FEATURES)
    evaluate(t, _as_sets(fake.minimal_filter(name)), frozenset({t.gated_by or ""}))


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_minimal_filter_plans_exactly_one_request(name: str) -> None:
    t = table(name)
    values = _as_sets(fake.minimal_filter(name))
    assert len(plan_requests(t, values, budget=1).slice_keys) == 1
    evaluated = evaluate(t, values, frozenset(KNOWN_FEATURES))
    plan = plan_requests(t, evaluated.constraints, budget=1)
    assert plan.slice_keys == (fake.slice_key_for(name),)


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_minimal_filter_has_no_redundant_parameter(name: str) -> None:
    t = table(name)
    filters = fake.minimal_filter(name)
    path_params = {p.name for p in t.params if p.location == "path"}
    for dropped in filters:
        if dropped in path_params:
            continue
        reduced = {k: v for k, v in filters.items() if k != dropped}
        with pytest.raises(RuleViolation):
            evaluate(t, _as_sets(reduced), frozenset(KNOWN_FEATURES))


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_minimal_filter_includes_every_path_parameter(name: str) -> None:
    t = table(name)
    filters = fake.minimal_filter(name)
    assert {p.name for p in t.params if p.location == "path"} <= set(filters)


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_request_for_routes_back_to_the_table_endpoint(name: str) -> None:
    path, query = fake.request_for(table(name))
    assert "{" not in path
    assert fake.resolve_path(path)[0] == table(name).endpoint
    assert all(isinstance(v, str) for v in query.values())


def test_known_filters_and_slice_keys() -> None:
    assert fake.minimal_filter("GAMES") == {"year": 2024}
    assert fake.minimal_filter("GAMES_TEAMS") == {"year": 2024, "week": 1}
    assert fake.slice_key_for("GAMES") == "/games?seasonType=both&year=2024"
    assert fake.request_for(table("GAMES")) == (
        "/games",
        {"seasonType": "both", "year": "2024"},
    )
    assert set(fake.minimal_filter("TEAMS_SEASON_OVERVIEW")) == {"year", "team"}
    assert "gameId" in fake.minimal_filter("PLAYS_STATS")
    assert fake.slice_key_for("GAMES_PREVIEW").endswith("/preview")


def test_minimal_filter_respects_bounds() -> None:
    for name in ("INFO_USAGE", "INFO_USAGE_TOP_ENDPOINTS"):
        filters = fake.minimal_filter(name)
        assert int(filters.get("days", 1)) <= 31
        assert int(filters.get("limit", 1)) <= 50


def test_tables_are_accepted_by_name_or_object() -> None:
    assert fake.minimal_filter("GAMES") == fake.minimal_filter(table("GAMES"))
    assert fake.slice_key_for("GAMES") == fake.slice_key_for(table("GAMES"))


NO_VARCHAR_VALUE = ["METRICS_FG_EP", "PPA_PREDICTED"]


@pytest.mark.parametrize("name", [n for n in TABLE_NAMES if n not in NO_VARCHAR_VALUE])
def test_oversized_variant_overflows_a_varchar(name: str) -> None:
    with pytest.raises(FlattenError, match="exceeds"):
        flatten(table(name), fake.oversized_variant(table(name)))


@pytest.mark.parametrize("name", NO_VARCHAR_VALUE)
def test_oversized_variant_needs_a_varchar_value_column(name: str) -> None:
    with pytest.raises(LookupError, match=name):
        fake.oversized_variant(table(name))


@pytest.mark.parametrize("name", TABLE_NAMES)
def test_invalid_type_variant_is_rejected(name: str) -> None:
    with pytest.raises(FlattenError, match=r"expected|not an ISO"):
        flatten(table(name), fake.invalid_type_variant(table(name)))


@pytest.mark.parametrize("name", ["GAMES", "TEAMS_MATCHUP", "STATS_CATEGORIES"])
def test_large_response_exceeds_two_megabytes_and_still_flattens(name: str) -> None:
    path, query = fake.request_for(table(name))
    payload = fake.large_response_for(path, query)
    assert len(payload) >= fake.LARGE_BODY_BYTES
    assert fake.LARGE_BODY_BYTES > 2 * 1024 * 1024
    assert flatten(table(name), json.loads(payload))


def test_large_response_of_a_raw_json_table_overflows_raw_json() -> None:
    path, query = fake.request_for(table("GAMES_PREVIEW"))
    payload = fake.large_response_for(path, query)
    with pytest.raises(FlattenError, match=r"RAW_JSON.*exceeds"):
        flatten(table("GAMES_PREVIEW"), json.loads(payload))


def _as_sets(filters: dict[str, Any]) -> dict[str, list[Any]]:
    return {name: [value] for name, value in filters.items()}
