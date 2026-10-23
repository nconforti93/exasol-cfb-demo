"""The model deployer and the model side of the harness, on Exasol Personal local.

Proves what the unit tests can only fake: the transaction, the compile pass, the read-back, the
orphan drop and the run lock of `vs/host/sqlobjects.py` / `model/deploy.py`; seeding the synthetic
league by CSV import (types, NULLs, booleans, the loader's views over it); the replay recordings;
`hold_run_lock`; and the `PERF_` namespace without a virtual schema. The deploy scenarios of the
specs on fixture directories are task 2.5; the model objects have their own test files.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from loader.runlock import RunLockRefused
from model import deploy as model_deploy
from tests.it import model_support
from tests.it.harness import Harness
from tests.synthetic import league as league_module
from tests.synthetic import scaled_league
from tests.synthetic.league import League
from tests.synthetic.seed_sql import seed_columns, seed_rows
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace

PLAN_VARIANT = "model"
pytestmark = pytest.mark.it

SEEDS = {
    "ERA": "010_era.sql",
    "CLASSIFICATION_LABEL": "011_classification_label.sql",
    "CONFERENCE_TIER": "012_conference_tier.sql",
    "CONFERENCE_CHAMPIONSHIP": "013_conference_championship.sql",
    "POLL": "014_poll.sql",
    "DATASET": "015_dataset.sql",
    "CLASSIFICATION_TRANSITION": "016_classification_transition.sql",
}

GOOD_TABLE = """\
CREATE OR REPLACE TABLE {MODEL_SCHEMA}.KEYED (
  K DECIMAL(4,0) NOT NULL COMMENT IS 'Key',
  V VARCHAR(20) UTF8 COMMENT IS 'Value'
) COMMENT IS 'One row per key.';

INSERT INTO {MODEL_SCHEMA}.KEYED VALUES (1, 'a'), (2, 'b');
"""

VIEW_OVER_TABLE = """\
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.KEYED_VIEW (
  K COMMENT IS 'Key',
  V COMMENT IS 'Value'
) AS
SELECT "K", "V" FROM {MODEL_SCHEMA}."KEYED"
COMMENT IS 'One row per key of the table.';
"""


KEYED_AS_VIEW = """\
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.KEYED (
  K COMMENT IS 'Key',
  V COMMENT IS 'Value'
) AS
SELECT "ID", "SEASON_TYPE" FROM {CORE_SCHEMA}."GAMES"
COMMENT IS 'One row per game, under the name of a former table.';
"""


def plain(value: Any) -> Any:
    """A cell comparable across the seed parser (`int`, `Decimal`, `bool`, `str`) and the driver."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    return Decimal(value).normalize()


