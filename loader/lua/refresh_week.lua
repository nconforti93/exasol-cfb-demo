-- loader/lua/refresh_week.lua: EXECUTE SCRIPT <infra>.REFRESH_WEEK(season, week, season_type)
-- (spec loader/refresh-week). Bundled after loadlib.lua; the script parameters are globals.
--
-- season: the season to refresh. week, season_type: a week of the stored calendar of that season;
-- season_type is 'regular' or 'postseason' (any case), NULL means 'regular'. Both season types
-- number their weeks from 1, so (2026, 1, 'postseason') is the first bowl week.
--
-- The run, under the run lock (kind REFRESH_WEEK; RUN_ARGS are the arguments as given), is a chain
-- of steps. Every step is a REFRESH_LOG row committed when it starts and when it ends, so another
-- session (the app's timeline) sees the progress.
-- 1    CHECK: the arguments; the week with its season type in <core>.CALENDAR for the season; the
--      due slices: the season's slices of `weekly` entries whose profile is in REFRESH_PROFILES,
--      without gaps, that are missing or not final (quarantine does not apply); no table with due
--      slices has orphan slices; the registered steps' script names. Live mode with due slices
--      only: the quota (/info, free) and needed <= QUOTA.REFRESH_SPENDABLE (the refresh may use
--      the reserve that backfills keep). Nothing else reads /info: replay runs and runs with nothing
--      to fetch send no request at all.
-- 2-99 FETCH <table>: one step per table with due slices, in plan priority order. One load_batch
--      per plan entry, without the per-slice fallback and quarantine of BACKFILL: the slices'
--      rows are replaced in CFB_CORE together with LOAD_LOG (IS_FINAL = FINAL_IF_LOADED_NOW),
--      FETCH_LOG and the step's DONE row, in one transaction per entry. A failed fetch rolls back,
--      so the table keeps its previous rows. Each table commits on its own: tables fetched before a
--      failure stay refreshed. Live mode charges the measured /info delta to the step, and checks
--      the latest REFRESH_SPENDABLE again before each table.
-- 100- the registered steps of REFRESH_STEPS, by step number, each called as
--      EXECUTE SCRIPT <script>(run_id, season, week, season_type) with the normalized season type.
--      A step runs inside the refresh's transaction (nested scripts share it): the refresh commits
--      before the step, and commits the step's work together with its DONE row; a failed step is
--      rolled back before its FAILED row is written. A step must declare exactly these four
--      parameters, must not take the run lock (acquire_lock would abandon this run, whose row
--      belongs to the same session) and should read CFB_CORE, not CFBD_VS. In live runs that read
--      the quota, its /info delta is charged to it; otherwise its CALLS are 0.
-- A failed step stops the chain: its row is FAILED with the error, the run is FAILED, and the error
-- names the run and the step. After a kill or client abort (R0003) nothing can be logged; the next
-- run frees the RUNNING rows (idle or own session). The result is the run's REFRESH_LOG rows.

local CHECK_STEP = 1
local FIRST_FETCH_STEP = 2
local LAST_FETCH_STEP = 99
local FIRST_REGISTERED_STEP = 100
local LAST_REGISTERED_STEP = 999
local DEFAULT_SEASON_TYPE = "regular"
local SEASON_TYPES = {regular = true, postseason = true}
local SHOWN_ARGUMENT = 40
local DETAIL_LENGTH = 2000
local RESULT_COLUMNS = "RUN_ID DECIMAL(18,0), STEP_NO DECIMAL(3,0), STEP_NAME VARCHAR(160), "
  .. "STEP_STATUS VARCHAR(10), SECONDS DECIMAL(9,3), SLICES DECIMAL(9,0), "
  .. "ROWS_LOADED DECIMAL(18,0), CALLS DECIMAL(9,0), DETAIL VARCHAR(2000)"

-- Arguments ---------------------------------------------------------------------------------------

local function shown(value)
  if value == nil then
    return "NULL"
  end
  return clip(tostring(value), SHOWN_ARGUMENT)
end

local function arguments_text(given)
  return shown(given.season) .. ", " .. shown(given.week) .. ", " .. shown(given.season_type)
end

local function required_number(value, name)
  if value == nil then
    error(name .. " is required: a whole number, got NULL", 0)
  end
  return whole_number(value, name)
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

local function refresh_arguments(given)
  return {
    season = required_number(given.season, "season"),
    week = required_number(given.week, "week"),
    season_type = season_type_of(given.season_type),
  }
end

-- Calendar ----------------------------------------------------------------------------------------

local function weeks_text(row)
  local first, last = tostring(nz(row.FIRST_WEEK)), tostring(nz(row.LAST_WEEK))
  if first == last then
    return string.format("%s week %s", tostring(nz(row.TYPE_NAME)), first)
  end
  return string.format("%s weeks %s-%s", tostring(nz(row.TYPE_NAME)), first, last)
end

local function check_calendar(args)
  local result = run_sql([[SELECT LOWER("SEASON_TYPE") AS "TYPE_NAME", MIN("WEEK") AS "FIRST_WEEK",
  MAX("WEEK") AS "LAST_WEEK", SUM(CASE WHEN "WEEK" = :week THEN 1 ELSE 0 END) AS "WEEK_FOUND"
FROM ]] .. core_table("CALENDAR") .. [[
WHERE "SEASON" = :season
GROUP BY LOWER("SEASON_TYPE")
ORDER BY 1 DESC]], {season = args.season, week = args.week})
  if #result == 0 then
    error(string.format("season %d: no %d calendar is stored in %s.CALENDAR; run BACKFILL first, "
      .. "so the plan's CALENDAR entry loads it", args.season, args.season, PLAIN.CORE_SCHEMA), 0)
  end
  local stored = {}
  for i = 1, #result do
    local row = result[i]
    if nz(row.TYPE_NAME) == args.season_type and (nz(row.WEEK_FOUND) or 0) > 0 then
      return
    end
    stored[i] = weeks_text(row)
  end
  error(string.format("week %d (%s) is not in the %d calendar stored in %s.CALENDAR (%s). If that "
    .. "calendar is older than the week (a new bowl week, say), set IS_FINAL = FALSE on its "
    .. "CALENDAR slice in %s.LOAD_LOG and run BACKFILL to fetch it again", args.week,
    args.season_type, args.season, PLAIN.CORE_SCHEMA, table.concat(stored, ", "),
    PLAIN.META_SCHEMA), 0)
end

-- Due slices, orphans, registered steps, budget --------------------------------------------------

local function due_slices(args)
  return run_sql([[SELECT S."ENTRY_ID", S."TABLE_NAME", S."SLICING", S."SEASON", S."SLICE_KEY",
  P."SEASON_FROM", P."SEASON_TO", P."FIXED_FILTER", FALSE AS "BATCH_ALONE",
  MIN(S."PRIORITY") OVER (PARTITION BY S."TABLE_NAME") AS "TABLE_PRIORITY"
FROM ]] .. meta_table("LOAD_SLICES") .. [[ S
INNER JOIN ]] .. meta_table("LOAD_PLAN") .. [[ P
  ON P."ENTRY_ID" = S."ENTRY_ID"
CROSS JOIN ]] .. meta_table("LOADER_SETTINGS") .. [[ CFG
WHERE S."SEASON" = :season
  AND S."REFRESH_CLASS" = 'weekly'
  AND NOT S."IS_GAP"
  AND NOT (S."IS_STORED" AND S."IS_FINAL")
  AND INSTR(',' || CFG."REFRESH_PROFILES" || ',', ',' || S."PROFILE_NAME" || ',') > 0
ORDER BY "TABLE_PRIORITY", S."TABLE_NAME", S."ENTRY_ID", S."SLICE_KEY"]], {season = args.season})
end

local function by_table(rows)
  local tables, current = {}, nil
  for i = 1, #rows do
    local name = nz(rows[i].TABLE_NAME)
    if current == nil or current.name ~= name then
      current = {name = name, rows = {}, keys = {}}
      tables[#tables + 1] = current
    end
    current.rows[#current.rows + 1] = rows[i]
    current.keys[#current.keys + 1] = nz(rows[i].SLICE_KEY)
  end
  if #tables > LAST_FETCH_STEP - FIRST_FETCH_STEP + 1 then
    error(string.format("%d tables have due slices; the timeline numbers FETCH steps %d to %d",
      #tables, FIRST_FETCH_STEP, LAST_FETCH_STEP), 0)
  end
  return tables
end

local function refuse_orphans(tables)
  local orphans = orphans_by_table(nil)
  local problems = {}
  for _, due in ipairs(tables) do
    local keys = orphans.keys[due.name]
    if keys ~= nil then
      problems[#problems + 1] = orphan_detail(due.name, keys)
    end
  end
  if #problems > 0 then
    error(table.concat(problems, "; "), 0)
  end
end

local function registered_steps()
  local result = run_sql('SELECT "STEP_NO", "STEP_NAME", "SCRIPT_NAME" FROM '
    .. meta_table("REFRESH_STEPS") .. ' ORDER BY "STEP_NO"')
  local steps = {}
  for i = 1, #result do
    local number = whole_number(result[i].STEP_NO, "REFRESH_STEPS.STEP_NO")
    if number == nil or number < FIRST_REGISTERED_STEP or number > LAST_REGISTERED_STEP then
      error(string.format("%s.REFRESH_STEPS: step number %s is outside %d to %d; run `make deploy`",
        PLAIN.META_SCHEMA, tostring(number), FIRST_REGISTERED_STEP, LAST_REGISTERED_STEP), 0)
    end
    local script = nz(result[i].SCRIPT_NAME)
    steps[i] = {no = number, name = nz(result[i].STEP_NAME), script = script,
                call = qualified_name(script)}
  end
  return steps
end

local function require_budget(run, needed)
  if run.quota == nil then
    return
  end
  local available = nz(run.quota.refresh) or 0
  if needed > available then
    error(string.format("the fetch needs %d call(s), but only %d is available (%s.QUOTA."
      .. "REFRESH_SPENDABLE: the key's remaining calls within the project budget, the refresh "
      .. "reserve included). No request was sent", needed, available, PLAIN.META_SCHEMA), 0)
  end
end

-- Steps -------------------------------------------------------------------------------------------

local function new_step(number, name, script)
  return {no = number, name = name, script = script, slices = 0, rows = 0, calls = 0}
end

local function counts(step)
  return {slices = step.slices, rows = step.rows, calls = step.calls}
end

local function step_message(run, step, err)
  local where = step.name
  if step.script ~= nil then
    where = where .. " (" .. step.script .. ")"
  end
  return clip(string.format("REFRESH_WEEK run %d failed in step %d %s: %s", run.job.run_id,
    step.no, where, tostring(err)), DETAIL_LENGTH)
end

local function measure(run, step)
  if run.quota == nil then
    return
  end
  local spent
  run.quota, spent = measure_spend(run.job, step.no, run.quota)
  step.calls = step.calls + spent
end

local function close_failed(run, step, message)
  rollback()
  step_end(run.job, step.no, "FAILED", counts(step), message)
  commit_step(run.job)
end

local function in_step(run, step, action)
  local ok, err = pcall(action)
  if ok then
    return
  end
  if is_killed(err) then
    error(err, 0)
  end
  local message = step_message(run, step, err)
  if run.job.open_step == step.no and pcall(close_failed, run, step, message) then
    pcall(measure, run, step)
  end
  error(message, 0)
end

-- CHECK -------------------------------------------------------------------------------------------

local function names_of(tables)
  local names = {}
  for i, due in ipairs(tables) do
    names[i] = due.name
  end
  return table.concat(names, ", ")
end

local function check_detail(run)
  local args = run.args
  local parts = {string.format("season %d week %d (%s) is in the stored calendar", args.season,
    args.week, args.season_type)}
  if run.needed == 0 then
    parts[#parts + 1] = string.format("nothing to fetch: no weekly slice of %d is missing or not "
      .. "final", args.season)
  else
    parts[#parts + 1] = string.format("%d slice(s) to fetch in %d table(s): %s", run.needed,
      #run.tables, names_of(run.tables))
  end
  if run.quota ~= nil then
    parts[#parts + 1] = string.format("%d call(s) needed, %d available (QUOTA.REFRESH_SPENDABLE)",
      run.needed, nz(run.quota.refresh) or 0)
  elseif not run.vs.live then
    parts[#parts + 1] = "replay mode: no quota read, no call"
  end
  if #run.steps > 0 then
    local registered = {}
    for i, step in ipairs(run.steps) do
      registered[i] = step.no .. " " .. step.name
    end
    parts[#parts + 1] = "registered steps: " .. table.concat(registered, ", ")
  end
  return clip(table.concat(parts, "; "), DETAIL_LENGTH)
end

local function check(run)
  local job = run.job
  local step = new_step(CHECK_STEP, "CHECK")
  step_begin(job, step.no, step.name, nil)
  in_step(run, step, function()
    run.args = refresh_arguments(run.given)
    run.vs = vs_settings()
    job.fetch_source = run.vs.fetch_source
    check_calendar(run.args)
    local due = due_slices(run.args)
    run.needed = #due
    run.tables = by_table(due)
    refuse_orphans(run.tables)
    run.steps = registered_steps()
    if run.vs.live and run.needed > 0 then
      run.quota = read_quota()
      require_budget(run, run.needed)
      set_remaining(job, "REMAINING_BEFORE", run.quota.remaining)
    end
    step_end(job, step.no, "DONE", counts(step), check_detail(run))
    commit_step(job)
  end)
end

-- FETCH <table> -----------------------------------------------------------------------------------

local function fetch_table(run, number, due)
  local job = run.job
  local step = new_step(number, "FETCH " .. due.name)
  local detail = "slices: " .. table.concat(due.keys, ", ")
  step_begin(job, step.no, step.name, detail)
  in_step(run, step, function()
    require_budget(run, #due.keys)
    local batches = split_batches(due.rows, run.vs.max_calls_per_query)
    for i, batch in ipairs(batches) do
      local status = i == #batches and "DONE" or "RUNNING"
      local loaded, outcome = load_batch(job, batch, function(stats)
        step_end(job, step.no, status, {slices = step.slices + stats.slices,
          rows = step.rows + stats.rows, calls = step.calls}, detail)
        write_totals(job, job.slices + stats.slices, job.rows + stats.rows, job.calls)
      end)
      if not loaded then
        error(string.format("slice(s) %s: %s; the stored rows of the slice(s) are unchanged",
          table.concat(batch.keys, ", "), outcome), 0)
      end
      step.slices, step.rows = step.slices + outcome.slices, step.rows + outcome.rows
      job.slices, job.rows = job.slices + outcome.slices, job.rows + outcome.rows
      if status == "DONE" then
        job.open_step = nil
      end
      measure(run, step)
    end
  end)
end

-- Registered steps --------------------------------------------------------------------------------

local function run_registered(run, registered)
  local job, args = run.job, run.args
  local step = new_step(registered.no, registered.name, registered.script)
  local detail = string.format("EXECUTE SCRIPT %s(%d, %d, %d, '%s')", registered.script,
    job.run_id, args.season, args.week, args.season_type)
  step_begin(job, step.no, step.name, detail)
  in_step(run, step, function()
    run_sql("EXECUTE SCRIPT " .. registered.call .. "(:run_id, :season, :week, :season_type)",
      {run_id = job.run_id, season = args.season, week = args.week,
       season_type = args.season_type})
    step_end(job, step.no, "DONE", counts(step), detail)
    commit_step(job)
    measure(run, step)
  end)
end

-- Result ------------------------------------------------------------------------------------------

local function timeline(job)
  local result = run_sql([[SELECT "RUN_ID", "STEP_NO", "STEP_NAME", "STEP_STATUS",
  CAST(SECONDS_BETWEEN("ENDED_AT", "STARTED_AT") AS DECIMAL(9,3)) AS "SECONDS",
  "SLICES", "ROWS_LOADED", "CALLS", "DETAIL"
FROM ]] .. meta_table("REFRESH_LOG") .. [[
WHERE "RUN_ID" = :run
ORDER BY "STEP_NO"]], {run = job.run_id})
  local rows = {}
  for i = 1, #result do
    local row = result[i]
    rows[i] = {row.RUN_ID, row.STEP_NO, row.STEP_NAME, row.STEP_STATUS, row.SECONDS, row.SLICES,
      row.ROWS_LOADED, row.CALLS, row.DETAIL}
  end
  return rows
end

local function done_detail(run)
  local args = run.args
  return string.format("season %d week %d (%s): %d slice(s) refreshed in %d table(s), %d "
    .. "registered step(s)", args.season, args.week, args.season_type, run.job.slices,
    #run.tables, #run.steps)
end

local function refresh(job, given)
  local run = {job = job, given = given}
  check(run)
  for i, due in ipairs(run.tables) do
    fetch_table(run, FIRST_FETCH_STEP + i - 1, due)
  end
  for _, registered in ipairs(run.steps) do
    run_registered(run, registered)
  end
  if run.quota ~= nil then
    set_remaining(job, "REMAINING_AFTER", run.quota.remaining)
  end
  local rows = timeline(job)
  end_job(job, "DONE", done_detail(run))
  return rows
end

local given = {season = nz(season), week = nz(week), season_type = nz(season_type)}
local job = new_job("REFRESH_WEEK", arguments_text(given))
local ok, outcome = pcall(refresh, job, given)
if not ok then
  fail(job, outcome)
end
exit(outcome, RESULT_COLUMNS)
