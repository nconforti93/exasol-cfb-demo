"""The prediction and simulation steps of a replayed weekly refresh, with zero network calls."""

from __future__ import annotations

from typing import Any

import pytest

from tests.it.harness import Harness, Mock

pytestmark = pytest.mark.it
PLAN_VARIANT = "model"


def register_sim_steps(harness: Harness) -> None:
    """Use the model test plan's fetch entries with the real Phase 4 refresh scripts."""
    harness.query(
        f"UPDATE {harness.meta}.REFRESH_STEPS SET SCRIPT_NAME = "
        f"'TEST_CFB_INFRA.REFRESH_PREDICT' WHERE STEP_NO = 200"
    )
    harness.query(
        f"INSERT INTO {harness.meta}.REFRESH_STEPS (STEP_NO, STEP_NAME, SCRIPT_NAME) "
        "VALUES (300, 'SIMULATE', 'TEST_CFB_INFRA.REFRESH_SIMULATE')"
    )


def test_replay_refresh_freezes_predictions_and_simulates(
    harness: Harness, mock: Mock, model_deployed: None
) -> None:
    harness.seed_league()
    harness.ensure_sim()
    register_sim_steps(harness)
    harness.write_league_recordings()
    harness.use_replay()
    try:
        with harness.set_sim_params(REFRESH_N_SIMS=2000, REFRESH_SIMS_PER_BATCH=500):
            rows = harness.refresh(2026, 9, "regular")
    finally:
        harness.reset_properties()
    steps = [(int(row["STEP_NO"]), row["STEP_NAME"], row["STEP_STATUS"]) for row in rows]
    assert steps[0] == (1, "CHECK", "DONE")
    assert steps[-3:] == [
        (100, "MODEL", "DONE"),
        (200, "PREDICT", "DONE"),
        (300, "SIMULATE", "DONE"),
    ]
    assert all(int(row["CALLS"]) == 0 for row in rows)
    refresh_id = int(rows[0]["RUN_ID"])
    (run,) = harness.query_dicts(
        f"SELECT * FROM {harness.sim}.SIM_RUN WHERE LOAD_RUN_ID = {refresh_id}"
    )
    assert (run["RUN_STATUS"], run["RUN_ORIGIN"], int(run["RUN_SEED"])) == (
        "DONE",
        "REFRESH",
        2026009,
    )
    assert (int(run["N_SIMS"]), int(run["SIMS_PER_BATCH"])) == (2000, 500)
    (frozen,) = harness.query(
        f"SELECT COUNT(*) FROM {harness.sim}.PREDICTION "
        f"WHERE FROZEN_BY = 'REFRESH {refresh_id}' AND WEEK = 10"
    )[0]
    assert int(frozen) > 0
    assert mock.hits() == 0


def test_next_simulation_cleans_abandoned_outputs_but_keeps_snapshot(
    harness: Harness, model_deployed: None
) -> None:
    harness.seed_league()
    harness.ensure_sim()

    def simulate() -> dict[str, Any]:
        return harness.query_dicts(
            f"EXECUTE SCRIPT {harness.infra}.SIMULATE(2026, 9, 'regular', 2000, 500, NULL, NULL)"
        )[0]

    old_id = int(simulate()["RUN_ID"])
    harness.query(
        f"UPDATE {harness.sim}.SIM_RUN SET RUN_STATUS = 'RUNNING', ENDED_AT = NULL "
        f"WHERE RUN_ID = {old_id}"
    )
    new_id = int(simulate()["RUN_ID"])
    assert new_id != old_id
    (status,) = harness.query(
        f"SELECT RUN_STATUS FROM {harness.sim}.SIM_RUN WHERE RUN_ID = {old_id}"
    )[0]
    assert status == "ABANDONED"
    for table in ("SIM_TEAM", "SIM_PATH", "TEAM_WINS", "CONF_CHAMP", "LEVERAGE"):
        (count,) = harness.query(
            f"SELECT COUNT(*) FROM {harness.sim}.{table} WHERE RUN_ID = {old_id}"
        )[0]
        assert int(count) == 0, table
    (snapshot,) = harness.query(
        f"SELECT COUNT(*) FROM {harness.sim}.SIM_RUN_TEAM WHERE RUN_ID = {old_id}"
    )[0]
    assert int(snapshot) > 0
