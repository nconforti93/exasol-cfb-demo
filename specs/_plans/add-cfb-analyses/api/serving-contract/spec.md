# Feature: Serving API contract

`CFB_API` is the only schema the app (Phase 6) and the chat (Phase 7) read: a fixed set of thin, commented views over the stored model (`CFB_MODEL`) and, once the engine exists, over the simulation schema (`CFB_SIM`). Every one of them costs zero CFBD calls. Each view says in its first words what one row is, so a developer or a language model can use it without reading the code. The pages that need no simulation work from the day the views are deployed; the simulation columns stay empty until the engine has run, and then fill in place, under the same names.

## Background

- **Deploy:** `make api [TARGET=local|aws]` (code `analyses/deploy.py`, also the last step of `make deploy`) lints `sql/api/` and `sql/api_live/`, then installs `CFB_API` and `CFB_API_LIVE` through the generic SQL-object deployer (`vs/host/sqlobjects.py`) in one transaction each, under the run lock (kind `DEPLOY`). A second run changes nothing. A view file that reads `CFB_SIM` is refused with "run make sim first" while that schema does not exist.
- **Objects of `CFB_API`** (views only; no table, function or script). The grain is the start of each view comment, "One row per …"; the columns are in the feature specs named in the last column.

  | View | One row per | Reads | Spec |
  |---|---|---|---|
  | `CURRENT_POINT` | the current as-of point (exactly one row) | `CFB_MODEL.CURRENT_AS_OF`; with the engine `CFB_SIM.POINT_RUN`, `CURRENT_RUN` | this spec |
  | `AS_OF_POINTS` | season and as-of point | `CFB_MODEL.AS_OF_POINT`, `CURRENT_AS_OF`; with the engine `CFB_SIM.POINT_RUN`, `PREDICTION`, `PREDICTION_GRADED` | this spec |
  | `TEAMS`, `GAMES`, `TEAM_GAMES`, `TEAM_SEASONS`, `RANKINGS`, `SP_RATINGS`, `TEAM_TRAVEL`, `WEEK_GAMES` | see `api/teams-and-weeks` | `CFB_MODEL` | `api/teams-and-weeks` |
  | `ROAD_WARRIORS`, `SP_MOVERS`, `CONFERENCE_STRENGTH`, `WIN_STREAKS` | see `api/league-stats` | `CFB_MODEL`, `TEAM_TRAVEL` | `api/league-stats` |
  | `MATCHUP_SERIES`, `MATCHUP_GAMES`, `MATCHUP_NEXT` | see `api/head-to-head` | `CFB_MODEL`; with the engine `CFB_SIM` | `api/head-to-head` |
  | `UPSETS` | see `api/upsets` | `CFB_MODEL`; with the engine `CFB_SIM.GAME_WIN_PROB` | `api/upsets` |
  | `REFRESH_TIMELINE` | refresh run and step | `CFB_META.LOAD_RUN`, `REFRESH_LOG` | this spec |
  | `FETCHES` | logged request of a slice | `CFB_META.FETCH_LOG` | this spec |
  | `ANALYSIS_SETTINGS`, `PLAYOFF_ODDS`, `PUBLISHED_RUNS`, `WHATIF_RUN`, `WHATIF_GAMES`, `WHATIF_OUTCOMES`, `WHATIF_PLAYOFF`, `WHATIF_TEAMS`, `ROOTING_GUIDE` | see `api/simulations` | `CFB_SIM` | `api/simulations` |
  | `PREDICTIONS`, `SCORECARD_WEEKS`, `SCORECARD_SEASONS`, `SCORECARD_CALIBRATION` | see `api/predictions` | `CFB_SIM` | `api/predictions` |

  Before the engine: 20 views (every row above except the last two). With the engine: 33.
