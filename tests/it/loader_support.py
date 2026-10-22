"""Pure helpers of the loader side of the integration harness; nothing here needs a database.

- Plan variants: the files `tests/it/fixtures/load-plan.<variant>.yaml`, and `PlanState`, what a
  deployment of one variant with its `LOADER_CONFIG` overrides looks like.
- SQL text: script calls (`EXECUTE SCRIPT`, never `WITH OUTPUT`), `KILL STATEMENT`, the UTC clock.
- Cleanup scope: which tables a reset truncates (the plan tables keep the deployed plan).
- The private snapshot directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from tests.it.support import HarnessError
from vs.cfbd_vs.sqlrender import sql_literal
from vs.host.namespace import Namespace

VARIANT_DIR = Path(__file__).parent / "fixtures"
VARIANT_PATTERN = "load-plan.*.yaml"
BASE_VARIANT = "base"
DEFAULT_NOW = "2020-10-20"
NOW_OVERRIDE = "now_override"
PLAN_TABLES = ("LOAD_PLAN", "LOAD_PLAN_GAPS", "LOADER_CONFIG", "REFRESH_STEPS", "SEASON_NUMBERS")
UTC_NOW = "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')"
PRIVATE_DIRECTORY_MODE = 0o700

_VARIANT_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_QUALIFIED_SCRIPT = re.compile(r"[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*")

ConfigItems = tuple[tuple[str, str], ...]


def variant_names() -> list[str]:
    """The plan variants, sorted: `base`, `gaps`, `refresh-steps`, ..."""
    return sorted(
        path.name.removeprefix("load-plan.").removesuffix(".yaml")
        for path in VARIANT_DIR.glob(VARIANT_PATTERN)
    )


def variant_text(name: str) -> str:
    """The YAML text of a plan variant; unknown names list the known ones."""
    known = variant_names()
    if not _VARIANT_NAME.fullmatch(name) or name not in known:
        raise HarnessError(f"no plan variant {name!r}; known: {', '.join(known)}")
    return (VARIANT_DIR / f"load-plan.{name}.yaml").read_text(encoding="utf-8")


def freeze(config: Mapping[str, str] | None) -> ConfigItems:
    """`LOADER_CONFIG` overrides as a sorted, comparable tuple."""
    return tuple(sorted((config or {}).items()))


@dataclass(frozen=True)
class PlanState:
    """One deployment of the loader: the plan variant and its `LOADER_CONFIG` overrides."""

    variant: str
    config: ConfigItems

    def config_overrides(self) -> dict[str, str]:
        return dict(self.config)

    @property
    def now(self) -> str | None:
        """The test clock this deployment installs, if any."""
        return self.config_overrides().get(NOW_OVERRIDE)


def script_call_sql(script: str, *arguments: object) -> str:
    """`EXECUTE SCRIPT <schema>.<name>(<literals>)`; the result table needs no `WITH OUTPUT`."""
    if not _QUALIFIED_SCRIPT.fullmatch(script):
        raise HarnessError(f"{script!r} is not a qualified UPPER_CASE script name")
    return f"EXECUTE SCRIPT {script}({', '.join(sql_literal(value) for value in arguments)})"


def kill_statement_sql(session_id: int | Decimal) -> str:
    """`KILL STATEMENT IN SESSION <id>`: ends the statement and keeps the session and its rows."""
    if isinstance(session_id, bool) or not isinstance(session_id, int | Decimal):
        raise HarnessError(f"a session id is a number, got {session_id!r}")
    return f"KILL STATEMENT IN SESSION {int(session_id)}"


def cleanup_schemas(namespace: Namespace) -> list[str]:
    """The schemas whose tables a reset may truncate."""
    return [
        namespace.core_schema,
        namespace.meta_schema,
        namespace.stage_schema,
        namespace.infra_schema,
    ]


def tables_to_truncate(
    tables: Iterable[tuple[str, str]], namespace: Namespace
) -> list[tuple[str, str]]:
    """`(schema, table)` rows without the plan tables of the meta schema, in the given order."""
    kept = {(namespace.meta_schema, name) for name in PLAN_TABLES}
    return [row for row in tables if row not in kept]


def private_directory(path: Path) -> Path:
    """Create `path` (and parents) readable by its owner only; tighten one that exists."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(PRIVATE_DIRECTORY_MODE)
    return path
