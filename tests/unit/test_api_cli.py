"""The API CLI honors the target exported by make."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from analyses import deploy as api_deploy
from scripts import api
from vs.host import target
from vs.host.namespace import Namespace


class FakeConnection:
    closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("argv", "expected"),
    [([], "aws"), (["--target", "local"], "local")],
)
def test_cli_uses_exported_target_unless_explicitly_overridden(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: str
) -> None:
    monkeypatch.setenv("TARGET", "aws")
    chosen: list[str | None] = []
    connected: list[str] = []
    db = FakeConnection()

    def resolve(name: str | None, **_: Any) -> target.Target:
        chosen.append(name)
        return target.Target(
            name or os.environ["TARGET"],
            "db",
            8563,
            "sys",
            Path("/nowhere"),
            target.TLS_PINNED,
        )

    def connector(deployment: target.Target) -> FakeConnection:
        connected.append(deployment.name)
        return db

    def deploy(connection: Any, namespace: Namespace) -> api_deploy.ApiReport:
        assert connection is db
        assert namespace.api_schema == "CFB_API"
        return api_deploy.ApiReport(None, None, 1, ("deployed",))  # type: ignore[arg-type]

    monkeypatch.setattr(api_deploy, "deploy_api", deploy)
    assert api.main(argv, connector=connector, resolve=resolve) == 0
    assert chosen == ([None] if not argv else ["local"])
    assert connected == [expected]
    assert db.closed
