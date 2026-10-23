"""The operator path: the namespace owner's connection, on only when the server listens on loopback,
and the fixed allowlist of statements that may run on it.

Each action opens its own connection through `vs/host/target.py` (the password is read at connect
time and never stored; in a test namespace the owner is `CFB_<prefix>OWNER` with
`CFB_TEST_OWNER_PASSWORD`). The connection is wrapped: it renders a statement, refuses it unless it
matches a template of the allowlist, records it, and only then sends it. The templates cover the
refresh (`EXECUTE SCRIPT ... REFRESH_WEEK`, the run-id lookup, the reads the guard and the ledger
need), the fetch-log rows of the app's own requests, and the database-wide impersonation
grants of the start check. Nothing else runs here; `tests/unit/test_app_operator.py` pins the
templates.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any

from app.backend import security, sqlcatalog
from app.backend.db import Connector, DatabaseUnreachable, ImpersonationReader, Rows
from app.backend.settings import Settings
from vs.host import fetchlog
from vs.host.namespace import Namespace
from vs.host.target import TargetError

OPERATOR_SOCKET_TIMEOUT_SECONDS = 1800
OWNER_PASSWORD_VARIABLE = "CFB_TEST_OWNER_PASSWORD"
SEASON_TYPES = ("regular", "postseason")
REFRESH_SCRIPT = "REFRESH_WEEK"
REFRESH_RUN_KIND = "REFRESH_WEEK"
MAX_SEASON = 9999
MAX_WEEK = 999
SUMMARY_WORDS = 3

Recorder = Callable[[str], None]


class OperatorDisabled(RuntimeError):
    """The operator path is off; the message is the reason `GET /api/config` shows (403)."""


class StatementNotAllowed(RuntimeError):
    """A statement outside the allowlist; it is refused before it is sent."""


@dataclass(frozen=True)
class AllowedStatement:
    """One template of the allowlist: a name and the whole-statement pattern it must match."""

    name: str
    pattern: re.Pattern[str]

    @classmethod
    def of(cls, name: str, regex: str) -> AllowedStatement:
        return cls(name, re.compile(regex, re.DOTALL))

    def matches(self, sql: str) -> bool:
        """True when the whole statement matches and it holds no `;` (one statement only)."""
        return ";" not in sql and self.pattern.fullmatch(sql) is not None


def refresh_statement(namespace: Namespace, season: int, week: int, season_type: str) -> str:
    """`EXECUTE SCRIPT "<infra>"."REFRESH_WEEK"(<season>, <week>, '<season type>')`; the arguments
    are validated here, so no text of a request ever enters the statement."""
    _require_int("season", season, MAX_SEASON)
    _require_int("week", week, MAX_WEEK)
    if season_type not in SEASON_TYPES:
        raise ValueError(f"season_type must be one of {', '.join(SEASON_TYPES)}")
    return (
        f'EXECUTE SCRIPT "{namespace.infra_schema}"."{REFRESH_SCRIPT}"'
        f"({season}, {week}, '{season_type}')"
    )


def run_id_statement(namespace: Namespace, session_id: int) -> str:
    """The `LOAD_RUN` row of the refresh that runs in the owner session `session_id`."""
    _require_int("session_id", session_id, None)
    return (
        f'SELECT "RUN_ID" FROM "{namespace.meta_schema}"."LOAD_RUN" '
        f'WHERE "SESSION_ID" = {session_id} AND "RUN_KIND" = \'{REFRESH_RUN_KIND}\' '
        'ORDER BY "RUN_ID" DESC LIMIT 1'
    )


def allowlist(namespace: Namespace) -> tuple[AllowedStatement, ...]:
    """The templates for `namespace`, as the plan's Operator allowlist lists them."""
    meta = re.escape(namespace.meta_schema)
    infra = re.escape(namespace.infra_schema)
    columns = re.escape(", ".join(fetchlog.FETCH_LOG_COLUMNS))
    types = "|".join(SEASON_TYPES)
    column_list = r'(?:\*|"[A-Z_]+"(?:, "[A-Z_]+")*)'
    return (
        AllowedStatement.of(
            "refresh_run",
            rf'EXECUTE SCRIPT "{infra}"\."{REFRESH_SCRIPT}"'
            rf"\(\d{{1,4}}, \d{{1,3}}, '(?:{types})'\)",
        ),
        AllowedStatement.of(
            "refresh_run_id",
            rf'SELECT "RUN_ID" FROM "{meta}"\."LOAD_RUN" WHERE "SESSION_ID" = \d+ '
            rf'AND "RUN_KIND" = \'{REFRESH_RUN_KIND}\' ORDER BY "RUN_ID" DESC LIMIT 1',
        ),
        AllowedStatement.of("quota", rf'SELECT \* FROM "{meta}"\."QUOTA"'),
        AllowedStatement.of(
            "load_run_row", rf'SELECT \* FROM "{meta}"\."LOAD_RUN" WHERE "RUN_ID" = \d+'
        ),
        AllowedStatement.of(
            "load_run_running",
            rf'SELECT {column_list} FROM "{meta}"\."LOAD_RUN" WHERE "RUN_STATUS" = \'RUNNING\'',
        ),
        AllowedStatement.of(
            "fetch_log_insert",
            rf'INSERT INTO "?{meta}"?\.FETCH_LOG \({columns}\) VALUES \(.*\)',
        ),
        AllowedStatement.of("impersonation_grants", re.escape(security.IMPERSONATION_QUERY)),
    )


