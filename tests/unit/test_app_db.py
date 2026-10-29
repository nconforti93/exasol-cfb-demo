"""Unit tests of `app/backend/db.py`: the read pool, the reserved live session, the console session,
the connector through `vs/host/target.py` and the effective-privilege start check. Fake connections;
no database."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.backend import db, security, settings, spend
from app.backend.sessions import QueryLog
from tests.unit.db_fakes import (
    FakeConnector,
    Reply,
    collision,
    lost_connection,
    query_error,
)
from vs.host import target

APP_PASSWORD = "app-secret-123"
CONSOLE_PASSWORD = "console-secret-456"
OWNER_PASSWORD = "owner-secret-789"
ROOT = Path(__file__).resolve().parents[2]


def resolver(host: str, port: int) -> list[str]:
    return ["127.0.0.1"] if host in ("127.0.0.1", "localhost") else ["10.1.2.3"]


def make_settings(**env: str) -> settings.Settings:
    values = {
        "CFB_APP_PASSWORD": APP_PASSWORD,
        "CFB_CONSOLE_PASSWORD": CONSOLE_PASSWORD,
        "TARGET": "local",
        **env,
    }
    return settings.Settings.from_env(
        values, dotenv_path=Path("/nonexistent/.env"), resolver=resolver
    )


def app_spec(**overrides: Any) -> db.SessionSpec:
    fields: dict[str, Any] = {
        "user": "CFB_APP_WEB",
        "password": APP_PASSWORD,
        "principal": "app",
        "query_timeout": spend.POOL_QUERY_TIMEOUT_SECONDS,
        "socket_timeout": db.POOL_SOCKET_TIMEOUT_SECONDS,
    }
    return db.SessionSpec(**{**fields, **overrides})


def live_spec() -> db.SessionSpec:
    return app_spec(
        query_timeout=spend.LIVE_QUERY_TIMEOUT_SECONDS,
        socket_timeout=spend.LIVE_SOCKET_TIMEOUT_SECONDS,
    )


def new_pool(connector: FakeConnector, **overrides: Any) -> db.ReadPool:
    options: dict[str, Any] = {"wait": 0.2}
    return db.ReadPool(connector, app_spec(), **{**options, **overrides})


# ---- sessions: timeouts, results, the query log --------------------------------------------


def test_the_pool_session_sets_the_30_second_query_timeout_and_the_socket_timeout() -> None:
    connector = FakeConnector()
    new_pool(connector).run("SELECT 1")
    assert connector.connects == [
        ("CFB_APP_WEB", APP_PASSWORD, db.POOL_SOCKET_TIMEOUT_SECONDS),
    ]
    assert connector.statements()[0] == "ALTER SESSION SET QUERY_TIMEOUT = 30"


def test_the_live_session_sets_300_seconds_and_a_330_second_socket_timeout() -> None:
    connector = FakeConnector()
    db.LiveSession(connector, live_spec()).run("SELECT 1")
    assert connector.connects == [("CFB_APP_WEB", APP_PASSWORD, 330)]
    assert connector.statements()[0] == "ALTER SESSION SET QUERY_TIMEOUT = 300"


def test_the_socket_timeout_stays_above_the_query_timeout_of_every_kind_of_session() -> None:
    assert spend.POOL_QUERY_TIMEOUT_SECONDS < db.POOL_SOCKET_TIMEOUT_SECONDS
    assert spend.LIVE_QUERY_TIMEOUT_SECONDS < spend.LIVE_SOCKET_TIMEOUT_SECONDS
    assert spend.worst_case_fetch_seconds() < spend.LIVE_QUERY_TIMEOUT_SECONDS


def test_a_result_carries_the_text_the_driver_sent_the_columns_the_rows_and_the_time() -> None:
    times = iter([10.0, 10.25])
    connector = FakeConnector(lambda sql: Reply(("A", "B"), [(1, "x"), (2, "y")]))
    pool = new_pool(connector, clock=lambda: next(times))
    result = pool.run('SELECT "A", "B" FROM T WHERE "A" = {a!d}', {"a": 7})
    assert result.sql == 'SELECT "A", "B" FROM T WHERE "A" = 7'
    assert result.columns == ("A", "B")
    assert result.rows == [(1, "x"), (2, "y")]
    assert result.ms == 250
    assert result.user == "CFB_APP_WEB" and result.principal == "app"
    assert result.dicts() == [{"A": 1, "B": "x"}, {"A": 2, "B": "y"}]


def test_a_statement_is_recorded_in_the_session_log_with_its_principal_and_page() -> None:
    log = QueryLog()
    pool = new_pool(FakeConnector(lambda sql: Reply(("A",), [(1,), (2,), (3,)])))
    pool.run("SELECT 1", log=log, page="odds", context="ctx")
    (entry,) = log.entries()
    assert (entry.sql, entry.principal, entry.user) == ("SELECT 1", "app", "CFB_APP_WEB")
    assert (entry.page, entry.rows, entry.error, entry.context) == ("odds", 3, None, "ctx")


def test_a_failed_statement_is_logged_with_the_first_line_of_the_error() -> None:
    log = QueryLog()
    failure = query_error("42000", "insufficient privileges\nsecond line (Session: 123)")
    pool = new_pool(FakeConnector(lambda sql: failure if sql == "SELECT 1" else None))
    with pytest.raises(type(failure)):
        pool.run("SELECT 1", log=log)
    (entry,) = log.entries()
    assert entry.error == "insufficient privileges" and entry.rows == 0


def test_without_a_log_nothing_is_recorded_and_the_result_still_comes_back() -> None:
    assert new_pool(FakeConnector()).run("SELECT 1").rows == [(1,)]


# ---- the read pool -------------------------------------------------------------------------


def test_sessions_open_lazily_and_are_reused() -> None:
    connector = FakeConnector()
    pool = new_pool(connector)
    assert connector.connects == []
    pool.run("SELECT 1")
    pool.run("SELECT 2")
    assert len(connector.connections) == 1


def test_four_threads_get_four_sessions_and_no_session_serves_two_at_once() -> None:
    connector = FakeConnector()
    pool = new_pool(connector)
    barrier = threading.Barrier(4)

    def responder(sql: str) -> Reply:
        if sql.startswith("SELECT"):
            barrier.wait(5)
        return Reply()

    connector.responder = responder
    errors: list[BaseException] = []

    def work() -> None:
        try:
            pool.run("SELECT 1")
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert len(connector.connections) == db.POOL_SIZE == 4
    assert not any(connection.overlapped for connection in connector.connections)


def test_a_checkout_that_finds_every_session_busy_waits_and_answers_busy() -> None:
    connector = FakeConnector()
    pool = new_pool(connector, size=1, wait=0.05)
    pool.run("SELECT 0")
    connection = connector.connections[0]
    connection.hold.clear()
    connection.entered.clear()
    holder = threading.Thread(target=lambda: pool.run("SELECT 1"))
    holder.start()
    assert connection.entered.wait(5)
    with pytest.raises(db.PoolBusy, match="busy"):
        pool.run("SELECT 2")
    connection.hold.set()
    holder.join(5)
    assert pool.run("SELECT 3").rows == [(1,)]


def test_an_unreachable_database_is_reported_with_the_target_message_and_frees_the_slot() -> None:
    connector = FakeConnector()
    connector.unreachable("cannot connect to Exasol at 127.0.0.1:8563 (target local): OSError")
    pool = new_pool(connector, size=1)
    for _ in range(3):
        with pytest.raises(db.DatabaseUnreachable, match="cannot connect to Exasol"):
            pool.run("SELECT 1")
    connector.reachable()
    assert pool.run("SELECT 1").rows == [(1,)]


def test_a_broken_session_is_closed_and_replaced_on_the_next_checkout() -> None:
    state = {"fail": True}

    def responder(sql: str) -> Reply | BaseException:
        if sql == "SELECT 1" and state["fail"]:
            state["fail"] = False
            return lost_connection()
        return Reply()

    connector = FakeConnector(responder)
    pool = new_pool(connector, size=1)
    with pytest.raises(db.DatabaseUnreachable) as caught:
        pool.run("SELECT 1")
    assert "127.0.0.1" not in str(caught.value) and "websocket" not in str(caught.value)
    assert connector.connections[0].close_calls == 1
    assert pool.run("SELECT 1").rows == [(1,)]
    assert len(connector.connections) == 2


def test_a_transaction_conflict_is_retried_once_for_a_zero_call_statement() -> None:
    attempts: list[str] = []

    def responder(sql: str) -> Reply | BaseException:
        if sql == "SELECT 1":
            attempts.append(sql)
            return collision() if len(attempts) == 1 else Reply()
        return Reply()

    pool = new_pool(FakeConnector(responder))
    assert pool.run("SELECT 1").rows == [(1,)]
    assert len(attempts) == 2


def test_a_second_conflict_is_raised_and_a_plain_error_is_not_retried() -> None:
    attempts: list[str] = []

    def responder(sql: str) -> Reply | BaseException:
        if sql.startswith("SELECT"):
            attempts.append(sql)
            return collision() if sql == "SELECT 1" else query_error("42000", "syntax error")
        return Reply()

    pool = new_pool(FakeConnector(responder))
    with pytest.raises(Exception, match="collision"):
        pool.run("SELECT 1")
    assert len(attempts) == 2
    attempts.clear()
    with pytest.raises(Exception, match="syntax error"):
        pool.run("SELECT 2")
    assert len(attempts) == 1


def test_the_pool_refuses_a_statement_marked_live_and_never_connects_for_it() -> None:
    connector = FakeConnector()
    pool = new_pool(connector)
    with pytest.raises(db.LiveStatementRefused, match="live"):
        pool.run("SELECT 1", kind=db.StatementKind.LIVE)
    assert connector.connects == []


def test_closing_the_pool_closes_its_sessions() -> None:
    connector = FakeConnector()
    pool = new_pool(connector)
    pool.run("SELECT 1")
    pool.close()
    assert connector.connections[0].close_calls == 1


# ---- the reserved live session -------------------------------------------------------------


def test_a_live_statement_runs_exactly_once_on_the_reserved_session() -> None:
    connector = FakeConnector()
    live = db.LiveSession(connector, live_spec())
    live.run("SELECT 1")
    live.run("SELECT 2")
    assert len(connector.connections) == 1
    assert connector.statements()[1:] == ["SELECT 1", "SELECT 2"]


def test_a_live_statement_is_never_retried_not_even_after_a_conflict() -> None:
    connector = FakeConnector(lambda sql: collision() if sql == "SELECT 1" else Reply())
    live = db.LiveSession(connector, live_spec())
    with pytest.raises(Exception, match="collision"):
        live.run("SELECT 1")
    assert connector.statements().count("SELECT 1") == 1


def test_a_live_statement_that_loses_the_connection_is_not_resent() -> None:
    connector = FakeConnector(lambda sql: lost_connection() if sql == "SELECT 1" else Reply())
    live = db.LiveSession(connector, live_spec())
    with pytest.raises(db.DatabaseUnreachable):
        live.run("SELECT 1")
    assert connector.statements().count("SELECT 1") == 1
    assert connector.connections[0].close_calls == 1
    assert live.run("SELECT 2").rows == [(1,)]
    assert len(connector.connections) == 2


def test_a_live_session_that_cannot_connect_reports_the_database_unreachable() -> None:
    connector = FakeConnector()
    connector.unreachable()
    with pytest.raises(db.DatabaseUnreachable):
        db.LiveSession(connector, live_spec()).run("SELECT 1")


def test_a_second_live_statement_while_one_runs_is_busy() -> None:
    connector = FakeConnector()
    live = db.LiveSession(connector, live_spec(), wait=0.05)
    live.run("SELECT 0")
    connection = connector.connections[0]
    connection.hold.clear()
    connection.entered.clear()
    runner = threading.Thread(target=lambda: live.run("SELECT 1"))
    runner.start()
    assert connection.entered.wait(5)
    with pytest.raises(db.PoolBusy):
        live.run("SELECT 2")
    connection.hold.set()
    runner.join(5)
    assert not connection.overlapped


# ---- the console session -------------------------------------------------------------------


def test_the_console_runs_as_the_console_user_with_the_live_timeouts_one_session_per_action() -> (
    None
):
    connector = FakeConnector()
    spec = db.SessionSpec("CFB_CONSOLE_WEB", CONSOLE_PASSWORD, "reader", 300, 330)
    console = db.ConsoleSessions(connector, spec, db.Availability(True, "console user"))
    with console.session() as first:
        result = first.execute("SELECT 1")
    with console.session() as second:
        second.execute("SELECT 2")
    assert connector.connects == [("CFB_CONSOLE_WEB", CONSOLE_PASSWORD, 330)] * 2
    assert (result.user, result.principal) == ("CFB_CONSOLE_WEB", "reader")
    assert [connection.close_calls for connection in connector.connections] == [1, 1]
    assert connector.statements()[0] == "ALTER SESSION SET QUERY_TIMEOUT = 300"


def test_a_disabled_console_never_connects_and_says_why() -> None:
    connector = FakeConnector()
    console = db.ConsoleSessions(connector, None, db.Availability(False, "no console user"))
    with pytest.raises(db.ConsoleDisabled, match="no console user"), console.session():
        pass
    assert connector.connects == []


def test_the_console_session_is_closed_when_the_action_fails() -> None:
    connector = FakeConnector()
    spec = db.SessionSpec("CFB_CONSOLE_WEB", CONSOLE_PASSWORD, "reader", 300, 330)
    console = db.ConsoleSessions(connector, spec, db.Availability(True, "ok"))
    with pytest.raises(RuntimeError), console.session():
        raise RuntimeError("boom")
    assert connector.connections[0].close_calls == 1


def test_the_console_needs_a_password_and_the_operator_path() -> None:
    both = db.console_availability(make_settings())
    assert both.enabled
    no_password = db.console_availability(make_settings(CFB_CONSOLE_PASSWORD=""))
    assert not no_password.enabled and "CFB_CONSOLE_PASSWORD" in no_password.reason
    network = db.console_availability(make_settings(APP_HOST="10.1.2.3"))
    assert not network.enabled and "operator" in network.reason


# ---- the database facade and the connector through target.py -------------------------------


def test_build_database_wires_the_users_and_timeouts_from_the_settings() -> None:
    connector = FakeConnector()
    database = db.build_database(
        make_settings(CFB_APP_USER="CFB_APP_X", CFB_CONSOLE_USER="CFB_CONSOLE_X"), connector
    )
    database.pool.run("SELECT 1")
    database.live.run("SELECT 1")
    with database.console.session() as console:
        console.execute("SELECT 1")
    assert [connect[0] for connect in connector.connects] == [
        "CFB_APP_X",
        "CFB_APP_X",
        "CFB_CONSOLE_X",
    ]
    assert [connect[2] for connect in connector.connects] == [
        db.POOL_SOCKET_TIMEOUT_SECONDS,
        330,
        330,
    ]
    database.close()
    assert all(connection.close_calls >= 1 for connection in connector.connections)


def test_the_default_connector_connects_through_target_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    class FakeTarget:
        def connect(
            self, user: str | None, password: str | None, *, socket_timeout: float | None
        ) -> str:
            calls.append((user, password, socket_timeout))
            return "connection"

    resolved: list[Mapping[str, Any]] = []

    def resolve(name: str | None, *, dsn: str | None = None, **rest: Any) -> FakeTarget:
        resolved.append({"name": name, "dsn": dsn})
        return FakeTarget()

    monkeypatch.setattr(target, "resolve", resolve)
    connector = db.default_connector(make_settings(EXASOL_DSN="db.example:9000", TARGET="aws"))
    assert connector("CFB_APP_WEB", "pw", 30.0) == "connection"
    assert calls == [("CFB_APP_WEB", "pw", 30.0)]
    assert resolved == [{"name": "aws", "dsn": "db.example:9000"}]


def test_a_target_that_cannot_be_resolved_is_a_database_unreachable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(*args: Any, **kwargs: Any) -> None:
        raise target.TargetError("deployment file missing")

    monkeypatch.setattr(target, "resolve", resolve)
    pool = db.ReadPool(db.default_connector(make_settings()), app_spec(), wait=0.1)
    with pytest.raises(db.DatabaseUnreachable, match="deployment file missing"):
        pool.run("SELECT 1")


def test_no_module_of_the_backend_connects_to_the_database_by_itself() -> None:
    for name in ("db.py", "operator.py"):
        source = (ROOT / "app" / "backend" / name).read_text()
        assert "pyexasol.connect" not in source and "import pyexasol" not in source.replace(
            "from pyexasol import", ""
        )


# ---- the effective-privilege start check ---------------------------------------------------

APP = "CFB_APP_WEB"
CONSOLE = "CFB_CONSOLE_WEB"


def app_rows(**changes: Sequence[tuple[Any, ...]]) -> dict[str, list[tuple[Any, ...]]]:
    rows: dict[str, list[tuple[Any, ...]]] = {
        "session_privs": [("CREATE SESSION",), ("USE ANY SCHEMA",)],
        "session_roles": [("CFB_APP",)],
        "user_sys": [(APP, "CREATE SESSION")],
        "role_sys": [("PUBLIC", "USE ANY SCHEMA")],
        "user_roles": [(APP, "CFB_APP")],
        "role_roles": [],
        "all_obj": [
            (None, name, "SCHEMA", "SELECT", "CFB_APP") for name in ("CFB_API", "CFB_API_LIVE")
        ],
        "user_conn": [],
        "role_conn": [],
        "user_restricted": [],
        "role_restricted": [],
        "user_impersonation": [],
        "user_schemas": [],
        "user_objects": [],
    }
    rows.update({key: list(value) for key, value in changes.items()})
    return rows


def console_rows() -> dict[str, list[tuple[Any, ...]]]:
    return app_rows(
        session_roles=[("CFB_READER",)],
        user_sys=[(CONSOLE, "CREATE SESSION")],
        user_roles=[(CONSOLE, "CFB_READER")],
        all_obj=[(None, "CFBD_VS", "SCHEMA", "SELECT", "CFB_READER")],
    )


class PrivilegeServer:
    """A responder that answers each privilege query with the rows of the connected user."""

    def __init__(self, tables: Mapping[str, dict[str, list[tuple[Any, ...]]]]) -> None:
        self.tables = tables
        self.by_sql = {sql: key for key, sql in security.PRIVILEGE_QUERIES.items()}
        self.connector = FakeConnector(self.respond)
        self.broken: set[str] = set()
        self.asked: list[str] = []

    def respond(self, sql: str) -> Reply | BaseException | None:
        key = self.by_sql.get(sql)
        if key is None:
            return None
        user = self.connector.connections[-1].user or ""
        self.asked.append(sql)
        if user in self.broken:
            return query_error("42500", "insufficient privileges for accessing view")
        return Reply(("C",), self.tables[user][key])


def run_check(
    server: PrivilegeServer,
    *,
    impersonation: Callable[[], Sequence[Sequence[Any]]] | None = lambda: [],
    **env: str,
) -> db.StartCheck:
    return db.run_start_check(make_settings(**env), server.connector, impersonation)


def test_a_read_user_with_the_expected_grants_passes_and_the_console_user_too() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    result = run_check(server)
    assert result.users == (APP, CONSOLE)
    assert result.skipped is False and result.warnings == ()
    assert [connect[0] for connect in server.connector.connects] == [APP, CONSOLE]


def test_the_start_check_runs_the_users_own_privilege_queries_and_nothing_else() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    run_check(server)
    sent = [s for c in server.connector.connections for s in c.statements if s.startswith("SELECT")]
    assert sorted(sent) == sorted(list(security.PRIVILEGE_QUERIES.values()) * 2)
    assert not any("EXA_DBA" in statement for statement in sent)


def test_a_privilege_through_the_role_is_refused_naming_the_user_privilege_and_route() -> None:
    rows = app_rows(
        session_privs=[("CREATE SESSION",), ("USE ANY SCHEMA",), ("CREATE TABLE",)],
        role_sys=[("PUBLIC", "USE ANY SCHEMA"), ("CFB_APP", "CREATE TABLE")],
    )
    server = PrivilegeServer({APP: rows, CONSOLE: console_rows()})
    with pytest.raises(security.PrivilegeError) as caught:
        run_check(server)
    message = str(caught.value)
    assert APP in message and "CREATE TABLE" in message and "role CFB_APP" in message


def test_select_granted_to_public_and_a_connection_grant_are_refused() -> None:
    public = app_rows(
        all_obj=[
            (None, "CFB_API", "SCHEMA", "SELECT", "CFB_APP"),
            (None, "CFB_API_LIVE", "SCHEMA", "SELECT", "CFB_APP"),
            (None, "CFB_SIM", "SCHEMA", "SELECT", "PUBLIC"),
        ]
    )
    with pytest.raises(security.PrivilegeError, match=r"SELECT on schema CFB_SIM.*PUBLIC"):
        run_check(PrivilegeServer({APP: public, CONSOLE: console_rows()}))
    connection = app_rows(role_conn=[("CFB_APP", "CFBD_API")])
    with pytest.raises(security.PrivilegeError, match=r"connection CFBD_API.*role CFB_APP"):
        run_check(PrivilegeServer({APP: connection, CONSOLE: console_rows()}))


def test_the_console_user_is_checked_against_the_virtual_schema_and_its_role() -> None:
    wide = console_rows()
    wide["all_obj"].append((None, "CFB_API", "SCHEMA", "SELECT", "CFB_READER"))
    with pytest.raises(security.PrivilegeError, match=r"CFB_CONSOLE_WEB.*CFB_API"):
        run_check(PrivilegeServer({APP: app_rows(), CONSOLE: wide}))


def test_a_test_namespace_expects_the_prefixed_role_schemas_and_a_console_user_without_a_role() -> (
    None
):
    app = "CFB_TEST_APP"
    reader = "CFB_TEST_READER"
    app_table = app_rows(
        session_roles=[("TEST_CFB_APP",)],
        user_sys=[(app, "CREATE SESSION")],
        user_roles=[(app, "TEST_CFB_APP")],
        all_obj=[
            (None, name, "SCHEMA", "SELECT", "TEST_CFB_APP")
            for name in ("TEST_CFB_API", "TEST_CFB_API_LIVE")
        ],
    )
    reader_table = app_rows(
        session_roles=[],
        user_sys=[(reader, "CREATE SESSION")],
        user_roles=[],
        all_obj=[(None, "TEST_CFBD_VS", "SCHEMA", "SELECT", reader)],
    )
    server = PrivilegeServer({app: app_table, reader: reader_table})
    result = run_check(
        server,
        APP_PREFIX="TEST_",
        APP_LEDGER_PATH="/tmp/ledger.jsonl",
        CFB_APP_USER=app,
        CFB_CONSOLE_USER=reader,
    )
    assert result.users == (app, reader)


def test_the_namespace_owner_and_sys_cannot_be_the_read_user() -> None:
    server = PrivilegeServer({"SYS": app_rows(), CONSOLE: console_rows()})
    with pytest.raises(security.PrivilegeError, match="SYS"):
        run_check(server, CFB_APP_USER="SYS")
    owner = PrivilegeServer({"CFB_TEST_OWNER": app_rows(), CONSOLE: console_rows()})
    with pytest.raises(security.PrivilegeError, match="namespace owner"):
        run_check(
            owner,
            CFB_APP_USER="CFB_TEST_OWNER",
            APP_PREFIX="TEST_",
            APP_LEDGER_PATH="/tmp/ledger.jsonl",
        )


def test_impersonation_through_a_role_is_found_on_the_operator_path() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    grants = [("CFB_APP", "SYS", "SYS"), ("SOMEONE_ELSE", "SYS", "SYS")]
    with pytest.raises(security.PrivilegeError, match=r"impersonate SYS.*role CFB_APP"):
        run_check(server, impersonation=lambda: grants)


def test_impersonation_of_other_users_roles_is_not_held_against_these_users() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    result = run_check(server, impersonation=lambda: [("SOMEONE_ELSE", "SYS", "SYS")])
    assert result.skipped is False


def test_without_the_operator_path_the_start_is_refused_unless_the_check_is_skipped() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    with pytest.raises(security.PrivilegeError, match=r"impersonat.*APP_SKIP_READ_CHECK"):
        run_check(server, impersonation=None)
    skipped = run_check(server, impersonation=None, APP_SKIP_READ_CHECK="yes")
    assert skipped.skipped is True
    assert any("impersonation" in warning for warning in skipped.warnings)


def test_skipping_never_waives_a_violation_the_user_can_see_itself() -> None:
    rows = app_rows(
        session_privs=[("CREATE SESSION",), ("CREATE TABLE",)],
        user_sys=[(APP, "CREATE SESSION"), (APP, "CREATE TABLE")],
    )
    server = PrivilegeServer({APP: rows, CONSOLE: console_rows()})
    with pytest.raises(security.PrivilegeError, match="CREATE TABLE"):
        run_check(server, impersonation=None, APP_SKIP_READ_CHECK="yes")


def test_a_failing_impersonation_read_counts_as_unverified() -> None:
    def broken() -> list[Any]:
        raise query_error("42500", "insufficient privileges for accessing view")

    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    with pytest.raises(security.PrivilegeError, match="APP_SKIP_READ_CHECK"):
        run_check(server, impersonation=broken)
    assert run_check(server, impersonation=broken, APP_SKIP_READ_CHECK="yes").skipped


def test_a_user_that_cannot_read_its_own_privileges_is_refused_unless_skipped() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    server.broken.add(APP)
    with pytest.raises(security.PrivilegeError, match=r"CFB_APP_WEB.*own privileges"):
        run_check(server)
    skipped = run_check(server, APP_SKIP_READ_CHECK="yes")
    assert skipped.skipped and any(APP in warning for warning in skipped.warnings)


def test_the_console_user_is_not_checked_when_the_console_is_off() -> None:
    server = PrivilegeServer({APP: app_rows()})
    result = run_check(server, CFB_CONSOLE_PASSWORD="")
    assert result.users == (APP,)
    assert [connect[0] for connect in server.connector.connects] == [APP]


def test_the_start_check_sessions_are_closed() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    run_check(server)
    assert [connection.close_calls for connection in server.connector.connections] == [1, 1]


def test_a_wrong_password_or_a_down_database_stops_the_start_without_a_secret() -> None:
    server = PrivilegeServer({APP: app_rows(), CONSOLE: console_rows()})
    server.connector.unreachable("cannot connect to Exasol at 127.0.0.1:8563: ExaAuthError")
    with pytest.raises(db.DatabaseUnreachable) as caught:
        run_check(server)
    text = str(caught.value)
    assert APP_PASSWORD not in text and CONSOLE_PASSWORD not in text


def test_no_error_message_of_the_module_contains_a_password() -> None:
    connector = FakeConnector()
    connector.unreachable()
    texts: list[str] = []
    actions: list[Callable[[], Any]] = [
        lambda: new_pool(connector).run("SELECT 1"),
        lambda: db.LiveSession(connector, live_spec()).run("SELECT 1"),
        lambda: new_pool(FakeConnector()).run("SELECT 1", kind=db.StatementKind.LIVE),
    ]
    for action in actions:
        with pytest.raises(RuntimeError) as caught:
            action()
        texts.append(str(caught.value))
    assert not any(APP_PASSWORD in text for text in texts)
    assert APP_PASSWORD not in repr(make_settings()) and APP_PASSWORD not in repr(app_spec())
