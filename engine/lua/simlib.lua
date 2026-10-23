-- engine/lua/simlib.lua: the run library of Phase 4's Lua scripts (SIMULATE; slice 3's refresh step).
-- Bundled by engine/deploy.py with loader.bundle.build_entry_script: the namespace constants (PLAIN and
-- QUOTED, with SIM_SCHEMA and MODEL_SCHEMA), the generated prelude (MODEL_COLUMN_NAMES, AUTO_BID_CODES,
-- G6_BID_CODES from engine/core.py), loader/lua/loadlib.lua, this file, then the entry file.
--
-- It follows loadlib's rules: every statement through run_sql (pquery), ROLLBACK before logging a
-- failure, nothing logged after a kill (R0003), whole numbers checked before they enter statement
-- text, every other value bound. It never takes the run lock: the entry does (kind SIMULATE), a
-- registered refresh step works under the refresh's lock.
--
-- Public API (globals):
--   sim_arguments(given) -> args: checks the call's arguments without reading anything; defaults
--       season_type 'regular', n_sims 100,000, sims_per_batch 5,000, run_label NULL.
--   sim_point(args) -> point: reads the season's SEASON_RULES row, the AS_OF_POINT row and the
--       SCORE_PARAMS constants; refuses an unknown rule set or point, a cutoff that is NULL or not
--       before SELECTION_AT, unknown rule codes and missing constants. Fills args.run_seed when NULL
--       (season x 1,000 + the point's week sequence). Writes nothing.
--   sim_arguments_text(args) -> text for LOAD_RUN.RUN_ARGS.
--   simulate_run(args, point, origin, load_run_id) -> result: registers the run (RUNNING, committed),
--       snapshots the inputs (committed), runs the engine into SIM_STAGE, splits it into the outputs,
--       truncates the stage and sets the run DONE with its timings (one transaction, committed).
--       On an error it rolls back, deletes the run's outputs, truncates the stage, sets the run
--       FAILED with the message, commits and raises; after a kill it only raises.
--       result = {run_id, row (the script's result row), summary}.
--   SIM_RESULT_COLUMNS: the column list of that row for exit().

local SIM_DEFAULT_SEASON_TYPE = "regular"
local SIM_SEASON_TYPES = {regular = true, postseason = true}
local SIM_DEFAULT_N_SIMS = 100000
local SIM_DEFAULT_BATCH = 5000
local SIM_MIN_BATCH = 100
local SIM_MAX_BATCH = 20000
local SIM_MAX_BATCHES = 10000
local SIM_MAX_SEED = 999999999999999999
local SIM_SEED_WEEKS = 1000
local SIM_PLAYOFF_TEAMS = 12
local SIM_LABEL_LENGTH = 200
local SIM_DETAIL_LENGTH = 2000
local SIM_SHOWN = 40
local SIM_UTC_NOW = "CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')"
local SIM_ENGINE_SCRIPT = "SIM_ENGINE"
local SIM_TEST_PREFIX = "TEST_"
local SIM_OUTPUT_TABLES = {"SIM_TEAM", "SIM_PATH", "TEAM_WINS", "CONF_CHAMP", "LEVERAGE"}
local SIM_SCORE_KEYS = {
  {key = "hfa", column = "HFA"},
  {key = "game_sigma", column = "SIGMA_1"},
  {key = "ot_margin", column = "OT_MARGIN"},
  {key = "margin_cap", column = "MARGIN_CAP"},
  {key = "w_win_pct", column = "W_WIN_PCT"},
  {key = "w_sos", column = "W_SOS"},
  {key = "w_pd", column = "W_PD"},
  {key = "w_rating", column = "W_RATING"},
}

SIM_RESULT_COLUMNS = "RUN_ID DECIMAL(18,0), RUN_STATUS VARCHAR(10) UTF8, N_SIMS DECIMAL(18,0), "
  .. "N_BATCHES DECIMAL(9,0), N_TEAMS DECIMAL(5,0), N_GAMES DECIMAL(5,0), SECONDS_TOTAL DOUBLE, "
  .. "SIMS_PER_SECOND DOUBLE, DETAIL VARCHAR(2000) UTF8"

-- Names and numbers ------------------------------------------------------------------------------

local function sim_name(schema_key, name)
  if type(name) ~= "string" or not name:match("^[A-Z][A-Z0-9_]*$") then
    error("SIMULATE: not an UPPER_CASE identifier: " .. tostring(name), 0)
  end
  return QUOTED[schema_key] .. '."' .. name .. '"'
end

local function sim_table(name)
  return sim_name("SIM_SCHEMA", name)
end

local function model_object(name)
  return sim_name("MODEL_SCHEMA", name)
end

local function int_sql(value, what)
  local number = whole_number(value, what)
  if number == nil then
    error("SIMULATE: " .. what .. " is NULL", 0)
  end
  return string.format("%d", number)
end

local function shown(value)
  if value == nil then
    return "NULL"
  end
  return clip(tostring(value), SIM_SHOWN)
end

local function number_of(value)
  value = nz(value)
  if value == nil then
    return nil
  end
  return tonumber(tostring(value))
end

-- Arguments ---------------------------------------------------------------------------------------

function sim_arguments(given)
  local args = {}
  args.season = whole_number(given.season, "season")
  args.week = whole_number(given.week, "week")
  if args.season == nil or args.week == nil then
    error(string.format("SIMULATE needs a season and a week, got %s and %s", shown(given.season),
      shown(given.week)), 0)
  end
  local season_type = given.season_type
  if season_type == nil then
    args.season_type = SIM_DEFAULT_SEASON_TYPE
  elseif type(season_type) == "string" and SIM_SEASON_TYPES[string.lower(season_type)] then
    args.season_type = string.lower(season_type)
  else
    error(string.format("season_type '%s' is unknown: use 'regular' or 'postseason' (NULL means "
      .. "'regular')", shown(season_type)), 0)
  end
  args.n_sims = whole_number(given.n_sims, "n_sims") or SIM_DEFAULT_N_SIMS
  args.sims_per_batch = whole_number(given.sims_per_batch, "sims_per_batch") or SIM_DEFAULT_BATCH
  if args.sims_per_batch < SIM_MIN_BATCH or args.sims_per_batch > SIM_MAX_BATCH then
    error(string.format("SIMULATE refused: sims_per_batch %d is outside %d to %d",
      args.sims_per_batch, SIM_MIN_BATCH, SIM_MAX_BATCH), 0)
  end
  if args.n_sims < 1 or args.n_sims % args.sims_per_batch ~= 0 then
    error(string.format("SIMULATE refused: n_sims %d is not a positive multiple of sims_per_batch %d",
      args.n_sims, args.sims_per_batch), 0)
  end
  args.n_batches = args.n_sims // args.sims_per_batch
  if args.n_batches > SIM_MAX_BATCHES then
    error(string.format("SIMULATE refused: %d batches is more than %d; raise sims_per_batch",
      args.n_batches, SIM_MAX_BATCHES), 0)
  end
  args.run_seed = whole_number(given.run_seed, "run_seed")
  if args.run_seed ~= nil and (args.run_seed < 0 or args.run_seed > SIM_MAX_SEED) then
    error(string.format("SIMULATE refused: run_seed %s is outside 0 to %d", shown(args.run_seed),
      SIM_MAX_SEED), 0)
  end
  if given.run_label ~= nil then
    args.run_label = clip(tostring(given.run_label), SIM_LABEL_LENGTH)
  end
  return args
end

function sim_arguments_text(args)
  return string.format("%d, %d, '%s', %d, %d, %s, %s", args.season, args.week, args.season_type,
    args.n_sims, args.sims_per_batch, shown(args.run_seed), shown(args.run_label))
end

-- Point, rules and constants (read only) ----------------------------------------------------------

local function read_rules(args)
  local rows = run_sql([[SELECT CAST("SELECTION_AT" AS VARCHAR(30)) AS "SELECTION_TEXT", "AUTO_BID_RULE",
  "AUTO_BIDS", "G6_BID", "GUARANTEE_SCHOOL", "GUARANTEE_RANK", "MIN_CONFERENCE_TEAMS"
FROM ]] .. sim_table("SEASON_RULES") .. [[ WHERE "SEASON" = :season]], {season = args.season})
  if #rows == 0 then
    error(string.format("SIMULATE refused: season %d has no rule set in %s.SEASON_RULES (the "
      .. "seasons that may be simulated)", args.season, PLAIN.SIM_SCHEMA), 0)
  end
  local row = rows[1]
  local rules = {
    selection_at = nz(row.SELECTION_TEXT),
    auto_bid_rule = AUTO_BID_CODES[nz(row.AUTO_BID_RULE) or ""],
    g6_bid = G6_BID_CODES[nz(row.G6_BID) or ""],
    auto_bids = whole_number(row.AUTO_BIDS, "AUTO_BIDS"),
    guarantee_school = nz(row.GUARANTEE_SCHOOL),
    guarantee_rank = whole_number(row.GUARANTEE_RANK, "GUARANTEE_RANK"),
    min_conference_teams = whole_number(row.MIN_CONFERENCE_TEAMS, "MIN_CONFERENCE_TEAMS"),
  }
  if rules.auto_bid_rule == nil or rules.g6_bid == nil then
    error(string.format("SIMULATE refused: SEASON_RULES of %d has an unknown AUTO_BID_RULE %s or "
      .. "G6_BID %s", args.season, shown(nz(row.AUTO_BID_RULE)), shown(nz(row.G6_BID))), 0)
  end
  return rules
end

local function read_point(args, rules)
  local rows = run_sql([[SELECT p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL",
  CAST(p."CUTOFF_AT" AS VARCHAR(30)) AS "CUTOFF_TEXT",
  CASE WHEN p."CUTOFF_AT" < CAST(:selection AS TIMESTAMP) THEN 1 ELSE 0 END AS "BEFORE_SELECTION"
FROM ]] .. model_object("AS_OF_POINT") .. [[ p
WHERE p."AS_OF_SEASON" = :season AND p."AS_OF_WEEK" = :week AND p."AS_OF_SEASON_TYPE" = :season_type]],
    {season = args.season, week = args.week, season_type = args.season_type,
     selection = rules.selection_at})
  if #rows == 0 then
    error(string.format("SIMULATE refused: season %d, %s week %d is unknown: %s.AS_OF_POINT has no "
      .. "such point", args.season, args.season_type, args.week, PLAIN.MODEL_SCHEMA), 0)
  end
  local row = rows[1]
  if nz(row.CUTOFF_TEXT) == nil or whole_number(row.BEFORE_SELECTION, "BEFORE_SELECTION") ~= 1 then
    error(string.format("SIMULATE refused: the point's cutoff %s is not before selection day %s; "
      .. "the playoff field is known from then on", shown(nz(row.CUTOFF_TEXT)),
      shown(rules.selection_at)), 0)
  end
  return {week_seq = whole_number(row.AS_OF_WEEK_SEQ, "AS_OF_WEEK_SEQ"), label = nz(row.AS_OF_LABEL),
          cutoff_at = nz(row.CUTOFF_TEXT), rules = rules}
end

local function check_constants()
  local columns = {"TAU"}
  for _, item in ipairs(SIM_SCORE_KEYS) do
    columns[#columns + 1] = item.column
  end
  local quoted = {}
  for i, column in ipairs(columns) do
    quoted[i] = '"' .. column .. '"'
  end
  local rows = run_sql("SELECT " .. table.concat(quoted, ", ") .. " FROM "
    .. sim_table("SCORE_PARAMS"))
  local missing = {}
  for _, column in ipairs(columns) do
    if #rows == 0 or nz(rows[1][column]) == nil then
      missing[#missing + 1] = column
    end
  end
  if #missing > 0 then
    error("SIMULATE refused: " .. PLAIN.SIM_SCHEMA .. ".SIM_PARAMS has no "
      .. table.concat(missing, ", ") .. "; deploy the seed with make sim", 0)
  end
end

function sim_point(args)
  local rules = read_rules(args)
  local point = read_point(args, rules)
  check_constants()
  if args.run_seed == nil then
    args.run_seed = args.season * SIM_SEED_WEEKS + point.week_seq
  end
  return point
end

-- Clock -------------------------------------------------------------------------------------------

local function utc_now()
  local rows = run_sql("SELECT CAST(" .. SIM_UTC_NOW .. ' AS VARCHAR(30)) AS "NOW_TEXT" FROM DUAL')
  return nz(rows[1].NOW_TEXT)
end

local function seconds_since(started)
  local rows = run_sql("SELECT CAST(SECONDS_BETWEEN(" .. SIM_UTC_NOW .. [[,
  CAST(:started AS TIMESTAMP)) AS DOUBLE) AS "ELAPSED" FROM DUAL]], {started = started})
  return number_of(rows[1].ELAPSED) or 0
end

local function new_clock()
  return {started = utc_now(), last = 0, laps = {}}
end

local function lap(clock, name)
  local now = seconds_since(clock.started)
  clock.laps[#clock.laps + 1] = {name = name, seconds = now - clock.last}
  clock.last = now
  return now
end

local function laps_text(clock, first, last)
  local parts = {}
  for i = first, last do
    parts[#parts + 1] = string.format("%s %.2f", clock.laps[i].name, clock.laps[i].seconds)
  end
  return table.concat(parts, ", ")
end

-- Register and snapshot ---------------------------------------------------------------------------

local function register(args, point, origin, load_run_id, clock)
  run_sql("TRUNCATE TABLE " .. sim_table("SIM_STAGE"))
  run_sql("INSERT INTO " .. sim_table("SIM_RUN") .. [[ ("RUN_STATUS", "RUN_ORIGIN", "RUN_LABEL",
  "LOAD_RUN_ID", "AS_OF_SEASON", "AS_OF_WEEK", "AS_OF_SEASON_TYPE", "AS_OF_WEEK_SEQ", "AS_OF_LABEL",
  "RUN_SEED", "N_SIMS", "SIMS_PER_BATCH", "N_BATCHES", "STARTED_AT")
VALUES ('RUNNING', :origin, :label, :load_run, :season, :week, :season_type, :week_seq, :point_label,
  :seed, :n_sims, :batch, :n_batches, CAST(:started AS TIMESTAMP))]],
    {origin = origin, label = sql_value(args.run_label), load_run = sql_value(load_run_id),
     season = args.season, week = args.week, season_type = args.season_type,
     week_seq = point.week_seq, point_label = sql_value(point.label), seed = args.run_seed,
     n_sims = args.n_sims, batch = args.sims_per_batch, n_batches = args.n_batches,
     started = clock.started})
  local rows = run_sql('SELECT MAX("RUN_ID") AS "RUN_ID" FROM ' .. sim_table("SIM_RUN")
    .. [[ WHERE "RUN_STATUS" = 'RUNNING']])
  local run_id = whole_number(rows[1].RUN_ID, "the new RUN_ID")
  commit()
  return run_id
end

local function snapshot_teams(run, season, seq)
  run_sql("INSERT INTO " .. sim_table("SIM_RUN_TEAM") .. [[ ("RUN_ID", "TEAM_IDX", "TEAM_ID", "SCHOOL",
  "CONFERENCE", "TIER", "HAS_CCG", "IS_ELIGIBLE", "RATING", "RATING_SOURCE", "SOS", "WINS", "LOSSES",
  "CONF_WINS", "CONF_LOSSES", "CONF_PD", "CAPPED_PD")
SELECT ]] .. run .. [[, ROW_NUMBER() OVER (ORDER BY e."TEAM_ID") - 1, e."TEAM_ID", e."SCHOOL",
  e."CONFERENCE", e."TIER", e."HAS_CCG", e."IS_ELIGIBLE", e."RATING", e."RATING_SOURCE", e."SOS",
  e."WINS", e."LOSSES", e."CONF_WINS", e."CONF_LOSSES", e."CONF_PD", e."CAPPED_PD"
FROM ]] .. sim_table("ENGINE_TEAM_AS_OF") .. [[ e
WHERE e."AS_OF_SEASON" = ]] .. season .. [[ AND e."AS_OF_WEEK_SEQ" = ]] .. seq)
end

local function snapshot_games(run, season, seq)
  run_sql("INSERT INTO " .. sim_table("SIM_RUN_GAME") .. [[ ("RUN_ID", "GAME_ROLE", "GAME_IDX",
  "PATH_POS", "GAME_ID", "WEEK_SEQ", "START_AT", "IS_NEXT_WEEK", "HOME_TEAM_ID", "AWAY_TEAM_ID",
  "HOME_IDX", "AWAY_IDX", "IS_NEUTRAL_SITE", "CONF_GAME", "MU", "SIGMA", "TEAM_SIGMA", "HOME_WIN_PROB",
  "MARGIN", "N_HOME_WINS")
SELECT ]] .. run .. [[, g."GAME_ROLE", g."ROLE_POS" - 1,
  CASE WHEN g."GAME_ROLE" = 'REMAINING' THEN g."ROLE_POS" END, g."GAME_ID", g."WEEK_SEQ",
  g."START_AT", g."IS_NEXT_WEEK", g."HOME_TEAM_ID", g."AWAY_TEAM_ID", COALESCE(h."TEAM_IDX", -1),
  COALESCE(a."TEAM_IDX", -1), g."IS_NEUTRAL_SITE", g."CONF_GAME", g."MU", g."SIGMA", g."TEAM_SIGMA",
  g."HOME_WIN_PROB", g."MARGIN", NULL
FROM (SELECT e.*, ROW_NUMBER() OVER (PARTITION BY e."GAME_ROLE" ORDER BY e."START_AT", e."GAME_ID")
               AS "ROLE_POS"
        FROM ]] .. sim_table("ENGINE_GAME_AS_OF") .. [[ e
       WHERE e."AS_OF_SEASON" = ]] .. season .. [[ AND e."AS_OF_WEEK_SEQ" = ]] .. seq .. [[) g
LEFT JOIN ]] .. sim_table("SIM_RUN_TEAM") .. [[ h
  ON h."RUN_ID" = ]] .. run .. [[ AND h."TEAM_ID" = g."HOME_TEAM_ID"
LEFT JOIN ]] .. sim_table("SIM_RUN_TEAM") .. [[ a
  ON a."RUN_ID" = ]] .. run .. [[ AND a."TEAM_ID" = g."AWAY_TEAM_ID"]])
  local outside = run_sql('SELECT COUNT(*) AS "N", MIN("GAME_ID") AS "FIRST_GAME" FROM '
    .. sim_table("SIM_RUN_GAME") .. ' WHERE "RUN_ID" = ' .. run
    .. ' AND ("HOME_IDX" < 0 OR "AWAY_IDX" < 0)')
  local n = whole_number(outside[1].N, "games outside the roster")
  if n > 0 then
    error(string.format("SIMULATE: %d snapshot game(s) have a team outside the roster, for example "
      .. "game %s; ENGINE_TEAM_AS_OF and ENGINE_GAME_AS_OF disagree", n,
      shown(nz(outside[1].FIRST_GAME))), 0)
  end
end

local function snapshot_sizes(run)
  local rows = run_sql([[SELECT
  (SELECT COUNT(*) FROM ]] .. sim_table("SIM_RUN_TEAM") .. ' WHERE "RUN_ID" = ' .. run .. [[) AS "N_TEAMS",
  (SELECT COUNT(*) FROM ]] .. sim_table("SIM_RUN_GAME") .. ' WHERE "RUN_ID" = ' .. run
    .. [[ AND "GAME_ROLE" = 'REMAINING') AS "N_GAMES",
  (SELECT MAX("WEEK_SEQ") FROM ]] .. sim_table("SIM_RUN_GAME") .. ' WHERE "RUN_ID" = ' .. run
    .. [[ AND "GAME_ROLE" = 'REMAINING') AS "LAST_WEEK_SEQ"
FROM DUAL]])
  return {teams = whole_number(rows[1].N_TEAMS, "N_TEAMS"),
          games = whole_number(rows[1].N_GAMES, "N_GAMES"),
          last_week_seq = whole_number(rows[1].LAST_WEEK_SEQ, "LAST_WEEK_SEQ")}
end

local function left_out_games(season, seq, games)
  local rows = run_sql('SELECT COUNT(*) AS "N" FROM ' .. model_object("GAME_AS_OF") .. [[ g
WHERE g."AS_OF_SEASON" = ]] .. season .. [[ AND g."AS_OF_WEEK_SEQ" = ]] .. seq .. [[
  AND g."SEASON_TYPE" = 'regular' AND NOT g."IS_CONF_CHAMPIONSHIP" AND g."INVOLVES_FBS"
  AND g."GAME_STATUS_AS_OF" IN ('NEXT', 'REMAINING', 'NOT_PLAYED')]])
  return math.max(whole_number(rows[1].N, "open games") - games, 0)
end

local function guarantee_index(run, school)
  if school == nil then
    return -1
  end
  local rows = run_sql('SELECT MIN("TEAM_IDX") AS "TEAM_IDX" FROM ' .. sim_table("SIM_RUN_TEAM")
    .. ' WHERE "RUN_ID" = ' .. run .. ' AND "SCHOOL" = :school AND "IS_ELIGIBLE"', {school = school})
  return whole_number(rows[1].TEAM_IDX, "the guaranteed school's TEAM_IDX") or -1
end

local function key_sql(key)
  if not key:match("^[a-z][a-z0-9_]*$") then
    error("SIMULATE: not a parameter key: " .. tostring(key), 0)
  end
  return "CAST('" .. key .. "' AS VARCHAR(40) ASCII)"
end

local function snapshot_params(run, point, sizes)
  local rules = point.rules
  local horizon_beyond_first = math.max((sizes.last_week_seq or point.week_seq) - point.week_seq, 0)
  local parts = {}
  for _, item in ipairs(SIM_SCORE_KEYS) do
    parts[#parts + 1] = "SELECT " .. key_sql(item.key) .. " AS \"PARAM_KEY\", CAST(p.\"" .. item.column
      .. '" AS DECIMAL(12,4)) AS "PARAM_VALUE" FROM ' .. sim_table("SCORE_PARAMS") .. " p"
  end
  parts[#parts + 1] = "SELECT " .. key_sql("post_team_sigma") .. ", CAST(p.\"TAU\" * SQRT("
    .. string.format("%d", horizon_beyond_first) .. ") / SQRT(2) AS DECIMAL(12,4)) FROM "
    .. sim_table("SCORE_PARAMS") .. " p"
  local fixed = {
    {"auto_bid_rule", rules.auto_bid_rule},
    {"auto_bids", rules.auto_bids},
    {"g6_bid", rules.g6_bid},
    {"guarantee_rank", rules.guarantee_rank},
    {"min_conference_teams", rules.min_conference_teams},
    {"guarantee_team_idx", guarantee_index(run, rules.guarantee_school)},
  }
  for _, pair in ipairs(fixed) do
    parts[#parts + 1] = "SELECT " .. key_sql(pair[1]) .. ", CAST(" .. int_sql(pair[2], pair[1])
      .. " AS DECIMAL(12,4)) FROM DUAL"
  end
  if PLAIN.SIM_SCHEMA:sub(1, #SIM_TEST_PREFIX) == SIM_TEST_PREFIX then
    parts[#parts + 1] = "SELECT " .. key_sql("fail_batch") .. ", CAST(s.\"PARAM_VALUE\" AS DECIMAL(12,4)) FROM "
      .. sim_table("SIM_PARAMS") .. [[ s WHERE s."PARAM_KEY" = 'FAIL_BATCH']]
  end
  run_sql("INSERT INTO " .. sim_table("SIM_RUN_PARAM") .. ' ("RUN_ID", "PARAM_KEY", "PARAM_VALUE") '
    .. "SELECT " .. run .. ', x."PARAM_KEY", x."PARAM_VALUE" FROM (' .. table.concat(parts, " UNION ALL ")
    .. ") x")
end

local function take_snapshot(run, args, point, clock)
  local season, seq = int_sql(args.season, "season"), int_sql(point.week_seq, "AS_OF_WEEK_SEQ")
  snapshot_teams(run, season, seq)
  lap(clock, "teams")
  snapshot_games(run, season, seq)
  lap(clock, "games")
  local sizes = snapshot_sizes(run)
  snapshot_params(run, point, sizes)
  lap(clock, "params")
  sizes.left_out = left_out_games(season, seq, sizes.games)
  lap(clock, "left out")
  run_sql("UPDATE " .. sim_table("SIM_RUN") .. [[ SET "N_TEAMS" = :teams, "N_GAMES" = :games,
  "N_LEFT_OUT_GAMES" = :left_out WHERE "RUN_ID" = ]] .. run,
    {teams = sizes.teams, games = sizes.games, left_out = sizes.left_out})
  commit()
  lap(clock, "commit")
  return sizes
end

-- Engine ------------------------------------------------------------------------------------------

local function engine_sql(run, args)
  local columns = {}
  for i, name in ipairs(MODEL_COLUMN_NAMES) do
    columns[i] = 'm."' .. name .. '"'
  end
  return "INSERT INTO " .. sim_table("SIM_STAGE") .. " SELECT " .. QUOTED.INFRA_SCHEMA .. '."'
    .. SIM_ENGINE_SCRIPT .. '"(' .. run .. ", " .. int_sql(args.run_seed, "run_seed") .. ", "
    .. int_sql(args.sims_per_batch, "sims_per_batch") .. ', b."BATCH_ID", '
    .. table.concat(columns, ", ") .. ") FROM " .. sim_table("SIM_RUN_MODEL_ROWS")
    .. " m CROSS JOIN (VALUES BETWEEN 0 AND " .. int_sql(args.n_batches - 1, "the last batch id")
    .. ') AS b("BATCH_ID") WHERE m."RUN_ID" = ' .. run .. ' GROUP BY b."BATCH_ID"'
end

local function parse_info(text)
  local info = {}
  for key, value in tostring(text or ""):gmatch("([^;=]+)=([^;]*)") do
    info[key] = value
  end
  return info
end

local function count_keys(set)
  local n = 0
  for _ in pairs(set) do
    n = n + 1
  end
  return n
end

local function joined_keys(set)
  local keys = {}
  for key in pairs(set) do
    keys[#keys + 1] = key
  end
  table.sort(keys)
  return table.concat(keys, ",")
end

local function batch_report(run, args)
  local rows = run_sql('SELECT "BATCH_ID", "INFO" FROM ' .. sim_table("SIM_STAGE") .. ' WHERE "RUN_ID" = '
    .. run .. [[ AND "ROW_KIND" = 'B' ORDER BY "BATCH_ID"]])
  local seen, vms, nodes, digests, numpys, pythons = {}, {}, {}, {}, {}, {}
  local ms_min, ms_max, ms_sum = nil, 0, 0
  for i = 1, #rows do
    local batch = whole_number(rows[i].BATCH_ID, "BATCH_ID")
    if seen[batch] or batch < 0 or batch >= args.n_batches then
      error(string.format("SIMULATE: batch %d reported twice or is outside 0 to %d", batch,
        args.n_batches - 1), 0)
    end
    seen[batch] = true
    local info = parse_info(nz(rows[i].INFO))
    vms[tostring(info.vm)] = true
    nodes[tostring(info.node)] = true
    digests[tostring(info.digest)] = true
    numpys[tostring(info.numpy)] = true
    pythons[tostring(info.python)] = true
    local ms = tonumber(info.ms) or 0
    ms_min = (ms_min == nil or ms < ms_min) and ms or ms_min
    ms_max = math.max(ms_max, ms)
    ms_sum = ms_sum + ms
  end
  if #rows ~= args.n_batches then
    error(string.format("SIMULATE: %d of %d batches reported", #rows, args.n_batches), 0)
  end
  if count_keys(digests) ~= 1 then
    error(string.format("SIMULATE: the batches report %d model digests (%s); every batch must read "
      .. "the same snapshot", count_keys(digests), clip(joined_keys(digests), 300)), 0)
  end
  return {instances = count_keys(vms), nodes = count_keys(nodes), digest = joined_keys(digests),
          numpy = joined_keys(numpys), python = joined_keys(pythons), ms_min = ms_min or 0,
          ms_max = ms_max, ms_mean = ms_sum / math.max(#rows, 1)}
end

local function engine_version()
  local rows = run_sql([[SELECT "SCRIPT_COMMENT" FROM "SYS"."EXA_ALL_SCRIPTS"
WHERE "SCRIPT_SCHEMA" = :schema AND "SCRIPT_NAME" = :name]],
    {schema = PLAIN.INFRA_SCHEMA, name = SIM_ENGINE_SCRIPT})
  if #rows == 0 then
    return nil
  end
  return tostring(nz(rows[1].SCRIPT_COMMENT) or ""):match("ENGINE_VERSION (%x+)")
end

-- Outputs -----------------------------------------------------------------------------------------

local function inserted(result)
  return whole_number(nz(result.rows_inserted) or nz(result.rows_affected) or 0, "rows inserted")
end

local function stage_filter(run, kind)
  return " FROM " .. sim_table("SIM_STAGE") .. ' WHERE "RUN_ID" = ' .. run .. ' AND "ROW_KIND" = \''
    .. kind .. "'"
end

local function require_rows(what, got, expected)
  if got ~= expected then
    error(string.format("SIMULATE: %s holds %d rows, expected %d", what, got, expected), 0)
  end
end

local function split_outputs(run, args, clock)
  local playoff = inserted(run_sql("INSERT INTO " .. sim_table("SIM_TEAM")
    .. ' SELECT "RUN_ID", "SIM_ID", "TEAM_ID", "SEED", "BYE", "REACHED_ROUND", "CHAMPION"'
    .. stage_filter(run, "T")))
  require_rows("SIM_TEAM", playoff, args.n_sims * SIM_PLAYOFF_TEAMS)
  lap(clock, "playoff rows")
  local paths = inserted(run_sql("INSERT INTO " .. sim_table("SIM_PATH")
    .. ' SELECT "RUN_ID", "SIM_ID", "OUTCOMES"' .. stage_filter(run, "P")))
  require_rows("SIM_PATH", paths, args.n_sims)
  lap(clock, "outcome strings")
  run_sql("INSERT INTO " .. sim_table("TEAM_WINS") .. ' SELECT "RUN_ID", "TEAM_ID", "WINS", SUM("N")'
    .. stage_filter(run, "W") .. ' GROUP BY "RUN_ID", "TEAM_ID", "WINS"')
  run_sql("INSERT INTO " .. sim_table("CONF_CHAMP") .. ' SELECT "RUN_ID", "TEAM_ID", SUM("N")'
    .. stage_filter(run, "C") .. ' GROUP BY "RUN_ID", "TEAM_ID"')
  run_sql("MERGE INTO " .. sim_table("SIM_RUN_GAME") .. ' g USING (SELECT "GAME_IDX", SUM("N") AS "N"'
    .. stage_filter(run, "G") .. [[ GROUP BY "GAME_IDX") s
ON (g."RUN_ID" = ]] .. run .. [[ AND g."GAME_ROLE" = 'REMAINING' AND g."GAME_IDX" = s."GAME_IDX")
WHEN MATCHED THEN UPDATE SET "N_HOME_WINS" = s."N"]])
  lap(clock, "tallies")
  run_sql("INSERT INTO " .. sim_table("LEVERAGE") .. [[ ("RUN_ID", "TEAM_ID", "GAME_IDX", "N_HOME",
  "N_HOME_IN_CFP", "N_AWAY", "N_AWAY_IN_CFP")
SELECT ]] .. run .. [[, t."TEAM_ID", g."GAME_IDX", g."N_HOME_WINS", COALESCE(l."N", 0),
  ]] .. int_sql(args.n_sims, "n_sims") .. [[ - g."N_HOME_WINS", t."N_IN_CFP" - COALESCE(l."N", 0)
FROM (SELECT "TEAM_ID", COUNT(*) AS "N_IN_CFP"]] .. stage_filter(run, "T") .. [[ GROUP BY "TEAM_ID") t
CROSS JOIN (SELECT "GAME_IDX", "N_HOME_WINS" FROM ]] .. sim_table("SIM_RUN_GAME") .. [[
             WHERE "RUN_ID" = ]] .. run .. [[ AND "GAME_ROLE" = 'REMAINING') g
LEFT JOIN (SELECT "TEAM_ID", "GAME_IDX", SUM("N") AS "N"]] .. stage_filter(run, "L") .. [[
            GROUP BY "TEAM_ID", "GAME_IDX") l
  ON l."TEAM_ID" = t."TEAM_ID" AND l."GAME_IDX" = g."GAME_IDX"]])
  local unset = run_sql('SELECT COUNT(*) AS "N" FROM ' .. sim_table("SIM_RUN_GAME") .. ' WHERE "RUN_ID" = '
    .. run .. [[ AND "GAME_ROLE" = 'REMAINING' AND "N_HOME_WINS" IS NULL]])
  require_rows("the remaining games without N_HOME_WINS", whole_number(unset[1].N, "N"), 0)
  lap(clock, "leverage")
  run_sql("TRUNCATE TABLE " .. sim_table("SIM_STAGE"))
  lap(clock, "stage")
end

-- Finish and failure ------------------------------------------------------------------------------

local function finish(run, args, sizes, report, version, clock, marks)
  local total = lap(clock, "finish")
  local summary = clip(string.format(
    "%d seasons, %d batches of %d, %d teams, %d games (%d left out); inputs %.2f s (%s); engine "
      .. "%.2f s, batch ms min/mean/max %d/%.0f/%d on %d instances, %d node(s); outputs %.2f s (%s); "
      .. "total %.2f s",
    args.n_sims, args.n_batches, args.sims_per_batch, sizes.teams, sizes.games, sizes.left_out,
    marks.inputs, laps_text(clock, marks.inputs_first, marks.inputs_last), marks.engine,
    report.ms_min, report.ms_mean, report.ms_max, report.instances, report.nodes,
    total - marks.outputs_from, laps_text(clock, marks.outputs_first, #clock.laps), total),
    SIM_DETAIL_LENGTH)
  run_sql("UPDATE " .. sim_table("SIM_RUN") .. [[ SET "RUN_STATUS" = 'DONE', "MODEL_DIGEST" = :digest,
  "ENGINE_VERSION" = :version, "NUMPY_VERSION" = :numpy, "PYTHON_VERSION" = :python,
  "INSTANCES" = :instances, "NODES" = :nodes, "ENDED_AT" = ]] .. SIM_UTC_NOW .. [[,
  "SECONDS_INPUTS" = :inputs, "SECONDS_ENGINE" = :engine, "SECONDS_OUTPUTS" = :outputs,
  "SECONDS_TOTAL" = :total, "SIMS_PER_SECOND" = :rate, "DETAIL" = :detail
WHERE "RUN_ID" = ]] .. run,
    {digest = clip(report.digest, 64), version = sql_value(version), numpy = clip(report.numpy, 20),
     python = clip(report.python, 20), instances = report.instances, nodes = report.nodes,
     inputs = marks.inputs, engine = marks.engine, outputs = total - marks.outputs_from,
     total = total, rate = args.n_sims / math.max(total, 0.001), detail = summary})
  commit()
  return total, summary
end

local function mark_failed(run, clock, message)
  for _, name in ipairs(SIM_OUTPUT_TABLES) do
    run_sql("DELETE FROM " .. sim_table(name) .. ' WHERE "RUN_ID" = ' .. run)
  end
  run_sql("TRUNCATE TABLE " .. sim_table("SIM_STAGE"))
  run_sql("UPDATE " .. sim_table("SIM_RUN") .. [[ SET "RUN_STATUS" = 'FAILED', "ENDED_AT" = ]]
    .. SIM_UTC_NOW .. [[, "SECONDS_TOTAL" = :total, "DETAIL" = :detail WHERE "RUN_ID" = ]] .. run,
    {total = seconds_since(clock.started), detail = clip(message, SIM_DETAIL_LENGTH)})
  commit()
end

local function failed(run_id, clock, step, err)
  local message = tostring(err)
  rollback()
  if is_killed(message) then
    error(message, 0)
  end
  local logged, why = pcall(mark_failed, int_sql(run_id, "run id"), clock, message)
  if not logged then
    rollback()
    message = message .. " (the simulation run could not be marked FAILED: " .. tostring(why) .. ")"
  end
  error(clip(string.format("SIMULATE run %d failed at %s: %s", run_id, step, message),
    SIM_DETAIL_LENGTH), 0)
end

-- The run -----------------------------------------------------------------------------------------

function simulate_run(args, point, origin, load_run_id)
  local clock = new_clock()
  local run_id = register(args, point, origin, load_run_id, clock)
  local run = int_sql(run_id, "run id")
  lap(clock, "register")
  local step = "the input snapshot"
  local ok, result = pcall(function()
    local marks = {inputs_first = #clock.laps + 1}
    local sizes = take_snapshot(run, args, point, clock)
    marks.inputs_last = #clock.laps
    marks.inputs = clock.last - clock.laps[1].seconds
    step = "the engine"
    local before_engine = clock.last
    run_sql(engine_sql(run, args))
    lap(clock, "engine")
    marks.engine = clock.last - before_engine
    marks.outputs_from = clock.last
    marks.outputs_first = #clock.laps + 1
    step = "the outputs"
    local report = batch_report(run, args)
    local version = engine_version()
    lap(clock, "batch rows")
    split_outputs(run, args, clock)
    step = "the finish"
    local total, summary = finish(run, args, sizes, report, version, clock, marks)
    return {sizes = sizes, total = total, summary = summary}
  end)
  if not ok then
    failed(run_id, clock, step, result)
  end
  local row = {run_id, "DONE", args.n_sims, args.n_batches, result.sizes.teams, result.sizes.games,
               result.total, args.n_sims / math.max(result.total, 0.001), result.summary}
  return {run_id = run_id, row = row, summary = string.format("SIMULATE run %d DONE: %s", run_id,
    result.summary)}
end
