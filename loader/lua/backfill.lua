-- loader/lua/backfill.lua: EXECUTE SCRIPT <infra>.BACKFILL(run_profile, from_season, max_calls)
-- (spec loader/backfill). Bundled after loadlib.lua; the script parameters are globals.
--
-- run_profile: 'demo' or 'full' (full includes demo). It is not called `profile`, a reserved word.
-- from_season: NULL or the newest season to load; slices without a season are always included.
-- max_calls: NULL or the most calls this run may spend (measured calls in live mode, slices in
-- replay mode); 0 is a dry run that registers gaps and checks every pending batch with EXPLAIN
-- VIRTUAL, fetching nothing.
--
-- The run, under the run lock (kind BACKFILL):
-- 1. Live mode reads the quota first (its own transaction); an unreadable quota fails the run
--    before any request.
-- 2. GAPS (step 1): the profile's known gaps become final empty LOAD_LOG slices, without a request.
-- 3. Tables with orphan slices are reported and never fetched (BACKFILL_QUEUE leaves them out).
-- 4. Batches, one step each (BATCH <table> #<entry>): the head of BACKFILL_QUEUE for the profile
--    and season, without slices that failed in this run. The allowance is max_calls minus what the
--    run was charged, and in live mode also MIN(QUOTA.BACKFILL_SPENDABLE, its value at the start
--    minus the charge). A batch has at most MAX_CALLS_PER_QUERY slices; in live mode also at most
--    allowance / FETCH_ATTEMPTS (at least one), because every request may take FETCH_ATTEMPTS
--    HTTP attempts. So a run spends at most FETCH_ATTEMPTS - 1 calls past its allowance (one slice
--    started with 1 to 3 calls left). A batch is charged MAX(measured /info delta, requests) in
--    live mode and its requests in replay mode; the measured delta is the step's and the run's
--    CALLS, also for a batch that raises after its fetch (then it is charged before the re-raise)
--    and, when /info cannot be read after a fetch, at least the batch's requests.
-- 5. A failed batch of several slices is retried slice by slice. A slice that fails alone gets one
--    LOAD_FAILURES failure for the run. A batch fails when none of its slices was stored, also
--    after its single-slice retries; one stored slice resets the count. After
--    STOP_AFTER_FAILED_BATCHES consecutive failed batches (one-slice batches and slices that had
--    failed before included) the cause is not one slice: the run stops with FAILED.
-- 6. The result: one row per table that was loaded, checked, blocked or had failures, then the
--    total row (TABLE_NAME '*'). CALLS_NEEDED counts the profile's pending slices (gaps and
--    quarantined slices excluded; tables blocked by orphans included).
-- Any error (SQL, Lua, drift, unreadable quota) goes through fail(): rollback, FAILED, re-raise.

local PROFILE_RANKS = {demo = 1, full = 2}
local FIRST_SEASON = 1869
local LAST_SEASON = 2100
local TOTAL_TABLE = "*"
local GAPS_STEP = 1
local RESULT_COLUMNS = "RUN_ID DECIMAL(18,0), TABLE_NAME VARCHAR(128), SLICES DECIMAL(9,0), "
  .. "ROWS_LOADED DECIMAL(18,0), CALLS DECIMAL(9,0), FAILED_SLICES DECIMAL(9,0), "
  .. "RUN_STATUS VARCHAR(20), CALLS_NEEDED DECIMAL(9,0), DETAIL VARCHAR(2000)"
local DETAIL_LENGTH = 2000
local SHOWN_FAILURES = 3
local FETCH_ATTEMPTS = 4
local STOP_AFTER_FAILED_BATCHES = 2

local function backfill_arguments(raw_profile, raw_from, raw_max)
  local name = nz(raw_profile)
  if type(name) ~= "string" or PROFILE_RANKS[string.lower(name)] == nil then
    error("BACKFILL: profile must be 'demo' or 'full', got " .. tostring(name), 0)
  end
  name = string.lower(name)
  local from = whole_number(raw_from, "BACKFILL: from_season")
  if from ~= nil and (from < FIRST_SEASON or from > LAST_SEASON) then
    error(string.format("BACKFILL: from_season must be NULL or a season from %d to %d, got %d",
      FIRST_SEASON, LAST_SEASON, from), 0)
  end
  local max = whole_number(raw_max, "BACKFILL: max_calls")
  if max ~= nil and max < 0 then
    error("BACKFILL: max_calls must be NULL, 0 (dry run) or more, got " .. max, 0)
  end
  local function shown(value)
    return value == nil and "NULL" or tostring(value)
  end
  return {
    profile = name,
    rank = PROFILE_RANKS[name],
    from_season = from,
    max_calls = max,
    text = name .. ", " .. shown(from) .. ", " .. shown(max),
  }
