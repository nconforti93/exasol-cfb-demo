"""Capture a virtual-schema query into `CFB_META.FETCH_LOG`: one row per slice actually read.

Inputs are the result of the query staged into a table (its `REQ_*` columns) and the manifest in
the leading `/*CFBD {...}*/` comment of the same query's `EXPLAIN VIRTUAL` pushdown SQL. The
manifest lists every planned slice and, in `sources`, the row sources the pushdown SQL reads:
`[]`, `["STORED"]`, `["LIVE"]` or `["REPLAY"]`, or `STORED` together with the fetch source ([21]).

- A slice with staged rows gets one row carrying that slice's `REQ_*` metadata.
- A planned slice without staged rows gets a row only when the SQL fetched it (`sources` holds
  `LIVE` or `REPLAY`): the fetch UDF emits nothing for an empty response, so the row carries
  `ROWS_RETURNED = 0`, that source and NULL status, attempts, latency and request time.
- On a query that does not fetch (stored-only, or nothing read at all) a planned slice without
  staged rows was never requested, so it gets no row. It is not logged as fetched with 0 rows.
  A stored final slice that holds zero rows is indistinguishable from an unstored one there, so it
  is not logged either; read-through queries log such a slice as an empty fetched one.

Logic is pure; the database is touched only through `SqlConnection` (a pyexasol connection fits).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from vs.cfbd_vs.naming import META_COLUMNS

MANIFEST_OPEN = "/*CFBD "
COMMENT_CLOSE = "*/"
MANIFEST_KEYS = (
    "table",
    "fetch_mode",
    "read_through",
    "budget",
    "planned_requests",
    "slice_keys",
    "sources",
)
REQUEST_COLUMNS = tuple(META_COLUMNS)
FETCH_LOG_COLUMNS = (
    "LOGGED_AT",
    "CONTEXT",
    "TABLE_NAME",
    "SLICE_KEY",
    "REQ_SOURCE",
    "HTTP_STATUS",
    "ATTEMPTS",
    "MS",
    "ROWS_RETURNED",
    "REQUESTED_AT",
)
NUMERIC_FETCH_LOG_COLUMNS = frozenset({"HTTP_STATUS", "ATTEMPTS", "MS", "ROWS_RETURNED"})
MAX_CONTEXT_LENGTH = 200
INSERT_BATCH_ROWS = 100
PUSHDOWN_SQL_COLUMN = "PUSHDOWN_SQL"
SOURCE_STORED = "STORED"
FETCH_SOURCES = ("LIVE", "REPLAY")
TIMESTAMP_PRECISION = "milliseconds"


class FetchLogError(ValueError):
    """The manifest or the staged metadata cannot be turned into fetch-log rows."""


class SqlConnection(Protocol):
    """The part of a pyexasol connection used here."""

    def execute(self, query: str, query_params: Mapping[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True)
class Manifest:
    table: str
    fetch_mode: str
    read_through: bool
    budget: int | None
    planned_requests: int
    slice_keys: tuple[str, ...]
    sources: tuple[str, ...]

    @property
    def fetch_source(self) -> str | None:
        """`LIVE` or `REPLAY` when the pushdown SQL fetches, otherwise None."""
        return next((source for source in self.sources if source != SOURCE_STORED), None)


@dataclass(frozen=True)
class FetchLogRow:
    logged_at: datetime
    context: str | None
    table_name: str
    slice_key: str
    source: str
    http_status: int | None
    attempts: int | None
    ms: int | None
    rows_returned: int
    requested_at: datetime | None

    def as_tuple(self) -> tuple[Any, ...]:
        """Values in `FETCH_LOG_COLUMNS` order."""
        return (
            self.logged_at,
            self.context,
            self.table_name,
            self.slice_key,
            self.source,
            self.http_status,
            self.attempts,
            self.ms,
            self.rows_returned,
            self.requested_at,
        )


def parse_manifest(pushdown_sql: str) -> Manifest:
    """Read the manifest of the leading `/*CFBD {...}*/` comment (`*/` is escaped as `*\\/`)."""
    text = pushdown_sql.lstrip()
    if not text.startswith(MANIFEST_OPEN):
        raise FetchLogError("pushdown SQL does not start with a /*CFBD {...}*/ manifest")
    end = text.find(COMMENT_CLOSE)
    if end < 0:
        raise FetchLogError("the CFBD manifest comment is never closed")
    try:
        data = json.loads(text[len(MANIFEST_OPEN) : end])
    except ValueError as error:
        raise FetchLogError(f"the CFBD manifest is not valid JSON: {error}") from None
    return _manifest_from_json(data)


def build_fetch_log_rows(
    manifest: Manifest,
    staged_metadata: Iterable[Mapping[str, Any]],
    *,
    logged_at: datetime,
    context: str | None = None,
) -> list[FetchLogRow]:
    """One row per slice the query read, in manifest order, with that slice's `REQ_*` metadata."""
    if context is not None and len(context) > MAX_CONTEXT_LENGTH:
        raise FetchLogError(f"context is longer than {MAX_CONTEXT_LENGTH} characters")
    by_slice = _metadata_by_slice(manifest, staged_metadata)
    empty_source = manifest.fetch_source
    rows = []
    for slice_key in manifest.slice_keys:
        metadata = by_slice.get(slice_key)
        if metadata is None:
            if empty_source is None:
                continue
            rows.append(
                FetchLogRow(
                    logged_at,
                    context,
                    manifest.table,
                    slice_key,
                    empty_source,
                    None,
                    None,
                    None,
                    0,
                    None,
                )
            )
        else:
            rows.append(_row_from_metadata(manifest, slice_key, metadata, logged_at, context))
    return rows


