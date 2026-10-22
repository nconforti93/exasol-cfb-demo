"""Pure and fake-connection parts of `tests/it/harness.py`: cleanup scope and secret handling."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.it import guards, harness
from tests.it.support import HarnessError
from vs.host import target
from vs.host.namespace import MOCK_CONNECTION, REAL_CONNECTION, Namespace
from vs.host.target import Target

TEST = Namespace("TEST_")
HOSTNAME_CONNECTION = f"{MOCK_CONNECTION}_HOSTNAME"
SECRET = "generated-Pa55word-123"


class Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    """Answers the harness's catalog queries from `tables` (matched by a substring of the SQL)."""

    def __init__(self, tables: Mapping[str, list[tuple[Any, ...]]] | None = None) -> None:
        self.tables = dict(tables or {})
        self.statements: list[str] = []

    def execute(self, sql: str, params: object = None) -> Rows:
        self.statements.append(sql)
        for marker, rows in self.tables.items():
            if marker in sql:
                return Rows(rows)
        return Rows([])


def test_owned_connections_are_the_mock_and_everything_named_after_it() -> None:
    names = [REAL_CONNECTION, MOCK_CONNECTION, HOSTNAME_CONNECTION, "OTHER", "MY_CFBD_API_MOCK"]
    assert harness.owned_connections(names) == sorted([MOCK_CONNECTION, HOSTNAME_CONNECTION])


def test_the_real_connection_is_never_an_owned_connection() -> None:
    assert harness.owned_connections([REAL_CONNECTION]) == []


def test_dropping_the_namespace_also_drops_stale_test_connections() -> None:
    connection = FakeConnection({"EXA_DBA_CONNECTIONS": [(HOSTNAME_CONNECTION,), ("OTHER",)]})
    assert harness.drop_namespace(connection, TEST) == []
    dropped = [s for s in connection.statements if s.startswith("DROP CONNECTION")]
    assert f"DROP CONNECTION IF EXISTS {MOCK_CONNECTION}" in dropped
    assert f"DROP CONNECTION IF EXISTS {HOSTNAME_CONNECTION}" in dropped
    assert not any("OTHER" in statement for statement in dropped)


def test_no_drop_statement_ever_names_the_real_connection() -> None:
    connection = FakeConnection({"EXA_DBA_CONNECTIONS": [(REAL_CONNECTION,), (MOCK_CONNECTION,)]})
    harness.drop_namespace(connection, TEST)
    for statement in connection.statements:
        assert not statement.startswith("DROP CONNECTION") or REAL_CONNECTION + " " not in (
            statement + " "
        )


def test_a_stale_connection_with_an_odd_name_is_reported_not_interpolated() -> None:
    odd = f'{MOCK_CONNECTION}"; DROP USER SYS --'
    connection = FakeConnection({"EXA_DBA_CONNECTIONS": [(odd,)]})
    failures = harness.drop_namespace(connection, TEST)
    assert any("refusing" in failure for failure in failures)
    assert not any("DROP USER SYS" in statement for statement in connection.statements)


def test_leftovers_report_a_stale_test_connection() -> None:
    connection = FakeConnection({"EXA_DBA_CONNECTIONS": [(HOSTNAME_CONNECTION,), ("OTHER",)]})
    assert harness.leftovers(connection, TEST) == [f"connection {HOSTNAME_CONNECTION}"]


def test_leftovers_are_empty_when_everything_is_gone() -> None:
    assert harness.leftovers(FakeConnection(), TEST) == []


def test_the_session_check_flags_a_test_schema_without_any_property_row() -> None:
    connection = FakeConnection({"EXA_ALL_VIRTUAL_SCHEMAS": [("TEST_CFBD_VS",), ("CFBD_VS",)]})
    with pytest.raises(guards.GuardError, match="TEST_CFBD_VS"):
        harness.assert_no_real_connection(connection)


def test_the_session_check_passes_a_mock_schema() -> None:
    connection = FakeConnection(
        {
            "EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES": [
                ("TEST_CFBD_VS", "CONNECTION_NAME", MOCK_CONNECTION)
            ],
            "EXA_ALL_VIRTUAL_SCHEMAS": [("TEST_CFBD_VS",)],
        }
    )
    harness.assert_no_real_connection(connection)


def test_an_error_carrying_a_generated_password_is_reraised_without_it() -> None:
    error = RuntimeError(f'CREATE USER X IDENTIFIED BY "{SECRET}"')
    with pytest.raises(HarnessError) as raised:
        harness.reraise_without_secrets(error, [SECRET])
    assert SECRET not in str(raised.value)
    assert raised.value.__suppress_context__


def test_an_error_without_a_secret_is_reraised_as_is() -> None:
    error = RuntimeError("harmless")
    with pytest.raises(RuntimeError) as raised:
        harness.reraise_without_secrets(error, [SECRET])
    assert raised.value is error


class SeenConnection(FakeConnection):
    """Reports the stale connection until its `DROP CONNECTION` ran."""

    def __init__(self, *, drop_works: bool) -> None:
        super().__init__()
        self.drop_works = drop_works
        self.stale = True

    def execute(self, sql: str, params: object = None) -> Rows:
        self.statements.append(sql)
        if sql.startswith(f"DROP CONNECTION IF EXISTS {HOSTNAME_CONNECTION}") and self.drop_works:
            self.stale = False
        if "EXA_DBA_CONNECTIONS" in sql:
            return Rows([(HOSTNAME_CONNECTION,)] if self.stale else [])
        return Rows([])


def test_session_setup_drops_a_stale_connection_and_verifies_it_is_gone() -> None:
    connection = SeenConnection(drop_works=True)
    harness.clear_previous_run(connection, TEST)
    assert not connection.stale


def test_session_setup_fails_loudly_when_a_stale_connection_cannot_be_dropped() -> None:
    with pytest.raises(HarnessError, match=f"left behind: connection {HOSTNAME_CONNECTION}"):
        harness.clear_previous_run(SeenConnection(drop_works=False), TEST)


def test_the_harness_only_runs_against_the_local_target() -> None:
    local = Target("local", "h", 1, "sys", Path("/x"), target.TLS_UNVERIFIED_LOCAL)
    aws = Target("aws", "h", 1, "sys", Path("/x"), target.TLS_PINNED, "AB")
    harness.require_local(local)
    with pytest.raises(HarnessError, match="local deployment"):
        harness.require_local(aws)
