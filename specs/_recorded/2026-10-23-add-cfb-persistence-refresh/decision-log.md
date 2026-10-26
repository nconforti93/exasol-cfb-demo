# Decision Log: add-cfb-persistence-refresh

Date: 2026-10-22 (revised the same day after the independent review, `review.md`)

## Interview

The owner (Nico) was away. He authorized the orchestrator to answer the clarifying questions and log the decisions. The answers below are the orchestrator's, as passed to the planner.

**Q:** What is the scope?
**A:** Roadmap Phase 2 ("Persistence + refresh"):
- Persist CFBD data from `CFBD_VS` into the `CFB_CORE` mirrors under a load plan with refresh classes and `demo`/`full` profiles.
- `REFRESH_WEEK(year, week)`, the one-call weekly refresh that demo beat 3 relies on.
- A resumable, idempotent `BACKFILL([from])`: newest first, only slices missing from `LOAD_LOG`, within a monthly call budget that keeps a reserve.
- Load, refresh and fetch logs, and a quota view.
- `make snapshot` / `make restore`: zero API calls on a rebuild, and also the rehearsal reset.

**Q (1) Execution mechanism:** Python UDFs cannot write tables. Where do loads run?
**A:** Where SQL can execute: Exasol Lua scripting (`EXECUTE SCRIPT`) or host-side Python. The demo shows "one call refreshes everything" as SQL, so prefer a database-side scripting entry point. Verify the exact Exasol syntax: Exasol has no `CALL`, so research what the demo can show instead, and tell the user what changes.

**Q (2) Load mechanics:** How are slices loaded and registered?
**A:**
- Insert into `CFB_CORE.<T>` from `CFBD_VS.<T>` (read-through off for the load).
- Register the slices in `CFB_META.LOAD_LOG`, final or non-final: the current or an incomplete week is non-final.
- Capture `FETCH_LOG` (`vs/host/fetchlog.py`).
- Decide where a non-final slice is replaced (delete+insert per slice), and keep it idempotent.

**Q (3) Load plan:** What does it hold, and where?
**A:** A config, presumably YAML next to the catalog-overrides.
- Per endpoint: refresh class (stored / volatile / on-demand), history start, profile membership (`demo` vs `full`) and priority.
- Base the demo profile on the roadmap's API budget table (about 200 calls) and `docs/cfbd-data-availability.md`.

**Q (4) Budget:** How are calls limited?
**A:**
- A monthly call budget property with a reserve for refreshes. The quota view reads `CFBD_VS.INFO` (free), and `BACKFILL` stops before the budget.
- The owner's current authorization is ≤ 400 live calls in October 2026 in total, including the foundation plan's ≤ 50.
- The plan's own live tasks must stay inside a stated cap with a ledger, like the foundation plan's `cache/live-calls.jsonl` + `CONFIRM=yes`.

**Q (5) Snapshot/restore:** Format and location?
**A:**
- `EXPORT`/`IMPORT` of `CFB_CORE` plus `LOAD_LOG` as Parquet, or as CSV if Parquet export isn't native. Check the Exasol skills: `exapump` handles Parquet export locally.
- Store them in a private directory outside the repo (e.g. `~/.cfb-demo/snapshots/<name>/`). No S3: it is external and costs money.
- It must work against both targets: local Personal and the AWS Personal instance.

**Q (6) Dev container volume:** Keep the roadmap item "Dev container data on a named Docker volume"?
**A:** Drop it. The runtime moved to Exasol Personal (D17). Log it.

**Q (7) Targets:** Which databases?
**A:**
- Live data lives on the AWS `default` Exasol Personal instance, where the owner created the `CFBD_API` connection.
- Mock/replay integration tests run on local Personal in a `TEST_` namespace with the existing harness (`tests/it/harness.py`).
- Deployment ownership is `sys` (foundation decision [20]).

**Q (constraints, from the orchestrator):** What limits apply to planning?
**A:**
- No DB session on either database: a parallel session runs exclusive-window benchmarks.
- No CFBD calls.
- Don't edit `docs/roadmap.md`, `specs/architecture.md` or `CLAUDE.md` (the parallel plan `add-engine-spikes` is about to edit them); write their changes as tasks.
- Check Exasol syntax and reserved words with the installed Exasol skills.
- Testing follows the foundation plan's tiers (unit, CLI, integration on mock or replay, using the existing harness). Live tasks go last and are gated.

