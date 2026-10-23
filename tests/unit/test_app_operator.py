"""Unit tests of `app/backend/operator.py`: when the owner connection exists, whose it is, and the
fixed statement allowlist that every statement on it must match (recorded and checked)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.backend import db, operator, security
from app.backend.settings import Settings
from tests.unit.db_fakes import FakeConnector, Reply
from vs.host import fetchlog
from vs.host.namespace import Namespace

OWNER_PASSWORD = "owner-secret-789"
REAL = Namespace("")
TEST = Namespace("TEST_")


def resolver(host: str, port: int) -> list[str]:
    return ["127.0.0.1"] if host in ("127.0.0.1", "localhost") else ["10.1.2.3"]


def make_settings(**env: str) -> Settings:
    values = {"CFB_APP_PASSWORD": "app-secret-123", **env}
    return Settings.from_env(values, dotenv_path=Path("/nonexistent/.env"), resolver=resolver)


def prefixed_settings(**env: str) -> Settings:
    return make_settings(
        APP_PREFIX="TEST_",
        APP_LEDGER_PATH="/tmp/ledger.jsonl",
        CFB_TEST_OWNER_PASSWORD=OWNER_PASSWORD,
        **env,
    )


def fetch_rows(count: int = 2) -> list[fetchlog.FetchLogRow]:
    logged = datetime(2026, 11, 2, 12, 0, 0)
    return [
        fetchlog.FetchLogRow(
            logged,
            "APP_PREVIEW#7",
            "GAMES",
            f"/games?year={2020 + index}",
            "LIVE",
            200,
            1,
            40,
            10,
            logged,
        )
        for index in range(count)
    ]


# ---- when the operator path exists and whose connection it is -------------------------------


def test_the_operator_path_is_on_for_a_loopback_bind() -> None:
    path = operator.OperatorPath(make_settings(), FakeConnector())
    assert path.enabled and "loopback" in path.reason


def test_a_non_loopback_bind_or_operator_off_never_connects_and_says_why() -> None:
    connector = FakeConnector()
    for env, reason in (
        ({"APP_HOST": "10.1.2.3"}, "bound to a non-loopback address"),
        ({"APP_OPERATOR": "off"}, "APP_OPERATOR=off"),
    ):
        path = operator.OperatorPath(make_settings(**env), connector)
        assert not path.enabled and path.reason == reason
        with pytest.raises(operator.OperatorDisabled, match=reason), path.connection():
            pass
        with pytest.raises(operator.OperatorDisabled):
            path.read_impersonation_grants()
    assert connector.connects == []


def test_the_real_namespace_connects_as_the_owner_through_the_target_without_a_password() -> None:
    connector = FakeConnector()
    with operator.OperatorPath(make_settings(), connector).connection():
        pass
    assert connector.connects == [(None, None, operator.OPERATOR_SOCKET_TIMEOUT_SECONDS)]


def test_a_test_namespace_connects_as_the_test_owner_with_its_password() -> None:
    connector = FakeConnector()
    with operator.OperatorPath(prefixed_settings(), connector).connection():
        pass
    assert connector.connects == [
        ("CFB_TEST_OWNER", OWNER_PASSWORD, operator.OPERATOR_SOCKET_TIMEOUT_SECONDS)
    ]


def test_a_test_namespace_without_the_owner_password_has_no_operator_path() -> None:
    connector = FakeConnector()
    plain = make_settings(APP_PREFIX="TEST_", APP_LEDGER_PATH="/tmp/ledger.jsonl")
    path = operator.OperatorPath(plain, connector)
    assert not path.enabled
    assert "CFB_TEST_OWNER_PASSWORD" in path.reason and OWNER_PASSWORD not in path.reason
    with pytest.raises(operator.OperatorDisabled), path.connection():
        pass
    assert connector.connects == []


def test_every_action_opens_its_own_connection_and_closes_it() -> None:
    connector = FakeConnector()
    path = operator.OperatorPath(make_settings(), connector)
    with path.connection():
        pass
    with pytest.raises(RuntimeError), path.connection():
        raise RuntimeError("boom")
    assert len(connector.connections) == 2
    assert [connection.close_calls for connection in connector.connections] == [1, 1]


def test_an_unreachable_database_is_reported_without_a_secret() -> None:
    connector = FakeConnector()
    connector.unreachable()
    path = operator.OperatorPath(prefixed_settings(), connector)
    with pytest.raises(db.DatabaseUnreachable) as caught, path.connection():
        pass
    assert OWNER_PASSWORD not in str(caught.value)


def test_the_connection_reports_the_session_id_the_refresh_will_run_in() -> None:
    connector = FakeConnector()
    with operator.OperatorPath(make_settings(), connector).connection() as connection:
        assert connection.session_id() == int(connector.connections[0].session_id())


# ---- the allowlist -------------------------------------------------------------------------

IMPERSONATION = security.IMPERSONATION_QUERY


def allowed_samples(namespace: Namespace) -> dict[str, str]:
    meta = namespace.meta_schema
    return {
        "refresh_run": operator.refresh_statement(namespace, 2026, 10, "regular"),
        "refresh_run_post": operator.refresh_statement(namespace, 2025, 3, "postseason"),
        "refresh_run_id": operator.run_id_statement(namespace, 123456),
        "quota": f'SELECT * FROM "{meta}"."QUOTA"',
        "load_run_row": f'SELECT * FROM "{meta}"."LOAD_RUN" WHERE "RUN_ID" = 42',
        "load_run_running": (
            f'SELECT "RUN_ID", "RUN_KIND" FROM "{meta}"."LOAD_RUN" WHERE "RUN_STATUS" = \'RUNNING\''
        ),
        "impersonation": IMPERSONATION,
    }


def refused_samples(namespace: Namespace) -> dict[str, str]:
    meta, infra = namespace.meta_schema, namespace.infra_schema
    return {
        "drop": f'DROP SCHEMA "{meta}" CASCADE',
        "delete_log": f'DELETE FROM "{meta}"."FETCH_LOG"',
        "truncate": f'TRUNCATE TABLE "{meta}"."FETCH_LOG"',
        "insert_other": f'INSERT INTO "{meta}"."LOAD_RUN" VALUES (1)',
        "other_script": f'EXECUTE SCRIPT "{infra}"."SIMULATE_FROM"(2026, 10)',
        "dba_view": 'SELECT * FROM "SYS"."EXA_DBA_USERS"',
        "alter_user": "ALTER USER CFB_APP_WEB IDENTIFIED BY 'x'",
        "grant": 'GRANT DBA TO "CFB_APP_WEB"',
        "stacked_refresh": (
            f'EXECUTE SCRIPT "{infra}"."REFRESH_WEEK"(2026, 10, \'regular\'); DROP USER X'
        ),
        "text_season": f"EXECUTE SCRIPT \"{infra}\".\"REFRESH_WEEK\"('2026', 10, 'regular')",
        "other_type": f'EXECUTE SCRIPT "{infra}"."REFRESH_WEEK"(2026, 10, \'preseason\')',
        "stacked_read": f'SELECT * FROM "{meta}"."QUOTA"; DELETE FROM "{meta}"."FETCH_LOG"',
        "read_suffix": (
            f'SELECT * FROM "{meta}"."QUOTA" UNION ALL SELECT * FROM "SYS"."EXA_DBA_USERS"'
        ),
        "other_table": f'SELECT * FROM "{meta}"."SECRETS"',
        "insert_stacked": (f'INSERT INTO "{meta}".FETCH_LOG ("LOGGED_AT") VALUES (1); DROP USER X'),
        "insert_columns": f'INSERT INTO {meta}.FETCH_LOG ("LOGGED_AT") VALUES (1)',
        "key_read": 'SELECT * FROM "SYS"."EXA_DBA_CONNECTIONS"',
        "empty": "",
    }


@pytest.mark.parametrize("namespace", [REAL, TEST], ids=["real", "test"])
def test_every_allowed_statement_matches_exactly_one_template(namespace: Namespace) -> None:
    allowed = operator.allowlist(namespace)
    for name, sql in allowed_samples(namespace).items():
        matching = [entry.name for entry in allowed if entry.matches(sql)]
        assert len(matching) == 1, (name, matching)


@pytest.mark.parametrize("namespace", [REAL, TEST], ids=["real", "test"])
def test_nothing_else_matches_any_template(namespace: Namespace) -> None:
    allowed = operator.allowlist(namespace)
    for name, sql in refused_samples(namespace).items():
        assert not any(entry.matches(sql) for entry in allowed), name


def test_the_templates_of_one_namespace_do_not_match_the_other() -> None:
    real_sql = allowed_samples(REAL)["refresh_run"]
    test_sql = allowed_samples(TEST)["refresh_run"]
    assert not any(entry.matches(real_sql) for entry in operator.allowlist(TEST))
    assert not any(entry.matches(test_sql) for entry in operator.allowlist(REAL))


def test_the_allowlist_names_the_refresh_the_fetch_log_the_impersonation_query_and_reads() -> None:
    names = {entry.name for entry in operator.allowlist(REAL)}
    assert {"refresh_run", "refresh_run_id", "fetch_log_insert", "impersonation_grants"} <= names


def test_the_refresh_statement_takes_only_integers_and_the_two_season_types() -> None:
    assert (
        operator.refresh_statement(REAL, 2026, 10, "regular")
        == 'EXECUTE SCRIPT "CFB_INFRA"."REFRESH_WEEK"(2026, 10, \'regular\')'
    )
    for season, week, kind in (
        ("2026", 10, "regular"),
        (2026, "10; DROP", "regular"),
        (True, 1, "regular"),
        (2026, 10, "preseason"),
        (2026, 10, "regular'); DROP"),
    ):
        with pytest.raises(ValueError):
            operator.refresh_statement(REAL, season, week, kind)  # type: ignore[arg-type]


def test_the_run_id_statement_takes_only_an_integer_session_id() -> None:
    assert operator.run_id_statement(REAL, 77).endswith(
        '"SESSION_ID" = 77 AND "RUN_KIND" = \'REFRESH_WEEK\' ORDER BY "RUN_ID" DESC LIMIT 1'
    )
    with pytest.raises(ValueError):
        operator.run_id_statement(REAL, "77 OR 1=1")  # type: ignore[arg-type]


# ---- the wrapper: records, refuses before sending --------------------------------------------


def test_the_wrapper_records_every_statement_and_sends_the_recorded_text() -> None:
    connector = FakeConnector()
    path = operator.OperatorPath(make_settings(), connector)
    with path.connection() as connection:
        for sql in allowed_samples(REAL).values():
            connection.execute(sql)
    sent = [s for s in connector.statements() if not s.startswith("ALTER SESSION")]
    assert sent == list(allowed_samples(REAL).values())
    assert connection.statements == sent
    allowed = operator.allowlist(REAL)
    assert all(any(entry.matches(sql) for entry in allowed) for sql in connection.statements)


def test_a_statement_outside_the_allowlist_is_refused_before_it_is_sent() -> None:
    connector = FakeConnector()
    path = operator.OperatorPath(make_settings(), connector)
    with path.connection() as connection:
        for sql in refused_samples(REAL).values():
            with pytest.raises(operator.StatementNotAllowed):
                connection.execute(sql)
    assert connector.statements() == []
    assert connection.statements == []


def test_a_refusal_names_the_statement_without_its_values() -> None:
    path = operator.OperatorPath(make_settings(), FakeConnector())
    with path.connection() as connection, pytest.raises(operator.StatementNotAllowed) as caught:
        connection.execute("ALTER USER X IDENTIFIED BY 'hunter2-secret'")
    assert "hunter2-secret" not in str(caught.value)


def test_the_fetch_log_insert_of_phase_four_goes_through_the_wrapper() -> None:
    connector = FakeConnector()
    path = operator.OperatorPath(prefixed_settings(), connector)
    with path.connection() as connection:
        fetchlog.insert_fetch_log(connection, TEST.meta_schema, fetch_rows(3))
    (statement,) = connection.statements
    assert statement.startswith("INSERT INTO TEST_CFB_META.FETCH_LOG (")
    assert statement == connector.statements()[-1]
    assert any(entry.matches(statement) for entry in operator.allowlist(TEST))


def test_the_fetch_log_insert_into_another_schema_is_refused() -> None:
    path = operator.OperatorPath(prefixed_settings(), FakeConnector())
    with path.connection() as connection, pytest.raises(operator.StatementNotAllowed):
        fetchlog.insert_fetch_log(connection, "CFB_META", fetch_rows(1))


def test_a_recorder_sees_every_statement_of_every_action() -> None:
    seen: list[str] = []
    path = operator.OperatorPath(make_settings(), FakeConnector(), recorder=seen.append)
    with path.connection() as connection:
        connection.execute(operator.refresh_statement(REAL, 2026, 10, "regular"))
    with path.connection() as connection:
        connection.execute('SELECT * FROM "CFB_META"."QUOTA"')
    assert len(seen) == 2


def test_extra_templates_extend_the_allowlist_for_one_path_only() -> None:
    extra = operator.AllowedStatement.of(
        "estimate", r'SELECT COUNT\(\*\) FROM "CFB_META"."LOAD_SLICES"'
    )
    connector = FakeConnector()
    path = operator.OperatorPath(make_settings(), connector, extra_allowed=(extra,))
    with path.connection() as connection:
        connection.execute('SELECT COUNT(*) FROM "CFB_META"."LOAD_SLICES"')
    plain_path = operator.OperatorPath(make_settings(), FakeConnector())
    with plain_path.connection() as plain, pytest.raises(operator.StatementNotAllowed):
        plain.execute('SELECT COUNT(*) FROM "CFB_META"."LOAD_SLICES"')


# ---- the privilege check's fallback -----------------------------------------------------------


def test_the_impersonation_grants_are_read_with_the_one_allowed_query() -> None:
    rows = [("CFB_APP", "SYS", "SYS")]
    connector = FakeConnector(lambda sql: Reply(("GRANTEE", "IMPERSONATION_ON", "GRANTOR"), rows))
    grants = operator.OperatorPath(make_settings(), connector).read_impersonation_grants()
    assert grants == rows
    assert IMPERSONATION in connector.statements()
    assert connector.connections[0].close_calls == 1


def test_the_operator_path_hands_the_start_check_its_impersonation_reader() -> None:
    on = operator.OperatorPath(make_settings(), FakeConnector())
    off = operator.OperatorPath(make_settings(APP_OPERATOR="off"), FakeConnector())
    assert on.impersonation_reader() is not None
    assert off.impersonation_reader() is None


def test_no_password_appears_in_any_operator_error_or_repr() -> None:
    texts: list[Any] = []
    path = operator.OperatorPath(prefixed_settings(APP_OPERATOR="off"), FakeConnector())
    for action in (path.connection().__enter__, path.read_impersonation_grants):
        with pytest.raises(operator.OperatorDisabled) as caught:
            action()
        texts.append(str(caught.value))
    texts += [repr(path), repr(prefixed_settings()), path.reason]
    assert not any(OWNER_PASSWORD in str(text) for text in texts)
