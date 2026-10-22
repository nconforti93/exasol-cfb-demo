import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest

from vs.cfbd_vs.naming import META_COLUMNS
from vs.cfbd_vs.plan import RequestPlan, build_manifest, manifest_comment
from vs.host import fetchlog
from vs.host.fetchlog import FetchLogError, FetchLogRow, Manifest
from vs.host.metaddl import render_meta_ddl

LOGGED_AT = datetime(2026, 10, 21, 12, 0, 0)
FIRST_AT = datetime(2026, 10, 21, 11, 59, 58)
SLICES = (
    "/games?seasonType=both&week=8&year=2019",
    "/games?seasonType=both&week=9&year=2019",
    "/games?seasonType=both&week=8&year=2020",
)


def sources_for(mode: str) -> tuple[str, ...]:
    return ("STORED", "LIVE" if mode == "live" else "REPLAY")


def pushdown_sql(
    slices: tuple[str, ...] = SLICES,
    mode: str = "live",
    table: str = "GAMES",
    sources: tuple[str, ...] | None = None,
    budget: int | None = 10,
) -> str:
    plan = RequestPlan(table=table, slice_keys=slices, budget=budget)
    body = build_manifest(plan, mode=mode, read_through=True)
    body["sources"] = list(sources_for(mode) if sources is None else sources)
    return f"{manifest_comment(body)}\nSELECT * FROM (SELECT 1)"


def staged(slice_key: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "REQ_SLICE": slice_key,
        "REQ_SOURCE": "LIVE",
        "REQ_STATUS": 200,
        "REQ_ATTEMPTS": 1,
        "REQ_MS": 120,
        "REQ_ROWS": 42,
        "REQ_AT": FIRST_AT,
    }
    row.update(overrides)
    return row


def manifest(
    slices: tuple[str, ...] = SLICES,
    mode: str = "live",
    sources: tuple[str, ...] | None = None,
) -> Manifest:
    return fetchlog.parse_manifest(pushdown_sql(slices, mode, sources=sources))


def stored(slice_key: str, rows: int = 9) -> dict[str, Any]:
    return staged(
        slice_key,
        REQ_SOURCE="STORED",
        REQ_STATUS=None,
        REQ_ATTEMPTS=0,
        REQ_MS=None,
        REQ_ROWS=rows,
    )


# --- manifest parsing ---


def test_manifest_round_trips_from_the_planner_comment() -> None:
    parsed = manifest()
    assert parsed == Manifest(
        table="GAMES",
        fetch_mode="live",
        read_through=True,
        budget=10,
        planned_requests=3,
        slice_keys=SLICES,
        sources=("STORED", "LIVE"),
    )


def test_manifest_tolerates_leading_whitespace_and_a_null_budget() -> None:
    plan = RequestPlan(table="T", slice_keys=("/t",), budget=None)
    body = build_manifest(plan, mode="replay", read_through=False)
    body["sources"] = ["REPLAY"]
    sql = "\n  " + manifest_comment(body)
    parsed = fetchlog.parse_manifest(sql + "\nSELECT 1")
    assert parsed.budget is None
    assert parsed.fetch_mode == "replay"
    assert parsed.read_through is False


def test_manifest_restores_an_escaped_comment_terminator_in_a_slice_key() -> None:
    tricky = "/games?team=a*/b"
    sql = pushdown_sql((tricky,))
    assert "*\\/" in sql
    assert fetchlog.parse_manifest(sql).slice_keys == (tricky,)


def test_manifest_stops_at_the_first_comment_terminator() -> None:
    sql = pushdown_sql() + " /* another */ SELECT 2"
    assert fetchlog.parse_manifest(sql).planned_requests == 3


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1", "/* plain comment */ SELECT 1", "/*CFBD {not json}*/ SELECT 1", "/*CFBD {}", ""],
)
def test_sql_without_a_valid_manifest_is_rejected(sql: str) -> None:
    with pytest.raises(FetchLogError, match="manifest"):
        fetchlog.parse_manifest(sql)


def test_manifest_with_missing_keys_names_them() -> None:
    sql = '/*CFBD {"table":"T","slice_keys":[]}*/ SELECT 1'
    with pytest.raises(FetchLogError, match="fetch_mode"):
        fetchlog.parse_manifest(sql)


