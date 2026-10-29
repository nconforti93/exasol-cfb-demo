"""Fake pyexasol connections and connectors for the backend's database tests (no network)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from pyexasol import ExaCommunicationError, ExaQueryError

from app.backend import sqlcatalog
from vs.host.target import TargetError

Responder = Callable[[str], "Reply | BaseException | None"]


class Reply:
    """What a fake statement returns."""

    def __init__(self, columns: Sequence[str] = ("X",), rows: Sequence[tuple[Any, ...]] = ((1,),)):
        self.columns = tuple(columns)
        self.rows = [tuple(row) for row in rows]


class FakeStatement:
    def __init__(self, query: str, reply: Reply) -> None:
        self.query = query
        self._reply = reply

    def column_names(self) -> list[str]:
        return list(self._reply.columns)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._reply.rows)


def pyexasol_connection_stub() -> Any:
    return SimpleNamespace(options={"verbose_error": False}, session_id=lambda: "1")


def query_error(code: str, message: str, query: str = "SELECT 1") -> ExaQueryError:
    return ExaQueryError(pyexasol_connection_stub(), query, code, message)  # type: ignore[no-untyped-call]


def collision() -> ExaQueryError:
    return query_error("40001", "GlobalTransactionRollback: Transaction collision")


def lost_connection() -> ExaCommunicationError:
    return ExaCommunicationError(pyexasol_connection_stub(), "websocket closed by 127.0.0.1:8563")  # type: ignore[no-untyped-call]


class FakeConnection:
    """A connection that records its statements and notices use from two threads at once."""

    def __init__(self, name: str, user: str | None, responder: Responder | None) -> None:
        self.name = name
        self.user = user
        self.statements: list[str] = []
        self.is_closed = False
        self.close_calls = 0
        self.overlapped = False
        self.session = str(1000 + int(name))
        self._responder = responder
        self._active = 0
        self._guard = threading.Lock()
        self.hold = threading.Event()
        self.hold.set()
        self.entered = threading.Event()

    def session_id(self) -> str:
        return self.session

    def execute(self, query: str, query_params: Mapping[str, Any] | None = None) -> FakeStatement:
        with self._guard:
            self._active += 1
            self.overlapped = self.overlapped or self._active > 1
        try:
            text = (
                sqlcatalog.new_formatter().format(query, **query_params) if query_params else query
            )
            self.statements.append(text)
            self.entered.set()
            self.hold.wait(5)
            reply = self._responder(text) if self._responder else None
            if isinstance(reply, BaseException):
                if isinstance(reply, ExaCommunicationError):
                    self.is_closed = True
                raise reply
            return FakeStatement(text, reply or Reply())
        finally:
            with self._guard:
                self._active -= 1

    def close(self, disconnect: bool = True) -> None:
        self.close_calls += 1
        self.is_closed = True


class FakeConnector:
    """Stands in for `Target.connect`: records every connect and hands out fake connections."""

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder
        self.connects: list[tuple[str | None, str | None, float | None]] = []
        self.connections: list[FakeConnection] = []
        self.failure: BaseException | None = None
        self._lock = threading.Lock()

    def __call__(
        self, user: str | None, password: str | None, socket_timeout: float | None
    ) -> FakeConnection:
        with self._lock:
            self.connects.append((user, password, socket_timeout))
            if self.failure is not None:
                raise self.failure
            connection = FakeConnection(str(len(self.connections) + 1), user, self.responder)
            self.connections.append(connection)
            return connection

    def unreachable(self, message: str = "cannot connect to Exasol at db:8563") -> None:
        self.failure = TargetError(message)

    def reachable(self) -> None:
        self.failure = None

    def statements(self, index: int = 0) -> list[str]:
        return self.connections[index].statements
