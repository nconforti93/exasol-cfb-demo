"""The refresh worker sends one script and charges a live run even when it fails."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.backend import actions, operator, reconcile, refresh_worker
from app.backend.sessions import LiveGate
from app.backend.settings import Settings
from tests.unit.db_fakes import FakeConnector, Reply, query_error
from vs.host import ledger


def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        {
            "CFB_APP_PASSWORD": "synthetic-app-password",
            "APP_LEDGER_PLAN": "add-cfb-app",
            "APP_LEDGER_PATH": str(tmp_path / "live.jsonl"),
        },
        dotenv_path=tmp_path / "absent.env",
        secrets_file=tmp_path / "absent-secrets.env",
        resolver=lambda *_: ["127.0.0.1"],
    )


def scenario(
    tmp_path: Path,
    *,
    mode: str,
    cap: int = 12,
    fail: bool = False,
    run_status: str | None = None,
) -> tuple[Any, actions.ActionStore, ledger.Ledger, FakeConnector, str]:
    chosen = settings(tmp_path)

    def answer(sql: str) -> Reply | BaseException:
        if "EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES" in sql:
            return Reply(("PROPERTY_VALUE",), ((mode,),))
        if '"LOAD_SLICES"' in sql:
            return Reply(("TABLE_NAME", "COUNT"), (("GAMES", 1),))
        if '"LOAD_STATUS"' in sql or "\"RUN_STATUS\" = 'RUNNING'" in sql:
            return Reply(rows=())
        if sql.startswith("EXECUTE SCRIPT"):
            return (
                query_error("42000", "synthetic refresh failure")
                if fail
                else Reply(("RUN_ID", "STEP_STATUS"), ((42, "DONE"),))
            )
        if 'SELECT "RUN_ID" FROM "CFB_META"."LOAD_RUN"' in sql:
            return Reply(("RUN_ID",), ((42,),))
        if 'SELECT * FROM "CFB_META"."LOAD_RUN"' in sql:
            return Reply(
                ("RUN_ID", "CALLS", "RUN_STATUS"),
                ((42, 1, run_status or ("FAILED" if fail else "DONE")),),
            )
        if 'SELECT * FROM "CFB_META"."QUOTA"' in sql:
            return Reply(
                ("USED_CALLS", "REMAINING_CALLS", "REFRESH_SPENDABLE"),
                ((246, 2754, 154),),
            )
        raise AssertionError(f"unexpected operator statement: {sql}")

    connector = FakeConnector(answer)
    path = operator.OperatorPath(chosen, connector)
    point = {
        "AS_OF_SEASON": 2026,
        "AS_OF_WEEK_SEQ": 7,
        "AS_OF_LABEL": "2026, after week 7",
    }
    pool = SimpleNamespace(run=lambda *_: SimpleNamespace(dicts=lambda: [point]))
    state = SimpleNamespace(
        operator=path,
        database=SimpleNamespace(pool=pool),
        live_gate=LiveGate(),
        reconciled=True,
    )
    month = datetime.now(UTC).strftime("%Y-%m")
    book = ledger.Ledger(
        tmp_path / "live.jsonl",
        config=ledger.LedgerConfig({"add-cfb-app": cap}, {month: 400}),
    )
    store = actions.ActionStore()
    action_id = store.begin(refresh_worker.point_ref(state.database, chosen))
    return state, store, book, connector, action_id


def run(
    state: Any,
    store: actions.ActionStore,
    book: ledger.Ledger,
    action_id: str,
    tmp_path: Path,
    *,
    mode: str,
) -> dict[str, Any]:
    refresh_worker.run_refresh(
        state,
        settings(tmp_path),
        book,
        store,
        action_id,
        season=2026,
        week=8,
        season_type="regular",
        max_calls=1 if mode == "LIVE" else 0,
        planned_mode=mode,
    )
    result = store.get(action_id)
    assert result is not None
    return result


def sent(connector: FakeConnector) -> list[str]:
    return [sql for connection in connector.connections for sql in connection.statements]


def test_replay_refresh_runs_once_and_never_writes_a_ledger_entry(tmp_path: Path) -> None:
    state, store, book, connector, action_id = scenario(tmp_path, mode="REPLAY")
    outcome = run(state, store, book, action_id, tmp_path, mode="REPLAY")
    assert outcome["state"] == "DONE" and outcome["run_id"] == 42
    assert outcome["calls_spent"] is None
    assert store.active() is None and book.entries() == []
    assert sum(sql.startswith("EXECUTE SCRIPT") for sql in sent(connector)) == 1


def test_live_failure_is_charged_before_the_action_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reconcile, "startup", lambda *_: None)
    state, store, book, connector, action_id = scenario(tmp_path, mode="LIVE", fail=True)
    outcome = run(state, store, book, action_id, tmp_path, mode="LIVE")
    assert outcome["state"] == "FAILED" and outcome["run_id"] == 42
    assert outcome["calls_spent"] == 1
    assert [entry.cost for entry in book.entries()] == [1]
    assert book.entries()[0].slice_key == "REFRESH_WEEK#42"
    assert sum(sql.startswith("EXECUTE SCRIPT") for sql in sent(connector)) == 1


def test_worst_case_retry_room_refuses_before_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reconcile, "startup", lambda *_: None)
    state, store, book, connector, action_id = scenario(tmp_path, mode="LIVE", cap=3)
    outcome = run(state, store, book, action_id, tmp_path, mode="LIVE")
    assert outcome["state"] == "FAILED"
    assert "headroom" in outcome["error"]
    assert not any(sql.startswith("EXECUTE SCRIPT") for sql in sent(connector))
    assert book.entries() == [] and state.reconciled is True


def test_an_unsettled_run_is_charged_for_every_possible_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reconcile, "startup", lambda *_: None)
    state, store, book, _, action_id = scenario(
        tmp_path, mode="LIVE", fail=True, run_status="RUNNING"
    )
    outcome = run(state, store, book, action_id, tmp_path, mode="LIVE")
    assert outcome["state"] == "FAILED" and outcome["calls_spent"] == 4
    assert [entry.cost for entry in book.entries()] == [4]


def test_ledger_write_failure_blocks_the_next_live_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, store, book, connector, action_id = scenario(tmp_path, mode="LIVE")

    def fail_record(*_: Any, **__: Any) -> None:
        raise ledger.LedgerError("synthetic ledger write failure")

    monkeypatch.setattr(book, "record_run", fail_record)
    outcome = run(state, store, book, action_id, tmp_path, mode="LIVE")
    assert outcome["state"] == "FAILED" and state.reconciled is False
    assert book.entries() == []
    assert sum(sql.startswith("EXECUTE SCRIPT") for sql in sent(connector)) == 1
