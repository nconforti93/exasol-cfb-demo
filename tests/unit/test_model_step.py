"""`model/step.py` host helpers over a fake connection: the call text and the post-hook of
`make backfill`, `make restore` and (until step 100 is registered) `make refresh`."""

from __future__ import annotations

from typing import Any

from model import step
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    def __init__(self, *, installed: bool = True, registered: bool = False, fail: bool = False):
        self.installed, self.registered, self.fail = installed, registered, fail
        self.statements: list[str] = []

    def execute(self, sql: str) -> _Rows:
        self.statements.append(sql)
        if "REFRESH_STEPS" in sql:
            return _Rows([(1 if self.registered else 0,)])
        if "EXA_ALL_SCRIPTS" in sql:
            return _Rows([(1 if self.installed else 0,)])
        if self.fail:
            raise RuntimeError("MODEL refused: BACKFILL run 3 is running\nmore")
        return _Rows([("TABLE", "GAME", None, "REBUILT", 1200, None)] * 2)


def test_the_step_call_renders_nulls_and_the_season_type() -> None:
    assert step.step_call(TEST) == (
        'EXECUTE SCRIPT "TEST_CFB_INFRA"."REFRESH_MODEL"(NULL, NULL, NULL, NULL)'
    )
    assert step.step_call(TEST, run_id=7, season=2026, week=9, season_type="postseason") == (
        'EXECUTE SCRIPT "TEST_CFB_INFRA"."REFRESH_MODEL"(7, 2026, 9, \'postseason\')'
    )


def test_the_post_hook_rebuilds_and_reports_the_rows() -> None:
    lines: list[str] = []
    connection = FakeConnection()
    assert step.rebuild_after_load(connection, TEST, lines.append)
    assert connection.statements[-1].endswith("(NULL, NULL, NULL, NULL)")
    assert lines == ["model: rebuilt 2 derived tables of TEST_CFB_MODEL (2,400 rows)"]


def test_the_post_hook_warns_instead_of_failing() -> None:
    lines: list[str] = []
    assert not step.rebuild_after_load(FakeConnection(installed=False), TEST, lines.append)
    assert not step.rebuild_after_load(FakeConnection(fail=True), TEST, lines.append)
    assert "is not installed; run `make model`" in lines[0]
    assert lines[1].startswith("warning: the model rebuild failed (MODEL refused: BACKFILL run 3")
    assert "stale until `make model-refresh` or the next refresh" in lines[1]


def test_a_refresh_skips_the_hook_once_step_100_is_registered() -> None:
    lines: list[str] = []
    registered = FakeConnection(registered=True)
    assert step.rebuild_after_load(registered, TEST, lines.append, unless_registered=True)
    assert lines == [] and not any("EXECUTE" in s for s in registered.statements)
    assert "'TEST_CFB_INFRA.REFRESH_MODEL'" in registered.statements[0]
    unregistered = FakeConnection()
    assert step.rebuild_after_load(unregistered, TEST, lines.append, unless_registered=True)
    assert unregistered.statements[-1].startswith("EXECUTE SCRIPT")
