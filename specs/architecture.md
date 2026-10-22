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
        │ ingestion (backfill once, REFRESH_WEEK)│ on-demand (previews, matchups, box scores, player lookups, quota)
        ▼                                        │
  CFB_CORE  (persisted mirrors, all history)     │
        ▼                                        │
  CFB_MODEL (era-aware analytical model, as-of)  │
        ▼                                        │
  CFB_SIM · CFB_PRED  (sims, what-if, predictions, scorecard, upsets)
        ▼                                        ▼
  CFB_API  (serving layer: curated, commented views + functions; the ONLY thing apps and the MCP user see)
        │
        ├──▶ FastAPI backend ◀── React app (odds · predictions · upsets · head-to-head · SQL↔HTTP inspector · refresh timeline)
        └──▶ Exasol MCP server (read-only user) ◀── Claude (chat loop in backend)
```

## Layers and schemas

| Schema | Contents | Reads from | Read by |
|--------|----------|-----------|---------|
| `CFB_INFRA` | Adapter, fetch UDF, loader and refresh scripts | — | `sys` (owner) |
| `CFBD_VS` | Virtual schema, one table per endpoint | CFBD API | Loader, `CFB_API` on-demand views, presenter |
| `CFB_META` | Load plan, `LOAD_LOG`, `FETCH_LOG`, refresh-step log, endpoint catalog | — | `sys` (owner), the virtual schema (`LOAD_LOG`), `CFB_API` (inspector, timeline) |
| `CFB_CORE` | Persisted mirrors of stored endpoints, all history | `CFBD_VS` | `CFB_MODEL` only |
| `CFB_MODEL` | Era-aware dims and facts, as-of helpers, `COMMENT ON` throughout | `CFB_CORE` | Analyses |
| `CFB_SIM` | Season model, run registry, `SIM_PATH`, `SIM_TEAM`, what-if | `CFB_MODEL` | `CFB_API` |
| `CFB_PRED` | Weekly predictions (frozen at refresh), scorecard, upset rankings | `CFB_MODEL`, `CFB_SIM` | `CFB_API` |
| `CFB_API` | Serving layer | `CFB_MODEL`, `CFB_SIM`, `CFB_PRED`, on-demand `CFBD_VS` tables | App user, MCP user |

Rules:
- Analyses read only `CFB_MODEL` and their own schemas.
- Apps and the MCP user read only `CFB_API`.
- On-demand endpoints reach apps only through `CFB_API` views, which always carry the required filters, so a click never fans out.
- Each endpoint is `stored`, `federated` or `unavailable` (D15). Stored data is scanned broadly; federated data is only ever queried selectively (required filters + call budget); the API is never used for broad scans.

## Components

### Platform
- **Generator** (`vs/generate.py`): turns the pinned OpenAPI spec plus the reviewed `vs/catalog-overrides.yaml` into the endpoint catalog (findings: `docs/cfbd-api-reference.md`).
  - **Columns:** nested objects are flattened as `PARENT_CHILD`. **Arrays of objects become child tables** with parent keys + array index (e.g. `RANKINGS` → `RANKINGS_POLLS` → `RANKINGS_POLLS_RANKS`; `GAMES_TEAMS` holds everything below `teams[]`). Arrays of scalars become a JSON `VARCHAR`. Very large objects (game preview: 1,837 leaf fields; team season overview: 839) become a curated column subset plus the raw JSON. Order, edge cases and types: see [CFBD virtual schema contract](#cfbd-virtual-schema-contract).
  - **Params:** query and path params map to `P_<PARAM>` columns ([1]). Hard requirements come from the spec. Conditional rules ("required unless…", "one of week/team/conference with year", "cannot be combined with…"), defaults to override, row caps and tier gating come from the overrides file, because the spec states them only in prose.
  - **Refresh class and profile** per endpoint.
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
- **Loader** (`loader/`):
  - `BACKFILL(profile)`: walks the load plan by profile (`demo` first, then `full`) and priority, newest first, from each endpoint's history start. It skips known gaps and slices already in the load log, and stops at the monthly call budget minus the refresh reserve (remaining quota is read from `/info`).
  - `REFRESH_WEEK(season, week)`: refreshes the `demo` profile endpoints for that week (about 8 calls); `full` profile endpoints refresh on the archive's slower schedule.
  - `make snapshot` / `make restore`: Parquet export/import of `CFB_CORE` plus the load log, stored privately outside the repo. A rebuild costs zero calls (D11).
  - `PROMOTE()` (nightly): persists slices that were fetched on demand (from the fetch log) into `CFB_CORE`, so frequently requested federated data becomes stored over time.
  - Both log steps to `CFB_META.REFRESH_LOG` (step, start, end, rows, calls).
- **CFB_MODEL** (`sql/model/`):
  - Dimensions: team, team-season (conference, classification/era), venue (`GEOGRAPHY`), player, coach, calendar.
  - Facts: game, team-game stats, play, drive, rating (Elo/SRS/SP+/FPI by week), ranking, recruit, transfer, draft pick, line, weather.
  - Coverage flags per game for play-level facts.

### Analyses
- **Score model** (`sql/sim/model/`): per remaining game, a predicted margin (rating difference + home field; ratings computed in SQL from games, with SP+ as a prior) and a predicted total (offense/defense parts of SP+, or a fallback). σ is calibrated on 2014–2025 from residuals, and calibration and Brier scores are reported. Upset probability for older seasons comes from pre-game Elo.
- **Engine** (`engine/`): one Python SET UDF on the contract below (D16), numpy-vectorized across the sims of a batch; batches run in parallel across cores and nodes.
- **CFB_PRED:**
  - `REFRESH_WEEK` freezes the predictions for the upcoming week before its games are played. Grading always compares against predictions made in advance.
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

- **`CFB_META.LOAD_LOG` ([5])**: `(TABLE_NAME, SLICE_KEY, IS_FINAL, ROWS_LOADED, LOADED_AT, LOADED_BY)`, PK (`TABLE_NAME`, `SLICE_KEY`). A slice is served from `CFB_CORE.<T>` iff a final row exists (a stored empty slice is a hit at 0 calls); stored rows of a non-final or unregistered slice are never returned. Matching is exact on the canonical slice key, no subsumption (a stored season does not serve a week). `IS_FINAL = FALSE` lets `REFRESH_WEEK` re-fetch the current week without a bypass switch. `CFB_CORE` mirrors have the same columns, order and types as the VS, so `INSERT INTO CFB_CORE.T SELECT * FROM CFBD_VS.T` works. Changing an explicit default changes the keys; the catalog diff shows it and the loader must migrate.
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
- The integration tests run on `TARGET=local` only (the mock sidecar needs the DB container's network namespace). `make deploy`, `grants`, `dns-check` and `record` accept `TARGET=aws`.
- The earlier S0 spike ran on a retired docker 2026.1.0 container, so every platform fact above was re-probed on Personal 2026.2 before the pushdown SQL shape was fixed ([15]).

## Engine contract

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
| `CFB_APP` | `SELECT` on `CFB_API` (+ execute on its functions) | Backend |
| `CFB_MCP` | `SELECT` on `CFB_API` | MCP server |

Verified:
- **Script-bound access works** (the `TEST_` namespace keeps a dedicated owner `CFB_TEST_OWNER` with `GRANT ACCESS ON CONNECTION <c> FOR SCRIPT <fetch> TO <owner>`, so this stays tested). A different script of the same owner cannot read the key.
- **Readers of the virtual schema need no connection privilege** (the pushdown runs with the owner's rights, probe 1.4g). They cannot call the fetch UDF, `LOAD_LOG`, the core tables or the connection directly; they may run `EXPLAIN VIRTUAL`.
- **The key doesn't appear in pushdown SQL or `EXPLAIN VIRTUAL`.** VS properties hold only `CONNECTION_NAME`. Responses and error messages are redacted ([23]): the key's raw, JSON-escaped and percent-encoded forms in bodies, plus any `Bearer <token>` in error text.
- **`CREATE OR REPLACE` of the fetch UDF drops a script-bound grant, even for identical text** (probe 1.4o). Deploy therefore compares the stored body with the generated one and replaces only when they differ. This matters for the `TEST_` owner (re-run `make grants ARGS="--prefix TEST_"` after a replace); the real deployment is owned by `sys`, which needs no grant.
- **UDF egress and DNS:** UDFs have no name resolution on Personal nano 2026.2, so the fetch UDF uses the resolver seam with the DoH fallback (D21, above).

Rules:
- The DBA creates the CONNECTION with plain SQL in a SQL client (D18; documented in `docs/user-guide.md`). There is no key tool; never pass the key on a shell command line.
- Rotate with `ALTER CONNECTION`.
- Never grant `ACCESS ANY CONNECTION`.
- **Trade-off of [20] (sys ownership):** `sys` can read every connection, so "only `CFBD_FETCH` reads the key" holds for readers (`CFB_READER`, `CFB_APP`, `CFB_MCP`), not for the owner. Accepted for a demo on a local Personal database: a separate owner would add a password and a grants step without demo value. A deployment that needs the stronger guarantee uses a prefixed namespace with its own owner, as the integration tests do.

## Design questions (Phase 0 spikes)

- **Resolved (D21): UDF DNS on Exasol Personal nano.** UDFs have no name resolution (empty resolv.conf and hosts). `CFBD_FETCH` resolves through a seam: system resolver first, DNS-over-HTTPS fallback (configurable property, default `https://1.1.1.1/dns-query`). TLS keeps verifying the hostname. Verified with `/info` on 2026-10-21.

