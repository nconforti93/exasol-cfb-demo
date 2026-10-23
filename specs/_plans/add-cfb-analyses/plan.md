# Plan: add-cfb-analyses

## Summary

Roadmap Phase 5 ("Analyses"): the analyses behind the v1 pages, and `CFB_API`, the serving schema that the app (Phase 6) and the chat (Phase 7) read and nothing else. Demo beat 4: "pages move" after the live refresh: the scorecard ("the model went 41–9"), the upset of the week ranked all-time, playoff odds with arrows, what-if toggles with a rooting guide on a bubble team, and head-to-head with a live preview fetched on a click.

- **What-if** (`CFB_SIM` data, served by `CFB_API`). A simulation run becomes what the pages show when it is **published**: the refresh publishes its own run in a new step 400 `PUBLISH`, and `make publish` publishes a manual run (the 1M scale dial on AWS).
  - A publication freezes the run's odds per team, so the week-over-week arrows survive dropping old runs.
  - One view, `CFB_PRED.WHATIF_PUBLICATION`, names the what-if run: the latest publication of the newest published point at or before the current point. Whenever it changes, three `CFB_API` views are regenerated over the run's per-run outcome table, with one `BOOLEAN` column per game named by game id. Conditional odds are a filter over those views; the SQL comes from a tested host library, because Exasol has no parameterized views.
  - The rooting guide reads Phase 4's precomputed `LEVERAGE` without toggles (all remaining games). With toggles it is computed over the coming week's games only, which is the design answer to S4's unmet 2.2 s: the cost follows the game columns aggregated, about 120 instead of S4's 800. A probe on AWS at 1M decides it before anything depends on it.
  - Forced re-sims are Phase 4's `SIMULATE_FROM`, an owner action; their odds and full-season guides (labeled as forced) are served next to the base run's.
- **Predictions** (`CFB_PRED`). Step 200 `PREDICT` freezes next week's predicted scores and win probabilities once, before kickoff. Missing weeks are reconstructed at their own as-of point and marked. The scorecard grades predictions made in advance and reconstructions apart, per week, season to date and in calibration buckets, next to the betting line, and its headline uses only the former. Upsets are ranked since 1978, the first Division I-A season: the line era since 2013, Elo before.
- **Head-to-head** comes from the stored games (0 calls, instant), not from `/teams/matchup` (4.9 s end to end in S1). The next meeting carries our prediction and what the game means for both teams' playoff odds. The live beat is CFBD's game preview of the next meeting, or of the latest one, one call per click (0.7 s at CFBD).
- **`CFB_API`**: 26 commented views (3 of them generated per publication), including 4 pass-through views over the model for the chat; no tables or functions, and no view that reaches CFBD. **`CFB_API_LIVE`** holds the 2 views that do (`GAME_PREVIEW`, `QUOTA`). Read-only roles: `CFB_APP` reads both schemas, `CFB_MCP` only `CFB_API`, so the chat cannot spend quota.

Everything is tested at zero CFBD calls on the synthetic sim league in the `TEST_` namespace. Live verification runs on AWS with at most 6 ledgered calls (3 planned).

The plan was revised on 2026-10-22 to resolve the independent review (`review.md`, § Review resolution).

## Design

### Context

The predecessor plans are binding (interview item 1):

