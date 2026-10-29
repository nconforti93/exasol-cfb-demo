import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from scripts import grants
from scripts.grants import GrantsError
from vs.host.namespace import Namespace

REAL = Namespace("")
TEST = Namespace("TEST_")
PASSWORD = "s3cret'pw\"value"


class FakeDatabase:
    def __init__(
        self,
        *,
        users: set[str] | None = None,
        scripts: set[str] | None = None,
        connections: set[str] | None = None,
        script_grants: set[tuple[str, str, str]] | None = None,
        roles: set[str] | None = None,
        schemas: set[str] | None = None,
        select_grants: set[tuple[str, str]] | None = None,
    ) -> None:
        self.users = users or set()
        self.scripts = scripts or set()
        self.connections = connections or set()
        self.script_grants = script_grants or set()
        self.roles = roles or set()
        self.schemas = schemas or set()
        self.select_grants = select_grants or set()
        self.executed: list[tuple[str, Mapping[str, Any]]] = []

    def user_exists(self, name: str) -> bool:
        return name in self.users

    def script_exists(self, qualified_name: str) -> bool:
        return qualified_name in self.scripts

    def connection_exists(self, name: str) -> bool:
        return name in self.connections

    def script_grant_exists(self, connection: str, script: str, grantee: str) -> bool:
        return (connection, script, grantee) in self.script_grants

    def role_exists(self, name: str) -> bool:
        return name in self.roles

    def schema_exists(self, name: str) -> bool:
        return name in self.schemas

    def select_grant_exists(self, schema: str, grantee: str) -> bool:
        return (schema, grantee) in self.select_grants

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        self.executed.append((sql, params or {}))

    @property
    def statements(self) -> list[str]:
        return [sql for sql, _ in self.executed]


# --- pure SQL generation ---


def test_create_user_binds_the_password_as_a_parameter() -> None:
    step = grants.create_user_step("CFB_TEST_OWNER", PASSWORD)
    assert step.sql == "CREATE USER CFB_TEST_OWNER IDENTIFIED BY {password!q}"
    assert step.params == {"password": PASSWORD}
    assert PASSWORD not in step.sql
    assert PASSWORD not in repr(step)


def test_owner_privileges_are_exactly_the_documented_system_privileges() -> None:
    step = grants.owner_privileges_step("CFB_TEST_OWNER")
    assert step.sql == (
        "GRANT CREATE SESSION, CREATE SCHEMA, CREATE TABLE, CREATE VIEW, CREATE SCRIPT, "
        "CREATE FUNCTION, CREATE VIRTUAL SCHEMA TO CFB_TEST_OWNER"
    )


def test_script_grant_real_namespace() -> None:
    step = grants.script_grant_step("CFBD_API", "CFB_INFRA.CFBD_FETCH", "CFB_TEST_OWNER")
    assert step.sql == (
        "GRANT ACCESS ON CONNECTION CFBD_API FOR SCRIPT CFB_INFRA.CFBD_FETCH TO CFB_TEST_OWNER"
    )


def test_script_grant_test_namespace() -> None:
    step = grants.script_grant_step("CFBD_API_MOCK", TEST.fetch_script, TEST.owner_user)
    assert step.sql == (
        "GRANT ACCESS ON CONNECTION CFBD_API_MOCK FOR SCRIPT TEST_CFB_INFRA.CFBD_FETCH "
        "TO CFB_TEST_OWNER"
    )


@pytest.mark.parametrize("bad", ["cfb_owner", "CFB OWNER", "X; DROP USER SYS", "", "A.B", '"X"'])
def test_unsafe_identifiers_are_rejected(bad: str) -> None:
    with pytest.raises(GrantsError, match="identifier"):
        grants.owner_privileges_step(bad)
    with pytest.raises(GrantsError, match="identifier"):
        grants.script_grant_step(bad, "CFB_INFRA.CFBD_FETCH", "CFB_TEST_OWNER")
    with pytest.raises(GrantsError, match="identifier"):
        grants.script_grant_step("CFBD_API", "CFB_INFRA.CFBD_FETCH", bad)


