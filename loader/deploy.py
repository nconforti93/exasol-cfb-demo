"""Deploy the loader into one namespace: plan tables, views and Lua scripts.

`deploy(connection, namespace, options)` runs after the virtual schema deploy (`vs/host/deploy.py`)
on the same kind of connection: `make deploy` passes a `sys` connection and the integration
harness passes the connection of its test owner. Every name comes from the `Namespace`.

The plan and the overrides are read, validated and compiled first, before any statement is sent,
so an invalid plan or `LOADER_CONFIG` override changes nothing. Then the steps, in order:

1. The stage schema, and one stage table per planned table, from the mirror DDL.
2. The loader's meta tables (`metaddl.loader_meta_ddl_names`).
3. `LOAD_PLAN`, `LOAD_PLAN_GAPS`, `LOADER_CONFIG` and `REFRESH_STEPS` are replaced in one
   transaction. The settings are those of the YAML with the `LOADER_CONFIG` overrides applied;
   `NOW_OVERRIDE` is kept only in a `TEST_` namespace. `LOAD_PLAN.PLAN_HASH` is the hash of the
   plan file, so an override does not change it.
4. `SEASON_NUMBERS` holds 1869 to 2100; it is rewritten only when it does not.
5. The views, in `metaddl.loader_view_names()` order, as `CREATE OR REPLACE VIEW`.
6. The Lua scripts `BACKFILL` and `REFRESH_WEEK`, bundled in memory for the namespace. A script
   whose sources do not exist yet is skipped with a notice.
7. The orphan slices per table, as a warning.

Existing tables must match their DDL exactly, as for the mirrors. The deploy never changes the
data of `LOAD_LOG`, `FETCH_LOG`, `LOAD_RUN`, `REFRESH_LOG`, `LOAD_FAILURES` or the core schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loader import bundle as lua_bundle
from loader.plan import (
    FIRST_SEASON,
    LAST_SEASON,
    LoadPlan,
    PlanError,
    default_catalog,
    load_plan,
    parse_config_overrides,
    parse_plan,
)
from loader.templates import SliceTemplate, compile_plan
from vs.bundle import BundleError
from vs.cfbd_vs.catalog import Catalog
from vs.cfbd_vs.sqlrender import sql_literal
from vs.host import metaddl
from vs.host.deploy import (
    MIRRORS_PATH,
    Connection,
    DeployError,
    TableDdl,
    columns_by_table,
    ensure_tables,
    mirror_tables,
    parse_table_ddl,
)
from vs.host.namespace import Namespace

TEST_PREFIX = "TEST_"
SEASON_COUNT = LAST_SEASON - FIRST_SEASON + 1
PLAN_TABLES = ("LOAD_PLAN", "LOAD_PLAN_GAPS", "LOADER_CONFIG", "REFRESH_STEPS")
FOUNDATION_HINT = "deploy the virtual schema first (`make deploy`)"


@dataclass(frozen=True)
class LoaderOptions:
    """Inputs of one loader deployment; everything defaults to the real artifacts."""

    config_overrides: Mapping[str, str] = field(default_factory=dict)
    plan_text: str | None = None
    catalog_text: str | None = None
    mirrors_sql: str | None = None
    lua_dir: Path = lua_bundle.LUA_DIR


@dataclass(frozen=True)
class LoaderReport:
    """What the deployment did as printable lines, with the facts a caller may act on.

    `warnings` also appear in `lines` (prefixed `warning: `). `orphans` maps a table to its number
    of orphan slices.
    """

    lines: tuple[str, ...]
    warnings: tuple[str, ...]
    orphans: Mapping[str, int]
    installed_scripts: tuple[str, ...]
    skipped_scripts: tuple[str, ...]
    plan_hash: str


@dataclass(frozen=True)
class _Prepared:
    plan: LoadPlan
    templates: tuple[SliceTemplate, ...]
    settings: dict[str, str]
    stage_tables: list[TableDdl]
    dropped_now_override: bool


def parse_overrides(text: str) -> dict[str, str]:
    """`k=v,k=v` of `make deploy LOADER_CONFIG=...` as lower-case names and text values."""
    try:
        return parse_config_overrides(text)
    except PlanError as error:
        raise DeployError(str(error)) from None


def validate(namespace: Namespace, options: LoaderOptions | None = None) -> None:
    """Read, validate and compile the plan and the overrides; sends nothing to a database."""
    _prepare(namespace, options or LoaderOptions())


def deploy(
    connection: Connection, namespace: Namespace, options: LoaderOptions | None = None
) -> LoaderReport:
    """Run every step for `namespace`; raises `DeployError` before any later step."""
    options = options or LoaderOptions()
    prepared = _prepare(namespace, options)
    return _Deployment(connection, namespace, options, prepared).run()


def _prepare(namespace: Namespace, options: LoaderOptions) -> _Prepared:
    catalog = Catalog.from_json(options.catalog_text) if options.catalog_text else default_catalog()
    try:
        plan = _read_plan(options, catalog)
        config = plan.config.with_overrides(options.config_overrides)
        templates = compile_plan(plan, catalog)
    except PlanError as error:
        raise DeployError(str(error)) from None
    keeps_override = namespace.prefix.startswith(TEST_PREFIX)
    dropped = config.now_override is not None and not keeps_override
    settings = config.settings()
    if dropped:
        settings.pop("NOW_OVERRIDE")
    return _Prepared(
        plan, templates, settings, _stage_tables(namespace, plan, catalog, options), dropped
    )


def _read_plan(options: LoaderOptions, catalog: Catalog) -> LoadPlan:
    if options.plan_text is None:
        return load_plan(catalog=catalog)
    return parse_plan(options.plan_text, catalog)


def _stage_tables(
    namespace: Namespace, plan: LoadPlan, catalog: Catalog, options: LoaderOptions
) -> list[TableDdl]:
    mirrors_sql = options.mirrors_sql or MIRRORS_PATH.read_text(encoding="utf-8")
    planned = {entry.table for entry in plan.entries}
    mirrors = [t for t in mirror_tables(mirrors_sql, catalog, namespace) if t.name in planned]
    prefix = f"{namespace.core_schema}."
    return [
        parse_table_ddl(m.statement.replace(prefix, f"{namespace.stage_schema}.", 1))
        for m in sorted(mirrors, key=lambda t: t.name)
    ]


class _Deployment:
    def __init__(
        self,
        connection: Connection,
        namespace: Namespace,
        options: LoaderOptions,
        prepared: _Prepared,
    ) -> None:
        self._connection = connection
        self._namespace = namespace
        self._options = options
        self._prepared = prepared
        self._lines: list[str] = []
        self._warnings: list[str] = []

    def run(self) -> LoaderReport:
        self._require_foundation()
        self._ensure_stage()
        self._ensure_meta_tables()
        self._write_plan()
        self._fill_season_numbers()
        self._create_views()
        installed, skipped = self._install_scripts()
        orphans = self._report_orphans()
        return LoaderReport(
            tuple(self._lines),
            tuple(self._warnings),
            orphans,
            installed,
            skipped,
            self._prepared.plan.plan_hash,
        )

    def _require_foundation(self) -> None:
        vs = self._namespace.vs_name
        found = self._query(
            f"SELECT COUNT(*) FROM EXA_ALL_VIRTUAL_SCHEMAS WHERE SCHEMA_NAME = {sql_literal(vs)}"
        )
        if int(found[0][0]) == 0:
            raise DeployError(f"virtual schema {vs} does not exist: {FOUNDATION_HINT}")
        present = columns_by_table(self._connection, self._namespace.meta_schema)
        missing = [name for name in ("LOAD_LOG", "FETCH_LOG") if name not in present]
        if missing:
            raise DeployError(
                f"{self._namespace.meta_schema} lacks {', '.join(missing)}: {FOUNDATION_HINT}"
            )

    def _ensure_stage(self) -> None:
        schema = self._namespace.stage_schema
        self._execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        self._lines.append(f"stage schema {schema}: ready")
        self._lines.append(
            ensure_tables(self._connection, self._prepared.stage_tables, "stage tables")
        )

    def _ensure_meta_tables(self) -> None:
        meta = self._namespace.meta_schema
        tables = [
            parse_table_ddl(metaddl.render_meta_ddl(name, meta))
            for name in metaddl.loader_meta_ddl_names()
        ]
        self._lines.append(ensure_tables(self._connection, tables, "loader meta tables"))

    def _write_plan(self) -> None:
        meta = self._namespace.meta_schema
        prepared = self._prepared
        rows = {
            "LOAD_PLAN": (_PLAN_COLUMNS, _plan_rows(prepared)),
            "LOAD_PLAN_GAPS": (("ENTRY_ID", "SEASON"), _gap_rows(prepared.plan)),
            "LOADER_CONFIG": (("SETTING_NAME", "SETTING_VALUE"), list(prepared.settings.items())),
            "REFRESH_STEPS": (
                ("STEP_NO", "STEP_NAME", "SCRIPT_NAME"),
                [(s.step_no, s.name, s.script) for s in prepared.plan.refresh_steps],
            ),
        }
        statements: list[str] = []
        for table, (columns, table_rows) in rows.items():
            statements.append(f"DELETE FROM {meta}.{table}")
            if table_rows:
                statements.append(_insert(f"{meta}.{table}", columns, table_rows))
        self._transaction(statements)
        plan = prepared.plan
        self._lines.append(
            f"load plan in {meta}: {len(plan.entries)} entries, {len(rows['LOAD_PLAN_GAPS'][1])} "
            f"gaps, {len(plan.refresh_steps)} refresh steps (plan {plan.plan_hash[:12]})"
        )
        overrides = sorted(self._options.config_overrides)
        if overrides:
            self._lines.append(f"LOADER_CONFIG overrides applied: {', '.join(overrides)}")
        if prepared.dropped_now_override:
            self._warn(
                "NOW_OVERRIDE ignored: only a TEST_ namespace keeps the test clock, "
                f"{self._namespace.meta_schema} uses the real one"
            )

    def _fill_season_numbers(self) -> None:
        table = f"{self._namespace.meta_schema}.SEASON_NUMBERS"
        state = self._query(
            'SELECT COUNT(*), COUNT(DISTINCT "SEASON"), MIN("SEASON"), MAX("SEASON") '
            f'FROM "{self._namespace.meta_schema}"."SEASON_NUMBERS"'
        )[0]
        complete = tuple(None if v is None else int(v) for v in state) == (
            SEASON_COUNT,
            SEASON_COUNT,
            FIRST_SEASON,
            LAST_SEASON,
        )
        if complete:
            self._lines.append(f"season numbers {FIRST_SEASON}-{LAST_SEASON} in {table}: unchanged")
            return
        seasons = [(season,) for season in range(FIRST_SEASON, LAST_SEASON + 1)]
        self._transaction([f"DELETE FROM {table}", _insert(table, ("SEASON",), seasons)])
        self._lines.append(f"season numbers {FIRST_SEASON}-{LAST_SEASON} in {table}: filled")

    def _create_views(self) -> None:
        names = metaddl.loader_view_names()
        for name in names:
            self._execute(metaddl.render_loader_view(name, self._namespace).strip())
        listed = ", ".join(name.upper() for name in names)
        self._lines.append(f"views in {self._namespace.meta_schema}: {listed} replaced")

    def _install_scripts(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        infra = self._namespace.infra_schema
        available = lua_bundle.available_scripts(self._options.lua_dir)
        installed: list[str] = []
        for script in lua_bundle.SCRIPTS:
            where = f"{infra}.{script.name}"
            if script not in available:
                entry = self._options.lua_dir / script.entry_file
                self._lines.append(f"Lua script {where}: skipped, {entry.name} does not exist yet")
                continue
            try:
                text = lua_bundle.build_script(script, self._namespace, self._options.lua_dir)
            except BundleError as error:
                raise DeployError(str(error)) from None
            self._execute(text)
            installed.append(script.name)
            self._lines.append(f"Lua script {where}: replaced")
        skipped = tuple(s.name for s in lua_bundle.SCRIPTS if s.name not in installed)
        return tuple(installed), skipped

    def _report_orphans(self) -> dict[str, int]:
        meta = self._namespace.meta_schema
        rows = self._query(
            f'SELECT "TABLE_NAME", COUNT(*) FROM "{meta}"."LOAD_ORPHANS" GROUP BY 1 ORDER BY 1'
        )
        orphans = {str(table): int(count) for table, count in rows}
        if not orphans:
            self._lines.append("orphan slices: none")
            return orphans
        for table, count in orphans.items():
            self._warn(f"orphan slices of {table}: {count} (planned keys no longer match them)")
        self._warn(
            f"orphan slices block fetching their table: BACKFILL and REFRESH_WEEK refuse it "
            f"until they are migrated or deleted, see {meta}.LOAD_ORPHANS"
        )
        return orphans

    def _transaction(self, statements: Sequence[str]) -> None:
        switch = getattr(self._connection, "set_autocommit", None)
        if switch is not None:
            switch(False)
        try:
            for statement in statements:
                self._execute(statement)
            self._execute("COMMIT")
        except BaseException:
            self._execute("ROLLBACK")
            raise
        finally:
            if switch is not None:
                switch(True)

    def _warn(self, text: str) -> None:
        self._warnings.append(text)
        self._lines.append(f"warning: {text}")

    def _execute(self, sql: str) -> None:
        self._connection.execute(sql)

    def _query(self, sql: str) -> list[Any]:
        return list(self._connection.execute(sql).fetchall())


_PLAN_COLUMNS = (
    "ENTRY_ID",
    "TABLE_NAME",
    "PROFILE_NAME",
    "PROFILE_RANK",
    "PRIORITY",
    "SLICING",
    "REFRESH_CLASS",
    "SEASON_FROM",
    "SEASON_TO",
    "FIXED_FILTER",
    "SLICE_TEMPLATE",
    "GATED_BY",
    "PLAN_HASH",
)


def _plan_rows(prepared: _Prepared) -> list[tuple[Any, ...]]:
    plan = prepared.plan
    return [
        (
            entry.entry_id,
            entry.table,
            entry.profile,
            entry.profile_rank,
            entry.priority,
            entry.slicing,
            entry.refresh_class,
            entry.seasons_from,
            entry.seasons_to,
            template.fixed_filter,
            template.template,
            template.gated_by,
            plan.plan_hash,
        )
        for entry, template in zip(plan.entries, prepared.templates, strict=True)
    ]


def _gap_rows(plan: LoadPlan) -> list[tuple[Any, ...]]:
    return [(entry.entry_id, season) for entry in plan.entries for season in sorted(entry.gaps)]


def _insert(table: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    values = ",\n".join(
        "  (" + ", ".join(sql_literal(value) for value in row) + ")" for row in rows
    )
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values}"
