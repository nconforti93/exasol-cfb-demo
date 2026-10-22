"""`loader/lua/run_lock.lua`: the bundled `RUN_LOCK` script in an embedded Lua 5.4 (lupa).

`pquery` and `exit` are stubbed, so these tests pin the dispatch of the entry: which library
function each action calls, with which values, and what the script returns. The lock rules
themselves are pinned in `test_loadlib_lua.py` and on the engine in the integration tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from loader import bundle
from vs.host.namespace import Namespace

LUA = pytest.importorskip("lupa.lua54")

TEST = Namespace("TEST_")
STUBS = r"""
null = setmetatable({}, {__tostring = function() return "null" end})
STATEMENTS = {}
EXITED = nil
function pquery(sql, binds)
  STATEMENTS[#STATEMENTS + 1] = {sql = sql, binds = binds or {}}
  if sql:find('SELECT MAX("RUN_ID")', 1, true) then
    return true, {{RUN_ID = 42}}
  end
  if sql:find('SET "RUN_STATUS" = :status', 1, true) then
    return true, {rows_affected = HELD_ROWS}
  end
  if sql:find('FROM "TEST_CFB_META"."LOAD_RUN" R', 1, true) then
    return true, {}
  end
  return true, {rows_affected = 1}
end
function exit(rows, columns)
  EXITED = {rows = rows, columns = columns}
  error("<exit>", 0)
end
"""


def run_lock_script() -> bundle.LuaScript:
    return next(s for s in bundle.SCRIPTS if s.name == "RUN_LOCK")


class RunLockScript:
    """The bundled `RUN_LOCK` body, called with one set of arguments."""

    def __init__(self, held_rows: int = 1) -> None:
        self.lua = LUA.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUBS)
        self.lua.globals().HELD_ROWS = held_rows
        statement = bundle.build_script(run_lock_script(), TEST)
        self.body = bundle.script_body(statement).removesuffix("\n/")

    def call(self, action: Any, word: Any, run: Any, text: Any) -> str:
        globals_ = self.lua.globals()
        for name, value in zip(
            run_lock_script().parameters, (action, word, run, text), strict=True
        ):
            globals_[name] = self.lua.eval("null") if value is None else value
        ok, message = self.lua.eval("pcall")(self.lua.eval("load")(self.body))
        assert not ok
        return str(message)

    def exited(self) -> tuple[list[list[Any]], str]:
        result = self.lua.globals().EXITED
        rows = [list(row.values()) for row in result.rows.values()]
        return rows, str(result.columns)

    def statements(self) -> list[str]:
        return [str(entry.sql) for entry in self.lua.globals().STATEMENTS.values()]

    def bound(self, fragment: str, name: str) -> Any:
        for entry in self.lua.globals().STATEMENTS.values():
            if fragment in str(entry.sql):
                return entry.binds[name]
        raise AssertionError(f"no statement contains {fragment!r}")


def test_acquire_takes_the_lock_for_the_kind_and_returns_the_run_id() -> None:
    script = RunLockScript()
    assert script.call("acquire", "RESTORE", None, "week7 FORCE") == "<exit>"
    assert script.exited() == ([[42]], "RUN_ID DECIMAL(18,0)")
    assert script.bound("INSERT INTO", "kind") == "RESTORE"
    assert script.bound("INSERT INTO", "args") == "week7 FORCE"


@pytest.mark.parametrize(("held_rows", "held"), [(1, 1), (0, 0)])
def test_release_ends_the_row_with_status_and_detail(held_rows: int, held: int) -> None:
    script = RunLockScript(held_rows)
    assert script.call("release", "DONE", 42, "restored week7") == "<exit>"
    assert script.exited() == ([[held]], "HELD DECIMAL(1,0)")
    assert script.bound('SET "RUN_STATUS" = :status', "status") == "DONE"
    assert script.bound('SET "RUN_STATUS" = :status', "run") == 42
    assert script.bound('SET "RUN_STATUS" = :status', "detail") == "restored week7"
    assert script.statements()[-1] == "COMMIT"


def test_release_without_a_detail_writes_null() -> None:
    script = RunLockScript()
    script.call("release", "FAILED", 7, None)
    detail = script.bound('SET "RUN_STATUS" = :status', "detail")
    assert str(detail) == "null"


def test_an_unknown_action_raises_naming_it_and_sends_nothing() -> None:
    script = RunLockScript()
    message = script.call("steal", "RESTORE", None, "x")
    assert "unknown action steal" in message
    assert script.statements() == []


def test_a_release_without_a_run_id_raises() -> None:
    script = RunLockScript()
    message = script.call("release", "DONE", None, "x")
    assert "run id" in message
    assert script.statements() == []
