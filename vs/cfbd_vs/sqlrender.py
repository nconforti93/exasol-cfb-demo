"""Injection-proof SQL text for the pushdown SQL (stdlib only).

- Identifiers are always double-quoted, with embedded double quotes doubled.
- Values become typed literals rendered from validated Python values, never from request text:
  strings are single-quoted with embedded quotes doubled, numbers are rendered from finite
  Decimals, and negative numbers are parenthesized, so no `--` can form.
- Text that cannot travel safely (NUL, lone surrogates) is refused, as are unknown literal types,
  operators and node types.
- Compound predicates are fully parenthesized, so the SQL has exactly the structure of the tree.
- Column types come from the catalog and must match a whitelist grammar. Select-list literals
  are cast to the exact column type (`cast_literal`), because Exasol rejects pushdown output
  types that differ from the virtual table's (probe 1.4).

Column types are resolved here (`column_sql_types`) for the `EMITS` clause and the zero-row
SELECT. The `CFB_CORE` mirror DDL must use the same function, so stored and live types match.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from vs.cfbd_vs.catalog import Column, Param, Table
from vs.cfbd_vs.filters import (
    DATE_PATTERN,
    TIMESTAMP_PATTERN,
    And,
    Between,
    ColumnRef,
    Comparison,
    Expr,
    InList,
    IsNotNull,
    IsNull,
    Like,
    Literal,
    Not,
    Or,
    check_text,
)
from vs.cfbd_vs.naming import DECIMAL_TYPE, JSON_TYPE, META_COLUMNS
from vs.cfbd_vs.sqltypes import SqlTypeError, parse_sql_type

MAX_IDENTIFIER_LENGTH = 128
COMPARISON_OPERATORS = frozenset({"=", "<>", "<", "<="})
DEFAULT_COLUMN_TYPES: Mapping[str, str] = {"position": DECIMAL_TYPE, "raw_json": JSON_TYPE}


class RenderError(ValueError):
    """A value, identifier, type or tree cannot be rendered safely."""


def quote_identifier(name: str) -> str:
    """Render `name` as a delimited identifier."""
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_IDENTIFIER_LENGTH:
        raise RenderError(f"identifier must have 1 to {MAX_IDENTIFIER_LENGTH} characters")
    _check_text(name, "identifier")
    return '"' + name.replace('"', '""') + '"'


def qualified_name(*parts: str) -> str:
    """Render a dotted name such as `"CFB_INFRA"."CFBD_FETCH"`."""
    if not parts:
        raise RenderError("a qualified name needs at least one part")
    return ".".join(quote_identifier(part) for part in parts)


def string_literal(value: str) -> str:
    """Render `value` as a single string literal."""
    if not isinstance(value, str):
        raise RenderError(f"string literal value must be a str, got {type(value).__name__}")
    _check_text(value, "string literal")
    return "'" + value.replace("'", "''") + "'"


def sql_literal(value: Any) -> str:
    """Render a Python value (None, bool, int, finite Decimal, str) as an SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return _signed(str(value))
    if isinstance(value, Decimal):
        return _exact_numeric(value)
    if isinstance(value, str):
        return string_literal(value)
    raise RenderError(f"cannot render a {type(value).__name__} value as an SQL literal")


def cast_literal(value: Any, sql_type: str) -> str:
    """`CAST(<literal> AS <type>)`: a value typed exactly as a virtual-table column.

    Exasol rejects pushdown SQL whose output types are narrower or wider than the virtual
    table's, so every literal in a select list (zero-row SELECT, stored branch) goes through here.
    """
    return f"CAST({sql_literal(value)} AS {check_sql_type(sql_type)})"


def render_literal(literal: Literal) -> str:
    """Render a filter literal with its own SQL type."""
    renderer = _LITERAL_RENDERERS.get(literal.type)
    if renderer is None:
        raise RenderError(f"cannot render a literal of type {literal.type!r}")
    return renderer(literal.value)


def render_predicate(expr: Expr) -> str:
    """Render a filter tree as a fully parenthesized SQL expression."""
    if isinstance(expr, ColumnRef):
        return quote_identifier(expr.name)
    if isinstance(expr, Literal):
        return render_literal(expr)
    if isinstance(expr, And):
        return _junction("AND", expr.items)
    if isinstance(expr, Or):
        return _junction("OR", expr.items)
    if isinstance(expr, Not):
        return f"(NOT {render_predicate(expr.item)})"
    if isinstance(expr, Comparison):
        if expr.op not in COMPARISON_OPERATORS:
            raise RenderError(f"unsupported comparison operator {expr.op[:20]!r}")
        return f"({render_predicate(expr.left)} {expr.op} {render_predicate(expr.right)})"
    if isinstance(expr, Like):
        escape = "" if expr.escape is None else f" ESCAPE {render_predicate(expr.escape)}"
        return f"({render_predicate(expr.expr)} LIKE {render_predicate(expr.pattern)}{escape})"
    if isinstance(expr, Between):
        bounds = f"{render_predicate(expr.low)} AND {render_predicate(expr.high)}"
        return f"({render_predicate(expr.expr)} BETWEEN {bounds})"
    if isinstance(expr, InList):
        if not expr.items:
            raise RenderError("an IN list needs at least one item")
        items = ", ".join(render_predicate(item) for item in expr.items)
        return f"({render_predicate(expr.expr)} IN ({items}))"
    if isinstance(expr, IsNull):
        return f"({render_predicate(expr.expr)} IS NULL)"
    if isinstance(expr, IsNotNull):
        return f"({render_predicate(expr.expr)} IS NOT NULL)"
    raise RenderError(f"cannot render a {type(expr).__name__} as a predicate")