def test_manifest_without_sources_is_rejected_naming_the_key() -> None:
    body = json.dumps(
        {
            "table": "T",
            "fetch_mode": "live",
            "read_through": True,
            "budget": 1,
            "planned_requests": 1,
            "slice_keys": ["/t"],
        }
    )
    with pytest.raises(FetchLogError, match="sources"):
        fetchlog.parse_manifest(f"/*CFBD {body}*/ SELECT 1")


@pytest.mark.parametrize(
    ("sources", "budget"),
    [
        ([], 5),
        (["STORED"], None),
        (["LIVE"], 5),
        (["REPLAY"], 5),
        (["STORED", "LIVE"], 5),
        (["STORED", "REPLAY"], 5),
    ],
)
def test_manifest_accepts_every_sources_shape_the_planner_writes(
    sources: list[str], budget: int | None
) -> None:
    sql = pushdown_sql(sources=tuple(sources), budget=budget)
    parsed = fetchlog.parse_manifest(sql)
    assert parsed.sources == tuple(sources)
    assert parsed.budget == budget


@pytest.mark.parametrize(
    "sources",
    [["BOGUS"], ["LIVE", "REPLAY"], "STORED", [1], ["STORED", "STORED"], None],
)
def test_manifest_with_invalid_sources_is_rejected(sources: Any) -> None:
    body = {
        "table": "T",
        "fetch_mode": "live",
        "read_through": True,
        "budget": 1,
        "planned_requests": 1,
        "slice_keys": ["/t"],
        "sources": sources,
    }
    with pytest.raises(FetchLogError, match="sources"):
        fetchlog.parse_manifest(f"/*CFBD {json.dumps(body)}*/ SELECT 1")


def test_manifest_with_a_non_integer_budget_is_rejected() -> None:
    body = {
        "table": "T",
        "fetch_mode": "live",
        "read_through": True,
        "budget": "ten",
        "planned_requests": 1,
        "slice_keys": ["/t"],
        "sources": ["LIVE"],
    }
    with pytest.raises(FetchLogError, match="budget"):
        fetchlog.parse_manifest(f"/*CFBD {json.dumps(body)}*/ SELECT 1")


def test_manifest_whose_count_disagrees_with_its_slices_is_rejected() -> None:
    body = json.dumps(
        {
            "table": "T",
            "fetch_mode": "live",
            "read_through": True,
            "budget": 1,
            "planned_requests": 2,
            "slice_keys": ["/t"],
            "sources": ["LIVE"],
        }
    )
    with pytest.raises(FetchLogError, match="planned_requests"):
        fetchlog.parse_manifest(f"/*CFBD {body}*/ SELECT 1")


# --- building rows ---


def test_one_row_per_planned_slice_in_manifest_order() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(),
        [staged(SLICES[2]), staged(SLICES[0]), staged(SLICES[1])],
        logged_at=LOGGED_AT,
        context="loader run 7",
    )
    assert [row.slice_key for row in rows] == list(SLICES)
    assert {row.table_name for row in rows} == {"GAMES"}
    assert {row.context for row in rows} == {"loader run 7"}
    assert {row.logged_at for row in rows} == {LOGGED_AT}


def test_row_carries_the_metadata_of_its_slice() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(),
        [
            staged(SLICES[0], REQ_STATUS=200, REQ_ATTEMPTS=2, REQ_MS=950, REQ_ROWS=7),
            staged(SLICES[1]),
            staged(SLICES[2]),
        ],
        logged_at=LOGGED_AT,
    )
    first = rows[0]
    assert (first.source, first.http_status, first.attempts, first.ms) == ("LIVE", 200, 2, 950)
    assert (first.rows_returned, first.requested_at) == (7, FIRST_AT)


def test_repeated_identical_rows_of_a_slice_collapse_to_one_log_row() -> None:
    staged_rows = [staged(SLICES[0])] * 5 + [staged(SLICES[1]), staged(SLICES[2])]
    rows = fetchlog.build_fetch_log_rows(manifest(), staged_rows, logged_at=LOGGED_AT)
    assert len(rows) == 3
    assert rows[0].rows_returned == 42


def test_planned_slice_without_rows_is_logged_with_zero_rows_and_unknown_metadata() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(), [staged(SLICES[0]), staged(SLICES[2])], logged_at=LOGGED_AT
    )
    empty = rows[1]
    assert empty.slice_key == SLICES[1]
    assert empty.rows_returned == 0
    assert empty.source == "LIVE"
    assert (empty.http_status, empty.attempts, empty.ms, empty.requested_at) == (
        None,
        None,
        None,
        None,
    )


