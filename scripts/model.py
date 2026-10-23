"""`make model`, `make model-check`, `make model-docs`, `make model-perf`: the analytical model.

    python -m scripts.model deploy [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                                   [--lint-only]
    python -m scripts.model check --season Y --week W [--season-type regular|postseason] [...]
    python -m scripts.model docs
    python -m scripts.model perf [--prefix PERF_] [--season Y] [--week-seq N] [--drop] [...]

    make model [TARGET=local|aws] [ARGS="--prefix TEST_"]
    make model-check SEASON=y WEEK=w [SEASON_TYPE=postseason] [TARGET=local|aws]
    make model-docs
    make model-perf [TARGET=local|aws] [ARGS="--prefix PERF_"]

- `deploy` lints `sql/model/` first (every problem is listed and nothing is sent), then connects
  as `sys` to the target (`vs/host/target.py`), takes the loader's run lock as kind `DEPLOY` and
  installs `CFB_MODEL` in one transaction (`model/deploy.py`). It refuses while a loader run,
  a snapshot or a restore holds the lock. Zero CFBD calls: the model never reads the virtual schema.
  `--lint-only` lints and renders only: no target is resolved, no connection is opened. Exit 0 and
  "lint ok", or 2 with every problem listed.
- `check` prints `MODEL_CHECKS` for the point `REFRESH_WEEK(season, week, season_type)` addresses,
  without the step script and without writing a log. Exit 1 when an ERROR check failed.
- `docs` regenerates `docs/model-dictionary.md` from the files (no database).
- `perf` seeds the scaled synthetic league into the `PERF_` namespace when it is not stored yet
  (`tests/it/model_support.py`, no CFBD call, no virtual schema), deploys the model there under the
  run lock, times the probes of `model/perf.py` (3 warm-ups, 5 runs, query cache off) and prints
  median, worst and a verdict per probe. `--drop` removes the namespace afterwards, also when a
  probe fails. It only runs against the `PERF_` namespace, never the real one.

Exit codes: 0 ok, 1 refused (the run lock is held, an ERROR check failed, or a probe is over its
target), 2 error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from loader.runlock import RunLockError, RunLockRefused
from model import deploy as model_deploy
from model import dictionary
from model import perf as model_perf
from model.deploy import ModelLintError, ModelOptions
from vs.cfbd_vs.sqlrender import qualified_name, string_literal
from vs.host import target
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace, NamespaceError

SEASON_TYPES = ("regular", "postseason")
CHECK_COLUMNS = (
    "CHECK_NAME",
    "SEVERITY",
    "SEASON",
    "WEEK_SEQ",
    "CHECK_STATUS",
    "OBSERVED",
    "DETAIL",
)
FAILED = "FAIL"
ERROR_SEVERITY = "ERROR"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2

PERF_PREFIX = "PERF_"

Connector = Callable[[target.Target], Any]
Seeder = Callable[[Any, Namespace, Callable[[str], None]], None]


class Refused(Exception):
    """The command ran and the answer is "no": an ERROR check failed."""


def connect_sys(deployment: target.Target) -> Any:
    """A sys connection to the target; the password comes from its secrets file."""
    return deployment.connect()


def seed_scaled_league(connection: Any, namespace: Namespace, log: Callable[[str], None]) -> None:
    """Deploy the foundation of `namespace` and store the scaled league unless it is stored."""
    from tests.it import model_support

    model_support.seed_scaled_namespace(connection, namespace, log=log)


def _print(line: str) -> None:
    print(line, flush=True)


class _Command:
    def __init__(
        self,
        args: argparse.Namespace,
        connector: Connector,
        resolve: Callable[..., target.Target],
        options: ModelOptions,
        docs_path: Path,
        seeder: Seeder,
    ) -> None:
        self._args = args
        self._connector = connector
        self._resolve = resolve
        self._options = options
        self._docs_path = docs_path
        self._seeder = seeder

    def deploy(self) -> int:
        namespace = Namespace(self._args.prefix)
        objects = model_deploy.prepare(namespace, self._options.directory)
        if self._args.lint_only:
            _print(f"lint ok: {len(objects)} objects; nothing sent, 0 CFBD calls")
            return EXIT_OK
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            report = model_deploy.deploy_model(connection, namespace, self._options)
        finally:
            connection.close()
        for line in report.lines:
            _print(line)
        return EXIT_OK

    def check(self) -> int:
        namespace = Namespace(self._args.prefix)
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            rows = _checks(
                connection, namespace, self._args.season, self._args.week, self._args.season_type
            )
        finally:
            connection.close()
        failed_errors = _print_checks(rows)
        if failed_errors:
            raise Refused(f"{failed_errors} ERROR check(s) failed")
        return EXIT_OK

    def docs(self) -> int:
        count = dictionary.write_dictionary(self._docs_path, self._options.directory)
        _print(f"wrote {self._docs_path}: {count} objects")
        return EXIT_OK

    def perf(self) -> int:
        if self._args.prefix != PERF_PREFIX:
            raise DeployError(
                f"perf runs only in the {PERF_PREFIX} namespace (got prefix "
                f"{self._args.prefix!r}); the real model is never seeded with synthetic games"
            )
        namespace = Namespace(self._args.prefix)
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            try:
                timings = self._measure(connection, namespace)
            finally:
                if self._args.drop:
                    _drop_namespace(connection, namespace)
        finally:
            connection.close()
        for line in model_perf.report(timings):
            _print(line)
        over = model_perf.misses(timings)
        if over:
            names = ", ".join(t.probe.name for t in over)
            raise Refused(f"{len(over)} probe(s) over target: {names}")
        return EXIT_OK

    def _measure(self, connection: Any, namespace: Namespace) -> list[model_perf.Timing]:
        self._seeder(connection, namespace, _print)
        for line in model_deploy.deploy_model(connection, namespace, self._options).lines:
            _print(line)
        probes = model_perf.probes(
            namespace, season=self._args.season, week_seq=self._args.week_seq
        )
        return model_perf.run_probes(
            connection, probes, warmups=self._args.warmups, runs=self._args.runs
        )


def _drop_namespace(connection: Any, namespace: Namespace) -> None:
    schemas = (
        namespace.model_schema,
        namespace.stage_schema,
        namespace.restore_schema,
        namespace.core_schema,
        namespace.meta_schema,
        namespace.infra_schema,
    )
    for schema in schemas:
        connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    _print(f"dropped {', '.join(schemas)}")


def _checks(
    connection: Any, namespace: Namespace, season: int, week: int, season_type: str
) -> list[dict[str, Any]]:
    schema = namespace.model_schema
    point = connection.execute(
        f'SELECT "AS_OF_WEEK_SEQ" FROM {qualified_name(schema, "AS_OF_POINT")} '
        f'WHERE "AS_OF_SEASON" = {int(season)} AND "AS_OF_WEEK" = {int(week)} '
        f'AND "AS_OF_SEASON_TYPE" = {string_literal(season_type)}'
    ).fetchall()
    if not point:
        raise DeployError(
            f"no as-of point for season {season}, week {week}, {season_type} in "
            f"{schema}.AS_OF_POINT (is the model deployed, and the season stored?)"
        )
    columns = ", ".join(f'"{name}"' for name in CHECK_COLUMNS)
    sequence = int(point[0][0])
    statement = connection.execute(
        f"SELECT {columns} FROM {qualified_name(schema, 'MODEL_CHECKS')} "
        f'WHERE "SEASON" = {int(season)} AND ("WEEK_SEQ" IS NULL OR "WEEK_SEQ" = {sequence}) '
        'ORDER BY "SEVERITY", "CHECK_NAME", "WEEK_SEQ"'
    )
    return [dict(zip(CHECK_COLUMNS, row, strict=True)) for row in statement.fetchall()]


def _print_checks(rows: Sequence[dict[str, Any]]) -> int:
    """Print the checks as a table and a summary; returns the number of failed ERROR checks."""
    cells = [
        [str(row[name]) if row[name] is not None else "-" for name in CHECK_COLUMNS] for row in rows
    ]
    widths = [
        max(len(name), *(len(line[i]) for line in cells)) if cells else len(name)
        for i, name in enumerate(CHECK_COLUMNS)
    ]
    _print("  ".join(name.ljust(width) for name, width in zip(CHECK_COLUMNS, widths, strict=True)))
    for line in cells:
        _print(
            "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip()
        )
    failed = [row for row in rows if row["CHECK_STATUS"] == FAILED]
    errors = sum(1 for row in failed if row["SEVERITY"] == ERROR_SEVERITY)
    _print(f"{len(rows)} checks, {len(failed)} failed ({errors} ERROR); 0 CFBD calls")
    return errors


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    common.add_argument("--dsn", default=None, help="overrides host and port of the target")
    common.add_argument("--prefix", default="", help="namespace prefix, for example TEST_")
    return common


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.model",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    common = _common()
    deploy = commands.add_parser("deploy", parents=[common], help="lint and deploy CFB_MODEL")
    deploy.add_argument(
        "--lint-only",
        action="store_true",
        help="lint and render sql/model/ only: no connection, exit 0 or 2",
    )
    check = commands.add_parser("check", parents=[common], help="print MODEL_CHECKS of a point")
    check.add_argument("--season", type=int, required=True)
    check.add_argument("--week", type=int, required=True)
    check.add_argument("--season-type", default="regular", choices=SEASON_TYPES)
    commands.add_parser("docs", help="regenerate docs/model-dictionary.md")
    timing = commands.add_parser(
        "perf", parents=[_common()], help="time the model on the scaled league (PERF_ namespace)"
    )
    timing.set_defaults(prefix=PERF_PREFIX)
    timing.add_argument("--season", type=int, default=model_perf.DEFAULT_SEASON)
    timing.add_argument("--week-seq", type=int, default=model_perf.DEFAULT_WEEK_SEQ)
    timing.add_argument("--warmups", type=int, default=model_perf.DEFAULT_WARMUPS)
    timing.add_argument("--runs", type=int, default=model_perf.DEFAULT_RUNS)
    timing.add_argument("--drop", action="store_true", help="drop the namespace afterwards")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    connector: Connector = connect_sys,
    resolve: Callable[..., target.Target] = target.resolve,
    options: ModelOptions | None = None,
    docs_path: Path = dictionary.DICTIONARY_PATH,
    seeder: Seeder = seed_scaled_league,
) -> int:
    args = _parser().parse_args(argv)
    command = _Command(args, connector, resolve, options or ModelOptions(), docs_path, seeder)
    try:
        run: Callable[[], int] = getattr(command, args.command)
        return run()
    except (Refused, RunLockRefused) as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except ModelLintError as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    except (DeployError, RunLockError, target.TargetError, NamespaceError, OSError) as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as error:
        message = str(getattr(error, "message", error)).strip().splitlines()
        print(
            f"{args.command} failed: {message[0] if message else type(error).__name__}",
            file=sys.stderr,
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
