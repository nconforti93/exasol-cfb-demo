# Plan: add-cfb-analyses

## Summary

Roadmap Phase 5 as a light, demo-first plan: `CFB_API`, the thin, commented, zero-call view layer that the app (Phase 6) and the chat (Phase 7) read, plus `CFB_API_LIVE` for the one-call game preview and the read-only roles `CFB_APP` and `CFB_MCP`. Slice A serves everything that needs no simulation (map, team page, this week, stats, head-to-head, upsets from stored lines) now, with the simulation columns empty; slice B fills them in place from Phase 4's `CFB_SIM` once its runs, predictions and guide exist.

Replanned on 2026-10-23 for the owner's light process ("this is a demo, not a full-blown product offering... we don't need a crazy amount of tests"). The reviewed heavy plan (12 features, 78 scenarios, 34 tasks, its own `CFB_PRED` schema) is commit c1be7e1. Phase 4's replan took over predictions, grades, movement and the shown run, so Phase 5 no longer computes anything that needs freezing or publishing. `tasks.md` lists the 8 new tasks and marks every heavy task as absorbed or superseded (`[-]`) with a reason. The light decisions are [41]–[56] in `decision-log.md`; `review.md` carries a note on how its major findings are handled.

## Design

### Context

**Phase 3 as recorded** (`specs/model/`, ADR-031..046; `docs/model-dictionary.md`):
- Derived tables `GAME`, `SEASON_WEEK`, `TEAM`, `RATING_SP`, `RANKING`, `TEAM_MEMBERSHIP`, `TEAM_SEASON`, `AS_OF_POINT`, rebuilt by `REFRESH_MODEL`.
- Views `TEAM_GAME`, `VENUE`, `CONFERENCE`, `CURRENT_AS_OF`, the as-of views (`GAME_AS_OF`, `RANKING_AS_OF`, `TEAM_SEASON_AS_OF`), `SEASON_COVERAGE`, `MODEL_CHECKS`, and the function `GREAT_CIRCLE_KM`.
- `TEAM` carries `COLOR`, `ALTERNATE_COLOR`, `LOGO_URL`, `LOGO_DARK_URL`, `LATITUDE`, `LONGITUDE` (Phase 6's P6-UI-a, done). `GAME` keeps `NOTES` (Phase 7's P7-f, done).
- **Rule:** filter the as-of views with literal `AS_OF_SEASON` / `AS_OF_WEEK_SEQ`; never join `CURRENT_AS_OF` to them (about 21 s, every point evaluated).
- `vs/host/sqlobjects.py` deploys a directory of SQL objects in one transaction with lint, compile pass and comment read-back. Phase 4's commit 74af62c generalizes it (`schema_placeholder`, `keep`, data tables).

**Phase 4 as replanned** (`../exasol-cfb-demo-p4/specs/_plans/add-cfb-score-engine/plan.md`, § Cross-plan hand-offs, decisions [44]–[57]):
- It owns predictions, grades, movement and the shown run: `CFB_SIM` tables (`SIM_RUN`, `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_TEAM`, `SIM_PATH` with layout A outcome strings, `LEVERAGE`, `PREDICTION`) and views (`SIM_TEAM_ODDS`, `POINT_RUN`, `CURRENT_RUN`, `ODDS_MOVEMENT`, `ROOTING_GUIDE`, `PREDICTION_GRADED`, `PREDICTION_SCORECARD`, `PREDICTION_CALIBRATION`, `GAME_WIN_PROB`), and `engine/whatif.py`, a pure SQL builder with configurable schema and table names.
- Refresh steps 200 `PREDICT` and 300 `SIMULATE` are Phase 4's; no run is ever dropped; no DDL is generated per run.
- Superseded here: `CFB_PRED` (`PREDICTION`, `PUBLICATION`, `PUBLISHED_ODDS`), steps 200/400, `WHATIF_PUBLICATION`, the generated what-if views and their escaping, and the review items P5-M3 and P5-m13.
- Its tasks: slice 1 (1.1–1.3 done, 1.4 `SIMULATE` + `engine/whatif.py` open), slice 2 (2.1 predictions, 2.2 `POINT_RUN`/`CURRENT_RUN`/`ODDS_MOVEMENT`/`ROOTING_GUIDE`, 2.3 the AWS hand-off check).

**Phase 6 UI revision** (`../exasol-cfb-demo-p6/specs/_plans/add-cfb-app/plan.md` § UI revision, hand-offs P6-UI-b/c/d):
- A map home, a team page `/team/:id`, and league tabs This week, Odds, Predictions, Upsets, Stats.
- Its real-data snapshot `scripts/app_fixtures.py` already has working SQL against `CFB_MODEL` for the map, the team page, this week and the four stats cards; its contract types are `app/frontend/src/api/types.ts`.
- P6-UI-d: the thin-slice views must answer before any simulation, with the run columns NULL and `HAS_PUBLICATION` FALSE. Slice S2 (`make app-serve` on real data, task 9.7) waits for them; S3 (task 9.8, outlook) waits for slice B.

**Phase 7** reads `CFB_API` as `CFB_MCP` (no `CFB_API_LIVE`), uses `analyses/whatif.py` for its `whatif_odds` tool, `ROOTING_GUIDE` for its guide tool, and the pass-through views for questions outside the pages.

**Owner decision U3 (2026-10-22):** head-to-head uses the stored games, not `/teams/matchup`; the live beat is the one-call game preview.

**Platform facts already established:** views read with their owner's rights (Phase 6 probe S9 (a), 2026-10-22/23); the virtual table `GAMES_PREVIEW` already has `REQ_ROWS` (`vs/catalog/catalog.json`).

Forces:
- **A demo, not a product.** Phase 6's S2 slice is blocked only by these views; the demo window is mid-November. Each slice must end in something the owner can click through.
- **One owner per number.** Phase 4 computes and freezes; Phase 5 only serves. Anything Phase 5 recomputes is a second definition that can drift.
- **Read-only consumers.** The app and the chat read only `CFB_API` (the chat) or `CFB_API` and `CFB_API_LIVE` (the app), as users with one role each.
- **No parameterized views in Exasol.** A view with a point is filtered by its consumer; it must stay cheap when filtered with literals and must not lean on the as-of views.
- **Quota.** October's cap is 400 calls; this plan needs none.
- **Process.** No DB session and no CFBD call while planning; local DB work in "local free" windows under the IT lock; AWS deploy and run only.

**Goals**
- Slice A: `make api` installs 20 commented `CFB_API` views over `CFB_MODEL` (`api/serving-contract`, `api/teams-and-weeks`, `api/league-stats`, `api/head-to-head`, `api/upsets`), and `make grants` adds `CFB_APP` and `CFB_MCP` (`api/roles`). Phase 6's 9.7 routes can replace every snapshot statement with a `CFB_API` query.
- Slice A, live part: `CFB_API_LIVE` with `GAME_PREVIEW` and `QUOTA`, and the refresh timeline views (`api/on-demand`), for Phase 6's S4 slice.
- Slice B: 13 more views over `CFB_SIM` (`api/simulations`, `api/predictions`), the run columns of the slice-A views filled in place, and `analyses/whatif.py` as a thin adapter over `engine/whatif.py`.
- `docs/api-dictionary.md` generated; both slices deployed on local and AWS; zero CFBD calls.

