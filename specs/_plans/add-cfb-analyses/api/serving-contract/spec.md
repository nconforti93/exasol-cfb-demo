# Feature: Serving API contract

`CFB_API` is the schema the app (Phase 6) and the chat (Phase 7) read. It is a fixed set of commented views, one per question a page asks, plus a few views over the model that let the chat answer questions outside the pages. Every one of them costs zero CFBD calls. The views that do call CFBD live apart, in `CFB_API_LIVE`, which only the app may read (`api/roles`, `api/on-demand`). Each view says in its first words what one row is, so a developer or a language model can use it without reading the code. The names and columns below are the contract that the later phases build on.

## Background

- **Objects of `CFB_API`** (all views; grain = the start of each view comment, "One row per …"):

  | View | One row per | Reads | Serves |
  |---|---|---|---|
  | `CURRENT_POINT` | the current as-of point (exactly one row) | `CFB_MODEL.CURRENT_AS_OF`, `CFB_PRED.POINT_PUBLICATION`, `CFB_PRED.WHATIF_PUBLICATION`, `WHATIF_RUN` | every page's default point |
  | `AS_OF_POINTS` | season and as-of point | `CFB_MODEL.AS_OF_POINT`, `CURRENT_AS_OF`, `CFB_PRED` | the as-of week picker |
  | `TEAMS` | team | `CFB_MODEL.TEAM` | team pickers and names |
  | `PLAYOFF_ODDS` | published point and eligible team | `CFB_PRED.ODDS_MOVEMENT` | odds with arrows (`analyses/published-odds`) |
  | `PUBLISHED_RUNS` | published point (its latest publication) | `CFB_PRED.POINT_PUBLICATION`, `CFB_SIM.SIM_RUN` | "N seasons in S seconds", machine sizing |
  | `ROOTING_GUIDE` | done simulation run, team with a playoff season in it, and remaining game not fixed in the run | `CFB_PRED.ROOTING_GUIDE` | rooting guide without toggles (`analyses/rooting-guide`) |
  | `FORCED_RUN_ODDS` | done forced run and eligible team | `CFB_PRED.FORCED_RUN_ODDS` | forced scenarios (`analyses/forced-resim`) |
  | `WHATIF_RUN` * | the what-if run (one row, or none before the first publication) | `CFB_PRED.PUBLICATION`, `CFB_SIM.SIM_RUN` | what-if anchor (`analyses/what-if`) |
  | `WHATIF_GAMES` | remaining game of the what-if run | `CFB_PRED.WHATIF_PUBLICATION`, `WHATIF_RUN`, `CFB_SIM.SIM_RUN_GAME`, `SIM_RUN_TEAM`, `LEVERAGE`, `MARGIN_WIN_PROB` | toggles |
  | `WHATIF_OUTCOMES` * | simulated season of the what-if run | the run's outcome table in `CFB_SIM` | what-if conditions |
  | `WHATIF_PLAYOFF` * | simulated season and playoff team of the what-if run | `CFB_SIM.SIM_TEAM` | conditional odds |
  | `ANALYSIS_SETTINGS` | setting | `CFB_PRED.ANALYSIS_SETTINGS` | sample thresholds, the arrow threshold, the all-time window |
  | `PREDICTIONS` | frozen game prediction | `CFB_PRED.PREDICTION_GRADED`, `CFB_MODEL.TEAM` | this week's predictions, last week's grades (`analyses/predictions`) |
  | `SCORECARD_WEEKS` | season, week, population and freeze kind | `CFB_PRED.SCORECARD_WEEK` | "the model went 41–9" (`analyses/scorecard`) |
  | `SCORECARD_SEASONS` | as-of point, population and freeze kind | `CFB_PRED.SCORECARD_SEASON` | season to date |
  | `SCORECARD_CALIBRATION` | season, population, freeze kind and probability bucket | `CFB_PRED.SCORECARD_CALIBRATION` | calibration chart |
  | `UPSETS` | completed game involving FBS with a winner and a pregame probability | `CFB_PRED.UPSET`, `CFB_MODEL.TEAM` | upsets ranked all-time (`analyses/upsets`) |
  | `MATCHUP_SERIES` | ordered team pair that has met | `CFB_PRED.MATCHUP_SERIES` | series summary (`analyses/head-to-head`) |
  | `MATCHUP_GAMES` | completed meeting and ordered team pair | `CFB_MODEL.TEAM_GAME`, `GAME`, `CFB_SIM.GAME_WIN_PROB` | the list of meetings |
  | `MATCHUP_NEXT` | ordered team pair with a meeting still to play this season | `CFB_PRED.MATCHUP_NEXT` | next meeting, prediction, stakes |
  | `REFRESH_TIMELINE` | refresh run and step | `CFB_META.LOAD_RUN`, `REFRESH_LOG` | the refresh timeline |
  | `FETCHES` | logged request of a slice | `CFB_META.FETCH_LOG` | the SQL↔HTTP inspector |
  | `GAMES` | stored game, every season since 1869 | `CFB_MODEL.GAME` | the chat: any game, last week's results behind a swing |
  | `TEAM_SEASONS` | team and season with a game or a conference membership | `CFB_MODEL.TEAM_SEASON` | the chat: records, conferences, tiers and final ranks of any season |
  | `RANKINGS` | poll, season, season type, week and ranked team | `CFB_MODEL.RANKING` | the chat: poll positions of any week |
  | `SP_RATINGS` | season and SP+-rated team | `CFB_MODEL.RATING_SP` | the chat: CFBD's SP+ ratings and strength of schedule |

  \* generated for the what-if publication and by `make analyses`, with the run id as a literal (`analyses/what-if`). 23 views come from files, 3 are generated: 26 in all.
