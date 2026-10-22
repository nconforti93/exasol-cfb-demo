# Decision Log: add-cfb-app

Date: 2026-10-22 (planned)

## Interview

The owner (Nico) was away and authorized the orchestrator to decide and log. The answers below are the orchestrator's, as it passed them to the planner.

**Q:** What is the scope?
**A:** Roadmap Phase 6, "Phase 6: App", in `docs/roadmap.md`. A FastAPI and React app:
- **Pages:** odds, predictions with the scorecard, upsets, head-to-head.
- **Extras:** a SQL↔HTTP inspector, a refresh button with a step timeline, an as-of week picker, and the CFBD credit in the footer.

It serves demo beats 1–4 of the roadmap's "Demo flow (~25 min)":
- the hook, "the app as of Sunday before week N+1";
- the inspector showing the HTTP behind the SQL, the fan-out and the 0-call contradiction;
- the `REFRESH_WEEK` timeline;
- the pages moving: the scorecard, the upset of the week ranked all-time, the playoff odds with arrows, what-if toggles with a rooting guide on a bubble team, and head-to-head with a live preview fetched on click.

Phase 7 (the chat panel) and Phase 8 (demo hardening, the AWS scale dial, the replay rehearsal) come later. Leave a clean slot for the chat panel.

**Q (1) Contract:** What does the app read, and what is left to Phase 6?
**A:** The app reads only `CFB_API` and `CFB_API_LIVE`, as the `CFB_APP` role. The contract is in `specs/_plans/add-cfb-analyses/`, just revised: read its `api/*` specs, the `plan.md` Key Interfaces and its decision log. Points from that plan:
- the page contract;
- `IS_HEADLINE` and `FREEZE_KIND` for the scorecard;
- `IS_AFTER_CURRENT` and the what-if publication;
- the what-if column views, regenerated per publication, and `WHATIF_VIEWS_CURRENT`;
- `GAME_PREVIEW`: 1 call per query, never on page load, a fetching state, literal game ids; it previews the next meeting, otherwise the latest one;
- "consumers use autocommit";
- the arrows at a threshold of 0.0070;
- "T-n" ties.

Open Phase 6 decisions that plan left to you:
- how the backend writes `FETCH_LOG` for previews;
- the owner path for the refresh, forced re-sims and publication from the app. The app's role is read-only, so the refresh button needs a separate, narrowly scoped owner path. Design it; for example, the backend runs `EXECUTE SCRIPT` as a dedicated runner user with only `EXECUTE` on the specific scripts;
- per-session preview caching.

**Q (2) Inspector:** What does it show, and from where?
**A:** For a page query, it shows the SQL sent, the `EXPLAIN VIRTUAL` manifest (slice keys, `sources`, budget) and the `REQ_*` columns. The data comes from `CFB_API.FETCHES` and `REFRESH_TIMELINE` and from the manifest; see the foundation specs `specs/cfbd-vs/*` and `specs/cfbd-fetch/*`. The demo moments:
- a year range fans out in parallel;
- a contradiction costs 0 calls;
- stored slices cost 0 calls.

Decide how the backend gets the `EXPLAIN VIRTUAL` output while the app role is read-only, and record the finding. Probe 1.4g showed that readers may run `EXPLAIN VIRTUAL`.

