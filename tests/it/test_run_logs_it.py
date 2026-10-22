"""Lock and failure scenarios of `loader/run-logs` (task 4.2), and the run kinds of hand-off H1.

The run lock is the `RUNNING` row of `LOAD_RUN`. These tests drive it with `BACKFILL` and, for the
kinds of other plans (`SIMULATE`, `DEPLOY`), through `TEST_LOCK_PROBE`, a script bundled like
another plan's script (`tests/it/lock_probe.py`). A second session is a second owner connection;
a killed run is a backfill whose batch waits for the mock while `KILL STATEMENT` lands.

The remaining run-log scenarios (every run recorded, fetched slices logged, the reference capture,
the load log matching the stored rows) come with task 7.3, once `REFRESH_WEEK` exists.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pyexasol
import pytest

from tests.it import lock_probe
from tests.it.backfill_helpers import (
    GAMES_PATH,
    IDLE,
    SERVER_ERROR,
    WAIT_SECONDS,
    batch_steps,
    deploy_games,
    fetch_log_keys,
    games_key,
    replay,
    running_batch,
    session_status,
    step_keys,
    stored_keys,
    total,
    wait_for,
)
from tests.it.harness import Harness, Mock
from tests.it.loader_support import script_call_sql

pytestmark = pytest.mark.it

LATENCY_MS = 10_000
INSERT_RUNNING = (
    "INSERT INTO {meta}.LOAD_RUN (RUN_KIND, RUN_ARGS, SESSION_ID, STARTED_AT, RUN_STATUS) "
    "VALUES ('BACKFILL', 'demo, NULL, NULL', CURRENT_SESSION, CURRENT_TIMESTAMP, 'RUNNING')"
)


@pytest.fixture(scope="module")
def probe(harness: Harness) -> str:
    """The qualified name of `TEST_LOCK_PROBE`, created once for the module."""
    harness.query(lock_probe.lock_probe_sql(harness.namespace))
    return f"{harness.infra}.{lock_probe.PROBE_SCRIPT}"


def call(connection: Any, script: str, *arguments: object) -> list[tuple[Any, ...]]:
    return [
        tuple(row) for row in connection.execute(script_call_sql(script, *arguments)).fetchall()
    ]


def acquire(connection: Any, probe: str, kind: str, args: str) -> int:
    return int(call(connection, probe, "acquire", kind, args)[0][0])


def session_of(connection: Any) -> int:
    return int(connection.execute("SELECT CURRENT_SESSION").fetchall()[0][0])


def statuses(harness: Harness) -> dict[int, str]:
    return {int(run["RUN_ID"]): str(run["RUN_STATUS"]) for run in harness.runs()}


def backfill_in(connection: Any, harness: Harness, outcome: list[Any]) -> Callable[[], None]:
    """A thread target: a backfill in `connection`, its rows or its error appended to `outcome`."""

    def run() -> None:
        try:
            outcome.append(call(connection, f"{harness.infra}.BACKFILL", "demo", None, None))
        except BaseException as error:
            outcome.append(error)

    return run


def test_failed_run_recorded(harness: Harness, mock: Mock) -> None:
    """Scenario: A failed run is recorded and registers nothing from the failed batch."""
    deploy_games(harness, 2017, 2020)
    harness.set_property("MAX_CALLS_PER_QUERY", "2")
    for year in ("2018", "2017"):
        mock.script(GAMES_PATH, query={"year": year}, **SERVER_ERROR)
    rows = harness.backfill()
    run = harness.runs()[-1]
    assert (run["RUN_STATUS"], total(rows)["RUN_STATUS"]) == ("FAILED", "FAILED")
    assert "500" in run["DETAIL"]
    steps = batch_steps(harness, run["RUN_ID"])
    assert [(step["STEP_STATUS"], step_keys(step)) for step in steps[:2]] == [
        ("DONE", [games_key(2020), games_key(2019)]),
        ("FAILED", [games_key(2018), games_key(2017)]),
    ]
    assert stored_keys(harness) == {games_key(2020), games_key(2019)}
    assert fetch_log_keys(harness) == {games_key(2020), games_key(2019)}
    assert (run["SLICES"], run["ENDED_AT"] is not None) == (2, True)


def test_concurrent_run_refused(harness: Harness, mock: Mock) -> None:
    """Scenario: A second run is refused while one is running."""
    deploy_games(harness, 2020, 2020)
    mock.script(GAMES_PATH, delay_ms=LATENCY_MS)
    outcome: list[Any] = []
    with harness.owner_session() as session:
        worker = threading.Thread(target=backfill_in(session, harness, outcome))
        worker.start()
        try:
            running = wait_for(lambda: running_batch(harness), "the first backfill's batch")
            with pytest.raises(pyexasol.ExaError) as refused:
                harness.backfill()
        finally:
            worker.join(WAIT_SECONDS)
    assert f"BACKFILL refused: BACKFILL run {running['RUN_ID']} is running" in str(refused.value)
    assert not isinstance(outcome[0], BaseException), outcome[0]
    assert harness.mock_hits(GAMES_PATH) == 1
    assert statuses(harness) == {int(running["RUN_ID"]): "DONE"}


def test_abandoned_run_does_not_block(harness: Harness) -> None:
    """Scenario: A run whose session ended does not block the next run."""
    deploy_games(harness, 2020, 2020)
    replay(harness, [games_key(2020)])
    with harness.owner_session() as session:
        gone = session_of(session)
        session.execute(INSERT_RUNNING.format(meta=harness.meta))
    wait_for(lambda: session_status(harness, gone) is None, "the closed session to disappear")
    rows = harness.backfill()
    runs = harness.runs()
    assert [run["RUN_STATUS"] for run in runs] == ["ABANDONED", "DONE"]
    assert "session of this run has ended" in runs[0]["DETAIL"]
    assert total(rows)["SLICES"] == 1


def test_failed_or_interrupted_run_does_not_block(harness: Harness, mock: Mock) -> None:
    """Scenario: After a failed or interrupted run, the next run starts."""
    deploy_games(harness, 2019, 2020)
    mock.script("/info", times=1, **SERVER_ERROR)
    with pytest.raises(pyexasol.ExaError, match="quota cannot be read"):
        harness.backfill()
    mock.script(GAMES_PATH, delay_ms=LATENCY_MS)
    outcome: list[Any] = []
    with harness.owner_session() as session:
        killed_session = session_of(session)
        worker = threading.Thread(target=backfill_in(session, harness, outcome))
        worker.start()
        try:
            killed = wait_for(lambda: running_batch(harness), "the second session's batch")
            harness.kill_statement(killed_session)
        finally:
            worker.join(WAIT_SECONDS)
        assert isinstance(outcome[0], pyexasol.ExaError), outcome[0]
        assert statuses(harness)[int(killed["RUN_ID"])] == "RUNNING"
        wait_for(lambda: session_status(harness, killed_session) == IDLE, "the idle session")
        harness.mock.reset()
        rows = harness.backfill()
    runs = statuses(harness)
    assert list(runs.values()) == ["FAILED", "ABANDONED", "DONE"]
    assert total(rows)["SLICES"] == 2
    open_steps = harness.query(
        f"SELECT COUNT(*) FROM {harness.meta}.REFRESH_LOG WHERE STEP_STATUS = 'RUNNING'"
    )
    assert open_steps == [(0,)]


def test_idle_simulate_lock_is_stale(harness: Harness, probe: str) -> None:
    """H1: a SIMULATE row (script rule) is stale once its session is idle."""
    deploy_games(harness, 2020, 2020)
    replay(harness, [games_key(2020)])
    with harness.owner_session() as session:
        simulation = acquire(session, probe, "SIMULATE", "sim 100000")
        wait_for(lambda: session_status(harness, session_of(session)) == IDLE, "an idle session")
        rows = harness.backfill()
        assert statuses(harness)[simulation] == "ABANDONED"
    assert total(rows)["RUN_STATUS"] == "DONE"


def test_idle_deploy_lock_blocks_until_its_session_ends(harness: Harness, probe: str) -> None:
    """H1: a DEPLOY row (host rule) is not stale while its session is open, even when idle."""
    deploy_games(harness, 2020, 2020)
    replay(harness, [games_key(2020)])
    with harness.owner_session() as session:
        deploy_session = session_of(session)
        deploy = acquire(session, probe, "DEPLOY", "make sim")
        wait_for(lambda: session_status(harness, deploy_session) == IDLE, "an idle session")
        with pytest.raises(pyexasol.ExaError) as refused:
            harness.backfill()
        assert f"DEPLOY run {deploy} is running" in str(refused.value)
        assert statuses(harness) == {deploy: "RUNNING"}
        assert stored_keys(harness) == set()
    wait_for(lambda: session_status(harness, deploy_session) is None, "the closed session")
    rows = harness.backfill()
    assert statuses(harness)[deploy] == "ABANDONED"
    assert total(rows)["RUN_STATUS"] == "DONE"


def test_own_session_lock_is_stale_and_release_ends_it(harness: Harness, probe: str) -> None:
    """H1: the caller's own RUNNING row is stale for every kind; release_lock ends a row."""
    first = acquire(harness.owner, probe, "DEPLOY", "model deploy")
    assert call(harness.owner, probe, "release", "DONE", str(first)) == [(1,)]
    assert call(harness.owner, probe, "release", "DONE", str(first)) == [(0,)]
    second = acquire(harness.owner, probe, "DEPLOY", "make calibrate")
    third = acquire(harness.owner, probe, "SIMULATE", "sim 10000")
    assert statuses(harness) == {first: "DONE", second: "ABANDONED", third: "RUNNING"}
    runs = {run["RUN_ID"]: run for run in harness.runs()}
    assert runs[first]["DETAIL"] == lock_probe.RELEASE_DETAIL
    assert runs[first]["ENDED_AT"] is not None
    assert (runs[third]["RUN_KIND"], runs[third]["RUN_ARGS"]) == ("SIMULATE", "sim 10000")
    with pytest.raises(pyexasol.ExaError, match="unknown run kind"):
        acquire(harness.owner, probe, "NO_SUCH_KIND", "x")


def test_lock_collision_changes_nothing(harness: Harness) -> None:
    """A lock taken while another session inserts its row at the same time fails cleanly (40001)."""
    deploy_games(harness, 2020, 2020)
    outcome: list[Any] = []
    with harness.owner_session() as session:
        session.set_autocommit(False)
        session.execute(INSERT_RUNNING.format(meta=harness.meta))
        worker = threading.Thread(
            target=lambda: outcome.append(_error_of(lambda: harness.backfill()))
        )
        worker.start()
        worker.join(WAIT_SECONDS)
        session.rollback()
        worker.join(WAIT_SECONDS)
    assert "collision" in str(outcome[0])
    assert harness.runs() == []


def _error_of(action: Callable[[], object]) -> BaseException | None:
    try:
        action()
    except BaseException as error:
        return error
    return None
