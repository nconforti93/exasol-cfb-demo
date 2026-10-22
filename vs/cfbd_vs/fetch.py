"""Entry of the fetch UDF `CFB_INFRA.CFBD_FETCH` (stdlib only).

The UDF is a `SET ... EMITS (...)` script. Its input columns are, in this order:

    TABLE_NAME, SLICE_KEY, CATALOG_HASH, FETCH_MODE, CONNECTION_NAME, REPLAY_PATH, DOH_URL,
    MAX_PARALLEL_FETCHES

`run(ctx, exa)` reads every input row of its group, checks each one, then dispatches the slices
through an executor and emits the rows in input order. Unless an executor is injected, its
width is the `MAX_PARALLEL_FETCHES` argument of the group's first row (NULL means 6, valid 1-16).
The first failure fails the group and stops it at once, whether a slice failed or emitting did:
from then on no slice starts a request, a retry or a retry wait. Requests already sent finish,
and that first failure is the error raised.

Per row: the catalog hash must equal the embedded catalog's (otherwise nothing is sent); live
mode reads the connection once per group and sends `GET <address><slice>` through
`CfbdClient`; replay mode reads the recording and never touches the connection or the network.
Replayed rows carry the recorded status, attempts, latency and request time; attempts are NULL
for a recording written before recordings stored them.
`TABLE_NAME = '@RAW'` emits the raw response body in parts for recording. Any other table emits
its flattened rows followed by the `P_*` echo columns (parsed from the slice key) and the `REQ_*`
request metadata, restricted and ordered by the output columns `exa.meta` declares.

Errors surface as `FetchEntryError` with the prefix `CFBD_VS.<TABLE>: `, never containing the key.

The bundler sets `CATALOG_JSON` and defines `run(ctx)` as `fetch.run(ctx, exa)`, because `exa` is
a global of the UDF script, not of this module.
"""

from __future__ import annotations

import functools
import json
import math
import random
import re
import threading
import urllib.parse
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from vs.cfbd_vs import naming, resolver
from vs.cfbd_vs.catalog import Catalog, CatalogError, Param, Table
from vs.cfbd_vs.flatten import FlattenError, data_columns, flatten
from vs.cfbd_vs.http import (
    STATUS_SUCCESS,
    CfbdClient,
    Clock,
    FetchError,
    RequestCancelled,
    Transport,
    http_transport,
    redact,
)
from vs.cfbd_vs.replay import (
    DEFAULT_REPLAY_PATH,
    FileSystem,
    ReplayError,
    ReplayStore,
    split_parts,
)

ERROR_PREFIX = "CFBD_VS"
RAW_TABLE = "@RAW"
MODE_LIVE = "live"
MODE_REPLAY = "replay"
SOURCE_LIVE = "LIVE"
SOURCE_REPLAY = "REPLAY"
DEFAULT_CONNECTION_NAME = "CFBD_API"
DEFAULT_PARALLEL_FETCHES = 6
MIN_PARALLEL_FETCHES = 1
MAX_PARALLEL_FETCHES = 16
RAW_COLUMNS = (
    "SLICE_KEY",
    "PART_NO",
    "PART_COUNT",
    "BODY_PART",
    "HTTP_STATUS",
    "ATTEMPTS",
    "MS",
    "REQUESTED_AT",
)

CATALOG_JSON: str | None = None
_embedded_catalog: Catalog | None = None

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_INTEGER = re.compile(r"-?[0-9]+\Z")

T = TypeVar("T")
R = TypeVar("R")
Row = tuple[Any, ...]


class FetchEntryError(Exception):
    """A fetch row failed. The message starts with `CFBD_VS.<TABLE>: `."""


class Executor(Protocol):
    """Applies a function to items, yielding the results in item order."""

    def map(self, function: Callable[[T], R], items: Iterable[T]) -> Iterable[R]: ...


class _SerialExecutor:
    def map(self, function: Callable[[T], R], items: Iterable[T]) -> Iterable[R]:
        return map(function, items)


class _ThreadedExecutor:
    def __init__(self, width: int) -> None:
        self._width = width

    def map(self, function: Callable[[T], R], items: Iterable[T]) -> Iterable[R]:
        return self._run(function, list(items))

    def _run(self, function: Callable[[T], R], items: list[T]) -> Iterator[R]:
        if not items:
            return
        pool = ThreadPoolExecutor(max_workers=min(self._width, len(items)))
        try:
            yield from pool.map(function, items)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)


class _Halt:
    """The stop signal of one group. The first failure is kept, then the signal is set."""

    def __init__(self) -> None:
        self.signal = threading.Event()
        self._lock = threading.Lock()
        self._first: Exception | None = None

    def fail(self, error: Exception) -> Exception:
        """Keep `error` unless a failure is kept already, set the signal, return the first."""
        with self._lock:
            if self._first is None:
                self._first = error
            first = self._first
        self.signal.set()
        return first

    def stop(self) -> None:
        self.signal.set()


