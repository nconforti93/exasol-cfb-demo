# Feature: Simulation runs and the run registry

"100,000 seasons in N seconds, in parallel on every core, in plain Python inside the database." One SQL call simulates the rest of a season from any point. It stores what the odds pages and the what-if queries need, and records everything that makes the run repeatable: inputs, seed, batch layout, code versions, machine sizing and the time of every step. A second call re-simulates a stored run with some results forced, using the same draws, so the difference is the forced results alone. The simulation count is the scale dial: 100,000 on a laptop, a million and more on AWS.

## Background

- **Call:** `EXECUTE SCRIPT CFB_INFRA.SIMULATE(season, week, season_type, n_sims, sims_per_batch, run_seed, params_version, run_label)`. A NULL argument takes its default:
  - `season_type` `'regular'`;
  - `n_sims` and `sims_per_batch` from `CFB_SIM.SIM_CONFIG` (`sim-engine/deploy`: 100,000 and 5,000 on the laptop, 1,000,000 and 10,000 on AWS, unless the deployment overrides them);
  - `run_seed` = season × 1,000 + the point's week sequence;
  - `params_version` `CURRENT`;
  - `run_label` NULL.

  Never run it `WITH OUTPUT`, which discards the returned timeline.
- `make simulate SEASON=… WEEK=… [SEASON_TYPE=…] [SIMS=…] [BATCH=…] [SEED=…] [PARAMS=…] [LABEL=…] [TARGET=…]` runs the call and prints the timeline. `make simulate BASE=… FIX=… [LABEL=…]` runs a forced re-sim (below). `make sim-verify RUN=…` and `make sim-drop RUN=… CONFIRM=yes` are below.
- **Selection week.** `CFB_SIM.SELECTION_WEEK` has one row per season with regular-season weeks. The committee selects the field the day after the conference championship games, so later regular-season games (Army–Navy since 2024) count for nothing. Columns:
  - `SELECTION_WEEK_SEQ` is the week of the season's last championship game of a power conference (`CFB_MODEL.CONFERENCE_CHAMPIONSHIP_GAME` of a conference whose teams have `IS_POWER_CONFERENCE`), with `SELECTION_WEEK_SOURCE` `CCG`;
  - while no such game is stored (the current season before its pairings are set, as on CFBD), it is the week after the last regular-season week of `CFB_MODEL.SEASON_WEEK` that ends before the first Saturday of December (UTC), with `SELECTION_WEEK_SOURCE` `DECEMBER`;
  - `LAST_REGULAR_WEEK_SEQ` and `FIRST_DECEMBER_SATURDAY` for reference.

  Like `LAST_REGULAR_WEEK_SEQ`, it describes the schedule, not results, so it is the same at every point of a season.
- **The call is refused, with a message and no registry row, when:**
  - the point is not in `CFB_MODEL.AS_OF_POINT`, or comes after the season's selection week (the engine selects the field itself);
  - the season has no rule set (`sim-engine/selection-rules`);
  - `n_sims` is below 1 or not a multiple of `sims_per_batch`;
  - `sims_per_batch` is outside 100–20,000, or the run needs more than 10,000 batches;
  - another loader or simulation run holds the lock.
- **Lock and stale runs** (`loader/run-logs`):
  - `SIMULATE`, `SIMULATE_FROM` and `DROP_SIM_RUN` take the loader's run lock as run kind `SIMULATE`. They are scripts, so a `SIMULATE` row is stale under the rule for script runs (`BACKFILL`, `REFRESH_WEEK`): its session is gone, it belongs to the session that takes the lock, or its session is idle.
  - `make sim` and `make calibrate` hold the lock from the host as run kind `DEPLOY`. A host session is idle between its statements, so a `DEPLOY` row is stale only when its session is gone or it belongs to the session that takes the lock, like a snapshot or restore row.
  - The refresh step runs under the refresh's own lock (`sim-engine/refresh-step`).
