# Feature: Refresh button and step timeline

Demo beat 3 in the app: one click (or one SQL call typed in a client) runs the weekly chain, and the app shows each step as it starts and ends: fetch, merge, model, predictions, simulations and publication, with seconds, rows and API calls. The cost, and whether the current point will move, are shown before the click. A run that the database refuses (another run holds the lock, the budget is too small) is reported plainly and costs nothing. A run whose app process dies still ends up in the ledger.

## Background

- **What the button runs.** It runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)` on the operator path (`app/access`).
  - The script, its run lock, its budget check and its timeline belong to Phase 2 (`loader/refresh-week`, `loader/run-logs`).
  - The steps are those registered: 100 `MODEL`, 200 `PREDICT`, 300 `SIMULATE` and 400 `PUBLISH`.
- **The target week.** By default the button proposes the point after the current one: `CFB_API.CURRENT_POINT.NEXT_WEEK_SEQ`, mapped to a season, week and season type through `CFB_API.AS_OF_POINTS`. The presenter may pick another week of the season.
  - The arguments are checked before any statement runs: an integer season, a week from 1 to 99, and a season type of `regular` or `postseason`.
- **Plan, at 0 calls, on the operator path.** The plan shows:
  - the estimate per table and in total, and the fetch mode;
  - in live mode, a fresh quota reading (`/info` is free, so the plan never uses a cached one), `REFRESH_SPENDABLE`, and the ledger room (`app/live-spend`);
  - any loader run that is `RUNNING` in `CFB_META.LOAD_RUN`;
  - **whether the point will move:** the target week's games in storage (`CFB_API.GAMES`), how many are already completed, and how many have not kicked off yet. The current point moves to a week only when all its games are completed (`model/as-of`), so a week with games still to play reads, for example, "week 10 is not complete: 3 games have not kicked off; the point stays at week 9".

  From these it says whether the refresh can run, or why not.
- **Retries.** `REFRESH_WEEK` takes no call limit, and CFBD answers that need retries cost one call per attempt. The confirmation therefore reads "up to N live calls, plus retries", and the ledger check reserves the retry headroom of one request (`MAX_ATTEMPTS − 1`) on top of the estimate. The script itself checks `REFRESH_SPENDABLE` again before each table (`loader/refresh-week`), and the refresh may use the reserve that on-demand spend leaves alone (`app/live-spend`).
- **Confirm and run.**
  - A live estimate above 0 asks for a confirmation that names the calls. In replay the button itself says "0 calls".
  - The server plans again and applies the spending guards. It then starts the script in a background thread, on its own owner connection with a 900-second socket timeout, and answers at once with an action id.
  - Before the script starts, the thread notes the owner connection's session id. A second connection looks up the run's `LOAD_RUN` row by that session id, so the action names its run id while the run is still going.
  - A live run holds the ledger lock for its whole run, as `make refresh` does; a replay run does not take it.
  - The script runs to its end even if the browser leaves. There is no cancel button: the owner can kill the statement, and the next run marks the row abandoned (`loader/run-logs`).
- **When the app process dies.** If the app process ends during a run (Ctrl-C, a crash), its session ends with it: the database aborts the script or lets it finish, and the steps committed so far stay. The record that the thread would have written is then missing. The app's reconciliation (`app/live-spend`) records such a run at the next start, under the app's ledger plan, with the calls of its committed steps.
- **Development.** `make app` restarts the backend whenever a source file changes, which would cut a run short. There the live refresh is disabled: the plan says "live refresh needs `make app-serve`", and a live run is refused. Replay refreshes still run.
- **The timeline** reads `CFB_API.REFRESH_TIMELINE` on the read path.
  - Each step's row is committed when the step starts and again when it ends (`loader/refresh-week`), so the page polls: every second while a refresh run is `RUNNING`, every 5 seconds otherwise.
  - It follows the newest refresh run, whoever started it: the app, `make refresh`, or a SQL client.
  - Each step shows its name, status, seconds, slices, rows, calls and detail. A running step shows a live elapsed counter.
  - The requests of the fetch steps come from `CFB_API.FETCHES`, polled with the timeline, so they appear while the run goes on (`app/inspector`).
- **After the run.**
  - The action ends `DONE` or `FAILED`, with the script's timeline or the first line of its error, and states the current point before and after the run: "the point moved to week 10", or "the point stayed at week 9", with the reason.
  - In live mode the spend is recorded in the ledger like `make refresh` does it, as `REFRESH_WEEK#<run id>` under the app's ledger plan (`app/live-spend`).
  - The pages follow the new point once the run has ended (`app/as-of`).
- **Refusals.** Each answers with status 409 and makes no call:
  - when another run holds the loader lock, the database's message names that run, and the page offers to follow it;
  - when a refresh started by the app is still running, the answer names its action;
  - when the budget cannot cover the estimate, the answer says so; the refusal comes before any call, from the plan and again from the script's `CHECK`.
- **Without the operator path** there is no refresh button, and `GET /api/config` gives the reason. The timeline still follows runs started elsewhere.
- The examples run on the app test stack in replay mode, with the load-plan variant `load-plan.app.yaml`. That variant registers steps 100–400 plus step 350 `WAIT`, the harness's `TEST_STEP_SLEEP`, which takes 5 seconds. The current point is 2026 week 9, and the league's week-10 results are available to the refresh (harness `complete_week(10)`).