class OperatorConnection:
    """An owner connection that sends only allowlisted statements and records each one."""

    def __init__(
        self,
        connection: Any,
        allowed: Sequence[AllowedStatement],
        recorder: Recorder | None = None,
    ) -> None:
        self._connection = connection
        self._allowed = tuple(allowed)
        self._recorder = recorder
        self.statements: list[str] = []

    def execute(self, query: str, query_params: Mapping[str, Any] | None = None) -> Any:
        """Render `query` with its values, refuse it unless it matches the allowlist, record it and
        send exactly the recorded text."""
        text = sqlcatalog.new_formatter().format(query, **query_params) if query_params else query
        if not any(entry.matches(text) for entry in self._allowed):
            summary = " ".join(text.split()[:SUMMARY_WORDS])
            raise StatementNotAllowed(
                f"the operator path does not run this statement (it starts with: {summary})"
            )
        self.statements.append(text)
        if self._recorder is not None:
            self._recorder(text)
        return self._connection.execute(text)

    def session_id(self) -> int:
        """The database session this connection runs in."""
        return int(self._connection.session_id())

    def close(self) -> None:
        with suppress(Exception):
            self._connection.close()


class OperatorPath:
    """Whether the owner connection exists, and a fresh allowlisted connection per action."""

    def __init__(
        self,
        settings: Settings,
        connector: Connector,
        *,
        extra_allowed: Sequence[AllowedStatement] = (),
        recorder: Recorder | None = None,
    ) -> None:
        namespace = settings.namespace
        self._connector = connector
        self._recorder = recorder
        self._allowed = (*allowlist(namespace), *extra_allowed)
        self._user = None if namespace.is_real else namespace.owner_user
        self._password = None if namespace.is_real else settings.owner_password
        self.enabled, self.reason = self._decide(settings, namespace)

    def open(self) -> OperatorConnection:
        """A new owner connection; the caller closes it. `OperatorDisabled` when the path is off,
        `DatabaseUnreachable` when the owner cannot connect."""
        if not self.enabled:
            raise OperatorDisabled(self.reason)
        try:
            connection = self._connector(
                self._user, self._password, OPERATOR_SOCKET_TIMEOUT_SECONDS
            )
        except TargetError as error:
            raise DatabaseUnreachable(str(error)) from None
        return OperatorConnection(connection, self._allowed, self._recorder)

    @contextmanager
    def connection(self) -> Iterator[OperatorConnection]:
        """One action's connection, closed at the end."""
        opened = self.open()
        try:
            yield opened
        finally:
            opened.close()

    def read_impersonation_grants(self) -> Rows:
        """`EXA_DBA_IMPERSONATION_PRIVS`: the grants no user can see for itself (probe s9 (d))."""
        with self.connection() as connection:
            statement = connection.execute(security.IMPERSONATION_QUERY)
            return [tuple(row) for row in statement.fetchall()]

    def impersonation_reader(self) -> ImpersonationReader | None:
        """What the start check calls to read the impersonation grants; None when off."""
        return self.read_impersonation_grants if self.enabled else None

    def _decide(self, settings: Settings, namespace: Namespace) -> tuple[bool, str]:
        decision = settings.operator
        if not decision.enabled:
            return False, decision.reason
        if not namespace.is_real and not self._password:
            return False, f"{OWNER_PASSWORD_VARIABLE} is not set for {namespace.owner_user}"
        return True, decision.reason


def _require_int(name: str, value: object, upper: int | None) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    if upper is not None and value > upper:
        raise ValueError(f"{name} must be at most {upper}")