- **Abandoned runs.** A run that is killed (`KILL STATEMENT`, a client abort, a lost session) cannot clean up after itself: after a kill, every later statement of the script fails (loader probe 1.1j, error `R0003`). So whoever takes the lock next cleans up. Right after taking the lock, and at the start of the refresh step, the script marks every other `SIM_RUN` that is still `RUNNING` as `ABANDONED`. It can do so because only the lock holder runs. For each such run it:
  - sets its `RUNNING` steps to `ABANDONED`;
  - deletes its rows from `SIM_TEAM`, `LEVERAGE`, `TEAM_WINS` and `CONF_CHAMP`;
  - drops its outcome table;
  - truncates the stage.

  Its registry, step, batch and input rows stay. Consumers read only `DONE` runs, so they never see a run between its kill and the cleanup.
- **Steps.** Each step is a row of `CFB_SIM.SIM_RUN_STEP` with its start, end, seconds, rows written and a detail, committed when the step starts and when it ends, so other sessions see progress:
  1. `INPUTS`: snapshot the run's inputs.
     - First the teams, from `ENGINE_TEAM_AS_OF` into `SIM_RUN_TEAM`. The team index follows the team id.
     - Then the games and the parameters, from `ENGINE_GAME_AS_OF` and `ENGINE_PARAM_AS_OF` into `SIM_RUN_GAME` and `SIM_RUN_PARAM`. These views carry team ids. The snapshot resolves the indices, the same-conference flag and the guarantee index through `SIM_RUN_TEAM`, so `ENGINE_TEAM_AS_OF` is evaluated once per run. The game index follows kickoff, then game id.
     - Only regular-season games up to the selection week become `REMAINING` or `CCG` rows.
  2. `CHECKS`: evaluate `CFB_SIM.SIM_INPUT_CHECKS` for the run. A failed `ERROR` check stops the run before the engine.

     | Check | Severity | Fails when |
     |---|---|---|
     | `TOO_FEW_ELIGIBLE_TEAMS` | ERROR | fewer than 12 eligible teams |
     | `POWER_CONFERENCE_EMPTY` | ERROR | a `P4` conference of the season has no team (the rule set needs its champion) |
     | `CONFERENCE_PAIR_REPEATED` | ERROR | two teams of one conference meet more than once in conference games |
     | `CROSS_CONFERENCE_FLAG` | WARNING | a game flagged as a conference game is between different conferences; it is simulated as a non-conference game |
     | `GUARANTEE_TEAM_MISSING` | WARNING | the rule set's guaranteed school is not an eligible team of the run |
     | `GAMES_NOT_PLAYED` | WARNING | a game of a week up to the point was not completed; it is left out |
     | `GAMES_AFTER_SELECTION` | WARNING | regular-season games lie after the selection week; they are left out, and the check names them |
     | `CCG_AFTER_SELECTION` | WARNING | a game flagged as a championship game lies after the selection week; it is left out |
     | `SELECTION_WEEK_ESTIMATED` | WARNING | the season's power championship games are not stored yet, so the selection week comes from the December rule |

  3. `PATH_TABLE`: create the run's outcome table (below).
  4. `ENGINE`: the fan-out, one engine call per batch (`sim-engine/engine-contract`) into the stage table.
  5. `OUTPUTS`: move the stage rows into the run's tables. Every batch must report the same model digest and engine version, and every batch must be present.
  6. `LEVERAGE`: build the run's leverage rows.
  7. `FINISH`: truncate the stage (the lock makes the run its only writer) and mark the run `DONE`.
