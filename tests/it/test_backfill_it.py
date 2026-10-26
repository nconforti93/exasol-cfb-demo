"""Scenarios of `loader/backfill` against the `TEST_` namespace (zero CFBD calls).

`EXECUTE SCRIPT TEST_CFB_INFRA.BACKFILL(profile, from_season, max_calls)` runs the bundled
`loader/lua/loadlib.lua` + `loader/lua/backfill.lua`. Replay scenarios read synthetic recordings;
live scenarios read the mock API. Where a scenario counts calls, the mock's `/info` counts every
data request it receives (`script_info(counting=True)`), as the spec assumes.

A failing slice is scripted as HTTP 500 with `Retry-After: 60`: the fetch UDF does not wait longer
than 30 s, so it gives up after one attempt instead of four, and a failure costs seconds, not
minutes. Plans that a scenario needs are written in the test and deployed with the test clock;
the reset puts the base plan back.
"""

from __future__ import annotations

import pyexasol
import pytest

from tests.it.backfill_helpers import (
    GAMES,
    GAMES_PATH,
    NOW,
    RATINGS,
    SERVER_ERROR,
    batch_steps,
    core_counts,
    deploy_games,
    fetch_log_keys,
    games_key,
    games_requests,
    load_log,
    ratings_key,
    replay,
    step_keys,
    stored_keys,
    table_row,
    total,
)
from tests.it.harness import Harness, Mock, virtual_schema_property_rows
from tests.it.loader_support import BASE_VARIANT

pytestmark = pytest.mark.it

GAP_LOADER = "LOAD_PLAN_GAP"


def test_priority_then_newest_season_with_max_calls(harness: Harness) -> None:
    """Scenario: A backfill loads by priority, newest season first."""
    harness.set_now(NOW)
    keys = [games_key(y) for y in range(2017, 2021)] + [ratings_key(2019), ratings_key(2020)]
    replay(harness, keys)
    rows = harness.backfill("demo", None, 3)
    assert stored_keys(harness) == {games_key(2020), games_key(2019), games_key(2018)}
    assert stored_keys(harness, RATINGS) == set()
    assert (total(rows)["RUN_STATUS"], total(rows)["CALLS_NEEDED"]) == ("STOPPED_MAX_CALLS", 3)
    assert (total(rows)["SLICES"], total(rows)["CALLS"]) == (3, 0)


def test_equal_priority_interleaves_seasons(harness: Harness) -> None:
    """Scenario: Entries of equal priority are loaded season by season."""
    harness.deploy_plan("equal-priority", {"now_override": NOW})
    replay(harness, [games_key(2020), games_key(2019), ratings_key(2020), ratings_key(2019)])
    rows = harness.backfill("demo", None, 2)
    assert stored_keys(harness) == {games_key(2020)}
    assert stored_keys(harness, RATINGS) == {ratings_key(2020)}
    assert total(rows)["RUN_STATUS"] == "STOPPED_MAX_CALLS"


def test_seasonless_slices_first_and_always_included(harness: Harness) -> None:
    """Scenario: Slices without a season come first and are always included."""
    harness.deploy_plan("seasonless", {"now_override": NOW})
    seasonless = [row for row in harness.load_slices() if row["SEASON"] is None]
    assert {row["TABLE_NAME"] for row in seasonless} == {"TEAMS", "CONFERENCES_AFFILIATIONS"}
    replay(harness, [row["SLICE_KEY"] for row in seasonless] + [games_key(2018), games_key(2017)])
    rows = harness.backfill("demo", 2018, 2)
    assert {key for _, key in load_log(harness)} == {row["SLICE_KEY"] for row in seasonless}
    assert stored_keys(harness) == set()
    assert total(rows)["RUN_STATUS"] == "STOPPED_MAX_CALLS"


def test_batch_never_mixes_entries(harness: Harness) -> None:
    """Scenario: A batch never mixes plan entries."""
    harness.deploy_plan("classifications", {"now_override": NOW})
    slices = harness.load_slices(GAMES)
    entry_of = {row["SLICE_KEY"]: row["ENTRY_ID"] for row in slices}
    assert len(entry_of) == 4
    replay(harness, entry_of)
    rows = harness.backfill()
    run_id = total(rows)["RUN_ID"]
    steps = batch_steps(harness, run_id)
    assert steps
    for step in steps:
        entry = int(step["STEP_NAME"].split("#")[-1])
        assert {entry_of[key] for key in step_keys(step)} == {entry}
    assert stored_keys(harness) == set(entry_of)
    classes = harness.query(
        f"SELECT DISTINCT REQ_SLICE, P_CLASSIFICATION FROM {harness.core}.GAMES"
    )
    assert {(key, f"classification={value}" in key) for key, value in classes} == {
        (key, True) for key in entry_of
    }
    assert total(rows)["RUN_STATUS"] == "DONE"


