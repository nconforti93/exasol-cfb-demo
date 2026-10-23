# Architecture Decision Records

<!-- ADRs are numbered sequentially starting from ADR-001. Never renumber. -->
<!-- recorder-agent appends new ADRs from plan decision logs. -->

---

## ADR-001: Parameters are exposed as separate `P_` echo columns

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

93 CFBD endpoints take about 150 query or path parameters, many of which overlap with response fields by name (`week` vs. `WEEK`). The virtual schema needs a uniform way to turn SQL predicates into API parameters.

### Decision

Every query or path parameter becomes a column `P_<PARAM>`. Each row carries the parameter values of the request that produced it, NULL if not sent; explicit defaults count as sent. Response fields keep their own columns, even when names overlap.

### Options Considered

| Option | Verdict |
|--------|---------|
| Separate `P_` echo columns | ✓ Chosen — uniform across 93 endpoints, no per-pair review, residual stays correct, sidesteps reserved words (`P_LIMIT`) |
| Bind parameters to same-named response fields | ✗ Rejected — about 150 pairs, several semantic mismatches (conference abbreviation vs. name, `team` meaning either side), a wrong binding silently drops rows |

### Consequences

Rows always satisfy their parameter predicates. Queries use `P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8,9)`. Users must learn that every `P_` column is an API knob.

---

## ADR-002: One dynamic-output fetch UDF serves every table; the catalog is embedded in both scripts

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

The connection grant that protects the API key is per script, and stored and live column types must match exactly.

### Decision

`CFB_INFRA.CFBD_FETCH(TABLE_NAME, SLICE_KEY, CATALOG_HASH, FETCH_MODE, CONNECTION_NAME, REPLAY_PATH) EMITS (...)`, with the `EMITS` clause written by the adapter from catalog types. The bundler embeds `vs/cfbd_vs` and the catalog into the adapter and the fetch UDF. A catalog-hash handshake refuses mixed deployments. Deploy replaces the fetch UDF only when its text changed.

### Options Considered

| Option | Verdict |
|--------|---------|
| One dynamic UDF, catalog embedded | ✓ Chosen — single key reader by construction, one type source, readable pushdown SQL |
| One UDF per endpoint | ✗ Rejected — the connection grant is per script |
| Catalog in BucketFS | ✗ Rejected — couples live mode to file sync |
| Flatten plan passed in the pushdown SQL | ✗ Rejected — clutters the inspector and `EXPLAIN` |

### Consequences

Adapter and UDF must be deployed together; a hash mismatch fails closed. Replacing the UDF drops its connection grant, so `make grants` must be re-run.

---

## ADR-003: Advertise extra predicate capabilities so untranslatable parameter predicates are rejected, not silently empty

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

If only `=`/`IN`/ranges/`AND` were advertised, Exasol would evaluate unadvertised predicates on `P_` columns locally against NULL echo values (for example `P_TEAM LIKE 'Mich%'` with `team` unsent) and return silently empty results.

### Decision

Advertise `FILTER_EXPRESSIONS` with `=`, `<>`, `<`, `<=`, `BETWEEN`, `IN`, `LIKE`, `IS [NOT] NULL`, `AND`, `OR`, `NOT` and the common literals. Translate only `=`/`IN`/bounded integer ranges/`AND`/same-parameter `OR` on `P_` columns and reject every other parameter predicate with a message. Apply predicates on non-parameter columns as a residual in the pushdown SQL. No aggregate, projection, `LIMIT`, ordering or join capability (avoids the 2026.1.0 `UNION ALL` aggregate crash path).

### Options Considered

| Option | Verdict |
|--------|---------|
| Advertise the wider set, translate a subset, reject the rest | ✓ Chosen — turns a silent-wrong-result class into explicit errors |
| Advertise only `=`/`IN`/ranges/`AND` | ✗ Rejected — silent empty results on unadvertised predicates |

### Consequences

Users get clear rejection messages for unsupported parameter predicates. Accepted by the user on 2026-10-21.

---

## ADR-004: The call budget counts planned requests, including stored slices; stored-only queries are exempt

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

The adapter cannot see `LOAD_LOG`, so at plan time it cannot know which slices are already stored.

### Decision

`MAX_CALLS_PER_QUERY` (default 10) is enforced at plan time on the number of planned requests per virtual-table access. A query restricted by `REQ_SOURCE = 'STORED'` emits no fetch branch and skips the budget.

### Options Considered

| Option | Verdict |
|--------|---------|
| Plan-time worst-case count, stored-only exempt | ✓ Chosen — safe, visible in `EXPLAIN VIRTUAL`, matches "never route a broad scan through the API" |
| Count only live slices at run time via a window count in the UDF | ✗ Rejected for v1 — more complex, rejection happens at execution, not at `EXPLAIN` |

### Consequences

