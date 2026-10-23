"""Deploy `CFB_SIM`: lint `sql/sim/`, render it for a namespace, install it under the run lock.

`deploy_sim(connection, namespace)` is the one entry (`make sim`, the integration harness):

1. `load` and `lint` the files of `sql/sim/` (views, functions, reference tables) and of
   `sql/sim/data/` (the data tables) with `vs/host/sqlobjects.py`. Any problem raises
   `SimLintError` listing every one of them, before anything is sent to a database.
2. `render` the files for the namespace and build the engine script (`engine/bundle.py`).
3. Take the loader's run lock as kind `DEPLOY` (`loader/runlock.py`): it refuses while a loader run,
   a snapshot or a restore holds the lock (`RunLockRefused`), and it needs the loader deployed
   (`make deploy`). The lock is taken before the transaction because acquiring it commits.
   A caller that already holds a `DEPLOY` lock passes it instead (slice 3's `make deploy`).
4. `sqlobjects.deploy`: one transaction, `CREATE SCHEMA IF NOT EXISTS`, orphans dropped, the data
   tables created when missing, the reference tables, functions and views in file order, then
   `CFB_INFRA.SIM_ENGINE` and its comment, a compile pass over every view, comment read-back,
   `COMMIT`; any failure rolls everything back, so the previous schema and script stay.

Run data is never lost: the data tables are `CREATE TABLE IF NOT EXISTS` and in the `keep` list, so
a redeploy replaces the reference tables (`SIM_PARAMS`, `SEASON_RULES`), the views and the script,
and leaves every row of the data tables as it was. A data table whose columns or comments differ
from its file is reported in the lines (`differs: ...`), not migrated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine import bundle
from loader.runlock import HeldLock, run_lock
from vs.host import sqlobjects
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace
from vs.host.sqlobjects import DeployReport, SqlObject

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "sql" / "sim"
DATA_DIR = SIM_DIR / "data"
LOCK_KIND = "DEPLOY"
LOCK_ARGS = "make sim"
KEPT_TABLES = (
    "SIM_RUN",
    "SIM_RUN_TEAM",
    "SIM_RUN_GAME",
    "SIM_RUN_PARAM",
    "SIM_STAGE",
    "SIM_TEAM",
    "SIM_PATH",
    "TEAM_WINS",
    "CONF_CHAMP",
    "LEVERAGE",
    "PREDICTION",
)
"""The data tables a deploy never drops, whether or not a file defines them yet (`PREDICTION`
arrives with slice 2)."""


class SimLintError(DeployError):
    """The simulation files break a lint rule; `problems` lists every one, nothing was sent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} lint problem(s) in the simulation files, nothing was sent:\n  "
            + "\n  ".join(problems)
        )


@dataclass(frozen=True)
class SimOptions:
    """Inputs of one deployment; everything defaults to the real artifacts."""

    directory: Path = SIM_DIR
    data_directory: Path = DATA_DIR
    engine_directory: Path = bundle.ENGINE_DIR
    lock_args: str = LOCK_ARGS


@dataclass(frozen=True)
class SimReport:
    """What the deployment did: the objects' report, the engine version, the lock run, lines."""

    objects: DeployReport
    engine_version: str
    run_id: int
    lines: tuple[str, ...] = field(default=())


def prepare(
    namespace: Namespace, directory: Path = SIM_DIR, data_directory: Path = DATA_DIR
) -> list[SqlObject]:
    """The objects of `directory` and `data_directory`, linted and rendered for `namespace`.

    Data tables come first, so views and reference tables find their tables in file order.
    Sends nothing. Raises `SimLintError` with every problem, `DeployError` for an empty directory.
    """
    objects = [*sqlobjects.load(data_directory), *sqlobjects.load(directory)]
    if not objects:
        raise DeployError(f"no .sql files in {directory} or {data_directory}")
    problems = sqlobjects.lint(
        objects,
        allowed_refs=sqlobjects.SIM_ALLOWED_REFS,
        reserved=sqlobjects.load_reserved_keywords(),
        schema_placeholder=sqlobjects.SIM_SCHEMA_PLACEHOLDER,
    )
    if problems:
        raise SimLintError(problems)
    return sqlobjects.render(objects, namespace)


def deploy_sim(
    connection: Any,
    namespace: Namespace,
    options: SimOptions | None = None,
    *,
    lock: HeldLock | None = None,
) -> SimReport:
    """Lint, render and deploy `CFB_SIM` and `SIM_ENGINE` under the DEPLOY run lock.

    `lock` is a `DEPLOY` lock the caller already holds in this session; without it the deploy
    takes the lock itself, as `options.lock_args`.
    """
    options = options or SimOptions()
    objects = prepare(namespace, options.directory, options.data_directory)
    try:
        script = bundle.build(namespace, options.engine_directory)
    except (bundle.BundleError, OSError) as error:
        raise DeployError(str(error)) from None
    if lock is not None:
        report = _install(connection, namespace, objects, script, lock)
        run_id = lock.run_id
    else:
        with run_lock(connection, namespace, LOCK_KIND, options.lock_args) as held:
            report = _install(connection, namespace, objects, script, held)
            run_id = held.run_id
    lines = (
        f"lint: {len(objects)} files clean",
        *report.lines,
        f"script {namespace.infra_schema}.{bundle.SCRIPT_NAME}: replaced, "
        f"ENGINE_VERSION {script.version}, {len(script.create):,} characters",
        f"simulation schema deployed as DEPLOY run {run_id}; 0 CFBD calls",
    )
    return SimReport(report, script.version, run_id, lines)


def _install(
    connection: Any,
    namespace: Namespace,
    objects: list[SqlObject],
    script: bundle.EngineScript,
    lock: HeldLock,
) -> DeployReport:
    report = sqlobjects.deploy(
        connection,
        namespace.sim_schema,
        objects,
        extra_statements=script.statements,
        keep=KEPT_TABLES,
        lock=lock,
    )
    lock.detail = f"{report.summary}; engine {script.version}"
    return report
