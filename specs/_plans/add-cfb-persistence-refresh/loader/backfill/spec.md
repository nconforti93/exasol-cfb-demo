# Feature: Backfill

`BACKFILL` persists a profile of the load plan through the virtual schema and fetches only slices that are not stored yet. It is resumable and idempotent: a second run continues where the first stopped and never pays twice for a slice. A slice that keeps failing is isolated, so it costs neither its batch nor the queue behind it. The backfill never spends the part of the monthly budget that is reserved for weekly refreshes.

## Background

- Call: `EXECUTE SCRIPT CFB_INFRA.BACKFILL(profile, from_season, max_calls)`. Exasol has no `CALL` statement; `EXECUTE SCRIPT` runs a script.
  - `profile`: `'demo'` or `'full'`. `full` includes `demo`.
  - `from_season`: NULL, or the newest season to load. Slices of newer seasons are skipped; slices without a season (`none`, `season_range`) are always included.
  - `max_calls`: NULL, or the most slices this run may fetch, in any fetch mode.
  - `max_calls = 0` is a **dry run**: it registers gaps, checks every pending batch against the virtual schema with `EXPLAIN VIRTUAL`, and fetches nothing.
- `make backfill PROFILE=demo [FROM=…] [MAX_CALLS=…] [CONFIRM=yes]` prints the call estimate and the quota first. In live mode it runs the script only with `CONFIRM=yes`.
- **Candidates** are the profile's planned slices (`loader/load-plan`) that either have no `LOAD_LOG` row, or are stored as non-final and would be final if loaded now. Excluded are:
  - quarantined slices;
  - slices of tables that have orphan slices (`loader/load-plan`). The result names those tables.
- **Gaps** are never fetched. The run registers them in `LOAD_LOG` as final with 0 rows and `LOADED_BY = 'LOAD_PLAN_GAP'`, so read-through serves them at zero calls. Gaps don't count as fetched slices; the run logs them in one step row `GAPS` with 0 slices and 0 calls.
- **Order** (`CFB_META.BACKFILL_QUEUE`), each key breaking ties of the one before:
  1. profile: `demo` before `full`;
  2. entry priority, lower first;
  3. newest season first, with slices without a season first;
  4. table name;
  5. entry.

  Entries with equal priority are therefore loaded season by season. Slices that already failed once are held back and fetched one at a time after every other candidate of the run.
- **Batches:** a batch is the longest run of consecutive queue entries of the same **plan entry**, so one `WHERE` clause expresses it. It is capped at the virtual schema's `MAX_CALLS_PER_QUERY` and at the calls still allowed. Each batch is one `INSERT … SELECT` through `CFBD_VS`.
  - Before fetching, a zero-call `EXPLAIN VIRTUAL` of the batch query MUST plan exactly the batch's slice keys. Otherwise the run stops before any request, because the catalog and the deployed plan are out of sync.
  - The query reads `CFBD_VS` with read-through on and keeps only fetched rows (`REQ_SOURCE <> 'STORED'`). The loader never changes virtual schema properties.
  - Each batch is one transaction. The fetched rows land in a stage table and then replace the batch's slices in `CFB_CORE` (delete, then insert). Each slice is registered in `LOAD_LOG` (final or not, per its refresh class) with its row count, and its request is written to `FETCH_LOG`. A failed batch changes nothing; earlier batches stay committed.
- **Failures:**
  - If a batch of several slices fails, the run fetches each of its slices alone. The good ones are stored, and only the failing ones count as failed.
  - A slice that fails alone gets one failure in `CFB_META.LOAD_FAILURES` for this run, with the error. It is quarantined once it has failed in `quarantine_after` runs (default 2): the queue skips it and `LOAD_STATUS` shows it with its error until the owner deletes its `LOAD_FAILURES` row.
  - A successful load clears the slice's failures.
  - If every slice of a batch fails, also when fetched alone, the cause is not the slice. The run stops with status `FAILED`.
- **Budget (live mode):** the run spends at most `BACKFILL_SPENDABLE` from `CFB_META.QUOTA` (`loader/quota`), and at most `max_calls`.
  - It reads the quota in its own transaction before each batch, and caps the batch at the calls still allowed.
  - The spend is measured as the change of `/info`'s used calls around each batch (free), not estimated from slices, so retries are counted.
  - If the quota cannot be read, a live run fails before any request.
  - In replay mode no quota is read and no call is made.
