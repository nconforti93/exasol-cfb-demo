"""`make sim` on Exasol Personal local: the simulation schema, the engine script and the rule that a
redeploy never loses run data (sim-engine/deploy, scenarios 1 and 2).

The tests deploy into `TEST_CFB_SIM` as the harness owner under the loader's `DEPLOY` run lock.
`PREDICTION` joins the second test with slice 2.
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pyexasol
import pytest

from engine import bundle
from engine import deploy as sim_deploy
from engine.deploy import SimOptions
from tests.it.harness import Harness
from vs.host import sqlobjects
from vs.host.namespace import Namespace

pytestmark = pytest.mark.it
PLAN_VARIANT = "model"

NAMESPACE = Namespace("TEST_")
SIM = NAMESPACE.sim_schema
HFA_CHANGED = "3.5"
KEPT_ROWS = {
    "SIM_RUN": (
        "(RUN_STATUS, RUN_ORIGIN, AS_OF_SEASON, AS_OF_WEEK, AS_OF_SEASON_TYPE, AS_OF_WEEK_SEQ, "
        "RUN_SEED, N_SIMS, SIMS_PER_BATCH, N_BATCHES, STARTED_AT) VALUES "
        "('DONE', 'MANUAL', 2026, 7, 'regular', 7, 42, 1000, 500, 2, '2026-10-23 10:00:00')"
    ),
    "SIM_TEAM": "VALUES (1, 0, 101, 1, TRUE, 4, TRUE)",
    "SIM_PATH": "VALUES (1, 0, '1011')",
    "LEVERAGE": "VALUES (1, 101, 0, 600, 480, 400, 120)",
}
ONE_BOGUS_ROW = (
    "SELECT {script}(1, 1, 10, 0, 'BOGUS', "
    + ", ".join(["NULL"] * (len(bundle.MODEL_COLUMNS) - 1))
    + ") FROM DUAL"
)


def number(value: object) -> Decimal:
    """A DECIMAL cell as the driver returns it (an int, a Decimal or a string) as a Decimal."""
    return Decimal(str(value))


def schema_objects(harness: Harness) -> dict[str, tuple[str, str | None]]:
    rows = harness.query(
        "SELECT OBJECT_NAME, OBJECT_TYPE, OBJECT_COMMENT FROM EXA_ALL_OBJECTS "
        "WHERE ROOT_NAME = {schema}",
        {"schema": SIM},
    )
    return {str(name): (str(kind), comment) for name, kind, comment in rows}


def hfa(harness: Harness) -> Decimal:
    return number(harness.query(f"SELECT HFA FROM {SIM}.SCORE_PARAMS")[0][0])


def kept_row_counts(harness: Harness) -> dict[str, int]:
    return {
        table: int(harness.query(f"SELECT COUNT(*) FROM {SIM}.{table}")[0][0])
        for table in KEPT_ROWS
    }


def test_make_sim_installs_schema(harness: Harness) -> None:
    version_before = bundle.engine_version(bundle.script_body())
    report = harness.deploy_sim()
    version_after = bundle.engine_version(bundle.script_body())
    expected = {o.name: o.kind for o in sqlobjects.load(sim_deploy.SIM_DIR)} | {
        o.name: o.kind for o in sqlobjects.load(sim_deploy.DATA_DIR)
    }
    found = schema_objects(harness)
    assert {name: kind for name, (kind, _) in found.items()} == expected
    assert all(comment for _, comment in found.values()), "every object is commented"
    uncommented = harness.query(
        "SELECT COLUMN_TABLE, COLUMN_NAME FROM EXA_ALL_COLUMNS "
        "WHERE COLUMN_SCHEMA = {schema} AND COLUMN_COMMENT IS NULL",
        {"schema": SIM},
    )
    assert uncommented == []
    assert report.objects.comment_percent == 100
    (rules,) = harness.query_dicts(
        f"SELECT SEASON, AUTO_BID_RULE, G6_BID, GUARANTEE_SCHOOL, GUARANTEE_RANK, "
        f"MIN_CONFERENCE_TEAMS, TO_CHAR(SELECTION_AT, 'YYYY-MM-DD HH24:MI') AS SELECTION_TEXT "
        f"FROM {SIM}.SEASON_RULES"
    )
    assert rules == {
        "SEASON": 2026,
        "AUTO_BID_RULE": "POWER_CHAMPIONS_PLUS_G6",
        "G6_BID": "TEAM",
        "GUARANTEE_SCHOOL": "Notre Dame",
        "GUARANTEE_RANK": 12,
        "MIN_CONFERENCE_TEAMS": 6,
        "SELECTION_TEXT": "2026-12-06 12:00",
    }
    (params,) = harness.query_dicts(
        f"SELECT HFA, SIGMA_1, W_SOS, OT_MARGIN FROM {SIM}.SCORE_PARAMS"
    )
    assert {name: number(value) for name, value in params.items()} == {
        "HFA": Decimal("2.5"),
        "SIGMA_1": Decimal("15"),
        "W_SOS": Decimal("2"),
        "OT_MARGIN": Decimal("3"),
    }
    (script,) = harness.query(
        "SELECT SCRIPT_COMMENT, SCRIPT_LANGUAGE FROM EXA_ALL_SCRIPTS "
        "WHERE SCRIPT_SCHEMA = {schema} AND SCRIPT_NAME = {name}",
        {"schema": harness.infra, "name": bundle.SCRIPT_NAME},
    )
    assert report.engine_version in {version_before, version_after}
    assert f"ENGINE_VERSION {report.engine_version}" in str(script[0])
    assert str(script[1]) == "PYTHON3"


def test_the_engine_script_loads_the_engine_inside_the_database(harness: Harness) -> None:
    harness.ensure_sim()
    script = f"{harness.infra}.{bundle.SCRIPT_NAME}"
    with pytest.raises(pyexasol.ExaError, match="unknown row kind"):
        harness.query(ONE_BOGUS_ROW.format(script=script))


def test_redeploy_keeps_runs_and_predictions(harness: Harness, tmp_path: Path) -> None:
    harness.ensure_sim()
    for table, rows in KEPT_ROWS.items():
        harness.query(f"INSERT INTO {SIM}.{table} {rows}")
    before = kept_row_counts(harness)
    assert set(before.values()) == {1}
    changed = tmp_path / "sim"
    shutil.copytree(sim_deploy.SIM_DIR, changed, ignore=shutil.ignore_patterns("data"))
    seed = changed / "010_sim_params.sql"
    seed.write_text(
        seed.read_text(encoding="utf-8").replace("('HFA', 2.5,", f"('HFA', {HFA_CHANGED},"),
        encoding="utf-8",
    )
    try:
        report = harness.deploy_sim(SimOptions(directory=changed))
        assert report.objects.dropped == ()
        assert kept_row_counts(harness) == before
        runs = harness.query(f"SELECT RUN_STATUS, N_SIMS FROM {SIM}.SIM_RUN")
        assert [(str(status), int(n)) for status, n in runs] == [("DONE", 1000)]
        assert harness.query(f"SELECT OUTCOMES FROM {SIM}.SIM_PATH") == [("1011",)]
        assert hfa(harness) == Decimal(HFA_CHANGED)
        assert set(before) <= set(report.objects.kept)
        assert not report.objects.differences
    finally:
        harness.deploy_sim()
    assert kept_row_counts(harness) == before
    assert hfa(harness) == Decimal("2.5")
