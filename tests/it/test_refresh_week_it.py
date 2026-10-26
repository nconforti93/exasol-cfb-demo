"""Scenarios of `loader/refresh-week` against the `TEST_` namespace (zero CFBD calls).

`EXECUTE SCRIPT TEST_CFB_INFRA.REFRESH_WEEK(season, week, season_type)` runs the bundled
`loader/lua/loadlib.lua` + `loader/lua/refresh_week.lua`. The module plan is the `refresh` variant
(`GAMES` and `RATINGS_SP` weekly, `TEAMS` and `CALENDAR` static, from 2019) with the test clock at
2020-10-20; scenarios with registered steps deploy the `refresh-steps`, `refresh-step-fail` or
`refresh-step-sleep` variant, whose steps are the harness's `TEST_STEP_*` scripts.

The stored calendar is the synthetic `/calendar` (regular weeks 1-15, postseason week 1), staged
into `CFB_CORE.CALENDAR` by `stage_slices` without a mock request. Live scenarios read the mock API;
where a scenario counts calls, its `/info` counts every data request (`script_info(counting=True)`).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from itertools import pairwise
from typing import Any

import pyexasol
import pytest

from tests.it import step_scripts
from tests.it.backfill_helpers import (
    GAMES,
    GAMES_PATH,
    IDLE,
    RATINGS,
    SERVER_ERROR,
    WAIT_SECONDS,
    core_counts,
    games_key,
    games_requests,
    load_log,
    ratings_key,
    session_status,
    wait_for,
)
from tests.it.harness import Harness, Mock
from tests.it.loader_support import script_call_sql, variant_text
from tests.synthetic import fake

pytestmark = pytest.mark.it

NOW = "2020-10-20"
PLAN_VARIANT = "refresh"
PLAN_CONFIG = {"now_override": NOW}

CALENDAR = "CALENDAR"
TEAMS = "TEAMS"
RATINGS_PATH = "/ratings/sp"
INFO_PATH = "/info"
CHANGED_GAME = 401628320
NEW_GAME = 401628321
HALF_STEP_SCRIPT = "TEST_STEP_HALF"
RESULT_COLUMNS = [
    ("RUN_ID", "DECIMAL", 18, 0),
    ("STEP_NO", "DECIMAL", 3, 0),
    ("STEP_NAME", "VARCHAR", 160, None),
    ("STEP_STATUS", "VARCHAR", 10, None),
    ("SECONDS", "DECIMAL", 9, 3),
    ("SLICES", "DECIMAL", 9, 0),
    ("ROWS_LOADED", "DECIMAL", 18, 0),
    ("CALLS", "DECIMAL", 9, 0),
    ("DETAIL", "VARCHAR", 2000, None),
]


# --- helpers ---------------------------------------------------------------------------------


def store_calendar(harness: Harness, year: int) -> None:
    """The synthetic `/calendar` of `year` in `CFB_CORE.CALENDAR`, registered final."""
    harness.stage_slices(CALENDAR, f"P_YEAR = {year}")


def store(harness: Harness, table: str, year: int, *, final: bool) -> str:
    """The synthetic slice of `table` for `year` in `CFB_CORE`, registered (non-)final."""
    [key] = harness.stage_slices(table, f"P_YEAR = {year}")
    if not final:
        harness.query(
            f"UPDATE {harness.meta}.LOAD_LOG SET IS_FINAL = FALSE "
            "WHERE TABLE_NAME = {t} AND SLICE_KEY = {k}",
            {"t": table, "k": key},
        )
    return key


def timeline(rows: Iterable[dict[str, Any]]) -> list[tuple[int, str, str]]:
    return [(int(r["STEP_NO"]), str(r["STEP_NAME"]), str(r["STEP_STATUS"])) for r in rows]


def last_run(harness: Harness) -> dict[str, Any]:
    return harness.runs()[-1]


def data_paths(harness: Harness) -> list[str]:
    """The paths of the data requests the mock received (everything but `/info`)."""
    return [str(entry["path"]) for entry in harness.mock.log() if entry["path"] != INFO_PATH]


def step_calls(harness: Harness) -> list[tuple[Any, ...]]:
    return harness.query(
        "SELECT RUN_ID, SEASON, WEEK, SEASON_TYPE, STEP_SCRIPT "
        f"FROM {harness.infra}.{step_scripts.CALLS_TABLE} ORDER BY CALL_NO"
    )


def refresh_error(
    harness: Harness, season: int | None, week: int | None, season_type: str | None
) -> str:
    with pytest.raises(pyexasol.ExaError) as raised:
        harness.refresh(season, week, season_type)
    return str(raised.value)


def core_rows(harness: Harness, table: str) -> list[str]:
    return sorted(map(repr, harness.query(f"SELECT * FROM {harness.core}.{table}")))


def plan_with_steps(steps: str) -> str:
    """The `refresh` variant with other registered steps (YAML list items)."""
    return variant_text(PLAN_VARIANT).replace("refresh_steps: []", f"refresh_steps:\n{steps}")


# --- scenarios -------------------------------------------------------------------------------


def test_refresh_week_returns_timeline(harness: Harness) -> None:
    """Scenario: One call refreshes the season and returns the timeline (replay mode)."""
    store_calendar(harness, 2020)
    keys = [store(harness, GAMES, 2020, final=False), store(harness, RATINGS, 2020, final=False)]
    harness.register_slice(GAMES, games_key(2019), 2, final=False)
    other_season = load_log(harness)[(GAMES, games_key(2019))]
    harness.use_replay()
    statement = harness.owner.execute(
        script_call_sql(f"{harness.infra}.REFRESH_WEEK", 2020, 7, "regular")
    )
    columns = statement.columns()
    rows = [dict(zip(columns, row, strict=True)) for row in statement.fetchall()]
    assert [
        (name, spec["type"], spec.get("precision") or spec.get("size"), spec.get("scale"))
        for name, spec in columns.items()
    ] == RESULT_COLUMNS
    run = last_run(harness)
    assert timeline(rows) == [
        (1, "CHECK", "DONE"),
        (2, "FETCH GAMES", "DONE"),
        (3, "FETCH RATINGS_SP", "DONE"),
    ]
    assert {row["RUN_ID"] for row in rows} == {run["RUN_ID"]}
    assert all(row["SECONDS"] is not None and float(row["SECONDS"]) >= 0 for row in rows)
    assert [(row["SLICES"], row["CALLS"]) for row in rows] == [(0, 0), (1, 0), (1, 0)]
    assert [row["ROWS_LOADED"] for row in rows[1:]] == [
        core_counts(harness, GAMES)[keys[0]],
        core_counts(harness, RATINGS)[keys[1]],
    ]
    log = load_log(harness)
    for table, key in ((GAMES, keys[0]), (RATINGS, keys[1])):
        assert (log[(table, key)]["IS_FINAL"], log[(table, key)]["LOADED_BY"]) == (
            False,
            f"REFRESH_WEEK#{run['RUN_ID']}",
        )
    assert log[(GAMES, games_key(2019))] == other_season
    fetched = harness.query(f"SELECT SLICE_KEY, CONTEXT FROM {harness.meta}.FETCH_LOG")
    assert sorted(fetched) == sorted((key, f"REFRESH_WEEK#{run['RUN_ID']}") for key in keys)
    assert (run["RUN_KIND"], run["RUN_ARGS"], run["RUN_STATUS"]) == (
        "REFRESH_WEEK",
        "2020, 7, regular",
        "DONE",
    )
    steps = harness.steps(run["RUN_ID"])
    assert timeline(steps) == timeline(rows)
    for column in ("SLICES", "ROWS_LOADED", "CALLS"):
        assert run[column] == sum(step[column] for step in steps)


def test_refreshed_rows_replace_previous(harness: Harness, mock: Mock) -> None:
    """Scenario: Refreshed rows replace the previous rows of a slice (live mode, mock API)."""
    store_calendar(harness, 2020)
    harness.refresh(2020, 7, "regular")
    scores = f"SELECT ID, HOME_POINTS, AWAY_POINTS FROM {harness.core}.GAMES ORDER BY ID"
    before = harness.query(scores)
    assert (CHANGED_GAME, 0, 0) in before
    mock.script(GAMES_PATH, query={"year": "2020"}, body=fake.changed_games_body(2020, 1))
    harness.refresh(2020, 7, "regular")
    after = harness.query(scores)
    assert (CHANGED_GAME, 31, 24) in after
    assert NEW_GAME in {row[0] for row in after}
    assert len(after) == len(before) + 1
    assert len({row[0] for row in after}) == len(after)
    assert core_counts(harness, GAMES) == {games_key(2020): len(after)}
    assert load_log(harness)[(GAMES, games_key(2020))]["ROWS_LOADED"] == len(after)


def test_refresh_after_final_date_finalizes(harness: Harness, mock: Mock) -> None:
    """Scenario: A refresh after the season's final date makes the season final."""
    store_calendar(harness, 2020)
    store(harness, GAMES, 2020, final=False)
    harness.set_now("2021-02-15")
    first = harness.refresh(2020, 1, "postseason")
    assert games_requests(harness) == ["2020"]
    entry = load_log(harness)[(GAMES, games_key(2020))]
    assert (entry["IS_FINAL"], entry["LOADED_BY"]) == (True, f"REFRESH_WEEK#{first[0]['RUN_ID']}")
    second = harness.refresh(2020, 1, "postseason")
    assert games_requests(harness) == ["2020"]
    assert timeline(second) == [(1, "CHECK", "DONE")]


