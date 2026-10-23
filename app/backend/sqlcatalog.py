"""Every statement a page reads, as fixed text with pyexasol placeholders, plus the lints that keep
the pages inside the serving schemas (pure).

A statement names only objects of `CFB_API` and, for the quota, `CFB_API_LIVE`, through the
placeholders `{api_schema!q}` and `{api_live_schema!q}` (so a test namespace works too). Every
identifier is double-quoted. A request value enters only through `{name!d}` (a number) or `{name}`
(a quoted literal), which pyexasol escapes; nothing is ever formatted into the text by Python. The
preview statement is not here: only `app/backend/live.py` names the on-demand view.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from pyexasol import ExaFormatter

API_SCHEMA = "api_schema"
API_LIVE_SCHEMA = "api_live_schema"
SCHEMA_CONVERSION = "q"
REQUEST_CONVERSIONS = (None, "d", "f")
TEXT_PARAMS = frozenset({"context", "population", "season_type"})
LIVE_ONLY_VIEW = "GAME" + "_PREVIEW"

SERVING_VIEWS = frozenset(
    {
        "CURRENT_POINT",
        "AS_OF_POINTS",
        "TEAMS",
        "PLAYOFF_ODDS",
        "PUBLISHED_RUNS",
        "ROOTING_GUIDE",
        "FORCED_RUN_ODDS",
        "WHATIF_RUN",
        "WHATIF_GAMES",
        "WHATIF_OUTCOMES",
        "WHATIF_PLAYOFF",
        "ANALYSIS_SETTINGS",
        "PREDICTIONS",
        "SCORECARD_WEEKS",
        "SCORECARD_SEASONS",
        "SCORECARD_CALIBRATION",
        "UPSETS",
        "MATCHUP_SERIES",
        "MATCHUP_GAMES",
        "MATCHUP_NEXT",
        "REFRESH_TIMELINE",
        "FETCHES",
        "GAMES",
        "TEAM_SEASONS",
        "RANKINGS",
        "SP_RATINGS",
    }
)
LIVE_VIEWS = frozenset({"QUOTA"})
ALLOWED_WORDS = frozenset(
    {
        "SELECT",
        "FROM",
        "WHERE",
        "AND",
        "OR",
        "NOT",
        "ORDER",
        "BY",
        "ASC",
        "DESC",
        "DISTINCT",
        "AS",
        "COUNT",
        "MAX",
        "TRUE",
        "FALSE",
        "NULL",
        "IS",
        "LIMIT",
    }
)

_SOURCE = re.compile(r"\b(?:FROM|JOIN)\s+(\S+)", re.IGNORECASE)
_QUALIFIED = re.compile(r'\{(api_schema|api_live_schema)!q\}\."([A-Za-z0-9_]+)"')
_HARD_CODED_SCHEMA = re.compile(r'"[^"{}]+"\s*\.\s*"')
_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VIRTUAL = re.compile(r"CFBD_VS|_VS\b", re.IGNORECASE)


@dataclass(frozen=True)
class Statement:
    """One read statement: fixed text, and the names of the request values it takes."""

    name: str
    sql: str
    params: tuple[str, ...] = ()


def _view(name: str, view: str, where: str = "", order: str = "") -> str:
    schema = API_LIVE_SCHEMA if name == "quota" else API_SCHEMA
    text = f'SELECT * FROM {{{schema}!q}}."{view}"'
    if where:
        text += f" WHERE {where}"
    if order:
        text += f" ORDER BY {order}"
    return text


def _define(name: str, view: str, where: str = "", order: str = "") -> Statement:
    names = re.findall(r"\{(\w+)(?:![a-z])?\}", where)
    params = tuple(
        dict.fromkeys(name for name in names if name not in (API_SCHEMA, API_LIVE_SCHEMA))
    )
    return Statement(name, _view(name, view, where, order), params)


_POINT = '"AS_OF_SEASON" = {season!d} AND "AS_OF_WEEK_SEQ" = {week_seq!d}'
_PAIR = '"TEAM_ID" = {team_id!d} AND "OPPONENT_TEAM_ID" = {opponent_id!d}'
_LATEST_REFRESH = (
    '"RUN_KIND" = \'REFRESH_WEEK\' AND "RUN_ID" = (SELECT MAX("RUN_ID") FROM '
    '{api_schema!q}."REFRESH_TIMELINE" WHERE "RUN_KIND" = \'REFRESH_WEEK\')'
)

_STATEMENTS = (
    Statement("health", "SELECT 1"),
    _define("current_point", "CURRENT_POINT"),
    Statement(
        "as_of_seasons",
        'SELECT DISTINCT "AS_OF_SEASON" FROM {api_schema!q}."AS_OF_POINTS" '
        'WHERE "IS_AFTER_CURRENT" = FALSE ORDER BY "AS_OF_SEASON" DESC',
    ),
    _define(
        "as_of_points",
        "AS_OF_POINTS",
        '"AS_OF_SEASON" = {season!d} AND "IS_AFTER_CURRENT" = FALSE',
        '"AS_OF_WEEK_SEQ"',
    ),
    _define("teams", "TEAMS", order='"SCHOOL"'),
    _define("team", "TEAMS", '"TEAM_ID" = {team_id!d}'),
    _define("odds_rows", "PLAYOFF_ODDS", _POINT, '"ODDS_RANK"'),
    _define("published_run", "PUBLISHED_RUNS", _POINT),
    _define("analysis_settings", "ANALYSIS_SETTINGS", order='"SETTING_NAME"'),
    _define("whatif_run", "WHATIF_RUN"),
    _define("whatif_games", "WHATIF_GAMES", order='"WEEK_SEQ", "START_AT", "GAME_ID"'),
    _define(
        "rooting_guide_top",
        "ROOTING_GUIDE",
        '"RUN_ID" = {run_id!d} AND "TEAM_ID" = {team_id!d} AND "GUIDE_RANK" <= 10',
        '"GUIDE_RANK"',
    ),
    _define(
        "rooting_guide_coming",
        "ROOTING_GUIDE",
        '"RUN_ID" = {run_id!d} AND "TEAM_ID" = {team_id!d} AND "IS_NEXT_WEEK" = TRUE',
        '"NEXT_WEEK_RANK"',
    ),
    _define(
        "predictions_for_week",
        "PREDICTIONS",
        '"SEASON" = {season!d} AND "WEEK_SEQ" = {week_seq!d}',
        '"START_AT", "GAME_ID"',
    ),
    _define(
        "predictions_graded_in_week",
        "PREDICTIONS",
        '"SEASON" = {season!d} AND "GAME_WEEK_SEQ" = {week_seq!d}',
        '"START_AT", "GAME_ID"',
    ),
    _define(
        "scorecard_week",
        "SCORECARD_WEEKS",
        '"SEASON" = {season!d} AND "WEEK_SEQ" = {week_seq!d} AND "POPULATION" = {population}',
        '"FREEZE_KIND"',
    ),
    _define(
        "scorecard_season",
        "SCORECARD_SEASONS",
        f'{_POINT} AND "POPULATION" = {{population}}',
        '"FREEZE_KIND"',
    ),
    _define(
        "scorecard_calibration",
        "SCORECARD_CALIBRATION",
        '"SEASON" = {season!d} AND "POPULATION" = {population}',
        '"FREEZE_KIND", "BUCKET_FROM"',
    ),
    _define(
        "upsets_week",
        "UPSETS",
        '"SEASON" = {season!d} AND "WEEK_SEQ" = {week_seq!d}',
        '"WEEK_RANK", "GAME_ID"',
    ),
    _define("upsets_all_time", "UPSETS", '"ALL_TIME_RANK" <= 25', '"ALL_TIME_RANK", "GAME_ID"'),
    _define("upsets_line_era", "UPSETS", '"LINE_ERA_RANK" <= 25', '"LINE_ERA_RANK", "GAME_ID"'),
    _define("matchup_series", "MATCHUP_SERIES", _PAIR),
    _define("matchup_games", "MATCHUP_GAMES", _PAIR, '"SEASON" DESC, "GAME_ID" DESC'),
    _define("matchup_next", "MATCHUP_NEXT", _PAIR),
    _define("quota", "QUOTA"),
    _define("timeline_latest_run", "REFRESH_TIMELINE", _LATEST_REFRESH, '"STEP_NO"'),
    _define("timeline_run", "REFRESH_TIMELINE", '"RUN_ID" = {run_id!d}', '"STEP_NO"'),
    _define("fetches_for_run", "FETCHES", '"CONTEXT" = {context}', '"REQUESTED_AT", "SLICE_KEY"'),
    Statement(
        "week_games",
        'SELECT "GAME_ID", "START_AT", "IS_COMPLETED" FROM {api_schema!q}."GAMES" '
        'WHERE "SEASON" = {season!d} AND "WEEK" = {week!d} AND "SEASON_TYPE" = {season_type} '
        'ORDER BY "START_AT", "GAME_ID"',
        ("season", "week", "season_type"),
    ),
)
CATALOG: Mapping[str, Statement] = {statement.name: statement for statement in _STATEMENTS}


def parameters(statement: Statement, **values: Any) -> dict[str, Any]:
    """The values for `connection.execute(statement.sql, ...)`: the two schema names and exactly the
    statement's own parameters."""
    missing = [
        name for name in (API_SCHEMA, API_LIVE_SCHEMA, *statement.params) if name not in values
    ]
    if missing:
        raise ValueError(f"statement {statement.name} needs {', '.join(missing)}")
    allowed = {API_SCHEMA, API_LIVE_SCHEMA, *statement.params}
    extra = sorted(set(values) - allowed)
    if extra:
        raise ValueError(f"statement {statement.name} takes no extra value {', '.join(extra)}")
    return dict(values)


