# Architecture

## Overview

```
  vs/openapi/cfbd-openapi-<ver>.json ──(generator)──▶ endpoint catalog (tables, columns, params)
                                                              │
  CFBD API v2 ◀── HTTPS, Bearer key from CONNECTION CFBD_API ── CFBD_FETCH (Python SET UDF, the only key reader for readers)
                                                              ▲        └──▶ REQ_* columns on every row (slice, source, status, attempts, ms, rows, at)
  SELECT … FROM CFBD_VS.<ENDPOINT> WHERE … ──▶ CFBD_ADAPTER (Python adapter script)
                                                 (predicates → P_ params, required filters, range/IN fan-out, call budget, manifest)
        │                                        │
        │ ingestion (backfill once, REFRESH_WEEK)│ guarded game preview, free quota
        ▼                                        │
  CFB_CORE  (persisted mirrors, all history)     │
        ▼                                        │
  CFB_MODEL (era-aware analytical model, as-of)  │
        ▼                                        │
  CFB_SIM  (sims, what-if, frozen predictions, scorecard, game win probabilities)
        ▼                                        ▼
  CFB_API  (stored serving views for the app and MCP user)    CFB_API_LIVE.QUOTA (app only)
        │
        ├──▶ FastAPI backend ◀── React app (odds · predictions · upsets · head-to-head · SQL↔HTTP inspector · refresh timeline)
        └──▶ Exasol MCP server (read-only user) ◀── Claude (chat loop in backend)
```

## Layers and schemas

| Schema | Contents | Reads from | Read by |
|--------|----------|-----------|---------|
| `CFB_INFRA` | Adapter, fetch UDF, loader scripts (`BACKFILL`, `REFRESH_WEEK`, `RUN_LOCK`) and registered MODEL/PREDICT/SIMULATE steps | — | `sys` (owner) |
| `CFBD_VS` | Virtual schema, one table per endpoint | CFBD API | Loader, guarded preview operator, `CFB_API_LIVE.QUOTA` indirectly through `CFB_META.QUOTA` |
| `CFB_META` | Tables: `LOAD_LOG`, `FETCH_LOG`, the compiled load plan (`LOAD_PLAN`, `LOAD_PLAN_GAPS`, `LOADER_CONFIG`, `SEASON_NUMBERS`, `REFRESH_STEPS`), the run records (`LOAD_RUN`, `REFRESH_LOG`, `LOAD_FAILURES`). Views: `LOADER_SETTINGS`, `LOAD_SLICES`, `LOAD_ORPHANS`, `BACKFILL_QUEUE`, `LOAD_STATUS`, `QUOTA` | `CFBD_VS` (`INFO`, for `QUOTA`) | `sys` (owner), the virtual schema (`LOAD_LOG`), `CFB_API` (inspector, timeline) |
| `CFB_STAGE` | One empty mirror per planned table: where a loader batch lands before the swap into `CFB_CORE` | `CFBD_VS` | The loader only |
| `CFB_RESTORE` | Landing schema that exists only while a restore runs (dropped afterwards, also on failure) | snapshot files | The restore only |
| `CFB_CORE` | Persisted mirrors of stored endpoints, all history | `CFB_STAGE` (loader), snapshot files (restore) | `CFB_MODEL` only (current-season rule below) |
| `CFB_MODEL` | Era-aware dims and facts (stored data only), as-of views, seeded reference tables, `GREAT_CIRCLE_KM`; every object commented | `CFB_CORE`, `CFB_META.{LOAD_PLAN, LOAD_PLAN_GAPS, LOAD_SLICES}`, itself | Analyses |
| `CFB_SIM` | Score model, run registry, `SIM_PATH`, `SIM_TEAM`, frozen predictions, scorecard and `GAME_WIN_PROB` | `CFB_MODEL` | `CFB_API` |
| `CFB_API` | 33 stored serving views, including odds, what-if, predictions and upsets; no own data tables | `CFB_MODEL`, `CFB_SIM`, stored `CFB_META` logs | `CFB_APP`, `CFB_MCP` |
| `CFB_API_LIVE` | Free quota view, separate from stored serving data | `CFB_META.QUOTA` | `CFB_APP` only |

Rules:
- The score engine reads `CFB_MODEL`; the serving layer reads `CFB_MODEL`, `CFB_SIM` and stored loader metadata.
- The app read user and the MCP user read stored data through `CFB_API`. Only the app role can read `CFB_API_LIVE.QUOTA`.
- Phase 6's guarded operator path is designed to query `CFBD_VS.GAMES_PREVIEW` directly with one validated literal game id after quota, ledger and pushdown checks. Exasol does not pass the required filter through a serving view.
- Each endpoint is `stored`, `federated` or `unavailable` (D15). Stored data is scanned broadly; federated data is only ever queried selectively (required filters + call budget); the API is never used for broad scans.
- **Current-season reads come from `CFB_CORE`, never through `CFBD_VS` (D36).** Current-season slices are never final, so a `CFBD_VS` query on them always fetches live and costs calls. Pages, the chat and analyses read the mirror; only the loader reads the current season through the virtual schema.

## Components

