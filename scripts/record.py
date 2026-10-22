"""`make record PATTERNS=<file> CONFIRM=yes`: record CFBD responses within a hard call cap.

Each pattern is one slice key, the request path the adapter would send. The recorder fetches it
once through the fetch UDF's raw output (`CFB_INFRA.CFBD_FETCH('@RAW', ...)`, live mode, as sys,
[20]), so the key never leaves Exasol, and stores the response as a replay recording.

Guards, in order:
- Patterns must be canonical slice keys of a catalog endpoint; a typo would waste a call or file
  the recording where replay never looks. Slices already recorded in `cache/replay/` are skipped
  (0 calls) unless `--rerecord` is given.
- The plan is printed with the ledger totals; without `CONFIRM=yes` nothing is sent.
- The ledger `cache/live-calls.jsonl` (`vs/host/ledger.py`) is locked for the whole run. A run is
  refused when its planned calls plus `MAX_ATTEMPTS - 1` (the retries of its last call) would
  exceed the plan cap (default 50) or the month cap of all plans (400). Before each call at least
  `MAX_ATTEMPTS` calls of headroom must remain, so retries can never push a cap past its limit.
- `/info` (free) is read before the run and after every call. Each call's ledger entry carries
  the readings around it; its cost is its HTTP attempts, or the observed drop of
  `remainingCalls` when that is larger. The run stops at the first failed call, the first
  missing reading, or the first call that cost more than its attempts.
- The database connection waits up to `SOCKET_TIMEOUT_SECONDS` for a statement (pyexasol's
  default of 30 s cut off a week of `/plays` after the call had been paid).

Recordings go to `cache/replay/` and, on the `local` target, to the Personal BucketFS host
directory that the default `REPLAY_PATH` maps to. The `aws` target's BucketFS has no host
directory and the tool has no BucketFS credentials for it, so its recordings stay in
`cache/replay/`; `--publish-local` copies them into the local BucketFS for replay there.
Measurements (status, attempts, ms, body bytes, rows per catalog table) go to
`cache/measurements/<run>.jsonl`. Everything under `cache/` is git-ignored.

Other modes: `--ledger` prints the totals; `--info` logs one free `/info` reading;
`--publish-local` copies the cached recordings (no database, no network). `--query "<SQL>"`
runs one documented virtual-schema query under the same guards: `EXPLAIN VIRTUAL` (free) gives
the planned slices for the cap check, `/info` is read before and after, and every slice the
result reports as `LIVE` (its `REQ_*` columns) gets a ledger entry. A query is charged at least
the observed drop of `remainingCalls`; without `REQ_*` columns, or when it fails, each planned
slice is charged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from vs import bundle
from vs.cfbd_vs.catalog import Catalog, CatalogError, Param, Table
from vs.cfbd_vs.fetch import MODE_LIVE, RAW_TABLE
from vs.cfbd_vs.flatten import FlattenError, flatten
from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.cfbd_vs.plan import PlanError, build_slice_key
from vs.cfbd_vs.replay import (
    DEFAULT_REPLAY_PATH,
    RECORDING_SUFFIX,
    Recording,
    ReplayError,
    ReplayStore,
    assemble_parts,
    decode_recording,
)
from vs.cfbd_vs.resolver import DEFAULT_DOH_URL
from vs.cfbd_vs.sqlrender import quote_identifier, string_literal
from vs.host import fetchlog, ledger, target
from vs.host.ledger import Entry, Ledger, LedgerError
from vs.host.namespace import REAL_CONNECTION, Namespace

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = "add-cfbd-vs-foundation"
DEFAULT_PLAN_CAP = 50
CONFIRM_WORD = "yes"
INFO_SLICE = "/info"
SOURCE_LIVE = "LIVE"
BUCKETS_PREFIX = "/buckets/"
ERROR_TEXT_LIMIT = 500
SOCKET_TIMEOUT_SECONDS = 900

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_STOPPED = 1
EXIT_ERROR = 2

RAW_COLUMNS = (
    ("SLICE_KEY", "VARCHAR(2000) UTF8"),
    ("PART_NO", "DECIMAL(9,0)"),
    ("PART_COUNT", "DECIMAL(9,0)"),
    ("BODY_PART", "VARCHAR(2000000) UTF8"),
    ("HTTP_STATUS", "DECIMAL(3,0)"),
    ("ATTEMPTS", "DECIMAL(3,0)"),
    ("MS", "DECIMAL(9,0)"),
    ("REQUESTED_AT", "TIMESTAMP"),
)
_INTEGER_COLUMNS = ("PART_NO", "PART_COUNT", "HTTP_STATUS", "ATTEMPTS", "MS")

_SLICE_KEY = re.compile(r"/[\x21-\x7e]*\Z")
_PLACEHOLDER = re.compile(r"\{([^/{}]+)\}")
_INTEGER = re.compile(r"-?[0-9]+\Z")
_ATTEMPTS = re.compile(r"\bafter (\d+) attempts?\b")
_STATUS = re.compile(r"\bHTTP (\d{3})\b")
_CAUSE_LINE = re.compile(r"^[\w.]*(?:Error|Exception): ")
_NO_REQUEST_SENT = (
    "name resolution failed",
    "TLS certificate verification failed",
    "different catalog versions",
    "cannot be read",
    "is unusable",
)
_PATTERN_KEYS = frozenset({"slice", "note"})


def _local_bucketfs_replay() -> Path:
    bucketfs = target.DEPLOYMENTS_ROOT / "local" / "local" / "runtime" / "exa" / "bucketfs"
    return bucketfs.joinpath(*DEFAULT_REPLAY_PATH.removeprefix(BUCKETS_PREFIX).split("/"))


@dataclass(frozen=True)
class Paths:
    """Where the recorder reads and writes on the host."""

    ledger: Path = ledger.LEDGER_PATH
    replay_cache: Path = ROOT / "cache" / "replay"
    measurements: Path = ROOT / "cache" / "measurements"
    local_bucketfs_replay: Path = field(default_factory=_local_bucketfs_replay)


class RecordError(RuntimeError):
    """The recorder cannot run; nothing more is sent."""


class Refused(RecordError):
    """A guard refused the run before the call it names."""


class PatternError(RecordError):
    """A pattern is not a canonical slice key of the catalog."""


class RawFetchFailed(Exception):
    """The fetch UDF failed for a slice; the message is the cause line of the database error."""


class RawSource(Protocol):
    """Raw-output rows of one slice, as dicts keyed by the `RAW_COLUMNS` names."""

    def fetch(self, slice_key: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class QuerySource(RawSource, Protocol):
    """A `RawSource` that also explains and runs virtual-schema queries."""

    def explain(self, sql: str) -> str: ...

    def run(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]: ...


@dataclass(frozen=True)
class SliceCall:
    """What a query did with one planned slice, from its `REQ_*` columns."""

    slice_key: str
    source: str
    status: int | None
    attempts: int | None
    ms: int | None
    rows: int | None
    requested_at: str | None


@dataclass(frozen=True)
class Pattern:
    """One slice to record, with an optional note for the measurement."""

    slice_key: str
    note: str = ""


@dataclass(frozen=True)
class Budget:
    """The caps one run is checked against."""

    plan: str
    plan_cap: int
    month_cap: int = ledger.MONTH_CAP

    def headroom(self, entries: Sequence[Entry], month: str) -> int:
        """Calls left before the tighter of the two caps."""
        return min(
            self.plan_cap - ledger.plan_total(entries, self.plan),
            self.month_cap - ledger.month_total(entries, month),
        )

    def describe(self, entries: Sequence[Entry], month: str) -> str:
        return (
            f"plan {self.plan} {ledger.plan_total(entries, self.plan)} of {self.plan_cap} "
            f"calls; {month} all plans {ledger.month_total(entries, month)} of "
            f"{self.month_cap}"
        )


@dataclass(frozen=True)
class Quota:
    """One `/info` reading."""

    remaining: int
    used: int | None
    limit: int | None
    tier: str | None
    reset_at: str | None
    status: int
    attempts: int | None
    ms: int

    def describe(self) -> str:
        return (
            f"CFBD /info: tier {self.tier}, remainingCalls {self.remaining} of {self.limit} "
            f"(used {self.used}), resets {self.reset_at}"
        )


def load_patterns(text: str, catalog: Catalog) -> list[Pattern]:
    """The patterns of a YAML file `patterns: [<slice> | {slice, note}, ...]`, all validated."""
    data = yaml.safe_load(text)
    items = data.get("patterns") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise PatternError("the patterns file must be a mapping with a 'patterns' list")
    if not items:
        raise PatternError("the patterns file has no patterns")
    patterns = [_pattern(item, number) for number, item in enumerate(items, start=1)]
    seen: set[str] = set()
    for pattern in patterns:
        if pattern.slice_key in seen:
            raise PatternError(f"slice {pattern.slice_key} is listed more than once")
        seen.add(pattern.slice_key)
        canonical = canonical_slice_key(pattern.slice_key, catalog)
        if canonical != pattern.slice_key:
            raise PatternError(
                f"slice {pattern.slice_key} is not canonical; the adapter would send {canonical}"
            )
    return patterns


def canonical_slice_key(slice_key: str, catalog: Catalog) -> str:
    """The slice key the planner builds for the parameters of `slice_key`."""
    if not _SLICE_KEY.match(slice_key):
        raise PatternError(
            f"slice {slice_key!r} must be a path starting with '/' of printable ASCII "
            "characters without spaces"
        )
    if "{" in slice_key or "}" in slice_key:
        raise PatternError(f"slice {slice_key} has an unfilled placeholder")
    path, _, query = slice_key.partition("?")
    tables = tables_for(catalog, path)
    if not tables:
        raise PatternError(f"slice {slice_key}: no catalog endpoint matches the path {path}")
    table = tables[0]
    texts = {**_path_values(table, path), **_query_values(slice_key, query)}
    params = {param.name: param for param in table.params}
    unknown = sorted(name for name in texts if name not in params)
    if unknown:
        raise PatternError(
            f"slice {slice_key}: unknown query parameter {', '.join(unknown)} for {table.endpoint}"
        )
    values = {name: _typed(slice_key, params[name], text) for name, text in texts.items()}
    try:
        return build_slice_key(table, values)
    except PlanError as error:
        raise PatternError(f"slice {slice_key}: {error}") from None


def tables_for(catalog: Catalog, path: str) -> list[Table]:
    """The catalog tables of the endpoint a request path belongs to; a literal endpoint wins."""
    literal = [table for table in catalog.tables if table.endpoint == path]
    if literal:
        return literal
    return [
        table
        for table in catalog.tables
        if "{" in table.endpoint and _template(table.endpoint).fullmatch(path)
    ]


def select_patterns(
    patterns: Sequence[Pattern], cache: ReplayStore, *, rerecord: bool
) -> tuple[list[Pattern], list[Pattern]]:
    """Split into patterns to fetch and patterns already recorded in the cache."""
    if rerecord:
        return list(patterns), []
    recorded = [pattern for pattern in patterns if cache.exists(pattern.slice_key)]
    return [pattern for pattern in patterns if pattern not in recorded], recorded


def check_budget(budget: Budget, entries: Sequence[Entry], month: str, planned: int) -> None:
    """Refuse when the planned calls plus the last call's retries could exceed a cap."""
    if planned == 0:
        return
    needed = planned + MAX_ATTEMPTS - 1
    if needed > budget.headroom(entries, month):
        raise Refused(
            f"{planned} planned call(s) plus {MAX_ATTEMPTS - 1} calls of retry headroom would "
            f"exceed a cap ({budget.describe(entries, month)})"
        )


