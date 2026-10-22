"""Read-through to stored slices (`cfbd-vs/read-through`): one test per scenario, plus edge cases.

How "the fetch UDF is not invoked for a stored slice" is proven, two ways at once:

- Nothing could answer it: the recordings written for staging are deleted (and the test waits
  until a UDF no longer sees them), and the mock is scripted to answer the slice with 404, so a
  fetch would fail the query loudly in replay mode and in live mode.
- Nothing asked: the mock's request log (one admin call instead of one per count) holds no
  request for the slice. The mock serves the request target verbatim, so the log's targets are
  slice keys.

Stored rows are marked in `CFB_CORE` after staging (`NOTES`, and `HOME_POINTS + 100` for the
aggregates), so a stored row can be told apart from a fetched one without trusting `REQ_SOURCE`.
Every identifier in the SQL here is quoted; the slice keys are the spec's `/games` keys.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

import pyexasol
import pytest

from tests.it.harness import OWNER, READER, READER_USER, SYS, Harness, Mock
from tests.it.support import split_slice_key, where_clause
from tests.synthetic import fake
from vs.cfbd_vs import flatten
from vs.cfbd_vs.catalog import Table
from vs.cfbd_vs.pushdown_sql import FETCH_UDF, LOAD_LOG, SOURCE_STORED, STORED_OVERRIDES
from vs.cfbd_vs.sqlrender import column_sql_types, qualified_name, quote_identifier, string_literal

pytestmark = pytest.mark.it

GAMES = "GAMES"
SOURCE_LIVE = "LIVE"
SOURCE_REPLAY = "REPLAY"
MARKER = "stored copy (read-through test)"
POINTS_BONUS = 100
EMPTY_YEAR = 1871
OTHER_TABLE = "GAMES_TEAMS"
VOLATILE_QUERIED = ("GAMES_SCHEDULE", "INFO")
STORED_ONLY_RANGE = (1869, 2026)
COLUMN_METADATA = (
    "COLUMN_ORDINAL_POSITION",
    "COLUMN_NAME",
    "COLUMN_TYPE",
    "COLUMN_TYPE_ID",
    "COLUMN_MAXSIZE",
    "COLUMN_NUM_PREC",
    "COLUMN_NUM_SCALE",
)
CHARACTER_SETS = ("UTF8", "ASCII")

Row = dict[str, Any]


# --- names and SQL --------------------------------------------------------------------------


def games_key(year: int) -> str:
    """The canonical slice key of one `/games` season (explicit default `seasonType=both`)."""
    return f"/games?seasonType=both&year={year}"


def vs_table(harness: Harness, table: str) -> str:
    return qualified_name(harness.vs, table)


def core_table(harness: Harness, table: str) -> str:
    return qualified_name(harness.core, table)


def load_log(harness: Harness) -> str:
    return qualified_name(harness.meta, LOAD_LOG)


def games_query(harness: Harness, years: Sequence[int], columns: str = "*") -> str:
    condition = f"= {years[0]}" if len(years) == 1 else f"IN ({', '.join(str(y) for y in years)})"
    return f'SELECT {columns} FROM {vs_table(harness, GAMES)} WHERE "P_YEAR" {condition}'


def key_list(keys: Iterable[str]) -> str:
    return ", ".join(string_literal(key) for key in keys)


def minimal_query(harness: Harness, table: str) -> str:
    """`SELECT *` with the table's minimal rule-satisfying filter (no WHERE when it is empty)."""
    condition = where_clause(table, fake.minimal_filter(table))
    query = f"SELECT * FROM {vs_table(harness, table)}"
    return f"{query} WHERE {condition}" if condition else query


# --- database helpers that are not in the harness -------------------------------------------


def execute_dicts(harness: Harness, sql: str, *, user: str = OWNER) -> tuple[list[Row], Any]:
    """Rows keyed by column name and the result's column metadata, from one execution."""
    statement = harness.connection_of(user).execute(sql)
    columns = statement.columns()
    names = list(columns)
    return [dict(zip(names, row, strict=True)) for row in statement.fetchall()], columns


