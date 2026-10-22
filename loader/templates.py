"""Slice templates: each plan entry compiled with the adapter's own request planner.

The slice-key grammar exists once, in `vs/cfbd_vs/plan.py`. For every entry this module runs the
adapter's code (`rules.apply_defaults`, `rules.check_rules`, `plan.plan_requests`) on the entry's
fixed parameters plus sentinel season numbers. The one planned request is the entry's key, with
the sentinels replaced by `{season}` (or `{season_from}` and `{season_to}`), so the views only
substitute integers and a planned key cannot drift from a requested key.

`fixed_filter` is the entry's fixed parameters as a SQL predicate on the echo columns, rendered
with `vs.cfbd_vs.sqlrender`; the batch query of the loader ANDs it with its slice filter.

Gating is not checked here: `gated_by` is carried to `LOAD_PLAN`, and the views plan a gated
entry only while the virtual schema lists its feature.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from loader.plan import (
    NO_SLICING,
    RANGE_PARAMS,
    RANGE_SLICING,
    SEASON_PARAM,
    SEASON_SLICING,
    LoadPlan,
    ParamValue,
    PlanEntry,
    PlanError,
    PlanIssue,
)
from vs.cfbd_vs import rules, sqlrender
from vs.cfbd_vs.catalog import Catalog, Param, Table
from vs.cfbd_vs.filters import ColumnRef, Comparison, Expr, Literal
from vs.cfbd_vs.plan import PlanError as RequestPlanError
from vs.cfbd_vs.plan import plan_requests

SEASON_PLACEHOLDER = "{season}"
SEASON_FROM_PLACEHOLDER = "{season_from}"
SEASON_TO_PLACEHOLDER = "{season_to}"
SEASON_SENTINEL = 900_000_001
FROM_SENTINEL = 900_000_002
TO_SENTINEL = 900_000_003
CHECK_SEASON = 2000
TEMPLATE_RULE = "slice-template"
SENTINEL_FOR_PARAM: Mapping[str, tuple[str, int]] = {
    SEASON_PARAM: (SEASON_PLACEHOLDER, SEASON_SENTINEL),
    RANGE_PARAMS[0]: (SEASON_FROM_PLACEHOLDER, FROM_SENTINEL),
    RANGE_PARAMS[1]: (SEASON_TO_PLACEHOLDER, TO_SENTINEL),
}


@dataclass(frozen=True)
class SliceTemplate:
    """One compiled entry: what `LOAD_PLAN.SLICE_TEMPLATE`, `FIXED_FILTER` and `GATED_BY` hold."""

    entry_id: int
    table: str
    slicing: str
    template: str
    fixed_filter: str | None
    gated_by: str | None
    seasons_from: int | None = None
    seasons_to: int | None = None

    def slice_key(self, season: int | None = None) -> str:
        """The key of one slice: `season` for `season` slicing, none for the other slicings.

        A `season_range` entry fills `{season_from}` and `{season_to}` from its own bounds.
        """
        if self.slicing == SEASON_SLICING:
            if season is None:
                raise ValueError(f"entry {self.entry_id} ({self.table}) needs a season")
            return self.template.replace(SEASON_PLACEHOLDER, str(season))
        if season is not None:
            raise ValueError(f"entry {self.entry_id} ({self.table}) takes no season")
        return self.template.replace(SEASON_FROM_PLACEHOLDER, str(self.seasons_from)).replace(
            SEASON_TO_PLACEHOLDER, str(self.seasons_to)
        )


def compile_plan(plan: LoadPlan, catalog: Catalog) -> tuple[SliceTemplate, ...]:
    """Compile every entry; raises `PlanError` listing each entry that breaks a request rule."""
    tables = {table.name: table for table in catalog.tables}
    issues: list[PlanIssue] = []
    compiled: list[SliceTemplate] = []
    for entry in plan.entries:
        result = _compile_entry(entry, tables[entry.table], issues)
        if result is not None:
            compiled.append(result)
    if issues:
        raise PlanError(issues)
    return tuple(compiled)


def _compile_entry(entry: PlanEntry, table: Table, issues: list[PlanIssue]) -> SliceTemplate | None:
    where = f"entry {entry.entry_id} ({entry.table})"
    fixed = entry.fixed_params()
    try:
        template = _template(entry, table, fixed, where, issues)
        if template is None:
            return None
        _check_round_trip(entry, table, fixed, template)
        filter_sql = _fixed_filter(table, fixed)
    except (RequestPlanError, sqlrender.RenderError, ValueError) as error:
        issues.append(PlanIssue(where, TEMPLATE_RULE, str(error)))
        return None
    return SliceTemplate(
        entry_id=entry.entry_id,
        table=entry.table,
        slicing=entry.slicing,
        template=template,
        fixed_filter=filter_sql,
        gated_by=table.gated_by,
        seasons_from=entry.seasons_from if entry.slicing == RANGE_SLICING else None,
        seasons_to=entry.seasons_to if entry.slicing == RANGE_SLICING else None,
    )


def _template(
    entry: PlanEntry,
    table: Table,
    fixed: Mapping[str, ParamValue],
    where: str,
    issues: list[PlanIssue],
) -> str | None:
    sentinels = {name: SENTINEL_FOR_PARAM[name][1] for name in _sliced_params(entry.slicing)}
    constraints = {name: [value] for name, value in {**fixed, **sentinels}.items()}
    evaluation = rules.apply_defaults(table, constraints)
    try:
        rules.check_rules(table, evaluation.constraints)
    except rules.RuleViolation as violation:
        issues.extend(PlanIssue(where, v.rule_id, v.text) for v in violation.violations)
        return None
    requests = plan_requests(table, evaluation.constraints, budget=None)
    if len(requests.slice_keys) != 1:
        raise ValueError(f"expected exactly one planned request, got {len(requests.slice_keys)}")
    key = requests.slice_keys[0]
    for name in sentinels:
        placeholder, sentinel = SENTINEL_FOR_PARAM[name]
        if key.count(str(sentinel)) != 1:
            raise ValueError(f"parameter {name} is not in the planned key {key!r}")
        key = key.replace(str(sentinel), placeholder)
    return key


def _check_round_trip(
    entry: PlanEntry, table: Table, fixed: Mapping[str, ParamValue], template: str
) -> None:
    """The template with real numbers must equal the adapter's key for the same values."""
    values: dict[str, ParamValue] = dict(fixed)
    probe = SliceTemplate(entry.entry_id, entry.table, entry.slicing, template, None, None)
    if entry.slicing == SEASON_SLICING:
        values[SEASON_PARAM] = CHECK_SEASON
        rendered = probe.slice_key(CHECK_SEASON)
    elif entry.slicing == RANGE_SLICING:
        values[RANGE_PARAMS[0]], values[RANGE_PARAMS[1]] = CHECK_SEASON, CHECK_SEASON + 1
        rendered = template.replace(SEASON_FROM_PLACEHOLDER, str(CHECK_SEASON)).replace(
            SEASON_TO_PLACEHOLDER, str(CHECK_SEASON + 1)
        )
    else:
        rendered = template
    constraints = {name: [value] for name, value in values.items()}
    expected = plan_requests(
        table, rules.apply_defaults(table, constraints).constraints, budget=None
    ).slice_keys[0]
    if rendered != expected:
        raise ValueError(f"template {template!r} does not reproduce the adapter key {expected!r}")


def _sliced_params(slicing: str) -> tuple[str, ...]:
    if slicing == SEASON_SLICING:
        return (SEASON_PARAM,)
    if slicing == RANGE_SLICING:
        return RANGE_PARAMS
    assert slicing == NO_SLICING
    return ()


def _fixed_filter(table: Table, fixed: Mapping[str, ParamValue]) -> str | None:
    if not fixed:
        return None
    params = {param.name: param for param in table.params}
    predicates: list[Expr] = [
        Comparison(
            "=",
            ColumnRef(params[name].column, "param", params[name]),
            _literal(params[name], fixed[name]),
        )
        for name in sorted(fixed)
    ]
    return sqlrender.render_conjunction(predicates)


def _literal(param: Param, value: ParamValue) -> Literal:
    if param.type == "boolean":
        return Literal("bool", bool(value))
    if param.type in ("integer", "number"):
        return Literal("exactnumeric", Decimal(str(value)))
    return Literal("string", str(value))