def failure_attempts(message: str) -> int | None:
    """HTTP attempts a failure message reports; None when the message does not say."""
    counted = _ATTEMPTS.search(message)
    if counted:
        return int(counted.group(1))
    if any(marker in message for marker in _NO_REQUEST_SENT):
        return 0
    if _STATUS.search(message):
        return 1
    return None


def failure_status(message: str) -> int | None:
    """The HTTP status a failure message reports, if any."""
    status = _STATUS.search(message)
    return int(status.group(1)) if status else None


def charged(attempts: int | None, before: int | None, after: int | None) -> int:
    """Calls to charge: the attempts (all of them when unknown), or the observed drop if larger."""
    counted = MAX_ATTEMPTS if attempts is None else attempts
    if before is None or after is None:
        return counted
    return max(counted, before - after)


def query_costs(attempts: Sequence[int | None], before: int, after: int | None) -> list[int]:
    """Charges for a query's live slices: the attempts, 1 for an unknown slice (all four without
    an `/info` reading afterwards), and the rest of the observed drop on the last slice."""
    unknown = 1 if after is not None else MAX_ATTEMPTS
    costs = [unknown if count is None else count for count in attempts]
    if costs and after is not None:
        costs[-1] += max(0, before - after - sum(costs))
    return costs


def slice_calls(
    manifest: fetchlog.Manifest, names: Sequence[str], rows: Sequence[tuple[Any, ...]]
) -> list[SliceCall] | None:
    """Per planned slice what the query read, or None when the result has no `REQ_*` columns."""
    if not all(column in names for column in fetchlog.REQUEST_COLUMNS):
        return None
    positions = [list(names).index(column) for column in fetchlog.REQUEST_COLUMNS]
    distinct = {tuple(row[position] for position in positions) for row in rows}
    metadata = [dict(zip(fetchlog.REQUEST_COLUMNS, values, strict=True)) for values in distinct]
    logged = fetchlog.build_fetch_log_rows(manifest, metadata, logged_at=datetime.now(UTC))
    return [
        SliceCall(
            row.slice_key,
            row.source,
            _optional_int(row.http_status),
            _optional_int(row.attempts),
            _optional_int(row.ms),
            _optional_int(row.rows_returned),
            None if row.requested_at is None else str(row.requested_at),
        )
        for row in logged
    ]


