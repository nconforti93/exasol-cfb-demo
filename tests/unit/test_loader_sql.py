"""Meta DDL of the loader (`sql/meta/*.sql`) and the namespace placeholders `metaddl` renders."""

import re
from pathlib import Path

import pytest

from vs.host import metaddl
from vs.host.metaddl import MetaDdlError
from vs.host.namespace import Namespace

RESERVED_KEYWORDS = (
    Path(__file__).resolve().parents[2] / "vs" / "gen" / "exasol_reserved_keywords.txt"
)
LOADER_TABLES = {
    "load_plan": "LOAD_PLAN",
    "load_plan_gaps": "LOAD_PLAN_GAPS",
    "loader_config": "LOADER_CONFIG",
    "season_numbers": "SEASON_NUMBERS",
    "refresh_steps": "REFRESH_STEPS",
    "load_run": "LOAD_RUN",
    "refresh_log": "REFRESH_LOG",
    "load_failures": "LOAD_FAILURES",
    "model_check_log": "MODEL_CHECK_LOG",
}
COLUMN = re.compile(
    r"^\s+([A-Z][A-Z0-9_]*) ((?:DECIMAL\(\d+,\d+\)|DOUBLE|BOOLEAN|TIMESTAMP|DATE"
    r"|VARCHAR\(\d+\) UTF8))( IDENTITY)?( NOT NULL)?,?$"
)

Column = tuple[str, str, bool]


def columns(name: str) -> list[Column]:
    """Name, type (with IDENTITY noted) and nullability of every column line."""
    found: list[Column] = []
    for line in metaddl.render_meta_ddl(name, "CFB_META").splitlines():
        match = COLUMN.match(line)
        if match:
            type_text = match.group(2) + (" IDENTITY" if match.group(3) else "")
            found.append((match.group(1), type_text, match.group(4) is None))
    return found


def test_the_loader_tables_are_registered_in_creation_order() -> None:
    assert metaddl.loader_meta_ddl_names() == tuple(LOADER_TABLES)


def test_the_foundation_names_are_unchanged_so_the_vs_deploy_does_not_create_loader_tables() -> (
    None
):
    assert metaddl.meta_ddl_names() == ("load_log", "fetch_log")


@pytest.mark.parametrize(("name", "table"), LOADER_TABLES.items())
def test_each_table_renders_one_create_table_in_the_namespace_schema(name: str, table: str) -> None:
    real = metaddl.render_meta_ddl(name, "CFB_META")
    test = metaddl.render_meta_ddl(name, "TEST_CFB_META")
    assert real.startswith(f"CREATE TABLE CFB_META.{table} (\n")
    assert test.startswith(f"CREATE TABLE TEST_CFB_META.{table} (\n")
    for text in (real, test):
        assert text.count("CREATE TABLE") == 1
        assert "{" not in text and "--" not in text
        assert not text.rstrip().endswith(";")
        assert text.rstrip().endswith(")")


@pytest.mark.parametrize("name", LOADER_TABLES)
def test_every_column_sits_on_its_own_line_so_the_deploy_can_compare_it(name: str) -> None:
    ddl = metaddl.render_meta_ddl(name, "CFB_META")
    body = ddl.split("(\n", 1)[1].rsplit("\n)", 1)[0].splitlines()
    parsed = [line for line in body if not line.strip().startswith("CONSTRAINT ")]
    assert len(columns(name)) == len(parsed)


@pytest.mark.parametrize("name", LOADER_TABLES)
def test_no_loader_name_is_a_reserved_word_so_no_sql_needs_quoting(name: str) -> None:
    reserved = set(RESERVED_KEYWORDS.read_text().split())
    ddl = metaddl.render_meta_ddl(name, "CFB_META")
    names = {column for column, _, _ in columns(name)} | {LOADER_TABLES[name]}
    assert names.isdisjoint(reserved), names & reserved
    assert '"' not in ddl