**Non-Goals** (superseded heavy scope, see `tasks.md`)
- `CFB_PRED`, its data tables and Lua scripts; refresh steps 200 and 400; publications; generated what-if views and the escaping rule; the probe S8.
- A conditional rooting guide (with toggles); the guide is Phase 4's precomputed `ROOTING_GUIDE`.
- Forced re-sims and `FORCED_RUN_ODDS` (Phase 4 has none).
- A serving view over `GAME_SCORE_AS_OF` ("current" predictions before a freeze).
- `RANK_MAGNITUDE` (unrounded Elo magnitude), host oracles, history variants of the league, a performance harness with gates.
- Live CFBD calls in this plan: the preview's real latency is measured by Phase 6 under its own ledger cap.
- `PROMOTE()`, `/teams/matchup`, users and passwords (the owner creates them), app or chat code.

### Decision

`CFB_API` is a view-only serving layer: pass-through views over the model tables, page-shaped views for the computed panels, and, in slice B, thin views over Phase 4's objects under the names Phases 6 and 7 already use. Views whose rows depend on a week carry the as-of point as columns, are built on `AS_OF_POINT` and the derived tables, and are filtered with literals by their consumers. Slice A ships the final names and columns, with the simulation columns NULL; slice B replaces the same files so those columns fill in. The one network-reaching view lives in `CFB_API_LIVE`, which only the app role reads.

#### Architecture

```
 CFB_MODEL (Phase 3, derived tables + views)                     CFB_SIM (Phase 4, slice B only)
 TEAM · VENUE · GAME · TEAM_GAME · TEAM_SEASON · RANKING          POINT_RUN · CURRENT_RUN · ODDS_MOVEMENT · SIM_RUN · SIM_RUN_GAME
 RATING_SP · AS_OF_POINT · CURRENT_AS_OF · GREAT_CIRCLE_KM        SIM_RUN_TEAM · SIM_PATH · SIM_TEAM · ROOTING_GUIDE · SIM_PARAMS
        │                                                         PREDICTION · PREDICTION_GRADED · PREDICTION_SCORECARD
        │                                                         PREDICTION_CALIBRATION · GAME_WIN_PROB
        ▼  CFB_API (sql/api/*.sql via analyses/deploy.py → vs/host/sqlobjects.py; 0 calls; views only)
 slice A  010 CURRENT_POINT · 020 AS_OF_POINTS                      ◀ CURRENT_AS_OF, AS_OF_POINT   (+ POINT_RUN, CURRENT_RUN, PREDICTION in B)
          030 TEAMS · 040 GAMES · 041 TEAM_GAMES · 042 TEAM_SEASONS · 043 RANKINGS · 044 SP_RATINGS   (pass-through)
          050 TEAM_TRAVEL (GREAT_CIRCLE_KM) · 060 WEEK_GAMES (point; + predictions, stakes in B)
          100 ROAD_WARRIORS (point) · 110 SP_MOVERS · 120 CONFERENCE_STRENGTH (point) · 130 WIN_STREAKS (point)
          200 MATCHUP_SERIES · 210 MATCHUP_GAMES · 220 MATCHUP_NEXT (+ prediction, stakes in B) · 300 UPSETS (lines; GAME_WIN_PROB in B)
          400 REFRESH_TIMELINE · 410 FETCHES                          ◀ CFB_META.LOAD_RUN, REFRESH_LOG, FETCH_LOG
 slice B  500 ANALYSIS_SETTINGS · 510 PLAYOFF_ODDS · 520 PUBLISHED_RUNS · 530 WHATIF_RUN · 540 WHATIF_GAMES
          550 WHATIF_OUTCOMES · 560 WHATIF_PLAYOFF · 570 WHATIF_TEAMS · 580 ROOTING_GUIDE
          600 PREDICTIONS · 610 SCORECARD_WEEKS · 620 SCORECARD_SEASONS · 630 SCORECARD_CALIBRATION
        ▼  CFB_API_LIVE (sql/api_live/*.sql)                        the only serving views that reach the network
          010 GAME_PREVIEW ◀ CFBD_VS.GAMES_PREVIEW (1 call per P_GAME_ID; replay 0)   020 QUOTA ◀ CFB_META.QUOTA (/info, free)

 make api      ─▶ lint sql/api + sql/api_live ─▶ DEPLOY lock ─▶ sqlobjects.deploy(CFB_API) ─▶ sqlobjects.deploy(CFB_API_LIVE)
 make deploy   ─▶ VS ─▶ loader ─▶ model ─▶ (sim, Phase 4 slice 3) ─▶ api
 make grants   ─▶ CFB_READER (foundation) · CFB_APP: SELECT ON SCHEMA CFB_API, CFB_API_LIVE · CFB_MCP: SELECT ON SCHEMA CFB_API
 make api-docs ─▶ docs/api-dictionary.md (no DB)
 analyses/whatif.py (slice B): game ids ─▶ PATH_POS (WHATIF_GAMES) ─▶ engine/whatif.py over CFB_API names ─▶ SQL any role can run

 Readers:  Phase 6 app user (role CFB_APP) ─▶ CFB_API + CFB_API_LIVE      Phase 7 chat user (role CFB_MCP) ─▶ CFB_API only
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Serve, never compute what another phase owns | slice B views are projections of `CFB_SIM` | One definition per number: the run a page shows, the movement, the grades and the guide are Phase 4's, so the page, the chat and Phase 4's own checks cannot disagree |
| A `CFB_API` twin for every snapshot statement | pass-through views (`TEAMS`, `GAMES`, `TEAM_GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`) and page views (`TEAM_TRAVEL`, `WEEK_GAMES`, the stats views) | Phase 6's `app_fixtures.py` SQL already works on real data; its route statements become the same shapes over `CFB_API`, so 9.7 is a rename, not a rewrite |
| Final names now, empty columns until the engine | `CURRENT_POINT`, `AS_OF_POINTS`, `WEEK_GAMES`, `MATCHUP_NEXT`, `MATCHUP_GAMES`, `UPSETS` | P6-UI-d: the thin slice answers before any run, and slice B fills the same columns without a contract change |
| Points as columns, filtered with literals | `WEEK_GAMES`, `ROAD_WARRIORS`, `CONFERENCE_STRENGTH`, `WIN_STREAKS` | No parameterized views in Exasol; built on `AS_OF_POINT` and the derived tables, never on the as-of views, so a literal filter costs one point (Phase 3's 21 s lesson) |
| Pass-through keeps the source's columns and comments | the six model views | The chat reads one comment per column wherever it looks; a test compares them with the source |
| Network-reaching views in their own schema | `CFB_API_LIVE` | A schema grant cannot exclude one view; "the chat cannot spend quota" stays a grant fact |
| Read-only roles by schema grant, owner's rights in views | `CFB_APP`, `CFB_MCP` | Views run with the owner's rights (Phase 6 probe), so the roles need nothing beyond `SELECT ON SCHEMA` |
| Thin adapter over the engine's SQL builder | `analyses/whatif.py` → `engine/whatif.py` | Phases 6 and 7 keep their import and game-id conditions; Phase 4 keeps the one builder; layout A needs no run guard |
| Sources are the test oracle | every IT compares a view with its `CFB_MODEL`/`CFB_SIM` source or with sums over another serving view | Few tests, each checking a whole view, no host re-implementation |

#### Key Interfaces

**Objects** (files `sql/api/NNN_<view>.sql` and `sql/api_live/NNN_<view>.sql`; one view per file; comment "One row per …"; DDL names unquoted upper case; the columns are the contract in the feature specs)

| File | View | Slice | Reads | Spec |
|---|---|---|---|---|
| `010_current_point.sql` | `CURRENT_POINT` | A, filled in B | `CURRENT_AS_OF`; B: `POINT_RUN`, `CURRENT_RUN` | `api/serving-contract` |
| `020_as_of_points.sql` | `AS_OF_POINTS` | A, filled in B | `AS_OF_POINT`, `CURRENT_AS_OF`; B: `POINT_RUN`, `PREDICTION`, `PREDICTION_GRADED` | `api/serving-contract` |
| `030_teams.sql` | `TEAMS` | A | `TEAM`, `VENUE` | `api/teams-and-weeks` |
| `040`–`044` | `GAMES`, `TEAM_GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS` | A | `GAME`, `TEAM_GAME`, `TEAM_SEASON`, `RANKING`, `RATING_SP` | `api/teams-and-weeks` |
| `050_team_travel.sql` | `TEAM_TRAVEL` | A | `TEAM_GAME`, `GAME`, `TEAM`, `VENUE`, `GREAT_CIRCLE_KM` | `api/teams-and-weeks` |
| `060_week_games.sql` | `WEEK_GAMES` | A, filled in B | `AS_OF_POINT`, `GAME`, `RANKING`; B: `POINT_RUN`, `PREDICTION`, `ROOTING_GUIDE` | `api/teams-and-weeks`, `api/predictions` |
| `100`–`130` | `ROAD_WARRIORS`, `SP_MOVERS`, `CONFERENCE_STRENGTH`, `WIN_STREAKS` | A | `AS_OF_POINT`, `TEAM_TRAVEL`, `TEAM_SEASON`, `TEAM_GAME`, `RANKING`, `TEAM` | `api/league-stats` |
| `200`–`220` | `MATCHUP_SERIES`, `MATCHUP_GAMES`, `MATCHUP_NEXT` | A, filled in B | `TEAM_GAME`, `TEAM`, `CURRENT_AS_OF`; B: `GAME_WIN_PROB`, `PREDICTION`, `CURRENT_RUN`, `SIM_RUN_GAME`, `ROOTING_GUIDE` | `api/head-to-head` |
| `300_upsets.sql` | `UPSETS` | A, source switched in B | `GAME`, `TEAM`; B: `GAME_WIN_PROB`, `PREDICTION`, `ANALYSIS_SETTINGS` | `api/upsets` |
| `400`, `410` | `REFRESH_TIMELINE`, `FETCHES` | A | `CFB_META.LOAD_RUN`, `REFRESH_LOG`, `FETCH_LOG` | `api/serving-contract` |
| `500`–`580` | `ANALYSIS_SETTINGS`, `PLAYOFF_ODDS`, `PUBLISHED_RUNS`, `WHATIF_RUN`, `WHATIF_GAMES`, `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF`, `WHATIF_TEAMS`, `ROOTING_GUIDE` | B | `SIM_PARAMS`, `ODDS_MOVEMENT`, `POINT_RUN`, `SIM_RUN`, `CURRENT_RUN`, `SIM_RUN_GAME`, `SIM_RUN_TEAM`, `SIM_PATH`, `SIM_TEAM`, `ROOTING_GUIDE` | `api/simulations` |
| `600`–`630` | `PREDICTIONS`, `SCORECARD_WEEKS`, `SCORECARD_SEASONS`, `SCORECARD_CALIBRATION` | B | `PREDICTION_GRADED`, `TEAM`, `PREDICTION_SCORECARD`, `PREDICTION_CALIBRATION` | `api/predictions` |
| `api_live/010_game_preview.sql` | `GAME_PREVIEW` | A | `{VS_NAME}.GAMES_PREVIEW` (rendered from the catalog) | `api/on-demand` |
| `api_live/020_quota.sql` | `QUOTA` | A | `CFB_META.QUOTA` | `api/on-demand` |

Allowed references (lint, `analyses/deploy.py`): `sql/api/`: `{API_SCHEMA}.*`, `{MODEL_SCHEMA}.*`, `{SIM_SCHEMA}.*`, `{META_SCHEMA}.REFRESH_LOG`, `{META_SCHEMA}.LOAD_RUN`, `{META_SCHEMA}.FETCH_LOG`; `sql/api_live/`: exactly `{VS_NAME}.GAMES_PREVIEW` and `{META_SCHEMA}.QUOTA`. Never `{CORE_SCHEMA}`.

**Host code**

```
analyses/__init__.py
analyses/deploy.py       make api: lint both directories, DEPLOY lock (loader.runlock), sqlobjects.deploy per schema, report;
                         refuses "run make sim first" when a file names {SIM_SCHEMA} and the schema is missing