def sources(statement: Statement) -> list[tuple[str, str]]:
    """The (schema placeholder, object name) pairs a statement reads."""
    return [(match.group(1), match.group(2)) for match in _QUALIFIED.finditer(statement.sql)]


def lint(statement: Statement) -> list[str]:
    """Why a statement may not be in the catalog; empty when it may."""
    sql = statement.sql
    found = _placeholder_problems(statement)
    if ";" in sql:
        found.append("a statement must not contain ';'")
    if _HARD_CODED_SCHEMA.search(sql):
        found.append("a schema is hard-coded; use the schema placeholders")
    if _VIRTUAL.search(sql):
        found.append("a page statement must not name a virtual schema")
    if LIVE_ONLY_VIEW in sql:
        found.append(f"{LIVE_ONLY_VIEW} may only be named by app/backend/live.py")
    found += _source_problems(sql)
    found += _word_problems(statement)
    return found


def _placeholder_problems(statement: Statement) -> list[str]:
    found: list[str] = []
    used: set[str] = set()
    for _, field, _, conversion in string.Formatter().parse(statement.sql):
        if field is None:
            continue
        if field in (API_SCHEMA, API_LIVE_SCHEMA):
            if conversion != SCHEMA_CONVERSION:
                found.append(f"{field} must be formatted with !{SCHEMA_CONVERSION}")
            continue
        used.add(field)
        if field not in statement.params:
            found.append(f"placeholder {field} is not declared")
        if conversion not in REQUEST_CONVERSIONS:
            found.append(f"request value {field} uses !{conversion}; only !d, !f or a literal")
    found += [f"parameter {name} is never used" for name in statement.params if name not in used]
    return found