def _close(results: object) -> None:
    close = getattr(results, "close", None)
    if close is not None:
        close()


def executor_for(width: int) -> Executor:
    """A serial executor for width 1, otherwise a thread pool of at most `width` workers."""
    if width < 1:
        raise ValueError(f"executor width must be at least 1, got {width}")
    return _SerialExecutor() if width == 1 else _ThreadedExecutor(width)


@dataclass(frozen=True)
class _Request:
    table_name: str
    slice_key: str
    catalog_hash: str
    mode: str
    connection_name: str
    replay_path: str
    doh_url: str
    parallel: int

    @property
    def label(self) -> str:
        return f"{ERROR_PREFIX}.{self.table_name}"


@dataclass(frozen=True)
class _Outcome:
    source: str
    status: int
    attempts: int | None
    ms: int
    requested_at: datetime
    body: str


@dataclass(frozen=True)
class _Job:
    request: _Request
    table: Table | None
    params: Mapping[str, Any]
    names: tuple[str, ...]
    client: CfbdClient | None = field(default=None, repr=False)
    key: str = field(default="", repr=False)
    store: ReplayStore | None = None


def embedded_catalog() -> Catalog:
    """The catalog the bundler embedded in this script, parsed once per process."""
    global _embedded_catalog
    if _embedded_catalog is None:
        if CATALOG_JSON is None:
            raise FetchEntryError(
                f"{ERROR_PREFIX}: the fetch script has no embedded catalog; "
                "deploy it from a bundle built by vs/bundle.py"
            )
        _embedded_catalog = Catalog.from_json(CATALOG_JSON)
    return _embedded_catalog


def run(ctx: Any, exa: Any) -> None:
    """UDF entry: process every input row of the group with the production dependencies."""
    FetchEntry(exa, embedded_catalog()).run(ctx)


class FetchEntry:
    """The fetch UDF's logic with injectable catalog, transport, clock, files and executor."""

    def __init__(
        self,
        exa: Any,
        catalog: Catalog,
        *,
        transport: Transport = http_transport,
        clock: Clock | None = None,
        uniform: Callable[[float, float], float] = random.uniform,
        files: FileSystem | None = None,
        install_resolver: Callable[[str], None] = resolver.install,
        executor: Executor | None = None,
    ) -> None:
        self._exa = exa
        self._catalog = catalog
        self._transport = transport
        self._clock = clock
        self._uniform = uniform
        self._files = files
        self._install_resolver = install_resolver
        self._executor = executor

    def run(self, ctx: Any) -> None:
        """Emit the rows of every input row of the group, in input order.

        The halt is set before the executor is closed, so a failing emit stops the slices still
        running before the pool waits for them.
        """
        declared = [column.name for column in self._exa.meta.output_columns]
        session = _Session(self)
        requests = _read_group(ctx)
        jobs = [session.plan(request, declared) for request in requests]
        executor = self._executor or executor_for(requests[0].parallel)
        halt = _Halt()
        results: Iterable[list[Row]] = ()
        try:
            results = executor.map(functools.partial(self._execute, halt), jobs)
            for rows in results:
                for row in rows:
                    ctx.emit(*row)
        except Exception as error:
            first = halt.fail(error)
            if first is error:
                raise
            raise first from None
        finally:
            halt.stop()
            _close(results)

    def make_client(self, address: str, key: str) -> CfbdClient:
        return CfbdClient(
            address, key, transport=self._transport, clock=self._clock, uniform=self._uniform
        )

    def read_connection(self, name: str) -> Any:
        return self._exa.get_connection(name)

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def files(self) -> FileSystem | None:
        return self._files

    @property
    def install_resolver(self) -> Callable[[str], None]:
        return self._install_resolver

    def _execute(self, halt: _Halt, job: _Job) -> list[Row]:
        if halt.signal.is_set():
            raise RequestCancelled(f"{job.request.slice_key}: not started, the fetch was cancelled")
        try:
            return self._rows(job, halt.signal)
        except RequestCancelled:
            raise
        except Exception as error:
            halt.fail(error)
            raise

    def _rows(self, job: _Job, cancel: threading.Event) -> list[Row]:
        request = job.request
        try:
            outcome = self._obtain(job, cancel)
            if job.table is None:
                return _raw_rows(job, outcome)
            return _table_rows(job, outcome)
        except FetchEntryError:
            raise
        except (FetchError, ReplayError) as error:
            raise _surface(job, str(error)) from None
        except FlattenError as error:
            raise _surface(job, f"{error} [slice {request.slice_key}]") from None

    def _obtain(self, job: _Job, cancel: threading.Event) -> _Outcome:
        request = job.request
        if job.client is not None:
            result = job.client.get(request.slice_key, cancel=cancel)
            return _Outcome(
                SOURCE_LIVE,
                result.status,
                result.attempts,
                result.ms,
                _naive_utc(result.requested_at),
                result.body,
            )
        assert job.store is not None
        recorded = job.store.load(request.slice_key)
        if recorded.status not in STATUS_SUCCESS:
            raise FetchEntryError(
                f"{request.label}: the recording of slice {request.slice_key} holds "
                f"HTTP status {recorded.status}, not a successful response"
            )
        return _Outcome(
            SOURCE_REPLAY,
            recorded.status,
            recorded.attempts,
            recorded.ms,
            _naive_utc(recorded.requested_at),
            recorded.body,
        )


