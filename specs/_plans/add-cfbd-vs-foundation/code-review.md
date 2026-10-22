# Code review: add-cfbd-vs-foundation

Reviewed 2026-10-22, before task 9 (live) and before `make test-it` has run.

## Verdict

**The implementation is ready to go to integration testing. No blocker was found, and one major finding must be fixed before the live tasks (group 9).** The runtime does what the spec says:

- I replayed 50 spec scenarios through the real adapter pipeline locally (no DB, no network), and every one produced exactly the requests or errors the spec demands.
- I found no SQL-injection path and no key-leak path.
- `vs/cfbd_vs` is stdlib-only.
- The scripts stay within the 2,000,000-character limit with 23 % headroom.

The integration tests check the scenarios meaningfully, and their main assertions have positive controls.

What remains is:

- one real quota leak;
- minor gaps in catalog integrity and in the `CFBD_API` test guard;
- dead code;
- DRY debt;
- doc statements that promise slightly more than the code does.

| Severity | Count |
|---|---|
| Blocker | 0 |
| Major | 1 |
| Minor | 29 |
| Nit | 18 |
| **Total** | **48** |

| Category | Count | Findings |
|---|---|---|
| Correctness, security, quota | 11 | M1, m1–m8, n1, n2 |
| Spec and docs mismatch | 4 | m9–m11, n3 |
| Guardrail violations and DRY | 8 | m14–m17, m28, n6, n8, n16 |
| Dead code | 5 | m12, m13, m29, n4, n5 |
| Tests (unit, CLI, IT) | 15 | m18–m27, n9, n10, n14, n15, n18 |
| Comments | 2 | n7, n17 |
| Optimizations | 3 | n11–n13 |

### Method and evidence

- **Read:** every runtime module in `vs/cfbd_vs` in full, plus `vs/host/*`, `vs/bundle.py`, `vs/generate.py`, `scripts/*`, `sql/meta/*`, `Makefile`, `pyproject.toml` and `docs/user-guide.md`. The generator, unit tests, probe spike and integration tests were reviewed in three parallel passes, and their findings are merged here.
- **Spec replay:** I ran the scenarios through `adapter.pushdown_query` against the committed catalog, using the probed filter-JSON shapes.
  - Covered: pushdown 1–14, requirement rules 1–10, gating 1–3, fan-out 1–4, stored-only, volatile and read-through switch-off.
  - Every one matched the spec: slice keys, error wording, zero-row SQL, and `budget: null` with `sources: ["STORED"]`.
- **Mechanical checks:**
  - an AST scan of `vs/cfbd_vs` imports against `sys.stdlib_module_names`: clean;
  - all 137 table names and every column against the 468 reserved keywords: clean;
  - comment lengths: at most 585, against a limit of 2,000;
  - bundle sizes: adapter 1,534,589 characters and fetch UDF 1,434,704, against the 1,990,000-character guard;
  - a fail-fast experiment on the fetch executor (M1).
- **Not done:** no DB connection, no `tests/it` run and no CFBD call, as instructed. Integration tests were judged by reading them.

---

## 1. Quota protection: plan-time guardrails are sound, but the fetch pool does not stop after a failure

The plan-time chain is correct and complete:

- contradiction first, then the 10,000-slice ceiling, then gating, rules and defaults, then the budget;
- `EXPLAIN VIRTUAL` makes zero calls;
- replay never touches the network;
- stored slices are excluded by `NOT EXISTS` at execution time;
- the stored-only pruning in `pushdown_sql.excludes_source` uses sound three-valued logic. I checked `NOT (… <> …)`, `IS NULL`, `''`-as-NULL, `OR` with response columns, trailing blanks and `LIKE`, and a fetch is only pruned when no fetched row can qualify.

The leak is at run time.

### M1 [major] A failed slice does not stop the pool from starting new CFBD requests
- **Location:** `vs/cfbd_vs/fetch.py:107-121` (`_ThreadedExecutor`), `fetch.py:216-219` (`run`). The docstring at `fetch.py:11` says otherwise.
- **Issue:** `ThreadPoolExecutor.map` queues every slice up front. The main thread only notices a failure when it reaches that slice in input order, and only then does `shutdown(cancel_futures=True)` run. Until then, idle workers keep taking queued slices and calling CFBD.
- **Evidence:** experiment with width 2 and 10 slices, where slice 1 is slow and slice 2 fails immediately with HTTP 400. Slices 3–10 were all still requested after the failure.
- **Why the tests miss it:** the unit test `tests/unit/test_fetch_entry.py:796` (`…cancels_queued_slices`) runs with `parallel=1`, the serial path, so the threaded path is untested.
- **Impact:** the query is already doomed, but up to `MAX_CALLS_PER_QUERY − 1` more calls are spent. That is up to 999 once an owner raises the budget, and the calls are never refunded. A 401/403 on a gated table whose `API_FEATURES` was set by hand, or a systematic 400, spends the whole budget on a 1,000-calls/month key.
- **Fix:**
  - Make the pool fail fast. A shared `threading.Event` is set by the first exception in `_execute` and checked before each `job.client.get` and before every retry sleep or attempt in `CfbdClient.get`. Alternatively, submit the futures yourself and use `concurrent.futures.wait(..., return_when=FIRST_EXCEPTION)`. Then emit in input order only after all futures succeeded.
  - Add a unit test with width ≥ 2, a slow first slice and a fast-failing second slice, and assert that later slices never reach the transport.
  - Reword the docstring at `fetch.py:11`.

