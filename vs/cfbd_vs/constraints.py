"""Parameter constraint solver (stdlib only).

Splits a parsed pushdown filter (`vs.cfbd_vs.filters`) into parameter predicates and residual
predicates, turns the parameter predicates into one value domain per API parameter, and decides
contradictions. The result type `SolvedFilter` is the input of the rules engine and the planner.

Invariants:
- A parameter's domain is exactly the set of echo values that satisfy all of its predicates under
  Exasol semantics: NULL never matches, `''` is NULL, integer bounds tighten to integers, and a
  value that does not fit the echo column type cannot match. Every row therefore satisfies the
  parameter predicates, so the residual never has to repeat them.
- Integer domains are unions of closed intervals and are never enumerated here: `count()` costs
  O(number of intervals). Callers check `request_count()` against the budget before expanding.
- Precedence: invalid or unsupported predicates are rejected first, in filter order; then a
  contradiction solves to zero requests; only then are open-ended ranges rejected.
- Exasol does not fold boolean constants (probe 1.4; `= NULL` arrives as `literal_null`). Inside a
  parameter formula, where NOT is rejected, NULL and FALSE match nothing and TRUE leaves the
  parameter unconstrained; a top-level NULL or FALSE conjunct is a contradiction.
"""

from __future__ import annotations

import bisect
import math
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import reduce
from typing import Any, ClassVar

from vs.cfbd_vs.catalog import Param, Table
from vs.cfbd_vs.filters import (
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
    PushdownError,
)
from vs.cfbd_vs.sqltypes import SqlType, SqlTypeError, parse_sql_type

ParamValue = int | float | str | bool

INTEGER_FORMS = "= value, IN (values), <, <=, >, >=, BETWEEN low AND high"
OTHER_FORMS = "= value, IN (values)"
MESSAGE_VALUE_LIMIT = 60

_NUMERIC_LITERALS = ("exactnumeric", "double")
_BOOLEAN_CONSTANTS = ("bool", "null")
_LITERAL_NAMES = {
    "exactnumeric": "numeric",
    "double": "numeric",
    "string": "string",
    "bool": "boolean",
    "date": "date",
    "timestamp": "timestamp",
}
_TYPED_TEXT_PREFIX = {"string": "", "date": "DATE ", "timestamp": "TIMESTAMP "}
_UNSUPPORTED_FORMS: dict[type, str] = {
    Like: "LIKE",
    IsNull: "IS NULL",
    IsNotNull: "IS NOT NULL",
    Not: "NOT",
}
_REVERSED = {"<": ">", "<=": ">="}

_End = int | float
_Spans = tuple[tuple[_End, _End], ...]


class ConstraintError(PushdownError):
    """A parameter predicate cannot be turned into API requests."""


@dataclass(frozen=True)
class IntRanges:
    """Integer values as sorted, disjoint, non-adjacent closed intervals `(low, high)`."""

    intervals: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        previous_high: int | None = None
        for low, high in self.intervals:
            if low > high or (previous_high is not None and low <= previous_high + 1):
                raise ValueError(f"intervals are not canonical: {self.intervals}")
            previous_high = high

    def count(self) -> int:
        """Number of values, without enumerating them."""
        return sum(high - low + 1 for low, high in self.intervals)

    def __iter__(self) -> Iterator[int]:
        for low, high in self.intervals:
            yield from range(low, high + 1)

    def __contains__(self, value: object) -> bool:
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        index = bisect.bisect_right(self.intervals, value, key=lambda interval: interval[0]) - 1
        return index >= 0 and value <= self.intervals[index][1]

    @property
    def min_value(self) -> int:
        return self.intervals[0][0]

    @property
    def max_value(self) -> int:
        return self.intervals[-1][1]


@dataclass(frozen=True)
class ValueSet:
    """A finite set of distinct string, number (float) or boolean values in ascending order."""

    values: tuple[ParamValue, ...]

    def count(self) -> int:
        """Number of values."""
        return len(self.values)

    def __iter__(self) -> Iterator[ParamValue]:
        return iter(self.values)

    def __contains__(self, value: object) -> bool:
        return value in self.values

    @property
    def min_value(self) -> ParamValue:
        return self.values[0]

    @property
    def max_value(self) -> ParamValue:
        return self.values[-1]


