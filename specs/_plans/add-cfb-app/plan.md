# Plan: add-cfb-app

## Summary

Roadmap Phase 6 ("App"): a FastAPI backend and a React frontend over `CFB_API` and `CFB_API_LIVE`. It has four pages (playoff odds with what-if toggles and a rooting guide, predictions with the scorecard, upsets, head-to-head with a live preview), a SQL↔HTTP inspector with a curated virtual-schema console, a refresh button with a live step timeline, an as-of week picker, and the CFBD credit in the footer. It serves demo beats 1–4. Pages read as a read-only app user. A small operator path runs as the namespace owner, only on a loopback bind, for the refresh, the fetch log and the console. Every live CFBD call is planned at 0 calls, shown and confirmed before it is sent, and recorded afterwards. Everything is tested at zero CFBD calls on the synthetic sim league; live verification on AWS spends at most 12 ledgered calls (8 planned), in an authorized month.

## Design

### Context

The predecessor plans are binding (interview item 1):

- **Phase 5** (`specs/_plans/add-cfb-analyses/`, revised 2026-10-22) is the contract the app consumes.
  - **Serving schemas.** `CFB_API` has 26 views (3 of them generated per publication) and costs 0 calls. `CFB_API_LIVE` holds `GAME_PREVIEW` (1 call per query) and `QUOTA` (`/info`, free).
  - **Role.** `CFB_APP` has `SELECT` on both schemas.
  - **Page contract.** Consumers use autocommit; pages start from `CURRENT_POINT` and never offer `IS_AFTER_CURRENT` points; the `IS_HEADLINE` and `FREEZE_KIND` labels; "T-n" ties with `PROB_SOURCE` beside every rank; the what-if run guard and `WHATIF_VIEWS_CURRENT`; the forced-run labels.
  - **Preview UX contract.** Never on page load; one literal id; a fetching state; the next meeting, else the latest one.
  - **What-if library.** `analyses/whatif.py`: `what_if_odds_sql` and `rooting_guide_sql`, both with the run guard.
  - **Thresholds.** The 0.0070 arrow threshold and the sample thresholds in `ANALYSIS_SETTINGS`.
  - **Open Phase 6 decisions it hands over:**
    - the backend's fetch-log writes for previews;
    - an owner path for refresh, forced re-sims and publication;
    - preview caching per session.
- **Phase 2** (`specs/_plans/add-cfb-persistence-refresh/`, worktree `../exasol-cfb-demo-p2`) provides:
  - `REFRESH_WEEK(season, week, season_type)`, whose steps commit their `REFRESH_LOG` rows at start and end; another session sees the progress (its scenario "Other sessions see the progress while the refresh runs");
  - the run lock, which refuses concurrent runs and names the running run;
  - the budget check in `CHECK`;
  - `LOAD_RUN` and `FETCH_LOG` with `CONTEXT = '<RUN_KIND>#<RUN_ID>'`;
  - the guarded host wrapper `scripts/loader_cli.py`: estimate, live check, quota, `Ledger.check`/`room`/`record_run`, and the `LEDGER=off` rule;
  - the harness, with `TEST_STEP_SLEEP`, `TEST_STEP_FAIL`, `set_now`, `refresh` and recordings.
- **Phase 4** provides the run registry fields behind `PUBLISHED_RUNS` and the harness's `hold_deploy_lock`.
- **Phase 3** provides the as-of points (`AS_OF_POINT`, `CURRENT_AS_OF`, week sequences).
- **The foundation** (recorded) provides:
  - `vs/host/target.py`: `TARGET`, with the sys password read at connect time and TLS pinned on AWS;
  - `vs/host/fetchlog.py`: `parse_manifest`, `build_fetch_log_rows` and `insert_fetch_log`;
  - `vs/host/ledger.py`;
  - `scripts/record.py --query`, the guarded virtual-schema query, with `planned_live`, `slice_calls`, `query_costs` and `charged`;
  - the mock sidecar, with `delay_ms`;
  - the security-model facts: readers may run `EXPLAIN VIRTUAL` (probe 1.4g), and the key never appears in it.
- **Roadmap demo flow, beats 1–4.**
  1. The hook (the app as of Sunday before week N+1).
  2. The inspector shows the HTTP behind SQL, the fan-out and the 0-call contradiction.
  3. The `REFRESH_WEEK` timeline.
  4. The pages move: the scorecard, the upset of the week ranked all-time, the odds with arrows, what-if toggles and a rooting guide on a bubble team, and head-to-head with a live preview on click.

Forces:
- **A read-only app and a refresh button.** `CFB_APP` can only `SELECT`, but the refresh writes `CFB_CORE`, `CFB_META`, `CFB_SIM`, `CFB_PRED` and DDL in `CFB_API`, and the fetch log needs `INSERT`. Exasol Lua scripts run with the caller's rights. The project memory has this as verified on 2026-06-05 in the preprocessor framework; the skills do not cover it; probe 1.1(a) re-checks it. So "a runner user with only `EXECUTE` on `REFRESH_WEEK`", the interview's example, cannot write anything the refresh writes.
- **Beat 2 needs the virtual schema itself.** The app role cannot read `CFBD_VS`, and the only virtual-table view it can read is `GAME_PREVIEW`, which has no year parameter. Showing a year range fanning out needs another principal.
- **Quota and honesty.**
  - CFBD calls are scarce: the October cap is 400, and November is not authorized as of 2026-10-22.
  - The ledger is the only record of live calls, and "live CFBD spend must always be explicit" (interview item 3).
  - Reading `/info` around every click would roughly double the preview's latency against the 2 s target.
