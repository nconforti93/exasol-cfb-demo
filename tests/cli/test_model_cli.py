"""`scripts/model.py` (`make model`, `model-check`, `model-docs`, `model-perf`) without a database.

The deployer runs for real over a fake connection (`tests/unit/test_model_deploy.py`); the target
and the connection are injected. The Makefile targets are checked with `make -n`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from loader.runlock import RunLockError
from model import dictionary, perf
from model.deploy import ModelOptions, prepare
from scripts import model as cli
from tests.unit.test_model_deploy import LockedConnection, _Rows
from tests.unit.test_sqlobjects_deploy import FakeQueryError, expected_catalog
from vs.host import target
from vs.host.namespace import Namespace

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "unit" / "fixtures" / "sqlobjects"
VALID = ModelOptions(directory=FIXTURES / "valid")
BROKEN = ModelOptions(directory=FIXTURES / "broken")


class CheckConnection(LockedConnection):
    """The deploy fake that also answers `AS_OF_POINT` and `MODEL_CHECKS`."""

    def __init__(self, points: list[tuple[int]], checks: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self.points = points
        self.checks = checks

    def execute(self, query: str) -> Any:
        if '"AS_OF_POINT"' in query:
            self.statements.append(query)
            return _Rows(self.points)
        if '"MODEL_CHECKS"' in query:
            self.statements.append(query)
            return _Rows(self.checks)
        return super().execute(query)


@dataclass
class Calls:
    connections: list[LockedConnection] = field(default_factory=list)
    targets: list[str | None] = field(default_factory=list)
    connection_factory: Any = None


@pytest.fixture
def calls() -> Calls:
    return Calls()


def run(
    calls: Calls,
    *argv: str,
    options: ModelOptions = VALID,
    docs_path: Path | None = None,
    seeder: cli.Seeder | None = None,
) -> int:
    def resolve(name: str | None = None, *, dsn: str | None = None) -> target.Target:
        calls.targets.append(name)
        return target.Target(
            name or "local", "127.0.0.1", 8563, "sys", Path("/x"), target.TLS_UNVERIFIED_LOCAL
        )

    def connector(_: target.Target) -> LockedConnection:
        connection = (calls.connection_factory or deploy_connection)()
        calls.connections.append(connection)
        return connection

    extra: dict[str, Any] = {} if docs_path is None else {"docs_path": docs_path}
    if seeder is not None:
        extra["seeder"] = seeder
    return cli.main(list(argv), connector=connector, resolve=resolve, options=options, **extra)


def deploy_connection(prefix: str = "TEST_") -> LockedConnection:
    connection = LockedConnection(prefix=prefix)
    connection.catalog, connection.columns = expected_catalog(
        prepare(Namespace(prefix), VALID.directory)
    )
    return connection


def check_rows(*, error_fails: bool = False) -> list[tuple[Any, ...]]:
    return [
        (
            "FBS_TEAM_WITHOUT_CONFERENCE",
            "ERROR",
            2026,
            None,
            "FAIL" if error_fails else "PASS",
            1 if error_fails else 0,
            "Quarry" if error_fails else None,
        ),
        ("WEEK_NOT_COMPLETE", "WARNING", 2026, 9, "FAIL", 1, "game 12"),
        ("GAME_TEAM_NOT_IN_TEAM_LIST", "INFO", 2026, None, "PASS", 0, None),
    ]


def with_checks(calls: Calls, points: list[tuple[int]], rows: list[tuple[Any, ...]]) -> None:
    calls.connection_factory = lambda: CheckConnection(points, rows)


class StepConnection(LockedConnection):
    """The deploy fake that also answers the `REFRESH_MODEL` script (installed or not)."""

    def __init__(self, *, installed: bool = True, failure: str | None = None) -> None:
        super().__init__()
        self.step_installed = installed
        self.failure = failure

    def execute(self, query: str) -> Any:
        if "SCRIPT_NAME = 'REFRESH_MODEL'" in query:
            self.statements.append(query)
            return _Rows([(1 if self.step_installed else 0,)])
        if query.startswith('EXECUTE SCRIPT "TEST_CFB_INFRA"."REFRESH_MODEL"'):
            self.statements.append(query)
            if self.failure:
                raise FakeQueryError(self.failure)
            return _Rows(
                [
                    ("TABLE", "GAME", None, "REBUILT", 120, None),
                    ("TABLE", "TEAM_SEASON", None, "REBUILT", 30, None),
                    ("CHECK", "GAME_WITHOUT_WEEK", "ERROR", "PASS", 0, None),
                    ("CHECK", "WEEK_NOT_COMPLETE", "WARNING", "FAIL", 1, "game 12"),
                ]
            )
        return super().execute(query)


# --- deploy ---------------------------------------------------------------------------------


def test_deploy_prints_the_report_and_closes_the_connection(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    calls.connection_factory = lambda: deploy_connection("")
    assert run(calls, "deploy") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "schema CFB_MODEL: ready" in out
    assert "1 tables, 2 views, 1 functions" in out
    assert "comments:" in out and "(100 %)" in out
    assert out.rstrip().endswith("0 CFBD calls")
    assert calls.connections[0].closed
    assert calls.targets == [None]


def test_prefix_and_target_reach_the_namespace_and_the_resolver(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "deploy", "--prefix", "TEST_", "--target", "aws") == cli.EXIT_OK
    assert calls.targets == ["aws"]
    assert "schema TEST_CFB_MODEL: ready" in capsys.readouterr().out
    assert any(
        s.startswith("CREATE SCHEMA IF NOT EXISTS TEST_CFB_MODEL")
        for s in calls.connections[0].statements
    )


def test_lint_errors_are_listed_and_nothing_is_sent(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "deploy", options=BROKEN) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "nothing was sent" in err
    assert "100_game_a.sql" in err and "reserved" in err and "CFBD_VS" in err
    assert err.count("\n") >= 6
    assert calls.connections == []
    assert calls.targets == []


def test_lint_only_lints_and_renders_without_a_connection(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "deploy", "--lint-only") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("lint ok: 4 objects") and out.rstrip().endswith("0 CFBD calls")
    assert calls.connections == [] and calls.targets == []


def test_lint_only_lists_every_problem_and_exits_2(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "deploy", "--lint-only", options=BROKEN) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "nothing was sent" in captured.err and "100_game_a.sql" in captured.err
    assert "lint ok" not in captured.out
    assert calls.connections == [] and calls.targets == []


def test_lint_only_accepts_a_prefix_and_rejects_a_bad_one(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(calls, "deploy", "--lint-only", "--prefix", "TEST_") == cli.EXIT_OK
    assert run(calls, "deploy", "--lint-only", "--prefix", "test") == cli.EXIT_ERROR
    assert "prefix" in capsys.readouterr().err
    assert calls.connections == []


def test_a_held_run_lock_is_a_refusal_with_exit_1(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused() -> LockedConnection:
        return LockedConnection(refuse=True)

    calls.connection_factory = refused
    assert run(calls, "deploy", "--prefix", "TEST_") == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert err.startswith("refused: DEPLOY refused: BACKFILL run 7")
    assert calls.connections[0].closed
    assert not any(s.startswith("CREATE") for s in calls.connections[0].statements)


def test_a_loader_that_is_not_deployed_is_an_error(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    calls.connection_factory = lambda: LockedConnection(installed=False)
    assert run(calls, "deploy", "--prefix", "TEST_") == cli.EXIT_ERROR
    assert "make deploy" in capsys.readouterr().err


def test_a_database_error_names_the_object_and_exits_2(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing() -> LockedConnection:
        connection = deploy_connection()
        connection.fail["CREATE OR REPLACE FORCE VIEW TEST_CFB_MODEL.SEASON"] = "no such thing"
        return connection

    calls.connection_factory = failing
    assert run(calls, "deploy", "--prefix", "TEST_") == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "110_season.sql" in err and "no such thing" in err


def test_a_bad_prefix_is_an_error(calls: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(calls, "deploy", "--prefix", "test") == cli.EXIT_ERROR
    assert "prefix" in capsys.readouterr().err
    assert calls.connections == []


def test_a_target_error_is_reported_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def resolve(name: str | None = None, *, dsn: str | None = None) -> target.Target:
        raise target.TargetError("deployment aws has no certificate")

    code = cli.main(["deploy"], resolve=resolve, options=VALID)
    assert code == cli.EXIT_ERROR
    assert "deployment aws has no certificate" in capsys.readouterr().err


def test_an_unexpected_driver_error_prints_its_first_line(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    class Exploding(LockedConnection):
        def execute(self, query: str) -> Any:
            raise FakeQueryError("connection reset\nsecond line")

    calls.connection_factory = Exploding
    assert run(calls, "deploy", "--prefix", "TEST_") == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "connection reset" in err and "second line" not in err


def test_run_lock_errors_are_errors_not_refusals() -> None:
    assert not issubclass(RunLockError, cli.Refused)


# --- check ----------------------------------------------------------------------------------


def test_check_prints_the_checks_of_the_point_and_a_summary(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    with_checks(calls, [(9,)], check_rows())
    assert (
        run(calls, "check", "--season", "2026", "--week", "9", "--prefix", "TEST_") == cli.EXIT_OK
    )
    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == list(cli.CHECK_COLUMNS)
    assert "WEEK_NOT_COMPLETE" in out and "game 12" in out
    assert "3 checks, 1 failed (0 ERROR); 0 CFBD calls" in out
    statements = calls.connections[0].statements
    assert 'FROM "TEST_CFB_MODEL"."AS_OF_POINT"' in statements[0]
    assert "\"AS_OF_SEASON_TYPE\" = 'regular'" in statements[0]
    assert '"AS_OF_WEEK" = 9' in statements[0]
    assert '"WEEK_SEQ" = 9' in statements[1] and '"SEASON" = 2026' in statements[1]
    assert calls.connections[0].closed
    assert not any("RUN_LOCK" in s for s in statements)


def test_check_uses_the_season_type_of_the_postseason(calls: Calls) -> None:
    with_checks(calls, [(101,)], [])
    assert (
        run(calls, "check", "--season", "2024", "--week", "1", "--season-type", "postseason") == 0
    )
    assert "'postseason'" in calls.connections[0].statements[0]
    assert '"WEEK_SEQ" = 101' in calls.connections[0].statements[1]


def test_a_failed_error_check_exits_1(calls: Calls, capsys: pytest.CaptureFixture[str]) -> None:
    with_checks(calls, [(9,)], check_rows(error_fails=True))
    code = run(calls, "check", "--season", "2026", "--week", "9")
    assert code == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "1 ERROR check(s) failed" in captured.err
    assert "Quarry" in captured.out and "(1 ERROR)" in captured.out


def test_an_unknown_point_is_an_error_that_names_the_view(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    with_checks(calls, [], [])
    assert run(calls, "check", "--season", "2026", "--week", "99") == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "no as-of point for season 2026, week 99, regular in CFB_MODEL.AS_OF_POINT" in err


def test_check_needs_a_season_and_a_week(calls: Calls) -> None:
    with pytest.raises(SystemExit):
        run(calls, "check", "--season", "2026")


def test_check_rejects_an_unknown_season_type(calls: Calls) -> None:
    with pytest.raises(SystemExit):
        run(calls, "check", "--season", "2026", "--week", "1", "--season-type", "bowl")


# --- refresh ----------------------------------------------------------------------------


def test_refresh_runs_the_step_on_its_own_and_prints_tables_and_checks(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    calls.connection_factory = StepConnection
    code = run(calls, "refresh", "--prefix", "TEST_", "--season", "2026", "--week", "7")
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    [connection] = calls.connections
    assert connection.statements[-1] == (
        'EXECUTE SCRIPT "TEST_CFB_INFRA"."REFRESH_MODEL"(NULL, 2026, 7, \'regular\')'
    )
    assert connection.closed
    assert "rebuilt 2 derived tables of TEST_CFB_MODEL: 150 rows" in out
    assert "WEEK_NOT_COMPLETE" in out and "logged to TEST_CFB_META.MODEL_CHECK_LOG" in out


def test_refresh_without_a_season_only_rebuilds(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    calls.connection_factory = StepConnection
    assert run(calls, "refresh", "--prefix", "TEST_") == cli.EXIT_OK
    assert calls.connections[0].statements[-1].endswith("(NULL, NULL, NULL, NULL)")
    assert run(calls, "refresh", "--prefix", "TEST_", "--season", "2026") == cli.EXIT_ERROR
    assert "both --season and --week, or neither" in capsys.readouterr().err


def test_refresh_refusals_and_a_missing_step(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    calls.connection_factory = lambda: StepConnection(
        failure="MODEL refused: BACKFILL run 7 is running (session 1, started x UTC)"
    )
    assert run(calls, "refresh", "--prefix", "TEST_") == cli.EXIT_REFUSED
    calls.connection_factory = lambda: StepConnection(
        failure="MODEL: 1 ERROR check(s) failed for season 2026, regular week 7: X (1)"
    )
    assert run(calls, "refresh", "--prefix", "TEST_", "--season", "2026", "--week", "7") == 1
    calls.connection_factory = lambda: StepConnection(installed=False)
    assert run(calls, "refresh", "--prefix", "TEST_") == cli.EXIT_ERROR
    assert "REFRESH_MODEL is not installed: run `make model` first" in capsys.readouterr().err


# --- docs and perf --------------------------------------------------------------------------


def test_docs_writes_the_dictionary_without_a_connection(
    calls: Calls, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "dictionary.md"
    assert run(calls, "docs", docs_path=out_path) == cli.EXIT_OK
    assert out_path.read_text(encoding="utf-8") == dictionary.render_dictionary(VALID.directory)
    assert f"wrote {out_path}: 4 objects" in capsys.readouterr().out
    assert calls.connections == [] and calls.targets == []


def test_docs_lint_errors_stop_the_generation(
    calls: Calls, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "dictionary.md"
    assert run(calls, "docs", options=BROKEN, docs_path=out_path) == cli.EXIT_ERROR
    assert not out_path.exists()
    assert "nothing was sent" in capsys.readouterr().err


def perf_run(calls: Calls, *argv: str) -> tuple[int, list[tuple[Any, Namespace]]]:
    calls.connection_factory = lambda: deploy_connection("PERF_")
    seeded: list[tuple[Any, Namespace]] = []

    def seeder(connection: Any, namespace: Namespace, log: Any) -> None:
        seeded.append((connection, namespace))
        log("scaled league seeded")

    return run(calls, "perf", *argv, seeder=seeder), seeded


def slow_probe(monkeypatch: pytest.MonkeyPatch, median: float) -> None:
    def fake_run_probes(connection: Any, to_run: Any, **_: Any) -> list[perf.Timing]:
        return [perf.Timing(probe, median, median, 3) for probe in to_run]

    monkeypatch.setattr(perf, "run_probes", fake_run_probes)


def test_perf_seeds_deploys_and_times_in_the_perf_namespace(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    code, seeded = perf_run(calls, "--warmups", "1", "--runs", "2")
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert [namespace.prefix for _, namespace in seeded] == ["PERF_"]
    connection = calls.connections[0]
    assert seeded[0][0] is connection and connection.closed
    assert "scaled league seeded" in out and "schema PERF_CFB_MODEL: ready" in out
    assert "GAME grouped by season" in out and "as-of prototype, one point" in out
    assert f"{len(perf.probes(Namespace('PERF_')))} probes: " in out
    selects = [s for s in connection.statements if s.startswith('SELECT "SEASON"')]
    assert selects and not any(s.startswith("DROP SCHEMA") for s in connection.statements)
    assert connection.statements.count("ALTER SESSION SET QUERY_CACHE = 'OFF'") == 1


def test_perf_refuses_any_namespace_but_perf(
    calls: Calls, capsys: pytest.CaptureFixture[str]
) -> None:
    code, seeded = perf_run(calls, "--prefix", "TEST_")
    assert code == cli.EXIT_ERROR and seeded == []
    assert "only in the PERF_ namespace" in capsys.readouterr().err
    assert calls.connections == []
    code, _ = perf_run(Calls(), "--prefix", "")
    assert code == cli.EXIT_ERROR


def test_perf_drop_removes_the_six_schemas_after_the_run(calls: Calls) -> None:
    code, _ = perf_run(calls, "--warmups", "0", "--runs", "1", "--drop")
    assert code == cli.EXIT_OK
    drops = [s for s in calls.connections[0].statements if s.startswith("DROP SCHEMA")]
    assert sorted(drops) == sorted(
        f"DROP SCHEMA IF EXISTS PERF_CFB_{name} CASCADE"
        for name in ("INFRA", "META", "CORE", "STAGE", "RESTORE", "MODEL")
    )


def test_perf_exits_1_naming_the_probes_over_target_and_still_drops(
    calls: Calls, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    slow_probe(monkeypatch, 9.0)
    code, _ = perf_run(calls, "--drop")
    captured = capsys.readouterr()
    assert code == cli.EXIT_REFUSED
    assert "probe(s) over target: GAME grouped by season" in captured.err
    assert "OVER 2X" in captured.out
    assert any(s.startswith("DROP SCHEMA") for s in calls.connections[0].statements)


def test_perf_drops_the_namespace_when_a_probe_fails(
    calls: Calls, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(connection: Any, to_run: Any, **_: Any) -> list[perf.Timing]:
        raise FakeQueryError("object GAME not found")

    monkeypatch.setattr(perf, "run_probes", broken)
    code, _ = perf_run(calls, "--drop")
    assert code == cli.EXIT_ERROR
    assert "object GAME not found" in capsys.readouterr().err
    assert any(s.startswith("DROP SCHEMA") for s in calls.connections[0].statements)


# --- the module and the Makefile ------------------------------------------------------------


def test_the_module_runs_as_a_script() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.model", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "make model-check" in result.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(
    ("target_line", "expected"),
    [
        (["model"], "-m scripts.model deploy"),
        (["model", "ARGS=--prefix TEST_"], "-m scripts.model deploy --prefix TEST_"),
        (["model-docs"], "-m scripts.model docs"),
        (
            ["model-check", "SEASON=2026", "WEEK=9", "SEASON_TYPE=postseason"],
            '-m scripts.model check --season "2026" --week "9" --season-type "postseason"',
        ),
        (["model-perf", "ARGS=--prefix PERF_"], "-m scripts.model perf --prefix PERF_"),
        (
            ["model-refresh", "SEASON=2026", "WEEK=7"],
            '-m scripts.model refresh --season "2026" --week "7"',
        ),
        (["model-refresh"], "-m scripts.model refresh"),
    ],
)
def test_the_make_targets_call_the_script(target_line: list[str], expected: str) -> None:
    result = subprocess.run(
        ["make", "-n", *target_line], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