def planned_live(manifest: fetchlog.Manifest) -> list[str]:
    """The slices a query may fetch live (the budget counts them all, [4])."""
    return list(manifest.slice_keys) if manifest.fetch_source == SOURCE_LIVE else []


def replay_sinks(target_name: str, paths: Paths) -> tuple[list[Path], str]:
    """The directories recordings are written to on this target, and a note about them."""
    if target_name == "local":
        note = (
            f"recordings: {paths.replay_cache} and the local BucketFS "
            f"{paths.local_bucketfs_replay} (REPLAY_PATH {DEFAULT_REPLAY_PATH})"
        )
        return [paths.replay_cache, paths.local_bucketfs_replay], note
    note = (
        f"recordings: cache/replay only ({paths.replay_cache}). Target {target_name} has no "
        "BucketFS host directory and this tool has no BucketFS credentials for it; replay "
        "these on local after `python -m scripts.record --publish-local`"
    )
    return [paths.replay_cache], note


def raw_sql(
    slice_key: str, *, fetch_script: str, catalog_hash: str, connection_name: str, doh_url: str
) -> str:
    """One raw-output call of the fetch UDF in live mode, one slice, one thread."""
    arguments = ", ".join(
        [
            string_literal(RAW_TABLE),
            string_literal(slice_key),
            string_literal(catalog_hash),
            string_literal(MODE_LIVE),
            string_literal(connection_name),
            "NULL",
            string_literal(doh_url),
            "1",
        ]
    )
    emits = ", ".join(f"{quote_identifier(name)} {sql_type}" for name, sql_type in RAW_COLUMNS)
    return f"SELECT {fetch_script}({arguments}) EMITS ({emits}) FROM DUAL"


