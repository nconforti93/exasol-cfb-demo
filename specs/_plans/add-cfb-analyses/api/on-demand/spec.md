# Feature: On-demand serving views

The live beat of head-to-head is a click: the app fetches CFBD's preview of one game, which nobody stores, through the virtual schema at one call per click. The views that can call CFBD live in their own schema, `CFB_API_LIVE`, which the app role may read and the chat role may not (`api/roles`). The virtual schema's guards apply unchanged: no literal game id, no call; more games than the budget, no call; replay mode serves a recording at no call.

## Background

- **`CFB_API_LIVE.GAME_PREVIEW`** (over the virtual table `CFBD_VS.GAMES_PREVIEW`, endpoint `/games/{gameId}/preview`): one row per requested game. It shows every column of the virtual table except `ROW_NO` and `ASSEMBLED_AT`, under the same names and with the catalog's comments (`vs/catalog/catalog.json`): the game header (`GAME_ID`, `GAME_SEASON`, … `GAME_PLAYOFF_BOWL_NAME`), `AVAILABILITY`, `REASON`, the odds and the per-team record and rating columns (`ANALYSIS_*`), `RAW_JSON`, the parameter `P_GAME_ID`, and the request columns `REQ_SLICE`, `REQ_SOURCE`, `REQ_STATUS`, `REQ_ATTEMPTS`, `REQ_MS`, `REQ_ROWS`, `REQ_AT`. The file `sql/api_live/010_game_preview.sql` is rendered from the catalog and checked against it.
  - The filter column is `P_GAME_ID`, unrenamed, so the virtual schema's pushdown sees exactly the column it requires.
  - Each query with a literal `P_GAME_ID` makes one CFBD call (live) or none (replay, or a stored slice). An `IN` list may hold at most `MAX_CALLS_PER_QUERY` (10) ids.
  - The view comment says that each query makes one CFBD call per game id.
- **`CFB_API_LIVE.QUOTA`** (over `CFB_META.QUOTA`): the key's quota, one row, with the columns of `CFB_META.QUOTA`. Reading it reads CFBD's `/info`, which costs no call but needs the network, or the mock in tests.
- The lint allows exactly `{VS_NAME}.GAMES_PREVIEW` and `{META_SCHEMA}.QUOTA` as references of `sql/api_live/`; any other virtual table or schema is refused.
- Deploying the views makes no CFBD request.
- The examples run in the integration-test namespace with the mock CFBD server, which serves synthetic preview bodies (`tests/synthetic/fake.py`), and with a synthetic replay recording in BucketFS. Every query runs as the app user `CFB_TEST_APP`.

## Scenarios

### Scenario: One query fetches one live preview

* *GIVEN* live mode against the mock and a 2026 week-10 game of the synthetic league
* *WHEN* the app user queries `CFB_API_LIVE.GAME_PREVIEW` with `P_GAME_ID` equal to that game's id
* *THEN* the result MUST be one row with that `GAME_ID`, `REQ_SOURCE` 'LIVE' and `REQ_ROWS` 1
* *AND* the mock MUST have received exactly one request, for `/games/<id>/preview`

### Scenario: A preview query without a literal game id is refused before any call

* *GIVEN* live mode against the mock
* *WHEN* the app user queries `CFB_API_LIVE.GAME_PREVIEW` without a filter, and again with `P_GAME_ID` taken from a subquery
* *THEN* both queries MUST fail with a message that names `gameId`
* *AND* the mock MUST NOT receive any request, during the queries or during the deploy before them

### Scenario: Replay serves a recorded preview without a call

* *GIVEN* replay mode and a synthetic recording of one game's preview in BucketFS
* *WHEN* the app user queries `CFB_API_LIVE.GAME_PREVIEW` for that game
* *THEN* the result MUST be one row with `REQ_SOURCE` 'REPLAY'
* *AND* the mock MUST NOT receive any request
