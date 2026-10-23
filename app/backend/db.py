"""The backend's database sessions: a read pool, one reserved live session, a console session per
console action, and the effective-privilege start check.

- **Read pool:** `POOL_SIZE` autocommit sessions of the read user, each used by one thread at a time
  (pyexasol connections are not thread-safe), opened lazily, replaced after a failure. Every session
  runs `ALTER SESSION SET QUERY_TIMEOUT = 30`. A checkout that finds every session busy waits
  `CHECKOUT_WAIT_SECONDS`, then answers `PoolBusy` (503 `busy`). A 0-call statement is retried once
  after a transaction conflict; a statement of kind `LIVE` is refused here.
- **Live session:** one reserved session of the read user with `QUERY_TIMEOUT` 300 s and a socket
  timeout of 330 s. A statement runs once, on that session, and is never retried.
- **Console session:** one session of the console user per console action, with the live timeouts.
- **Connections** go through `vs/host/target.py` (`default_connector`); a database that cannot be
  reached is `DatabaseUnreachable` (503 `database_unreachable`) with `TargetError`'s message, which
  never holds a secret.

The effective-privilege start check (probe s9 (d)) lives here too: each user reads its own
privileges through `security.PRIVILEGE_QUERIES`; the impersonation grants, which only the
database-wide view shows, come from the operator path through a callable the caller passes.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pyexasol import ExaCommunicationError, ExaQueryError

from app.backend import security, spend
from app.backend.explain import refusal_message
from app.backend.sessions import Clock, QueryLog
from app.backend.settings import Settings
from vs.host import target
from vs.host.target import TargetError

POOL_SIZE = 4
CHECKOUT_WAIT_SECONDS = 5.0
POOL_SOCKET_TIMEOUT_SECONDS = 45
TRANSACTION_CONFLICT_CODE = "40001"
APP_PRINCIPAL = "app"
CONSOLE_PRINCIPAL = "reader"

Connector = Callable[[str | None, str | None, float | None], Any]
Rows = Sequence[Sequence[Any]]
ImpersonationReader = Callable[[], Rows]


class DatabaseUnreachable(RuntimeError):
    """The database cannot be reached or the session was lost (503 `database_unreachable`)."""


class PoolBusy(RuntimeError):
    """Every session of the pool, or the live session, is in use (503 `busy`)."""


class LiveStatementRefused(RuntimeError):
    """A statement that may call CFBD was sent to the read pool, which never retries and never
    charges."""


class ConsoleDisabled(RuntimeError):
    """The console path is off; the message says why (403)."""


class StatementKind(Enum):
    """`ZERO_CALL` statements may be retried after a conflict; `LIVE` ones never are."""

    ZERO_CALL = "zero_call"
    LIVE = "live"


@dataclass(frozen=True)
class SessionSpec:
    """Who a session connects as and its timeouts; the password never appears in a repr."""

    user: str
    password: str = field(repr=False)
    principal: str
    query_timeout: int
    socket_timeout: float


@dataclass(frozen=True)
class Result:
    """What one statement returned, and what it cost in time."""

    sql: str
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]
    ms: int
    user: str
    principal: str

    def dicts(self) -> list[dict[str, Any]]:
        """Each row as a dict keyed by column name."""
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


@dataclass(frozen=True)
class Availability:
    """Whether a path is on, and the reason to show."""

    enabled: bool
    reason: str


def default_connector(settings: Settings) -> Connector:
    """Connect through `vs/host/target.py`, resolving the target at every connect so the password
    and the address are read at connect time and never kept."""

    def connect(user: str | None, password: str | None, socket_timeout: float | None) -> Any:
        resolved = target.resolve(settings.target, dsn=settings.dsn_override)
        return resolved.connect(user, password, socket_timeout=socket_timeout)

    return connect


class DbSession:
    """One database session of one principal. Not thread-safe: one thread at a time."""

    def __init__(self, connection: Any, spec: SessionSpec, clock: Clock) -> None:
        self._connection = connection
        self._spec = spec
        self._clock = clock
        self.broken = False

    @classmethod
    def open(
        cls, connector: Connector, spec: SessionSpec, clock: Clock = time.monotonic
    ) -> DbSession:
        """Connect, then set the session's `QUERY_TIMEOUT`."""
        try:
            connection = connector(spec.user, spec.password, spec.socket_timeout)
        except TargetError as error:
            raise DatabaseUnreachable(str(error)) from None
        session = cls(connection, spec, clock)
        try:
            session._send(f"ALTER SESSION SET QUERY_TIMEOUT = {int(spec.query_timeout)}")
        except BaseException:
            session.close()
            raise
        return session

    @property
    def user(self) -> str:
        return self._spec.user

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        *,
        log: QueryLog | None = None,
        page: str | None = None,
        context: str | None = None,
    ) -> Result:
        """Run one statement; the text the driver sent, the rows and the time go to `log`."""
        started = self._clock()
        try:
            statement = self._send(sql, params)
            columns = tuple(statement.column_names())
            fetched = (
                [tuple(row) for row in statement.fetchall()]
                if getattr(statement, "result_type", "resultSet") == "resultSet"
                else []
            )
        except BaseException as error:
            self._record(log, _sent_text(error, sql, params), started, 0, page, context, error)
            raise
        result = Result(
            statement.query,
            columns,
            fetched,
            self._elapsed_ms(started),
            self._spec.user,
            self._spec.principal,
        )
        self._record(log, result.sql, started, len(fetched), page, context, None)
        return result

    def close(self) -> None:
        """Close the connection; closing twice, or a dead connection, is not an error."""
        with suppress(Exception):
            self._connection.close()
        self.broken = True

    def _send(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        try:
            return self._connection.execute(sql, params)
        except ExaQueryError:
            raise
        except BaseException as error:
            if isinstance(error, ExaCommunicationError | OSError) or self._connection.is_closed:
                self.broken = True
                raise DatabaseUnreachable(
                    f"the database connection failed ({type(error).__name__})"
                ) from None
            raise

    def _elapsed_ms(self, started: float) -> int:
        return round((self._clock() - started) * 1000)

    def _record(
        self,
        log: QueryLog | None,
        sql: str,
        started: float,
        rows: int,
        page: str | None,
        context: str | None,
        error: BaseException | None,
    ) -> None:
        if log is None:
            return
        log.record(
            sql=sql,
            principal=self._spec.principal,
            user=self._spec.user,
            ms=self._elapsed_ms(started),
            rows=rows,
            page=page,
            context=context,
            error=None if error is None else refusal_message(getattr(error, "message", error)),
        )


class ReadPool:
    """`size` sessions of the read user; a thread holds one for one statement."""

    def __init__(
        self,
        connector: Connector,
        spec: SessionSpec,
        *,
        size: int = POOL_SIZE,
        wait: float = CHECKOUT_WAIT_SECONDS,
        clock: Clock = time.monotonic,
    ) -> None:
        self._connector = connector
        self._spec = spec
        self._wait = wait
        self._clock = clock
        self._slots: queue.LifoQueue[DbSession | None] = queue.LifoQueue()
        for _ in range(size):
            self._slots.put(None)
        self._closed = False

    def run(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        *,
        kind: StatementKind = StatementKind.ZERO_CALL,
        log: QueryLog | None = None,
        page: str | None = None,
        context: str | None = None,
    ) -> Result:
        """Run a 0-call statement on a pooled session, retrying once after a transaction conflict.
        A statement of kind `LIVE` raises `LiveStatementRefused` without touching the database."""
        if kind is StatementKind.LIVE:
            raise LiveStatementRefused(
                "a live statement must run on the live session, once; the read pool never runs one"
            )
        with self._checkout() as session:
            try:
                return session.execute(sql, params, log=log, page=page, context=context)
            except ExaQueryError as error:
                if not is_transaction_conflict(error):
                    raise
                return session.execute(sql, params, log=log, page=page, context=context)

    def close(self) -> None:
        """Close every idle session; a session in use is closed when its checkout ends."""
        self._closed = True
        drained: list[DbSession | None] = []
        while True:
            try:
                drained.append(self._slots.get_nowait())
            except queue.Empty:
                break
        for session in drained:
            if session is not None:
                session.close()
            self._slots.put(None)

    @contextmanager
    def _checkout(self) -> Iterator[DbSession]:
        try:
            session = self._slots.get(timeout=self._wait)
        except queue.Empty:
            raise PoolBusy("every database session is busy; try again shortly") from None
        try:
            if session is None:
                session = DbSession.open(self._connector, self._spec, self._clock)
            yield session
        finally:
            if session is not None and (session.broken or self._closed):
                session.close()
                session = None
            self._slots.put(session)


class LiveSession:
    """The one session reserved for live statements: opened on first use, replaced after a failure,
    and used for one statement at a time. A statement is sent once and never retried."""

    def __init__(
        self,
        connector: Connector,
        spec: SessionSpec,
        *,
        wait: float = CHECKOUT_WAIT_SECONDS,
        clock: Clock = time.monotonic,
    ) -> None:
        self._connector = connector
        self._spec = spec
        self._wait = wait
        self._clock = clock
        self._lock = threading.Lock()
        self._session: DbSession | None = None

    def run(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        *,
        log: QueryLog | None = None,
        page: str | None = None,
        context: str | None = None,
    ) -> Result:
        """Run one statement exactly once; `PoolBusy` when the session is still in use."""
        if not self._lock.acquire(timeout=self._wait):
            raise PoolBusy("the live database session is busy; try again shortly")
        try:
            if self._session is None:
                self._session = DbSession.open(self._connector, self._spec, self._clock)
            try:
                return self._session.execute(sql, params, log=log, page=page, context=context)
            finally:
                if self._session.broken:
                    self._session.close()
                    self._session = None
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None


class ConsoleSessions:
    """A session of the console user per console action, closed when the action ends."""

    def __init__(
        self,
        connector: Connector,
        spec: SessionSpec | None,
        availability: Availability,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._connector = connector
        self._spec = spec
        self._clock = clock
        self.availability = availability

    @contextmanager
    def session(self) -> Iterator[DbSession]:
        """Open the console user's session for one action; `ConsoleDisabled` when it is off."""
        if not self.availability.enabled or self._spec is None:
            raise ConsoleDisabled(self.availability.reason)
        opened = DbSession.open(self._connector, self._spec, self._clock)
        try:
            yield opened
        finally:
            opened.close()


@dataclass
class Database:
    """The three paths of the backend to the database."""

    pool: ReadPool
    live: LiveSession
    console: ConsoleSessions

    def close(self) -> None:
        self.pool.close()
        self.live.close()


def console_availability(settings: Settings) -> Availability:
    """The console needs the console user's password and the operator path."""
    if not settings.console_password:
        return Availability(False, "CFB_CONSOLE_PASSWORD is not set")
    if not settings.operator.enabled:
        return Availability(
            False, f"the console needs the operator path, which is off ({settings.operator.reason})"
        )
    return Availability(True, f"console user {settings.console_user}")


def build_database(
    settings: Settings, connector: Connector | None = None, clock: Clock = time.monotonic
) -> Database:
    """The pool, the live session and the console sessions for these settings."""
    connect = connector or default_connector(settings)
    pool_spec = SessionSpec(
        settings.app_user,
        settings.app_password,
        APP_PRINCIPAL,
        spend.POOL_QUERY_TIMEOUT_SECONDS,
        POOL_SOCKET_TIMEOUT_SECONDS,
    )
    live_spec = SessionSpec(
        settings.app_user,
        settings.app_password,
        APP_PRINCIPAL,
        spend.LIVE_QUERY_TIMEOUT_SECONDS,
        spend.LIVE_SOCKET_TIMEOUT_SECONDS,
    )
    console_spec = (
        SessionSpec(
            settings.console_user,
            settings.console_password,
            CONSOLE_PRINCIPAL,
            spend.LIVE_QUERY_TIMEOUT_SECONDS,
            spend.LIVE_SOCKET_TIMEOUT_SECONDS,
        )
        if settings.console_password
        else None
    )
    return Database(
        ReadPool(connect, pool_spec, clock=clock),
        LiveSession(connect, live_spec, clock=clock),
        ConsoleSessions(connect, console_spec, console_availability(settings), clock=clock),
    )


def is_transaction_conflict(error: BaseException) -> bool:
    """True for Exasol's transaction collision (SQLSTATE 40001, "Transaction collision")."""
    code = str(getattr(error, "code", ""))
    return code == TRANSACTION_CONFLICT_CODE or "transaction collision" in str(error).lower()


def _sent_text(error: BaseException, sql: str, params: Mapping[str, Any] | None) -> str:
    sent = getattr(error, "query", None)
    if isinstance(sent, str) and sent:
        return sent
    if not params:
        return sql
    from app.backend import sqlcatalog

    try:
        return sqlcatalog.new_formatter().format(sql, **params)
    except Exception:
        return sql


# ---- the effective-privilege start check ---------------------------------------------------

UNREADABLE_PRIVILEGES = "cannot read its own privileges"
SKIP_HINT = "set APP_SKIP_READ_CHECK=yes to start without that check"


@dataclass(frozen=True)
class ExpectedUser:
    """A user the start check proves: its role (if any) and the schemas it may read."""

    spec: SessionSpec
    roles: tuple[str, ...]
    schemas: tuple[str, ...]


@dataclass(frozen=True)
class StartCheck:
    """What the start check established; `warnings` are for the header when `skipped`."""

    users: tuple[str, ...]
    skipped: bool
    warnings: tuple[str, ...]


def expected_users(settings: Settings) -> list[ExpectedUser]:
    """The read user, and the console user when the console is on. The read user holds the role
    `<prefix>CFB_APP` (`SELECT` on the two serving schemas). The console user holds `CFB_READER` on
    the real deployment and `SELECT` on the virtual schema directly in a test namespace."""
    namespace = settings.namespace
    users = [
        ExpectedUser(
            SessionSpec(
                settings.app_user,
                settings.app_password,
                APP_PRINCIPAL,
                spend.POOL_QUERY_TIMEOUT_SECONDS,
                POOL_SOCKET_TIMEOUT_SECONDS,
            ),
            (f"{namespace.prefix}CFB_APP",),
            (settings.api_schema, settings.api_live_schema),
        )
    ]
    if console_availability(settings).enabled and settings.console_password:
        users.append(
            ExpectedUser(
                SessionSpec(
                    settings.console_user,
                    settings.console_password,
                    CONSOLE_PRINCIPAL,
                    spend.POOL_QUERY_TIMEOUT_SECONDS,
                    POOL_SOCKET_TIMEOUT_SECONDS,
                ),
                () if namespace.prefix else (namespace.reader_role,),
                (namespace.vs_name,),
            )
        )
    return users


def run_start_check(
    settings: Settings,
    connector: Connector,
    impersonation: ImpersonationReader | None,
    clock: Clock = time.monotonic,
) -> StartCheck:
    """Prove that the read and console users hold nothing beyond their roles, by every route.

    Raises `security.PrivilegeError` for a violation (never waived), and for what cannot be verified
    (a user that cannot read its own privileges; impersonation without the operator path) unless
    `APP_SKIP_READ_CHECK=yes`, which turns it into a warning. `DatabaseUnreachable` when a user
    cannot connect."""
    grants, grants_note = _impersonation_grants(impersonation)
    warnings: list[str] = []
    expected = expected_users(settings)
    for user in expected:
        warnings += _check_user(settings, connector, clock, user, grants, grants_note)
    return StartCheck(tuple(user.spec.user for user in expected), bool(warnings), tuple(warnings))


def _impersonation_grants(reader: ImpersonationReader | None) -> tuple[Rows | None, str | None]:
    if reader is None:
        return None, None
    try:
        return reader(), None
    except Exception as error:
        return None, type(error).__name__


def _check_user(
    settings: Settings,
    connector: Connector,
    clock: Clock,
    user: ExpectedUser,
    grants: Rows | None,
    grants_note: str | None,
) -> list[str]:
    name = user.spec.user
    rows = _own_privileges(connector, user.spec, clock)
    if rows is None:
        if not settings.skip_read_check:
            raise security.PrivilegeError(f"{name} {UNREADABLE_PRIVILEGES}; {SKIP_HINT}")
        return [f"{name}: privileges were not checked ({UNREADABLE_PRIVILEGES})"]
    snapshot = security.PrivilegeSnapshot.from_rows(rows, grants)
    report = security.check_effective_privileges(
        snapshot,
        user=name,
        roles=user.roles,
        schemas=user.schemas,
        forbidden_users=[settings.namespace.owner_user],
    )
    report.raise_if_violations(name)
    if not report.unverified:
        return []
    detail = f" (reading it failed: {grants_note})" if grants_note else ""
    if not settings.skip_read_check:
        raise security.PrivilegeError(
            f"{name} cannot be fully verified: {'; '.join(report.unverified)}{detail}; "
            f"it needs the operator path. {SKIP_HINT}"
        )
    return [f"{name}: {'; '.join(report.unverified)} was not checked{detail}"]


def _own_privileges(
    connector: Connector, spec: SessionSpec, clock: Clock
) -> dict[str, list[tuple[Any, ...]]] | None:
    session = DbSession.open(connector, spec, clock)
    try:
        return {key: session.execute(sql).rows for key, sql in security.PRIVILEGE_QUERIES.items()}
    except ExaQueryError:
        return None
    finally:
        session.close()
