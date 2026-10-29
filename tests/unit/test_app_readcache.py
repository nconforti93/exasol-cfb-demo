"""The stored-read cache: hits are logged as cached, clears and the TTL invalidate, live reads
bypass it."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

from app.backend.db import Result
from app.backend.readcache import Memo, ReadCache, cache_key
from app.backend.routes.read import ReadService
from app.backend.sessions import QueryLog


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Pool:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(
        self, sql: str, params: Any = None, *, log: QueryLog, page: str, context: str
    ) -> Result:
        self.calls.append(page)
        log.record(
            sql=sql, principal="app", user="CFB_APP_WEB", ms=420, rows=1, page=page, context=context
        )
        return Result(sql, ("TEAM_ID",), [(1,)], 420, "CFB_APP_WEB", "app")


def service(
    pool: Pool, cache: ReadCache | None, log: QueryLog, *, cached: bool = True
) -> ReadService:
    state = SimpleNamespace(database=SimpleNamespace(pool=pool), read_cache=cache)
    request: Any = SimpleNamespace(
        app=SimpleNamespace(state=state), state=SimpleNamespace(session_id="s")
    )
    store: Any = SimpleNamespace(session=lambda _: SimpleNamespace(log=log))
    settings: Any = SimpleNamespace(api_schema="CFB_API", api_live_schema="CFB_API_LIVE")
    return ReadService(request, settings, store, cached=cached)


def test_second_read_is_served_from_cache_and_logged_as_cached() -> None:
    pool, cache, log = Pool(), ReadCache(), QueryLog()
    first = service(pool, cache, log).fetch("teams")
    first[0]["TEAM_ID"] = 99  # a caller mutating its rows must not poison the cache
    other = service(pool, cache, log)
    assert other.fetch("teams") == [{"TEAM_ID": 1}]
    assert pool.calls == ["teams"]
    newest = log.entries()[0]
    assert newest.cached and newest.ms == 420 and other.ids["teams"] == str(newest.id)


def test_clear_ttl_and_uncached_callers_go_to_the_database() -> None:
    clock = Clock()
    pool, cache, log = Pool(), ReadCache(ttl=10, clock=clock), QueryLog()
    service(pool, cache, log).fetch("teams")
    cache.clear()
    service(pool, cache, log).fetch("teams")
    clock.now = 10
    service(pool, cache, log).fetch("teams")
    service(pool, cache, log, cached=False).fetch("teams")
    service(pool, cache, log).fetch("timeline_latest_run")
    service(pool, cache, log).fetch("timeline_latest_run")
    assert pool.calls == ["teams"] * 4 + ["timeline_latest_run"] * 2


def test_a_read_that_started_before_a_clear_is_not_stored() -> None:
    cache = ReadCache()
    key = cache_key("teams", {"api_schema": "CFB_API"})
    generation = cache.generation
    cache.clear()
    cache.put(key, generation=generation, sql="s", rows=[{"A": 1}], principal="app", user="u", ms=1)
    assert cache.get(key) is None and len(cache) == 0


def test_memo_reuses_a_value_until_it_expires_and_never_stores_an_error() -> None:
    clock, computed = Clock(), []
    memo = Memo(clock=clock)

    def compute() -> int:
        computed.append(1)
        return len(computed)

    assert memo.get("k", 60, compute) == 1 and memo.get("k", 60, compute) == 1
    clock.now = 60
    assert memo.get("k", 60, compute) == 2

    def failing() -> int:
        raise RuntimeError("down")

    memo.clear()
    with contextlib.suppress(RuntimeError):
        memo.get("k", 60, failing)
    assert memo.get("k", 60, compute) == 3
