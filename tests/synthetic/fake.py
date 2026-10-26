"""Deterministic synthetic CFBD responses, derived from the pinned OpenAPI spec (no network).

Public API, consumed by the mock server and the integration harness:

- `response_for(path, query) -> bytes` / `body_for(path, query) -> object`: the response of a
  concrete request path (`/games/401/preview`) and query mapping. Unknown paths raise
  `UnknownEndpoint`. The same request always returns the same bytes.
- `endpoints()`, `resolve_path(path)`: the spec's path templates and the template match.
- `minimal_filter(table) -> {param: value}`: the smallest parameter set that satisfies every rule
  of the table (path parameters always included, gating ignored).
- `slice_key_for(table)`, `request_for(table)`: the slice key the adapter would plan for the
  minimal filter (explicit defaults applied), and that request as `(path, query)`.
- `oversized_variant(table)`, `invalid_type_variant(table)`: bodies the strict flattener rejects.
- `large_response_for(path, query)`: a body above `LARGE_BODY_BYTES` that still flattens.
- `calendar_body(year)`: the hand-tuned `/calendar` of a season (regular weeks 1 to 15 from the
  first Saturday of September, postseason week 1 in January of the next year).
- `changed_games_body(year, week)`: `/games` after the upstream data changed, for refresh tests.
- `INFO_DEFAULT`: the default `/info` fields (tier `Free`, 1000 calls, none used).

Generated bodies follow the response schema: every field present, 2 elements per array, typed
values, date-times with UTC offsets. Hand-tuned fixtures (games, rankings, games/teams, matchup,
preview) are overlaid on the generated body, so they can only refine values, never drop fields,
and a fixture key that is not in the schema fails loudly.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache, lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote

from vs.cfbd_vs.catalog import Catalog, Column, Param, Rule, Table
from vs.cfbd_vs.flatten import data_columns
from vs.cfbd_vs.plan import build_slice_key
from vs.cfbd_vs.rules import KNOWN_FEATURES, RuleViolation, Value, evaluate
from vs.gen.schema_walk import (
    ArrayNode,
    JsonValue,
    Node,
    ObjectNode,
    Operation,
    Scalar,
    read_operations,
)

ELEMENTS_PER_ARRAY = 2
REGULAR_WEEKS = 15
SATURDAY = 5
DAYS_PER_WEEK = 7
INFO_DEFAULT: Mapping[str, Any] = {
    "patronLevel": 0,
    "tierName": "Free",
    "monthlyLimit": 1000,
    "remainingCalls": 1000,
    "usedCalls": 0,
    "resetAt": "2099-01-01T00:00:00Z",
}
LARGE_BODY_BYTES = 2_100_000
PADDING_KEY = "_padding"

_ROOT = Path(__file__).resolve().parents[2]
_SPEC_GLOB = "vs/openapi/cfbd-openapi-*.json"
_CATALOG_PATH = "vs/catalog/catalog.json"
_PLACEHOLDER = re.compile(r"\{([^/{}]+)\}")
_MAX_REPAIR_ROUNDS = 25
_BASE_DAY = date(2024, 8, 24)
_OFFSETS = ("Z", "-04:00", "+02:00")
_ALL_FEATURES = frozenset(KNOWN_FEATURES)

Query = Mapping[str, str | Sequence[str]]
Patch = Any


class UnknownEndpoint(KeyError):
    """The request path matches no path template of the spec."""


@dataclass(frozen=True)
class _Request:
    template: str
    path_params: Mapping[str, str]
    query: Mapping[str, str]


def endpoints() -> tuple[str, ...]:
    """The path templates of every GET operation, sorted."""
    return tuple(sorted(_operations()))


def resolve_path(path: str) -> tuple[str, dict[str, str]]:
    """Match a concrete path to its template; literal segments beat placeholders."""
    matches: list[tuple[int, str, dict[str, str]]] = []
    for template, pattern in _patterns().items():
        found = pattern.fullmatch(path)
        if found:
            params = {name: unquote(value) for name, value in found.groupdict().items()}
            matches.append((len(params), template, params))
    if not matches:
        raise UnknownEndpoint(path)
    _, template, params = min(matches, key=lambda match: match[:2])
    return template, params


def body_for(path: str, query: Query | None = None) -> Any:
    """The parsed synthetic response of a request; every call returns a fresh object."""
    return json.loads(response_for(path, query))


def response_for(path: str, query: Query | None = None) -> bytes:
    """The synthetic response of a request as compact UTF-8 JSON."""
    return _response_bytes(_request(path, query))


def to_bytes(body: Any) -> bytes:
    """Serialize a body the way `response_for` does."""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def large_response_for(
    path: str, query: Query | None = None, min_bytes: int = LARGE_BODY_BYTES
) -> bytes:
    """A response of at least `min_bytes`, padded with an extra field the flattener ignores.

    Raw-JSON tables keep the padding in `RAW_JSON`, so the strict flattener rejects those.
    """
    body = body_for(path, query)
    if isinstance(body, dict):
        body[PADDING_KEY] = "x" * min_bytes
        return to_bytes(body)
    first = body[0]
    if isinstance(first, dict):
        first[PADDING_KEY] = "x" * min_bytes
        return to_bytes(body)
    while len(to_bytes(body)) < min_bytes:
        body.extend([first] * max(1, min_bytes // (len(to_bytes(first)) + 1)))
    return to_bytes(body)


def minimal_filter(table: Table | str) -> dict[str, Value]:
    """The smallest filter (API parameter to value) that satisfies the rules of the table."""
    return dict(_minimal_filter(_table(table).name))


def slice_key_for(table: Table | str) -> str:
    """The slice key the adapter plans for the minimal filter, explicit defaults applied."""
    resolved = _table(table)
    evaluation = evaluate(resolved, _as_sets(minimal_filter(resolved)), _ALL_FEATURES)
    values = {name: next(iter(options)) for name, options in evaluation.constraints.items()}
    return build_slice_key(resolved, values)


def request_for(table: Table | str) -> tuple[str, dict[str, str]]:
    """`slice_key_for` as a concrete `(path, query)` request."""
    path, _, query = slice_key_for(table).partition("?")
    return path, dict(parse_qsl(query, keep_blank_values=True))


def oversized_variant(table: Table | str) -> Any:
    """A body whose first VARCHAR value is one character too long for its column."""
    resolved = _table(table)
    column = _first_value_column(resolved, lambda c: (c.sql_type or "").startswith("VARCHAR"))
    size = _varchar_size(column)
    return _mutated(resolved, column, "x" * (size + 1))


def _varchar_size(column: Column) -> int:
    found = re.search(r"\((\d+)\)", column.sql_type or "")
    if found is None:
        raise LookupError(f"{column.name} has no VARCHAR size")
    return int(found.group(1))


def invalid_type_variant(table: Table | str) -> Any:
    """A body whose first value column holds a value of the wrong JSON type."""
    resolved = _table(table)
    column = _first_value_column(resolved, lambda c: True)
    wrong: Any = 12345 if (column.sql_type or "").startswith("VARCHAR") else "not-a-valid-value"
    return _mutated(resolved, column, wrong)


def catalog() -> Catalog:
    """The reviewed catalog the minimal filters and variants are derived from."""
    return _catalog()


@lru_cache(maxsize=1)
def _catalog() -> Catalog:
    return Catalog.from_json((_ROOT / _CATALOG_PATH).read_text(encoding="utf-8"))


def _table(table: Table | str) -> Table:
    return _catalog().table(table) if isinstance(table, str) else table


@lru_cache(maxsize=1)
def _operations() -> dict[str, Operation]:
    specs = sorted(_ROOT.glob(_SPEC_GLOB))
    if len(specs) != 1:
        raise RuntimeError(f"expected exactly one pinned OpenAPI spec, found {len(specs)}")
    document = json.loads(specs[0].read_text(encoding="utf-8"))
    return {operation.path: operation for operation in read_operations(document)}


@lru_cache(maxsize=1)
def _patterns() -> dict[str, re.Pattern[str]]:
    return {template: _template_pattern(template) for template in _operations()}


def _template_pattern(template: str) -> re.Pattern[str]:
    parts = _PLACEHOLDER.split(template)
    regex = "".join(
        f"(?P<{part}>[^/]+)" if index % 2 else re.escape(part) for index, part in enumerate(parts)
    )
    return re.compile(regex)


def _request(path: str, query: Query | None) -> _Request:
    template, path_params = resolve_path(path)
    flat = {
        name: value if isinstance(value, str) else (value[0] if value else "")
        for name, value in (query or {}).items()
    }
    return _Request(template, path_params, flat)


def _response_bytes(request: _Request) -> bytes:
    return _cached_bytes(
        request.template,
        tuple(sorted(request.path_params.items())),
        tuple(sorted(request.query.items())),
    )


@lru_cache(maxsize=512)
def _cached_bytes(
    template: str, path_params: tuple[tuple[str, str], ...], query: tuple[tuple[str, str], ...]
) -> bytes:
    request = _Request(template, dict(path_params), dict(query))
    fixture = _FIXTURES.get(template)
    if fixture is None:
        return to_bytes(_generate(_operations()[template].response, (template,)))
    return to_bytes(_fixture_body(template, fixture(request)))


def _fixture_body(template: str, patch: Patch) -> Any:
    body = _generate(_operations()[template].response, (template,))
    return _overlay(body, patch, template)


def _digest(trail: tuple[str | int, ...]) -> int:
    text = "\x1f".join(str(part) for part in trail)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _generate(node: Node, trail: tuple[str | int, ...]) -> Any:
    if isinstance(node, ObjectNode):
        return {f.name: _generate(f.node, (*trail, f.name)) for f in node.fields}
    if isinstance(node, ArrayNode):
        return [_generate(node.items, (*trail, index)) for index in range(ELEMENTS_PER_ARRAY)]
    if isinstance(node, JsonValue):
        return {"synthetic": True, "n": _digest(trail) % 100}
    return _scalar(node, trail)


def _scalar(node: Scalar, trail: tuple[str | int, ...]) -> Any:
    seed = _digest(trail)
    if node.enum:
        return node.enum[seed % len(node.enum)]
    if node.type == "integer":
        return 1 + seed % 999
    if node.type == "number":
        return (seed % 9000) / 10 + 0.25
    if node.type == "boolean":
        return seed % 2 == 0
    return _string(node, trail, seed)


def _string(node: Scalar, trail: tuple[str | int, ...], seed: int) -> str:
    if node.format == "date-time":
        day = _BASE_DAY + timedelta(days=seed % 60)
        offset = _OFFSETS[seed // 7 % len(_OFFSETS)]
        return f"{day.isoformat()}T{12 + seed % 10:02d}:{seed // 3 % 60:02d}:00.000{offset}"
    if node.format == "date":
        return (_BASE_DAY + timedelta(days=seed % 60)).isoformat()
    label = next((str(p) for p in reversed(trail) if isinstance(p, str)), "value")
    return f"{label[:40]} {1 + seed % 99}"


def _overlay(base: Any, patch: Patch, where: str) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        unknown = sorted(set(patch) - set(base))
        if unknown:
            raise ValueError(f"fixture for {where} names fields not in the schema: {unknown}")
        return {
            k: _overlay(v, patch[k], f"{where}.{k}") if k in patch else v for k, v in base.items()
        }
    if isinstance(base, list) and isinstance(patch, list):
        if not base:
            return patch
        return [
            _overlay(base[min(index, len(base) - 1)], item, f"{where}[{index}]")
            for index, item in enumerate(patch)
        ]
    return patch


def _int(query: Mapping[str, str], name: str, fallback: int) -> int:
    try:
        return int(query[name])
    except (KeyError, ValueError):
        return fallback


def _games(request: _Request) -> Patch:
    return _games_patch(_int(request.query, "year", 2024), _int(request.query, "week", 1))


def changed_games_body(year: int = 2024, week: int = 1) -> Any:
    """`/games` after the upstream data changed: game 401628320 now has its final score of 31 to
    24, and a third game (401628321) is new. The same request otherwise returns `_games_patch`."""
    return _fixture_body("/games", _games_patch(year, week, changed=True))


def _games_patch(year: int, week: int, *, changed: bool = False) -> Patch:
    games = _games_unchanged(year, week)
    if not changed:
        return games
    games[1].update(
        {
            "completed": True,
            "homePoints": 31,
            "awayPoints": 24,
            "homePostgameWinProbability": 0.82,
            "awayPostgameWinProbability": 0.18,
        }
    )
    games.append(
        {
            "id": 401628321,
            "season": year,
            "week": week,
            "seasonType": "regular",
            "startDate": f"{year}-09-14T19:00:00.000-05:00",
            "completed": False,
            "homeTeam": "Texas",
            "homeClassification": "fbs",
            "homePoints": 0,
            "awayTeam": "Oklahoma",
            "awayClassification": "fbs",
            "awayPoints": 0,
        }
    )
    return games


def _games_unchanged(year: int, week: int) -> list[dict[str, Any]]:
    return [
        {
            "id": 401628319,
            "season": year,
            "week": week,
            "seasonType": "regular",
            "startDate": f"{year}-08-31T19:30:00.000-04:00",
            "startTimeTBD": False,
            "completed": True,
            "neutralSite": False,
            "conferenceGame": True,
            "attendance": 92746,
            "venueId": 3917,
            "venue": "Sanford Stadium",
            "homeId": 61,
            "homeTeam": "Georgia",
            "homeConference": "SEC",
            "homeClassification": "fbs",
            "homePoints": 34,
            "homeLineScores": [7.0, 10.0, 7.0, 10.0],
            "homePostgameWinProbability": 0.91,
            "homePregameElo": 2087,
            "homePostgameElo": 2094,
            "awayId": 333,
            "awayTeam": "Alabama",
            "awayConference": "SEC",
            "awayClassification": "fbs",
            "awayPoints": 27,
            "awayLineScores": [10.0, 7.0, 3.0, 7.0],
            "awayPostgameWinProbability": 0.09,
            "awayPregameElo": 2071,
            "awayPostgameElo": 2064,
            "excitementIndex": 7.4,
            "highlights": "https://example.invalid/highlights/401628319",
            "notes": "synthetic regular-season game",
            "playoff": {
                "competition": "cfp",
                "format": "12-team",
                "round": "quarterfinal",
                "roundName": "Quarterfinal",
                "bracketSlot": "QF1",
                "homeSeed": 1,
                "awaySeed": 8,
                "bowlName": "Sugar Bowl",
            },
        },
        {
            "id": 401628320,
            "season": year,
            "week": week,
            "seasonType": "regular",
            "startDate": f"{year}-09-07T12:00:00.000Z",
            "completed": False,
            "homeTeam": "Ohio State",
            "homeClassification": "fbs",
            "homePoints": 0,
            "awayTeam": "Michigan",
            "awayClassification": "fbs",
            "awayPoints": 0,
        },
    ]


def _rankings(request: _Request) -> Patch:
    year = _int(request.query, "year", 2024)
    return [
        {
            "season": year,
            "seasonType": "regular",
            "week": week,
            "polls": [
                {
                    "poll": poll,
                    "isFinal": False,
                    "ranks": [
                        {"rank": 1, "teamId": 61, "school": "Georgia", "conference": "SEC"},
                        {"rank": 2, "teamId": 194, "school": "Ohio State", "conference": "Big Ten"},
                    ],
                }
                for poll in ("AP Top 25", "Coaches Poll")
            ],
        }
        for week in (1, 2)
    ]


def _games_teams(request: _Request) -> Patch:
    return [
        {
            "id": game_id,
            "teams": [
                {
                    "teamId": team_id,
                    "team": team,
                    "conference": "SEC",
                    "homeAway": side,
                    "points": points,
                    "stats": [
                        {"category": "totalYards", "stat": "412"},
                        {"category": "rushingYards", "stat": "187"},
                    ],
                }
                for team_id, team, side, points in teams
            ],
        }
        for game_id, teams in (
            (401628319, ((61, "Georgia", "home", 34), (333, "Alabama", "away", 27))),
            (401628320, ((194, "Ohio State", "home", 21), (130, "Michigan", "away", 24))),
        )
    ]


def _matchup(request: _Request) -> Patch:
    team1 = request.query.get("team1", "Georgia")
    team2 = request.query.get("team2", "Alabama")
    return {
        "team1": team1,
        "team2": team2,
        "startYear": _int(request.query, "minYear", 1895),
        "endYear": _int(request.query, "maxYear", 2024),
        "team1Wins": 28,
        "team2Wins": 41,
        "ties": 4,
        "games": [
            {
                "season": 2023,
                "week": 14,
                "seasonType": "postseason",
                "date": "2023-12-02T20:00:00.000Z",
                "neutralSite": True,
                "venue": "Mercedes-Benz Stadium",
                "homeTeam": team1,
                "homeScore": 24,
                "awayTeam": team2,
                "awayScore": 27,
                "winner": team2,
            },
            {
                "season": 2018,
                "week": 15,
                "seasonType": "postseason",
                "date": "2018-12-01T16:00:00.000-05:00",
                "neutralSite": True,
                "venue": "Mercedes-Benz Stadium",
                "homeTeam": team2,
                "homeScore": 35,
                "awayTeam": team1,
                "awayScore": 28,
                "winner": team2,
            },
        ],
    }


def _preview(request: _Request) -> Patch:
    game_id = _int(request.path_params, "gameId", 401628319)
    return {
        "assembledAt": "2024-08-30T14:05:00.000Z",
        "game": {
            "id": game_id,
            "season": 2024,
            "week": 1,
            "seasonType": "regular",
            "startDate": "2024-08-31T19:30:00.000-04:00",
            "status": "scheduled",
            "statusCheckedAt": "2024-08-30T14:00:00.000Z",
            "neutralSite": False,
            "conferenceGame": True,
            "venue": {"id": 3917, "name": "Sanford Stadium", "city": "Athens", "state": "GA"},
            "homeTeam": {"id": 61, "name": "Georgia", "conference": "SEC"},
            "awayTeam": {"id": 333, "name": "Alabama", "conference": "SEC"},
        },
        "availability": "pregame",
    }


def first_saturday_of_september(year: int) -> date:
    """The day week 1 of the regular season starts in the synthetic calendar."""
    first = date(year, 9, 1)
    return first + timedelta(days=(SATURDAY - first.weekday()) % DAYS_PER_WEEK)


def calendar_body(year: int = 2024) -> Any:
    """The hand-tuned `/calendar` of `year`: regular weeks 1 to 15 from the first Saturday of
    September (Saturday to Friday, deterministic), and postseason week 1 in January of year + 1.
    The calendar numbers both season types from week 1."""
    return _fixture_body("/calendar", _calendar(year))


def _calendar(year: int) -> Patch:
    start = first_saturday_of_september(year)
    weeks: list[dict[str, Any]] = []
    for week in range(1, REGULAR_WEEKS + 1):
        first_day = start + timedelta(days=DAYS_PER_WEEK * (week - 1))
        last_day = first_day + timedelta(days=DAYS_PER_WEEK - 1)
        weeks.append(
            {
                "season": year,
                "week": week,
                "seasonType": "regular",
                "startDate": f"{first_day.isoformat()}T00:00:00.000Z",
                "endDate": f"{last_day.isoformat()}T23:59:00.000Z",
                "firstGameStart": f"{first_day.isoformat()}T16:00:00.000Z",
                "lastGameStart": f"{first_day.isoformat()}T23:30:00.000Z",
            }
        )
    weeks.append(
        {
            "season": year,
            "week": 1,
            "seasonType": "postseason",
            "startDate": f"{year + 1}-01-01T00:00:00.000Z",
            "endDate": f"{year + 1}-01-15T23:59:00.000Z",
            "firstGameStart": f"{year + 1}-01-01T17:00:00.000Z",
            "lastGameStart": f"{year + 1}-01-13T00:30:00.000Z",
        }
    )
    return weeks


def _calendar_request(request: _Request) -> Patch:
    return _calendar(_int(request.query, "year", 2024))


def _info(request: _Request) -> Patch:
    return dict(INFO_DEFAULT)


_FIXTURES: dict[str, Callable[[_Request], Patch]] = {
    "/calendar": _calendar_request,
    "/info": _info,
    "/games": _games,
    "/rankings": _rankings,
    "/games/teams": _games_teams,
    "/teams/matchup": _matchup,
    "/games/{gameId}/preview": _preview,
}


def _first_value_column(table: Table, accept: Callable[[Column], bool]) -> Column:
    for column in data_columns(table):
        if column.kind == "value" and not column.json and accept(column):
            return column
    raise LookupError(f"{table.name} has no suitable value column")


def _mutated(table: Table, column: Column, value: Any) -> Any:
    path, query = request_for(table)
    body = body_for(path, query)
    holder, key, element = _first_element(table, body)
    steps = column.path or ()
    if not steps:
        holder[key] = value
        return body
    for step in steps[:-1]:
        element = element[step]
    element[steps[-1]] = value
    return body


def _first_element(table: Table, body: Any) -> tuple[Any, Any, Any]:
    positions = [c for c in data_columns(table) if c.kind == "position"]
    holder: Any = None
    key: Any = None
    element = body
    if positions[0].path is not None:
        holder, key, element = body, 0, body[0]
    for position in positions[1:]:
        steps = position.path or ()
        for step in steps[:-1]:
            element = element[step]
        holder, key = element[steps[-1]], 0
        element = holder[0]
    return holder, key, element


def _as_sets(filters: Mapping[str, Value]) -> dict[str, list[Value]]:
    return {name: [value] for name, value in filters.items()}


@cache
def _minimal_filter(name: str) -> tuple[tuple[str, Value], ...]:
    table = _catalog().table(name)
    protected = _protected(table)
    chosen: dict[str, Value] = {
        p.name: _sample(table, p) for p in table.params if p.name in protected
    }
    for _ in range(_MAX_REPAIR_ROUNDS):
        broken = _broken_rules(table, chosen)
        if not broken:
            break
        for rule in broken:
            _repair(table, rule, chosen, protected)
    else:
        raise RuntimeError(f"{name}: no rule-satisfying filter found")
    for param in reversed(list(chosen)):
        if param not in protected:
            trial = {k: v for k, v in chosen.items() if k != param}
            if not _broken_rules(table, trial):
                chosen = trial
    return tuple((p.name, chosen[p.name]) for p in table.params if p.name in chosen)


def _protected(table: Table) -> set[str]:
    names = {p.name for p in table.params if p.location == "path"}
    for rule in table.rules:
        if rule.type == "required":
            names.update(rule.params)
    return names


def _broken_rules(table: Table, chosen: Mapping[str, Value]) -> list[Rule]:
    try:
        evaluate(table, _as_sets(chosen), _ALL_FEATURES)
    except RuleViolation as error:
        by_id = {rule.id: rule for rule in table.rules}
        return [by_id[violation.rule_id] for violation in error.violations]
    return []


def _repair(table: Table, rule: Rule, chosen: dict[str, Value], protected: set[str]) -> None:
    details = rule.details
    if rule.type == "required":
        _add_all(table, chosen, rule.params)
    elif rule.type == "one_of":
        _add_all(table, chosen, rule.params[:1])
    elif rule.type == "requires_any":
        _add_all(table, chosen, details["any_of"][:1])
    elif rule.type == "row_cap":
        _add_all(table, chosen, details["complete_with"][:1])
    elif rule.type == "requires_value":
        chosen[details["then"]] = details["then_value"]
    elif rule.type == "bounds":
        name = rule.params[0]
        chosen[name] = _sample(table, _param(table, name))
    elif rule.type == "excludes":
        victims = [details["if"], *details["none_of"]]
        for name in victims:
            if name in chosen and name not in protected:
                del chosen[name]
                return
        raise RuntimeError(f"{table.name}: cannot repair rule {rule.id}")


def _add_all(table: Table, chosen: dict[str, Value], names: Sequence[str]) -> None:
    for name in names:
        chosen.setdefault(name, _sample(table, _param(table, name)))


def _param(table: Table, name: str) -> Param:
    for param in table.params:
        if param.name == name:
            return param
    raise RuntimeError(f"{table.name}: rule names unknown parameter {name}")


def _sample(table: Table, param: Param) -> Value:
    low, high = _limits(table, param.name)
    for rule in table.rules:
        if rule.type == "default" and rule.params == (param.name,):
            return rule.details["value"]  # type: ignore[no-any-return]
    if param.enum:
        return param.enum[0]
    if param.type == "boolean":
        return True
    lowered = param.name.lower()
    if param.type in ("integer", "number"):
        base = 2024 if "year" in lowered else 1 if "week" in lowered else 1001
        value = min(max(base, low), high)
        return float(value) if param.type == "number" else value
    if "conference" in lowered:
        return "SEC"
    return "Georgia" if "team" in lowered else "sample"


def _limits(table: Table, name: str) -> tuple[int, int]:
    low, high = -(10**9), 10**9
    for rule in table.rules:
        if rule.type == "bounds" and rule.params == (name,):
            low = max(low, rule.details.get("min", low))
            high = min(high, rule.details.get("max", high))
    return low, high
