# Tasks: add-cfb-app

Worktree: ../exasol-cfb-demo-p6 (branch add-cfb-app, from the foundation)

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
- [~] 2.1 `app/backend/db.py` and `operator.py`:

## Group P2 (gate: Phase 2 merged)
- [ ] 1.9 Extract `loader/guard.py` from `scripts/loader_cli.py` [expert]
- [ ] 2.4 `app/backend/live.py`, the guarded execution of Key Interfaces [expert]

## Group B (gate: Phases 2–5 merged)
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
- [ ] 6.2 E2E smoke.
- [ ] 6.3 `scripts/app.py perf` (`make app-perf`), with the fixed catalog of read endpoints

## Group F
- [ ] 7.1 `docs/user-guide.md`, section "The app":
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
- [ ] P7-a..e Phase 7 hand-offs (see review.md); fold them into 5.1, 2.1, 2.2 and 6.x

## Review and Verification
- [ ] R.1 Code review
- [ ] V.1 Checklist
- [ ] V.2 Scenario coverage audit
- [ ] V.3 Manual testing and verification-report.md
