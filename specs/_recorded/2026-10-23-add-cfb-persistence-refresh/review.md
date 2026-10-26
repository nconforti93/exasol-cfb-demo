# Review: add-cfb-persistence-refresh

Reviewer: independent plan review (Claude), 2026-10-22. Read-only: no database session, no CFBD call. Inputs: `plan.md`, `decision-log.md`, the six `loader/*/spec.md`, mission, roadmap, the CFBD docs, the foundation plan and decision log, the foundation code (`vs/cfbd_vs/{adapter,plan,pushdown_sql,properties,fetch,http}.py`, `vs/host/{deploy,fetchlog,namespace,metaddl}.py`, `scripts/{deploy,grants}.py`, `sql/meta/*.sql`, `tests/it/harness.py`), `docs/spikes/s2-platform-probe.md`, the skills `exasol-database`, `exasol-udfs`, `exasol-export` and `exasol-import`, and `exapump 0.13.0 --help` (local, no connection).

## Verdict: approve with changes

The architecture holds up: Lua `EXECUTE SCRIPT` entry points, read-through left on with `REQ_SOURCE <> 'STORED'`, stage-then-swap per batch, slice templates compiled by the adapter's own planner, and a zero-call `EXPLAIN` drift check. It fits the foundation contracts, and the probe-first approach covers most platform risk. I found **no blockers**. Ten **major** findings need a change in the plan or the specs before implementation starts. Most are small edits; three of them protect the owner's 400-call cap.

`speq plan validate add-cfb-persistence-refresh` passes (6 delta specs).

Major findings at a glance:

| # | Theme | Finding |
|---|---|---|
| M1 | Quota | The in-database guard allows about 800 calls in October; only the local ledger enforces the owner's 400 |
| M2 | Quota | One failing slice discards every other slice already fetched in its batch, and the next run pays for them again |
| M3 | Correctness | The failure path and the lock release need `pquery`; a failed or killed run in a live session blocks all later runs |
| M4 | Correctness | Batches are grouped by table, but a batch query can only express one plan entry |
| M5 | Correctness | Restore breaks `LOAD_RUN`: identity counter, the snapshot's own `RUNNING` row, and the restore's lock row |
| M6 | Quota | Slice-key drift after a restore or a catalog change makes `BACKFILL` re-buy everything, and the drift check cannot see it |
| M7 | Feasibility | Every platform fact was probed on single-node nano 2026.2 local; the first loader run on 3-node AWS is a paid run |
| M8 | Data | `RANKINGS_POLLS` and `RANKINGS_POLLS_RANKS` come from two requests in two transactions but are joined by position |
| M9 | Interface | `REFRESH_WEEK(season, week)` cannot tell postseason week 1 from regular week 1, and Phases 3–5 inherit that step contract |
| M10 | Testability | Lua code and view behaviour have no test until groups E/F, after the code that depends on them |

---

## 1. Quota safety

### M1 (major): the in-database budget does not reflect the owner's 400-call authorization

**Evidence.**
- The October key is Academic, 3,000 calls (`docs/cfbd-api-reference.md:41`).
- `PROJECT_BUDGET = LEAST(MONTHLY_LIMIT, 1000)` and `BACKFILL_SPENDABLE = REFRESH_SPENDABLE − 150` (`loader/quota/spec.md:14-16`). With about 50 calls used, a backfill may spend about 800 calls this month.
- The 400 cap exists only in `vs/live/ledger.yaml` and `cache/live-calls.jsonl` (`plan.md:291-298`), and only `make backfill` / `make refresh` consult it (`plan.md:542-547`).
- Neither the ledger nor the cap applies to:
  - `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, NULL)` typed into any SQL client on AWS;
  - a rehearsal `REFRESH_WEEK` run by the presenter;
  - a wrapper that crashes after the script but before appending to the ledger.
- The ledger also cannot see calls made outside it (foundation runs, the CBBD shared pool).

**Fix.**
1. Enforce the month cap against `/info`, not against the local file: refuse when `USED_CALLS + estimate > months[YYYY-MM]`. This works because `/info` is free and its period is the calendar month (`resetAt` 2026-11-01). Keep the jsonl as the audit trail and the per-plan cap.
2. For the October live group, deploy AWS with `monthly_call_budget: 400` and `refresh_reserve: 50`, so the in-database guard matches the authorization even when someone bypasses `make`. Restore the defaults afterwards. Add this to 9.0, and to 9.6 for the reset.
3. Have 9.0 assert that `QUOTA.BACKFILL_SPENDABLE ≤` the ledger room.

### M2 (major): a failing slice wastes the rest of its batch on every retry

**Evidence.**
- `vs/cfbd_vs/fetch.py:11`: "One failing slice fails the group: queued slices are cancelled, running ones finish."
- With `MAX_PARALLEL_FETCHES` 6 and batches of up to `MAX_CALLS_PER_QUERY` 10, up to 5–9 sibling slices are paid for and then lost, because the `INSERT … SELECT` statement fails as a whole.
- `vs/cfbd_vs/http.py:27`: the failing slice is tried up to 4 times. CFBD may count each attempt.
- The batch order is fixed (`backfill/spec.md:14-22`), so a slice that fails every time (a 4xx or a flattening overflow on one old season) fails the same batch on each re-run. Each re-run wastes the same calls again, and the queue never moves past it. That breaks "resumable" in practice.
- The allowance counts slices, "Every fetched slice counts as one call" (`backfill/spec.md:26`), not attempts. The run can therefore spend more than its allowance.

