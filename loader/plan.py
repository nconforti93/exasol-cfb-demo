"""The load plan: schema, loader and validator for `loader/load-plan.yaml`.

The plan decides which `CFBD_VS` tables are persisted into `CFB_CORE`, in which slices, for which
seasons, when a slice is final and in which profile it is loaded (spec `loader/load-plan`).
`parse_plan` reads the YAML text, checks it against the endpoint catalog and returns a frozen
`LoadPlan`; every problem found is listed in one `PlanError`, each naming the entry and the rule:

    entry 4 (GAMES): gap-outside-seasons: gap 1871 is outside the entry's seasons 1872-current

What the validator checks is what the catalog alone can decide: tables, mirrors, gating, the
parameters a slicing needs, fixed parameters, seasons, gaps, overlaps, refresh steps and config.
The catalog's request rules (for example `plays.spec_required`) are applied by the template
compiler (`loader/templates.py`), because they need the adapter's planner.

`ENTRY_ID` is the 1-based position of an entry in the file. `plan_hash` is the SHA-256 of
`normalized_json(plan)`: the plan without comments, notes and entry ids, with sorted keys and
sorted gaps. It changes when the entry order, a setting or anything that decides a slice changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from vs.cfbd_vs.catalog import Catalog, Param, Table

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN_PATH = ROOT / "loader" / "load-plan.yaml"
DEFAULT_CATALOG_PATH = ROOT / "vs" / "catalog" / "catalog.json"

PLAN_VERSION = 1
PROFILES = ("demo", "full")
SLICINGS = ("none", "season", "season_range")
REFRESH_CLASSES = ("static", "weekly")
SEASON_SLICING = "season"
RANGE_SLICING = "season_range"
NO_SLICING = "none"
STATIC = "static"
FIRST_SEASON = 1869
LAST_SEASON = 2100
MAX_PRIORITY = 9999
MIN_STEP_NO = 100
MAX_STEP_NO = 999
MAX_STEP_NAME = 64
MAX_SCRIPT_NAME = 256
SEASON_PARAM = "year"
RANGE_PARAMS = ("minYear", "maxYear")
SNAPSHOT_SCHEMAS = ("CFB_CORE",)
SNAPSHOT_META_TABLES = ("LOAD_LOG", "FETCH_LOG")
SCOPE_COMPANIONS = {"CFB_CORE": ("LOAD_LOG",)}
"""Meta tables a snapshot schema needs beside it. `LOAD_LOG` says which `CFB_CORE` slices are
stored: a restore that replaced `CFB_CORE` without it would serve replaced or deleted slices as
stored, at zero calls, and never fetch them again (D11)."""
PROFILE_LIST_SEPARATORS = re.compile(r"[+;]")

_IDENTIFIER = r"[A-Z][A-Z0-9_]*"
_SCRIPT = re.compile(rf"{_IDENTIFIER}\.{_IDENTIFIER}")
_MONTH_DAY = re.compile(r"(\d{2})-(\d{2})")
_NOW = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?")
_INTEGER_TEXT = re.compile(r"\d+")
_LEAP_YEAR = 2000

INTEGER_SETTINGS = ("monthly_call_budget", "refresh_reserve", "quarantine_after")
ENTRY_FIELDS = frozenset(
    {
        "table",
        "profile",
        "priority",
        "slicing",
        "seasons",
        "gaps",
        "params",
        "refresh_class",
        "note",
    }
)
REQUIRED_ENTRY_FIELDS = ("table", "profile", "priority", "slicing", "refresh_class")
TOP_LEVEL_KEYS = frozenset({"version", "config", "snapshot", "refresh_steps", "entries"})

ParamValue = int | float | bool | str


@dataclass(frozen=True)
class PlanIssue:
    """One problem: where it is, the rule it breaks and a message that names the values."""

    where: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.rule}: {self.message}"


class PlanError(ValueError):
    """The plan (or an override) is invalid; `issues` lists every problem.

    `entries` are the plan entries that passed their own checks, so a caller can still apply the
    checks that need the adapter's planner and list those problems in the same message.
    """

    def __init__(
        self,
        issues: Sequence[PlanIssue],
        subject: str = "the load plan",
        entries: Sequence[PlanEntry] = (),
    ) -> None:
        self.issues = tuple(issues)
        self.entries = tuple(entries)
        noun = "problem" if len(self.issues) == 1 else "problems"
        lines = [f"{subject} has {len(self.issues)} {noun}:"]
        lines += [f"  - {issue}" for issue in self.issues]
        super().__init__("\n".join(lines))


@dataclass(frozen=True)
class PlanConfig:
    """The loader settings (`config` in the YAML), with the defaults of the spec.

    `now_override` is the test clock; it can only come from a `LOADER_CONFIG` override, never
    from the YAML.
    """

    monthly_call_budget: int = 1000
    refresh_reserve: int = 150
    quarantine_after: int = 2
    season_rollover: str = "08-01"
    season_final_after: str = "02-15"
    refresh_profiles: tuple[str, ...] = ("demo",)
    now_override: str | None = None

    def settings(self) -> dict[str, str]:
        """The `LOADER_CONFIG` rows: upper-case setting name to text value."""
        rows = {
            "MONTHLY_CALL_BUDGET": str(self.monthly_call_budget),
            "REFRESH_RESERVE": str(self.refresh_reserve),
            "QUARANTINE_AFTER": str(self.quarantine_after),
            "SEASON_ROLLOVER": self.season_rollover,
            "SEASON_FINAL_AFTER": self.season_final_after,
            "REFRESH_PROFILES": ",".join(self.refresh_profiles),
        }
        if self.now_override is not None:
            rows["NOW_OVERRIDE"] = self.now_override
        return rows

    def with_overrides(self, overrides: Mapping[str, str]) -> PlanConfig:
        """A copy with `LOADER_CONFIG` overrides applied, validated like the YAML settings.

        Values are text. Profile lists use `+` or `;` (the pairs are comma-separated).
        """
        issues: list[PlanIssue] = []
        raw: dict[str, Any] = {}
        for name, text in overrides.items():
            raw[name] = _coerce_override(name, text, issues)
        merged = {**_config_as_raw(self), **{k: v for k, v in raw.items() if v is not None}}
        config = _build_config(merged, "LOADER_CONFIG", issues, allow_now_override=True)
        if issues or config is None:
            raise PlanError(issues, "LOADER_CONFIG")
        return config


@dataclass(frozen=True)
class SnapshotScope:
    """What `make snapshot` copies besides the mirrors: the schemas and the meta tables."""

    schemas: tuple[str, ...] = SNAPSHOT_SCHEMAS
    meta_tables: tuple[str, ...] = SNAPSHOT_META_TABLES


def scope_problem(schemas: Iterable[str], meta_tables: Iterable[str]) -> str | None:
    """Why a snapshot scope is unsafe (`SCOPE_COMPANIONS`), or None when it is safe."""
    present = set(meta_tables)
    for schema in schemas:
        missing = [name for name in SCOPE_COMPANIONS.get(schema, ()) if name not in present]
        if missing:
            return (
                f"meta_tables must include {', '.join(missing)} while schemas include {schema}: "
                f"{', '.join(missing)} says which {schema} slices are stored, so a restore that "
                f"replaced {schema} without it would serve replaced or deleted slices as stored"
            )
    return None


@dataclass(frozen=True)
class RefreshStep:
    """A registered `REFRESH_WEEK` step: `EXECUTE SCRIPT <script>(run_id, season, week, type)`."""

    step_no: int
    name: str
    script: str


@dataclass(frozen=True)
class PlanEntry:
    """One entry of the plan. `seasons_to` None means up to the current season."""

    entry_id: int
    table: str
    profile: str
    priority: int
    slicing: str
    refresh_class: str
    seasons_from: int | None = None
    seasons_to: int | None = None
    gaps: tuple[int, ...] = ()
    params: tuple[tuple[str, ParamValue], ...] = ()
    note: str | None = field(default=None, compare=False)

    @property
    def profile_rank(self) -> int:
        """1 for `demo`, 2 for `full`: the order in which profiles load."""
        return profile_rank(self.profile)

    def fixed_params(self) -> dict[str, ParamValue]:
        """The fixed API parameters of the entry, by API name."""
        return dict(self.params)


def profile_rank(profile: str) -> int:
    """1 for `demo`, 2 for `full`: the order in which profiles load (`full` includes `demo`)."""
    return PROFILES.index(profile) + 1


def sliced_params(slicing: str | None) -> tuple[str, ...]:
    """The API parameters a slicing fills per slice: `year`, `minYear`/`maxYear`, or none."""
    if slicing == SEASON_SLICING:
        return (SEASON_PARAM,)
    return RANGE_PARAMS if slicing == RANGE_SLICING else ()


@dataclass(frozen=True)
class LoadPlan:
    """A validated plan."""

    version: int
    config: PlanConfig
    snapshot: SnapshotScope
    refresh_steps: tuple[RefreshStep, ...]
    entries: tuple[PlanEntry, ...]
    plan_hash: str


def load_plan(path: Path = DEFAULT_PLAN_PATH, catalog: Catalog | None = None) -> LoadPlan:
    """Read, validate and return the plan file; `catalog` defaults to `vs/catalog/catalog.json`."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PlanError([PlanIssue("plan", "plan-format", f"file not found: {path}")]) from None
    return parse_plan(text, catalog or default_catalog())


