# Feature: SQL↔HTTP inspector

Demo beat 2 in the app. The inspector shows the SQL behind every page, what the virtual schema planned to request for it (`EXPLAIN VIRTUAL`, free), the HTTP requests those plans become, and what each request cost (the `REQ_*` columns). A console of four curated queries over the virtual schema shows the platform's promises with evidence: a year range fans out into parallel requests, a contradiction costs no call, 157 seasons of stored history cost no call, and a query over the budget is refused before it is sent. The console runs as an ordinary reader of the virtual schema, so what it shows is what any reader with `SELECT` gets.

## Background

- **Query log.** For each browser session the backend keeps its last 50 statements. Each entry records:
  - the SQL text exactly as the driver sent it, which is pyexasol's formatted statement text;
  - the principal: `app` (the read user) or `reader` (the console user), with the user's name;
  - the page, the start time, the milliseconds, the row count and any error;
  - when the result has `REQ_*` columns, one line per slice: source, status, attempts, milliseconds, rows and request time;
  - the calls the statement spent (`app/live-spend`).
- **Plan on demand.** For a logged statement, the inspector runs `EXPLAIN VIRTUAL` on its recorded text, as the same principal that ran it, at 0 calls.
  - For each pushdown it shows the manifest: table, fetch mode, read-through, budget, planned requests, slice keys and `sources` (`cfbd-fetch/fetch-log`).
  - Each slice key is also shown as the HTTP request it becomes, for example `GET https://api.collegefootballdata.com/games/teams?seasonType=both&team=Michigan&year=2022`. The key and the authorization header are never shown.
  - A query that reads only `CFB_API` views has no pushdown, and the inspector shows it as "no virtual table: 0 calls".
  - Readers may run `EXPLAIN VIRTUAL` (foundation probe 1.4g), also through a serving view (Phase 5 probe 1.1(b)), so neither path needs owner rights for it.
  - A "copy SQL" control copies the logged text, so the presenter can paste the same statement into a SQL client.
- **Waterfall.** The slices of a statement, or of a refresh run, are drawn as bars from their request time over their milliseconds, so overlapping bars show requests that ran in parallel. Replayed rows carry recorded timings, and the waterfall labels them so.
- **Refresh requests.** For a refresh run, the inspector lists the rows of `CFB_API.FETCHES` whose `CONTEXT` is `REFRESH_WEEK#<run id>`, next to the per-step calls of `CFB_API.REFRESH_TIMELINE`. While the run is `RUNNING`, the list is polled with the timeline, so the requests of each fetch step appear as soon as the step commits.
- **Console.** It offers four curated queries over the virtual schema. Their texts are in `plan.md`, Key Interfaces, and are fixed in code; the console accepts no free text.
  - It explains and runs them as the console user (`app/access`): `CREATE SESSION` and the role `CFB_READER`, nothing else. The fetch-log rows of its runs are written on the operator path.
  - It exists only when the operator path is on and a console user is configured; otherwise `GET /api/config` says why.

  | Query | Shows |
  |---|---|
  | `fan-out` | `GAMES_TEAMS` for one team over the seasons 2022–2024: 3 slices fetched in parallel |
  | `contradiction` | the same table with `P_YEAR = 2023 AND P_YEAR = 2024`: 0 slices, 0 calls |
  | `stored` | `GAMES` of every season from 1869 to 2025, restricted to `REQ_SOURCE = 'STORED'`, counted per stored slice: the plan contains no fetch and no call budget applies (`cfbd-fetch/read-through`), so 157 seasons cost 0 calls |
  | `over-budget` | `GAMES_TEAMS` over the seasons 2010–2024: 15 slices, refused at plan time against the budget of 10 |

  Each query can be explained at 0 calls, and run under the spending guards (`app/live-spend`). Rows and fetch-log rows of a console run carry the context `APP_CONSOLE#<action id>`.
  - The console's help text also points at stored history the pages read at 0 calls, such as a head-to-head series since 1897, whose plan reads "no virtual table: 0 calls".
- In the integration-test namespace the console reads `TEST_CFBD_VS` as `CFB_TEST_READER`.
- The examples run on the app test stack, live against the mock API. The mock delays `/games/teams` by 400 ms. The 1997 `GAMES` slice is registered as stored and final (harness `register_slice`). A temporary ledger is used, as in `app/live-spend`.