def explain_pushdown_sql(connection: SqlConnection, query: str) -> str:
    """The pushdown SQL of `EXPLAIN VIRTUAL <query>` (zero API calls); exactly one is expected."""
    statement = connection.execute(f"EXPLAIN VIRTUAL {query}")
    names = list(statement.columns())
    if PUSHDOWN_SQL_COLUMN not in names:
        raise FetchLogError(f"EXPLAIN VIRTUAL returned no {PUSHDOWN_SQL_COLUMN} column: {names}")
    rows = statement.fetchall()
    if len(rows) != 1:
        raise FetchLogError(f"expected exactly one pushdown in EXPLAIN VIRTUAL, got {len(rows)}")
    return str(rows[0][names.index(PUSHDOWN_SQL_COLUMN)])


def read_slice_metadata(
    connection: SqlConnection, staged_table: tuple[str, str]
) -> list[dict[str, Any]]:
    """Distinct `REQ_*` metadata of a staged table, grouped in SQL."""
    columns = ", ".join(REQUEST_COLUMNS)
    rows = connection.execute(
        f"SELECT {columns} FROM {{staged!i}} GROUP BY {columns}", {"staged": staged_table}
    ).fetchall()
    return [dict(zip(REQUEST_COLUMNS, row, strict=True)) for row in rows]


def insert_fetch_log(
    connection: SqlConnection, meta_schema: str, rows: Sequence[FetchLogRow]
) -> None:
    """Append rows to `<meta_schema>.FETCH_LOG`, in batches."""
    for start in range(0, len(rows), INSERT_BATCH_ROWS):
        batch = rows[start : start + INSERT_BATCH_ROWS]
        connection.execute(*_insert_statement(meta_schema, batch))


def capture_fetch_log(
    connection: SqlConnection,
    *,
    staged_table: tuple[str, str],
    pushdown_sql: str,
    meta_schema: str,
    context: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC).replace(tzinfo=None),
) -> list[FetchLogRow]:
    """Read the staged metadata, build one row per slice the query read and insert them."""
    manifest = parse_manifest(pushdown_sql)
    metadata = read_slice_metadata(connection, staged_table)
    rows = build_fetch_log_rows(manifest, metadata, logged_at=now(), context=context)
    insert_fetch_log(connection, meta_schema, rows)
    return rows