- **Drivers.** A pyexasol connection is one Exasol session, runs one statement at a time, and cannot be shared between threads (threadsafety level 1; pyexasol docs via context7).
- **Demo-grade UI.** It must be legible on a projector, offer light and dark themes, credit CFBD, and look the same offline in replay mode.
- **Phase 8 on AWS.** The `aws` target pins TLS (`vs/host/target.py`), and the app must run as a single process.
- **Minimal, current frontend dependencies.** TypeScript 7.0.2 is current, but typescript-eslint 8.72.0 requires TypeScript below 6.1.0. FastAPI shipped four releases in 24 hours.
- **Process.**
  - No database session during planning, and zero CFBD calls.
  - `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only by the docs tasks.
  - Local tests run in "local free" windows under the IT lock. On AWS: deploy and run only.

**Goals**
- A backend that reads every page through the app user and names only `CFB_API` and `CFB_API_LIVE` objects, plus an operator path with a fixed allowlist that is on only for a loopback bind.
- **Four pages:**
  - odds, with arrows, a run banner, what-if toggles and the rooting guide;
  - predictions, with the scorecard and calibration;
  - upsets, with all-time and line-era ranks;
  - head-to-head, with a click-only live preview and a per-session cache.
- **The inspector:**
  - a per-session query log with the exact SQL;
  - `EXPLAIN VIRTUAL` on demand, by the same principal;
  - the `REQ_*` columns and a waterfall;
  - the refresh's requests;
  - a four-query console for beat 2.
- **Refresh:** a plan at 0 calls, confirmation of any live spend, a background run on the operator path, a timeline that follows any refresh run, and clean refusals.
- **The as-of picker,** addressable by URL, following the current point by default.
- **Explicit spending:** a 0-call plan, a cost label or confirmation, a server-side re-plan against `max_calls`, ledger and quota guards, and a ledger entry per live request plus fetch-log rows.
- **The shell:** one process serving the API and the built frontend; dev with Vite; a strict CSP; loopback by default; CSRF and CORS rules; light and dark themes legible on a projector; the CFBD credit; a clear outage state; a reserved chat slot.
- **Make targets:** `make app`, `make app-build`, `make app-serve`, `make app-user`, `make app-test`, `make app-e2e` and `make app-perf`, with frontend lint in `make lint` and vitest in `make test`.
- **Tests:** API integration tests on the `TEST_` stack, component tests with vitest and Testing Library, a Playwright smoke if it installs cleanly, and zero CFBD calls.

**Non-Goals**
- The chat panel and its tools (Phase 7). Only the slot, the state store and the reserved `/api/chat/` path exist here.
- Demo hardening, the AWS scale dial, the replay rehearsal recipe and `docs/demo-script.md` (Phase 8). The app's support for them is listed under Dependencies.
- **Owner actions other than the refresh.** Forced re-sims (`make simulate BASE=`), manual publications (`make publish`), predictions (`make predict`) and drops stay CLI owner actions. The forced runs' odds are not shown (they serve no beat; the chat can read `FORCED_RUN_ODDS`).
- Free-text SQL in the app. The console runs four curated queries; ad-hoc SQL belongs in a SQL client.
- **The operator path on a non-loopback bind.** It would need authentication, which is a Phase 8 decision. The app can still be served on a network address read-only.
- Users, logins, multi-tenant use, mobile layouts (mission out of scope).
- `PROMOTE()` of previews, and any preview cache beyond the browser session.
- New database objects, apart from the read user that `make app-user` creates.
- Server-side caching of page data. The database answers within budget, and freshness after a refresh matters more.
- A coverage gate on the frontend.

### Decision

The backend has two principals:
- **The read path,** the app user `CFB_APP_WEB` with the role `CFB_APP`. Every page, the what-if queries, the preview fetch and the timeline run on it, from fixed SQL over the serving schemas.
- **The operator path,** the namespace owner, read at connect time through `vs/host/target.py`. It runs only an allowlist (the refresh, the fetch-log rows of the app's own requests, and the curated console), and only when the server is bound to a loopback address.

Every action that can call CFBD goes through one spending guard: plan at 0 calls, label or confirmation, re-plan against `max_calls`, ledger and quota, act, then a ledger entry per live request and fetch-log rows. The refresh reuses Phase 2's own guard, extracted from the CLI.

The frontend is a dependency-light React app: no router, state, data-fetching or chart library. It is served by the same FastAPI process in production and by Vite in development.

#### Architecture

```
 Browser: React 19 app (built by Vite)                        AppState store ─▶ chat slot (Phase 7; not rendered)
   pages  Odds · Predictions · Upsets · Head-to-head     header  as-of picker · quota · refresh · theme · inspector
     │  fetch /api/*, same origin; every POST carries X-CFB-Action: 1 and is Origin-checked
     ▼
 FastAPI  app/backend   one uvicorn worker, 127.0.0.1:8000; serves app/frontend/dist (SPA fallback, CSP) in production
   ├── read path   ReadPool: 4 sessions as CFB_APP_WEB (role CFB_APP), autocommit, query timeout 30 s, 1 retry on conflict
   │     routes ── sqlcatalog (fixed SQL over CFB_API / CFB_API_LIVE) ── analyses/whatif.py (what-if, conditional guide)
   │     preview ─ EXPLAIN VIRTUAL (0 calls) ─ spend guard ─ SELECT … FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <id>
   │     per browser session: query log (stmt.query), preview cache (15 min, 20)   process-wide: quota reading (5 min)
   ├── spend guard  app/backend/spend.py + live.py
   │     max_calls ≥ planned live · Ledger.check(plan app, planned + MAX_ATTEMPTS − 1) · quota · operator present
   │     after: one ledger entry per LIVE request (vs/host/ledger.py) · FETCH_LOG rows (vs/host/fetchlog.py rules)
   └── operator path  owner (sys via vs/host/target.py; CFB_TEST_OWNER in TEST_); on only for a loopback bind
         ops/refresh  ── loader/guard.py (extracted from scripts/loader_cli.py) ── EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(…)
                          in a background thread on its own connection (socket timeout 900 s)
         ops/fetchlog ── INSERT INTO CFB_META.FETCH_LOG …  CONTEXT APP_PREVIEW#<action> / APP_CONSOLE#<action>
         ops/console  ── 4 curated statements over CFBD_VS: EXPLAIN VIRTUAL, guarded run
     ▼
 Exasol   CFB_API (26 views) · CFB_API_LIVE (GAME_PREVIEW, QUOTA)                     ◀── read path only
          CFB_INFRA.REFRESH_WEEK: 1 CHECK · FETCH … · 100 MODEL · 200 PREDICT · 300 SIMULATE · 400 PUBLISH
            (a REFRESH_LOG row committed at each step's start and end) ──▶ CFB_API.REFRESH_TIMELINE / FETCHES
            (polled every 1 s while a refresh runs, else every 5 s; follows runs started anywhere)
          CFBD_VS ──▶ CFBD API v2 (live) or BucketFS recordings (replay)               ◀── console (operator) only
 Host     cache/live-calls.jsonl (ledger; plan app) · .env (CFB_APP_PASSWORD; git-ignored)
          ~/.exasol/personal/deployments/<local|default>/secrets.json (sys; read at connect, never stored)
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Two principals: a read-only path for everything a page shows, an owner path for an allowlist | `db.py`, `operator.py`, `ops/*` | Lua scripts run with the caller's rights, so only an owner can refresh; views run with the owner's rights, so pages need only the serving schemas. A bug in a page cannot write, and the owner path cannot run arbitrary SQL |
| Capability tied to the bind address | `settings.py` | The owner path exists only where the presenter's machine is the only client (loopback). A network-exposed app is read-only by construction, until Phase 8 adds authentication |
| Plan free, act guarded, record always | `spend.py`, `live.py`, `loader/guard.py` | `EXPLAIN VIRTUAL` and the refresh estimate cost 0 calls, so every button can state its cost. The server re-plans against `max_calls`, so a mode switch between showing and clicking can never spend unseen calls. The ledger and the fetch log are written by the same code that spent |
| One guard for CLI and app | `loader/guard.py` | `make refresh` and the refresh button check and record the same way; only the ledger plan differs |
| Fixed SQL catalog with a serving-schema lint | `sqlcatalog.py` | Pages cannot drift into `CFB_SIM` or `CFB_META`. The inspector shows texts that a reviewer has seen |
| Curated console instead of free text | `console_catalog.py` | Beat 2's three promises (fan-out, contradiction, stored) plus the budget refusal, with exact expected plans and bounded spend, and no injection surface |
| Same-principal `EXPLAIN` | `explain.py`, inspector | The plan describes exactly what that principal's query does. Readers may explain (probe 1.4g), so no owner rights are needed for page or preview plans |
| Follow, don't push | timeline poll | Every refresh step commits its row (Phase 2). Polling the serving view follows refreshes started from the app, `make refresh` or a SQL client alike, without a socket channel |
| Server-recorded statement text | query log | pyexasol's `stmt.query` is the text the database received. The client only references log entries by id, so it can never submit SQL |
| Per-session state, never global | `sessions.py` | The preview cache and the query log belong to the browser that spent or ran them. An anonymous cookie grants nothing |
| Dependency-light frontend | `app/frontend` | React, React DOM and Vite only at runtime and build. Charts are a few SVG forms after the dataviz method. Fewer moving parts for a public Labs repo |
| Contract fixtures validated on both sides | `src/test/fixtures`, `tests/unit/test_app_fixtures.py` | The component tests use synthetic API responses that the backend's response models must parse, so the frontend's tests cannot drift from the API |

#### Key Interfaces

**Pages and the views they read** (all on the read path; columns per `api/serving-contract` and the Phase 5 specs)

| Page / element | Views | Filter |
|---|---|---|
| Header: point, picker | `CFB_API.CURRENT_POINT`, `CFB_API.AS_OF_POINTS` | points with `IS_AFTER_CURRENT` FALSE, one season at a time |
| Header: quota badge | `CFB_API_LIVE.QUOTA` | one reading per 5 min, or on request (free `/info`) |
| Odds | `PLAYOFF_ODDS`, `PUBLISHED_RUNS`, `ANALYSIS_SETTINGS` | the point (`AS_OF_SEASON`, `AS_OF_WEEK_SEQ`) |
| Odds: what-if | `WHATIF_RUN`, `WHATIF_GAMES`; `what_if_odds_sql` (reads `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF`, `PLAYOFF_ODDS`, `WHATIF_RUN`, `ANALYSIS_SETTINGS`) | the what-if run only (`CURRENT_POINT.WHATIF_RUN_ID`), while `WHATIF_VIEWS_CURRENT` |
| Odds: rooting guide | `ROOTING_GUIDE`; `rooting_guide_sql` over the coming week | the run of the point's publication; the focus team |
| Predictions | `PREDICTIONS` (next week of P; games played in P's week), `SCORECARD_WEEKS`, `SCORECARD_SEASONS`, `SCORECARD_CALIBRATION` | point, population |
| Upsets | `UPSETS` | P's week; `ALL_TIME_RANK` ≤ 25; `LINE_ERA_RANK` ≤ 25 |
| Head-to-head | `TEAMS`, `MATCHUP_SERIES`, `MATCHUP_GAMES`, `MATCHUP_NEXT` | the ordered pair; next meeting at the current point |
| Head-to-head: preview | `CFB_API_LIVE.GAME_PREVIEW` | `GAME_ID = <literal integer>`, on click only |
| Timeline, inspector | `REFRESH_TIMELINE`, `FETCHES` | the newest refresh run, or a chosen one; `CONTEXT = 'REFRESH_WEEK#<run>'` |

**HTTP API** (JSON; snake_case keys are the view columns in lower case; every `POST` needs `X-CFB-Action: 1` and an allowed `Origin`)

| Method and path | Principal | Reads or does | Calls |
|---|---|---|---|
| `GET /api/config` | — | target name, operator `{enabled, reason}`, ledger plan, `chat: false`, version | 0 |
| `GET /api/health` | read | `SELECT 1`; 503 when unreachable | 0 |
| `GET /api/points?season=` | read | `CURRENT_POINT`; `AS_OF_POINTS` of the season (default: the current one); the list of seasons | 0 |
| `GET /api/teams` | read | `TEAMS` | 0 |
| `GET /api/odds?season=&week=` | read | odds rows, run banner, settings, what-if availability `{available, reason, run_id}` | 0 |
| `GET /api/whatif/games` | read | `WHATIF_RUN`, `WHATIF_GAMES` | 0 |
| `POST /api/whatif/odds` `{run_id, conditions[]}` | read | `what_if_odds_sql`; 400 on `ConditionError`; 409 `whatif_run_changed` | 0 |
| `GET /api/guide?season=&week=&team=` | read | `ROOTING_GUIDE` of the point's run: top 10 by `GUIDE_RANK` and the coming week | 0 |
| `POST /api/whatif/guide` `{run_id, team_id, conditions[]}` | read | `rooting_guide_sql` over the coming week's `WHATIF_GAMES` | 0 |
| `GET /api/predictions?season=&week=` | read | this week's and last week's predictions | 0 |
| `GET /api/scorecard?season=&week=&population=` | read | week, season to date, calibration | 0 |
| `GET /api/upsets?season=&week=` | read | the week's upsets, all-time and line-era top 25 | 0 |
| `GET /api/matchup?team=&opponent=` | read | series, meetings, next meeting, the preview's game id | 0 |
| `GET /api/preview/plan?game=` | read | `EXPLAIN VIRTUAL` of the preview query, plus the session cache state → cost label | 0 |
| `POST /api/preview` `{game_id, max_calls, force}` | read (+ operator for the fetch log) | the guarded fetch | ≤ 1 |
| `GET /api/quota?fresh=` | read | `CFB_API_LIVE.QUOTA` (cached 5 min) | 0 (free `/info`) |
| `GET /api/timeline?run=` | read | `REFRESH_TIMELINE` (the newest refresh run by default), `FETCHES` of the run | 0 |
| `POST /api/refresh/plan` `{season, week, season_type}` | operator | the guard's plan: estimate, mode, quota, ledger room, running runs | 0 (live: free `/info`) |
| `POST /api/refresh/run` `{season, week, season_type, max_calls}` | operator | re-plan, guard, background `REFRESH_WEEK`; 202 `{action_id}` | ≤ the estimate |
| `GET /api/actions/{id}` | — | the action's state, run id once known, outcome | 0 |
| `GET /api/inspector` | — | this session's query log | 0 |
| `POST /api/inspector/{entry}/explain` | the entry's principal | `EXPLAIN VIRTUAL` of the logged text | 0 |
| `GET /api/console` | operator | the four curated queries | 0 |
| `POST /api/console/{id}/explain` | operator | the plan | 0 |
| `POST /api/console/{id}/run` `{max_calls}` | operator | the guarded run | ≤ 3 |
| `/api/chat/*` | — | reserved for Phase 7: 404 | — |

**Settings** (`app/backend/settings.py`; environment first, then `.env`; nothing secret is ever logged)

| Variable | Default | Meaning |
|---|---|---|
| `TARGET`, `EXASOL_DSN` | `local`, — | deployment and address override (`vs/host/target.py`) |
| `CFB_APP_USER` | `CFB_APP_WEB` | the read user |
| `CFB_APP_PASSWORD` | — (required) | its password; `.env` is git-ignored |
| `APP_HOST`, `APP_PORT` | `127.0.0.1`, `8000` | bind |
| `APP_OPERATOR` | `auto` | `auto` (on iff loopback), `on` (refuses a non-loopback bind), `off` |
| `APP_CORS_ORIGINS` | empty | comma-separated origins; `*` is refused; `make app` sets the Vite origins |
| `APP_LEDGER_PLAN` | `app` | the ledger plan the app's spend counts against |
| `APP_LEDGER_PATH` | `cache/live-calls.jsonl` | ledger file; a prefixed namespace requires an explicit path |
| `LEDGER` | on | `off` skips the ledger (ledger module rule) |
| `APP_PREFIX` | empty | namespace prefix (integration tests); refused with the real `CFBD_API` connection, like `--prefix` |
| `CFB_TEST_OWNER_PASSWORD` | — | operator password for a prefixed namespace (precedent: `scripts/grants.py`) |
| `APP_STATIC_DIR` | `app/frontend/dist` | the built frontend |

**Operator allowlist** (`app/backend/operator.py`; the wrapper records every statement, and `tests/unit/test_app_operator.py` asserts that each matches one of these templates)

| Action | Statements | Module |
|---|---|---|
| refresh plan | `loader/guard.py`'s estimate queries over `CFB_META.LOAD_SLICES`, `LOADER_SETTINGS`, `LOAD_ORPHANS`, `LOAD_STATUS`; the `FETCH_MODE` property; `SELECT * FROM CFB_META.QUOTA`; `SELECT … FROM CFB_META.LOAD_RUN WHERE "RUN_STATUS" = 'RUNNING'` | `ops/refresh.py` |
| refresh run | `EXECUTE SCRIPT "CFB_INFRA"."REFRESH_WEEK"(<int>, <int>, 'regular'\|'postseason')`; then the `LOAD_RUN` row and `QUOTA` for the ledger record | `ops/refresh.py` |
| fetch-log write | `INSERT INTO "CFB_META".FETCH_LOG (…) VALUES …` (`fetchlog.insert_fetch_log`, bound values) | `ops/fetchlog.py` |
| console | `EXPLAIN VIRTUAL <catalog text>`; `<catalog text>` | `ops/console.py` |
| `make app-user` (CLI, not HTTP) | `CREATE USER … IDENTIFIED BY {password!q}`, `ALTER USER … IDENTIFIED BY …` (`ROTATE=yes`), `GRANT CREATE SESSION`, `GRANT CFB_APP` | `scripts/app.py` |

**Console catalog** (`app/backend/console_catalog.py`; `{VS}` is `CFBD_VS`, or `TEST_CFBD_VS` in the tests; the expected slice keys are pinned by task 1.7 against the catalog's canonical slice-key code)

| Id | SQL | Expected plan |
|---|---|---|
| `fan-out` | `SELECT * FROM {VS}."GAMES_TEAMS" WHERE "P_YEAR" BETWEEN 2022 AND 2024 AND "P_TEAM" = 'Michigan'` | 3 slices, `/games/teams?seasonType=both&team=Michigan&year=2022` … `2024`; federated, so up to 3 live calls (0 in replay) |
| `contradiction` | the same with `"P_YEAR" = 2023 AND "P_YEAR" = 2024` instead of the range | 0 slices, `sources` `[]` |
| `stored` | `SELECT * FROM {VS}."GAMES" WHERE "P_YEAR" = 1997` | 1 slice, `/games?seasonType=both&year=1997`, served `STORED` (demo profile, D15) |
| `over-budget` | the `fan-out` query over `BETWEEN 2010 AND 2024` | 15 slices > `MAX_CALLS_PER_QUERY` 10: refused at plan time |

`GAMES_TEAMS` needs `year` plus one of `week`, `team` or `conference` (catalog rule `games_teams.week_team_or_conference_with_year`), so a team filter keeps it selective.

**Spending guard** (`spend.py` is pure, `live.py` executes)

```
plan(statement, principal)            # 0 calls
  manifests = EXPLAIN VIRTUAL statement   (0..n pushdowns, vs/host/fetchlog.parse_manifest)
  live = Σ len(slice_keys) over manifests whose fetch_source is LIVE ; source = LIVE | REPLAY | STORED | NONE
act(statement, principal, max_calls)
  with Ledger.exclusive():                      # the cross-process file lock of vs/host/ledger.py
    plan again; refuse 409 if live > max_calls
    if live: refuse if the operator path is off; Ledger.check(APP_LEDGER_PLAN, month, live + MAX_ATTEMPTS − 1, quota.used)
             refuse if quota.remaining < live   (reading ≤ 5 min old, else read now)
    rows = run(statement); per slice: REQ_SOURCE, REQ_STATUS, REQ_ATTEMPTS, REQ_MS, REQ_AT, rows (REQ_ROWS, or counted)
    for each LIVE slice: ledger.append(plan, target, run=<action id>, slice_key, status, attempts,
                                       cost = attempts or MAX_ATTEMPTS, ms, remaining_* = null)
  operator: insert_fetch_log(build_fetch_log_rows(manifest, metadata, context='APP_PREVIEW#<action id>'))
```

**Refresh flow**

```
POST /api/refresh/plan → ops/refresh → loader/guard.plan_refresh(conn, ns, season, week, type, plan=APP_LEDGER_PLAN)
                          → {estimate per table, total, mode, quota, ledger room, running runs, can_run, reasons}
POST /api/refresh/run  → in-process single flight (409 naming the active action) → re-plan; refuse if total > max_calls
                          → thread: guard.run_refresh(...) = EXECUTE SCRIPT … REFRESH_WEEK(...) on its own owner connection
                          → outcome rows or first error line → ledger record REFRESH_WEEK#<run> (live) → action DONE | FAILED
                       ← 202 {action_id}
page: GET /api/timeline every 1 s while the newest refresh run is RUNNING, else every 5 s; GET /api/actions/{id};
      when CURRENT_POINT changes → pages reload (follow mode) or a notice (pinned point)
```

**App test stack** (`tests/it/app_stack.py`, task 2.3; the `TEST_` namespace; IT lock; 0 calls)
1. The harness deploys the virtual schema, the loader with `tests/it/fixtures/load-plan.app.yaml`, the model, the simulation schema and the analyses, then runs the grants with the prefix. The load-plan variant is Phase 5's `load-plan.analyses.yaml` plus step 350 `WAIT`, which is `TEST_STEP_SLEEP` (5 s). The users are `CFB_TEST_APP` with `TEST_CFB_APP` (Phase 5's helper) and `CFB_TEST_OWNER`.
2. It seeds Phase 4's sim league and Phase 5's history variant, writes replay recordings of the league's 2026 weekly slices, sets `SIM_CONFIG` to 2,000 seasons in batches of 500, and sets the clock to the end of 2026 week 9.
3. It publishes a manual run at 2026 week 8 (4,000 seasons, 8 × 500), then runs `REFRESH_WEEK(2026, 9, 'regular')` as the owner, which freezes, simulates and publishes.
4. `reset()` brings the stack back to this base state after a mutating test. `refreshed(10)` stores the week-10 results (`complete_week(10)`) and runs the app's replay refresh of week 10.
5. `app_client(**overrides)` is a FastAPI `TestClient` with the read user `CFB_TEST_APP`, the operator `CFB_TEST_OWNER`, `APP_PREFIX=TEST_`, a bind of `127.0.0.1`, and a temporary ledger: `Ledger(path, config_path=…)` over a temporary file and a temporary caps file in which plan `app` has a cap of 20 and the month is authorized for 20.
6. `served_app()` runs uvicorn as a child process on a free loopback port. The credentials reach it only through the child's environment. It is used by the E2E smoke.

**Frontend** (`app/frontend`)

```
package.json (exact pins), package-lock.json, index.html, vite.config.ts (/api proxy → 127.0.0.1:8000),
tsconfig.json (strict), eslint.config.js (typescript-eslint, react-hooks), vitest.setup.ts (jsdom, jest-dom)
src/main.tsx, App.tsx (path routing without a router library: /odds /predictions /upsets /head-to-head)
src/api/client.ts (same-origin fetch; POST adds X-CFB-Action; typed errors 400/403/409/503), types.ts
src/state/store.ts (AppState: point, page, team, conditions, inspector; actions; follow-current), url.ts
src/theme/tokens.ts (validated light/dark palette and text tokens → CSS variables), theme.css, ThemeSwitch.tsx
src/format/format.ts (percent, "< 0.1 %", "T-37", record labels, "N seasons in S s", spend labels)
src/shell/AppShell.tsx Header.tsx Footer.tsx OutageBanner.tsx AsOfPicker.tsx QuotaBadge.tsx ChatSlot.tsx
src/spend/SpendButton.tsx ConfirmSpend.tsx SpendNotice.tsx
src/odds/OddsPage.tsx OddsTable.tsx RunBanner.tsx WhatIfPanel.tsx RootingGuide.tsx
src/predictions/PredictionsPage.tsx ScorecardHeadline.tsx CalibrationChart.tsx PredictionList.tsx
src/upsets/UpsetsPage.tsx    src/headtohead/HeadToHeadPage.tsx TeamPicker.tsx SeriesSummary.tsx NextMeeting.tsx PreviewCard.tsx
src/refresh/RefreshPanel.tsx Timeline.tsx    src/inspector/Inspector.tsx QueryLog.tsx PlanView.tsx Waterfall.tsx Console.tsx
src/charts/ (SVG: inline bars, dot plot with reference line, timeline bars, waterfall; each with a table view)
src/test/fixtures/*.json (synthetic, sim-league names), src/test/render.tsx; *.test.ts(x) beside their components
e2e/smoke.spec.ts, playwright.config.ts (only if task 1.2's gate passes)
```

**Charts and color (the dataviz method).**
- The odds bars use one sequential hue.
- Movement is polarity: two diverging hues, always with an arrow icon and a word.
- Timeline states use the status palette, with an icon and a label.
- Calibration is a single series of dots on a gray diagonal, with no legend (the title names it).
- The waterfall colors bars by source, with a direct label.

Task 1.8 runs the dataviz validator (`validate_palette.js`) on the chosen hexes for `--mode light` and `--mode dark` against the two surfaces. `tokens.test.ts` pins the validated values and the WCAG contrast floors.

**Make targets** (the repo path contains spaces: every recipe quotes `$(ROOT)`; `scripts/app.py` launches the processes)

| Target | Does |
|---|---|
| `make app` | `db-check`, `app-deps`, then `python -m scripts.app dev`: uvicorn with `--reload` on 127.0.0.1:8000 and `APP_CORS_ORIGINS` set to the Vite origins, plus `npm run dev` (5173, proxy `/api`); Ctrl-C stops both |
| `make app-build` | `app-deps`, then `npm run build` (`tsc -b && vite build`) → `app/frontend/dist/` (git-ignored) |
| `make app-serve` | `db-check`, then `python -m scripts.app serve`: one process, one worker; refuses without `dist/`; `TARGET=aws` works |
| `make app-user [ROTATE=yes] [ARGS=…]` | `db-check`, then `python -m scripts.app user`: create or rotate the read user (`app/access`) |
| `make app-deps` | `npm ci` in `app/frontend` when `package-lock.json` is newer than `node_modules` |
| `make app-test` | `app-deps`, then `npm test` (vitest run) |
| `make app-e2e` | `db-check`, `app-build`, then `pytest tests/it -m e2e` (run under the IT lock) |
| `make app-perf` | `db-check`, then `python -m scripts.app perf`: the performance budget's read rows (never a live endpoint) |
| `make test` / `make lint` / `make test-it` / `make coverage` | + `app-test`; + `npm run lint` and `npm run typecheck`; `-m "it and not network and not e2e"`; + a report for the backend's pure modules |

**Versions** (checked 2026-10-22 against npm and PyPI)

| Package | Latest | Released | Note |
|---|---|---|---|
| fastapi | 0.161.0 | 2026-10-22 | 0.159.0, 0.159.1, 0.160.0 and 0.161.0 all within 24 h → policy below |
| uvicorn | 0.54.0 | 2026-09-25 | no `[standard]` extra; `watchfiles` only for `make app` reload if needed |
| pydantic / starlette | 2.14.1 / 1.9.0 | 2026-10-11 / 10-18 | through fastapi |
| httpx | 0.28.1 | 2024-12-06 | dev group, for `TestClient` |
| react, react-dom | 19.3.1 | 2026-10-14 | runtime |
| vite | 8.3.5 | 2026-10-21 | engines: Node ^20.19 or ≥ 22.12 (local Node 24.15.0, npm 11.12.1) |
| @vitejs/plugin-react | 6.1.2 | 2026-10-05 | peer `vite ^8` |
| typescript | **6.0.3** | 2026-04-16 | not 7.0.2: typescript-eslint 8.72.0 has the peer `typescript >=4.8.4 <6.1.0` |
| vitest | 5.0.5 | 2026-10-15 | peer `vite ^6 \|\| ^7 \|\| ^8` |
| jsdom | 30.1.3 | 2026-10-15 | vitest environment |
| @testing-library/react / dom / jest-dom / user-event | 16.3.3 / 10.4.2 / 7.0.1 / 14.6.8 | 2026-08-27 / — / 08-09 / 10-13 | react peer `^18 \|\| ^19` |
| eslint / typescript-eslint / eslint-plugin-react-hooks | 10.13.0 / 8.72.0 / 7.1.1 | 2026-10-16 / 10-19 / 04-17 | flat config |
| @types/react, @types/react-dom | 19.3.0 | 2026-10-04 | |
| @playwright/test | 1.64.1 | 2026-10-14 | only if task 1.2's install gate passes |

Policy (task 1.2): within these majors, take the newest release that is at least 7 days old. Pin npm packages exactly, with `package-lock.json` committed, and Python packages by bounds resolved in `uv.lock`. Record the resolved set in `docs/app-verification.md`. No other runtime dependency: no router, state, data-fetching or chart library.

**Exasol claims checked against the skills** (`exasol:exasol-database` loaded; grammar and keyword references), **the predecessor probes and the project memory**

| Claim | Evidence | Result |
|---|---|---|
| `EXPLAIN VIRTUAL <subquery>` returns the pushdowns, with the manifest in `PUSHDOWN_SQL` | `exasol-grammar.md` `explain_virtual_statement`; `vs/host/fetchlog.explain_pushdown_sql`; foundation | Confirmed |
| Readers may run `EXPLAIN VIRTUAL`; the key never appears in it | Foundation probe 1.4g; architecture § Security model | Confirmed for virtual-schema readers. Through a serving view with only schema `SELECT`: Phase 5 probe 1.1(b), re-checked here by probe 1.1(b) |
| `EXPLAIN VIRTUAL` of a query without virtual tables returns no pushdown rows rather than an error | Not stated | Probe 1.1(b). Fallback: label "no virtual table" from the serving-contract lint (Q3) |
| Lua scripting programs run with the caller's rights; views read with their owner's rights | Project memory (preprocessor framework, verified 2026-06-05); the skills are silent | Decisive for [2]. Re-checked by probe 1.1(a) in `TEST_` on 2026.2. If scripts ran with definer rights, the operator path would become an `EXECUTE`-only runner user (Q1) |
| `GRANT EXECUTE ON SCRIPT`, `GRANT IMPERSONATION ON user`, `IMPERSONATE user` exist | `exasol-grammar.md` `grant_objprivs_statement_1`, `grant_impersonation_statement`, `impersonate_statement` | Confirmed; considered and rejected ([2]) |
| `CREATE USER <u> IDENTIFIED BY <password>`; `ALTER USER` | `create_user_statement`; `scripts/grants.py` `create_user_step` (`{password!q}`) | Confirmed; reused |
| `ALTER SESSION SET <param> = <value>` for a per-session query timeout | `alter_session_statement` (generic parameter) | The parameter name and its effect are checked in probe 1.1(e). Fallback: pyexasol's socket timeout only |
| Autocommit is pyexasol's default; `stmt.query` is the sent text; one connection is one session, runs one statement, and is not shared between threads | pyexasol docs (context7 `/exasol/pyexasol`) | Confirmed: a 4-session read pool; the refresh gets its own connection |
| Each refresh step commits its row at start and end, and another session sees the progress | Phase 2 `loader/refresh-week` scenario | Taken. Re-checked through `REFRESH_TIMELINE` as the app role during `TEST_STEP_SLEEP` (probe 1.1(c)) |
| The run lock refuses concurrent runs, naming the running run | Phase 2 `loader/run-logs` | Taken; mapped to 409 |
| A schema grant covers views replaced later (the regenerated what-if views) | Phase 5 probe 1.1(c); project memory (preprocessor framework) | Taken |
| Views of the current user's privileges, for the read-user check (`EXA_USER_SYS_PRIVS`, `EXA_USER_ROLE_PRIVS`) | Not in the skills' references | Names and columns read in probe 1.1(d). Fallback: a name check (not `sys`, not the owner) plus a documented warning (Q5) |
| Reserved words | `exasol-reserved-keywords.md` | The only new identifiers are the user `CFB_APP_WEB` and test users (`CFB_TEST_APP2`): not reserved. `CONTEXT` values are strings. The console SQL quotes every identifier |

**Performance budget** (targets; local = laptop at 100k seasons on restored real data; AWS = 1M; measured by task 6.3 locally and task 8.2 on AWS; protocol after S4: 3 discarded warm-ups, 25 samples, p95 is the second largest, an exclusive window, sizing recorded)

| Request | Local | AWS | Basis |
|---|---|---|---|
| Odds page data (points, odds, banner, settings) | < 0.6 s p95 | < 0.8 s p95 | Phase 5 `PLAYOFF_ODDS` < 0.3 s, plus 3 small views |
| What-if toggle → conditional odds | < 0.5 s p95 | < 1.2 s p95 | Phase 5: < 0.3 s / < 1 s, plus ≤ 50 ms backend |
| Conditional guide (coming week) | < 0.7 s p95 | < 1.2 s p95 | Phase 5: < 0.5 s / < 1 s (or its F1/F2 outcome) |
| Predictions or scorecard page | < 1 s | < 1 s | small views |
| Upsets page | < 1.5 s | < 1.5 s | Phase 5 `UPSETS` < 1 s per week, plus two top-25 lists |
| Head-to-head (series, meetings, next) | < 1.2 s | < 1.2 s | Phase 5 < 1 s |
| Preview click, replay | < 1.5 s | — | `EXPLAIN` plus a replayed fetch, at 0 calls |
| Preview click, live | — | < 2 s, median of the live calls | mission on-demand target; S1: 0.7 s at CFBD plus ≈ 0.6 s database, plus `EXPLAIN` (Q7) |
| Timeline poll | < 0.2 s | < 0.3 s | `LOAD_RUN` × `REFRESH_LOG` |
| A step's change visible on the page | ≤ 1.5 s after its commit | ≤ 2 s | 1 s poll |
| Backend overhead per request | ≤ 50 ms p95 | — | measured against direct SQL |

`scripts/app.py perf` times a fixed catalog of read endpoints. `tests/unit/test_app_perf.py` asserts that the catalog names no preview, quota, console or refresh endpoint, the same rule as Phase 5's [31]. Live latency comes only from task 8.2's ledgered calls.

**Code layout**

```
app/__init__.py  app/backend/__init__.py
app/backend/main.py            create_app(settings, read_factory, owner_factory, ledger); static + SPA fallback; headers; errors
app/backend/settings.py        Settings.from_env (environment, .env); operator decision; CORS parse            (pure)
app/backend/security.py        X-CFB-Action + Origin check; CSP and headers; session cookie                   (pure + middleware)
app/backend/db.py              ReadPool; read-user check; query recorder; transaction-conflict retry; 503 mapping
app/backend/operator.py        OperatorPath: enabled and reason; owner connection per action; statement allowlist
app/backend/ops/refresh.py · ops/fetchlog.py · ops/console.py
app/backend/sqlcatalog.py      every read statement (pyexasol placeholders) + the serving-schema lint         (pure)
app/backend/explain.py         EXPLAIN VIRTUAL rows → 0..n manifests                                          (pure)
app/backend/spend.py           plans, guard decisions, ledger entries, fetch-log rows from REQ_*              (pure)
app/backend/live.py            guarded execution (ledger lock, run, record)
app/backend/sessions.py        session ids; preview cache; query log                                          (pure, thread-safe)
app/backend/console_catalog.py the four statements and their expected plans                                   (pure)
app/backend/models.py          pydantic response models (also validate the frontend fixtures)
app/backend/routes/            config points odds whatif predictions upsets matchup preview quota timeline refresh inspector console
loader/guard.py                plan_refresh / run_refresh / record, extracted from scripts/loader_cli.py
vs/host/livecalls.py           planned_live, slice_calls, query_costs, charged (moved from scripts/record.py)
scripts/app.py                 serve | dev | user | perf
app/frontend/                  see above
tests/it/app_stack.py, tests/it/fixtures/load-plan.app.yaml, tests/it/test_app_*_it.py
tests/unit/test_app_*.py, test_loader_guard.py, test_livecalls.py; tests/cli/test_app_cli.py
docs/spikes/s9_app_probe.py, docs/spikes/s9-app-probe.md, docs/app-verification.md
```

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Read path as `CFB_APP_WEB` (role `CFB_APP`); an operator path as the owner with a fixed allowlist ([2]) | A runner user with only `EXECUTE` on `REFRESH_WEEK` (the interview's example); a runner user granted every write and `CREATE ANY` privilege; `GRANT IMPERSONATION` to the app user; a request-queue table plus an owner-side worker; a separate runner process | Scripts run with the caller's rights, so an `EXECUTE`-only user fails at its first write. A fully privileged runner is an owner with one more password. Impersonation lets any page bug become `sys`. A queue gives the app a write path and adds a process. A separate process gives no real separation on a laptop, where the OS user can read the secrets file anyway |
| The operator path only on a loopback bind; `on` with a network bind refuses to start ([3]) | Always on; a token header; authentication in Phase 6 | The owner can do anything. On loopback the presenter's machine is the only client. Authentication is Phase 8's decision; until then a network-exposed app is read-only |
| Allowlist = refresh, fetch-log rows, curated console; forced re-sims and publication stay CLI ([4]) | Buttons for `SIMULATE_FROM`, `PUBLISH_RUN`, `PREDICT` | They serve no beat (Phase 5 [8]), take ≈ 40 s at 1M and write. The scale dial is Phase 8 and adds one allowlist entry then |
| Fetch log of the app's own requests written by the operator path from the result's `REQ_*` columns ([5]) | `INSERT` on `FETCH_LOG` for `CFB_APP`; a separate logger user; not logging previews | Keeps Phase 5's "the app role cannot change data" true. One principal for writes. `PROMOTE()` and `QUOTA` read the log later. Without the operator path, live requests are refused rather than left unlogged |
| One spending guard for every live action ([6]) | Labels only; a per-feature check | "Live spend always explicit" holds by construction. The re-plan against `max_calls` closes the label-to-click gap. The ledger stays the single record |
| Per-click ledger entries without `/info` readings; quota cached for 5 min ([7]) | `/info` before and after each click, like the recorder | Two extra virtual-table reads would push the preview past 2 s. The cost uses the attempts (the recorder's rule when readings are missing), and the ledger check keeps the retry headroom |
| Preview cache per browser session, 15 min, 20 entries ([8]) | A process-wide cache; no cache; `PROMOTE()` | Phase 5's hand-off. The viewer who spent owns the result. A demo segment fits in 15 min. "Fetch again" stays explicit |
| The inspector logs `stmt.query` per session and explains on demand as the same principal ([9]) | Explain every query eagerly; reconstruct the SQL client-side; explain as the owner | 0 calls and no owner rights for pages and the preview (probe 1.4g); the text shown is the text sent; no client SQL ever reaches the server |
| A curated four-query console on the operator path for beat 2 ([10]) | Free-text SQL as a reader user; a third database user with `CFB_READER`; a `CFB_API_LIVE` view per demo query | Exact expected plans, bounded spend (≤ 3 calls), no injection surface, no new database user. The SQL client remains for ad-hoc queries |
| Refresh: plan, confirm, background thread, poll the serving timeline for any run, map refusals to 409, no cancel ([11]) | Server-sent events; run synchronously in the request; a cancel button | Each step commits its row, so polling is correct and simple. A 2-minute statement must not hold an HTTP request. Following runs started in a SQL client keeps beat 3's "one SQL call" honest. Cancelling is a `KILL`, an owner action |
| Extract Phase 2's CLI guard into `loader/guard.py` with the plan name as a parameter ([12]) | Call `scripts.loader_cli` from the app; re-implement it | One implementation of estimate, quota, ledger verdict and record; the CLI's tests pin the behavior |
| One worker, a 4-session read pool, autocommit, a 30 s query timeout, one retry on a transaction conflict ([13]) | Several workers; a connection per request; autocommit off | The in-process state (sessions, actions) needs one process. pyexasol sessions cannot be shared between threads. Autocommit is the page contract (Phase 5) |
| React 19 + Vite 8 + TypeScript 6.0, no router, state, data or chart library ([14]) | TypeScript 7; react-router, TanStack Query, a chart library | TypeScript 7 breaks typescript-eslint. Four routes, one store and four SVG forms do not justify libraries. Less to keep current in a public repo |
| FastAPI serves `dist` with an SPA fallback and a CSP; `make app` runs Vite with a proxy ([15]) | nginx in front; hash routing; a single dev process | "A single process" (interview). `StaticFiles(html=True)` has no SPA fallback (Starlette source via context7), so a catch-all route returns `index.html` |
| The read user's password is read from the environment or `.env`; `make app-user` creates the user from it; the start refuses a writable read user ([16]) | Tooling generates and writes the password; the read path as `sys` in development | The precedent `CFB_TEST_OWNER_PASSWORD` (env or `.env`); no tool ever writes a secret; the "read-only pages" claim cannot be misconfigured silently |
| As-of in the URL; follow-current by default; later points never offered ([17]) | Session state only; always follow | Links reproduce a view; beat 4's "pages move" happens without a click; a pinned point is respected |
| A chat slot as a store, a region and a reserved path ([19]) | Build nothing; a placeholder panel | Phase 7 plugs in without a layout or state rework; nothing visible changes now |
| Tests: API integration tests on the `TEST_` stack, component tests with fixtures validated against the backend models, a gated Playwright smoke ([20]) | Mocked-DB API tests only; E2E for everything | The database behavior (roles, plans, locks) is the risk; fixtures cannot drift; E2E only if it installs cleanly (interview) |
| Live verification ≤ 12 calls, 8 planned, only in an authorized month ([21]) | 0 calls; a full demo run live | The live path (TLS-pinned AWS, ledger, fetch log, real latency) needs one proof; November is not authorized yet |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| app/access | NEW | `app/access/spec.md` |
| app/live-spend | NEW | `app/live-spend/spec.md` |
| app/refresh | NEW | `app/refresh/spec.md` |
| app/inspector | NEW | `app/inspector/spec.md` |
| app/as-of | NEW | `app/as-of/spec.md` |
| app/odds | NEW | `app/odds/spec.md` |
| app/predictions | NEW | `app/predictions/spec.md` |
| app/upsets | NEW | `app/upsets/spec.md` |
| app/head-to-head | NEW | `app/head-to-head/spec.md` |
| app/shell | NEW | `app/shell/spec.md` |

Domain `app/`, as `CLAUDE.md` plans it. The Phase 2–5 specs are not changed by delta files; none of them is recorded yet. This plan uses their contracts and leaves these recorder notes:
- **`loader/refresh-week`, `loader/quota`:** unchanged. Recorder note: `make refresh` and the app share `loader/guard.py`; the app's spend counts under its own ledger plan.
- **`api/on-demand`:** unchanged. Hand-off P6-a below adds `REQ_ROWS` to `GAME_PREVIEW` if Phase 5 accepts it.
- **`api/roles`:** unchanged. The read user `CFB_APP_WEB` is created by `make app-user` (`app/access`). Phase 5 left user creation to the owner, and that is what this target is.
- **`cfbd-fetch/fetch-log`** (recorded): unchanged. Its Background already names "the app backend (Phase 6)" as a consumer; the contexts `APP_PREVIEW#…` and `APP_CONSOLE#…` are new values, not a contract change.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls in tests | Integration, component and E2E tests use the mock, replay and synthetic data; the harness guard against `CFBD_API` stays. `scripts/app.py perf` never touches a live endpoint (unit-tested). Live verification: ≤ **12** ledgered calls under plan `add-cfb-app` (8 planned, task 8.2), only in a month the ledger authorizes; the October total stays ≤ 400 |
| Explicit live spend | Every action that can call CFBD has a 0-call plan, a cost label or confirmation, a server re-plan against `max_calls`, ledger and quota guards, one ledger entry per live request and fetch-log rows (`app/live-spend`) |
| Read-only pages | Pages read only `CFB_API` and `CFB_API_LIVE`, as `CFB_APP_WEB` (lint plus integration test). The start refuses a read user that can write |
| Operator path | The owner, through `vs/host/target.py` (the password read at connect time, never stored); the allowlist only (unit-tested statement templates); on only for a loopback bind; one connection per action |
| Secrets | No password, key or DSN secret in the repository, logs, API responses or the built frontend (`app/access` scenario). `.env` stays git-ignored. The CFBD key stays in `CFBD_API` only (CLAUDE.md) |
| CFBD terms | Credit in every page's footer; "not official NCAA or CFP data"; no raw response in git; the preview's `RAW_JSON` is never sent to the browser; fixtures are synthetic (sim-league names) |
| Browser safety | Loopback by default; `X-CFB-Action` plus `Origin` on every `POST`; CORS only for listed origins (never `*`); CSP `default-src 'self'`; nosniff; `frame-ancestors 'none'` |
| Accessibility and design | Base text ≥ 18 px; contrast ≥ 4.5:1 for text and ≥ 3:1 for controls and marks, in both themes (unit-tested); keyboard reachable; states as icon plus word; a table view for every chart; dataviz palette validated in both themes |
| Performance | The budget above; misses recorded with a breakdown and a logged decision |
| Runtime | Python 3.12 (uv; group `app`: fastapi, uvicorn); Node ≥ 22.12 (local 24.15.0); one process; `TARGET=local\|aws` |
| Ownership | `sys` owns every schema; the app adds no database object except its read user |
| Shared database etiquette | Integration tests, the E2E smoke and `make app-perf` run in "local free" windows under `flock <scratchpad>/cfb-it.lock`; AWS `default`: deploy and run only; no database session during planning |

## Dependencies

- **Phases 2–5 implemented and merged into `main`** before group B. This plan uses:
  - Phase 5's serving schemas and roles, `analyses/whatif.py`, the harness helpers (`deploy_analyses`, `seed_analyses_history`, `complete_week`, `publish`, `app_connection(role)`, `snapshot_restore_at`, the user `CFB_TEST_APP`) and the `load-plan.analyses.yaml` variant;
  - Phase 4's sim league, `SIM_CONFIG` and `hold_deploy_lock`;
  - Phase 3's as-of points;
  - Phase 2's `REFRESH_WEEK`, `TEST_STEP_SLEEP`, `TEST_STEP_FAIL`, `scripts/loader_cli.py`, the ledger configuration (`vs/live/ledger.yaml`) and the harness.

  Their live groups (Phase 2 J, Phase 4 J, Phase 5 K) come before this plan's group H.
- **Hand-offs** (for the orchestrator to record in the plans named):
  - **P6-a (Phase 5):** add `REQ_ROWS` to `CFB_API_LIVE.GAME_PREVIEW`, so the fetch log takes the rows per slice from the view. Fallback: the backend counts the result rows per slice. For a single-object preview that count is 1, and it is equal by construction while the view adds no row filter.
  - **P6-b (Phase 5):** the harness keeps the password of `CFB_TEST_APP` in process memory, available to `app_stack.py`, so the E2E child process can be given it through its environment.
  - **P6-c (Phase 2):** none. The guard extraction (task 1.9) happens here, after Phase 2 has merged, and keeps its CLI tests green. The orchestrator tells the Phase 2 session that `scripts/loader_cli.py` will be refactored.
  - **Phase 7:** the chat slot contract (`app/shell`): the AppState store and its actions, the region that `GET /api/config` `chat` turns on, and `/api/chat/`. The app adds no MCP or Claude code.
  - **Phase 8:**
    - the operator path on a non-loopback bind needs authentication;
    - the scale dial (a manual 1M run published from the app) is one more allowlist entry;
    - the replay recordings for the console's `fan-out` (3 slices) are made with `make record` under the ledger;
    - the rehearsal recipe ends with `make app-serve` and opens the app at the restored "Sunday after week N".
- **`add-engine-spikes`:** none.
- **User prerequisites:**
  - U1: "local free" windows.
  - U2: a ledger month authorization for the month in which task 8.2 runs (November 2026 is not authorized as of 2026-10-22).
  - U3: the owner reviews decisions [2], [3] and [10] (the backend process holds the `sys` connection on loopback; a curated console). This does not block any task.
  - U4: the owner sets `CFB_APP_PASSWORD` in `.env` and runs `make app-user` once per deployment (user guide, task 7.1).

## Migration

No data migration: the app adds no database object except the read user.

| Current | New |
|---------|-----|
| `make test`: unit + CLI | + `app-test` (vitest) |
| `make lint`: ruff, mypy | + `npm run lint` (eslint) and `npm run typecheck` (`tsc --noEmit`); mypy `files` + `app` |
| `make test-it`: `-m "it and not network"` | `-m "it and not network and not e2e"`; `make app-e2e` runs the `e2e` marker |
| `make coverage` | + a report for `app/backend/{settings,security,spend,sessions,sqlcatalog,explain,console_catalog}.py` at ≥ 90 % |
| `pyproject.toml` | + group `app` (fastapi, uvicorn) and `[tool.uv] default-groups = ["dev", "app"]`; dev + httpx; pytest marker `e2e`; coverage source + `app/backend` |
| `scripts/loader_cli.py` | delegates to `loader/guard.py` |
| `scripts/record.py` | imports `planned_live`, `slice_calls`, `query_costs`, `charged` from `vs/host/livecalls.py` |
| `vs/live/ledger.yaml` | + plans `app` (cap set by the owner) and `add-cfb-app: 12` |
| `.gitignore` | + `app/frontend/test-results/`, `app/frontend/playwright-report/` (`node_modules/`, `dist/` and `.env` are already ignored) |
| `tests/it/harness.py` | `leftovers` and `drop_namespace` also drop test app users (`CFB_TEST_APP2`); the app stack lives in `tests/it/app_stack.py` |
| Local `.env` (not committed) | + `CFB_APP_PASSWORD` |

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first; each backend task writes its integration test file first (failing), then the code; each frontend task writes its component tests first. Integration tests run with `flock <scratchpad>/cfb-it.lock make test-it` in "local free" windows at 0 CFBD calls.

**Who starts and who finishes each integration test file.** A scenario is written by the task that builds the last piece it needs, never earlier.

| File | Started by | Scenarios added later by | Green at the end of |
|---|---|---|---|
| `tests/it/test_app_shell_it.py` | 2.2 | — | 2.2 |
| `tests/it/test_app_access_it.py` | 2.2: refuses read user, operator follows bind, foreign origin, `make app-user` | 6.1: pages through the role, no secret | 6.1 |
| `tests/it/test_app_as_of_it.py` | 3.1: opens at current, after-current, no data | 6.1: earlier week moves every page | 6.1 |
| `tests/it/test_app_odds_it.py` | 3.1: odds and arrows | 3.2: what-if, guide, invalid conditions | 3.2 |
| `tests/it/test_app_predictions_it.py` | 3.3 | — | 3.3 |
| `tests/it/test_app_upsets_it.py` | 3.4 | — | 3.4 |
| `tests/it/test_app_head_to_head_it.py` | 3.5: series, page load, never met | 4.1: click, latest meeting, cache | 4.1 |
| `tests/it/test_app_live_spend_it.py` | 4.1 | — | 4.1 |
| `tests/it/test_app_refresh_it.py` | 4.2 | — | 4.2 |
| `tests/it/test_app_inspector_it.py` | 4.3 | — | 4.3 |
| `tests/it/test_app_e2e_it.py` | 6.2 | — | 6.2 |

1. **Probe, toolchain and pure code** (no database except 1.1)
   - [ ] 1.1 Probe `docs/spikes/s9_app_probe.py`, with its findings in `docs/spikes/s9-app-probe.md`. It runs in the `TEST_` namespace with the mock, under the IT lock, at 0 calls. **Gate:** Phases 2–5 are merged and their integration tests are green.
     - (a) **Invoker rights (decisive).** A probe user with only `CREATE SESSION` and `EXECUTE ON SCRIPT TEST_CFB_INFRA.REFRESH_WEEK` runs it. Expected: insufficient privileges at its first read of `TEST_CFB_META`, with nothing written. A second probe user gets `EXECUTE` on a one-line Lua script, owned by the test owner, that inserts into an owner table. Expected: refused. Record the messages and the version.
     - (b) As `CFB_TEST_APP`:
       - `EXPLAIN VIRTUAL SELECT * FROM TEST_CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <id>`: the manifest;
       - `EXPLAIN VIRTUAL` of a `TEST_CFB_API.PLAYOFF_ODDS` query: no rows, or an error, and which;
       - a contradiction through the view (`GAME_ID = 1 AND GAME_ID = 2`): 0 planned.
     - (c) As `CFB_TEST_APP`: `SELECT … FROM TEST_CFB_API.REFRESH_TIMELINE` while `TEST_STEP_SLEEP` runs in an owner session. Record the latency of 10 reads and whether any waits.
     - (d) The privilege views for the read-user check, as `CFB_TEST_APP` and as a user with an extra `CREATE TABLE`: which views and columns show the system privileges and roles of the current user.
     - (e) `ALTER SESSION SET QUERY_TIMEOUT = 30`: accepted, and it aborts a 35-second statement.

     **Gates:**
     - (a) fixes [2]: on a definer-rights result, switch the operator's refresh to an `EXECUTE`-only runner user and log it;
     - (b) fixes the inspector's no-pushdown handling (Q2, Q3);
     - (c) fixes the poll design (Q4);
     - (d) fixes the read-user check (Q5);
     - (e) fixes the read pool's timeout.
   - [ ] 1.2 Toolchain and scaffolding. Per the version policy:
     - `app/frontend/package.json` (exact pins), `package-lock.json`, `vite.config.ts` (proxy `/api`), `tsconfig.json` (strict), `eslint.config.js`, the vitest configuration (jsdom, jest-dom setup), and one placeholder component test;
     - `pyproject.toml` (group `app`, httpx, mypy and coverage entries, marker `e2e`), `.gitignore`, and the Makefile targets of Key Interfaces;
     - `app/backend/__init__.py`.

     **Gates:**
     - `npm ci && npm run build && npm test && npm run lint && npm run typecheck` pass (Q9);
     - `npx playwright install chromium` without `sudo`, then a trivial Playwright test against `vite preview`. If either fails, record it in `docs/app-verification.md`, drop Playwright from `package.json`, and task 6.2 takes the fallback (Q8).
   - [ ] 1.3 `app/backend/settings.py` and `security.py`: the settings table, the operator decision (auto, on or off × loopback), the CORS parse (refuse `*`), the `POST` check (header and `Origin`), the security headers and the session cookie. Unit tests first: `tests/unit/test_app_settings.py` and `test_app_security.py`.
   - [ ] 1.4 Move `planned_live`, `slice_calls`, `query_costs` and `charged` from `scripts/record.py` to `vs/host/livecalls.py`; `record.py` imports them, its tests move to `tests/unit/test_livecalls.py`, and the CLI tests stay green. Then `app/backend/spend.py` (pure):
     - plans from manifests;
     - the guard decision for (plan, `max_calls`, ledger room, quota, operator state), with the refusal messages of `app/live-spend`;
     - ledger entries from `REQ_*` rows;
     - fetch-log rows through `fetchlog.build_fetch_log_rows`, with a `REQ_ROWS` fallback that counts the rows.

     Unit tests first: `tests/unit/test_app_spend.py`.
   - [ ] 1.5 `app/backend/sessions.py`: session ids; the preview cache (15 min, 20 entries, per session, `force`); the query log (50 entries, ids). It is thread-safe under concurrent requests. Unit tests first: `tests/unit/test_app_sessions.py`, with a fake clock.
   - [ ] 1.6 `app/backend/sqlcatalog.py` and `explain.py`:
     - every read statement of Key Interfaces with pyexasol placeholders;
     - the lint: only `{API_SCHEMA}` and `{API_LIVE_SCHEMA}` objects, every identifier quoted, no string interpolation of request values;
     - parsing `EXPLAIN VIRTUAL` results into 0..n manifests.

     Unit tests first: `tests/unit/test_app_sqlcatalog.py` and `test_app_explain.py`.
   - [ ] 1.7 `app/backend/console_catalog.py`: the four statements and their expected plans. Unit test first: `tests/unit/test_app_console_catalog.py`, which builds the expected slice keys with the catalog's canonical slice-key code (`vs/cfbd_vs`) and checks the `games_teams` requirement rule against `vs/catalog/catalog.json`.
   - [ ] 1.8 Frontend foundations:
     - `src/theme/tokens.ts`, from a palette run through the dataviz validator for both modes, with the output pasted into `docs/app-verification.md`;
     - `theme.css` and `src/format/format.ts`;
     - `src/api/types.ts` and `app/backend/models.py`, the pydantic response models;
     - the synthetic fixtures `src/test/fixtures/*.json`.

     Tests first: `src/theme/tokens.test.ts` (contrast floors, base size, pinned palette), `src/format/format.test.ts` ("T-37", "< 0.1 %", records, spend labels, "N seasons in S s"), and `tests/unit/test_app_fixtures.py` (every fixture parses with its response model).
   - [ ] 1.9 Extract `loader/guard.py` from `scripts/loader_cli.py`: `plan_refresh`, `plan_backfill` and `run`, plus the record step, with structured results (estimate, mode, quota, verdict, room, outcome) and the ledger plan as a parameter. `scripts/loader_cli.py` keeps its output and exit codes by delegating. Tests first: `tests/unit/test_loader_guard.py` (fake connection: estimate, live check, verdicts, ledger record, `LEDGER=off`, prefixed namespace). Phase 2's `tests/cli/test_loader_cli.py` stays green unchanged. [expert]

2. **Backend core**
   - [ ] 2.1 `app/backend/db.py` and `operator.py`:
     - **ReadPool:** 4 sessions as the read user; autocommit; the timeout per 1.1(e); one retry on a transaction-conflict error; recording `stmt.query`, the principal, the milliseconds and the rows to the session's query log; mapping an unreachable database to 503 with `TargetError`'s message.
     - **The read-user check** per 1.1(d).
     - **OperatorPath:** enabled and reason; an owner connection per action through `vs/host/target.py`, or the test owner for a prefixed namespace; a statement recorder for the allowlist test.

     Unit tests first: `tests/unit/test_app_db.py` and `test_app_operator.py` (fake connections; allowlist templates; no password in any message).
   - [ ] 2.2 `app/backend/main.py`, `scripts/app.py` (`serve`, `dev`, `user`) and the Makefile targets:
     - `create_app` with injected factories and ledger;
     - `/api/config` and `/api/health`;
     - static serving with the SPA catch-all, the cache headers and the security headers;
     - the `POST` check and CORS middleware;
     - the error handlers (400, 403, 409, 503, with JSON bodies and no driver text);
     - one worker.

     Integration test files first: `tests/it/test_app_shell_it.py` (one process serves; outage) and the first scenarios of `tests/it/test_app_access_it.py` (per the table). CLI tests: `tests/cli/test_app_cli.py` (argument checks, refusal without `dist/`, `user` without a password, no secret in the output).
   - [ ] 2.3 `tests/it/app_stack.py` and `tests/it/fixtures/load-plan.app.yaml`, per Key Interfaces:
     - the session fixture, `reset()`, `refreshed(10)`, `app_client()` and `served_app()`;
     - the temporary-ledger helper;
     - `mock_delay(path, ms)`.

     All app helpers are written here, so later tasks do not edit them in parallel. A smoke test `tests/it/test_app_stack_smoke.py`: the base state (two publications, the current point at 2026 week 9) and `reset()` round trips.

3. **Read endpoints**
   - [ ] 3.1 `routes/config`, `points`, `odds` (odds rows, run banner, settings, what-if availability) and `teams`, with the point validation (unknown or after-current → the current point plus a notice flag). Integration tests first: `tests/it/test_app_as_of_it.py` (scenarios 1, 3, 4) and `tests/it/test_app_odds_it.py` (scenario 1).
   - [ ] 3.2 `routes/whatif`: games, conditional odds and the conditional guide through `analyses/whatif.py`; the unconditional guide; 400 on `ConditionError`; 409 `whatif_run_changed` on an empty result or an unknown-column error; the what-if games cached per run id. Integration tests: the remaining scenarios of `tests/it/test_app_odds_it.py`.
   - [ ] 3.3 `routes/predictions`: predictions, scorecard and calibration, with the population. Integration tests first: `tests/it/test_app_predictions_it.py`.
   - [ ] 3.4 `routes/upsets`. Integration tests first: `tests/it/test_app_upsets_it.py`.
   - [ ] 3.5 `routes/matchup`: series, meetings, next meeting, the preview's game id, never met. Integration tests first: `tests/it/test_app_head_to_head_it.py` (series, page load without a request, never met).

4. **Live, operator and inspector**
   - [ ] 4.1 `routes/preview`, `routes/quota`, `live.py` and `ops/fetchlog.py`. [expert]
     - The preview plan through `EXPLAIN VIRTUAL` on the read path.
     - The guarded fetch under `Ledger.exclusive()`: the re-plan against `max_calls`, `Ledger.check` with the retry headroom, the quota, and the operator requirement for `LIVE`.
     - The literal integer id, the session cache with `force`, one ledger entry per `LIVE` slice, fetch-log rows with `APP_PREVIEW#<action>`, and the spent calls in the response.
     - The quota reading, cached 5 min, with `fresh`.

     Integration tests first: `tests/it/test_app_live_spend_it.py`, plus the click, latest-meeting and cache scenarios in `tests/it/test_app_head_to_head_it.py`.
   - [ ] 4.2 `routes/refresh`, `routes/timeline`, `routes/actions` and `ops/refresh.py`, over `loader/guard.py`. [expert]
     - The plan, with the running runs from `LOAD_RUN`.
     - The run: the argument checks, a single flight per process, the re-plan against `max_calls`, and a background thread with its own owner connection (socket timeout 900 s). The action's state and outcome; the ledger record under the app plan.
     - The timeline of the newest refresh run, or a chosen run, with its `FETCHES`.
     - The mapping of the lock refusal and the `CHECK` refusal to 409 with the database's message.

     Integration tests first: `tests/it/test_app_refresh_it.py`, using `TEST_STEP_SLEEP`, `TEST_FAIL_PUBLISH` and `hold_deploy_lock`.
   - [ ] 4.3 `routes/inspector`, `routes/console` and `ops/console.py`: the query log; explain on demand as the entry's principal ("no virtual table: 0 calls" per 1.1(b)); the console's explain and its guarded run through `live.py` with `APP_CONSOLE#<action>`; the refresh requests per run. Integration tests first: `tests/it/test_app_inspector_it.py`, using `mock_delay("/games/teams", 400)` and `register_slice` for 1997.

5. **Frontend** (component tests first, against the fixtures of 1.8; each task owns its own directory)
   - [ ] 5.1 Shell (`src/shell`, `src/state`, `src/api`, `App.tsx`):
     - path routing; the AppState store with its actions and follow-current;
     - the header: as-of picker, quota badge, theme switch, inspector switch;
     - the footer, the outage banner and the chat slot;
     - the API client: `X-CFB-Action` on every `POST`, typed errors.

     Tests: `AppShell.test.tsx`, `Footer.test.tsx`, `ThemeSwitch.test.tsx`, `AsOfPicker.test.tsx`, `src/state/followCurrent.test.tsx` and `ChatSlot.test.tsx`.
   - [ ] 5.2 Odds (`src/odds`, `src/charts` bars): the table with movement, the run banner, the what-if panel (coming week first, ≤ 16, debounce 250 ms, sample and flags, method label, 409 reload), the rooting guide (unconditional and conditional labels), and the focus team from the URL. Tests: `OddsTable.test.tsx`, `WhatIfPanel.test.tsx` and `OddsPage.test.tsx`.
   - [ ] 5.3 Predictions (`src/predictions`, `src/charts` dot plot): the headline with the reconstruction label, the line beside it, the season to date, the calibration chart with its table view, the two prediction lists, and the population switch. Tests: `ScorecardHeadline.test.tsx`, `CalibrationChart.test.tsx` and `PredictionsPage.test.tsx`.
   - [ ] 5.4 Upsets (`src/upsets`): the upset of the week, "T-n" with the source, the two top-25 lists, the pre-window games, and the empty week. Tests: `UpsetsPage.test.tsx`.
   - [ ] 5.5 Head-to-head and spending (`src/headtohead`, `src/spend`): the team pickers, the series, the meetings, the next meeting with the "as of" label, `PreviewCard` (plan label, click only, a fetching state, cached state and "fetch again"), `SpendButton`, `ConfirmSpend` and `SpendNotice`. Tests: `PreviewCard.test.tsx` and `SpendButton.test.tsx` (the live-spend UI scenario).
   - [ ] 5.6 Refresh and inspector (`src/refresh`, `src/inspector`, `src/charts` timeline and waterfall):
     - the refresh panel: plan, confirmation, run, the action state, the refusal notice, and the reason without the operator path;
     - the timeline, polled every 1 s or 5 s, with an elapsed counter;
     - the inspector drawer: the query log, the plan view, the `REQ_*` lines, the waterfall, the console (operator only) and the refresh runs.

     Tests: `RefreshPanel.test.tsx`, `Timeline.test.tsx`, `Inspector.test.tsx` and `Waterfall.test.tsx`.

6. **End to end, access and performance**
   - [ ] 6.1 Complete `tests/it/test_app_access_it.py` (every page through the app role; no secret reaches the browser, with a built frontend) and `tests/it/test_app_as_of_it.py` (an earlier week moves every page).
   - [ ] 6.2 E2E smoke.
     - **With Playwright** (gate 1.2): `app/frontend/e2e/smoke.spec.ts`, driven by `tests/it/test_app_e2e_it.py` (markers `it` and `e2e`) against `served_app()` in replay. It opens the app at 2026 week 9 and sees the odds with arrows; runs the refresh of week 10 and sees `WAIT` running, then every step done, and the point moving to week 10; and on head-to-head clicks the preview and sees "fetching", then `REPLAY`, with no request to the mock.
     - **Fallback:** the same three flows as API-level steps in `tests/it/test_app_e2e_it.py` over `served_app()` with httpx, and the reason logged in this plan's decision log.
   - [ ] 6.3 `scripts/app.py perf` (`make app-perf`), with the fixed catalog of read endpoints. Unit test first: `tests/unit/test_app_perf.py`, which asserts that no preview, quota, console or refresh endpoint is in the catalog.
     - A local run in a "local free" window on the restored real data with a 100k published run (Phase 5's task 7.4 state): every local row of the budget, plus the replay preview click and the timeline visibility, with sizing recorded.
     - A projector and accessibility pass: a keyboard-only walk through the four pages, the inspector and the refresh; a review at 1280×720 in both themes.

     Results go to `docs/app-verification.md`.

7. **Documentation** (each file re-read first; `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only here)
   - [ ] 7.1 `docs/user-guide.md`, section "The app":
     - creating the read user (`CFB_APP_PASSWORD` in `.env`, `make app-user`, `ROTATE=yes`);
     - `make app`, `make app-build` and `make app-serve` (local and `TARGET=aws`);
     - the operator path and the loopback rule;
     - explicit spending and the ledger plan `app` (`LEDGER=off` for one's own key);
     - replay mode in the app;
     - the inspector and the console;
     - what each page reads.
   - [ ] 7.2 `specs/architecture.md`:
     - § Components, Backend and Frontend: the two principals, the routes, the inspector and the console;
     - § Security model: the user `CFB_APP_WEB` and the operator path; the fact that Lua scripts run with the caller's rights, with probe 1.1(a); "+ execute on its functions" removed;
     - § Fetch log: the app writes through the operator path with `APP_PREVIEW#` and `APP_CONSOLE#`;
     - the overview diagram's app line.
   - [ ] 7.3 `docs/roadmap.md`:
     - the decisions marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 6 marked done with its bullets: the console; the operator path; the refresh button over `REFRESH_WEEK`, with the wording `EXECUTE SCRIPT` instead of `CALL` in demo beat 3;
     - the backlog: authentication for a network-bound operator path; app-triggered forced re-sims and publications; `PROMOTE()` of previews; a free-text console; a cancel button.
   - [ ] 7.4 `CLAUDE.md`, dense bullets:
     - the domain `app/`, the code in `app/backend` and `app/frontend`, and the make targets;
     - the frontend toolchain (Node ≥ 22.12, `npm ci`, the lockfile, exact pins, TypeScript 6.0 because of typescript-eslint), with eslint in `make lint` and vitest in `make test`;
     - the live-call sources gain the app (preview, console, refresh), all ledgered under `app`;
     - the operator path rule (loopback only, allowlist);
     - the `.env` keys `CFB_APP_USER` and `CFB_APP_PASSWORD`;
     - this plan's ledger cap.

     `specs/mission.md`:
     - § Commands (the `app-*` targets, replacing the planned `make build`);
     - § Project Structure (`app/backend`, `app/frontend`);
     - § Tech Stack (versions);
     - glossary entries "Operator path", "Read path" and "Live spend".

8. **Live verification** (last; AWS `default` deploy and run only; local in a "local free" window)
   - [ ] 8.1 Local rehearsal at 0 calls:
     - restore the "Sunday after week N" snapshot (Phase 2), `make deploy`, `make grants` and `make app-user`;
     - `make app-build && make app-serve`;
     - click through beats 1–4 in replay: the hook, the inspector's console (contradiction and stored live; the fan-out only if its recordings exist, else its plan), the refresh of week N+1 with the timeline, and the pages moving;
     - record the timings and check that the ledger and `/info` are unchanged.
   - [ ] 8.2 AWS live, at most 12 calls for this plan with **8 planned**, under `APP_LEDGER_PLAN=add-cfb-app` and in a month the ledger authorizes (U2); `make dns-check TARGET=aws` first. In a window agreed with the orchestrator in which no other session spends calls: `make app-serve TARGET=aws` on loopback, then
     - **1 preview** of a coming-week game (median latency with the replayed and cached clicks as context);
     - the **`fan-out` console query** (3 calls; check the parallel waterfall);
     - **1 refresh** of the current week from the button (estimate 4; the timeline live).

     Read `make quota TARGET=aws` before and after.
   - [ ] 8.3 Zero-call proof and write-up:
     - the ledger entries of `add-cfb-app` are ≤ 12 and match one to one the `FETCH_LOG` rows with `APP_PREVIEW#`, `APP_CONSOLE#` and `REFRESH_WEEK#` contexts of the window;
     - the `/info` drop over the window equals the sum of every ledger entry in it, each listed with its plan;
     - `FETCH_LOG` has no row from tests;
     - `docs/app-verification.md` is complete, with derived numbers only (latencies, counts, resolved versions, the palette report).

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8 |
| Group A2 | 1.9 |
| Group B | 2.1 → 2.2 → 2.3 (sequential) |
| Group C | 3.1, 3.3, 3.4, 3.5, 5.1 |
| Group C2 | 3.2, 4.1, 4.2 |
| Group D | 4.3, 5.2, 5.3, 5.4, 5.5, 5.6 |
| Group E | 6.1, 6.2, 6.3 |
| Group F | 7.1, 7.2, 7.3, 7.4 |
| Group G (sequential, gated) | 8.1 → 8.2 → 8.3 |

Sequential dependencies:
- A → B → C → C2 → D → E → F → G.
- A2 runs alongside B and C, and must be done before 4.2.
- **Group A:**
  - 1.1 needs Phases 2–5 merged and a "local free" window.
  - 1.2 to 1.8 need no database and touch disjoint files. 1.8 creates `models.py`, which 2.2 imports.
  - 1.1's gates fix 2.1 (timeout, read-user check), 4.2 (poll) and 4.3 (no-pushdown handling).
- **Group B:**
  - 2.1 needs 1.1(d, e), 1.3, 1.5 and 1.6.
  - 2.2 needs 2.1, 1.2 and 1.8.
  - 2.3 needs 2.2's `create_app`.
- **Group C** touches disjoint files: `routes/points`, `odds` and `teams` with their two test files; `routes/predictions`; `routes/upsets`; `routes/matchup`; `src/shell`, `src/state` and `src/api`.
- **Group C2:**
  - 3.2 needs 3.1 and extends `test_app_odds_it.py`.
  - 4.1 needs 3.5 and 1.4, and extends `test_app_head_to_head_it.py`.
  - 4.2 needs 1.9 and 3.1.

  Their files are disjoint: `routes/whatif`; `routes/preview`, `quota`, `live.py` and `ops/fetchlog.py`; `routes/refresh`, `timeline`, `actions` and `ops/refresh.py`.
- **Group D:**
  - 4.3 needs 4.1 and 4.2.
  - 5.2–5.6 need 5.1 and own disjoint directories. Each owns its chart form in `src/charts/`, in separate files.
- **Group E:**
  - 6.1 needs every route and 5.1–5.6 (a built frontend).
  - 6.2 needs 6.1's build and 1.2's gate.
  - 6.3 needs every route and a "local free" window with restored real data.
- **Group F** needs green A–E.
- **Group G** needs F, the live groups of Phases 2, 4 and 5, U1 and U2.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| Duplicated logic | `scripts/loader_cli.py` `_Session` (estimate, quota, verdict, room, record) | Moved to `loader/guard.py` (task 1.9); the CLI keeps only argument parsing and printing |
| Functions | `scripts/record.py` `planned_live`, `slice_calls`, `query_costs`, `charged` | Moved to `vs/host/livecalls.py` (task 1.4); `record.py` imports them |
| Tests | the unit tests of those four functions | Moved to `tests/unit/test_livecalls.py` |
| Docs | `specs/mission.md` "Planned … `make build` (frontend bundle)" | Replaced by `make app-build` (task 7.4) |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| access: The backend refuses a read user that can do more than read | Integration | `tests/it/test_app_access_it.py` | `test_refuses_read_user_that_can_write` |
| access: Every page reads through the app role | Integration | `tests/it/test_app_access_it.py` | `test_pages_read_through_app_role` |
| access: The operator path follows the bind address | Integration | `tests/it/test_app_access_it.py` | `test_operator_path_follows_bind` |
| access: A request from another origin cannot act | Integration | `tests/it/test_app_access_it.py` | `test_foreign_origin_cannot_act` |
| access: No secret reaches the browser | Integration | `tests/it/test_app_access_it.py` | `test_no_secret_reaches_browser` |
| access: make app-user creates the read user once | Integration (CLI + DB) | `tests/it/test_app_access_it.py` | `test_make_app_user_idempotent` |
| live-spend: The plan states the cost before any call | Integration | `tests/it/test_app_live_spend_it.py` | `test_plan_states_cost_without_call` |
| live-spend: A live request is recorded in the ledger and the fetch log | Integration | `tests/it/test_app_live_spend_it.py` | `test_live_request_ledgered_and_logged` |
| live-spend: The server refuses to spend more than the user confirmed | Integration | `tests/it/test_app_live_spend_it.py` | `test_refuses_above_confirmed` |
| live-spend: A ledger or quota without room refuses before any call | Integration | `tests/it/test_app_live_spend_it.py` | `test_ledger_and_quota_refuse` |
| live-spend: Without the operator path a live request is refused and a replayed one is served | Integration | `tests/it/test_app_live_spend_it.py` | `test_operator_off_live_refused_replay_served` |
| live-spend: Replayed answers cost nothing and are still logged | Integration | `tests/it/test_app_live_spend_it.py` | `test_replay_costs_nothing_and_is_logged` |
| live-spend: Every spending control states its cost | Component | `app/frontend/src/spend/SpendButton.test.tsx` | `states the cost on every spending control` |
| refresh: The refresh plan names its cost before anything runs | Integration | `tests/it/test_app_refresh_it.py` | `test_plan_names_cost` |
| refresh: A confirmed refresh runs the chain and the timeline follows it live | Integration | `tests/it/test_app_refresh_it.py` | `test_refresh_runs_chain_timeline_live` |
| refresh: A refresh started elsewhere is followed too | Integration | `tests/it/test_app_refresh_it.py` | `test_follows_refresh_started_elsewhere` |
| refresh: A refresh while another run holds the lock is refused cleanly | Integration + Component | `tests/it/test_app_refresh_it.py`, `app/frontend/src/refresh/RefreshPanel.test.tsx` | `test_refused_while_locked`, `shows a lock refusal as a notice` |
| refresh: A refresh the budget cannot cover is refused before any call | Integration | `tests/it/test_app_refresh_it.py` | `test_budget_refusal_before_call` |
| refresh: Only one app-started refresh runs at a time | Integration | `tests/it/test_app_refresh_it.py` | `test_single_app_refresh` |
| refresh: A failed step shows where the chain stopped | Integration | `tests/it/test_app_refresh_it.py` | `test_failed_step_shown` |
| refresh: Without the operator path there is no refresh | Integration + Component | `tests/it/test_app_refresh_it.py`, `app/frontend/src/refresh/RefreshPanel.test.tsx` | `test_no_refresh_without_operator`, `hides the button without the operator path` |
| inspector: Every query of a page is listed with its exact SQL | Integration | `tests/it/test_app_inspector_it.py` | `test_page_queries_logged_with_exact_sql` |
| inspector: A serving-view query plans no request | Integration | `tests/it/test_app_inspector_it.py` | `test_serving_query_plans_no_request` |
| inspector: A preview shows its planned request and its request columns | Integration | `tests/it/test_app_inspector_it.py` | `test_preview_plan_and_request_columns` |
| inspector: A year range fans out in parallel | Integration + Component | `tests/it/test_app_inspector_it.py`, `app/frontend/src/inspector/Waterfall.test.tsx` | `test_console_fan_out_parallel`, `draws overlapping bars for parallel slices` |
| inspector: A contradiction costs no call | Integration | `tests/it/test_app_inspector_it.py` | `test_console_contradiction_zero_calls` |
| inspector: Stored history costs no call | Integration | `tests/it/test_app_inspector_it.py` | `test_console_stored_zero_calls` |
| inspector: A query over the budget is refused at plan time | Integration | `tests/it/test_app_inspector_it.py` | `test_console_over_budget_refused` |
| inspector: The refresh's requests are listed per run | Integration | `tests/it/test_app_inspector_it.py` | `test_refresh_requests_per_run` |
| inspector: The console exists only with the operator path | Integration + Component | `tests/it/test_app_inspector_it.py`, `app/frontend/src/inspector/Inspector.test.tsx` | `test_console_requires_operator`, `hides the console without the operator path` |
| as-of: The app opens at the current point | Integration + Component | `tests/it/test_app_as_of_it.py`, `app/frontend/src/shell/AsOfPicker.test.tsx` | `test_opens_at_current_point`, `opens at the current point` |
| as-of: Choosing an earlier week moves every page | Integration | `tests/it/test_app_as_of_it.py` | `test_earlier_week_moves_every_page` |
| as-of: Points after the current one are never offered | Integration | `tests/it/test_app_as_of_it.py` | `test_after_current_points_never_offered` |
| as-of: A page without data at a point says so | Integration + Component | `tests/it/test_app_as_of_it.py`, `app/frontend/src/odds/OddsPage.test.tsx` | `test_point_without_data_is_empty`, `states when a point has no published run` |
| as-of: After a refresh the pages follow the new current point | Component | `app/frontend/src/state/followCurrent.test.tsx` | `follows the new current point unless a point is pinned` |
| odds: The odds show the published run with arrows since the last published week | Integration | `tests/it/test_app_odds_it.py` | `test_odds_with_movement_and_banner` |
| odds: Movement is shown with an icon and a word, never by color alone | Component | `app/frontend/src/odds/OddsTable.test.tsx` | `shows movement with an icon and a word` |
| odds: A toggle recomputes the odds over the matching seasons | Integration + Component | `tests/it/test_app_odds_it.py`, `app/frontend/src/odds/WhatIfPanel.test.tsx` | `test_toggle_matches_library`, `shows the effective sample and the low-sample flag` |
| odds: A what-if for a superseded run is reloaded, not shown | Integration + Component | `tests/it/test_app_odds_it.py`, `app/frontend/src/odds/WhatIfPanel.test.tsx` | `test_superseded_run_conflict`, `reloads and clears the toggles when the run changed` |
| odds: Toggles are offered only at the what-if run's point | Integration | `tests/it/test_app_odds_it.py` | `test_toggles_only_at_whatif_point` |
| odds: The rooting guide of a team, without and with toggles | Integration | `tests/it/test_app_odds_it.py` | `test_rooting_guide_with_and_without_toggles` |
| odds: Invalid conditions are refused before any query | Integration | `tests/it/test_app_odds_it.py` | `test_invalid_conditions_refused` |
| predictions: The scorecard headline is the marked row | Integration | `tests/it/test_app_predictions_it.py` | `test_headline_is_marked_row` |
| predictions: A reconstructed headline says so | Integration + Component | `tests/it/test_app_predictions_it.py`, `app/frontend/src/predictions/ScorecardHeadline.test.tsx` | `test_reconstructed_headline_labeled`, `labels a reconstructed headline` |
| predictions: This week's predictions are the ones frozen for the next week | Integration | `tests/it/test_app_predictions_it.py` | `test_this_week_predictions` |
| predictions: Last week's games are graded with their results | Integration | `tests/it/test_app_predictions_it.py` | `test_last_week_graded` |
| predictions: The calibration chart compares claimed and actual win shares | Component | `app/frontend/src/predictions/CalibrationChart.test.tsx` | `draws one dot per bucket and a matching table` |
| predictions: The population switch changes every number | Integration | `tests/it/test_app_predictions_it.py` | `test_population_switch` |
| upsets: The upset of the week shows its all-time rank | Integration | `tests/it/test_app_upsets_it.py` | `test_upset_of_the_week` |
| upsets: A shared rank reads T-n | Unit + Component | `app/frontend/src/format/format.test.ts`, `app/frontend/src/upsets/UpsetsPage.test.tsx` | `formats shared ranks as T-n`, `shows the source next to every rank` |
| upsets: Upsets before the all-time window rank only within their season | Integration | `tests/it/test_app_upsets_it.py` | `test_pre_window_upset_season_rank_only` |
| upsets: The all-time and line-era lists come from their ranks | Integration | `tests/it/test_app_upsets_it.py` | `test_all_time_and_line_era_lists` |
| upsets: A week without an upset says so | Integration | `tests/it/test_app_upsets_it.py` | `test_week_without_upset` |
| head-to-head: The series shows without a call | Integration | `tests/it/test_app_head_to_head_it.py` | `test_series_without_call` |
| head-to-head: Opening the page never fetches a preview | Integration + Component | `tests/it/test_app_head_to_head_it.py`, `app/frontend/src/headtohead/PreviewCard.test.tsx` | `test_page_load_makes_no_request`, `never fetches on load` |
| head-to-head: A click fetches the next meeting's preview once | Integration + Component | `tests/it/test_app_head_to_head_it.py`, `app/frontend/src/headtohead/PreviewCard.test.tsx` | `test_click_fetches_next_meeting_once`, `shows fetching until the row arrives` |
| head-to-head: A pair without a meeting left previews its latest meeting | Integration | `tests/it/test_app_head_to_head_it.py` | `test_latest_meeting_preview` |
| head-to-head: A second click in the same session is served from its cache | Integration | `tests/it/test_app_head_to_head_it.py` | `test_session_cache` |
| head-to-head: Teams that never met have no series and no preview | Integration | `tests/it/test_app_head_to_head_it.py` | `test_never_met` |
| shell: One process serves the app and its API | Integration | `tests/it/test_app_shell_it.py` | `test_one_process_serves_app_and_api` |
| shell: The footer credits CFBD on every page | Component | `app/frontend/src/shell/Footer.test.tsx` | `credits CFBD on every page` |
| shell: Both themes keep text legible on a projector | Unit + Component | `app/frontend/src/theme/tokens.test.ts`, `app/frontend/src/shell/ThemeSwitch.test.tsx` | `meets the contrast and size floors`, `switches, persists and survives storage errors` |
| shell: A database outage shows a clear state, not a crash | Integration + Component | `tests/it/test_app_shell_it.py`, `app/frontend/src/shell/AppShell.test.tsx` | `test_database_outage_clear_state`, `shows an outage banner with retry` |
| shell: The chat slot receives the app state | Component | `app/frontend/src/shell/ChatSlot.test.tsx` | `receives the app state and drives it` |

64 scenarios, each mapped to at least one test. Component tests render real components against synthetic API fixtures that the backend's response models validate (`tests/unit/test_app_fixtures.py`). Unit tests cover only pure computation: the contrast tokens and the rank format.

Supporting tests back the integration tests; they do not replace them:
- **Unit** (pure, no I/O):
  - `tests/unit/test_app_settings.py`, `test_app_security.py`, `test_app_spend.py`, `test_app_sessions.py`, `test_app_sqlcatalog.py`, `test_app_explain.py`, `test_app_console_catalog.py`, `test_app_fixtures.py`, `test_app_perf.py`;
  - `test_app_db.py` and `test_app_operator.py`, with fake connections;
  - `test_loader_guard.py` and `test_livecalls.py`;
  - frontend: `format.test.ts` and `tokens.test.ts`.
- **CLI:** `tests/cli/test_app_cli.py`; Phase 2's `tests/cli/test_loader_cli.py` unchanged.
- **Smoke:** `tests/it/test_app_stack_smoke.py`.
- **E2E:** `tests/it/test_app_e2e_it.py` with `app/frontend/e2e/smoke.spec.ts`, or its API-level fallback.

### Manual Testing

All commands run from the repo root against Exasol Personal local (`TARGET=local`) after `make deploy`, `make grants`, `make app-user` and `make app-build`, with `make app-serve` running, unless a row names AWS. None spends a CFBD call, except the rows marked live (task 8.2, ledgered).

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| app/access | `curl -s http://127.0.0.1:8000/api/config`; `APP_HOST=0.0.0.0 make app-serve` and `curl -s http://<lan ip>:8000/api/config`; `APP_OPERATOR=on APP_HOST=0.0.0.0 make app-serve`; `curl -s -X POST http://127.0.0.1:8000/api/refresh/plan -d '{}'` | Operator on (loopback); operator off with the reason "bound to a non-loopback address"; the third refuses to start, naming `APP_OPERATOR` and the address; the `POST` without `X-CFB-Action` answers 403 |
| app/live-spend | Head-to-head, a pair with a coming-week meeting: read the preview button; in replay, click it; then click again | "replay · 0 calls", then a row with `REPLAY` and "0 calls spent", then "cached · 0 calls"; `make record ARGS="--ledger"` unchanged. *(live, AWS, task 8.2)* the label reads "1 live call", and after the click "1 live call spent" and one ledger entry under `add-cfb-app` |
| app/refresh | In replay: refresh, pick the next week, run | The button reads "0 calls · replay"; the timeline shows `CHECK`, `FETCH …`, `MODEL`, `PREDICT`, `SIMULATE` and `PUBLISH` turning `RUNNING` then `DONE` with seconds, rows and calls; the header moves to the new week. Then `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(...)` in a SQL client: the app's timeline follows it without a click |
| app/inspector | Open the inspector on the odds page → plan of `PLAYOFF_ODDS`; console `contradiction` → explain, run; `stored` → run; `over-budget` → explain | "no virtual table: 0 calls"; 0 slices and 0 calls; rows `STORED` and 0 calls; refused with "15 requests planned, budget 10". *(live, AWS)* `fan-out` → 3 overlapping bars and 3 calls |
| app/as-of | Pick last week in the picker; copy the URL into a new tab; edit the URL to a later week | Every page moves to last week; the new tab shows the same view; the later week falls back to the current point with a notice |
| app/odds | Odds page; focus a bubble team; set two toggles on the coming week's games | Arrows with words and point changes since last week; "100,000 seasons in N s on …"; conditional odds with "n of 100,000 seasons" and the method label; the team's coming-week guide labeled conditional |
| app/predictions | Predictions page at the current point; switch the population | The headline record (for example "41–9") with the line beside it, labeled "reconstructed …" when the freeze kind is `ALL`; this week's predictions; the calibration dots and table; every number changes with the population |
| app/upsets | Upsets page at the current point | The upset of the week with "since 1978", "T-n" where tied, the source beside every rank; the all-time and line-era top 25 |
| app/head-to-head | Michigan vs Ohio State | The series since 1897 at 0 calls in < 1.2 s; the next meeting with our prediction and stakes, or the latest meeting as the preview's game; the preview only after a click |
| app/shell | `curl -sI http://127.0.0.1:8000/odds`; switch the theme and reload; stop the database briefly (`exasol stop -d local` in a local free window), then start it | `200`, `text/html`, the CSP header; the theme is kept; an outage banner with retry, then the data again without restarting the server; the footer "Data provided by CollegeFootballData.com" on every page |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle && make app-build` | Exit 0; `app/frontend/dist/index.html` exists |
| Test | `make test` | 0 failures (Python unit + CLI, vitest) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| E2E | `flock <scratchpad>/cfb-it.lock make app-e2e` | 0 failures (Playwright, or the recorded API-level fallback) |
| Lint | `make lint` | 0 ruff, mypy, eslint and `tsc` errors |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % for `vs/cfbd_vs`, `vs/gen`, `loader`, `model`, `engine`, `analyses`, and the backend's pure modules |
| Page SQL | `uv run pytest tests/unit/test_app_sqlcatalog.py` | Every read statement names only `{API_SCHEMA}` and `{API_LIVE_SCHEMA}` objects |
| Operator allowlist | `uv run pytest tests/unit/test_app_operator.py` | Every operator statement matches an allowlist template |
| Secrets | `git grep -n -E 'CFB_APP_PASSWORD[[:space:]]*=[[:space:]]*[^[:space:]]' -- . ':!docs' ':!specs'`; `flock <scratchpad>/cfb-it.lock uv run pytest tests/it/test_app_access_it.py -k no_secret` | No `CFB_APP_PASSWORD` value in a tracked file outside documentation (`.env` is ignored); the scan of every response and of `dist/` for the test passwords and the marker key passes (a plain search for the word "password" would match react-dom's input-type table) |
| Performance | `make app-perf` (task 6.3); task 8.2 on AWS | Budget met, or a logged decision with the breakdown; no live endpoint timed |
| Deploy | `make deploy && make grants && make app-user && make app-build && make app-serve` | The server starts on 127.0.0.1:8000 with the operator path on; `/api/health` 200 |
| Zero calls | `make record ARGS="--ledger"`; `make quota TARGET=aws` before and after task 8.2 | Ledger entries for `add-cfb-app` ≤ 12 (8 planned), one to one with the `FETCH_LOG` rows of the window; the `/info` drop equals the sum of every ledger entry in the window |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json' 'cache/*' 'app/frontend/dist/*'` | Empty; the fixtures are synthetic |
