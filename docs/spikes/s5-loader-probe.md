# S5 loader and snapshot probes: findings

Tasks 1.1 and 1.2 of `add-cfb-persistence-refresh`. Probe scripts: `docs/spikes/s5_loader_probe.py` (1.1) and `docs/spikes/s5_snapshot_probe.py` (1.2). Raw results: `cache/probe/s5-loader-probe.json` and `cache/probe/s5-snapshot-probe.json` (gitignored).

- **Runtime:** Exasol Personal local, `2026.2.0-nano.3` (Lua reports `2026.2.0-dev.0`, Lua 5.4.7), 1 node, 127.0.0.1:8563, through `vs/host/target.py` `resolve('local')`. exapump 0.13.0, pyexasol 2.4.1.
- **Run:** 2026-10-22, as `sys`.
- **Safety:**
  - Objects: only `PROBE_*`: the schemas `PROBE_S5` and `PROBE_S5_SNAP`, the user `PROBE_S5_USER` (random in-memory password), the connection `PROBE_S5_CONN` to `http://192.0.2.1:9` (TEST-NET-1, never contacted) with a dummy key, and a replay-mode probe namespace `PROBE_` (`PROBE_CFBD_VS`, `PROBE_CFB_INFRA`, `PROBE_CFB_META`, `PROBE_CFB_CORE`, `PROBE_CFB_STAGE`).
  - Files: synthetic recordings in BucketFS `bfsdefault/default/cfbd/probe-s5-replay`, and Parquet/CSV files in a private temporary directory.
  - Cleanup: everything was removed afterwards. Nothing named `PROBE%` is left (schema, user or connection).
  - Untouched: `TEST_*`, `CFB_*` and the existing `CFBD_API` connection.
- **Zero CFBD calls.** The probe VS replays recordings, and `API_FEATURES` was not synced (no `/info`).
- **Re-run:**
  - 1.1: `uv run python docs/spikes/s5_loader_probe.py [--only a,b] [--keep] [--target aws]`.
  - 1.2: `uv run python docs/spikes/s5_snapshot_probe.py [--keep] [--target aws]`.
  - `--target aws` (task 9.0) runs items a, b, c, g, h, i, j, l and m; (d) needs the local BucketFS directory.
- **Gate (A1): passed.** (a), (b) and (c) are positive: Lua can commit and roll back, catch failures, return tables and run `EXPLAIN VIRTUAL`. No escalation.

## 1.1 Loader platform: summary

Each row: result → the fallback the plan adopts if it is negative (none is), and what the later tasks do with it.

