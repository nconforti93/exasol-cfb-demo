"""Entry of the CFBD virtual schema adapter `CFB_INFRA.CFBD_ADAPTER` (stdlib only).

Exasol calls `adapter_call(request)` with a JSON request of the Virtual Schema API and shows any
exception text to the user verbatim (probe 1.4a). Requests:

- `createVirtualSchema` / `refresh`: the table metadata of the embedded catalog, with column types
  from `sqlrender.column_sql_types` and the catalog comments. `createVirtualSchema` validates the
  properties; a refresh of `requestedTables` returns only those that exist and echoes the list.
- `setProperties`: validates the properties as they will be after the change and raises on any
  problem, so Exasol keeps the previous values. Properties do not change the metadata.
- `dropVirtualSchema`: always succeeds, so a broken virtual schema can still be dropped.
- `getCapabilities`: exactly `CAPABILITIES`, filters only (probe 1.4e; unknown names are silently
  ignored by Exasol, so the list is pinned by a unit test).
- `pushdown`: `pushdown_query`, the pipeline in the fixed order.

The bundler sets `CATALOG_JSON` and defines the script's `adapter_call` as this module's.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from vs.cfbd_vs import rules
from vs.cfbd_vs.catalog import Catalog, CatalogError, Table
from vs.cfbd_vs.constraints import SolvedFilter, solve
from vs.cfbd_vs.filters import PushdownError, parse_filter
from vs.cfbd_vs.plan import plan_requests
from vs.cfbd_vs.properties import Properties, merge_properties, parse_properties
from vs.cfbd_vs.pushdown_sql import (
    FETCH_SOURCES,
    contradiction_sql,
    excludes_source,
    pushdown_sql,
)
from vs.cfbd_vs.sqlrender import column_sql_types
from vs.cfbd_vs.sqltypes import SqlTypeError, parse_sql_type

ERROR_PREFIX = "CFBD_VS"
MAX_PLANNED_SLICES = 10_000
MAX_COMMENT_LENGTH = 2000

CAPABILITIES = (
    "FILTER_EXPRESSIONS",
    "FN_PRED_AND",
    "FN_PRED_OR",
    "FN_PRED_NOT",
    "FN_PRED_EQUAL",
    "FN_PRED_NOTEQUAL",
    "FN_PRED_LESS",
    "FN_PRED_LESSEQUAL",
    "FN_PRED_BETWEEN",
    "FN_PRED_IN_CONSTLIST",
    "FN_PRED_LIKE",
    "FN_PRED_LIKE_ESCAPE",
    "FN_PRED_IS_NULL",
    "FN_PRED_IS_NOT_NULL",
    "LITERAL_NULL",
    "LITERAL_BOOL",
    "LITERAL_DATE",
    "LITERAL_TIMESTAMP",
    "LITERAL_DOUBLE",
    "LITERAL_EXACTNUMERIC",
    "LITERAL_STRING",
)

CATALOG_JSON: str | None = None
_embedded_catalog: Catalog | None = None

_PLAIN_TYPES: Mapping[str, dict[str, Any]] = {
    "DOUBLE": {"type": "DOUBLE"},
    "BOOLEAN": {"type": "BOOLEAN"},
    "DATE": {"type": "DATE"},
    "TIMESTAMP": {"type": "TIMESTAMP", "withLocalTimeZone": False},
}


class AdapterError(ValueError):
    """A request the adapter cannot serve; the message starts with `CFBD_VS`."""


class PlanCeilingError(PushdownError):
    """A filter allows more requests than the adapter will ever plan for one access."""


def embedded_catalog() -> Catalog:
    """The catalog the bundler embedded in this script, parsed once per process."""
    global _embedded_catalog
    if _embedded_catalog is None:
        if CATALOG_JSON is None:
            raise AdapterError(
                f"{ERROR_PREFIX}: the adapter script has no embedded catalog; "
                "deploy it from a bundle built by vs/bundle.py"
            )
        _embedded_catalog = Catalog.from_json(CATALOG_JSON)
    return _embedded_catalog


def adapter_call(request: str) -> str:
    """Adapter script entry: one JSON request in, one JSON response out."""
    try:
        data = json.loads(request)
    except ValueError:
        raise AdapterError(f"{ERROR_PREFIX}: the adapter request is not valid JSON") from None
    return json.dumps(Adapter(embedded_catalog()).handle(data))


class Adapter:
    """The Virtual Schema API requests for one catalog."""

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._handlers: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
            "createVirtualSchema": self._create,
            "refresh": self._refresh,
            "setProperties": self._set_properties,
            "dropVirtualSchema": self._drop,
            "getCapabilities": self._capabilities,
            "pushdown": self._pushdown,
        }

    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Serve one decoded request and return the decoded response."""
        kind = request.get("type") if isinstance(request, Mapping) else None
        handler = self._handlers.get(kind) if isinstance(kind, str) else None
        if handler is None:
            raise AdapterError(f"{ERROR_PREFIX}: unsupported adapter request type {kind!r}")
        return handler(request)

    def _create(self, request: Mapping[str, Any]) -> dict[str, Any]:
        parse_properties(_schema_info(request).get("properties"))
        return {"type": "createVirtualSchema", "schemaMetadata": schema_metadata(self._tables())}

    def _refresh(self, request: Mapping[str, Any]) -> dict[str, Any]:
        requested = request.get("requestedTables")
        if requested is None:
            return {"type": "refresh", "schemaMetadata": schema_metadata(self._tables())}
        wanted = set(requested)
        tables = [table for table in self._tables() if table.name in wanted]
        return {
            "type": "refresh",
            "schemaMetadata": schema_metadata(tables),
            "requestedTables": requested,
        }

    def _set_properties(self, request: Mapping[str, Any]) -> dict[str, Any]:
        current = _schema_info(request).get("properties")
        parse_properties(merge_properties(current, request.get("properties")))
        return {"type": "setProperties"}

    def _drop(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return {"type": "dropVirtualSchema"}

    def _capabilities(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return {"type": "getCapabilities", "capabilities": list(CAPABILITIES)}

    def _pushdown(self, request: Mapping[str, Any]) -> dict[str, Any]:
        props = parse_properties(_schema_info(request).get("properties"))
        pushdown = request.get("pushdownRequest")
        if not isinstance(pushdown, Mapping):
            raise AdapterError(f"{ERROR_PREFIX}: pushdown request without pushdownRequest")
        source = pushdown.get("from")
        if not isinstance(source, Mapping) or source.get("type") != "table":
            kind = source.get("type") if isinstance(source, Mapping) else source
            raise AdapterError(
                f"{ERROR_PREFIX}: pushdown from {kind!r} is not supported, only from one table"
            )
        table = self._table(str(source.get("name")))
        sql = pushdown_query(
            table, pushdown.get("filter"), props, catalog_hash=self._catalog.catalog_hash
        )
        return {"type": "pushdown", "sql": sql}

    def _tables(self) -> list[Table]:
        return sorted(self._catalog.tables, key=lambda table: table.name)

    def _table(self, name: str) -> Table:
        try:
            return self._catalog.table(name)
        except CatalogError:
            raise PushdownError(
                name,
                "the adapter's catalog has no table of that name; the virtual schema metadata "
                "is out of date (ALTER VIRTUAL SCHEMA <schema> REFRESH)",
            ) from None


def pushdown_query(table: Table, filter_data: Any, props: Properties, *, catalog_hash: str) -> str:
    """The pushdown SQL of one access to `table`, checked in the fixed order.

    Parse and solve the filter (unsupported predicates first); a contradiction returns zero rows
    before anything else; the ceiling bounds the plan before any value set is materialized; then
    gating, explicit defaults and rules; then the expansion, with the call budget unless the
    residual prunes the fetch (stored-only queries); then the SQL.
    """
    solved = solve(parse_filter(filter_data, table), table)
    if solved.contradiction:
        return contradiction_sql(table, props)
    _check_ceiling(table, solved)
    evaluation = rules.evaluate(table, solved.value_sets(), props.api_features)
    stored_only = excludes_source(solved.residual, FETCH_SOURCES[props.fetch_mode])
    budget = None if stored_only else props.max_calls_per_query
    plan = plan_requests(table, evaluation.constraints, budget=budget)
    return pushdown_sql(table, plan, solved.residual, props, catalog_hash=catalog_hash)


def schema_metadata(tables: Iterable[Table]) -> dict[str, Any]:
    """The `schemaMetadata` of a create or refresh response."""
    return {"tables": [table_metadata(table) for table in tables]}


def table_metadata(table: Table) -> dict[str, Any]:
    """One table of `schemaMetadata`: typed columns in catalog order, with comments."""
    comments = {column.name: column.comment for column in table.columns}
    columns = []
    for name, sql_type in column_sql_types(table):
        column: dict[str, Any] = {"name": name, "dataType": data_type(sql_type)}
        if comments[name]:
            column["comment"] = comments[name]
        columns.append(column)
    return {
        "type": "table",
        "name": table.name,
        "comment": _table_comment(table),
        "columns": columns,
    }


def data_type(sql_type: str) -> dict[str, Any]:
    """The Virtual Schema API `dataType` of a catalog column type."""
    try:
        parsed = parse_sql_type(sql_type)
    except SqlTypeError:
        raise ValueError(
            f"{ERROR_PREFIX}: no virtual schema data type for {sql_type[:40]!r}"
        ) from None
    if parsed.kind in _PLAIN_TYPES:
        return dict(_PLAIN_TYPES[parsed.kind])
    if parsed.kind == "DECIMAL":
        return {"type": "DECIMAL", "precision": parsed.precision, "scale": parsed.scale}
    return {"type": "VARCHAR", "size": parsed.size, "characterSet": parsed.character_set}


def _schema_info(request: Mapping[str, Any]) -> Mapping[str, Any]:
    info = request.get("schemaMetadataInfo")
    return info if isinstance(info, Mapping) else {}


def _check_ceiling(table: Table, solved: SolvedFilter) -> None:
    count = solved.request_count()
    if count > MAX_PLANNED_SLICES:
        raise PlanCeilingError(
            table.name,
            f"the filter allows {count:,} requests, but the adapter plans at most "
            f"{MAX_PLANNED_SLICES:,} for one virtual-table access, stored-only queries included; "
            "use a narrower filter on the P_ columns",
        )


def _table_comment(table: Table) -> str:
    text = f"CFBD API GET {table.endpoint}"
    if table.kind == "child":
        text += f": elements of {'.'.join(table.array_path)} (child table of {table.parent})"
    if table.gated_by is not None:
        text += f"; needs the CFBD feature {table.gated_by} (property API_FEATURES)"
    if not table.read_through:
        text += "; always fetched, never read through from CFB_CORE"
    return text[:MAX_COMMENT_LENGTH]