class _Session:
    """State of one `run`: connections read once, the resolver installed once per DoH URL."""

    def __init__(self, entry: FetchEntry) -> None:
        self._entry = entry
        self._clients: dict[str, tuple[CfbdClient, str]] = {}
        self._doh_url: str | None = None

    def plan(self, request: _Request, declared: list[str]) -> _Job:
        entry = self._entry
        if request.catalog_hash != entry.catalog.catalog_hash:
            raise FetchEntryError(
                f"{request.label}: the adapter and the fetch UDF were deployed from different "
                f"catalog versions (adapter {request.catalog_hash[:12]}, fetch UDF "
                f"{entry.catalog.catalog_hash[:12]}); a redeploy is required "
                "(`make deploy`, then `make grants`)"
            )
        table, available = self._table(request)
        missing = [name for name in declared if name not in available]
        if missing:
            raise FetchEntryError(
                f"{request.label}: the UDF output declares columns the table does not have: "
                f"{', '.join(missing)}"
            )
        names = tuple(declared)
        params = {} if table is None else _param_values(request, table)
        if request.mode == MODE_REPLAY:
            store = ReplayStore(request.replay_path, entry.files)
            return _Job(request, table, params, names, store=store)
        client, key = self._client(request)
        return _Job(request, table, params, names, client=client, key=key)

    def _table(self, request: _Request) -> tuple[Table | None, tuple[str, ...]]:
        if request.table_name == RAW_TABLE:
            return None, RAW_COLUMNS
        try:
            table = self._entry.catalog.table(request.table_name)
        except CatalogError:
            raise FetchEntryError(
                f"{request.label}: the catalog has no table of that name"
            ) from None
        return table, tuple(column.name for column in table.columns)

    def _client(self, request: _Request) -> tuple[CfbdClient, str]:
        try:
            resolver.validate_doh_url(request.doh_url)
        except ValueError as error:
            raise FetchEntryError(f"{request.label}: {error}") from None
        if request.doh_url != self._doh_url:
            self._entry.install_resolver(request.doh_url)
            self._doh_url = request.doh_url
        name = request.connection_name
        if name not in self._clients:
            self._clients[name] = self._open(request)
        return self._clients[name]

    def _open(self, request: _Request) -> tuple[CfbdClient, str]:
        name = request.connection_name
        try:
            connection = self._entry.read_connection(name)
        except Exception as error:
            raise FetchEntryError(
                f"{request.label}: connection '{name}' cannot be read: the fetch UDF has no "
                f"access to it, or it does not exist ({type(error).__name__}: {error}). "
                f"A DBA must run `make grants` (GRANT ACCESS ON CONNECTION {name} "
                "FOR SCRIPT CFB_INFRA.CFBD_FETCH TO <owner>)"
            ) from None
        key = str(connection.password or "")
        try:
            return self._entry.make_client(str(connection.address or ""), key), key
        except ValueError as error:
            raise FetchEntryError(
                f"{request.label}: connection '{name}' is unusable: {error}"
            ) from None


def _read_group(ctx: Any) -> list[_Request]:
    requests = []
    while True:
        requests.append(_read_request(ctx))
        if not ctx.next():
            return requests


def _read_request(ctx: Any) -> _Request:
    table_name = ctx.TABLE_NAME
    if not table_name:
        raise FetchEntryError(f"{ERROR_PREFIX}: TABLE_NAME is missing")
    label = f"{ERROR_PREFIX}.{table_name}"
    if not ctx.SLICE_KEY:
        raise FetchEntryError(f"{label}: SLICE_KEY is missing")
    mode = (ctx.FETCH_MODE or MODE_LIVE).lower()
    if mode not in (MODE_LIVE, MODE_REPLAY):
        raise FetchEntryError(
            f"{label}: FETCH_MODE must be 'live' or 'replay', got {ctx.FETCH_MODE!r}"
        )
    return _Request(
        table_name=table_name,
        slice_key=ctx.SLICE_KEY,
        catalog_hash=ctx.CATALOG_HASH or "",
        mode=mode,
        connection_name=ctx.CONNECTION_NAME or DEFAULT_CONNECTION_NAME,
        replay_path=ctx.REPLAY_PATH or DEFAULT_REPLAY_PATH,
        doh_url=ctx.DOH_URL or resolver.DEFAULT_DOH_URL,
        parallel=_parallel_fetches(label, ctx.MAX_PARALLEL_FETCHES),
    )