| Item | Result | Fallback if negative | What the plan does with it |
|---|---|---|---|
| (a) `RETURNS TABLE`, `exit(rows, coldefs)` | **Pass.** pyexasol gets a typed result set with exactly the declared columns and types. A Lua `nil` cell becomes NULL, a float into `DECIMAL(9,3)` is rounded (1.23456 → 1.235), and `exit({}, …)` gives an empty table. `WITH OUTPUT` returns only `OUTPUT VARCHAR(2000000)` with the `output()` lines, so the table is discarded. **Argument count must match exactly:** `REFRESH_WEEK(2026, 9)` fails with "expected 3 script parameters but got 2". **`exit()` fails the whole script** on a value longer than its VARCHAR ("right truncation"), on a cut through a UTF-8 sequence ("Invalid Bytestream"), and on a reserved word in the column list (`LOG`, `RESULT` and `SCOPE_USER` failed in exploratory runs, "invalid column description string"). The plan's result column names are not reserved | Gate: timeline only in `REFRESH_LOG`, printed by the wrappers. The demo line keeps three arguments | Decision [2]: only the 3-argument form exists; 8.1 must not show `REFRESH_WEEK(2026, 9)`. 4.2: truncate every string with `utf8.offset` before `exit()` and before writing VARCHAR columns. 4.1: check the result column names against `vs/gen/exasol_reserved_keywords.txt` |
| (b) Transactions and errors | **Pass, with an autocommit trap.** `COMMIT`/`ROLLBACK` work inside scripts. A failing `pquery` (division by zero `22012`, UDF exception `22002` with the Python traceback, PK violation `27002`) rolls back only that statement: the open transaction keeps earlier work and stays usable. `ROLLBACK` + log + `COMMIT` + `error()` works: the log row survives and the batch is gone. `pcall` catches `error()` and also `query()` failures. Error objects become strings. Committed work survives a later `error()`. **Under autocommit (pyexasol, DBeaver, exapump), a script that ends in `error()` or an uncaught `query()` failure COMMITS its uncommitted work.** With autocommit off, that work stays open in the caller's transaction | Gate: one batch per `EXECUTE SCRIPT`, with `make backfill` looping and stopping on failure or no progress (m2) | 4.2: the error path must `ROLLBACK` before `error()`; this is mandatory, not hygiene. Use `error(msg, 0)` (no position prefix); the client sees `<msg> caught in script "S"."N": (Session: …)` |
| (c) `EXPLAIN VIRTUAL` from Lua | **Pass.** Both forms work through `pquery`: `SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL …)` and plain `EXPLAIN VIRTUAL …` (columns `PUSHDOWN_ID, PUSHDOWN_SQL, PUSHDOWN_JSON, PUSHDOWN_INVOLVED_TABLES`). Each gives one row whose SQL starts with `/*CFBD `. Plain search for `"<key>"` found 2/2 batch keys, and `"planned_requests":N` matches. `require("cjson")` decodes the manifest (`sources` = `STORED,REPLAY` with read-through on). No fetch | Gate: plain `EXPLAIN VIRTUAL`; else the post-fetch `REQ_SLICE` check alone | 4.2 `explain_check` uses the subselect form as planned. Keys are plain ASCII in the manifest: only `*/` is escaped (`*\/`) and non-ASCII would be `\u`-escaped (`manifest_json`) |
| (d) Replay-mode load inside a script; [21] guard | **Pass.** `INSERT INTO <stage> SELECT * FROM PROBE_CFBD_VS.GAMES WHERE "P_YEAR" IN (2020, 2019) AND "REQ_SOURCE" <> 'STORED'` inside Lua staged 4 rows of 2 `REPLAY` slices, and the swap plus `MERGE INTO LOAD_LOG` committed. With one slice non-final, only that slice was staged; the stored slice was filtered out. With every slice final-stored and the recordings deleted, the INSERT staged 0 rows without an error: the empty-SET-group guard holds and nothing was fetched | Load the failing shape from the host | Batch shape of Key Interfaces confirmed. Rows in `CFB_CORE` keep the fetch source (`REPLAY`/`LIVE`) in `REQ_SOURCE`; reads through the VS relabel them `STORED` |
| (e) Nested `EXECUTE SCRIPT` | **Pass.** Through `pquery`, `:name` binds pass an integer, NULL (arrives as `null`) and a quoted string. An inner `RETURNS ROWCOUNT` script (`exit({rows_affected = 7})`) gives `rows_affected`. An inner `error()` or SQL failure returns `false`, code `43000`, `<message> caught in script "S"."N"…`. **The inner script shares the outer transaction:** its uncommitted inserts, also those of an inner script that failed, stay in it until the outer rolls back. A missing script gives "script X does not exist". A nested `WITH OUTPUT` returns 0 rows. `import("S.LIB", "lib")` works | `import()` of a step module (works; not needed) | 4.3: `COMMIT` before each registered step and after it returns. On a failed step, `ROLLBACK` first, then log `FAILED` and `COMMIT`. Step scripts must declare exactly `(run_id, season, week, season_type)` |
| (f) Caller's rights | **Pass.** The caller needs `EXECUTE` on the script ("insufficient privileges for executing a script"). Every statement inside runs as the caller: `CURRENT_USER` and `exa.meta.current_user` are the caller, and a missing `SELECT` fails inside the script until it is granted | Document a definer model; the Phase 6 role changes | [22] confirmed. The Phase 6 loader role needs `EXECUTE` on the scripts plus every privilege the scripts use |
| (g) Lua value types | **Pass.** `DECIMAL(p,0)` → Lua integer (`999999999999999999` exact; `CURRENT_SESSION` exact as integer). `DECIMAL(p,s>0)` and `DOUBLE` → float, `BOOLEAN` → boolean, `TIMESTAMP` and `DATE` → string (`2026-10-22 12:34:56.789000`), VARCHAR → string. NULL → `null` (userdata): `null ~= nil`, `tostring(null)` is `userdata: 0x…`, and comparing it with a number raises. Script parameters: `NULL` → `null`, `2018` → integer, `2.5` → float, `'demo'` → string, `TRUE` → boolean. `exa.meta.session_id` is a string. An empty result gives `#res == 0` | Normalize at the Lua boundary | 4.2: map `null` to `nil` when reading rows and arguments; never compare `null` with numbers. Re-check on AWS in 9.0 (its version is not yet recorded) |
| (h) Commits visible during the run | **Pass.** A row committed by a running script is visible to another session at once; rows of its still-open transaction are not | Timeline after the run only | Refresh timeline (`REFRESH_LOG`) confirmed |
| (i) Concurrent lock inserts | **Pass.** Exactly one wins. The loser's check still reads 0 `RUNNING` rows, and its `INSERT` fails **at once** (no wait) with `40001 GlobalTransactionRollback … Transaction collision: automatic transaction rollback`; its whole transaction is rolled back. This held both when A held its uncommitted insert for 4 s and when both read before either inserted | Single-row lock table with `UPDATE` | 4.2 lock: see "Run lock recipe" |
| (j) Liveness and idleness | **Pass.** See "Run lock recipe". In short: `EXA_ALL_SESSIONS` (readable by non-DBA users; `EXA_DBA_SESSIONS` is not) shows `STATUS`. A running script is never `IDLE`; after `KILL STATEMENT` or a client abort the session is `IDLE`. **A killed run cannot log `FAILED`.** A kill inside a bare `query()` commits the partial transaction under autocommit | A9: only "own session" and "session gone" | The idle rule is needed and safe; `pquery` everywhere is load-bearing |
| (k) `LIKE`; `MERGE` reading its target | **Pass.** `CREATE TABLE … (LIKE …)` copies names, types and NOT NULL, but not the primary key and not the `IDENTITY` property. `MERGE` into a PK table whose `USING` reads the same table through a view works, and a re-run is idempotent | Stage DDL from the mirror DDL; DELETE + INSERT | Stage and landing tables via `LIKE` (3.3, 5.2). `MERGE INTO LOAD_LOG … USING (… LOAD_SLICES …)` as planned |
| (l) UTC "now" | **Pass.** Personal's session time zone is `CET` (Berlin rules: CEST in October), so `CURRENT_TIMESTAMP` is local time. **`CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')`** (TIMESTAMP(3)) gives UTC in views and scripts under Berlin, New York and UTC sessions. Do not use `SYSTIMESTAMP` (it is already UTC here although `DBTIMEZONE` says CET, so converting it shifts twice) or `FROM_POSIX_TIME(POSIX_TIME())` (session-local). Caveat: in the fall-back hour of a DST zone `CONVERT_TZ` picks standard time (`02:30` Berlin on 2026-10-25 → `01:30` UTC), up to 1 h late, once a year | `os.date('!…')` in Lua plus a stored offset | 3.2 and 4.2 use this one expression (or `NOW_OVERRIDE`). 6.1: the harness's `register_slice` writes local `CURRENT_TIMESTAMP` |
| (m) Lua runtime | **Pass (info).** `cjson` is not a global, but `require("cjson")` works and decodes `\/` escapes. `os` has `clock, date, difftime, getenv, sleep, time`; `os.date('!%Y-%m-%d %H:%M:%S')` gives UTC and `os.sleep(1)` sleeps 1 s. `utf8` and `unicode` exist; `io`, `socket`, `load` and `debug` do not. `:name` binds values (quotes and NULL safe, also in `IN (:k1, :k2)`), and `::name` inserts text verbatim. `quote('my col')` gives `"my col"`. About 6–10 ms per tiny `pquery` on local (1,500 statements in 10–14 s) | Plain string search for the manifest | The plan's plain search stays; `cjson` is an option. 6.2 may write `TEST_STEP_SLEEP` as a Lua step with `os.sleep` instead of a Python UDF |

## Run lock recipe (4.2, from (i) and (j))

Observed:
- `EXA_ALL_SESSIONS`:
  - Columns: `SESSION_ID, USER_NAME, STATUS, COMMAND_NAME, STMT_ID, DURATION, QUERY_TIMEOUT, ACTIVITY, TEMP_DB_RAM, PERSISTENT_DB_RAM, LOGIN_TIME, CLIENT, DRIVER, ENCRYPTED, CONSUMER_GROUP, NICE, RESOURCES, CLUSTER_NAME`; no `SQL_TEXT`.
  - A non-DBA user sees every session (3 of 3). `EXA_DBA_SESSIONS` needs DBA rights, and the `TEST_` owner has none.
- **Running script:**
  - `STATUS = 'EXECUTE SQL'`, with `COMMAND_NAME` `EXECUTE SCRIPT` or the inner statement's command.
  - In 160 samples of a 1,500-statement loop, it was never `IDLE` between busy samples. The only `IDLE` sample came after the script had ended.
- **After `KILL STATEMENT IN SESSION s`, or a client abort (`abort_query`):**
  - `STATUS = 'IDLE'`, `COMMAND_NAME = 'NOT SPECIFIED'`.
  - After a disconnect, the row is gone.
- **Kill or abort inside a `pquery`:**
  - The `pquery` returns `false` with code `R0003`, "Client requested execution abort.", and the script continues.
  - **Every later statement fails with the same error.** `ROLLBACK` and `COMMIT` still take effect while reporting the failure (the uncommitted batch row was gone afterwards); DML does not run.
  - So the run cannot write `FAILED`, and its committed `RUNNING` row stays.
- **Kill inside a bare `query()`:** the script ends, and autocommit **commits** the open transaction. The batch row inserted before the kill was committed. In a real batch, a kill between `DELETE FROM CFB_CORE.T …` and `INSERT …` would commit the delete.
- **Takeover:** `UPDATE … SET RUN_STATUS = 'ABANDONED'` from another session ran in 0.02–0.05 s and did not wait on the dead run.
- **Concurrent start:** see (i). `40001` arrives at once, on the loser's `INSERT`.

Recipe:
1. **Acquire the lock in its own transaction.**
   1. Read the `RUNNING` rows joined to `EXA_ALL_SESSIONS`.
   2. A row is stale when it has no session row, when its `SESSION_ID = CURRENT_SESSION`, or, for `BACKFILL`/`REFRESH_WEEK` rows, when the session's `STATUS = 'IDLE'`. Compare in SQL.
   3. `UPDATE` the stale rows to `ABANDONED`, `INSERT` the own `RUNNING` row, and `COMMIT`.
2. **On `40001` at any step:**
   1. `ROLLBACK`, then read again.
   2. If another live `RUNNING` row exists, refuse and name its run id. Otherwise retry once.
3. **Run every statement through `pquery`.** After a failure:
   1. `pquery("ROLLBACK")` first, and ignore its status.
   2. If the error is `R0003`, skip logging; the next run's own-session or idle rule frees the row.
   3. Otherwise log `FAILED`, `COMMIT`, then `error(msg, 0)`.
4. **No bare `query()` anywhere** (4.1's source test). It is the only way a kill can commit half a batch.

## 1.2 Snapshot format

**Decision: gzip CSV through pyexasol `export_to_file` / `import_from_file` (the fallback of [15]); not Parquet via exapump.** Recorded as decision [31].

### exapump 0.13 (Parquet)

| Check | Result |
|---|---|
| DSN from `EXAPUMP_DSN` in the child environment only, `--certificate-fingerprint` | **Works**, but **only with a lower-case fingerprint**. An upper-case one (as `vs/host/target.py` produces it) fails: "Certificate fingerprint mismatch: expected 1F40…, got 1f40…". Without a fingerprint: "invalid peer certificate: UnknownIssuer" (no silent unverified connection). Wrong fingerprint or wrong password: exit 1. The DSN never appeared in any output. `--transport websocket` works too. Client shows as `exarrow-rs 0.16.0` |
| Round trip of LOAD_LOG, FETCH_LOG, extremes | Exact. Covered: `DATE` and `TIMESTAMP(3)` at 0001-01-01 and 9999-12-31, `DOUBLE` 1e-300 and 1.7976e308, NULLs |
| Round trip of the type table (VARCHAR with JSON) | **Fails.** Export exits 1 with "CSV parsing error … Expected 13 columns, found 2" and leaves a 0-byte file |
| VARCHAR values, one table each | Comma, newline, CRLF and a JSON array: export exits 1 (0-byte file). Double quote: depends on the table; export failed in an exploratory 3-column table and the quote was **silently dropped** in the probe's 2-column table (`a"b` → `ab`). Leading or trailing space: **silently trimmed**. CR, tab, `;`, `'`, `\`, umlauts and emoji: exact |
| Parquet typing | **Untyped.** `upload --dry-run` shows `col0…colN VARCHAR(2000000)`. Upload maps by position: a reversed column order fails with ETL-3053, and fewer columns with ETL-6009. An upload into a missing table creates `col0…` VARCHAR(2000000) columns |
| Other behaviour | Upload into a non-empty table appends (3 → 6 rows). An empty table exits 0 ("Exported 0 rows") but **writes no file**. Export silently overwrites an existing file. A missing table exits 1. Files are 0644 |

