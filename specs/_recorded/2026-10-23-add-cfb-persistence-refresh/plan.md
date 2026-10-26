# Plan: add-cfb-persistence-refresh

## Summary

Roadmap Phase 2: persist CFBD data from `CFBD_VS` into the `CFB_CORE` mirrors under a reviewed load plan, and keep it current with one SQL call per week.

- **Load plan.** `loader/load-plan.yaml` sets, per entry: slicing (`none` / `season` / `season_range`), seasons, gaps, refresh class (`static` / `weekly`), profile (`demo` / `full`) and priority. It also holds the monthly call budget with a refresh reserve.
- **Loader scripts.** Two Exasol Lua scripts, the only way Exasol runs multi-statement SQL in the database:
  - `EXECUTE SCRIPT CFB_INFRA.BACKFILL(profile, from_season, max_calls)`: resumable and idempotent, newest first, missing slices only. It stops before the budget and isolates and then quarantines slices that keep failing.
  - `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)`: the demo's one-call weekly refresh. It returns a step timeline per table, and later phases plug their steps into it.
- **Logs and quota.** Run, step, failure and fetch logs, plus a zero-call quota view over `/info`. Its budget caps the key's whole monthly use.
- **Snapshots.** `make snapshot` / `make restore` copy `CFB_CORE`, the load log and the fetch log to private Parquet files, and rebuild any deployment with zero API calls. A restore is also the rehearsal reset.

> **Superseded (probe 1.2, decisions [31] and [44]):** snapshots are gzip CSV files written and read by pyexasol, not Parquet through exapump, and `loader/exapump.py` was removed again in task 5.2. Every "Parquet", "exapump" and `exapump_env` mention below (Summary, Snapshots, Targets, tasks 1.2, 1.3, 5.2, 6.1, 9.0) describes the abandoned design. The specs, `docs/user-guide.md`, `specs/architecture.md` (D45) and the decision log are authoritative.

All of it is verified at zero CFBD calls against the mock API and replay in the `TEST_` namespace. Before any paid call on the AWS `default` instance, a zero-call smoke test runs there. The plan ends with a measured live run of at most 300 CFBD calls: the demo backfill (217), one refresh (4) and a snapshot from AWS restored on the laptop (0). The in-database budget is set to the owner's October authorization (400) for that run.

## Design

### Context

The mission promises that all history is persisted once, that a single SQL call refreshes each week, and that a rebuilt database never pays for data twice. The foundation plan (`add-cfbd-vs-foundation`) provides the virtual schema and the contracts this phase writes to:

- `CFB_META.LOAD_LOG`: slice key and `IS_FINAL`; read-through serves only final slices.
- `CFB_META.FETCH_LOG` and the request metadata columns `REQ_*`.
- The `CFB_CORE` mirrors, generated from the same catalog as the virtual tables.
- The `EXPLAIN VIRTUAL` manifest.
- The `TEST_` namespace harness, whose `stage_slices` already shows the load pattern.

Five forces shape this phase:

- **Execution.** Python UDFs cannot write tables, and the adapter cannot run SQL.
  - In Exasol, only Lua scripting programs run multi-statement SQL in the database (`CREATE [LUA] SCRIPT …`, `EXECUTE SCRIPT …`).
  - `query()` ends the script on an error; `pquery()` lets it continue (skill `exasol-udfs`).
  - There is no `CALL` statement. `CALL` is a reserved word without a statement (grammar checked with the `exasol-database` skill).
  - The demo line therefore becomes `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`.
- **Quota.**
  - The demo must work on a free key (1,000 calls/month, D13).
  - The owner authorized at most 400 live calls in October 2026 in total, including the foundation's ≤ 50.
  - `/info` is free (verified 2026-10-21). It counts every call of the key, including the CBBD shared pool, and its period is the calendar month (`resetAt` 2026-11-01).
  - The foundation's fetch UDF fails a whole fan-out query when one slice fails (`vs/cfbd_vs/fetch.py`), and retries count against the quota.
- **Exact slice matching** (foundation [5]). A stored season slice does not serve a week query, so the same game must never be stored under two overlapping slices. A weekly refresh therefore re-fetches the current season's season slices, which stay non-final, instead of adding week slices. Child tables are joined by position within one response, so two child tables fetched by two requests can drift apart; the rankings are loaded from one table only.
- **Runtime.**
  - Integration tests run on Exasol Personal local (nano 2026.2, single node, 127.0.0.1:8563), which a parallel session uses for exclusive benchmark windows.
  - Live data lives on the AWS `default` Exasol Personal instance (3 nodes, version not yet recorded), where the owner created `CFBD_API`. Every platform fact so far was probed on local nano.
  - `sys` owns the real deployment (foundation [20]).
- **Snapshots must stay private** (CFBD terms; D11):
  - Native `EXPORT` writes only CSV/FBV, and native Parquet `IMPORT` reads only from S3 (skills `exasol-export`, `exasol-import`).
  - Local Parquet goes through `exapump export` and `exapump upload` (0.13.0 installed).
  - S3 is excluded: it is external and costs money.

**Goals**
- One reviewed load plan from which every planned slice, every call estimate and every finality decision is derived.
  - It is validated against the catalog and compiled with the adapter's own planner, so planned slice keys always equal virtual-table requests.
  - Profiles: `demo` (217 calls once, then 4 per refresh), and `full` as a mechanism for the optional archive. The `full` entries themselves are backlog.
- `BACKFILL` that is:
  - **resumable:** one transaction per batch;
  - **idempotent:** it fetches only missing slices, and finalizable slices once more;
  - ordered newest first by priority;
  - parallel within a batch (one fan-out query per plan entry);
  - kept below the monthly budget minus the refresh reserve, measured on `/info`;
  - registering known gaps at zero calls;
  - **failure-tolerant:** a failing batch is retried slice by slice, slices that keep failing are quarantined, and a systemic failure stops the run.
- `REFRESH_WEEK(season, week, season_type)` as a step chain (`CHECK`, one `FETCH <table>` per table, then registered steps) that:
  - returns the timeline;
  - commits each step's log row so the app sees progress;
  - fails before any call when the budget cannot cover it;
  - keeps the previous data of a table whose fetch fails.
- Load, refresh, failure and fetch logs: `LOAD_RUN`, `REFRESH_LOG`, `LOAD_FAILURES`, and the existing `FETCH_LOG` and `LOAD_LOG`. Runs are exclusive, and stale locks (dead, own or idle session) never block.
- `CFB_META.QUOTA`: the key's quota from free `/info`, the project budget (a cap on the key's monthly use), both spendable amounts, calls per run kind this period, and the calls to complete each profile.
- Orphan slices (stored slices that match no planned key after a catalog change, plan edit or restore) are reported, and the loader refuses to re-buy their table.
- `make snapshot` / `make restore` on both targets:
  - private directory `~/.cfb-demo/snapshots/<name>/`, manifest with per-table fingerprints;
  - catalog-hash and column checks, then an atomic, fingerprint-checked restore with zero API calls;
  - the restore also serves as the rehearsal reset.
- `TARGET=local|aws` for the loader's make targets, through the foundation's `vs/host/target.py` (P2): the sys password is read at run time and the AWS certificate is pinned. The loader adds only the exapump child-process environment.
- A shared live-call ledger: this plan's cap (300), plus the monthly authorization checked against `/info` used calls (2026-10: 400). It is the second guard after the in-database budget.

**Non-Goals**
- `PROMOTE()`, the nightly persistence of slices fetched on demand. It needs the backend's capture of on-demand fetch logs (Phase 6) and is deferred until then (decision [18]).
- Model, prediction and simulation steps of `REFRESH_WEEK` (Phases 3–5). This plan provides the step registry and runs none.
- **Backlog ([29]):**
  - `season_week` slicing and week-based finality;
  - authoring the `full` profile's entries;
  - automatic refresh of the current season's calendar.

  The demo profile needs none of them.
- Recording the weekly refresh slices for offline replay rehearsals (Phase 8, with `make record`).
- Mirror migration when the catalog changes a column or a default ([23]). Deploy and restore refuse on a column difference, and orphans block re-fetching. Migration is an open question for the architecture.
- A database role that lets the app user trigger `REFRESH_WEEK` (Phase 6). Lua scripts run with the caller's rights.
- Private S3 snapshots, the dev-container Docker volume (D17), and a `CALL` alias via an SQL preprocessor.
- Fetching several tables from one response (foundation non-goal). It is why the demo loads `RANKINGS_POLLS_RANKS` alone, with parent keys (prerequisite P1).

### Decision

The load plan is compiled on the host into `CFB_META` tables. SQL views derive the slice universe, the backfill queue, finality, orphans, the load status and the quota from those tables, the load log, the failure list and `/info`. Two thin Lua scripts walk the views and load batch by batch. Every statement in them runs through `pquery`, and each entry function is wrapped in `pcall`. Each batch is one fan-out `INSERT … SELECT` through `CFBD_VS` for one plan entry: read-through stays on, only fetched rows are kept (`REQ_SOURCE <> 'STORED'`), and the rows go through a stage table into `CFB_CORE` in one transaction together with `LOAD_LOG` and `FETCH_LOG`. Host Python only deploys, wraps the calls with estimates and the ledger, and runs snapshot and restore.

#### Architecture

