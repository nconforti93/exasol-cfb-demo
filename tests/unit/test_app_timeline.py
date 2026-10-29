"""The refresh timeline reads stored serving views and preserves follow semantics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.backend.routes.timeline import make_router, read_timeline
from app.backend.sessions import QueryLog, SessionStore
from app.backend.settings import Settings
from vs.host import ledger

POINT = {
    "AS_OF_SEASON": "2026",
    "AS_OF_WEEK_SEQ": "8",
    "PUBLICATION_ID": "7",
    "WHATIF_RUN_ID": "6",
    "WHATIF_VIEWS_CURRENT": True,
}
STEP = {
    "RUN_ID": "5",
    "RUN_KIND": "REFRESH_WEEK",
    "RUN_ARGS": "2026,8,regular",
    "RUN_STATUS": "DONE",
    "RUN_STARTED_AT": "2026-10-26 11:00:00",
    "STEP_NO": "100",
    "STEP_NAME": "MODEL",
    "STEP_STATUS": "DONE",
    "STARTED_AT": "2026-10-26 11:01:00",
    "ENDED_AT": "2026-10-26 11:01:02",
    "SECONDS": "2.000",
    "SLICES": "4",
    "ROWS_LOADED": "56",
    "CALLS": "0",
    "DETAIL": "model rebuilt",
}
FETCH = {
    "CONTEXT": "REFRESH_WEEK#5",
    "TABLE_NAME": "GAMES",
    "SLICE_KEY": "/games?year=2026&week=8",
    "REQ_SOURCE": "REPLAY",
    "HTTP_STATUS": "200",
    "ATTEMPTS": "1",
    "MS": "42",
    "ROWS_RETURNED": "56",
    "REQUESTED_AT": "2026-10-26 11:00:02",
}


class FakePool:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.pages: list[str] = []
        self.params: list[Mapping[str, Any]] = []

    def run(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        log: QueryLog,
        page: str,
        context: str,
    ) -> Any:
        self.pages.append(page)
        self.params.append(params)
        rows = self.rows.get(page, [])
        log.record(
            sql=sql, principal="app", user="test", ms=0, rows=len(rows), page=page, context=context
        )
        return SimpleNamespace(dicts=lambda: rows)


def app_with_pool(
    rows: dict[str, list[dict[str, Any]]], tmp_path: Path
) -> tuple[FastAPI, FakePool]:
    pool = FakePool(rows)
    app = FastAPI()
    app.state.database = SimpleNamespace(pool=pool)
    app.state.operator = SimpleNamespace(enabled=False)
    app.state.reconciled = True
    settings = cast(
        Settings,
        SimpleNamespace(api_schema="TEST_CFB_API", api_live_schema="TEST_CFB_API_LIVE"),
    )
    app.include_router(
        make_router(settings, SessionStore(), ledger.Ledger(tmp_path / "ledger", enabled=False))
    )

    @app.middleware("http")
    async def session(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.session_id = "synthetic-browser"
        return await call_next(request)

    return app, pool


def test_latest_timeline_shapes_run_fetches_and_follow(tmp_path: Path) -> None:
    app, pool = app_with_pool(
        {
            "timeline_latest_run": [STEP],
            "current_point": [POINT],
            "fetches_for_run": [FETCH],
        },
        tmp_path,
    )
    with TestClient(app) as client:
        response = client.get("/api/timeline")
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["steps"][0]["seconds"] == 2
    assert body["run"]["steps"][0]["started_at"] == "2026-10-26T11:01:00Z"
    assert body["fetches"][0]["source"] == "REPLAY"
    assert body["fetches"][0]["status"] == 200
    assert body["follow"] == {
        "key": {
            "as_of_season": 2026,
            "as_of_week_seq": 8,
            "publication_id": 7,
            "whatif_run_id": 6,
            "whatif_views_current": True,
        },
        "hold": False,
        "stuck": False,
    }
    assert pool.pages == ["timeline_latest_run", "current_point", "fetches_for_run"]
    assert pool.params[-1]["context"] == "REFRESH_WEEK#5"


def test_selected_history_does_not_clear_hold_for_newer_stuck_run(tmp_path: Path) -> None:
    latest = {
        **STEP,
        "RUN_ID": "6",
        "RUN_STATUS": "RUNNING",
        "STARTED_AT": "2026-10-26 11:10:00",
        "ENDED_AT": None,
        "STEP_STATUS": "RUNNING",
    }
    selected = {**STEP, "RUN_ID": "5"}
    pool = FakePool(
        {
            "timeline_latest_run": [latest],
            "timeline_run": [selected],
            "current_point": [POINT],
            "fetches_for_run": [FETCH],
        }
    )
    service = SimpleNamespace(fetch=lambda name, **_: pool.rows.get(name, []))
    body = read_timeline(
        cast(Any, service),
        5,
        now=datetime(2026, 10, 26, 11, 21, tzinfo=UTC),
    )
    assert body["run"]["run_id"] == 5
    assert body["follow"]["hold"] is True and body["follow"]["stuck"] is True
    assert body["fetches"][0]["context"] == "REFRESH_WEEK#5"


def test_no_refresh_still_returns_current_follow_key(tmp_path: Path) -> None:
    app, pool = app_with_pool({"timeline_latest_run": [], "current_point": [POINT]}, tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/timeline")
    assert response.status_code == 200
    assert response.json()["run"] is None and response.json()["fetches"] == []
    assert response.json()["follow"]["key"]["as_of_week_seq"] == 8
    assert "fetches_for_run" not in pool.pages
