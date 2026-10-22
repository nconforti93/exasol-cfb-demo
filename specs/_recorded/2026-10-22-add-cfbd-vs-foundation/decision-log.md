# Decision Log: add-cfbd-vs-foundation

Date: 2026-10-21

## Interview

**Q:** How should roadmap Phase 0 be split into plans?
**A:** Two plans: this one (S1 + S2, kept code with specs and tests) and a separate one for S3 + S4 (throwaway experiments). No Rust or simulation work here.

**Q:** Which endpoints get virtual tables?
**A:** All 93 from the start. Tier-gated endpoints (weather, `/games/{id}/preview/adjusted`, scoreboard, live plays, likely `/wepa/*`) exist as tables but refuse queries when the key lacks the feature per `/info` `features`. Problem endpoints may be excluded only with an explicit, listed reason.

**Q:** Where do the conditional requirement rules come from, given they are prose-only in the spec?
**A:** From a reviewed, machine-readable overrides file `vs/catalog-overrides.yaml`, seeded from `docs/cfbd-api-reference.md` for every endpoint with conditional rules, defaults, caps or gating (about 40). The adapter enforces hard and conditional rules with messages that name the rule.

**Q:** Which flattening rules apply in the generator?
**A:**
- Nested objects are flattened as `PARENT_CHILD`.
- Arrays of objects become child tables (parent keys + array index; `RANKINGS` → `RANKINGS_POLLS` → `RANKINGS_POLLS_RANKS`; `/games/teams` and `/games/players` carry everything under `teams[]`).
- Arrays of scalars become a JSON `VARCHAR`.
- Huge objects (`/games/{id}/preview` with 1,837 leaf fields, `/teams/season/overview` with 839) get a curated subset plus a raw JSON column.
- Loader and adapter never rely on API defaults: they always set `classification`/`seasonType`/`year` explicitly (`SeasonType` has `both`).

**Q:** What does the adapter advertise and do?
**A:**
- A Python 3 adapter script, stateless.
- Filter capabilities only (equality, `IN`, ranges/`BETWEEN`, `AND`) on parameter columns. No aggregate pushdown, which avoids the 2026.1.0 crash on `COUNT`/`SUM` over `UNION ALL` with a VS branch.
- Pipeline: intersect predicates per parameter → contradiction gives zero rows at zero calls → hard and conditional rules and gating → expand year ranges and `IN` lists → enforce `MAX_CALLS_PER_QUERY` (VS property) → emit SQL.

**Q:** How does read-through (D15) work, given the adapter cannot run SQL?
**A:**
- The pushdown SQL is stored slices from `CFB_CORE.<T>` `UNION ALL` `CFBD_FETCH` over `(VALUES <slice requests>) WHERE NOT EXISTS` (the slice in `CFB_META.LOAD_LOG`).
- These must be verified, not assumed:
  - Exasol does not invoke the UDF for slices that `NOT EXISTS` filters out;
  - stored and live column types match exactly;
  - no `UNION`/aggregate crash occurs.
- `PROMOTE()` and the loader are Phase 2. This plan defines the `LOAD_LOG` slice-key contract and the `CFB_CORE` mirror DDL generated from the same catalog.

**Q:** What does the fetch UDF do, and how is the fetch log produced?
**A:**
- Python `SCALAR … EMITS`.
- Reads `CFBD_API` via `exa.get_connection` (address = base URL, password = key); one HTTPS GET per request.
- Flattens per the catalog, retries 429/5xx with backoff, redacts the key from errors.
- Modes `live` | `replay`. Replay reads recorded responses from BucketFS; recordings are never in git.
- The fetch-log mechanism is to be decided in this plan (UDFs cannot write tables), for example hidden metadata columns.

**Q:** What is the security model?
**A:** Verified on 2026.1.0:
- `GRANT ACCESS ON CONNECTION CFBD_API FOR SCRIPT CFB_INFRA.CFBD_FETCH TO CFB_OWNER`.
- Readers need no connection grant.
- `CREATE OR REPLACE` of the fetch UDF drops the grant, so deploy must re-grant (`make grants`, DBA).
- Never `ACCESS ANY CONNECTION`.

**Q:** Which targets and test tiers are needed?
**A:**
- Make targets `test`, `test-it`, `lint`, `coverage`, `deploy`, `grants` (a `connection` target was later dropped by D18).
- pytest unit tests (no DB) plus integration tests against the local DB in replay mode (zero API calls).
- Unit fixtures are small and synthetic: CFBD terms forbid redistributing raw data, and the repo goes public.

