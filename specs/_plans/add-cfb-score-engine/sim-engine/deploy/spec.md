# Feature: Simulation schema deployment

The score model, the engine, the run scripts and the run tables are installed with one command, on the laptop and on AWS. A redeploy never loses a run or a frozen prediction: they are data, and they stay.

## Background

- **Command:** `make sim [TARGET=local|aws] [ARGS="…"]` (`engine/deploy.py`, `scripts/sim.py deploy`); from slice 3 on, `make deploy` runs it after the model. Under the loader's run lock, kind `DEPLOY`, in one transaction it installs:
  - **reference tables**, replaced from git on every deploy: `SIM_PARAMS` (with the target's `REFRESH_N_SIMS` and `REFRESH_SIMS_PER_BATCH` from `engine/sim-config.yaml`) and `SEASON_RULES`;
  - **the function** `NORMAL_CDF`;
  - **views**: `SCORE_PARAMS`, `TEAM_RATING_AS_OF`, `GAME_SCORE_AS_OF`, `ENGINE_TEAM_AS_OF`, `ENGINE_GAME_AS_OF`, `SIM_RUN_MODEL_ROWS`, `SIM_TEAM_ODDS`, `POINT_RUN`, `CURRENT_RUN`, `ODDS_MOVEMENT`, `ROOTING_GUIDE`, `PREDICTION_GRADED`, `PREDICTION_SCORECARD`, `PREDICTION_CALIBRATION` and `GAME_WIN_PROB`;
  - **data tables**, created when missing and never replaced or dropped: `SIM_RUN`, `SIM_RUN_TEAM`, `SIM_RUN_GAME`, `SIM_RUN_PARAM`, `SIM_STAGE`, `SIM_TEAM`, `SIM_PATH`, `TEAM_WINS`, `CONF_CHAMP`, `LEVERAGE` and `PREDICTION`. A column that differs from its file is reported, not migrated;
  - **scripts in `CFB_INFRA`**: `SIM_ENGINE` (Python 3 `SET`, explicit `EMITS`, its comment naming the `ENGINE_VERSION`), and the Lua scripts `SIMULATE`, `PREDICT`, `REFRESH_PREDICT` and `REFRESH_SIMULATE`, bundled with the loader's `loadlib.lua`.
- **Rules** shared with the model deploy (`model/deploy`): SQL files in `sql/sim/` (data tables in `sql/sim/data/`), one object per file; every object and column commented, the grain first; DDL names unquoted upper case and not reserved; a lint before anything is sent, then every view compiled and every comment read back; any failure rolls the whole transaction back.
- **Layering:** objects of `CFB_SIM` reference only `CFB_SIM` and `CFB_MODEL`; a reference to `CFB_CORE`, `CFB_META` or the virtual schema is a lint error. The Lua scripts also use `CFB_META` (the run lock) and `CFB_INFRA` (the engine).

## Scenarios

### Scenario: One command installs the simulation schema

* *GIVEN* a deployed model in the integration-test namespace and no `CFB_SIM` schema
* *WHEN* `make sim` runs for that namespace
* *THEN* every reference table, function, view and data table of `sql/sim/` MUST exist, with every object and column commented
* *AND* `CFB_INFRA.SIM_ENGINE` and the Lua scripts of `engine/lua/` MUST exist, and the engine's comment MUST name the `ENGINE_VERSION` computed on the host from the bundled body

### Scenario: A redeploy keeps every run and prediction

* *GIVEN* a deployed simulation schema with rows in `SIM_RUN`, `SIM_TEAM`, `SIM_PATH`, `LEVERAGE` and `PREDICTION`
* *WHEN* `make sim` runs again with changed constants in `SIM_PARAMS`
* *THEN* every one of those rows MUST still be there, unchanged
* *AND* `SIM_PARAMS` MUST hold the changed constants
