# Verification report: add-cfb-persistence-refresh

Date: 2026-10-23. Branch `add-cfb-persistence-refresh`, HEAD 3bfaf9c. Zero CFBD calls, no AWS. Lighter process by the owner's request: no new tests, gaps are listed, not closed.

## V.1 Checklist

| Item | Result |
|---|---|
| `make test` | 6577 passed (F.1 log `f1-gates.log`) |
| `make lint` (ruff, format, mypy) | clean; `ruff format --check`: 220 files already formatted |
| `make coverage` | 96.49 % total; `loader` 96 %, `vs/cfbd_vs` 98 %, `vs/gen` 94 % (gate 90 %) |
| `make test-it` | 258 passed, 1 deselected (`network`), 0 failed (F.1 log `f1-it-full.log`, 03:10-03:48 UTC). The 258 collected tests equal the 258 passed |
| `make generate` + `git diff --exit-code vs/catalog sql/core` | exit 0 (catalog b6389f65c48c) |
| Bundle (`vs.bundle`, `loader.bundle`) | exit 0; loader scripts 55 k characters each |
| `loader.plan --check --estimate` | plan ok, demo 217 calls, weekly refresh 4 |
| No data in git (`*.parquet`, `*.cfbd.json`, `*.csv.gz`) | empty |
| Secrets grep | only placeholders, documentation and test dummies |
| `vs.host.ledger --summary` | plan 0 of 300; 2026-10: 21 of 400 (foundation) |
| Tasks 1.1 to 8.4, R.1, F.1 | all implemented, tested and documented (probe doc, specs, user guide section 9, architecture, roadmap D39-D49, CLAUDE.md, mission) |
| Review findings | M1, M2 and minors fixed in F.1; skipped with reasons in the decision log: m14 and m15 (partial), n1, n3, n7, n10, n14, n15, n18 |

Doc fixes made during V.1: `plan.md` got a "Superseded" note (the snapshot is gzip CSV via pyexasol, decisions [31] and [44], not Parquet via exapump); the roadmap Phase 2 status line no longer lists the review and verification gates as open.

## V.2 Scenario coverage

All 73 scenarios of the six spec deltas appear in the plan's Scenario Coverage table (checked by script: 73 rows, 73 scenarios, none missing either way). All 82 named tests exist in the files the table lists. All of them ran green in the F.1 runs (unit 6577 collected = 6577 passed; IT 258 = 258). I read the test body of every scenario against its THEN/AND clauses (quota, load-plan, backfill, refresh-week, run-logs, snapshot-restore).

**Gaps (listed, not closed):**
1. No scenario lacks a test. Scenarios with no integration test, by nature: "A plain deploy keeps a target's budget" (unit test with a fake database; AWS cannot run it before group J), "Snapshots never land in the repository or a synced folder" and "A snapshot is never overwritten" (CLI tests with fakes).
2. The host ledger (guard 2: refusals, interrupted runs, `LEDGER=off`) has no spec scenario; it is covered by `tests/unit/test_ledger.py` and `tests/cli/test_loader_cli.py` only.
3. `tests/cli/test_loader_cli.py` names the string `CFBD_API` for its fake database. The plan said no new test file names it; no DB or API is touched, and decision [49] needs the string.
4. DB-side behaviour on a 3-node cluster and the real `/info` is not covered anywhere (group J).

## V.3 Manual flows

Per the coordinator's instruction, flows that the integration tests already cover were not repeated. Only the plan's mock-backed CLI path was run once more, in the `TEST_` namespace with the mock sidecar (never the real `CFB_*` schemas), under the IT lock. Result of the first run: all steps behaved as the plan expects.

| Flow | Outcome |
|---|---|
| `loader.plan --check --estimate` | plan ok, 217 calls, 4 per refresh |
| `LOAD_STATUS` after deploy of the project plan | 10 entries, demo calls to complete 217, no orphans |
| `make quota` (CLI) | one row, only `/info` read, data requests 0 |
| Dry run `BACKFILL('demo', NULL, 0)` via CLI | `CALLS_NEEDED` 217, 29 batches checked with `EXPLAIN VIRTUAL`, 1 gap registered, 0 data requests |
| Backfill `MAX_CALLS=20`, then the rest | `STOPPED_MAX_CALLS` with 20 calls, then `DONE` with 197; mock data requests 217 = sum of `LOAD_RUN.CALLS` |
| Rerun | 0 calls, estimate 0, `LOAD_STATUS` complete, no orphans |
| Refresh 2026 week 6 | 4 calls, timeline `CHECK` + 4 `FETCH` steps, 2026 slices non-final with fresh `LOADED_AT`, `LOADED_BY` = `REFRESH_WEEK#5` |
| Run logs | `LOAD_RUN`, `REFRESH_LOG`, `FETCH_LOG` contexts consistent; `LOAD_FAILURES` empty |
| Snapshot and restore via CLI | the first run stopped at the snapshot step: the guard refused my scratch directory because its path contains "OneDrive" (the rule works). Rerun with a private home directory: see the addendum below |