def test_postseason_weeks_by_season_type(harness: Harness) -> None:
    """Scenario: Postseason weeks are addressed by season type."""
    harness.deploy_plan("refresh-steps", {"now_override": "2021-01-05"})
    store_calendar(harness, 2020)
    first = harness.refresh(2020, 1, "postseason")
    second = harness.refresh(2020, 1, None)
    for rows in (first, second):
        assert timeline(rows)[0] == (1, "CHECK", "DONE")
        assert all(row["STEP_STATUS"] == "DONE" for row in rows)
    first_run, second_run = first[0]["RUN_ID"], second[0]["RUN_ID"]
    record = step_scripts.RECORD_SCRIPT
    assert step_calls(harness) == [
        (first_run, 2020, 1, "postseason", record),
        (first_run, 2020, 1, "postseason", record),
        (second_run, 2020, 1, "regular", record),
        (second_run, 2020, 1, "regular", record),
    ]


def test_completed_season_fetches_nothing(harness: Harness, mock: Mock) -> None:
    """Scenario: Refreshing a completed season fetches nothing (live mode, mock API)."""
    store_calendar(harness, 2019)
    for table, key in ((GAMES, games_key(2019)), (RATINGS, ratings_key(2019))):
        harness.register_slice(table, key, 2)
    rows = harness.refresh(2019, 5, "regular")
    assert timeline(rows) == [(1, "CHECK", "DONE")]
    assert rows[0]["CALLS"] == 0
    assert mock.hits() == 0
    run = last_run(harness)
    assert (run["RUN_STATUS"], run["SLICES"], run["CALLS"]) == ("DONE", 0, 0)


