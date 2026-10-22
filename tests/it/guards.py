"""Hard guards of the integration tests: they never touch the real CFBD connection.

Three layers, all built from `REAL_CONNECTION` so this file holds no literal of its name:

- `forbidden_references` scans the files of `tests/it` at collection time. The mock connection
  name contains the real one as a prefix, so only an occurrence not followed by `_MOCK` counts.
- `schemas_on_real_connection` is the session-start check on the database: a test virtual
  schema whose `CONNECTION_NAME` is the real connection, or has none and so falls back to it,
  stops the run before anything is deployed or queried.
- `check_property_change` refuses the harness helper that would point a test schema at it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from vs.host.namespace import MOCK_CONNECTION, REAL_CONNECTION

CONNECTION_PROPERTY = "CONNECTION_NAME"
SCHEMA_PROPERTIES = ("INFRA_SCHEMA", "META_SCHEMA", "CORE_SCHEMA")
MOCK_SUFFIX = MOCK_CONNECTION.removeprefix(REAL_CONNECTION)
SKIPPED_FILES = frozenset({"guards.py"})
SKIPPED_DIRECTORIES = frozenset({"__pycache__"})

_FORBIDDEN = re.compile(re.escape(REAL_CONNECTION) + f"(?!{re.escape(MOCK_SUFFIX)})")


class GuardError(RuntimeError):
    """An integration test would reach the real CFBD connection."""


def forbidden_references(root: Path) -> list[str]:
    """`path:line` of every reference to the real connection below `root`."""
    problems: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or path.name in SKIPPED_FILES
            or SKIPPED_DIRECTORIES.intersection(relative.parts)
        ):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        problems += [
            f"{relative}:{number} refers to the real connection {REAL_CONNECTION}"
            for number, line in enumerate(lines, start=1)
            if _FORBIDDEN.search(line)
        ]
    return problems


def schemas_on_real_connection(
    property_rows: Iterable[tuple[str, str, str | None]],
    prefix: str,
    schema_names: Iterable[str] = (),
) -> list[str]:
    """Virtual schemas of the test namespace that would read the real connection.

    `property_rows` are `(schema, property name, value)` rows of the database's virtual-schema
    properties, and `schema_names` the names of all virtual schemas (a schema without a single
    property row only shows up there). A schema belongs to the test namespace when its name or
    one of its infra, meta or core schema properties starts with `prefix`. A missing, empty or
    NULL `CONNECTION_NAME` means the default, which is the real connection.
    """
    properties: dict[str, dict[str, str]] = defaultdict(dict)
    for schema in schema_names:
        properties.setdefault(schema, {})
    for schema, name, value in property_rows:
        properties[schema][name.upper()] = value or ""
    flagged = []
    for schema, values in properties.items():
        in_test_namespace = schema.startswith(prefix) or any(
            values.get(name, "").startswith(prefix) for name in SCHEMA_PROPERTIES
        )
        connection = values.get(CONNECTION_PROPERTY, "").strip().upper() or REAL_CONNECTION
        if in_test_namespace and connection == REAL_CONNECTION:
            flagged.append(schema)
    return sorted(flagged)


def check_property_change(name: str, value: str) -> None:
    """Refuse to point a test virtual schema at the real connection, or to unset the property."""
    if name.upper() != CONNECTION_PROPERTY:
        return
    if not value.strip():
        raise GuardError(
            f"integration tests must not unset {CONNECTION_PROPERTY}: "
            f"an empty value falls back to {REAL_CONNECTION}"
        )
    if value.strip().upper() == REAL_CONNECTION:
        raise GuardError(
            f"integration tests must not set {CONNECTION_PROPERTY} to {REAL_CONNECTION}"
        )
