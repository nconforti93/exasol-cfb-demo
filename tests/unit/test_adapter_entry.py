"""Adapter entry: the Exasol virtual schema API requests and the pushdown pipeline (task 3.7).

Requests and responses follow the Exasol Virtual Schema API (createVirtualSchema, refresh,
setProperties, dropVirtualSchema, getCapabilities, pushdown). The spec scenarios run against the
committed catalog, so the plans here are the plans `EXPLAIN VIRTUAL` shows on Exasol.
"""

import json
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.unit import filter_fixtures as f
from vs.cfbd_vs import adapter, constraints
from vs.cfbd_vs.adapter import (
    CAPABILITIES,
    MAX_PLANNED_SLICES,
    Adapter,
    AdapterError,
    adapter_call,
    data_type,
)
from vs.cfbd_vs.catalog import Catalog, Rule
from vs.cfbd_vs.constraints import ConstraintError
from vs.cfbd_vs.filters import COMPARISON_OPERATORS, LITERAL_TYPES, PushdownError, parse_filter
from vs.cfbd_vs.plan import BudgetError
from vs.cfbd_vs.properties import KNOWN_PROPERTIES, PropertyError
from vs.cfbd_vs.rules import RuleViolation
from vs.cfbd_vs.sqlrender import column_sql_types, zero_row_select

CATALOG_PATH = Path(__file__).resolve().parents[2] / "vs" / "catalog" / "catalog.json"
REAL = Catalog.from_json(CATALOG_PATH.read_text(encoding="utf-8"))
SCHEMA = "CFBD_VS"


def synthetic_catalog(**games_changes: Any) -> Catalog:
    games = replace(f.games_table(), **games_changes)
    return Catalog(
        catalog_hash="h" * 64,
        spec_version="5.33.0",
        generator_version="1",
        excluded=(),
        tables=(games,),
    )


def info(properties: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"name": SCHEMA}
    if properties is not None:
        data["properties"] = properties
    return data


def pushdown_request(
    table: str, filter_node: dict[str, Any] | None, properties: dict[str, Any] | None = None
) -> dict[str, Any]:
    request: dict[str, Any] = {"type": "select", "from": {"type": "table", "name": table}}
    if filter_node is not None:
        request["filter"] = filter_node
    return {
        "type": "pushdown",
        "pushdownRequest": request,
        "involvedTables": [{"name": table, "columns": []}],
        "schemaMetadataInfo": info(properties),
    }


def push(
    catalog: Catalog,
    table: str,
    filter_node: dict[str, Any] | None,
    properties: dict[str, Any] | None = None,
) -> str:
    response = Adapter(catalog).handle(pushdown_request(table, filter_node, properties))
    assert response["type"] == "pushdown"
    return str(response["sql"])


def manifest(sql: str) -> dict[str, Any]:
    match = re.fullmatch(r"/\*CFBD (.*)\*/", sql.split("\n", 1)[0])
    assert match is not None
    return dict(json.loads(match.group(1)))


def col(name: str, table: str) -> dict[str, Any]:
    return {"type": "column", "tableName": table, "columnNr": 0, "name": name}


# --- getCapabilities ---

PINNED_CAPABILITIES = [
    "FILTER_EXPRESSIONS",
    "FN_PRED_AND",
    "FN_PRED_OR",
    "FN_PRED_NOT",
    "FN_PRED_EQUAL",
    "FN_PRED_NOTEQUAL",
    "FN_PRED_LESS",
    "FN_PRED_LESSEQUAL",
    "FN_PRED_BETWEEN",
    "FN_PRED_IN_CONSTLIST",
    "FN_PRED_LIKE",
    "FN_PRED_LIKE_ESCAPE",
    "FN_PRED_IS_NULL",
    "FN_PRED_IS_NOT_NULL",
    "LITERAL_NULL",
    "LITERAL_BOOL",
    "LITERAL_DATE",
    "LITERAL_TIMESTAMP",
    "LITERAL_DOUBLE",
    "LITERAL_EXACTNUMERIC",
    "LITERAL_STRING",
]


