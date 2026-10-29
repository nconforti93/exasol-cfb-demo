"""Per-browser state: the preview cache, the single flight per (session, game), one console run at a
time, the query log, and the in-process gate for live requests (pure and thread-safe; the clocks are
injected).

Nothing here is shared between browsers: the viewer who spent owns the result. The session id is an
anonymous cookie value (`security.py`); it grants nothing.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from vs.host.livecalls import SliceCall

PREVIEW_TTL_SECONDS = 15 * 60
PREVIEW_CACHE_ENTRIES = 20
QUERY_LOG_ENTRIES = 50
MAX_SESSIONS = 500
LIVE_GATE_WAIT_SECONDS = 5.0
FLIGHT_WAIT_SECONDS = 400.0
PRINCIPALS = frozenset({"app", "reader", "chat"})

Clock = Callable[[], float]


class ConsoleBusy(RuntimeError):
    """The session already has a console run in flight."""


class GateTimeout(RuntimeError):
    """The in-process live gate did not free up in time; the message names its holder."""


class FlightTimeout(RuntimeError):
    """A waiter gave up on the fetch it was sharing."""


@dataclass(frozen=True)
class QueryLogEntry:
    """One statement the backend ran for a session, with the text exactly as the driver sent it."""

    id: int
    sql: str
    principal: str
    user: str
    page: str | None
    started_at: float
    ms: int
    rows: int
    error: str | None
    slices: tuple[SliceCall, ...]
    calls: int
    context: str | None
    cached: bool = False


class QueryLog:
    """The last `QUERY_LOG_ENTRIES` statements of one session."""

    def __init__(self, wall: Clock = time.time, max_entries: int = QUERY_LOG_ENTRIES) -> None:
        self._wall = wall
        self._max = max_entries
        self._entries: OrderedDict[int, QueryLogEntry] = OrderedDict()
        self._next = 1
        self._lock = threading.Lock()

    def record(
        self,
        *,
        sql: str,
        principal: str,
        user: str,
        ms: int,
        rows: int,
        page: str | None = None,
        error: str | None = None,
        slices: Sequence[SliceCall] = (),
        calls: int = 0,
        context: str | None = None,
        started_at: float | None = None,
        cached: bool = False,
    ) -> QueryLogEntry:
        """Append an entry and return it with its id."""
        if principal not in PRINCIPALS:
            raise ValueError(f"principal must be one of {', '.join(sorted(PRINCIPALS))}")
        with self._lock:
            entry = QueryLogEntry(
                self._next,
                sql,
                principal,
                user,
                page,
                self._wall() if started_at is None else started_at,
                ms,
                rows,
                error,
                tuple(slices),
                calls,
                context,
                cached,
            )
            self._entries[entry.id] = entry
            self._next += 1
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
            return entry

    def entries(self) -> list[QueryLogEntry]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._entries.values()))

    def get(self, entry_id: int) -> QueryLogEntry | None:
        with self._lock:
            return self._entries.get(entry_id)


@dataclass(frozen=True)
class CachedPreview[T]:
    value: T
    cached_at: float
    expires: float


@dataclass(frozen=True)
class FlightResult[T]:
    """What a preview flight answered: the value, whether it came from the cache or from a flight
    this call joined (0 calls), and when it was fetched."""

    value: T
    cached: bool
    cached_at: float | None = None


class PreviewCache:
    """At most `PREVIEW_CACHE_ENTRIES` previews of one session, each for `PREVIEW_TTL_SECONDS`."""

    def __init__(self, clock: Clock, wall: Clock) -> None:
        self._clock = clock
        self._wall = wall
        self._items: OrderedDict[int, CachedPreview[Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, game_id: int) -> CachedPreview[Any] | None:
        with self._lock:
            item = self._items.get(game_id)
            if item is None:
                return None
            if self._clock() >= item.expires:
                del self._items[game_id]
                return None
            self._items.move_to_end(game_id)
            return item

    def put(self, game_id: int, value: Any) -> CachedPreview[Any]:
        item = CachedPreview(value, self._wall(), self._clock() + PREVIEW_TTL_SECONDS)
        with self._lock:
            self._items[game_id] = item
            self._items.move_to_end(game_id)
            while len(self._items) > PREVIEW_CACHE_ENTRIES:
                self._items.popitem(last=False)
        return item

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class _Flight:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.cached_at: float | None = None
        self.error: BaseException | None = None


class Session:
    """The state of one browser."""

    def __init__(
        self,
        session_id: str,
        *,
        clock: Clock = time.monotonic,
        wall: Clock = time.time,
        flight_wait: float = FLIGHT_WAIT_SECONDS,
    ) -> None:
        self.id = session_id
        self.log = QueryLog(wall)
        self.previews = PreviewCache(clock, wall)
        self._flights: dict[int, _Flight] = {}
        self._flight_lock = threading.Lock()
        self._flight_wait = flight_wait
        self._console_running = False
        self._console_lock = threading.Lock()

    def preview_flight[T](
        self, game_id: int, fetch: Callable[[], T], *, force: bool = False
    ) -> FlightResult[T]:
        """One fetch per (session, game). The first caller fetches; callers that arrive meanwhile
        wait and share its outcome, a failure included, so a double click never spends twice. The
        cache is checked inside the flight, so a caller that arrives after one finished is served
        from it. `force` skips the cache but still joins a flight in progress."""
        with self._flight_lock:
            flight = self._flights.get(game_id)
            if flight is None:
                cached = None if force else self.previews.get(game_id)
                if cached is not None:
                    return FlightResult(cached.value, True, cached.cached_at)
                flight = self._flights[game_id] = _Flight()
                leader = True
            else:
                leader = False
        if leader:
            return self._lead(game_id, flight, fetch)
        if not flight.done.wait(self._flight_wait):
            raise FlightTimeout("the preview this request was waiting for did not finish")
        if flight.error is not None:
            raise flight.error
        return FlightResult(flight.value, True, flight.cached_at)

    def _lead[T](self, game_id: int, flight: _Flight, fetch: Callable[[], T]) -> FlightResult[T]:
        try:
            value = fetch()
            stored = self.previews.put(game_id, value)
            flight.value, flight.cached_at = value, stored.cached_at
            return FlightResult(value, False, stored.cached_at)
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._flight_lock:
                del self._flights[game_id]
            flight.done.set()

    @contextmanager
    def console_run(self) -> Iterator[None]:
        """One console run per session; a second one raises `ConsoleBusy`."""
        with self._console_lock:
            if self._console_running:
                raise ConsoleBusy("a console run of this session is still running")
            self._console_running = True
        try:
            yield
        finally:
            with self._console_lock:
                self._console_running = False


class SessionStore:
    """All sessions of the process, least recently used first out."""

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        clock: Clock = time.monotonic,
        wall: Clock = time.time,
    ) -> None:
        self._max = max_sessions
        self._clock = clock
        self._wall = wall
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def session(self, session_id: str) -> Session:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                self._sessions.move_to_end(session_id)
                return existing
            created = Session(session_id, clock=self._clock, wall=self._wall)
            self._sessions[session_id] = created
            while len(self._sessions) > self._max:
                self._sessions.popitem(last=False)
            return created

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


class LiveGate:
    """The in-process gate every live request passes: the app's own live requests queue for at most
    `wait_seconds` instead of failing each other on the ledger lock, which does not wait."""

    def __init__(self, wait_seconds: float = LIVE_GATE_WAIT_SECONDS) -> None:
        self._wait = wait_seconds
        self._lock = threading.Lock()
        self._holder: str | None = None

    @property
    def holder(self) -> str | None:
        return self._holder

    @contextmanager
    def hold(self, label: str) -> Iterator[None]:
        """Hold the gate for `label`; raises `GateTimeout` naming the holder after the wait."""
        if not self._lock.acquire(timeout=self._wait):
            raise GateTimeout(
                f"another live request is running ({self._holder or 'unknown'}); try again shortly"
            )
        self._holder = label
        try:
            yield
        finally:
            self._holder = None
            self._lock.release()
