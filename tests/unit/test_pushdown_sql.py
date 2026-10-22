"""Pushdown SQL builder: the shapes, their structure and the stored-only pruning (task 3.6).

Golden SQL pins one example of every shape; the structural tests assert the invariants that
matter on Exasol regardless of formatting: exact column types (probe 1.4c), the residual in the
outer WHERE (1.4e), one SET group without GROUP BY ([18]), the NULL-row guard of an empty SET
group, the same LOAD_LOG predicate in both branches, and the schema names from the properties.
"""

import json
import re
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.filter_fixtures import games_table, make_param
from vs.cfbd_vs import fetch
from vs.cfbd_vs.catalog import Column, Table
from vs.cfbd_vs.filters import (
    And,
    Between,
    ColumnRef,
    Comparison,
    Expr,
    InList,
    IsNotNull,
    IsNull,
    Like,
    Literal,
    Not,
    Or,
    PushdownError,
)
from vs.cfbd_vs.naming import META_COLUMNS
from vs.cfbd_vs.plan import RequestPlan
from vs.cfbd_vs.properties import Properties
from vs.cfbd_vs.pushdown_sql import (
    MAX_SLICE_KEY_LENGTH,
    SliceKeyError,
    contradiction_sql,
    excludes_source,
    pushdown_sql,
    row_sources,
)
from vs.cfbd_vs.sqlrender import (
    RenderError,
    column_sql_types,
    emits_clause,
    render_conjunction,
    zero_row_select,
)

HASH = "c0ffee"
SLICES = ("/games?year=2019", "/games?year=2020")
DEFAULTS = Properties()
TEST_NAMESPACE = Properties(
    infra_schema="TEST_CFB_INFRA", meta_schema="TEST_CFB_META", core_schema="TEST_CFB_CORE"
)


def mini_table(read_through: bool = True) -> Table:
    year = make_param("year", "P_YEAR", "integer", "DECIMAL(18,0)")
    return Table(
        name="GAMES",
        endpoint="/games",
        kind="endpoint",
        parent=None,
        array_path=(),
        hoisted=False,
        read_through=read_through,
        gated_by=None,
        params=(year,),
        columns=(
            Column("ROW_NO", "position"),
            Column("HOME_POINTS", "value", ("homePoints",), "DECIMAL(18,0)"),
            Column("P_YEAR", "param"),
            *(Column(name, "meta") for name in META_COLUMNS),
        ),
        rules=(),
    )


def col(name: str) -> ColumnRef:
    return ColumnRef(name, "meta" if name.startswith("REQ_") else "value")


def text(value: str) -> Literal:
    return Literal("string", value)


def num(value: int) -> Literal:
    return Literal("exactnumeric", Decimal(value))


SOURCE = col("REQ_SOURCE")
HOME_OVER_50 = Comparison("<", num(50), col("HOME_POINTS"))


def budgeted(keys: tuple[str, ...] = SLICES, budget: int | None = 10) -> RequestPlan:
    return RequestPlan(table="GAMES", slice_keys=keys, budget=budget)


def manifest_of(sql: str) -> dict[str, Any]:
    first = sql.split("\n", 1)[0]
    match = re.fullmatch(r"/\*CFBD (.*)\*/", first)
    assert match is not None, first
    manifest: dict[str, Any] = json.loads(match.group(1))
    return manifest


def body_of(sql: str) -> str:
    return sql.split("\n", 1)[1]


# --- golden SQL: one example of every shape ---