def default_catalog() -> Catalog:
    return Catalog.from_json(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))


def parse_plan(text: str, catalog: Catalog) -> LoadPlan:
    """Validate YAML `text` against `catalog`; raises `PlanError` listing every problem."""
    issues: list[PlanIssue] = []
    data = _read_yaml(text, issues)
    if data is None:
        raise PlanError(issues)
    plan, entries = _build_plan(data, catalog, issues)
    if issues or plan is None:
        raise PlanError(issues, entries=entries)
    return plan


def parse_config_overrides(text: str) -> dict[str, str]:
    """`k=v,k=v` of `make deploy LOADER_CONFIG=...` as lower-case names and text values."""
    overrides: dict[str, str] = {}
    issues: list[PlanIssue] = []
    for pair in text.split(",") if text.strip() else []:
        name, separator, value = pair.partition("=")
        key = name.strip().lower()
        if not separator or not key:
            issues.append(PlanIssue("override", "override-format", f"{pair!r} is not name=value"))
        elif key in overrides:
            issues.append(PlanIssue("override", "override-format", f"{key!r} is given twice"))
        else:
            overrides[key] = value.strip()
    if issues:
        raise PlanError(issues, "LOADER_CONFIG")
    return overrides


def normalized_json(plan: LoadPlan) -> str:
    """The text `plan_hash` is taken from; see the module docstring."""
    document = {
        "version": plan.version,
        "config": plan.config.settings(),
        "snapshot": {
            "schemas": list(plan.snapshot.schemas),
            "meta_tables": list(plan.snapshot.meta_tables),
        },
        "refresh_steps": [
            {"step_no": s.step_no, "name": s.name, "script": s.script} for s in plan.refresh_steps
        ],
        "entries": [_normalized_entry(entry) for entry in plan.entries],
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _normalized_entry(entry: PlanEntry) -> dict[str, Any]:
    return {
        "table": entry.table,
        "profile": entry.profile,
        "priority": entry.priority,
        "slicing": entry.slicing,
        "refresh_class": entry.refresh_class,
        "seasons_from": entry.seasons_from,
        "seasons_to": entry.seasons_to,
        "gaps": sorted(entry.gaps),
        "params": dict(entry.params),
    }


def _read_yaml(text: str, issues: list[PlanIssue]) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        issues.append(PlanIssue("plan", "plan-format", "the file is not valid YAML"))
        return None
    if not isinstance(data, dict):
        issues.append(PlanIssue("plan", "plan-format", "the plan must be a mapping"))
        return None
    return data


def _build_plan(
    data: dict[str, Any], catalog: Catalog, issues: list[PlanIssue]
) -> tuple[LoadPlan | None, tuple[PlanEntry, ...]]:
    for key in sorted(set(data) - TOP_LEVEL_KEYS, key=str):
        issues.append(PlanIssue("plan", "plan-format", f"unknown top-level key {key!r}"))
    if data.get("version") != PLAN_VERSION:
        issues.append(
            PlanIssue(
                "plan",
                "plan-format",
                f"version must be {PLAN_VERSION}, got {data.get('version')!r}",
            )
        )
    config = _build_config(data.get("config") or {}, "config", issues, allow_now_override=False)
    snapshot = _build_snapshot(data.get("snapshot") or {}, issues)
    steps = _build_steps(data.get("refresh_steps") or [], issues)
    entries = _build_entries(data.get("entries"), catalog, issues)
    if issues or config is None or snapshot is None:
        return None, entries
    plan = LoadPlan(PLAN_VERSION, config, snapshot, steps, entries, plan_hash="")
    digest = hashlib.sha256(normalized_json(plan).encode("utf-8")).hexdigest()
    return replace(plan, plan_hash=digest), entries


# --- config ------------------------------------------------------------------------------------


def _config_as_raw(config: PlanConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "monthly_call_budget": config.monthly_call_budget,
        "refresh_reserve": config.refresh_reserve,
        "quarantine_after": config.quarantine_after,
        "season_rollover": config.season_rollover,
        "season_final_after": config.season_final_after,
        "refresh_profiles": list(config.refresh_profiles),
    }
    if config.now_override is not None:
        raw["now_override"] = config.now_override
    return raw


def _coerce_override(name: str, text: str, issues: list[PlanIssue]) -> Any:
    if name in INTEGER_SETTINGS:
        if _INTEGER_TEXT.fullmatch(text):
            return int(text)
        issues.append(
            PlanIssue("LOADER_CONFIG", "config", f"{name} must be an integer, got {text!r}")
        )
        return None
    if name == "refresh_profiles":
        return [part.strip() for part in PROFILE_LIST_SEPARATORS.split(text)]
    return text


def _build_config(
    raw: Any, where: str, issues: list[PlanIssue], *, allow_now_override: bool
) -> PlanConfig | None:
    if not isinstance(raw, dict):
        issues.append(PlanIssue(where, "config", "config must be a mapping"))
        return None
    start = len(issues)
    known = set(_config_as_raw(PlanConfig())) | ({"now_override"} if allow_now_override else set())
    for key in sorted(set(raw) - known, key=str):
        issues.append(PlanIssue(where, "config", f"unknown setting {key!r}"))
    values = {key: raw[key] for key in known if key in raw}
    config = PlanConfig(**{k: v for k, v in values.items() if k != "refresh_profiles"})
    if "refresh_profiles" in values:
        profiles = values["refresh_profiles"]
        config = replace(
            config,
            refresh_profiles=tuple(profiles) if isinstance(profiles, list) else (),
        )
    _check_config(config, values, where, issues)
    return config if len(issues) == start else None


def _check_config(
    config: PlanConfig, given: Mapping[str, Any], where: str, issues: list[PlanIssue]
) -> None:
    def bad(message: str) -> None:
        issues.append(PlanIssue(where, "config", message))

    typed = {name: getattr(config, name) for name in INTEGER_SETTINGS}
    for name, value in typed.items():
        if not _is_int(value):
            bad(f"{name} must be an integer, got {value!r}")
    budget, reserve, quarantine = (typed[name] for name in INTEGER_SETTINGS)
    if _is_int(budget) and budget < 1:
        bad(f"monthly_call_budget must be at least 1, got {budget}")
    if _is_int(reserve) and reserve < 0:
        bad(f"refresh_reserve must not be negative, got {reserve}")
    elif _is_int(reserve) and _is_int(budget) and budget >= 1 and reserve > budget:
        bad(f"refresh_reserve ({reserve}) must not exceed monthly_call_budget ({budget})")
    if _is_int(quarantine) and quarantine < 1:
        bad(f"quarantine_after must be at least 1, got {quarantine}")
    for name in ("season_rollover", "season_final_after"):
        if not _is_month_day(getattr(config, name)):
            bad(f"{name} must be a valid MM-DD day, got {getattr(config, name)!r}")
    if "refresh_profiles" in given:
        _check_profile_list(given["refresh_profiles"], bad)
    if config.now_override is not None and not _NOW.fullmatch(str(config.now_override)):
        bad(f"now_override must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS, got {config.now_override!r}")


def _check_profile_list(profiles: Any, bad: Any) -> None:
    if not isinstance(profiles, list) or not profiles:
        bad("refresh_profiles must be a non-empty list of profiles")
        return
    for profile in profiles:
        if profile not in PROFILES:
            bad(f"refresh_profiles has unknown profile {profile!r}, expected one of {PROFILES}")
    if len(set(map(str, profiles))) != len(profiles):
        bad("refresh_profiles lists a profile twice")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_month_day(value: Any) -> bool:
    match = _MONTH_DAY.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return False
    try:
        date(_LEAP_YEAR, int(match.group(1)), int(match.group(2)))
    except ValueError:
        return False
    return True


# --- snapshot and steps ------------------------------------------------------------------------


def _build_snapshot(raw: Any, issues: list[PlanIssue]) -> SnapshotScope | None:
    start = len(issues)

    def bad(message: str) -> None:
        issues.append(PlanIssue("snapshot", "snapshot", message))

    if not isinstance(raw, dict):
        bad("snapshot must be a mapping")
        return None
    for key in sorted(set(raw) - {"schemas", "meta_tables"}, key=str):
        bad(f"unknown key {key!r}")
    schemas = _scope_list(raw, "schemas", SNAPSHOT_SCHEMAS, bad)
    meta_tables = _scope_list(raw, "meta_tables", SNAPSHOT_META_TABLES, bad)
    if len(issues) != start:
        return None
    problem = scope_problem(schemas, meta_tables)
    if problem is not None:
        bad(problem)
        return None
    return SnapshotScope(schemas, meta_tables)


def _scope_list(
    raw: Mapping[str, Any], key: str, allowed: tuple[str, ...], bad: Any
) -> tuple[str, ...]:
    if key not in raw:
        return allowed
    value = raw[key]
    if not isinstance(value, list) or not value:
        bad(f"{key} must be a non-empty list")
        return allowed
    for item in value:
        if item not in allowed:
            bad(f"{key} may only contain {', '.join(allowed)}; got {item!r}")
    return tuple(str(item) for item in value)


def _build_steps(raw: Any, issues: list[PlanIssue]) -> tuple[RefreshStep, ...]:
    if not isinstance(raw, list):
        issues.append(PlanIssue("refresh_steps", "refresh-step", "refresh_steps must be a list"))
        return ()
    steps: list[RefreshStep] = []
    for index, item in enumerate(raw, start=1):
        step = _build_step(item, f"refresh_steps[{index}]", issues)
        if step is not None:
            steps.append(step)
    _check_unique(steps, "step_no", lambda s: s.step_no, issues)
    _check_unique(steps, "name", lambda s: s.name, issues)
    return tuple(sorted(steps, key=lambda s: s.step_no))


def _check_unique(steps: list[RefreshStep], label: str, key: Any, issues: list[PlanIssue]) -> None:
    seen: set[Any] = set()
    for step in steps:
        if key(step) in seen:
            value = key(step)
            shown = value if isinstance(value, int) else repr(value)
            issues.append(
                PlanIssue("refresh_steps", "refresh-step", f"{label} {shown} is used twice")
            )
        seen.add(key(step))


def _build_step(item: Any, where: str, issues: list[PlanIssue]) -> RefreshStep | None:
    def bad(message: str) -> None:
        issues.append(PlanIssue(where, "refresh-step", message))

    if not isinstance(item, dict):
        bad("a step must be a mapping with step_no, name and script")
        return None
    start = len(issues)
    for key in sorted(set(item) - {"step_no", "name", "script"}, key=str):
        bad(f"unknown key {key!r}")
    step_no: Any = item.get("step_no")
    name: Any = item.get("name")
    script: Any = item.get("script")
    if not _is_int(step_no) or not MIN_STEP_NO <= step_no <= MAX_STEP_NO:
        bad(f"step_no must be an integer from {MIN_STEP_NO} to {MAX_STEP_NO}, got {step_no!r}")
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_STEP_NAME:
        bad(f"name must be 1 to {MAX_STEP_NAME} characters, got {name!r}")
    if (
        not isinstance(script, str)
        or not _SCRIPT.fullmatch(script)
        or len(script) > MAX_SCRIPT_NAME
    ):
        bad(f"script must be a qualified UPPER_CASE name like CFB_INFRA.M, got {script!r}")
    if len(issues) != start:
        return None
    return RefreshStep(step_no, name, script)


# --- entries -----------------------------------------------------------------------------------


def _build_entries(raw: Any, catalog: Catalog, issues: list[PlanIssue]) -> tuple[PlanEntry, ...]:
    if not isinstance(raw, list):
        issues.append(PlanIssue("plan", "plan-format", "entries must be a list"))
        return ()
    tables = {table.name: table for table in catalog.tables}
    entries: list[PlanEntry] = []
    for position, item in enumerate(raw, start=1):
        entry = _build_entry(position, item, tables, issues)
        if entry is not None:
            entries.append(entry)
    _check_overlaps(entries, issues)
    return tuple(entries)


def _label(position: int, item: Any) -> str:
    table = item.get("table") if isinstance(item, dict) else None
    return f"entry {position} ({table})" if isinstance(table, str) else f"entry {position}"


def _build_entry(
    position: int, item: Any, tables: Mapping[str, Table], issues: list[PlanIssue]
) -> PlanEntry | None:
    where = _label(position, item)

    def bad(rule: str, message: str) -> None:
        issues.append(PlanIssue(where, rule, message))

    if not isinstance(item, dict):
        bad("bad-entry-field", "an entry must be a mapping")
        return None
    start = len(issues)
    _check_fields(item, bad)
    table = _check_table(item, tables, bad)
    profile = _choice(item, "profile", PROFILES, "unknown-profile", bad)
    slicing = _slicing(item, bad)
    refresh_class = _choice(item, "refresh_class", REFRESH_CLASSES, "unknown-refresh-class", bad)
    priority = _priority(item, bad)
    if table is not None and profile == "demo" and table.gated_by:
        bad(
            "gated-in-demo",
            f"{table.name} is gated by the feature {table.gated_by!r}; "
            "the demo profile must work on a free key, so use profile full",
        )
    if table is not None and slicing is not None:
        _check_slicing_params(table, slicing, bad)
    if slicing in (NO_SLICING, RANGE_SLICING) and refresh_class not in (None, STATIC):
        bad("static-required", f"slicing {slicing} entries must have refresh_class static")
    seasons = _seasons(item, slicing, bad)
    gaps = _gaps(item, slicing, seasons, bad)
    params = _params(item, table, slicing, bad)
    if len(issues) != start or None in (table, profile, slicing, refresh_class, priority):
        return None
    assert table is not None and profile and slicing and refresh_class and priority
    note = item.get("note")
    return PlanEntry(
        position,
        table.name,
        profile,
        priority,
        slicing,
        refresh_class,
        seasons[0] if seasons else None,
        seasons[1] if seasons else None,
        gaps,
        params,
        note if isinstance(note, str) else None,
    )


def _check_fields(item: Mapping[str, Any], bad: Any) -> None:
    for key in sorted(set(item) - ENTRY_FIELDS, key=str):
        bad("bad-entry-field", f"unknown field {key!r}")
    for key in REQUIRED_ENTRY_FIELDS:
        if item.get(key) is None:
            bad("bad-entry-field", f"missing field {key!r}")
    if "note" in item and item["note"] is not None and not isinstance(item["note"], str):
        bad("bad-entry-field", "note must be text")


def _check_table(item: Mapping[str, Any], tables: Mapping[str, Table], bad: Any) -> Table | None:
    name = item.get("table")
    if name is None:
        return None
    table = tables.get(name) if isinstance(name, str) else None
    if table is None:
        bad("unknown-table", f"{name!r} is not a table of the catalog")
        return None
    if not table.read_through:
        bad(
            "volatile-table",
            f"{table.name} has no CFB_CORE mirror (volatile), so it cannot be stored",
        )
        return None
    return table


def _choice(
    item: Mapping[str, Any], key: str, allowed: tuple[str, ...], rule: str, bad: Any
) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if value not in allowed:
        bad(rule, f"{key} {value!r} is not one of {', '.join(allowed)}")
        return None
    return str(value)


def _slicing(item: Mapping[str, Any], bad: Any) -> str | None:
    value = item.get("slicing")
    if value is None:
        return None
    if value in SLICINGS:
        return str(value)
    hint = "; season_week slicing is backlog" if value == "season_week" else ""
    bad("unknown-slicing", f"slicing {value!r} is not one of {', '.join(SLICINGS)}{hint}")
    return None


def _priority(item: Mapping[str, Any], bad: Any) -> int | None:
    value = item.get("priority")
    if value is None:
        return None
    if not _is_int(value) or not 1 <= value <= MAX_PRIORITY:
        bad(
            "bad-entry-field",
            f"priority must be an integer from 1 to {MAX_PRIORITY}, got {value!r}",
        )
        return None
    return int(value)


def _check_slicing_params(table: Table, slicing: str, bad: Any) -> None:
    needed = {SEASON_SLICING: (SEASON_PARAM,), RANGE_SLICING: RANGE_PARAMS}.get(slicing, ())
    names = {param.name for param in table.params}
    missing = [name for name in needed if name not in names]
    if missing:
        quoted = " and ".join(repr(name) for name in missing)
        bad(
            "slicing-param",
            f"{table.name} has no {quoted} parameter, which slicing {slicing} needs",
        )


def _seasons(
    item: Mapping[str, Any], slicing: str | None, bad: Any
) -> tuple[int, int | None] | None:
    raw = item.get("seasons")
    if slicing == NO_SLICING:
        if raw is not None:
            bad("unexpected-seasons", "slicing none takes no seasons")
        return None
    if slicing is None:
        return None
    if raw is None:
        bad("missing-seasons", f"slicing {slicing} needs seasons with from")
        return None
    return _season_bounds(raw, slicing, bad)


def _season_bounds(raw: Any, slicing: str, bad: Any) -> tuple[int, int | None] | None:
    if not isinstance(raw, dict) or set(raw) - {"from", "to"} or not _is_int(raw.get("from")):
        bad(
            "bad-seasons",
            "seasons must be a mapping with an integer from and an optional integer to",
        )
        return None
    first, last = raw["from"], raw.get("to")
    if last is not None and not _is_int(last):
        bad(
            "bad-seasons",
            "seasons must be a mapping with an integer from and an optional integer to",
        )
        return None
    ok = True
    if first < FIRST_SEASON or (last is not None and last < FIRST_SEASON):
        bad(
            "season-order",
            f"seasons start in {FIRST_SEASON}; got {first}-{last if last else 'current'}",
        )
        ok = False
    elif last is not None and last < first:
        bad("season-order", f"seasons.to ({last}) is before seasons.from ({first})")
        ok = False
    if first > LAST_SEASON or (last is not None and last > LAST_SEASON):
        bad("season-order", f"seasons end in {LAST_SEASON} at the latest")
        ok = False
    if slicing == RANGE_SLICING and last is None:
        bad("season-order", "slicing season_range requires seasons.to")
        ok = False
    return (first, last) if ok else None


def _gaps(
    item: Mapping[str, Any],
    slicing: str | None,
    seasons: tuple[int, int | None] | None,
    bad: Any,
) -> tuple[int, ...]:
    raw = item.get("gaps")
    if raw is None:
        return ()
    if slicing is not None and slicing != SEASON_SLICING:
        bad("gaps-not-supported", f"gaps need slicing season, not {slicing}")
        return ()
    if not isinstance(raw, list) or not all(_is_int(gap) for gap in raw):
        bad("bad-entry-field", "gaps must be a list of seasons")
        return ()
    for gap in sorted(set(raw)):
        if raw.count(gap) > 1:
            bad("gap-duplicate", f"gap {gap} is listed twice")
        if seasons is not None and not _inside(gap, seasons):
            bad(
                "gap-outside-seasons",
                f"gap {gap} is outside the entry's seasons {seasons[0]}-{seasons[1] or 'current'}",
            )
    return tuple(sorted(set(raw)))


def _inside(season: int, bounds: tuple[int, int | None]) -> bool:
    return bounds[0] <= season <= (bounds[1] if bounds[1] is not None else LAST_SEASON)


def _params(
    item: Mapping[str, Any], table: Table | None, slicing: str | None, bad: Any
) -> tuple[tuple[str, ParamValue], ...]:
    raw = item.get("params")
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        bad("bad-entry-field", "params must be a mapping of API parameter names to values")
        return ()
    if table is None:
        return ()
    known = {param.name: param for param in table.params}
    sliced = sliced_params(slicing)
    fixed: dict[str, ParamValue] = {}
    for name in sorted(raw, key=str):
        if name in sliced:
            bad("slice-param-fixed", f"parameter {name!r} is set by slicing {slicing}")
        elif name not in known:
            bad("unknown-param", f"{table.name} has no parameter {name!r}")
        elif _value_fits(known[name], raw[name], bad):
            fixed[str(name)] = raw[name]
    return tuple(sorted(fixed.items()))


def _value_fits(param: Param, value: Any, bad: Any) -> bool:
    expected = {"integer": "an integer", "number": "a number", "boolean": "true or false"}.get(
        param.type, "a string"
    )
    fits = {
        "integer": _is_int(value),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }.get(param.type, isinstance(value, str))
    if not fits:
        bad("bad-param-value", f"parameter {param.name!r} must be {expected}, got {value!r}")
        return False
    if param.enum is not None and value not in param.enum:
        bad(
            "bad-param-value",
            f"parameter {param.name!r}={value!r} is not in its enum {', '.join(param.enum)}",
        )
        return False
    return True


def _check_overlaps(entries: list[PlanEntry], issues: list[PlanIssue]) -> None:
    for index, later in enumerate(entries):
        for earlier in entries[:index]:
            if (earlier.table, earlier.params) != (later.table, later.params):
                continue
            message = _overlap(earlier, later)
            if message is not None:
                issues.append(
                    PlanIssue(f"entry {later.entry_id} ({later.table})", "overlap", message)
                )


def _overlap(earlier: PlanEntry, later: PlanEntry) -> str | None:
    if NO_SLICING in (earlier.slicing, later.slicing):
        return (
            f"loads {later.table} without seasons, which entry {earlier.entry_id} "
            "also covers; the same rows would be stored twice"
        )
    low = max(_start(earlier), _start(later))
    high = min(_end(earlier), _end(later))
    if low > high:
        return None
    return (
        f"seasons overlap with entry {earlier.entry_id} from {low}; "
        "the same game must not be stored under two slices"
    )


def _start(entry: PlanEntry) -> int:
    return entry.seasons_from if entry.seasons_from is not None else FIRST_SEASON


def _end(entry: PlanEntry) -> int:
    return entry.seasons_to if entry.seasons_to is not None else LAST_SEASON


# --- estimate ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryEstimate:
    """The planned slices of one entry for a date. A gap is a slice that costs no call."""

    entry_id: int
    table: str
    profile: str
    slicing: str
    slices: int
    gaps: int

    @property
    def calls(self) -> int:
        return self.slices - self.gaps


def current_season(config: PlanConfig, today: date) -> int:
    """The calendar year from the `season_rollover` day on, the previous year before it."""
    month, day = (int(part) for part in config.season_rollover.split("-"))
    return today.year if (today.month, today.day) >= (month, day) else today.year - 1


def estimate_plan(plan: LoadPlan, today: date) -> tuple[EntryEstimate, ...]:
    """Per entry, the slices and gaps the plan lists on `today`."""
    current = current_season(plan.config, today)
    return tuple(_estimate_entry(entry, current) for entry in plan.entries)


def weekly_refresh_calls(plan: LoadPlan, today: date) -> int:
    """The calls of one `REFRESH_WEEK`: the current season's slice of each refreshed entry."""
    current = current_season(plan.config, today)
    return sum(
        1
        for entry in plan.entries
        if entry.refresh_class == "weekly"
        and entry.slicing == SEASON_SLICING
        and entry.profile in plan.config.refresh_profiles
        and _start(entry) <= current <= _end(entry)
        and current not in entry.gaps
    )


def _estimate_entry(entry: PlanEntry, current: int) -> EntryEstimate:
    if entry.slicing == SEASON_SLICING:
        first, last = _start(entry), min(_end(entry), current)
        slices = max(0, last - first + 1)
        gaps = sum(1 for gap in entry.gaps if first <= gap <= last)
    else:
        slices, gaps = 1, 0
    return EntryEstimate(entry.entry_id, entry.table, entry.profile, entry.slicing, slices, gaps)


def format_estimate(plan: LoadPlan, today: date) -> list[str]:
    """The `--estimate` report: the season, one line per profile, and the weekly refresh."""
    estimates = estimate_plan(plan, today)
    season = current_season(plan.config, today)
    lines = [f"estimate for {today.isoformat()} (current season {season})"]
    for profile in PROFILES:
        rows = [e for e in estimates if e.profile == profile]
        if rows:
            lines.append(
                f"  {profile}: {len(rows)} entries, {sum(e.slices for e in rows)} slices, "
                f"{sum(e.gaps for e in rows)} gaps, {sum(e.calls for e in rows)} calls"
            )
    lines.append(f"  weekly refresh: {weekly_refresh_calls(plan, today)} calls")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m loader.plan --check [--estimate]`: print every problem, or the counts."""
    parser = argparse.ArgumentParser(prog="python -m loader.plan", description="Check the plan.")
    parser.add_argument("--check", action="store_true", help="validate (always done)")
    parser.add_argument("--estimate", action="store_true", help="print slice and call counts")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN_PATH, help="the plan file")
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="YYYY-MM-DD (UTC)")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
    except PlanError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"plan ok: {len(plan.entries)} entries, hash {plan.plan_hash[:12]}")
    if args.estimate:
        today = args.today or datetime.now(UTC).date()
        print("\n".join(format_estimate(plan, today)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