def write(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def model_objects(harness: Harness) -> dict[str, str]:
    rows = harness.query(
        "SELECT OBJECT_NAME, OBJECT_TYPE FROM EXA_ALL_OBJECTS "
        f"WHERE ROOT_NAME = '{harness.model}' ORDER BY OBJECT_NAME"
    )
    return {str(name): str(kind) for name, kind in rows}


@pytest.fixture(autouse=True)
def real_model_afterwards(harness: Harness) -> Iterator[None]:
    """Every test starts from, and leaves, the deployed `sql/model/` (fixture files are orphans)."""
    yield
    harness.ensure_model()


# --- the deployer ---------------------------------------------------------------------------


def test_the_seed_tables_are_deployed_with_their_rows_and_comments(harness: Harness) -> None:
    report = harness.deploy_model()
    objects = model_objects(harness)
    assert {name for name, kind in objects.items() if kind == "TABLE"} == set(SEEDS)
    for table, file in SEEDS.items():
        count = harness.query(f"SELECT COUNT(*) FROM {harness.model}.{table}")[0][0]
        assert int(count) == len(seed_rows(file)), table
    assert report.objects.comments_found == report.objects.comments_expected
    assert report.objects.comment_percent == 100
    assert "7 tables" in "\n".join(report.lines)
    assert harness.query(
        f"SELECT OBJECT_COMMENT FROM EXA_ALL_OBJECTS WHERE ROOT_NAME = '{harness.model}' "
        "AND OBJECT_NAME = 'POLL'"
    )[0][0].startswith("One row per CFBD poll name")


def test_reference_values_survive_the_engine_with_their_types(harness: Harness) -> None:
    harness.ensure_model()
    stored = harness.query(f"SELECT * FROM {harness.model}.ERA ORDER BY ERA_KIND, ERA_CODE")
    assert [tuple(plain(cell) for cell in row) for row in stored] == sorted(
        tuple(plain(cell) for cell in row) for row in seed_rows("010_era.sql")
    )
    assert seed_columns("010_era.sql")[:2] == ["ERA_KIND", "ERA_CODE"]


def test_a_second_deploy_keeps_the_same_rows_without_duplicates(harness: Harness) -> None:
    harness.deploy_model()
    first = {t: harness.query(f"SELECT COUNT(*) FROM {harness.model}.{t}")[0][0] for t in SEEDS}
    objects = model_objects(harness)
    report = harness.deploy_model()
    second = {t: harness.query(f"SELECT COUNT(*) FROM {harness.model}.{t}")[0][0] for t in SEEDS}
    assert first == second
    assert model_objects(harness) == objects
    assert report.objects.dropped == ()


def test_a_view_over_a_table_deploys_compiles_and_survives_the_tables_replacement(
    harness: Harness, tmp_path: Path
) -> None:
    folder = write(
        tmp_path / "m", {"100_keyed.sql": GOOD_TABLE, "200_keyed_view.sql": VIEW_OVER_TABLE}
    )
    harness.deploy_model(folder)
    harness.deploy_model(folder)
    assert harness.query(f"SELECT K, V FROM {harness.model}.KEYED_VIEW ORDER BY K") == [
        (1, "a"),
        (2, "b"),
    ]
    status = harness.query(
        "SELECT DISTINCT STATUS FROM EXA_ALL_COLUMNS "
        f"WHERE COLUMN_SCHEMA = '{harness.model}' AND COLUMN_TABLE = 'KEYED_VIEW'"
    )
    assert status == [(None,)]


def test_a_view_in_an_earlier_file_may_read_a_later_one(harness: Harness, tmp_path: Path) -> None:
    folder = write(
        tmp_path / "m", {"100_keyed_view.sql": VIEW_OVER_TABLE, "200_keyed.sql": GOOD_TABLE}
    )
    harness.deploy_model(folder)
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.KEYED_VIEW") == [(2,)]
    comments = harness.query(
        "SELECT COLUMN_NAME, COLUMN_COMMENT FROM EXA_ALL_COLUMNS "
        f"WHERE COLUMN_SCHEMA = '{harness.model}' AND COLUMN_TABLE = 'KEYED_VIEW' "
        "ORDER BY COLUMN_ORDINAL_POSITION"
    )
    assert comments == [("K", "Key"), ("V", "Value")]


def test_a_view_that_only_fails_at_its_first_query_fails_the_deploy_and_changes_nothing(
    harness: Harness, tmp_path: Path
) -> None:
    harness.deploy_model()
    before = model_objects(harness)
    rows_before = harness.query(f"SELECT COUNT(*) FROM {harness.model}.ERA")
    folder = write(
        tmp_path / "m",
        {
            "100_keyed.sql": GOOD_TABLE,
            "200_broken_view.sql": VIEW_OVER_TABLE.replace("KEYED_VIEW", "BROKEN_VIEW").replace(
                'SELECT "K", "V"', 'SELECT "K", "NO_SUCH_COLUMN"'
            ),
        },
    )
    with pytest.raises(DeployError, match=r"compile 200_broken_view\.sql: VIEW BROKEN_VIEW"):
        harness.deploy_model(folder)
    assert model_objects(harness) == before
    assert harness.query(f"SELECT COUNT(*) FROM {harness.model}.ERA") == rows_before
    assert "KEYED" not in model_objects(harness)


def test_objects_that_no_file_defines_are_dropped(harness: Harness, tmp_path: Path) -> None:
    folder = write(
        tmp_path / "m", {"100_keyed.sql": GOOD_TABLE, "200_keyed_view.sql": VIEW_OVER_TABLE}
    )
    harness.deploy_model(folder)
    assert {"KEYED", "KEYED_VIEW"} <= set(model_objects(harness))
    report = harness.deploy_model()
    assert {"TABLE KEYED", "VIEW KEYED_VIEW"} <= set(report.objects.dropped)
    assert not {"KEYED", "KEYED_VIEW"} & set(model_objects(harness))


def test_an_object_that_changes_its_kind_is_replaced(harness: Harness, tmp_path: Path) -> None:
    as_table = write(tmp_path / "t", {"100_keyed.sql": GOOD_TABLE})
    as_view = write(tmp_path / "v", {"100_keyed.sql": KEYED_AS_VIEW})
    harness.deploy_model(as_table)
    report = harness.deploy_model(as_view)
    assert report.objects.retyped == ("KEYED (was TABLE)",)
    assert model_objects(harness)["KEYED"] == "VIEW"


def test_a_lint_problem_sends_nothing(harness: Harness, tmp_path: Path) -> None:
    harness.deploy_model()
    folder = write(tmp_path / "m", {"100_bad.sql": GOOD_TABLE.replace("One row per key", "Keys")})
    with pytest.raises(model_deploy.ModelLintError, match="nothing was sent"):
        harness.deploy_model(folder)
    assert "KEYED" not in model_objects(harness)


def test_a_deploy_is_refused_while_a_loader_run_holds_the_lock(harness: Harness) -> None:
    harness.deploy_model()
    before = model_objects(harness)
    with harness.hold_run_lock("RESTORE") as lock:
        with pytest.raises(RunLockRefused, match=f"RESTORE run {lock.run_id} is running"):
            harness.deploy_model()
        assert [(r["RUN_KIND"], r["RUN_STATUS"]) for r in harness.runs()] == [
            ("DEPLOY", "DONE"),
            ("RESTORE", "RUNNING"),
        ]
    assert model_objects(harness) == before
    harness.deploy_model()
    runs = harness.runs()
    assert [(r["RUN_KIND"], r["RUN_STATUS"]) for r in runs] == [
        ("DEPLOY", "DONE"),
        ("RESTORE", "DONE"),
        ("DEPLOY", "DONE"),
    ]
    assert runs[-1]["RUN_ARGS"] == "make model"
    assert runs[-1]["DETAIL"].startswith(f"{harness.model}: 7 tables, 0 views, 0 functions")


def test_the_deploy_run_is_logged_failed_when_the_deploy_fails(
    harness: Harness, tmp_path: Path
) -> None:
    folder = write(
        tmp_path / "m", {"100_keyed.sql": GOOD_TABLE.replace("VARCHAR(20)", "NOT_A_TYPE(20)")}
    )
    with pytest.raises(DeployError, match=r"100_keyed\.sql"):
        harness.deploy_model(folder)
    last = harness.runs()[-1]
    assert (last["RUN_KIND"], last["RUN_STATUS"]) == ("DEPLOY", "FAILED")
    assert "100_keyed.sql" in last["DETAIL"]


# --- the league -----------------------------------------------------------------------------


def stored_counts(harness: Harness, league: League) -> dict[str, int]:
    return {
        table: int(harness.query(f"SELECT COUNT(*) FROM {harness.core}.{table}")[0][0])
        for table in league.tables()
    }


def test_the_league_is_stored_with_every_row_and_slice_registered(harness: Harness) -> None:
    league = harness.seed_league()
    assert stored_counts(harness, league) == {t: len(league.rows(t)) for t in league.tables()}
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG") == [(len(league.slices),)]
    stored = [s for s in harness.load_slices() if s["IS_STORED"]]
    assert len(stored) == len(league.slices)
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_ORPHANS") == [(0,)]


def test_seeding_again_replaces_the_data_instead_of_doubling_it(harness: Harness) -> None:
    league = harness.seed_league()
    again = harness.seed_league(league_module.with_unaffiliated_fbs_team)
    assert stored_counts(harness, again) == {t: len(again.rows(t)) for t in again.tables()}
    assert len(again.rows("TEAMS")) == len(league.rows("TEAMS")) + 1


def test_the_clock_is_the_leagues_and_the_reset_returns_the_modules_clock(harness: Harness) -> None:
    harness.seed_league()
    now = harness.query(
        "SELECT TO_CHAR(NOW_UTC, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_SEASON "
        f"FROM {harness.meta}.LOADER_SETTINGS"
    )
    assert now == [("2026-10-28 12:00:00", 2026)]
    assert harness.reset_state() == []
    assert harness.query(f"SELECT COUNT(*) FROM {harness.core}.GAMES") == [(0,)]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG") == [(0,)]
    after = harness.query(
        f"SELECT TO_CHAR(NOW_UTC, 'YYYY-MM-DD') FROM {harness.meta}.LOADER_SETTINGS"
    )
    assert after != [("2026-10-28",)]


def test_values_nulls_booleans_and_timestamps_come_back_as_the_league_made_them(
    harness: Harness,
) -> None:
    league = harness.seed_league()
    columns = [
        c.name
        for c in league.get("GAMES", league_module.season_key("GAMES", 2024)).table_def().columns
    ]
    expected = {
        row[columns.index("ID")]: dict(zip(columns, row, strict=True))
        for row in league.rows("GAMES")
    }
    game_id = league.game_id("ashford_brookvale_2024")
    stored = harness.query_dicts(f"SELECT * FROM {harness.core}.GAMES WHERE ID = {game_id}")[0]
    wanted = expected[game_id]
    for name in (
        "SEASON",
        "WEEK",
        "SEASON_TYPE",
        "HOME_TEAM",
        "AWAY_TEAM",
        "HOME_POINTS",
        "AWAY_POINTS",
    ):
        assert str(stored[name]) == str(wanted[name]), name
    assert datetime.fromisoformat(str(stored["START_DATE"])) == wanted["START_DATE"]
    assert datetime.fromisoformat(str(stored["REQ_AT"])) == wanted["REQ_AT"]
    nulls = sum(1 for row in expected.values() if row["HOME_PREGAME_ELO"] is None)
    assert nulls > 0
    assert harness.query(
        f"SELECT COUNT(*) FROM {harness.core}.GAMES WHERE HOME_PREGAME_ELO IS NULL"
    ) == [(nulls,)]
    log = {
        (r[0], r[1]): r[2]
        for r in harness.query(
            f"SELECT TABLE_NAME, SLICE_KEY, IS_FINAL FROM {harness.meta}.LOAD_LOG"
        )
    }
    assert log[("GAMES", league_module.season_key("GAMES", 2025))] is True
    assert log[("GAMES", league_module.season_key("GAMES", 2026))] is False


def test_the_league_recordings_are_the_2026_weekly_slices_a_udf_can_read(harness: Harness) -> None:
    keys = harness.write_league_recordings()
    assert len(keys) == 4
    for key in keys:
        assert harness.replay_area.host_file(key).exists()
    harness.wait_for_files([harness.replay_area.udf_file(key) for key in keys])


# --- the performance namespace --------------------------------------------------------------


@pytest.fixture
def two_seasons(monkeypatch: pytest.MonkeyPatch) -> None:
    def few(self: scaled_league.ScaledLeague) -> Iterator[Any]:
        yield from scaled_league.static_slices()
        yield from scaled_league.season_slices(2023)
        yield from scaled_league.season_slices(2024)

    monkeypatch.setattr(scaled_league.ScaledLeague, "iter_slices", few)


def test_the_perf_namespace_is_built_without_a_virtual_schema_and_takes_the_model(
    harness: Harness, two_seasons: None
) -> None:
    perf = Namespace(model_support.PERF_PREFIX)
    totals = harness.seed_scaled_league(force=True)
    expected = scaled_league.game_count(2023) + scaled_league.game_count(2024)
    assert totals["GAMES"] == expected
    assert harness.query(f"SELECT COUNT(*) FROM {perf.core_schema}.GAMES") == [(expected,)]
    assert harness.query(
        f"SELECT COUNT(*) FROM EXA_ALL_VIRTUAL_SCHEMAS WHERE SCHEMA_NAME = '{perf.vs_name}'"
    ) == [(0,)]
    clock = harness.query(
        f"SELECT TO_CHAR(NOW_UTC, 'YYYY-MM-DD HH24:MI:SS') FROM {perf.meta_schema}.LOADER_SETTINGS"
    )
    assert clock == [("2026-10-28 12:00:00",)]
    report = model_deploy.deploy_model(harness.owner, perf)
    assert report.objects.schema == "PERF_CFB_MODEL"
    assert harness.query("SELECT COUNT(*) FROM PERF_CFB_MODEL.ERA")[0][0] == len(
        seed_rows(SEEDS["ERA"])
    )
