"""The spend ledger of the chat's model calls (`chat/budget`): reservations under one process lock,
and the spend file that the server and the rehearsal runs share.

- **Reserve.** Before a call, under the process lock, `reserve` reads today's lines of the spend
  file again and grants the call only when the day's spend plus every open reservation plus the
  call's worst case stays within the daily cap, each scope's spend plus its open reservations plus
  the worst case stays within that scope's cap (a conversation, a rehearsal run), and the input
  estimate stays within the context cap. The daily cap is checked first, because a new
  conversation does not help against it.
- **Settle.** After the call, `settle` prices it from its usage, or at its worst case when no
  usable usage came (a closed stream, an abandoned call), appends one line under the file lock
  and flushes it to disk, and only then frees the reservation. A reservation running at the same
  time may count the call twice, but never misses it.
- **Failing closed.** A line that cannot be written keeps its cost in memory and turns the ledger
  `spend_log_unwritable` for the life of the process, so no further live call is made. So does a
  spend file that cannot be read or parsed (spend that cannot be proven is not available) and a
  file lock that is not granted in time. Stub mode makes no reservations and is not affected.
- Use one ledger per process and spend file: reservations are shared only within one ledger. A
  call that is still open when the process dies leaves no line.

The line format, one JSON object per line in UTF-8, fields in this order: `ts` (UTC,
`YYYY-MM-DDTHH:MM:SS.mmmZ`), `process` (`<label>/<pid>`), `conversation`, `model` (the model that
served the call, else the one requested), `usage` (`budget.usage_record`, null when none came),
`cost_usd`, `estimated` (true when the cost is the worst case). Readers need only `ts` and
`cost_usd` and ignore other fields.
"""

from __future__ import annotations

import fcntl
import itertools
import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from app.backend.chat import budget
from app.backend.chat.budget import CapRefusal, CapScope

LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_POLL_SECONDS = 0.005
_USD_STEP = Decimal("0.00000001")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")
_RESERVATION_IDS = itertools.count(1)


@dataclass(frozen=True, slots=True)
class CallPlan:
    """A call about to be made: the model requested, its input estimate and its worst case."""

    model: str
    input_tokens: int
    worst_usd: Decimal

    @classmethod
    def for_request(
        cls, model: str, input_tokens: int, *, max_tokens: int, fallbacks: bool
    ) -> CallPlan:
        """The plan of a request: its input estimate and the worst case of `budget`."""
        worst = budget.worst_case(input_tokens, max_tokens, model, fallbacks=fallbacks)
        return cls(model, input_tokens, worst)


@dataclass(frozen=True, slots=True)
class Limits:
    """The caps that are not scopes: the day's dollars and the context per call (None: no cap)."""

    daily_usd: Decimal | None
    context_tokens: int | None


@dataclass(frozen=True, slots=True, eq=False)
class Reservation:
    """A granted call: settle it once, or release it when no request was sent."""

    id: int
    plan: CallPlan
    scopes: tuple[str, ...]
    conversation: str


@dataclass(frozen=True, slots=True)
class Settlement:
    """A settled call: its cost, whether that is the worst case, the models priced at the highest
    row, whether it cost more than its worst case, and whether its line was written."""

    cost_usd: Decimal
    estimated: bool
    unknown_models: tuple[str, ...]
    exceeded_worst_case: bool
    written: bool


class SpendFileError(Exception):
    """The spend file or its lock cannot be used; the message names the file and the line."""