def test_static_entries_not_refreshed(harness: Harness, mock: Mock) -> None:
    """Scenario: Static entries are not refreshed, even when stored as non-final."""
    store_calendar(harness, 2020)
    [teams] = [row["SLICE_KEY"] for row in harness.load_slices(TEAMS)]
    harness.register_slice(TEAMS, teams, 3)
    harness.query(f"UPDATE {harness.meta}.LOAD_LOG SET IS_FINAL = FALSE")
    stored = {row["TABLE_NAME"] for row in harness.load_slices() if row["IS_STORED"]}
    assert stored == {TEAMS, CALENDAR}
    static_before = load_log(harness)
    rows = harness.refresh(2020, 7, "regular")
    assert timeline(rows) == [
        (1, "CHECK", "DONE"),
        (2, "FETCH GAMES", "DONE"),
        (3, "FETCH RATINGS_SP", "DONE"),
    ]
    assert sorted(set(data_paths(harness))) == [GAMES_PATH, RATINGS_PATH]
    after = load_log(harness)
    assert {key[0] for key in static_before} == {TEAMS, CALENDAR}
    for key, row in static_before.items():
        assert after[key] == row


def test_invalid_arguments_rejected(harness: Harness, mock: Mock) -> None:
    """Scenario: Invalid arguments are rejected before any request."""
    store_calendar(harness, 2020)
    store(harness, GAMES, 2020, final=False)
    log_before, rows_before = load_log(harness), core_rows(harness, GAMES)
    cases: list[tuple[tuple[int | None, int | None, str | None], list[str]]] = [
        ((2020, 23, "regular"), ["week 23", "regular", "not in the 2020 calendar"]),
        ((2020, 2, "postseason"), ["week 2", "postseason", "not in the 2020 calendar"]),
        ((2020, 7, "bowl"), ["season_type", "'bowl'", "unknown"]),
        ((2021, 1, "regular"), ["season 2021", "no 2021 calendar is stored", "BACKFILL"]),
        ((None, 7, None), ["season", "required"]),
        ((2020, None, "regular"), ["week", "required"]),
    ]
    for arguments, parts in cases:
        message = refresh_error(harness, *arguments)
        run = last_run(harness)
        for part in [*parts, "CHECK", f"run {run['RUN_ID']}"]:
            assert part in message, (arguments, part, message)
        assert run["RUN_STATUS"] == "FAILED"
        [check] = harness.steps(run["RUN_ID"])
        assert (check["STEP_NAME"], check["STEP_STATUS"]) == ("CHECK", "FAILED")
        assert parts[-1] in check["DETAIL"]
    assert "regular weeks 1-15, postseason week 1" in refresh_error(harness, 2020, 23, "regular")
    assert mock.hits() == 0
    assert load_log(harness) == log_before
    assert core_rows(harness, GAMES) == rows_before