def test_zero_row_source_follows_the_fetch_mode() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(mode="replay"), [staged(SLICES[0], REQ_SOURCE="REPLAY")], logged_at=LOGGED_AT
    )
    assert [row.source for row in rows] == ["REPLAY", "REPLAY", "REPLAY"]


def test_no_staged_rows_at_all_logs_every_slice_with_zero_rows() -> None:
    rows = fetchlog.build_fetch_log_rows(manifest(), [], logged_at=LOGGED_AT)
    assert [row.rows_returned for row in rows] == [0, 0, 0]


def test_stored_slices_keep_their_stored_metadata() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(),
        [stored(SLICES[0]), staged(SLICES[1]), staged(SLICES[2])],
        logged_at=LOGGED_AT,
    )
    assert (rows[0].source, rows[0].http_status, rows[0].attempts, rows[0].ms) == (
        "STORED",
        None,
        0,
        None,
    )


def test_staged_rows_of_an_unplanned_slice_are_rejected() -> None:
    with pytest.raises(FetchLogError, match="/games\\?year=1999"):
        fetchlog.build_fetch_log_rows(manifest(), [staged("/games?year=1999")], logged_at=LOGGED_AT)


def test_conflicting_metadata_within_one_slice_is_rejected() -> None:
    with pytest.raises(FetchLogError, match="conflicting"):
        fetchlog.build_fetch_log_rows(
            manifest(), [staged(SLICES[0]), staged(SLICES[0], REQ_MS=5)], logged_at=LOGGED_AT
        )


def test_staged_row_without_a_row_count_is_rejected() -> None:
    with pytest.raises(FetchLogError, match="REQ_ROWS"):
        fetchlog.build_fetch_log_rows(
            manifest(), [staged(SLICES[0], REQ_ROWS=None)], logged_at=LOGGED_AT
        )


def test_staged_row_missing_a_request_column_is_rejected() -> None:
    broken = staged(SLICES[0])
    del broken["REQ_AT"]
    with pytest.raises(FetchLogError, match="REQ_AT"):
        fetchlog.build_fetch_log_rows(manifest(), [broken], logged_at=LOGGED_AT)


# --- the sources key: only slices that were actually read are logged ([21]) ---


def test_stored_only_query_logs_the_stored_slices_and_skips_the_ones_never_read() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(sources=("STORED",)),
        [stored(SLICES[0]), stored(SLICES[2], rows=5)],
        logged_at=LOGGED_AT,
    )
    assert [row.slice_key for row in rows] == [SLICES[0], SLICES[2]]
    assert [row.source for row in rows] == ["STORED", "STORED"]
    assert [row.rows_returned for row in rows] == [9, 5]


def test_stored_only_query_over_unstored_slices_logs_nothing() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(sources=("STORED",)), [], logged_at=LOGGED_AT, context="ignored"
    )
    assert rows == []


def test_stored_only_slice_without_rows_is_not_logged_as_fetched_with_zero_rows() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(sources=("STORED",)), [stored(SLICES[1])], logged_at=LOGGED_AT
    )
    assert [row.slice_key for row in rows] == [SLICES[1]]
    assert all(row.source != "LIVE" for row in rows)


def test_query_that_reads_no_source_logs_nothing() -> None:
    assert fetchlog.build_fetch_log_rows(manifest(sources=()), [], logged_at=LOGGED_AT) == []


def test_fetch_only_query_still_logs_every_planned_slice() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(sources=("LIVE",)), [staged(SLICES[0])], logged_at=LOGGED_AT
    )
    assert [row.slice_key for row in rows] == list(SLICES)
    assert [row.source for row in rows] == ["LIVE", "LIVE", "LIVE"]
    assert [row.rows_returned for row in rows] == [42, 0, 0]


def test_read_through_zero_row_slices_take_the_fetch_source_of_the_manifest() -> None:
    rows = fetchlog.build_fetch_log_rows(
        manifest(mode="replay"), [stored(SLICES[0])], logged_at=LOGGED_AT
    )
    assert [row.source for row in rows] == ["STORED", "REPLAY", "REPLAY"]


