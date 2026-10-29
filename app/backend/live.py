"""Guarded execution of a paid virtual-table statement: the preview and the curated console.

The serving view cannot push its outer game-id predicate into the adapter, so the preview uses the
operator allowlist's one direct virtual-table template. The console runs its fixed statements on the
console user's session; the owner connection then only reads the quota and writes the fetch log.
Neither path interpolates request text.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.backend import explain, spend
from app.backend.operator import OperatorPath
from app.backend.sessions import GateTimeout, LiveGate
from vs.host import fetchlog, ledger
from vs.host.livecalls import planned_live, slice_calls
from vs.host.namespace import Namespace


@dataclass(frozen=True)
class LiveResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    plan: spend.SpendPlan
    calls_charged: int
    fetch_log_ok: bool
    logged: tuple[fetchlog.FetchLogRow, ...] = ()


class LiveRefused(RuntimeError):
    """A stable code and status for a refusal made before a paid statement is sent."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class LiveFailed(RuntimeError):
    """The single paid statement failed; the ledger has already been charged."""

    def __init__(self, message: str, calls_charged: int) -> None:
        super().__init__(message)
        self.calls_charged = calls_charged


def preview_statement(namespace: Namespace, game_id: int) -> str:
    """Fixed direct-VS statement with a checked decimal literal, required for pushdown."""
    if type(game_id) is not int or not 1 <= game_id < 10**18:
        raise ValueError("game_id must be a positive integer below 10^18")
    return f'SELECT * FROM "{namespace.vs_name}"."GAMES_PREVIEW" WHERE "P_GAME_ID" = {game_id}'


CONTEXTS = frozenset({"APP_PREVIEW", "APP_CONSOLE"})