Broad reads of stored data go through `CFB_CORE` or the stored-only filter. The budget applies per virtual-table access, because Exasol pushes each table separately.

---

## ADR-005: Read-through matches canonical slice keys exactly; `LOAD_LOG` contract

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

Read-through needs a rule for when stored data may substitute for a live fetch, without the adapter running SQL.

### Decision

A slice is (`TABLE_NAME`, `SLICE_KEY`); `SLICE_KEY` is the canonical request path plus sorted query string, equal to `REQ_SLICE`. `CFB_META.LOAD_LOG(TABLE_NAME, SLICE_KEY, IS_FINAL, ROWS_LOADED, LOADED_AT, LOADED_BY)` has PK (`TABLE_NAME`, `SLICE_KEY`). A slice is served from `CFB_CORE` iff a final `LOAD_LOG` row exists; stored rows of non-final or unregistered slices are never returned. Mirrors have identical columns.

### Options Considered

| Option | Verdict |
|--------|---------|
| Exact slice-key match with `IS_FINAL` | ✓ Chosen — simple, provable, exactly-once per slice; `REFRESH_WEEK` re-fetches without a bypass switch |
| Subsumption (a stored season serves a week query) | ✗ Deferred — needs parameter-to-field equivalences that ADR-001 avoids |
| Catalog hash per slice | ✗ Rejected — every overrides tweak would invalidate all stored slices and re-spend quota |

### Consequences

Changing an explicit default changes slice keys; the catalog diff shows it and the loader must migrate.

---

## ADR-006: Fetch log is request metadata columns plus an EXPLAIN manifest; consumers persist

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

UDFs cannot write tables, yet every query must show which HTTP requests it became and what each cost.

### Decision

Every table carries `REQ_SLICE`, `REQ_SOURCE`, `REQ_STATUS`, `REQ_ATTEMPTS`, `REQ_MS`, `REQ_ROWS`, `REQ_AT`. The pushdown SQL starts with a JSON manifest of the planned requests, so zero-row requests are visible too. `CFB_META.FETCH_LOG` is written by consumers (Phase 2 loader, Phase 6 backend) from a staged result plus the manifest, using the reference helper `vs/host/fetchlog.py`.

### Options Considered

| Option | Verdict |
|--------|---------|
| Metadata columns plus `EXPLAIN` manifest, consumers persist | ✓ Chosen — actuals travel with rows, plan with `EXPLAIN`, persistence stays with whoever stages the result |
| Marker row per request | ✗ Rejected — breaks SQL result semantics |
| UDF writes to BucketFS or HTTP | ✗ Rejected — needs another secret |
| Backend-only log | ✗ Rejected — cannot see in-database refreshes |

### Consequences

Every consumer that wants a persistent log must stage the result and call the helper.

---

## ADR-007: Child tables use 1-based position columns, declared parent keys, explicit hoisting and curated huge objects

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

Arrays of objects become child tables; heuristics for parent keys and hoisting would produce silent wrong joins.

### Decision

`ROW_NO` plus one `<ARRAY>_NO` per level; children join on `REQ_SLICE` plus position columns. Natural parent keys are declared in the overrides. Hoisting only where declared (`/games/teams`, `/games/players`). Endpoints over 500 flattened fields need a curated subset plus `RAW_JSON` and get no child tables.

### Options Considered

| Option | Verdict |
|--------|---------|
| Positional keys plus declared natural keys and hoisting | ✓ Chosen — a positional join always works; reviewed entries add usability |
| Infer parent keys and hoisting heuristically | ✗ Rejected — wrong guesses are silent |

### Consequences

Overrides must be maintained and reviewed when the API spec changes.

---

## ADR-008: Integration tests use a mock CFBD server, synthetic recordings and a `TEST_` namespace

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

CFBD terms forbid redistributing raw data (the repo goes public), quota is scarce, and tests must not endanger stored data or the real connection.

### Decision

Integration tests deploy into `TEST_CFB_*`/`TEST_CFBD_VS` (schema-name properties). Live mode is tested against a host-side mock API addressed by IP, through the connection `CFBD_API_MOCK` with a marker key. Replay is tested with schema-generated synthetic recordings. A guard refuses any test that touches `CFBD_API`.

### Options Considered

| Option | Verdict |
|--------|---------|
| Mock server, synthetic recordings, `TEST_` namespace | ✓ Chosen — `make test-it` costs zero calls and needs no DNS; retry, redaction and key-protection become testable |
| Real recordings in tests, or tests on the real namespace | ✗ Rejected — CFBD terms, quota, risk to stored data and the real connection |

### Consequences

Integration tests never prove behavior against the real API; live calls are a separate, capped step.

---

## ADR-009: Explicit hidden defaults, made-required defaults and the row-cap completeness rule

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

Silently relying on API defaults or capped responses produces incomplete results that look complete.

### Decision