def core_rows(harness: Harness, table: str, keys: Iterable[str]) -> list[Row]:
    """The rows `CFB_CORE.<table>` holds for the given slices."""
    return harness.query_dicts(
        f'SELECT * FROM {core_table(harness, table)} WHERE "REQ_SLICE" IN ({key_list(keys)})'
    )


def mark_stored(harness: Harness, keys: Iterable[str], *, points_bonus: int = 0) -> int:
    """Mark the stored rows of `/games` slices; returns how many rows were marked."""
    statement = harness.owner.execute(
        f"UPDATE {core_table(harness, GAMES)} "
        f'SET "NOTES" = {string_literal(MARKER)}, '
        f'"HOME_POINTS" = "HOME_POINTS" + {points_bonus} '
        f'WHERE "REQ_SLICE" IN ({key_list(keys)})'
    )
    return int(statement.rowcount())


def unregister(harness: Harness, table: str, key: str) -> None:
    harness.query(
        f'DELETE FROM {load_log(harness)} WHERE "TABLE_NAME" = {string_literal(table)} '
        f'AND "SLICE_KEY" = {string_literal(key)}'
    )


def make_non_final(harness: Harness, table: str, key: str) -> None:
    harness.query(
        f'UPDATE {load_log(harness)} SET "IS_FINAL" = FALSE '
        f'WHERE "TABLE_NAME" = {string_literal(table)} AND "SLICE_KEY" = {string_literal(key)}'
    )


def clear_recordings(harness: Harness) -> None:
    """Delete every recording and wait until a UDF no longer sees any of them."""
    removed = harness.replay_area.clear()
    harness.wait_for_files(removed, present=False)


def forbid_fetch(mock: Mock, year: int) -> None:
    """Make the mock refuse a stored `/games` season, so an accidental fetch fails loudly."""
    mock.script(
        "/games",
        query={"year": str(year)},
        status=404,
        body={"message": f"read-through test: season {year} is stored and must not be requested"},
    )


def requested(mock: Mock) -> list[str]:
    """The request targets (= slice keys) the mock saw since the test began, sorted."""
    return sorted(str(entry["target"]) for entry in mock.log())


# --- row comparison --------------------------------------------------------------------------


def number(value: Any) -> Decimal | None:
    """A numeric result as `Decimal`, whatever Python type the driver used."""
    return None if value is None else Decimal(str(value))


def synthetic_rows(table: str, key: str) -> int:
    """How many rows a fetch of the slice returns: the mock and the recordings are synthetic."""
    path, query = split_slice_key(key)
    return len(flatten.flatten(fake.catalog().table(table), fake.body_for(path, query)))