## Scenarios

### Scenario: The refresh plan names its cost before anything runs

* *GIVEN* the app test stack in replay mode, and then the same stack in live mode against the mock API, which reports 500 remaining calls
* *WHEN* the presenter asks for the plan of 2026 week 10 `regular` in each mode
* *THEN* the replay plan MUST state 0 calls, the mode `replay`, and that the refresh can run
* *AND* the live plan MUST state the estimate per table and in total, "plus retries", a quota reading taken for this plan, `REFRESH_SPENDABLE` and the ledger room
* *AND* both plans MUST state week 10's games in storage, how many are completed, and how many have not kicked off
* *AND* the mock MUST NOT receive any request other than `/info`

### Scenario: A confirmed refresh runs the chain and the timeline follows it live

* *GIVEN* the app test stack in replay mode
* *WHEN* the presenter runs the refresh of 2026 week 10 `regular` and the timeline and the action are read while step `WAIT` runs, and again after the action ends
* *THEN* the first reading MUST show `CHECK`, the `FETCH` steps, `MODEL`, `PREDICT` and `SIMULATE` as `DONE`, and `WAIT` as `RUNNING`, and the action MUST already name the run id
* *AND* the second reading MUST show every step `DONE` with its seconds, rows and calls, and 0 calls in total
* *AND* the action MUST end `DONE` and state that the point moved to week 10, and `CFB_API.CURRENT_POINT` MUST then show 2026 week 10

### Scenario: A refresh started elsewhere is followed too

* *GIVEN* the app test stack in replay mode
* *WHEN* the owner runs `EXECUTE SCRIPT TEST_CFB_INFRA.REFRESH_WEEK(2026, 10, 'regular')` in another session, and the timeline is read during step `WAIT` and after the run
* *THEN* the timeline MUST show that run, first with `WAIT` `RUNNING` and then with every step `DONE`
* *AND* the app MUST NOT have created an action for it

### Scenario: A refresh while another run holds the lock is refused cleanly

* *GIVEN* another session that holds the loader lock (harness `hold_deploy_lock`)
* *WHEN* the presenter runs the refresh of 2026 week 10 `regular`
* *THEN* the request MUST be refused with status 409 and a message that names the run holding the lock
* *AND* no slice MUST be fetched, and `CFB_API.CURRENT_POINT` MUST still show 2026 week 9
* *AND* the page MUST show the refusal as a notice that names the running run, not as an error page

### Scenario: A refresh the budget cannot cover is refused before any call

* *GIVEN* live mode against the mock API, whose `/info` reports 1 remaining call, and an estimate of 2 calls for 2026
* *WHEN* the presenter asks for the plan and then runs the refresh with `max_calls` 2
* *THEN* the plan MUST state that 2 calls are needed and 1 is available
* *AND* the run MUST be refused with status 409 for the same reason
* *AND* the mock MUST NOT receive any data request, and the ledger MUST NOT change

### Scenario: Only one app-started refresh runs at a time

* *GIVEN* a refresh started by the app whose step `WAIT` is running
* *WHEN* the presenter starts a second refresh
* *THEN* the second request MUST be refused with status 409, naming the first action's id
* *AND* the first action MUST still end `DONE`

### Scenario: A failed step shows where the chain stopped

* *GIVEN* the test hook `TEST_FAIL_PUBLISH`, which makes step `PUBLISH` fail
* *WHEN* the presenter runs the refresh of 2026 week 10 `regular`
* *THEN* the action MUST end `FAILED` with a message that names step `PUBLISH` and the run
* *AND* the timeline MUST show the steps before `PUBLISH` as `DONE` and `PUBLISH` as `FAILED` with its detail
* *AND* `CFB_API.WHATIF_RUN` MUST still name the run published at week 9

### Scenario: Without the operator path there is no refresh

* *GIVEN* the backend started with `APP_OPERATOR=off`
* *WHEN* the page loads and a client posts a refresh plan and a refresh run
* *THEN* both posts MUST be refused with status 403, naming the reason in `GET /api/config`
* *AND* the page MUST NOT show a refresh button, and MUST show the reason and the timeline of the newest run

### Scenario: A refresh whose app process died is still recorded

* *GIVEN* the app served as its own process in live mode against the mock API, with a temporary ledger that survives restarts
* *WHEN* the presenter runs the refresh of 2026 week 10 `regular`, the server process is killed while step `WAIT` runs, and the app is started again once the run is no longer running, and then a third time
* *THEN* after the second start the ledger MUST hold exactly one entry `REFRESH_WEEK#<run id>` for the run, charged the requests the mock received for the run's slices, with an error that says it was recorded by reconciliation
* *AND* the third start MUST NOT add an entry

### Scenario: The development server offers no live refresh

* *GIVEN* the backend started as `make app` starts it, with source reload, in live mode against the mock API
* *WHEN* the presenter asks for the plan of 2026 week 10 `regular` and then runs it
* *THEN* the plan MUST state that the live refresh needs `make app-serve`
* *AND* the run MUST be refused with status 409 for that reason, and the mock MUST NOT receive any data request
* *AND* in replay mode the same development server MUST run the refresh
