# Feature: Explicit live spending

Every app action that can call CFBD says how many calls it may spend before anyone triggers it, is checked again by the server at the moment it runs, and leaves a trace afterwards: a ledger entry per live request and a fetch-log row per slice. The trace is written even when the request fails, and a request that may have reached CFBD is never sent twice by the app. Replayed, stored and cached answers say they cost nothing. A demo can therefore show live data on stage without ever spending quota by surprise.

## Background

- **Actions that can call CFBD.**
  - Fetching a game preview: `app/head-to-head`, through `CFB_API_LIVE.GAME_PREVIEW` on the read path.
  - Running a curated console query: `app/inspector`, on the console path.
  - Running a weekly refresh: `app/refresh`, on the operator path.

  Nothing else in the app reaches CFBD: every `CFB_API` view costs 0 calls (`api/serving-contract`). The quota badge reads `CFB_API_LIVE.QUOTA`, which reads CFBD's free `/info`, at most once every 5 minutes unless the user asks for a fresh reading. In replay mode the badge reads "replay · no calls" and never queries `QUOTA`, because a replayed `/info` needs a recording. The app learns the fetch mode at 0 calls from the manifest of an `EXPLAIN VIRTUAL` of the preview statement.
- **Plan, at 0 calls.** Before an action, the app plans it without making a call:
  - for a query: the manifest of `EXPLAIN VIRTUAL` for that query, run as the principal that will run the query (`cfbd-fetch/fetch-log`);
  - for a refresh: the refresh estimate, which counts the weekly slices of the season that are missing or not final (`loader/refresh-week`);
  - in both cases, the fetch mode.

  The plan states the source and the most live calls the action can make:
  - `LIVE`: up to the number of planned requests. These are the manifest's slice keys when its `sources` hold `LIVE`. Which of those slices are already stored is only known when the query runs, so this number is an upper bound. CFBD answers that need retries cost one call per attempt, up to `MAX_ATTEMPTS` (4) per request.
  - `REPLAY`, `STORED`, a contradiction, or a preview cached in the session: 0.
- **An explicit act.**
  - Every control that can spend shows that number in its label before it is used, for example "1 live call", "up to 3 live calls", "replay · 0 calls" or "cached · 0 calls".
  - A refresh or console run that can spend live calls asks for a confirmation that names the number.
  - Clicking the preview button is its own confirmation. The button is disabled while its request runs, and the app never repeats a request on its own.
