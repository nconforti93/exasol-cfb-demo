# Feature: Published playoff odds and weekly movement

A simulation run becomes what the pages show when it is published. The weekly refresh publishes its own run as its last step, so the odds, their arrows since last week and the what-if toggles all move together. The presenter can publish a manual run too, such as the one-million-season run of the scale dial on AWS. Each publication freezes the run's odds per team, so the arrows survive the removal of old runs.

## Background

- **What can be published:** a run of `CFB_SIM.SIM_RUN` (`sim-engine/sim-runs`) that is `DONE`, has `RUN_ORIGIN` `REFRESH` or `MANUAL`, uses `PARAMS_VERSION` `CURRENT`, and whose point is not after the current point (`CFB_MODEL.CURRENT_AS_OF`). Forced re-sims (`analyses/forced-resim`), runs of other parameter versions (the backtest's `HOLDOUT_2023`), unfinished runs and runs of a later point (possible after a restore) are never published.
- **Step 400 `PUBLISH`:** `EXECUTE SCRIPT CFB_INFRA.REFRESH_PUBLISH(run_id, season, week, season_type)`, registered in `loader/load-plan.yaml` after step 300 `SIMULATE`.
  - It publishes the refresh's own simulation run: the run with `LOAD_RUN_ID` equal to the refresh run, `RUN_ORIGIN` `REFRESH` and `RUN_STATUS` `DONE`.
  - When step 300 created no run (it skips points after the selection week, `sim-engine/refresh-step`), it ends `DONE` with the detail "skipped: no simulation run" and publishes nothing.
  - It runs under the refresh's run lock, reports 0 calls and returns the number of odds rows it wrote.
- **Manual publication:** `EXECUTE SCRIPT CFB_INFRA.PUBLISH_RUN(run_id)` (`make publish RUN=…`). It takes the loader's run lock as run kind `SIMULATE` (script rule) with the run arguments `PUBLISH_RUN <run id>`. A run that cannot be published is refused with a message naming the reason, and nothing is written.
- **One transaction.** A publication writes a `CFB_PRED.PUBLICATION` row and its `CFB_PRED.PUBLISHED_ODDS` rows. Still inside the transaction it reads `CFB_PRED.WHATIF_PUBLICATION` (`analyses/what-if`); when that names the new publication, it regenerates the what-if views. Then it commits once. Any failure rolls all of it back: the step or the call fails naming the run, and the previous publication and views stay.
- `CFB_PRED.PUBLICATION`, a data table: one row per publication: `PUBLICATION_ID`, `RUN_ID`, the run's point (`AS_OF_SEASON`, `AS_OF_WEEK`, `AS_OF_SEASON_TYPE`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`), `RUN_ORIGIN`, `N_SIMS`, `RUN_SECONDS` (the run's `SECONDS_TOTAL`), `PUBLISHED_AT` (UTC, the loader's clock), `PUBLISHED_BY` (`REFRESH_WEEK#<run id>` or `PUBLISH_RUN#<lock run id>`) and `LOAD_RUN_ID`.
- `CFB_PRED.PUBLISHED_ODDS`, a data table: one row per publication and eligible team of the run's roster. It freezes the run's `CFB_SIM.SIM_TEAM_ODDS` values (`P_IN_CFP`, `P_BYE`, `P_QUARTERFINAL`, `P_SEMIFINAL`, `P_FINAL`, `P_CHAMPION`, `P_CONF_CHAMPION`, `EXPECTED_WINS`, `MEAN_SEED`; probabilities stored as `DECIMAL(7,6)`, which holds the view's values exactly), with `SCHOOL`, `CONFERENCE` and `TIER` from the roster (`SIM_RUN_TEAM`) and `ODDS_RANK` (by `P_IN_CFP`, then `P_CHAMPION`, both descending, then team id).
- `CFB_PRED.POINT_PUBLICATION`: one row per as-of point with a publication: its latest publication. Pages read the latest publication of each point; the earlier ones stay as history. `IS_AFTER_CURRENT` is TRUE for a point after `CFB_MODEL.CURRENT_AS_OF`, which happens only after a restore to an earlier week; pages hide those points.
- `CFB_PRED.ODDS_MOVEMENT`: one row per point of `POINT_PUBLICATION` and team of its `PUBLISHED_ODDS`, with the odds, `IS_AFTER_CURRENT`, and the movement since the previous published point of the same season (the nearest earlier point with a publication, its latest publication):
  - `PREV_AS_OF_WEEK_SEQ`, `PREV_AS_OF_LABEL`, `PREV_P_IN_CFP`, `PREV_ODDS_RANK`;
  - `DELTA_P_IN_CFP` (`P_IN_CFP` − `PREV_P_IN_CFP`), `DELTA_P_CHAMPION` and `RANK_CHANGE` (previous rank − rank);
  - `MOVE`: `NEW` without a previous row for the team, `UP` or `DOWN` when |`DELTA_P_IN_CFP`| ≥ `MOVE_THRESHOLD`, else `SAME`.
- **`MOVE_THRESHOLD` is 0.0070** in `CFB_PRED.ANALYSIS_SETTINGS`, so sampling noise does not draw arrows at the demo's run sizes.
  - An arrow compares two independent runs (each week has its own seed), so the noise is the standard error of a difference: √(p(1 − p)(1/n₁ + 1/n₂)), largest at p = 0.5.
  - At 100,000 seasons on both sides that is 0.0022, so 0.0070 is 3.1 standard errors; 1,000,000 against 100,000 gives 4.2, and 1,000,000 on both sides 9.9.
  - At 3.1 standard errors a team at p = 0.5 draws a false arrow about twice in a thousand weeks; teams far from 0.5 have smaller errors still.
  - Runs below 100,000 seasons (the 4,000-season test runs) can draw noise arrows; the settings comment says so.
- Movement reads only frozen publications, so it does not depend on keeping the runs. Phase 4's retention stays as documented in the user guide: weekly refresh runs are kept for the season (their rooting guides stay readable), manual and forced runs are dropped with `make sim-drop`. The run of `CFB_PRED.WHATIF_PUBLICATION` must not be dropped (hand-off to Phase 4: `make sim-drop` refuses it unless forced).
- Publications are this deployment's history, like its run logs: they are not in the snapshot scope (`loader/snapshot-restore`). A restore therefore keeps publications of later points; `IS_AFTER_CURRENT` and the what-if rule handle them (`analyses/what-if`).
- The examples run over the sim league in the integration-test namespace with `SIM_CONFIG` 2,000 seasons in batches of 500, in replay mode. Manual runs have 4,000 seasons in 8 batches of 500.

## Scenarios

### Scenario: The refresh publishes its simulation

* *GIVEN* replay recordings of the sim league's 2026 weekly slices and no publication
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.REFRESH_WEEK(2026, 9, 'regular')`
* *THEN* the timeline MUST list `PUBLISH` after `SIMULATE`, `DONE` with 0 calls
* *AND* `CFB_PRED.PUBLICATION` MUST gain one row for 2026 week 9 naming the refresh's simulation run and the refresh run as `LOAD_RUN_ID`
* *AND* its `CFB_PRED.PUBLISHED_ODDS` rows MUST equal that run's `CFB_SIM.SIM_TEAM_ODDS` rows, team by team
* *AND* `CFB_PRED.WHATIF_PUBLICATION` and `CFB_API.WHATIF_RUN` MUST name that run

### Scenario: The arrows show the movement since the last published week

* *GIVEN* manual runs at 2026 weeks 8 and 9, published in that order
* *WHEN* `CFB_PRED.ODDS_MOVEMENT` is queried for both points
* *THEN* at week 9 every team MUST have `PREV_AS_OF_WEEK_SEQ` 8, and `DELTA_P_IN_CFP` and `RANK_CHANGE` equal to the differences of the two publications' `PUBLISHED_ODDS`
* *AND* `MOVE` MUST be `UP` or `DOWN` exactly where |`DELTA_P_IN_CFP`| is at least 0.0070, and `SAME` elsewhere
* *AND* at week 8 every `MOVE` MUST be `NEW`, and both points MUST have `IS_AFTER_CURRENT` FALSE

### Scenario: The movement survives dropping old runs

* *GIVEN* the two publications of the previous scenario
* *WHEN* the owner runs `make sim-drop RUN=<week-8 run> CONFIRM=yes ARGS="--prefix TEST_"`
* *THEN* `CFB_PRED.ODDS_MOVEMENT` for week 9 MUST be unchanged

### Scenario: A refresh without a simulation publishes nothing

* *GIVEN* the publication of a manual run at 2026 week 9, and replay recordings of the sim league's 2025 slices
* *WHEN* the presenter runs `REFRESH_WEEK(2025, 1, 'postseason')`
* *THEN* `PUBLISH` MUST be `DONE` with the detail "skipped: no simulation run"
* *AND* `CFB_PRED.PUBLICATION` MUST NOT gain a row, and `CFB_API.WHATIF_RUN` MUST still name the week-9 run

### Scenario: A manual run is published on request, and the latest publication of a point wins

* *GIVEN* the refresh's publication of 2026 week 9, and a manual run at 2026 week 9
* *WHEN* the owner runs `make publish RUN=<manual run> ARGS="--prefix TEST_"`
* *THEN* `CFB_PRED.POINT_PUBLICATION` for week 9 MUST name the manual run, and `CFB_PRED.ODDS_MOVEMENT` for week 9 MUST carry its odds
* *AND* the refresh's publication MUST remain in `CFB_PRED.PUBLICATION`

### Scenario: Runs that must not be shown are refused

* *GIVEN* a forced re-sim of a published run, a run that failed in the engine (test hook), and a manual run with params `HOLDOUT_2023`
* *WHEN* the owner runs `CFB_INFRA.PUBLISH_RUN` for each of them
* *THEN* each call MUST fail with a message that names the run and the reason
* *AND* `CFB_PRED.PUBLICATION` MUST NOT gain a row

### Scenario: Publishing an older point leaves the what-if views on the newest point

* *GIVEN* manual runs at 2026 weeks 8 and 9, published in that order
* *WHEN* a new manual run at 2026 week 8 is published
* *THEN* `CFB_PRED.POINT_PUBLICATION` for week 8 MUST name the new run
* *AND* `CFB_PRED.WHATIF_PUBLICATION` and `CFB_API.WHATIF_RUN` MUST still name the week-9 run

### Scenario: A failed publication leaves nothing behind

* *GIVEN* a publication of a manual run at 2026 week 8, and the test hook `TEST_FAIL_PUBLISH` that makes the regeneration of the what-if views fail
* *WHEN* the owner publishes a manual run at 2026 week 9
* *THEN* the call MUST fail with an error that names the run
* *AND* `CFB_PRED.PUBLICATION` and `CFB_PRED.PUBLISHED_ODDS` MUST NOT gain a row, and `CFB_API.WHATIF_RUN` MUST still name the week-8 run
