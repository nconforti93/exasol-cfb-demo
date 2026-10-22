# Feature: Predicate pushdown

Turns SQL `WHERE` clauses on a virtual table into CFBD query-string parameters. Users filter the `P_` parameter columns with ordinary SQL, contradictions cost nothing, predicates the API cannot express are rejected up front instead of returning silently wrong results, and Exasol computes everything else.

## Background

- The adapter advertises filter capabilities only:
  - comparisons `=`, `<>`, `<`, `<=` (Exasol sends `>` and `>=` as reversed `<` and `<=`), `BETWEEN`, `IN` with a constant list, `LIKE`, `IS NULL`, `IS NOT NULL`, `AND`, `OR`, `NOT`;
  - exact-numeric, double, string, boolean, date, timestamp and null literals.

  It advertises no aggregate, projection, ordering, limit or join capability. Exasol computes those itself.
- **Parameter predicates** (on `P_` columns) become request parameters:
  - `=` and `IN` work on every parameter.
  - `<`, `<=`, `>`, `>=` and `BETWEEN` work on integer parameters only, and must form a bounded range.
  - Parameter predicates combine with `AND`. An `OR` is allowed only when every branch constrains the same single parameter, in which case it acts like `IN`.
  - Values must be literals.
  - Any other predicate on a `P_` column is rejected.
- **Echo semantics:** every row carries, in its `P_` columns, the parameter values of the request that produced it. A parameter that was not sent is NULL. An explicit default (`cfbd-vs/requirement-rules`) counts as sent.
- **Residual predicates** (on response, position or `REQ_*` columns) are applied to the produced rows inside the pushdown SQL. They never change the requests.
- **Order of checks:** intersect the predicates per parameter; on a contradiction, return zero rows; enforce requirement rules and gating; expand into requests (`cfbd-vs/fan-out`); enforce the call budget; emit SQL. Every rejection happens while the pushdown SQL is planned, before any request. `EXPLAIN VIRTUAL` therefore shows it at zero calls.
- Error messages start with `CFBD_VS.<TABLE>:` and name the column or rule involved.

## Scenarios

### Scenario: Equality on a parameter column becomes a request parameter

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.RATINGS_SP WHERE P_YEAR = 2024`
* *THEN* the planned requests MUST be exactly one request with the slice key `/ratings/sp?year=2024`

### Scenario: Rows echo the parameters of their request

* *GIVEN* a deployed `CFBD_VS` in replay mode with a recording for the 2024 games slice
* *WHEN* the user runs `SELECT P_YEAR, P_SEASON_TYPE, P_TEAM FROM CFBD_VS.GAMES WHERE P_YEAR = 2024`
* *THEN* every row MUST have `P_YEAR = 2024`
* *AND* every row MUST have `P_SEASON_TYPE = 'both'`, the explicit default declared for `/games`
* *AND* every row MUST have `P_TEAM` NULL, because `team` was not sent

### Scenario: Bounded integer ranges are enumerated

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user runs `EXPLAIN VIRTUAL` for `SELECT * FROM CFBD_VS.RATINGS_SP WHERE P_YEAR BETWEEN 2019 AND 2021`, and again for `... WHERE P_YEAR >= 2019 AND P_YEAR < 2022`
* *THEN* both plans MUST contain exactly the requests for `year=2019`, `year=2020` and `year=2021`

### Scenario: Open-ended ranges are rejected

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.RATINGS_SP WHERE P_YEAR >= 2020`
* *THEN* the query MUST fail with an error naming `P_YEAR` and stating that a bounded range or an `IN` list is required
* *AND* no request SHALL be sent to the API

### Scenario: IN lists produce one request per value

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.RATINGS_SP WHERE P_TEAM IN ('Michigan', 'Ohio State')`
* *THEN* the plan MUST contain exactly two requests, one for each team

### Scenario: OR on one parameter acts like IN

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.RATINGS_SP WHERE P_YEAR = 2020 OR P_YEAR BETWEEN 2022 AND 2023`
* *THEN* the plan MUST contain exactly the requests for `year=2020`, `year=2022` and `year=2023`