MINI_EMITS = (
    'EMITS ("ROW_NO" DECIMAL(18,0), "HOME_POINTS" DECIMAL(18,0), "P_YEAR" DECIMAL(18,0), '
    '"REQ_SLICE" VARCHAR(2000) UTF8, "REQ_SOURCE" VARCHAR(8) UTF8, "REQ_STATUS" DECIMAL(3,0), '
    '"REQ_ATTEMPTS" DECIMAL(3,0), "REQ_MS" DECIMAL(9,0), "REQ_ROWS" DECIMAL(18,0), '
    '"REQ_AT" TIMESTAMP)'
)
STORED_BRANCH = (
    'SELECT "ROW_NO", "HOME_POINTS", "P_YEAR", "REQ_SLICE", '
    "CAST('STORED' AS VARCHAR(8) UTF8) AS \"REQ_SOURCE\", "
    'CAST(NULL AS DECIMAL(3,0)) AS "REQ_STATUS", CAST(0 AS DECIMAL(3,0)) AS "REQ_ATTEMPTS", '
    'CAST(NULL AS DECIMAL(9,0)) AS "REQ_MS", "REQ_ROWS", "REQ_AT"\n'
    'FROM "CFB_CORE"."GAMES"\n'
    "WHERE \"REQ_SLICE\" IN ('/games?year=2019', '/games?year=2020')\n"
    'AND "REQ_SLICE" IN (SELECT "SLICE_KEY" FROM "CFB_META"."LOAD_LOG" '
    'WHERE "TABLE_NAME" = \'GAMES\' AND "IS_FINAL" = TRUE)'
)
FETCH_CALL = (
    'SELECT "CFB_INFRA"."CFBD_FETCH"(\'GAMES\', "R"."SLICE_KEY", \'c0ffee\', \'live\', '
    "'CFBD_API', '/buckets/bfsdefault/default/cfbd/replay', 'https://1.1.1.1/dns-query', 6) "
    + MINI_EMITS
    + "\nFROM (VALUES ('/games?year=2019'), ('/games?year=2020')) AS \"R\"(\"SLICE_KEY\")"
)
NOT_EXISTS = (
    '\nWHERE NOT EXISTS (SELECT 1 FROM "CFB_META"."LOAD_LOG" "L" '
    'WHERE "L"."TABLE_NAME" = \'GAMES\' AND "L"."SLICE_KEY" = "R"."SLICE_KEY" '
    'AND "L"."IS_FINAL" = TRUE)'
)


def test_golden_read_through_sql() -> None:
    sql = pushdown_sql(mini_table(), budgeted(), (HOME_OVER_50,), DEFAULTS, catalog_hash=HASH)

    assert sql == (
        '/*CFBD {"budget":10,"fetch_mode":"live","planned_requests":2,"read_through":true,'
        '"slice_keys":["/games?year=2019","/games?year=2020"],"sources":["STORED","LIVE"],'
        '"table":"GAMES"}*/\n'
        "SELECT * FROM (\n"
        + STORED_BRANCH
        + "\nUNION ALL\nSELECT * FROM (\n"
        + FETCH_CALL
        + NOT_EXISTS
        + '\n) WHERE "REQ_SLICE" IS NOT NULL\n'
        ') WHERE (50 < "HOME_POINTS")'
    )


def test_golden_live_only_sql() -> None:
    props = replace(DEFAULTS, read_through=False)

    sql = pushdown_sql(mini_table(), budgeted(), (), props, catalog_hash=HASH)

    assert sql == (
        '/*CFBD {"budget":10,"fetch_mode":"live","planned_requests":2,"read_through":false,'
        '"slice_keys":["/games?year=2019","/games?year=2020"],"sources":["LIVE"],'
        '"table":"GAMES"}*/\n'
        "SELECT * FROM (\nSELECT * FROM (\n" + FETCH_CALL + '\n) WHERE "REQ_SLICE" IS NOT NULL\n'
        ") WHERE TRUE"
    )


def test_golden_stored_only_sql() -> None:
    residual = (Comparison("=", SOURCE, text("STORED")),)

    sql = pushdown_sql(mini_table(), budgeted(budget=None), residual, DEFAULTS, catalog_hash=HASH)

    assert sql == (
        '/*CFBD {"budget":null,"fetch_mode":"live","planned_requests":2,"read_through":true,'
        '"slice_keys":["/games?year=2019","/games?year=2020"],"sources":["STORED"],'
        '"table":"GAMES"}*/\n'
        "SELECT * FROM (\n" + STORED_BRANCH + "\n) WHERE (\"REQ_SOURCE\" = 'STORED')"
    )


def test_golden_zero_row_sql_for_a_contradiction() -> None:
    sql = contradiction_sql(mini_table(), DEFAULTS)

    assert sql == (
        '/*CFBD {"budget":null,"fetch_mode":"live","planned_requests":0,"read_through":true,'
        '"slice_keys":[],"sources":[],"table":"GAMES"}*/\n' + zero_row_select(mini_table())
    )


# --- structure of the read-through shape ---


@pytest.fixture
def read_through_sql() -> str:
    residual = (HOME_OVER_50, Like(col("VENUE"), text("M%"), None))
    return pushdown_sql(games_table(), budgeted(), residual, DEFAULTS, catalog_hash=HASH)


