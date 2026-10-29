-- engine/lua/simulate.lua: EXECUTE SCRIPT <infra>.SIMULATE(season, week, season_type, n_sims,
-- sims_per_batch, run_seed, run_label) (sim-engine/sim-runs). Bundled by engine/deploy.py: the
-- namespace constants, the generated prelude, loader/lua/loadlib.lua, engine/lua/simlib.lua, then
-- this file, as one Lua chunk. The script parameters are globals.
--
-- 1 Arguments (no statement): batch sizes 100-20,000, n_sims a multiple, at most 10,000 batches.
-- 2 Rules, point and constants (reads only): a SEASON_RULES row for the season, the point in
--   AS_OF_POINT, a cutoff before SELECTION_AT, the score model's constants. A refusal writes nothing.
-- 3 The loader's run lock as kind SIMULATE (script rule): refused while a loader run, a model
--   refresh, a snapshot, a restore, a deploy or another simulation holds it.
-- 4 simulate_run (simlib.lua): register, snapshot, engine, outputs, DONE; a failure marks the run
--   FAILED without outputs, then the lock row FAILED, and raises.
-- 5 The lock is released DONE.
-- Result: one row RUN_ID, RUN_STATUS, N_SIMS, N_BATCHES, N_TEAMS, N_GAMES, SECONDS_TOTAL,
-- SIMS_PER_SECOND, DETAIL. Never run it WITH OUTPUT (that discards the row). Zero CFBD calls:
-- nothing here reads the virtual schema.

local LOCK_KIND = "SIMULATE"
local ORIGIN = "MANUAL"

local given = {season = nz(season), week = nz(week), season_type = nz(season_type),
               n_sims = nz(n_sims), sims_per_batch = nz(sims_per_batch), run_seed = nz(run_seed),
               run_label = nz(run_label)}
local args = sim_arguments(given)
local point = sim_point(args)
local job = new_job(LOCK_KIND, sim_arguments_text(args))
local cleaned, cleanup_result = pcall(cleanup_abandoned_runs)
if not cleaned then
  fail(job, cleanup_result)
end
local ok, result = pcall(simulate_run, args, point, ORIGIN, nil)
if not ok then
  fail(job, result)
end
end_job(job, "DONE", result.summary)
exit({result.row}, SIM_RESULT_COLUMNS)
