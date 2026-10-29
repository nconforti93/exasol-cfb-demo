"""What-if SQL over the stored seasons of a run (sim-engine/sim-runs, layout A).

A condition fixes the winner of one remaining game: character `PATH_POS` of the run's outcome
strings (`SIM_PATH.OUTCOMES`) is `1` for a home win, `0` for an away win. The matching seasons are
the outcome rows that meet every condition; a team's conditional odds are its playoff rows
(`SIM_TEAM`) among them divided by their number.

Pure standard library, so the serving layer can reuse it over its own views (`Names`):

- `team_sql(run_id, team_id, conditions)`: one row `N_MATCHING`, `N_IN_CFP`, `N_BYE`, `N_CHAMPION`.
- `all_teams_sql(run_id, conditions)`: the same counts per eligible roster team (`TEAM_ID` first),
  zero counts included, ordered by `TEAM_ID`.
- `team_condition(...)`: the condition "this team wins (or loses) this game".

At most `MAX_CONDITIONS` conditions, one per game. Every number must be a plain `int` (not a
`bool`), positions run from 1 to `MAX_POSITION`, and names are upper-case identifiers, written
double-quoted; nothing else enters the text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MAX_CONDITIONS = 16
MAX_POSITION = 2000
HOME_WIN, AWAY_WIN = "1", "0"
_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]*")


class WhatIfError(ValueError):
    """A what-if request that cannot be turned into SQL; nothing was built."""


@dataclass(frozen=True)
class Condition:
    """The remaining game at `path_pos` (`SIM_RUN_GAME.PATH_POS`) ends in a home win or not."""

    path_pos: int
    home_wins: bool


@dataclass(frozen=True)
class Names:
    """The schema and the three objects the SQL reads: outcomes, playoff rows and the roster."""

    schema: str = "CFB_SIM"
    outcomes: str = "SIM_PATH"
    playoff: str = "SIM_TEAM"
    roster: str = "SIM_RUN_TEAM"


def team_condition(
    team_id: int, path_pos: int, *, home_team_id: int, away_team_id: int, team_wins: bool
) -> Condition:
    """The condition that `team_id` wins (or loses) the game at `path_pos`."""
    if team_id == home_team_id:
        return Condition(path_pos, team_wins)
    if team_id == away_team_id:
        return Condition(path_pos, not team_wins)
    raise WhatIfError(
        f"team {team_id} does not play the game at position {path_pos} "
        f"({home_team_id} against {away_team_id})"
    )


def team_sql(
    run_id: int, team_id: int, conditions: Sequence[Condition], names: Names | None = None
) -> str:
    """One row: matching seasons and, among them, the team's playoff, bye and title seasons."""
    chosen = names or Names()
    run = _number(run_id, "run id")
    team = _number(team_id, "team id")
    playoff = _object(chosen, chosen.playoff)
    return f"""{_matching(run, conditions, chosen)}
SELECT COUNT(*) AS "N_MATCHING", COUNT(t."SIM_ID") AS "N_IN_CFP",
       COALESCE(SUM(CASE WHEN t."BYE" THEN 1 ELSE 0 END), 0) AS "N_BYE",
       COALESCE(SUM(CASE WHEN t."CHAMPION" THEN 1 ELSE 0 END), 0) AS "N_CHAMPION"
  FROM "MATCHING" m
  LEFT JOIN (SELECT p."SIM_ID", p."BYE", p."CHAMPION"
               FROM {playoff} p WHERE p."RUN_ID" = {run} AND p."TEAM_ID" = {team}) t
    ON t."SIM_ID" = m."SIM_ID\""""


def all_teams_sql(run_id: int, conditions: Sequence[Condition], names: Names | None = None) -> str:
    """Per eligible roster team: matching seasons and the team's playoff, bye and title seasons."""
    chosen = names or Names()
    run = _number(run_id, "run id")
    playoff = _object(chosen, chosen.playoff)
    roster = _object(chosen, chosen.roster)
    return f"""{_matching(run, conditions, chosen)},
"MATCH_COUNT" AS (SELECT COUNT(*) AS "N_MATCHING" FROM "MATCHING"),
"TEAM_COUNTS" AS (
  SELECT p."TEAM_ID", COUNT(*) AS "N_IN_CFP",
         SUM(CASE WHEN p."BYE" THEN 1 ELSE 0 END) AS "N_BYE",
         SUM(CASE WHEN p."CHAMPION" THEN 1 ELSE 0 END) AS "N_CHAMPION"
    FROM {playoff} p JOIN "MATCHING" m ON m."SIM_ID" = p."SIM_ID"
   WHERE p."RUN_ID" = {run}
   GROUP BY p."TEAM_ID"
)
SELECT r."TEAM_ID", c."N_MATCHING", COALESCE(k."N_IN_CFP", 0) AS "N_IN_CFP",
       COALESCE(k."N_BYE", 0) AS "N_BYE", COALESCE(k."N_CHAMPION", 0) AS "N_CHAMPION"
  FROM {roster} r
 CROSS JOIN "MATCH_COUNT" c
  LEFT JOIN "TEAM_COUNTS" k ON k."TEAM_ID" = r."TEAM_ID"
 WHERE r."RUN_ID" = {run} AND r."IS_ELIGIBLE"
 ORDER BY r."TEAM_ID\""""


def _matching(run: str, conditions: Sequence[Condition], names: Names) -> str:
    filters = [f'o."RUN_ID" = {run}', *_filters(conditions)]
    return (
        f'WITH "MATCHING" AS (SELECT o."SIM_ID" FROM {_object(names, names.outcomes)} o\n'
        f" WHERE {' AND '.join(filters)})"
    )


def _filters(conditions: Sequence[Condition]) -> list[str]:
    if len(conditions) > MAX_CONDITIONS:
        raise WhatIfError(f"at most {MAX_CONDITIONS} conditions per what-if, got {len(conditions)}")
    seen: set[int] = set()
    filters = []
    for condition in conditions:
        position = _number(condition.path_pos, "position")
        if not 1 <= condition.path_pos <= MAX_POSITION:
            raise WhatIfError(f"position {position} is outside 1..{MAX_POSITION}")
        if condition.path_pos in seen:
            raise WhatIfError(f"the game at position {position} appears twice")
        seen.add(condition.path_pos)
        if not isinstance(condition.home_wins, bool):
            raise WhatIfError(f"home_wins of position {position} must be True or False")
        winner = HOME_WIN if condition.home_wins else AWAY_WIN
        filters.append(f"SUBSTR(o.\"OUTCOMES\", {position}, 1) = '{winner}'")
    return filters


def _number(value: object, what: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WhatIfError(f"the {what} must be an integer, got {value!r}")
    return str(value)


def _object(names: Names, name: str) -> str:
    return f"{_quoted(names.schema)}.{_quoted(name)}"


def _quoted(name: str) -> str:
    if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
        raise WhatIfError(f"not an upper-case identifier: {name!r}")
    return f'"{name}"'