def test_capabilities_are_exactly_the_pinned_list() -> None:
    response = Adapter(REAL).handle({"type": "getCapabilities", "schemaMetadataInfo": info()})

    assert response == {"type": "getCapabilities", "capabilities": PINNED_CAPABILITIES}
    assert list(CAPABILITIES) == PINNED_CAPABILITIES


def test_capability_names_use_the_probed_spelling() -> None:
    assert "FN_PRED_LESSEQUAL" in CAPABILITIES
    assert not any(name.endswith("LESSEQUALS") for name in CAPABILITIES)


def test_no_aggregate_projection_ordering_limit_or_join_capability() -> None:
    for name in CAPABILITIES:
        assert name == "FILTER_EXPRESSIONS" or name.startswith(("FN_PRED_", "LITERAL_"))


def test_literal_capabilities_match_the_literals_the_parser_accepts() -> None:
    advertised = {
        name.removeprefix("LITERAL_").lower() for name in CAPABILITIES if "LITERAL_" in name
    }

    assert advertised == set(LITERAL_TYPES)


def sample_predicates() -> dict[str, dict[str, Any]]:
    year, venue = f.col("P_YEAR"), f.col("VENUE")
    samples = {
        "FN_PRED_AND": f.and_(f.eq(year, f.num(1)), f.eq(year, f.num(1))),
        "FN_PRED_OR": f.or_(f.eq(year, f.num(1)), f.eq(year, f.num(2))),
        "FN_PRED_NOT": f.not_(f.eq(venue, f.text("a"))),
        "FN_PRED_BETWEEN": f.between(year, f.num(1), f.num(2)),
        "FN_PRED_IN_CONSTLIST": f.in_list(year, f.num(1), f.num(2)),
        "FN_PRED_LIKE": f.like(venue, f.text("a%")),
        "FN_PRED_LIKE_ESCAPE": f.like(venue, f.text("a%"), f.text("!")),
        "FN_PRED_IS_NULL": f.is_null(venue),
        "FN_PRED_IS_NOT_NULL": f.is_not_null(venue),
    }
    for node_type in COMPARISON_OPERATORS:
        name = "FN_PRED_" + node_type.removeprefix("predicate_").upper()
        samples[name] = {"type": node_type, "left": year, "right": f.num(1)}
    return samples


def test_every_advertised_predicate_is_parsed() -> None:
    samples = sample_predicates()
    predicates = {name for name in CAPABILITIES if name.startswith("FN_PRED_")}

    assert set(samples) == predicates
    for node in samples.values():
        assert parse_filter(node, f.games_table()) is not None


# --- createVirtualSchema and refresh ---


def test_create_returns_every_catalog_table_with_typed_commented_columns() -> None:
    response = Adapter(REAL).handle(
        {"type": "createVirtualSchema", "schemaMetadataInfo": info({"CONNECTION_NAME": "CFBD_API"})}
    )

    assert response["type"] == "createVirtualSchema"
    tables = response["schemaMetadata"]["tables"]
    assert [t["name"] for t in tables] == sorted(t.name for t in REAL.tables)
    games = next(t for t in tables if t["name"] == "GAMES")
    catalog_games = REAL.table("GAMES")
    assert [c["name"] for c in games["columns"]] == [c.name for c in catalog_games.columns]
    assert [c["dataType"] for c in games["columns"]] == [
        data_type(t) for _, t in column_sql_types(catalog_games)
    ]
    for meta, column in zip(games["columns"], catalog_games.columns, strict=True):
        assert meta.get("comment") == column.comment
    assert all(t["type"] == "table" for t in tables)


def test_every_comment_fits_exasol_limit() -> None:
    response = Adapter(REAL).handle({"type": "createVirtualSchema", "schemaMetadataInfo": info()})

    for table in response["schemaMetadata"]["tables"]:
        assert 0 < len(table["comment"]) <= 2000
        for column in table["columns"]:
            assert len(column.get("comment", "")) <= 2000


