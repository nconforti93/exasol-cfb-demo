"""Shared loader estimate, spending decision, run, record and reconciliation logic.

The caller supplies the ledger, plan name and lock policy. The namespace never chooses a ledger:
that choice belongs to the CLI or app. Every query here is a fixed template with validated numeric
arguments and names supplied by ``Namespace``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from loader.plan import profile_rank
from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.cfbd_vs.sqlrender import quote_identifier
from vs.host import ledger
from vs.host.ledger import Ledger, LedgerError, LedgerRefused
from vs.host.namespace import Namespace

Query = Callable[[str], list[tuple[Any, ...]]]


@dataclass(frozen=True)
class Estimate:
    calls: int
    per_table: tuple[tuple[str, int], ...]
    blocked: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Spend:
    expected: int
    limit: int | None
    room: int | None
    wanted: int
    capped_from: int | None = None


@dataclass(frozen=True)
class Decision:
    estimate: Estimate
    mode: str
    quota: Any | None
    spend: Spend
    verdict: str | None


@dataclass(frozen=True)
class Outcome:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    error: str | None = None


@dataclass(frozen=True)
class Record:
    calls: int
    run_id: int
    note: str


class Connection(Protocol):
    def execute(self, sql: str) -> Any: ...


def _meta(namespace: Namespace, table: str) -> str:
    return f"{quote_identifier(namespace.meta_schema)}.{quote_identifier(table)}"


def _estimate(counts: Sequence[Sequence[Any]], blocked: Sequence[Sequence[Any]]) -> Estimate:
    per_table = tuple((str(name), int(count)) for name, count in counts)
    return Estimate(
        sum(count for _, count in per_table),
        per_table,
        tuple((str(name), str(reason)) for name, reason in blocked),
    )


def plan_backfill(
    query: Query, namespace: Namespace, profile: str, from_season: int | None
) -> Estimate:
    rank = profile_rank(profile)
    if from_season is not None and (
        type(from_season) is not int or not 1869 <= from_season <= 2100
    ):
        raise ValueError("from_season must be a season from 1869 to 2100")
    season = "" if from_season is None else f' AND ("SEASON" IS NULL OR "SEASON" <= {from_season})'
    queue = query(
        f'SELECT "TABLE_NAME", COUNT(*) FROM {_meta(namespace, "BACKFILL_QUEUE")} '
        f'WHERE "PROFILE_RANK" <= {rank}{season} GROUP BY "TABLE_NAME" ORDER BY "TABLE_NAME"'
    )
    blocked = query(
        f'SELECT "TABLE_NAME", "BLOCKED_BY" FROM {_meta(namespace, "LOAD_STATUS")} '
        f'WHERE "BLOCKED_BY" IS NOT NULL AND "PROFILE_RANK" <= {rank} '
        'GROUP BY "TABLE_NAME", "BLOCKED_BY" ORDER BY "TABLE_NAME"'
    )
    return _estimate(queue, blocked)


def plan_refresh(query: Query, namespace: Namespace, season: int) -> Estimate:
    if type(season) is not int or not 1869 <= season <= 2100:
        raise ValueError("season must be from 1869 to 2100")
    slices, settings, orphans = (
        _meta(namespace, name) for name in ("LOAD_SLICES", "LOADER_SETTINGS", "LOAD_ORPHANS")
    )
    candidates = query(
        f'SELECT S."TABLE_NAME", COUNT(*) FROM {slices} S CROSS JOIN {settings} CFG '
        f'WHERE S."REFRESH_CLASS" = \'weekly\' AND S."SEASON" = {season} '
        'AND NOT S."IS_GAP" AND NOT S."IS_FINAL" '
        "AND INSTR(',' || CFG.\"REFRESH_PROFILES\" || ',', "
        "',' || S.\"PROFILE_NAME\" || ',') > 0 "
        f'AND NOT EXISTS (SELECT 1 FROM {orphans} O WHERE O."TABLE_NAME" = S."TABLE_NAME") '
        'GROUP BY S."TABLE_NAME" ORDER BY S."TABLE_NAME"'
    )
    blocked = query(
        f'SELECT "TABLE_NAME", "BLOCKED_BY" FROM {_meta(namespace, "LOAD_STATUS")} '
        'WHERE "BLOCKED_BY" IS NOT NULL AND "REFRESH_CLASS" = \'weekly\' '
        'GROUP BY "TABLE_NAME", "BLOCKED_BY" ORDER BY "TABLE_NAME"'
    )
    return _estimate(candidates, blocked)


def decide(
    estimate: Estimate,
    *,
    mode: str,
    quota: Any | None,
    book: Ledger,
    ledger_plan: str,
    requested: int | None,
    limit_applies: bool,
    retry_headroom: int = 0,
) -> Decision:
    """The guard's structured plan. A CLI can print it; an app can render it as JSON."""
    if requested is not None and (type(requested) is not int or requested < 0):
        raise ValueError("requested calls must be a non-negative integer")
    if retry_headroom < 0:
        raise ValueError("retry headroom must not be negative")
    live = mode.lower() == "live"
    month = ledger.month_of(datetime.now(UTC))
    used = None if quota is None else quota.used_calls
    room = book.room(ledger_plan, month, used) if live and book.enabled else None
    wanted = estimate.calls if requested is None else min(estimate.calls, requested)
    if room is None or not limit_applies:
        spend = Spend(wanted, requested, room, wanted)
    else:
        limit = room if requested is None else min(requested, room)
        capped = requested if requested is not None and requested > room else None
        spend = Spend(min(estimate.calls, limit), limit, room, wanted, capped)
    verdict = None
    if live and quota is not None and book.enabled:
        try:
            book.check(ledger_plan, month, spend.expected, used, headroom=retry_headroom)
        except LedgerRefused as error:
            verdict = str(error)
        if verdict is None and limit_applies and room == 0 and wanted > 0:
            verdict = (
                f"ledger room is 0 for plan {ledger_plan} in {month}, "
                f"and the run needs {wanted} calls"
            )
    return Decision(estimate, mode, quota, spend, verdict)


