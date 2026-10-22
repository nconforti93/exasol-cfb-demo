# Tasks: add-cfbd-vs-foundation

## Group A
- [x] 1.1 Scaffold the Python project

## Group B
- [x] 1.2 Root `Makefile` with the targets `generate`, `test` (unit + cli), `test-it`, `lint` (ruff check, ruff format -
- [x] 1.3 No action
- [x] 1.4 Platform probe `docs/spikes/s2_platform_probe.py` with findings in `docs/spikes/s2-platform-probe.md`, run as  [expert]
- [x] 1.5 `scripts/dns.py`, `make dns-check`
- [x] 2.1 Catalog model and rules in `vs/cfbd_vs/catalog.py` (dataclasses, stable JSON, catalog hash) and `vs/cfbd_vs/na

## Group C
- [x] 2.2 OpenAPI schema walker `vs/gen/schema_walk.py` [expert]
- [x] 2.4 Overrides schema, loader and validator `vs/gen/overrides.py`
- [x] 2.5 Prose-rule lint `vs/gen/prose_lint.py`
- [x] 3.1 Filter parser `vs/cfbd_vs/filters.py`
- [x] 3.3 Rules engine `vs/cfbd_vs/rules.py`
- [x] 3.4 Request planner `vs/cfbd_vs/plan.py`
- [x] 4.2 HTTP client `vs/cfbd_vs/http.py` and resolver seam `vs/cfbd_vs/resolver.py`
- [x] 4.3 Replay store `vs/cfbd_vs/replay.py`
- [x] 6.2 Mock CFBD server `tests/mock_cfbd/server.py`

## Group D
- [x] 2.3 Table derivation `vs/gen/tables.py` [expert]
- [x] 3.2 Parameter constraint solver `vs/cfbd_vs/constraints.py` [expert]
- [x] 3.5 SQL rendering `vs/cfbd_vs/sqlrender.py` [expert]
- [x] 4.1 Flattener `vs/cfbd_vs/flatten.py` [expert]

## Group E
- [x] 2.6 Seed `vs/catalog-overrides.yaml` from `docs/cfbd-api-reference.md`
- [x] 2.7 Generator CLI `vs/generate.py` plus mirror DDL renderer `vs/gen/mirrors.py`
- [x] 3.6 Pushdown SQL builder `vs/cfbd_vs/pushdown_sql.py` [expert]
- [x] 3.7 Adapter entry `vs/cfbd_vs/adapter.py`
- [x] 4.4 Fetch entry `vs/cfbd_vs/fetch.py`
- [x] 6.1 Synthetic response generator `tests/synthetic/fake.py`

## Group F
- [x] 5.1 Bundler `vs/bundle.py`
- [x] 5.2 Meta DDL `sql/meta/load_log.sql` and `sql/meta/fetch_log.sql` per Key Interfaces.
- [x] 5.3 Deploy `scripts/deploy.py` plus `vs/host/deploy.py`, run as `CFB_OWNER`
- [x] 5.4 Grants `scripts/grants.py`, run as DBA (sys password from the starter-kit file at run time)
- [x] 5.5 Fetch-log capture helper `vs/host/fetchlog.py`
- [x] 5.6 Apply decision [20]: sys owns the real deployment (grants.py, namespace.py, key-protection spec, plan, .env.example)

## Group G
- [x] 6.3 IT harness `tests/it/conftest.py`

## Group H
- [x] 7.1 `tests/it/test_catalog_it.py`, `tests/cli/test_generate_cli.py`, `tests/it/test_flattening_it.py` (including t
- [x] 7.2 `tests/it/test_pushdown_it.py`, `tests/it/test_rules_it.py`, `tests/it/test_gating_it.py`, `tests/it/test_fano
- [x] 7.3 `tests/it/test_read_through_it.py` [expert]
- [~] 7.4 `tests/it/test_fetch_log_it.py`, `tests/it/test_fetch_modes_it.py`, `tests/it/test_key_protection_it.py`.

## Group I
- [x] 7.5 Quality gate
- [x] 8.1 `docs/user-guide.md`
- [ ] 8.2 `specs/architecture.md`
- [ ] 8.3 `docs/roadmap.md`
- [ ] 8.4 `CLAUDE.md` dense bullets

## Group J (sequential, gated)
- [ ] 9.0 Gate
- [ ] 9.1 Recorder `scripts/record.py` (`make record PATTERNS=… CONFIRM=yes`), run as `CFB_OWNER`
- [ ] 9.2 Real deployment on Personal
- [ ] 9.3 S1 measurement (≤ 26 calls) with `vs/live/s1-patterns.yaml`
- [ ] 9.4 Apply the findings
- [ ] 9.5 S2 live verification (≤ 7 calls; record everything)
- [ ] 9.6 Live documentation

## Review fixes (from code-review.md and the Phase 2 review)
- [x] F.1 Fail-fast fetch pool (M1), key redaction scoped to the key (m2), replay attempts (m9) [expert]
- [x] F.2 IT guard and harness hardening (m22, m23, m26, m27), deploy prefix guard (m3), single sys-password source (m15), local|aws target resolution, `make test-network` (m24)
- [x] F.3 Catalog hash over generated content (m5), RANKINGS_POLLS_RANKS parent keys + regenerate (Phase 2 review M8), dead code, SQL type grammar DRY, user-guide mismatches
- [ ] F.4 Redact the key after JSON parsing too (closes the exotic-escape gap from F.1: `\/`, `\u002B`, lowercase %hex); after the ITs finish

## Review and Verification
- [x] R.1 Code review
- [ ] V.1 Checklist commands (generate, build, test, test-it, lint, format, coverage, deploy, secrets)
- [ ] V.2 Scenario coverage audit
- [ ] V.3 Manual testing and verification-report.md
