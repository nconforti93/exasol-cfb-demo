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
