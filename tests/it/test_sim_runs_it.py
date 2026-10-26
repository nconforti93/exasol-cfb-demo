"""The first stored simulation and conditional odds on the synthetic league."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pyexasol
import pytest

from engine import whatif
from tests.it.harness import Harness
from tests.synthetic.league import League

pytestmark = pytest.mark.it
PLAN_VARIANT = "model"
SEASON, WEEK, SIMS, BATCH = 2026, 9, 2000, 500


@pytest.fixture(scope="module", autouse=True)
def sim_deployed(harness: Harness, league: League) -> None:
    harness.ensure_sim()


def simulate(harness: Harness, sims: int = SIMS) -> dict[str, Any]:
    (row,) = harness.query_dicts(
        f"EXECUTE SCRIPT {harness.infra}.SIMULATE({SEASON}, {WEEK}, 'regular', "
        f"{sims}, {BATCH}, NULL, 'it')"
    )
    return row


def test_a_run_stores_odds_and_a_what_if_matches_leverage(harness: Harness) -> None:
    result = simulate(harness)
    run_id = int(result["RUN_ID"])
    assert result["RUN_STATUS"] == "DONE"
    assert int(result["N_SIMS"]) == SIMS
    run = harness.query_dicts(f"SELECT * FROM {harness.sim}.SIM_RUN WHERE RUN_ID = {run_id}")[0]
    assert (int(run["RUN_SEED"]), int(run["N_BATCHES"])) == (2026009, 4)
    assert int(run["INSTANCES"]) >= 1
    assert all(
        run[key] for key in ("ENGINE_VERSION", "NUMPY_VERSION", "PYTHON_VERSION", "MODEL_DIGEST")
    )
    assert harness.query(f"SELECT COUNT(*) FROM {harness.sim}.SIM_STAGE")[0][0] == 0
    assert (
        int(
            harness.query(f"SELECT COUNT(*) FROM {harness.sim}.SIM_TEAM WHERE RUN_ID = {run_id}")[
                0
            ][0]
        )
        == SIMS * 12
    )
    paths = harness.query(
        f"SELECT COUNT(*), MIN(LENGTH(OUTCOMES)), MAX(LENGTH(OUTCOMES)) "
        f"FROM {harness.sim}.SIM_PATH WHERE RUN_ID = {run_id}"
    )[0]
    assert tuple(int(value) for value in paths) == (SIMS, int(run["N_GAMES"]), int(run["N_GAMES"]))
    odds = harness.query(
        f"SELECT SUM(P_IN_CFP), SUM(P_CHAMPION) FROM {harness.sim}.SIM_TEAM_ODDS "
        f"WHERE RUN_ID = {run_id}"
    )[0]
    assert abs(Decimal(str(odds[0])) - 12) < Decimal("0.000000001")
    assert abs(Decimal(str(odds[1])) - 1) < Decimal("0.000000001")
    (game,) = harness.query(
        f"SELECT g.GAME_IDX, g.PATH_POS, g.HOME_TEAM_ID FROM {harness.sim}.SIM_RUN_GAME g "
        f"JOIN {harness.sim}.SIM_RUN_TEAM t ON t.RUN_ID = g.RUN_ID "
        f"AND t.TEAM_ID = g.HOME_TEAM_ID WHERE g.RUN_ID = {run_id} "
        "AND g.GAME_ROLE = 'REMAINING' AND t.IS_ELIGIBLE ORDER BY g.GAME_IDX LIMIT 1"
    )
    counts = harness.query(
        whatif.team_sql(
            run_id,
            int(game[2]),
            [whatif.Condition(int(game[1]), True)],
            whatif.Names(schema=harness.sim),
        )
    )[0]
    leverage = harness.query(
        f"SELECT N_HOME, N_HOME_IN_CFP FROM {harness.sim}.LEVERAGE "
        f"WHERE RUN_ID = {run_id} AND TEAM_ID = {int(game[2])} "
        f"AND GAME_IDX = {int(game[0])}"
    )[0]
    assert tuple(int(value) for value in counts[:2]) == tuple(int(value) for value in leverage)


def test_failed_batch_leaves_no_outputs_and_releases_the_lock(harness: Harness) -> None:
    harness.query(
        f"INSERT INTO {harness.sim}.SIM_PARAMS VALUES "
        "('FAIL_BATCH', 2, 'ASSUMED', 'Integration test hook')"
    )
    try:
        with pytest.raises(pyexasol.ExaError, match="fail_batch: batch 2"):
            simulate(harness)
    finally:
        harness.query(f"DELETE FROM {harness.sim}.SIM_PARAMS WHERE PARAM_KEY = 'FAIL_BATCH'")
    (run_id, status, detail) = harness.query(
        f"SELECT RUN_ID, RUN_STATUS, DETAIL FROM {harness.sim}.SIM_RUN ORDER BY RUN_ID DESC LIMIT 1"
    )[0]
    assert status == "FAILED" and "batch 2" in str(detail)
    for table in ("SIM_TEAM", "SIM_PATH", "TEAM_WINS", "CONF_CHAMP", "LEVERAGE"):
        assert (
            harness.query(
                f"SELECT COUNT(*) FROM {harness.sim}.{table} WHERE RUN_ID = {int(run_id)}"
            )[0][0]
            == 0
        )
    assert harness.query(f"SELECT COUNT(*) FROM {harness.sim}.SIM_STAGE")[0][0] == 0
    assert simulate(harness, 1000)["RUN_STATUS"] == "DONE"


def test_rooting_guide_uses_precomputed_branch_counts(harness: Harness) -> None:
    run_id = int(simulate(harness, 1000)["RUN_ID"])
    rows = harness.query_dicts(
        f"SELECT g.*, l.N_HOME, l.N_HOME_IN_CFP, l.N_AWAY, l.N_AWAY_IN_CFP "
        f"FROM {harness.sim}.ROOTING_GUIDE g "
        f"JOIN {harness.sim}.LEVERAGE l ON l.RUN_ID = g.RUN_ID AND l.TEAM_ID = g.TEAM_ID "
        f"JOIN {harness.sim}.SIM_RUN_GAME s ON s.RUN_ID = l.RUN_ID "
        "AND s.GAME_IDX = l.GAME_IDX AND s.GAME_ID = g.GAME_ID "
        f"WHERE g.RUN_ID = {run_id} ORDER BY g.TEAM_ID, g.GUIDE_RANK"
    )
    assert rows
    for row in rows:
        home = int(row["N_HOME"])
        away = int(row["N_AWAY"])
        assert home + away == 1000
        assert bool(row["LOW_SAMPLE"]) == (home < 100 or away < 100)
        if home and away:
            home_prob = float(row["N_HOME_IN_CFP"]) / home
            away_prob = float(row["N_AWAY_IN_CFP"]) / away
            assert abs(float(row["P_IF_HOME_WIN"]) - home_prob) < 1e-9
            assert abs(float(row["P_IF_AWAY_WIN"]) - away_prob) < 1e-9
            assert abs(float(row["LEVERAGE"]) - abs(home_prob - away_prob)) < 1e-9
            if home_prob > away_prob:
                assert int(row["ROOT_FOR_TEAM_ID"]) == int(row["HOME_TEAM_ID"])
            elif away_prob > home_prob:
                assert int(row["ROOT_FOR_TEAM_ID"]) == int(row["AWAY_TEAM_ID"])
            else:
                assert row["ROOT_FOR_TEAM_ID"] is None
    next_week = [row for row in rows if row["IS_NEXT_WEEK"]]
    assert next_week
    assert all(row["NEXT_WEEK_RANK"] is not None for row in next_week)
    assert all(row["NEXT_WEEK_RANK"] is None for row in rows if not row["IS_NEXT_WEEK"])


def test_odds_movement_uses_latest_done_run_per_point(harness: Harness) -> None:
    week8 = int(
        harness.query_dicts(
            f"EXECUTE SCRIPT {harness.infra}.SIMULATE("
            "2026, 8, 'regular', 1000, 500, NULL, 'week 8')"
        )[0]["RUN_ID"]
    )
    first_week9 = int(simulate(harness, 1000)["RUN_ID"])
    last_week9 = int(simulate(harness, 1000)["RUN_ID"])
    point = harness.query_dicts(
        f"SELECT * FROM {harness.sim}.POINT_RUN WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = 9"
    )[0]
    assert (int(point["RUN_ID"]), int(point["PREV_RUN_ID"])) == (last_week9, week8)
    assert last_week9 > first_week9
    current = harness.query_dicts(f"SELECT * FROM {harness.sim}.CURRENT_RUN")
    assert len(current) <= 1
    if current:
        assert int(current[0]["RUN_ID"]) == last_week9
    rows = harness.query_dicts(
        f"SELECT m.TEAM_ID, m.RUN_ID, m.P_IN_CFP, m.PREV_P_IN_CFP, "
        f"m.DELTA_P_IN_CFP, m.MOVE, p.P_IN_CFP AS PRIOR_ODDS "
        f"FROM {harness.sim}.ODDS_MOVEMENT m "
        f"JOIN {harness.sim}.SIM_TEAM_ODDS p "
        f"ON p.RUN_ID = {week8} AND p.TEAM_ID = m.TEAM_ID "
        "WHERE m.AS_OF_SEASON = 2026 AND m.AS_OF_WEEK_SEQ = 9"
    )
    assert rows
    for row in rows:
        assert int(row["RUN_ID"]) == last_week9
        delta = float(row["P_IN_CFP"]) - float(row["PRIOR_ODDS"])
        assert abs(float(row["PREV_P_IN_CFP"]) - float(row["PRIOR_ODDS"])) < 1e-9
        assert abs(float(row["DELTA_P_IN_CFP"]) - delta) < 1e-9
        assert row["MOVE"] == ("UP" if delta >= 0.007 else "DOWN" if delta <= -0.007 else "SAME")
    assert {
        row[0]
        for row in harness.query(
            f"SELECT DISTINCT MOVE FROM {harness.sim}.ODDS_MOVEMENT WHERE RUN_ID = {week8}"
        )
    } == {"NEW"}
