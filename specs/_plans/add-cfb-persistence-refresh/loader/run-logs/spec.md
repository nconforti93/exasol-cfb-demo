# Feature: Load, refresh and fetch logs

Every loader run leaves a record: which run it was, what it loaded, what every step took, and what every API request cost. The refresh timeline in the app, the SQL↔HTTP inspector and the quota view read these logs. Runs are exclusive, so two loads never overwrite each other's slices. A run that failed or was interrupted never blocks the next one.

## Background

- `CFB_META.LOAD_RUN` has one row per run of `BACKFILL`, `REFRESH_WEEK`, snapshot or restore:

  | Column | Meaning |
  |---|---|
  | `RUN_ID` | Unique, increasing |
  | `RUN_KIND` | `BACKFILL`, `REFRESH_WEEK`, `SNAPSHOT` or `RESTORE` |
  | `RUN_ARGS` | The arguments, e.g. `demo, NULL, 20` or `2026, 9, regular` |
  | `SESSION_ID` | Session that runs it |
  | `STARTED_AT`, `ENDED_AT` | UTC |
  | `RUN_STATUS` | `RUNNING`, `DONE`, `DONE_WITH_FAILURES`, `STOPPED_BUDGET`, `STOPPED_MAX_CALLS`, `FAILED` or `ABANDONED` |
  | `SLICES`, `ROWS_LOADED`, `CALLS` | Totals of the run. `SLICES` counts fetched slices (registered gaps excluded). `CALLS` is the measured change of `/info`'s used calls (live mode) |
  | `REMAINING_BEFORE`, `REMAINING_AFTER` | `/info` remaining calls at start and end (live mode) |
  | `DETAIL` | Error message or stop reason |

- `CFB_META.REFRESH_LOG` has one row per step:
  - `REFRESH_WEEK` writes `CHECK`, one `FETCH <table>` per table, and the registered steps;
  - `BACKFILL` writes one row per batch (`BATCH <table> #<entry>`) and one `GAPS` row.

  Columns: `RUN_ID`, `STEP_NO`, `STEP_NAME`, `STEP_STATUS` (`RUNNING`, `DONE`, `FAILED`), `STARTED_AT`, `ENDED_AT`, `SLICES`, `ROWS_LOADED`, `CALLS`, `DETAIL`. A row is committed when its step starts and again when it ends. The `GAPS` row has 0 slices and 0 calls, and names the number of registered gaps in `DETAIL`.
- `CFB_META.FETCH_LOG` (contract: `cfbd-fetch/fetch-log`) gets one row per slice a run fetched, including slices without rows, with `CONTEXT = '<RUN_KIND>#<RUN_ID>'`. The rows equal what the reference capture helper (`vs/host/fetchlog.py`) writes for the same staged result.
- `CFB_META.LOAD_LOG` (contract: `cfbd-fetch/read-through`) gets one row per stored slice. `LOADED_BY` is `'<RUN_KIND>#<RUN_ID>'`, or `'LOAD_PLAN_GAP'` for registered gaps.
- `CFB_META.LOAD_FAILURES` has one row per failing slice: table, slice key, failure count, last error, last run and time (`loader/backfill`).
- **Exclusive runs:** while a run is `RUNNING`, a second `BACKFILL`, `REFRESH_WEEK`, snapshot or restore is refused with a message naming the running run. A `RUNNING` row is stale, and the next run sets it to `ABANDONED` and proceeds, when:
  - its session no longer exists;
  - it belongs to the session that starts the new run, because one session cannot run two scripts at once;
  - it is a `BACKFILL` or `REFRESH_WEEK` row and its session is idle (no statement running). A script run is one statement, so an idle session means the script ended without closing its row: a failure, a `KILL STATEMENT`, or an interrupt.

  Snapshot and restore rows run from a host process that is often idle between statements, so they become stale only when their session ends.
- A run catches every SQL error (`pquery`) and every Lua error. It then rolls back the open batch, marks the step and the run `FAILED`, commits the log, and re-raises the error.

## Scenarios

### Scenario: Every run is recorded with its outcome

* *GIVEN* a deployed loader in replay mode and a plan with one gap
* *WHEN* the owner runs a backfill that completes and then a refresh that completes
* *THEN* `LOAD_RUN` MUST hold one row per run with its kind, arguments, start, end, status `DONE` and its totals of slices, rows and calls
* *AND* the totals MUST equal the sums over the run's rows in `REFRESH_LOG`, where the `GAPS` row counts 0 slices

### Scenario: Every fetched slice is logged with its run

* *GIVEN* live mode against the mock API and a backfill batch of three slices, one of which returns no rows
* *WHEN* the batch completes
* *THEN* `FETCH_LOG` MUST hold exactly three rows with `CONTEXT = 'BACKFILL#<run id>'`, one per slice
* *AND* the slice without rows MUST be logged with `ROWS_RETURNED = 0`
* *AND* each row's status, attempts, latency and request time MUST equal the request metadata of that slice's rows

### Scenario: The loader's fetch log equals the reference capture

* *GIVEN* a batch staged by a run
* *WHEN* the reference capture helper is applied to the same staged rows and the batch's `EXPLAIN VIRTUAL` manifest
* *THEN* the rows it produces MUST equal the run's `FETCH_LOG` rows in every column except `LOGGED_AT`

### Scenario: The load log matches the stored rows

* *GIVEN* any state after backfills and refreshes
* *WHEN* `LOAD_LOG` is compared with `SELECT REQ_SLICE, COUNT(*) FROM CFB_CORE.<table> GROUP BY REQ_SLICE` for every planned table
* *THEN* every registered slice MUST have `ROWS_LOADED` equal to its stored rows
* *AND* `CFB_CORE` MUST NOT hold rows of an unregistered slice

### Scenario: A failed run is recorded and registers nothing from the failed batch

* *GIVEN* live mode against the mock API, which answers every slice of the second batch with status 500
* *WHEN* the owner runs a backfill
* *THEN* `LOAD_RUN` MUST show the run as `FAILED`, with the error in `DETAIL`
* *AND* `REFRESH_LOG` MUST show the first batch as `DONE` and the second as `FAILED`
* *AND* `LOAD_LOG` and `FETCH_LOG` MUST NOT contain any slice of the second batch

### Scenario: A second run is refused while one is running

* *GIVEN* a backfill whose batch waits 10 seconds for the mock API (latency injection)
* *WHEN* another session starts a second backfill while that batch runs
* *THEN* the second backfill MUST fail with a message naming the running backfill and its run id
* *AND* the second backfill MUST NOT send any request or change any table

### Scenario: A run whose session ended does not block the next run

* *GIVEN* a `LOAD_RUN` row with status `RUNNING` whose session no longer exists
* *WHEN* the owner runs a backfill
* *THEN* that row MUST be set to `ABANDONED`
* *AND* the backfill MUST run normally

### Scenario: After a failed or interrupted run, the next run starts

* *GIVEN* a backfill in the owner's session failed, and another session left a `BACKFILL` row `RUNNING` after its statement was killed (`KILL STATEMENT`), with that session still open and idle
* *WHEN* the owner starts a backfill again in the same session
* *THEN* the earlier rows MUST be `FAILED` or `ABANDONED` and MUST NOT be `RUNNING`
* *AND* the new backfill MUST run normally
