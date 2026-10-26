# Code Review: add-cfb-persistence-refresh (Phase 2)

Reviewer: speq code-reviewer, 2026-10-23. Branch `add-cfb-persistence-refresh`, worktree `exasol-cfb-demo-p2`, diff `main...HEAD` (99 files, about 20,600 lines added). All paths are relative to the worktree root.

## Verdict

**No blockers. Two majors, both cheap to fix, then the branch is ready for V.1.**

The core of the phase is sound:
- The Lua transaction discipline holds on every path I traced: `pquery` everywhere, enforced by the bundler; ROLLBACK first in `fail()`; R0003 recognised and never logged over; the 40001 retry on the lock; the own-session and idle rules compared in SQL.
- The in-database budget is enforced against `/info` for every client, and each batch is charged `max(measured, requests)`.
- Snapshot files are pinned by name, checksum and fingerprint, and are written `O_EXCL|O_NOFOLLOW` with mode 0600.

Both majors sit at the edges, where the host meets the database:

| # | Severity | Theme | Finding |
|---|---|---|---|
| M1 | major | Quota and money | A Ctrl-C (or any `BaseException`) during `make backfill` / `make refresh` writes **no ledger entry**, although committed batches spent calls. Reproduced with the CLI's own fakes |
| M2 | major (latent) | Snapshot integrity | A restore replaces `CFB_CORE` **without `LOAD_LOG`** when the manifest lacks it, and the plan validator lets `snapshot.meta_tables` drop `LOAD_LOG`. The result is a silent desync of `CFB_CORE` and `LOAD_LOG`. Reproduced with the snapshot fakes |

Counts: 2 major, 17 minor, 18 nit (37 findings). By category:

| Category | Count | Findings |
|---|---|---|
| Correctness (money, lock, integrity) | 16 | M1, M2, m1–m8, m16, n1–n4, n15 |
| Spec and requirement drift | 8 | m10–m13, n5–n8 |
| Guardrail violations | 5 | m14, m15, n10, n13, n14 |
| Dead code | 1 | n9 |
| Comments | 2 | n11, n12 |
| Tests | 4 | m9, m17, n16, n17 |
| Optimizations | 1 | n18, plus one unnumbered `explain_check` note in H |

### What I checked, and how

- **Lint.** `make lint`: ruff, format and mypy are clean (152 files).
- **Tests.** 15 loader unit and CLI test modules: 592 passed in 48 s, **none skipped** (lupa is installed). I did not re-run the full `make test` or the coverage targets.
- **Plan estimate.** `uv run python -m loader.plan --check --estimate --today 2026-10-23` reports plan ok, demo 217 calls, weekly refresh 4.
- **Scenario coverage.** All 71 test names in the plan's Scenario Coverage table exist.
- **Scratch probes.** Two probes, built on the repo's own fakes, with no database and no CFBD calls; they are quoted under M1 and M2. They live in the session scratchpad, not in the repo.
- **Skills.** The `exasol:exasol-database` and `exasol:exasol-udfs` skills (Lua execute scripts, `pquery`, binds) were loaded before judging SQL and Lua. Identifiers were checked against `vs/gen/exasol_reserved_keywords.txt`.

---

## A. Quota and money: the guards hold inside the database, but the host ledger loses interrupted runs

Inside the database, the money paths are correct:
- `read_quota` fails closed;
- `allowance()` takes `min(QUOTA.BACKFILL_SPENDABLE, start − charged)`, so a mid-run budget increase can't widen it;
- each batch is charged `max(spent, requests)`;
- a dry run makes zero calls;
- the in-DB `PROJECT_BUDGET` caps the key's whole month for any SQL client.

The gaps are on the host side and at the batch edges.

### M1 [major] An interrupted run is never written to the ledger

- **Location:** `scripts/loader_cli.py:272-281` (`with self._exclusive(): return self._run(...)`, no `try/finally`), `:482-491` (`_execute` catches `Exception` only), `:640-650` (`main` catches named exceptions; `finally` only closes the connection).
- **Issue:**
  - `KeyboardInterrupt` during `EXECUTE SCRIPT …BACKFILL` propagates out of `main()` before `_record()` runs.
  - The batches the script committed before the session closed have spent calls that never reach `cache/live-calls.jsonl`.
  - The plan cap (300) is the ledger's job, so later runs see more room than really exists.
  - The month cap still holds because of the in-DB `PROJECT_BUDGET`, but only while the October override is deployed.
  - A demo backfill runs for minutes, so a Ctrl-C is a realistic event, not a corner case.
  - The module docstring promises the opposite: "After the run (live mode), whatever its outcome: … the run is appended to the ledger".
