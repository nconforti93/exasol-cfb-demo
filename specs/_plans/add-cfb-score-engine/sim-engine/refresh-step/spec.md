# Feature: Simulation step of the weekly refresh

After a game week, one call refreshes everything: fetch, merge, model, predictions, simulations. The simulation step reruns the season's playoff odds from the new point, so the odds pages move with the refresh, and the demo's timeline shows the seasons simulated and the seconds they took.

## Background

- **Step script:** `EXECUTE SCRIPT CFB_INFRA.REFRESH_SIMULATE(run_id, season, week, season_type)`, the contract of registered refresh steps (`loader/refresh-week`).
  - `loader/load-plan.yaml` registers it as step 300 `SIMULATE`. Step 100 is `MODEL` (`model/refresh-step`); step 200 is left for Phase 5's predictions.
  - It simulates the point `(season, week, season_type)` with the defaults of `CFB_SIM.SIM_CONFIG` (`N_SIMS`, `SIMS_PER_BATCH`) and the seed rule of `sim-engine/sim-runs` (season × 1,000 + week sequence), with params `CURRENT`.
  - It runs under the refresh's run lock and takes no lock of its own. Its registry row has `RUN_ORIGIN` `REFRESH` and the refresh's run id as `LOAD_RUN_ID`.
  - It first cleans up abandoned simulation runs, like a run that takes the lock (`sim-engine/sim-runs`). A refresh killed during its simulation leaves its run `RUNNING`; the next refresh's step cleans it up.
  - Its steps, checks, outputs and failure handling are those of `sim-engine/sim-runs`. It returns the number of rows the simulation wrote.
- **Skip:** a point after the season's selection week (`CFB_SIM.SELECTION_WEEK`) has no simulation, because the playoff field is set then. That includes a regular-season week after the championship games, such as the Army–Navy week. The step ends `DONE` with the detail "skipped: playoff field set", and creates no registry row.
- A failed simulation fails the step, which stops the refresh (`loader/refresh-week`). The slices fetched before it and the model's check log stay as they are.
- It never reads the virtual schema: the step reports 0 calls.
- The examples run in the integration-test namespace in replay mode. The load plan registers `MODEL` as step 100 and `SIMULATE` as step 300. `SIM_CONFIG` sets `N_SIMS` 2,000 and `SIMS_PER_BATCH` 500. The replay recordings of the sim league's 2026 weekly slices hold the results of week 9.

## Scenarios

### Scenario: The refresh simulates the season after the model step

* *GIVEN* replay recordings of the sim league's 2026 weekly slices
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the timeline MUST list `CHECK`, the `FETCH` steps, `MODEL` and `SIMULATE`, in that order, each `DONE`
* *AND* `SIMULATE` MUST report 0 calls
* *AND* `CFB_SIM.SIM_RUN` MUST gain one `DONE` run for 2026 week 9 with `RUN_ORIGIN` `REFRESH`, the refresh's run id as `LOAD_RUN_ID`, 2,000 seasons in batches of 500 and `RUN_SEED` 2026009

### Scenario: The step follows the deployment's configuration

* *GIVEN* `SIM_CONFIG` changed by a deploy override to `N_SIMS` 4,000 and `SIMS_PER_BATCH` 1,000
* *WHEN* the presenter runs `REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the new run MUST have 4,000 seasons in 4 batches

### Scenario: A refresh after the selection week skips the simulation

* *GIVEN* replay recordings of the sim league variant whose 2024 season has a regular-season game in week 16, after the championship games of week 15, and of the sim league's 2025 slices
* *WHEN* the presenter runs `REFRESH_WEEK(2024, 16, 'regular')`, and then `REFRESH_WEEK(2025, 1, 'postseason')`
* *THEN* `SIMULATE` MUST be `DONE` with the detail "skipped: playoff field set" in both refreshes
* *AND* `CFB_SIM.SIM_RUN` MUST NOT gain a row

### Scenario: A failed simulation stops the refresh and keeps the fetched data

* *GIVEN* the test hook that makes the engine fail in batch 2
* *WHEN* the presenter runs `REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the call MUST fail with an error that names the step `SIMULATE` and the refresh run
* *AND* the 2026 slices fetched by the `FETCH` steps MUST stay stored, and `CFB_META.MODEL_CHECK_LOG` MUST keep the run's rows
* *AND* the simulation run MUST be `FAILED` with no outputs
