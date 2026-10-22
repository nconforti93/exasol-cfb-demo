import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from vs import bundle
from vs.cfbd_vs.catalog import Catalog
from vs.host import deploy
from vs.host.deploy import DeployError, DeployOptions
from vs.host.namespace import Namespace

ROOT = Path(__file__).resolve().parents[2]
CATALOG_TEXT = (ROOT / "vs" / "catalog" / "catalog.json").read_text(encoding="utf-8")
CATALOG = Catalog.from_json(CATALOG_TEXT)
REAL = Namespace("")
TEST = Namespace("TEST_")
FAKE_BUNDLE = bundle.Bundle(adapter_body="adapter-body\n# v1", fetch_body="fetch-body\n# v1")
MIRRORS_SQL = (ROOT / "sql" / "core" / "cfb_core_mirrors.sql").read_text(encoding="utf-8")
FEATURE_COLUMNS = [
    "FEATURES_ADJUSTED_METRICS",
    "FEATURES_WEATHER",
    "FEATURES_SCOREBOARD",
    "FEATURES_LIVE_PLAY_BY_PLAY",
    "FEATURES_GRAPH_QL",
]


class Result:
    def __init__(self, rows: Sequence[tuple[Any, ...]] = ()) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeDatabase:
    """Just enough of Exasol for the statements the deployer sends."""

    def __init__(
        self,
        *,
        connections: Sequence[str] = (),
        info_flags: tuple[bool, ...] | Exception = (False, True, False, False, False),
    ) -> None:
        self.statements: list[str] = []
        self.tables: dict[tuple[str, str], list[tuple[str, str, bool]]] = {}
        self.scripts: dict[tuple[str, str], str] = {}
        self.virtual_schemas: dict[str, dict[str, str]] = {}
        self.connections = set(connections)
        self.info_flags = info_flags
        self.queries: list[str] = []

    def execute(self, query: str) -> Result:
        if query.lstrip().startswith("SELECT"):
            self.queries.append(query)
            return self._select(query)
        self.statements.append(query)
        self._apply(query)
        return Result()

    def _select(self, query: str) -> Result:
        if "FROM EXA_ALL_COLUMNS" in query:
            schema = _literal_after(query, "COLUMN_SCHEMA = ")
            rows = [
                (table, name, type_text, nullable)
                for (table_schema, table), columns in sorted(self.tables.items())
                if table_schema == schema
                for name, type_text, nullable in columns
            ]
            return Result(rows)
        if "FROM EXA_ALL_SCRIPTS" in query:
            key = (
                _literal_after(query, "SCRIPT_SCHEMA = "),
                _literal_after(query, "SCRIPT_NAME = "),
            )
            return Result([(self.scripts[key],)] if key in self.scripts else [])
        if "FROM EXA_ALL_VIRTUAL_SCHEMAS" in query:
            return Result([(int(_literal_after(query, "SCHEMA_NAME = ") in self.virtual_schemas),)])
        if "FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES" in query:
            props = self.virtual_schemas[_literal_after(query, "SCHEMA_NAME = ")]
            return Result(list(props.items()))
        if "FROM EXA_ALL_CONNECTIONS" in query:
            name = _literal_after(query, "CONNECTION_NAME = ")
            return Result([(int(name in self.connections),)])
        if '."INFO"' in query:
            if isinstance(self.info_flags, Exception):
                raise self.info_flags
            return Result([self.info_flags])
        raise AssertionError(f"unexpected query: {query[:120]}")

    def _apply(self, statement: str) -> None:
        if statement.startswith("CREATE TABLE"):
            table = deploy.parse_table_ddl(statement)
            self.tables[(table.schema, table.name)] = list(table.columns)
        elif "SCRIPT" in statement.split("\n", 1)[0] and statement.startswith("CREATE"):
            header, _, body = statement.partition(" AS\n")
            name = re.search(r"SCRIPT (\w+)\.(\w+)", header)
            assert name is not None
            self.scripts[(name.group(1), name.group(2))] = (
                "CREATE PYTHON3 SET SCRIPT normalized AS\n" + body.removesuffix("/")
            )
        elif statement.startswith("CREATE VIRTUAL SCHEMA"):
            vs = statement.split()[3]
            self.virtual_schemas[vs] = _assignments(statement.split(" WITH ", 1)[1])
        elif statement.startswith("ALTER VIRTUAL SCHEMA") and " SET " in statement:
            vs = statement.split()[3]
            self.virtual_schemas[vs].update(_assignments(statement.split(" SET ", 1)[1]))

    def starting_with(self, prefix: str) -> list[str]:
        return [s for s in self.statements if s.startswith(prefix)]


