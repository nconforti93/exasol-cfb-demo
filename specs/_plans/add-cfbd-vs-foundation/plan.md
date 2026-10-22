# Plan: add-cfbd-vs-foundation

## Summary

Build the first production-grade slice of the CFBD Virtual Schema (roadmap spikes S1 + S2). It consists of an OpenAPI-driven generator that turns all 93 endpoints into virtual tables, a Python adapter and fetch UDF on Exasol Personal that turn SQL into budgeted CFBD REST calls (pushdown, fan-out, guardrails, read-through to stored slices, fetch log), and a test harness that verifies all of it at zero API calls. The plan ends with a measured live run of at most 50 CFBD calls.

## Design

### Context

The mission's first promise is "the API is a schema": 93 REST endpoints become SQL tables without hand-written mapping, and every SQL query shows the HTTP it became. Four forces shape the design.

- **Quota.** The demo must run on the free tier (1,000 calls/month, D13). A careless `SELECT *` must not be able to burn the month. Data already persisted must never be paid for twice (D15).
- **The spec is incomplete.** Most requirement rules, hidden defaults, row caps and tier gates are in prose only (`docs/cfbd-api-reference.md`, D14). Responses nest deeply: arrays of objects, 1,919-leaf preview objects.
- **Exasol constraints.**
  - The adapter is stateless and cannot run SQL.
  - UDFs cannot write tables.
  - Only one script may read the key, because the grant is script-bound (D2, D18).
  - `CREATE OR REPLACE` drops that grant.
  - Aggregate pushdown over `UNION ALL` crashed 2026.1.0. That may not apply to 2026.2, but the no-aggregate-pushdown rule stays and 2026.2 is verified.
- **Runtime (D17).** Exasol Personal local (nano 2026.2.0-nano.3, podman), 127.0.0.1:8563, builtin PYTHON3 = Python 3.12.3, 20 CPUs.
  - BucketFS is a host directory, reconciled every second, with no HTTP port exposed.
  - **UDFs have no name resolution of their own.** Python UDFs run in a sandbox (`nschroot`) whose `/etc/resolv.conf` and `/etc/hosts` are empty, on any network. TCP egress to IP addresses works, and there is no DNS setting in `exasol.conf`. The podman `use-vc` workaround does not apply, because the nano image has no shell.
  - **Approved workaround (D21):** the system resolver is tried first, with a DNS-over-HTTPS fallback (`https://1.1.1.1/dns-query`, configurable) on `gaierror`. Verified 2026-10-21: `/info` returned HTTP 200 from a UDF, with the certificate verified against the host name.
  - The user created the CFBD connection `CFBD_API` there with plain SQL (D18).

**Goals**
- Generate a reviewed, deterministic catalog for all 93 GET operations (exclusions only with a listed reason). It covers child tables for arrays of objects, JSON columns for arrays of scalars, curated subsets for huge objects, and machine-readable rules from `vs/catalog-overrides.yaml`.
- A Python adapter that:
  - intersects predicates per parameter;
  - short-circuits contradictions at zero calls;
  - enforces hard and conditional rules and tier gating, naming the rule;
  - expands ranges and `IN` lists;
  - enforces `MAX_CALLS_PER_QUERY`;
  - emits read-through SQL that serves final stored slices from `CFB_CORE` and fetches only the rest.
- One fetch UDF (the only reader of the key):
  - live mode with retries and key redaction;
  - replay mode from BucketFS;
  - raw output for recording;
  - per-row request metadata for the fetch log.
- The contracts the Phase 2 loader builds on: the `CFB_META.LOAD_LOG` slice key, the `CFB_CORE` mirror DDL generated from the same catalog, and the `CFB_META.FETCH_LOG` capture.
- Verification without assumptions, at zero CFBD calls:
  - Exasol does not invoke the UDF for slices that `NOT EXISTS` removes.
  - Stored and live types match.
  - Aggregates over the read-through `UNION ALL` don't crash.
  - Readers need no privileges on storage or the connection.
- Make targets `test`, `test-it`, `lint`, `coverage`, `generate`, `deploy`, `grants`, `dns-check` and `record`. No connection target (D18), and no DNS "fix" target.
- Name resolution for the fetch UDF goes through one resolver seam implementing D21: system resolver first, DNS-over-HTTPS fallback on `gaierror`, a per-UDF-instance cache, and the endpoint set by the property `DOH_URL` (`off` disables the fallback). Everything before the live tasks works without name resolution: the generator, catalog, overrides, adapter pushdown, read-through, replay mode and all tests (the mock API is reached by IP address). The one exception is the network-marked DoH test, which needs HTTPS egress to the DoH endpoint but makes no CFBD call.
- A live finale of at most 50 CFBD calls: sizes and defaults measured, demo budget re-confirmed, replay recordings captured. It is gated on `make dns-check` passing on the target runtime.

**Non-Goals**
- The loader: `BACKFILL`, `REFRESH_WEEK`, nightly `PROMOTE()`, the load plan, snapshots (Phase 2). This plan defines only the contracts and tables they write to.
- Slice subsumption, where a stored season slice would serve a week query. Read-through matches canonical slice keys exactly.
- Fetching several tables from one response (parent and child tables cost one request each when fetched live).
- Aggregate, projection, `LIMIT` or join pushdown.
- `CFB_API` views, app, inspector UI, chat (Phases 5–7).
- A key tool. Users create `CFBD_API` with SQL (D18).
- Any Rust (D16).
- BucketFS HTTP upload for non-Personal deployments (AWS, Phase 8).
- Masking of `CREATE CONNECTION` text in audit tables (an open question in architecture.md, untouched here).
- Name-resolution options beyond D21's system-first + DoH fallback, such as pinned IP addresses or an Exasol-side sandbox fix. The fallback is removed once nano fixes the sandbox.

### Decision

A generator compiles spec plus overrides into one catalog JSON. A bundler embeds the stdlib-only runtime package `vs/cfbd_vs` and that catalog into two scripts: the adapter (`CFB_INFRA.CFBD_ADAPTER`) and a single dynamic-output fetch UDF (`CFB_INFRA.CFBD_FETCH … EMITS (...)`) that serves every table. The pushdown SQL reads the load log, which the adapter cannot do itself.

#### Architecture