**Q (review, orchestrator's answers to `review.md`):** Which review findings are accepted, and how?
**A:**
- **M1:** accepted. Enforce the month cap in the database against `/info` `USED_CALLS`. The October run deploys with budget 400 and reserve 50. The ledger stays as a second guard.
- **M2:** accepted. A per-slice fallback after a batch failure, a quarantine for persistent failures, and real spend measured as the `/info` delta.
- **M3:** accepted. `pquery`/`pcall`, plus abandoned-lock detection for own or idle sessions; both go into probe 1.1.
- **M4:** accepted. Batches are grouped by `ENTRY_ID`, and the season of range slices is defined.
- **M5:** accepted. Snapshot only `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`.
- **M6:** accepted. Orphan detection in `LOAD_STATUS`, plus a catalog-hash check on restore.
- **M7:** accepted. A zero-call AWS smoke step in 9.0.
- **M8:** this belongs to the foundation's overrides; don't edit `vs/catalog-overrides.yaml`, the foundation's code-review fixes will. State it as an explicit prerequisite: "foundation: `RANKINGS_POLLS_RANKS` carries `SEASON`/`WEEK`/`POLL` parent keys so it loads standalone; `RANKINGS_POLLS` is not loaded". Adjust the demo profile and the call counts.
- **M9:** accepted. `season_type` in `REFRESH_WEEK` and in the step contract.
- **M10:** accepted. Reorder the tasks, with single ownership of `loadlib.lua`.
- **Minors:** resolve the cheap ones.
  - YAGNI: drop week-sliced loading and the authoring of the `full` profile entries unless the demo needs them, and put them in the backlog.
  - Placeholders: use `{META_SCHEMA}`, which is what the code renders.

**Q (orchestrator update during the revision):** Which foundation fixes should the plan reference instead of duplicating?
**A:**
- (1) `vs/host/target.py` (`TARGET=local|aws`) resolves the DSN, the password and AWS certificate pinning from the Personal deployment files, and is wired into the deploy, grants and dns scripts and the Makefile. Decision [17] and task 1.3 reuse it and add only what the loader needs.
- (2) M8 is handled on the foundation side. `RANKINGS_POLLS_RANKS` gets its own `SEASON`/`WEEK`/`POLL` parent keys and the catalog is regenerated, so the demo loads only `RANKINGS_POLLS_RANKS`.
- The catalog hash now covers the generated content (foundation [25]).

No git commit is made before implementation.

## Design Decisions

### [1] The loader's entry points are Lua scripting programs; host Python only deploys and wraps them

- **Decision:**
  - `CFB_INFRA.BACKFILL(profile, from_season, max_calls)` and `CFB_INFRA.REFRESH_WEEK(season, week, season_type)` are Lua scripting programs (`CREATE LUA SCRIPT … RETURNS TABLE`), run with `EXECUTE SCRIPT`.
  - Host Python compiles and deploys the load plan, wraps the calls (`make backfill`, `make refresh`: estimate, `CONFIRM`, ledger) and runs snapshot/restore.
- **Alternatives:**
  - A host-side Python orchestrator over pyexasol: it loses the "one SQL call" beat and needs a client next to the DB.
  - Python UDFs: they cannot run SQL or write tables.
- **Rationale:**
  - The Exasol grammar (skill `exasol-database`) allows only `CREATE [LUA] SCRIPT` for scripting programs. Lua is the only way to run multi-statement SQL inside the database.
  - It works from any SQL client, the app backend (Phase 6) and the AWS instance without host tooling.
- **Promotes to ADR:** yes

### [2] The demo shows `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, 'regular')`, which returns the step timeline

- **Decision:**
  - The demo line `CALL REFRESH_WEEK(2026, N)` (roadmap demo flow, beat 3) becomes `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, 'regular')`.
  - The script returns the step timeline as a result table, one row per fetched table (step, status, seconds, slices, rows, calls), so the one call also shows "SQL → HTTP" per table.
  - Never run it `WITH OUTPUT`: that discards the table.
  - If probe 1.1a shows that `EXECUTE SCRIPT` accepts fewer arguments than declared, the two-argument form works too.
- **Alternatives:** An SQL preprocessor that rewrites `CALL x(...)` into `EXECUTE SCRIPT`. Rejected: it shows non-Exasol syntax to an Exasol audience, and it is a session-level dependency that can break the demo.
- **Rationale:** `CALL` is a reserved word in Exasol but has no statement. `EXECUTE SCRIPT script(args) [WITH OUTPUT]` is the statement (grammar checked). The mission, roadmap and user guide need the wording change (tasks 8.1, 8.3, 8.4).
- **Promotes to ADR:** yes

### [3] Set logic lives in SQL views; the Lua stays thin, uses `pquery` throughout, and the views are tested before the Lua

- **Decision:**
  - Finality, gaps, gating, the current season, queue order, quarantine, orphans, calls to complete and the quota arithmetic live in the `CFB_META` views `LOAD_SLICES`, `BACKFILL_QUEUE`, `LOAD_STATUS` and `QUOTA`. They are tested on the engine in group E, before any Lua exists.
  - The Lua only:
    - takes the lock;
    - reads the next batch;
    - runs the batch transaction;
    - logs and commits.
  - Every SQL statement in the Lua runs through `pquery`, and each entry function is wrapped in `pcall`. A failure therefore always reaches the rollback, the log and the lock release before the error is re-raised.
- **Alternatives:**
  - Logic in Lua with a host Lua 5.4 + busted toolchain: a toolchain for about 300 lines of glue, and the logic would be invisible to the presenter.
  - `query()` throughout: it ends the script on the first error (skill `exasol-udfs`), so the error path could not run (review M3).
- **Rationale:** Declarative views can be inspected (`SELECT * FROM CFB_META.BACKFILL_QUEUE` is itself the estimate) and tested early. `pquery` is the only way to keep the lock and logs consistent after a failure.
- **Promotes to ADR:** yes

### [4] Read-through stays on during loads; the loader keeps only fetched rows and never changes virtual schema properties

- **Decision:**
  - Every batch query reads `CFBD_VS.<T>` with read-through on and adds `REQ_SOURCE <> 'STORED'`.
  - No loader code issues `ALTER VIRTUAL SCHEMA`; a unit test scans the sources.
  - This deviates from the orchestrator's wording "read-through off for the load".
- **Alternatives:** Switch `READ_THROUGH` to `FALSE` for the load (the harness's `stage_slices` pattern) and back afterwards.
- **Rationale:**
  - Virtual schema properties are global. During a demo refresh, the app's and the presenter's concurrent queries would re-fetch stored slices and spend quota, and a crash would leave read-through off.
  - The result is the same either way. A batch contains only slices that are missing or not final, and the stored branch serves only final slices. The fetch branch therefore fetches every batch slice, and the stored branch returns nothing for them; the filter is a safety net.
  - Consequence: current-season slices are never final, so any `CFBD_VS` query on them is a live call. Pages and the chat must read current-season data from `CFB_CORE` (review m1; tasks 8.1, 8.2).
- **Promotes to ADR:** yes

### [5] Stage, then swap, in one transaction per batch of one plan entry

- **Decision:**
  - A batch is one plan entry (`ENTRY_ID`), of at most `MIN(MAX_CALLS_PER_QUERY, allowance)` slices. Its fan-out query (fixed filter + slicing filter) inserts into `CFB_STAGE.<T>` (the same DDL as the mirror), which is asserted empty before the batch. Then, in the same transaction:
    1. `DELETE FROM CFB_CORE.<T> WHERE REQ_SLICE IN (batch)` and `INSERT … SELECT` from the stage;
    2. `MERGE` into `LOAD_LOG`, with `IS_FINAL` from `LOAD_SLICES.FINAL_IF_LOADED_NOW` and `ROWS_LOADED` from the staged counts;
    3. one `FETCH_LOG` row per batch slice (staged `REQ_*` metadata, or `ROWS_RETURNED = 0`);
    4. clear the batch's `LOAD_FAILURES` rows and the stage, update the run and step logs, `COMMIT`.
  - Any error is handled by [24].
- **Alternatives:**
  - Batches per table: a `WHERE` clause cannot express two entries of the same table (two classifications, range + season) (review M4).
  - Insert straight into `CFB_CORE`: the HTTP time would fall inside the core table's write window.
  - One transaction per run: not resumable.
- **Rationale:**
  - Non-final slices are replaced where the orchestrator asked: delete+insert per slice, inside the batch transaction.
  - Other sessions never see a half-replaced slice, and a failed batch leaves no trace.
  - The HTTP part runs before `CFB_CORE` is touched, so the write window is short.
  - The quota is read in its own committed transaction before the batch (review m5).
- **Promotes to ADR:** yes

### [6] Slice keys come from templates compiled by the adapter's own planner; each batch is checked with a zero-call EXPLAIN

- **Decision:**
  - At deploy, `loader/templates.py` runs `vs.cfbd_vs.rules` (explicit defaults, rules) and `vs.cfbd_vs.plan` with sentinel values for each entry, and stores `SLICE_TEMPLATE` (e.g. `/games?seasonType=both&year={season}`).
  - The views substitute integers.
  - Before fetching, each batch runs `EXPLAIN VIRTUAL` on its query. The Lua checks every batch key as a quoted string in the manifest, with plain string search and no JSON library, together with the planned count. On a mismatch it stops before any request and names `make deploy`.
  - After the fetch, every staged `REQ_SLICE` must be a batch key.
- **Alternatives:**
  - Re-implement the slice-key grammar in Lua or SQL: two implementations would drift.
  - `EXPLAIN` every slice at run time: one adapter call per slice.
- **Rationale:**
  - The grammar exists once (foundation [5]), and a drift between plan and adapter is caught at zero calls.
  - A drift that moves both sides together (catalog change, restore from an older catalog) is caught by the orphan guard [23].
- **Promotes to ADR:** yes

### [7] One load plan file, `loader/load-plan.yaml`, compiled into `CFB_META`; several entries per table allowed

- **Decision:**
  - The plan lives at `loader/load-plan.yaml`, following the mission's planned layout (`loader/`: load plan, backfill, `REFRESH_WEEK`), not next to `vs/catalog-overrides.yaml` as the orchestrator presumed.
  - Each entry has: table, profile, priority, slicing (`none` / `season` / `season_range`), seasons, gaps, fixed `params`, refresh class.
  - A table may have several entries with non-overlapping seasons per fixed parameters.
  - `none` and `season_range` slices have `SEASON` NULL, so they are queued first and `from_season` never skips them (review M4).
  - `make deploy` validates the plan against the catalog, listing every error, and replaces the plan tables in one transaction. `LOADER_CONFIG="k=v,…"` overrides settings for one deployment until the next deploy.
- **Alternatives:**
  - Load-plan columns in `vs/catalog-overrides.yaml`: that mixes "what the API means" with "what the project stores".
  - A plan table edited with SQL: not reviewable in git.
- **Rationale:** One reviewed source decides what costs calls. The validator uses the adapter's own rules, so an entry the API cannot serve fails at deploy, not on a paid call.
- **Promotes to ADR:** yes

### [8] Refresh classes `static` and `weekly` with date-based finality; D15 storage classes are derived

- **Decision:**
  - Configured refresh class:
    - `static`: final on load.
    - `weekly`: season slices are final from `season_final_after` (02-15) of the next year; `REFRESH_WEEK` re-fetches them while they are not final, for the profiles in `refresh_profiles`.
  - A slice stored before it was final is fetched once more by the next `BACKFILL` after it becomes final.
  - The current season starts on `season_rollover` (08-01).
  - Storage classes are derived, not configured. The orchestrator's "stored / volatile / on-demand" are D15's classes:
    - `stored` = a `demo` entry;
    - `federated`/on-demand = no entry, or `full` only;
    - `volatile` = no mirror in the catalog (rejected by the validator);
    - `unavailable` = gated without the feature.
  - The first draft's `archive` class and `season_week` slicing moved to the backlog ([29]): `refresh_profiles` already keeps `full` entries out of the weekly refresh.
- **Alternatives:**
  - Final when every game of the season has `COMPLETED = TRUE`: historic seasons contain cancelled games that never complete.
  - Configuring D15 classes in the plan: duplicates the catalog.
- **Rationale:** Dates are deterministic, cheap and testable with a clock seam. The roadmap's "the current or incomplete week is non-final" holds: the current season's slices contain the current week and stay non-final.
- **Promotes to ADR:** yes

### [9] The weekly refresh re-fetches the current season's season slices instead of adding week slices

- **Decision:** For `weekly` entries, `REFRESH_WEEK(season, week, season_type)` re-fetches the season's slice (e.g. `/games?seasonType=both&year=2026`), which stays non-final, with delete+insert. `week` and `season_type` are validated against the stored calendar, name the run and feed the registered steps.
- **Alternatives:** Fetch `/games?…&week=N&year=2026` each week, as the roadmap's refresh list implies.
- **Rationale:** Read-through matches slice keys exactly (foundation [5]). Week slices next to a stored season slice would store every game twice, and subsumption is a foundation non-goal. A season call costs the same 1 call, and it also picks up corrections to earlier weeks.
- **Promotes to ADR:** yes

### [10] Demo profile: 217 calls once, 4 per weekly refresh

- **Decision:**

  | Table | Seasons | Refresh class | Calls |
  |---|---|---|---|
  | `TEAMS`, `VENUES`, `CONFERENCES` | none | static | 3 |
  | `CONFERENCES_AFFILIATIONS` | 1869–2025 as one range + 2026 | static | 2 |
  | `CALENDAR` | 2013– | static | 14 |
  | `GAMES` | 1869– without the 1871 gap | weekly | 157 |
  | `METRICS_WP_PREGAME`, `RATINGS_SP` | 2013– | weekly | 28 |
  | `RANKINGS_POLLS_RANKS` | 2014–, standalone per [27] | weekly | 13 |

  - The weekly refresh fetches the four weekly season slices (4 calls).
  - `/games/schedule` is volatile (no mirror) and stays on-demand.
  - Distinct priorities (1 to 6) keep each entry's slices consecutive, so batches are full fan-outs.
- **Alternatives:**
  - Calendar only for the current season: saves 13 calls, but the as-of helpers of Phase 3 want week dates back to 2013.
  - Week-level refresh calls: see [9].
- **Rationale:** 217 fits the free tier's first month (spendable 850) and the October cap. The current season's calendar is `static` and may lack bowl weeks. The remedy (reset `IS_FINAL`, backfill) is documented, and an automatic refresh is backlog ([29]; review m9).
- **Promotes to ADR:** no

### [11] Budget: loader settings from the YAML, checked against `/info`, overridable per deploy; two guards

- **Decision:**
  - `monthly_call_budget` (1000, D13) and `refresh_reserve` (150) live in `LOADER_CONFIG`, deployed from the YAML or overridden for one deployment with `make deploy LOADER_CONFIG="…"`.
  - `CFB_META.QUOTA` computes:
    - `PROJECT_BUDGET = LEAST(MONTHLY_LIMIT, budget)`;
    - `REFRESH_SPENDABLE = GREATEST(0, LEAST(REMAINING_CALLS, PROJECT_BUDGET − USED_CALLS))`;
    - `BACKFILL_SPENDABLE = GREATEST(0, REFRESH_SPENDABLE − reserve)`.

    `USED_CALLS` counts the key's whole month, so `PROJECT_BUDGET` caps every client, including SQL typed outside `make`.
  - For the October live group, AWS is deployed with budget 400 and reserve 50 (the owner's authorization), and the default is restored afterwards.
  - The ledger is the second guard: the per-plan cap, plus the month cap checked against `/info` `USED_CALLS` + estimate, not against the local file (review M1).
  - The refresh may dip into the reserve. Replay-mode runs read no quota.
- **Alternatives:**
  - Virtual schema properties: the adapter cannot use them.
  - The ledger as the only month guard: it misses calls made from SQL clients, other plans and the shared pool.
- **Rationale:** The key's own counter is the only complete record. The default keeps the free-tier design target even on the academic key (3,000).
- **Promotes to ADR:** yes

### [12] Backfill semantics: candidates, gaps, order, batches, measured spend, dry run, signature

- **Decision:**
  - **Candidates:** missing slices plus finalizable non-final slices. Quarantined slices and tables with orphans are excluded.
  - **Gaps:** registered as final empty slices (`LOADED_BY = 'LOAD_PLAN_GAP'`) at 0 calls, in one `GAPS` step row; they are excluded from the run's `SLICES` (review m18).
  - **Order:** profile (`demo` before `full`), priority, newest season (seasonless first), table, entry. Slices with an earlier failure come last, fetched alone.
  - **Batches:** the longest run of consecutive queue entries of one plan entry, capped by `MAX_CALLS_PER_QUERY` and the allowance.
  - **Spend:** in live mode it is measured as the `/info` used-calls delta around each batch, and the allowance decreases by the measured spend (review M2).
  - **Signature:** `BACKFILL(profile, from_season, max_calls)`, with NULL meaning no limit. `max_calls = 0` is a dry run: it registers gaps, runs `EXPLAIN` on every pending batch, and fetches nothing (review m20).
  - The result has numeric `CALLS_NEEDED` and `FAILED_SLICES` columns (review m17).
- **Alternatives:**
  - Strict season-major across all endpoints: batches of one slice.
  - The `ARRAY` parameter form for optional arguments: less readable.
  - Count slices as calls: retries make that an underestimate.
- **Rationale:**
  - Priority-major ordering gives the owner control and keeps fan-out parallelism.
  - The roadmap's `BACKFILL([from])` is the positional `from_season`.
  - The measured spend makes the allowance true.
- **Promotes to ADR:** yes

### [13] Logs and exclusive runs, with stale-lock rules

- **Decision:**
  - `CFB_META.LOAD_RUN` holds one row per run, with kind, args, session, times, status, totals, `/info` before and after, and the measured calls.
  - `CFB_META.REFRESH_LOG` holds one row per step, per table fetch or per batch, committed at start and end.
  - `CFB_META.LOAD_FAILURES` holds the failing slices.
  - The existing `FETCH_LOG` (`CONTEXT = '<KIND>#<RUN_ID>'`) and `LOAD_LOG` (`LOADED_BY = '<KIND>#<RUN_ID>'`) contracts are unchanged.
  - A `RUNNING` row is the lock. It is stale, set to `ABANDONED`, and the next run proceeds, when:
    - its session is gone;
    - it belongs to the calling session;
    - it is a script run (`BACKFILL`, `REFRESH_WEEK`) whose session is idle. A script is one statement, so idle means it ended without closing the row (failure, `KILL STATEMENT`, interrupt) (review M3).

    Snapshot and restore rows run from host sessions that are often idle, so they are stale only when the session is gone.
  - Probes 1.1i and 1.1j fix the concurrent-insert behaviour and the idle column.
- **Alternatives:**
  - A separate lock table.
  - A timeout: a long backfill could exceed it, and a failed run would block until it expires.
- **Rationale:** Two loads writing the same table would collide in Exasol's optimistic transaction model. A failed run in the presenter's still-open session must not block the next refresh.
- **Promotes to ADR:** no

### [14] `REFRESH_WEEK` is a step chain with a registry; steps receive the season type

- **Decision:**
  - Built-in steps: 1 `CHECK`, then 2–99 `FETCH <table>`, one per fetched table (review m13).
  - Registered steps come from `refresh_steps` in the YAML, deployed to `REFRESH_STEPS` (step number ≥ 100), and are called as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)`.
  - `season_type` is `'regular'` or `'postseason'` (NULL = `'regular'`). The calendar numbers both from week 1 (review M9).
  - Each step commits its log row, and a failing step stops the chain.
  - Tables commit one by one, so a failure leaves earlier tables refreshed (review m14).
  - This plan registers no steps. Phases 3–5 add model, predictions and simulations.
- **Alternatives:**
  - `(run_id, season, week)` only: postseason week 1 and regular week 1 would be indistinguishable, and every Phase 3–5 step would need a later interface change.
  - Hard-code later steps in the refresh script.
- **Rationale:** The mission's chain (fetch → merge → model → predictions → simulations) grows phase by phase without touching the Phase 2 script, and the bowl weeks up to the 2027-01-25 title game are addressable.
- **Promotes to ADR:** yes

### [15] Snapshots are Parquet files written by exapump, scoped to `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`; restore is atomic and catalog-checked

- **Decision:**
  - `make snapshot` writes one `exapump export --format parquet --compression zstd` file per non-empty scope table to `~/.cfb-demo/snapshots/<name>/` (0700/0600; never in the repo or OneDrive; never overwritten).
  - The scope is every `CFB_CORE` mirror plus `LOAD_LOG` and `FETCH_LOG` (review M5). `LOAD_RUN`, `REFRESH_LOG` and `LOAD_FAILURES` stay with each deployment.
  - `manifest.json` holds catalog and plan hashes, columns, rows, SHA-256 and a per-column fingerprint.
  - `make restore`:
    1. refuses on a column difference, and on a catalog-hash difference unless `FORCE=yes` (review M6);
    2. `exapump upload`s into a landing schema `CFB_RESTORE`;
    3. checks the fingerprints;
    4. replaces every scope table in one transaction;
    5. drops the landing schema and records a `RESTORE` run.

    It never touches `CFBD_VS`.
  - Both commands take the run lock. Probe 1.2 decides; if exapump's round trip is not exact, the fallback is gzip CSV through pyexasol.
- **Alternatives:**
  - Snapshot `LOAD_RUN` and `REFRESH_LOG` too: identity collisions, resurrected `RUNNING` rows, and the restore's own lock row replaced by the swap.
  - Native `EXPORT … INTO LOCAL CSV`: CSV only.
  - Native Parquet `IMPORT`: S3 only.
  - pyexasol `export_to_parquet`: it infers column types from the CSV stream.
  - S3: external.
- **Rationale:**
  - The skills name `exapump` as the local Parquet path, and it is installed (0.13.0).
  - Fingerprints check every conversion, and the landing schema makes a failed restore change nothing.
  - Run history is per deployment: restored `LOADED_BY`/`CONTEXT` values refer to the source deployment's runs.
  - The catalog hash covers the generated content (foundation [25]), so any regeneration changes it, not only a slice-key change. `FORCE=yes` is then a deliberate decision, backed by the orphan report after the restore ([23]).
- **Promotes to ADR:** yes

### [16] No S3, also for AWS

- **Decision:** The roadmap's "local dir or private S3 for AWS" becomes "local dir only". An AWS snapshot streams to the presenter's laptop through `TARGET=aws`.
- **Alternatives:** A private S3 bucket for the AWS instance.
- **Rationale:** The orchestrator excluded S3 (external, costs money). The demo profile is small, and the laptop is where rehearsals run.
- **Promotes to ADR:** no

### [17] Deployment targets come from the foundation's `vs/host/target.py`; the loader adds only the exapump environment

- **Decision:**
  - The foundation's code-review fix `vs/host/target.py` (prerequisite P2) provides `TARGET=local|aws`:
    - it resolves the DSN, the sys password (at connect time) and AWS certificate pinning from the Personal deployment files;
    - it is wired into deploy, grants, dns and the Makefile (`TARGET ?= local`).
  - This plan reuses it and adds:
    - `loader/exapump.py`: `EXAPUMP_DSN` only in the child's environment, `--certificate-fingerprint` from the target's pinned certificate;
    - `TARGET` on the loader's own make targets.
  - The 9.0 smoke test checks that exapump's native transport stays on the pinned node of the 3-node cluster, or that the pinning covers every node it opens (review m11).
- **Alternatives:**
  - A second target module in this plan (the first draft's `vs/host/targets.py`): two owners for the same secrets and pinning code.
  - `CERT_NONE` for AWS: the sys password would cross the internet unverified.
  - exapump profiles: secrets in `~/.exapump/config.toml`.
- **Rationale:** One owner for target resolution and pinning (orchestrator instruction). The loader's only new surface is the child-process environment, with no secret in files, argv or logs.
- **Promotes to ADR:** yes

### [18] `PROMOTE()` is deferred until after Phase 6

- **Decision:** The nightly promotion of slices fetched on demand is not built in this plan.
- **Alternatives:** Build it now by re-fetching the slices named in `FETCH_LOG`.
- **Rationale:** On-demand results are not kept, and their fetch-log rows are written by the backend (Phase 6). Before that there is nothing to promote except by re-fetching.
- **Promotes to ADR:** no

### [19] The dev-container Docker volume item is dropped

- **Decision:** Remove "Dev container data on a named Docker volume" from roadmap Phase 2 (task 8.3).
- **Alternatives:** Keep it for the retired `cfb-exa` container.
- **Rationale:** The runtime is Exasol Personal (D17), and snapshots cover rebuilds. Orchestrator answer (6).
- **Promotes to ADR:** no

### [20] Live work runs on AWS `default` under two guards: in-DB budget 400/50 and the ledger (plan cap 300)

- **Decision:**
  - Group J runs on AWS `default`:
    - a zero-call smoke test (9.0, [26]);
    - the demo backfill (≈ 217), first 20 calls, then the resume;
    - an idempotent re-run (0);
    - one live `REFRESH_WEEK` (4; it may use the reserve);
    - a snapshot restored on local (0).
  - Guards:
    - in the database, `PROJECT_BUDGET` 400 and reserve 50 on `/info` used calls ([11]);
    - in the wrappers, the ledger with this plan's cap (300) and the month cap (2026-10: 400) checked against `/info` used calls.
  - A month without a ledger entry refuses live calls, and `LEDGER=off` disables the ledger for other keys.
- **Alternatives:**
  - Live work on local Personal: the owner placed live data on AWS.
  - The ledger as the only month guard (review M1).
- **Rationale:**
  - 217 + 4 + a reserve of 79 = 300 ≤ 300; 300 + 50 = 350 ≤ 400.
  - 9.0 requires ledger room ≥ estimate + 4 only (review m3).
  - If group J cannot finish by 2026-10-31, the owner should add a November entry and choose the November budget. The quota resets on 2026-11-01 (review m4).
- **Promotes to ADR:** no

### [21] Integration tests: `TEST_` namespace, plan variants, a test-only clock, counting `/info`

- **Decision:**
  - The harness deploys the loader into the `TEST_` namespace with small plan variants (`tests/it/fixtures/load-plan.*.yaml`, module-scoped deploys) over:
    - synthetic data (hand-tuned `/calendar`);
    - the mock API (static or counting `/info`, 429/500, latency, changed bodies);
    - replay.
  - `LOADER_CONFIG.NOW_OVERRIDE` sets the date; deploy removes it outside `TEST_` namespaces.
  - The restore test rebuilds the test namespace (drop, deploy, re-grant, restore). The cross-target restore (AWS → local) is checked live in 9.5.
- **Alternatives:** A second test namespace for cross-deployment restores. Rejected: it needs another owner user and doubles setup time.
- **Rationale:** Zero calls, deterministic dates, and the real engine semantics for the Lua and the views.
- **Promotes to ADR:** no

### [22] Lua scripts run with the caller's rights; an app-triggered refresh needs a loader role later

- **Decision:**
  - On the real deployment `sys` owns and runs the scripts ([20] of the foundation).
  - Letting the app user `CFB_APP` press "refresh" (Phase 6) needs a role with the loader's privileges. That is deferred to Phase 6.
  - Probe 1.1f verifies the caller-rights model.
- **Alternatives:** Grant the app user write access now.
- **Rationale:** No consumer exists yet. Least privilege until Phase 6 designs it.
- **Promotes to ADR:** no

### [23] Stored data is pinned to the catalog and the plan; orphans are reported and block re-fetching; migration is out of scope

- **Decision:**
  - Deploy refuses a mirror whose columns differ from the catalog (foundation behaviour), and restore refuses a snapshot whose columns differ.
  - Restore also refuses a different catalog hash without `FORCE=yes`.
  - **Orphan slices** are `LOAD_LOG` rows that a loader run wrote (`BACKFILL#…`, `REFRESH_WEEK#…`, `LOAD_PLAN_GAP`) for a planned table but that match no planned key:
    - `LOAD_STATUS` reports them;
    - `make deploy` warns about them;
    - `BACKFILL` and `REFRESH_WEEK` refuse to fetch their table until the owner migrates or deletes them (review M6).
  - This plan's live backfill waits for the foundation's 9.4, so the stored data fits the final catalog. A future catalog change needs its own migration plan.
- **Alternatives:**
  - Build a migration now.
  - Only warn: a backfill would then re-buy the whole table.
- **Rationale:** A changed default changes every slice key of a table. Paying for a re-backfill after a catalog tweak, a plan edit or a restore is exactly what D11 forbids.
- **Promotes to ADR:** yes

### [24] A failing slice is isolated and quarantined; a systemic failure stops the run

- **Decision:**
  - In `BACKFILL`, a failed batch of several slices is rolled back and each slice is fetched alone. The good ones commit.
  - A slice that fails alone gets one failure per run in `LOAD_FAILURES`. From then on it is batched alone, after every other candidate of a run, and it is quarantined after `quarantine_after` (2) runs.
  - Quarantined slices are skipped and shown in `LOAD_STATUS` until the owner deletes the row; a success clears them.
  - If every slice of a batch fails alone too, the cause is not the slice: the run stops with `FAILED`. *Amended by [45]: the run stops after 2 consecutive failed batches.*
  - `REFRESH_WEEK` has no fallback or quarantine: a failure stops the refresh and is shown.
- **Alternatives:**
  - Fail the run and re-run the same batch: the fetch UDF fails the whole fan-out on one slice (`vs/cfbd_vs/fetch.py`), so every retry re-pays the siblings, and a persistent failure blocks the queue forever (review M2).
  - Quarantine on the first failure: a short outage would quarantine slices.
- **Rationale:** It bounds the waste per persistent failure (one batch, plus one solo fetch per run, for two runs), keeps the queue moving, and stops quickly on outages.
- **Promotes to ADR:** yes

### [25] Assumptions accepted for planning, each with a verify step

- **Decision:** These are assumed; each has a verify step and a fallback in the plan.
  - **A1:** Lua scripts can `COMMIT`/`ROLLBACK`, catch a failing `pquery` inside an open transaction and still roll back and log, run `EXPLAIN VIRTUAL`, return tables, nest `EXECUTE SCRIPT`, and run with the caller's rights.
    - verify: probe 1.1 (a–f), re-run on AWS in 9.0.
    - Fallback: one batch per call with a non-progress stop; `query("EXPLAIN VIRTUAL …")`; `import()` for steps.
  - **A2:** exapump 0.13 round-trips every mirror type exactly into an existing table, takes its DSN from `EXAPUMP_DSN`, and pins certificates on all nodes.
    - verify: probe 1.2, local and AWS.
    - Fallback: gzip CSV via pyexasol.
  - **A3:** The foundation's `vs/host/target.py` (P2) resolves the AWS password and the certificate pinning, and exapump accepts the pinned fingerprint for the node it connects to.
    - verify: the foundation's own tests, then task 1.3 and the 9.0 smoke test.
  - **A4:** `/games?seasonType=both&year=Y` returns all divisions in one call.
    - verify: the foundation's S1 marker `classification-omission-means-all`.
    - Fallback: per-classification `GAMES` entries, at about +157 calls per extra division. Task 9.1 then stops and reports.
  - **A5:** The foundation makes `RANKINGS_POLLS_RANKS` standalone with `SEASON`/`WEEK`/`POLL` (P1, [27]).
    - verify: gate in task 2.3.
  - **A6:** Group J runs before 2026-11-01 under the October authorization.
    - verify: both guards refuse otherwise.
  - **A7:** UDF name resolution works on AWS.
    - verify: `make dns-check TARGET=aws` (9.0).
  - **A8:** `season_final_after = 02-15` is late enough for CFBD's post-bowl corrections. Adjustable in the YAML.
  - **A9:** `EXA_ALL_SESSIONS` (or `EXA_DBA_SESSIONS`) shows whether a session runs a statement.
    - verify: probe 1.1j.
    - Fallback: only "own session" and "session gone" make a row stale; documented.
- **Alternatives:** Block planning until each is known. Rejected: no DB session or CFBD call was allowed while planning.
- **Rationale:** Each assumption has a zero-call probe or an existing gate before any money is spent.
- **Promotes to ADR:** no

### [26] A zero-call smoke test on AWS precedes every paid call

- **Decision:** Task 9.0 runs on AWS, before 9.1, at zero CFBD calls:
  - it records the DB version;
  - it re-runs the read-only and lock items of probe 1.1 and the snapshot probe 1.2 (exapump and pyexasol pinning on three nodes);
  - it runs `BACKFILL('demo', NULL, 0)` (gap registration, `EXPLAIN` of every pending batch, unchanged `/info`);
  - it runs a two-session lock check.

  9.1 is gated on it.
- **Alternatives:** Let the first paid `MAX_CALLS=20` run find platform differences (review M7).
- **Rationale:** Every probed fact so far comes from single-node nano 2026.2 local. AWS is a 3-node instance of unknown version.
- **Promotes to ADR:** no

### [27] Rankings come from `RANKINGS_POLLS_RANKS` alone, which needs foundation prerequisite P1

- **Decision:**
  - The demo loads only `RANKINGS_POLLS_RANKS`. The foundation's code-review fix gives it its own parent keys `SEASON`, `WEEK` and `POLL` in `vs/catalog-overrides.yaml` and regenerates the catalog; this plan does not edit the overrides.
  - `RANKINGS_POLLS` is not loaded, which saves 13 calls once and 1 per refresh.
  - Task 2.3 stops if the catalog lacks the keys.
  - Open recommendation to the foundation (not a blocker): add `SEASON_TYPE` as a parent key too. The final poll is postseason week 1, so it shares (`SEASON`, `WEEK` = 1, `POLL`) with the regular-season week-1 poll. Without it, Phase 3 must tell them apart some other way.
- **Alternatives:**
  - Load both tables and join them by position (the first draft): two requests in two transactions can shift the arrays when CFBD publishes a poll in between, and the join then silently pairs ranks with the wrong week (review M8).
  - Load both in one transaction with a consistency check: more code, and the extra calls remain.
- **Rationale:** A natural key needs no positional join and makes each slice self-contained.
- **Promotes to ADR:** yes

### [28] Task order: views tested before the Lua, and a single owner for `loadlib.lua`

- **Decision:**
  - Group E tests the views (load-plan and quota integration tests) before any Lua.
  - 4.1 is only the bundler. 4.2 owns `loader/lua/loadlib.lua` (lock, `load_batch`, failures) together with `backfill.lua` and the lock and failure tests. 4.3 (`refresh_week.lua`) starts after 4.2.
  - 7.3 tests the remaining run logs after 4.3.
- **Alternatives:** The first draft's order: Lua library in group B, untested until group E, with 4.2 and 4.3 editing it in parallel (review M10).
- **Rationale:** Every behaviour is tested before code depends on it, and only one task edits the shared library at a time.
- **Promotes to ADR:** no

### [29] Backlog

- **Decision:** Out of this plan, for the plan that first runs `PROFILE=full`, or for Phase 8:
  - `season_week` slicing with week-based finality (latest end date across season types, grace days), and "weeks up to `week`" in `REFRESH_WEEK`;
  - authoring the `full` profile's entries (per-season federated endpoints first, e.g. `DRIVES`, `CALENDAR` 2002–2012);
  - an `archive` refresh class, if `refresh_profiles` turns out not to be enough;
  - automatic refresh of the current season's calendar (bowl weeks published late);
  - `PROMOTE()` ([18]), mirror migration ([23]), an app loader role ([22]);
  - recording refresh slices for offline rehearsals (Phase 8).
- **Alternatives:** Build them now (the first draft's `season_week`, `archive` and full-profile authoring).
- **Rationale:** The demo profile needs none of them, and they are where the subtle bugs would live (review m7, m16).
- **Promotes to ADR:** no

### [30] Roadmap decision numbers are assigned when the docs are written

- **Decision:** Task 8.3 numbers this plan's promoted decisions after the last D-number in `docs/roadmap.md` at that time: after `add-engine-spikes`' D19/D20, the user's D21 and the foundation's D22+ block.
- **Alternatives:** Reserve numbers now.
- **Rationale:** The foundation's promoted count is not final until its group I runs. Coordinating only within this log avoids collisions (orchestrator instruction).
- **Promotes to ADR:** no

### [31] Snapshot files are gzip CSV written and read by pyexasol, not Parquet via exapump (probe 1.2)

- **Decision:**
  - `make snapshot` writes one file per scope table: `export_to_file(…, export_params={"format": "gz", "with_column_names": True})`. `make restore` reads it with `import_from_file(…, import_params={"format": "gz", "skip": 1})`.
  - Both use the target's pinned pyexasol connection. The manifest says `"format": "csv-gzip"`.
  - DOUBLE uses Exasol's default text form (15 significant digits).
  - Fingerprints:
    - DOUBLE: `SUM(CAST(ROUND(x, 8) AS DECIMAL(36,8)))`;
    - DATE: `SUM(DAYS_BETWEEN(d, DATE '1970-01-01'))`;
    - TIMESTAMP: `SUM(SECONDS_BETWEEN(ts, TIMESTAMP '1970-01-01 00:00:00'))`, never `POSIX_TIME`.
  - This amends [15]; the rest of [15] stands.
- **Alternatives:**
  - Parquet via exapump 0.13 (the plan's default; A2).
  - A 17-digit DOUBLE format (`csv_cols … FORMAT='9.9999999999999999EEEE'`).
- **Rationale** (`docs/spikes/s5-loader-probe.md` § "Snapshot format"):
  - exapump 0.13 fails on VARCHAR values with a comma, double quote or newline: exit 1 and a 0-byte file. Every JSON column has those.
  - It also silently trims leading and trailing spaces, and sometimes drops a double quote.
  - Its Parquet files are untyped (`col0…` VARCHAR, positional).
  - It writes no file for an empty table, and needs a lower-case certificate fingerprint.
  - The pyexasol path round-trips every mirror and meta type exactly, including 2,000,000-character values and the 0001/9999 dates, except full-precision DOUBLEs (≤ 3.8e-12 relative).
  - JSON-like short decimals came back exact (0 of 4,000 differ). The 17-digit format shifts about a third of all values by 1 ULP, because Exasol's text-to-DOUBLE parse is not correctly rounded.
  - `SUM(ROUND(x, 8))` over identical data differed by 9.3e-10 between evaluations; the DECIMAL sum never did.
- **Promotes to ADR:** no (it amends [15], whose ADR states the final format)

### [32] Two helper views, `LOADER_SETTINGS` and `LOAD_ORPHANS`, join the four views of the plan (task 3.2)

- **Decision:**
  - `LOADER_SETTINGS` is a one-row view: `CLOCK_UTC`, `NOW_UTC` (`NOW_OVERRIDE` or the clock), `CURRENT_SEASON`, the settings of `LOADER_CONFIG` as typed columns, and the virtual schema's `API_FEATURES`. Every other view and the Lua scripts read the date, the season and the gating from it.
  - `LOAD_ORPHANS` lists the orphan slices (a loader-written `LOAD_LOG` row for a planned table whose key matches no `LOAD_SLICES` row). `LOAD_STATUS`, `BACKFILL_QUEUE` and the deploy warning read it.
  - `vs/host/metaddl.py` creates the six views in the order `LOADER_SETTINGS`, `LOAD_SLICES`, `LOAD_ORPHANS`, `BACKFILL_QUEUE`, `LOAD_STATUS`, `QUOTA`; each reads only tables and views before it.
- **Alternatives:** Repeat the clock and the feature test in each view; compute orphans inside `LOAD_STATUS` only.
- **Rationale:** "Now" is one expression (probe 1.1l), so the test clock cannot drift between views. The orphan rule is needed in three places, and one definition keeps them equal.
- **Promotes to ADR:** no

### [33] `QUOTA.RESET_AT` is a UTC `TIMESTAMP`

- **Decision:** `QUOTA.RESET_AT` is `/info`'s `resetAt` (ISO text with `T` and `Z`) converted to a `TIMESTAMP` in UTC. `PERIOD_START = ADD_MONTHS(RESET_AT, -1)`. `LOAD_LOG`, `FETCH_LOG` and `LOADER_SETTINGS.CLOCK_UTC` are UTC too, so the period test is a plain timestamp comparison.
- **Alternatives:** Keep `resetAt` as text; convert it to the session time zone.
- **Rationale:** The session time zone is `CET` on Personal (probe 1.1l). A local `RESET_AT` would move the month boundary by one or two hours against the `FETCH_LOG` rows it is compared with.
- **Promotes to ADR:** no

### [34] Spend attribution counts `FETCH_LOG.ATTEMPTS`, not rows

- **Decision:** `QUOTA.BACKFILL_CALLS` and `REFRESH_CALLS` sum `GREATEST(COALESCE(ATTEMPTS, 1), 1)` over the `LIVE` `FETCH_LOG` rows of the current period whose `CONTEXT` is `BACKFILL#<run>` or `REFRESH_WEEK#<run>`. `OTHER_CALLS = GREATEST(0, USED_CALLS − BACKFILL_CALLS − REFRESH_CALLS)`.
- **Alternatives:** Count rows (one per slice).
- **Rationale:** A retried request costs the key a call per HTTP attempt. Counting rows would show the loader as cheaper than it is and push the difference into `OTHER_CALLS`. Batches that failed are rolled back, so their calls appear only in `OTHER_CALLS`.
- **Promotes to ADR:** no

### [35] `CALLS_TO_COMPLETE` includes tables that orphans block

- **Decision:** `LOAD_STATUS.CALLS_TO_COMPLETE` and `QUOTA.DEMO_CALLS_TO_COMPLETE` / `FULL_CALLS_TO_COMPLETE` count every pending slice (not a gap, not quarantined), also those of a table blocked by orphans. `BACKFILL_QUEUE` leaves those tables out.
- **Alternatives:** Count only what the queue can fetch now.
- **Rationale:** The calls are still needed once the owner resolves the orphans, and the status must not report "nothing left" for a table that is stuck.
- **Promotes to ADR:** no

### [36] `LOAD_STATUS.BLOCKED_BY` holds a feature name or `'ORPHANS'`

- **Decision:** `BLOCKED_BY` is the missing API feature of a gated entry (for example `weather`; the entry then has no slices), else `'ORPHANS'` when its table has orphan slices, else NULL. A missing feature wins over orphans.
- **Alternatives:** Two columns (`BLOCKED_BY_FEATURE`, `HAS_ORPHANS`); a free-text reason.
- **Rationale:** One column answers "why does this entry not load", and the two reasons are exclusive in practice: a gated table without its feature lists no slices, so it has no orphans (decision [38]). `ORPHAN_SLICES` carries the count.
- **Promotes to ADR:** no

### [37] The slice of a `season_range` or `none` entry is always planned

- **Decision:** A `none` or `season_range` entry gives exactly one slice with `SEASON` NULL, whatever the current season is. Only `season` entries are limited to the seasons from `SEASON_FROM` to the current season (or `SEASON_TO`). A range's bounds are the entry's own `SEASON_FROM` / `SEASON_TO`.
- **Alternatives:** Clip or drop a range whose end is beyond the current season.
- **Rationale:** A range slice is one API call by definition (`CONFERENCES_AFFILIATIONS` 1869–2025). It does not depend on the clock, and `from_season` never skips it (review M4).
- **Promotes to ADR:** no

### [38] Gated tables never report orphans

- **Decision:** `LOAD_ORPHANS` skips a table whose API feature is missing from `API_FEATURES`. `LOAD_SLICES` lists no slices for it then, so its stored slices would all look orphaned.
- **Alternatives:** Report them with a note; treat a missing feature as an error.
- **Rationale:** A missing feature is a normal state (the key's tier), not a catalog or plan change. The orphan rule (decision [23]) is for those. `LOAD_STATUS` shows the entry as blocked by the feature, and the orphans appear once the feature returns and the keys really do not match.
- **Promotes to ADR:** no

### [39] The demo plan gives each weekly entry its own priority (4, 5, 6)

- **Decision:** In `loader/load-plan.yaml`, `METRICS_WP_PREGAME` has priority 4, `RATINGS_SP` 5 and `RANKINGS_POLLS_RANKS` 6, where the first draft had 4, 4 and 5. A test pins the season-sliced priorities as distinct. The backfill total stays 217 calls.
- **Alternatives:** Keep equal priorities for `METRICS_WP_PREGAME` and `RATINGS_SP`.
- **Rationale:** The queue order is priority, then newest season (decision [12]), so entries of equal priority load season by season, and a batch never mixes entries. The two endpoints would give 28 one-slice batches. Distinct priorities give one fan-out of 14 slices each: 2 batches.
- **Promotes to ADR:** no

### [40] The loader deploy validates first, installs the Lua in memory and keeps the plan file's hash

- **Decision:**
  - `loader/deploy.py` reads, validates and compiles the plan and the `LOADER_CONFIG` overrides before it sends any statement. An invalid plan or override changes nothing, and the four plan tables are replaced in one transaction (`set_autocommit(False)`, then `COMMIT`; `ROLLBACK` on failure).
  - The Lua statements are built in memory for the namespace (`loader.bundle.build_script`), not read from `build/sql/loader_*.sql`. A script without sources is skipped with a notice.
  - `LOAD_PLAN.PLAN_HASH` is the hash of the plan file. A `LOADER_CONFIG` override changes the settings but not the hash. *Wording corrected in F.1 (review n6): it is the SHA-256 of the normalized plan (`loader.plan.normalized_json`), so comments, notes and entry ids do not change it.*
  - `NOW_OVERRIDE` is dropped with a warning outside a `TEST_` namespace.
- **Alternatives:**
  - Install the files of `build/sql`.
  - Hash the effective settings.
- **Rationale:**
  - The files hold the names of the real namespace, so a prefixed namespace would need its own file.
  - A snapshot compares plan hashes on restore, and the October budget override on AWS must not make a local snapshot look like a different plan.
- **Promotes to ADR:** no

### [41] The DOUBLE fingerprint sums the value's exported text form (task 5.2)

- **Decision:** A `DOUBLE` column's aggregate is `SUM(CASE WHEN ABS(t) < 1E27 THEN CAST(ROUND(t, 8) AS DECIMAL(36,8)) END)` with `t = CAST(CAST(x AS VARCHAR(100)) AS DOUBLE)`, on both sides. Values of 1E27 or more are only counted (`ROUND` would overflow). The spec states the exception: a `DOUBLE` with more than 15 significant digits comes back rounded to its text form (relative change at most 4e-12).
- **Alternatives:**
  - The spike's `SUM(CAST(ROUND(x, 8) AS DECIMAL(36,8)))` on the raw value: deterministic, but the round trip moves full-precision values, and their 8-decimal rounding then flips. A restore of real data would be refused.
  - Compare `DOUBLE` sums with a tolerance: needs a magnitude per column and weakens the check.
  - Short-decimal test data only: hides the problem until the live restore (9.5).
- **Rationale** (probe 2026-10-22, local nano 2026.2, 10,009 values incl. full-precision, short decimals, 1e-300, 1.7976e308, -0.0):
  - `EXPORT` writes exactly `CAST(x AS VARCHAR(100))` (also `TO_CHAR(x)`) for every value: 0 differences.
  - The imported value equals `CAST(CAST(x AS VARCHAR(100)) AS DOUBLE)` for every row, and that normalization is idempotent on the imported value. So the source fingerprint describes the restored value exactly.
  - `ROUND(x, 8)` differed between source and copy for 284 of 10,009 values; with the text form, 0.
  - The guarded `CASE` keeps `ROUND` away from 1.7976E308.
  - The 9.0 smoke and the 9.5 restore check the same on AWS (another version could parse text differently; the check then refuses, it does not pass silently).
- **Promotes to ADR:** no

### [42] Host processes take the run lock through a deployed `RUN_LOCK` script; `loader.runlock.run_lock` is the host API (hand-off H1b)

- **Decision:**
  - `<INFRA>.RUN_LOCK(lock_action, lock_word, lock_run, lock_text)` is a third bundled script (constants + `loadlib.lua` + `run_lock.lua`), installed by `make deploy`: `('acquire', kind, NULL, args)` returns `RUN_ID`, `('release', status, run_id, detail)` returns `HELD`. Four parameters instead of three: a release needs the run id and the detail.
  - `with run_lock(connection, namespace, kind, args) as lock:` (`loader/runlock.py`) for the host kinds `DEPLOY`, `SNAPSHOT`, `RESTORE`. The row ends `DONE` with `lock.detail`; an exception (also Ctrl-C) rolls the session back first, then ends the row `FAILED` with the error's first line and propagates. A refusal raises `RunLockRefused` naming the holder.
  - Guards: a connection with autocommit off is refused (acquiring commits), a second `run_lock` on the same connection is refused (the own-session rule would abandon the first), and script kinds are refused (a host session is idle between statements, so the idle rule would free their row at once).
  - Phase 3's model deploy and Phase 4's `make sim` / `make calibrate` use it with `DEPLOY`.
- **Alternatives:**
  - Re-implement the stale rules in host SQL: two implementations of one rule.
  - Three parameters with the run id and detail packed into one text: parsing in Lua for no gain.
- **Rationale:** one implementation of the lock in `loadlib.lua`, whose own-session and host rules were probed in 4.2. The host keeps its session open while it works, so a crashed process frees the lock with its session.
- **Promotes to ADR:** no

### [43] Snapshot details: logical names, an exact table set, a full stability check (task 5.2)

- **Decision:**
  - The manifest and the file names use logical schema names (`CFB_CORE`, `CFB_META`); a restore maps them to its namespace. A file name must equal `<SCHEMA>.<TABLE>.csv.gz` exactly, so a manifest cannot point outside its directory.
  - The snapshot fingerprints every scope table before and after the export and keeps nothing when one changed (stronger than the planned `LOAD_LOG`-only check: an app may write `FETCH_LOG` outside the lock). An empty table gets no file. Export and import name the columns explicitly; the header line is checked before the import.
  - The restore refuses a missing or extra `CFB_CORE` table and any column difference also with `FORCE=yes`; `FORCE` only accepts another catalog hash. The deployed catalog hash is read from the adapter script (`catalog_hash` in its embedded catalog); the plan hash is compared and reported, not enforced; orphan slices are reported after the restore.
  - Order: manifest, deployment checks (columns, catalog), file checks (SHA-256, header), lock, landing (dropped first if a crashed restore left it; the session's schema is reopened after `CREATE SCHEMA`), fingerprints, one-transaction swap, landing dropped also on failure.
  - pyexasol's file transfer is `EXPORT` / `IMPORT`, which need those system privileges. `sys` has them on the real deployment; the integration harness grants them to the test owner (no connection access comes with them).
- **Alternatives:** namespaced names in the manifest (a `TEST_` snapshot would not restore into the real namespace); `FORCE` overriding column checks (a positional import into other columns).
- **Rationale:** the restore stays exact and atomic, and every refusal happens before the first change.
- **Promotes to ADR:** no

### [44] `loader/exapump.py` is removed (task 5.2)

- **Decision:** With gzip CSV through pyexasol ([31]), no code calls exapump. `loader/exapump.py`, `tests/unit/test_loader_exapump.py`, the harness's `exapump_env()` with its smoke test, and `tests/it/loader_support.owner_target` (only used for it) are deleted. The probe script `docs/spikes/s5_snapshot_probe.py` keeps its own exapump calls as evidence.
- **Alternatives:** keep the module for a later Parquet path.
- **Rationale:** YAGNI; it would also need the lower-case fingerprint fix found in probe 1.2.
- **Promotes to ADR:** no

### [45] A backfill stops after 2 consecutive failed batches, not after one failed one-slice batch (F.1, review m8)

- **Decision:**
  - A batch **fails** when none of its slices is stored, also after its single-slice retries. That includes a fresh batch of one slice and a slice that failed in an earlier run (`BATCH_ALONE`).
  - A failed batch is recorded (its slices get one `LOAD_FAILURES` failure each) and the run continues. After `STOP_AFTER_FAILED_BATCHES = 2` consecutive failed batches the run stops with `FAILED` and names the last error. A batch with at least one stored slice resets the count.
  - The spec scenario "A failure of every slice stops the run" becomes "Two consecutive failed batches stop the run" (`GAMES` 2015–2020, `MAX_CALLS_PER_QUERY = 2`: the third batch is never requested), plus "One failed single-slice batch does not stop the run".
- **Alternatives:**
  - The rule of [24], stop at the first failed batch: in the demo plan `TEAMS`, `VENUES`, `CONFERENCES` and both `CONFERENCES_AFFILIATIONS` entries are one-slice batches, so one transient 500 on reference data stopped the whole backfill.
  - Record and continue without a stop: an outage would spend the quota slice by slice.
  - Do not count failures toward quarantine in a run that ended systemic (the review's second option): changes the quarantine semantics; not needed for the outage case.
  - Count only fresh batches, not slices that failed before: an outage over the held-back slices at the end of the queue would fetch every one of them.
- **Rationale:** The orchestrator's decision: two in a row protects the quota during an outage (at most two batches and their single-slice retries are paid), and one transient error, for example on `TEAMS`, costs only that slice. Two adjacent slices that keep failing alone now end the run `FAILED` instead of `DONE_WITH_FAILURES`; they are quarantined after `quarantine_after` runs as before.
- **Promotes to ADR:** yes (amends [24], D48)

### [46] Per-target loader settings in `vs/live/loader-config.yaml`; on AWS the budget follows the ledger's month cap (F.1, review m4)

- **Decision:**
  - `vs/live/loader-config.yaml` (committed) holds `LOADER_CONFIG` overrides per deployment target. `make deploy TARGET=<target>` applies them on top of `loader/load-plan.yaml`, so a plain redeploy (to fix a view, say) keeps a target's in-database budget.
  - `aws: {monthly_call_budget: ledger, refresh_reserve: 50}`. `ledger` means the owner's cap in `vs/live/ledger.yaml` for the UTC month of the deploy: 400 in October 2026, 1000 from the first November deploy. A month without a cap refuses the deploy unless `LOADER_CONFIG` sets `monthly_call_budget`.
  - An explicit `LOADER_CONFIG` wins per setting. The deploy prints each override with its source, and `loader/deploy.py` warns when a deploy changes `MONTHLY_CALL_BUDGET` or `REFRESH_RESERVE` against the deployed values (the review's own fix).
  - The values are deploy-time: they last until the next deploy. After a month change, the previous month's value stays in force until the first deploy of the new month.
- **Alternatives:**
  - Literal values per target (`aws: 400`): November needs a manual edit, and a forgotten edit keeps 400 or, worse, lifts it.
  - Month-keyed budgets in `LOADER_CONFIG`, chosen by the `QUOTA` view at query time: follows the month without a redeploy, but changes the view, the plan settings and the spec for a two-month horizon; a month without an entry would fall back to the plan's 1000.
  - Writing 0 for a month without a cap: `monthly_call_budget` must be at least 1, and `refresh_reserve` would exceed it.
  - Only the warning (the review's fix): still silent for anyone who does not read the deploy output, and November still needs the right `LOADER_CONFIG` by hand.
- **Rationale:** The simplest rule that is correct at every deploy: the in-database guard equals the owner's authorization of the month the deploy happens in, from the one file the owner already edits. Between deploys the value can only be the previous month's, never a lifted one; the ledger stays the second guard. The owner's caps stay in `ledger.yaml` only.
- **Promotes to ADR:** no

### [47] An interrupted or timed-out run is recorded from a new session (F.1, review M1)

- **Decision:**
  - `scripts/loader_cli.py` reads `CURRENT_SESSION` before it starts the script. A `KeyboardInterrupt` (any `BaseException`) during the script is caught, the run is recorded, then the interrupt is re-raised.
  - When the script did not return normally (an error, a socket timeout, an interrupt), the CLI opens a **new** connection. If the run's session is still running the script, it is killed with `KILL SESSION` and awaited for up to 30 s, so the measured spend is final. Then it reads the run's `LOAD_RUN` row by session id and `/info`, and records the larger of `CALLS` and the `/info` delta. A script error whose session is already idle is read the same way, without a kill.
  - When neither can be read, the worst case is charged: the script's cap `max_calls` for a backfill, every attempt of the estimate (estimate × 4) for a refresh. This replaces the old fallback of the estimate, which was below the script's real cap.
  - A refusal raised by the script itself (`<KIND> refused:`, the run lock) exits 1 and records nothing (review n8).
- **Alternatives:**
  - Read on the old connection: after a socket timeout its websocket may still carry the old statement's response; after a Ctrl-C it does.
  - Read without killing: the script keeps spending after the CLI has read `/info`, so the entry undercounts.
  - Always charge the cap after an interrupt: a Ctrl-C after 5 calls would charge 300 and block the plan.
- **Rationale:** The ledger is the plan cap's only guard, so every run that may have spent must leave an entry, and the entry must not be smaller than what the key was charged. Killing the session also makes Ctrl-C mean "stop", instead of leaving the script running until the closing websocket frees it.
- **Promotes to ADR:** no

### [48] A live batch is sized for 4 attempts per request; the run overshoots `max_calls` by at most 3 calls (F.1, review m2)

- **Decision:** In live mode a batch has at most `MAX(1, MIN(MAX_CALLS_PER_QUERY, allowance // 4))` slices, because the fetch UDF makes up to `MAX_ATTEMPTS = 4` HTTP attempts per request (`vs/cfbd_vs/http.py`; a unit test pins the Lua constant to it). A batch therefore fits the allowance even if every request is retried to its last attempt. The only overshoot left is one last slice started with 1 to 3 calls left: at most 3 calls past `max_calls` or `BACKFILL_SPENDABLE`, which the refresh reserve absorbs. Replay batches keep `MIN(allowance, MAX_CALLS_PER_QUERY)`. The bound is documented in the spec, the Lua header and the CLI help.
- **Alternatives:** documenting the overshoot only (the review's first option; up to 3 × `MAX_CALLS_PER_QUERY` = 30 calls past the cap); refusing a fetch with fewer than 4 calls left (no overshoot, but `MAX_CALLS=3` would fetch nothing).
- **Rationale:** Batching changes the number of statements, not the number of calls, so smaller batches near the end of the allowance cost run time only. With the plan cap at 300 and the demo at 217, 30 calls of overshoot mattered; 3 do not.
- **Promotes to ADR:** no

### [49] The ledger is used unless the virtual schema reads the mock connection (F.1, review m3)

- **Decision:** `make backfill` / `make refresh` decide from the virtual schema's `CONNECTION_NAME`, not from the namespace prefix: the ledger is used (when it is on) unless the virtual schema reads `CFBD_API_MOCK`. A prefixed namespace pointed at the real key is charged like the real one; the real namespace pointed at the mock on a local test deployment is not.
- **Alternatives:** keep the prefix rule and rely on the deploy refusing the real connection with a prefix: a manual `ALTER VIRTUAL SCHEMA` or a later deploy change would bypass it silently.
- **Rationale:** The money follows the connection, and the CLI already reads the virtual schema's properties for `FETCH_MODE`.
- **Promotes to ADR:** no

## Review Findings

<!-- Populated by speq-implement after code review. The plan review (pre-implementation) is resolved in review.md § Review resolution. -->

Code review `code-review.md` (2026-10-23), fixed in task F.1. Tests named are new or changed.

| # | Resolution | Tests |
|---|---|---|
| M1 | Fixed, decision [47]: session id read first; `BaseException` caught, recorded from a new session (kill of a still-running script, wait up to 30 s), re-raised; worst case `max_calls` (backfill) or estimate × 4 (refresh) when nothing is readable | `tests/cli/test_loader_cli.py`: `test_an_interrupted_run_is_recorded_from_a_new_session_and_re_raised`, `test_a_timed_out_run_is_recorded_from_a_new_session`, `test_a_session_that_does_not_end_is_noted_and_the_run_still_recorded`, `test_when_nothing_can_be_read_*` |
| M2 | Fixed: `loader.plan.scope_problem` (`LOAD_LOG` required with `CFB_CORE`) in plan validation, `Manifest.from_json`, `check_restore` and `take_snapshot`; spec `loader/snapshot-restore` and `loader/load-plan` | `test_load_plan.py`, `test_snapshot.py`, `test_snapshot_flow.py`, IT `test_restore_refuses_a_manifest_without_load_log` |
| m1 | Fixed: a batch that raises after its fetch is rolled back, its step closed `FAILED`, its spend measured, then re-raised; an unreadable `/info` after a fetch charges at least the requests | `tests/unit/test_backfill_lua.py` (new lupa harness of the whole script) |
| m2 | Fixed, decision [48]: live batches sized for 4 attempts per request; overshoot ≤ 3 calls, stated in the spec, the Lua header, the CLI help and the user guide | `test_backfill_lua.py`: `test_a_live_batch_is_sized_*`, `test_without_retries_*`, `test_replay_batches_are_not_shrunk` |
| m3 | Fixed, decision [49]: ledger unless `CONNECTION_NAME` is `CFBD_API_MOCK` | `test_loader_cli.py`: `test_a_prefixed_namespace_on_the_real_connection_uses_the_ledger` and three more |
| m4 | Fixed, decision [46]: `vs/live/loader-config.yaml` + `loader/target_config.py`; `aws` budget = ledger month cap; explicit `LOADER_CONFIG` wins and is printed; deploy warns on a budget change | `tests/unit/test_target_config.py`, `test_deploy_cli.py`, `test_loader_deploy.py` |
| m5 | Fixed: `end_job` raises when `release_lock` returns `held = false` | `test_loadlib_lua.py::test_end_job_raises_when_the_run_lost_its_lock` |
| m6 | Fixed: release only `AND "SESSION_ID" = CURRENT_SESSION`; `RUN_LOCK` takes host kinds only (`acquire_host_lock`) | `test_loadlib_lua.py`, `test_run_lock_lua.py`, IT `test_another_session_cannot_end_a_running_run` |
| m7 | Fixed: `make deploy` holds the `DEPLOY` lock around the VS and loader deploy once `RUN_LOCK` exists (`scripts.deploy.deploy_namespace`, `loader.runlock.lock_installed`) | `test_deploy_cli.py`, IT `test_a_deploy_is_refused_while_a_run_holds_the_lock` |
| m8 | Fixed, decision [45]: stop after 2 consecutive failed batches | `test_backfill_lua.py`, IT `test_one_failed_single_slice_batch_does_not_stop_the_run`, `test_systemic_failure_stops_run`, `test_failed_run_recorded` |
| m9 | Fixed: IT with key echo | IT `test_the_key_never_reaches_the_loader_logs` |
| m10, m11 | Fixed in the specs (`refresh-week` CHECK wording; `backfill` `max_calls` in calls) | — |
| m12 | Fixed | `tests/unit/test_loader_sources.py` |
| m13 | Fixed: `BACKFILL(run_profile, …)`; positional calls unchanged | `test_lua_bundle.py`: `test_no_script_parameter_is_a_reserved_word`, `test_no_result_column_is_a_reserved_word` |
| m14 | Partly: one `first_line` (`loader.runlock`), one `run_transaction` (`loader/transaction.py`), one `set_remaining` (`loadlib.lua`), one `sliced_params` and `profile_rank` (`loader/plan.py`). **Skipped:** `connect_deployment` / `SOCKET_TIMEOUT_SECONDS` in `scripts/snapshot.py` and `scripts/record.py`; a shared helper belongs in the foundation's `vs/host/target.py` | existing tests |
| m15 | Partly: `_Reader` (reads of one connection) and `_Context` (seams) extracted from `_Session`. **Skipped:** the full guard/view split, which Phase 6 task 1.9 (P6-c) does when it moves the guard into `loader/guard.py` | — |
| m16 | Fixed in the loader deploy (an empty plan is valid by `test_an_empty_entry_list_is_valid`) | `test_loader_deploy.py::test_an_empty_plan_deploys_without_stage_tables` |
| m17 | Fixed: the tests above, plus a `REFRESH_WEEK` kill and the refused run's unchanged tables | IT `test_a_killed_refresh_is_abandoned_by_the_next_one`, `test_concurrent_run_refused` |
| n1 | **Skipped:** the ledger lock is taken milliseconds after the verdict and is non-blocking; a re-check would add a second `/info` read to every run for a window that small, and the in-DB budget still caps | — |
| n2 | Fixed: `load_batch` passes `job.fetch_source`; `explain_check` refuses a manifest whose `fetch_mode` differs | `test_loadlib_lua.py::test_a_batch_refuses_a_fetch_mode_changed_during_the_run` |
| n3 | **Skipped:** the directory is private (0700), and the landed fingerprints catch a file changed after its checksum check | — |
| n4 | Fixed: the restore logs a note when the manifest's name differs from the directory | `test_snapshot_flow.py::test_a_copied_snapshot_restores_*` |
| n5, n6 | Fixed: `EXPLAIN` rows in `run-logs`; [40] and the module docstrings say "normalized plan" | — |
| n7 | **Skipped:** the names are validated against `^[A-Z][A-Z0-9_]*$`; quoting the header changes the statement text the foundation's fake and the lock probe parse, for no behavior gain | — |
| n8 | Fixed: `<KIND> refused:` from the script exits 1 and records nothing | `test_loader_cli.py::test_a_refusal_raised_by_the_script_*` |
| n9, n11, n12, n13 | Fixed: dead `PLAN_TABLES` removed; `QUOTED` comment; plan-local references in code point to specs or D-numbers; `RUN_STATUS_LENGTH`, `MAX_FAILURES` | — |
| n10 | **Skipped:** `RESTORE_SCHEMA` / `INFRA_SCHEMA` constants and placeholders are part of `render_constants`, the cross-bundle contract (H1) the Phase 3/4 bundles build on | — |
| n14 | **Skipped:** low priority per the review; the signatures are used by other phases | — |
| n15 | **Skipped:** `vs/host/deploy.py` is the foundation's table comparison; `LOAD_RUN` is only ever created by the deploy itself | — |
| n16 | Fixed: `lupa` imported directly (mypy override for the untyped package) | — |
| n17 | Fixed | IT `test_a_retried_server_error_is_charged_every_attempt` |
| n18 | **Skipped:** fine at demo scale (the review's own assessment) | — |
| H (`explain_check`) | Fixed: keys are matched inside the `slice_keys` array only | `test_loadlib_lua.py::test_a_key_outside_the_slice_key_list_*` |