def test_table_comments_name_endpoint_parent_gating_and_volatility() -> None:
    tables = {
        t["name"]: t["comment"]
        for t in Adapter(REAL).handle({"type": "refresh", "schemaMetadataInfo": info()})[
            "schemaMetadata"
        ]["tables"]
    }

    assert "GET /games" in tables["GAMES"]
    assert "RANKINGS" in tables["RANKINGS_POLLS_RANKS"]
    assert "weather" in tables["GAMES_WEATHER"]
    assert "API_FEATURES" in tables["GAMES_WEATHER"]
    assert "always fetched" in tables["INFO"]
    assert "always fetched" not in tables["GAMES"]


def test_column_without_comment_has_no_comment_key() -> None:
    columns = Adapter(synthetic_catalog()).handle(
        {"type": "createVirtualSchema", "schemaMetadataInfo": info()}
    )["schemaMetadata"]["tables"][0]["columns"]

    assert all("comment" not in column for column in columns)


def test_create_rejects_invalid_properties() -> None:
    with pytest.raises(PropertyError, match="MAX_CALLS_PER_QUERY"):
        Adapter(REAL).handle(
            {
                "type": "createVirtualSchema",
                "schemaMetadataInfo": info({"MAX_CALLS_PER_QUERY": "0"}),
            }
        )


def test_refresh_reproduces_the_create_metadata() -> None:
    create = Adapter(REAL).handle({"type": "createVirtualSchema", "schemaMetadataInfo": info()})
    refresh = Adapter(REAL).handle({"type": "refresh", "schemaMetadataInfo": info()})

    assert refresh == {"type": "refresh", "schemaMetadata": create["schemaMetadata"]}


def test_refresh_of_requested_tables_returns_only_those_and_echoes_the_request() -> None:
    response = Adapter(REAL).handle(
        {
            "type": "refresh",
            "schemaMetadataInfo": info(),
            "requestedTables": ["GAMES", "NO_SUCH_TABLE", "INFO"],
        }
    )

    assert response["requestedTables"] == ["GAMES", "NO_SUCH_TABLE", "INFO"]
    assert [t["name"] for t in response["schemaMetadata"]["tables"]] == ["GAMES", "INFO"]


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("DECIMAL(18,0)", {"type": "DECIMAL", "precision": 18, "scale": 0}),
        ("DECIMAL(3,0)", {"type": "DECIMAL", "precision": 3, "scale": 0}),
        ("DOUBLE", {"type": "DOUBLE"}),
        ("BOOLEAN", {"type": "BOOLEAN"}),
        ("DATE", {"type": "DATE"}),
        ("TIMESTAMP", {"type": "TIMESTAMP", "withLocalTimeZone": False}),
        ("VARCHAR(2000) UTF8", {"type": "VARCHAR", "size": 2000, "characterSet": "UTF8"}),
        ("VARCHAR(8) ASCII", {"type": "VARCHAR", "size": 8, "characterSet": "ASCII"}),
    ],
)
def test_sql_types_map_to_exasol_data_types(sql_type: str, expected: dict[str, Any]) -> None:
    assert data_type(sql_type) == expected


def test_unsupported_sql_types_are_refused() -> None:
    with pytest.raises(ValueError, match="CHAR"):
        data_type("CHAR(10) UTF8")


# --- setProperties and dropVirtualSchema ---


def set_properties(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    return Adapter(REAL).handle(
        {"type": "setProperties", "schemaMetadataInfo": info(current), "properties": changes}
    )


def test_set_properties_accepts_valid_changes_without_new_metadata() -> None:
    response = set_properties({"FETCH_MODE": "replay"}, {"MAX_CALLS_PER_QUERY": "12"})

    assert response == {"type": "setProperties"}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_CALLS_PER_QUERY", "0"),
        ("MAX_CALLS_PER_QUERY", "ten"),
        ("FETCH_MODE", "record"),
        ("READ_THROUGH", "maybe"),
        ("API_FEATURES", "weather,teleport"),
        ("DOH_URL", "http://1.1.1.1/dns-query"),
        ("DOH_URL", "https://cloudflare-dns.com/dns-query"),
    ],
)
def test_set_properties_rejects_invalid_values(name: str, value: str) -> None:
    with pytest.raises(PropertyError) as caught:
        set_properties({"MAX_CALLS_PER_QUERY": "10"}, {name: value})

    assert name in str(caught.value)
    assert "must be" in str(caught.value)