### Platform
- **Generator** (`vs/generate.py`): turns the pinned OpenAPI spec plus the reviewed `vs/catalog-overrides.yaml` into the endpoint catalog (findings: `docs/cfbd-api-reference.md`).
  - **Columns:** nested objects are flattened as `PARENT_CHILD`. **Arrays of objects become child tables** with parent keys + array index (e.g. `RANKINGS` → `RANKINGS_POLLS` → `RANKINGS_POLLS_RANKS`; `GAMES_TEAMS` holds everything below `teams[]`). Arrays of scalars become a JSON `VARCHAR`. Very large objects (game preview: 1,837 leaf fields; team season overview: 839) become a curated column subset plus the raw JSON. Order, edge cases and types: see [CFBD virtual schema contract](#cfbd-virtual-schema-contract).
  - **Params:** query and path params map to `P_<PARAM>` columns ([1]). Hard requirements come from the spec. Conditional rules ("required unless…", "one of week/team/conference with year", "cannot be combined with…"), defaults to override, row caps and tier gating come from the overrides file, because the spec states them only in prose.
  - **Refresh class and profile** are not in the catalog: they live in the load plan (`loader/load-plan.yaml`), which is validated against the catalog (D39).
  - A new API version means regenerate, review the diff, commit. Optional later: a `SPEC_SOURCE=live` property that reads the spec on `REFRESH` for the "new endpoint, one statement later it's a table" moment.
- **CFBD_ADAPTER** (`vs/adapter.py`): stateless Python adapter script.
  - Advertises filter capabilities only (`FILTER_EXPRESSIONS` plus the predicate and literal sets in the contract below). No aggregate, projection, `LIMIT`, ordering or join pushdown, which also avoids the 2026.1.0 UNION/aggregate crash. **Probe 1.4h (2026.2, `docs/spikes/s2-platform-probe.md`): no crash.** `COUNT`, `SUM`, `GROUP BY` and `COUNT DISTINCT` over the read-through VS (scalar, SET, one-group SET) and a user-side `UNION ALL` with a VS branch all returned correct results (8/8) with filter-only capabilities. The crash trigger itself (`AGGREGATE_SINGLE_GROUP` + `FN_AGG_COUNT_STAR`) was deliberately not run on the shared DB, so whether 2026.2 fixed the 2026.1.0 regression stays unknown and the rule stays.
  - On pushdown:
    1. Intersect predicates per parameter.
    2. If a filter contradicts itself (including `= NULL`), return zero rows without any call. This runs before rules, gating and budget ([13]).
    3. Reject queries that violate a hard or conditional requirement, naming the rule; reject gated endpoints the key has no access to (the `API_FEATURES` property, synced from `/info`, fail closed [7]). Every untranslatable predicate on a `P_` column is rejected with a message, never silently empty ([3]).
    4. Always set `classification` / `seasonType` explicitly; never rely on API defaults.
    5. Expand year ranges and `IN` lists into requests (slices) and enforce `MAX_CALLS_PER_QUERY` on the planned requests, stored slices included; a stored-only query (`REQ_SOURCE = 'STORED'`) plans no fetch and skips the budget ([4]).
    6. **Read-through (D15):** emit `SELECT … FROM CFB_CORE.<T> WHERE <slices in LOAD_LOG, IS_FINAL> UNION ALL SELECT * FROM (SELECT CFB_INFRA.CFBD_FETCH(…) FROM (VALUES <slice requests>) WHERE NOT EXISTS (<slice in CFB_META.LOAD_LOG>)) WHERE "REQ_SLICE" IS NOT NULL`. Already-stored slices cost 0 calls; missing ones fetch in parallel. The adapter itself cannot run SQL, so the check lives in the pushdown SQL. Stored and live columns are identical because both come from the generator, and Exasol requires the pushdown types to equal the VS column types exactly (probe 1.4c). The outer `WHERE` re-applies every pushed predicate, because Exasol does not re-check them (probe 1.4e). The `REQ_SLICE IS NOT NULL` wrapper is the empty-SET-group guard ([21], see the contract).
- **CFBD_FETCH** (`vs/cfbd_vs/fetch.py`): Python `SET … EMITS` UDF ([18]; a scalar `EMITS` UDF always runs in one VM serially, probe 1.4m).
  - Reads `CFBD_API`, performs the GETs of one query through a bounded thread pool (`MAX_PARALLEL_FETCHES`), flattens per the catalog, and puts the request metadata on every row as `REQ_*` columns.
  - Retries (at most 4 attempts, backoff, `Retry-After` up to 30 s) 429/5xx/timeouts/connection errors; redacts the key ([23]).
  - Modes: `live` / `replay` (recorded responses in BucketFS, never in git).
  - `TABLE_NAME = '@RAW'` emits the raw body in parts for the recorder ([11]).
  - Name resolution goes through the resolver seam (below).
- **Resolver seam** (`vs/cfbd_vs/resolver.py`, D21): the only module that touches `socket.getaddrinfo`.
  - The system resolver is tried first. On `gaierror` (the Personal nano 2026.2 UDF sandbox has an empty `resolv.conf` and `hosts`, so nothing resolves, probe 1.4q) it falls back to DNS over HTTPS: `GET <DOH_URL>?name=<host>&type=A`, `Accept: application/dns-json`, verified TLS, A records cached per UDF instance, then `getaddrinfo` with the IP.
  - `DOH_URL` defaults to `https://1.1.1.1/dns-query`, must be `https` with an IP-literal host (no resolution loop), and `off` re-raises the original error. DoH failures are not retried and name the host, `DOH_URL` and the reason.
  - Only name lookup changes. SNI, certificate verification and `Host` of the API connection stay on the host name; the DoH request carries only the host name, never the key. No pinned IPs. Remove the fallback once nano fixes the sandbox.
  - `make dns-check` (0 CFBD calls) resolves the API host through the seam and completes a verified TLS handshake without an HTTP request: it is the gate for every live step.
- **Fetch log:** UDFs cannot write tables, so nothing logs itself. Resolved ([6], [19]):
  - Every row carries the `REQ_*` columns, and the pushdown SQL starts with a JSON manifest of the planned requests, so requests that returned no rows are visible too (`EXPLAIN VIRTUAL`).
  - `CFB_META.FETCH_LOG` is written by the consumer that ran the query on purpose (Phase 2 loader, Phase 6 backend), through the reference helper `vs/host/fetchlog.py`: `capture_fetch_log` takes the staged result and the manifest of the same query and writes one row per slice the query actually read. The manifest's `sources` key ([21]) keeps a stored-only query from being logged as a fetch of zero rows.
- **Loader** (`loader/`): persists the stored endpoints and keeps them current. Contract in [Loader contract](#loader-contract).
  - **Load plan** (`loader/load-plan.yaml`, D39): per entry the table, slicing (`none` / `season` / `season_range`), seasons, gaps, refresh class (`static` / `weekly`), profile (`demo` / `full`) and priority, plus the monthly call budget and refresh reserve. `loader/plan.py` validates it against the catalog; `loader/templates.py` compiles each entry with the adapter's own planner into a slice-key template (D38). `make deploy` writes it into the `CFB_META` plan tables.
  - **Entry points are Lua scripting programs** (D33): `EXECUTE SCRIPT CFB_INFRA.BACKFILL(run_profile, from_season, max_calls)` and `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)`. Exasol has no `CALL` statement, so the demo line is `EXECUTE SCRIPT` (D34). Lua is the only in-database way to run multi-statement SQL: Python UDFs cannot write tables and the adapter cannot run SQL. The sources are `loader/lua/*.lua`, bundled by `loader/bundle.py` into `CREATE LUA SCRIPT` statements with deploy-time constants (every rendered identifier double-quoted).
  - **Thin Lua over SQL views** (D35): finality, gaps, gating, queue order, quarantine, orphans, calls to complete and the quota arithmetic are declarative views, tested on the engine before any Lua existed. Every statement in the Lua runs through `pquery` and every entry function through `pcall`, so a failure always reaches rollback, log, lock release and re-raise.
  - **Batches** (D37, D36): one plan entry per batch, one fan-out query through `CFBD_VS` with read-through left on and `REQ_SOURCE <> 'STORED'` instead of a property flip, staged in `CFB_STAGE`, then swapped into `CFB_CORE` with the `LOAD_LOG` and `FETCH_LOG` rows in one transaction.
  - **Failure handling** (D48, amended by plan decision [45]): per-slice fallback after a failed batch, quarantine after `quarantine_after` failing runs, a systemic stop after 2 consecutive failed batches (a batch fails when none of its slices is stored, also alone), and spend measured as the `/info` used-calls delta. Live batches are sized for 4 attempts per request, so a run overshoots `max_calls` by at most 3 calls.
  - **Orphan guard** (D47): stored slices that match no planned key block re-fetching their table.
  - **Step registry** (D44): `REFRESH_WEEK` is a chain `CHECK`, `FETCH <table>`, then registered steps, each called with `(run_id, season, week, season_type)`. Phases 3 to 5 register model, prediction and simulation steps without touching the refresh script.
  - **Snapshots** (D45): `make snapshot` / `make restore`, gzip CSV in a private directory, atomic and fingerprint-checked restore. A rebuild costs zero calls (D11).
  - **Host Python** only deploys, wraps the scripts with the estimate, the confirmation and the call ledger (`scripts/loader_cli.py`), takes the host run lock (`loader/runlock.py`) and runs snapshot and restore (`loader/snapshot.py`, `scripts/snapshot.py`).
  - **`PROMOTE()` is deferred.** The nightly persistence of on-demand slices needs the backend's capture of on-demand fetch logs (Phase 6); before that there is nothing to promote except by re-fetching. It is in the backlog.
- **CFB_MODEL** (`sql/model/`, plan `add-cfb-analytical-model`; one file per object, deployed in one transaction by `vs/host/sqlobjects.py` under the run lock):
  - **Stored data only.** It reads the demo profile's mirrors in `CFB_CORE` and the loader's plan views (`LOAD_PLAN`, `LOAD_PLAN_GAPS`, `LOAD_SLICES`), never `CFBD_VS`, so it costs zero calls (lint plus a dependency test). Datasets that are not stored (players, plays, drives, weather, ...) are rows of the reference table `DATASET` with their endpoint and `CFBD_VS` table; `SEASON_COVERAGE` gives a trust level per season and dataset.
  - **Objects:** seven seeded reference tables (eras, division labels, conference tiers, championship seasons, polls, datasets, classification transitions); eight derived tables (`GAME`, `SEASON_WEEK`, `TEAM`, `RATING_SP`, `RANKING`, `TEAM_MEMBERSHIP`, `TEAM_SEASON`, `AS_OF_POINT`); views for the rest (`TEAM_GAME`, `SEASON`, `VENUE`, `CONFERENCE`, `CONFERENCE_CHAMPIONSHIP_GAME`, `SEASON_COVERAGE`, `MODEL_CHECKS`) and the as-of views; one function. The dictionary is `docs/model-dictionary.md`.
  - **Derived tables** are views that were measured too slow (compile time of the deep stack) and became tables. They are current as of the last rebuild: `CFB_INFRA.REFRESH_MODEL` rebuilds them (registered refresh step 100, post-hook of `make backfill` / `make restore`, `make model`, `make model-refresh`). A `BACKFILL` run directly or a manual edit of `CFB_CORE` leaves them stale until the next rebuild (plan decision [40]).
  - **As-of views** (`CURRENT_AS_OF`, `GAME_AS_OF`, `RANKING_AS_OF`, `TEAM_SEASON_AS_OF`): one horizon, "known before the next week's first kickoff"; points are `(season, WEEK_SEQ)` with `WEEK_SEQ` 0 preseason, n regular week n, 100 + n postseason week n; games whose teams depend on results are hidden until known; filter with literal points. No in-season snapshot history.
  - **Venues** use `GEOMETRY(4326)` (Exasol has no `GEOGRAPHY`); distances come from `GREAT_CIRCLE_KM`.
  - **Refresh step 100 `MODEL`** (`CFB_INFRA.REFRESH_MODEL`): rebuilds the derived tables, evaluates `MODEL_CHECKS` for the point, logs to `CFB_META.MODEL_CHECK_LOG` and stops the chain on a failed ERROR check.
  - **Not built (backlog):** players, coaches, plays, drives, recruits, transfers, draft, weather, per-week snapshot history of SP+ and lines.

### Analyses
- **Score model** (`sql/sim/model/`): per remaining game, a predicted margin (rating difference + home field; ratings computed in SQL from games, with SP+ as a prior) and a predicted total (offense/defense parts of SP+, or a fallback). σ is calibrated on 2014–2025 from residuals, and calibration and Brier scores are reported. Upset probability for older seasons comes from pre-game Elo.
- **Engine** (`engine/`): one Python SET UDF on the contract below (D16), numpy-vectorized across the sims of a batch; batches run in parallel across cores and nodes.
- **Predictions in `CFB_SIM`:**
  - `REFRESH_WEEK` freezes the predictions for the upcoming week before its games are played. The scorecard separates in-advance predictions from reconstructed grades.
  - The scorecard: record, margin MAE, Brier score, calibration buckets.
  - The upset ranking: this week's upsets with their all-time rank.
- **Backend / Frontend / Bench:**
  - FastAPI over `CFB_API` and the chat loop (Claude API + MCP client).
  - React pages: odds, predictions + scorecard, upsets, head-to-head, plus the inspector and refresh timeline.
  - The benchmark harness writes to the run registry.

## CFBD virtual schema contract

What the generated virtual schema looks like from SQL, and the platform facts it rests on (probe results: `docs/spikes/s2-platform-probe.md`; user-facing view: `docs/user-guide.md`). Decision references are to `specs/_plans/add-cfbd-vs-foundation/decision-log.md`, promoted to `docs/roadmap.md` as D22 and onward.

### Tables and columns

- The catalog has 137 tables from the pinned spec 5.33.0: 93 endpoint tables and 44 child tables, nothing excluded. A generated, committed `vs/catalog/catalog.json` is the source of truth for the adapter, the fetch UDF and the mirror DDL; `catalog_hash` is the SHA-256 of its generated content ([25]) and the adapter/fetch handshake refuses a mixed deployment ([2]).
- **Column order per table:** position columns (`ROW_NO`, then one `<ARRAY>_NO` per nesting level, 1-based), declared parent keys, response columns (spec order), `RAW_JSON` (curated tables only), `P_*` (spec order), `REQ_*`. Children join their parent on `REQ_SLICE` plus the position columns ([8]).
- **`P_<PARAM>` columns ([1]):** one per query or path parameter. A row echoes the parameter values of the request that produced it, NULL if not sent; explicit defaults count as sent ([12]: `seasonType=both`, `classification=fbs` where the spec says so). Response fields keep their own columns, so `WEEK` and `P_WEEK` coexist. Putting a predicate on a `P_` column decides which requests are made.
- **`REQ_*` columns** (filled on every row):

  | Column | Meaning |
  |---|---|
  | `REQ_SLICE` | The request: canonical path plus sorted query string, equal to `LOAD_LOG.SLICE_KEY` |
  | `REQ_SOURCE` | `LIVE`, `REPLAY` or `STORED` |
  | `REQ_STATUS` | Final HTTP status; NULL for `STORED` |
  | `REQ_ATTEMPTS` | HTTP attempts including retries. `REPLAY` emits the recorded value, NULL for recordings that predate it ([24]); `STORED` is 0 |
  | `REQ_MS` | Wall time including retries; NULL for `STORED` |
  | `REQ_ROWS` | Rows the response produced for this table |
  | `REQ_AT` | UTC start of the (original) request |

- **Types ([9]):** integer `DECIMAL(18,0)`, number `DOUBLE`, boolean `BOOLEAN`, `date-time` UTC `TIMESTAMP`, string `VARCHAR(2000) UTF8`, JSON `VARCHAR(2000000) UTF8`. Strict: a value that doesn't fit fails the query naming table and column (never the value). Reserved-word column names get a trailing `_` (468 reserved words read from `EXA_SQL_KEYWORDS`, committed in `vs/gen/exasol_reserved_keywords.txt`). Table and column comments come from the spec and are cut to 2,000 characters, the platform maximum (probe 1.4i).
- **Flattening edge cases (resolved):**
  - A single-object response root is one row (`ROW_NO` = 1); an array root gives one row per element; a scalar root is a single `VALUE` column; a null body or a missing/null array yields no rows.
  - Hoisting of an array's parent into its rows happens only where declared and lossless (`/games/teams`, `/games/players`) ([8]).
  - Endpoints above 500 flattened fields need a curated subset plus `RAW_JSON` and get no child tables (game preview, team season overview).
  - Very wide stats endpoints (for example `PASSING_TEAMS_GAMES`, 399 columns) stay as wide as the API; the VS never pivots. Reshaping to long form is a `CFB_MODEL` concern.
  - Volatile endpoints (`/info`, `/info/usage`, `/games/schedule`, `/scoreboard`, `/live/plays` and their children) are never read through and have no mirror ([16]).

### Capabilities, pushdown shape and manifest

- **Capabilities** (probe 1.4e): `FILTER_EXPRESSIONS`; predicates `FN_PRED_AND/OR/NOT/EQUAL/NOTEQUAL/LESS/LESSEQUAL/BETWEEN/IN_CONSTLIST/LIKE/LIKE_ESCAPE/IS_NULL/IS_NOT_NULL`; literals `NULL`, `BOOL`, `DATE`, `TIMESTAMP`, `TIMESTAMP_UTC`, `DOUBLE`, `EXACTNUMERIC`, `STRING`. More than the translatable set is advertised on purpose so an untranslatable `P_` predicate reaches the adapter and is rejected instead of being evaluated locally against NULL echoes ([3]). **An unknown capability name is silently ignored** (the real one is `FN_PRED_LESSEQUAL`; the first draft `…LESSEQUALS` was dropped without a word), so a unit test pins the exact list.
- **Pushdown SQL** starts with `/*CFBD {manifest}*/` and has one of four shapes: zero-row (contradiction, nothing can contribute), read-through (stored `UNION ALL` fetch), live-only (volatile tables, `READ_THROUGH = FALSE`) and stored-only (`REQ_SOURCE = 'STORED'`, no fetch, no budget). The comment survives verbatim up to at least 1,000 slices (58 KB); `/` is escaped as `\/` so `*/` can never occur inside it (probe 1.4j).
- **Manifest keys:** `table`, `fetch_mode`, `read_through`, `budget` (`null` when no budget applied), `planned_requests`, `slice_keys` (every planned request, in order, including ones that return no rows), `sources` ([21]: the row sources the SQL actually reads: `[]`, `["STORED"]`, `["LIVE"]`, `["REPLAY"]`, or `STORED` plus the fetch source).
- **Fan-out ([18], resolved):** one `SET … EMITS` group, no `GROUP BY`. The UDF fetches all slices of a query with a thread pool of `MAX_PARALLEL_FETCHES` (default 6, 1 to 16) and emits rows in input order. Probe 1.4m: scalar `EMITS` over `VALUES` runs serially in 1 VM (6 × 2 s = 12 s); `SET` + `GROUP BY` slice reached 6 VMs idle but only 2 under load (6 slices took 6.0 s); one group with a pool ran 10 slices in 2.01 s on 1 VM regardless of load. The plan-level cost: all responses of one query sit in one VM's memory, capped by `MAX_CALLS_PER_QUERY`. Slices come from a `VALUES` list (up to 1,000 rows, probe 1.4d), not from a request table.
- **Empty-SET-group guard ([21]):** a one-group `SET … EMITS` UDF with zero input rows is never called, but Exasol still returns one all-NULL row (as an aggregate does over an empty set). In the read-through shape that happens whenever every planned slice is stored, so the fetch branch is wrapped in `SELECT * FROM (<fetch>) WHERE "REQ_SLICE" IS NOT NULL`. The UDF always sets `REQ_SLICE`, so no real row is lost.
- **Call budget ([4]):** `MAX_CALLS_PER_QUERY` (default 10) counts planned requests per virtual-table access at planning time, so it fails at `EXPLAIN VIRTUAL`, not at execution. A filter that expands to more than 10,000 requests is refused outright, stored-only queries included.
- **Known limitation (probe 1.4e): a subquery on an optional `P_` column is never pushed down.** Exasol evaluates `P_WEEK = (SELECT 8 FROM DUAL)` (also a function, an expression or a join condition on a `P_` column) locally, so the adapter never sees it, fetches without `week`, and the NULL echo makes the local filter match nothing: **0 rows, no error.** Required parameters are safe (the missing-value rule rejects the query); optional ones cannot be detected. Mitigation: literals on `P_` columns decide the requests, computed values filter response columns (`WEEK`, not `P_WEEK`). Documented in `docs/user-guide.md` §4.

### `LOAD_LOG` and `FETCH_LOG`

- **`CFB_META.LOAD_LOG` ([5])**: `(TABLE_NAME, SLICE_KEY, IS_FINAL, ROWS_LOADED, LOADED_AT, LOADED_BY)`, PK (`TABLE_NAME`, `SLICE_KEY`). A slice is served from `CFB_CORE.<T>` iff a final row exists (a stored empty slice is a hit at 0 calls); stored rows of a non-final or unregistered slice are never returned. Matching is exact on the canonical slice key, no subsumption (a stored season does not serve a week). `IS_FINAL = FALSE` lets `REFRESH_WEEK` re-fetch the current week without a bypass switch. `CFB_CORE` mirrors have the same columns, order and types as the VS, so `INSERT INTO CFB_CORE.T SELECT * FROM CFBD_VS.T` works. Changing an explicit default changes the keys; the catalog diff shows it, the loader reports the old slices as orphans and refuses to re-fetch the table until they are migrated or deleted (D47; there is no migration tool yet).
- **`CFB_META.FETCH_LOG`**: `(LOGGED_AT, CONTEXT, TABLE_NAME, SLICE_KEY, REQ_SOURCE, HTTP_STATUS, ATTEMPTS, MS, ROWS_RETURNED, REQUESTED_AT)`. The source column is `REQ_SOURCE`, not `SOURCE`, which is an Exasol reserved word ([19]); it is the same name as the VS column, so capture is a straight copy. Written only by consumers (above).

### Virtual schema properties

All optional; set at creation (`make deploy ARGS="--property NAME=value"`) or with `ALTER VIRTUAL SCHEMA`; effective on the next query with no `REFRESH`. A wrong value or an unknown name is rejected when set.

| Property | Default | Meaning |
|---|---|---|
| `CONNECTION_NAME` | `CFBD_API` | Connection holding API address and key |
| `FETCH_MODE` | `live` | `live` or `replay` |
| `REPLAY_PATH` | `/buckets/bfsdefault/default/cfbd/replay` | UDF-visible BucketFS directory of the recordings |
| `MAX_CALLS_PER_QUERY` | `10` | Request budget per virtual-table access (1 to 1000) |
| `MAX_PARALLEL_FETCHES` | `6` | Thread-pool width of one query (1 to 16) |
| `READ_THROUGH` | `TRUE` | Serve final stored slices from `CFB_CORE` |
| `API_FEATURES` | empty | Paid CFBD features of the key; empty fails closed; synced from `/info` by `make deploy` |
| `DOH_URL` | `https://1.1.1.1/dns-query` | DoH fallback endpoint (`off` disables) |
| `INFRA_SCHEMA`, `META_SCHEMA`, `CORE_SCHEMA` | `CFB_INFRA`, `CFB_META`, `CFB_CORE` | Schema names; the integration tests deploy under `TEST_` |

### Replay layout (resolved)

- One file per real request path: `<REPLAY_PATH>/<endpoint path segments>/<sha256(slice_key)[:24]>.cfbd.json` holding `{"slice_key", "status", "attempts", "ms", "requested_at", "body"}`. `body` is the raw response text, so every table of an endpoint (parent and children) replays from one file. Recordings store the attempts of the recorded request ([24]); older ones decode with NULL attempts, never 0. Path segments are restricted to `A-Za-z0-9._-` (at most 100 characters) and a missing recording fails the query naming the slice key and path; nothing falls back to the network. Replay never reads the connection.
- **BucketFS on Personal (probe 1.4k):** the host directory `~/.exasol/personal/deployments/local/local/runtime/exa/bucketfs/<service>/<bucket>/<path>` appears in UDFs as `/buckets/<service>/<bucket>/<path>`, read-only (EROFS) there. Only depth-2 directories (`<service>/<bucket>`) become buckets; a new bucket is visible after about 1 s, files in an existing bucket after 0.1 to 0.3 s. Hence the default `REPLAY_PATH` `/buckets/bfsdefault/default/cfbd/replay`, with the standard default bucket name.
- Recordings are written by `scripts/record.py` (task 9.1) and, like `cache/`, never committed (`.cfbd.json` and `cache/` are gitignored).

### Runtime targets

- Exasol Personal **2026.2**: the local nano deployment `local` (podman, `2026.2.0-nano.3`, 127.0.0.1:8563) and the AWS `default` 3-node instance. `TARGET=local|aws` (default `local`) selects the deployment directory under `~/.exasol/personal/deployments` (`local/` or `default/`); `vs/host/target.py` reads host, port and the sys password at run time from `deployment.json` and `secrets.json` (`dbPassword`) and never stores them.
- **TLS:** `local` skips certificate validation (loopback, self-signed). `aws` pins the SHA-256 fingerprint of the node certificate from `deployment.json` (cross-checked with `connection-instructions.txt`), passed in the DSN, and refuses to connect unverified when no fingerprint is available. `EXASOL_DSN` overrides host and port and never loosens TLS.
- The integration tests run on `TARGET=local` only (the mock sidecar needs the DB container's network namespace). `make deploy`, `grants`, `dns-check`, `record`, `backfill`, `refresh`, `quota`, `snapshot`, `restore` and `snapshots` accept `TARGET=aws`. The loader reuses `vs/host/target.py` for every one of them and adds no second target module (D46).
- The earlier S0 spike ran on a retired docker 2026.1.0 container, so every platform fact above was re-probed on Personal 2026.2 before the pushdown SQL shape was fixed ([15]).

## Loader contract

What the loader writes and how it behaves (specs: `specs/loader/*`; decisions D33 to D49 in `docs/roadmap.md`, with the details in the Phase 2 plan's decision log; user view: `docs/user-guide.md` §9).

### Load plan and views

- `loader/load-plan.yaml` is the only place that decides what costs calls. `make deploy` validates it against the catalog (every problem listed at once, nothing changed on error), compiles every entry into a slice template and replaces `LOAD_PLAN`, `LOAD_PLAN_GAPS`, `LOADER_CONFIG` and `REFRESH_STEPS` in one transaction. `LOAD_PLAN.PLAN_HASH` is the hash of the plan file; a `LOADER_CONFIG` override changes settings but not the hash. `LOADER_CONFIG.NOW_OVERRIDE` is a test clock and is removed outside a `TEST_` namespace.
- Slice keys come from the adapter's own planner run with sentinel values, so planned and requested keys cannot drift (D38). Several entries per table are allowed (for example one range slice plus one slice per season). A `none` or `season_range` slice has `SEASON` NULL, so it is queued first and `from_season` never skips it.
- Views, each reading only tables and views before it: `LOADER_SETTINGS` (one row: the UTC clock or `NOW_OVERRIDE`, the current season, the settings, `API_FEATURES`), `LOAD_SLICES` (one row per planned slice with `FINAL_IF_LOADED_NOW`, `IS_STORED`, `IS_FINAL`, `FAILURES`, `IS_QUARANTINED`, `IS_PENDING`), `LOAD_ORPHANS`, `BACKFILL_QUEUE` (candidates in `QUEUE_ORDER`: slices with failures last, then profile rank, priority, newest season, table, entry), `LOAD_STATUS` (per entry: planned, stored, final, missing, quarantined, orphans, calls to complete, `BLOCKED_BY`) and `QUOTA`. Entries whose feature is missing from `API_FEATURES` have no slices and report `BLOCKED_BY` the feature.
- **Finality is a date rule** (D40): `static` slices are final on load; `weekly` slices of season S are final from `season_final_after` (02-15) of S+1; the current season starts on `season_rollover` (08-01). A slice stored before it was final is fetched once more by the next backfill after it becomes final. Storage classes of D15 are derived from the plan, not configured: `stored` is a `demo` entry, `federated` is no entry or `full` only.
- Backlog: `season_week` slicing with week-based finality, authoring the `full` profile's entries, an `archive` refresh class, an automatic refresh of the current season's calendar.

### Scripts and the batch transaction

- The scripts are bundled from `loader/lua/loadlib.lua` (shared library: lock, run and step logging, quota read, batch, fallback, `fail`) plus `backfill.lua`, `refresh_week.lua` or `run_lock.lua`. Lua runs with the caller's rights, so on the real deployment `sys` owns and runs them. An app-triggered refresh (Phase 6) needs a loader role with `EXECUTE` on the scripts plus every privilege they use; that is deferred to Phase 6. Calls take exactly the declared number of arguments, and `WITH OUTPUT` returns only the script's log lines, so the demo line never uses it.
- The platform facts behind this design were probed on Personal local (`docs/spikes/s5-loader-probe.md`): `COMMIT` and `ROLLBACK` work in scripts; a failing `pquery` rolls back only that statement; a script that ends in `error()` or an uncaught `query()` failure **commits** its open transaction under autocommit, so the error path always rolls back first; a nested `EXECUTE SCRIPT` shares the outer transaction; commits of a running script are visible to other sessions; the session time zone is `CET`, so every loader timestamp uses one UTC expression.
- **Batch** (D37), for one plan entry of at most `MIN(MAX_CALLS_PER_QUERY, allowance)` slices:

```
 quota read (live mode, own transaction) ─▶ allowance
 EXPLAIN VIRTUAL <batch query>   manifest keys = batch keys?      (0 calls; else stop: "make deploy")
 assert CFB_STAGE.<T> is empty
 INSERT INTO CFB_STAGE.<T> SELECT * FROM CFBD_VS.<T> WHERE <fixed filter> AND <batch filter> AND REQ_SOURCE <> 'STORED'
 check: every staged REQ_SLICE is a batch key
 DELETE FROM CFB_CORE.<T> WHERE REQ_SLICE IN (batch) ; INSERT INTO CFB_CORE.<T> SELECT * FROM CFB_STAGE.<T>
 MERGE INTO LOAD_LOG (IS_FINAL = FINAL_IF_LOADED_NOW) ; INSERT INTO FETCH_LOG ; DELETE FROM LOAD_FAILURES (batch)
 DELETE FROM CFB_STAGE.<T> ; UPDATE LOAD_RUN / REFRESH_LOG ; COMMIT ; measured spend = Δ /info used calls
 any error: ROLLBACK, log FAILED, COMMIT, re-raise
```

  - Batch filters: `season` is `"P_YEAR" IN (...)`, `season_range` is `"P_MIN_YEAR" = a AND "P_MAX_YEAR" = b`, `none` has none; each is prefixed by the entry's fixed filter. Slice keys are bound values, never concatenated.
  - **Read-through stays on** (D36). Virtual schema properties are global, so switching `READ_THROUGH` off for a load would make concurrent queries re-fetch stored slices and a crash would leave it off. A batch contains only slices that are missing or not final, which read-through fetches anyway; the `REQ_SOURCE` filter is the safety net. No loader code issues `ALTER VIRTUAL SCHEMA` (a unit test scans the sources).
  - The HTTP part runs before `CFB_CORE` is touched, so the write window is short and other sessions never see a half-replaced slice. Rows in `CFB_CORE` keep the fetch source in `REQ_SOURCE` (`LIVE` or `REPLAY`); reads through the virtual schema relabel them `STORED`.
- **Backfill** (D43): candidates are missing slices plus finalizable non-final ones, without quarantined slices and tables with orphans; gaps become final empty slices (`LOADED_BY = 'LOAD_PLAN_GAP'`) at 0 calls. `max_calls = 0` is a dry run. The result has one row per table plus a `*` total row with numeric `CALLS_NEEDED` and `FAILED_SLICES`.
- **Weekly refresh** (D41): `REFRESH_WEEK` re-fetches the current season's season slices of the `weekly` entries (4 calls for the demo plan) instead of adding week slices, because read-through matches slice keys exactly and the same game must never be stored under two slices. A season call costs the same one call and picks up corrections to earlier weeks. `week` and `season_type` are validated against the stored calendar and name the run. Child tables join their parent by position within one response, so two child tables fetched by two requests can drift apart: rankings load from `RANKINGS_POLLS_RANKS` alone, which carries its own `SEASON`, `SEASON_TYPE`, `WEEK` and `POLL` keys (D49).

### Failures, quarantine, orphans

- The fetch UDF fails a whole fan-out query when one slice fails, so a retry re-pays the siblings. `BACKFILL` rolls back a failed multi-slice batch and fetches each slice alone (D48). A slice that fails alone gets one `LOAD_FAILURES` failure per run, is then batched alone after every other candidate, and is quarantined after `quarantine_after` (2) runs. A success clears it; the owner clears a quarantine by deleting its row. If every slice of a fresh batch fails alone, the run stops `FAILED`. `REFRESH_WEEK` has no fallback: a failing step stops the chain and a failed fetch keeps the table's previous rows.
- **Orphans** (D47) are loader-written `LOAD_LOG` rows (`BACKFILL#...`, `REFRESH_WEEK#...`, `LOAD_PLAN_GAP`) of a planned table that match no planned key, after a catalog change, plan edit or restore. `LOAD_STATUS` reports them, `make deploy` warns, and both scripts refuse to fetch their table, because a re-fetch would pay twice (D11). A table whose feature is missing never reports orphans.
- Stored data is pinned to the catalog: deploy and restore refuse a column difference. Migrating mirrors when the catalog changes a column or default is **open** (below).

### Run lock, run records and refresh steps

- `LOAD_RUN` has one row per run (`BACKFILL`, `REFRESH_WEEK`, `SNAPSHOT`, `RESTORE`; the kinds `DEPLOY` and `SIMULATE` are for the model deploy and the simulation runs of later phases). A `RUNNING` row is the run lock. It is stale, set to `ABANDONED`, and the next run proceeds, when its session is gone, when it is the caller's own session, or, for script kinds, when the session is idle (a script is one statement, so idle means it ended without closing the row). Host kinds (`SNAPSHOT`, `RESTORE`, `DEPLOY`) run from sessions that are idle between statements, so only a vanished session frees them. Two concurrent starters: exactly one wins; the loser's insert fails at once with a transaction collision (`40001`), which is retried up to 3 times and then refused (a live row is refused with a message that names the holder). A killed run cannot log `FAILED`; the next run frees its row.
- Host processes take the lock through the deployed script `<INFRA>.RUN_LOCK(lock_action, lock_word, lock_run, lock_text)`, wrapped by `loader.runlock.run_lock` (a context manager: the row ends `DONE`, or `FAILED` with the error after rolling the session back). One implementation of the stale rules serves Lua and host.
- `REFRESH_LOG` has one row per step, committed when the step starts and when it ends, so the app's timeline (Phase 6) reads progress from another session. Built-in steps: 1 `CHECK`, 2 to 99 `FETCH <table>`. Registered steps (100 and up, from `refresh_steps` in the plan, deployed to `REFRESH_STEPS`) are called as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)`, where `season_type` is `'regular'` or `'postseason'` (D44): both season types number their weeks from 1, so a bowl week needs it. A step declares exactly these four parameters, **must not take the run lock** (the refresh holds it, and acquiring it again would abandon the refresh's own row), runs inside the refresh's transaction (the refresh commits before the step and commits its work with the step's `DONE` row; a failing step is rolled back before `FAILED` is written) and reads `CFB_CORE`, not `CFBD_VS`. The loader registers no steps.

### Budget guards

- **In the database** (D42): `QUOTA` reads the free `/info` once per query. `PROJECT_BUDGET = LEAST(MONTHLY_LIMIT, monthly_call_budget)` caps the key's whole monthly use, `REFRESH_SPENDABLE = GREATEST(0, LEAST(REMAINING_CALLS, PROJECT_BUDGET - USED_CALLS))`, `BACKFILL_SPENDABLE = GREATEST(0, REFRESH_SPENDABLE - refresh_reserve)`. `USED_CALLS` is the key's whole month (other tools and the shared CBBD pool included), so the cap holds for every SQL client. A refresh may use the reserve; replay runs read no quota. `BACKFILL_CALLS` and `REFRESH_CALLS` count `FETCH_LOG` attempts, not rows (a retry costs the key a call).
- **Ledger** (`vs/host/ledger.py`, `cache/live-calls.jsonl` in the main working tree, caps in `vs/live/ledger.yaml`): the second guard. A plan's total plus the estimate must fit its cap (`plans`), and `/info` used calls plus the estimate must fit the month's cap (`months`, the owner's authorization; a month without an entry refuses live calls). The wrappers `make backfill` / `make refresh` check it, cap `MAX_CALLS` at the room and append one entry per run. `LEDGER=off` disables it for people who run their own key. It covers the real namespace only.
- Per-target loader settings live in `vs/live/loader-config.yaml` and every `make deploy TARGET=<t>` applies them: on `aws` the in-database budget is the ledger's cap for the deploy's UTC month (400 in October 2026, 1000 from the first November deploy) with a reserve of 50, so a plain redeploy never lifts it. `LOADER_CONFIG` wins per setting.

### Snapshots

- Directory `~/.cfb-demo/snapshots/<name>/` (modes 0700/0600, `CFB_SNAPSHOT_DIR` overrides; refused inside the repository or a OneDrive folder; never overwritten). **No S3** (external, costs money): an AWS snapshot streams to the laptop through `TARGET=aws`. Snapshots stay private because CFBD forbids redistributing the data.
- Format (D45, amended by probe 1.2): one gzip CSV per non-empty table, written and read by pyexasol `export_to_file` / `import_from_file` over the target's pinned connection, plus `manifest.json` written last (logical schema names, catalog hash, plan hash, per table columns, rows, SHA-256, per-column fingerprint). exapump's Parquet path was rejected after the probe: it fails on VARCHARs with a comma, quote or newline, trims spaces and writes untyped columns.
- Scope: every `CFB_CORE` table plus `LOAD_LOG` and `FETCH_LOG`. `LOAD_RUN`, `REFRESH_LOG` and `LOAD_FAILURES` stay with each deployment (restoring them would collide identities and resurrect `RUNNING` rows).
- Snapshot: under the lock, fingerprint every table before and after the export and keep nothing if the data changed. Restore: manifest, deployment checks (columns and order, the exact table set, catalog hash unless `FORCE=yes`; `FORCE` never overrides a column difference), file checks (SHA-256, header), then the lock, a landing schema `CFB_RESTORE`, fingerprints, one transaction that replaces every scope table, the landing schema dropped (also on failure) and a `RESTORE` row. It never reads `CFBD_VS` (0 calls). A failed restore changes nothing; this is also the rehearsal reset.
- Fingerprints are the same generated SQL on both sides: `DECIMAL` sum, `BOOLEAN` count, `DATE` days since 1970, `TIMESTAMP` seconds since 1970 (never `POSIX_TIME`, which depends on the session time zone), `VARCHAR` total length. **`DOUBLE` exception:** Exasol writes a `DOUBLE` with 15 significant digits, so a value with more digits comes back rounded to its text form (relative change at most 4e-12); the fingerprint sums the text form, rounded to 8 decimals, on both sides, so it describes the restored value. Every other type round-trips exactly.

### Open: mirror migration

Deploy and restore refuse a column difference, and orphans block re-fetching, but nothing migrates stored rows when the catalog changes a column or an explicit default (which changes every slice key of a table). The owner migrates or deletes the orphans by hand until a migration plan exists.

## Engine contract

- **Data inputs come from the model:** `CFB_MODEL.GAME_AS_OF` and `CFB_MODEL.TEAM_SEASON_AS_OF` hold every data input below (neutral site, conference flag, final margins, records, conference records, tier, Elo, SP+ prior) as fixed-scale `DECIMAL`; the engine computes indices, μ, σ, ratings and the capped differential itself (plan decision [31]; the mapping is in Key Interfaces of the recorded plan `specs/_recorded/2026-10-23-add-cfb-analytical-model/plan.md`).
- **Input:** one SET call per sim batch with the full remaining-season model (D19, `docs/spikes/s3-python-engine-throughput.md`).
  - The model is one table of tagged rows, `MODEL_ROWS(row_kind ∈ {GAME, TEAM, PARAM}, …)`; columns that do not apply to a kind are NULL.
    - `GAME`: game_idx, home_idx, away_idx, neutral, conference flag, predicted margin μ, σ, predicted total; final games carry the played flag and margin instead (needed for head-to-head).
    - `TEAM`: team_idx, team, conference, conference tier, rating, strength of schedule, records so far, conference point differential.
    - `PARAM`: key/value overrides of the defaults (selection config, rating-update flag).
  - Run columns (run_id, run_seed, sims_per_batch, batch_id) are constant per call.
  - Fan-out: `MODEL_ROWS` cross-joined with the batch ids, `GROUP BY batch_id`. Every group receives the whole model; row order inside a group is not guaranteed, so the engine re-sorts by `game_idx` / `team_idx`.
  - **Real values are fixed-scale DECIMAL**, never DOUBLE: Exasol's string-to-DOUBLE parse is not correctly rounded, so DECIMAL keeps host and UDF model values bit-identical. The engine rejects an invalid model instead of repairing it.
  - Concurrency is `nrOfCores` instances per node, and Personal sizes cores and DB RAM at each start. Recommended `sims_per_batch` 1,000–10,000 and at least 2 batches per slot; the stored default is 10,000 (D19).
- **Randomness:** numpy `Generator(Philox(SeedSequence([run_seed, batch_id])))`, one generator per batch, `sim_id = batch_id * sims_per_batch + k`. A run is reproducible regardless of how batches are scheduled (verified across reruns, regrouping, local and AWS), **given the same `sims_per_batch` and numpy version**. Changing the batch size changes the results, and numpy only guarantees stable Philox streams, not distribution methods, across versions (NEP 19); the S3 comparison of numpy 1.26.4 and 2.4.4 found identical draws, but the version stays in the key.
  - **Reproducibility key** (stored in the run registry): `(run_seed, sims_per_batch, n_sims, numpy + Python version, model digest incl. conference ordering, engine/rules version)`. The model digest is a SHA-256 over the parsed model; conference names are sorted, so the ordering is covered by it.
  - Margin = μ + σ·z. A margin of exactly 0 is decided by an extra draw (overtime).
  - Fixed results override the draw, using the sign plus a conditional margin.
  - The draw order is part of the contract (season normals, overtime uniforms, postseason normals, postseason uniforms); changing it changes every result.
- **Per sim:** regular season with scores → standings (simplified tiebreakers, point differential available) → conference championships → resume ranking → selection (4 power champions + highest-ranked G6, at-large to 12; top 4 = byes; straight seeding) → bracket.
- **Output** (D20, `docs/spikes/s4-sim-storage.md`):
  - `SIM_TEAM` is **sparse**: only the rows where the team is in the CFP, about 12 per sim: run_id, sim_id, team, seed, bye, reached_round, champion (no `in_cfp` flag, no dense table). A `TEAMS` roster supplies the teams with zero in-CFP rows, and `n` (sims in a run) comes from the path table. The engine emits only the in-CFP rows, so it never emits the 136 rows per sim of the dense shape.
  - `SIM_PATH` is **layout C**: one row per sim, `run_id, sim_id` plus one BOOLEAN column per remaining game (home win), DDL generated per run from the game list. A (one VARCHAR vector, fixed schema) is the near-tie fallback.
  - `LEVERAGE (run_id, team, game_idx, n_home, n_home_in_cfp, n_away, n_away_in_cfp)` is built **by the loader** after the load (one pass over the in-CFP rows and the path table), not by the engine. It serves the unconditional rooting guide.
  - **No per-sim margins.** The per-run histogram of game margins (M0, about 1 MB per run) is optional and off by default.
  - Engine implementation: emit one `pandas.DataFrame` per batch with narrow dtypes, run the engine once per batch for all outputs, and use one UDF per output shape with explicit `EMITS`.

## Security model (S0 verified on 2026.1.0, re-verified on Personal 2026.2 by `tests/it/test_key_protection_it.py` and probe 1.4g/1.4o; the throwaway S0 script was deleted 2026-10-21 because it recreated `CFBD_API` with a dummy key)

| Principal | Holds | Purpose |
|-----------|-------|---------|
| `sys` (DBA) | The `CFBD_API` CONNECTION; owns all CFB schemas, the adapter, the fetch UDF and `CFBD_VS` ([20]) | Creates and rotates the key; deploys, backfills, refreshes. No `CFB_OWNER` user, no owner password |
| `CFB_READER` (role) | `SELECT` on `CFBD_VS` (created by `make grants`) | Anyone who queries the virtual schema |
| `CFB_APP` | `SELECT` on `CFB_API` and `CFB_API_LIVE` | Backend read and free quota view |
| `CFB_MCP` | `SELECT` on `CFB_API` | MCP server |

Verified:
- **Script-bound access works** (the `TEST_` namespace keeps a dedicated owner `CFB_TEST_OWNER` with `GRANT ACCESS ON CONNECTION <c> FOR SCRIPT <fetch> TO <owner>`, so this stays tested). A different script of the same owner cannot read the key.
- **Readers of the virtual schema need no connection privilege** (the pushdown runs with the owner's rights, probe 1.4g). They cannot call the fetch UDF, `LOAD_LOG`, the core tables or the connection directly; they may run `EXPLAIN VIRTUAL`.
- **The key doesn't appear in pushdown SQL or `EXPLAIN VIRTUAL`.** VS properties hold only `CONNECTION_NAME`. Responses and error messages are redacted ([23]): the key's raw, JSON-escaped and percent-encoded forms in bodies, plus any `Bearer <token>` in error text.
- **`CREATE OR REPLACE` of the fetch UDF drops a script-bound grant, even for identical text** (probe 1.4o). Deploy therefore compares the stored body with the generated one and replaces only when they differ. This matters for the `TEST_` owner (re-run `make grants ARGS="--prefix TEST_"` after a replace); the real deployment is owned by `sys`, which needs no grant.
- **UDF egress and DNS:** UDFs have no name resolution on Personal nano 2026.2, so the fetch UDF uses the resolver seam with the DoH fallback (D21, above).
- **Paid preview:** the read user has no direct virtual-schema grant. The planned loopback-only operator path uses the owner's session with an exact allowlist for a single `GAMES_PREVIEW.P_GAME_ID`, after quota, ledger and `EXPLAIN VIRTUAL` checks. The MCP role cannot use that path.

Rules:
- The DBA creates the CONNECTION with plain SQL in a SQL client (D18; documented in `docs/user-guide.md`). There is no key tool; never pass the key on a shell command line.
- Rotate with `ALTER CONNECTION`.
- Loader scripts run with the caller's rights, so `sys` runs them. Letting the app user trigger a refresh needs a loader role with `EXECUTE` on the scripts and the privileges they use; that is a Phase 6 decision. Snapshots hold CFBD data and stay outside the repository and OneDrive.
- Never grant `ACCESS ANY CONNECTION`.
- **Trade-off of [20] (sys ownership):** `sys` can read every connection, so "only `CFBD_FETCH` reads the key" holds for readers (`CFB_READER`, `CFB_APP`, `CFB_MCP`), not for the owner. Accepted for a demo on a local Personal database: a separate owner would add a password and a grants step without demo value. A deployment that needs the stronger guarantee uses a prefixed namespace with its own owner, as the integration tests do.

## Design questions (Phase 0 spikes)

- **Resolved (D21): UDF DNS on Exasol Personal nano.** UDFs have no name resolution (empty resolv.conf and hosts). `CFBD_FETCH` resolves through a seam: system resolver first, DNS-over-HTTPS fallback (configurable property, default `https://1.1.1.1/dns-query`). TLS keeps verifying the hostname. Verified with `/info` on 2026-10-21.

- **Resolved ([18]): fan-out shape.** One `SET … EMITS` group over a `VALUES` slice list plus a thread pool of `MAX_PARALLEL_FETCHES` (default 6), not `GROUP BY` slice and not a request table. The pool width also bounds concurrent CFBD calls (rate-limit friendly). See [CFBD virtual schema contract](#cfbd-virtual-schema-contract).
- **Resolved ([6], [21]): fetch-log mechanism.** `REQ_*` columns on every row plus a JSON manifest (with the `sources` key) in the pushdown SQL; consumers persist to `CFB_META.FETCH_LOG` with `vs/host/fetchlog.py` (`capture_fetch_log`). No UDF writes anything.
- **Resolved (D33, D34): loader entry points.** Lua scripting programs, run with `EXECUTE SCRIPT` (Exasol has no `CALL`); host Python only deploys and wraps them.
- **Resolved (D45): snapshot format.** gzip CSV through pyexasol, not Parquet through exapump (probe 1.2); no S3.
- **Deferred: `PROMOTE()`** (nightly persistence of on-demand slices) until the backend captures on-demand fetch logs (Phase 6); backlog.
- **Open: mirror migration on a catalog change.** See [Loader contract](#loader-contract).
- **Open (Phase 3, Q3): as-of granularity inside the postseason.** CFBD puts all bowls and CFP rounds in one postseason week, so as-of points inside the postseason are no finer than that week; Phase 4 and 5 handle it by date.
- **Resolved: flattening edge cases.** Object-root = one row, scalar root = `VALUE`, over-500-field objects curated + `RAW_JSON`, wide stats tables not pivoted (a `CFB_MODEL` concern).
- **Resolved (D20): `SIM_PATH` layout and per-game score storage at 1M sims.** Layout C (wide BOOLEAN), sparse `SIM_TEAM`, loader-built `LEVERAGE`, no per-sim margins (`docs/spikes/s4-sim-storage.md`). Still open for Phase 5: the conditional rooting guide (k = 3) measures about 2.2 s p95 at 1M against the < 1 s target.
- **Resolved (D19): Python engine throughput.** The vectorized chain runs 100k sims in about 7 s (local, compute only) and 12.4 s end to end on AWS; locally 55.4 s median, dominated by row emission. `GROUP BY batch_id` fans out to `nrOfCores` instances per node (`docs/spikes/s3-python-engine-throughput.md`).
- **Resolved ([24], probe 1.4k): replay recordings.** One file per real request path at `/buckets/bfsdefault/default/cfbd/replay/<endpoint segments>/<sha256(slice)[:24]>.cfbd.json`, storing attempts; recorded by `scripts/record.py` under the call ledger, gitignored.
- **Open, accepted limitation (probe 1.4e): subquery on an optional `P_` column.** Returns 0 rows without an error because Exasol never pushes it down; documented in the contract and the user guide, not detectable by the adapter.
- **CFP rules:** G6 auto-bid wording, configurable.
- **Key masking:** is `CREATE CONNECTION` statement text masked in auditing and profiling tables? Check before shared or AWS use.
