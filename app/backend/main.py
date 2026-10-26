"""FastAPI entry point for the read-only app shell and future guarded actions."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, MutableMapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pyexasol import ExaCommunicationError, ExaQueryError
from starlette.responses import Response

from app.backend import db, security
from app.backend.operator import OperatorPath
from app.backend.routes.logos import make_router as logo_router
from app.backend.settings import Settings
from vs.host import ledger as ledger_module

VERSION = "0.1.0"
SHELL_CACHE = "no-cache"
ASSET_CACHE = "public, max-age=31536000, immutable"
ERROR_CODES = {
    400: "invalid_argument",
    403: "forbidden",
    409: "live_busy",
    502: "cfbd_failed",
    503: "database_unreachable",
}


class CachedAssets(StaticFiles):
    """Serve hashed frontend assets with immutable caching."""

    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        """Add the long cache lifetime after StaticFiles has chosen the asset or a 404."""
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = ASSET_CACHE
        return response


def _ledger_state(settings: Settings, calls: ledger_module.Ledger) -> dict[str, Any]:
    if not settings.ledger_enabled:
        return {
            "on": False,
            "plan": None,
            "plan_cap": None,
            "plan_room": None,
            "month": None,
            "month_cap": None,
            "month_room": None,
        }
    month = datetime.now(UTC).strftime("%Y-%m")
    config = calls.config
    entries = calls.entries()
    plan_cap = config.plans.get(settings.ledger_plan)
    month_cap = config.months.get(month)
    return {
        "on": True,
        "plan": settings.ledger_plan,
        "plan_cap": plan_cap,
        "plan_room": None
        if plan_cap is None
        else max(0, plan_cap - ledger_module.plan_total(entries, settings.ledger_plan)),
        "month": month,
        "month_cap": month_cap,
        "month_room": None
        if month_cap is None
        else max(0, month_cap - ledger_module.month_total(entries, month)),
    }


def create_app(
    settings: Settings,
    read_factory: Callable[[Settings], Any] = db.build_database,
    console_factory: Callable[[Settings], Any] | None = None,
    owner_factory: Callable[[Settings, db.Connector], Any] = OperatorPath,
    ledger: ledger_module.Ledger | None = None,
    *,
    start_check: Callable[..., Any] | None = None,
    reconcile: Callable[..., Any] | None = None,
) -> FastAPI:
    """Build the one-worker app; I/O is deferred to startup and request handlers for testability."""
    calls = ledger or ledger_module.Ledger(settings.ledger_path, enabled=settings.ledger_enabled)
    connector = db.default_connector(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = read_factory(settings)
        operator = owner_factory(settings, connector)
        app.state.database = database
        app.state.operator = operator
        app.state.console = console_factory(settings) if console_factory else database.console
        app.state.start_check = (start_check or db.run_start_check)(
            settings,
            connector,
            operator.impersonation_reader() if hasattr(operator, "impersonation_reader") else None,
        )
        app.state.ledger = _ledger_state(settings, calls)
        app.state.reconciled = False
        if reconcile is not None:
            reconcile(settings, operator, calls)
            app.state.reconciled = True
        try:
            yield
        finally:
            database.close()

    browser = security.SecurityConfig(
        settings.allowed_hosts, settings.origins, settings.cors_origins
    )
    app = FastAPI(lifespan=lifespan, middleware=security.middleware(browser))
    app.state.settings = settings
    app.include_router(logo_router(settings.logo_dir, enabled=settings.logos_enabled))

    @app.get("/api/health")
    def health(request: Request) -> dict[str, bool]:
        request.app.state.database.pool.run("SELECT 1")
        return {"ok": True}

    @app.get("/api/config")
    def config(request: Request) -> dict[str, Any]:
        state = request.app.state
        reasons = []
        if settings.dev:
            reasons.append("development mode")
        if not state.operator.enabled:
            reasons.append(state.operator.reason)
        if not state.reconciled:
            reasons.append("startup reconciliation is unavailable")
        if state.ledger["plan_cap"] is None or state.ledger["month_cap"] is None:
            reasons.append("the ledger has no cap for this plan or month")
        console = state.console.availability
        return {
            "version": VERSION,
            "target": settings.target,
            "dev": settings.dev,
            "fetch_mode": None,
            "operator": {
                "enabled": state.operator.enabled,
                "reason": None if state.operator.enabled else state.operator.reason,
            },
            "console": {
                "enabled": console.enabled,
                "reason": None if console.enabled else console.reason,
            },
            "ledger": state.ledger,
            "live": {"enabled": not reasons, "reasons": reasons},
            "read_check_skipped": bool(getattr(state.start_check, "skipped", False)),
            "chat": {
                "available": False,
                "enabled": False,
                "reason": "chat arrives with Phase 7",
                "model": None,
            },
        }

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        code = ERROR_CODES.get(error.status_code, "invalid_argument")
        return JSONResponse(
            {"error": code, "message": str(error.detail)}, status_code=error.status_code
        )

    @app.exception_handler(db.PoolBusy)
    async def busy(_: Request, __: db.PoolBusy) -> JSONResponse:
        return JSONResponse(
            {"error": "busy", "message": "database sessions are busy"}, status_code=503
        )

    @app.exception_handler(db.DatabaseUnreachable)
    async def unreachable(_: Request, __: db.DatabaseUnreachable) -> JSONResponse:
        return JSONResponse(
            {"error": "database_unreachable", "message": "database is unavailable"}, status_code=503
        )

    @app.exception_handler(ExaCommunicationError)
    @app.exception_handler(ExaQueryError)
    async def driver_error(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(
            {"error": "database_unreachable", "message": "database query failed"}, status_code=503
        )

    @app.get("/api/{path:path}")
    def unknown_api(path: str) -> JSONResponse:
        return JSONResponse(
            {"error": "not_found", "message": "API route not found"}, status_code=404
        )

    static_dir = settings.static_dir
    app.mount(
        "/assets", CachedAssets(directory=static_dir / "assets", check_dir=False), name="assets"
    )

    @app.get("/{path:path}")
    def shell(path: str) -> FileResponse:
        if path and "." in Path(path).name:
            raise HTTPException(404, "file not found")
        index = static_dir / "index.html"
        if not index.is_file():
            raise HTTPException(503, "built frontend is unavailable; run make app-build")
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": SHELL_CACHE})

    return app


def app_factory() -> FastAPI:
    """Uvicorn reload target; settings are read in each child process."""
    return create_app(Settings.from_env(os.environ))
