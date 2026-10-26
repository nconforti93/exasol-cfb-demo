-- model/lua/refresh_model.lua: EXECUTE SCRIPT <infra>.REFRESH_MODEL(run_id, season, week, season_type)
-- (spec model/refresh-step; decisions [15] and [40]). Bundled by model/step.py: the namespace
-- constants (PLAIN, QUOTED, with MODEL_SCHEMA), the generated DERIVED_TABLES list, then
-- loader/lua/loadlib.lua and this file, as one Lua chunk. The script parameters are globals.
--
-- 1 Arguments. run_id NULL: a run of its own under the loader's run lock (kind MODEL, script rule),
--   refused while a loader run, a snapshot, a restore or a deploy holds the lock. run_id set: a
--   registered REFRESH_WEEK step (step 100 MODEL). The run must be RUNNING in this session; the step
--   works under the refresh's lock and never takes it (the own-session rule would abandon the
--   refresh's row). season NULL, with week and season_type NULL: rebuild only, no checks. Otherwise
--   week is required, and a NULL season_type means 'regular'.
-- 2 Rebuild. Every derived table of the model (DERIVED_TABLES, in file order, so a table reads only
--   tables rebuilt before it) is emptied (TRUNCATE) and filled by its INSERT, in one transaction,
--   then COMMIT. A failure rolls the whole rebuild back, so the tables keep their previous rows.
--   The tables are therefore current as of the last load that ran this step or its post-hooks.
-- 3 Point. AS_OF_WEEK_SEQ from AS_OF_POINT (rebuilt in 2) for (season, week, season_type). An
--   unknown point raises an error and logs nothing.
-- 4 Checks. MODEL_CHECKS for the season and the point's week, evaluated once, written to
--   <meta>.MODEL_CHECK_LOG with the run id (NULL outside a refresh), then COMMIT. The refresh has
--   committed its step row before the step started, so this COMMIT ends no open refresh state.
-- 5 A failed ERROR check raises an error that names every one of them; REFRESH_WEEK then stops its
--   chain. The rebuilt tables and the log rows stay committed.
-- Result: one row per rebuilt table (ITEM_KIND TABLE, OBSERVED = rows) and per check (CHECK).
-- Zero CFBD calls: nothing here reads the virtual schema.

local LOCK_KIND = "MODEL"
local DEFAULT_SEASON_TYPE = "regular"
local SEASON_TYPES = {regular = true, postseason = true}
local SHOWN_ARGUMENT = 40
local DETAIL_LENGTH = 2000
local LISTED_FAILURES = 10
local ERROR_SEVERITY = "ERROR"
local FAILED_STATUS = "FAIL"
local UTC_NOW = "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')"
local RESULT_COLUMNS = "ITEM_KIND VARCHAR(8) UTF8, ITEM_NAME VARCHAR(64) UTF8, "
  .. "SEVERITY VARCHAR(8) UTF8, ITEM_STATUS VARCHAR(8) UTF8, OBSERVED DECIMAL(18,0), "
  .. "DETAIL VARCHAR(2000) UTF8"
local LOG_COLUMNS = '"RUN_ID", "AS_OF_SEASON", "AS_OF_WEEK_SEQ", "CHECK_NAME", "SEVERITY", '
  .. '"CHECK_SEASON", "CHECK_WEEK_SEQ", "CHECK_STATUS", "OBSERVED", "DETAIL", "CHECKED_AT"'

-- Arguments ---------------------------------------------------------------------------------------

local function model_table(name)
  if type(name) ~= "string" or not name:match("^[A-Z][A-Z0-9_]*$") then
    error("REFRESH_MODEL: not an UPPER_CASE identifier: " .. tostring(name), 0)
  end
  return QUOTED.MODEL_SCHEMA .. '."' .. name .. '"'
end

local function shown(value)
  if value == nil then
    return "NULL"
  end
  return clip(tostring(value), SHOWN_ARGUMENT)