**Q (3) Refresh button and timeline:** How does it work?
**A:**
- The button runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(season, week, season_type)`, which returns the step timeline (see the Phase 2 plan's `loader/refresh-week` spec, in the worktree `../exasol-cfb-demo-p2/specs/_plans/add-cfb-persistence-refresh/`).
- Show the progress live by polling `REFRESH_TIMELINE`, since each step commits its log row.
- The run lock refuses concurrent runs; show that refusal cleanly.
- The quota and the ledger caps apply.
- In the UI, live CFBD spend must always be explicit.

**Q (4) As-of week picker:** What does it drive?
**A:** Every page, through the `AS_OF_POINTS` and `CURRENT_POINT` semantics.

**Q (5) Tech choices:** Which stack, build and tests?
**A:**
- **Backend:** Python FastAPI with pyexasol. Reuse `vs/host/target.py` for `TARGET=local|aws`.
- **Frontend:** React with Vite and TypeScript. Keep dependencies minimal. Use a charting library only if it is needed.
- **Build and serve:** `make app` (dev) and `make app-build`. In production, FastAPI serves the built frontend as a single process. It must also run on AWS for Phase 8.
- **Tests:**
  - backend: pytest unit tests, plus API integration tests against the `TEST_` namespace with the existing harness and the mock sidecar;
  - frontend: component tests with vitest and Testing Library, plus a small Playwright E2E smoke against a local stack with synthetic data. Use Playwright only if it installs cleanly; otherwise record that and fall back to API-level tests;
  - zero CFBD calls in tests.
- Check the current stable versions with the context7 or ext-research tools. Don't guess versions.

**Q (6) Accessibility and design:** What standard?
**A:** A clean, demo-grade UI: legible on a projector, dark and light themes, a credit footer. Charts follow the `dataviz` skill guidance if you load it.

**Q (7) Security:** What rules?
**A:**
- No secrets in the frontend.
- The backend reads its database credentials through `target.py`, or uses a dedicated app user whose password comes from the environment or `.env`, never committed.
- Bind to localhost by default.
- Provide a CORS policy for development.

**Q (constraints, from the orchestrator):** What limits apply to planning?
**A:**
- No DB sessions; other agents are using them.
- No CFBD calls.
- Don't edit `docs/roadmap.md`, `specs/architecture.md` or `CLAUDE.md`. List the needed edits as tasks.
- Invoke `exasol:exasol-database` through the Skill tool before making Exasol claims (the owner's standing rule).
- TDD tiers, as in the other plans.
- Write spec deltas in the domain `app/`, plus `plan.md` and `decision-log.md` in `specs/_plans/add-cfb-app/`, with the same structure as the other plans (Verification with Scenario Coverage, Manual Testing and Checklist). Tag `[expert]` sparingly. Run `speq plan validate add-cfb-app`.

## Design Decisions

### [1] Domain `app/` with ten features; code in `app/backend` and `app/frontend`

- **Decision:** ten features under `app/`: `access`, `live-spend`, `refresh`, `inspector`, `as-of`, `odds`, `predictions`, `upsets`, `head-to-head` and `shell`. Python lives in `app/backend` and TypeScript in `app/frontend`, as the mission's project structure plans it. Two cross-cutting features (`access`, `live-spend`) hold the rules every page and action obeys, so the page specs stay about what a page shows.
- **Alternatives:** one feature per page only, with the security and spend rules repeated in each; a separate `api/` addition.
- **Rationale:** the `api/` domain is Phase 5's contract; `app/` is its consumer. The spend and access rules are what the owner will review and what tests must pin, so they get their own specs.
- **Promotes to ADR:** no

### [2] Two principals: a read path for the pages, an operator path for an allowlist, run by the namespace owner

- **Decision:**
  - **Read path.** Every page query, what-if query, preview fetch and timeline read runs as the app user `CFB_APP_WEB`, which holds only `CREATE SESSION` and the role `CFB_APP`.
  - **Operator path.** The refresh, the fetch-log rows of the app's own requests, and the curated console run as the namespace owner: `sys`, through `vs/host/target.py`, with the password read at connect time and never stored; `CFB_TEST_OWNER` in `TEST_`. They run on one connection per action, and only for a fixed allowlist of statement templates, pinned by a unit test.
- **Finding** (the interview's example, re-checked by probe 1.1(a)): Exasol Lua scripting programs run with the caller's rights, not their owner's. The project memory has this verified on 2026-06-05 in the preprocessor framework ("Lua scripts run with caller (invoker) rights; no definer-rights option; views read base tables with OWNER rights"); the Exasol skills do not cover it. A user with only `EXECUTE` on `REFRESH_WEEK` therefore fails at its first read of `CFB_META` or its first write. The refresh writes `CFB_CORE`, `CFB_META`, `CFB_STAGE`, `CFB_SIM` and `CFB_PRED`, and runs DDL in `CFB_SIM` and `CFB_API`.
- **Alternatives:**
  - An `EXECUTE`-only runner user: does not work under invoker rights.
  - A runner user granted every write privilege, `CREATE ANY TABLE` / `VIEW` and dictionary access (Phase 2's stale-lock check reads sessions): an owner in all but name, with one more password to manage. Phase 2's [20] already chose `sys` ownership without a `CFB_OWNER`.
  - `GRANT IMPERSONATION ON SYS TO` the app user (the grammar has `IMPERSONATE`): any SQL bug in a page could become `sys`.
  - A request-queue table that the app inserts into, with an owner-side worker: gives the app a write path (Phase 5 rejected it in [8]) and needs a second process.
  - A separate runner process that holds the owner credentials: on a laptop the OS user can read `secrets.json` anyway, so it adds a process without a real boundary.
- **Rationale:** the only principal that can refresh is an owner. Keeping the owner's use narrow (an allowlist, fixed statements, validated integers and enumerations) and tied to the bind address ([3]) gives the presenter one button without making the read path any stronger. If probe 1.1(a) shows definer rights, the operator's refresh switches to an `EXECUTE`-only runner user. The allowlist stays the same, and the change is logged.
- **Promotes to ADR:** yes

### [3] The operator path exists only on a loopback bind

- **Decision:** `APP_OPERATOR` is `auto` (the default: on if and only if the bind address is loopback), `on` (refuses to start on a non-loopback address) or `off`. With it off, the app is read-only: no refresh, no console, no live request. `GET /api/config` gives the reason. The default bind is `127.0.0.1:8000`.
- **Alternatives:** always on; a shared-secret header; authentication in Phase 6.
- **Rationale:** the owner can do anything, so the backend must hold that connection only where the presenter's machine is the only client. Authentication is a Phase 8 decision (the AWS-hosted app), recorded as a hand-off. Until then, a network-exposed app shows everything except the owner actions.
- **Promotes to ADR:** yes

### [4] Allowlist = refresh, fetch-log writes, curated console; forced re-sims and publication stay CLI

- **Decision:** the app starts only `REFRESH_WEEK`. Forced re-sims (`make simulate BASE= FIX=`), manual publications (`make publish`), `make predict` and `make sim-drop` stay owner CLI actions. Forced runs are not shown in this phase; `CFB_API.FORCED_RUN_ODDS` stays for the chat.
- **Alternatives:** buttons for `SIMULATE_FROM` and `PUBLISH_RUN`; showing forced runs read-only.
- **Rationale:** beats 1–4 need only the refresh. A forced run at 1M takes about 40 s and writes (Phase 5 [8]). The 1M scale dial is Phase 8 and will add one allowlist entry then. Showing forced runs would add a page section that serves no beat.
- **Promotes to ADR:** yes

### [5] The fetch log of the app's own requests is written by the operator path

- **Decision:**
  - After a preview or console query, the backend builds `FETCH_LOG` rows with the reference capture's rules (`vs/host/fetchlog.build_fetch_log_rows`). The input is the result's distinct `REQ_*` values plus the manifest of the same statement's `EXPLAIN VIRTUAL`.
  - It inserts them on the operator path, with `CONTEXT` `APP_PREVIEW#<action id>` or `APP_CONSOLE#<action id>`.
  - `REQ_ROWS` comes from the view (hand-off P6-a), else from counting the result rows per slice.
  - Without the operator path a live request is refused, so nothing live goes unlogged. Replayed answers are served and noted as not persisted.