- **Result:** one row per table that was loaded, blocked or had failures, plus a total row (`TABLE_NAME = '*'`). The columns are:
  - `RUN_ID`, `TABLE_NAME`, `SLICES`, `ROWS_LOADED`;
  - `CALLS`: the measured live calls;
  - `FAILED_SLICES`;
  - `RUN_STATUS`: `DONE`, `DONE_WITH_FAILURES`, `STOPPED_BUDGET`, `STOPPED_MAX_CALLS` or `FAILED`;
  - `CALLS_NEEDED`: calls still needed to complete the profile, without quarantined slices;
  - `DETAIL`.
- Only one loader run at a time, and every run is logged (`loader/run-logs`).
- The examples run in the integration-test namespace. "Request" means a data request to the mock API or the replay store. The free `/info` request is not one. Unless stated otherwise, the mock API's `/info` counts every data request it receives as a used call.

## Scenarios

### Scenario: A backfill loads by priority, newest season first

* *GIVEN* profile `demo` with `GAMES` (priority 1, seasons 2017–2020) and `RATINGS_SP` (priority 2, seasons 2019–2020), nothing stored, replay mode
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, 3)`
* *THEN* exactly the `GAMES` slices of 2020, 2019 and 2018 MUST be stored
* *AND* the total row MUST report status `STOPPED_MAX_CALLS` and `CALLS_NEEDED` 3

### Scenario: Entries of equal priority are loaded season by season

* *GIVEN* profile `demo` with `GAMES` and `RATINGS_SP`, both priority 1 and seasons 2019–2020, nothing stored, replay mode
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, 2)`
* *THEN* the 2020 slices of both tables MUST be stored
* *AND* the 2019 slices MUST NOT be stored

### Scenario: Slices without a season come first and are always included

* *GIVEN* `TEAMS` (`none`), `CONFERENCES_AFFILIATIONS` (`season_range` 1869–2019) and `GAMES` (seasons 2017–2020), all with priority 1, nothing stored, replay mode
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', 2018, 2)`
* *THEN* the `TEAMS` slice and the range slice of `CONFERENCES_AFFILIATIONS` MUST be stored
* *AND* no `GAMES` slice MUST be fetched

### Scenario: A batch never mixes plan entries

* *GIVEN* two `GAMES` entries with priority 1 and seasons 2019–2020, one with `params: {classification: fbs}` and one with `params: {classification: fcs}`, replay mode
* *WHEN* the owner runs a backfill
* *THEN* every batch logged in `REFRESH_LOG` MUST contain slices of one entry only
* *AND* all four slices MUST be stored, each with its classification in `P_CLASSIFICATION`

### Scenario: A second run fetches nothing that is already stored

* *GIVEN* a completed `BACKFILL('demo', NULL, NULL)` in live mode against the mock API, at a date when every slice is final
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, NULL)` again
* *THEN* the mock API MUST NOT receive any data request
* *AND* `CFB_CORE` and `LOAD_LOG` MUST be unchanged
* *AND* the total row MUST report status `DONE` and 0 calls

### Scenario: Stored slices equal what the virtual table returns

* *GIVEN* a `GAMES` entry for 2019 and a replay recording of `/games?seasonType=both&year=2019`
* *WHEN* the owner runs a backfill and then compares `CFB_CORE.GAMES` with the rows of that slice fetched with read-through switched off
* *THEN* `CFB_CORE.GAMES` MUST hold exactly the fetched rows, equal in every response, parameter and request column
* *AND* `LOAD_LOG` MUST register the slice as final, with `ROWS_LOADED` equal to its stored rows and `LOADED_BY` naming the run
* *AND* a later `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2019` MUST be served from storage without a request

### Scenario: Known gaps are registered without a request

* *GIVEN* a `GAMES` entry for 1869–1872 with the gap 1871
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, NULL)`
* *THEN* `LOAD_LOG` MUST register `/games?seasonType=both&year=1871` as final with 0 rows and `LOADED_BY = 'LOAD_PLAN_GAP'`
* *AND* the 1871 slice MUST NOT be requested
* *AND* `FETCH_LOG` MUST NOT contain the 1871 slice

### Scenario: A non-final slice is left to the weekly refresh

* *GIVEN* the 2020 `GAMES` slice (refresh class `weekly`) is stored as non-final
* *AND* the current date is 2020-12-01
* *WHEN* the owner runs a backfill
* *THEN* the 2020 `GAMES` slice MUST NOT be fetched

### Scenario: A slice that became final is fetched once more

* *GIVEN* the 2020 `GAMES` slice is stored as non-final
* *AND* the current date is 2021-02-15
* *WHEN* the owner runs a backfill twice
* *THEN* the first run MUST fetch the slice once and register it as final
* *AND* the second run MUST NOT fetch it

### Scenario: The backfill keeps the refresh reserve