end

local function arguments_text(given)
  return shown(given.run_id) .. ", " .. shown(given.season) .. ", " .. shown(given.week) .. ", "
    .. shown(given.season_type)
end

local function season_type_of(value)
  if value == nil then
    return DEFAULT_SEASON_TYPE
  end
  if type(value) == "string" and SEASON_TYPES[string.lower(value)] then
    return string.lower(value)
  end
  error(string.format("season_type '%s' is unknown: use 'regular' or 'postseason' (NULL means "
    .. "'regular')", shown(value)), 0)
end

local function model_arguments(given)
  local args = {run_id = whole_number(given.run_id, "run_id")}
  if given.season == nil then
    if given.week ~= nil or given.season_type ~= nil then
      error(string.format("season is NULL, which rebuilds the model without checks; week and "
        .. "season_type must then be NULL too, got %s and %s", shown(given.week),
        shown(given.season_type)), 0)
    end
    return args
  end
  args.season = whole_number(given.season, "season")
  args.week = whole_number(given.week, "week")
  if args.week == nil then
    error("week is required with a season: a whole number, got NULL", 0)
  end
  args.season_type = season_type_of(given.season_type)
  return args
end

local function require_refresh_run(run_id)
  local result = run_sql('SELECT COUNT(*) AS "RUNS" FROM ' .. meta_table("LOAD_RUN")
    .. [[ WHERE "RUN_ID" = :run AND "RUN_STATUS" = 'RUNNING' AND "SESSION_ID" = CURRENT_SESSION]],
    {run = run_id})
  if (whole_number(result[1].RUNS, "RUNS") or 0) == 0 then
    error(string.format("run_id %d is not a running loader run of this session: REFRESH_WEEK passes "
      .. "its run id to its steps. Run the model step on its own with run_id NULL", run_id), 0)
  end
end

-- Rebuild -----------------------------------------------------------------------------------------

