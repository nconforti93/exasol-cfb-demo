# Feature: Fetch log and SQL↔HTTP transparency

Every virtual-schema query shows which HTTP requests it became, where each slice came from, and what each request cost. This feeds the SQL↔HTTP inspector and the load and refresh logs. UDFs cannot write tables, so the log travels with the rows (request metadata columns) and with the plan (a manifest in `EXPLAIN VIRTUAL`). Consumers persist it to `CFB_META.FETCH_LOG` from a staged result.

## Background

- Every virtual table has these request metadata columns:

  | Column | Type | Meaning |
  |---|---|---|
  | `REQ_SLICE` | `VARCHAR(2000) UTF8` | Canonical slice key: the path and query string exactly as sent, without host or key |
  | `REQ_SOURCE` | `VARCHAR(8) UTF8` | `LIVE`, `REPLAY` or `STORED` |
  | `REQ_STATUS` | `DECIMAL(3,0)` | Final HTTP status; NULL for `STORED` |
  | `REQ_ATTEMPTS` | `DECIMAL(3,0)` | HTTP attempts including retries (the recorded attempts for `REPLAY`, NULL for a recording that predates them); 0 for `STORED` |
  | `REQ_MS` | `DECIMAL(9,0)` | Request wall time including retries (the recorded time for `REPLAY`); NULL for `STORED` |
  | `REQ_ROWS` | `DECIMAL(18,0)` | Rows the response produced for this table |
  | `REQ_AT` | `TIMESTAMP` | UTC start time of the request (of the original request for `STORED`) |

- The pushdown SQL that `EXPLAIN VIRTUAL` returns carries a machine-readable JSON manifest with:
  - the table and the fetch mode;
  - whether read-through is on;
  - the call budget and the number of planned requests;
  - the ordered list of planned slice keys;
  - `sources`: the row sources the pushdown SQL actually reads (`[]`, `["STORED"]`, `["LIVE"]` or `["REPLAY"]`, or `STORED` together with the fetch source). The budget is `null` when no budget was applied (a stored-only query).
- The fetch log contract is `CFB_META.FETCH_LOG`: `LOGGED_AT`, `CONTEXT`, `TABLE_NAME`, `SLICE_KEY`, `REQ_SOURCE`, `HTTP_STATUS`, `ATTEMPTS`, `MS`, `ROWS_RETURNED`, `REQUESTED_AT`.
  - A reference capture helper in this plan writes one fetch-log row per planned request, from a staged query result plus the manifest of the same query.
  - The loader (Phase 2) and the app backend (Phase 6) use it.

## Scenarios

### Scenario: Every row carries its request metadata

* *GIVEN* a deployed `CFBD_VS` against the mock API in live mode
* *WHEN* the user queries `SELECT * FROM CFBD_VS.RATINGS_SP WHERE P_YEAR = 2024`
* *THEN* every row MUST have `REQ_SLICE = '/ratings/sp?year=2024'`, `REQ_SOURCE = 'LIVE'`, `REQ_STATUS = 200`, `REQ_ATTEMPTS = 1`, a non-negative `REQ_MS` and a non-NULL `REQ_AT`
* *AND* `REQ_ROWS` MUST equal the number of rows of that slice

### Scenario: The slice key is exactly the request that was sent

* *GIVEN* the mock API logs the path and query string of every request it receives
* *WHEN* the user queries `CFBD_VS.GAMES` with `P_YEAR IN (2019, 2020) AND P_WEEK = 8`
* *THEN* the set of distinct `REQ_SLICE` values in the result MUST equal the set of paths with query strings the mock API received
* *AND* no `REQ_SLICE` MUST contain a host name or the key

### Scenario: EXPLAIN VIRTUAL shows the planned requests without calling the API

* *GIVEN* a deployed `CFBD_VS` against the mock API
* *WHEN* the user runs `EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8, 9)`
* *THEN* the returned pushdown SQL MUST contain a manifest that parses as JSON, with the table `GAMES`, the mode, the read-through flag, the budget, the planned request count 6 and the 6 slice keys
* *AND* the mock API MUST receive no request

### Scenario: A staged result is captured into the fetch log

* *GIVEN* the result of a virtual-table query staged into a table, and the manifest of the same query
* *AND* one planned request of that query returned no rows
* *WHEN* the capture helper writes the fetch log
* *THEN* `CFB_META.FETCH_LOG` MUST contain exactly one row per planned slice key
* *AND* each row's source, status, attempts, latency, row count and request time MUST equal the metadata of that slice's rows
* *AND* the planned request without rows MUST be logged with `ROWS_RETURNED = 0`

### Scenario: A stored-only query logs only the slices it read

* *GIVEN* a query whose manifest `sources` is `["STORED"]`, with three planned slices of which two are stored
* *WHEN* the capture helper writes the fetch log
* *THEN* `CFB_META.FETCH_LOG` MUST contain exactly the two stored slices, each with `REQ_SOURCE = 'STORED'` and its row count
* *AND* the unstored slice MUST NOT be logged, in particular not as fetched with `ROWS_RETURNED = 0`
* *AND* a stored slice that holds zero rows SHALL NOT be logged either, because in a stored-only query it cannot be told apart from an unstored one

### Scenario: Stored rows report their origin

* *GIVEN* a stored, final slice
* *WHEN* a query is served from it
* *THEN* its rows MUST have `REQ_SOURCE = 'STORED'`, `REQ_STATUS` NULL, `REQ_MS` NULL and `REQ_ATTEMPTS = 0`
* *AND* `REQ_AT` MUST be the request time of the original fetch that produced the stored rows
