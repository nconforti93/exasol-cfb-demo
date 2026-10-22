# Feature: Requirement rules

Enforces CFBD's parameter rules before any call is made: the hard requirements from the spec, and the conditional rules, hidden defaults and row caps that the spec states only in prose. A query that the API would reject, or answer with silently defaulted or truncated data, fails with a message that names the rule. The free-tier quota is never spent on a bad request.

## Background

- Hard requirements come from the spec's `required` flags. All other rules come from `vs/catalog-overrides.yaml`. Each rule has an id `<endpoint>.<rule>`, for example `games_teams.week_team_or_conference_with_year`.
- Rule types:

  | Type | Meaning |
  |---|---|
  | `required` | The parameter must be set |
  | `one_of` | At least one of the listed parameters must be set |
  | `requires_any` | When parameter A is set, at least one of the listed parameters must be set |
  | `excludes` | When parameter A is set, the listed parameters must not be set |
  | `requires_value` | When parameter A has value v, parameter B must have value w |
  | `bounds` | Minimum and maximum value of a parameter |
  | `default` | A hidden API default made explicit: sent and echoed when the user does not filter the parameter |
  | `row_cap` | The API returns at most N rows; the listed completeness parameters become required |

- Hidden API defaults are never relied on. Each one listed in the overrides is either made explicit (`default`), turned into a requirement, or documented as "omission means all values" after verification against the live API.
- Rules apply identically to an endpoint table and all of its child tables. They are checked against the parameter values of every expanded request.
- A violation fails the query while the pushdown SQL is planned. The error has the form `CFBD_VS.<TABLE>: rule <id>: <explanation>`, and no request is sent.

## Scenarios

### Scenario: A missing hard-required parameter is rejected

* *GIVEN* the spec marks `year` and `week` as required for `/plays`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.PLAYS WHERE P_YEAR = 2024`
* *THEN* the query MUST fail with an error naming the rule id and `P_WEEK`
* *AND* no request SHALL be sent to the API

### Scenario: A "required unless" rule accepts either parameter

* *GIVEN* `/ratings/sp` requires `year` unless `team` is specified, and `team` unless `year` is specified
* *WHEN* the user queries `CFBD_VS.RATINGS_SP` without any parameter filter
* *THEN* the query MUST fail with an error naming the rule and both `P_YEAR` and `P_TEAM`
* *AND* the same table filtered only by `P_TEAM = 'Michigan'` MUST be accepted

### Scenario: A conditional requirement applies when its trigger is set

* *GIVEN* `/games/teams` requires one of `week`, `team` or `conference` when filtering by `year`, and requires `year` unless `id` is specified
* *WHEN* the user queries `CFBD_VS.GAMES_TEAMS` with only `P_YEAR = 2024`
* *THEN* the query MUST fail with an error naming the rule and `P_WEEK`, `P_TEAM` and `P_CONFERENCE`
* *AND* the filter `P_YEAR = 2024 AND P_WEEK = 8` MUST be accepted
* *AND* the filter `P_ID = 401628374` alone MUST be accepted

### Scenario: Mutually exclusive parameters are rejected

* *GIVEN* for `/conferences/affiliations`, `year` cannot be combined with `minYear` or `maxYear`
* *WHEN* the user queries `CFBD_VS.CONFERENCES_AFFILIATIONS` with `P_YEAR = 2024 AND P_MIN_YEAR = 2000`
* *THEN* the query MUST fail with an error naming the rule, `P_YEAR` and `P_MIN_YEAR`
* *AND* no request SHALL be sent to the API

### Scenario: A value-dependent requirement is enforced

* *GIVEN* for `/rankings`, `latest` requires `poll = 'cfp'` and cannot be combined with `final`
* *WHEN* the user filters `CFBD_VS.RANKINGS` with `P_YEAR = 2024 AND P_LATEST = TRUE` and no `P_POLL`, or with `P_YEAR = 2024 AND P_POLL = 'cfp' AND P_LATEST = TRUE AND P_FINAL = TRUE`
* *THEN* each query MUST fail with an error naming the violated rule
* *AND* the filter `P_YEAR = 2024 AND P_POLL = 'cfp' AND P_LATEST = TRUE` MUST be accepted

### Scenario: Parameter bounds are enforced

* *GIVEN* for `/info/usage`, `days` has a maximum of 31 and `limit` a maximum of 50
* *WHEN* the user queries `SELECT * FROM CFBD_VS.INFO_USAGE WHERE P_DAYS = 60`
* *THEN* the query MUST fail with an error naming the rule, `P_DAYS` and the maximum 31
* *AND* no request SHALL be sent to the API

### Scenario: Hidden defaults are sent explicitly

* *GIVEN* `/stats/season` defaults `classification` to `fbs`, and the overrides declare that default explicit
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.STATS_SEASON WHERE P_YEAR = 2024`
* *THEN* the planned request MUST include `classification=fbs`
* *AND* when the query runs, every row MUST echo `P_CLASSIFICATION = 'fbs'`
* *AND* a filter `P_CLASSIFICATION = 'fcs'` MUST replace the default in the request

### Scenario: A hidden default can become a requirement

* *GIVEN* `/roster` silently defaults `year` to 2025, and the overrides turn `year` into a requirement
* *WHEN* the user queries `SELECT * FROM CFBD_VS.ROSTER WHERE P_TEAM = 'Michigan'`
* *THEN* the query MUST fail with an error naming the rule and `P_YEAR`, and stating that the API would otherwise silently use 2025

### Scenario: Row-capped endpoints require their completeness parameter

* *GIVEN* `/plays/stats` returns at most 2,000 records and the overrides declare `gameId` as its completeness parameter
* *WHEN* the user queries `SELECT * FROM CFBD_VS.PLAYS_STATS WHERE P_YEAR = 2024 AND P_WEEK = 8`
* *THEN* the query MUST fail with an error naming the rule, the 2,000-row cap and `P_GAME_ID`
* *AND* the filter `P_GAME_ID = 401628374` MUST be accepted

### Scenario: Child tables enforce the rules of their endpoint

* *GIVEN* `/rankings` requires `year`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.RANKINGS_POLLS_RANKS` without `P_YEAR`
* *THEN* the query MUST fail with the same rule id as `CFBD_VS.RANKINGS`

### Scenario: Rule violations are visible in EXPLAIN VIRTUAL at zero calls

* *GIVEN* a deployed `CFBD_VS` against the mock API
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.PLAYS WHERE P_YEAR = 2024`
* *THEN* the statement MUST fail with the same rule error as the query itself
* *AND* the mock API MUST receive no request
