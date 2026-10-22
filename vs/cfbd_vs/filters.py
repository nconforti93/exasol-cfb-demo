"""Exasol pushdown filter JSON -> predicate AST (stdlib only).

The parser is closed-world: it accepts exactly the advertised predicates (`=`, `<>`, `<`, `<=`,
`BETWEEN`, `IN` with constants, `LIKE`, `IS [NOT] NULL`, `AND`, `OR`, `NOT`) and literal types
(null, bool, exact numeric, double, string, date, timestamp). Anything else, and any malformed or
unrepresentable value, is a `FilterError`. Literal values are validated and converted to Python
values here, so later stages never see raw request text. Columns resolve to their catalog kind.
Trees are at most `MAX_DEPTH` levels deep, so recursive consumers stay within the stack.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from vs.cfbd_vs.catalog import Param, Table

ERROR_SCHEMA = "CFBD_VS"
PARAM_KIND = "param"
MAX_DEPTH = 100
MAX_NUMERIC_EXPONENT = 400
MAX_NUMERIC_TEXT = 100

COMPARISON_OPERATORS: Mapping[str, str] = {
    "predicate_equal": "=",
    "predicate_notequal": "<>",
    "predicate_less": "<",
    "predicate_lessequal": "<=",
}
LITERAL_TYPES = ("null", "bool", "exactnumeric", "double", "string", "date", "timestamp")

_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?"
)
_BOOL_TEXT = {"true": True, "false": False}


class PushdownError(ValueError):
    """A plan-time rejection of a virtual-table access. The message starts with CFBD_VS.<TABLE>:."""

    def __init__(self, table: str, detail: str) -> None:
        super().__init__(f"{ERROR_SCHEMA}.{table}: {detail}")
        self.table = table
        self.detail = detail


class FilterError(PushdownError):
    """The pushdown filter is malformed or uses an element the adapter does not advertise."""


@dataclass(frozen=True)
class ColumnRef:
    """A column of the virtual table, with its catalog kind and, for `P_` columns, its parameter."""

    name: str
    kind: str
    param: Param | None = field(default=None, compare=False, repr=False)

    @property
    def is_param(self) -> bool:
        """True for a parameter echo column (`P_*`)."""
        return self.kind == PARAM_KIND


@dataclass(frozen=True)
class Literal:
    """A validated constant: None, bool, a finite Decimal, or str (string, date, timestamp)."""

    type: str
    value: bool | Decimal | str | None


@dataclass(frozen=True)
class And:
    items: tuple[Expr, ...]


@dataclass(frozen=True)
class Or:
    items: tuple[Expr, ...]


@dataclass(frozen=True)
class Not:
    item: Expr


@dataclass(frozen=True)
class Comparison:
    """`left op right` with op one of `=`, `<>`, `<`, `<=` (Exasol sends `>`/`>=` reversed)."""

    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Like:
    expr: Expr
    pattern: Expr
    escape: Expr | None


@dataclass(frozen=True)
class Between:
    expr: Expr
    low: Expr
    high: Expr


@dataclass(frozen=True)
class InList:
    expr: Expr
    items: tuple[Expr, ...]


@dataclass(frozen=True)
class IsNull:
    expr: Expr


@dataclass(frozen=True)
class IsNotNull:
    expr: Expr


Expr = (
    ColumnRef | Literal | And | Or | Not | Comparison | Like | Between | InList | IsNull | IsNotNull
)


def parse_filter(data: Any, table: Table) -> Expr | None:
    """Parse the `filter` element of a pushdown request for `table`; None means no filter."""
    if data is None:
        return None
    return _Parser(table).parse(data, 1)


def check_text(value: str) -> str | None:
    """Return why `value` cannot be passed on as SQL or request text, or None when it can."""
    if "\x00" in value:
        return "contains a NUL character"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "is not valid Unicode"
    return None


class _Parser:
    def __init__(self, table: Table) -> None:
        self._table = table
        self._columns = {column.name: column for column in table.columns}
        self._params = {param.column: param for param in table.params}

    def _error(self, detail: str) -> FilterError:
        return FilterError(self._table.name, detail)

    def parse(self, data: Any, depth: int) -> Expr:
        if depth > MAX_DEPTH:
            raise self._error(f"the filter is nested deeper than {MAX_DEPTH} levels")
        if not isinstance(data, Mapping):
            raise self._error("malformed filter: expected a JSON object")
        node_type = data.get("type")
        if not isinstance(node_type, str):
            raise self._error("malformed filter: element without a type")
        if node_type == "column":
            return self._column(data)
        if node_type.startswith("literal_"):
            return self._literal(data, node_type.removeprefix("literal_"))
        return self._predicate(data, node_type, depth)

    def _predicate(self, data: Mapping[str, Any], node_type: str, depth: int) -> Expr:
        child = depth + 1
        if node_type in COMPARISON_OPERATORS:
            return Comparison(
                COMPARISON_OPERATORS[node_type],
                self.parse(self._key(data, "left"), child),
                self.parse(self._key(data, "right"), child),
            )
        if node_type in ("predicate_and", "predicate_or"):
            items = self._items(data, "expressions", child)
            return And(items) if node_type == "predicate_and" else Or(items)
        if node_type == "predicate_not":
            return Not(self.parse(self._key(data, "expression"), child))
        if node_type == "predicate_between":
            return Between(
                self.parse(self._key(data, "expression"), child),
                self.parse(self._key(data, "left"), child),
                self.parse(self._key(data, "right"), child),
            )
        if node_type == "predicate_in_constlist":
            expr = self.parse(self._key(data, "expression"), child)
            return InList(expr, self._items(data, "arguments", child))
        if node_type == "predicate_like":
            escape = data.get("escapeChar")
            return Like(
                self.parse(self._key(data, "expression"), child),
                self.parse(self._key(data, "pattern"), child),
                None if escape is None else self.parse(escape, child),
            )
        if node_type in ("predicate_is_null", "predicate_is_not_null"):
            expr = self.parse(self._key(data, "expression"), child)
            return IsNull(expr) if node_type == "predicate_is_null" else IsNotNull(expr)
        raise self._error(f"the filter contains the unsupported element {node_type}")

    def _key(self, data: Mapping[str, Any], key: str) -> Any:
        if key not in data:
            raise self._error(f"malformed filter: {data['type']} without {key!r}")
        return data[key]

    def _items(self, data: Mapping[str, Any], key: str, depth: int) -> tuple[Expr, ...]:
        items = self._key(data, key)
        if not isinstance(items, list) or not items:
            raise self._error(f"malformed filter: {data['type']} needs a non-empty {key!r} list")
        return tuple(self.parse(item, depth) for item in items)

    def _column(self, data: Mapping[str, Any]) -> ColumnRef:
        name = data.get("name")
        if not isinstance(name, str):
            raise self._error("malformed filter: column reference without a name")
        column = self._columns.get(name)
        if column is None:
            raise self._error(f"the filter references the unknown column {name}")
        if column.kind != PARAM_KIND:
            return ColumnRef(name, column.kind)
        param = self._params.get(name)
        if param is None:
            raise self._error(f"catalog error: column {name} has no parameter")
        return ColumnRef(name, column.kind, param)

    def _literal(self, data: Mapping[str, Any], literal_type: str) -> Literal:
        if literal_type not in LITERAL_TYPES:
            raise self._error(f"the filter contains the unsupported element literal_{literal_type}")
        if literal_type == "null":
            return Literal("null", None)
        value = self._key(data, "value")
        if literal_type == "bool":
            return Literal("bool", self._bool(value))
        if literal_type in ("exactnumeric", "double"):
            return Literal(literal_type, self._number(value, literal_type))
        if not isinstance(value, str):
            raise self._error(f"malformed filter: literal_{literal_type} value is not a string")
        if literal_type == "string":
            return Literal("string", self._text(value))
        pattern = DATE_PATTERN if literal_type == "date" else TIMESTAMP_PATTERN
        if not pattern.fullmatch(value):
            raise self._error(f"malformed {literal_type} literal {value[:40]!r}")
        return Literal(literal_type, value)

    def _bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in _BOOL_TEXT:
            return _BOOL_TEXT[value.lower()]
        raise self._error(f"malformed boolean literal {value!r}")

    def _number(self, value: Any, literal_type: str) -> Decimal:
        if isinstance(value, bool):
            raise self._error(f"malformed {literal_type} literal {value!r}")
        if isinstance(value, float) and literal_type == "double":
            value = repr(value)
        if isinstance(value, int):
            value = str(value)
        if not isinstance(value, str) or not _NUMBER.fullmatch(value):
            raise self._error(f"malformed {literal_type} literal {str(value)[:40]!r}")
        if len(value) > MAX_NUMERIC_TEXT:
            raise self._error(f"{literal_type} literal {value[:40]!r}... is too long")
        number = Decimal(value)
        if number and abs(number.adjusted()) > MAX_NUMERIC_EXPONENT:
            raise self._error(f"{literal_type} literal {value[:40]!r} is out of range")
        return number

    def _text(self, value: str) -> str:
        problem = check_text(value)
        if problem is not None:
            raise self._error(f"a string literal {problem}")
        return value
