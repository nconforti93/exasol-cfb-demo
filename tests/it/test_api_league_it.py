"""Stored travel, week and stats views agree with the model at one real point."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.it.harness import Harness
from tests.synthetic.league import League

pytestmark = pytest.mark.model_readonly
PLAN_VARIANT = "model"


def test_travel_and_next_week_use_stored_game_facts(harness: Harness, league: League) -> None:
    harness.deploy_api()
    api = harness.namespace.api_schema
    rows = harness.query(
        f'SELECT "SITE", "KM_FROM_HOME" FROM {api}."TEAM_TRAVEL" '
        'WHERE "SEASON" = 2026 AND "SITE" = \'HOME\' AND "HAS_RESULT"'
    )
    assert rows and all(Decimal(distance) == 0 for _, distance in rows)
    served = harness.query(
        f'SELECT "GAME_ID", "HOME_AP_RANK", "AWAY_AP_RANK", "RUN_ID" '
        f'FROM {api}."WEEK_GAMES" '
        'WHERE "AS_OF_SEASON" = 2026 AND "AS_OF_WEEK_SEQ" = 9 ORDER BY "GAME_ID"'
    )
    source = harness.query(
        f'SELECT "GAME_ID" FROM {harness.model}."GAME" '
        'WHERE "SEASON" = 2026 AND "WEEK_SEQ" = 10 AND "HAS_TEAMS" '
        'AND "INVOLVES_FBS" ORDER BY "GAME_ID"'
    )
    assert [row[0] for row in served] == [row[0] for row in source]
    assert all(row[3] is None for row in served)


def test_stats_cards_have_point_grain_and_consistent_totals(
    harness: Harness, league: League
) -> None:
    harness.deploy_api()
    api = harness.namespace.api_schema
    movers = harness.query(
        f'SELECT "SP_RATING", "SP_PRIOR_RATING", "SP_DELTA", "RISE_RANK" '
        f'FROM {api}."SP_MOVERS" WHERE "SEASON" = 2026 ORDER BY "RISE_RANK"'
    )
    assert movers
    assert all(Decimal(now) - Decimal(prior) == Decimal(delta) for now, prior, delta, _ in movers)
    assert Decimal(movers[0][2]) == max(Decimal(row[2]) for row in movers)
    conferences = harness.query(
        f'SELECT "NONCONF_GAMES", "NONCONF_WINS", "NONCONF_LOSSES" '
        f'FROM {api}."CONFERENCE_STRENGTH" '
        'WHERE "AS_OF_SEASON" = 2026 AND "AS_OF_WEEK_SEQ" = 9'
    )
    assert conferences
    assert all(
        Decimal(wins) + Decimal(losses) <= Decimal(games) for games, wins, losses in conferences
    )
    road = harness.query(
        f'SELECT "TRIPS", "KM_TRAVELED", "LONGEST_TRIP_KM" '
        f'FROM {api}."ROAD_WARRIORS" '
        'WHERE "AS_OF_SEASON" = 2026 AND "AS_OF_WEEK_SEQ" = 9'
    )
    assert all(
        Decimal(trips) > 0 and Decimal(km) >= Decimal(longest) for trips, km, longest in road
    )
    streaks = harness.query(
        f'SELECT "STREAK_KIND", "STREAK_LENGTH", "WIN_STREAK_RANK" '
        f'FROM {api}."WIN_STREAKS" '
        'WHERE "AS_OF_SEASON" = 2026 AND "AS_OF_WEEK_SEQ" = 9'
    )
    assert streaks
    assert all(
        Decimal(length) > 0 and (kind == "W") == (rank is not None)
        for kind, length, rank in streaks
    )
