"""`engine/deploy.py` and `scripts/sim.py` (`make sim`) over a fake connection: lint before
anything, the DEPLOY lock first, the data tables kept, the engine script inside the transaction.
The deployer's own rules are in `test_sqlobjects_deploy.py`; the engine is covered by
`tests/it/test_sim_deploy_it.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from engine import bundle
from engine import deploy as sim_deploy
from engine.deploy import SimLintError, SimOptions, deploy_sim, prepare
from loader.runlock import HeldLock, RunLockRefused
from scripts import sim as cli
from tests.unit.test_model_deploy import LockedConnection
from tests.unit.test_sqlobjects_deploy import expected_catalog
from vs.host import target
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = Namespace("TEST_")
LOCK = '"TEST_CFB_INFRA"."RUN_LOCK"'
DATA_TABLES = (
    "CONF_CHAMP",
    "LEVERAGE",
    "SIM_PATH",
    "SIM_RUN",
    "SIM_RUN_GAME",
    "SIM_RUN_PARAM",
    "SIM_RUN_TEAM",
    "SIM_STAGE",
    "SIM_TEAM",
    "TEAM_WINS",
)


@pytest.fixture
def options(tmp_path: Path) -> SimOptions:
    (tmp_path / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "udf.py").write_text("def run(ctx, exa):\n    pass\n", encoding="utf-8")
    return SimOptions(engine_directory=tmp_path)


def connection_for(**changes: Any) -> LockedConnection:
    connection = LockedConnection(**changes)
    connection.catalog, connection.columns = expected_catalog(prepare(NAMESPACE))
    return connection


def test_the_real_files_lint_clean_and_hold_every_slice_one_table() -> None:
    objects = prepare(NAMESPACE)
    names = {o.name for o in objects}
    assert set(DATA_TABLES) <= names
    assert {"SIM_PARAMS", "SEASON_RULES", "SCORE_PARAMS"} <= names
    assert [o.name for o in objects if o.keep_existing] == sorted(DATA_TABLES)
    assert {o.schema for o in objects} == {"TEST_CFB_SIM"}


def test_the_data_tables_come_first_and_the_keep_list_names_them() -> None:
    objects = prepare(NAMESPACE)
    assert [o.keep_existing for o in objects][: len(DATA_TABLES)] == [True] * len(DATA_TABLES)
    assert set(DATA_TABLES) <= set(sim_deploy.KEPT_TABLES)
    assert "PREDICTION" in sim_deploy.KEPT_TABLES


def test_the_lock_is_taken_first_and_released_done_with_the_summary_last(
    options: SimOptions,
) -> None:
    connection = connection_for()
    report = deploy_sim(connection, NAMESPACE, options)
    statements = connection.statements
    assert statements[0].startswith("SELECT COUNT(*) FROM EXA_ALL_SCRIPTS")
    assert statements[1] == f"EXECUTE SCRIPT {LOCK}('acquire', 'DEPLOY', NULL, 'make sim')"
    assert statements.index("COMMIT") < len(statements) - 1
    release = statements[-1]
    assert release.startswith(f"EXECUTE SCRIPT {LOCK}('release', 'DONE', 42, 'TEST_CFB_SIM: ")
    assert f"engine {report.engine_version}'" in release
    assert report.run_id == 42


def test_the_engine_script_and_its_comment_run_inside_the_transaction_after_the_objects(
    options: SimOptions,
) -> None:
    connection = connection_for()
    report = deploy_sim(connection, NAMESPACE, options)
    statements = connection.statements
    script = next(
        i for i, s in enumerate(statements) if "CREATE OR REPLACE PYTHON3 SET SCRIPT" in s
    )
    comment = next(i for i, s in enumerate(statements) if s.startswith("COMMENT ON SCRIPT"))
    assert comment == script + 1
    assert '"TEST_CFB_INFRA"."SIM_ENGINE"' in statements[script]
    assert f"ENGINE_VERSION {report.engine_version}" in statements[comment]
    simulate = next(i for i, s in enumerate(statements) if "CREATE OR REPLACE LUA SCRIPT" in s)
    assert "TEST_CFB_INFRA.SIMULATE" in statements[simulate]
    objects = [
        i
        for i, s in enumerate(statements)
        if s.startswith(("CREATE OR REPLACE", "CREATE TABLE")) and i not in (script, simulate)
    ]
    assert max(objects) < script < simulate < statements.index("COMMIT")
    assert any("script TEST_CFB_INFRA.SIM_ENGINE: replaced" in line for line in report.lines)
    assert any("script TEST_CFB_INFRA.SIMULATE: replaced" in line for line in report.lines)


def test_nothing_is_dropped_or_emptied_and_the_data_tables_are_created_if_missing(
    options: SimOptions,
) -> None:
    connection = connection_for()
    deploy_sim(connection, NAMESPACE, options)
    created = [s for s in connection.statements if s.startswith("CREATE TABLE IF NOT EXISTS")]
    assert len(created) == len(DATA_TABLES)
    assert all(s.split()[5].startswith("TEST_CFB_SIM.") for s in created)
    assert not any(s.startswith(("DROP", "TRUNCATE", "DELETE")) for s in connection.statements)


def test_a_data_table_that_exists_is_kept_and_reported(options: SimOptions) -> None:
    connection = connection_for()
    report = deploy_sim(connection, NAMESPACE, options)
    assert set(report.objects.kept) == set(DATA_TABLES)
    assert report.objects.differences == ()
    assert any(line.startswith("data tables: 10 kept as they were") for line in report.lines)


def test_under_a_lock_the_caller_holds_no_lock_statement_is_sent(options: SimOptions) -> None:
    connection = connection_for()
    held = HeldLock(31, "DEPLOY")
    report = deploy_sim(connection, NAMESPACE, options, lock=held)
    assert not [s for s in connection.statements if f"EXECUTE SCRIPT {LOCK}" in s]
    assert report.run_id == 31
    assert held.detail is not None and held.detail.endswith(f"engine {report.engine_version}")
    assert report.lines[-1] == "simulation schema deployed as DEPLOY run 31; 0 CFBD calls"


def test_a_lint_problem_stops_before_any_statement(tmp_path: Path, options: SimOptions) -> None:
    (tmp_path / "100_bad.sql").write_text(
        "CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.BAD (X COMMENT IS 'x') AS\n"
        "SELECT 1 FROM {CORE_SCHEMA}.GAMES\nCOMMENT IS 'One row per bad.';\n",
        encoding="utf-8",
    )
    connection = LockedConnection()
    bad = SimOptions(directory=tmp_path, engine_directory=options.engine_directory)
    with pytest.raises(SimLintError) as raised:
        deploy_sim(connection, NAMESPACE, bad)
    assert connection.statements == []
    assert "nothing was sent" in str(raised.value)
    assert any("reference" in problem for problem in raised.value.problems)


def test_a_refused_lock_changes_nothing(options: SimOptions) -> None:
    connection = connection_for(refuse=True)
    with pytest.raises(RunLockRefused, match="BACKFILL run 7"):
        deploy_sim(connection, NAMESPACE, options)
    assert not any(s.startswith("CREATE") for s in connection.statements)


def test_a_database_error_rolls_back_and_names_the_statement(options: SimOptions) -> None:
    connection = connection_for()
    connection.fail["CREATE OR REPLACE PYTHON3"] = "syntax error"
    with pytest.raises(DeployError, match="extra statement 1: syntax error"):
        deploy_sim(connection, NAMESPACE, options)
    assert "COMMIT" not in connection.statements
    assert "ROLLBACK" in connection.statements


def test_a_missing_engine_source_is_a_deploy_error(tmp_path: Path) -> None:
    with pytest.raises(DeployError, match=r"core\.py"):
        deploy_sim(LockedConnection(), NAMESPACE, SimOptions(engine_directory=tmp_path))


# --- the command line --------------------------------------------------------------------------


def run_cli(connection: LockedConnection | None, options: SimOptions, *argv: str) -> int:
    def resolve(name: str | None = None, *, dsn: str | None = None) -> target.Target:
        return target.Target(
            name or "local", "127.0.0.1", 8563, "sys", Path("/x"), target.TLS_UNVERIFIED_LOCAL
        )

    def connector(_: target.Target) -> LockedConnection:
        assert connection is not None, "no connection expected"
        return connection

    return cli.main(list(argv), connector=connector, resolve=resolve, options=options)


def test_deploy_prints_the_report_and_closes_the_connection(
    options: SimOptions, capsys: pytest.CaptureFixture[str]
) -> None:
    connection = connection_for()
    assert run_cli(connection, options, "deploy", "--prefix", "TEST_") == cli.EXIT_OK
    assert connection.closed
    out = capsys.readouterr().out
    assert "simulation schema deployed as DEPLOY run 42; 0 CFBD calls" in out
    assert "schema TEST_CFB_SIM: ready" in out


def test_lint_only_sends_nothing_and_opens_no_connection(
    options: SimOptions, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(None, options, "deploy", "--lint-only") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("lint ok: 20 objects, engine script ")
    assert "nothing sent, 0 CFBD calls" in out


def test_a_held_lock_exits_one(options: SimOptions, capsys: pytest.CaptureFixture[str]) -> None:
    connection = connection_for(refuse=True)
    assert run_cli(connection, options, "deploy", "--prefix", "TEST_") == cli.EXIT_REFUSED
    assert "refused: DEPLOY refused: BACKFILL run 7" in capsys.readouterr().err
    assert connection.closed


def test_a_lint_problem_exits_two_and_lists_it(
    tmp_path: Path, options: SimOptions, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "100_bad.sql").write_text("SELECT 1;\n", encoding="utf-8")
    bad = SimOptions(directory=tmp_path, engine_directory=options.engine_directory)
    assert run_cli(None, bad, "deploy", "--lint-only") == cli.EXIT_ERROR
    assert "nothing was sent" in capsys.readouterr().err


def test_a_database_error_exits_two_with_its_first_line(
    options: SimOptions, capsys: pytest.CaptureFixture[str]
) -> None:
    connection = connection_for()
    connection.fail["CREATE SCHEMA"] = "insufficient privileges\nsecond line"
    assert run_cli(connection, options, "deploy", "--prefix", "TEST_") == cli.EXIT_ERROR
    assert "insufficient privileges" in capsys.readouterr().err


def test_make_sim_runs_the_deploy_command() -> None:
    result = subprocess.run(
        ["make", "-n", "sim", "TARGET=aws", "ARGS=--prefix TEST_"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "-m scripts.sim deploy --prefix TEST_" in result.stdout
    assert "scripts.dbcheck" in result.stdout


def test_the_engine_directory_default_is_the_real_one() -> None:
    assert SimOptions().engine_directory == bundle.ENGINE_DIR
