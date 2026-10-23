# Decision Log: add-cfb-app

Date: 2026-10-22 (planned); revised 2026-10-22/23 after the independent review (`review.md`); revised 2026-10-23 for the owner's new UI (§ UI revision interview, [34]–[42])

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

### Review resolution (orchestrator decisions, 2026-10-22)

The owner (Nico) was away and delegated the decisions on the independent review (`review.md`) to the orchestrator. They are recorded as the orchestrator passed them; the design entries [24]–[33] and the revised entries below implement them.

**Q (M1) No `Host` allowlist, and "own origin" is undefined:** accept?
**A:** Accept all.
- Allowlist `Host` on every request, GETs included.
- Use a fixed `Origin` set from configuration that is never derived from `Host`.
- Pin Vite to `127.0.0.1` with `strictPort` and `changeOrigin: false`.
- Resolve `localhost` binds and require every resolved address to be loopback.
- Add scenarios for a rebinding attempt (wrong `Host`) and a cross-origin POST.

→ [24], revised [3], [15]

**Q (M2) The read-user start check ignores the role's own privileges:** accept?
**A:** Accept.
- Make the start check a positive check of the read user's effective privileges. It must cover role grants, PUBLIC, direct object grants, owned objects, connection access and impersonation.
- Add a scenario where the role is granted INSERT and the start fails.
- Add a lint rule that `GAME_PREVIEW` appears only in `live.py`.
- Document that the read user can still spend calls from a SQL client.

→ [25], revised [16]

**Q (M3) A failed, timed-out or retried live statement spends calls without a ledger entry:** accept?
**A:** Accept.
- Write the ledger in a `finally`. On error, charge `MAX_ATTEMPTS` per planned slice.
- Never retry a live statement.
- Set live timeouts above the fetch UDF's worst case.
- Add a scenario: "failed request is charged".

→ [26], revised [5], [6], [13]

**Q (M4) `Ledger.exclusive()` does not block:** accept?
**A:** Accept.
- Take the ledger lock only for live plans.
- Add a single flight per (session, game), with the cache checked inside it.
- Map a held lock to 409 `live_busy`.
- Add scenarios for concurrent clicks and for a preview during a refresh.
- Verify how `Ledger.exclusive()` behaves today. It lives in `vs/host/ledger.py` in the Phase 2 worktree at `../exasol-cfb-demo-p2`. If it can't wait, design around that and don't change its semantics.

→ [27]

**Q (M5) A refresh thread that dies mid-run leaves its spend unledgered:** accept?
**A:** Accept.
- At startup, reconcile `LOAD_RUN` rows against the ledger and record any missing run.
- Learn the run id early through the owner session id.
- In dev mode, disable the live refresh while running with `--reload`.

→ [28], revised [11], [12]

**Q (M6) Budget facts are stale, and the app's ledger plans do not exist:** what now?
**A:** Resolved by the orchestrator.
- `vs/live/ledger.yaml` on the Phase 2 branch now has the plan caps `add-cfb-analyses: 6`, `add-cfb-app: 12` and `app: 60`, plus month caps 2026-10: 400 and 2026-11: 1000. The owner authorized 1000 for November.
- Use the plan names `add-cfb-app` for verification and `app` for runtime demo spend.
- Remove the "November not authorized" assumption.
- Guard previews and the console against `REFRESH_SPENDABLE − REFRESH_RESERVE`, using Phase 2's quota semantics. The refresh button may use the reserve, as Phase 2's refresh does.
- Warn at startup when a cap is missing.

→ [29], revised [7], [21]

**Q (M7) Follow mode moves the pages to an unpublished week in the middle of beat 3:** accept?
**A:** Accept.
- Follow a composite key: point, publication id, what-if run id and `WHATIF_VIEWS_CURRENT`.
- Hold reloads while a refresh runs.
- Widen probe 1.1(c) to cover page reads during `SIMULATE` and `PUBLISH`.
- Add an integration test.

→ [30], revised [17]

**Q (M8) Frontend and pure backend work wait for Phases 2–5 to merge:** accept?
**A:** Accept.
- Run the frontend group (5.x) right after 1.8, against fixtures.
- Split probe 1.1 so that (a), (d) and (e) run now and (b) and (c) run after Phases 2–5 merge.
- Define a demo-minimum cut line. Which pages and features must exist for the mid-November demo, and what is stretch?

→ [31]

**Q (minors m1–m18):** which?
**A:** Fix all 18. Specifically:
- have the console's stored-history query show 157 persisted years, for example with a stored-only `REQ_SOURCE = 'STORED'` query, which is exempt from the budget (foundation [21]);
- the quota badge must handle replay mode;
- the server derives the preview game id from the team pair;
- run console queries as a `CFB_READER` user where possible, not as sys;
- write a CSP-compatible theme script;
- try the Playwright podman image before falling back to API-level tests.

→ [32], [33], revised [8], [9], [10], [15], [20], [22], [23]; `review.md` § Review resolution lists every minor

**Q (versions):** still current?
**A:** Re-check them, and note that FastAPI has had 8 releases in about 29 hours. Keep the policy of using the newest release that is at least 7 days old, pinned exactly. → revised [14]

