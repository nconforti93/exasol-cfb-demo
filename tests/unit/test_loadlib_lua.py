"""`loader/lua/loadlib.lua` in an embedded Lua 5.4 (lupa), with `pquery` stubbed.

No database: the stub records every statement with its binds and answers from scripted responses.
These tests pin what the integration tests cannot see, the order of the statements on the failure
and lock paths:

- a failure rolls back before anything else, then logs and commits, then re-raises the message;
- after a kill (`R0003`) nothing but the rollback is attempted;
- the lock refuses a live holder without writing, retries a transaction collision, abandons only
  stale rows, and applies the idle rule to script kinds only;
- the batch transaction, the batch filters, the EXPLAIN check and the measured spend.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from loader import bundle
from vs.cfbd_vs import properties
from vs.host.namespace import Namespace

LUA = pytest.importorskip("lupa.lua54")

LIBRARY = (Path(bundle.LUA_DIR) / bundle.LIBRARY_FILE).read_text(encoding="utf-8")
TEST = Namespace("TEST_")
KILLED = "R0003"
COLLISION = "40001"
TRACEBACK = "\nstack traceback:"
STUBS = r"""
null = setmetatable({}, {__tostring = function() return "null" end})
STATEMENTS = {}
RESPONSES = {}
function respond(pattern, ok, result, times)
  RESPONSES[#RESPONSES + 1] = {pattern = pattern, ok = ok, result = result, times = times}
end
function failure(code, message)
  return {error_code = code, error_message = message}
end
function pquery(sql, binds)
  STATEMENTS[#STATEMENTS + 1] = {sql = sql, binds = binds or {}}
  for _, r in ipairs(RESPONSES) do
    if (r.times == nil or r.times > 0) and sql:find(r.pattern, 1, true) then
      if r.times ~= nil then r.times = r.times - 1 end
      return r.ok, r.result
    end
  end
  return true, {rows_affected = 1}
end
function bound(index, name)
  local value = STATEMENTS[index].binds[name]
  if value == null then return "<null>" end
  return value
end
"""


def lua_text(text: str) -> str:
    assert "]==]" not in text
    return f"[==[{text}]==]"


class LoadLib:
    """The bundled library (namespace constants + `loadlib.lua`) over the `pquery` stub."""

    def __init__(self) -> None:
        self.lua = LUA.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUBS)
        self.lua.execute(bundle.render_constants(TEST) + LIBRARY)

    def run(self, code: str) -> Any:
        return self.lua.execute(code)

    def respond(self, pattern: str, result: str, *, times: int | None = None) -> None:
        count = "nil" if times is None else str(times)
        self.run(f"respond({lua_text(pattern)}, true, {result}, {count})")

    def fail_with(self, pattern: str, code: str, message: str, *, times: int | None = None) -> None:
        count = "nil" if times is None else str(times)
        self.run(
            f"respond({lua_text(pattern)}, false, "
            f"failure({lua_text(code)}, {lua_text(message)}), {count})"
        )

    def statements(self) -> list[str]:
        return [str(entry["sql"]) for entry in self.lua.globals().STATEMENTS.values()]

    def bound(self, index: int, name: str) -> Any:
        return self.run(f"return bound({index + 1}, {lua_text(name)})")

    def error_of(self, code: str) -> str:
        """The message a Lua error raised by `code` carries, without lupa's stack traceback."""
        with pytest.raises(LUA.LuaError) as raised:
            self.run(code)
        return str(raised.value).split(TRACEBACK, 1)[0]


def verbs(statements: list[str]) -> list[str]:
    """Each statement as `<VERB> <first meta/stage/core table it names>`."""
    named = []
    for sql in statements:
        verb = sql.split(None, 1)[0].upper()
        table = re.search(r'"TEST_CFB_(?:META|STAGE|CORE)"\."([A-Z_]+)"', sql)
        named.append(verb if table is None else f"{verb} {table.group(1)}")
    return named


@pytest.fixture
def lib() -> LoadLib:
    return LoadLib()


def running_row(run_id: int, kind: str, state: str) -> str:
    return (
        f"{{RUN_ID = {run_id}, RUN_KIND = '{kind}', SESSION_TEXT = '{run_id}00', "
        f"STARTED_AT = '2020-10-20 12:00:00.000000', LOCK_STATE = '{state}'}}"
    )


# Values ------------------------------------------------------------------------------------------


def test_nz_maps_null_to_nil_and_sql_value_back(lib: LoadLib) -> None:
    assert lib.run("return nz(null) == nil, nz(0), nz('x')") == (True, 0, "x")
    assert lib.run("return sql_value(nil) == null, sql_value(5)") == (True, 5)


def test_clip_keeps_whole_characters(lib: LoadLib) -> None:
    clipped = lib.run("return clip(string.rep('ä', 3000), 2000)")
    assert len(clipped) == 2000
    assert clipped.endswith("...")
    assert set(clipped[:-3]) == {"ä"}
    assert lib.run("return clip('kurz', 2000)") == "kurz"
    assert lib.run("return clip(nil, 10) == nil") is True


def test_clip_replaces_invalid_utf8_bytes(lib: LoadLib) -> None:
    assert lib.run(r"return clip('a\255b', 10)") == "a?b"


def test_whole_number_accepts_integers_and_null_only(lib: LoadLib) -> None:
    assert lib.run("return whole_number(2018, 'x'), whole_number(2018.0, 'x')") == (2018, 2018)
    assert lib.run("return whole_number(null, 'x') == nil") is True
    assert "from_season must be a whole number" in lib.error_of("whole_number(2.5, 'from_season')")
    assert "must be a whole number" in lib.error_of("whole_number('2018', 'x')")


def test_short_error_keeps_the_adapter_line(lib: LoadLib) -> None:
    text = "UDF failed\nTraceback ...\nCFBD_VS.GAMES: HTTP 500 for /games?year=2018\nmore"
    assert lib.run(f"return short_error({lua_text(text)})") == (
        "CFBD_VS.GAMES: HTTP 500 for /games?year=2018"
    )
    assert lib.run("return short_error('first line\\nsecond')") == "first line"


def test_qualified_name_quotes_both_parts_of_a_script_name(lib: LoadLib) -> None:
    assert lib.run("return qualified_name('CFB_INFRA.REFRESH_MODEL')") == (
        '"CFB_INFRA"."REFRESH_MODEL"'
    )
    for bad in ("REFRESH_MODEL", "cfb_infra.M", 'A."B"', "A.B.C", "A.B; DROP", "A.", ".B"):
        message = lib.error_of(f"qualified_name({lua_text(bad)})")
        assert "not a qualified UPPER_CASE name like SCHEMA.NAME" in message, bad
    assert "got nil" in lib.error_of("qualified_name(nil)")


def test_is_killed_names_only_a_kill_or_abort(lib: LoadLib) -> None:
    lib.fail_with("SELECT 1", KILLED, "Client requested execution abort.")
    lib.fail_with("SELECT 2", "42000", "syntax error")
    assert lib.run("local ok, err = pcall(run_sql, 'SELECT 1'); return is_killed(err)") is True
    assert lib.run("local ok, err = pcall(run_sql, 'SELECT 2'); return is_killed(err)") is False
    assert lib.run("return is_killed('Client requested execution abort.')") is False
    assert lib.run("return is_killed(nil)") is False


def test_the_default_per_query_budget_equals_the_adapter_default() -> None:
    found = re.search(r"DEFAULT_MAX_CALLS_PER_QUERY = (\d+)", LIBRARY)
    highest = re.search(r"MAX_MAX_CALLS_PER_QUERY = (\d+)", LIBRARY)
    assert found is not None and highest is not None
    assert int(found.group(1)) == properties.DEFAULT_MAX_CALLS_PER_QUERY
    assert int(highest.group(1)) == properties.MAX_MAX_CALLS_PER_QUERY


# Failure path ------------------------------------------------------------------------------------


def test_fail_rolls_back_then_logs_then_commits_then_raises(lib: LoadLib) -> None:
    lib.run("JOB = {run_id = 7, open_step = 3, slices = 2, rows = 10, calls = 1}")
    assert lib.error_of("fail(JOB, 'boom: the fetch failed')") == "boom: the fetch failed"
    assert verbs(lib.statements()) == [
        "ROLLBACK",
        "UPDATE REFRESH_LOG",
        "UPDATE LOAD_RUN",
        "UPDATE LOAD_RUN",
        "COMMIT",
    ]
    assert lib.bound(1, "status") == "FAILED"
    assert (lib.bound(1, "run"), lib.bound(1, "step")) == (7, 3)
    assert (lib.bound(2, "slices"), lib.bound(2, "rows"), lib.bound(2, "calls")) == (2, 10, 1)
    assert lib.bound(3, "status") == "FAILED"
    assert lib.bound(3, "detail") == "boom: the fetch failed"


def test_fail_without_an_open_step_only_ends_the_run(lib: LoadLib) -> None:
    lib.run("JOB = {run_id = 7, slices = 0, rows = 0, calls = 0}")
    lib.error_of("fail(JOB, 'boom')")
    assert verbs(lib.statements()) == ["ROLLBACK", "UPDATE LOAD_RUN", "UPDATE LOAD_RUN", "COMMIT"]


def test_fail_after_a_kill_only_rolls_back(lib: LoadLib) -> None:
    lib.fail_with("SELECT 1", KILLED, "Client requested execution abort.")
    lib.run("JOB = {run_id = 7, open_step = 3, slices = 0, rows = 0, calls = 0}")
    message = lib.error_of("local ok, err = pcall(run_sql, 'SELECT 1'); fail(JOB, err)")
    assert "execution abort" in message
    assert lib.statements() == ["SELECT 1", "ROLLBACK"]


def test_fail_raises_the_original_error_when_the_log_fails_too(lib: LoadLib) -> None:
    lib.fail_with("UPDATE", "40001", "Transaction collision")
    lib.run("JOB = {run_id = 7, slices = 0, rows = 0, calls = 0}")
    message = lib.error_of("fail(JOB, 'boom')")
    assert message.startswith("boom (the run could not be marked FAILED: ")
    assert verbs(lib.statements()) == ["ROLLBACK", "UPDATE LOAD_RUN", "ROLLBACK"]


def test_fail_without_a_job_only_rolls_back(lib: LoadLib) -> None:
    assert lib.error_of("fail(nil, 'early')") == "early"
    assert lib.statements() == ["ROLLBACK"]


def test_run_sql_keeps_the_error_code_beside_the_message(lib: LoadLib) -> None:
    lib.fail_with("SELECT 1", "42000", "syntax error")
    assert lib.run("local ok, err = pcall(run_sql, 'SELECT 1'); return err, failure_code(err)") == (
        "syntax error",
        "42000",
    )
    assert lib.run("return failure_code('another message')") is None


# Run lock ----------------------------------------------------------------------------------------


def test_the_lock_refuses_a_live_holder_without_writing(lib: LoadLib) -> None:
    lib.respond('"LOCK_STATE"', "{" + running_row(5, "BACKFILL", "LIVE") + "}")
    message = lib.error_of("acquire_lock('REFRESH_WEEK', '2020, 9, regular')")
    assert "REFRESH_WEEK refused: BACKFILL run 5 is running (session 500" in message
    assert verbs(lib.statements()) == ["COMMIT", "SELECT LOAD_RUN", "ROLLBACK"]


def test_the_lock_abandons_stale_rows_then_inserts_its_own(lib: LoadLib) -> None:
    rows = ", ".join(
        running_row(run_id, kind, state)
        for run_id, kind, state in (
            (1, "BACKFILL", "OWN"),
            (2, "DEPLOY", "GONE"),
            (3, "SIMULATE", "IDLE"),
        )
    )
    lib.respond('"LOCK_STATE"', "{" + rows + "}")
    lib.respond('SELECT MAX("RUN_ID")', "{{RUN_ID = 42}}")
    assert lib.run("return acquire_lock('SIMULATE', 'sim 100000')") == 42
    assert verbs(lib.statements()) == [
        "COMMIT",
        "SELECT LOAD_RUN",
        *["UPDATE LOAD_RUN", "UPDATE REFRESH_LOG"] * 3,
        "INSERT LOAD_RUN",
        "SELECT LOAD_RUN",
        "COMMIT",
    ]
    details = [lib.bound(index, "detail") for index in (2, 4, 6)]
    assert "new run started in the session of this run" in details[0]
    assert "session of this run has ended" in details[1]
    assert "is idle" in details[2]
    assert (lib.bound(8, "kind"), lib.bound(8, "args")) == ("SIMULATE", "sim 100000")


def test_the_idle_rule_applies_to_script_kinds_only(lib: LoadLib) -> None:
    lib.respond('SELECT MAX("RUN_ID")', "{{RUN_ID = 1}}")
    lib.run("acquire_lock('DEPLOY', 'make sim')")
    select = lib.statements()[1]
    idle = re.search(r"\"STATUS\" = 'IDLE' AND R\.\"RUN_KIND\" IN \(([^)]*)\)", select)
    assert idle is not None
    assert sorted(re.findall(r"'([A-Z_]+)'", idle.group(1))) == [
        "BACKFILL",
        "REFRESH_WEEK",
        "SIMULATE",
    ]
    assert "WHEN R.\"SESSION_ID\" = CURRENT_SESSION THEN 'OWN'" in select
    assert "WHEN S.\"SESSION_ID\" IS NULL THEN 'GONE'" in select


def test_a_collision_is_rolled_back_and_retried(lib: LoadLib) -> None:
    lib.fail_with('INSERT INTO "TEST_CFB_META"."LOAD_RUN"', COLLISION, "collision", times=2)
    lib.respond('SELECT MAX("RUN_ID")', "{{RUN_ID = 9}}")
    assert lib.run("return acquire_lock('BACKFILL', 'demo, NULL, NULL')") == 9
    assert verbs(lib.statements()).count("ROLLBACK") == 2
    assert verbs(lib.statements())[-1] == "COMMIT"


def test_three_collisions_refuse_the_run(lib: LoadLib) -> None:
    lib.fail_with('INSERT INTO "TEST_CFB_META"."LOAD_RUN"', COLLISION, "collision")
    message = lib.error_of("acquire_lock('BACKFILL', 'demo, NULL, NULL')")
    assert "another session took the run lock" in message
    assert "3 attempts" in message
    assert verbs(lib.statements())[-1] == "ROLLBACK"
    again = "local ok, err = pcall(acquire_lock, 'BACKFILL', 'x'); return failure_code(err)"
    assert lib.run(again) == COLLISION


def test_another_lock_error_is_raised_at_once(lib: LoadLib) -> None:
    lib.fail_with('"LOCK_STATE"', "42500", "insufficient privileges")
    message = lib.error_of("acquire_lock('BACKFILL', 'x')")
    assert "could not take the run lock in TEST_CFB_META.LOAD_RUN: insufficient privileges" in (
        message
    )
    assert verbs(lib.statements()) == ["COMMIT", "SELECT LOAD_RUN", "ROLLBACK"]


def test_an_unknown_kind_is_refused_before_any_statement(lib: LoadLib) -> None:
    assert "unknown run kind PROMOTE" in lib.error_of("acquire_lock('PROMOTE', 'x')")
    assert lib.statements() == []


def test_release_ends_only_a_running_row(lib: LoadLib) -> None:
    lib.respond('SET "RUN_STATUS" = :status', "{rows_affected = 0}", times=1)
    assert lib.run("return release_lock(4, 'DONE', 'ok')") is False
    assert lib.run("return release_lock(4, 'DONE', 'ok')") is True
    assert "AND \"RUN_STATUS\" = 'RUNNING'" in lib.statements()[0]
    assert "must be an upper-case word other than RUNNING" in lib.error_of(
        "release_lock(4, 'RUNNING', 'x')"
    )
    assert "upper-case word" in lib.error_of("release_lock(4, 'done', 'x')")


# Quota and measured spend ------------------------------------------------------------------------


def quota_row(used: int, remaining: int, backfill: int) -> str:
    return (
        f"{{{{USED_CALLS = {used}, REMAINING_CALLS = {remaining}, "
        f"REFRESH_SPENDABLE = {backfill + 150}, BACKFILL_SPENDABLE = {backfill}}}}}"
    )


def test_read_quota_reads_and_commits(lib: LoadLib) -> None:
    lib.respond('"BACKFILL_SPENDABLE"', quota_row(840, 160, 10))
    assert lib.run("local q = read_quota(); return q.used, q.remaining, q.refresh, q.backfill") == (
        840,
        160,
        160,
        10,
    )
    assert verbs(lib.statements()) == ["SELECT QUOTA", "COMMIT"]


def test_an_unreadable_quota_raises(lib: LoadLib) -> None:
    lib.fail_with('"BACKFILL_SPENDABLE"', "22002", "CFBD_VS.INFO: HTTP 500\ntraceback")
    message = lib.error_of("read_quota()")
    assert "quota cannot be read from TEST_CFB_META.QUOTA (/info): CFBD_VS.INFO: HTTP 500" in (
        message
    )
    assert "sends no request without it" in message


def test_a_quota_without_a_row_raises(lib: LoadLib) -> None:
    lib.respond('"BACKFILL_SPENDABLE"', "{}")
    assert "returned 0 row(s)" in lib.error_of("read_quota()")


def test_measured_spend_charges_the_info_delta_to_the_step_and_the_run(lib: LoadLib) -> None:
    lib.respond('"BACKFILL_SPENDABLE"', quota_row(847, 153, 3))
    lib.run("JOB = {run_id = 7, slices = 3, rows = 30, calls = 2}")
    after, spent = lib.run("local q, s = measure_spend(JOB, 4, {used = 840}); return q.used, s")
    assert (after, spent) == (847, 7)
    assert lib.run("return JOB.calls") == 9
    assert verbs(lib.statements()) == [
        "SELECT QUOTA",
        "COMMIT",
        "UPDATE REFRESH_LOG",
        "UPDATE LOAD_RUN",
        "COMMIT",
    ]
    assert '"CALLS" = COALESCE("CALLS", 0) + :calls' in lib.statements()[2]
    assert (lib.bound(2, "calls"), lib.bound(2, "step")) == (7, 4)
    assert lib.bound(3, "calls") == 9


def test_measured_spend_is_never_negative(lib: LoadLib) -> None:
    lib.respond('"BACKFILL_SPENDABLE"', quota_row(5, 995, 845))
    lib.run("JOB = {run_id = 7, slices = 0, rows = 0, calls = 0}")
    assert lib.run("local q, s = measure_spend(JOB, 2, {used = 9}); return s") == 0
    assert verbs(lib.statements()) == ["SELECT QUOTA", "COMMIT"]


# Batches -----------------------------------------------------------------------------------------


def queue_row(entry: int, season: int | None, *, alone: bool = False) -> str:
    season_text = "null" if season is None else str(season)
    key = f"/t{entry}?year={season}"
    return (
        f"{{ENTRY_ID = {entry}, TABLE_NAME = 'GAMES', SLICING = 'season', "
        f"SEASON = {season_text}, SLICE_KEY = '{key}', SEASON_FROM = 1869, SEASON_TO = null, "
        f"FIXED_FILTER = null, BATCH_ALONE = {'true' if alone else 'false'}}}"
    )


def lua_list(table: Any) -> list[Any]:
    return [table[index] for index in range(1, len(table) + 1)]


def batches_of(lib: LoadLib, rows: list[str], cap: int) -> list[list[str]]:
    lib.run(f"BATCHES = split_batches({{{', '.join(rows)}}}, {cap})")
    count = int(lib.run("return #BATCHES"))
    return [lua_list(lib.run(f"return BATCHES[{i}].keys")) for i in range(1, count + 1)]


def test_batches_are_runs_of_one_entry_capped(lib: LoadLib) -> None:
    rows = [queue_row(1, 2020), queue_row(1, 2019), queue_row(1, 2018), queue_row(2, 2020)]
    assert batches_of(lib, rows, 2) == [
        ["/t1?year=2020", "/t1?year=2019"],
        ["/t1?year=2018"],
        ["/t2?year=2020"],
    ]


def test_a_slice_that_failed_before_is_a_batch_of_its_own(lib: LoadLib) -> None:
    rows = [
        queue_row(1, 2020),
        queue_row(1, 2017, alone=True),
        queue_row(1, 2016, alone=True),
    ]
    assert batches_of(lib, rows, 10) == [["/t1?year=2020"], ["/t1?year=2017"], ["/t1?year=2016"]]
    assert lib.run("return BATCHES[1].alone, BATCHES[2].alone") == (False, True)


def test_single_slice_batches_keep_the_entry(lib: LoadLib) -> None:
    batches_of(lib, [queue_row(3, 2020), queue_row(3, 2019)], 10)
    lib.run("SINGLES = single_slice_batches(BATCHES[1])")
    assert lib.run("return #SINGLES, SINGLES[2].keys[1], SINGLES[2].seasons[1]") == (
        2,
        "/t3?year=2019",
        2019,
    )
    assert lib.run("return SINGLES[1].entry_id, #BATCHES[1].keys") == (3, 2)


def test_the_batch_query_is_fixed_filter_slicing_filter_and_fetched_rows_only(
    lib: LoadLib,
) -> None:
    fixed = lua_text("\"P_CLASSIFICATION\" = 'fbs'")
    lib.run(
        "B = {entry_id = 1, table_name = 'GAMES', slicing = 'season', "
        f"fixed_filter = {fixed}, keys = {{'a', 'b'}}, seasons = {{2020, 2019}}}}"
    )
    assert lib.run("return batch_query(B)") == (
        'SELECT * FROM "TEST_CFBD_VS"."GAMES" WHERE ("P_CLASSIFICATION" = \'fbs\') '
        'AND "P_YEAR" IN (2020, 2019) AND "REQ_SOURCE" <> \'STORED\''
    )
    lib.run(
        "B = {entry_id = 2, table_name = 'CONFERENCES_AFFILIATIONS', slicing = 'season_range', "
        "season_from = 1869, season_to = 2025, keys = {'r'}, seasons = {}}"
    )
    assert lib.run("return batch_query(B)") == (
        'SELECT * FROM "TEST_CFBD_VS"."CONFERENCES_AFFILIATIONS" WHERE "P_MIN_YEAR" = 1869 '
        'AND "P_MAX_YEAR" = 2025 AND "REQ_SOURCE" <> \'STORED\''
    )
    lib.run("B = {entry_id = 3, table_name = 'TEAMS', slicing = 'none', keys = {'t'}}")
    assert lib.run("return batch_query(B)") == (
        'SELECT * FROM "TEST_CFBD_VS"."TEAMS" WHERE "REQ_SOURCE" <> \'STORED\''
    )
    lib.run("B.slicing = 'season_week'")
    assert "unknown slicing season_week" in lib.error_of("batch_query(B)")
    lib.run("B = {entry_id = 4, table_name = 'games', slicing = 'none', keys = {}, seasons = {}}")
    assert "not an UPPER_CASE identifier: games" in lib.error_of("batch_query(B)")


def manifest(keys: list[str]) -> str:
    listed = ",".join(f'"{key}"' for key in keys)
    text = f'/*CFBD {{"planned_requests":{len(keys)},"slice_keys":[{listed}]}}*/ SELECT 1'
    return "{{PUSHDOWN_SQL = " + lua_text(text) + "}}"


def batch_lua(keys: list[str]) -> str:
    listed = ", ".join(f"'{key}'" for key in keys)
    seasons = ", ".join(str(2020 - i) for i in range(len(keys)))
    return (
        "{entry_id = 7, table_name = 'GAMES', slicing = 'season', "
        f"keys = {{{listed}}}, seasons = {{{seasons}}}}}"
    )


def test_the_explain_check_accepts_exactly_the_batch_keys(lib: LoadLib) -> None:
    lib.respond("EXPLAIN VIRTUAL", manifest(["/g?year=2020", "/g?year=2019"]))
    lib.run(f"explain_check({batch_lua(['/g?year=2020', '/g?year=2019'])})")
    assert lib.statements()[0].startswith('SELECT "PUSHDOWN_SQL" FROM (EXPLAIN VIRTUAL SELECT')


def test_the_explain_check_names_both_key_lists_and_the_remedy(lib: LoadLib) -> None:
    lib.respond("EXPLAIN VIRTUAL", manifest(["/g?year=2020"]), times=1)
    message = lib.error_of(f"explain_check({batch_lua(['/g?seasonType=both&year=2020'])})")
    for part in ("GAMES (plan entry 7)", "/g?seasonType=both&year=2020", '["/g?year=2020"]'):
        assert part in message
    assert "make deploy" in message and "No request was sent" in message
    lib.respond("EXPLAIN VIRTUAL", manifest(["a", "b"]))
    assert "plans 2 request(s)" in lib.error_of(f"explain_check({batch_lua(['a'])})")


def load_batch_responses(
    lib: LoadLib, keys: list[str], *, staged: int = 0, strays: int = 0, merged: int | None = None
) -> None:
    lib.respond("EXPLAIN VIRTUAL", manifest(keys))
    lib.respond('"REQ_SLICE" NOT IN', f"{{{{N = {strays}}}}}")
    lib.respond('SELECT COUNT(*) AS "N" FROM "TEST_CFB_STAGE"', f"{{{{N = {staged}}}}}")
    lib.respond("MERGE INTO", f"{{rows_affected = {len(keys) if merged is None else merged}}}")
    lib.respond('INSERT INTO "TEST_CFB_META"."FETCH_LOG"', f"{{rows_affected = {len(keys)}}}")
    lib.respond('INSERT INTO "TEST_CFB_CORE"', "{rows_affected = 20}")


def test_a_batch_is_one_transaction_in_the_planned_order(lib: LoadLib) -> None:
    keys = ["/g?year=2020", "/g?year=2019"]
    load_batch_responses(lib, keys)
    lib.run("JOB = {run_id = 7, tag = 'BACKFILL#7', fetch_source = 'REPLAY'}")
    loaded, slices, rows = lib.run(
        f"local ok, s = load_batch(JOB, {batch_lua(keys)}, function(stats) "
        "run_sql('UPDATE CLOSE') end); return ok, s.slices, s.rows"
    )
    assert (loaded, slices, rows) == (True, 2, 20)
    assert verbs(lib.statements()) == [
        "SELECT",
        "SELECT GAMES",
        "INSERT GAMES",
        "SELECT GAMES",
        "DELETE GAMES",
        "INSERT GAMES",
        "MERGE LOAD_LOG",
        "INSERT FETCH_LOG",
        "DELETE LOAD_FAILURES",
        "DELETE GAMES",
        "UPDATE",
        "COMMIT",
    ]
    statements = lib.statements()
    assert statements[2].startswith('INSERT INTO "TEST_CFB_STAGE"."GAMES" SELECT * FROM')
    assert statements[4].startswith('DELETE FROM "TEST_CFB_CORE"."GAMES"')
    assert statements[5] == (
        'INSERT INTO "TEST_CFB_CORE"."GAMES" SELECT * FROM "TEST_CFB_STAGE"."GAMES"'
    )
    assert (lib.bound(4, "k1"), lib.bound(4, "k2")) == tuple(keys)
    assert lib.bound(6, "tag") == "BACKFILL#7"
    assert lib.bound(7, "source") == "REPLAY"


def test_a_failed_fetch_is_rolled_back_and_returned(lib: LoadLib) -> None:
    keys = ["/g?year=2018"]
    load_batch_responses(lib, keys)
    lib.fail_with(
        'INSERT INTO "TEST_CFB_STAGE"',
        "22002",
        "VM error: Traceback\nCFBD_VS.GAMES: HTTP 500 for /g?year=2018 (1 attempt)\nmore",
    )
    lib.run("JOB = {run_id = 7, tag = 'BACKFILL#7', fetch_source = 'LIVE'}")
    assert lib.run(f"return load_batch(JOB, {batch_lua(keys)})") == (
        False,
        "CFBD_VS.GAMES: HTTP 500 for /g?year=2018 (1 attempt)",
    )
    assert verbs(lib.statements()) == ["SELECT", "SELECT GAMES", "INSERT GAMES", "ROLLBACK"]


def test_a_killed_fetch_raises_after_the_rollback(lib: LoadLib) -> None:
    keys = ["/g?year=2018"]
    load_batch_responses(lib, keys)
    lib.fail_with('INSERT INTO "TEST_CFB_STAGE"', KILLED, "Client requested execution abort.")
    lib.run("JOB = {run_id = 7, tag = 'BACKFILL#7', fetch_source = 'LIVE'}")
    code = lib.run(
        f"local ok, err = pcall(load_batch, JOB, {batch_lua(keys)}); return failure_code(err)"
    )
    assert code == KILLED
    assert verbs(lib.statements())[-1] == "ROLLBACK"


def test_a_stage_with_rows_stops_the_batch_before_the_fetch(lib: LoadLib) -> None:
    keys = ["/g?year=2018"]
    load_batch_responses(lib, keys, staged=4)
    lib.run("JOB = {run_id = 7, tag = 'BACKFILL#7', fetch_source = 'LIVE'}")
    message = lib.error_of(f"load_batch(JOB, {batch_lua(keys)})")
    assert "TEST_CFB_STAGE.GAMES holds 4 row(s)" in message
    assert verbs(lib.statements()) == ["SELECT", "SELECT GAMES"]


def test_rows_of_other_slices_stop_the_batch(lib: LoadLib) -> None:
    keys = ["/g?year=2018"]
    load_batch_responses(lib, keys, strays=3)
    lib.run("JOB = {run_id = 7, tag = 'BACKFILL#7', fetch_source = 'LIVE'}")
    assert "staged 3 row(s) of slices outside the batch" in lib.error_of(
        f"load_batch(JOB, {batch_lua(keys)})"
    )
    assert "DELETE GAMES" not in verbs(lib.statements())


def test_a_merge_that_misses_a_slice_stops_the_batch(lib: LoadLib) -> None:
    keys = ["/g?year=2020", "/g?year=2019"]
    load_batch_responses(lib, keys, merged=1)
    lib.run("JOB = {run_id = 7, tag = 'BACKFILL#7', fetch_source = 'LIVE'}")
    assert "MERGE INTO LOAD_LOG wrote 1 row(s), expected 2" in lib.error_of(
        f"load_batch(JOB, {batch_lua(keys)}, nil)"
    )
    assert "COMMIT" not in verbs(lib.statements())


def test_load_batch_needs_the_fetch_source(lib: LoadLib) -> None:
    lib.run("JOB = {run_id = 7, tag = 'BACKFILL#7'}")
    assert "fetch_source is not set" in lib.error_of(f"load_batch(JOB, {batch_lua(['a'])})")
    assert lib.statements() == []


def test_a_failure_is_counted_once_per_run(lib: LoadLib) -> None:
    lib.run("record_failure({run_id = 7}, 'GAMES', '/g?year=2017', 'HTTP 500')")
    merge = lib.statements()[0]
    assert merge.startswith('MERGE INTO "TEST_CFB_META"."LOAD_FAILURES"')
    assert 'CASE WHEN F."LAST_RUN_ID" = :run THEN F."FAILURES"' in merge
    assert (lib.bound(0, "run"), lib.bound(0, "e")) == (7, "HTTP 500")
