import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import loader_cli
from vs.cfbd_vs import http
from vs.host import ledger, target
from vs.host.ledger import Ledger, LedgerConfig

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
PLAN = "add-cfb-persistence-refresh"
CONFIG = LedgerConfig(plans={PLAN: 300}, months={"2026-10": 400})
QUOTA_COLUMNS = (
    "TIER_NAME",
    "MONTHLY_LIMIT",
    "USED_CALLS",
    "REMAINING_CALLS",
    "RESET_AT",
    "PERIOD_START",
    "MONTHLY_CALL_BUDGET",
    "REFRESH_RESERVE",
    "PROJECT_BUDGET",
    "REFRESH_SPENDABLE",
    "BACKFILL_SPENDABLE",
    "BACKFILL_CALLS",
    "REFRESH_CALLS",
    "OTHER_CALLS",
    "DEMO_CALLS_TO_COMPLETE",
    "FULL_CALLS_TO_COMPLETE",
)
BACKFILL_COLUMNS = (
    "RUN_ID",
    "TABLE_NAME",
    "SLICES",
    "ROWS_LOADED",
    "CALLS",
    "FAILED_SLICES",
    "RUN_STATUS",
    "CALLS_NEEDED",
    "DETAIL",
)
REFRESH_COLUMNS = (
    "RUN_ID",
    "STEP_NO",
    "STEP_NAME",
    "STEP_STATUS",
    "SECONDS",
    "SLICES",
    "ROWS_LOADED",
    "CALLS",
    "DETAIL",
)
BACKFILL_SQL = 'EXECUTE SCRIPT "CFB_INFRA"."BACKFILL"(\'demo\', NULL, {limit})'
REFRESH_SQL = 'EXECUTE SCRIPT "CFB_INFRA"."REFRESH_WEEK"(2026, 9, \'regular\')'
SESSION = "4711"
SESSION_SQL = "SELECT CAST(CURRENT_SESSION AS VARCHAR(20))"
KILL_SQL = f"KILL SESSION {SESSION}"
REFUSAL = (
    "BACKFILL refused: REFRESH_WEEK run 3 is running (session 9, started 2026-10-22 09:00:00 UTC)."
    ' Only one loader run at a time caught in script "CFB_INFRA"."BACKFILL" at line 380'
)


def quota_row(used: int = 50, limit: int = 1000, **override: Any) -> tuple[Any, ...]:
    values: dict[str, Any] = {
        "TIER_NAME": "Free",
        "MONTHLY_LIMIT": limit,
        "USED_CALLS": used,
        "REMAINING_CALLS": limit - used,
        "RESET_AT": "2026-11-01 00:00:00",
        "PERIOD_START": "2026-10-01 00:00:00",
        "MONTHLY_CALL_BUDGET": 400,
        "REFRESH_RESERVE": 50,
        "PROJECT_BUDGET": 400,
        "REFRESH_SPENDABLE": 350,
        "BACKFILL_SPENDABLE": 300,
        "BACKFILL_CALLS": 0,
        "REFRESH_CALLS": 0,
        "OTHER_CALLS": used,
        "DEMO_CALLS_TO_COMPLETE": 217,
        "FULL_CALLS_TO_COMPLETE": 217,
    }
    values.update(override)
    return tuple(values[name] for name in QUOTA_COLUMNS)


def backfill_result(
    status: str = "DONE", calls: int = 217, run_id: int = 7
) -> tuple[Sequence[str], list[tuple[Any, ...]]]:
    rows: list[tuple[Any, ...]] = [
        (run_id, "GAMES", 157, 90000, calls - 60, 0, "DONE", 0, None),
        (run_id, "*", 217, 120000, calls, 0, status, 0, None),
    ]
    return BACKFILL_COLUMNS, rows


def refresh_result(
    failed: bool = False, run_id: int = 9
) -> tuple[Sequence[str], list[tuple[Any, ...]]]:
    rows: list[tuple[Any, ...]] = [
        (run_id, 1, "CHECK", "DONE", 0.1, 0, 0, 0, None),
        (run_id, 2, "FETCH GAMES", "FAILED" if failed else "DONE", 1.5, 1, 800, 1, "boom"),
    ]
    return REFRESH_COLUMNS, rows