- **Alternatives:**
  - `INSERT` on `FETCH_LOG` for `CFB_APP`: breaks Phase 5's "the app role cannot change data", and the role is shared by design;
  - a separate logger user with only that `INSERT`: a third principal and password for one table;
  - not logging previews: breaks the architecture's "the consumer that ran the query on purpose writes the fetch log", and starves `PROMOTE()` and `QUOTA`'s accounting.
- **Rationale:** one principal writes; the read path stays read-only; every fetch the app causes is in the same log as the loader's.
- **Promotes to ADR:** yes

### [6] One spending guard for every action that can call CFBD

- **Decision:**
  1. **Plan at 0 calls:** the `EXPLAIN VIRTUAL` manifest or the refresh estimate.
  2. **Show the cost:** a label on the control, plus a confirmation for live refreshes and console runs.
  3. **Re-plan on the server against the request's `max_calls`,** refusing more than was confirmed.
  4. **Check the ledger and the quota:** `Ledger.check` for the app's plan with `planned + MAX_ATTEMPTS − 1`, the month's authorization, and the remaining calls.
  5. **Require the operator path** for a live request.
  6. **Act, then record:** one ledger entry per live request, fetch-log rows ([5]), and the calls spent in the response.

  The refresh uses Phase 2's own guard ([12]) with the app's ledger plan.