def test_a_rejected_change_leaves_the_request_untouched() -> None:
    current = {"MAX_CALLS_PER_QUERY": "10"}
    changes = {"MAX_CALLS_PER_QUERY": "0"}

    with pytest.raises(PropertyError):
        set_properties(current, changes)

    assert current == {"MAX_CALLS_PER_QUERY": "10"}
    assert changes == {"MAX_CALLS_PER_QUERY": "0"}


def test_set_properties_rejects_unknown_names_listing_the_known_ones() -> None:
    with pytest.raises(PropertyError) as caught:
        set_properties({}, {"MAX_CALL": "5"})

    assert "MAX_CALL" in str(caught.value)
    for known in KNOWN_PROPERTIES:
        assert known in str(caught.value)


def test_set_properties_validates_the_merged_result_and_allows_deleting() -> None:
    assert set_properties({"FETCH_MODE": "replay"}, {"FETCH_MODE": None}) == {
        "type": "setProperties"
    }


def test_drop_always_succeeds_even_with_invalid_properties() -> None:
    response = Adapter(REAL).handle(
        {"type": "dropVirtualSchema", "schemaMetadataInfo": info({"MAX_CALLS_PER_QUERY": "0"})}
    )

    assert response == {"type": "dropVirtualSchema"}


def test_unknown_request_type_is_refused() -> None:
    with pytest.raises(AdapterError, match="teleport"):
        Adapter(REAL).handle({"type": "teleport"})


# --- pushdown: envelope ---


def test_pushdown_of_an_unknown_table_asks_for_a_refresh() -> None:
    with pytest.raises(PushdownError) as caught:
        push(REAL, "NO_SUCH_TABLE", None)

    assert str(caught.value).startswith("CFBD_VS.NO_SUCH_TABLE: ")
    assert "REFRESH" in str(caught.value)


def test_pushdown_from_something_other_than_one_table_is_refused() -> None:
    request = pushdown_request("GAMES", None)
    request["pushdownRequest"]["from"] = {"type": "join"}

    with pytest.raises(AdapterError, match="join"):
        Adapter(REAL).handle(request)


def test_pushdown_rejects_invalid_properties() -> None:
    with pytest.raises(PropertyError, match="FETCH_MODE"):
        push(
            REAL, "RATINGS_SP", f.eq(col("P_YEAR", "RATINGS_SP"), f.num(2024)), {"FETCH_MODE": "x"}
        )


def test_pushdown_uses_the_embedded_catalog_hash() -> None:
    sql = push(REAL, "RATINGS_SP", f.eq(col("P_YEAR", "RATINGS_SP"), f.num(2024)))

    assert f"'{REAL.catalog_hash}'" in sql


# --- pushdown: spec scenarios against the committed catalog ---


def year(table: str) -> dict[str, Any]:
    return col("P_YEAR", table)


def test_equality_becomes_one_request() -> None:
    sql = push(REAL, "RATINGS_SP", f.eq(year("RATINGS_SP"), f.num(2024)))

    assert manifest(sql)["slice_keys"] == ["/ratings/sp?year=2024"]
    assert manifest(sql)["planned_requests"] == 1