**Q:** How are live calls handled?
**A:** Live tasks come last: read `/info`, about 25 real calls (one per access pattern) for row counts and payload sizes, confirm the `seasonType`/`classification` defaults and whether one `/games` call returns a full season, document the results (catalog + budget re-confirmation), record replay fixtures (gitignored). The total live budget for this plan is ≤ 50 calls and must be stated.

**Q:** Which specs belong to this plan?
**A:** Spec governance (CLAUDE.md): `specs/` holds only user-visible behavior; testing, toolchain, CI and licensing are not specs. Domain `cfbd-vs/`, with features such as endpoint-catalog, pushdown, fan-out/call-budget, requirement-rules, read-through, fetch-log/inspector, gating, flattening/child-tables and fetch-modes. speq supports two path levels only.

**Q (coordinator update):** Does the key exist yet?
**A:** Yes. `CFBD_API` was created and checked via a throwaway UDF: tier Academic, `monthlyLimit` 3000, `resetAt` 2026-11-01, all five `features` false. `/info` consumed no call. The design target stays the free tier (1k/month, D13), and the live cap stays ≤ 50.

**Q (user decision D17):** What is the runtime?
**A:** Exasol Personal local (laptop-class), not the docker container `cfb-exa`:
- nano 2026.2.0-nano.3 under podman, 127.0.0.1:8563;
- sys password read at run time from the starter-kit credentials file;
- builtin PYTHON3 = Python 3.12.3, 20 CPUs;
- lifecycle only via `exasol stop/start -d local`, never `exakit`;
- keep the no-aggregate-pushdown rule even if the 2026.1.0 crash no longer applies.

**Q (user decision D18):** Is there a key tool?
**A:** No. `scripts/create_connection.py` was deleted and there is no `make connection`. The user creates `CFBD_API` with plain SQL as documented in `docs/user-guide.md`. The plan must keep the user guide current. `make grants` stays.

**Q (user decision D16):** Rust?
**A:** Python is the only engine. No Rust toolchain assumptions in the root Makefile or CLAUDE.md.

**Q (coordinator correction):** How is UDF DNS handled on Personal nano?
**A:**
- The podman `use-vc` workaround does not apply: the nano image has no shell, and the Python UDF sandbox's `/etc/resolv.conf` is empty on any network. TCP egress to IPs works.
- DNS over HTTPS inside the UDF is not approved; the user decides. (Superseded: approved as D21, see below.)
- The plan needs:
  - a blocker task with an explicit check gating all live tasks;
  - the fetch UDF's resolution behind a single seam;
  - nothing before the live tasks may depend on DNS;
  - no `make dns-fix`.
- `CFBD_API` now exists on Personal.

**Q (user decision D21):** Is DNS over HTTPS approved?
**A:** Yes, recorded as D21 in `docs/roadmap.md`.
- Verified 2026-10-21 on Personal with a UDF that wraps `socket.getaddrinfo`:
  - the system resolver is tried first;
  - on `gaierror`, it queries `https://1.1.1.1/dns-query?name=<host>&type=A` with `Accept: application/dns-json` and caches the A records;
  - it then calls the original `getaddrinfo` with the IP.
- `/info` returned HTTP 200, with TLS SNI and the certificate still verified against the host name.
- The resolver seam implements exactly this:
  - system first, DoH fallback, per-UDF-instance cache;
  - the DoH endpoint is a VS property (default `https://1.1.1.1/dns-query`, `off` disables the fallback);
  - the DoH request verifies TLS;
  - no pinned-IP option.
- Task 9.0 becomes "`make dns-check` passes on Personal", and this plan's roadmap decisions start at D22.

**Q (planner questions 2–7, answered by the user 2026-10-21):**
**A:** All accepted as recommended:
- Q2: advertise `OR`/`NOT`/`<>`/`LIKE`/`IS NULL` with rejection messages, still with no aggregate, projection or `LIMIT` pushdown.
- Q3: the `P_` prefix for parameter echo columns.
- Q4: `MAX_CALLS_PER_QUERY` default 10, counting all planned requests including stored ones; `REQ_SOURCE = 'STORED'` skips the fetch and the budget.
- Q5: stricter overrides (`/plays/stats` requires `gameId`, `/roster` requires `year`, reserved-word columns get a trailing underscore).
- Q6: `docs/spikes/connection_privileges_test.py` is already deleted, so there is nothing to neutralize.
- Q7: the extra make targets `generate`, `dns-check` and `record` are fine.

No git commit is made before implementation.