- `CFB_SIM.SIM_RUN`, the run registry, has one row per run:
  - `RUN_ID`, `RUN_STATUS` (`RUNNING`, `DONE`, `FAILED`, `ABANDONED` or `DROPPED`), `RUN_ORIGIN` (`MANUAL`, `REFRESH` or `FORCED`), `LOAD_RUN_ID` (the refresh run, or the manual run's lock row), `BASE_RUN_ID` (the base run of a forced re-sim) and `RUN_LABEL`;
  - the point (`AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`), `SELECTION_WEEK_SEQ`, `RULE_SET` and `PARAMS_VERSION`;
  - the reproducibility key: `RUN_SEED`, `N_SIMS`, `SIMS_PER_BATCH`, `N_BATCHES`, `MODEL_DIGEST`, `ENGINE_VERSION`, `NUMPY_VERSION` and `PYTHON_VERSION`;
  - the machine: `DB_VERSION`, `NODES`, `DB_RAM_GIB` (from `EXA_SYSTEM_EVENTS` at the run's start), `INSTANCES` (distinct engine VMs seen) and `BATCHES_PER_INSTANCE`. D19 recommends at least 2. The laptop default of 20 batches of 5,000 gives 2.5 on an 8-core start; the value is recorded, not enforced;
  - `PATH_TABLE` (the name of the run's outcome table; NULL once dropped), `N_TEAMS`, `N_GAMES`, `SESSION_ID`, `STARTED_AT`, `ENDED_AT`, `SECONDS_TOTAL`, `SECONDS_ENGINE`, `SIMS_PER_SECOND` and `DETAIL`.
- `CFB_SIM.SIM_RUN_BATCH`: one row per batch, with its number of seasons, checksum, model digest, engine seconds per stage, VM id, node id and peak memory.
- **Outputs.** Only runs with `RUN_STATUS` `DONE` are ever read by consumers.
  - `SIM_RUN_TEAM` (the roster, D20 `TEAMS`): one row per team of the run, with index, id, school, conference, tier, eligibility, rating, strength of schedule and records so far.
  - `SIM_RUN_GAME`: one row per game row of the run's inputs, with the game id, role, teams, `MU`, `SIGMA`, `TEAM_SIGMA`, `FIXED_HOME_WIN` and, for remaining games, the outcome column name.
  - `SIM_RUN_PARAM`: the run's parameters.
  - `CFB_SIM.SIM_TEAM` (sparse, D20): one row per run, simulated season and playoff team: `RUN_ID`, `SIM_ID`, `TEAM_ID`, `SEED`, `BYE`, `REACHED_ROUND`, `CHAMPION`. It is partitioned by `RUN_ID`.
  - `CFB_SIM.SIM_PATH_R<run id>` (layout C, D20): one row per simulated season, with `RUN_ID`, `SIM_ID` and one `BOOLEAN` column per remaining game, `TRUE` for a home win. Each game column is named `G<game index + 1, 4 digits>`, and its comment names the week, the teams and the game id. The table name changes per run, so queries over it are generated from `SIM_RUN.PATH_TABLE` and `SIM_RUN_GAME`.
  - `CFB_SIM.LEVERAGE`: one row per run, team that reaches the playoff in at least one season, and remaining game: `N_HOME`, `N_HOME_IN_CFP`, `N_AWAY`, `N_AWAY_IN_CFP` (the seasons in which the home or the away team won the game, and of those, the seasons in which the team reached the playoff).
  - `CFB_SIM.TEAM_WINS`: one row per run, team and number of regular-season wins up to the selection week, with the seasons that ended with it. `CFB_SIM.CONF_CHAMP`: one row per run and team, with the seasons in which it won its conference.
  - `CFB_SIM.SIM_TEAM_ODDS`: a view with one row per `DONE` run and eligible roster team: seasons, `P_IN_CFP`, `P_BYE`, `P_QUARTERFINAL`, `P_SEMIFINAL`, `P_FINAL`, `P_CHAMPION`, `P_CONF_CHAMPION`, `EXPECTED_WINS` and `MEAN_SEED` (over its playoff seasons). A team with no playoff season has probability 0.
- **Forced re-sims:** `EXECUTE SCRIPT CFB_INFRA.SIMULATE_FROM(base_run_id, fixed_results, run_label)`, with `fixed_results` a list such as `'401628571=A,401628569=H'` (game id, then `H` for a home win or `A` for an away win).
  - It takes the lock and copies the base run's inputs, with `FIXED_HOME_WIN` set on the named games. The base run must be `DONE` or `DROPPED`, whose inputs stay.
  - It then runs steps 2 to 7 with the base run's seed, batch size and number of seasons.
  - The new run has `RUN_ORIGIN` `FORCED` and the base run as `BASE_RUN_ID`. A fixed result uses only its own game's draws (`sim-engine/engine-contract`), so every other game's outcomes equal the base run's, season by season.
  - It is refused when a named game is not a remaining game of the base run.

  Phase 5's forced re-sims call it; they need no engine change.
- **Verification:** `make sim-verify RUN=…` runs the engine in `SUMMARY` mode on the run's stored inputs with its seed and batch size. It compares each batch's checksum and the model digest with `SIM_RUN_BATCH`, and writes nothing.
- **Dropping:** `EXECUTE SCRIPT CFB_INFRA.DROP_SIM_RUN(run_id)` (`make sim-drop`) takes the lock, deletes the run's rows from every output table, drops its outcome table, and sets the run to `DROPPED`. Registry, steps, batches and inputs stay. Outcome tables accumulate with every run, so runs nobody reads are dropped this way (retention in the user guide).
- **Failure:** any error stops the run. The run's output rows are deleted, its outcome table is dropped, the stage is truncated, and the step and the run are marked `FAILED` with the error. The lock is released, the log is committed, and the error is raised again, naming the step and the run.
- No step reads the virtual schema: a run costs zero CFBD calls.
- The examples run over the sim league in the integration-test namespace with the test parameters, at 2026 week 9, with 4,000 seasons in 8 batches of 500 unless stated.

## Scenarios

### Scenario: One call simulates the rest of the season and returns its timeline

* *GIVEN* the sim league and no other run
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.SIMULATE(2026, 9, 'regular', 4000, 500, NULL, NULL, NULL)`
* *THEN* the result MUST list the steps `INPUTS`, `CHECKS`, `PATH_TABLE`, `ENGINE`, `OUTPUTS`, `LEVERAGE` and `FINISH`, each with its seconds and rows, followed by a summary with the run id, 4,000 seasons, the total seconds and seasons per second
* *AND* `CFB_SIM.SIM_RUN` MUST show the run as `DONE` with `RUN_SEED` 2026009, `N_BATCHES` 8 and `RUN_ORIGIN` `MANUAL`
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: The registry records what makes the run repeatable

* *GIVEN* the run of the previous scenario
* *WHEN* its `CFB_SIM.SIM_RUN` row is read
* *THEN* it MUST hold the model digest and engine version reported by every batch, numpy 1.26.4, the Python version, the database version, the node count, the DB RAM and the number of engine instances seen
* *AND* `CFB_SIM.SIM_RUN_BATCH` MUST hold 8 rows with checksums, and `SIM_RUN_STEP` 7 rows whose seconds add up to the run's total within one second

### Scenario: The run keeps its inputs when the data changes

* *GIVEN* the run of the first scenario
* *WHEN* a week-10 result is stored afterwards, and `SIM_RUN_TEAM` and `SIM_RUN_GAME` of the run are read again
* *THEN* they MUST be unchanged, while `CFB_SIM.ENGINE_GAME_AS_OF` at 2026 week 10 differs from them

### Scenario: A run can be verified from its stored inputs

* *GIVEN* the run of the first scenario and the later week-10 result
* *WHEN* the owner runs `make sim-verify RUN=<run id> ARGS="--prefix TEST_"`
* *THEN* it MUST report all 8 batches as matching, with the run's model digest
* *AND* no output table MUST gain a row

### Scenario: Only the playoff teams of each season are stored

* *GIVEN* the run of the first scenario
* *WHEN* `CFB_SIM.SIM_TEAM` and `CFB_SIM.SIM_TEAM_ODDS` are queried for it
* *THEN* `SIM_TEAM` MUST hold exactly 48,000 rows, 12 per season
* *AND* `SIM_TEAM_ODDS` MUST hold one row for each eligible team of the run's roster, with `P_IN_CFP` equal to the team's `SIM_TEAM` rows divided by 4,000, and 0 for teams without any
* *AND* the `P_IN_CFP` values MUST add up to 12

### Scenario: Every simulated game's outcome is stored per season

* *GIVEN* the run of the first scenario
* *WHEN* its outcome table, named in `SIM_RUN.PATH_TABLE`, is read
* *THEN* it MUST hold 4,000 rows and one `BOOLEAN` column for each remaining game of `SIM_RUN_GAME`, named as listed there
* *AND* the comment of the column of the week-12 game Ashford against Brookvale MUST name the week, both teams and its game id
* *AND* that game's `SIM_RUN_GAME` row MUST have `SIGMA` 15.26 and `TEAM_SIGMA` 2.00
* *AND* the share of `TRUE` in that column MUST be within 0.03 of the game's `HOME_WIN_PROB` 0.8289

### Scenario: The rooting guide's counts are precomputed

* *GIVEN* the run of the first scenario
* *WHEN* `CFB_SIM.LEVERAGE` is compared with counts recomputed from `SIM_TEAM` and the outcome table for Brookvale and every remaining game
* *THEN* `N_HOME`, `N_HOME_IN_CFP`, `N_AWAY` and `N_AWAY_IN_CFP` MUST be equal for every game
* *AND* `N_HOME` + `N_AWAY` MUST be 4,000 for every row

### Scenario: Win and title distributions cover every team

* *GIVEN* the run of the first scenario
* *WHEN* `CFB_SIM.TEAM_WINS` and `CFB_SIM.CONF_CHAMP` are queried for it
* *THEN* the seasons of each team in `TEAM_WINS` MUST add up to 4,000, with no win count below the team's wins so far
* *AND* the `CONF_CHAMP` seasons of each conference that has a champion MUST add up to 4,000

### Scenario: The selection week is the week of the power championship games

* *GIVEN* the sim league variant with a regular-season game Ivel at Eastport in week 16 of 2024 and of 2026, after the championship games of week 15
* *AND* 2026's championship games are not scheduled yet, and its week 15 holds Saturday 5 December 2026
* *WHEN* `CFB_SIM.SELECTION_WEEK` is queried for 2024 and 2026
* *THEN* 2024 MUST have `SELECTION_WEEK_SEQ` 15 with source `CCG` and `LAST_REGULAR_WEEK_SEQ` 16
* *AND* 2026 MUST have `SELECTION_WEEK_SEQ` 15 with source `DECEMBER` and `LAST_REGULAR_WEEK_SEQ` 16

### Scenario: Games after the selection week are not simulated

* *GIVEN* the sim league variant of the previous scenario
* *WHEN* the owner runs `CFB_INFRA.SIMULATE` for 2024 week 13, and then for 2024 week 16
* *THEN* the first run MUST be `DONE` with no week-16 game in `SIM_RUN_GAME`, and its check rows MUST report `GAMES_AFTER_SELECTION` naming Ivel at Eastport
* *AND* the second call MUST be refused with a message that names the selection week 15
* *AND* `CFB_SIM.SIM_RUN` MUST gain no row for the second call

### Scenario: A run is refused while another run holds the lock

* *GIVEN* a refresh run of another session that is `RUNNING` and not idle
* *WHEN* the owner runs `CFB_INFRA.SIMULATE` for 2026 week 9
* *THEN* the call MUST fail with a message that names the running run
* *AND* `CFB_SIM.SIM_RUN` MUST NOT gain a row

### Scenario: An idle deploy keeps the lock until its session ends

* *GIVEN* another session that holds the run lock as kind `DEPLOY` and is idle between its statements
* *WHEN* the owner runs `CFB_INFRA.SIMULATE` for 2026 week 9
* *THEN* the call MUST fail with a message that names the deploy's run
* *AND* after that session disconnects, the same call MUST run to `DONE`, and the deploy's lock row MUST be `ABANDONED`

### Scenario: A failed run leaves no outputs

* *GIVEN* a test hook that makes the engine fail in batch 5
* *WHEN* the owner runs `CFB_INFRA.SIMULATE` for 2026 week 9
* *THEN* the call MUST fail with an error that names the step `ENGINE` and the run
* *AND* the run MUST be `FAILED`, with no rows in `SIM_TEAM`, `LEVERAGE`, `TEAM_WINS`, `CONF_CHAMP` or the stage, and no outcome table
* *AND* the next run MUST start without waiting for the lock

### Scenario: A killed run is cleaned up by the next run

* *GIVEN* a run in another session that was stopped with `KILL STATEMENT` while it waited at the start of step `LEVERAGE` (test hook `TEST_SLEEP_BEFORE_STEP`), whose session is still open and idle
* *AND* that run is still `RUNNING`, with its `SIM_TEAM` rows, its tallies, its stage rows and its outcome table stored
* *WHEN* the owner runs `CFB_INFRA.SIMULATE` for 2026 week 9
* *THEN* the killed run MUST be `ABANDONED`, with its step `LEVERAGE` `ABANDONED`
* *AND* no output table and no stage row MUST hold the killed run's rows, and its outcome table MUST be gone
* *AND* its registry, step, batch and input rows MUST remain, and the new run MUST be `DONE`

### Scenario: An input error stops the run before the engine

* *GIVEN* the sim league variant in which two SEC teams are scheduled to meet twice in conference games
* *WHEN* the owner runs `CFB_INFRA.SIMULATE` for 2026 week 9
* *THEN* the call MUST fail at the step `CHECKS`, naming `CONFERENCE_PAIR_REPEATED` and both teams
* *AND* the step `ENGINE` MUST NOT run

### Scenario: Points after the selection week and uneven batches are refused

* *GIVEN* the sim league, whose 2025 selection week is week 15
* *WHEN* the owner runs `CFB_INFRA.SIMULATE` for 2025 postseason week 1, then for 2026 week 9 with 4,000 seasons in batches of 600
* *THEN* both calls MUST fail with a message that names the reason
* *AND* `CFB_SIM.SIM_RUN` MUST NOT gain a row

### Scenario: A forced result re-simulates from the base run's inputs and draws

* *GIVEN* the run of the first scenario
* *WHEN* the owner runs `EXECUTE SCRIPT CFB_INFRA.SIMULATE_FROM(<run id>, '<game id of Ashford against Brookvale>=A', 'Brookvale wins at Ashford')`
* *THEN* the new run MUST be `DONE` with `RUN_ORIGIN` `FORCED`, the run as `BASE_RUN_ID`, and the base run's seed, batch size and number of seasons
* *AND* its `SIM_RUN_GAME` rows MUST equal the base run's, except `FIXED_HOME_WIN` FALSE on that game
* *AND* that game's outcome column MUST be FALSE in every season, and every other outcome column MUST equal the base run's in every season
* *AND* Brookvale's `P_IN_CFP` MUST be higher than in the base run

### Scenario: A run's outputs can be dropped

* *GIVEN* the run of the first scenario
* *WHEN* the owner runs `make sim-drop RUN=<run id> CONFIRM=yes ARGS="--prefix TEST_"`
* *THEN* the run MUST be `DROPPED`, its outcome table MUST be gone, and no output table MUST hold its rows
* *AND* its registry, step, batch and input rows MUST remain
