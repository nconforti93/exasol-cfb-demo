"""`model/lua/refresh_model.lua`, bundled for the `TEST_` namespace, in embedded Lua 5.4 (lupa).

`pquery` is the stub of `test_loadlib_lua.py`; `exit` records the result. The tests pin the order
of the statements: the lock (only on its own), the rebuild of every derived table in file order and
its commit before the point and the checks, the logged checks committed before a failed ERROR check
raises, and no check at all for a rebuild-only call (decisions [15], [40]).
"""

from __future__ import annotations

import re
from typing import Any

import lupa.lua54 as LUA
import pytest

from model import step
from model.deploy import prepare
from tests.unit.test_loadlib_lua import STUBS, TRACEBACK, lua_text
from vs.bundle import SCRIPT_TERMINATOR, script_body
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")
OBJECTS = prepare(TEST)
DERIVED = [obj.name for obj in step.derived_tables(OBJECTS)]
BODY = script_body(step.build_step_script(TEST, OBJECTS)).removesuffix(SCRIPT_TERMINATOR)
EXIT_STUB = "function exit(rows, columns) EXITED = {rows = rows, columns = columns} end\n"
CHECK_ROWS = (
    "{{CHECK_NAME = 'GAME_WITHOUT_WEEK', SEVERITY = 'ERROR', SEASON = 2024, WEEK_SEQ = null, "
    "CHECK_STATUS = '{status}', OBSERVED = {observed}, DETAIL = 'games: 1'}}, "
    "{{CHECK_NAME = 'WEEK_NOT_COMPLETE', SEVERITY = 'WARNING', SEASON = 2024, WEEK_SEQ = 9, "
    "CHECK_STATUS = 'PASS', OBSERVED = 0, DETAIL = null}}"
)


class Step:
    """One call of the bundled script with its four arguments over the `pquery` stub."""

    def __init__(
        self,
        run_id: str,
        season: str,
        week: str,
        season_type: str,
        *,
        point: str = "{{AS_OF_WEEK_SEQ = 9}}",
    ) -> None:
        self.lua = LUA.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUBS + EXIT_STUB)
        self.lua.execute(
            f"run_id, season, week, season_type = {run_id}, {season}, {week}, {season_type}"
        )
        self.respond('SELECT MAX("RUN_ID")', "{{RUN_ID = 42}}")
        self.respond('SELECT COUNT(*) AS "RUNS"', "{{RUNS = 1}}")
        self.respond('FROM "TEST_CFB_MODEL"."AS_OF_POINT"', point)

    def respond(self, pattern: str, result: str) -> None:
        self.lua.execute(f"respond({lua_text(pattern)}, true, {result}, nil)")

    def checks(self, *, status: str = "PASS", observed: int = 0) -> None:
        rows = CHECK_ROWS.format(status=status, observed=observed)
        self.respond('FROM "TEST_CFB_MODEL"."MODEL_CHECKS"', "{" + rows + "}")

    def run(self) -> Any:
        return self.lua.execute(BODY)

    def error(self) -> str:
        with pytest.raises(LUA.LuaError) as raised:
            self.run()
        return str(raised.value).split(TRACEBACK, 1)[0]

    def statements(self) -> list[str]:
        return [str(entry["sql"]) for entry in self.lua.globals().STATEMENTS.values()]

    def binds(self, index: int) -> dict[str, Any]:
        return dict(self.lua.globals().STATEMENTS[index + 1]["binds"].items())

    def exited(self) -> list[tuple[Any, ...]]:
        rows = self.lua.globals().EXITED["rows"]
        return [tuple(row.values()) for row in rows.values()]


_TARGET = re.compile(
    r'(?:TRUNCATE\s+TABLE|INSERT\s+INTO|FROM|UPDATE)\s+"?TEST_CFB_(?:META|MODEL)"?\."?([A-Z_]+)'
)


def verbs(statements: list[str]) -> list[str]:
    """Each statement as `<VERB> <first meta or model table it names>`."""
    named = []
    for sql in statements:
        verb = sql.split(None, 1)[0].upper()
        table = _TARGET.search(sql)
        named.append(verb if table is None else f"{verb} {table[1]}")
    return named


def rebuild_verbs() -> list[str]:
    return [verb for name in DERIVED for verb in (f"TRUNCATE {name}", f"INSERT {name}")]


