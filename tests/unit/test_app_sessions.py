"""Unit tests of `app/backend/sessions.py`: per-session state, a fake clock and real threads."""

from __future__ import annotations

import threading
import time
from functools import partial
from typing import Any

import pytest

from app.backend import sessions
from app.backend.sessions import (
    ConsoleBusy,
    FlightResult,
    GateTimeout,
    LiveGate,
    QueryLog,
    Session,
    SessionStore,
)
from vs.host.livecalls import SliceCall


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def record_run[V](runs: list[V], value: V) -> V:
    runs.append(value)
    return value


def new_session(clock: FakeClock | None = None, **limits: Any) -> Session:
    fake = clock or FakeClock()
    return Session("s1", clock=fake, wall=fake, **limits)


def log_entry(log: QueryLog, sql: str = "SELECT 1", **overrides: Any) -> sessions.QueryLogEntry:
    fields: dict[str, Any] = {
        "sql": sql,
        "principal": "app",
        "user": "CFB_APP_WEB",
        "page": "odds",
        "ms": 12,
        "rows": 3,
    }
    return log.record(**{**fields, **overrides})


def test_the_query_log_numbers_entries_and_lists_the_newest_first() -> None:
    log = QueryLog()
    first, second = log_entry(log, "SELECT 1"), log_entry(log, "SELECT 2")
    assert (first.id, second.id) == (1, 2)
    assert [entry.sql for entry in log.entries()] == ["SELECT 2", "SELECT 1"]
    assert log.get(1) == first and log.get(99) is None


def test_the_query_log_keeps_the_last_fifty() -> None:
    log = QueryLog()
    for number in range(60):
        log_entry(log, f"SELECT {number}")
    entries = log.entries()
    assert len(entries) == sessions.QUERY_LOG_ENTRIES == 50
    assert entries[0].id == 60 and entries[-1].id == 11
    assert log.get(10) is None and log.get(11) is not None


@pytest.mark.parametrize("principal", ["app", "reader", "chat"])
def test_the_query_log_accepts_the_app_reader_and_chat_principals(principal: str) -> None:
    assert log_entry(QueryLog(), principal=principal).principal == principal


def test_the_query_log_refuses_any_other_principal() -> None:
    for principal in ("owner", "operator", "sys", ""):
        with pytest.raises(ValueError, match="principal"):
            log_entry(QueryLog(), principal=principal)


def test_an_entry_keeps_its_request_lines_calls_and_error() -> None:
    call = SliceCall("/games/1/preview", "LIVE", 200, 1, 700, 1, "2026-10-22 15:00:00.000000")
    entry = log_entry(QueryLog(), slices=[call], calls=1, error=None, context="APP_PREVIEW#a1")
    assert entry.slices == (call,) and entry.calls == 1 and entry.context == "APP_PREVIEW#a1"
    assert log_entry(QueryLog(), error="boom").error == "boom"


def test_the_query_log_is_thread_safe() -> None:
    log = QueryLog()

    def worker() -> None:
        for _ in range(100):
            log_entry(log)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    entries = log.entries()
    assert len(entries) == 50
    assert len({entry.id for entry in entries}) == 50 and entries[0].id == 800


def test_the_preview_cache_returns_a_fresh_entry_with_its_time() -> None:
    clock = FakeClock()
    session = new_session(clock)
    session.previews.put(7, {"home": "A"})
    clock.now += 60
    cached = session.previews.get(7)
    assert cached is not None and cached.value == {"home": "A"} and cached.cached_at == 1000.0
    assert session.previews.get(8) is None


def test_a_preview_expires_after_fifteen_minutes() -> None:
    clock = FakeClock()
    session = new_session(clock)
    session.previews.put(7, "x")
    clock.now += sessions.PREVIEW_TTL_SECONDS - 1
    assert session.previews.get(7) is not None
    clock.now += 1
    assert session.previews.get(7) is None
    assert sessions.PREVIEW_TTL_SECONDS == 15 * 60


def test_the_cache_holds_twenty_previews_and_drops_the_least_recently_used() -> None:
    session = new_session()
    for game in range(20):
        session.previews.put(game, game)
    assert session.previews.get(0) is not None
    session.previews.put(20, 20)
    assert len(session.previews) == sessions.PREVIEW_CACHE_ENTRIES == 20
    assert session.previews.get(1) is None
    assert session.previews.get(0) is not None and session.previews.get(20) is not None


def test_a_cached_preview_is_served_without_a_fetch() -> None:
    session = new_session()
    session.previews.put(7, "cached")
    calls: list[str] = []
    result = session.preview_flight(7, lambda: record_run(calls, "fresh"))
    assert result == FlightResult("cached", cached=True, cached_at=1000.0)
    assert calls == []


def test_a_fetch_is_cached_for_the_next_call() -> None:
    session = new_session()
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return "fresh"

    first = session.preview_flight(7, fetch)
    second = session.preview_flight(7, fetch)
    assert (first.value, first.cached) == ("fresh", False)
    assert (second.value, second.cached) == ("fresh", True)
    assert len(calls) == 1


def test_force_fetches_again_and_replaces_the_cache() -> None:
    session = new_session()
    values = iter(["one", "two"])
    session.preview_flight(7, lambda: next(values))
    forced = session.preview_flight(7, lambda: next(values), force=True)
    assert (forced.value, forced.cached) == ("two", False)
    assert session.preview_flight(7, lambda: "never").value == "two"


