"""The serving layer passes one stored simulation and its what-if through unchanged."""

from __future__ import annotations

import pytest

from analyses import whatif
from engine import whatif as engine_whatif
from tests.it.harness import Harness
from tests.synthetic.league import League

pytestmark = pytest.mark.it
PLAN_VARIANT = "model"


def test_current_run_odds_and_whatif_match_the_engine(harness: Harness, league: League) -> None:
    harness.deploy_api()
    before = harness.query(
        f'SELECT "RUN_ID", "WHATIF_RUN_ID" FROM {harness.namespace.api_schema}."CURRENT_POINT"'
    )
    assert before == [(None, None)]

    result = harness.query_dicts(
        f"EXECUTE SCRIPT {harness.infra}.SIMULATE(2026, 9, 'regular', 1000, 500, NULL, 'api-it')"
    )[0]
    assert result["RUN_STATUS"] == "DONE"
    run_id = int(result["RUN_ID"])
    point = harness.query(
        f'SELECT "RUN_ID", "PUBLICATION_ID", "WHATIF_RUN_ID", "HAS_PUBLICATION" '
        f'FROM {harness.namespace.api_schema}."CURRENT_POINT"'
    )[0]
    assert tuple(int(value) for value in point[:3]) == (run_id, run_id, run_id)
    assert point[3] is True

    serving = harness.query(
        f'SELECT "TEAM_ID", "P_IN_CFP" FROM {harness.namespace.api_schema}."PLAYOFF_ODDS" '
        f'WHERE "RUN_ID" = {run_id} ORDER BY "TEAM_ID"'
    )
    source = harness.query(
        f'SELECT "TEAM_ID", "P_IN_CFP" FROM {harness.sim}."ODDS_MOVEMENT" '
        f'WHERE "RUN_ID" = {run_id} ORDER BY "TEAM_ID"'
    )
    assert serving and serving == source
    assert (
        harness.query(
            f'SELECT COUNT(*) FROM {harness.namespace.api_schema}."PUBLISHED_RUNS" '
            f'WHERE "RUN_ID" = {run_id}'
        )[0][0]
        == 1
    )

    games = harness.query(
        f'SELECT "GAME_ID", "PATH_POS" FROM {harness.namespace.api_schema}."WHATIF_GAMES" '
        'ORDER BY "GAME_ID" LIMIT 1'
    )
    assert games
    game_id, position = (int(value) for value in games[0])
    serving_counts = harness.query(
        whatif.what_if_odds_sql(
            run_id,
            [whatif.Condition(game_id, True)],
            {game_id: position},
            api_schema=harness.namespace.api_schema,
        )
    )
    engine_counts = harness.query(
        engine_whatif.all_teams_sql(
            run_id,
            [engine_whatif.Condition(position, True)],
            engine_whatif.Names(schema=harness.sim),
        )
    )
    assert serving_counts and serving_counts == engine_counts

    api = harness.namespace.api_schema
    predictions = harness.query(
        f'SELECT COUNT(*), COUNT("HOME_SCHOOL"), COUNT("PICK_SCHOOL") FROM {api}."PREDICTIONS"'
    )[0]
    source_predictions = harness.query(f'SELECT COUNT(*) FROM {harness.sim}."PREDICTION_GRADED"')[
        0
    ][0]
    assert int(predictions[0]) == int(source_predictions) > 0
    assert int(predictions[1]) == int(predictions[2]) == int(source_predictions)

    scorecard_weeks = harness.query(
        f'SELECT COUNT(*) FROM {api}."SCORECARD_WEEKS" WHERE "WEEK_SEQ" IS NULL'
    )[0][0]
    scorecard_seasons = harness.query(
        f'SELECT COUNT(*) FROM {api}."SCORECARD_SEASONS" WHERE "WEEK_SEQ" IS NOT NULL'
    )[0][0]
    assert int(scorecard_weeks) == int(scorecard_seasons) == 0
    for serving_view, source_view in (
        ("SCORECARD_WEEKS", "PREDICTION_SCORECARD"),
        ("SCORECARD_SEASONS", "PREDICTION_SCORECARD"),
        ("SCORECARD_CALIBRATION", "PREDICTION_CALIBRATION"),
    ):
        predicate = (
            ' WHERE "WEEK_SEQ" IS NOT NULL'
            if serving_view == "SCORECARD_WEEKS"
            else ' WHERE "WEEK_SEQ" IS NULL'
            if serving_view == "SCORECARD_SEASONS"
            else ""
        )
        served = harness.query(f'SELECT COUNT(*) FROM {api}."{serving_view}"')[0][0]
        source = harness.query(f'SELECT COUNT(*) FROM {harness.sim}."{source_view}"{predicate}')[0][
            0
        ]
        assert int(served) == int(source)

    next_week = harness.query(
        f'SELECT w."GAME_ID", w."RUN_ID", w."MODEL_HOME_WIN_PROB", '
        f'p."HOME_WIN_PROB" FROM {api}."WEEK_GAMES" w '
        f'JOIN {api}."PREDICTIONS" p ON p."GAME_ID" = w."GAME_ID" '
        'WHERE w."AS_OF_SEASON" = 2026 AND w."AS_OF_WEEK_SEQ" = 9 LIMIT 1'
    )
    assert next_week and int(next_week[0][1]) == run_id
    assert next_week[0][2] == next_week[0][3]

    next_meetings = harness.query(
        f'SELECT "PREDICTION_KIND", COUNT(*) FROM {api}."MATCHUP_NEXT" GROUP BY "PREDICTION_KIND"'
    )
    kinds = {kind: int(count) for kind, count in next_meetings}
    assert kinds.get("FROZEN", 0) > 0 and kinds.get("CURRENT", 0) > 0
    for kind in ("FROZEN", "CURRENT"):
        (meeting,) = harness.query_dicts(
            f'SELECT m."TEAM_ID", m."GAME_ID", m."TEAM_WIN_PROB", '
            f'm."PRED_TEAM_POINTS", m."PRED_OPPONENT_POINTS", m."STAKES_RUN_ID", '
            f'm."TEAM_P_IN_CFP_IF_WIN", m."TEAM_P_IN_CFP_IF_LOSE", '
            f'g."HOME_TEAM_ID", f."HOME_WIN_PROB" AS "FROZEN_HOME_PROB", '
            f'f."HOME_POINTS_PRED", f."AWAY_POINTS_PRED", '
            f'sg."HOME_WIN_PROB" AS "CURRENT_HOME_PROB", '
            f'own."P_IF_HOME_WIN", own."P_IF_AWAY_WIN" '
            f'FROM {api}."MATCHUP_NEXT" m '
            f'JOIN {harness.model}."GAME" g ON g."GAME_ID" = m."GAME_ID" '
            f'LEFT JOIN {harness.sim}."PREDICTION" f ON f."GAME_ID" = m."GAME_ID" '
            f'LEFT JOIN {harness.sim}."SIM_RUN_GAME" sg '
            f'ON sg."RUN_ID" = {run_id} AND sg."GAME_ID" = m."GAME_ID" '
            f"AND sg.\"GAME_ROLE\" = 'REMAINING' "
            f'LEFT JOIN {harness.sim}."ROOTING_GUIDE" own '
            f'ON own."RUN_ID" = {run_id} AND own."GAME_ID" = m."GAME_ID" '
            f'AND own."TEAM_ID" = m."TEAM_ID" '
            f"WHERE m.\"PREDICTION_KIND\" = '{kind}' "
            'AND m."TEAM_P_IN_CFP_IF_WIN" IS NOT NULL LIMIT 1'
        )
        is_home = meeting["TEAM_ID"] == meeting["HOME_TEAM_ID"]
        home_prob = meeting["FROZEN_HOME_PROB" if kind == "FROZEN" else "CURRENT_HOME_PROB"]
        expected_prob = float(home_prob) if is_home else 1 - float(home_prob)
        assert abs(float(meeting["TEAM_WIN_PROB"]) - expected_prob) < 0.0001
        assert int(meeting["STAKES_RUN_ID"]) == run_id
        assert (
            meeting["TEAM_P_IN_CFP_IF_WIN"]
            == meeting["P_IF_HOME_WIN" if is_home else "P_IF_AWAY_WIN"]
        )
        assert (
            meeting["TEAM_P_IN_CFP_IF_LOSE"]
            == meeting["P_IF_AWAY_WIN" if is_home else "P_IF_HOME_WIN"]
        )
        if kind == "FROZEN":
            assert (
                meeting["PRED_TEAM_POINTS"]
                == meeting["HOME_POINTS_PRED" if is_home else "AWAY_POINTS_PRED"]
            )
            assert (
                meeting["PRED_OPPONENT_POINTS"]
                == meeting["AWAY_POINTS_PRED" if is_home else "HOME_POINTS_PRED"]
            )
        else:
            assert meeting["PRED_TEAM_POINTS"] is None
            assert meeting["PRED_OPPONENT_POINTS"] is None

    elo_upsets = harness.query(
        f'SELECT "SEASON", "ALL_TIME_RANK", "LINE_ERA_RANK" '
        f'FROM {api}."UPSETS" WHERE "PROB_SOURCE" = \'ELO\''
    )
    assert elo_upsets
    assert all(rank is None for _, _, rank in elo_upsets)
    assert any(season >= 1978 and rank is not None for season, rank, _ in elo_upsets)
