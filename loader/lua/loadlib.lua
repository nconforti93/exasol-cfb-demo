-- loader/lua/loadlib.lua: the library of the loader's Lua scripts (BACKFILL, REFRESH_WEEK) and of
-- every other bundle that joins the run lock (Phase 4's SIMULATE scripts).
--
-- Bundling. loader/bundle.py renders `local PLAIN = {...}` and `local QUOTED = {...}` (the names of
-- the namespace: META_SCHEMA, CORE_SCHEMA, STAGE_SCHEMA, RESTORE_SCHEMA, INFRA_SCHEMA, VS_NAME) in
-- front of this file and the entry file after it, as one Lua chunk. Another bundle uses the same
-- recipe: `loader.bundle.render_constants(namespace)`, this file, then its own entry code.
--
-- Rules every function follows (docs/spikes/s5-loader-probe.md and the 4.2 probe):
-- * Every statement runs through pquery. A failed statement raises error(message, 0); Exasol turns
--   Lua error objects into strings, so the Exasol error code is kept beside the message and read
--   back with failure_code(message).
-- * On any failure: ROLLBACK first (under autocommit a script that ends in error() commits its
--   open work), then log and COMMIT, then error(message, 0). After a kill or client abort (R0003)
--   every later statement fails too: nothing is logged, and the next lock holder frees the rows.
-- * NULL arrives as `null`, not nil: nz() normalizes every value read from a row or an argument.
-- * Session ids are compared in SQL only; a DECIMAL(20,0) can exceed a Lua integer.
-- * Strings written to a VARCHAR column or returned by exit() are clipped to whole characters.
-- * Values are bound as :name. Only the PLAIN and QUOTED constants, identifiers checked against
--   ^[A-Z][A-Z0-9_]*$, whole numbers and the plan's FIXED_FILTER (SQL rendered by make deploy)
--   are concatenated into statement text.
--
-- Public API (globals, so any bundle can call them; each raises error(message, 0) on failure).
--
-- Run lock: the RUNNING row of LOAD_RUN, shared by everything that writes the loader's tables or
-- reads them as one consistent state (loader runs, snapshots and restores, Phase 3's model deploy,
-- Phase 4's simulations and deploys). Two stale rules, chosen by the kind:
--   script rule (BACKFILL, REFRESH_WEEK, SIMULATE): a row of a Lua script run, which is one
--       statement. It is stale when its session is gone, when it is the caller's own session, or
--       when its session is IDLE (a running script is never idle, so an idle session means the script
--       ended without closing its row: a failure, a KILL STATEMENT or an interrupt).
--   host rule (DEPLOY, SNAPSHOT, RESTORE): a row of a host process, whose session is idle between
--       its statements. It is stale only when its session is gone or is the caller's own session.
--   acquire_lock(kind, args) -> run_id
--       Commits the caller's open transaction first. args: text for RUN_ARGS (clipped to 200
--       characters). In its own transaction it sets every stale RUNNING row to ABANDONED (its
--       RUNNING steps to FAILED, DETAIL naming the reason), inserts a RUNNING row for
--       CURRENT_SESSION and commits. A live row refuses the lock with "<kind> refused: <kind of the
--       holder> run <id> is running (session ..., started ... UTC)", and nothing is changed. A
--       transaction collision (40001, another session inserting at the same moment) is rolled back
--       and retried; after 3 collisions the lock is refused. An unknown kind raises at once.
--       The row belongs to the calling session: a host process can take a host kind through a
--       script run in its own session (tests/it/lock_probe.py shows the bundle) and keeps that session
--       open while it works; a session that runs another locking script abandons its own row.
--       Never call it from a script nested in a locked run (a registered REFRESH_WEEK step such as
--       Phase 4's REFRESH_SIMULATE): the caller's session owns that run's row, so the own-session
--       rule would abandon it. A nested step works under the run id it is given.
--   acquire_host_lock(kind, args) -> run_id: acquire_lock for a host kind only (RUN_LOCK's entry);
--       a script kind raises before any statement, because a host session is idle between its
--       statements and the idle rule would free the row at once.
--   release_lock(run_id, status, detail) -> held
--       Ends a RUNNING row of the calling session with an upper-case status (DONE, FAILED, ...)
--       and commits. `held` is false when the row was no longer RUNNING or belongs to another
--       session: only the holder's session releases a row; other sessions free it only through the
--       stale rules of acquire_lock.
--   new_job(kind, args) -> job
--       acquire_lock plus the job table the functions below use: run_id, kind, tag
--       ('<KIND>#<run_id>', the LOAD_LOG.LOADED_BY and FETCH_LOG.CONTEXT value), slices, rows,
--       calls (the run totals), open_step, fetch_source ('LIVE' or 'REPLAY', set by the caller
--       from vs_settings() before load_batch).
--   end_job(job, status, detail): writes the totals and releases the lock (commits). Raises when
--       the run had lost its lock (another session abandoned the row), so the run fails instead of
--       reporting a result whose work may have overlapped another run.
--   fail(job, err): never returns. Rolls back; unless the run was killed, marks the open step
--       and the run FAILED with the message and commits; then error(message, 0). job may be nil.
--   Pattern for an entry:
--       local job = new_job(kind, args)
--       local ok, result = pcall(work, job)
--       if not ok then fail(job, result) end
--       end_job(job, 'DONE', detail)
--
-- Steps (REFRESH_LOG; a step row is committed when it starts and when it ends):
--   step_begin(job, step_no, name, detail): inserts the RUNNING row and commits.
--   step_end(job, step_no, status, counts, detail): closes the row inside the caller's transaction;
--       counts = {slices =, rows =, calls =} (nil fields are NULL). Commit with commit_step(job).
--   commit_step(job): commits and forgets the open step.
--   add_step_calls(job, step_no, calls): adds measured calls to a step (caller commits).
--   write_totals(job, slices, rows, calls) / save_totals(job): LOAD_RUN totals (caller commits).
--   set_remaining(job, column, remaining): REMAINING_BEFORE or REMAINING_AFTER of the run's row
--       (caller commits).
--
-- Settings and quota:
--   vs_settings() -> {live, fetch_source, max_calls_per_query}: FETCH_MODE and
--       MAX_CALLS_PER_QUERY of the virtual schema (defaults live and 10).
--   read_quota() -> {used, remaining, refresh, backfill}: QUOTA (/info, free) in its own
--       transaction (commits). An unreadable quota raises: a live run sends no request without it.
--   measure_spend(job, step_no, before) -> quota, spent: live mode, after a fetch. Reads the quota
--       again and charges the change of /info's USED_CALLS since `before` (an earlier read_quota
--       result; never negative) to the step's CALLS, job.calls and LOAD_RUN.CALLS, and commits.
--       Retries and other clients of the key in between count too: the spend is measured, not
--       estimated.
--
-- Batches (one plan entry; rows of BACKFILL_QUEUE, or any rows with the columns ENTRY_ID,
-- TABLE_NAME, SLICING, SEASON, SLICE_KEY, SEASON_FROM, SEASON_TO, FIXED_FILTER and BATCH_ALONE):
--   split_batches(rows, cap) -> batches: consecutive rows of one ENTRY_ID, at most `cap` slices; a
--       BATCH_ALONE row is a batch of its own. batch = {entry_id, table_name, slicing,
--       season_from, season_to, fixed_filter, alone, keys = {...}, seasons = {...}}.
--   single_slice_batches(batch) -> batches: the batch's slices one by one, in order.
--   batch_query(batch) -> SQL: SELECT * FROM <vs>.<table> WHERE <fixed filter> AND <slicing
--       filter> AND "REQ_SOURCE" <> 'STORED' (read-through stays on; only fetched rows are kept).
--   explain_check(batch, fetch_source): EXPLAIN VIRTUAL of batch_query must plan exactly the
--       batch's keys (zero calls; only the manifest's slice_keys array counts); otherwise it
--       raises, naming the table, both key lists and `make deploy`. With fetch_source ('LIVE' or 'REPLAY')
--       the manifest's fetch_mode must match it, so a FETCH_MODE changed during a run (a replay run
--       turning live without a quota) raises before the fetch.
--   load_batch(job, batch, close) -> true, {slices, rows} | false, error_text
--       One transaction: explain_check with job.fetch_source, the stage must be empty, the fetch
--       into the stage, every staged REQ_SLICE must be a batch key, the core rows of the keys are
--       replaced, LOAD_LOG is merged (IS_FINAL = FINAL_IF_LOADED_NOW, LOADED_BY = job.tag), one
--       FETCH_LOG row per key,
--       the keys' LOAD_FAILURES rows and the stage are cleared, close(stats) runs (the caller's
--       statements, e.g. step_end), COMMIT. A failed fetch is rolled back and returned as false
--       plus a short error text; a kill and every other failure raise.
--       BACKFILL retries a failed batch slice by slice (single_slice_batches, record_failure);
--       REFRESH_WEEK calls it once per plan entry of a table with no fallback, and a false return
--       keeps the table's previous rows.
--   record_failure(job, table_name, slice_key, error_text): one LOAD_FAILURES failure for this
--       run (a second one in the same run does not count; caller commits).
--   orphans_by_table(rank) -> {order = {table, ...}, keys = {[table] = {key, ...}}}: the orphan
--       slices (LOAD_ORPHANS) of tables planned up to profile rank `rank` (nil: every table).
--   orphan_detail(table_name, keys) -> text naming the orphans and the remedy.
--
-- Values: nz(v), sql_value(v) (nil -> null for binds and exit()), clip(text, limit),
--   whole_number(v, what), short_error(text), meta_table(name), core_table(name),
--   stage_table(name), virtual_table(name), run_sql(statement, binds), commit(), rollback(),
--   failure_code(message).
--   qualified_name('SCHEMA.NAME') -> '"SCHEMA"."NAME"': a plan's script name (REFRESH_STEPS) for
--       statement text; anything but two UPPER_CASE identifiers joined by one dot raises.
--   is_killed(message) -> true when the error was a kill or client abort (R0003). Re-raise such an
--       error unchanged (error(message, 0)): fail() recognizes it by the message and logs nothing.

