# Feature: Explicit live spending

Every app action that can call CFBD says how many calls it may spend before anyone triggers it, is checked again by the server at the moment it runs, and leaves a trace afterwards: a ledger entry per live request and a fetch-log row per slice. Replayed, stored and cached answers say they cost nothing. A demo can therefore show live data on stage without ever spending quota by surprise.

## Background

- **Actions that can call CFBD.**
  - Fetching a game preview: `app/head-to-head`, through `CFB_API_LIVE.GAME_PREVIEW` on the read path.
  - Running a curated console query: `app/inspector`, on the operator path.
  - Running a weekly refresh: `app/refresh`, on the operator path.

  Nothing else in the app reaches CFBD: every `CFB_API` view costs 0 calls (`api/serving-contract`). The quota badge reads `CFB_API_LIVE.QUOTA`, which reads CFBD's free `/info`. It reads it at most once every 5 minutes, unless the user asks for a fresh reading.
- **Plan, at 0 calls.** Before an action, the app plans it without making a call:
  - for a query: the manifest of `EXPLAIN VIRTUAL` for that query (`cfbd-fetch/fetch-log`);
  - for a refresh: the refresh estimate, which counts the weekly slices of the season that are missing or not final (`loader/refresh-week`);
  - in both cases, the fetch mode.

  The plan states the source and the most live calls the action can make:
  - `LIVE`: up to the number of planned requests. These are the manifest's slice keys when its `sources` hold `LIVE`. Which of those slices are already stored is only known when the query runs, so this number is an upper bound.
  - `REPLAY`, `STORED`, a contradiction, or a preview cached in the session: 0.
- **An explicit act.**
  - Every control that can spend shows that number in its label before it is used, for example "1 live call", "up to 3 live calls", "replay · 0 calls" or "cached · 0 calls".
  - A refresh or console run that can spend live calls asks for a confirmation that names the number.
  - Clicking the preview button is its own confirmation.
- **Server guards.** When the action runs, the server checks these before any call:
  - **Confirmed amount.** The request carries `max_calls`, the number the user saw. The server plans again and refuses when the new plan's live calls exceed it, because the mode can have changed since the page showed the label.
  - **Ledger.** The ledger (`vs/host/ledger.py`, the project's `cache/live-calls.jsonl`) must have room for the plan's live calls plus the recorder's retry headroom (`MAX_ATTEMPTS − 1`). The room counts within the app's ledger plan (`APP_LEDGER_PLAN`, default `app`) and within the month's cap. A month without an authorization refuses. `LEDGER=off`, for a visitor running their own key, skips the ledger, as it does for the loader.
  - **Quota.** `REMAINING_CALLS` must cover the plan, from the latest quota reading. A reading older than 5 minutes is replaced by a new one.
  - **Operator path.** A live request needs the operator path, because that path writes the request's fetch-log rows. Without it, the request is refused.

  A refusal names the guard and costs nothing.
- **After the action.**
  - **Ledger entries.** Every `LIVE` request is appended to the ledger with its slice key, status, attempts and latency. Its cost is its attempts, or `MAX_ATTEMPTS` when they are unknown. The `/info` readings around the request are left unknown, because reading `/info` on every click would double the latency.
  - **Fetch-log rows.** Every slice the query fetched (`LIVE` or `REPLAY`) and every stored slice it read gets a row in `CFB_META.FETCH_LOG`.
    - The rows follow the reference capture's rules (`vs/host/fetchlog.py`) and are built from the result's `REQ_*` columns and the manifest.
    - Their `CONTEXT` is `APP_PREVIEW#<action id>` for a preview, or `APP_CONSOLE#<action id>` for a console query.
    - A refresh writes its own rows, with the context `REFRESH_WEEK#<run id>`.
  - **Report.** The response and the page show the calls spent and the source.
- **A refresh's spend** is guarded and recorded by the same guard as `make refresh` (`loader/refresh-week`), with the app's ledger plan. It is charged the larger of the run's measured calls (`LOAD_RUN.CALLS`) and the `/info` delta.
- **Test ledger.** A prefixed namespace, as the integration tests use, never writes the project ledger: the app writes to the ledger file it is given.
- The examples run on the app test stack, live against the mock API unless a scenario says replay. They use a temporary ledger in which the plan `app` has a cap of 20 and the month is authorized for 20.

## Scenarios

### Scenario: The plan states the cost before any call

* *GIVEN* a week-10 game id, live mode, and an empty ledger
* *WHEN* the head-to-head page asks for the preview plan of that game
* *THEN* the plan MUST state source `LIVE`, 1 live call, and the slice key `/games/<id>/preview`
* *AND* the mock MUST NOT receive any request

### Scenario: A live request is recorded in the ledger and the fetch log

* *GIVEN* a week-10 game id in live mode
* *WHEN* the user fetches its preview with `max_calls` 1
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
* *WHEN* the user fetches a preview with `max_calls` 1, and then again after the ledger is reset and the mock's `/info` reports 0 remaining calls
* *THEN* the first request MUST be refused, naming plan `app`, its cap and the retry headroom
* *AND* the second request MUST be refused, naming the remaining calls
* *AND* the mock MUST NOT receive any data request

### Scenario: Without the operator path a live request is refused and a replayed one is served

* *GIVEN* the backend started with `APP_OPERATOR=off`
* *WHEN* the user fetches a preview in live mode, and then the same preview in replay mode with a synthetic recording
* *THEN* the live request MUST be refused, stating that live requests need the operator path to log them, and the mock MUST NOT receive any request
* *AND* the replayed request MUST return the preview with `REQ_SOURCE` `REPLAY` and 0 calls spent, and the ledger MUST NOT change

### Scenario: Replayed answers cost nothing and are still logged

* *GIVEN* replay mode with a synthetic recording of a week-10 game's preview
* *WHEN* the user fetches that preview with `max_calls` 0
* *THEN* the response MUST report 0 calls spent from source `REPLAY`
* *AND* the ledger MUST NOT change
* *AND* `TEST_CFB_META.FETCH_LOG` MUST gain one row for the slice with `REQ_SOURCE` `REPLAY`

### Scenario: Every spending control states its cost

* *GIVEN* the page rendered with plans for a live preview (1 call), a replayed preview, a cached preview, and a live refresh estimated at 4 calls
* *WHEN* the user looks at each control, opens the refresh, and fetches the live preview
* *THEN* the labels MUST read "1 live call", "replay · 0 calls" and "cached · 0 calls"
* *AND* the refresh MUST ask for a confirmation that names "up to 4 live calls" before it runs
* *AND* after the live preview the page MUST show "1 live call spent" with the quota's remaining calls
