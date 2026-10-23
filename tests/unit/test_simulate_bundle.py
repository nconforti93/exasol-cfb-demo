"""The `SIMULATE` Lua script as `engine/deploy.py` bundles it, and the engine-input view it reads
(sim-engine/sim-runs, sim-engine/engine-contract). The library runs in embedded Lua 5.4 (`lupa`)
without a database: only the argument checks, which read nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lupa.lua54 as LUA
import pytest

from engine import core, deploy
from vs.host import sqlobjects
from vs.host.namespace import Namespace

SIM_DIR = Path(deploy.SIM_DIR)
CALL = "SIMULATE needs"


def lua_body(prefix: str) -> str:
    text = deploy.simulate_script(Namespace(prefix))
    return text.split(" AS\n", 1)[1].rsplit("\n/", 1)[0]


def library_runtime() -> Any:
    """Lua with the bundle up to the entry file, so the library's functions are callable."""
    body = lua_body("TEST_")
    entry = (deploy.LUA_DIR / deploy.SIMULATE_ENTRY).read_text(encoding="utf-8")
    lua = LUA.LuaRuntime(unpack_returned_tuples=True)
    lua.execute("null = setmetatable({}, {__tostring = function() return 'null' end})")
    lua.execute(body.replace(entry.rstrip("\n"), ""))
    return lua


@pytest.mark.parametrize("prefix", ["", "TEST_"])
def test_the_script_compiles_and_names_its_namespace(prefix: str) -> None:
    text = deploy.simulate_script(Namespace(prefix))
    assert text.startswith(
        f"CREATE OR REPLACE LUA SCRIPT {prefix}CFB_INFRA.SIMULATE(season, week, season_type, "
        "n_sims, sims_per_batch, run_seed, run_label) RETURNS TABLE AS\n"
    )
    assert f'SIM_SCHEMA = "\\"{prefix}CFB_SIM\\""' in text
    lua = LUA.LuaRuntime(unpack_returned_tuples=True)
    check = lua.eval(
        "function(src) local f, e = load(src); if f then return 'ok' end; return e end"
    )
    assert check(lua_body(prefix)) == "ok"


def test_the_prelude_is_the_engines_input_and_rule_codes() -> None:
    lua = LUA.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(deploy.render_prelude().replace("local ", ""))
    names = lua.globals().MODEL_COLUMN_NAMES
    assert [names[i + 1] for i in range(len(names))] == [n for n, _ in core.MODEL_COLUMNS]
    assert dict(lua.globals().AUTO_BID_CODES) == {
        "TOP_CHAMPIONS": core.AUTO_BID_TOP_CHAMPIONS,
        "POWER_CHAMPIONS_PLUS_G6": core.AUTO_BID_POWER_CHAMPIONS_PLUS_G6,
    }
    assert dict(lua.globals().G6_BID_CODES) == {
        "CHAMPION": core.G6_BID_CHAMPION,
        "TEAM": core.G6_BID_TEAM,
    }


def test_the_model_rows_view_is_the_engine_input_after_the_run_id() -> None:
    (view,) = [o for o in sqlobjects.load(SIM_DIR) if o.name == "SIM_RUN_MODEL_ROWS"]
    assert [c.name for c in view.columns] == ["RUN_ID", *(n for n, _ in core.MODEL_COLUMNS)]
    text = (SIM_DIR / "230_sim_run_model_rows.sql").read_text(encoding="utf-8")
    for _, sql_type in core.MODEL_COLUMNS:
        if sql_type != "BOOLEAN":
            assert f"AS {sql_type})" in text, sql_type


@pytest.mark.parametrize(
    ("n_sims", "batch", "message"),
    [
        (2000, 300, "not a positive multiple"),
        (2000, 99, "outside 100 to 20000"),
        (2000, 20001, "outside 100 to 20000"),
        (10001 * 100, 100, "more than 10000"),
    ],
)
def test_batch_sizes_outside_the_rules_are_refused(n_sims: int, batch: int, message: str) -> None:
    lua = library_runtime()
    given = lua.table_from({"season": 2026, "week": 9, "n_sims": n_sims, "sims_per_batch": batch})
    with pytest.raises(LUA.LuaError, match=message):
        lua.globals().sim_arguments(given)


def test_arguments_take_their_defaults() -> None:
    lua = library_runtime()
    args = lua.globals().sim_arguments(lua.table_from({"season": 2026, "week": 7}))
    assert (args.season_type, args.n_sims, args.sims_per_batch, args.n_batches) == (
        "regular",
        100000,
        5000,
        20,
    )
    assert args.run_seed is None and args.run_label is None
    with pytest.raises(LUA.LuaError, match=CALL):
        lua.globals().sim_arguments(lua.table_from({"season": 2026}))
