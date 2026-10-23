# Feature: Simulation schema deployment

The score model, the engine and the run tables are installed with one command, together with everything else, on the laptop and on AWS. A redeploy never loses a run: simulations are data, and they stay. What the schema reads and what it is configured with are checked before anything is sent.

## Background

- **Command:** `make sim [TARGET=local|aws] [ARGS="…"]`, also run by `make deploy` after the model (`model/deploy`). It renders and deploys:
  - **reference tables**, replaced with their seed rows on every deploy: `SCORE_MODEL_PARAMS`, `RESUME_WEIGHTS`, `SELECTION_RULES` and `SIM_CONFIG`;
  - **functions** `NORMAL_CDF` and `MARGIN_WIN_PROB`;
  - **views**: `SELECTION_WEEK`, `TEAM_RATING_AS_OF`, `GAME_SCORE_AS_OF`, `GAME_WIN_PROB`, `SCORE_TRAINING_ROWS`, `SCORE_MODEL_REPORT`, `SCORE_CALIBRATION_BUCKETS`, `SCORE_SOURCE_REPORT`, `ENGINE_TEAM_AS_OF`, `ENGINE_GAME_AS_OF`, `ENGINE_PARAM_AS_OF`, `SIM_INPUT_CHECKS`, `SIM_RUN_MODEL_ROWS`, `SIM_TEAM_ODDS`, `BACKTEST_FIELD`, `BACKTEST_REPORT` and `BACKTEST_CALIBRATION`;
  - **data tables**, created when missing and never replaced: `SIM_RUN`, `SIM_RUN_STEP`, `SIM_RUN_BATCH`, `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM`, `SIM_STAGE`, `SIM_TEAM`, `LEVERAGE`, `TEAM_WINS`, `CONF_CHAMP` and `CALIBRATION_ROWS`;
  - **scripts in `CFB_INFRA`**: the engine `SIM_ENGINE` (Python 3 `SET`, explicit `EMITS`, with its `ENGINE_VERSION` embedded), and `SIMULATE`, `SIMULATE_FROM`, `REFRESH_SIMULATE` and `DROP_SIM_RUN` (Lua).
  - It also adds the `SIMULATE` step to `REFRESH_STEPS`, through the load plan.
- **Rules**, shared with the model deploy (`model/deploy`, `model/documentation`):
  - one transaction under the loader's run lock, taken as run kind `DEPLOY` (stale only when its session ends, `sim-engine/sim-runs`);
  - SQL files in `sql/sim/`, one object per file;
  - every object and column commented, the grain first;
  - DDL names unquoted upper case and not reserved;
  - a lint before anything is sent, then every view compiled and every comment read back.
- **Layering:** objects of `CFB_SIM` may reference only `CFB_SIM` and `CFB_MODEL`. A reference to `CFB_CORE`, `CFB_META` or the virtual schema is a lint error. The Lua scripts may also use `CFB_META` for the lock and `CFB_INFRA` for the engine.
- **Data is kept:**
  - Objects that no file defines are dropped, except the data tables and the run outcome tables `SIM_PATH_R<run id>`.
  - A data table whose columns differ from its file stops the deploy, with a message that names the table and the difference. Nothing is migrated silently.
- **Configuration:** `CFB_SIM.SIM_CONFIG` holds `N_SIMS` and `SIMS_PER_BATCH` from the target's block of `engine/sim-config.yaml`:
  - `local` 100,000 and 5,000: 20 batches, 2.5 per slot on an 8-core start, so every core stays busy to the end (D19 measured 5,000 and 10,000 as equal);
  - `aws` 1,000,000 and 10,000: the scale dial, 100 batches over 12 slots.

  `ARGS="--config-block aws"` picks another block (for tests); `ARGS="--config NAME=value,…"` overrides single values for one deployment, validated like the file: positive integers, `N_SIMS` a multiple of `SIMS_PER_BATCH`, and `SIMS_PER_BATCH` from 100 to 20,000. The integration-test namespace may also set the test hooks `TEST_FAIL_BATCH`, `TEST_SLEEP_BEFORE_STEP` and `TEST_SLEEP_SECONDS`; a deploy elsewhere removes them.
- **Script size:** the engine body must stay below 1,990,000 characters, the foundation's bundle limit.
- The deploy costs zero CFBD calls. The examples run in the integration-test namespace.

## Scenarios

### Scenario: One command installs the simulation schema

* *GIVEN* a deployed model and no simulation schema
* *WHEN* the owner runs `make sim ARGS="--prefix TEST_"`
* *THEN* the report MUST list 4 reference tables, 2 functions, 17 views, 12 data tables and 5 scripts, with 100 % comments
* *AND* `CFB_META.REFRESH_STEPS` MUST hold step 300 `SIMULATE`
* *AND* the engine script MUST carry the `ENGINE_VERSION` that the bundler computed

### Scenario: A redeploy keeps every run

* *GIVEN* a deployed simulation schema with a `DONE` run and its outcome table
* *WHEN* the owner runs `make sim ARGS="--prefix TEST_"` again
* *THEN* the run's registry row, its `SIM_TEAM` rows and its outcome table MUST be unchanged
* *AND* the report MUST drop nothing

### Scenario: The full deploy includes the simulation schema

* *GIVEN* an empty integration-test namespace
* *WHEN* the harness runs the full deploy
* *THEN* the virtual schema, the loader, the model and the simulation schema MUST be deployed, in that order

### Scenario: The simulation schema reads only the model

* *GIVEN* a file in `sql/sim/` whose view selects from `CFB_CORE.GAMES`
* *WHEN* the owner runs `make sim`
* *THEN* the lint MUST name the file, the view and the reference, and nothing MUST be sent to the database
* *AND* for the deployed schema, `EXA_ALL_DEPENDENCIES` MUST show references only to `CFB_SIM` and `CFB_MODEL`

### Scenario: A changed data table stops the deploy

* *GIVEN* a deployed simulation schema, and a file that adds a column to `SIM_TEAM`
* *WHEN* the owner runs `make sim`
* *THEN* the deploy MUST fail with a message that names `SIM_TEAM` and the added column
* *AND* the deployed schema MUST be unchanged

### Scenario: A deploy is refused while a run holds the lock

* *GIVEN* a simulation run of another session that is `RUNNING`
* *WHEN* the owner runs `make sim`
* *THEN* the deploy MUST fail with a message that names the running run
* *AND* no object MUST change

### Scenario: The configuration follows the target and can be overridden

* *GIVEN* a deployed simulation schema
* *WHEN* the owner deploys it with `ARGS="--prefix TEST_ --config-block aws"`, then with `ARGS="--prefix TEST_ --config N_SIMS=4000,SIMS_PER_BATCH=500"`, then with `ARGS="--prefix TEST_ --config N_SIMS=1234"`
* *THEN* after the first deploy, `SIM_CONFIG` MUST hold `N_SIMS` 1,000,000 and `SIMS_PER_BATCH` 10,000
* *AND* after the second, it MUST hold 4,000 and 500
* *AND* the third MUST fail, because 1,234 is not a multiple of the `local` block's 5,000, and leave the configuration of the second unchanged