def test_the_model_has_derived_tables_in_dependency_order() -> None:
    assert DERIVED == [
        "GAME",
        "SEASON_WEEK",
        "TEAM",
        "RATING_SP",
        "RANKING",
        "TEAM_MEMBERSHIP",
        "TEAM_SEASON",
        "AS_OF_POINT",
    ]
    assert "local DERIVED_TABLES = {" in BODY
    assert "TEST_CFB_MODEL.GAME" not in BODY.split("local DERIVED_TABLES", 1)[0]


def test_on_its_own_it_locks_rebuilds_commits_then_logs_the_checks() -> None:
    call = Step("nil", "2024", "9", "'regular'")
    call.checks()
    call.run()
    done = verbs(call.statements())
    lock_end = done.index("INSERT LOAD_RUN") + 3
    assert done[: done.index("INSERT LOAD_RUN")][:2] == ["COMMIT", "SELECT LOAD_RUN"]
    assert done[lock_end : lock_end + len(DERIVED) * 2] == rebuild_verbs()
    after = done[lock_end + len(DERIVED) * 2 :]
    assert after[:5] == [
        "COMMIT",
        "SELECT AS_OF_POINT",
        "SELECT MODEL_CHECKS",
        "INSERT MODEL_CHECK_LOG",
        "COMMIT",
    ]
    assert after[-2:] == ["UPDATE LOAD_RUN", "COMMIT"]
    release = call.binds(len(done) - 2)
    assert release["status"] == "DONE"
    assert "no ERROR failed" in release["detail"]
    log = call.binds(done.index("INSERT MODEL_CHECK_LOG"))
    assert str(log["run"]) == "null" and log["season"] == 2024 and log["seq"] == 9
    assert (log["c1_name"], str(log["c1_week"]), log["c2_week"]) == ("GAME_WITHOUT_WEEK", "null", 9)
    kinds = [row[0] for row in call.exited()]
    assert kinds == ["TABLE"] * len(DERIVED) + ["CHECK", "CHECK"]


def test_a_registered_step_takes_no_lock_and_names_the_failed_error_check() -> None:
    call = Step("7", "2024", "9", "nil")
    call.checks(status="FAIL", observed=3)
    message = call.error()
    assert "MODEL: 1 ERROR check(s) failed for season 2024, regular week 9" in message
    assert "GAME_WITHOUT_WEEK (3)" in message
    done = verbs(call.statements())
    assert done[0] == "SELECT LOAD_RUN"
    assert "INSERT LOAD_RUN" not in done and "UPDATE LOAD_RUN" not in done
    assert done[1 : 1 + len(DERIVED) * 2] == rebuild_verbs()
    assert done[-3:] == ["INSERT MODEL_CHECK_LOG", "COMMIT", "ROLLBACK"]
    assert call.binds(done.index("INSERT MODEL_CHECK_LOG"))["run"] == 7


def test_a_null_season_rebuilds_without_checks() -> None:
    call = Step("nil", "nil", "nil", "nil")
    call.run()
    done = verbs(call.statements())
    assert not {"SELECT AS_OF_POINT", "SELECT MODEL_CHECKS", "INSERT MODEL_CHECK_LOG"} & set(done)
    assert rebuild_verbs()[-1] in done
    assert [row[1] for row in call.exited()] == DERIVED


def test_an_unknown_point_fails_after_the_rebuild_and_logs_no_check() -> None:
    call = Step("nil", "2024", "23", "'regular'", point="{}")
    message = call.error()
    assert "season 2024, regular week 23 is unknown" in message
    done = verbs(call.statements())
    assert "SELECT MODEL_CHECKS" not in done and "INSERT MODEL_CHECK_LOG" not in done
    failed = [i for i, verb in enumerate(done) if verb == "UPDATE LOAD_RUN"]
    assert any(call.binds(i).get("status") == "FAILED" for i in failed)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("nil", "nil", "9", "nil"), "week and season_type must then be NULL"),
        (("nil", "2024", "nil", "nil"), "week is required with a season"),
        (("nil", "2024", "9", "'spring'"), "season_type 'spring' is unknown"),
    ],
)
def test_bad_arguments_fail_before_any_statement(
    arguments: tuple[str, str, str, str], expected: str
) -> None:
    call = Step(*arguments)
    assert expected in call.error()
    assert call.statements() == []
