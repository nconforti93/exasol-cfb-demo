"""Pure and fake-connection parts of `tests/it/harness.py`: cleanup scope and secret handling."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from loader import deploy as loader_deploy
from tests.it import guards, harness, loader_support, support
from tests.it.support import HarnessError
from vs.host import target
from vs.host.deploy import DeployError
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


def test_dropping_namespace_removes_serving_schemas_and_roles() -> None:
    connection = FakeConnection()
    assert harness.drop_namespace(connection, TEST) == []
    statements = connection.statements
    assert "DROP SCHEMA IF EXISTS TEST_CFB_API CASCADE" in statements
    assert "DROP SCHEMA IF EXISTS TEST_CFB_API_LIVE CASCADE" in statements
    assert "DROP ROLE IF EXISTS TEST_CFB_APP CASCADE" in statements
    assert "DROP ROLE IF EXISTS TEST_CFB_MCP CASCADE" in statements


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


def test_leftovers_reports_serving_role() -> None:
    connection = FakeConnection({"EXA_DBA_ROLES": [("TEST_CFB_APP",)]})
    assert harness.leftovers(connection, TEST) == ["role TEST_CFB_APP"]


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


def test_dropping_the_namespace_covers_the_stage_and_restore_schemas() -> None:
    connection = FakeConnection()
    harness.drop_namespace(connection, TEST)
    assert "DROP SCHEMA IF EXISTS TEST_CFB_STAGE CASCADE" in connection.statements
    assert "DROP SCHEMA IF EXISTS TEST_CFB_RESTORE CASCADE" in connection.statements


def test_leftovers_look_for_the_stage_and_restore_schemas() -> None:
    connection = FakeConnection({"EXA_ALL_SCHEMAS": [("TEST_CFB_STAGE",)]})
    assert harness.leftovers(connection, TEST) == ["schema TEST_CFB_STAGE"]
    listing = next(s for s in connection.statements if "FROM EXA_ALL_SCHEMAS" in s)
    assert "'TEST_CFB_STAGE'" in listing
    assert "'TEST_CFB_RESTORE'" in listing


class Statement:
    def __init__(self, names: list[str], rows: list[tuple[Any, ...]]) -> None:
        self._names = names
        self._rows = rows

    def columns(self) -> list[str]:
        return self._names

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class Session:
    """Records statements; answers those containing a marker with `(columns, rows)`."""

    def __init__(
        self, answers: Mapping[str, tuple[list[str], list[tuple[Any, ...]]]] | None = None
    ):
        self.answers = dict(answers or {})
        self.statements: list[str] = []
        self.closed = False

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> Statement:
        self.statements.append(_rendered(sql, params))
        for marker, (names, rows) in self.answers.items():
            if marker in sql:
                return Statement(names, rows)
        return Statement([], [])

    def close(self) -> None:
        self.closed = True

    def seen(self, text: str) -> list[str]:
        return [statement for statement in self.statements if text in statement]


def _rendered(sql: str, params: Mapping[str, Any] | None) -> str:
    text = sql
    for name, value in (params or {}).items():
        quoted = "NULL" if value is None else repr(value)
        for suffix in ("!r", "!d", ""):
            text = text.replace("{" + name + suffix + "}", quoted)
    return text


class FakeHandle:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def set_info(self, mode: str, **fields: Any) -> dict[str, Any]:
        self.calls.append(("set_info", mode, fields))
        return {"mode": mode}

    def info_state(self) -> dict[str, Any]:
        return {"mode": "static", "data_requests": 0}

    def reset(self) -> None:
        self.calls.append(("reset",))

    def stop(self) -> None:
        self.calls.append(("stop",))


class DeployRecorder:
    def __init__(self) -> None:
        self.options: list[Any] = []
        self.fail = False

    def __call__(self, connection: Any, namespace: Namespace, options: Any) -> str:
        if self.fail:
            raise RuntimeError("deploy broke half way")
        self.options.append(options)
        return "report"


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> DeployRecorder:
    recorder = DeployRecorder()
    monkeypatch.setattr(loader_deploy, "deploy", recorder)
    return recorder


def make_harness(owner: Session | None = None) -> tuple[harness.Harness, Session, FakeHandle]:
    owner = owner or Session()
    handle = FakeHandle()
    local = Target("local", "127.0.0.1", 8563, "sys", Path("/x"), target.TLS_UNVERIFIED_LOCAL)
    built = harness.Harness(
        namespace=TEST,
        deployment=local,
        sys_connection=Session(),
        owner=owner,
        mock=harness.Mock(handle),  # type: ignore[arg-type]
        marker_key="marker",
        replay_area=support.ReplayArea(bucketfs_root=Path("/nonexistent-bucketfs")),
        owner_password="owner-pw",
        reader_password="reader-pw",
    )
    return built, owner, handle


def test_use_plan_deploys_the_variant_once_with_its_overrides(deployed: DeployRecorder) -> None:
    built, _, _ = make_harness()
    built.use_plan("gaps", {"monthly_call_budget": "400"})
    built.use_plan("gaps", {"monthly_call_budget": "400"})
    assert len(deployed.options) == 1
    assert deployed.options[0].config_overrides == {"monthly_call_budget": "400"}
    assert "gaps: [1871]" in deployed.options[0].plan_text
    assert str(built.loader_report) == "report"


def test_the_reset_returns_to_the_module_plan_after_a_test_switched_variants(
    deployed: DeployRecorder,
) -> None:
    built, _, _ = make_harness()
    built.use_plan("refresh")
    built.deploy_plan("gaps")
    assert len(deployed.options) == 2
    assert built.reset_state() == []
    assert len(deployed.options) == 3
    assert "TEAMS" in deployed.options[-1].plan_text
    assert built.plan == loader_support.PlanState("refresh", ())


def test_the_reset_does_not_redeploy_an_unchanged_plan(deployed: DeployRecorder) -> None:
    built, _, _ = make_harness()
    built.use_plan("base")
    assert built.reset_state() == []
    assert len(deployed.options) == 1


def test_an_invalid_plan_raises_before_any_statement_and_keeps_the_state(
    deployed: DeployRecorder,
) -> None:
    built, _, _ = make_harness()
    built.use_plan("base")
    with pytest.raises(DeployError):
        built.deploy_plan_text("version: 1\nentries:\n  - table: NO_SUCH_TABLE\n")
    assert built.plan == loader_support.PlanState("base", ())
    assert len(deployed.options) == 1


def test_a_deploy_that_fails_after_validation_forces_a_redeploy_at_reset(
    deployed: DeployRecorder,
) -> None:
    built, _, _ = make_harness()
    built.use_plan("base")
    deployed.fail = True
    with pytest.raises(RuntimeError):
        built.deploy_plan("gaps")
    assert built.plan is None
    deployed.fail = False
    assert built.reset_state() == []
    assert built.plan == loader_support.PlanState("base", ())


def test_the_test_clock_is_written_to_loader_config(deployed: DeployRecorder) -> None:
    built, owner, _ = make_harness()
    built.set_now("2020-10-20")
    deletes = owner.seen(
        "DELETE FROM TEST_CFB_META.LOADER_CONFIG WHERE SETTING_NAME = 'NOW_OVERRIDE'"
    )
    inserts = owner.seen("INSERT INTO TEST_CFB_META.LOADER_CONFIG")
    assert len(deletes) == 1
    assert "'2020-10-20'" in inserts[0]


def test_the_clock_accepts_a_time_and_none_but_not_text() -> None:
    built, owner, _ = make_harness()
    built.set_now("2021-02-15 06:30:00")
    built.set_now(None)
    assert len(owner.seen("INSERT INTO TEST_CFB_META.LOADER_CONFIG")) == 1
    with pytest.raises(HarnessError, match="YYYY-MM-DD"):
        built.set_now("tomorrow")


def test_the_reset_clears_the_clock_a_test_set(deployed: DeployRecorder) -> None:
    built, owner, _ = make_harness()
    built.use_plan("base")
    built.set_now("2020-10-20")
    owner.statements.clear()
    assert built.reset_state() == []
    assert owner.seen("DELETE FROM TEST_CFB_META.LOADER_CONFIG WHERE SETTING_NAME = 'NOW_OVERRIDE'")
    assert not owner.seen("INSERT INTO TEST_CFB_META.LOADER_CONFIG")
    assert len(deployed.options) == 1


def test_the_reset_restores_the_clock_of_the_module_plan(deployed: DeployRecorder) -> None:
    built, owner, _ = make_harness()
    built.use_plan("base", {"now_override": "2021-01-05"})
    built.set_now("2020-10-20")
    owner.statements.clear()
    assert built.reset_state() == []
    assert "'2021-01-05'" in owner.seen("INSERT INTO TEST_CFB_META.LOADER_CONFIG")[0]


def test_the_reset_truncates_data_tables_but_never_the_plan_tables() -> None:
    dirty = [
        ("TEST_CFB_META", "LOAD_LOG"),
        ("TEST_CFB_META", "LOAD_PLAN"),
        ("TEST_CFB_META", "LOADER_CONFIG"),
        ("TEST_CFB_META", "LOAD_RUN"),
        ("TEST_CFB_STAGE", "GAMES"),
        ("TEST_CFB_INFRA", "TEST_STEP_CALLS"),
    ]
    owner = Session({"FROM EXA_ALL_TABLES": (["TABLE_SCHEMA", "TABLE_NAME"], dirty)})
    built, _, _ = make_harness(owner)
    assert built.reset_state() == []
    truncated = [s.removeprefix("TRUNCATE TABLE ") for s in owner.seen("TRUNCATE TABLE")]
    assert truncated == [
        "TEST_CFB_META.LOAD_LOG",
        "TEST_CFB_META.LOAD_RUN",
        "TEST_CFB_STAGE.GAMES",
        "TEST_CFB_INFRA.TEST_STEP_CALLS",
    ]
    query = owner.seen("FROM EXA_ALL_TABLES")[0]
    assert "'TEST_CFB_STAGE'" in query
    assert "TABLE_ROW_COUNT > 0" in query


def test_a_mock_change_without_the_mock_fixture_is_undone_by_the_reset() -> None:
    built, _, handle = make_harness()
    built.script_info(used=5, limit=10)
    assert built.reset_state() == []
    assert ("reset",) in handle.calls


def test_the_reset_leaves_an_unchanged_mock_alone() -> None:
    built, _, handle = make_harness()
    assert built.reset_state() == []
    assert ("reset",) not in handle.calls


def test_script_info_static_serves_the_fields_as_given() -> None:
    built, _, handle = make_harness()
    built.script_info(used=120, limit=1000, remaining=880, reset_at="2020-11-01T00:00:00Z")
    assert handle.calls == [
        (
            "set_info",
            "static",
            {
                "used": 120,
                "limit": 1000,
                "remaining": 880,
                "tier": None,
                "reset_at": "2020-11-01T00:00:00Z",
            },
        )
    ]


def test_script_info_counting_starts_from_the_used_calls() -> None:
    built, _, handle = make_harness()
    built.script_info(counting=True, used=840, limit=1000)
    assert handle.calls[0][:2] == ("set_info", "counting")
    assert handle.calls[0][2]["base_used"] == 840
    assert "used" not in handle.calls[0][2]


def test_a_mock_change_by_script_info_counts_as_changed() -> None:
    built, _, _ = make_harness()
    built.script_info(used=1)
    assert built.mock.changed


def test_backfill_and_refresh_call_the_scripts_in_the_infra_schema() -> None:
    owner = Session()
    built, _, _ = make_harness(owner)
    assert built.backfill("demo", None, 3) == []
    assert built.backfill() == []
    assert built.refresh(2020, 7, None) == []
    assert built.refresh(2020, 1, "postseason") == []
    assert owner.statements == [
        "EXECUTE SCRIPT TEST_CFB_INFRA.BACKFILL('demo', NULL, 3)",
        "EXECUTE SCRIPT TEST_CFB_INFRA.BACKFILL('demo', NULL, NULL)",
        "EXECUTE SCRIPT TEST_CFB_INFRA.REFRESH_WEEK(2020, 7, NULL)",
        "EXECUTE SCRIPT TEST_CFB_INFRA.REFRESH_WEEK(2020, 1, 'postseason')",
    ]


def test_a_script_result_comes_back_keyed_by_column() -> None:
    owner = Session({"BACKFILL": (["TABLE_NAME", "SLICES"], [("*", 3)])})
    built, _, _ = make_harness(owner)
    assert built.backfill("demo", None, 3) == [{"TABLE_NAME": "*", "SLICES": 3}]


def test_the_quota_is_exactly_one_row() -> None:
    owner = Session({"QUOTA": (["USED_CALLS"], [(120,)])})
    built, _, _ = make_harness(owner)
    assert built.quota() == {"USED_CALLS": 120}
    two_rows = Session({"QUOTA": (["USED_CALLS"], [(1,), (2,)])})
    with pytest.raises(HarnessError, match="exactly one row"):
        make_harness(two_rows)[0].quota()


def test_log_readers_order_their_rows_and_filter_by_run() -> None:
    owner = Session()
    built, _, _ = make_harness(owner)
    built.runs()
    built.steps(7)
    built.steps()
    built.failures()
    built.load_slices("GAMES")
    built.load_status()
    built.backfill_queue()
    assert owner.statements == [
        "SELECT * FROM TEST_CFB_META.LOAD_RUN ORDER BY RUN_ID",
        "SELECT * FROM TEST_CFB_META.REFRESH_LOG WHERE RUN_ID = 7 "
        "ORDER BY RUN_ID, STEP_NO, STARTED_AT",
        "SELECT * FROM TEST_CFB_META.REFRESH_LOG ORDER BY RUN_ID, STEP_NO, STARTED_AT",
        "SELECT * FROM TEST_CFB_META.LOAD_FAILURES ORDER BY TABLE_NAME, SLICE_KEY",
        "SELECT * FROM TEST_CFB_META.LOAD_SLICES WHERE TABLE_NAME = 'GAMES' "
        "ORDER BY ENTRY_ID, SLICE_KEY",
        "SELECT * FROM TEST_CFB_META.LOAD_STATUS ORDER BY ENTRY_ID",
        "SELECT * FROM TEST_CFB_META.BACKFILL_QUEUE ORDER BY QUEUE_ORDER",
    ]


def test_a_slice_is_registered_with_a_utc_timestamp() -> None:
    owner = Session()
    built, _, _ = make_harness(owner)
    built.register_slice("GAMES", "/games?year=2020", 3)
    statement = owner.statements[0]
    assert "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')" in statement
    assert "'IT_HARNESS'" in statement


def test_a_slice_can_be_registered_as_loaded_by_a_run_at_a_given_time() -> None:
    owner = Session()
    built, _, _ = make_harness(owner)
    built.register_slice(
        "GAMES", "/games?year=2020", 3, loaded_by="BACKFILL#7", loaded_at="2020-10-01 12:00:00"
    )
    statement = owner.statements[0]
    assert "CAST('2020-10-01 12:00:00' AS TIMESTAMP)" in statement
    assert "'BACKFILL#7'" in statement
    assert "CURRENT_TIMESTAMP" not in statement


def test_a_failure_is_registered_with_a_utc_timestamp() -> None:
    owner = Session()
    built, _, _ = make_harness(owner)
    built.register_failure("GAMES", "/games?year=2018", 2, "boom", run_id=4)
    statement = owner.statements[0]
    assert "INSERT INTO TEST_CFB_META.LOAD_FAILURES" in statement
    assert "'boom'" in statement
    assert "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')" in statement


def test_a_statement_is_killed_as_sys() -> None:
    built, owner, _ = make_harness()
    built.kill_statement(4711)
    sys_session = built.sys
    assert sys_session.statements == ["KILL STATEMENT IN SESSION 4711"]
    assert not owner.statements


def test_the_session_id_is_read_from_the_owner_connection() -> None:
    owner = Session({"CURRENT_SESSION": (["S"], [(1234567890123,)])})
    built, _, _ = make_harness(owner)
    assert built.session_id() == 1234567890123
