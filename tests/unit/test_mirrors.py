from dataclasses import replace
from pathlib import Path

import pytest

from vs.cfbd_vs.catalog import Catalog, Column, Param, Table
from vs.cfbd_vs.naming import META_COLUMNS
from vs.cfbd_vs.sqlrender import column_sql_types
from vs.gen.mirrors import MirrorError, render_mirrors


def table(name: str, columns: tuple[Column, ...], read_through: bool = True) -> Table:
    return Table(
        name=name,
        endpoint="/x",
        kind="endpoint",
        parent=None,
        array_path=(),
        hoisted=False,
        read_through=read_through,
        gated_by=None,
        params=(),
        columns=columns,
        rules=(),
    )


ID = Column("ROW_NO", "position", sql_type="DECIMAL(18,0)")
NAME = Column("NAME", "value", ("name",), "VARCHAR(2000) UTF8")


def test_renders_one_create_table_per_read_through_table_in_name_order() -> None:
    sql = render_mirrors([table("B", (ID, NAME)), table("A", (ID,))], "abc123")

    assert sql.index("CREATE TABLE CFB_CORE.A (") < sql.index("CREATE TABLE CFB_CORE.B (")
    assert "  ROW_NO DECIMAL(18,0),\n  NAME VARCHAR(2000) UTF8\n);" in sql
    assert "abc123" in sql


def test_skips_tables_without_read_through() -> None:
    sql = render_mirrors([table("KEEP", (ID,)), table("VOLATILE", (ID,), read_through=False)], "h")

    assert "CFB_CORE.KEEP" in sql
    assert "VOLATILE" not in sql


def test_schema_name_is_a_parameter() -> None:
    sql = render_mirrors([table("A", (ID,))], "h", schema="TEST_CFB_CORE")

    assert "CREATE TABLE TEST_CFB_CORE.A (" in sql


def test_output_ends_with_a_newline_and_is_deterministic() -> None:
    tables = [table("A", (ID, NAME))]

    assert render_mirrors(tables, "h") == render_mirrors(tables, "h")
    assert render_mirrors(tables, "h").endswith(";\n")


@pytest.mark.parametrize("bad", ["lower", "A B", "A;DROP", "1A", ""])
def test_rejects_identifiers_that_would_need_quoting(bad: str) -> None:
    with pytest.raises(MirrorError, match="identifier"):
        render_mirrors([table(bad, (ID,))], "h")
    with pytest.raises(MirrorError, match="identifier"):
        render_mirrors([table("T", (Column(bad, "value", ("x",), "VARCHAR(2000) UTF8"),))], "h")


# --- mirror types are the virtual table's types by construction (read-through type identity) ---


def test_mirror_types_come_from_column_sql_types() -> None:
    year = Param("year", "P_YEAR", "query", "integer", "DECIMAL(18,0)", None)
    columns = (
        Column("ROW_NO", "position"),
        NAME,
        Column("P_YEAR", "param"),
        *(Column(meta, "meta") for meta in META_COLUMNS),
    )
    mirrored = replace(table("T", columns), params=(year,))

    sql = render_mirrors([mirrored], "h")

    expected = ",\n".join(f"  {name} {sql_type}" for name, sql_type in column_sql_types(mirrored))
    assert f"CREATE TABLE CFB_CORE.T (\n{expected}\n);" in sql
    assert "  ROW_NO DECIMAL(18,0)," in sql
    assert "  P_YEAR DECIMAL(18,0)," in sql
    assert "  REQ_SOURCE VARCHAR(8) UTF8," in sql


def test_catalog_types_that_contradict_the_virtual_table_are_refused() -> None:
    year = Param("year", "P_YEAR", "query", "integer", "DECIMAL(18,0)", None)
    wrong = replace(
        table("T", (Column("P_YEAR", "param", sql_type="VARCHAR(10) UTF8"),)), params=(year,)
    )

    with pytest.raises(MirrorError, match="P_YEAR"):
        render_mirrors([wrong], "h")


def test_value_columns_without_a_type_are_refused() -> None:
    with pytest.raises(MirrorError, match="NAME"):
        render_mirrors([table("T", (Column("NAME", "value", ("name",)),))], "h")


def test_committed_mirrors_match_the_virtual_table_types() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = Catalog.from_json((root / "vs" / "catalog" / "catalog.json").read_text("utf-8"))

    for mirrored in (t for t in catalog.tables if t.read_through):
        ddl = render_mirrors([mirrored], catalog.catalog_hash)
        expected = ",\n".join(f"  {n} {t}" for n, t in column_sql_types(mirrored))
        assert f"(\n{expected}\n);" in ddl