* *GIVEN* live mode against the mock API, whose `/info` reports `monthlyLimit` 1000 and starts at `usedCalls` 840
* *AND* `monthly_call_budget` 1000, `refresh_reserve` 150, and 25 missing `GAMES` slices
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, NULL)`
* *THEN* exactly 10 data requests MUST reach the mock API
* *AND* the total row MUST report status `STOPPED_BUDGET` and `CALLS_NEEDED` 15

### Scenario: The run counts measured calls, not slices

* *GIVEN* live mode against the mock API, 6 missing `GAMES` slices and `MAX_CALLS_PER_QUERY = 3`
* *AND* the mock API answers the first request for one slice with status 429 and the retry with 200
* *WHEN* the owner runs a backfill
* *THEN* `LOAD_RUN.CALLS` and the total row's `CALLS` MUST be 7, the change of `/info`'s used calls during the run
* *AND* all 6 slices MUST be stored

### Scenario: A dry run checks the plan and fetches nothing

* *GIVEN* a profile with 6 missing slices in two batches and one gap
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, 0)`
* *THEN* the run MUST run `EXPLAIN VIRTUAL` for both pending batches and MUST NOT send any request
* *AND* the total row MUST report `CALLS_NEEDED` 6
* *AND* the gap MUST be registered as described above

### Scenario: The backfill can start at an older season

* *GIVEN* a `GAMES` entry for 2017–2020, nothing stored, replay mode
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', 2018, NULL)`
* *THEN* the 2018 and 2017 slices MUST be stored
* *AND* the 2020 and 2019 slices MUST NOT be fetched

### Scenario: A failing slice is isolated and does not cost its batch

* *GIVEN* live mode against the mock API, a `GAMES` entry for 2017–2020 and `MAX_CALLS_PER_QUERY = 2`, so the batches are 2020–2019 and 2018–2017
* *AND* the mock API answers every request for 2018 with status 500
* *WHEN* the owner runs a backfill
* *THEN* the 2020, 2019 and 2017 slices MUST be stored and registered
* *AND* the 2018 slice MUST NOT be stored, and `LOAD_FAILURES` MUST show it with 1 failure and the error
* *AND* the total row MUST report status `DONE_WITH_FAILURES` and 1 failed slice

### Scenario: A slice that keeps failing is quarantined without blocking the queue

* *GIVEN* a `GAMES` entry for 2015–2020, where 2017 failed in one earlier run and 2016 and 2015 are missing
* *AND* the mock API still answers every request for 2017 with status 500
* *WHEN* the owner runs a backfill, and then another one
* *THEN* the first run MUST store 2016 and 2015, and MUST request 2017 exactly once, alone, after them
* *AND* 2017 MUST then be quarantined with 2 failures and shown in `LOAD_STATUS` with its error
* *AND* the second run MUST NOT request 2017

### Scenario: A failure of every slice stops the run

* *GIVEN* live mode against the mock API, a `GAMES` entry for 2017–2020 with `MAX_CALLS_PER_QUERY = 2`, and the mock API answers every `GAMES` request with status 500
* *WHEN* the owner runs a backfill
* *THEN* the run MUST stop with status `FAILED` after the batch 2020–2019 and its two single-slice retries
* *AND* the 2018 and 2017 slices MUST NOT be requested
* *AND* the 2020 and 2019 slices MUST each show 1 failure in `LOAD_FAILURES`

### Scenario: The next run resumes after a failure

* *GIVEN* a previous backfill stopped with status `FAILED` after storing 2020 and 2019, and 2018 and 2017 have 1 failure each
* *AND* the mock API answers normally again
* *WHEN* the owner runs the backfill again
* *THEN* exactly the 2018 and 2017 slices MUST be requested, each alone
* *AND* all four slices MUST then be stored and registered, and their failures cleared

### Scenario: A plan that disagrees with the virtual schema stops before any request

* *GIVEN* a deployed plan whose `GAMES` slice keys lack the explicit default `seasonType=both`, e.g. compiled from an older catalog
* *WHEN* the owner runs a backfill
* *THEN* the script MUST fail before any request
* *AND* the error MUST name the table, the expected and the planned slice keys, and `make deploy` as the remedy

### Scenario: The backfill leaves the virtual schema properties unchanged

* *GIVEN* the virtual schema properties before a run
* *WHEN* a backfill completes, and when another backfill fails
* *THEN* `EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES` MUST show the same properties after each run

### Scenario: No batch exceeds the per-query call budget

* *GIVEN* `MAX_CALLS_PER_QUERY = 3` and 7 missing `GAMES` slices, replay mode
* *WHEN* the owner runs a backfill
* *THEN* the run MUST fetch them in three batches of 3, 3 and 1 slices, each logged as a step in `REFRESH_LOG`
* *AND* the call budget MUST NOT reject any batch
