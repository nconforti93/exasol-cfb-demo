# App verification

Derived numbers only. Sections are added by the tasks of `specs/_plans/add-cfb-app`.

## Frontend toolchain (task 1.2, frontend part)

Resolved on 2026-10-23 with the plan's policy: within the planned majors, the newest release at least 7 days old, pinned exactly in `app/frontend/package.json`; `package-lock.json` is committed. The host clock read 2026-10-22 22:27 UTC while the task named 2026-10-23, so the cutoff was set conservatively at 2026-10-15 22:27 UTC (a release that is 7 days old by either reading).

| Package | Pinned | Released (UTC) | Newer exists |
|---|---|---|---|
| react, react-dom | 19.3.1 | 2026-10-14 | no |
| vite | 8.3.4 | 2026-10-08 | 8.3.5 (2026-10-21) |
| @vitejs/plugin-react | 6.1.2 | 2026-10-05 | no |
| typescript | 6.0.3 | 2026-04-16 | 7.0.2 (typescript-eslint 8.x peers `<6.1.0`) |
| vitest | 5.0.5 | 2026-10-15 | no |
| @types/node | 24.19.2 | 2026-10-09 | no |
| jsdom | 30.1.3 | 2026-10-15 | no |
| @testing-library/react / dom / jest-dom / user-event | 16.3.3 / 10.4.2 / 7.0.1 / 14.6.8 | 2026-08-27 / 09-13 / 08-09 / 10-13 | no |
| eslint | 10.12.0 | 2026-10-02 | 10.13.0 (2026-10-16) |
| @eslint/js | 10.0.1 | 2026-02-06 | no (the 10.x line) |
| typescript-eslint | 8.71.2 | 2026-10-12 | 8.72.0 (2026-10-19) |
| eslint-plugin-react-hooks | 7.1.1 | 2026-04-17 | no |
| @types/react, @types/react-dom | 19.3.0 | 2026-09-09 | no |
| globals | 17.13.0 | 2026-10-01 | no |
| ajv (dev; fixture schema check) | 8.20.0 | 2026-04-24 | no |
| @playwright/test | 1.64.1 | 2026-10-14 | no |

Runtime dependencies: `react` and `react-dom` only.

### Where node_modules lives

The repository sits on a OneDrive-synced Windows drive (`/mnt/c/...`). Measured there: `require('jsdom')` took 3 min 28 s, against 2.4 s from the native file system, `npm ci` took about 5 minutes against 17 s, and vitest 5 gave up with "Timeout waiting for worker to respond" (its worker start timeout is a fixed 60 s). `app/frontend/scripts/deps.sh` (`make app-deps`) therefore runs `npm ci` in `~/.cache/cfb-app-frontend` (override: `CFB_FRONTEND_DEPS`) and links `app/frontend/node_modules` to it when the project is under `/mnt/`; elsewhere it installs in place. With the link, `make app-test` takes about 15 s. The link is git-ignored (`app/frontend/node_modules`; the existing `node_modules/` pattern does not match a symlink).

### Playwright gate (Q8, Q19)

Path taken: **(1) local install**. `npx playwright install chromium` ran without `sudo` (Chrome Headless Shell 156.0.8078.4, playwright chromium-headless-shell v1248, into `~/.cache/ms-playwright`), and `npx playwright test` passed `e2e/toolchain.spec.ts` against `vite preview` on 127.0.0.1:4173 (1 passed, 400 ms). The podman image and the API-level fallback were not needed. Side effect: the install pruned the older browser builds (chromium-1223, chromium_headless_shell-1223, ffmpeg-1011) that another Playwright version had cached.

## Palette (task 1.8)

Reference palette of the dataviz skill (`references/palette.md`), light surface `#fcfcfb` on page `#f9f9f7`, dark surface `#1a1a19` on page `#0d0d0d`.