Overrides turn each hidden default into an explicit sent-and-echoed default (`seasonType=both`, `classification=fbs` where the spec says so), a requirement (`/roster` `year`), or a verified "omission means all". `/plays/stats` requires `gameId` because of its 2,000-row cap. A prose lint fails generation for uncovered rule-like descriptions.

### Options Considered

| Option | Verdict |
|--------|---------|
| Explicit defaults, requirements and prose lint | ✓ Chosen — implements "never rely on defaults" mechanically and keeps it from drifting on spec bumps |
| Rely on API defaults, or allow capped queries | ✗ Rejected — silent incompleteness |

### Consequences

Generation fails on new rule-like API descriptions until the overrides cover them.

---

## ADR-010: Fan-out runs as one SET group with a bounded thread pool

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

Slices of one query must be fetched in parallel without depending on database load, and without exceeding rate limits.

### Decision

`CFBD_FETCH` is a `SET ... EMITS` UDF. The pushdown sends all slices of a query to one group (no `GROUP BY`), and the UDF fetches them through a thread pool whose width is the property `MAX_PARALLEL_FETCHES` (default 6, integer 1-16). Rows are emitted in input order.

### Options Considered

| Option | Verdict |
|--------|---------|
| SET group plus thread pool | ✓ Chosen — speed independent of DB load, code controls concurrent CFBD calls; `NOT EXISTS` guard and aggregates verified for this shape |
| Scalar `EMITS` | ✗ Rejected — always one VM, serial |
| SET plus `GROUP BY` slice | ✗ Rejected — 2-4 VMs and up to 6.1 s for 6 slices under parallel spike load |

### Consequences

Parallelism is a property of the schema, not of the cluster. Chosen by Nico on 2026-10-21.

---

## ADR-011: sys owns the real deployment; CFB_OWNER is dropped

**Date:** 2026-10-21
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

The demo runs on a local Exasol Personal database; a separate owner user adds a password and a grants step without demo value.

### Decision

On the real deployment `sys` creates and owns the schemas, the adapter, the fetch UDF and `CFBD_VS`. There is no `CFB_OWNER` user. `make grants` only creates roles for readers (e.g. `CFB_READER` with `SELECT` on `CFBD_VS`). The `TEST_` namespace keeps its generated `CFB_TEST_OWNER`/`CFB_TEST_READER`, so key-protection integration tests still prove the script-bound grant model.

### Options Considered

| Option | Verdict |
|--------|---------|
| `sys` owns everything | ✓ Chosen — simplest for a local demo |
| Dedicated `CFB_OWNER` with script-bound grant | ✗ Rejected — extra password and grants step without demo value |
| `CFB_OWNER` with an auto-generated password | ✗ Rejected — same |

### Consequences

"Only `CFBD_FETCH` can read the key" holds for readers, not for the owner (`sys` can read every connection), on the real deployment. Chosen by Nico on 2026-10-21.

---

## ADR-012: CFBD features are split into two domains, `cfbd-vs` and `cfbd-fetch`

**Date:** 2026-10-22
**Plan:** `add-cfbd-vs-foundation`
**Status:** Accepted

### Context

The plan delivered 11 features in one domain `cfbd-vs`, above the library threshold of 8 features per domain. The owner was away and delegated the decision to the orchestrator.

### Decision

Split along the adapter/runtime boundary:
- `cfbd-vs/` (7): endpoint-catalog, flattening, pushdown, requirement-rules, gating, fan-out, properties.
- `cfbd-fetch/` (4): read-through, fetch-log, fetch-modes, key-protection.

Cross-references in the specs were rewritten to `cfbd-fetch/<feature>`. Features with more than 10 scenarios (pushdown 15, fetch-modes 13, flattening 12, endpoint-catalog 11, read-through 11, requirement-rules 11) were kept whole, because no obviously MECE split exists.

### Options Considered

| Option | Verdict |
|--------|---------|
| Two domains, 7 + 4 | ✓ Chosen — least disruptive; each domain is cohesive (what the schema exposes vs. how rows are fetched, stored and protected) |
| Keep 11 features in one domain | ✗ Rejected — exceeds the threshold |
| Split large features by scenario | ✗ Rejected — artificial splits |

### Consequences

Feature paths cited in `specs/_recorded/*` (the archived plan) and in the decision log of the plan still say `cfbd-vs/<feature>` for the four moved features. Other plans must cite `cfbd-fetch/...`. Revisit if either domain grows past 8.


## ADR-013: The loader's entry points are Lua scripting programs; host Python only deploys and wraps them

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

- The Exasol grammar (skill `exasol-database`) allows only `CREATE [LUA] SCRIPT` for scripting programs. Lua is the only way to run multi-statement SQL inside the database.
- It works from any SQL client, the app backend (Phase 6) and the AWS instance without host tooling.

### Decision