@dataclass(frozen=True)
class ParamConstraint:
    """The values one API parameter takes in the requests of a virtual-table access.

    Values are `int` for integer parameters, finite `float` for number parameters (their echo
    column is DOUBLE; `-0.0` is normalized to `0.0`), `str` and `bool` otherwise, in ascending
    order and never empty.
    """

    param: Param
    domain: IntRanges | ValueSet

    def count(self) -> int:
        """Number of values, without enumerating them."""
        return self.domain.count()

    def values(self) -> Iterator[ParamValue]:
        """The values in ascending order, lazily."""
        return iter(self.domain)

    def __contains__(self, value: object) -> bool:
        return value in self.domain

    @property
    def min_value(self) -> ParamValue:
        return self.domain.min_value

    @property
    def max_value(self) -> ParamValue:
        return self.domain.max_value

    def as_collection(self) -> Collection[ParamValue]:
        """The values as the planner and rules engine take them.

        A single integer interval stays a lazy `range`; anything else is materialized, so check
        `SolvedFilter.request_count()` against the budget before calling this.
        """
        domain = self.domain
        if isinstance(domain, IntRanges) and len(domain.intervals) == 1:
            low, high = domain.intervals[0]
            return range(low, high + 1)
        return tuple(domain)


@dataclass(frozen=True)
class SolvedFilter:
    """The solved filter of one virtual-table access.

    - `contradiction`: the parameter predicates cannot all hold; plan zero requests, emit the
      zero-row SQL, and skip rules, gating and budget. `constraints` and `residual` are then empty.
    - `constraints`: one entry per filtered parameter, in catalog parameter order. Unfiltered
      parameters are absent (explicit defaults are the rules engine's business).
    - `residual`: the top-level conjuncts on response, position and `REQ_*` columns, in filter
      order, to be AND-ed into the pushdown SQL (`sqlrender.render_conjunction`).
    """

    contradiction: bool
    constraints: tuple[ParamConstraint, ...] = ()
    residual: tuple[Expr, ...] = ()

    def constraint(self, param_name: str) -> ParamConstraint | None:
        """The constraint of the API parameter `param_name`, or None when it is not filtered."""
        for constraint in self.constraints:
            if constraint.param.name == param_name:
                return constraint
        return None

    def request_count(self) -> int:
        """Size of the cartesian product of the constrained values (0 on a contradiction)."""
        if self.contradiction:
            return 0
        return math.prod(constraint.count() for constraint in self.constraints)

    def value_sets(self) -> dict[str, Collection[ParamValue]]:
        """API parameter name -> `as_collection()`, in catalog order: the planner's input.

        Check `request_count()` against the budget first; only single intervals stay lazy.
        """
        return {c.param.name: c.as_collection() for c in self.constraints}


def solve(expr: Expr | None, table: Table) -> SolvedFilter:
    """Solve the parsed filter of an access to `table`; raises `ConstraintError` on rejection."""
    if expr is None:
        return SolvedFilter(contradiction=False)
    return _Solver(table).solve(expr)


class _Solver:
    def __init__(self, table: Table) -> None:
        self._table = table
        self._params = {param.column: param for param in table.params}
        self._evaluators: dict[str, _Evaluator[Any]] = {}
        self._domains: dict[str, Any] = {}

    def solve(self, expr: Expr) -> SolvedFilter:
        residual: list[Expr] = []
        always_false = False
        for conjunct in _conjuncts(expr):
            if isinstance(conjunct, Literal) and conjunct.type in _BOOLEAN_CONSTANTS:
                always_false = always_false or conjunct.value is not True
                continue
            column = self._parameter_of(conjunct)
            if column is None:
                residual.append(conjunct)
            else:
                self._constrain(column, conjunct)
        finished = {
            column: self._evaluators[column].finish(domain)
            for column, domain in self._domains.items()
        }
        if always_false or any(domain.count() == 0 for domain, _ in finished.values()):
            return SolvedFilter(contradiction=True)
        params = [param for param in self._table.params if param.column in finished]
        for param in params:
            if finished[param.column][1]:
                raise ConstraintError(self._table.name, _open_range_detail(param.column))
        constraints = tuple(ParamConstraint(p, finished[p.column][0]) for p in params)
        return SolvedFilter(contradiction=False, constraints=constraints, residual=tuple(residual))

    def _parameter_of(self, conjunct: Expr) -> str | None:
        columns = _columns(conjunct)
        params = [column.name for column in columns if column.is_param]
        if not params:
            return None
        if len(columns) > 1 and any(isinstance(node, Or) for node in _walk(conjunct)):
            raise _cross_or(self._table, [column.name for column in columns])
        return params[0]

    def _constrain(self, column: str, conjunct: Expr) -> None:
        evaluator = self._evaluators.get(column)
        if evaluator is None:
            evaluator = _evaluator(self._table, self._params[column])
            self._evaluators[column] = evaluator
        domain = evaluator.evaluate(conjunct)
        if domain is None:
            return
        prior = self._domains.get(column)
        self._domains[column] = domain if prior is None else evaluator.intersect(prior, domain)


