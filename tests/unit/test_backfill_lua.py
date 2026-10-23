"""`loader/lua/backfill.lua` in an embedded Lua 5.4 (lupa) over a simulated database.

The scenarios run on the engine in `tests/it/test_backfill_it.py`. These tests pin the batch loop
where the engine is slow or cannot be steered: the stop rule after consecutive failed batches, the
worst-case batch size near the end of the allowance, and the measured spend of a batch that raises
after its fetch.

The simulation (`SIMULATION`) answers the statements of one run: a `GAMES` queue of season slices
(`/games?year=<y>`), an `/info` whose used calls grow by `ATTEMPTS` per fetched slice, scripted
fetch failures per year, and a transaction model in which `LOAD_LOG` and `LOAD_FAILURES` writes
count only once committed.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import lupa.lua54 as LUA
import pytest

from loader import bundle
from tests.unit.test_loadlib_lua import lua_text
from vs.cfbd_vs import http
from vs.host.namespace import Namespace

TEST = Namespace("TEST_")
SIMULATION = r"""
null = setmetatable({}, {__tostring = function() return "null" end})
STATEMENTS = {}
USED = 0
BUDGET = 850
ATTEMPTS = 1
PER_QUERY = 10
MODE = 'live'
QUEUE = {}
FAIL_YEARS = {}
RAISE = {}
STORED, FAILED, FETCHED = {}, {}, {}
local pending_store, pending_fail, staged = {}, {}, {}
QUOTA_DOWN_AFTER = nil
local quota_reads = 0