**Fix.**
- After a failed batch, the run (or the next run, from the `REFRESH_LOG` detail) retries that batch's slices one at a time, so the good slices commit and the failing slice is isolated.
- Add a quarantine: a slice that failed N runs in a row (for example 2) is skipped with `BLOCKED_BY = 'FAILED: <error>'` in `LOAD_STATUS` until someone clears it.
- In live mode, read `/info` (free) after each batch, and decrement the allowance by the real `usedCalls` delta rather than by the slice count. `REMAINING_BEFORE/AFTER` already exist for this.
- Add a scenario: "a slice that always fails is fetched at most twice and does not block the slices queued after it".

### M6 (major): slice-key drift after a restore or a catalog change re-buys everything

**Evidence.**
- Foundation [5] (`add-cfbd-vs-foundation/decision-log.md:174`, `plan.md:198`): changing an explicit default changes the slice keys, and "the loader must migrate".
- The drift check compares the deployed templates with the adapter's current keys (`decision-log.md:138`). After a catalog change, both sides use the new keys, so the check passes.
- Restore refuses only on column differences, not on `catalog_hash` (`snapshot-restore/spec.md:33`).
- After restoring an older snapshot, or deploying a catalog whose defaults changed, every stored `LOAD_LOG` key is an orphan. `BACKFILL_QUEUE` then lists the whole profile as missing, and `BACKFILL(…, NULL)` spends up to `BACKFILL_SPENDABLE`. That is exactly what D11 forbids.

**Fix.**
- `LOAD_STATUS` reports **orphan slices**: `LOAD_LOG` rows of a planned table that match no planned key.
- `BACKFILL` and `REFRESH_WEEK` refuse to fetch a table that has orphans, naming the migration problem.
- Restore warns, or refuses without `FORCE=yes`, when `manifest.catalog_hash` differs from the deployed catalog.
- Add one scenario per guard.

### Minor quota points

- **m1.** Current-season slices are never final, so any `CFBD_VS` query on 2026 `GAMES` (demo beat 2, the inspector, the chat) always makes a live call (`pushdown_sql.py:184-193`). Rationale [4] (`decision-log.md:108-110`) argues that read-through avoids re-fetches, but that only holds for final slices. Document in the user guide and in 8.2 that the pages and the chat read current-season data from `CFB_CORE`, never from `CFBD_VS`.
- **m2.** The fallback for probe 1.1b, "`make backfill` looping" (`plan.md:415`), must stop on the first failed call and on any call that makes no progress. Otherwise it becomes a paid retry loop.
- **m3.** The live budget arithmetic is right: 3 + 2 + 14 + 157 + 28 + 26 = 230, plus 5, plus 65 = 300; 300 + 50 = 350 ≤ 400 (`plan.md:187-196`, `310-321`). But 9.0 requires ledger room ≥ estimate + 70 = 300 (`plan.md:623`), so any earlier call by this plan (a retried 9.2) blocks 9.4. State that 9.4 may use the reserve, or lower the margin to the refresh's 5.
- **m4.** Schedule risk: today is 2026-10-22, and group 9 must finish by 2026-10-31 (A6) after about 30 tasks and two shared-DB windows. The ledger refuses safely, so this costs no calls. Say in the plan whether the owner prefers a November entry now, given that the quota resets on 2026-11-01.

---

## 2. Correctness and feasibility on Exasol

### M3 (major): the failure path needs `pquery`, and the lock can stick in a live session

**Evidence.**
- Skill `exasol-udfs`, `references/lua-execute-scripts.md:18`: `query()` "Raises a Lua error and **terminates the script**". Line 19: `pquery()` returns `success, result` "– the script keeps running".
- The plan's error path, "any error: ROLLBACK; mark step + run FAILED; COMMIT; error(…)" (`plan.md:105`, `256`, `525`), cannot run after a failing `query()`. The plan never mentions `pquery` or `pcall`.
- The abandoned-run rule frees a `RUNNING` row only when "its session no longer exists" (`run-logs/spec.md:28`). Three cases leave the lock stuck with the session still alive:
  - a failed script in the presenter's still-open DBeaver session;
  - a `KILL STATEMENT`;
  - a Ctrl-C.

  Every later run is refused, and so are snapshot and restore, until that client disconnects.
- Probe 1.1b asks what happens to uncommitted work after `error()`, but not whether a failed statement can be caught.

**Fix.**
- Run every statement in `load_batch` and in the step runner through `pquery`, and wrap each entry function in Lua `pcall` for non-SQL errors.
- Add to probe 1.1b: "a failing `pquery` inside an open transaction: is the transaction still usable for `ROLLBACK` + log + `COMMIT`?"
- Extend the abandoned rule:
  - a `RUNNING` row owned by the *calling* session is always stale, because one session cannot run two scripts at once;
  - a `RUNNING` row whose session is alive but idle (`EXA_ALL_SESSIONS.STATUS = 'IDLE'` with no running statement) is also stale. Probe 1.1j records the column.
- Add a scenario: "after a failed refresh, the same session can start the next run".

### M4 (major): batches must be grouped by plan entry, not by table