def branches(sql: str) -> tuple[str, str]:
    stored, fetch_part = body_of(sql).split("\nUNION ALL\n")
    return stored, fetch_part


def test_read_through_has_exactly_one_stored_and_one_fetch_branch(read_through_sql: str) -> None:
    assert read_through_sql.count("UNION ALL") == 1
    assert read_through_sql.count('"CFBD_FETCH"(') == 1
    assert read_through_sql.count('FROM "CFB_CORE"."GAMES"') == 1


def test_outer_where_reapplies_the_whole_residual(read_through_sql: str) -> None:
    residual = (HOME_OVER_50, Like(col("VENUE"), text("M%"), None))

    assert read_through_sql.endswith(") WHERE " + render_conjunction(residual))


def test_no_group_by_and_no_aggregate_anywhere(read_through_sql: str) -> None:
    upper = read_through_sql.upper()
    assert "GROUP BY" not in upper
    for function in ("COUNT(", "SUM(", "MIN(", "MAX(", "AVG("):
        assert function not in upper


def test_fetch_branch_emits_exactly_the_catalog_types(read_through_sql: str) -> None:
    assert read_through_sql.count(emits_clause(games_table())) == 1


def test_stored_branch_lists_every_column_in_catalog_order_with_exact_override_types(
    read_through_sql: str,
) -> None:
    stored, _ = branches(read_through_sql)
    items = stored_select_items(stored)
    types = dict(column_sql_types(games_table()))
    names = [name for name, _ in column_sql_types(games_table())]

    assert [output_name(item) for item in items] == names
    overrides = {output_name(item): item for item in items if item.startswith("CAST(")}
    assert overrides == {
        "REQ_SOURCE": f"CAST('STORED' AS {types['REQ_SOURCE']}) AS \"REQ_SOURCE\"",
        "REQ_STATUS": f'CAST(NULL AS {types["REQ_STATUS"]}) AS "REQ_STATUS"',
        "REQ_ATTEMPTS": f'CAST(0 AS {types["REQ_ATTEMPTS"]}) AS "REQ_ATTEMPTS"',
        "REQ_MS": f'CAST(NULL AS {types["REQ_MS"]}) AS "REQ_MS"',
    }


def test_stored_rows_keep_their_original_slice_rows_and_request_time(
    read_through_sql: str,
) -> None:
    stored, _ = branches(read_through_sql)
    items = stored_select_items(stored)

    for name in ("REQ_SLICE", "REQ_ROWS", "REQ_AT", "P_YEAR", "VENUE"):
        assert f'"{name}"' in items


def test_both_branches_test_the_same_final_load_log_row(read_through_sql: str) -> None:
    stored, fetch_part = branches(read_through_sql)
    log = '"CFB_META"."LOAD_LOG"'

    assert stored.count(log) == 1
    assert fetch_part.count(log) == 1
    assert '"TABLE_NAME" = \'GAMES\' AND "IS_FINAL" = TRUE' in stored
    assert '"L"."SLICE_KEY" = "R"."SLICE_KEY"' in fetch_part
    assert '"L"."TABLE_NAME" = \'GAMES\'' in fetch_part
    assert '"L"."IS_FINAL" = TRUE' in fetch_part
    assert "WHERE NOT EXISTS (SELECT 1 FROM" in fetch_part


def test_every_planned_slice_is_offered_to_both_branches_exactly_once(
    read_through_sql: str,
) -> None:
    stored, fetch_part = branches(read_through_sql)
    for key in SLICES:
        assert stored.count(f"'{key}'") == 1
        assert fetch_part.count(f"('{key}')") == 1


def test_fetch_branch_is_guarded_against_the_null_row_of_an_empty_set_group(
    read_through_sql: str,
) -> None:
    _, fetch_part = branches(read_through_sql)

    assert fetch_part.startswith('SELECT * FROM (\nSELECT "CFB_INFRA"."CFBD_FETCH"(')
    assert '\n) WHERE "REQ_SLICE" IS NOT NULL\n) WHERE ' in fetch_part


def test_manifest_leads_the_sql_and_describes_the_plan(read_through_sql: str) -> None:
    assert manifest_of(read_through_sql) == {
        "table": "GAMES",
        "fetch_mode": "live",
        "read_through": True,
        "budget": 10,
        "planned_requests": 2,
        "slice_keys": list(SLICES),
        "sources": ["STORED", "LIVE"],
    }


