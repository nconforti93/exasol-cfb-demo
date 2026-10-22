"""Shared helpers of the backfill and run-log integration tests (task 4.2).

Slice keys, small plans written in the test, readers of the loader's logs and of the mock's request
log, and waits for another session. Nothing here creates state on its own except through the
`Harness` it is given.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from tests.it.harness import SYS, Harness

NOW = "2020-10-20"
GAMES = "GAMES"
RATINGS = "RATINGS_SP"
TOTAL = "*"
GAMES_PATH = "/games"
SERVER_ERROR: dict[str, Any] = {"status": 500, "retry_after": 60}
"""HTTP 500 with `Retry-After: 60`: the fetch UDF waits at most 30 s, so it gives up after one
attempt instead of four, and a scripted failure costs seconds instead of minutes."""
WAIT_SECONDS = 40.0
POLL_SECONDS = 0.2
IDLE = "IDLE"


def games_key(year: int) -> str:
    return f"/games?seasonType=both&year={year}"


def ratings_key(year: int) -> str:
    return f"/ratings/sp?year={year}"


def games_plan(first: int, last: int | None = None, gaps: Iterable[int] = ()) -> str:
    """A plan with one weekly `GAMES` entry (entry 1) for `first`..`last` (open: current season)."""
    seasons = f"{{from: {first}}}" if last is None else f"{{from: {first}, to: {last}}}"
    listed = list(gaps)
    gap_text = f", gaps: [{', '.join(str(g) for g in listed)}]" if listed else ""
    return (
        "version: 1\nentries:\n"
        "  - {table: GAMES, profile: demo, priority: 1, slicing: season, "
        f"seasons: {seasons}{gap_text}, refresh_class: weekly}}\n"
    )


def deploy_games(
    harness: Harness, first: int, last: int | None = None, gaps: Iterable[int] = ()
) -> None:
    """Deploy `games_plan` with the test clock at `NOW`; the reset puts the base plan back."""
    harness.deploy_plan_text(games_plan(first, last, gaps), {"now_override": NOW})


def total(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The total row of a `BACKFILL` result (always the last one)."""
    assert rows[-1]["TABLE_NAME"] == TOTAL
    return rows[-1]


def table_row(rows: list[dict[str, Any]], table: str) -> dict[str, Any]:
    return next(row for row in rows if row["TABLE_NAME"] == table)


def load_log(harness: Harness) -> dict[tuple[str, str], dict[str, Any]]:
    rows = harness.query_dicts(f"SELECT * FROM {harness.meta}.LOAD_LOG")
    return {(row["TABLE_NAME"], row["SLICE_KEY"]): row for row in rows}


def stored_keys(harness: Harness, table: str = GAMES) -> set[str]:
    return {key for name, key in load_log(harness) if name == table}


def fetch_log_keys(harness: Harness) -> set[str]:
    return {str(row[0]) for row in harness.query(f"SELECT SLICE_KEY FROM {harness.meta}.FETCH_LOG")}


def replay(harness: Harness, keys: Iterable[str]) -> None:
    """Record the synthetic response of every key, then switch to replay mode."""
    harness.write_recordings(keys)
    harness.use_replay()


def batch_steps(harness: Harness, run_id: int) -> list[dict[str, Any]]:
    """The `BATCH <table> #<entry>` steps of a run, in step order."""
    return [step for step in harness.steps(run_id) if str(step["STEP_NAME"]).startswith("BATCH ")]


def step_keys(step: dict[str, Any]) -> list[str]:
    """The slice keys a batch step names: its whole detail, or what follows `(slices: `."""
    detail = str(step["DETAIL"])
    if " (slices: " in detail:
        detail = detail.rsplit(" (slices: ", 1)[1].removesuffix(")")
    return detail.split(", ")


def games_requests(harness: Harness) -> list[str]:
    """The years of the `/games` requests the mock received, in arrival order."""
    return [
        entry["query"]["year"][0] for entry in harness.mock.log() if entry["path"] == GAMES_PATH
    ]


def core_counts(harness: Harness, table: str = GAMES) -> dict[str, int]:
    rows = harness.query(f"SELECT REQ_SLICE, COUNT(*) FROM {harness.core}.{table} GROUP BY 1")
    return {str(key): int(count) for key, count in rows}


def wait_for[T](probe: Callable[[], T | None], what: str, seconds: float = WAIT_SECONDS) -> T:
    """Poll `probe` until it returns something other than None or False."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = probe()
        if found is not None and found is not False:
            return found
        time.sleep(POLL_SECONDS)
    raise AssertionError(f"timed out after {seconds:g} s waiting for {what}")


def session_status(harness: Harness, session_id: int) -> str | None:
    """`EXA_ALL_SESSIONS.STATUS` of a session, or None once it is gone."""
    rows = harness.query(
        "SELECT STATUS FROM EXA_ALL_SESSIONS WHERE SESSION_ID = {id!d}",
        {"id": session_id},
        user=SYS,
    )
    return str(rows[0][0]) if rows else None


def running_batch(harness: Harness) -> dict[str, Any] | None:
    """The `RUNNING` batch step of any run, once one is committed."""
    rows = harness.query_dicts(
        f"SELECT RUN_ID, STEP_NO FROM {harness.meta}.REFRESH_LOG "
        "WHERE STEP_STATUS = 'RUNNING' AND STEP_NAME LIKE 'BATCH %'"
    )
    return rows[0] if rows else None