end

-- Per-table outcome -------------------------------------------------------------------------------

local function table_entry(run, name)
  local entry = run.tables[name]
  if entry == nil then
    entry = {name = name, slices = 0, rows = 0, calls = 0, batches = 0, checked = 0,
             failed = 0, failures = {}, blocked = nil}
    run.tables[name] = entry
    run.order[#run.order + 1] = name
  end
  return entry
end

local function table_detail(entry)
  local parts = {}
  if entry.blocked ~= nil then
    parts[#parts + 1] = entry.blocked
  end
  if entry.batches > 0 then
    parts[#parts + 1] = string.format("%d slice(s) loaded in %d batch(es)", entry.slices,
      entry.batches)
  end
  if entry.checked > 0 then
    parts[#parts + 1] = string.format("dry run: %d pending slice(s) checked with EXPLAIN VIRTUAL",
      entry.checked)
  end
  if entry.failed > 0 then
    local shown = {}
    for i = 1, math.min(#entry.failures, SHOWN_FAILURES) do
      shown[i] = entry.failures[i]
    end
    parts[#parts + 1] = string.format("%d slice(s) failed alone: %s", entry.failed,
      table.concat(shown, "; "))
  end
  return clip(table.concat(parts, "; "), DETAIL_LENGTH)
end

-- Queue and allowance -----------------------------------------------------------------------------

local function queue_rows(run, limit)
  local binds = {rank = run.args.rank, run = run.job.run_id}
  local season = ""
  if run.args.from_season ~= nil then
    season = ' AND ("SEASON" IS NULL OR "SEASON" <= :from_season)'
    binds.from_season = run.args.from_season
  end
  local head = ""
  if limit ~= nil then
    head = " LIMIT " .. string.format("%d", limit)
  end
  local result = run_sql([[SELECT "ENTRY_ID", "TABLE_NAME", "SLICING", "SEASON", "SLICE_KEY",
  "SEASON_FROM", "SEASON_TO", "FIXED_FILTER", "BATCH_ALONE"
FROM ]] .. meta_table("BACKFILL_QUEUE") .. [[
WHERE "PROFILE_RANK" <= :rank
  AND ("LAST_FAILED_RUN_ID" IS NULL OR "LAST_FAILED_RUN_ID" <> :run)]] .. season .. [[
ORDER BY "QUEUE_ORDER"]] .. head, binds)
  commit()
  return result
end

local function allowance(run)
  local room, reason = math.huge, nil
  if run.args.max_calls ~= nil then
    room, reason = run.args.max_calls - run.charged, "STOPPED_MAX_CALLS"
  end
  if run.vs.live then
    local budget = math.min(run.quota.backfill, run.budget_start - run.charged)
    if budget < room then
      room, reason = budget, "STOPPED_BUDGET"
    end
  end
  return room, reason
end

local function stop_detail(run, reason)
  if reason == "STOPPED_MAX_CALLS" then
    return string.format("stopped: max_calls %d reached (%d charged)", run.args.max_calls,
      run.charged)
  end
  return string.format("stopped: the backfill allowance is used up (QUOTA.BACKFILL_SPENDABLE %d, "
    .. "%d charged in this run); the refresh reserve stays for REFRESH_WEEK", run.quota.backfill,
    run.charged)
end

local function charge_unmeasured(job, step_no, requests)
  rollback()
  job.calls = job.calls + requests
  add_step_calls(job, step_no, requests)
  save_totals(job)
  commit()
end

local function measure(run, step_no, entry, requests)
  if not run.vs.live then
    run.charged = run.charged + requests
    return
  end
  local measured, quota, spent = pcall(measure_spend, run.job, step_no, run.quota)
  if not measured then
    if not is_killed(quota) then
      pcall(charge_unmeasured, run.job, step_no, requests)
    end
    error(quota, 0)
  end
  run.quota = quota
  run.charged = run.charged + math.max(spent, requests)
  entry.calls = entry.calls + spent
end

local function batch_size(run, room, per_query)
  if not run.vs.live then
    return math.min(room, per_query)
  end
  return math.max(1, math.min(per_query, room // FETCH_ATTEMPTS))
end

-- Batches -----------------------------------------------------------------------------------------

local function keys_text(batch)
  return table.concat(batch.keys, ", ")
end

local function close_raised_step(job, step_no, batch, err)
  step_end(job, step_no, "FAILED", {slices = 0, rows = 0, calls = 0},
    tostring(err) .. " (slices: " .. keys_text(batch) .. ")")
  commit_step(job)
end

local function charge_raised_batch(run, step_no, entry, batch, err)
  if is_killed(err) then
    error(err, 0)
  end
  rollback()
  if pcall(close_raised_step, run.job, step_no, batch, err) then
    pcall(measure, run, step_no, entry, #batch.keys)
  end
  error(err, 0)
end

local function fetch_batch(run, batch)
  local job = run.job
  local entry = table_entry(run, batch.table_name)
  run.step_no = run.step_no + 1
  local step_no = run.step_no
  step_begin(job, step_no, "BATCH " .. batch.table_name .. " #" .. batch.entry_id, keys_text(batch))
  local finished, loaded, outcome = pcall(load_batch, job, batch, function(stats)
    step_end(job, step_no, "DONE", {slices = stats.slices, rows = stats.rows, calls = 0},
      keys_text(batch))
    write_totals(job, job.slices + stats.slices, job.rows + stats.rows, job.calls)
  end)
  if not finished then
    charge_raised_batch(run, step_no, entry, batch, loaded)
  end
  if loaded then
    job.open_step = nil
    job.slices, job.rows = job.slices + outcome.slices, job.rows + outcome.rows
    entry.slices, entry.rows = entry.slices + outcome.slices, entry.rows + outcome.rows
    entry.batches = entry.batches + 1
  else
    step_end(job, step_no, "FAILED", {slices = 0, rows = 0, calls = 0},
      outcome .. " (slices: " .. keys_text(batch) .. ")")
    if #batch.keys == 1 then
      record_failure(job, batch.table_name, batch.keys[1], outcome)
      run.failed = run.failed + 1
      entry.failed = entry.failed + 1
      entry.failures[#entry.failures + 1] = batch.keys[1] .. ": " .. outcome
    end
    commit_step(job)
  end
  measure(run, step_no, entry, #batch.keys)
  return loaded, outcome
end

local function systemic(run, batch, problem)
  return string.format("%d consecutive batches failed, also when their slices were fetched "
    .. "alone, so the cause is not one slice; the run stopped to protect the quota. Last: batch "
    .. "%s #%d: %s", run.failed_batches, batch.table_name, batch.entry_id, problem)
end

local function after_failure(run, batch, problem)
  local stored = 0
  if #batch.keys > 1 then
    for _, single in ipairs(single_slice_batches(batch)) do
      local room, reason = allowance(run)
      if room < 1 then
        return reason, stop_detail(run, reason)
      end
      local loaded, outcome = fetch_batch(run, single)
      if loaded then
        stored = stored + 1
      else
        problem = outcome
      end
    end
  end
  if stored > 0 then
    run.failed_batches = 0
    return nil
  end
  run.failed_batches = run.failed_batches + 1
  if run.failed_batches >= STOP_AFTER_FAILED_BATCHES then
    return "FAILED", systemic(run, batch, problem)
  end
  return nil
end

local function finished(run)
  if run.failed > 0 then
    return "DONE_WITH_FAILURES", string.format("%d slice(s) failed alone in this run, see %s.LOAD_FAILURES",
      run.failed, PLAIN.META_SCHEMA)
  end
  return "DONE", "nothing left to fetch for the profile"
end

local function load_pending(run)
  local per_query = run.vs.max_calls_per_query
  while true do
    local rows = queue_rows(run, per_query)
    if #rows == 0 then
      return finished(run)
    end
    local room, reason = allowance(run)
    if room < 1 then
      return reason, stop_detail(run, reason)
    end
    local batch = split_batches(rows, batch_size(run, room, per_query))[1]
    local loaded, problem = fetch_batch(run, batch)
    if loaded then
      run.failed_batches = 0
    else
      local status, detail = after_failure(run, batch, problem)
      if status ~= nil then
        return status, detail
      end
    end
  end
end

local function check_pending(run)
  local job = run.job
  local batches = split_batches(queue_rows(run, nil), run.vs.max_calls_per_query)
  local slices = 0
  for _, batch in ipairs(batches) do
    run.step_no = run.step_no + 1
    step_begin(job, run.step_no, "EXPLAIN " .. batch.table_name .. " #" .. batch.entry_id,
      keys_text(batch))
    explain_check(batch)
    step_end(job, run.step_no, "DONE", {slices = 0, rows = 0, calls = 0}, string.format(
      "dry run: EXPLAIN VIRTUAL plans exactly these %d slice(s), nothing fetched (slices: %s)",
      #batch.keys, keys_text(batch)))
    commit_step(job)
    local entry = table_entry(run, batch.table_name)
    entry.checked = entry.checked + #batch.keys
    slices = slices + #batch.keys
  end
  return "DONE", string.format("dry run: %d pending slice(s) in %d batch(es) checked with EXPLAIN "
    .. "VIRTUAL, nothing fetched", slices, #batches)
end

-- Gaps, orphans, totals ---------------------------------------------------------------------------

local function register_gaps(run)
  local job = run.job
  step_begin(job, GAPS_STEP, "GAPS", nil)
  local result = run_sql("INSERT INTO " .. meta_table("LOAD_LOG")
    .. [[ ("TABLE_NAME", "SLICE_KEY", "IS_FINAL", "ROWS_LOADED", "LOADED_AT", "LOADED_BY")
SELECT S."TABLE_NAME", S."SLICE_KEY", TRUE, 0, CFG."NOW_UTC", 'LOAD_PLAN_GAP'
FROM ]] .. meta_table("LOAD_SLICES") .. [[ S
CROSS JOIN ]] .. meta_table("LOADER_SETTINGS") .. [[ CFG
WHERE S."IS_GAP" AND NOT S."IS_STORED" AND S."PROFILE_RANK" <= :rank
  AND NOT EXISTS (
    SELECT 1 FROM ]] .. meta_table("LOAD_ORPHANS") .. [[ O WHERE O."TABLE_NAME" = S."TABLE_NAME"
  )]], {rank = run.args.rank})
  run.gaps = whole_number(nz(result.rows_affected) or 0, "registered gaps")
  step_end(job, GAPS_STEP, "DONE", {slices = 0, rows = 0, calls = 0}, string.format(
    "%d known gap(s) registered as final empty slices, without a request", run.gaps))
  commit_step(job)
end

local function note_blocked_tables(run)
  local orphans = orphans_by_table(run.args.rank)
  commit()
  for _, name in ipairs(orphans.order) do
    table_entry(run, name).blocked = orphan_detail(name, orphans.keys[name])
    run.blocked[#run.blocked + 1] = name
  end
end

local function calls_needed(run)
  local result = run_sql('SELECT "TABLE_NAME", COUNT(*) AS "N" FROM ' .. meta_table("LOAD_SLICES")
    .. ' WHERE "IS_PENDING" AND "PROFILE_RANK" <= :rank GROUP BY "TABLE_NAME"',
    {rank = run.args.rank})
  local needed = {total = 0, by_table = {}}
  for i = 1, #result do
    local count = whole_number(result[i].N, "pending slices")
    needed.by_table[nz(result[i].TABLE_NAME)] = count
    needed.total = needed.total + count
  end
  return needed
end

local function total_detail(run, detail)
  local parts = {detail}
  if run.gaps > 0 then
    parts[#parts + 1] = string.format("%d gap(s) registered", run.gaps)
  end
  if #run.blocked > 0 then
    parts[#parts + 1] = "blocked by orphan slices: " .. table.concat(run.blocked, ", ")
  end
  return clip(table.concat(parts, "; "), DETAIL_LENGTH)
end

local function result_rows(run, status, detail, needed)
  local job = run.job
  local rows = {}
  for _, name in ipairs(run.order) do
    local entry = run.tables[name]
    rows[#rows + 1] = {job.run_id, name, entry.slices, entry.rows, entry.calls, entry.failed, status,
      needed.by_table[name] or 0, sql_value(table_detail(entry))}
  end
  rows[#rows + 1] = {job.run_id, TOTAL_TABLE, job.slices, job.rows, job.calls, run.failed, status,
    needed.total, sql_value(total_detail(run, detail))}
  return rows
end

local function backfill(job, args)
  local run = {job = job, args = args, vs = vs_settings(), charged = 0, failed = 0, gaps = 0,
               failed_batches = 0, step_no = GAPS_STEP, tables = {}, order = {}, blocked = {}}
  commit()
  job.fetch_source = run.vs.fetch_source
  if run.vs.live then
    run.quota = read_quota()
    run.budget_start = run.quota.backfill
    set_remaining(job, "REMAINING_BEFORE", run.quota.remaining)
    commit()
  end
  register_gaps(run)
  note_blocked_tables(run)
  local status, detail
  if args.max_calls == 0 then
    status, detail = check_pending(run)
  else
    status, detail = load_pending(run)
  end
  local needed = calls_needed(run)
  if run.vs.live then
    set_remaining(job, "REMAINING_AFTER", run.quota.remaining)
  end
  end_job(job, status, total_detail(run, detail))
  return result_rows(run, status, detail, needed)
end

local arguments = backfill_arguments(run_profile, from_season, max_calls)
local job = new_job("BACKFILL", arguments.text)
local ok, outcome = pcall(backfill, job, arguments)
if not ok then
  fail(job, outcome)
end
exit(outcome, RESULT_COLUMNS)
