"""Deploy the CFBD virtual schema into one namespace: schemas, tables, scripts, virtual schema.

`deploy(connection, namespace, options)` takes any SQL connection, so `make deploy` passes a `sys`
connection (the real deployment is owned by `sys`, [20]) and the integration harness passes the
connection of its test owner. Every name comes from the `Namespace`. The steps, in order:

1. `CREATE SCHEMA IF NOT EXISTS` for the infra, meta and core schemas.
2. The `CFB_META` tables from `sql/meta` (`metaddl.render_meta_ddl`), then the `CFB_CORE` mirrors
   from `sql/core/cfb_core_mirrors.sql` with the schema substituted. A table that exists must
   match its DDL exactly; otherwise the deploy stops with the column diff and alters nothing.
3. The adapter script: `CREATE OR REPLACE` every time.
4. The fetch UDF: created when missing, replaced only when its body changed (or when forced).
   `CREATE OR REPLACE` drops the script-bound connection grant even for identical text (probe
   1.4o), so an unchanged body keeps the grant. The comparison uses the text after the first
   ` AS\\n`, because Exasol stores a normalized header.
5. The virtual schema: created, or its changed properties are set; then refreshed.
6. `API_FEATURES`: read from `<vs>.INFO` (free, no data calls) when the fetch UDF can read the
   connection and the API answers; otherwise the property is left as it is (empty means fail
   closed) and the reason is reported.

All SQL is built from validated identifiers and escaped literals and sent without parameters, so
the large script bodies are never run through a format string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from vs import bundle
from vs.cfbd_vs.catalog import Catalog
from vs.cfbd_vs.properties import PropertyError, parse_properties
from vs.cfbd_vs.rules import KNOWN_FEATURES
from vs.cfbd_vs.sqlrender import string_literal
from vs.cfbd_vs.sqltypes import normalize_sql_type
from vs.host.metaddl import meta_ddl_names, render_meta_ddl
from vs.host.namespace import Namespace, NamespaceError

ROOT = Path(__file__).resolve().parents[2]
MIRRORS_PATH = ROOT / "sql" / "core" / "cfb_core_mirrors.sql"
MIRROR_SCHEMA = "CFB_CORE"
INFO_TABLE = "INFO"
API_FEATURES_PROPERTY = "API_FEATURES"

_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*")
_CREATE_TABLE = re.compile(r"CREATE TABLE ([A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*) \((.*?)\n\)", re.S)
_MIRROR_HEADER = re.compile(r"\(catalog ([0-9a-f]+)\)")
_CAUSE_LINE = re.compile(r"^[\w.]*(?:Error|Exception): ")
_NOT_NULL = " NOT NULL"

DNS_CHECK_HINT = "run `make dns-check`"
GRANTS_HINT = "run `make grants`"


class DeployError(RuntimeError):
    """The deployment cannot continue; the message says why and what to do."""


class Connection(Protocol):
    """The part of a pyexasol connection used here; `execute` returns a statement with rows."""

    def execute(self, query: str) -> Any: ...


@dataclass(frozen=True)
class DeployOptions:
    """Inputs of one deployment; everything defaults to the real artifacts of the repository."""

    connection_name: str | None = None
    properties: Mapping[str, str] = field(default_factory=dict)
    sync_api_features: bool = True
    force_fetch_replace: bool = False
    catalog_text: str | None = None
    built: bundle.Bundle | None = None
    mirrors_sql: str | None = None


@dataclass(frozen=True)
class DeployReport:
    """What a deployment did, as printable lines, plus what the caller may need to follow up."""

    lines: tuple[str, ...]
    fetch_udf: str
    virtual_schema: str
    api_features: tuple[str, ...] | None
    next_steps: tuple[str, ...]


@dataclass(frozen=True)
class TableDdl:
    """A `CREATE TABLE` statement and the columns it defines."""

    schema: str
    name: str
    statement: str
    columns: tuple[tuple[str, str, bool], ...]


def deploy(
    connection: Connection, namespace: Namespace, options: DeployOptions | None = None
) -> DeployReport:
    """Run every step for `namespace` and report; raises `DeployError` before any later step."""
    options = options or DeployOptions()
    catalog_text = options.catalog_text or bundle.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8")
    catalog = Catalog.from_json(catalog_text)
    built = options.built or bundle.build(catalog_text)
    mirrors_sql = options.mirrors_sql or MIRRORS_PATH.read_text(encoding="utf-8")
    properties = desired_properties(namespace, options)
    tables = meta_tables(namespace) + mirror_tables(mirrors_sql, catalog, namespace)
    return _Deployment(connection, namespace, options, catalog, built, properties, tables).run()


def desired_properties(namespace: Namespace, options: DeployOptions) -> dict[str, str]:
    """The virtual schema properties this deployment manages, validated like the adapter does."""
    properties = {
        "CONNECTION_NAME": options.connection_name or namespace.default_connection,
        "INFRA_SCHEMA": namespace.infra_schema,
        "META_SCHEMA": namespace.meta_schema,
        "CORE_SCHEMA": namespace.core_schema,
        **options.properties,
    }
    try:
        namespace.check_connection(properties["CONNECTION_NAME"])
    except NamespaceError as error:
        raise DeployError(str(error)) from None
    try:
        parse_properties(properties)
    except PropertyError as error:
        raise DeployError("invalid virtual schema properties:\n" + str(error)) from None
    return properties


def meta_tables(namespace: Namespace) -> list[TableDdl]:
    """The `CFB_META` tables, rendered for the namespace, in creation order."""
    return [
        parse_table_ddl(render_meta_ddl(name, namespace.meta_schema)) for name in meta_ddl_names()
    ]


def mirror_tables(mirrors_sql: str, catalog: Catalog, namespace: Namespace) -> list[TableDdl]:
    """The mirror tables of the committed DDL for the namespace; refuses a stale file."""
    header = _MIRROR_HEADER.search(mirrors_sql.split("\n", 1)[0])
    if header is None or header.group(1) != catalog.catalog_hash:
        found = "no catalog hash" if header is None else f"catalog {header.group(1)[:12]}"
        raise DeployError(
            f"sql/core/cfb_core_mirrors.sql has {found}, but the catalog is "
            f"{catalog.catalog_hash[:12]}; run `make generate` and review the diff"
        )
    tables = [
        parse_table_ddl(match.group(0).replace(f"{MIRROR_SCHEMA}.", f"{namespace.core_schema}.", 1))
        for match in _CREATE_TABLE.finditer(mirrors_sql)
    ]
    expected = sorted(table.name for table in catalog.tables if table.read_through)
    if sorted(table.name for table in tables) != expected:
        raise DeployError(
            "sql/core/cfb_core_mirrors.sql does not define exactly the read-through tables of "
            "the catalog; run `make generate`"
        )
    return tables


def parse_table_ddl(statement: str) -> TableDdl:
    """Schema, table and `(name, type, nullable)` columns of one `CREATE TABLE` statement."""
    match = _CREATE_TABLE.search(statement)
    if match is None:
        raise DeployError(f"cannot read table DDL: {statement[:80]!r}")
    columns = []
    for raw in match.group(3).splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.startswith("CONSTRAINT "):
            continue
        name, _, type_text = line.partition(" ")
        nullable = not type_text.endswith(_NOT_NULL)
        columns.append((name, normalize_sql_type(type_text.removesuffix(_NOT_NULL)), nullable))
    return TableDdl(
        match.group(1), match.group(2), statement.strip().removesuffix(";"), tuple(columns)
    )


def column_diff(expected: TableDdl, found: Sequence[tuple[str, str, bool]]) -> list[str]:
    """Differences between a table's DDL and the columns found in the database."""
    qualified = f"{expected.schema}.{expected.name}"
    wanted = {name: (type_text, nullable) for name, type_text, nullable in expected.columns}
    have = {name: (type_text, nullable) for name, type_text, nullable in found}
    diff = [f"{qualified}: column {name} is missing" for name in wanted if name not in have]
    diff += [f"{qualified}: unexpected column {name}" for name in have if name not in wanted]
    for name, (type_text, nullable) in wanted.items():
        if name in have and have[name] != (type_text, nullable):
            diff.append(
                f"{qualified}.{name}: expected {_describe(type_text, nullable)}, "
                f"found {_describe(*have[name])}"
            )
    if not diff and [c[0] for c in expected.columns] != [c[0] for c in found]:
        diff.append(f"{qualified}: the columns are in a different order")
    return diff


