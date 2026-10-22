# Feature: Read-through to stored slices

A virtual-table query serves slices that are already persisted in `CFB_CORE` at zero API calls and fetches only the missing ones. Data the project already paid for is never paid for twice, and the virtual schema stays the one SQL interface to the API, stored or not (D15). This plan defines the contract between the virtual schema and the loader. The loader itself (backfill, `REFRESH_WEEK`, nightly `PROMOTE()`) follows in Phase 2.

## Background

- **Slice:** one planned request of one virtual table, identified by the table name and its canonical slice key (`cfbd-vs/fan-out`). The rows a slice produced carry the slice key in `REQ_SLICE`.
- **Load log contract:** `CFB_META.LOAD_LOG` has one row per stored slice:

  | Column | Type |
  |---|---|
  | `TABLE_NAME` | `VARCHAR(128)` |
  | `SLICE_KEY` | `VARCHAR(2000)` |
  | `IS_FINAL` | `BOOLEAN` |
  | `ROWS_LOADED` | `DECIMAL(18,0)` |
  | `LOADED_AT` | `TIMESTAMP` |
  | `LOADED_BY` | `VARCHAR(64)` |

  The primary key is (`TABLE_NAME`, `SLICE_KEY`). Writers keep the stored rows of a slice and its load-log row consistent in one transaction.
- **Mirror contract:** `CFB_CORE.<TABLE>` exists for every read-through-eligible table. Its column names, column order and data types are identical to `CFBD_VS.<TABLE>`, and both are generated from the same catalog. A slice's stored rows have `REQ_SLICE` equal to its `SLICE_KEY`.
- **Serving rule:** at execution time, a planned slice is served from `CFB_CORE` if and only if `LOAD_LOG` holds a row for it with `IS_FINAL = TRUE`. Otherwise it is fetched. Every slice is served from exactly one source. The adapter cannot run SQL, so this check lives in the pushdown SQL.
- Stored rows are returned with `REQ_SOURCE = 'STORED'`, `REQ_STATUS` and `REQ_MS` NULL, and `REQ_ATTEMPTS = 0`.
- Read-through applies to every table except those the overrides mark as volatile: `/info`, `/info/usage`, `/games/schedule`, `/scoreboard`, `/live/plays` and their child tables. Volatile tables have no mirror. The virtual schema property `READ_THROUGH` (default `TRUE`) switches read-through off for all tables.
- The examples below use replay mode or the mock API. "No request" means that neither the mock API nor the replay store is asked for the slice.

## Scenarios

### Scenario: A stored slice costs zero calls

* *GIVEN* the rows of the slice `/games?seasonType=both&year=2019` in `CFB_CORE.GAMES`, registered in `LOAD_LOG` with `IS_FINAL = TRUE`
* *AND* no replay recording and no mock response exists for that slice
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2019`
* *THEN* the query MUST succeed and return exactly the stored rows, with `REQ_SOURCE = 'STORED'`
* *AND* the fetch UDF MUST NOT be invoked for that slice

### Scenario: A mixed query fetches only the missing slices

* *GIVEN* the 2019 games slice is stored and final, and the 2020 slice is not in `LOAD_LOG`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR IN (2019, 2020)`
* *THEN* the 2019 rows MUST be returned with `REQ_SOURCE = 'STORED'` and the 2020 rows with the fetch mode's source
* *AND* exactly one request (for 2020) SHALL reach the API
* *AND* no slice's rows MUST appear twice

### Scenario: Only final, registered slices are served from storage

* *GIVEN* `CFB_CORE.GAMES` holds rows for the 2021 slice without a `LOAD_LOG` row, and rows for the 2022 slice whose `LOAD_LOG` row has `IS_FINAL = FALSE`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR IN (2021, 2022)`
* *THEN* both slices MUST be fetched
* *AND* none of the stored rows of those slices MUST be returned

### Scenario: A stored empty slice costs zero calls

* *GIVEN* `LOAD_LOG` registers the slice `/games?seasonType=both&year=1871` as final with `ROWS_LOADED = 0`, and `CFB_CORE.GAMES` has no rows for it
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 1871`
* *THEN* the query MUST return zero rows
* *AND* no request SHALL reach the API

### Scenario: Query results can be stored as slices directly

* *GIVEN* an empty `CFB_CORE.GAMES`
* *WHEN* the owner runs `INSERT INTO CFB_CORE.GAMES SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2020` and registers each distinct `REQ_SLICE` of the inserted rows in `LOAD_LOG` as final
* *AND* then runs `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2020` again
* *THEN* the `INSERT ... SELECT *` MUST succeed without a column list or casts
* *AND* the second query MUST return the same rows in all response and parameter columns, with `REQ_SOURCE = 'STORED'`
* *AND* the second query MUST NOT reach the API

### Scenario: Stored and live columns are identical

* *GIVEN* a deployed `CFBD_VS` and its `CFB_CORE` mirrors
* *WHEN* the column metadata of every read-through table is compared between `CFBD_VS` and `CFB_CORE` in `EXA_ALL_COLUMNS`
* *THEN* names, order, data types, precision, scale and character set MUST be identical for every table
* *AND* a query that returns rows from both sources MUST return every value unchanged, without implicit conversion

### Scenario: Readers need no privileges on the stored data

* *GIVEN* a reader with only `SELECT` on `CFBD_VS`, without privileges on `CFB_CORE`, `CFB_META` or the connection
* *WHEN* the reader runs a query that is served partly from storage and partly by a fetch
* *THEN* the query MUST return both the stored and the fetched rows
* *AND* the reader's direct `SELECT` on `CFB_CORE.GAMES` and `CFB_META.LOAD_LOG` MUST be denied

### Scenario: Stored-only queries never call the API

* *GIVEN* some of the `/games` season slices between 1869 and 2026 are stored and final
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 1869 AND 2026 AND REQ_SOURCE = 'STORED'`
* *THEN* the query MUST return exactly the rows of the stored final slices in that range
* *AND* the plan MUST NOT contain a fetch
* *AND* the call budget MUST NOT be applied

### Scenario: Read-through can be switched off

* *GIVEN* the 2019 games slice is stored and final
* *AND* `READ_THROUGH` is set to `FALSE`
* *WHEN* the user queries `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2019`
* *THEN* the slice MUST be fetched
* *AND* no stored rows MUST be returned

### Scenario: Volatile endpoints are always fetched

* *GIVEN* a deployed `CFBD_VS`
* *WHEN* the user queries `CFBD_VS.GAMES_SCHEDULE` or `CFBD_VS.INFO`
* *THEN* the plan MUST contain only a fetch, without a stored branch
* *AND* `CFB_CORE` MUST NOT contain a mirror for these tables

### Scenario: Aggregates over mixed results are correct

* *GIVEN* the 2019 games slice is stored and final, and the 2020 slice is fetched
* *WHEN* the user runs `SELECT COUNT(*), SUM(HOME_POINTS) FROM CFBD_VS.GAMES WHERE P_YEAR IN (2019, 2020)`, and the same query with `GROUP BY P_YEAR`
* *THEN* the results MUST equal the totals over the plain rows of the same query
* *AND* the session MUST stay alive
