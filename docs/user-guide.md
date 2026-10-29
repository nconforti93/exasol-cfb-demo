# User Guide

> Work in progress: sections are added as features land. This version covers the CFBD virtual schema (deploying it, storing the key, querying it, its properties) and the loader that persists and refreshes the data ([§9](#9-persisting-and-refreshing)). The app and the chat are not part of it yet.

## Prerequisites
- Exasol Personal, local deployment (laptop-class machine is enough), or any Exasol 2026.x database.
- A free CollegeFootballData API key: https://collegefootballdata.com/key. The demo is designed to run within the free tier (1,000 calls/month).
- The repository checked out, with [uv](https://docs.astral.sh/uv/) installed. Every `make` target runs through `uv run`, except `make db-check`, which runs `python3 scripts/dbcheck.py` and needs only a Python 3 interpreter.

## 1. Deploy and grant readers

`sys` owns the real deployment: the schemas `CFB_INFRA`, `CFB_META`, `CFB_CORE` and `CFB_STAGE`, the adapter, the fetch function, the loader scripts and the virtual schema `CFBD_VS`. `make deploy` and `make grants` log in as `sys`, with the password read at run time from the Exasol Personal secrets file. It is never printed or written. Both targets first check that the database is reachable (start it with `exasol start -d local`).

```bash
make deploy    # build the bundle, create everything, create CFBD_VS, then deploy the loader (§9)
make grants    # create the role CFB_READER, grant it SELECT ON CFBD_VS
```

Then create the key connection (section 2) and run **`make deploy` again**. The second run syncs the `API_FEATURES` property from your CFBD tier (see [Tier gating](#tier-gating)). Re-running the whole sequence is always safe: every step is idempotent.

What each step does:

- `make deploy` creates what is missing, replaces the adapter on every run, and replaces the fetch function only when its text changed. A table that already exists must match its DDL exactly, otherwise the deploy stops, names the column differences and alters nothing. Extra options go through `ARGS`, for example `make deploy ARGS="--property MAX_CALLS_PER_QUERY=5"`. Properties you changed with `ALTER VIRTUAL SCHEMA` stay as they are, except `CONNECTION_NAME` and the three schema names, which every deploy sets (use `ARGS="--connection <NAME>"` for a connection other than `CFBD_API`), and `API_FEATURES`, which a deploy that can reach `/info` overwrites (see [Tier gating](#tier-gating)).
- `make grants` creates the role `CFB_READER` and grants it `SELECT ON CFBD_VS`. If the virtual schema does not exist yet, it says so; run `make deploy`, then `make grants` again.

To let a database user query the virtual schema, give that user a session and the role:

```sql
CREATE USER ANALYST IDENTIFIED BY "<a password>";
GRANT CREATE SESSION TO ANALYST;
GRANT CFB_READER TO ANALYST;
```

A reader needs nothing else: no rights on `CFB_CORE`, `CFB_META` or the connection. The virtual schema runs with its owner's rights, so readers can reach the stored data and the key only through it. Readers may also run `EXPLAIN VIRTUAL`.

## 2. Store your CFBD API key in Exasol

The key lives in exactly one place: an Exasol CONNECTION named `CFBD_API`. Nothing else in the project (config files, environment variables, the app) ever holds it.

Run as a database administrator (e.g. `sys`) in a SQL client:

```sql
CREATE CONNECTION CFBD_API
  TO 'https://api.collegefootballdata.com'
  USER ''
  IDENTIFIED BY '<your CFBD API key>';
```

Type the statement in a SQL client (DBeaver, the Exasol VS Code extension, `exapump sql` interactively). Don't pass it as a shell command-line argument, where it would land in your shell history.

### Who can read it

- The fetch function `CFB_INFRA.CFBD_FETCH` is owned by `sys`, and `sys` can read every connection, so it needs no grant. There is no `CFB_OWNER` user and no `GRANT ACCESS ON CONNECTION` step.
- Readers (`CFB_READER`) can use the key only through queries of `CFBD_VS`. They can't select it, and no error message or result contains it.
- The consequence of `sys` ownership: "only the fetch function reads the key" holds for readers, not for `sys` itself. That is acceptable for a demo on a local database.
- Never grant `ACCESS ANY CONNECTION` to anyone.

### Check it

```sql
-- the connection exists (the key itself is never shown)
SELECT CONNECTION_NAME, CONNECTION_STRING, CREATED
FROM EXA_DBA_CONNECTIONS
WHERE CONNECTION_NAME = 'CFBD_API';

-- after deploy: your tier, remaining calls and features (this call is free)
SELECT * FROM CFBD_VS.INFO;
```

`CFBD_VS.INFO` is a live request to the CFBD `/info` endpoint. It costs no quota and is never read from stored data. If it fails, the message names the reason; a failure to resolve the host name points at [Name resolution](#3-name-resolution-inside-udfs).

### Rotate or replace the key

```sql
ALTER CONNECTION CFBD_API
  TO 'https://api.collegefootballdata.com'
  USER ''
  IDENTIFIED BY '<your new CFBD API key>';
```

Nothing else changes and no redeploy is needed. The next query uses the new key.

### Tier gating

Some CFBD endpoints need a paid tier (weather, scoreboard, live play-by-play, adjusted metrics, GraphQL). The virtual schema property `API_FEATURES` lists the features your key has. It is empty by default and **fails closed**: a query on a gated table is refused at planning time, before any request, with an error that names the missing feature:

```
CFBD_VS.GAMES_WEATHER: rule gating.weather: this table needs the CFBD feature 'weather', which is
not in the virtual schema property API_FEATURES (currently: none). Refresh it with the deployment
(`make deploy` syncs it from /info) or ALTER VIRTUAL SCHEMA CFBD_VS SET API_FEATURES = '<comma-separated features>'
```

`make deploy` fills `API_FEATURES` from `CFBD_VS.INFO` when the connection exists and the API answers; otherwise it leaves the property as it is and prints why. Gated tables also say so in their table and column comments. You can set the property by hand with `ALTER VIRTUAL SCHEMA`, but the next `make deploy` that reaches `/info` overwrites it with what the API reports; pass `ARGS="--no-sync-api-features"` to keep a hand-set value. A feature your key lacks is refused by the API itself (HTTP 401/403, see [Errors](#errors)).

## 3. Name resolution inside UDFs

On Exasol Personal (nano 2026.2), UDFs cannot resolve host names: the UDF sandbox has no DNS configuration. The project's fetch function therefore falls back to DNS over HTTPS (DoH) whenever the normal lookup fails. The system resolver is always tried first, so the fallback does nothing once your runtime can resolve names. Only the address lookup goes to the DoH service. The HTTPS connection to CollegeFootballData still verifies its certificate against `api.collegefootballdata.com`, and the DoH request carries only the host name, never the key.

The virtual schema property `DOH_URL` sets the DoH endpoint:

| Value | Meaning |
|---|---|
| not set | `https://1.1.1.1/dns-query` |
| `https://<IP literal>/<path>` | Use this DoH service, for example `https://8.8.8.8/dns-query` |
| `off` | No fallback: a failed lookup fails the query with the system resolver's error and the note that the fallback is off |

The host of the URL **must be an IP literal** (the fallback can't resolve a name to find the resolver), and the scheme must be `https`. Anything else is rejected when you set the property. If your network blocks 1.1.1.1, point the property at another DoH service reachable by IP:

```sql
ALTER VIRTUAL SCHEMA CFBD_VS SET DOH_URL = 'https://8.8.8.8/dns-query';
```

If `make deploy` could not sync `API_FEATURES` because the name lookup failed, fix the cause (or `DOH_URL`) and run `make deploy` again.

A failed DoH lookup fails the query after one attempt (it is not retried) with an error that names the host, the `DOH_URL` value and the reason, and points at `make dns-check`.

### Check it

```bash
make dns-check
```

The check creates a small function `CFB_TOOLS.DNS_CHECK` as `sys`, resolves `api.collegefootballdata.com` through the same resolver the fetch function uses, opens a TLS connection on port 443 and verifies the certificate. It sends **no HTTP request**, so it uses no CFBD quota. It prints the resolution path (`system` or `doh`), the addresses, the TLS result and whether `/etc/resolv.conf` and `/etc/hosts` are empty, and exits non-zero on a failure. It reads the endpoint from the environment variable `DOH_URL` (default `https://1.1.1.1/dns-query`), not from the virtual schema property, so to test another service run `DOH_URL=https://8.8.8.8/dns-query make dns-check`. Run it before the first live query, and again after you change networks.

## 4. Querying the virtual schema

Every CFBD endpoint is a table in `CFBD_VS`; array fields of a response become child tables (for example `GAMES_TEAMS` has one row per team of a game). `SELECT TABLE_NAME, TABLE_COMMENT FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'CFBD_VS'` lists them with the endpoint each one reads, and every table and column carries a comment taken from the CFBD API specification. Column names that collide with Exasol reserved words get a trailing underscore (for example `YEAR_` in `RATINGS_SP`).

### `P_` columns are the API's knobs

Each query or path parameter of an endpoint is a column named `P_<PARAMETER>`: `P_YEAR`, `P_WEEK`, `P_SEASON_TYPE`, `P_TEAM`. Putting a predicate on a `P_` column decides which HTTP requests the query makes. Each row echoes the parameter values of the request that produced it (NULL for a parameter that wasn't sent), so rows always satisfy the predicates you wrote.

```sql
SELECT "ID", "HOME_TEAM", "AWAY_TEAM", "HOME_POINTS", "AWAY_POINTS"
FROM CFBD_VS.GAMES
WHERE P_YEAR = 2024 AND P_WEEK = 8;
-- one request: GET /games?seasonType=both&week=8&year=2024
```

What you can write on a `P_` column:

- `=` and `IN (...)`, for every type.
- For integer parameters also `<`, `<=`, `>`, `>=` and `BETWEEN`. A range must have both bounds. `P_YEAR > 2018` alone is refused, because it would mean an unbounded number of requests.
- `AND` between predicates, and `OR` between predicates on the **same** `P_` column.
- Several values or a range expand into one request per combination (a request is a *slice*): `P_YEAR IN (2019, 2020, 2021) AND P_WEEK IN (8, 9)` is six requests.

Everything else on a `P_` column is refused with a message, instead of silently returning nothing: `LIKE`, `<>`, `NOT`, comparisons between two columns, an `OR` across different parameters (use `UNION ALL` of separate queries), and a literal of the wrong type. A contradiction such as `P_YEAR = 2024 AND P_YEAR = 2025` (or `P_YEAR = NULL`) returns zero rows and makes no request.

Predicates on ordinary (response) columns work everywhere, but they filter **after** the API answered: `WHERE HOME_TEAM = 'Michigan'` does not reduce the requests. To reduce them, use the matching `P_` column (`P_TEAM`). Aggregates, `GROUP BY`, `ORDER BY`, `LIMIT` and joins are all computed by Exasol on the rows the virtual schema returns, and give correct results. Row order is not guaranteed; use `ORDER BY`.

Some parameters have a default that the API applies silently. The virtual schema sends such a default explicitly and echoes it, for example `P_SEASON_TYPE = 'both'`, so a row always tells you what was asked.

### Requirement rules and errors

Many endpoints require some parameters, or forbid combinations. The virtual schema checks these rules while it plans the query, so a bad query fails before any request and costs nothing. Every error starts with `CFBD_VS.<TABLE>:`, names the rule and the `P_` columns involved, and the same error appears in `EXPLAIN VIRTUAL` (below):

```
CFBD_VS.PLAYS: rule plays.spec_required: required parameter not set: P_WEEK (the API spec marks it as required)
CFBD_VS.GAMES: rule games.year_or_id: at least one of P_YEAR, P_ID must be set (the API rejects the request unless year or id is given)
```

Rule types are: a parameter is required, one of several is required, one parameter needs another (optionally with a given value), two parameters exclude each other, a parameter must have a given value when another has, numeric bounds, and, for endpoints that silently cap their answer (for example `PLAYS_STATS` at 2,000 rows), a parameter that makes the result complete. A parameter value that doesn't fit its type or its allowed values (for example an unknown `P_SEASON_TYPE`) is refused the same way and the message lists the allowed values.

A join condition is not a parameter value. `SELECT * FROM CFBD_VS.RATINGS_SP R JOIN S ON R.P_YEAR = S.Y` fails with the missing-year error of `RATINGS_SP`. See the limitation below.

### The call budget

CFBD's free tier allows 1,000 calls per month, so every query has a ceiling on the requests it may plan.

- `MAX_CALLS_PER_QUERY` (default 10, 1 to 1000) is checked **while planning**, against the number of planned requests per virtual-table access, **stored slices included**. A query that plans more fails before it makes any request:

  ```
  CFBD_VS.GAMES: 30 requests are planned but MAX_CALLS_PER_QUERY is 10. Use a narrower filter or raise MAX_CALLS_PER_QUERY.
  ```

  Raise it deliberately: `ALTER VIRTUAL SCHEMA CFBD_VS SET MAX_CALLS_PER_QUERY = 40;`. A join or `UNION ALL` of two virtual tables is budgeted per table.
- A query restricted with `REQ_SOURCE = 'STORED'` plans no fetch, so the budget does not apply (see [Stored-only queries](#stored-only-queries)).
- Independently of the budget, a filter that allows more than **10,000 requests** for one virtual-table access is refused outright, stored-only queries included: "the filter allows N requests, but the adapter plans at most 10,000 ..." Narrow the `P_` predicates.
- `MAX_PARALLEL_FETCHES` (default 6, 1 to 16) is how many of a query's requests run at the same time. All requests of one query go through one group of the fetch function, which fetches them with a bounded thread pool. A lower value is gentler on CFBD's rate limit; it never changes what a query returns or how many calls it makes. Failed requests are retried up to 4 attempts in total (HTTP 429, 5xx, timeouts and connection errors; a `Retry-After` of up to 30 s is honored). Calls that sibling requests already made are not refunded when one request finally fails.

### `EXPLAIN VIRTUAL`: see the requests before you make them

`EXPLAIN VIRTUAL` shows what a query becomes, makes no CFBD call, and shows planning errors as errors:

```sql
EXPLAIN VIRTUAL
SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2024 AND P_WEEK = 8;
```

The result has the columns `PUSHDOWN_ID`, `PUSHDOWN_SQL`, `PUSHDOWN_JSON` and `PUSHDOWN_INVOLVED_TABLES`. `PUSHDOWN_SQL` starts with a JSON **manifest** in a comment:

```
/*CFBD {"budget":10,"fetch_mode":"live","planned_requests":1,"read_through":true,"slice_keys":["/games?seasonType=both&week=8&year=2024"],"sources":["STORED","LIVE"],"table":"GAMES"}*/
```

| Key | Meaning |
|---|---|
| `table` | The virtual table |
| `fetch_mode` | `live` or `replay` |
| `read_through` | Whether stored slices are used |
| `budget` | The `MAX_CALLS_PER_QUERY` applied, or `null` when no budget applied (stored-only query) |
| `planned_requests` | The number of requests the filter expands to |
| `slice_keys` | The planned requests in order: path plus sorted query string, exactly as sent (without host or key) |
| `sources` | The row sources the SQL actually reads: `[]`, `["STORED"]`, `["LIVE"]` or `["REPLAY"]`, or `STORED` together with the fetch source |

The manifest lists every planned request, including ones that return no rows, so it is the place to see what a query will cost. The rest of the statement is the SQL Exasol runs: the stored branch, the fetch branch and your residual predicates.

### `REQ_*` columns: what each row cost

Every table also has request metadata columns, filled for every row:

| Column | Meaning |
|---|---|
| `REQ_SLICE` | The request that produced the row, as in the manifest (for example `/games?seasonType=both&week=8&year=2024`) |
| `REQ_SOURCE` | `LIVE`, `REPLAY` or `STORED` |
| `REQ_STATUS` | Final HTTP status; NULL for `STORED` |
| `REQ_ATTEMPTS` | HTTP attempts including retries (for `REPLAY`, the attempts of the recorded request); 0 for `STORED` |
| `REQ_MS` | Wall time of the request including retries (the recorded time for `REPLAY`); NULL for `STORED` |
| `REQ_ROWS` | Rows the response produced for this table |
| `REQ_AT` | UTC start time of the request (of the original request for `STORED`) |

```sql
SELECT REQ_SLICE, REQ_SOURCE, REQ_STATUS, REQ_ATTEMPTS, REQ_MS, COUNT(*) AS "ROWS"
FROM CFBD_VS.GAMES
WHERE P_YEAR IN (2023, 2024)
GROUP BY REQ_SLICE, REQ_SOURCE, REQ_STATUS, REQ_ATTEMPTS, REQ_MS;
```

Child tables join to their parent on `REQ_SLICE` plus position columns: `ROW_NO`, and one `<ARRAY>_NO` per nesting level (1-based). Some child tables also carry their parent's natural keys as columns, so they can be used without the parent. `RANKINGS_POLLS_RANKS` carries `SEASON`, `SEASON_TYPE`, `WEEK` and `POLL`, so it never needs a positional join to `RANKINGS_POLLS`.

### Known limitation: a subquery on an optional `P_` column is never pushed down

Exasol pushes a comparison with a literal, an `IN` list, `BETWEEN`, `LIKE`, `IS NULL` and a comparison between two columns to the adapter. A subquery, a function or expression, and a join condition on a `P_` column are **not pushed**: the adapter never sees them. For a **required** parameter that is safe, because the rule check then rejects the query for the missing value. For an **optional** parameter it is not: the virtual schema fetches *without* that parameter, the echo column `P_<PARAMETER>` is NULL in every row, and Exasol's local filter on it then matches nothing. **The query returns 0 rows, silently, with no error.** The virtual schema can't detect this case.

```sql
-- WRONG: P_WEEK is optional for /games. The subquery is not pushed down, the request
-- is /games?seasonType=both&year=2024 (no week), P_WEEK is NULL, and 0 rows come back.
SELECT * FROM CFBD_VS.GAMES
WHERE P_YEAR = 2024 AND P_WEEK = (SELECT 8 FROM DUAL);
```

Workarounds:

```sql
-- 1. Use literals on the P_ columns.
SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2024 AND P_WEEK IN (8, 9);

-- 2. Or keep the P_ predicates literal and join on the response column instead
--    (WEEK, not P_WEEK) to a stored table. The query fetches all weeks of 2024,
--    which is one request, and the join keeps the weeks in your table.
SELECT G.*
FROM CFBD_VS.GAMES G
JOIN MY_WEEKS W ON G.WEEK = W.WEEK
WHERE G.P_YEAR = 2024;
```

Rule of thumb: values that must decide *which requests are made* belong in literals on `P_` columns. Anything computed (a subquery, a join, a function) can only filter the rows after the fact, and works on response columns.

## 5. Read-through and stored data

A virtual-table query is served partly from storage: a slice that is already stored in `CFB_CORE` costs zero API calls, and only the missing slices are fetched. Whether a slice is stored is decided when the query runs, per slice, and every slice is served from exactly one source. No row appears twice.

The rule: a slice is served from `CFB_CORE.<TABLE>` if and only if `CFB_META.LOAD_LOG` has a row for it with `IS_FINAL = TRUE`. Otherwise it is fetched. Stored rows of a slice that has no load-log row, or a non-final one, are never returned.

`CFB_META.LOAD_LOG` has one row per stored slice:

| Column | Meaning |
|---|---|
| `TABLE_NAME` | The virtual table, for example `GAMES` |
| `SLICE_KEY` | The canonical request, equal to `REQ_SLICE` of the stored rows |
| `IS_FINAL` | `TRUE` when the slice can never change (a finished season); `FALSE` for data that is still moving, so it is fetched again |
| `ROWS_LOADED`, `LOADED_AT`, `LOADED_BY` | Bookkeeping |

`CFB_CORE.<TABLE>` has the same columns, in the same order and with the same types, as `CFBD_VS.<TABLE>`, so a query result can be stored directly. As `sys`:

```sql
INSERT INTO CFB_CORE.GAMES
SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR = 2020;

INSERT INTO CFB_META.LOAD_LOG
SELECT 'GAMES', REQ_SLICE, TRUE, COUNT(*), CURRENT_TIMESTAMP, CURRENT_USER
FROM CFB_CORE.GAMES
WHERE P_YEAR = 2020
GROUP BY REQ_SLICE;

-- zero API calls now, REQ_SOURCE = 'STORED'
SELECT REQ_SOURCE, COUNT(*) FROM CFBD_VS.GAMES WHERE P_YEAR = 2020 GROUP BY REQ_SOURCE;
```

Doing this by hand is for experiments. The loader ([§9](#9-persisting-and-refreshing)) does it for the whole load plan, in batches, with the bookkeeping and the call budget; until it has run, `CFB_CORE` and `LOAD_LOG` are empty and every query fetches. Stored rows come back with `REQ_SOURCE = 'STORED'`, `REQ_STATUS` and `REQ_MS` NULL and `REQ_ATTEMPTS = 0`.

Notes:

- Read-through matches slices exactly. A stored season does not serve a query for one week of it. The slice key of your query must equal a stored slice key, and the key depends on the explicit defaults (`seasonType=both`).
- A stored final slice with zero rows is still a hit: it returns zero rows at zero calls.
- `/info`, `/info/usage`, `/games/schedule`, `/scoreboard` and `/live/plays` (and their child tables) change between calls, so they are always fetched and have no mirror.
- `ALTER VIRTUAL SCHEMA CFBD_VS SET READ_THROUGH = 'FALSE'` switches read-through off for all tables: every slice is fetched.
- A slice is final only when it can no longer change. Slices of the current season are never final, so **any query on them through `CFBD_VS` is a live call**. Pages and the chat read current-season data from `CFB_CORE`, never through `CFBD_VS` (see [§9.8](#98-reading-current-season-data)).

### Stored-only queries

Add `REQ_SOURCE = 'STORED'` to read only what is already stored. The plan then contains no fetch at all, so it never calls the API and the call budget does not apply. This is the way to read widely over stored history:

```sql
SELECT P_YEAR, COUNT(*) AS GAMES
FROM CFBD_VS.GAMES
WHERE P_YEAR BETWEEN 1869 AND 2026 AND REQ_SOURCE = 'STORED'
GROUP BY P_YEAR
ORDER BY P_YEAR;
```

The result holds only the slices stored and final in that range, not the ones that would have to be fetched. The 10,000-request ceiling still applies.

## 6. Replay mode

Replay mode serves recorded responses instead of calling CFBD: no network, no key and no quota. The tables look exactly like live ones; only `REQ_SOURCE` is `REPLAY` and the request timing columns hold the recorded values. This is how the demo runs offline.

```sql
ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE = 'replay';   -- next query, no refresh needed
ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE = 'live';
```

Recordings are read from the BucketFS directory in the property `REPLAY_PATH`. On Exasol Personal the default is `/buckets/bfsdefault/default/cfbd/replay`, which is the host directory `~/.exasol/personal/deployments/local/local/runtime/exa/bucketfs/bfsdefault/default/cfbd/replay/`. Files you put there appear in the UDF sandbox within about 0.3 seconds, and a new bucket within about a second.

- One file per slice: `<REPLAY_PATH>/<endpoint path segments>/<first 24 characters of sha256(slice key)>.cfbd.json`, holding `{"slice_key", "status", "ms", "requested_at", "body"}`. `body` is the raw response text, so every table of an endpoint (parent and children) replays from the same file.
- A missing recording fails the query with an error that names the slice key and the replay path. Nothing falls back to the network.
- Replay never reads the connection, so it works without `CFBD_API`.
- Recordings contain CFBD data. They live in BucketFS and the git-ignored `cache/` directory, never in git.

## 7. Virtual schema properties

Set them at creation (`make deploy ARGS="--property NAME=value"`) or later with `ALTER VIRTUAL SCHEMA CFBD_VS SET NAME = 'value';`. They take effect on the next query; none needs a `REFRESH`. `ALTER VIRTUAL SCHEMA CFBD_VS SET NAME = '';` (or NULL) removes a property, which restores its default. A wrong value or an unknown property name is rejected when you set it, with a message that names the property and its allowed values, and the previous values stay in place. Keyword values (`live`, `TRUE`, `off`) are case-insensitive.

| Property | Default | Allowed values | Effect |
|---|---|---|---|
| `CONNECTION_NAME` | `CFBD_API` | an upper-case identifier (A-Z, 0-9, `_`, starting with a letter, at most 128 characters) | The connection holding the API address and key |
| `FETCH_MODE` | `live` | `live`, `replay` | Call CFBD, or serve recordings |
| `REPLAY_PATH` | `/buckets/bfsdefault/default/cfbd/replay` | an absolute UDF-visible BucketFS path `/buckets/<service>/<bucket>[/<directory>...]`, segments of `A-Z a-z 0-9 . _ -`, no `.` or `..` segments, at most 1024 characters | Where `replay` mode reads recordings |
| `MAX_CALLS_PER_QUERY` | `10` | an integer from 1 to 1000 | The request budget per virtual-table access |
| `MAX_PARALLEL_FETCHES` | `6` | an integer from 1 to 16 | How many requests of one query run at once |
| `READ_THROUGH` | `TRUE` | `TRUE`, `FALSE` | Serve final stored slices from `CFB_CORE` |
| `API_FEATURES` | empty (no features) | a comma-separated subset of `adjustedMetrics`, `weather`, `scoreboard`, `livePlayByPlay`, `graphQl` (empty means none) | The paid CFBD features your key has; synced by `make deploy` |
| `DOH_URL` | `https://1.1.1.1/dns-query` | `off`, or an https URL whose host is an IP literal (at most 1024 characters) | DNS over HTTPS fallback for host names |
| `INFRA_SCHEMA` | `CFB_INFRA` | an upper-case identifier | Schema of the adapter and fetch function |
| `META_SCHEMA` | `CFB_META` | an upper-case identifier | Schema of `LOAD_LOG`, which the virtual schema reads (`FETCH_LOG` is written by the fetch-log helper, which takes its schema as an argument) |
| `CORE_SCHEMA` | `CFB_CORE` | an upper-case identifier | Schema of the stored mirror tables |

Leave the three schema properties alone unless you deploy under a different name; `make deploy` sets them consistently. `SELECT PROPERTY_NAME, PROPERTY_VALUE FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES WHERE SCHEMA_NAME = 'CFBD_VS'` lists the properties that currently have a value; the others use their default.

### Errors

Errors reach you as the text of the failed statement. Planning errors start with `CFBD_VS.<TABLE>`. A failure inside the fetch function arrives wrapped in Exasol's script error and traceback, with `CFBD_VS.<TABLE>: ...` inside the text. Besides the planning errors above, a fetch can fail with:

- `HTTP 401` or `HTTP 403`: "the key has no access to this endpoint". The message names the slice key (so the endpoint) and the status and tells you to check the key and that its tier or feature includes the endpoint. For a gated table, the planning error above is the one that names `API_FEATURES`. It is not retried.
- Other `HTTP 4xx`: not retried; the message names the slice key, the status and the API's message.
- `HTTP 429`, `5xx`, timeouts: retried (see above); after the fourth attempt the query fails and names the table, the slice key and the status.
- A host name that can't be resolved: names the host and `DOH_URL` and points at `make dns-check`.

No error text, result column or log ever contains the key.

## 8. The fetch log

Every row tells you what it cost (`REQ_*`), and the manifest of `EXPLAIN VIRTUAL` tells you what was planned. A UDF can't write a table, so nothing is logged on its own. Whoever runs a query on purpose (the loader, the app backend) persists it to `CFB_META.FETCH_LOG`, one row per slice the query actually read:

| Column | Meaning |
|---|---|
| `LOGGED_AT` | When the row was written (UTC) |
| `CONTEXT` | A free label of the caller, at most 200 characters |
| `TABLE_NAME`, `SLICE_KEY` | The virtual table and the request |
| `REQ_SOURCE` | `LIVE`, `REPLAY` or `STORED` (the same name as the virtual-table column) |
| `HTTP_STATUS`, `ATTEMPTS`, `MS` | Final status, attempts including retries and wall time of the request |
| `ROWS_RETURNED` | Rows the request produced |
| `REQUESTED_AT` | UTC start time of the request |

```sql
SELECT TABLE_NAME, SLICE_KEY, REQ_SOURCE, HTTP_STATUS, ATTEMPTS, MS, ROWS_RETURNED
FROM CFB_META.FETCH_LOG
ORDER BY LOGGED_AT DESC;
```

`vs/host/fetchlog.py` is the reference capture helper. `capture_fetch_log(connection, staged_table=..., pushdown_sql=..., meta_schema='CFB_META', context=...)` takes two things of the *same* query:

1. The query result staged into a table, from which it reads the distinct `REQ_*` metadata (staging means `CREATE TABLE ... AS SELECT ...` or `INSERT ... SELECT` from the virtual schema).
2. The manifest in the `PUSHDOWN_SQL` of `EXPLAIN VIRTUAL <the query>` (`explain_pushdown_sql` fetches it).

It writes one row per slice, in manifest order, from that slice's `REQ_*` values. The manifest is what makes this complete and honest:

- A planned slice whose response was empty has no rows, so it has no metadata; if the query fetched (`sources` holds `LIVE` or `REPLAY`), it is logged with `ROWS_RETURNED = 0` and that source.
- If the query did not fetch (a stored-only query, or nothing read at all), a planned slice without rows was never requested and gets no row. It is not logged as a fetch of zero rows.
- Staged rows of a slice that the manifest did not plan, or with a source the manifest did not list, are an error and nothing is written.

Logging is the caller's job on purpose: it must see the staged result, and only the caller knows its context.

## 9. Persisting and refreshing

The loader copies slices from `CFBD_VS` into the mirrors in `CFB_CORE` and registers them in `CFB_META.LOAD_LOG`, so read-through serves them at zero calls ([§5](#5-read-through-and-stored-data)). It has two entry points, both Lua scripting programs in `CFB_INFRA` that you run with plain SQL:

| Entry point | Does |
|---|---|
| `EXECUTE SCRIPT CFB_INFRA.BACKFILL(run_profile, from_season, max_calls)` | Fetches every slice of the load plan that is missing (and every slice that has become final since it was stored). Resumable, idempotent, newest first |
| `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)` | The weekly refresh: re-fetches the season's weekly slices that are not final yet, and returns a step timeline |

`make backfill`, `make refresh` and `make quota` wrap them with a call estimate, a confirmation and the call ledger. Run the loader as `sys`, which owns the scripts: a Lua script runs with the rights of its caller, so a user needs `EXECUTE` on the script and every privilege the script uses. A role for the app is planned for the app phase.

Every command takes `TARGET=local|aws` (default `local`), as in §1: `make backfill PROFILE=demo TARGET=aws`.

### 9.1 The load plan

`loader/load-plan.yaml` is the single reviewed file that decides what costs calls. Every planned slice, every call estimate and every finality decision comes from it. An entry has:

| Field | Meaning |
|---|---|
| `table` | The virtual table to store (it must have a mirror in `CFB_CORE`) |
| `profile` | `demo` (the v1 pages, designed for the free tier) or `full` (the optional archive; `full` includes `demo`) |
| `priority` | Lower numbers load first. Entries of equal priority interleave season by season, so give each weekly entry its own priority: one fan-out batch per entry is much cheaper than one-slice batches |
| `slicing` | `none` (one slice), `season` (one slice per season) or `season_range` (one slice for a year range, for endpoints that take `minYear` and `maxYear`) |
| `seasons` | `from` and, optionally, `to` (omitted means the current season). A `none` or `season_range` slice has no season, so `FROM` never skips it |
| `gaps` | Seasons the API has no data for. They are registered as final empty slices at zero calls |
| `params` | Fixed parameter values, for example `{classification: fbs}` |
| `refresh_class` | `static` (final when loaded) or `weekly` (see below) |

The `config` block holds the settings: `monthly_call_budget` (1000), `refresh_reserve` (150), `quarantine_after` (2), `season_rollover` (`08-01`: a new season's slices enter the plan on this day), `season_final_after` (`02-15`: season S is final when loaded on or after 15 February of S+1) and `refresh_profiles` (`[demo]`: which profiles the weekly refresh re-fetches).

Finality is a date rule, not a data rule: historic seasons contain cancelled games that never complete, so "all games completed" would never hold. A `static` slice is final on load. A `weekly` slice is final from `season_final_after`; until then it stays non-final and `REFRESH_WEEK` replaces it. A slice stored while it was not final is fetched once more by the next `BACKFILL` after it becomes final.

**The demo profile** (current season 2026):

| Entries | Calls |
|---|---|
| `TEAMS`, `VENUES`, `CONFERENCES` | 3 |
| `CONFERENCES_AFFILIATIONS` 1869-2025 as one range slice, plus 2026 | 2 |
| `CALENDAR` 2013-2026 | 14 |
| `GAMES` 1869-2026, without the 1871 gap | 157 |
| `METRICS_WP_PREGAME`, `RATINGS_SP` 2013-2026 | 28 |
| `RANKINGS_POLLS_RANKS` 2014-2026 | 13 |
| **Backfill, once** | **217** |
| **Weekly refresh:** the current season's slice of `GAMES`, `METRICS_WP_PREGAME`, `RATINGS_SP` and `RANKINGS_POLLS_RANKS` | **4** |

The count assumes that one `/games?seasonType=both&year=Y` call returns every division (verified live, S1). Rankings load from `RANKINGS_POLLS_RANKS` alone, which carries its own `SEASON`, `SEASON_TYPE`, `WEEK` and `POLL` keys, so no positional join to `RANKINGS_POLLS` is needed.

**Editing the plan.** Edit the file, check it, and deploy:

```bash
uv run python -m loader.plan --check --estimate   # validates against the catalog, prints slices and calls (0 calls)
make deploy                                       # replaces the plan tables, views and scripts
```

The check lists every problem at once, each naming the entry and the rule, and the deploy changes nothing when the plan is invalid. Entry order is the entry's id, so redeploy after reordering. Editing the plan never refetches anything: a slice whose key still matches stays stored. A plan edit that changes a slice key (a new fixed parameter, say) orphans the old slices (see [§9.6](#96-failures-quarantine-and-orphans)).

### 9.2 Deploy and settings

`make deploy` deploys the virtual schema (§1) and then the loader: the `CFB_STAGE` schema (one empty mirror per planned table, used while a batch loads), the plan tables, the six views and the scripts `BACKFILL`, `REFRESH_WEEK` and `RUN_LOCK`. It never touches the data in `LOAD_LOG`, `FETCH_LOG`, the run logs or `CFB_CORE`. It prints a warning when stored slices no longer match the plan. Once a namespace has `RUN_LOCK` (after its first deploy), the deploy holds the run lock as a `DEPLOY` run, so it is refused while a backfill, refresh, snapshot or restore runs, and nothing changes.

The loader settings come from three places, each winning over the one before, setting by setting:

1. `loader/load-plan.yaml` (`config`), the same for every deployment;
2. `vs/live/loader-config.yaml`, per target: a plain `make deploy TARGET=aws` applies its `aws` entry, so a redeploy never lifts the AWS budget. There, `monthly_call_budget: ledger` means the owner's cap in `vs/live/ledger.yaml` for the UTC month of the deploy (400 in October 2026, 1000 from the first deploy in November), and `refresh_reserve` is 50. A month without a cap refuses the deploy until the owner adds one, or until you pass the budget yourself;
3. `LOADER_CONFIG`, for one deploy:

```bash
make deploy LOADER_CONFIG="monthly_call_budget=400,refresh_reserve=50"
```

The deploy prints every override with its source, and warns when `MONTHLY_CALL_BUDGET` or `REFRESH_RESERVE` changes. Overrides change the settings but not the plan's hash, and they last until the next deploy: after a month change, deploy once to follow the new month's cap.

### 9.3 Backfill

```bash
make backfill PROFILE=demo                      # prints the estimate and the quota, then refuses: no CONFIRM
make backfill PROFILE=demo MAX_CALLS=20 CONFIRM=yes
make backfill PROFILE=demo CONFIRM=yes          # resumes where the last run stopped
make backfill PROFILE=demo FROM=2018 CONFIRM=yes   # seasons up to 2018 (seasonless slices always run)
make backfill PROFILE=demo MAX_CALLS=0 CONFIRM=yes # dry run
```

| Setting | Meaning |
|---|---|
| `PROFILE` | `demo` (default) or `full` |
| `FROM` | The newest season to load. Seasonless slices are always included |
| `MAX_CALLS` | The most calls this run may spend, measured (retries included). Without it, the ledger room caps the run. A run can spend up to 3 calls more: a request may take 4 attempts, so near the end the backfill fetches one slice at a time, and a last slice started with 1 to 3 calls left can be retried to its fourth attempt. `0` is a dry run: it registers known gaps, checks every pending batch with `EXPLAIN VIRTUAL` and fetches nothing |
| `CONFIRM=yes` | Required to run in live mode. Without it the command prints the estimate and the quota and stops (exit 1) |
| `LEDGER=off` | Turns the call ledger off (see [§9.5](#95-quota-budget-and-the-ledger)) |

What the wrapper does, in order, each guard stopping the command before the next one: validate the arguments; connect; check that the script is installed (otherwise `make deploy`); print the estimate from the deployed views (calls per table, and tables that are blocked); in live mode read `CFB_META.QUOTA` (free); require `CONFIRM=yes`; check the ledger. In replay mode (`FETCH_MODE = 'replay'`) there are no calls, so there is no quota read, no confirmation and no ledger.

Running the script directly, from any SQL client:

```sql
EXECUTE SCRIPT CFB_INFRA.BACKFILL('demo', NULL, NULL);   -- run_profile, from_season, max_calls (NULL = no limit)
```

It returns one row per table that was loaded, checked, blocked or had failures, then a total row with `TABLE_NAME = '*'`. Never run the call `WITH OUTPUT`: that returns only the script's log lines and discards the result table. All three arguments are required.

| Column | Meaning |
|---|---|
| `RUN_ID` | The run's row in `CFB_META.LOAD_RUN` |
| `TABLE_NAME` | The table, or `*` for the run totals |
| `SLICES`, `ROWS_LOADED` | Slices and rows loaded (known gaps excluded) |
| `CALLS` | Calls spent, measured as the change of `/info` used calls |
| `FAILED_SLICES` | Slices that failed even when fetched alone |
| `RUN_STATUS` | `DONE`, `DONE_WITH_FAILURES`, `STOPPED_BUDGET`, `STOPPED_MAX_CALLS` or `FAILED` |
| `CALLS_NEEDED` | Pending slices still to fetch (gaps and quarantined slices excluded) |
| `DETAIL` | Gaps registered, blocked tables, the first failures |

Illustrative result of a first run with `MAX_CALLS=20` (row counts elided):

```
RUN_ID  TABLE_NAME                SLICES  ROWS_LOADED  CALLS  FAILED_SLICES  RUN_STATUS        CALLS_NEEDED
7       TEAMS                     1       ...          1      0              STOPPED_MAX_CALLS 0
7       VENUES                    1       ...          1      0              STOPPED_MAX_CALLS 0
7       CONFERENCES               1       ...          1      0              STOPPED_MAX_CALLS 0
7       CONFERENCES_AFFILIATIONS  2       ...          2      0              STOPPED_MAX_CALLS 0
7       CALENDAR                  14      ...          14     0              STOPPED_MAX_CALLS 0
7       GAMES                     1       ...          1      0              STOPPED_MAX_CALLS 156
7       *                         20      ...          20     0              STOPPED_MAX_CALLS 197
```

How it works:

- **Order:** profile (`demo` before `full`), priority, newest season first (seasonless slices first), table, entry. A slice that failed before comes last and is fetched alone.
- **Batches:** one plan entry at a time, at most `MAX_CALLS_PER_QUERY` slices, and in live mode at most a quarter of the remaining allowance (every request may take 4 attempts; at least one slice), fetched by one fan-out query. The rows go through `CFB_STAGE` into `CFB_CORE` in one transaction together with the `LOAD_LOG` and `FETCH_LOG` rows. A failed batch leaves no trace and earlier batches stay committed, which is what makes a run resumable.
- **Idempotent:** a rerun fetches only missing slices and slices that have become final since. A complete profile costs 0 calls to run again.
- **Budget:** it stops before the quota allows no more (`STOPPED_BUDGET`), at `MAX_CALLS` (`STOPPED_MAX_CALLS`), or when `QUOTA.BACKFILL_SPENDABLE` runs out. The allowance follows the key's real counter after every batch, so retries are counted too.
- **Read-through stays on.** The loader never switches `READ_THROUGH` (it is global, and concurrent queries would re-fetch stored slices); its batch queries keep only fetched rows (`REQ_SOURCE <> 'STORED'`).
- **Drift check:** before each batch, `EXPLAIN VIRTUAL` (0 calls) confirms that the adapter would request exactly the batch's slice keys. A mismatch stops the run before any request and names `make deploy`.

Exit codes of the wrapper: 0 for `DONE`, `STOPPED_BUDGET` and `STOPPED_MAX_CALLS`; 1 when a guard refused, also when the script found another run holding the run lock (nothing was sent); 2 for an error (arguments, connection, database); 3 when the run itself failed (`FAILED`, `DONE_WITH_FAILURES`, a raised error).

**Ctrl-C and timeouts.** The wrapper notes the session before it starts the script. If you interrupt it, or the connection times out, it opens a new connection, kills the run's session if the script is still running (so it stops spending), reads the run's `LOAD_RUN` row and `/info`, writes the ledger entry, and only then exits. When nothing can be read, it charges the most the run could have spent: `MAX_CALLS` for a backfill, four attempts per estimated slice for a refresh.

### 9.4 The weekly refresh

The demo's one call:

```sql
EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular');   -- season, week, season_type
```

Exasol has no `CALL` statement, so the refresh is an `EXECUTE SCRIPT`. All three arguments are required (`'regular'` or `'postseason'`; NULL means `'regular'`). Both season types number their weeks from 1 in the calendar, so `(2026, 1, 'postseason')` is the first bowl week and differs from `(2026, 1, 'regular')`. Never run it `WITH OUTPUT`.

From the shell: `make refresh SEASON=2026 WEEK=9 [SEASON_TYPE=postseason] [CONFIRM=yes] [TARGET=local|aws]` runs the same guards as `make backfill` (estimate, quota, confirmation, ledger), then prints the timeline.

The result is the step timeline, one row per step:

| Column | Meaning |
|---|---|
| `RUN_ID` | The run's row in `CFB_META.LOAD_RUN` |
| `STEP_NO` | 1 `CHECK`, 2 to 99 `FETCH <table>`, 100 and up registered steps |
| `STEP_NAME`, `STEP_STATUS` | The step and `DONE` / `RUNNING` / `FAILED` |
| `SECONDS` | Time between the step's start and end (for a `FETCH`, the SQL-to-HTTP time of that table) |
| `SLICES`, `ROWS_LOADED`, `CALLS` | What the step loaded and spent |
| `DETAIL` | Slices fetched, or the error |

Illustrative timeline with the demo plan (numbers elided):

```
RUN_ID  STEP_NO  STEP_NAME                       STEP_STATUS  SECONDS  SLICES  ROWS_LOADED  CALLS
12      1        CHECK                           DONE         ...      0       0            0
12      2        FETCH GAMES                     DONE         ...      1       ...          1
12      3        FETCH METRICS_WP_PREGAME        DONE         ...      1       ...          1
12      4        FETCH RATINGS_SP                DONE         ...      1       ...          1
12      5        FETCH RANKINGS_POLLS_RANKS      DONE         ...      1       ...          1
```

What the steps do:

- **`CHECK`** validates the arguments, checks that the week with its season type exists in the stored `CALENDAR` of the season, lists the due slices (the season's slices of `weekly` entries in `refresh_profiles` that are missing or not final), refuses a table that has orphan slices, and, in live mode, requires the due calls to fit `QUOTA.REFRESH_SPENDABLE`. It fails before any request when they do not. A refresh may use the reserve that backfills keep.
- **`FETCH <table>`** re-fetches the season's slice of that table and replaces its rows (delete and insert) together with the `LOAD_LOG` row in one transaction. A weekly refresh re-fetches the whole season slice, not a week slice: read-through matches slice keys exactly, so a week slice next to a season slice would store every game twice, and a season call costs the same one call. It also picks up corrections to earlier weeks. A failed fetch keeps that table's previous rows. Each table commits on its own, so tables fetched before a failure stay refreshed, and readers may briefly see a mix; a failing step stops the chain.
- Every step writes its `CFB_META.REFRESH_LOG` row when it starts and again when it ends, and commits both, so another session (the app's timeline) sees progress while the script runs.
- **Registered steps** (100 and up) are the hook of the later phases (model, predictions, simulations). They come from `refresh_steps` in the load plan (`{step_no: 100, name: MODEL, script: CFB_INFRA.REFRESH_MODEL}`) and are called as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)`. The contract:
  - exactly four parameters, `(run_id, season, week, season_type)` with the season type normalized to `'regular'` or `'postseason'`;
  - the step must **not** take the run lock: the refresh already holds it, and taking it again would abandon the refresh's own run;
  - it runs in the refresh's transaction: the refresh commits before it and commits its work together with its `DONE` row; a failing step is rolled back before its `FAILED` row is written;
  - it should read `CFB_CORE`, never `CFBD_VS`.

  The loader registers no steps itself.
- **Calendar gap.** The `CALENDAR` of the current season is `static`, so it is final once loaded and may lack a bowl week that CFBD publishes later. A week missing from the stored calendar fails `CHECK` with that explanation (see [§9.7](#97-re-fetching-a-slice) for the remedy).

### 9.5 Quota, budget and the ledger

`make quota` (or `SELECT * FROM "CFB_META"."QUOTA"`) prints the key's quota and what the loader may spend; the command also prints the ledger room. It reads `/info`, which costs no call, and has no row if `/info` cannot be read; the scripts then refuse to run live.

| Column | Meaning |
|---|---|
| `TIER_NAME`, `MONTHLY_LIMIT`, `USED_CALLS`, `REMAINING_CALLS` | From `/info`. `USED_CALLS` counts **every** call of the key this month, including other tools and the shared CBBD pool |
| `RESET_AT`, `PERIOD_START` | The end and start of the quota month, in UTC |
| `MONTHLY_CALL_BUDGET`, `REFRESH_RESERVE` | The project's settings from the load plan (or `LOADER_CONFIG`) |
| `PROJECT_BUDGET` | `LEAST(MONTHLY_LIMIT, MONTHLY_CALL_BUDGET)`: a cap on the key's whole monthly use |
| `REFRESH_SPENDABLE` | `GREATEST(0, LEAST(REMAINING_CALLS, PROJECT_BUDGET - USED_CALLS))` |
| `BACKFILL_SPENDABLE` | `GREATEST(0, REFRESH_SPENDABLE - REFRESH_RESERVE)` |
| `BACKFILL_CALLS`, `REFRESH_CALLS`, `OTHER_CALLS` | Calls this month by run kind (from `FETCH_LOG`, counting retries); the rest is `OTHER_CALLS` (on-demand queries, other clients, failed batches) |
| `DEMO_CALLS_TO_COMPLETE`, `FULL_CALLS_TO_COMPLETE` | Pending slices of the demo profile, and of demo plus full |

The reserve exists so a refresh can still run when the backfill has used up its share: a backfill stops at `BACKFILL_SPENDABLE`, a refresh may spend down to `REFRESH_SPENDABLE`. The demo is designed for the free tier (1,000 calls a month, even on a larger key), which is why `monthly_call_budget` defaults to 1000.

Two guards keep live runs inside a budget:

1. **In the database:** `PROJECT_BUDGET` against `/info`'s used calls. It holds for every SQL client, including a `BACKFILL` typed outside `make`.
2. **The ledger** (`cache/live-calls.jsonl`, gitignored, shared with the recorder and every other plan through the main working tree). `make backfill` and `make refresh` check it before a live run and append the run to it afterwards. The caps live in `vs/live/ledger.yaml`, which the owner edits: `plans` (calls a plan may spend over all months) and `months` (calls the whole key may spend in a UTC month, across all plans). A month without an entry refuses live calls. The wrappers refuse when the plan's total plus the estimate would exceed its cap, or when `/info`'s used calls plus the estimate would exceed the month's cap. `MAX_CALLS` defaults to the ledger room and is capped at it. A ledger entry is written for every live run, with the larger of the script's measured calls and the `/info` change.

`LEDGER=off` turns the ledger off for people who run their own key (nothing is read, checked or written); the in-database budget still applies. The ledger is not used when the virtual schema reads the mock connection `CFBD_API_MOCK` (its calls cost nothing); the connection decides, not the namespace prefix. `python -m vs.host.ledger --summary` prints the totals against the caps.

### 9.6 Failures, quarantine and orphans

**Run logs.** `CFB_META.LOAD_RUN` has one row per run (`BACKFILL`, `REFRESH_WEEK`, `SNAPSHOT`, `RESTORE`, and later the `DEPLOY` kind that the model deploy and simulation runs take): kind, arguments, session, times in UTC, status, slices, rows, calls and the key's remaining calls before and after. `CFB_META.REFRESH_LOG` has one row per step. `LOAD_LOG.LOADED_BY` and `FETCH_LOG.CONTEXT` hold `<KIND>#<run id>`.

```sql
SELECT "RUN_ID", "RUN_KIND", "RUN_STATUS", "STARTED_AT", "CALLS", "DETAIL"
FROM "CFB_META"."LOAD_RUN" ORDER BY "RUN_ID" DESC LIMIT 10;

SELECT * FROM "CFB_META"."LOAD_STATUS" ORDER BY "ENTRY_ID";   -- per plan entry: planned, stored, final, missing, quarantined, orphans, calls to complete, BLOCKED_BY
```

**One run at a time.** A `RUNNING` row in `LOAD_RUN` is the run lock; a second loader run, a snapshot, a restore or a deploy refuses to start while another holds it, and the refusal names the holder. Only the session that took the lock ends its row. A lock never blocks forever: a `RUNNING` row is treated as abandoned, and the next run proceeds, when its session is gone, when it belongs to the calling session, or (for `BACKFILL` and `REFRESH_WEEK`, which are one statement) when its session is idle, meaning the script ended without closing its row after a failure or `KILL STATEMENT`. Snapshot and restore run from host sessions that are often idle between statements, so for them only a vanished session counts.

**Failing slices.** The fetch function fails a whole fan-out when one slice fails. `BACKFILL` therefore rolls back a failed multi-slice batch and fetches each slice alone: the good ones commit, and a slice that fails alone gets a row in `CFB_META.LOAD_FAILURES` (one failure per run). From then on it is fetched alone and last. After `quarantine_after` (2) failing runs it is quarantined: skipped, and shown in `LOAD_STATUS`, until you clear it. A batch fails when none of its slices could be stored, also alone. One failed batch is recorded and the run goes on, so one transient error (on `TEAMS`, say) costs only that slice. After two failed batches in a row, the cause is not the slice and the run stops with `FAILED` instead of burning calls. `REFRESH_WEEK` has no fallback: a failure stops the refresh and is shown in the timeline.

```sql
SELECT "TABLE_NAME", "SLICE_KEY", "FAILURES", "LAST_ERROR" FROM "CFB_META"."LOAD_FAILURES";

-- after fixing the cause (a plan or catalog error, a tier problem, an outage)
DELETE FROM "CFB_META"."LOAD_FAILURES"
WHERE "TABLE_NAME" = 'GAMES' AND "SLICE_KEY" = '/games?seasonType=both&year=2019';
```

A slice that later succeeds clears its own row.

**Orphans.** An orphan slice is a `LOAD_LOG` row the loader wrote (`BACKFILL#...`, `REFRESH_WEEK#...` or a registered gap) for a planned table whose key matches no planned slice. It appears after a catalog change (an explicit default changes every key of a table), a plan edit or a restore from another catalog. `LOAD_STATUS.ORPHAN_SLICES` counts them, `SELECT * FROM "CFB_META"."LOAD_ORPHANS"` lists them, `make deploy` warns, and **`BACKFILL` and `REFRESH_WEEK` refuse to fetch that table** (`BLOCKED_BY = 'ORPHANS'`), because refetching would pay twice for data already stored under another key. Resolve them either by migrating the stored rows to the new keys (there is no migration tool yet), or by deleting the orphan `LOAD_LOG` rows and their `CFB_CORE` rows so the table is fetched again. A table whose API feature is missing from `API_FEATURES` (a paid tier) is blocked by that feature instead, and never reports orphans.

### 9.7 Re-fetching a slice

To fetch a stored slice again, make it non-final and run the loader. Example: the current-season calendar lacks a bowl week that CFBD published later, and `REFRESH_WEEK` fails its `CHECK`:

```sql
UPDATE "CFB_META"."LOAD_LOG" SET "IS_FINAL" = FALSE
WHERE "TABLE_NAME" = 'CALENDAR' AND "SLICE_KEY" = '/calendar?year=2026';
```

Then `make backfill PROFILE=demo CONFIRM=yes`. The loader fetches the slice once more (one call). Read the exact slice key with `SELECT "SLICE_KEY" FROM "CFB_META"."LOAD_LOG" WHERE "TABLE_NAME" = 'CALENDAR'`. For a `static` slice that is final the backfill does not touch it unless you do this.

### 9.8 Reading current-season data

Slices of the current season are never final, so a `CFBD_VS` query on them always fetches live and costs calls. Pages, the chat and analyses read current-season data from `CFB_CORE` (later through `CFB_MODEL` and `CFB_API`), never through `CFBD_VS`. Only the loader reads the current season through the virtual schema.

### 9.9 Snapshots and restore

A snapshot is a private copy of the stored data. It lets you rebuild a database, move data from AWS to your laptop, or reset a rehearsal, with **zero CFBD calls**: neither command reads the virtual schema.

```bash
make snapshot NAME=sunday-week9 [TARGET=aws]                 # copy the stored data to a private directory
make snapshots                                               # list them
make restore NAME=sunday-week9 [TARGET=local]                # checks the snapshot and stops: no CONFIRM
make restore NAME=sunday-week9 CONFIRM=yes [FORCE=yes] [TARGET=local]
```

- **Where:** `~/.cfb-demo/snapshots/<NAME>/` (`CFB_SNAPSHOT_DIR` overrides the root). Directories are mode 0700 and files 0600. A location inside the repository or a OneDrive folder is refused, and an existing name is never overwritten. CFBD's terms forbid redistributing the raw data, so snapshots stay on your machine: never commit, share or upload them.
- **Scope:** every `CFB_CORE` table plus `CFB_META.LOAD_LOG` and `CFB_META.FETCH_LOG`. The run logs (`LOAD_RUN`, `REFRESH_LOG`, `LOAD_FAILURES`) stay with each deployment, so restored `LOADED_BY` and `CONTEXT` values refer to the source deployment's runs.
- **Format:** one gzip CSV file per non-empty table (`CFB_CORE.GAMES.csv.gz`, with a header line), plus `manifest.json` written last: the name, time, source target, database version, catalog hash, plan hash, and per table the columns, rows, SHA-256 and a per-column fingerprint. No credential is in it. A directory without a manifest is an incomplete snapshot. (An earlier plan used Parquet through exapump; the probe found it unusable for these tables: it fails on commas, quotes and newlines in VARCHARs, trims spaces and writes untyped columns.)
- **Snapshot:** takes the run lock, fingerprints every table before and after the export, and keeps nothing if the data changed in between.
- **Restore** checks everything before it changes anything: the manifest, that every table exists with the manifest's columns in the same order (a column difference is always refused, also with `FORCE`), that the set of `CFB_CORE` tables matches, the files' checksums and headers, and the catalog hash. **A snapshot of another catalog is refused unless `FORCE=yes`**: slice keys may differ, so restored slices could become orphans. The catalog hash covers the generated catalog, so any regeneration changes it. After a forced restore, `LOAD_ORPHANS` shows what no longer matches. Then it takes the run lock, loads each file into a landing schema `CFB_RESTORE`, checks every fingerprint against the manifest, replaces all scope tables in one transaction, and drops the landing schema. A failed restore changes nothing. It records a `RESTORE` row in `LOAD_RUN`.
- **Rehearsal reset:** restoring the "Sunday after week N" snapshot returns the database to that state at zero calls.
- **The DOUBLE exception.** The copy goes through each value's text form, and Exasol writes a `DOUBLE` with 15 significant digits. A `DOUBLE` with more digits comes back rounded to its text form (a relative change of at most 4e-12). Every other type round-trips exactly, including 2,000,000-character values and the 0001 and 9999 dates. The fingerprint of a `DOUBLE` column is computed on the text form on both sides, so a restore that matches its fingerprints is exact in that sense, and it would refuse rather than pass silently if another database version parsed the text differently.
- `sys` runs both commands: the file transfer is `EXPORT` and `IMPORT`, which need those system privileges.

### 9.10 AWS and other keys

`TARGET=aws` runs every loader command against the AWS `default` deployment, with the node's TLS certificate pinned: the sys password is read from the deployment's secrets at run time and is never sent to a non-loopback host unverified (`vs/host/target.py`). On AWS, only deploy and run: never stop, start or resize the deployment.

People who run their own key and do not want the repository's ledger use `LEDGER=off`. They should still set `monthly_call_budget` to what their key allows: in the plan file, or with `make deploy LOADER_CONFIG="monthly_call_budget=<n>"`.

## 10. The analytical model

`CFB_MODEL` is what analyses, the engine and the app read; they never touch `CFB_CORE` or `CFBD_VS`. It reads only stored data, so building and querying it costs **zero CFBD calls**. `docs/model-dictionary.md` (generated, `make model-docs`) lists every object and column with its grain; every object also carries that comment in the database.

```bash
make model [TARGET=aws]                          # lint, then deploy the model in one transaction (also part of make deploy)
make model-refresh [SEASON=2026 WEEK=7]          # rebuild the derived tables; with a season and week, also log that point's checks
make model-check SEASON=2026 WEEK=7              # print the checks of one point, change nothing
make model-docs                                  # regenerate docs/model-dictionary.md from sql/model/
```

### What is in it

- **Reference tables** (seeds in `sql/model/01x`): eras, division labels, conference tiers, conference championship seasons, polls, the dataset catalog and classification transitions. Curated knowledge CFBD does not provide.
- **Derived tables** (`GAME`, `SEASON_WEEK`, `TEAM`, `RATING_SP`, `RANKING`, `TEAM_MEMBERSHIP`, `TEAM_SEASON`, `AS_OF_POINT`): the dimensions and facts that pages and the as-of views read most. They are tables filled by one `INSERT ... SELECT`, because the same queries as views took 1 to 6 s on AWS, mostly compile time (decision [40]).
- **Views** for everything else: `TEAM_GAME`, `SEASON`, `VENUE`, `CONFERENCE`, `CONFERENCE_CHAMPIONSHIP_GAME`, `SEASON_COVERAGE` (trust level per season and dataset, and which datasets are on demand), `MODEL_CHECKS`, and the four as-of views below.
- **Venues** carry `GEO_POINT` as `GEOMETRY(4326)` (Exasol has no `GEOGRAPHY`); `GREAT_CIRCLE_KM(lat1, lon1, lat2, lon2)` gives kilometres. Engine-facing real values (spreads, win probabilities, SP+ ratings) are fixed-scale `DECIMAL`.

### When the derived tables rebuild

They are **current as of the last rebuild**. Rebuilds happen in `make model` and `make deploy`; as step 100 `MODEL` of every `REFRESH_WEEK` (it rebuilds, then logs the checks to `CFB_META.MODEL_CHECK_LOG`, and an ERROR check stops the chain); after `make backfill` and `make restore` (a post-hook, a failure is a printed warning); and by hand with `make model-refresh`. Not covered: `EXECUTE SCRIPT CFB_INFRA.BACKFILL(...)` run directly and manual edits of `CFB_CORE`; run `make model-refresh` after them. A target gets step 100 with its next `make deploy`.

### As-of points

`GAME_AS_OF`, `RANKING_AS_OF`, `TEAM_SEASON_AS_OF` and `CURRENT_AS_OF` answer "what was known at season S, week W". The point is `(AS_OF_SEASON, AS_OF_WEEK_SEQ)`, where `WEEK_SEQ` is 0 for the preseason, n for regular week n and 100 + n for postseason week n, the same week `REFRESH_WEEK` receives. One horizon everywhere: everything known after that week's games and before the next week's first kickoff (`CUTOFF_AT`).

- Results, Elo and polls are visible once they happen; values CFBD overwrites during a season (current SP+, pregame lines, the current polls) are visible from their fetch time. There is no per-week snapshot history, so a replay of an earlier week of the current season shows them as NULL.
- Games whose teams depend on results (conference championships, bowls, CFP rounds) are not rows until their teams are known (`GAME.PARTICIPANTS_RULE`).
- **Filter by a literal point.** Write `WHERE AS_OF_SEASON = 2024 AND AS_OF_WEEK_SEQ = 9`, or read `CURRENT_AS_OF` first and pass its values. A join to `CURRENT_AS_OF` is not pushed into the views and evaluates every point of every season (21 s instead of under 1 s).

```sql
SELECT * FROM CFB_MODEL.CURRENT_AS_OF;
SELECT SCHOOL, WINS, LOSSES, CONF_WINS, ELO_AS_OF, AP_RANK
  FROM CFB_MODEL.TEAM_SEASON_AS_OF
 WHERE AS_OF_SEASON = 2024 AND AS_OF_WEEK_SEQ = 9 AND CLASSIFICATION = 'fbs'
 ORDER BY AP_RANK NULLS LAST LIMIT 10;
```

- **Known late visibility:** CFBD labels the committee's selection ranking of 2024 (released 8 December, after the title games of week 15) as regular week 16, and the label rule makes it visible from point 16, one week after the selection; at point 15 `RANKING_AS_OF` still shows the week-15 ranking (the 2025 ranking behaves the same, and so does every committee ranking of a past season: its label lead is 0). A ranking fetched before the cutoff is visible at once, so the live demo is not affected. Phase 4 is not affected because it reads final rankings as labels only.
- Real data shows CFBD labels the 2025 lower-division playoff as postseason weeks 13 and 14, so `WEEK_SEQ` is not chronological there; the `WEEK_ORDER_MISMATCH` warning lists it. See `docs/model-verification.md` for the checks and warnings seen on the stored data.

## 11. Serving API

`CFB_API` contains 33 read-only views for the app and chat. They serve teams, games, the weekly schedule, league statistics, head-to-head history, odds, what-if results, predictions, scorecards, upsets and the refresh timeline from stored `CFB_MODEL` and `CFB_SIM` data. They make zero CFBD calls. `CFB_API_LIVE.QUOTA` is separate: selecting it reads the free CFBD `/info` endpoint, while deploying it makes no request. The view and column descriptions are in `docs/api-dictionary.md`, generated from the SQL comments.

```bash
make api TARGET=local          # lint and deploy serving views; also part of make deploy
make grants TARGET=local       # grant CFB_APP, CFB_MCP and CFB_READER access
make api-docs                  # regenerate docs/api-dictionary.md without a database
```

Use `TARGET=aws` for the AWS deployment. `make api` creates views under `CFB_API` and `CFB_API_LIVE` without replacing stored data. `make grants` gives `CFB_APP` SELECT on both schemas, and `CFB_MCP` SELECT only on `CFB_API`. The chat cannot query the live quota view or the virtual schema through its role. An administrator can create app and chat users in an interactive SQL client, typing each password there rather than placing it in a command line or repository file:

```sql
CREATE USER CFB_APP_WEB IDENTIFIED BY "<type an app password here>";
GRANT CREATE SESSION TO CFB_APP_WEB;
GRANT CFB_APP TO CFB_APP_WEB;

CREATE USER CFB_CHAT IDENTIFIED BY "<type a chat password here>";
GRANT CREATE SESSION TO CFB_CHAT;
GRANT CFB_MCP TO CFB_CHAT;
```

The app's `make app-user` command manages its users from `~/.cfb-demo/app.env` when that app is installed; it never prints the passwords. Run `make grants` after a fresh deployment before connecting either user.

Read `CFB_API.CURRENT_POINT` first, then put its season and week sequence into point-grain queries as literals. A literal keeps the database from evaluating every point. For example:

```sql
SELECT "AS_OF_SEASON", "AS_OF_WEEK_SEQ" FROM "CFB_API"."CURRENT_POINT";
SELECT "GAME_ID", "HOME_TEAM", "AWAY_TEAM"
  FROM "CFB_API"."WEEK_GAMES"
 WHERE "AS_OF_SEASON" = 2026 AND "AS_OF_WEEK_SEQ" = 7;
```

For a published simulation, `PLAYOFF_ODDS` shows each team's playoff chance and movement, and `PUBLISHED_RUNS` identifies the run at each point. `WHATIF_RUN` identifies the current run used by `WHATIF_GAMES`, `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF` and `WHATIF_TEAMS`. The app's adapter in `analyses/whatif.py` converts up to 16 selected `WHATIF_GAMES.GAME_ID` values to the run's outcome positions and returns conditional odds. `ROOTING_GUIDE` shows which side of each remaining game helps a team most and the difference in its playoff chances. A game with too few simulated seasons in either branch has `LOW_SAMPLE = TRUE`.

`PREDICTIONS` contains frozen model forecasts and later grades with the home, away and pick schools. `SCORECARD_WEEKS`, `SCORECARD_SEASONS` and `SCORECARD_CALIBRATION` split weekly record, season-to-date record and probability buckets. `WEEK_GAMES` joins each point's next-week schedule to its frozen predictions and maximum playoff stakes. `MATCHUP_NEXT` shows a frozen prediction when one exists, otherwise the current run's win probability, with conditional playoff stakes for both teams when the guide has rows. `UPSETS` combines stored lines with fitted Elo probabilities, ranks all-time from 1978, and marks ties. Its frozen `MODEL_WINNER_WIN_PROB` is available only for a game with a prediction.

`GAME_PREVIEW` is not a serving view. Exasol did not push the required `P_GAME_ID` filter through a view in the local mock test, so the app's guarded operator path sends a direct virtual-table query with a validated literal game ID. Its quota and ledger checks run before a paid preview. See `docs/api-verification.md` for deployments, query timings and the top-upset plausibility check.