def test_load_plan_has_the_contract_columns() -> None:
    assert columns("load_plan") == [
        ("ENTRY_ID", "DECIMAL(9,0)", False),
        ("TABLE_NAME", "VARCHAR(128) UTF8", False),
        ("PROFILE_NAME", "VARCHAR(16) UTF8", False),
        ("PROFILE_RANK", "DECIMAL(2,0)", False),
        ("PRIORITY", "DECIMAL(4,0)", False),
        ("SLICING", "VARCHAR(16) UTF8", False),
        ("REFRESH_CLASS", "VARCHAR(16) UTF8", False),
        ("SEASON_FROM", "DECIMAL(4,0)", True),
        ("SEASON_TO", "DECIMAL(4,0)", True),
        ("FIXED_FILTER", "VARCHAR(2000) UTF8", True),
        ("SLICE_TEMPLATE", "VARCHAR(2000) UTF8", False),
        ("GATED_BY", "VARCHAR(32) UTF8", True),
        ("PLAN_HASH", "VARCHAR(64) UTF8", False),
    ]
    assert "CONSTRAINT LOAD_PLAN_PK PRIMARY KEY (ENTRY_ID)" in metaddl.render_meta_ddl(
        "load_plan", "CFB_META"
    )


def test_gaps_config_numbers_and_steps_have_the_contract_columns() -> None:
    assert columns("load_plan_gaps") == [
        ("ENTRY_ID", "DECIMAL(9,0)", False),
        ("SEASON", "DECIMAL(4,0)", False),
    ]
    assert columns("loader_config") == [
        ("SETTING_NAME", "VARCHAR(64) UTF8", False),
        ("SETTING_VALUE", "VARCHAR(2000) UTF8", True),
    ]
    assert columns("season_numbers") == [("SEASON", "DECIMAL(4,0)", False)]
    assert columns("refresh_steps") == [
        ("STEP_NO", "DECIMAL(3,0)", False),
        ("STEP_NAME", "VARCHAR(64) UTF8", False),
        ("SCRIPT_NAME", "VARCHAR(256) UTF8", False),
    ]


def test_load_run_has_an_identity_run_id_and_the_run_columns() -> None:
    assert columns("load_run") == [
        ("RUN_ID", "DECIMAL(18,0) IDENTITY", False),
        ("RUN_KIND", "VARCHAR(16) UTF8", False),
        ("RUN_ARGS", "VARCHAR(200) UTF8", True),
        ("SESSION_ID", "DECIMAL(20,0)", False),
        ("STARTED_AT", "TIMESTAMP", False),
        ("ENDED_AT", "TIMESTAMP", True),
        ("RUN_STATUS", "VARCHAR(20) UTF8", False),
        ("SLICES", "DECIMAL(9,0)", True),
        ("ROWS_LOADED", "DECIMAL(18,0)", True),
        ("CALLS", "DECIMAL(9,0)", True),
        ("REMAINING_BEFORE", "DECIMAL(9,0)", True),
        ("REMAINING_AFTER", "DECIMAL(9,0)", True),
        ("DETAIL", "VARCHAR(2000) UTF8", True),
    ]


def test_refresh_log_and_failures_have_the_contract_columns() -> None:
    assert columns("refresh_log") == [
        ("RUN_ID", "DECIMAL(18,0)", False),
        ("STEP_NO", "DECIMAL(5,0)", False),
        ("STEP_NAME", "VARCHAR(160) UTF8", False),
        ("STEP_STATUS", "VARCHAR(10) UTF8", False),
        ("STARTED_AT", "TIMESTAMP", False),
        ("ENDED_AT", "TIMESTAMP", True),
        ("SLICES", "DECIMAL(9,0)", True),
        ("ROWS_LOADED", "DECIMAL(18,0)", True),
        ("CALLS", "DECIMAL(9,0)", True),
        ("DETAIL", "VARCHAR(2000) UTF8", True),
    ]
    assert columns("load_failures") == [
        ("TABLE_NAME", "VARCHAR(128) UTF8", False),
        ("SLICE_KEY", "VARCHAR(2000) UTF8", False),
        ("FAILURES", "DECIMAL(3,0)", False),
        ("LAST_ERROR", "VARCHAR(2000) UTF8", True),
        ("LAST_RUN_ID", "DECIMAL(18,0)", True),
        ("LAST_FAILED_AT", "TIMESTAMP", True),
    ]
    ddl = metaddl.render_meta_ddl("load_failures", "CFB_META")
    assert "CONSTRAINT LOAD_FAILURES_PK PRIMARY KEY (TABLE_NAME, SLICE_KEY)" in ddl


