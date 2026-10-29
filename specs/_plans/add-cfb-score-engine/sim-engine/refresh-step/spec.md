# Feature: Prediction and simulation steps of the weekly refresh

After a game week, one call refreshes everything: fetch, model, predictions, simulations. The refresh freezes next week's predictions and reruns the season's playoff odds from the new point, so the pages move with the refresh, and the demo's timeline shows the seasons simulated and the seconds they took.

## Background

- **Steps** (`loader/refresh-week`: registered steps run after the fetch, by step number, each as `EXECUTE SCRIPT <script>(run_id, season, week, season_type)`):
  - step 200 `PREDICT`, script `CFB_INFRA.REFRESH_PREDICT`: the freeze of `sim-engine/predictions` at the refreshed point, with `FROZEN_BY` `REFRESH <run id>`;
  - step 300 `SIMULATE`, script `CFB_INFRA.REFRESH_SIMULATE`: a run of `sim-engine/sim-runs` at the refreshed point with `RUN_ORIGIN` `REFRESH`, the refresh's run id as `LOAD_RUN_ID`, the seed rule season × 1,000 + week sequence, and `N_SIMS` and `SIMS_PER_BATCH` from `SIM_PARAMS` (`REFRESH_N_SIMS`, `REFRESH_SIMS_PER_BATCH`), which `make sim` sets per target from `engine/sim-config.yaml`: 100,000 and 5,000 on the laptop, 1,000,000 and 10,000 on AWS.
  - `loader/load-plan.yaml` registers both; step 100 `MODEL` belongs to the model (`model/refresh-step`).
  - Both run under the refresh's run lock and take no lock of their own.
- **Cleanup of killed runs:** a run killed mid-way (for example by `KILL STATEMENT`) cannot log its own end, and stays `RUNNING`. Whoever next holds the run lock cleans it up: right after `SIMULATE` takes the lock, and at the start of `REFRESH_SIMULATE`, every other `RUNNING` row of `SIM_RUN` becomes `ABANDONED`, its output rows are deleted and the stage is truncated. The registry row and the input snapshot stay.
- **Skip:** a point whose `CUTOFF_AT` is NULL or not before the season's `SELECTION_AT`, or a season without a `SEASON_RULES` row, has no simulation: `SIMULATE` ends `DONE` with the detail "skipped: no simulation for this point" and no registry row. `PREDICT` freezes nothing outside the current season.
- Both steps report 0 CFBD calls. A failure fails the step, which stops the refresh; the fetched slices stay stored.
- The examples run in the integration-test namespace in replay mode, with `REFRESH_N_SIMS` 2,000 and `REFRESH_SIMS_PER_BATCH` 500, on the replay recordings of the synthetic league's 2026 weekly slices.

## Scenarios

### Scenario: The weekly refresh freezes predictions and simulates the season

* *GIVEN* replay recordings of the synthetic league's 2026 weekly slices and the clock at 2026-10-28 12:00 UTC
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the timeline MUST list `CHECK`, the `FETCH` steps, `MODEL` when the model registers it, `PREDICT` and `SIMULATE`, in that order, each `DONE` with 0 calls
* *AND* `CFB_SIM.SIM_RUN` MUST gain one `DONE` run for 2026 week 9 with `RUN_ORIGIN` `REFRESH`, the refresh's run id as `LOAD_RUN_ID`, 2,000 seasons in batches of 500 and `RUN_SEED` 2026009
* *AND* the week-10 games MUST be frozen in `PREDICTION` with `FROZEN_BY` naming the refresh

### Scenario: A killed run is cleaned up by the next run

* *GIVEN* a `RUNNING` row in `SIM_RUN` whose session no longer exists, with rows in `SIM_TEAM`, `SIM_PATH` and `LEVERAGE` and in `SIM_STAGE`
* *WHEN* the presenter runs `SIMULATE(2026, 9, 'regular', 2000, 500, NULL, NULL)`
* *THEN* the old run MUST be `ABANDONED`, with no rows left in its outputs
* *AND* the new run MUST be `DONE`