- **Alternatives:** labels only; per-feature checks; trusting the client's label.
- **Rationale:** "live CFBD spend must always be explicit" holds by construction. The replay/live switch is a virtual-schema property that can change between showing and clicking, and the re-plan closes that gap. The ledger stays the single record of live calls (CLAUDE.md).
- **Promotes to ADR:** yes

### [7] Ledger entries per click without `/info` readings; the quota reading is cached for 5 minutes

- **Decision:**
  - A live preview or console request is charged its attempts, or `MAX_ATTEMPTS` when they are unknown, the recorder's rule when no readings exist. Its `remaining_before` and `remaining_after` stay null.
  - The guard uses the latest `CFB_API_LIVE.QUOTA` reading if it is at most 5 minutes old, else reads it again.
  - The app's spend counts under the ledger plan `app` (`APP_LEDGER_PLAN`). A prefixed namespace never writes the project ledger.
- **Alternatives:** `/info` before and after every click, as `make record --query` does; no ledger for the app.
- **Rationale:** each `/info` read is a virtual-table query (about 0.6 s on the database side), and two of them would push the preview past the 2 s target. The retry headroom in the check already bounds what a request can cost.
- **Promotes to ADR:** no

### [8] The preview cache belongs to the browser session

- **Decision:** previews are cached per anonymous session cookie (`HttpOnly`, `SameSite=Strict`), keyed by game id, for 15 minutes, with at most 20 per session. A cached answer reads "cached <time> · 0 calls". "Fetch again" is explicit and labeled.
- **Alternatives:** a process-wide cache; no cache; storing previews (`PROMOTE()`).
- **Rationale:** this was Phase 5's hand-off. The viewer who chose to spend owns the result, and a demo segment fits in 15 minutes. A global cache would show one viewer's paid data to another without their act. `PROMOTE()` stays deferred.
- **Promotes to ADR:** no

### [9] The inspector records the sent text and explains it on demand as the same principal

- **Decision:**
  - Per browser session, the backend keeps the last 50 statements with pyexasol's `stmt.query`, which is the text the database received. It also keeps the principal, the milliseconds, the rows, the `REQ_*` lines per slice, and the calls.
  - On request, it runs `EXPLAIN VIRTUAL` of that text as the same principal: the read user for pages and the preview, the operator for the console.
  - A query over `CFB_API` alone has no pushdown and is shown as "no virtual table: 0 calls".
  - Refresh runs are shown from `CFB_API.FETCHES` and `REFRESH_TIMELINE`.
