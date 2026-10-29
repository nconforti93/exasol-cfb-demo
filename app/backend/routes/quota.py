"""Free /info quota read through CFB_API_LIVE, cached for five minutes."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.backend import models
from app.backend.readcache import DISPLAY_TTL_SECONDS
from app.backend.routes.read import ReadService, Row, _number
from app.backend.sessions import SessionStore
from app.backend.settings import Settings

CACHE_SECONDS = 300


def make_router(settings: Settings, store: SessionStore) -> APIRouter:
    router = APIRouter()
    lock = threading.Lock()
    cached: tuple[float, Row] | None = None

    @router.get("/api/quota", response_model=models.QuotaResponse)
    def quota(request: Request, fresh: bool = False) -> Row:
        nonlocal cached
        operator = request.app.state.operator
        if not operator.enabled:
            raise HTTPException(503, "the virtual schema mode is unavailable")
        memo = getattr(request.app.state, "memo", None)
        try:
            if memo is None or fresh:
                mode = operator.fetch_mode(settings.namespace)
            else:
                mode = memo.get(
                    "fetch_mode",
                    DISPLAY_TTL_SECONDS,
                    lambda: operator.fetch_mode(settings.namespace),
                )
        except Exception:
            raise HTTPException(503, "the virtual schema mode is unavailable") from None
        if mode == "REPLAY":
            return {
                "mode": "replay",
                "read_at": None,
                "age_seconds": None,
                "remaining_calls": None,
                "used_calls": None,
                "project_budget": None,
                "refresh_spendable": None,
                "backfill_spendable": None,
            }
        with lock:
            now = time.monotonic()
            if cached is not None and not fresh and now - cached[0] < CACHE_SECONDS:
                return {**cached[1], "age_seconds": int(now - cached[0])}
            rows = ReadService(request, settings, store, cached=False).fetch("quota")
            if not rows:
                raise HTTPException(503, "the quota is unavailable")
            row = rows[0]
            response = {
                "mode": "live",
                "read_at": datetime.now(UTC).isoformat(),
                "age_seconds": 0,
                "remaining_calls": _number(row["REMAINING_CALLS"]),
                "used_calls": _number(row["USED_CALLS"]),
                "project_budget": _number(row["PROJECT_BUDGET"]),
                "refresh_spendable": _number(row["REFRESH_SPENDABLE"]),
                "backfill_spendable": _number(row["BACKFILL_SPENDABLE"]),
            }
            cached = now, response
            return response

    return router
