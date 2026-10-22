# Feature: Weekly refresh

After a game week, one SQL call brings the stored data up to date and drives every later step of the weekly chain. This is the demo's beat 3: `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')` fetches the season's changing slices through the virtual schema, replaces them in `CFB_CORE`, and returns a step timeline with seconds, rows and API calls per table. Later phases add their steps (model, predictions, simulations) to the same chain.

## Background

- Call: `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)`. Exasol has no `CALL` statement.
  - `season_type` is `'regular'` or `'postseason'`. NULL means `'regular'`.
  - The calendar numbers both season types from week 1, so `(2026, 1, 'postseason')` is the first bowl week.
- `make refresh SEASON=… WEEK=… [SEASON_TYPE=postseason] [CONFIRM=yes]` prints the call estimate and the quota first. In live mode it runs the script only with `CONFIRM=yes`.
- **What it fetches:** the slices of `season` that are missing or not final, in the `weekly` entries of the profiles listed in `refresh_profiles` (default `demo`). Slices of other seasons and of `static` entries are not fetched.
  - With the demo plan this is 4 calls, one season slice each: `GAMES`, `METRICS_WP_PREGAME`, `RATINGS_SP` and `RANKINGS_POLLS_RANKS`.
  - The current season's slices stay non-final until `season_final_after` (`loader/load-plan`), so every refresh replaces them.
- **Loading:** exactly as in `loader/backfill`.
  - Each table's slices are one batch through `CFBD_VS` with read-through on.
  - A batch's rows land in a stage table, then replace the slices in `CFB_CORE` in one transaction, together with `LOAD_LOG` and `FETCH_LOG`.
  - A slice that is not final keeps `IS_FINAL = FALSE`.
  - Each table commits on its own. A failure in one table leaves the tables refreshed before it in place, so readers can see a mix of refreshed and previous tables until the next successful refresh.
  - The per-slice fallback and the quarantine of `loader/backfill` do not apply: a refresh failure stops the refresh and is shown.
  - A table with orphan slices (`loader/load-plan`) is not fetched; `CHECK` fails and names it.
- **Steps**, in order:
  1. Step 1, `CHECK`:
     - the arguments are valid;
     - `week` with `season_type` is a week of the stored calendar of `season`;
     - no other loader run is active, and no planned table has orphans;
     - the calls the fetch needs fit the budget.
  2. Steps 2–99, `FETCH <table>`: one step per fetched table.
  3. Steps 100 and up: the registered steps from `refresh_steps` in the load plan, by step number. Each is called as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)`. This plan registers none; Phases 3–5 add theirs.

  A failed step stops the chain.
- **Budget (live mode):** `CHECK` requires the needed calls to be at most `REFRESH_SPENDABLE` from `CFB_META.QUOTA`. The refresh may use the reserve that backfills keep. In replay mode no quota is read and no call is made.
- **Logging:** every step is a row in `CFB_META.REFRESH_LOG`, committed when the step starts and when it ends, so other sessions (the app's timeline) see the progress (`loader/run-logs`).
- **Result:** one row per step: `RUN_ID`, `STEP_NO`, `STEP_NAME`, `STEP_STATUS`, `SECONDS`, `SLICES`, `ROWS_LOADED`, `CALLS`, `DETAIL`. A failure raises an error that names the step and the run; the log keeps the timeline up to the failure.
- The examples run in the integration-test namespace with:
  - `GAMES` and `RATINGS_SP` as `weekly` season entries;
  - `TEAMS` and `CALENDAR` as `static` entries;
  - a stored 2020 calendar with regular-season weeks 1–15 and postseason week 1;
  - the current date stated per scenario.

## Scenarios

### Scenario: One call refreshes the season and returns the timeline

* *GIVEN* the current date is 2020-10-20
* *AND* the 2020 `GAMES` and `RATINGS_SP` slices are stored as non-final, and replay mode is on
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* the result MUST list `CHECK`, `FETCH GAMES` and `FETCH RATINGS_SP`, each with status `DONE` and its seconds, slices, rows and calls
* *AND* the 2020 `GAMES` and `RATINGS_SP` slices MUST have been fetched again and MUST stay registered as non-final
* *AND* slices of other seasons MUST NOT be fetched

### Scenario: Refreshed rows replace the previous rows of a slice

* *GIVEN* the stored non-final 2020 `GAMES` slice holds game 401 without a score
* *AND* the API now returns game 401 with a final score of 31–24 and one game more than before
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* `CFB_CORE.GAMES` MUST hold only the new rows of the slice, including the score and the additional game
* *AND* the slice MUST NOT contain any row twice
* *AND* `LOAD_LOG.ROWS_LOADED` of the slice MUST equal its new row count

### Scenario: A refresh after the season's final date makes the season final

* *GIVEN* the 2020 `GAMES` slice is stored as non-final
* *AND* the current date is 2021-02-15
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 1, 'postseason')` twice
* *THEN* the first call MUST fetch the slice once more and register it as final
* *AND* the second call MUST NOT fetch it

### Scenario: Postseason weeks are addressed by season type

* *GIVEN* the current date is 2021-01-05, and the registered step 100 records its arguments
* *WHEN* the presenter runs `REFRESH_WEEK(2020, 1, 'postseason')`, and then `REFRESH_WEEK(2020, 1, NULL)`
* *THEN* both calls MUST pass `CHECK`
* *AND* step 100 MUST receive `'postseason'` from the first call and `'regular'` from the second

### Scenario: Refreshing a completed season fetches nothing

* *GIVEN* every `weekly` slice of 2019 is stored and final
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2019, 5, 'regular')`
* *THEN* the result MUST list `CHECK` and no `FETCH` step, and report 0 calls
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: Static entries are not refreshed

