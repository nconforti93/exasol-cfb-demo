-- loader/lua/run_lock.lua: the entry of <INFRA_SCHEMA>.RUN_LOCK(lock_action, lock_word, lock_run,
-- lock_text), the run lock of host processes (loader/runlock.py). Bundled like every loader script:
-- the namespace constants, loadlib.lua, then this file.
--
--   ('acquire', <kind>, NULL, <args>)       -> one row RUN_ID: acquire_host_lock(kind, args); a
--                                              script kind (BACKFILL, ...) raises at once
--   ('release', <status>, <run id>, <text>) -> one row HELD: release_lock(run id, status, text),
--                                              1 when the row was still RUNNING in this
--                                              session, else 0
--
-- The lock row belongs to the session that calls this script. A host process calls it in the
-- session it works in, keeps that session open until it releases, and runs no other locking script
-- in it (the own-session rule would abandon its row). acquire_lock commits the session's open
-- transaction, so a host takes the lock before its own transaction starts.

local action = nz(lock_action)

if action == "acquire" then
  exit({{acquire_host_lock(nz(lock_word), nz(lock_text))}}, "RUN_ID DECIMAL(18,0)")
end

if action == "release" then
  local run_id = whole_number(lock_run, "RUN_LOCK release: the run id")
  if run_id == nil then
    error("RUN_LOCK release needs a run id", 0)
  end
  local held = release_lock(run_id, nz(lock_word), nz(lock_text))
  exit({{held and 1 or 0}}, "HELD DECIMAL(1,0)")
end

error("RUN_LOCK: unknown action " .. tostring(action) .. "; use acquire or release", 0)
