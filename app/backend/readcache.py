"""Process-wide cache of stored-data read results.

The stored model changes only when a loader run (refresh, backfill, restore, deploy) finishes, so
the read routes reuse a statement's rows until then. `clear()` is called when the timeline poll sees
the latest run change, and every entry also expires after `ttl` seconds, so a run the poll missed is
picked up within that bound. Rows are copied on the way in and out: callers may mutate them."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from app.backend.sessions import Clock

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_MAX_ENTRIES = 512
DISPLAY_TTL_SECONDS = 60.0

Row = dict[str, Any]


@dataclass(frozen=True)
class CachedRead:
    """What a database read produced: the text the driver sent, the rows, who ran it, how long."""

    sql: str
    rows: tuple[Row, ...]
    principal: str
    user: str
    ms: int
    stored_at: float


def cache_key(name: str, values: Mapping[str, Any]) -> tuple[Hashable, ...]:
    """A statement name plus its parameter values (schema names included), order-independent."""
    return (name, *sorted(values.items()))


class ReadCache:
    """A bounded LRU of read results with a time-to-live and a generation that `clear()` bumps."""

    def __init__(
        self,
        ttl: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Clock = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[Hashable, ...], CachedRead] = OrderedDict()
        self._generation = 0
        self._lock = threading.Lock()

    @property
    def generation(self) -> int:
        """Read before a database query and pass to `put`, so a result that started before a
        `clear()` is never stored after it."""
        with self._lock:
            return self._generation

    def get(self, key: tuple[Hashable, ...]) -> CachedRead | None:
        with self._lock:
            found = self._entries.get(key)
            if found is None:
                return None
            if self._clock() - found.stored_at >= self._ttl:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
        return CachedRead(
            found.sql,
            tuple(dict(row) for row in found.rows),
            found.principal,
            found.user,
            found.ms,
            found.stored_at,
        )

    def put(
        self,
        key: tuple[Hashable, ...],
        *,
        generation: int,
        sql: str,
        rows: list[Row],
        principal: str,
        user: str,
        ms: int,
    ) -> None:
        stored = CachedRead(
            sql, tuple(dict(row) for row in rows), principal, user, ms, self._clock()
        )
        with self._lock:
            if generation != self._generation:
                return
            self._entries[key] = stored
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generation += 1

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class Memo:
    """Short-lived values for display-only routes (`/api/config`, `/api/quota`) that would otherwise
    open an owner connection per request. Never use it for a value a spend decision depends on."""

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._values: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get[T](self, key: Hashable, ttl: float, compute: Callable[[], T]) -> T:
        """The value stored under `key` if younger than `ttl`, otherwise `compute()` (stored only
        when it returns without raising)."""
        with self._lock:
            found = self._values.get(key)
            if found is not None and self._clock() - found[0] < ttl:
                return cast(T, found[1])
        value = compute()
        with self._lock:
            self._values[key] = (self._clock(), value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
