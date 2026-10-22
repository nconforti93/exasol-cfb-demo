"""Identifier rules and SQL type mapping for the CFBD virtual schema (stdlib only)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from vs.cfbd_vs.sqltypes import MAX_VARCHAR_SIZE, SqlTypeError, decimal_type, varchar_type

ROW_NO = "ROW_NO"
RAW_JSON_COLUMN = "RAW_JSON"
PARAM_PREFIX = "P_"
POSITION_SUFFIX = "_NO"

DEFAULT_VARCHAR_SIZE = 2000
DATE_TIME_FORMAT = "date-time"

DECIMAL_TYPE = decimal_type(18, 0)
DOUBLE_TYPE = "DOUBLE"
BOOLEAN_TYPE = "BOOLEAN"
TIMESTAMP_TYPE = "TIMESTAMP"
JSON_TYPE = varchar_type(MAX_VARCHAR_SIZE)

META_COLUMNS: dict[str, str] = {
    "REQ_SLICE": varchar_type(2000),
    "REQ_SOURCE": varchar_type(8),
    "REQ_STATUS": decimal_type(3, 0),
    "REQ_ATTEMPTS": decimal_type(3, 0),
    "REQ_MS": decimal_type(9, 0),
    "REQ_ROWS": DECIMAL_TYPE,
    "REQ_AT": TIMESTAMP_TYPE,
}

_LOWER_OR_DIGIT_THEN_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM_THEN_WORD = re.compile(r"([A-Z]+)([A-Z][a-z])")
_NON_ALNUM_RUN = re.compile(r"[^A-Za-z0-9]+")


class NamingError(ValueError):
    """A name or type cannot be mapped to a valid Exasol identifier or type."""


def to_upper_snake(name: str) -> str:
    """Convert a camelCase or free-form name to UPPER_SNAKE, keeping acronyms and digits whole."""
    spaced = _ACRONYM_THEN_WORD.sub(r"\1_\2", _LOWER_OR_DIGIT_THEN_UPPER.sub(r"\1_\2", name))
    snake = _NON_ALNUM_RUN.sub("_", spaced).strip("_").upper()
    if not snake:
        raise NamingError(f"name {name!r} has no letters or digits")
    if snake[0].isdigit():
        raise NamingError(f"name {name!r} maps to {snake!r}, which starts with a digit")
    return snake


def table_name_from_path(path: str) -> str:
    """Derive a table name from an endpoint path: drop path parameters, join segments with `_`."""
    if not path.startswith("/"):
        raise NamingError(f"endpoint path {path!r} must start with '/'")
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    if not segments:
        raise NamingError(f"endpoint path {path!r} has no static segment to name a table")
    return "_".join(to_upper_snake(s) for s in segments)


def assign_table_names(paths: Iterable[str]) -> dict[str, str]:
    """Map every path to its table name, failing and naming both paths on a collision."""
    names: dict[str, str] = {}
    owners: dict[str, str] = {}
    for path in paths:
        name = table_name_from_path(path)
        if name in owners:
            raise NamingError(f"table name {name} is produced by both {owners[name]} and {path}")
        owners[name] = path
        names[path] = name
    return names


def child_table_name(parent_table: str, array_path: Sequence[str]) -> str:
    """Name the child table of an array: the parent table plus the array field path."""
    return "_".join([parent_table, *(to_upper_snake(s) for s in array_path)])


def column_name_from_path(path: Sequence[str]) -> str:
    """Name the column of a (possibly nested) response field."""
    return "_".join(to_upper_snake(s) for s in path)


def param_column_name(param_name: str) -> str:
    """Name the echo column of an API parameter."""
    return PARAM_PREFIX + to_upper_snake(param_name)


def position_column_name(array_path: Sequence[str]) -> str:
    """Name the 1-based position column of a nested array."""
    return column_name_from_path(array_path) + POSITION_SUFFIX


def safe_column_name(name: str, reserved: frozenset[str]) -> str:
    """Append `_` to a name that is an Exasol reserved keyword (the set is injected, uppercase)."""
    return f"{name}_" if name.upper() in reserved else name


def column_sql_type(
    spec_type: str, spec_format: str | None = None, varchar_size: int | None = None
) -> str:
    """Map an OpenAPI response type (or the pseudo type `json`) to an Exasol column type."""
    if spec_type == "integer":
        return DECIMAL_TYPE
    if spec_type == "number":
        return DOUBLE_TYPE
    if spec_type == "boolean":
        return BOOLEAN_TYPE
    if spec_type == "json":
        return JSON_TYPE
    if spec_type == "string":
        if spec_format == DATE_TIME_FORMAT:
            return TIMESTAMP_TYPE
        return _varchar(varchar_size)
    raise NamingError(f"spec type {spec_type!r} has no SQL type mapping")


def param_sql_type(spec_type: str, spec_format: str | None = None) -> str:
    """Map an OpenAPI parameter type to the type of its echo column (strings stay VARCHAR)."""
    if spec_type == "json":
        raise NamingError("a parameter cannot have the type 'json'")
    if spec_type == "string":
        return _varchar(None)
    return column_sql_type(spec_type, spec_format)


def _varchar(size: int | None) -> str:
    try:
        return varchar_type(DEFAULT_VARCHAR_SIZE if size is None else size)
    except SqlTypeError as error:
        raise NamingError(str(error)) from None