def test_unaffordable_refresh_fails_before_request(harness: Harness, mock: Mock) -> None:
    """Scenario: A refresh that cannot afford its calls fails before any request."""
    store_calendar(harness, 2020)
    harness.script_info(used=999, limit=1000)
    message = refresh_error(harness, 2020, 7, "regular")
    for part in ("CHECK", "needs 2 call(s)", "1 is available", "REFRESH_SPENDABLE"):
        assert part in message, (part, message)
    assert data_paths(harness) == []
    run = last_run(harness)
    assert timeline(harness.steps(run["RUN_ID"])) == [(1, "CHECK", "FAILED")]
    assert (run["RUN_STATUS"], run["CALLS"]) == ("FAILED", 0)


def test_refresh_may_use_reserve(harness: Harness, mock: Mock) -> None:
    """Scenario: The refresh may use the reserve that backfills keep."""
    store_calendar(harness, 2020)
    harness.script_info(counting=True, used=900, limit=1000)
    quota = harness.quota()
    assert (quota["REFRESH_SPENDABLE"], quota["BACKFILL_SPENDABLE"]) == (100, 0)
    rows = harness.refresh(2020, 7, "regular")
    assert [(row["STEP_NAME"], row["SLICES"], row["CALLS"]) for row in rows[1:]] == [
        ("FETCH GAMES", 1, 1),
        ("FETCH RATINGS_SP", 1, 1),
    ]
    run = last_run(harness)
    assert (run["RUN_STATUS"], run["CALLS"]) == ("DONE", 2)
    assert (run["REMAINING_BEFORE"], run["REMAINING_AFTER"]) == (100, 98)