* *GIVEN* the `TEAMS` slice and the 2020 `CALENDAR` slice are stored
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* the `TEAMS` and 2020 `CALENDAR` slices MUST NOT be fetched

### Scenario: Invalid arguments are rejected before any request

* *GIVEN* no 2021 calendar is stored
* *WHEN* the presenter runs `REFRESH_WEEK(2020, 23, 'regular')`, `REFRESH_WEEK(2020, 2, 'postseason')`, `REFRESH_WEEK(2020, 7, 'bowl')`, `REFRESH_WEEK(2021, 1, 'regular')` or `REFRESH_WEEK(NULL, 7, NULL)`
* *THEN* each call MUST fail in `CHECK` with a message that names the argument and the reason: the week is not in the 2020 calendar, the season type is unknown, no 2021 calendar is stored (run `BACKFILL` first), or the season is required
* *AND* the mock API and the replay store MUST NOT receive any request
* *AND* the stored slices MUST NOT change

### Scenario: A refresh that cannot afford its calls fails before any request

* *GIVEN* live mode against the mock API, whose `/info` reports 1 remaining call
* *AND* the 2 `weekly` slices of 2020 (`GAMES`, `RATINGS_SP`) are due
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* the call MUST fail in step `CHECK` with a message stating that 2 calls are needed and 1 is available
* *AND* the mock API MUST NOT receive any data request

### Scenario: The refresh may use the reserve that backfills keep

* *GIVEN* live mode against the mock API, whose `/info` reports `monthlyLimit` 1000, `usedCalls` 900 and `remainingCalls` 100
* *AND* `refresh_reserve` 150, so a backfill could spend nothing
* *AND* the 2 `weekly` slices of 2020 are due
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* the refresh MUST fetch both slices and report 2 calls

### Scenario: A failed fetch keeps the previous data and records the failed step

* *GIVEN* live mode against the mock API, and the 2020 `GAMES` slice is stored as non-final
* *AND* the mock API answers every request for that slice with status 500
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* the call MUST fail with an error that names the step `FETCH GAMES`, the slice and the run
* *AND* the stored rows and the `LOAD_LOG` row of the 2020 `GAMES` slice MUST be unchanged
* *AND* `REFRESH_LOG` MUST show `CHECK` as `DONE` and `FETCH GAMES` as `FAILED` with the error, and MUST NOT show any later step

### Scenario: Registered steps run after the fetch, in order

* *GIVEN* the load plan registers step 100 `MODEL` and step 200 `PREDICT`, whose scripts record their arguments
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* `MODEL` MUST run after the `FETCH` steps and before `PREDICT`, each with the run id, 2020, 7 and `'regular'`
* *AND* the result MUST list `CHECK`, the `FETCH` steps, `MODEL` and `PREDICT` in that order

### Scenario: A failing step stops the chain

* *GIVEN* registered step 100 fails and step 200 is registered after it
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* the call MUST fail with an error that names step 100 and the run
* *AND* step 200 MUST NOT run
* *AND* the slices that the `FETCH` steps refreshed MUST stay stored

### Scenario: Other sessions see the progress while the refresh runs

* *GIVEN* a registered step that takes 5 seconds
* *WHEN* the presenter runs `REFRESH_WEEK(2020, 7, 'regular')` and another session reads `CFB_META.REFRESH_LOG` while that step runs
* *THEN* the other session MUST see `CHECK` and the `FETCH` steps as `DONE` and the registered step as `RUNNING`

### Scenario: A replay-mode refresh costs zero calls

* *GIVEN* replay mode with recordings of the 2020 `weekly` slices
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2020, 7, 'regular')`
* *THEN* the slices MUST be replaced with the recorded rows, with `REQ_SOURCE = 'REPLAY'`
* *AND* the result MUST report 0 calls in every step
* *AND* the mock API MUST NOT receive any request