- **Finding (how the backend gets `EXPLAIN VIRTUAL` while the app role is read-only):** `EXPLAIN VIRTUAL` is a read statement. It asks the adapter for the pushdown and does not run the fetch UDF, so it costs 0 calls. A reader with `SELECT` on the virtual schema may run it (foundation probe 1.4g), and the key never appears in it (architecture). Through a serving view with only schema `SELECT`, Phase 5's probe 1.1(b) and this plan's probe 1.1(b) confirm it. No owner rights are needed, and the plan shown is exactly the one for the user who ran the query.
- **Alternatives:** explain every query eagerly (an adapter round trip per page query for nothing); rebuild the SQL on the client (it would not be the sent text); explain as the owner (another principal's plan).
- **Rationale:** evidence, not claims. The client references entries by id and never sends SQL.
- **Promotes to ADR:** yes

### [10] Beat 2 in the app uses a curated four-query console on the operator path

- **Decision:** the inspector's console offers four fixed statements over `CFBD_VS`:
  - `fan-out`: `GAMES_TEAMS` for Michigan over 2022–2024, 3 slices;
  - `contradiction`: the same with `P_YEAR = 2023 AND P_YEAR = 2024`, 0 slices;
  - `stored`: `GAMES` of 1997, 1 stored slice;
  - `over-budget`: 2010–2024, 15 slices, refused at plan time.

  Each can be explained at 0 calls and run through the spending guard. No free text is accepted.
- **Alternatives:**
  - free-text SQL as a reader user (`CFB_READER`): an injection surface and unbounded spend in a web page;
  - a third database user for the console: another password, with the curated text running as an owner already safe because it carries no input;
  - a `CFB_API_LIVE` view per demo query: changes Phase 5's contract for demo props;
  - leaving beat 2 to a SQL client: loses the inspector's waterfall and plan view.
- **Rationale:** `GAME_PREVIEW` has no year parameter, so a year-range fan-out needs the virtual schema itself. Curated text has exact expected plans (pinned against the catalog in task 1.7), costs at most 3 calls, and has no input. `GAMES_TEAMS` is federated and needs year plus team, week or conference, so a team filter keeps it selective.
- **Promotes to ADR:** yes

### [11] Refresh: plan, confirm, background run, follow any run by polling, map refusals to 409, no cancel

- **Decision:**
  - `POST /api/refresh/plan` and `POST /api/refresh/run` run on the operator path. The run executes in a background thread on its own owner connection (socket timeout 900 s) and returns an action id at once. One app-started refresh runs at a time per process.
  - The timeline polls `CFB_API.REFRESH_TIMELINE` on the read path: every 1 s while the newest refresh run is `RUNNING`, else every 5 s. It follows any run, whether started by the app, by `make refresh` or from a SQL client.
  - The lock and budget refusals are mapped to 409 with the database's message.
  - There is no cancel button.
- **Alternatives:** server-sent events; running the script inside the HTTP request; a cancel button.
- **Rationale:** Phase 2 commits each step's row at its start and end, so polling a serving view is correct and needs no channel. A 2-minute statement must not hold an HTTP request. Following runs started from a SQL client keeps beat 3's "one SQL call" true on stage. Cancelling is a `KILL STATEMENT`, an owner act, and Phase 2's stale rules clean up after it.
- **Promotes to ADR:** yes

### [12] Phase 2's refresh guard is extracted into `loader/guard.py`, and the CLI and the app share it

- **Decision:** the estimate, live check, quota, ledger verdict, room and record logic of `scripts/loader_cli.py` moves into `loader/guard.py`, with structured results and the ledger plan as a parameter. The CLI keeps its output and exit codes. The recorder's pure cost helpers move to `vs/host/livecalls.py`.
- **Alternatives:** import `scripts.loader_cli` internals from the app; re-implement the guard in the backend.
- **Rationale:** one implementation of what a refresh may spend and how it is recorded, already pinned by Phase 2's CLI tests. This is a cross-file refactor with behavioral dependencies, hence `[expert]`.
- **ADR scope:** every host caller that spends CFBD calls uses the shared guard.
- **Promotes to ADR:** yes

### [13] Backend runtime: one worker, a 4-session read pool, autocommit, a query timeout, one retry

- **Decision:**
  - uvicorn runs one worker, and the endpoints are synchronous (FastAPI runs them in its thread pool).
  - The read pool holds 4 pyexasol sessions as the read user, with autocommit and a 30 s query timeout (per probe 1.1(e)). A transaction-conflict error is retried once.
  - An unreachable database answers 503 with `TargetError`'s message and no driver text.
  - The in-process state (sessions, actions, the quota reading) lives in memory.
- **Alternatives:** several workers; a connection per request; autocommit off.
- **Rationale:** a pyexasol connection is one session, runs one statement at a time, and must not be shared between threads (threadsafety 1, pyexasol docs). The page contract requires autocommit (Phase 5). The state that keeps spending safe (single flight, the session cache) must live in one process.
- **Promotes to ADR:** no

### [14] Frontend stack: React 19, Vite 8 and TypeScript 6.0, with no router, state, data-fetching or chart library

- **Decision:** the runtime dependencies are `react` and `react-dom` only. The build and tests use Vite, `@vitejs/plugin-react`, TypeScript 6.0, vitest with jsdom and Testing Library, eslint with typescript-eslint and react-hooks, and Playwright if task 1.2's gate passes.
  - Versions were checked on 2026-10-22 (`plan.md`, Key Interfaces).
  - At implementation, take the newest release that is at least 7 days old within these majors, pin it exactly and commit the lockfile.
  - Charts are four hand-written SVG forms (inline bars, a dot plot with a reference line, timeline bars, a waterfall), each with a table view.
- **Alternatives:** TypeScript 7.0.2 (current), react-router, TanStack Query, a chart library such as Recharts.
- **Rationale:**
  - typescript-eslint 8.72.0 declares the peer `typescript >=4.8.4 <6.1.0`, so TypeScript 7 would break linting.
  - Four routes, one store and four simple chart forms do not justify libraries, and a public Labs repo has less to keep current.
  - FastAPI shipped 0.159.0, 0.159.1, 0.160.0 and 0.161.0 within 24 hours, hence the 7-day rule.
- **Promotes to ADR:** no

### [15] Production: FastAPI serves `dist` with an SPA fallback and a strict CSP; development: Vite with a proxy

- **Decision:**
  - `make app-serve` runs one process that serves `/api/*` and `app/frontend/dist`. A catch-all GET returns `index.html` for the client routes. Hashed assets get long caching and `index.html` gets `no-cache`.
  - The headers are a CSP limited to the app's own origin (images from `data:` too, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
  - `make app` runs uvicorn with `--reload` plus Vite, which forwards `/api`. CORS is allowed only for the origins in `APP_CORS_ORIGINS` (the Vite origins in development; `*` is refused).
  - Every `POST` needs `X-CFB-Action: 1` and an allowed `Origin`.
- **Alternatives:** nginx in front; hash routing; CORS `*` in development.
- **Rationale:** "a single process" (interview item 5). Starlette's `StaticFiles(html=True)` serves `index.html` only for directories and `404.html` otherwise (source via context7), so it has no SPA fallback. The custom header with the `Origin` check stops cross-site requests from acting even when the cookie would be sent.
- **Promotes to ADR:** no

### [16] The read user's password comes from the environment or `.env`; `make app-user` creates the user; the start refuses a writable read user

- **Decision:**
  - `CFB_APP_USER` (default `CFB_APP_WEB`) and `CFB_APP_PASSWORD` come from the environment, then from `.env`, which git ignores.
  - `make app-user` runs as the owner. It creates the user with that password, plus `CREATE SESSION` and `CFB_APP`. It is idempotent; `ROTATE=yes` changes the password, and it refuses when the role or the password is missing.
  - At start, the backend refuses a read user that is `sys` or the owner, or that holds any other system privilege or role (per probe 1.1(d)).
  - No tool generates or writes a password.
- **Alternatives:** the tool generates a password and writes `.env`; the read path as `sys` in development; a documented manual `CREATE USER` only.
- **Rationale:** the precedent `CFB_TEST_OWNER_PASSWORD` (environment or `.env`, `scripts/grants.py`). Phase 5 left user creation to the owner, and this target is the owner's tool. The check makes "pages are read-only" a fact that cannot be misconfigured silently.
- **Promotes to ADR:** yes

### [17] The as-of point lives in the URL; pages follow the current point unless one is pinned

- **Decision:**
  - The point is `?season=&week=` (the week sequence). An unknown point, or one after the current point, falls back to the current point with a notice.
  - Only points with `IS_AFTER_CURRENT` FALSE are offered, one season at a time.
  - Without an explicit choice, the app reloads every page when `CURRENT_POINT` changes. With one, it shows a notice instead.
  - The next meeting is always "as of the current point", and is labeled so.
- **Alternatives:** session-only state; always follow; offering every point.
- **Rationale:** links reproduce a view. Beat 4's "pages move" happens without a click. The page contract forbids later points.
- **Promotes to ADR:** no

### [18] What each page reads

- **Decision:** as in `plan.md`, Key Interfaces, "Pages and the views they read":
  - **Odds:** `PLAYOFF_ODDS`, `PUBLISHED_RUNS` and `ANALYSIS_SETTINGS`; the what-if (`WHATIF_RUN`, `WHATIF_GAMES` and the library over `WHATIF_OUTCOMES` and `WHATIF_PLAYOFF`); the guide (`ROOTING_GUIDE` and the library).
  - **Predictions:** `PREDICTIONS` and `SCORECARD_WEEKS`, `SEASONS` and `CALIBRATION`.
  - **Upsets:** `UPSETS`.
  - **Head-to-head:** `TEAMS`, `MATCHUP_SERIES`, `MATCHUP_GAMES`, `MATCHUP_NEXT` and `CFB_API_LIVE.GAME_PREVIEW`.
  - **Header:** `CURRENT_POINT`, `AS_OF_POINTS` and `CFB_API_LIVE.QUOTA`.
  - **Timeline and inspector:** `REFRESH_TIMELINE` and `FETCHES`.
  - The four pass-through views and `FORCED_RUN_ODDS` are not read by the app.
- **Alternatives:** pages reading `CFB_PRED` or `CFB_SIM` directly (forbidden by the layering); a backend cache of page data.
- **Rationale:** Phase 5's page contract, one view per question.
- **Promotes to ADR:** no

### [19] The chat slot: a state store, a region and a reserved path, rendered by nothing in this phase

- **Decision:**
  - One `AppState` store holds the point, the page, the focus team, the what-if conditions and the inspector's state, with actions that set each.
  - The chat region renders only when `GET /api/config` reports the chat enabled, which it never does in Phase 6.
  - `/api/chat/` is reserved and answers 404.
  - A component test proves that the slot receives the state and that its action drives the what-if toggles.
- **Alternatives:** nothing (Phase 7 would rework the layout and the state); a visible placeholder panel.
- **Rationale:** Phase 7's app-state tools set what-ifs and read the app state; the store is that interface, now.
- **Promotes to ADR:** no

### [20] Tests: API integration tests on the `TEST_` stack, component tests on validated fixtures, a gated Playwright smoke

- **Decision:**
  - **Backend scenarios** are integration tests through FastAPI's `TestClient`, on the app test stack: the `TEST_` namespace, the sim league, the mock, replay, and a temporary ledger.
  - **UI scenarios** are vitest and Testing Library component tests on synthetic JSON fixtures, which the backend's pydantic response models must parse (`tests/unit/test_app_fixtures.py`).
  - **Pure code** gets unit tests.
  - **The E2E smoke** uses Playwright if it installs cleanly in task 1.2, else API-level flows over the served app, with the reason logged.
  - Coverage ≥ 90 % applies to the backend's pure modules. The frontend has no coverage gate.
  - Zero CFBD calls everywhere.
- **Alternatives:** mocked-database API tests; E2E for every scenario; a frontend coverage gate.
- **Rationale:** the risk sits in the database behavior (roles, plans, locks, the ledger), which only the real stack shows. Validated fixtures keep component tests honest. Playwright stays optional, as the interview asks.
- **Promotes to ADR:** no

### [21] Live verification: at most 12 calls, 8 planned, only in an authorized month

- **Decision:** on AWS, from the laptop's loopback-bound app (`make app-serve TARGET=aws`): 1 preview, the `fan-out` console query (3 calls) and 1 refresh from the button (an estimate of 4). All go through the ledger under `add-cfb-app`, capped at 12. They run in a window agreed with the orchestrator, after `make dns-check TARGET=aws`, and only in a month the ledger authorizes (November 2026 is not authorized as of 2026-10-22).
- **Alternatives:** 0 calls (the TLS-pinned live path and the real latency would stay unproven); a full live demo run.
- **Rationale:** each live mechanism (the read-path preview, the operator console, the operator refresh) is proven once. Everything else is proven in replay at 0 calls.
- **Promotes to ADR:** no

### [22] Projector legibility and the dataviz method

- **Decision:**
  - Base text is at least 18 px, with tabular figures. Contrast is at least 4.5:1 for text and 3:1 for controls and chart marks, in both themes; `tokens.test.ts` computes it from the tokens.
  - The palette is run through the dataviz validator for `--mode light` and `--mode dark`, and the report goes into `docs/app-verification.md`.
  - **Color by job:** movement is polarity (two diverging hues), always with an arrow icon and a word. Timeline states use the status palette with an icon and a label. The odds bars use one sequential hue.
  - Every chart has a table view, and no chart has two axes.
  - The theme follows the system preference, can be overridden, and is remembered when storage works (wrapped in try/catch).
- **Alternatives:** a chart library's default theme; color-only arrows.
- **Rationale:** interview item 6, and the mission's audience sits in a room with a projector.
- **Promotes to ADR:** no

### [23] What-if interaction

- **Decision:**
  - The toggles come from `WHATIF_GAMES`, coming week first, with at most 16 set (the library's limit).
  - Changes are debounced by 250 ms, and the request carries the page's `WHATIF_RUN_ID`.
  - The library's errors become 400 before any statement runs. An empty result or an unknown-column error becomes 409 `whatif_run_changed`; the page reloads `WHATIF_RUN` and `WHATIF_GAMES` and clears the toggles.
  - Toggles appear only at the what-if run's point, and only while `WHATIF_VIEWS_CURRENT`.
  - The conditional guide covers the coming week only, labeled conditional.
- **Alternatives:** recompute on every click without debounce; keep the toggles across a run change.
- **Rationale:** Phase 5's page contract, which this decision makes concrete.
- **Promotes to ADR:** no

## Open Questions (assumption + verify step)

| # | Assumption | Verify step | If wrong |
|---|---|---|---|
| Q1 | Lua scripting programs run with the caller's rights on Personal 2026.2 and AWS 2026.1.1, so an `EXECUTE`-only user cannot run `REFRESH_WEEK` | Probe 1.1(a) in `TEST_` (0 calls) | Switch the operator's refresh to an `EXECUTE`-only runner user (`make app-user`'s sibling, `make app-runner`), keep the allowlist, and log a decision. The fetch-log write and the console stay on the owner path, or move to that user if its grants suffice |
| Q2 | The app role can run `EXPLAIN VIRTUAL` through `CFB_API_LIVE.GAME_PREVIEW` with only schema `SELECT` | Probe 1.1(b); Phase 5 probe 1.1(b) | The preview's plan is explained on the operator path (a fixed statement template added to the allowlist). The fetch itself stays on the read path |
| Q3 | `EXPLAIN VIRTUAL` of a query with no virtual table returns no rows rather than an error | Probe 1.1(b) | The inspector maps that error to "no virtual table: 0 calls", backed by the serving-contract lint |
| Q4 | The app role reads `REFRESH_TIMELINE` during a running refresh without waiting for a commit | Probe 1.1(c); Phase 2's "other sessions see the progress" scenario | Poll with a 2 s statement timeout and show "waiting for the database" during that step; the action's own outcome still arrives at the end |
| Q5 | The current user's system privileges and roles are readable from `EXA_USER_SYS_PRIVS` and `EXA_USER_ROLE_PRIVS` (or equivalents) | Probe 1.1(d) | The start check falls back to the name (not `sys`, not the owner) plus a warning in the user guide |
| Q6 | Phase 5 adds `REQ_ROWS` to `GAME_PREVIEW` (hand-off P6-a) | Task 4.1 reads the deployed view | Count the result rows per slice: 1 for a preview, equal by construction while the view has no row filter |
| Q7 | A live preview click, including its `EXPLAIN VIRTUAL` and the guard, ends under 2 s on AWS | Task 8.2 (median of the live click plus the replayed and cached ones as context); task 6.3 locally in replay | Reuse the plan from the page's plan request when it is at most 60 s old for the same game and mode (the re-plan then happens only if the mode changed), and log it |
| Q8 | Playwright installs cleanly on this WSL host without `sudo` (chromium and its system libraries) | Task 1.2 gate | API-level E2E fallback in `tests/it/test_app_e2e_it.py`; Playwright is dropped from `package.json`; logged here |
| Q9 | The pinned set resolves together: Vite 8, plugin-react 6, vitest 5, TypeScript 6.0.3, typescript-eslint 8.72, eslint 10, jsdom 30, Testing Library 16 and jest-dom 7 | Task 1.2: `npm ci`, build, test, lint, typecheck | Step back within each major to the newest compatible release, and record the set |
| Q10 | Phase 2's refresh estimate (weekly slices missing or not final) equals what `REFRESH_WEEK` spends (4 on the demo plan) | Phase 2's tests; task 8.2 compares the estimate with `LOAD_RUN.CALLS` | The guard already records the larger of the measured calls and the `/info` delta; the label states "up to", and a difference is logged |
| Q11 | The ledger authorizes the month in which task 8.2 runs | The owner adds the month to `vs/live/ledger.yaml` (U2) | Group G waits; the replay verification (8.1) and the integration tests cover the mechanisms |
| Q12 | A refresh with 100k seasons finishes within the operator connection's 900 s socket timeout locally (target < 2 min) | Task 8.1 | Raise the timeout for the refresh connection only; the timeline is unaffected |
| Q13 | After a replay refresh of a completed week, `CURRENT_POINT` moves to that week (Phase 3: a week is done when all its games are completed) | `test_refresh_runs_chain_timeline_live` | The page states "refreshed; the current point stays at week N until all its games are completed", and the as-of follow mode waits |
| Q14 | Phase 8's AWS story is "the app on the laptop against the AWS database" (`TARGET=aws`, loopback), so the operator path stays usable there | Owner review (U3) with Phase 8's plan | An app hosted on AWS runs read-only until Phase 8 adds authentication for the operator path |

## Review Findings

<!-- Populated by speq-implement after code review. -->
