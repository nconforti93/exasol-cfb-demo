"""Flattener: one parsed CFBD response → the typed rows of one catalog table (stdlib only).

The table's position columns define its levels: `ROW_NO` numbers the response root (path `()`:
an array, one row per element; no path: a single object, always 1), every further position
column numbers the array at its path inside the element of the level above. A row is produced
for every element of the deepest level. Value columns read their path from that element,
parent keys read their path (from the row element, crossing array names) from an ancestor
element, `RAW_JSON` is the element itself as JSON text.

Strict: a value that does not fit its column type or size, a wrong root kind, an array that is
not an array or an element that is not an object fails with `FlattenError`, naming the table,
the column (or array) and the offending value type or length, never the value itself. Missing
fields and JSON nulls become NULL; a missing or null array yields no child rows; a null body
yields no rows. `date-time` strings become naive UTC datetimes with at most millisecond
precision. `flatten` returns tuples aligned with `data_columns(table)`; the caller appends the
`P_*` and `REQ_*` values.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from vs.cfbd_vs.catalog import Column, Table
from vs.cfbd_vs.sqltypes import SqlType, SqlTypeError, parse_sql_type

SCHEMA_NAME = "CFBD_VS"
DATA_KINDS = ("position", "parent_key", "value", "raw_json")
TIMESTAMP_FRACTION_DIGITS = 3

_FRACTION = re.compile(r"\d{2}:\d{2}:\d{2}[.,](\d+)")

Element = tuple[int, Any]
Chain = tuple[Element, ...]
Converter = Callable[[Any], Any]


class FlattenError(ValueError):
    """A response does not fit the table, or the table is not a valid flatten plan."""


class _Misfit(ValueError):
    """A value does not fit its column; the caller adds table, column and position."""


def data_columns(table: Table) -> tuple[Column, ...]:
    """The columns `flatten` fills: the leading position, parent-key, value and raw-JSON columns."""
    count = 0
    while count < len(table.columns) and table.columns[count].kind in DATA_KINDS:
        count += 1
    if any(c.kind in DATA_KINDS for c in table.columns[count:]):
        raise FlattenError(f"{_table(table)}: response columns must precede P_ and REQ_ columns")
    return table.columns[:count]


def flatten(table: Table, body: Any) -> list[tuple[Any, ...]]:
    """Flatten a parsed JSON response into rows aligned with `data_columns(table)`."""
    return _Plan(table).rows(body)


@dataclass(frozen=True)
class _Cell:
    column: Column
    level: int
    path: tuple[str, ...]
    convert: Converter | None


class _Plan:
    def __init__(self, table: Table) -> None:
        self.table = table
        columns = data_columns(table)
        positions = [c for c in columns if c.kind == "position"]
        if not positions or columns[0].kind != "position":
            raise FlattenError(f"{_table(table)}: the first column must be a position column")
        self.levels: list[tuple[str, ...] | None] = [positions[0].path]
        for column in positions[1:]:
            if not column.path:
                raise FlattenError(f"{_table(table)}: position column {column.name} has no path")
            self.levels.append(column.path)
        self.depth = len(self.levels) - 1
        self.cells = [
            _Cell(c, positions.index(c), (), None) if c.kind == "position" else self._cell(c)
            for c in columns
        ]
        self.object_elements = not any(c.kind == "value" and c.path == () for c in columns)

    def rows(self, body: Any) -> list[tuple[Any, ...]]:
        return [
            tuple(self._value(cell, chain) for cell in self.cells) for chain in self._chains(body)
        ]

    def _cell(self, column: Column) -> _Cell:
        convert = _converter(self.table, column)
        if column.kind == "raw_json":
            return _Cell(column, self.depth, (), convert)
        if column.path is None:
            raise FlattenError(f"{_table(self.table)}: column {column.name} has no path")
        if column.kind == "value":
            return _Cell(column, self.depth, column.path, convert)
        level, rest = self._decode(column.path)
        if level >= self.depth or not rest:
            raise FlattenError(
                f"{_table(self.table)}: parent key {column.name} does not name a field of an "
                "ancestor element"
            )
        return _Cell(column, level, rest, convert)

    def _decode(self, path: tuple[str, ...]) -> tuple[int, tuple[str, ...]]:
        level, rest = 0, path
        for index in range(1, len(self.levels)):
            array = self.levels[index] or ()
            if rest[: len(array)] != array:
                break
            level, rest = index, rest[len(array) :]
        return level, rest

    def _chains(self, body: Any) -> Iterator[Chain]:
        if body is None:
            return
        if self.levels[0] is None:
            if isinstance(body, list) and self._needs_object(0):
                raise FlattenError(
                    f"{_table(self.table)}: expected a JSON object at the response root, got array"
                )
            roots: Sequence[Element] = [(1, body)]
        else:
            if not isinstance(body, list):
                raise FlattenError(
                    f"{_table(self.table)}: expected a JSON array at the response root, "
                    f"got {_type_name(body)}"
                )
            roots = list(enumerate(body, 1))
        for root in roots:
            self._check_element(root[1], 0, "the response root")
            yield from self._descend((root,))

    def _descend(self, chain: Chain) -> Iterator[Chain]:
        depth = len(chain) - 1
        if depth == self.depth:
            yield chain
            return
        array_path = self.levels[depth + 1] or ()
        where = ".".join(array_path)
        try:
            container = _get(chain[-1][1], array_path)
        except _Misfit as misfit:
            raise FlattenError(
                f"{_table(self.table)}: array {where}: {misfit}{_at(chain)}"
            ) from None
        if container is None:
            return
        if not isinstance(container, list):
            raise FlattenError(
                f"{_table(self.table)}: array {where}: expected a JSON array, "
                f"got {_type_name(container)}{_at(chain)}"
            )
        for position, element in enumerate(container, 1):
            self._check_element(element, depth + 1, f"array {where}")
            yield from self._descend((*chain, (position, element)))

    def _needs_object(self, level: int) -> bool:
        return level < self.depth or self.object_elements

    def _check_element(self, element: Any, level: int, where: str) -> None:
        if self._needs_object(level) and not isinstance(element, dict):
            raise FlattenError(
                f"{_table(self.table)}: {where}: expected a JSON object element, "
                f"got {_type_name(element)}"
            )

    def _value(self, cell: _Cell, chain: Chain) -> Any:
        position, element = chain[cell.level]
        if cell.convert is None:
            return position
        try:
            return cell.convert(_get(element, cell.path))
        except _Misfit as misfit:
            column = cell.column
            raise FlattenError(
                f"{_table(self.table)}: column {column.name} ({column.sql_type}): {misfit}"
                f"{_at(chain)}"
            ) from None


def _get(value: Any, path: tuple[str, ...]) -> Any:
    for index, key in enumerate(path):
        if value is None:
            return None
        if not isinstance(value, dict):
            inside = ".".join(path[:index]) or "the element"
            raise _Misfit(f"{inside} is a JSON {_type_name(value)}, not an object")
        value = value.get(key)
    return value


def _converter(table: Table, column: Column) -> Converter:
    try:
        sql_type = parse_sql_type(column.sql_type or "")
    except SqlTypeError:
        raise FlattenError(
            f"{_table(table)}: column {column.name} has unsupported type {column.sql_type!r}"
        ) from None
    if column.json or column.kind == "raw_json":
        return _json_converter(table, column, sql_type)
    simple = _SIMPLE_CONVERTERS.get(sql_type.kind)
    if simple is not None:
        return simple
    if sql_type.is_integer:
        limit = 10 ** (sql_type.precision or 0)
        return lambda value: _integer(value, limit)
    if sql_type.size is not None:
        size = sql_type.size
        return lambda value: _string(value, size)
    raise FlattenError(
        f"{_table(table)}: column {column.name} has unsupported type {column.sql_type!r}"
    )


def _json_converter(table: Table, column: Column, sql_type: SqlType) -> Converter:
    size = sql_type.size
    if size is None:
        raise FlattenError(f"{_table(table)}: JSON column {column.name} must be a VARCHAR")
    return lambda value: _json_text(value, size)


def _integer(value: Any, limit: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _Misfit(f"expected an integer, got {_type_name(value)}")
    if abs(value) >= limit:
        raise _Misfit(f"integer with {len(str(abs(value)))} digits is out of range")
    return value


def _double(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Misfit(f"expected a number, got {_type_name(value)}")
    try:
        number = float(value)
    except OverflowError:
        raise _Misfit("number is out of range for DOUBLE") from None
    if not math.isfinite(number):
        raise _Misfit("number is not finite")
    return number


def _boolean(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise _Misfit(f"expected a boolean, got {_type_name(value)}")


def _string(value: Any, size: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Misfit(f"expected a string, got {_type_name(value)}")
    if len(value) > size:
        raise _Misfit(f"string of length {len(value)} exceeds {size} characters")
    return value


def _json_text(value: Any, size: int) -> str | None:
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError:
        raise _Misfit("JSON value holds a number that is not finite") from None
    if len(text) > size:
        raise _Misfit(f"JSON text of length {len(text)} exceeds {size} characters")
    return text


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Misfit(f"expected an ISO-8601 date-time string, got {_type_name(value)}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _Misfit(f"string of length {len(value)} is not an ISO-8601 date-time") from None
    fraction = _FRACTION.search(value)
    if fraction and fraction.group(1)[TIMESTAMP_FRACTION_DIGITS:].strip("0"):
        raise _Misfit("date-time has sub-millisecond precision, which TIMESTAMP cannot hold")
    if parsed.tzinfo is None:
        return parsed
    try:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    except OverflowError:
        raise _Misfit("date-time is out of range in UTC") from None


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _table(table: Table) -> str:
    return f"{SCHEMA_NAME}.{table.name}"


def _at(chain: Chain) -> str:
    return " (element " + ".".join(str(position) for position, _ in chain) + ")"


_SIMPLE_CONVERTERS: Mapping[str, Converter] = {
    "DOUBLE": _double,
    "BOOLEAN": _boolean,
    "TIMESTAMP": _timestamp,
}
