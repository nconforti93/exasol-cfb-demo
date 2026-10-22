"""Integration tests of the quota view (spec `loader/quota`): `CFB_META.QUOTA`, no Lua.

The mock's `/info` is scripted per test (`Harness.script_info`); the view reads it through the
virtual schema. `FETCH_LOG` rows are inserted by the test, because the attribution reads only
the log. Timestamps come back as text, so they are parsed before they are compared.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from tests.it.harness import Harness, Mock

pytestmark = pytest.mark.it

NOW = "2020-10-20"
RESET_AT = "2020-11-01T00:00:00Z"
PERIOD_START = datetime(2020, 10, 1)
IN_PERIOD = "2020-10-15 12:00:00"
BEFORE_PERIOD = "2020-09-30 23:59:59"
AFTER_PERIOD = "2020-11-01 00:00:00"


def timestamp(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def script_key(
    harness: Harness, used: int, limit: int = 1000, remaining: int | None = None
) -> None:
    harness.script_info(
        used=used,
        limit=limit,
        remaining=limit - used if remaining is None else remaining,
        tier="Free",
        reset_at=RESET_AT,
    )


def spendable(harness: Harness) -> tuple[int, int, int]:
    row = harness.quota()
    return (
        int(row["PROJECT_BUDGET"]),
        int(row["REFRESH_SPENDABLE"]),
        int(row["BACKFILL_SPENDABLE"]),
    )


def log_fetches(
    harness: Harness,
    count: int,
    context: str | None,
    requested_at: str = IN_PERIOD,
    source: str = "LIVE",
    attempts: int = 1,
) -> None:
    for _ in range(count):
        harness.query(
            f"INSERT INTO {harness.meta}.FETCH_LOG "
            "(LOGGED_AT, CONTEXT, TABLE_NAME, SLICE_KEY, REQ_SOURCE, HTTP_STATUS, ATTEMPTS, MS, "
            "ROWS_RETURNED, REQUESTED_AT) VALUES "
            "(CAST({at} AS TIMESTAMP), {context}, 'GAMES', '/games?seasonType=both&year=2019', "
            "{source}, 200, {attempts!d}, 5, 1, CAST({at} AS TIMESTAMP))",
            {"at": requested_at, "context": context, "source": source, "attempts": attempts},
        )


def test_quota_view_zero_calls(harness: Harness, mock: Mock) -> None:
    """Scenario: The quota view shows the key's quota without spending a call."""
    harness.script_info(
        tier="Free", limit=1000, used=120, remaining=880, reset_at="2020-11-01T00:00:00Z"
    )
    row = harness.quota()
    assert (row["TIER_NAME"], row["MONTHLY_LIMIT"]) == ("Free", 1000)
    assert (row["USED_CALLS"], row["REMAINING_CALLS"]) == (120, 880)
    assert timestamp(row["RESET_AT"]) == datetime(2020, 11, 1)
    assert timestamp(row["PERIOD_START"]) == PERIOD_START
    assert harness.mock_hits() == harness.mock_hits("/info") == 1


def test_backfill_allowance_keeps_reserve(harness: Harness) -> None:
    """Scenario: The backfill allowance keeps the reserve."""
    script_key(harness, used=700, limit=1000, remaining=300)
    row = harness.quota()
    assert (row["MONTHLY_CALL_BUDGET"], row["REFRESH_RESERVE"]) == (1000, 150)
    assert spendable(harness) == (1000, 300, 150)


@pytest.mark.parametrize(
    ("used", "refresh", "backfill"),
    [(850, 150, 0), (900, 100, 0), (1000, 0, 0), (1200, 0, 0)],
)
def test_allowance_is_never_negative(
    harness: Harness, used: int, refresh: int, backfill: int
) -> None:
    """Scenario: The backfill allowance keeps the reserve (at and past the reserve: 0)."""
    script_key(harness, used=used, limit=1000, remaining=max(0, 1000 - used))
    assert spendable(harness) == (1000, refresh, backfill)


def test_project_budget_caps_key_quota(harness: Harness) -> None:
    """Scenario: The project budget caps a larger key quota."""
    script_key(harness, used=500, limit=3000, remaining=2500)
    assert spendable(harness) == (1000, 500, 350)


