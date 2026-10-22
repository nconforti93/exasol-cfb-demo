"""The host side of the run lock (`loader.runlock.run_lock`, hand-off H1b) on Exasol Personal local.

A host process holds the lock through the deployed `RUN_LOCK` script in its own session, which is
idle between its statements. The rows of its kinds (`DEPLOY`, `SNAPSHOT`, `RESTORE`) are stale
only when that session is gone or is the caller's own, never because it is idle. Each test holds
the lock in a second owner session, the host, and starts the loader in the harness's owner session.
"""

from __future__ import annotations

from typing import Any

import pyexasol
import pytest

from loader.runlock import RunLockError, RunLockRefused, run_lock
from tests.it.backfill_helpers import (
    IDLE,
    deploy_games,
    games_key,
    replay,
    session_status,
    total,
    wait_for,
)
from tests.it.harness import Harness

pytestmark = pytest.mark.it


def session_of(connection: Any) -> int:
    return int(connection.execute("SELECT CURRENT_SESSION").fetchall()[0][0])


def statuses(harness: Harness) -> dict[int, tuple[str, str]]:
    return {
        int(run["RUN_ID"]): (str(run["RUN_KIND"]), str(run["RUN_STATUS"])) for run in harness.runs()
    }


def test_an_idle_host_lock_refuses_a_backfill_until_it_ends(harness: Harness) -> None:
    deploy_games(harness, 2020, 2020)
    replay(harness, [games_key(2020)])
    with harness.owner_session() as host:
        with run_lock(host, harness.namespace, "DEPLOY", "make sim") as lock:
            lock.detail = "model deployed"
            wait_for(lambda: session_status(harness, session_of(host)) == IDLE, "an idle host")
            with pytest.raises(pyexasol.ExaError) as refused:
                harness.backfill()
            assert statuses(harness) == {lock.run_id: ("DEPLOY", "RUNNING")}
        rows = harness.backfill()
    assert f"BACKFILL refused: DEPLOY run {lock.run_id} is running" in str(refused.value)
    runs = {int(run["RUN_ID"]): run for run in harness.runs()}
    assert (runs[lock.run_id]["RUN_STATUS"], runs[lock.run_id]["DETAIL"]) == (
        "DONE",
        "model deployed",
    )
    assert runs[lock.run_id]["RUN_ARGS"] == "make sim"
    assert total(rows)["RUN_STATUS"] == "DONE"


def test_a_second_host_is_refused_naming_the_holder(harness: Harness) -> None:
    with harness.owner_session() as first, harness.owner_session() as second:
        with (
            run_lock(first, harness.namespace, "RESTORE", "week7") as held,
            pytest.raises(RunLockRefused) as refused,
            run_lock(second, harness.namespace, "SNAPSHOT", "week8"),
        ):
            pytest.fail("the second host must not run")
        assert str(refused.value).startswith(
            f"SNAPSHOT refused: RESTORE run {held.run_id} is running (session "
        )
    assert statuses(harness) == {held.run_id: ("RESTORE", "DONE")}


def test_a_failing_block_is_rolled_back_and_ends_failed(harness: Harness) -> None:
    table = f"{harness.meta}.LOAD_FAILURES"
    with harness.owner_session() as host:
        with (
            pytest.raises(RuntimeError, match="deploy step failed"),
            run_lock(host, harness.namespace, "DEPLOY", "model deploy") as lock,
        ):
            host.set_autocommit(False)
            host.execute(
                f"INSERT INTO {table} VALUES ('GAMES', 'k', 1, 'x', NULL, CURRENT_TIMESTAMP)"
            )
            raise RuntimeError("deploy step failed\nwith details")
        host.set_autocommit(True)
    assert harness.query(f"SELECT COUNT(*) FROM {table}") == [(0,)]
    run = harness.runs()[0]
    assert (run["RUN_ID"], run["RUN_STATUS"], run["DETAIL"]) == (
        lock.run_id,
        "FAILED",
        "deploy step failed",
    )


def test_a_host_lock_whose_session_ended_does_not_block(harness: Harness) -> None:
    with harness.owner_session() as host:
        gone = session_of(host)
        rows = host.execute(
            f"EXECUTE SCRIPT {harness.infra}.RUN_LOCK('acquire', 'SNAPSHOT', NULL, 'crashed')"
        ).fetchall()
        crashed = int(rows[0][0])
    wait_for(lambda: session_status(harness, gone) is None, "the closed session to disappear")
    with (
        harness.owner_session() as host,
        run_lock(host, harness.namespace, "RESTORE", "week7") as lock,
    ):
        pass
    assert statuses(harness) == {
        crashed: ("SNAPSHOT", "ABANDONED"),
        lock.run_id: ("RESTORE", "DONE"),
    }


def test_a_script_kind_is_not_a_host_lock(harness: Harness) -> None:
    with (
        pytest.raises(RunLockError, match="host kinds"),
        run_lock(harness.owner, harness.namespace, "SIMULATE", "sim"),
    ):
        pass
    assert harness.runs() == []
