"""Pushdown SQL builder: the SQL one virtual-table access becomes (stdlib only).

Every pushdown SQL starts with the `/*CFBD {manifest}*/` header (`plan.manifest_comment`), whose
keys are those of `plan.build_manifest` plus `sources`: the row sources the SQL reads
(`STORED`, and `LIVE` or `REPLAY` for the fetch). The body has one of four shapes:

- zero-row: a contradiction, or no source can contribute a row (`SELECT … FROM DUAL WHERE FALSE`);
- read-through: the stored branch `UNION ALL` the fetch branch;
- live-only: the fetch branch alone (volatile tables, `READ_THROUGH = FALSE`);
- stored-only: the stored branch alone, when the residual excludes every fetched row
  (`REQ_SOURCE = 'STORED'`); no fetch, so the caller plans it without a budget.

Invariants:
- Every select list has exactly the virtual table's columns, in catalog order, with the types of
  `sqlrender.column_sql_types`: the fetch branch through the `EMITS` clause, the stored branch by
  passing mirror columns through unchanged and casting its four overrides to the column type.
  Exasol rejects any other type (probe 1.4c), and nothing is converted implicitly.
- The outer WHERE applies the whole residual, because Exasol does not re-check pushed predicates
  (probe 1.4e). Parameter predicates hold by construction: rows echo their slice's parameters.
- A planned slice is served by exactly one branch: the stored branch takes the slices that have a
  final `LOAD_LOG` row, the fetch branch's `NOT EXISTS` drops exactly those, and both read the
  same snapshot within one statement.
- The fetch branch calls the `SET` UDF once for all slices, without GROUP BY ([18]). An empty SET
  input still yields one all-NULL row (verified on 2026.2), so the branch keeps only rows with a
  `REQ_SLICE`, which the UDF always sets.
- A fetch branch is only built for a plan that passed the call budget.
- Identifiers are always quoted and values are literals rendered by `sqlrender`.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Final

from vs.cfbd_vs import fetch
from vs.cfbd_vs.catalog import Table
from vs.cfbd_vs.filters import (
    And,
    ColumnRef,
    Comparison,
    Expr,
    InList,
    IsNotNull,
    IsNull,
    Literal,
    Not,
    Or,
    PushdownError,
)
from vs.cfbd_vs.naming import META_COLUMNS
from vs.cfbd_vs.plan import RequestPlan, build_manifest, manifest_comment
from vs.cfbd_vs.properties import Properties
from vs.cfbd_vs.sqlrender import (
    RenderError,
    cast_literal,
    column_sql_types,
    emits_clause,
    qualified_name,
    quote_identifier,
    render_conjunction,
    sql_literal,
    string_literal,
    zero_row_select,
)

FETCH_UDF = "CFBD_FETCH"
LOAD_LOG = "LOAD_LOG"
SOURCE_COLUMN = "REQ_SOURCE"
SLICE_COLUMN = "REQ_SLICE"
SOURCE_STORED = "STORED"
FETCH_SOURCES: Mapping[str, str] = {
    fetch.MODE_LIVE: fetch.SOURCE_LIVE,
    fetch.MODE_REPLAY: fetch.SOURCE_REPLAY,
}
MAX_SLICE_KEY_LENGTH = 2000
MESSAGE_KEY_LIMIT = 80
STORED_OVERRIDES: Mapping[str, Any] = {
    "REQ_SOURCE": SOURCE_STORED,
    "REQ_STATUS": None,
    "REQ_ATTEMPTS": 0,
    "REQ_MS": None,
}

Truth = bool | None
_ANY_TRUTH: Final = frozenset[Truth]({True, False, None})
_UNKNOWN: Final = object()

_VALUES_ALIAS = quote_identifier("R")
_LOG_ALIAS = quote_identifier("L")
_SLICE_KEY = quote_identifier("SLICE_KEY")
_TABLE_NAME = quote_identifier("TABLE_NAME")
_IS_FINAL = quote_identifier("IS_FINAL")
_VALUES_SLICE_KEY = f"{_VALUES_ALIAS}.{_SLICE_KEY}"


class SliceKeyError(PushdownError):
    """A planned slice key does not fit `REQ_SLICE`, `LOAD_LOG.SLICE_KEY` or the UDF argument."""


def row_sources(table: Table, props: Properties, residual: Sequence[Expr]) -> tuple[str, ...]:
    """The sources whose rows can reach the result: `STORED` and/or the fetch mode's source."""
    sources = []
    if props.read_through and table.read_through:
        sources.append(SOURCE_STORED)
    fetch_source = FETCH_SOURCES[props.fetch_mode]
    if not excludes_source(residual, fetch_source):
        sources.append(fetch_source)
    return tuple(sources)