def test_bounded_ranges_are_enumerated_both_ways() -> None:
    between = push(REAL, "RATINGS_SP", f.between(year("RATINGS_SP"), f.num(2019), f.num(2021)))
    bounds = push(
        REAL,
        "RATINGS_SP",
        f.and_(
            f.greater_eq(year("RATINGS_SP"), f.num(2019)), f.less(year("RATINGS_SP"), f.num(2022))
        ),
    )

    expected = [f"/ratings/sp?year={y}" for y in (2019, 2020, 2021)]
    assert manifest(between)["slice_keys"] == expected
    assert manifest(bounds)["slice_keys"] == expected


def test_open_ranges_are_rejected() -> None:
    with pytest.raises(ConstraintError, match="P_YEAR"):
        push(REAL, "RATINGS_SP", f.greater_eq(year("RATINGS_SP"), f.num(2020)))


def test_range_and_in_list_fan_out_with_the_explicit_default() -> None:
    filter_node = f.and_(
        f.between(year("GAMES"), f.num(2019), f.num(2021)),
        f.in_list(col("P_WEEK", "GAMES"), f.num(8), f.num(9)),
    )

    keys = manifest(push(REAL, "GAMES", filter_node))["slice_keys"]

    assert len(keys) == 6
    assert all("seasonType=both" in key for key in keys)
    assert "/games?seasonType=both&week=8&year=2019" in keys


def test_over_budget_is_rejected_with_the_fixed_message() -> None:
    with pytest.raises(BudgetError) as caught:
        push(REAL, "GAMES", f.between(year("GAMES"), f.num(2000), f.num(2024)))

    assert str(caught.value) == (
        "CFBD_VS.GAMES: 25 requests are planned but MAX_CALLS_PER_QUERY is 10. "
        "Use a narrower filter or raise MAX_CALLS_PER_QUERY."
    )


def test_exactly_at_the_budget_is_allowed() -> None:
    sql = push(REAL, "GAMES", f.between(year("GAMES"), f.num(2015), f.num(2024)))

    assert manifest(sql)["planned_requests"] == 10


def test_property_change_applies_to_the_next_query() -> None:
    filter_node = f.between(year("GAMES"), f.num(2013), f.num(2024))
    with pytest.raises(BudgetError):
        push(REAL, "GAMES", filter_node, {"MAX_CALLS_PER_QUERY": "10"})

    sql = push(REAL, "GAMES", filter_node, {"MAX_CALLS_PER_QUERY": "12"})

    assert manifest(sql)["planned_requests"] == 12
    assert manifest(sql)["budget"] == 12


def test_contradictions_return_typed_zero_rows_before_rules() -> None:
    filter_node = f.and_(f.eq(year("PLAYS"), f.num(2020)), f.eq(year("PLAYS"), f.num(2021)))

    sql = push(REAL, "PLAYS", filter_node)

    assert sql.split("\n", 1)[1] == zero_row_select(REAL.table("PLAYS"))
    assert manifest(sql)["planned_requests"] == 0
    assert "CFBD_FETCH" not in sql


def test_missing_required_parameter_names_rule_and_column() -> None:
    with pytest.raises(RuleViolation) as caught:
        push(REAL, "PLAYS", f.eq(year("PLAYS"), f.num(2024)))

    assert "CFBD_VS.PLAYS: rule plays.spec_required" in str(caught.value)
    assert "P_WEEK" in str(caught.value)


def test_gated_table_fails_closed_and_works_with_the_feature() -> None:
    filter_node = f.eq(year("GAMES_WEATHER"), f.num(2024))
    with pytest.raises(RuleViolation, match="weather"):
        push(REAL, "GAMES_WEATHER", filter_node)

    sql = push(REAL, "GAMES_WEATHER", filter_node, {"API_FEATURES": "weather"})

    assert manifest(sql)["slice_keys"] == ["/games/weather?seasonType=both&year=2024"]


def test_rules_and_gating_come_before_the_budget() -> None:
    with pytest.raises(RuleViolation, match="weather"):
        push(REAL, "GAMES_WEATHER", f.between(year("GAMES_WEATHER"), f.num(2000), f.num(2024)))