def _literal_after(query: str, marker: str) -> str:
    match = re.search(re.escape(marker) + r"'((?:[^']|'')*)'", query)
    assert match is not None, query
    return match.group(1).replace("''", "'")


def _assignments(text: str) -> dict[str, str]:
    return {
        name: value.replace("''", "'")
        for name, value in re.findall(r"(\w+) = '((?:[^']|'')*)'", text)
    }


def options(**overrides: Any) -> DeployOptions:
    values: dict[str, Any] = {
        "catalog_text": CATALOG_TEXT,
        "built": FAKE_BUNDLE,
        "mirrors_sql": MIRRORS_SQL,
    }
    values.update(overrides)
    return DeployOptions(**values)


def run(db: FakeDatabase, namespace: Namespace = REAL, **overrides: Any) -> deploy.DeployReport:
    return deploy.deploy(db, namespace, options(**overrides))


# --- DDL parsing and diffs ---


def test_table_ddl_parser_reads_types_nullability_and_skips_constraints() -> None:
    table = deploy.parse_table_ddl(
        "CREATE TABLE S.T (\n  A DECIMAL(18,0),\n  B VARCHAR(8) UTF8 NOT NULL,\n"
        "  CONSTRAINT PK PRIMARY KEY (A, B)\n);\n"
    )
    assert (table.schema, table.name) == ("S", "T")
    assert table.columns == (("A", "DECIMAL(18,0)", True), ("B", "VARCHAR(8) UTF8", False))
    assert table.statement.endswith(")")


def test_column_diff_names_missing_extra_and_different_columns() -> None:
    table = deploy.parse_table_ddl("CREATE TABLE S.T (\n  A DECIMAL(18,0),\n  B DOUBLE\n)")
    found = [("A", "VARCHAR(5) UTF8", True), ("C", "DOUBLE", True)]
    diff = deploy.column_diff(table, found)
    assert "S.T: column B is missing" in diff
    assert "S.T: unexpected column C" in diff
    assert "S.T.A: expected DECIMAL(18,0), found VARCHAR(5) UTF8" in diff


def test_column_diff_flags_nullability_and_order() -> None:
    table = deploy.parse_table_ddl("CREATE TABLE S.T (\n  A DOUBLE NOT NULL,\n  B DOUBLE\n)")
    assert deploy.column_diff(table, [("A", "DOUBLE", True), ("B", "DOUBLE", True)]) == [
        "S.T.A: expected DOUBLE NOT NULL, found DOUBLE"
    ]
    assert deploy.column_diff(table, [("B", "DOUBLE", True), ("A", "DOUBLE", False)]) == [
        "S.T: the columns are in a different order"
    ]
    assert deploy.column_diff(table, [("A", "DOUBLE", False), ("B", "DOUBLE", True)]) == []


def test_mirror_tables_substitute_the_namespace_schema() -> None:
    tables = deploy.mirror_tables(MIRRORS_SQL, CATALOG, TEST)
    assert len(tables) == sum(1 for table in CATALOG.tables if table.read_through)
    assert {table.schema for table in tables} == {"TEST_CFB_CORE"}
    assert all(table.statement.startswith("CREATE TABLE TEST_CFB_CORE.") for table in tables)
    assert "CFB_CORE." not in "".join(t.statement.replace("TEST_CFB_CORE.", "") for t in tables)


def test_stale_mirror_file_is_refused_with_the_make_generate_hint() -> None:
    stale = MIRRORS_SQL.replace(CATALOG.catalog_hash, "0" * 64, 1)
    with pytest.raises(DeployError, match="make generate"):
        deploy.mirror_tables(stale, CATALOG, REAL)
    with pytest.raises(DeployError, match="no catalog hash"):
        deploy.mirror_tables("CREATE TABLE CFB_CORE.X (\n  A DOUBLE\n);", CATALOG, REAL)


def test_mirror_file_missing_a_table_is_refused() -> None:
    first = MIRRORS_SQL.index("CREATE TABLE")
    end = MIRRORS_SQL.index(");", first) + 2
    with pytest.raises(DeployError, match="exactly the read-through tables"):
        deploy.mirror_tables(MIRRORS_SQL[:first] + MIRRORS_SQL[end:], CATALOG, REAL)


