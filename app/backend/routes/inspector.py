"""The browser's query log and a zero-call explanation for stored page reads."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.backend import models, sqlcatalog
from app.backend.explain import NO_VIRTUAL_TABLE_TEXT
from app.backend.sessions import QueryLogEntry, SessionStore

STORED_PAGES = frozenset(sqlcatalog.CATALOG) - {"health", "quota"} | {
    "whatif_odds",
    "whatif_guide",
}


def _browser_log(request: Request, store: SessionStore) -> list[QueryLogEntry]:
    return store.session(request.state.session_id).log.entries()


def make_router(store: SessionStore) -> APIRouter:
    """Expose only this browser's recorded statements; no database query is needed."""
    router = APIRouter()

    @router.get("/api/inspector", response_model=models.InspectorResponse)
    def inspector(request: Request) -> dict[str, object]:
        entries: list[dict[str, object]] = [
            {
                "entry_id": str(entry.id),
                "principal": entry.principal,
                "user": entry.user,
                "page": entry.page,
                "sql": entry.sql,
                "started_at": datetime.fromtimestamp(entry.started_at, UTC).isoformat(),
                "ms": entry.ms,
                "row_count": entry.rows,
                "error": entry.error,
                "requests": [],
                "calls_spent": entry.calls,
                "cached": entry.cached,
            }
            for entry in _browser_log(request, store)
        ]
        return {"entries": entries}

    @router.post("/api/inspector/{entry_id}/explain", response_model=models.ExplainResponse)
    def explain(request: Request, entry_id: int) -> dict[str, object]:
        entry = store.session(request.state.session_id).log.get(entry_id)
        if entry is None:
            raise HTTPException(404, "query-log entry not found in this browser session")
        if entry.page not in STORED_PAGES or entry.calls != 0:
            raise HTTPException(400, "this query needs the full inspector plan")
        return {
            "entry_id": str(entry.id),
            "principal": entry.principal,
            "no_virtual_table": True,
            "message": NO_VIRTUAL_TABLE_TEXT,
            "manifests": [],
            "spend": {"source": "NONE", "live_calls": 0, "fetch_mode": None},
            "refused": None,
        }

    return router