def test_second_run_fetches_nothing(harness: Harness, mock: Mock) -> None:
    """Scenario: A second run fetches nothing that is already stored."""
    harness.set_now("2021-03-01")
    harness.script_info(counting=True, used=0, limit=1000)
    first = harness.backfill("demo", None, None)
    assert total(first)["RUN_STATUS"] == "DONE"
    assert total(first)["CALLS"] == 6
    requests, log_before, core_before = (
        harness.info_state()["data_requests"],
        load_log(harness),
        (core_counts(harness), core_counts(harness, RATINGS)),
    )
    second = harness.backfill("demo", None, None)
    assert harness.info_state()["data_requests"] == requests
    assert load_log(harness) == log_before
    assert (core_counts(harness), core_counts(harness, RATINGS)) == core_before
    assert (total(second)["RUN_STATUS"], total(second)["CALLS"]) == ("DONE", 0)


def test_stored_slices_equal_virtual_table(harness: Harness, mock: Mock) -> None:
    """Scenario: Stored slices equal what the virtual table returns.

    "Served from storage without a request" is checked in live mode against the mock: the
    pushdown SQL of a read-through query always carries the (empty) fetch branch ([21]), so the
    manifest's sources cannot show it; the rows' `REQ_SOURCE` and the mock's request log do.
    """
    deploy_games(harness, 2019, 2019)
    replay(harness, [games_key(2019)])
    rows = harness.backfill()
    run_id = total(rows)["RUN_ID"]
    stored = harness.query(f"SELECT * FROM {harness.core}.GAMES")
    entry = load_log(harness)[(GAMES, games_key(2019))]
    assert entry["IS_FINAL"] is True
    assert entry["ROWS_LOADED"] == len(stored) > 0
    assert entry["LOADED_BY"] == f"BACKFILL#{run_id}"
    query = f"SELECT * FROM {harness.vs}.GAMES WHERE P_YEAR = 2019"
    harness.set_property("FETCH_MODE", "live")
    served = harness.query(f"SELECT REQ_SOURCE FROM ({query})")
    assert len(served) == len(stored)
    assert {row[0] for row in served} == {"STORED"}
    assert harness.mock_hits(GAMES_PATH) == 0
    harness.use_replay()
    harness.set_property("READ_THROUGH", "FALSE")
    fetched = harness.query(query)
    assert sorted(map(repr, stored)) == sorted(map(repr, fetched))


def test_gaps_registered_without_request(harness: Harness) -> None:
    """Scenario: Known gaps are registered without a request (no recording exists for 1871)."""
    harness.deploy_plan("gaps", {"now_override": NOW})
    replay(harness, [games_key(1869), games_key(1870), games_key(1872)])
    rows = harness.backfill("demo", None, None)
    gap = load_log(harness)[(GAMES, games_key(1871))]
    assert (gap["IS_FINAL"], gap["ROWS_LOADED"], gap["LOADED_BY"]) == (True, 0, GAP_LOADER)
    assert fetch_log_keys(harness) == {games_key(1869), games_key(1870), games_key(1872)}
    run_id = total(rows)["RUN_ID"]
    gaps_step = harness.steps(run_id)[0]
    assert (gaps_step["STEP_NAME"], gaps_step["SLICES"], gaps_step["CALLS"]) == ("GAPS", 0, 0)
    assert gaps_step["DETAIL"].startswith("1 known gap")
    assert (total(rows)["RUN_STATUS"], total(rows)["SLICES"]) == ("DONE", 3)


def test_non_final_slice_left_to_refresh(harness: Harness, mock: Mock) -> None:
    """Scenario: A non-final slice is left to the weekly refresh."""
    deploy_games(harness, 2020, 2020)
    harness.set_now("2020-12-01")
    harness.register_slice(GAMES, games_key(2020), 5, final=False)
    rows = harness.backfill()
    assert harness.mock_hits(GAMES_PATH) == 0
    assert load_log(harness)[(GAMES, games_key(2020))]["IS_FINAL"] is False
    assert (total(rows)["RUN_STATUS"], total(rows)["CALLS_NEEDED"]) == ("DONE", 0)


