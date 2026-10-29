# Tasks: add-cfb-app

Worktree: ../exasol-cfb-demo-p6 (branch add-cfb-app, from the foundation)

**UI revision 2026-10-23** (plan.md § UI revision; decisions [34]–[42]): map home, team page, league tabs. Light process: a few tests per task, no plan review. Do groups UI-A and UI-B first; they need no other phase.

## Group UI-A (start now; no other phase)
- [x] 9.0 Foundation: contract (types, pydantic models, synthetic fixtures with coordinates, fixtureApi routes incl. `/api/team/<id>` and `/api/logos/<id>.png`), routing (`/`, `/team/<id>`, `/league/<tab>`, old-path redirects, `mapConference`, default `home`), design tokens, `teamColor.ts`, `src/ui/` (Card, Chip, TeamMark, RankTag, RecordLine, StatTile, SqlButton)
- [x] 9.5 Logos: `scripts/logos.py` + `make logos` (owner read of `CFB_CORE.TEAMS`, https, `espncdn.com` only, ≤ 1 MB PNG, `~/.cfb-demo/logos`), `routes/logos.py` (`/api/logos/{id}.png`), `APP_LOGO_DIR`, `APP_LOGOS`; unit tests only

## Group UI-B (after 9.0)
- [x] 9.1 Shell: nav (Map · League · Head-to-head), team search, League page hosting the built Odds/Predictions/Upsets as tabs, TeamMark in their rows, restyle to cards/grid
- [x] 9.2 Map home (`src/map`): d3-geo 3.1.1, topojson-client 3.1.0, us-atlas 3.0.1 (+ @types/d3-geo 3.1.1, @types/topojson-client 3.1.5), `layout.ts` relaxation + leader lines, conference zoom, list view
- [x] 9.3 Team page (`src/team`): hero, schedule/results, SP+/polls; outlook panels (odds + trajectory, this week, games to watch + what-if, rivalries) with empty states
- [x] 9.4 League This week + Stats (`src/league/WeekPage.tsx`, `StatsPage.tsx`): matchup order/upset alert; road warriors, SP+ movers, conference strength, streaks; SQL buttons
- [x] 9.6 Real-data snapshot: `scripts/app_fixtures.py` + `make app-fixtures` + `make app-preview REAL=1` (owner, stored tables only, 0 CFBD calls, output `~/.cfb-demo/fixtures/`); run once on AWS after the backfill + Phase 3 views + `make logos` Run 2026-10-23 on AWS (Exasol 2026.1.1), season 2026 after week 7: 138 FBS teams, 138 on the map, 138 with logos, 138 team pages, 49 games this week, 10 statements, 0 CFBD calls, ~35 s.

## Group UI-R (gate: 2.2, Phase 2 merged, Phase 5 thin views P6-UI-b/c/d deployed)
- [x] 9.7 Routes `points`, `teams`, `teams/map`, `team/{id}`, `league/week`, `league/stats` over `CFB_API`; focused route tests and AWS live-data smoke (138 map teams, 56 week games, all routes HTTP 200). The stored-query inspector GET and zero-call plan support the page SQL buttons; full inspector/console task 4.3 remains open.

## Group UI-O (gate: Phases 4–5 merged, 3.1)
- [x] 9.8 Route `team/{id}/outlook` + run-dependent parts of `league/week` (our pick, stakes). Served from Phase 5's AWS views: Auburn outlook shows run 2, odds and rooting guide; the week route returns 56 games. Focused backend tests, 33 frontend tests, typecheck, ESLint and production build passed; the loopback app was restarted and both routes returned HTTP 200. No CFBD calls or commit.

## Group A
- [x] 1.1 Probe, early part
- [x] 1.2 Toolchain and scaffolding (frontend and backend parts done)
- [x] 1.3 `app/backend/settings.py` and `security.py`
- [x] 1.4 Move `planned_live`, `slice_calls`, `query_costs` and `charged` from `scripts/record.py` t
- [x] 1.5 `app/backend/sessions.py`
- [x] 1.6 `app/backend/sqlcatalog.py` and `explain.py`:
- [x] 1.7 `app/backend/console_catalog.py`
- [x] 1.8 Frontend foundations: (frontend part; backend half, `app/backend/models.py` and `tests/unit/test_app_fixtures.py`, done by the backend agent)

## Group A-FE1
- [x] 5.1 Shell (`src/shell`, `src/state`, `src/api`, `App.tsx`):