### m1 [minor] The stored-only ceiling of 10,000 slices was never probed
- **Location:** `vs/cfbd_vs/adapter.py:41,254-262`; `pushdown_sql.py:173-193`; `docs/user-guide.md:178,311`.
- **Issue:** a stored-only query may plan 10,000 slices. That renders a 10,000-literal `IN (...)` in the stored branch and a 10,000-key manifest of about 0.5 MB in `PUSHDOWN_SQL`.
  - Probe 1.4 (d)/(j) verified `VALUES` and the manifest only up to 1,000 slices (58 KB).
  - A unit test pins the 10,000 case (`test_adapter_entry.py:633-638`), but the platform has not been tried at that size.
- **Fix:** probe a 10,000-slice stored-only `EXPLAIN VIRTUAL` and query once on Personal, or lower `MAX_PLANNED_SLICES` to 1,000, which equals the budget maximum and is already proven.

### n1 [nit] The budget counts requests, not attempts
- A query within budget can still make up to 4× its planned calls under retries (`http.py:27`). This is documented in `user-guide.md:179` and accepted by the spec.
- Consider showing `planned_requests × MAX_ATTEMPTS` as the worst case in the manifest, so `EXPLAIN` shows the true ceiling.

---

## 2. Injection and key protection: no exploitable path found

**Key finding: every value that reaches SQL is either a validated identifier or a doubled-quote literal, and the key never leaves the fetch UDF.**

Evidence for injection safety:

- **Pushdown SQL:**
  - Identifiers always go through `quote_identifier` (`sqlrender.py:63-68`).
  - Literals are rendered from typed Python values, never from request text: `filters.py:237-283` and `sqlrender.py:86-115`.
  - Negative numbers are parenthesized.
  - NUL characters and lone surrogates are refused (`filters.py:144-152`).
- **Properties:** schema and connection properties must match `[A-Z][A-Z0-9_]*`, `REPLAY_PATH` is whitelisted with dot segments refused, and `DOH_URL` is length-capped and becomes a literal (`properties.py:47-50,168-187`).
- **Slice keys:** they are percent-encoded ASCII (`plan.py:46-51`), so they cannot carry `*/`, quotes or `&`. The manifest also escapes `*/` (`plan.py:121-124`).
- **Deploy:** properties are validated before `ALTER … SET`, and property names are restricted to the known set (`vs/host/deploy.py:115-128,445-446`).
- **Grants:** identifiers are validated, and the password travels as a pyexasol `{password!q}` parameter (`scripts/grants.py:97-135`).

Evidence for key safety:

- The host comes only from the connection address (`http.py:137-145`), and a slice key must start with `/` (`http.py:44,189`).
- Redirects are not followed.
- Every error text and every success body is redacted (`http.py:125-130,202,222,229,287,296,299`).
- Flatten errors never include values (`flatten.py:10-14`).
- Raw output (`@RAW`) is reachable only by whoever can execute the UDF, never through the virtual schema.

### m2 [minor] Bearer redaction silently changes response data
- **Location:** `vs/cfbd_vs/http.py:45,125-130,202`.
- **Issue:** the case-insensitive pattern `\bbearer\s+<token>` is applied to every successful body. As a result:
  - `{"playText":"the standard bearer Smith ran 5 yds"}` comes back as `"... Bearer [REDACTED] ran 5 yds"` (reproduced);
  - the change propagates into `CFB_CORE` and into `@RAW` recordings;
  - this violates decision [9] ("no silent data changes").
  - The plan asked for this redaction (task 4.2), but the strict-flattening principle wins: the key's own forms are what must go.
  - Only positive tests exist (`tests/unit/test_http.py:321-334`).
- **Fix:** redact only `_key_forms(key)` in success bodies, and keep `_BEARER` for error and detail text. Add a test where a body containing "bearer word" comes back unchanged.

### m3 [minor] `make deploy --prefix` deploys a test namespace as `sys`, without the guard that `make grants` has
- **Location:** `scripts/deploy.py:111-131`, `vs/host/deploy.py:115-128`, against `scripts/grants.py:138-144` and `vs/host/namespace.py:1-6`.
- **Issues:**
  - A prefixed namespace is meant to be owned by its dedicated owner, and the harness deploys it as `CFB_TEST_OWNER` (`tests/it/harness.py:519-531`). `scripts/deploy.py` always connects as `sys`.
    - So `make deploy ARGS="--prefix TEST_"` creates `sys`-owned `TEST_` objects. The script-bound grant that `make grants --prefix TEST_` then binds is meaningless, because `sys` reads every connection.
    - Leftover `sys`-owned `TEST_` schemas also break the next harness deploy, which runs as the owner.
  - `deploy` does not reject `--prefix TEST_ --connection CFBD_API`. `grants.resolve_connection` does reject it.