Every mirror JSON column (`VARCHAR(2000000)`) holds commas and quotes, so A2 is negative.

### pyexasol gzip CSV (adopted)

- **Calls:**
  - Export: `export_to_file(dst, (schema, table), export_params={"format": "gz", "with_column_names": True})`.
  - Import: `import_from_file(src, (schema, landing), import_params={"format": "gz", "skip": 1})`.
- **Exact for every type except DOUBLE:**
  - `DECIMAL(3,0)` … `DECIMAL(36,6)`, `BOOLEAN`, `DATE` and `TIMESTAMP(3)` (0001–9999), NULLs.
  - `VARCHAR(8/2000/2,000,000)`, including commas, quotes, CR/LF, tabs, leading and trailing spaces, emoji, a 1 MB JSON value and a 2,000,000-character value.
  - `LOAD_LOG` and `FETCH_LOG` are exact.
- **DOUBLE**, 4,000 values per row:

  | Data | Default export (15 significant digits) | `csv_cols` `FORMAT='9.9999999999999999EEEE'` (17 digits) |
  |---|---|---|
  | Short decimals, JSON-like (1–6 decimals) | **0 differ** | 1,432 differ by ≤ 1 ULP (2.2e-16) |
  | Full-precision doubles | 3,611 differ, ≤ 3.8e-12 relative | 1,074 differ by ≤ 1 ULP |

  Exasol's text-to-DOUBLE parse is not correctly rounded, so no text format is bit-exact for every double. **Use the default format:** CFBD JSON numbers are short decimals and come back exact. `-0.0` becomes `0`.
