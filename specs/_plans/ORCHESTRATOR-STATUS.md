# Orchestrator status (autonomous run, started 2026-10-22)

Mandate: finish the mission end to end through the speq loop (plan → review → implement → verify → record).

Limits:
- At most 400 CFBD calls in October 2026. November is not authorized.
- AWS `default`: deploy and run only. Never stop, start or resize it.
- Commit locally only, never push.
- Make decisions myself and log them.
- Check Exasol syntax and reserved words with the `exasol:*` skills.

## RESUME AFTER RESTART (planned machine restart ~14:33 CEST, 2026-10-22)

State at stop: all work is committed locally on branch `add-cfbd-vs-foundation` (7da9901). Integration tests 7.1–7.3 are `[x]`. 7.4 is `[~]` only because of the blocked network test. No DB sessions or locks are open.

1. Bring local Exasol back with `exasol start -d local` (via the launcher, never exakit). Check it with a trivial PYTHON3 UDF.
2. The mock sidecar container is gone after the reboot. The IT harness restarts it on its own (`tests/mock_cfbd/sidecar.py`).
3. The IT lock file is `/tmp/claude-1002/-mnt-c-Users-nc-OneDrive---EXASOL-AG-Documents-Notes/d157cdb7-6061-4526-8ef3-8d5d6fabb341/scratchpad/cfb-it.lock`. /tmp may be wiped by the reboot; if so, recreate it by running any `flock` on that path.
4. Read `specs/_plans/add-cfbd-vs-foundation/tasks.md`. Resume every `[~]` task with a fresh implementer agent. Point it at its test files and the state report quoted below under "Agent state at stop".
5. **AWS `default` is FREE** (exasol-cfb-demo-a4 confirmed at 14:06; add-engine-spikes is finished with both DBs). The AWS DB has no schemas; the `CFBD_API` connection should still exist, and 9.2 verifies it via `EXA_DBA_CONNECTIONS`. Never stop the instance (mandate). It also holds the shared files `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` until it sends "4.1 done". Check with ListAgents and SendMessage after the restart, because that session may also have restarted.
6. Then continue with the queue under "Next steps".

## Plan pipeline

| Plan | Roadmap phase | State |
|---|---|---|
| add-cfbd-vs-foundation | Phase 0 S1/S2 + Phase 1 | Groups A–G done. Review R.1 done. Fixes F.1–F.3 done, F.4 open. Group H integration tests running on local (see tasks.md). Docs 8.2–8.4 wait for "4.1 done". Live group J waits for "AWS free" |
| add-engine-spikes | Phase 0 S3/S4 | Owned by the parallel session |
| add-cfb-persistence-refresh | Phase 2 | Reviewed (approve with changes), revised, validates. Implementation waits for the foundation |
| add-cfb-analytical-model | Phase 3 | Plan complete and validated: 27 tasks, 10 specs, 18 views plus 5 seeded reference tables. **Next: independent review**, then revise. 12 open questions (Q1–Q12) are answered by zero-call checks in its task 7.2 |
| Phases 4–8 | | Not started. Phase 4 builds on the add-engine-spikes S3/S4 results |

## Inputs for Phase 4 (from add-engine-spikes, 2026-10-22)

- **Write-ups.** `docs/spikes/s3-python-engine-throughput.md` (D19) and `docs/spikes/s4-sim-storage.md` (D20). Raw results are in `spikes/s3/results/*.csv` and `spikes/s4/results/*.csv`; `*_aws` files are the AWS runs.
- **S3 engine.** The Python engine is a GO.
  - **Compute only, 100k sims:** about 7 s local (8 cores) and about 1.5 s on AWS (3×4 cores).
  - **Full mode, 100k sims:** about 55 s local and 12.4 s on AWS. About 75 % of that time is emitting SIM_TEAM rows.
  - **Settings to use:** sims_per_batch between 1k and 10k, at least 2 batches per core slot, one DataFrame per batch, and explicit EMITS.