```
 vs/openapi/cfbd-openapi-5.33.0.json ─┐
 vs/catalog-overrides.yaml (reviewed) ─┼─▶ vs/generate.py ─▶ vs/catalog/catalog.json (committed)
 vs/gen/exasol_reserved_keywords.txt ──┘                  └▶ sql/core/cfb_core_mirrors.sql (committed)
                                                                  │
            vs/cfbd_vs/*.py (stdlib only) + catalog ──▶ vs/bundle.py ──▶ build/sql/{cfbd_adapter,cfbd_fetch}.sql
                                                                  │
 make deploy (sys, [20]) ─▶ CFB_INFRA.CFBD_ADAPTER · CFB_INFRA.CFBD_FETCH · CFB_META.{LOAD_LOG,FETCH_LOG} · CFB_CORE.<T> · CFBD_VS
 make grants (sys)        ─▶ CREATE ROLE CFB_READER; GRANT SELECT ON CFBD_VS TO CFB_READER   (no connection grant: sys owns the UDF)
 TEST_ namespace          ─▶ CFB_TEST_OWNER owns it; GRANT ACCESS ON CONNECTION CFBD_API_MOCK FOR SCRIPT TEST_CFB_INFRA.CFBD_FETCH TO CFB_TEST_OWNER

 SELECT … FROM CFBD_VS.T WHERE …
      │ pushdown request (filter JSON, properties)
      ▼
 CFBD_ADAPTER: parse filter ─▶ split param vs residual predicates ─▶ intersect per P_ column
               ─▶ contradiction? ──yes──▶ SELECT CAST(NULL AS …) … FROM DUAL WHERE FALSE   (0 calls)
               ─▶ rules + gating (reject, name rule) ─▶ expand to slice keys ─▶ budget ─▶ SQL + manifest
      │
      ▼ pushdown SQL (executed by Exasol with the VS owner's rights)
 /*CFBD {manifest}*/
 SELECT * FROM (
   SELECT <cols>, 'STORED' … FROM CFB_CORE.T                                   ── stored branch
    WHERE REQ_SLICE IN (<slices>) AND REQ_SLICE IN (SELECT SLICE_KEY FROM CFB_META.LOAD_LOG
                                                    WHERE TABLE_NAME='T' AND IS_FINAL)
   UNION ALL
   SELECT CFB_INFRA.CFBD_FETCH('T', R.SLICE_KEY, '<catalog hash>', '<mode>', '<conn>', '<replay path>', '<doh url>')
          EMITS (<cols typed from catalog>)                                    ── fetch branch
     FROM (VALUES (<slice 1>), …) AS R(SLICE_KEY)
    WHERE NOT EXISTS (SELECT 1 FROM CFB_META.LOAD_LOG L
                       WHERE L.TABLE_NAME='T' AND L.SLICE_KEY=R.SLICE_KEY AND L.IS_FINAL)
 ) WHERE <residual predicates>
      │
      ▼
 CFBD_FETCH: live  ─▶ exa.get_connection(<conn>) ─▶ resolver seam(host: system, else DoH) ─▶ GET <address><slice>
                      (TLS SNI/cert = host name; Bearer; retry; redact) ───────────────────────────▶ flatten ─▶ rows + REQ_*
             replay ─▶ <REPLAY_PATH>/…/<sha256(slice)[:24]>.cfbd.json ─────────────────────────────▶ flatten ─▶ rows + REQ_*
             raw   ─▶ (owner only, recording) body parts + status/ms/attempts
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Spec + reviewed overrides → generated catalog | `vs/generate.py`, `vs/gen/*` | No hand mapping. Prose rules become machine-readable and reviewable. A spec bump is regenerate, diff, commit. The prose lint and unknown-reference checks catch drift |
| Echo parameter columns (`P_*`) | catalog, adapter, fetch UDF | Uniform for all 93 endpoints, with no per-pair review of which parameter equals which response field. Rows always satisfy the parameter predicates, so the residual stays correct. Avoids reserved words (`P_LIMIT`) |
| Single dynamic-output fetch UDF | `CFBD_FETCH(...) EMITS (...)` | The connection grant is per script, so one script must serve all tables. The `EMITS` clause comes from the same catalog types as the mirrors, so stored and live types match by construction |
| Catalog embedded in both scripts + hash handshake | `vs/bundle.py`, `fetch.py` | Stateless and atomic. The pushdown SQL stays readable for the inspector (table name + slice key, no flatten plan). A mismatch fails loudly instead of mis-flattening |
| Plan-time guardrails | `constraints.py`, `rules.py`, `plan.py` | Every rejection happens before any request and is visible in `EXPLAIN VIRTUAL` at zero calls |
| Advertise more predicates than can be pushed | `getCapabilities` | Exasol would otherwise evaluate unadvertised parameter predicates locally against NULL echo columns and return silently empty results. Seeing them lets the adapter reject them with a clear message |
| Full residual re-application in the pushdown SQL | `pushdown_sql.py` | Correctness never depends on the API's filtering semantics. Response-column filters work everywhere |
| Read-through via `UNION ALL` + `NOT EXISTS` against the load log | pushdown SQL | The adapter cannot run SQL. The decision happens at execution time, per slice, exactly once |
| Request metadata columns + EXPLAIN manifest | `REQ_*`, `/*CFBD {…}*/` | UDFs cannot write tables. The log travels with rows (actuals) and with the plan (zero-row requests). Consumers persist it |
| Owner-only raw output for recording | fetch UDF raw mode, `scripts/record.py` | The key never leaves Exasol. Recordings are the raw body, so every table of an endpoint replays from one recording |
| Test namespace via schema properties | `TEST_CFB_*`, `CFBD_API_MOCK` | Integration tests run beside the real deployment, never touch stored data or `CFBD_API`, and make zero CFBD calls |
| Dependency-injected transport, clock and filesystem | `http.py`, `replay.py`, `fetch.py` | Unit tests for retry, redaction and replay without DB or network |
| Resolver seam with DoH fallback (D21) | `vs/cfbd_vs/resolver.py`, installed by `fetch.py` and the `dns-check` UDF | The nano UDF sandbox has no DNS. Wrapping `socket.getaddrinfo` (system first, DoH on `gaierror`, cached) leaves `http.client` unchanged, so SNI and certificate checks stay on the host name. Removing the workaround later touches one module |

#### Key Interfaces

**Catalog (`vs/catalog/catalog.json`, sketch)**

```json
{"catalog_hash": "sha256…", "spec_version": "5.33.0", "generator_version": "1",
 "excluded": [{"path": "/…", "reason": "…"}],
 "tables": [{"name": "RANKINGS_POLLS", "endpoint": "/rankings", "kind": "child", "parent": "RANKINGS",
   "array_path": ["polls"], "hoisted": false, "read_through": true, "gated_by": null, "row_cap": null,
   "params": [{"name": "year", "column": "P_YEAR", "in": "query", "type": "integer", "sql_type": "DECIMAL(18,0)",
               "enum": null, "default": null, "description": "…"}],
   "columns": [{"name": "ROW_NO", "kind": "position"}, {"name": "POLLS_NO", "kind": "position"},
               {"name": "SEASON", "kind": "parent_key", "path": ["season"], "sql_type": "DECIMAL(18,0)"},
               {"name": "POLL", "kind": "value", "path": ["poll"], "sql_type": "VARCHAR(2000) UTF8", "comment": "…"},
               {"name": "P_YEAR", "kind": "param"}, {"name": "REQ_SLICE", "kind": "meta"}],
   "rules": [{"id": "rankings.required_year", "type": "required", "params": ["year"], "message": "…"}]}]}
```

Column order per table: position columns, parent keys, response columns (spec order), `RAW_JSON` (curated tables only), `P_*` (spec order), `REQ_*`.

**Overrides (`vs/catalog-overrides.yaml`, sketch)**

```yaml
endpoints:
  /games:
    defaults: {seasonType: both}            # sent and echoed when unfiltered
    verify: [classification-omission-means-all]   # S1 live check
  /games/teams:
    hoist: {array: teams, carry: {id: GAME_ID}}
    rules:
      - {id: year_unless_id, type: one_of, params: [year, id]}
      - {id: week_team_or_conference_with_year, type: requires_any, if: year, any_of: [week, team, conference]}
  /roster:
    rules: [{id: year_required, type: required, params: [year], why: "API silently defaults year to 2025"}]
  /plays/stats:
    row_cap: {rows: 2000, complete_with: [gameId]}
  /games/weather: {gated_by: weather}
  /games/{gameId}/preview: {curated: [id, homeTeam, …], raw_json: true}
  /info: {read_through: false}
children:
  RANKINGS_POLLS: {parent_keys: [season, seasonType, week]}
acknowledged:                               # prose matched by the lint, no rule needed
  - {endpoint: /plays, param: team, why: "describes semantics, not a rule"}
exclude: []                                 # every entry needs `reason`
```

**Fetch UDF**

`CFB_INFRA.CFBD_FETCH(TABLE_NAME VARCHAR(128), SLICE_KEY VARCHAR(2000), CATALOG_HASH VARCHAR(64), FETCH_MODE VARCHAR(16), CONNECTION_NAME VARCHAR(128), REPLAY_PATH VARCHAR(1024), DOH_URL VARCHAR(1024), MAX_PARALLEL_FETCHES DECIMAL(2,0)) EMITS (...)` ([18]: a `SET` script, called once per query with no `GROUP BY`)

- `TABLE_NAME = '@RAW'` selects the raw recording output: `SLICE_KEY, PART_NO, PART_COUNT, BODY_PART VARCHAR(2000000), HTTP_STATUS, ATTEMPTS, MS, REQUESTED_AT`.
- Otherwise the UDF emits exactly the columns `exa.meta` declares, in catalog order.
- Retries: at most 4 attempts, waits of about 1/2/4 s ±20 % jitter, `Retry-After` honored up to 30 s, 30 s per-attempt timeout.
- Name resolution failures, including DoH failures, are not retried. They raise a clear error that names the host, the `DOH_URL` value and the reason, and points to `make dns-check`.
- Resolver seam (D21): `vs/cfbd_vs/resolver.py` exposes `install(doh_url)`, which wraps `socket.getaddrinfo` for the UDF process, and `resolve(host, port) -> list[str]`.
  1. Call the original `getaddrinfo`. On success, return it unchanged (IP literals always succeed, so the mock API needs no DoH).
  2. On `socket.gaierror`, and if `DOH_URL` is not `off`, send `GET <DOH_URL>?name=<host>&type=A` with `Accept: application/dns-json` over verified TLS. `DOH_URL`'s host is an IP literal, so the DoH request itself never recurses.
  3. Take the A records from `Answer` (type 1), cache them per UDF instance, and call the original `getaddrinfo` with the IP.
  4. With `DOH_URL='off'`, re-raise the original `gaierror` with a message saying that the fallback is off.
  - The DoH request carries only the host name. `http.client` keeps `server_hostname=<host>`, so certificate verification is unchanged.
  - Pinned IPs are not supported.
  - Only `resolver.py` touches `socket.getaddrinfo`/`gethostbyname`; a unit test enforces this.

**Slice key grammar** (= `REQ_SLICE` = `LOAD_LOG.SLICE_KEY` = the request path sent):

```
slice_key := path ( "?" param ( "&" param )* )?      ; params sorted by API name, only params actually sent
path      := catalog path with path params substituted   e.g. /games/401628374/preview
param     := name "=" pct-encode(value)              ; integer: plain digits; boolean: true|false;
                                                     ; string: RFC 3986 unreserved kept, all else %XX (space = %20)