# --- the steps ---


def test_fresh_deploy_runs_the_steps_in_order() -> None:
    db = FakeDatabase()
    report = run(db, TEST)
    kinds = [s.split("(")[0].split(" WITH ")[0][:48] for s in db.statements]
    first_table = next(i for i, s in enumerate(db.statements) if s.startswith("CREATE TABLE"))
    last_table = max(i for i, s in enumerate(db.statements) if s.startswith("CREATE TABLE"))
    adapter = next(i for i, s in enumerate(db.statements) if "ADAPTER SCRIPT" in s)
    fetch = next(i for i, s in enumerate(db.statements) if "SET SCRIPT" in s)
    vs = next(i for i, s in enumerate(db.statements) if s.startswith("CREATE VIRTUAL SCHEMA"))
    assert kinds[:3] == [
        "CREATE SCHEMA IF NOT EXISTS TEST_CFB_INFRA",
        "CREATE SCHEMA IF NOT EXISTS TEST_CFB_META",
        "CREATE SCHEMA IF NOT EXISTS TEST_CFB_CORE",
    ]
    assert 3 <= first_table <= last_table < adapter < fetch < vs
    assert report.fetch_udf == "created"
    assert report.virtual_schema == "created"


def test_meta_tables_come_first_and_come_from_the_meta_ddl() -> None:
    db = FakeDatabase()
    run(db, TEST)
    tables = db.starting_with("CREATE TABLE")
    assert tables[0].startswith("CREATE TABLE TEST_CFB_META.LOAD_LOG (")
    assert tables[1].startswith("CREATE TABLE TEST_CFB_META.FETCH_LOG (")
    assert len(tables) == 2 + sum(1 for table in CATALOG.tables if table.read_through)
    assert "REQ_SOURCE VARCHAR(8) UTF8 NOT NULL" in tables[1]


def test_scripts_use_the_bundle_and_the_namespace_infra_schema() -> None:
    db = FakeDatabase()
    run(db, TEST)
    assert db.scripts[("TEST_CFB_INFRA", "CFBD_ADAPTER")].endswith("adapter-body\n# v1\n")
    adapter = next(s for s in db.statements if "ADAPTER SCRIPT" in s)
    assert adapter.startswith(
        "CREATE OR REPLACE PYTHON3 ADAPTER SCRIPT TEST_CFB_INFRA.CFBD_ADAPTER"
    )
    fetch = next(s for s in db.statements if "SET SCRIPT" in s)
    assert fetch.startswith("CREATE PYTHON3 SET SCRIPT TEST_CFB_INFRA.CFBD_FETCH(TABLE_NAME")
    assert "EMITS (...) AS\n" in fetch
    assert fetch.endswith("fetch-body\n# v1\n/")


def test_virtual_schema_is_created_with_the_namespace_properties() -> None:
    db = FakeDatabase()
    run(db, TEST)
    assert db.virtual_schemas["TEST_CFBD_VS"] == {
        "CONNECTION_NAME": "CFBD_API_MOCK",
        "INFRA_SCHEMA": "TEST_CFB_INFRA",
        "META_SCHEMA": "TEST_CFB_META",
        "CORE_SCHEMA": "TEST_CFB_CORE",
    }
    create = db.starting_with("CREATE VIRTUAL SCHEMA")[0]
    assert create.startswith("CREATE VIRTUAL SCHEMA TEST_CFBD_VS USING TEST_CFB_INFRA.CFBD_ADAPTER")


def test_real_namespace_uses_the_real_connection_and_extra_properties() -> None:
    db = FakeDatabase()
    run(db, REAL, properties={"MAX_CALLS_PER_QUERY": "7", "FETCH_MODE": "replay"})
    assert db.virtual_schemas["CFBD_VS"]["CONNECTION_NAME"] == "CFBD_API"
    assert db.virtual_schemas["CFBD_VS"]["MAX_CALLS_PER_QUERY"] == "7"
    assert db.virtual_schemas["CFBD_VS"]["FETCH_MODE"] == "replay"


def test_connection_option_overrides_the_default() -> None:
    db = FakeDatabase()
    run(db, TEST, connection_name="MY_MOCK")
    assert db.virtual_schemas["TEST_CFBD_VS"]["CONNECTION_NAME"] == "MY_MOCK"