Note: with the mock's default `/info` (`resetAt` 2099) the quota attribution shows every call as `OTHER_CALLS`; this is a mock default, the IT `test_calls_attributed_by_run_kind` sets the reset date.

## Nothing blocks recording

No code fix was needed. Nothing found would break the live AWS backfill.

## Group J (live) must prove

| Row | Status |
|---|---|
| 9.0 AWS deploy, grants, `dns-check`, `make quota TARGET=aws` (budget 400 / reserve 50 from the ledger, `BACKFILL_SPENDABLE` <= ledger room) | pending live group J |
| 9.0 AWS smoke: DB version, probes 1.1 (a, b, c, g, h, i, j, l) and 1.2 (now the gzip CSV round trip) with `--target aws`, pinned TLS on all three nodes, dry run `BACKFILL('demo', NULL, 0)` with `/info` used calls unchanged, concurrent-lock check from two sessions | pending live group J |
| 9.1 estimate 217 (stop if more than 10 % above) | pending live group J |
| 9.2 resumable live backfill: `MAX_CALLS=20`, then the rest; measured calls = `/info` delta = ledger entries | pending live group J |
| 9.3 idempotence: 0 calls, `LOAD_STATUS` complete, no orphans | pending live group J |
| 9.4 live refresh: 4 calls, `FETCH` seconds per table (target under 30 s) | pending live group J |
| 9.5 snapshot AWS, restore local: hashes and fingerprints match, `GAMES` 2019 reads as one `STORED` group, restore time (target under 5 minutes) | pending live group J |
| 9.6 measurements doc, roadmap budget table, final ledger total (<= 300) | pending live group J |

Points to watch in group J:
- **Month room.** 2026-10 has 400 calls, 21 used. The other plans' sub-allocations (analyses 6, app 12, hardening 70, app 60) are not spent yet. The key's `/info` is the authority. If those are spent first, the room for 217 + 4 is thin. After 2026-10-31 the November cap (1000) applies, and a redeploy on AWS is needed to pick it up.
- **Fall-back hour.** Probe 1.1l: `CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')` can be up to 1 h late in the DST fall-back hour (Berlin 2026-10-25 02:00-03:00). Avoid starting a run then.
- **AWS version unknown.** Every platform fact was probed on local nano 2026.2. Lua value types, `EXPLAIN VIRTUAL` and lock behaviour need the 9.0 re-check.

## Addendum: second mock run (with a realistic `/info` reset date)

Same flow, `resetAt` 2026-11-01 so the attribution works. Results:
- Quota attribution after the backfill and refresh: `BACKFILL_CALLS` 217, `REFRESH_CALLS` 4, `OTHER_CALLS` 21 (the scripted base), mock data requests 217 + 4.
- Snapshot through the CLI: 11 gzip CSV files plus `manifest.json`, directory mode 0700, files 0600, 1147 rows, 0 CFBD calls; `make snapshots` lists it; a second snapshot with the same name is refused.
- A refresh with a changed upstream body, then `restore` without `CONFIRM`: checks columns, catalog, plan and files, prints what it would replace, changes nothing (exit 1).
- `restore ... CONFIRM=yes`: lock row `RESTORE`, 11 tables landed, fingerprints ok, 127 tables replaced in one transaction, 0 data requests; `SELECT ... FROM CFBD_VS.GAMES WHERE P_YEAR = 2019` then returns one `STORED` group with 0 requests.
- My own table digest differed after the restore for `GAMES` and `RATINGS_SP` only (same row counts). These are the two tables with DOUBLE columns, which fits the documented DOUBLE exception (a value with more than 15 significant digits returns rounded to its text form, relative change at most 4e-12; the restore's fingerprint check passed). I did not compare the values numerically in this run; the integration tests' `assert_restored` allows exactly that exception.
- The `TEST_` namespace was dropped at the end (no cleanup problems); my scratch snapshot directory was removed.