def test_staged_rows_from_a_source_the_manifest_does_not_read_are_rejected() -> None:
    with pytest.raises(FetchLogError, match="STORED"):
        fetchlog.build_fetch_log_rows(
            manifest(sources=("LIVE",)), [stored(SLICES[0])], logged_at=LOGGED_AT
        )
    with pytest.raises(FetchLogError, match="LIVE"):
        fetchlog.build_fetch_log_rows(
            manifest(sources=("STORED",)), [staged(SLICES[0])], logged_at=LOGGED_AT
        )


def test_too_long_context_is_rejected() -> None:
    with pytest.raises(FetchLogError, match="context"):
        fetchlog.build_fetch_log_rows(manifest(), [], logged_at=LOGGED_AT, context="x" * 201)


# --- contract with the DDL and the REQ_ columns ---


def test_request_columns_are_the_naming_meta_columns() -> None:
    assert tuple(META_COLUMNS) == fetchlog.REQUEST_COLUMNS


def test_row_tuple_order_matches_the_fetch_log_ddl() -> None:
    ddl = render_meta_ddl("fetch_log", "CFB_META")
    ddl_columns = [
        line.split()[0]
        for line in ddl.splitlines()
        if line.startswith("  ") and not line.strip().startswith("CONSTRAINT")
    ]
    assert list(fetchlog.FETCH_LOG_COLUMNS) == ddl_columns
    row = FetchLogRow(LOGGED_AT, "c", "T", "/t", "LIVE", 200, 1, 5, 3, FIRST_AT)
    assert row.as_tuple() == (LOGGED_AT, "c", "T", "/t", "LIVE", 200, 1, 5, 3, FIRST_AT)


# --- database seam ---


class FakeStatement:
    def __init__(self, rows: list[tuple[Any, ...]], columns: tuple[str, ...] = ()) -> None:
        self._rows = rows
        self._columns = columns

    def columns(self) -> dict[str, dict[str, Any]]:
        return {name: {} for name in self._columns}

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(
        self,
        answers: list[list[tuple[Any, ...]]] | None = None,
        columns: tuple[str, ...] = (),
    ) -> None:
        self.answers = answers or []
        self.columns = columns
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def execute(self, query: str, query_params: Mapping[str, Any] | None = None) -> FakeStatement:
        self.calls.append((query, query_params))
        return FakeStatement(self.answers.pop(0) if self.answers else [], self.columns)


def explain_row(sql: str, pushdown_id: int = 1, tables: str = "CFBD_VS.GAMES") -> tuple[Any, ...]:
    """A row of `EXPLAIN VIRTUAL`: PUSHDOWN_ID, PUSHDOWN_SQL, PUSHDOWN_JSON, INVOLVED_TABLES."""
    return (pushdown_id, sql, "{}", tables)


EXPLAIN_COLUMNS = ("PUSHDOWN_ID", "PUSHDOWN_SQL", "PUSHDOWN_JSON", "PUSHDOWN_INVOLVED_TABLES")


def metadata_tuple(slice_key: str, **overrides: Any) -> tuple[Any, ...]:
    row = staged(slice_key)
    row.update(
        {f"REQ_{name.upper()}": value for name, value in overrides.items()},
    )
    return tuple(row[column] for column in fetchlog.REQUEST_COLUMNS)


def test_read_slice_metadata_groups_staged_rows_in_sql() -> None:
    connection = FakeConnection([[metadata_tuple(SLICES[0])]])
    rows = fetchlog.read_slice_metadata(connection, ("CFB_STAGE", "GAMES_STAGED"))
    query, params = connection.calls[0]
    assert "GROUP BY" in query
    assert params == {"staged": ("CFB_STAGE", "GAMES_STAGED")}
    assert "{staged!i}" in query
    assert rows == [staged(SLICES[0])]


def test_explain_pushdown_sql_returns_the_single_pushdown() -> None:
    connection = FakeConnection([[explain_row(pushdown_sql())]], EXPLAIN_COLUMNS)
    sql = fetchlog.explain_pushdown_sql(
        connection, "SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2019"
    )
    assert connection.calls[0][0].startswith("EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES")
    assert fetchlog.parse_manifest(sql).table == "GAMES"


