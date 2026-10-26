"""Deploy `CFB_MODEL`: lint `sql/model/`, render it for a namespace, install it under the run lock.

`deploy_model(connection, namespace)` is the one entry (`make model`, `make deploy`, the
integration harness):

1. `load` and `lint` the files of `sql/model/` (`vs/host/sqlobjects.py`). Any problem raises
   `ModelLintError` listing every one of them, before anything is sent to a database.
2. `render` the files for the namespace.
3. Take the loader's run lock as kind `DEPLOY` (`loader/runlock.py`): it refuses while a loader run,
   a snapshot or a restore holds the lock (`RunLockRefused`), and it needs the loader deployed
   (`make deploy`). The lock is taken before the transaction because acquiring it commits.
   `make deploy` passes the lock it already holds instead, so its three steps are one `DEPLOY`
   run ([35]).
4. `sqlobjects.deploy`: one transaction, `CREATE SCHEMA`, orphans dropped (a view that became a
   table is dropped first), objects in file order, the step's log table
   (`CFB_META.MODEL_CHECK_LOG`, created if missing) and the refresh step script
   (`CFB_INFRA.REFRESH_MODEL`), a compile pass over every view, comment read-back, `COMMIT`; any
   failure rolls everything back.

Derived tables (decision [40]): a `TABLE` file whose `INSERT` reads other objects is a materialized
view. The deploy recreates and fills it in file order inside the transaction, so a deployed model is
current; `CFB_INFRA.REFRESH_MODEL` rebuilds the same tables after every load (`model/step.py`).

The step script is installed in the same transaction, so a failed model deploy also leaves the
previous script in place. `ModelOptions.step_script` returns its `CREATE OR REPLACE LUA SCRIPT`
text for a namespace and the rendered objects (the derived tables' `INSERT`s are compiled into it),
or `None` when `model/lua/refresh_model.lua` does not exist.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loader.runlock import HeldLock, run_lock
from model import step as model_step
from vs.host import metaddl, sqlobjects
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace
from vs.host.sqlobjects import DeployReport, SqlObject

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "sql" / "model"
STEP_SOURCE = model_step.STEP_SOURCE
LOCK_KIND = "DEPLOY"
LOCK_ARGS = "make model"

StepScript = Callable[[Namespace, Sequence[SqlObject]], str | None]


class ModelLintError(DeployError):
    """The model files break a lint rule; `problems` lists every one, nothing was sent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} lint problem(s) in the model files, nothing was sent:\n  "
            + "\n  ".join(problems)
        )


def default_step_script(namespace: Namespace, objects: Sequence[SqlObject]) -> str | None:
    """The text that creates `<infra>.REFRESH_MODEL` for the rendered `objects`; `None` without
    `model/lua/refresh_model.lua`."""
    if not STEP_SOURCE.exists():
        return None
    return model_step.build_step_script(namespace, objects, STEP_SOURCE)


def check_log_ddl(namespace: Namespace) -> str:
    """`CREATE TABLE IF NOT EXISTS <meta>.MODEL_CHECK_LOG`: the step's log, also on a target whose
    loader was deployed before the table was registered (`make deploy` creates it too)."""
    ddl = metaddl.render_meta_ddl("model_check_log", namespace.meta_schema)
    return ddl.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)


@dataclass(frozen=True)
class ModelOptions:
    """Inputs of one model deployment; everything defaults to the real artifacts."""

    directory: Path = MODEL_DIR
    step_script: StepScript = default_step_script
    lock_args: str = LOCK_ARGS


@dataclass(frozen=True)
class ModelReport:
    """What the deployment did: the objects' report, the lock run and printable lines."""

    objects: DeployReport
    run_id: int
    step_installed: bool
    lines: tuple[str, ...] = field(default=())


def prepare(namespace: Namespace, directory: Path = MODEL_DIR) -> list[SqlObject]:
    """The model objects of `directory`, linted and rendered for `namespace`; sends nothing.

    Raises `ModelLintError` with every problem, `DeployError` for an empty directory.
    """
    objects = sqlobjects.load(directory)
    if not objects:
        raise DeployError(f"no .sql files in {directory}")
    problems = sqlobjects.lint(
        objects,
        allowed_refs=sqlobjects.MODEL_ALLOWED_REFS,
        reserved=sqlobjects.load_reserved_keywords(),
    )
    if problems:
        raise ModelLintError(problems)
    return sqlobjects.render(objects, namespace)


def deploy_model(
    connection: Any,
    namespace: Namespace,
    options: ModelOptions | None = None,
    *,
    lock: HeldLock | None = None,
) -> ModelReport:
    """Lint, render and deploy the model into `namespace.model_schema` under the DEPLOY lock.

    `lock` is a `DEPLOY` lock the caller already holds in this session (`make deploy` deploys the
    virtual schema, the loader and the model under one lock, decision [35]); without it the model
    takes the lock itself, as `options.lock_args`.
    """
    options = options or ModelOptions()
    objects = prepare(namespace, options.directory)
    step = options.step_script(namespace, objects)
    extra = [check_log_ddl(namespace), *([step] if step else [])]
    if lock is not None:
        report = sqlobjects.deploy(
            connection, namespace.model_schema, objects, extra_statements=extra, lock=lock
        )
        run_id = lock.run_id
    else:
        with run_lock(connection, namespace, LOCK_KIND, options.lock_args) as held:
            report = sqlobjects.deploy(
                connection, namespace.model_schema, objects, extra_statements=extra, lock=held
            )
            run_id = held.run_id
    notice = (
        f"Lua script {namespace.infra_schema}.REFRESH_MODEL: replaced"
        if step
        else f"Lua script {namespace.infra_schema}.REFRESH_MODEL: skipped, "
        f"{STEP_SOURCE.name} does not exist"
    )
    derived = [obj.name for obj in model_step.derived_tables(objects)]
    lines = (
        f"lint: {len(objects)} files clean",
        *report.lines,
        f"derived tables filled: {', '.join(derived) if derived else 'none'}",
        notice,
        f"model deployed as DEPLOY run {run_id}; 0 CFBD calls",
    )
    return ModelReport(report, run_id, bool(step), lines)