class FakeStatement:
    def __init__(self, columns: Sequence[str], rows: Sequence[tuple[Any, ...]]) -> None:
        self._columns = list(columns)
        self._rows = list(rows)

    def column_names(self) -> list[str]:
        return list(self._columns)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeLoaderDatabase:
    """Answers the statements the CLI sends from a script, and records all of them."""

    def __init__(
        self,
        *,
        mode: str | None = "live",
        scripts: Sequence[str] = ("BACKFILL", "REFRESH_WEEK"),
        quotas: Sequence[tuple[Any, ...] | Exception | None] = (quota_row(), quota_row(used=267)),
        queue: Sequence[tuple[str, int]] = (("GAMES", 157), ("RATINGS_SP", 14), ("TEAMS", 1)),
        candidates: Sequence[tuple[str, int]] = (("GAMES", 1), ("RATINGS_SP", 1)),
        blocked: Sequence[tuple[str, str]] = (),
        result: tuple[Sequence[str], list[tuple[Any, ...]]] | BaseException | None = None,
        run_row: tuple[Any, ...] | Exception | None = (7, 217, "DONE"),
        connection: str = "CFBD_API",
        session_states: Sequence[str | None] = (None,),
        dead_after_script: bool = False,
    ) -> None:
        self.mode = mode
        self.scripts = set(scripts)
        self.quotas = list(quotas)
        self.queue = list(queue)
        self.candidates = list(candidates)
        self.blocked = list(blocked)
        self.result = result if result is not None else backfill_result()
        self.run_row = run_row
        self.connection = connection
        self.session_states = list(session_states)
        self.dead_after_script = dead_after_script
        self.statements: list[str] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def execute(self, query: str) -> FakeStatement:
        if self.dead_after_script and self.scripts_run():
            raise ConnectionError("the connection is closed")
        self.statements.append(query)
        if "EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES" in query:
            rows = [("CONNECTION_NAME", self.connection)]
            if self.mode:
                rows.append(("FETCH_MODE", self.mode))
            return FakeStatement(("PROPERTY_NAME", "PROPERTY_VALUE"), rows)
        if query == SESSION_SQL:
            return FakeStatement(("SESSION_TEXT",), [(SESSION,)])
        if '"EXA_ALL_SESSIONS"' in query:
            states = self.session_states
            state = states.pop(0) if len(states) > 1 else states[0]
            return FakeStatement(("STATUS",), [(state,)] if state else [])
        if query.startswith("KILL SESSION"):
            return FakeStatement((), [])
        if "FROM EXA_ALL_SCRIPTS" in query:
            found = any(f"SCRIPT_NAME = '{name}'" in query for name in self.scripts)
            return FakeStatement(("N",), [(int(found),)])
        if '."QUOTA"' in query:
            return self._next_quota()
        if '."BACKFILL_QUEUE"' in query:
            return FakeStatement(("TABLE_NAME", "N"), self.queue)
        if '."LOAD_SLICES"' in query:
            return FakeStatement(("TABLE_NAME", "N"), self.candidates)
        if '."LOAD_STATUS"' in query:
            return FakeStatement(("TABLE_NAME", "BLOCKED_BY"), self.blocked)
        if '."LOAD_RUN"' in query:
            if isinstance(self.run_row, Exception):
                raise self.run_row
            rows = [self.run_row] if self.run_row else []
            return FakeStatement(("RUN_ID", "CALLS", "RUN_STATUS"), rows)
        if query.startswith("EXECUTE SCRIPT"):
            if isinstance(self.result, BaseException):
                raise self.result
            return FakeStatement(*self.result)
        raise AssertionError(f"unexpected statement: {query[:120]}")

    def _next_quota(self) -> FakeStatement:
        item = self.quotas.pop(0) if len(self.quotas) > 1 else self.quotas[0]
        if isinstance(item, Exception):
            raise item
        return FakeStatement(QUOTA_COLUMNS, [item] if item is not None else [])

    def scripts_run(self) -> list[str]:
        return [s for s in self.statements if s.startswith("EXECUTE SCRIPT")]

    def read_quota(self) -> bool:
        return any('."QUOTA"' in s for s in self.statements)


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        db: FakeLoaderDatabase,
        book: Ledger | None = None,
        recovery: FakeLoaderDatabase | Exception | None = None,
    ) -> None:
        self.db = db
        self.book = book or Ledger(tmp_path / "live-calls.jsonl", config=CONFIG)
        self.deployments: list[target.Target] = []
        self.requested: list[str | None] = []
        self.recovery = db if recovery is None else recovery
        self.pauses: list[float] = []

    def resolve(self, name: str | None = None, **_: Any) -> target.Target:
        self.requested.append(name)
        chosen = name or "local"
        return target.Target(chosen, "db", 8563, "sys", Path("/nowhere"), target.TLS_PINNED)

    def connector(self, deployment: target.Target) -> FakeLoaderDatabase:
        self.deployments.append(deployment)
        if len(self.deployments) == 1:
            return self.db
        if isinstance(self.recovery, Exception):
            raise self.recovery
        return self.recovery

    def run(self, *argv: str) -> int:
        return loader_cli.main(
            list(argv),
            connector=self.connector,
            resolve=self.resolve,
            book=self.book,
            clock=lambda: NOW,
            pause=self.pauses.append,
        )

    def entries(self) -> list[ledger.Entry]:
        return self.book.entries()


