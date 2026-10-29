# Feature: Playoff odds, what-if and the rooting guide

The odds page, the team outlook and the chat show the simulation engine's numbers (Phase 4, `CFB_SIM`): every team's playoff odds with arrows since the last week, the run behind them ("N seasons in S seconds"), what-if toggles ("if Auburn wins this weekend…") and the games each team's fans should root for. These views pass Phase 4's objects through under the serving names, so the app and the chat never read `CFB_SIM`, and the run the pages show is decided in one place, by Phase 4.

## Background

- **Source of truth:** Phase 4's views over done runs. The run shown at a point is `CFB_SIM.POINT_RUN` (its latest done run); the what-if run is `CFB_SIM.CURRENT_RUN` (the run of the newest point at or before the current one, at most one row). No serving view is generated per run, and runs are never dropped (Phase 4).
- **Views** (each shows its source's columns under the same names unless stated):

  | View | One row per | Source | Columns |
  |---|---|---|---|
  | `PLAYOFF_ODDS` | as-of point with a shown run, and eligible team | `CFB_SIM.ODDS_MOVEMENT` | `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `RUN_ID`, `TEAM_ID`, `SCHOOL`, `CONFERENCE`, `TIER`, `ODDS_RANK`, `P_IN_CFP`, `P_BYE`, `P_QUARTERFINAL`, `P_SEMIFINAL`, `P_FINAL`, `P_CHAMPION`, `P_CONF_CHAMPION`, `EXPECTED_WINS`, `MEAN_SEED`, `PREV_AS_OF_LABEL`, `PREV_P_IN_CFP`, `DELTA_P_IN_CFP`, `DELTA_P_CHAMPION`, `RANK_CHANGE`, `MOVE` |
  | `PUBLISHED_RUNS` | as-of point with a shown run | `CFB_SIM.POINT_RUN` joined to `SIM_RUN` | `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `RUN_ID`, `PREV_RUN_ID`, `RUN_ORIGIN`, `RUN_LABEL`, `N_SIMS`, `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND`, `INSTANCES`, `NODES`, `ENGINE_VERSION`, `ENDED_AT` |
  | `WHATIF_RUN` | the what-if run (one row, or none before the first run) | `CFB_SIM.CURRENT_RUN` | `RUN_ID`, `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`, `N_SIMS`, `NEXT_WEEK_SEQ` |
  | `WHATIF_GAMES` | remaining game of the what-if run | `CFB_SIM.SIM_RUN_GAME` (`GAME_ROLE` REMAINING) of `CURRENT_RUN`, schools from `SIM_RUN_TEAM` | `RUN_ID`, `GAME_ID`, `PATH_POS`, `SEASON`, `WEEK_SEQ`, `IS_NEXT_WEEK`, `START_AT`, `HOME_TEAM_ID`, `HOME_SCHOOL`, `AWAY_TEAM_ID`, `AWAY_SCHOOL`, `HOME_WIN_PROB`, `N_HOME_WINS` |
  | `WHATIF_OUTCOMES` | done run and simulated season | `CFB_SIM.SIM_PATH` | `RUN_ID`, `SIM_ID`, `OUTCOMES` (character `PATH_POS` is 1 for a home win, 0 for an away win) |
  | `WHATIF_PLAYOFF` | done run, simulated season and playoff team | `CFB_SIM.SIM_TEAM` | `RUN_ID`, `SIM_ID`, `TEAM_ID`, `SEED`, `BYE`, `REACHED_ROUND`, `CHAMPION` |
  | `WHATIF_TEAMS` | done run and roster team | `CFB_SIM.SIM_RUN_TEAM` | `RUN_ID`, `TEAM_ID`, `SCHOOL`, `CONFERENCE`, `TIER`, `IS_ELIGIBLE` |
  | `ROOTING_GUIDE` | done run, team in the run's leverage counts, and remaining game | `CFB_SIM.ROOTING_GUIDE` | every column of the source: `RUN_ID`, `TEAM_ID`, `GAME_ID`, `SEASON`, `WEEK_SEQ`, `IS_NEXT_WEEK`, `HOME_TEAM_ID`, `HOME_SCHOOL`, `AWAY_TEAM_ID`, `AWAY_SCHOOL`, `ROOT_FOR_TEAM_ID`, `ROOT_FOR_SCHOOL`, `P_IF_HOME_WIN`, `P_IF_AWAY_WIN`, `LEVERAGE`, `GUIDE_RANK`, `NEXT_WEEK_RANK`, `LOW_SAMPLE` |
  | `ANALYSIS_SETTINGS` | setting | `CFB_SIM.SIM_PARAMS` and constants | `SETTING_NAME`, `SETTING_NUMBER`, `SETTING_NOTE`: `MOVE_THRESHOLD` and `MIN_BRANCH_SAMPLE` from `SIM_PARAMS`; `MIN_EFFECTIVE_SAMPLE` 1000, `MAX_CONDITIONS` 16 and `ALL_TIME_FROM_SEASON` 1978 as constants |

- **`CURRENT_POINT` and `AS_OF_POINTS`** fill their engine columns as `api/serving-contract` describes, from `CFB_SIM.POINT_RUN`, `CURRENT_RUN`, `PREDICTION` and `PREDICTION_GRADED`.
- **What-if SQL** comes from `analyses/whatif.py`, a thin adapter over Phase 4's `engine/whatif.py` (pure, standard library only):
  - `Condition(game_id: int, home_wins: bool)`, `MAX_CONDITIONS` 16, `ConditionError`, and `METHOD`, the sentence that names the method ("conditioning: the simulated seasons in which these results happened");
  - `what_if_odds_sql(run_id, conditions, games, *, api_schema="CFB_API")`, where `games` maps the `GAME_ID`s of `WHATIF_GAMES` to their `PATH_POS`. It refuses an unknown or repeated game, more than 16 conditions, and ids that are not `int` (`bool` included) before any SQL is built, then calls Phase 4's builder with the serving names (`WHATIF_OUTCOMES`, `WHATIF_PLAYOFF`, `WHATIF_TEAMS`). The SQL names only `CFB_API` objects, so the app and chat roles can run it.
  - A what-if names its run in every filter, so its numbers stay right for that run even after a newer run exists. There is no conditional rooting guide: the guide is the precomputed `ROOTING_GUIDE` of the run.
- The examples run in the integration-test namespace over Phase 3's synthetic league with Phase 4's simulation schema, test constants and two done runs, at 2026 week 8 and week 9 (2,000 seasons in batches of 500). The current point is 2026 after week 9.

## Scenarios

### Scenario: The odds and the guide of every shown run are served

* *GIVEN* the two done runs
* *WHEN* `PLAYOFF_ODDS`, `PUBLISHED_RUNS` and `ROOTING_GUIDE` are queried
* *THEN* each MUST return the same rows and values as `CFB_SIM.ODDS_MOVEMENT`, `POINT_RUN` with `SIM_RUN`, and `CFB_SIM.ROOTING_GUIDE`
* *AND* the week-9 rows of `PLAYOFF_ODDS` MUST carry the movement against the week-8 run

### Scenario: The current point names the run the pages and the what-if use

* *GIVEN* the two done runs and the frozen predictions of their points
* *WHEN* `CURRENT_POINT`, `AS_OF_POINTS`, `WHATIF_RUN` and `WHATIF_GAMES` are queried
* *THEN* `CURRENT_POINT` MUST name the week-9 run as `RUN_ID`, `PUBLICATION_ID`, `WHATIF_RUN_ID` and `WHATIF_PUBLICATION_ID`, with `WHATIF_AS_OF_WEEK_SEQ` 9, `HAS_PUBLICATION` TRUE and `WHATIF_VIEWS_CURRENT` TRUE
* *AND* `AS_OF_POINTS` MUST have `HAS_PUBLICATION` TRUE at weeks 8 and 9 only, and `HAS_PREDICTIONS` TRUE at week 9
* *AND* `WHATIF_RUN` MUST be one row for that run, and `WHATIF_GAMES` its remaining games with their `PATH_POS`

### Scenario: A what-if over the serving views equals the engine's own

* *GIVEN* the week-9 run and a condition that a week-10 game's home team loses
* *WHEN* the app user runs the SQL of `analyses/whatif.py` over `CFB_API`, and the owner runs Phase 4's `engine/whatif.py` over `CFB_SIM` for the same run and condition
* *THEN* both MUST return the same matching-season count and the same count per team for making the playoff, a bye and the title
* *AND* the adapter MUST refuse a game id that is not in `WHATIF_GAMES` with `ConditionError` before any SQL is built