**Evidence.**
- "A batch is the longest run of consecutive queue entries of the same table" (`backfill/spec.md:22`, `decision-log.md:233`).
- But the batch filter depends on the entry: slicing, plus the `FIXED_FILTER` prefix (`plan.md:259`).
- Two entries of the same table can sit next to each other in the queue:
  - the A4 fallback, `GAMES` once per `classification` with equal priority, is a likely S1 outcome (`plan.md:198`);
  - `CONFERENCES_AFFILIATIONS` range + season.

  Either way the result is a batch that no single `WHERE` clause can express.
- In addition, `LOAD_SLICES` does not define the `SEASON` of a `season_range` slice. That decides its queue position and whether `from_season` skips it ("slices without a season are always included", `backfill/spec.md:9`).

**Fix.**
- Make the batch key `ENTRY_ID` (and, for `season_week`, the season) in the plan, the backfill spec and `decision-log [12]`.
- State that a `season_range` slice has `SEASON = NULL` (or `SEASON_TO`), and pin the choice with a scenario.

### M5 (major): restoring `LOAD_RUN` and `REFRESH_LOG` breaks the run log

**Evidence.**
- `LOAD_RUN.RUN_ID … IDENTITY` (`plan.md:218`).
- Restore inserts explicit `RUN_ID`s into a freshly deployed table. The grammar has `ALTER [COLUMN] col SET IDENTITY [int]` (skill `exasol-database`, `exasol-grammar.md:302-303`), and nothing in the plan advances the counter. New runs therefore reuse restored ids, which collide in `LOADED_BY = 'BACKFILL#42'`, in the `FETCH_LOG` context and in the `REFRESH_LOG` joins.
- The snapshot runs under its own `RUNNING` lock row (`snapshot-restore/spec.md:38`), so the snapshot contains that row, and every restore resurrects a `RUNNING` row from a dead session.
- The restore's own lock row lives in `LOAD_RUN`, the table that the one-transaction swap replaces (`snapshot-restore/spec.md:35`, `:80`). This contradicts "apart from the restore's own `LOAD_RUN` row".
- Restoring `FETCH_LOG` from AWS onto local also rewrites the quota attribution. The guard is unaffected, because it uses `/info`.

**Fix.** Preferred (YAGNI):
- Snapshot only what the roadmap and D11 name: `CFB_CORE` + `LOAD_LOG`, plus `FETCH_LOG` for the inspector.
- Drop `LOAD_RUN` and `REFRESH_LOG` from `snapshot.meta_tables`.

Otherwise:
- export them `WHERE RUN_STATUS <> 'RUNNING'`;
- re-insert the restore's own row after the swap;
- `ALTER TABLE … ALTER COLUMN RUN_ID SET IDENTITY <max+1>` inside the restore transaction;
- add the identity check to the restore scenario.

### M7 (major): the AWS 3-node target is never exercised before money is spent

**Evidence.**
- Probes 1.1/1.2 and all of the foundation's verified facts ran on local nano 2026.2, 1 node (`plan.md:406-439`, `s2-platform-probe.md:5`).
- The foundation's own live group deploys to local Personal (foundation tasks 9.2), never to AWS.
- `~/.exasol/personal/deployments/default/deployment.json` shows `clusterSize 3` with three node DNS names. Its DB version is not recorded in the plan.
- Group 9.0 (`plan.md:617-623`) deploys and checks DNS and the quota. The first time the loader's Lua, transactions, lock, `EXPLAIN` subselect and `[21]` guard run on AWS is the paid `MAX_CALLS=20` run in 9.2.

**Fix.** Add a zero-call AWS smoke to 9.0:
- record `SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'`;
- `EXECUTE SCRIPT …BACKFILL('demo', NULL, 0)`, which registers the one gap and spends 0 calls;
- an `EXPLAIN VIRTUAL` of one demo batch from the Lua (`explain_check` only);
- a concurrent-lock check from two sessions;
- one `make snapshot` of a `PROBE_` table with exapump and certificate pinning (see m11).

Gate 9.1 on it.

### Exasol claims checked against the skills

