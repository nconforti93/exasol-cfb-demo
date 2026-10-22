"""`tests/it/step_scripts.py`: the registered-step test scripts, rendered without a database."""

import re

import pytest

from tests.it import step_scripts

SCHEMA = "TEST_CFB_INFRA"


def statements() -> list[str]:
    return step_scripts.step_scripts_sql(SCHEMA)


def named(fragment: str) -> str:
    (found,) = [s for s in statements() if fragment in s.split("\n", 1)[0]]
    return found


def test_the_step_script_names_are_the_ones_the_plan_lists() -> None:
    assert step_scripts.RECORD_SCRIPT == "TEST_STEP_RECORD"
    assert step_scripts.FAIL_SCRIPT == "TEST_STEP_FAIL"
    assert step_scripts.SLEEP_SCRIPT == "TEST_STEP_SLEEP"
    assert step_scripts.STEP_SCRIPT_NAMES == (
        "TEST_STEP_RECORD",
        "TEST_STEP_FAIL",
        "TEST_STEP_SLEEP",
    )


def test_the_statements_come_in_dependency_order() -> None:
    kinds = [s.split("\n", 1)[0] for s in statements()]
    assert kinds[0].startswith(f"CREATE TABLE IF NOT EXISTS {SCHEMA}.TEST_STEP_CALLS")
    assert kinds[1].startswith(f"CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {SCHEMA}.TEST_SLEEP(")
    assert [k.split("(")[0] for k in kinds[2:]] == [
        f"CREATE OR REPLACE LUA SCRIPT {SCHEMA}.{name}" for name in step_scripts.STEP_SCRIPT_NAMES
    ]


@pytest.mark.parametrize("name", ["TEST_STEP_RECORD", "TEST_STEP_FAIL", "TEST_STEP_SLEEP"])
def test_each_step_has_the_registered_step_contract(name: str) -> None:
    script = named(name)
    header = script.split("\n", 1)[0]
    assert header.endswith("(run_id, season, week, season_type) RETURNS ROWCOUNT AS")
    assert script.endswith("\n/")


def test_the_record_step_inserts_its_arguments_in_call_order() -> None:
    script = named("TEST_STEP_RECORD")
    assert f'INSERT INTO "{SCHEMA}"."TEST_STEP_CALLS"' in script
    for argument in ("run_id", "season", "week", "season_type"):
        assert argument in script.split("\n", 1)[1]
    assert "'TEST_STEP_RECORD'" in script
    table = statements()[0]
    assert "CALL_NO DECIMAL(18,0) IDENTITY NOT NULL" in table
    assert "SEASON_TYPE VARCHAR(16) UTF8" in table


def test_the_record_step_passes_a_missing_season_type_as_null() -> None:
    assert "season_type or null" in named("TEST_STEP_RECORD")


def test_the_fail_step_raises_an_error_that_names_itself_and_the_run() -> None:
    script = named("TEST_STEP_FAIL")
    assert re.search(r'error\("TEST_STEP_FAIL: .*" \.\. tostring\(run_id\)\)', script)


def test_the_sleep_step_calls_the_python_sleep_udf_for_five_seconds_by_default() -> None:
    assert step_scripts.SLEEP_SECONDS == 5
    script = named("TEST_STEP_SLEEP")
    assert f'"{SCHEMA}"."TEST_SLEEP"' in script
    assert "n = 5" in script
    udf = named("TEST_SLEEP(")
    assert "time.sleep(ctx.SECONDS)" in udf
    assert "RETURNS DOUBLE" in udf


def test_the_sleep_duration_can_be_changed() -> None:
    script = step_scripts.step_scripts_sql(SCHEMA, sleep_seconds=2)[-1]
    assert "n = 2" in script


@pytest.mark.parametrize("schema", ["", "test_cfb_infra", "A; DROP", '"X"', "1ABC"])
def test_an_unsafe_schema_name_is_rejected(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        step_scripts.step_scripts_sql(schema)


def test_no_step_touches_the_virtual_schema() -> None:
    assert "VIRTUAL SCHEMA" not in "\n".join(statements())


def test_the_lua_bodies_compile() -> None:
    lupa = pytest.importorskip("lupa")
    runtime = lupa.LuaRuntime()
    for name in step_scripts.STEP_SCRIPT_NAMES:
        body = named(name).split(" AS\n", 1)[1].removesuffix("\n/")
        compiled = runtime.eval("load")(body)
        assert callable(compiled), (name, compiled)