- **Server guards.** When the action runs, the server checks these before any call:
  - **Confirmed amount.** The request carries `max_calls`, the number the user saw. The server plans again and refuses when the new plan's live calls exceed it, because the mode can have changed since the page showed the label.
  - **Ledger.** The ledger (`vs/host/ledger.py`, the project's `cache/live-calls.jsonl`) must have room for the plan's live calls plus the retry headroom of one request (`MAX_ATTEMPTS − 1`). The room counts within the app's ledger plan (`APP_LEDGER_PLAN`, default `app`) and within the month's cap (`vs/live/ledger.yaml`). A plan or a month without a cap refuses. `LEDGER=off`, for a visitor running their own key, skips the ledger, as it does for the loader.
  - **Quota.** The plan's live calls plus the same headroom must fit `BACKFILL_SPENDABLE` of the latest quota reading: the key's spendable calls minus the reserve the weekly refresh keeps (`GREATEST(0, REFRESH_SPENDABLE − REFRESH_RESERVE)`, `loader/quota`). On-demand spend therefore never eats the refresh's reserve. A reading older than 5 minutes is replaced by a new one.
  - **Operator path.** A live request needs the operator path, because that path writes the request's fetch-log rows. Without it, the request is refused.
  - **Not busy.** Live requests run one at a time, and never while a refresh runs:
    - the app's own live requests wait for each other for at most 5 seconds;
    - the ledger lock (`Ledger.exclusive()`) is taken only by an action whose plan has live calls; it does not wait, so when another process holds it, or a refresh run is `RUNNING` in `CFB_API.REFRESH_TIMELINE`, the request is refused as busy (`live_busy`), naming the holder;
    - plans with 0 live calls never take the lock, so replayed, stored and cached answers are served during a refresh.
  - **Reconciled.** A finished loader run that spent calls but has no ledger entry is recorded first (below). While one cannot be recorded, live requests are refused, naming the run.

  A refusal names the guard and costs nothing.
- **One flight per question.** Two requests of one browser session for the same preview share one fetch: the second waits for the first and is answered from the session's cache at 0 calls. A second console run in a session while one is running is refused as busy.
- **After the action, whatever its outcome.**
  - **Ledger entries first.** Every `LIVE` request is appended to the ledger with its slice key, status, attempts and latency. Its cost is its attempts, or `MAX_ATTEMPTS` when they are unknown. The `/info` readings around the request are left unknown, because reading `/info` on every click would double the latency.
    - When the statement fails or times out, CFBD may already have been called, up to `MAX_ATTEMPTS` times per slice. The server then appends one entry per planned live slice, with status and attempts unknown, a cost of `MAX_ATTEMPTS`, and the first line of the error. Over-counting is the safe direction.
    - If a plan of 0 live calls returns `LIVE` rows anyway (the mode changed between the plan and the run), those requests are charged the same way.
  - **No second attempt.** A statement whose plan has live calls is never retried by the app, also not after a transaction conflict. It runs on a session reserved for live statements, whose statement timeout lies above the fetch function's worst case (4 attempts of 30 seconds, 3 waits of at most 30 seconds, and name resolution), so the database does not cut off a request that CFBD has already answered.
  - **Fetch-log rows second.** Every slice the query fetched (`LIVE` or `REPLAY`) and every stored slice it read gets a row in `CFB_META.FETCH_LOG`, written on the operator path after the ledger entries.
    - The rows follow the reference capture's rules (`vs/host/fetchlog.py`) and are built from the result's `REQ_*` columns and the manifest. A failed statement leaves its planned live slices logged as fetched with status unknown and 0 rows.
    - Their `CONTEXT` is `APP_PREVIEW#<action id>` for a preview, or `APP_CONSOLE#<action id>` for a console query.
    - A refresh writes its own rows, with the context `REFRESH_WEEK#<run id>`.
    - A failed fetch-log write is reported and never removes a ledger entry.
  - **Report.** The response and the page show the calls spent and the source, or the error.
- **A refresh's spend** is guarded and recorded by the same guard as `make refresh` (`loader/refresh-week`), with the app's ledger plan. It is charged the larger of the run's measured calls (`LOAD_RUN.CALLS`) and the `/info` delta.
- **Reconciliation.** At start, before every live refresh, and whenever the timeline sees a refresh run end, the operator path compares the loader runs of the current month with the ledger. A `REFRESH_WEEK` or `BACKFILL` run that is no longer running (it ended, or its session is gone), that read the quota (a live run), and that has no ledger entry `<RUN_KIND>#<run id>` is recorded under the app's ledger plan. Its cost is the run's `CALLS`, else the calls of its committed steps plus `MAX_ATTEMPTS` per slice of a fetch step the run left unfinished; the entry's error says it was recorded by reconciliation. It runs under the ledger lock, so a run is never recorded twice.
- **The ledger state is visible.** `GET /api/config` reports the ledger plan, whether the ledger is on, the plan's and the month's caps and room, and whether live actions are possible at all. When `LEDGER=off` is set, or the plan or the month has no cap, the page header says so from the start, for example "ledger off" or "live actions disabled: no ledger cap for plan app", and every live control shows that reason instead of a cost.
- **Test ledger.** A prefixed namespace, as the integration tests use, never writes the project ledger: the app writes to the ledger file it is given.
- The examples run on the app test stack, live against the mock API unless a scenario says replay. They use a temporary ledger in which the plan `app` has a cap of 20 and the month is authorized for 20. "The week-10 pair" is a pair of sim-league teams that meet in 2026 week 10, taken by the test from `TEST_CFB_API.MATCHUP_NEXT`; that meeting is the pair's preview game.

## Scenarios

### Scenario: The plan states the cost before any call

* *GIVEN* live mode and an empty ledger
* *WHEN* the head-to-head page asks for the preview plan of the week-10 pair
* *THEN* the plan MUST state source `LIVE`, 1 live call, and the slice key `/games/<id>/preview` of their week-10 meeting
* *AND* the mock MUST NOT receive any request

### Scenario: A live request is recorded in the ledger and the fetch log

* *GIVEN* live mode
* *WHEN* the user fetches the preview of the week-10 pair with `max_calls` 1
* *THEN* the mock MUST receive exactly one request
* *AND* the ledger MUST gain exactly one entry, with plan `app`, slice key `/games/<id>/preview`, status 200 and cost 1
* *AND* `TEST_CFB_META.FETCH_LOG` MUST gain exactly one row for that slice, with `REQ_SOURCE` `LIVE` and a `CONTEXT` that starts with `APP_PREVIEW#`
* *AND* the response MUST report 1 call spent from source `LIVE`

### Scenario: The server refuses to spend more than the user confirmed

* *GIVEN* a preview plan made in replay mode, which showed 0 calls, after which the virtual schema was switched to live mode
* *WHEN* the user fetches the preview with `max_calls` 0
* *THEN* the request MUST be refused with status 409, stating 1 planned live call against 0 confirmed
* *AND* the mock MUST NOT receive any request, and the ledger MUST NOT change

### Scenario: A ledger or quota without room refuses before any call

* *GIVEN* live mode, and a ledger in which plan `app` has spent 18 of its 20 calls
* *WHEN* the user fetches a preview with `max_calls` 1, and then again after the ledger is reset and the mock's `/info` makes `BACKFILL_SPENDABLE` 0 while `REFRESH_SPENDABLE` is still above 0
* *THEN* the first request MUST be refused, naming plan `app`, its cap and the retry headroom
* *AND* the second request MUST be refused, naming the spendable calls and the refresh reserve
* *AND* the mock MUST NOT receive any data request

### Scenario: Without the operator path a live request is refused and a replayed one is served

* *GIVEN* the backend started with `APP_OPERATOR=off`
* *WHEN* the user fetches a preview in live mode, and then the same preview in replay mode with a synthetic recording
* *THEN* the live request MUST be refused, stating that live requests need the operator path to log them, and the mock MUST NOT receive any request
* *AND* the replayed request MUST return the preview with `REQ_SOURCE` `REPLAY` and 0 calls spent, and the ledger MUST NOT change

### Scenario: Replayed answers cost nothing and are still logged

* *GIVEN* replay mode with a synthetic recording of the preview of the week-10 pair, and no recording of `/info`
* *WHEN* the user fetches that preview with `max_calls` 0, and the page reads the quota badge
* *THEN* the response MUST report 0 calls spent from source `REPLAY`
* *AND* the ledger MUST NOT change
* *AND* `TEST_CFB_META.FETCH_LOG` MUST gain one row for the slice with `REQ_SOURCE` `REPLAY`
* *AND* the quota badge MUST read "replay · no calls" without an error, and the inspector's query log MUST NOT hold a query of `QUOTA`

### Scenario: Every spending control states its cost

* *GIVEN* the page rendered with plans for a live preview (1 call), a replayed preview, a cached preview, and a live refresh estimated at 4 calls
* *WHEN* the user looks at each control, opens the refresh, and fetches the live preview
* *THEN* the labels MUST read "1 live call", "replay · 0 calls" and "cached · 0 calls"
* *AND* the refresh MUST ask for a confirmation that names "up to 4 live calls, plus retries" before it runs
* *AND* while the live preview runs its button MUST be disabled, and afterwards the page MUST show "1 live call spent" with the quota's remaining calls

### Scenario: A live request that fails is still charged

* *GIVEN* live mode, and the mock answering the preview of the week-10 pair with status 500 on every attempt
* *WHEN* the user fetches that preview with `max_calls` 1
* *THEN* the response MUST be an error that names the failed request, and MUST NOT report the preview
* *AND* the mock MUST have received exactly 4 requests, and the app MUST NOT have sent the statement a second time
* *AND* the ledger MUST gain exactly one entry for the slice, with cost 4, status and attempts unknown, and the error's first line
* *AND* `TEST_CFB_META.FETCH_LOG` MUST gain one row for the slice with `REQ_SOURCE` `LIVE`, status unknown and 0 rows

### Scenario: Two concurrent clicks spend once

* *GIVEN* live mode, one browser session, and the mock delaying the preview of the week-10 pair by 500 ms
* *WHEN* two requests of that session fetch the preview at the same time
* *THEN* the mock MUST receive exactly one request, and the ledger MUST gain exactly one entry
* *AND* both requests MUST be answered with the preview: one with 1 call spent from source `LIVE`, the other "cached" with 0 calls

### Scenario: A live request during a refresh is refused as busy, and a replayed one is served

* *GIVEN* a refresh of 2026 week 10 started by the app whose step `WAIT` is running, first in replay mode and then, on a second run, in live mode against the mock
* *WHEN* the user fetches the preview of the week-10 pair during each run's `WAIT`, and once more after the second run has ended, while another process holds the ledger lock
* *THEN* during the replay run the preview MUST be served from `REPLAY` with 0 calls
* *AND* during the live run the request MUST be refused with status 409 `live_busy`, naming the running refresh run, and the mock MUST NOT receive a preview request
* *AND* while the other process holds the lock the request MUST be refused with status 409 `live_busy`, naming another process as the holder
* *AND* the ledger MUST hold no preview entry

### Scenario: The ledger state is reported before the first click

* *GIVEN* the backend started in live mode with a caps file that has no cap for plan `app`, and then started again with `LEDGER=off`
* *WHEN* `GET /api/config` is read and the user asks for the preview plan of the week-10 pair and fetches it, in each start
* *THEN* the first start's configuration MUST report live actions disabled with the reason "no ledger cap for plan app", the plan MUST show that reason instead of a cost, the fetch MUST be refused with status 409 naming it, and the mock MUST NOT receive any request
* *AND* the second start's configuration MUST report the ledger off, and the page header MUST show a "ledger off" badge