@pytest.mark.parametrize(
    "properties",
    [{"NO_SUCH": "x"}, {"MAX_PARALLEL_FETCHES": "99"}, {"FETCH_MODE": "dream"}],
)
def test_invalid_properties_fail_before_any_statement(properties: dict[str, str]) -> None:
    db = FakeDatabase()
    with pytest.raises(DeployError, match="invalid virtual schema properties"):
        run(db, TEST, properties=properties)
    assert db.statements == []


def test_adapter_is_replaced_on_every_deploy() -> None:
    db = FakeDatabase()
    run(db, TEST)
    run(db, TEST)
    assert len([s for s in db.statements if "ADAPTER SCRIPT" in s]) == 2


def test_second_deploy_creates_nothing_and_keeps_the_fetch_udf() -> None:
    db = FakeDatabase()
    run(db, TEST)
    db.statements.clear()
    report = run(db, TEST)
    assert not db.starting_with("CREATE TABLE")
    assert not [s for s in db.statements if "SET SCRIPT" in s]
    assert not db.starting_with("CREATE VIRTUAL SCHEMA")
    assert db.starting_with("ALTER VIRTUAL SCHEMA TEST_CFBD_VS REFRESH")
    assert report.fetch_udf == "unchanged"
    assert report.virtual_schema == "refreshed"


def test_fetch_udf_is_replaced_only_when_its_body_changed() -> None:
    db = FakeDatabase()
    run(db, TEST)
    db.statements.clear()
    changed = bundle.Bundle(FAKE_BUNDLE.adapter_body, "fetch-body\n# v2")
    report = run(db, TEST, built=changed)
    replace = [s for s in db.statements if "SET SCRIPT" in s]
    assert len(replace) == 1
    assert replace[0].startswith("CREATE OR REPLACE PYTHON3 SET SCRIPT TEST_CFB_INFRA.CFBD_FETCH(")
    assert report.fetch_udf == "replaced"
    assert any("make grants" in step and "TEST_" in step for step in report.next_steps)


def test_force_replaces_an_unchanged_fetch_udf() -> None:
    db = FakeDatabase()
    run(db, TEST)
    db.statements.clear()
    report = run(db, TEST, force_fetch_replace=True)
    assert len([s for s in db.statements if "SET SCRIPT" in s]) == 1
    assert report.fetch_udf == "replaced"
    assert any("forced" in line for line in report.lines)


def test_real_namespace_needs_no_script_grant_follow_up_after_a_replace() -> None:
    db = FakeDatabase()
    run(db, REAL)
    changed = bundle.Bundle(FAKE_BUNDLE.adapter_body, "fetch-body\n# v2")
    report = run(db, REAL, built=changed)
    assert report.fetch_udf == "replaced"
    assert report.next_steps == ()


def test_first_real_deploy_points_at_the_reader_role() -> None:
    report = run(FakeDatabase(), REAL)
    assert any("CFB_READER" in step for step in report.next_steps)


def test_existing_virtual_schema_only_sets_changed_properties_then_refreshes() -> None:
    db = FakeDatabase()
    run(db, TEST)
    db.statements.clear()
    run(db, TEST, properties={"MAX_CALLS_PER_QUERY": "3"})
    alters = db.starting_with("ALTER VIRTUAL SCHEMA")
    assert alters == [
        "ALTER VIRTUAL SCHEMA TEST_CFBD_VS SET MAX_CALLS_PER_QUERY = '3'",
        "ALTER VIRTUAL SCHEMA TEST_CFBD_VS REFRESH",
    ]
    assert not [s for s in db.statements if s.startswith(("DROP", "CREATE VIRTUAL"))]


def test_properties_set_by_hand_survive_a_redeploy() -> None:
    db = FakeDatabase()
    run(db, TEST)
    db.virtual_schemas["TEST_CFBD_VS"]["FETCH_MODE"] = "replay"
    run(db, TEST)
    assert db.virtual_schemas["TEST_CFBD_VS"]["FETCH_MODE"] == "replay"


# --- existing tables must match ---


