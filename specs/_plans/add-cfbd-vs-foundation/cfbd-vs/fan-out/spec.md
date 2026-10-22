# Feature: Fan-out and call budget

One SQL query can become several API requests: one per combination of the requested parameter values, sent in parallel. A per-query call budget makes a broad query fail before it spends quota. This makes "a year range fans out in parallel" demoable on a free key without risking the monthly allowance.

## Background

- The requests of one virtual-table access are the cartesian product of the value sets of all parameters after intersection (`cfbd-vs/pushdown`), with explicit defaults added (`cfbd-vs/requirement-rules`). Duplicates are removed.
- Each request has a canonical slice key: the API path with path parameters filled in, then `?`, then the sent query parameters sorted by name. Values are rendered canonically (integers without decimals, `true`/`false`, strings percent-encoded per RFC 3986). Example: `/games?seasonType=both&week=8&year=2019`.
- The virtual schema property `MAX_CALLS_PER_QUERY` (default 10) caps the planned requests of one virtual-table access.
  - The adapter cannot see at planning time which slices are already stored (`cfbd-vs/read-through`), so stored slices count toward the budget too.
  - The exception is a query restricted to stored data, which never calls the API.
- Exasol plans each virtual table in a statement separately. The budget therefore applies per virtual-table access, not per statement.
- An over-budget query fails while the pushdown SQL is planned, before any request.

## Scenarios

### Scenario: A range and an IN list fan out into their product

* *GIVEN* a deployed `CFBD_VS` with `MAX_CALLS_PER_QUERY = 10`
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)`
* *THEN* the plan MUST contain exactly 6 requests, one for each combination of year 2019–2021 and week 8–9
* *AND* every request MUST carry the explicit default `seasonType=both`

### Scenario: Duplicate values are requested once

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR IN (2020, 2020, 2021) AND P_YEAR BETWEEN 2019 AND 2020 AND P_WEEK IN (8, 8)`
* *THEN* the plan MUST contain exactly one request: year 2020, week 8

### Scenario: An over-budget query is rejected before any call

* *GIVEN* `MAX_CALLS_PER_QUERY = 10`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2000 AND 2024`
* *THEN* the query MUST fail with an error stating that 25 requests are planned, that the budget is 10, and that the remedy is a narrower filter or a higher property value
* *AND* no request SHALL be sent to the API

### Scenario: A query exactly at the budget is allowed

* *GIVEN* `MAX_CALLS_PER_QUERY = 10`
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2015 AND 2024`
* *THEN* the plan MUST contain 10 requests and no error

### Scenario: Stored slices count toward the budget

* *GIVEN* `MAX_CALLS_PER_QUERY = 10`
* *AND* all `/games` season slices from 2000 to 2024 are registered as final stored slices
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2000 AND 2024` without restricting `REQ_SOURCE`
* *THEN* the query MUST fail with the budget error

### Scenario: The budget applies to each virtual-table access

* *GIVEN* `MAX_CALLS_PER_QUERY = 10`
* *WHEN* the user runs one statement that unions `CFBD_VS.RATINGS_SP` filtered to 6 seasons and `CFBD_VS.RATINGS_SRS` filtered to 6 seasons
* *THEN* the statement MUST be accepted
* *AND* each of the two accesses MUST plan 6 requests

### Scenario: The requests of one query run concurrently

* *GIVEN* the mock API answers every request after 2 seconds
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)` in live mode
* *THEN* the query SHOULD complete in less than half the summed latency of its 6 requests (12 seconds)
* *AND* the `REQ_AT` and `REQ_MS` values of the result SHOULD show overlapping request intervals