def test_finalizable_slice_fetched_once(harness: Harness, mock: Mock) -> None:
    """Scenario: A slice that became final is fetched once more."""
    deploy_games(harness, 2020, 2020)
    harness.set_now("2021-02-15")
    harness.register_slice(GAMES, games_key(2020), 5, final=False)
    first = harness.backfill()
    assert games_requests(harness) == ["2020"]
    entry = load_log(harness)[(GAMES, games_key(2020))]
    assert (entry["IS_FINAL"], entry["LOADED_BY"]) == (True, f"BACKFILL#{total(first)['RUN_ID']}")
    harness.backfill()
    assert games_requests(harness) == ["2020"]


def test_backfill_keeps_refresh_reserve(harness: Harness, mock: Mock) -> None:
    """Scenario: The backfill keeps the refresh reserve (25 missing slices: 1996-2020)."""
    deploy_games(harness, 1996)
    harness.script_info(counting=True, used=840, limit=1000)
    rows = harness.backfill("demo", None, None)
    assert harness.mock_hits(GAMES_PATH) == 10
    assert (total(rows)["RUN_STATUS"], total(rows)["CALLS_NEEDED"]) == ("STOPPED_BUDGET", 15)
    assert total(rows)["CALLS"] == 10


def test_run_counts_measured_calls(harness: Harness, mock: Mock) -> None:
    """Scenario: The run counts measured calls, not slices."""
    deploy_games(harness, 2015, 2020)
    harness.set_property("MAX_CALLS_PER_QUERY", "3")
    harness.script_info(counting=True, used=0, limit=1000)
    mock.script(GAMES_PATH, query={"year": "2018"}, times=1, status=429)
    rows = harness.backfill()
    run = harness.runs()[-1]
    assert (total(rows)["CALLS"], run["CALLS"]) == (7, 7)
    assert sum(step["CALLS"] for step in harness.steps(run["RUN_ID"])) == 7
    assert stored_keys(harness) == {games_key(y) for y in range(2015, 2021)}


def test_a_retried_server_error_is_charged_every_attempt(harness: Harness, mock: Mock) -> None:
    """Review n17: two 503s and then a 200 cost 3 calls, and FETCH_LOG keeps the 3 attempts."""
    deploy_games(harness, 2020, 2020)
    harness.script_info(counting=True, used=0, limit=1000)
    mock.script(GAMES_PATH, times=2, status=503, retry_after=0)
    rows = harness.backfill()
    assert (total(rows)["RUN_STATUS"], total(rows)["CALLS"]) == ("DONE", 3)
    assert harness.runs()[-1]["CALLS"] == 3
    attempts = harness.query(f"SELECT SLICE_KEY, ATTEMPTS FROM {harness.meta}.FETCH_LOG")
    assert attempts == [(games_key(2020), 3)]


def test_dry_run_checks_and_fetches_nothing(harness: Harness, mock: Mock) -> None:
    """Scenario: A dry run checks the plan and fetches nothing (6 missing slices, one gap)."""
    deploy_games(harness, 2014, 2020, gaps=[2017])
    harness.set_property("MAX_CALLS_PER_QUERY", "3")
    harness.script_info(counting=True, used=0, limit=1000)
    rows = harness.backfill("demo", None, 0)
    assert harness.info_state()["data_requests"] == 0
    run_id = total(rows)["RUN_ID"]
    explained = [s for s in harness.steps(run_id) if s["STEP_NAME"].startswith("EXPLAIN ")]
    assert [len(step_keys(step)) for step in explained] == [3, 3]
    assert all(step["STEP_STATUS"] == "DONE" for step in explained)
    assert (total(rows)["RUN_STATUS"], total(rows)["CALLS_NEEDED"]) == ("DONE", 6)
    assert (total(rows)["SLICES"], total(rows)["CALLS"]) == (0, 0)
    assert load_log(harness).keys() == {(GAMES, games_key(2017))}


def test_from_season(harness: Harness) -> None:
    """Scenario: The backfill can start at an older season."""
    harness.set_now(NOW)
    replay(harness, [games_key(2018), games_key(2017)])
    rows = harness.backfill("demo", 2018, None)
    assert stored_keys(harness) == {games_key(2018), games_key(2017)}
    assert stored_keys(harness, RATINGS) == set()
    assert total(rows)["RUN_STATUS"] == "DONE"