- **Fix:**
  - In `desired_properties`, refuse the real connection for prefixed namespaces, reusing `resolve_connection`.
  - Either make `scripts/deploy.py --prefix` connect as `namespace.owner_user` (with its password from the environment), or drop `--prefix` from the CLI, since the harness calls `deploy()` directly. Update the Makefile comment at `Makefile:41` accordingly.

### m4 [minor] The access-error hint names the wrong schema and the wrong remedy
- **Location:** `vs/cfbd_vs/fetch.py:344-350`.
- **Issue:**
  - The hint hard-codes `FOR SCRIPT CFB_INFRA.CFBD_FETCH`. In the `TEST_` namespace that should be `TEST_CFB_INFRA`.
  - It tells the user to run `make grants`. On the real deployment ([20]), `make grants` never grants connection access, and the usual cause is a missing `CFBD_API`.
- **Fix:** have the adapter pass the schema (`INFRA_SCHEMA` is already a UDF argument source) or drop the schema from the hint. Word it as "the connection does not exist or this script has no ACCESS on it; create it (docs/user-guide.md §2) or, in a dedicated-owner namespace, run `make grants`".

### n2 [nit] An `http` address is accepted for the key-bearing connection
- **Location:** `http.py:36,171`.
- **Issue:** the spec says each request is an HTTPS GET. An owner who types `http://api.collegefootballdata.com` would send the key in cleartext.
- **Fix:** accept `http` only for IP-literal loopback hosts (the mock sidecar), and otherwise require `https`.

---

## 3. Catalog integrity: the version handshake checks inputs, not content

### m5 [minor] The catalog hash covers the spec, the overrides and `GENERATOR_VERSION`, not the generated catalog
- **Location:** `vs/generate.py:29,71`; `vs/cfbd_vs/catalog.py:348-355`; checked at `fetch.py:294-300` and `vs/host/deploy.py:140-146`.
- **Issue:**
  - Changing `vs/gen/*` or `vs/cfbd_vs/naming.py` changes `catalog.json` but keeps the hash, unless someone bumps `GENERATOR_VERSION="1"` by hand.
  - A partial deploy (adapter replaced, then the fetch replace fails) or a hand-deployed script could pair two different catalogs that carry the same hash. The handshake would then pass, and only `fetch.py:302-307` would catch missing columns, not type or path drift.
  - Conversely, a comment-only YAML edit changes the hash and forces a fetch replace, which drops the `TEST_` grant.
- **Fix:** hash the canonical catalog body instead: `to_json(indent=None)` with `catalog_hash=""`, then sha256. Keep `generator_version` as metadata only, and align the endpoint-catalog spec wording ("derived from spec, overrides and generator").

### m6 [minor] Truncating a parameter comment would cut the rules and gating text first
- **Location:** `vs/gen/tables.py:239-250,608-611`.
- **Issue:** the comment is assembled as description + rules + enum + feature and then cut from the end. A long spec description would push out exactly the parts the endpoint-catalog and gating scenarios require. Nothing is truncated today (the maximum is 585 characters), so the problem is latent.
- **Fix:** place the rules, allowed values and feature first, or truncate only the description to the remaining budget. Add a test with a 1,900-character description.

### m7 [minor] Table names are not checked against reserved words
- **Location:** `vs/gen/tables.py:154`; `vs/gen/mirrors.py:259,289-291`.
- **Issue:** columns get the `_` suffix, but a future endpoint `/order` or `/result` would yield an unquoted mirror DDL that fails to parse.
- **Fix:** fail generation, or apply the suffix, when `table.name in reserved`, and have `mirrors._check_identifier` reject reserved words.

### m8 [minor] `P_PRESEASON = FALSE` slips past `ratings_elo.week_or_preseason`
- **Location:** `vs/catalog-overrides.yaml:154`.
- **Issue:** a `one_of` rule counts any value as set, so `preseason=false` without `week` gets the silent latest-week answer the rule exists to prevent.
- **Fix:** add `{id: no_preseason_needs_week, type: requires_any, if: preseason, if_value: false, any_of: [week]}`.

---

## 4. Spec and docs: three statements promise more than the code does

### m9 [minor] Replay emits `REQ_ATTEMPTS = 0`; the spec allows only the source and the timing columns to differ
- **Location:** `vs/cfbd_vs/fetch.py:69,274-281`; recording format `replay.py:3-4`; spec `fetch-modes` "A recorded response replays identically", `fetch-log` background table (`REQ_ATTEMPTS`: "0 for STORED"); `docs/user-guide.md:217`.
- **Issue:**
  - Live rows carry `REQ_ATTEMPTS ≥ 1`, while replayed rows carry 0. The recording format does not store attempts, although `@RAW` emits them.
  - The integration test hides the difference by listing `REQ_ATTEMPTS` among the "timing" columns (`tests/it/fetch_helpers.py:41`).
- **Fix:** either store `attempts` in the recording and replay it, or amend the spec and the user guide ("0 for STORED and REPLAY"). Do not leave the test to redefine the spec.