| Plan claim | Skill evidence | Result |
|---|---|---|
| Lua scripting programs are the only in-DB multi-statement mechanism; `CREATE [LUA] SCRIPT … RETURNS TABLE` (`plan.md:31`, `[1]`) | `exasol-grammar.md:382-389`: `"CREATE" ["OR" "REPLACE"] ["LUA"] "SCRIPT"`, `["RETURNS" "TABLE" \| "RETURNS" "ROWCOUNT"] "AS"`. `lua-execute-scripts.md:3`: "the Exasol-native way to orchestrate" | **Confirmed** |
| No `CALL` statement; `CALL` is reserved (`plan.md:32`, `[2]`) | `exasol-reserved-keywords.md:40` lists `CALL`; the grammar has no statement starting with `"CALL"` | **Confirmed** |
| `EXECUTE SCRIPT s(args) [WITH OUTPUT]`; NULL arguments | `exasol-grammar.md:608-615`: `script_param = expr \| ARRAY(…)`. `lua-execute-scripts.md:53`: `WITH OUTPUT` discards the return table | **Confirmed.** Never run the demo line `WITH OUTPUT`, or the timeline disappears |
| Untyped script parameters `BACKFILL(profile, from_season, max_calls)` | `lua-execute-scripts.md:38`: parameters are untyped names; typing them is a syntax error | **Confirmed** |
| `exit(rows, "<column defs>")` returns the table (probe 1.1a) | Silent: the skill only says "the script's own return value (a table …)" | Unverified; probe 1.1a covers it |
| `query("COMMIT")` / `ROLLBACK` inside a script; catching failures | Grammar has `COMMIT`/`ROLLBACK`. `lua-execute-scripts.md:18-19`: `query()` terminates the script, `pquery()` catches | **Contradicts the error path as written** (M3) |
| Nested `EXECUTE SCRIPT` via `query()` (probe 1.1e, step registry) | `lua-execute-scripts.md:94`: "Nested `query`/`pquery` calls to `EXECUTE SCRIPT` are possible but have a recursion limit of 255" | **Confirmed.** Still give 1.1e a fallback (`import()` of a step module) |
| Bind syntax: values `:name`, identifiers "quoted" `::name` (`plan.md:519`) | `lua-execute-scripts.md:20`: `query([[INSERT INTO ::t VALUES :v]], {t=…, v=…})` | **Partly.** `::name` is plain text substitution, not quoting. Call it "inserted verbatim (constants only)" and quote identifiers in the bundler (m6) |
| `SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL …)` from Lua (probe 1.1c) | Grammar: `explain_virtual_statement = "EXPLAIN" "VIRTUAL" subquery`; `from_item` does not list `EXPLAIN` as a subquery | **Silent.** The fallback is simpler than the plan's: `query("EXPLAIN VIRTUAL …")` returns the result set directly, as `vs/host/fetchlog.py:170-179` does |
| Lua parses the manifest JSON | `udf-java-lua.md:242-251` lists `lua-cjson` for Lua *UDFs*; scripting programs are not mentioned | **Silent.** Add "is `cjson` available in a scripting program?" to probe 1.1, or compare the keys in SQL |
| Lua runs with the caller's rights ([22]) | Silent | Probe 1.1f covers it |
| `MERGE` into a PK table (probe 1.1k) | `exasol-grammar.md:167-170`: `MERGE INTO …` exists | Confirmed syntax. Add to 1.1k a `USING` source that reads the target (`LOAD_SLICES` reads `LOAD_LOG`) |
| `IDENTITY` column (`LOAD_RUN`) | `exasol-grammar.md:237`, `302-303` | Confirmed; the counter reset is needed (M5) |
| Reserved words avoided (`PROFILE`, `FINAL`, `ENABLED`, `ROWS`, `LOG`, `VALUE`, `REFRESH`, `STATE`, `CURRENT`) | Checked against `exasol-reserved-keywords.md`; none of the new table or column names (`SEASON`, `WEEK`, `DETAIL`, `INFO`, `RUN_ID`, …) is reserved | **Confirmed** |
| Native `EXPORT` writes only CSV/FBV; native Parquet `IMPORT` reads only from S3; local Parquet goes through `exapump export`/`upload` | `exasol-export/references/export.md:89`, `131-134` (`--format parquet`, `--compression zstd`); `exasol-import/references/import.md:92-96`, `119`, `126-131` | **Confirmed** |
| exapump DSN through `EXAPUMP_DSN`, `--certificate-fingerprint` | Skills silent (profile-based only). `exapump 0.13.0 export/upload --help`: `-d, --dsn [env: EXAPUMP_DSN=]` and `--certificate-fingerprint` (SHA-256 of the DER certificate) | **Confirmed by the CLI.** The skills route through `~/.exapump/config.toml`, which [17] rightly avoids |
| `FROM LOCAL` is unusable from Lua | `import.md:96`: "not from UDF scripts or Lua scripts" | Consistent: snapshot/restore run from the host |

### Minor feasibility points

- **m5.** Read the quota in its own transaction. `QUOTA` reads `FETCH_LOG`, which the batch writes. Once the Phase 6 backend also writes `FETCH_LOG` (foundation [6]), a batch transaction that read the quota can hit a transaction collision after its HTTP spend. Read `QUOTA` and the config, `COMMIT`, then start the batch.
- **m6.** Make every rendered identifier in the Lua bundle quoted, as the skill's "always double-quote" rule and `pushdown_sql.py:27` require. `::name` inserts text as given.
- **m7.** `loader/load-plan/spec.md:36` and `LOAD_SLICES` (`plan.md:228`) use calendar weeks by number only.
  - `CALENDAR` has `WEEK` + `SEASON_TYPE` (catalog), so regular week 1 and postseason week 1 become two rows with the same slice key (double-counted calls) and the wrong week-end date. A `season_week` week-1 slice could also turn final in September and never pick up January postseason plays.
  - `full` only, but say which day ends a week number: the latest end date across season types.
  - A `season_week` entry without a stored calendar (for example `PLAYS` 2001, calendar from 2002) silently plans nothing. Show it in `LOAD_STATUS.BLOCKED_BY`.
- **m8.** Placeholders: the plan uses `{META}`, `{CORE}`, `{VS}` (`plan.md:202`, `227`), but `vs/host/metaddl.py:8` renders `{META_SCHEMA}`. Pick one; registering the new DDL in `metaddl.py` (task 3.1) needs `{META_SCHEMA}` or a renderer change.
- **m9.** `CALENDAR` for the current season is `static` (`plan.md:171`), so it is final on load. If the October 2026 load lacks the postseason weeks, `REFRESH_WEEK(2026, <bowl week>)` fails `CHECK` with no fetch path other than a manual `IS_FINAL` reset. Either finalize the current season's calendar at `season_final_after`, like `weekly`, or document the reset in 8.1.
- **m10.** Assert at batch start that `CFB_STAGE.<T>` is empty, and include `CFB_STAGE` in the harness truncation. Today `reset_state` truncates only `CFB_CORE`/`CFB_META` (`tests/it/harness.py:464-471`).
- **m11.** Multi-node pinning on AWS:
  - Each AWS node has its own DNS name and possibly its own `tlsCert` (`deployment.json` `nodes.n11..n13`).
  - exapump's native transport may open connections to other nodes than the one pinned with `--certificate-fingerprint`. Probe 1.2 runs only on 1-node local.
  - pyexasol pinning against a self-signed certificate needs `check_hostname=False` with `ca_certs`, or the DSN fingerprint form.
  - Cover all three in the M7 smoke.