def test_failing_slice_isolated(harness: Harness, mock: Mock) -> None:
    """Scenario: A failing slice is isolated and does not cost its batch."""
    deploy_games(harness, 2017, 2020)
    harness.set_property("MAX_CALLS_PER_QUERY", "2")
    harness.script_info(counting=True, used=0, limit=1000)
    mock.script(GAMES_PATH, query={"year": "2018"}, **SERVER_ERROR)
    rows = harness.backfill()
    assert stored_keys(harness) == {games_key(2020), games_key(2019), games_key(2017)}
    assert games_key(2018) not in core_counts(harness)
    [failure] = harness.failures()
    assert (failure["SLICE_KEY"], failure["FAILURES"]) == (games_key(2018), 1)
    assert "500" in failure["LAST_ERROR"]
    assert (total(rows)["RUN_STATUS"], total(rows)["FAILED_SLICES"]) == ("DONE_WITH_FAILURES", 1)
    assert table_row(rows, GAMES)["FAILED_SLICES"] == 1


def test_failing_slice_quarantined(harness: Harness, mock: Mock) -> None:
    """Scenario: A slice that keeps failing is quarantined without blocking the queue."""
    deploy_games(harness, 2015, 2020)
    for year in (2020, 2019, 2018):
        harness.register_slice(GAMES, games_key(year), 3)
    harness.register_failure(GAMES, games_key(2017), 1, "HTTP 500 in an earlier run")
    harness.script_info(counting=True, used=0, limit=1000)
    mock.script(GAMES_PATH, query={"year": "2017"}, **SERVER_ERROR)
    first = harness.backfill()
    requests = games_requests(harness)
    assert (sorted(requests[:2]), requests[2:]) == (["2015", "2016"], ["2017"])
    run_id = total(first)["RUN_ID"]
    assert [step_keys(step) for step in batch_steps(harness, run_id)] == [
        [games_key(2016), games_key(2015)],
        [games_key(2017)],
    ]
    [failure] = harness.failures()
    assert failure["FAILURES"] == 2
    status = next(row for row in harness.load_status() if row["TABLE_NAME"] == GAMES)
    assert status["QUARANTINED_SLICES"] == 1
    assert status["LAST_ERROR_SLICE"] == games_key(2017)
    assert "500" in status["LAST_ERROR"]
    assert total(first)["RUN_STATUS"] == "DONE_WITH_FAILURES"
    second = harness.backfill()
    assert games_requests(harness) == requests
    assert total(second)["RUN_STATUS"] == "DONE"


def test_one_failed_single_slice_batch_does_not_stop_the_run(harness: Harness, mock: Mock) -> None:
    """Scenario: One failed single-slice batch does not stop the run (decision [45])."""
    deploy_games(harness, 2017, 2020)
    harness.set_property("MAX_CALLS_PER_QUERY", "1")
    harness.script_info(counting=True, used=0, limit=1000)
    mock.script(GAMES_PATH, query={"year": "2019"}, **SERVER_ERROR)
    rows = harness.backfill()
    assert stored_keys(harness) == {games_key(2020), games_key(2018), games_key(2017)}
    [failure] = harness.failures()
    assert (failure["SLICE_KEY"], failure["FAILURES"]) == (games_key(2019), 1)
    assert (total(rows)["RUN_STATUS"], total(rows)["FAILED_SLICES"]) == ("DONE_WITH_FAILURES", 1)


def test_systemic_failure_stops_run(harness: Harness, mock: Mock) -> None:
    """Scenario: Two consecutive failed batches stop the run."""
    deploy_games(harness, 2015, 2020)
    harness.set_property("MAX_CALLS_PER_QUERY", "2")
    harness.script_info(counting=True, used=0, limit=1000)
    mock.script(GAMES_PATH, **SERVER_ERROR)
    rows = harness.backfill()
    assert total(rows)["RUN_STATUS"] == "FAILED"
    assert "2 consecutive batches failed" in total(rows)["DETAIL"]
    assert harness.runs()[-1]["RUN_STATUS"] == "FAILED"
    assert sorted(games_requests(harness)) == sorted(["2020", "2019", "2018", "2017"] * 2)
    assert {(f["SLICE_KEY"], f["FAILURES"]) for f in harness.failures()} == {
        (games_key(year), 1) for year in (2020, 2019, 2018, 2017)
    }
    assert [step["STEP_STATUS"] for step in batch_steps(harness, total(rows)["RUN_ID"])] == [
        "FAILED"
    ] * 6


