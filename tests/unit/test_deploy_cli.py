from functools import partial
from pathlib import Path
from typing import Any, Never

import pytest

from loader.runlock import RunLockRefused
from model import deploy as model_deploy
from model.deploy import ModelLintError, ModelReport
from scripts import deploy as deploy_cli
from tests.unit.test_loader_deploy import LoaderDatabase
from vs import bundle
from vs.host import target
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace

SIZES = bundle.BundleSizes(1_534_511, 1_434_615)
MODEL_LINE = "model deployed as DEPLOY run 42; 0 CFBD calls"


class ClosableDatabase(LoaderDatabase):
    closed = False

    def __init__(self) -> None:
        super().__init__(foundation=False)

    def close(self) -> None:
        self.closed = True


def written() -> bundle.BundleSizes:
    return SIZES


class ModelCalls:
    """Stands in for the model deploy, which needs the run lock script of a real database."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    def __call__(self, connection: Any, namespace: Namespace) -> ModelReport:
        self.calls.append((connection, namespace.prefix))
        return ModelReport(None, 42, False, ("model line", MODEL_LINE))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def model_calls(monkeypatch: pytest.MonkeyPatch) -> ModelCalls:
    calls = ModelCalls()
    monkeypatch.setattr(model_deploy, "deploy_model", calls)
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


def test_the_model_is_deployed_last_on_the_same_connection(
    capsys: pytest.CaptureFixture[str], model_calls: ModelCalls
) -> None:
    db = ClosableDatabase()
    code = deploy_cli.main(
        ["--prefix", "PROBE_", "--dsn", "db:1", "--no-sync-api-features"],
        connector=lambda _: db,
        write_bundle=written,
    )
    out = capsys.readouterr().out
    assert code == deploy_cli.EXIT_OK
    assert model_calls.calls == [(db, "PROBE_")]
    assert (
        out.index("load plan in PROBE_CFB_META") < out.index("model line") < out.index(MODEL_LINE)
    )
    assert db.closed


def test_a_refused_model_deploy_is_an_error_after_the_earlier_steps_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refused(connection: Any, namespace: Namespace) -> ModelReport:
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