- **m12.** `LOADED_AT` must be UTC. The foundation harness writes `CURRENT_TIMESTAMP`, which is session-local (`harness.py:384`). Probe 1.1l should fix one expression (for example `CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')`) that both the views and the Lua use.

---

## 3. Consistency with the foundation code and contracts

| Contract | Plan usage | Status |
|---|---|---|
| Slice key grammar, `build_slice_key` (`vs/cfbd_vs/plan.py:65-80`) | Templates compiled with sentinel values (`plan.md:469-475`); keys sorted by parameter name, explicit defaults included (`seasonType=both`, catalog `GAMES`, `RANKINGS`, `METRICS_WP_PREGAME`) | Consistent. The unit test against `build_slice_key` is the right guard |
| `LOAD_LOG` serving rule (foundation [5]; `pushdown_sql.py:184-193`, `217-222`) | Batch slices are missing or non-final, so the stored branch returns nothing and the fetch branch's `NOT EXISTS` keeps them | Consistent |
| `REQ_SOURCE <> 'STORED'` | `excludes_source` only prunes the *fetch* branch (`pushdown_sql.py:101-119`), so the stored branch is still emitted and the budget still applies (`adapter.py:204-206`). The manifest `sources` is `["STORED","LIVE"]` | Consistent. The batch ≤ `MAX_CALLS_PER_QUERY` is required, and the plan has it |
| Manifest `sources` + `fetchlog.build_fetch_log_rows` (`fetchlog.py:9-15`, `144-166`) | With a fetch source in `sources`, a planned slice without staged rows is logged as fetched with 0 rows, which matches the plan's "ROWS_RETURNED 0 + fetch source" | Consistent. `test_fetch_log_equals_reference_capture` is the right pin |
| [21] empty-group guard (`pushdown_sql.py:225-226`) | Probe 1.1d re-checks it inside a script | Consistent |
| `FETCH_LOG`/`LOAD_LOG` DDL (`sql/meta/*.sql`) | Unchanged; `LOADED_BY VARCHAR(64)` fits `REFRESH_WEEK#<18 digits>`; `CONTEXT VARCHAR(200)` fits | Consistent |
| Properties (`properties.py:195-234`) | Reads `FETCH_MODE`, `MAX_CALLS_PER_QUERY`, `API_FEATURES`, `READ_THROUGH`; never `ALTER`s them | Consistent. The source-scan unit test is good |
| Namespace (`namespace.py`) | Adds `stage_schema`, `restore_schema` | Fine. The real namespace's owner is `SYS` ([20]); scripts run as caller (sys) |
| sys ownership [20] / reader role | `make grants` gives only `SELECT ON CFBD_VS`; the loader needs no reader grant | Consistent. Note in 8.1 that the stage demo runs as `sys` |
| Child tables (foundation [8]) | `RANKINGS_POLLS` (has `SEASON`, `SEASON_TYPE`, `WEEK`, `POLL`) and `RANKINGS_POLLS_RANKS` (has `POLL`, but no week) joined by `REQ_SLICE`, `ROW_NO`, `POLLS_NO` (A5) | See M8 |

### M8 (major): the two rankings child tables can drift apart by position

**Evidence.**
- `RANKINGS_POLLS_RANKS` has no season, season type or week columns (catalog). Its week comes only from the positional join to `RANKINGS_POLLS` on `ROW_NO`/`POLLS_NO` (A5, `decision-log.md:400-402`).
- The two tables are fetched by **two requests in two batch transactions** (`[5]`, foundation non-goal). If CFBD publishes a poll between the two calls, the arrays shift: the ranks join the wrong week or poll, silently. On Tuesday refreshes the chance is real.
- Between the two commits, readers also see a new parent with old children.

**Fix (pick one).**
1. Preferred, while the catalog is still open before foundation 9.4: add `children: RANKINGS_POLLS_RANKS: {parent_keys: [season, seasonType, week]}` (the mechanism exists, foundation `plan.md:165-166`). Then drop `RANKINGS_POLLS` from `demo`, which saves 13 + 1/week calls and keeps a natural key.
2. Otherwise, load both tables of one endpoint in one transaction. After the second fetch, check that every `(REQ_SLICE, ROW_NO, POLLS_NO, POLL)` agrees between the two tables, and fail the batch if not. Add a scenario.

---

## 4. Demo fit and scope

- **Phase 2 coverage.** Load plan, profiles and priority, `REFRESH_WEEK`, logs, quota view, resumable `BACKFILL`, snapshot/restore and the rehearsal reset are all delivered. Dropping the Docker volume (D17) and private S3 is correctly logged ([16], [19]). Deferring `PROMOTE()` [18] is justified.
- **Beat 3.** `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N)` returning a timeline is honest Exasol and demos well.

### M9 (major): the step contract cannot address postseason weeks