def _parse_echo(sql_type: str) -> SqlType | None:
    try:
        return parse_sql_type(sql_type)
    except SqlTypeError:
        return None


class _Evaluator[D](ABC):
    """Turns predicates on one parameter column into a domain of type D."""

    forms: ClassVar[str]
    echo_kind: ClassVar[str]

    def __init__(self, table: Table, param: Param) -> None:
        self._table = table
        self._param = param
        self._column = param.column
        echo = _parse_echo(param.sql_type)
        if echo is None or not self._accepts(echo):
            raise self._error(
                f"catalog error: {param.column} has the unexpected echo column type "
                f"{param.sql_type} for a {param.type} parameter"
            )
        self._echo = echo

    def _accepts(self, echo: SqlType) -> bool:
        return echo.kind == self.echo_kind

    def evaluate(self, node: Expr) -> D | None:
        """The domain of a predicate on this parameter; None when it does not constrain it."""
        if isinstance(node, Literal) and node.type in _BOOLEAN_CONSTANTS:
            return None if node.value is True else self.points([])
        if not any(isinstance(n, ColumnRef) and n.name == self._column for n in _walk(node)):
            raise _cross_or(self._table, [self._column, "a constant"])
        if isinstance(node, And):
            known = [d for d in (self.evaluate(item) for item in node.items) if d is not None]
            return reduce(self.intersect, known) if known else None
        if isinstance(node, Or):
            domains = [self.evaluate(item) for item in node.items]
            known = [d for d in domains if d is not None]
            return self.union(known) if len(known) == len(domains) else None
        if isinstance(node, Comparison):
            return self._comparison(node)
        if isinstance(node, InList):
            if not self._is_parameter(node.expr):
                raise self._unsupported("IN")
            return self.points([self._literal(item) for item in node.items])
        if isinstance(node, Between):
            if not self._is_parameter(node.expr):
                raise self._unsupported("BETWEEN")
            return self.between(node.low, node.high)
        if isinstance(node, ColumnRef):
            return self.points([Literal("bool", True)])
        raise self._unsupported(_UNSUPPORTED_FORMS[type(node)])

    def _comparison(self, node: Comparison) -> D:
        if self._is_parameter(node.left):
            other, form = node.right, node.op
        elif self._is_parameter(node.right):
            other, form = node.left, _REVERSED.get(node.op, node.op)
        else:
            raise self._unsupported("(nested comparison)")
        if form == "=":
            return self.points([self._literal(other)])
        if form == "<>":
            raise self._unsupported("<>")
        return self.bound(form, other)

    def _is_parameter(self, node: Expr) -> bool:
        return isinstance(node, ColumnRef) and node.name == self._column

    def _literal(self, node: Expr) -> Literal:
        if isinstance(node, Literal):
            return node
        operand = f"column {node.name}" if isinstance(node, ColumnRef) else "an expression"
        raise self._error(
            f"{self._column} can only be compared with literal values, not with {operand}"
        )

    def _error(self, detail: str) -> ConstraintError:
        return ConstraintError(self._table.name, detail)

    def _unsupported(self, form: str) -> ConstraintError:
        column = self._column
        return self._error(
            f"unsupported predicate {form} on parameter column {column}; supported for {column}: "
            f"{self.forms}, combined with AND, or with OR on {column} only"
        )

    def _type_error(self, literal: Literal) -> ConstraintError:
        param_type = self._param.type
        article = "an" if param_type[0] in "aeiou" else "a"
        return self._error(
            f"{self._column} is {article} {param_type} parameter and cannot take the "
            f"{_LITERAL_NAMES[literal.type]} literal {_show(literal)}"
        )

    @abstractmethod
    def points(self, literals: Sequence[Literal]) -> D:
        """The domain of `column IN (literals)`."""

    @abstractmethod
    def bound(self, form: str, node: Expr) -> D:
        """The domain of `column <form> node` for form `<`, `<=`, `>` or `>=`."""

    @abstractmethod
    def between(self, low: Expr, high: Expr) -> D:
        """The domain of `column BETWEEN low AND high`."""

    @abstractmethod
    def intersect(self, first: D, second: D) -> D:
        """Values in both domains."""

    @abstractmethod
    def union(self, domains: Sequence[D]) -> D:
        """Values in any of the domains."""

    @abstractmethod
    def finish(self, domain: D) -> tuple[IntRanges | ValueSet, bool]:
        """The public domain, limited to echo-representable values, and whether it is open."""