## Design Decisions

### [1] Parameters are exposed as separate `P_` echo columns

- **Decision:**
  - Every query or path parameter becomes a column `P_<PARAM>`.
  - Each row carries the parameter values of the request that produced it, NULL if not sent; explicit defaults count as sent.
  - Response fields keep their own columns, even when names overlap (`WEEK` vs. `P_WEEK`).
- **Alternatives:** Bind parameters to same-named response fields (`WHERE WEEK = 8`), reviewed per endpoint. Rejected: about 150 parameter/field pairs, several semantic mismatches (conference abbreviation vs. name, `team` meaning either side), and a wrong binding silently drops rows.
- **Rationale:** Uniform across 93 endpoints with no per-pair review. Rows always satisfy their parameter predicates, so the residual stays correct. It sidesteps reserved words (`P_LIMIT`). On stage: "every `P_` column is an API knob". The S2 example query becomes `P_YEAR BETWEEN 2019 AND 2021 AND P_WEEK IN (8,9)`.
- **Promotes to ADR:** yes

### [2] One dynamic-output fetch UDF serves every table; the catalog is embedded in both scripts

- **Decision:**
  - `CFB_INFRA.CFBD_FETCH(TABLE_NAME, SLICE_KEY, CATALOG_HASH, FETCH_MODE, CONNECTION_NAME, REPLAY_PATH) EMITS (...)` with the `EMITS` clause written by the adapter from catalog types.
  - The bundler embeds `vs/cfbd_vs` and the catalog into the adapter and the fetch UDF.
  - A catalog-hash handshake refuses mixed deployments.
  - Deploy replaces the fetch UDF only when its text changed.
- **Alternatives:**
  - One UDF per endpoint: rejected, because the connection grant is per script.
  - Catalog in BucketFS: rejected, because it couples live mode to file sync.
  - Flatten plan passed in the pushdown SQL: rejected, because it clutters the inspector and `EXPLAIN`.
- **Rationale:** The single key reader holds by construction. Stored and live types come from one source. The pushdown SQL stays readable.
- **Promotes to ADR:** yes

### [3] Advertise extra predicate capabilities so untranslatable parameter predicates are rejected, not silently empty

- **Decision:**
  - Advertise `FILTER_EXPRESSIONS` with `=`, `<>`, `<`, `<=`, `BETWEEN`, `IN`, `LIKE`, `IS [NOT] NULL`, `AND`, `OR`, `NOT` and the common literals.
  - Translate only `=`/`IN`/bounded integer ranges/`AND`/same-parameter `OR` on `P_` columns.
  - Reject every other parameter predicate with a message.
  - Apply all predicates on non-parameter columns as a residual in the pushdown SQL.
  - No aggregate, projection, `LIMIT`, ordering or join capability.
- **Alternatives:** Advertise only `=`/`IN`/ranges/`AND`, the interview's literal wording. Rejected: Exasol would evaluate unadvertised predicates on `P_` columns locally against NULL echo values (for example `P_TEAM LIKE 'Mich%'` with `team` unsent) and return silently empty results.
- **Rationale:** Keeps the interview's intent (filters only, no aggregate crash path) and turns a silent-wrong-result class into explicit errors. A refinement of the interview decision; **accepted by the user on 2026-10-21**.
- **Promotes to ADR:** yes

### [4] The call budget counts planned requests, including stored slices; stored-only queries are exempt

- **Decision:** `MAX_CALLS_PER_QUERY` (default 10) is enforced at plan time on the number of planned requests per virtual-table access. A query restricted by `REQ_SOURCE = 'STORED'` emits no fetch branch and skips the budget.
- **Alternatives:** Count only live slices at run time, inside the UDF via a window count. Rejected for v1: it is more complex, and the rejection would happen at execution, not at `EXPLAIN`.
- **Rationale:** The adapter cannot see `LOAD_LOG`. A worst-case ceiling is safe, visible in `EXPLAIN VIRTUAL`, and matches "never route a broad scan through the API". Broad reads of stored data go through `CFB_CORE` or the stored-only filter. The budget applies per virtual-table access, because Exasol pushes each table separately.
- **Promotes to ADR:** yes

### [5] Read-through matches canonical slice keys exactly; `LOAD_LOG` contract