def test_failed_fetch_keeps_previous_data(harness: Harness, mock: Mock) -> None:
    """Scenario: A failed fetch keeps the previous data and records the failed step."""
    store_calendar(harness, 2020)
    key = store(harness, GAMES, 2020, final=False)
    log_before, rows_before = load_log(harness)[(GAMES, key)], core_rows(harness, GAMES)
    harness.script_info(counting=True, used=0, limit=1000)
    mock.script(GAMES_PATH, query={"year": "2020"}, **SERVER_ERROR)
    message = refresh_error(harness, 2020, 7, "regular")
    run = last_run(harness)
    for part in ("FETCH GAMES", key, f"run {run['RUN_ID']}", "500"):
        assert part in message, (part, message)
    assert load_log(harness)[(GAMES, key)] == log_before
    assert core_rows(harness, GAMES) == rows_before
    steps = harness.steps(run["RUN_ID"])
    assert timeline(steps) == [(1, "CHECK", "DONE"), (2, "FETCH GAMES", "FAILED")]
    assert "500" in steps[1]["DETAIL"] and key in steps[1]["DETAIL"]
    assert (steps[1]["SLICES"], steps[1]["CALLS"]) == (0, 1)
    assert (run["RUN_STATUS"], run["CALLS"]) == ("FAILED", 1)
    assert "FETCH GAMES" in run["DETAIL"]
    assert RATINGS_PATH not in data_paths(harness)
    assert harness.failures() == []
    assert harness.query(f"SELECT COUNT(*) FROM {harness.stage}.GAMES") == [(0,)]


def test_registered_steps_run_in_order(harness: Harness) -> None:
    """Scenario: Registered steps run after the fetch, in order."""
    harness.deploy_plan("refresh-steps", {"now_override": NOW})
    store_calendar(harness, 2020)
    rows = harness.refresh(2020, 7, "regular")
    assert timeline(rows) == [
        (1, "CHECK", "DONE"),
        (2, "FETCH GAMES", "DONE"),
        (3, "FETCH RATINGS_SP", "DONE"),
        (100, "MODEL", "DONE"),
        (200, "PREDICT", "DONE"),
    ]
    run_id = rows[0]["RUN_ID"]
    record = step_scripts.RECORD_SCRIPT
    assert step_calls(harness) == [(run_id, 2020, 7, "regular", record)] * 2
    steps = harness.steps(run_id)
    for earlier, later in pairwise(steps):
        assert later["STARTED_AT"] >= earlier["ENDED_AT"]
    assert [(row["SLICES"], row["ROWS_LOADED"]) for row in rows[3:]] == [(0, 0), (0, 0)]


def test_failing_step_stops_chain(harness: Harness) -> None:
    """Scenario: A failing step stops the chain."""
    harness.deploy_plan("refresh-step-fail", {"now_override": NOW})
    store_calendar(harness, 2020)
    message = refresh_error(harness, 2020, 7, "regular")
    run = last_run(harness)
    for part in ("step 100 MODEL", f"run {run['RUN_ID']}", "forced failure"):
        assert part in message, (part, message)
    assert timeline(harness.steps(run["RUN_ID"])) == [
        (1, "CHECK", "DONE"),
        (2, "FETCH GAMES", "DONE"),
        (3, "FETCH RATINGS_SP", "DONE"),
        (100, "MODEL", "FAILED"),
    ]
    assert step_calls(harness) == []
    log = load_log(harness)
    for table, key in ((GAMES, games_key(2020)), (RATINGS, ratings_key(2020))):
        assert log[(table, key)]["LOADED_BY"] == f"REFRESH_WEEK#{run['RUN_ID']}"
        assert core_counts(harness, table)[key] > 0
    assert run["RUN_STATUS"] == "FAILED"
    assert "step 100 MODEL" in run["DETAIL"]


