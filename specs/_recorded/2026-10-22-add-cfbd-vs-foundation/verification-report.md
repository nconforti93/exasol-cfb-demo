# Verification Report: add-cfbd-vs-foundation

**Generated:** 2026-10-22

## Verdict

| Result | Details |
|--------|---------|
| **PASS (with three open items)** | Every automated check is green, and all 102 spec scenarios map to a passing test (101 passed in this run; 1 needs the owner). 10 of 11 manual rows passed (6 on AWS, 3 on local replay, and the properties row on local because auto mode blocked it on AWS). The read-through row was not run. 0 CFBD calls were spent; the ledger stands at 21 of 50. |

| Check | Status |
|-------|--------|
| Build | ✓ |
| Tests | ✓ (5,790 unit + CLI, 141 integration) |
| Lint | ✓ (ruff, mypy strict) |
| Format | ✓ |
| Scenario Coverage | ✓ (102 of 102 have a test; 1 test is `network`-marked and not run) |
| Manual Tests | ✓ with exceptions (see Manual Tests) |

**Open items (none is a failure):**

1. **7.4 network DoH test not run, blocked on the owner.** `make test-network` (`test_doh_fallback_resolves_and_verifies_tls`) was blocked by auto mode earlier ("Interfere With Workloads"). Run it with `! make test-network`. It needs HTTPS egress to 1.1.1.1 and a TLS handshake with the CFBD host. It makes no API call. The AWS `make dns-check` (9.0) resolved through the system resolver, so the DoH fallback has never been exercised end to end against the real endpoint. Task 7.4 stays `[~]` for this reason.
2. **Manual row `read-through` not run.** AWS holds no stored slices now (9.5b staged one and removed it afterwards), so the query would have made a live call. Staging a slice on local (an INSERT into `CFB_CORE.GAMES` and `CFB_META.LOAD_LOG`, then a live-mode query) was blocked by auto mode, so I did not work around it. The behaviour is covered by the 11 read-through integration tests (all pass in this run) and by the measured 9.5b result in `docs/cfbd-api-measurements.md`: all rows `STORED`, `REQ_ATTEMPTS` 0, `remainingCalls` unchanged.
3. **Manual row `properties` ran on local, not AWS.** The `ALTER VIRTUAL SCHEMA CFBD_VS SET MAX_CALLS_PER_QUERY='0'` statement on the shared AWS deployment was blocked by auto mode ("Modify Shared Resources"). It passed on the local real-namespace deployment instead.

## Test Evidence

### Coverage

| Type | Coverage % |
|------|------------|
| Unit + CLI, `vs/cfbd_vs` | 98 % (2,889 statements, 37 missed) |
| Unit + CLI, `vs/gen` | 94 % (1,413 statements, 70 missed) |
| Integration | not measured (the UDF code runs inside the database sandbox; the gate is unit + CLI only, `fail_under = 90`) |

### Test Results

| Type | Run | Passed | Ignored |
|------|-----|--------|---------|
| Unit + CLI (`make test`) | 5,790 | 5,790 | 0 |
| Integration (`make test-it`, fresh run, 779.6 s, under the `cfb-it.lock` flock) | 141 | 141 | 1 deselected (`network`: `test_doh_fallback_resolves_and_verifies_tls`) |

### Manual Tests

Targets: **AWS** is the real `CFBD_VS` on Exasol 2026.1.1 (3 nodes). **Local** is the real namespace deployed on Personal local with `make deploy TARGET=local ARGS="--no-sync-api-features"`, in `FETCH_MODE='replay'`, using the recordings 9.4 published to local BucketFS. AWS has no BucketFS replay path, so all replay rows ran on local. Calls spent: **0** (ledger before and after: 21 of 50 for the plan, 21 of 400 for October; `/info` `remainingCalls` 2,983 before and after).

