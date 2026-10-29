"""No-network checks for the guarded direct preview operator path."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.backend import live, operator
from app.backend.settings import Settings
from tests.unit.db_fakes import FakeConnector, FakeStatement, Reply
from vs.cfbd_vs.plan import RequestPlan, build_manifest, manifest_comment
from vs.host import fetchlog, ledger
from vs.host.namespace import Namespace

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
NS = Namespace("TEST_")
GAME = 401
KEY = f"/games/{GAME}/preview"


def _settings(path: Path) -> Settings:
    return Settings.from_env(
        {
            "APP_PREFIX": "TEST_",
            "APP_LEDGER_PATH": str(path),
            "CFB_APP_PASSWORD": "synthetic-app-password",
            "CFB_TEST_OWNER_PASSWORD": "synthetic-owner-password",
        },
        dotenv_path=Path("/nonexistent/.env"),
        resolver=lambda host, port: ["127.0.0.1"],
    )


def _manifest(source: str) -> str:
    body = build_manifest(
        RequestPlan(table="GAMES_PREVIEW", slice_keys=(KEY,), budget=5),
        mode="replay" if source == "REPLAY" else "live",
        read_through=True,
    )
    body["sources"] = [source]
    return manifest_comment(body) + "\nSELECT 1"


def _make(
    path: Path,
    *,
    source: str = "LIVE",
    fail: bool = False,
    quota: int = 100,
    pending: bool = False,
) -> tuple[live.LiveExecutor, FakeConnector, ledger.Ledger]:
    config = ledger.LedgerConfig(plans={"app": 50}, months={"2026-10": 200})
    book = ledger.Ledger(path, config=config)

    def respond(sql: str) -> Reply | BaseException | None:
        if sql.startswith("EXPLAIN VIRTUAL"):
            return Reply((fetchlog.PUSHDOWN_SQL_COLUMN,), ((_manifest(source),),))
        if sql.endswith('"."QUOTA"'):
            return Reply(
                ("BACKFILL_SPENDABLE", "REFRESH_SPENDABLE", "REFRESH_RESERVE", "USED_CALLS"),
                ((quota, 150, 50, 10),),
            )
        if sql.startswith("SELECT * FROM") and '"GAMES_PREVIEW"' in sql:
            if fail:
                return RuntimeError("synthetic fetch failed")
            return Reply(("REQ_SLICE", "REQ_SOURCE", "REQ_ATTEMPTS"), ((KEY, source, 1),))
        return None

    connector = FakeConnector(respond)
    executor = live.LiveExecutor(
        NS,
        operator.OperatorPath(_settings(path), connector),
        book,
        target="local",
        reconcile_pending=lambda: pending,
        clock=lambda: NOW,
    )
    return executor, connector, book


def _paid_queries(connector: FakeConnector) -> list[str]:
    return [
        sql
        for connection in connector.connections
        for sql in connection.statements
        if sql.startswith("SELECT * FROM") and '"GAMES_PREVIEW"' in sql
    ]


def test_literal_filter_and_owner_path_are_guarded_before_one_paid_query(tmp_path: Path) -> None:
    executor, connector, book = _make(tmp_path / "calls.jsonl")
    result = executor.run_preview(GAME, max_calls=1, action_id="abc7")
    assert _paid_queries(connector) == [live.preview_statement(NS, GAME)]
    assert connector.connects == [
        ("CFB_TEST_OWNER", "synthetic-owner-password", operator.OPERATOR_SOCKET_TIMEOUT_SECONDS)
    ]
    assert result.calls_charged == 4  # unknown REQ_ATTEMPTS is charged conservatively
    assert book.entries()[0].slice_key == KEY
    assert any("FETCH_LOG" in sql for sql in connector.statements())
    for invalid in (0, True, "401", 10**18):
        with pytest.raises(ValueError):
            live.preview_statement(NS, invalid)  # type: ignore[arg-type]


def test_quota_and_reconciliation_refuse_before_fetch(tmp_path: Path) -> None:
    for options, code in (
        ({"quota": 0}, "quota_refused"),
        ({"pending": True}, "reconcile_pending"),
    ):
        executor, connector, book = _make(tmp_path / f"{code}.jsonl", **options)
        with pytest.raises(live.LiveRefused) as error:
            executor.run_preview(GAME, max_calls=1, action_id="a7")
        assert error.value.code == code
        assert _paid_queries(connector) == []
        assert book.entries() == []


def test_failed_paid_query_charges_before_fetch_log_and_is_not_retried(tmp_path: Path) -> None:
    executor, connector, book = _make(tmp_path / "failed.jsonl", fail=True)
    with pytest.raises(live.LiveFailed) as error:
        executor.run_preview(GAME, max_calls=1, action_id="a8")
    assert error.value.calls_charged == 4
    assert len(_paid_queries(connector)) == 1
    assert len(book.entries()) == 1
    assert book.entries()[0].cost == 4
    assert any("FETCH_LOG" in sql for sql in connector.statements())


def test_replay_plan_runs_without_ledger_lock_or_quota(tmp_path: Path) -> None:
    executor, connector, book = _make(tmp_path / "replay.jsonl", source="REPLAY")
    result = executor.run_preview(GAME, max_calls=0, action_id="a9")
    assert result.plan.source == "REPLAY" and result.calls_charged == 0
    assert not any('"QUOTA"' in sql for sql in connector.statements())
    assert not book.path.with_name(book.path.name + ".lock").exists()
    assert len(_paid_queries(connector)) == 1


def test_failed_ledger_write_blocks_the_next_paid_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, connector, book = _make(tmp_path / "ledger-failure.jsonl")
    pending = {"value": False}
    executor.reconcile_pending = lambda: pending["value"]
    executor.on_record_failure = lambda: pending.__setitem__("value", True)
    monkeypatch.setattr(book, "append", lambda _entry: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(live.LiveFailed, match="ledger write failed"):
        executor.run_preview(GAME, max_calls=1, action_id="first")
    assert pending["value"] and len(_paid_queries(connector)) == 1
    with pytest.raises(live.LiveRefused) as refusal:
        executor.run_preview(GAME, max_calls=1, action_id="second")
    assert refusal.value.code == "reconcile_pending"
    assert len(_paid_queries(connector)) == 1


class _Principal:
    """A console-user connection that answers EXPLAIN and the statement; never the owner path."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.statements: list[str] = []

    def execute(self, sql: str) -> object:
        self.statements.append(sql)
        if sql.startswith("EXPLAIN VIRTUAL"):
            reply = Reply((fetchlog.PUSHDOWN_SQL_COLUMN,), ((_manifest(self.source),),))
        else:
            reply = Reply(("REQ_SLICE", "REQ_SOURCE", "REQ_ATTEMPTS"), ((KEY, self.source, 1),))
        return FakeStatement(sql, reply)


def test_console_statement_runs_as_its_principal_and_charges_once(tmp_path: Path) -> None:
    executor, connector, book = _make(tmp_path / "console.jsonl")
    principal = _Principal("LIVE")
    sql = 'SELECT * FROM "TEST_CFBD_VS"."GAMES_TEAMS"'
    result = executor.run_statement(
        sql,
        max_calls=1,
        action_id="c1",
        label="console fan-out",
        context="APP_CONSOLE",
        query=principal,
    )
    assert principal.statements[-1] == sql
    assert not any(statement == sql for statement in connector.statements())
    assert any('"QUOTA"' in statement for statement in connector.statements())
    assert any("FETCH_LOG" in statement for statement in connector.statements())
    assert [entry.run for entry in book.entries()] == ["APP_CONSOLE#c1"]
    assert result.calls_charged == 4 and [row.slice_key for row in result.logged] == [KEY]
    with pytest.raises(ValueError):
        executor.run_statement(sql, max_calls=0, action_id="c2", label="x", context="OTHER")
