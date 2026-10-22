"""`loader/runlock.py`: the host side of the run lock, over a fake connection (no database).

The fake answers the `RUN_LOCK` script the way the bundled Lua does (`test_run_lock_lua.py`); the
behaviour on the engine is covered by `tests/it/test_run_lock_it.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from loader import runlock
from loader.runlock import RunLockError, RunLockRefused, run_lock
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")
SCRIPT = '"TEST_CFB_INFRA"."RUN_LOCK"'
REFUSAL = (
    "RESTORE refused: BACKFILL run 7 is running (session 123, started 2026-10-22 10:00:00 UTC). "
    "Only one loader run at a time: wait until it ends."
)


class FakeQueryError(Exception):
    """Like `pyexasol.ExaQueryError`: the database text is in `message`."""

    def __init__(self, message: str) -> None:
        super().__init__(f"query failed: {message}")
        self.message = message


class Result:
    def __init__(self, rows: Sequence[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(
        self,
        *,
        installed: bool = True,
        refuse: bool = False,
        held: int = 1,
        autocommit: bool = True,
        release_error: str | None = None,
    ) -> None:
        self.statements: list[str] = []
        self.installed = installed
        self.refuse = refuse
        self.held = held
        self.release_error = release_error
        self.attr: dict[str, Any] = {"autocommit": autocommit}

    def execute(self, query: str) -> Result:
        self.statements.append(query)
        if query.startswith("SELECT COUNT(*) FROM EXA_ALL_SCRIPTS"):
            return Result([(1 if self.installed else 0,)])
        if query.startswith(f"EXECUTE SCRIPT {SCRIPT}('acquire'"):
            if self.refuse:
                raise FakeQueryError(
                    REFUSAL + ' caught in script "TEST_CFB_INFRA"."RUN_LOCK" at line 16 '
                    "(Session: 456)"
                )
            return Result([(42,)])
        if query.startswith(f"EXECUTE SCRIPT {SCRIPT}('release'"):
            if self.release_error:
                raise FakeQueryError(self.release_error)
            return Result([(self.held,)])
        return Result([])

    def locking(self) -> list[str]:
        return [s for s in self.statements if s.startswith("EXECUTE SCRIPT") or s == "ROLLBACK"]


def test_the_lock_is_taken_and_released_through_the_run_lock_script() -> None:
    connection = FakeConnection()
    with run_lock(connection, TEST, "RESTORE", "week7 FORCE") as lock:
        assert (lock.run_id, lock.kind) == (42, "RESTORE")
        lock.detail = "restored week7: 3 tables"
    assert connection.locking() == [
        f"EXECUTE SCRIPT {SCRIPT}('acquire', 'RESTORE', NULL, 'week7 FORCE')",
        f"EXECUTE SCRIPT {SCRIPT}('release', 'DONE', 42, 'restored week7: 3 tables')",
    ]


def test_a_release_without_a_detail_sends_null() -> None:
    connection = FakeConnection()
    with run_lock(connection, TEST, "DEPLOY", "make sim"):
        pass
    assert connection.locking()[-1] == f"EXECUTE SCRIPT {SCRIPT}('release', 'DONE', 42, NULL)"


def test_a_failing_block_rolls_back_then_marks_the_run_failed_and_reraises() -> None:
    connection = FakeConnection()
    with (
        pytest.raises(ValueError, match="landing failed"),
        run_lock(connection, TEST, "RESTORE", "week7"),
    ):
        raise ValueError("landing failed\nsecond line")
    assert connection.locking()[1:] == [
        "ROLLBACK",
        f"EXECUTE SCRIPT {SCRIPT}('release', 'FAILED', 42, 'landing failed')",
    ]


def test_an_interrupt_also_ends_the_run_failed() -> None:
    connection = FakeConnection()
    with pytest.raises(KeyboardInterrupt), run_lock(connection, TEST, "SNAPSHOT", "week7"):
        raise KeyboardInterrupt
    assert connection.locking()[-1] == (
        f"EXECUTE SCRIPT {SCRIPT}('release', 'FAILED', 42, 'KeyboardInterrupt')"
    )


def test_a_refused_lock_names_the_holder_and_runs_nothing() -> None:
    connection = FakeConnection(refuse=True)
    ran = []
    with pytest.raises(RunLockRefused) as refused, run_lock(connection, TEST, "RESTORE", "week7"):
        ran.append(True)
    assert str(refused.value) == REFUSAL
    assert ran == []
    assert [s for s in connection.statements if "'release'" in s] == []


def test_a_release_error_does_not_hide_the_original_error() -> None:
    connection = FakeConnection(release_error="connection lost")
    with (
        pytest.raises(ValueError, match="boom") as raised,
        run_lock(connection, TEST, "RESTORE", "week7"),
    ):
        raise ValueError("boom")
    notes = getattr(raised.value, "__notes__", [])
    assert any("run 42 could not be marked FAILED" in note for note in notes)


def test_a_row_that_was_no_longer_running_at_the_end_is_an_error() -> None:
    connection = FakeConnection(held=0)
    with (
        pytest.raises(RunLockError, match="run 42 was no longer RUNNING"),
        run_lock(connection, TEST, "DEPLOY", "model deploy"),
    ):
        pass


@pytest.mark.parametrize("kind", ["BACKFILL", "REFRESH_WEEK", "SIMULATE", "restore", ""])
def test_only_host_kinds_are_accepted(kind: str) -> None:
    connection = FakeConnection()
    with (
        pytest.raises(RunLockError, match="DEPLOY, RESTORE, SNAPSHOT"),
        run_lock(connection, TEST, kind, "x"),
    ):
        pass
    assert connection.statements == []


def test_a_connection_without_autocommit_is_refused_before_anything_is_sent() -> None:
    connection = FakeConnection(autocommit=False)
    with (
        pytest.raises(RunLockError, match="autocommit"),
        run_lock(connection, TEST, "RESTORE", "week7"),
    ):
        pass
    assert connection.statements == []


def test_a_second_lock_on_the_same_connection_is_refused_and_the_first_still_ends() -> None:
    connection = FakeConnection()
    with (
        pytest.raises(RunLockError, match="already holds the run lock"),
        run_lock(connection, TEST, "RESTORE", "outer"),
        run_lock(connection, TEST, "DEPLOY", "inner"),
    ):
        pass
    acquires = [s for s in connection.statements if "'acquire'" in s]
    assert len(acquires) == 1
    assert connection.locking()[-1].startswith(f"EXECUTE SCRIPT {SCRIPT}('release', 'FAILED', 42")
    with run_lock(connection, TEST, "DEPLOY", "again"):
        pass


def test_a_missing_script_says_to_deploy() -> None:
    connection = FakeConnection(installed=False)
    with (
        pytest.raises(RunLockError, match="make deploy"),
        run_lock(connection, TEST, "SNAPSHOT", "week7"),
    ):
        pass
    assert not [s for s in connection.statements if s.startswith("EXECUTE SCRIPT")]


def test_texts_are_single_line_literals() -> None:
    connection = FakeConnection()
    with run_lock(connection, TEST, "RESTORE", "it's\nweek7") as lock:
        lock.detail = "two\nlines  here"
    assert connection.locking() == [
        f"EXECUTE SCRIPT {SCRIPT}('acquire', 'RESTORE', NULL, 'it''s week7')",
        f"EXECUTE SCRIPT {SCRIPT}('release', 'DONE', 42, 'two lines here')",
    ]


def test_the_host_kinds_are_the_host_rule_kinds_of_the_library() -> None:
    assert runlock.HOST_KINDS == ("DEPLOY", "RESTORE", "SNAPSHOT")
