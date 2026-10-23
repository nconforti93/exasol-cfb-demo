"""`model/deploy.py` over a fake connection: lint before anything, the DEPLOY lock first, then the
transaction. The deployer's own rules are in `test_sqlobjects_deploy.py`; the engine is covered
by `tests/it/test_model_deploy_it.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from loader.runlock import RunLockError, RunLockRefused
from model import deploy as model_deploy
from model.deploy import ModelLintError, ModelOptions, deploy_model, prepare
from tests.unit.test_sqlobjects_deploy import FakeConnection as DeployConnection
from tests.unit.test_sqlobjects_deploy import FakeQueryError, expected_catalog
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace

NAMESPACE = Namespace("TEST_")
FIXTURES = Path(__file__).parent / "fixtures" / "sqlobjects"
SCRIPT = '"TEST_CFB_INFRA"."RUN_LOCK"'
REFUSAL = (
    "DEPLOY refused: BACKFILL run 7 is running (session 123, started 2026-10-22 10:00:00 UTC). "
    "Only one loader run at a time: wait until it ends."
)


class LockedConnection(DeployConnection):
    """The deploy fake plus the `RUN_LOCK` script of the loader (acquire, release)."""

    def __init__(
        self, *, installed: bool = True, refuse: bool = False, prefix: str = "TEST_"
    ) -> None:
        super().__init__()
        self.script = f'"{prefix}CFB_INFRA"."RUN_LOCK"'
        self.installed = installed
        self.refuse = refuse
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def execute(self, query: str) -> Any:
        if query.startswith("SELECT COUNT(*) FROM EXA_ALL_SCRIPTS"):
            self.statements.append(query)
            return _Rows([(1 if self.installed else 0,)])
        if query.startswith(f"EXECUTE SCRIPT {self.script}('acquire'"):
            self.statements.append(query)
            if self.refuse:
                raise FakeQueryError(REFUSAL + f" caught in script {SCRIPT} at line 16")
            return _Rows([(42,)])
        if query.startswith(f"EXECUTE SCRIPT {self.script}('release'"):
            self.statements.append(query)
            return _Rows([(1,)])
        return super().execute(query)


class _Rows:
    def __init__(self, rows: Sequence[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


def valid_options(**changes: Any) -> ModelOptions:
    return ModelOptions(directory=FIXTURES / "valid", **changes)


def connection_for(directory: Path) -> LockedConnection:
    connection = LockedConnection()
    connection.catalog, connection.columns = expected_catalog(prepare(NAMESPACE, directory))
    return connection


def test_the_lock_is_taken_first_and_released_done_with_the_summary_last() -> None:
    connection = connection_for(FIXTURES / "valid")
    report = deploy_model(connection, NAMESPACE, valid_options())
    statements = connection.statements
    assert statements[0].startswith("SELECT COUNT(*) FROM EXA_ALL_SCRIPTS")
    assert statements[1] == f"EXECUTE SCRIPT {SCRIPT}('acquire', 'DEPLOY', NULL, 'make model')"
    assert statements.index("COMMIT") < len(statements) - 1
    assert statements[-1] == (
        f"EXECUTE SCRIPT {SCRIPT}('release', 'DONE', 42, "
        "'TEST_CFB_MODEL: 1 tables, 2 views, 1 functions; dropped 0; comments 100 %')"
    )
    assert report.run_id == 42


def test_the_transaction_starts_only_after_the_lock_is_held() -> None:
    connection = connection_for(FIXTURES / "valid")
    deploy_model(connection, NAMESPACE, valid_options())
    statements = connection.statements
    assert statements.index("CREATE SCHEMA IF NOT EXISTS TEST_CFB_MODEL") > 1
    assert connection.autocommit_calls == [False, True]


def test_the_objects_are_rendered_for_the_namespace() -> None:
    connection = connection_for(FIXTURES / "valid")
    deploy_model(connection, NAMESPACE, valid_options())
    created = [s for s in connection.statements if s.startswith("CREATE OR REPLACE FORCE VIEW")]
    assert created[0].startswith("CREATE OR REPLACE FORCE VIEW TEST_CFB_MODEL.GAME")
    assert 'FROM TEST_CFB_CORE."GAMES"' in created[0]
    assert not any("{" in s for s in connection.statements if s.startswith("CREATE"))


def test_a_lint_problem_stops_before_any_statement_and_lists_every_problem() -> None:
    connection = LockedConnection()
    with pytest.raises(ModelLintError) as raised:
        deploy_model(connection, NAMESPACE, ModelOptions(directory=FIXTURES / "broken"))
    assert connection.statements == []
    assert len(raised.value.problems) >= 5
    assert "nothing was sent" in str(raised.value)
    assert all(problem in str(raised.value) for problem in raised.value.problems)


def test_an_empty_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match=r"no \.sql files"):
        deploy_model(LockedConnection(), NAMESPACE, ModelOptions(directory=tmp_path))


def test_a_held_lock_refuses_the_deploy_and_nothing_else_is_sent() -> None:
    connection = LockedConnection(refuse=True)
    with pytest.raises(RunLockRefused, match="BACKFILL run 7"):
        deploy_model(connection, NAMESPACE, valid_options())
    assert not any(s.startswith("CREATE") for s in connection.statements)
    assert connection.autocommit_calls == []


def test_a_missing_loader_is_reported_before_the_lock_or_any_ddl() -> None:
    connection = LockedConnection(installed=False)
    with pytest.raises(RunLockError, match="make deploy"):
        deploy_model(connection, NAMESPACE, valid_options())
    assert not any(s.startswith(("CREATE", "EXECUTE")) for s in connection.statements)


def test_a_failing_statement_rolls_back_then_marks_the_run_failed() -> None:
    connection = connection_for(FIXTURES / "valid")
    connection.fail["CREATE OR REPLACE FORCE VIEW TEST_CFB_MODEL.SEASON"] = "boom"
    with pytest.raises(DeployError, match="boom"):
        deploy_model(connection, NAMESPACE, valid_options())
    statements = connection.statements
    assert "COMMIT" not in statements
    assert statements.count("ROLLBACK") >= 1
    assert statements[-1].startswith(f"EXECUTE SCRIPT {SCRIPT}('release', 'FAILED', 42, ")


def test_a_view_that_does_not_compile_fails_the_deploy_and_the_lock_run() -> None:
    connection = connection_for(FIXTURES / "valid")
    connection.fail['SELECT 1 FROM "TEST_CFB_MODEL"."GAME"'] = "view GAME is invalid"
    with pytest.raises(DeployError, match="view GAME is invalid"):
        deploy_model(connection, NAMESPACE, valid_options())
    assert "COMMIT" not in connection.statements
    assert "'FAILED'" in connection.statements[-1]


def test_a_missing_comment_after_the_deploy_fails_it() -> None:
    connection = connection_for(FIXTURES / "valid")
    connection.catalog = [(n, k, None) for n, k, _ in connection.catalog]
    with pytest.raises(DeployError, match="comment"):
        deploy_model(connection, NAMESPACE, valid_options())
    assert "COMMIT" not in connection.statements


def test_orphans_of_the_model_schema_are_dropped() -> None:
    connection = connection_for(FIXTURES / "valid")
    connection.catalog.append(("OLD_VIEW", "VIEW", "One row per old"))
    report = deploy_model(connection, NAMESPACE, valid_options())
    assert 'DROP VIEW "TEST_CFB_MODEL"."OLD_VIEW"' in connection.statements
    assert report.objects.dropped == ("VIEW OLD_VIEW",)


def test_a_step_script_is_installed_in_the_same_transaction() -> None:
    connection = connection_for(FIXTURES / "valid")
    script = "CREATE OR REPLACE LUA SCRIPT TEST_CFB_INFRA.REFRESH_MODEL(run_id) AS return 1"
    report = deploy_model(
        connection, NAMESPACE, valid_options(step_script=lambda namespace: script)
    )
    assert report.step_installed
    assert connection.statements.index(script) < connection.statements.index("COMMIT")
    assert "Lua script TEST_CFB_INFRA.REFRESH_MODEL: replaced" in report.lines


def test_without_a_step_source_the_report_says_the_script_is_skipped() -> None:
    connection = connection_for(FIXTURES / "valid")
    report = deploy_model(connection, NAMESPACE, valid_options())
    assert not report.step_installed
    assert any("REFRESH_MODEL: skipped" in line for line in report.lines)
    assert report.lines[-1].endswith("0 CFBD calls")


def test_the_default_step_script_refuses_an_unbundled_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "refresh_model.lua"
    source.write_text("return 1\n")
    monkeypatch.setattr(model_deploy, "STEP_SOURCE", source)
    with pytest.raises(DeployError, match="not bundled"):
        model_deploy.default_step_script(NAMESPACE)


def test_the_real_model_files_are_lint_clean_for_every_namespace() -> None:
    for prefix in ("", "TEST_", "PERF_"):
        objects = prepare(Namespace(prefix))
        assert objects
        assert {o.schema for o in objects} == {f"{prefix}CFB_MODEL"}
