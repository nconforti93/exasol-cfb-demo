-- Freeze the current season's next-week predictions through the requested as-of point.
-- Bundled after loadlib.lua and simlib.lua. This manual command holds the shared run lock.
local season_no = whole_number(nz(season), "season")
local week_no = whole_number(nz(week), "week")
local kind = nz(season_type) or "regular"
if season_no == nil or week_no == nil or (kind ~= "regular" and kind ~= "postseason") then
  error("PREDICT needs a season, week and season_type regular or postseason", 0)
end
local points = run_sql([[SELECT "AS_OF_WEEK_SEQ" FROM ]] .. QUOTED.MODEL_SCHEMA
  .. [[."AS_OF_POINT" WHERE "AS_OF_SEASON" = :season AND "AS_OF_WEEK" = :week
  AND "AS_OF_SEASON_TYPE" = :season_type]],
  {season = season_no, week = week_no, season_type = kind})
if #points ~= 1 then
  error(string.format("PREDICT refused: season %d, %s week %d is not an as-of point",
    season_no, kind, week_no), 0)
end
local seq = whole_number(points[1].AS_OF_WEEK_SEQ, "as-of week sequence")
local job = new_job("SIMULATE", string.format("PREDICT %d, %d, %s", season_no, week_no, kind))
local ok, count = pcall(freeze_predictions, season_no, seq, "PREDICT")
if not ok then
  fail(job, count)
end
end_job(job, "DONE", string.format("PREDICT froze %d games through %d %s week %d",
  count, season_no, kind, week_no))
exit({{count}}, "FROZEN_GAMES DECIMAL(18,0)")
