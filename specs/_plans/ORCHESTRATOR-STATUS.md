# Orchestrator status (autonomous run, started 2026-10-22)

> **Superseded as the live status by `HANDOVER.md` (2026-10-23).** Kept as history.

Mandate: finish the mission end to end through the speq loop (plan → review → implement → verify → record).

Limits:
- At most 400 CFBD calls in October 2026. November is not authorized.
- AWS `default`: deploy and run only. Never stop, start or resize it.
- Commit locally only, never push.
- Make decisions myself and log them.
- Check Exasol syntax and reserved words with the `exasol:*` skills.

## PROCESS CHANGE (owner, 2026-10-23): lighter process

Nico: "Let's do a lighter process moving forward, this is a demo, not a full-blown product offering. We also don't need a crazy amount of tests really. Ideally I could test the web app today."
- From now on, run thin end-to-end slices first, then harden. Each remaining phase gets one plan or revision pass at most, with no separate plan review. Code review only for risky code (spend, locks, SQL guard). No coverage audits.
- Tests: a few per feature that cover the demo path. Don't chase scenario-by-scenario coverage.
- Today's priority: a clickable web app. Step 1 is `make app-preview` on fixture data (running now). Step 2 is a real-data path: merge Phase 2 → live backfill on AWS → Phase 3 views → a minimal Phase 4 engine → only the Phase 5 views the pages need → Phase 6 routes.

## STOP POINT (usage limit, 2026-10-23)

- **Phase 3 (p3):** group E is committed (f21b00c). 6817 unit/CLI tests pass and lint is clean. Task 3.8 TEAM_SEASON is `[~]` and an expert agent was working on it when the session stopped. Its output is uncommitted and may be incomplete.
- **Phase 2 (p2):** DONE. Verification and live group J are done (a93501a). AWS stores 155k rows over 9 tables (GAMES 1869–2026, SP+/WP/polls 2013/14–2026), with week 7 of 2026 refreshed. **The Oct 31 restore gate PASSED** (snapshot `~/.cfb-demo/snapshots/aws-demo-2026-10-23` restored onto local in about 20 s; local now holds the same data). Ledger: plan 221/300; October /info 240 of 3000 used, ledger 242/400. Recording is running, then fast-forward main.
- **Phase 6 UI revision (owner, 2026-10-23):** a US map home with team logos and a conference filter, then a team page, then league tabs (predictions, matchups, stats). The planner is revising the Phase 6 plan. Logos are cached locally and never committed. The fixture preview `make app-preview` is committed (64f983a); the owner found it not useful without real data.
- **Phase 7 (p7):** groups A and B are done (2.1 committed 89f21ae; spec text aligned with [28]; FU-2 coverage list). **Group C onward is blocked** until Phases 5 and 6 merge to main (plan sequencing; gate 3.0 checks P7-a..e in Phase 6).
- **Resume:**
  1. Run `git status` and `git diff --stat` in p2, p3 and p7.
  2. Re-run `make test` and `make lint` under the IT lock.
  3. Commit each task if it's green. Otherwise start a fresh agent pointed at the files already on disk.
  4. Then go on with Phase 3 groups G–K, Phase 2 V.1–V.3 and live group J (by Oct 31), and Phase 7 group C.

## RESUME AFTER RESTART (planned machine restart ~14:33 CEST, 2026-10-22)

State at stop: all work is committed locally on branch `add-cfbd-vs-foundation` (7da9901). Integration tests 7.1–7.3 are `[x]`. 7.4 is `[~]` only because of the blocked network test. No DB sessions or locks are open.

1. Bring local Exasol back with `exasol start -d local` (via the launcher, never exakit). Check it with a trivial PYTHON3 UDF.
2. The mock sidecar container is gone after the reboot. The IT harness restarts it on its own (`tests/mock_cfbd/sidecar.py`).
3. The IT lock file is `/tmp/claude-1002/-mnt-c-Users-nc-OneDrive---EXASOL-AG-Documents-Notes/d157cdb7-6061-4526-8ef3-8d5d6fabb341/scratchpad/cfb-it.lock`. /tmp may be wiped by the reboot; if so, recreate it by running any `flock` on that path.
4. Read `specs/_plans/add-cfbd-vs-foundation/tasks.md`. Resume every `[~]` task with a fresh implementer agent. Point it at its test files and the state report quoted below under "Agent state at stop".
5. **AWS `default` is FREE** (exasol-cfb-demo-a4 confirmed at 14:06; add-engine-spikes is finished with both DBs). The AWS DB has no schemas; the `CFBD_API` connection should still exist, and 9.2 verifies it via `EXA_DBA_CONNECTIONS`. Never stop the instance (mandate). It also holds the shared files `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` until it sends "4.1 done". Check with ListAgents and SendMessage after the restart, because that session may also have restarted.
6. Then continue with the queue under "Next steps".

