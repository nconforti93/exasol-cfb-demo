"""The spending guard's pure part: plans from manifests, the guard decision, the ledger entries of a
live statement (success and failure), the fetch-log rows, and the live timeouts.

`live.py` executes; this module only decides and builds. A plan costs 0 calls to make (it comes from
`EXPLAIN VIRTUAL`). The guard refuses in a fixed order and names the guard in its message.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from vs.cfbd_vs import http as cfbd_http
from vs.cfbd_vs import resolver
from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.host import fetchlog, ledger
from vs.host.fetchlog import FetchLogRow, Manifest
from vs.host.ledger import Entry
from vs.host.livecalls import SOURCE_LIVE, SliceCall, charged

SOURCE_REPLAY = "REPLAY"
SOURCE_STORED = fetchlog.SOURCE_STORED
SOURCE_NONE = "NONE"
RETRY_HEADROOM = MAX_ATTEMPTS - 1
POOL_QUERY_TIMEOUT_SECONDS = 30
LIVE_QUERY_TIMEOUT_SECONDS = 300
LIVE_SOCKET_TIMEOUT_SECONDS = 330
ERROR_LIMIT = 500
STATUS_CONFLICT = 409
STATUS_FORBIDDEN = 403


def worst_case_fetch_seconds() -> float:
    """The longest one slice can take in the fetch function: every attempt times out, every wait is
    the longest `Retry-After` it honors, and name resolution takes its whole timeout. Slices of one
    statement run in parallel."""
    attempts = MAX_ATTEMPTS * cfbd_http.ATTEMPT_TIMEOUT_SECONDS
    waits = (MAX_ATTEMPTS - 1) * cfbd_http.RETRY_AFTER_LIMIT_SECONDS
    return attempts + waits + resolver.DOH_TIMEOUT_SECONDS


@dataclass(frozen=True)
class SpendPlan:
    """What an action may cost: where its rows come from and the most live calls it can make."""

    source: str
    live_calls: int
    fetch_mode: str | None


@dataclass(frozen=True)
class LedgerState:
    """The ledger as the guard sees it: the app's plan and the month, caps and totals."""

    enabled: bool
    plan: str
    plan_cap: int | None
    plan_total: int
    month: str
    month_cap: int | None
    month_total: int

    @property
    def plan_room(self) -> int | None:
        return None if self.plan_cap is None else self.plan_cap - self.plan_total

    @property
    def month_room(self) -> int | None:
        return None if self.month_cap is None else self.month_cap - self.month_total


@dataclass(frozen=True)
class QuotaState:
    """A reading of `CFB_META.QUOTA`: what a refresh may spend and what on-demand spend may."""

    backfill_spendable: int | None
    refresh_spendable: int | None = None
    refresh_reserve: int | None = None


@dataclass(frozen=True)
class Refusal:
    """A guard that said no: the error code, the HTTP status and a message naming the guard."""

    code: str
    status: int
    message: str


def plan_from_manifests(manifests: Sequence[Manifest]) -> SpendPlan:
    """Plan an action from the manifests of its `EXPLAIN VIRTUAL`: the live calls are the slices of
    every manifest whose sources hold `LIVE`; an upper bound, since stored slices are skipped at
    run time."""
    live = sum(len(item.slice_keys) for item in manifests if SOURCE_LIVE in item.sources)
    sources = {source for item in manifests for source in item.sources}
    if SOURCE_LIVE in sources:
        source = SOURCE_LIVE
    elif SOURCE_REPLAY in sources:
        source = SOURCE_REPLAY
    elif SOURCE_STORED in sources:
        source = SOURCE_STORED
    else:
        source = SOURCE_NONE
    fetch = next((item.fetch_source for item in manifests if item.fetch_source), None)
    return SpendPlan(source, live, fetch)


def live_disabled_reasons(state: LedgerState) -> list[str]:
    """Why the ledger configuration alone makes live actions impossible; empty when it does not."""
    if not state.enabled:
        return ["ledger off"]
    reasons = []
    if state.plan_cap is None:
        reasons.append(f"no ledger cap for plan {state.plan}")
    if state.month_cap is None:
        reasons.append(f"no ledger cap for {state.month}")
    return reasons


def check_guard(
    plan: SpendPlan,
    *,
    max_calls: int,
    ledger_state: LedgerState,
    quota: QuotaState | None,
    operator_enabled: bool,
    operator_reason: str,
) -> Refusal | None:
    """The guards of a live plan, in order: confirmed amount, operator path, ledger caps, ledger
    room (with the retry headroom), quota. A plan without live calls is never refused."""
    if plan.live_calls == 0:
        return None
    needed = plan.live_calls + RETRY_HEADROOM
    for check in (
        _confirmed(plan, max_calls),
        _operator(operator_enabled, operator_reason),
        _caps(ledger_state),
        _room(plan, needed, ledger_state),
        _quota(plan, needed, quota),
    ):
        if check is not None:
            return check
    return None


def _confirmed(plan: SpendPlan, max_calls: int) -> Refusal | None:
    if plan.live_calls <= max_calls:
        return None
    noun = "call" if plan.live_calls == 1 else "calls"
    return Refusal(
        "max_calls_exceeded",
        STATUS_CONFLICT,
        f"the plan makes {plan.live_calls} planned live {noun} against {max_calls} confirmed; "
        "the mode may have changed since the label was shown",
    )


def _operator(enabled: bool, reason: str) -> Refusal | None:
    if enabled:
        return None
    return Refusal(
        "operator_disabled",
        STATUS_FORBIDDEN,
        f"live requests need the operator path to log them ({reason})",
    )