- **Format details:**
  - The header line equals the column list; the restore checks it against the manifest.
  - Import is positional: a reversed column order fails.
  - An empty table gives a 64-byte, header-only file that imports 0 rows.
  - Files are created 0644, so 5.2 opens the target with mode 0600 and passes the file object.
- **Speed:** a table with 3 MB of text: export 3.1 s, import 1.1 s. A small table: about 1.2 s export (transport setup) and 0.1–0.4 s import.
- **Fingerprint:**
  - **`SUM(ROUND(x, 8))` on DOUBLE is not deterministic:** two evaluations over the same 20,000-row table differed by 9.3e-10 in 2 of 8 runs, and source vs. a value-identical copy differed in 2 of 4. **`SUM(CAST(ROUND(x, 8) AS DECIMAL(36,8)))` was always equal.** It needs `|x| < 1E27`; count larger values separately. `ROUND(1.7976E308, 8)` overflows.
  - The spec's table has no `DATE` row, but mirrors have 23 `DATE` columns: use `SUM(DAYS_BETWEEN(d, DATE '1970-01-01'))` (DECIMAL).
  - TIMESTAMP: `SUM(SECONDS_BETWEEN(ts, TIMESTAMP '1970-01-01 00:00:00'))` (DECIMAL(15,3)) is independent of the session time zone. **`POSIX_TIME(ts)` is not**: it shifts by −7200 s in Berlin and +14400 s in New York, which breaks an AWS → local comparison.