- `CFB_INFRA.BACKFILL(profile, from_season, max_calls)` and `CFB_INFRA.REFRESH_WEEK(season, week, season_type)` are Lua scripting programs (`CREATE LUA SCRIPT … RETURNS TABLE`), run with `EXECUTE SCRIPT`.
- Host Python compiles and deploys the load plan, wraps the calls (`make backfill`, `make refresh`: estimate, `CONFIRM`, ledger) and runs snapshot/restore.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| A host-side Python orchestrator over pyexasol | ✗ Rejected — it loses the "one SQL call" beat and needs a client next to the DB. |
| Python UDFs | ✗ Rejected — they cannot run SQL or write tables. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [1].

---

## ADR-014: The demo shows `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, 'regular')`, which returns the step timeline

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

`CALL` is a reserved word in Exasol but has no statement. `EXECUTE SCRIPT script(args) [WITH OUTPUT]` is the statement (grammar checked). The mission, roadmap and user guide need the wording change (tasks 8.1, 8.3, 8.4).

### Decision

- The demo line `CALL REFRESH_WEEK(2026, N)` (roadmap demo flow, beat 3) becomes `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, N, 'regular')`.
- The script returns the step timeline as a result table, one row per fetched table (step, status, seconds, slices, rows, calls), so the one call also shows "SQL → HTTP" per table.
- Never run it `WITH OUTPUT`: that discards the table.
- If probe 1.1a shows that `EXECUTE SCRIPT` accepts fewer arguments than declared, the two-argument form works too.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| An SQL preprocessor that rewrites `CALL x(...)` into `EXECUTE SCRIPT`. Rejected | ✗ Rejected — it shows non-Exasol syntax to an Exasol audience, and it is a session-level dependency that can break the demo. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [2].

---

## ADR-015: Set logic lives in SQL views; the Lua stays thin, uses `pquery` throughout, and the views are tested before the Lua

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

Declarative views can be inspected (`SELECT * FROM CFB_META.BACKFILL_QUEUE` is itself the estimate) and tested early. `pquery` is the only way to keep the lock and logs consistent after a failure.

### Decision

- Finality, gaps, gating, the current season, queue order, quarantine, orphans, calls to complete and the quota arithmetic live in the `CFB_META` views `LOAD_SLICES`, `BACKFILL_QUEUE`, `LOAD_STATUS` and `QUOTA`. They are tested on the engine in group E, before any Lua exists.
- The Lua only:
  - takes the lock;
  - reads the next batch;
  - runs the batch transaction;
  - logs and commits.
- Every SQL statement in the Lua runs through `pquery`, and each entry function is wrapped in `pcall`. A failure therefore always reaches the rollback, the log and the lock release before the error is re-raised.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Logic in Lua with a host Lua 5.4 + busted toolchain | ✗ Rejected — a toolchain for about 300 lines of glue, and the logic would be invisible to the presenter. |
| `query()` throughout | ✗ Rejected — it ends the script on the first error (skill `exasol-udfs`), so the error path could not run (review M3). |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [3].

---

## ADR-016: Read-through stays on during loads; the loader keeps only fetched rows and never changes virtual schema properties

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

- Virtual schema properties are global. During a demo refresh, the app's and the presenter's concurrent queries would re-fetch stored slices and spend quota, and a crash would leave read-through off.
- The result is the same either way. A batch contains only slices that are missing or not final, and the stored branch serves only final slices. The fetch branch therefore fetches every batch slice, and the stored branch returns nothing for them; the filter is a safety net.
- Consequence: current-season slices are never final, so any `CFBD_VS` query on them is a live call. Pages and the chat must read current-season data from `CFB_CORE` (review m1; tasks 8.1, 8.2).

### Decision

