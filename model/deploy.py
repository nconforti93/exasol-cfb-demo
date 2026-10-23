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
4. `sqlobjects.deploy`: one transaction, `CREATE SCHEMA`, orphans dropped, objects in file order,
   the refresh step script (`CFB_INFRA.REFRESH_MODEL`, when it exists), a compile pass over every
   view, comment read-back, `COMMIT`; any failure rolls everything back.

The step script is installed in the same transaction, so a failed model deploy also leaves the
previous script in place. Until task 4.1 adds `model/lua/refresh_model.lua` there is none, and the
report says so. `ModelOptions.step_script` is the one place that 4.1 changes: it returns the
`CREATE OR REPLACE LUA SCRIPT` text for a namespace.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loader.runlock import HeldLock, run_lock
from vs.host import sqlobjects
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace
from vs.host.sqlobjects import DeployReport, SqlObject

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "sql" / "model"
STEP_SOURCE = ROOT / "model" / "lua" / "refresh_model.lua"
LOCK_KIND = "DEPLOY"
LOCK_ARGS = "make model"

StepScript = Callable[[Namespace], str | None]


class ModelLintError(DeployError):
    """The model files break a lint rule; `problems` lists every one, nothing was sent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"{len(problems)} lint problem(s) in the model files, nothing was sent:\n  "
            + "\n  ".join(problems)
        )


def default_step_script(namespace: Namespace) -> str | None:
    """The text that creates `<infra>.REFRESH_MODEL`; `None` until `refresh_model.lua` exists."""
    if not STEP_SOURCE.exists():
        return None
    raise DeployError(
        f"{STEP_SOURCE} exists but is not bundled yet: add its entry to loader/bundle.py and "
        "return the bundled text from model.deploy.default_step_script"
    )


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
    step = options.step_script(namespace)
    extra = [step] if step else []
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
        f"{STEP_SOURCE.relative_to(ROOT)} does not exist yet"
    )
    lines = (
        f"lint: {len(objects)} files clean",
        *report.lines,
        notice,
        f"model deployed as DEPLOY run {run_id}; 0 CFBD calls",
    )
    return ModelReport(report, run_id, bool(step), lines)