def test_failed_step_work_is_rolled_back(harness: Harness) -> None:
    """A registered step shares the refresh's transaction: what it wrote before failing is rolled
    back before the failure is logged and committed (under autocommit it would be committed)."""
    calls = f'"{harness.infra}"."{step_scripts.CALLS_TABLE}"'
    harness.query(
        f"CREATE OR REPLACE LUA SCRIPT {harness.infra}.{HALF_STEP_SCRIPT}"
        "(run_id, season, week, season_type) RETURNS ROWCOUNT AS\n"
        f'query([[INSERT INTO {calls} ("RUN_ID", "STEP_SCRIPT") VALUES (:r, \'HALF\')]], '
        "{r = run_id})\n"
        'error("half done")\n/'
    )
    try:
        harness.deploy_plan_text(
            plan_with_steps(
                f"  - {{step_no: 100, name: HALF, script: {harness.infra}.{HALF_STEP_SCRIPT}}}"
            ),
            {"now_override": NOW},
        )
        store_calendar(harness, 2020)
        message = refresh_error(harness, 2020, 7, "regular")
        assert "step 100 HALF" in message and "half done" in message
        assert step_calls(harness) == []
        run = last_run(harness)
        assert timeline(harness.steps(run["RUN_ID"]))[-1] == (100, "HALF", "FAILED")
        assert load_log(harness)[(GAMES, games_key(2020))]["LOADED_BY"] == (
            f"REFRESH_WEEK#{run['RUN_ID']}"
        )
    finally:
        harness.query(f"DROP SCRIPT IF EXISTS {harness.infra}.{HALF_STEP_SCRIPT}")


def test_progress_visible_to_other_sessions(harness: Harness) -> None:
    """Scenario: Other sessions see the progress while the refresh runs."""
    harness.deploy_plan("refresh-step-sleep", {"now_override": NOW})
    store_calendar(harness, 2020)
    outcome: list[Any] = []

    def running_step() -> list[dict[str, Any]] | None:
        steps = harness.steps()
        if any(s["STEP_NO"] == 100 and s["STEP_STATUS"] == "RUNNING" for s in steps):
            return steps
        return None

    with harness.owner_session() as session:
        sql = script_call_sql(f"{harness.infra}.REFRESH_WEEK", 2020, 7, "regular")

        def run() -> None:
            try:
                outcome.append(session.execute(sql).fetchall())
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            seen = wait_for(running_step, "the registered step as RUNNING")
        finally:
            worker.join(WAIT_SECONDS)
    assert timeline(seen) == [
        (1, "CHECK", "DONE"),
        (2, "FETCH GAMES", "DONE"),
        (3, "FETCH RATINGS_SP", "DONE"),
        (100, "MODEL", "RUNNING"),
    ]
    assert not isinstance(outcome[0], BaseException), outcome[0]
    assert [step["STEP_STATUS"] for step in harness.steps()] == ["DONE"] * 4
    assert float(outcome[0][-1][4]) >= step_scripts.SLEEP_SECONDS


