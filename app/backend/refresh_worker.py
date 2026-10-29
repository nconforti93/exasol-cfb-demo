"""One guarded owner refresh in a background thread, with a durable ledger charge."""

from __future__ import annotations

import contextlib
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.backend import reconcile, sqlcatalog
from app.backend.actions import ActionStore
from app.backend.operator import REFRESH_SOCKET_TIMEOUT_SECONDS, OperatorPath, refresh_statement
from app.backend.sessions import GateTimeout, LiveGate
from app.backend.settings import Settings
from loader import guard
from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.host import ledger

POLL_RUN_SECONDS = 1.0
MAX_ERROR_LENGTH = 500


class RefreshRefused(RuntimeError):
    """A guard refused before sending the refresh script."""


def first_line(error: BaseException) -> str:
    """A bounded database error for the action response and ledger."""
    lines = str(error).splitlines()
    return (lines[0] if lines else type(error).__name__)[:MAX_ERROR_LENGTH]


def point_ref(database: Any, settings: Settings) -> dict[str, Any] | None:
    """The app-role serving point before or after a refresh."""
    statement = sqlcatalog.CATALOG["current_point"]
    rows = database.pool.run(
        statement.sql,
        sqlcatalog.parameters(
            statement,
            api_schema=settings.api_schema,
            api_live_schema=settings.api_live_schema,
        ),
    ).dicts()
    if not rows:
        return None
    row = rows[0]
    return {
        "season": int(row["AS_OF_SEASON"]),
        "week_seq": int(row["AS_OF_WEEK_SEQ"]),
        "label": str(row["AS_OF_LABEL"]),
    }


def _query(connection: Any, sql: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in connection.execute(sql).fetchall()]


def _quota(connection: Any, meta: str) -> dict[str, int] | None:
    result = connection.execute(f'SELECT * FROM "{meta}"."QUOTA"')
    rows = result.fetchall()
    if not rows:
        return None
    values = dict(zip(result.column_names(), rows[0], strict=True))
    names = ("USED_CALLS", "REMAINING_CALLS", "REFRESH_SPENDABLE")
    if any(values.get(name) is None for name in names):
        return None
    return {name: int(values[name]) for name in names}


def _run_id(operator: OperatorPath, settings: Settings, session_id: int) -> int | None:
    from app.backend.operator import run_id_statement

    with operator.connection() as connection:
        rows = _query(connection, run_id_statement(settings.namespace, session_id))
    return int(rows[0][0]) if rows else None


def _watch_run_id(
    operator: OperatorPath,
    settings: Settings,
    session_id: int,
    actions: ActionStore,
    action_id: str,
    stop: threading.Event,
) -> None:
    while not stop.is_set():
        try:
            found = _run_id(operator, settings, session_id)
            if found is not None:
                actions.update(action_id, run_id=found)
                return
        except Exception:
            pass
        stop.wait(POLL_RUN_SECONDS)


def _run_row(connection: Any, meta: str, run_id: int) -> dict[str, Any] | None:
    result = connection.execute(f'SELECT * FROM "{meta}"."LOAD_RUN" WHERE "RUN_ID" = {run_id}')
    rows = result.fetchall()
    return None if not rows else dict(zip(result.column_names(), rows[0], strict=True))