- **Objects of `CFB_API_LIVE`** (`api/on-demand`): `GAME_PREVIEW` (one row per requested game; **one CFBD call per query**; `CFBD_VS.GAMES_PREVIEW`) and `QUOTA` (the key's quota, one row; reads CFBD's free `/info` through `CFB_META.QUOTA`). These are the only serving views that reach the network.
- **Key columns** (the full lists are in the specs of the views' sources and in the generated `docs/api-dictionary.md`):
  - `CURRENT_POINT`: `AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `NEXT_WEEK_SEQ`, `AS_OF_LABEL`, `CUTOFF_AT`, `PUBLICATION_ID` (the latest publication at that point, NULL if none), `WHATIF_PUBLICATION_ID`, `WHATIF_RUN_ID` and `WHATIF_AS_OF_WEEK_SEQ` (from `CFB_PRED.WHATIF_PUBLICATION`; the what-if point is earlier than the current point until the point has its own publication), and `WHATIF_VIEWS_CURRENT` (the generated views name that run).
  - `AS_OF_POINTS`: the point columns of `CURRENT_POINT` plus `IS_CURRENT`, `IS_AFTER_CURRENT`, `HAS_PUBLICATION`, `HAS_PREDICTIONS` (predictions of the point's next week exist) and `HAS_GRADES` (graded predictions of the point's week exist).
  - `TEAMS`: `TEAM_ID`, `SCHOOL`, and the team's current conference and classification as `CFB_MODEL.TEAM` names them.
  - `PLAYOFF_ODDS`: the point, `IS_AFTER_CURRENT`, `PUBLICATION_ID`, `RUN_ID`, `TEAM_ID`, `SCHOOL`, `CONFERENCE`, the odds and movement columns of `CFB_PRED.ODDS_MOVEMENT`.
  - `PUBLISHED_RUNS`: the point, `PUBLICATION_ID`, `RUN_ID`, `RUN_ORIGIN`, `N_SIMS`, `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND`, `NODES`, `INSTANCES`, `DB_VERSION`, `ENGINE_VERSION`, `PUBLISHED_AT`.
  - `ROOTING_GUIDE`: the columns of `CFB_PRED.ROOTING_GUIDE`, `RUN_ORIGIN` and `FIXED_RESULTS` included.
  - `ANALYSIS_SETTINGS`: `SETTING_NAME`, `SETTING_NUMBER`, `SETTING_NOTE`.
  - `PREDICTIONS`: the columns of `CFB_PRED.PREDICTION_GRADED` (`FREEZE_KIND` and the week played included) plus `HOME_SCHOOL`, `AWAY_SCHOOL` and `PICK_SCHOOL`.
  - `SCORECARD_WEEKS` and `SCORECARD_SEASONS`: their sources' columns, `FREEZE_KIND` and `IS_HEADLINE` included (TRUE for the `IN_ADVANCE` row of a population when it has graded games, otherwise for its `ALL` row, `analyses/scorecard`).
  - `UPSETS`: the columns of `CFB_PRED.UPSET` (`RANK_MAGNITUDE`, `ALL_TIME_FROM_SEASON`, `ALL_TIME_TIED` and `LINE_ERA_TIED` included) plus `WINNER_SCHOOL` and `LOSER_SCHOOL`.
  - `MATCHUP_SERIES` and `MATCHUP_NEXT`: their sources' columns plus `SCHOOL` and `OPPONENT_SCHOOL`.
  - `MATCHUP_GAMES`: `TEAM_ID`, `OPPONENT_TEAM_ID`, `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `START_AT`, `SITE`, `TEAM_POINTS`, `OPPONENT_POINTS`, `GAME_RESULT`, `PROB_SOURCE`, `TEAM_PREGAME_WIN_PROB` and `UPSET_MAGNITUDE`.
  - `REFRESH_TIMELINE`: `RUN_ID`, `RUN_KIND`, `RUN_ARGS`, `RUN_STATUS`, `STEP_NO`, `STEP_NAME`, `STEP_STATUS`, `STARTED_AT`, `ENDED_AT`, `SECONDS`, `SLICES`, `ROWS_LOADED`, `CALLS`, `DETAIL`.
  - `FETCHES`: the columns of `CFB_META.FETCH_LOG` (`loader/run-logs`).
  - `GAMES`, `TEAM_SEASONS`, `RANKINGS` and `SP_RATINGS`: the columns of their `CFB_MODEL` sources with the sources' column comments (`model/games`, `model/teams-conferences`, `model/ratings-rankings`). Each view comment starts with its grain, says that it shows stored history as it is now (not as of a point), and names the serving views that answer the page questions.
- **Page contract** (Phase 6 and the chat's tools):
  - consumers read with autocommit on, so a publication's commit never meets an open read transaction (probe 1.1(e) records what happens otherwise);
  - pages start from `CURRENT_POINT` and may choose any point of `AS_OF_POINTS` without `IS_AFTER_CURRENT`;
  - the scorecard headline is the `IS_HEADLINE` row. When its freeze kind is `ALL`, the page labels it "reconstructed at the as-of point (leak-free, made after kickoff)";
  - an upset rank reads "since <`ALL_TIME_FROM_SEASON`>", a shared rank shows as "T-37", and `PROB_SOURCE` stands next to every rank;
  - what-ifs pass `CURRENT_POINT.WHATIF_RUN_ID` to `analyses/whatif.py`. An empty result or an unknown-column error means the what-if run changed: the page reloads `WHATIF_RUN` and `WHATIF_GAMES`. While `WHATIF_VIEWS_CURRENT` is FALSE the page offers no toggles;
  - a forced run's rooting guide is labeled with its `FIXED_RESULTS` ("with these results forced"), the toggled guide as conditional (`analyses/rooting-guide`);
  - the live preview and the quota badge read `CFB_API_LIVE` (`api/on-demand`).
- **No functions.** Exasol's SQL functions return one value and cannot serve rows in `FROM` (`model/as-of`), so pages filter views, and what-if conditions go through `analyses/whatif.py` (`analyses/what-if`). Neither schema has functions, scripts or tables.
- **Calls:** every `CFB_API` view costs 0 CFBD calls and makes no network request. Of `CFB_API_LIVE`, `GAME_PREVIEW` costs one call per query and `QUOTA` reads CFBD's `/info`, which does not count against the quota but needs the network, or a recording in replay mode.
- **Stability:** the view names, their grains and the columns named here are the contract. A later phase that changes them changes this spec. New views and new columns may be added.
- **Dictionary:** `make api-docs` writes `docs/api-dictionary.md` (no database) from `sql/api/`, `sql/api_live/` and the templates of the generated views: per view its grain, columns and comments, in file order.
- The examples run in the integration-test namespace, deployed with `make deploy ARGS="--prefix TEST_"` and `make grants ARGS="--prefix TEST_"`, over the sim league with 2026 week 9 as the current point, in replay mode, with `SIM_CONFIG` 2,000 seasons in batches of 500.

## Scenarios

### Scenario: The serving schemas hold exactly the documented views, each described by its grain

* *GIVEN* a deployed namespace with a publication
* *WHEN* `EXA_ALL_OBJECTS` and `EXA_ALL_COLUMNS` are read for `CFB_API` and `CFB_API_LIVE`
* *THEN* `CFB_API` MUST hold exactly the 26 views of the object table and `CFB_API_LIVE` exactly `GAME_PREVIEW` and `QUOTA`, and neither schema any table, function or script
* *AND* every view comment MUST start with "One row per", and every column MUST have a comment
* *AND* the comment of `GAME_PREVIEW` MUST say that each query makes one CFBD call

### Scenario: No serving view reads the raw mirrors or a broad API table

* *GIVEN* a deployed namespace with a publication
* *WHEN* the direct references of every view of `CFB_API` and `CFB_API_LIVE` are read from `EXA_ALL_DEPENDENCIES`
* *THEN* each reference of a `CFB_API` view MUST be an object of `CFB_API`, `CFB_PRED`, `CFB_SIM` or `CFB_MODEL`, or one of the `CFB_META` objects `REFRESH_LOG`, `LOAD_RUN` and `FETCH_LOG`
* *AND* the references of `CFB_API_LIVE` MUST be exactly the virtual table `GAMES_PREVIEW` and `CFB_META.QUOTA`
* *AND* the views of both schemas MUST NOT reference `CFB_CORE`, and the `CFB_API` views MUST NOT reference any virtual table

### Scenario: The current point tells the pages what to show by default

* *GIVEN* the sim league with 2026 week 9 as the current point and a publication of a run at 2026 week 9
* *WHEN* `CFB_API.CURRENT_POINT` is queried
* *THEN* it MUST return one row with `AS_OF_SEASON` 2026, `AS_OF_WEEK_SEQ` 9, `NEXT_WEEK_SEQ` 10, the publication's id as `PUBLICATION_ID` and `WHATIF_PUBLICATION_ID`, and the published run as `WHATIF_RUN_ID`
* *AND* `WHATIF_VIEWS_CURRENT` MUST be TRUE

### Scenario: The as-of points say where each page has data

* *GIVEN* the predictions frozen by `REFRESH_WEEK(2026, 9, 'regular')`, a publication at 2026 week 9 and no publication at week 8
* *WHEN* `CFB_API.AS_OF_POINTS` is queried for 2026
* *THEN* week 9 MUST have `IS_CURRENT`, `HAS_PUBLICATION` and `HAS_PREDICTIONS` TRUE
* *AND* week 8 MUST have `HAS_PUBLICATION` FALSE and `HAS_PREDICTIONS` TRUE (its next week was reconstructed)
* *AND* every point with `HAS_PUBLICATION` TRUE MUST have `IS_AFTER_CURRENT` FALSE

### Scenario: The refresh timeline shows every step of the weekly chain

* *GIVEN* replay recordings of the sim league's 2026 weekly slices
* *WHEN* the presenter runs `REFRESH_WEEK(2026, 9, 'regular')` and then `CFB_API.REFRESH_TIMELINE` is queried for that run
* *THEN* it MUST list `CHECK`, the `FETCH` steps, `MODEL`, `PREDICT`, `SIMULATE` and `PUBLISH` in step order, each with its status, seconds, rows and calls
* *AND* `CFB_API.FETCHES` MUST hold the run's fetch-log rows

### Scenario: The chat can answer questions outside the pages from the model views

* *GIVEN* the sim league with the history variant of `analyses/upsets`
* *WHEN* `CFB_API.GAMES` is queried for Ashford's 1995 game, `TEAM_SEASONS` for Ashford in 2025, `RANKINGS` for the AP poll of 2026 week 9, and `SP_RATINGS` for 2025
* *THEN* each MUST return the same rows and values as its `CFB_MODEL` source for the same filter
* *AND* each view comment MUST start with "One row per" and say that it shows stored history as it is now
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: The data dictionary matches the deployed schema

* *GIVEN* a deployed namespace with a publication
* *WHEN* the owner runs `make api-docs` and compares `docs/api-dictionary.md` with `EXA_ALL_COLUMNS` of `CFB_API` and `CFB_API_LIVE`
* *THEN* every view and column of both schemas MUST appear with its comment, the generated what-if columns as their `G<game id>` pattern
* *AND* running `make api-docs` again MUST leave the file unchanged