### Scenario: OR across parameters is rejected

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user filters `CFBD_VS.GAMES` with `P_YEAR = 2024 OR P_TEAM = 'Michigan'`, or with `P_YEAR = 2024 OR HOME_POINTS > 50`
* *THEN* each query MUST fail with an error stating that an `OR` is supported only between predicates on one and the same parameter, and suggesting `UNION ALL` of separate queries
* *AND* no request SHALL be sent to the API

### Scenario: Unsupported predicates on parameter columns are rejected

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user filters `CFBD_VS.GAMES` with `P_YEAR = 2024` combined with `P_TEAM LIKE 'Mich%'`, `P_TEAM <> 'Michigan'`, `P_TEAM IS NULL`, `NOT (P_WEEK = 3)` or `P_TEAM < 'M'`
* *THEN* each query MUST fail with an error naming the parameter column and listing the supported predicate forms for it
* *AND* no request SHALL be sent to the API

### Scenario: Parameter values must be literals

* *GIVEN* a native table `S(Y DECIMAL(4,0))` holding the value 2024
* *WHEN* the user runs `SELECT * FROM CFBD_VS.RATINGS_SP R JOIN S ON R.P_YEAR = S.Y`
* *THEN* the query MUST fail with the requirement error for the missing year filter of `RATINGS_SP`, because a join condition is not a parameter value
* *AND* no request SHALL be sent to the API

### Scenario: Predicates on response columns filter rows without changing requests

* *GIVEN* a deployed `CFBD_VS` in replay mode with a recording for the 2024 games slice
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2024 AND (HOME_POINTS > 50 OR AWAY_POINTS > 50) AND VENUE LIKE 'M%'`
* *THEN* the plan MUST contain exactly one request, for the 2024 slice
* *AND* the result MUST contain exactly the rows of that slice that satisfy the response-column predicates

### Scenario: Contradictory filters return no rows and make no calls

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user queries `CFBD_VS.GAMES` with any of the filters `P_YEAR = 2024 AND P_YEAR = 2025`, `P_YEAR BETWEEN 2025 AND 2020`, `P_YEAR IN (2019, 2020) AND P_YEAR > 2021`, or `P_YEAR > 2024 AND P_YEAR < 2025`
* *THEN* each query MUST return zero rows with the table's column names and data types
* *AND* the plan MUST contain no request
* *AND* the fetch UDF MUST NOT be invoked

### Scenario: Contradictions are decided before requirement rules

* *GIVEN* `/plays` requires both `year` and `week`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.PLAYS WHERE P_YEAR = 2020 AND P_YEAR = 2021`
* *THEN* the query MUST return zero rows without an error

### Scenario: Enum parameter values are validated

* *GIVEN* `seasonType` allows `regular`, `postseason`, `both`, `allstar`, `spring_regular` and `spring_postseason`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2024 AND P_SEASON_TYPE = 'Regular'`
* *THEN* the query MUST fail with an error naming `P_SEASON_TYPE` and listing the allowed values
* *AND* no request SHALL be sent to the API

### Scenario: Parameter values are passed safely

* *GIVEN* a deployed `CFBD_VS` with the mock API
* *WHEN* the user queries `CFBD_VS.RATINGS_SP` with `P_TEAM = 'Texas A&M'`, and again with `P_TEAM = 'O''Brien; DROP TABLE X--'`
* *THEN* the requests MUST carry the values percent-encoded as single query values (`team=Texas%20A%26M`), with no additional query parameters
* *AND* every row MUST echo the exact original value in `P_TEAM`
* *AND* the pushdown SQL MUST carry each value only as a quoted string literal, so that no statement other than the pushdown query is executed

### Scenario: Aggregates over a virtual table are computed by Exasol

* *GIVEN* a deployed `CFBD_VS` in replay mode with a recording for the 2024 games slice
* *WHEN* the user runs `SELECT COUNT(*), SUM(HOME_POINTS) FROM CFBD_VS.GAMES WHERE P_YEAR = 2024`, and `SELECT WEEK, COUNT(*) FROM CFBD_VS.GAMES WHERE P_YEAR = 2024 GROUP BY WEEK`
* *THEN* the results MUST equal the counts and sums computed over the plain rows of the same filter
* *AND* the session MUST stay alive
* *AND* the pushdown SQL MUST NOT contain an aggregate function