class _IntegerEvaluator(_Evaluator[_Spans]):
    forms = INTEGER_FORMS
    echo_kind = "DECIMAL"

    def __init__(self, table: Table, param: Param) -> None:
        super().__init__(table, param)
        self._limit = 10 ** (self._echo.precision or 0) - 1

    def _accepts(self, echo: SqlType) -> bool:
        return echo.is_integer

    def points(self, literals: Sequence[Literal]) -> _Spans:
        values = (self._integer(literal) for literal in literals)
        return _merge((value, value) for value in values if value is not None)

    def bound(self, form: str, node: Expr) -> _Spans:
        value = self._number(self._literal(node))
        if value is None:
            return ()
        if form == "<":
            return ((-math.inf, math.ceil(value) - 1),)
        if form == "<=":
            return ((-math.inf, math.floor(value)),)
        if form == ">":
            return ((math.floor(value) + 1, math.inf),)
        return ((math.ceil(value), math.inf),)

    def between(self, low: Expr, high: Expr) -> _Spans:
        return self.intersect(self.bound(">=", low), self.bound("<=", high))

    def intersect(self, first: _Spans, second: _Spans) -> _Spans:
        result: list[tuple[_End, _End]] = []
        i = j = 0
        while i < len(first) and j < len(second):
            low = max(first[i][0], second[j][0])
            high = min(first[i][1], second[j][1])
            if low <= high:
                result.append((low, high))
            if first[i][1] < second[j][1]:
                i += 1
            else:
                j += 1
        return tuple(result)

    def union(self, domains: Sequence[_Spans]) -> _Spans:
        return _merge(span for domain in domains for span in domain)

    def finish(self, domain: _Spans) -> tuple[IntRanges | ValueSet, bool]:
        is_open = any(math.isinf(end) for span in domain for end in span)
        clamped = self.intersect(domain, ((-self._limit, self._limit),))
        return IntRanges(tuple((int(low), int(high)) for low, high in clamped)), is_open

    def _number(self, literal: Literal) -> Decimal | None:
        if literal.type == "null":
            return None
        if literal.type not in _NUMERIC_LITERALS or not isinstance(literal.value, Decimal):
            raise self._type_error(literal)
        return literal.value

    def _integer(self, literal: Literal) -> int | None:
        value = self._number(literal)
        if value is None:
            return None
        integral = math.floor(value)
        if integral != value or abs(integral) > self._limit:
            return None
        return integral


class _SetEvaluator[V: (str, float, bool)](_Evaluator[frozenset[V]]):
    forms = OTHER_FORMS

    def points(self, literals: Sequence[Literal]) -> frozenset[V]:
        values = (self._value(literal) for literal in literals)
        return frozenset(value for value in values if value is not None)

    def bound(self, form: str, node: Expr) -> frozenset[V]:
        raise self._unsupported(form)

    def between(self, low: Expr, high: Expr) -> frozenset[V]:
        raise self._unsupported("BETWEEN")

    def intersect(self, first: frozenset[V], second: frozenset[V]) -> frozenset[V]:
        return first & second

    def union(self, domains: Sequence[frozenset[V]]) -> frozenset[V]:
        return frozenset[V]().union(*domains)

    def finish(self, domain: frozenset[V]) -> tuple[IntRanges | ValueSet, bool]:
        return ValueSet(tuple(sorted(domain))), False

    @abstractmethod
    def _value(self, literal: Literal) -> V | None:
        """The parameter value of an equality literal; None when no echo value can equal it."""


