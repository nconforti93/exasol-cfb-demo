-- Registered REFRESH_WEEK step 300. The refresh owns the shared run lock.
local refresh_id = whole_number(nz(run_id), "refresh run_id")
local season_no = whole_number(nz(season), "season")
local week_no = whole_number(nz(week), "week")
local kind = nz(season_type) or "regular"
if refresh_id == nil or season_no == nil or week_no == nil
  or (kind ~= "regular" and kind ~= "postseason") then
  error("REFRESH_SIMULATE needs run_id, season, week and season_type", 0)
end
require_refresh_run(refresh_id)

local function run_refresh()
  cleanup_abandoned_runs()
  local rows = run_sql([[SELECT p."AS_OF_WEEK_SEQ",
  CASE WHEN p."CUTOFF_AT" < r."SELECTION_AT" THEN 1 ELSE 0 END AS "BEFORE_SELECTION"
FROM ]] .. QUOTED.MODEL_SCHEMA .. [[."AS_OF_POINT" p
LEFT JOIN ]] .. QUOTED.SIM_SCHEMA .. [[."SEASON_RULES" r ON r."SEASON" = p."AS_OF_SEASON"
WHERE p."AS_OF_SEASON" = :season AND p."AS_OF_WEEK" = :week
  AND p."AS_OF_SEASON_TYPE" = :season_type]],
    {season = season_no, week = week_no, season_type = kind})
  if #rows ~= 1 then
    error(string.format("REFRESH_SIMULATE: season %d %s week %d has no as-of point",
      season_no, kind, week_no), 0)
  end
  if whole_number(rows[1].BEFORE_SELECTION, "before selection") ~= 1 then
    return nil
  end
  local params = run_sql([[SELECT "PARAM_KEY", "PARAM_VALUE" FROM ]] .. QUOTED.SIM_SCHEMA
    .. [[."SIM_PARAMS" WHERE "PARAM_KEY" IN ('REFRESH_N_SIMS', 'REFRESH_SIMS_PER_BATCH')]])
  local values = {}
  for _, row in ipairs(params) do
    values[nz(row.PARAM_KEY)] = whole_number(row.PARAM_VALUE, tostring(row.PARAM_KEY))
  end
  if values.REFRESH_N_SIMS == nil or values.REFRESH_SIMS_PER_BATCH == nil then
    error("REFRESH_SIMULATE needs REFRESH_N_SIMS and REFRESH_SIMS_PER_BATCH in SIM_PARAMS; run make sim", 0)
  end
  local args = sim_arguments({season = season_no, week = week_no, season_type = kind,
    n_sims = values.REFRESH_N_SIMS, sims_per_batch = values.REFRESH_SIMS_PER_BATCH,
    run_label = "refresh " .. refresh_id})
  local point = sim_point(args)
  return simulate_run(args, point, "REFRESH", refresh_id)
end

local ok, result = pcall(run_refresh)
if not ok then
  rollback()
  error(result, 0)
end
if result == nil then
  exit({{null, "skipped: no simulation for this point"}},
    "SIM_RUN_ID DECIMAL(18,0), DETAIL VARCHAR(2000) UTF8")
end
exit({{result.run_id, clip(result.summary, 2000)}},
  "SIM_RUN_ID DECIMAL(18,0), DETAIL VARCHAR(2000) UTF8")