local function rebuild(items)
  local total = 0
  for _, derived in ipairs(DERIVED_TABLES) do
    run_sql("TRUNCATE TABLE " .. model_table(derived.name))
    local result = run_sql(derived.insert)
    local rows = whole_number(nz(result.rows_inserted) or nz(result.rows_affected) or 0,
      "rows inserted into " .. derived.name)
    total = total + rows
    items[#items + 1] = {"TABLE", derived.name, null, "REBUILT", rows, null}
  end
  commit()
  return total
end

-- Point and checks --------------------------------------------------------------------------------

local function point_of(args)
  local result = run_sql('SELECT "AS_OF_WEEK_SEQ" FROM ' .. model_table("AS_OF_POINT")
    .. [[ WHERE "AS_OF_SEASON" = :season AND "AS_OF_WEEK" = :week
  AND "AS_OF_SEASON_TYPE" = :season_type]],
    {season = args.season, week = args.week, season_type = args.season_type})
  if #result == 0 then
    error(string.format("season %d, %s week %d is unknown: %s.AS_OF_POINT has no such point (the "
      .. "points are the stored weeks of the season and its preseason point)", args.season,
      args.season_type, args.week, PLAIN.MODEL_SCHEMA), 0)
  end
  return whole_number(result[1].AS_OF_WEEK_SEQ, "AS_OF_WEEK_SEQ")
end

local function evaluate(args, sequence)
  local result = run_sql([[SELECT "CHECK_NAME", "SEVERITY", "SEASON", "WEEK_SEQ", "CHECK_STATUS",
  "OBSERVED", "DETAIL"
FROM ]] .. model_table("MODEL_CHECKS") .. [[
WHERE "SEASON" = :season AND ("WEEK_SEQ" IS NULL OR "WEEK_SEQ" = :seq)
ORDER BY "SEVERITY", "CHECK_NAME", "WEEK_SEQ"]], {season = args.season, seq = sequence})
  local checks = {}
  for i = 1, #result do
    local row = result[i]
    checks[i] = {name = nz(row.CHECK_NAME), severity = nz(row.SEVERITY), season = nz(row.SEASON),
      week_seq = nz(row.WEEK_SEQ), status = nz(row.CHECK_STATUS), observed = nz(row.OBSERVED),
      detail = clip(nz(row.DETAIL), DETAIL_LENGTH)}
  end
  return checks
end

local function log_checks(args, sequence, checks)
  if #checks == 0 then
    return
  end
  local rows = {}
  local binds = {run = sql_value(args.run_id), season = args.season, seq = sequence}
  for i, check in ipairs(checks) do
    local p = "c" .. i .. "_"
    rows[i] = string.format("(:run, :season, :seq, :%sname, :%sseverity, :%sseason, :%sweek, "
      .. ":%sstatus, :%sobserved, :%sdetail, %s)", p, p, p, p, p, p, p, UTC_NOW)
    binds[p .. "name"] = check.name
    binds[p .. "severity"] = check.severity
    binds[p .. "season"] = check.season
    binds[p .. "week"] = sql_value(check.week_seq)
    binds[p .. "status"] = check.status
    binds[p .. "observed"] = check.observed
    binds[p .. "detail"] = sql_value(check.detail)
  end
  run_sql("INSERT INTO " .. meta_table("MODEL_CHECK_LOG") .. " (" .. LOG_COLUMNS .. ") VALUES "
    .. table.concat(rows, ", "), binds)
end

local function failed_errors(checks)
  local failed = {}
  for _, check in ipairs(checks) do
    if check.severity == ERROR_SEVERITY and check.status == FAILED_STATUS then
      local where = check.week_seq == nil and "" or (" week " .. tostring(check.week_seq))
      failed[#failed + 1] = string.format("%s%s (%s)", tostring(check.name), where,
        tostring(check.observed))
    end
  end
  return failed
end

-- The step ----------------------------------------------------------------------------------------

local function refresh_model(args)
  local items = {}
  local total = rebuild(items)
  local detail = string.format("rebuilt %d derived table(s), %d rows", #DERIVED_TABLES, total)
  if args.season == nil then
    return {rows = items, detail = detail .. "; no checks (season NULL)"}
  end
  local sequence = point_of(args)
  local checks = evaluate(args, sequence)
  log_checks(args, sequence, checks)
  commit()
  for _, check in ipairs(checks) do
    items[#items + 1] = {"CHECK", check.name, check.severity, check.status,
      sql_value(check.observed), sql_value(check.detail)}
  end
  local failed = failed_errors(checks)
  local point = string.format("season %d, %s week %d", args.season, args.season_type, args.week)
  if #failed > 0 then
    local listed = {}
    for i = 1, math.min(#failed, LISTED_FAILURES) do
      listed[i] = failed[i]
    end
    error(clip(string.format("MODEL: %d ERROR check(s) failed for %s: %s. The derived tables are "
      .. "rebuilt; the checks are in %s.MODEL_CHECK_LOG", #failed, point,
      table.concat(listed, ", "), PLAIN.META_SCHEMA), DETAIL_LENGTH), 0)
  end
  return {rows = items, detail = string.format("%s; %d check(s) for %s, no ERROR failed", detail,
    #checks, point)}
end

local given = {run_id = nz(run_id), season = nz(season), week = nz(week),
               season_type = nz(season_type)}
local args = model_arguments(given)
local outcome
if args.run_id == nil then
  local job = new_job(LOCK_KIND, arguments_text(given))
  local ok, result = pcall(refresh_model, args)
  if not ok then
    fail(job, result)
  end
  end_job(job, "DONE", result.detail)
  outcome = result
else
  require_refresh_run(args.run_id)
  local ok, result = pcall(refresh_model, args)
  if not ok then
    rollback()
    error(result, 0)
  end
  outcome = result
end
exit(outcome.rows, RESULT_COLUMNS)