@pytest.fixture
def make_harness(tmp_path: Path) -> Callable[..., Harness]:
    def build(db: FakeLoaderDatabase | None = None, book: Ledger | None = None) -> Harness:
        return Harness(tmp_path, db or FakeLoaderDatabase(), book)

    return build


def test_quota_prints_the_view_and_the_ledger_room(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness()
    assert harness.run("quota") == loader_cli.EXIT_OK
    out = capsys.readouterr().out
    assert "TIER_NAME" in out and "Free" in out
    assert "BACKFILL_SPENDABLE" in out and "300" in out
    assert "DEMO_CALLS_TO_COMPLETE" in out and "217" in out
    assert f"ledger room now: 300 ({PLAN} cap 300, month 2026-10 cap 400)" in out
    assert harness.db.closed
    assert harness.db.scripts_run() == []


def test_quota_without_a_row_fails(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[None]))
    assert harness.run("quota") == loader_cli.EXIT_ERROR
    assert "no row" in capsys.readouterr().err


def test_quota_with_the_ledger_off(
    make_harness: Callable[..., Harness], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = Ledger(tmp_path / "never.jsonl", enabled=False, config=CONFIG)
    harness = make_harness(book=book)
    assert harness.run("quota") == loader_cli.EXIT_OK
    assert "ledger: off (LEDGER=off)" in capsys.readouterr().out


def test_backfill_without_confirmation_prints_the_estimate_and_stops(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness()
    assert harness.run("backfill", "--profile", "demo") == loader_cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "estimate: 172 calls" in captured.out
    assert "GAMES" in captured.out and "157" in captured.out
    assert "BACKFILL_SPENDABLE" in captured.out
    assert "ledger room now: 300" in captured.out
    assert "rerun with CONFIRM=yes" in captured.err
    assert harness.db.scripts_run() == []
    assert harness.entries() == []
    assert harness.db.closed


def test_backfill_estimate_reads_the_queue_for_the_profile_and_season(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.run("backfill", "--profile", "demo", "--from-season", "2019")
    queue_sql = next(s for s in harness.db.statements if '."BACKFILL_QUEUE"' in s)
    assert '"PROFILE_RANK" <= 1' in queue_sql
    assert '"SEASON" IS NULL OR "SEASON" <= 2019' in queue_sql
    assert '"CFB_META"."BACKFILL_QUEUE"' in queue_sql
    full = make_harness()
    full.run("backfill", "--profile", "full")
    assert '"PROFILE_RANK" <= 2' in next(s for s in full.db.statements if "BACKFILL_QUEUE" in s)


def test_backfill_estimate_names_blocked_tables(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(blocked=[("GAMES", "ORPHANS")]))
    harness.run("backfill", "--profile", "demo")
    assert "blocked, no calls: GAMES (ORPHANS)" in capsys.readouterr().out


def test_confirmed_backfill_runs_with_the_ledger_room_as_max_calls(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness()
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_OK
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit=300)]
    out = capsys.readouterr().out
    assert "GAMES" in out and "DONE" in out
    assert "ledger: recorded 217 calls as BACKFILL#7" in out


def test_backfill_records_the_measured_calls_in_the_ledger(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[quota_row(50), quota_row(267)]))
    assert harness.run("--confirm", "yes", "backfill", "--profile", "demo") == loader_cli.EXIT_OK
    [entry] = harness.entries()
    assert (entry.plan, entry.target, entry.slice_key, entry.cost) == (
        PLAN,
        "local",
        "BACKFILL#7",
        217,
    )
    assert (entry.remaining_before, entry.remaining_after) == (950, 733)
    assert entry.error is None
    assert entry.ts.startswith("2026-10-22T09:30:00")


def test_the_ledger_records_the_larger_of_the_run_and_the_info_delta(
    make_harness: Callable[..., Harness],
) -> None:
    db = FakeLoaderDatabase(quotas=[quota_row(50), quota_row(290)], run_row=(7, 217, "DONE"))
    harness = make_harness(db)
    harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert [e.cost for e in harness.entries()] == [240]


def test_a_smaller_max_calls_is_passed_through(make_harness: Callable[..., Harness]) -> None:
    harness = make_harness()
    harness.run("--confirm", "yes", "backfill", "--profile", "demo", "--max-calls", "20")
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit=20)]


