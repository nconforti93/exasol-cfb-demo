"""Read the latest or selected refresh timeline without starting a refresh."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.backend import models
from app.backend.routes.read import ReadService, Row, _iso, _number
from app.backend.sessions import SessionStore
from app.backend.settings import Settings

STUCK_AFTER = timedelta(minutes=10)


def _utc(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _stuck(rows: list[Row], now: datetime) -> bool:
    if not rows or rows[0]["RUN_STATUS"] != "RUNNING":
        return False
    changes = [
        timestamp
        for row in rows
        for value in (row["RUN_STARTED_AT"], row["STARTED_AT"], row["ENDED_AT"])
        if (timestamp := _utc(value)) is not None
    ]
    return bool(changes) and now - max(changes) >= STUCK_AFTER


def _follow(point: Row, *, hold: bool, stuck: bool) -> Row:
    return {
        "key": {
            "as_of_season": int(point["AS_OF_SEASON"]),
            "as_of_week_seq": int(point["AS_OF_WEEK_SEQ"]),
            "publication_id": _number(point.get("PUBLICATION_ID")),
            "whatif_run_id": _number(point.get("WHATIF_RUN_ID")),
            "whatif_views_current": bool(point.get("WHATIF_VIEWS_CURRENT")),
        },
        "hold": hold,
        "stuck": stuck,
    }


def _step(row: Row) -> Row:
    return {
        "step_no": _number(row["STEP_NO"]),
        "step_name": row["STEP_NAME"],
        "step_status": row["STEP_STATUS"],
        "started_at": _iso(row["STARTED_AT"]),
        "ended_at": _iso(row["ENDED_AT"]),
        "seconds": _number(row["SECONDS"]),
        "slices": _number(row["SLICES"]),
        "rows_loaded": _number(row["ROWS_LOADED"]),
        "calls": _number(row["CALLS"]),
        "detail": row["DETAIL"],
    }


def _fetch(row: Row) -> Row:
    return {
        "context": row["CONTEXT"],
        "table_name": row["TABLE_NAME"],
        "slice_key": row["SLICE_KEY"],
        "source": row["REQ_SOURCE"],
        "status": _number(row["HTTP_STATUS"]),
        "attempts": _number(row["ATTEMPTS"]),
        "ms": _number(row["MS"]),
        "rows": _number(row["ROWS_RETURNED"]),
        "requested_at": _iso(row["REQUESTED_AT"]),
    }


def read_timeline(
    service: ReadService,
    run_id: int | None = None,
    *,
    now: datetime | None = None,
) -> Row:
    """Assemble the route body from three stored serving-view reads."""
    if run_id is not None and run_id <= 0:
        raise HTTPException(400, "run must be a positive integer")
    latest = service.fetch("timeline_latest_run")
    rows = latest if run_id is None else service.fetch("timeline_run", run_id=run_id)
    current = service.fetch("current_point")
    if not current:
        raise HTTPException(503, "no current point is available")
    found_run = int(rows[0]["RUN_ID"]) if rows else None
    fetches = (
        service.fetch("fetches_for_run", context=f"REFRESH_WEEK#{found_run}")
        if found_run is not None
        else []
    )
    hold = bool(latest and latest[0]["RUN_STATUS"] == "RUNNING")
    return {
        "run": None
        if not rows
        else {
            "run_id": found_run,
            "run_kind": rows[0]["RUN_KIND"],
            "run_args": rows[0]["RUN_ARGS"],
            "run_status": rows[0]["RUN_STATUS"],
            "started_at": _iso(rows[0]["RUN_STARTED_AT"]),
            "steps": [_step(row) for row in rows],
        },
        "fetches": [_fetch(row) for row in fetches],
        "follow": _follow(
            current[0],
            hold=hold,
            stuck=_stuck(latest, now or datetime.now(UTC)),
        ),
    }


def make_router(settings: Settings, store: SessionStore) -> APIRouter:
    """Make the read-only timeline route on the app's read pool."""
    router = APIRouter()

    @router.get("/api/timeline", response_model=models.TimelineResponse)
    def timeline(request: Request, run: int | None = None) -> Row:
        service = ReadService(request, settings, store)
        return read_timeline(service, run)

    return router
