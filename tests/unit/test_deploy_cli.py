from collections.abc import Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Never

import pytest

from engine import deploy as sim_deploy
from loader import target_config
from loader.runlock import HeldLock, RunLockRefused
from model import deploy as model_deploy
from model.deploy import ModelLintError, ModelReport
from scripts import deploy as deploy_cli
from tests.unit.test_deploy import Result
from tests.unit.test_loader_deploy import LoaderDatabase
from vs import bundle
from vs.host import target
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace

SIZES = bundle.BundleSizes(1_534_511, 1_434_615)
MODEL_LINE = "model deployed as DEPLOY run 42; 0 CFBD calls"
OCTOBER = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
NOVEMBER = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
DECEMBER = datetime(2026, 12, 1, 0, 0, tzinfo=UTC)
CAPS = {"2026-10": 400, "2026-11": 1000}
AWS_CONFIG = target_config.parse(
    "targets:\n  aws:\n    monthly_call_budget: ledger\n    refresh_reserve: 50\n"
)
LOCK = '"PROBE_CFB_INFRA"."RUN_LOCK"'
HOLDER = (
    "DEPLOY refused: BACKFILL run 5 is running (session 77, started 2026-10-23 10:00:00 UTC). "
    'Only one loader run at a time caught in script "PROBE_CFB_INFRA"."RUN_LOCK" at line 17'
)


class ClosableDatabase(LoaderDatabase):
    closed = False

    def __init__(self, *, holder: str | None = None) -> None:
        super().__init__(foundation=False)
        self.holder = holder
        self.lost = False

    def close(self) -> None:
        self.closed = True

    def execute(self, query: str) -> Result:
        if query.startswith("SELECT COUNT(*) FROM EXA_ALL_SCRIPTS"):
            installed = ("PROBE_CFB_INFRA", "RUN_LOCK") in self.scripts
            self.log.append(query[:90])
            return Result([(int(installed),)])
        if query.startswith(f"EXECUTE SCRIPT {LOCK}('acquire'"):
            self.log.append(query)
            if self.holder:
                raise RuntimeError(self.holder)
            return Result([(31,)])
        if query.startswith(f"EXECUTE SCRIPT {LOCK}('release'"):
            self.log.append(query)
            return Result([(0 if self.lost else 1,)])
        return super().execute(query)


def aws(name: str | None = None, **_: Any) -> target.Target:
    return target.Target(name or "local", "db", 8563, "sys", Path("/nowhere"), target.TLS_PINNED)


def deploy_main(
    db: ClosableDatabase, *argv: str, clock: datetime = OCTOBER, caps: Mapping[str, int] = CAPS
) -> int:
    return deploy_cli.main(
        ["--prefix", "PROBE_", "--no-sync-api-features", *argv],
        connector=lambda _: db,
        write_bundle=written,
        resolve=aws,
        clock=lambda: clock,
        month_caps=lambda: caps,
        target_settings=AWS_CONFIG,
    )


def config_row(db: ClosableDatabase) -> str:
    return next(
        s for s in db.statements if s.startswith("INSERT INTO PROBE_CFB_META.LOADER_CONFIG")
    )


def written() -> bundle.BundleSizes:
    return SIZES


