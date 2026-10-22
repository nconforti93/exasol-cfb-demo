# Feature: Flattening and child tables

Turns CFBD's nested JSON responses into relational rows. Nested objects become prefixed columns, arrays of objects become child tables that join back to their parent, and very large objects keep a curated column set plus the raw JSON. Every endpoint can then be queried with plain SQL and stored in `CFB_CORE` without loss.

## Background

- **Nested objects** become columns named `PARENT_CHILD` (camelCase split, uppercased). For example, `offense.rating` → `OFFENSE_RATING` and `defense.havoc.total` → `DEFENSE_HAVOC_TOTAL`.
- **Arrays of objects** become child tables, one per array level:
  - The child table is named after its parent table plus the array field path, e.g. `RANKINGS` → `RANKINGS_POLLS` → `RANKINGS_POLLS_RANKS`.
  - A child row carries the position columns of every level above it, the parent key columns the overrides declare for it, all `P_` parameter columns and all `REQ_*` columns of its request.
  - A child table is queried with the same parameters and rules as its endpoint table.
- **Position columns** are 1-based:
  - `ROW_NO` is the element's position in the response's top-level array, or 1 for an object-root response.
  - `<ARRAY>_NO` is the position within each nested array, e.g. `POLLS_NO`, `RANKS_NO`.
  - A child joins to its parent on `REQ_SLICE` plus the parent's position columns.
- **Hoisting:** an endpoint whose row holds only an identifier plus one array of objects can be declared hoisted in the overrides (`/games/teams`, `/games/players`). Its endpoint table then holds the array's elements, and the identifier is copied in under the declared name.
- **Arrays of scalars** and free-form or variant objects become a JSON text column (`VARCHAR(2000000) UTF8`).
- **Very large objects:** the overrides declare a curated column subset for endpoints whose rows exceed 500 flattened fields (`/games/{gameId}/preview`, `/teams/season/overview`). Their table has the curated columns plus `RAW_JSON` with the complete row object, and no child tables. Generation fails for an endpoint over 500 fields that has no curated declaration.
- **Response column types:**

  | Spec type | Column type |
  |---|---|
  | integer | `DECIMAL(18,0)` |
  | number | `DOUBLE` |
  | boolean | `BOOLEAN` |
  | string (`date-time` format) | `TIMESTAMP`, converted to UTC |
  | other string | `VARCHAR(2000) UTF8`, unless an override sets another size |

## Scenarios

### Scenario: Nested objects become prefixed columns

* *GIVEN* an SP+ response row with the nested fields `offense.rating` and `defense.havoc.total`
* *WHEN* the user queries `SELECT OFFENSE_RATING, DEFENSE_HAVOC_TOTAL FROM CFBD_VS.RATINGS_SP WHERE P_YEAR = 2024`
* *THEN* each row MUST contain the values of `offense.rating` and `defense.havoc.total` of the corresponding response element

### Scenario: Arrays of objects become child tables

* *GIVEN* a rankings response for 2024 with one element per week, each holding `polls[]`, each poll holding `ranks[]`
* *WHEN* the user queries `CFBD_VS.RANKINGS`, `CFBD_VS.RANKINGS_POLLS` and `CFBD_VS.RANKINGS_POLLS_RANKS` with `WHERE P_YEAR = 2024`
* *THEN* `RANKINGS` MUST return one row per week element, without the `polls` array
* *AND* `RANKINGS_POLLS` MUST return one row per poll, with `ROW_NO`, `POLLS_NO` and the declared parent keys `SEASON`, `SEASON_TYPE` and `WEEK`
* *AND* `RANKINGS_POLLS_RANKS` MUST return one row per rank, with `ROW_NO`, `POLLS_NO`, `RANKS_NO` and the declared parent key `POLL`

### Scenario: Child rows join to exactly one parent row

* *GIVEN* the rows of `RANKINGS_POLLS` and `RANKINGS_POLLS_RANKS` for the same filter
* *WHEN* the ranks are joined to the polls on `REQ_SLICE`, `ROW_NO` and `POLLS_NO`
* *THEN* every rank row MUST match exactly one poll row
* *AND* the number of rank rows per poll MUST equal the length of that poll's `ranks` array in the response

### Scenario: Hoisted endpoints expose the array elements directly

* *GIVEN* a `/games/teams` response whose elements hold a game `id` and a `teams[]` array whose elements each hold `stats[]`
* *WHEN* the user queries `CFBD_VS.GAMES_TEAMS` and `CFBD_VS.GAMES_TEAMS_STATS` for the same filter
* *THEN* `GAMES_TEAMS` MUST return one row per element of `teams[]`, carrying the game identifier under the name the overrides declare (`GAME_ID`)
* *AND* `GAMES_TEAMS_STATS` MUST return one row per element of `stats[]`, with the position columns of its team row
* *AND* `CFBD_VS` MUST NOT contain a table that holds only the game identifier

### Scenario: Arrays of scalars become JSON text

* *GIVEN* a game whose `homeLineScores` is the array `[7, 0, 14, 3]`
* *WHEN* the user queries `SELECT HOME_LINE_SCORES FROM CFBD_VS.GAMES` for that game's slice
* *THEN* the value MUST be the JSON text `[7,0,14,3]`

### Scenario: Very large objects keep a curated subset plus the raw JSON

* *GIVEN* a game preview response for one game
* *WHEN* the user queries `CFBD_VS.GAMES_PREVIEW WHERE P_GAME_ID = <id>`
* *THEN* the result MUST contain exactly the curated columns declared in the overrides, plus the parameter, metadata and position columns
* *AND* `RAW_JSON` MUST contain the complete response object, so that parsing it yields JSON equal to the response
* *AND* `CFBD_VS` MUST NOT contain child tables for arrays inside the preview object

### Scenario: Date-time values become UTC timestamps

* *GIVEN* response values `2024-08-24T16:00:00.000Z` and `2024-08-24T18:00:00+02:00` in a `date-time` field
* *WHEN* the rows are returned
* *THEN* both values MUST be `TIMESTAMP '2024-08-24 16:00:00.000'`

### Scenario: Missing and null fields become NULL

* *GIVEN* a response element where one declared field is absent and another is JSON `null`
* *WHEN* the rows are returned
* *THEN* both columns MUST be NULL
* *AND* the query MUST NOT fail

### Scenario: Object-root responses become one row

* *GIVEN* a `/teams/matchup` response, which is a single JSON object with a `games[]` array
* *WHEN* the user queries `CFBD_VS.TEAMS_MATCHUP` for that matchup
* *THEN* the result MUST be exactly one row with `ROW_NO = 1`
* *AND* the matchup's games MUST be returned by `CFBD_VS.TEAMS_MATCHUP_GAMES`, one row per element of `games[]`

### Scenario: Empty responses return no rows

* *GIVEN* a request whose response is an empty JSON array
* *WHEN* the table is queried
* *THEN* the query MUST return zero rows without an error

### Scenario: Values that do not fit their column fail loudly

* *GIVEN* a response with a string longer than its column's declared size, or a non-integer number in a field typed integer
* *WHEN* the table is queried
* *THEN* the query MUST fail with an error naming the table, the column and the offending length or value type
* *AND* the value MUST NOT be truncated, rounded or silently replaced

### Scenario: Every table returns typed rows for a schema-conforming response

* *GIVEN* a synthetic, schema-conforming response for every non-excluded endpoint, available as a replay recording
* *WHEN* each virtual table is queried in replay mode with a filter that satisfies its rules
* *THEN* every query MUST succeed
* *AND* every table whose response path contains at least one element MUST return at least one row
* *AND* every returned value MUST match its column's declared data type