## Plan pipeline (updated 2026-10-23 morning)

| Plan | Phase | Where | State |
|---|---|---|---|
| add-cfbd-vs-foundation | 1 | main | **Recorded**. Live on AWS. Ledger 21 calls |
| add-cfb-persistence-refresh | 2 | worktree p2 | All groups A–I and code review done. **Review fixes F.1 running**. Then verification and the live group J on AWS (217 calls; Oct 31 restore gate) |
| add-cfb-analytical-model | 3 | worktree p3 | Groups A–B done. **Group C running** (3.1 GAME+X1, 2.5, 3.2) |
| add-cfb-score-engine | 4 | main (plan) | Reviewed and revised. Starts after Phase 3 group C/D |
| add-cfb-analyses | 5 | main (plan) | Reviewed and revised |
| add-cfb-app | 6 | worktree p6 | Group A done (backend core + whole frontend, 582 FE tests). **2.1 db/operator running**. Rest gated on Phases 2–5 merging |
| add-cfb-chat | 7 | worktree p7 (from p6) | Reviewed and revised. **Group A running** |
| add-cfb-demo-hardening | 8 | main (plan) | Reviewed. **Revision running** |

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

## STOP POINT (usage limit, 2026-10-22 evening)

- **Phase 2 (worktree p2):** groups A–E are done and committed. Task 4.2 (Lua `loadlib.lua`/`backfill.lua`, plus H1 lock kinds) was STOPPED mid-work. Its files are on disk in p2, uncommitted. On resume: inspect `git status` in p2, then start a fresh 4.2 expert agent pointed at the existing files and the 4.2 brief, and run the integration tests under the lock. After that: 4.3, then 5.2, then group H, then group I docs, then live group J (217 calls, must finish by 2026-10-31).
- **Phase 3 (worktree p3):** group A is done. Group B waits for Phase 2 tasks 4.2/4.3.
- **Phase 4 (main):** the plan is reviewed and revised. Implementation follows Phase 3.
- **Phase 5 (main):** plan `add-cfb-analyses` is written (69 scenarios, 31 tasks) and validates. **Next: independent review.** U3/Q13 needs Nico: head-to-head drops `/teams/matchup` and uses stored games plus a 1-call game preview. Q2 is a security probe; escalate to Nico if it fails.
- **Phases 6–8:** not planned yet.

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

## Owner answers (2026-10-22 evening)

- **November CFBD budget:** 1000 calls, recorded in `vs/live/ledger.yaml` on the Phase 2 branch. October stays at 400.
- **Head-to-head without `/teams/matchup`:** confirmed (Phase 5 U3).
- **Network DoH test:** passed (run by Nico). Foundation task 7.4 is closed. Stale podman container `exasol-db-49afcb12`: removed by Nico.
- **Anthropic key:** Nico will provide it later.
- **Demo date and replay week:** the demo is around mid-November 2026. The replay/rehearsal snapshot uses the latest completed week at the time of the Phase 2/8 live runs (Phase 8 input).

## Owner actions (final list from the Phase 8 plan, 2026-10-23)

- **U1.** Approve up to $10 of Anthropic spend for Phase 8 and up to $20 for Phase 7 verification. The ledger entries are done.
- **U2 (conditional).** A local `CFBD_API` connection, due Sun Nov 1, needed only if the Oct 31 restore gate (AWS 2026.1.1 → laptop 2026.2) fails, or for U11.
- **U3.** `ANTHROPIC_API_KEY` in `.env` when it's available. The orchestrator generates the app, console and chat passwords and creates their users.
- **U4.** A Claude Desktop `exasol-cfb` entry via `wsl.exe -e ~/.local/bin/uvx exasol-mcp-server@<v>`, by Fri Nov 13.
- **U5 (fallback only).** `make weekly WEEK=n CONFIRM=yes` if no session has run the Sunday cadence by Monday evening.
- **U6.** Confirm the demo date (Nov 16–20 assumed) and the venue network. Approve the bubble team and team pairs on Nov 15.
- **U7.** A fallback video and screenshots at dress rehearsal #1 (Nov 9–13).
- **U8.** Confirm the license (MIT, Exasol AG), the CFBD terms, that team marks appear in private media only, and the dependency inventory.
- **U9.** What ships publicly, any history rewrite, and when to publish.
- **U10.** One click-through rehearsal. On demo day: `exasol stop/start -d local` (never exakit), mains power, and a backup network.
- **U11 (optional).** Live data on the laptop on stage (needs U2).

Key dates: Oct 31 restore gate · Nov 1/8/15 Sunday cadence · Nov 3/10 predictions before kickoff · Nov 9 go/no-go on the demo tier · Nov 16–20 demo.

## Notes (information only)

- **Two V.3 manual steps were blocked by auto mode.** Integration tests and the live 9.5b run cover them.
- **Local Personal has the real namespace deployed**, in `FETCH_MODE='replay'`.
