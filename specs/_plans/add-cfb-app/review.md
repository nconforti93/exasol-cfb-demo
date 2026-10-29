# Review: add-cfb-app (Phase 6, App)

Reviewer: independent review, 2026-10-22. Read only, with no DB session and no CFBD call. The only file written is this one.
Skills loaded first: `exasol:exasol-database` (grammar and keyword references), `exasol:exasol-virtual-schema-adapter-development`. Versions were checked live against the npm registry and PyPI on 2026-10-22. `speq plan validate add-cfb-app` passes: 10 delta specs, 64 scenarios, 64 coverage rows.

## Verdict: approve with changes

The architecture is right. It has a read-only path for pages, a narrow owner path tied to a loopback bind, and one spending guard of the form "plan free, act guarded, record always". The curated console is the right call for beat 2. No finding needs a redesign. **Eight major findings must be fixed in the plan before the tasks they touch start.** They fall into three groups:

1. **Two security holes in "loopback is enough".**
   - The `Origin` check is defined against "the app's own origin". If that origin is derived from the `Host` header, DNS rebinding passes the check.
   - The read-user start check does not look at what the role itself holds.
2. **Quota-honesty gaps.** The plan's own rule is that every live call ends up in the ledger, and these paths break it:
   - a live statement that fails or times out leaves no ledger entry;
   - the read pool retries a statement after a transaction conflict;
   - the ledger lock does not block, so a refresh makes every preview fail for about 2 minutes;
   - a refresh thread that dies (including on `--reload`) leaves its spend unrecorded;
   - the `app` ledger plan has no cap, so every live click at the demo would be refused.
3. **Demo fit and schedule.**
   - Follow mode jumps the pages to an unpublished point in the middle of beat 3.
   - The task graph keeps all frontend work behind "Phases 2–5 merged", with the demo about 3.5 weeks away.

None of these is a blocker: each has a contained fix, listed below. Group A can start once M1 (security, task 1.3) and M8 (read-user check, task 2.1) are written into the specs.

| # | Severity | Theme | One line |
|---|---|---|---|
| M1 | major | Security | No `Host` allowlist, and "own origin" is undefined: DNS rebinding and an exposed Vite dev server reach the owner path |
| M2 | major | Security | The read-user start check ignores the role's own privileges, `PUBLIC`, direct object grants, ownership and connection access |
| M3 | major | Quota | A failed, timed-out or retried live statement spends calls without a ledger entry |
| M4 | major | Quota | `Ledger.exclusive()` does not block: a running refresh fails every preview and console run (even in replay), and a double-click raises an unmapped error |
| M5 | major | Quota | A refresh thread that dies mid-run (process exit, `make app --reload`) leaves its spend unledgered |
| M6 | major | Quota / process | Budget facts are stale: November is authorized (1000), but the plans `app` and `add-cfb-app` have no cap, so the demo would refuse every live action |
| M7 | major | Demo fit | `CURRENT_POINT` moves after the fetch step, so follow mode reloads the pages to an unpublished week mid-refresh, and a refresh started elsewhere never reloads them at publish |
| M8 | major | Schedule / testability | Frontend and pure backend work wait for Phases 2–5 to merge. No probe split and no cut line before the mid-November demo |

---

## 1. Security

### M1 (major): add a `Host` allowlist and a fixed origin set; the loopback rule alone does not hold the owner path

**Evidence**
- The plan's `app/access` spec says every `POST` needs `X-CFB-Action: 1` and "an `Origin` that is either the app's own origin or one listed in `APP_CORS_ORIGINS`". "Own origin" is not defined. The common implementation compares `Origin` with `f"http://{request.headers['host']}"`.
- **DNS rebinding.** A page on `evil.example` re-resolves its own name to `127.0.0.1`. The browser then treats requests to `http://evil.example:8000` as same-origin:
  - the custom header needs no preflight;
  - `Origin` is `http://evil.example:8000`, and `Host` is `evil.example:8000`;
  - so a check derived from `Host` passes.

  The attacker can then:
  - call `POST /api/preview` with arbitrary game ids, one call each;
  - run the console's `fan-out` (3 calls);
  - start a live refresh with any `max_calls`, because the confirmation is only a number in the body;
  - read every `GET`: config, inspector log, timeline.

  The ledger caps bound the damage, but the plan's promise is "never spend quota by surprise".
- **`make app` (development).** Vite proxies `/api` to the loopback backend. If Vite is started with `--host` (a common habit), or `vite.config.ts` sets `server.host`, the LAN reaches the owner path through the proxy. The backend still sees a loopback bind, so `APP_OPERATOR=auto` stays on. With `changeOrigin: true` the proxy also rewrites `Host` to `127.0.0.1:8000`, which defeats any `Host` check.
- The `SameSite=Strict` cookie does not help: after rebinding, the request is same-site.
- `APP_HOST=localhost` counts as loopback by name only.

**Fix** (task 1.3, `security.py`; `app/access` Background plus two scenarios)
1. **A `Host` allowlist on every request, `GET` included** (Starlette `TrustedHostMiddleware` or an equivalent check):
   - `127.0.0.1:<port>`, `localhost:<port>` and `[::1]:<port>`;
   - in development, also `localhost:5173` and `127.0.0.1:5173`;
   - anything else gets 400 before routing.

   This is the real defence against rebinding.
2. **A fixed `Origin` set,** never derived from `Host`: the same list as http origins, plus `APP_CORS_ORIGINS`. A `POST` without `Origin` is refused. `Content-Type: application/json` is required.
3. **Vite.** `vite.config.ts` sets `server.host: '127.0.0.1'`, `strictPort: true`, and proxy `changeOrigin: false`. `scripts/app.py dev` refuses a Vite host override. Vite 8 keeps its own `server.allowedHosts` check on.
4. **Bind check.** `APP_HOST` is resolved, and auto mode counts as loopback only when **every** resolved address is loopback.
5. **New scenarios:**
   - "A request with a foreign `Host` is refused": `GET /api/config` and `POST /api/preview` with `Host: evil.example:8000` and a matching `Origin` both answer 400, and the mock gets nothing;
   - a `POST` without `Origin` answers 403.
6. **Optional, and limited.** A per-start random operator token served in `index.html` (`<meta>`, so CSP-safe) adds little: after rebinding, an attacker can read `index.html` too. Keep it out unless Phase 8 needs it for a non-loopback bind. There, real authentication is the answer, as the plan already says.

On WSL2, a `127.0.0.1` bind is forwarded to Windows `localhost`, which is intended because the browser runs on Windows. It is not exposed to the LAN in NAT or mirrored mode. Put one line about this in the user guide.

### M2 (major): the "read-only read user" start check must check effective privileges, not names

**Evidence**
- Decision [16] and the `app/access` Background refuse a user that is `sys` or the owner, holds a system privilege other than `CREATE SESSION`, or holds a role other than the app role. The check misses:
  1. **The role's own grants.** If someone runs `GRANT INSERT ON SCHEMA CFB_PRED TO CFB_APP` or `GRANT CREATE TABLE TO CFB_APP`, the user still holds "only `CFB_APP`" and passes.
  2. **Grants to `PUBLIC`.**
  3. **Direct object privileges on the user.**
  4. **Objects or schemas the user owns.** Ownership bypasses grants.
  5. **`ACCESS` on a connection.** This one matters most: a connection is how a key leaks.
  6. **`IMPERSONATION` grants.**