class LiveExecutor:
    """Plan and run a statement once, with the gate, ledger, quota, and fetch-log ordering."""

    def __init__(
        self,
        namespace: Namespace,
        operator: OperatorPath,
        book: ledger.Ledger,
        *,
        target: str,
        ledger_plan: str = "app",
        gate: LiveGate | None = None,
        busy: Callable[[], str | None] = lambda: None,
        reconcile_pending: Callable[[], bool] = lambda: False,
        on_record_failure: Callable[[], None] = lambda: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.namespace = namespace
        self.operator = operator
        self.book = book
        self.target = target
        self.ledger_plan = ledger_plan
        self.gate = gate or LiveGate()
        self.busy = busy
        self.reconcile_pending = reconcile_pending
        self.on_record_failure = on_record_failure
        self.clock = clock

    def plan_preview(self, game_id: int) -> spend.SpendPlan:
        sql = preview_statement(self.namespace, game_id)
        with self.operator.connection() as connection:
            return self._plan(connection, sql)[0]

    def run_preview(self, game_id: int, *, max_calls: int, action_id: str) -> LiveResult:
        sql = preview_statement(self.namespace, game_id)
        return self.run_statement(
            sql,
            max_calls=max_calls,
            action_id=action_id,
            label=f"preview {game_id}",
            context="APP_PREVIEW",
        )

    def run_statement(
        self,
        sql: str,
        *,
        max_calls: int,
        action_id: str,
        label: str,
        context: str,
        query: Any | None = None,
    ) -> LiveResult:
        """Plan, guard and send `sql` once. `query` is the principal's connection for `EXPLAIN` and
        the statement (the owner connection when None); quota and fetch log stay on the owner."""
        if not action_id.isascii() or not action_id.isalnum() or len(action_id) > 40:
            raise ValueError("action_id must be 1-40 ASCII letters or digits")
        if context not in CONTEXTS:
            raise ValueError(f"context must be one of {', '.join(sorted(CONTEXTS))}")
        try:
            with self.gate.hold(label):
                self._check_busy()
                with self.operator.connection() as owner:
                    principal = owner if query is None else query
                    initial, _ = self._plan(principal, sql)
                    if initial.live_calls:
                        try:
                            with self.book.exclusive():
                                self._check_busy()
                                # The mode, cache, or stored slices may change while waiting.
                                return self._guard_and_run(
                                    owner,
                                    principal,
                                    sql,
                                    max_calls,
                                    f"{context}#{action_id}",
                                    lock_held=True,
                                )
                        except ledger.LedgerError as error:
                            raise LiveRefused("live_busy", str(error)) from None
                    return self._guard_and_run(
                        owner, principal, sql, max_calls, f"{context}#{action_id}", lock_held=False
                    )
        except GateTimeout as error:
            raise LiveRefused("live_busy", str(error)) from None

    def _plan(
        self, connection: Any, sql: str
    ) -> tuple[spend.SpendPlan, tuple[fetchlog.Manifest, ...]]:
        statement = connection.execute(f"EXPLAIN VIRTUAL {sql}")
        explained = explain.parse_explain(statement.column_names(), statement.fetchall())
        return spend.plan_from_manifests(explained.manifests), explained.manifests

    def _check_busy(self) -> None:
        if self.reconcile_pending():
            raise LiveRefused("reconcile_pending", "a loader run still needs ledger reconciliation")
        holder = self.busy()
        if holder:
            raise LiveRefused("live_busy", f"a refresh is running ({holder})")

    def _guard_and_run(
        self,
        connection: Any,
        principal: Any,
        sql: str,
        max_calls: int,
        run: str,
        *,
        lock_held: bool,
    ) -> LiveResult:
        plan, manifests = self._plan(principal, sql)
        if plan.live_calls and not lock_held:
            raise LiveRefused("live_busy", "the statement became live; retry with a fresh plan")
        quota, used = self._quota(connection) if plan.live_calls else (None, None)
        state = self._ledger_state()
        refusal = spend.check_guard(
            plan,
            max_calls=max_calls,
            ledger_state=state,
            quota=quota,
            operator_enabled=self.operator.enabled,
            operator_reason=self.operator.reason,
        )
        if refusal is not None:
            raise LiveRefused(refusal.code, refusal.message, refusal.status)
        if plan.live_calls:
            try:
                self.book.check(
                    self.ledger_plan,
                    state.month,
                    plan.live_calls,
                    used,
                    headroom=spend.RETRY_HEADROOM,
                )
            except ledger.LedgerRefused as error:
                raise LiveRefused("ledger_refused", str(error)) from None
        columns: tuple[str, ...] | None = None
        rows: tuple[tuple[Any, ...], ...] | None = None
        failure: BaseException | None = None
        try:
            statement = principal.execute(sql)
            columns = tuple(statement.column_names())
            rows = tuple(tuple(row) for row in statement.fetchall())
        except BaseException as error:
            failure = error
        # A paid query has been sent. Record even if the database or fetch-log write fails.
        now = self.clock()
        entries = self._entries(manifests, columns, rows, failure, run, now)
        try:
            for entry in entries:
                self.book.append(entry)
        except BaseException:
            self.on_record_failure()
            raise LiveFailed(
                "the ledger write failed after the query; live actions are disabled",
                sum(entry.cost for entry in entries),
            ) from None
        charged = sum(entry.cost for entry in entries)
        logged = spend.fetch_log_rows(
            manifests, columns, rows, context=run, logged_at=now.replace(tzinfo=None)
        )
        try:
            fetchlog.insert_fetch_log(connection, self.namespace.meta_schema, logged)
            fetch_log_ok = True
        except Exception:
            # The paid result must still be returned; the ledger was charged first.
            fetch_log_ok = False
        if failure is not None:
            raise LiveFailed(explain.refusal_message(failure), charged) from None
        assert columns is not None and rows is not None
        return LiveResult(columns, rows, plan, charged, fetch_log_ok, tuple(logged))

    def _entries(
        self,
        manifests: Sequence[fetchlog.Manifest],
        columns: tuple[str, ...] | None,
        rows: tuple[tuple[Any, ...], ...] | None,
        failure: BaseException | None,
        run: str,
        now: datetime,
    ) -> list[ledger.Entry]:
        entries: list[ledger.Entry] = []
        for manifest in manifests:
            planned = planned_live(manifest)
            if failure is not None or columns is None or rows is None:
                entries += spend.failure_entries(
                    planned,
                    str(failure),
                    plan=self.ledger_plan,
                    target=self.target,
                    run=run,
                    now=now,
                )
                continue
            calls = slice_calls(manifest, columns, list(rows))
            if calls is None:
                entries += spend.failure_entries(
                    planned,
                    "request metadata unavailable",
                    plan=self.ledger_plan,
                    target=self.target,
                    run=run,
                    now=now,
                )
            else:
                entries += spend.entries_for_calls(
                    calls, plan=self.ledger_plan, target=self.target, run=run, now=now
                )
        return entries

    def _ledger_state(self) -> spend.LedgerState:
        month = ledger.month_of(self.clock())
        entries = self.book.entries() if self.book.enabled else []
        config = self.book.config if self.book.enabled else None
        return spend.LedgerState(
            enabled=self.book.enabled,
            plan=self.ledger_plan,
            plan_cap=None if config is None else config.plans.get(self.ledger_plan),
            plan_total=ledger.plan_total(entries, self.ledger_plan),
            month=month,
            month_cap=None if config is None else config.months.get(month),
            month_total=ledger.month_total(entries, month),
        )

    def _quota(self, connection: Any) -> tuple[spend.QuotaState, int | None]:
        statement = connection.execute(f'SELECT * FROM "{self.namespace.meta_schema}"."QUOTA"')
        rows = statement.fetchall()
        if not rows:
            return spend.QuotaState(None), None
        values = dict(zip(statement.column_names(), rows[0], strict=True))

        def number(name: str) -> int | None:
            return None if values.get(name) is None else int(values[name])

        return (
            spend.QuotaState(
                number("BACKFILL_SPENDABLE"), number("REFRESH_SPENDABLE"), number("REFRESH_RESERVE")
            ),
            number("USED_CALLS"),
        )