def by_slice(rows: Iterable[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[row["REQ_SLICE"]].append(row)
    return dict(grouped)


def without_overrides(row: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Every column except the four the stored branch overrides, in column order."""
    return tuple((name, value) for name, value in row.items() if name not in STORED_OVERRIDES)


def canonical(rows: Iterable[Any]) -> list[Any]:
    return sorted(rows, key=repr)


def assert_served_from_storage(served: Sequence[Row], stored: Sequence[Row]) -> None:
    """`served` are exactly the `stored` rows, with the stored-row request metadata.

    Every row has a `REQ_SLICE`, so no all-NULL row of an empty SET group slipped through ([21]).
    """
    assert len(served) == len(stored)
    for row in served:
        assert row["REQ_SLICE"] is not None
        assert row["REQ_SOURCE"] == SOURCE_STORED
        assert row["REQ_STATUS"] is None
        assert row["REQ_MS"] is None
        assert number(row["REQ_ATTEMPTS"]) == 0
    assert canonical(without_overrides(r) for r in served) == canonical(
        without_overrides(r) for r in stored
    )


def assert_fetched(rows: Sequence[Row], table: str, key: str, source: str = SOURCE_LIVE) -> None:
    """`rows` are one complete fetch of the slice, and none of them is a marked stored row."""
    assert len(rows) == synthetic_rows(table, key)
    assert {row["REQ_SOURCE"] for row in rows} == {source}
    assert {row["REQ_SLICE"] for row in rows} == {key}
    assert all(row.get("NOTES") != MARKER for row in rows)


def assert_no_duplicates(rows: Sequence[Row]) -> None:
    identities = [(row["REQ_SLICE"], row["ROW_NO"]) for row in rows]
    assert len(identities) == len(set(identities))


# --- representative tables for INSERT ... SELECT * ------------------------------------------


def representative_tables() -> list[str]:
    """Non-gated read-through tables covering every table shape and every column type.

    GAMES first, then the first table (by name) of each shape, then a greedy cover of any column
    type not covered yet. Gated tables are left out: their access depends on `API_FEATURES`.
    """
    candidates = sorted(
        (t for t in fake.catalog().tables if t.read_through and t.gated_by is None),
        key=lambda t: t.name,
    )
    deepest_child = min(
        (t for t in candidates if t.kind == "child"),
        key=lambda t: (-len(t.array_path), t.name),
    )
    shapes: tuple[Callable[[Table], bool], ...] = (
        lambda t: t.hoisted,
        lambda t: any(column.name == "RAW_JSON" for column in t.columns),
        lambda t: any(param.location == "path" for param in t.params),
        lambda t: any(rule.type == "row_cap" for rule in t.rules),
        lambda t: not t.params,
    )
    picks: list[Table] = [fake.catalog().table(GAMES), deepest_child]
    for shape in shapes:
        picks.append(next(t for t in candidates if shape(t)))
    covered = {sql_type for table in picks for _, sql_type in column_sql_types(table)}
    for table in candidates:
        types = {sql_type for _, sql_type in column_sql_types(table)}
        if not types <= covered:
            picks.append(table)
            covered |= types
    return list(dict.fromkeys(table.name for table in picks))


# --- scenarios -------------------------------------------------------------------------------


def test_stored_slice_zero_calls(harness: Harness, mock: Mock) -> None:
    """Scenario: A stored slice costs zero calls.

    No recording and no mock response exists for the stored slice, in replay and in live mode;
    the query returns exactly the stored rows (no extra all-NULL row, [21]) and the mock sees no
    request.
    """
    key = games_key(2019)
    assert harness.stage_slices(GAMES, '"P_YEAR" = 2019') == [key]
    stored = core_rows(harness, GAMES, [key])
    assert stored
    clear_recordings(harness)
    assert not harness.replay_area.host_file(key).exists()
    forbid_fetch(mock, 2019)
    query = games_query(harness, [2019])

    manifest = harness.explain(query)
    assert manifest.slice_keys == (key,)
    assert manifest.sources == (SOURCE_STORED, SOURCE_LIVE)
    assert_served_from_storage(harness.query_dicts(query), stored)

    harness.use_replay()
    assert harness.explain(query).sources == (SOURCE_STORED, SOURCE_REPLAY)
    assert_served_from_storage(harness.query_dicts(query), stored)

    assert requested(mock) == []


def test_mixed_query_fetches_missing_only(harness: Harness, mock: Mock) -> None:
    """Scenario: A mixed query fetches only the missing slices.

    2019 is stored and final, 2020 is not registered: each slice comes from exactly one source,
    and exactly one request (for 2020) reaches the API.
    """
    stored_key, missing_key = games_key(2019), games_key(2020)
    harness.stage_slices(GAMES, '"P_YEAR" = 2019')
    assert mark_stored(harness, [stored_key]) > 0
    stored = core_rows(harness, GAMES, [stored_key])
    forbid_fetch(mock, 2019)
    query = games_query(harness, [2019, 2020])

    manifest = harness.explain(query)
    assert set(manifest.slice_keys) == {stored_key, missing_key}
    assert manifest.sources == (SOURCE_STORED, SOURCE_LIVE)
    rows = harness.query_dicts(query)

    grouped = by_slice(rows)
    assert set(grouped) == {stored_key, missing_key}
    assert_served_from_storage(grouped[stored_key], stored)
    assert_fetched(grouped[missing_key], GAMES, missing_key)
    assert_no_duplicates(rows)
    assert requested(mock) == [missing_key]


def test_only_final_registered_slices_served(harness: Harness, mock: Mock) -> None:
    """Scenario: Only final, registered slices are served from storage.

    `CFB_CORE.GAMES` holds rows for 2021 without a `LOAD_LOG` row and for 2022 with
    `IS_FINAL = FALSE`: both slices are fetched and none of their stored rows is returned.
    """
    unregistered, non_final = games_key(2021), games_key(2022)
    assert set(harness.stage_slices(GAMES, '"P_YEAR" IN (2021, 2022)')) == {
        unregistered,
        non_final,
    }
    marked = mark_stored(harness, [unregistered, non_final])
    assert marked > 0
    unregister(harness, GAMES, unregistered)
    make_non_final(harness, GAMES, non_final)
    assert harness.query(f'SELECT "SLICE_KEY", "IS_FINAL" FROM {load_log(harness)}') == [
        (non_final, False)
    ]

    rows = harness.query_dicts(games_query(harness, [2021, 2022]))

    grouped = by_slice(rows)
    assert set(grouped) == {unregistered, non_final}
    for key, slice_rows in grouped.items():
        assert_fetched(slice_rows, GAMES, key)
    assert_no_duplicates(rows)
    assert requested(mock) == sorted([unregistered, non_final])
    still_stored = harness.query(
        f"SELECT COUNT(*) FROM {core_table(harness, GAMES)} "
        f'WHERE "NOTES" = {string_literal(MARKER)}'
    )
    assert number(still_stored[0][0]) == marked


def test_slice_registered_for_another_table_is_fetched(harness: Harness, mock: Mock) -> None:
    """Scenario: Only final, registered slices are served from storage (edge case).

    A final `LOAD_LOG` row only counts for its own `TABLE_NAME`: the 2023 games rows are in
    `CFB_CORE.GAMES`, but the slice is registered for another table, so it is fetched exactly
    once. Without the table check in the stored branch the slice would come back twice; without
    it in the fetch guard it would not come back at all.
    """
    key = games_key(2023)
    harness.stage_slices(GAMES, '"P_YEAR" = 2023')
    assert mark_stored(harness, [key]) > 0
    harness.query(
        f'UPDATE {load_log(harness)} SET "TABLE_NAME" = {string_literal(OTHER_TABLE)} '
        f'WHERE "TABLE_NAME" = {string_literal(GAMES)} AND "SLICE_KEY" = {string_literal(key)}'
    )

    rows = harness.query_dicts(games_query(harness, [2023]))

    assert_fetched(rows, GAMES, key)
    assert_no_duplicates(rows)
    assert requested(mock) == [key]


def test_stored_empty_slice_zero_calls(harness: Harness, mock: Mock) -> None:
    """Scenario: A stored empty slice costs zero calls.

    The 1871 slice is final with `ROWS_LOADED = 0` and has no rows: the query returns zero rows,
    without the all-NULL row an empty SET group produces ([21]), in live and in replay mode, and
    also inside a mixed query; only the missing 2020 slice is requested.
    """
    key, missing_key = games_key(EMPTY_YEAR), games_key(2020)
    harness.register_slice(GAMES, key, 0)
    forbid_fetch(mock, EMPTY_YEAR)
    assert not harness.replay_area.host_file(key).exists()
    query = games_query(harness, [EMPTY_YEAR])

    assert harness.explain(query).sources == (SOURCE_STORED, SOURCE_LIVE)
    assert harness.query_dicts(query) == []

    mixed = harness.query_dicts(games_query(harness, [EMPTY_YEAR, 2020]))
    assert set(by_slice(mixed)) == {missing_key}
    assert_fetched(mixed, GAMES, missing_key)

    harness.use_replay()
    assert harness.query_dicts(query) == []

    assert requested(mock) == [missing_key]


def test_query_results_store_as_slices(harness: Harness, mock: Mock) -> None:
    """Scenario: Query results can be stored as slices directly.

    `INSERT INTO CFB_CORE.GAMES SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2020` needs no column
    list or casts; once its slices are registered, the same query returns the same rows in every
    response and parameter column, stored, without reaching the API again.
    """
    key = games_key(2020)
    games = core_table(harness, GAMES)
    assert number(harness.query(f"SELECT COUNT(*) FROM {games}")[0][0]) == 0

    inserted = harness.owner.execute(
        f'INSERT INTO {games} SELECT * FROM {vs_table(harness, GAMES)} WHERE "P_YEAR" = 2020'
    ).rowcount()
    assert inserted == synthetic_rows(GAMES, key)
    slices = harness.query(f'SELECT "REQ_SLICE", COUNT(*) FROM {games} GROUP BY "REQ_SLICE"')
    for slice_key, count in slices:
        harness.register_slice(GAMES, str(slice_key), int(count))
    assert [slice_key for slice_key, _ in slices] == [key]
    stored = core_rows(harness, GAMES, [key])
    assert {row["REQ_SOURCE"] for row in stored} == {SOURCE_LIVE}

    served = harness.query_dicts(games_query(harness, [2020]))

    assert_served_from_storage(served, stored)
    assert requested(mock) == [key]


def test_stored_and_live_columns_identical(harness: Harness) -> None:
    """Scenario: Stored and live columns are identical.

    `EXA_ALL_COLUMNS` of `CFBD_VS` and `CFB_CORE` agree for every read-through table (names,
    order, type, precision, scale, character set), the volatile tables have no mirror, and for a
    representative set of tables `INSERT INTO <mirror> SELECT * FROM <virtual table>` works and
    the stored rows come back unchanged, also next to fetched rows in one query.
    """
    catalog = fake.catalog()
    read_through = {t.name for t in catalog.tables if t.read_through}
    columns = column_metadata(harness)
    vs_columns, core_columns = columns[harness.vs], columns[harness.core]
    assert set(vs_columns) == {t.name for t in catalog.tables}
    assert set(core_columns) == read_through
    assert len(core_columns) == len(read_through)
    column_types = {
        str(column[2])
        for schema in (vs_columns, core_columns)
        for table in schema.values()
        for column in table
    }
    assert all(
        type_text.endswith(CHARACTER_SETS)
        for type_text in column_types
        if type_text.startswith("VARCHAR")
    ), "the character set must be part of COLUMN_TYPE, or this comparison would miss it"
    mismatches = [
        f"{name}: virtual {vs_columns[name]} != mirror {core_columns[name]}"
        for name in sorted(read_through)
        if vs_columns[name] != core_columns[name]
    ]
    assert mismatches == []

    check_insert_select_round_trip(harness, representative_tables())


def column_metadata(harness: Harness) -> dict[str, dict[str, list[tuple[Any, ...]]]]:
    """`EXA_ALL_COLUMNS` of the virtual schema and the mirror schema, by schema and table."""
    listed = ", ".join(quote_identifier(name) for name in COLUMN_METADATA)
    rows = harness.query(
        f'SELECT "COLUMN_SCHEMA", "COLUMN_TABLE", {listed} FROM "EXA_ALL_COLUMNS" '
        f'WHERE "COLUMN_SCHEMA" IN ({key_list([harness.vs, harness.core])}) '
        'ORDER BY "COLUMN_SCHEMA", "COLUMN_TABLE", "COLUMN_ORDINAL_POSITION"'
    )
    result: dict[str, dict[str, list[tuple[Any, ...]]]] = {harness.vs: {}, harness.core: {}}
    for schema, table, *metadata in rows:
        result[schema].setdefault(table, []).append(tuple(metadata))
    return result


def check_insert_select_round_trip(harness: Harness, tables: Sequence[str]) -> None:
    """Store each table's minimal slice with `INSERT ... SELECT *`, then read it through the VS.

    Replay mode over synthetic recordings throughout, so the mock is never involved. The GAMES
    slice is also read together with a fetched season: both come back unchanged.
    """
    games_year = int(fake.minimal_filter(GAMES)["year"])
    fetched_year = 2020 if games_year != 2020 else 2021
    queries = {table: minimal_query(harness, table) for table in tables}
    keys = {table: harness.explain(query).slice_keys for table, query in queries.items()}
    planned = [key for table in tables for key in keys[table]]
    harness.write_recordings(list(dict.fromkeys([*planned, games_key(fetched_year)])))
    harness.use_replay()
    harness.set_property("READ_THROUGH", "FALSE")
    fetched: dict[str, list[Row]] = {}
    for table, query in queries.items():
        fetched[table] = harness.query_dicts(query)
        assert fetched[table], f"{table}: the replay fetch returned no rows to compare"
        inserted = harness.owner.execute(
            f"INSERT INTO {core_table(harness, table)} {query}"
        ).rowcount()
        assert inserted == len(fetched[table]), table
        counts = {key: len(rows) for key, rows in by_slice(fetched[table]).items()}
        for key in keys[table]:
            harness.register_slice(table, key, counts.get(key, 0))
    fetched_games = harness.query_dicts(games_query(harness, [fetched_year]))
    harness.set_property("READ_THROUGH", "TRUE")

    for table, query in queries.items():
        served, served_types = execute_dicts(harness, query)
        _, mirror_types = execute_dicts(harness, f"SELECT * FROM {core_table(harness, table)}")
        assert list(served_types.items()) == list(mirror_types.items()), table
        assert_served_from_storage(served, fetched[table])

    mixed = by_slice(harness.query_dicts(games_query(harness, [games_year, fetched_year])))
    assert set(mixed) == {games_key(games_year), games_key(fetched_year)}
    assert_served_from_storage(mixed[games_key(games_year)], fetched[GAMES])
    assert canonical(mixed[games_key(fetched_year)]) == canonical(fetched_games)
    assert {row["REQ_SOURCE"] for row in fetched_games} == {SOURCE_REPLAY}


def test_reader_needs_no_storage_privileges(harness: Harness, mock: Mock) -> None:
    """Scenario: Readers need no privileges on the stored data.

    `CFB_TEST_READER` holds only `SELECT` on the virtual schema (checked as `sys`), gets the
    stored 2019 rows and the fetched 2020 rows in one query, and is denied direct `SELECT` on
    `CFB_CORE.GAMES` and `CFB_META.LOAD_LOG`.
    """
    assert_reader_privileges(harness)
    stored_key, missing_key = games_key(2019), games_key(2020)
    harness.stage_slices(GAMES, '"P_YEAR" = 2019')
    assert mark_stored(harness, [stored_key]) > 0
    stored = core_rows(harness, GAMES, [stored_key])

    rows = harness.query_dicts(games_query(harness, [2019, 2020]), user=READER)

    grouped = by_slice(rows)
    assert set(grouped) == {stored_key, missing_key}
    assert_served_from_storage(grouped[stored_key], stored)
    assert_fetched(grouped[missing_key], GAMES, missing_key)
    assert requested(mock) == [missing_key]
    for target, name in ((core_table(harness, GAMES), GAMES), (load_log(harness), LOAD_LOG)):
        with pytest.raises(pyexasol.ExaQueryError, match=f"insufficient privileges.*{name}"):
            harness.query(f"SELECT COUNT(*) FROM {target}", user=READER)


def assert_reader_privileges(harness: Harness) -> None:
    """The reader's grants: `SELECT` on the virtual schema and `CREATE SESSION`, nothing else."""
    reader = {"reader": READER_USER}
    objects = harness.query(
        'SELECT "OBJECT_NAME", "PRIVILEGE" FROM "EXA_DBA_OBJ_PRIVS" WHERE "GRANTEE" = {reader}',
        reader,
        user=SYS,
    )
    assert set(objects) == {(harness.vs, "SELECT")}
    system = harness.query(
        'SELECT "PRIVILEGE" FROM "EXA_DBA_SYS_PRIVS" WHERE "GRANTEE" = {reader}', reader, user=SYS
    )
    assert system == [("CREATE SESSION",)]
    connections = harness.query(
        'SELECT "GRANTED_CONNECTION" FROM "EXA_DBA_CONNECTION_PRIVS" WHERE "GRANTEE" = {reader}',
        reader,
        user=SYS,
    )
    assert connections == []
    restricted = harness.query(
        'SELECT COUNT(*) FROM "EXA_DBA_RESTRICTED_OBJ_PRIVS" WHERE "GRANTEE" = {reader}',
        reader,
        user=SYS,
    )
    assert number(restricted[0][0]) == 0
    roles = harness.query(
        'SELECT "GRANTED_ROLE" FROM "EXA_DBA_ROLE_PRIVS" WHERE "GRANTEE" = {reader}',
        reader,
        user=SYS,
    )
    assert {role for (role,) in roles} <= {"PUBLIC"}


def test_stored_only_queries_never_call(harness: Harness, mock: Mock) -> None:
    """Scenario: Stored-only queries never call the API.

    Over 1869..2026 (158 planned slices, far over the budget of 10), `REQ_SOURCE = 'STORED'`
    returns exactly the rows of the stored final slices in the range: not the non-final 2021, not
    the unregistered 2022, not the final 2027 outside the range. The plan reads only storage
    (`sources == ["STORED"]`, no fetch UDF in the SQL, no budget) and the mock sees nothing;
    without the source predicate the same range is rejected by the budget at zero calls.
    """
    low, high = STORED_ONLY_RANGE
    served_keys = [games_key(2019), games_key(2020)]
    staged = harness.stage_slices(GAMES, '"P_YEAR" IN (2019, 2020, 2021, 2022, 2027)')
    assert len(staged) == 5
    make_non_final(harness, GAMES, games_key(2021))
    unregister(harness, GAMES, games_key(2022))
    harness.register_slice(GAMES, games_key(EMPTY_YEAR), 0)
    stored = core_rows(harness, GAMES, served_keys)
    range_query = (
        f'SELECT * FROM {vs_table(harness, GAMES)} WHERE "P_YEAR" BETWEEN {low} AND {high}'
    )
    query = f"{range_query} AND \"REQ_SOURCE\" = '{SOURCE_STORED}'"

    manifest = harness.explain(query)
    assert manifest.sources == (SOURCE_STORED,)
    assert manifest.budget is None
    assert manifest.fetch_source is None
    assert manifest.planned_requests == high - low + 1
    sql = harness.pushdown_sql(query)
    assert quote_identifier(FETCH_UDF) not in sql
    assert quote_identifier(harness.core) in sql

    rows = harness.query_dicts(query)

    assert set(by_slice(rows)) == set(served_keys)
    assert_served_from_storage(rows, stored)
    assert requested(mock) == []
    with pytest.raises(pyexasol.ExaQueryError, match=f"{high - low + 1} requests are planned"):
        harness.explain(range_query)


def test_read_through_switch_off(harness: Harness, mock: Mock) -> None:
    """Scenario: Read-through can be switched off.

    With `READ_THROUGH = FALSE` the stored, final 2019 slice is fetched and no stored row comes
    back: the pushdown SQL reads neither `CFB_CORE` nor `LOAD_LOG`, and a stored-only filter
    reads nothing at all.
    """
    key = games_key(2019)
    harness.stage_slices(GAMES, '"P_YEAR" = 2019')
    assert mark_stored(harness, [key]) > 0
    harness.set_property("READ_THROUGH", "FALSE")
    query = games_query(harness, [2019])

    manifest = harness.explain(query)
    assert manifest.sources == (SOURCE_LIVE,)
    assert manifest.read_through is False
    sql = harness.pushdown_sql(query)
    assert quote_identifier(harness.core) not in sql
    assert quote_identifier(LOAD_LOG) not in sql
    stored_only = f"{query} AND \"REQ_SOURCE\" = '{SOURCE_STORED}'"
    assert harness.explain(stored_only).sources == ()
    assert harness.query_dicts(stored_only) == []

    rows = harness.query_dicts(query)

    assert_fetched(rows, GAMES, key)
    assert requested(mock) == [key]


def test_volatile_endpoints_always_fetched(harness: Harness, mock: Mock) -> None:
    """Scenario: Volatile endpoints are always fetched.

    No volatile table has a mirror; every non-gated volatile table plans only a fetch; and
    `GAMES_SCHEDULE` and `INFO` are fetched even with a (bogus) final `LOAD_LOG` row.
    """
    catalog = fake.catalog()
    volatile = sorted(t.name for t in catalog.tables if not t.read_through)
    assert set(VOLATILE_QUERIED) <= set(volatile)
    mirrors = harness.query(
        'SELECT "TABLE_NAME" FROM "EXA_ALL_TABLES" '
        f'WHERE "TABLE_SCHEMA" = {string_literal(harness.core)} '
        f'AND "TABLE_NAME" IN ({key_list(volatile)})'
    )
    assert mirrors == []

    for name in volatile:
        if catalog.table(name).gated_by is not None:
            continue
        query = minimal_query(harness, name)
        manifest = harness.explain(query)
        assert manifest.sources == (SOURCE_LIVE,), name
        assert manifest.read_through is False, name
        sql = harness.pushdown_sql(query)
        assert quote_identifier(harness.core) not in sql, name
        assert quote_identifier(LOAD_LOG) not in sql, name

    keys = {name: fake.slice_key_for(name) for name in VOLATILE_QUERIED}
    for name, key in keys.items():
        harness.register_slice(name, key, 1)
    for name, key in keys.items():
        assert_fetched(harness.query_dicts(minimal_query(harness, name)), name, key)
    assert requested(mock) == sorted(keys.values())


def test_aggregates_over_mixed_results(harness: Harness, mock: Mock) -> None:
    """Scenario: Aggregates over mixed results are correct.

    2019 is stored (its `HOME_POINTS` raised by 100, so a wrongly sourced row changes the sums)
    and 2020 is fetched: `COUNT(*)`, `SUM`, `GROUP BY P_YEAR`, `GROUP BY REQ_SOURCE` and
    `COUNT(DISTINCT ...)` equal the totals over the plain rows, and the session stays alive.
    """
    stored_key, missing_key = games_key(2019), games_key(2020)
    harness.stage_slices(GAMES, '"P_YEAR" = 2019')
    assert mark_stored(harness, [stored_key], points_bonus=POINTS_BONUS) > 0
    stored_sum = harness.query(
        f'SELECT SUM("HOME_POINTS") FROM {core_table(harness, GAMES)} '
        f'WHERE "REQ_SLICE" = {string_literal(stored_key)}'
    )[0][0]
    session = harness.owner.session_id()
    years = [2019, 2020]

    rows = harness.query_dicts(
        games_query(harness, years, '"P_YEAR", "REQ_SOURCE", "REQ_SLICE", "ID", "HOME_POINTS"')
    )
    sources = Counter(str(row["REQ_SOURCE"]) for row in rows)
    assert sources == {
        SOURCE_STORED: synthetic_rows(GAMES, stored_key),
        SOURCE_LIVE: synthetic_rows(GAMES, missing_key),
    }
    per_year: dict[int, list[Row]] = defaultdict(list)
    for row in rows:
        per_year[int(row["P_YEAR"])].append(row)
    assert total_points(per_year[2019]) == number(stored_sum)

    totals = harness.query(games_query(harness, years, 'COUNT(*), SUM("HOME_POINTS")'))
    assert [tuple(number(v) for v in row) for row in totals] == [
        (Decimal(len(rows)), total_points(rows))
    ]

    grouped = harness.query(
        games_query(harness, years, '"P_YEAR", COUNT(*), SUM("HOME_POINTS")')
        + ' GROUP BY "P_YEAR" ORDER BY "P_YEAR"'
    )
    assert [tuple(number(v) for v in row) for row in grouped] == [
        (Decimal(year), Decimal(len(year_rows)), total_points(year_rows))
        for year, year_rows in sorted(per_year.items())
    ]

    by_source = harness.query(
        games_query(harness, years, '"REQ_SOURCE", COUNT(*)')
        + ' GROUP BY "REQ_SOURCE" ORDER BY "REQ_SOURCE"'
    )
    assert [(source, number(count)) for source, count in by_source] == [
        (source, Decimal(count)) for source, count in sorted(sources.items())
    ]

    distinct = harness.query(
        games_query(
            harness,
            years,
            'COUNT(DISTINCT "ID"), COUNT(DISTINCT "REQ_SLICE"), COUNT(DISTINCT "REQ_SOURCE")',
        )
    )
    assert [tuple(number(v) for v in row) for row in distinct] == [
        (
            Decimal(len({r["ID"] for r in rows if r["ID"] is not None})),
            Decimal(len({r["REQ_SLICE"] for r in rows})),
            Decimal(len({r["REQ_SOURCE"] for r in rows})),
        )
    ]

    assert harness.owner.session_id() == session
    assert harness.query("SELECT 1 FROM DUAL") == [(1,)]
    assert requested(mock) == [missing_key] * 5


def total_points(rows: Iterable[Row]) -> Decimal | None:
    """`SUM(HOME_POINTS)` over plain rows: NULLs ignored, NULL when there is no value."""
    values = [Decimal(str(row["HOME_POINTS"])) for row in rows if row["HOME_POINTS"] is not None]
    return sum(values, Decimal(0)) if values else None