def _parallel_fetches(label: str, value: Any) -> int:
    if value is None:
        return DEFAULT_PARALLEL_FETCHES
    width = int(value)
    if width != value or not MIN_PARALLEL_FETCHES <= width <= MAX_PARALLEL_FETCHES:
        raise FetchEntryError(
            f"{label}: MAX_PARALLEL_FETCHES must be an integer from {MIN_PARALLEL_FETCHES} to "
            f"{MAX_PARALLEL_FETCHES}, got {value!r}"
        )
    return width


def _param_values(request: _Request, table: Table) -> dict[str, Any]:
    """The `P_*` echo values, parsed from the slice key; parameters not sent are NULL."""
    path, _, query = request.slice_key.partition("?")
    path_values = _match_path(request, table, path)
    query_values = _parse_query(request, query)
    known = {param.name for param in table.params if param.location == "query"}
    unknown = sorted(set(query_values) - known)
    if unknown:
        raise _slice_error(request, f"unknown query parameter {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for param in table.params:
        source = path_values if param.location == "path" else query_values
        text = source.get(param.name)
        values[param.column] = None if text is None else _typed(request, param, text)
    return values


def _match_path(request: _Request, table: Table, path: str) -> dict[str, str]:
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
        raise _slice_error(request, f"path {path!r} does not match endpoint {table.endpoint}")
    return {
        name: urllib.parse.unquote(value)
        for name, value in zip(names, matched.groups(), strict=True)
    }


def _parse_query(request: _Request, query: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in filter(None, query.split("&")):
        name, separator, value = item.partition("=")
        if not separator:
            raise _slice_error(request, f"query item {item!r} has no '='")
        name = urllib.parse.unquote(name)
        if name in values:
            raise _slice_error(request, f"parameter {name} appears more than once")
        values[name] = urllib.parse.unquote(value)
    return values


def _typed(request: _Request, param: Param, text: str) -> Any:
    if param.type == "integer" and _INTEGER.match(text):
        return int(text)
    if param.type == "number":
        number = _number(text)
        if number is not None:
            return number
    if param.type == "boolean" and text in ("true", "false"):
        return text == "true"
    if param.type in ("integer", "number", "boolean"):
        raise _slice_error(request, f"parameter {param.name} is {param.type}, got {text!r}")
    return text


def _number(text: str) -> float | None:
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _table_rows(job: _Job, outcome: _Outcome) -> list[Row]:
    table = job.table
    assert table is not None
    try:
        parsed = json.loads(outcome.body)
    except ValueError:
        raise _surface(
            job, f"{job.request.slice_key}: the response body is not valid JSON"
        ) from None
    flattened = flatten(table, parsed)
    data_names = [column.name for column in data_columns(table)]
    meta = {
        "REQ_SLICE": job.request.slice_key,
        "REQ_SOURCE": outcome.source,
        "REQ_STATUS": outcome.status,
        "REQ_ATTEMPTS": outcome.attempts,
        "REQ_MS": outcome.ms,
        "REQ_ROWS": len(flattened),
        "REQ_AT": outcome.requested_at,
    }
    assert tuple(meta) == tuple(naming.META_COLUMNS)
    rows = []
    for values in flattened:
        cells = {**dict(zip(data_names, values, strict=True)), **job.params, **meta}
        rows.append(tuple(cells[name] for name in job.names))
    return rows


def _raw_rows(job: _Job, outcome: _Outcome) -> list[Row]:
    parts = split_parts(outcome.body)
    rows = []
    for number, body in enumerate(parts, start=1):
        cells = {
            "SLICE_KEY": job.request.slice_key,
            "PART_NO": number,
            "PART_COUNT": len(parts),
            "BODY_PART": body,
            "HTTP_STATUS": outcome.status,
            "ATTEMPTS": outcome.attempts,
            "MS": outcome.ms,
            "REQUESTED_AT": outcome.requested_at,
        }
        rows.append(tuple(cells[name] for name in job.names))
    return rows


def _surface(job: _Job, text: str) -> FetchEntryError:
    text = redact(text, job.key)
    label = job.request.label
    return FetchEntryError(text if text.startswith(label) else f"{label}: {text}")


def _slice_error(request: _Request, reason: str) -> FetchEntryError:
    return FetchEntryError(f"{request.label}: slice {request.slice_key!r}: {reason}")


def _naive_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)
