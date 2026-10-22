# Feature: Serving API contract

`CFB_API` is the only schema the app (Phase 6) and the chat (Phase 7) read. It is a fixed set of commented views, one per question a page or the chat asks, over the analyses, the model and the logs, plus one on-demand view that calls CFBD on a click. Each view says in its first words what one row is, so a developer or a language model can use it without reading the code. The names and columns below are the contract that the later phases build on.

## Background

- **Objects** (all views; grain = the start of each view comment, "One row per …"):

  | View | One row per | Reads | Serves |
  |---|---|---|---|
  | `CURRENT_POINT` | the current as-of point (exactly one row) | `CFB_MODEL.CURRENT_AS_OF`, `CFB_PRED.POINT_PUBLICATION`, `WHATIF_RUN` | every page's default point |
  | `AS_OF_POINTS` | season and as-of point | `CFB_MODEL.AS_OF_POINT`, `CFB_PRED` | the as-of week picker |
  | `TEAMS` | team | `CFB_MODEL.TEAM` | team pickers and names |
  | `PLAYOFF_ODDS` | published point and eligible team | `CFB_PRED.ODDS_MOVEMENT` | odds with arrows (`analyses/published-odds`) |
  | `PUBLISHED_RUNS` | published point (its latest publication) | `CFB_PRED.POINT_PUBLICATION`, `CFB_SIM.SIM_RUN` | "N seasons in S seconds", machine sizing |
  | `ROOTING_GUIDE` | done simulation run, team with a playoff season in it, and remaining game | `CFB_PRED.ROOTING_GUIDE` | rooting guide without toggles (`analyses/rooting-guide`) |
  | `FORCED_RUN_ODDS` | done forced run and eligible team | `CFB_PRED.FORCED_RUN_ODDS` | forced scenarios (`analyses/forced-resim`) |
  | `WHATIF_RUN` * | the what-if run (one row, or none before the first publication) | `CFB_PRED.PUBLICATION`, `CFB_SIM.SIM_RUN` | what-if anchor (`analyses/what-if`) |
  | `WHATIF_GAMES` | remaining game of the what-if run | `CFB_SIM.SIM_RUN_GAME`, `SIM_RUN_TEAM`, `LEVERAGE`, `MARGIN_WIN_PROB` | toggles |
  | `WHATIF_OUTCOMES` * | simulated season of the what-if run | the run's outcome table in `CFB_SIM` | what-if conditions |
  | `WHATIF_PLAYOFF` * | simulated season and playoff team of the what-if run | `CFB_SIM.SIM_TEAM` | conditional odds |
  | `WHATIF_SETTINGS` | setting | `CFB_PRED.ANALYSIS_SETTINGS` | sample thresholds |
  | `PREDICTIONS` | frozen game prediction | `CFB_PRED.PREDICTION_GRADED`, `CFB_MODEL.TEAM` | this week's predictions, last week's grades (`analyses/predictions`) |
  | `SCORECARD_WEEKS` | season, week and population | `CFB_PRED.SCORECARD_WEEK` | "the model went 41–9" (`analyses/scorecard`) |
  | `SCORECARD_SEASONS` | as-of point and population | `CFB_PRED.SCORECARD_SEASON` | season to date |
  | `SCORECARD_CALIBRATION` | season, population and probability bucket | `CFB_PRED.SCORECARD_CALIBRATION` | calibration chart |
  | `UPSETS` | completed game involving FBS with a winner and a pregame probability | `CFB_PRED.UPSET`, `CFB_MODEL.TEAM` | upsets ranked all-time (`analyses/upsets`) |
  | `MATCHUP_SERIES` | ordered team pair that has met | `CFB_PRED.MATCHUP_SERIES` | series summary (`analyses/head-to-head`) |
  | `MATCHUP_GAMES` | completed meeting and ordered team pair | `CFB_MODEL.TEAM_GAME`, `GAME`, `CFB_SIM.GAME_WIN_PROB` | the list of meetings |
  | `MATCHUP_NEXT` | ordered team pair with a meeting still to play this season | `CFB_PRED.MATCHUP_NEXT` | next meeting, prediction, stakes |
  | `GAME_PREVIEW` | requested game; **one CFBD call per query** | `CFBD_VS.GAMES_PREVIEW` | live preview on click (`api/on-demand`) |
  | `REFRESH_TIMELINE` | refresh run and step | `CFB_META.LOAD_RUN`, `REFRESH_LOG` | the refresh timeline |
  | `FETCHES` | logged request of a slice | `CFB_META.FETCH_LOG` | the SQL↔HTTP inspector |
  | `QUOTA` | the key's quota (one row; reads CFBD's free `/info`) | `CFB_META.QUOTA` | the quota badge |

  \* generated at each publication of the newest point and by `make analyses`, with the run id as a literal (`analyses/what-if`).
