"""Zero-call refresh plan over the shared loader guard and the stored week calendar."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.backend import models, reconcile, refresh_worker
from app.backend.actions import ActionStore, RefreshBusy
from app.backend.routes.read import ReadService, Row, _iso
from app.backend.sessions import SessionStore
from app.backend.settings import Settings
from loader import guard
from vs.cfbd_vs.http import MAX_ATTEMPTS
from vs.host import ledger


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _target(body: models.RefreshTarget) -> tuple[int, int, str]:
    if type(body.season) is not int or not 1869 <= body.season <= 2100:
        raise HTTPException(400, "season must be an integer from 1869 to 2100")
    if type(body.week) is not int or not 1 <= body.week <= 99:
        raise HTTPException(400, "week must be an integer from 1 to 99")
    return body.season, body.week, body.season_type


def _ledger(book: ledger.Ledger, plan: str, now: datetime) -> Row:
    if not book.enabled:
        return dict.fromkeys(
            ("on", "plan", "plan_cap", "plan_room", "month", "month_cap", "month_room"),
            None,
        ) | {"on": False}
    month = ledger.month_of(now)
    entries = book.entries()
    plan_cap = book.config.plans.get(plan)
    month_cap = book.config.months.get(month)
    return {
        "on": True,
        "plan": plan,
        "plan_cap": plan_cap,
        "plan_room": None
        if plan_cap is None
        else max(0, plan_cap - ledger.plan_total(entries, plan)),
        "month": month,
        "month_cap": month_cap,
        "month_room": None
        if month_cap is None
        else max(0, month_cap - ledger.month_total(entries, month)),
    }


def _quota(connection: Any, meta_schema: str, now: datetime) -> Row | None:
    statement = connection.execute(f'SELECT * FROM "{meta_schema}"."QUOTA"')
    rows = statement.fetchall()
    if not rows:
        return None
    values = dict(zip(statement.column_names(), rows[0], strict=True))
    return {
        "mode": "live",
        "read_at": now.isoformat(),
        "age_seconds": 0,
        "remaining_calls": _int(values.get("REMAINING_CALLS")),
        "used_calls": _int(values.get("USED_CALLS")),
        "project_budget": _int(values.get("PROJECT_BUDGET")),
        "refresh_spendable": _int(values.get("REFRESH_SPENDABLE")),
        "backfill_spendable": _int(values.get("BACKFILL_SPENDABLE")),
    }


def _week(
    service: ReadService,
    season: int,
    week: int,
    season_type: str,
    calendar_done: bool,
    now: datetime,
) -> Row:
    games = service.fetch("week_games", season=season, week=week, season_type=season_type)
    current, _, _ = service.point(None, None)
    completed = sum(bool(game["IS_COMPLETED"]) for game in games)
    not_kicked_off = sum(
        game["START_AT"] is not None
        and (_iso(game["START_AT"]) or "") > now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for game in games
    )
    sequence = week + (100 if season_type == "postseason" else 0)
    later = (season, sequence) > (int(current["AS_OF_SEASON"]), int(current["AS_OF_WEEK_SEQ"]))
    moves = bool(later and (calendar_done or (games and completed == len(games))))
    if moves:
        message = f"week {week} is complete: the point will move to week {week}"
    elif not_kicked_off:
        message = (
            f"week {week} is not complete: {not_kicked_off} games have not kicked off; "
            f"the point stays at week {current['AS_OF_WEEK']}"
        )
    else:
        message = (
            f"week {week} is not yet complete; the point may stay at week {current['AS_OF_WEEK']}"
        )
    return {
        "games": len(games),
        "completed": completed,
        "not_kicked_off": not_kicked_off,
        "point_will_move": moves,
        "message": message,
    }


def plan(
    request: Request,
    body: models.RefreshTarget,
    settings: Settings,
    store: SessionStore,
    book: ledger.Ledger,
) -> Row:
    season, week, season_type = _target(body)
    state = request.app.state
    operator = state.operator
    if not operator.enabled:
        raise HTTPException(403, operator.reason)
    now = datetime.now(UTC)
    mode = operator.fetch_mode(settings.namespace)
    with operator.connection() as connection:

        def query(sql: str) -> list[tuple[Any, ...]]:
            return [tuple(row) for row in connection.execute(sql).fetchall()]

        estimate = guard.plan_refresh(query, settings.namespace, season)
        rows = query(
            f'SELECT "IS_DONE" FROM "{settings.namespace.model_schema}"."SEASON_WEEK" '
            f'WHERE "SEASON" = {season} AND "WEEK" = {week} '
            f"AND \"SEASON_TYPE\" = '{season_type}'"
        )
        calendar_done = bool(rows and rows[0][0])
        running = query(
            'SELECT "RUN_ID", "RUN_KIND", "STARTED_AT" FROM '
            f'"{settings.namespace.meta_schema}"."LOAD_RUN" '
            "WHERE \"RUN_STATUS\" = 'RUNNING'"
        )
        quota = _quota(connection, settings.namespace.meta_schema, now) if mode == "LIVE" else None
    week_state = _week(
        ReadService(request, settings, store, cached=False),
        season,
        week,
        season_type,
        calendar_done,
        now,
    )
    ledger_state = _ledger(book, settings.ledger_plan, now)
    live_calls = estimate.calls if mode == "LIVE" else 0
    reasons = [f"{name}: {reason}" for name, reason in estimate.blocked]
    if not rows:
        reasons.append("the target is not in the stored season calendar")
    if running:
        reasons.append(f"a refresh is already running (run {int(running[0][0])})")
    if mode == "LIVE" and not state.reconciled:
        reasons.append("ledger reconciliation is pending")
    if mode == "LIVE":
        if not book.enabled:
            reasons.append("ledger off: live refresh is disabled")
        if settings.dev:
            reasons.append("live refresh needs `make app-serve`")
        if quota is None or quota["used_calls"] is None:
            reasons.append("the key's quota could not be read")
        else:
            decision = guard.decide(
                estimate,
                mode=mode,
                quota=SimpleNamespace(used_calls=quota["used_calls"]),
                book=book,
                ledger_plan=settings.ledger_plan,
                requested=None,
                limit_applies=False,
                retry_headroom=live_calls * (MAX_ATTEMPTS - 1),
            )
            if decision.verdict:
                reasons.append(decision.verdict)
            spendable = quota["refresh_spendable"]
            if spendable is None or live_calls * MAX_ATTEMPTS > spendable:
                reasons.append("the refresh needs more calls than REFRESH_SPENDABLE permits")
    return {
        "target": {"season": season, "week": week, "season_type": season_type},
        "mode": mode,
        "can_run": not reasons,
        "reasons": reasons,
        "estimate": {
            "total_calls": live_calls,
            "per_table": dict(estimate.per_table) if mode == "LIVE" else {},
        },
        "needs_confirmation": bool(live_calls),
        "plus_retries": bool(live_calls),
        "quota": quota,
        "ledger": ledger_state,
        "running": [
            {"run_id": int(row[0]), "run_kind": str(row[1]), "started_at": _iso(row[2])}
            for row in running
        ],
        "week": week_state,
        "dev_blocks_live": bool(settings.dev and mode == "LIVE"),
    }


def make_router(settings: Settings, store: SessionStore, book: ledger.Ledger) -> APIRouter:
    router = APIRouter()
    actions = ActionStore()
    start_lock = threading.Lock()

    @router.post("/api/refresh/plan", response_model=models.RefreshPlanResponse)
    def refresh_plan(request: Request, body: models.RefreshTarget) -> Row:
        return plan(request, body, settings, store, book)

    @router.post("/api/refresh/run", response_model=models.RefreshRunResponse, status_code=202)
    def refresh_run(request: Request, body: models.RefreshRunRequest) -> Row | JSONResponse:
        target = models.RefreshTarget(
            season=body.season, week=body.week, season_type=body.season_type
        )
        season, week, season_type = _target(target)
        if type(body.max_calls) is not int or body.max_calls < 0:
            raise HTTPException(400, "max_calls must be a non-negative integer")
        with start_lock:
            if active := actions.active():
                return _refuse("refresh_running", f"refresh action {active} is already running")
            state = request.app.state
            if not state.operator.enabled:
                return _refuse("operator_disabled", state.operator.reason, status=403)
            mode = state.operator.fetch_mode(settings.namespace)
            if mode == "LIVE":
                try:
                    reconcile.startup(settings, state.operator, book)
                    state.reconciled = True
                except guard.ReconcilePending:
                    state.reconciled = False
                    return _refuse("reconcile_pending", "ledger reconciliation has not finished")
            planned = plan(request, target, settings, store, book)
            needed = int(planned["estimate"]["total_calls"])
            if needed > body.max_calls:
                return _refuse(
                    "max_calls_exceeded",
                    f"the refresh needs {needed} calls, above the {body.max_calls} confirmed",
                )
            if not planned["can_run"]:
                reason = "; ".join(planned["reasons"])
                code = "refresh_running" if planned["running"] else "live_disabled"
                if "ledger" in reason or reason.startswith(("plan ", "month ")):
                    code = "ledger_refused"
                elif "REFRESH_SPENDABLE" in reason or "quota" in reason:
                    code = "quota_refused"
                return _refuse(code, reason)
            before = refresh_worker.point_ref(state.database, settings)
            try:
                action_id = actions.begin(before)
            except RefreshBusy as error:
                return _refuse("refresh_running", str(error))
            thread = threading.Thread(
                target=refresh_worker.run_refresh,
                args=(state, settings, book, actions, action_id),
                kwargs={
                    "season": season,
                    "week": week,
                    "season_type": season_type,
                    "max_calls": body.max_calls,
                    "planned_mode": mode,
                },
                name=f"cfb-refresh-{action_id}",
                daemon=True,
            )
            try:
                thread.start()
            except RuntimeError as error:
                actions.finish(action_id, error=str(error))
                return _refuse("live_busy", "the refresh worker could not start")
            return {"action_id": action_id}

    @router.get("/api/actions/{action_id}", response_model=models.ActionResponse)
    def action(action_id: str) -> Row:
        found = actions.get(action_id)
        if found is None:
            raise HTTPException(404, "action not found")
        return found

    return router


def _refuse(code: str, message: str, *, status: int = 409) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)
