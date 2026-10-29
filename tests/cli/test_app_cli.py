"""CLI argument and secret handling without connecting to Exasol."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI

from scripts import app


def test_serve_requires_built_frontend(tmp_path: Path) -> None:
    with pytest.raises(app.AppCliError, match="app-build"):
        app.serve(static_dir=tmp_path / "missing", runner=lambda *a, **k: None)


def test_dev_rejects_vite_host_override() -> None:
    with pytest.raises(app.AppCliError, match="host"):
        app.check_vite_args(["--", "--host", "0.0.0.0"])
    with pytest.raises(app.AppCliError, match="host"):
        app.check_vite_args(["--", "--server.host=0.0.0.0"])


def test_user_requires_password_without_disclosing_it(tmp_path: Path) -> None:
    with pytest.raises(app.AppCliError, match="CFB_APP_PASSWORD") as error:
        app.user(values={}, connector=lambda *a, **k: None)
    assert "synthetic-secret" not in str(error.value)


def test_serve_uses_the_validated_static_directory(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html></html>")
    captured: list[FastAPI] = []
    app.serve(
        static_dir=tmp_path,
        runner=lambda application, **_: captured.append(application),
        environ={"CFB_APP_PASSWORD": "synthetic", "APP_OPERATOR": "off"},
    )
    assert captured[0].state.settings.static_dir == tmp_path


def test_dev_stops_when_either_process_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        def __init__(self, code: int | None) -> None:
            self.code = code
            self.terminated = False

        def poll(self) -> int | None:
            return self.code

        def terminate(self) -> None:
            self.terminated = True

        def wait(self) -> int:
            return self.code or 0

    backend, frontend = Process(1), Process(None)
    processes = iter((backend, frontend))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: next(processes))
    monkeypatch.setattr(os, "environ", {"CFB_APP_PASSWORD": "synthetic", "APP_OPERATOR": "off"})
    with pytest.raises(app.AppCliError, match="exited with an error"):
        app.dev([])
    assert backend.terminated and frontend.terminated


def test_user_lookup_binds_name_as_a_value_and_password_as_identifier() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    class Connection:
        def execute(self, sql: str, params: dict[str, str] | None = None) -> Connection:
            calls.append((sql, params))
            return self

        def fetchone(self) -> None:
            return None

        def close(self) -> None:
            pass

    names = app.user(values={"CFB_APP_PASSWORD": "synthetic"}, connector=Connection)
    assert names == ("CFB_APP_WEB",)
    assert '"USER_NAME" = {name}' in calls[0][0]
    assert calls[0][1] == {"name": "CFB_APP_WEB"}
    assert "IDENTIFIED BY {password!q}" in calls[1][0]
    assert calls[1][1] == {"password": "synthetic"}