### m10 [minor] The fetch-log helper records a stored empty slice as a live fetch
- **Location:** `vs/host/fetchlog.py:146-164`; the docstring at `:14-15` admits it; `docs/user-guide.md:387` does not mention it.
- **Issue:** in a read-through query, a final stored slice with 0 rows produces no staged row, so it is logged as `LIVE`/`REPLAY` with `ROWS_RETURNED = 0`. That overstates live calls in the very log that will be used to account for quota.
- **Fix:** have `capture_fetch_log` read the final `LOAD_LOG` keys for the planned slices (one query) and log those as `STORED`/0. At minimum, document the limitation in the user guide next to line 387.

### m11 [minor] The user guide overstates or misstates six behaviors

| `docs/user-guide.md` | Says | Code does | Fix |
|---|---|---|---|
| :351 | Errors "always start with `CFBD_VS` and the table" | Fetch-UDF errors arrive wrapped in Exasol's VM error and traceback, as `vs/host/deploy.py:453-462` itself handles | "Planning errors start with …; fetch errors contain `CFBD_VS.<TABLE>: …` inside the UDF error" |
| :353 | The 401/403 message "points at … `API_FEATURES`" | `http.py:249-257` names the key, the tier and the feature, not the property | Add the property name to the message, or fix the doc |
| :23, :93 | Hand-set properties stay; setting `API_FEATURES` by hand is allowed | Every `make deploy` with a reachable `/info` overwrites `API_FEATURES` (`deploy.py:386-391`) | Add "`make deploy` overwrites a hand-set `API_FEATURES`" |
| :344 | `META_SCHEMA` is the schema of `LOAD_LOG` and `FETCH_LOG` | The virtual schema uses it only for `LOAD_LOG`; `FETCH_LOG` takes an explicit argument (`fetchlog.py:193-199`) | "Schema of `LOAD_LOG`" |
| :217 | `REQ_ATTEMPTS` is 0 for `STORED` | Also 0 for `REPLAY` (m9) | See m9 |
| :8 | "Every `make` target runs through `uv run`" | `db-check` runs `python3 scripts/dbcheck.py` (`Makefile:16`) | Qualify the statement |

### n3 [nit] The doc describes predicate pushdown too narrowly
- **Location:** `docs/user-guide.md:233`.
- **Issue:** "pushes a predicate only when it is a plain comparison with a literal". In fact `IN`, `BETWEEN`, `LIKE`, `IS NULL` and column-to-column comparisons are pushed too (probe 1.4e). Only subqueries, functions and join conditions are not.
- **Fix:** reword it that way.

---

## 5. Integration tests (`tests/it`, `tests/mock_cfbd`)

**Key finding: the suite tests the scenarios meaningfully and its main paths cannot pass vacuously. The gaps are in the `CFBD_API` guard, a handful of negative assertions, and the one DoH end-to-end test that no target runs.**

Evidence the suite is sound (offline checks, no DB):

- `pytest tests/it --collect-only -m "it and not network"` collects 141 of 142 tests, and 102 of the plan's 103 Verification names exist (see n14).
- Mock-hit assertions have positive controls in the same session, so an unwired mock log would fail the suite. Examples: `test_info_is_read_live_through_the_mock_with_one_request` expects 1 request, and `test_live_one_authenticated_get_per_request` expects 2.
- Read-through proves "UDF not invoked" two ways:
  - the mock answers 404 for the stored slice;
  - the request log equals an exact list.
- Manifest assertions cannot pass without a manifest, because `fetchlog.parse_manifest` raises (`vs/host/fetchlog.py:118-130`).
- The all-table sweep inputs check out: for each of the 137 tables, the minimal filter plans exactly `fake.slice_key_for(table)` and flattens to at least one row.
- No path reaches CFBD:
  - connections point only at the mock IP or `cfbd-mock.invalid`;
  - the network test does a TLS handshake only, through `dns_udf.probe`;
  - the `API_FEATURES` sync reads the mock's `/info`.
- `tests/mock_cfbd/smoke_live.py` makes zero CFBD calls despite its name.

### m22 [minor] The `CFBD_API` hard guard has two gaps
- **Location:** `tests/it/guards.py:131-154,157-162`.
- **Issue:**
  - `schemas_on_real_connection` only sees schemas that have at least one row in `EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES`. A test virtual schema with no `CONNECTION_NAME` row falls back to the default `CFBD_API` and is not flagged, although the docstring at `:136-139` claims it is.
  - `check_property_change` refuses `CONNECTION_NAME='CFBD_API'` but allows `''`, which unsets the property and also falls back to `CFBD_API`.
  - Today the only backstop is that `CFB_TEST_OWNER` holds no grant on `CFBD_API`, so such a fetch would be denied at zero calls.
- **Fix:** list `EXA_ALL_VIRTUAL_SCHEMAS` names with the test prefix and flag every one that lacks a non-mock `CONNECTION_NAME` row. Refuse an empty or NULL `CONNECTION_NAME` in `check_property_change`.