| Test | Target | Result |
|------|--------|--------|
| endpoint-catalog: table count and `P_` columns of `GAMES_TEAMS` | AWS | ✓ 137 tables = 93 endpoint + 44 child (`catalog.json`). `P_YEAR`, `P_WEEK`, `P_TEAM`, `P_CONFERENCE`, `P_CLASSIFICATION`, `P_SEASON_TYPE`, `P_ID` with types and comments carrying `games_teams.year_or_id` and `games_teams.week_team_or_conference_with_year` |
| flattening: polls of 2024 rankings (replay) | local | ✓ 6 polls: AFCA D-II Coaches 325, AFCA D-III Coaches 326, AP Top 25 426, Coaches Poll 425, FCS Coaches 376, Playoff Committee Rankings 150 |
| pushdown: contradictory `P_YEAR` filters in `EXPLAIN VIRTUAL` | AWS | ✓ manifest `planned_requests: 0`, `slice_keys: []`, `sources: []`; the SQL ends in `FROM DUAL WHERE FALSE` and contains no `CFBD_FETCH` |
| requirement-rules: `PLAYS WHERE P_YEAR=2024` | AWS | ✓ `RuleViolation: CFBD_VS.PLAYS: rule plays.spec_required: required parameter not set: P_WEEK (the API spec marks it as required)`; `remainingCalls` unchanged |
| gating: `GAMES_WEATHER WHERE P_YEAR=2024` | AWS | ✓ `rule gating.weather: this table needs the CFBD feature 'weather', which is not in the virtual schema property API_FEATURES (currently: none)` (key is Academic tier, all feature flags false) |
| fan-out: manifest and over-budget query | AWS | ✓ manifest: 6 slice keys (2019 to 2021 x weeks 8, 9), `budget: 10`, `sources: ["STORED","LIVE"]`. `GAMES WHERE P_YEAR BETWEEN 2000 AND 2024`: `BudgetError: 25 requests are planned but MAX_CALLS_PER_QUERY is 10`, no call made |
| read-through: stored year, `REQ_SOURCE` groups | n/a | **not run** (open item 2) |
| fetch-log: `SELECT DISTINCT REQ_*` for 2024 games (replay) | local | ✓ one line: `/games?seasonType=both&year=2024`, `REPLAY`, 200, attempts 1, 2028 ms, 3,801 rows |
| fetch-modes: `FETCH_MODE='replay'`, count of 2024 games | local | ✓ 3,801, equal to the live recording (S1 and 9.5b: 3,801). "Network disconnected" was **not** simulated: replay needs no network by construction, and `test_replay_without_network_or_key` covers it (passes) |
| key-protection: temporary reader with `CREATE SESSION` and role `CFB_READER`, then dropped | AWS | ✓ direct `CFB_INFRA.CFBD_FETCH(...)` calls (with and without `FROM DUAL`): `insufficient privileges for calling script`. `EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES` returned no rows for the reader (no key). The reader's zero-call contradiction query and `EXPLAIN VIRTUAL` ran. User `CFB_V3_TMP_READER` was dropped (0 left). The key was never read or printed |
| properties: `MAX_CALLS_PER_QUERY='0'` | local (AWS blocked) | ✓ `PropertyError: CFBD_VS: property MAX_CALLS_PER_QUERY must be an integer from 1 to 1000, got '0'`; the property stayed unset (default 10). On AWS the property list is `CONNECTION_NAME`, `INFRA_SCHEMA`, `META_SCHEMA`, `CORE_SCHEMA`, so the default 10 applies (the budget error above states 10) |

## Tool Evidence

### Linter

```
$ make lint
ruff check .            All checks passed!
ruff format --check .   170 files already formatted
mypy                    Success: no issues found in 113 source files
```

### Formatter

```
$ uv run ruff format --check .
170 files already formatted
```

### Checklist rows (V.1)

