"""Render the `CFB_META` table DDL (`sql/meta/*.sql`) and other namespace SQL files.

Two sets of tables: the foundation's (`meta_ddl_names`: `LOAD_LOG`, `FETCH_LOG`, created by the VS
deploy) and the loader's (`loader_meta_ddl_names`, created by the loader deploy). The loader's views
(`sql/loader/*.sql`, `loader_view_names` in creation order) read those tables and each other. Any
SQL file may use the placeholders `{META_SCHEMA}`, `{CORE_SCHEMA}`, `{STAGE_SCHEMA}`,
`{RESTORE_SCHEMA}`, `{INFRA_SCHEMA}`, `{MODEL_SCHEMA}`, `{SIM_SCHEMA}` and `{VS_NAME}`;
`render_namespace_sql` fills them from a `Namespace`.
"""

from __future__ import annotations

import re
from pathlib import Path

from vs.host.namespace import Namespace

META_SCHEMA_PLACEHOLDER = "{META_SCHEMA}"
SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
META_DDL_DIR = SQL_DIR / "meta"
LOADER_VIEW_DIR = SQL_DIR / "loader"
META_DDL_NAMES = ("load_log", "fetch_log")
LOADER_META_DDL_NAMES = (
    "load_plan",
    "load_plan_gaps",
    "loader_config",
    "season_numbers",
    "refresh_steps",
    "load_run",
    "refresh_log",
    "load_failures",
    "model_check_log",
)
LOADER_VIEW_NAMES = (
    "loader_settings",
    "load_slices",
    "load_orphans",
    "backfill_queue",
    "load_status",
    "quota",
)

_SCHEMA_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_SQL_COMMENT = re.compile(r"^--.*\n", re.MULTILINE)
_OPEN_PLACEHOLDER = re.compile(r"\{[A-Z][A-Z_]*\}")


class MetaDdlError(ValueError):
    """The requested meta DDL does not exist or the schema name is unusable."""


def meta_ddl_names() -> tuple[str, ...]:
    """The foundation's meta tables in creation order."""
    return META_DDL_NAMES


def loader_meta_ddl_names() -> tuple[str, ...]:
    """The loader's meta tables in creation order."""
    return LOADER_META_DDL_NAMES


def loader_view_names() -> tuple[str, ...]:
    """The loader's views in creation order: each reads only tables and views before it."""
    return LOADER_VIEW_NAMES


def render_loader_view(name: str, namespace: Namespace) -> str:
    """One `CREATE OR REPLACE VIEW` statement of `sql/loader/<name>.sql` for `namespace`."""
    if name not in LOADER_VIEW_NAMES:
        raise MetaDdlError(f"unknown loader view {name!r}, expected one of {LOADER_VIEW_NAMES}")
    return render_sql_file(LOADER_VIEW_DIR / f"{name}.sql", namespace)


def render_meta_ddl(name: str, meta_schema: str) -> str:
    """One CREATE TABLE statement, comment-free and without a trailing semicolon."""
    known = META_DDL_NAMES + LOADER_META_DDL_NAMES
    if name not in known:
        raise MetaDdlError(f"unknown meta DDL {name!r}, expected one of {known}")
    if not _SCHEMA_NAME.fullmatch(meta_schema):
        raise MetaDdlError(f"schema name {meta_schema!r} must be an UPPER_CASE identifier")
    text = (META_DDL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    statement = _SQL_COMMENT.sub("", text).strip().removesuffix(";")
    return statement.replace(META_SCHEMA_PLACEHOLDER, meta_schema) + "\n"


def placeholder_values(namespace: Namespace) -> dict[str, str]:
    """Every placeholder (with braces) and the name it takes in `namespace`."""
    return {
        "{META_SCHEMA}": namespace.meta_schema,
        "{CORE_SCHEMA}": namespace.core_schema,
        "{STAGE_SCHEMA}": namespace.stage_schema,
        "{RESTORE_SCHEMA}": namespace.restore_schema,
        "{INFRA_SCHEMA}": namespace.infra_schema,
        "{MODEL_SCHEMA}": namespace.model_schema,
        "{SIM_SCHEMA}": namespace.sim_schema,
        "{API_SCHEMA}": namespace.api_schema,
        "{API_LIVE_SCHEMA}": namespace.api_live_schema,
        "{VS_NAME}": namespace.vs_name,
    }


def fill_placeholders(text: str, namespace: Namespace) -> str:
    """`text` with every placeholder of `placeholder_values` replaced; comments are kept.

    An upper-case placeholder that is not known is an error; lower-case braces (slice-key
    templates) are left alone.
    """
    for placeholder, value in placeholder_values(namespace).items():
        text = text.replace(placeholder, value)
    open_ones = sorted(set(_OPEN_PLACEHOLDER.findall(text)))
    if open_ones:
        raise MetaDdlError(f"unknown placeholder {', '.join(open_ones)} in the SQL text")
    return text


def render_namespace_sql(text: str, namespace: Namespace) -> str:
    """`text` without whole-line comments and the trailing semicolon, placeholders filled.

    Fills `{META_SCHEMA}`, `{CORE_SCHEMA}`, `{STAGE_SCHEMA}`, `{RESTORE_SCHEMA}`, `{INFRA_SCHEMA}`,
    `{MODEL_SCHEMA}`, `{SIM_SCHEMA}` and `{VS_NAME}` with the names of `namespace`. An upper-case
    placeholder that is not one of them is an error; lower-case braces (slice-key templates) are
    left alone.
    """
    statement = _SQL_COMMENT.sub("", text).strip().removesuffix(";")
    return fill_placeholders(statement, namespace) + "\n"


def render_sql_file(path: Path, namespace: Namespace) -> str:
    """`render_namespace_sql` of a file."""
    return render_namespace_sql(path.read_text(encoding="utf-8"), namespace)