def test_manifest_can_never_close_its_comment_early() -> None:
    plan = budgeted(("/games?team=a*/b", "/x/*y"))

    sql = pushdown_sql(games_table(), plan, (), DEFAULTS, catalog_hash=HASH)

    first_line = sql.split("\n", 1)[0]
    assert first_line.index("*/") == len(first_line) - 2
    assert manifest_of(sql)["slice_keys"] == ["/games?team=a*/b", "/x/*y"]


# --- the fetch UDF call matches the fetch entry's input contract ---

_ARGUMENT = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"\.\"(?:[^\"]|\"\")*\"|[0-9]+")


def udf_arguments(sql: str, schema: str = "CFB_INFRA") -> list[str]:
    start = sql.index(f'"{schema}"."CFBD_FETCH"(') + len(f'"{schema}"."CFBD_FETCH"(')
    end = sql.index(") EMITS (", start)
    return _ARGUMENT.findall(sql[start:end])


def decode(token: str) -> object:
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    if token.startswith('"'):
        return token
    return int(token)


@pytest.mark.parametrize(
    "props",
    [
        DEFAULTS,
        Properties(
            connection_name="CFBD_API_MOCK",
            fetch_mode="replay",
            replay_path="/buckets/bfsdefault/default/test/replay",
            max_parallel_fetches=16,
            doh_url="off",
        ),
    ],
)
def test_udf_arguments_follow_the_fetch_entry_input_order(props: Properties) -> None:
    sql = pushdown_sql(games_table(), budgeted(), (), props, catalog_hash=HASH)
    tokens = udf_arguments(sql)

    assert len(tokens) == 8
    assert tokens[1] == '"R"."SLICE_KEY"'
    names = (
        "TABLE_NAME",
        "SLICE_KEY",
        "CATALOG_HASH",
        "FETCH_MODE",
        "CONNECTION_NAME",
        "REPLAY_PATH",
        "DOH_URL",
        "MAX_PARALLEL_FETCHES",
    )
    values = dict(zip(names, (decode(token) for token in tokens), strict=True))
    values["SLICE_KEY"] = SLICES[0]
    request = fetch._read_request(SimpleNamespace(**values))

    assert (request.table_name, request.slice_key, request.catalog_hash) == (
        "GAMES",
        SLICES[0],
        HASH,
    )
    assert (request.mode, request.connection_name, request.replay_path) == (
        props.fetch_mode,
        props.connection_name,
        props.replay_path,
    )
    assert (request.doh_url, request.parallel) == (props.doh_url, props.max_parallel_fetches)


def test_hostile_property_text_stays_one_string_literal() -> None:
    props = Properties(replay_path="/x'); DROP TABLE T; --")

    sql = pushdown_sql(games_table(), budgeted(), (), props, catalog_hash=HASH)

    assert "'/x''); DROP TABLE T; --'" in sql
    assert decode(udf_arguments(sql)[5]) == "/x'); DROP TABLE T; --"


# --- shapes chosen by read-through and the REQ_SOURCE residual ---


def test_live_only_for_read_through_off() -> None:
    props = replace(DEFAULTS, read_through=False)

    sql = pushdown_sql(games_table(), budgeted(), (HOME_OVER_50,), props, catalog_hash=HASH)

    assert "LOAD_LOG" not in sql
    assert "CFB_CORE" not in sql
    assert "NOT EXISTS" not in sql
    assert "UNION ALL" not in sql
    assert sql.count('"CFBD_FETCH"(') == 1
    assert '\n) WHERE "REQ_SLICE" IS NOT NULL\n' in sql
    assert sql.endswith(") WHERE " + render_conjunction((HOME_OVER_50,)))
    assert manifest_of(sql)["read_through"] is False


def test_volatile_tables_are_always_fetched_even_with_read_through_on() -> None:
    table = replace(games_table(), read_through=False)

    sql = pushdown_sql(table, budgeted(), (), DEFAULTS, catalog_hash=HASH)

    assert "LOAD_LOG" not in sql
    assert "CFB_CORE" not in sql
    assert manifest_of(sql)["read_through"] is False
    assert manifest_of(sql)["sources"] == ["LIVE"]