def test_a_larger_max_calls_is_capped_at_the_ledger_room(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness()
    harness.run("--confirm", "yes", "backfill", "--profile", "demo", "--max-calls", "900")
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit=300)]
    assert "MAX_CALLS 900 capped to the ledger room 300" in capsys.readouterr().out


def test_the_room_follows_the_info_used_calls(make_harness: Callable[..., Harness]) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[quota_row(used=380), quota_row(used=398)]))
    harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit=20)]


def test_the_room_follows_the_plan_total(
    make_harness: Callable[..., Harness], tmp_path: Path
) -> None:
    book = Ledger(tmp_path / "used.jsonl", config=CONFIG)
    book.record_run(PLAN, "aws", kind="BACKFILL", run_id=1, calls=250, clock=lambda: NOW)
    harness = make_harness(FakeLoaderDatabase(quotas=[quota_row(used=250)]), book)
    harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit=50)]


def test_a_dry_run_is_confirmed_like_any_live_run_and_costs_no_room(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    refused = harness.run("backfill", "--profile", "demo", "--max-calls", "0")
    assert refused == loader_cli.EXIT_REFUSED and harness.db.scripts_run() == []
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo", "--max-calls", "0")
    assert code == loader_cli.EXIT_OK
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit=0)]


def test_a_month_without_a_ledger_entry_refuses(
    make_harness: Callable[..., Harness], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = Ledger(tmp_path / "l.jsonl", config=LedgerConfig({PLAN: 300}, {"2026-09": 400}))
    harness = make_harness(book=book)
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "refused:" in err and "no entry for month 2026-10" in err
    assert harness.db.scripts_run() == [] and harness.entries() == []


def test_no_ledger_room_refuses_a_run_that_needs_calls(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[quota_row(used=400)]))
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_REFUSED
    assert "ledger room is 0" in capsys.readouterr().err
    assert harness.db.scripts_run() == []


def test_the_refusal_is_shown_in_the_preview_too(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[quota_row(used=400)]))
    harness.run("backfill", "--profile", "demo")
    assert "ledger: would refuse" in capsys.readouterr().out


def test_nothing_pending_runs_even_without_ledger_room(
    make_harness: Callable[..., Harness],
) -> None:
    db = FakeLoaderDatabase(quotas=[quota_row(used=400)], queue=[], result=backfill_result(calls=0))
    harness = make_harness(db)
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_OK
    assert db.scripts_run() == [BACKFILL_SQL.format(limit=0)]


def test_an_unreadable_quota_refuses_a_live_run(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[None]))
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_ERROR
    assert "quota" in capsys.readouterr().err
    assert harness.db.scripts_run() == []


def test_a_missing_script_stops_before_anything_else(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(scripts=["REFRESH_WEEK"]))
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert '"CFB_INFRA"."BACKFILL" is not installed' in err and "make deploy" in err
    assert not harness.db.read_quota() and harness.db.scripts_run() == []
    assert harness.db.closed


def test_a_missing_refresh_script_names_it(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(scripts=["BACKFILL"]))
    assert harness.run("refresh", "--season", "2026", "--week", "9") == loader_cli.EXIT_ERROR
    assert '"CFB_INFRA"."REFRESH_WEEK" is not installed' in capsys.readouterr().err


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("DONE", loader_cli.EXIT_OK),
        ("STOPPED_MAX_CALLS", loader_cli.EXIT_OK),
        ("STOPPED_BUDGET", loader_cli.EXIT_OK),
        ("DONE_WITH_FAILURES", loader_cli.EXIT_FAILED),
        ("FAILED", loader_cli.EXIT_FAILED),
    ],
)
def test_the_exit_code_follows_the_run_status(
    make_harness: Callable[..., Harness], status: str, code: int
) -> None:
    harness = make_harness(FakeLoaderDatabase(result=backfill_result(status)))
    assert harness.run("--confirm", "yes", "backfill", "--profile", "demo") == code
    assert len(harness.entries()) == 1


