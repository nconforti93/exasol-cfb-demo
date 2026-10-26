"""The prediction entry and its host command use the same deployed name."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from lupa import LuaRuntime

from engine import deploy
from scripts.sim import predict_call
from vs.host.namespace import Namespace as DbNamespace


def test_prediction_entry_bundles_freeze_and_uses_the_current_point() -> None:
    script = deploy.predict_script(DbNamespace("TEST_"))
    assert "CREATE OR REPLACE LUA SCRIPT" in script
    assert "TEST_CFB_INFRA" in script
    assert "function freeze_predictions(season, through_seq, frozen_by)" in script
    assert '"CURRENT_AS_OF"' in script
    assert '"GAME_SCORE_AS_OF"' in script
    assert 'SELECT CAST("NOW_UTC" AS VARCHAR(30)) AS "NOW_TEXT"' in script


def test_predict_command_quotes_the_season_type() -> None:
    sql = predict_call(DbNamespace("TEST_"), Namespace(season=2026, week=9, season_type="regular"))
    assert sql == 'EXECUTE SCRIPT "TEST_CFB_INFRA"."PREDICT"(2026, 9, \'regular\')'


def test_each_historical_point_uses_a_fresh_freeze_clock() -> None:
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(
        """
        QUOTED = {MODEL_SCHEMA='"TEST_CFB_MODEL"', SIM_SCHEMA='"TEST_CFB_SIM"',
                  META_SCHEMA='"TEST_CFB_META"'}
        PLAIN = {SIM_SCHEMA='TEST_CFB_SIM'}
        captured = {}
        clock_reads = 0
        function nz(value) return value end
        function sql_value(value) return value end
        function whole_number(value) return tonumber(value) end
        function commit() end
        function run_sql(statement, parameters)
          if statement:find('CURRENT_AS_OF', 1, true) then
            return {{AS_OF_SEASON=2026}}
          elseif statement:find('AS_OF_POINT', 1, true) then
            return {{AS_OF_WEEK_SEQ=7, NEXT_WEEK_SEQ=8, AS_OF_LABEL='after week 7'},
                    {AS_OF_WEEK_SEQ=8, NEXT_WEEK_SEQ=9, AS_OF_LABEL='after week 8'}}
          elseif statement:find('COUNT(*)', 1, true) then
            return {{N=1}}
          elseif statement:find('NOW_UTC', 1, true) then
            clock_reads = clock_reads + 1
            local moment = clock_reads == 1 and '2026-10-27 22:59:59'
              or '2026-10-27 23:00:01'
            return {{NOW_TEXT=moment}}
          elseif statement:find('INSERT INTO', 1, true) then
            captured[#captured + 1] = parameters.frozen_at
            assert(statement:find('CAST(:frozen_at AS TIMESTAMP)', 1, true))
            return {rows_inserted=1}
          end
          error('unexpected SQL: ' .. statement)
        end
        """
    )
    source = Path(deploy.LUA_DIR / "simlib.lua").read_text(encoding="utf-8")
    lua.execute(source)
    assert lua.eval("freeze_predictions")(2026, 8, "PREDICT") == 2
    assert [lua.globals().captured[index] for index in (1, 2)] == [
        "2026-10-27 22:59:59",
        "2026-10-27 23:00:01",
    ]