def excludes_source(residual: Sequence[Expr], source: str) -> bool:
    """True when no row with `REQ_SOURCE = source` can satisfy every residual conjunct.

    Three-valued logic with `REQ_SOURCE` bound to `source` and every other column unknown. Any
    form it does not model counts as possibly TRUE, so a fetch is only pruned when its rows would
    certainly be filtered out.
    """
    return True not in _outcomes(And(tuple(residual)), source)


def contradiction_sql(table: Table, props: Properties) -> str:
    """The zero-row SQL of a contradictory filter: no request, no source read."""
    plan = RequestPlan(table=table.name, slice_keys=(), budget=None)
    return _header(table, plan, props, ()) + "\n" + zero_row_select(table)


def pushdown_sql(
    table: Table,
    plan: RequestPlan,
    residual: Sequence[Expr],
    props: Properties,
    *,
    catalog_hash: str,
) -> str:
    """The pushdown SQL of a planned access to `table`, residual re-applied in the outer WHERE.

    `plan.budget` must be set unless the residual prunes the fetch (stored-only queries).
    """
    if plan.table != table.name:
        raise ValueError(f"the plan is for table {plan.table}, not {table.name}")
    _require_meta_columns(table)
    _check_slice_keys(table, plan.slice_keys)
    sources = row_sources(table, props, residual)
    fetching = FETCH_SOURCES[props.fetch_mode] in sources
    if fetching and plan.budget is None:
        raise ValueError(
            f"{table.name}: a fetch branch needs a plan that was checked against the budget"
        )
    header = _header(table, plan, props, sources)
    if not sources or not plan.slice_keys:
        return header + "\n" + zero_row_select(table)
    stored = SOURCE_STORED in sources
    branches = []
    if stored:
        branches.append(_stored_branch(table, plan.slice_keys, props))
    if fetching:
        fetch_rows = _fetch_call(table, plan.slice_keys, props, catalog_hash)
        if stored:
            fetch_rows += "\n" + _not_stored(table, props)
        branches.append(_without_empty_group_row(fetch_rows))
    body = "\nUNION ALL\n".join(branches)
    return f"{header}\nSELECT * FROM (\n{body}\n) WHERE {render_conjunction(residual)}"


def _header(table: Table, plan: RequestPlan, props: Properties, sources: Sequence[str]) -> str:
    read_through = props.read_through and table.read_through
    manifest = build_manifest(plan, mode=props.fetch_mode, read_through=read_through)
    manifest["sources"] = list(sources)
    return manifest_comment(manifest)


def _stored_branch(table: Table, slice_keys: Sequence[str], props: Properties) -> str:
    items = []
    for name, sql_type in column_sql_types(table):
        if name in STORED_OVERRIDES:
            items.append(
                f"{cast_literal(STORED_OVERRIDES[name], sql_type)} AS {quote_identifier(name)}"
            )
        else:
            items.append(quote_identifier(name))
    slice_column = quote_identifier(SLICE_COLUMN)
    keys = ", ".join(string_literal(key) for key in slice_keys)
    final_slices = (
        f"SELECT {_SLICE_KEY} FROM {_load_log(props)} "
        f"WHERE {_TABLE_NAME} = {string_literal(table.name)} AND {_IS_FINAL} = TRUE"
    )
    return (
        f"SELECT {', '.join(items)}\n"
        f"FROM {qualified_name(props.core_schema, table.name)}\n"
        f"WHERE {slice_column} IN ({keys})\n"
        f"AND {slice_column} IN ({final_slices})"
    )


def _fetch_call(
    table: Table, slice_keys: Sequence[str], props: Properties, catalog_hash: str
) -> str:
    arguments = (
        string_literal(table.name),
        _VALUES_SLICE_KEY,
        string_literal(catalog_hash),
        string_literal(props.fetch_mode),
        string_literal(props.connection_name),
        string_literal(props.replay_path),
        string_literal(props.doh_url),
        sql_literal(props.max_parallel_fetches),
    )
    udf = qualified_name(props.infra_schema, FETCH_UDF)
    values = ", ".join(f"({string_literal(key)})" for key in slice_keys)
    return (
        f"SELECT {udf}({', '.join(arguments)}) {emits_clause(table)}\n"
        f"FROM (VALUES {values}) AS {_VALUES_ALIAS}({_SLICE_KEY})"
    )


