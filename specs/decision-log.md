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