## Group A-FE2
- [x] 5.2 Odds (`src/odds`, `src/charts` bars)
- [x] 5.3 Predictions (`src/predictions`, `src/charts` dot plot)
- [x] 5.4 Upsets (`src/upsets`)
- [x] 5.5 Head-to-head and spending (`src/headtohead`, `src/spend`)
- [x] 5.6 Refresh and inspector (`src/refresh`, `src/inspector`, `src/charts` timeline and waterfall

## Group A3
- [x] 2.1 `app/backend/db.py` and `operator.py`:

## Group P2 (gate: Phase 2 merged)
- [~] 1.9 Extract `loader/guard.py` from `scripts/loader_cli.py` [expert]. The shared guard exists and the app uses its startup reconciliation; the CLI still needs to adopt the extraction.
- [x] 2.4 `app/backend/live.py`, the guarded execution of Key Interfaces [expert]. One AWS preview was planned at one call, executed once under the shared ledger and fetch log, then served from the browser cache at zero calls. Quota moved 245→246, ledger `add-cfb-app` 0→1/12. A zero-call confirmation was refused before any paid query.

## Group B (gate: Phases 2–5 merged; **2.2 only:** Phase 2 merged + P6-UI-b/c/d deployed, for slice S2)
- [ ] 1.10 Probe, late part
- [x] 2.2 `app/backend/main.py`, `scripts/app.py` (`serve`, `dev`, `user`) and the Makefile targets:
  - `scripts/app.py user` reads `CFB_APP_PASSWORD` and `CFB_CONSOLE_PASSWORD` through `vs/host/appenv.py` (environment, then `~/.cfb-demo/app.env`; never the repo `.env`, [44]); it creates no secrets file (`make app-env` does) and never prints a password.
- [ ] 2.3 `tests/it/app_stack.py` and `tests/it/fixtures/load-plan.app.yaml`, per Key Interfaces

## Group C
- [x] 3.1 `routes/config`, `points`, `odds` (odds rows, run banner, settings, what-if availability). Config now reads the virtual schema's real `FETCH_MODE` and refreshes shared-ledger totals per request; all routes are live on AWS.
- [x] 3.3 `routes/predictions`: frozen this-week picks, graded prior week, both scorecard populations, and calibration/season only when the stored totals do not include a later week. AWS HTTP smoke: 56 upcoming picks, 62 graded games, two weekly scorecard rows. Two synthetic tests, 11 focused backend tests, Ruff and mypy passed; zero CFBD calls, no commit.
- [x] 3.4 `routes/upsets`, including the all-time and line-era lists. AWS app-role smoke returned 19 week, 28 all-time (ties) and 25 line-era rows; zero CFBD calls, no commit.
- [x] 3.5 `routes/matchup`: ordered series, completed games and next meeting. AWS app-role smoke returned Michigan–Ohio State's 121 meetings and Auburn–Ole Miss's 48, with current/frozen next-game predictions. Frontend typecheck, ESLint, 14 focused tests and production build passed; zero CFBD calls, no commit.

## Group C2
- [x] 3.2 `routes/whatif`. Games, conditional odds, unconditional guide and the conditional coming-week guide are live. The guide counts matching stored seasons per unset game and branch; AWS run 2 (1M seasons) returned 55 games for Auburn in 6.5 s and excluded the selected game. Invalid game input returned 400. Run-change unknown-column handling and a two-run game cache are implemented; local `TestClient` against the real DB returned a 55-game conditional guide with the selected game excluded. Seven route unit tests, Ruff and strict mypy passed. No CFBD calls or commit.
- [x] 4.1 `routes/preview`, `routes/quota` and fetch-log integration over `live.py`: zero-call plan chooses next/latest; POST uses the shared live gate, ledger, quota guard and per-browser cache; quota reads `/info` through CFB_API_LIVE with a five-minute cache. AWS one-call smoke and cache, port-8000 config/quota/plan and 409 refusal with `max_calls=0` passed. The implementation keeps fetch-log write handling in `live.py` rather than a separate ops module.
- [~] 4.2 `routes/refresh`, `routes/timeline`, `routes/actions`, `ops/refresh.py` and `ops/reconcile` [expert]. The zero-call plan, guarded worker, action state, early run-id lookup and timeline reconciliation are implemented. Local replay ran as `LOAD_RUN` 39 directly and 40 through HTTP at zero calls. After the owner lifted the app-specific cap ([46]), AWS run 18 refreshed week 8, spent four ledgered calls and moved the point to week 8. Process-death recovery and isolated lock/busy integration scenarios remain.

## Group D
- [x] 4.3 `routes/inspector`, `routes/console` and `console.py`. The per-browser stored-query log and zero-call explanation cover every stored catalog query. The curated console explains and runs its four statements as `CFB_CONSOLE_WEB` through `LiveExecutor.run_statement`; AWS verified all four plans at 0 calls, contradiction/stored/refusals at 0 calls and one paid fan-out at 3 ledgered calls (`docs/app-verification.md`). The console logic lives in `routes/console.py`; there is no separate `console.py`.

## Group E
- [ ] 6.1 Complete `tests/it/test_app_access_it.py` (every page through the app role; no secret reac
- [-] 6.2 E2E smoke. *Superseded 2026-10-23 (UI revision + light process): the app opens on the map now; the flows become a manual walk in V.3 (map → team → league odds → replay refresh → head-to-head preview click). Playwright optional.*
- [ ] 6.3 `scripts/app.py perf` (`make app-perf`), with the fixed catalog of read endpoints

## Group F
- [ ] 7.1 `docs/user-guide.md`, section "The app": (+ UI revision: map/team/league, `make logos` as an external non-CFBD fetch, `APP_LOGOS=off`, `make app-fixtures`, `make app-preview REAL=1`)
- [ ] 7.2 `specs/architecture.md`:
- [ ] 7.3 `docs/roadmap.md`:
- [ ] 7.4 `CLAUDE.md`, dense bullets:

## Group G (sequential, gated)
- [ ] 8.1 Local rehearsal at 0 calls:
- [~] 8.2 AWS live verification. The owner's 2026-10-27 cap revision [46] superseded the 12-call Phase 6 allocation; the October 400-call month cap remains. AWS week-8 refresh run 18 spent four calls under `APP_LEDGER_PLAN=add-cfb-app` and published week-8 data. The earlier preview spent one call. Curated console verification remains.
- [ ] 8.3 Zero-call proof and write-up:

## Follow-ups
- [x] FU-1 Backend adds an optional `refresh_target {season, week, season_type}` to `ConfigResponse`, computed from `SEASON_WEEK` (the next week after the current point, across the regular→postseason and season boundaries). The RefreshPanel uses it instead of its week+1 guess. Update `types.ts`, the schema, the fixtures and the models. Fold this into 2.2/4.2.

## Cross-plan items
- [ ] P7-secrets Phase 7 (`../exasol-cfb-demo-p7`, branch `add-cfb-chat`) has its own chat settings that read the repo `.env` for `ANTHROPIC_API_KEY` and `CFB_CHAT_PASSWORD`. When p6 is merged into p7 it must adopt the same loader (`vs/host/appenv.py`: environment, then `~/.cfb-demo/app.env`; fail fast on a `*_PASSWORD` or `*_API_KEY` key in the repo `.env`) and update its plan, specs and user guide to say `~/.cfb-demo/app.env` instead of `.env` ([44]). Not edited in p7.
- [ ] P7-a..e Phase 7 hand-offs (see review.md); fold them into 5.1, 2.1, 2.2 and 6.x. P7-a update (UI revision): PageId gains `home`, `team`, `week`, `stats` (old ids kept); team page team = `focusTeam`; new `mapConference`
- [x] P6-UI-a (Phase 3): `CFB_MODEL.TEAM` + `COLOR`, `ALTERNATE_COLOR`; live through the map/team API slice.
- [x] P6-UI-b (Phase 5): `CFB_API.TEAMS` + `MASCOT`, `ABBREVIATION`, `COLOR`, `ALTERNATE_COLOR`, `HOME_VENUE_NAME`, `HOME_CITY`, `HOME_STATE`, `LATITUDE`, `LONGITUDE`; deployed with Phase 5 Slice A.
- [x] P6-UI-c (Phase 5): `CFB_API.TEAM_TRAVEL` (`KM_FROM_HOME` via `GREAT_CIRCLE_KM`); deployed with Phase 5 Slice A.
- [x] P6-UI-d (Phase 5): the thin-slice views (`TEAMS`, `GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`, `TEAM_TRAVEL`, `CURRENT_POINT`, `AS_OF_POINTS`) were deployed before publication-dependent routes.
- [ ] Phase 8 note: the demo opens on the map; copy `~/.cfb-demo/logos` with the rehearsal snapshot; `APP_LOGOS=off` for recordings

## Review and Verification
- [ ] R.1 Code review (light process: only the risky code: spend, locks, the SQL guard, the logo fetcher)
- [ ] V.1 Checklist
- [-] V.2 Scenario coverage audit *Dropped 2026-10-23 by the owner's light process ("no coverage audits"); V.3 lists any scenario left without a test.*
- [ ] V.3 Manual testing and verification-report.md