local KILLED = "R0003"
local COLLISION = "40001"
local LOCK_ATTEMPTS = 3
local IDENTIFIER = "^[A-Z][A-Z0-9_]*$"
local ELLIPSIS = "..."
local DETAIL_LENGTH = 2000
local ARGS_LENGTH = 200
local RUN_STATUS_LENGTH = 20
local MAX_FAILURES = 999
local STEP_NAME_LENGTH = 160
local ERROR_LENGTH = 2000
local DEFAULT_MAX_CALLS_PER_QUERY = 10
local MAX_MAX_CALLS_PER_QUERY = 1000
local MANIFEST_OPEN = "/*CFBD "
local LISTED_KEYS = 5
local FETCH_SOURCES = {live = "LIVE", replay = "REPLAY"}
local SCRIPT_RULE = "script"
local HOST_RULE = "host"
local LOCK_KINDS = {
  BACKFILL = SCRIPT_RULE,
  REFRESH_WEEK = SCRIPT_RULE,
  SIMULATE = SCRIPT_RULE,
  DEPLOY = HOST_RULE,
  SNAPSHOT = HOST_RULE,
  RESTORE = HOST_RULE,
}

local function kind_names(rule)
  local names = {}
  for kind, kind_rule in pairs(LOCK_KINDS) do
    if rule == nil or kind_rule == rule then
      names[#names + 1] = kind
    end
  end
  table.sort(names)
  return names
end

local SCRIPT_KINDS_SQL = "'" .. table.concat(kind_names(SCRIPT_RULE), "', '") .. "'"
local ABANDONED_BECAUSE = {
  OWN = "abandoned: a new run started in the session of this run, so this run had ended without "
    .. "closing its row",
  GONE = "abandoned: the session of this run has ended",
  IDLE = "abandoned: the session of this run is idle, so the script ended without closing its row "
    .. "(a failure, a kill or an interrupt)",
}

local last_failure = {code = nil, message = nil}

-- Values and text ---------------------------------------------------------------------------------

function nz(value)
  if value == null then
    return nil
  end
  return value
end

function sql_value(value)
  if value == nil then
    return null
  end
  return value
end

function clip(text, limit)
  if text == nil then
    return nil
  end
  text = tostring(text)
  if utf8.len(text) == nil then
    text = (text:gsub("[\128-\255]", "?"))
  end
  if utf8.len(text) <= limit then
    return text
  end
  return text:sub(1, utf8.offset(text, limit - #ELLIPSIS + 1) - 1) .. ELLIPSIS
end

function whole_number(value, what)
  value = nz(value)
  if value == nil then
    return nil
  end
  local number = type(value) == "number" and math.tointeger(value) or nil
  if number == nil then
    error(what .. " must be a whole number, got " .. tostring(value), 0)
  end
  return number
end

local function number_text(value)
  return string.format("%d", whole_number(value, "a number in SQL text"))
end

function short_error(text)
  text = tostring(text or "unknown error")
  local adapter = text:match("(CFBD_VS%.[%w_@]+:[^\r\n]*)")
  local first = adapter or text:match("^[^\r\n]*") or text
  return clip(first, ERROR_LENGTH)
end

-- SQL ---------------------------------------------------------------------------------------------

local function raise(code, message)
  last_failure = {code = code, message = message}
  error(message, 0)
end

function failure_code(message)
  if message ~= nil and message == last_failure.message then
    return last_failure.code
  end
  return nil
end

function run_sql(statement, binds)
  local ok, result
  if binds == nil then
    ok, result = pquery(statement)
  else
    ok, result = pquery(statement, binds)
  end
  if not ok then
    raise(
      tostring(nz(result.error_code) or ""),
      tostring(nz(result.error_message) or "SQL statement failed")
    )
  end
  return result
end

function commit()
  run_sql("COMMIT")
end

function rollback()
  pquery("ROLLBACK")
end

local function quoted(name)
  if type(name) ~= "string" or not name:match(IDENTIFIER) then
    error("loader: not an UPPER_CASE identifier: " .. tostring(name), 0)
  end
  return '"' .. name .. '"'
end

function qualified_name(name)
  local schema, object = nil, nil
  if type(name) == "string" then
    schema, object = name:match("^([A-Z][A-Z0-9_]*)%.([A-Z][A-Z0-9_]*)$")
  end
  if schema == nil then
    error("loader: not a qualified UPPER_CASE name like SCHEMA.NAME, got " .. tostring(name), 0)
  end
  return quoted(schema) .. "." .. quoted(object)
end

function is_killed(message)
  return failure_code(message) == KILLED
end

function meta_table(name)
  return QUOTED.META_SCHEMA .. "." .. quoted(name)
end

function core_table(name)
  return QUOTED.CORE_SCHEMA .. "." .. quoted(name)
end

function stage_table(name)
  return QUOTED.STAGE_SCHEMA .. "." .. quoted(name)
end

function virtual_table(name)
  return QUOTED.VS_NAME .. "." .. quoted(name)
end

local function clock_sql()
  return '(SELECT "CLOCK_UTC" FROM ' .. meta_table("LOADER_SETTINGS") .. ")"
end

local function affected(result)
  return whole_number(nz(result.rows_affected) or 0, "rows affected")
end

-- Run lock ----------------------------------------------------------------------------------------

local function check_kind(kind)
  if type(kind) ~= "string" or LOCK_KINDS[kind] == nil then
    error("run lock: unknown run kind " .. tostring(kind) .. "; known: "
      .. table.concat(kind_names(nil), ", "), 0)
  end
end

local function check_host_kind(kind)
  check_kind(kind)
  if LOCK_KINDS[kind] ~= HOST_RULE then
    error("run lock: " .. kind .. " is not a host kind; RUN_LOCK takes "
      .. table.concat(kind_names(HOST_RULE), ", ") .. ". Script kinds take the lock in their own "
      .. "Lua (acquire_lock): a host session is idle between its statements, so the idle rule "
      .. "would free the row at once", 0)
  end
end

local function running_rows()
  return run_sql([[SELECT R."RUN_ID", R."RUN_KIND", CAST(R."SESSION_ID" AS VARCHAR(20)) AS "SESSION_TEXT",
  R."STARTED_AT",
  CASE
    WHEN R."SESSION_ID" = CURRENT_SESSION THEN 'OWN'
    WHEN S."SESSION_ID" IS NULL THEN 'GONE'
    WHEN S."STATUS" = 'IDLE' AND R."RUN_KIND" IN (]] .. SCRIPT_KINDS_SQL .. [[) THEN 'IDLE'
    ELSE 'LIVE'
  END AS "LOCK_STATE"
FROM ]] .. meta_table("LOAD_RUN") .. [[ R
LEFT JOIN "SYS"."EXA_ALL_SESSIONS" S
  ON S."SESSION_ID" = R."SESSION_ID"
WHERE R."RUN_STATUS" = 'RUNNING'
ORDER BY R."RUN_ID"]])
end

local function abandon(row)
  local binds = {run = nz(row.RUN_ID), detail = ABANDONED_BECAUSE[nz(row.LOCK_STATE)]}
  run_sql("UPDATE " .. meta_table("LOAD_RUN") .. [[ SET "RUN_STATUS" = 'ABANDONED',
  "ENDED_AT" = ]] .. clock_sql() .. [[, "DETAIL" = :detail
WHERE "RUN_ID" = :run AND "RUN_STATUS" = 'RUNNING']], binds)
  run_sql("UPDATE " .. meta_table("REFRESH_LOG") .. [[ SET "STEP_STATUS" = 'FAILED',
  "ENDED_AT" = ]] .. clock_sql() .. [[, "DETAIL" = :detail
WHERE "RUN_ID" = :run AND "STEP_STATUS" = 'RUNNING']], binds)
end

local function try_lock(kind, args)
  local rows = running_rows()
  for i = 1, #rows do
    if nz(rows[i].LOCK_STATE) == "LIVE" then
      return nil, rows[i]
    end
  end
  for i = 1, #rows do
    abandon(rows[i])
  end
  run_sql("INSERT INTO " .. meta_table("LOAD_RUN")
    .. [[ ("RUN_KIND", "RUN_ARGS", "SESSION_ID", "STARTED_AT", "RUN_STATUS")
SELECT :kind, :args, CURRENT_SESSION, CFG."CLOCK_UTC", 'RUNNING'
FROM ]] .. meta_table("LOADER_SETTINGS") .. " CFG",
    {kind = kind, args = sql_value(clip(args, ARGS_LENGTH))})
  local mine = run_sql('SELECT MAX("RUN_ID") AS "RUN_ID" FROM ' .. meta_table("LOAD_RUN")
    .. [[ WHERE "SESSION_ID" = CURRENT_SESSION AND "RUN_STATUS" = 'RUNNING']])
  local run_id = whole_number(mine[1].RUN_ID, "the new RUN_ID")
  commit()
  return run_id, nil
end

local function refusal(kind, holder)
  return string.format(
    "%s refused: %s run %d is running (session %s, started %s UTC). Only one loader run at a "
      .. "time: wait until it ends. A run whose session has ended, or a script run whose session "
      .. "is idle, no longer blocks",
    kind, tostring(nz(holder.RUN_KIND)), whole_number(holder.RUN_ID, "RUN_ID"),
    tostring(nz(holder.SESSION_TEXT)), tostring(nz(holder.STARTED_AT)))
end

function acquire_lock(kind, args)
  check_kind(kind)
  local problem = nil
  for _ = 1, LOCK_ATTEMPTS do
    local ok, run_id, holder = pcall(function()
      commit()
      return try_lock(kind, args)
    end)
    if ok and run_id ~= nil then
      return run_id
    end
    rollback()
    if ok then
      error(refusal(kind, holder), 0)
    end
    problem = run_id
    if failure_code(problem) ~= COLLISION then
      raise(failure_code(problem), kind .. " could not take the run lock in " .. PLAIN.META_SCHEMA
        .. ".LOAD_RUN: " .. tostring(problem))
    end
  end
  raise(COLLISION, string.format(
    "%s refused: another session took the run lock in %s.LOAD_RUN at the same moment "
      .. "(transaction collision, %d attempts): %s. Run it again",
    kind, PLAIN.META_SCHEMA, LOCK_ATTEMPTS, tostring(problem)))
end

function acquire_host_lock(kind, args)
  check_host_kind(kind)
  return acquire_lock(kind, args)
end

function release_lock(run_id, status, detail)
  if type(status) ~= "string" or not status:match(IDENTIFIER) or status == "RUNNING"
      or #status > RUN_STATUS_LENGTH then
    error("release_lock: status must be an upper-case word other than RUNNING, got "
      .. tostring(status), 0)
  end
  local result = run_sql("UPDATE " .. meta_table("LOAD_RUN") .. [[ SET "RUN_STATUS" = :status,
  "ENDED_AT" = ]] .. clock_sql() .. [[, "DETAIL" = :detail
WHERE "RUN_ID" = :run AND "RUN_STATUS" = 'RUNNING' AND "SESSION_ID" = CURRENT_SESSION]],
    {status = status, detail = sql_value(clip(detail, DETAIL_LENGTH)), run = whole_number(run_id, "run_id")})
  commit()
  return affected(result) == 1
end

-- Jobs and steps ----------------------------------------------------------------------------------

function new_job(kind, args)
  local run_id = acquire_lock(kind, args)
  return {
    run_id = run_id,
    kind = kind,
    tag = kind .. "#" .. tostring(run_id),
    slices = 0,
    rows = 0,
    calls = 0,
    open_step = nil,
    fetch_source = nil,
  }
end

function write_totals(job, slices, rows, calls)
  run_sql("UPDATE " .. meta_table("LOAD_RUN")
    .. [[ SET "SLICES" = :slices, "ROWS_LOADED" = :rows, "CALLS" = :calls WHERE "RUN_ID" = :run]],
    {slices = slices, rows = rows, calls = calls, run = job.run_id})
end

function save_totals(job)
  write_totals(job, job.slices, job.rows, job.calls)
end

function set_remaining(job, column, remaining)
  run_sql("UPDATE " .. meta_table("LOAD_RUN") .. " SET " .. quoted(column)
    .. ' = :remaining WHERE "RUN_ID" = :run', {remaining = sql_value(remaining), run = job.run_id})
end

function end_job(job, status, detail)
  save_totals(job)
  if not release_lock(job.run_id, status, detail) then
    error(string.format("%s run %d lost the run lock: its row in %s.LOAD_RUN was no longer RUNNING "
      .. "in this session when it ended %s (another session abandoned it), so the work may have "
      .. "overlapped another run. Check %s.LOAD_RUN before trusting this run's result",
      job.kind, job.run_id, PLAIN.META_SCHEMA, status, PLAIN.META_SCHEMA), 0)
  end
end

function step_begin(job, step_no, name, detail)
  run_sql("INSERT INTO " .. meta_table("REFRESH_LOG")
    .. [[ ("RUN_ID", "STEP_NO", "STEP_NAME", "STEP_STATUS", "STARTED_AT", "DETAIL")
SELECT :run, :step, :name, 'RUNNING', CFG."CLOCK_UTC", :detail
FROM ]] .. meta_table("LOADER_SETTINGS") .. " CFG",
    {run = job.run_id, step = step_no, name = clip(name, STEP_NAME_LENGTH),
     detail = sql_value(clip(detail, DETAIL_LENGTH))})
  commit()
  job.open_step = step_no
end

function step_end(job, step_no, status, counts, detail)
  counts = counts or {}
  run_sql("UPDATE " .. meta_table("REFRESH_LOG") .. [[ SET "STEP_STATUS" = :status,
  "ENDED_AT" = ]] .. clock_sql() .. [[, "SLICES" = :slices, "ROWS_LOADED" = :rows, "CALLS" = :calls,
  "DETAIL" = :detail
WHERE "RUN_ID" = :run AND "STEP_NO" = :step]],
    {status = status, slices = sql_value(counts.slices), rows = sql_value(counts.rows),
     calls = sql_value(counts.calls), detail = sql_value(clip(detail, DETAIL_LENGTH)),
     run = job.run_id, step = step_no})
end

function commit_step(job)
  commit()
  job.open_step = nil
end

function add_step_calls(job, step_no, calls)
  run_sql("UPDATE " .. meta_table("REFRESH_LOG")
    .. [[ SET "CALLS" = COALESCE("CALLS", 0) + :calls WHERE "RUN_ID" = :run AND "STEP_NO" = :step]],
    {calls = calls, run = job.run_id, step = step_no})
end

local function log_failure(job, message)
  if job.open_step ~= nil then
    step_end(job, job.open_step, "FAILED", {slices = 0, rows = 0}, message)
  end
  save_totals(job)
  release_lock(job.run_id, "FAILED", message)
  job.open_step = nil
end

function fail(job, err)
  local message = tostring(err)
  local code = failure_code(err)
  rollback()
  if code ~= KILLED and job ~= nil and job.run_id ~= nil then
    local logged, why = pcall(log_failure, job, message)
    if not logged then
      rollback()
      message = message .. " (the run could not be marked FAILED: " .. tostring(why) .. ")"
    end
  end
  error(message, 0)
end

-- Settings and quota ------------------------------------------------------------------------------

function vs_settings()
  local result = run_sql([[SELECT "PROPERTY_NAME", "PROPERTY_VALUE"
FROM "SYS"."EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES"
WHERE "SCHEMA_NAME" = :vs]], {vs = PLAIN.VS_NAME})
  local properties = {}
  for i = 1, #result do
    properties[nz(result[i].PROPERTY_NAME)] = nz(result[i].PROPERTY_VALUE)
  end
  local mode = string.lower(properties.FETCH_MODE or "live")
  local source = FETCH_SOURCES[mode]
  if source == nil then
    error(PLAIN.VS_NAME .. ": FETCH_MODE must be live or replay, got " .. tostring(mode), 0)
  end
  local per_query = DEFAULT_MAX_CALLS_PER_QUERY
  if properties.MAX_CALLS_PER_QUERY ~= nil then
    per_query = math.tointeger(tonumber(properties.MAX_CALLS_PER_QUERY) or 0)
  end
  if per_query == nil or per_query < 1 or per_query > MAX_MAX_CALLS_PER_QUERY then
    error(PLAIN.VS_NAME .. ": MAX_CALLS_PER_QUERY must be a whole number from 1 to "
      .. MAX_MAX_CALLS_PER_QUERY .. ", got " .. tostring(properties.MAX_CALLS_PER_QUERY), 0)
  end
  return {live = mode == "live", fetch_source = source, max_calls_per_query = per_query}
end

function read_quota()
  local where = PLAIN.META_SCHEMA .. ".QUOTA"
  local ok, result = pquery([[SELECT "USED_CALLS", "REMAINING_CALLS", "REFRESH_SPENDABLE",
  "BACKFILL_SPENDABLE"
FROM ]] .. meta_table("QUOTA"))
  if not ok then
    local code = tostring(nz(result.error_code) or "")
    local text = tostring(nz(result.error_message) or "unknown error")
    if code == KILLED then
      raise(code, text)
    end
    raise(code, "the quota cannot be read from " .. where .. " (/info): " .. short_error(text)
      .. "; a live run sends no request without it")
  end
  local quota = nil
  if #result == 1 then
    local row = result[1]
    quota = {
      used = nz(row.USED_CALLS),
      remaining = nz(row.REMAINING_CALLS),
      refresh = nz(row.REFRESH_SPENDABLE),
      backfill = nz(row.BACKFILL_SPENDABLE),
    }
  end
  if quota == nil or quota.used == nil or quota.remaining == nil or quota.backfill == nil then
    raise("QUOTA", "the quota cannot be read: " .. where .. " returned " .. #result
      .. " row(s) without used and remaining calls; a live run sends no request without it")
  end
  commit()
  return quota
end

function measure_spend(job, step_no, before)
  local quota = read_quota()
  local spent = math.max(0, quota.used - before.used)
  if spent > 0 then
    job.calls = job.calls + spent
    add_step_calls(job, step_no, spent)
    save_totals(job)
    commit()
  end
  return quota, spent
end

-- Batches -----------------------------------------------------------------------------------------

local function new_batch(row)
  return {
    entry_id = whole_number(row.ENTRY_ID, "ENTRY_ID"),
    table_name = nz(row.TABLE_NAME),
    slicing = nz(row.SLICING),
    season_from = whole_number(row.SEASON_FROM, "SEASON_FROM"),
    season_to = whole_number(row.SEASON_TO, "SEASON_TO"),
    fixed_filter = nz(row.FIXED_FILTER),
    alone = nz(row.BATCH_ALONE) == true,
    keys = {},
    seasons = {},
  }
end

local function add_slice(batch, key, season)
  local position = #batch.keys + 1
  batch.keys[position] = key
  batch.seasons[position] = season
end

function split_batches(rows, cap)
  local batches, current = {}, nil
  for i = 1, #rows do
    local row = rows[i]
    local alone = nz(row.BATCH_ALONE) == true
    local entry_id = whole_number(row.ENTRY_ID, "ENTRY_ID")
    if current == nil or alone or current.alone or current.entry_id ~= entry_id
        or #current.keys >= cap then
      current = new_batch(row)
      batches[#batches + 1] = current
    end
    add_slice(current, nz(row.SLICE_KEY), whole_number(row.SEASON, "SEASON"))
  end
  return batches
end

function single_slice_batches(batch)
  local singles = {}
  for i, key in ipairs(batch.keys) do
    local single = {}
    for name, value in pairs(batch) do
      single[name] = value
    end
    single.keys, single.seasons = {}, {}
    add_slice(single, key, batch.seasons[i])
    singles[i] = single
  end
  return singles
end

local function slicing_filter(batch)
  if batch.slicing == "season" then
    local years = {}
    for i = 1, #batch.keys do
      years[i] = number_text(batch.seasons[i])
    end
    return '"P_YEAR" IN (' .. table.concat(years, ", ") .. ")"
  elseif batch.slicing == "season_range" then
    return '"P_MIN_YEAR" = ' .. number_text(batch.season_from) .. ' AND "P_MAX_YEAR" = '
      .. number_text(batch.season_to)
  elseif batch.slicing == "none" then
    return nil
  end
  error("plan entry " .. tostring(batch.entry_id) .. ": unknown slicing " .. tostring(batch.slicing), 0)
end

function batch_query(batch)
  local conditions = {}
  if batch.fixed_filter ~= nil and batch.fixed_filter ~= "" then
    conditions[#conditions + 1] = "(" .. batch.fixed_filter .. ")"
  end
  conditions[#conditions + 1] = slicing_filter(batch)
  conditions[#conditions + 1] = [["REQ_SOURCE" <> 'STORED']]
  return "SELECT * FROM " .. virtual_table(batch.table_name) .. " WHERE "
    .. table.concat(conditions, " AND ")
end

local function batch_label(batch)
  return batch.table_name .. " (plan entry " .. tostring(batch.entry_id) .. ")"
end

local function key_binds(batch, binds)
  local names = {}
  for i, key in ipairs(batch.keys) do
    binds["k" .. i] = key
    names[i] = ":k" .. i
  end
  return table.concat(names, ", "), binds
end

function explain_check(batch, fetch_source)
  local result = run_sql('SELECT "PUSHDOWN_SQL" FROM (EXPLAIN VIRTUAL ' .. batch_query(batch) .. ")")
  local text = (#result == 1 and nz(result[1].PUSHDOWN_SQL)) or ""
  local planned, listed, mode = nil, nil, nil
  if text:sub(1, #MANIFEST_OPEN) == MANIFEST_OPEN then
    local digits = text:match('"planned_requests":(%d+)')
    planned = digits and math.tointeger(tonumber(digits))
    listed = text:match('"slice_keys":(%b[])')
    mode = text:match('"fetch_mode":"(%a+)"')
  end
  local matches = planned == #batch.keys and listed ~= nil
  for _, key in ipairs(batch.keys) do
    matches = matches and listed:find('"' .. key .. '"', 1, true) ~= nil
  end
  if not matches then
    raise("DRIFT", string.format(
      "%s: the deployed load plan and the virtual schema disagree. The load plan expects %d "
        .. "slice key(s): %s. EXPLAIN VIRTUAL plans %s request(s): %s. Run `make deploy` to compile "
        .. "the load plan from the current catalog. No request was sent",
      batch_label(batch), #batch.keys, table.concat(batch.keys, ", "), tostring(planned or "no"),
      listed or "(no CFBD manifest)"))
  end
  if fetch_source ~= nil and FETCH_SOURCES[mode or ""] ~= fetch_source then
    raise("MODE", string.format(
      "%s: the virtual schema would fetch in %s mode, but this run started in %s mode: FETCH_MODE "
        .. "of %s changed during the run. No request was sent; run again",
      batch_label(batch), tostring(mode or "an unknown"), string.lower(fetch_source), PLAIN.VS_NAME))
  end
end

local function require_empty_stage(batch)
  local result = run_sql('SELECT COUNT(*) AS "N" FROM ' .. stage_table(batch.table_name))
  local waiting = whole_number(result[1].N, "staged rows")
  if waiting ~= 0 then
    raise("STAGE", string.format(
      "%s: the stage table %s.%s holds %d row(s) of an interrupted load. Only a loader run writes "
        .. "it; empty it with DELETE FROM %s.%s and run again. No request was sent",
      batch_label(batch), PLAIN.STAGE_SCHEMA, batch.table_name, waiting, PLAIN.STAGE_SCHEMA,
      batch.table_name))
  end
end

local function require_batch_keys(batch)
  local keys, binds = key_binds(batch, {})
  local result = run_sql('SELECT COUNT(*) AS "N" FROM ' .. stage_table(batch.table_name)
    .. ' WHERE "REQ_SLICE" IS NULL OR "REQ_SLICE" NOT IN (' .. keys .. ")", binds)
  local strays = whole_number(result[1].N, "rows of other slices")
  if strays ~= 0 then
    raise("STRAY", string.format("%s: the fetch staged %d row(s) of slices outside the batch (%s); "
      .. "the batch was rolled back. Run `make deploy` and check the catalog", batch_label(batch),
      strays, table.concat(batch.keys, ", ")))
  end
end

local function require_count(result, expected, what, batch)
  local count = affected(result)
  if count ~= expected then
    raise("COUNT", string.format("%s: %s wrote %d row(s), expected %d; the batch was rolled back",
      batch_label(batch), what, count, expected))
  end
end

local function replace_core_rows(batch)
  local keys, binds = key_binds(batch, {})
  run_sql("DELETE FROM " .. core_table(batch.table_name) .. ' WHERE "REQ_SLICE" IN (' .. keys .. ")",
    binds)
  local inserted = run_sql("INSERT INTO " .. core_table(batch.table_name) .. " SELECT * FROM "
    .. stage_table(batch.table_name))
  return affected(inserted)
end

local function register_slices(job, batch)
  local keys, binds = key_binds(batch, {entry = batch.entry_id, tag = job.tag})
  local result = run_sql("MERGE INTO " .. meta_table("LOAD_LOG") .. [[ L
USING (
  SELECT S."TABLE_NAME" AS "TABLE_NAME", S."SLICE_KEY" AS "SLICE_KEY",
    S."FINAL_IF_LOADED_NOW" AS "IS_FINAL", COALESCE(C."N", 0) AS "ROWS_LOADED",
    CFG."NOW_UTC" AS "LOADED_AT"
  FROM ]] .. meta_table("LOAD_SLICES") .. [[ S
  CROSS JOIN ]] .. meta_table("LOADER_SETTINGS") .. [[ CFG
  LEFT JOIN (
    SELECT "REQ_SLICE", COUNT(*) AS "N" FROM ]] .. stage_table(batch.table_name) .. [[
    GROUP BY "REQ_SLICE"
  ) C
    ON C."REQ_SLICE" = S."SLICE_KEY"
  WHERE S."ENTRY_ID" = :entry AND S."SLICE_KEY" IN (]] .. keys .. [[)
) X
ON (L."TABLE_NAME" = X."TABLE_NAME" AND L."SLICE_KEY" = X."SLICE_KEY")
WHEN MATCHED THEN UPDATE SET "IS_FINAL" = X."IS_FINAL", "ROWS_LOADED" = X."ROWS_LOADED",
  "LOADED_AT" = X."LOADED_AT", "LOADED_BY" = :tag
WHEN NOT MATCHED THEN INSERT VALUES
  (X."TABLE_NAME", X."SLICE_KEY", X."IS_FINAL", X."ROWS_LOADED", X."LOADED_AT", :tag)]], binds)
  require_count(result, #batch.keys, "MERGE INTO LOAD_LOG", batch)
end

local function log_fetches(job, batch)
  local keys, binds = key_binds(batch, {entry = batch.entry_id, tag = job.tag,
    source = job.fetch_source})
  local result = run_sql("INSERT INTO " .. meta_table("FETCH_LOG") .. [[
  ("LOGGED_AT", "CONTEXT", "TABLE_NAME", "SLICE_KEY", "REQ_SOURCE", "HTTP_STATUS", "ATTEMPTS", "MS",
   "ROWS_RETURNED", "REQUESTED_AT")
SELECT CFG."CLOCK_UTC", :tag, S."TABLE_NAME", S."SLICE_KEY", COALESCE(M."REQ_SOURCE", :source),
  M."REQ_STATUS", M."REQ_ATTEMPTS", M."REQ_MS", COALESCE(M."REQ_ROWS", 0), M."REQ_AT"
FROM ]] .. meta_table("LOAD_SLICES") .. [[ S
CROSS JOIN ]] .. meta_table("LOADER_SETTINGS") .. [[ CFG
LEFT JOIN (
  SELECT "REQ_SLICE", MAX("REQ_SOURCE") AS "REQ_SOURCE", MAX("REQ_STATUS") AS "REQ_STATUS",
    MAX("REQ_ATTEMPTS") AS "REQ_ATTEMPTS", MAX("REQ_MS") AS "REQ_MS", MAX("REQ_ROWS") AS "REQ_ROWS",
    MAX("REQ_AT") AS "REQ_AT"
  FROM ]] .. stage_table(batch.table_name) .. [[
  GROUP BY "REQ_SLICE"
) M
  ON M."REQ_SLICE" = S."SLICE_KEY"
WHERE S."ENTRY_ID" = :entry AND S."SLICE_KEY" IN (]] .. keys .. ")", binds)
  require_count(result, #batch.keys, "INSERT INTO FETCH_LOG", batch)
end

local function clear_batch(batch)
  local keys, binds = key_binds(batch, {t = batch.table_name})
  run_sql("DELETE FROM " .. meta_table("LOAD_FAILURES")
    .. ' WHERE "TABLE_NAME" = :t AND "SLICE_KEY" IN (' .. keys .. ")", binds)
  run_sql("DELETE FROM " .. stage_table(batch.table_name))
end

function load_batch(job, batch, close)
  if job.fetch_source == nil then
    error("load_batch: job.fetch_source is not set (use vs_settings().fetch_source)", 0)
  end
  explain_check(batch, job.fetch_source)
  require_empty_stage(batch)
  local fetched, outcome = pquery("INSERT INTO " .. stage_table(batch.table_name) .. " "
    .. batch_query(batch))
  if not fetched then
    local code = tostring(nz(outcome.error_code) or "")
    local text = tostring(nz(outcome.error_message) or "the fetch failed")
    rollback()
    if code == KILLED then
      raise(code, text)
    end
    return false, short_error(text)
  end
  require_batch_keys(batch)
  local stats = {slices = #batch.keys, rows = replace_core_rows(batch)}
  register_slices(job, batch)
  log_fetches(job, batch)
  clear_batch(batch)
  if close ~= nil then
    close(stats)
  end
  commit()
  return true, stats
end

function record_failure(job, table_name, slice_key, error_text)
  run_sql("MERGE INTO " .. meta_table("LOAD_FAILURES") .. [[ F
USING (
  SELECT CAST(:t AS VARCHAR(128) UTF8) AS "TABLE_NAME", CAST(:k AS VARCHAR(2000) UTF8) AS "SLICE_KEY",
    CFG."CLOCK_UTC" AS "FAILED_AT"
  FROM ]] .. meta_table("LOADER_SETTINGS") .. [[ CFG
) X
ON (F."TABLE_NAME" = X."TABLE_NAME" AND F."SLICE_KEY" = X."SLICE_KEY")
WHEN MATCHED THEN UPDATE SET
  "FAILURES" = CASE WHEN F."LAST_RUN_ID" = :run THEN F."FAILURES" ELSE LEAST(F."FAILURES" + 1, ]]
    .. number_text(MAX_FAILURES) .. [[) END,
  "LAST_ERROR" = :e, "LAST_RUN_ID" = :run, "LAST_FAILED_AT" = X."FAILED_AT"
WHEN NOT MATCHED THEN INSERT VALUES (X."TABLE_NAME", X."SLICE_KEY", 1, :e, :run, X."FAILED_AT")]],
    {t = table_name, k = slice_key, e = clip(error_text, ERROR_LENGTH), run = job.run_id})
end

-- Orphans -----------------------------------------------------------------------------------------

function orphans_by_table(rank)
  local binds, profile = {}, ""
  if rank ~= nil then
    binds.rank = rank
    profile = ' WHERE O."TABLE_NAME" IN (SELECT P."TABLE_NAME" FROM ' .. meta_table("LOAD_PLAN")
      .. ' P WHERE P."PROFILE_RANK" <= :rank)'
  end
  local result = run_sql('SELECT O."TABLE_NAME", O."SLICE_KEY" FROM ' .. meta_table("LOAD_ORPHANS")
    .. " O" .. profile .. " ORDER BY 1, 2", binds)
  local orphans = {order = {}, keys = {}}
  for i = 1, #result do
    local name = nz(result[i].TABLE_NAME)
    if orphans.keys[name] == nil then
      orphans.keys[name] = {}
      orphans.order[#orphans.order + 1] = name
    end
    local keys = orphans.keys[name]
    keys[#keys + 1] = nz(result[i].SLICE_KEY)
  end
  return orphans
end

function orphan_detail(table_name, keys)
  local shown = {}
  for i = 1, math.min(#keys, LISTED_KEYS) do
    shown[i] = keys[i]
  end
  local more = ""
  if #keys > LISTED_KEYS then
    more = string.format(" and %d more", #keys - LISTED_KEYS)
  end
  return clip(string.format(
    "blocked: %d orphan slice(s) of %s match no planned slice key (%s%s); fetching the table again "
      .. "would pay twice. Migrate or delete them in %s.LOAD_LOG (listed in %s.LOAD_ORPHANS), "
      .. "then run again",
    #keys, table_name, table.concat(shown, ", "), more, PLAIN.META_SCHEMA, PLAIN.META_SCHEMA),
    DETAIL_LENGTH)
end
