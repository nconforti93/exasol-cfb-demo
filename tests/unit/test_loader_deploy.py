"""`loader/deploy.py`: the loader objects of one namespace, deployed through a fake connection."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from loader import bundle as lua_bundle
from loader import deploy as loader_deploy
from loader.deploy import LoaderOptions
from loader.plan import FIRST_SEASON, LAST_SEASON, load_plan
from tests.unit.test_deploy import CATALOG_TEXT, MIRRORS_SQL, FakeDatabase, Result
from vs.host import metaddl
from vs.host.deploy import DeployError, parse_table_ddl
from vs.host.namespace import Namespace

REAL = Namespace("")
TEST = Namespace("TEST_")
PROBE = Namespace("PROBE_LD_")
PROJECT_PLAN = load_plan()
PLANNED_TABLES = sorted({entry.table for entry in PROJECT_PLAN.entries})
SEASON_COUNT = LAST_SEASON - FIRST_SEASON + 1
LOG_TABLES = (
    "LOAD_LOG",
    "FETCH_LOG",
    "LOAD_RUN",
    "REFRESH_LOG",
    "LOAD_FAILURES",
    "MODEL_CHECK_LOG",
)


class LoaderDatabase(FakeDatabase):
    """The foundation's fake plus the statements the loader deploy sends."""

    def __init__(
        self,
        namespace: Namespace = REAL,
        *,
        orphans: Sequence[tuple[str, int]] = (),
        season_state: tuple[Any, ...] | None = None,
        fail_on: str | None = None,
        foundation: bool = True,
        deployed_settings: Sequence[tuple[str, str]] = (),
    ) -> None:
        super().__init__()
        self.namespace = namespace
        self.deployed_settings = list(deployed_settings)
        self.orphans = list(orphans)
        self.season_state = season_state
        self.fail_on = fail_on
        self.autocommit_calls: list[bool] = []
        self.log: list[str] = []
        if foundation:
            self.virtual_schemas[namespace.vs_name] = {}
            for name in metaddl.meta_ddl_names():
                ddl = parse_table_ddl(metaddl.render_meta_ddl(name, namespace.meta_schema))
                self.tables[(ddl.schema, ddl.name)] = list(ddl.columns)

    def set_autocommit(self, value: bool) -> None:
        self.autocommit_calls.append(value)
        self.log.append(f"autocommit {value}")

    def execute(self, query: str) -> Result:
        self.log.append(query.split("\n", 1)[0][:90])
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("boom")
        return super().execute(query)

    def _select(self, query: str) -> Result:
        if "LOAD_ORPHANS" in query:
            return Result(self.orphans)
        if '"SEASON_NUMBERS"' in query:
            state = self.season_state or (0, 0, None, None)
            return Result([state])
        if '"LOADER_CONFIG"' in query:
            return Result(self.deployed_settings)
        return super()._select(query)