- The skills' references do not name the privilege dictionary views (the plan says so, Q5). The grammar does confirm `GRANT IMPERSONATION`, `CREATE CONSUMER GROUP … QUERY_TIMEOUT` and `ALTER USER … SET CONSUMER_GROUP`.
- **Separately, "read-only" is not "zero-spend".** `CFB_APP_WEB` can run `CFB_API_LIVE.GAME_PREVIEW` from any SQL client, using the password in `.env`, and spend a call outside the ledger. This is acceptable for a demo, but the plan should say it, not imply that the role cannot spend.

**Fix**
- **Probe 1.1(d) reads the effective-privilege views.** It reads the `EXA_USER_*` views and the `EXA_ROLE_*` views of the roles the user holds (system, object and role privileges, recursively, `PUBLIC` included), connection privileges, and objects owned by the user. The start check is then a **positive** rule:
  - system privileges are exactly `CREATE SESSION`;
  - object privileges are exactly `SELECT` on the schemas `CFB_API` and `CFB_API_LIVE`;
  - there are no owned objects, no connection access and no impersonation.
- **Fallback if the views are not readable by the user:** the backend runs the check on the operator path against the `EXA_DBA_*` views, when the operator path is on. Otherwise it refuses to start with `APP_OPERATOR=off` unless `APP_SKIP_READ_CHECK=yes` is set, with a warning.
- **Scenario addition:** "…and then with `TEST_CFB_APP` granted `INSERT` on `TEST_CFB_META`: the start MUST fail, naming the role and the privilege."
- **Lint rule** (`sqlcatalog`): `CFB_API_LIVE.GAME_PREVIEW` may appear only in the preview template that `live.py` executes. Every other statement naming it fails the lint, so no page can spend by accident.
- **Docs:** state in `docs/user-guide.md` and in the architecture's security model that the read user can spend preview calls from a SQL client, and that the ledger guards the app's spend, not the role's.

### Smaller security items (minor)

- **m1. CSP.** `default-src 'self'` breaks two common shortcuts:
  - an inline "no-flash" theme script (set the theme class from a same-origin script, or accept the flash);
  - Google-hosted fonts (self-host them).

  React's `style` props go through the CSSOM and are fine.
- **m2. Passwords in statement text.** The password of `CREATE/ALTER USER … IDENTIFIED BY` appears in statement text. Probe 1.1 should check whether Exasol masks it in the audit and profiling views (`EXA_DBA_AUDIT_SQL`) when auditing is on. If it does not, document it.
- **m3. Arbitrary preview ids.** `POST /api/preview` accepts any integer `game_id`. Derive the id on the server from `(team, opponent)`, the next meeting else the latest, and take only the pair from the client. This narrows the spend surface to "one game per pair" and makes the cache key meaningful. It also matches the Phase 5 UX contract exactly.
- **m4. `LEDGER=off` must be visible.** If an owner shell carries `LEDGER=off`, the app spends unledgered without a word. `GET /api/config` should report the ledger state, and the header should show a "ledger off" badge.

---

## 2. Quota safety

### M3 (major): a live statement that fails, times out or is retried spends calls the ledger never sees

**Evidence**
- In the plan's spending guard, `rows = run(statement)` is followed by "for each LIVE slice: ledger.append(…)", using the `REQ_*` columns of the result. When the statement raises, there are no `REQ_*` rows and therefore no entry. CFBD may already have been called, up to `MAX_ATTEMPTS` (4) per slice. The causes:
  - a 404 or 5xx after retries;
  - a 429 storm;
  - an error in the fetch UDF after the HTTP call;
  - the 30 s query timeout of task 1.1(e).
- Phase 2 learned this the hard way. `scripts/loader_cli.py` (in the p2 worktree) notes that "pyexasol's 30 s default once cut off a response that had been paid for", and the CLI records the run **whatever its outcome**.
- `ReadPool` adds "1 retry on a transaction conflict" (decision [13]). The preview runs on the read pool. If the read-through lookup of `GAME_PREVIEW` meets a refresh writing the stored tables, a conflict after the UDF has fetched leads to a second fetch.
- The `app/live-spend` spec has no scenario for a failed live request.

