-- Registered REFRESH_WEEK step 200. The refresh owns the shared run lock.
local refresh_id = whole_number(nz(run_id), "refresh run_id")
local season_no = whole_number(nz(season), "season")
local week_no = whole_number(nz(week), "week")
local kind = nz(season_type) or "regular"
if refresh_id == nil or season_no == nil or week_no == nil
  or (kind ~= "regular" and kind ~= "postseason") then
  error("REFRESH_PREDICT needs run_id, season, week and season_type", 0)
end
require_refresh_run(refresh_id)
local rows = run_sql([[SELECT "AS_OF_WEEK_SEQ" FROM ]] .. QUOTED.MODEL_SCHEMA
  .. [[."AS_OF_POINT" WHERE "AS_OF_SEASON" = :season AND "AS_OF_WEEK" = :week
  AND "AS_OF_SEASON_TYPE" = :season_type]],
  {season = season_no, week = week_no, season_type = kind})
if #rows ~= 1 then
  error(string.format("REFRESH_PREDICT: season %d %s week %d has no as-of point",
    season_no, kind, week_no), 0)
end
local seq = whole_number(rows[1].AS_OF_WEEK_SEQ, "as-of week sequence")
local ok, count = pcall(freeze_predictions, season_no, seq, "REFRESH " .. refresh_id)
if not ok then
  rollback()
  error(count, 0)
end
exit({{count}}, "FROZEN_GAMES DECIMAL(18,0)")