def render_conjunction(exprs: Sequence[Expr]) -> str:
    """Render residual conjuncts AND-ed together; `TRUE` when there are none."""
    if not exprs:
        return "TRUE"
    return " AND ".join(render_predicate(expr) for expr in exprs)


def check_sql_type(sql_type: str) -> str:
    """Return `sql_type` if it is a supported Exasol column type, else raise."""
    try:
        parse_sql_type(sql_type)
    except SqlTypeError as error:
        raise RenderError(str(error)) from None
    return sql_type


def column_sql_types(table: Table) -> tuple[tuple[str, str], ...]:
    """The `(name, SQL type)` of every column of `table`, in catalog order.

    Explicit catalog types win. Otherwise position columns are DECIMAL(18,0), RAW_JSON is the JSON
    VARCHAR, `P_` columns take their parameter's type and `REQ_` columns the fixed meta types.
    Explicit types of `P_` and `REQ_` columns must agree with those.
    """
    params = {param.column: param for param in table.params}
    return tuple(
        (column.name, check_sql_type(_column_type(table, column, params)))
        for column in table.columns
    )


def emits_clause(table: Table) -> str:
    """The typed `EMITS (...)` clause of the fetch UDF call for `table`."""
    columns = _typed_columns(table)
    return "EMITS (" + ", ".join(f"{quote_identifier(n)} {t}" for n, t in columns) + ")"


def zero_row_select(table: Table) -> str:
    """A SELECT with the columns and types of `table` that returns no rows and calls nothing."""
    columns = _typed_columns(table)
    select_list = ", ".join(f"{cast_literal(None, t)} AS {quote_identifier(n)}" for n, t in columns)
    return f"SELECT {select_list} FROM DUAL WHERE FALSE"


def _check_text(value: str, what: str) -> None:
    problem = check_text(value)
    if problem is not None:
        raise RenderError(f"{what} {problem}")


def _signed(digits: str) -> str:
    return f"({digits})" if digits.startswith("-") else digits


def _finite_decimal(value: Any, literal_type: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise RenderError(f"{literal_type} literal value must be a finite Decimal")
    return value


def _exact_numeric(value: Any) -> str:
    return _signed(format(_finite_decimal(value, "exactnumeric"), "f"))


def _double(value: Any) -> str:
    number = _finite_decimal(value, "double")
    coefficient = "".join(str(digit) for digit in number.as_tuple().digits).lstrip("0")
    if not coefficient:
        return "0E0"
    mantissa = coefficient[0] + (f".{coefficient[1:]}" if len(coefficient) > 1 else "")
    text = f"{mantissa}E{number.adjusted()}"
    return f"(-{text})" if number.is_signed() else text


def _null(value: Any) -> str:
    if value is not None:
        raise RenderError("null literal must have no value")
    return "NULL"


def _boolean(value: Any) -> str:
    if not isinstance(value, bool):
        raise RenderError("bool literal value must be a bool")
    return "TRUE" if value else "FALSE"


def _typed_text(keyword: str, pattern: re.Pattern[str]) -> Callable[[Any], str]:
    def render(value: Any) -> str:
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise RenderError(f"malformed {keyword.lower()} literal {str(value)[:40]!r}")
        return f"{keyword} {string_literal(value)}"

    return render


_LITERAL_RENDERERS: Mapping[str, Callable[[Any], str]] = {
    "null": _null,
    "bool": _boolean,
    "exactnumeric": _exact_numeric,
    "double": _double,
    "string": string_literal,
    "date": _typed_text("DATE", DATE_PATTERN),
    "timestamp": _typed_text("TIMESTAMP", TIMESTAMP_PATTERN),
}


def _junction(keyword: str, items: Sequence[Expr]) -> str:
    if not items:
        raise RenderError(f"{keyword} needs at least one operand")
    if len(items) == 1:
        return render_predicate(items[0])
    return "(" + f" {keyword} ".join(render_predicate(item) for item in items) + ")"


def _typed_columns(table: Table) -> tuple[tuple[str, str], ...]:
    columns = column_sql_types(table)
    if not columns:
        raise RenderError(f"table {table.name} has no columns")
    return columns


def _column_type(table: Table, column: Column, params: Mapping[str, Param]) -> str:
    if column.kind == "param":
        param = params.get(column.name)
        if param is None:
            raise RenderError(f"{table.name}.{column.name}: no parameter for this column")
        expected = param.sql_type
    elif column.kind == "meta":
        if column.name not in META_COLUMNS:
            raise RenderError(f"{table.name}.{column.name}: unknown request metadata column")
        expected = META_COLUMNS[column.name]
    else:
        resolved = column.sql_type or DEFAULT_COLUMN_TYPES.get(column.kind)
        if resolved is None:
            raise RenderError(f"{table.name}.{column.name}: column has no SQL type")
        return resolved
    if column.sql_type is not None and column.sql_type != expected:
        raise RenderError(
            f"{table.name}.{column.name}: catalog type {column.sql_type[:40]!r} "
            f"contradicts {expected!r}"
        )
    return expected
