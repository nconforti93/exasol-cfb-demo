# Feature: Tier gating

Some CFBD endpoints need a paid Patreon feature. They stay visible as tables, so the schema shows the whole API, but they refuse queries when the key lacks the feature. A free-tier user gets a clear explanation instead of a failed call.

## Background

- `/info` reports the key's features: `adjustedMetrics`, `weather`, `scoreboard`, `livePlayByPlay` and `graphQl`. Calling `/info` consumes no quota (verified 2026-10-21).
- The overrides map gated endpoints to features:

  | Endpoints | Feature |
  |---|---|
  | `/games/weather` | `weather` |
  | `/scoreboard` | `scoreboard` |
  | `/live/plays` | `livePlayByPlay` |
  | `/games/{gameId}/preview/adjusted`, `/wepa/*` | `adjustedMetrics` (presumed from the spec's tags and prose; the overrides flag the mapping as unverified) |

  A gated endpoint's child tables are gated with it.
- The virtual schema property `API_FEATURES` lists the features the key has, comma-separated. The deployment sets it from the `/info` features. It is empty by default, so every gated table refuses queries until the features are known (fail closed).
- The project key (Academic tier, verified 2026-10-21) reports all five features as false.

## Scenarios

### Scenario: Gated endpoints exist as documented tables

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user lists its tables
* *THEN* `GAMES_WEATHER`, `SCOREBOARD`, `LIVE_PLAYS`, `GAMES_PREVIEW_ADJUSTED` and the `WEPA_*` tables MUST exist
* *AND* the comment of each of their parameter columns MUST name the required feature

### Scenario: A gated table refuses queries when the key lacks the feature

* *GIVEN* `API_FEATURES` does not contain `weather`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES_WEATHER WHERE P_YEAR = 2024`
* *THEN* the query MUST fail with an error naming the feature `weather`, the property `API_FEATURES` and how to refresh it
* *AND* no request SHALL be sent to the API

### Scenario: A gated table works when the key has the feature

* *GIVEN* `API_FEATURES` contains `weather`
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES_WEATHER WHERE P_YEAR = 2024`
* *THEN* the plan MUST contain the request for `/games/weather` with `year=2024`

### Scenario: Gating fails closed while features are unknown

* *GIVEN* a virtual schema on which `API_FEATURES` was never set
* *WHEN* the user queries any gated table with a filter that satisfies its rules
* *THEN* the query MUST fail with the gating error
* *AND* queries on ungated tables MUST NOT be affected

### Scenario: An access refusal from the API is reported clearly

* *GIVEN* `API_FEATURES` contains `weather`, but the API answers the request with HTTP 401 or 403
* *WHEN* the user queries `CFBD_VS.GAMES_WEATHER` with a valid filter
* *THEN* the query MUST fail after a single attempt with an error naming the endpoint and stating that the key has no access to it
* *AND* the error MUST NOT contain the key