def _manifest_from_json(data: Any) -> Manifest:
    if not isinstance(data, dict):
        raise FetchLogError("the CFBD manifest must be a JSON object")
    missing = [key for key in MANIFEST_KEYS if key not in data]
    if missing:
        raise FetchLogError(f"the CFBD manifest lacks {', '.join(missing)}")
    slice_keys = data["slice_keys"]
    if not isinstance(slice_keys, list) or not all(isinstance(key, str) for key in slice_keys):
        raise FetchLogError("manifest slice_keys must be a list of strings")
    if data["planned_requests"] != len(slice_keys):
        raise FetchLogError(
            f"manifest planned_requests is {data['planned_requests']} "
            f"but lists {len(slice_keys)} slice keys"
        )
    return Manifest(
        table=str(data["table"]),
        fetch_mode=str(data["fetch_mode"]),
        read_through=bool(data["read_through"]),
        budget=_budget(data["budget"]),
        planned_requests=len(slice_keys),
        slice_keys=tuple(slice_keys),
        sources=_sources(data["sources"]),
    )


def _budget(value: Any) -> int | None:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise FetchLogError("manifest budget must be an integer or null")


def _sources(value: Any) -> tuple[str, ...]:
    allowed = (SOURCE_STORED, *FETCH_SOURCES)
    if (
        not isinstance(value, list)
        or not all(isinstance(item, str) and item in allowed for item in value)
        or len(set(value)) != len(value)
        or sum(item in FETCH_SOURCES for item in value) > 1
    ):
        raise FetchLogError(
            f"manifest sources must list distinct values of {list(allowed)} "
            f"with at most one fetch source, got {value!r}"
        )
    return tuple(value)


def _metadata_by_slice(
    manifest: Manifest, staged_metadata: Iterable[Mapping[str, Any]]
) -> dict[str, Mapping[str, Any]]:
    planned = set(manifest.slice_keys)
    by_slice: dict[str, Mapping[str, Any]] = {}
    for staged in staged_metadata:
        missing = [column for column in REQUEST_COLUMNS if column not in staged]
        if missing:
            raise FetchLogError(f"staged metadata lacks {', '.join(missing)}")
        slice_key = staged["REQ_SLICE"]
        if slice_key not in planned:
            raise FetchLogError(
                f"staged rows of slice {slice_key!r} were not planned by the manifest"
            )
        if staged["REQ_SOURCE"] not in manifest.sources:
            raise FetchLogError(
                f"staged rows of slice {slice_key!r} come from {staged['REQ_SOURCE']!r}, "
                f"which the manifest sources {list(manifest.sources)} do not read"
            )
        known = by_slice.setdefault(slice_key, staged)
        if any(known[column] != staged[column] for column in REQUEST_COLUMNS):
            raise FetchLogError(f"conflicting request metadata for slice {slice_key!r}")
    return by_slice


def _row_from_metadata(
    manifest: Manifest,
    slice_key: str,
    metadata: Mapping[str, Any],
    logged_at: datetime,
    context: str | None,
) -> FetchLogRow:
    if metadata["REQ_ROWS"] is None:
        raise FetchLogError(f"slice {slice_key!r} has no REQ_ROWS")
    return FetchLogRow(
        logged_at=logged_at,
        context=context,
        table_name=manifest.table,
        slice_key=slice_key,
        source=metadata["REQ_SOURCE"],
        http_status=metadata["REQ_STATUS"],
        attempts=metadata["REQ_ATTEMPTS"],
        ms=metadata["REQ_MS"],
        rows_returned=metadata["REQ_ROWS"],
        requested_at=metadata["REQ_AT"],
    )


def _insert_statement(meta_schema: str, rows: Sequence[FetchLogRow]) -> tuple[str, dict[str, Any]]:
    columns = ", ".join(FETCH_LOG_COLUMNS)
    params: dict[str, Any] = {"schema": meta_schema}
    tuples = []
    for row_index, row in enumerate(rows):
        placeholders = []
        for column, value in zip(FETCH_LOG_COLUMNS, row.as_tuple(), strict=True):
            name = f"r{row_index}_{column.lower()}"
            params[name] = _param_value(value)
            conversion = "!d" if column in NUMERIC_FETCH_LOG_COLUMNS else ""
            placeholders.append(f"{{{name}{conversion}}}")
        tuples.append(f"({', '.join(placeholders)})")
    return f"INSERT INTO {{schema!i}}.FETCH_LOG ({columns}) VALUES {', '.join(tuples)}", params


def _param_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec=TIMESTAMP_PRECISION)
    return value
