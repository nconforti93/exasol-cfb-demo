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
    names = engine_whatif.Names(
        schema=api_schema,
        outcomes="WHATIF_OUTCOMES",
        playoff="WHATIF_PLAYOFF",
        roster="WHATIF_TEAMS",
    )
    try:
        return engine_whatif.all_teams_sql(run_id, positions, names)
    except engine_whatif.WhatIfError as error:
        raise ConditionError(str(error)) from None