### m23 [minor] Four negative assertions are too weak
- **Location and fix:**
  - The reader's direct call of `CFBD_FETCH` is only checked to fail (`test_key_protection_it.py:62-64`, `test_fetch_modes_it.py:363-364`), so a syntax or hash error would pass. Assert `"insufficient privileges"`, as `test_read_through_it.py:536` does.
  - `mock.doh_log() == []` with `DOH_URL='off'` (`test_fetch_modes_it.py:270`) is always true, because nothing points at the mock DoH. Drop it, or assert that the text lacks `"DoH lookup via"`.
  - "Single attempt" is asserted only for the certificate branch of the DoH failure (`:256-257`), not for the unreachable branch. Assert that the message has no `after N attempts`, or count connects.
  - The key-leak check over results has no non-empty guard (`test_key_protection_it.py:76`). Add `assert rows`.

### m24 [minor] The DoH end-to-end scenario never runs and would skip itself
- **Location:** `test_doh_fallback_resolves_and_verifies_tls` (`test_fetch_modes_it.py:204-229`).
- **Issue:**
  - It is marked `network`, which `make test-it` excludes (`Makefile:28`), and no other target runs it.
  - Lines 220-221 `pytest.skip` it once the sandbox resolves names, so after nano is fixed it stays green without testing anything.
- **Fix:**
  - Add a `make test-network` target, or list the test in checklist V.1.
  - Have the test inject a failing `original` resolver, so the DoH path always runs, and drop the skip.

### m25 [minor] Half of the schema-redirect scenario is conditional
- **Location:** `test_properties_it.py:240-250`.
- **Issue:** it checks the real `CFB_META.LOAD_LOG` only `if before is not None`, which is vacuous without a real deployment.
- **Fix:** keep the SQL-reference check (`:238`) as the proof, and state the condition in the docstring. Alternatively, require a real deployment as a stated prerequisite.

### m26 [minor] One connection can leak when a test run crashes
- **Location:** `hostname_connection` (`tests/it/fetch_helpers.py:389-407`) against `drop_namespace` and `leftovers` (`harness.py:83-146`).
- **Issue:** `hostname_connection` creates `CFBD_API_MOCK_HOSTNAME`, which holds the marker key and a script grant. The cleanup and leftover checks only cover `CFBD_API_MOCK`, so a killed run leaves the connection behind.
- **Fix:** add the connection to both lists.

### m27 [minor] Generated passwords can reach pytest output
- **Location:** `_create_users` and `_create_connection` (`harness.py:545-563`).
- **Issue:** `{password!q}` is formatted on the client. A failing `CREATE USER` raises `ExaQueryError` whose text includes the query, so the generated password lands in the test log. This contradicts the claim at `harness.py:9-10`.
- **Fix:** catch `pyexasol.ExaError` around these steps and re-raise with the step description only.

### m28 [minor] The integration helpers are duplicated many times
- **Locations:**
  - `failure_text` (`fetch_helpers.py:270`) and `error_of` (`plan_helpers.py:458`) do the same job.
  - `games_query` exists four times.
  - The `/games` slice-key builder exists four times: `fetch_helpers.py:279`, `plan_helpers.py:497,525`, `test_read_through_it.py:63`.
  - The minimal-filter query exists five times.
  - `explain_manifests` re-implements `Harness.pushdown_sql`.
  - `test_fetch_log_it.py:82` parses the manifest by hand instead of calling `fetchlog.parse_manifest`.
  - `NO_ACCESS` is defined twice.
  - `support._literal` re-implements SQL literals.
  - `sidecar.py:41-42` duplicates the names in `certs.py:6-7`.
- **Fix:** keep one helper each in `support.py` or `harness.py`, and reuse the production `parse_manifest` and `sqlrender.string_literal`.

### m29 [minor] The mock contains dead or superseded code
- **Locations and fix:**
  - `sidecar._current`, `log` and `script` have no callers (`sidecar.py:201,252,257`). Delete them.
  - `MockState.doh_connections` is used only by a unit test (`server.py:271-273`). Delete it, or keep it with that test as its stated purpose.
  - The `oversize_bytes` rule is unused by the integration tests (`server.py:161-163,187-191`). Delete it unless group 9 needs it.
  - The two `set_doh_answers` calls set answers that can never be served, and each costs one `podman exec` (`test_fetch_modes_it.py:250,267`). Delete them.
  - `smoke_live.py` is superseded by `test_harness_smoke.py`. It also hard-codes the DSN and the secrets path (`:19-21`) and uses `assert` for control flow (`:78-96`). Delete it, or route it through `grants.resolve_dsn` and `read_sys_password` with explicit checks.

### n14 [nit] One test name differs from the plan
- **Location:** `test_hoisted_endpoint_exposes_array_elements_directly` (`test_flattening_it.py:185`); the plan's Verification table says `…_elements`.
- **Fix:** align the two before V.2.

### n15 [nit] One test depends on the tests before it
- **Location:** `test_state_is_clean_again_after_the_tests_above` (`test_harness_smoke.py:73-77`).
- **Issue:** it passes trivially when it runs alone or in a different order.
- **Fix:** move the check into the `clean_state` fixture.

### n16 [nit] Guardrails in the test code
- **More than 3 arguments:**
  - `stage` (5);
  - `Harness.__init__` (9);
  - `MockHandle.script` (9);
  - `MockCfbd.__init__` (7);
  - `_launch` and `_run_command` (6 each).