def test_a_killed_refresh_is_abandoned_by_the_next_one(harness: Harness) -> None:
    """Review m17: a KILL STATEMENT in REFRESH_WEEK logs nothing (R0003); the next run frees it."""
    harness.deploy_plan("refresh-step-sleep", {"now_override": NOW})
    store_calendar(harness, 2020)
    outcome: list[Any] = []

    def running_step() -> bool:
        return any(s["STEP_NO"] == 100 and s["STEP_STATUS"] == "RUNNING" for s in harness.steps())

    with harness.owner_session() as session:
        killed_session = int(session.execute("SELECT CURRENT_SESSION").fetchall()[0][0])
        sql = script_call_sql(f"{harness.infra}.REFRESH_WEEK", 2020, 7, "regular")

        def run() -> None:
            try:
                outcome.append(session.execute(sql).fetchall())
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        try:
            wait_for(running_step, "the registered step as RUNNING")
            harness.kill_statement(killed_session)
        finally:
            worker.join(WAIT_SECONDS)
        assert isinstance(outcome[0], pyexasol.ExaError), outcome[0]
        [killed] = harness.runs()
        assert (killed["RUN_KIND"], killed["RUN_STATUS"]) == ("REFRESH_WEEK", "RUNNING")
        wait_for(lambda: session_status(harness, killed_session) == IDLE, "the idle session")
        harness.refresh(2020, 7, "regular")
    runs = {run["RUN_ID"]: run for run in harness.runs()}
    assert runs[killed["RUN_ID"]]["RUN_STATUS"] == "ABANDONED"
    assert "is idle" in runs[killed["RUN_ID"]]["DETAIL"]
    killed_steps = harness.steps(killed["RUN_ID"])
    assert [(s["STEP_NO"], s["STEP_STATUS"]) for s in killed_steps][-1] == (100, "FAILED")
    assert [run["RUN_STATUS"] for run in runs.values()] == ["ABANDONED", "DONE"]


def test_replay_refresh_zero_calls(harness: Harness, mock: Mock) -> None:
    """Scenario: A replay-mode refresh costs zero calls."""
    store_calendar(harness, 2020)
    harness.refresh(2020, 7, "regular")
    assert {row[0] for row in harness.query(f"SELECT REQ_SOURCE FROM {harness.core}.GAMES")} == {
        "LIVE"
    }
    harness.write_recordings([games_key(2020), ratings_key(2020)])
    harness.use_replay()
    mock.reset()
    rows = harness.refresh(2020, 7, "regular")
    assert [row["CALLS"] for row in rows] == [0, 0, 0]
    assert mock.hits() == 0
    for table, key in ((GAMES, games_key(2020)), (RATINGS, ratings_key(2020))):
        sources = harness.query(
            f"SELECT REQ_SOURCE, COUNT(*) FROM {harness.core}.{table} GROUP BY REQ_SOURCE"
        )
        assert sources == [("REPLAY", core_counts(harness, table)[key])]
        assert load_log(harness)[(table, key)]["LOADED_BY"] == f"REFRESH_WEEK#{rows[0]['RUN_ID']}"


def test_orphans_block_refresh(harness: Harness, mock: Mock) -> None:
    """Scenario (load-plan): Orphan slices are reported and block fetching their table."""
    store_calendar(harness, 2020)
    for year in (2017, 2018, 2019):
        harness.register_slice(GAMES, games_key(year), 2, loaded_by="BACKFILL#7")
    harness.deploy_plan("games-2019", {"now_override": NOW})
    message = refresh_error(harness, 2020, 7, "regular")
    for part in ("CHECK", "2 orphan slice", games_key(2017), games_key(2018), "Migrate or delete"):
        assert part in message, (part, message)
    assert data_paths(harness) == []
    run = last_run(harness)
    assert timeline(harness.steps(run["RUN_ID"])) == [(1, "CHECK", "FAILED")]


def test_fetch_steps_follow_the_plan_priority(harness: Harness) -> None:
    """The FETCH steps follow the plan's priority, not the table name (the demo's timeline)."""
    plan = variant_text(PLAN_VARIANT).replace("priority: 3", "priority: 5")
    harness.deploy_plan_text(plan, {"now_override": NOW})
    store_calendar(harness, 2020)
    harness.write_recordings([games_key(2020), ratings_key(2020)])
    harness.use_replay()
    rows = harness.refresh(2020, 7, "regular")
    assert [row["STEP_NAME"] for row in rows] == ["CHECK", "FETCH RATINGS_SP", "FETCH GAMES"]