- Odds bars: one sequential hue, blue (`#2a78d6` light, `#3987e5` dark), ramp steps 100/200/300/400/500/600/700.
- Movement: diverging pair blue (up) and red (down): `#2a78d6`/`#e34948` light, `#3987e5`/`#e66767` dark, always with an arrow icon and the word.
- Timeline states: the fixed status palette (`#0ca30c`, `#fab219`, `#ec835a`, `#d03b3b`) with an icon and a word. Warning and serious sit below 3:1 on the light surface (relief rule: icon and label, never color alone); the timeline bars use only good, critical and the accent.
- Waterfall by source: slot 1 blue for LIVE, slot 2 orange for REPLAY (`#eb6834` light, `#d95926` dark), STORED in neutral gray (`#7b7a74` light, `#898781` dark). Two series with a legend and direct labels.
- Text tokens: primary `#0b0b0b`/`#f0efec`, secondary `#52514e`/`#c3c2b7`, muted `#6b6a65`/`#a09e96` (the reference's muted `#898781` is 3.5:1 on the light surface, so it is darkened to clear 4.5:1 for text), accent text `#1c5cab`/`#86b6ef`.

`src/theme/tokens.test.ts` computes WCAG contrast for every text token (at least 4.5:1) and every control and chart color on both the page and the surface (at least 3:1) in both themes, pins the palette, and checks that `theme.css` is the generated output of the tokens.

### Validator output (`validate_palette.js --pairs all`)

```
$ node validate_palette.js "#2a78d6,#eb6834" --mode light --surface "#fcfcfb" --pairs all

Palette (light, surface #fcfcfb, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.43–0.77
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #eb6834↔#2a78d6 ΔE 24.7 (protan) · tritan 32.7
  [PASS] Normal-vision floor    worst all-pairs #eb6834↔#2a78d6 ΔE 33.6 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#3987e5,#d95926" --mode dark --surface "#1a1a19" --pairs all

Palette (dark, surface #1a1a19, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.48–0.67
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #d95926↔#3987e5 ΔE 26.8 (protan) · tritan 32.4
  [PASS] Normal-vision floor    worst all-pairs #d95926↔#3987e5 ΔE 31.8 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#2a78d6,#e34948" --mode light --surface "#fcfcfb" --pairs all

Palette (light, surface #fcfcfb, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.43–0.77
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #e34948↔#2a78d6 ΔE 21.6 (protan) · tritan 34.5
  [PASS] Normal-vision floor    worst all-pairs #e34948↔#2a78d6 ΔE 32.3 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#3987e5,#e66767" --mode dark --surface "#1a1a19" --pairs all

Palette (dark, surface #1a1a19, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.48–0.67
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #e66767↔#3987e5 ΔE 19.2 (protan) · tritan 31.4
  [PASS] Normal-vision floor    worst all-pairs #e66767↔#3987e5 ΔE 29.0 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#2a78d6,#eb6834" --mode light --surface "#f9f9f7" --pairs all

Palette (light, surface #f9f9f7, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.43–0.77
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #eb6834↔#2a78d6 ΔE 24.7 (protan) · tritan 32.7
  [PASS] Normal-vision floor    worst all-pairs #eb6834↔#2a78d6 ΔE 33.6 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

$ node validate_palette.js "#3987e5,#d95926" --mode dark --surface "#0d0d0d" --pairs all

Palette (dark, surface #0d0d0d, categorical): 2 slots
  [PASS] Lightness band         all 2 inside L 0.48–0.67
  [PASS] Chroma floor           all 2 >= 0.1
  [PASS] CVD separation         worst all-pairs #d95926↔#3987e5 ΔE 26.8 (protan) · tritan 32.4
  [PASS] Normal-vision floor    worst all-pairs #d95926↔#3987e5 ΔE 31.8 (normal)
  [PASS] Contrast vs surface    all 2 >= 3:1

  → ALL CHECKS PASS  (CVD in the 6–8 floor band is legal ONLY with secondary encoding: direct labels, gaps, or texture)
  scope: categorical palettes only. For a lone status/text color check WCAG text contrast; for a sequential ramp, lightness monotonicity.

exit 0

```

## Python toolchain (task 1.2, backend part)

Versions resolved on 2026-10-22 (system clock, UTC), pre-releases excluded, newest release at least
7 days old, pinned with `==` in `pyproject.toml` and resolved in the committed `uv.lock`:

| Package | Pinned | Released | Latest on PyPI that day | Group |
|---|---|---|---|---|
| fastapi | 0.143.2 | 2026-10-15 | 0.161.1 (2026-10-22) | `app` |
| starlette | 1.8.0 | 2026-10-13 | 1.9.0 (2026-10-18) | `app` |
| uvicorn | 0.54.0 | 2026-09-25 | 0.54.0 | `app` |
| httpx | 0.28.1 | 2024-12-06 | 0.28.1 (1.0 is a pre-release) | `dev`, for `TestClient` |

Resolved with them: pydantic 2.14.1, anyio 4.15.1, h11 0.16.0, click 8.5.0, annotated-doc 0.0.5 and
opentelemetry-api 1.45.1 (a hard requirement of fastapi 0.143.2 and 0.161.x alike). fastapi 0.143.3
was released 2026-10-16 14:14, inside the 7-day window on the day of the task. `[tool.uv]
default-groups = ["dev", "app"]`, so a plain `uv sync` installs both.

Starlette 1.8 prints a `StarletteDeprecationWarning` that `httpx` with `starlette.testclient` is
deprecated in favour of `httpx2`. The plan pins httpx 0.28.1; the warning is harmless and the
tests pass. Moving to `httpx2` is a decision for a later task.

## Early probe results (task 1.1)

Full findings: `docs/spikes/s9-app-probe.md` (script `docs/spikes/s9_app_probe.py --part early`,
Exasol Personal local 2026.2.0-nano.3, `PROBE_APP_*` namespace, 0 calls).

- (a) Lua scripts run with the caller's rights: confirmed. A user with only `EXECUTE` fails at the
  script's first read and first insert, and nothing is written. Views read with their owner's
  rights. Decision [2] stands: the refresh runs on the operator path as the owner.
- (d) A user reads all of its own effective privileges from `EXA_SESSION_PRIVS`,
  `EXA_ALL_OBJ_PRIVS`, the `EXA_USER_*` and `EXA_ROLE_*` views, with one exception: impersonation
  granted through a role or `PUBLIC` is invisible to the user and works, so proving "may impersonate
  no one" needs `EXA_DBA_IMPERSONATION_PRIVS` on the operator path. `PUBLIC` holds `USE ANY SCHEMA`
  on a fresh deployment, which the check tolerates. Passwords in `CREATE USER` and `ALTER USER`
  are masked as `'<SECRET>'` in the audit and profile views.
- (e) `ALTER SESSION SET QUERY_TIMEOUT` is accepted. It aborts a CPU-bound statement at 30.0 s but
  ends a statement that runs a UDF only when the UDF returns (35.2 s for a 35 s UDF); 300 lets it
  finish. pyexasol's `socket_timeout` of 10 or 30 s did not cut a 35 s statement.

## Backend unit modules (tasks 1.3 to 1.7)

`make test` runs their unit tests; `make coverage` reports the pure modules.

| Module | Role |
|---|---|
| `app/backend/settings.py` | settings table, bind resolution, operator decision, `Host` allowlist, `Origin` set |
| `app/backend/security.py` | `Host`/`POST`/CORS/session middleware, security headers, effective-privilege check |
| `app/backend/spend.py` | plans from manifests, guard decision, ledger entries, fetch-log rows, live timeouts |
| `app/backend/sessions.py` | per-session preview cache, single flight, console run, query log; the live gate |
| `app/backend/sqlcatalog.py`, `explain.py` | fixed page statements with lints; `EXPLAIN VIRTUAL` rows to manifests |
| `app/backend/console_catalog.py` | the four console statements and their pinned expected plans |
| `app/backend/models.py` | pydantic models of the HTTP contract; every frontend fixture parses with its model |

## Refresh button (task 4.2, 2026-10-27)

The local database was in `REPLAY`. Under the shared integration lock, a direct owner-path run of 2025 regular week 7 finished as `LOAD_RUN` 39 at zero calls. A separate FastAPI `TestClient` pass planned the same week at zero calls, returned an action id from `POST /api/refresh/run`, then polled `GET /api/actions/{id}` to `DONE` at run 40. Both left the point at 2026 after week 7. The HTTP test supplied a startup-check override only inside the test process because the local `CFB_APP_WEB` still inherits unrelated `PREPROC_RT` grants from `PUBLIC`; normal AWS startup uses the strict check and passes.

AWS `POST /api/refresh/plan` for 2026 regular week 8 found four due slices, 302 stored games (285 complete), no running refresh, quota usage 246/400 and 11 of 12 Phase 6 ledger calls free. Four slices can use up to 16 calls with four attempts each, so the live plan refused under decision [45]. `POST /api/refresh/run` refused both an unconfirmed zero-call request and a four-call confirmation with HTTP 409. `/info` usage stayed 246 and no refresh script was sent. The app server uses `APP_LEDGER_PLAN=add-cfb-app` for this verification.

Synthetic checks cover one replay script, a charged live failure, a refusal before the script when retry room is insufficient, worst-case charging of an unsettled run, and a ledger-write failure that blocks subsequent live actions. A subsequent owner-authorized cap change allowed AWS refresh run 18 (below). The process-death behavior in Q16 remains to be measured in an isolated integration test.

## Conditional rooting guide (task 3.2, 2026-10-27)

`POST /api/whatif/guide` now counts playoff outcomes for each unset coming-week game among the current run's simulated seasons matching the selected what-if results. It uses `CFB_API.WHATIF_OUTCOMES`, `WHATIF_PLAYOFF` and `WHATIF_GAMES` only, and spends zero CFBD calls. It returns a conditional, ranked coming-week guide, with null probabilities for an empty branch and a low-sample flag using `MIN_BRANCH_SAMPLE`.

On AWS run 2 (1,000,000 seasons), an Auburn request with one game set returned HTTP 200 in 6.5 s with 55 remaining games and no row for the set game. An unknown game returned HTTP 400. A local `TestClient` pass under the integration lock read run 1 and returned the conditional 55-game guide with the selected game excluded. The test process disabled the operator path and supplied a startup-check override because local `CFB_APP_WEB` inherits unrelated `PREPROC_RT` PUBLIC privileges. Seven focused route unit tests passed, including the two-run game cache and unknown-column run-change behavior. This is slower than the Phase 5 guide latency target; allow for a pause when showing a conditional guide in the demo. Quota remains 246/400 and the Phase 6 ledger remains 1/12.

## Preview ledger failure guard (task 2.4, 2026-10-27)

If a preview ledger append fails after a paid statement was sent, the current app process now marks reconciliation pending and refuses further paid preview and refresh actions. A synthetic test forced an `OSError` on ledger append: the first statement was sent once and the next attempt was refused before a second paid query. Five live guard tests passed. Durable recovery after a process death during an unrecorded preview still needs a separate design.

## Live week-8 refresh (2026-10-27)

The owner lifted the app-specific plan cap on 2026-10-27 because its 12-call verification allocation blocked an overdue refresh. The app was restarted with `add-cfb-app: 1400`; the October monthly cap stayed 400 and `REFRESH_SPENDABLE` was 154 before the run. A fresh `POST /api/refresh/plan` estimated four due slices and returned `can_run=true`, with a 16-call worst-case retry bound. `make dns-check TARGET=aws` passed without an HTTP request.

`POST /api/refresh/run` with `max_calls=4` returned action `7b77863fbf3e4a4c70410f9f` and database run 18. All eight steps finished `DONE`: CHECK, four fetches, MODEL, PREDICT and SIMULATE. Each fetch spent one call; the app charged a `REFRESH_WEEK#18` ledger entry of four calls. Fresh `/info` moved 246→250 out of the October cap of 400, with `REFRESH_SPENDABLE` moving 154→150. The current point moved from 2026 after week 7 to after week 8; publication and what-if run 4 are current, odds HTTP 200 serves 138 teams, and 56 week-9 picks are frozen before the first listed kickoff at 23:00 UTC on October 27. The next refresh target is week 9; it is not yet complete.

## Curated console (task 4.3, 2026-10-27)

`GET /api/console`, `POST /api/console/{id}/explain` and `POST /api/console/{id}/run` are mounted (`app/backend/routes/console.py`). `EXPLAIN VIRTUAL` and the statement run on a session of the console user `CFB_CONSOLE_WEB` (role `CFB_READER`), so the plan is what any reader of `CFBD_VS` gets. The owner path only reads the quota and writes the fetch log through its existing allowlist. A run reuses `LiveExecutor.run_statement`, the same gate, busy check, ledger lock, retry-headroom guard and charge-before-fetch-log order as the preview, with context `APP_CONSOLE#<action>`. One console run per browser session; a plan-time adapter refusal is HTTP 409 `max_calls_exceeded` with the adapter's `BudgetError` text.

AWS, port-8000 app as `CFB_CONSOLE_WEB`:

| Query | Plan (0 calls) | Run |
|---|---|---|
| fan-out | `GAMES_TEAMS`, 3 slices 2022–2024, budget 10, 3 planned live | `max_calls=0` → 409 before sending; `max_calls=3` → HTTP 200 in 5.1 s, 84 rows, three parallel requests (200, one attempt each), 3 ledger entries `APP_CONSOLE#bbca9629958cb30ca143f7bf`, fetch log written |
| contradiction | no request, no budget | HTTP 200, 0 rows, 0 calls |
| stored | `GAMES`, 157 stored slices 1869–2025, 0 live | HTTP 200 in 2.8 s, 156 slices with rows, 0 calls, fetch log written |
| over-budget | refused: `BudgetError: … 15 requests are planned but MAX_CALLS_PER_QUERY is 10` | 409 `max_calls_exceeded`, nothing sent |

The ledger moved by exactly the fan-out's three calls (app plan room 1395 → 1392, October room 149 → 146). Route units (listing, disabled 403, unknown 404, reader explain, plan-time refusal, body validation, reconciliation) and an executor test that keeps the statement off the owner connection pass; 602 app units, Ruff and strict mypy pass. The first stored run returned HTTP 500 because `REQ_AT` arrives as text; fixed and covered by a unit test.