class SpendLedger:
    """Reservations and the spend file of one process; every method is thread-safe."""

    def __init__(
        self,
        path: Path,
        *,
        process: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lock_timeout: float = LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.path = path
        self._process = f"{process}/{os.getpid()}"
        self._clock = clock
        self._lock_timeout = lock_timeout
        self._lock = threading.Lock()
        self._open: dict[int, Reservation] = {}
        self._spent: dict[str, Decimal] = {}
        self._unwritten: list[tuple[str, Decimal]] = []
        self._unwritable: str | None = None

    @property
    def unwritable_reason(self) -> str | None:
        """Why live calls are off for this process (`spend_log_unwritable`), or None."""
        return self._unwritable

    def check_usable(self) -> bool:
        """At start: the directory, the lock and the file can be created, written and read.
        False (and the ledger turned unwritable) when they cannot."""
        with self._lock:
            if self._unwritable is None:
                try:
                    with self._file_lock(fcntl.LOCK_EX):
                        os.close(self._open_for_append())
                    self._file_spent_on(self._today())
                except (OSError, SpendFileError) as error:
                    self._fail(f"cannot use {self.path}: {error}")
            return self._unwritable is None

    def check(
        self, plan: CallPlan, scopes: Sequence[CapScope], limits: Limits
    ) -> CapRefusal | None:
        """What `reserve` would decide, without reserving."""
        with self._lock:
            return self._refusal(plan, scopes, limits)

    def reserve(
        self,
        plan: CallPlan,
        scopes: Sequence[CapScope],
        limits: Limits,
        *,
        conversation: str = "",
    ) -> Reservation | CapRefusal:
        """Grant the call and hold its worst case against the caps, or refuse it naming the cap."""
        with self._lock:
            refusal = self._refusal(plan, scopes, limits)
            if refusal is not None:
                return refusal
            keys = tuple(scope.key for scope in scopes)
            reservation = Reservation(next(_RESERVATION_IDS), plan, keys, conversation)
            self._open[reservation.id] = reservation
            return reservation

    def settle(
        self,
        reservation: Reservation,
        usage: Mapping[str, object] | None,
        served_model: str | None = None,
    ) -> Settlement:
        """Price the call (at its worst case without usable usage), write its line and free its
        reservation. A failed write keeps the cost in memory and turns the ledger unwritable."""
        with self._lock:
            self._check_open(reservation)
            plan = reservation.plan
            model = served_model or plan.model
            cost, estimated, unknown, record = _price(plan, usage, model)
            moment = self._now()
            line = json.dumps(
                {
                    "ts": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "process": self._process,
                    "conversation": reservation.conversation,
                    "model": model,
                    "usage": record,
                    "cost_usd": float(cost),
                    "estimated": estimated,
                },
                ensure_ascii=False,
            )
            written = self._append(line)
            del self._open[reservation.id]
            for key in reservation.scopes:
                self._spent[key] = self._spent_of(key) + cost
            if not written:
                self._unwritten.append((moment.date().isoformat(), cost))
            return Settlement(cost, estimated, unknown, cost > plan.worst_usd, written)

    def release(self, reservation: Reservation) -> None:
        """Free a reservation whose request was never sent; it writes no line."""
        with self._lock:
            self._check_open(reservation)
            del self._open[reservation.id]

    def spent(self, scope_key: str) -> Decimal:
        """Dollars settled in a scope by this ledger."""
        with self._lock:
            return self._spent_of(scope_key)

    def reserved(self, scope_key: str) -> Decimal:
        """Dollars held by the open reservations of a scope."""
        with self._lock:
            return self._reserved(scope_key)

    def reserved_total(self) -> Decimal:
        """Dollars held by every open reservation of this ledger."""
        with self._lock:
            return sum((r.plan.worst_usd for r in self._open.values()), Decimal(0))

    def today_spent(self) -> Decimal:
        """Today's (UTC) spend: the spend file's lines plus costs this process could not write.
        A file that cannot be read turns the ledger unwritable and counts as nothing here."""
        with self._lock:
            day = self._today()
            try:
                from_file = self._file_spent_on(day)
            except SpendFileError as error:
                self._fail(str(error))
                from_file = Decimal(0)
            return from_file + self._unwritten_on(day)

    def _refusal(
        self, plan: CallPlan, scopes: Sequence[CapScope], limits: Limits
    ) -> CapRefusal | None:
        day = self._today()
        day_spent = Decimal(0)
        if self._unwritable is None:
            try:
                day_spent = self._file_spent_on(day) + self._unwritten_on(day)
            except SpendFileError as error:
                self._fail(str(error))
        if self._unwritable is not None:
            return CapRefusal(
                budget.SPEND_LOG_UNWRITABLE,
                f"live calls are off because the spend file is not usable: {self._unwritable}",
            )
        checks = [
            (scope, self._spent_of(scope.key) + self._reserved(scope.key)) for scope in scopes
        ]
        if limits.daily_usd is not None:
            open_total = sum((r.plan.worst_usd for r in self._open.values()), Decimal(0))
            checks.insert(0, (budget.daily_scope(limits.daily_usd), day_spent + open_total))
        for scope, committed in checks:
            refusal = budget.money_check(scope, committed=committed, worst=plan.worst_usd)
            if refusal is not None:
                return refusal
        if limits.context_tokens is not None:
            return budget.context_check(plan.input_tokens, limits.context_tokens)
        return None

    def _check_open(self, reservation: Reservation) -> None:
        if self._open.get(reservation.id) is not reservation:
            raise ValueError(f"reservation {reservation.id} is not open in this ledger")

    def _spent_of(self, scope_key: str) -> Decimal:
        return self._spent.get(scope_key, Decimal(0))

    def _reserved(self, scope_key: str) -> Decimal:
        held = (r.plan.worst_usd for r in self._open.values() if scope_key in r.scopes)
        return sum(held, Decimal(0))

    def _unwritten_on(self, day: str) -> Decimal:
        return sum((cost for when, cost in self._unwritten if when == day), Decimal(0))

    def _now(self) -> datetime:
        moment = self._clock()
        if moment.tzinfo is None:
            raise ValueError("the spend ledger's clock must return a timezone-aware time")
        return moment.astimezone(UTC)

    def _today(self) -> str:
        return self._now().date().isoformat()

    def _fail(self, reason: str) -> None:
        if self._unwritable is None:
            self._unwritable = reason

    def _append(self, line: str) -> bool:
        data = (line + "\n").encode("utf-8")
        try:
            with self._file_lock(fcntl.LOCK_EX):
                handle = self._open_for_append()
                try:
                    count = os.write(handle, data)
                    if count != len(data):
                        raise OSError(f"short write ({count} of {len(data)} bytes)")
                    os.fsync(handle)
                finally:
                    os.close(handle)
        except (OSError, SpendFileError) as error:
            self._fail(f"cannot write {self.path}: {error}")
            return False
        return True

    def _open_for_append(self) -> int:
        return os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)

    def _file_spent_on(self, day: str) -> Decimal:
        with self._file_lock(fcntl.LOCK_SH):
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                return Decimal(0)
            except OSError as error:
                raise SpendFileError(f"cannot read {self.path}: {error}") from None
        return _sum_of_day(raw, day, self.path)

    @contextmanager
    def _file_lock(self, mode: int) -> Iterator[None]:
        lock_path = self.path.with_name(self.path.name + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as error:
            raise SpendFileError(f"cannot open the lock file {lock_path}: {error}") from None
        try:
            deadline = time.monotonic() + self._lock_timeout
            while True:
                try:
                    fcntl.flock(handle, mode | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise SpendFileError(
                            f"the lock {lock_path} was not granted within {self._lock_timeout} s"
                        ) from None
                    time.sleep(_LOCK_POLL_SECONDS)
            yield
        finally:
            os.close(handle)


def _price(
    plan: CallPlan, usage: Mapping[str, object] | None, model: str
) -> tuple[Decimal, bool, tuple[str, ...], dict[str, object] | None]:
    worst = plan.worst_usd.quantize(_USD_STEP, rounding=ROUND_CEILING)
    if usage is None:
        return worst, True, (), None
    try:
        cost = budget.call_cost(usage, model)
        record = budget.usage_record(usage)
    except ValueError:
        return worst, True, (), None
    return cost.usd.quantize(_USD_STEP, rounding=ROUND_CEILING), False, cost.unknown_models, record


def _sum_of_day(raw: bytes, day: str, path: Path) -> Decimal:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SpendFileError(f"{path}: not UTF-8 at byte {error.start}") from None
    lines = text.split("\n")
    if lines.pop():
        raise SpendFileError(f"{path}:{len(lines) + 1}: the last line is incomplete")
    total = Decimal(0)
    for number, line in enumerate(lines, start=1):
        if line.strip():
            when, cost = _parse_line(line, f"{path}:{number}")
            if when == day:
                total += cost
    return total


def _parse_line(line: str, where: str) -> tuple[str, Decimal]:
    try:
        data = json.loads(line, parse_float=Decimal, parse_constant=_no_constant)
    except ValueError:
        raise SpendFileError(f"{where}: not valid JSON") from None
    if not isinstance(data, dict):
        raise SpendFileError(f"{where}: not a JSON object")
    stamp, cost = data.get("ts"), data.get("cost_usd")
    if not isinstance(stamp, str) or not _TIMESTAMP.match(stamp):
        raise SpendFileError(f"{where}: field 'ts' is not a UTC timestamp")
    if isinstance(cost, bool) or not isinstance(cost, int | Decimal) or cost < 0:
        raise SpendFileError(f"{where}: field 'cost_usd' is not a non-negative number")
    return stamp[:10], Decimal(cost)


def _no_constant(name: str) -> object:
    raise ValueError(f"{name} is not a number")