def test_next_run_resumes_after_failure(harness: Harness, mock: Mock) -> None:
    """Scenario: The next run resumes after a failure."""
    deploy_games(harness, 2017, 2020)
    for year in (2020, 2019):
        harness.register_slice(GAMES, games_key(year), 3, loaded_by="BACKFILL#1")
    for year in (2018, 2017):
        harness.register_failure(GAMES, games_key(year), 1, "HTTP 500")
    rows = harness.backfill()
    assert sorted(games_requests(harness)) == ["2017", "2018"]
    run_id = total(rows)["RUN_ID"]
    assert [step_keys(step) for step in batch_steps(harness, run_id)] == [
        [games_key(2018)],
        [games_key(2017)],
    ]
    assert stored_keys(harness) == {games_key(y) for y in range(2017, 2021)}
    assert harness.failures() == []
    assert total(rows)["RUN_STATUS"] == "DONE"


def test_slice_key_drift_stops_before_request(harness: Harness, mock: Mock) -> None:
    """Scenario: A plan that disagrees with the virtual schema stops before any request."""
    harness.set_now(NOW)
    harness.query(
        f"UPDATE {harness.meta}.LOAD_PLAN SET SLICE_TEMPLATE = '/games?year={{season}}' "
        "WHERE TABLE_NAME = 'GAMES'"
    )
    try:
        with pytest.raises(pyexasol.ExaError) as raised:
            harness.backfill()
        message = str(raised.value)
        for part in ("GAMES", "/games?year=2020", games_key(2020), "make deploy"):
            assert part in message
        assert harness.mock_hits(GAMES_PATH) == 0
        run = harness.runs()[-1]
        assert run["RUN_STATUS"] == "FAILED"
        assert "make deploy" in run["DETAIL"]
        assert load_log(harness) == {}
    finally:
        harness.deploy_plan(BASE_VARIANT)


def test_properties_unchanged_after_runs(harness: Harness, mock: Mock) -> None:
    """Scenario: The backfill leaves the virtual schema properties unchanged."""
    deploy_games(harness, 2020, 2020)
    harness.write_recordings([games_key(2020)])
    harness.use_replay()
    before = sorted(virtual_schema_property_rows(harness.sys))
    assert total(harness.backfill())["RUN_STATUS"] == "DONE"
    assert sorted(virtual_schema_property_rows(harness.sys)) == before
    harness.set_property("FETCH_MODE", "live")
    mock.script(GAMES_PATH, **SERVER_ERROR)
    harness.set_now("2021-02-15")
    before = sorted(virtual_schema_property_rows(harness.sys))
    assert total(harness.backfill())["RUN_STATUS"] == "DONE_WITH_FAILURES"
    assert sorted(virtual_schema_property_rows(harness.sys)) == before


def test_batches_respect_query_budget(harness: Harness) -> None:
    """Scenario: No batch exceeds the per-query call budget (7 missing slices: 2014-2020)."""
    deploy_games(harness, 2014, 2020)
    harness.set_property("MAX_CALLS_PER_QUERY", "3")
    replay(harness, [games_key(y) for y in range(2014, 2021)])
    rows = harness.backfill()
    steps = batch_steps(harness, total(rows)["RUN_ID"])
    assert [(step["SLICES"], step["STEP_STATUS"]) for step in steps] == [
        (3, "DONE"),
        (3, "DONE"),
        (1, "DONE"),
    ]
    assert (total(rows)["RUN_STATUS"], total(rows)["SLICES"]) == ("DONE", 7)


def test_orphans_block_backfill(harness: Harness, mock: Mock) -> None:
    """Scenario (load-plan): Orphan slices are reported and block fetching their table."""
    harness.set_now(NOW)
    for year in (2017, 2018, 2019):
        harness.register_slice(GAMES, games_key(year), 2, loaded_by="BACKFILL#7")
    harness.deploy_plan("games-2019", {"now_override": NOW})
    rows = harness.backfill()
    assert harness.mock_hits(GAMES_PATH) == 0
    assert stored_keys(harness, RATINGS) == {ratings_key(2019), ratings_key(2020)}
    games = table_row(rows, GAMES)
    for part in ("2 orphan slice", games_key(2017), games_key(2018), "Migrate or delete"):
        assert part in games["DETAIL"]
    assert (games["SLICES"], games["CALLS_NEEDED"]) == (0, 1)
    assert total(rows)["RUN_STATUS"] == "DONE"
