# Feature: On-demand game preview

The head-to-head page's live moment: a click fetches CFBD's game preview for one game through the virtual schema, and the inspector shows the HTTP request it became. One click, one call, about a second, and never a broad scan, because the serving view only answers for the game id it is given.

## Background

- **`CFB_API_LIVE`** holds the serving views that reach the network, apart from `CFB_API` (`api/serving-contract`), so that a role can be given the stored analyses without the power to spend quota (`api/roles`: the app role has it, the chat role does not). It holds two views:
  - `GAME_PREVIEW`, below;
  - `QUOTA`: one row, the key's quota as `CFB_META.QUOTA` reports it (`loader/quota`), for the app's quota badge. It reads CFBD's free `/info`, which does not count against the quota but needs the network, or a recording in replay mode.
- `CFB_API_LIVE.GAME_PREVIEW`: a view over the federated virtual table `GAMES_PREVIEW` (`/games/{gameId}/preview`, D15). One row per requested game.
  - `GAME_ID` is the request's game id (the virtual table's `P_GAME_ID`), the required filter. The response's own game id column is left out; it carries the same value.
  - The game: `SEASON`, `WEEK`, `SEASON_TYPE`, `START_DATE`, `STATUS`, `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, venue name, city and state, both teams' ids, names, conferences and classifications, and the points once played.
  - The preview: `AVAILABILITY` and `REASON`; the odds (`ODDS_PROVIDER`, `SPREAD`, `OVER_UNDER`, `HOME_MONEYLINE`, `AWAY_MONEYLINE`); each team's record (wins, losses, ties) and ratings as CFBD reports them (SP+ overall rating and rank with offense and defense ratings, SRS rating, Elo, FPI overall rating and rank); and `RAW_JSON` for everything else (key players, recent results, recent meetings, broadcasts).
  - The request: `REQ_SOURCE`, `REQ_STATUS`, `REQ_ATTEMPTS`, `REQ_MS`, `REQ_AT` and `REQ_SLICE` (`cfbd-vs/endpoint-catalog`), for the inspector.
- **UX contract for Phase 6:**
  - the preview is fetched only when a user clicks, never on page load, by polling or in a loop;
  - the game to preview is the pair's next meeting (`CFB_API.MATCHUP_NEXT.GAME_ID`) when there is one, else its latest meeting (`CFB_API.MATCHUP_SERIES.LAST_GAME_ID`), so every pair that has met offers the click (`analyses/head-to-head`); the preview of a played game carries its points;
  - one game id per query, as a literal: `SELECT … FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <id>`;
  - the page shows a "fetching from CFBD" state until the row arrives. S1 measured the endpoint at 0.7 s at CFBD and about 0.6 s on the database side, so about 1.3 s end to end, under the mission's 2 s target for an on-demand lookup (measured live in task 7.3);
  - the page shows `REQ_SOURCE` and `REQ_MS` in the inspector, and `EXPLAIN VIRTUAL` of the same query shows the planned request at 0 calls.
- **The stored series and CFBD agree.** The preview's `RAW_JSON` carries CFBD's recent meetings of the two teams. Task 7.3 compares them with the stored meetings of `CFB_API.MATCHUP_GAMES` at no extra call; only derived results (equal or not, and the counts) are written down.
- **Calls:** each query costs one call unless its slice is stored (read-through, `cfbd-fetch/read-through`). Previews are not stored in this phase (`PROMOTE()` stays deferred), so a repeated click costs another call; Phase 6 may cache a preview per session. Callers that run it on purpose log the fetch (`cfbd-fetch/fetch-log`); how the backend writes `CFB_META.FETCH_LOG` is Phase 6's decision.
- **Guards** come from the virtual schema (`cfbd-vs/requirement-rules`, `cfbd-vs/fan-out`, `cfbd-vs/pushdown`), and the view keeps them:
  - without a literal game id the query is refused before any call, naming the required parameter. A game id computed by an expression or a subquery never reaches the adapter (architecture, probe 1.4e), so it is refused the same way;
  - a list of more game ids than `MAX_CALLS_PER_QUERY` (10) is refused before any call;
  - in replay mode a recorded preview is served at 0 calls, and a missing recording fails the query.
- **Never timed by the performance harness.** `make analyses-perf` leaves `CFB_API_LIVE` out by construction; the preview's latency comes only from task 7.3's ledgered calls (`plan.md`).
- The examples run in the integration-test namespace in live mode against the mock API, which answers `/games/{gameId}/preview` with synthetic schema-valid bodies (with points for completed games), unless a scenario says replay.

## Scenarios

### Scenario: One click fetches one live preview

* *GIVEN* a week-10 game id of the sim league
* *WHEN* the user runs `SELECT * FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <id>`
* *THEN* it MUST return one row with that `GAME_ID` and the teams of the mock's body, with `REQ_SOURCE` `LIVE` and `REQ_STATUS` 200
* *AND* the mock MUST have received exactly one request, for `/games/<id>/preview`

### Scenario: The latest meeting of a pair can be previewed after it was played

* *GIVEN* the latest completed meeting of two teams of the sim league that do not meet again this season, as `CFB_PRED.MATCHUP_SERIES.LAST_GAME_ID`
* *WHEN* the user runs `SELECT * FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <that id>`
* *THEN* it MUST return one row with both teams' points from the mock's body and `REQ_SOURCE` `LIVE`
* *AND* the mock MUST have received exactly one request

### Scenario: A preview query without a literal game id is refused before any call

* *GIVEN* the deployed view
* *WHEN* the user runs `SELECT * FROM CFB_API_LIVE.GAME_PREVIEW`, and then the same query with `WHERE GAME_ID = (SELECT MIN("GAME_ID") FROM CFB_MODEL.GAME WHERE "SEASON" = 2026 AND "WEEK" = 10)`
* *THEN* both queries MUST fail with a message that names the required parameter `gameId`
* *AND* the mock MUST NOT receive any request

### Scenario: A query for more games than the budget is refused before any call

* *GIVEN* eleven week-10 game ids
* *WHEN* the user runs `SELECT * FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID IN (<eleven ids>)`
* *THEN* the query MUST fail stating that 11 requests are planned and the budget is 10
* *AND* the mock MUST NOT receive any request

### Scenario: The plan shows the request before it is sent

* *GIVEN* a week-10 game id
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <id>`
* *THEN* the pushdown manifest MUST list exactly one planned request, `/games/<id>/preview`
* *AND* the mock MUST NOT receive any request

### Scenario: Replay mode serves a recorded preview without a call

* *GIVEN* `FETCH_MODE` `replay` and a synthetic recording of the preview of a week-10 game
* *WHEN* the user runs `SELECT * FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <id>`
* *THEN* it MUST return the recorded preview with `REQ_SOURCE` `REPLAY`
* *AND* the mock MUST NOT receive any request
