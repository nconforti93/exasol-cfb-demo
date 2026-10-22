"""Render the `CFB_META` table DDL (`sql/meta/*.sql`) for a real or prefixed namespace."""

from __future__ import annotations

import re
from pathlib import Path

META_SCHEMA_PLACEHOLDER = "{META_SCHEMA}"
META_DDL_DIR = Path(__file__).resolve().parents[2] / "sql" / "meta"
META_DDL_NAMES = ("load_log", "fetch_log")

_SCHEMA_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_SQL_COMMENT = re.compile(r"^--.*\n", re.MULTILINE)


class MetaDdlError(ValueError):
    """The requested meta DDL does not exist or the schema name is unusable."""


def meta_ddl_names() -> tuple[str, ...]:
    """The meta tables in creation order."""
    return META_DDL_NAMES


def render_meta_ddl(name: str, meta_schema: str) -> str:
    """One CREATE TABLE statement, comment-free and without a trailing semicolon."""
    if name not in META_DDL_NAMES:
        raise MetaDdlError(f"unknown meta DDL {name!r}, expected one of {META_DDL_NAMES}")
    if not _SCHEMA_NAME.fullmatch(meta_schema):
        raise MetaDdlError(f"schema name {meta_schema!r} must be an UPPER_CASE identifier")
    text = (META_DDL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    statement = _SQL_COMMENT.sub("", text).strip().removesuffix(";")
    return statement.replace(META_SCHEMA_PLACEHOLDER, meta_schema) + "\n"