- **Boolean flags:** `write_recording(wait)`, `wait_for_files(present)`, `set_echo_key(enabled)` and `first_value_column(varchar)`.
- **Long functions:** `test_aggregates_over_mixed_results` (about 70 lines) and `build_harness`.
- **Redundant marker:** every file sets `pytestmark = pytest.mark.it`, and `conftest.py:36-38` adds the same marker.
- **Fix:** split the flag functions, pass small dataclasses, and drop one of the two markers.

### n17 [nit] Stray comments, a print and contradictory docstrings
- **Location:** `plan_helpers.py:24` and the banners in `test_read_through_it.py:60,96,160,220,256` (also n7); a `print` in `test_fanout_it.py:127`; `harness.py:152` puts an admin call at 1.5–2 s, while `sidecar.py:8` says about 0.2 s.
- **Fix:** remove the comments and the print, and reconcile the two docstrings.

### n18 [nit] Several behaviours are tested twice, at DB-staging cost
- **Location:**
  - 401/403: `test_fetch_modes_it.py:181-191` and `test_gating_it.py:222-234`.
  - [21] stored edge cases: `test_pushdown_it.py:239-271` and `test_read_through_it.py:259-285,370-393`.
  - Stored-only without a budget: `test_fanout_it.py:84-90` and `test_read_through_it.py:573-610`.
- **Fix:** keep one test per behaviour, or say in the docstring why the overlap is deliberate.

A documented deviation, not a finding: `test_defaults_apply` (`test_properties_it.py:182-199`) unsets four properties instead of creating a fresh schema. As a result, the default values of the three schema properties and `REPLAY_PATH` are covered only by unit tests.

---

## 6. Dead code and obsolete tests: about 15 symbols are reachable only from tests

### m12 [minor] Dead runtime code ships in both scripts
- **Locations:**
  - `sqlrender.json_comment` (`vs/cfbd_vs/sqlrender.py:201-210`). It is a second manifest escaper next to the one that ships, `plan.manifest_comment`. 209 seeded fuzz tests target it (`tests/unit/test_sqlrender.py:779-821`), while the real manifest has only example tests.
  - `rules.check_request` (`rules.py:116-118`; its docstring is at `:7`).
  - `rules.Evaluation.defaulted` (`rules.py:50-55,83-91`), which is never read.
  - `ReplayStore.exists` (`replay.py:155-161`).
  - `resolver.uninstall` (`resolver.py:215-219`), which only tests use.
  - `prose_lint.check_prose`, `find_prose_rules` and `ProseLintError` (`vs/gen/prose_lint.py:59-73,89-93`).
  - `overrides.load_overrides` (`vs/gen/overrides.py:168-170`).
  - The `SYSTEM_ONLY_RESOLVER_SOURCE` fallback in `scripts/dns.py:34-45,75-79`, obsolete since task 4.2. If the path ever breaks, it silently disables DoH in `make dns-check`, and three tests pin it (`tests/unit/test_dns_check.py:101-103,113-118,139-143`).
- **Fix:**
  - Delete these and their tests.
  - Move the hostile-payload fuzz onto `plan.manifest_comment`.
  - Keep `resolver.uninstall` only if its docstring says it is test teardown.
  - Make `dns.py` raise when `resolver.py` is missing.

### m13 [minor] Dead catalog fields inflate both embedded catalogs
- **Location:** `vs/cfbd_vs/catalog.py`.
  - `Param.default`: 76 parameters. It duplicates the `default` rule's `details.value`.
  - `Param.description`: about 16 K characters. It duplicates the `P_` comment.
  - `Table.row_cap`: it duplicates the `row_cap` rule. Its only reader is a test filter at `tests/it/test_read_through_it.py:241`.
  - `Table.hoisted`: informational only.
- **Fix:** drop the first three, filtering on the rule type in that test, and document `hoisted` as informational.

### n4 [nit] Recording write helpers sit in the read-only runtime
- **Location:** `replay.encode_recording`, `ReplayStore.save`, `LocalFileSystem.write_text` and `assemble_parts` (`replay.py:66-104,163-200`).
- **Issue:** they are bundled into the fetch UDF, where `/buckets` is read-only, and only the IT harness uses them, plus the not-yet-written `scripts/record.py`. Keeping the format in one module is right.
- **Fix:** none required. Optionally move the write side to `vs/host`.

### n5 [nit] `make record` points at a missing module
- **Location:** `Makefile:52-53` calls `scripts.record`, which does not exist yet (task 9.1).
- **Fix:** remove the target until 9.1, or have it print "not implemented (task 9.1)".

---

## 7. Guardrails and DRY: duplication is the main debt

### m14 [minor] The SQL type grammar is parsed in four places with four different rules
- **Location:**
  - `sqlrender.py:52-55`: strict.
  - `adapter.py:71-72`: any digit count.
  - `flatten.py:36-37`: tolerates `\s*` and a missing character set.
  - `constraints.py:399,491`.