def test_replay_mode_names_replay_as_the_fetch_source() -> None:
    props = replace(DEFAULTS, fetch_mode="replay")

    sql = pushdown_sql(games_table(), budgeted(), (), props, catalog_hash=HASH)

    assert manifest_of(sql)["fetch_mode"] == "replay"
    assert manifest_of(sql)["sources"] == ["STORED", "REPLAY"]
    assert decode(udf_arguments(sql)[3]) == "replay"


def test_stored_only_query_has_no_fetch_and_skips_the_budget() -> None:
    residual = (Comparison("=", SOURCE, text("STORED")), HOME_OVER_50)

    sql = pushdown_sql(games_table(), budgeted(budget=None), residual, DEFAULTS, catalog_hash=HASH)

    assert "CFBD_FETCH" not in sql
    assert "VALUES" not in sql
    assert "NOT EXISTS" not in sql
    assert sql.count('FROM "CFB_CORE"."GAMES"') == 1
    assert sql.endswith(") WHERE " + render_conjunction(residual))
    assert manifest_of(sql)["budget"] is None
    assert manifest_of(sql)["sources"] == ["STORED"]


def test_stored_only_without_read_through_returns_no_rows_and_calls_nothing() -> None:
    residual = (Comparison("=", SOURCE, text("STORED")),)
    props = replace(DEFAULTS, read_through=False)

    sql = pushdown_sql(games_table(), budgeted(budget=None), residual, props, catalog_hash=HASH)

    assert body_of(sql) == zero_row_select(games_table())
    assert manifest_of(sql)["sources"] == []
    assert manifest_of(sql)["slice_keys"] == list(SLICES)


def test_a_fetch_branch_requires_a_budget_checked_plan() -> None:
    with pytest.raises(ValueError, match="budget"):
        pushdown_sql(games_table(), budgeted(budget=None), (), DEFAULTS, catalog_hash=HASH)


def test_plan_of_another_table_is_refused() -> None:
    plan = RequestPlan(table="TEAMS", slice_keys=("/teams",), budget=10)

    with pytest.raises(ValueError, match="TEAMS"):
        pushdown_sql(games_table(), plan, (), DEFAULTS, catalog_hash=HASH)


def test_schema_names_come_from_the_properties() -> None:
    sql = pushdown_sql(games_table(), budgeted(), (), TEST_NAMESPACE, catalog_hash=HASH)

    assert '"TEST_CFB_INFRA"."CFBD_FETCH"(' in sql
    assert sql.count('"TEST_CFB_META"."LOAD_LOG"') == 2
    assert '"TEST_CFB_CORE"."GAMES"' in sql
    for default in ('"CFB_INFRA"', '"CFB_META"', '"CFB_CORE"'):
        assert default not in sql


def test_slice_keys_longer_than_req_slice_are_refused_before_any_sql() -> None:
    long_key = "/games?team=" + "%C3%A4" * 400

    with pytest.raises(SliceKeyError) as caught:
        pushdown_sql(games_table(), budgeted((long_key,)), (), DEFAULTS, catalog_hash=HASH)

    assert isinstance(caught.value, PushdownError)
    message = str(caught.value)
    assert message.startswith("CFBD_VS.GAMES: ")
    assert str(MAX_SLICE_KEY_LENGTH) in message
    assert len(message) < 400


def test_a_slice_key_of_exactly_the_limit_is_accepted() -> None:
    key = "/games?team=" + "a" * (MAX_SLICE_KEY_LENGTH - len("/games?team="))

    sql = pushdown_sql(games_table(), budgeted((key,)), (), DEFAULTS, catalog_hash=HASH)

    assert f"'{key}'" in sql


def test_max_slice_key_length_matches_req_slice_type() -> None:
    assert META_COLUMNS["REQ_SLICE"] == f"VARCHAR({MAX_SLICE_KEY_LENGTH}) UTF8"


def test_tables_without_request_metadata_columns_are_refused() -> None:
    table = replace(
        games_table(), columns=tuple(c for c in games_table().columns if c.name != "REQ_MS")
    )

    with pytest.raises(RenderError, match="REQ_MS"):
        pushdown_sql(table, budgeted(), (), DEFAULTS, catalog_hash=HASH)


def test_contradiction_sql_reads_nothing() -> None:
    sql = contradiction_sql(games_table(), TEST_NAMESPACE)

    assert body_of(sql) == zero_row_select(games_table())
    assert "CFBD_FETCH" not in sql
    assert "LOAD_LOG" not in sql
    assert manifest_of(sql)["planned_requests"] == 0


