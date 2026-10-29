"""Translate game-id what-if conditions into the engine's stored-season SQL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from engine import whatif as engine_whatif

MAX_CONDITIONS = engine_whatif.MAX_CONDITIONS
METHOD = "conditioning: the simulated seasons in which these results happened"


class ConditionError(ValueError):
    """A what-if condition is invalid before any SQL is built."""


@dataclass(frozen=True)
class Condition:
    game_id: int
    home_wins: bool


def what_if_odds_sql(
    run_id: int,
    conditions: Sequence[Condition],
    games: Mapping[int, int],
    *,
    api_schema: str = "CFB_API",
) -> str:
    """Counts for every eligible team in the given run, using only serving views."""
    positions = _positions(run_id, conditions, games)
    names = _names(api_schema)
    try:
        return engine_whatif.all_teams_sql(run_id, positions, names)
    except engine_whatif.WhatIfError as error:
        raise ConditionError(str(error)) from None


def _positions(
    run_id: int, conditions: Sequence[Condition], games: Mapping[int, int]
) -> list[engine_whatif.Condition]:
    if type(run_id) is not int or run_id <= 0:
        raise ConditionError("run id must be a positive integer")
    if len(conditions) > MAX_CONDITIONS:
        raise ConditionError(f"at most {MAX_CONDITIONS} conditions per what-if")
    positions: list[engine_whatif.Condition] = []
    seen: set[int] = set()
    for condition in conditions:
        if type(condition.game_id) is not int or condition.game_id <= 0:
            raise ConditionError("game id must be a positive integer")
        if not isinstance(condition.home_wins, bool):
            raise ConditionError("home_wins must be True or False")
        if condition.game_id in seen:
            raise ConditionError(f"game {condition.game_id} appears twice")
        seen.add(condition.game_id)
        position = games.get(condition.game_id)
        if type(position) is not int:
            raise ConditionError(f"game {condition.game_id} is not in this what-if run")
        positions.append(engine_whatif.Condition(position, condition.home_wins))
    return positions


def _names(api_schema: str) -> engine_whatif.Names:
    return engine_whatif.Names(
        schema=api_schema,
        outcomes="WHATIF_OUTCOMES",
        playoff="WHATIF_PLAYOFF",
        roster="WHATIF_TEAMS",
    )


def rooting_guide_sql(
    run_id: int,
    team_id: int,
    conditions: Sequence[Condition],
    games: Mapping[int, int],
    *,
    api_schema: str = "CFB_API",
) -> str:
    """Count both outcome branches of each unset coming-week game among matching seasons."""
    if type(team_id) is not int or team_id <= 0:
        raise ConditionError("team id must be a positive integer")
    positions = _positions(run_id, conditions, games)
    excluded = ", ".join(str(condition.game_id) for condition in conditions)
    exclude_sql = f' AND g."GAME_ID" NOT IN ({excluded})' if excluded else ""
    schema = api_schema
    try:
        cte = engine_whatif.matching_paths_sql(run_id, positions, _names(schema))
    except engine_whatif.WhatIfError as error:
        raise ConditionError(str(error)) from None
    return f'''{cte},
"BRANCH_COUNTS" AS (
  SELECT g."GAME_ID", SUBSTR(m."OUTCOMES", g."PATH_POS", 1) AS "HOME_WIN",
         COUNT(*) AS "N", COUNT(p."SIM_ID") AS "N_IN_CFP"
    FROM "{schema}"."WHATIF_GAMES" g
   CROSS JOIN "MATCHING" m
    LEFT JOIN "{schema}"."WHATIF_PLAYOFF" p
      ON p."RUN_ID" = {run_id} AND p."SIM_ID" = m."SIM_ID" AND p."TEAM_ID" = {team_id}
   WHERE g."RUN_ID" = {run_id} AND g."IS_NEXT_WEEK" = TRUE{exclude_sql}
   GROUP BY g."GAME_ID", SUBSTR(m."OUTCOMES", g."PATH_POS", 1)
)
SELECT g."GAME_ID", g."SEASON", g."WEEK_SEQ", g."IS_NEXT_WEEK",
       g."HOME_TEAM_ID", g."HOME_SCHOOL", g."AWAY_TEAM_ID", g."AWAY_SCHOOL",
       COALESCE(h."N", 0) AS "N_HOME", COALESCE(h."N_IN_CFP", 0) AS "N_HOME_IN_CFP",
       COALESCE(a."N", 0) AS "N_AWAY", COALESCE(a."N_IN_CFP", 0) AS "N_AWAY_IN_CFP"
  FROM "{schema}"."WHATIF_GAMES" g
  LEFT JOIN "BRANCH_COUNTS" h ON h."GAME_ID" = g."GAME_ID" AND h."HOME_WIN" = '1'
  LEFT JOIN "BRANCH_COUNTS" a ON a."GAME_ID" = g."GAME_ID" AND a."HOME_WIN" = '0'
 WHERE g."RUN_ID" = {run_id} AND g."IS_NEXT_WEEK" = TRUE{exclude_sql}
 ORDER BY g."GAME_ID"'''
