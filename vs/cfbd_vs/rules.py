"""Requirement rules and tier gating, evaluated at plan time (stdlib only).

Input seam: `constraints` maps an API parameter name to the finite collection of values the
solved query allows for it (an integer interval may be passed as a `range`). A parameter that
is a key is "set" in every expanded request, because the planner expands the cartesian product
of the sets. Presence rules are therefore decided on the keys and value rules on the sets,
which is exact for every expanded request.
"""

from __future__ import annotations

import itertools
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from vs.cfbd_vs.catalog import Rule, Table

SCHEMA_NAME = "CFBD_VS"
FEATURES_PROPERTY = "API_FEATURES"
GATING_RULE_PREFIX = "gating"
KNOWN_FEATURES = ("adjustedMetrics", "weather", "scoreboard", "livePlayByPlay", "graphQl")
MESSAGE_VALUE_COUNT = 5
MESSAGE_VALUE_LIMIT = 60

Value = str | int | float | bool
Constraints = Mapping[str, Collection[Value]]


@dataclass(frozen=True)
class Violation:
    """One violated rule, already worded for the user."""

    table: str
    rule_id: str
    text: str

    def __str__(self) -> str:
        return f"{SCHEMA_NAME}.{self.table}: rule {self.rule_id}: {self.text}"