```
 loader/load-plan.yaml ──▶ loader/plan.py (validate vs catalog) ──▶ loader/templates.py (adapter planner: slice templates)
                                                                          │
 make deploy [TARGET] [LOADER_CONFIG=…] ─▶ vs/host/deploy.py (foundation) ─▶ loader/deploy.py:
     CFB_META.{LOAD_PLAN, LOAD_PLAN_GAPS, LOADER_CONFIG, SEASON_NUMBERS, REFRESH_STEPS, LOAD_RUN, REFRESH_LOG, LOAD_FAILURES}
     CFB_META views {LOAD_SLICES, BACKFILL_QUEUE, LOAD_STATUS, QUOTA}      CFB_STAGE.<T> (mirror DDL)
     CFB_INFRA.BACKFILL, CFB_INFRA.REFRESH_WEEK  (Lua; loader/lua/*.lua bundled by loader/bundle.py)
     warning when stored slices no longer match the plan (orphans)

 EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')   EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, NULL)
   1 CHECK: args, calendar (week, season_type), lock,          lock; gaps ─▶ LOAD_LOG (final, 0 rows, 0 calls)
           orphans, QUOTA.REFRESH_SPENDABLE                    loop: QUOTA in its own transaction (live) ─▶ allowance
   2.. FETCH <table>: weekly slices of the season ─┐                 next batch = one ENTRY_ID from BACKFILL_QUEUE ─┐
   100.. EXECUTE SCRIPT <step>(run, season, week, season_type)                                                     ▼
   per batch (one plan entry, ≤ MIN(MAX_CALLS_PER_QUERY, allowance) slices, one transaction, every statement via pquery):
     EXPLAIN VIRTUAL <batch query>  ── manifest keys = batch keys? (0 calls; else stop: "make deploy")
     INSERT INTO CFB_STAGE.T SELECT * FROM CFBD_VS.T WHERE <fixed filter> AND <batch filter> AND REQ_SOURCE <> 'STORED'
     DELETE FROM CFB_CORE.T WHERE REQ_SLICE IN (batch) ; INSERT INTO CFB_CORE.T SELECT * FROM CFB_STAGE.T
     MERGE INTO LOAD_LOG (IS_FINAL = LOAD_SLICES.FINAL_IF_LOADED_NOW) ; INSERT INTO FETCH_LOG ; DELETE FROM LOAD_FAILURES (batch)
     DELETE FROM CFB_STAGE.T ; UPDATE LOAD_RUN/REFRESH_LOG ; COMMIT ; measured spend = Δ /info used calls
   batch error: ROLLBACK ─▶ (BACKFILL) each slice alone ─▶ failing slice: LOAD_FAILURES +1 per run
                ─▶ every slice failed alone: run FAILED, stop      (REFRESH_WEEK: step FAILED, stop)
   any error: ROLLBACK, log FAILED, COMMIT, re-raise

 SELECT * FROM CFB_META.QUOTA   ── CFBD_VS.INFO (/info, free) + LOADER_CONFIG + FETCH_LOG + BACKFILL_QUEUE

 make snapshot NAME=n TARGET=aws ─▶ lock ─▶ exapump export (Parquet) of CFB_CORE.*, LOAD_LOG, FETCH_LOG ─▶ ~/.cfb-demo/snapshots/n/
                                            + manifest.json (catalog hash, columns, rows, SHA-256, fingerprints)
 make restore NAME=n TARGET=local CONFIRM=yes [FORCE=yes] ─▶ column + catalog-hash check ─▶ lock
     ─▶ exapump upload into CFB_RESTORE.<T> ─▶ fingerprints ─▶ one transaction: replace every scope table
     ─▶ drop CFB_RESTORE ─▶ LOAD_RUN 'RESTORE' row                (never touches CFBD_VS: 0 calls)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Declarative load plan, compiled at deploy | `loader/plan.py`, `loader/templates.py`, `CFB_META.LOAD_PLAN` | One reviewed file decides what costs calls. The validator rejects what the API or the catalog cannot serve before any call |
| Slice templates from the adapter's own planner | `loader/templates.py` | The slice-key grammar exists once (`vs/cfbd_vs/plan.py`). The host runs it with sentinel values, so planned and requested keys cannot drift |
| Set logic in SQL views, thin Lua | `sql/loader/*.sql`, `loader/lua/*.lua` | Finality, gaps, gating, orphans, ordering and quota math are declarative, tested on the engine before any Lua exists (group E). Lua only loops, logs and commits |
| Read-through stays on; keep fetched rows only | batch query `… AND REQ_SOURCE <> 'STORED'` | Virtual schema properties are global. Switching `READ_THROUGH` off for a load would make concurrent queries re-fetch stored slices, and a crash would leave it off |
| One plan entry per batch | `BACKFILL_QUEUE.ENTRY_ID` | One `WHERE` clause (fixed filter + slicing) expresses every batch |
| Stage, then swap, in one transaction per batch | `CFB_STAGE.<T>`, `load_batch` in `loadlib.lua` | The HTTP part runs before `CFB_CORE` is touched, so the write window is short. A failed batch leaves no trace, and earlier batches stay committed (resumable) |
| Catch, roll back, log, re-raise | `pquery` for every statement, `pcall` around each entry | `query()` would end the script before the failure could be logged and the lock released |
| Per-slice fallback + quarantine | `LOAD_FAILURES`, `quarantine_after` | One bad slice costs neither its batch's siblings nor the queue behind it. A systemic failure (all slices fail) stops the run instead of burning calls |
| Measured spend | `/info` used calls before and after each batch | Retries count against the quota. The allowance follows the key's real counter, not an estimate |
| Zero-call drift check | `EXPLAIN VIRTUAL` per batch | Compares the manifest's slice keys with the batch before any request |
| Orphan guard | `LOAD_STATUS.ORPHAN_SLICES`, deploy warning, loaders refuse | After a catalog change, plan edit or restore, re-fetching would pay twice. The loader refuses until the owner migrates or deletes the orphans |
| Gaps as stored empty slices | `LOADED_BY = 'LOAD_PLAN_GAP'` | Read-through then serves known holes at 0 calls too (foundation scenario "a stored empty slice costs zero calls") |
| Run lock in the run log | `LOAD_RUN` `RUNNING` row | One loader run at a time. Snapshot and restore join the lock. Dead, own and idle sessions never block |
| Steps commit their log rows | `REFRESH_LOG` | The app's refresh timeline (Phase 6) reads progress from another session |
| Step registry | `refresh_steps` in the YAML → `REFRESH_STEPS`, contract `(run_id, season, week, season_type)` | Phases 3–5 add model, prediction and simulation steps without touching the refresh script, and can address postseason weeks |
| Clock seam | `LOADER_CONFIG.NOW_OVERRIDE` (test namespace only) | Finality and season rollover are date rules, and tests need deterministic dates |
| Fingerprinted, atomic restore | `loader/snapshot.py` | Format conversions (Parquet ↔ Exasol types) are checked, not trusted. A failed restore changes nothing |
| Deployment targets | foundation `vs/host/target.py` (P2) + `loader/exapump.py` | The same commands run on local Personal and on AWS. Secrets are read at connect time, AWS TLS is pinned, and the loader adds only the exapump child environment |
| Two budget guards | in-DB `PROJECT_BUDGET` on `/info` + ledger | The in-database cap holds for any SQL client. The ledger audits this plan's live work and caps it per plan |

#### Key Interfaces

**SQL entry points** (Lua scripting programs in `CFB_INFRA`, owned and run by `sys` on the real deployment)

```sql
EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, NULL);   -- profile, from_season, max_calls (NULL = no limit; 0 = dry run)
-- RETURNS TABLE: RUN_ID DECIMAL(18,0), TABLE_NAME VARCHAR(128), SLICES DECIMAL(9,0), ROWS_LOADED DECIMAL(18,0),
--   CALLS DECIMAL(9,0), FAILED_SLICES DECIMAL(9,0), RUN_STATUS VARCHAR(20), CALLS_NEEDED DECIMAL(9,0), DETAIL VARCHAR(2000)
--   one row per table loaded, blocked or with failures, then TABLE_NAME = '*' with the run totals; RUN_STATUS is
--   DONE | DONE_WITH_FAILURES | STOPPED_BUDGET | STOPPED_MAX_CALLS | FAILED

EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular');   -- season, week, season_type ('regular' | 'postseason'; NULL = regular)
-- RETURNS TABLE: RUN_ID, STEP_NO DECIMAL(3,0), STEP_NAME VARCHAR(160), STEP_STATUS VARCHAR(10), SECONDS DECIMAL(9,3),
--                SLICES, ROWS_LOADED, CALLS, DETAIL
--   steps: 1 CHECK, 2..99 FETCH <table>, 100.. registered (EXECUTE SCRIPT <script>(run_id, season, week, season_type))
```

Never run the demo line `WITH OUTPUT`: that discards the returned timeline (skill `exasol-udfs`).

**Load plan** (`loader/load-plan.yaml`; validator rules in task 2.1 and the `loader/load-plan` spec)

```yaml
version: 1
config:
  monthly_call_budget: 1000        # D13: design target is the free tier, even on a larger key
  refresh_reserve: 150             # kept back from BACKFILL for REFRESH_WEEK and on-demand queries
  quarantine_after: 2              # runs in which a slice failed alone before it is skipped
  season_rollover: "08-01"         # a new season's slices enter the plan on this day
  season_final_after: "02-15"      # season S is final when loaded on or after (S+1)-02-15
  refresh_profiles: [demo]
snapshot:
  schemas: [CFB_CORE]
  meta_tables: [LOAD_LOG, FETCH_LOG]
refresh_steps: []                  # Phases 3–5: {step_no: 100, name: MODEL, script: CFB_INFRA.REFRESH_MODEL}
entries:
  - {table: TEAMS,                    profile: demo, priority: 1, slicing: none,         refresh_class: static}
  - {table: VENUES,                   profile: demo, priority: 1, slicing: none,         refresh_class: static}
  - {table: CONFERENCES,              profile: demo, priority: 1, slicing: none,         refresh_class: static}
  - {table: CONFERENCES_AFFILIATIONS, profile: demo, priority: 1, slicing: season_range, seasons: {from: 1869, to: 2025}, refresh_class: static}
  - {table: CONFERENCES_AFFILIATIONS, profile: demo, priority: 1, slicing: season,       seasons: {from: 2026}, refresh_class: static}
  - {table: CALENDAR,                 profile: demo, priority: 2, slicing: season,       seasons: {from: 2013}, refresh_class: static}
  - {table: GAMES,                    profile: demo, priority: 3, slicing: season,       seasons: {from: 1869}, gaps: [1871], refresh_class: weekly}
  - {table: METRICS_WP_PREGAME,       profile: demo, priority: 4, slicing: season,       seasons: {from: 2013}, refresh_class: weekly}
  - {table: RATINGS_SP,               profile: demo, priority: 5, slicing: season,       seasons: {from: 2013}, refresh_class: weekly}
  - {table: RANKINGS_POLLS_RANKS,     profile: demo, priority: 6, slicing: season,       seasons: {from: 2014}, refresh_class: weekly}
  # full entries: backlog ([29])
```

Entry fields: `table`, `profile`, `priority`, `slicing` (`none` | `season` | `season_range`), `seasons` {`from`, `to`}, `gaps` (seasons), `params` (fixed parameter values, e.g. `{classification: fbs}`), `refresh_class` (`static` | `weekly`), `note`. `none` and `season_range` slices have `SEASON` NULL.

**Prerequisite P1 (foundation fix, in progress; not edited by this plan):**
- In `vs/catalog-overrides.yaml`, `RANKINGS_POLLS_RANKS` gets its own parent keys `SEASON`, `WEEK` and `POLL`, and the catalog is regenerated. The table then loads standalone, and `RANKINGS_POLLS` is not loaded.
- Task 2.3 checks the regenerated catalog and stops if the keys are missing.
- Open recommendation to the foundation (not a blocker): add `SEASON_TYPE` too. The final poll is postseason week 1, so it shares (`SEASON`, `WEEK` = 1, `POLL`) with the regular-season week-1 poll. Without it, Phase 3 must tell them apart some other way.

**Demo profile budget** (current season 2026; replaces the roadmap's "about 200 / about 5")

| Entries | Calls |
|---|---|
| `TEAMS`, `VENUES`, `CONFERENCES` (one slice each) | 3 |
| `CONFERENCES_AFFILIATIONS` 1869–2025 as one range slice + 2026 | 2 |
| `CALENDAR` 2013–2026 | 14 |
| `GAMES` 1869–2026 without the 1871 gap (Elo/WP per game) | 157 |
| `METRICS_WP_PREGAME` 2013–2026, `RATINGS_SP` 2013–2026 | 28 |
| `RANKINGS_POLLS_RANKS` 2014–2026 (P1) | 13 |
| **Backfill total** | **217** |
| **Weekly refresh:** current-season slices of `GAMES`, `METRICS_WP_PREGAME`, `RATINGS_SP`, `RANKINGS_POLLS_RANKS`. `/games/schedule` is volatile, has no mirror and is not stored | **4** |

Assumption A4: `/games?seasonType=both&year=Y` returns all divisions in one call (the foundation's S1 marker `classification-omission-means-all`). If S1 shows otherwise, `GAMES` gets one entry per classification (`params: {classification: …}`) and the demo profile grows by about 157 calls per extra division.

**CFB_META objects** (`sql/meta/*.sql`, rendered per namespace by `vs/host/metaddl.py`; placeholders `{META_SCHEMA}`, `{CORE_SCHEMA}`, `{STAGE_SCHEMA}`, `{INFRA_SCHEMA}`, `{VS_NAME}`; reserved words avoided: `PROFILE`, `FINAL`, `ENABLED`, `ROWS`, `LOG`, `VALUE`, `REFRESH`, `STATE`, `CURRENT`)

```sql
CREATE TABLE {META_SCHEMA}.LOAD_PLAN (ENTRY_ID DECIMAL(9,0) NOT NULL, TABLE_NAME VARCHAR(128) UTF8 NOT NULL,
  PROFILE_NAME VARCHAR(16) UTF8 NOT NULL, PROFILE_RANK DECIMAL(2,0) NOT NULL, PRIORITY DECIMAL(4,0) NOT NULL,
  SLICING VARCHAR(16) UTF8 NOT NULL, REFRESH_CLASS VARCHAR(16) UTF8 NOT NULL,
  SEASON_FROM DECIMAL(4,0), SEASON_TO DECIMAL(4,0),               -- SEASON_TO NULL = current season
  FIXED_FILTER VARCHAR(2000) UTF8,                                 -- rendered SQL, e.g. "P_CLASSIFICATION" = 'fbs'
  SLICE_TEMPLATE VARCHAR(2000) UTF8 NOT NULL,                      -- /games?seasonType=both&year={season}
  GATED_BY VARCHAR(32) UTF8, PLAN_HASH VARCHAR(64) UTF8 NOT NULL,
  CONSTRAINT LOAD_PLAN_PK PRIMARY KEY (ENTRY_ID));
CREATE TABLE {META_SCHEMA}.LOAD_PLAN_GAPS (ENTRY_ID DECIMAL(9,0) NOT NULL, SEASON DECIMAL(4,0) NOT NULL);
CREATE TABLE {META_SCHEMA}.LOADER_CONFIG (SETTING_NAME VARCHAR(64) UTF8 NOT NULL, SETTING_VALUE VARCHAR(2000) UTF8);
  -- MONTHLY_CALL_BUDGET, REFRESH_RESERVE, QUARANTINE_AFTER, SEASON_ROLLOVER, SEASON_FINAL_AFTER, REFRESH_PROFILES,
  -- NOW_OVERRIDE (TEST_ namespace only; deploy removes it elsewhere)
CREATE TABLE {META_SCHEMA}.SEASON_NUMBERS (SEASON DECIMAL(4,0) NOT NULL);           -- 1869..2100
CREATE TABLE {META_SCHEMA}.REFRESH_STEPS (STEP_NO DECIMAL(3,0) NOT NULL, STEP_NAME VARCHAR(64) UTF8 NOT NULL,
  SCRIPT_NAME VARCHAR(256) UTF8 NOT NULL);                                           -- STEP_NO >= 100
CREATE TABLE {META_SCHEMA}.LOAD_RUN (RUN_ID DECIMAL(18,0) IDENTITY NOT NULL, RUN_KIND VARCHAR(16) UTF8 NOT NULL,
  RUN_ARGS VARCHAR(200) UTF8, SESSION_ID DECIMAL(20,0) NOT NULL, STARTED_AT TIMESTAMP NOT NULL, ENDED_AT TIMESTAMP,
  RUN_STATUS VARCHAR(20) UTF8 NOT NULL, SLICES DECIMAL(9,0), ROWS_LOADED DECIMAL(18,0), CALLS DECIMAL(9,0),
  REMAINING_BEFORE DECIMAL(9,0), REMAINING_AFTER DECIMAL(9,0), DETAIL VARCHAR(2000) UTF8);
CREATE TABLE {META_SCHEMA}.REFRESH_LOG (RUN_ID DECIMAL(18,0) NOT NULL, STEP_NO DECIMAL(5,0) NOT NULL,
  STEP_NAME VARCHAR(160) UTF8 NOT NULL, STEP_STATUS VARCHAR(10) UTF8 NOT NULL, STARTED_AT TIMESTAMP NOT NULL,
  ENDED_AT TIMESTAMP, SLICES DECIMAL(9,0), ROWS_LOADED DECIMAL(18,0), CALLS DECIMAL(9,0), DETAIL VARCHAR(2000) UTF8);
CREATE TABLE {META_SCHEMA}.LOAD_FAILURES (TABLE_NAME VARCHAR(128) UTF8 NOT NULL, SLICE_KEY VARCHAR(2000) UTF8 NOT NULL,
  FAILURES DECIMAL(3,0) NOT NULL, LAST_ERROR VARCHAR(2000) UTF8, LAST_RUN_ID DECIMAL(18,0), LAST_FAILED_AT TIMESTAMP,
  CONSTRAINT LOAD_FAILURES_PK PRIMARY KEY (TABLE_NAME, SLICE_KEY));
```

Views (`sql/loader/*.sql`):
- `LOAD_SLICES`: one row per planned slice, from `LOAD_PLAN` × `SEASON_NUMBERS` (≤ current season; `none` and `season_range` give one slice with `SEASON` NULL).
  - Columns: `ENTRY_ID`, `TABLE_NAME`, `PROFILE_NAME`, `PROFILE_RANK`, `PRIORITY`, `SLICING`, `REFRESH_CLASS`, `SEASON`, `SLICE_KEY` (template with numbers substituted), `IS_GAP`, `FINAL_IF_LOADED_NOW`, `IS_STORED`, `IS_FINAL`, `ROWS_LOADED`, `LOADED_AT`, `FAILURES`, `IS_QUARANTINED`.
  - It excludes entries whose `GATED_BY` feature is missing from the virtual schema's `API_FEATURES`.
- `BACKFILL_QUEUE`: `LOAD_SLICES` without gaps, quarantined slices and tables with orphans, where `NOT IS_STORED OR (NOT IS_FINAL AND FINAL_IF_LOADED_NOW)`.
  - `QUEUE_ORDER`: `FAILURES > 0` last, then `PROFILE_RANK`, `PRIORITY`, `SEASON DESC NULLS FIRST`, `TABLE_NAME`, `ENTRY_ID`.
  - `BATCH_ALONE` is set for slices with failures.
- `LOAD_STATUS`: per entry, planned, stored, final, missing, quarantined and orphan slices; calls to complete; stored rows; last load; `BLOCKED_BY` (missing feature or `ORPHANS`).
  - Orphans are `LOAD_LOG` rows of the entry's table with `LOADED_BY` `BACKFILL#…`, `REFRESH_WEEK#…` or `LOAD_PLAN_GAP` and no matching planned key.
- `QUOTA`: as in the `loader/quota` spec. `PERIOD_START = ADD_MONTHS(RESET_AT, -1)`.
- "Now" is one UTC expression shared by the views and the Lua (probe 1.1l), or `NOW_OVERRIDE`.

**Batch transaction** (inside the Lua scripts; every statement through `pquery`; names are deploy-time constants, double-quoted by the bundler; slice keys bound as `:values`)

```sql
-- before the batch, in its own transaction (live mode): SELECT … FROM "CFB_META"."QUOTA"; COMMIT;
SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL SELECT * FROM "CFBD_VS"."GAMES"
  WHERE "P_YEAR" IN (2020, 2019) AND "REQ_SOURCE" <> 'STORED');   -- manifest slice_keys must equal the batch (0 calls)
  -- fallback per probe 1.1c: query("EXPLAIN VIRTUAL …") returns the result set directly
-- check: "CFB_STAGE"."GAMES" is empty
INSERT INTO "CFB_STAGE"."GAMES" SELECT * FROM "CFBD_VS"."GAMES"
  WHERE "P_YEAR" IN (2020, 2019) AND "REQ_SOURCE" <> 'STORED';    -- the fetch: one fan-out query, one plan entry
-- check: every staged REQ_SLICE is a batch key
DELETE FROM "CFB_CORE"."GAMES" WHERE "REQ_SLICE" IN (:k1, :k2);
INSERT INTO "CFB_CORE"."GAMES" SELECT * FROM "CFB_STAGE"."GAMES";
MERGE INTO "CFB_META"."LOAD_LOG" L USING (
  SELECT S."TABLE_NAME", S."SLICE_KEY", S."FINAL_IF_LOADED_NOW" AS "IS_FINAL", COALESCE(C."N", 0) AS "ROWS_LOADED"
    FROM "CFB_META"."LOAD_SLICES" S
    LEFT JOIN (SELECT "REQ_SLICE", COUNT(*) AS "N" FROM "CFB_STAGE"."GAMES" GROUP BY "REQ_SLICE") C
      ON C."REQ_SLICE" = S."SLICE_KEY"
   WHERE S."ENTRY_ID" = 7 AND S."SLICE_KEY" IN (:k1, :k2)) X
  ON (L."TABLE_NAME" = X."TABLE_NAME" AND L."SLICE_KEY" = X."SLICE_KEY")
  WHEN MATCHED THEN UPDATE SET "IS_FINAL" = X."IS_FINAL", "ROWS_LOADED" = X."ROWS_LOADED",
       "LOADED_AT" = <utc now>, "LOADED_BY" = 'BACKFILL#42'
  WHEN NOT MATCHED THEN INSERT VALUES (X."TABLE_NAME", X."SLICE_KEY", X."IS_FINAL", X."ROWS_LOADED", <utc now>, 'BACKFILL#42');
INSERT INTO "CFB_META"."FETCH_LOG" (…)   -- one row per batch key: staged REQ_* metadata, or ROWS_RETURNED 0 + fetch source
DELETE FROM "CFB_META"."LOAD_FAILURES" WHERE "TABLE_NAME" = 'GAMES' AND "SLICE_KEY" IN (:k1, :k2);
DELETE FROM "CFB_STAGE"."GAMES";
UPDATE "CFB_META"."LOAD_RUN" …; UPDATE "CFB_META"."REFRESH_LOG" …;
COMMIT;
-- afterwards (live mode): read /info again; measured spend = Δ used calls; allowance −= spend
-- any pquery failure: ROLLBACK; then BACKFILL retries each slice alone (multi-slice batch) or records the failure;
-- the log rows are written and committed; error() only after the log is committed
```

Batch filters, each prefixed by the entry's `FIXED_FILTER`:
- `season`: `"P_YEAR" IN (…)`;
- `season_range`: `"P_MIN_YEAR" = a AND "P_MAX_YEAR" = b`;
- `none`: no parameter filter.

**Namespace additions** (`vs/host/namespace.py`): `stage_schema` = `{prefix}CFB_STAGE` (one table per planned mirror, same DDL, normally empty) and `restore_schema` = `{prefix}CFB_RESTORE` (exists only during a restore).

**Lua layout and ownership**
- `loader/bundle.py` (task 4.1): renders `CREATE OR REPLACE LUA SCRIPT {INFRA_SCHEMA}.<NAME>(<params>) RETURNS TABLE AS <constants> <loadlib> <entry> /` into `build/sql/loader_{backfill,refresh_week}.sql`.
  - Namespace names become double-quoted Lua string constants.
  - `::name` inserts text verbatim, so it is used only for these constants; every value is bound as `:name`.
- `loader/lua/loadlib.lua` is owned by task 4.2:
  - lock with stale-row rules, run and step logging;
  - config, virtual schema properties, quota read in its own transaction;
  - `next_batch`, `explain_check`, `load_batch`;
  - per-slice fallback and failure recording;
  - `fail`.
- `loader/lua/backfill.lua` comes with 4.2. `loader/lua/refresh_week.lua` comes with 4.3, which starts after 4.2.

**Snapshots** (`~/.cfb-demo/snapshots/<name>/`, mode 0700; files 0600; `CFB_SNAPSHOT_DIR` overrides; refused inside the repo or a OneDrive path)

```
manifest.json      {"name", "created_at", "source": {"target", "host"}, "db_version", "catalog_hash", "plan_hash",
                    "format": "parquet-zstd", "tables": [{"schema", "table", "rows", "columns": [[name, type]...],
                    "file", "sha256", "fingerprint": {"rows": n, "<COL>": [non_null, aggregate], ...}}]}
CFB_CORE.GAMES.parquet, ..., CFB_META.LOAD_LOG.parquet, CFB_META.FETCH_LOG.parquet
```

- Scope: `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`. `LOAD_RUN`, `REFRESH_LOG` and `LOAD_FAILURES` stay with each deployment.
- Restore refuses on a column difference, and on a catalog-hash difference unless `FORCE=yes`.
- Export and upload use `exapump export --format parquet --compression zstd` and `exapump upload`. They run as child processes with `EXAPUMP_DSN` set only in the child's environment and `--certificate-fingerprint` pinned.
- Fallback, if probe 1.2 fails: gzip CSV through pyexasol `export_to_file` / `import_from_file`, with the same manifest and fingerprints.

**Targets** (prerequisite P2: the foundation fix `vs/host/target.py`; this plan only adds to it)

- The foundation fix provides `TARGET=local|aws`:
  - it resolves the DSN, the sys password and AWS certificate pinning from the Personal deployment files;
  - it is wired into `scripts/{deploy,grants,dns}.py` and the Makefile (`TARGET ?= local`).

  This plan does not re-implement any of it.
- The loader adds (task 1.3):
  - `loader/exapump.py`: builds the exapump child-process environment from a resolved target. `EXAPUMP_DSN` is set only in the child's environment, and `--certificate-fingerprint` comes from the target's pinned certificate, for the node exapump connects to.
  - `TARGET` for the new make targets (`backfill`, `refresh`, `quota`, `snapshot`, `restore`, `snapshots`) and `scripts/loader_cli.py`, `scripts/snapshot.py`, through the foundation module.

**Budget guards**
1. **In the database.** `PROJECT_BUDGET` on `/info` used calls (the key's whole monthly use). For the October live group, AWS is deployed with `make deploy TARGET=aws LOADER_CONFIG="monthly_call_budget=400,refresh_reserve=50"`, and 9.6 redeploys without the override. *Superseded in F.1 (decision [46]): `vs/live/loader-config.yaml` makes every `make deploy TARGET=aws` write the ledger's month cap and a reserve of 50, so no override and no reset redeploy are needed.*
2. **Ledger** (`cache/live-calls.jsonl`, shared with the foundation; caps in `vs/live/ledger.yaml`):

   ```yaml
   plans: {add-cfbd-vs-foundation: 50, add-cfb-persistence-refresh: 300}
   months: {"2026-10": 400}          # owner's authorization; a month without an entry refuses live calls
   ```

   - Records: `{"at", "plan", "target", "kind", "calls", "used_before", "used_after", "detail"}`.
   - The wrappers refuse when this plan's ledger total plus the estimate would exceed its cap, or when `/info` used calls (from `QUOTA`) plus the estimate would exceed the month's cap.
   - `LEDGER=off` disables the ledger for people running their own key.

**Make targets** (all DB targets take `TARGET=local|aws`)

| Target | Does |
|---|---|
| `make deploy [LOADER_CONFIG="k=v,…"]` | VS (foundation) + loader objects; re-run after editing `loader/load-plan.yaml`; the override lasts until the next deploy |
| `make backfill PROFILE=demo [FROM=y] [MAX_CALLS=n] [CONFIRM=yes]` | Estimate + quota, ledger check, then `EXECUTE SCRIPT …BACKFILL(…)`; prints the result |
| `make refresh SEASON=y WEEK=w [SEASON_TYPE=postseason] [CONFIRM=yes]` | Estimate + quota, ledger check, then `EXECUTE SCRIPT …REFRESH_WEEK(y, w, t)`; prints the timeline |
| `make quota` | Prints `CFB_META.QUOTA` |
| `make snapshot NAME=n` / `make restore NAME=n CONFIRM=yes [FORCE=yes]` / `make snapshots` | Snapshot, restore, list |

**Live budget (this plan, ≤ 300 CFBD calls; with the foundation's ≤ 50 this stays within the owner's 400 for 2026-10)**

| Step | Calls |
|---|---|
| 9.0 deploy (budget 400 / reserve 50), grants, `dns-check`, `make quota`, zero-call AWS smoke (`/info` is free) | 0 |
| 9.1 estimate | 0 |
| 9.2 demo backfill, first 20, then the rest | ≈ 217 |
| 9.3 rerun (idempotence) | 0 |
| 9.4 one live `REFRESH_WEEK` (may use the reserve) | 4 |
| 9.5 snapshot AWS → restore local | 0 |
| Reserve (retries, isolated slices) | ≤ 79 |
| **Hard cap (ledger)** | **300** |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Lua scripting programs as the entry points; host Python only deploys and wraps them | Host-side Python orchestrator (`make refresh` over pyexasol); Python UDF orchestration | Python UDFs cannot run SQL. A host orchestrator loses the demo's "one SQL call" and needs a client next to the DB. Lua is the only in-database scripting language (grammar: `CREATE [LUA] SCRIPT`) |
| `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')` on stage | `CALL REFRESH_WEEK(…)` via an SQL preprocessor alias | Exasol has no `CALL` statement. A preprocessor rewrite hides real Exasol syntax from an Exasol audience and adds a session-level dependency |
| Decision logic in SQL views; thin Lua; views tested before the Lua | Logic in Lua with a host Lua toolchain | Views are declarative, inspectable by the presenter and testable on the engine (group E). A Lua toolchain would add a dependency for about 300 lines of glue |
| `pquery` for every statement, `pcall` per entry | `query()` throughout | `query()` ends the script on the first error, so the rollback, the log and the lock release could not run |
| Read-through stays on; batch queries keep `REQ_SOURCE <> 'STORED'` | Switch `READ_THROUGH` off during loads (the orchestrator's wording) | Properties are global: concurrent queries would re-fetch stored slices and spend quota, and a crash would leave read-through off. Batch slices are never final-stored, so read-through returns nothing stored for them anyway |
| Batch = one plan entry; stage table + one transaction per batch | Batches per table; insert straight into `CFB_CORE`; one transaction per run | One `WHERE` per batch. The HTTP time stays outside the `CFB_CORE` write window. Failures are atomic per batch, and progress is kept (resumable) |
| Per-slice fallback, quarantine after 2 runs, systemic stop, measured spend | Fail the run; re-run the same batch | The fetch UDF fails a whole fan-out on one bad slice. Re-running the batch re-pays its siblings, and a persistent failure would block the queue forever |
| Slice templates compiled by the adapter's planner + `EXPLAIN` drift check + orphan guard | Re-implement the slice-key grammar in Lua/SQL | One grammar, no drift. Drift costs zero calls to detect, and orphans stop a re-buy after catalog changes |
| Weekly refresh re-fetches non-final season slices | Week slices for the current season | Exact-match read-through cannot subsume (foundation [5]), so week and season slices would store games twice. Same cost: 1 call per endpoint |
| Rankings from `RANKINGS_POLLS_RANKS` alone, with parent keys (P1) | Load `RANKINGS_POLLS` and `RANKINGS_POLLS_RANKS`, joined by position | Two requests in two transactions can shift the arrays, and the positional join then silently pairs ranks with the wrong week. One table saves 13 + 1/week calls |
| Refresh classes `static` / `weekly` with date-based finality; `season_week` and `archive` in the backlog | Week slicing now; finality by "all games completed" | The demo needs neither. Historic seasons have cancelled games that never complete, so dates are the deterministic and testable rule |
| `season_type` in `REFRESH_WEEK` and in the step contract | `(season, week)` only | Regular week 1 and postseason week 1 share a number. Phases 3–5 inherit the step contract, so it must carry the season type now |
| Budget and reserve in `LOADER_CONFIG`, deploy-time override, checked against `/info` | Virtual schema properties; ledger only | The adapter cannot use properties. The ledger misses calls from SQL clients, while `/info` sees them all |
| Order: profile, priority, newest season, table, entry; failed slices last | Strict season-major across all endpoints | Priority gets a real meaning and batches become full fan-outs. Equal priorities still interleave season by season |
| Parquet via exapump; scope `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`; catalog-hash check | Native `EXPORT` CSV via JDBC-local; pyexasol Parquet (infers types from CSV); S3; snapshot `LOAD_RUN` too | Native `EXPORT` has no Parquet and native Parquet `IMPORT` is S3-only. pyexasol's Parquet export infers column types. Restoring the run log would collide identities and resurrect lock rows |
| Reuse the foundation's `TARGET` module with TLS pinning; a zero-call smoke test on AWS before paid runs | A second target module; `CERT_NONE`; first AWS contact in the paid run | One owner for target resolution. The sys password must not cross the internet unverified. AWS is a 3-node cluster that nothing has probed yet |
| `PROMOTE()` deferred | Build it now | Without the backend's capture of on-demand fetch logs (Phase 6) there is nothing to promote except by re-fetching |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| loader/load-plan | NEW | `loader/load-plan/spec.md` |
| loader/backfill | NEW | `loader/backfill/spec.md` |
| loader/refresh-week | NEW | `loader/refresh-week/spec.md` |
| loader/run-logs | NEW | `loader/run-logs/spec.md` |
| loader/quota | NEW | `loader/quota/spec.md` |
| loader/snapshot-restore | NEW | `loader/snapshot-restore/spec.md` |

The foundation's `cfbd-vs/*` specs are not changed. This plan uses their contracts: `read-through` (the `LOAD_LOG` serving rule), `fetch-log` (`FETCH_LOG`, `REQ_*`, manifest) and `fan-out` (slice keys, `MAX_CALLS_PER_QUERY`). Prerequisite P1 changes the foundation's catalog overrides, not its specs.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls outside group J | Unit and CLI tests use fakes. Integration tests use the mock API and replay in the `TEST_` namespace. The harness guard against `CFBD_API` stays, and no new test file names it. The probes (1.1, 1.2) use only `PROBE_*` objects and the `TEST_` namespace; the AWS smoke (9.0) makes no data request |
| Live cap | ≤ 300 CFBD calls for this plan (ledger), and ≤ 400 for the key in 2026-10. The month cap is enforced twice: in the database (`PROJECT_BUDGET` 400 on `/info` used calls) and in the wrappers (`/info` used + estimate). Every live run goes through `make backfill` / `make refresh` with `CONFIRM=yes`. If group J slips past 2026-10-31, the owner must add a month entry and choose the November budget |
| Shared DB etiquette | Local Personal: probes and `make test-it` only in "local free" windows of `add-engine-spikes`. AWS `default`: deploy and run only, no stop, start or resize, and only after the "AWS free" signal |
| No CFBD data in git | Snapshots and manifests live in `~/.cfb-demo/snapshots/` (outside the repo and OneDrive). `docs/cfbd-api-measurements.md` holds only derived numbers. `git ls-files '*.parquet'` stays empty |
| No secrets in files, argv or logs | The sys password is read from the deployment's `secrets.json` at connect time. exapump gets its DSN only through the child's environment. The CFBD key stays in `CFBD_API` |
| TLS | No sys password is sent to a non-loopback host without certificate verification. AWS is pinned via `deployment.json` for every node |
| Loader leaves the VS alone | No loader code issues `ALTER VIRTUAL SCHEMA`. A unit test scans the Lua and Python loader sources |
| Error handling | Every Lua statement runs through `pquery`, and each entry function through `pcall`. No `RUNNING` row survives a failed run |
| UTC | Every loader timestamp (`LOADED_AT`, `STARTED_AT`, `ENDED_AT`, `NOW`) is UTC, from one expression fixed by probe 1.1l |
| Quoted identifiers | Every identifier the bundler renders into Lua or SQL is double-quoted |
| Current-season reads | Pages and the chat read current-season data from `CFB_CORE`, never through `CFBD_VS`: non-final slices are always fetched live (m1; documented in 8.1, 8.2) |
| Config tables survive test resets | The harness reset truncates data tables only (including `CFB_STAGE` and `LOAD_FAILURES`). `LOAD_PLAN`, `LOAD_PLAN_GAPS`, `LOADER_CONFIG`, `SEASON_NUMBERS` and `REFRESH_STEPS` keep the deployed plan |
| `NOW_OVERRIDE` is test-only | Deploy removes it from every namespace without the `TEST_` prefix |
| Runtime code | Lua as built into Exasol scripting (no external modules; the manifest check uses plain string search, no JSON library). Host code is Python 3.12 with PyYAML and pyexasol 2.x |
| Performance (measured, not gated) | `FETCH` of a demo `REFRESH_WEEK` should take under 30 s on AWS live (4 calls). A rebuild by restore should take under 5 minutes for the demo profile. Both are recorded in 9.6. `make test-it` grows by the loader tests: module-scoped deploys per plan variant keep it bounded |

## Dependencies

- **`add-cfbd-vs-foundation` (in flight).** This plan depends on:
  - Groups A–H (code, harness, integration tests written): implemented. Its integration tests must pass on Personal before this plan's group E.
  - Group I (quality gate, docs) before this plan's group I.
  - **P1: its code-review fix gives `RANKINGS_POLLS_RANKS` the parent keys `SEASON`, `WEEK` and `POLL` and regenerates the catalog** (decision [27]). This is needed before task 2.3; this plan does not edit `vs/catalog-overrides.yaml`.
  - **P2: its code-review fix `vs/host/target.py`** (`TARGET=local|aws`: DSN, password, AWS certificate pinning, wired into deploy, grants, dns and the Makefile). It is needed before tasks 1.3, 5.1 and 5.2; this plan builds on it and does not duplicate it.
  - Foundation [25]: the catalog hash covers the generated content. A restore therefore sees a hash difference after any regeneration, not only after a slice-key change. `FORCE=yes` plus the orphan report decides then.
  - **Its group J task 9.4 (S1 findings applied to the overrides, catalog final) before this plan's live group J.** Otherwise the stored data may not fit a later catalog, and mirror migration is out of scope.
  - Contracts used: decisions [5] (`LOAD_LOG`), [6] (fetch log), [18] (SET fetch with thread pool), [20] (sys owns the real deployment), [21] (empty-group guard and manifest `sources`), and `vs/host/fetchlog.py` as the reference capture.
- **`add-engine-spikes` (parallel).**
  - Shared files: it edits `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` (D19, D20). This plan edits them only in its group I, after that plan's task 4.1 has landed, and re-reads each file first.
  - It holds exclusive benchmark windows on local Personal and on AWS.
- **Tools.** exapump ≥ 0.13 (`~/.local/bin/exapump`, from the Exasol starter kit), pyexasol 2.4.1, PyYAML; `uv`.
- **User prerequisites before group J:**
  - U1: `CFBD_API` exists on AWS `default` (created by the owner with plain SQL, D18). It is verified via `EXA_DBA_CONNECTIONS` with `TARGET=aws`.
  - U2: `make grants TARGET=aws` after `make deploy TARGET=aws` (reader role only, [20]).
  - U3: `make dns-check TARGET=aws` passes (DoH fallback if needed, D21).
  - U4: the "AWS free" and "local free" signals from the parallel session.
  - U5: `CONFIRM=yes` for each live command.
  - U6: the 2026-10 authorization (≤ 400) still covers the run, or the owner adds a month entry.

## Migration

No data migration: the real namespaces hold no stored slices yet.
- `make deploy` adds the new `CFB_META` tables and views, the `CFB_STAGE` schema and the Lua scripts. Existing `LOAD_LOG` and `FETCH_LOG` keep their foundation DDL unchanged.
- `vs/host/metaddl.py` renders the new DDL with `{META_SCHEMA}`, the placeholder it renders today. Views and Lua use `{CORE_SCHEMA}`, `{STAGE_SCHEMA}`, `{INFRA_SCHEMA}` and `{VS_NAME}` through the same renderer.
- The foundation harness (`tests/it/harness.py`) changes:
  - It also deploys the loader with a test plan.
  - It drops `TEST_CFB_STAGE` and `TEST_CFB_RESTORE` with the namespace.
  - Its reset no longer truncates the configuration tables, and now truncates `CFB_STAGE` and `LOAD_FAILURES`.
- The mock API's `/info` gains a counting mode: used calls = base + data requests since the last reset.
- `TARGET` for the existing scripts comes from the foundation fix (P2). This plan adds it only to its own make targets and scripts.
- `.env.example` documents `CFB_SNAPSHOT_DIR`.

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first (`tests/unit/…`, `tests/cli/…`). The views are tested on the engine before any Lua exists (group E). The Lua tasks (4.2, 4.3) and the snapshot task (5.2) write their integration test files first, which fail, and then implement until they pass. Lua has no unit tier.

1. **Probes, targets and ledger (zero CFBD calls)**
   - [ ] 1.1 Loader platform probe `docs/spikes/s5_loader_probe.py`, with findings in `docs/spikes/s5-loader-probe.md`. Setup and rules:
     - Run as `sys` on local Personal in a "local free" window.
     - Use only `PROBE_*` objects, plus a `TEST_` namespace built with `tests.it.harness.build_harness()` and dropped afterwards. Zero CFBD calls.
     - The probe takes `--target aws`; 9.0 re-runs the read-only items there.
     - Each finding records the fallback the plan adopts if it is negative. **Gate:** (a), (b) or (c) negative → stop and escalate. [expert]
     - (a) `CREATE LUA SCRIPT … RETURNS TABLE` with `exit(rows, "<column defs>")`, called with `EXECUTE SCRIPT` from pyexasol; `WITH OUTPUT` discards the table. Whether `EXECUTE SCRIPT` accepts fewer arguments than declared (then `REFRESH_WEEK(2026, 9)` works too).
     - (b) Transactions and errors inside a script:
       - `query("COMMIT")` / `query("ROLLBACK")`;
       - a **failing `pquery` inside an open transaction**: whether the transaction can still be rolled back, logged and committed;
       - `pcall` around Lua errors;
       - committed work survives a later `error()`;
       - what happens to uncommitted work when a script ends in `error()`.

       Fallback: one batch per `EXECUTE SCRIPT` call, with `make backfill` looping. The loop stops on the first failed call and on any call without progress (m2).
     - (c) `SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL …)` through `query()`, and plain `query("EXPLAIN VIRTUAL …")`. Fallback: the latter; then the post-fetch `REQ_SLICE` check alone.
     - (d) In replay mode inside a script, `INSERT INTO <stage> SELECT * FROM <vs>.<t> WHERE … AND REQ_SOURCE <> 'STORED'` works. Check that the empty-SET-group guard ([21]) holds when every planned slice is final-stored.
     - (e) Nested `EXECUTE SCRIPT` via `query()`/`pquery()`: argument passing (`run_id`, numbers, strings, NULL), an inner `RETURNS ROWCOUNT`, and how an inner failure surfaces. Fallback: `import()` of a step module.
     - (f) Scripts run with the caller's privileges (a probe user without rights on the probe tables).
     - (g) Lua value types for `DECIMAL`, `DOUBLE`, `BOOLEAN`, `TIMESTAMP`, `VARCHAR` and NULL (`null`), in `query()` results and in script parameters (`EXECUTE SCRIPT s(NULL, 2018, 0)`).
     - (h) Rows committed by a running script are visible to another session before the script ends.
     - (i) Two sessions inserting the run-lock row at the same time: wait, collision error, or both succeed. The lock design in 4.2 follows the finding.
     - (j) `EXA_DBA_SESSIONS` / `EXA_ALL_SESSIONS`: live session ids, and the column that shows a session as idle (no running statement). Check it after `KILL STATEMENT` of a running script and after a client-side interrupt.
     - (k) `CREATE TABLE … (LIKE …)`, and `MERGE` into a table with a primary key whose `USING` source reads the target (`LOAD_SLICES` reads `LOAD_LOG`).
     - (l) The session time zone on Personal, and one UTC "now" expression (e.g. `CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')`) used by the views and the Lua alike.
   - [ ] 1.2 Snapshot format probe `docs/spikes/s5_snapshot_probe.py`, with findings in the same doc. Zero calls, local (and `--target aws` in 9.0), `PROBE_*` objects only.
     - Build a probe table with every mirror type:
       - `DECIMAL(18,0)`, `DOUBLE`, `BOOLEAN`, `TIMESTAMP`;
       - `VARCHAR(2000) UTF8` with umlauts and an emoji;
       - `VARCHAR(2000000) UTF8` holding a 1 MB JSON;
       - NULLs in every column.
     - Round trip: `exapump export --format parquet --compression zstd`, then `exapump upload` into a `(LIKE …)` copy. Compare `MINUS` both ways and the fingerprint (Key Interfaces).
     - Also record:
       - DSN through `EXAPUMP_DSN` in the child environment only, with `--certificate-fingerprint`;
       - exit codes;
       - behaviour on an existing non-empty table;
       - column matching by name or position;
       - the export of an empty table.
     - Decide: Parquet via exapump (default), or the fallback gzip CSV via pyexasol `export_to_file` / `import_from_file`. Record the decision in the decision log.
   - [ ] 1.3 Loader additions to the foundation's targets (P2, `vs/host/target.py`; no second target module):
     - `loader/exapump.py`: `exapump_env(target) -> (env, args)`. `EXAPUMP_DSN` is set in the child's environment only, `--certificate-fingerprint` comes from the target's pinned certificate, and no password appears in argv, files or logs. For a loopback target the server certificate fingerprint is computed at run time.
     - The new make targets (`backfill`, `refresh`, `quota`, `snapshot`, `restore`, `snapshots`) take `TARGET` and connect through the foundation module.
     - Unit tests `tests/unit/test_loader_exapump.py` with a fake target: no secret in argv, the fingerprint is passed, and the environment is not inherited by later processes.
     - verify (in the 9.0 smoke test): exapump's native transport stays on the pinned node of the 3-node AWS cluster, or the pinning covers every node it opens.
   - [ ] 1.4 Ledger `vs/host/ledger.py` + `vs/live/ledger.yaml`.
     - Append-only `cache/live-calls.jsonl` with a `plan` field; totals per plan.
     - `check(plan, month, estimate, used_calls)` refuses when:
       - the plan total plus the estimate would exceed the plan cap;
       - the `/info` used calls plus the estimate would exceed the month cap;
       - the month has no cap.

       `LEDGER=off` disables it.
     - `--summary` prints the totals.
     - Unit tests.
     - If the foundation's `scripts/record.py` (9.1) already exists, switch it to this module; otherwise its 9.1 uses it.

2. **Load plan (host, unit-tested)**
   - [ ] 2.1 `loader/plan.py`: YAML schema, dataclasses and loader. The validator checks the plan against `vs/catalog/catalog.json` and lists every error, naming entry and rule. It rejects:
     - an unknown table, or a table without a mirror (volatile);
     - a gated table in `demo`;
     - unknown profile, slicing (`season_week` is backlog) or refresh class;
     - `none` or `season_range` that is not `static`;
     - a table without the `year`, `minYear` or `maxYear` parameter its slicing needs;
     - unknown fixed parameters, or values that break their type or enum;
     - seasons out of order or before 1869, or `season_range` without `to`;
     - gaps outside the entry's seasons;
     - overlapping seasons between entries of the same table with equal fixed parameters;
     - invalid or duplicate `refresh_steps` (number ≥ 100, qualified script name);
     - invalid config values.

     It also computes a plan hash (SHA-256 of the normalized plan). Unit tests `tests/unit/test_load_plan.py`.
   - [ ] 2.2 `loader/templates.py`: compile each entry with the adapter's own code: `vs.cfbd_vs.rules` for explicit defaults and rules, `vs.cfbd_vs.plan` for keys.
     - Plan the request for sentinel season and range values. Require exactly one planned request that passes every rule.
     - Replace the sentinels with `{season}`, `{season_from}` and `{season_to}` to get `SLICE_TEMPLATE`.
     - Render `FIXED_FILTER` with `vs.cfbd_vs.sqlrender`.
     - Unit tests `tests/unit/test_load_plan_templates.py`:
       - for every entry of `loader/load-plan.yaml` and sample seasons, the template equals `build_slice_key`;
       - a `PLAYS` entry with season slicing fails, naming the rule id.
   - [ ] 2.3 Author `loader/load-plan.yaml`:
     - config, snapshot scope, empty `refresh_steps`;
     - the demo entries of Key Interfaces, with gaps from `docs/cfbd-data-availability.md`.

     Add `uv run python -m loader.plan --check [--estimate]`, which prints the errors, or the per-profile slice and call counts for today's current season. The demo estimate is 217.

     **Gate P1:** the catalog's `RANKINGS_POLLS_RANKS` has the parent-key columns `SEASON`, `WEEK` and `POLL`. Otherwise stop and report to the foundation.

3. **CFB_META objects and loader deploy**
   - [ ] 3.1 Meta DDL `sql/meta/{load_plan,load_plan_gaps,loader_config,season_numbers,refresh_steps,load_run,refresh_log,load_failures}.sql` per Key Interfaces.
     - Register them in `vs/host/metaddl.py` (`{META_SCHEMA}`), and extend the renderer to the other placeholders.
     - `Namespace` gains `stage_schema` and `restore_schema`.
     - Unit tests for rendering and names.
   - [ ] 3.2 Views `sql/loader/{load_slices,backfill_queue,load_status,quota}.sql`. [expert] They implement:
     - UTC now with `NOW_OVERRIDE` (per 1.1l), and the current season from `SEASON_ROLLOVER`;
     - season numbers, NULL seasons for `none` and `season_range`, substituted slice keys, gaps;
     - gating through the virtual schema's `API_FEATURES`;
     - finality per refresh class;
     - the `LOAD_LOG` state, failures and quarantine (`QUARANTINE_AFTER`), and orphans;
     - queue order with failed slices last, `BATCH_ALONE`, calls to complete;
     - the quota arithmetic and attribution per period (`FETCH_LOG` `REQ_SOURCE = 'LIVE'`, context prefixes).

     Unit tests check the rendering. Behaviour is tested in group E (7.1, 7.2), before any Lua.
   - [ ] 3.3 Loader deploy `loader/deploy.py`, called by `scripts/deploy.py` after the VS deploy and by the IT harness. Steps:
     1. Create the stage schema, and the stage tables from the mirror DDL for every planned table. Existing tables must match exactly, as for mirrors.
     2. Create the meta tables.
     3. Validate and compile the plan. Apply `LOADER_CONFIG="k=v,…"` overrides (validated like the YAML), then replace `LOAD_PLAN`, `LOAD_PLAN_GAPS`, `LOADER_CONFIG` and `REFRESH_STEPS` in one transaction. An invalid plan or override changes nothing.
     4. Fill `SEASON_NUMBERS`.
     5. `CREATE OR REPLACE VIEW` for the views.
     6. Install the Lua scripts from `build/sql/loader_*.sql` (4.1; skipped with a notice until they exist).
     7. Report orphan slices per table as a warning.

     Rules:
     - It never touches the data of `LOAD_LOG`, `FETCH_LOG`, `LOAD_RUN`, `REFRESH_LOG`, `LOAD_FAILURES` or `CFB_CORE`.
     - It removes `NOW_OVERRIDE` outside `TEST_` namespaces.
     - It reports lines like the VS deploy.
     - Unit tests use a fake connection (`tests/unit/test_loader_deploy.py`).

4. **Lua scripts**
   - [ ] 4.1 Bundler `loader/bundle.py` (no Lua logic):
     - It renders `CREATE OR REPLACE LUA SCRIPT {INFRA_SCHEMA}.BACKFILL(profile, from_season, max_calls) RETURNS TABLE AS …` and `{INFRA_SCHEMA}.REFRESH_WEEK(season, week, season_type)` from `loader/lua/loadlib.lua` + the entry file.
     - Namespace names become double-quoted Lua string constants.
     - Unit tests `tests/unit/test_lua_bundle.py`, using stub Lua files:
       - deterministic output with no unresolved placeholders;
       - quoted identifiers;
       - size below the 2,000,000-character cap;
       - no `ALTER VIRTUAL SCHEMA` and no bare `query(` in any loader source, only `pquery`.
   - [ ] 4.2 `loader/lua/loadlib.lua` (owned by this task) and `loader/lua/backfill.lua`, test-first with `tests/it/test_backfill_it.py` and the lock and failure tests of `tests/it/test_run_logs_it.py` (failed run, concurrent run, abandoned and stale-session runs). [expert]
     - Library:
       - the lock with the stale-row rules of `loader/run-logs` (per 1.1i, 1.1j);
       - config and virtual schema properties;
       - the quota read in its own transaction;
       - `log_step`, `next_batch` (one `ENTRY_ID`, `BATCH_ALONE`), `explain_check`;
       - `load_batch`: the batch transaction of Key Interfaces with `pquery` throughout, the stage-empty assertion and the staged-key check;
       - the per-slice fallback, failure recording and systemic stop;
       - measured spend from the `/info` delta;
       - `fail`.
     - Backfill:
       - arguments, gap registration (`GAPS` step), the allowance;
       - the batch loop over `BACKFILL_QUEUE`, the dry run (`max_calls = 0`: `EXPLAIN` of every pending batch);
       - blocked tables (orphans), stop reasons and the result table with `CALLS_NEEDED` and `FAILED_SLICES`.
   - [ ] 4.3 `loader/lua/refresh_week.lua`, after 4.2, test-first with `tests/it/test_refresh_week_it.py`. [expert]
     - `CHECK`: arguments including `season_type`, calendar `(SEASON, WEEK, SEASON_TYPE)`, lock, orphans, `REFRESH_SPENDABLE`.
     - `FETCH <table>`: one step per table (steps 2–99) via `load_batch`, with no per-slice fallback.
     - Registered steps (≥ 100) via nested `EXECUTE SCRIPT <script>(run_id, season, week, season_type)`, each with step logging.
     - The failure semantics and the result timeline.
     - Library changes go through 4.2's owner: no parallel edits of `loadlib.lua`.

5. **Host wrappers and snapshots**
   - [ ] 5.1 `scripts/loader_cli.py` with the subcommands `backfill`, `refresh` and `quota`, and the make targets `backfill`, `refresh` and `quota`. The flow:
     1. Connect to the target.
     2. Print the estimate (`BACKFILL_QUEUE`, or the weekly candidates of the season) and `QUOTA`.
     3. In live mode, require `CONFIRM=yes` and pass `ledger.check` with the estimate and `QUOTA.USED_CALLS`.
     4. Run `EXECUTE SCRIPT …`, with `MAX_CALLS` defaulting to the ledger room.
     5. Print the result, append the run's measured calls (`LOAD_RUN.CALLS`, used calls before and after) to the ledger, and exit non-zero on failure.

     CLI tests `tests/cli/test_loader_cli.py` use a fake connection and no DB.
   - [ ] 5.2 Snapshot and restore `loader/snapshot.py` + `scripts/snapshot.py`, with the make targets `snapshot`, `restore` (`CONFIRM=yes`, `FORCE=yes`) and `snapshots`. Test-first with `tests/it/test_snapshot_restore_it.py` and `tests/cli/test_snapshot_cli.py`. [expert] It covers:
     - path rules (repo, OneDrive, existing name, modes);
     - the manifest, and a pure, unit-tested fingerprint SQL generator;
     - the exporter and importer per the 1.2 decision;
     - the lock rows in `LOAD_RUN` (host sessions, stale only when gone), and a `LOAD_LOG` stability check around the export;
     - the column and catalog-hash checks;
     - the landing schema, fingerprint check, one-transaction swap of `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`, and the `RESTORE` run row;
     - cleanup on failure.

     The rebuild test re-runs the harness's grant step after redeploying the fetch UDF (foundation probe (o)). Unit tests `tests/unit/test_snapshot.py`.

6. **Integration test harness (zero CFBD calls)**
   - [ ] 6.1 Extend `tests/it/harness.py`:
     - Deploy the loader into the `TEST_` namespace with plan variants from `tests/it/fixtures/load-plan.*.yaml`: the entries the specs use, with priorities, classifications and gaps per variant. Deploys are module-scoped per variant.
     - `drop_namespace` and `leftovers` cover the stage and restore schemas.
     - `reset_state` keeps the configuration tables (and redeploys the base plan if a test switched variants), and truncates `CFB_STAGE`, `LOAD_RUN`, `REFRESH_LOG` and `LOAD_FAILURES`.
     - New helpers: `set_now`, `deploy_plan(variant, loader_config=None)`, `backfill(...)`, `refresh(...)`, `quota()`, `runs()`, `steps(run_id)`, `failures()`, `script_info(...)` (static or counting `/info`), `kill_statement(session)`, `snapshot_dir` (pytest tmp path) and `exapump_env()` (test owner DSN in memory).
     - The guard against `CFBD_API` stays.
   - [ ] 6.2 Synthetic data, mock and test scripts:
     - `tests/synthetic/fake.py` gains a hand-tuned `/calendar` (regular weeks 1–15 from the first Saturday of September, postseason week 1 in January, deterministic per year) and a default `/info` body.
     - The mock's `/info` counting mode (Migration).
     - A "changed upstream" `GAMES` body.
     - Registered-step test scripts, created by the harness: `TEST_STEP_RECORD`, `TEST_STEP_FAIL` and `TEST_STEP_SLEEP` (a Python UDF sleep called from Lua).

7. **Integration tests per feature** (mapping in Verification)
   - [ ] 7.1 `tests/it/test_load_plan_it.py` and `tests/cli/test_load_plan_cli.py`: the views and the deploy, before any Lua. The fetch-blocking part of the orphan scenario is in 4.2 and 4.3.
   - [ ] 7.2 `tests/it/test_quota_it.py`: the views only, before any Lua.
   - [ ] 7.3 The rest of `tests/it/test_run_logs_it.py`: every run recorded, fetched slices logged, reference capture, load log matches rows. Needs 4.3.
   - [ ] 7.4 Quality gate:
     - `make lint` is clean (`mypy` `files` += `loader`, coverage `source` += `loader`);
     - `make coverage` reaches ≥ 90 % line coverage for `loader` from unit and CLI tests;
     - `make test-it` is green, including the foundation's integration tests.

8. **Documentation** (after `add-engine-spikes` has written D19/D20. Re-read each file first, and only append to decision tables)
   - [ ] 8.1 `docs/user-guide.md` § "Persisting and refreshing":
     - `make deploy` includes the loader, and `LOADER_CONFIG` overrides;
     - `make backfill` (estimate, `CONFIRM`, profiles, `FROM`, `MAX_CALLS`, dry run);
     - `EXECUTE SCRIPT CFB_INFRA.BACKFILL(…)` and `REFRESH_WEEK(…, 'regular' | 'postseason')` with sample output, and "never `WITH OUTPUT`";
     - editing the load plan and the budget; `CFB_META.QUOTA`;
     - failures and quarantine (clearing `LOAD_FAILURES`);
     - orphans (migrate or delete);
     - re-fetching a slice: `UPDATE CFB_META.LOAD_LOG SET IS_FINAL = FALSE …`, then refresh or backfill, e.g. a current-season calendar that lacks a bowl week (m9);
     - pages and the chat read current-season data from `CFB_CORE`, not `CFBD_VS` (m1);
     - snapshots: location, privacy, scope, `FORCE`, the restore as rehearsal reset;
     - `TARGET=aws`, and `LEDGER=off` for other keys.
   - [ ] 8.2 `specs/architecture.md`:
     - Loader component:
       - Lua scripts with `pquery`, the stage schema and per-entry batch transaction;
       - `REQ_SOURCE <> 'STORED'` instead of property flips;
       - the per-slice fallback and quarantine;
       - the orphan guard;
       - the step registry with `season_type`.

       Remove the `PROMOTE()` bullet and mark it deferred.
     - Schema table: `CFB_STAGE`, the transient `CFB_RESTORE`, the new `CFB_META` objects.
     - Snapshots: exapump Parquet, scope, no S3.
     - Targets and TLS pinning; the Phase 6 loader-role note.
     - The current-season read rule (m1).
     - Open question: mirror migration on a catalog change.
   - [ ] 8.3 `docs/roadmap.md`:
     - Decisions from the decision-log entries marked `Promotes to ADR: yes`, numbered after the last D-number present (after the foundation's D22+ block).
     - Phase 2 bullets: drop "Dev container data on a named Docker volume" (D17) and "private S3".
     - Demo flow beat 3: `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, 'regular')`.
     - API budget table: demo backfill 217 calls, demo refresh 4.
     - Backlog: `season_week` slicing, the `full` entries, `PROMOTE()`.
     - Mark Phase 2 progress.
   - [ ] 8.4 `CLAUDE.md` dense bullets:
     - `TARGET`;
     - both budget guards and `LEDGER=off`;
     - the loader test tier and the test-only `NOW_OVERRIDE`;
     - Lua scripts use `pquery` and run with the caller's rights;
     - the loader never switches `READ_THROUGH`;
     - snapshots stay outside the repo and OneDrive;
     - `make deploy` after editing the load plan.

     `specs/mission.md`:
     - § Commands: `make backfill PROFILE=…`, `make refresh SEASON= WEEK= [SEASON_TYPE=]`, `make quota`, `make snapshot|restore|snapshots`;
     - § Tech Stack: orchestration = Lua scripting programs;
     - the demo's `EXECUTE SCRIPT` wording;
     - § Constraints: "about 200 … about 5" becomes "217 … 4" (m15).

9. **Live (last; ≤ 300 CFBD calls; AWS `default`; needs U1–U6)**
   - [ ] 9.0 Gates and AWS smoke (0 CFBD calls):
     - The foundation's 9.4 and P1 are done, and the "AWS free" signal is given.
     - `make deploy TARGET=aws && make grants TARGET=aws` (decision [46]: `vs/live/loader-config.yaml` applies the ledger's cap for the deploy's month and a reserve of 50; check the printed `loader config for target aws` line). U1 is verified, and `make dns-check TARGET=aws` passes.
     - `make quota TARGET=aws`: tier, used calls. Assert that `BACKFILL_SPENDABLE` is at most this plan's ledger room, and that the ledger room is at least the estimate + 4.
     - **Smoke**, which 9.1 is gated on:
       1. record `SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'`;
       2. re-run probes 1.1 (a, b, c, g, h, i, j, l: read-only and lock items on `PROBE_*` objects) and 1.2 with `--target aws`, covering exapump and pyexasol certificate pinning on all three nodes;
       3. `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, 0)`, a dry run that registers the 1871 gap, runs `EXPLAIN` on every pending batch, and must show unchanged `/info` used calls;
       4. a concurrent-lock check from two sessions.
   - [ ] 9.1 Estimate (0 calls): `make backfill PROFILE=demo TARGET=aws` without `CONFIRM` prints the estimate (expected 217) and stops. If the estimate is more than 10 % above 217 (assumption A4 failed), stop and report; both guards still hold.
   - [ ] 9.2 Resumable live backfill:
     1. `make backfill PROFILE=demo TARGET=aws MAX_CALLS=20 CONFIRM=yes` → `STOPPED_MAX_CALLS`, about 20 measured calls.
     2. `make backfill PROFILE=demo TARGET=aws CONFIRM=yes` → `DONE`.

     The measured calls equal the `/info` delta and the ledger entries. Any `DONE_WITH_FAILURES` is reported with the quarantined slices.
   - [ ] 9.3 Idempotence: run it again → 0 calls, `/info` unchanged, `LOAD_STATUS` complete for `demo` with no orphans.
   - [ ] 9.4 Live refresh: `make refresh SEASON=2026 WEEK=<latest completed regular week in CALENDAR> TARGET=aws CONFIRM=yes` → 4 calls; it may use the reserve. Print the timeline and record the `FETCH` seconds per table.
   - [ ] 9.5 Snapshot AWS → restore local (0 calls):
     1. `make snapshot TARGET=aws NAME=aws-demo-<date>`.
     2. In a "local free" window: `make deploy TARGET=local`, then `make restore TARGET=local NAME=… CONFIRM=yes`.

     Check:
     - the catalog hashes and fingerprints match, and `/info` is unchanged on both targets;
     - `SELECT REQ_SOURCE, COUNT(*) FROM CFBD_VS.GAMES WHERE P_YEAR = 2019 GROUP BY REQ_SOURCE` on local gives one `STORED` group;
     - the restore time.
   - [ ] 9.6 Live documentation and reset:
     - No budget reset is needed (decision [46]): the AWS budget follows the ledger's month cap at every deploy. Deploy AWS once in November to pick up its cap.
     - `docs/cfbd-api-measurements.md`, derived numbers only: calls per entry, rows per table, seconds per batch, the refresh timeline, restore time, the AWS DB version.
     - The roadmap budget table.
     - The final ledger total (≤ 300).

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 2.1, 3.1, 6.2 |
| Group B | 2.2, 3.2, 4.1 |
| Group C | 2.3, 3.3 |
| Group D | 5.1, 6.1 |
| Group E | 7.1, 7.2 |
| Group F | 4.2 |
| Group G | 4.3, 5.2 |
| Group H (sequential) | 7.3 → 7.4 |
| Group I | 8.1, 8.2, 8.3, 8.4 |
| Group J (sequential, gated) | 9.0 → 9.1 → 9.2 → 9.3 → 9.4 → 9.5 → 9.6 |

Sequential dependencies:
- Group A → B → C → D → E → F → G → H → I → J.
- 1.1 and 1.2 need a "local free" window. 3.2 needs the 1.1 findings (l, j). 4.2 needs 1.1. 5.2 needs the 1.2 decision and 1.3.
- 2.2 needs 2.1. 2.3 needs 2.1, 2.2 and P1.
- 3.2 needs 3.1. 3.3 needs 2.2, 3.1, 3.2 and 4.1.
- 5.1 needs 1.3, 1.4 and 3.3. 6.1 needs 3.3 and 6.2.
- Group E (view tests) needs 6.1 and the foundation's integration tests passing on Personal. It runs before any Lua.
- 4.2 needs group E. 4.3 needs 4.2 (single owner of `loadlib.lua`). 5.2 needs the lock from 4.2.
- 7.3 needs 4.3.
- Group I needs `add-engine-spikes` 4.1 to have landed and the foundation's group I.
- Group J needs green groups A–I, the foundation's 9.4, and U1–U6.

## Dead Code Removal

None. `tests/it/harness.py` `stage_slices` stays: the foundation's read-through tests use it, and it remains the minimal load pattern for tests that don't need the loader.

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| load-plan: The deployed plan lists every slice the loader may fetch | Integration | `tests/it/test_load_plan_it.py` | `test_deployed_plan_lists_every_slice` |
| load-plan: Planned slice keys match the virtual schema's requests | Integration | `tests/it/test_load_plan_it.py` | `test_project_plan_keys_match_explain` |
| load-plan: An invalid plan stops the deploy | Integration (CLI + DB) | `tests/cli/test_load_plan_cli.py`, `tests/it/test_load_plan_it.py` | `test_invalid_plan_lists_every_error`, `test_invalid_plan_keeps_deployed_plan` |
| load-plan: A new season enters the plan on the rollover date | Integration | `tests/it/test_load_plan_it.py` | `test_new_season_on_rollover_date` |
| load-plan: Finality follows the refresh class | Integration | `tests/it/test_load_plan_it.py` | `test_finality_follows_refresh_class` |
| load-plan: Range and seasonless slices have no season | Integration | `tests/it/test_load_plan_it.py` | `test_range_and_seasonless_slices_have_no_season` |
| load-plan: The demo profile is part of the full profile and comes first | Integration | `tests/it/test_load_plan_it.py` | `test_demo_part_of_full_and_first` |
| load-plan: Gated entries wait for their feature | Integration | `tests/it/test_load_plan_it.py` | `test_gated_entries_wait_for_feature` |
| load-plan: The plan shows what completing it costs | Integration | `tests/it/test_load_plan_it.py` | `test_load_status_shows_cost` |
| load-plan: Orphan slices are reported and block fetching their table | Integration | `tests/it/test_load_plan_it.py`, `tests/it/test_backfill_it.py`, `tests/it/test_refresh_week_it.py` | `test_orphan_slices_reported`, `test_orphans_block_backfill`, `test_orphans_block_refresh` |
| backfill: A backfill loads by priority, newest season first | Integration | `tests/it/test_backfill_it.py` | `test_priority_then_newest_season_with_max_calls` |
| backfill: Entries of equal priority are loaded season by season | Integration | `tests/it/test_backfill_it.py` | `test_equal_priority_interleaves_seasons` |
| backfill: Slices without a season come first and are always included | Integration | `tests/it/test_backfill_it.py` | `test_seasonless_slices_first_and_always_included` |
| backfill: A batch never mixes plan entries | Integration | `tests/it/test_backfill_it.py` | `test_batch_never_mixes_entries` |
| backfill: A second run fetches nothing that is already stored | Integration | `tests/it/test_backfill_it.py` | `test_second_run_fetches_nothing` |
| backfill: Stored slices equal what the virtual table returns | Integration | `tests/it/test_backfill_it.py` | `test_stored_slices_equal_virtual_table` |
| backfill: Known gaps are registered without a request | Integration | `tests/it/test_backfill_it.py` | `test_gaps_registered_without_request` |
| backfill: A non-final slice is left to the weekly refresh | Integration | `tests/it/test_backfill_it.py` | `test_non_final_slice_left_to_refresh` |
| backfill: A slice that became final is fetched once more | Integration | `tests/it/test_backfill_it.py` | `test_finalizable_slice_fetched_once` |
| backfill: The backfill keeps the refresh reserve | Integration | `tests/it/test_backfill_it.py` | `test_backfill_keeps_refresh_reserve` |
| backfill: The run counts measured calls, not slices | Integration | `tests/it/test_backfill_it.py` | `test_run_counts_measured_calls` |
| backfill: A dry run checks the plan and fetches nothing | Integration | `tests/it/test_backfill_it.py` | `test_dry_run_checks_and_fetches_nothing` |
| backfill: The backfill can start at an older season | Integration | `tests/it/test_backfill_it.py` | `test_from_season` |
| backfill: A failing slice is isolated and does not cost its batch | Integration | `tests/it/test_backfill_it.py` | `test_failing_slice_isolated` |
| backfill: A slice that keeps failing is quarantined without blocking the queue | Integration | `tests/it/test_backfill_it.py` | `test_failing_slice_quarantined` |
| backfill: One failed single-slice batch does not stop the run | Integration | `tests/it/test_backfill_it.py`, `tests/unit/test_backfill_lua.py` | `test_one_failed_single_slice_batch_does_not_stop_the_run`, `test_one_failed_single_slice_batch_is_recorded_and_the_run_continues` |
| backfill: Two consecutive failed batches stop the run | Integration | `tests/it/test_backfill_it.py`, `tests/unit/test_backfill_lua.py` | `test_systemic_failure_stops_run`, `test_an_outage_stops_after_two_batches_and_their_single_slice_retries` |
| backfill: The next run resumes after a failure | Integration | `tests/it/test_backfill_it.py` | `test_next_run_resumes_after_failure` |
| backfill: A plan that disagrees with the virtual schema stops before any request | Integration | `tests/it/test_backfill_it.py` | `test_slice_key_drift_stops_before_request` |
| backfill: The backfill leaves the virtual schema properties unchanged | Integration | `tests/it/test_backfill_it.py` | `test_properties_unchanged_after_runs` |
| backfill: No batch exceeds the per-query call budget | Integration | `tests/it/test_backfill_it.py` | `test_batches_respect_query_budget` |
| refresh-week: One call refreshes the season and returns the timeline | Integration | `tests/it/test_refresh_week_it.py` | `test_refresh_week_returns_timeline` |
| refresh-week: Refreshed rows replace the previous rows of a slice | Integration | `tests/it/test_refresh_week_it.py` | `test_refreshed_rows_replace_previous` |
| refresh-week: A refresh after the season's final date makes the season final | Integration | `tests/it/test_refresh_week_it.py` | `test_refresh_after_final_date_finalizes` |
| refresh-week: Postseason weeks are addressed by season type | Integration | `tests/it/test_refresh_week_it.py` | `test_postseason_weeks_by_season_type` |
| refresh-week: Refreshing a completed season fetches nothing | Integration | `tests/it/test_refresh_week_it.py` | `test_completed_season_fetches_nothing` |
| refresh-week: Static entries are not refreshed | Integration | `tests/it/test_refresh_week_it.py` | `test_static_entries_not_refreshed` |
| refresh-week: Invalid arguments are rejected before any request | Integration | `tests/it/test_refresh_week_it.py` | `test_invalid_arguments_rejected` |
| refresh-week: A refresh that cannot afford its calls fails before any request | Integration | `tests/it/test_refresh_week_it.py` | `test_unaffordable_refresh_fails_before_request` |
| refresh-week: The refresh may use the reserve that backfills keep | Integration | `tests/it/test_refresh_week_it.py` | `test_refresh_may_use_reserve` |
| refresh-week: A failed fetch keeps the previous data and records the failed step | Integration | `tests/it/test_refresh_week_it.py` | `test_failed_fetch_keeps_previous_data` |
| refresh-week: Registered steps run after the fetch, in order | Integration | `tests/it/test_refresh_week_it.py` | `test_registered_steps_run_in_order` |
| refresh-week: A failing step stops the chain | Integration | `tests/it/test_refresh_week_it.py` | `test_failing_step_stops_chain` |
| refresh-week: Other sessions see the progress while the refresh runs | Integration | `tests/it/test_refresh_week_it.py` | `test_progress_visible_to_other_sessions` |
| refresh-week: A replay-mode refresh costs zero calls | Integration | `tests/it/test_refresh_week_it.py` | `test_replay_refresh_zero_calls` |
| run-logs: Every run is recorded with its outcome | Integration | `tests/it/test_run_logs_it.py` | `test_every_run_recorded` |
| run-logs: Every fetched slice is logged with its run | Integration | `tests/it/test_run_logs_it.py` | `test_fetched_slices_logged_with_run` |
| run-logs: The loader's fetch log equals the reference capture | Integration | `tests/it/test_run_logs_it.py` | `test_fetch_log_equals_reference_capture` |
| run-logs: The load log matches the stored rows | Integration | `tests/it/test_run_logs_it.py` | `test_load_log_matches_stored_rows` |
| run-logs: A failed run is recorded and registers nothing from the failed batch | Integration | `tests/it/test_run_logs_it.py` | `test_failed_run_recorded` |
| run-logs: A second run is refused while one is running | Integration | `tests/it/test_run_logs_it.py` | `test_concurrent_run_refused` |
| run-logs: Another session cannot end a running run | Integration | `tests/it/test_run_lock_it.py` | `test_another_session_cannot_end_a_running_run` |
| run-logs: A deploy is refused while a run holds the lock | Integration | `tests/it/test_run_lock_it.py`, `tests/unit/test_deploy_cli.py` | `test_a_deploy_is_refused_while_a_run_holds_the_lock`, `test_a_deploy_during_a_loader_run_is_refused_and_changes_nothing` |
| run-logs: A run whose session ended does not block the next run | Integration | `tests/it/test_run_logs_it.py` | `test_abandoned_run_does_not_block` |
| run-logs: After a failed or interrupted run, the next run starts | Integration | `tests/it/test_run_logs_it.py` | `test_failed_or_interrupted_run_does_not_block` |
| quota: The quota view shows the key's quota without spending a call | Integration | `tests/it/test_quota_it.py` | `test_quota_view_zero_calls` |
| quota: The backfill allowance keeps the reserve | Integration | `tests/it/test_quota_it.py` | `test_backfill_allowance_keeps_reserve` |
| quota: The project budget caps a larger key quota | Integration | `tests/it/test_quota_it.py` | `test_project_budget_caps_key_quota` |
| quota: A deploy-time override caps one deployment and is undone by the next deploy | Integration | `tests/it/test_quota_it.py` | `test_deploy_override_caps_deployment` |
| quota: A plain deploy keeps a target's budget, following the ledger's month cap | Unit (CLI, fake DB) | `tests/unit/test_deploy_cli.py` | `test_a_plain_aws_deploy_keeps_the_target_budget_of_the_month`, `test_in_november_the_aws_budget_follows_the_ledger_cap`, `test_an_explicit_loader_config_wins_and_is_printed` |
| quota: Calls are attributed to backfill, refresh and other use | Integration | `tests/it/test_quota_it.py` | `test_calls_attributed_by_run_kind` |
| quota: The view shows what completing each profile costs | Integration | `tests/it/test_quota_it.py` | `test_calls_to_complete_profiles` |
| quota: A budget change in the plan takes effect after deploy | Integration | `tests/it/test_quota_it.py` | `test_budget_change_after_deploy` |
| snapshot-restore: A snapshot writes a private, self-describing copy | Integration | `tests/it/test_snapshot_restore_it.py` | `test_snapshot_private_self_describing` |
| snapshot-restore: Snapshots never land in the repository or a synced folder | Integration (CLI) | `tests/cli/test_snapshot_cli.py` | `test_snapshot_refuses_repo_and_onedrive` |
| snapshot-restore: A snapshot is never overwritten | Integration (CLI) | `tests/cli/test_snapshot_cli.py` | `test_snapshot_never_overwritten` |
| snapshot-restore: A rebuilt deployment is restored exactly, with zero calls | Integration | `tests/it/test_snapshot_restore_it.py` | `test_rebuilt_deployment_restored_exactly` |
| snapshot-restore: A restore resets newer data to the snapshot | Integration | `tests/it/test_snapshot_restore_it.py` | `test_restore_resets_newer_data` |
| snapshot-restore: A restore into a deployment with different columns is refused | Integration | `tests/it/test_snapshot_restore_it.py` | `test_restore_refuses_column_mismatch` |
| snapshot-restore: A snapshot from another catalog needs FORCE | Integration | `tests/it/test_snapshot_restore_it.py` | `test_restore_needs_force_for_other_catalog` |
| snapshot-restore: A failed restore changes nothing | Integration | `tests/it/test_snapshot_restore_it.py` | `test_failed_restore_changes_nothing` |
| snapshot-restore: A snapshot without the load log is refused | Integration | `tests/it/test_snapshot_restore_it.py`, `tests/unit/test_snapshot_flow.py` | `test_restore_refuses_a_manifest_without_load_log`, `test_a_manifest_without_load_log_is_refused_before_any_change` |
| snapshot-restore: Snapshot and restore are refused while a loader run is active | Integration | `tests/it/test_snapshot_restore_it.py` | `test_snapshot_and_restore_refused_during_run` |

Supporting unit tests (pure computation, no I/O) back the integration tests. They do not replace them:
- load plan: `tests/unit/test_load_plan.py`, `test_load_plan_templates.py`;
- loader SQL and deploy: `test_loader_sql.py` (view and meta rendering), `test_loader_deploy.py`;
- Lua: `test_lua_bundle.py`;
- snapshots: `test_snapshot.py` (paths, manifest, fingerprint SQL, swap order);
- infrastructure: `test_loader_exapump.py`, `test_ledger.py` (the target module's own tests belong to the foundation fix);
- CLI: `tests/cli/test_loader_cli.py` (estimate, `CONFIRM`, both ledger refusals).

### Manual Testing

All commands run from the repo root. Rows without a target run against Exasol Personal local (`TARGET=local`) after `make deploy`, in replay mode with recordings present, at 0 calls. Rows marked *(live, counted)* spend ledger calls on AWS.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| loader/load-plan | `uv run python -m loader.plan --check --estimate`, then `SELECT TABLE_NAME, PROFILE_NAME, SLICES_PLANNED, SLICES_MISSING, QUARANTINED_SLICES, ORPHAN_SLICES, CALLS_TO_COMPLETE, BLOCKED_BY FROM CFB_META.LOAD_STATUS ORDER BY 1` | "plan ok", demo 217 calls; one row per entry, no orphans |
| loader/backfill | `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, 0)`, then *(live, counted)* `make backfill PROFILE=demo TARGET=aws MAX_CALLS=20 CONFIRM=yes` | First: a dry run with `CALLS_NEEDED` 217 and 0 calls. Then the estimate, quota and ledger lines, a result table with about 20 slices and measured calls, status `STOPPED_MAX_CALLS` |
| loader/refresh-week | *(live, counted)* `make refresh SEASON=2026 WEEK=<w> TARGET=aws CONFIRM=yes`; `SELECT TABLE_NAME, IS_FINAL, LOADED_AT FROM CFB_META.LOAD_LOG WHERE SLICE_KEY LIKE '%year=2026%'` | Timeline `CHECK`, then `FETCH GAMES`, `FETCH METRICS_WP_PREGAME`, `FETCH RATINGS_SP`, `FETCH RANKINGS_POLLS_RANKS` with seconds and 1 call each; the 2026 slices non-final with a fresh `LOADED_AT` |
| loader/run-logs | `SELECT RUN_ID, RUN_KIND, RUN_STATUS, SLICES, CALLS FROM CFB_META.LOAD_RUN ORDER BY RUN_ID DESC LIMIT 5`; `SELECT STEP_NAME, STEP_STATUS, SLICES FROM CFB_META.REFRESH_LOG WHERE RUN_ID = <id>`; `SELECT * FROM CFB_META.LOAD_FAILURES` | The runs with their status and totals; per-batch or per-step rows; failures, if any, with their errors |
| loader/quota | `make quota TARGET=aws` | Tier, limit, used, remaining, reset date, project budget (400 during the October run), both spendable amounts, calls per run kind, calls to complete `demo` and `full`; `/info` used calls unchanged before and after |
| loader/snapshot-restore | `make snapshot TARGET=aws NAME=aws-demo-1`; `ls -la ~/.cfb-demo/snapshots/aws-demo-1`; `make restore TARGET=local NAME=aws-demo-1 CONFIRM=yes`; `SELECT REQ_SOURCE, COUNT(*) FROM CFBD_VS.GAMES WHERE P_YEAR = 2019 GROUP BY REQ_SOURCE` | Parquet files for `CFB_CORE`, `LOAD_LOG`, `FETCH_LOG` + `manifest.json` with mode 0600 in a 0700 directory; restore prints "catalog ok, fingerprints ok, 0 CFBD calls"; one group `STORED` |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0; the catalog and mirror DDL are current |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle` | Exit 0; `build/sql/` holds the adapter, fetch and loader scripts |
| Load plan | `uv run python -m loader.plan --check --estimate` | Exit 0; demo 217 calls |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`vs`, `loader`) |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % line coverage for `vs/cfbd_vs`, `vs/gen` and `loader` |
| Deploy | `make deploy && make deploy TARGET=aws` | Exit 0 both; a second run reports the loader objects unchanged and no orphans |
| Quota | `make quota TARGET=aws` | One row; `/info` used calls unchanged |
| Live ledger | `uv run python -m vs.host.ledger --summary` | Plan total ≤ 300; 2026-10 `/info` used calls ≤ 400 |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json'` | Empty |
| Secrets | `git grep -n -I -e 'IDENTIFIED BY' -e 'Bearer ' -e 'dbPassword' -- . ':!docs/user-guide.md' ':!specs' ':!vs/host/target.py' ':!scripts/grants.py'` | No key material; only placeholders and documentation |