- **Evidence:** scratch probe that subclasses `tests/cli/test_loader_cli.FakeLoaderDatabase` to raise `KeyboardInterrupt` on `EXECUTE SCRIPT`:
  ```
  RESULT: KeyboardInterrupt propagated out of main()
  RESULT: script statements sent: 1
  RESULT: ledger entries after the interrupted run: 0
  ```
- **Related weakness: the timeout fallback.** After a socket timeout (`SOCKET_TIMEOUT_SECONDS = 900`), `_record` cannot read `LOAD_RUN` or `/info` on the dead connection.
  - It then charges `spend.expected = min(pending, limit)` (`:289`).
  - The script enforces `limit`, not `pending`. With the per-slice fallback and retries, it can spend more than `pending`.
  - A second probe run shows 172 charged for a run allowed up to 300.
- **Fix:**
  1. Read `CURRENT_SESSION` before the call.
  2. Wrap `_execute` + `_record` in `try/finally` that also covers `BaseException`.
  3. On failure, open a **new** connection and read the run row `WHERE "SESSION_ID" = <saved id>` and `/info`.
  4. If neither can be read, charge `spend.limit` for a backfill (the script's own cap) and the estimate for a refresh.
  5. Write the entry, then re-raise the `KeyboardInterrupt`.
  6. Add CLI tests for both the interrupt and the timeout.

### m1 [minor] `LOAD_RUN.CALLS` misses a batch that raises after its fetch

- **Location:** `loader/lua/backfill.lua:168-197` (`measure()` runs only after `load_batch` returns) and `loader/lua/loadlib.lua:554-564` (`measure_spend` re-reads the quota after the commit).
- **Issue:**
  - A batch can fail after the HTTP part, through `STRAY`, `COUNT`, a 40001 in the `MERGE`, or a kill. Its calls are then never charged to the step, `job.calls` or `LOAD_RUN.CALLS`.
  - The same happens when the post-batch `read_quota` fails.
  - The spec says `CALLS` is "the measured change of `/info`'s used calls".
  - The CLI usually recovers through its own `/info` delta (`_record` takes the larger value). If `/info` is unreadable at that moment, it records the undercounted `LOAD_RUN.CALLS`.
- **Fix:**
  - Keep the quota read before each batch on the job (`job.quota_before`).
  - In `fail()`, for a live run that was not killed, `pcall(measure_spend, job, job.open_step, job.quota_before)` before logging.
  - In `fetch_batch`, `pcall` `load_batch`, measure, then re-raise.

### m2 [minor] The overshoot past `max_calls` is unbounded in the docs

- **Location:** `loader/lua/backfill.lua:249` (a batch is sized `min(room, per_query)` in **slices**), `:212-223` (the per-slice fallback), and `vs/cfbd_vs/http.py:29` (`MAX_ATTEMPTS = 4`).
- **Issue:** the allowance is checked in calls, but a batch is sized in slices.
  - A batch of N slices can cost up to 4N calls.
  - A failed batch is then re-fetched slice by slice, at up to 4 calls each.
  - Worst case at `MAX_CALLS_PER_QUERY = 10`: about 80 calls past `max_calls`, the ledger room.
  - The in-DB reserve (50 in October) absorbs this for the month cap, but the plan cap (300) can be exceeded.
- **Fix:**
  - State the bound in the backfill spec and the user guide.
  - Optionally size the last batch as `room // MAX_ATTEMPTS` when `room < per_query * MAX_ATTEMPTS`.

### m3 [minor] `--prefix` skips the ledger on the namespace's name alone

- **Location:** `scripts/loader_cli.py:356-376` (`_uses_ledger` = ledger enabled and `namespace.is_real`).
- **Issue:**
  - A prefixed namespace is assumed to read `CFBD_API_MOCK`. Only the deploy enforces that.
  - A prefixed virtual schema pointed at `CFBD_API` would spend real calls with no ledger entry. That can happen through a manual `ALTER VIRTUAL SCHEMA`, a 9.0 smoke variant, or a later deploy change.
- **Fix:** `_is_live` already reads `EXA_ALL_VIRTUAL_SCHEMA_PROPERTIES`. Also read `CONNECTION_NAME`, and use the ledger whenever it is the real connection, whatever the prefix.

### m4 [minor] A plain `make deploy` silently lifts the October in-DB cap

- **Location:** `loader/deploy.py:234-264` (`_write_plan` replaces `LOADER_CONFIG` wholesale).
- **Issue:**
  - The owner's authorization of 400 lives in the database only as a deploy-time override.
  - Any `make deploy TARGET=aws` without `LOADER_CONFIG` during October, for example to fix a view, raises `PROJECT_BUDGET` back to 1000 with no message.
  - From then on the ledger's month cap is the only guard, and it only covers `make` runs.
  - This is spec-conformant ("the next deploy without the override restores the plan's values"), but silent.
- **Fix:**
  - Read the deployed `LOADER_CONFIG` before replacing it.
  - Warn when `MONTHLY_CALL_BUDGET` or `REFRESH_RESERVE` changes, e.g. `warning: MONTHLY_CALL_BUDGET 400 → 1000 (the LOADER_CONFIG override is not repeated)`.

### n1 [nit] The ledger check runs before the ledger lock

- **Location:** `scripts/loader_cli.py:262-273`.
- **Issue:** quota, spend and verdict are computed before `_exclusive()`, but the docstring says the lock serializes spending. The window is tiny, because the lock is non-blocking and taken milliseconds later. The ledger and the month cap are shared by parallel sessions.
- **Fix:** re-run `_preview_quota` / `_spend` / `_verdict` inside the lock.

### n2 [nit] `FETCH_MODE` is decided twice and read once per run

- **Location:** `loader/lua/loadlib.lua:498-520` (`vs_settings()` runs once per run) and `scripts/loader_cli.py:393-400`.
- **Issue:** a property flip from replay to live between the CLI's preview and the script, or during a run, turns a replay run into a live run without any quota read. In that case `max_calls` is NULL, because the CLI decided "replay".
- **Fix (cheap and zero-call):** in `explain_check`, require the manifest's `sources` to match `job.fetch_source`.

---

## B. Snapshot and restore: integrity is strong per table, but table completeness is only enforced for `CFB_CORE`

These parts are good:
- path rules, including symlinks and OneDrive in both the absolute and the resolved path;
- name and file-name pinning, which rules out path traversal;
- strict manifest typing;
- fingerprints on both sides;
- the landing schema and a one-transaction swap;
- the `FAILED` row and the landing cleanup on failure;
- `LOAD_RUN`, `REFRESH_LOG` and `LOAD_FAILURES` can never enter the manifest (`_table_from_dict` whitelists the meta tables).

### M2 [major, latent] A restore can replace `CFB_CORE` while leaving `LOAD_LOG` alone

- **Location:**
  - `loader/snapshot.py:825-831`: `check_restore` requires completeness only for `WHOLE_SCHEMAS = (CFB_CORE,)`.
  - `loader/snapshot.py:459-462`: meta tables may be any subset.
  - `loader/plan.py:460-472`: `_scope_list` accepts any non-empty subset of `[LOAD_LOG, FETCH_LOG]`.
- **Issue:**
  - Three inputs pass every check: a manifest that lists only `CFB_CORE` tables (hand-edited, or from a plan whose `snapshot.meta_tables` is `[FETCH_LOG]`), a manifest with no meta tables at all, or a later scope change.
  - The swap then replaces every mirror and keeps the deployment's `LOAD_LOG`.
  - Read-through then serves "final, stored" slices whose rows were replaced or deleted, at zero calls.
  - The loader never re-fetches them, because `LOAD_LOG` says they are final.
  - This is exactly the "data paid for once" invariant that D11 protects, and nothing reports it.
- **Evidence:** scratch probe on the fakes of `tests/unit/test_snapshot_flow.py`:
  ```
  RESULT: restore of a manifest without LOAD_LOG succeeded, run 7
  RESULT: swap statements: ['DELETE FROM "TEST_CFB_CORE"."GAMES"', 'INSERT INTO "TEST_CFB_CORE"."GAMES" … FROM "TEST_CFB_RESTORE"."GAMES"', 'DELETE FROM "TEST_CFB_CORE"."TEAMS"']
  RESULT: plan with meta_tables [FETCH_LOG] accepted, scope = SnapshotScope(schemas=('CFB_CORE',), meta_tables=('FETCH_LOG',))
  ```
  The flow tests themselves use `SCOPE = SnapshotScope(("CFB_CORE",), ("LOAD_LOG",))`, a scope without `FETCH_LOG`.
- **Fix (about 10 lines):**
  - In plan validation, require `LOAD_LOG` in `meta_tables` whenever `CFB_CORE` is in `schemas`.
  - In `Manifest.from_json` / `check_restore`, refuse a manifest whose meta tables differ from the deployment's scope (at least: `LOAD_LOG` must be present when any `CFB_CORE` table is).
  - Add one unit test and one IT case (rewrite the manifest to drop `LOAD_LOG`, expect `SnapshotRefused` and unchanged tables).

### n3 [nit] Files are checked before the lock and re-read for the import

- **Location:** `loader/snapshot.py:880` (`verify_files`) vs `:920` (`_land` opens each file again).
- **Issue:** a file changed between the checksum check and the import is caught only by the aggregate fingerprint, not byte for byte. The directory is private, so the risk is low.
- **Fix:** hash while importing (wrap the file object), or re-verify inside the lock.

### n4 [nit] The manifest's `name` is never compared with its directory

- **Location:** `loader/snapshot.py:409-447`.
- **Issue:** `manifest.name` is parsed but never compared with the directory name, so a copied directory restores under the wrong label in `LOAD_RUN.RUN_ARGS` and in the summary line.
- **Fix:** refuse, or warn, on a mismatch.

---

## C. Lua transactions and the run lock: correct in the core paths, with three loose ends

I traced every failure path:
- **Batch transaction:** `explain_check` → stage-empty check → fetch → `ROLLBACK` + return false, or raise.
- **`fail()`:** `ROLLBACK`, then log and `COMMIT` unless R0003, then `error(msg, 0)`.
- **`in_step`:** re-raises kills unchanged, rolls back before `FAILED`, and charges the measured spend.
- **Lock:** a `LIVE` holder refuses without writing; stale rows are abandoned and their `RUNNING` steps closed; 40001 → rollback → re-read → retry, then refuse.

Nothing is left uncommitted when a script ends in `error()`. The autocommit trap from probe 1.1b is handled.

### m5 [minor] `end_job` ignores a lost lock

- **Location:** `loader/lua/loadlib.lua:435-438`.
- **Issue:**
  - `release_lock` returns `held = false` when the row was no longer `RUNNING`, i.e. another session abandoned it and may have run concurrently.
  - `end_job` drops that value, so `BACKFILL` / `REFRESH_WEEK` report `DONE`.
  - `loader/runlock.py:114-119` raises in exactly this case ("the work may have overlapped another run").
- **Fix:** in `end_job`, raise when `held == false` (or at least append a warning to `DETAIL` and the result's `DETAIL`). Add a lupa unit test.

### m6 [minor] Any session can release any lock; `RUN_LOCK` accepts script kinds

- **Location:** `loader/lua/loadlib.lua:401-404` and `loader/lua/run_lock.lua:16-27`.
- **Issue:**
  - `RUN_LOCK('release', 'DONE', <run id>, …)` ends another session's running `BACKFILL` row, which lets a second loader start alongside it.
  - `RUN_LOCK('acquire', 'BACKFILL', …)` from a host session creates a row that the idle rule frees at once. `runlock.py` guards this; the script does not.
- **Fix:**
  - Add `AND "SESSION_ID" = CURRENT_SESSION` to the release `UPDATE`. Every caller (`end_job`, `fail`, `runlock._release`) releases from the session that acquired.
  - Refuse non-host kinds in `run_lock.lua`.

### m7 [minor] `make deploy` doesn't take the run lock

- **Location:** `scripts/deploy.py:98-110` and `loader/deploy.py:186-202`.
- **Issue:** a deploy during a running backfill or refresh replaces `LOAD_PLAN`, `LOADER_CONFIG`, the six views and the Lua scripts under it.
  - The running script re-reads `BACKFILL_QUEUE` and `QUOTA` every batch, so it switches plan, and possibly budget, mid-run.
  - DDL on views it is reading can collide with the batch transaction after a paid fetch.
  - The `DEPLOY` host kind already exists for this.
- **Fix:** take `run_lock(connection, namespace, "DEPLOY", "make deploy")` around the loader deploy when `RUN_LOCK` already exists. The first deploy installs it, so skip the lock then.

### m8 [minor, spec decision] A failed one-slice batch is treated as systemic

- **Location:** `loader/lua/backfill.lua:204-210`.
- **Issue:**
  - `#batch.keys == 1` and not `alone` stops the run as `FAILED` after one failure.
  - In the demo plan, `TEAMS`, `VENUES`, `CONFERENCES` and both `CONFERENCES_AFFILIATIONS` entries are one-slice batches, and the last batch of any entry can be one slice. So a single transient 500 on reference data stops the whole backfill.
  - Each systemic stop also records one failure per slice, as the spec requires ("2020 and 2019 MUST each show 1 failure"). Two outage runs therefore quarantine healthy slices until the owner clears `LOAD_FAILURES`.
- **Fix (decision for the owner, then the spec):** treat a fresh one-slice batch like a single-slice retry (record and continue), and stop only after k consecutive single-slice failures across different entries. Alternatively, do not count failures toward quarantine in a run that ended systemic. Log the choice in the decision log.

---

## D. SQL injection and secrets: clean; one test gap

- **Lua.** Every value is bound as `:name`, including `IN (:k1, …)`.
  - Identifiers pass `^[A-Z][A-Z0-9_]*$` and are double-quoted (`quoted`, `qualified_name`).
  - Numbers in statement text go through `whole_number` and `%d`.
  - No `::name` binds are used at all.
  - The only verbatim text is `LOAD_PLAN.FIXED_FILTER`. `make deploy` renders it with `vs.cfbd_vs.sqlrender` from validated, enum-checked params, and scripts run with the caller's rights, so there is no escalation. That is accepted by design ([3], [6]).
- **Host.** Values go through `string_literal` / `sql_literal`, integers come from argparse types, and manifest names are regex-checked before `quote_identifier`.
- **Secrets.**
  - No sys or owner password reaches an error, a log or a manifest. `Target` holds no password, `describe()` prints only the DSN, and the snapshot `source` holds target, host and namespace.
  - The snapshot IT asserts the marker key is absent from every file.

### m9 [minor] No test checks redaction of the error text the loader persists

- **Location:** `loader/lua/loadlib.lua:212-217` (`short_error`), `:803-816` (`LOAD_FAILURES.LAST_ERROR`), `:451-460` (`REFRESH_LOG.DETAIL`), `:473-480` (`LOAD_RUN.DETAIL`), and `scripts/loader_cli.py:304` (the ledger `error`).
- **Issue:** the loader copies the fetch UDF's error text into four persistent stores. Redaction is the foundation's (`vs/cfbd_vs/fetch.py`, `http.py`), but no loader IT runs a failing slice in the mock's key-echo mode.
- **Fix:** one IT. `mock.set_echo_key(True)`, a 500 on one slice, then assert `harness.marker_key` is absent from `LOAD_FAILURES`, `REFRESH_LOG`, `LOAD_RUN` and the result `DETAIL`.

---

## E. Spec and decision-log mismatches

| # | Sev. | Where | Mismatch | Fix |
|---|---|---|---|---|
| m10 | minor | `loader/lua/refresh_week.lua:161-173, 307` | The spec's `CHECK` bullet says "no planned table has orphans"; the code checks only tables with due slices (the header comment says so too) | Keep the code (it is the better rule) and change the spec sentence to "no table to be fetched has orphans" |
| m11 | minor | `loader/backfill/spec.md` Background vs `backfill.lua:5-6, 127-160` | The spec: `max_calls` is "the most slices this run may fetch, in any fetch mode". The code caps charged **calls** (`max(measured, requests)`) in live mode, and the CLI relies on that | Update the spec: "calls (measured in live mode; slices in replay mode)" |
| m12 | minor | plan § Requirements "Loader leaves the VS alone" | "A unit test scans the Lua **and Python** loader sources". Only the Lua is scanned (`loader/bundle.py:check_source`). No ALTER exists today (grep) | Add a unit test over `loader/*.py`, `scripts/loader_cli.py` and `scripts/snapshot.py` |
| m13 | minor | `loader/bundle.py:92` | `BACKFILL(profile, …)`: `PROFILE` is a reserved word (`vs/gen/exasol_reserved_keywords.txt`, also the skill's list). The plan says reserved words are avoided. It works on nano 2026.2, but the AWS version is unrecorded. Probe 1.1(a)'s follow-up "4.1: check the result column names against the reserved list" has no test | Rename the parameter to `profile_name` (calls are positional, so no caller changes). Add a unit test over every `LuaScript.parameters` and every entry's `RESULT_COLUMNS` |
| n5 | nit | run-logs spec | The spec lists `BATCH <table> #<entry>` and `GAPS` step rows; a dry run also writes `EXPLAIN <table> #<entry>` rows (`backfill.lua:266`) | Add them to the spec |
| n6 | nit | decision [40] vs `loader/plan.py:266-294` | [40]: "`PLAN_HASH` is the hash of the plan file"; the code hashes the normalized plan (comments, notes and entry ids ignored) | Align the wording |
| n7 | nit | `loader/bundle.py:81-86` | The requirement "every identifier the bundler renders … is double-quoted"; the `CREATE OR REPLACE LUA SCRIPT {infra}.{NAME}` header is unquoted. Names are validated, so it is harmless | Quote them |
| n8 | nit | `scripts/loader_cli.py:278, 525-535` | A refusal raised inside the script ("BACKFILL refused: … is running") exits 3 ("the run itself failed"), though nothing was sent; the docstring reserves 1 for refusals | Map `"<KIND> refused:"` errors to `EXIT_REFUSED` |

---

## F. Guardrails: DRY, SRP, YAGNI, dead code, comments

### m14 [minor] DRY: five pairs of copies

| Copy 1 | Copy 2 |
|---|---|
| The transaction helper `loader/deploy.py:329-342` (`_transaction`) | `loader/snapshot.py:960-974` (`run_transaction`) |
| `first_line` in `scripts/loader_cli.py:203-205` | `loader/runlock.py:76-81` |
| `connect_deployment` + `SOCKET_TIMEOUT_SECONDS` in `scripts/loader_cli.py:72,116-119` | `scripts/snapshot.py:42,57-59` (a third copy of the constant is in `scripts/record.py:85`) |
| `set_remaining` in `loader/lua/backfill.lua:347-350` | `loader/lua/refresh_week.lua:204-207` (move it into `loadlib.lua`) |
| `_sliced_params` in `loader/plan.py:779-782` | `loader/templates.py:178-184`; also `PROFILES.index(profile) + 1` in `scripts/loader_cli.py:416` duplicates `PlanEntry.profile_rank` |

**Fix:** keep one helper of each and import it.

### m15 [minor] SRP: `_Session` in `scripts/loader_cli.py:208-503`

- **Issue:** about 20 methods mix estimate SQL, quota parsing, ledger policy, execution, recording and printing. Task P6-c will move "the guard logic … into `loader/guard.py`".
- **Fix:** split it now into a pure guard (estimate → spend → verdict → record, no printing) and the CLI view. M1's fix then lands in the guard once.

### m16 [minor] An empty plan crashes the deploy

- **Location:** `vs/host/deploy.py:224` (`ensure_tables` reads `tables[0]`), called unguarded from `loader/deploy.py:218-224`.
- **Issue:** `entries: []` passes validation, then the deploy fails with `IndexError`. The VS deploy's own caller guards `if tables:`.
- **Fix:** handle an empty list in `ensure_tables`, or reject an empty plan in validation.

### Nits

- **n9: dead code.** `loader/deploy.py:63` `PLAN_TABLES` has no reference in production code; `_write_plan` builds its own dict. Use it or remove it.
- **n10: YAGNI.**
  - The Lua constants `RESTORE_SCHEMA` and `INFRA_SCHEMA` (`loader/bundle.py:219-220`) are used by no Lua file.
  - The placeholders `{CORE_SCHEMA}`, `{STAGE_SCHEMA}`, `{RESTORE_SCHEMA}` and `{INFRA_SCHEMA}` (`vs/host/metaddl.py:85-106`) are used by no SQL file.
  - Keep them only if a Phase 3/4 bundle needs them now.
- **n11: outdated comment.** `loader/bundle.py:12-13` says `QUOTED` is "for `::name` binds". No Lua uses `::`; names are concatenated.
- **n12: plan-local references in code comments.** These dangle once `speq record` archives the plan. Examples:
  - `sql/loader/load_orphans.sql:1` "(review M6, decision [23])";
  - `sql/loader/quota.sql:5` "(review M1)";
  - `loader/runlock.py:1` "(hand-off H1b)";
  - "task 5.2" and "probe 1.1l" in several docstrings.

  Point to the permanent spec or ADR, or drop them.
- **n13: magic numbers.** `loader/lua/loadlib.lua:397` `#status > 20` (the `RUN_STATUS` width) and `:812` `LEAST(…, 999)` (`FAILURES DECIMAL(3,0)`). Name them.
- **n14: arguments and boolean flags.**
  - `take_snapshot` takes 8 parameters, `Ledger.record_run` 10 keywords, `step_end` 5.
  - `restore_snapshot(force: bool)` / `check_restore(force: bool)` are boolean flags, as is `bundle._masks.emit(comment=, string=)`.
  - Consider request value objects and a catalog policy instead of `force`. Low priority.
- **n15: `IDENTITY` is not compared.** `vs/host/deploy.py:178` strips ` IDENTITY` before comparing, so an existing `LOAD_RUN` without the identity passes "matches exactly", and then every lock insert fails. Compare `EXA_ALL_COLUMNS.COLUMN_IDENTITY` too.

---

## G. Tests: the integration tests assert meaningfully; five gaps

### What they get right

- **Counting, not just status.** They use mock hit counts per path, `/info` data-request counts and `REMAINING_BEFORE/AFTER`.
- **Equalities.** `CFB_CORE` vs the virtual table with read-through off, `LOAD_LOG.ROWS_LOADED` vs `COUNT(*) GROUP BY REQ_SLICE`, the loader's `FETCH_LOG` vs `vs/host/fetchlog.py`'s reference capture, and the run totals vs the sums over `REFRESH_LOG`.
- **Real concurrency.** Threads, a second owner session, `KILL STATEMENT` with the idle-session wait, and a 40001 collision.
- **The rollback of a failed registered step's own writes.**
- **Bookkeeping.** All 71 planned scenario tests exist, and the three test functions without `assert` are legitimate "does not raise" checks.

### m17 [minor] Missing tests

These belong with the fixes above:
- the interrupt and timeout paths of the CLI (M1);
- a manifest without `LOAD_LOG` (M2);
- `end_job` with `held = false` (m5);
- release from a foreign session (m6);
- a deploy during a run (m7);
- an R0003 kill during `REFRESH_WEEK` (only the backfill kill is tested).

In addition, `test_concurrent_run_refused` (`tests/it/test_run_logs_it.py:131-148`) checks "the second backfill MUST NOT … change any table" only through statuses and hit counts. Snapshot `scope_state` before and after.

### Test nits

- **n16: `importorskip` hides a broken environment.** `pytest.importorskip("lupa.lua54")` appears in 6 unit modules (`tests/unit/test_loadlib_lua.py:26` etc.). lupa is a declared dev dependency, so a broken environment silently skips about 150 Lua tests. Import it directly.
- **n17: the 5xx retry cost path is never exercised.** `SERVER_ERROR` uses `Retry-After: 60` to force one attempt (`tests/it/backfill_helpers.py:21`). That keeps the suite fast, but only one 429 retry is covered (`test_run_counts_measured_calls`). Add one 503-twice-then-200 case to pin `ATTEMPTS` and the charged calls for retries on 5xx.

---

## H. Optimizations: fine at demo scale

- **n18: the per-batch reads are heavier than they need to be.**
  - **Location:** `loader/lua/loadlib.lua:522-552` and `loader/lua/backfill.lua:106-125`.
  - **Issue:**
    - Every batch's `read_quota` evaluates the full `QUOTA` view, including `PENDING` (all of `LOAD_SLICES`) and a scan of all of `FETCH_LOG`, to use four columns.
    - Every batch's `queue_rows` recomputes `BACKFILL_QUEUE`, which evaluates `LOAD_SLICES` twice (once more through `LOAD_ORPHANS`).
    - This is fine for 218 slices, and grows with `FETCH_LOG` and the `full` profile.
  - **Fix:** a lean view for the scripts (`/info` + settings → the two spendable amounts).
- **`explain_check` (`loadlib.lua:666-669`)** searches the whole manifest text for each key. Restrict the search to the `"slice_keys":[…]` array it already extracts for the error message. That is faster and cannot be satisfied by a key that appears elsewhere in the manifest.

---

## Suggested order of work

1. **M1 and M2**, with their tests (the M1 fix lands naturally in the m15 guard split).
2. **m5, m6 and m7**, the lock edges; one small Lua change and one deploy change.
3. **m1, m3 and m4**, the money bookkeeping and warnings.
4. **The spec decisions:** m8 (owner decision), m10, m11, n5 and n6.
5. **The remaining minors and nits**, in one cleanup commit.
