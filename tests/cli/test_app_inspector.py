"""A SQL button resolves to this browser's stored-query log without a database call."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.backend.main import create_app
from app.backend.settings import Settings


def test_inspector_shows_the_page_statement_and_zero_call_plan(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"CFB_APP_PASSWORD": "synthetic", "APP_OPERATOR": "off", "LEDGER": "off"},
        dotenv_path=tmp_path / ".env",
        secrets_file=tmp_path / "app.env",
    )
    database = SimpleNamespace(
        console=SimpleNamespace(availability=SimpleNamespace(enabled=False, reason="off")),
        close=lambda: None,
    )
    app = create_app(
        settings,
        read_factory=lambda _: database,
        start_check=lambda *_: SimpleNamespace(skipped=False),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/inspector").json() == {"entries": []}
        session_id = client.cookies.get("cfb_session")
        assert session_id is not None
        logged = app.state.sessions.session(session_id).log.record(
            sql='SELECT * FROM "CFB_API"."TEAMS"',
            principal="app",
            user="CFB_APP_WEB",
            ms=3,
            rows=138,
            page="teams",
        )
        shown = client.get("/api/inspector")
        assert shown.status_code == 200
        assert shown.json()["entries"][0]["entry_id"] == str(logged.id)
        assert shown.json()["entries"][0]["sql"] == logged.sql
        plan = client.post(
            f"/api/inspector/{logged.id}/explain",
            json={},
            headers={"origin": "http://127.0.0.1:8000", "x-cfb-action": "1"},
        )
        assert plan.status_code == 200
        assert plan.json()["no_virtual_table"] is True
        assert plan.json()["spend"]["live_calls"] == 0
        client.cookies.clear()
        assert client.get("/api/inspector").json() == {"entries": []}