```

Changing an explicit default changes the slice keys of that endpoint. The loader must then rewrite the stored keys; the catalog diff makes this visible.

**Meta tables (`sql/meta/*.sql`)**

```sql
CREATE TABLE CFB_META.LOAD_LOG (
  TABLE_NAME VARCHAR(128) UTF8 NOT NULL, SLICE_KEY VARCHAR(2000) UTF8 NOT NULL,
  IS_FINAL BOOLEAN NOT NULL, ROWS_LOADED DECIMAL(18,0) NOT NULL,
  LOADED_AT TIMESTAMP NOT NULL, LOADED_BY VARCHAR(64) UTF8 NOT NULL,
  CONSTRAINT LOAD_LOG_PK PRIMARY KEY (TABLE_NAME, SLICE_KEY));
CREATE TABLE CFB_META.FETCH_LOG (
  LOGGED_AT TIMESTAMP NOT NULL, CONTEXT VARCHAR(200) UTF8, TABLE_NAME VARCHAR(128) UTF8 NOT NULL,
  SLICE_KEY VARCHAR(2000) UTF8 NOT NULL, REQ_SOURCE VARCHAR(8) UTF8 NOT NULL, HTTP_STATUS DECIMAL(3,0),
  ATTEMPTS DECIMAL(3,0), MS DECIMAL(9,0), ROWS_RETURNED DECIMAL(18,0) NOT NULL, REQUESTED_AT TIMESTAMP);
```

**Replay recording**

- File: `<REPLAY_PATH>/<endpoint path segments>/<sha256(slice_key)[:24]>.cfbd.json` containing `{"slice_key", "status", "ms", "requested_at", "body"}`.
- On Personal, BucketFS is the host directory `~/.exasol/personal/deployments/local/local/runtime/exa/bucketfs`, reconciled into buckets every second. The platform probe (task 1.4) fixes the directory-to-UDF-path mapping and the default `REPLAY_PATH`.
- `.cfbd.json` and `cache/` are gitignored.

**Environment** (no secrets in files)

- DSN `127.0.0.1:8563`.
- `sys` (DBA) password read at run time from `~/.exasol/personal/deployments/local/secrets.json` (key `dbPassword`). `sys` creates and owns the real deployment ([20]); there is no `CFB_OWNER` user and no owner password. Readers get the role `CFB_READER`.
- The `TEST_` namespace keeps a generated owner `CFB_TEST_OWNER` (its password is generated by the harness, or `CFB_TEST_OWNER_PASSWORD` for `make grants ARGS="--prefix TEST_"`), so the script-bound grant model stays tested.
- The CFBD key is only in the `CFBD_API` connection, created by the user (`docs/user-guide.md`).

**Live budget (this plan, ≤ 50 CFBD calls; `/info` is free, verified 2026-10-21)**

| Step | Calls |
|---|---|
| 9.2 `/info` re-check on Personal, `API_FEATURES` sync | 0 |
| 9.3 S1 access patterns (`vs/live/s1-patterns.yaml`, one per pattern) | ≤ 26 |
| 9.5a fan-out `P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8,9)` on `GAMES` | 6 |
| 9.5b stored slice queried live | 0 |
| 9.5c one missing slice queried live | 1 |
| Reserve (retries, one re-run) | ≤ 17 |
| **Hard cap (enforced by the ledger `cache/live-calls.jsonl`)** | **50** |

S1 patterns (draft, finalized in 9.3):
1. `/games?year=2024` with nothing else, to check the defaults
2. `/games?seasonType=both&year=2024`
3. `/games?classification=fbs&seasonType=both&year=2024`
4. `/games?seasonType=both&year=1950`
5. `/games?seasonType=both&week=8&year=2024`
6. `/metrics/wp/pregame?seasonType=both&year=2024`
7. `/ratings/sp?year=2024`
8. `/rankings?seasonType=both&year=2024`
9. `/conferences/affiliations?maxYear=2026&minYear=1869`
10. `/teams`
11. `/venues`
12. `/calendar?year=2026`
13. `/games/schedule`
14. `/teams/matchup?team1=Michigan&team2=Ohio%20State`
15. `/games/{id}/preview`
16. `/games/teams?seasonType=both&week=8&year=2024`
17. `/games/players?seasonType=both&week=8&year=2024`
18. `/plays?week=8&year=2024`
19. `/drives?year=2024`
20. `/plays/stats?gameId=…`
21. `/stats/season?year=2024`
22. `/stats/season?classification=fcs&year=2024`
23. `/passing/teams/season?year=2024`
24. `/teams/season/overview?team=Michigan&year=2024`
25. `/game/box/advanced?id=…`
26. `/lines?year=2024`

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Parameters are separate `P_` echo columns | Bind parameters to response fields with the same name (`WEEK`), reviewed per endpoint | About 150 parameter/field pairs need review, some differ semantically (conference abbreviation vs. name), and a wrong binding silently drops rows. Echo is uniform and always correct. Binding can be added later as sugar |
| One fetch UDF with dynamic `EMITS`, catalog embedded | One UDF per endpoint; catalog in BucketFS; flatten plan passed in the SQL | The per-script grant rules out many UDFs. BucketFS couples live mode to file sync. A flatten plan in the SQL clutters the inspector. Cost: every catalog change replaces the UDF, so `make grants` follows (deploy replaces the UDF only when its text changed) |
| Advertise `OR`/`NOT`/`<>`/`LIKE`/`IS NULL` in addition to `=`/`IN`/ranges/`AND` | Advertise only `=`/`IN`/ranges/`AND` (interview wording) | Unadvertised parameter predicates would be evaluated by Exasol against NULL echo values and return silently empty results. Advertised, they are rejected with a message. Still no aggregate pushdown, so the interview's intent (no crash path) holds. Accepted by the user 2026-10-21 |
| No projection or `LIMIT` capability | Advertise `SELECTLIST_PROJECTION`/`LIMIT` | Follows the interview decision (filters only). Exasol projects locally. The emit cost is negligible next to HTTP |
| Budget counts planned requests (stored included); stored-only queries (`REQ_SOURCE = 'STORED'`) skip it | Count only live slices at run time inside the UDF | The adapter cannot see `LOAD_LOG`. The plan-time ceiling is safe and visible in `EXPLAIN`. Broad reads of stored data belong in `CFB_CORE`, or use the stored-only filter |
| Exact slice-key match for read-through | Subsumption (a stored season serves a week query) | Exact match is simple and provable. Subsumption needs parameter-to-field equivalences, which this plan rejected (echo design) |
| Gating via `API_FEATURES` property, fail-closed, synced from free `/info` by deploy | Fetch UDF checks `/info` at run time | Matches the adapter pipeline (rejection at plan time, visible in `EXPLAIN`). The UDF also maps 401/403 to a clear error |
| Contradiction checked before rules | Rules first | The interview fixed this order. A contradiction makes zero calls, so it is never harmful |
| Stored branch passes the original `REQ_AT`/`REQ_ROWS` and overrides source/status/ms | Keep stored metadata as fetched | The inspector must show "stored, 0 calls", while the original fetch time stays visible |
| Integration tests: synthetic recordings + mock CFBD server + `TEST_` namespace | Real recordings, or tests on the real namespace | CFBD terms (no redistribution; the repo goes public), zero quota, no risk to stored data or `CFBD_API` |
| Strict flattening (fail on overflow or type mismatch) | Truncate or coerce | No silent data changes. S1's live run and the all-endpoint synthetic sweep surface catalog fixes early |
| Reserved-word columns get a trailing `_` | Always-quoted identifiers | Users and the MCP chat write plain SQL. The original field name stays in the comment |
| `/plays/stats` requires `gameId` (row cap 2,000) | Allow capped queries | Complete results are guaranteed. The reference doc already classes it as on-demand per game |
| Resolver seam: system first, DoH fallback via `DOH_URL` (default `https://1.1.1.1/dns-query`, `off` disables), per-instance cache; `make dns-check` gates the live tasks | podman `use-vc` (not possible on nano: no shell, empty sandbox `resolv.conf`); pinned IPs (CDN IPs change); DoH always | Approved by the user as D21 and verified live (`/info` 200). System-first keeps the workaround inert once nano fixes the sandbox. Restricting `DOH_URL` to an IP host avoids a resolution loop |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| cfbd-vs/endpoint-catalog | NEW | `cfbd-vs/endpoint-catalog/spec.md` |
| cfbd-vs/flattening | NEW | `cfbd-vs/flattening/spec.md` |
| cfbd-vs/pushdown | NEW | `cfbd-vs/pushdown/spec.md` |
| cfbd-vs/requirement-rules | NEW | `cfbd-vs/requirement-rules/spec.md` |
| cfbd-vs/gating | NEW | `cfbd-vs/gating/spec.md` |
| cfbd-vs/fan-out | NEW | `cfbd-vs/fan-out/spec.md` |
| cfbd-vs/read-through | NEW | `cfbd-vs/read-through/spec.md` |
| cfbd-vs/fetch-log | NEW | `cfbd-vs/fetch-log/spec.md` |
| cfbd-vs/fetch-modes | NEW | `cfbd-vs/fetch-modes/spec.md` |
| cfbd-vs/key-protection | NEW | `cfbd-vs/key-protection/spec.md` |
| cfbd-vs/properties | NEW | `cfbd-vs/properties/spec.md` |

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls outside group 9 | Unit, CLI and integration tests use synthetic data, the mock API and replay. `make test-it` refuses to run if any test virtual schema has `CONNECTION_NAME = 'CFBD_API'` |
| No DNS dependency outside group 9 | Generator, catalog, deploy, adapter, read-through, replay and all tests work without UDF name resolution. The mock API's connection address is an IP literal. The one exception is the DoH-success integration test (pytest marker `network`), which needs HTTPS egress to the DoH endpoint and a TLS handshake with `api.collegefootballdata.com`, but sends no HTTP request and makes no CFBD call. Deploy leaves `API_FEATURES` empty (fail closed) when `/info` is unreachable |
| Live cap | At most 50 CFBD calls for the whole plan. Every live call goes through `scripts/record.py` or a documented query, and is appended to `cache/live-calls.jsonl`. The tool prints the planned calls, requires `CONFIRM=yes`, reads `/info` before and after (free), and stops before exceeding the cap |
| No CFBD data in git | Recordings, measurements with raw values, and caches stay in `cache/` and BucketFS. `docs/cfbd-api-measurements.md` holds only derived numbers (rows, bytes, ms, observed defaults). Unit fixtures are synthetic |
| No secrets in files | The CFBD key is only in `CFBD_API`. The sys password is read at run time from `~/.exasol/personal/deployments/local/secrets.json`. There is no owner password ([20]). Test connections carry a generated marker key |
| Runtime code is stdlib-only | `vs/cfbd_vs` imports only the Python 3.12 standard library, because it is bundled into the builtin PYTHON3 SLC (3.12.3). Host-only code (`vs/gen`, `scripts`, tests) may use PyYAML and pyexasol |
| Target runtime | Exasol Personal local, deployment `local`, 127.0.0.1:8563, nano 2026.2. Lifecycle only via `exasol stop/start -d local`, never `exakit` |
| Performance (measured, not gated) | An on-demand single-slice VS lookup should take under 2 s (mission). Recorded in 9.6 |
| Shared DB etiquette | Don't run `make test-it` during the timed S3/S4 windows of `add-engine-spikes` on the same Personal DB |

## Dependencies

- Exasol Personal local running (`exasol start -d local`) with builtin PYTHON3, podman, and the Personal secrets file `~/.exasol/personal/deployments/local/secrets.json`.
- Python tooling via `uv` (`pyproject.toml`): pytest, pytest-cov, ruff, mypy, PyYAML, types-PyYAML, pyexasol 2.x (`websocket_sslopt={"cert_reqs": ssl.CERT_NONE}`).
- Shared files with the parallel plan `add-engine-spikes`:
  - It keeps its own `spikes/Makefile` and leaves the root Makefile to this plan.
  - Both plans append to `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md`. `add-engine-spikes` reserves D19/D20 and the user's DoH decision is D21, so this plan's decisions start at **D22**.
- **User prerequisites before group 9 (live):**
  - U1: `CFBD_API` exists on Personal. The user created it with the SQL in `docs/user-guide.md` (D18), and the implementation verifies it via `EXA_DBA_CONNECTIONS`. Claude never handles the key.
  - U2: run `make grants` after `make deploy` (as sys; it only creates the reader role `CFB_READER` and its `SELECT` on `CFBD_VS`, [20]).
  - U3: `make dns-check` passes on Personal through the resolver seam with the DoH fallback (task 9.0, D21). If the network blocks 1.1.1.1, the user points `DOH_URL` at another DoH service reachable by IP.
  - U4: approve the live run (`CONFIRM=yes`).

## Migration

Greenfield, so no data migration. Notes:
- The docker container `cfb-exa` (9571) is retired (D17). The privilege model verified there on 2026.1.0 (S0) is re-verified on Personal 2026.2 by the key-protection integration tests.
- `.env.example` points `EXASOL_DSN` at `127.0.0.1:8563`.

## Implementation Tasks

Tasks are TDD: each code task writes its unit tests first (`tests/unit/…`), per `/speq:code-guardrails`. Integration tests per feature live in group 7.

1. **Toolchain, safety and platform probe (no CFBD calls)**
   - [ ] 1.1 Scaffold the Python project:
     - `pyproject.toml` (uv; Python 3.12; dev deps above; ruff and mypy config).
     - Package dirs `vs/cfbd_vs/` (runtime, stdlib-only), `vs/gen/` (generator), `vs/host/` (host-side helpers), `scripts/`, `tests/{unit,cli,it,synthetic,mock_cfbd}/`.
     - `.gitignore` += `build/`. `.env.example`: `EXASOL_DSN=127.0.0.1:8563`; no key variables and no owner password ([20]).
   - [ ] 1.2 Root `Makefile` with the targets `generate`, `test` (unit + cli), `test-it`, `lint` (ruff check, ruff format --check, mypy), `coverage`, `deploy`, `grants`, `dns-check`, `record`:
     - Quote paths, because the repo path has spaces (pass `CURDIR` explicitly if a target breaks).
     - Every DB target first checks reachability, with the hint `exasol start -d local`.
     - No Rust and no connection target (D16, D18).
   - [ ] 1.3 No action. `docs/spikes/connection_privileges_test.py` was deleted by the user on 2026-10-21; the key-protection integration tests re-verify S0 on Personal. Kept as a numbered placeholder so the task references stay stable. Only `specs/architecture.md`'s S0 reference needs repointing (task 8.2).
   - [ ] 1.4 Platform probe `docs/spikes/s2_platform_probe.py` with findings in `docs/spikes/s2-platform-probe.md`, run as `sys` on Personal. It uses only `PROBE_*` objects and a probe connection with a dummy key, and makes zero CFBD calls. **Gate:** if (a) fails, stop and escalate. Each finding records the fallback the plan adopts if it is negative. [expert]
     - (a) A PYTHON3 ADAPTER SCRIPT plus `CREATE VIRTUAL SCHEMA` works on nano 2026.2.
     - (b) SLC Python version and stdlib (`urllib`, `ssl`, `json`, `hashlib`).
     - (c) A dynamic `EMITS(...)` UDF inside pushdown SQL.
     - (d) `FROM (VALUES …) AS R(…)` syntax.
     - (e) With the planned capability set, Exasol pushes supported conjuncts and evaluates unsupported ones locally.
     - (f) A row removed by `NOT EXISTS` never reaches the UDF (the UDF raises on a marker row).
     - (g) The pushdown runs with the VS owner's rights for tables and scripts (a reader without privileges on the probe schemas).
     - (h) `COUNT(*)`/`SUM`/`GROUP BY` over a VS whose pushdown is a table `UNION ALL` a UDF does not crash on 2026.2.
     - (i) A column `comment` in VS metadata shows in `EXA_ALL_COLUMNS`.
     - (j) A leading `/*…*/` survives in `EXPLAIN VIRTUAL` `PUSHDOWN_SQL` (fallback: manifest as a string literal in a one-row CTE).
     - (k) BucketFS on Personal: how a host directory under `runtime/exa/bucketfs` maps to a UDF path, the reconcile delay, the default `REPLAY_PATH`.
     - (l) The UDF can reach a host-side HTTP server **by IP address** (podman gateway or host IP, or a sidecar container's IP), because the sandbox cannot resolve names, `host.containers.internal` included.
     - (m) Concurrency of a scalar `EMITS` UDF over a 6-row `VALUES` with 20 CPUs, vs. `SET … EMITS` + `GROUP BY` (fallback if scalar serializes: switch `CFBD_FETCH` to SET + `GROUP BY` slice and record the decision).
     - (n) `CREATE SCRIPT` with a body of about 2 MB.
     - (o) `CREATE OR REPLACE` drops the script-bound grant on 2026.2.
     - (p) Snapshot `EXA_SQL_KEYWORDS WHERE RESERVED` into `vs/gen/exasol_reserved_keywords.txt` (metadata only).
     - (q) Record the UDF sandbox's name resolution state: the content of `/etc/resolv.conf` and `/etc/hosts` as seen by a Python UDF, `getaddrinfo('api.collegefootballdata.com', 443)`, and TCP connects to an IP on 443. This only confirms the known finding; the D21 fallback is built in 4.2.
   - [ ] 1.5 `scripts/dns.py`, `make dns-check`: create, as `sys`, a tiny UDF in `CFB_TOOLS`. It uses the bundled resolver seam (`vs/cfbd_vs/resolver.py`, same `DOH_URL` default) to resolve `api.collegefootballdata.com`, then opens a TLS connection to port 443 with certificate verification against the host name, without sending an HTTP request (no quota). It reports ok/fail, which path resolved (system or DoH), the A records and the sandbox `resolv.conf` state, and exits non-zero on failure. No `podman exec`. (Before 4.2 lands, it runs with the system resolver only.)

2. **Catalog generator**
   - [ ] 2.1 Catalog model and rules in `vs/cfbd_vs/catalog.py` (dataclasses, stable JSON, catalog hash) and `vs/cfbd_vs/naming.py`: table names from paths, camelCase → `UPPER_SNAKE`, `P_`/`REQ_`/position names, reserved-word `_` suffix, SQL type mapping.
   - [ ] 2.2 OpenAPI schema walker `vs/gen/schema_walk.py`:
     - Resolve `$ref`, single-branch `allOf`, nullable `oneOf`/`anyOf`, and `nullable`.
     - Flatten leaves to `PARENT_CHILD`.
     - Classify arrays of objects vs. arrays of scalars, free-form/variant objects → JSON, `date-time` → TIMESTAMP.
     - Guard against cycles and unbounded depth. [expert]
   - [ ] 2.3 Table derivation `vs/gen/tables.py`:
     - Endpoint and child tables per array level, with `ROW_NO`/`<ARRAY>_NO`, declared parent keys and explicit hoisting.
     - Curated tables with `RAW_JSON` and no children; fail on more than 500 fields without a curated entry.
     - `P_` and `REQ_` columns.
     - Detect collisions (table names, columns), apply override renames.
     - Comments from the spec description plus rule text and gating. [expert]
   - [ ] 2.4 Overrides schema, loader and validator `vs/gen/overrides.py`:
     - Rule types `required`, `one_of`, `requires_any`, `excludes`, `requires_value`, `bounds`, `default`, `row_cap`.
     - Entry types `gated_by`, `exclude` (reason mandatory), `curated`, `hoist`, `parent_keys`, `type`/`size`/`rename`, `read_through`, `acknowledged`, `verify`.
     - Every unknown endpoint, parameter or field is an error; all errors are listed.
   - [ ] 2.5 Prose-rule lint `vs/gen/prose_lint.py`: match the rule phrases (spec scenario list) in operation and parameter descriptions, and fail on matches that no rule or `acknowledged` entry covers.
   - [ ] 2.6 Seed `vs/catalog-overrides.yaml` from `docs/cfbd-api-reference.md`:
     - Every endpoint with conditional rules, defaults, caps or gating (about 40), until the prose lint passes.
     - `seasonType` default `both` where accepted; `classification` defaults made explicit where the spec states `fbs`.
     - `/roster` `year` required; `/ratings/elo` `week` handled; `/recruiting/groups` years; `/info/usage` bounds.
     - `/plays/stats` row cap with `gameId`.
     - Curated subsets for `/games/{gameId}/preview` and `/teams/season/overview`.
     - Hoist `/games/teams` and `/games/players`; parent keys for the main child tables.
     - Read-through off for `/info`, `/info/usage`, `/games/schedule`, `/scoreboard`, `/live/plays`.
     - Gating per the gating spec.
     - `verify:` markers for S1 (e.g. whether omitting `classification` on `/games` means all divisions).
   - [ ] 2.7 Generator CLI `vs/generate.py` plus mirror DDL renderer `vs/gen/mirrors.py`:
     - Writes `vs/catalog/catalog.json` and `sql/core/cfb_core_mirrors.sql` deterministically, all-or-nothing (temp file, then rename).
     - Exits non-zero with all errors listed.
     - Wired to `make generate`. The first generated outputs are added to the change set.

3. **Adapter core (stdlib-only)**
   - [ ] 3.1 Filter parser `vs/cfbd_vs/filters.py`: Exasol pushdown filter JSON → predicate AST for the advertised predicates and literal types. Columns are resolved to their catalog kind (param, response, position, meta).
   - [ ] 3.2 Parameter constraint solver `vs/cfbd_vs/constraints.py`. [expert]
     - Split parameter and residual predicates.
     - Build per-parameter value sets and integer intervals, intersect them, tighten integer bounds.
     - Treat a same-parameter `OR` as a union.
     - Detect contradictions.
     - Reject, with exact messages:
       - open ranges;
       - an `OR` across parameters or mixing a parameter with a response column;
       - unsupported forms on `P_` columns;
       - non-literal values;
       - enum violations;
       - type mismatches.
   - [ ] 3.3 Rules engine `vs/cfbd_vs/rules.py`:
     - Evaluate the rule types and gating (`API_FEATURES`) per table against the solved constraints and every expanded request.
     - Apply explicit defaults.
     - Error format `CFBD_VS.<TABLE>: rule <id>: …`.
   - [ ] 3.4 Request planner `vs/cfbd_vs/plan.py`: cartesian expansion, dedup, canonical slice keys (grammar above), budget check, manifest JSON.
   - [ ] 3.5 SQL rendering `vs/cfbd_vs/sqlrender.py`. Injection-proof, with property tests over hostile strings: [expert]
     - identifier quoting;
     - literal escaping for every literal type;
     - residual predicate rendering;
     - typed `EMITS` clause and zero-row `SELECT … FROM DUAL WHERE FALSE` from catalog types.
   - [ ] 3.6 Pushdown SQL builder `vs/cfbd_vs/pushdown_sql.py`. Its shape is final only after the 1.4 findings (c–j, m) are in. [expert]
     - zero-row, live-only, and read-through SQL (stored branch filtered to final `LOAD_LOG` slices `UNION ALL` fetch branch over `VALUES` with `NOT EXISTS`);
     - stored-only pruning when `REQ_SOURCE` excludes `LIVE`/`REPLAY`;
     - volatile tables and `READ_THROUGH=FALSE`;
     - schema names from properties;
     - manifest header.
   - [ ] 3.7 Adapter entry `vs/cfbd_vs/adapter.py`:
     - `createVirtualSchema`/`refresh`: metadata with comments from the catalog.
     - `setProperties`: validation, defaults, unknown property, previous value kept on error.
     - `dropVirtualSchema`.
     - `getCapabilities`: exactly the advertised set.
     - `pushdown`: the pipeline in the fixed order.

4. **Fetch UDF core (stdlib-only)**
   - [ ] 4.1 Flattener `vs/cfbd_vs/flatten.py`. [expert]
     - Object or array root; nested paths; child explosion with position columns and parent keys; hoisting.
     - JSON columns; curated subset plus `RAW_JSON`.
     - ISO-8601 → UTC TIMESTAMP.
     - Strict type and size checks with errors naming table, column and value type.
   - [ ] 4.2 HTTP client `vs/cfbd_vs/http.py` and resolver seam `vs/cfbd_vs/resolver.py`:
     - `GET <address><slice>` with Bearer, injected transport and clock.
     - Resolver seam per D21 (Key Interfaces): system first, DoH fallback on `gaierror` with `Accept: application/dns-json`, A records cached per UDF instance, verified TLS for the DoH request, `DOH_URL` (`off` disables). TLS SNI, certificate verification and the `Host` header of the API connection stay on the host name.
     - Resolution and DoH errors become non-retried errors naming the host, the `DOH_URL` and the reason, pointing to `make dns-check`.
     - Unit tests with injected DoH transport cover: system hit (no DoH call), fallback, cache hit, DoH HTTP/TLS/empty-answer failures, `off`.
     - A unit test asserts that no other module calls `socket.getaddrinfo` or `gethostbyname`.
     - The retry policy, with attempts and ms accounting.
     - 401/403 → access error; other 4xx raised immediately with the API's message.
     - Key redaction (the key and `Bearer …`) in every message and in the response body before parsing.
   - [ ] 4.3 Replay store `vs/cfbd_vs/replay.py`: recording format and path layout (1.4k), part reassembly, a missing-recording error naming the slice and path.
   - [ ] 4.4 Fetch entry `vs/cfbd_vs/fetch.py`:
     - Argument contract and catalog-hash handshake.
     - Mode dispatch (live/replay/`@RAW`); emit the columns `exa.meta` declares, including `REQ_*`.
     - Raw parts of at most 2,000,000 characters.
     - A clear access error when `exa.get_connection` is denied.

5. **Bundling, SQL infra, deploy, grants**
   - [ ] 5.1 Bundler `vs/bundle.py`:
     - Amalgamate `vs/cfbd_vs` plus the catalog into the adapter and fetch script bodies, with a `sys.modules` shim, deterministically, into `build/sql/`.
     - Unit test: the bundle runs in a clean `python3 -I` with only the stdlib.
   - [ ] 5.2 Meta DDL `sql/meta/load_log.sql` and `sql/meta/fetch_log.sql` per Key Interfaces.
   - [ ] 5.3 Deploy `scripts/deploy.py` plus `vs/host/deploy.py`, run as `sys` ([20]); `deploy(connection, namespace)` takes any connection, so the `TEST_` harness deploys as `CFB_TEST_OWNER`:
     - Optional `--prefix` for test namespaces.
     - Create schemas, meta tables and mirrors. Existing mirrors must match the catalog; otherwise fail with the column diff and do not alter them.
     - `CREATE OR REPLACE` the adapter. Replace the fetch UDF only if its text changed, which keeps the grant.
     - Create or refresh the VS and set properties.
     - If the fetch UDF can read the connection and `/info` is reachable, sync `API_FEATURES` from `CFBD_VS.INFO` (free). Otherwise leave it as it is (empty fails closed) and print the reason: `make dns-check`, or the connection is missing. With `sys` as owner the grant is no longer the usual cause.
   - [ ] 5.4 Grants `scripts/grants.py`, run as `sys` (password from `~/.exasol/personal/deployments/local/secrets.json` at run time; applied by [20]):
     - Real namespace: create the role `CFB_READER` if missing, and `GRANT SELECT ON CFBD_VS TO CFB_READER` once the virtual schema exists. No owner user, no owner password, no connection grant (`sys` owns the fetch UDF and can read every connection).
     - `TEST_` namespace: create `CFB_TEST_OWNER` if missing, with system privileges (CREATE SESSION, SCHEMA, TABLE, VIEW, SCRIPT, VIRTUAL SCHEMA), and `GRANT ACCESS ON CONNECTION CFBD_API_MOCK FOR SCRIPT TEST_CFB_INFRA.CFBD_FETCH TO CFB_TEST_OWNER` once the script exists.
     - Namespace-aware, idempotent, never `ACCESS ANY CONNECTION`.
   - [ ] 5.5 Fetch-log capture helper `vs/host/fetchlog.py`: staged table + manifest → `CFB_META.FETCH_LOG` rows, one per planned slice, including zero-row requests.

6. **Integration test harness (zero CFBD calls)**
   - [ ] 6.1 Synthetic response generator `tests/synthetic/fake.py`:
     - Deterministic, schema-driven responses for every endpoint: 2 elements per array, typed values, timestamps with offsets.
     - Hand-tuned fixtures for games, rankings, games/teams, matchup and the curated preview.
     - Helpers: oversized and invalid-type variants, and a minimal rule-satisfying filter per table derived from the catalog.
   - [ ] 6.2 Mock CFBD server `tests/mock_cfbd/server.py`:
     - Serves synthetic responses by path and query.
     - Scripted 429 (with `Retry-After`), 5xx, 400, 401/403, latency injection, >2 MB bodies.
     - Request log with headers; key-echo mode.
     - Runs where probe 1.4l says the UDF can reach it. `CFBD_API_MOCK` addresses it by IP literal (`http://<ip>:<port>`).
     - A mock DoH endpoint over HTTPS with a self-signed certificate that logs every connection. It proves "system resolver used → no DoH connection", and that a DoH certificate that fails verification yields the clear DoH error.
   - [ ] 6.3 IT harness `tests/it/conftest.py`:
     - Deploy into the `TEST_` namespace (`TEST_CFB_INFRA`, `TEST_CFBD_VS`, `TEST_CFB_META`, `TEST_CFB_CORE`).
     - Users `CFB_TEST_OWNER`/`CFB_TEST_READER` with in-memory random passwords.
     - Connection `CFBD_API_MOCK` with a marker key, plus the script-bound grant.
     - Write synthetic recordings into the test replay path and wait for reconcile.
     - Helpers: query, EXPLAIN manifest, mock hit counts, property set/reset, stage-and-register slices.
     - A hard guard that no test references `CFBD_API`.

