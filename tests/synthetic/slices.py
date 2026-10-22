"""Slices of API-shaped bodies, and the `CFB_CORE` mirror rows the foundation's flattener makes.

A `Slice` is one stored request: a table, the slice key of the request, the parsed response body
and the time it was fetched. `slice_rows` flattens the body with `vs.cfbd_vs.flatten`, then
appends the `P_*` echoes (parsed from the slice key, parameters not sent are NULL) and the `REQ_*`
columns, in the column order of the catalog table, which is the order of the mirror. So a row is
exactly what a loader would store. A `SliceSet` holds slices and answers with rows, `LOAD_LOG`
registrations and replay recordings; the league and the scaled league are slice sets.
"""

from __future__ import annotations

import dataclasses
import re
import urllib.parse
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

from tests.synthetic import fake
from vs.cfbd_vs.catalog import Param, Table
from vs.cfbd_vs.flatten import data_columns, flatten
from vs.cfbd_vs.replay import Recording

Row = tuple[Any, ...]

REQ_SOURCE = "REPLAY"
REQ_STATUS = 200
REQ_ATTEMPTS = 1
REQ_MS = 5
LOADED_BY = "SYNTHETIC"
_PLACEHOLDER = re.compile(r"\{([^/{}]+)\}")
_INTEGER = re.compile(r"-?\d+")


@dataclass(frozen=True)
class Slice:
    """One stored slice. `fetched_at` is a naive UTC time, as the mirrors store `REQ_AT`."""

    table: str
    slice_key: str
    body: Any
    fetched_at: datetime
    is_final: bool

    def table_def(self) -> Table:
        return fake.catalog().table(self.table)


def mirror_columns(table: str) -> tuple[str, ...]:
    """The column names of a `CFB_CORE` mirror, in order."""
    return tuple(column.name for column in fake.catalog().table(table).columns)


def echo_values(table: Table, slice_key: str) -> dict[str, Any]:
    """The `P_*` echo column values of a slice key; parameters that were not sent are NULL."""
    path, _, query = slice_key.partition("?")
    path_values = _path_values(table, path)
    query_values = {
        urllib.parse.unquote(name): urllib.parse.unquote(value)
        for name, _, value in (item.partition("=") for item in query.split("&") if item)
    }
    values: dict[str, Any] = {}
    for param in table.params:
        source = path_values if param.location == "path" else query_values
        text = source.get(param.name)
        values[param.column] = None if text is None else _typed(param, text)
    return values


def slice_rows(item: Slice) -> list[Row]:
    """The mirror rows of a slice: flattened body, `P_*` echoes and `REQ_*`, catalog order."""
    table = item.table_def()
    names = [column.name for column in data_columns(table)]
    flattened = flatten(table, item.body)
    echoes = echo_values(table, item.slice_key)
    meta = {
        "REQ_SLICE": item.slice_key,
        "REQ_SOURCE": REQ_SOURCE,
        "REQ_STATUS": REQ_STATUS,
        "REQ_ATTEMPTS": REQ_ATTEMPTS,
        "REQ_MS": REQ_MS,
        "REQ_ROWS": len(flattened),
        "REQ_AT": item.fetched_at,
    }
    order = [column.name for column in table.columns]
    rows = []
    for values in flattened:
        cells = {**dict(zip(names, values, strict=True)), **echoes, **meta}
        rows.append(tuple(cells[name] for name in order))
    return rows


def load_log_row(item: Slice, rows: int) -> Row:
    """The `LOAD_LOG` registration of a slice that stored `rows` rows."""
    return (item.table, item.slice_key, item.is_final, rows, item.fetched_at, LOADED_BY)


def recording_of(item: Slice) -> Recording:
    """The replay recording of a slice: the same body, fetched at the slice's time (UTC)."""
    return Recording(
        slice_key=item.slice_key,
        status=REQ_STATUS,
        ms=REQ_MS,
        requested_at=item.fetched_at.replace(tzinfo=UTC),
        body=fake.to_bytes(item.body).decode("utf-8"),
        attempts=REQ_ATTEMPTS,
    )


@dataclass(frozen=True)
class SliceSet:
    """An ordered set of slices; a slice key is unique per table."""

    slices: tuple[Slice, ...]

    def __post_init__(self) -> None:
        keys = [(item.table, item.slice_key) for item in self.slices]
        if len(set(keys)) != len(keys):
            raise ValueError("a slice key is registered twice for one table")

    def tables(self) -> tuple[str, ...]:
        """The stored tables, in first-seen order."""
        return tuple(dict.fromkeys(item.table for item in self.slices))

    def of(self, table: str) -> list[Slice]:
        return [item for item in self.slices if item.table == table]

    def get(self, table: str, slice_key: str) -> Slice:
        for item in self.slices:
            if item.table == table and item.slice_key == slice_key:
                return item
        raise KeyError(f"no slice {slice_key} for {table}")

    def rows(self, table: str) -> list[Row]:
        """Every stored row of a table, slice after slice."""
        return [row for item in self.of(table) for row in slice_rows(item)]

    def table_rows(self) -> dict[str, list[Row]]:
        return {table: self.rows(table) for table in self.tables()}

    def load_log_rows(self) -> list[Row]:
        """`LOAD_LOG` registrations: table, slice key, final flag, rows, load time, loader."""
        return [load_log_row(item, len(slice_rows(item))) for item in self.slices]

    def recordings(self, predicate: Callable[[Slice], bool]) -> list[Recording]:
        """Replay recordings of the slices that satisfy `predicate`."""
        return [recording_of(item) for item in self.slices if predicate(item)]

    def with_slices(self, slices: Iterable[Slice]) -> Self:
        return dataclasses.replace(self, slices=tuple(slices))

    def mapped(self, change: Callable[[Slice], Slice]) -> Self:
        """A copy in which every slice went through `change`."""
        return self.with_slices(change(item) for item in self.slices)

    def iter_rows(self, table: str) -> Iterator[Row]:
        for item in self.of(table):
            yield from slice_rows(item)


def _path_values(table: Table, path: str) -> dict[str, str]:
    names: list[str] = []
    pieces: list[str] = []
    position = 0
    for placeholder in _PLACEHOLDER.finditer(table.endpoint):
        pieces.append(re.escape(table.endpoint[position : placeholder.start()]))
        pieces.append("([^/]+)")
        names.append(placeholder.group(1))
        position = placeholder.end()
    pieces.append(re.escape(table.endpoint[position:]))
    matched = re.fullmatch("".join(pieces), path)
    if matched is None:
        raise ValueError(f"path {path!r} does not match endpoint {table.endpoint}")
    return {
        name: urllib.parse.unquote(value)
        for name, value in zip(names, matched.groups(), strict=True)
    }


def _typed(param: Param, text: str) -> Any:
    if param.type == "integer" and _INTEGER.fullmatch(text):
        return int(text)
    if param.type == "number":
        return float(text)
    if param.type == "boolean" and text in ("true", "false"):
        return text == "true"
    if param.type in ("integer", "boolean"):
        raise ValueError(f"parameter {param.name} is {param.type}, got {text!r}")
    return text