# --- stored-only pruning: the residual must be provably never TRUE for the fetch source ---


@pytest.mark.parametrize(
    "residual",
    [
        (Comparison("=", SOURCE, text("STORED")),),
        (Comparison("=", text("STORED"), SOURCE),),
        (InList(SOURCE, (text("STORED"), text("REPLAY"))),),
        (Comparison("<>", SOURCE, text("LIVE")),),
        (Not(Comparison("=", SOURCE, text("LIVE"))),),
        (IsNull(SOURCE),),
        (Not(IsNotNull(SOURCE)),),
        (Comparison("=", SOURCE, Literal("null", None)),),
        (Comparison("=", SOURCE, text("")),),
        (Comparison("=", SOURCE, text("live")),),
        (HOME_OVER_50, Comparison("=", SOURCE, text("STORED"))),
        (
            Or(
                (
                    And((Comparison("=", SOURCE, text("STORED")), HOME_OVER_50)),
                    Comparison("=", SOURCE, text("REPLAY")),
                )
            ),
        ),
        (InList(SOURCE, (text("STORED"), Literal("null", None))),),
        (Literal("bool", False),),
        (Literal("null", None),),
    ],
)
def test_residuals_that_exclude_live_rows_prune_the_fetch(residual: tuple[Expr, ...]) -> None:
    assert excludes_source(residual, "LIVE") is True
    assert row_sources(games_table(), DEFAULTS, residual) == ("STORED",)


@pytest.mark.parametrize(
    "residual",
    [
        (),
        (HOME_OVER_50,),
        (Comparison("=", SOURCE, text("LIVE")),),
        (InList(SOURCE, (text("STORED"), text("LIVE"))),),
        (Comparison("<>", SOURCE, text("STORED")),),
        (IsNotNull(SOURCE),),
        (Like(SOURCE, text("S%"), None),),
        (Comparison("<", SOURCE, text("M")),),
        (Between(SOURCE, text("A"), text("Z")),),
        (Or((Comparison("=", SOURCE, text("STORED")), HOME_OVER_50)),),
        (Comparison("=", SOURCE, col("VENUE")),),
        (Comparison("=", SOURCE, num(5)),),
        (Comparison("=", SOURCE, text("LIVE ")),),
        (Not(Comparison("=", SOURCE, text("STORED"))),),
        (Not(And((Comparison("=", SOURCE, text("LIVE")), HOME_OVER_50))),),
        (ColumnRef("NEUTRAL_SITE", "value"),),
    ],
)
def test_residuals_that_may_keep_live_rows_keep_the_fetch(residual: tuple[Expr, ...]) -> None:
    assert excludes_source(residual, "LIVE") is False
    assert row_sources(games_table(), DEFAULTS, residual) == ("STORED", "LIVE")


def test_pruning_follows_the_fetch_mode() -> None:
    live_only = (Comparison("=", SOURCE, text("LIVE")),)
    replay = replace(DEFAULTS, fetch_mode="replay")

    assert row_sources(games_table(), DEFAULTS, live_only) == ("STORED", "LIVE")
    assert row_sources(games_table(), replay, live_only) == ("STORED",)


def test_sources_without_read_through() -> None:
    stored_only = (Comparison("=", SOURCE, text("STORED")),)
    off = replace(DEFAULTS, read_through=False)
    volatile = replace(games_table(), read_through=False)

    assert row_sources(games_table(), off, ()) == ("LIVE",)
    assert row_sources(volatile, DEFAULTS, ()) == ("LIVE",)
    assert row_sources(volatile, DEFAULTS, stored_only) == ()


# --- helpers for the select-list assertions ---


def split_top_level(text_: str) -> list[str]:
    items, depth, quote, current = [], 0, "", []
    for char in text_:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    items.append("".join(current).strip())
    return items


def stored_select_items(stored: str) -> list[str]:
    lines = stored.split("\n")
    assert lines[0] == "SELECT * FROM ("
    assert lines[1].startswith("SELECT ")
    return split_top_level(lines[1].removeprefix("SELECT "))


def output_name(item: str) -> str:
    match = re.search(r'"([^"]+)"$', item)
    assert match is not None, item
    return match.group(1)
