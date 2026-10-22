import re
from pathlib import Path

import pytest

from vs.host.metaddl import (
    META_SCHEMA_PLACEHOLDER,
    MetaDdlError,
    meta_ddl_names,
    render_meta_ddl,
)

COLUMN_LINE = re.compile(r"^\s+([A-Z_]+) ([A-Z]+(?:\(\d+(?:,\d+)?\))?(?: UTF8)?)( NOT NULL)?,?$")


RESERVED_KEYWORDS = (
    Path(__file__).resolve().parents[2] / "vs" / "gen" / "exasol_reserved_keywords.txt"
)


def parse_columns(ddl: str) -> list[tuple[str, str, bool]]:
    """Column name, type and nullability of every column line of a CREATE TABLE."""
    columns = []
    for line in ddl.splitlines():
        match = COLUMN_LINE.match(line)
        if match:
            columns.append((match.group(1), match.group(2), match.group(3) is None))
    return columns


def test_load_log_has_the_contract_columns() -> None:
    columns = parse_columns(render_meta_ddl("load_log", "CFB_META"))
    assert columns == [
        ("TABLE_NAME", "VARCHAR(128) UTF8", False),
        ("SLICE_KEY", "VARCHAR(2000) UTF8", False),
        ("IS_FINAL", "BOOLEAN", False),
        ("ROWS_LOADED", "DECIMAL(18,0)", False),
        ("LOADED_AT", "TIMESTAMP", False),
        ("LOADED_BY", "VARCHAR(64) UTF8", False),
    ]


def test_load_log_primary_key_is_table_and_slice() -> None:
    ddl = render_meta_ddl("load_log", "CFB_META")
    assert "CONSTRAINT LOAD_LOG_PK PRIMARY KEY (TABLE_NAME, SLICE_KEY)" in ddl


def test_no_meta_column_is_a_reserved_word_so_no_sql_needs_quoting() -> None:
    reserved = set(RESERVED_KEYWORDS.read_text().split())
    for name in meta_ddl_names():
        ddl = render_meta_ddl(name, "CFB_META")
        names = {column for column, _, _ in parse_columns(ddl)}
        assert names
        assert names.isdisjoint(reserved), names & reserved
        assert '"' not in ddl


def test_fetch_log_has_the_contract_columns() -> None:
    columns = parse_columns(render_meta_ddl("fetch_log", "CFB_META"))
    assert columns == [
        ("LOGGED_AT", "TIMESTAMP", False),
        ("CONTEXT", "VARCHAR(200) UTF8", True),
        ("TABLE_NAME", "VARCHAR(128) UTF8", False),
        ("SLICE_KEY", "VARCHAR(2000) UTF8", False),
        ("REQ_SOURCE", "VARCHAR(8) UTF8", False),
        ("HTTP_STATUS", "DECIMAL(3,0)", True),
        ("ATTEMPTS", "DECIMAL(3,0)", True),
        ("MS", "DECIMAL(9,0)", True),
        ("ROWS_RETURNED", "DECIMAL(18,0)", False),
        ("REQUESTED_AT", "TIMESTAMP", True),
    ]


@pytest.mark.parametrize(("name", "table"), [("load_log", "LOAD_LOG"), ("fetch_log", "FETCH_LOG")])
def test_schema_placeholder_is_replaced_in_the_real_and_test_namespace(
    name: str, table: str
) -> None:
    real = render_meta_ddl(name, "CFB_META")
    test = render_meta_ddl(name, "TEST_CFB_META")
    assert f"CREATE TABLE CFB_META.{table} (" in real
    assert f"CREATE TABLE TEST_CFB_META.{table} (" in test
    assert META_SCHEMA_PLACEHOLDER not in real + test


def test_rendered_ddl_is_a_single_statement_without_trailing_semicolon() -> None:
    for name in meta_ddl_names():
        ddl = render_meta_ddl(name, "CFB_META")
        assert ddl.count("CREATE TABLE") == 1
        assert not ddl.rstrip().endswith(";")
        assert "--" not in ddl


def test_names_are_the_two_meta_tables() -> None:
    assert meta_ddl_names() == ("load_log", "fetch_log")


def test_unknown_ddl_name_is_rejected() -> None:
    with pytest.raises(MetaDdlError, match="unknown meta DDL"):
        render_meta_ddl("nope", "CFB_META")


@pytest.mark.parametrize("schema", ["", "cfb_meta", "CFB META", "CFB_META; DROP", '"X"', "1ABC"])
def test_unsafe_schema_names_are_rejected(schema: str) -> None:
    with pytest.raises(MetaDdlError, match="schema name"):
        render_meta_ddl("load_log", schema)