class Gate:
    """A fetch that blocks until released, counting how often it ran."""

    def __init__(self, value: str = "fresh", error: Exception | None = None) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.runs = 0
        self.value = value
        self.error = error

    def __call__(self) -> str:
        self.runs += 1
        self.started.set()
        assert self.release.wait(10)
        if self.error:
            raise self.error
        return self.value


def test_two_concurrent_clicks_fetch_once_and_the_waiter_is_served_from_the_cache() -> None:
    session = new_session()
    fetch = Gate()
    results: list[Any] = [None, None]

    def leader() -> None:
        results[0] = session.preview_flight(7, fetch)

    def waiter() -> None:
        results[1] = session.preview_flight(7, fetch)

    first = threading.Thread(target=leader)
    first.start()
    assert fetch.started.wait(5)
    second = threading.Thread(target=waiter)
    second.start()
    time.sleep(0.2)
    assert second.is_alive()
    fetch.release.set()
    first.join(5)
    second.join(5)
    assert fetch.runs == 1
    assert (results[0].value, results[0].cached) == ("fresh", False)
    assert (results[1].value, results[1].cached) == ("fresh", True)


def test_many_waiters_share_one_fetch() -> None:
    session = new_session()
    fetch = Gate()
    results: list[Any] = [None] * 6

    def click(index: int) -> None:
        results[index] = session.preview_flight(7, fetch)

    threads = [threading.Thread(target=click, args=(index,)) for index in range(6)]
    threads[0].start()
    assert fetch.started.wait(5)
    for thread in threads[1:]:
        thread.start()
    time.sleep(0.2)
    fetch.release.set()
    for thread in threads:
        thread.join(5)
    assert fetch.runs == 1
    assert sorted(result.cached for result in results) == [False, True, True, True, True, True]


def test_a_forced_click_during_a_flight_joins_it_instead_of_spending_again() -> None:
    session = new_session()
    fetch = Gate()
    results: list[Any] = [None, None]
    first = threading.Thread(
        target=lambda: results.__setitem__(0, session.preview_flight(7, fetch))
    )
    first.start()
    assert fetch.started.wait(5)
    second = threading.Thread(
        target=lambda: results.__setitem__(1, session.preview_flight(7, fetch, force=True))
    )
    second.start()
    time.sleep(0.2)
    fetch.release.set()
    first.join(5)
    second.join(5)
    assert fetch.runs == 1 and results[1].value == "fresh"


def test_a_failed_fetch_is_shared_by_the_waiters_and_never_repeated() -> None:
    session = new_session()
    fetch = Gate(error=RuntimeError("cfbd failed"))
    errors: list[Any] = [None, None]

    def click(index: int) -> None:
        try:
            session.preview_flight(7, fetch)
        except RuntimeError as error:
            errors[index] = error

    first = threading.Thread(target=click, args=(0,))
    first.start()
    assert fetch.started.wait(5)
    second = threading.Thread(target=click, args=(1,))
    second.start()
    time.sleep(0.2)
    fetch.release.set()
    first.join(5)
    second.join(5)
    assert fetch.runs == 1
    assert all(str(error) == "cfbd failed" for error in errors)
    assert session.previews.get(7) is None
    again = session.preview_flight(7, lambda: "recovered")
    assert again.value == "recovered" and not again.cached


def test_different_games_fly_in_parallel() -> None:
    session = new_session()
    barrier = threading.Barrier(2, timeout=5)

    def fetch() -> str:
        barrier.wait()
        return "ok"

    results: list[Any] = [None, None]
    threads = [
        threading.Thread(
            target=lambda game=game: results.__setitem__(game, session.preview_flight(game, fetch))
        )
        for game in (0, 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert [result.value for result in results] == ["ok", "ok"]


def test_the_same_game_in_two_sessions_is_two_fetches() -> None:
    store = SessionStore()
    runs: list[str] = []
    for name in ("a", "b"):
        store.session(name).preview_flight(7, partial(record_run, runs, name))
    assert runs == ["a", "b"]


def test_a_console_run_blocks_a_second_one_of_the_same_session() -> None:
    session = new_session()
    with session.console_run(), pytest.raises(ConsoleBusy), session.console_run():
        pass
    with session.console_run():
        pass


def test_a_failing_console_run_releases_the_session() -> None:
    session = new_session()
    with pytest.raises(RuntimeError), session.console_run():
        raise RuntimeError("boom")
    with session.console_run():
        pass


def test_the_live_gate_serializes_and_names_the_holder() -> None:
    gate = LiveGate(wait_seconds=0.1)
    with gate.hold("preview of game 7"):
        started = time.monotonic()
        with pytest.raises(GateTimeout, match="preview of game 7"), gate.hold("console"):
            pass
        assert time.monotonic() - started >= 0.09
    with gate.hold("console"):
        pass


def test_a_waiter_gets_the_gate_when_the_holder_finishes_in_time() -> None:
    gate = LiveGate(wait_seconds=5)
    order: list[str] = []
    holding = threading.Event()

    def first() -> None:
        with gate.hold("first"):
            holding.set()
            time.sleep(0.2)
            order.append("first")

    def second() -> None:
        holding.wait(5)
        with gate.hold("second"):
            order.append("second")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert order == ["first", "second"]
    assert sessions.LIVE_GATE_WAIT_SECONDS == 5.0


def test_the_store_returns_the_same_session_and_evicts_the_least_recently_used() -> None:
    store = SessionStore(max_sessions=2)
    first = store.session("a")
    assert store.session("a") is first
    store.session("b")
    store.session("a")
    store.session("c")
    assert store.session("a") is first
    assert store.session("b") is not None
    assert len(store) == 2