class _StringEvaluator(_SetEvaluator[str]):
    echo_kind = "VARCHAR"

    def __init__(self, table: Table, param: Param) -> None:
        super().__init__(table, param)
        self._size = self._echo.size or 0

    def _value(self, literal: Literal) -> str | None:
        if literal.type == "null":
            return None
        if literal.type != "string" or not isinstance(literal.value, str):
            raise self._type_error(literal)
        value = literal.value
        if value == "":
            return None
        enum = self._param.enum
        if enum is not None and value not in enum:
            raise self._error(
                f"{_show(literal)} is not an allowed value for {self._column}; "
                f"allowed values: {', '.join(enum)}"
            )
        return value if len(value) <= self._size else None


class _NumberEvaluator(_SetEvaluator[float]):
    echo_kind = "DOUBLE"

    def _value(self, literal: Literal) -> float | None:
        if literal.type == "null":
            return None
        if literal.type not in _NUMERIC_LITERALS or not isinstance(literal.value, Decimal):
            raise self._type_error(literal)
        number = float(literal.value) + 0.0
        return number if math.isfinite(number) else None


class _BooleanEvaluator(_SetEvaluator[bool]):
    echo_kind = "BOOLEAN"

    def _value(self, literal: Literal) -> bool | None:
        if literal.type == "null":
            return None
        if literal.type != "bool" or not isinstance(literal.value, bool):
            raise self._type_error(literal)
        return literal.value


_EVALUATORS: dict[str, type[_Evaluator[Any]]] = {
    "integer": _IntegerEvaluator,
    "number": _NumberEvaluator,
    "string": _StringEvaluator,
    "boolean": _BooleanEvaluator,
}


def _evaluator(table: Table, param: Param) -> _Evaluator[Any]:
    evaluator_type = _EVALUATORS.get(param.type)
    if evaluator_type is None:
        raise ConstraintError(
            table.name,
            f"catalog error: {param.column} has the unsupported parameter type {param.type}",
        )
    return evaluator_type(table, param)


def _merge(spans: Iterable[tuple[_End, _End]]) -> _Spans:
    merged: list[tuple[_End, _End]] = []
    for low, high in sorted(span for span in spans if span[0] <= span[1]):
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return tuple(merged)


def _children(node: Expr) -> tuple[Expr, ...]:
    if isinstance(node, (And, Or)):
        return node.items
    if isinstance(node, Not):
        return (node.item,)
    if isinstance(node, Comparison):
        return (node.left, node.right)
    if isinstance(node, Like):
        return (
            (node.expr, node.pattern)
            if node.escape is None
            else (node.expr, node.pattern, node.escape)
        )
    if isinstance(node, Between):
        return (node.expr, node.low, node.high)
    if isinstance(node, InList):
        return (node.expr, *node.items)
    if isinstance(node, (IsNull, IsNotNull)):
        return (node.expr,)
    return ()


def _walk(node: Expr) -> Iterator[Expr]:
    yield node
    for child in _children(node):
        yield from _walk(child)


def _columns(node: Expr) -> list[ColumnRef]:
    found: dict[str, ColumnRef] = {}
    for item in _walk(node):
        if isinstance(item, ColumnRef):
            found.setdefault(item.name, item)
    return list(found.values())


def _conjuncts(node: Expr) -> Iterator[Expr]:
    if isinstance(node, And):
        for item in node.items:
            yield from _conjuncts(item)
    else:
        yield node


def _cross_or(table: Table, names: Sequence[str]) -> ConstraintError:
    listed = ", ".join(names[:-1]) + " and " + names[-1]
    return ConstraintError(
        table.name,
        "OR is supported only between predicates on one and the same parameter column, "
        f"but this OR involves {listed}; use UNION ALL of separate queries instead",
    )


def _open_range_detail(column: str) -> str:
    return (
        f"{column} needs a bounded range or an IN list: give both a lower and an upper bound "
        f"(for example {column} BETWEEN <low> AND <high>) or list the values ({column} IN (...))"
    )


def _show(literal: Literal) -> str:
    value = literal.value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    text = str(value)
    if literal.type not in _TYPED_TEXT_PREFIX:
        return text
    if len(text) > MESSAGE_VALUE_LIMIT:
        text = text[:MESSAGE_VALUE_LIMIT] + "..."
    return _TYPED_TEXT_PREFIX[literal.type] + "'" + text.replace("'", "''") + "'"
