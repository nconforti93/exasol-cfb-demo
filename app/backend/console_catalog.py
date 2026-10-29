"""The four curated console statements over the virtual schema, with their expected plans (pure).

The console runs these, and only these, as a reader of the virtual schema (beat 2): a year range
that fans out, a contradiction that costs no call, 157 seasons of stored history that cost no call,
and a query over the budget that is refused before it is sent. There is no free text and no input:
the only thing substituted is the virtual schema's name, checked as a plain identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VS_PLACEHOLDER = "{VS}"
FIRST_STORED_SEASON = 1869
LAST_STORED_SEASON = 2025
FAN_OUT_SEASONS = (2022, 2023, 2024)
STORED_SEASONS = range(FIRST_STORED_SEASON, LAST_STORED_SEASON + 1)
MAX_CALLS_PER_QUERY = 10
ALLOWED_WORDS = frozenset(
    {"SELECT", "FROM", "WHERE", "AND", "BETWEEN", "COUNT", "AS", "GROUP", "BY", "ORDER"}
)
HELP_TEXT = (
    "These four queries run as an ordinary reader of the virtual schema. The pages read stored "
    "history too, for example the head-to-head series since 1897: its plan reads "
    '"no virtual table: 0 calls".'
)

_SCHEMA_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_YEARS_MARKER = "@YEARS@"
_GAMES_TEAMS = (
    f'SELECT * FROM {VS_PLACEHOLDER}."GAMES_TEAMS" WHERE "P_YEAR" {_YEARS_MARKER} '
    "AND \"P_TEAM\" = 'Michigan'"
)


@dataclass(frozen=True)
class ExpectedPlan:
    """What `EXPLAIN VIRTUAL` must show for a query, and the most calls a run may spend."""

    table: str
    slice_keys: tuple[str, ...]
    fetches: bool
    budget: int | None
    max_live_calls: int
    refused: str | None = None


@dataclass(frozen=True)
class ConsoleQuery:
    id: str
    title: str
    summary: str
    template: str
    expected: ExpectedPlan


def _games_teams(years: str) -> str:
    return _GAMES_TEAMS.replace(_YEARS_MARKER, years)


def _team_slice(year: int) -> str:
    return f"/games/teams?seasonType=both&team=Michigan&year={year}"


def _games_slice(year: int) -> str:
    return f"/games?seasonType=both&year={year}"


CONSOLE_QUERIES: tuple[ConsoleQuery, ...] = (
    ConsoleQuery(
        "fan-out",
        "A year range fans out",
        "Michigan's games over 2022 to 2024: three requests, fetched in parallel.",
        _games_teams("BETWEEN 2022 AND 2024"),
        ExpectedPlan(
            "GAMES_TEAMS",
            tuple(_team_slice(year) for year in FAN_OUT_SEASONS),
            True,
            MAX_CALLS_PER_QUERY,
            len(FAN_OUT_SEASONS),
        ),
    ),
    ConsoleQuery(
        "contradiction",
        "A contradiction costs no call",
        "The same table with year = 2023 AND year = 2024: no request, no rows.",
        _games_teams('= 2023 AND "P_YEAR" = 2024'),
        ExpectedPlan("GAMES_TEAMS", (), False, None, 0),
    ),
    ConsoleQuery(
        "stored",
        "Stored history costs no call",
        "Every stored season from 1869 to 2025, counted per stored slice: no fetch, no budget.",
        'SELECT "REQ_SLICE", "REQ_SOURCE", "REQ_STATUS", "REQ_ATTEMPTS", "REQ_MS", "REQ_AT", '
        f'COUNT(*) AS "REQ_ROWS" FROM {VS_PLACEHOLDER}."GAMES" '
        f'WHERE "P_YEAR" BETWEEN {FIRST_STORED_SEASON} AND {LAST_STORED_SEASON} '
        "AND \"REQ_SOURCE\" = 'STORED' "
        'GROUP BY "REQ_SLICE", "REQ_SOURCE", "REQ_STATUS", "REQ_ATTEMPTS", "REQ_MS", "REQ_AT" '
        'ORDER BY "REQ_SLICE"',
        ExpectedPlan(
            "GAMES",
            tuple(
                _games_slice(year) for year in range(FIRST_STORED_SEASON, LAST_STORED_SEASON + 1)
            ),
            False,
            None,
            0,
        ),
    ),
    ConsoleQuery(
        "over-budget",
        "A query over the budget is refused",
        "The first query over 2010 to 2024: 15 requests against a budget of 10, refused up front.",
        _games_teams("BETWEEN 2010 AND 2024"),
        ExpectedPlan(
            "GAMES_TEAMS",
            (),
            False,
            MAX_CALLS_PER_QUERY,
            0,
            refused="15 requests are planned but MAX_CALLS_PER_QUERY is 10",
        ),
    ),
)


def get(query_id: str) -> ConsoleQuery:
    """The console query with this id; `KeyError` for anything else."""
    for item in CONSOLE_QUERIES:
        if item.id == query_id:
            return item
    raise KeyError(query_id)


def sql(query: ConsoleQuery, vs_schema: str) -> str:
    """The statement text over the named virtual schema (`CFBD_VS`, or `TEST_CFBD_VS` in tests)."""
    if not _SCHEMA_NAME.fullmatch(vs_schema):
        raise ValueError("the virtual schema name must be a plain upper-case identifier")
    return query.template.replace(VS_PLACEHOLDER, f'"{vs_schema}"')
