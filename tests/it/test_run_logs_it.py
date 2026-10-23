"""Lock and failure scenarios of `loader/run-logs` (task 4.2), and the run kinds of hand-off H1.

The run lock is the `RUNNING` row of `LOAD_RUN`. These tests drive it with `BACKFILL` and, for the
kinds of other plans (`SIMULATE`, `DEPLOY`), through `TEST_LOCK_PROBE`, a script bundled like
another plan's script (`tests/it/lock_probe.py`). A second session is a second owner connection;
a killed run is a backfill whose batch waits for the mock while `KILL STATEMENT` lands.

The scenarios of task 7.3 read the logs after real runs: every run recorded (a backfill and a
refresh against the mock, `/info` counting), fetched slices logged (live batch with an empty
slice), the loader's fetch log against the reference capture (replay mode, so both read the same
recordings) and the load log against the stored rows.
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
    NOW,
    SERVER_ERROR,
    WAIT_SECONDS,
    batch_steps,
    core_counts,
    deploy_games,
    fetch_log_keys,
    games_key,
    load_log,
    replay,
    running_batch,
    session_status,
    step_keys,
    stored_keys,
    total,
    wait_for,
)
from tests.it.harness import HARNESS_SCHEMA, Harness, Mock
from tests.it.loader_support import script_call_sql
from tests.synthetic import fake
from vs.host import fetchlog

pytestmark = pytest.mark.it

LATENCY_MS = 10_000
COUNTED_LIMIT = 1000
EMPTY_YEAR = 2019
GAP_YEAR = 2019
STAGED_TABLE = "RUN_LOGS_STAGED"
FLOW_ENTRIES = (
    "{table: TEAMS, profile: demo, priority: 1, slicing: none, refresh_class: static}",
    "{table: CALENDAR, profile: demo, priority: 2, slicing: season, seasons: {from: 2020}, "
    "refresh_class: static}",
    "{table: GAMES, profile: demo, priority: 3, slicing: season, seasons: {from: 2019}, "
    f"gaps: [{GAP_YEAR}], refresh_class: weekly}}",
    "{table: RATINGS_SP, profile: demo, priority: 4, slicing: season, seasons: {from: 2019}, "
    "refresh_class: weekly}",
)
FLOW_PLAN = "version: 1\nrefresh_steps: []\nentries:\n" + "".join(
    f"  - {entry}\n" for entry in FLOW_ENTRIES
)
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


def start_counting(harness: Harness) -> None:
    harness.script_info(counting=True, used=0, limit=COUNTED_LIMIT)


def deploy_flow(harness: Harness) -> None:
    harness.deploy_plan_text(FLOW_PLAN, {"now_override": NOW})


def logged_rows(harness: Harness) -> int:
    return sum(int(row["ROWS_LOADED"]) for row in load_log(harness).values())


def test_every_run_recorded(harness: Harness, mock: Mock) -> None:
    """Scenario: Every run is recorded with its outcome (live mode, so the calls are measured)."""
    deploy_flow(harness)
    start_counting(harness)
    backfill_rows = total(harness.backfill())
    stored_by_backfill = logged_rows(harness)
    harness.refresh(2020, 7, "regular")
    runs = harness.runs()
    backfill, refresh = runs
    assert backfill["RUN_ID"] == backfill_rows["RUN_ID"]
    assert [(run["RUN_KIND"], run["RUN_ARGS"], run["RUN_STATUS"]) for run in runs] == [
        ("BACKFILL", "demo, NULL, NULL", "DONE"),
        ("REFRESH_WEEK", "2020, 7, regular", "DONE"),
    ]
    session = harness.session_id()
    for run in runs:
        assert int(run["SESSION_ID"]) == session
        assert run["ENDED_AT"] >= run["STARTED_AT"]
        steps = harness.steps(run["RUN_ID"])
        for column in ("SLICES", "ROWS_LOADED", "CALLS"):
            assert run[column] == sum(step[column] for step in steps), (run["RUN_KIND"], column)
    gaps = next(step for step in harness.steps(backfill["RUN_ID"]) if step["STEP_NAME"] == "GAPS")
    assert (gaps["SLICES"], gaps["CALLS"]) == (0, 0)
    assert str(gaps["DETAIL"]).startswith("1 known gap")
    assert (backfill["SLICES"], backfill["CALLS"]) == (5, 5)
    assert backfill["ROWS_LOADED"] == stored_by_backfill > 0
    assert (refresh["SLICES"], refresh["CALLS"]) == (2, 2)
    refreshed = {
        key: row["ROWS_LOADED"]
        for key, row in load_log(harness).items()
        if row["LOADED_BY"] == f"REFRESH_WEEK#{refresh['RUN_ID']}"
    }
    assert len(refreshed) == 2
    assert refresh["ROWS_LOADED"] == sum(refreshed.values()) > 0
    assert (backfill["REMAINING_BEFORE"], backfill["REMAINING_AFTER"]) == (
        COUNTED_LIMIT,
        COUNTED_LIMIT - 5,
    )
    assert (refresh["REMAINING_BEFORE"], refresh["REMAINING_AFTER"]) == (
        COUNTED_LIMIT - 5,
        COUNTED_LIMIT - 7,
    )


def test_fetched_slices_logged_with_run(harness: Harness, mock: Mock) -> None:
    """Scenario: Every fetched slice is logged with its run (3 slices, 2019 has no rows)."""
    deploy_games(harness, 2018, 2020)
    mock.script(GAMES_PATH, query={"year": str(EMPTY_YEAR)}, body=[])
    run_id = total(harness.backfill())["RUN_ID"]
    logged = {
        row["SLICE_KEY"]: row
        for row in harness.query_dicts(f"SELECT * FROM {harness.meta}.FETCH_LOG")
    }
    assert set(logged) == {games_key(y) for y in (2018, EMPTY_YEAR, 2020)}
    assert {row["CONTEXT"] for row in logged.values()} == {f"BACKFILL#{run_id}"}
    empty = logged[games_key(EMPTY_YEAR)]
    assert (empty["ROWS_RETURNED"], empty["REQ_SOURCE"], empty["HTTP_STATUS"]) == (0, "LIVE", None)
    metadata = {
        row["REQ_SLICE"]: row
        for row in harness.query_dicts(
            "SELECT REQ_SLICE, REQ_STATUS, REQ_ATTEMPTS, REQ_MS, REQ_AT, REQ_ROWS, COUNT(*) AS N "
            f"FROM {harness.core}.GAMES GROUP BY 1, 2, 3, 4, 5, 6"
        )
    }
    assert set(metadata) == {games_key(2018), games_key(2020)}
    for key, stored in metadata.items():
        row = logged[key]
        assert (row["HTTP_STATUS"], row["ATTEMPTS"], row["MS"], row["REQUESTED_AT"]) == (
            stored["REQ_STATUS"],
            stored["REQ_ATTEMPTS"],
            stored["REQ_MS"],
            stored["REQ_AT"],
        )
        assert row["ROWS_RETURNED"] == stored["N"] == stored["REQ_ROWS"] > 0
        assert row["REQ_SOURCE"] == "LIVE"


def test_fetch_log_equals_reference_capture(harness: Harness) -> None:
    """Scenario: The loader's fetch log equals the reference capture.

    Replay mode: the reference stages the batch first, so both read the same recordings (a stored
    slice is not fetched again, so the staging cannot follow the run).
    """
    deploy_games(harness, 2018, 2020)
    replay(harness, [games_key(2018), games_key(2020)])
    harness.write_recording(games_key(EMPTY_YEAR), [])
    query = (
        f"SELECT * FROM {harness.vs}.GAMES WHERE P_YEAR IN (2018, 2019, 2020) "
        "AND REQ_SOURCE <> 'STORED'"
    )
    pushdown = fetchlog.explain_pushdown_sql(harness.owner, query)
    manifest = fetchlog.parse_manifest(pushdown)
    staged = (HARNESS_SCHEMA, STAGED_TABLE)
    harness.query(f"DROP TABLE IF EXISTS {HARNESS_SCHEMA}.{STAGED_TABLE}")
    harness.query(f"CREATE TABLE {HARNESS_SCHEMA}.{STAGED_TABLE} AS {query}")
    try:
        metadata = fetchlog.read_slice_metadata(harness.owner, staged)
    finally:
        harness.query(f"DROP TABLE IF EXISTS {HARNESS_SCHEMA}.{STAGED_TABLE}")
    run_id = total(harness.backfill())["RUN_ID"]
    loaded = harness.query_dicts(f"SELECT * FROM {harness.meta}.FETCH_LOG")
    assert len(loaded) == 3
    reference = fetchlog.build_fetch_log_rows(
        manifest, metadata, logged_at=loaded[0]["LOGGED_AT"], context=f"BACKFILL#{run_id}"
    )
    assert len(reference) == 3
    expected = [
        dict(zip(fetchlog.FETCH_LOG_COLUMNS, row.as_tuple(), strict=True)) for row in reference
    ]
    by_slice = {row["SLICE_KEY"]: row for row in loaded}
    assert len(by_slice) == 3
    for row in expected:
        assert {**by_slice[row["SLICE_KEY"]], "LOGGED_AT": None} == {**row, "LOGGED_AT": None}


def test_load_log_matches_stored_rows(harness: Harness, mock: Mock) -> None:
    """Scenario: The load log matches the stored rows (after a backfill and two refreshes)."""
    deploy_flow(harness)
    start_counting(harness)
    harness.backfill()
    harness.refresh(2020, 7, "regular")
    mock.script(GAMES_PATH, query={"year": "2020"}, body=fake.changed_games_body(2020, 1))
    harness.refresh(2020, 8, "regular")
    log = load_log(harness)
    planned = {row["TABLE_NAME"] for row in harness.load_slices()}
    assert planned == {"TEAMS", "CALENDAR", "GAMES", "RATINGS_SP"}
    for table in sorted(planned):
        counts = core_counts(harness, table)
        registered = {key: row for (name, key), row in log.items() if name == table}
        assert set(counts) <= set(registered), table
        for key, row in registered.items():
            assert row["ROWS_LOADED"] == counts.get(key, 0), (table, key)
    gap = log[("GAMES", games_key(GAP_YEAR))]
    assert (gap["ROWS_LOADED"], gap["LOADED_BY"]) == (0, "LOAD_PLAN_GAP")
    assert sum(row["ROWS_LOADED"] for row in log.values()) > len(log)
    assert harness.query(f"SELECT COUNT(*) FROM {harness.meta}.LOAD_ORPHANS") == [(0,)]


def _error_of(action: Callable[[], object]) -> BaseException | None:
    try:
        action()
    except BaseException as error:
        return error
    return None