- **Issue:** a catalog type accepted by one module can be read differently by another.
- **Fix:** add one `naming.parse_sql_type()` that returns a small dataclass, and use it everywhere.

### m15 [minor] Constants and helpers are duplicated across modules

| What | Where |
|---|---|
| sys password source | `scripts/dns.py:21,151-158` reads `~/.exasol-starter-kit/credentials/personal_sys_password`; `scripts/grants.py:36-37,230-240` (used by deploy and the IT harness) reads `~/.exasol/personal/deployments/local/secrets.json`. If they diverge, the 9.0 gate (`make dns-check`) fails while deploy works |
| DSN default and resolution | `dbcheck.py:7`, `dns.py:25,193` (environment only), `grants.py:39,386-389` (environment, then `.env`) |
| DoH default | `dns.py:26` and `resolver.py:22` |
| `string_literal` | `vs/host/deploy.py:196-197` copies `sqlrender.string_literal` without its NUL check |
| `MAX_COMMENT_LENGTH` | `tables.py:50` and `adapter.py:42` |
| `FEATURES_PROPERTY` | `tables.py:54` and `rules.py:20` |
| `"CFBD_VS"` error prefix | `ERROR_PREFIX` or `SCHEMA_NAME` defined separately in `adapter.py`, `fetch.py`, `filters.py`, `flatten.py`, `plan.py`, `properties.py` and `rules.py` |
| `KNOWN_PROPERTIES` | Repeats the keys of `_SPECS` (`properties.py:33-45,194-235`) |
| `$ref` resolution | Three resolvers with different depth limits and escaping: `schema_walk.py:174-184`, `overrides.py:197-257`, `prose_lint.py:108-113` |

- **Fix:**
  - `dns.py` should import `grants.read_sys_password` and `grants.resolve_dsn`, and `resolver.DEFAULT_DOH_URL`.
  - Move the shared constants into `naming.py`, and have `deploy.py` import `sqlrender.string_literal`.
  - Build the overrides `SpecIndex` from `read_operations()`, so a single walker decides which paths exist.

### m16 [minor] The adapter bundle carries the whole fetch stack
- **Location:** `properties.py:20-22` and `pushdown_sql.py:36` import `fetch` (and through it `http`, `flatten`, `replay` and `resolver`) only for mode, source and default constants.
- **Issue:** this couples planning to the fetch implementation and adds about 70 KB to the adapter, the larger of the two scripts.
- **Fix:** move `MODE_*`, `SOURCE_*` and the defaults into a small `vs/cfbd_vs/constants.py`, or into `naming`.

### m17 [minor] Several functions take more than three arguments or a boolean flag

**More than three arguments (DI seams with defaults are acceptable; the rest should take a parameter object):**
- `vs/host/deploy.py:217-226` (`_Deployment`, 7).
- `pushdown_sql.pushdown_sql` (5) and `_fetch_call` (4).
- `fetchlog.capture_fetch_log` (6).
- `overrides._RuleBuilder._register` (6) and the 8 rule builders (4 each).
- `schema_walk._walk*` (4 each). Bundle `(location, stack, depth)` into a `_Cursor`.
- `scripts/dns_udf.probe` (8).

**Boolean flag parameters:**
- `bundle.fetch_header` and `fetch_script(..., replace: bool)` (`vs/bundle.py:136-151`). Split them into `create_fetch_script` and `replace_fetch_script`.
- `tables._hoisted` and `_curated(root_array: bool)` (`tables.py:312,354`). Pass the root path instead.

**Large classes:** `tables._EndpointDeriver` (387 lines) and `overrides._Parser` (322 lines) mix several responsibilities. Split them the way `_RuleBuilder` already is.

### n6 [nit] `assert` statements guard production invariants
- **Location:** `fetch.py:267,477,495`.
- **Fix:** raise `FetchEntryError` instead, or rely on the types.

### n7 [nit] Inline comments break the comments policy
- **Location:** `scripts/grants.py:383` and `scripts/dns.py:180,184` (`# pyexasol ships no types` after the `type: ignore`); `tests/it/plan_helpers.py:24`; the section dividers `# --- … ---` in `tests/unit/test_constraints.py`, `test_fetchlog.py` and `tests/it/test_read_through_it.py`.
- **Fix:**
  - Keep the bare `# type: ignore[...]` and drop the trailing text.
  - Use a `NamedTuple` instead of the explanatory comment in `plan_helpers.py`.
  - Split the large test files instead of using dividers.

### n8 [nit] Smaller cleanups
- **Location and fix:**
  - `meta_ddl_names()` (`vs/host/metaddl.py:94-96`) wraps a constant; use the constant directly.
  - `FetchLogRow(logged_at, context, …, None, None, None, 0, None)` (`fetchlog.py:151-164`) passes ten positional arguments; use keywords.
  - The bundle is built three times per `make deploy` (`Makefile:43` `bundle`, `scripts/deploy.py:126`, `vs/host/deploy.py:108`); pass the built bundle through.

---

## 8. Unit-test quality: strong suite with a few blind spots

