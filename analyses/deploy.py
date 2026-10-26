"""Install the CFB_API serving views under the shared DEPLOY run lock."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loader.runlock import HeldLock, run_lock
from vs.host import sqlobjects
from vs.host.deploy import DeployError
from vs.host.namespace import Namespace
from vs.host.sqlobjects import DeployReport, SqlObject

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "sql" / "api"
LIVE_DIR = ROOT / "sql" / "api_live"
API_REFS = (
    "{API_SCHEMA}.*",
    "{MODEL_SCHEMA}.*",
    "{SIM_SCHEMA}.*",
    "{META_SCHEMA}.REFRESH_LOG",
    "{META_SCHEMA}.LOAD_RUN",
    "{META_SCHEMA}.FETCH_LOG",
)
LIVE_REFS = ("{VS_NAME}.GAMES_PREVIEW", "{META_SCHEMA}.QUOTA")


class ApiLintError(DeployError):
    """A serving file violates the schema and reference contract."""


@dataclass(frozen=True)
class ApiReport:
    api: DeployReport
    live: DeployReport
    run_id: int
    lines: tuple[str, ...]


def prepare(
    namespace: Namespace, api_dir: Path = API_DIR, live_dir: Path = LIVE_DIR
) -> tuple[list[SqlObject], list[SqlObject]]:
    """Lint and render both serving directories without opening a database connection."""
    api = sqlobjects.load(api_dir)
    live = sqlobjects.load(live_dir)
    if not api:
        raise ApiLintError(f"no .sql serving views in {api_dir}")
    reserved = sqlobjects.load_reserved_keywords()
    problems = sqlobjects.lint(
        api, allowed_refs=API_REFS, reserved=reserved, schema_placeholder="{API_SCHEMA}"
    )
    problems.extend(
        sqlobjects.lint(
            live, allowed_refs=LIVE_REFS, reserved=reserved, schema_placeholder="{API_LIVE_SCHEMA}"
        )
    )
    problems.extend(
        f"{item.file}: only VIEW objects belong in serving schemas"
        for item in [*api, *live]
        if item.kind != "VIEW"
    )
    if problems:
        raise ApiLintError(
            f"{len(problems)} serving lint problem(s); nothing was sent:\n  "
            + "\n  ".join(problems)
        )
    return sqlobjects.render(api, namespace), sqlobjects.render(live, namespace)


def deploy_api(
    connection: Any,
    namespace: Namespace,
    *,
    lock: HeldLock | None = None,
) -> ApiReport:
    """Install the two serving schemas without reading CFBD virtual tables."""
    api, live = prepare(namespace)

    def install(held: HeldLock) -> ApiReport:
        api_report = sqlobjects.deploy(connection, namespace.api_schema, api, lock=held)
        live_report = sqlobjects.deploy(connection, namespace.api_live_schema, live, lock=held)
        lines = (
            f"lint: {len(api)} API and {len(live)} live views clean",
            *api_report.lines,
            *live_report.lines,
            f"serving schemas deployed as DEPLOY run {held.run_id}; 0 CFBD calls",
        )
        return ApiReport(api_report, live_report, held.run_id, lines)

    if lock is not None:
        return install(lock)
    with run_lock(connection, namespace, "DEPLOY", "make api") as held:
        return install(held)
