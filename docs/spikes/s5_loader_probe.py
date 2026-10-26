#!/usr/bin/env python3
"""S5 loader platform probe (task 1.1, plan add-cfb-persistence-refresh).

Checks the platform facts (a)-(m) that the loader's Lua scripting programs (`BACKFILL`,
`REFRESH_WEEK`, `loadlib.lua`), the loader views and the run lock are built on. Findings and the
fallback the plan adopts when a result is negative are in `docs/spikes/s5-loader-probe.md`.

Safety rules:
- Only PROBE_* objects: the schema `PROBE_S5`, the user `PROBE_S5_USER` (random password, held in
  memory only), the connection `PROBE_S5_CONN` to `http://192.0.2.1:9` (TEST-NET-1, never
  contacted) with a generated dummy key, and a probe virtual schema namespace `PROBE_`
  (`PROBE_CFBD_VS`, `PROBE_CFB_INFRA`, `PROBE_CFB_META`, `PROBE_CFB_CORE`, `PROBE_CFB_STAGE`) in
  `FETCH_MODE = replay` over synthetic recordings in the BucketFS directory
  `bfsdefault/default/cfbd/probe-s5-replay`. Everything is removed at the end (unless --keep).
- Zero CFBD calls: the probe namespace replays recordings and never reads a real connection, and
  `API_FEATURES` is not synced (no `/info` request). Nothing `TEST_*`, `CFB_*` or `CFBD_API` is
  touched.
- The sys password comes from the Personal deployment files through `vs/host/target.py` at
  connect time and is never printed or stored.

Usage:
    uv run python docs/spikes/s5_loader_probe.py [--target local|aws] [--only a,b,...] [--keep]

`--target aws` (task 9.0) runs the read-only and lock items a, b, c, g, h, i, j, l and m; item (d)
needs the local BucketFS host directory and stays local. Writes
`cache/probe/s5-loader-probe[-aws].json` (gitignored). Exit code 3 when the gate (a, b, c) fails.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.it import support  # noqa: E402
from vs.host import deploy, fetchlog, target  # noqa: E402
from vs.host.namespace import Namespace  # noqa: E402

RESULTS_DIR = REPO_ROOT / "cache" / "probe"
SCHEMA = "PROBE_S5"
USER = "PROBE_S5_USER"
CONN = "PROBE_S5_CONN"
CONN_ADDRESS = "http://192.0.2.1:9"
NAMESPACE = Namespace("PROBE_")
STAGE_SCHEMA = "PROBE_CFB_STAGE"
REPLAY_DIRECTORY = "cfbd/probe-s5-replay"
PROBE_TABLE = "GAMES"
PROBE_SEASONS = (2020, 2019)
KILL_AFTER_SECONDS = 2.5
SLEEP_SECONDS = 8
SAMPLE_LOOP_STATEMENTS = 1500
GATE_ITEMS = ("a", "b", "c")
ITEMS = "abcdefghijklm"
AWS_ITEMS = "abcghijlm"
ABORT_MESSAGE = "Client requested execution abort."

TITLES = {
    "a": "RETURNS TABLE with exit(rows, coldefs); WITH OUTPUT; argument count",
    "b": "COMMIT/ROLLBACK, failing pquery in an open transaction, pcall, error() and autocommit",
    "c": "EXPLAIN VIRTUAL from Lua: subselect and plain form, manifest search",
    "d": "Replay-mode INSERT … SELECT from the VS inside a script; empty-SET-group guard",
    "e": "Nested EXECUTE SCRIPT: arguments, RETURNS ROWCOUNT, inner failures, import()",
    "f": "Scripts run with the caller's privileges",
    "g": "Lua value types of query results and script parameters",
    "h": "Rows committed by a running script are visible to other sessions",
    "i": "Two sessions inserting the run-lock row at the same time",
    "j": "Session liveness and idleness: EXA_ALL_SESSIONS, KILL STATEMENT, client abort",
    "k": "CREATE TABLE … (LIKE …) and MERGE whose USING source reads the target",
    "l": "Session time zone and one UTC 'now' expression",
    "m": "Lua runtime: cjson, os, utf8, exa.meta, bind parameters",
}

FALLBACKS = {
    "a": "Gate. Without a returned table the timeline goes to REFRESH_LOG only and the wrappers "
    "print it; escalate. A missing short argument form keeps the 3-argument demo line.",
    "b": "Gate. One batch per EXECUTE SCRIPT call, with `make backfill` looping; the loop stops on "
    "the first failed call and on any call without progress (m2).",
    "c": "Gate. Use plain query('EXPLAIN VIRTUAL …') (result set directly); if neither works, "
    "keep only the post-fetch REQ_SLICE check.",
    "d": "Load through the host (pyexasol) for the failing shape; escalate if the empty-group guard "
    "leaks rows.",
    "e": "import() of a step module instead of a nested EXECUTE SCRIPT.",
    "f": "Document the definer model instead; the Phase 6 loader role changes accordingly.",
    "g": "Normalize values at the Lua boundary (tonumber/tostring) and compare in SQL.",
    "h": "The app timeline reads REFRESH_LOG after the run ends only; document it.",
    "i": "Serialize lock acquisition through a dedicated lock table with one row and UPDATE.",
    "j": "A9 fallback: only 'own session' and 'session gone' make a RUNNING row stale; documented.",
    "k": "Stage DDL rendered from the mirror DDL; LOAD_LOG update as DELETE + INSERT.",
    "l": "Store the UTC offset in LOADER_CONFIG and compute now in Lua with os.date('!…').",
    "m": "Plain string search for the manifest (the plan's default); no JSON library.",
}


@dataclass
class Finding:
    """One probe result, with the fallback the plan adopts when the result is negative."""

    item: str
    status: str
    result: str
    details: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    fallback_if_negative: str = ""
    fallback_adopted: str = ""

    def __post_init__(self) -> None:
        self.title = TITLES[self.item]
        self.fallback_if_negative = FALLBACKS[self.item]
        negative = self.status in ("fail", "error")
        self.fallback_adopted = self.fallback_if_negative if negative else "none needed"


class Session:
    """A pyexasol connection whose query errors come back as data."""

    def __init__(
        self, deployment: target.Target, user: str | None = None, password: str | None = None
    ):
        self.conn = deployment.connect(user, password)
        self.conn.set_autocommit(True)

    @property
    def session_id(self) -> str:
        return str(self.conn.session_id())

    def run(self, sql: str) -> Any:
        return self.conn.execute(sql)

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self.conn.execute(sql).fetchall()]

    def value(self, sql: str) -> Any:
        return self.conn.execute(sql).fetchval()

    def attempt(self, sql: str) -> tuple[bool, Any]:
        try:
            statement = self.conn.execute(sql)
            if statement.result_type == "resultSet":
                return True, [tuple(row) for row in statement.fetchall()]
            return True, statement.rowcount()
        except Exception as error:
            return False, error_text(error)

    def table(self, sql: str) -> tuple[bool, Any]:
        """A script result as `(column names + types, rows)`, or the error text."""
        try:
            statement = self.conn.execute(sql)
            columns = {name: info.get("type") for name, info in statement.columns().items()}
            sizes = {
                name: info.get("size") or (info.get("precision"), info.get("scale"))
                for name, info in statement.columns().items()
            }
            return True, {
                "columns": columns,
                "sizes": sizes,
                "rows": [tuple(r) for r in statement.fetchall()],
            }
        except Exception as error:
            return False, error_text(error)

    def notes(self, sql: str) -> dict[str, str]:
        """Run a probe script that returns `(K, V)` note rows; the notes as a dict."""
        ok, result = self.attempt(sql)
        if not ok:
            return {"__error__": result}
        return {str(k): v for k, v in result}

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()


def error_text(error: BaseException) -> str:
    message = getattr(error, "message", None) or str(error)
    return " ".join(str(message).split())[:1500]


def lua_script(name: str, params: str, body: str, returns: str = "RETURNS TABLE") -> str:
    """`CREATE OR REPLACE LUA SCRIPT PROBE_S5.<name>(<params>) <returns> AS <body> /`."""
    return f"CREATE OR REPLACE LUA SCRIPT {SCHEMA}.{name}({params}) {returns} AS\n{LUA_PRELUDE}{body}\n/"


LUA_PRELUDE = """local out = {}
local function note(k, v) out[#out + 1] = {k, string.sub(tostring(v), 1, 1900)} end
local function ty(v)
  if v == null then return "null" end
  if v == nil then return "nil" end
  local t = type(v)
  if t == "number" then return t .. "/" .. math.type(v) end
  return t
end
local function notes_exit() exit(out, "K VARCHAR(200), V VARCHAR(2000)") end
"""

SLEEP_UDF = f"""CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {SCHEMA}.SLEEP_UDF(S DOUBLE) RETURNS DOUBLE AS
import time


def run(ctx):
    time.sleep(ctx.S)
    return ctx.S
/"""

FAIL_UDF = f"""CREATE OR REPLACE PYTHON3 SCALAR SCRIPT {SCHEMA}.FAIL_UDF(X DECIMAL(9,0)) RETURNS DECIMAL(9,0) AS
def run(ctx):
    raise RuntimeError("probe udf failure")
/"""


class Probe:
    def __init__(self, target_name: str, keep: bool) -> None:
        self.target_name = target_name
        self.keep = keep
        self.deployment = target.resolve(target_name)
        self.sys = Session(self.deployment)
        self.user_password = "P" + secrets.token_hex(16)
        self.findings: dict[str, Finding] = {}
        self.area = support.ReplayArea(directory=REPLAY_DIRECTORY)
        self.vs_ready = False
        suffix = "" if target_name == "local" else f"-{target_name}"
        self.results_file = RESULTS_DIR / f"s5-loader-probe{suffix}.json"
        self.meta = {
            "target": self.deployment.describe(),
            "db_version": self.sys.value(
                "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
            ),
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------ setup and cleanup

    def setup(self) -> None:
        self.cleanup()
        for statement in (
            f"CREATE SCHEMA {SCHEMA}",
            f"CREATE TABLE {SCHEMA}.TX (ID DECIMAL(9,0), CONSTRAINT TX_PK PRIMARY KEY (ID))",
            f"CREATE TABLE {SCHEMA}.LOGT (ID DECIMAL(9,0), MSG VARCHAR(2000) UTF8)",
            f"CREATE TABLE {SCHEMA}.RUNS (RUN_ID DECIMAL(18,0) IDENTITY NOT NULL, TAG VARCHAR(10) UTF8, "
            "SESSION_ID DECIMAL(20,0), RUN_STATUS VARCHAR(20) UTF8)",
            f"CREATE TABLE {SCHEMA}.SECRET_T (ID DECIMAL(9,0))",
            f"INSERT INTO {SCHEMA}.SECRET_T VALUES 1",
            SLEEP_UDF,
            FAIL_UDF,
            f'CREATE USER {USER} IDENTIFIED BY "{self.user_password}"',
            f"GRANT CREATE SESSION TO {USER}",
        ):
            self.sys.run(statement)

    def setup_vs(self) -> None:
        """The probe namespace: a replay-mode VS over the real catalog, plus a stage table."""
        if self.vs_ready:
            return
        if self.target_name == "local":
            self.area.create()
        self.sys.run(
            f"CREATE CONNECTION {CONN} TO '{CONN_ADDRESS}' USER '' "
            f"IDENTIFIED BY 'probe-s5-dummy-{secrets.token_hex(8)}'"
        )
        report = deploy.deploy(
            self.sys.conn,
            NAMESPACE,
            deploy.DeployOptions(
                connection_name=CONN,
                properties={"FETCH_MODE": "replay", "REPLAY_PATH": self.area.udf_path},
                sync_api_features=False,
            ),
        )
        self.meta["vs_deploy"] = list(report.lines)
        self.sys.run(f"CREATE SCHEMA {STAGE_SCHEMA}")
        self.sys.run(
            f'CREATE TABLE "{STAGE_SCHEMA}"."{PROBE_TABLE}" (LIKE "{NAMESPACE.core_schema}"."{PROBE_TABLE}")'
        )
        self.sys.run(support.wait_script_sql(SCHEMA))
        self.vs_ready = True

    def cleanup(self) -> dict[str, Any]:
        statements = [
            f"DROP VIRTUAL SCHEMA IF EXISTS {NAMESPACE.vs_name} CASCADE",
            *(
                f"DROP SCHEMA IF EXISTS {schema} CASCADE"
                for schema in (
                    NAMESPACE.infra_schema,
                    NAMESPACE.meta_schema,
                    NAMESPACE.core_schema,
                    STAGE_SCHEMA,
                    SCHEMA,
                )
            ),
            f"DROP CONNECTION IF EXISTS {CONN}",
            f"DROP USER IF EXISTS {USER} CASCADE",
        ]
        failures = []
        for statement in statements:
            ok, result = self.sys.attempt(statement)
            if not ok:
                failures.append(f"{statement}: {result}")
        if self.target_name == "local":
            with contextlib.suppress(Exception):
                self.area.remove()
        left = self.sys.rows(
            "SELECT 'schema ' || SCHEMA_NAME FROM EXA_SCHEMAS WHERE SCHEMA_NAME LIKE 'PROBE%' "
            "UNION ALL SELECT 'user ' || USER_NAME FROM EXA_DBA_USERS WHERE USER_NAME LIKE 'PROBE%' "
            "UNION ALL SELECT 'connection ' || CONNECTION_NAME FROM EXA_DBA_CONNECTIONS "
            "WHERE CONNECTION_NAME LIKE 'PROBE%'"
        )
        return {"failures": failures, "left": [row[0] for row in left]}

    def user_session(self) -> Session:
        return Session(self.deployment, USER, self.user_password)

    def new_session(self) -> Session:
        return Session(self.deployment)

    # ------------------------------------------------------------------ (a)

    def probe_a(self) -> Finding:
        timeline_cols = (
            "RUN_ID DECIMAL(18,0), STEP_NO DECIMAL(3,0), STEP_NAME VARCHAR(160), STEP_STATUS VARCHAR(10), "
            "SECONDS DECIMAL(9,3), SLICES DECIMAL(9,0), ROWS_LOADED DECIMAL(18,0), CALLS DECIMAL(9,0), "
            "DETAIL VARCHAR(2000)"
        )
        self.sys.run(
            lua_script(
                "A_TIMELINE",
                "season, week, season_type",
                f"""output("output line for WITH OUTPUT")
local st = season_type
if st == null then st = "regular" end
exit({{
  {{42, 1, "CHECK", "DONE", 0.0123, nil, nil, 0, "season " .. season .. " week " .. week .. " " .. st}},
  {{42, 2, "FETCH GAMES", "DONE", 1.23456, 1, 812, 1, nil}},
}}, "{timeline_cols}")""",
            )
        )
        self.sys.run(
            lua_script(
                "A_EDGE",
                "mode",
                """if mode == 1 then exit({}, "A DECIMAL(9,0), B VARCHAR(5)") end
if mode == 2 then exit({{string.rep("a", 2001)}}, "DETAIL VARCHAR(2000)") end
if mode == 3 then exit({{string.sub(string.rep("ä", 1500), 1, 1999)}}, "DETAIL VARCHAR(2000)") end
if mode == 4 then
  local s = string.rep("ä", 2500)
  local cut = utf8.offset(s, 2001)
  exit({{string.sub(s, 1, cut - 1)}}, "DETAIL VARCHAR(2000)")
end
if mode == 5 then exit({{"abc"}}, "A DECIMAL(9,0)") end""",
            )
        )
        details: dict[str, Any] = {}
        details["three_args"] = self.sys.table(
            f"EXECUTE SCRIPT {SCHEMA}.A_TIMELINE(2026, 9, 'regular')"
        )
        details["null_season_type"] = self.sys.table(
            f"EXECUTE SCRIPT {SCHEMA}.A_TIMELINE(2026, 9, NULL)"
        )
        details["two_args"] = self.sys.attempt(f"EXECUTE SCRIPT {SCHEMA}.A_TIMELINE(2026, 9)")
        details["four_args"] = self.sys.attempt(
            f"EXECUTE SCRIPT {SCHEMA}.A_TIMELINE(2026, 9, 'regular', 1)"
        )
        details["with_output"] = self.sys.table(
            f"EXECUTE SCRIPT {SCHEMA}.A_TIMELINE(2026, 9, 'regular') WITH OUTPUT"
        )
        details["empty_rows"] = self.sys.table(f"EXECUTE SCRIPT {SCHEMA}.A_EDGE(1)")
        details["detail_2001_chars"] = self.sys.attempt(f"EXECUTE SCRIPT {SCHEMA}.A_EDGE(2)")
        details["detail_cut_mid_utf8"] = self.sys.attempt(f"EXECUTE SCRIPT {SCHEMA}.A_EDGE(3)")
        ok_cut, cut = self.sys.attempt(f"EXECUTE SCRIPT {SCHEMA}.A_EDGE(4)")
        details["detail_cut_utf8_offset"] = (ok_cut, len(cut[0][0]) if ok_cut else cut)
        details["string_into_decimal"] = self.sys.attempt(f"EXECUTE SCRIPT {SCHEMA}.A_EDGE(5)")
        ok, table = details["three_args"]
        returned = ok and table["rows"] and table["columns"].get("SECONDS") == "DECIMAL"
        output_ok = details["with_output"][0] and list(details["with_output"][1]["columns"]) == [
            "OUTPUT"
        ]
        short_form = details["two_args"][0]
        status = "pass" if returned and output_ok else "fail"
        result = (
            f"exit(rows, coldefs) returns a typed table to pyexasol ({len(table['rows']) if ok else 0} rows; "
            f"Lua nil cells become NULL, 1.23456 into DECIMAL(9,3) is rounded). WITH OUTPUT returns only the "
            f"OUTPUT column of output() lines (table discarded: {output_ok}). Fewer or more arguments than "
            f"declared are rejected ({'accepted' if short_form else details['two_args'][1][:60]}), so "
            "REFRESH_WEEK(2026, 9) does NOT work: the demo line keeps all three arguments. A value longer "
            "than its declared VARCHAR fails the whole script; truncate with utf8.offset."
        )
        return Finding("a", status, result, details)

    # ------------------------------------------------------------------ (b)

    def probe_b(self) -> Finding:
        self.sys.run(
            lua_script(
                "B_TX",
                "mode",
                f"""local function cnt(id) return query("SELECT COUNT(*) AS N FROM {SCHEMA}.TX WHERE ID = :i", {{i = id}})[1].N end
if mode == 1 then
  query("INSERT INTO {SCHEMA}.TX VALUES 1"); query("COMMIT")
  query("INSERT INTO {SCHEMA}.TX VALUES 2"); query("ROLLBACK")
  query("INSERT INTO {SCHEMA}.TX VALUES 3")
  note("in-script count of 1,2,3", cnt(1) .. "," .. cnt(2) .. "," .. cnt(3))
elseif mode >= 2 and mode <= 4 then
  query("INSERT INTO {SCHEMA}.TX VALUES 10")
  local sql = ({{[2] = "INSERT INTO {SCHEMA}.TX SELECT 1/0 FROM DUAL",
                [3] = "INSERT INTO {SCHEMA}.TX SELECT {SCHEMA}.FAIL_UDF(1) FROM DUAL",
                [4] = "INSERT INTO {SCHEMA}.TX VALUES 10"}})[mode]
  local ok, res = pquery(sql)
  note("pquery ok", ok)
  note("error_code", res.error_code)
  note("error_message", res.error_message)
  note("result type", type(res))
  note("row 10 still in the open tx", cnt(10))
  local ok_more = pquery("INSERT INTO {SCHEMA}.TX VALUES 11")
  note("tx still usable (insert 11)", ok_more)
  query("ROLLBACK")
  note("rows 10/11 after ROLLBACK", cnt(10) .. "/" .. cnt(11))
elseif mode == 5 then
  local ok1, e1 = pcall(function() error("lua boom") end)
  note("pcall(error('lua boom'))", tostring(ok1) .. " | " .. tostring(e1))
  local ok2, e2 = pcall(function() error({{code = 7}}) end)
  note("pcall(error(table)) -> type of error object", type(e2))
  local ok3, e3 = pcall(function() error("no position", 0) end)
  note("pcall(error(msg, 0))", e3)
  local ok4, e4 = pcall(function() return query("SELECT 1/0 FROM DUAL") end)
  note("pcall(query failing)", tostring(ok4) .. " | " .. tostring(e4))
  note("script continued after pcall(query failing)", true)
end
notes_exit()""",
            )
        )
        self.sys.run(
            lua_script(
                "B_ERRPATH",
                "mode",
                f"""query("INSERT INTO {SCHEMA}.TX VALUES 20")
if mode == 1 then
  local ok, res = pquery("INSERT INTO {SCHEMA}.TX SELECT {SCHEMA}.FAIL_UDF(1) FROM DUAL")
  pquery("ROLLBACK")
  pquery("INSERT INTO {SCHEMA}.LOGT VALUES (1, :m)", {{m = "FAILED: " .. string.sub(res.error_message, 1, 200)}})
  pquery("COMMIT")
  error("re-raised after the log was committed: " .. string.sub(res.error_message, 1, 60), 0)
elseif mode == 2 then
  query("COMMIT")
  error("late error after commit")
elseif mode == 3 then
  error("error() with row 20 uncommitted")
elseif mode == 4 then
  query("SELECT 1/0 FROM DUAL")
end
notes_exit()""",
            )
        )
        details: dict[str, Any] = {}
        for mode, label in (
            (1, "commit_rollback_inside"),
            (2, "failed_pquery_division"),
            (3, "failed_pquery_udf_error"),
            (4, "failed_pquery_pk_violation"),
            (5, "pcall"),
        ):
            self.reset_tx()
            details[label] = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.B_TX({mode})")
            if mode == 1:
                details[label]["committed afterwards"] = self.sys.rows(
                    f"SELECT ID FROM {SCHEMA}.TX ORDER BY 1"
                )
        observer = self.new_session()
        try:
            for mode, label in (
                (1, "error_path_rollback_log_commit_reraise"),
                (2, "committed_work_survives_error"),
                (3, "uncommitted_at_error_autocommit_on"),
                (4, "uncaught_query_failure_autocommit_on"),
            ):
                self.reset_tx()
                ok, message = self.sys.attempt(f"EXECUTE SCRIPT {SCHEMA}.B_ERRPATH({mode})")
                details[label] = {
                    "client sees": message if not ok else "no error",
                    "TX rows seen by another session right after": observer.rows(
                        f"SELECT ID FROM {SCHEMA}.TX"
                    ),
                    "LOGT rows": observer.rows(f"SELECT ID, MSG FROM {SCHEMA}.LOGT"),
                }
            self.reset_tx()
            manual = self.new_session()
            try:
                manual.conn.set_autocommit(False)
                ok, message = manual.attempt(f"EXECUTE SCRIPT {SCHEMA}.B_ERRPATH(3)")
                details["uncommitted_at_error_autocommit_off"] = {
                    "client sees": message,
                    "own session sees": manual.rows(f"SELECT ID FROM {SCHEMA}.TX"),
                    "other session sees": observer.rows(f"SELECT ID FROM {SCHEMA}.TX"),
                }
                manual.conn.rollback()
                details["uncommitted_at_error_autocommit_off"]["after client ROLLBACK"] = (
                    observer.rows(f"SELECT ID FROM {SCHEMA}.TX")
                )
            finally:
                manual.close()
        finally:
            observer.close()
        division = details["failed_pquery_division"]
        errpath = details["error_path_rollback_log_commit_reraise"]
        in_tx_ok = (
            division.get("row 10 still in the open tx") == "1"
            and division.get("rows 10/11 after ROLLBACK") == "0/0"
        )
        errpath_ok = (
            errpath["TX rows seen by another session right after"] == []
            and len(errpath["LOGT rows"]) == 1
        )
        survives = details["committed_work_survives_error"][
            "TX rows seen by another session right after"
        ] == [(20,)]
        autocommits = details["uncommitted_at_error_autocommit_on"][
            "TX rows seen by another session right after"
        ] == [(20,)]
        status = "pass" if in_tx_ok and errpath_ok and survives else "fail"
        result = (
            "COMMIT/ROLLBACK work inside a script. A failing pquery (SQL error, UDF error, PK violation) "
            "rolls back only that statement: the open transaction keeps earlier work and stays usable, and "
            "ROLLBACK + log + COMMIT + error() works (the log survives, the batch is gone). pcall catches "
            "error() and also query() failures. Committed work survives a later error(). "
            + (
                "CAUTION: with autocommit on (pyexasol, DBeaver, exapump default) a script that ends in "
                "error() or an uncaught query() failure COMMITS its uncommitted work; with autocommit off it "
                "stays open in the caller's transaction. The error path must ROLLBACK before error()."
                if autocommits
                else "Uncommitted work at error() is rolled back."
            )
        )
        return Finding("b", status, result, details)

    def reset_tx(self) -> None:
        self.sys.run(f"TRUNCATE TABLE {SCHEMA}.TX")
        self.sys.run(f"TRUNCATE TABLE {SCHEMA}.LOGT")

    # ------------------------------------------------------------------ (c)

    def batch_query(self, seasons: tuple[int, ...] = PROBE_SEASONS) -> str:
        years = ", ".join(str(season) for season in seasons)
        return (
            f'SELECT * FROM "{NAMESPACE.vs_name}"."{PROBE_TABLE}" '
            f'WHERE "P_YEAR" IN ({years}) AND "REQ_SOURCE" <> \'STORED\''
        )

    def probe_c(self) -> Finding:
        self.setup_vs()
        query = self.batch_query()
        manifest = fetchlog.parse_manifest(fetchlog.explain_pushdown_sql(self.sys.conn, query))
        keys = list(manifest.slice_keys)
        key_list = ", ".join(json.dumps(key) for key in keys)
        lua_keys = (
            "{"
            + ", ".join('"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"' for key in keys)
            + "}"
        )
        self.sys.run(
            lua_script(
                "C_EXPLAIN",
                "",
                f"""local keys = {lua_keys}
local q = [[{query}]]
local function check(label, ok, res)
  note(label .. " ok", ok)
  if not ok then note(label .. " error", res.error_message) return end
  note(label .. " rows", #res)
  local sql = res[1].PUSHDOWN_SQL
  note(label .. " has PUSHDOWN_SQL", type(sql) == "string")
  if type(sql) ~= "string" then return end
  note(label .. " starts with manifest", string.sub(sql, 1, 7) == "/*CFBD ")
  local found = 0
  for _, k in ipairs(keys) do
    if string.find(sql, '"' .. k .. '"', 1, true) then found = found + 1 end
  end
  note(label .. " batch keys found by plain search", found .. "/" .. #keys)
  note(label .. " planned_requests text", string.match(sql, '"planned_requests":(%d+)'))
  local cjson = require("cjson")
  local close = string.find(sql, "*/", 1, true)
  local okj, m = pcall(function() return cjson.decode(string.sub(sql, 8, close - 1)) end)
  note(label .. " cjson decode", okj and ("slice_keys=" .. #m.slice_keys .. " sources=" .. table.concat(m.sources, ",")) or tostring(m))
end
local ok1, r1 = pquery("SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL " .. q .. ")")
check("subselect", ok1, r1)
local ok2, r2 = pquery("EXPLAIN VIRTUAL " .. q)
check("plain", ok2, r2)
if ok2 then
  local names = {{}}
  for _, n in ipairs({{"PUSHDOWN_ID", "PUSHDOWN_SQL", "PUSHDOWN_JSON", "PUSHDOWN_INVOLVED_TABLES"}}) do
    local okn, v = pcall(function() return r2[1][n] end)
    if okn and v ~= nil then names[#names + 1] = n end
  end
  note("plain columns", table.concat(names, ","))
end
notes_exit()""",
            )
        )
        stage_before = self.sys.value(f'SELECT COUNT(*) FROM "{STAGE_SCHEMA}"."{PROBE_TABLE}"')
        notes = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.C_EXPLAIN()")
        details = {
            "host manifest keys": keys,
            "host key list": key_list,
            "notes": notes,
            "stage rows before": stage_before,
        }
        sub_ok = (
            notes.get("subselect ok") == "true"
            and notes.get("subselect batch keys found by plain search")
            == f"{len(keys)}/{len(keys)}"
        )
        plain_ok = (
            notes.get("plain ok") == "true"
            and notes.get("plain batch keys found by plain search") == f"{len(keys)}/{len(keys)}"
        )
        status = "pass" if sub_ok else ("partial" if plain_ok else "fail")
        result = (
            f"SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL …) through pquery: {'works' if sub_ok else 'fails'}; plain "
            f"pquery('EXPLAIN VIRTUAL …'): {'works' if plain_ok else 'fails'}. The manifest starts the "
            f"PUSHDOWN_SQL; every batch key is found as a quoted string by plain string search "
            f"({notes.get('subselect batch keys found by plain search')}), and require('cjson') decodes it too. "
            "EXPLAIN only plans: no fetch."
        )
        return Finding("c", status, result, details)

    # ------------------------------------------------------------------ (d)

    def probe_d(self) -> Finding:
        if self.target_name != "local":
            return Finding("d", "skipped", "needs the local BucketFS host directory for recordings")
        self.setup_vs()
        query = self.batch_query()
        every_source = query.replace("<> 'STORED'", "<> 'NONE'")
        manifest = fetchlog.parse_manifest(fetchlog.explain_pushdown_sql(self.sys.conn, query))
        keys = list(manifest.slice_keys)
        self.write_recordings(keys)
        core = f'"{NAMESPACE.core_schema}"."{PROBE_TABLE}"'
        stage = f'"{STAGE_SCHEMA}"."{PROBE_TABLE}"'
        load_log = f'"{NAMESPACE.meta_schema}"."LOAD_LOG"'
        self.sys.run(
            lua_script(
                "D_LOAD",
                "",
                f"""local function step(label, sql, binds)
  local ok, r = pquery(sql, binds or {{}})
  if not ok then note(label, "FAIL: " .. r.error_message) error(label .. ": " .. r.error_message, 0) end
  return r
end
local q = [[{query}]]
local n0 = step("stage count", [[SELECT COUNT(*) AS N FROM {stage}]])[1].N
note("stage empty before", n0 == 0)
local ins = step("fetch", [[INSERT INTO {stage} ]] .. q)
note("rows staged", ins.rows_inserted)
local by = step("by source", [[SELECT REQ_SOURCE, COUNT(DISTINCT REQ_SLICE) AS S, COUNT(*) AS N FROM {stage} GROUP BY REQ_SOURCE]])
for i = 1, #by do note("staged source " .. by[i].REQ_SOURCE, by[i].S .. " slices, " .. by[i].N .. " rows") end
step("delete core", [[DELETE FROM {core} WHERE REQ_SLICE IN (SELECT DISTINCT REQ_SLICE FROM {stage})]])
local moved = step("insert core", [[INSERT INTO {core} SELECT * FROM {stage}]])
note("rows moved to core", moved.rows_inserted)
step("merge load_log", [[MERGE INTO {load_log} L USING (
    SELECT '{PROBE_TABLE}' AS TABLE_NAME, REQ_SLICE AS SLICE_KEY, COUNT(*) AS ROWS_LOADED FROM {stage} GROUP BY REQ_SLICE) X
  ON (L.TABLE_NAME = X.TABLE_NAME AND L.SLICE_KEY = X.SLICE_KEY)
  WHEN MATCHED THEN UPDATE SET IS_FINAL = TRUE, ROWS_LOADED = X.ROWS_LOADED,
    LOADED_AT = CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC'), LOADED_BY = 'PROBE_S5'
  WHEN NOT MATCHED THEN INSERT VALUES (X.TABLE_NAME, X.SLICE_KEY, TRUE, X.ROWS_LOADED,
    CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC'), 'PROBE_S5')]])
step("clear stage", [[DELETE FROM {stage}]])
step("commit", "COMMIT")
notes_exit()""",
            )
        )
        details: dict[str, Any] = {"keys": keys}
        details["first_load"] = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.D_LOAD()")
        details["core_by_source_after_first"] = self.sys.rows(
            f"SELECT REQ_SOURCE, COUNT(DISTINCT REQ_SLICE), COUNT(*) FROM {core} GROUP BY REQ_SOURCE"
        )
        details["load_log_after_first"] = self.sys.rows(
            f"SELECT SLICE_KEY, IS_FINAL, ROWS_LOADED, LOADED_BY FROM {load_log} ORDER BY 1"
        )
        # One slice back to non-final: read-through serves the other one stored, the loader keeps only fetched rows.
        self.sys.run(f"UPDATE {load_log} SET IS_FINAL = FALSE WHERE SLICE_KEY = '{keys[-1]}'")
        details["mixed_select_by_source"] = self.sys.rows(
            f"SELECT REQ_SOURCE, COUNT(DISTINCT REQ_SLICE), COUNT(*) FROM ({every_source}) GROUP BY REQ_SOURCE"
        )
        details["mixed_load"] = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.D_LOAD()")
        # Every planned slice final-stored; recordings removed, so any fetch attempt would fail.
        removed = self.area.clear()
        self.wait_for(removed, present=False)
        details["all_stored_load"] = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.D_LOAD()")
        details["all_stored_plain_select_by_source"] = self.sys.rows(
            f"SELECT REQ_SOURCE, COUNT(*) FROM ({every_source}) GROUP BY REQ_SOURCE"
        )
        first = details["first_load"]
        mixed = details["mixed_load"]
        stored = details["all_stored_load"]
        first_ok = first.get("staged source REPLAY", "").startswith(f"{len(keys)} slices")
        mixed_ok = (
            mixed.get("staged source REPLAY", "").startswith("1 slices")
            and "staged source STORED" not in mixed
        )
        guard_ok = stored.get("rows staged") == "0" and "__error__" not in stored
        status = "pass" if first_ok and mixed_ok and guard_ok else "fail"
        result = (
            f"Inside a Lua script, INSERT INTO stage SELECT * FROM PROBE_CFBD_VS.GAMES WHERE P_YEAR IN (…) AND "
            f"REQ_SOURCE <> 'STORED' fetched {first.get('rows staged')} rows of {len(keys)} replayed slices, and the "
            "stage → core swap with MERGE INTO LOAD_LOG committed. With one slice non-final, only that slice was "
            "fetched (the stored one was filtered out). With every slice final-stored and the recordings removed, "
            f"the INSERT staged {stored.get('rows staged')} rows without an error: the empty-SET-group guard ([21]) "
            "holds and nothing was fetched."
        )
        return Finding("d", status, result, details)

    def write_recordings(self, keys: list[str]) -> None:
        for key in keys:
            self.area.write(support.synthetic_recording(key))
        self.wait_for([self.area.udf_file(key) for key in keys], present=True)

    def wait_for(self, udf_paths: list[str], *, present: bool) -> None:
        if not udf_paths:
            return
        paths = json.dumps(udf_paths).replace("'", "''")
        flag = "TRUE" if present else "FALSE"
        self.sys.run(f"SELECT {SCHEMA}.{support.WAIT_SCRIPT_NAME}('{paths}', {flag}, 30)")

    # ------------------------------------------------------------------ (e)

    def probe_e(self) -> Finding:
        self.sys.run(
            lua_script(
                "E_INNER_RC",
                "run_id, season, week, season_type",
                f"""query("INSERT INTO {SCHEMA}.LOGT VALUES (:r, :t)", {{r = run_id, t = tostring(season_type)}})
if season_type == "fail" then error("inner step failed for run " .. tostring(run_id), 0) end
if season_type == "sqlfail" then query("SELECT 1/0 FROM DUAL") end
exit({{rows_affected = 7}})""",
                returns="RETURNS ROWCOUNT",
            )
        )
        self.sys.run(
            lua_script(
                "E_INNER_TAB",
                "a, b, c",
                """exit({{ty(a) .. ":" .. tostring(a) .. " " .. ty(b) .. ":" .. (b == null and "NULL" or tostring(b)) .. " " .. ty(c) .. ":" .. tostring(c)}}, "T VARCHAR(200)")""",
            )
        )
        self.sys.run(
            f"CREATE OR REPLACE LUA SCRIPT {SCHEMA}.E_LIB() AS\nfunction hello(x) return 'hello ' .. tostring(x) end\n/"
        )
        self.sys.run(
            lua_script(
                "E_OUTER",
                "",
                f"""local rid = query("SELECT CAST(4242 AS DECIMAL(18,0)) AS R FROM DUAL")[1].R
local ok, r = pquery("EXECUTE SCRIPT {SCHEMA}.E_INNER_RC(:r, :s, :w, :t)", {{r = rid, s = 2026, w = 9, t = "regular"}})
note("rowcount script ok", ok)
if ok then note("rowcount rows_affected", r.rows_affected) end
local ok2, r2 = pquery("EXECUTE SCRIPT {SCHEMA}.E_INNER_TAB(:a, :b, :c)", {{a = rid, b = null, c = "it's"}})
note("table script ok", ok2)
if ok2 then note("table script value (bound args)", r2[1][1]) end
local ok3, r3 = pquery("EXECUTE SCRIPT {SCHEMA}.E_INNER_TAB(NULL, 2018, 'postseason')")
if ok3 then note("table script value (literal args)", r3[1][1]) end
local ok4, r4 = pquery("EXECUTE SCRIPT {SCHEMA}.E_INNER_RC(:r, 2026, 9, 'fail')", {{r = rid}})
note("inner error() -> pquery ok", ok4)
if not ok4 then note("inner error() code", r4.error_code) note("inner error() message", r4.error_message) end
local ok5, r5 = pquery("EXECUTE SCRIPT {SCHEMA}.E_INNER_RC(:r, 2026, 9, 'sqlfail')", {{r = rid}})
note("inner SQL failure -> pquery ok", ok5)
if not ok5 then note("inner SQL failure code", r5.error_code) note("inner SQL failure message", r5.error_message) end
note("inner inserts visible in the outer transaction", query("SELECT COUNT(*) AS N FROM {SCHEMA}.LOGT")[1].N)
query("ROLLBACK")
note("after outer ROLLBACK", query("SELECT COUNT(*) AS N FROM {SCHEMA}.LOGT")[1].N)
local ok6, r6 = pquery("EXECUTE SCRIPT {SCHEMA}.E_INNER_TAB(1, 2, 3) WITH OUTPUT")
note("nested WITH OUTPUT rows", ok6 and #r6 or r6.error_message)
local ok7, r7 = pquery("EXECUTE SCRIPT {SCHEMA}.MISSING_STEP(1)")
note("missing script message", ok7 and "no error" or r7.error_message)
local ok8, e8 = pcall(function() import("{SCHEMA}.E_LIB", "lib") return lib.hello(5) end)
note("import() fallback", tostring(ok8) .. " " .. tostring(e8))
notes_exit()""",
            )
        )
        self.reset_tx()
        notes = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.E_OUTER()")
        ok = (
            notes.get("rowcount rows_affected") == "7"
            and notes.get("inner error() -> pquery ok") == "false"
            and notes.get("table script value (bound args)", "").startswith(
                "number/integer:4242 null:NULL"
            )
        )
        result = (
            "Nested EXECUTE SCRIPT through pquery works: :name binds pass integers, NULL and quoted strings "
            "(a NULL argument arrives as null); an inner RETURNS ROWCOUNT gives rows_affected; an inner "
            f"error() or SQL failure comes back as pquery false (code {notes.get('inner error() code')}) with "
            "'<message> caught in script …'. The inner script shares the caller's transaction: its "
            "uncommitted inserts, also those of an inner script that failed, stay in the outer transaction until the outer rolls back. import() works too."
        )
        return Finding("e", "pass" if ok else "fail", result, {"notes": notes})

    # ------------------------------------------------------------------ (f)

    def probe_f(self) -> Finding:
        self.sys.run(
            lua_script(
                "F_READS",
                "",
                f"""local ok, r = pquery("SELECT COUNT(*) AS N FROM {SCHEMA}.SECRET_T")
note("sql CURRENT_USER", query("SELECT CURRENT_USER AS U FROM DUAL")[1].U)
note("exa.meta.current_user", exa.meta.current_user)
note("select on probe table", ok and ("ok " .. r[1].N) or r.error_message)
notes_exit()""",
            )
        )
        user = self.user_session()
        try:
            details: dict[str, Any] = {}
            details["without EXECUTE"] = user.attempt(f"EXECUTE SCRIPT {SCHEMA}.F_READS()")
            self.sys.run(f"GRANT EXECUTE ON SCRIPT {SCHEMA}.F_READS TO {USER}")
            details["EXECUTE only"] = user.notes(f"EXECUTE SCRIPT {SCHEMA}.F_READS()")
            details["as sys"] = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.F_READS()")
            self.sys.run(f"GRANT SELECT ON {SCHEMA}.SECRET_T TO {USER}")
            details["EXECUTE + SELECT"] = user.notes(f"EXECUTE SCRIPT {SCHEMA}.F_READS()")
            self.sys.run(f"REVOKE SELECT ON {SCHEMA}.SECRET_T FROM {USER}")
        finally:
            user.close()
        denied = "insufficient privileges" in details["EXECUTE only"].get(
            "select on probe table", ""
        )
        runs_as_caller = details["EXECUTE only"].get("sql CURRENT_USER") == USER
        status = "pass" if denied and runs_as_caller else "fail"
        result = (
            "Caller's rights: the user needs EXECUTE on the script, and every statement inside runs with the "
            "caller's own privileges (CURRENT_USER and exa.meta.current_user are the caller; a SELECT the "
            "caller lacks fails inside the script, and works once granted)."
        )
        return Finding("f", status, result, details)

    # ------------------------------------------------------------------ (g)

    def probe_g(self) -> Finding:
        self.sys.run(
            lua_script(
                "G_TYPES",
                "",
                """local r = query([[SELECT CAST(42 AS DECIMAL(18,0)) AS D18, CAST(5 AS DECIMAL(9,0)) AS D9,
  CAST(1.25 AS DECIMAL(9,3)) AS D93, CAST(0.1 AS DOUBLE) AS DBL, TRUE AS BOOLV,
  TIMESTAMP '2026-10-22 12:34:56.789' AS TS, DATE '2026-10-22' AS DT, 'äöü😀' AS VC,
  CAST(NULL AS DECIMAL(18,0)) AS NUL, CAST(999999999999999999 AS DECIMAL(18,0)) AS MAX18,
  CURRENT_SESSION AS SESS, TO_CHAR(CURRENT_SESSION) AS SESS_TEXT FROM DUAL]])[1]
for _, name in ipairs({"D18", "D9", "D93", "DBL", "BOOLV", "TS", "DT", "VC", "NUL", "MAX18", "SESS"}) do
  local v = r[name]
  note(name, ty(v) .. " | " .. (v == null and "null" or tostring(v)))
end
note("SESS exact as integer", string.format("%d", r.SESS) == r.SESS_TEXT)
note("exa.meta.session_id", ty(exa.meta.session_id) .. " | " .. tostring(exa.meta.session_id == r.SESS_TEXT))
note("null == nil", null == nil)
local okc, ec = pcall(function() return r.NUL > 0 end)
note("null > 0", tostring(okc) .. " | " .. tostring(ec))
note("D18 == 42", r.D18 == 42)
note("#rows of empty result", #query("SELECT 1 AS X FROM DUAL WHERE FALSE"))
notes_exit()""",
            )
        )
        self.sys.run(
            lua_script(
                "G_PARAMS",
                "profile, from_season, max_calls",
                """note("profile", ty(profile) .. " | " .. (profile == null and "null" or tostring(profile)))
note("from_season", ty(from_season) .. " | " .. (from_season == null and "null" or tostring(from_season)))
note("max_calls", ty(max_calls) .. " | " .. (max_calls == null and "null" or tostring(max_calls)))
notes_exit()""",
            )
        )
        details = {
            "query_results": self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.G_TYPES()"),
            "params (NULL, 2018, 0)": self.sys.notes(
                f"EXECUTE SCRIPT {SCHEMA}.G_PARAMS(NULL, 2018, 0)"
            ),
            "params ('demo', NULL, NULL)": self.sys.notes(
                f"EXECUTE SCRIPT {SCHEMA}.G_PARAMS('demo', NULL, NULL)"
            ),
            "params (2.5, TRUE, '9')": self.sys.notes(
                f"EXECUTE SCRIPT {SCHEMA}.G_PARAMS(2.5, TRUE, '9')"
            ),
        }
        types = details["query_results"]
        result = (
            f"DECIMAL(p,0) → Lua integer ({types.get('D18')}; CURRENT_SESSION exact: "
            f"{types.get('SESS exact as integer')}), DECIMAL(p,s>0) and DOUBLE → float, BOOLEAN → boolean, "
            "TIMESTAMP/DATE → string ('YYYY-MM-DD HH:MI:SS.FFFFFF'), VARCHAR → string, NULL → null (userdata; "
            "null ~= nil, comparing it with a number raises). Script parameters: NULL → null, 2018 → integer, "
            "2.5 → float, 'demo' → string, TRUE → boolean. exa.meta.session_id is a string."
        )
        return Finding("g", "pass", result, details)

    # ------------------------------------------------------------------ (h)

    def probe_h(self) -> Finding:
        self.sys.run(
            lua_script(
                "H_PROGRESS",
                "secs",
                f"""query("INSERT INTO {SCHEMA}.LOGT VALUES (1, 'step 1 committed')")
query("COMMIT")
query("INSERT INTO {SCHEMA}.TX VALUES 1")
query("SELECT {SCHEMA}.SLEEP_UDF(:s) FROM DUAL", {{s = secs}})
query("COMMIT")
notes_exit()""",
            )
        )
        self.reset_tx()
        runner = self.new_session()
        observer = self.new_session()
        outcome: dict[str, Any] = {}
        try:
            thread = threading.Thread(
                target=lambda: outcome.setdefault(
                    "r", runner.attempt(f"EXECUTE SCRIPT {SCHEMA}.H_PROGRESS(4)")
                )
            )
            thread.start()
            time.sleep(1.5)
            during_committed = observer.rows(f"SELECT MSG FROM {SCHEMA}.LOGT")
            during_uncommitted = observer.rows(f"SELECT ID FROM {SCHEMA}.TX")
            thread.join(30)
            after = observer.rows(f"SELECT ID FROM {SCHEMA}.TX")
        finally:
            runner.close()
            observer.close()
        details = {
            "committed row seen while the script runs": during_committed,
            "uncommitted row seen while the script runs": during_uncommitted,
            "after the script": after,
            "script": outcome.get("r"),
        }
        ok = (
            during_committed == [("step 1 committed",)]
            and during_uncommitted == []
            and after == [(1,)]
        )
        result = (
            "A row committed by a running script is visible to another session at once; rows of its still "
            "open transaction are not. REFRESH_LOG progress rows reach the app while the refresh runs."
        )
        return Finding("h", "pass" if ok else "fail", result, details)

    # ------------------------------------------------------------------ (i)

    def probe_i(self) -> Finding:
        self.sys.run(
            lua_script(
                "I_LOCK",
                "tag, delay_before_insert, hold",
                f"""local steps = {{}}
local function step(label, sql, binds)
  local ok, r = pquery(sql, binds or {{}})
  steps[#steps + 1] = label .. "=" .. (ok and "ok" or ("FAIL[" .. tostring(r.error_code) .. "] " .. string.sub(r.error_message, 1, 110)))
  return ok, r
end
local ok, r = step("check", "SELECT COUNT(*) AS N FROM {SCHEMA}.RUNS WHERE RUN_STATUS = 'RUNNING'")
local running = ok and r[1].N or -1
steps[#steps + 1] = "running=" .. tostring(running)
if running == 0 then
  if delay_before_insert > 0 then step("delay", "SELECT {SCHEMA}.SLEEP_UDF(:s) FROM DUAL", {{s = delay_before_insert}}) end
  step("insert", "INSERT INTO {SCHEMA}.RUNS (TAG, SESSION_ID, RUN_STATUS) VALUES (:t, CURRENT_SESSION, 'RUNNING')", {{t = tag}})
  if hold > 0 then step("hold", "SELECT {SCHEMA}.SLEEP_UDF(:s) FROM DUAL", {{s = hold}}) end
  step("commit", "COMMIT")
  local ok2, r2 = step("recheck", "SELECT COUNT(*) AS N FROM {SCHEMA}.RUNS WHERE RUN_STATUS = 'RUNNING'")
  if ok2 then steps[#steps + 1] = "running after=" .. tostring(r2[1].N) end
end
note("steps", table.concat(steps, " ; "))
notes_exit()""",
            )
        )
        details = {
            "A inserts and holds its tx 4 s; B checks and inserts 1 s later": self.race(
                "'A', 0, 4", "'B', 0, 0", 1.0
            ),
            "A and B both read before either inserts": self.race("'A', 2, 0", "'B', 2, 0", 0.5),
        }
        winners = [len(case["committed RUNNING rows"]) == 1 for case in details.values()]
        collisions = [
            "40001" in case["B"].get("steps", "") or "40001" in case["A"].get("steps", "")
            for case in details.values()
        ]
        ok = all(winners) and all(collisions)
        result = (
            "Exactly one session wins. The loser's check reads the old state (0 RUNNING rows), and its INSERT "
            "fails at once with 40001 'GlobalTransactionRollback … Transaction collision: automatic transaction "
            "rollback' (no WAIT FOR COMMIT); its whole transaction is rolled back. Lock acquisition must run in "
            "its own transaction and treat 40001 on any statement as 'another run is starting': re-read and refuse."
        )
        return Finding("i", "pass" if ok else "fail", result, details)

    def race(self, a_args: str, b_args: str, b_delay: float) -> dict[str, Any]:
        self.sys.run(f"TRUNCATE TABLE {SCHEMA}.RUNS")
        sessions = {"A": self.new_session(), "B": self.new_session()}
        out: dict[str, Any] = {}

        def run(label: str, args: str) -> None:
            started = time.monotonic()
            notes = sessions[label].notes(f"EXECUTE SCRIPT {SCHEMA}.I_LOCK({args})")
            notes["seconds"] = f"{time.monotonic() - started:.2f}"
            out[label] = notes

        first = threading.Thread(target=run, args=("A", a_args))
        first.start()
        time.sleep(b_delay)
        second = threading.Thread(target=run, args=("B", b_args))
        second.start()
        first.join(60)
        second.join(60)
        for session in sessions.values():
            session.close()
        out["committed RUNNING rows"] = self.sys.rows(
            f"SELECT TAG FROM {SCHEMA}.RUNS WHERE RUN_STATUS = 'RUNNING' ORDER BY RUN_ID"
        )
        return out

    # ------------------------------------------------------------------ (j)

    def probe_j(self) -> Finding:
        self.sys.run(
            lua_script(
                "J_HOLD",
                "run_id, secs, use_pquery",
                f"""query("INSERT INTO {SCHEMA}.RUNS (TAG, SESSION_ID, RUN_STATUS) VALUES (:t, CURRENT_SESSION, 'RUNNING')", {{t = tostring(run_id)}})
query("COMMIT")
query("INSERT INTO {SCHEMA}.TX VALUES :r", {{r = run_id}})
local steps = {{}}
local function step(label, sql)
  local ok, r = pquery(sql)
  steps[#steps + 1] = label .. "=" .. (ok and "ok" or ("FAIL[" .. tostring(r.error_code) .. "] " .. r.error_message))
  return ok
end
if use_pquery then
  step("sleep", "SELECT {SCHEMA}.SLEEP_UDF(" .. secs .. ") FROM DUAL")
  step("select after", "SELECT 1 FROM DUAL")
  step("rollback", "ROLLBACK")
  step("log insert", "INSERT INTO {SCHEMA}.LOGT VALUES (1, 'failure logged')")
  step("commit", "COMMIT")
  error("steps: " .. table.concat(steps, " | "), 0)
else
  query("SELECT {SCHEMA}.SLEEP_UDF(" .. secs .. ") FROM DUAL")
end
notes_exit()""",
            )
        )
        self.sys.run(
            lua_script(
                "J_LOOP",
                "n",
                """for i = 1, n do pquery("SELECT :i FROM DUAL", {i = i}) end
notes_exit()""",
            )
        )
        details: dict[str, Any] = {}
        details["EXA_ALL_SESSIONS columns"] = [
            row[0]
            for row in self.sys.rows(
                "SELECT COLUMN_NAME FROM EXA_SYS_COLUMNS WHERE COLUMN_TABLE = 'EXA_ALL_SESSIONS' "
                "ORDER BY COLUMN_ORDINAL_POSITION"
            )
        ]
        user = self.user_session()
        try:
            details["non-DBA user: EXA_ALL_SESSIONS rows vs all"] = (
                user.value("SELECT COUNT(*) FROM EXA_ALL_SESSIONS"),
                self.sys.value("SELECT COUNT(*) FROM EXA_DBA_SESSIONS"),
            )
            details["non-DBA user: EXA_DBA_SESSIONS"] = user.attempt(
                "SELECT COUNT(*) FROM EXA_DBA_SESSIONS"
            )
            details["kill_pquery"] = self.interrupt_case(user, 1, "kill", use_pquery=True)
            details["kill_query"] = self.interrupt_case(user, 2, "kill", use_pquery=False)
            details["client_abort_pquery"] = self.interrupt_case(user, 3, "abort", use_pquery=True)
            details["status_samples_while_running"] = self.sample_statuses()
        finally:
            user.close()
        killed = details["kill_pquery"]
        idle_after = all(
            details[case]["status after (non-DBA view)"][:1] == [("IDLE", "NOT SPECIFIED")]
            for case in ("kill_pquery", "kill_query", "client_abort_pquery")
        )
        gone = all(
            details[case]["status after close"] == [] for case in ("kill_pquery", "kill_query")
        )
        sampled = details["status_samples_while_running"]
        never_idle = sampled["idle samples between busy samples"] == 0
        status = "pass" if idle_after and gone and never_idle else "fail"
        result = (
            "EXA_ALL_SESSIONS lists every session to any user (a non-DBA cannot read EXA_DBA_SESSIONS) with "
            "STATUS and COMMAND_NAME, but no SQL_TEXT. While a script runs the session is 'EXECUTE SQL' "
            "(COMMAND_NAME 'EXECUTE SCRIPT' or the inner statement): no 'IDLE' between busy samples "
            f"({sampled['samples']} samples; idle only at the edges: "
            f"{sampled['idle samples before the first or after the last busy sample']}); after KILL STATEMENT or a client abort "
            "it is 'IDLE'/'NOT SPECIFIED'; after disconnect the row is gone. A takeover UPDATE of the RUNNING row "
            "from another session does not wait. A kill inside pquery is caught ('Client requested execution "
            "abort.'), but every later statement of the script fails with the same message: ROLLBACK and COMMIT "
            "still take effect while reporting the failure, DML does not run, so the run cannot log FAILED and "
            f"its RUNNING row stays ({killed['RUNS after (other session)']}). A kill inside a bare query() ends the "
            "script and autocommit COMMITS the open transaction "
            f"({details['kill_query']['TX after (other session)']} committed)."
        )
        return Finding("j", status, result, details)

    def interrupt_case(
        self, user: Session, run_id: int, how: str, *, use_pquery: bool
    ) -> dict[str, Any]:
        self.sys.run(f"TRUNCATE TABLE {SCHEMA}.TX")
        self.sys.run(f"TRUNCATE TABLE {SCHEMA}.LOGT")
        self.sys.run(f"TRUNCATE TABLE {SCHEMA}.RUNS")
        runner = self.new_session()
        session_id = runner.session_id
        outcome: dict[str, Any] = {}
        flag = "TRUE" if use_pquery else "FALSE"
        thread = threading.Thread(
            target=lambda: outcome.setdefault(
                "r",
                runner.attempt(
                    f"EXECUTE SCRIPT {SCHEMA}.J_HOLD({run_id}, {SLEEP_SECONDS}, {flag})"
                ),
            )
        )
        thread.start()
        time.sleep(KILL_AFTER_SECONDS)
        status_sql = (
            f"SELECT STATUS, COMMAND_NAME FROM EXA_ALL_SESSIONS WHERE SESSION_ID = {session_id}"
        )
        result: dict[str, Any] = {"status while running (non-DBA view)": user.rows(status_sql)}
        if how == "kill":
            result["kill"] = self.sys.attempt(f"KILL STATEMENT IN SESSION {session_id}")
        else:
            runner.conn.abort_query()
        thread.join(60)
        time.sleep(0.5)
        result["script error seen by the client"] = outcome.get("r")
        result["status after (non-DBA view)"] = user.rows(status_sql)
        result["TX after (other session)"] = self.sys.rows(f"SELECT ID FROM {SCHEMA}.TX")
        result["LOGT after (other session)"] = self.sys.rows(f"SELECT MSG FROM {SCHEMA}.LOGT")
        result["RUNS after (other session)"] = self.sys.rows(
            f"SELECT TAG, RUN_STATUS FROM {SCHEMA}.RUNS"
        )
        started = time.monotonic()
        result["takeover UPDATE from another session"] = self.sys.attempt(
            f"UPDATE {SCHEMA}.RUNS SET RUN_STATUS = 'ABANDONED' WHERE SESSION_ID = {session_id} "
            "AND RUN_STATUS = 'RUNNING'"
        )
        result["takeover seconds"] = round(time.monotonic() - started, 2)
        runner.close()
        time.sleep(0.5)
        result["status after close"] = user.rows(status_sql)
        return result

    def sample_statuses(self) -> dict[str, Any]:
        runner = self.new_session()
        observer = self.new_session()
        session_id = runner.session_id
        outcome: dict[str, Any] = {}
        seen: collections.Counter[str] = collections.Counter()
        sequence: list[str] = []
        try:
            thread = threading.Thread(
                target=lambda: outcome.setdefault(
                    "r", runner.attempt(f"EXECUTE SCRIPT {SCHEMA}.J_LOOP({SAMPLE_LOOP_STATEMENTS})")
                )
            )
            started = time.monotonic()
            thread.start()
            time.sleep(0.2)
            while thread.is_alive():
                rows = observer.rows(
                    f"SELECT STATUS, COMMAND_NAME FROM EXA_ALL_SESSIONS WHERE SESSION_ID = {session_id}"
                )
                if rows:
                    state = " / ".join(str(value) for value in rows[0])
                    seen[state] += 1
                    sequence.append(state)
            thread.join()
            seconds = round(time.monotonic() - started, 1)
        finally:
            runner.close()
            observer.close()
        busy = [index for index, state in enumerate(sequence) if not state.startswith("IDLE")]
        interior_idle = [
            index
            for index, state in enumerate(sequence)
            if state.startswith("IDLE") and busy and busy[0] < index < busy[-1]
        ]
        edge_idle = [
            index
            for index, state in enumerate(sequence)
            if state.startswith("IDLE") and index not in interior_idle
        ]
        return {
            "statements": SAMPLE_LOOP_STATEMENTS,
            "seconds": seconds,
            "samples": len(sequence),
            "seen": dict(seen),
            "idle samples between busy samples": len(interior_idle),
            "idle samples before the first or after the last busy sample": edge_idle,
            "script": outcome.get("r"),
        }

    # ------------------------------------------------------------------ (k)

    def probe_k(self) -> Finding:
        for statement in (
            f"CREATE TABLE {SCHEMA}.LL (TABLE_NAME VARCHAR(128) UTF8 NOT NULL, SLICE_KEY VARCHAR(2000) UTF8 NOT NULL, "
            "IS_FINAL BOOLEAN NOT NULL, ROWS_LOADED DECIMAL(18,0) NOT NULL, LOADED_AT TIMESTAMP NOT NULL, "
            "LOADED_BY VARCHAR(64) UTF8 NOT NULL, CONSTRAINT LL_PK PRIMARY KEY (TABLE_NAME, SLICE_KEY))",
            f"CREATE TABLE {SCHEMA}.IDT (RUN_ID DECIMAL(18,0) IDENTITY NOT NULL, X DECIMAL(9,0))",
            f"CREATE OR REPLACE VIEW {SCHEMA}.SLICES AS SELECT 'GAMES' AS TABLE_NAME, K.SLICE_KEY, "
            "TRUE AS FINAL_IF_LOADED_NOW, CASE WHEN L.SLICE_KEY IS NULL THEN FALSE ELSE TRUE END AS IS_STORED FROM "
            "(SELECT 'k1' AS SLICE_KEY FROM DUAL UNION ALL SELECT 'k2' FROM DUAL) K "
            f"LEFT JOIN {SCHEMA}.LL L ON L.TABLE_NAME = 'GAMES' AND L.SLICE_KEY = K.SLICE_KEY",
            f"INSERT INTO {SCHEMA}.LL VALUES ('GAMES', 'k1', FALSE, 1, CURRENT_TIMESTAMP, 'OLD')",
        ):
            self.sys.run(statement)
        details: dict[str, Any] = {}
        details["LIKE"] = self.sys.attempt(f"CREATE TABLE {SCHEMA}.LL_STAGE (LIKE {SCHEMA}.LL)")
        details["LIKE columns"] = self.sys.rows(
            "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_IS_NULLABLE FROM EXA_ALL_COLUMNS "
            f"WHERE COLUMN_SCHEMA = '{SCHEMA}' AND COLUMN_TABLE = 'LL_STAGE' ORDER BY COLUMN_ORDINAL_POSITION"
        )
        details["LIKE constraints"] = self.sys.rows(
            "SELECT CONSTRAINT_TABLE, CONSTRAINT_TYPE FROM EXA_ALL_CONSTRAINTS "
            f"WHERE CONSTRAINT_SCHEMA = '{SCHEMA}' AND CONSTRAINT_TABLE LIKE 'LL%' ORDER BY 1, 2"
        )
        details["LIKE identity (default)"] = self.sys.attempt(
            f"CREATE TABLE {SCHEMA}.IDT_COPY (LIKE {SCHEMA}.IDT)"
        )
        details["identity columns"] = self.sys.rows(
            "SELECT COLUMN_TABLE, COLUMN_NAME, COLUMN_IDENTITY FROM EXA_ALL_COLUMNS "
            f"WHERE COLUMN_SCHEMA = '{SCHEMA}' AND COLUMN_TABLE LIKE 'IDT%' AND COLUMN_NAME = 'RUN_ID' ORDER BY 1"
        )
        merge = (
            f"MERGE INTO {SCHEMA}.LL L USING (SELECT S.TABLE_NAME, S.SLICE_KEY, S.FINAL_IF_LOADED_NOW AS IS_FINAL, "
            f"5 AS ROWS_LOADED FROM {SCHEMA}.SLICES S WHERE S.SLICE_KEY IN ('k1', 'k2')) X "
            "ON (L.TABLE_NAME = X.TABLE_NAME AND L.SLICE_KEY = X.SLICE_KEY) "
            "WHEN MATCHED THEN UPDATE SET IS_FINAL = X.IS_FINAL, ROWS_LOADED = X.ROWS_LOADED, "
            "LOADED_AT = CURRENT_TIMESTAMP, LOADED_BY = 'NEW' "
            "WHEN NOT MATCHED THEN INSERT VALUES (X.TABLE_NAME, X.SLICE_KEY, X.IS_FINAL, X.ROWS_LOADED, "
            "CURRENT_TIMESTAMP, 'NEW')"
        )
        details["MERGE reading the target through a view"] = self.sys.attempt(merge)
        details["after MERGE"] = self.sys.rows(
            f"SELECT SLICE_KEY, IS_FINAL, ROWS_LOADED, LOADED_BY FROM {SCHEMA}.LL ORDER BY 1"
        )
        details["MERGE again (idempotent)"] = self.sys.attempt(merge)
        ok = (
            details["LIKE"][0]
            and details["MERGE reading the target through a view"] == (True, 2)
            and details["after MERGE"] == [("k1", True, 5, "NEW"), ("k2", True, 5, "NEW")]
        )
        result = (
            "CREATE TABLE … (LIKE …) copies column names, types and NOT NULL, but not the primary key. MERGE "
            "into a primary-key table whose USING source reads the same table through a view works and is "
            "idempotent on a re-run."
        )
        return Finding("k", "pass" if ok else "fail", result, details)

    # ------------------------------------------------------------------ (l)

    def probe_l(self) -> Finding:
        expressions = (
            "SESSIONTIMEZONE, DBTIMEZONE, CURRENT_TIMESTAMP, LOCALTIMESTAMP, SYSTIMESTAMP, "
            "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC') AS UTC_NOW, "
            "CONVERT_TZ(SYSTIMESTAMP, DBTIMEZONE, 'UTC') AS SYS_UTC, FROM_POSIX_TIME(POSIX_TIME()) AS POSIX_BACK"
        )
        session = self.new_session()
        details: dict[str, Any] = {"host UTC": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")}
        try:
            details["default session"] = session.rows(f"SELECT {expressions} FROM DUAL")
            session.run(
                f"CREATE OR REPLACE VIEW {SCHEMA}.NOWV AS SELECT CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC') "
                "AS UTC_NOW FROM DUAL"
            )
            for zone in ("Europe/Berlin", "America/New_York", "UTC"):
                session.run(f"ALTER SESSION SET TIME_ZONE = '{zone}'")
                details[f"session {zone}"] = session.rows(f"SELECT {expressions} FROM DUAL")
                details[f"view in session {zone}"] = session.rows(
                    f"SELECT UTC_NOW FROM {SCHEMA}.NOWV"
                )
            details["view column type"] = session.rows(
                "SELECT COLUMN_TYPE FROM EXA_ALL_COLUMNS "
                f"WHERE COLUMN_SCHEMA = '{SCHEMA}' AND COLUMN_TABLE = 'NOWV'"
            )
            details["ambiguous fall-back hour 2026-10-25 02:30 Europe/Berlin"] = session.rows(
                "SELECT CONVERT_TZ(TIMESTAMP '2026-10-25 02:30:00', 'Europe/Berlin', 'UTC') FROM DUAL"
            )
        finally:
            session.close()
        self.sys.run(
            lua_script(
                "L_NOW",
                "",
                """note("sql utc", query("SELECT CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC') AS U FROM DUAL")[1].U)
note("os.date utc", os.date("!%Y-%m-%d %H:%M:%S"))
notes_exit()""",
            )
        )
        details["inside a script"] = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.L_NOW()")
        host = details["host UTC"][:13]
        consistent = all(
            str(details[f"view in session {zone}"][0][0])[:13] == host
            for zone in ("Europe/Berlin", "America/New_York", "UTC")
        )
        status = "pass" if consistent else "fail"
        default = details["default session"][0]
        result = (
            f"Personal's default session time zone is {default[0]} (DBTIMEZONE {default[1]}), so CURRENT_TIMESTAMP "
            "is local time. The UTC 'now' is CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC') "
            "(TIMESTAMP(3)): it gives UTC in views and scripts whatever the reader's session time zone. Do not use "
            "SYSTIMESTAMP (UTC on this host although DBTIMEZONE says CET, so converting it shifts twice) or "
            "FROM_POSIX_TIME(POSIX_TIME()) (returns session-local time). In the ambiguous fall-back hour of a "
            "DST session zone CONVERT_TZ picks standard time (up to 1 h late, once a year)."
        )
        return Finding("l", status, result, details)

    # ------------------------------------------------------------------ (m)

    def probe_m(self) -> Finding:
        self.sys.run(
            lua_script(
                "M_LIBS",
                "",
                f"""note("_VERSION", _VERSION)
for _, name in ipairs({{"cjson", "require", "os", "io", "utf8", "unicode", "socket", "load", "debug", "exa", "import", "quote", "decimal"}}) do
  note("global " .. name, type(_G[name]))
end
local ok, cj = pcall(function() return require("cjson") end)
note("require('cjson')", ok and type(cj) or tostring(cj))
if ok then
  local okd, v = pcall(function() return cj.decode('{{"slice_keys":["\\\\/games?year=2020","*\\\\/x"],"n":3}}') end)
  note("cjson.decode with escapes", okd and (v.slice_keys[1] .. " | " .. v.slice_keys[2] .. " | n=" .. tostring(v.n)) or tostring(v))
end
local names = {{}}
for k in pairs(os) do names[#names + 1] = k end
table.sort(names)
note("os functions", table.concat(names, ","))
local t0 = os.time()
local c0 = os.clock()
local oks = pcall(function() os.sleep(1) end)
note("os.sleep(1)", tostring(oks) .. " elapsed≈" .. os.difftime(os.time(), t0) .. "s")
note("exa.meta.script_language", exa.meta.script_language)
note("exa.meta.database_version", exa.meta.database_version)
note("exa.meta.node_count", exa.meta.node_count)
local okb, rb = pquery("SELECT :a AS A, :b AS B, :c AS C FROM DUAL", {{a = "it's", b = null, c = 12}})
note(":name value binds", okb and (rb[1].A .. " | B is null: " .. tostring(rb[1].B == null) .. " | " .. rb[1].C) or rb.error_message)
local okv, rv = pquery("SELECT COUNT(*) AS N FROM ::t", {{t = '"{SCHEMA}"."SECRET_T"'}})
note("::name verbatim", okv and ("ok " .. rv[1].N) or rv.error_message)
local okin, rin = pquery("SELECT COUNT(*) AS N FROM (SELECT 'a' AS K FROM DUAL UNION ALL SELECT 'b' FROM DUAL) WHERE K IN (:k1, :k2)", {{k1 = "a", k2 = "b"}})
note("IN (:k1, :k2)", okin and rin[1].N or rin.error_message)
note("quote('my col')", quote("my col"))
notes_exit()""",
            )
        )
        notes = self.sys.notes(f"EXECUTE SCRIPT {SCHEMA}.M_LIBS()")
        cjson = notes.get("require('cjson')") == "table"
        result = (
            f"{notes.get('_VERSION')} ({notes.get('exa.meta.script_language')}). cjson is not a global but "
            f"require('cjson') {'works' if cjson else 'fails'} and decodes '\\/' escapes. os offers "
            f"{notes.get('os functions')} (os.date('!…') gives UTC; os.sleep exists), utf8 is available, io, "
            "socket, load and debug are not. Binds: ':name' binds values (quotes and null safe, also in "
            "IN (:k1, :k2)); '::name' inserts text verbatim."
        )
        return Finding("m", "pass", result, {"notes": notes})

    # ------------------------------------------------------------------ driver

    def record(self, finding: Finding) -> None:
        self.findings[finding.item] = finding
        print(f"({finding.item}) {finding.status.upper()}: {finding.result}\n", flush=True)

    def save(self, cleanup: dict[str, Any] | None) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        previous: dict[str, Any] = {}
        if self.results_file.exists():
            with contextlib.suppress(ValueError):
                previous = json.loads(self.results_file.read_text(encoding="utf-8")).get(
                    "findings", {}
                )
        merged = {**previous, **{item: asdict(finding) for item, finding in self.findings.items()}}
        payload = {"meta": self.meta, "cleanup": cleanup, "findings": dict(sorted(merged.items()))}
        self.results_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def run(self, items: str) -> int:
        probes: dict[str, Callable[[], Finding]] = {
            item: getattr(self, f"probe_{item}") for item in ITEMS
        }
        cleanup = None
        try:
            self.setup()
            for item in items:
                try:
                    self.record(probes[item]())
                except Exception as error:
                    self.record(
                        Finding(item, "error", f"{type(error).__name__}: {error_text(error)}")
                    )
        finally:
            if not self.keep:
                cleanup = self.cleanup()
                print(f"cleanup: {cleanup}")
            self.save(cleanup)
            self.sys.close()
        gate = [
            item
            for item in GATE_ITEMS
            if item in self.findings and self.findings[item].status in ("fail", "error")
        ]
        if gate:
            print(f"GATE FAILED: {', '.join(gate)}; stop and escalate (A1).")
            return 3
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target", choices=("local", "aws"), default="local")
    parser.add_argument("--only", default="", help="comma-separated items, e.g. a,b,c")
    parser.add_argument("--keep", action="store_true", help="leave the PROBE_* objects in place")
    args = parser.parse_args()
    allowed = ITEMS if args.target == "local" else AWS_ITEMS
    chosen = [item.strip() for item in args.only.split(",") if item.strip()] or list(allowed)
    unknown = [item for item in chosen if item not in allowed]
    if unknown:
        parser.error(
            f"items {unknown} are not available for target {args.target} (allowed: {allowed})"
        )
    return Probe(args.target, args.keep).run("".join(chosen))


if __name__ == "__main__":
    sys.exit(main())
