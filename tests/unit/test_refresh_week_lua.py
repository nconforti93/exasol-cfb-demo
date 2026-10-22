"""`loader/lua/refresh_week.lua` in an embedded Lua 5.4 (lupa), with `pquery` and `exit` stubbed.

The scenarios run on the engine in `tests/it/test_refresh_week_it.py`. These tests pin what the
engine cannot show: the statements around a registered step that fails or is killed. A failed step
is rolled back before its FAILED row is written (nested scripts share the refresh's transaction),
and after a kill nothing but the rollback is attempted. The stub answers the CHECK reads with a
stored calendar week, no due slice and one registered step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loader import bundle
from tests.unit.test_loadlib_lua import KILLED, STUBS, lua_text, verbs
from vs.host.namespace import Namespace

LUA = pytest.importorskip("lupa.lua54")

TEST = Namespace("TEST_")
STEP_CALL = "EXECUTE SCRIPT"
EXIT_STUB = r"""
function exit(rows, columns)
  EXITED = {rows = rows, columns = columns}
end
"""
CHECK_READS = r"""
respond('SELECT MAX("RUN_ID")', true, {{RUN_ID = 7}})
respond('"LOCK_STATE"', true, {})
respond('AS "WEEK_FOUND"', true,
  {{TYPE_NAME = 'regular', FIRST_WEEK = 1, LAST_WEEK = 15, WEEK_FOUND = 1},
   {TYPE_NAME = 'postseason', FIRST_WEEK = 1, LAST_WEEK = 7, WEEK_FOUND = 1}})
respond('AS "TABLE_PRIORITY"', true, {})
respond('FROM "TEST_CFB_META"."REFRESH_STEPS"', true,
  {{STEP_NO = 100, STEP_NAME = 'MODEL', SCRIPT_NAME = 'TEST_CFB_INFRA.STEP_MODEL'}})
"""


class RefreshWeek:
    """The bundled `REFRESH_WEEK` body over the `pquery` stub, run with the given arguments."""

    def __init__(self) -> None:
        self.lua = LUA.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUBS + EXIT_STUB + CHECK_READS)
        lua_dir = Path(bundle.LUA_DIR)
        self.body = "\n".join(
            [
                bundle.render_constants(TEST),
                (lua_dir / bundle.LIBRARY_FILE).read_text(encoding="utf-8"),
                (lua_dir / "refresh_week.lua").read_text(encoding="utf-8"),
            ]
        )

    def fail_step_with(self, code: str, message: str) -> None:
        self.lua.execute(
            f"respond({lua_text(STEP_CALL)}, false, failure({lua_text(code)}, {lua_text(message)}))"
        )

    def run(self, season_type: str = "null") -> str | None:
        """Run the script; the error message it raises, or None when it exited normally."""
        self.lua.execute(f"season = 2020; week = 7; season_type = {season_type}")
        try:
            self.lua.execute(self.body)
        except LUA.LuaError as error:
            return str(error).split("\nstack traceback:", 1)[0]
        return None

    def statements(self) -> list[str]:
        return [str(entry["sql"]) for entry in self.lua.globals().STATEMENTS.values()]

    def after_step_call(self) -> list[str]:
        sql = self.statements()
        position = next(i for i, text in enumerate(sql) if text.startswith(STEP_CALL))
        return verbs(sql[position + 1 :])

    def step_call(self) -> tuple[str, dict[str, Any]]:
        entries = self.lua.globals().STATEMENTS.values()
        entry = next(e for e in entries if str(e["sql"]).startswith(STEP_CALL))
        return str(entry["sql"]), dict(entry["binds"].items())


@pytest.fixture
def refresh() -> RefreshWeek:
    return RefreshWeek()


def test_a_registered_step_gets_the_run_and_the_normalized_season_type(
    refresh: RefreshWeek,
) -> None:
    assert refresh.run() is None
    sql, binds = refresh.step_call()
    assert sql == (
        'EXECUTE SCRIPT "TEST_CFB_INFRA"."STEP_MODEL"(:run_id, :season, :week, :season_type)'
    )
    assert binds == {"run_id": 7, "season": 2020, "week": 7, "season_type": "regular"}
    assert refresh.after_step_call()[:2] == ["UPDATE REFRESH_LOG", "COMMIT"]


def test_a_failed_step_is_rolled_back_before_its_failure_is_logged(refresh: RefreshWeek) -> None:
    refresh.fail_step_with("43000", "boom caught in script")
    message = refresh.run("'POSTSEASON'")
    assert message == (
        "REFRESH_WEEK run 7 failed in step 100 MODEL (TEST_CFB_INFRA.STEP_MODEL): "
        "boom caught in script"
    )
    assert refresh.step_call()[1]["season_type"] == "postseason"
    assert refresh.after_step_call() == [
        "ROLLBACK",
        "UPDATE REFRESH_LOG",
        "COMMIT",
        "ROLLBACK",
        "UPDATE LOAD_RUN",
        "UPDATE LOAD_RUN",
        "COMMIT",
    ]


def test_a_killed_step_only_rolls_back(refresh: RefreshWeek) -> None:
    refresh.fail_step_with(KILLED, "Client requested execution abort.")
    assert refresh.run() == "Client requested execution abort."
    assert refresh.after_step_call() == ["ROLLBACK"]