def stub_lua(directory: Path, *names: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("loadlib.lua", *names):
        (directory / name).write_text("-- stub\nlocal x = 1\n", encoding="utf-8")
    return directory


@pytest.fixture
def lua_dir(tmp_path: Path) -> Path:
    return stub_lua(tmp_path / "lua", "backfill.lua", "refresh_week.lua", "run_lock.lua")


@pytest.fixture
def no_lua(tmp_path: Path) -> Path:
    return tmp_path / "no-lua"


def options(lua: Path, **overrides: Any) -> LoaderOptions:
    values: dict[str, Any] = {
        "catalog_text": CATALOG_TEXT,
        "mirrors_sql": MIRRORS_SQL,
        "lua_dir": lua,
    }
    values.update(overrides)
    return LoaderOptions(**values)


def run(
    db: LoaderDatabase, lua: Path, namespace: Namespace = REAL, **overrides: Any
) -> loader_deploy.LoaderReport:
    return loader_deploy.deploy(db, namespace, options(lua, **overrides))


def inserted(db: LoaderDatabase, table: str) -> str:
    matches = [s for s in db.statements if s.startswith(f"INSERT INTO {table} ")]
    assert len(matches) == 1, matches
    return matches[0]


# --- stage schema, stage tables, meta tables ----------------------------------------------------


def test_the_stage_schema_and_one_stage_table_per_planned_table_are_created(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    assert "CREATE SCHEMA IF NOT EXISTS TEST_CFB_STAGE" in db.statements
    staged = sorted(t for (schema, t) in db.tables if schema == "TEST_CFB_STAGE")
    assert staged == PLANNED_TABLES


def test_an_empty_plan_deploys_without_stage_tables(no_lua: Path) -> None:
    """Review m16: a valid plan without entries crashed the deploy (IndexError)."""
    db = LoaderDatabase(TEST)
    report = run(db, no_lua, TEST, plan_text="version: 1\nentries: []\n")
    assert "stage tables: none planned" in report.lines
    assert not [s for s in db.statements if s.startswith("CREATE TABLE TEST_CFB_STAGE")]
    assert not [s for s in db.statements if s.startswith("INSERT INTO TEST_CFB_META.LOAD_PLAN ")]


def test_a_stage_table_has_the_mirror_columns(no_lua: Path) -> None:
    db = LoaderDatabase(REAL)
    run(db, no_lua)
    mirror = parse_table_ddl(
        next(
            m.group(0)
            for m in re.finditer(r"CREATE TABLE CFB_CORE\.GAMES \(.*?\n\)", MIRRORS_SQL, re.S)
        )
    )
    assert db.tables[("CFB_STAGE", "GAMES")] == list(mirror.columns)


def test_the_loader_meta_tables_are_created_in_order(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    created = [
        s.split()[2].split(".")[1]
        for s in db.statements
        if s.startswith("CREATE TABLE TEST_CFB_META.")
    ]
    assert [name.lower() for name in created] == list(metaddl.loader_meta_ddl_names())


def test_a_stage_table_that_does_not_match_stops_the_deploy_before_any_change(
    no_lua: Path,
) -> None:
    db = LoaderDatabase(TEST)
    db.tables[("TEST_CFB_STAGE", "GAMES")] = [("ROW_NO", "VARCHAR(5) UTF8", True)]
    with pytest.raises(DeployError, match="stage tables do not match") as caught:
        run(db, no_lua, TEST)
    assert "TEST_CFB_STAGE.GAMES" in str(caught.value)
    assert not [s for s in db.statements if s.startswith(("INSERT", "DELETE", "CREATE TABLE"))]


def test_a_loader_meta_table_with_a_different_shape_stops_the_deploy(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    db.tables[("TEST_CFB_META", "LOAD_PLAN")] = [("ENTRY_ID", "DOUBLE", True)]
    with pytest.raises(DeployError, match="meta tables do not match"):
        run(db, no_lua, TEST)
    assert not [s for s in db.statements if s.startswith(("INSERT", "DELETE"))]


def test_the_identity_column_of_load_run_matches_what_the_database_reports(
    no_lua: Path,
) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    assert db.tables[("TEST_CFB_META", "LOAD_RUN")][0] == ("RUN_ID", "DECIMAL(18,0)", False)
    again = run(db, no_lua, TEST)
    assert "0 created" in " ".join(again.lines)


def test_a_deploy_without_the_virtual_schema_asks_for_the_foundation_first(no_lua: Path) -> None:
    db = LoaderDatabase(TEST, foundation=False)
    with pytest.raises(DeployError, match="deploy the virtual schema first"):
        run(db, no_lua, TEST)
    assert not db.statements


# --- the plan tables --------------------------------------------------------------------------


def test_the_plan_is_written_in_one_transaction_in_the_listed_order(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    start = db.log.index("autocommit False")
    stop = db.log.index("autocommit True")
    tables = [
        re.match(r"(?:DELETE FROM|INSERT INTO) TEST_CFB_META\.(\w+)", line).group(1)  # type: ignore[union-attr]
        for line in db.log[start:stop]
        if line.startswith(("DELETE FROM", "INSERT INTO"))
    ]
    first_seen = list(dict.fromkeys(tables))
    assert first_seen == ["LOAD_PLAN", "LOAD_PLAN_GAPS", "LOADER_CONFIG", "REFRESH_STEPS"]
    assert db.log[stop - 1] == "COMMIT"
    assert db.autocommit_calls == [False, True, False, True]


def test_every_plan_entry_becomes_a_row_with_its_compiled_template(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    statement = inserted(db, "TEST_CFB_META.LOAD_PLAN")
    assert statement.count("\n  (") == len(PROJECT_PLAN.entries)
    assert "'/games?seasonType=both&year={season}'" in statement
    assert PROJECT_PLAN.plan_hash in statement
    assert "'TEAMS'" in statement


def test_the_gap_of_the_plan_is_written(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    games = next(e.entry_id for e in PROJECT_PLAN.entries if e.table == "GAMES")
    assert f"({games}, 1871)" in inserted(db, "TEST_CFB_META.LOAD_PLAN_GAPS")


def test_a_plan_without_gaps_and_steps_writes_neither_table_but_still_clears_them(
    no_lua: Path,
) -> None:
    db = LoaderDatabase(TEST)
    text = (
        "version: 1\nentries:\n  - table: TEAMS\n    profile: demo\n    priority: 1\n"
        "    slicing: none\n    refresh_class: static\n"
    )
    run(db, no_lua, TEST, plan_text=text)
    assert "DELETE FROM TEST_CFB_META.LOAD_PLAN_GAPS" in db.statements
    assert not [
        s for s in db.statements if s.startswith("INSERT INTO TEST_CFB_META.LOAD_PLAN_GAPS")
    ]
    assert not [s for s in db.statements if s.startswith("INSERT INTO TEST_CFB_META.REFRESH_STEPS")]
    assert sorted(t for (s, t) in db.tables if s == "TEST_CFB_STAGE") == ["TEAMS"]


def test_registered_refresh_steps_are_written(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    text = (
        "version: 1\nrefresh_steps:\n"
        "  - {step_no: 100, name: MODEL, script: CFB_INFRA.REFRESH_MODEL}\n"
        "entries:\n  - table: TEAMS\n    profile: demo\n    priority: 1\n"
        "    slicing: none\n    refresh_class: static\n"
    )
    run(db, no_lua, TEST, plan_text=text)
    assert "(100, 'MODEL', 'CFB_INFRA.REFRESH_MODEL')" in inserted(
        db, "TEST_CFB_META.REFRESH_STEPS"
    )


def test_the_settings_of_the_plan_are_written(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    config = inserted(db, "TEST_CFB_META.LOADER_CONFIG")
    for name, value in PROJECT_PLAN.config.settings().items():
        assert f"('{name}', '{value}')" in config
    assert "NOW_OVERRIDE" not in config


def test_overrides_replace_the_yaml_settings(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(
        db,
        no_lua,
        TEST,
        config_overrides={"monthly_call_budget": "400", "refresh_reserve": "50"},
    )
    config = inserted(db, "TEST_CFB_META.LOADER_CONFIG")
    assert "('MONTHLY_CALL_BUDGET', '400')" in config
    assert "('REFRESH_RESERVE', '50')" in config
    assert "('QUARANTINE_AFTER', '2')" in config


def test_a_deploy_that_changes_the_budget_settings_warns(no_lua: Path) -> None:
    """Review m4: a redeploy must not lift a deployment's budget without a message."""
    deployed = [
        ("MONTHLY_CALL_BUDGET", "400"),
        ("REFRESH_RESERVE", "50"),
        ("QUARANTINE_AFTER", "2"),
    ]
    db = LoaderDatabase(TEST, deployed_settings=deployed)
    report = run(db, no_lua, TEST)
    assert report.warnings == (
        "MONTHLY_CALL_BUDGET changes from 400 to 1000",
        "REFRESH_RESERVE changes from 50 to 150",
    )
    read = next(q for q in db.queries if '"LOADER_CONFIG"' in q)
    assert db.log.index(read.split("\n", 1)[0][:90]) < next(
        i
        for i, line in enumerate(db.log)
        if line.startswith("DELETE FROM TEST_CFB_META.LOADER_CONFIG")
    )


def test_an_unchanged_or_first_budget_setting_does_not_warn(no_lua: Path) -> None:
    same = [("MONTHLY_CALL_BUDGET", "400"), ("REFRESH_RESERVE", "50")]
    db = LoaderDatabase(TEST, deployed_settings=same)
    overrides = {"monthly_call_budget": "400", "refresh_reserve": "50"}
    assert run(db, no_lua, TEST, config_overrides=overrides).warnings == ()
    assert run(LoaderDatabase(TEST), no_lua, TEST).warnings == ()


def test_the_plan_hash_names_the_plan_file_not_the_overrides(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    report = run(db, no_lua, TEST, config_overrides={"monthly_call_budget": "400"})
    assert report.plan_hash == PROJECT_PLAN.plan_hash


def test_now_override_is_kept_in_a_test_namespace(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    report = run(db, no_lua, TEST, config_overrides={"now_override": "2027-02-16"})
    assert "('NOW_OVERRIDE', '2027-02-16')" in inserted(db, "TEST_CFB_META.LOADER_CONFIG")
    assert not report.warnings


@pytest.mark.parametrize("namespace", [REAL, PROBE])
def test_now_override_is_removed_outside_test_namespaces(
    namespace: Namespace, no_lua: Path
) -> None:
    db = LoaderDatabase(namespace)
    report = run(db, no_lua, namespace, config_overrides={"now_override": "2027-02-16"})
    assert "NOW_OVERRIDE" not in inserted(db, f"{namespace.meta_schema}.LOADER_CONFIG")
    assert any("NOW_OVERRIDE" in warning and "TEST_" in warning for warning in report.warnings)


# --- invalid input changes nothing --------------------------------------------------------------


def test_an_invalid_plan_sends_no_statement(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    with pytest.raises(DeployError, match="unknown-table"):
        run(db, no_lua, TEST, plan_text="version: 1\nentries:\n  - table: NOPE\n")
    assert not db.statements and not db.queries


def test_one_deploy_lists_the_parse_and_the_request_rule_problems(no_lua: Path) -> None:
    plan = (
        "version: 1\nentries:\n"
        "  - {table: NOPE, profile: demo, priority: 1, slicing: none, refresh_class: static}\n"
        "  - {table: PLAYS, profile: demo, priority: 2, slicing: season, seasons: {from: 2019},"
        " refresh_class: weekly}\n"
    )
    db = LoaderDatabase(TEST)
    with pytest.raises(DeployError) as caught:
        run(db, no_lua, TEST, plan_text=plan)
    message = str(caught.value)
    assert message.startswith("the load plan has 2 problems:")
    assert "entry 1 (NOPE): unknown-table" in message
    assert "entry 2 (PLAYS): plays.spec_required" in message
    assert not db.statements and not db.queries


def test_an_invalid_override_sends_no_statement_and_lists_every_problem(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    with pytest.raises(DeployError) as caught:
        run(
            db,
            no_lua,
            TEST,
            config_overrides={"monthly_call_budget": "lots", "refresh_reserve": "9999"},
        )
    assert "LOADER_CONFIG" in str(caught.value)
    assert "monthly_call_budget" in str(caught.value)
    assert not db.statements and not db.queries


def test_a_failure_inside_the_plan_transaction_rolls_back_and_resets_autocommit(
    no_lua: Path,
) -> None:
    db = LoaderDatabase(TEST, fail_on="INSERT INTO TEST_CFB_META.LOADER_CONFIG")
    with pytest.raises(RuntimeError, match="boom"):
        run(db, no_lua, TEST)
    assert "ROLLBACK" in db.statements
    assert "COMMIT" not in db.statements
    assert db.autocommit_calls[-1] is True
    assert not [s for s in db.statements if s.startswith("CREATE OR REPLACE VIEW")]


# --- season numbers ---------------------------------------------------------------------------


def test_the_season_numbers_cover_1869_to_2100(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    report = run(db, no_lua, TEST)
    statement = inserted(db, "TEST_CFB_META.SEASON_NUMBERS")
    seasons = [int(n) for n in re.findall(r"\((\d{4})\)", statement)]
    assert seasons == list(range(FIRST_SEASON, LAST_SEASON + 1))
    assert any("season numbers" in line and "filled" in line for line in report.lines)


def test_a_complete_season_table_is_left_alone(no_lua: Path) -> None:
    db = LoaderDatabase(TEST, season_state=(SEASON_COUNT, SEASON_COUNT, FIRST_SEASON, LAST_SEASON))
    report = run(db, no_lua, TEST)
    assert not [
        s for s in db.statements if s.startswith(("DELETE", "INSERT")) and "SEASON_NUMBERS" in s
    ]
    assert any("season numbers" in line and "unchanged" in line for line in report.lines)


@pytest.mark.parametrize(
    "state",
    [
        (0, 0, None, None),
        (5, 5, 1869, 1873),
        (SEASON_COUNT + 1, SEASON_COUNT, FIRST_SEASON, LAST_SEASON),
        (SEASON_COUNT, SEASON_COUNT, FIRST_SEASON + 1, LAST_SEASON),
    ],
)
def test_an_incomplete_season_table_is_replaced(state: tuple[Any, ...], no_lua: Path) -> None:
    db = LoaderDatabase(TEST, season_state=state)
    run(db, no_lua, TEST)
    assert "DELETE FROM TEST_CFB_META.SEASON_NUMBERS" in db.statements
    assert "INSERT INTO TEST_CFB_META.SEASON_NUMBERS" in inserted(
        db, "TEST_CFB_META.SEASON_NUMBERS"
    )


# --- views ------------------------------------------------------------------------------------


def test_the_views_follow_the_plan_tables_in_creation_order(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, no_lua, TEST)
    views = [s for s in db.statements if s.startswith("CREATE OR REPLACE VIEW")]
    assert views == [
        metaddl.render_loader_view(name, TEST).strip() for name in metaddl.loader_view_names()
    ]
    first_view = db.log.index(next(line for line in db.log if "VIEW" in line))
    last_insert = max(i for i, line in enumerate(db.log) if line.startswith("INSERT INTO"))
    assert last_insert < first_view


def test_views_of_a_prefixed_namespace_read_only_that_namespace(no_lua: Path) -> None:
    db = LoaderDatabase(PROBE)
    run(db, no_lua, PROBE)
    views = " ".join(s for s in db.statements if s.startswith("CREATE OR REPLACE VIEW"))
    assert "PROBE_LD_CFB_META" in views and "PROBE_LD_CFBD_VS" in views
    assert not re.search(r'"CFB_META"|"CFBD_VS"', views)


# --- Lua scripts ------------------------------------------------------------------------------


def test_every_script_is_installed_from_the_bundle_text(lua_dir: Path) -> None:
    db = LoaderDatabase(TEST)
    report = run(db, lua_dir, TEST)
    assert report.installed_scripts == ("BACKFILL", "REFRESH_WEEK", "RUN_LOCK")
    assert report.skipped_scripts == ()
    scripts = [s for s in db.statements if "LUA SCRIPT" in s.split("\n", 1)[0]]
    assert scripts == [
        lua_bundle.build_script(script, TEST, lua_dir) for script in lua_bundle.SCRIPTS
    ]
    assert ("TEST_CFB_INFRA", "BACKFILL") in db.scripts
    assert ("TEST_CFB_INFRA", "RUN_LOCK") in db.scripts


def test_a_script_without_sources_is_skipped_with_a_notice(no_lua: Path) -> None:
    db = LoaderDatabase(TEST)
    report = run(db, no_lua, TEST)
    assert report.installed_scripts == ()
    assert report.skipped_scripts == ("BACKFILL", "REFRESH_WEEK", "RUN_LOCK")
    notices = [line for line in report.lines if "skipped" in line]
    assert len(notices) == 3
    assert "TEST_CFB_INFRA.BACKFILL" in notices[0]
    assert not [s for s in db.statements if "LUA SCRIPT" in s.split("\n", 1)[0]]


def test_only_the_script_with_sources_is_installed(tmp_path: Path) -> None:
    lua = stub_lua(tmp_path / "lua", "backfill.lua")
    db = LoaderDatabase(TEST)
    report = run(db, lua, TEST)
    assert (report.installed_scripts, report.skipped_scripts) == (
        ("BACKFILL",),
        ("REFRESH_WEEK", "RUN_LOCK"),
    )


def test_a_script_that_breaks_the_source_checks_stops_the_deploy(tmp_path: Path) -> None:
    lua = stub_lua(tmp_path / "lua", "backfill.lua", "refresh_week.lua")
    (lua / "backfill.lua").write_text("query('SELECT 1')\n", encoding="utf-8")
    with pytest.raises(DeployError, match="bare query"):
        run(LoaderDatabase(TEST), lua, TEST)


# --- orphans ----------------------------------------------------------------------------------


def test_orphan_slices_are_reported_per_table_as_a_warning(no_lua: Path) -> None:
    db = LoaderDatabase(TEST, orphans=[("GAMES", 3), ("CALENDAR", 1)])
    report = run(db, no_lua, TEST)
    assert dict(report.orphans) == {"GAMES": 3, "CALENDAR": 1}
    warnings = [w for w in report.warnings if "orphan" in w]
    assert any("GAMES: 3" in w for w in warnings)
    assert any("CALENDAR: 1" in w for w in warnings)
    assert any("TEST_CFB_META.LOAD_ORPHANS" in w for w in warnings)
    assert all(w in report.lines or f"warning: {w}" in report.lines for w in report.warnings)
    query = next(q for q in db.queries if "LOAD_ORPHANS" in q)
    assert query == (
        'SELECT "TABLE_NAME", COUNT(*) FROM "TEST_CFB_META"."LOAD_ORPHANS" GROUP BY 1 ORDER BY 1'
    )


def test_no_orphans_means_no_warning(no_lua: Path) -> None:
    report = run(LoaderDatabase(TEST), no_lua, TEST)
    assert report.orphans == {}
    assert not [w for w in report.warnings if "orphan" in w]


# --- what the deploy never touches ------------------------------------------------------------


@pytest.mark.parametrize("namespace", [REAL, TEST, PROBE])
def test_the_deploy_never_touches_logs_runs_failures_or_the_mirrors(
    namespace: Namespace, lua_dir: Path
) -> None:
    db = LoaderDatabase(namespace)
    run(db, lua_dir, namespace)
    for statement in db.statements:
        verb = statement.split(None, 1)[0]
        if verb in ("INSERT", "DELETE", "UPDATE", "TRUNCATE", "MERGE", "DROP"):
            target = statement.split("\n", 1)[0]
            assert not any(f".{name}" in target for name in LOG_TABLES), target
            assert f"{namespace.core_schema}." not in target, target
        if verb == "CREATE" and statement.startswith("CREATE TABLE"):
            assert not statement.startswith(f"CREATE TABLE {namespace.core_schema}."), statement


def test_a_second_deploy_creates_no_table_and_replaces_the_plan_again(lua_dir: Path) -> None:
    db = LoaderDatabase(TEST)
    run(db, lua_dir, TEST)
    created = len([s for s in db.statements if s.startswith("CREATE TABLE")])
    again = run(db, lua_dir, TEST)
    assert len([s for s in db.statements if s.startswith("CREATE TABLE")]) == created
    plan_inserts = [
        s for s in db.statements if s.startswith("INSERT INTO TEST_CFB_META.LOAD_PLAN ")
    ]
    assert len(plan_inserts) == 2
    assert any("0 created" in line for line in again.lines)


def test_the_report_lines_follow_the_steps(lua_dir: Path) -> None:
    report = run(LoaderDatabase(TEST), lua_dir, TEST)
    text = "\n".join(report.lines)
    order = [
        "stage schema TEST_CFB_STAGE",
        "stage tables",
        "loader meta tables",
        "load plan",
        "season numbers",
        "views",
        "Lua script TEST_CFB_INFRA.BACKFILL",
        "orphan",
    ]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)