| Step | Command | Result |
|------|---------|--------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | ✓ `catalog b6389f65c48c: 93 endpoint tables, 44 child tables, 0 excluded`; diff exit 0 |
| Build | `uv run python -m vs.bundle` | ✓ `cfbd_adapter.sql` (1,508,380 characters) and `cfbd_fetch.sql` (1,410,141) written. The plan names `vs/bundle.py` as a script; the real entry is `python -m vs.bundle` |
| Test | `make test` | ✓ `5790 passed in 94.16s` |
| Integration | `flock … make test-it` | ✓ `141 passed, 1 deselected in 779.55s (0:12:59)` |
| Lint, Format | `make lint`, `ruff format --check .` | ✓ see above |
| Coverage | `make coverage` | ✓ `vs/cfbd_vs` 98 %, `vs/gen` 94 % (both at least 90 %) |
| Deploy (AWS) | `make deploy TARGET=aws` | ✓ exit 0: schemas ready; meta tables 0 created, 2 existing and matching; mirror tables 0 created, 125 existing and matching; fetch UDF `unchanged, kept as is (its grants stay)`; virtual schema refreshed, properties none changed; `API_FEATURES` synced: none. The adapter is always `CREATE OR REPLACE`d, so it reports `replaced`, which is not drift |
| Deploy (AWS) | `SELECT * FROM CFBD_VS.INFO` | ✓ Academic, monthly limit 3,000, remaining 2,983, used 17, reset 2026-11-01, shared pool, products cfb and cbb, every `FEATURES_*` column false |
| DNS | 9.0 result (cited, not rerun) | ✓ AWS `make dns-check` passed via the **system** resolver; the DoH path was not exercised (open item 1) |
| Ledger | `make record ARGS=--ledger` | ✓ `add-cfbd-vs-foundation: 21 of 50 calls`; `2026-10, all plans: 21 of 400 calls`; last `/info` remaining 2,983 |
| Secrets | `git grep -n -I -e 'IDENTIFIED BY' -e 'Bearer ' -- . ':!docs/user-guide.md' ':!specs'` | ✓ no key material. Hits are `{password!q}` and `{key}` placeholders, synthetic test keys (`probe-marker-key`, `KEY`, `SECRET` constants), the `Bearer ` header builder in `vs/cfbd_vs/http.py`, and the throwaway probe in `docs/spikes/s2_platform_probe.py` |
| Network DoH | `make test-network` | **not run, blocked on owner** (open item 1) |

## Scenario Coverage

Method: the 102 `### Scenario:` headings of the current spec deltas (`cfbd-vs/*/spec.md`, after decisions [18] to [26]) were matched by title against the 102 rows of the plan's Scenario Coverage table (102 of 102 identical, none missing on either side). Each named test was then found by name in `pytest --collect-only` (5,932 tests) and in the file the plan names. The integration tests all passed in the 141-test run; the unit and CLI tests all passed in the 5,790-test run.

Findings:
- **No scenario lacks a test.**
- **Name drift in the plan table.** The plan names `test_hoisted_endpoint_exposes_array_elements`; the test is `test_hoisted_endpoint_exposes_array_elements_directly`. The table below carries the real name. The plan text is not edited.
- **Not run.** `fetch-modes: DNS over HTTPS supplies the address…` is carried by the one `network`-marked test (open item 1).
- **Edited-spec checks.** [22] is asserted by `test_gating_it.py` ("the key has no access to this endpoint"). [21] is asserted through the manifest `sources` in `test_read_through_it.py` and the unit test for the fetch log. [24] is asserted because `REQ_ATTEMPTS` is no longer an ignored column in `tests/it/fetch_helpers.py`, so "replays identically" compares it. [19] `REQ_SOURCE` is asserted in `test_fetch_log_it.py`. [23] (key-only body redaction; "standard bearer Smith" unchanged) is pinned by unit tests in `test_http.py` and `test_fetch_entry.py`, with the integration scenario `test_key_redacted_from_errors_and_bodies`. [25] (hash over content, rankings parent keys) is covered by `test_catalog_it.py`, `test_flattening_it.py` and the unit tests.