- **Decision:**
  - A slice is (`TABLE_NAME`, `SLICE_KEY`). `SLICE_KEY` is the canonical request path plus sorted query string, equal to `REQ_SLICE`.
  - `CFB_META.LOAD_LOG(TABLE_NAME, SLICE_KEY, IS_FINAL, ROWS_LOADED, LOADED_AT, LOADED_BY)` has PK (`TABLE_NAME`, `SLICE_KEY`).
  - A slice is served from `CFB_CORE` iff a final `LOAD_LOG` row exists. Stored rows of non-final or unregistered slices are never returned.
  - Mirrors have identical columns, so `INSERT INTO CFB_CORE.T SELECT * FROM CFBD_VS.T …` works.
- **Alternatives:**
  - Subsumption, where a stored season serves a week query: deferred, because it needs parameter-to-field equivalences that [1] avoids.
  - A catalog hash per slice: rejected, because every overrides tweak would invalidate all stored slices and re-spend quota.
- **Rationale:** Simple, provable and exactly-once per slice. `IS_FINAL` lets `REFRESH_WEEK` re-fetch the current week without a bypass switch. Changing an explicit default changes slice keys; the catalog diff shows it and the loader must migrate.
- **Promotes to ADR:** yes

### [6] Fetch log: request metadata columns plus an EXPLAIN manifest; consumers persist

- **Decision:**
  - Every table carries `REQ_SLICE`, `REQ_SOURCE`, `REQ_STATUS`, `REQ_ATTEMPTS`, `REQ_MS`, `REQ_ROWS`, `REQ_AT`.
  - The pushdown SQL starts with a JSON manifest of the planned requests, so zero-row requests are visible too.
  - `CFB_META.FETCH_LOG` is written by consumers (Phase 2 loader, Phase 6 backend) from a staged result plus the manifest, using the reference helper `vs/host/fetchlog.py`.
- **Alternatives:**
  - A marker row per request: rejected, it breaks SQL result semantics.
  - The UDF writing to BucketFS or HTTP: rejected, it needs another secret.
  - A backend-only log: rejected, it can't see in-database refreshes.
- **Rationale:** UDFs cannot write tables. Actuals travel with the rows, the plan travels with `EXPLAIN`, and persistence stays with whoever stages the result.
- **Promotes to ADR:** yes

### [7] Tier gating via the `API_FEATURES` property, fail closed, synced from free `/info`

- **Decision:** Gated endpoints map to `/info` features in the overrides. The adapter refuses when `API_FEATURES` lacks the feature, and the property is empty by default. Deploy syncs it from `CFBD_VS.INFO`, which is free (verified), when reachable. The fetch UDF maps 401/403 to a clear access error. The `adjustedMetrics` mappings for `/wepa/*` and preview/adjusted are marked unverified.
- **Alternatives:** The fetch UDF checks `/info` at run time before every gated call. Rejected: the refusal would happen at execution, not plan time, and it needs DNS.
- **Rationale:** Fits the adapter pipeline, is visible in `EXPLAIN`, and fails safe.
- **Promotes to ADR:** no

### [8] Child tables: 1-based position columns, declared parent keys, explicit hoisting, curated huge objects

- **Decision:**
  - `ROW_NO` plus one `<ARRAY>_NO` per level. Children join on `REQ_SLICE` plus position columns.
  - Natural parent keys are declared in the overrides.
  - Hoisting only where declared (`/games/teams`, `/games/players`).
  - Endpoints over 500 flattened fields need a curated subset plus `RAW_JSON` and get no child tables.
- **Alternatives:** Infer parent keys and hoisting heuristically. Rejected: wrong guesses are silent; explicit entries are reviewed.
- **Rationale:** A positional join always works. Natural keys add usability where reviewed.
- **Promotes to ADR:** yes

### [9] Type mapping and strict flattening

- **Decision:**

  | Spec type | Column type |
  |---|---|
  | integer | `DECIMAL(18,0)` |
  | number | `DOUBLE` |
  | boolean | `BOOLEAN` |
  | `date-time` | UTC `TIMESTAMP` |
  | string | `VARCHAR(2000) UTF8` (override per column) |
  | JSON | `VARCHAR(2000000) UTF8` |

  Values that don't fit fail the query with the table and column named. Reserved-word column names get a trailing `_`.
- **Alternatives:**
  - Truncate or coerce: rejected, because it changes data silently.
  - Quote reserved identifiers: rejected for user and chat ergonomics.
- **Rationale:** No silent data loss. The live run (9.3) and the synthetic sweep catch catalog sizing early.
- **Promotes to ADR:** no

### [10] Integration tests: mock CFBD server + synthetic recordings + `TEST_` namespace