- Every batch query reads `CFBD_VS.<T>` with read-through on and adds `REQ_SOURCE <> 'STORED'`.
- No loader code issues `ALTER VIRTUAL SCHEMA`; a unit test scans the sources.
- This deviates from the orchestrator's wording "read-through off for the load".

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Switch `READ_THROUGH` to `FALSE` for the load (the harness's `stage_slices` pattern) and back afterwards. | ✗ Rejected |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [4].

---

## ADR-017: Stage, then swap, in one transaction per batch of one plan entry

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

- Non-final slices are replaced where the orchestrator asked: delete+insert per slice, inside the batch transaction.
- Other sessions never see a half-replaced slice, and a failed batch leaves no trace.
- The HTTP part runs before `CFB_CORE` is touched, so the write window is short.
- The quota is read in its own committed transaction before the batch (review m5).

### Decision

- A batch is one plan entry (`ENTRY_ID`), of at most `MIN(MAX_CALLS_PER_QUERY, allowance)` slices. Its fan-out query (fixed filter + slicing filter) inserts into `CFB_STAGE.<T>` (the same DDL as the mirror), which is asserted empty before the batch. Then, in the same transaction:
  1. `DELETE FROM CFB_CORE.<T> WHERE REQ_SLICE IN (batch)` and `INSERT … SELECT` from the stage;
  2. `MERGE` into `LOAD_LOG`, with `IS_FINAL` from `LOAD_SLICES.FINAL_IF_LOADED_NOW` and `ROWS_LOADED` from the staged counts;
  3. one `FETCH_LOG` row per batch slice (staged `REQ_*` metadata, or `ROWS_RETURNED = 0`);
  4. clear the batch's `LOAD_FAILURES` rows and the stage, update the run and step logs, `COMMIT`.
- Any error is handled by [24].

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Batches per table | ✗ Rejected — a `WHERE` clause cannot express two entries of the same table (two classifications, range + season) (review M4). |
| Insert straight into `CFB_CORE` | ✗ Rejected — the HTTP time would fall inside the core table's write window. |
| One transaction per run | ✗ Rejected — not resumable. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [5].

---

## ADR-018: Slice keys come from templates compiled by the adapter's own planner; each batch is checked with a zero-call EXPLAIN

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

- The grammar exists once (foundation [5]), and a drift between plan and adapter is caught at zero calls.
- A drift that moves both sides together (catalog change, restore from an older catalog) is caught by the orphan guard [23].

### Decision

- At deploy, `loader/templates.py` runs `vs.cfbd_vs.rules` (explicit defaults, rules) and `vs.cfbd_vs.plan` with sentinel values for each entry, and stores `SLICE_TEMPLATE` (e.g. `/games?seasonType=both&year={season}`).
- The views substitute integers.
- Before fetching, each batch runs `EXPLAIN VIRTUAL` on its query. The Lua checks every batch key as a quoted string in the manifest, with plain string search and no JSON library, together with the planned count. On a mismatch it stops before any request and names `make deploy`.
- After the fetch, every staged `REQ_SLICE` must be a batch key.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Re-implement the slice-key grammar in Lua or SQL | ✗ Rejected — two implementations would drift. |
| `EXPLAIN` every slice at run time | ✗ Rejected — one adapter call per slice. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [6].

---

## ADR-019: One load plan file, `loader/load-plan.yaml`, compiled into `CFB_META`; several entries per table allowed

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

One reviewed source decides what costs calls. The validator uses the adapter's own rules, so an entry the API cannot serve fails at deploy, not on a paid call.

### Decision

- The plan lives at `loader/load-plan.yaml`, following the mission's planned layout (`loader/`: load plan, backfill, `REFRESH_WEEK`), not next to `vs/catalog-overrides.yaml` as the orchestrator presumed.
- Each entry has: table, profile, priority, slicing (`none` / `season` / `season_range`), seasons, gaps, fixed `params`, refresh class.
- A table may have several entries with non-overlapping seasons per fixed parameters.
- `none` and `season_range` slices have `SEASON` NULL, so they are queued first and `from_season` never skips them (review M4).
- `make deploy` validates the plan against the catalog, listing every error, and replaces the plan tables in one transaction. `LOADER_CONFIG="k=v,…"` overrides settings for one deployment until the next deploy.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Load-plan columns in `vs/catalog-overrides.yaml` | ✗ Rejected — that mixes "what the API means" with "what the project stores". |
| A plan table edited with SQL | ✗ Rejected — not reviewable in git. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [7].

---

## ADR-020: Refresh classes `static` and `weekly` with date-based finality; D15 storage classes are derived

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

Dates are deterministic, cheap and testable with a clock seam. The roadmap's "the current or incomplete week is non-final" holds: the current season's slices contain the current week and stay non-final.

### Decision

- Configured refresh class:
  - `static`: final on load.
  - `weekly`: season slices are final from `season_final_after` (02-15) of the next year; `REFRESH_WEEK` re-fetches them while they are not final, for the profiles in `refresh_profiles`.
- A slice stored before it was final is fetched once more by the next `BACKFILL` after it becomes final.
- The current season starts on `season_rollover` (08-01).
- Storage classes are derived, not configured. The orchestrator's "stored / volatile / on-demand" are D15's classes:
  - `stored` = a `demo` entry;
  - `federated`/on-demand = no entry, or `full` only;
  - `volatile` = no mirror in the catalog (rejected by the validator);
  - `unavailable` = gated without the feature.
- The first draft's `archive` class and `season_week` slicing moved to the backlog ([29]): `refresh_profiles` already keeps `full` entries out of the weekly refresh.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Final when every game of the season has `COMPLETED = TRUE` | ✗ Rejected — historic seasons contain cancelled games that never complete. |
| Configuring D15 classes in the plan | ✗ Rejected — duplicates the catalog. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [8].

---

## ADR-021: The weekly refresh re-fetches the current season's season slices instead of adding week slices

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

Read-through matches slice keys exactly (foundation [5]). Week slices next to a stored season slice would store every game twice, and subsumption is a foundation non-goal. A season call costs the same 1 call, and it also picks up corrections to earlier weeks.

### Decision

For `weekly` entries, `REFRESH_WEEK(season, week, season_type)` re-fetches the season's slice (e.g. `/games?seasonType=both&year=2026`), which stays non-final, with delete+insert. `week` and `season_type` are validated against the stored calendar, name the run and feed the registered steps.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Fetch `/games?…&week=N&year=2026` each week, as the roadmap's refresh list implies. | ✗ Rejected |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [9].

---

## ADR-022: Budget: loader settings from the YAML, checked against `/info`, overridable per deploy; two guards

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

The key's own counter is the only complete record. The default keeps the free-tier design target even on the academic key (3,000).

### Decision

- `monthly_call_budget` (1000, D13) and `refresh_reserve` (150) live in `LOADER_CONFIG`, deployed from the YAML or overridden for one deployment with `make deploy LOADER_CONFIG="…"`.
- `CFB_META.QUOTA` computes:
  - `PROJECT_BUDGET = LEAST(MONTHLY_LIMIT, budget)`;
  - `REFRESH_SPENDABLE = GREATEST(0, LEAST(REMAINING_CALLS, PROJECT_BUDGET − USED_CALLS))`;
  - `BACKFILL_SPENDABLE = GREATEST(0, REFRESH_SPENDABLE − reserve)`.

  `USED_CALLS` counts the key's whole month, so `PROJECT_BUDGET` caps every client, including SQL typed outside `make`.
- For the October live group, AWS is deployed with budget 400 and reserve 50 (the owner's authorization), and the default is restored afterwards.
- The ledger is the second guard: the per-plan cap, plus the month cap checked against `/info` `USED_CALLS` + estimate, not against the local file (review M1).
- The refresh may dip into the reserve. Replay-mode runs read no quota.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Virtual schema properties | ✗ Rejected — the adapter cannot use them. |
| The ledger as the only month guard | ✗ Rejected — it misses calls made from SQL clients, other plans and the shared pool. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [11].

---

## ADR-023: Backfill semantics: candidates, gaps, order, batches, measured spend, dry run, signature

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

- Priority-major ordering gives the owner control and keeps fan-out parallelism.
- The roadmap's `BACKFILL([from])` is the positional `from_season`.
- The measured spend makes the allowance true.

### Decision

- **Candidates:** missing slices plus finalizable non-final slices. Quarantined slices and tables with orphans are excluded.
- **Gaps:** registered as final empty slices (`LOADED_BY = 'LOAD_PLAN_GAP'`) at 0 calls, in one `GAPS` step row; they are excluded from the run's `SLICES` (review m18).
- **Order:** profile (`demo` before `full`), priority, newest season (seasonless first), table, entry. Slices with an earlier failure come last, fetched alone.
- **Batches:** the longest run of consecutive queue entries of one plan entry, capped by `MAX_CALLS_PER_QUERY` and the allowance.
- **Spend:** in live mode it is measured as the `/info` used-calls delta around each batch, and the allowance decreases by the measured spend (review M2).
- **Signature:** `BACKFILL(profile, from_season, max_calls)`, with NULL meaning no limit. `max_calls = 0` is a dry run: it registers gaps, runs `EXPLAIN` on every pending batch, and fetches nothing (review m20).
- The result has numeric `CALLS_NEEDED` and `FAILED_SLICES` columns (review m17).

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Strict season-major across all endpoints | ✗ Rejected — batches of one slice. |
| The `ARRAY` parameter form for optional arguments | ✗ Rejected — less readable. |
| Count slices as calls | ✗ Rejected — retries make that an underestimate. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [12].

---

## ADR-024: `REFRESH_WEEK` is a step chain with a registry; steps receive the season type

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

The mission's chain (fetch → merge → model → predictions → simulations) grows phase by phase without touching the Phase 2 script, and the bowl weeks up to the 2027-01-25 title game are addressable.

### Decision

- Built-in steps: 1 `CHECK`, then 2–99 `FETCH <table>`, one per fetched table (review m13).
- Registered steps come from `refresh_steps` in the YAML, deployed to `REFRESH_STEPS` (step number ≥ 100), and are called as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)`.
- `season_type` is `'regular'` or `'postseason'` (NULL = `'regular'`). The calendar numbers both from week 1 (review M9).
- Each step commits its log row, and a failing step stops the chain.
- Tables commit one by one, so a failure leaves earlier tables refreshed (review m14).
- This plan registers no steps. Phases 3–5 add model, predictions and simulations.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| `(run_id, season, week)` only | ✗ Rejected — postseason week 1 and regular week 1 would be indistinguishable, and every Phase 3–5 step would need a later interface change. |
| Hard-code later steps in the refresh script. | ✗ Rejected |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [14].

---

## ADR-025: Snapshots are gzip CSV files written by pyexasol, scoped to `CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`; restore is atomic and catalog-checked

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

- Fingerprints check every conversion, and the landing schema makes a failed restore change nothing.
- Run history is per deployment: restored `LOADED_BY`/`CONTEXT` values refer to the source deployment's runs.
- The catalog hash covers the generated content (foundation [25]), so any regeneration changes it, not only a slice-key change. `FORCE=yes` is then a deliberate decision, backed by the orphan report after the restore ([23]).
- Probe 1.2: the pyexasol path round-trips every mirror and meta type exactly, except full-precision DOUBLEs (at most 3.8e-12 relative); fingerprints use a DECIMAL sum for DOUBLE.

### Decision

- `make snapshot` writes one gzip CSV file per scope table (pyexasol `export_to_file`, manifest format `csv-gzip`) to `~/.cfb-demo/snapshots/<name>/` (0700/0600; never in the repo or OneDrive; never overwritten).
- The scope is every `CFB_CORE` mirror plus `LOAD_LOG` and `FETCH_LOG` (review M5). `LOAD_RUN`, `REFRESH_LOG` and `LOAD_FAILURES` stay with each deployment.
- `manifest.json` holds catalog and plan hashes, columns, rows, SHA-256 and a per-column fingerprint.
- `make restore`:
  1. refuses on a column difference, and on a catalog-hash difference unless `FORCE=yes` (review M6);
  2. `import_from_file`s into a landing schema `CFB_RESTORE`;
  3. checks the fingerprints;
  4. replaces every scope table in one transaction;
  5. drops the landing schema and records a `RESTORE` run.

  It never touches `CFBD_VS`.
- Both commands take the run lock.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Parquet via exapump 0.13 | ✗ Rejected — fails on VARCHAR values with a comma, quote or newline (every JSON column), trims spaces, writes untyped columns and no file for an empty table (probe 1.2). |
| Snapshot `LOAD_RUN` and `REFRESH_LOG` too | ✗ Rejected — identity collisions, resurrected `RUNNING` rows, and the restore's own lock row replaced by the swap. |
| Native `EXPORT … INTO LOCAL CSV` | ✗ Rejected — CSV only. |
| Native Parquet `IMPORT` | ✗ Rejected — S3 only. |
| pyexasol `export_to_parquet` | ✗ Rejected — it infers column types from the CSV stream. |
| S3 | ✗ Rejected — external. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [15], with the format amended by plan decision [31] (probe 1.2).

---

## ADR-026: Deployment targets come from the foundation's `vs/host/target.py`; the loader adds only the exapump environment

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

One owner for target resolution and pinning (orchestrator instruction). The loader's only new surface is the child-process environment, with no secret in files, argv or logs.

### Decision

- The foundation's code-review fix `vs/host/target.py` (prerequisite P2) provides `TARGET=local|aws`:
  - it resolves the DSN, the sys password (at connect time) and AWS certificate pinning from the Personal deployment files;
  - it is wired into deploy, grants, dns and the Makefile (`TARGET ?= local`).
- This plan reuses it and adds:
  - `loader/exapump.py`: `EXAPUMP_DSN` only in the child's environment, `--certificate-fingerprint` from the target's pinned certificate;
  - `TARGET` on the loader's own make targets.
- The 9.0 smoke test checks that exapump's native transport stays on the pinned node of the 3-node cluster, or that the pinning covers every node it opens (review m11).

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| A second target module in this plan (the first draft's `vs/host/targets.py`) | ✗ Rejected — two owners for the same secrets and pinning code. |
| `CERT_NONE` for AWS | ✗ Rejected — the sys password would cross the internet unverified. |
| exapump profiles | ✗ Rejected — secrets in `~/.exapump/config.toml`. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [17].

---

## ADR-027: Stored data is pinned to the catalog and the plan; orphans are reported and block re-fetching; migration is out of scope

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

A changed default changes every slice key of a table. Paying for a re-backfill after a catalog tweak, a plan edit or a restore is exactly what D11 forbids.

### Decision

- Deploy refuses a mirror whose columns differ from the catalog (foundation behaviour), and restore refuses a snapshot whose columns differ.
- Restore also refuses a different catalog hash without `FORCE=yes`.
- **Orphan slices** are `LOAD_LOG` rows that a loader run wrote (`BACKFILL#…`, `REFRESH_WEEK#…`, `LOAD_PLAN_GAP`) for a planned table but that match no planned key:
  - `LOAD_STATUS` reports them;
  - `make deploy` warns about them;
  - `BACKFILL` and `REFRESH_WEEK` refuse to fetch their table until the owner migrates or deletes them (review M6).
- This plan's live backfill waits for the foundation's 9.4, so the stored data fits the final catalog. A future catalog change needs its own migration plan.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Build a migration now. | ✗ Rejected |
| Only warn | ✗ Rejected — a backfill would then re-buy the whole table. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [23].

---

## ADR-028: A failing slice is isolated and quarantined; a systemic failure stops the run

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

It bounds the waste per persistent failure (one batch, plus one solo fetch per run, for two runs), keeps the queue moving, and stops quickly on outages.

### Decision

- In `BACKFILL`, a failed batch of several slices is rolled back and each slice is fetched alone. The good ones commit.
- A slice that fails alone gets one failure per run in `LOAD_FAILURES`. From then on it is batched alone, after every other candidate of a run, and it is quarantined after `quarantine_after` (2) runs.
- Quarantined slices are skipped and shown in `LOAD_STATUS` until the owner deletes the row; a success clears them.
- If every slice of a batch fails alone too, the cause is not the slice: the run stops with `FAILED`. *Amended by [45]: the run stops after 2 consecutive failed batches.*
- `REFRESH_WEEK` has no fallback or quarantine: a failure stops the refresh and is shown.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Fail the run and re-run the same batch | ✗ Rejected — the fetch UDF fails the whole fan-out on one slice (`vs/cfbd_vs/fetch.py`), so every retry re-pays the siblings, and a persistent failure blocks the queue forever (review M2). |
| Quarantine on the first failure | ✗ Rejected — a short outage would quarantine slices. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [24]. Amended by plan decision [45] (see Decision).

---

## ADR-029: Rankings come from `RANKINGS_POLLS_RANKS` alone, which needs foundation prerequisite P1

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

A natural key needs no positional join and makes each slice self-contained.

### Decision

- The demo loads only `RANKINGS_POLLS_RANKS`. The foundation's code-review fix gives it its own parent keys `SEASON`, `WEEK` and `POLL` in `vs/catalog-overrides.yaml` and regenerates the catalog; this plan does not edit the overrides.
- `RANKINGS_POLLS` is not loaded, which saves 13 calls once and 1 per refresh.
- Task 2.3 stops if the catalog lacks the keys.
- Open recommendation to the foundation (not a blocker): add `SEASON_TYPE` as a parent key too. The final poll is postseason week 1, so it shares (`SEASON`, `WEEK` = 1, `POLL`) with the regular-season week-1 poll. Without it, Phase 3 must tell them apart some other way.

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| Load both tables and join them by position (the first draft) | ✗ Rejected — two requests in two transactions can shift the arrays when CFBD publishes a poll in between, and the join then silently pairs ranks with the wrong week (review M8). |
| Load both in one transaction with a consistency check | ✗ Rejected — more code, and the extra calls remain. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [27].

---

## ADR-030: A backfill stops after 2 consecutive failed batches, not after one failed one-slice batch (F.1, review m8)

**Date:** 2026-10-22
**Plan:** `add-cfb-persistence-refresh`
**Status:** Accepted

### Context

The orchestrator's decision: two in a row protects the quota during an outage (at most two batches and their single-slice retries are paid), and one transient error, for example on `TEAMS`, costs only that slice. Two adjacent slices that keep failing alone now end the run `FAILED` instead of `DONE_WITH_FAILURES`; they are quarantined after `quarantine_after` runs as before.

### Decision

- A batch **fails** when none of its slices is stored, also after its single-slice retries. That includes a fresh batch of one slice and a slice that failed in an earlier run (`BATCH_ALONE`).
- A failed batch is recorded (its slices get one `LOAD_FAILURES` failure each) and the run continues. After `STOP_AFTER_FAILED_BATCHES = 2` consecutive failed batches the run stops with `FAILED` and names the last error. A batch with at least one stored slice resets the count.
- The spec scenario "A failure of every slice stops the run" becomes "Two consecutive failed batches stop the run" (`GAMES` 2015–2020, `MAX_CALLS_PER_QUERY = 2`: the third batch is never requested), plus "One failed single-slice batch does not stop the run".

### Options Considered

| Option | Verdict |
|--------|---------|
| Decision as stated above | ✓ Chosen |
| The rule of [24], stop at the first failed batch | ✗ Rejected — in the demo plan `TEAMS`, `VENUES`, `CONFERENCES` and both `CONFERENCES_AFFILIATIONS` entries are one-slice batches, so one transient 500 on reference data stopped the whole backfill. |
| Record and continue without a stop | ✗ Rejected — an outage would spend the quota slice by slice. |
| Do not count failures toward quarantine in a run that ended systemic (the review's second option) | ✗ Rejected — changes the quarantine semantics; not needed for the outage case. |
| Count only fresh batches, not slices that failed before | ✗ Rejected — an outage over the held-back slices at the end of the queue would fetch every one of them. |

### Consequences

Shapes the `loader` domain specs (`specs/loader/*`). Plan decision [45].

---
