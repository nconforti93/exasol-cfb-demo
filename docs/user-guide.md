# User Guide

> Work in progress: sections are added as features land. This version covers the CFBD virtual schema: deploying it, storing the key, querying it, and its properties. The loader, the app and the chat are not part of it yet.

## Prerequisites
- Exasol Personal, local deployment (laptop-class machine is enough), or any Exasol 2026.x database.
- A free CollegeFootballData API key: https://collegefootballdata.com/key. The demo is designed to run within the free tier (1,000 calls/month).
- The repository checked out, with [uv](https://docs.astral.sh/uv/) installed. Every `make` target runs through `uv run`, except `make db-check`, which runs `python3 scripts/dbcheck.py` and needs only a Python 3 interpreter.

## 1. Deploy and grant readers

`sys` owns the real deployment: the schemas `CFB_INFRA`, `CFB_META` and `CFB_CORE`, the adapter, the fetch function and the virtual schema `CFBD_VS`. `make deploy` and `make grants` log in as `sys`, with the password read at run time from the Exasol Personal secrets file. It is never printed or written. Both targets first check that the database is reachable (start it with `exasol start -d local`).

```bash
make deploy    # build the bundle, create everything, create CFBD_VS
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

Doing this by hand is for experiments. The loader that backfills and refreshes slices is a later phase; until then `CFB_CORE` and `LOAD_LOG` are empty and every query fetches. Stored rows come back with `REQ_SOURCE = 'STORED'`, `REQ_STATUS` and `REQ_MS` NULL and `REQ_ATTEMPTS = 0`.

Notes:

- Read-through matches slices exactly. A stored season does not serve a query for one week of it. The slice key of your query must equal a stored slice key, and the key depends on the explicit defaults (`seasonType=both`).
- A stored final slice with zero rows is still a hit: it returns zero rows at zero calls.
- `/info`, `/info/usage`, `/games/schedule`, `/scoreboard` and `/live/plays` (and their child tables) change between calls, so they are always fetched and have no mirror.
- `ALTER VIRTUAL SCHEMA CFBD_VS SET READ_THROUGH = 'FALSE'` switches read-through off for all tables: every slice is fetched.

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