**Q (constraints for the revision):** what limits apply?
**A:**
- No DB sessions, no CFBD calls, and no edits outside the plan directory.
- `speq plan validate add-cfb-app` must pass with no warnings.
- Append a "Review resolution" section to `review.md`.
- Invoke `exasol:exasol-database` through the Skill tool first (the owner's standing rule); use the `dataviz` skill for the chart guidance.

### UI revision (owner request and orchestrator brief, 2026-10-23)

**Q (owner, after viewing `make app-preview` on sample data):** what should the UI be?
**A (Nico, verbatim):** "it isn't that 'pretty' of a UI … I would really like where the opening screen has a map of the US and then each team has their logo geographically where they are (with filter by conference probably). Then when you click the team, it opens a page dedicated to them about record, odds, predictions, etc. Then a different tab(s) has the universal CFB predictions, matchups of the week, and those kinds of cool stats/info."

**Q (owner, same day):** how heavy should the process be?
**A (Nico):** "Let's do a lighter process moving forward, this is a demo, not a full-blown product offering. We also don't need a crazy amount of tests really. Ideally I could test the web app today."

**Brief from the orchestrator** (decisions delegated to the planner, to log here):
- **Information architecture.**
  - Home: a US map of FBS teams with logos at their campus or stadium, a conference filter, probably a season selector and a search.
  - Team page `/team/:id`: record and schedule, playoff odds and their movement, this week's prediction, SP+ and rankings, games to watch and the what-if, and head-to-head entry points.
  - League tabs: predictions and matchups of the week, upsets, the odds board, and 3–5 cheap "cool stats" that show off Exasol.
  - The inspector, the refresh and the spend gates stay. Reuse what works.
- **Map tech:** offline at the demo, no CDN at runtime, bundled US geometry (for example `us-atlas` with `d3-geo` `geoAlbersUsa`), exact pinned versions, and a simple answer to logo overlap.
- **Logos (already decided):**
  - Download once from the `LOGOS` URLs into a cache outside the repository and outside OneDrive, with `make logos`. The app serves them; the browser never hotlinks ESPN.
  - Never commit them (owner action U8: private media only). Use a fallback badge.
  - Note that this is an external fetch, though not a CFBD call.
- **Visual design:** team colors as accents, a clean sports-dashboard look, both themes, and concrete guidance so implementers don't each invent a style.
- **API:** the endpoints the new pages need and the `CFB_API` views they read, with Phase 5 hand-offs. Build the frontend against fixtures generated from real data once the AWS backfill finishes.
- **Sequencing:** a thin slice first (the map plus the team page's record, schedule and SP+ on real data from `CFB_MODEL`, which needs Phases 2–3 only), then odds and predictions after Phase 4. Say what is blocked on which phase.
- **Output:** a compact revision of `plan.md` (a superseding section), minimal spec edits, `tasks.md`, this log, a validation run, no commit.

## Design Decisions

### [1] Domain `app/` with ten features; code in `app/backend` and `app/frontend`

- **Decision:** ten features under `app/`: `access`, `live-spend`, `refresh`, `inspector`, `as-of`, `odds`, `predictions`, `upsets`, `head-to-head` and `shell`. Python lives in `app/backend` and TypeScript in `app/frontend`, as the mission's project structure plans it. Two cross-cutting features (`access`, `live-spend`) hold the rules every page and action obeys, so the page specs stay about what a page shows.
- **Alternatives:** one feature per page only, with the security and spend rules repeated in each; a separate `api/` addition.
- **Rationale:** the `api/` domain is Phase 5's contract; `app/` is its consumer. The spend and access rules are what the owner will review and what tests must pin, so they get their own specs.
- **Promotes to ADR:** no

### [2] Principals: a read path for the pages, a console path (review m9), and an operator path for an allowlist, run by the namespace owner

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
- **Revised (review m9):** a third principal, the console path. The console user `CFB_CONSOLE_WEB`, with `CREATE SESSION` and `CFB_READER`, explains and runs the curated console queries ([10]). The owner keeps only the refresh, the fetch-log rows, the reconciliation of loader runs ([28]) and the privilege check's fallback ([25]).
- **Promotes to ADR:** yes

### [3] The operator path exists only on a loopback bind

- **Decision:** `APP_OPERATOR` is `auto` (the default: on if and only if the bind address is loopback), `on` (refuses to start on a non-loopback address) or `off`. With it off, the app is read-only: no refresh, no console, no live request. `GET /api/config` gives the reason. The default bind is `127.0.0.1:8000`.
- **Alternatives:** always on; a shared-secret header; authentication in Phase 6.
- **Rationale:** the owner can do anything, so the backend must hold that connection only where the presenter's machine is the only client. Authentication is a Phase 8 decision (the AWS-hosted app), recorded as a hand-off. Until then, a network-exposed app shows everything except the owner actions.
- **Revised (review M1):** `APP_HOST` is resolved, and the bind counts as loopback only when every resolved address is loopback; `localhost` is no longer trusted by its name. The bind rule is necessary but not sufficient: [24] adds the `Host` allowlist that stops DNS rebinding.
- **Promotes to ADR:** yes

### [4] Allowlist = refresh, fetch-log writes, reconciliation, the privilege check's fallback; forced re-sims and publication stay CLI

- **Decision:** the app starts only `REFRESH_WEEK`. Forced re-sims (`make simulate BASE= FIX=`), manual publications (`make publish`), `make predict` and `make sim-drop` stay owner CLI actions. Forced runs are not shown in this phase; `CFB_API.FORCED_RUN_ODDS` stays for the chat.
- **Alternatives:** buttons for `SIMULATE_FROM` and `PUBLISH_RUN`; showing forced runs read-only.
- **Rationale:** beats 1–4 need only the refresh. A forced run at 1M takes about 40 s and writes (Phase 5 [8]). The 1M scale dial is Phase 8 and will add one allowlist entry then. Showing forced runs would add a page section that serves no beat.
- **Revised (review m9, M5, M2):** the curated console left the owner's allowlist, because it runs as a reader ([10]). The reconciliation of loader runs ([28]) and the privilege check's fallback ([25]) joined it.
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
- **Revised (review M3):** the fetch-log rows are written after the ledger entries, and a failed insert never removes one. A failed statement leaves its planned live slices logged with NULL status and 0 rows, and its ledger entries carry the error ([26]).
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
- **Revised (review M3, M4, M5, M6):** step 4 checks the quota against `BACKFILL_SPENDABLE` ([29]); step 6 runs behind an in-process live gate and, for live plans only, the ledger lock ([27]); the statement runs once ([26]); the record happens in a `finally`, ledger first, with `MAX_ATTEMPTS` per planned slice on failure ([26]); unrecorded loader runs are reconciled first ([28]).
- **Promotes to ADR:** yes

### [7] Ledger entries per click without `/info` readings; the quota reading is cached for 5 minutes

- **Decision:**
  - A live preview or console request is charged its attempts, or `MAX_ATTEMPTS` when they are unknown, the recorder's rule when no readings exist. Its `remaining_before` and `remaining_after` stay null.
  - The guard uses the latest `CFB_API_LIVE.QUOTA` reading if it is at most 5 minutes old, else reads it again.
  - The app's spend counts under the ledger plan `app` (`APP_LEDGER_PLAN`). A prefixed namespace never writes the project ledger.
- **Alternatives:** `/info` before and after every click, as `make record --query` does; no ledger for the app.
- **Rationale:** each `/info` read is a virtual-table query (about 0.6 s on the database side), and two of them would push the preview past the 2 s target. The retry headroom in the check already bounds what a request can cost.
- **Revised (review M6, m6):** previews and the console check the quota reading against `BACKFILL_SPENDABLE` with the headroom ([29]). Every refresh plan reads `/info` fresh, since it is free and other clients spend in between (`sharedPool`); the 5-minute reading stays for previews and the console. The plan `app` exists, with a cap of 60.
- **Promotes to ADR:** no

### [8] The preview cache belongs to the browser session

- **Decision:** previews are cached per anonymous session cookie (`HttpOnly`, `SameSite=Strict`), keyed by game id, for 15 minutes, with at most 20 per session. A cached answer reads "cached <time> · 0 calls". "Fetch again" is explicit and labeled.
- **Alternatives:** a process-wide cache; no cache; storing previews (`PROMOTE()`).
- **Rationale:** this was Phase 5's hand-off. The viewer who chose to spend owns the result, and a demo segment fits in 15 minutes. A global cache would show one viewer's paid data to another without their act. `PROMOTE()` stays deferred.
- **Revised (review m3, m7, M4):** the cache key is the game the server picked from the pair ([32]), and two requests of one session for the same game share one fetch ([27]). Previews of played games never change and could be cached longer; not worth the complexity now.
- **Promotes to ADR:** no

### [9] The inspector records the sent text and explains it on demand as the same principal

- **Decision:**
  - Per browser session, the backend keeps the last 50 statements with pyexasol's `stmt.query`, which is the text the database received. It also keeps the principal, the milliseconds, the rows, the `REQ_*` lines per slice, and the calls.
  - On request, it runs `EXPLAIN VIRTUAL` of that text as the same principal: the read user for pages and the preview, the operator for the console.
  - A query over `CFB_API` alone has no pushdown and is shown as "no virtual table: 0 calls".
  - Refresh runs are shown from `CFB_API.FETCHES` and `REFRESH_TIMELINE`.
- **Finding (how the backend gets `EXPLAIN VIRTUAL` while the app role is read-only):** `EXPLAIN VIRTUAL` is a read statement. It asks the adapter for the pushdown and does not run the fetch UDF, so it costs 0 calls. A reader with `SELECT` on the virtual schema may run it (foundation probe 1.4g), and the key never appears in it (architecture). Through a serving view with only schema `SELECT`, Phase 5's probe 1.1(b) and this plan's probe 1.10(b) confirm it. No owner rights are needed, and the plan shown is exactly the one for the user who ran the query.
- **Alternatives:** explain every query eagerly (an adapter round trip per page query for nothing); rebuild the SQL on the client (it would not be the sent text); explain as the owner (another principal's plan).
- **Rationale:** evidence, not claims. The client references entries by id and never sends SQL.
- **Revised (review m10):** each slice key is also shown as its HTTP request (`GET https://api.collegefootballdata.com…`, never with the key), a "copy SQL" control copies the logged text, and console entries name the reader principal.
- **Promotes to ADR:** yes

### [10] Beat 2 in the app uses a curated four-query console, run as a reader of the virtual schema

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
- **Revised (review m8, m9):**
  - The console runs as the console user `CFB_CONSOLE_WEB` (`CREATE SESSION` and `CFB_READER`), not as the owner. The inspector's principal then reads "reader", which is beat 2's message: any reader with `SELECT` gets this. The fetch-log rows of its runs stay on the operator path, so the console still needs the operator path. In `TEST_` the console user is `CFB_TEST_READER`. This reverses the earlier rejection of "a third database user"; its password is set once in `.env` (U4).
  - The `stored` entry is a stored-only query over 1869–2025 (`REQ_SOURCE = 'STORED'`, counted per slice). The plan contains no fetch and no budget applies (`cfbd-fetch/read-through`), so it shows 157 persisted seasons at 0 calls. The earlier one-season query proved the mechanism but not the scale, and a plain range query would be refused, because stored slices count toward the budget (`cfbd-vs/fan-out`).
  - The help text also points at the pages' stored history, such as the head-to-head series since 1897 ("no virtual table: 0 calls").
- **Promotes to ADR:** yes

### [11] Refresh: plan, confirm, background run, follow any run by polling, map refusals to 409, no cancel

- **Decision:**
  - `POST /api/refresh/plan` and `POST /api/refresh/run` run on the operator path. The run executes in a background thread on its own owner connection (socket timeout 900 s) and returns an action id at once. One app-started refresh runs at a time per process.
  - The timeline polls `CFB_API.REFRESH_TIMELINE` on the read path: every 1 s while the newest refresh run is `RUNNING`, else every 5 s. It follows any run, whether started by the app, by `make refresh` or from a SQL client.
  - The lock and budget refusals are mapped to 409 with the database's message.
  - There is no cancel button.
- **Alternatives:** server-sent events; running the script inside the HTTP request; a cancel button.
- **Rationale:** Phase 2 commits each step's row at its start and end, so polling a serving view is correct and needs no channel. A 2-minute statement must not hold an HTTP request. Following runs started from a SQL client keeps beat 3's "one SQL call" true on stage. Cancelling is a `KILL STATEMENT`, an owner act, and Phase 2's stale rules clean up after it.
- **Revised (review M5, m11, m12):** the thread notes its owner session id, and a second connection learns the run id early. A live run holds the ledger lock for its whole run; a replay run does not ([27]). A run whose process died is recorded by reconciliation ([28]). The live refresh is disabled under `make app`. The plan states the week's completeness, and the action states the point before and after. The inspector polls the run's `FETCHES` with the timeline.
- **Promotes to ADR:** yes

### [12] Phase 2's refresh guard is extracted into `loader/guard.py`, and the CLI and the app share it

- **Decision:** the estimate, live check, quota, ledger verdict, room and record logic of `scripts/loader_cli.py` moves into `loader/guard.py`, with structured results and the ledger plan as a parameter. The CLI keeps its output and exit codes. The recorder's pure cost helpers move to `vs/host/livecalls.py`.
- **Alternatives:** import `scripts.loader_cli` internals from the app; re-implement the guard in the backend.
- **Rationale:** one implementation of what a refresh may spend and how it is recorded, already pinned by Phase 2's CLI tests. This is a cross-file refactor with behavioral dependencies, hence `[expert]`.
- **ADR scope:** every host caller that spends CFBD calls uses the shared guard.
- **Revised (review M4, M5, m5, the 1.9 note):** the guard takes the ledger, the plan, the lock policy (CLI: every run of the real namespace; app: live runs only), the retry headroom (CLI: 0; app: `MAX_ATTEMPTS − 1`) and a fresh-quota flag as parameters, and it never derives the ledger from the prefix (the CLI passes a disabled one for `--prefix`). `reconcile` is added. The CLI's behavior and tests stay unchanged.
- **Promotes to ADR:** yes

### [13] Backend runtime: one worker, a 4-session read pool, autocommit, a query timeout, one retry

- **Decision:**
  - uvicorn runs one worker, and the endpoints are synchronous (FastAPI runs them in its thread pool).
  - The read pool holds 4 pyexasol sessions as the read user, with autocommit and a 30 s query timeout (per probe 1.1(e)). A transaction-conflict error is retried once.
  - An unreachable database answers 503 with `TargetError`'s message and no driver text.
  - The in-process state (sessions, actions, the quota reading) lives in memory.
- **Alternatives:** several workers; a connection per request; autocommit off.
- **Rationale:** a pyexasol connection is one session, runs one statement at a time, and must not be shared between threads (threadsafety 1, pyexasol docs). The page contract requires autocommit (Phase 5). The state that keeps spending safe (single flight, the session cache) must live in one process.
- **Revised (review M3, m16):** the conflict retry applies only to 0-call catalog statements. Live statements run once, on a reserved live session (`QUERY_TIMEOUT` 300 s, socket 330 s) or a console session, never through the pool ([26]). Checkout waits at most 5 s, then answers 503 `busy`. A broken session is replaced on the next checkout.
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
- **Revised (review, versions):** re-checked 2026-10-22 21:46 UTC. FastAPI shipped 23 releases since 2026-10-19, 8 of them between 2026-10-21 13:56 and 2026-10-22 18:30 UTC; the newest at least 7 days old is 0.143.2. FastAPI bounds starlette only from below and also requires `opentelemetry-api`. The policy is kept and tightened: the newest release at least 7 days old on the day of task 1.2, pinned exactly on both sides (`==` in the `app` group, starlette included, plus `uv.lock`). Under the rule, vite, eslint and typescript-eslint step back one release today (8.3.4, 10.12.0, 8.71.2). Two dates in the first table were wrong (@types/react 2026-09-09; @testing-library/dom 2026-09-13).
- **Promotes to ADR:** no

### [15] Production: FastAPI serves `dist` with an SPA fallback and a strict CSP; development: Vite with a proxy

- **Decision:**
  - `make app-serve` runs one process that serves `/api/*` and `app/frontend/dist`. A catch-all GET returns `index.html` for the client routes. Hashed assets get long caching and `index.html` gets `no-cache`.
  - The headers are a CSP limited to the app's own origin (images from `data:` too, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
  - `make app` runs uvicorn with `--reload` plus Vite, which forwards `/api`. CORS is allowed only for the origins in `APP_CORS_ORIGINS` (the Vite origins in development; `*` is refused).
  - Every `POST` needs `X-CFB-Action: 1` and an allowed `Origin`.
- **Alternatives:** nginx in front; hash routing; CORS `*` in development.
- **Rationale:** "a single process" (interview item 5). Starlette's `StaticFiles(html=True)` serves `index.html` only for directories and `404.html` otherwise (source via context7), so it has no SPA fallback. The custom header with the `Origin` check stops cross-site requests from acting even when the cookie would be sent.
- **Revised (review M1, m1):** a `Host` allowlist runs before everything else ([24]). The `Origin` set is configured, never derived from `Host`, and a `POST` also needs `Content-Type: application/json`. Vite is pinned to `127.0.0.1:5173` with `strictPort`, its own host check on and `changeOrigin: false`, and the launcher refuses `--host`. The CSP holds without exceptions: no inline script (the theme is set by `public/theme-init.js`, a same-origin file loaded before the first paint) and no font from another host (the system font stack).
- **Promotes to ADR:** no

### [16] The read user's password comes from the environment or `.env`; `make app-user` creates the user; the start refuses a writable read user

- **Decision:**
  - `CFB_APP_USER` (default `CFB_APP_WEB`) and `CFB_APP_PASSWORD` come from the environment, then from `.env`, which git ignores.
  - `make app-user` runs as the owner. It creates the user with that password, plus `CREATE SESSION` and `CFB_APP`. It is idempotent; `ROTATE=yes` changes the password, and it refuses when the role or the password is missing.
  - At start, the backend refuses a read user that is `sys` or the owner, or that holds any other system privilege or role (per probe 1.1(d)).
  - No tool generates or writes a password.
- **Alternatives:** the tool generates a password and writes `.env`; the read path as `sys` in development; a documented manual `CREATE USER` only.
- **Rationale:** the precedent `CFB_TEST_OWNER_PASSWORD` (environment or `.env`, `scripts/grants.py`). Phase 5 left user creation to the owner, and this target is the owner's tool. The check makes "pages are read-only" a fact that cannot be misconfigured silently.
- **Revised (review M2):** the start check is now the positive rule of [25], for the read user and the console user, and `make app-user` creates both users.
- **Promotes to ADR:** yes

### [17] The as-of point lives in the URL; pages follow the current point unless one is pinned

- **Decision:**
  - The point is `?season=&week=` (the week sequence). An unknown point, or one after the current point, falls back to the current point with a notice.
  - Only points with `IS_AFTER_CURRENT` FALSE are offered, one season at a time.
  - Without an explicit choice, the app reloads every page when `CURRENT_POINT` changes. With one, it shows a notice instead.
  - The next meeting is always "as of the current point", and is labeled so.
- **Alternatives:** session-only state; always follow; offering every point.
- **Rationale:** links reproduce a view. Beat 4's "pages move" happens without a click. The page contract forbids later points.
- **Revised (review M7, m15):** follow mode keys on the follow key of [30] and holds reloads while a refresh runs. One shared validation serves every route that takes a point.
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
- **Revised (review m18, m17):** before falling back to API-level flows, Playwright is tried in the official image `mcr.microsoft.com/playwright:v1.64.1-noble` through podman (4.9.3 on this host) with `--network host`, and the path taken is recorded. Component tests render in `StrictMode`.
- **Promotes to ADR:** no

### [21] Live verification: at most 12 calls, 8 planned, in November (authorized)

- **Decision:** on AWS, from the laptop's loopback-bound app (`make app-serve TARGET=aws`): 1 preview, the `fan-out` console query (3 calls) and 1 refresh from the button (an estimate of 4). All go through the ledger under `add-cfb-app`, capped at 12. They run in a window agreed with the orchestrator, after `make dns-check TARGET=aws`, and only in a month the ledger authorizes (November 2026 is authorized for 1000 calls since 2026-10-22).
- **Alternatives:** 0 calls (the TLS-pinned live path and the real latency would stay unproven); a full live demo run.
- **Rationale:** each live mechanism (the read-path preview, the console, the operator refresh) is proven once. Everything else is proven in replay at 0 calls.
- **Revised (review M6, live-cap note):** November is authorized (1000 calls, owner, 2026-10-22), and the plan cap `add-cfb-app: 12` exists on the Phase 2 branch. Task 8.2 runs after Phase 2's live group J and Phase 5's task 7.3, in the same window protocol. The refresh runs live only if its plan shows an estimate of at most 4; otherwise it runs in replay and the reason is logged. With the retry headroom the ledger checks pass in sequence (4, 7 and 11 of 12).
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
- **Revised (review m1):** no web font: the system font stack, with tabular figures.
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
- **Revised (review m14):** the what-if point is compared as (season, week sequence), with the season taken from `WHATIF_RUN`, because `CURRENT_POINT` has no what-if season.
- **Promotes to ADR:** no

### [24] Every request must name an allowed host; every action must come from a configured origin (review M1)

- **Decision:**
  - A `Host` allowlist runs on every request, `GET` included, before routing. It holds `127.0.0.1:<port>`, `localhost:<port>` and `[::1]:<port>`; the Vite hosts `127.0.0.1:5173` and `localhost:5173` under `make app` only; and `APP_ALLOWED_HOSTS`, for a read-only network bind (`*` is refused). Name and port must both match; anything else gets 400.
  - The `Origin` set for `POST` is built once from configuration: the loopback list as `http://` origins, plus `APP_CORS_ORIGINS`. It is never derived from the request. A `POST` needs `X-CFB-Action: 1`, `Content-Type: application/json` and an `Origin` in the set; a missing `Origin` fails with 403.
  - Vite: `server.host: '127.0.0.1'`, `strictPort: true`, its own `allowedHosts` check left on, and the `/api` proxy with `changeOrigin: false`. `scripts/app.py dev` refuses a `--host` override, and a vitest test pins the configuration.
  - The bind address is resolved; it is loopback only when every address is ([3]).
- **Finding:** with DNS rebinding, a page on `evil.example` re-resolves its own name to `127.0.0.1`. The browser then sends same-origin requests with `Origin: http://evil.example:8000` and `Host: evil.example:8000`. A check of `Origin` against `Host` passes, the custom header needs no preflight, and `SameSite=Strict` does not help, because the request is same-site. Only the `Host` value tells the attacker apart. Starlette's `TrustedHostMiddleware` (source read 2026-10-22) lowercases the name, drops the port and answers 400, so the app uses its own check of name and port.
- **Alternatives:**
  - Starlette's middleware: ignores the port.
  - An origin derived from `Host`: passes after rebinding.
  - A per-start operator token served in `index.html`: readable after rebinding too. Kept out unless Phase 8 needs it for a non-loopback bind, where real authentication is the answer.
  - Loopback alone: a rebinding page and a Vite dev server opened with `--host` both reach the owner path.
- **Rationale:** the `Host` allowlist is the one check that rebinding cannot pass. The configured origin set closes cross-site `POST`s. The Vite pins keep the dev proxy from opening the owner path to the LAN or rewriting `Host`.
- **Promotes to ADR:** yes

### [25] The start check proves the users' effective privileges (review M2)

- **Decision:**
  - At start the backend reads, for the read user and for the console user:
    - the privileges granted to the user directly, through its roles (recursively), and to `PUBLIC`;
    - both connection grants (`GRANT CONNECTION` and `GRANT ACCESS ON CONNECTION`);
    - impersonation grants;
    - the objects and schemas it owns.
  - It refuses to start, naming the user, the privilege and its route, unless:
    - the system privileges are exactly `CREATE SESSION`;
    - the object privileges are exactly `SELECT` on `CFB_API` and `CFB_API_LIVE` (read user) or on `CFBD_VS` (console user);
    - the user owns nothing, holds no connection privilege and may impersonate no one;
    - the user is neither `sys` nor the owner.
  - Each user reads its own privileges where the views allow it; probe 1.1(d) names the views and what a user can see. Otherwise the operator path reads the database-wide views. With the operator path off, the start is refused unless `APP_SKIP_READ_CHECK=yes`, which the header then shows.
  - A lint (`test_game_preview_only_in_live`) fails when any backend file other than `app/backend/live.py` names `GAME_PREVIEW`.
  - The user guide and the architecture's security model say that "read-only" is not "zero-spend". The read user can run `GAME_PREVIEW`, and the console user any `CFBD_VS` query, from a SQL client with its password, outside the ledger. The ledger guards the app's spend; the database's `PROJECT_BUDGET` caps every client.
- **Alternatives:**
  - The earlier name check (not `sys`, not the owner, no other system privilege or role). It passes when the role itself gains `INSERT`, when `PUBLIC` gains a grant, or when the user owns an object or holds connection access.
  - No check.
- **Rationale:** "pages are read-only" must be a fact that no route of a privilege can silently break. A connection grant matters most, because it is how a key leaks.
- **Promotes to ADR:** yes

### [26] A live statement is charged in a `finally`, never retried, and outlives the fetch function (review M3)

- **Decision:**
  - `live.py` writes the ledger in a `finally`:
    - on success, one entry per `LIVE` slice from the `REQ_*` columns;
    - on an exception, one entry per planned live slice, with status and attempts null, cost `MAX_ATTEMPTS` (the recorder's rule for unknown attempts) and the error's first line.
  - The ledger is written before the fetch-log insert, so a failing owner connection cannot lose it. The fetch log then gets the planned live slices with NULL status and 0 rows (`build_fetch_log_rows` without staged rows). `FETCH_LOG` has no error column, so the ledger carries the error.
  - No statement whose plan has live calls is retried by the app. Live statements run on a reserved read-user session (the live session) or a console session, never through the pool's retry wrapper, and the pool refuses a statement marked live.
  - The live sessions set `QUERY_TIMEOUT` to 300 s (per probe 1.1(e)) and a socket timeout of 330 s. The fetch function's worst case per slice is 4 attempts of 30 s, plus 3 waits of at most 30 s (the `Retry-After` limit), plus 10 s of DoH: 220 s. Slices of one statement run in parallel. A unit test computes the worst case from `vs/cfbd_vs/http.py` and the resolver and asserts the margin. The socket timeout lies above the query timeout, so the database ends a stuck statement and the driver sees an error.
  - A failed preview answers 502 `cfbd_failed`, naming the error and the calls charged.
- **Alternatives:**
  - Charge only on success: a 404, a 5xx after retries, a 429 storm, an error after the HTTP call, or a timeout would leave spent calls unrecorded.
  - Keep the pool's retry for previews: a transaction conflict after the fetch would fetch again.
  - A consumer group with `QUERY_TIMEOUT` for the read user (grammar confirmed: `CREATE CONSUMER GROUP … QUERY_TIMEOUT`, `ALTER USER … SET CONSUMER_GROUP`): it applies to the whole user, live statements included, and it is a new database object. A per-session `ALTER SESSION` does the same for one session.
- **Rationale:** over-counting is the safe direction. A second automatic attempt is a spend the user never confirmed. Phase 2 learned that pyexasol's 30 s default once cut off a paid response.
- **Promotes to ADR:** yes

### [27] The ledger lock is taken only for live plans, behind an in-process gate; duplicates share one flight (review M4)

- **Decision:**
  - **Verified** in the Phase 2 worktree's `vs/host/ledger.py`:
    - `Ledger.exclusive()` opens `<ledger>.lock` on every call and takes `fcntl.flock(LOCK_EX | LOCK_NB)`;
    - it raises `LedgerError("another run holds the ledger lock …; wait for it to finish")` at once;
    - `flock` locks belong to the open file description, so two threads of one process conflict too;
    - a disabled ledger yields without locking, and `LedgerRefused` subclasses `LedgerError`;
    - the CLI takes the lock for the whole of every run of the real namespace, replay included.

    The app designs around this and changes neither.
  - Plans with 0 live calls (replay, stored, contradiction, cached) never take the lock and never wait.
  - Live plans pass an in-process live gate first: a `threading.Lock` with a wait of at most 5 s, so the app's own live requests queue instead of failing each other on `flock`. Inside the gate come:
    - a busy check: the app's own refresh action, or the newest refresh run `RUNNING` in `REFRESH_TIMELINE`, unless the operator path finds its session gone;
    - one non-blocking `Ledger.exclusive()`. A held lock answers 409 `live_busy`, naming the holder: the app's refresh action, the running refresh run, or "another process (for example `make record` or `make backfill`)".
  - The preview has a single flight per (session, game), with the cache checked inside it, so a concurrent duplicate waits and is served "cached · 0 calls". A second console run of a session while one runs answers `live_busy`. The frontend disables the button while it fetches, and the API client never retries a `POST`.
  - The preview plan reports the busy state, so the cost label reads "live previews resume when the refresh ends" while a refresh runs.
  - The app's live refresh holds the lock for its whole run, as the CLI does; its replay refresh takes no lock.
- **Alternatives:**
  - Lock every act, as the CLI does: every replay click fails during a refresh.
  - Wait on the lock: changes Phase 2's semantics, and a 2-minute refresh would hold HTTP threads.
  - A process-wide preview cache: shares paid data across viewers ([8]).
- **Rationale:** spends stay serialized across processes, replay works during beat 3, and a double click costs one call.
- **Promotes to ADR:** yes

### [28] Loader runs are reconciled with the ledger; the run id is learned early; the dev server runs no live refresh (review M5)

- **Decision:**
  - `loader/guard.reconcile` runs on the operator path, under the ledger lock, at start, before every live refresh, and when the timeline sees a refresh run leave `RUNNING`. It records every `REFRESH_WEEK` or `BACKFILL` run of the current UTC month that:
    - is no longer running: it ended, or it is `RUNNING` with its session gone, by Phase 2's stale rule;
    - was live: `REMAINING_BEFORE` is set;
    - has no ledger entry `<RUN_KIND>#<RUN_ID>`.

    The cost is `LOAD_RUN.CALLS`, else the committed steps' calls plus `MAX_ATTEMPTS` per slice of a fetch step left unfinished. The plan is the app's ledger plan. The error reads "recorded by reconciliation: <reason>".
  - While a known run cannot be recorded (the lock is held, `LOAD_RUN` is unreadable, the ledger cannot be written), live actions answer 409 `reconcile_pending`.
  - The refresh thread notes its owner connection's session id before `EXECUTE SCRIPT`. A second operator connection finds `LOAD_RUN WHERE SESSION_ID = <id> AND RUN_KIND = 'REFRESH_WEEK'`, so the action names its run while it runs, and the record has a run id even if the script raised before returning one.
  - `make app` (uvicorn `--reload`) disables the live refresh: the plan says "live refresh needs `make app-serve`", and a live run is refused. Replay refreshes run.
- **Finding:** the review assumed that a killed process's script runs on. Phase 2's `refresh_week.lua` says that after a kill or a client abort nothing more can be logged and the next run frees the `RUNNING` row, and `loader/runlock.py` says a process that dies frees the lock with its session. Either way the committed step rows survive, and reconciliation records the run. The process-death scenario records which case happens (Q16).
- **Alternatives:**
  - Trust the thread: a dead process cannot record.
  - A claims file written before each run: a second ledger-like format to keep consistent.
  - Exclude `app/` and `loader/` from `--reload` while a refresh runs: depends on watch flags, and edits to other files still restart the worker.
  - Reconcile only runs the app started: a refresh from a SQL client (beat 3's "one SQL call") would stay unledgered.
- **Rationale:** the database's run log outlives any process, and recording under the lock means no run is charged twice. Charging a run the app did not start to the app's plan errs on the safe side: the month total is exact, and the plan total over-counts.
- **Promotes to ADR:** yes

### [29] Ledger plans `app` and `add-cfb-app`; on-demand spend leaves the refresh reserve alone; the ledger state is shown at start (review M6, m4, m5, m6)

- **Decision:**
  - The app's runtime spend (rehearsals and the demo) counts under `app` (cap 60, all months). Task 8.2 runs under `add-cfb-app` (cap 12). The months are capped at 400 (2026-10) and 1000 (2026-11, authorized by the owner). The caps are on the Phase 2 branch; this plan does not edit `vs/live/ledger.yaml`.
  - Previews and console runs are checked against `BACKFILL_SPENDABLE`, which is `GREATEST(0, REFRESH_SPENDABLE − REFRESH_RESERVE)`, with the headroom `MAX_ATTEMPTS − 1`. On-demand spend therefore never eats the weekly refresh's reserve. The refresh keeps Phase 2's rule: its `CHECK` compares with `REFRESH_SPENDABLE` and may use the reserve.
  - **Refresh retries (m5).** The refresh's ledger check adds the retry headroom of one request (`MAX_ATTEMPTS − 1`), and its confirmation reads "up to N live calls, plus retries". `REFRESH_WEEK` takes no limit, so the label states the truth. A headroom of `(MAX_ATTEMPTS − 1) × estimate` would make a 4-call refresh need 16 calls of room and would fail task 8.2 under its cap of 12. Phase 2's per-table `REFRESH_SPENDABLE` check stays the hard limit in the database.
  - Every refresh plan reads `/info` fresh (m6); previews and the console keep the 5-minute reading.
  - **Visible state (m4).** `GET /api/config` reports the ledger plan, whether the ledger is on, the caps and the rooms, and whether live actions are possible. A missing plan or month cap, or `LEDGER=off`, shows in the header from the start ("live actions disabled: no ledger cap for plan app", "ledger off"), and every live control shows the reason instead of a cost.
- **Alternatives:**
  - The raw `REMAINING_CALLS`: previews could eat the refresh reserve on stage.
  - One plan for runtime and verification: rehearsals would eat the proof's cap.
  - A refusal on the first click: the demo would find out on stage.
- **Rationale:** Phase 2's quota semantics, applied the way a backfill applies them, with the caps the orchestrator set.
- **Promotes to ADR:** yes

### [30] Follow mode keys on the publication and waits for the refresh to end (review M7, m14)

- **Decision:**
  - The follow key is `(AS_OF_SEASON, AS_OF_WEEK_SEQ, PUBLICATION_ID, WHATIF_RUN_ID, WHATIF_VIEWS_CURRENT)` of `CURRENT_POINT`. Every `GET /api/timeline` returns it with `hold` (the newest refresh run is `RUNNING`) and `stuck`.
  - While `hold` is set, follow mode does not reload, and the header says "refresh running: pages update when it publishes". When the run ends, the pages reload once if the key changed. App-started and externally started runs behave alike. A run with no step change for 10 minutes is `stuck` and no longer holds.
  - Probe 1.10(c) reads odds, what-if and the guide as the app user during `SIMULATE` and `PUBLISH`. Its result fixes the pool's retry and timeout policy for 0-call statements.
  - The integration scenario "The pages follow a refresh only after it has published" runs a refresh elsewhere and polls during `WAIT`; the component scenario covers the UI half.
  - The what-if point is compared as (season of `WHATIF_RUN`, `WHATIF_AS_OF_WEEK_SEQ`), since `CURRENT_POINT` has no what-if season (m14).
- **Finding:** `CURRENT_AS_OF` (Phase 3) moves when the fetch steps merge the games, about a minute before `PUBLISH`. Reloading on a `CURRENT_POINT` change would show "no published run" in the middle of beat 3, and a run from a SQL client would never trigger the reload at publish.
- **Alternatives:** reload on any `CURRENT_POINT` change; reload only after app-started runs.
- **Rationale:** the step from beat 3 to beat 4 is the moment the audience watches; the pages must move once, to a published week.
- **Promotes to ADR:** no

### [31] Frontend first, a split probe and a demo-minimum cut line (review M8)

- **Decision:**
  - Groups A, A-FE1, A-FE2 and A3 need no other phase: the toolchain, the pure backend modules, the frontend foundations, the six frontend tasks against validated fixtures, and the database layer tested with fakes. Group P2 (the guard extraction and `live.py`) starts when Phase 2 merges. Group B onward waits for Phases 2–5.
  - Probe 1.1 is split. Task 1.1 (parts a, d and e) runs now in a `PROBE_` namespace with a toy Lua script, users and grants, at 0 calls. Task 1.10 (parts b and c) runs at the head of group B.
  - The plan defines a demo-minimum cut line (`plan.md`, Parallelization). Security and spending are never cut. The stretch items, in cutting order:
    1. the local perf run;
    2. the projector pass;
    3. the chat-slot test;
    4. the predictions extras;
    5. the upsets lists;
    6. the URL-addressable point and the earlier-week picker;
    7. the over-budget console entry and "copy SQL";
    8. the conditional guide.
- **Alternatives:** keep the A → B → … chain behind all merges, which leaves no frontend line written before Phases 2–5 merge, about 3.5 weeks before the demo.
- **Rationale:** the component tests need only fixtures that the backend's models validate, and the probe's decisive facts (invoker rights, privilege views, the timeout) need no other phase.
- **Promotes to ADR:** no

### [32] The server picks the preview's game from the pair (review m3, m7)

- **Decision:** `GET /api/preview/plan` and `POST /api/preview` take `team` and `opponent`. The server reads the pair's next meeting at the current point, else its latest meeting, and writes that game id as the literal. The session cache and the single flight key on that game id.
- **Alternatives:**
  - The client sends a game id checked to be an integer: any game could be previewed, one call each.
  - A longer cache life for played games (m7): their previews never change, but it is not worth the complexity now.
- **Rationale:** it narrows the spend surface to one game per pair, makes the cache key meaningful, and matches Phase 5's UX contract exactly.
- **Promotes to ADR:** no

### [33] Minor hardening from the review (m1, m13, m15, m16, m17)

- **Decision:**
  - **CSP (m1):** `index.html` has no inline script; `public/theme-init.js` sets the theme before the first paint; no web fonts.
  - **Replay (m13):** in replay the quota badge reads "replay · no calls" and never queries `QUOTA`. The fetch mode comes from the manifest of a 0-call `EXPLAIN` of the preview statement. AWS has no BucketFS replay host directory, so the AWS demo is live-only (Phase 8 note).
  - **Point validation (m15):** one FastAPI dependency for every route that takes a point; the after-current case is also tested on the predictions route.
  - **Pool (m16):** checkout waits at most 5 s, then 503 `busy`; broken sessions are replaced; no pool session is held across a live wait.
  - **Client (m17):** one `AbortController` per page load, aborted on a point or page change; `StrictMode`-safe polling hooks; component tests render in `StrictMode`.
- **Alternatives:** TanStack Query for cancellation (one more dependency for one feature); an inline theme script with a CSP hash (a hash to maintain).
- **Rationale:** each closes a failure the review found: an error banner in replay, unbounded waits, stale responses, double polls, a route without validation.
- **Promotes to ADR:** no

### [34] Information architecture: a map home, a page per team, league tabs; the built pages are reused as tabs

- **Decision:**
  - **Routes:** `/` is the map (`app/map`); `/team/<id>` is the team page (`app/team`); `/league/week|odds|predictions|upsets|stats` are the league tabs (`app/league`); `/head-to-head` is unchanged.
  - **Reuse:** the built Odds, Predictions and Upsets pages (5.2–5.4) become tabs unchanged, apart from team marks in their rows. The team page reuses `RootingGuide` and `WhatIfPanel` with the team as the focus team.
  - **Old paths** redirect with `replaceState`.
  - **Store:** `PageId` keeps its ids and adds `home`, `team`, `week` and `stats`. `focusTeam` is the team page's team. A new `mapConference` field is added.
  - **Season selector:** the existing as-of picker. **Search:** a header combobox over `/api/teams`.
  - **Cool stats:** four cards, each with its SQL one click away:
    - road warriors (kilometres via `GREAT_CIRCLE_KM`, the geo story);
    - SP+ movers against last season;
    - conference strength (mean SP+, non-conference record);
    - hottest active win streaks (window functions over all 157 stored seasons, the "history at 0 calls" story).
- **Alternatives:**
  - A separate team route state (`teamId`) next to `focusTeam`.
  - Rewriting the built pages as team-scoped panels.
  - A router library.
  - Stats on home-field advantage by elevation, attendance or the excitement index, which have patchy data in older seasons.
  - Point-differential "luck", which needs explanation on stage.
- **Rationale:**
  - **Reuse:** the built pages have 582 passing tests; making them tabs costs a prop.
  - **One focus team:** sharing `focusTeam` keeps Phase 7's app-state tools working without a signature change, and "focus Brookvale" means the same thing everywhere.
  - **Routing:** nine paths and one parameter still do not justify a router ([14]).
  - **The stats:** each is one readable statement, cheap at 0 calls, and shows a different Exasol strength.
- **Promotes to ADR:** no

### [35] Map: `d3-geo` + `topojson-client` + `us-atlas` pre-projected, bundled; no tiles, no zoom or force library

- **Decision:**
  - **Packages:** `d3-geo` 3.1.1, `topojson-client` 3.1.0 and `us-atlas` 3.0.1 at runtime; `@types/d3-geo` 3.1.1 and `@types/topojson-client` 3.1.5 for development. All exact pins, all ISC or MIT, all older than the 7-day rule (npm registry, 2026-10-23).
  - **Geometry:** `us-atlas`'s `states-albers-10m.json` is already projected with `geoAlbersUsa().scale(1300).translate([487.5, 305])` for 975 × 610 (README). The states render with `geoPath()` and no projection, and team points are projected with the same `geoAlbersUsa`, which places Hawaii and Alaska in their insets.
  - **Offline:** the JSON is bundled by Vite into `dist/`.
  - **Zoom** is a `viewBox` change.
  - **This amends [14]:** runtime dependencies are now React, React DOM and these three.
- **Alternatives:**
  - Leaflet or MapLibre with raster or vector tiles: these need a tile server or CDN at runtime, and offline tiles are a large asset.
  - A hand-drawn SVG of the states.
  - Path strings generated at build time and committed (80 kB of generated code in git).
  - `d3-zoom` for pan and zoom, `d3-force` for collision.
  - A choropleth by conference instead of logos.
- **Rationale:**
  - The mission requires the demo to run offline with the same look (`specs/mission.md`).
  - Pre-projected TopoJSON plus one projection function is the smallest correct way: about 35 kB of minified code and 30 kB of gzipped geometry.
  - The atlas is public-domain Census geometry under ISC.
  - A `viewBox` zoom and a 60-line relaxation are easier to test than two more libraries.
- **Promotes to ADR:** yes. Maps are drawn from bundled, pre-projected geometry; tile maps and runtime CDNs are rejected because of the offline demo.

### [36] Logo overlap: deterministic relaxation with leader lines, plus conference zoom; no clustering

- **Decision:**
  - **Markers** have a constant size on screen: 36 px nationally and 44 px zoomed.
  - **Relaxation:** a pure layout function runs at most 80 passes, pushing overlapping pairs apart with a 10 % pull back toward each anchor. A marker moved by more than half its size gets a leader line to a dot at its true location.
  - **Zoom:** a conference chip fits the view box to its teams, with a minimum box. "+", "−" and "Reset" buttons zoom; there is no wheel or drag pan.
  - **List view:** the table view and keyboard path.
- **Alternatives:**
  - Clustering at low zoom (numbered bubbles).
  - Smaller logos.
  - Free wheel and drag pan and zoom.
  - `d3-force`.
  - Placing teams on a hex grid by state.
- **Rationale:**
  - Clusters hide the logos, which are the whole point of the home page.
  - Leader lines keep the geography honest while making every logo clickable, and conference zoom is how a fan uses the map anyway.
  - Determinism (sorting by team id, a fixed number of passes) makes the layout testable and stable between renders.
  - Without free pan, the app avoids gesture handling and keyboard traps.
- **Promotes to ADR:** no

### [37] Logos: a private cache filled once by `make logos`, served by the app, with a badge fallback; never committed, never hotlinked

- **Decision:** per `plan.md` § Logos:
  - **Source:** the owner reads `CFB_CORE.TEAMS` (stored data, 0 CFBD calls).
  - **Fetching:** https only, hosts under `espncdn.com` only, PNGs of at most 1 MB with the signature checked, into `~/.cfb-demo/logos/<id>.png` and `<id>-dark.png`.
  - **Serving:** `/api/logos/{id}.png`, with an integer id, no database and a one-day cache. `has_logo` comes with every team.
  - **Fallback:** a color badge with the abbreviation.
  - **Switch:** `APP_LOGOS=off` for recordings.
  - **CSP:** unchanged, so a hotlink cannot load.
- **Alternatives:**
  - Hotlinking the ESPN URLs: online only, a third-party request at the demo, and a CSP hole.
  - Committing the logos: team marks in a public repo, against U8.
  - Storing them in BucketFS or the database.
  - Badges only.
  - SVG logos from another source.
- **Rationale:**
  - The logos make the page; the cache makes them offline and keeps the marks out of the repo.
  - The download is an external fetch (not CFBD, no quota), so it runs by hand, once, with tight rules, and is documented.
  - Reading the URLs from `CFB_CORE` as the owner keeps logo URLs out of the serving layer, where no page needs them.
- **Promotes to ADR:** yes. Third-party media (team marks) live only in a private cache outside the repository, are fetched by an explicit owner command, and are served by the app itself; never hotlinked or committed.

### [38] A small design system: tokens, cards, team colors as accents only

- **Decision:** `plan.md` § Visual design:
  - spacing, type, radius and shadow tokens in `tokens.ts`, plus `--surface-raised` and `--border-subtle`, with contrast tests;
  - a 12-column grid at most 1320 px wide, designed for a 1280 × 720 projector first;
  - one `Card` anatomy with an "SQL" button;
  - `teamColor.ts`, which picks the team color, else the alternate, else the app accent, by a 3:1 test against the surface. Team colors are never used for text or behind text, except the badge with computed ink;
  - shared components in `src/ui/`.
- **Alternatives:**
  - A UI kit (MUI, Chakra) or Tailwind.
  - Team colors as page themes (a full-bleed hero in the team color).
  - Letting each page task style itself.
- **Rationale:**
  - **Team colors:** many are dark navy or near-black, which fail on a dark surface, and a few are pale yellow, which fail on light. Accents with a contrast fallback look good in both themes without a per-team review.
  - **No UI kit:** it would bring a dependency set and a look to fight.
  - **Fixed tokens** keep four parallel implementers consistent.
- **Promotes to ADR:** no

### [39] API: thin-slice endpoints separate from the outlook; every panel names its query

- **Decision:**
  - **Endpoints:** `/api/teams/map`, `/api/team/{id}`, `/api/team/{id}/outlook`, `/api/league/week`, `/api/league/stats` and `/api/logos/{id}.png`, all on the read path over `CFB_API` (table in `plan.md`).
  - **The split:** the team's record, schedule and ratings (Phases 2–3) are separate from its outlook (Phases 4–5), and each outlook panel says `{available, reason}`.
  - **`queries`:** each response maps its panels to query-log entry ids for the "SQL" buttons.
  - **Source labels:** this week uses CFBD's stored pregame win probability and the line until our predictions exist.
- **Alternatives:**
  - One team endpoint that fails or blocks until Phase 4.
  - Reading `CFB_MODEL` directly from the app as an interim (it breaks the serving-layer rule and its lint).
  - Matching panels to the log by page name only.
- **Rationale:**
  - The thin slice must ship before Phase 4 exists.
  - The panel-to-entry map is what makes "the SQL behind each panel", the Exasol story, one click.
  - The stored pregame probability is real data that is already loaded, labeled by its source.
- **Promotes to ADR:** no

### [40] A real-data snapshot for the preview, kept outside the repository; synthetic fixtures stay the test contract

- **Decision:**
  - `make app-fixtures` reads the stored model and team list as the owner and writes response-shaped JSON to `~/.cfb-demo/fixtures/<target>-<season>/`. Every file is validated by the pydantic models, and no statement may name `CFBD_VS`.
  - `make app-preview REAL=1` serves it ahead of the synthetic fixtures.
  - Component tests keep the committed synthetic fixtures.
- **Alternatives:**
  - Committing real-data fixtures.
  - Waiting for the backend (S2).
  - Running tests against real data.
- **Rationale:**
  - **Outside the repository:** CFBD terms forbid raw data in git; derived data is fine, but the fixtures sit close to raw rows.
  - **Today:** the owner wants to click real data today, and the export needs only the backfill and Phase 3's views.
  - **Tests:** they must not depend on what was loaded on a given day.
  - **Cost:** the export's SQL is a throwaway twin of 9.7's routes, which is acceptable for one day of lead time.
- **Promotes to ADR:** no

### [41] Sequencing: slices S0–S4, and Phase 5's thin views first

- **Decision:**
  - **S0 (today):** all new UI on synthetic fixtures.
  - **S1 (today or tomorrow):** a real-data snapshot and real logos. It needs the backfill and Phase 3's views on the target, which is deploy only.
  - **S2:** `make app-serve` over `CFB_API` for the map, the team record and the league stats. It needs Phase 2 merged and hand-offs P6-UI-b, c and d, and 2.2's gate moves up to this.
  - **S3:** the outlook and the league tabs that depend on runs, after Phases 4–5.
  - **S4:** refresh, console and preview, unchanged.
  - **Hand-offs:** Phase 3 adds team colors to `TEAM` (P6-UI-a). Phase 5 extends `TEAMS` (P6-UI-b), adds `TEAM_TRAVEL` (P6-UI-c) and deploys the thin views first (P6-UI-d). Phase 4 needs nothing. Phase 7's P7-a gets the new page ids. Phase 8 opens the demo on the map.
- **Alternatives:**
  - Keep every route behind the full Phase 2–5 merge.
  - Compute travel in the app's SQL (that needs `EXECUTE` on a `CFB_MODEL` function for `CFB_APP`, and venues outside `CFB_API`).
  - Put coordinates in `CFB_MODEL.TEAM` instead of joining `VENUE`.
- **Rationale:**
  - "Ideally I could test the web app today" (owner).
  - A view keeps the function call under the owner's rights and the app inside `CFB_API`.
  - The venue join is the model's normal form, and the team list's location already maps to `HOME_VENUE_ID`.
- **Promotes to ADR:** no

### [42] The light process for the rest of Phase 6

- **Decision:**
  - Each UI task writes a few component tests (one per new scenario) and unit tests for its pure parts; only 9.7 and 9.8 add one integration file.
  - Task 6.2's Playwright smoke becomes a manual walk in V.3, and Playwright is optional.
  - V.2, the coverage audit, is dropped.
  - Code review (R.1) only for risky code: spend, locks, the SQL guard and the logo fetcher.
  - The security and spending scenarios are untouched.
- **Alternatives:**
  - Keep the full process.
  - Drop tests for the new pages entirely.
- **Rationale:**
  - The owner: "this is a demo, not a full-blown product offering. We also don't need a crazy amount of tests really."
  - The guard rails that protect quota and the database stay fully tested; the UI gets enough tests to keep refactors safe.
- **Promotes to ADR:** no

## Open Questions (assumption + verify step)

| # | Assumption | Verify step | If wrong |
|---|---|---|---|
| Q1 | Lua scripting programs run with the caller's rights on Personal 2026.2 and AWS 2026.1.1, so an `EXECUTE`-only user cannot run `REFRESH_WEEK` | Probe 1.1(a) with a toy read and write script in a `PROBE_` namespace (0 calls) | Switch the operator's refresh to an `EXECUTE`-only runner user (`make app-user`'s sibling, `make app-runner`), keep the allowlist, and log a decision. The fetch-log write stays on the owner path, or moves to that user if its grants suffice |
| Q2 | The app role can run `EXPLAIN VIRTUAL` through `CFB_API_LIVE.GAME_PREVIEW` with only schema `SELECT` | Probe 1.10(b); Phase 5 probe 1.1(b) | The preview's plan is explained on the operator path (a fixed statement template added to the allowlist). The fetch itself stays on the read path |
| Q3 | `EXPLAIN VIRTUAL` of a query with no virtual table returns no rows rather than an error | Probe 1.10(b) | The inspector maps that error to "no virtual table: 0 calls", backed by the serving-contract lint |
| Q4 | The app role reads `REFRESH_TIMELINE`, the odds, the what-if and the guide during a running refresh, including `SIMULATE` and `PUBLISH`, without waiting for a commit or failing | Probe 1.10(c); Phase 2's "other sessions see the progress" scenario | Poll the timeline with a 2 s statement timeout and show "waiting for the database" during that step; a page read that conflicts during `PUBLISH` shows "the database is publishing; retry", and follow mode's hold ([30]) already keeps follow-mode pages from reading then |
| Q5 | A user can read its own effective privileges, including those of its roles and of `PUBLIC`, its connection and impersonation grants and its owned objects, from `EXA_USER_*` and `EXA_ROLE_*` views | Probe 1.1(d) | The check runs on the operator path against the database-wide views; with the operator path off, the start is refused unless `APP_SKIP_READ_CHECK=yes` (header warning) ([25]) |
| Q6 | Phase 5 adds `REQ_ROWS` to `GAME_PREVIEW` (hand-off P6-a) | Task 4.1 reads the deployed view | Count the result rows per slice: 1 for a preview, equal by construction while the view has no row filter |
| Q7 | A live preview click, including its `EXPLAIN VIRTUAL`, the busy check, the guard and the ledger, ends under 2 s on AWS | Task 8.2 (median of the live click plus the replayed and cached ones as context); task 6.3 or 8.1 locally in replay | Reuse the plan from the page's plan request when it is at most 60 s old for the same game and mode (the re-plan then happens only if the mode changed), and log it |
| Q8 | Playwright installs cleanly on this WSL host without `sudo` (chromium and its system libraries) | Task 1.2 gate (1) | The podman image (Q19); then the API-level E2E fallback in `tests/it/test_app_e2e_it.py`, with Playwright dropped from `package.json`; logged here |
| Q9 | The pinned set resolves together: Vite 8, plugin-react 6, vitest 5, TypeScript 6.0.3, typescript-eslint 8.71/8.72, eslint 10, jsdom 30, Testing Library 16, jest-dom 7 and `@types/node` 24 | Task 1.2: `npm ci`, build, test, lint, typecheck | Step back within each major to the newest compatible release, and record the set |
| Q10 | Phase 2's refresh estimate (weekly slices missing or not final) equals what `REFRESH_WEEK` spends (4 on the demo plan) | Phase 2's tests; task 8.2 compares the estimate with `LOAD_RUN.CALLS` | The guard already records the larger of the measured calls and the `/info` delta; the label states "up to … plus retries"; task 8.2 refreshes live only if the plan shows at most 4, and a difference is logged |
| Q11 | (Resolved 2026-10-22.) The ledger authorizes the month in which task 8.2 runs: November is authorized for 1000, and the plan caps `add-cfb-app: 12` and `app: 60` exist on the Phase 2 branch | — | The app reports a missing cap at start ([29]); group G waits for the owner |
| Q12 | A refresh with 100k seasons finishes within the operator connection's 900 s socket timeout locally (target < 2 min) | Task 8.1 | Raise the timeout for the refresh connection only; the timeline is unaffected |
| Q13 | After a replay refresh of a completed week, `CURRENT_POINT` moves to that week (Phase 3: a week is done when all its games are completed) | `test_refresh_runs_chain_timeline_live` | The action states "the point stayed at week N" with the reason, and follow mode waits |
| Q14 | Phase 8's AWS story is "the app on the laptop against the AWS database" (`TARGET=aws`, loopback), so the operator path stays usable there | Owner review (U3) with Phase 8's plan | An app hosted on AWS runs read-only, with its address in `APP_ALLOWED_HOSTS`, until Phase 8 adds authentication for the operator path |
| Q15 | `hold_deploy_lock` (Phase 4 harness) holds the `LOAD_RUN` lock that `REFRESH_WEEK`'s `CHECK` refuses on (Phase 2's H1 lock kinds) | Task 4.2 | Start a run with `TEST_STEP_SLEEP` in another session to hold the lock instead |
| Q16 | When the app process is killed, Exasol aborts its running `REFRESH_WEEK` as a client abort rather than letting it finish | `test_dead_process_refresh_reconciled` (task 4.2) records which | Either way reconciliation records the run: with `LOAD_RUN.CALLS` if it finished, else with the committed steps' calls once the session is gone |
| Q17 | `REMAINING_BEFORE` is set exactly for live runs with due slices, and a fetch step's start row carries its `SLICES` | Task 1.9, against `refresh_week.lua` and `backfill.lua` | Treat a run as live when a step has calls above 0 or a `FETCH` step was left `RUNNING` in live mode, and take an unfinished step's slices from the estimate query |
| Q18 | `ALTER SESSION SET QUERY_TIMEOUT` exists and applies per session, for 30 s (pool) and 300 s (live sessions) | Probe 1.1(e) | Socket timeouts only (pool 35 s, live 330 s). A socket timeout leaves the statement running on the server; the `finally` still charges it |
| Q19 | The image `mcr.microsoft.com/playwright:v1.64.1-noble` runs under rootless podman with `--network host` and reaches `served_app()` on 127.0.0.1 | Task 1.2 gate (2) | The API-level fallback (task 6.2), logged |
| Q20 | The read user's 0-call `EXPLAIN VIRTUAL` of the preview statement yields the manifest's `fetch_mode` | Probe 1.10(b) | With the operator path on, read the virtual schema's `FETCH_MODE` property as the CLI does; with it off, show "mode unknown" and treat the quota badge as replay |
| Q21 | The `stored` console query keeps the stored-only exemption with the `GROUP BY` around it, because the `REQ_SOURCE` filter is still pushed down | Task 1.7 (planning code) and probe 1.10(b) | Run the plain stored-only query and count per slice in the backend; the page shows the counts |
| Q22 | `CFB_API.GAMES` (Phase 5's pass-through of `CFB_MODEL.GAME`) exposes each game's week, start time and completion | Task 4.2 reads the deployed view | Read the predicted games of the week from `CFB_API.PREDICTIONS` and say "N predicted games" in the plan; the outcome after the run still states whether the point moved |
| Q23 | Each FBS row of `CFB_CORE.TEAMS` lists its logos as `http://a.espncdn.com/i/teamlogos/ncaa/500/<id>.png` and `…/500-dark/<id>.png`, and `https://` serves the same files | `make logos` summary (9.6 run): files, skipped hosts, failures | Teams without a usable logo get the badge; a new host is added to the allowlist only by a reviewed edit, never automatically |
| Q24 | Every FBS team's `HOME_VENUE_ID` resolves to a `VENUE` row with coordinates | The "Not on the map" count of the 9.6 run | P6-UI-b falls back to the team list's own `LOCATION_LATITUDE` and `LOCATION_LONGITUDE` (through `CFB_MODEL.TEAM`, an addition to P6-UI-a) |
| Q25 | CFBD's stored pregame win probability (`HOME_PREGAME_WIN_PROB`) exists for next week's games before kickoff, and the line (`PREGAME_SPREAD`) too | The 9.6 snapshot's `league-week` | "This week" shows the line alone, ordered by rank and line, labeled "no probability yet" |
| Q26 | The relaxation separates the densest real cluster (Dallas–Fort Worth, Florida) at 36 px within 80 passes on a 1280-px-wide map | The 9.6 snapshot in the preview | Raise the passes or shrink national markers to 30 px; the conference zoom remains the real fix |

## Review Findings

<!-- Populated by speq-implement after code review. -->
