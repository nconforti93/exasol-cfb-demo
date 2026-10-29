"""Console routes: the listing, a 0-call explanation as the reader, refusals before a run."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pyexasol import ExaQueryError

from app.backend import db
from app.backend.routes.console import _refusal_text, _request_lines, make_router
from app.backend.sessions import SessionStore
from app.backend.settings import Settings
from vs.cfbd_vs.plan import RequestPlan, build_manifest, manifest_comment
from vs.host import fetchlog, ledger

BUDGET = (
    "VM error: F-UDF-CL-LIB-1126: F-UDF-CL-SL-PYTHON-1004: BudgetError: "
    "15 requests are planned but MAX_CALLS_PER_QUERY is 10"
)


def _settings() -> Settings:
    return Settings.from_env(
        {
            "APP_PREFIX": "TEST_",
            "APP_LEDGER_PATH": "/nonexistent/calls.jsonl",
            "CFB_APP_PASSWORD": "synthetic-app-password",
            "CFB_CONSOLE_PASSWORD": "synthetic-console-password",
        },
        dotenv_path=Path("/nonexistent/.env"),
        resolver=lambda host, port: ["127.0.0.1"],
    )


class _Session:
    def __init__(self, sent: list[str]) -> None:
        self.sent = sent

    def execute(self, sql: str, **log: Any) -> db.Result:
        self.sent.append(sql)
        log["log"].record(sql=sql, principal="reader", user="U", ms=1, rows=0, page=log["page"])
        if "2010 AND 2024" in sql:
            raise ExaQueryError(None, sql, "VS-BUDGET", BUDGET)  # type: ignore[no-untyped-call]
        if '= 2023 AND "P_YEAR" = 2024' in sql:
            return db.Result(sql, (fetchlog.PUSHDOWN_SQL_COLUMN,), [], 1, "U", "reader")
        body = build_manifest(
            RequestPlan(table="GAMES_TEAMS", slice_keys=("/games/teams?year=2022",), budget=10),
            mode="live",
            read_through=True,
        )
        body["sources"] = ["LIVE"]
        row = (manifest_comment(body) + "\nSELECT 1",)
        return db.Result(sql, (fetchlog.PUSHDOWN_SQL_COLUMN,), [row], 1, "U", "reader")


class _Console:
    def __init__(self, enabled: bool) -> None:
        self.availability = db.Availability(enabled, "CFB_CONSOLE_PASSWORD is not set")
        self.sent: list[str] = []

    @contextmanager
    def session(self) -> Iterator[_Session]:
        yield _Session(self.sent)


def _client(tmp_path: Path, *, enabled: bool = True, reconciled: bool = True) -> TestClient:
    app = FastAPI()
    app.state.console = _Console(enabled)
    app.state.reconciled = reconciled

    @app.middleware("http")
    async def browser(request: Request, call_next: Any) -> Any:
        request.state.session_id = "browser"
        return await call_next(request)

    book = ledger.Ledger(tmp_path / "calls.jsonl", config=ledger.LedgerConfig({}, {}))
    app.include_router(make_router(_settings(), SessionStore(), book))
    return TestClient(app)


def test_listing_names_the_reader_and_four_fixed_statements(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/console").json()
    assert body["principal"] == "reader" and body["user"] == _settings().console_user
    assert [query["id"] for query in body["queries"]] == [
        "fan-out",
        "contradiction",
        "stored",
        "over-budget",
    ]
    assert all('"TEST_CFBD_VS"' in query["sql"] for query in body["queries"])


def test_disabled_console_and_unknown_query_are_refused(tmp_path: Path) -> None:
    off = _client(tmp_path, enabled=False).get("/api/console")
    assert off.status_code == 403 and off.json()["error"] == "console_disabled"
    unknown = _client(tmp_path).post("/api/console/drop-table/explain")
    assert unknown.status_code == 404


def test_explain_runs_as_reader_and_reports_plan_time_refusals(tmp_path: Path) -> None:
    client = _client(tmp_path)
    fan_out = client.post("/api/console/fan-out/explain").json()
    assert fan_out["principal"] == "reader" and fan_out["refused"] is None
    assert fan_out["spend"] == {"source": "LIVE", "live_calls": 1, "fetch_mode": "LIVE"}
    assert fan_out["entry_id"] != ""
    contradiction = client.post("/api/console/contradiction/explain").json()
    assert contradiction["no_virtual_table"] and contradiction["spend"]["live_calls"] == 0
    refused = client.post("/api/console/over-budget/explain").json()
    assert refused["refused"] is not None and "MAX_CALLS_PER_QUERY" in refused["refused"]


def test_run_validates_its_body_and_waits_for_reconciliation(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for body in ({}, {"max_calls": -1}, {"max_calls": "3"}, {"max_calls": 3, "force": True}):
        answer = client.post("/api/console/fan-out/run", json=body)
        assert answer.status_code == 400, body
    pending = _client(tmp_path, reconciled=False).post(
        "/api/console/fan-out/run", json={"max_calls": 3}
    )
    assert pending.status_code == 409 and pending.json()["error"] == "reconcile_pending"


def test_refusal_drops_udf_codes_and_request_times_accept_text() -> None:
    error = ExaQueryError(None, "SELECT 1", "VS-BUDGET", BUDGET)  # type: ignore[no-untyped-call]
    assert _refusal_text(error).startswith("BudgetError: 15 requests")
    row = fetchlog.FetchLogRow(
        datetime(2026, 10, 27),
        "APP_CONSOLE#a",
        "GAMES",
        "/games?year=1869",
        "STORED",
        None,
        None,
        None,
        4,
        "2026-10-01 10:00:00",  # type: ignore[arg-type]
    )
    assert _request_lines([row])[0]["requested_at"] == "2026-10-01 10:00:00"