**Evidence.**
- Registered steps get `(run_id, season, week)` (`refresh-week/spec.md:23`, `[14]`).
- The calendar has regular week 1 *and* postseason week 1 (`refresh-week/spec.md:74`; catalog `CALENDAR.SEASON_TYPE`). The demo season runs to the 2027-01-25 title game.
- Model, prediction and simulation steps (Phases 3–5) need to know which week. Because this plan fixes the step interface, changing it later means touching every step script.

**Fix.**
- Add `season_type` now: `REFRESH_WEEK(season, week, season_type)` (positional; a NULL third argument means `regular`). Pass it to steps as `(run_id, season, week, season_type)`.
- Alternatively, define one week numbering that continues into the postseason, and document it.
- Add a scenario for postseason week 1.

### Minor demo points

- **m13.** The timeline shows only `CHECK` and `FETCH` (5 slices, 5 calls in one row). Roadmap beat 3 promises "fetch → merge → … with seconds and calls per step". Give the timeline one row per fetched table (`FETCH GAMES … 1 call, 2.1 s`), using step numbers 2–9 or sub-rows. That makes the beat visibly "SQL → HTTP", at no extra cost.
- **m14.** The refresh is not atomic across tables: each table commits on its own, so a failure in table 4 leaves tables 1–3 refreshed. That is acceptable, but say it in the refresh spec Background, because the app (Phase 6) will show a mixed state.
- **m15.** Task 8.4 updates the mission's commands and wording, but not the constraint "about 200 calls once … about 5 per weekly refresh" (`specs/mission.md`, Constraints). Add "230 / 5".

### YAGNI candidates

- **m16.** `season_week` slicing, week finality with grace days, "weeks up to `week`" in `REFRESH_WEEK`, and task 2.4 (all `full` entries) serve only the optional archive. Nothing in the demo or in group 9 runs them, and m7 shows they are where the subtle bugs live. Options:
  - Keep the `full` mechanism, but limit 2.4 to the per-season `full` entries (for example `DRIVES`, `CALENDAR` 2002–2012).
  - Defer `season_week` to the plan that first runs `PROFILE=full`.
  - At minimum, fix m7.
- Keep the step registry, targets/TLS, the ledger, fingerprints and `LOAD_STATUS`: each has a consumer in this plan or the next one.

---

## 5. Testability

### M10 (major): Lua and view behaviour is first tested after the code that depends on it

**Evidence.**
- Views (3.2) and `loadlib.lua` (4.1, which holds `load_batch`, the lock, `explain_check` and `fail`) are in group B with rendering and bundle unit tests only (`plan.md:502`, `526-529`).
- Their behaviour is first executed in group E (4.2/4.3).
- The quota and run-log tests (7.2) come in group F, after 4.2/4.3, which already depend on `QUOTA`, the lock and the abandoned check.
- 4.2 and 4.3 run in parallel, yet both will edit `loadlib.lua`.

**Fix.**
- Move `test_quota_it.py` and the lock/abandoned tests of `test_run_logs_it.py` into group E, ahead of 4.2/4.3 (they need only 3.3 + 6.1).
- Make 4.1's `load_batch` and lock part of 4.2: 4.2 owns `loadlib.lua`, and 4.3 starts after 4.2.

### Other testability points

- **Scenario mapping.** All 59 scenarios map 1:1 to a named test (`plan.md:677-736`). The harness can do what they need:
  - scripted `/info` and per-path 500s/delays (`tests/mock_cfbd/server.py:21-25`);
  - the call counter (`Mock.hits`);
  - temporary property switches for "read-through off" (`harness.py:474-496`);
  - replay recordings (`write_recordings`).

  Zero CFBD calls is credible: the mock is reached by IP and the `CFBD_API` guard stays.
- **m17.** The total row's "calls still needed" exists only inside the `DETAIL` string (`plan.md:142-143`), but the scenarios assert it numerically (`backfill/spec.md:42`, `96`, `103`). Add a `CALLS_NEEDED DECIMAL(9,0)` result column so the tests do not parse prose.
- **m18.** `LOAD_RUN.SLICES` vs `REFRESH_LOG` sums: are registered gaps counted? `run-logs/spec.md:37` requires the run totals to equal the step sums, but gaps have no step row. Specify that gaps are excluded from `SLICES`, or give gap registration its own `REFRESH_LOG` row.
- **m19.** The restore IT drops and redeploys the `TEST_` namespace. That drops the fetch UDF and its script-bound grant (foundation probe (o)), so the test must re-run the harness's grant step. Its 1.5–2 s mock admin calls plus 4-attempt retries in the failure tests (about 7 s each) make `make test-it` noticeably slower. Budget for that, and consider a module-scoped deploy per plan variant.
- **m20.** `max_calls = 0` is called "report only" (`backfill/spec.md:10`), yet it writes gaps to `LOAD_LOG` and a `LOAD_RUN` row (`:98-104`). Either rename it ("register gaps, fetch nothing"), or use `BACKFILL_QUEUE` for the report and make 0 write nothing.

---

## 6. Spec quality

- **m21. RFC 2119.** Negative requirements are written as "no X MUST …" / "neither … MUST …", which reads as a positive MUST. Use "MUST NOT". `SHALL` is mixed in for the same meaning ("no request SHALL be sent"); use `MUST NOT` throughout. Occurrences:
  - `backfill/spec.md:49`, `72`, `102`, `120`, `156`
  - `refresh-week/spec.md:40`, `48`, `64`, `77`, `86`
  - `run-logs/spec.md:58`, `66`
  - `snapshot-restore/spec.md:50`, `57`, `96`, `103`