| Domain | Feature | Scenario | Test Location | Test Name | Passes |
|--------|---------|----------|---------------|-----------|--------|
| cfbd-vs | endpoint-catalog | Every GET operation becomes a virtual table | `tests/it/test_catalog_it.py` | `test_every_get_operation_is_a_table` | Pass |
| cfbd-vs | endpoint-catalog | Invalid overrides stop generation | `tests/cli/test_generate_cli.py` | `test_invalid_overrides_stop_generation` | Pass |
| cfbd-vs | endpoint-catalog | Prose-only rules must be covered by an override | `tests/cli/test_generate_cli.py` | `test_uncovered_prose_rules_stop_generation` | Pass |
| cfbd-vs | endpoint-catalog | Generation is deterministic and the committed catalog is current | `tests/cli/test_generate_cli.py` | `test_generation_deterministic_and_committed_current` | Pass |
| cfbd-vs | endpoint-catalog | Table names derive from the endpoint path | `tests/it/test_catalog_it.py`, `tests/cli/test_generate_cli.py` | `test_table_names_derive_from_path`, `test_colliding_table_names_stop_generation` | Pass |
| cfbd-vs | endpoint-catalog | Every API parameter becomes a typed parameter column | `tests/it/test_catalog_it.py` | `test_parameters_become_typed_p_columns` | Pass |
| cfbd-vs | endpoint-catalog | Columns carry the API documentation and the rules | `tests/it/test_catalog_it.py` | `test_column_comments_carry_docs_and_rules` | Pass |
| cfbd-vs | endpoint-catalog | Column name collisions stop generation | `tests/cli/test_generate_cli.py` | `test_column_collisions_stop_generation` | Pass |
| cfbd-vs | endpoint-catalog | Reserved words never become column names | `tests/it/test_catalog_it.py` | `test_reserved_word_columns_get_trailing_underscore` | Pass |
| cfbd-vs | endpoint-catalog | Refreshing the virtual schema reproduces the same tables | `tests/it/test_catalog_it.py` | `test_refresh_reproduces_metadata` | Pass |
| cfbd-vs | endpoint-catalog | Adapter and fetch UDF from different catalog versions are refused | `tests/it/test_catalog_it.py` | `test_catalog_version_mismatch_refused` | Pass |
| cfbd-vs | flattening | Nested objects become prefixed columns | `tests/it/test_flattening_it.py` | `test_nested_objects_become_prefixed_columns` | Pass |
| cfbd-vs | flattening | Arrays of objects become child tables | `tests/it/test_flattening_it.py` | `test_arrays_of_objects_become_child_tables` | Pass |
| cfbd-vs | flattening | Child rows join to exactly one parent row | `tests/it/test_flattening_it.py` | `test_child_rows_join_exactly_one_parent` | Pass |
| cfbd-vs | flattening | Hoisted endpoints expose the array elements directly | `tests/it/test_flattening_it.py` | `test_hoisted_endpoint_exposes_array_elements_directly` | Pass |
| cfbd-vs | flattening | Arrays of scalars become JSON text | `tests/it/test_flattening_it.py` | `test_scalar_arrays_become_json_text` | Pass |
| cfbd-vs | flattening | Very large objects keep a curated subset plus the raw JSON | `tests/it/test_flattening_it.py` | `test_large_objects_curated_plus_raw_json` | Pass |
| cfbd-vs | flattening | Date-time values become UTC timestamps | `tests/it/test_flattening_it.py` | `test_date_time_values_become_utc_timestamps` | Pass |
| cfbd-vs | flattening | Missing and null fields become NULL | `tests/it/test_flattening_it.py` | `test_missing_and_null_fields_are_null` | Pass |
| cfbd-vs | flattening | Object-root responses become one row | `tests/it/test_flattening_it.py` | `test_object_root_response_is_one_row` | Pass |
| cfbd-vs | flattening | Empty responses return no rows | `tests/it/test_flattening_it.py` | `test_empty_response_returns_no_rows` | Pass |
| cfbd-vs | flattening | Values that do not fit their column fail loudly | `tests/it/test_flattening_it.py` | `test_values_that_do_not_fit_fail_loudly` | Pass |
| cfbd-vs | flattening | Every table returns typed rows for a schema-conforming response | `tests/it/test_flattening_it.py` | `test_all_tables_replay_sweep` | Pass |
| cfbd-vs | pushdown | Equality on a parameter column becomes a request parameter | `tests/it/test_pushdown_it.py` | `test_equality_becomes_request_parameter` | Pass |
| cfbd-vs | pushdown | Rows echo the parameters of their request | `tests/it/test_pushdown_it.py` | `test_rows_echo_request_parameters` | Pass |
| cfbd-vs | pushdown | Bounded integer ranges are enumerated | `tests/it/test_pushdown_it.py` | `test_bounded_ranges_are_enumerated` | Pass |
| cfbd-vs | pushdown | Open-ended ranges are rejected | `tests/it/test_pushdown_it.py` | `test_open_ranges_rejected` | Pass |
| cfbd-vs | pushdown | IN lists produce one request per value | `tests/it/test_pushdown_it.py` | `test_in_list_one_request_per_value` | Pass |
| cfbd-vs | pushdown | OR on one parameter acts like IN | `tests/it/test_pushdown_it.py` | `test_same_parameter_or_acts_like_in` | Pass |
| cfbd-vs | pushdown | OR across parameters is rejected | `tests/it/test_pushdown_it.py` | `test_cross_parameter_or_rejected` | Pass |
| cfbd-vs | pushdown | Unsupported predicates on parameter columns are rejected | `tests/it/test_pushdown_it.py` | `test_unsupported_parameter_predicates_rejected` | Pass |
| cfbd-vs | pushdown | Parameter values must be literals | `tests/it/test_pushdown_it.py` | `test_parameter_values_must_be_literals` | Pass |
| cfbd-vs | pushdown | Predicates on response columns filter rows without changing requests | `tests/it/test_pushdown_it.py` | `test_response_predicates_are_residual` | Pass |
| cfbd-vs | pushdown | Contradictory filters return no rows and make no calls | `tests/it/test_pushdown_it.py` | `test_contradictions_return_no_rows_no_calls` | Pass |
| cfbd-vs | pushdown | Contradictions are decided before requirement rules | `tests/it/test_pushdown_it.py` | `test_contradiction_before_rules` | Pass |
| cfbd-vs | pushdown | Enum parameter values are validated | `tests/it/test_pushdown_it.py` | `test_enum_values_validated` | Pass |
| cfbd-vs | pushdown | Parameter values are passed safely | `tests/it/test_pushdown_it.py` | `test_parameter_values_passed_safely` | Pass |
| cfbd-vs | pushdown | Aggregates over a virtual table are computed by Exasol | `tests/it/test_pushdown_it.py` | `test_aggregates_computed_by_exasol` | Pass |
| cfbd-vs | requirement-rules | A missing hard-required parameter is rejected | `tests/it/test_rules_it.py` | `test_missing_hard_required_rejected` | Pass |
| cfbd-vs | requirement-rules | A "required unless" rule accepts either parameter | `tests/it/test_rules_it.py` | `test_required_unless_accepts_either` | Pass |
| cfbd-vs | requirement-rules | A conditional requirement applies when its trigger is set | `tests/it/test_rules_it.py` | `test_conditional_requirement_with_trigger` | Pass |
| cfbd-vs | requirement-rules | Mutually exclusive parameters are rejected | `tests/it/test_rules_it.py` | `test_mutually_exclusive_rejected` | Pass |
| cfbd-vs | requirement-rules | A value-dependent requirement is enforced | `tests/it/test_rules_it.py` | `test_value_dependent_requirement` | Pass |
| cfbd-vs | requirement-rules | Parameter bounds are enforced | `tests/it/test_rules_it.py` | `test_parameter_bounds_enforced` | Pass |
| cfbd-vs | requirement-rules | Hidden defaults are sent explicitly | `tests/it/test_rules_it.py` | `test_hidden_defaults_sent_explicitly` | Pass |
| cfbd-vs | requirement-rules | A hidden default can become a requirement | `tests/it/test_rules_it.py` | `test_hidden_default_becomes_requirement` | Pass |
| cfbd-vs | requirement-rules | Row-capped endpoints require their completeness parameter | `tests/it/test_rules_it.py` | `test_row_capped_requires_completeness_param` | Pass |
| cfbd-vs | requirement-rules | Child tables enforce the rules of their endpoint | `tests/it/test_rules_it.py` | `test_child_tables_enforce_endpoint_rules` | Pass |
| cfbd-vs | requirement-rules | Rule violations are visible in EXPLAIN VIRTUAL at zero calls | `tests/it/test_rules_it.py` | `test_rule_violation_in_explain_zero_calls` | Pass |
| cfbd-vs | gating | Gated endpoints exist as documented tables | `tests/it/test_gating_it.py` | `test_gated_tables_exist_documented` | Pass |
| cfbd-vs | gating | A gated table refuses queries when the key lacks the feature | `tests/it/test_gating_it.py` | `test_gated_refused_without_feature` | Pass |
| cfbd-vs | gating | A gated table works when the key has the feature | `tests/it/test_gating_it.py` | `test_gated_allowed_with_feature` | Pass |
| cfbd-vs | gating | Gating fails closed while features are unknown | `tests/it/test_gating_it.py` | `test_gating_fails_closed` | Pass |
| cfbd-vs | gating | An access refusal from the API is reported clearly | `tests/it/test_gating_it.py` | `test_api_access_refusal_reported` | Pass |
| cfbd-vs | fan-out | A range and an IN list fan out into their product | `tests/it/test_fanout_it.py` | `test_range_and_in_fan_out_product` | Pass |
| cfbd-vs | fan-out | Duplicate values are requested once | `tests/it/test_fanout_it.py` | `test_duplicate_values_requested_once` | Pass |
| cfbd-vs | fan-out | An over-budget query is rejected before any call | `tests/it/test_fanout_it.py` | `test_over_budget_rejected` | Pass |
| cfbd-vs | fan-out | A query exactly at the budget is allowed | `tests/it/test_fanout_it.py` | `test_at_budget_allowed` | Pass |
| cfbd-vs | fan-out | Stored slices count toward the budget | `tests/it/test_fanout_it.py` | `test_stored_slices_count_toward_budget` | Pass |
| cfbd-vs | fan-out | The budget applies to each virtual-table access | `tests/it/test_fanout_it.py` | `test_budget_per_table_access` | Pass |
| cfbd-vs | fan-out | The requests of one query run concurrently | `tests/it/test_fanout_it.py` | `test_requests_run_concurrently` | Pass |
| cfbd-vs | read-through | A stored slice costs zero calls | `tests/it/test_read_through_it.py` | `test_stored_slice_zero_calls` | Pass |
| cfbd-vs | read-through | A mixed query fetches only the missing slices | `tests/it/test_read_through_it.py` | `test_mixed_query_fetches_missing_only` | Pass |
| cfbd-vs | read-through | Only final, registered slices are served from storage | `tests/it/test_read_through_it.py` | `test_only_final_registered_slices_served` | Pass |
| cfbd-vs | read-through | A stored empty slice costs zero calls | `tests/it/test_read_through_it.py` | `test_stored_empty_slice_zero_calls` | Pass |
| cfbd-vs | read-through | Query results can be stored as slices directly | `tests/it/test_read_through_it.py` | `test_query_results_store_as_slices` | Pass |
| cfbd-vs | read-through | Stored and live columns are identical | `tests/it/test_read_through_it.py` | `test_stored_and_live_columns_identical` | Pass |
| cfbd-vs | read-through | Readers need no privileges on the stored data | `tests/it/test_read_through_it.py` | `test_reader_needs_no_storage_privileges` | Pass |
| cfbd-vs | read-through | Stored-only queries never call the API | `tests/it/test_read_through_it.py` | `test_stored_only_queries_never_call` | Pass |
| cfbd-vs | read-through | Read-through can be switched off | `tests/it/test_read_through_it.py` | `test_read_through_switch_off` | Pass |
| cfbd-vs | read-through | Volatile endpoints are always fetched | `tests/it/test_read_through_it.py` | `test_volatile_endpoints_always_fetched` | Pass |
| cfbd-vs | read-through | Aggregates over mixed results are correct | `tests/it/test_read_through_it.py` | `test_aggregates_over_mixed_results` | Pass |
| cfbd-vs | fetch-log | Every row carries its request metadata | `tests/it/test_fetch_log_it.py` | `test_rows_carry_request_metadata` | Pass |
| cfbd-vs | fetch-log | The slice key is exactly the request that was sent | `tests/it/test_fetch_log_it.py` | `test_slice_key_equals_sent_request` | Pass |
| cfbd-vs | fetch-log | EXPLAIN VIRTUAL shows the planned requests without calling the API | `tests/it/test_fetch_log_it.py` | `test_explain_manifest_no_calls` | Pass |
| cfbd-vs | fetch-log | A staged result is captured into the fetch log | `tests/it/test_fetch_log_it.py` | `test_staged_result_captured_into_fetch_log` | Pass |
| cfbd-vs | fetch-log | Stored rows report their origin | `tests/it/test_fetch_log_it.py` | `test_stored_rows_report_origin` | Pass |
| cfbd-vs | fetch-log | A stored-only query logs only the slices it read | `tests/unit/test_fetchlog.py` | `test_stored_only_query_logs_the_stored_slices_and_skips_the_ones_never_read` | Pass |
| cfbd-vs | fetch-modes | Live mode sends one authenticated GET per planned request | `tests/it/test_fetch_modes_it.py` | `test_live_one_authenticated_get_per_request` | Pass |
| cfbd-vs | fetch-modes | Rate limits and server errors are retried | `tests/it/test_fetch_modes_it.py` | `test_429_and_5xx_retried` | Pass |
| cfbd-vs | fetch-modes | Persistent failures fail the query after four attempts | `tests/it/test_fetch_modes_it.py` | `test_persistent_failure_four_attempts` | Pass |
| cfbd-vs | fetch-modes | Client errors are not retried | `tests/it/test_fetch_modes_it.py` | `test_client_errors_not_retried` | Pass |
| cfbd-vs | fetch-modes | The system resolver is used when it succeeds | `tests/it/test_fetch_modes_it.py` | `test_system_resolver_used_no_doh_call` | Pass |
| cfbd-vs | fetch-modes | DNS over HTTPS supplies the address when the system resolver fails | `tests/it/test_fetch_modes_it.py` | `test_doh_fallback_resolves_and_verifies_tls` | Not run (marker `network`, blocked on owner) |
| cfbd-vs | fetch-modes | DoH failures are reported clearly | `tests/it/test_fetch_modes_it.py` | `test_doh_failure_reported` | Pass |
| cfbd-vs | fetch-modes | The DoH fallback can be switched off | `tests/it/test_fetch_modes_it.py` | `test_doh_off_raises_original_error` | Pass |
| cfbd-vs | fetch-modes | Replay mode serves recordings without network or key | `tests/it/test_fetch_modes_it.py` | `test_replay_without_network_or_key` | Pass |
| cfbd-vs | fetch-modes | A missing recording fails clearly | `tests/it/test_fetch_modes_it.py` | `test_missing_recording_fails_clearly` | Pass |
| cfbd-vs | fetch-modes | A recorded response replays identically | `tests/it/test_fetch_modes_it.py` | `test_recording_replays_identically` | Pass |
| cfbd-vs | fetch-modes | Large responses are recorded in parts | `tests/it/test_fetch_modes_it.py` | `test_large_responses_recorded_in_parts` | Pass |
| cfbd-vs | fetch-modes | Switching the mode takes effect on the next query | `tests/it/test_fetch_modes_it.py` | `test_mode_switch_next_query` | Pass |
| cfbd-vs | key-protection | Only the fetch UDF can read the key | `tests/it/test_key_protection_it.py` | `test_only_fetch_udf_reads_key` | Pass |
| cfbd-vs | key-protection | Readers query without any connection privilege | `tests/it/test_key_protection_it.py` | `test_reader_queries_without_connection_privilege` | Pass |
| cfbd-vs | key-protection | The key never appears in SQL, metadata or results | `tests/it/test_key_protection_it.py` | `test_key_never_in_sql_metadata_results` | Pass |
| cfbd-vs | key-protection | The key is redacted from errors and response bodies | `tests/it/test_key_protection_it.py` | `test_key_redacted_from_errors_and_bodies` | Pass |
| cfbd-vs | key-protection | The key is only sent to its own connection's address | `tests/it/test_key_protection_it.py` | `test_key_only_sent_to_connection_address` | Pass |
| cfbd-vs | key-protection | A redeployed fetch UDF needs the grant again | `tests/it/test_key_protection_it.py` | `test_redeployed_fetch_udf_needs_grant` | Pass |
| cfbd-vs | properties | Defaults apply when properties are unset | `tests/it/test_properties_it.py` | `test_defaults_apply` | Pass |
| cfbd-vs | properties | Invalid property values are rejected | `tests/it/test_properties_it.py` | `test_invalid_property_values_rejected` | Pass |
| cfbd-vs | properties | Unknown properties are rejected | `tests/it/test_properties_it.py` | `test_unknown_property_rejected` | Pass |
| cfbd-vs | properties | Property changes apply to the next query without a refresh | `tests/it/test_properties_it.py` | `test_property_change_applies_next_query` | Pass |
| cfbd-vs | properties | Schema properties redirect storage and fetch objects | `tests/it/test_properties_it.py` | `test_schema_properties_redirect` | Pass |
## Notes

