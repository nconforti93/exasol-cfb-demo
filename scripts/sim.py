"""`make sim`: the season simulator's schema and engine script.

    python -m scripts.sim deploy [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                                 [--lint-only]

    python -m scripts.sim fit [--target local|aws] [--dsn HOST:PORT] [--prefix PREFIX_]
                              [--output PATH] [--dry-run]

    make sim [TARGET=local|aws] [ARGS="--prefix TEST_"]
    make sim-fit [TARGET=local|aws] [ARGS="--dry-run"]

- `deploy` lints `sql/sim/` first (every problem is listed and nothing is sent), then connects as
  `sys` to the target (`vs/host/target.py`), takes the loader's run lock as kind `DEPLOY` and
  installs `CFB_SIM` and `CFB_INFRA.SIM_ENGINE` in one transaction (`engine/deploy.py`). It refuses
  while a loader run, a snapshot or a restore holds the lock, and it needs the loader deployed
  (`make deploy`). The data tables are created when missing and never replaced, so a redeploy keeps
  every run. Zero CFBD calls: the simulation never reads the virtual schema.
  `--lint-only` lints, renders and builds the script only: no target is resolved, no connection is
  opened. Exit 0 and "lint ok", or 2 with every problem listed.

- `fit` reads the completed regular-season games of 2014-2025 and the SP+ ratings of the seasons
  before them from `CFB_MODEL.GAME` and `RATING_SP` of the target (read-only `SELECT`s, no lock,
  0 CFBD calls, never the virtual schema), estimates the constants of the score model
  (`engine/fit.py`), prints them with the games behind each and the fit quality, and writes the
  seed `sql/sim/010_sim_params.sql` (`--output` another file, `--dry-run` nothing). Review the
  diff and commit it; `make sim` deploys it.

Exit codes: 0 ok, 1 refused (the run lock is held), 2 error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from engine import bundle
from engine import deploy as sim_deploy
from engine import fit as fit_module
from engine.deploy import SimLintError, SimOptions
from loader.runlock import RunLockError, RunLockRefused
from vs.host import target
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace, NamespaceError

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ERROR = 2

Connector = Callable[[target.Target], Any]


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
        options: SimOptions,
    ) -> None:
        self._args = args
        self._connector = connector
        self._resolve = resolve
        self._options = options

    def deploy(self) -> int:
        namespace = Namespace(self._args.prefix)
        objects = sim_deploy.prepare(
            namespace, self._options.directory, self._options.data_directory
        )
        script = bundle.build(namespace, self._options.engine_directory)
        if self._args.lint_only:
            _print(
                f"lint ok: {len(objects)} objects, engine script {len(script.create):,} "
                f"characters, ENGINE_VERSION {script.version}; nothing sent, 0 CFBD calls"
            )
            return EXIT_OK
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            report = sim_deploy.deploy_sim(connection, namespace, self._options)
        finally:
            connection.close()
        for line in report.lines:
            _print(line)
        return EXIT_OK

    def fit(self) -> int:
        namespace = Namespace(self._args.prefix)
        connection = self._connector(self._resolve(self._args.target, dsn=self._args.dsn))
        try:
            games, ratings = fit_module.read_inputs(connection, namespace.model_schema)
        finally:
            connection.close()
        table = fit_module.build_table(games, ratings)
        result = fit_module.fit_constants(table)
        for line in fit_module.summary_lines(result, table):
            _print(line)
        if self._args.dry_run:
            _print("dry run: nothing written")
            return EXIT_OK
        output = Path(self._args.output)
        output.write_text(fit_module.render_seed(result), encoding="utf-8")
        _print(f"seed written: {output}; review the diff, then make sim to deploy it")
        return EXIT_OK


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
        prog="python -m scripts.sim",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    deploy = commands.add_parser(
        "deploy", parents=[_common()], help="lint and deploy CFB_SIM and CFB_INFRA.SIM_ENGINE"
    )
    deploy.add_argument(
        "--lint-only",
        action="store_true",
        help="lint, render and build only: no connection, exit 0 or 2",
    )
    fit_command = commands.add_parser(
        "fit", parents=[_common()], help="fit the score model's constants and write the seed"
    )
    fit_command.add_argument(
        "--output", default=str(fit_module.SEED_PATH), help="the seed file to write"
    )
    fit_command.add_argument(
        "--dry-run", action="store_true", help="print the fit and write nothing"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    connector: Connector = connect_sys,
    resolve: Callable[..., target.Target] = target.resolve,
    options: SimOptions | None = None,
) -> int:
    args = _parser().parse_args(argv)
    command = _Command(args, connector, resolve, options or SimOptions())
    try:
        run: Callable[[], int] = getattr(command, args.command)
        return run()
    except RunLockRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except SimLintError as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return EXIT_ERROR
    except (
        DeployError,
        bundle.BundleError,
        RunLockError,
        target.TargetError,
        NamespaceError,
        OSError,
    ) as error:
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