def test_script_name_must_be_schema_qualified() -> None:
    with pytest.raises(GrantsError, match="schema-qualified"):
        grants.script_grant_step("CFBD_API", "CFBD_FETCH", "CFB_TEST_OWNER")


def test_test_namespace_must_not_use_the_real_connection() -> None:
    with pytest.raises(GrantsError, match="real connection"):
        grants.resolve_connection(TEST, "CFBD_API")


def test_connection_defaults_follow_the_namespace() -> None:
    assert grants.resolve_connection(REAL, None) == "CFBD_API"
    assert grants.resolve_connection(TEST, None) == "CFBD_API_MOCK"
    assert grants.resolve_connection(TEST, "MY_OTHER_MOCK") == "MY_OTHER_MOCK"


def test_reader_role_steps() -> None:
    assert grants.create_role_step("CFB_READER").sql == "CREATE ROLE CFB_READER"
    assert grants.select_grant_step("CFBD_VS", "CFB_READER").sql == (
        "GRANT SELECT ON CFBD_VS TO CFB_READER"
    )


@pytest.mark.parametrize("bad", ["cfb_reader", "X; DROP USER SYS", "", '"X"'])
def test_reader_role_steps_reject_unsafe_identifiers(bad: str) -> None:
    with pytest.raises(GrantsError, match="identifier"):
        grants.create_role_step(bad)
    with pytest.raises(GrantsError, match="identifier"):
        grants.select_grant_step("CFBD_VS", bad)
    with pytest.raises(GrantsError, match="identifier"):
        grants.select_grant_step(bad, "CFB_READER")


# --- orchestration against a fake database: the real namespace (sys owns everything, [20]) ---


def test_real_first_run_creates_the_reader_role_and_defers_the_select_grant_until_deploy() -> None:
    db = FakeDatabase()
    report = grants.apply(db, REAL, "CFBD_API")
    assert db.statements == ["CREATE ROLE CFB_READER", "CREATE ROLE CFB_APP", "CREATE ROLE CFB_MCP"]
    assert report.script_grant == "not needed"
    assert report.reader_grant == "pending"
    assert report.exit_code == grants.EXIT_OK
    assert any("make deploy" in line for line in report.lines)


def test_real_deployed_virtual_schema_gets_select_for_the_role() -> None:
    db = FakeDatabase(roles={"CFB_READER"}, schemas={"CFBD_VS"})
    report = grants.apply(db, REAL, "CFBD_API")
    assert db.statements == [
        "GRANT SELECT ON CFBD_VS TO CFB_READER",
        "CREATE ROLE CFB_APP",
        "CREATE ROLE CFB_MCP",
    ]
    assert report.reader_grant == "granted"


def test_real_rerun_is_idempotent() -> None:
    db = FakeDatabase(
        roles={"CFB_READER", "CFB_APP", "CFB_MCP"},
        schemas={"CFBD_VS"},
        select_grants={("CFBD_VS", "CFB_READER")},
    )
    report = grants.apply(db, REAL, "CFBD_API")
    assert db.executed == []
    assert report.reader_grant == "already granted"
    assert report.exit_code == grants.EXIT_OK


def test_real_run_needs_no_password_no_user_and_no_connection() -> None:
    db = FakeDatabase(schemas={"CFBD_VS"}, scripts={"CFB_INFRA.CFBD_FETCH"})
    report = grants.apply(db, REAL, "CFBD_API")
    joined = "\n".join(db.statements).upper()
    assert "CREATE USER" not in joined
    assert "FOR SCRIPT" not in joined
    assert "CFB_OWNER" not in joined
    assert all(not params for _, params in db.executed)
    assert report.exit_code == grants.EXIT_OK


def test_real_run_leaves_the_connection_alone_even_when_it_is_missing() -> None:
    db = FakeDatabase(schemas={"CFBD_VS"}, scripts={"CFB_INFRA.CFBD_FETCH"})
    report = grants.apply(db, REAL, "CFBD_API")
    assert report.exit_code == grants.EXIT_OK
    assert not any("CONNECTION" in sql.upper() for sql in db.statements)