def test_remaining_calls_cap_the_allowance(harness: Harness) -> None:
    """Scenario: The project budget caps a larger key quota (the key's own remainder wins too)."""
    script_key(harness, used=500, limit=3000, remaining=120)
    assert spendable(harness) == (1000, 120, 0)


def test_deploy_override_caps_deployment(harness: Harness) -> None:
    """Scenario: A deploy-time override caps one deployment and is undone by the next deploy."""
    script_key(harness, used=50, limit=3000, remaining=2950)
    harness.deploy_plan("base", {"monthly_call_budget": "400", "refresh_reserve": "50"})
    assert spendable(harness) == (400, 350, 300)
    harness.deploy_plan("base")
    assert spendable(harness) == (1000, 950, 800)


def test_calls_attributed_by_run_kind(harness: Harness) -> None:
    """Scenario: Calls are attributed to backfill, refresh and other use."""
    script_key(harness, used=20)
    log_fetches(harness, 12, "BACKFILL#3")
    log_fetches(harness, 2, "REFRESH_WEEK#4")
    log_fetches(harness, 5, "BACKFILL#3", source="REPLAY")
    log_fetches(harness, 3, "BACKFILL#2", requested_at=BEFORE_PERIOD)
    log_fetches(harness, 2, "REFRESH_WEEK#1", requested_at=AFTER_PERIOD)
    log_fetches(harness, 4, None)
    row = harness.quota()
    assert (row["BACKFILL_CALLS"], row["REFRESH_CALLS"], row["OTHER_CALLS"]) == (12, 2, 6)


def test_retries_and_the_period_start_count(harness: Harness) -> None:
    """Scenario: Calls are attributed to backfill, refresh and other use (attempts, boundaries)."""
    script_key(harness, used=50)
    log_fetches(harness, 1, "BACKFILL#5", attempts=3)
    log_fetches(harness, 1, "REFRESH_WEEK#6", requested_at="2020-10-01 00:00:00")
    log_fetches(harness, 1, "BACKFILL#5", requested_at=AFTER_PERIOD)
    row = harness.quota()
    assert (row["BACKFILL_CALLS"], row["REFRESH_CALLS"], row["OTHER_CALLS"]) == (3, 1, 46)
    script_key(harness, used=2)
    assert harness.quota()["OTHER_CALLS"] == 0


def test_calls_to_complete_profiles(harness: Harness) -> None:
    """Scenario: The view shows what completing each profile costs."""
    plan = (
        "version: 1\nentries:\n"
        "  - {table: GAMES, profile: demo, priority: 1, slicing: season,"
        " seasons: {from: 2015, to: 2020}, gaps: [2016], refresh_class: weekly}\n"
        "  - {table: RATINGS_SP, profile: full, priority: 2, slicing: season,"
        " seasons: {from: 2014}, refresh_class: weekly}\n"
    )
    harness.deploy_plan_text(plan, {"now_override": NOW})
    script_key(harness, used=0)
    games_2020 = next(r["SLICE_KEY"] for r in harness.load_slices("GAMES") if r["SEASON"] == 2020)
    harness.register_failure("GAMES", games_2020, 2, "HTTP 500")
    status = {row["TABLE_NAME"]: row for row in harness.load_status()}
    assert status["GAMES"]["SLICES_PLANNED"] == 6
    assert status["GAMES"]["GAP_SLICES"] == 1
    assert status["GAMES"]["QUARANTINED_SLICES"] == 1
    assert status["RATINGS_SP"]["SLICES_PLANNED"] == 7
    row = harness.quota()
    assert (row["DEMO_CALLS_TO_COMPLETE"], row["FULL_CALLS_TO_COMPLETE"]) == (4, 11)


def test_budget_change_after_deploy(harness: Harness) -> None:
    """Scenario: A budget change in the plan takes effect after deploy."""
    script_key(harness, used=100, limit=1000)
    assert harness.quota()["MONTHLY_CALL_BUDGET"] == 1000
    plan = (
        "version: 1\nconfig:\n  monthly_call_budget: 800\nentries:\n"
        "  - {table: GAMES, profile: demo, priority: 1, slicing: season,"
        " seasons: {from: 2019}, refresh_class: weekly}\n"
    )
    harness.deploy_plan_text(plan)
    row = harness.quota()
    assert (row["MONTHLY_CALL_BUDGET"], row["PROJECT_BUDGET"]) == (800, 800)
    assert spendable(harness) == (800, 700, 550)