class DatabaseRawSource:
    """`RawSource` over a database connection: the owner calls the fetch UDF directly."""

    def __init__(
        self,
        connection: Any,
        *,
        fetch_script: str,
        catalog_hash: str,
        connection_name: str,
        doh_url: str,
    ) -> None:
        self._connection = connection
        self._fetch_script = fetch_script
        self._catalog_hash = catalog_hash
        self._connection_name = connection_name
        self._doh_url = doh_url

    def fetch(self, slice_key: str) -> list[dict[str, Any]]:
        sql = raw_sql(
            slice_key,
            fetch_script=self._fetch_script,
            catalog_hash=self._catalog_hash,
            connection_name=self._connection_name,
            doh_url=self._doh_url,
        )
        try:
            rows = self._connection.execute(sql).fetchall()
        except Exception as error:
            raise RawFetchFailed(cause_line(error)) from None
        names = [name for name, _ in RAW_COLUMNS]
        return [_typed_raw_row(dict(zip(names, row, strict=True))) for row in rows]

    def explain(self, sql: str) -> str:
        try:
            return fetchlog.explain_pushdown_sql(self._connection, sql)
        except fetchlog.FetchLogError as error:
            raise RecordError(f"cannot explain the query: {error}") from None
        except Exception as error:
            raise RecordError(f"cannot explain the query: {cause_line(error)}") from None

    def run(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        try:
            statement = self._connection.execute(sql)
            return list(statement.column_names()), [tuple(row) for row in statement.fetchall()]
        except Exception as error:
            raise RawFetchFailed(cause_line(error)) from None


def cause_line(error: Exception) -> str:
    """The line of a database error that names the cause: the last `...Error: ` line."""
    message = str(getattr(error, "message", error)).strip()
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    causes = [line for line in lines if _CAUSE_LINE.match(line)]
    text = _CAUSE_LINE.sub("", causes[-1], count=1) if causes else (lines[0] if lines else "")
    return (text or type(error).__name__)[:ERROR_TEXT_LIMIT]


def read_quota(source: RawSource) -> Quota:
    """Read `/info` (free) through the raw output."""
    try:
        recording = assemble_parts(source.fetch(INFO_SLICE))
        data = json.loads(recording.body)
    except (RawFetchFailed, ReplayError, ValueError) as error:
        raise RecordError(f"cannot read /info: {error}") from None
    remaining = data.get("remainingCalls") if isinstance(data, dict) else None
    if _as_count(remaining) is None:
        raise RecordError("/info returned no usable remainingCalls")
    return Quota(
        remaining=_as_count(remaining) or 0,
        used=_as_count(data.get("usedCalls")),
        limit=_as_count(data.get("monthlyLimit")),
        tier=data.get("tierName"),
        reset_at=data.get("resetAt"),
        status=recording.status,
        attempts=recording.attempts,
        ms=recording.ms,
    )


def measure(catalog: Catalog, pattern: Pattern, recording: Recording) -> dict[str, Any]:
    """Derived numbers of one response: sizes, timing and rows per catalog table."""
    rows: dict[str, int] = {}
    errors: dict[str, str] = {}
    try:
        parsed = json.loads(recording.body)
    except ValueError:
        errors["*"] = "the response body is not valid JSON"
    else:
        for table in tables_for(catalog, pattern.slice_key.partition("?")[0]):
            try:
                rows[table.name] = len(flatten(table, parsed))
            except FlattenError as error:
                errors[table.name] = str(error)[:ERROR_TEXT_LIMIT]
    return {
        "slice_key": pattern.slice_key,
        "note": pattern.note,
        "status": recording.status,
        "attempts": recording.attempts,
        "ms": recording.ms,
        "bytes": len(recording.body.encode("utf-8")),
        "requested_at": recording.requested_at.isoformat(),
        "rows": rows,
        "flatten_errors": errors,
    }


class Recorder:
    """One recorder run against one target; every dependency is injected."""

    def __init__(
        self,
        source: RawSource,
        *,
        catalog: Catalog,
        book: Ledger,
        budget: Budget,
        target_name: str,
        sinks: Sequence[Path],
        measurements: Path,
        clock: Callable[[], datetime],
        out: Callable[[str], None] = print,
    ) -> None:
        self._source = source
        self._catalog = catalog
        self._book = book
        self._budget = budget
        self._target = target_name
        self._sinks = [ReplayStore(str(path)) for path in sinks]
        self._measurements = measurements
        self._clock = clock
        self._out = out
        self._run = clock().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    def describe_query(self, sql: str) -> list[str]:
        """The plan of a query from `EXPLAIN VIRTUAL` (free): table, mode and planned slices."""
        manifest = self._manifest(self._runner(), sql)
        planned = planned_live(manifest)
        lines = [
            f"query: table {manifest.table}, fetch_mode {manifest.fetch_mode}, sources "
            f"{list(manifest.sources)}, {len(planned)} slice(s) that may be fetched live"
        ]
        lines += [f"  slice {key}" for key in manifest.slice_keys]
        lines.append(f"ledger: {self._budget.describe(self._book.entries(), self._month())}")
        return lines

    def query(self, sql: str) -> int:
        """Run one virtual-schema query and log every live slice it fetched; see the module doc."""
        runner = self._runner()
        with self._book.exclusive():
            entries = self._book.entries()
            month = self._month()
            manifest = self._manifest(runner, sql)
            planned = planned_live(manifest)
            check_budget(self._budget, entries, month, len(planned))
            before = read_quota(self._source)
            entries.append(self._log_quota(before))
            self._out(before.describe())
            if before.remaining < len(planned):
                raise Refused(
                    f"the key has remainingCalls {before.remaining}, fewer than the "
                    f"{len(planned)} planned slice(s)"
                )
            names: list[str] = []
            rows: list[tuple[Any, ...]] = []
            failure: str | None = None
            started = time.monotonic()
            try:
                names, rows = runner.run(sql)
            except RawFetchFailed as error:
                failure = str(error)[:ERROR_TEXT_LIMIT] or type(error).__name__
            wall_ms = round((time.monotonic() - started) * 1000)
            after = self._try_quota()
            calls = self._calls(manifest, names, rows, planned, failure)
            live = [call for call in calls if call.source == SOURCE_LIVE]
            remaining_after = after.remaining if after else None
            costs = query_costs([call.attempts for call in live], before.remaining, remaining_after)
            for call, cost in zip(live, costs, strict=True):
                entry = self._entry(
                    call.slice_key,
                    cost=cost,
                    status=call.status,
                    attempts=call.attempts,
                    ms=call.ms,
                    remaining_before=before.remaining,
                    remaining_after=remaining_after,
                    error=failure,
                )
                self._book.append(entry)
                entries.append(entry)
                self._out(_entry_line(entry))
            if after is not None:
                self._log_quota(after)
            self._measure_query(sql, wall_ms, len(rows), before, after, calls)
        drop = "unknown" if after is None else str(before.remaining - after.remaining)
        self._out(
            f"query: {len(rows)} row(s) in {wall_ms} ms, {len(live)} live call(s), "
            f"remainingCalls {before.remaining} -> {remaining_after} (drop {drop}); "
            f"ledger: {self._budget.describe(entries, month)}"
        )
        if failure is not None or after is None:
            self._out(f"stopped: {failure or 'no /info reading after the query'}")
            return EXIT_STOPPED
        return EXIT_OK

    def info(self) -> int:
        """Log one free `/info` reading."""
        with self._book.exclusive():
            quota = read_quota(self._source)
            self._log_quota(quota)
        self._out(quota.describe())
        return EXIT_OK

    def record(self, patterns: Sequence[Pattern]) -> int:
        """Fetch and record every pattern, or stop at the first guard; see the module doc."""
        with self._book.exclusive():
            entries = self._book.entries()
            month = ledger.month_of(self._clock())
            check_budget(self._budget, entries, month, len(patterns))
            reading = read_quota(self._source)
            entries.append(self._log_quota(reading))
            self._out(reading.describe())
            if reading.remaining < len(patterns):
                raise Refused(
                    f"the key has remainingCalls {reading.remaining}, fewer than the "
                    f"{len(patterns)} planned call(s)"
                )
            done = 0
            stop: str | None = None
            closing: Quota | None = None
            for pattern in patterns:
                if self._budget.headroom(entries, month) < MAX_ATTEMPTS:
                    stop = (
                        f"fewer than {MAX_ATTEMPTS} calls of headroom left before "
                        f"{pattern.slice_key} ({self._budget.describe(entries, month)})"
                    )
                    break
                entry, after, stop = self._call(pattern, reading)
                entries.append(entry)
                closing = after
                if entry.error is None:
                    done += 1
                if stop is not None or after is None:
                    break
                reading = after
            if closing is not None:
                self._log_quota(closing)
        self._out(
            f"recorded {done} of {len(patterns)}; ledger: {self._budget.describe(entries, month)}"
        )
        if stop is not None:
            self._out(f"stopped: {stop}")
            return EXIT_STOPPED
        return EXIT_OK

    def _runner(self) -> QuerySource:
        if not isinstance(self._source, QuerySource):
            raise RecordError("this source cannot run virtual-schema queries")
        return self._source

    def _month(self) -> str:
        return ledger.month_of(self._clock())

    def _manifest(self, runner: QuerySource, sql: str) -> fetchlog.Manifest:
        try:
            return fetchlog.parse_manifest(runner.explain(sql))
        except fetchlog.FetchLogError as error:
            raise RecordError(f"the query has no CFBD manifest: {error}") from None

    def _calls(
        self,
        manifest: fetchlog.Manifest,
        names: Sequence[str],
        rows: Sequence[tuple[Any, ...]],
        planned: Sequence[str],
        failure: str | None,
    ) -> list[SliceCall]:
        unknown = [SliceCall(key, SOURCE_LIVE, None, None, None, None, None) for key in planned]
        if failure is not None:
            return unknown
        try:
            calls = slice_calls(manifest, names, rows)
        except fetchlog.FetchLogError as error:
            self._out(f"warning: the request columns do not match the manifest: {error}")
            return unknown
        if calls is None:
            self._out("warning: the result has no REQ_* columns; every planned slice is charged")
            return unknown
        return calls

    def _measure_query(
        self,
        sql: str,
        wall_ms: int,
        rows: int,
        before: Quota,
        after: Quota | None,
        calls: Sequence[SliceCall],
    ) -> None:
        self._write_measurement(
            {
                "query": sql,
                "wall_ms": wall_ms,
                "rows": rows,
                "remaining_before": before.remaining,
                "remaining_after": after.remaining if after else None,
                "slices": [call.__dict__ for call in calls],
            }
        )

    def _write_measurement(self, record: Mapping[str, Any]) -> None:
        self._measurements.mkdir(parents=True, exist_ok=True)
        path = self._measurements / f"{self._run}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def _call(self, pattern: Pattern, before: Quota) -> tuple[Entry, Quota | None, str | None]:
        slice_key = pattern.slice_key
        recording: Recording | None = None
        failure: str | None = None
        try:
            recording = assemble_parts(self._source.fetch(slice_key))
        except (RawFetchFailed, ReplayError) as error:
            failure = str(error)[:ERROR_TEXT_LIMIT] or type(error).__name__
        after = self._try_quota()
        remaining_after = after.remaining if after else None
        attempts = recording.attempts if recording else failure_attempts(failure or "")
        status = recording.status if recording else failure_status(failure or "")
        entry = self._entry(
            slice_key,
            cost=charged(attempts, before.remaining, remaining_after),
            status=status,
            attempts=attempts,
            ms=recording.ms if recording else None,
            bytes=len(recording.body.encode("utf-8")) if recording else None,
            remaining_before=before.remaining,
            remaining_after=remaining_after,
            error=failure,
        )
        self._book.append(entry)
        self._out(_entry_line(entry))
        if recording is not None:
            self._save(pattern, recording)
        return entry, after, _stop_reason(entry, attempts)

    def _save(self, pattern: Pattern, recording: Recording) -> None:
        for store in self._sinks:
            store.save(recording)
        self._write_measurement(measure(self._catalog, pattern, recording))

    def _try_quota(self) -> Quota | None:
        try:
            return read_quota(self._source)
        except RecordError as error:
            self._out(f"warning: {error}")
            return None

    def _log_quota(self, quota: Quota) -> Entry:
        entry = self._entry(
            INFO_SLICE,
            cost=0,
            status=quota.status,
            attempts=quota.attempts,
            ms=quota.ms,
            remaining_before=quota.remaining,
            remaining_after=quota.remaining,
        )
        self._book.append(entry)
        return entry

    def _entry(self, slice_key: str, *, cost: int, **fields: Any) -> Entry:
        return Entry(
            ts=ledger.timestamp(self._clock()),
            plan=self._budget.plan,
            target=self._target,
            run=self._run,
            slice_key=slice_key,
            cost=cost,
            **fields,
        )


def plan_lines(
    to_fetch: Sequence[Pattern],
    recorded: Sequence[Pattern],
    *,
    budget: Budget,
    entries: Sequence[Entry],
    month: str,
    target_name: str,
    book: Path,
) -> list[str]:
    """The printed plan of a run."""
    lines = [
        f"record: target {target_name}, {len(to_fetch)} call(s) planned, "
        f"{len(recorded)} already recorded (0 calls)"
    ]
    lines += [f"  call  {pattern.slice_key}  {pattern.note}".rstrip() for pattern in to_fetch]
    lines += [f"  skip  {pattern.slice_key}  (in cache/replay)" for pattern in recorded]
    lines.append(f"ledger {book}: {budget.describe(entries, month)}")
    lines.append(
        f"each call may take up to {MAX_ATTEMPTS} attempts; a call starts only with "
        f"{MAX_ATTEMPTS} calls of headroom left"
    )
    return lines


def ledger_lines(entries: Sequence[Entry], budget: Budget, month: str, book: Path) -> list[str]:
    """The `--ledger` summary."""
    plans = sorted({entry.plan for entry in entries} | {budget.plan})
    lines = [f"ledger {book}: {len(entries)} entries"]
    for plan in plans:
        total = ledger.plan_total(entries, plan)
        cap = f" of {budget.plan_cap}" if plan == budget.plan else ""
        lines.append(f"  {plan}: {total}{cap} calls")
    lines.append(
        f"  {month}, all plans: {ledger.month_total(entries, month)} of {budget.month_cap} calls"
    )
    readings = [entry for entry in entries if entry.slice_key == INFO_SLICE]
    if readings:
        last = readings[-1]
        lines.append(
            f"  last /info: remainingCalls {last.remaining_after} at {last.ts} ({last.target})"
        )
    return lines


def publish_local(paths: Paths) -> int:
    """Copy every cached recording into the local BucketFS replay directory; returns the count."""
    store = ReplayStore(str(paths.local_bucketfs_replay))
    count = 0
    for path in sorted(paths.replay_cache.rglob("*" + RECORDING_SUFFIX)):
        text = path.read_text(encoding="utf-8")
        try:
            slice_key = json.loads(text)["slice_key"]
        except (ValueError, KeyError, TypeError):
            raise RecordError(f"{path} is not a recording") from None
        store.save(decode_recording(text, slice_key, str(path)))
        count += 1
    return count


SourceOpener = Callable[
    [target.Target, argparse.Namespace, Catalog], AbstractContextManager[RawSource]
]


@contextmanager
def open_database_source(
    deployment: target.Target, options: argparse.Namespace, catalog: Catalog
) -> Iterator[RawSource]:
    """A sys connection to the target, wrapped as a `RawSource`, closed afterwards."""
    try:
        connection = deployment.connect(socket_timeout=SOCKET_TIMEOUT_SECONDS)
    except target.TargetError as error:
        raise RecordError(str(error)) from None
    try:
        yield DatabaseRawSource(
            connection,
            fetch_script=Namespace().fetch_script,
            catalog_hash=catalog.catalog_hash,
            connection_name=options.connection,
            doh_url=options.doh_url,
        )
    finally:
        connection.close()


def utc_now() -> datetime:
    return datetime.now(UTC)


def main(
    argv: list[str] | None = None,
    *,
    paths: Paths | None = None,
    clock: Callable[[], datetime] = utc_now,
    resolve: Callable[..., target.Target] = target.resolve,
    open_source: SourceOpener = open_database_source,
) -> int:
    args = _parser().parse_args(argv)
    paths = paths or Paths()
    book = Ledger(paths.ledger)
    budget = Budget(args.plan, args.cap)
    month = ledger.month_of(clock())
    try:
        if args.ledger:
            print("\n".join(ledger_lines(book.entries(), budget, month, paths.ledger)))
            return EXIT_OK
        if args.publish_local:
            count = publish_local(paths)
            print(f"published {count} recording(s) to {paths.local_bucketfs_replay}")
            return EXIT_OK
        catalog = Catalog.from_json(bundle.DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
        deployment = resolve(args.target, dsn=args.dsn)
        if args.info:
            with open_source(deployment, args, catalog) as source:
                return _recorder(
                    source, args, catalog, book, budget, deployment, paths, clock
                ).info()
        if args.query:
            with open_source(deployment, args, catalog) as source:
                recorder = _recorder(source, args, catalog, book, budget, deployment, paths, clock)
                print("\n".join(recorder.describe_query(args.query)))
                if args.confirm != CONFIRM_WORD:
                    print(
                        "refused: the query was not run; rerun with --confirm yes", file=sys.stderr
                    )
                    return EXIT_REFUSED
                return recorder.query(args.query)
        if not args.patterns:
            raise RecordError("PATTERNS is required: make record PATTERNS=<file> CONFIRM=yes")
        patterns = load_patterns(Path(args.patterns).read_text(encoding="utf-8"), catalog)
        cache = ReplayStore(str(paths.replay_cache))
        to_fetch, recorded = select_patterns(patterns, cache, rerecord=args.rerecord)
        _, note = replay_sinks(deployment.name, paths)
        lines = plan_lines(
            to_fetch,
            recorded,
            budget=budget,
            entries=book.entries(),
            month=month,
            target_name=deployment.name,
            book=paths.ledger,
        )
        print("\n".join([*lines, note]))
        if args.confirm != CONFIRM_WORD:
            print("refused: nothing was sent; rerun with CONFIRM=yes", file=sys.stderr)
            return EXIT_REFUSED
        if not to_fetch:
            print("nothing to call: every pattern is already recorded")
            return EXIT_OK
        with open_source(deployment, args, catalog) as source:
            recorder = _recorder(source, args, catalog, book, budget, deployment, paths, clock)
            return recorder.record(to_fetch)
    except Refused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except (
        RecordError,
        LedgerError,
        ReplayError,
        CatalogError,
        target.TargetError,
        OSError,
    ) as error:
        print(f"record failed: {error}", file=sys.stderr)
        return EXIT_ERROR


def _recorder(
    source: RawSource,
    args: argparse.Namespace,
    catalog: Catalog,
    book: Ledger,
    budget: Budget,
    deployment: target.Target,
    paths: Paths,
    clock: Callable[[], datetime],
) -> Recorder:
    sinks, _ = replay_sinks(deployment.name, paths)
    return Recorder(
        source,
        catalog=catalog,
        book=book,
        budget=budget,
        target_name=deployment.name,
        sinks=sinks,
        measurements=paths.measurements,
        clock=clock,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--patterns", default="", help="YAML file with the slices to record")
    parser.add_argument("--confirm", default="", help=f"must be '{CONFIRM_WORD}' to send calls")
    parser.add_argument("--rerecord", action="store_true", help="fetch already recorded slices")
    parser.add_argument("--ledger", action="store_true", help="print the ledger totals")
    parser.add_argument("--info", action="store_true", help="log one free /info reading")
    parser.add_argument(
        "--query",
        default="",
        help="run one virtual-schema query and log its live calls (needs --confirm yes)",
    )
    parser.add_argument(
        "--publish-local",
        action="store_true",
        help="copy cache/replay into the local BucketFS replay directory",
    )
    parser.add_argument("--plan", default=DEFAULT_PLAN, help="plan the calls count against")
    parser.add_argument("--cap", type=_positive, default=DEFAULT_PLAN_CAP, help="plan call cap")
    parser.add_argument("--connection", default=REAL_CONNECTION, help="CFBD connection name")
    parser.add_argument("--doh-url", default=os.environ.get("DOH_URL", DEFAULT_DOH_URL))
    parser.add_argument(
        "--target", default=None, help="local or aws (default: $TARGET, else local)"
    )
    parser.add_argument("--dsn", default=None, help="overrides host and port of the target")
    return parser


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _pattern(item: Any, number: int) -> Pattern:
    if isinstance(item, str):
        return Pattern(item)
    if not isinstance(item, Mapping):
        raise PatternError(f"pattern {number} must be a slice key or a mapping with 'slice'")
    unknown = sorted(set(item) - _PATTERN_KEYS)
    if unknown:
        raise PatternError(f"pattern {number} has unknown key(s) {', '.join(map(str, unknown))}")
    slice_key, note = item.get("slice"), item.get("note", "")
    if not isinstance(slice_key, str) or not isinstance(note, str):
        raise PatternError(f"pattern {number} needs a string 'slice' (and a string 'note')")
    return Pattern(slice_key, note)


def _template(endpoint: str) -> re.Pattern[str]:
    pieces: list[str] = []
    position = 0
    for placeholder in _PLACEHOLDER.finditer(endpoint):
        pieces.append(re.escape(endpoint[position : placeholder.start()]))
        pieces.append(f"(?P<{_group(placeholder.group(1))}>[^/]+)")
        position = placeholder.end()
    pieces.append(re.escape(endpoint[position:]))
    return re.compile("".join(pieces))


def _group(name: str) -> str:
    return "p_" + re.sub(r"\W", "_", name)


def _path_values(table: Table, path: str) -> dict[str, str]:
    if "{" not in table.endpoint:
        return {}
    matched = _template(table.endpoint).fullmatch(path)
    assert matched is not None
    names = [match.group(1) for match in _PLACEHOLDER.finditer(table.endpoint)]
    return {name: _unquote(matched.group(_group(name))) for name in names}


def _query_values(slice_key: str, query: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in query.split("&") if query else []:
        name, separator, value = item.partition("=")
        if not separator:
            raise PatternError(f"slice {slice_key}: query item {item!r} has no '='")
        name = _unquote(name)
        if name in values:
            raise PatternError(f"slice {slice_key}: parameter {name} appears more than once")
        values[name] = _unquote(value)
    return values


def _unquote(text: str) -> str:
    from urllib.parse import unquote

    return unquote(text)


def _typed(slice_key: str, param: Param, text: str) -> int | float | bool | str:
    if param.type == "integer":
        if not _INTEGER.match(text):
            raise PatternError(f"slice {slice_key}: {param.name} must be an integer, got {text!r}")
        return int(text)
    if param.type == "number":
        try:
            return float(text)
        except ValueError:
            raise PatternError(
                f"slice {slice_key}: {param.name} must be a number, got {text!r}"
            ) from None
    if param.type == "boolean":
        if text not in ("true", "false"):
            raise PatternError(f"slice {slice_key}: {param.name} must be true or false")
        return text == "true"
    return text


def _typed_raw_row(row: dict[str, Any]) -> dict[str, Any]:
    for name in _INTEGER_COLUMNS:
        if row[name] is not None:
            row[name] = int(row[name])
    return row


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _as_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value) if value >= 0 else None


def _stop_reason(entry: Entry, attempts: int | None) -> str | None:
    if entry.error is not None:
        return f"{entry.slice_key} failed: {entry.error}"
    if entry.remaining_after is None:
        return f"no /info reading after {entry.slice_key}; stopping so no call goes unaccounted"
    if attempts is not None and entry.cost > attempts:
        return (
            f"{entry.slice_key} made {attempts} attempt(s) but remainingCalls dropped by "
            f"{entry.cost}; check before spending more"
        )
    return None


def _entry_line(entry: Entry) -> str:
    outcome = entry.error or f"HTTP {entry.status}, {entry.bytes} bytes, {entry.ms} ms"
    return (
        f"  {entry.slice_key}: {outcome}; attempts {entry.attempts}, cost {entry.cost}, "
        f"remainingCalls {entry.remaining_before} -> {entry.remaining_after}"
    )


if __name__ == "__main__":
    sys.exit(main())