- **Objects of `CFB_API_LIVE`:** `GAME_PREVIEW` and `QUOTA` (`api/on-demand`), the only serving views that reach the network.
- **`CURRENT_POINT`** columns: `AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `PREVIOUS_WEEK_SEQ`, `NEXT_WEEK_SEQ`, `LAST_REGULAR_WEEK_SEQ`, `CUTOFF_AT`, `IS_SEASON_COMPLETE` (from `CURRENT_AS_OF`), and the engine columns `RUN_ID`, `PUBLICATION_ID`, `HAS_PUBLICATION`, `WHATIF_RUN_ID`, `WHATIF_PUBLICATION_ID`, `WHATIF_AS_OF_WEEK_SEQ`, `WHATIF_VIEWS_CURRENT`.
  - Before the engine the id columns are NULL and both flags FALSE.
  - With the engine (`api/simulations`): `RUN_ID` and its alias `PUBLICATION_ID` name the run shown at the current point (`CFB_SIM.POINT_RUN`), NULL when the point has none, and `HAS_PUBLICATION` says whether it has one. `WHATIF_RUN_ID` and its alias `WHATIF_PUBLICATION_ID` name `CFB_SIM.CURRENT_RUN` (the run of the newest point at or before the current one), with its point in `WHATIF_AS_OF_WEEK_SEQ`. `WHATIF_VIEWS_CURRENT` is TRUE whenever that run exists: no serving view is generated per run.
- **`AS_OF_POINTS`** columns: the point columns of `CURRENT_POINT`, `IS_CURRENT`, `IS_AFTER_CURRENT` (the point is later than the current one, by season and week sequence), and the engine columns `RUN_ID`, `HAS_PUBLICATION` (a run is shown at the point), `HAS_PREDICTIONS` (frozen predictions exist for the point's next week) and `HAS_GRADES` (graded predictions exist for games played in the point's week). Before the engine `RUN_ID` is NULL and the three flags FALSE.
- **`REFRESH_TIMELINE`** columns: `RUN_ID`, `RUN_KIND`, `RUN_ARGS`, `RUN_STATUS`, `RUN_STARTED_AT`, `RUN_ENDED_AT`, `STEP_NO`, `STEP_NAME`, `STEP_STATUS`, `STARTED_AT`, `ENDED_AT`, `SECONDS`, `SLICES`, `ROWS_LOADED`, `CALLS`, `DETAIL`; one row per `REFRESH_LOG` row of a `REFRESH_WEEK` run. **`FETCHES`**: the columns of `CFB_META.FETCH_LOG` unchanged.
- **Layering:** a `CFB_API` view reads only `CFB_API`, `CFB_MODEL`, `CFB_SIM` and the three `CFB_META` logs above; never `CFB_CORE`, never a virtual table. The lint enforces it on the files (`sql/api/` may name `{API_SCHEMA}`, `{MODEL_SCHEMA}`, `{SIM_SCHEMA}` and `{META_SCHEMA}.REFRESH_LOG`, `LOAD_RUN`, `FETCH_LOG`), the database's dependency list on the deployed views.
- **Point rule** (Phase 3): views that have a point (`WEEK_GAMES`, the point-grain stats views) are built on `CFB_MODEL.AS_OF_POINT` and the derived tables, never on the as-of views; consumers read `CURRENT_POINT` first and filter those views with literal `AS_OF_SEASON` and `AS_OF_WEEK_SEQ` values, never by joining `CURRENT_POINT`.
- **Comments:** every view comment starts with "One row per", every column has a comment, DDL names are unquoted upper case and not reserved words (`vs/gen/exasol_reserved_keywords.txt`).
- **Dictionary:** `make api-docs` writes `docs/api-dictionary.md` from `sql/api/` and `sql/api_live/` (no database): per view its grain, reads, columns and comments.
- **Stability:** view names, grains and the columns named in the `api/` specs are the contract Phases 6 and 7 build on. New views and columns may be added; a change to a named one changes the spec.
- The examples run in the integration-test namespace (`TEST_` prefix) over Phase 3's synthetic league, whose current point is 2026 after week 9 (next week 10, cutoff 2026-10-29 23:30 UTC), in replay mode with the mock CFBD server. Once view files read `CFB_SIM`, the harness deploys Phase 4's simulation schema (without runs) before the serving schemas.

## Scenarios

### Scenario: One command installs the serving views, each described by its grain

* *GIVEN* the synthetic league stored and the model deployed
* *WHEN* the owner deploys the serving schemas twice with the code behind `make api`
* *THEN* `CFB_API` MUST hold exactly one view per file of `sql/api/`, each with the name its file defines, and no table, function or script
* *AND* every view comment MUST start with "One row per" and every column MUST have a comment
* *AND* the second deploy MUST report no dropped, replaced or changed object

### Scenario: No serving view reads the raw mirrors or the virtual schema

* *GIVEN* the deployed serving schemas
* *WHEN* the direct references of every `CFB_API` view are read from `EXA_ALL_DEPENDENCIES`
* *THEN* each reference MUST be an object of `CFB_API`, `CFB_MODEL` or `CFB_SIM`, or one of `CFB_META.REFRESH_LOG`, `LOAD_RUN` and `FETCH_LOG`
* *AND* no reference MUST be an object of `CFB_CORE` or a virtual table

### Scenario: Before the first simulation the pages still have a current point

* *GIVEN* the deployed serving schemas and no done simulation run (no simulation schema before the engine exists, an empty one after)
* *WHEN* `CURRENT_POINT` and the 2026 rows of `AS_OF_POINTS` are queried
* *THEN* `CURRENT_POINT` MUST return one row with `AS_OF_SEASON` 2026, `AS_OF_WEEK_SEQ` 9, `NEXT_WEEK_SEQ` 10 and `AS_OF_LABEL` '2026, after week 9'
* *AND* its `RUN_ID`, `PUBLICATION_ID`, `WHATIF_RUN_ID`, `WHATIF_PUBLICATION_ID` and `WHATIF_AS_OF_WEEK_SEQ` MUST be NULL and `HAS_PUBLICATION` and `WHATIF_VIEWS_CURRENT` FALSE
* *AND* in `AS_OF_POINTS` week 9 MUST have `IS_CURRENT` TRUE, week 10 MUST have `IS_AFTER_CURRENT` TRUE, and every row MUST have `HAS_PUBLICATION`, `HAS_PREDICTIONS` and `HAS_GRADES` FALSE

### Scenario: The refresh timeline shows every step of a weekly refresh

* *GIVEN* replay recordings of the synthetic league's 2026 weekly slices
* *WHEN* the harness runs `REFRESH_WEEK(2026, 9, 'regular')` and `REFRESH_TIMELINE` and `FETCHES` are queried for that run
* *THEN* `REFRESH_TIMELINE` MUST list one row per `REFRESH_LOG` row of the run in step order, each with its status, seconds, rows and calls
* *AND* `FETCHES` MUST hold the run's fetch-log rows (`CONTEXT` 'REFRESH_WEEK#<run id>')
* *AND* the mock MUST NOT receive any request from the queries of the two views