def test_model_check_log_has_the_contract_columns() -> None:
    assert columns("model_check_log") == [
        ("RUN_ID", "DECIMAL(18,0)", True),
        ("AS_OF_SEASON", "DECIMAL(4,0)", False),
        ("AS_OF_WEEK_SEQ", "DECIMAL(3,0)", False),
        ("CHECK_NAME", "VARCHAR(64) UTF8", False),
        ("SEVERITY", "VARCHAR(8) UTF8", False),
        ("CHECK_SEASON", "DECIMAL(4,0)", False),
        ("CHECK_WEEK_SEQ", "DECIMAL(3,0)", True),
        ("CHECK_STATUS", "VARCHAR(4) UTF8", False),
        ("OBSERVED", "DECIMAL(18,0)", False),
        ("DETAIL", "VARCHAR(2000) UTF8", True),
        ("CHECKED_AT", "TIMESTAMP", False),
    ]


def test_the_run_and_log_tables_match_the_foundation_log_conventions() -> None:
    log = {n: t for n, t, _ in columns("load_log")}
    failures = {n: t for n, t, _ in columns("load_failures")}
    assert failures["TABLE_NAME"] == log["TABLE_NAME"]
    assert failures["SLICE_KEY"] == log["SLICE_KEY"]


def test_an_unknown_name_is_still_rejected() -> None:
    with pytest.raises(MetaDdlError, match="unknown meta DDL"):
        metaddl.render_meta_ddl("nope", "CFB_META")


# --- the other placeholders --------------------------------------------------------------------

PLACEHOLDER_SQL = """\
-- a whole-line comment with {META_SCHEMA} and {UNKNOWN} stays out
CREATE OR REPLACE VIEW {META_SCHEMA}.V AS
SELECT * FROM {CORE_SCHEMA}.T, {STAGE_SCHEMA}.T, {INFRA_SCHEMA}.S, {VS_NAME}.INFO,
  {RESTORE_SCHEMA}.R, {MODEL_SCHEMA}.M;
"""


def test_every_namespace_placeholder_is_rendered_for_the_real_namespace() -> None:
    text = metaddl.render_namespace_sql(PLACEHOLDER_SQL, Namespace(""))
    assert text == (
        "CREATE OR REPLACE VIEW CFB_META.V AS\n"
        "SELECT * FROM CFB_CORE.T, CFB_STAGE.T, CFB_INFRA.S, CFBD_VS.INFO,\n"
        "  CFB_RESTORE.R, CFB_MODEL.M\n"
    )


def test_every_namespace_placeholder_is_rendered_for_the_test_namespace() -> None:
    text = metaddl.render_namespace_sql(PLACEHOLDER_SQL, Namespace("TEST_"))
    assert "TEST_CFB_META.V" in text
    assert "TEST_CFB_CORE.T, TEST_CFB_STAGE.T, TEST_CFB_INFRA.S, TEST_CFBD_VS.INFO" in text
    assert "TEST_CFB_RESTORE.R" in text
    assert "TEST_CFB_MODEL.M" in text
    assert "{" not in text


def test_an_unknown_placeholder_is_an_error_that_names_it() -> None:
    with pytest.raises(MetaDdlError, match=r"\{MISSING_SCHEMA\}"):
        metaddl.render_namespace_sql("SELECT * FROM {MISSING_SCHEMA}.T", Namespace(""))


def test_lowercase_braces_are_left_alone_for_slice_templates() -> None:
    text = metaddl.render_namespace_sql(
        "SELECT '/games?year={season}' FROM {META_SCHEMA}.T", Namespace("")
    )
    assert "'/games?year={season}'" in text


def test_a_sql_file_is_read_and_rendered(tmp_path: Path) -> None:
    path = tmp_path / "view.sql"
    path.write_text("-- header\nCREATE VIEW {META_SCHEMA}.V AS SELECT 1;\n", encoding="utf-8")
    assert metaddl.render_sql_file(path, Namespace("TEST_")) == (
        "CREATE VIEW TEST_CFB_META.V AS SELECT 1\n"
    )