- **Decision:**
  - Integration tests deploy into `TEST_CFB_*`/`TEST_CFBD_VS` (schema-name properties).
  - Live mode is tested against a host-side mock API addressed by IP, through the connection `CFBD_API_MOCK` with a marker key.
  - Replay is tested with schema-generated synthetic recordings.
  - A guard refuses any test that touches `CFBD_API`.
- **Alternatives:** Real recordings in tests, or tests on the real namespace. Rejected: CFBD terms (the repo is public), quota, risk to stored data and to the real connection.
- **Rationale:** `make test-it` costs zero calls and needs no DNS. Retry, redaction and key-protection behavior become testable.
- **Promotes to ADR:** yes

### [11] Recordings via owner-only raw output; live calls capped by a ledger

- **Decision:**
  - `CFBD_FETCH('@RAW', …)` emits the raw body in parts of at most 2,000,000 characters.
  - `scripts/record.py` (owner) writes recordings to `cache/replay/` and the Personal BucketFS directory, with names derived from the slice-key hash.
  - Every live call goes into `cache/live-calls.jsonl`, with a hard cap of 50 for this plan and `/info` before and after.
- **Alternatives:** Record flattened rows (can't replay child tables), or call CFBD from the host (the key would leave Exasol).
- **Rationale:** One recording replays every table of an endpoint, the key stays in Exasol, and the budget is provable.
- **Promotes to ADR:** no

### [12] Explicit hidden defaults, made-required defaults and the row-cap completeness rule

- **Decision:**
  - Overrides turn each hidden default into an explicit sent-and-echoed default (`seasonType=both`, `classification=fbs` where the spec says so), a requirement (`/roster` `year`), or a verified "omission means all".
  - `/plays/stats` requires `gameId` because of its 2,000-row cap.
  - A prose lint fails generation for uncovered rule-like descriptions.
- **Alternatives:** Rely on API defaults, or allow capped queries.
- **Rationale:** Implements D14's "never rely on defaults" mechanically and keeps it from drifting on spec bumps.
- **Promotes to ADR:** yes

### [13] Contradiction check before rules

- **Decision:** A contradictory parameter filter returns zero typed rows before rules, gating or budget are evaluated.
- **Alternatives:** Rules first.
- **Rationale:** The interview fixed this order. A contradiction never makes a call.
- **Promotes to ADR:** no

### [14] Resolver seam with the DNS-over-HTTPS fallback (implements user decision D21); `make dns-check` gates live mode

- **Decision:**
  - `vs/cfbd_vs/resolver.py` wraps `socket.getaddrinfo` in the fetch UDF (and the `dns-check` UDF):
    - the system resolver first;
    - on `gaierror`, a DoH `GET <DOH_URL>?name=<host>&type=A` with `Accept: application/dns-json` over verified TLS;
    - A records cached per UDF instance;
    - then the original `getaddrinfo` is called with the IP.
  - Property `DOH_URL`: default `https://1.1.1.1/dns-query`; must be `https` with an IP-literal host (no resolution loop); `off` re-raises the original error.
  - The DoH request carries only the host name.
  - TLS SNI, certificate checks and `Host` of the API connection stay on the host name.
  - No pinned-IP option.
  - DoH failures are not retried and name the host, the `DOH_URL` and the reason.
  - `make dns-check` resolves the API host through the seam and completes a certificate-verified TLS handshake without an HTTP request (0 calls). Task 9.0 is that check passing on Personal.
- **Alternatives:**
  - podman `use-vc`: impossible on nano (no shell; the sandbox `resolv.conf` is empty).
  - Pinned IPs: CDN IPs change; excluded by the user.
  - DoH always, without trying the system resolver: rejected, because the system resolver must win once nano fixes the sandbox.
- **Rationale:** The user approved D21, which is already recorded in the roadmap, and it was verified live (`/info` 200). Wrapping `getaddrinfo` leaves `http.client` and its certificate checks untouched, and the workaround stays in one removable module. Everything before the live tasks still needs no name resolution, apart from one network-marked DoH test that makes no CFBD call.
- **Promotes to ADR:** no

### [15] Platform behavior is probed before the pushdown SQL shape is fixed

- **Decision:** Task 1.4 probes Personal nano 2026.2 before the pushdown SQL builder (3.6) is finalized:
  - adapter-script support;
  - dynamic `EMITS`;
  - `VALUES`;
  - partial conjunct pushdown;
  - UDF not invoked for `NOT EXISTS`-filtered rows;
  - owner rights;
  - the aggregate-over-`UNION` crash;
  - comments in metadata and pushdown SQL;
  - BucketFS layout;
  - mock reachability by IP;
  - scalar vs. SET concurrency;
  - script size;
  - grant drop on replace;
  - reserved keywords.

  Each finding records the fallback the plan adopts if it is negative. If scalar `EMITS` serializes requests, `CFBD_FETCH` becomes `SET … EMITS` with `GROUP BY` slice.
- **Alternatives:** Build first, discover later.
- **Rationale:** S0 was verified on the retired docker 2026.1.0, not on the target runtime. These facts determine the SQL shape.
- **Promotes to ADR:** no

### [16] Volatile endpoints never read through and get no mirror

- **Decision:** `/info`, `/info/usage`, `/games/schedule`, `/scoreboard` and `/live/plays` (and their child tables) are always fetched.
- **Alternatives:** Mirror everything.
- **Rationale:** Their content changes between calls, so a "final slice" is meaningless for them.
- **Promotes to ADR:** no

### [17] Decision numbering in the roadmap starts at D22

- **Decision:** Decisions promoted from this plan go into `docs/roadmap.md` as D22 and onward.
- **Alternatives:** Continue at D19 or D21.
- **Rationale:** The parallel plan `add-engine-spikes` reserves D19 and D20, and D21 is the user's DoH decision.
- **Promotes to ADR:** no

### [18] Fan-out runs as one SET group with a bounded thread pool

- **Decision:** `CFBD_FETCH` is a `SET … EMITS` UDF. The pushdown sends all slices of a query to one group (no `GROUP BY`), and the UDF fetches them through a thread pool whose width is the new property `MAX_PARALLEL_FETCHES` (default 6, integer 1–16). Rows are emitted in input order.
- **Alternatives:** Scalar `EMITS` (probe 1.4m: always 1 VM, serial). `SET` + `GROUP BY` slice (the earlier fallback in [15]: 6 VMs and 2.0 s idle, but 2–4 VMs and up to 6.1 s for 6 slices under the parallel spike load).
- **Rationale:** Speed doesn't depend on DB load, the code controls how many CFBD calls run at once (rate-limit friendly), and probe 1.4f/h verified the `NOT EXISTS` guard and aggregates for this shape. Chosen by Nico on 2026-10-21.
- **Promotes to ADR:** yes

### [19] FETCH_LOG column REQ_SOURCE instead of SOURCE

- **Decision:** The `CFB_META.FETCH_LOG` column that records where a slice came from (`LIVE`, `REPLAY`, `STORED`) is named `REQ_SOURCE`, unquoted, the same name as the virtual-schema metadata column.
- **Alternatives:** The quoted `"SOURCE"` (the original contract name; every query must quote it). `SOURCE_` (the trailing-underscore rule for reserved words).
- **Rationale:** `SOURCE` is an Exasol reserved word, so the plan's DDL failed with a syntax error when created on Personal. `REQ_SOURCE` matches the column the rows already carry, so capture is a straight copy, and demo SQL needs no quoting. Chosen by Nico on 2026-10-21.
- **Promotes to ADR:** no

### [20] sys owns the real deployment; CFB_OWNER is dropped

- **Decision:** On the real deployment `sys` creates and owns the schemas, the adapter, the fetch UDF and `CFBD_VS`. There is no `CFB_OWNER` user and no `CFB_OWNER_PASSWORD`. `make grants` only creates roles for readers (e.g. `CFB_READER` with `SELECT` on `CFBD_VS`). The `TEST_` namespace keeps its generated `CFB_TEST_OWNER`/`CFB_TEST_READER`, so the key-protection integration tests still prove the script-bound grant model.
- **Alternatives:** A dedicated `CFB_OWNER` with a script-bound grant (the original D2/D18 model); `CFB_OWNER` with an auto-generated password.
- **Rationale:** It's a demo on a local Personal DB. A separate owner adds a password and a grants step without demo value. Trade-off accepted: `sys` can read every connection, so "only `CFBD_FETCH` can read the key" holds for readers, not for the owner, on the real deployment. Chosen by Nico on 2026-10-21.
- **Promotes to ADR:** yes

### [21] Empty SET group guard in the fetch branch

- **Decision:** The fetch branch of every pushdown is wrapped in `SELECT * FROM (<fetch>) WHERE "REQ_SLICE" IS NOT NULL`. The manifest gains a `sources` key listing the row sources the SQL actually reads (`[]`, `["STORED"]`, `["LIVE"|"REPLAY"]`, or both). Its `budget` is `null` when no budget was applied.
- **Alternatives:** Drop the fetch branch when all slices are stored. That isn't possible, because which slices are stored is only known at query time.
- **Rationale:** Found while building 3.6 on Personal 2026.2. A one-group `SET … EMITS` UDF with zero input rows is never called, but Exasol still returns one all-NULL row, as an aggregate does over an empty set. In the read-through shape this happens whenever every planned slice is stored. The UDF always sets `REQ_SLICE`, so the guard loses no real rows. The fetch-log helper needs `sources` to avoid logging unread slices as fetched with 0 rows. Integration tests 7.2/7.3 pin both an all-stored query and a stored empty slice.
- **Promotes to ADR:** no

### [22] 401/403 message follows the gating spec wording

- **Decision:** The error for an HTTP 401/403 answer states that "the key has no access to this endpoint", names the slice key (so the endpoint) and the status, and points at the key, the tier and the feature. It still fails after a single attempt and never contains the key.
- **Alternatives:** Keep the first wording ("the API refused the request ... its tier includes this endpoint") and loosen the gating spec scenario to match.
- **Rationale:** Found while writing the gating integration tests (7.2): the spec scenario "An access refusal from the API is reported clearly" requires the message to state that the key has no access, and the implemented text only implied it. The code follows the spec. Chosen by the orchestrator on 2026-10-22.
- **Promotes to ADR:** no

### [23] Body redaction is key-only; error messages also redact Bearer tokens

- **Decision:** A successful response body (and so every emitted value and every `@RAW` part) is redacted with `http.redact_key`: only the key itself, raw, JSON-escaped and percent-encoded. `Bearer <key>` therefore still becomes `Bearer [REDACTED]`. Error messages, including the detail taken from an error body, use `http.redact`: the key's forms plus any `Bearer <token>`, because they can echo request headers.
- **Alternatives:** Keep the generic `Bearer <token>` pattern on bodies as well (task 4.2's original wording). Drop the pattern everywhere.
- **Rationale:** Code review m2: the generic pattern rewrote real data ("the standard bearer Smith ran 5 yds" came back as "Bearer [REDACTED] ran 5 yds") and the change propagated into `CFB_CORE` and recordings. That violates [9] (no silent data changes). The key's own forms are what must never leave the UDF, and those are still caught. Error text has no data value and may carry another party's token, so the broad pattern stays there. Chosen by the orchestrator on 2026-10-22.
- **Promotes to ADR:** no

### [24] Recordings store attempts; replay emits them

- **Decision:** The recording format gains `attempts` (`{"slice_key", "status", "attempts", "ms", "requested_at", "body"}`). `assemble_parts` takes it from the `@RAW` `ATTEMPTS` column, and replay emits the recorded value as `REQ_ATTEMPTS` (and as `ATTEMPTS` in raw replay). A recording without the field (the older format), or `@RAW` rows without an `ATTEMPTS` column, gives NULL attempts, never 0. The stored branch keeps its constant `REQ_ATTEMPTS = 0`.
- **Alternatives:** Keep `REQ_ATTEMPTS = 0` for `REPLAY` and amend the fetch-modes spec and the user guide ("0 for STORED and REPLAY").
- **Rationale:** Code review m9: the spec says a replayed result differs from the live one only in `REQ_SOURCE` and the timing columns, and the integration test hid the difference by treating `REQ_ATTEMPTS` as a timing column. Recording the value makes replay faithful and lets the test compare it. NULL for older recordings is honest: their attempts are unknown, and 0 would claim that no request was made. Chosen by the orchestrator on 2026-10-22.
- **Promotes to ADR:** no

### [25] Catalog hash covers generated content; rankings ranks carry their own parent keys

- **Decision:**
  - `catalog_hash` is the SHA-256 of the canonical compact catalog JSON with the hash field blanked (`Catalog.content_hash()`). `generator_version` stays in the catalog as metadata and is part of the hashed content, but nobody has to bump it by hand any more. The adapter/fetch handshake still compares only strings.
  - `RANKINGS_POLLS_RANKS` declares the parent keys `season`, `seasonType`, `week` and `polls.poll`, so it has `SEASON`, `SEASON_TYPE`, `WEEK` and `POLL` columns (+3 columns, 6,398 in total) and loads standalone. `RANKINGS_POLLS` is no longer needed for the demo.
  - Every column and parameter type of a catalog must parse with the one grammar in `vs/cfbd_vs/sqltypes.py`; a catalog with another type is refused at load.
- **Alternatives:** Hash the inputs plus the canonical tables (still lets two catalogs share a hash when the inputs are equal but the generator changed). Keep `GENERATOR_VERSION` as the manual guard (it depends on someone remembering to bump it). For the rankings: load both tables in one transaction and check the position join (code-review of the persistence plan, M8, option 2).
- **Rationale:**
  - The hash exists to stop an adapter and a fetch UDF from different catalogs from talking to each other, so it has to change exactly when the content changes. A comment-only overrides edit no longer changes it (and so no longer forces a fetch replace that drops the `TEST_` grant).
  - The two rankings tables come from two requests in two transactions. A poll published between them shifts the arrays, and the positional join silently pairs ranks with the wrong week. `SEASON_TYPE` is needed because the final poll is postseason week 1 and would share (`SEASON`, `WEEK` = 1, `POLL`) with the regular-season week-1 poll.
  - Four modules parsed SQL types with four different rules, so a type one module accepted could be read differently by another.
- **Consequences:** A restore sees a different hash after any regeneration (see the persistence plan). `sqlrender.json_comment` (209 fuzz tests) and `rules.check_request` were removed as unused; the fuzz moved onto `plan.manifest_comment`. `Param.default` and `Param.description` left the catalog (the `default` rule and the `P_` column comment carry them). `Table.row_cap` stays until `tests/it/test_read_through_it.py` filters on the `row_cap` rule instead.
- **Promotes to ADR:** no

### [26] S1 keeps the generated VARCHAR(2000) sizes
- **Decision:** Task 9.4 does not shrink response `VARCHAR` sizes to the S1 maxima. Response strings stay `VARCHAR(2000) UTF8` and `RAW_JSON` stays `VARCHAR(2000000) UTF8`. The observed maxima are recorded in `docs/cfbd-api-measurements.md`.
- **Evidence:**
  - The S1 recordings exercised 134 of the 1,131 `VARCHAR(2000)` columns. The longest value was 416 characters (`PLAYS.PLAY_TEXT`), the next 89 (`GAMES.NOTES`).
  - The largest `RAW_JSON` was 48 KB (a game preview).
  - Every real payload flattened with zero errors and no uncovered fields.
- **Alternatives:** Size every observed column to its maximum plus headroom, as task 9.4 is worded.
- **Rationale:**
  - Exasol stores actual string lengths, so a smaller declared size saves nothing.
  - Strict flattening ([9]) would turn any longer value from an unseen season into a failed, already paid slice of the Phase 2 backfill (157 seasons of `/games` from 1869).
  - Changing column types would also force dropping and recreating the mirror tables on both deployments for no benefit.
- **Revisit:** Revisit if a consumer, such as a BI tool or the Phase 7 chat schema prompt, needs narrower types.
- **Chosen by:** the implementer on 2026-10-22; the orchestrator to confirm.
- **Promotes to ADR:** no

## Review Findings

<!-- Populated by speq-implement after code review. -->

### F.1 Fetch path (M1, m2, m9)

- **M1, fail-fast fetch pool:**
  - Each `run` now has one stop signal. The first failure sets it, whether a slice failed or `ctx.emit` did, and it is the error raised. Once it is set, no slice starts, and `CfbdClient.get(..., cancel=)` checks it before every attempt and before every retry wait.
  - Requests already sent finish. The signal is set before the pool is joined, so a failing emit cannot leave workers retrying while the pool waits.
  - Unit tests at width 2 use a barrier and a gated transport, with no sleeps. They pin that later slices are never requested, that an in-flight 503 is not retried, that the first failure wins over a later one in input order, and that a failing emit stops the pool. They pass 2,400 stress iterations and fail against both mutants: no stop signal, and the pool joined before the signal is set.
- **m2, redaction scope:** see [23]. A body containing "standard bearer Smith" now reaches rows and `@RAW` unchanged. The key and `Bearer <key>` are still redacted in bodies, and any `Bearer <token>` is still redacted in errors.
- **m9, replay attempts:** see [24]. `tests/it/fetch_helpers.py` no longer counts `REQ_ATTEMPTS` among the ignored columns, so the "replays identically" integration assertion now compares it. The fetch-modes and fetch-log spec deltas name the recorded attempts.

### F.4 Key redaction after JSON parsing

- **Exotic-escape gap closed:** `fetch.py` now calls `http.redact_body` on the live body before flattening or `@RAW`. It runs text redaction (percent forms now match in either hex case), then, only for a body containing a backslash, parses it and redacts the key from every string value and object key. A tree that held the key is re-serialized, so `@RAW` carries no escaped form. A body without the key keeps its original bytes, and the recording replays into the same rows. "standard bearer Smith" is unchanged.
