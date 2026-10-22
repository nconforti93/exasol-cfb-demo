"""The loader views (`sql/loader/*.sql`): registry, rendering, contract columns and references.

The views' behaviour is tested on the engine (group E). These tests pin what can be checked
without a database: every file renders for the real and the `TEST_` namespace, every column a
view reads exists in its source, no identifier is a reserved word, and the clock and the API are
each reached through exactly one place.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from loader import plan as load_plan
from loader import templates
from loader.plan import PlanConfig
from vs.host import metaddl
from vs.host.metaddl import MetaDdlError
from vs.host.namespace import Namespace

ROOT = Path(__file__).resolve().parents[2]
RESERVED_KEYWORDS = frozenset(
    (ROOT / "vs" / "gen" / "exasol_reserved_keywords.txt").read_text().split()
)
UTC_NOW = "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')"
INFO_TABLE = '"{VS_NAME}"."INFO"'
VIEW_PROPERTIES = frozenset({"SCHEMA_NAME", "PROPERTY_NAME", "PROPERTY_VALUE"})

VIEW_COLUMNS: dict[str, tuple[str, ...]] = {
    "loader_settings": (
        "CLOCK_UTC",
        "NOW_UTC",
        "CURRENT_SEASON",
        "SEASON_ROLLOVER",
        "SEASON_FINAL_AFTER",
        "QUARANTINE_AFTER",
        "MONTHLY_CALL_BUDGET",
        "REFRESH_RESERVE",
        "REFRESH_PROFILES",
        "API_FEATURES",
    ),
    "load_slices": (
        "ENTRY_ID",
        "TABLE_NAME",
        "PROFILE_NAME",
        "PROFILE_RANK",
        "PRIORITY",
        "SLICING",
        "REFRESH_CLASS",
        "SEASON",
        "SLICE_KEY",
        "IS_GAP",
        "FINAL_IF_LOADED_NOW",
        "IS_STORED",
        "IS_FINAL",
        "ROWS_LOADED",
        "LOADED_AT",
        "LOADED_BY",
        "FAILURES",
        "LAST_ERROR",
        "LAST_FAILED_AT",
        "LAST_FAILED_RUN_ID",
        "IS_QUARANTINED",
        "IS_PENDING",
    ),
    "load_orphans": (
        "TABLE_NAME",
        "SLICE_KEY",
        "IS_FINAL",
        "ROWS_LOADED",
        "LOADED_AT",
        "LOADED_BY",
    ),
    "backfill_queue": (
        "QUEUE_ORDER",
        "ENTRY_ID",
        "TABLE_NAME",
        "PROFILE_NAME",
        "PROFILE_RANK",
        "PRIORITY",
        "SLICING",
        "REFRESH_CLASS",
        "SEASON",
        "SLICE_KEY",
        "SEASON_FROM",
        "SEASON_TO",
        "FIXED_FILTER",
        "FINAL_IF_LOADED_NOW",
        "IS_STORED",
        "FAILURES",
        "LAST_FAILED_RUN_ID",
        "BATCH_ALONE",
    ),
    "load_status": (
        "ENTRY_ID",
        "TABLE_NAME",
        "PROFILE_NAME",
        "PROFILE_RANK",
        "PRIORITY",
        "SLICING",
        "REFRESH_CLASS",
        "SEASON_FROM",
        "SEASON_TO",
        "GATED_BY",
        "SLICES_PLANNED",
        "GAP_SLICES",
        "SLICES_STORED",
        "SLICES_FINAL",
        "SLICES_MISSING",
        "QUARANTINED_SLICES",
        "ORPHAN_SLICES",
        "CALLS_TO_COMPLETE",
        "STORED_ROWS",
        "LAST_LOADED_AT",
        "BLOCKED_BY",
        "LAST_ERROR_SLICE",
        "LAST_ERROR",
    ),
    "quota": (
        "TIER_NAME",
        "MONTHLY_LIMIT",
        "USED_CALLS",
        "REMAINING_CALLS",
        "RESET_AT",
        "PERIOD_START",
        "MONTHLY_CALL_BUDGET",
        "REFRESH_RESERVE",
        "PROJECT_BUDGET",
        "REFRESH_SPENDABLE",
        "BACKFILL_SPENDABLE",
        "BACKFILL_CALLS",
        "REFRESH_CALLS",
        "OTHER_CALLS",
        "DEMO_CALLS_TO_COMPLETE",
        "FULL_CALLS_TO_COMPLETE",
    ),
}

DDL_COLUMN = re.compile(r"^\s+([A-Z][A-Z0-9_]*) [A-Z]", re.MULTILINE)
QUOTED = re.compile(r'"([^"]+)"')
OBJECT = re.compile(r'"(\{[A-Z_]+\}|SYS)"\."([A-Z][A-Z0-9_]*)"')
BOUND_OBJECT = re.compile(r'"(\{[A-Z_]+\}|SYS)"\."([A-Z][A-Z0-9_]*)" ([A-Z][A-Z0-9_]*)\b')
BOUND_CTE = re.compile(r"\b(?:FROM|JOIN) ([A-Z][A-Z0-9_]*) ([A-Z][A-Z0-9_]*)\b")
CTE_NAME = re.compile(r"\b([A-Z][A-Z0-9_]*) AS \($", re.MULTILINE)
COLUMN_REFERENCE = re.compile(r'\b([A-Z][A-Z0-9_]*)\."([A-Z][A-Z0-9_]*)"')
OUTPUT_ALIAS = re.compile(r'AS "([A-Z][A-Z0-9_]*)",?$')
STRING_LITERAL = re.compile(r"'([^']*)'")
OPEN_PLACEHOLDER = re.compile(r"\{[A-Z][A-Z_]*\}")


def source(name: str) -> str:
    return (metaddl.LOADER_VIEW_DIR / f"{name}.sql").read_text(encoding="utf-8")


def body(name: str) -> str:
    """The statement after `AS`, without the header comments and the CREATE line."""
    statement = re.sub(r"^--.*\n", "", source(name), flags=re.MULTILINE)
    return statement.split(" AS\n", 1)[1]


def output_columns(name: str) -> tuple[str, ...]:
    """The aliases of the outermost select list: the lines from the last column-0 SELECT to FROM."""
    lines = body(name).splitlines()
    start = max(i for i, line in enumerate(lines) if line == "SELECT")
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("FROM "))
    found = (OUTPUT_ALIAS.search(line) for line in lines[start + 1 : end])
    return tuple(match.group(1) for match in found if match)


def ddl_columns(name: str) -> frozenset[str]:
    text = metaddl.render_meta_ddl(name, "CFB_META")
    return frozenset(DDL_COLUMN.findall(text)) - {"CONSTRAINT"}


def object_columns() -> dict[tuple[str, str], frozenset[str]]:
    """Columns of every object a view may read, keyed by (schema placeholder, object)."""
    tables = metaddl.meta_ddl_names() + metaddl.loader_meta_ddl_names()
    known = {("{META_SCHEMA}", name.upper()): ddl_columns(name) for name in tables}
    info = load_plan.default_catalog().table("INFO")
    known[("{VS_NAME}", "INFO")] = frozenset(column.name for column in info.columns)
    known[("SYS", "EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES")] = VIEW_PROPERTIES
    for view, columns in VIEW_COLUMNS.items():
        known[("{META_SCHEMA}", view.upper())] = frozenset(columns)
    return known


def test_the_views_are_registered_in_creation_order() -> None:
    assert metaddl.loader_view_names() == tuple(VIEW_COLUMNS)


@pytest.mark.parametrize("name", VIEW_COLUMNS)
@pytest.mark.parametrize(("prefix", "schema"), [("", '"CFB_META"'), ("TEST_", '"TEST_CFB_META"')])
def test_each_view_renders_one_create_or_replace_view(name: str, prefix: str, schema: str) -> None:
    text = metaddl.render_loader_view(name, Namespace(prefix))
    assert text.startswith(f'CREATE OR REPLACE VIEW {schema}."{name.upper()}" AS\n')
    assert text.count("CREATE ") == 1
    assert not text.rstrip().endswith(";")
    assert "--" not in text
    assert not OPEN_PLACEHOLDER.search(text)


@pytest.mark.parametrize("name", VIEW_COLUMNS)
def test_the_test_namespace_reads_only_its_own_schemas(name: str) -> None:
    text = metaddl.render_loader_view(name, Namespace("TEST_"))
    schemas = {schema for schema, _ in re.findall(r'"([A-Z_]+)"\."([A-Z_]+)"', text)}
    assert schemas <= {"TEST_CFB_META", "TEST_CFBD_VS", "SYS"}
    assert "'TEST_CFBD_VS'" in text or name != "loader_settings"


def test_an_unknown_view_is_rejected() -> None:
    with pytest.raises(MetaDdlError, match="unknown loader view"):
        metaddl.render_loader_view("nope", Namespace(""))


@pytest.mark.parametrize(("name", "columns"), VIEW_COLUMNS.items())
def test_each_view_has_its_contract_columns(name: str, columns: tuple[str, ...]) -> None:
    assert output_columns(name) == columns


@pytest.mark.parametrize("name", VIEW_COLUMNS)
def test_a_view_reads_only_tables_and_views_created_before_it(name: str) -> None:
    order = metaddl.loader_view_names()
    earlier = {view.upper() for view in order[: order.index(name)]}
    tables = {t.upper() for t in metaddl.meta_ddl_names() + metaddl.loader_meta_ddl_names()}
    read = {obj for schema, obj in OBJECT.findall(body(name)) if schema == "{META_SCHEMA}"}
    assert read <= tables | earlier, read - tables - earlier


@pytest.mark.parametrize("name", VIEW_COLUMNS)
def test_every_column_a_view_reads_exists_in_its_source(name: str) -> None:
    text = body(name)
    known = object_columns()
    bound: dict[str, frozenset[str]] = {}
    for schema, obj, alias in BOUND_OBJECT.findall(text):
        columns = known.get((schema, obj))
        assert columns is not None, f"{name}: unknown object {schema}.{obj}"
        assert alias not in bound or bound[alias] == columns, f"{name}: alias {alias} reused"
        bound[alias] = columns
    derived = frozenset(OUTPUT_ALIAS.findall(text)) | frozenset(
        re.findall(r'AS "([A-Z][A-Z0-9_]*)"', text)
    )
    for alias, column in COLUMN_REFERENCE.findall(text):
        allowed = bound.get(alias, derived)
        assert column in allowed, f"{name}: {alias}.{column} is not a column of its source"


@pytest.mark.parametrize("name", VIEW_COLUMNS)
def test_every_derived_alias_is_bound_to_a_cte_of_the_same_view(name: str) -> None:
    text = body(name)
    ctes = set(CTE_NAME.findall(text))
    for source_name, alias in BOUND_CTE.findall(text):
        assert source_name in ctes, f"{name}: {alias} reads an unknown CTE {source_name}"


@pytest.mark.parametrize("name", VIEW_COLUMNS)
def test_no_identifier_or_alias_is_a_reserved_word(name: str) -> None:
    text = body(name)
    quoted = {word for word in QUOTED.findall(text) if not word.startswith("{")}
    aliases = {alias for _, _, alias in BOUND_OBJECT.findall(text)}
    aliases |= {alias for _, alias in BOUND_CTE.findall(text)} | set(CTE_NAME.findall(text))
    names = quoted | aliases | {name.upper()} | set(VIEW_COLUMNS[name])
    assert names.isdisjoint(RESERVED_KEYWORDS), names & RESERVED_KEYWORDS


def test_now_is_one_utc_expression_in_one_view() -> None:
    texts = {name: body(name) for name in VIEW_COLUMNS}
    assert [name for name, text in texts.items() if UTC_NOW in text] == ["loader_settings"]
    assert texts["loader_settings"].count(UTC_NOW) == 1
    for name, text in texts.items():
        without = text.replace(UTC_NOW, "")
        for clock in ("CURRENT_TIMESTAMP", "SYSTIMESTAMP", "SYSDATE", "CURRENT_DATE", "NOW()"):
            assert clock not in without, f"{name} reads the clock {clock} directly"


def test_only_the_quota_reads_the_virtual_schema_and_only_once() -> None:
    reads = {name: body(name).count('"{VS_NAME}".') for name in VIEW_COLUMNS}
    assert reads == {name: (1 if name == "quota" else 0) for name in VIEW_COLUMNS}
    assert body("quota").count(INFO_TABLE) == 1


def test_slice_keys_are_filled_from_the_template_placeholders() -> None:
    text = body("load_slices")
    for placeholder in (
        templates.SEASON_PLACEHOLDER,
        templates.SEASON_FROM_PLACEHOLDER,
        templates.SEASON_TO_PLACEHOLDER,
    ):
        assert f"'{placeholder}'" in text


def test_every_string_literal_is_a_known_plan_value() -> None:
    settings = set(PlanConfig(now_override="2020-01-01").settings())
    allowed = (
        set(load_plan.SLICINGS)
        | set(load_plan.REFRESH_CLASSES)
        | set(load_plan.PROFILES)
        | settings
        | {
            templates.SEASON_PLACEHOLDER,
            templates.SEASON_FROM_PLACEHOLDER,
            templates.SEASON_TO_PLACEHOLDER,
            "BACKFILL#",
            "REFRESH_WEEK#",
            "LOAD_PLAN_GAP",
            "LIVE",
            "ORPHANS",
            "API_FEATURES",
            "{VS_NAME}",
            "BACKFILL",
            "REFRESH_WEEK",
            "UTC",
            "MM-DD",
            "YYYY-MM-DD",
            "YYYY-MM-DD HH24:MI:SS",
            "T",
            " ",
            ",",
            "",
        }
    )
    literals = {literal for name in VIEW_COLUMNS for literal in STRING_LITERAL.findall(body(name))}
    assert literals <= allowed, literals - allowed