def _caps(state: LedgerState) -> Refusal | None:
    reasons = live_disabled_reasons(state)
    if not reasons:
        return None
    return Refusal(
        "live_disabled", STATUS_CONFLICT, f"live actions are disabled: {'; '.join(reasons)}"
    )


def _room(plan: SpendPlan, needed: int, state: LedgerState) -> Refusal | None:
    if not state.enabled:
        return None
    plan_room, month_room = state.plan_room, state.month_room
    if plan_room is not None and needed > plan_room:
        return Refusal(
            "ledger_refused",
            STATUS_CONFLICT,
            f"plan {state.plan} (cap {state.plan_cap}) has {plan_room} left; {needed} are needed: "
            f"{plan.live_calls} planned plus {RETRY_HEADROOM} calls of retry headroom",
        )
    if month_room is not None and needed > month_room:
        return Refusal(
            "ledger_refused",
            STATUS_CONFLICT,
            f"month {state.month} (cap {state.month_cap}) has {month_room} left; {needed} are "
            f"needed: {plan.live_calls} planned plus {RETRY_HEADROOM} calls of retry headroom",
        )
    return None


def _quota(plan: SpendPlan, needed: int, quota: QuotaState | None) -> Refusal | None:
    if quota is None or quota.backfill_spendable is None:
        return Refusal("quota_refused", STATUS_CONFLICT, "the key's quota could not be read")
    if needed <= quota.backfill_spendable:
        return None
    refresh = (
        "" if quota.refresh_spendable is None else f" (REFRESH_SPENDABLE {quota.refresh_spendable}"
    )
    reserve = (
        ""
        if quota.refresh_reserve is None
        else f" minus the refresh reserve {quota.refresh_reserve}"
    )
    detail = f"{refresh}{reserve})" if refresh else ""
    return Refusal(
        "quota_refused",
        STATUS_CONFLICT,
        f"{needed} calls are needed ({plan.live_calls} planned plus {RETRY_HEADROOM} calls of "
        f"retry headroom) but BACKFILL_SPENDABLE {quota.backfill_spendable}{detail} leaves the "
        "weekly refresh's reserve alone",
    )


def entries_for_calls(
    calls: Sequence[SliceCall], *, plan: str, target: str, run: str, now: datetime
) -> list[Entry]:
    """One ledger entry per `LIVE` slice of a result, charged its attempts (all of them when
    unknown). Replayed and stored slices are not charged."""
    return [
        Entry(
            ts=ledger.timestamp(now),
            plan=plan,
            target=target,
            run=run,
            slice_key=call.slice_key,
            cost=charged(call.attempts, None, None),
            status=call.status,
            attempts=call.attempts,
            ms=call.ms,
        )
        for call in calls
        if call.source == SOURCE_LIVE
    ]


def failure_entries(
    planned: Sequence[str], error: str, *, plan: str, target: str, run: str, now: datetime
) -> list[Entry]:
    """When a statement failed, CFBD may have been called up to `MAX_ATTEMPTS` times per slice:
    one entry per planned live slice, status and attempts unknown, the error's first line."""
    first_line = _first_line(error)
    return [
        Entry(
            ts=ledger.timestamp(now),
            plan=plan,
            target=target,
            run=run,
            slice_key=key,
            cost=MAX_ATTEMPTS,
            error=first_line,
        )
        for key in planned
    ]


def fetch_log_rows(
    manifests: Sequence[Manifest],
    columns: Sequence[str] | None,
    rows: Sequence[Sequence[Any]] | None,
    *,
    context: str,
    logged_at: datetime,
) -> list[FetchLogRow]:
    """The fetch-log rows of a statement: one per slice the manifests plan, from the result's
    `REQ_*` columns. A failed statement (`columns` None) logs its planned fetch slices with status
    unknown and 0 rows. Rows per slice are counted from the result when `REQ_ROWS` is not
    selected."""
    logged: list[FetchLogRow] = []
    for manifest in manifests:
        metadata = [] if columns is None or rows is None else _metadata(manifest, columns, rows)
        logged += fetchlog.build_fetch_log_rows(
            manifest, metadata, logged_at=logged_at, context=context
        )
    return logged


def _metadata(
    manifest: Manifest, columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> list[dict[str, Any]]:
    names = list(columns)
    if "REQ_SLICE" not in names:
        return _whole_result(manifest, len(rows))
    planned = set(manifest.slice_keys)
    counts: dict[str, int] = {}
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        values = {
            column: row[names.index(column)] if column in names else None
            for column in fetchlog.REQUEST_COLUMNS
        }
        key = values["REQ_SLICE"]
        if key not in planned:
            continue
        counts[key] = counts.get(key, 0) + 1
        seen.setdefault(key, values)
    for key, values in seen.items():
        if "REQ_ROWS" not in names or values["REQ_ROWS"] is None:
            values["REQ_ROWS"] = counts[key]
    return list(seen.values())


def _whole_result(manifest: Manifest, row_count: int) -> list[dict[str, Any]]:
    source = manifest.fetch_source
    if len(manifest.slice_keys) != 1 or source is None:
        return []
    values: dict[str, Any] = dict.fromkeys(fetchlog.REQUEST_COLUMNS)
    values.update(REQ_SLICE=manifest.slice_keys[0], REQ_SOURCE=source, REQ_ROWS=row_count)
    return [values]


def _first_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip() not in ("", "(", ")")]
    return (lines[0] if lines else "the statement failed")[:ERROR_LIMIT]