def _not_stored(table: Table, props: Properties) -> str:
    return (
        f"WHERE NOT EXISTS (SELECT 1 FROM {_load_log(props)} {_LOG_ALIAS} "
        f"WHERE {_LOG_ALIAS}.{_TABLE_NAME} = {string_literal(table.name)} "
        f"AND {_LOG_ALIAS}.{_SLICE_KEY} = {_VALUES_SLICE_KEY} AND {_LOG_ALIAS}.{_IS_FINAL} = TRUE)"
    )


def _without_empty_group_row(fetch_rows: str) -> str:
    return f"SELECT * FROM (\n{fetch_rows}\n) WHERE {quote_identifier(SLICE_COLUMN)} IS NOT NULL"


def _load_log(props: Properties) -> str:
    return qualified_name(props.meta_schema, LOAD_LOG)


def _require_meta_columns(table: Table) -> None:
    present = {column.name for column in table.columns}
    missing = [name for name in META_COLUMNS if name not in present]
    if missing:
        raise RenderError(f"table {table.name} lacks request metadata columns {', '.join(missing)}")


def _check_slice_keys(table: Table, slice_keys: Iterable[str]) -> None:
    for key in slice_keys:
        if len(key) > MAX_SLICE_KEY_LENGTH:
            shown = key[:MESSAGE_KEY_LIMIT] + "..."
            raise SliceKeyError(
                table.name,
                f"the request {shown} is {len(key):,} characters long, but slice keys are "
                f"limited to {MAX_SLICE_KEY_LENGTH} characters (REQ_SLICE); "
                "use shorter parameter values",
            )


def _outcomes(expr: Expr, source: str) -> frozenset[Truth]:
    if isinstance(expr, And):
        return _fold(expr.items, source, _and3, True)
    if isinstance(expr, Or):
        return _fold(expr.items, source, _or3, False)
    if isinstance(expr, Not):
        return frozenset(_not3(value) for value in _outcomes(expr.item, source))
    if isinstance(expr, Literal) and expr.type in ("bool", "null"):
        return frozenset({expr.value if isinstance(expr.value, bool) else None})
    if isinstance(expr, Comparison) and expr.op in ("=", "<>"):
        equal = _equals(_value(expr.left, source), _value(expr.right, source))
        return equal if expr.op == "=" else frozenset(_not3(value) for value in equal)
    if isinstance(expr, InList):
        candidate = _value(expr.expr, source)
        outcomes = frozenset[Truth]({False})
        for item in expr.items:
            outcomes = _combine(outcomes, _equals(candidate, _value(item, source)), _or3)
        return outcomes
    if isinstance(expr, (IsNull, IsNotNull)):
        value = _value(expr.expr, source)
        if value is _UNKNOWN:
            return _ANY_TRUTH
        return frozenset({(value is None) == isinstance(expr, IsNull)})
    return _ANY_TRUTH


def _fold(
    items: Sequence[Expr],
    source: str,
    operator: Callable[[Truth, Truth], Truth],
    neutral: bool,
) -> frozenset[Truth]:
    outcomes = frozenset[Truth]({neutral})
    for item in items:
        outcomes = _combine(outcomes, _outcomes(item, source), operator)
    return outcomes


def _combine(
    first: frozenset[Truth],
    second: frozenset[Truth],
    operator: Callable[[Truth, Truth], Truth],
) -> frozenset[Truth]:
    return frozenset(operator(a, b) for a, b in itertools.product(first, second))


def _value(node: Expr, source: str) -> object:
    if isinstance(node, ColumnRef):
        return source if node.name == SOURCE_COLUMN else _UNKNOWN
    if isinstance(node, Literal) and node.type == "null":
        return None
    if isinstance(node, Literal) and node.type == "string" and isinstance(node.value, str):
        return node.value or None
    return _UNKNOWN


def _equals(left: object, right: object) -> frozenset[Truth]:
    if left is _UNKNOWN or right is _UNKNOWN:
        return _ANY_TRUTH
    if left is None or right is None:
        return frozenset({None})
    if left == right:
        return frozenset({True})
    if str(left).rstrip(" ") == str(right).rstrip(" "):
        return _ANY_TRUTH
    return frozenset({False})


def _and3(first: Truth, second: Truth) -> Truth:
    if first is False or second is False:
        return False
    if first is None or second is None:
        return None
    return True


def _or3(first: Truth, second: Truth) -> Truth:
    if first is True or second is True:
        return True
    if first is None or second is None:
        return None
    return False


def _not3(value: Truth) -> Truth:
    return None if value is None else not value