## Consequences for later tasks

- **1.3:**
  - With the CSV decision, no snapshot code calls exapump, and `loader/exapump.py` has no consumer. The orchestrator decides whether to drop it.
  - If it stays, it must pass the fingerprint in lower case.
- **3.2:**
  - "Now" is `CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')` or `NOW_OVERRIDE`.
  - Nothing else changes.
- **4.2:**
  - Error path: `ROLLBACK` before `error()`, because autocommit commits otherwise. Treat `R0003` (kill or abort) as "cannot log".
  - Lock: as in the recipe, through `EXA_ALL_SESSIONS`, with `40001` handling and staleness compared in SQL.
  - Strings: truncate with `utf8.offset`.
  - Lua boundary: normalize `null`.
  - Binds: `:name` for values and `IN` lists, `::name` only for constants.
- **4.3:**
  - Registered steps run in the refresh's transaction: `COMMIT` around each step, and `ROLLBACK` before logging a failed step.
  - Always three `REFRESH_WEEK` arguments.
  - Steps declare exactly four parameters.
- **5.2:**
  - Format: exporter and importer are the pyexasol gzip CSV path. Manifest `"format": "csv-gzip"`, files `<SCHEMA>.<TABLE>.csv.gz`.
  - Landing tables: create them via `LIKE` after the column check, and verify each file's header line before import.
  - Files: 0600.
  - Fingerprint: DECIMAL-cast DOUBLE sums, a `DATE` aggregate, `SECONDS_BETWEEN` for TIMESTAMP, never `POSIX_TIME`.
  - DOUBLE values with more than 15 significant digits may differ by ≤ 4e-12 relative after a restore. The "value for value" scenario holds for JSON-like data. The test data should use short decimals, or the spec should state the exception.
  - The child-process DSN rule (no secret in argv) is moot: export and import use the pinned pyexasol connection from `target.connect()`.
- **6.1 / 6.2:**
  - The harness writes local `CURRENT_TIMESTAMP` into `LOAD_LOG`.
  - `TEST_STEP_SLEEP` can be a Lua step (`os.sleep`).
- **8.1:** only the 3-argument `REFRESH_WEEK` form exists, and `WITH OUTPUT` discards the timeline (confirmed).
- **9.0:**
  - Run both probes with `--target aws`: the Lua value types (g), the session statuses (j), and pyexasol's HTTP transport through the pinned node of the 3-node cluster.
  - The `--target aws` probe is written but has not been run (AWS was busy).