def test_mismatching_mirror_fails_with_the_diff_and_alters_nothing() -> None:
    db = FakeDatabase()
    run(db, TEST)
    table = ("TEST_CFB_CORE", "GAMES")
    columns = db.tables[table]
    db.tables[table] = [("ID", "VARCHAR(5) UTF8", True), *columns[1:-1]]
    db.statements.clear()
    with pytest.raises(DeployError, match="do not match the catalog") as raised:
        run(db, TEST)
    message = str(raised.value)
    assert "TEST_CFB_CORE.GAMES" in message
    assert "nothing was altered" in message
    assert not [s for s in db.statements if s.startswith(("ALTER TABLE", "DROP", "CREATE TABLE"))]
    assert not [s for s in db.statements if "SCRIPT" in s]


def test_mismatching_meta_table_fails_before_the_mirrors() -> None:
    db = FakeDatabase()
    db.tables[("TEST_CFB_META", "FETCH_LOG")] = [("SOURCE", "VARCHAR(8) UTF8", False)]
    with pytest.raises(DeployError, match="meta tables") as raised:
        run(db, TEST)
    assert "TEST_CFB_META.FETCH_LOG" in str(raised.value)
    assert not db.starting_with("CREATE TABLE TEST_CFB_CORE")


def test_a_missing_mirror_is_created_next_to_matching_ones() -> None:
    db = FakeDatabase()
    run(db, TEST)
    del db.tables[("TEST_CFB_CORE", "GAMES")]
    db.statements.clear()
    run(db, TEST)
    assert [s.split(" (")[0] for s in db.starting_with("CREATE TABLE")] == [
        "CREATE TABLE TEST_CFB_CORE.GAMES"
    ]


# --- API_FEATURES ---


def test_features_are_synced_from_info_when_the_connection_can_be_used() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"], info_flags=(False, True, True, False, False))
    report = run(db, TEST)
    assert report.api_features == ("scoreboard", "weather")
    assert db.virtual_schemas["TEST_CFBD_VS"]["API_FEATURES"] == "scoreboard,weather"
    info = next(q for q in db.queries if '."INFO"' in q)
    assert info.startswith('SELECT "FEATURES_ADJUSTED_METRICS", "FEATURES_WEATHER"')
    assert info.endswith('FROM TEST_CFBD_VS."INFO"')


def test_synced_features_cover_every_known_feature_column() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"], info_flags=(True,) * 5)
    report = run(db, TEST)
    assert report.api_features == (
        "adjustedMetrics",
        "graphQl",
        "livePlayByPlay",
        "scoreboard",
        "weather",
    )
    info = next(q for q in db.queries if '."INFO"' in q)
    for column in FEATURE_COLUMNS:
        assert f'"{column}"' in info


def test_no_feature_flags_set_leaves_the_property_unset() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"], info_flags=(False,) * 5)
    report = run(db, TEST)
    assert report.api_features == ()
    assert "API_FEATURES" not in db.virtual_schemas["TEST_CFBD_VS"]


def test_features_that_go_away_are_cleared() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"], info_flags=(False, True, False, False, False))
    run(db, TEST)
    db.info_flags = (False,) * 5
    run(db, TEST)
    assert db.virtual_schemas["TEST_CFBD_VS"]["API_FEATURES"] == ""


def test_missing_connection_leaves_features_alone_without_querying_info() -> None:
    db = FakeDatabase()
    report = run(db, TEST)
    assert report.api_features is None
    assert not [q for q in db.queries if '."INFO"' in q]
    assert any("CFBD_API_MOCK does not exist" in line for line in report.lines)
    assert "API_FEATURES" not in db.virtual_schemas["TEST_CFBD_VS"]


UDF_TRACEBACK = (
    "VM error: F-UDF-CL-LIB-1127: F-UDF-CL-SL-PYTHON-1002: Exception during run \n"
    "CFBD_FETCH:33 run\n<bundle vs.cfbd_vs.fetch>:183 run\n"
    '  File "/usr/lib/python3.12/concurrent/futures/_base.py", line 619, in result_iterator\n'
    "vs.cfbd_vs.fetch.FetchEntryError: {cause}\n (Session: 1879662402071298048)"
)


def test_name_resolution_failure_reports_the_cause_line_and_points_at_dns_check() -> None:
    cause = (
        "CFBD_VS.INFO: /info: name resolution failed for host 'api.example'. Run `make dns-check`."
    )
    db = FakeDatabase(
        connections=["CFBD_API"], info_flags=RuntimeError(UDF_TRACEBACK.format(cause=cause))
    )
    report = run(db, REAL)
    assert report.api_features is None
    line = next(line for line in report.lines if line.startswith("API_FEATURES"))
    assert "name resolution failed for host 'api.example'" in line
    assert line.count("make dns-check") == 1
    assert "F-UDF-CL-LIB" not in line
    assert "left as it is" in line