**Fix**
- **`live.py` writes the ledger in a `finally`.** On an exception, append one entry per planned live slice with `status` null, `attempts` null, `cost = MAX_ATTEMPTS` (the recorder's rule for unknown attempts) and `error` set to the first line. Over-counting is the safe direction. Write the ledger **before** the fetch-log insert, so a failing owner connection cannot lose it.
- **No automatic retry for any statement whose plan is not 0 calls.** Every live statement goes through `live.py`, never through the pool's retry wrapper. Unit-test this in `test_app_db.py`.
- **Timeouts for live statements.** Either exempt them from the session `QUERY_TIMEOUT` or set it above the fetch UDF's worst case (`MAX_ATTEMPTS` × backoff). Give them a socket timeout ≥ 120 s, as Phase 2 does.
- **A consumer group for the read user** (grammar confirmed: `CREATE CONSUMER GROUP … QUERY_TIMEOUT`, `ALTER USER … SET CONSUMER_GROUP`). It is a sturdier place for the page timeout than `ALTER SESSION`, which is unverified (probe 1.1(e)). If you use it, have it exempt live statements.
- **New `app/live-spend` scenario:** "A live request that fails is still charged". The mock answers 500 on every attempt. The response is an error; the ledger gains one entry with cost 4 and the error; the fetch log gains the failed slice.

### M4 (major): the ledger lock does not block, but the plan treats it as if it did

**Evidence**
- `vs/host/ledger.py` `Ledger.exclusive()` uses `flock(LOCK_EX | LOCK_NB)`. It raises `LedgerError("another run holds the ledger lock …")` at once. `flock` locks belong to the open file description, so two threads of the same process conflict too.
- The plan's `act()` wraps everything in `with Ledger.exclusive()`, **including replay and stored plans**. The extracted guard runs the refresh under the same lock for its whole run, as the CLI does (`_launch` → `with self._exclusive(): return self._run(...)`).
- The consequences:
  - **During a refresh** (about 2 minutes locally, the heart of beat 3), every preview click and console run fails, even in replay at 0 calls.
  - **The same happens while `make refresh` or `make record` runs** in a terminal.
  - **A double-click, or two tabs, on one preview:**
    - neither request finds the cache entry yet;
    - the first takes the lock;
    - the second gets a `LedgerError`, which the HTTP mapping does not handle (400, 403, 409 and 503 are listed);
    - so it probably surfaces as a 500.
  - Without the non-blocking lock, the same race would spend twice.

**Fix**
- **Take the ledger lock only when the plan has live calls.** Replay, stored, contradiction and cached plans never touch it.
- **An in-process single flight per `(session, game_id)`** for previews, with the cache checked **inside** it. A concurrent duplicate waits for the first result and is served "cached · 0 calls". The frontend disables the button while it fetches. The API client never retries a `POST` automatically.
- **Map `LedgerError` for a held lock to 409 `live_busy`,** naming the holder: "a refresh is running; live previews resume when it ends". While the newest refresh run is `RUNNING`, the preview's cost label reads so.
- **New scenarios:**
  - "Two concurrent clicks spend once" (two threads, one session: one mock request, one ledger entry, both answered);
  - "A preview during a running refresh": replay is served; live answers 409 `live_busy` with no call.

### M5 (major): a refresh thread that dies mid-run leaves its spend unledgered

**Evidence**
- The ledger record is written by the background thread **after** `EXECUTE SCRIPT` returns. If the process ends first, the record is never written:
  - Ctrl-C;
  - a crash;
  - `make app`, which runs uvicorn `--reload`, so saving any watched file restarts the worker.
- The script has already committed its `FETCH_LOG` and `LOAD_RUN` rows per step, and CFBD has been called. The in-process single flight is lost on restart too. The database run lock still protects against overlap.
- The plan's spending principle is that the ledger records every spend, and that the ledger is written by the same code that spent. Nothing covers this case.

**Fix**
- **Reconcile at startup and before every live plan** (`loader/guard.py`, shared with the CLI):
  - find `LOAD_RUN` rows of kind `REFRESH_WEEK` or `BACKFILL` since the month start that have live calls and no ledger entry `<KIND>#<run id>`;
  - record each with `LOAD_RUN.CALLS`, or the estimate when that is null, and the error "recorded by reconciliation";
  - while any is unrecorded, refuse new live actions with 409 until it is recorded.

  This makes the CLI safer too.
- **Know the run id early.** The thread records the owner session id (`pyexasol` `session_id()`). The action then learns its run id from another connection (`LOAD_RUN WHERE SESSION_ID = …`), so `/api/actions/{id}` can name it even if the process later dies.
- **Development mode:** `make app` either excludes `app/` and `loader/` from `--reload` while a refresh action is running, or starts with live refresh disabled (the button reads "live refresh needs `make app-serve`"). Pick one and log it.
- **New scenario:** "A refresh whose app process died is still recorded". Start a refresh, kill the server process during `WAIT`, let the script finish, start the app again: the ledger holds the run once.

### M6 (major): budget facts are stale, and the app's ledger plans do not exist

**Evidence**
- The plan says "November is not authorized as of 2026-10-22" in Forces, U2, Q11, [21] and task 8.2. The owner has since authorized **1000 calls for 2026-11** (`vs/live/ledger.yaml` in p2, commit `bb34528`). The demo is around mid-November.
- That file's `plans` section lists only `add-cfbd-vs-foundation` and `add-cfb-persistence-refresh`. `Ledger.check` refuses a plan without a cap ("add one to vs/live/ledger.yaml"). So `APP_LEDGER_PLAN=app` and `add-cfb-app` would refuse every live preview, console run and app refresh, **at the demo too**. The Migration row "+ plans `app` (cap set by the owner)" is listed, but no user prerequisite makes it happen. The file's header says "Edited by the owner only".
- Plan caps hold "over all months", so rehearsals and the demo itself draw down the same `app` cap.
- The guard checks the raw `REMAINING_CALLS`. Phase 2's `CFB_META.QUOTA` already gives the project-level number (`REFRESH_SPENDABLE`, which respects `PROJECT_BUDGET`) and a `REFRESH_RESERVE`. A run of previews on stage could eat the reserve the weekly refresh needs.

**Fix**
- **Replace U2 with U5:** "the owner adds `app: <N>` (suggested 60: rehearsals plus the demo) and `add-cfb-app: 12` to `vs/live/ledger.yaml` before group G and before the first rehearsal." Update Forces, Q11, [21] and 8.2 to say November is authorized.
- **Schedule 8.2** after Phase 2's live group J and Phase 5's 7.3 in the same window protocol.
- **Quota guard for previews and the console:** `REFRESH_SPENDABLE − REFRESH_RESERVE`, the backfill rule, so on-demand spend never eats the refresh reserve. The refresh itself keeps Phase 2's rule.
- **Startup warning:** when the configured `APP_LEDGER_PLAN` has no cap, `GET /api/config` and the header say so ("live actions disabled: no ledger cap for plan app"). It must not surface as a refusal on the first click on stage.

### Smaller quota items (minor)

- **m5. Refresh retry headroom.** Previews and the console check `planned + MAX_ATTEMPTS − 1`, but the refresh uses Phase 2's guard with no headroom. `REFRESH_WEEK` takes no limit argument (the CLI passes it only to backfill), and Q10 says the estimate can be wrong. Either add a headroom of `(MAX_ATTEMPTS − 1) × estimate` to the refresh's ledger check, or label the confirmation "up to N calls, plus retries". State which in `app/refresh`.
- **m6. The ledger's month check uses a stale `/info` reading.** The cached quota can be up to 5 minutes old, and `sharedPool` is true, so other clients spend in between. The ledger file total partly covers this. Read `/info` fresh for **live refresh** plans (it is free, and the refresh plan reads it anyway). Keep the 5-minute cache for previews only.
- **m7. Preview caching.** Per session, 15 minutes and 20 entries is fine. With the in-flight fix (M4), concurrent tabs of one browser share it correctly. A second browser spending again is by design and explicit. Previewing a played game (the latest-meeting fallback) never changes, so it could be cached longer. That is not worth the complexity now.

---

## 3. Demo fit

### M7 (major): follow mode moves the pages to an unpublished week in the middle of beat 3

**Evidence**
- `CFB_MODEL.CURRENT_AS_OF` (Phase 3 `model/as-of`) is the newest week that is done, from the **stored games**. `REFRESH_WEEK` merges the games in its fetch steps, before `MODEL`, `PREDICT`, `SIMULATE` and `PUBLISH`. So `CFB_API.CURRENT_POINT` moves to week N+1 about a minute before the publication exists.
- Plan [17] and `app/as-of`: "when `CURRENT_POINT` changes, after a refresh, every page reloads". On stage, during the timeline, the odds page therefore flips to "no published run at 2026, after week 10". Predictions and upsets reload half-populated.
- For a refresh started **elsewhere** (beat 3's "one SQL call" from a SQL client), nothing reloads the pages when `PUBLISH` lands: the point has already changed, and only a point change triggers follow mode. The app-started path reloads "after the run"; the other path does not.
- Probe 1.1(c) checks that `REFRESH_TIMELINE` is readable during a step. It does not check that **page reads** stay healthy while `SIMULATE` writes and `PUBLISH` regenerates the what-if views with DDL. That window is exactly the beat 3→4 transition.
- The as-of scenario "After a refresh the pages follow" is component-only. Nothing integration-level covers this ordering.

**Fix**
- **Change the follow key** to `(AS_OF_SEASON, AS_OF_WEEK_SEQ, PUBLICATION_ID, WHATIF_RUN_ID, WHATIF_VIEWS_CURRENT)` from `CURRENT_POINT`, polled with the timeline. Reload on any change.
- **Defer follow-mode reloads while the newest refresh run is `RUNNING`.** Show "refresh running: pages update when it publishes", then reload once at the end. This works the same for app-started and externally started runs.
- **Widen probe 1.1(c):** as the app user, read odds, what-if and the guide during `SIMULATE` and during `PUBLISH`. Record waits, conflicts and errors. Fix the pool's retry and timeout policy from the result (M3 already forbids retries for live statements).
- **Promote the follow scenario to integration** (`test_app_as_of_it.py`): start a refresh elsewhere, poll `/api/points` during `WAIT`, and assert that the follow key's publication changes only after `PUBLISH`. Keep the component test for the UI half.

### Beat coverage

| Beat | What the plan delivers | Verdict |
|---|---|---|
| 1. Hook | The app opens at `CURRENT_POINT`, with odds, arrows and the run banner | Delivered. The URL-addressable point is a nice extra |
| 2. API is a schema | The query log with exact SQL; `EXPLAIN VIRTUAL` on demand; `REQ_*` and the waterfall; the curated console (fan-out, contradiction, stored, over-budget) | Delivered, with the gaps below |
| 3. One call refreshes | Plan, confirm, background run, a 1 s poll of `REFRESH_TIMELINE`; follows runs from a SQL client | Delivered once M4, M5 and M7 are fixed |
| 4. Pages move | Scorecard with `IS_HEADLINE` and `FREEZE_KIND`; upsets with "T-n" and source; odds with arrows; what-if toggles and the conditional guide; head-to-head with a click-only preview | Delivered; follow mode per M7 |

### Is the curated console the right call for beat 2? Yes, with three adjustments (minor)

It gives the evidence in fixed form: exact expected plans pinned against the catalog, at most 3 calls, and no injection surface. A free-text console in a web page would be the wrong risk for a demo.

- **m8. "157 years already persisted" has no console line.** Stored slices count toward `MAX_CALLS_PER_QUERY` (`cfbd-vs/fan-out`: "Stored slices count toward the budget"). So a console query over 1869–2025 is refused at plan time, and the `stored` entry (1997, 1 slice) proves the mechanism but not the scale. Point the narration and the console's help text at a serving-view read that the inspector shows as "no virtual table: 0 calls", for example the head-to-head series since 1897, or a count over `CFB_API.GAMES`. Do not invent a VS query that the budget refuses.
- **m9. The console runs as `sys`.** In front of a DB audience, the inspector's principal column reading "operator (sys)" weakens the message "any reader with `SELECT` gets this". Consider running the console's `EXPLAIN` and `SELECT` as a reader user with the existing `CFB_READER` role. The role exists already (`make grants`), and an `app-user` sibling could create the user. That leaves only the refresh and the fetch-log insert on the owner path. If you keep `sys`, at least label it "owner (for the fetch log)". This is the presenter's call (U3).
- **m10. Show the HTTP request, and offer the SQL.**
  - Render the slice key as the HTTP request it became: `GET https://api.collegefootballdata.com/games/teams?…`, without the key.
  - Add a "copy SQL" button, so the presenter can paste the same text into a SQL client for credibility. The roadmap's beat 2 starts in SQL: `CREATE VIRTUAL SCHEMA`, then `SELECT`.

### Other demo-fit items (minor)

- **m11. Mission line 20: "the live `REFRESH_WEEK(n+1)` with the SQL↔HTTP inspector".**
  - Phase 2 commits per table, so `FETCHES` rows for the run appear during the run.
  - Let the inspector's refresh view poll with the timeline, so the requests appear as the fetch steps run, not only per finished run.
- **m12. The default refresh week can be a no-op on stage.**
  - The button proposes `NEXT_WEEK_SEQ`. A week whose games are not all completed does not move the point (Q13), so beat 4's "pages move" fails silently.
  - The refresh **plan** should say before the click whether the target week is complete ("the point will move to week 10", or "week 10 is not complete: the point stays at 9").
  - The choreography itself (restore "Sunday before N+1" and refresh week N+1) is Phase 8's, but the app must make the outcome visible in advance.
- **m13. Replay mode breaks the quota badge, and AWS has no replay.**
  - `CFB_API_LIVE.QUOTA` reads `/info` through the virtual schema. In replay, that needs a recording ("a missing recording fails the query").
  - In replay, the badge must not query `QUOTA`. It shows "replay · no calls" and never an error banner.
  - Note for Phase 8: AWS has no BucketFS replay host directory (`ORCHESTRATOR-STATUS.md`), so the AWS demo is live-only. The rehearsal and the fallback are local.
- **m14. The what-if point's season.** Toggles are offered when the picker's week sequence equals `CURRENT_POINT.WHATIF_AS_OF_WEEK_SEQ`. `CURRENT_POINT` has no what-if season column, so at a season boundary a picked point in another season could match. Compare `(season, week_seq)`, taking the season from `WHATIF_RUN`.
- **m15. Shared point validation.** Point validation ("unknown or after-current → the current point plus a notice") is specified in `routes/points` and `routes/odds` (task 3.1). Predictions, upsets and the scorecard take the same `season` and `week`. Make it one shared FastAPI dependency, and add the after-current case to `test_after_current_points_never_offered` for a non-odds route.

---

## 4. Contract fidelity

The plan honours the Phase 5 contract closely. Checked against `api/serving-contract`, `api/on-demand`, `api/roles` and the review resolution:

| Contract rule | Where in the plan | Status |
|---|---|---|
| Reads only `CFB_API`/`CFB_API_LIVE` on the read path | sqlcatalog lint plus `test_pages_read_through_app_role` | OK. Add the `GAME_PREVIEW`-only-in-`live.py` lint (M2) |
| Owner allowlist | `operator.py` templates plus `test_app_operator.py` | OK. Refresh plan reads `CFB_META.*`, console reads `CFBD_VS`, both deliberate |
| Autocommit | ReadPool | OK |
| Start at `CURRENT_POINT`; never offer `IS_AFTER_CURRENT` | as-of spec, 3 scenarios | OK. Share the validation (m15) |
| `IS_HEADLINE`; `FREEZE_KIND = ALL` label, verbatim | predictions spec, 2 scenarios | OK |
| "T-n" with `PROB_SOURCE` beside every rank; "since `ALL_TIME_FROM_SEASON`" | upsets spec, format test | OK |
| Arrow threshold 0.0070 from `ANALYSIS_SETTINGS.MOVE_THRESHOLD` | odds spec ("arrows from 0.7 points") | OK. Nit: say "percentage points" (`DELTA_P_IN_CFP` is a fraction) |
| What-if passes `WHATIF_RUN_ID`; empty or unknown column → reload; no toggles while `WHATIF_VIEWS_CURRENT` is FALSE | odds spec, 3 scenarios, [23] | OK. Season check (m14) |
| Forced runs labeled | Out of scope, not shown | OK |
| Preview: click only, one literal id, fetching state, next else latest meeting | head-to-head spec, 6 scenarios | OK. Derive the id on the server (m3) |
| `RAW_JSON` never sent to the browser | head-to-head Background | OK. Add an assertion to `test_click_fetches_next_meeting_once` |
| Fetch-log writes for app requests (a Phase 6 decision) | [5], operator path, `APP_PREVIEW#` and `APP_CONSOLE#` | OK. Phase 2's `QUOTA` counts these under `OTHER_CALLS` (`USED_CALLS` − backfill − refresh), so no change is needed there |

Two smaller notes:
- **Context strings.** `REFRESH_WEEK#<run>` matches Phase 2's `<RUN_KIND>#<RUN_ID>`. The new `APP_PREVIEW#` and `APP_CONSOLE#` contexts are new values, as the plan says.
- **Test hooks.** `TEST_FAIL_PUBLISH` (Phase 5) and `TEST_STEP_SLEEP` (Phase 2) are correctly attributed. Verify one thing in task 4.2: that `hold_deploy_lock` (a **Phase 4** harness helper) holds the same lock `REFRESH_WEEK`'s `CHECK` takes (Phase 2's H1 lock kinds). If not, the "refused while locked" scenario needs a Phase 2 helper that starts a run in another session.

---

## 5. Architecture and YAGNI

- **One worker and a pool of 4 sessions: right.**
  - A pyexasol connection is one session and is not thread-safe. A checkout pool under FastAPI's thread pool is the standard pattern, and the in-process state (sessions, cache, single flight, actions) needs one process.
  - Add these to the pool (minor, m16):
    - an acquire timeout that answers 503 "busy", not an unbounded wait behind 40 threads;
    - replacement of a broken session after an outage, which the outage scenario needs;
    - no read session held across a live preview's network wait, since live calls go through `live.py`.
  - The refresh's own owner connection is right.
- **No router, state, data-fetching or chart library: realistic for this scope.**
  - Four path routes, one `useSyncExternalStore` store and four SVG forms are a few hundred lines.
  - The one thing TanStack Query would give for free is cancelling stale responses when the as-of point changes quickly. Specify an `AbortController` per page load in `api/client.ts` (minor, m17).
  - React 19 StrictMode double-invokes effects in development. The polling hooks and the "never fetch on load" rule must be written for that. The component test covers the preview, so make the timeline poll idempotent too.
- **Over-engineered for a 3.5-week horizon, though each piece is defensible.**
  - The parts that pay for themselves: the spending guard, the operator allowlist, the curated console, the timeline poll and the validated fixtures.
  - The parts that could slip without hurting a beat:
    - the chat-slot store test;
    - the population switch;
    - URL-addressable points (keep follow mode);
    - the projector and accessibility pass beyond the contrast tests;
    - the `make app-perf` local protocol (keep the budget table, measure on the 8.1 rehearsal);
    - the frontend-fixture-vs-pydantic cross-validation (nice; keep it if cheap).

  See M8 for the cut line.
- **Version claims: verified** (npm registry and PyPI, 2026-10-22).

  | Claim | Registry | Result |
  |---|---|---|
  | react 19.3.1 (2026-10-14) | 19.3.1, 2026-10-14 | Confirmed |
  | vite 8.3.5; Node `^20.19 \|\| >=22.12` | 8.3.5; engines as stated | Confirmed |
  | TypeScript 7.0.2 latest; typescript-eslint 8.72.0 peer `typescript >=4.8.4 <6.1.0` | 7.0.2 (released 2026-07-08); peer exactly as stated | Confirmed. TypeScript 6.0.3 (2026-04-16) is the newest 6.0, so the choice is forced and correct |
  | @vitejs/plugin-react 6.1.2 peer `vite ^8` | Yes; its other peers are optional | Confirmed |
  | vitest 5.0.5 peer `vite ^6 \|\| ^7 \|\| ^8` | `^6.4.0 \|\| ^7 \|\| ^8`; also `@types/node ^22 \|\| >=24` | Confirmed. Pin `@types/node` 24 to match local Node 24.15 |
  | eslint 10.13.0; react-hooks 7.1.1 accepts eslint 10 | Yes | Confirmed |
  | FastAPI "four releases in 24 h" | **Eight** releases, 0.157.0 to 0.161.0, between 2026-10-21 13:56 and 2026-10-22 18:30 UTC; requires `starlette>=0.46.0` with no upper bound | Understated. The 7-day rule plus `uv.lock` is the right answer; also pin starlette through the lock |
  | @playwright/test 1.64.1 | 1.64.1, 2026-10-14 | Confirmed |

---

## 6. Testability and process

### M8 (major): the task graph puts all frontend and most pure backend work behind "Phases 2–5 merged"

**Evidence**
- The parallelization is A → B → C → C2 → D → E → F → G, and B waits for Phases 2–5 to be merged. Frontend tasks 5.1–5.6 sit in groups C and D, after B.
- Yet the plan says the component tests run "against the fixtures of 1.8". They need no database, no route and no other phase.
- Probe 1.1 is gated on Phases 2–5 as a whole, although parts (a), (d) and (e) need only a scratch namespace (a Lua script, users and grants).
- **State on 2026-10-22** (`ORCHESTRATOR-STATUS.md`):
  - Phase 2 is mid-way through 4.2, with live group J due by 2026-10-31;
  - Phase 3 is in group A;
  - Phase 4 has not started implementing;
  - Phase 5 is reviewed but not implemented.

  Phases 7 and 8 follow, and the demo is around mid-November. As drawn, Phase 6 cannot start its first frontend line until all of that has merged.

**Fix**
- **Re-cut the groups:**
  - **A:** 1.2–1.8, plus the probe part **1.1-early** (a, d, e) in a `PROBE_` namespace with a toy Lua script, at 0 calls.
  - **A-FE:** 5.1, then 5.2–5.6 in parallel, right after 1.8 and 1.2, against the fixtures. This runs while Phases 3–5 implement.
  - **B onward:** unchanged, gated on the merges. **1.1-late** (b, c) runs at the head of B.
- **Define a demo-minimum cut line** in the plan, for the orchestrator to apply if Phase 5 lands late:
  - **must:**
    - odds with arrows and the banner;
    - the timeline with refresh;
    - head-to-head with the preview;
    - the console's contradiction, fan-out and stored queries;
    - the scorecard headline;
    - the upset of the week;
    - the footer;
    - the spending guard, M1–M7;
  - **may slip to Phase 8:**
    - the population switch;
    - the calibration chart;
    - the URL-addressable as-of point (keep follow mode);
    - the chat-slot store;
    - the accessibility pass;
    - `make app-perf`.

### Scenario mapping, E2E and live cap

- **Mapping.** All 64 scenarios are mapped; `speq plan validate` passes. The "who starts and who finishes" table is good practice. Gaps to add, from the findings above:
  - foreign `Host` (M1);
  - privilege through the role (M2);
  - failed live request charged (M3);
  - concurrent clicks and preview during a refresh (M4);
  - process death during a refresh (M5);
  - follow mode deferred until publish, at integration level (M7).
- **The E2E fallback is sound but under-ambitious** (minor, m18).
  - API-level flows over `served_app()` prove the backend. They do not prove the browser wiring that M7 is about: polling, follow mode, the preview button never auto-firing.
  - Playwright on WSL usually fails on missing system libraries (`libnss3`, `libasound2` and similar), which need `sudo npx playwright install-deps`.
  - Before dropping to API-level flows, try the official image through **podman**, which is already on this host for Exasol nano: `mcr.microsoft.com/playwright:v1.64.1-noble`, with `--network host` against `served_app()`.
  - Record which path the gate took.
- **Is the live cap of 12 realistic?** Yes, with a pre-flight gate.
  - The planned spend is 1 preview, 3 for the fan-out and about 4 for the refresh: 8 in total.
  - The ledger checks pass in sequence with the 3-call headroom: 0+4, 1+6 and 4+4 (+ m5 headroom) all ≤ 12.
  - The risk is the refresh estimate. If Phase 2's own weekly `make refresh` has not run that week, or the season has accumulated non-final slices, it can exceed 4.
  - Make 8.2's refresh conditional on its plan showing ≤ 4. Otherwise refresh in replay and log it.
  - With November authorized (M6), 8.2 is no longer blocked on U2, only on the plan caps.
- **The integration ledger is right.** The temporary ledger and caps file in `app_client()` is the right design. Note for task 1.9: the CLI's rule "`--prefix` → no ledger" must stay the CLI's choice, made by passing a null ledger. The app tests need a prefixed namespace **with** a temporary ledger, so the extracted guard must take the ledger as a parameter and must not derive it from the prefix.

---

## What's good

- **The two-principal split is argued from evidence.** The invoker-rights finding is backed by project memory and re-probed (1.1(a)). The alternatives are rejected for concrete reasons: impersonation, a request queue, a runner user that is "an owner with one more password".
- **"Plan free, act guarded, record always".** The re-plan against `max_calls` is the right answer to a mode switch between showing a label and clicking it, a subtle failure most plans would miss.
- **The curated console** turns beat 2's three promises into pinned, checkable plans with bounded spend, using the catalog's own slice-key code (task 1.7).
- **Polling the serving timeline instead of pushing** follows runs from any client. That keeps beat 3's "one SQL call" true and needs no socket channel.
- **Same-principal `EXPLAIN VIRTUAL` with the server-recorded `stmt.query`.** The inspector shows the text the database received, and the client never submits SQL.
- **Phase 2's guard is extracted, not re-implemented,** with the CLI tests kept green, and the recorder helpers are moved to `vs/host/livecalls.py`. One spending implementation.
- **The version discipline is honest.** The TypeScript 6.0 vs 7 constraint is real and verified, and the 7-day rule fits FastAPI's churn.
- **Accessibility and dataviz are done as method, not decoration:** a validated palette in both themes, contrast computed in a unit test, icon plus word for every state, a table view for every chart.
- **Clear test ownership:** a "who starts and who finishes each integration-test file" table, synthetic fixtures validated against the backend models, and zero CFBD calls in every test tier.

---

## Review resolution

Planner revision, 2026-10-22/23. The owner (Nico) was away; the orchestrator decided each finding for him, and the decisions are logged in `decision-log.md` (§ Interview → "Review resolution", new design entries [24]–[33], revised [2]–[17] and [20]–[23], and Q1–Q22). No DB session, no CFBD call, and no file outside this plan directory changed, apart from this section. Skills loaded first: `exasol:exasol-database` (grammar and keyword references), `dataviz`, and the speq skills. Versions were re-checked against PyPI and the npm registry on 2026-10-22 at 21:46 UTC.

Result: **73 scenarios** (64 + 9 new), all mapped; **38 tasks** (36 + 2: the late probe 1.10 and `live.py` as 2.4), **3 `[expert]`** (1.9, 2.4 and 4.2; 4.1 lost its tag because its hard part moved to 2.4); **12 parallel groups**. `speq plan validate add-cfb-app` passes without warnings.

### Facts checked for the revision

| Fact | Source | Used in |
|---|---|---|
| `Ledger.exclusive()` opens `<ledger>.lock` per call and takes `flock(LOCK_EX \| LOCK_NB)`; a held lock raises `LedgerError` at once, also between threads of one process; a disabled ledger takes no lock; `LedgerRefused` subclasses `LedgerError`; `check()` has a `headroom` parameter | `../exasol-cfb-demo-p2/vs/host/ledger.py` | M4, [27] |
| The CLI takes the lock for every run of the real namespace, replay included, and records only after the statement returns, while its process lives | `../exasol-cfb-demo-p2/scripts/loader_cli.py` | M4, M5 |
| Caps: plans `add-cfb-app: 12`, `app: 60` (commit `ac151ab`); months `2026-10: 400`, `2026-11: 1000` (`bb34528`) | `../exasol-cfb-demo-p2/vs/live/ledger.yaml` | M6, [29] |
| `REFRESH_SPENDABLE = GREATEST(0, LEAST(REMAINING_CALLS, PROJECT_BUDGET − USED_CALLS))`, `BACKFILL_SPENDABLE = GREATEST(0, REFRESH_SPENDABLE − REFRESH_RESERVE)`; `CFB_API_LIVE.QUOTA` passes the row through | Phase 2 `loader/quota` spec; Phase 5 `api/on-demand` | M6 |
| After a kill or client abort `REFRESH_WEEK` can log nothing more, and the next run frees the `RUNNING` row; a process that dies frees the lock with its session; `REMAINING_BEFORE` is read only for live runs with due slices | `refresh_week.lua` header, `loader/runlock.py`, `sql/meta/load_run.sql` (p2) | M5, [28], Q16, Q17 |
| Fetch function worst case per slice: 4 × 30 s attempts + 3 waits ≤ 30 s (`Retry-After` limit) + 10 s DoH ≈ 220 s | `vs/cfbd_vs/http.py`, `vs/cfbd_vs/resolver.py` | M3, [26] |
| A stored-only query (`REQ_SOURCE = 'STORED'`) has no fetch and no budget | recorded `cfbd-fetch/read-through` scenario "Stored-only queries never call the API" | m8 |
| `CFB_READER` holds only `SELECT ON CFBD_VS`; the `TEST_` user `CFB_TEST_READER` holds `CREATE SESSION` and `SELECT` on `TEST_CFBD_VS`, password in harness memory | `CLAUDE.md`, `tests/it/harness.py` | m9 |
| The mock scripts any status, delay and count per path (`script(path, status=, delay_ms=, times=)`) | `tests/mock_cfbd/sidecar.py` | M3, M4 scenarios |
| Grammar: `GRANT CONNECTION` and `GRANT ACCESS ON CONNECTION … [FOR SCRIPT]` are distinct; object grants take `ON SCHEMA`; consumer groups take `QUERY_TIMEOUT`; `ALTER SESSION SET` is generic | `exasol-grammar.md` | M2, M3 |
| Starlette's `TrustedHostMiddleware` lowercases the name, drops the port and answers 400 | `starlette/middleware/trustedhost.py` (GitHub) | M1, [24] |
| `CURRENT_POINT` has `PUBLICATION_ID`, `WHATIF_RUN_ID`, `WHATIF_AS_OF_WEEK_SEQ`, `WHATIF_VIEWS_CURRENT`, and no what-if season; `REFRESH_TIMELINE` has no session id | Phase 5 `api/serving-contract` | M7, m14, M5 |
| The Playwright image `mcr.microsoft.com/playwright:v1.64.1-noble` exists; podman 4.9.3 is installed | MCR tag list; `podman --version` | m18 |

### Major findings

| # | Resolution | Where |
|---|---|---|
| M1 | **Accepted.** A `Host` allowlist on every request, name and port, before routing (400): the loopback names with the app's port, the Vite hosts only under `make app`, and `APP_ALLOWED_HOSTS` for a read-only network bind. A configured `Origin` set, never derived from `Host`; a `POST` without `Origin` or without JSON is refused (403). Vite pinned to `127.0.0.1:5173`, `strictPort`, its host check on, `changeOrigin: false`, and the launcher refuses `--host`; a vitest test pins the config. `APP_HOST` is resolved, and every address must be loopback. New scenarios "A rebinding attempt with a foreign Host is refused" and "A cross-origin POST cannot act" (now with the no-`Origin` case). WSL2 note in the user guide. The operator token stays out, as the review advised | [24], revised [3], [15]; `app/access`; tasks 1.2, 1.3, 2.2, 7.1 |
| M2 | **Accepted.** A positive check of the effective privileges of the read user and the console user: direct, through roles (recursively) and `PUBLIC`, both connection grants, impersonation, and ownership. Probe 1.1(d) names the views; fallback on the operator path; with the operator off, refuse unless `APP_SKIP_READ_CHECK=yes` (warned in the header). New scenario "A privilege that reaches the read user through its role, PUBLIC or a connection is refused" (role `INSERT`, `PUBLIC` `SELECT`, connection access). Lint `test_game_preview_only_in_live`. The user guide and the security model say the roles can still spend from a SQL client | [25], revised [16]; `app/access`; tasks 1.1, 1.6, 2.1, 7.1, 7.2 |
| M3 | **Accepted.** The ledger in a `finally`, before the fetch log; on error one entry per planned live slice at `MAX_ATTEMPTS`. Live statements run once, on a reserved live session or the console session, never through the pool's retry (the pool refuses them). `QUERY_TIMEOUT` 300 s and socket 330 s, above the computed 220 s worst case (unit-tested margin). New scenario "A live request that fails is still charged" (500 on every attempt: 4 mock requests, one entry of cost 4, a fetch-log row with NULL status, 502) | [26], revised [5], [6], [13]; `app/live-spend`; tasks 1.4, 2.1, 2.4, 4.1 |
| M4 | **Accepted, and verified:** `Ledger.exclusive()` cannot wait (above). The app keeps its semantics and designs around it: the lock only for live plans; an in-process live gate (≤ 5 s) so the app's own threads queue instead of colliding on `flock`; busy checks (own refresh, a `RUNNING` refresh run); a held lock → 409 `live_busy` naming the holder; single flight per (session, game) with the cache checked inside; one console run per session; the button disabled; no `POST` retry. New scenarios "Two concurrent clicks spend once" and "A live request during a refresh is refused as busy, and a replayed one is served" (also covers a lock held by another process) | [27]; `app/live-spend`, `app/head-to-head`; tasks 1.5, 2.4, 4.1, 4.2, 5.1, 5.5 |
| M5 | **Accepted.** `loader/guard.reconcile` records every live `REFRESH_WEEK` or `BACKFILL` run of the month that is no longer running and has no ledger entry, under the ledger lock, at start, before every live refresh and when a run ends; while one is unrecorded, live actions answer `reconcile_pending`. The run id is learned early from the owner session id. `make app` disables the live refresh. New scenarios "A refresh whose app process died is still recorded" and "The development server offers no live refresh" | [28], revised [11], [12]; `app/refresh`, `app/live-spend`; tasks 1.9, 4.2 |
| M6 | **Resolved by the orchestrator** (caps added on the Phase 2 branch; November authorized for 1000). Plan names: `app` for runtime spend, `add-cfb-app` for task 8.2. Every "November not authorized" statement removed; U2 marked resolved; the `vs/live/ledger.yaml` migration row says this plan does not edit it. Previews and the console are checked against `BACKFILL_SPENDABLE` (= `REFRESH_SPENDABLE − REFRESH_RESERVE`, floored at 0); the refresh keeps Phase 2's rule and may use the reserve. A missing cap or `LEDGER=off` shows at start. 8.2 runs after Phase 2's group J and Phase 5's 7.3. New scenario "The ledger state is reported before the first click" | [29], revised [7], [21]; `app/live-spend`; tasks 2.2, 4.1, 8.2 |
| M7 | **Accepted.** The follow key `(AS_OF_SEASON, AS_OF_WEEK_SEQ, PUBLICATION_ID, WHATIF_RUN_ID, WHATIF_VIEWS_CURRENT)` comes with every timeline poll; reloads are held while the newest refresh run is `RUNNING` and happen once at its end, for app-started and external runs alike. Probe 1.10(c) reads odds, what-if and the guide during `SIMULATE` and `PUBLISH`. New integration scenario "The pages follow a refresh only after it has published"; the component scenario now covers the hold | [30], revised [17]; `app/as-of`; tasks 1.10, 4.2, 5.1 |
| M8 | **Accepted.** New groups: A (toolchain, pure modules, frontend foundations, early probe), A-FE1/A-FE2 (all six frontend tasks against fixtures), A3 (the database layer with fakes) start now; P2 (guard extraction, `live.py`) starts when Phase 2 merges; B onward waits for Phases 2–5. Probe split: 1.1 = (a, d, e) now in a `PROBE_` namespace with a toy Lua script; 1.10 = (b, c) at the head of B. A demo-minimum cut line with a must list and an ordered stretch table (below) | [31]; `plan.md` Parallelization; tasks 1.1, 1.10 |

### Minor findings

| # | Resolution |
|---|---|
| m1 | No inline script: `public/theme-init.js` sets the theme before the first paint; no web fonts (system font stack). The shell scenario asserts that `index.html` has no inline script and names nothing from another host ([33], [15], `app/shell`) |
| m2 | Probe 1.1(d) reads `EXA_DBA_AUDIT_SQL` (when auditing is on) and the profiling views after `CREATE/ALTER USER … IDENTIFIED BY`, and the user guide states the finding (task 7.1) |
| m3 | The browser sends only the pair; the server picks the next meeting, else the latest, and writes its id as the literal ([32], `app/head-to-head`) |
| m4 | `GET /api/config` reports the ledger state; the header shows "ledger off" (scenario "The ledger state is reported before the first click") |
| m5 | Chosen and stated in `app/refresh`: the ledger check adds the headroom of one request (`MAX_ATTEMPTS − 1`), and the confirmation reads "up to N live calls, plus retries". Not `(MAX_ATTEMPTS − 1) × estimate`: see "Changed" below ([29]) |
| m6 | Every refresh plan reads `/info` fresh; the 5-minute reading stays for previews and the console ([7], [29]) |
| m7 | No change, as the review suggested: per-session cache, 15 min, 20 entries, now shared correctly by the single flight; a longer life for played games is noted and declined ([8], [32]) |
| m8 | The `stored` console entry is a stored-only query over 1869–2025 (`REQ_SOURCE = 'STORED'`, counted per slice with `GROUP BY`): no fetch, no budget, 157 rows on the real deployment, 0 calls. The help text also points at the pages' stored history (head-to-head since 1897). Q21 covers the `GROUP BY` form ([10], `app/inspector`) |
| m9 | The console runs as a `CFB_READER` user, `CFB_CONSOLE_WEB` (`CFB_TEST_READER` in tests), created by `make app-user`; only the fetch-log insert stays on the owner path. The inspector shows the principal "reader" ([2], [10], `app/access`, `app/inspector`) |
| m10 | Slice keys are also shown as `GET https://api.collegefootballdata.com…` lines, never with the key; a "copy SQL" control ([9], `app/inspector`) |
| m11 | The inspector's refresh view polls the run's `FETCHES` with the timeline; the scenario reads the requests during `WAIT` (`app/inspector`) |
| m12 | The refresh plan states the target week's games, how many are completed in storage and how many have not kicked off, with the expectation; the action states the point before and after. Q22 covers the `CFB_API.GAMES` columns (`app/refresh`) |
| m13 | In replay the quota badge reads "replay · no calls" and never queries `QUOTA`; the fetch mode comes from a 0-call `EXPLAIN` (Q20). Phase 8 note: AWS has no replay host directory, so the AWS demo is live-only ([33], `app/live-spend`) |
| m14 | The what-if point is compared as (season of `WHATIF_RUN`, `WHATIF_AS_OF_WEEK_SEQ`) ([23], [30], `app/odds`) |
| m15 | One FastAPI dependency (`routes/deps.py`) validates the point for every route; the after-current case is tested on the predictions route too (`app/as-of` scenario, `test_after_current_point_falls_back`) |
| m16 | Pool checkout ≤ 5 s, then 503 `busy`; broken sessions replaced; no pool session held across a live wait (the reserved live session) ([13], [33]) |
| m17 | One `AbortController` per page load; `StrictMode`-safe polling; component tests render in `StrictMode`; the preview's "never on load" test runs in `StrictMode` ([33], task 5.1) |
| m18 | Playwright in order: local without `sudo`, then the podman image `v1.64.1-noble` with `--network host`, then the API-level flows; the path is recorded. The E2E smoke now also checks the follow-mode hold and that no preview request is sent on load ([20], tasks 1.2, 6.2) |

### Other review notes

| Note | Resolution |
|---|---|
| Odds: "percentage points" | `app/odds` says "percentage points" and gives the fraction rule (0.023 reads "+2.3") |
| `RAW_JSON` assertion | Added to "A click fetches the next meeting's preview once" |
| `hold_deploy_lock` vs `REFRESH_WEEK`'s lock | Task 4.2 verifies; fallback: a run with `TEST_STEP_SLEEP` in another session (Q15) |
| Task 1.9: `--prefix` → no ledger stays the CLI's choice | The guard takes the ledger as a parameter and never derives it from the prefix; the CLI passes a disabled ledger |
| Live cap of 12 | 8.2 refreshes live only if its plan shows ≤ 4, else in replay with the reason logged; with the headroom the checks pass at 4, 7 and 11 of 12 |
| Versions | Re-checked. FastAPI: 23 releases since 2026-10-19, 8 in 29 h; under the 7-day rule 0.143.2 today. Exact `==` pins on the Python side too (fastapi, starlette, uvicorn), since fastapi bounds starlette only from below and now also requires `opentelemetry-api`. Vite 8.3.4, eslint 10.12.0 and typescript-eslint 8.71.2 under the rule today. `@types/node` 24.19.2 pinned. Two release dates corrected |
| Over-engineering | Turned into the cut line's stretch table rather than removed |

### Demo-minimum cut line (`plan.md`, Parallelization)

- **Must:** the shell (one process, tabs, footer, outage banner, themes, CSP-safe theme script, ledger and quota badges); all of M1–M7; opening at the current point with follow mode and its hold, and the shared point validation; odds with arrows, banner, focus team, what-if toggles and the unconditional rooting guide; the scorecard headline with its label and the line, last week graded, this week's predictions; the upset of the week with "since 1978", "T-n" and source, the week's other upsets; head-to-head with series, meetings, next meeting and the preview; the inspector's log, plans, HTTP lines, waterfall, and the console's `fan-out`, `contradiction` and `stored`; the refresh with plan, confirmation, timeline, refusals and outcome; the E2E smoke; 8.1–8.3.
- **Stretch, in cutting order:** (1) the local `make app-perf` run; (2) the keyboard and projector pass; (3) the chat-slot test; (4) the predictions extras (population switch, calibration chart, season to date); (5) the upsets top-25 lists; (6) the URL-addressable point and the earlier-week picker; (7) the `over-budget` console entry and "copy SQL"; (8) the conditional rooting guide. The table in `plan.md` names the scenarios that move with each.

### Rejected or changed from the review's suggestion

Nothing was rejected outright. Where the review offered options, or where its assumption did not hold, these choices were made:

- **M3, consumer group:** not adopted. A consumer group applies to the whole read user, so it would cut the live statements too, and it is a new database object. The per-session `ALTER SESSION` on separate sessions (pool 30 s, live 300 s) gives both timeouts; Q18 covers its failure.
- **M4, waiting:** beyond the review, the app's own live requests wait for each other in an in-process gate (≤ 5 s) before the non-blocking `flock`, because two app threads would otherwise fail each other with `live_busy` on an ordinary pair of clicks on different games.
- **M5, when to reconcile:** at start, before every live refresh and when the timeline sees a run end, not "before every live plan". A preview checks an in-memory pending flag instead; an operator query per click would cost latency against the 2 s target.
- **M5, the scenario:** the review assumed the killed process's script finishes. Phase 2's own documentation says a client abort ends it; the scenario is written to hold either way, and records which happens (Q16).
- **M5, attribution:** a reconciled run is charged to the app's ledger plan, also when it was started from a SQL client (beat 3's "one SQL call"). The month total is exact; the plan total errs high.
- **M5, development mode:** of the review's two options, the live refresh is disabled under `make app`. Excluding directories from `--reload` depends on watch flags, and edits elsewhere still restart the worker.
- **M7, stuck runs:** beyond the review, a `RUNNING` run with no step change for 10 minutes stops holding the reloads, so a dead run cannot freeze follow mode.
- **M8, the cut line:** the review's must list omitted the what-if toggles and the rooting guide; beat 4 names them, so they are must. Only the conditional guide (with toggles) is stretch, since Phase 5 measured it near its latency limit.
- **m5:** not `(MAX_ATTEMPTS − 1) × estimate`. That would make a 4-call refresh need 16 calls of room, fail task 8.2 under its cap of 12, and fit only three rehearsed refreshes into `app`'s 60. The headroom of one request plus the "plus retries" label, with Phase 2's per-table `REFRESH_SPENDABLE` check as the hard limit, is the chosen balance.
- **m9:** the review offered "label it owner" as the minimum. The orchestrator chose a reader user; without its password the console is off with a reason, rather than falling back to the owner.
- **m12:** before the fetch, the app cannot know whether CFBD will report every game completed. The plan states the stored state and the kickoff times as an expectation, and the outcome states what happened.