def test_hidden_default_is_sent_and_a_filter_replaces_it() -> None:
    default = push(REAL, "STATS_SEASON", f.eq(year("STATS_SEASON"), f.num(2024)))
    fcs = push(
        REAL,
        "STATS_SEASON",
        f.and_(
            f.eq(year("STATS_SEASON"), f.num(2024)),
            f.eq(col("P_CLASSIFICATION", "STATS_SEASON"), f.text("fcs")),
        ),
    )

    assert manifest(default)["slice_keys"] == ["/stats/season?classification=fbs&year=2024"]
    assert manifest(fcs)["slice_keys"] == ["/stats/season?classification=fcs&year=2024"]


def test_child_tables_enforce_the_rules_of_their_endpoint() -> None:
    with pytest.raises(RuleViolation, match=r"rankings\.spec_required"):
        push(REAL, "RANKINGS_POLLS_RANKS", None)


def test_residual_predicates_are_reapplied_without_changing_requests() -> None:
    filter_node = f.and_(
        f.eq(year("GAMES"), f.num(2024)),
        f.or_(
            f.greater(col("HOME_POINTS", "GAMES"), f.num(50)),
            f.greater(col("AWAY_POINTS", "GAMES"), f.num(50)),
        ),
        f.like(col("VENUE", "GAMES"), f.text("M%")),
    )

    sql = push(REAL, "GAMES", filter_node)

    assert manifest(sql)["slice_keys"] == ["/games?seasonType=both&year=2024"]
    assert sql.endswith(
        ') WHERE ((50 < "HOME_POINTS") OR (50 < "AWAY_POINTS")) AND ("VENUE" LIKE \'M%\')'
    )


def test_volatile_tables_are_always_fetched() -> None:
    sql = push(REAL, "INFO", None)

    assert "LOAD_LOG" not in sql
    assert manifest(sql)["read_through"] is False
    assert manifest(sql)["slice_keys"] == ["/info"]


def test_read_through_switch_and_fetch_mode_and_schema_properties_apply() -> None:
    properties = {
        "READ_THROUGH": "FALSE",
        "FETCH_MODE": "replay",
        "INFRA_SCHEMA": "TEST_CFB_INFRA",
        "CONNECTION_NAME": "CFBD_API_MOCK",
    }

    sql = push(REAL, "RATINGS_SP", f.eq(year("RATINGS_SP"), f.num(2024)), properties)

    assert "LOAD_LOG" not in sql
    assert '"TEST_CFB_INFRA"."CFBD_FETCH"(' in sql
    assert "'CFBD_API_MOCK'" in sql
    assert manifest(sql)["fetch_mode"] == "replay"
    assert manifest(sql)["sources"] == ["REPLAY"]


def test_defaults_show_in_the_manifest() -> None:
    sql = push(REAL, "RATINGS_SP", f.eq(year("RATINGS_SP"), f.num(2024)), {"CONNECTION_NAME": "X"})

    found = manifest(sql)
    assert (found["fetch_mode"], found["read_through"], found["budget"]) == ("live", True, 10)


# --- stored-only queries and the hard ceiling (hardening a) ---


def stored_only(table: str, *conjuncts: dict[str, Any]) -> dict[str, Any]:
    return f.and_(*conjuncts, f.eq(col("REQ_SOURCE", table), f.text("STORED")))


def test_stored_only_queries_skip_the_budget_and_plan_no_fetch() -> None:
    sql = push(
        REAL, "GAMES", stored_only("GAMES", f.between(year("GAMES"), f.num(1869), f.num(2026)))
    )

    found = manifest(sql)
    assert found["planned_requests"] == 158
    assert found["budget"] is None
    assert found["sources"] == ["STORED"]
    assert "CFBD_FETCH" not in sql


def test_stored_only_queries_still_enforce_rules() -> None:
    with pytest.raises(RuleViolation, match="P_WEEK"):
        push(REAL, "PLAYS", stored_only("PLAYS", f.eq(year("PLAYS"), f.num(2024))))


