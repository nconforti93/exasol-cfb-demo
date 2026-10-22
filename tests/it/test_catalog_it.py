"""Integration tests of cfbd-vs/endpoint-catalog: the deployed `TEST_CFBD_VS` against the catalog.

The metadata comes from the real adapter (`createVirtualSchema`), read back through
`EXA_ALL_TABLES` / `EXA_ALL_COLUMNS`. The expectations come from the pinned OpenAPI spec and the
committed catalog, not from the adapter.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pyexasol
import pytest

from tests.it.harness import Harness, Mock
from tests.it.support import where_clause
from tests.synthetic import fake
from vs import bundle
from vs.cfbd_vs.catalog import Catalog, Table
from vs.gen.schema_walk import Operation, read_operations

pytestmark = pytest.mark.it

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "vs" / "openapi" / "cfbd-openapi-5.33.0.json"
GET_OPERATIONS = 93
ALTERED_HASH = "f" * 64
MISMATCH_MESSAGE = "different catalog versions"
DECIMAL_18 = "DECIMAL(18,0)"
VARCHAR_2000 = "VARCHAR(2000) UTF8"
PATH_TABLE_NAMES = {
    "/games/{gameId}/preview": "GAMES_PREVIEW",
    "/metrics/wp/pregame": "METRICS_WP_PREGAME",
    "/player/search": "PLAYER_SEARCH",
    "/stats/season/advanced": "STATS_SEASON_ADVANCED",
}
GAMES_PARAMETER_COLUMNS = (
    "P_YEAR",
    "P_WEEK",
    "P_SEASON_TYPE",
    "P_CLASSIFICATION",
    "P_TEAM",
    "P_HOME",
    "P_AWAY",
    "P_CONFERENCE",
    "P_ID",
    "P_COMPETITION",
    "P_ROUND",
)
SPREAD_DESCRIPTION = "Home-relative spread; negative favors home."

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

ColumnKey = tuple[str, str]


def derived_table_name(path: str) -> str:
    """The table name the spec derives from a path, written out independently of the generator."""
    segments = [part for part in path.strip("/").split("/") if not part.startswith("{")]
    return "_".join(_CAMEL_BOUNDARY.sub("_", segment).upper() for segment in segments)


def normalized_type(text: str) -> str:
    """Upper case, single spaces, a plain `TIMESTAMP` for `TIMESTAMP(3)`."""
    return re.sub(r"^TIMESTAMP\(3\)", "TIMESTAMP", " ".join(text.upper().split()))


def squashed(text: str) -> str:
    return " ".join(text.split())


def spec_operations() -> dict[str, Operation]:
    document = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return {operation.path: operation for operation in read_operations(document)}


def database_tables(harness: Harness) -> dict[str, str]:
    """`{table name: table comment}` of the virtual schema."""
    rows = harness.query(
        "SELECT TABLE_NAME, TABLE_COMMENT FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = {vs}",
        {"vs": harness.vs},
    )
    return {str(name): str(comment or "") for name, comment in rows}


def database_columns(harness: Harness) -> dict[ColumnKey, tuple[int, str, str]]:
    """`{(table, column): (ordinal position, normalized type, comment)}` of the virtual schema."""
    rows = harness.query(
        "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_ORDINAL_POSITION, COLUMN_TYPE, COLUMN_COMMENT "
        "FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA = {vs}",
        {"vs": harness.vs},
    )
    return {
        (str(table), str(name)): (int(position), normalized_type(str(sql_type)), str(comment or ""))
        for table, name, position, sql_type, comment in rows
    }


def column_names(columns: dict[ColumnKey, Any], table: str) -> list[str]:
    ordered = sorted((value[0], name) for (owner, name), value in columns.items() if owner == table)
    return [name for _, name in ordered]


def reserved_keywords(harness: Harness) -> set[str]:
    rows = harness.query("SELECT KEYWORD FROM EXA_SQL_KEYWORDS WHERE RESERVED")
    return {str(row[0]).upper() for row in rows}


def calendar_query(harness: Harness) -> str:
    where = where_clause("CALENDAR", fake.minimal_filter("CALENDAR"))
    return f"SELECT * FROM {harness.vs}.CALENDAR WHERE {where}"


@contextmanager
def adapter_from_altered_catalog(harness: Harness) -> Iterator[None]:
    """Replace only the adapter script by one built from a catalog with another hash."""
    original_text = bundle.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8")
    altered = dataclasses.replace(Catalog.from_json(original_text), catalog_hash=ALTERED_HASH)
    infra = harness.infra
    original = bundle.adapter_script(bundle.build(original_text).adapter_body, infra)
    other = bundle.adapter_script(bundle.build(altered.to_json()).adapter_body, infra)
    harness.owner.execute(other)
    try:
        yield
    finally:
        harness.owner.execute(original)


def test_every_get_operation_is_a_table(harness: Harness) -> None:
    """Scenario endpoint-catalog: Every GET operation becomes a virtual table."""
    catalog = fake.catalog()
    operations = set(spec_operations())
    excluded = {entry.path for entry in catalog.excluded}
    endpoint_tables = [table for table in catalog.tables if table.kind == "endpoint"]
    tables = database_tables(harness)

    assert len(operations) == GET_OPERATIONS
    assert excluded <= operations
    assert all(entry.reason.strip() for entry in catalog.excluded)
    assert len(endpoint_tables) + len(excluded) == GET_OPERATIONS
    assert sorted(table.endpoint for table in endpoint_tables) == sorted(operations - excluded)
    assert set(tables) == {table.name for table in catalog.tables}
    assert {table.name for table in endpoint_tables} <= set(tables)


def test_table_names_derive_from_path(harness: Harness) -> None:
    """Scenario endpoint-catalog: Table names derive from the endpoint path."""
    catalog = fake.catalog()
    tables = database_tables(harness)

    assert set(PATH_TABLE_NAMES.values()) <= set(tables)
    for path, name in PATH_TABLE_NAMES.items():
        assert catalog.table(name).endpoint == path
    for table in catalog.tables:
        if table.kind == "endpoint":
            assert table.name == derived_table_name(table.endpoint)
            assert table.name in tables


def test_parameters_become_typed_p_columns(harness: Harness) -> None:
    """Scenario endpoint-catalog: Every API parameter becomes a typed parameter column."""
    catalog = fake.catalog()
    operations = spec_operations()
    columns = database_columns(harness)

    games = set(column_names(columns, "GAMES"))
    assert set(GAMES_PARAMETER_COLUMNS) <= games
    for name in ("P_YEAR", "P_WEEK", "P_ID"):
        assert columns[("GAMES", name)][1] == DECIMAL_18
    for name in ("P_SEASON_TYPE", "P_TEAM"):
        assert columns[("GAMES", name)][1] == VARCHAR_2000
    assert columns[("GAMES_PREVIEW", "P_GAME_ID")][1] == DECIMAL_18
    for table in catalog.tables:
        parameter_columns = [p.column for p in table.params]
        assert len(parameter_columns) == len(operations[table.endpoint].parameters), table.name
        for param in table.params:
            assert columns[(table.name, param.column)][1] == normalized_type(param.sql_type)


def test_column_comments_carry_docs_and_rules(harness: Harness) -> None:
    """Scenario endpoint-catalog: Columns carry the API documentation and the rules."""
    catalog = fake.catalog()
    operations = spec_operations()
    columns = database_columns(harness)

    for table in catalog.tables:
        described = {p.name: p.description for p in operations[table.endpoint].parameters}
        by_name = {param.name: param for param in table.params}
        for name, param in by_name.items():
            comment = squashed(columns[(table.name, param.column)][2])
            assert squashed(described[name]) in comment, (table.name, name)
            assert f"API {param.location} parameter {name}" in comment
            assert all(value in comment for value in param.enum or ())
            if table.gated_by is not None:
                assert table.gated_by in comment
        for rule in table.rules:
            for name in rule.params:
                assert rule.id in columns[(table.name, by_name[name].column)][2], (table.name, rule)
        for column in table.columns:
            if column.kind == "value":
                assert columns[(table.name, column.name)][2] == column.comment

    spread = columns[("GAMES_PREVIEW", "ANALYSIS_ODDS_DATA_SPREAD")][2]
    assert SPREAD_DESCRIPTION in SPEC_PATH.read_text(encoding="utf-8")
    assert SPREAD_DESCRIPTION in spread
    assert "API field analysis.odds.data.spread" in spread


def test_reserved_word_columns_get_trailing_underscore(harness: Harness) -> None:
    """Scenario endpoint-catalog: Reserved words never become column names."""
    columns = database_columns(harness)
    reserved = reserved_keywords(harness)

    assert ("TEAMS_MATCHUP_GAMES", "DATE_") in columns
    assert ("TEAMS_MATCHUP_GAMES", "DATE") not in columns
    assert "games.date" in columns[("TEAMS_MATCHUP_GAMES", "DATE_")][2]
    assert "DATE" in reserved
    clashes = sorted({f"{table}.{name}" for table, name in columns if name in reserved})
    assert clashes == []


def test_refresh_reproduces_metadata(harness: Harness) -> None:
    """Scenario endpoint-catalog: Refreshing the virtual schema reproduces the same tables."""
    tables_before = database_tables(harness)
    columns_before = database_columns(harness)

    harness.query(f"ALTER VIRTUAL SCHEMA {harness.vs} REFRESH")

    assert database_tables(harness) == tables_before
    assert database_columns(harness) == columns_before
    assert len(tables_before) == len(fake.catalog().tables)


def test_catalog_version_mismatch_refused(harness: Harness, mock: Mock) -> None:
    """Scenario endpoint-catalog: Adapter and fetch UDF from different catalog versions are refused.

    Only the adapter is replaced (by one built from a catalog with another hash), so the fetch
    UDF and its connection grant stay as they are; the original adapter is put back afterwards.
    """
    query = calendar_query(harness)
    with adapter_from_altered_catalog(harness):
        with pytest.raises(pyexasol.ExaError) as refused:
            harness.query(query)
        assert MISMATCH_MESSAGE in str(refused.value)
        assert "redeploy" in str(refused.value)
        assert harness.mock_hits() == 0

    harness.write_recordings([fake.slice_key_for("CALENDAR")])
    harness.use_replay()
    assert harness.query(query)


def test_every_table_has_catalog_columns_in_order(harness: Harness) -> None:
    """Scenario endpoint-catalog: Every GET operation becomes a table (columns in catalog order)."""
    catalog = fake.catalog()
    columns = database_columns(harness)

    def expected(table: Table) -> list[str]:
        return [column.name for column in table.columns]

    for table in catalog.tables:
        assert column_names(columns, table.name) == expected(table), table.name