- **Resolved ([18]): fan-out shape.** One `SET … EMITS` group over a `VALUES` slice list plus a thread pool of `MAX_PARALLEL_FETCHES` (default 6), not `GROUP BY` slice and not a request table. The pool width also bounds concurrent CFBD calls (rate-limit friendly). See [CFBD virtual schema contract](#cfbd-virtual-schema-contract).
- **Resolved ([6], [21]): fetch-log mechanism.** `REQ_*` columns on every row plus a JSON manifest (with the `sources` key) in the pushdown SQL; consumers persist to `CFB_META.FETCH_LOG` with `vs/host/fetchlog.py` (`capture_fetch_log`). No UDF writes anything.
- **Resolved: flattening edge cases.** Object-root = one row, scalar root = `VALUE`, over-500-field objects curated + `RAW_JSON`, wide stats tables not pivoted (a `CFB_MODEL` concern).
- **Resolved (D20): `SIM_PATH` layout and per-game score storage at 1M sims.** Layout C (wide BOOLEAN), sparse `SIM_TEAM`, loader-built `LEVERAGE`, no per-sim margins (`docs/spikes/s4-sim-storage.md`). Still open for Phase 5: the conditional rooting guide (k = 3) measures about 2.2 s p95 at 1M against the < 1 s target.
- **Resolved (D19): Python engine throughput.** The vectorized chain runs 100k sims in about 7 s (local, compute only) and 12.4 s end to end on AWS; locally 55.4 s median, dominated by row emission. `GROUP BY batch_id` fans out to `nrOfCores` instances per node (`docs/spikes/s3-python-engine-throughput.md`).
- **Resolved ([24], probe 1.4k): replay recordings.** One file per real request path at `/buckets/bfsdefault/default/cfbd/replay/<endpoint segments>/<sha256(slice)[:24]>.cfbd.json`, storing attempts; recorded by `scripts/record.py` under the call ledger, gitignored.
- **Open, accepted limitation (probe 1.4e): subquery on an optional `P_` column.** Returns 0 rows without an error because Exasol never pushes it down; documented in the contract and the user guide, not detectable by the adapter.
- **CFP rules:** G6 auto-bid wording, configurable.
- **Key masking:** is `CREATE CONNECTION` statement text masked in auditing and profiling tables? Check before shared or AWS use.