def test_real_run_never_grants_access_any_connection() -> None:
    databases = [
        FakeDatabase(),
        FakeDatabase(roles={"CFB_READER"}, schemas={"CFBD_VS"}, connections={"CFBD_API"}),
    ]
    for db in databases:
        grants.apply(db, REAL, "CFBD_API")
        assert "ANY CONNECTION" not in "\n".join(db.statements).upper()


def test_serving_roles_separate_live_access_from_chat() -> None:
    db = FakeDatabase(
        roles={"CFB_READER", "CFB_APP", "CFB_MCP"},
        schemas={"CFBD_VS", "CFB_API", "CFB_API_LIVE"},
        select_grants={("CFBD_VS", "CFB_READER")},
    )
    grants.apply(db, REAL, "CFBD_API")
    assert db.statements == [
        "GRANT SELECT ON SCHEMA CFB_API TO CFB_APP",
        "GRANT SELECT ON SCHEMA CFB_API TO CFB_MCP",
        "GRANT SELECT ON SCHEMA CFB_API_LIVE TO CFB_APP",
    ]


# --- orchestration against a fake database: the TEST_ namespace keeps its owner model ---


def test_test_first_run_creates_the_owner_and_defers_the_script_grant_until_deploy() -> None:
    db = FakeDatabase(connections={"CFBD_API_MOCK"})
    report = grants.apply(db, TEST, "CFBD_API_MOCK", PASSWORD)
    assert db.statements == [
        "CREATE USER CFB_TEST_OWNER IDENTIFIED BY {password!q}",
        "GRANT CREATE SESSION, CREATE SCHEMA, CREATE TABLE, CREATE VIEW, CREATE SCRIPT, "
        "CREATE FUNCTION, CREATE VIRTUAL SCHEMA TO CFB_TEST_OWNER",
        "CREATE ROLE TEST_CFB_APP",
        "CREATE ROLE TEST_CFB_MCP",
    ]
    assert db.executed[0][1] == {"password": PASSWORD}
    assert report.script_grant == "pending"
    assert report.exit_code == grants.EXIT_OK
    assert any("make deploy" in line for line in report.lines)


def test_test_deployed_script_gets_the_script_bound_grant() -> None:
    db = FakeDatabase(
        users={"CFB_TEST_OWNER"},
        scripts={"TEST_CFB_INFRA.CFBD_FETCH"},
        connections={"CFBD_API_MOCK"},
    )
    report = grants.apply(db, TEST, "CFBD_API_MOCK")
    assert (
        "GRANT ACCESS ON CONNECTION CFBD_API_MOCK FOR SCRIPT TEST_CFB_INFRA.CFBD_FETCH "
        "TO CFB_TEST_OWNER"
    ) in db.statements
    assert report.script_grant == "granted"
    assert report.exit_code == grants.EXIT_OK


def test_test_existing_owner_keeps_its_password_and_needs_none() -> None:
    db = FakeDatabase(users={"CFB_TEST_OWNER"})
    grants.apply(db, TEST, "CFBD_API_MOCK")
    assert not any(sql.startswith("CREATE USER") for sql in db.statements)
    assert all(not params for _, params in db.executed)


def test_test_rerun_after_replace_regrants_and_a_second_rerun_is_a_no_op_for_the_grant() -> None:
    script = "TEST_CFB_INFRA.CFBD_FETCH"
    db = FakeDatabase(
        users={"CFB_TEST_OWNER"},
        scripts={script},
        connections={"CFBD_API_MOCK"},
        script_grants={("CFBD_API_MOCK", script, "CFB_TEST_OWNER")},
    )
    report = grants.apply(db, TEST, "CFBD_API_MOCK")
    assert report.script_grant == "already granted"
    assert not any("FOR SCRIPT" in sql for sql in db.statements)
    assert report.exit_code == grants.EXIT_OK