def run(
    connection: Connection, statement: str, first_line: Callable[[BaseException], str]
) -> Outcome:
    """Send a loader statement exactly once and return its result or first error line."""
    try:
        reply = connection.execute(statement)
        return Outcome(tuple(reply.column_names()), tuple(tuple(row) for row in reply.fetchall()))
    except Exception as error:
        return Outcome((), (), first_line(error))


def record(
    book: Ledger,
    ledger_plan: str,
    target: str,
    *,
    kind: str,
    run_id: int,
    script_calls: int | None,
    remaining_before: int,
    remaining_after: int | None,
    worst_case: int,
    error: str | None,
    clock: Callable[[], datetime],
) -> Record:
    delta = None if remaining_after is None else max(0, remaining_before - remaining_after)
    known = [value for value in (script_calls, delta) if value is not None]
    calls = max(known) if known else worst_case
    note = (
        f"script reports {script_calls if script_calls is not None else '?'}, "
        f"/info delta {delta if delta is not None else '?'}"
    )
    book.record_run(
        ledger_plan,
        target,
        kind=kind,
        run_id=run_id,
        calls=calls,
        remaining_before=remaining_before,
        remaining_after=remaining_after,
        error=error,
        clock=clock,
    )
    return Record(calls, run_id, note)


def lock(book: Ledger, *, policy: str, live: bool) -> AbstractContextManager[None]:
    """CLI locks every charged real run; the app locks live runs only."""
    if policy not in ("all", "live"):
        raise ValueError("unknown loader lock policy")
    return book.exclusive() if book.enabled and (policy == "all" or live) else nullcontext()


class ReconcilePending(RuntimeError):
    """A known run could not yet be charged safely; all new live actions must refuse."""


def reconcile(
    connection: Connection,
    namespace: Namespace,
    book: Ledger,
    ledger_plan: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    *,
    target: str = "local",
) -> list[Record]:
    """Charge unrecorded live loader runs in this UTC month, under the ledger lock."""
    if not book.enabled:
        return []
    now = clock()
    month = now.strftime("%Y-%m")
    start = f"{month}-01 00:00:00"
    year, number = divmod(now.year * 12 + now.month, 12)
    next_month = f"{year:04d}-{number + 1:02d}-01 00:00:00"
    runs_sql = (
        'SELECT "RUN_ID", "RUN_KIND", "SESSION_ID", "RUN_STATUS", "CALLS", '
        '"REMAINING_BEFORE", "REMAINING_AFTER" '
        f"FROM {_meta(namespace, 'LOAD_RUN')} "
        "WHERE \"RUN_KIND\" IN ('BACKFILL', 'REFRESH_WEEK') "
        'AND "REMAINING_BEFORE" IS NOT NULL '
        f"AND \"STARTED_AT\" >= TIMESTAMP '{start}' "
        f'AND "STARTED_AT" < TIMESTAMP \'{next_month}\' ORDER BY "RUN_ID"'
    )
    try:
        with book.exclusive():
            runs = connection.execute(runs_sql).fetchall()
            existing = {entry.slice_key for entry in book.entries()}
            recorded = []
            for raw in runs:
                run_id, kind, session, status, calls, before, after = raw
                key = f"{kind}#{int(run_id)}"
                if key in existing:
                    continue
                if str(status) == "RUNNING":
                    active = connection.execute(
                        'SELECT "SESSION_ID" FROM "SYS"."EXA_ALL_SESSIONS" '
                        f'WHERE "SESSION_ID" = {int(session)}'
                    ).fetchall()
                    if active:
                        continue
                steps = connection.execute(
                    'SELECT "STEP_NAME", "STEP_STATUS", "CALLS", "SLICES" '
                    f'FROM {_meta(namespace, "REFRESH_LOG")} WHERE "RUN_ID" = {int(run_id)}'
                ).fetchall()
                committed = sum(int(row[2] or 0) for row in steps)
                unfinished = sum(
                    int(row[3] or 0) * MAX_ATTEMPTS
                    for row in steps
                    if str(row[0]).startswith("FETCH ") and str(row[1]) == "RUNNING"
                )
                estimated = committed + unfinished
                charged = int(calls) if calls is not None else estimated
                charged = max(charged, estimated)
                book.record_run(
                    ledger_plan,
                    target,
                    kind=str(kind),
                    run_id=int(run_id),
                    calls=charged,
                    remaining_before=int(before),
                    remaining_after=None if after is None else int(after),
                    error=f"recorded by reconciliation: {status}",
                    clock=clock,
                )
                recorded.append(
                    Record(charged, int(run_id), f"recorded by reconciliation: {status}")
                )
                existing.add(key)
            return recorded
    except (LedgerError, OSError, RuntimeError) as error:
        raise ReconcilePending("loader reconciliation could not finish") from error