def test_explain_pushdown_sql_reads_the_column_by_name_not_the_last_one() -> None:
    connection = FakeConnection(
        [[(1, "{}", pushdown_sql(), "CFBD_VS.GAMES")]],
        ("PUSHDOWN_ID", "PUSHDOWN_JSON", "PUSHDOWN_SQL", "PUSHDOWN_INVOLVED_TABLES"),
    )
    sql = fetchlog.explain_pushdown_sql(connection, "SELECT 1")
    assert sql.startswith("/*CFBD ")


def test_explain_without_a_pushdown_sql_column_is_rejected() -> None:
    connection = FakeConnection([[(1, "x")]], ("PUSHDOWN_ID", "OTHER"))
    with pytest.raises(FetchLogError, match="PUSHDOWN_SQL"):
        fetchlog.explain_pushdown_sql(connection, "SELECT 1")


def test_explain_with_several_pushdowns_is_rejected() -> None:
    connection = FakeConnection(
        [[explain_row(pushdown_sql()), explain_row(pushdown_sql(), pushdown_id=2)]],
        EXPLAIN_COLUMNS,
    )
    with pytest.raises(FetchLogError, match="exactly one"):
        fetchlog.explain_pushdown_sql(connection, "SELECT 1")


def test_insert_writes_rows_in_ddl_column_order_without_quoting() -> None:
    connection = FakeConnection()
    rows = fetchlog.build_fetch_log_rows(manifest(), [staged(SLICES[0])], logged_at=LOGGED_AT)
    fetchlog.insert_fetch_log(connection, "CFB_META", rows)
    query, params = connection.calls[0]
    assert query.startswith("INSERT INTO {schema!i}.FETCH_LOG (LOGGED_AT, CONTEXT")
    assert "REQ_SOURCE" in query
    assert '"' not in query
    assert query.count("({") == 3
    assert params is not None
    assert params["schema"] == "CFB_META"
    assert SLICES[0] in params.values()
    assert "2026-10-21 12:00:00.000" in params.values()
    assert "{r0_http_status!d}" in query


def test_insert_batches_large_row_sets() -> None:
    connection = FakeConnection()
    many = tuple(f"/t?i={i}" for i in range(250))
    rows = fetchlog.build_fetch_log_rows(
        Manifest("T", "live", True, None, len(many), many, ("LIVE",)), [], logged_at=LOGGED_AT
    )
    fetchlog.insert_fetch_log(connection, "CFB_META", rows)
    assert len(connection.calls) == 3


def test_insert_of_nothing_issues_no_statement() -> None:
    connection = FakeConnection()
    fetchlog.insert_fetch_log(connection, "CFB_META", [])
    assert connection.calls == []


def test_capture_runs_read_build_insert_and_returns_the_rows() -> None:
    connection = FakeConnection([[metadata_tuple(SLICES[0]), metadata_tuple(SLICES[2])]])
    rows = fetchlog.capture_fetch_log(
        connection,
        staged_table=("CFB_STAGE", "GAMES_STAGED"),
        pushdown_sql=pushdown_sql(),
        meta_schema="CFB_META",
        context="demo",
        now=lambda: LOGGED_AT,
    )
    assert [row.rows_returned for row in rows] == [42, 0, 42]
    assert len(connection.calls) == 2
    assert connection.calls[1][0].startswith("INSERT INTO")


def test_capture_of_a_stored_only_query_inserts_only_the_stored_slices() -> None:
    connection = FakeConnection(
        [[metadata_tuple(SLICES[1], source="STORED", status=None, attempts=0, ms=None)]]
    )
    rows = fetchlog.capture_fetch_log(
        connection,
        staged_table=("CFB_STAGE", "GAMES_STAGED"),
        pushdown_sql=pushdown_sql(sources=("STORED",), budget=None),
        meta_schema="CFB_META",
        now=lambda: LOGGED_AT,
    )
    assert [(row.slice_key, row.source) for row in rows] == [(SLICES[1], "STORED")]
    assert len(connection.calls) == 2


def test_capture_of_a_query_that_read_nothing_issues_no_insert() -> None:
    connection = FakeConnection([[]])
    rows = fetchlog.capture_fetch_log(
        connection,
        staged_table=("CFB_STAGE", "GAMES_STAGED"),
        pushdown_sql=pushdown_sql(sources=("STORED",), budget=None),
        meta_schema="CFB_META",
        now=lambda: LOGGED_AT,
    )
    assert rows == []
    assert len(connection.calls) == 1
