"""`make model`, `make model-check`, `make model-docs`, `make model-perf`: the analytical model.

    python -m scripts.model deploy [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                                   [--lint-only]
    python -m scripts.model check --season Y --week W [--season-type regular|postseason] [...]
    python -m scripts.model docs
    python -m scripts.model perf [...]

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
- `perf` is implemented with the performance smoke (task 3.7) and refuses until then.

Exit codes: 0 ok, 1 refused (the run lock is held, or an ERROR check failed), 2 error.
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

Connector = Callable[[target.Target], Any]


class Refused(Exception):
    """The command ran and the answer is "no": an ERROR check failed."""


def connect_sys(deployment: target.Target) -> Any:
    """A sys connection to the target; the password comes from its secrets file."""
    return deployment.connect()


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
    ) -> None:
        self._args = args
        self._connector = connector
        self._resolve = resolve
        self._options = options
        self._docs_path = docs_path

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
        raise DeployError(
            "model-perf is implemented with the performance smoke (task 3.7 of "
            "add-cfb-analytical-model); it does nothing yet"
        )


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
    commands.add_parser("perf", parents=[common], help="time the model on the scaled league")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    connector: Connector = connect_sys,
    resolve: Callable[..., target.Target] = target.resolve,
    options: ModelOptions | None = None,
    docs_path: Path = dictionary.DICTIONARY_PATH,
) -> int:
    args = _parser().parse_args(argv)
    command = _Command(args, connector, resolve, options or ModelOptions(), docs_path)
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