## Scenarios

### Scenario: Every query of a page is listed with its exact SQL

* *GIVEN* a browser session on the app test stack
* *WHEN* the odds page of 2026 week 9 is loaded and the inspector's query log is read
* *THEN* the log MUST hold one entry per statement the page ran, each with the principal `app`, the milliseconds and the row count
* *AND* the SQL of each entry MUST equal the catalog statement rendered with the page's parameters, and running that text as `CFB_TEST_APP` MUST return the logged row count

### Scenario: A serving-view query plans no request

* *GIVEN* the logged statement that read `TEST_CFB_API.PLAYOFF_ODDS`
* *WHEN* the user asks for its plan
* *THEN* the inspector MUST report no pushdown and "no virtual table: 0 calls"
* *AND* the mock MUST NOT receive any request

### Scenario: A preview shows its planned request and its request columns

* *GIVEN* a preview of a week-10 game fetched live in the session
* *WHEN* the user opens its entry and asks for its plan
* *THEN* the entry MUST show one slice with source `LIVE`, status 200, 1 attempt, its milliseconds, its request time and 1 call spent
* *AND* the plan MUST show table `GAMES_PREVIEW`, budget 10, and exactly one planned slice key, `/games/<id>/preview`, shown as `GET https://api.collegefootballdata.com/games/<id>/preview`
* *AND* neither the entry nor the plan MUST contain the test connection's marker key

### Scenario: A year range fans out in parallel

* *GIVEN* the console's `fan-out` query and a ledger with room
* *WHEN* the presenter explains it, and then runs it with `max_calls` 3
* *THEN* the plan MUST list three slice keys, for 2022, 2023 and 2024, at 0 calls
* *AND* the run MUST make exactly three requests to the mock, each of which starts before any of them ends
* *AND* the log entry MUST name the principal `reader` and the user `CFB_TEST_READER`
* *AND* the waterfall MUST show three overlapping bars, and the ledger and `TEST_CFB_META.FETCH_LOG` MUST each gain three rows

### Scenario: A contradiction costs no call

* *GIVEN* the console's `contradiction` query
* *WHEN* the presenter explains it and runs it with `max_calls` 0
* *THEN* the plan MUST show 0 planned requests and empty `sources`
* *AND* the run MUST return no rows and report 0 calls
* *AND* the mock MUST NOT receive any request, and the ledger MUST NOT change

### Scenario: Stored history costs no call

* *GIVEN* the console's `stored` query, and the `/games` slices that `TEST_CFB_META.LOAD_LOG` registers as stored and final, among them 1997
* *WHEN* the presenter explains it and runs it with `max_calls` 0
* *THEN* the plan MUST contain no fetch and no budget, although the range spans 157 seasons
* *AND* the run MUST return one row per registered stored `/games` slice of 1869–2025, each with `REQ_SOURCE` `STORED` and its game count, and MUST report 0 calls
* *AND* the mock MUST NOT receive any request, and the ledger MUST NOT change
* *AND* `TEST_CFB_META.FETCH_LOG` MUST gain one row with `REQ_SOURCE` `STORED` per returned slice

### Scenario: A query over the budget is refused at plan time

* *GIVEN* the console's `over-budget` query
* *WHEN* the presenter explains it and then tries to run it
* *THEN* the plan MUST fail with the virtual schema's message that 15 requests are planned against a budget of 10
* *AND* the run MUST be refused with the same message
* *AND* the mock MUST NOT receive any request

### Scenario: The refresh's requests are listed per run

* *GIVEN* a refresh of 2026 week 10 run by the app in replay mode
* *WHEN* the user opens that run in the inspector while step `WAIT` runs, and again after the run
* *THEN* the first reading MUST already list one row of `CFB_API.FETCHES` per slice of the run's finished fetch steps, each with `REQ_SOURCE` `REPLAY`
* *AND* after the run the list MUST hold one row per fetched slice of the run, and the calls per step MUST equal those of `CFB_API.REFRESH_TIMELINE` for the run

### Scenario: The console exists only with the operator path

* *GIVEN* the backend started with `APP_OPERATOR=off`
* *WHEN* a client lists the console queries and posts an explain of `fan-out`
* *THEN* both requests MUST be refused with status 403, naming the reason
* *AND* the inspector MUST NOT show the console, while it still shows the query log
