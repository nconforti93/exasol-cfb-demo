"""The served shell and safety checks without a database or network."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.backend.db import DatabaseUnreachable
from app.backend.main import create_app
from app.backend.settings import Settings


class FakePool:
    def __init__(self, fails: bool = False) -> None:
        self.fails = fails

    def run(self, sql: str, **kwargs: object) -> SimpleNamespace:
        if self.fails:
            raise DatabaseUnreachable("server unavailable")
        return SimpleNamespace(rows=[(1,)])


class FakeDatabase:
    def __init__(self, fails: bool = False) -> None:
        self.pool = FakePool(fails)
        self.console = SimpleNamespace(availability=SimpleNamespace(enabled=False, reason="off"))

    def close(self) -> None:
        pass


def settings(tmp_path: Path) -> Settings:
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text('<html><script src="/assets/app.js"></script></html>')
    assets = static / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("document.body.dataset.ready = 'yes'")
    return Settings.from_env(
        {"CFB_APP_PASSWORD": "synthetic", "APP_STATIC_DIR": str(static), "APP_OPERATOR": "off"},
        dotenv_path=tmp_path / ".env",
        secrets_file=tmp_path / "app.env",
    )


def test_shell_fallback_cache_headers_and_host_check(tmp_path: Path) -> None:
    app = create_app(
        settings(tmp_path), read_factory=lambda _: FakeDatabase(), start_check=lambda *_: None
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        shell = client.get("/team/123")
        assert shell.status_code == 200
        assert shell.headers["cache-control"] == "no-cache"
        assert shell.headers["content-security-policy"].startswith("default-src 'self'")
        assert client.get("/assets/app.js").headers["cache-control"].startswith("public")
        foreign = client.get("/", headers={"host": "evil.test:8000"})
        assert foreign.status_code == 400
        assert foreign.json()["error"] == "bad_host"
        denied = client.post("/api/action", json={})
        assert denied.status_code == 403


def test_health_reports_database_outage(tmp_path: Path) -> None:
    app = create_app(
        settings(tmp_path), read_factory=lambda _: FakeDatabase(True), start_check=lambda *_: None
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/health").status_code == 503
        assert client.get("/api/health").json()["error"] == "database_unreachable"