- **m22.** `quota/spec.md:60-62`: "one unregistered gap" is not counted, but nothing in the scenario says so. Add "AND the gap MUST NOT count".
- **m23.** `load-plan/spec.md:20` allows several entries per table; add the M4 batch rule and the `season_range` season value to the Background.
- Otherwise the scenarios are concrete, numeric and testable. The Background sections carry the rules, and the scenarios are examples, which is the right split.

---

## What is good and should stay

- **`EXECUTE SCRIPT` instead of a `CALL` preprocessor** ([1], [2]). It is honest Exasol syntax, checked against the grammar, and it returns the timeline as a result table.
- **Read-through stays on, with `REQ_SOURCE <> 'STORED'`** ([4]), instead of flipping global properties. It is crash-safe, concurrency-safe, and backed by a source-scan test.
- **Stage then swap, one transaction per batch, with `LOAD_LOG` and `FETCH_LOG` in the same transaction** ([5]). The HTTP time stays outside the `CFB_CORE` write window, and a failed batch leaves no trace.
- **Slice templates compiled by the adapter's own planner, plus the zero-call `EXPLAIN` check** ([6]). The key grammar stays in one place.
- **Re-fetching the non-final season slice instead of adding week slices** ([9]). It respects exact-match read-through without double storage.
- **Gaps stored as final empty slices**, so read-through serves them at zero calls.
- **Set logic in inspectable views** ([3]). `SELECT * FROM CFB_META.BACKFILL_QUEUE` *is* the estimate, which is good for the presenter.
- **The quota view on free `/info`, with a refresh reserve the backfill cannot touch** ([11]). The arithmetic is checked by scenarios.
- **Probe-first with stated fallbacks and an escalation gate** (1.1, 1.2, [25]).
- **The rankings correction (230, not about 200)** and the explicit A4 cost of the per-classification fallback.
- **Snapshot privacy**: outside the repo and OneDrive, 0700/0600, never overwritten, fingerprinted, atomic restore. TLS pinning instead of `CERT_NONE` for AWS ([17]).
- **A per-plan and per-month live ledger, with `CONFIRM=yes` and `/info` before and after.** Keep it, and add the `/info`-based month check (M1).

---

## Review resolution

Planner revision, 2026-10-22, following the orchestrator's answers. Changed: `plan.md` (rewritten), `decision-log.md` (rewritten: [1]–[30]) and all six `loader/*/spec.md`. `speq plan validate add-cfb-persistence-refresh` passes. No finding was rejected; three were resolved with a deliberate variation, marked **(varied)**.

### Majors

| # | Resolution | Where |
|---|---|---|
| M1 | The month cap is enforced in the database against `/info` `USED_CALLS`: `PROJECT_BUDGET` caps the key's whole monthly use. `make deploy LOADER_CONFIG="monthly_call_budget=400,refresh_reserve=50"` sets the October values on AWS (9.0), and 9.6 restores the defaults. The ledger stays as the second guard; its month check uses `/info` used calls + estimate. 9.0 asserts `BACKFILL_SPENDABLE ≤` ledger room | plan Key Interfaces "Budget guards", tasks 1.4, 3.3, 5.1, 9.0, 9.6; quota spec (override scenario); decision [11], [20] |
| M2 | A failed multi-slice batch is retried slice by slice: good slices commit, the failing slice gets one failure per run in the new `CFB_META.LOAD_FAILURES`, is then batched alone after all other candidates, and is quarantined after `quarantine_after` (2) runs. If every slice also fails alone, the run stops (`FAILED`). The allowance and `CALLS` use the measured `/info` delta. **(varied)** The bound is "its batch once, then once alone per run, for two runs" rather than "at most twice", so a transient failure does not quarantine at once | backfill spec (4 new scenarios), run-logs spec, `LOAD_FAILURES` DDL, `BACKFILL_QUEUE`, task 4.2; decision [12], [24] |
| M3 | Every Lua statement runs through `pquery`, each entry function through `pcall`; rollback, log and commit happen before the error is re-raised. Stale-lock rules: session gone, the caller's own session, or an idle session for script runs (`BACKFILL`, `REFRESH_WEEK`). **(varied)** Snapshot and restore rows run from often-idle host sessions, so for them only "session gone" counts. Probe 1.1b covers a failing `pquery` in an open transaction; 1.1j covers the idle column after `KILL STATEMENT` and an interrupt. New scenario "After a failed or interrupted run, the next run starts" | run-logs spec, tasks 1.1, 4.1 (no bare `query(`), 4.2; decisions [3], [13], A1, A9 |
| M4 | The batch key is `ENTRY_ID`. `none` and `season_range` slices have `SEASON` NULL, so they are queued first and `from_season` never skips them. Pinned by scenarios in load-plan ("Range and seasonless slices have no season") and backfill ("Slices without a season come first…", "A batch never mixes plan entries") | specs, plan Key Interfaces; decisions [5], [7], [12] |
| M5 | The snapshot scope is `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`. `LOAD_RUN`, `REFRESH_LOG` and `LOAD_FAILURES` stay per deployment. The restore records its own `RESTORE` row after the swap | snapshot-restore spec, YAML `snapshot.meta_tables`; decision [15] |
| M6 | `LOAD_STATUS` reports orphan slices: loader-written `LOAD_LOG` rows that match no planned key. `make deploy` warns, and `BACKFILL`/`REFRESH_WEEK` refuse to fetch a table with orphans until they are migrated or deleted. Restore refuses a different catalog hash without `FORCE=yes`; the hash covers generated content (foundation [25]) | load-plan spec (orphan scenario), snapshot-restore spec (`FORCE` scenario), tasks 3.2, 3.3, 4.2, 4.3, 5.2; decision [23] |
| M7 | 9.0 runs a zero-call AWS smoke test that gates 9.1: DB version; re-run of probe 1.1 (read-only and lock items) and 1.2 with `--target aws` (exapump and pyexasol pinning on three nodes); the `BACKFILL('demo', NULL, 0)` dry run (gap, `EXPLAIN` of every pending batch, unchanged `/info`); a two-session lock check | task 9.0; decision [26] |
| M8 | Handled on the foundation side (prerequisite P1): `RANKINGS_POLLS_RANKS` gets `SEASON`/`WEEK`/`POLL` parent keys and the catalog is regenerated. The demo loads only that table: 217 calls once and 4 per refresh, instead of 230 and 5. Task 2.3 gates on the keys. Open recommendation to the foundation: add `SEASON_TYPE` (final poll = postseason week 1) | plan Key Interfaces (P1, budget), Dependencies, task 2.3; decision [10], [27] |
| M9 | `REFRESH_WEEK(season, week, season_type)`, where `'regular'`/`'postseason'` and NULL mean regular. Registered steps get `(run_id, season, week, season_type)`. `CHECK` validates (`SEASON`, `WEEK`, `SEASON_TYPE`) against the calendar. New scenario "Postseason weeks are addressed by season type" | refresh-week spec, task 4.3; decision [14] |
| M10 | The views are tested in group E before any Lua (7.1 load plan, 7.2 quota). 4.1 is the bundler only. 4.2 owns `loadlib.lua` together with the backfill and the lock and failure tests. 4.3 starts after 4.2. 7.3 (remaining run logs) runs after 4.3 | plan Implementation Tasks and Parallelization (groups A–J); decision [28] |

