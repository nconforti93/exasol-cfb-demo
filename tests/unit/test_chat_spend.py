"""Unit tests of `app/backend/chat/spend.py` (`chat/budget`): reservations under one process lock,
the spend file (appended and flushed under a file lock, shared by processes, today's lines read
again before each reservation), and failing closed when the file cannot be written or read.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.backend.chat import budget
from app.backend.chat.budget import CapRefusal
from app.backend.chat.spend import CallPlan, Limits, Reservation, Settlement, SpendLedger

D = Decimal
OPUS_55 = "claude-opus-5-5"
NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
TEN_CENTS: dict[str, Any] = {"input_tokens": 10_000, "output_tokens": 3_000}  # $0.04 + $0.06
DAILY = Limits(daily_usd=D("1.00"), context_tokens=None)
OPEN = Limits(daily_usd=None, context_tokens=None)
LINE_FIELDS = ["ts", "process", "conversation", "model", "usage", "cost_usd", "estimated"]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def spend_path(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "chat-spend.jsonl"


def make_ledger(tmp_path: Path, clock: Callable[[], datetime] | None = None) -> SpendLedger:
    return SpendLedger(spend_path(tmp_path), process="test", clock=clock or Clock(NOW))


def old_line(ts: str, cost: str, **extra: Any) -> str:
    body = {"ts": ts, "process": "rehearsal/1", "conversation": "x", "model": OPUS_55}
    return json.dumps({**body, "usage": {}, "cost_usd": float(cost), "estimated": False, **extra})


def write_file(tmp_path: Path, *lines: str, end: str = "\n") -> Path:
    path = spend_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(line + "\n" for line in lines[:-1]) + (lines[-1] + end if lines else "")
    )
    return path


def plan(worst: str = "0.30", tokens: int = 10_000) -> CallPlan:
    return CallPlan(OPUS_55, tokens, D(worst))


def scope(chat: str, cap: str = "2.00") -> budget.CapScope:
    return budget.conversation_scope(chat, D(cap))


def granted(result: Reservation | CapRefusal) -> Reservation:
    assert isinstance(result, Reservation), result
    return result


def file_lines(tmp_path: Path) -> list[dict[str, Any]]:
    text = spend_path(tmp_path).read_text(encoding="utf-8")
    return [json.loads(line, parse_float=Decimal) for line in text.splitlines() if line]


# --- reservations ---


def test_concurrent_reservations(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        old_line("2026-10-22T23:59:59.999Z", "5.00"),
        old_line("2026-10-23T08:00:00.000Z", "0.25"),
        old_line("2026-10-23T09:30:00.000Z", "0.35"),
    )
    ledger = make_ledger(tmp_path)
    barrier = threading.Barrier(2)
    results: dict[str, Reservation | CapRefusal] = {}

    def reserve(chat: str) -> None:
        barrier.wait()
        results[chat] = ledger.reserve(plan("0.30"), [scope(chat)], DAILY, conversation=chat)

    threads = [threading.Thread(target=reserve, args=(chat,)) for chat in ("c1", "c2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [r for r in results.values() if isinstance(r, Reservation)]
    losers = [r for r in results.values() if isinstance(r, CapRefusal)]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].reason == "daily_cap"

    settlement = ledger.settle(winners[0], TEN_CENTS, OPUS_55)
    assert settlement.cost_usd == D("0.1")
    assert ledger.today_spent() == D("0.70")
    granted(ledger.reserve(plan("0.30"), [scope("c3")], DAILY, conversation="c3"))


def test_concurrent_reservations_hold_when_the_check_is_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap check sits between reading the open reservations and adding one; slowing it down
    opens the race that only the process lock closes."""
    write_file(tmp_path, old_line("2026-10-23T08:00:00.000Z", "0.60"))
    ledger = make_ledger(tmp_path)
    original = budget.money_check

    def slow(scope: budget.CapScope, *, committed: Decimal, worst: Decimal) -> CapRefusal | None:
        time.sleep(0.02)
        return original(scope, committed=committed, worst=worst)

    monkeypatch.setattr(budget, "money_check", slow)
    barrier = threading.Barrier(8)
    results: list[Reservation | CapRefusal] = []

    def reserve(index: int) -> None:
        barrier.wait()
        results.append(ledger.reserve(plan("0.30"), [scope(f"c{index}")], DAILY))

    threads = [threading.Thread(target=reserve, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(isinstance(r, Reservation) for r in results) == 1
    assert ledger.reserved_total() == D("0.30")


def test_open_reservations_count_against_their_scope_until_settled(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    conversation = scope("c1", "0.80")
    first = granted(ledger.reserve(plan("0.30"), [conversation], OPEN))
    granted(ledger.reserve(plan("0.30"), [conversation], OPEN))
    refusal = ledger.reserve(plan("0.30"), [conversation], OPEN)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "conversation_cap"
    assert ledger.reserved(conversation.key) == D("0.60")

    ledger.settle(first, TEN_CENTS, OPUS_55)
    assert ledger.spent(conversation.key) == D("0.1")
    assert ledger.reserved(conversation.key) == D("0.30")
    granted(ledger.reserve(plan("0.30"), [conversation], OPEN))
    assert ledger.spent("conversation:other") == D("0")


def test_the_spec_conversation_cap_admits_exactly_five_calls(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    conversation = scope("c1", "0.80")
    ledger.settle(granted(ledger.reserve(plan("0.10"), [conversation], OPEN)), TEN_CENTS, OPUS_55)
    estimate = budget.input_estimate(budget.context_tokens(TEN_CENTS), 600)
    worst = budget.worst_case(estimate, 8_000, OPUS_55, fallbacks=False)
    assert D("0.22") < worst < D("0.23")
    calls = 0
    while True:
        result = ledger.reserve(CallPlan(OPUS_55, estimate, worst), [conversation], OPEN)
        if isinstance(result, CapRefusal):
            break
        ledger.settle(result, TEN_CENTS, OPUS_55)
        calls += 1
    assert calls == 5
    assert result.reason == "conversation_cap"
    assert ledger.spent(conversation.key) == D("0.60")


def test_the_context_cap_applies_to_the_input_estimate(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    limits = Limits(daily_usd=None, context_tokens=18_000)
    granted(ledger.reserve(plan("0.10", tokens=15_300), [scope("c1")], limits))
    refusal = ledger.reserve(plan("0.10", tokens=19_300), [scope("c1")], limits)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "context_cap"


def test_the_daily_cap_is_named_before_the_conversation_cap(tmp_path: Path) -> None:
    write_file(tmp_path, old_line("2026-10-23T08:00:00.000Z", "0.98"))
    refusal = make_ledger(tmp_path).reserve(plan("0.30"), [scope("c1", "0.10")], DAILY)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "daily_cap"


def test_a_dry_run_check_reserves_nothing(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    conversation = scope("c1", "0.50")
    assert ledger.check(plan("0.30"), [conversation], DAILY) is None
    assert ledger.reserved(conversation.key) == D("0")
    granted(ledger.reserve(plan("0.30"), [conversation], DAILY))
    refusal = ledger.check(plan("0.30"), [conversation], DAILY)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "conversation_cap"


def test_release_frees_a_reservation_without_a_line(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    conversation = scope("c1")
    reservation = granted(ledger.reserve(plan("0.30"), [conversation], OPEN))
    ledger.release(reservation)
    assert ledger.reserved(conversation.key) == D("0")
    assert not spend_path(tmp_path).exists() or file_lines(tmp_path) == []


def test_settling_twice_or_a_foreign_reservation_is_an_error(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    reservation = granted(ledger.reserve(plan(), [scope("c1")], OPEN))
    ledger.settle(reservation, TEN_CENTS, OPUS_55)
    with pytest.raises(ValueError, match="not open"):
        ledger.settle(reservation, TEN_CENTS, OPUS_55)
    with pytest.raises(ValueError, match="not open"):
        ledger.release(reservation)
    other = granted(make_ledger(tmp_path / "other").reserve(plan(), [scope("c1")], OPEN))
    with pytest.raises(ValueError, match="not open"):
        ledger.settle(other, None)


def test_a_call_plan_for_a_request_carries_its_worst_case() -> None:
    call = CallPlan.for_request(OPUS_55, 10_000, max_tokens=8_000, fallbacks=True)
    assert call == CallPlan(OPUS_55, 10_000, D("0.4725"))
    assert CallPlan.for_request(OPUS_55, 10_000, max_tokens=8_000, fallbacks=False).worst_usd == (
        D("0.21")
    )


# --- settling and the spend file ---


def test_settle_appends_one_line_before_returning(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    reservation = granted(ledger.reserve(plan(), [scope("c1")], OPEN, conversation="c1"))
    settlement = ledger.settle(reservation, {**TEN_CENTS, "service_tier": "standard"}, OPUS_55)
    assert settlement == Settlement(D("0.1"), False, (), False, True)
    [entry] = file_lines(tmp_path)
    assert list(entry) == LINE_FIELDS
    assert entry["ts"] == "2026-10-23T15:00:00.000Z"
    assert entry["process"] == f"test/{os.getpid()}"
    assert entry["conversation"] == "c1"
    assert entry["model"] == OPUS_55
    assert entry["usage"] == {
        "input_tokens": 10_000,
        "output_tokens": 3_000,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    assert entry["cost_usd"] == D("0.1")
    assert entry["estimated"] is False
    assert make_ledger(tmp_path).today_spent() == D("0.1")


def test_costs_survive_the_file_exactly(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    usage = {"input_tokens": 1_200, "cache_read_input_tokens": 15_000, "output_tokens": 900}
    usage["cache_creation_input_tokens"] = 2_001
    expected = budget.call_cost(usage, OPUS_55).usd
    for _ in range(3):
        ledger.settle(granted(ledger.reserve(plan(), [scope("c1")], OPEN)), usage, OPUS_55)
    assert [entry["cost_usd"] for entry in file_lines(tmp_path)] == [expected] * 3
    assert make_ledger(tmp_path).today_spent() == expected * 3


def test_a_call_without_usage_is_settled_at_its_worst_case(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    settlement = ledger.settle(granted(ledger.reserve(plan("0.4725"), [scope("c1")], OPEN)), None)
    assert settlement.cost_usd == D("0.4725") and settlement.estimated
    [entry] = file_lines(tmp_path)
    assert entry["estimated"] is True and entry["usage"] is None
    assert entry["cost_usd"] == D("0.4725") and entry["model"] == OPUS_55


def test_malformed_usage_is_settled_at_its_worst_case(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    reservation = granted(ledger.reserve(plan("0.21"), [scope("c1")], OPEN))
    settlement = ledger.settle(reservation, {"input_tokens": -3}, OPUS_55)
    assert settlement.cost_usd == D("0.21") and settlement.estimated


def test_an_unknown_model_and_a_worst_case_overrun_are_reported(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    reservation = granted(ledger.reserve(plan("0.01"), [scope("c1")], OPEN))
    settlement = ledger.settle(reservation, TEN_CENTS, "claude-x")
    assert settlement.unknown_models == ("claude-x",)
    assert settlement.exceeded_worst_case
    assert file_lines(tmp_path)[0]["model"] == "claude-x"


def test_the_day_follows_the_utc_clock(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        old_line("2026-10-22T10:00:00.000Z", "5.00"),
        old_line("2026-10-23T08:00:00.000Z", "0.98"),
    )
    clock = Clock(NOW)
    ledger = make_ledger(tmp_path, clock)
    refusal = ledger.reserve(plan("0.21"), [scope("c1")], DAILY)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "daily_cap"
    clock.now = datetime(2026, 10, 24, 0, 0, 0, 1, tzinfo=UTC)
    reservation = granted(ledger.reserve(plan("0.21"), [scope("c1")], DAILY))
    ledger.settle(reservation, TEN_CENTS, OPUS_55)
    assert ledger.today_spent() == D("0.1")
    assert file_lines(tmp_path)[-1]["ts"] == "2026-10-24T00:00:00.000Z"


def test_the_day_is_the_utc_day_whatever_the_clock_zone(tmp_path: Path) -> None:
    local = datetime(2026, 10, 23, 23, 30, tzinfo=UTC).astimezone(timezone(timedelta(hours=2)))
    assert local.day == 24
    ledger = make_ledger(tmp_path, Clock(local))
    ledger.settle(granted(ledger.reserve(plan(), [scope("c1")], OPEN)), TEN_CENTS, OPUS_55)
    assert file_lines(tmp_path)[0]["ts"] == "2026-10-23T23:30:00.000Z"
    assert make_ledger(tmp_path, Clock(NOW)).today_spent() == D("0.1")


def test_lines_of_another_process_count_before_each_reservation(tmp_path: Path) -> None:
    server = make_ledger(tmp_path)
    rehearsal = SpendLedger(spend_path(tmp_path), process="rehearsal", clock=Clock(NOW))
    granted(server.reserve(plan("0.30"), [scope("c1")], DAILY))
    rehearsal_call = granted(rehearsal.reserve(plan("0.50"), [], OPEN))
    rehearsal.settle(rehearsal_call, {"input_tokens": 50_000, "output_tokens": 15_000}, OPUS_55)
    assert server.today_spent() == D("0.5")
    refusal = server.reserve(plan("0.30"), [scope("c2")], DAILY)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "daily_cap"


def test_lines_with_extra_fields_from_other_writers_are_read(tmp_path: Path) -> None:
    write_file(tmp_path, old_line("2026-10-23T08:00:00.000Z", "0.25", run="r1", question="RQ01"))
    assert make_ledger(tmp_path).today_spent() == D("0.25")


_APPEND_SCRIPT = """
import sys, time
from datetime import UTC, datetime
from pathlib import Path
from app.backend.chat.spend import CallPlan, Limits, SpendLedger
from decimal import Decimal
path, go, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
ledger = SpendLedger(path, process=name, clock=lambda: datetime(2026, 10, 23, 15, tzinfo=UTC))
while not go.exists():
    time.sleep(0.005)
limits = Limits(daily_usd=None, context_tokens=None)
for _ in range(40):
    reservation = ledger.reserve(CallPlan("claude-opus-5-5", 1, Decimal("0.30")), [], limits)
    ledger.settle(reservation, {"input_tokens": 10_000, "output_tokens": 3_000}, "claude-opus-5-5")
"""


def test_two_processes_append_without_losing_or_tearing_lines(tmp_path: Path) -> None:
    path = spend_path(tmp_path)
    go = tmp_path / "go"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", _APPEND_SCRIPT, str(path), str(go), name], cwd=ROOT, env=env
        )
        for name in ("server", "rehearsal")
    ]
    time.sleep(0.3)
    go.touch()
    assert [worker.wait(timeout=120) for worker in workers] == [0, 0]
    entries = file_lines(tmp_path)
    assert len(entries) == 80
    assert {entry["process"].split("/")[0] for entry in entries} == {"server", "rehearsal"}
    assert sum(entry["cost_usd"] for entry in entries) == D("8.0")
    assert make_ledger(tmp_path).today_spent() == D("8.0")


# --- failing closed ---


def test_a_failed_write_keeps_the_cost_and_stops_live_calls(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    conversation = scope("c1")
    reservation = granted(ledger.reserve(plan("0.30"), [conversation], DAILY))
    spend_path(tmp_path).mkdir(parents=True)
    settlement = ledger.settle(reservation, TEN_CENTS, OPUS_55)
    assert not settlement.written and settlement.cost_usd == D("0.1")
    assert ledger.unwritable_reason is not None
    assert "chat-spend.jsonl" in ledger.unwritable_reason
    assert ledger.spent(conversation.key) == D("0.1")
    assert ledger.reserved(conversation.key) == D("0")
    refusal = ledger.reserve(plan("0.30"), [conversation], DAILY)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "spend_log_unwritable"
    spend_path(tmp_path).rmdir()
    assert isinstance(ledger.reserve(plan("0.30"), [conversation], OPEN), CapRefusal)
    assert ledger.today_spent() == D("0.1")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_directory_fails_the_first_line(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    reservation = granted(ledger.reserve(plan("0.30"), [scope("c1")], DAILY))
    directory = spend_path(tmp_path).parent
    directory.chmod(0o500)
    try:
        assert not ledger.settle(reservation, TEN_CENTS, OPUS_55).written
    finally:
        directory.chmod(0o700)
    assert ledger.unwritable_reason is not None


@pytest.mark.parametrize(
    ("content", "end"),
    [
        ("not json", "\n"),
        ('{"ts": "2026-10-23T08:00:00.000Z"}', "\n"),
        ('{"ts": "yesterday", "cost_usd": 1}', "\n"),
        ('{"ts": "2026-10-23T08:00:00.000Z", "cost_usd": -1}', "\n"),
        ('{"ts": "2026-10-23T08:00:00.000Z", "cost_usd": true}', "\n"),
        ('{"ts": "2026-10-23T08:00:00.000Z", "cost_usd": "1"}', "\n"),
        ('{"ts": "2026-10-23T08:00:00.000Z", "cost_usd": NaN}', "\n"),
        ("[1]", "\n"),
        ('{"ts": "2026-10-23T08:00:00.000Z", "cost_usd": 0.1}', ""),
    ],
)
def test_a_spend_file_that_cannot_be_read_fails_closed(
    tmp_path: Path, content: str, end: str
) -> None:
    write_file(tmp_path, old_line("2026-10-23T07:00:00.000Z", "0.10"), content, end=end)
    ledger = make_ledger(tmp_path)
    refusal = ledger.reserve(plan(), [scope("c1")], OPEN)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "spend_log_unwritable"
    assert ledger.unwritable_reason is not None and ":2" in ledger.unwritable_reason


def test_bytes_that_are_not_utf8_fail_closed(tmp_path: Path) -> None:
    path = write_file(tmp_path, old_line("2026-10-23T07:00:00.000Z", "0.10"))
    with path.open("ab") as handle:
        handle.write(b"\xff\xfe\n")
    refusal = make_ledger(tmp_path).reserve(plan(), [scope("c1")], OPEN)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "spend_log_unwritable"


def test_a_held_file_lock_times_out_closed(tmp_path: Path) -> None:
    path = spend_path(tmp_path)
    path.parent.mkdir(parents=True)
    lock = path.with_name(path.name + ".lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        ledger = SpendLedger(path, process="test", clock=Clock(NOW), lock_timeout=0.05)
        refusal = ledger.reserve(plan(), [scope("c1")], OPEN)
    finally:
        lock.close()
    assert isinstance(refusal, CapRefusal) and refusal.reason == "spend_log_unwritable"
    assert ledger.unwritable_reason is not None and "lock" in ledger.unwritable_reason


def test_check_usable_creates_the_directory_and_reports_a_bad_path(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    assert ledger.check_usable()
    assert spend_path(tmp_path).parent.is_dir()
    assert ledger.unwritable_reason is None
    blocked = tmp_path / "blocked.jsonl"
    blocked.mkdir()
    bad = SpendLedger(blocked, process="test", clock=Clock(NOW))
    assert not bad.check_usable()
    assert bad.unwritable_reason is not None


def test_an_unreadable_file_counts_as_nothing_and_turns_live_calls_off(tmp_path: Path) -> None:
    spend_path(tmp_path).mkdir(parents=True)
    ledger = make_ledger(tmp_path)
    assert ledger.today_spent() == D("0")
    assert ledger.unwritable_reason is not None and "cannot read" in ledger.unwritable_reason
    refusal = ledger.reserve(plan(), [scope("c1")], OPEN)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "spend_log_unwritable"


def test_a_lock_file_that_cannot_be_opened_fails_closed(tmp_path: Path) -> None:
    path = spend_path(tmp_path)
    path.with_name(path.name + ".lock").mkdir(parents=True)
    refusal = make_ledger(tmp_path).reserve(plan(), [scope("c1")], OPEN)
    assert isinstance(refusal, CapRefusal) and refusal.reason == "spend_log_unwritable"
    assert "lock file" in refusal.message


def test_a_clock_without_a_timezone_is_rejected(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path, Clock(datetime(2026, 10, 23, 15, 0)))
    with pytest.raises(ValueError, match="timezone"):
        ledger.reserve(plan(), [scope("c1")], OPEN)


def test_a_fallback_call_records_its_attempts(tmp_path: Path) -> None:
    ledger = make_ledger(tmp_path)
    usage = {
        "input_tokens": 9_000,
        "output_tokens": 700,
        "iterations": [
            {"type": "message", "model": OPUS_55, "input_tokens": 3_000, "output_tokens": 400},
            {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 9_000},
        ],
    }
    reservation = granted(ledger.reserve(plan("0.50"), [scope("c1")], OPEN))
    settlement = ledger.settle(reservation, usage, "claude-opus-4-8")
    assert settlement.cost_usd == D("0.012") + D("0.008") + D("0.045")
    [entry] = file_lines(tmp_path)
    assert entry["model"] == "claude-opus-4-8"
    assert [(item["type"], item["model"]) for item in entry["usage"]["iterations"]] == [
        ("message", OPUS_55),
        ("fallback_message", "claude-opus-4-8"),
    ]
