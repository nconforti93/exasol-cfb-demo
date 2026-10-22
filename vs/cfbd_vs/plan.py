"""Request planner: parameter value sets to canonical slice keys, budget and manifest.

The input is deliberately narrow: for each API parameter name, a finite list of literal values
(the output of the constraint solver, with explicit defaults already applied). Nothing here
reads the filter or the rules, so the planner can be tested on its own.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from vs.cfbd_vs.catalog import Param, Table

ParamValue = int | float | bool | str

ERROR_PREFIX = "CFBD_VS"
MANIFEST_TAG = "CFBD"
SQL_COMMENT_CLOSE = "*/"
ESCAPED_SQL_COMMENT_CLOSE = "*\\/"
DOT_SEGMENTS = (".", "..")
UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class PlanError(ValueError):
    """The solved parameter values cannot be turned into requests."""


class BudgetError(PlanError):
    """More requests are planned than the call budget allows."""


@dataclass(frozen=True)
class RequestPlan:
    """The ordered, de-duplicated slice keys of one virtual-table access."""

    table: str
    slice_keys: tuple[str, ...]
    budget: int | None


def percent_encode(text: str) -> str:
    """Encode per RFC 3986: unreserved characters stay, every other UTF-8 byte becomes %XX."""
    return "".join(
        char if char in UNRESERVED else "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))
        for char in text
    )


def render_value(param: Param, value: ParamValue) -> str:
    """Render one value canonically for its parameter type, before percent-encoding."""
    if param.type == "boolean":
        return _render_boolean(param, value)
    if param.type == "integer":
        return _render_integer(param, value)
    if param.type == "number":
        return _render_number(param, value)
    return _render_string(param, value)


def build_slice_key(table: Table, values: Mapping[str, ParamValue]) -> str:
    """Return the canonical slice key for one request: one value per sent parameter."""
    params = {param.name: param for param in table.params}
    path = table.endpoint
    query: list[tuple[str, str]] = []
    for name in sorted(values):
        param = _known_param(table, params, name)
        encoded = percent_encode(render_value(param, values[name]))
        if param.location == "path":
            path = _substitute(table, path, param, encoded)
        else:
            query.append((name, encoded))
    _require_no_open_placeholder(table, path)
    if not query:
        return path
    return path + "?" + "&".join(f"{name}={encoded}" for name, encoded in query)


def plan_requests(
    table: Table,
    values: Mapping[str, Collection[ParamValue] | None],
    *,
    budget: int | None,
) -> RequestPlan:
    """Expand the value sets into the cartesian product of requests and check the budget.

    Parameters follow the table's parameter order, the first one varying slowest. Duplicate
    values collapse. `budget=None` skips the check, for queries restricted to stored data.
    """
    chosen = _distinct_values(table, values)
    count = math.prod(len(options) for _, options in chosen)
    if budget is not None and count > budget:
        raise BudgetError(
            f"{ERROR_PREFIX}.{table.name}: {count} requests are planned but MAX_CALLS_PER_QUERY "
            f"is {budget}. Use a narrower filter or raise MAX_CALLS_PER_QUERY."
        )
    names = [name for name, _ in chosen]
    keys = (
        build_slice_key(table, dict(zip(names, combination, strict=True)))
        for combination in itertools.product(*(options for _, options in chosen))
    )
    return RequestPlan(table=table.name, slice_keys=tuple(dict.fromkeys(keys)), budget=budget)


def build_manifest(plan: RequestPlan, *, mode: str, read_through: bool) -> dict[str, Any]:
    """The machine-readable summary of what a query will request."""
    return {
        "table": plan.table,
        "fetch_mode": mode,
        "read_through": read_through,
        "budget": plan.budget,
        "planned_requests": len(plan.slice_keys),
        "slice_keys": list(plan.slice_keys),
    }


def manifest_json(manifest: Mapping[str, Any]) -> str:
    """Compact JSON with sorted keys that can never close an SQL block comment."""
    text = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text.replace(SQL_COMMENT_CLOSE, ESCAPED_SQL_COMMENT_CLOSE)


def manifest_comment(manifest: Mapping[str, Any]) -> str:
    """The `/*CFBD {…}*/` header that leads the pushdown SQL."""
    return f"/*{MANIFEST_TAG} {manifest_json(manifest)}*/"


def _distinct_values(
    table: Table, values: Mapping[str, Collection[ParamValue] | None]
) -> list[tuple[str, Collection[ParamValue]]]:
    params = {param.name: param for param in table.params}
    for name in values:
        _known_param(table, params, name)
    chosen: list[tuple[str, Collection[ParamValue]]] = []
    for param in table.params:
        options = values.get(param.name)
        if options is None:
            continue
        chosen.append((param.name, _dedupe(table, param, options)))
    return chosen


def _dedupe(table: Table, param: Param, options: Collection[ParamValue]) -> Collection[ParamValue]:
    if not options:
        raise PlanError(
            f"{ERROR_PREFIX}.{table.name}: parameter {param.name} has no values to request"
        )
    if isinstance(options, range):
        render_value(param, options[0])
        return options
    distinct: dict[str, ParamValue] = {}
    for value in _ordered(options):
        distinct.setdefault(render_value(param, value), value)
    return list(distinct.values())


def _ordered(options: Collection[ParamValue]) -> list[ParamValue]:
    if isinstance(options, (set, frozenset)):
        try:
            return sorted(options)
        except TypeError:
            return sorted(options, key=repr)
    return list(options)


def _known_param(table: Table, params: Mapping[str, Param], name: str) -> Param:
    if name not in params:
        raise PlanError(f"{ERROR_PREFIX}.{table.name}: unknown parameter {name}")
    return params[name]


def _substitute(table: Table, path: str, param: Param, encoded: str) -> str:
    placeholder = "{" + param.name + "}"
    if placeholder not in path:
        raise PlanError(
            f"{ERROR_PREFIX}.{table.name}: path parameter {param.name} "
            f"has no placeholder in {table.endpoint}"
        )
    if encoded in DOT_SEGMENTS:
        raise PlanError(
            f"{ERROR_PREFIX}.{table.name}: path parameter {param.name} must not be {encoded!r}"
        )
    return path.replace(placeholder, encoded)


def _require_no_open_placeholder(table: Table, path: str) -> None:
    start = path.find("{")
    if start >= 0:
        name = path[start + 1 : path.find("}", start)]
        raise PlanError(f"{ERROR_PREFIX}.{table.name}: no value for path parameter {name}")


def _mismatch(param: Param, value: object) -> PlanError:
    return PlanError(
        f"parameter {param.name} is {param.type}, got {type(value).__name__} value {value!r}"
    )


def _render_boolean(param: Param, value: object) -> str:
    if not isinstance(value, bool):
        raise _mismatch(param, value)
    return "true" if value else "false"


def _render_integer(param: Param, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _mismatch(param, value)
    if isinstance(value, float) and not (math.isfinite(value) and value.is_integer()):
        raise _mismatch(param, value)
    return str(int(value))


def _render_number(param: Param, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _mismatch(param, value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _mismatch(param, value)
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def _render_string(param: Param, value: object) -> str:
    if not isinstance(value, str):
        raise _mismatch(param, value)
    return value