def test_test_missing_password_for_a_new_owner_is_an_error_naming_the_variable() -> None:
    db = FakeDatabase()
    with pytest.raises(GrantsError, match="CFB_TEST_OWNER_PASSWORD"):
        grants.apply(db, TEST, "CFBD_API_MOCK", None)
    assert db.executed == []


def test_test_missing_connection_fails_after_the_script_exists() -> None:
    db = FakeDatabase(users={"CFB_TEST_OWNER"}, scripts={"TEST_CFB_INFRA.CFBD_FETCH"})
    report = grants.apply(db, TEST, "CFBD_API_MOCK")
    assert report.script_grant == "connection missing"
    assert report.exit_code == grants.EXIT_ERROR
    assert any("docs/user-guide.md" in line for line in report.lines)
    assert not any("FOR SCRIPT" in sql for sql in db.statements)


def test_test_namespace_targets_test_objects_only() -> None:
    db = FakeDatabase(scripts={"TEST_CFB_INFRA.CFBD_FETCH"}, connections={"CFBD_API_MOCK"})
    grants.apply(db, TEST, "CFBD_API_MOCK", PASSWORD)
    joined = "\n".join(db.statements)
    assert "CFB_TEST_OWNER" in joined
    assert "TEST_CFB_INFRA.CFBD_FETCH" in joined
    assert not re.search(r"\bCFBD_API\b", joined)
    assert "CFB_READER" not in joined
    assert "CFB_OWNER" not in joined.replace("CFB_TEST_OWNER", "")


def test_test_run_never_grants_access_any_connection() -> None:
    databases = [
        FakeDatabase(),
        FakeDatabase(
            users={"CFB_TEST_OWNER"},
            scripts={"TEST_CFB_INFRA.CFBD_FETCH"},
            connections={"CFBD_API_MOCK"},
        ),
    ]
    for db in databases:
        grants.apply(db, TEST, "CFBD_API_MOCK", PASSWORD)
        assert "ANY CONNECTION" not in "\n".join(db.statements).upper()


def test_report_never_contains_the_password() -> None:
    db = FakeDatabase()
    report = grants.apply(db, TEST, "CFBD_API_MOCK", PASSWORD)
    assert PASSWORD not in "\n".join(report.lines)


# --- environment and credentials ---


def test_owner_password_prefers_the_process_environment_over_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CFB_TEST_OWNER_PASSWORD=from-file\n")
    assert grants.owner_password({"CFB_TEST_OWNER_PASSWORD": "from-env"}, env_file) == "from-env"
    assert grants.owner_password({}, env_file) == "from-file"
    assert grants.owner_password({}, tmp_path / "none") is None


# --- the SQL behind the reader-role checks (verified against Personal 2026.2) ---


class RecordingConnection:
    def __init__(self, count: int) -> None:
        self.count = count
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> "RecordingConnection":
        self.calls.append((sql, params))
        return self

    def fetchone(self) -> tuple[int]:
        return (self.count,)


def test_role_and_schema_checks_read_the_dba_views_with_bound_names() -> None:
    connection = RecordingConnection(1)
    db = grants.PyexasolDatabase(connection)
    assert db.role_exists("CFB_READER") is True
    assert db.schema_exists("CFBD_VS") is True
    (role_sql, role_params), (schema_sql, schema_params) = connection.calls
    assert "EXA_DBA_ROLES" in role_sql
    assert role_params == {"n": "CFB_READER"}
    assert "EXA_DBA_VIRTUAL_SCHEMAS" in schema_sql
    assert schema_params == {"n": "CFBD_VS"}


def test_select_grant_check_looks_for_a_schema_level_select_privilege() -> None:
    connection = RecordingConnection(0)
    db = grants.PyexasolDatabase(connection)
    assert db.select_grant_exists("CFBD_VS", "CFB_READER") is False
    sql, params = connection.calls[0]
    assert "EXA_DBA_OBJ_PRIVS" in sql
    assert "OBJECT_TYPE = 'SCHEMA'" in sql
    assert "PRIVILEGE = 'SELECT'" in sql
    assert params == {"s": "CFBD_VS", "g": "CFB_READER"}