@pytest.fixture
def forbid_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: constraints.SolvedFilter) -> None:
        raise AssertionError("value_sets() was called before the ceiling check")

    monkeypatch.setattr(constraints.SolvedFilter, "value_sets", refuse)


@pytest.mark.usefixtures("forbid_materialization")
@pytest.mark.parametrize(
    "filter_node",
    [
        stored_only("GAMES", f.between(year("GAMES"), f.num(0), f.num(10**17))),
        f.between(year("GAMES"), f.num(0), f.num(10**17)),
        stored_only(
            "GAMES",
            f.or_(
                f.in_list(year("GAMES"), f.num(1), f.num(3)),
                f.between(year("GAMES"), f.num(5), f.num(10**17)),
            ),
        ),
        stored_only(
            "GAMES",
            f.between(year("GAMES"), f.num(1), f.num(200)),
            f.between(col("P_WEEK", "GAMES"), f.num(1), f.num(200)),
        ),
    ],
)
def test_the_hard_ceiling_rejects_huge_plans_before_anything_is_materialized(
    filter_node: dict[str, Any],
) -> None:
    started = time.monotonic()

    with pytest.raises(PushdownError) as caught:
        push(REAL, "GAMES", filter_node)

    assert time.monotonic() - started < 5
    message = str(caught.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert f"{MAX_PLANNED_SLICES:,}" in message


def test_a_plan_exactly_at_the_ceiling_is_accepted_for_stored_only_queries() -> None:
    filter_node = stored_only(
        "GAMES",
        f.between(year("GAMES"), f.num(1), f.num(100)),
        f.between(col("P_WEEK", "GAMES"), f.num(1), f.num(MAX_PLANNED_SLICES // 100)),
    )

    sql = push(REAL, "GAMES", filter_node)

    assert manifest(sql)["planned_requests"] == MAX_PLANNED_SLICES


def test_the_ceiling_is_above_the_largest_budget() -> None:
    assert MAX_PLANNED_SLICES >= 1000


# --- pipeline order on a synthetic table ---


def test_unsupported_parameter_predicates_are_rejected_before_contradictions() -> None:
    catalog = synthetic_catalog()
    filter_node = f.and_(
        f.eq(f.col("P_YEAR"), f.num(2024)),
        f.eq(f.col("P_YEAR"), f.num(2025)),
        f.like(f.col("P_TEAM"), f.text("Mich%")),
    )

    with pytest.raises(ConstraintError, match="P_TEAM"):
        push(catalog, "GAMES", filter_node)


def test_contradiction_skips_gating_rules_and_budget() -> None:
    required = Rule("games.year_required", "required", "", ("year",))
    catalog = synthetic_catalog(gated_by="weather", rules=(required,))
    filter_node = f.and_(f.eq(f.col("P_TEAM"), f.text("a")), f.eq(f.col("P_TEAM"), f.text("b")))

    sql = push(catalog, "GAMES", filter_node, {"MAX_CALLS_PER_QUERY": "1"})

    assert manifest(sql)["planned_requests"] == 0


# --- adapter_call: the JSON entry point of the adapter script ---


def test_adapter_call_round_trips_json_with_the_embedded_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter, "CATALOG_JSON", synthetic_catalog().to_json(indent=None))
    monkeypatch.setattr(adapter, "_embedded_catalog", None)
    request = pushdown_request("GAMES", f.eq(f.col("P_YEAR"), f.num(2024)))

    response = json.loads(adapter_call(json.dumps(request)))

    assert response["type"] == "pushdown"
    assert manifest(response["sql"])["slice_keys"] == ["/games?year=2024"]


def test_adapter_call_without_an_embedded_catalog_says_how_to_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter, "CATALOG_JSON", None)
    monkeypatch.setattr(adapter, "_embedded_catalog", None)

    with pytest.raises(AdapterError, match="bundle"):
        adapter_call(json.dumps({"type": "getCapabilities"}))


def test_adapter_call_rejects_malformed_json() -> None:
    with pytest.raises(AdapterError, match="JSON"):
        adapter_call("{not json")
