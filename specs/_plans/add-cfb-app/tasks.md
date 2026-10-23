# Tasks: add-cfb-app

Worktree: ../exasol-cfb-demo-p6 (branch add-cfb-app, from the foundation)

**UI revision 2026-10-23** (plan.md § UI revision; decisions [34]–[42]): map home, team page, league tabs. Light process: a few tests per task, no plan review. Do groups UI-A and UI-B first; they need no other phase.

## Group UI-A (start now; no other phase)
- [ ] 9.0 Foundation: contract (types, pydantic models, synthetic fixtures with coordinates, fixtureApi routes incl. `/api/team/<id>` and `/api/logos/<id>.png`), routing (`/`, `/team/<id>`, `/league/<tab>`, old-path redirects, `mapConference`, default `home`), design tokens, `teamColor.ts`, `src/ui/` (Card, Chip, TeamMark, RankTag, RecordLine, StatTile, SqlButton)
- [x] 9.5 Logos: `scripts/logos.py` + `make logos` (owner read of `CFB_CORE.TEAMS`, https, `espncdn.com` only, ≤ 1 MB PNG, `~/.cfb-demo/logos`), `routes/logos.py` (`/api/logos/{id}.png`), `APP_LOGO_DIR`, `APP_LOGOS`; unit tests only

## Group UI-B (after 9.0)
- [ ] 9.1 Shell: nav (Map · League · Head-to-head), team search, League page hosting the built Odds/Predictions/Upsets as tabs, TeamMark in their rows, restyle to cards/grid
- [ ] 9.2 Map home (`src/map`): d3-geo 3.1.1, topojson-client 3.1.0, us-atlas 3.0.1 (+ @types/d3-geo 3.1.1, @types/topojson-client 3.1.5), `layout.ts` relaxation + leader lines, conference zoom, list view
- [ ] 9.3 Team page (`src/team`): hero, schedule/results, SP+/polls; outlook panels (odds + trajectory, this week, games to watch + what-if, rivalries) with empty states
- [ ] 9.4 League This week + Stats (`src/league/WeekPage.tsx`, `StatsPage.tsx`): matchup order/upset alert; road warriors, SP+ movers, conference strength, streaks; SQL buttons
- [ ] 9.6 Real-data snapshot: `scripts/app_fixtures.py` + `make app-fixtures` + `make app-preview REAL=1` (owner, stored tables only, 0 CFBD calls, output `~/.cfb-demo/fixtures/`); run once on AWS after the backfill + Phase 3 views + `make logos`

## Group UI-R (gate: 2.2, Phase 2 merged, Phase 5 thin views P6-UI-b/c/d deployed)
- [ ] 9.7 Routes `teams/map`, `team/{id}`, `league/week`, `league/stats` over `CFB_API`; `tests/it/test_app_team_it.py`

## Group UI-O (gate: Phases 4–5 merged, 3.1)
- [ ] 9.8 Route `team/{id}/outlook` + run-dependent parts of `league/week` (our pick, stakes)

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
- [ ] 1.9 Extract `loader/guard.py` from `scripts/loader_cli.py` [expert]
- [ ] 2.4 `app/backend/live.py`, the guarded execution of Key Interfaces [expert]

## Group B (gate: Phases 2–5 merged; **2.2 only:** Phase 2 merged + P6-UI-b/c/d deployed, for slice S2)
- [ ] 1.10 Probe, late part
- [ ] 2.2 `app/backend/main.py`, `scripts/app.py` (`serve`, `dev`, `user`) and the Makefile targets:
- [ ] 2.3 `tests/it/app_stack.py` and `tests/it/fixtures/load-plan.app.yaml`, per Key Interfaces

## Group C
- [ ] 3.1 `routes/config`, `points`, `odds` (odds rows, run banner, settings, what-if availability) 
- [ ] 3.3 `routes/predictions`
- [ ] 3.4 `routes/upsets`, with the all-time and line-era lists *(stretch)*
- [ ] 3.5 `routes/matchup`

## Group C2
- [ ] 3.2 `routes/whatif`
- [ ] 4.1 `routes/preview`, `routes/quota` and `ops/fetchlog.py` over `live.py`:
- [ ] 4.2 `routes/refresh`, `routes/timeline`, `routes/actions`, `ops/refresh.py` and `ops/reconcile [expert]

## Group D
- [ ] 4.3 `routes/inspector`, `routes/console` and `console.py`

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
- [ ] 8.2 AWS live, at most 12 calls for this plan with **8 planned**, under `APP_LEDGER_PLAN=add-cf
- [ ] 8.3 Zero-call proof and write-up:

## Follow-ups
- [ ] FU-1 Backend adds an optional `refresh_target {season, week, season_type}` to `ConfigResponse`, computed from `SEASON_WEEK` (the next week after the current point, across the regular→postseason and season boundaries). The RefreshPanel uses it instead of its week+1 guess. Update `types.ts`, the schema, the fixtures and the models. Fold this into 2.2/4.2.

## Cross-plan items
- [ ] P7-a..e Phase 7 hand-offs (see review.md); fold them into 5.1, 2.1, 2.2 and 6.x. P7-a update (UI revision): PageId gains `home`, `team`, `week`, `stats` (old ids kept); team page team = `focusTeam`; new `mapConference`
- [ ] P6-UI-a (Phase 3): `CFB_MODEL.TEAM` + `COLOR`, `ALTERNATE_COLOR`
- [ ] P6-UI-b (Phase 5): `CFB_API.TEAMS` + `MASCOT`, `ABBREVIATION`, `COLOR`, `ALTERNATE_COLOR`, `HOME_VENUE_NAME`, `HOME_CITY`, `HOME_STATE`, `LATITUDE`, `LONGITUDE`
- [ ] P6-UI-c (Phase 5): new view `CFB_API.TEAM_TRAVEL` (`KM_FROM_HOME` via `GREAT_CIRCLE_KM`)
- [ ] P6-UI-d (Phase 5): deploy the thin-slice views first (`TEAMS`, `GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`, `TEAM_TRAVEL`, `CURRENT_POINT`, `AS_OF_POINTS`), answering before any publication
- [ ] Phase 8 note: the demo opens on the map; copy `~/.cfb-demo/logos` with the rehearsal snapshot; `APP_LOGOS=off` for recordings

## Review and Verification
- [ ] R.1 Code review (light process: only the risky code: spend, locks, the SQL guard, the logo fetcher)
- [ ] V.1 Checklist
- [-] V.2 Scenario coverage audit *Dropped 2026-10-23 by the owner's light process ("no coverage audits"); V.3 lists any scenario left without a test.*
- [ ] V.3 Manual testing and verification-report.md