analyses/live_views.py   renders sql/api_live/010_game_preview.sql from vs/catalog/catalog.json (unit-checked, committed)
analyses/docs.py         make api-docs over model/dictionary.py (title and intro become parameters)
analyses/whatif.py       slice B adapter: Condition(game_id, home_wins), MAX_CONDITIONS 16, ConditionError, METHOD,
                         what_if_odds_sql(run_id, conditions, games: Mapping[int, int], *, api_schema="CFB_API")
scripts/api.py           deploy [--lint-only] | docs        (Makefile: api, api-docs)
scripts/grants.py        + roles CFB_APP / CFB_MCP, prefix-aware, idempotent, skip a missing schema with a note
vs/host/namespace.py     + api_schema, api_live_schema, app_role, mcp_role   (and Phase 4's sim_schema, identical text)
vs/host/metaddl.py       + {API_SCHEMA}, {API_LIVE_SCHEMA}                   (and Phase 4's {SIM_SCHEMA}, identical text)
vs/host/sqlobjects.py    Phase 4's 74af62c text (schema_placeholder, keep), unchanged otherwise
```

**Exasol facts relied on**

| Claim | Evidence |
|---|---|
| A user with `SELECT` on a view reads it without rights on its sources (owner's rights) | Phase 6 probe S9 (a), 2026-10-22/23 |
| `GRANT SELECT ON SCHEMA s TO r` covers views created or replaced later | syntax: `exasol-grammar.md` `grant_objprivs_statement_1` (`"ON" ["SCHEMA" | …] object`); the coverage of later views is asserted by the roles IT after a redeploy |
| A literal filter on a view column that is a base-table column is pushed into the view | standard view merging; measured per point-grain view in task 1.5 (open question N2) |
| `RANK()`, `ROW_NUMBER()` with `PARTITION BY`; correlated scalar subqueries | `exasol-grammar-functions.md`, Phase 3's views |
| `CREATE OR REPLACE FORCE VIEW v (c COMMENT IS '…', …) AS … COMMENT IS '…'` | Phase 3 (all model views) |
| A view over a virtual table pushes a literal filter on an unrenamed `P_` column to the adapter | foundation probe 1.4e (subqueries and expressions on `P_` columns are not pushed); verified by `api/on-demand`'s IT (open question N1) |
| Reserved words | every planned view and column name checked against `vs/gen/exasol_reserved_keywords.txt`: none is reserved |

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Light, demo-first replan in two slices; heavy tasks superseded in `tasks.md` ([41]) | Implement the reviewed heavy plan | Owner's light process; Phase 4's replan took the computing half |
| `CFB_API` is thin views over `CFB_MODEL` and `CFB_SIM`; no `CFB_PRED` ([42]) | Keep `CFB_PRED` with publications and frozen odds | Phase 4 owns freezing, grading and the shown run ([51] there); a second schema would be a second definition |
| Spec domain `api/`; code stays in `analyses/` ([43]) | Rename the package | Phases 6 and 7 import `analyses/whatif.py` |
| Final names in slice A, engine columns NULL until slice B ([44]) | Separate "lite" views, renamed later | P6-UI-d; no contract churn |
| A `CFB_API` twin for each snapshot statement, `TEAM_GAMES` added ([45]) | Only the four chat pass-throughs; routes build team-side rows from `GAMES` | Phase 6's working SQL maps one to one |
| Point-grain views on `AS_OF_POINT` and derived tables, filtered with literals; AP rank by the label rule ([46], [47]) | Season grain "as stored now"; `RANKING_AS_OF` joins | The as-of picker stays honest; no as-of view inside a serving view |
| Upsets from stored lines first, from `GAME_WIN_PROB` since 1978 in slice B; ranks on the stored magnitude, ties shared ([48]) | Wait for the engine; recompute an unrounded Elo magnitude | Upsets work today; one probability definition (Phase 4's) |
| Head-to-head from stored games; next meeting from the schedule, prediction FROZEN else CURRENT, stakes from the guide ([49]) | `/teams/matchup`; a `GAME_SCORE_AS_OF` view | Owner decision U3; Phase 4's numbers only |
| No conditional rooting guide ([50]) | One-pass guide over the coming week with a probe | Not on the demo path; S4's unmet 2.2 s stops mattering |
| What-if through a thin adapter over `engine/whatif.py`, static views ([51]) | Own builder; generated views with a run guard | Layout A makes the views static and the run guard unnecessary |
| `CURRENT_POINT` keeps `PUBLICATION_ID` and `WHATIF_PUBLICATION_ID` as aliases of run ids ([52]) | Rename to run ids only | Phase 6's types and Phase 7's tools already use them |
| No view over `GAME_SCORE_AS_OF` ([53]) | A "current predictions" view | An as-of view inside a serving view is the 21 s trap; every point with a run has frozen predictions |
| `CFB_API_LIVE` kept, `P_GAME_ID` unrenamed, `REQ_ROWS` passed through, zero live calls here; refresh timeline views kept ([54]) | Rename to `GAME_ID`; spend 3 ledgered calls | No pushdown question; the column already exists; Phase 6 measures the preview under its own cap |
| Tests: a few ITs per feature on Phase 3's league and Phase 4's runs, sources as oracle ([55]) | Host oracles, history variants, a sim league | 26 scenarios cover the demo path |
| Slice A brings Phase 4's generalized `sqlobjects.py` as identical text ([56]) | Wait for Phase 4's merge; generalize again | Slice A must not wait for Phase 4; identical hunks merge cleanly |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| api/serving-contract | NEW | `api/serving-contract/spec.md` |
| api/roles | NEW | `api/roles/spec.md` |
| api/teams-and-weeks | NEW | `api/teams-and-weeks/spec.md` |
| api/league-stats | NEW | `api/league-stats/spec.md` |
| api/head-to-head | NEW | `api/head-to-head/spec.md` |
| api/upsets | NEW | `api/upsets/spec.md` |
| api/on-demand | NEW | `api/on-demand/spec.md` |
| api/simulations | NEW | `api/simulations/spec.md` |
| api/predictions | NEW | `api/predictions/spec.md` |

The heavy plan's nine `analyses/*` deltas are removed (Dead Code Removal): predictions, scorecard, published odds, what-if and the rooting guide are Phase 4's `sim-engine/*` features; upsets and head-to-head became `api/upsets` and `api/head-to-head`; deploy is part of `api/serving-contract`; forced re-sims are dropped. No recorded spec changes. Recorder notes: `model/*` is read only; the foundation's `make grants` gains two roles (`api/roles`).

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls | No `CFB_API` view references the virtual schema (lint + `EXA_ALL_DEPENDENCIES`); only `CFB_API_LIVE.GAME_PREVIEW` can call CFBD, and this plan never queries it live. `make quota TARGET=aws` before and after each AWS task: used calls unchanged. Integration tests use the mock and replay only |
| Shared databases | Local DB work only in "local free" windows, `make test-it` under `flock <scratchpad>/cfb-it.lock`. AWS `default`: deploy and run only |
| Layering | `CFB_API` reads `CFB_API`, `CFB_MODEL`, `CFB_SIM` and three `CFB_META` logs; `CFB_API_LIVE` reads `GAMES_PREVIEW` and `CFB_META.QUOTA`; never `CFB_CORE` |
| Read-only consumers | `CFB_APP`: `SELECT ON SCHEMA CFB_API`, `CFB_API_LIVE`; `CFB_MCP`: `SELECT ON SCHEMA CFB_API`; nothing else |
| Self-description | Every view and column commented, grain first; `docs/api-dictionary.md` generated and diff-checked |
| Performance | Each page query at one literal point under 1 s on AWS; recorded once in 1.5 and 2.3, a miss logged with a decision, not a gate |
| Data in git | Synthetic fixtures and recordings only; `docs/api-verification.md` holds derived numbers only |
| Ownership | `sys` owns `CFB_API` and `CFB_API_LIVE` on the real deployment, `CFB_TEST_OWNER` in `TEST_` |

## Dependencies

- **Phase 3 (met):** recorded on `main`; `CFB_MODEL` deployed on local and AWS.
- **Phase 2 (met):** run lock kind `DEPLOY` (`loader/runlock.py`), the IT harness, the mock and replay.
- **Phase 4, slice A:** only the text of commit 74af62c for `vs/host/sqlobjects.py`, `vs/host/namespace.py` and `vs/host/metaddl.py` (task 1.1 copies it; no runtime dependency).
- **Phase 4, slice B:** tasks 1.4 (`SIMULATE`, `engine/whatif.py`), 2.1 (predictions, `GAME_WIN_PROB`) and 2.2 (`POINT_RUN`, `CURRENT_RUN`, `ODDS_MOVEMENT`, `ROOTING_GUIDE`) merged into this branch before 2.1 and 2.2; its 2.3 (AWS runs and the hand-off column check) before this plan's 2.3. Phase 4 slice 3 adds `sim` to `make deploy`; the merge puts `api` after it.
- **Owner:** none. U3 is confirmed. Users for the app and the chat are created by the owner when Phases 6 and 7 deploy (`docs/user-guide.md`).

## Cross-plan hand-offs

**Phase 6, task 9.7 (thin-slice routes; gate: 1.1, 1.2 and 1.5 deployed).** Each route statement replaces a snapshot statement of `scripts/app_fixtures.py` with the same shape over `CFB_API` (filters are literals read from `CURRENT_POINT` or the request):

| Route / panel | View | Filter | Replaces snapshot statement |
|---|---|---|---|
| `/api/points` | `CURRENT_POINT`; `AS_OF_POINTS` | `AS_OF_SEASON = :season`; seasons: `SELECT DISTINCT AS_OF_SEASON` | `weeks` (`SEASON_WEEK`) |
| `/api/teams` (header search) | `TEAMS` | `IS_FBS` | `map` (team part) |
| `/api/teams/map` | `TEAMS` (`TEAM_ID`, `SCHOOL`, `MASCOT`, `ABBREVIATION`, `CURRENT_CONFERENCE`, `COLOR`, `ALTERNATE_COLOR`, `LATITUDE`, `LONGITUDE`); `TEAM_GAMES` for the record at P; `RANKINGS` for AP/CFP at P | `IS_FBS`; `SEASON = s AND WEEK_SEQ <= p AND HAS_RESULT`; latest `VISIBLE_AT_WEEK_SEQ <= p` of `POLL_CODE IN ('AP', 'CFP')` | `map` |
| `/api/team/{id}` hero, schedule | `TEAM_GAMES` (+ `TEAMS` for opponent marks) | `TEAM_ID = id AND SEASON = s` | `games` |
| `/api/team/{id}` polls | `RANKINGS` | `SEASON = s AND POLL_CODE IN ('AP', 'CFP') AND TEAM_ID IS NOT NULL` | `ranks` |
| `/api/team/{id}` SP+ | `SP_RATINGS` | `SEASON BETWEEN s - 1 AND s AND TEAM_ID = id` | `sp` |
| `/api/league/week` | `WEEK_GAMES` (+ `TEAMS` marks; `HOME_AP_RANK`, `AWAY_AP_RANK` included) | `AS_OF_SEASON = s AND AS_OF_WEEK_SEQ = p AND IS_FBS_GAME AND NOT IS_COMPLETED` | `week_games` |
| `/api/league/stats` road warriors | `ROAD_WARRIORS` | point; `ORDER BY TRAVEL_RANK LIMIT 5` | `road_warriors` |
| movers | `SP_MOVERS` | `SEASON = s AND (RISE_RANK <= 5 OR FALL_RANK <= 5)` | `movers` |
| conference strength | `CONFERENCE_STRENGTH` | point; `ORDER BY STRENGTH_RANK` | `conference_strength` |
| streaks | `WIN_STREAKS` | point; `STREAK_KIND = 'W' ORDER BY WIN_STREAK_RANK LIMIT 10` | `streaks` |

Notes for 9.7: `TEAMS` keeps the model's names `CURRENT_CONFERENCE` and `CURRENT_CLASSIFICATION` (the backend maps them to `conference`); it adds `LOGO_URL` and `LOGO_DARK_URL` (links only, the app keeps its cache). The stats cards' SQL is now a short query over a view; the inspector can show the view's text from `EXA_ALL_VIEWS` if the `GREAT_CIRCLE_KM` call should stay visible.

**Phase 6, task 9.8 (outlook; gate: 2.1, 2.2 and 2.3) and the built league tabs (3.1–3.5):**

| Route / panel | View | Filter / mapping |
|---|---|---|
| outlook `run_id` | `CURRENT_POINT.WHATIF_RUN_ID` | NULL → every panel `available: false`, reason `no_run` |
| odds trajectory, current, move | `PLAYOFF_ODDS` | `TEAM_ID = id AND AS_OF_SEASON = s AND AS_OF_WEEK_SEQ <= p ORDER BY AS_OF_WEEK_SEQ`; `MOVE`, `DELTA_P_IN_CFP` of the point's row |
| run banner | `PUBLISHED_RUNS` | the point; `publication_id` = `RUN_ID`, `published_at` = `ENDED_AT` |
| this week's game | `WEEK_GAMES` | the point, the team on either side; `PREDICTION_KIND` FROZEN only (no CURRENT kind, [53]); none → `available: false` |
| games to watch | `ROOTING_GUIDE` | `RUN_ID = run AND TEAM_ID = id`: top `GUIDE_RANK <= 5`, coming week `IS_NEXT_WEEK` by `NEXT_WEEK_RANK`; `conditional` always FALSE |
| rivalries | `MATCHUP_SERIES` | `TEAM_ID = id ORDER BY MEETINGS DESC LIMIT 5` |
| league week: our pick, stakes | `WEEK_GAMES` | `PICK_TEAM_ID`, `MODEL_HOME_WIN_PROB`, `HOME_POINTS_PRED`, `AWAY_POINTS_PRED`, `STAKES`, `RUN_ID` |
| odds tab | `PLAYOFF_ODDS`, `PUBLISHED_RUNS`, `ANALYSIS_SETTINGS` | the point |
| what-if | `WHATIF_RUN`, `WHATIF_GAMES`; `analyses/whatif.py` `what_if_odds_sql(run_id, conditions, games)` with `games` = `{GAME_ID: PATH_POS}` of `WHATIF_GAMES`; base odds from `PLAYOFF_ODDS` of the same run; `low_sample` against `MIN_EFFECTIVE_SAMPLE` | the what-if run |
| predictions tab | `PREDICTIONS`, `SCORECARD_WEEKS`, `SCORECARD_SEASONS`, `SCORECARD_CALIBRATION` | headline = `IS_HEADLINE` row; none → the `ALL` row labeled reconstructed |
| upsets tab | `UPSETS` | `SEASON = s AND WEEK_SEQ = p`; `ALL_TIME_RANK <= 25`; `LINE_ERA_RANK <= 25` |
| head-to-head | `TEAMS`, `MATCHUP_SERIES`, `MATCHUP_GAMES`, `MATCHUP_NEXT`; preview `CFB_API_LIVE.GAME_PREVIEW WHERE P_GAME_ID = <MATCHUP_NEXT.GAME_ID, else MATCHUP_SERIES.LAST_GAME_ID>` | the ordered pair |
| refresh timeline, inspector | `REFRESH_TIMELINE`, `FETCHES`, `CURRENT_POINT` | unchanged |

Contract changes against the reviewed Phase 5 contract that Phase 6 must absorb:
- No generated what-if views and no run guard: `WHATIF_OUTCOMES` holds one `OUTCOMES` string per season for every done run; a what-if names its run and stays right while the run is stored. The 409 `whatif_run_changed` path is needed only when `CURRENT_POINT.WHATIF_RUN_ID` changed between the page load and the click. `WHATIF_VIEWS_CURRENT` is TRUE whenever a what-if run exists.
- The conditional rooting guide (`POST /api/whatif/guide`, stretch #8) is dropped; `rooting_guide_sql` does not exist.
- `MATCHUP_NEXT.PREDICTION_KIND` CURRENT has NULL predicted points: `MatchupNext.pred_team_points` and `pred_opponent_points` become nullable.
- `IS_HEADLINE` is TRUE only on `IN_ADVANCE` rows; `SCORECARD_SEASONS` is the season to date as stored now, not one row per point.
- `UPSETS` has no `RANK_MAGNITUDE`; equal magnitudes share a rank. Before slice B its window starts in 2013 and `MODEL_WINNER_WIN_PROB` is NULL.
- `FORCED_RUN_ODDS` and `CFB_PRED` do not exist.
- P6-a: `GAME_PREVIEW` already carries `REQ_ROWS` from the virtual table; the filter column is `P_GAME_ID` (not `GAME_ID`).
- P6-b and the harness: Phase 5's harness provides `deploy_api()`, the users `CFB_TEST_APP` (role `TEST_CFB_APP`) and `CFB_TEST_MCP` (role `TEST_CFB_MCP`) with their passwords in process memory and accessors. `seed_analyses_history`, `complete_week`, `publish`, `snapshot_restore_at` and `load-plan.analyses.yaml` do not exist: runs come from Phase 4's `deploy_sim` and `EXECUTE SCRIPT CFB_INFRA.SIMULATE(…)`, and Phase 6's plan variant builds on Phase 4's steps 200/300.

**Phase 7 (chat, role `CFB_MCP`):**
- Reads every `CFB_API` view, never `CFB_API_LIVE` (grant fact, `api/roles`). P7-f: `CFB_API.GAMES` carries `NOTES`.
- `analyses/whatif.py` keeps `what_if_odds_sql(run_id, conditions, games)` with `Condition(game_id, home_wins)` and `METHOD`; `rooting_guide_sql` is dropped (its `rooting_guide` tool reads `ROOTING_GUIDE`, as planned).
- `WHATIF_OUTCOMES` has one `OUTCOMES` string column, not one column per game: the result-cap scenario of `chat/data-tools` needs another wide view (`GAMES`, about 50 columns). The "what-if run changed" error is needed only when `WHATIF_RUN_ID` changed between lookup and query.
- RQ07: `IS_HEADLINE` marks only `IN_ADVANCE` rows; a reconstructed week answers from its `ALL` row with the label.
- Test users: Phase 5's harness creates `CFB_TEST_MCP`; Phase 7 may keep its own `CFB_TEST_CHAT`.

**Phase 4:** no new request. Its task 2.3 checks the hand-off columns that this plan's 2.1 and 2.2 read; a name that differs keeps this plan's serving name through an alias (open question N3).

**Phase 8:** `make deploy` includes `make api`; after a fresh deploy or a restore run `make grants`; Phase 5 needs no restore step (nothing is published or frozen here).

## Migration

No data migration: `CFB_API` and `CFB_API_LIVE` are new and hold only views.

| Current | New |
|---------|-----|
| `make deploy`: VS → loader → model (→ sim, Phase 4 slice 3) | … → api |
| `make grants`: role `CFB_READER` | + `CFB_APP` and `CFB_MCP`, prefix-aware |
| `vs/host/namespace.py`, `metaddl.py`, `sqlobjects.py` | Phase 4's 74af62c text + `api_schema`, `api_live_schema`, `app_role`, `mcp_role`, `{API_SCHEMA}`, `{API_LIVE_SCHEMA}` |
| `tests/it/harness.py` | `api` and `api_live` names, `deploy_api()`, users `CFB_TEST_APP` / `CFB_TEST_MCP`, `drop_namespace`/`leftovers` cover the schemas, roles and users |
| `tests/mock_cfbd/` | synthetic `/games/{id}/preview` bodies |
| `Makefile` | targets `api`, `api-docs` |
| `pyproject.toml` | mypy `files` + `analyses` |

## Implementation Tasks

Light process: tests first in each task, a few per feature on the demo path (Scenario Coverage); integration tests for SQL, unit tests for pure Python. Every DB task holds the IT lock locally and makes zero CFBD calls.

### Slice A: the serving layer without the engine (now)

- [ ] 1.1 Serving deploy, roles and the thin slice for the map and the team page (P6-UI-b, P6-UI-d)
  - **Files:** `vs/host/sqlobjects.py`, `vs/host/namespace.py`, `vs/host/metaddl.py` (Phase 4's 74af62c text, then the API names); `analyses/__init__.py`, `analyses/deploy.py` (both directories wired, `sql/api_live/` still empty), `scripts/api.py`, `scripts/deploy.py` (api after the model), `scripts/grants.py`, `Makefile` (`api`), `pyproject.toml`; `sql/api/010_current_point.sql`, `020_as_of_points.sql`, `030_teams.sql`, `040_games.sql`, `041_team_games.sql`, `042_team_seasons.sql`, `043_rankings.sql`, `044_sp_ratings.sql`; `tests/it/harness.py`; `tests/unit/test_api_sql.py` (both directories lint clean, the allowlists, no `{CORE_SCHEMA}`), `tests/unit/test_grants.py`, `tests/unit/test_namespace.py`, `tests/unit/test_meta_ddl.py`; `tests/it/test_api_contract_it.py`, `tests/it/test_api_roles_it.py`, `tests/it/test_api_teams_it.py`.
  - **Tests:** serving-contract 1–3, roles 1, teams-and-weeks 1–2 (IT); unit: lint and grant statements.
  - **Done:** tests green, `make lint` clean; in a local free window `make api` twice (the second changes nothing) and `make grants`; `SELECT * FROM CFB_API.CURRENT_POINT` on local shows the current point with NULL run columns.
- [ ] 1.2 Travel, this week and the stats tab (P6-UI-c)
  - **Files:** `sql/api/050_team_travel.sql`, `060_week_games.sql`, `100_road_warriors.sql`, `110_sp_movers.sql`, `120_conference_strength.sql`, `130_win_streaks.sql`; `tests/it/test_api_teams_it.py` (two more tests), `tests/it/test_api_league_it.py`.
  - **Rules:** start from the matching statements of Phase 6's `scripts/app_fixtures.py` (`week_games`, `road_warriors`, `movers`, `conference_strength`, `streaks`), turned from literal points into point columns joined to `AS_OF_POINT`; no as-of view, no `CURRENT_AS_OF`.
  - **Tests:** teams-and-weeks 3–4, league-stats 1–2 (IT).
  - **Done:** tests green; on local, each point-grain view filtered at the current point answers in under 1 s, and its numbers equal the snapshot's for the same point (`make app-fixtures` output, when present; 3 teams spot-checked).
- [ ] 1.3 Head-to-head and upsets from stored games
  - **Files:** `sql/api/200_matchup_series.sql`, `210_matchup_games.sql`, `220_matchup_next.sql`, `300_upsets.sql`; `tests/it/test_api_matchups_it.py`.
  - **Tests:** head-to-head 1–3, upsets 1 (IT). Where the league lacks a fact (equal upset magnitudes, an upcoming 2026 meeting), the test stores an extra fetch with `tests/it/mirror_edit.py` and removes it again (open question N4).
  - **Done:** tests green; on local, Michigan–Ohio State through `MATCHUP_SERIES` and `MATCHUP_GAMES` in under 1 s, and the 2025 upsets ranked.
- [ ] 1.4 Live preview, quota and the refresh timeline
  - **Files:** `analyses/live_views.py`, `sql/api_live/010_game_preview.sql` (rendered), `020_quota.sql`, `sql/api/400_refresh_timeline.sql`, `410_fetches.sql`, `analyses/deploy.py` (the `sql/api_live/` allowlist; the compile pass of N1), `tests/mock_cfbd/` (preview bodies from `tests/synthetic/fake.py`), `tests/unit/test_api_live_view.py` (the committed file equals the render), `tests/unit/test_api_sql.py`, `tests/it/test_api_live_it.py`, `tests/it/test_api_roles_it.py` (chat role).
  - **Tests:** on-demand 1–3, serving-contract 4, roles 2 (IT); unit: the render.
  - **Done:** tests green with the mock and replay only; the deploy makes no request (N1).
- [ ] 1.5 Slice A on AWS and local, the dictionary and the docs
  - **Steps:** `make quota TARGET=aws`; `make api TARGET=aws` and `make grants TARGET=aws`; the same on local in a local free window; time each slice-A page query at the current point (5 runs, query cache off, median) on AWS; `make api-docs`; `make quota TARGET=aws` again (used calls unchanged).
  - **Files:** `analyses/docs.py`, `model/dictionary.py` (title and intro as parameters), `scripts/api.py` (`docs`), `Makefile` (`api-docs`), `tests/unit/test_api_docs.py`, `docs/api-dictionary.md`, `docs/api-verification.md` (derived numbers only), `docs/user-guide.md` § "Serving API" (`make api`, the roles, creating the app and chat users with a typed password, the point rule, zero calls), `CLAUDE.md` (dense bullets: domain `api/`, code `analyses/`, `make api`, the roles, the layering; re-read first, other agents edit it).
  - **Done:** Phase 6's 9.7 gate is met on AWS and local (the orchestrator relays it); a miss of the 1 s target is logged with a decision.

### Slice B: the engine's numbers (gate: Phase 4 1.4, 2.1 and 2.2 merged into this branch)

- [ ] 2.1 Odds, runs, what-if and the rooting guide
  - **Files:** `sql/api/010_current_point.sql`, `020_as_of_points.sql` (run columns), `500_analysis_settings.sql`, `510_playoff_odds.sql`, `520_published_runs.sql`, `530_whatif_run.sql`, `540_whatif_games.sql`, `550_whatif_outcomes.sql`, `560_whatif_playoff.sql`, `570_whatif_teams.sql`, `580_rooting_guide.sql`; `analyses/deploy.py` (refuse without `CFB_SIM`); `analyses/whatif.py`; `tests/it/harness.py` (`deploy_api()` deploys Phase 4's simulation schema first once a file reads it, so the slice-A tests keep running with no done run); `tests/unit/test_api_whatif.py`; `tests/it/test_api_sim_it.py`.
  - **Tests:** simulations 1–3 (IT); unit: the adapter maps game ids to positions, refuses an unknown or repeated game, 17 conditions and `bool` ids, and its SQL names only `CFB_API` objects.
  - **Done:** tests green; on local after `make sim` and one `make simulate`, `make api` and a what-if for one team through the adapter equal Phase 4's `make what-if` for the same run and condition.
- [ ] 2.2 Predictions, the scorecard, upsets since 1978 and the next meeting
  - **Files:** `sql/api/600_predictions.sql`, `610_scorecard_weeks.sql`, `620_scorecard_seasons.sql`, `630_scorecard_calibration.sql`, `060_week_games.sql`, `210_matchup_games.sql`, `220_matchup_next.sql`, `300_upsets.sql` (engine columns and sources); `tests/it/test_api_predictions_it.py`.
  - **Tests:** predictions 1–2, head-to-head 4, upsets 2 (IT).
  - **Done:** tests green; the slice-A tests of the same views still pass with the simulation schema present.
- [ ] 2.3 Slice B on AWS, the hand-off check and the docs (gate: Phase 4 2.3)
  - **Steps:** `make quota TARGET=aws`; `make api TARGET=aws`; compare the columns of every slice-B view (`EXA_ALL_COLUMNS`) with § Cross-plan hand-offs and the specs; time `PLAYOFF_ODDS` at the current point, a what-if at k = 1 and k = 3 through the adapter as an app-role user, `WEEK_GAMES`, `MATCHUP_NEXT` and the `UPSETS` week and all-time top 20 (5 runs, median); look at the all-time top 20 for plausibility (Q8: not dominated by one era or by ties); `make api-docs`; `make quota TARGET=aws` again.
  - **Files:** `docs/api-verification.md`, `docs/api-dictionary.md`, `docs/user-guide.md` (append: odds, what-if, guide, predictions), `specs/architecture.md` (§ Layers and schemas: `CFB_API` over `CFB_MODEL` and `CFB_SIM`, no `CFB_PRED`; § Security model: `CFB_APP`, `CFB_MCP`), `docs/roadmap.md` (Phase 5 bullets, the D13 head-to-head wording per U3, ADR numbers for the decisions marked `Promotes to ADR: yes`), `CLAUDE.md` (re-read each first).
  - **Done:** the orchestrator can relay the 9.8 gate to Phase 6 and the contract to Phase 7; zero CFBD calls.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| A-1 | 1.1 |
| A-2 | 1.2, 1.3, 1.4 |
| A-3 | 1.5 |
| B-1 (gate: Phase 4 1.4, 2.1, 2.2 merged) | 2.1, 2.2 |
| B-2 (gate: Phase 4 2.3) | 2.3 |

Sequential dependencies:
- A-1 → A-2 → A-3. 1.1 wires the deployer, the harness and both directories, so A-2 touches disjoint files: 1.2 `sql/api/050`–`130` and `test_api_teams_it.py` + `test_api_league_it.py`; 1.3 `sql/api/200`–`300` and `test_api_matchups_it.py`; 1.4 `sql/api_live/`, `sql/api/400`–`410`, `analyses/deploy.py`, `analyses/live_views.py`, the mock, `test_api_live_it.py`, `test_api_sql.py` and the chat-role test in `test_api_roles_it.py`.
- B-1 needs A-3 and the Phase 4 merge. 2.1 and 2.2 touch disjoint files: 2.1 `010`, `020`, `500`–`580`, `analyses/deploy.py`, `analyses/whatif.py`, `tests/it/harness.py`; 2.2 `060`, `210`, `220`, `300`, `600`–`630`. Both use Phase 4's harness helpers for runs; only 2.1 edits the harness.
- B-1 → B-2.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| Spec deltas | `analyses/predictions`, `scorecard`, `upsets`, `head-to-head`, `published-odds`, `what-if`, `rooting-guide`, `forced-resim`, `deploy` (this plan) | Superseded by the light replan: Phase 4 owns the computing features; upsets and head-to-head moved to `api/`; forced re-sims dropped |
| — | code | Nothing of the heavy plan was implemented; this plan adds code only |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| serving-contract: One command installs the serving views, each described by its grain | Integration | `tests/it/test_api_contract_it.py` | `test_make_api_installs_commented_views` |
| serving-contract: No serving view reads the raw mirrors or the virtual schema | Integration | `tests/it/test_api_contract_it.py` | `test_api_dependencies_allowed` |
| serving-contract: Before the first simulation the pages still have a current point | Integration | `tests/it/test_api_contract_it.py` | `test_current_point_before_engine` |
| serving-contract: The refresh timeline shows every step of a weekly refresh | Integration | `tests/it/test_api_live_it.py` | `test_refresh_timeline_and_fetches` |
| roles: The app role reads the serving views and nothing else | Integration | `tests/it/test_api_roles_it.py` | `test_app_role_reads_only_serving_views` |
| roles: The chat role reads the serving views but cannot reach CFBD | Integration | `tests/it/test_api_roles_it.py` | `test_mcp_role_cannot_reach_cfbd` |
| teams-and-weeks: A team carries its marks and its home | Integration | `tests/it/test_api_teams_it.py` | `test_team_marks_and_home` |
| teams-and-weeks: The model views are served unchanged | Integration | `tests/it/test_api_teams_it.py` | `test_pass_through_views_equal_model` |
| teams-and-weeks: Travel is great-circle kilometres from the home venue | Integration | `tests/it/test_api_teams_it.py` | `test_travel_great_circle_km` |
| teams-and-weeks: This week lists the next week's games at a point | Integration | `tests/it/test_api_teams_it.py` | `test_week_games_at_point` |
| league-stats: The season cards add up the games up to the point | Integration | `tests/it/test_api_league_it.py` | `test_season_cards_add_up_to_point` |
| league-stats: A streak counts the latest run of equal results across seasons | Integration | `tests/it/test_api_league_it.py` | `test_streak_across_seasons` |
| head-to-head: The all-time series comes from stored games without a call | Integration | `tests/it/test_api_matchups_it.py` | `test_series_from_stored_games` |
| head-to-head: Teams that never met have no series | Integration | `tests/it/test_api_matchups_it.py` | `test_never_met_no_series` |
| head-to-head: The next meeting is listed from the schedule before any run | Integration | `tests/it/test_api_matchups_it.py` | `test_next_meeting_from_schedule` |
| head-to-head: With a run, the next meeting carries our prediction and the stakes | Integration | `tests/it/test_api_predictions_it.py` | `test_next_meeting_prediction_and_stakes` |
| upsets: Upsets are ranked within their week, their season and the line era | Integration | `tests/it/test_api_matchups_it.py` | `test_upsets_line_era_ranks` |
| upsets: With the engine, upsets since 1978 include the Elo era and our own probability | Integration | `tests/it/test_api_predictions_it.py` | `test_upsets_since_1978` |
| on-demand: One query fetches one live preview | Integration | `tests/it/test_api_live_it.py` | `test_one_query_one_preview` |
| on-demand: A preview query without a literal game id is refused before any call | Integration | `tests/it/test_api_live_it.py` | `test_missing_game_id_refused` |
| on-demand: Replay serves a recorded preview without a call | Integration | `tests/it/test_api_live_it.py` | `test_replay_preview_zero_calls` |
| simulations: The odds and the guide of every shown run are served | Integration | `tests/it/test_api_sim_it.py` | `test_odds_runs_guide_served` |
| simulations: The current point names the run the pages and the what-if use | Integration | `tests/it/test_api_sim_it.py` | `test_current_point_names_runs` |
| simulations: A what-if over the serving views equals the engine's own | Integration | `tests/it/test_api_sim_it.py` | `test_whatif_equals_engine` |
| predictions: Frozen predictions are served with the schools, the grades and the stakes | Integration | `tests/it/test_api_predictions_it.py` | `test_predictions_and_week_games` |
| predictions: The scorecard separates the weeks from the season to date | Integration | `tests/it/test_api_predictions_it.py` | `test_scorecard_split` |

26 scenarios, each mapped to one integration test. Supporting unit tests: `tests/unit/test_api_sql.py` (lint, allowlists), `test_grants.py` (role statements), `test_namespace.py` and `test_meta_ddl.py` (names, placeholders), `test_api_live_view.py` (the rendered preview view), `test_api_docs.py` (the committed dictionary equals the generated one), `test_api_whatif.py` (the adapter).

### Manual Testing

All commands from the repo root on local (`TARGET=local`, local free window) after `make deploy` and `make grants`, unless a row names AWS. None spends a CFBD call.

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| api/serving-contract | `make api && make api`; `SELECT COUNT(*) FROM EXA_ALL_VIEWS WHERE VIEW_SCHEMA = 'CFB_API'`; `SELECT * FROM CFB_API.CURRENT_POINT` | The second run reports no change; 20 views before slice B, 33 after; one row, the current point, run columns NULL before slice B |
| api/roles | As the owner: `CREATE USER CFB_MCP_DEMO IDENTIFIED BY <typed in a SQL client>`, `GRANT CREATE SESSION, CFB_MCP TO CFB_MCP_DEMO`; as that user: `SELECT COUNT(*) FROM CFB_API.TEAMS`, `SELECT COUNT(*) FROM CFB_MODEL.TEAM`, `SELECT * FROM CFB_API_LIVE.QUOTA`; then `DROP USER CFB_MCP_DEMO` | The first answers, the other two fail with insufficient privileges |
| api/teams-and-weeks | `SELECT SCHOOL, CURRENT_CONFERENCE, COLOR, LATITUDE, LONGITUDE, HOME_CITY FROM CFB_API.TEAMS WHERE IS_FBS ORDER BY SCHOOL LIMIT 5`; `SELECT HOME_TEAM, AWAY_TEAM, HOME_AP_RANK, AWAY_AP_RANK, PREGAME_SPREAD FROM CFB_API.WEEK_GAMES WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = <current> ORDER BY START_AT` | FBS teams with colors and coordinates; next week's slate with ranks and lines |
| api/league-stats | `SELECT SCHOOL, TRIPS, KM_TRAVELED, LONGEST_TRIP_TO FROM CFB_API.ROAD_WARRIORS WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = <current> ORDER BY TRAVEL_RANK LIMIT 5`; `SELECT SCHOOL, STREAK_LENGTH, SINCE_SEASON FROM CFB_API.WIN_STREAKS WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = <current> AND STREAK_KIND = 'W' ORDER BY WIN_STREAK_RANK LIMIT 10` | The same top 5 and top 10 as Phase 6's real-data snapshot for that point, each in under 1 s |
| api/head-to-head | `SELECT s.* FROM CFB_API.MATCHUP_SERIES s JOIN CFB_API.TEAMS a ON a.TEAM_ID = s.TEAM_ID JOIN CFB_API.TEAMS b ON b.TEAM_ID = s.OPPONENT_TEAM_ID WHERE a.SCHOOL = 'Michigan' AND b.SCHOOL = 'Ohio State'` | The all-time series with first season, streak and biggest results, under 1 s, 0 calls |
| api/upsets | `SELECT SEASON, WEEK, WINNER_SCHOOL, LOSER_SCHOOL, PROB_SOURCE, UPSET_MAGNITUDE, ALL_TIME_RANK, ALL_TIME_TIED FROM CFB_API.UPSETS ORDER BY ALL_TIME_RANK LIMIT 20` | Before slice B: line-era upsets since 2013; after: since 1978 with ELO rows, ties shown by `ALL_TIME_TIED` |
| api/on-demand | Replay: `SELECT GAME_ID, GAME_HOME_TEAM_NAME, GAME_AWAY_TEAM_NAME, ANALYSIS_ODDS_DATA_SPREAD, REQ_SOURCE, REQ_ROWS FROM CFB_API_LIVE.GAME_PREVIEW WHERE P_GAME_ID = <a recorded game>` | One row, `REPLAY`, 0 calls |
| api/simulations | `SELECT SCHOOL, P_IN_CFP, PREV_P_IN_CFP, MOVE FROM CFB_API.PLAYOFF_ODDS WHERE AS_OF_SEASON = 2026 AND AS_OF_WEEK_SEQ = <current> ORDER BY ODDS_RANK LIMIT 15`; `SELECT * FROM CFB_API.WHATIF_RUN`; `SELECT * FROM CFB_API.ROOTING_GUIDE WHERE RUN_ID = <run> AND TEAM_ID = <bubble team> ORDER BY GUIDE_RANK LIMIT 5` | Top 15 with arrows; one what-if run; five games to watch |
| api/predictions | `SELECT WEEK_SEQ, POPULATION, FREEZE_KIND, IS_HEADLINE, RECORD_LABEL, BRIER FROM CFB_API.SCORECARD_WEEKS WHERE SEASON = 2026 ORDER BY WEEK_SEQ DESC, POPULATION, FREEZE_KIND LIMIT 12` | The latest weeks' records; `IS_HEADLINE` only on `IN_ADVANCE` rows |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `make bundle` | Exit 0 |
| API lint | `uv run python -m scripts.api deploy --lint-only` | Exit 0; "lint ok" |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`analyses` included) |
| Format | `uv run ruff format --check .` | No changes |
| Deploy | `make deploy && make grants`; `make api TARGET=aws && make grants TARGET=aws` | Exit 0; a second `make api` changes nothing |
| Layering | `git grep -n -i -e 'CFB_CORE' -e '{CORE_SCHEMA}' -- sql/api sql/api_live`; `git grep -n -e '{VS_NAME}' -e 'CFBD_VS' -e 'QUOTA' -- sql/api sql/api_live` | First: no match. Second: only `sql/api_live/010_game_preview.sql` and `020_quota.sql` |
| Dictionary | `make api-docs && git diff --exit-code docs/api-dictionary.md` | Exit 0 |
| Roles | `SELECT GRANTEE, PRIVILEGE, OBJECT_NAME FROM EXA_DBA_OBJ_PRIVS WHERE GRANTEE IN ('CFB_APP', 'CFB_MCP')`; `SELECT GRANTEE, PRIVILEGE FROM EXA_DBA_SYS_PRIVS WHERE GRANTEE IN ('CFB_APP', 'CFB_MCP')` | `CFB_APP`: `SELECT` on `CFB_API` and `CFB_API_LIVE`; `CFB_MCP`: `SELECT` on `CFB_API`. Second: empty |
| Zero calls | `make quota TARGET=aws` before and after 1.5 and 2.3 | `/info` used calls unchanged |
| No data in git | `git ls-files '*.csv.gz' '*.parquet' '*.cfbd.json' 'cache/*'` | Empty |