### Minors

| # | Resolution |
|---|---|
| m1 | Requirement "current-season reads come from `CFB_CORE`", in 8.1/8.2 and decision [4] |
| m2 | The fallback loop of probe 1.1b stops on the first failed call and on any call without progress |
| m3 | 9.0 requires ledger room ≥ estimate + 4; 9.4 may use the reserve |
| m4 | Decision [20] and the report ask the owner whether to add a November entry now (the quota resets 2026-11-01) |
| m5 | The quota is read in its own committed transaction before each batch |
| m6 | The bundler double-quotes every rendered identifier; `::name` is described as verbatim insertion of deploy-time constants only |
| m7 | Moot: `season_week` slicing moved to the backlog ([29]), with the week-end rule recorded there |
| m8 | Placeholders are `{META_SCHEMA}` (as rendered by `metaddl.py`), `{CORE_SCHEMA}`, `{STAGE_SCHEMA}`, `{INFRA_SCHEMA}` and `{VS_NAME}` |
| m9 | The remedy (reset `IS_FINAL`, backfill) is documented in 8.1, and the `CHECK` message names it. Automatic calendar refresh is in the backlog ([10], [29]). **(varied):** documentation rather than a weekly calendar re-fetch, to keep the refresh at 4 calls |
| m10 | Batch start asserts that `CFB_STAGE.<T>` is empty; the harness truncates `CFB_STAGE` (and `LOAD_FAILURES`) |
| m11 | Pinning comes from the foundation's `vs/host/target.py` (P2). Task 1.3 adds the exapump fingerprint; the 9.0 smoke test checks all three nodes |
| m12 | Probe 1.1l fixes one UTC "now" expression, which the views and the Lua share (Requirements) |
| m13 | The timeline has one `FETCH <table>` row per table (steps 2–99); registered steps start at 100 |
| m14 | The refresh-week Background says that tables commit one by one and readers may see a mix |
| m15 | 8.4 updates the mission's constraint to "217 … 4" |
| m16 | `season_week`, week finality, the `archive` class and the `full` entries' authoring (old task 2.4) moved to the backlog ([29]). The `full` profile mechanism stays (one rank column) |
| m17 | Numeric `CALLS_NEEDED` and `FAILED_SLICES` result columns |
| m18 | Gaps are excluded from `SLICES`, and logged as one `GAPS` step row with 0 slices |
| m19 | The rebuild test re-runs the grant step (5.2); deploys are module-scoped per plan variant (6.1); the test-time growth is noted under Requirements |
| m20 | `max_calls = 0` is a dry run: it registers gaps, runs `EXPLAIN` on every pending batch, and fetches nothing |
| m21 | Every negative requirement uses `MUST NOT`, and `SHALL` is no longer used in the specs |
| m22 | Quota scenario: "the gap and the quarantined slice MUST NOT count" |
| m23 | The load-plan Background states the one-entry-per-fetch rule and `SEASON` NULL for range and seasonless slices |

### Also applied (orchestrator update during the revision)

- Targets: decision [17] and task 1.3 reuse the foundation fix `vs/host/target.py` (P2). The loader adds only `loader/exapump.py` and `TARGET` on its own make targets.
- The catalog hash covers generated content (foundation [25]), which is noted in [15] and the Dependencies.