local function years_of(sql)
  local list = sql:match('"P_YEAR" IN %(([^)]*)%)')
  local years = {}
  for year in (list or ""):gmatch("%d+") do years[#years + 1] = tonumber(year) end
  return years
end

local function key(year) return "/games?year=" .. year end

local function queue(limit)
  local rows = {}
  for _, row in ipairs(QUEUE) do
    if not STORED[row.SEASON] and not FAILED[row.SEASON] then
      rows[#rows + 1] = {ENTRY_ID = 1, TABLE_NAME = 'GAMES', SLICING = 'season',
        SEASON = row.SEASON, SLICE_KEY = key(row.SEASON), SEASON_FROM = 1869, SEASON_TO = null,
        FIXED_FILTER = null, BATCH_ALONE = row.ALONE}
      if limit and #rows >= limit then break end
    end
  end
  return rows
end

function exit(rows, columns) EXITED = {rows = rows, columns = columns} end

function pquery(sql, binds)
  STATEMENTS[#STATEMENTS + 1] = {sql = sql, binds = binds or {}}
  for pattern, failure in pairs(RAISE) do
    if sql:find(pattern, 1, true) then
      return false, {error_code = failure[1], error_message = failure[2]}
    end
  end
  if sql == "COMMIT" then
    for year in pairs(pending_store) do STORED[year] = true end
    for year in pairs(pending_fail) do FAILED[year] = true end
    pending_store, pending_fail = {}, {}
    return true, {rows_affected = 0}
  elseif sql == "ROLLBACK" then
    pending_store, pending_fail, staged = {}, {}, {}
    return true, {rows_affected = 0}
  elseif sql:find('"LOCK_STATE"', 1, true) then
    return true, {}
  elseif sql:find('SELECT MAX("RUN_ID")', 1, true) then
    return true, {{RUN_ID = 7}}
  elseif sql:find('EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES', 1, true) then
    return true, {{PROPERTY_NAME = 'FETCH_MODE', PROPERTY_VALUE = MODE},
                  {PROPERTY_NAME = 'MAX_CALLS_PER_QUERY', PROPERTY_VALUE = tostring(PER_QUERY)}}
  elseif sql:find('"BACKFILL_SPENDABLE"', 1, true) then
    quota_reads = quota_reads + 1
    if QUOTA_DOWN_AFTER ~= nil and quota_reads > QUOTA_DOWN_AFTER then
      return false, {error_code = '22002', error_message = 'CFBD_VS.INFO: HTTP 503'}
    end
    return true, {{USED_CALLS = USED, REMAINING_CALLS = 1000 - USED,
      REFRESH_SPENDABLE = math.max(0, BUDGET + 150 - USED),
      BACKFILL_SPENDABLE = math.max(0, BUDGET - USED)}}
  elseif sql:find('"BACKFILL_QUEUE"', 1, true) then
    return true, queue(tonumber(sql:match("LIMIT (%d+)")))
  elseif sql:find('EXPLAIN VIRTUAL', 1, true) then
    local keys = {}
    for i, year in ipairs(years_of(sql)) do keys[i] = '"' .. key(year) .. '"' end
    return true, {{PUSHDOWN_SQL = '/*CFBD {"fetch_mode":"' .. MODE
      .. '","planned_requests":' .. #keys
      .. ',"slice_keys":[' .. table.concat(keys, ",") .. ']}*/ SELECT 1'}}
  elseif sql:find('INSERT INTO "TEST_CFB_STAGE"', 1, true) then
    local years = years_of(sql)
    USED = USED + #years * ATTEMPTS
    FETCHED[#FETCHED + 1] = years
    for _, year in ipairs(years) do
      if FAIL_YEARS[year] then
        return false, {error_code = '22002',
          error_message = 'VM error\nCFBD_VS.GAMES: HTTP 500 for ' .. key(year) .. '\nmore'}
      end
    end
    staged = years
    return true, {rows_affected = #years * 10}
  elseif sql:find('"REQ_SLICE" NOT IN', 1, true) then
    return true, {{N = 0}}
  elseif sql:find('SELECT COUNT(*) AS "N" FROM "TEST_CFB_STAGE"', 1, true) then
    return true, {{N = 0}}
  elseif sql:find('MERGE INTO "TEST_CFB_META"."LOAD_LOG"', 1, true) then
    for _, year in ipairs(staged) do pending_store[year] = true end
    return true, {rows_affected = #staged}
  elseif sql:find('INSERT INTO "TEST_CFB_META"."FETCH_LOG"', 1, true) then
    return true, {rows_affected = #staged}
  elseif sql:find('INSERT INTO "TEST_CFB_CORE"', 1, true) then
    return true, {rows_affected = #staged * 10}
  elseif sql:find('MERGE INTO "TEST_CFB_META"."LOAD_FAILURES"', 1, true) then
    pending_fail[tonumber(binds.k:match("year=(%d+)"))] = true
    return true, {rows_affected = 1}
  elseif sql:find('"IS_PENDING"', 1, true) or sql:find('SELECT O."TABLE_NAME"', 1, true) then
    return true, {}
  elseif sql:find('INSERT INTO "TEST_CFB_META"."LOAD_LOG"', 1, true) then
    return true, {rows_affected = 0}
  end
  return true, {rows_affected = 1}
end
"""
TOTALS = 'SET "SLICES" = :slices'
STEP_END = 'SET "STEP_STATUS" = :status'
STEP_CALLS = '"CALLS" = COALESCE("CALLS", 0) + :calls'


class Backfill:
    """The bundled `BACKFILL` body over `SIMULATION`; `run` executes it once."""

    def __init__(self) -> None:
        self.lua = LUA.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(SIMULATION)
        lua_dir = Path(bundle.LUA_DIR)
        self.body = "\n".join(
            [
                bundle.render_constants(TEST),
                (lua_dir / bundle.LIBRARY_FILE).read_text(encoding="utf-8"),
                (lua_dir / "backfill.lua").read_text(encoding="utf-8"),
            ]
        )

    def setup(
        self,
        years: Iterable[int],
        *,
        fail: Iterable[int] = (),
        alone: Iterable[int] = (),
        per_query: int = 10,
        attempts: int = 1,
        mode: str = "live",
    ) -> None:
        queued = list(years)
        later = list(alone)
        rows = [f"{{SEASON = {y}, ALONE = false}}" for y in queued if y not in later]
        rows += [f"{{SEASON = {y}, ALONE = true}}" for y in later]
        self.lua.execute(f"QUEUE = {{{', '.join(rows)}}}")
        for year in fail:
            self.lua.execute(f"FAIL_YEARS[{year}] = true")
        self.lua.execute(f"PER_QUERY = {per_query}; ATTEMPTS = {attempts}; MODE = '{mode}'")

    def raise_on(self, fragment: str, code: str, message: str) -> None:
        self.lua.execute(
            f"RAISE[ {lua_text(fragment)} ] = {{{lua_text(code)}, {lua_text(message)}}}"
        )

    def run(self, max_calls: int | None = None) -> str | None:
        """Run the script; the error it raises, or None when it exited with its result."""
        limit = "null" if max_calls is None else str(max_calls)
        self.lua.execute(f"run_profile = 'demo'; from_season = null; max_calls = {limit}")
        try:
            self.lua.execute(self.body)
        except LUA.LuaError as error:
            return str(error).split("\nstack traceback:", 1)[0]
        return None

    def total(self) -> dict[str, Any]:
        exited = self.lua.globals().EXITED
        names = [part.split()[0] for part in str(exited.columns).split(", ")]
        last = list(exited.rows.values())[-1]
        return dict(zip(names, list(last.values()), strict=True))

    def fetched(self) -> list[list[int]]:
        batches = self.lua.globals().FETCHED.values()
        return [[int(year) for year in batch.values()] for batch in batches]

    def years(self, name: str) -> set[int]:
        return {int(year) for year, flag in self.lua.globals()[name].items() if flag}

    def used(self) -> int:
        return int(self.lua.globals().USED)

    def statements(self) -> list[str]:
        return [str(entry["sql"]) for entry in self.lua.globals().STATEMENTS.values()]

    def last_bound(self, fragment: str, name: str) -> Any:
        entries = list(self.lua.globals().STATEMENTS.values())
        found = [e["binds"][name] for e in entries if fragment in str(e["sql"])]
        assert found, fragment
        return found[-1]

    def step_statuses(self) -> list[str]:
        entries = list(self.lua.globals().STATEMENTS.values())
        return [str(e["binds"]["status"]) for e in entries if STEP_END in str(e["sql"])]


@pytest.fixture
def backfill() -> Backfill:
    return Backfill()


def test_the_fetch_attempt_cap_equals_the_fetch_udf_retry_cap() -> None:
    source = (Path(bundle.LUA_DIR) / "backfill.lua").read_text(encoding="utf-8")
    assert f"local FETCH_ATTEMPTS = {http.MAX_ATTEMPTS}\n" in source


# Consecutive failed batches (review m8) -----------------------------------------------------------


def test_one_failed_single_slice_batch_is_recorded_and_the_run_continues(
    backfill: Backfill,
) -> None:
    backfill.setup([2020, 2019, 2018, 2017], fail=[2019], per_query=1)
    assert backfill.run() is None
    assert backfill.fetched() == [[2020], [2019], [2018], [2017]]
    assert backfill.years("STORED") == {2020, 2018, 2017}
    assert backfill.years("FAILED") == {2019}
    total = backfill.total()
    assert (total["RUN_STATUS"], total["FAILED_SLICES"]) == ("DONE_WITH_FAILURES", 1)


def test_two_consecutive_failed_single_slice_batches_stop_the_run(backfill: Backfill) -> None:
    backfill.setup([2020, 2019, 2018, 2017], fail=[2019, 2018], per_query=1)
    assert backfill.run() is None
    assert backfill.fetched() == [[2020], [2019], [2018]]
    total = backfill.total()
    assert total["RUN_STATUS"] == "FAILED"
    assert "2 consecutive batches failed" in total["DETAIL"]
    assert "HTTP 500 for /games?year=2018" in total["DETAIL"]
    assert backfill.years("FAILED") == {2019, 2018}


def test_an_outage_stops_after_two_batches_and_their_single_slice_retries(
    backfill: Backfill,
) -> None:
    years = list(range(2020, 2014, -1))
    backfill.setup(years, fail=years, per_query=2)
    assert backfill.run() is None
    assert backfill.fetched() == [[2020, 2019], [2020], [2019], [2018, 2017], [2018], [2017]]
    assert backfill.total()["RUN_STATUS"] == "FAILED"
    assert backfill.years("FAILED") == {2020, 2019, 2018, 2017}
    assert backfill.step_statuses() == ["DONE"] + ["FAILED"] * 6


def test_a_batch_with_a_stored_slice_resets_the_count(backfill: Backfill) -> None:
    backfill.setup(list(range(2020, 2014, -1)), fail=[2019, 2018, 2017], per_query=2)
    assert backfill.run() is None
    assert backfill.years("STORED") == {2020, 2016, 2015}
    assert backfill.years("FAILED") == {2019, 2018, 2017}
    total = backfill.total()
    assert (total["RUN_STATUS"], total["FAILED_SLICES"]) == ("DONE_WITH_FAILURES", 3)


def test_slices_that_failed_before_count_as_failed_batches_too(backfill: Backfill) -> None:
    backfill.setup([2020, 2019, 2018], fail=[2019, 2018], alone=[2019, 2018], per_query=10)
    assert backfill.run() is None
    assert backfill.fetched() == [[2020], [2019], [2018]]
    assert backfill.total()["RUN_STATUS"] == "FAILED"


# Worst-case batch size near the end of the allowance (review m2) ----------------------------------


def test_a_live_batch_is_sized_for_every_request_taking_all_attempts(backfill: Backfill) -> None:
    backfill.setup(list(range(2020, 2010, -1)), attempts=http.MAX_ATTEMPTS, per_query=10)
    assert backfill.run(max_calls=10) is None
    assert backfill.fetched() == [[2020, 2019], [2018]]
    assert backfill.used() == 12
    assert backfill.used() - 10 <= http.MAX_ATTEMPTS - 1
    assert backfill.total()["RUN_STATUS"] == "STOPPED_MAX_CALLS"


def test_without_retries_the_allowance_is_spent_exactly(backfill: Backfill) -> None:
    backfill.setup(list(range(2020, 2005, -1)), attempts=1, per_query=10)
    assert backfill.run(max_calls=9) is None
    assert sum(len(batch) for batch in backfill.fetched()) == 9
    assert backfill.used() == 9
    assert max(len(batch) for batch in backfill.fetched()) == 2


def test_replay_batches_are_not_shrunk(backfill: Backfill) -> None:
    backfill.setup(list(range(2020, 2010, -1)), per_query=10, mode="replay")
    assert backfill.run(max_calls=6) is None
    assert backfill.fetched() == [[2020, 2019, 2018, 2017, 2016, 2015]]
    assert backfill.total()["RUN_STATUS"] == "STOPPED_MAX_CALLS"


def test_without_max_calls_the_budget_sizes_the_batch(backfill: Backfill) -> None:
    backfill.lua.execute("BUDGET = 1000 - 150")
    backfill.setup(list(range(2020, 2000, -1)), per_query=10)
    assert backfill.run() is None
    assert [len(batch) for batch in backfill.fetched()] == [10, 10]
    assert backfill.total()["RUN_STATUS"] == "DONE"


# The spend of a batch that raises after its fetch (review m1) -------------------------------------


def test_a_batch_that_raises_after_its_fetch_is_charged_to_the_run(backfill: Backfill) -> None:
    backfill.setup([2020, 2019], attempts=3, per_query=10)
    backfill.raise_on('MERGE INTO "TEST_CFB_META"."LOAD_LOG"', "40001", "Transaction collision")
    message = backfill.run()
    assert message == "Transaction collision"
    assert backfill.used() == 6
    assert backfill.last_bound(TOTALS, "calls") == 6
    assert backfill.last_bound(STEP_CALLS, "calls") == 6
    sql = backfill.statements()
    fetch = next(i for i, s in enumerate(sql) if s.startswith('INSERT INTO "TEST_CFB_STAGE"'))
    after = sql[fetch + 1 :]
    assert after.index("ROLLBACK") < next(
        i for i, s in enumerate(after) if '"BACKFILL_SPENDABLE"' in s
    )
    assert backfill.step_statuses() == ["DONE", "FAILED"]
    assert backfill.last_bound('SET "RUN_STATUS" = :status', "status") == "FAILED"
    assert backfill.years("STORED") == set()


def test_a_killed_batch_is_only_rolled_back(backfill: Backfill) -> None:
    backfill.setup([2020, 2019])
    backfill.raise_on('MERGE INTO "TEST_CFB_META"."LOAD_LOG"', "R0003", "Client requested abort.")
    assert backfill.run() == "Client requested abort."
    sql = backfill.statements()
    merge = next(i for i, s in enumerate(sql) if s.startswith("MERGE INTO"))
    assert sql[merge + 1 :] == ["ROLLBACK"]


def test_an_unmeasurable_spend_charges_the_requests_at_least(backfill: Backfill) -> None:
    backfill.setup([2020, 2019, 2018], fail=[2020, 2019, 2018], attempts=2, per_query=10)
    backfill.lua.execute("QUOTA_DOWN_AFTER = 1")
    message = backfill.run()
    assert message is not None and "quota cannot be read" in message
    assert backfill.used() == 6
    assert backfill.last_bound(TOTALS, "calls") == 3
    assert backfill.last_bound('SET "RUN_STATUS" = :status', "status") == "FAILED"