def run_refresh(
    state: Any,
    settings: Settings,
    book: ledger.Ledger,
    actions: ActionStore,
    action_id: str,
    *,
    season: int,
    week: int,
    season_type: str,
    max_calls: int,
    planned_mode: str,
) -> None:
    """Own the live gate and ledger lock until the sent script has been charged."""
    error: str | None = None
    charged: int | None = None
    after: dict[str, Any] | None = None
    try:
        gate: LiveGate = state.live_gate
        with gate.hold(f"refresh {season} week {week}"):
            with guard.lock(book, policy="live", live=planned_mode == "LIVE"):  # noqa: SIM117
                with state.operator.connection(
                    timeout_seconds=REFRESH_SOCKET_TIMEOUT_SECONDS
                ) as connection:
                    # The final estimate, mode and quota are read while the ledger lock is held.
                    mode = state.operator.fetch_mode(settings.namespace)
                    if mode != planned_mode:
                        raise RefreshRefused("the virtual schema mode changed; plan again")
                    estimate = guard.plan_refresh(
                        lambda sql: _query(connection, sql), settings.namespace, season
                    )
                    if estimate.blocked:
                        raise RefreshRefused("a loader table is blocked; plan again")
                    running = _query(
                        connection,
                        'SELECT "RUN_ID", "RUN_KIND", "STARTED_AT" FROM '
                        f'"{settings.namespace.meta_schema}"."LOAD_RUN" '
                        "WHERE \"RUN_STATUS\" = 'RUNNING'",
                    )
                    if running:
                        raise RefreshRefused(f"refresh run {int(running[0][0])} is already running")
                    needed = estimate.calls if mode == "LIVE" else 0
                    if needed > max_calls:
                        raise RefreshRefused(
                            f"the refresh now needs {needed} calls, above the {max_calls} confirmed"
                        )
                    quota = (
                        _quota(connection, settings.namespace.meta_schema)
                        if mode == "LIVE"
                        else None
                    )
                    if mode == "LIVE":
                        if not book.enabled:
                            raise RefreshRefused("ledger off: live refresh is disabled")
                        if quota is None:
                            raise RefreshRefused("the key's quota could not be read")
                        decision = guard.decide(
                            estimate,
                            mode=mode,
                            quota=SimpleNamespace(used_calls=quota["USED_CALLS"]),
                            book=book,
                            ledger_plan=settings.ledger_plan,
                            requested=None,
                            limit_applies=False,
                            retry_headroom=needed * (MAX_ATTEMPTS - 1),
                        )
                        if decision.verdict:
                            raise RefreshRefused(decision.verdict)
                        if needed * MAX_ATTEMPTS > quota["REFRESH_SPENDABLE"]:
                            raise RefreshRefused(
                                "the refresh exceeds REFRESH_SPENDABLE with retries"
                            )
                    session_id = connection.session_id()
                    stop = threading.Event()
                    watcher = threading.Thread(
                        target=_watch_run_id,
                        args=(state.operator, settings, session_id, actions, action_id, stop),
                        daemon=True,
                    )
                    watcher.start()
                    outcome: guard.Outcome | None = None
                    try:
                        outcome = guard.run(
                            connection,
                            refresh_statement(settings.namespace, season, week, season_type),
                            first_line,
                        )
                    finally:
                        stop.set()
                        watcher.join(timeout=2)
                        action = actions.get(action_id)
                        run_id = action["run_id"] if action else None
                        if outcome is not None and outcome.rows and "RUN_ID" in outcome.columns:
                            run_id = int(outcome.rows[0][outcome.columns.index("RUN_ID")])
                            actions.update(action_id, run_id=run_id)
                        if run_id is None:
                            try:
                                run_id = _run_id(state.operator, settings, session_id)
                                if run_id is not None:
                                    actions.update(action_id, run_id=run_id)
                            except Exception:
                                pass
                        if mode == "LIVE":
                            if run_id is None:
                                if outcome is None or not (outcome.error or "").startswith(
                                    "REFRESH_WEEK refused:"
                                ):
                                    state.reconciled = False
                                    raise guard.ReconcilePending(
                                        "refresh may have spent calls without a known run id"
                                    )
                            else:
                                try:
                                    row = _run_row(
                                        connection, settings.namespace.meta_schema, run_id
                                    )
                                except Exception:
                                    row = None
                                try:
                                    quota_after = _quota(connection, settings.namespace.meta_schema)
                                except Exception:
                                    quota_after = None
                                settled = bool(
                                    row is not None
                                    and row.get("RUN_STATUS") in ("DONE", "FAILED", "ABANDONED")
                                    and row.get("CALLS") is not None
                                )
                                try:
                                    record = guard.record(
                                        book,
                                        settings.ledger_plan,
                                        settings.target,
                                        kind="REFRESH_WEEK",
                                        run_id=run_id,
                                        script_calls=int(row["CALLS"])
                                        if settled and row is not None
                                        else None,
                                        remaining_before=quota["REMAINING_CALLS"] if quota else 0,
                                        remaining_after=None
                                        if quota_after is None or not settled
                                        else quota_after["REMAINING_CALLS"],
                                        worst_case=estimate.calls * MAX_ATTEMPTS,
                                        error=(None if outcome is None else outcome.error)
                                        or (
                                            None
                                            if settled
                                            else "run status unverified; charged worst case"
                                        ),
                                        clock=lambda: datetime.now(UTC),
                                    )
                                except BaseException:
                                    state.reconciled = False
                                    raise
                                charged = record.calls
                                actions.update(action_id, calls_spent=charged)
                    if outcome is None:
                        raise RuntimeError("refresh script returned no outcome")
                    if outcome.error:
                        raise RuntimeError(outcome.error)
                    if "STEP_STATUS" in outcome.columns and any(
                        str(row[outcome.columns.index("STEP_STATUS")]) == "FAILED"
                        for row in outcome.rows
                    ):
                        raise RuntimeError("a refresh step failed; see the timeline")
            with contextlib.suppress(Exception):
                after = point_ref(state.database, settings)
            if planned_mode == "LIVE":
                try:
                    reconcile.startup(settings, state.operator, book)
                    state.reconciled = True
                except guard.ReconcilePending:
                    state.reconciled = False
    except (GateTimeout, ledger.LedgerError, guard.ReconcilePending, RefreshRefused) as failure:
        error = str(failure)
    except BaseException as failure:
        error = first_line(failure)
        if planned_mode == "LIVE" and charged is None:
            state.reconciled = False
    finally:
        if after is None:
            with contextlib.suppress(Exception):
                after = point_ref(state.database, settings)
        before = actions.get(action_id)
        before_point = before["point_before"] if before else None
        if before_point is None or after is None:
            message = None
        elif before_point["label"] == after["label"]:
            message = f"the point stayed at {after['label']}"
        else:
            message = f"the point moved to {after['label']}"
        actions.finish(
            action_id,
            error=error,
            calls_spent=charged,
            point_after=after,
            point_message=message,
        )