def test_a_script_error_is_recorded_and_fails_the_run(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeLoaderDatabase(
        result=RuntimeError("batch 3 failed\nsecond line"),
        quotas=[quota_row(50), quota_row(80)],
        run_row=(11, 25, "FAILED"),
        session_states=["IDLE"],
    )
    harness = make_harness(db)
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_FAILED
    assert "script failed: batch 3 failed" in capsys.readouterr().err
    [entry] = harness.entries()
    assert (entry.slice_key, entry.cost, entry.error) == ("BACKFILL#11", 30, "batch 3 failed")
    run_sql = next(s for s in db.statements if '."LOAD_RUN"' in s)
    assert f'"SESSION_ID" = {SESSION}' in run_sql
    assert KILL_SQL not in db.statements


def test_the_session_is_identified_before_the_script_runs(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    statements = harness.db.statements
    assert statements.index(SESSION_SQL) < statements.index(BACKFILL_SQL.format(limit=300))


def test_an_interrupted_run_is_recorded_from_a_new_session_and_re_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review M1: a Ctrl-C during the script still writes the run to the ledger."""
    db = FakeLoaderDatabase(result=KeyboardInterrupt(), quotas=[quota_row(50)])
    recovery = FakeLoaderDatabase(
        quotas=[quota_row(95)], run_row=(7, 40, "RUNNING"), session_states=["EXECUTE SQL", None]
    )
    harness = Harness(tmp_path, db, recovery=recovery)
    with pytest.raises(KeyboardInterrupt):
        harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    [entry] = harness.entries()
    assert (entry.slice_key, entry.cost) == ("BACKFILL#7", 45)
    assert (entry.remaining_before, entry.remaining_after) == (950, 905)
    assert entry.error == "interrupted (KeyboardInterrupt)"
    assert db.scripts_run() == [BACKFILL_SQL.format(limit=300)]
    kill = recovery.statements.index(KILL_SQL)
    quota = next(i for i, s in enumerate(recovery.statements) if '."QUOTA"' in s)
    assert kill < quota
    run_sql = next(s for s in recovery.statements if '."LOAD_RUN"' in s)
    assert f'"SESSION_ID" = {SESSION}' in run_sql
    assert recovery.closed and db.closed
    out = capsys.readouterr().out
    assert f"session {SESSION} was still running the script: killed it" in out
    assert "ledger: recorded 45 calls as BACKFILL#7" in out


def test_a_timed_out_run_is_recorded_from_a_new_session(tmp_path: Path) -> None:
    db = FakeLoaderDatabase(
        result=TimeoutError("socket timed out"), quotas=[quota_row(50)], dead_after_script=True
    )
    recovery = FakeLoaderDatabase(
        quotas=[quota_row(120)],
        run_row=(7, 70, "RUNNING"),
        session_states=["EXECUTE SQL", "EXECUTE SQL", "EXECUTE SQL", None],
    )
    harness = Harness(tmp_path, db, recovery=recovery)
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_FAILED
    [entry] = harness.entries()
    assert (entry.slice_key, entry.cost, entry.error) == ("BACKFILL#7", 70, "socket timed out")
    assert KILL_SQL in recovery.statements
    assert harness.pauses == [loader_cli.RECOVERY_POLL_SECONDS] * 2


def test_a_session_that_does_not_end_is_noted_and_the_run_still_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeLoaderDatabase(result=KeyboardInterrupt(), quotas=[quota_row(50)])
    recovery = FakeLoaderDatabase(
        quotas=[quota_row(60)], run_row=(7, 10, "RUNNING"), session_states=["EXECUTE SQL"] * 999
    )
    harness = Harness(tmp_path, db, recovery=recovery)
    with pytest.raises(KeyboardInterrupt):
        harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert [e.cost for e in harness.entries()] == [10]
    polls = round(loader_cli.RECOVERY_WAIT_SECONDS / loader_cli.RECOVERY_POLL_SECONDS)
    assert len(harness.pauses) == polls
    assert "did not end within" in capsys.readouterr().out


@pytest.mark.parametrize(("max_calls", "charged"), [((), 300), (("--max-calls", "20"), 20)])
def test_when_nothing_can_be_read_a_backfill_is_charged_its_max_calls(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    max_calls: tuple[str, ...],
    charged: int,
) -> None:
    db = FakeLoaderDatabase(result=KeyboardInterrupt(), quotas=[quota_row(50)])
    harness = Harness(tmp_path, db, recovery=ConnectionError("the database is unreachable"))
    with pytest.raises(KeyboardInterrupt):
        harness.run("--confirm", "yes", "backfill", "--profile", "demo", *max_calls)
    [entry] = harness.entries()
    assert (entry.slice_key, entry.cost) == ("BACKFILL#0", charged)
    out = capsys.readouterr().out
    assert f"charged the script's cap max_calls {charged}" in out
    assert "the database is unreachable" in out


def test_when_nothing_can_be_read_a_refresh_is_charged_every_attempt_of_its_slices(
    tmp_path: Path,
) -> None:
    db = FakeLoaderDatabase(
        result=TimeoutError("socket timed out"), quotas=[quota_row(50)], dead_after_script=True
    )
    harness = Harness(tmp_path, db, recovery=ConnectionError("down"))
    code = harness.run("--confirm", "yes", "refresh", "--season", "2026", "--week", "9")
    assert code == loader_cli.EXIT_FAILED
    assert [e.cost for e in harness.entries()] == [2 * http.MAX_ATTEMPTS]


def test_a_refusal_raised_by_the_script_exits_refused_and_records_nothing(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(result=RuntimeError(REFUSAL)))
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "refused: BACKFILL refused: REFRESH_WEEK run 3 is running" in err
    assert "caught in script" not in err
    assert harness.entries() == []


def test_ledger_off_runs_without_a_limit_and_writes_nothing(
    make_harness: Callable[..., Harness], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "off.jsonl"
    harness = make_harness(book=Ledger(path, enabled=False, config=CONFIG))
    code = harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_OK
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit="NULL")]
    assert not path.exists()
    out = capsys.readouterr().out
    assert "ledger: off (LEDGER=off)" in out and "recorded" not in out


@pytest.mark.parametrize("word", ["", "maybe", "YES", "true"])
def test_only_the_word_yes_confirms(make_harness: Callable[..., Harness], word: str) -> None:
    harness = make_harness()
    code = harness.run("--confirm", word, "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_REFUSED
    assert harness.db.scripts_run() == []


def test_ledger_off_still_requires_confirmation(
    make_harness: Callable[..., Harness], tmp_path: Path
) -> None:
    harness = make_harness(book=Ledger(tmp_path / "off.jsonl", enabled=False, config=CONFIG))
    assert harness.run("backfill", "--profile", "demo") == loader_cli.EXIT_REFUSED
    assert harness.db.scripts_run() == []


def test_the_default_ledger_honours_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "env.jsonl"
    monkeypatch.setenv("LEDGER_PATH", str(path))
    monkeypatch.delenv("LEDGER", raising=False)
    db = FakeLoaderDatabase()
    harness = Harness(tmp_path, db)
    code = loader_cli.main(
        ["--confirm", "yes", "backfill", "--profile", "demo"],
        connector=harness.connector,
        resolve=harness.resolve,
        clock=lambda: NOW,
    )
    assert code == loader_cli.EXIT_OK
    assert path.exists() and len(path.read_text(encoding="utf-8").splitlines()) == 1
    monkeypatch.setenv("LEDGER", "off")
    path.unlink()
    again = loader_cli.main(
        ["--confirm", "yes", "backfill", "--profile", "demo"],
        connector=harness.connector,
        resolve=harness.resolve,
        clock=lambda: NOW,
    )
    assert again == loader_cli.EXIT_OK
    assert db.scripts_run()[-1] == BACKFILL_SQL.format(limit="NULL")
    assert not path.exists()


def test_replay_mode_needs_no_confirmation_quota_or_ledger(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(mode="replay"))
    code = harness.run("backfill", "--profile", "demo", "--max-calls", "5")
    assert code == loader_cli.EXIT_OK
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit=5)]
    assert not harness.db.read_quota() and harness.entries() == []
    assert "replay mode: no API calls" in capsys.readouterr().out


def test_a_virtual_schema_without_a_fetch_mode_is_live(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(FakeLoaderDatabase(mode=None))
    assert harness.run("backfill", "--profile", "demo") == loader_cli.EXIT_REFUSED


def test_a_prefixed_namespace_uses_its_names_and_skips_the_ledger(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(connection="CFBD_API_MOCK"))
    code = harness.run("--prefix", "PROBE_", "--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_OK
    assert harness.db.scripts_run() == [
        'EXECUTE SCRIPT "PROBE_CFB_INFRA"."BACKFILL"(\'demo\', NULL, NULL)'
    ]
    assert any('"PROBE_CFB_META"."QUOTA"' in s for s in harness.db.statements)
    assert any("PROBE_CFBD_VS" in s for s in harness.db.statements)
    assert harness.entries() == []
    assert "ledger: not used: PROBE_CFBD_VS reads the mock connection CFBD_API_MOCK" in (
        capsys.readouterr().out
    )


def test_a_prefixed_namespace_on_the_real_connection_uses_the_ledger(
    make_harness: Callable[..., Harness],
) -> None:
    """Review m3: the connection the virtual schema reads decides, not the namespace name."""
    harness = make_harness(FakeLoaderDatabase(connection="CFBD_API"))
    code = harness.run("--prefix", "PROBE_", "--confirm", "yes", "backfill", "--profile", "demo")
    assert code == loader_cli.EXIT_OK
    assert harness.db.scripts_run() == [
        'EXECUTE SCRIPT "PROBE_CFB_INFRA"."BACKFILL"(\'demo\', NULL, 300)'
    ]
    assert [e.slice_key for e in harness.entries()] == ["BACKFILL#7"]


@pytest.mark.parametrize("connection", ["CFBD_API_KEY2", ""])
def test_any_connection_but_the_mock_uses_the_ledger(
    make_harness: Callable[..., Harness], connection: str
) -> None:
    harness = make_harness(FakeLoaderDatabase(connection=connection))
    harness.run("--confirm", "yes", "backfill", "--profile", "demo")
    assert len(harness.entries()) == 1


def test_the_real_namespace_on_the_mock_connection_skips_the_ledger(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(FakeLoaderDatabase(connection="cfbd_api_mock"))
    assert harness.run("--confirm", "yes", "backfill", "--profile", "demo") == loader_cli.EXIT_OK
    assert harness.db.scripts_run() == [BACKFILL_SQL.format(limit="NULL")]
    assert harness.entries() == []


def test_refresh_prints_the_weekly_candidates_and_needs_confirmation(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness()
    code = harness.run("refresh", "--season", "2026", "--week", "9")
    assert code == loader_cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "estimate: 2 calls" in captured.out and "RATINGS_SP" in captured.out
    assert "rerun with CONFIRM=yes" in captured.err
    assert harness.db.scripts_run() == []
    slices_sql = next(s for s in harness.db.statements if '."LOAD_SLICES"' in s)
    assert "\"REFRESH_CLASS\" = 'weekly'" in slices_sql
    assert '"SEASON" = 2026' in slices_sql
    assert "REFRESH_PROFILES" in slices_sql and "LOAD_ORPHANS" in slices_sql


def test_confirmed_refresh_runs_prints_the_timeline_and_records_the_calls(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeLoaderDatabase(
        result=refresh_result(), quotas=[quota_row(50), quota_row(52)], run_row=(9, 1, "DONE")
    )
    harness = make_harness(db)
    code = harness.run("--confirm", "yes", "refresh", "--season", "2026", "--week", "9")
    assert code == loader_cli.EXIT_OK
    assert db.scripts_run() == [REFRESH_SQL]
    out = capsys.readouterr().out
    assert "FETCH GAMES" in out and "CHECK" in out
    [entry] = harness.entries()
    assert (entry.slice_key, entry.cost) == ("REFRESH_WEEK#9", 2)
    assert (entry.remaining_before, entry.remaining_after) == (950, 948)


def test_refresh_passes_the_season_type(make_harness: Callable[..., Harness]) -> None:
    harness = make_harness(FakeLoaderDatabase(result=refresh_result()))
    harness.run(
        "--confirm", "yes", "refresh", "--season", "2026", "--week", "1",
        "--season-type", "postseason",
    )  # fmt: skip
    assert harness.db.scripts_run() == [
        'EXECUTE SCRIPT "CFB_INFRA"."REFRESH_WEEK"(2026, 1, \'postseason\')'
    ]


def test_a_failed_refresh_step_fails_the_run_but_is_recorded(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(result=refresh_result(failed=True)))
    code = harness.run("--confirm", "yes", "refresh", "--season", "2026", "--week", "9")
    assert code == loader_cli.EXIT_FAILED
    assert "FETCH GAMES: boom" in capsys.readouterr().out
    assert len(harness.entries()) == 1


def test_refresh_is_refused_when_the_estimate_does_not_fit_the_ledger(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[quota_row(used=399)]))
    code = harness.run("--confirm", "yes", "refresh", "--season", "2026", "--week", "9")
    assert code == loader_cli.EXIT_REFUSED
    assert "month 2026-10" in capsys.readouterr().err
    assert harness.db.scripts_run() == []


def test_quota_runs_in_replay_mode_without_the_ledger_lines(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(mode="replay"))
    assert harness.run("quota") == loader_cli.EXIT_OK
    assert "ledger" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["backfill", "--profile", "huge"],
        ["backfill", "--profile", "demo", "--max-calls", "-1"],
        ["backfill", "--profile", "demo", "--from-season", "1066"],
        ["backfill", "--profile", "demo", "--max-calls", "many"],
        ["refresh", "--season", "2026"],
        ["refresh", "--week", "9"],
        ["refresh", "--season", "2026", "--week", "0"],
        ["refresh", "--season", "2026", "--week", "9", "--season-type", "bowl"],
        [],
    ],
)
def test_invalid_arguments_stop_before_any_connection(
    make_harness: Callable[..., Harness], argv: list[str]
) -> None:
    harness = make_harness()
    with pytest.raises(SystemExit) as stop:
        harness.run(*argv)
    assert stop.value.code == loader_cli.EXIT_ERROR
    assert harness.deployments == []


def test_the_target_option_selects_the_deployment(make_harness: Callable[..., Harness]) -> None:
    harness = make_harness()
    harness.run("--target", "aws", "quota")
    assert harness.requested == ["aws"]
    assert [d.name for d in harness.deployments] == ["aws"]


def test_a_ledger_entry_carries_the_target_name(make_harness: Callable[..., Harness]) -> None:
    harness = make_harness()
    harness.run("--target", "aws", "--confirm", "yes", "backfill", "--profile", "demo")
    assert [e.target for e in harness.entries()] == ["aws"]


def test_an_unresolvable_target_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(name: str | None = None, **_: Any) -> target.Target:
        raise target.TargetError("no deployment")

    code = loader_cli.main(
        ["quota"], resolve=broken, connector=lambda _: FakeLoaderDatabase(), book=None
    )
    assert code == loader_cli.EXIT_ERROR
    assert "no deployment" in capsys.readouterr().err


def test_a_rejected_query_is_reported_without_a_traceback(
    make_harness: Callable[..., Harness], capsys: pytest.CaptureFixture[str]
) -> None:
    harness = make_harness(FakeLoaderDatabase(quotas=[RuntimeError("view missing\nmore")]))
    assert harness.run("quota") == loader_cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "Exasol rejected a query: view missing" in err and "more" not in err


def test_the_default_connector_waits_long_for_a_statement() -> None:
    seen: dict[str, Any] = {}
    database = FakeLoaderDatabase()

    class Deployment(target.Target):
        def connect(self, *args: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return database

    deployment = Deployment("local", "h", 1, "sys", Path("/x"), target.TLS_PINNED)
    assert loader_cli.connect_deployment(deployment) is database
    assert seen == {"socket_timeout": loader_cli.SOCKET_TIMEOUT_SECONDS}
    assert loader_cli.SOCKET_TIMEOUT_SECONDS >= 600


def test_the_module_runs_as_a_script_and_shows_its_usage() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "scripts.loader_cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0
    assert "backfill" in done.stdout and "refresh" in done.stdout and "quota" in done.stdout


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize(
    ("goal", "variables", "expected"),
    [
        (
            "backfill",
            ["PROFILE=full", "FROM=2019", "MAX_CALLS=20", "CONFIRM=yes", "TARGET=aws"],
            '-m scripts.loader_cli --confirm "yes" backfill --profile "full"'
            ' --from-season "2019" --max-calls "20"',
        ),
        ("backfill", [], '-m scripts.loader_cli --confirm "" backfill --profile "demo"'),
        (
            "refresh",
            ["SEASON=2026", "WEEK=9", "SEASON_TYPE=postseason", "CONFIRM=yes"],
            '-m scripts.loader_cli --confirm "yes" refresh --season "2026" --week "9"'
            ' --season-type "postseason"',
        ),
        ("quota", [], "-m scripts.loader_cli quota"),
    ],
)
def test_the_make_targets_call_the_cli(goal: str, variables: list[str], expected: str) -> None:
    done = subprocess.run(
        ["make", "-n", goal, *variables],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert expected in done.stdout
    assert "scripts.dbcheck" in done.stdout