class ModelCalls:
    """Stands in for the model deploy, which needs the run lock script of a real database."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str, HeldLock | None]] = []

    def __call__(
        self, connection: Any, namespace: Namespace, *, lock: HeldLock | None = None
    ) -> ModelReport:
        self.calls.append((connection, namespace.prefix, lock))
        if lock is not None:
            lock.detail = "PROBE_CFB_MODEL: 1 tables, 2 views"
        return ModelReport(None, 42, False, ("model line", MODEL_LINE))  # type: ignore[arg-type]


class SimCalls:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, str, HeldLock | None, str]] = []

    def __call__(
        self,
        connection: Any,
        namespace: Namespace,
        *,
        lock: HeldLock | None = None,
        target_name: str = "local",
    ) -> sim_deploy.SimReport:
        self.calls.append((connection, namespace.prefix, lock, target_name))
        if lock is not None:
            lock.detail = "PROBE_CFB_SIM: 11 tables, 15 views"
        return sim_deploy.SimReport(None, "test", 42, ("sim line",))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def model_calls(monkeypatch: pytest.MonkeyPatch) -> ModelCalls:
    calls = ModelCalls()
    monkeypatch.setattr(model_deploy, "deploy_model", calls)
    return calls


@pytest.fixture(autouse=True)
def sim_calls(monkeypatch: pytest.MonkeyPatch) -> SimCalls:
    calls = SimCalls()
    monkeypatch.setattr(sim_deploy, "deploy_sim", calls)
    return calls


def test_property_arguments_are_split_and_upper_cased() -> None:
    assert deploy_cli.parse_property_arguments(["max_calls_per_query=5", "DOH_URL=off"]) == {
        "MAX_CALLS_PER_QUERY": "5",
        "DOH_URL": "off",
    }


@pytest.mark.parametrize("bad", ["NOVALUE", "=x", ""])
def test_malformed_property_argument_is_rejected(bad: str) -> None:
    with pytest.raises(DeployError, match="NAME=value"):
        deploy_cli.parse_property_arguments([bad])


def test_main_builds_the_bundle_deploys_and_closes_the_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    dsns: list[str] = []

    def connector(deployment: target.Target) -> ClosableDatabase:
        dsns.append(deployment.dsn)
        return db

    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1", "--no-sync-api-features"],
        connector=connector,
        write_bundle=written,
    )
    out = capsys.readouterr().out
    assert code == deploy_cli.EXIT_OK
    assert dsns == ["db:1"]
    assert db.closed
    assert out.startswith("bundle: adapter 1,534,511 and fetch UDF 1,434,615 characters")
    assert "virtual schema PROBE_CFBD_VS: created" in out
    assert "next: " in out
    assert ("PROBE_CFB_INFRA", "CFBD_FETCH") in db.scripts


def test_main_deploys_the_loader_after_the_virtual_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1", "--no-sync-api-features"],
        connector=lambda _: db,
        write_bundle=written,
    )
    out = capsys.readouterr().out
    assert code == deploy_cli.EXIT_OK
    assert out.index("virtual schema PROBE_CFBD_VS") < out.index("stage schema PROBE_CFB_STAGE")
    assert "load plan in PROBE_CFB_META" in out
    assert ("PROBE_CFB_META", "LOAD_PLAN") in db.tables
    assert db.closed


def test_the_model_and_sim_are_deployed_in_order_on_the_same_connection(
    capsys: pytest.CaptureFixture[str], model_calls: ModelCalls, sim_calls: SimCalls
) -> None:
    db = ClosableDatabase()
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1", "--no-sync-api-features"],
        connector=lambda _: db,
        write_bundle=written,
    )
    out = capsys.readouterr().out
    assert code == deploy_cli.EXIT_OK
    assert model_calls.calls == [(db, "PROBE_", None)]
    assert sim_calls.calls == [(db, "PROBE_", None, "local")]
    assert (
        out.index("load plan in PROBE_CFB_META")
        < out.index("model line")
        < out.index(MODEL_LINE)
        < out.index("sim line")
    )
    assert db.closed


def test_a_refused_model_deploy_is_an_error_after_the_earlier_steps_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refused(
        connection: Any, namespace: Namespace, *, lock: HeldLock | None = None
    ) -> ModelReport:
        raise RunLockRefused("DEPLOY refused: BACKFILL run 7 is running")

    db = ClosableDatabase()
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1", "--no-sync-api-features"],
        connector=lambda _: db,
        write_bundle=written,
        deploy_model=refused,
    )
    captured = capsys.readouterr()
    assert code == deploy_cli.EXIT_ERROR
    assert "load plan in PROBE_CFB_META" in captured.out
    assert "deploy failed: DEPLOY refused: BACKFILL run 7 is running" in captured.err
    assert "the virtual schema and the loader were deployed, the model was not" in captured.err
    assert "nothing was deployed" not in captured.err
    assert db.closed


def test_a_model_lint_problem_is_refused_before_anything_is_built_or_sent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(namespace: Namespace) -> Never:
        raise ModelLintError(["100_game.sql: GAME: grain: the comment must start with One row per"])

    def never_connect(deployment: target.Target) -> ClosableDatabase:
        raise AssertionError("must not connect")

    def never_bundle() -> bundle.BundleSizes:
        raise AssertionError("must not build")

    monkeypatch.setattr(model_deploy, "prepare", broken)
    code = deploy_cli.main(["--dsn", "db:1"], connector=never_connect, write_bundle=never_bundle)
    err = capsys.readouterr().err
    assert code == deploy_cli.EXIT_ERROR
    assert "nothing was sent" in err and "100_game.sql" in err


def test_loader_config_overrides_reach_the_deployed_settings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    code = deploy_cli.main(
        [
            "--prefix",
            "PROBE_",
            "--dsn",
            "db:1",
            "--no-sync-api-features",
            "--loader-config",
            "monthly_call_budget=400, refresh_reserve=50",
        ],
        connector=lambda _: db,
        write_bundle=written,
    )
    assert code == deploy_cli.EXIT_OK
    config = next(
        s for s in db.statements if s.startswith("INSERT INTO PROBE_CFB_META.LOADER_CONFIG")
    )
    assert "('MONTHLY_CALL_BUDGET', '400')" in config
    assert "('REFRESH_RESERVE', '50')" in config
    assert "LOADER_CONFIG overrides applied: monthly_call_budget, refresh_reserve" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("override", ["monthly_call_budget=lots", "nonsense", "budget=1,budget=2"])
def test_an_invalid_loader_config_is_refused_before_anything_happens(
    override: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def never_connect(deployment: target.Target) -> ClosableDatabase:
        raise AssertionError("must not connect")

    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1", "--loader-config", override],
        connector=never_connect,
        write_bundle=written,
    )
    assert code == deploy_cli.EXIT_ERROR
    assert "LOADER_CONFIG" in capsys.readouterr().err


def test_a_loader_failure_still_prints_what_the_virtual_schema_deploy_did(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    db.fail_on = "CREATE SCHEMA IF NOT EXISTS PROBE_CFB_STAGE"
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1", "--no-sync-api-features"],
        connector=lambda _: db,
        write_bundle=written,
    )
    captured = capsys.readouterr()
    assert code == deploy_cli.EXIT_ERROR
    assert "virtual schema PROBE_CFBD_VS: created" in captured.out
    assert "deploy failed" in captured.err
    assert db.closed


def test_main_reports_a_deploy_error_and_still_closes_the_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    db.tables[("PROBE_CFB_META", "LOAD_LOG")] = [("X", "DOUBLE", True)]
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1"], connector=lambda _: db, write_bundle=written
    )
    captured = capsys.readouterr()
    assert code == deploy_cli.EXIT_ERROR
    assert "deploy failed: existing meta tables do not match" in captured.err
    assert db.closed


def test_main_wraps_a_database_error_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Exploding(ClosableDatabase):
        def execute(self, query: str) -> Never:
            raise RuntimeError("syntax error near FOO\nline 2")

    db = Exploding()
    code = deploy_cli.main(["--dsn", "db:1"], connector=lambda _: db, write_bundle=written)
    err = capsys.readouterr().err
    assert code == deploy_cli.EXIT_ERROR
    assert "Exasol rejected a statement: syntax error near FOO" in err
    assert "line 2" not in err
    assert db.closed


def test_main_rejects_an_invalid_prefix_and_a_failing_bundle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert deploy_cli.main(["--prefix", "bad"], connector=lambda _: None) == 2
    assert "prefix" in capsys.readouterr().err

    def failing() -> bundle.BundleSizes:
        raise bundle.BundleError("too big")

    assert deploy_cli.main(["--dsn", "db:1"], connector=lambda _: None, write_bundle=failing) == 2
    assert "too big" in capsys.readouterr().err


def test_connect_failure_is_a_clean_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "local"
    directory.mkdir()
    (directory / "secrets.json").write_text('{"dbPassword": "pw-that-must-not-leak"}')
    monkeypatch.setattr(target, "DEPLOYMENTS_ROOT", tmp_path)
    monkeypatch.setattr(
        target, "resolve", partial(target.resolve, deployments_root=tmp_path, environ={})
    )
    code = deploy_cli.main(["--dsn", "127.0.0.1:1"], write_bundle=written)
    captured = capsys.readouterr()
    assert code == deploy_cli.EXIT_ERROR
    assert "cannot connect to Exasol at 127.0.0.1:1" in captured.err
    assert "pw-that-must-not-leak" not in captured.out + captured.err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--prefix", "TEST_", "--connection", "CFBD_API"],
        ["--prefix", "TEST_", "--connection", "cfbd_api"],
        ["--prefix", "TEST_", "--property", "CONNECTION_NAME=CFBD_API"],
        ["--prefix", "TEST_", "--property", "connection_name="],
    ],
)
def test_a_prefixed_deploy_with_the_real_connection_is_refused_before_anything_happens(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    def never_connect(deployment: target.Target) -> ClosableDatabase:
        raise AssertionError("must not connect")

    def never_bundle() -> bundle.BundleSizes:
        raise AssertionError("must not build")

    code = deploy_cli.main(
        [*arguments, "--dsn", "db:1"], connector=never_connect, write_bundle=never_bundle
    )
    err = capsys.readouterr().err
    assert code == deploy_cli.EXIT_ERROR
    assert "must not use the real connection CFBD_API" in err


def test_the_real_namespace_may_use_the_real_connection(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    code = deploy_cli.main(
        ["--connection", "CFBD_API", "--dsn", "db:1", "--no-sync-api-features"],
        connector=lambda _: db,
        write_bundle=written,
    )
    assert code == deploy_cli.EXIT_OK


def test_the_target_option_selects_the_deployment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "default").mkdir()
    (tmp_path / "default" / "deployment.json").write_text(
        '{"connection": {"host": "aws.example", "dbPort": 8563}}'
    )
    monkeypatch.setattr(
        target, "resolve", partial(target.resolve, deployments_root=tmp_path, environ={})
    )
    code = deploy_cli.main(["--target", "aws"], connector=lambda _: None, write_bundle=written)
    assert code == deploy_cli.EXIT_ERROR
    assert "no certificate fingerprint" in capsys.readouterr().err


# --- per-target loader settings (review m4) -----------------------------------------------------


def test_a_plain_aws_deploy_keeps_the_target_budget_of_the_month(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    assert deploy_main(db, "--target", "aws") == deploy_cli.EXIT_OK
    assert "('MONTHLY_CALL_BUDGET', '400')" in config_row(db)
    assert "('REFRESH_RESERVE', '50')" in config_row(db)
    out = capsys.readouterr().out
    assert (
        "loader config for target aws (vs/live/loader-config.yaml): monthly_call_budget=400 "
        "(the ledger's cap for 2026-10), refresh_reserve=50"
    ) in out


def test_in_november_the_aws_budget_follows_the_ledger_cap() -> None:
    db = ClosableDatabase()
    assert deploy_main(db, "--target", "aws", clock=NOVEMBER) == deploy_cli.EXIT_OK
    assert "('MONTHLY_CALL_BUDGET', '1000')" in config_row(db)


def test_an_explicit_loader_config_wins_and_is_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    code = deploy_main(db, "--target", "aws", "--loader-config", "monthly_call_budget=1000")
    assert code == deploy_cli.EXIT_OK
    assert "('MONTHLY_CALL_BUDGET', '1000')" in config_row(db)
    assert "('REFRESH_RESERVE', '50')" in config_row(db)
    out = capsys.readouterr().out
    assert "LOADER_CONFIG: monthly_call_budget=1000 (wins over vs/live/loader-config.yaml)" in out


def test_a_local_deploy_keeps_the_plan_settings() -> None:
    db = ClosableDatabase()
    assert deploy_main(db) == deploy_cli.EXIT_OK
    assert "('MONTHLY_CALL_BUDGET', '1000')" in config_row(db)
    assert "('REFRESH_RESERVE', '150')" in config_row(db)


def test_a_month_without_a_ledger_cap_refuses_an_aws_deploy_before_connecting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    assert deploy_main(db, "--target", "aws", clock=DECEMBER) == deploy_cli.EXIT_ERROR
    assert "no cap for 2026-12" in capsys.readouterr().err
    assert db.statements == [] and db.log == []
    explicit = ClosableDatabase()
    code = deploy_main(
        explicit, "--target", "aws", "--loader-config", "monthly_call_budget=200", clock=DECEMBER
    )
    assert code == deploy_cli.EXIT_OK
    assert "('MONTHLY_CALL_BUDGET', '200')" in config_row(explicit)


def test_the_default_target_settings_come_from_the_committed_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--no-sync-api-features", "--target", "aws"],
        connector=lambda _: db,
        write_bundle=written,
        resolve=aws,
        clock=lambda: OCTOBER,
        month_caps=lambda: CAPS,
    )
    assert code == deploy_cli.EXIT_OK
    assert "('REFRESH_RESERVE', '50')" in config_row(db)


# --- the DEPLOY run lock (review m7) ------------------------------------------------------------


def test_the_first_deploy_runs_without_the_lock_it_installs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    assert deploy_main(db) == deploy_cli.EXIT_OK
    assert not [line for line in db.log if "RUN_LOCK\"('" in line]
    assert "RUN_LOCK is not installed yet" in capsys.readouterr().out
    assert ("PROBE_CFB_INFRA", "RUN_LOCK") in db.scripts


def test_a_redeploy_holds_the_deploy_lock_around_both_deploys(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    deploy_main(db)
    db.log.clear()
    assert deploy_main(db) == deploy_cli.EXIT_OK
    acquire = next(i for i, line in enumerate(db.log) if "('acquire', 'DEPLOY', NULL" in line)
    release = next(i for i, line in enumerate(db.log) if "('release', 'DONE', 31" in line)
    first_change = next(i for i, line in enumerate(db.log) if line.startswith(("CREATE", "ALTER")))
    last_change = max(i for i, line in enumerate(db.log) if line.startswith("CREATE"))
    assert acquire < first_change and last_change < release
    assert "'make deploy'" in db.log[acquire]
    assert "run lock: DEPLOY run 31" in capsys.readouterr().out


def test_a_redeploy_deploys_the_model_under_the_same_lock(
    capsys: pytest.CaptureFixture[str], model_calls: ModelCalls
) -> None:
    db = ClosableDatabase()
    deploy_main(db)
    db.log.clear()
    assert deploy_main(db) == deploy_cli.EXIT_OK
    (_, _, held) = model_calls.calls[-1]
    assert held is not None and held.run_id == 31 and held.kind == "DEPLOY"
    assert len([line for line in db.log if "('acquire'" in line]) == 1
    release = next(line for line in db.log if "('release', 'DONE', 31" in line)
    assert "'deployed plan " in release and "; PROBE_CFB_SIM: 11 tables, 15 views'" in release
    out = capsys.readouterr().out
    assert out.index("load plan in PROBE_CFB_META", out.index("run lock: DEPLOY run 31")) < (
        out.index(MODEL_LINE, out.index("run lock: DEPLOY run 31"))
    )


def test_a_model_failure_under_the_lock_ends_it_failed(capsys: pytest.CaptureFixture[str]) -> None:
    def failing(
        connection: Any, namespace: Namespace, *, lock: HeldLock | None = None
    ) -> ModelReport:
        raise DeployError("PROBE_CFB_MODEL: 100_game.sql: GAME: view does not compile")

    db = ClosableDatabase()
    deploy_main(db)
    db.log.clear()
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--no-sync-api-features"],
        connector=lambda _: db,
        write_bundle=written,
        resolve=aws,
        clock=lambda: OCTOBER,
        month_caps=lambda: CAPS,
        target_settings=AWS_CONFIG,
        deploy_model=failing,
    )
    assert code == deploy_cli.EXIT_ERROR
    assert any("('release', 'FAILED', 31" in line for line in db.log)
    assert "view does not compile" in capsys.readouterr().err


def test_a_deploy_during_a_loader_run_is_refused_and_changes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    deploy_main(db)
    db.holder = HOLDER
    db.log.clear()
    db.statements.clear()
    assert deploy_main(db) == deploy_cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "DEPLOY refused: BACKFILL run 5 is running" in err and "nothing was deployed" in err
    assert db.statements == []
    assert db.closed


def test_a_lock_lost_during_the_deploy_is_an_error_without_the_refusal_note(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = ClosableDatabase()
    deploy_main(db)
    db.lost = True
    assert deploy_main(db) == deploy_cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "was no longer RUNNING when it ended" in err
    assert "nothing was deployed" not in err


def test_a_failed_redeploy_ends_its_lock_failed(capsys: pytest.CaptureFixture[str]) -> None:
    db = ClosableDatabase()
    deploy_main(db)
    db.fail_on = "CREATE SCHEMA IF NOT EXISTS PROBE_CFB_STAGE"
    db.log.clear()
    assert deploy_main(db) == deploy_cli.EXIT_ERROR
    assert any("('release', 'FAILED', 31" in line for line in db.log)
    assert "deploy failed" in capsys.readouterr().err
