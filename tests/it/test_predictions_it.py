"""The current-season prediction freeze and grade views on the synthetic league."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.it.harness import Harness
from tests.synthetic.league import League

pytestmark = pytest.mark.it
PLAN_VARIANT = "model"


@pytest.fixture(scope="module", autouse=True)
def sim_deployed(harness: Harness, league: League) -> None:
    harness.ensure_sim()


def test_freeze_keeps_next_week_picks_and_marks_old_games_reconstructed(harness: Harness) -> None:
    expected = harness.query_dicts(
        f"SELECT GAME_ID, MU, SIGMA, HOME_WIN_PROB FROM {harness.sim}.GAME_SCORE_AS_OF "
        "WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = 9 AND GAME_STATUS_AS_OF = 'NEXT'"
    )
    assert expected
    first = harness.script("PREDICT", 2026, 9, "regular")
    assert int(first[0]["FROZEN_GAMES"]) >= len(expected)
    before = {
        int(row["GAME_ID"]): row
        for row in harness.query_dicts(f"SELECT * FROM {harness.sim}.PREDICTION")
    }
    assert all(before[int(game["GAME_ID"])]["FREEZE_KIND"] == "IN_ADVANCE" for game in expected)
    for game in expected:
        frozen = before[int(game["GAME_ID"])]
        assert frozen["FREEZE_WEEK_SEQ"] == 9
        assert (frozen["MU"], frozen["SIGMA"], frozen["HOME_WIN_PROB"]) == (
            game["MU"],
            game["SIGMA"],
            game["HOME_WIN_PROB"],
        )
    assert any(
        row["FREEZE_KIND"] == "RECONSTRUCTED" and row["FREEZE_WEEK_SEQ"] < 9
        for row in before.values()
    )
    with harness.set_sim_params(HFA="5.00"):
        second = harness.script("PREDICT", 2026, 9, "regular")
    assert int(second[0]["FROZEN_GAMES"]) == 0
    after = {
        int(row["GAME_ID"]): row
        for row in harness.query_dicts(f"SELECT * FROM {harness.sim}.PREDICTION")
    }
    assert after == before

    (score,) = harness.query_dicts(
        f"SELECT * FROM {harness.sim}.PREDICTION_SCORECARD WHERE SEASON = 2026 "
        "AND WEEK_SEQ IS NULL AND POPULATION = 'FBS_INVOLVED' AND FREEZE_KIND = 'ALL'"
    )
    assert sum(int(score[key]) for key in ("CORRECT", "WRONG", "TIES")) == int(
        score["GAMES_GRADED"]
    )
    buckets = harness.query(
        f"SELECT SUM(GAMES_GRADED) FROM {harness.sim}.PREDICTION_CALIBRATION "
        "WHERE SEASON = 2026 AND POPULATION = 'FBS_INVOLVED' AND FREEZE_KIND = 'ALL'"
    )[0][0]
    assert int(buckets) == int(score["CORRECT"]) + int(score["WRONG"])


def test_completed_game_probability_uses_line_else_elo(harness: Harness, league: League) -> None:
    line_game = league.game_id("ashford_brookvale_2024")
    (line,) = harness.query_dicts(
        f"SELECT * FROM {harness.sim}.GAME_WIN_PROB WHERE GAME_ID = {line_game}"
    )
    assert line["PROB_SOURCE"] == "LINE"
    assert abs(Decimal(str(line["HOME_PREGAME_WIN_PROB"])) - Decimal("0.70")) < Decimal("0.0001")
    older = harness.query_dicts(
        f"SELECT * FROM {harness.sim}.GAME_WIN_PROB "
        "WHERE SEASON < 2013 AND PROB_SOURCE = 'ELO' ORDER BY SEASON DESC LIMIT 1"
    )
    assert older
    elo = older[0]
    (inputs,) = harness.query_dicts(
        f"SELECT HOME_PREGAME_ELO, AWAY_PREGAME_ELO, IS_NEUTRAL_SITE "
        f"FROM {harness.model}.GAME WHERE GAME_ID = {int(elo['GAME_ID'])}"
    )
    (params,) = harness.query_dicts(
        f"SELECT ELO_PROB_SCALE, ELO_PROB_HFA FROM {harness.sim}.SCORE_PARAMS"
    )
    home_edge = float(inputs["HOME_PREGAME_ELO"]) - float(inputs["AWAY_PREGAME_ELO"])
    if not inputs["IS_NEUTRAL_SITE"]:
        home_edge += float(params["ELO_PROB_HFA"])
    expected = 1.0 / (1.0 + 10 ** (-home_edge / float(params["ELO_PROB_SCALE"])))
    assert abs(float(elo["HOME_PREGAME_WIN_PROB"]) - expected) < 0.0001
    assert abs(float(elo["UPSET_MAGNITUDE"]) + float(elo["WINNER_PREGAME_WIN_PROB"]) - 1.0) < 0.0001


def test_simulate_freezes_games_before_running_the_engine(harness: Harness) -> None:
    (run,) = harness.script("SIMULATE", 2026, 9, "regular", 1000, 500, None, "freeze smoke")
    assert run["RUN_STATUS"] == "DONE"
    run_id = int(run["RUN_ID"])
    (frozen,) = harness.query(
        f"SELECT COUNT(*) FROM {harness.sim}.PREDICTION "
        f"WHERE FROZEN_BY = 'SIMULATE {run_id}' AND FREEZE_WEEK_SEQ = 9"
    )[0]
    assert int(frozen) > 0