def _describe(type_text: str, nullable: bool) -> str:
    return type_text if nullable else type_text + _NOT_NULL


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise DeployError(f"{value!r} is not an UPPER_CASE identifier")
    return value


class _Deployment:
    def __init__(
        self,
        connection: Connection,
        namespace: Namespace,
        options: DeployOptions,
        catalog: Catalog,
        built: bundle.Bundle,
        properties: dict[str, str],
        tables: list[TableDdl],
    ) -> None:
        self._connection = connection
        self._namespace = namespace
        self._options = options
        self._catalog = catalog
        self._built = built
        self._properties = properties
        self._tables = tables
        self._lines: list[str] = []
        self._next_steps: list[str] = []
        self._applied: dict[str, str] = {}

    def run(self) -> DeployReport:
        for schema in (
            self._namespace.infra_schema,
            self._namespace.meta_schema,
            self._namespace.core_schema,
        ):
            self._execute(f"CREATE SCHEMA IF NOT EXISTS {_identifier(schema)}")
        self._lines.append(
            f"schemas {self._namespace.infra_schema}, {self._namespace.meta_schema}, "
            f"{self._namespace.core_schema}: ready"
        )
        self._ensure_tables(
            [t for t in self._tables if t.schema == self._namespace.meta_schema], "meta tables"
        )
        self._ensure_tables(
            [t for t in self._tables if t.schema == self._namespace.core_schema], "mirror tables"
        )
        self._deploy_adapter()
        fetch_udf = self._deploy_fetch()
        created = self._deploy_virtual_schema()
        features = self._sync_api_features()
        self._follow_ups(fetch_udf, created)
        return DeployReport(
            tuple(self._lines),
            fetch_udf,
            "created" if created else "refreshed",
            features,
            tuple(self._next_steps),
        )

    def _ensure_tables(self, tables: list[TableDdl], label: str) -> None:
        if not tables:
            return
        existing = self._columns_by_table(tables[0].schema)
        problems: list[str] = []
        missing: list[TableDdl] = []
        for table in tables:
            found = existing.get(table.name)
            if found is None:
                missing.append(table)
            else:
                problems += column_diff(table, found)
        if problems:
            raise DeployError(
                f"existing {label} do not match the catalog; nothing was altered. Differences:\n"
                + "\n".join(problems)
            )
        for table in missing:
            self._execute(table.statement)
        self._lines.append(
            f"{label} in {tables[0].schema}: {len(missing)} created, "
            f"{len(tables) - len(missing)} existing and matching"
        )

    def _columns_by_table(self, schema: str) -> dict[str, list[tuple[str, str, bool]]]:
        rows = self._query(
            "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_TYPE, COLUMN_IS_NULLABLE "
            "FROM EXA_ALL_COLUMNS "
            f"WHERE COLUMN_SCHEMA = {string_literal(_identifier(schema))} "
            "AND COLUMN_OBJECT_TYPE = 'TABLE' "
            "ORDER BY COLUMN_TABLE, COLUMN_ORDINAL_POSITION"
        )
        by_table: dict[str, list[tuple[str, str, bool]]] = {}
        for table, name, type_text, nullable in rows:
            by_table.setdefault(table, []).append(
                (name, normalize_sql_type(type_text), bool(nullable))
            )
        return by_table

    def _deploy_adapter(self) -> None:
        infra = self._namespace.infra_schema
        text = bundle.adapter_script(self._built.adapter_body, infra)
        self._execute(text)
        self._lines.append(f"adapter {infra}.{bundle.ADAPTER_SCRIPT_NAME}: replaced")

    def _deploy_fetch(self) -> str:
        infra = self._namespace.infra_schema
        name = f"{infra}.{bundle.FETCH_SCRIPT_NAME}"
        stored = self._script_text(infra, bundle.FETCH_SCRIPT_NAME)
        if stored is None:
            self._execute(bundle.fetch_script(self._built.fetch_body, infra))
            self._lines.append(f"fetch UDF {name}: created")
            return "created"
        unchanged = bundle.script_body(stored) == bundle.stored_body(self._built.fetch_body)
        if unchanged and not self._options.force_fetch_replace:
            self._lines.append(f"fetch UDF {name}: unchanged, kept as is (its grants stay)")
            return "unchanged"
        self._execute(bundle.fetch_script(self._built.fetch_body, infra, replace=True))
        reason = "forced" if unchanged else "body changed"
        self._lines.append(f"fetch UDF {name}: replaced ({reason})")
        return "replaced"

    def _script_text(self, schema: str, name: str) -> str | None:
        rows = self._query(
            "SELECT SCRIPT_TEXT FROM EXA_ALL_SCRIPTS "
            f"WHERE SCRIPT_SCHEMA = {string_literal(schema)} "
            f"AND SCRIPT_NAME = {string_literal(name)}"
        )
        return None if not rows else str(rows[0][0])

    def _deploy_virtual_schema(self) -> bool:
        vs = _identifier(self._namespace.vs_name)
        adapter = f"{self._namespace.infra_schema}.{bundle.ADAPTER_SCRIPT_NAME}"
        current = self._virtual_schema_properties(vs)
        self._applied = {**(current or {}), **self._properties}
        if current is None:
            self._execute(
                f"CREATE VIRTUAL SCHEMA {vs} USING {adapter} WITH {_assignments(self._properties)}"
            )
            self._lines.append(f"virtual schema {vs}: created with {_names(self._properties)}")
            return True
        changed = {k: v for k, v in self._properties.items() if current.get(k) != v}
        if changed:
            self._execute(f"ALTER VIRTUAL SCHEMA {vs} SET {_assignments(changed)}")
        self._execute(f"ALTER VIRTUAL SCHEMA {vs} REFRESH")
        self._lines.append(
            f"virtual schema {vs}: refreshed, properties set: {_names(changed) or 'none changed'}"
        )
        return False

    def _virtual_schema_properties(self, vs: str) -> dict[str, str] | None:
        exists = self._query(
            f"SELECT COUNT(*) FROM EXA_ALL_VIRTUAL_SCHEMAS WHERE SCHEMA_NAME = {string_literal(vs)}"
        )
        if int(exists[0][0]) == 0:
            return None
        rows = self._query(
            "SELECT PROPERTY_NAME, PROPERTY_VALUE FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES "
            f"WHERE SCHEMA_NAME = {string_literal(vs)}"
        )
        return dict(rows)

    def _sync_api_features(self) -> tuple[str, ...] | None:
        if not self._options.sync_api_features:
            self._lines.append("API_FEATURES: sync skipped, left as it is")
            return None
        connection = self._properties["CONNECTION_NAME"]
        if not self._connection_exists(connection):
            self._lines.append(
                f"API_FEATURES: left as it is (empty fails closed): connection {connection} "
                "does not exist; create it first, see docs/user-guide.md"
            )
            return None
        try:
            features = self._read_features()
        except DeployError as error:
            self._lines.append(f"API_FEATURES: left as it is (empty fails closed): {error}")
            return None
        value = ",".join(features)
        if value or API_FEATURES_PROPERTY in self._applied:
            self._execute(
                f"ALTER VIRTUAL SCHEMA {self._namespace.vs_name} "
                f"SET {API_FEATURES_PROPERTY} = {string_literal(value)}"
            )
        self._lines.append(
            f"API_FEATURES: synced from {self._namespace.vs_name}.INFO: {value or 'none'}"
        )
        return features

    def _read_features(self) -> tuple[str, ...]:
        info = self._catalog.table(INFO_TABLE)
        flags = [
            (column.name, column.path[1])
            for column in info.columns
            if column.kind == "value"
            and column.path is not None
            and len(column.path) == 2
            and column.path[0] == "features"
            and column.path[1] in KNOWN_FEATURES
        ]
        names = ", ".join(f'"{name}"' for name, _ in flags)
        try:
            rows = self._connection.execute(
                f'SELECT {names} FROM {self._namespace.vs_name}."{INFO_TABLE}"'
            ).fetchall()
        except Exception as error:
            raise DeployError(_failure_reason(error)) from None
        if len(rows) != 1:
            raise DeployError(f"/info returned {len(rows)} rows, expected 1")
        return tuple(
            sorted(feature for (_, feature), flag in zip(flags, rows[0], strict=True) if flag)
        )

    def _connection_exists(self, name: str) -> bool:
        rows = self._query(
            "SELECT COUNT(*) FROM EXA_ALL_CONNECTIONS "
            f"WHERE CONNECTION_NAME = {string_literal(name)}"
        )
        return int(rows[0][0]) > 0

    def _follow_ups(self, fetch_udf: str, created: bool) -> None:
        if fetch_udf != "unchanged" and not self._namespace.is_real:
            self._next_steps.append(
                f"the fetch UDF was {fetch_udf}; with a dedicated owner its script-bound "
                "connection grant must be (re)created: run "
                f'`make grants ARGS="--prefix {self._namespace.prefix}"`'
            )
        if created and self._namespace.is_real:
            self._next_steps.append(f"{GRANTS_HINT} to give {self._namespace.reader_role} SELECT")

    def _execute(self, sql: str) -> None:
        self._connection.execute(sql)

    def _query(self, sql: str) -> list[Any]:
        return list(self._connection.execute(sql).fetchall())


def _assignments(properties: Mapping[str, str]) -> str:
    return " ".join(f"{name} = {string_literal(value)}" for name, value in properties.items())


def _names(properties: Mapping[str, str]) -> str:
    return ", ".join(properties)


def _failure_reason(error: Exception) -> str:
    """The informative line of a database error, with a hint for the two fixable causes.

    A UDF failure arrives as a VM error and a traceback; the message that names the cause is on
    the last `...Error: ...` line.
    """
    message = str(getattr(error, "message", error)).strip()
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    causes = [line for line in lines if _CAUSE_LINE.match(line)]
    reason = _CAUSE_LINE.sub("", causes[-1], count=1) if causes else (lines[0] if lines else "")
    reason = reason or type(error).__name__
    lowered = message.lower()
    if _mentions_name_resolution(lowered) and "make dns-check" not in reason:
        reason = f"{reason} ({DNS_CHECK_HINT})"
    elif "connection" in lowered and "access" in lowered and "make grants" not in reason:
        reason = f"{reason} ({GRANTS_HINT})"
    return reason


def _mentions_name_resolution(lowered: str) -> bool:
    return any(word in lowered for word in ("dns-check", "name resolution", "cannot resolve"))