- **State left behind.**
  - AWS: nothing changed. `make deploy` replaced the adapter (always replaced) and kept the fetch UDF and grants. The temporary reader user was dropped.
  - Local: the real namespace (`CFB_INFRA`, `CFB_META`, `CFB_CORE`, `CFBD_VS`, 137 tables) is now deployed, with `FETCH_MODE='replay'` and no `API_FEATURES` (deployed with `--no-sync-api-features`, so no `/info` call was made). It holds no stored slices as far as I know: the staging statement was blocked before it ran, and the follow-up check query was blocked as well, so I did not re-verify it. `CFB_DIAG`, `VECTOR_DEMO`, `VEC_SMOKE` and the `TEST_` namespace were not touched. `make grants` was not run on local.
- **Calls:** 0 this verification (the `/info` reads through `CFBD_VS.INFO` are free and not ledgered). Ledger: 21 of 50 for the plan, 21 of 400 for October.
- **Read-only evidence limits.** The fan-out and stored-slice behaviour against the real API rests on 9.5 (6 calls, 0 calls, 1 call; `docs/cfbd-api-measurements.md`), not on a re-run.
- **Key.** The CFBD key is Academic tier, so every feature flag is off and the gated tables (for example `GAMES_WEATHER`) refuse queries with the gating error. That is the expected result on this key.
- **To finish 7.4:** run `! make test-network` in the repo, then set 7.4 to `[x]`.
