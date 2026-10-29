# Feature: Simulation runs, odds, what-if and the rooting guide

"100,000 seasons in N seconds, in parallel on every core, in plain Python inside the database." One SQL call simulates the rest of a season from an as-of point and stores what the pages need: the playoff odds of every team, one outcome string per simulated season for the what-if filter ("if Auburn wins this weekend, how does that change their playoff odds?"), and the counts behind the rooting guide. Each run keeps its inputs, seed and code versions, so it can be repeated. Odds move from one point to the next, and the pages show by how much. The simulation count is the scale dial: 100,000 on the laptop, 1,000,000 on AWS.

## Background

- **Call:** `EXECUTE SCRIPT CFB_INFRA.SIMULATE(season, week, season_type, n_sims, sims_per_batch, run_seed, run_label)`. NULL arguments take their defaults: `season_type` `'regular'`, `n_sims` 100,000, `sims_per_batch` 5,000, `run_seed` season × 1,000 + the point's week sequence, `run_label` NULL. `make simulate SEASON=… WEEK=… [SIMS=…] [BATCH=…] [SEED=…] [LABEL=…] [TARGET=…]` wraps it and prints the result and the top 25 of `SIM_TEAM_ODDS`.
- **Refusals**, before any row is written: the run lock is held by another run (Phase 2's run lock in `CFB_META.LOAD_RUN`, kind `SIMULATE`); the point is not a row of `CFB_MODEL.AS_OF_POINT`; the season has no `SEASON_RULES` row; the point's `CUTOFF_AT` is NULL or not before `SELECTION_AT`; `sims_per_batch` is outside 100–20,000, `n_sims` is not a multiple of it, or there are more than 10,000 batches.
- **Steps**, in one script run under the lock. The script resolves the point first and filters every as-of view with literal season and week sequence values.
  1. Register the run in `SIM_RUN` (`RUNNING`) and commit.
  2. Freeze predictions for the point (`sim-engine/predictions`).
  3. Snapshot the inputs: `SIM_RUN_TEAM` (the roster: every FBS team-season of the point's season, plus the non-FBS opponents of remaining games), `SIM_RUN_GAME` (`REMAINING` games from `GAME_SCORE_AS_OF`: not a championship game, both teams known, at least one FBS team, kickoff before `SELECTION_AT`; and `PLAYED_CONF` games for head-to-head) and `SIM_RUN_PARAM`. `NOT_PLAYED` games and later games are left out and counted in `N_LEFT_OUT_GAMES`.
  4. Run the engine into `SIM_STAGE` (`sim-engine/engine-contract`).
  5. Split the stage into the outputs: `SIM_TEAM` from `T`, `SIM_PATH` from `P`, `TEAM_WINS` from `W`, `CONF_CHAMP` from `C`, `LEVERAGE` from `L`, `G` and `T`, `SIM_RUN_GAME.N_HOME_WINS` from `G`. Check that every batch reported and that all report one model digest.
  6. Truncate the stage, set the run `DONE` with its seconds and sizing, commit and release the lock.

  On any error the script rolls back, deletes the run's output rows, truncates the stage, sets the run `FAILED` with the message, commits, releases the lock and raises the error.
- **Outputs** (data tables of `CFB_SIM`, kept across deploys; readers use only `DONE` runs):
  - `SIM_RUN`: `RUN_ID`, `RUN_STATUS`, `RUN_ORIGIN` (`MANUAL` or `REFRESH`), `RUN_LABEL`, `LOAD_RUN_ID`, the point (`AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`), `RUN_SEED`, `N_SIMS`, `SIMS_PER_BATCH`, `N_BATCHES`, `N_TEAMS`, `N_GAMES`, `N_LEFT_OUT_GAMES`, `MODEL_DIGEST`, `ENGINE_VERSION`, `NUMPY_VERSION`, `PYTHON_VERSION`, `INSTANCES`, `NODES`, `STARTED_AT`, `ENDED_AT`, `SECONDS_INPUTS`, `SECONDS_ENGINE`, `SECONDS_OUTPUTS`, `SECONDS_TOTAL`, `SIMS_PER_SECOND`, `DETAIL`;
  - `SIM_TEAM`: `RUN_ID`, `SIM_ID`, `TEAM_ID`, `SEED`, `BYE`, `REACHED_ROUND`, `CHAMPION`: the 12 playoff teams of each simulated season only;
  - `SIM_PATH`: `RUN_ID`, `SIM_ID`, `OUTCOMES`: the outcome string of each simulated season;
  - `TEAM_WINS` (`RUN_ID`, `TEAM_ID`, `WINS`, `N`), `CONF_CHAMP` (`RUN_ID`, `TEAM_ID`, `N`);
  - `LEVERAGE`: `RUN_ID`, `TEAM_ID`, `GAME_IDX`, `N_HOME`, `N_HOME_IN_CFP`, `N_AWAY`, `N_AWAY_IN_CFP`, one row per remaining game and team that made the field in at least one season.
  - `SIM_RUN_GAME` maps each remaining game to its character: `PATH_POS` = `GAME_IDX` + 1.
- **Odds** (`CFB_SIM.SIM_TEAM_ODDS`): one row per `DONE` run and eligible roster team: `P_IN_CFP`, `P_BYE`, `P_QUARTERFINAL`, `P_SEMIFINAL`, `P_FINAL`, `P_CHAMPION` (shares of `N_SIMS`), `P_CONF_CHAMPION` (NULL for a team without a conference champion), `EXPECTED_WINS`, `MEAN_SEED` (over the seasons in which the team made the field) and `ODDS_RANK` (by `P_IN_CFP`, then `P_CHAMPION`, then `TEAM_ID`).
- **What-if:** a condition fixes the winner of a remaining game: `SUBSTR("OUTCOMES", <PATH_POS>, 1) = '1'` for a home win, `'0'` for an away win. The matching seasons are the `SIM_PATH` rows of the run that meet every condition; a team's conditional odds are its `SIM_TEAM` rows among them divided by their number. `engine/whatif.py` (pure, stdlib) builds the SQL for one team and for all teams, with at most 16 conditions, validated integers only, and configurable schema and table names, so the serving layer can reuse it over its own views. `make what-if RUN_ID=… TEAM=… WIN=next|<game id> [LOSE=…]` prints the team's odds before and after, and the number of matching seasons.
- **Rooting guide** (`CFB_SIM.ROOTING_GUIDE`): one row per `DONE` run, team in `LEVERAGE` and remaining game: `GAME_ID`, `SEASON`, `WEEK_SEQ`, `IS_NEXT_WEEK`, `HOME_TEAM_ID`, `HOME_SCHOOL`, `AWAY_TEAM_ID`, `AWAY_SCHOOL`, `P_IF_HOME_WIN` = `N_HOME_IN_CFP` / `N_HOME`, `P_IF_AWAY_WIN` = `N_AWAY_IN_CFP` / `N_AWAY`, `LEVERAGE` = their absolute difference, `ROOT_FOR_TEAM_ID` and `ROOT_FOR_SCHOOL` (the side with the higher probability), `GUIDE_RANK` and `NEXT_WEEK_RANK` (by `LEVERAGE`, over all games and over next week's games), and `LOW_SAMPLE` (either branch below `MIN_BRANCH_SAMPLE`, 100).
- **Shown runs and movement:**
  - `CFB_SIM.POINT_RUN`: one row per as-of point with a `DONE` run: the run shown for that point is its latest `DONE` run. `PREV_RUN_ID` and `PREV_AS_OF_WEEK_SEQ` name the shown run of the season's previous point that has one.
  - `CFB_SIM.CURRENT_RUN`: at most one row, the `POINT_RUN` of the newest point at or before `CFB_MODEL.CURRENT_AS_OF`. It is the run the what-if and the guide pages use.
  - `CFB_SIM.ODDS_MOVEMENT`: one row per `POINT_RUN` and eligible team, the `SIM_TEAM_ODDS` columns plus `PREV_AS_OF_LABEL`, `PREV_P_IN_CFP`, `DELTA_P_IN_CFP`, `DELTA_P_CHAMPION`, `RANK_CHANGE` and `MOVE`: `UP` or `DOWN` when `|DELTA_P_IN_CFP|` ≥ `MOVE_THRESHOLD` (0.0070), else `SAME`; `NEW` without a previous run.
- The examples run in the integration-test namespace on the synthetic league, deployed with test constants, at the point 2026 week 9.

## Scenarios

### Scenario: One call simulates the rest of the season and registers the run

* *GIVEN* the synthetic league and a deployed simulation schema
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.SIMULATE(2026, 9, 'regular', 2000, 500, NULL, 'it')`
* *THEN* the result MUST be one row with the run id, `DONE`, 2,000 seasons and the run's seconds
* *AND* `SIM_RUN` MUST show `RUN_SEED` 2026009, 4 batches, `INSTANCES` of at least 1, the engine, numpy and Python versions and a model digest
* *AND* `SIM_RUN_GAME` MUST hold, as `REMAINING`, every 2026 game that is `NEXT` or `REMAINING` at the point, has both teams known, is not a championship game and kicks off before `SELECTION_AT`, each with `PATH_POS` = `GAME_IDX` + 1
* *AND* `SIM_STAGE` MUST be empty

### Scenario: Only the playoff teams and one outcome string per season are stored

* *GIVEN* the run of the previous scenario
* *WHEN* its outputs are read
* *THEN* `SIM_TEAM` MUST hold exactly 12 rows for every `SIM_ID` from 0 to 1,999
* *AND* `SIM_PATH` MUST hold 2,000 rows whose `OUTCOMES` have `N_GAMES` characters each
* *AND* `P_IN_CFP` MUST sum to 12 and `P_CHAMPION` to 1 over the run's `SIM_TEAM_ODDS` rows, within 1e−9

### Scenario: A what-if filters the stored seasons

* *GIVEN* the run, a team T and T's next remaining game g, in which T is the home team
* *WHEN* the SQL of `engine/whatif.py` for team T with the condition "g home win" runs
* *THEN* the number of matching seasons MUST equal the number of `SIM_PATH` rows with `1` at g's `PATH_POS`
* *AND* T's conditional `P_IN_CFP` MUST equal T's `SIM_TEAM` rows among those seasons divided by their number
* *AND* both numbers MUST equal `N_HOME` and `N_HOME_IN_CFP` / `N_HOME` of T's `LEVERAGE` row for g

### Scenario: The rooting guide's counts are precomputed

* *GIVEN* the run
* *WHEN* `LEVERAGE` is compared with counts recomputed in SQL from `SIM_PATH` and `SIM_TEAM`
* *THEN* every row's four counts MUST equal the recomputed ones
* *AND* `ROOTING_GUIDE` MUST name the home team as `ROOT_FOR_TEAM_ID` exactly where `P_IF_HOME_WIN` > `P_IF_AWAY_WIN`

### Scenario: Odds move between points

* *GIVEN* `DONE` runs of the synthetic league at 2026 weeks 8 and 9, and a second, later `DONE` run at week 9
* *WHEN* `ODDS_MOVEMENT` is queried for week 9
* *THEN* its rows MUST come from the later week-9 run, with the week-8 run as the previous run
* *AND* `DELTA_P_IN_CFP` MUST equal the difference of the two runs' `P_IN_CFP`, and `MOVE` MUST be `UP`, `DOWN` or `SAME` by the 0.0070 threshold
* *AND* the week-8 rows MUST have `MOVE` `NEW`

### Scenario: A failed run leaves no outputs and frees the lock

* *GIVEN* the test hook `fail_batch` set to 2
* *WHEN* the presenter runs `SIMULATE(2026, 9, 'regular', 2000, 500, NULL, NULL)`
* *THEN* the call MUST fail with an error that names batch 2
* *AND* the run MUST be `FAILED` with the message, with no rows in `SIM_TEAM`, `SIM_PATH`, `TEAM_WINS`, `CONF_CHAMP` or `LEVERAGE`, and `SIM_STAGE` MUST be empty
* *AND* a following `SIMULATE` without the hook MUST succeed

### Scenario: Runs outside the rules or under another lock are refused

* *GIVEN* a session that holds the run lock as a running `BACKFILL`
* *WHEN* the presenter runs `SIMULATE(2026, 9, 'regular', 2000, 500, NULL, NULL)`, and, after the lock is free, `SIMULATE(2025, 9, 'regular', 2000, 500, NULL, NULL)` and `SIMULATE(2026, 9, 'regular', 2000, 300, NULL, NULL)`
* *THEN* the first call MUST be refused naming the running `BACKFILL`
* *AND* the second MUST be refused because 2025 has no rule set, and the third because 2,000 is not a multiple of 300
* *AND* `SIM_RUN` MUST NOT gain a row
