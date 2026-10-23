"""Unit tests of `app/backend/spend.py`: plans from manifests, the guard decision, ledger entries
for success and failure, fetch-log rows, and the live timeouts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.backend import spend
from app.backend.spend import LedgerState, QuotaState, SpendPlan
from vs.cfbd_vs import http as cfbd_http
from vs.cfbd_vs import resolver
from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.cfbd_vs.plan import RequestPlan, build_manifest, manifest_comment
from vs.host import fetchlog, ledger
from vs.host.livecalls import SliceCall

NOW = datetime(2026, 11, 3, 15, 0, 0, tzinfo=UTC)
PREVIEW_KEY = "/games/401/preview"
FAN_OUT = tuple(f"/games/teams?seasonType=both&team=Michigan&year={y}" for y in (2022, 2023, 2024))
HEADROOM = MAX_ATTEMPTS - 1


def manifest(
    slices: tuple[str, ...], sources: list[str], *, mode: str = "live", table: str = "GAMES_PREVIEW"
) -> fetchlog.Manifest:
    body = build_manifest(
        RequestPlan(table=table, slice_keys=slices, budget=10), mode=mode, read_through=True
    )
    body["sources"] = sources
    return fetchlog.parse_manifest(f"{manifest_comment(body)}\nSELECT 1")


def ledger_state(**overrides: Any) -> LedgerState:
    values: dict[str, Any] = {
        "enabled": True,
        "plan": "app",
        "plan_cap": 20,
        "plan_total": 0,
        "month": "2026-11",
        "month_cap": 20,
        "month_total": 0,
    }
    return LedgerState(**{**values, **overrides})


def quota(
    backfill: int | None = 100, refresh: int | None = 150, reserve: int | None = 50
) -> QuotaState:
    return QuotaState(
        backfill_spendable=backfill, refresh_spendable=refresh, refresh_reserve=reserve
    )


def live_plan(calls: int = 1) -> SpendPlan:
    return SpendPlan(source="LIVE", live_calls=calls, fetch_mode="LIVE")


def decide(
    plan: SpendPlan | None = None,
    *,
    max_calls: int = 1,
    state: LedgerState | None = None,
    reading: QuotaState | None = None,
    operator: tuple[bool, str] = (True, "bound to loopback address 127.0.0.1"),
) -> spend.Refusal | None:
    return spend.check_guard(
        plan or live_plan(),
        max_calls=max_calls,
        ledger_state=state or ledger_state(),
        quota=reading or quota(),
        operator_enabled=operator[0],
        operator_reason=operator[1],
    )


def test_a_live_manifest_counts_its_slices_as_planned_live_calls() -> None:
    plan = spend.plan_from_manifests([manifest(FAN_OUT, ["STORED", "LIVE"], table="GAMES_TEAMS")])
    assert plan == SpendPlan(source="LIVE", live_calls=3, fetch_mode="LIVE")


def test_replay_stored_and_contradiction_plans_cost_nothing() -> None:
    replay = spend.plan_from_manifests([manifest((PREVIEW_KEY,), ["REPLAY"], mode="replay")])
    assert replay == SpendPlan(source="REPLAY", live_calls=0, fetch_mode="REPLAY")
    stored = spend.plan_from_manifests([manifest(FAN_OUT, ["STORED"], table="GAMES")])
    assert stored == SpendPlan(source="STORED", live_calls=0, fetch_mode=None)
    contradiction = spend.plan_from_manifests([manifest((), [], table="GAMES_TEAMS")])
    assert contradiction == SpendPlan(source="NONE", live_calls=0, fetch_mode=None)
    assert spend.plan_from_manifests([]) == SpendPlan(source="NONE", live_calls=0, fetch_mode=None)


def test_several_manifests_add_up_their_live_slices() -> None:
    plan = spend.plan_from_manifests(
        [manifest((PREVIEW_KEY,), ["LIVE"]), manifest(FAN_OUT[:2], ["STORED", "LIVE"])]
    )
    assert plan.live_calls == 3 and plan.source == "LIVE"


def test_a_plan_within_every_limit_is_allowed() -> None:
    assert decide() is None


def test_a_plan_without_live_calls_is_never_refused() -> None:
    free = SpendPlan(source="REPLAY", live_calls=0, fetch_mode="REPLAY")
    broken = ledger_state(plan_cap=None, month_cap=None)
    assert (
        decide(free, max_calls=0, state=broken, reading=quota(0), operator=(False, "off")) is None
    )


def test_more_planned_calls_than_confirmed_is_refused_with_both_numbers() -> None:
    refusal = decide(live_plan(1), max_calls=0)
    assert refusal is not None and (refusal.code, refusal.status) == ("max_calls_exceeded", 409)
    assert "1 planned live call" in refusal.message and "0 confirmed" in refusal.message


def test_a_live_plan_needs_the_operator_path() -> None:
    refusal = decide(operator=(False, "bound to a non-loopback address"))
    assert refusal is not None and (refusal.code, refusal.status) == ("operator_disabled", 403)
    assert "operator path" in refusal.message and "non-loopback" in refusal.message


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"plan_cap": None}, "no ledger cap for plan app"),
        ({"month_cap": None}, "no ledger cap for 2026-11"),
    ],
)
def test_a_missing_cap_disables_live_actions_by_name(
    overrides: dict[str, Any], reason: str
) -> None:
    state = ledger_state(**overrides)
    assert spend.live_disabled_reasons(state) == [reason]
    refusal = decide(state=state)
    assert refusal is not None and (refusal.code, refusal.status) == ("live_disabled", 409)
    assert reason in refusal.message


def test_the_ledger_being_off_skips_the_ledger_rules() -> None:
    state = ledger_state(enabled=False, plan_cap=None, month_cap=None, plan_total=999)
    assert spend.live_disabled_reasons(state) == []
    assert decide(state=state) is None


def test_the_retry_headroom_counts_against_the_plan_cap() -> None:
    state = ledger_state(plan_total=18)
    refusal = decide(state=state)
    assert refusal is not None and (refusal.code, refusal.status) == ("ledger_refused", 409)
    for fragment in ("app", "20", f"{HEADROOM} calls of retry headroom", "2 left"):
        assert fragment in refusal.message
    assert decide(state=ledger_state(plan_total=16)) is None
    assert decide(state=ledger_state(plan_total=17)) is not None


def test_the_month_cap_refuses_too() -> None:
    refusal = decide(state=ledger_state(month_total=19))
    assert refusal is not None and refusal.code == "ledger_refused"
    assert "2026-11" in refusal.message


def test_the_quota_check_uses_backfill_spendable_and_names_the_reserve() -> None:
    refusal = decide(reading=quota(backfill=0, refresh=30, reserve=50))
    assert refusal is not None and (refusal.code, refusal.status) == ("quota_refused", 409)
    for fragment in ("BACKFILL_SPENDABLE 0", "REFRESH_SPENDABLE 30", "reserve 50"):
        assert fragment in refusal.message
    assert decide(reading=quota(backfill=HEADROOM + 1)) is None
    assert decide(reading=quota(backfill=HEADROOM)) is not None


def test_an_unreadable_quota_refuses_a_live_plan() -> None:
    refusal = spend.check_guard(
        live_plan(),
        max_calls=1,
        ledger_state=ledger_state(),
        quota=None,
        operator_enabled=True,
        operator_reason="on",
    )
    assert refusal is not None and refusal.code == "quota_refused"


def refusal_code(**kwargs: Any) -> str | None:
    refusal = decide(**kwargs)
    return None if refusal is None else refusal.code


def test_the_guards_apply_in_the_documented_order() -> None:
    no_cap = ledger_state(plan_cap=None)
    off = (False, "off")
    assert refusal_code(max_calls=0, state=no_cap, reading=quota(0), operator=off) == (
        "max_calls_exceeded"
    )
    assert refusal_code(state=no_cap, reading=quota(0), operator=off) == "operator_disabled"
    assert refusal_code(state=no_cap, reading=quota(0)) == "live_disabled"
    assert refusal_code(state=ledger_state(plan_total=19), reading=quota(0)) == "ledger_refused"
    assert refusal_code(reading=quota(0)) == "quota_refused"


def calls(*items: tuple[str, str, int | None, int | None, int | None]) -> list[SliceCall]:
    return [
        SliceCall(key, source, status, attempts, ms, 1, "2026-11-03 15:00:00.000000")
        for key, source, status, attempts, ms in items
    ]


def test_each_live_row_becomes_an_entry_charged_its_attempts() -> None:
    entries = spend.entries_for_calls(
        calls((FAN_OUT[0], "LIVE", 200, 1, 700), (FAN_OUT[1], "LIVE", 200, 2, 900)),
        plan="app",
        target="local",
        run="a1",
        now=NOW,
    )
    assert [(e.slice_key, e.status, e.attempts, e.cost, e.ms) for e in entries] == [
        (FAN_OUT[0], 200, 1, 1, 700),
        (FAN_OUT[1], 200, 2, 2, 900),
    ]
    assert all(
        (e.plan, e.target, e.run, e.error, e.remaining_before, e.remaining_after)
        == ("app", "local", "a1", None, None, None)
        for e in entries
    )
    assert entries[0].ts == ledger.timestamp(NOW)


def test_an_unknown_attempt_count_is_charged_all_attempts() -> None:
    (entry,) = spend.entries_for_calls(
        calls((PREVIEW_KEY, "LIVE", None, None, None)),
        plan="app",
        target="local",
        run="a1",
        now=NOW,
    )
    assert entry.cost == MAX_ATTEMPTS and entry.attempts is None


def test_replayed_and_stored_rows_are_not_charged() -> None:
    entries = spend.entries_for_calls(
        calls((PREVIEW_KEY, "REPLAY", None, 0, 5), (FAN_OUT[0], "STORED", None, 0, None)),
        plan="app",
        target="local",
        run="a1",
        now=NOW,
    )
    assert entries == []


def test_a_failed_statement_charges_every_planned_live_slice_all_attempts() -> None:
    entries = spend.failure_entries(
        FAN_OUT,
        "CFBD_VS.GAMES_TEAMS: HTTP 500 after 4 attempts\nsecond line (Session: 12)",
        plan="app",
        target="aws",
        run="a2",
        now=NOW,
    )
    assert [e.slice_key for e in entries] == list(FAN_OUT)
    assert all(
        (e.cost, e.status, e.attempts, e.plan, e.target, e.run)
        == (MAX_ATTEMPTS, None, None, "app", "aws", "a2")
        for e in entries
    )
    assert all(e.error == "CFBD_VS.GAMES_TEAMS: HTTP 500 after 4 attempts" for e in entries)


def test_a_long_error_is_cut_to_a_ledger_sized_line() -> None:
    (entry,) = spend.failure_entries(
        (PREVIEW_KEY,), "x" * 2000, plan="app", target="local", run="a", now=NOW
    )
    assert entry.error is not None and len(entry.error) <= spend.ERROR_LIMIT


REQ_COLUMNS = list(fetchlog.REQUEST_COLUMNS)


def req_row(
    key: str, source: str, status: int | None, attempts: int, ms: int | None, n: int
) -> Any:
    return (key, source, status, attempts, ms, n, "2026-11-03 15:00:00.000000")


def test_fetch_log_rows_follow_the_request_columns_and_the_context() -> None:
    rows_ = spend.fetch_log_rows(
        [manifest(FAN_OUT, ["STORED", "LIVE"], table="GAMES_TEAMS")],
        REQ_COLUMNS,
        [req_row(key, "LIVE", 200, 1, 700, 2) for key in FAN_OUT],
        context="APP_CONSOLE#a1",
        logged_at=NOW,
    )
    assert [(r.slice_key, r.source, r.http_status, r.rows_returned) for r in rows_] == [
        (key, "LIVE", 200, 2) for key in FAN_OUT
    ]
    assert {r.context for r in rows_} == {"APP_CONSOLE#a1"} and rows_[0].table_name == "GAMES_TEAMS"


def test_missing_rows_per_slice_are_counted_from_the_result() -> None:
    columns = [name for name in REQ_COLUMNS if name != "REQ_ROWS"]
    result = [
        (key, "LIVE", 200, 1, 700, "2026-11-03 15:00:00.000000") for key in (PREVIEW_KEY,) * 3
    ]
    (row,) = spend.fetch_log_rows(
        [manifest((PREVIEW_KEY,), ["LIVE"])],
        columns,
        result,
        context="APP_PREVIEW#a",
        logged_at=NOW,
    )
    assert row.rows_returned == 3 and row.source == "LIVE"


def test_a_single_slice_result_without_request_columns_is_counted_whole() -> None:
    (row,) = spend.fetch_log_rows(
        [manifest((PREVIEW_KEY,), ["LIVE"])],
        ["GAME_ID", "RAW_JSON"],
        [(401, "{}")],
        context="APP_PREVIEW#a",
        logged_at=NOW,
    )
    assert (row.slice_key, row.source, row.rows_returned, row.http_status) == (
        PREVIEW_KEY,
        "LIVE",
        1,
        None,
    )


def test_a_failed_statement_logs_its_planned_live_slices_with_unknown_status() -> None:
    rows_ = spend.fetch_log_rows(
        [manifest(FAN_OUT, ["LIVE"], table="GAMES_TEAMS")],
        None,
        None,
        context="APP_CONSOLE#a",
        logged_at=NOW,
    )
    assert [(r.slice_key, r.source, r.http_status, r.attempts, r.rows_returned) for r in rows_] == [
        (key, "LIVE", None, None, 0) for key in FAN_OUT
    ]


def test_the_stored_slices_of_a_stored_only_query_are_logged_from_their_rows() -> None:
    keys = tuple(f"/games?seasonType=both&year={y}" for y in (1869, 1870))
    result = [
        (key, "STORED", None, 0, None, count, None)
        for key, count in zip(keys, (12, 30), strict=True)
    ]
    rows_ = spend.fetch_log_rows(
        [manifest(keys, ["STORED"], table="GAMES")],
        REQ_COLUMNS,
        result,
        context="APP_CONSOLE#a",
        logged_at=NOW,
    )
    assert [(r.source, r.rows_returned) for r in rows_] == [("STORED", 12), ("STORED", 30)]


def test_the_live_statement_timeout_outlives_the_fetch_functions_worst_case() -> None:
    per_slice = (
        MAX_ATTEMPTS * cfbd_http.ATTEMPT_TIMEOUT_SECONDS
        + (MAX_ATTEMPTS - 1) * cfbd_http.RETRY_AFTER_LIMIT_SECONDS
        + resolver.DOH_TIMEOUT_SECONDS
    )
    assert spend.worst_case_fetch_seconds() == per_slice == 220
    assert spend.LIVE_QUERY_TIMEOUT_SECONDS == 300 > per_slice
    assert spend.LIVE_SOCKET_TIMEOUT_SECONDS == 330 > spend.LIVE_QUERY_TIMEOUT_SECONDS
    assert spend.LIVE_QUERY_TIMEOUT_SECONDS - per_slice >= 60
    assert spend.POOL_QUERY_TIMEOUT_SECONDS == 30
    assert spend.RETRY_HEADROOM == HEADROOM


def test_a_backoff_never_exceeds_the_retry_after_limit_the_worst_case_assumes() -> None:
    longest_backoff = max(
        cfbd_http.BACKOFF_BASE_SECONDS * 2**n * (1 + cfbd_http.JITTER_FRACTION)
        for n in range(MAX_ATTEMPTS - 1)
    )
    assert longest_backoff <= cfbd_http.RETRY_AFTER_LIMIT_SECONDS