- **Phase 4** (`specs/_plans/add-cfb-score-engine/`, revised 2026-10-22). It provides:
  - the run registry `SIM_RUN` (statuses, origins `MANUAL`/`REFRESH`/`FORCED`, `PATH_TABLE`, `LOAD_RUN_ID`);
  - the per-run outcome table `SIM_PATH_R<run>` (layout C, one `BOOLEAN` column `G0001…` per remaining game, or layout A if its probe 1.1c says so, one layout for both targets);
  - sparse `SIM_TEAM` partitioned by run, the precomputed `LEVERAGE`, the `TEAM_WINS`/`CONF_CHAMP` tallies, the roster `SIM_RUN_TEAM`, `SIM_RUN_GAME` (`PATH_COLUMN`, `MU`, `SIGMA`, `FIXED_HOME_WIN`) and the view `SIM_TEAM_ODDS`;
  - `SIMULATE_FROM` (forced re-sim from a base run's snapshot with the same draws) and `DROP_SIM_RUN`;
  - the score model `GAME_SCORE_AS_OF` (μ, σ, total, points, `HOME_WIN_PROB`, leak-free), `MARGIN_WIN_PROB`, `GAME_WIN_PROB` (line probability since 2013, Elo before, `UPSET_MAGNITUDE`);
  - refresh step 300 `SIMULATE`, with **200 reserved for this phase's `PREDICT`**;
  - the retention rule: weekly refresh runs are kept for the season, manual and forced runs are dropped with `make sim-drop`;
  - the run lock kinds `SIMULATE` (script rule) and `DEPLOY` (host rule) through hand-off H1.
- **Phase 3** (`specs/_plans/add-cfb-analytical-model/`):
  - `CFB_MODEL` as views, with as-of points addressed by week sequence (0 preseason, regular n, postseason 100 + n), `AS_OF_POINT`, `CURRENT_AS_OF`, `GAME_AS_OF`, `GAME`, `TEAM_GAME`, `TEAM`, and (for the chat's pass-through views) `TEAM_SEASON`, `RANKING`, `RATING_SP`;
  - the generic SQL-object deployer `vs/host/sqlobjects.py` (lint, one transaction, `FORCE` views, orphans dropped, comments read back) and the dictionary generator;
  - grain-first comments, unquoted upper-case DDL names;
  - decision [18]: head-to-head can come from stored games.
- **Phase 2** (`specs/_plans/add-cfb-persistence-refresh/`, worktree `../exasol-cfb-demo-p2`):
  - `REFRESH_WEEK` with registered steps ≥ 100 called as `(run_id, season, week, season_type)`, step rows committed at start and end;
  - the run lock in `LOAD_RUN`, the Lua bundler, `NOW_OVERRIDE` (test clock);
  - the snapshot scope (`CFB_CORE` + `LOAD_LOG` + `FETCH_LOG`; run history stays per deployment);
  - the IT harness (`deploy_plan`, `set_now`, `refresh`, recordings).
- **Foundation** (`specs/cfbd-vs/`, `specs/cfbd-fetch/`, `specs/decision-log.md`):
  - read-through by exact slice key, required-filter rejection, `MAX_CALLS_PER_QUERY` 10 at plan time, `REQ_*` columns and the `EXPLAIN VIRTUAL` manifest;
  - the subquery-on-`P_` limitation (probe 1.4e) and pushdown with the owner's rights (probe 1.4g);
  - the mock sidecar, `make record --query` under the ledger, and `make grants` (`CFB_READER`).
- **S4** (`docs/spikes/s4-sim-storage.md`, D20), on AWS at 1M (3 nodes):
  - what-if Q1 p95 0.52 s (k = 1), 0.21 s (k = 3), 0.17 s (k = 8); one-team Q2 < 0.2 s; unconditional rooting guide 0.078 s via `LEVERAGE`;
  - **conditional rooting guide (k = 3) 2.2 s p95, unmet**, its cost "in unpivoting the path table (400 game columns for 92k–394k matching sims), not in `SIM_TEAM`"; at 100k locally 0.95 s on C (borderline);
  - Q3 needs the semi-join shape (about 20× faster than a join plus team filter);
  - effective sample at k = 8: median 944 of 100k, 10,079 of 1M; the thin-sample threshold left to Phase 5.
- **S1** (`docs/cfbd-api-measurements.md`):
  - `/teams/matchup` took 4.26 s at CFBD (4.87 s end to end);
  - `/games/{id}/preview` took 0.7 s for one game (48 KB, 103 curated columns plus `RAW_JSON`);
  - the database side of a single-slice lookup is about 0.6 s.

  A preview recording exists locally (`cache/replay/games/401858271/preview`, never committed).

Forces:
- **Exasol has no parameterized views and no table functions in `FROM`** (Phase 3). Conditions of a what-if are column predicates on a per-run table. Either SQL is generated per click (by whom, against which names?), or a script returns rows (`EXECUTE SCRIPT`, which the MCP server's read-only queries and the read-only roles cannot use). Phase 4 already noted: "Per-run table names force generated SQL in Phase 5's `CFB_API`".
- **Apps and the MCP user read only `CFB_API`** (architecture). They cannot see `CFB_SIM.SIM_PATH_R42`, so generated SQL must target `CFB_API` names.
- **Every toggle shrinks the sample**, so every conditional number needs its effective sample beside it, and the arrows need a threshold above sampling noise.
- **The conditional rooting guide misses 1 s** at 1M (S4). The interview asks for a design and a verify step. The options named: top-N precompute, next week only, per-what-if caching.
- **Honesty of grades.** Predictions graded against results must have been made in advance. Model parameters can be recalibrated mid-season, and a deployment can be new or restored.
- **Retention and restore.** A rehearsal restores `CFB_CORE` only. Simulation runs and predictions stay in the database, and old runs may be dropped.
- **Quota and latency.** The October cap is 400 calls; 21 + 217 + ~5 are planned elsewhere. The mission's on-demand target is 2 s; `/teams/matchup` misses it by 2.5×.
- **Read-only consumers.** The backend and the MCP server must not write, start refreshes or simulations, or reach the key. The live preview still needs the virtual schema. The chat must not spend quota on its own.
- **The chat answers questions outside the pages** (demo beat 5), so `CFB_API` needs a few general views over the model besides the page-shaped ones.
- **Generated SQL embeds data.** School names reach DDL text and comments; CFBD's FBS school "Hawai'i" has an apostrophe.
- **Process.** No DB session during planning, zero CFBD calls. `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only by the docs tasks. Local tests run in "local free" windows under the IT lock. AWS: deploy and run only.

**Goals**
- `CFB_PRED` with 1 reference table, 3 data tables and 12 views (Key Interfaces); `CFB_API` with 23 views from files plus 3 generated; `CFB_API_LIVE` with 2 views.
- Five Lua scripts in `CFB_INFRA`: `REFRESH_PREDICT` (step 200), `PREDICT`, `REFRESH_PUBLISH` (step 400), `PUBLISH_RUN` and `WHATIF_VIEWS`.
- Frozen weekly predictions (first freeze wins), reconstruction of gaps at their own point, and `FROZEN_BEFORE_KICKOFF`.
- The scorecard per week, season to date and in calibration buckets, split by freeze kind (in advance, reconstructed, all), with the line on the same games and a headline that never presents a reconstruction as a forecast.
- Upset ranks since `ALL_TIME_FROM_SEASON` (1978), in the line era, the season and the week, on the unrounded magnitude, with our own frozen probability.
- Head-to-head from stored games: series, meetings, streak, next meeting with prediction and playoff stakes.
- Publications with frozen odds, movement with a noise threshold, and the what-if views regenerated atomically for one what-if publication that never lies after the current point.
- `analyses/whatif.py`: conditional odds and the conditional rooting guide (coming week), validated conditions, a run guard, effective-sample and branch flags.
- The forced re-sim results next to their base; the full-season guide of any `DONE` run, forced runs labeled.
- `GAME_PREVIEW` on demand (one call per click) in `CFB_API_LIVE`, with the virtual schema's guards and the inspector columns.
- Pass-through views `GAMES`, `TEAM_SEASONS`, `RANKINGS` and `SP_RATINGS` for the chat.
- Read-only roles `CFB_APP` (both serving schemas) and `CFB_MCP` (`CFB_API` only) through `make grants`.
- One escaping rule for every string a generator writes into SQL, unit-tested on the host.
- `make analyses` (part of `make deploy`, and the restore recipe's last step), `make predict`, `make publish`, `make what-if`, `make api-docs`, `make analyses-perf`.
- A probe that decides the rooting-guide design on AWS at 1M before group B; performance measured locally (100k) and on AWS (1M, real data).

**Non-Goals**
- App code, backend caching, the refresh button and backend fetch logging (Phase 6); chat tools and MCP setup (Phase 7). This plan states the contracts they use.
- Users and passwords for the backend and the MCP server: the owner creates them (documented), the roles come from `make grants`.
- Starting forced re-sims, refreshes or publications from the app or the chat. The roles stay read-only; a separate owner path is Phase 6's decision.
- What-ifs on runs other than the what-if publication's: older points, forced runs, AWS-only runs on the laptop.
- Live CFBD access for the chat: `CFB_MCP` gets no grant on `CFB_API_LIVE`; Phase 7 may add one.
- More pass-through views than the four the chat plausibly needs (as-of team seasons, venues, conferences); "new views may be added" covers them later.
- Conditional conference titles and win totals: Phase 4 stores them as tallies over all seasons.
- Predictions for bowls and playoff games, and predictions or scorecards for past seasons. `CURRENT` is fitted on 2014–2025, and the calibration report covers those seasons.
- `PROMOTE()`: stays deferred (interview item 4). The preview is fetched again on every click unless Phase 6 caches it.
- `/teams/matchup` in any serving view (decision [14]).
- Snapshots of predictions or publications: they are deployment history (decision [10]).
- Upset ranks "as of the time of the game", and ranks per era. One all-time rank (since `ALL_TIME_FROM_SEASON`) plus a line-era rank.
- Changes to `CFB_SIM` objects or `make sim`: Phase 5 adds no object to `CFB_SIM` (decision [18]).
- Benchmark harness and `docs/benchmark.md` (Phase 8).

### Decision

The analyses live in `CFB_PRED`, Phase 5's own schema. Data tables hold what must be frozen (predictions, publications, published odds); views compute everything else from `CFB_MODEL`, `CFB_SIM` and the frozen tables. `CFB_API` is a thin, commented, view-only serving layer over them.

Three refresh steps chain around Phase 4's simulation: 200 `PREDICT` freezes, 300 `SIMULATE` runs, 400 `PUBLISH` publishes. A publication is one transaction: it freezes the run's odds and, when it becomes the what-if publication (`CFB_PRED.WHATIF_PUBLICATION`, never after the current point), regenerates three `CFB_API` views that expose the run's outcome table under stable names. Per-click what-ifs are SQL text built by a host library against those stable names, guarded by the run id, so the app and the chat run them as read-only users. The rooting guide splits: precomputed counts without toggles, a computed coming-week guide with toggles. Head-to-head reads stored games; the only call is the preview, one per click, served from `CFB_API_LIVE`, which only the app role reads.

#### Architecture

```
 CFB_MODEL (Phase 3)  AS_OF_POINT · CURRENT_AS_OF · GAME_AS_OF · GAME · TEAM_GAME · TEAM · TEAM_SEASON · RANKING · RATING_SP
 CFB_SIM   (Phase 4)  GAME_SCORE_AS_OF · GAME_WIN_PROB · MARGIN_WIN_PROB · SIM_RUN · SIM_RUN_GAME · SIM_RUN_TEAM
                      SIM_TEAM · LEVERAGE · SIM_TEAM_ODDS · SIM_PATH_R<run> · SCORE_MODEL_PARAMS
        │
        ▼  CFB_PRED (sql/pred/, deployed by vs/host/sqlobjects.py via analyses/deploy.py)
 ANALYSIS_SETTINGS (seed)          data: PREDICTION · PUBLICATION · PUBLISHED_ODDS   (deployment history, never replaced)
 PREDICTION ─▶ PREDICTION_GRADED (FREEZE_KIND, week played) ─▶ SCORECARD_WEEK · SCORECARD_SEASON · SCORECARD_CALIBRATION
 GAME_WIN_PROB + GAME Elo + PREDICTION ─▶ UPSET (RANK_MAGNITUDE; ALL_TIME_RANK since 1978, LINE_ERA_RANK, SEASON_RANK, WEEK_RANK)
 PUBLICATION ─▶ POINT_PUBLICATION (IS_AFTER_CURRENT) ─▶ ODDS_MOVEMENT (PUBLISHED_ODDS vs previous published point)
 POINT_PUBLICATION + CURRENT_AS_OF + SIM_RUN ─▶ WHATIF_PUBLICATION (≤ 1 row: THE what-if run, read by every consumer)
 LEVERAGE ─▶ ROOTING_GUIDE (every DONE run, fixed games dropped) ;  SIM_TEAM_ODDS ─▶ FORCED_RUN_ODDS
 TEAM_GAME ─▶ MATCHUP_SERIES ;  GAME_AS_OF + GAME_SCORE_AS_OF + PREDICTION + WHATIF_PUBLICATION + ROOTING_GUIDE ─▶ MATCHUP_NEXT
        │
        ▼  CFB_API (sql/api/ 23 views + 3 generated; 0 calls, no network)          read by CFB_APP and CFB_MCP
 CURRENT_POINT AS_OF_POINTS TEAMS │ PLAYOFF_ODDS PUBLISHED_RUNS ROOTING_GUIDE FORCED_RUN_ODDS │ WHATIF_GAMES ANALYSIS_SETTINGS
 PREDICTIONS SCORECARD_WEEKS SCORECARD_SEASONS SCORECARD_CALIBRATION │ UPSETS │ MATCHUP_SERIES MATCHUP_GAMES MATCHUP_NEXT
 REFRESH_TIMELINE FETCHES ──▶ CFB_META.LOAD_RUN / REFRESH_LOG / FETCH_LOG
 pass-through for the chat: GAMES · TEAM_SEASONS · RANKINGS · SP_RATINGS ──▶ CFB_MODEL
 generated: WHATIF_RUN · WHATIF_OUTCOMES (G<game id> BOOLEAN columns over SIM_PATH_R<run>) · WHATIF_PLAYOFF (SIM_TEAM, RUN_ID literal)
        ▼  CFB_API_LIVE (sql/api_live/ 2 views; the only serving views that reach the network)   read by CFB_APP only
 GAME_PREVIEW ──▶ CFBD_VS.GAMES_PREVIEW (1 call per query; required GAME_ID; budget 10; replay)
 QUOTA ──▶ CFB_META.QUOTA (/info, free)
        ▲
        │  analyses/whatif.py (host, pure): what_if_odds_sql(run, conditions, games) · rooting_guide_sql(run, team, conditions, games)
        │  ─▶ SQL over CFB_API only, guarded by the run id ─▶ backend (Phase 6) / app-state tools (Phase 7) / make what-if
 EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')                                         (one lock, Phase 2)
   1 CHECK ─▶ 2..99 FETCH ─▶ 100 MODEL ─▶ 200 PREDICT ─▶ 300 SIMULATE ─▶ 400 PUBLISH   (steps registered by the loader deploy)
   200 PREDICT (analib.lua): current season only; for each point Q ≤ P whose next week has unfrozen games, oldest first:
       INSERT INTO PREDICTION SELECT … FROM GAME_SCORE_AS_OF @Q (CURRENT, HORIZON 1) WHERE NOT EXISTS (frozen); COMMIT
   400 PUBLISH (analib.lua): the refresh's DONE REFRESH run (else "skipped: no simulation run"); one transaction:
       INSERT PUBLICATION ; INSERT PUBLISHED_ODDS SELECT … FROM SIM_TEAM_ODDS WHERE RUN_ID = :run ;
       if WHATIF_PUBLICATION now names it: CREATE OR REPLACE VIEW CFB_API.WHATIF_RUN / WHATIF_OUTCOMES / WHATIF_PLAYOFF ; COMMIT
 make publish RUN= ─▶ CFB_INFRA.PUBLISH_RUN(run)        make predict SEASON= WEEK= ─▶ CFB_INFRA.PREDICT(…)   (lock kind SIMULATE)
 make analyses ─▶ one transaction: CFB_PRED ─▶ empty generated views if missing ─▶ CFB_API ─▶ CFB_API_LIVE ─▶ scripts
               (lock kind DEPLOY) ─▶ commit ─▶ EXECUTE SCRIPT CFB_INFRA.WHATIF_VIEWS()   (also the restore recipe's last step)
 make grants   ─▶ CFB_APP: SELECT ON SCHEMA CFB_API, CFB_API_LIVE ;  CFB_MCP: SELECT ON SCHEMA CFB_API
```

#### Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| Publication as an explicit, atomic act | step 400, `PUBLISH_RUN`, `PUBLICATION` | "Which run do the pages show" has one answer per point. The scale dial (a manual 1M run) and rehearsal re-runs are shown deliberately; backtests and forced runs never leak onto the odds page |
| Freeze what must not change; compute the rest | `PREDICTION`, `PUBLISHED_ODDS` tables; everything else views | Grades and arrows compare with what was shown then, robust to recalibration and to dropped runs; all other numbers stay consistent with the data |
| First freeze wins; gaps reconstructed at their own point and flagged | `REFRESH_PREDICT`, `PREDICT` | Honest grading; a skipped week or a new deployment still gets a complete, leak-free scorecard that says which predictions were made after kickoff |
| One definition per concept | `CFB_PRED.WHATIF_PUBLICATION` | "The what-if run" is decided in one view (newest published point at or before the current point, run still `DONE`) and read by the generator, the publication, `WHATIF_GAMES`, `CURRENT_POINT` and `MATCHUP_NEXT`, so they cannot disagree after a restore or a dropped run |
| Stable names over per-run tables, regenerated in the publishing transaction | `WHATIF_RUN`, `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF` | Read-only users and the chat can reach the run's seasons without dynamic table names. The literal run id prunes the partition. Columns named by game id are stable week to week and self-describing. One code path hides layout C or A. Their empty forms exist from the first deploy, so the file views always compile |
| One escaping rule and a host reference renderer for generated DDL | `analyses/sqltext.py`, `analyses/whatif_ddl.py`, `analib.sql_str` | Data (school names, labels) reaches DDL text. One rule (`'` doubled) is unit-tested on the host; the Lua generator's output is compared with the host renderer's in the integration tests, with "Hawai'i" in the league |
| Generated SQL from a pure, tested library over serving names | `analyses/whatif.py` | Exasol has no parameterized views or table functions. The library validates conditions (integers from the run's game list only), so no injection is possible, and names the run, so a stale page gets no rows instead of another run's numbers. It is unit-tested for shape and integration-tested against host oracles |
| Precompute what you can, restrict what you can't | `ROOTING_GUIDE` from `LEVERAGE`; conditional guide over the coming week | The unconditional guide is a lookup (S4 0.078 s); the conditional guide's cost follows the columns aggregated, so the coming week (about 60 games, 120 aggregates) instead of S4's 400 games (800 aggregates) |
| Sample size beside every conditional number | `EFFECTIVE_SAMPLE`, `LOW_SAMPLE`, branch counts, `MOVE_THRESHOLD` | Toggles shrink the sample (S4: k = 8 leaves about 1 %); thresholds come from standard errors, not taste, and they show the scale dial's value (1M vs 100k) |
| Honest labels in the data | `FREEZE_KIND`, `IS_HEADLINE`, `ALL_TIME_FROM_SEASON`, `*_TIED`, `FIXED_RESULTS`, `IS_AFTER_CURRENT` | Every caveat a page or the chat must state is a column, so neither has to re-derive it |
| Thin serving layer with an allowlist | `CFB_API` views; lint per object | The contract is names and grains; analysis logic stays in `CFB_PRED`; the chat sees 26 curated views, 4 of them thin pass-throughs over the model, and none reaches CFBD |
| Network-reaching views in their own schema | `CFB_API_LIVE` (`GAME_PREVIEW`, `QUOTA`) | A schema grant cannot exclude one view. A second schema makes "the chat cannot spend quota" a grant fact, and Phase 7 can widen it with one grant |
| Read-only roles per schema | `CFB_APP` (both), `CFB_MCP` (`CFB_API`) | Views run with their owner's rights, so schema grants give exactly the serving layer and nothing else |
| Stored series, live preview | `MATCHUP_*` vs `GAME_PREVIEW` | The broad part (157 years of games) is stored and instant; the selective part (one upcoming game's preview) is the VS's on-demand role, under 2 s |
| Same declarative/thin-Lua split as Phases 2–4 | views for logic, `analib.lua` for steps | Testable without Lua; Lua only resolves points, locks, inserts, regenerates and logs |
| Host oracles for random outputs | `tests/analyses/oracle.py` | What-if and guide answers depend on a seeded run; the tests recompute them on the host from the run's own rows instead of hard-coding numbers |

#### Key Interfaces

**Objects of `CFB_PRED`** (files `sql/pred/NNN_<object>.sql`, data tables `sql/pred/data/<object>.sql`; every comment starts with "One row per <grain>"; DDL names unquoted upper case)

| File | Object | Kind | Grain | Reads |
|---|---|---|---|---|
| 010 | `ANALYSIS_SETTINGS` | reference (seed) | setting | seed |
| data | `PREDICTION` | data table | predicted game (frozen once) | — |
| data | `PUBLICATION` | data table | publication of a simulation run | — |
| data | `PUBLISHED_ODDS` | data table | publication and eligible team | — |
| 100 | `PREDICTION_GRADED` | view | prediction | `PREDICTION`, `CFB_MODEL.GAME` (result, current week and kickoff → `FREEZE_KIND`, `IS_RESCHEDULED`) |
| 110 | `SCORECARD_WEEK` | view | season, season type and week played, population and freeze kind | `PREDICTION_GRADED` |
| 120 | `SCORECARD_SEASON` | view | as-of point, population and freeze kind | `PREDICTION_GRADED`, `CFB_MODEL.AS_OF_POINT` |
| 130 | `SCORECARD_CALIBRATION` | view | season, population, freeze kind and pick-probability bucket | `PREDICTION_GRADED` |
| 200 | `UPSET` | view | completed FBS-involved game with a winner and a pregame probability | `CFB_SIM.GAME_WIN_PROB`, `SCORE_MODEL_PARAMS` (`CURRENT` Elo scale and HFA), `CFB_MODEL.GAME` (pregame Elo), `PREDICTION`, `ANALYSIS_SETTINGS` |
| 300 | `POINT_PUBLICATION` | view | as-of point with a publication | `PUBLICATION`, `CFB_MODEL.CURRENT_AS_OF` (`IS_AFTER_CURRENT`) |
| 305 | `WHATIF_PUBLICATION` | view | the what-if publication (≤ 1 row) | `POINT_PUBLICATION`, `PUBLICATION`, `CFB_MODEL.CURRENT_AS_OF`, `CFB_SIM.SIM_RUN` |
| 310 | `ODDS_MOVEMENT` | view | published point and team | `POINT_PUBLICATION`, `PUBLISHED_ODDS`, `ANALYSIS_SETTINGS` |
| 320 | `ROOTING_GUIDE` | view | done run, team with a playoff season, remaining game not fixed in the run | `CFB_SIM.LEVERAGE`, `SIM_RUN`, `SIM_RUN_GAME`, `SIM_RUN_TEAM`, `ANALYSIS_SETTINGS` |
| 330 | `FORCED_RUN_ODDS` | view | done forced run and eligible team | `CFB_SIM.SIM_RUN`, `SIM_RUN_GAME`, `SIM_TEAM_ODDS` |
| 400 | `MATCHUP_SERIES` | view | ordered team pair that has met | `CFB_MODEL.TEAM_GAME` |
| 410 | `MATCHUP_NEXT` | view | ordered team pair with a meeting still to play this season | `CFB_MODEL.CURRENT_AS_OF`, `GAME_AS_OF`, `CFB_SIM.GAME_SCORE_AS_OF`, `PREDICTION`, `WHATIF_PUBLICATION`, `ROOTING_GUIDE` |

Allowed references for `sql/pred/`: `{PRED_SCHEMA}.*`, `{SIM_SCHEMA}.*`, `{MODEL_SCHEMA}.*`.

**`WHATIF_PUBLICATION`** (the M1/M2 rule, the only definition of "the what-if run"): among the publications whose run is `DONE` and whose point is at or before `CURRENT_AS_OF` (by season, then week sequence), the newest point, then its highest `PUBLICATION_ID`. Readers: `whatif_views.lua` (what to generate), `analib.publish` (whether to regenerate, read inside its own transaction after its inserts), `CFB_API.WHATIF_GAMES` (only while the generated `WHATIF_RUN` names the same run), `CFB_API.CURRENT_POINT` (`WHATIF_*` columns and `WHATIF_VIEWS_CURRENT`) and `MATCHUP_NEXT` (stakes).

**Objects of `CFB_API`** (files `sql/api/NNN_<view>.sql`; templates of the generated views in `sql/api/generated/`; the grains and columns are `api/serving-contract`; every view costs 0 calls and makes no network request)

| File | View | Grain | File | View | Grain |
|---|---|---|---|---|---|
| 010 | `CURRENT_POINT` | the current point (1 row) | 330 | `SCORECARD_CALIBRATION` | season, population, freeze kind, bucket |
| 020 | `AS_OF_POINTS` | season and as-of point | 400 | `UPSETS` | ranked upset game |
| 030 | `TEAMS` | team | 500 | `MATCHUP_SERIES` | ordered pair that met |
| 100 | `PLAYOFF_ODDS` | published point and eligible team | 510 | `MATCHUP_GAMES` | meeting and ordered pair |
| 110 | `PUBLISHED_RUNS` | published point | 520 | `MATCHUP_NEXT` | ordered pair with a meeting to play |
| 120 | `ROOTING_GUIDE` | done run, team, remaining game | 600 | `REFRESH_TIMELINE` | refresh run and step |
| 130 | `FORCED_RUN_ODDS` | done forced run and team | 610 | `FETCHES` | logged request of a slice |
| 200 | `WHATIF_GAMES` | remaining game of the what-if run | 700 | `GAMES` | stored game (pass-through) |
| 210 | `ANALYSIS_SETTINGS` | setting | 710 | `TEAM_SEASONS` | team and season (pass-through) |
| 300 | `PREDICTIONS` | frozen prediction | 720 | `RANKINGS` | poll, week and ranked team (pass-through) |
| 310 | `SCORECARD_WEEKS` | season, week, population, freeze kind | 730 | `SP_RATINGS` | season and SP+-rated team (pass-through) |
| 320 | `SCORECARD_SEASONS` | as-of point, population, freeze kind | gen | `WHATIF_RUN` | the what-if run (≤ 1 row) |
| | | | gen | `WHATIF_OUTCOMES` | simulated season of the what-if run |
| | | | gen | `WHATIF_PLAYOFF` | simulated season and playoff team |

**Objects of `CFB_API_LIVE`** (files `sql/api_live/NNN_<view>.sql`; `api/on-demand`): `010_game_preview.sql` → `GAME_PREVIEW` (requested game, 1 call per query) and `020_quota.sql` → `QUOTA` (the key's quota, 1 row, `/info`).

Allowed references:
- `sql/api/`: `{API_SCHEMA}.*`, `{PRED_SCHEMA}.*`, `{SIM_SCHEMA}.*`, `{MODEL_SCHEMA}.*`, and `{META_SCHEMA}.REFRESH_LOG`, `LOAD_RUN`, `FETCH_LOG`. No virtual table and no `QUOTA`;
- `sql/api_live/`: `{VS_NAME}.GAMES_PREVIEW` (only in `010_game_preview.sql`) and `{META_SCHEMA}.QUOTA` (only in `020_quota.sql`);
- the lint rejects `{CORE_SCHEMA}` everywhere and every other virtual table.

The pass-through views select the source's columns unchanged. Their files restate the source's column comments (Exasol takes view column comments inline), and `test_api_contract_it.py` compares them with the source's. Their view comments are rewritten grain first, say that they show stored history as it is now (not as of a point), and name the page views for the same questions.

**Data tables** (`sql/pred/data/*.sql`; every column commented in the files)

```sql
CREATE TABLE IF NOT EXISTS {PRED_SCHEMA}.PREDICTION (GAME_ID DECIMAL(18,0) NOT NULL, SEASON DECIMAL(4,0) NOT NULL,
  SEASON_TYPE VARCHAR(10) UTF8 NOT NULL, WEEK DECIMAL(3,0) NOT NULL, WEEK_SEQ DECIMAL(3,0) NOT NULL, START_AT TIMESTAMP,
  HOME_TEAM_ID DECIMAL(18,0) NOT NULL, AWAY_TEAM_ID DECIMAL(18,0) NOT NULL, IS_NEUTRAL_SITE BOOLEAN,
  IS_CONF_CHAMPIONSHIP BOOLEAN, IS_FBS_VS_FBS BOOLEAN, FREEZE_SEASON DECIMAL(4,0) NOT NULL,
  FREEZE_WEEK_SEQ DECIMAL(3,0) NOT NULL, FREEZE_LABEL VARCHAR(100) UTF8, PARAMS_VERSION VARCHAR(20) UTF8 NOT NULL,
  WEEK_PHASE VARCHAR(8) UTF8, FEATURE_SET VARCHAR(20) UTF8, PARAMS_FINGERPRINT VARCHAR(200) UTF8,
  HOME_RATING DECIMAL(6,2), AWAY_RATING DECIMAL(6,2), HOME_RATING_SOURCE VARCHAR(16) UTF8, AWAY_RATING_SOURCE VARCHAR(16) UTF8,
  MU DECIMAL(7,2) NOT NULL, SIGMA DECIMAL(5,2) NOT NULL, TOTAL DECIMAL(5,1), HOME_POINTS_PRED DECIMAL(5,1),
  AWAY_POINTS_PRED DECIMAL(5,1), HOME_WIN_PROB DECIMAL(5,4) NOT NULL, PICK_TEAM_ID DECIMAL(18,0),
  PICK_WIN_PROB DECIMAL(5,4) NOT NULL, FROZEN_AT TIMESTAMP NOT NULL, FROZEN_BEFORE_KICKOFF BOOLEAN NOT NULL,
  FROZEN_BY VARCHAR(64) UTF8 NOT NULL);
CREATE TABLE IF NOT EXISTS {PRED_SCHEMA}.PUBLICATION (PUBLICATION_ID DECIMAL(18,0) IDENTITY NOT NULL,
  RUN_ID DECIMAL(18,0) NOT NULL, AS_OF_SEASON DECIMAL(4,0) NOT NULL, AS_OF_WEEK DECIMAL(3,0) NOT NULL,
  AS_OF_SEASON_TYPE VARCHAR(10) UTF8 NOT NULL, AS_OF_WEEK_SEQ DECIMAL(3,0) NOT NULL, AS_OF_LABEL VARCHAR(100) UTF8,
  RUN_ORIGIN VARCHAR(8) UTF8 NOT NULL, N_SIMS DECIMAL(18,0) NOT NULL, RUN_SECONDS DECIMAL(9,3),
  PUBLISHED_AT TIMESTAMP NOT NULL, PUBLISHED_BY VARCHAR(64) UTF8 NOT NULL, LOAD_RUN_ID DECIMAL(18,0));
CREATE TABLE IF NOT EXISTS {PRED_SCHEMA}.PUBLISHED_ODDS (PUBLICATION_ID DECIMAL(18,0) NOT NULL,
  RUN_ID DECIMAL(18,0) NOT NULL, TEAM_ID DECIMAL(18,0) NOT NULL, SCHOOL VARCHAR(2000) UTF8, CONFERENCE VARCHAR(2000) UTF8,
  TIER VARCHAR(8) UTF8, P_IN_CFP DECIMAL(7,6) NOT NULL, P_BYE DECIMAL(7,6) NOT NULL, P_QUARTERFINAL DECIMAL(7,6) NOT NULL,
  P_SEMIFINAL DECIMAL(7,6) NOT NULL, P_FINAL DECIMAL(7,6) NOT NULL, P_CHAMPION DECIMAL(7,6) NOT NULL,
  P_CONF_CHAMPION DECIMAL(7,6), EXPECTED_WINS DECIMAL(5,2), MEAN_SEED DECIMAL(4,2), ODDS_RANK DECIMAL(4,0) NOT NULL);
-- ANALYSIS_SETTINGS (reference, replaced on deploy): SETTING_NAME VARCHAR(64), SETTING_NUMBER DECIMAL(12,4), SETTING_NOTE
--   rows MIN_EFFECTIVE_SAMPLE 1000, MIN_BRANCH_SAMPLE 100, MOVE_THRESHOLD 0.0070 (valid for runs >= 100,000 seasons),
--   ALL_TIME_FROM_SEASON 1978 (first Division I-A season); TEST_FAIL_PUBLISH only in TEST_
```

Uniqueness of `PREDICTION.GAME_ID` and of `(PUBLICATION_ID, TEAM_ID)` is kept by the writers (`NOT EXISTS` under the run lock) and asserted by the tests. No `PRIMARY KEY` constraint is declared, so a constraint check cannot fail a refresh.

**Generated what-if views** (templates `sql/api/generated/whatif_*.sql`, rendered by `analib.lua` for the what-if publication; shown for layout C and run 42)

```sql
CREATE OR REPLACE VIEW {API_SCHEMA}.WHATIF_OUTCOMES (
  RUN_ID     COMMENT IS 'Simulation run 42, the what-if run (CFB_API.WHATIF_RUN)',
  SIM_ID     COMMENT IS 'Simulated season: batch id × sims per batch + k (sim-engine/engine-contract)',
  G401628571 COMMENT IS 'Week 12: Brookvale at Ashford (game 401628571); TRUE = home win',
  …) AS
SELECT "RUN_ID", "SIM_ID", "G0037", … FROM {SIM_SCHEMA}."SIM_PATH_R42"
COMMENT IS 'One row per simulated season of run 42 (2026, after week 9; 1,000,000 seasons), the what-if run. One BOOLEAN column per remaining game, named G<game id>, TRUE = home win. Filter on these columns and count playoff rows in CFB_API.WHATIF_PLAYOFF; build queries with analyses/whatif.py. Rewritten whenever CFB_PRED.WHATIF_PUBLICATION changes.';
--   a school with an apostrophe is written with it doubled: COMMENT IS 'Week 11: Hawai''i at Eastport (game 401628590); TRUE = home win'
-- layout A (if Phase 4's probe 1.1c adopts it): "G401628571" ← SUBSTR("OUTCOMES", 37, 1) = '1' … FROM {SIM_SCHEMA}.<A table> WHERE "RUN_ID" = 42
CREATE OR REPLACE VIEW {API_SCHEMA}.WHATIF_PLAYOFF (…) AS
SELECT "SIM_ID", "TEAM_ID", "SEED", "BYE", "REACHED_ROUND", "CHAMPION" FROM {SIM_SCHEMA}."SIM_TEAM" WHERE "RUN_ID" = 42
COMMENT IS 'One row per simulated season and playoff team of run 42, the what-if run …';
-- WHATIF_RUN: one row from SIM_RUN 42 joined to its PUBLICATION row, NEXT_WEEK_SEQ = MIN(WEEK_SEQ) of its REMAINING games
-- empty form (no what-if publication; also created inside the deploy transaction when missing): the same columns, no rows,
--   e.g. SELECT CAST(NULL AS DECIMAL(18,0)) "RUN_ID", … WHERE FALSE (FROM is optional in Exasol's SELECT grammar);
--   WHATIF_OUTCOMES then has only RUN_ID, SIM_ID
```

- **The column list** comes from `SIM_RUN_GAME` (`GAME_ROLE` `REMAINING`, `PATH_COLUMN`, `GAME_IDX`, teams, week), read `ORDER BY GAME_IDX`. `analib.lua` builds the text by iterating those rows; `LISTAGG` is not used, so there is no ordering or length question (review m12; probe 1.1(d) only times the DDL).
- **Escaping:** every string literal in the text (column comments with school names, the view comment with the point's label) goes through `analib.sql_str(s)`, which doubles `'`. The new identifiers are built from integers only (`G<game id>`, the run id). The source names come from Phase 4's `SIM_RUN.PATH_TABLE` and `SIM_RUN_GAME.PATH_COLUMN`; the generator checks each against `^[A-Z][A-Z0-9_]*$` before use and double-quotes it.
- **Reference renderer:** `analyses/whatif_ddl.py` renders the same names, order and comments from the same rows on the host (pure, using `analyses/sqltext.py`'s `sql_str`). Unit tests pin it (apostrophes, ordering, the empty forms). The integration tests compare the deployed view's columns and comments with it, so the Lua and the host cannot drift. `analyses/deploy.py` uses it for the empty forms and `analyses/docs.py` for the dictionary's patterns.

**Library** (`analyses/whatif.py`, pure, no DB; Python 3.12, stdlib only)

```python
@dataclass(frozen=True)
class Condition:
    game_id: int
    home_wins: bool

MAX_CONDITIONS = 16

def parse_conditions(text: str) -> list[Condition]: ...        # "401628571=A,401628569=H" (make what-if, CLI)
def what_if_odds_sql(run_id: int, conditions: Sequence[Condition], games: Collection[int],
                     *, api_schema: str = "CFB_API") -> str: ...
def rooting_guide_sql(run_id: int, team_id: int, conditions: Sequence[Condition], games: Sequence[int],
                      *, api_schema: str = "CFB_API") -> str: ...   # games: the coming week's WHATIF_GAMES ids
class ConditionError(ValueError): ...                          # names the offending condition or argument
```

- Validation runs before any text is built. It refuses: a game not in `games`; a game listed twice; more than 16 conditions; ids that are not `int` (`bool` included); `team_id` or `run_id` not an `int`. Identifiers are double-quoted; only validated integers and the literals `TRUE`/`FALSE` enter the text.
- **Run guard** (review m2): `"R" AS (SELECT … FROM WHATIF_RUN WHERE "RUN_ID" = <run_id>)` anchors every result row, `"M"` filters `WHATIF_OUTCOMES` on `"RUN_ID" = <run_id>` as well, and every row returns `RUN_ID`. A query for a run that is no longer the what-if run returns no rows (same games) or fails on an unknown column (other games); the page contract says to reload on either.
- Conditional odds shape (S4's measured sparse Q1):
  - `WITH "M" AS (SELECT "SIM_ID" FROM WHATIF_OUTCOMES WHERE "RUN_ID" = <run> AND <c₁> AND … )`, then `"X"` = `COUNT(*)` of `"M"`;
  - per-team counts from `WHATIF_PLAYOFF JOIN "M" ON "SIM_ID" GROUP BY "TEAM_ID"`;
  - the roster with base odds from `PLAYOFF_ODDS JOIN "R" ON PUBLICATION_ID`, cross-joined with `"X"` and left-joined with the counts;
  - the threshold from `ANALYSIS_SETTINGS`.
- Conditional guide shape (S4's Q3 semi-join, decided by probe 1.1(a)):
  - `"M"` with the run guard, the conditions and the columns of the requested games;
  - an in-CFP flag per season via `"SIM_ID" IN (SELECT "SIM_ID" FROM WHATIF_PLAYOFF WHERE "TEAM_ID" = :team)` inside `SUM(CASE …)`. The skills do not cover a predicate subquery in the select list, so probe 1.1(a) confirms that Exasol accepts it and evaluates it once (a semi-join in the profile), not per row. Fallback: a `LEFT JOIN` of `"M"` to the team's playoff seasons and `CASE WHEN "P"."SIM_ID" IS NOT NULL`;
  - one aggregate pass with `COUNT(*)`, the flag's sum, and per game `SUM(CASE WHEN "G…" THEN 1 ELSE 0 END)` and the flagged sum (two aggregates per game);
  - unpivoted by `CASE "GAME_ID" WHEN … END` over the requested `WHATIF_GAMES` rows. Games fixed by a condition are dropped from `games` first.
- Alternative measured by probe 1.1(a): S4's two-pass shape (`per_game_counts` twice, once restricted to the team's playoff seasons, `spikes/s4/queries.py`) cut to the same 60 games, so a fallback with measured provenance exists if the one-pass shape misbehaves.

**Lua scripts** (`analyses/lua/`, bundled by `loader/bundle.py` with `loadlib.lua`; `pquery` everywhere, `ROLLBACK` before logging a failure, `R0003` treated as "cannot log", the Phase 2 recipe)

| Script | Args | Lock | Does |
|---|---|---|---|
| `REFRESH_PREDICT` | `run_id, season, week, season_type` | the refresh's | resolve the point; skip outside the current season; freeze per point, oldest first (`analib.freeze`); returns rows |
| `PREDICT` | `season, week, season_type` | kind `SIMULATE` (H1a), args `PREDICT …` | the same, standalone |
| `REFRESH_PUBLISH` | `run_id, season, week, season_type` | the refresh's | find the refresh's `DONE` run, else skip; `analib.publish` |
| `PUBLISH_RUN` | `run_id` | kind `SIMULATE`, args `PUBLISH_RUN <run>` | check eligibility (the point not after `CURRENT_AS_OF` included); `analib.publish` |
| `WHATIF_VIEWS` | — | the caller's (deploy holds `DEPLOY`) | regenerate the three views for `CFB_PRED.WHATIF_PUBLICATION`, or empty ones |

`analib.publish(run, by)` runs in one transaction: `INSERT` `PUBLICATION`, `INSERT … SELECT` `PUBLISHED_ODDS` from `SIM_TEAM_ODDS` with the ranks, then reads `WHATIF_PUBLICATION` (which sees the uncommitted rows of its own transaction) and regenerates the views when it names the new publication, then `COMMIT`; on any error `ROLLBACK` and re-raise naming the run. `TEST_FAIL_PUBLISH` raises inside the view step. `analib.sql_str` is the only way a string enters generated SQL text.

**Refresh steps** (`loader/load-plan.yaml`; written into `CFB_META.REFRESH_STEPS` by the loader's deploy, Phase 2's `loader/deploy.py`, like steps 100 and 300; `make analyses` only reports them, review m9)

```yaml
refresh_steps:
  - {step_no: 100, name: MODEL,    script: CFB_INFRA.REFRESH_MODEL}      # Phase 3
  - {step_no: 200, name: PREDICT,  script: CFB_INFRA.REFRESH_PREDICT}    # this plan
  - {step_no: 300, name: SIMULATE, script: CFB_INFRA.REFRESH_SIMULATE}   # Phase 4
  - {step_no: 400, name: PUBLISH,  script: CFB_INFRA.REFRESH_PUBLISH}    # this plan
```

The integration tests deploy the loader with this plan's variant `tests/it/fixtures/load-plan.analyses.yaml` (steps 100–400 with `TEST_` scripts). Phase 3's tests use its variant `load-plan.model.yaml`; task 1.6 confirms that no Phase 3 or Phase 4 integration test deploys the project YAML, which would register steps 200 and 400 without their scripts.

**`CFB_API_LIVE.GAME_PREVIEW` columns** (from `GAMES_PREVIEW` of the catalog, renamed): `GAME_ID` ← `P_GAME_ID`; `SEASON`, `WEEK`, `SEASON_TYPE`, `START_DATE`, `STATUS`, `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, `VENUE_NAME`, `VENUE_CITY`, `VENUE_STATE`; per side (`HOME_`/`AWAY_`): `TEAM_ID`, `TEAM_NAME`, `CONFERENCE`, `CLASSIFICATION`, `POINTS`, `WINS`, `LOSSES`, `TIES`, `SP_RATING`, `SP_RANK`, `SP_OFFENSE_RATING`, `SP_DEFENSE_RATING`, `SRS_RATING`, `ELO`, `FPI_RATING`, `FPI_RANK`; `AVAILABILITY`, `REASON`, `ODDS_PROVIDER`, `SPREAD`, `OVER_UNDER`, `HOME_MONEYLINE`, `AWAY_MONEYLINE`, `RAW_JSON`; `REQ_SOURCE`, `REQ_STATUS`, `REQ_ATTEMPTS`, `REQ_MS`, `REQ_AT`, `REQ_SLICE`. The task checks every new name against the reserved-word file.

**Make targets** (`scripts/analyses.py`; DB targets run `db-check` first and accept `TARGET` and `ARGS="--prefix …"`)

| Target | Does |
|---|---|
| `make analyses [ARGS="--setting K=V"]` | lint, render, deploy `CFB_PRED`, the missing empty generated views, `CFB_API`, `CFB_API_LIVE`, the Lua scripts (lock `DEPLOY`, one transaction); then `WHATIF_VIEWS`; report with the registered steps. Also the last step of the restore recipe |
| `make deploy` | virtual schema → loader → model → sim → analyses |
| `make grants` | + roles `CFB_APP` (`SELECT ON SCHEMA CFB_API`, `CFB_API_LIVE`) and `CFB_MCP` (`SELECT ON SCHEMA CFB_API`), prefix-aware |
| `make predict SEASON= WEEK= [SEASON_TYPE=]` | `EXECUTE SCRIPT CFB_INFRA.PREDICT(…)`; print the frozen rows |
| `make publish RUN=` | `EXECUTE SCRIPT CFB_INFRA.PUBLISH_RUN(run)`; print the top 25 with arrows |
| `make what-if TEAM=<school or id> [FIX="<game id>=H\|A,…"]` | run both library queries as the owner, print conditional odds (top 25), the team's coming-week guide, the effective sample and the seconds |
| `make api-docs` | regenerate `docs/api-dictionary.md` (no DB) |
| `make analyses-perf [ARGS="--prefix PERF_ --drop"]` | the timed queries of the performance budget, S4 protocol; print p50/p95/max and sizing. Never touches `CFB_API_LIVE` (review M6) |

**Code layout**

```
analyses/__init__.py
analyses/whatif.py        pure builders and validation
analyses/sqltext.py       sql_str(): the one escaping rule for string literals in generated SQL (pure)
analyses/whatif_ddl.py    reference renderer of the generated views' names, order and comments, and their empty forms (pure)
analyses/deploy.py        make analyses (binds vs/host/sqlobjects.py to sql/pred, sql/api, sql/api_live; data tables; empty generated views; scripts; WHATIF_VIEWS; step report)
analyses/docs.py          make api-docs (reuses Phase 3's dictionary generator; renders the generated-view templates as patterns)
analyses/perf.py          make analyses-perf (S4 protocol, condition sets, sizing; a fixed query catalog over CFB_API only)
analyses/lua/analib.lua, refresh_predict.lua, predict.lua, refresh_publish.lua, publish_run.lua, whatif_views.lua
scripts/analyses.py       CLI for the make targets
sql/pred/*.sql, sql/pred/data/*.sql, sql/api/*.sql, sql/api/generated/*.sql, sql/api_live/*.sql
tests/it/fixtures/load-plan.analyses.yaml   plan variant with steps 100–400
tests/analyses/oracle.py              host oracles: conditional counts, guide branches, series and streaks, ranks, scorecard
tests/synthetic/analyses_history.py   history variant of the sim league (games before 1998 only)
docs/spikes/s8_analyses_probe.py, docs/spikes/s8-analyses-probe.md
docs/api-dictionary.md (generated), docs/analyses-verification.md
```

**History variant** (`tests/synthetic/analyses_history.py`, on top of Phase 4's `sim_league()`, through the same flattener; game ids from a reserved range; seasons before 1998, so no power-tier, simulation or calibration season is touched)

| Season | Game | Pregame Elo (home, away) | Purpose |
|---|---|---|---|
| 1885 | Ashford 4 – Kestrel 0 at Ashford | none | no probability → not ranked |
| 1898 | Kestrel 6 at Ashford 5 | 2400, 1000 | Elo-era upset 0.9998 |
| 1950 | Ashford 7 – Brookvale 7 at Ashford | 1600, 1500 | tie in the series, not ranked |
| 1985 | Brookvale 3 at Ashford 0 | 2400, 1000 | the 1898 magnitude exactly (0.99976964): tied with it when the window starts in 1890 |
| 1987 | Brookvale 7 at Ashford 6 | 2395, 1000 | 0.99976292: also shows 0.9998, ranks below 1985 on the unrounded magnitude |
| 1990 | Ashford 35 at Brookvale 10 | 1550, 1650 | away win, not an upset (0.4356) |
| 1995 | Brookvale 28 at Ashford 24 | 1800, 1400 | Elo-era upset 0.9321 in the series |

Ashford is classified `fbs` on these games; Kestrel has no membership and no later game. A unit test mirrors Phase 3's ERROR rules for these seasons. The 1898 game lies before the default all-time window (1978) and is ranked within its season only.

**Apostrophe school.** The what-if and roles tests need a remaining game of a school whose name has an apostrophe. Phase 4's sim league provides it through hand-off P5-M3 (recorded in Phase 4's `review.md`): one Mountain West team is named `Hawai'i`, and Phase 4's per-run DDL escapes it. Task 1.5 asserts that `sim_league()` has the school with remaining 2026 games after week 9. If Phase 4 has not adopted the rename by then, task 1.5 adds a variant `with_apostrophe_school` in `analyses_history.py` that renames one Mountain West team, and the what-if tests seed it; Phase 4's escaping is then exercised by this plan's tests.

A second, separate variant `with_fcs_only_game` adds one 2026 week-11 game between the FCS teams Lowell and Pemberton, for the predictions scenario that excludes games without an FBS team. The engine never sees it: its inputs take only games that involve an FBS team.

**Performance budget** (targets; measured by probe 1.1(a) on synthetic data, task 5.1 locally, task 7.2 on AWS real data, task 7.4 locally on restored data)

| Query or step | Local 100k (8 cores) | AWS 1M (3 × 4) | Basis |
|---|---|---|---|
| Conditional odds, all teams, k = 1 / 3 / 8 | < 0.3 s p95 | < 1 s p95 | S4 sparse 0.52 / 0.21 / 0.17 s at 1M AWS, < 0.12 s at 100k; plus the view layer (probe 1.1(a)) |
| **Conditional rooting guide, coming week (≈ 60 games), k = 1 / 3** | **< 0.5 s p95** | **< 1 s p95** | S4 Q3 k = 3 2.2 s aggregating 800 `SUM(CASE)` columns (2 × 400 games); the one-pass shape over 60 games aggregates ≈ 120 → ≈ 0.12 s + 2.0 s × 120/800 ≈ 0.4 s at 1M; S4's 100k local 0.95 s → ≈ 0.15–0.25 s (estimates; probe 1.1(a) decides) |
| Unconditional guide, one run and team | < 0.2 s | < 0.2 s | S4 `LEVERAGE` 0.078 s |
| `PLAYOFF_ODDS`, one point | < 0.3 s | < 0.3 s | ≈ 136 rows of a frozen table and its previous point |
| `UPSETS`, one week | < 1 s | < 1 s | a rank window over ≈ 150k FBS-involved games (real data) |
| `MATCHUP_SERIES` + `MATCHUP_GAMES` + `MATCHUP_NEXT`, one pair | < 1 s | < 1 s | `TEAM_GAME` over ≈ 250k games; Phase 3's < 1 s per view target |
| `CFB_API_LIVE.GAME_PREVIEW`, live | — | < 2 s, median of 3 | S1: 0.7 s at CFBD + ≈ 0.6 s database. **Measured only by task 7.3's 3 ledgered calls, never by `make analyses-perf`** |
| Step `PREDICT`, one point | < 5 s | < 5 s | one `GAME_SCORE_AS_OF` evaluation (Phase 3/4: < 1 s per as-of view and point) |
| `PREDICT` filling 10 missing points | < 30 s | < 30 s | one evaluation per point |
| Step `PUBLISH` (odds, 3 views) | < 5 s | < 10 s | `SIM_TEAM_ODDS` over 1.2M / 12M rows (S4: 12M sparse rows in 0.41 s); DDL of ≤ 900 commented columns (probe 1.1(d)) |
| `REFRESH_WEEK` with 100k seasons and steps 200–400, replay | < 2 min | — | mission; Phase 4's budget ≤ 30 s for step 300 |

Protocol (S4): `QUERY_CACHE` off, 3 discarded warm-ups, 5 condition sets × 5 runs (25 samples, p95 = the second largest), exclusive window, sizing recorded (`EXA_SYSTEM_EVENTS`, `nrOfCores`). Steps are judged on the median of 3. A miss is recorded with its breakdown and a logged decision.

`analyses/perf.py` times a fixed catalog of queries over `CFB_API` only. The live rows of the budget (`GAME_PREVIEW`, and `QUOTA`, which would reach `/info` on every sample) are excluded by construction, and `tests/unit/test_analyses_perf.py` asserts that no catalog query names `GAME_PREVIEW`, `QUOTA`, `CFB_API_LIVE`, `{API_LIVE_SCHEMA}` or a virtual schema (review M6).

The measuring task does not change the design, except for the rooting-guide fallbacks:
- **F1:** the conditional guide over the team's top 25 coming-week games by unconditional |leverage|, from a `LEVERAGE` lookup first.
- **F2:** keep the coming week and accept up to 2 s, with a "computing" state in the page contract, logged.

**Exasol claims checked against the skills (`exasol-database`, `exasol-udfs` and `exasol-distributed-ml` for the first version; `exasol-database`, `exasol-udfs` and `exasol-virtual-schema-adapter-development` for the revision), the predecessor probes and the architecture**

| Claim | Evidence | Result |
|---|---|---|
| `GRANT SELECT ON SCHEMA s TO role`; `CREATE ROLE` without `IF NOT EXISTS` | `exasol-grammar.md` `grant_objprivs_statement_1` (`"ON" ["SCHEMA" \| …] object`), `create_role_statement` | Grammar confirmed; idempotency by checking the role first. Whether a schema grant covers views created after it: probe 1.1(c) |
| A user with `SELECT` on a view reads it without rights on the underlying objects (owner's rights), also over a virtual table | Not stated in the skills; foundation probe 1.4g: VS pushdown runs with the owner's rights | Probe 1.1(b), decisive for `api/roles`; fallback in Q2 |
| A schema's objects belong to the schema's owner, so the views run with the rights of the owner of `CFB_API` and `CFB_API_LIVE` | Not stated in the skills; review § 4 | One user owns every schema of a deployment (`sys`, `CFB_TEST_OWNER`); probe 1.1(b) creates its views in a schema owned by the test owner and checks it |
| `IN (SELECT …)` as a predicate inside `SUM(CASE …)` in the select list, evaluated once | Not covered by the skills (review m4) | Probe 1.1(a): accepted, and a semi-join in the profile; fallback a `LEFT JOIN` flag |
| `SELECT … WHERE FALSE` without `FROM` for the empty generated views | `exasol-grammar.md` `select_statement_1`/`_2`: `FROM` and `WHERE` are both optional | Confirmed by the grammar; compiled by task 2.2's deploy test |
| `POWER(base, exponent)` in `DOUBLE` for the unrounded Elo magnitude | `exasol-grammar-functions.md` `power` | Confirmed; the upsets test pins the result against `GAME_WIN_PROB`'s rounded value |
| A literal predicate on a view column that aliases a `P_` column is pushed to the adapter; `EXPLAIN VIRTUAL` works through a view | Not stated; architecture: subqueries, expressions and joins on `P_` columns are never pushed (probe 1.4e) | Probe 1.1(b); fallback: expose `P_GAME_ID` unaliased (Q4) |
| `CREATE OR REPLACE [FORCE] VIEW v (c COMMENT IS '…', …) AS … COMMENT IS '…'`; no `COMMENT ON VIEW` | `exasol-grammar.md` `create_view_statement`; Phase 3 probe 1.1a/1.1f | Confirmed; the generated views use inline comments |
| DDL is transactional (view regeneration rolls back with the publication); a view created earlier in the same transaction is visible to later statements of it (the deploy's empty forms, then the file views) | Phase 3 probe 1.1b | Re-checked in probe 1.1(e) with the widened reader set (WAIT FOR COMMIT vs rollback is the open part) |
| No table functions in `FROM`; SQL functions return one value | `create_function_statement` (`RETURN` type); Phase 3 | Confirmed: no parameterized views; the library builds SQL |
| `LISTAGG` with an overflow clause | `exasol-grammar-functions.md` `listagg_1`, `listagg_overflow` | Exists, but not used: the Lua generator iterates the rows `ORDER BY GAME_IDX` (review m12; ≈ 100k characters would have been far below `LISTAGG`'s limit anyway) |
| `RANK()`, `ROW_NUMBER()`, windows with `PARTITION BY` | `analytics-qualify.md`, `exasol-grammar-functions.md` | Confirmed |
| A query reads only the columns it names (column store), so the guide's cost follows the columns counted | Not stated in the skills; S4: Q2 (k columns) < 0.2 s vs Q3 (400 columns) 2.2 s at 1M | Probe 1.1(a) measures 60 vs 400 columns |
| `QUERY_CACHE` session switch | S4 hygiene (`QUERY_CACHE = OFF`) | Used for timings; a warm repeat is recorded, not relied on (Q10) |
| Lua `pquery`, nested `EXECUTE SCRIPT`, `error()`, output discarded without `WITH OUTPUT` | `exasol-udfs` `lua-execute-scripts.md` | Confirmed; the Phase 2–4 recipe |
| Partition pruning on `SIM_TEAM` by a literal `RUN_ID` with a season of runs stored | Phase 4 probe 1.1e | Taken from Phase 4; re-measured through `WHATIF_PLAYOFF` in 1.1(a) and 7.2 |
| Reserved words | `vs/gen/exasol_reserved_keywords.txt` and the skill's `exasol-reserved-keywords.md` (2026.1.0) | Every planned name checked. `NEXT`, `CURRENT`, `NEW`, `FIRST`, `LAST`, `RESULT`, `VALUE`, `SOURCE`, `CONDITION` and `ALL` are reserved and avoided as names (`NEXT_WEEK_SEQ`, `LAST_GAME_RESULT`, `PROB_SOURCE`); `NEW`, `NEXT`, `CURRENT` and `ALL` occur only as string values. The revision's names (`CFB_API_LIVE`, `WHATIF_PUBLICATION`, `FREEZE_KIND`, `IS_HEADLINE`, `RANK_MAGNITUDE`, `ALL_TIME_FROM_SEASON`, `ALL_TIME_TIED`, `LINE_ERA_TIED`, `IS_AFTER_CURRENT`, `WHATIF_VIEWS_CURRENT`, `IS_RESCHEDULED`, `FIXED_RESULTS`, `GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`, `ANALYSIS_SETTINGS`) are not reserved |

No UDF is added: the analyses are SQL and Lua. The ML skill's guidance (load once per VM, batch per SET group) applies to Phase 4's engine and is not exercised here.

### Consequences

| Decision | Alternatives Considered | Rationale |
|----------|------------------------|-----------|
| Explicit publication per point; step 400 `PUBLISH`; `make publish` ([2]) | "Newest DONE run of the point wins" by rule; only refresh runs | One deliberate answer to "what do pages show"; the scale dial shows a manual 1M run on request; backtests and forced runs never leak; the publication is the trigger for regenerating the what-if views |
| Movement from frozen published odds ([3]) | Compare live `SIM_TEAM_ODDS` of two retained runs | Arrows survive dropped runs and redeploys, cost O(teams), and compare with what was shown; Phase 4's retention stays for older guides |
| Generated `CFB_API` views with game-id columns and a literal run id ([4]) | Backend generates SQL against `CFB_SIM.SIM_PATH_R<n>`; per-run views in `CFB_API`; copy the run into a fixed table; a Lua script returning rows | Read-only roles and MCP can only `SELECT` from `CFB_API`; one stable name; partition pruning; no copy at 1M; layout-agnostic |
| A pure host library builds what-if SQL ([5]) | Parameterized views (not in Exasol); `EXECUTE SCRIPT` (not usable by MCP or read-only roles); SQL inside the app | Tested once, shared by app, chat tools and CLI; validation makes injection impossible |
| Rooting guide: `LEVERAGE` lookup without toggles; computed over the coming week with toggles; F1/F2 fallbacks; full season via forced re-sim ([6]) | Precompute per-condition leverage for top-N bubble teams; cache per what-if; accept 2.2 s; `DISTRIBUTE BY` | Per-condition precompute only covers k = 1 and costs about G² per team; caching helps only repeats; the coming week is what fans ask and cuts the aggregated columns from S4's 800 to about 120; measured at k = 1 and 3 before use, with S4's two-pass shape as the measured alternative; a forced run's guide drops its forced games and is labeled ([37]) |
| Effective-sample flags at 1,000 seasons and 100 per branch ([7]); arrows at 0.7 points ([35]) | No flags; fixed share of n; 0.5 points; a per-team SE threshold | Standard-error based: 1.6 points at worst for 1,000; an arrow compares two independent runs, and 0.7 points is 3.1 SE of that difference at 100k vs 100k (4.2 at 1M vs 100k). A constant is simpler than a per-team threshold and valid for every run size the demo uses |
| Forced re-sims stay owner actions; their results are served ([8]) | Grant the roles `EXECUTE` on a wrapper; a request queue | Interview: read-only roles; a forced run writes and locks; Phase 6 decides an owner path |
| Freeze next week once; reconstruct gaps at their own point, flagged; current season only ([9]) | Re-freeze every refresh; no reconstruction; predict past seasons with `CURRENT` | Honest grades; complete scorecards after skips and new deployments; no in-sample grades |
| Predictions and publications are deployment history, not in the snapshot ([10]) | Add `CFB_PRED` to the snapshot scope | Like run logs; restore keeps them; no cross-database run-id clashes; no Phase 2 change |
| Two populations, pick-probability buckets, the line on the same games ([12]) | One population; tenths of home probability | Sensible headline (`FBS_INVOLVED`) and a clean comparison (`FBS_VS_FBS`); like-for-like Brier vs the line |
| Freeze kinds `IN_ADVANCE` / `RECONSTRUCTED` / `ALL` in every scorecard view; the headline uses `IN_ADVANCE` or is labeled; games graded in the week played, the kind re-judged against the current kickoff ([29]) | One mixed record; a flag count only; drop reconstructions | "Predictions made in advance" is the architecture's own claim; a rehearsal or a mid-season deployment would otherwise headline reconstructions; reconstructions stay useful and are leak-free |
| All-time rank since `ALL_TIME_FROM_SEASON` 1978, on the unrounded magnitude, plus a line-era rank; ties shown as "T-n" ([13], [30]) | Every season since 1890; rank on `DECIMAL(5,4)`; line era only; rank as of the game's time | Before Division I-A the FBS population is a retroactive label, and early Elo gaps lie far outside the fit and crowd at 0.9999; rounding made ties that are not ties. Mixed sources are labeled; the era rank keeps a clean comparison |
| Head-to-head from stored games, preview of the next or the latest meeting as the live beat, no `/teams/matchup` ([14], [34]) | D13's on-demand matchup with a fetching state; stored pairs else fetch; preview only for a meeting still to play | 0 calls and instant for 157 years; matchup is 4.9 s and redundant; the preview is 0.7 s and genuinely on-demand data, and with the latest meeting every pair that has met gets the click. Its recent meetings check the stored series at no extra call (3 planned calls) |
| `CFB_API` is views only, thin, with a per-object allowlist ([16]); four pass-through views over the model for the chat ([32]) | Logic in `CFB_API`; functions; page-shaped views only, or a Phase 7 hand-off | One contract; the chat sees curated names and can still answer "how did Michigan do in 1997?" at 0 calls |
| The what-if run is defined once (`WHATIF_PUBLICATION`: newest published point at or before the current point); empty generated views exist from the first deploy; `make analyses` after a restore ([26], [27]) | "Newest published point" as before; four separate derivations; file views reading only `CFB_PRED` | A restore no longer leaves the pages on a later week; consumers cannot disagree; a fresh deploy compiles every view |
| One escaping rule for strings in generated SQL, a host reference renderer, an apostrophe school in the league ([28]) | Escape ad hoc in each generator; avoid names in comments | "Hawai'i" would have rolled back every weekly publication with a Hawai'i game left; the renderer makes the Lua output testable on the host |
| Network-reaching views in `CFB_API_LIVE`; `CFB_APP` reads both schemas, `CFB_MCP` only `CFB_API` ([17], [33]) | One schema for both roles; per-object grants regenerated per publication | A schema grant cannot exclude one view; the split makes "the chat cannot spend quota" a grant fact and survives regenerated views (probe 1.1(c)); nothing else is reachable |
| Analyses in `CFB_PRED`, nothing added to `CFB_SIM` ([18]) | What-if views in `CFB_SIM` via `make sim` | Phase 4's deploy contract (object counts, orphan drops) stays untouched |
| Standalone scripts lock as kind `SIMULATE` ([19]) | A new run kind via another Phase 2 hand-off | Same stale rule (script); no new cross-plan edit |
| Probe first: the guide's latency on AWS at 1M and the platform facts ([23]) | Build, then measure | The one unmet target and the role model are decided before code depends on them |

## Features

| Feature | Status | Spec |
|---------|--------|------|
| analyses/predictions | NEW | `analyses/predictions/spec.md` |
| analyses/scorecard | NEW | `analyses/scorecard/spec.md` |
| analyses/upsets | NEW | `analyses/upsets/spec.md` |
| analyses/head-to-head | NEW | `analyses/head-to-head/spec.md` |
| analyses/published-odds | NEW | `analyses/published-odds/spec.md` |
| analyses/what-if | NEW | `analyses/what-if/spec.md` |
| analyses/rooting-guide | NEW | `analyses/rooting-guide/spec.md` |
| analyses/forced-resim | NEW | `analyses/forced-resim/spec.md` |
| analyses/deploy | NEW | `analyses/deploy/spec.md` |
| api/serving-contract | NEW | `api/serving-contract/spec.md` |
| api/roles | NEW | `api/roles/spec.md` |
| api/on-demand | NEW | `api/on-demand/spec.md` |

Domains: `analyses/` (the behavior of `CFB_PRED` and the steps; code in `analyses/`) and `api/` (the `CFB_API` and `CFB_API_LIVE` contract and the roles), as the orchestrator named them. `CLAUDE.md` lists `analyses/` already; `api/` is added by task 6.4.

The Phase 2, 3 and 4 specs are not changed by delta files (none is recorded yet). This plan uses their contracts and leaves recorder notes:
- `loader/refresh-week` and `loader/load-plan`: two more `refresh_steps` entries (200, 400), as anticipated there, written by the loader's deploy. Recorder note: the refresh-week examples already use 200 `PREDICT`.
- `loader/run-logs`: no new run kind; `PREDICT` and `PUBLISH_RUN` use `SIMULATE` (H1a) with their own `RUN_ARGS`.
- `loader/snapshot-restore`: scope unchanged. Recorder note: the restore recipe in the user guide ends with `make analyses`, which regenerates the what-if views for the restored point.
- `sim-engine/sim-runs`: unchanged. Recorder note: the user-guide retention text gains "the run of `CFB_PRED.WHATIF_PUBLICATION` must not be dropped; `make analyses` or the next publication restores the views". Hand-off P5-m13 (below) adds a guard.
- `sim-engine/win-probability`: unchanged; `CFB_PRED.UPSET` recomputes its Elo probability unrounded with the same formula and parameters, and its test pins the two together.
- `model/*`: read only.
- Foundation `make grants` (no recorded scenario covers roles beyond `CFB_READER`): extended, specified in `api/roles`.

## Requirements

| Requirement | Details |
|-------------|---------|
| Zero CFBD calls | Integration tests and probes: 0 (mock, replay, synthetic data; the harness guard against `CFBD_API` stays). No `CFB_PRED` or `CFB_API` object references the virtual schema (lint + dependencies); only `CFB_API_LIVE.GAME_PREVIEW` (and `QUOTA`'s free `/info`) reach CFBD. `make analyses-perf` never touches `CFB_API_LIVE` (unit-tested). Live verification: at most **6** ledgered calls for this plan (`make record --plan add-cfb-analyses --cap 6`), 3 planned (task 7.3), every one through the ledger; October total stays ≤ 400 |
| Shared DB etiquette | Probe 1.1, `make test-it` and the perf smoke only in "local free" windows, serialized with `flock <scratchpad>/cfb-it.lock`. AWS `default`: deploy and run only, never stop, start or resize; `PERF_`/`PROBE_` objects dropped after use. No DB session during planning |
| Layering | `CFB_PRED` reads `CFB_PRED`, `CFB_SIM`, `CFB_MODEL`; `CFB_API` adds three `CFB_META` log objects and nothing that reaches the network; `CFB_API_LIVE` reads only `GAMES_PREVIEW` and `CFB_META.QUOTA`; never `CFB_CORE`. Apps and the MCP user read only the serving schemas (roles) |
| Read-only consumers | `CFB_APP`: `SELECT ON SCHEMA CFB_API`, `CFB_API_LIVE`; `CFB_MCP`: `SELECT ON SCHEMA CFB_API` only. No write, no script, no other schema, no connection; proven per role by integration tests. Consumers read with autocommit (page contract) |
| Leak-free and honest | Predictions only from `GAME_SCORE_AS_OF` at each game's freeze point; first freeze wins; `FROZEN_BEFORE_KICKOFF` marks reconstructions, and `FREEZE_KIND` keeps them out of the headline; current season only. Grades read results only. The line comparison is labeled closing-line information. The what-if run never lies after the current point |
| Reproducibility | What-ifs and guides are deterministic functions of a stored run; publications freeze what was shown; the generated views and every what-if result row name their run |
| Self-description | 100 % comments on `CFB_PRED`, `CFB_API` and `CFB_API_LIVE` objects and columns, generated views included, grain first; `docs/api-dictionary.md` generated and diff-checked |
| Generated SQL | Every string literal in generated SQL goes through one escaping rule (`analyses/sqltext.py`, `analib.sql_str`); identifiers in generated SQL are built from integers only |
| Performance | The budget table above (targets, measured, misses logged with a decision); the rooting guide's F1/F2 path decided by probe 1.1(a) |
| Runtime code | SQL and Exasol Lua in the database; Python 3.12 stdlib for `analyses/whatif.py` (no new dependency); host tooling as before (pyexasol, PyYAML) |
| Data in git | Synthetic fixtures only; no preview body, recording or `RAW_JSON` value in tests, docs or fixtures; `docs/analyses-verification.md` holds derived numbers only (latencies, counts, ranks without raw rows) |
| Ownership | `sys` owns `CFB_PRED`, `CFB_API`, `CFB_API_LIVE` and the scripts on the real deployment; `CFB_TEST_OWNER` in `TEST_`/`PERF_`. One owner per deployment for every schema, because views run with their schema owner's rights |

## Dependencies

- **`add-cfb-score-engine` (Phase 4), implemented through group H** before this plan's group B: `CFB_SIM` with `SIM_RUN`, `SIM_RUN_GAME`, `SIM_RUN_TEAM`, `SIM_TEAM`, `LEVERAGE`, `SIM_TEAM_ODDS`, `GAME_SCORE_AS_OF`, `GAME_WIN_PROB`, `MARGIN_WIN_PROB`, `SCORE_MODEL_PARAMS`, `SIMULATE`, `SIMULATE_FROM`, `DROP_SIM_RUN`, step 300, the sim league and its harness helpers (`deploy_sim`, `seed_sim_league`, `set_score_params`, `set_sim_config`, `hold_deploy_lock`). Its probe 1.1c (layout C or A) fixes which branch `WHATIF_OUTCOMES` implements. Its group J (calibrated seeds, 1M runs on AWS) before this plan's group K.
  - **Hand-off P5-M3** (recorded in Phase 4's `review.md`): its per-run DDL escapes `'`, and its sim league names one Mountain West team `Hawai'i`. Task 1.5 checks it and has a fallback variant.
  - **Hand-off P5-m13** (new, for the orchestrator to add to Phase 4's `review.md`; review m13): `make sim-drop` refuses the run named by `CFB_PRED.WHATIF_PUBLICATION` unless `FORCE=yes`, and skips the check when `CFB_PRED` does not exist. It is a host-side check in `scripts/`, so no `CFB_SIM` object references `CFB_PRED`. If Phase 4 does not adopt it, the documented rule stays and the lag handling of `analyses/what-if` (`WHATIF_VIEWS_CURRENT` FALSE, no toggles, `make analyses` restores) limits the damage.
- **`add-cfb-analytical-model` (Phase 3)**: `CFB_MODEL` views, `vs/host/sqlobjects.py` (extended by Phase 4's 2.1 and by this plan's 2.1, sequentially), the dictionary generator, harness `deploy_model`. Its group K before group K here.
- **`add-cfb-persistence-refresh` (Phase 2)**: `REFRESH_WEEK` step registry and the loader deploy that writes it, `loadlib.lua` with H1a (`acquire_lock`/`release_lock`, kind `SIMULATE`) and H1b (host `DEPLOY` lock), `NOW_OVERRIDE`, `loader/bundle.py`, `make snapshot`/`make restore` (used by the restore scenario of `analyses/what-if`), the harness (`set_now`, `refresh`, recordings, snapshot helpers). Its group J (AWS backfill + snapshot) before group K. No new hand-off: this plan only adds `refresh_steps` entries and a recipe step in the user guide.
- **`add-cfbd-vs-foundation`** (recorded): `scripts/grants.py`, `scripts/record.py` (`--query`, `--plan`, `--cap`), the mock sidecar (extended for `/games/{id}/preview` bodies from `tests/synthetic/fake.py`), `vs/host/namespace.py`.
- **`add-engine-spikes`**: `spikes/s4/` (generator, layouts, queries, `LEVERAGE` SQL) is used by probe 1.1(a); it is untracked in the main checkout today. **Gate of group A: `spikes/s4/` committed** (by its own session) before probe 1.1(a) starts.
- **Phase 6/7 hand-offs** (contracts this plan states, decisions it leaves):
  - backend fetch logging for `GAME_PREVIEW` (needs `INSERT` on `FETCH_LOG` or another path);
  - an owner path for refresh, forced re-sims and publication from the app;
  - preview caching per session;
  - the page contract of `api/serving-contract` (autocommit, headline, "T-n", run guard, labels);
  - Phase 7 may grant `CFB_MCP` `SELECT` on `CFB_API_LIVE` if the chat should fetch previews; this plan does not.
- **Phase 8 hand-offs:**
  - the rehearsal recipe ("restore Sunday after week N") ends with `make analyses`, then `make simulate` + `make publish` at week N if the laptop has no week-N publication;
  - the rehearsal's scorecard headline will be the labeled reconstruction, unless predictions were frozen live during the season (`analyses/scorecard`).
- **User prerequisites:** U1 "local free" windows; U2 Phase 2's AWS snapshot for the local restore (Q8 in 3.3, and 7.4); U3 the owner reviews decision [14] (no `/teams/matchup`, Q13), which changes the roadmap's D13 head-to-head wording (task 6.3). U3 does not block any task.

## Migration

No data migration: `CFB_PRED`, `CFB_API` and `CFB_API_LIVE` are new.

| Current | New |
|---------|-----|
| `make deploy`: VS → loader → model → sim | VS → loader → model → sim → analyses |
| `loader/load-plan.yaml` `refresh_steps`: 100 `MODEL`, 300 `SIMULATE` | + 200 `PREDICT`, 400 `PUBLISH` (written by the loader deploy) |
| `make grants`: role `CFB_READER` | + roles `CFB_APP` (`SELECT ON SCHEMA CFB_API`, `CFB_API_LIVE`) and `CFB_MCP` (`SELECT ON SCHEMA CFB_API`), prefix-aware |
| `vs/host/namespace.py` | + `pred_schema`, `api_schema`, `api_live_schema`, `app_role`, `mcp_role`; renderer + `{PRED_SCHEMA}`, `{API_SCHEMA}`, `{API_LIVE_SCHEMA}` |
| `vs/host/sqlobjects.py` | + table-level allowed references per file, keep rule for the generated `WHATIF_*` views |
| `tests/mock_cfbd/` | + synthetic `/games/{id}/preview` bodies (schema-valid, from `fake.py`; with points for completed games) |
| `tests/it/fixtures/` | + `load-plan.analyses.yaml` (steps 100–400) |
| `tests/it/harness.py` | `drop_namespace`/`leftovers` cover `{prefix}CFB_PRED`, `{prefix}CFB_API`, `{prefix}CFB_API_LIVE`, the roles and `CFB_TEST_APP`; helpers `deploy_analyses`, `seed_analyses_history`, `complete_week`, `move_game` (week and kickoff of a stored game), `mark_prediction` (test fixture edits of `PREDICTION`), `publish`, `app_connection(role)`, `set_analysis_setting`, `snapshot_restore_at(week)` (over Phase 2's helpers) |
| `pyproject.toml` | mypy `files` + `analyses`; coverage source + `analyses` |
| `Makefile` | targets `analyses`, `predict`, `publish`, `what-if`, `api-docs`, `analyses-perf`; `coverage` reports `analyses` |

## Implementation Tasks

Tasks are TDD per `/speq:code-guardrails`. Host code writes its unit tests first. Each SQL/Lua task writes its integration test file first (failing), then the code. Lua has no unit tier. Integration tests run with `flock <scratchpad>/cfb-it.lock make test-it` in "local free" windows, at 0 CFBD calls.

**Who starts and who finishes each integration test file** (review m10). A scenario is written by the task that builds the last object it needs, never earlier. Task 2.2 deploys stub versions of the five Lua scripts (each returns 0 rows with the detail "stub"), so every registered step and every script exists from the first deploy; tasks 3.1 and 3.5 replace their bodies.

| File | Started by | Scenarios added later by | Green at the end of |
|---|---|---|---|
| `tests/it/test_analyses_deploy_it.py` | 2.2: full deploy, layering, changed data table, lock | 3.5: redeploy keeps history; 4.1: one command installs (object counts) | 4.1 |
| `tests/it/test_api_roles_it.py` | 2.4: grants create roles; cannot write or run scripts (stubs exist) | 3.5: regenerated views readable; 4.2: live preview for the app role; 4.6: app reads every view, chat role | 4.6 |
| `tests/it/test_predictions_it.py` | 3.1 (step 400 is the stub until 3.5) | — | 3.1 |
| `tests/it/test_scorecard_it.py` | 3.2 | — | 3.2 |
| `tests/it/test_upsets_it.py` | 3.3 | — | 3.3 |
| `tests/it/test_head_to_head_it.py` | 3.4: series | 3.6: next meeting, stakes | 3.6 |
| `tests/it/test_published_odds_it.py` | 3.5 | — | 3.5 |
| `tests/it/test_forced_resim_it.py` | 3.7 (against `CFB_PRED` objects) | — | 3.7 |
| `tests/it/test_game_preview_it.py` | 4.2 | — | 4.2 |
| `tests/it/test_api_contract_it.py` | 4.1 | 4.4: the dictionary | 4.4 |
| `tests/it/test_whatif_it.py`, `test_rooting_guide_it.py` | 4.3 | — | 4.3 |

1. **Probe, scaffolding and pure code** (no DB except 1.1)
   - [ ] 1.1 Probe `docs/spikes/s8_analyses_probe.py`, findings in `docs/spikes/s8-analyses-probe.md`. It runs as sys on `PROBE_*` objects, synthetic data only, 0 calls. Timed items follow the S4 protocol. **Gate: `spikes/s4/` is committed** before (a) starts.
     - (a) **Rooting guide and what-if latency (decisive).** On S4's synthetic family (136 teams, 400 remaining games, layout C, sparse `SIM_TEAM`, `LEVERAGE`; regenerated with `spikes/s4/`), at 100k locally and 1M on AWS `default` (deploy and run only):
       - the conditional guide over 60 columns ("next week" = the first 60 games) at k = 1 and 3, in the library's formulation (one aggregate pass, the `IN (SELECT …)` flag inside `SUM(CASE …)`, `CASE` unpivot);
       - the alternative: S4's measured two-pass shape (`per_game_counts` twice, `spikes/s4/queries.py`) cut to the same 60 columns;
       - the flag: that Exasol accepts the predicate subquery in the select list and evaluates it once (a semi-join in `EXA_STATISTICS`' profile), not per row; else the `LEFT JOIN` flag;
       - the 400-column guide at k = 3 (S4 re-baseline, expected ≈ 2.2 s at 1M);
       - F1 (top 25 by unconditional |leverage|, with its `LEVERAGE` lookup) at k = 1 and 3;
       - the all-team what-if at k = 1, 3, 8 through an alias view with `G<id>` names, a literal run id and the run guard, against S4's direct text;
       - one warm repeat with the query cache on (Q10).

       **Gate:** the 60-column guide p95 < 1 s on AWS and < 0.5 s locally, **at k = 1 and at k = 3** → design as planned. Else F1 if it passes, else F2. Logged as a decision either way.
     - (b) Locally in the `TEST_` namespace with the mock (IT lock):
       - a view over a virtual table, with `P_GAME_ID` aliased to `GAME_ID`, in a schema owned by the test owner (a schema's objects belong to its owner, so this checks whose rights apply);
       - a user with only `SELECT ON SCHEMA` of the view's schema queries it with `GAME_ID = <literal>`: one request, rows, no privilege on the virtual schema or the connection;
       - without the filter and with a subquery: refused naming `gameId`, 0 requests;
       - `EXPLAIN VIRTUAL` through the view shows the manifest;
       - an `IN` list of 11 is refused.
     - (c) `GRANT SELECT ON SCHEMA` to a role, then `CREATE OR REPLACE VIEW` in that schema: the grantee reads the new view. With two probe schemas, a role granted one cannot read the other. The grantee cannot read any other schema, insert, or `EXECUTE SCRIPT`.
     - (d) A generated view with 900 `BOOLEAN` aliases named `G<9-digit id>`, each with a comment containing an escaped apostrophe, over a 900-column table, its text built by iterating rows in Lua: success and DDL time. (No `LISTAGG`; review m12.)
     - (e) View regeneration inside a transaction with DML, then `ROLLBACK`: the old view text and the rows return. Then, while a regeneration with `PUBLISHED_ODDS`-like inserts commits, record for each reader whether it waits, conflicts (rollback) or succeeds, and what it sees:
       - a reader of the view with autocommit on;
       - a reader with autocommit off that read the views and the odds table before the publication and reads again after it (a pooled backend or MCP session);
       - a reader of the odds table through a `PLAYOFF_ODDS`-like view during the commit;
       - a long guide query (≈ 2 s: the 400-column guide) running across the commit.

     **Gates:**
     - (a) fixes the guide (as planned, F1 or F2) and the flag's form;
     - (b) fixes the role model (Q2) and the preview view shape (Q4);
     - (c) the schema grants (Q3);
     - (d) the DDL budget of step `PUBLISH`;
     - (e) the publication's retry rule (Q5) and the page contract's autocommit requirement.
   - [ ] 1.2 `Namespace.pred_schema`, `api_schema`, `api_live_schema`, `app_role`, `mcp_role`; placeholders `{PRED_SCHEMA}`, `{API_SCHEMA}` and `{API_LIVE_SCHEMA}` in the renderer. Unit tests in `tests/unit/test_namespace.py` and `test_meta_ddl.py`.
   - [ ] 1.3 `analyses/whatif.py`: `Condition`, `parse_conditions`, `what_if_odds_sql`, `rooting_guide_sql` (both with `run_id` and the run guard), `ConditionError`, in the formulation probe 1.1(a) adopts. [expert]
     - Test first: `tests/unit/test_whatif_sql.py`.
     - The invalid-conditions scenario: unknown game, duplicate, 17 conditions, non-integer and `bool` ids, a non-integer team, a non-integer run id.
     - Shapes: quoting, only integers and `TRUE`/`FALSE` in the text, the run guard in `"R"` and `"M"` and `RUN_ID` in the select list, conditioned games dropped from the guide's games, the empty-conditions form, `api_schema` rendering.
     - Determinism: identical input gives identical text.
   - [ ] 1.4 Host oracles `tests/analyses/oracle.py`: conditional counts per team, guide branches per game, series and streaks, ranks with ties (on unrounded values, with an all-time window), and the scorecard measures, buckets and freeze kinds (including a game played in another week). Pure functions on row lists. Unit tests `tests/unit/test_analyses_oracle.py` on hand-made cases (ties in ranks, empty sample, streak across a tie, bucket edge 0.9, the three freeze-kind rows adding up).
   - [ ] 1.5 History variant, `with_fcs_only_game` and the apostrophe check in `tests/synthetic/analyses_history.py` per Key Interfaces, on Phase 4's `sim_league()`. Unit tests `tests/unit/test_analyses_history.py`:
     - deterministic output;
     - only seasons before 1998 in the history variant, one 2026 FCS-only game in the other, game ids in the reserved range;
     - mirror rows typed per catalog;
     - the seven named games with their Elo, and the magnitudes 0.99976964 (1898, 1985) and 0.99976292 (1987) recomputed;
     - no ERROR rule of Phase 3's mirrored checks violated;
     - `sim_league()` has the Mountain West school `Hawai'i` with remaining 2026 games after week 9 (hand-off P5-M3); if not, the variant `with_apostrophe_school` provides it and this test covers the variant instead.
   - [ ] 1.6 Seeds and plan entries:
     - `sql/pred/010_analysis_settings.sql` (`MIN_EFFECTIVE_SAMPLE` 1000, `MIN_BRANCH_SAMPLE` 100, `MOVE_THRESHOLD` 0.0070, `ALL_TIME_FROM_SEASON` 1978, each with its reason in `SETTING_NOTE`);
     - `loader/load-plan.yaml` steps 200 and 400; the plan variant `tests/it/fixtures/load-plan.analyses.yaml` (steps 100–400 with `TEST_` scripts);
     - confirm that no Phase 3 or Phase 4 integration test deploys the project YAML (review m9); give any that does a variant without steps 200 and 400, and record it.

     Unit tests `tests/unit/test_analyses_seeds.py`: the four settings with their values, `TEST_FAIL_PUBLISH` absent from the file; the load plan's steps 100–400 unique and ordered (extends Phase 2's load-plan unit test); the variant's steps.
   - [ ] 1.7 `analyses/sqltext.py` (`sql_str`, the one escaping rule) and `analyses/whatif_ddl.py` (the reference renderer: the generated views' column names, order and comments and the view comment for a list of game rows and a publication, and the three empty forms). Unit tests first:
     - `tests/unit/test_sqltext.py`: one and two apostrophes, `Hawai'i` → `'Hawai''i'`, an empty string, no other character changed, `None` refused;
     - `tests/unit/test_whatif_ddl.py`: columns in game-index order whatever the input order, `G<id>` names from integers only (a non-integer id refused), comment texts for `Hawai'i` escaped in the SQL text and plain in the comment value, the empty forms' columns equal to the templates' fixed columns, determinism.

2. **Deploy, roles**
   - [ ] 2.1 Extend `vs/host/sqlobjects.py` (sequential after Phase 4's 2.1, no parallel edits):
     - table-level allowed references per file (`{META_SCHEMA}.REFRESH_LOG`, `LOAD_RUN`, `FETCH_LOG` for `sql/api/`; `{VS_NAME}.GAMES_PREVIEW` and `{META_SCHEMA}.QUOTA` per file for `sql/api_live/`);
     - a keep rule for the generated `WHATIF_*` views.

     Extend `tests/unit/test_sqlobjects.py`.
   - [ ] 2.2 `analyses/deploy.py`, `scripts/analyses.py deploy`, `make analyses`, and the call from `scripts/deploy.py` after the sim deploy:
     - one transaction in the order of `analyses/deploy`: `CFB_PRED`, the empty generated views where missing (rendered by 1.7's `whatif_ddl.py`), `CFB_API`, `CFB_API_LIVE`, the scripts; then commit and `WHATIF_VIEWS`;
     - stub versions of the five Lua entry files in `analyses/lua/` (0 rows, detail "stub"), bundled through `loader/bundle.py` entries;
     - the run lock as kind `DEPLOY` (H1b), `--setting` overrides through `sql_str` (test hooks only in `TEST_`);
     - the report, with the registered steps and their scripts (warnings per `analyses/deploy`).

     Harness helpers per Migration (all of them here, so later tasks do not edit `harness.py` in parallel). Unit tests `tests/unit/test_analyses_deploy.py` (fake connection: statement order, the empty forms only when missing, escaping of `--setting` values) and `tests/cli/test_analyses_cli.py`. Integration test file first: `tests/it/test_analyses_deploy_it.py` (its scenarios per the table above).
   - [ ] 2.3 Data tables `sql/pred/data/{prediction,publication,published_odds}.sql`, every column commented (covered by 2.2's tests).
   - [ ] 2.4 `scripts/grants.py`: roles `CFB_APP` (`SELECT ON SCHEMA` `CFB_API` and `CFB_API_LIVE`) and `CFB_MCP` (`CFB_API` only), prefix-aware and idempotent; the role checks of probe 1.1(b, c) as fallbacks. Extend `tests/unit/test_grants.py`. Integration test file first: `tests/it/test_api_roles_it.py` (its first scenarios per the table above).

3. **Analyses (`CFB_PRED` and the steps)**
   - [ ] 3.1 Predictions: `analyses/lua/analib.lua` (point resolution, the current-season check, the loader clock, `freeze` per point oldest first, `sql_str`), `refresh_predict.lua`, `predict.lua` (replacing the stubs). Integration tests first: `tests/it/test_predictions_it.py`.
   - [ ] 3.2 Scorecard views `100`–`130`: freeze kinds, the week played, `IS_HEADLINE`. Integration tests first: `tests/it/test_scorecard_it.py`, using `complete_week`, `move_game`, `mark_prediction` and the oracle.
   - [ ] 3.3 Upset view `200`: `RANK_MAGNITUDE`, the all-time window, tie flags. Integration tests first: `tests/it/test_upsets_it.py` (history variant, oracle ranks, `set_analysis_setting`).
     - **Q8 check, moved here from 7.2** (review M5): read-only SQL at 0 calls, the top 20 by `ALL_TIME_RANK` and by `LINE_ERA_RANK` with season, source, magnitude and the number of tied rows, plus spot checks of well-known upsets in the top 100. On the locally restored Phase 2 snapshot (U2) with Phases 3 and 4 deployed. If that is not available yet, run it on synthetic data for the mechanics only and leave Q8 open until 7.2.
     - Keep `ALL_TIME_FROM_SEASON` 1978 or change it, with a logged decision in this plan's decision log either way.
   - [ ] 3.4 Series view `400`. Integration tests first: `tests/it/test_head_to_head_it.py` (series, mirror, streak, never met).
   - [ ] 3.5 Publication: `analib.publish`, `refresh_publish.lua`, `publish_run.lua` and `whatif_views.lua` (replacing the stubs) with the templates `sql/api/generated/`; views `300` `POINT_PUBLICATION`, `305` `WHATIF_PUBLICATION`, `310` `ODDS_MOVEMENT` and `320` `ROOTING_GUIDE` (fixed games dropped, `RUN_ORIGIN`, `FIXED_RESULTS`). [expert]
     - The eligibility checks, the point after the current one included.
     - The single transaction with `ROLLBACK` on any error; the regeneration decided by reading `WHATIF_PUBLICATION` inside it.
     - The generator: the layout branch fixed by Phase 4's 1.1c, rows `ORDER BY GAME_IDX`, every string through `analib.sql_str`, output equal to `whatif_ddl.py`'s; the retry rule of 1.1(e).
     - The test hook.

     Integration tests first: `tests/it/test_published_odds_it.py`. Adds its scenarios to `test_api_roles_it.py` and `test_analyses_deploy_it.py`.
   - [ ] 3.6 `MATCHUP_NEXT` (`410`), which needs 3.1 and 3.5. Its scenarios go into `tests/it/test_head_to_head_it.py`.
   - [ ] 3.7 `FORCED_RUN_ODDS` (`330`), built last among the analyses because it serves no demo beat. Integration tests first: `tests/it/test_forced_resim_it.py`, against `CFB_PRED.FORCED_RUN_ODDS` and `CFB_PRED.ROOTING_GUIDE`.

4. **Serving schemas (`CFB_API`, `CFB_API_LIVE`)**
   - [ ] 4.1 The 23 file views of `sql/api/`, with columns per `api/serving-contract`: `CURRENT_POINT` with the `WHATIF_*` columns and `WHATIF_VIEWS_CURRENT`, `WHATIF_GAMES` with its guard, `ANALYSIS_SETTINGS`, and the pass-through views `700`–`730` with the sources' column comments. Integration tests first: `tests/it/test_api_contract_it.py`. Adds "one command installs" to `test_analyses_deploy_it.py`.
   - [ ] 4.2 `sql/api_live/010_game_preview.sql` (shape per probe 1.1(b)) and `020_quota.sql`, and the mock's preview bodies (points for completed games). Integration tests first: `tests/it/test_game_preview_it.py`, including a synthetic replay recording in BucketFS. Adds the live-preview scenario to `test_api_roles_it.py`.
   - [ ] 4.3 What-if and guide integration: run the library's SQL as `CFB_TEST_APP` against the generated views; compare with the oracle; the run guard, the apostrophe and the restore scenarios. Integration tests first: `tests/it/test_whatif_it.py` and `tests/it/test_rooting_guide_it.py`.
   - [ ] 4.4 `analyses/docs.py` and `make api-docs` → `docs/api-dictionary.md`, both serving schemas. Unit test `tests/unit/test_api_dictionary.py` (the committed file equals the generated one); the database comparison goes into `test_api_contract_it.py`.
   - [ ] 4.5 Host CLI: `make predict`, `make publish`, `make what-if` (parses `FIX`, resolves `TEAM` by school or id through `CFB_API.TEAMS`, takes the run from `CFB_API.CURRENT_POINT`, prints with seconds). CLI tests `tests/cli/test_analyses_cli.py`.
   - [ ] 4.6 Roles over the full contract: the remaining scenarios of `tests/it/test_api_roles_it.py` (the app role reads every view of both schemas and nothing else; the chat role reads `CFB_API`, is refused on `CFB_API_LIVE`, and the mock gets no request).

5. **Performance**
   - [ ] 5.1 `analyses/perf.py` and `make analyses-perf`, with a fixed query catalog over `CFB_API` only. Unit test first: `tests/unit/test_analyses_perf.py` asserts that no catalog query names `GAME_PREVIEW`, `QUOTA`, `CFB_API_LIVE`, `{API_LIVE_SCHEMA}` or a virtual schema (review M6). Local smoke in a "local free" window:
     - seed `sim_league(scale="real")` into `PERF_`, deploy, 3 × `make simulate SIMS=100000 BATCH=5000` at 2026 week 9 plus `make publish`;
     - every budget row at 100k except the live ones, with the S4 protocol, plus `REFRESH_WEEK` in replay with steps 200–400 (< 2 min);
     - record sizing; drop `PERF_`. Draft `docs/analyses-verification.md`.

6. **Documentation** (each file re-read first; `docs/roadmap.md`, `specs/architecture.md` and `CLAUDE.md` are edited only here)
   - [ ] 6.1 `docs/user-guide.md`, section "Analyses and the serving API":
     - the weekly chain (200/300/400);
     - `make predict`, `make publish`, `make what-if`, `make analyses`; the restore recipe ends with `make analyses`;
     - frozen and reconstructed predictions, freeze kinds and the headline; reading the scorecard (populations, the closing-line caveat); upset ranks since 1978, sources and "T-n";
     - head-to-head from stored games and the preview of the next or latest meeting, one call per click;
     - what-if semantics (conditioning vs forcing, the forced guide's label), effective sample and thresholds, the run guard;
     - the what-if publication and why its run must not be dropped;
     - creating the backend and MCP users with the roles (no passwords in files); why the chat role has no `CFB_API_LIVE`; autocommit for consumers;
     - the zero-call guarantee and the preview's quota cost.
   - [ ] 6.2 `specs/architecture.md`:
     - § Layers and schemas: `CFB_PRED` contents (data tables, views), `CFB_API` (26 views, 3 generated, 4 pass-through, no tables or functions, no network) and `CFB_API_LIVE` (2 views);
     - the layering rule with both allowlists; the escaping rule for generated SQL;
     - § Analyses: publication, `WHATIF_PUBLICATION`, the steps 200/300/400 and the generated what-if views;
     - § Security model: roles `CFB_APP` (both serving schemas) and `CFB_MCP` (`CFB_API`), replacing "+ execute on its functions";
     - § Design questions: the conditional rooting guide resolved by [6] with probe 1.1(a)'s numbers.
   - [ ] 6.3 `docs/roadmap.md`:
     - decisions marked `Promotes to ADR: yes`, numbered after the last D-number present;
     - Phase 5 bullets: head-to-head from stored games and the preview as the live beat (replacing "all-time series on demand via `/teams/matchup`"); the publication step; frozen predictions with reconstruction; `CFB_API` and `CFB_API_LIVE`;
     - the D13 head-to-head wording and the API call budget row ("Head-to-head … 1 per click" → "Game preview … 1 per click; series 0"), subject to U3;
     - Demo flow beat 4 wording ("upset of the week ranked since 1978"); mark Phase 5 progress;
     - backlog: bowl and playoff predictions, `PROMOTE()` for previews, what-ifs on older points, app-triggered forced re-sims, upset rank as of the time, more chat pass-through views.
   - [ ] 6.4 `CLAUDE.md` dense bullets:
     - domains `analyses/` and `api/`, code `analyses/`, schemas `CFB_PRED`, `CFB_API`, `CFB_API_LIVE`;
     - `make analyses`/`predict`/`publish`/`what-if`/`api-docs`/`analyses-perf`; `make analyses` after a restore;
     - the what-if publication rule, "never drop its run";
     - the library as the only builder of what-if SQL; `sql_str` as the only way a string enters generated SQL;
     - roles read-only, schema grants, the chat without `CFB_API_LIVE`;
     - this plan's ledger cap.

     `specs/mission.md`:
     - § Commands (new targets); § Project Structure (`analyses/`, `sql/pred`, `sql/api`, `sql/api_live`);
     - Capability 9 (series from stored games; the live preview on click); the upsets capability ("ranked since 1978, the first Division I-A season");
     - Glossary: "Publication / published run", "What-if publication", "Freeze point", "Freeze kind".

7. **Live verification** (last; AWS `default` deploy and run only; local in a "local free" window)
   - [ ] 7.1 Gates and deploy:
     - gates: Phase 4 group J done (calibrated seeds, 1M runs on AWS) and Phase 3 group K done; record `make quota TARGET=aws` and the ledger totals (`make record ARGS="--ledger"`);
     - `make deploy TARGET=aws` and `make grants TARGET=aws`;
     - `make publish TARGET=aws RUN=<newest 1M run at the current point>` and `make predict TARGET=aws SEASON=2026 WEEK=<current>`.
   - [ ] 7.2 AWS measurements on real data (S4 protocol): every budget row at 1M except the live ones; the coming week's game count (Q7); Q8 repeated (the top 20 by `ALL_TIME_RANK` and by `LINE_ERA_RANK`, tie counts); `PUBLISH` and `PREDICT` step seconds; record sizing. Results go to `docs/analyses-verification.md` as derived numbers.
   - [ ] 7.3 Live calls, at most 6 for this plan, **3 planned**, every one through `make record TARGET=aws CONFIRM=yes ARGS="--plan add-cfb-analyses --cap 6 --query \"…\""`; `make dns-check TARGET=aws` first. Run them in a window agreed with the orchestrator in which no other session spends calls (review m11); note any other ledger entry in the window.
     - 2 × `SELECT * FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = <a game of the coming week>` (two different games);
     - 1 × the same for the latest meeting of a pair with no meeting left this season (`CFB_API.MATCHUP_SERIES.LAST_GAME_ID`): CFBD answers for a played game (Q15);
     - end-to-end seconds and `REQ_MS` of all three, median judged against 2 s (Q6);
     - the recent meetings in the three `RAW_JSON` values compared on the host with `CFB_API.MATCHUP_GAMES` (game ids, scores): stored series = CFBD's, at no extra call. Only the result and the counts are written down; no `RAW_JSON` is saved;
     - **optional**, only if no `RAW_JSON` carries recent meetings: 1 × `SELECT TEAM1_WINS, TEAM2_WINS, TIES FROM CFBD_VS.TEAMS_MATCHUP WHERE P_TEAM1 = 'Michigan' AND P_TEAM2 = 'Ohio State'`, compared with `CFB_API.MATCHUP_SERIES` (still within the cap).
   - [ ] 7.4 Local:
     - `make restore TARGET=local NAME=<Phase 2 snapshot> CONFIRM=yes` if needed, then `make deploy` and `make grants` (the restore recipe's `make analyses` is part of `make deploy`);
     - `make simulate` (100k) and `make publish` at the current point; `REFRESH_WEEK` of the next recorded week in replay (< 2 min, steps 200–400);
     - the budget rows at 100k except the live ones;
     - `CFB_API_LIVE.GAME_PREVIEW` in replay for the locally recorded game (`make record ARGS="--publish-local"`, 0 calls).
   - [ ] 7.5 Zero-call proof and write-up (review m11):
     - the ledger entries for `add-cfb-analyses` are ≤ 6 (3 planned) and match one to one the `FETCH_LOG` rows that those `make record` runs wrote (path and time);
     - the `/info` drop over 7.3's window equals the sum of every ledger entry in that window, each listed with its plan;
     - `FETCH_LOG` has no row from tests;
     - `docs/analyses-verification.md` complete (derived numbers only), with the demo beat-4 numbers.

## Parallelization

| Parallel Group | Tasks |
|----------------|-------|
| Group A | 1.1, 1.2, 1.4, 1.5, 1.6, 1.7 |
| Group A2 | 1.3 |
| Group B | 2.1, 2.3 |
| Group C | 2.2 |
| Group D | 2.4, 3.1, 3.4 |
| Group E | 3.2, 3.3, 3.5 |
| Group F | 3.6, 3.7, 4.2 |
| Group G | 4.1 |
| Group H | 4.3, 4.4, 4.5, 4.6 |
| Group I | 5.1 |
| Group J | 6.1, 6.2, 6.3, 6.4 |
| Group K (sequential, gated) | 7.1 → 7.2 → 7.3 → 7.4 → 7.5 |

Sequential dependencies:
- A → B → C → D → E → F → G → H → I → J → K. A2 runs alongside B–G and must be done before H.
- 1.1(a) waits for `spikes/s4/` to be committed. 1.3 (A2) waits for 1.1(a): the adopted formulation is part of its tests. 1.1 needs a "local free" window for (b)–(e) and AWS for (a).
- Before group B: Phase 4 groups A–H (its 1.1c layout decision and hand-off P5-M3 included), Phase 3's deployer and dictionary, Phase 2's 4.2/5.2 with H1. 2.1 follows Phase 4's 2.1 on `sqlobjects.py`.
- 2.2 needs 1.2, 1.6, 1.7, 2.1 and 2.3, and adds every harness helper and the script stubs.
- Group D touches disjoint files: `scripts/grants.py` + `test_api_roles_it.py`; `analyses/lua/analib.lua` + predict scripts; `sql/pred/400`.
- Group E touches disjoint files: `sql/pred/100`–`130`; `sql/pred/200`; the publish scripts, `whatif_views.lua`, `sql/api/generated/`, `sql/pred/300`–`320` and the scenarios 3.5 adds to two started files. 3.2 and 3.3 need 3.1's predictions; 3.5 needs 3.1 (it extends `analib.lua`) and 1.1(d, e).
- Group F touches disjoint files: `sql/pred/410`; `sql/pred/330`; `sql/api_live/` and the mock. 3.6 needs 3.1 and 3.5; 3.7 needs 3.5 (`ROOTING_GUIDE`); 4.2 needs 1.1(b) and 2.2.
- 4.1 needs every `CFB_PRED` view (3.2–3.7). Group H: 4.3 needs 1.3, 1.4, 3.5 and 4.1; 4.4 needs 4.1 and 4.2; 4.5 needs 1.3, 3.1 and 3.5; 4.6 needs 4.1 and 4.2. Their files are disjoint (`test_whatif_it.py` + `test_rooting_guide_it.py`; `analyses/docs.py` + `test_api_contract_it.py`; the CLI; `test_api_roles_it.py`).
- 5.1 needs every task of groups A–H and a "local free" window. Group J needs green A–I. Group K needs J, Phase 4 group J, Phase 3 group K, Phase 2 group J, U1 and U2. U3 is an owner review that does not block.

## Dead Code Removal

| Type | Location | Reason |
|------|----------|--------|
| — | none | This plan adds code only. `spikes/s4/` is read by probe 1.1(a) and stays as D20's evidence. No `/teams/matchup` code exists to remove; the virtual table stays in the catalog |

## Verification

### Scenario Coverage

| Scenario | Test Type | Test Location | Test Name |
|----------|-----------|---------------|-----------|
| predictions: The refresh freezes next week's predictions before the simulation | Integration | `tests/it/test_predictions_it.py` | `test_refresh_freezes_next_week_before_simulation` |
| predictions: A prediction names the pick and the predicted score | Integration | `tests/it/test_predictions_it.py` | `test_prediction_pick_and_score` |
| predictions: A frozen prediction never changes | Integration | `tests/it/test_predictions_it.py` | `test_frozen_prediction_never_changes` |
| predictions: Missing weeks are reconstructed at their own point and marked | Integration | `tests/it/test_predictions_it.py` | `test_missing_weeks_reconstructed_and_marked` |
| predictions: Points outside the current season freeze nothing | Integration | `tests/it/test_predictions_it.py` | `test_points_outside_current_season_freeze_nothing` |
| predictions: Games without an FBS team are not predicted | Integration | `tests/it/test_predictions_it.py` | `test_games_without_fbs_team_not_predicted` |
| predictions: A standalone prediction call is refused while another run holds the lock | Integration | `tests/it/test_predictions_it.py` | `test_predict_refused_while_locked` |
| scorecard: Last week's predictions are graded against the results | Integration | `tests/it/test_scorecard_it.py` | `test_week_graded_against_results` |
| scorecard: Predictions made in advance and reconstructions are graded apart | Integration | `tests/it/test_scorecard_it.py` | `test_freeze_kinds_graded_apart` |
| scorecard: A rescheduled game is graded in the week it is played | Integration | `tests/it/test_scorecard_it.py` | `test_rescheduled_game_graded_in_week_played` |
| scorecard: A game without a result is not graded | Integration | `tests/it/test_scorecard_it.py` | `test_game_without_result_not_graded` |
| scorecard: Ties and games without a pick are neither correct nor wrong | Integration | `tests/it/test_scorecard_it.py` | `test_ties_and_no_pick` |
| scorecard: The season so far adds up the weeks | Integration | `tests/it/test_scorecard_it.py` | `test_season_to_date_adds_weeks` |
| scorecard: Calibration buckets compare the claimed and the actual win shares | Integration | `tests/it/test_scorecard_it.py` | `test_calibration_buckets` |
| scorecard: The line is graded on the same games as the model | Integration | `tests/it/test_scorecard_it.py` | `test_line_graded_on_same_games` |
| upsets: The week's upsets are ranked against all of history | Integration | `tests/it/test_upsets_it.py` | `test_week_upsets_ranked_all_time` |
| upsets: An upset from the Elo era ranks next to the line era | Integration | `tests/it/test_upsets_it.py` | `test_elo_era_upset_ranks` |
| upsets: Ranks use the magnitude before rounding | Integration | `tests/it/test_upsets_it.py` | `test_ranks_use_unrounded_magnitude` |
| upsets: Upsets before the all-time window rank within their season only | Integration | `tests/it/test_upsets_it.py` | `test_all_time_window_and_ties` |
| upsets: The line-era rank counts only games with a line | Integration | `tests/it/test_upsets_it.py` | `test_line_era_rank` |
| upsets: Ties and games without a probability are not ranked | Integration | `tests/it/test_upsets_it.py` | `test_ties_and_missing_probability_not_ranked` |
| upsets: Our own prediction is shown next to the upset | Integration | `tests/it/test_upsets_it.py` | `test_model_probability_beside_upset` |
| head-to-head: The all-time series comes from stored games without a call | Integration | `tests/it/test_head_to_head_it.py` | `test_series_from_stored_games_zero_calls` |
| head-to-head: Every pair has a mirror row | Integration | `tests/it/test_head_to_head_it.py` | `test_series_mirror_rows` |
| head-to-head: The streak and the biggest results follow the meetings | Integration | `tests/it/test_head_to_head_it.py` | `test_streak_and_biggest_results` |
| head-to-head: The next meeting carries our prediction | Integration | `tests/it/test_head_to_head_it.py` | `test_next_meeting_prediction_current_then_frozen` |
| head-to-head: The next meeting shows what it means for both teams' playoff odds | Integration | `tests/it/test_head_to_head_it.py` | `test_next_meeting_stakes` |
| head-to-head: Teams that never met have no series | Integration | `tests/it/test_head_to_head_it.py` | `test_never_met_no_series` |
| published-odds: The refresh publishes its simulation | Integration | `tests/it/test_published_odds_it.py` | `test_refresh_publishes_its_run` |
| published-odds: The arrows show the movement since the last published week | Integration | `tests/it/test_published_odds_it.py` | `test_movement_since_last_published_week` |
| published-odds: The movement survives dropping old runs | Integration (CLI + DB) | `tests/it/test_published_odds_it.py` | `test_movement_survives_sim_drop` |
| published-odds: A refresh without a simulation publishes nothing | Integration | `tests/it/test_published_odds_it.py` | `test_refresh_without_simulation_publishes_nothing` |
| published-odds: A manual run is published on request, and the latest publication of a point wins | Integration (CLI + DB) | `tests/it/test_published_odds_it.py` | `test_manual_publication_latest_wins` |
| published-odds: Runs that must not be shown are refused | Integration | `tests/it/test_published_odds_it.py` | `test_ineligible_runs_refused` |
| published-odds: Publishing an older point leaves the what-if views on the newest point | Integration | `tests/it/test_published_odds_it.py` | `test_older_point_keeps_whatif_on_newest` |
| published-odds: A failed publication leaves nothing behind | Integration | `tests/it/test_published_odds_it.py` | `test_failed_publication_rolls_back` |
| what-if: The what-if views expose every simulated season of the published run | Integration | `tests/it/test_whatif_it.py` | `test_whatif_views_expose_run` |
| what-if: School names with an apostrophe are quoted in the generated views | Integration | `tests/it/test_whatif_it.py` | `test_apostrophe_school_quoted_and_matches_renderer` |
| what-if: Conditional odds count only the matching seasons | Integration | `tests/it/test_whatif_it.py` | `test_conditional_odds_match_oracle` |
| what-if: A small effective sample is flagged | Integration | `tests/it/test_whatif_it.py` | `test_low_sample_flag` |
| what-if: Conditions that no season matches give zero counts | Integration | `tests/it/test_whatif_it.py` | `test_unmatched_conditions_zero_counts` |
| what-if: A what-if built for an earlier run returns no rows | Integration | `tests/it/test_whatif_it.py` | `test_stale_run_returns_no_rows` |
| what-if: Invalid conditions are refused before any SQL is built | Unit | `tests/unit/test_whatif_sql.py` | `test_invalid_conditions_refused` |
| what-if: Before the first publication the what-if views are empty | Integration | `tests/it/test_whatif_it.py` | `test_whatif_views_empty_before_publication` |
| what-if: After a restore the what-if follows the restored point | Integration (CLI + DB) | `tests/it/test_whatif_it.py` | `test_restore_moves_whatif_to_restored_point` |
| rooting-guide: The unconditional guide reads the precomputed counts | Integration | `tests/it/test_rooting_guide_it.py` | `test_unconditional_guide_from_leverage` |
| rooting-guide: The guide marks the coming week's games and the team's own games | Integration | `tests/it/test_rooting_guide_it.py` | `test_guide_marks_next_week_and_own_games` |
| rooting-guide: With what-if conditions the guide counts only the matching seasons | Integration | `tests/it/test_rooting_guide_it.py` | `test_conditional_guide_matches_oracle` |
| rooting-guide: Without conditions the computed guide equals the precomputed one | Integration | `tests/it/test_rooting_guide_it.py` | `test_computed_guide_equals_precomputed` |
| rooting-guide: A game fixed by a condition leaves the conditional guide | Integration | `tests/it/test_rooting_guide_it.py` | `test_conditioned_game_leaves_guide` |
| forced-resim: A forced re-sim's odds are shown next to its base run's | Integration | `tests/it/test_forced_resim_it.py` | `test_forced_odds_beside_base` |
| forced-resim: A forced run has its own full-season rooting guide | Integration | `tests/it/test_forced_resim_it.py` | `test_forced_run_guide_drops_forced_game` |
| forced-resim: The read-only roles cannot start a forced re-sim | Integration | `tests/it/test_forced_resim_it.py` | `test_roles_cannot_force_resim` |
| deploy: One command installs the analyses and the serving schemas | Integration (CLI + DB) | `tests/it/test_analyses_deploy_it.py` | `test_make_analyses_installs` |
| deploy: A redeploy keeps predictions and publications | Integration (CLI + DB) | `tests/it/test_analyses_deploy_it.py` | `test_redeploy_keeps_history` |
| deploy: The full deploy includes the analyses after the simulation schema | Integration | `tests/it/test_analyses_deploy_it.py` | `test_full_deploy_includes_analyses` |
| deploy: The analyses and the API read only what they may | Integration (CLI + DB) | `tests/cli/test_analyses_cli.py`, `tests/it/test_analyses_deploy_it.py` | `test_layering_lint_names_reference`, `test_deployed_dependencies_allowed` |
| deploy: A changed data table stops the deploy | Integration | `tests/it/test_analyses_deploy_it.py` | `test_changed_data_table_stops_deploy` |
| deploy: A deploy is refused while a run holds the lock | Integration | `tests/it/test_analyses_deploy_it.py` | `test_deploy_refused_while_locked` |
| serving-contract: The serving schemas hold exactly the documented views, each described by its grain | Integration | `tests/it/test_api_contract_it.py` | `test_api_objects_and_comments` |
| serving-contract: No serving view reads the raw mirrors or a broad API table | Integration | `tests/it/test_api_contract_it.py` | `test_api_direct_references` |
| serving-contract: The current point tells the pages what to show by default | Integration | `tests/it/test_api_contract_it.py` | `test_current_point` |
| serving-contract: The as-of points say where each page has data | Integration | `tests/it/test_api_contract_it.py` | `test_as_of_points_flags` |
| serving-contract: The refresh timeline shows every step of the weekly chain | Integration | `tests/it/test_api_contract_it.py` | `test_refresh_timeline_steps` |
| serving-contract: The chat can answer questions outside the pages from the model views | Integration | `tests/it/test_api_contract_it.py` | `test_pass_through_views_equal_model` |
| serving-contract: The data dictionary matches the deployed schema | Integration (CLI + DB) | `tests/it/test_api_contract_it.py` | `test_dictionary_matches_schema` |
| roles: Grants create read-only roles for the app and the chat | Integration (CLI + DB) | `tests/it/test_api_roles_it.py` | `test_grants_create_roles` |
| roles: The app role reads every serving view and nothing else | Integration | `tests/it/test_api_roles_it.py` | `test_app_role_reads_only_serving_schemas` |
| roles: The app role cannot change data or run scripts | Integration | `tests/it/test_api_roles_it.py` | `test_app_role_cannot_write_or_execute` |
| roles: The chat role reads the serving views but cannot reach CFBD | Integration | `tests/it/test_api_roles_it.py` | `test_mcp_role_without_live_schema` |
| roles: Regenerated what-if views stay readable without a new grant | Integration | `tests/it/test_api_roles_it.py` | `test_regenerated_views_readable` |
| roles: The live preview works for the app role without access to the virtual schema or the key | Integration | `tests/it/test_api_roles_it.py` | `test_preview_without_vs_privilege` |
| on-demand: One click fetches one live preview | Integration | `tests/it/test_game_preview_it.py` | `test_one_click_one_call` |
| on-demand: The latest meeting of a pair can be previewed after it was played | Integration | `tests/it/test_game_preview_it.py` | `test_latest_meeting_preview` |
| on-demand: A preview query without a literal game id is refused before any call | Integration | `tests/it/test_game_preview_it.py` | `test_missing_game_id_refused` |
| on-demand: A query for more games than the budget is refused before any call | Integration | `tests/it/test_game_preview_it.py` | `test_over_budget_refused` |
| on-demand: The plan shows the request before it is sent | Integration | `tests/it/test_game_preview_it.py` | `test_explain_virtual_through_view` |
| on-demand: Replay mode serves a recorded preview without a call | Integration | `tests/it/test_game_preview_it.py` | `test_replay_preview_zero_calls` |

78 scenarios, each mapped to one test.

Supporting tests back the integration tests. They do not replace them:
- unit (pure computation, no I/O):
  - the library: `tests/unit/test_whatif_sql.py` (shapes, quoting, the run guard, determinism);
  - the escaping rule and the reference renderer: `tests/unit/test_sqltext.py`, `tests/unit/test_whatif_ddl.py` (review M3);
  - the oracles: `tests/unit/test_analyses_oracle.py`;
  - the history variant and the apostrophe school: `tests/unit/test_analyses_history.py`;
  - seeds and plan entries: `tests/unit/test_analyses_seeds.py`;
  - the deployer extensions: `tests/unit/test_sqlobjects.py`, `test_analyses_deploy.py`;
  - namespace and renderer: `test_namespace.py`, `test_meta_ddl.py`;
  - grants: `test_grants.py`;
  - the dictionary: `test_api_dictionary.py`;
  - the perf catalog never names a live view: `tests/unit/test_analyses_perf.py` (review M6);
  - the Lua bundle: `test_lua_bundle.py` (extended);
- CLI: `tests/cli/test_analyses_cli.py`.

### Manual Testing

All commands run from the repo root against Exasol Personal local (`TARGET=local`) after `make deploy` and `make grants`, unless the row names AWS. None spends a CFBD call, except the `GAME_PREVIEW` rows on AWS (ledgered, task 7.3).

| Feature | Command | Expected Output |
|---------|---------|-----------------|
| analyses/deploy | `make analyses`, then again; `make analyses TARGET=aws` | Report: `CFB_PRED` 1 reference table, 3 data tables, 12 views; `CFB_API` 23 + 3 generated views; `CFB_API_LIVE` 2 views; 5 scripts; comments 100 %; steps 100–400 with their scripts, no warning; the second run drops nothing and names the same what-if run |
| analyses/predictions | `make predict SEASON=2026 WEEK=<current>`; `SELECT WEEK, COUNT(*), SUM(CASE WHEN FROZEN_BEFORE_KICKOFF THEN 1 ELSE 0 END) FROM CFB_API.PREDICTIONS WHERE SEASON = 2026 GROUP BY 1 ORDER BY 1` | Next week's games frozen (before kickoff), earlier weeks reconstructed; a second `make predict` adds nothing |
| analyses/scorecard | `SELECT WEEK, POPULATION, FREEZE_KIND, IS_HEADLINE, RECORD_LABEL, BRIER, MAE_MARGIN, LINE_CORRECT, LINE_WRONG FROM CFB_API.SCORECARD_WEEKS WHERE SEASON = 2026 ORDER BY WEEK_SEQ DESC, POPULATION, FREEZE_KIND LIMIT 12` | Last week's record such as `41–9`, Brier around 0.15–0.20, the line's record beside it; the headline row is `IN_ADVANCE` for weeks frozen live and `ALL` (a labeled reconstruction) for earlier weeks |
| analyses/upsets | `SELECT SEASON, WEEK, WINNER_SCHOOL, LOSER_SCHOOL, PROB_SOURCE, UPSET_MAGNITUDE, ALL_TIME_RANK, ALL_TIME_TIED, ALL_TIME_FROM_SEASON, LINE_ERA_RANK FROM CFB_API.UPSETS WHERE IS_UPSET AND SEASON = 2026 AND WEEK_RANK = 1 ORDER BY WEEK_SEQ DESC LIMIT 3`; the same `ORDER BY ALL_TIME_RANK LIMIT 20` | Each week's biggest upset with its rank since 1978 ("T-n" where tied) and its line-era rank; a plausible top 20 since 1978 (Q8) |
| analyses/head-to-head | `SELECT * FROM CFB_API.MATCHUP_SERIES s JOIN CFB_API.TEAMS a ON a.TEAM_ID = s.TEAM_ID JOIN CFB_API.TEAMS b ON b.TEAM_ID = s.OPPONENT_TEAM_ID WHERE a.SCHOOL = 'Michigan' AND b.SCHOOL = 'Ohio State'`; `MATCHUP_NEXT` for the same pair | The all-time series (meetings since 1897, wins, ties, streak) in < 1 s at 0 calls; the next meeting with our prediction and stakes when scheduled, else `LAST_GAME_ID` as the game to preview |
| analyses/published-odds | `make simulate SEASON=2026 WEEK=<current>`, `make publish RUN=<id>`; `SELECT SCHOOL, P_IN_CFP, PREV_P_IN_CFP, DELTA_P_IN_CFP, MOVE FROM CFB_API.PLAYOFF_ODDS p JOIN CFB_API.CURRENT_POINT c ON p.AS_OF_WEEK_SEQ = c.AS_OF_WEEK_SEQ AND p.AS_OF_SEASON = c.AS_OF_SEASON ORDER BY P_IN_CFP DESC LIMIT 15` | Top 15 with arrows since the previous published week (threshold 0.0070); `WHATIF_RUN` names the run and `CURRENT_POINT.WHATIF_VIEWS_CURRENT` is TRUE |
| analyses/what-if | `make what-if TEAM=<bubble team> FIX="<game id>=A"`; then with 3 and with 8 conditions | Conditional odds (top 25) with the run id and the effective sample, `LOW_SAMPLE` where it is below 1,000; seconds below the budget |
| analyses/rooting-guide | `make what-if TEAM=<bubble team>`; `SELECT * FROM CFB_API.ROOTING_GUIDE WHERE RUN_ID = (SELECT RUN_ID FROM CFB_API.WHATIF_RUN) AND TEAM_ID = <id> ORDER BY GUIDE_RANK LIMIT 10` | The coming week's guide with the side to root for; the season-wide top 10 from `LEVERAGE` in < 0.2 s, `FIXED_RESULTS` NULL |
| analyses/forced-resim | `make simulate BASE=<published run> FIX="<game id>=A" LABEL="…"`; `SELECT SCHOOL, BASE_P_IN_CFP, P_IN_CFP, DELTA_P_IN_CFP FROM CFB_API.FORCED_RUN_ODDS f JOIN CFB_API.TEAMS t ON t.TEAM_ID = f.TEAM_ID WHERE RUN_ID = <new> ORDER BY ABS(DELTA_P_IN_CFP) DESC LIMIT 10`; `CFB_API.ROOTING_GUIDE` for the new run | The forced scenario's biggest movers next to the base run; its guide has no row for the forced game and every row carries `FIXED_RESULTS` |
| api/serving-contract | `make api-docs && git diff --exit-code docs/api-dictionary.md`; `SELECT OBJECT_NAME, OBJECT_COMMENT FROM EXA_ALL_OBJECTS WHERE ROOT_NAME IN ('CFB_API', 'CFB_API_LIVE') ORDER BY 1`; `SELECT * FROM CFB_API.CURRENT_POINT`; `SELECT WINS, LOSSES FROM CFB_API.TEAM_SEASONS t JOIN CFB_API.TEAMS s ON s.TEAM_ID = t.TEAM_ID WHERE s.SCHOOL = 'Michigan' AND t.SEASON = 1997` | No diff; 26 + 2 views, each comment starting "One row per"; one current point; Michigan's 1997 record at 0 calls |
| api/roles | As the owner: `CREATE USER CFB_MCP_DEMO IDENTIFIED BY <typed in a SQL client>`, `GRANT CREATE SESSION, CFB_MCP TO CFB_MCP_DEMO`; as that user: `SELECT COUNT(*) FROM CFB_API.PLAYOFF_ODDS`, `SELECT COUNT(*) FROM CFB_SIM.SIM_RUN`, `SELECT * FROM CFB_API_LIVE.QUOTA`; then the same with `CFB_APP`; then drop the user | With `CFB_MCP`: the first succeeds, the second and third fail with insufficient privileges. With `CFB_APP`: the first and third succeed, the second fails |
| api/on-demand | Local replay: `SELECT GAME_ID, HOME_TEAM_NAME, AWAY_TEAM_NAME, SPREAD, REQ_SOURCE FROM CFB_API_LIVE.GAME_PREVIEW WHERE GAME_ID = 401858271` (0 calls); AWS: task 7.3's `make record … --query` | Replay: one row, `REPLAY`; AWS: one row, `LIVE`, end to end < 2 s (median of 3), one ledger entry per query |

### Checklist

| Step | Command | Expected |
|------|---------|----------|
| Generate | `make generate && git diff --exit-code -- vs/catalog sql/core` | Exit 0 |
| Build | `uv run python -m vs.bundle && uv run python -m loader.bundle && uv run python -m engine.bundle` | Exit 0; `build/sql/` also holds the five analyses Lua scripts, all below 1,990,000 characters |
| Analyses lint | `uv run python -m scripts.analyses deploy --lint-only` | Exit 0; "lint ok" |
| Test | `make test` | 0 failures (unit + CLI, no DB) |
| Integration | `flock <scratchpad>/cfb-it.lock make test-it` | 0 failures; mock and replay only; 0 requests to `api.collegefootballdata.com` |
| Performance | task 5.1 (local), 7.2 (AWS) | Budget met, or a logged decision with the breakdown; the guide's F1/F2 decision recorded; no live view timed |
| Lint | `make lint` | 0 ruff errors, 0 mypy errors (`vs`, `scripts`, `tests`, `loader`, `model`, `engine`, `analyses`) |
| Format | `uv run ruff format --check .` | No changes |
| Coverage | `make coverage` | ≥ 90 % line coverage for `vs/cfbd_vs`, `vs/gen`, `loader`, `model`, `engine`, `analyses` |
| Deploy | `make deploy && make grants && make deploy TARGET=aws && make grants TARGET=aws` | Exit 0; `CFB_PRED`, `CFB_API` and `CFB_API_LIVE` comments 100 %; a second run drops nothing |
| Layering | `git grep -n -i -e 'CFB_CORE' -e '{CORE_SCHEMA}' -- sql/pred sql/api sql/api_live`; `git grep -n -i -e 'CFBD_VS' -e '{VS_NAME}' -e 'QUOTA' -- sql/pred sql/api sql/api_live` | First: no match. Second: only `sql/api_live/010_game_preview.sql` and `sql/api_live/020_quota.sql` |
| Escaping | `git grep -n -e "'" -- analyses/lua/whatif_views.lua` reviewed; `uv run pytest tests/unit/test_sqltext.py tests/unit/test_whatif_ddl.py` | Every string literal built from data goes through `analib.sql_str`; tests pass |
| Dictionary | `make api-docs && git diff --exit-code docs/api-dictionary.md` | Exit 0 |
| Roles | `SELECT GRANTEE, PRIVILEGE, OBJECT_TYPE, OBJECT_NAME FROM EXA_DBA_OBJ_PRIVS WHERE GRANTEE IN ('CFB_APP', 'CFB_MCP')`; `SELECT GRANTEE, PRIVILEGE FROM EXA_DBA_SYS_PRIVS WHERE GRANTEE IN ('CFB_APP', 'CFB_MCP')` | First: `CFB_APP` has `SELECT` on the schemas `CFB_API` and `CFB_API_LIVE`, `CFB_MCP` on `CFB_API` only. Second: empty |
| Zero calls | `make record ARGS="--ledger"`; `make quota TARGET=aws` before and after 7.3 | Ledger entries for `add-cfb-analyses` ≤ 6 (3 planned), one to one with the `FETCH_LOG` rows of those `make record` runs; the `/info` drop over 7.3's window equals the sum of every ledger entry in it |
| No data in git | `git ls-files '*.parquet' '*.cfbd.json' 'cache/*'` | Empty; tests use synthetic preview bodies only; no `RAW_JSON` value saved |