def test_name_resolution_hint_is_added_when_the_message_lacks_it() -> None:
    db = FakeDatabase(connections=["CFBD_API"], info_flags=RuntimeError("cannot resolve host x"))
    line = next(line for line in run(db, REAL).lines if line.startswith("API_FEATURES"))
    assert line.endswith("(run `make dns-check`)")


def test_connection_access_denied_reports_the_cause_and_points_at_grants_once() -> None:
    cause = (
        "CFBD_VS.INFO: connection 'CFBD_API_MOCK' cannot be read: no access. "
        "A DBA must run `make grants`"
    )
    db = FakeDatabase(
        connections=["CFBD_API_MOCK"], info_flags=RuntimeError(UDF_TRACEBACK.format(cause=cause))
    )
    line = next(line for line in run(db, TEST).lines if line.startswith("API_FEATURES"))
    assert "connection 'CFBD_API_MOCK' cannot be read" in line
    assert line.count("make grants") == 1
    assert "Session" not in line


def test_other_info_errors_are_reported_on_their_first_line() -> None:
    error = RuntimeError("HTTP 500 from /info\nsecond line")
    db = FakeDatabase(connections=["CFBD_API_MOCK"], info_flags=error)
    line = next(line for line in run(db, TEST).lines if line.startswith("API_FEATURES"))
    assert "HTTP 500 from /info" in line
    assert "second line" not in line


def test_failed_sync_keeps_features_synced_earlier() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"], info_flags=(False, True, False, False, False))
    run(db, TEST)
    db.info_flags = RuntimeError("down")
    run(db, TEST)
    assert db.virtual_schemas["TEST_CFBD_VS"]["API_FEATURES"] == "weather"


def test_sync_can_be_switched_off() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"])
    report = run(db, TEST, sync_api_features=False)
    assert report.api_features is None
    assert not [q for q in db.queries if '."INFO"' in q]


# --- safety ---


def test_test_namespace_never_touches_the_real_objects() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"])
    run(db, TEST)
    text = "\n".join(db.statements + db.queries)
    assert not re.search(r"\bCFBD_API\b", text)
    assert not re.search(r"(?<!TEST_)\bCFB_(INFRA|META|CORE)\b", text)
    assert not re.search(r"(?<!TEST_)\bCFBD_VS\b", text)


def test_no_statement_grants_anything_or_replaces_tables() -> None:
    db = FakeDatabase(connections=["CFBD_API_MOCK"])
    run(db, TEST)
    run(db, TEST, force_fetch_replace=True)
    text = "\n".join(db.statements).upper()
    for forbidden in ("GRANT ", "DROP ", "ALTER TABLE", "CREATE OR REPLACE TABLE", "CREATE USER"):
        assert forbidden not in text


@pytest.mark.parametrize(
    "options",
    [
        DeployOptions(connection_name="CFBD_API"),
        DeployOptions(connection_name="cfbd_api"),
        DeployOptions(properties={"CONNECTION_NAME": "CFBD_API"}),
        DeployOptions(properties={"CONNECTION_NAME": ""}),
    ],
)
def test_a_prefixed_namespace_cannot_be_deployed_on_the_real_connection(
    options: DeployOptions,
) -> None:
    with pytest.raises(DeployError, match="real connection"):
        deploy.desired_properties(TEST, options)


def test_the_real_namespace_defaults_to_the_real_connection_and_a_prefix_to_the_mock() -> None:
    assert deploy.desired_properties(REAL, DeployOptions())["CONNECTION_NAME"] == "CFBD_API"
    assert deploy.desired_properties(TEST, DeployOptions())["CONNECTION_NAME"] == "CFBD_API_MOCK"


def test_deploy_uses_the_runtimes_string_literal_and_type_normalizer() -> None:
    assert getattr(deploy, "string_literal").__module__ == "vs.cfbd_vs.sqlrender"  # noqa: B009
    assert getattr(deploy, "normalize_sql_type").__module__ == "vs.cfbd_vs.sqltypes"  # noqa: B009
