"""Proof that the harness drives the loader: plan variants, clock, step scripts, `/info`, logs.

No Lua yet: the views read the deployed plan, the load log and the mock's `/info`. Tests that
switch the plan or the clock are followed by one that shows the reset put the module's plan back.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyexasol
import pytest

from tests.it import step_scripts
from tests.it.harness import READER_USER, Harness, Mock
from tests.it.support import where_clause
from tests.synthetic import fake

pytestmark = pytest.mark.it

NOW = "2020-10-20"
GAMES_2019 = "/games?seasonType=both&year=2019"
KILL_WAIT_SECONDS = 15
SLEEP_SECONDS = 5
IDLE_STATUS = "IDLE"
MAX_CLOCK_SKEW = timedelta(minutes=5)


def slice_counts(harness: Harness) -> dict[str, int]:
    return {row["TABLE_NAME"]: int(row["SLICES_PLANNED"]) for row in harness.load_status()}


def test_the_base_plan_follows_the_test_clock(harness: Harness) -> None:
    harness.set_now(NOW)
    assert slice_counts(harness) == {"GAMES": 4, "RATINGS_SP": 2}
    queue = harness.backfill_queue()
    assert [(row["TABLE_NAME"], row["SEASON"]) for row in queue] == [
        ("GAMES", 2020),
        ("GAMES", 2019),
        ("GAMES", 2018),
        ("GAMES", 2017),
        ("RATINGS_SP", 2020),
        ("RATINGS_SP", 2019),
    ]
    harness.set_now("2021-07-31")
    assert slice_counts(harness) == {"GAMES": 4, "RATINGS_SP": 2}
    harness.set_now("2021-08-01")
    assert slice_counts(harness) == {"GAMES": 5, "RATINGS_SP": 3}


def test_the_reset_cleared_the_clock_and_kept_the_plan(harness: Harness) -> None:
    settings = dict(
        harness.query(f"SELECT SETTING_NAME, SETTING_VALUE FROM {harness.meta}.LOADER_CONFIG")
    )
    assert "NOW_OVERRIDE" not in settings
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_PLAN") == [(2,)]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.SEASON_NUMBERS") == [(232,)]


def test_a_plan_variant_replaces_the_plan_and_registers_gaps(harness: Harness) -> None:
    report = harness.deploy_plan("gaps", {"now_override": NOW})
    assert report.skipped_scripts == ("BACKFILL", "REFRESH_WEEK")
    slices = harness.load_slices("GAMES")
    assert [row["SEASON"] for row in slices] == [1869, 1870, 1871, 1872]
    assert [row["IS_GAP"] for row in slices] == [False, False, True, False]
    status = harness.load_status()
    assert [
        (row["SLICES_PLANNED"], row["GAP_SLICES"], row["CALLS_TO_COMPLETE"]) for row in status
    ] == [(4, 1, 3)]


def test_the_reset_redeployed_the_base_plan_after_a_variant(harness: Harness) -> None:
    assert [row["TABLE_NAME"] for row in harness.load_status()] == ["GAMES", "RATINGS_SP"]
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_PLAN_GAPS") == [(0,)]


def test_an_ad_hoc_plan_text_deploys_and_an_invalid_one_changes_nothing(harness: Harness) -> None:
    text = (
        "version: 1\nentries:\n"
        "  - {table: TEAMS, profile: demo, priority: 1, slicing: none, refresh_class: static}\n"
    )
    harness.deploy_plan_text(text)
    assert [row["TABLE_NAME"] for row in harness.load_status()] == ["TEAMS"]
    with pytest.raises(Exception, match="NO_SUCH_TABLE"):
        harness.deploy_plan_text("version: 1\nentries:\n  - {table: NO_SUCH_TABLE}\n")
    assert [row["TABLE_NAME"] for row in harness.load_status()] == ["TEAMS"]


def test_a_registered_slice_is_stored_and_written_in_utc(harness: Harness) -> None:
    harness.set_now(NOW)
    harness.register_slice("GAMES", GAMES_2019, 3)
    row = next(r for r in harness.load_slices("GAMES") if r["SEASON"] == 2019)
    assert row["IS_STORED"] is True
    assert row["IS_FINAL"] is True
    loaded_at = datetime.fromisoformat(
        harness.query(f"SELECT LOADED_AT FROM {harness.meta}.LOAD_LOG")[0][0]
    )
    assert abs(loaded_at - datetime.now(UTC).replace(tzinfo=None)) < MAX_CLOCK_SKEW
    status = next(r for r in harness.load_status() if r["TABLE_NAME"] == "GAMES")
    assert (status["SLICES_STORED"], status["CALLS_TO_COMPLETE"]) == (1, 3)


def test_a_registered_failure_quarantines_the_slice(harness: Harness) -> None:
    harness.set_now(NOW)
    harness.register_failure("GAMES", GAMES_2019, 2, "HTTP 500")
    status = next(r for r in harness.load_status() if r["TABLE_NAME"] == "GAMES")
    assert (status["QUARANTINED_SLICES"], status["CALLS_TO_COMPLETE"]) == (1, 3)
    assert [f["LAST_ERROR"] for f in harness.failures()] == ["HTTP 500"]


def test_the_reset_emptied_the_logs(harness: Harness) -> None:
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_LOG") == [(0,)]
    assert harness.failures() == []
    assert harness.runs() == []
    assert harness.steps() == []


def test_a_static_info_reaches_the_quota_view(harness: Harness, mock: Mock) -> None:
    harness.script_info(
        used=120, limit=1000, remaining=880, tier="Free", reset_at="2020-11-01T00:00:00Z"
    )
    quota = harness.quota()
    assert (quota["TIER_NAME"], quota["USED_CALLS"], quota["REMAINING_CALLS"]) == ("Free", 120, 880)
    assert datetime.fromisoformat(quota["PERIOD_START"]) == datetime(2020, 10, 1)
    assert harness.mock_hits() == 1
    assert harness.mock_hits("/info") == 1


def test_a_counting_info_adds_the_data_requests(harness: Harness, mock: Mock) -> None:
    harness.script_info(counting=True, used=10, limit=1000)
    assert harness.quota()["USED_CALLS"] == 10
    where = where_clause("CALENDAR", fake.minimal_filter("CALENDAR"))
    harness.query(f"SELECT COUNT(*) FROM {harness.vs}.CALENDAR WHERE {where}")
    assert harness.info_state()["data_requests"] == 1
    assert harness.quota()["USED_CALLS"] == 11


def test_the_mock_info_is_back_to_its_default(harness: Harness, mock: Mock) -> None:
    assert harness.info_state()["mode"] == "default"


def test_the_step_record_script_logs_its_arguments(harness: Harness) -> None:
    infra = harness.infra
    harness.query(f"EXECUTE SCRIPT {infra}.{step_scripts.RECORD_SCRIPT}(7, 2020, 1, 'postseason')")
    harness.query(f"EXECUTE SCRIPT {infra}.{step_scripts.RECORD_SCRIPT}(8, 2020, 2, NULL)")
    rows = harness.query(
        "SELECT RUN_ID, SEASON, WEEK, SEASON_TYPE, STEP_SCRIPT "
        f"FROM {infra}.{step_scripts.CALLS_TABLE} ORDER BY CALL_NO"
    )
    assert rows == [
        (7, 2020, 1, "postseason", step_scripts.RECORD_SCRIPT),
        (8, 2020, 2, None, step_scripts.RECORD_SCRIPT),
    ]


def test_the_reset_emptied_the_step_calls(harness: Harness) -> None:
    rows = harness.query(f"SELECT COUNT(*) FROM {harness.infra}.{step_scripts.CALLS_TABLE}")
    assert rows == [(0,)]


def test_the_step_fail_script_names_the_run(harness: Harness) -> None:
    with pytest.raises(pyexasol.ExaError, match=r"TEST_STEP_FAIL: forced failure in run 12"):
        harness.query(
            f"EXECUTE SCRIPT {harness.infra}.{step_scripts.FAIL_SCRIPT}(12, 2020, 7, 'regular')"
        )


def test_the_step_sleep_script_takes_its_seconds(harness: Harness) -> None:
    started = time.monotonic()
    harness.query(
        f"EXECUTE SCRIPT {harness.infra}.{step_scripts.SLEEP_SCRIPT}(1, 2020, 7, 'regular')"
    )
    assert time.monotonic() - started >= step_scripts.SLEEP_SECONDS


def test_the_refresh_step_variants_register_the_harness_scripts(harness: Harness) -> None:
    harness.deploy_plan("refresh-steps")
    steps = harness.query(
        f"SELECT STEP_NO, STEP_NAME, SCRIPT_NAME FROM {harness.meta}.REFRESH_STEPS ORDER BY STEP_NO"
    )
    assert steps == [
        (100, "MODEL", f"{harness.infra}.{step_scripts.RECORD_SCRIPT}"),
        (200, "PREDICT", f"{harness.infra}.{step_scripts.RECORD_SCRIPT}"),
    ]


def test_a_killed_statement_ends_but_the_session_stays(harness: Harness) -> None:
    outcome: list[BaseException | None] = []
    with harness.owner_session() as session:
        session_id = int(session.execute("SELECT CURRENT_SESSION").fetchall()[0][0])
        sleeping = f"SELECT {harness.infra}.{step_scripts.SLEEP_UDF}({SLEEP_SECONDS})"

        def run_sleep() -> None:
            try:
                session.execute(sleeping)
                outcome.append(None)
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=run_sleep)
        worker.start()
        deadline = time.monotonic() + KILL_WAIT_SECONDS
        while time.monotonic() < deadline and not _is_executing(harness, session_id):
            time.sleep(0.2)
        harness.kill_statement(session_id)
        worker.join(KILL_WAIT_SECONDS)
        assert not worker.is_alive()
        assert isinstance(outcome[0], pyexasol.ExaQueryAbortError)
        assert session.execute("SELECT 1").fetchall() == [(1,)]


def _is_executing(harness: Harness, session_id: int) -> bool:
    rows = harness.query(
        "SELECT STATUS FROM EXA_ALL_SESSIONS WHERE SESSION_ID = {id!d}",
        {"id": session_id},
        user="sys",
    )
    return bool(rows) and rows[0][0] != IDLE_STATUS


def test_the_exapump_environment_holds_the_owner_dsn_in_memory_only(harness: Harness) -> None:
    env, args = harness.exapump_env()
    dsn = env["EXAPUMP_DSN"]
    assert dsn.startswith(f"exasol://{harness.namespace.owner_user}:")
    assert dsn.endswith("@127.0.0.1:8563")
    assert args[0] == "--certificate-fingerprint"
    assert "EXAPUMP_DSN" not in os.environ


def test_the_snapshot_directory_is_private_and_outside_the_repo(snapshot_dir: Path) -> None:
    assert snapshot_dir.is_dir()
    assert snapshot_dir.stat().st_mode & 0o777 == 0o700
    assert list(snapshot_dir.iterdir()) == []
    assert Path(__file__).resolve().parents[2] not in snapshot_dir.resolve().parents


def test_the_reader_still_has_only_the_virtual_schema(harness: Harness) -> None:
    assert harness.query(f"SELECT COUNT(*) FROM {harness.vs}.INFO", user="reader") == [(1,)]
    assert (
        READER_USER
        in harness.query(
            "SELECT USER_NAME FROM EXA_DBA_USERS WHERE USER_NAME = {u}",
            {"u": READER_USER},
            user="sys",
        )[0]
    )