- **S4 storage.** Layout C wins: one BOOLEAN column per game for outcomes.
  - **SIM_TEAM:** keep it sparse, with in-CFP rows only (run_id, sim_id, team, seed, bye, reached_round, champion). Add a per-run LEVERAGE table and a TEAMS roster. Don't store per-sim margins.
  - **What-if p95 at 1M on AWS:** 0.52 s at k=1 and 0.21 s at k=3.
  - **Conditional rooting guide at k=3:** about 2.2 s, which misses the target. This is deferred to Phase 5.
  - **Sparse SIM_TEAM effect:** it cuts local full mode to about 20 s (estimated, not re-measured).

## Facts learned on AWS (2026-10-22)

- **AWS `default`:** Exasol **2026.1.1**, 3 nodes, PYTHON3 3.12.3. UDF DNS works (system resolver).
- **CFBD key:** Academic tier, 3000/month, sharedPool true. remainingCalls was 3000 at 15:26Z and resets on 2026-11-01. All feature flags are false, so `API_FEATURES` is empty. The owner cap of 400 for October still applies.
- **Deployment:** `CFBD_VS` is deployed on AWS (137 tables) together with `CFB_READER`. Read-through and aggregates work on 2026.1.1, with no UNION ALL crash.
- **AWS BucketFS:** there is no host directory or exapump profile for it, so replay recordings live in `cache/replay/` and on local BucketFS only.

## Next steps (queue)

1. Finish the foundation integration tests 7.1–7.4 on local, then F.4 (redact the key after JSON parsing too).
2. Foundation V.1–V.3 (checklist, scenario coverage, verification report), apart from the live rows.
3. On "AWS free": run foundation group J on AWS with `TARGET=aws`, at most 50 calls (9.0 dns-check gate, then 9.1–9.6). Then docs 8.2–8.4 after "4.1 done". Then `/speq:record add-cfbd-vs-foundation` and commit.
4. Implement add-cfb-persistence-refresh. Its live backfill costs about 217 calls and must finish by 2026-10-31.
5. Independent review of the Phase 3 plan, then implement it. Then plan, review and implement Phases 4–8.

## Decisions taken for Nico (please review)

- **Demo beat 3 is `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, season_type)`.** Exasol has no CALL.
- **Demo profile is about 217 calls once, then about 4 per weekly refresh.** Only `RANKINGS_POLLS_RANKS` is loaded, and it carries its own keys.
- **`PROMOTE()` is deferred until after Phase 6.**
- **Phase 3 venues use `GEOMETRY(4326)` points.** Exasol has no GEOGRAPHY type, so the mission wording will change. All model objects are views, and their comments are written inline because Exasol has no COMMENT ON VIEW.
- **The loader never toggles `READ_THROUGH`.** It filters `REQ_SOURCE <> 'STORED'` instead.
- **sys owns the real deployment (foundation [20]).** Readers get the `CFB_READER` role.
- **Fan-out is one SET group plus a thread pool, `MAX_PARALLEL_FETCHES` (foundation [18]).**
- **The 401/403 message follows the gating spec (foundation [22]).** Body redaction is key-only ([23]). Recordings store attempts ([24]). The catalog hash covers generated content ([25]).

## Blocked on Nico

- **November CFBD budget.** Needed only if the Phase 2 backfill can't finish by 2026-10-31.
- **The DoH-success network test was never run (foundation 7.4 stays `[~]`).** Auto mode blocked `flock … make test-network` as "Interfere With Workloads". You can run it yourself with `! make test-network`; it needs HTTPS egress to 1.1.1.1 plus a TLS handshake with the CFBD host, and makes no API call. The AWS dns-check (9.0) resolved via the **system** resolver, so the DoH fallback is still unverified.
- **Two V.3 manual steps were blocked by auto mode.** Staging a stored slice on local was blocked, so the read-through manual row wasn't run; integration tests and the live 9.5b result cover it. `ALTER VIRTUAL SCHEMA` on AWS was blocked as "Modify Shared Resources", so the properties row ran on local instead.
- **Local Personal has the real namespace deployed** (`CFB_INFRA`, `CFB_META`, `CFB_CORE`, `CFBD_VS`). It is in `FETCH_MODE='replay'` from V.3. `make grants` was not run there.
- **Stale podman container `exasol-db-49afcb12` (Created).** Auto mode denied `podman rm`, so it is still there.
- **Phase 7 chat has no Anthropic API key.** It will be built against a stub.