- **Key columns** (the full lists are in the specs of the views' sources and in the generated `docs/api-dictionary.md`):
  - `CURRENT_POINT`: `AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `NEXT_WEEK_SEQ`, `AS_OF_LABEL`, `CUTOFF_AT`, `PUBLICATION_ID` (the latest publication at that point, NULL if none) and `WHATIF_RUN_ID`.
  - `AS_OF_POINTS`: the point columns of `CURRENT_POINT` plus `IS_CURRENT`, `HAS_PUBLICATION`, `HAS_PREDICTIONS` (predictions of the point's next week exist) and `HAS_GRADES` (graded predictions of the point's week exist).
  - `TEAMS`: `TEAM_ID`, `SCHOOL`, and the team's current conference and classification as `CFB_MODEL.TEAM` names them.
  - `PLAYOFF_ODDS`: the point, `PUBLICATION_ID`, `RUN_ID`, `TEAM_ID`, `SCHOOL`, `CONFERENCE`, the odds and movement columns of `CFB_PRED.ODDS_MOVEMENT`.
  - `PUBLISHED_RUNS`: the point, `PUBLICATION_ID`, `RUN_ID`, `RUN_ORIGIN`, `N_SIMS`, `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND`, `NODES`, `INSTANCES`, `DB_VERSION`, `ENGINE_VERSION`, `PUBLISHED_AT`.
  - `PREDICTIONS`: the columns of `CFB_PRED.PREDICTION_GRADED` plus `HOME_SCHOOL`, `AWAY_SCHOOL` and `PICK_SCHOOL`.
  - `UPSETS`: the columns of `CFB_PRED.UPSET` plus `WINNER_SCHOOL` and `LOSER_SCHOOL`.
  - `MATCHUP_SERIES` and `MATCHUP_NEXT`: their sources' columns plus `SCHOOL` and `OPPONENT_SCHOOL`.
  - `MATCHUP_GAMES`: `TEAM_ID`, `OPPONENT_TEAM_ID`, `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `START_AT`, `SITE`, `TEAM_POINTS`, `OPPONENT_POINTS`, `GAME_RESULT`, `PROB_SOURCE`, `TEAM_PREGAME_WIN_PROB` and `UPSET_MAGNITUDE`.
  - `REFRESH_TIMELINE`: `RUN_ID`, `RUN_KIND`, `RUN_ARGS`, `RUN_STATUS`, `STEP_NO`, `STEP_NAME`, `STEP_STATUS`, `STARTED_AT`, `ENDED_AT`, `SECONDS`, `SLICES`, `ROWS_LOADED`, `CALLS`, `DETAIL`.
  - `FETCHES` and `QUOTA`: the columns of `CFB_META.FETCH_LOG` and `CFB_META.QUOTA` (`loader/run-logs`, `loader/quota`).
- **Defaults:** pages and the chat start from `CURRENT_POINT` and may choose any point of `AS_OF_POINTS`. What-ifs exist for the what-if run only.
- **No functions.** Exasol's SQL functions return one value and cannot serve rows in `FROM` (`model/as-of`), so pages filter views, and what-if conditions go through `analyses/whatif.py` (`analyses/what-if`). The schema has no functions, scripts or tables.
- **Calls:** every view costs 0 CFBD calls except `GAME_PREVIEW` (one call per query, `api/on-demand`) and `QUOTA`, which reads CFBD's `/info`; `/info` does not count against the quota, but needs the network, or a recording in replay mode.
- **Stability:** the view names, their grains and the columns named here are the contract. A later phase that changes them changes this spec. New views and new columns may be added.
- **Dictionary:** `make api-docs` writes `docs/api-dictionary.md` (no database) from `sql/api/` and the templates of the generated views: per view its grain, columns and comments, in file order.
- The examples run in the integration-test namespace, deployed with `make deploy ARGS="--prefix TEST_"` and `make grants ARGS="--prefix TEST_"`, over the sim league with 2026 week 9 as the current point, in replay mode, with `SIM_CONFIG` 2,000 seasons in batches of 500.

## Scenarios

### Scenario: The serving schema holds exactly the documented views, each described by its grain

* *GIVEN* a deployed namespace with a publication
* *WHEN* `EXA_ALL_OBJECTS` and `EXA_ALL_COLUMNS` are read for `CFB_API`
* *THEN* the schema MUST hold exactly the 24 views of the object table, and no table, function or script
* *AND* every view comment MUST start with "One row per", and every column MUST have a comment
* *AND* the comment of `GAME_PREVIEW` MUST say that each query makes one CFBD call

### Scenario: No serving view reads the raw mirrors or a broad API table

* *GIVEN* a deployed namespace with a publication
* *WHEN* the direct references of every `CFB_API` view are read from `EXA_ALL_DEPENDENCIES`
* *THEN* each MUST be an object of `CFB_API`, `CFB_PRED`, `CFB_SIM` or `CFB_MODEL`, one of the `CFB_META` objects `REFRESH_LOG`, `LOAD_RUN`, `FETCH_LOG` and `QUOTA`, or the virtual table `GAMES_PREVIEW`
* *AND* no `CFB_API` view MUST reference `CFB_CORE` or any other virtual table

### Scenario: The current point tells the pages what to show by default

* *GIVEN* the sim league with 2026 week 9 as the current point and a publication of a run at 2026 week 9
* *WHEN* `CFB_API.CURRENT_POINT` is queried
* *THEN* it MUST return one row with `AS_OF_SEASON` 2026, `AS_OF_WEEK_SEQ` 9, `NEXT_WEEK_SEQ` 10, the publication's id and the published run as `WHATIF_RUN_ID`

### Scenario: The as-of points say where each page has data

* *GIVEN* the predictions frozen by `REFRESH_WEEK(2026, 9, 'regular')`, a publication at 2026 week 9 and no publication at week 8
* *WHEN* `CFB_API.AS_OF_POINTS` is queried for 2026
* *THEN* week 9 MUST have `IS_CURRENT`, `HAS_PUBLICATION` and `HAS_PREDICTIONS` TRUE
* *AND* week 8 MUST have `HAS_PUBLICATION` FALSE and `HAS_PREDICTIONS` TRUE (its next week was reconstructed)

### Scenario: The refresh timeline shows every step of the weekly chain

* *GIVEN* replay recordings of the sim league's 2026 weekly slices
* *WHEN* the presenter runs `REFRESH_WEEK(2026, 9, 'regular')` and then `CFB_API.REFRESH_TIMELINE` is queried for that run
* *THEN* it MUST list `CHECK`, the `FETCH` steps, `MODEL`, `PREDICT`, `SIMULATE` and `PUBLISH` in step order, each with its status, seconds, rows and calls
* *AND* `CFB_API.FETCHES` MUST hold the run's fetch-log rows

### Scenario: The data dictionary matches the deployed schema

* *GIVEN* a deployed namespace with a publication
* *WHEN* the owner runs `make api-docs` and compares `docs/api-dictionary.md` with `EXA_ALL_COLUMNS` of `CFB_API`
* *THEN* every view and column of the schema MUST appear with its comment, the generated what-if columns as their `G<game id>` pattern
* *AND* running `make api-docs` again MUST leave the file unchanged