### For the orchestrator

- **Start now:** groups A, A-FE1 → A-FE2 and A3 need no other phase and no CFBD call; probe 1.1 needs one "local free" window.
- **On Phase 2's merge:** group P2 (1.9, 2.4). Tell the Phase 2 session (hand-off P6-c) that `scripts/loader_cli.py` will be refactored with its behavior unchanged, that the app's live refresh holds the ledger lock for its whole run, and that the CLI's lock during replay runs makes the app's live requests answer busy. Calling `guard.reconcile` from the CLI is optional.
- **On the merge of Phases 2–5:** group B onward, starting with probe 1.10.
- **Owner items:** U4 now also needs `CFB_CONSOLE_PASSWORD` in `.env`; U3 adds [24] to the decisions for the owner to review. No item blocks a task.
- **Cut line:** apply it, in the listed order, if Phase 5 lands late; the stretch scenarios then leave this plan's specs before recording and go to Phase 8's interview.

## Cross-plan items from Phase 7 (2026-10-23)

- **P7-a:** The app-state store holds the head-to-head pair, plus an action that sets it.
- **P7-b:** `GET /api/config` returns `chat` as an object, `{available, enabled, reason, model}`, not `false`.
- **P7-c:** The read-user effective-privilege check is a reusable function of (user, roles, schemas). The chat user `CFB_CHAT_WEB` reuses it.
- **P7-d:** The query log accepts entries with principal `chat`.
- **P7-e:** `create_app` and `app_client` accept a `chat_factory`.
