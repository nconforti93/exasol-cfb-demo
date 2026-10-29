"""Head-to-head and upsets are derived from stored games with no CFBD request."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.it.harness import Harness
from tests.synthetic.league import League

pytestmark = pytest.mark.model_readonly
PLAN_VARIANT = "model"


def test_series_mirrors_completed_games_and_next_meeting_has_no_run(
    harness: Harness, league: League
) -> None:
    harness.deploy_api()
    api = harness.namespace.api_schema
    teams = harness.query(
        f'SELECT "TEAM_ID", "SCHOOL" FROM {api}."TEAMS" '
        "WHERE \"SCHOOL\" IN ('Ashford', 'Brookvale')"
    )
    ids = {school: int(team_id) for team_id, school in teams}
    a, b = ids["Ashford"], ids["Brookvale"]
    series = harness.query(
        f'SELECT "MEETINGS", "WINS", "LOSSES", "TIES", "STREAK_LENGTH" '
        f'FROM {api}."MATCHUP_SERIES" '
        f'WHERE "TEAM_ID" = {a} AND "OPPONENT_TEAM_ID" = {b}'
    )
    mirror = harness.query(
        f'SELECT "MEETINGS", "WINS", "LOSSES", "TIES" '
        f'FROM {api}."MATCHUP_SERIES" '
        f'WHERE "TEAM_ID" = {b} AND "OPPONENT_TEAM_ID" = {a}'
    )
    games = harness.query(
        f'SELECT "GAME_ID", "TEAM_POINTS", "OPPONENT_POINTS" '
        f'FROM {api}."MATCHUP_GAMES" '
        f'WHERE "TEAM_ID" = {a} AND "OPPONENT_TEAM_ID" = {b}'
    )
    assert series and len(games) == int(series[0][0])
    assert tuple(map(int, mirror[0])) == (
        int(series[0][0]),
        int(series[0][2]),
        int(series[0][1]),
        int(series[0][3]),
    )
    assert int(series[0][4]) > 0
    assert any(points == against for _, points, against in games)
    next_games = harness.query(
        f'SELECT "TEAM_ID", "GAME_ID", "PREDICTION_KIND", "STAKES_RUN_ID" '
        f'FROM {api}."MATCHUP_NEXT" '
        f'WHERE ("TEAM_ID" = {a} AND "OPPONENT_TEAM_ID" = {b}) '
        f'OR ("TEAM_ID" = {b} AND "OPPONENT_TEAM_ID" = {a})'
    )
    assert len(next_games) == 2
    assert len({row[1] for row in next_games}) == 1
    assert all(row[2:] == (None, None) for row in next_games)


def test_line_upsets_have_probability_and_rank_consistency(
    harness: Harness, league: League
) -> None:
    harness.deploy_api()
    rows = harness.query(
        f'SELECT "WINNER_PREGAME_WIN_PROB", "UPSET_MAGNITUDE", '
        f'"PROB_SOURCE", "ALL_TIME_RANK", "LINE_ERA_RANK", "MODEL_WINNER_WIN_PROB" '
        f'FROM {harness.namespace.api_schema}."UPSETS" '
        'WHERE "PROB_SOURCE" = \'LINE\' ORDER BY "ALL_TIME_RANK"'
    )
    assert rows
    assert all(
        Decimal(prob) < Decimal("0.5")
        and abs(Decimal(magnitude) + Decimal(prob) - 1) < Decimal("0.0001")
        and source == "LINE"
        and all_rank is not None
        and line_rank is not None
        and model_prob is None
        for prob, magnitude, source, all_rank, line_rank, model_prob in rows
    )