def _source_problems(sql: str) -> list[str]:
    found = []
    for match in _SOURCE.finditer(sql):
        token = match.group(1)
        if token.startswith("("):
            continue
        qualified = _QUALIFIED.fullmatch(token)
        if qualified is None:
            found.append(f"source {token} is not a serving-schema object")
            continue
        schema, name = qualified.groups()
        views = SERVING_VIEWS if schema == API_SCHEMA else LIVE_VIEWS
        if name not in views:
            found.append(f"{name} is not a view the pages may read from {schema}")
    return found


def _word_problems(statement: Statement) -> list[str]:
    dummy: dict[str, Any] = {API_SCHEMA: "S1", API_LIVE_SCHEMA: "S2"}
    for _, field, _, _ in string.Formatter().parse(statement.sql):
        if field is not None:
            dummy.setdefault(field, 1)
    try:
        rendered = _render_for_lint(statement.sql, dummy)
    except (ValueError, KeyError, IndexError) as error:
        return [f"the statement cannot be rendered: {error}"]
    bare = _WORD.findall(_LITERAL.sub(" ", rendered))
    return [
        f"unquoted identifier or unknown word {word}"
        for word in bare
        if word.upper() not in ALLOWED_WORDS
    ]


def new_formatter() -> ExaFormatter:
    """pyexasol's SQL formatter without a connection, for rendering a statement in tests."""
    return ExaFormatter(SimpleNamespace(options={"quote_ident": False}))  # type: ignore[no-untyped-call]


def render(statement: Statement, **values: Any) -> str:
    """The statement text as the database receives it, for the given values."""
    return new_formatter().format(statement.sql, **parameters(statement, **values))


def _render_for_lint(sql: str, values: Mapping[str, Any]) -> str:
    return new_formatter().format(sql, **values)