### m18 [minor] The "clean interpreter" bundle test does not enforce the stdlib-only rule
- **Location:** `tests/unit/test_bundle.py:21-31,57,76`.
- **Issue:** `sys.executable -I` is the uv venv interpreter, and `-I` keeps the venv's site-packages. `import yaml, pyexasol` succeeds there, so plan 5.1 is not enforced. The rule holds today, as the AST scan confirmed.
- **Fix:** add `-S`, and assert that every top-level module in `sys.modules` is in `sys.stdlib_module_names ∪ {"vs", "__main__"}`. Alternatively, add the AST import scan as a unit test.

### m19 [minor] The synthetic determinism test compares a cached value with itself
- **Location:** `tests/unit/test_synthetic.py:80-83` against the `@lru_cache` in `tests/synthetic/fake.py:226-235`.
- **Issue:** the test can never fail. The property that matters is that the mock sidecar and the harness produce the same bytes, and that is untested.
- **Fix:** call `cache_clear()` between the calls, and add one subprocess comparison under a different `PYTHONHASHSEED`.

### m20 [minor] Coverage and the resolver guard leave out security-relevant code
- **Location:** `pyproject.toml:84-90` and `Makefile:36-39`; `tests/unit/test_resolver.py:300-318`.
- **Issue:**
  - Coverage leaves out `vs/host/*`, `vs/bundle.py`, `vs/generate.py` and `scripts/*` (grants, deploy, dns), so the reported 98 %/94 % says nothing about the grants and deploy paths.
  - The "only `resolver.py` touches `getaddrinfo`" scan covers only `vs/`. `scripts/dns.py:43` and `scripts/dns_udf.py:86,98` call it too.
- **Fix:**
  - Add the host code to coverage, with its own `fail_under`.
  - Extend the scan to `scripts/` with an explicit allowlist for `dns_udf.probe`. The `dns.py` stub goes away with m12.

### m21 [minor] Five CLI tests duplicate the scenario-named ones
- **Location:** `tests/cli/test_generate_cli.py:144,180,193,243`.
- **Issue:** `:193` asserts only the substrings `"WIDGET"` and `"DATE"`, and `"DATE"` also matches `DATE_`. The others are subsets of `:315`, `:346`, `:362` and `:378`.
- **Fix:**
  - Delete them, and fold the 93-count assertion of `:243` into `:346`.
  - Assert the table name in the column-collision test (`:400-403`).
  - Assert each specific reason in the invalid-overrides test (`:174-176`).

### n9 [nit] Test hygiene
- **Location and fix:**
  - Timing-based concurrency test `test_fetch_entry.py:672-697`: use a `threading.Barrier`.
  - Duplicated test doubles (`FakeClock`, `FakeDatabase`, `make_table`/`make_param` in 5 files): add a shared `tests/unit/support.py`.
  - The literal `2000` in `test_adapter_entry.py:199-201` and `test_tables.py:667`: import the constant.
  - About 2,600 of the 5,589 tests are seeded fuzz cases. That is legitimate, but report example tests and seeded cases separately.
  - `tests/synthetic/fake.py:418-440` uses the real 2023 SEC Championship score: use clearly fictional values, since fixtures must be synthetic.

### n10 [nit] The probe spike's cleanup can stop halfway
- **Location:** `docs/spikes/s2_platform_probe.py:2180-2186`.
- **Issue:** one failing cleanup step skips the remaining steps, `save()` and `sys.close()`.
- **Fix:** wrap each step on its own. In `s2-platform-probe.md` row (j), say "`*/` is escaped as `*\/`", which is what `plan.manifest_json` does.

---

## 9. Optimizations

### n11 [nit] The fetch UDF embeds comments it never reads
- **Location:** the fetch bundle (1,434,704 characters).
- **Issue:** the 425,439 comment characters and the repeated meta-column comments (48 K) are dead weight for `CFBD_FETCH`.
- **Fix:** embed a comment-free catalog in the fetch UDF. The handshake compares the hash, not the content, so compute the hash first (m5). Rendering the 7 meta comments from `naming` instead of storing them on 137 tables also frees about 48 K in the adapter, which is the binding script.

### n12 [nit] A cold cache can trigger several DoH lookups at once
- **Location:** `resolver.py:140-153`.
- **Issue:** up to `MAX_PARALLEL_FETCHES` threads can each send a DoH request for the same host on a cold cache. No quota is affected.
- **Fix:** a `threading.Lock` around the cache fill gives exactly one lookup per host.

### n13 [nit] Small runtime costs
- `Catalog.table()` is a linear search over 137 tables (`catalog.py:302-307`). Build a dict once.
- `Adapter` rebuilds its handler map on every call (`adapter.py:114-123`).

Both are negligible next to HTTP.

---

## Fix order

1. **M1** before any live call: fail fast in the pool, with a width-2 test.
2. **m22, m26, m27 and m23** before the first `make test-it`: close the guard gaps, the leak on a crash and the weak negatives, so the first run's verdict can be trusted.
3. **m2, m3, m9 and m15 (password source)** before group 9: they affect what the live run records and whether the 9.0 gate (`make dns-check`) passes.
4. **m5 and m24** before recording: content hash, and a target that runs the DoH test.
5. The rest as cleanup alongside tasks 8.2–8.4.