class RuleViolation(ValueError):
    """One or more rules were violated; the message has one line per violation."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        super().__init__("\n".join(str(v) for v in violations))
        self.violations = violations


@dataclass(frozen=True)
class Evaluation:
    """The constraints with explicit defaults applied and the parameters that were defaulted."""

    constraints: dict[str, Collection[Value]]
    defaulted: tuple[str, ...]


def parse_api_features(text: str | None) -> frozenset[str]:
    """Parse the comma-separated `API_FEATURES` property; empty or missing means no features."""
    if text is None:
        return frozenset()
    return frozenset(part.strip() for part in text.split(",") if part.strip())


def check_gating(table: Table, features: frozenset[str]) -> None:
    """Refuse a gated table unless its feature is listed (fail closed on an empty set)."""
    feature = table.gated_by
    if feature is None or feature in features:
        return
    known = ", ".join(sorted(features)) if features else "none"
    text = (
        f"this table needs the CFBD feature '{feature}', which is not in the virtual schema "
        f"property {FEATURES_PROPERTY} (currently: {known}). Refresh it with the deployment "
        f"(`make deploy` syncs it from /info) or ALTER VIRTUAL SCHEMA {SCHEMA_NAME} "
        f"SET {FEATURES_PROPERTY} = '<comma-separated features>'"
    )
    raise RuleViolation((Violation(table.name, f"{GATING_RULE_PREFIX}.{feature}", text),))


def apply_defaults(table: Table, constraints: Constraints) -> Evaluation:
    """Add every explicit default whose parameter the query does not filter."""
    result: dict[str, Collection[Value]] = dict(constraints)
    defaulted: list[str] = []
    for rule in table.rules:
        if rule.type != "default":
            continue
        name = _only_param(rule)
        if name not in result:
            result[name] = [rule.details["value"]]
            defaulted.append(name)
    return Evaluation(result, tuple(defaulted))


def check_rules(table: Table, constraints: Constraints) -> None:
    """Raise `RuleViolation` listing every table rule the constraints violate."""
    _reject_empty_sets(constraints)
    columns = {p.name: p.column for p in table.params}
    violations = []
    for rule in table.rules:
        check = _CHECKS.get(rule.type)
        head = check(rule, constraints, columns) if check else None
        if head:
            violations.append(Violation(table.name, rule.id, _with_message(head, rule)))
    if violations:
        raise RuleViolation(tuple(violations))


def evaluate(table: Table, constraints: Constraints, features: frozenset[str]) -> Evaluation:
    """Gating, then explicit defaults, then every rule. Returns the defaulted constraints."""
    check_gating(table, features)
    evaluation = apply_defaults(table, constraints)
    check_rules(table, evaluation.constraints)
    return evaluation


def _only_param(rule: Rule) -> str:
    if len(rule.params) != 1:
        raise ValueError(f"rule {rule.id}: type {rule.type} needs exactly one parameter")
    return rule.params[0]


def _reject_empty_sets(constraints: Constraints) -> None:
    for name, values in constraints.items():
        if len(values) == 0:
            raise ValueError(f"empty value set for {name}: contradictions must be handled earlier")


def _with_message(head: str, rule: Rule) -> str:
    return f"{head} ({rule.message})" if rule.message else head


def _column(columns: Mapping[str, str], name: str, rule: Rule) -> str:
    if name not in columns:
        raise ValueError(f"rule {rule.id}: unknown parameter {name!r}")
    return columns[name]


def _list(columns: Mapping[str, str], names: Collection[str], rule: Rule) -> str:
    return ", ".join(_column(columns, name, rule) for name in names)


def _literal(value: Value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _same(left: Value, right: Value) -> bool:
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def _contains(values: Collection[Value], wanted: Value) -> bool:
    if isinstance(values, range):
        return _in_range(values, wanted)
    return any(_same(value, wanted) for value in values)


def _in_range(values: range, wanted: Value) -> bool:
    if isinstance(wanted, bool) or not isinstance(wanted, (int, float)):
        return False
    if isinstance(wanted, float):
        if not wanted.is_integer():
            return False
        wanted = int(wanted)
    return wanted in values


def _triggered(rule: Rule, constraints: Constraints) -> bool:
    trigger = rule.details["if"]
    if trigger not in constraints:
        return False
    if "if_value" not in rule.details:
        return True
    return _contains(constraints[trigger], rule.details["if_value"])


def _trigger_text(rule: Rule, columns: Mapping[str, str]) -> str:
    column = _column(columns, rule.details["if"], rule)
    if "if_value" in rule.details:
        return f"{column} = {_literal(rule.details['if_value'])}"
    return f"{column} is set"


def _check_required(rule: Rule, constraints: Constraints, columns: Mapping[str, str]) -> str | None:
    missing = [name for name in rule.params if name not in constraints]
    if not missing:
        return None
    return f"required parameter not set: {_list(columns, missing, rule)}"


def _check_one_of(rule: Rule, constraints: Constraints, columns: Mapping[str, str]) -> str | None:
    if any(name in constraints for name in rule.params):
        return None
    return f"at least one of {_list(columns, rule.params, rule)} must be set"


def _check_requires_any(
    rule: Rule, constraints: Constraints, columns: Mapping[str, str]
) -> str | None:
    if not _triggered(rule, constraints):
        return None
    options = rule.details["any_of"]
    if any(name in constraints for name in options):
        return None
    return (
        f"when {_trigger_text(rule, columns)}, at least one of "
        f"{_list(columns, options, rule)} must be set"
    )


def _check_excludes(rule: Rule, constraints: Constraints, columns: Mapping[str, str]) -> str | None:
    if not _triggered(rule, constraints):
        return None
    clashing = [name for name in rule.details["none_of"] if name in constraints]
    if not clashing:
        return None
    trigger = _column(columns, rule.details["if"], rule)
    return f"{trigger} cannot be combined with {_list(columns, clashing, rule)}"


def _check_requires_value(
    rule: Rule, constraints: Constraints, columns: Mapping[str, str]
) -> str | None:
    if not _triggered(rule, constraints):
        return None
    target = rule.details["then"]
    wanted = rule.details["then_value"]
    if target in constraints and all(_same(v, wanted) for v in constraints[target]):
        return None
    got = "not set" if target not in constraints else _describe_values(constraints[target], wanted)
    return (
        f"when {_trigger_text(rule, columns)}, {_column(columns, target, rule)} must be "
        f"{_literal(wanted)} (got: {got})"
    )


def _describe_values(values: Collection[Value], wanted: Value) -> str:
    offending = (v for v in values if not _same(v, wanted))
    shown = list(itertools.islice(offending, MESSAGE_VALUE_COUNT + 1))
    text = ", ".join(_short(_literal(v)) for v in shown[:MESSAGE_VALUE_COUNT])
    return text + ", ..." if len(shown) > MESSAGE_VALUE_COUNT else text


def _short(text: str) -> str:
    return text if len(text) <= MESSAGE_VALUE_LIMIT else text[:MESSAGE_VALUE_LIMIT] + "..."


def _extent(values: Collection[Value], rule: Rule) -> tuple[Any, Any]:
    if isinstance(values, range):
        return min(values[0], values[-1]), max(values[0], values[-1])
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(numbers) != len(values):
        raise ValueError(f"rule {rule.id}: bounds need numeric values")
    return min(numbers), max(numbers)


def _check_bounds(rule: Rule, constraints: Constraints, columns: Mapping[str, str]) -> str | None:
    name = _only_param(rule)
    if name not in constraints:
        return None
    column = _column(columns, name, rule)
    low, high = _extent(constraints[name], rule)
    minimum = rule.details.get("min")
    maximum = rule.details.get("max")
    if minimum is not None and low < minimum:
        return f"{column} must be at least {minimum} (got {low})"
    if maximum is not None and high > maximum:
        return f"{column} must be at most {maximum} (got {high})"
    return None


def _check_row_cap(rule: Rule, constraints: Constraints, columns: Mapping[str, str]) -> str | None:
    completing = rule.details["complete_with"]
    if any(name in constraints for name in completing):
        return None
    return (
        f"the API returns at most {rule.details['rows']:,} rows, so a complete result needs "
        f"{_list(columns, completing, rule)} to be set"
    )


_CHECKS = {
    "required": _check_required,
    "one_of": _check_one_of,
    "requires_any": _check_requires_any,
    "excludes": _check_excludes,
    "requires_value": _check_requires_value,
    "bounds": _check_bounds,
    "row_cap": _check_row_cap,
}