7. **Integration tests per feature** (scenario mapping in Verification)
   - [ ] 7.1 `tests/it/test_catalog_it.py`, `tests/cli/test_generate_cli.py`, `tests/it/test_flattening_it.py` (including the all-table replay sweep).
   - [ ] 7.2 `tests/it/test_pushdown_it.py`, `tests/it/test_rules_it.py`, `tests/it/test_gating_it.py`, `tests/it/test_fanout_it.py`, `tests/it/test_properties_it.py`.
   - [ ] 7.3 `tests/it/test_read_through_it.py`. [expert]
     - Prove "UDF not invoked" two ways: no recording or mock response exists for stored slices, and the mock hit count is 0.
     - Type identity for all read-through tables.
     - Reader without storage privileges.
     - Aggregates over both branches.
   - [ ] 7.4 `tests/it/test_fetch_log_it.py`, `tests/it/test_fetch_modes_it.py`, `tests/it/test_key_protection_it.py`.
   - [ ] 7.5 Quality gate: `make lint` clean, and `make coverage` reaches at least 90 % line coverage for `vs/cfbd_vs` and `vs/gen` from unit and CLI tests.

8. **Documentation (no CFBD calls)**
   - [ ] 8.1 `docs/user-guide.md`:
     - Deploy and grants order (`make deploy` → `make grants` for the reader role; `/info` is read by `make deploy` itself, so run it again after creating `CFBD_API` or fixing `make dns-check`).
     - The promised `SELECT * FROM CFBD_VS.INFO` check.
     - Keep the DNS section (already describes the D21 DoH fallback) consistent with the implementation: the property name `DOH_URL`, the value `off`, the IP-host rule, and `make dns-check` as the check.
     - Querying the VS: `P_` columns, rules, budget, `EXPLAIN VIRTUAL`, `REQ_*`.
     - Replay mode and the properties table.
   - [ ] 8.2 `specs/architecture.md`:
     - Resolve the open questions on fan-out shape, fetch-log mechanism, flattening edge cases and replay layout.
     - Document the `P_`/`REQ_` columns, the `LOAD_LOG` contract and the properties.
     - Change the runtime references to Personal 2026.2. Keep the no-aggregate-pushdown rule and state the result of probe 1.4h.
     - Document the resolver seam and the D21 DoH fallback, and repoint the S0 security-model reference from the deleted spike script to `tests/it/test_key_protection_it.py`.
   - [ ] 8.3 `docs/roadmap.md`: decisions D22+ from the decision-log entries marked `Promotes to ADR: yes` (D21 is the user's DoH decision); the S2 example uses `P_YEAR`/`P_WEEK`; mark S1/S2 progress.
   - [ ] 8.4 `CLAUDE.md` dense bullets: test tiers (unit/cli/it, mock + replay, `TEST_` namespace, zero calls), toolchain (uv, make targets), the deploy → grants rule, the live ledger. `specs/mission.md` § Commands with the real targets (no cargo).

9. **Live (last; ≤ 50 CFBD calls in total; needs U1–U4)**
   - [ ] 9.0 Gate: `make dns-check` passes on Exasol Personal local through the resolver seam (system first, DoH fallback, D21). It reports the DoH path and a TLS handshake with `api.collegefootballdata.com` whose certificate verified, at 0 CFBD calls. If 1.1.1.1 is blocked on the current network, set `DOH_URL` to another DoH service reachable by IP. 9.1–9.6 run only after this passes; 9.1 can be built and unit-tested earlier.
   - [ ] 9.1 Recorder `scripts/record.py` (`make record PATTERNS=… CONFIRM=yes`), run as `sys` ([20]):
     - Read a patterns file, print the planned calls and the ledger total, and refuse without `CONFIRM=yes` or when the ledger would exceed 50.
     - Read `/info` before and after (free).
     - Fetch through the `@RAW` output; write recordings to `cache/replay/` and the Personal BucketFS directory.
     - Append each call to `cache/live-calls.jsonl`.
     - Write measurements (status, attempts, ms, body bytes, rows per catalog table via local flattening) to `cache/measurements/`.
   - [ ] 9.2 Real deployment on Personal: `make deploy`, U1 verified, user U2, `make dns-check` passes (U3), `make deploy` again. `CFBD_VS.INFO` shows tier, remaining calls and features; `API_FEATURES` is synced (expected empty). 0 calls.
   - [ ] 9.3 S1 measurement (≤ 26 calls) with `vs/live/s1-patterns.yaml`:
     - one call per access pattern (Key Interfaces);
     - whether `/games` returns a full season in one call;
     - the `seasonType` and `classification` defaults;
     - payload sizes for preview, overview, plays and box scores;
     - all responses recorded.
   - [ ] 9.4 Apply the findings:
     - Update the overrides: verified defaults (resolve every `verify:` marker), `VARCHAR` sizes from the observed maximum lengths, curated subsets.
     - Run `make generate`, re-run `make test` and `make test-it`.
     - Run a replay sweep over the real recordings (0 calls) to validate flattening on real payloads.
   - [ ] 9.5 S2 live verification (≤ 7 calls; record everything):
     - (a) `GAMES` with `P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8,9)`: 6 calls, with concurrency from `REQ_AT`/`REQ_MS`.
     - (b) Register one recorded slice as stored (stage from replay, 0 calls), switch to live, query it: `/info` `remainingCalls` is unchanged.
     - (c) Query one missing slice live: `remainingCalls` drops by exactly 1.
   - [ ] 9.6 Live documentation:
     - `docs/cfbd-api-measurements.md` (derived numbers only).
     - Verified defaults and sizes in `docs/cfbd-api-reference.md`.
     - Demo budget re-confirmed in `docs/roadmap.md` (demo profile about 200 calls, refresh about 5 per week).
     - The on-demand lookup latency against the 2 s target.
     - The final ledger total (≤ 50).

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1 |
| Group B | 1.2, 1.3, 1.4, 1.5, 2.1 |
| Group C | 2.2, 2.4, 2.5, 3.1, 3.3, 3.4, 4.2, 4.3, 6.2 |
| Group D | 2.3, 3.2, 3.5, 4.1 |
| Group E | 2.6, 2.7, 3.6, 3.7, 4.4, 6.1 |
| Group F | 5.1, 5.2, 5.3, 5.4, 5.5 |
| Group G | 6.3 |
| Group H | 7.1, 7.2, 7.3, 7.4 |
| Group I | 7.5, 8.1, 8.2, 8.3, 8.4 |
| Group J (sequential, gated) | 9.0 (dns-check gate) → 9.1 → 9.2 → 9.3 → 9.4 → 9.5 → 9.6 |

Sequential dependencies:
- Group A → Group B → Group C → Group D → Group E → Group F → Group G → Group H → Group I → Group J.
- 2.3 needs 2.2. 2.6 needs 2.4 and 2.5. 2.7 needs 2.3, 2.4 and 2.6.
- 3.2 needs 3.1. 3.3 needs 2.4 (rule schema). 3.6 needs 3.2, 3.4, 3.5 and the 1.4 findings. 3.7 needs 3.6.
- 4.1 needs 2.3 (table plans). 4.4 needs 4.1, 4.2 and 4.3.
- 6.1 needs 2.7 (catalog for minimal filters). 6.2 needs the 1.4l finding.
- 9.x need U1–U4 and a green Group I.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| Spike script (already removed) | `docs/spikes/connection_privileges_test.py` | Deleted by the user on 2026-10-21; nothing left to remove. `tests/it/test_key_protection_it.py` re-verifies S0 on Personal 2026.2. Remaining work: repoint the S0 reference in `specs/architecture.md` (task 8.2) |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| endpoint-catalog: Every GET operation becomes a virtual table | Integration | `tests/it/test_catalog_it.py` | `test_every_get_operation_is_a_table` |
| endpoint-catalog: Invalid overrides stop generation | Integration (CLI) | `tests/cli/test_generate_cli.py` | `test_invalid_overrides_stop_generation` |
| endpoint-catalog: Prose-only rules must be covered by an override | Integration (CLI) | `tests/cli/test_generate_cli.py` | `test_uncovered_prose_rules_stop_generation` |
| endpoint-catalog: Generation is deterministic and the committed catalog is current | Integration (CLI) | `tests/cli/test_generate_cli.py` | `test_generation_deterministic_and_committed_current` |
| endpoint-catalog: Table names derive from the endpoint path | Integration | `tests/it/test_catalog_it.py`, `tests/cli/test_generate_cli.py` | `test_table_names_derive_from_path`, `test_colliding_table_names_stop_generation` |
| endpoint-catalog: Every API parameter becomes a typed parameter column | Integration | `tests/it/test_catalog_it.py` | `test_parameters_become_typed_p_columns` |
| endpoint-catalog: Columns carry the API documentation and the rules | Integration | `tests/it/test_catalog_it.py` | `test_column_comments_carry_docs_and_rules` |
| endpoint-catalog: Column name collisions stop generation | Integration (CLI) | `tests/cli/test_generate_cli.py` | `test_column_collisions_stop_generation` |
| endpoint-catalog: Reserved words never become column names | Integration | `tests/it/test_catalog_it.py` | `test_reserved_word_columns_get_trailing_underscore` |
| endpoint-catalog: Refreshing the virtual schema reproduces the same tables | Integration | `tests/it/test_catalog_it.py` | `test_refresh_reproduces_metadata` |
| endpoint-catalog: Adapter and fetch UDF from different catalog versions are refused | Integration | `tests/it/test_catalog_it.py` | `test_catalog_version_mismatch_refused` |
| flattening: Nested objects become prefixed columns | Integration | `tests/it/test_flattening_it.py` | `test_nested_objects_become_prefixed_columns` |
| flattening: Arrays of objects become child tables | Integration | `tests/it/test_flattening_it.py` | `test_arrays_of_objects_become_child_tables` |
| flattening: Child rows join to exactly one parent row | Integration | `tests/it/test_flattening_it.py` | `test_child_rows_join_exactly_one_parent` |
| flattening: Hoisted endpoints expose the array elements directly | Integration | `tests/it/test_flattening_it.py` | `test_hoisted_endpoint_exposes_array_elements` |
| flattening: Arrays of scalars become JSON text | Integration | `tests/it/test_flattening_it.py` | `test_scalar_arrays_become_json_text` |
| flattening: Very large objects keep a curated subset plus the raw JSON | Integration | `tests/it/test_flattening_it.py` | `test_large_objects_curated_plus_raw_json` |
| flattening: Date-time values become UTC timestamps | Integration | `tests/it/test_flattening_it.py` | `test_date_time_values_become_utc_timestamps` |
| flattening: Missing and null fields become NULL | Integration | `tests/it/test_flattening_it.py` | `test_missing_and_null_fields_are_null` |
| flattening: Object-root responses become one row | Integration | `tests/it/test_flattening_it.py` | `test_object_root_response_is_one_row` |
| flattening: Empty responses return no rows | Integration | `tests/it/test_flattening_it.py` | `test_empty_response_returns_no_rows` |
| flattening: Values that do not fit their column fail loudly | Integration | `tests/it/test_flattening_it.py` | `test_values_that_do_not_fit_fail_loudly` |
| flattening: Every table returns typed rows for a schema-conforming response | Integration | `tests/it/test_flattening_it.py` | `test_all_tables_replay_sweep` |
| pushdown: Equality on a parameter column becomes a request parameter | Integration | `tests/it/test_pushdown_it.py` | `test_equality_becomes_request_parameter` |
| pushdown: Rows echo the parameters of their request | Integration | `tests/it/test_pushdown_it.py` | `test_rows_echo_request_parameters` |
| pushdown: Bounded integer ranges are enumerated | Integration | `tests/it/test_pushdown_it.py` | `test_bounded_ranges_are_enumerated` |
| pushdown: Open-ended ranges are rejected | Integration | `tests/it/test_pushdown_it.py` | `test_open_ranges_rejected` |
| pushdown: IN lists produce one request per value | Integration | `tests/it/test_pushdown_it.py` | `test_in_list_one_request_per_value` |
| pushdown: OR on one parameter acts like IN | Integration | `tests/it/test_pushdown_it.py` | `test_same_parameter_or_acts_like_in` |
| pushdown: OR across parameters is rejected | Integration | `tests/it/test_pushdown_it.py` | `test_cross_parameter_or_rejected` |
| pushdown: Unsupported predicates on parameter columns are rejected | Integration | `tests/it/test_pushdown_it.py` | `test_unsupported_parameter_predicates_rejected` |
| pushdown: Parameter values must be literals | Integration | `tests/it/test_pushdown_it.py` | `test_parameter_values_must_be_literals` |
| pushdown: Predicates on response columns filter rows without changing requests | Integration | `tests/it/test_pushdown_it.py` | `test_response_predicates_are_residual` |
| pushdown: Contradictory filters return no rows and make no calls | Integration | `tests/it/test_pushdown_it.py` | `test_contradictions_return_no_rows_no_calls` |
| pushdown: Contradictions are decided before requirement rules | Integration | `tests/it/test_pushdown_it.py` | `test_contradiction_before_rules` |
| pushdown: Enum parameter values are validated | Integration | `tests/it/test_pushdown_it.py` | `test_enum_values_validated` |
| pushdown: Parameter values are passed safely | Integration | `tests/it/test_pushdown_it.py` | `test_parameter_values_passed_safely` |
| pushdown: Aggregates over a virtual table are computed by Exasol | Integration | `tests/it/test_pushdown_it.py` | `test_aggregates_computed_by_exasol` |
| requirement-rules: A missing hard-required parameter is rejected | Integration | `tests/it/test_rules_it.py` | `test_missing_hard_required_rejected` |
| requirement-rules: A "required unless" rule accepts either parameter | Integration | `tests/it/test_rules_it.py` | `test_required_unless_accepts_either` |
| requirement-rules: A conditional requirement applies when its trigger is set | Integration | `tests/it/test_rules_it.py` | `test_conditional_requirement_with_trigger` |
| requirement-rules: Mutually exclusive parameters are rejected | Integration | `tests/it/test_rules_it.py` | `test_mutually_exclusive_rejected` |
| requirement-rules: A value-dependent requirement is enforced | Integration | `tests/it/test_rules_it.py` | `test_value_dependent_requirement` |
| requirement-rules: Parameter bounds are enforced | Integration | `tests/it/test_rules_it.py` | `test_parameter_bounds_enforced` |
| requirement-rules: Hidden defaults are sent explicitly | Integration | `tests/it/test_rules_it.py` | `test_hidden_defaults_sent_explicitly` |
| requirement-rules: A hidden default can become a requirement | Integration | `tests/it/test_rules_it.py` | `test_hidden_default_becomes_requirement` |
| requirement-rules: Row-capped endpoints require their completeness parameter | Integration | `tests/it/test_rules_it.py` | `test_row_capped_requires_completeness_param` |
| requirement-rules: Child tables enforce the rules of their endpoint | Integration | `tests/it/test_rules_it.py` | `test_child_tables_enforce_endpoint_rules` |
| requirement-rules: Rule violations are visible in EXPLAIN VIRTUAL at zero calls | Integration | `tests/it/test_rules_it.py` | `test_rule_violation_in_explain_zero_calls` |
| gating: Gated endpoints exist as documented tables | Integration | `tests/it/test_gating_it.py` | `test_gated_tables_exist_documented` |
| gating: A gated table refuses queries when the key lacks the feature | Integration | `tests/it/test_gating_it.py` | `test_gated_refused_without_feature` |
| gating: A gated table works when the key has the feature | Integration | `tests/it/test_gating_it.py` | `test_gated_allowed_with_feature` |
| gating: Gating fails closed while features are unknown | Integration | `tests/it/test_gating_it.py` | `test_gating_fails_closed` |
| gating: An access refusal from the API is reported clearly | Integration | `tests/it/test_gating_it.py` | `test_api_access_refusal_reported` |
| fan-out: A range and an IN list fan out into their product | Integration | `tests/it/test_fanout_it.py` | `test_range_and_in_fan_out_product` |
| fan-out: Duplicate values are requested once | Integration | `tests/it/test_fanout_it.py` | `test_duplicate_values_requested_once` |
| fan-out: An over-budget query is rejected before any call | Integration | `tests/it/test_fanout_it.py` | `test_over_budget_rejected` |
| fan-out: A query exactly at the budget is allowed | Integration | `tests/it/test_fanout_it.py` | `test_at_budget_allowed` |
| fan-out: Stored slices count toward the budget | Integration | `tests/it/test_fanout_it.py` | `test_stored_slices_count_toward_budget` |
| fan-out: The budget applies to each virtual-table access | Integration | `tests/it/test_fanout_it.py` | `test_budget_per_table_access` |
| fan-out: The requests of one query run concurrently | Integration | `tests/it/test_fanout_it.py` | `test_requests_run_concurrently` |
| read-through: A stored slice costs zero calls | Integration | `tests/it/test_read_through_it.py` | `test_stored_slice_zero_calls` |
| read-through: A mixed query fetches only the missing slices | Integration | `tests/it/test_read_through_it.py` | `test_mixed_query_fetches_missing_only` |
| read-through: Only final, registered slices are served from storage | Integration | `tests/it/test_read_through_it.py` | `test_only_final_registered_slices_served` |
| read-through: A stored empty slice costs zero calls | Integration | `tests/it/test_read_through_it.py` | `test_stored_empty_slice_zero_calls` |
| read-through: Query results can be stored as slices directly | Integration | `tests/it/test_read_through_it.py` | `test_query_results_store_as_slices` |
| read-through: Stored and live columns are identical | Integration | `tests/it/test_read_through_it.py` | `test_stored_and_live_columns_identical` |
| read-through: Readers need no privileges on the stored data | Integration | `tests/it/test_read_through_it.py` | `test_reader_needs_no_storage_privileges` |
| read-through: Stored-only queries never call the API | Integration | `tests/it/test_read_through_it.py` | `test_stored_only_queries_never_call` |
| read-through: Read-through can be switched off | Integration | `tests/it/test_read_through_it.py` | `test_read_through_switch_off` |
| read-through: Volatile endpoints are always fetched | Integration | `tests/it/test_read_through_it.py` | `test_volatile_endpoints_always_fetched` |
| read-through: Aggregates over mixed results are correct | Integration | `tests/it/test_read_through_it.py` | `test_aggregates_over_mixed_results` |
| fetch-log: Every row carries its request metadata | Integration | `tests/it/test_fetch_log_it.py` | `test_rows_carry_request_metadata` |
| fetch-log: The slice key is exactly the request that was sent | Integration | `tests/it/test_fetch_log_it.py` | `test_slice_key_equals_sent_request` |
| fetch-log: EXPLAIN VIRTUAL shows the planned requests without calling the API | Integration | `tests/it/test_fetch_log_it.py` | `test_explain_manifest_no_calls` |
| fetch-log: A staged result is captured into the fetch log | Integration | `tests/it/test_fetch_log_it.py` | `test_staged_result_captured_into_fetch_log` |
| fetch-log: Stored rows report their origin | Integration | `tests/it/test_fetch_log_it.py` | `test_stored_rows_report_origin` |
| fetch-log: A stored-only query logs only the slices it read | Unit | `tests/unit/test_fetchlog.py` | `test_stored_only_query_logs_the_stored_slices_and_skips_the_ones_never_read` |
| fetch-modes: Live mode sends one authenticated GET per planned request | Integration | `tests/it/test_fetch_modes_it.py` | `test_live_one_authenticated_get_per_request` |
| fetch-modes: Rate limits and server errors are retried | Integration | `tests/it/test_fetch_modes_it.py` | `test_429_and_5xx_retried` |
| fetch-modes: Persistent failures fail the query after four attempts | Integration | `tests/it/test_fetch_modes_it.py` | `test_persistent_failure_four_attempts` |
| fetch-modes: Client errors are not retried | Integration | `tests/it/test_fetch_modes_it.py` | `test_client_errors_not_retried` |
| fetch-modes: The system resolver is used when it succeeds | Integration | `tests/it/test_fetch_modes_it.py` | `test_system_resolver_used_no_doh_call` |
| fetch-modes: DNS over HTTPS supplies the address when the system resolver fails | Integration (marker `network`, no CFBD call) | `tests/it/test_fetch_modes_it.py` | `test_doh_fallback_resolves_and_verifies_tls` |
| fetch-modes: DoH failures are reported clearly | Integration | `tests/it/test_fetch_modes_it.py` | `test_doh_failure_reported` |
| fetch-modes: The DoH fallback can be switched off | Integration | `tests/it/test_fetch_modes_it.py` | `test_doh_off_raises_original_error` |
| fetch-modes: Replay mode serves recordings without network or key | Integration | `tests/it/test_fetch_modes_it.py` | `test_replay_without_network_or_key` |
| fetch-modes: A missing recording fails clearly | Integration | `tests/it/test_fetch_modes_it.py` | `test_missing_recording_fails_clearly` |
| fetch-modes: A recorded response replays identically | Integration | `tests/it/test_fetch_modes_it.py` | `test_recording_replays_identically` |
| fetch-modes: Large responses are recorded in parts | Integration | `tests/it/test_fetch_modes_it.py` | `test_large_responses_recorded_in_parts` |
| fetch-modes: Switching the mode takes effect on the next query | Integration | `tests/it/test_fetch_modes_it.py` | `test_mode_switch_next_query` |
| key-protection: Only the fetch UDF can read the key | Integration | `tests/it/test_key_protection_it.py` | `test_only_fetch_udf_reads_key` |
| key-protection: Readers query without any connection privilege | Integration | `tests/it/test_key_protection_it.py` | `test_reader_queries_without_connection_privilege` |
| key-protection: The key never appears in SQL, metadata or results | Integration | `tests/it/test_key_protection_it.py` | `test_key_never_in_sql_metadata_results` |
| key-protection: The key is redacted from errors and response bodies | Integration | `tests/it/test_key_protection_it.py` | `test_key_redacted_from_errors_and_bodies` |
| key-protection: The key is only sent to its own connection's address | Integration | `tests/it/test_key_protection_it.py` | `test_key_only_sent_to_connection_address` |
| key-protection: A redeployed fetch UDF needs the grant again | Integration | `tests/it/test_key_protection_it.py` | `test_redeployed_fetch_udf_needs_grant` |
| properties: Defaults apply when properties are unset | Integration | `tests/it/test_properties_it.py` | `test_defaults_apply` |
| properties: Invalid property values are rejected | Integration | `tests/it/test_properties_it.py` | `test_invalid_property_values_rejected` |
| properties: Unknown properties are rejected | Integration | `tests/it/test_properties_it.py` | `test_unknown_property_rejected` |
| properties: Property changes apply to the next query without a refresh | Integration | `tests/it/test_properties_it.py` | `test_property_change_applies_next_query` |
| properties: Schema properties redirect storage and fetch objects | Integration | `tests/it/test_properties_it.py` | `test_schema_properties_redirect` |

Supporting unit tests (pure computation, no I/O) back the integration tests. They do not replace them:
- `tests/unit/test_naming_types.py`, `test_schema_walk.py`, `test_tables.py`, `test_overrides.py`, `test_prose_lint.py` (generator);
- `test_filters.py`, `test_constraints.py`, `test_rules.py`, `test_plan.py`, `test_sqlrender.py`, `test_pushdown_sql.py`, `test_adapter_entry.py` (adapter);
- `test_flatten.py`, `test_http.py`, `test_replay.py`, `test_fetch_entry.py`, `test_bundle.py` (fetch UDF and bundle).

### Manual Testing

All commands run from the repo root against Exasol Personal local (127.0.0.1:8563) after `make deploy` and `make grants`. Rows marked *(replay)* run after `ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE='replay'` with the group 9 recordings present, at 0 calls. Rows marked *(live, counted)* spend ledger calls.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| cfbd-vs/endpoint-catalog | `SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA='CFBD_VS'`, then `SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA='CFBD_VS' AND COLUMN_TABLE='GAMES_TEAMS' AND COLUMN_NAME LIKE 'P\_%' ESCAPE '\'` | Table count = endpoint tables + child tables in `catalog.json`. `P_` columns with types and comments naming the `year`/`id` and week/team/conference rules |
| cfbd-vs/flattening | *(replay)* `SELECT POLL, COUNT(*) FROM CFBD_VS.RANKINGS_POLLS_RANKS WHERE P_YEAR=2024 GROUP BY POLL` | One group per poll of the 2024 rankings recording (AP Top 25, Coaches, CFP …) |
| cfbd-vs/pushdown | `SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR=2024 AND P_YEAR=2025)` | Zero-row SQL (`… FROM DUAL WHERE FALSE`), no `CFBD_FETCH` call |
| cfbd-vs/requirement-rules | `SELECT * FROM CFBD_VS.PLAYS WHERE P_YEAR=2024` | Error `CFBD_VS.PLAYS: rule plays.…: … P_WEEK …`, `/info` remaining unchanged |
| cfbd-vs/gating | `SELECT * FROM CFBD_VS.GAMES_WEATHER WHERE P_YEAR=2024` | Error naming feature `weather` and `API_FEATURES` (the project key has no weather access) |
| cfbd-vs/fan-out | `SELECT PUSHDOWN_SQL FROM (EXPLAIN VIRTUAL SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8,9))`, then `SELECT * FROM CFBD_VS.GAMES WHERE P_YEAR BETWEEN 2000 AND 2024` | Manifest with 6 slice keys; the second statement fails: 25 planned, budget 10 |
| cfbd-vs/read-through | *(live, 0 counted)* after 9.5b: `SELECT REQ_SOURCE, COUNT(*) FROM CFBD_VS.GAMES WHERE P_YEAR=<stored year> GROUP BY REQ_SOURCE`, then `SELECT REMAINING_CALLS FROM CFBD_VS.INFO` | One group `STORED`; remaining calls unchanged |
| cfbd-vs/fetch-log | *(replay)* `SELECT DISTINCT REQ_SLICE, REQ_SOURCE, REQ_STATUS, REQ_MS, REQ_ROWS FROM CFBD_VS.GAMES WHERE P_YEAR=2024` | One line `/games?seasonType=both&year=2024`, `REPLAY`, 200, the recorded ms, the row count |
| cfbd-vs/fetch-modes | `ALTER VIRTUAL SCHEMA CFBD_VS SET FETCH_MODE='replay'`, then `SELECT COUNT(*) FROM CFBD_VS.GAMES WHERE P_YEAR=2024` with the network disconnected | Same count as the live recording; no network needed |
| cfbd-vs/key-protection | As a reader with only `SELECT ON CFBD_VS`: `SELECT * FROM EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES`, `SELECT CFB_INFRA.CFBD_FETCH('GAMES','/games','x','live','CFBD_API','')` | Properties without any key; the direct UDF call is denied |
| cfbd-vs/properties | `ALTER VIRTUAL SCHEMA CFBD_VS SET MAX_CALLS_PER_QUERY='0'` | Error naming `MAX_CALLS_PER_QUERY` and the range 1–1000; the value stays 10 |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0; committed catalog and mirror DDL current |
| Build | `uv run python vs/bundle.py` (also run by `make deploy`) | Exit 0; `build/sql/cfbd_adapter.sql` and `build/sql/cfbd_fetch.sql` written |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % line coverage for `vs/cfbd_vs` and `vs/gen` |
| Deploy | `make deploy && make grants && make deploy` | Exit 0; `SELECT * FROM CFBD_VS.INFO` returns tier, remaining calls and features |
| DNS (live gate) | `make dns-check` | Resolves `api.collegefootballdata.com` from a UDF (path `doh` on Personal nano) and completes a certificate-verified TLS handshake; 0 CFBD calls |
| Live ledger | `uv run python scripts/record.py --ledger` | Total CFBD calls for this plan ≤ 50 |
| Secrets | `git grep -n -I -e 'IDENTIFIED BY' -e 'Bearer ' -- . ':!docs/user-guide.md' ':!specs'` | No key material; only placeholders and documentation |
