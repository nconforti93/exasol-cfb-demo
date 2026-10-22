"""Synthetic catalog table and Exasol pushdown-filter JSON builders for the adapter-core tests."""

from __future__ import annotations

from typing import Any

from vs.cfbd_vs.catalog import Column, Param, Table
from vs.cfbd_vs.naming import META_COLUMNS

SEASON_TYPES = (
    "regular",
    "postseason",
    "both",
    "allstar",
    "spring_regular",
    "spring_postseason",
)

Node = dict[str, Any]


def make_param(
    name: str,
    column: str,
    param_type: str,
    sql_type: str,
    enum: tuple[str, ...] | None = None,
) -> Param:
    return Param(
        name=name,
        column=column,
        location="query",
        type=param_type,
        sql_type=sql_type,
        enum=enum,
    )


GAMES_PARAMS = (
    make_param("year", "P_YEAR", "integer", "DECIMAL(18,0)"),
    make_param("week", "P_WEEK", "integer", "DECIMAL(18,0)"),
    make_param("seasonType", "P_SEASON_TYPE", "string", "VARCHAR(2000) UTF8", SEASON_TYPES),
    make_param("team", "P_TEAM", "string", "VARCHAR(2000) UTF8"),
    make_param("latest", "P_LATEST", "boolean", "BOOLEAN"),
    make_param("threshold", "P_THRESHOLD", "number", "DOUBLE"),
)


def games_table(params: tuple[Param, ...] = GAMES_PARAMS) -> Table:
    columns = (
        Column(name="ROW_NO", kind="position"),
        Column(name="ID", kind="value", path=("id",), sql_type="DECIMAL(18,0)"),
        Column(name="SEASON", kind="value", path=("season",), sql_type="DECIMAL(18,0)"),
        Column(name="HOME_POINTS", kind="value", path=("homePoints",), sql_type="DECIMAL(18,0)"),
        Column(name="AWAY_POINTS", kind="value", path=("awayPoints",), sql_type="DECIMAL(18,0)"),
        Column(name="VENUE", kind="value", path=("venue",), sql_type="VARCHAR(2000) UTF8"),
        Column(name="START_DATE", kind="value", path=("startDate",), sql_type="TIMESTAMP"),
        Column(name="NEUTRAL_SITE", kind="value", path=("neutralSite",), sql_type="BOOLEAN"),
        Column(name="EXCITEMENT", kind="value", path=("excitement",), sql_type="DOUBLE"),
        *(Column(name=p.column, kind="param") for p in params),
        *(Column(name=name, kind="meta") for name in META_COLUMNS),
    )
    return Table(
        name="GAMES",
        endpoint="/games",
        kind="endpoint",
        parent=None,
        array_path=(),
        hoisted=False,
        read_through=True,
        gated_by=None,
        params=params,
        columns=columns,
        rules=(),
    )


def col(name: str) -> Node:
    return {"type": "column", "tableName": "GAMES", "columnNr": 0, "name": name}


def num(value: str | int) -> Node:
    return {"type": "literal_exactnumeric", "value": str(value)}


def dbl(value: str) -> Node:
    return {"type": "literal_double", "value": value}


def text(value: str) -> Node:
    return {"type": "literal_string", "value": value}


def boolean(value: bool) -> Node:
    return {"type": "literal_bool", "value": value}


def null() -> Node:
    return {"type": "literal_null"}


def date(value: str) -> Node:
    return {"type": "literal_date", "value": value}


def timestamp(value: str) -> Node:
    return {"type": "literal_timestamp", "value": value}


def _binary(kind: str, left: Node, right: Node) -> Node:
    return {"type": kind, "left": left, "right": right}


def eq(left: Node, right: Node) -> Node:
    return _binary("predicate_equal", left, right)


def ne(left: Node, right: Node) -> Node:
    return _binary("predicate_notequal", left, right)


def less(left: Node, right: Node) -> Node:
    return _binary("predicate_less", left, right)


def less_eq(left: Node, right: Node) -> Node:
    return _binary("predicate_lessequal", left, right)


def greater(left: Node, right: Node) -> Node:
    """Exasol sends `a > b` as `b < a`."""
    return less(right, left)


def greater_eq(left: Node, right: Node) -> Node:
    """Exasol sends `a >= b` as `b <= a`."""
    return less_eq(right, left)


def between(expr: Node, low: Node, high: Node) -> Node:
    return {"type": "predicate_between", "expression": expr, "left": low, "right": high}


def in_list(expr: Node, *items: Node) -> Node:
    return {"type": "predicate_in_constlist", "expression": expr, "arguments": list(items)}


def like(expr: Node, pattern: Node, escape: Node | None = None) -> Node:
    node: Node = {"type": "predicate_like", "expression": expr, "pattern": pattern}
    if escape is not None:
        node["escapeChar"] = escape
    return node


def is_null(expr: Node) -> Node:
    return {"type": "predicate_is_null", "expression": expr}


def is_not_null(expr: Node) -> Node:
    return {"type": "predicate_is_not_null", "expression": expr}


def and_(*items: Node) -> Node:
    return {"type": "predicate_and", "expressions": list(items)}


def or_(*items: Node) -> Node:
    return {"type": "predicate_or", "expressions": list(items)}


def not_(item: Node) -> Node:
    return {"type": "predicate_not", "expression": item}
