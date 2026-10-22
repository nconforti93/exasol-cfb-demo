# Feature: Score model calibration

A prediction is only worth showing if it has been checked against history. One command fits the score model, the Elo win probability and the resume weights on the seasons 2014–2025, from what was known at each point. It writes the fitted values to reviewed seed files, and reports how good the predictions are: margin error, Brier score and calibration, next to the betting line as a yardstick. A model that misses its targets is reported, not silently shipped.

## Background

- **Command:** `make calibrate [TARGET=local|aws] [ARGS="…"]`. It costs zero CFBD calls and needs the stored seasons 2013–2025 (2013 is the first SP+ prior). It runs these steps:
  1. **Training rows.** Replace the contents of `CFB_SIM.CALIBRATION_ROWS` with the rows of the view `CFB_SIM.SCORE_TRAINING_ROWS` for the seasons 2014–2025: one row per params version, as-of point P and game g, where:
     - g is `NEXT` or `REMAINING` at P, has teams, involves an FBS team, is a regular-season game, and was completed later;
     - the features are the raw inputs of `GAME_SCORE_AS_OF` at P: both teams' SP+ prior, Elo as of P, classification and previous-season offense and defense ratings, neutral site and `HORIZON_WEEKS`;
     - the labels come from `CFB_MODEL.GAME`: margin, total points and home win;
     - the yardstick is g's stored closing line, its spread and home win probability. A closing line is set at kickoff and knows more than the model does at P, so it is only compared with, never used as a feature;
     - the version's deployed `MU`, `SIGMA`, `TOTAL` and `HOME_WIN_PROB`, refilled in step 4 after the new parameters are deployed.
  2. **Fit the score parameters** on the host, with numpy pinned to the database's version, for `CURRENT` (2014–2025) and `HOLDOUT_2023` (2014–2023):
     - the margin coefficients by least squares on the rows with `HORIZON_WEEKS` 1, one fit per feature set: both features, Elo only, SP+ only;
     - `FBS_DEFAULT_RATING` and `NON_FBS_RATING` as mean residual ratings of teams without features;
     - `SIGMA_1` and `TAU` by weighted least squares on the residual variance per horizon, over all rows;
     - the totals by least squares on the rows with all four offense and defense ratings, and `TOTAL_DEFAULT` as the mean total of the other rows;
     - `ELO_PROB_SCALE` and `ELO_PROB_HFA` by maximum likelihood over completed FBS games with both pregame Elo values (`sim-engine/win-probability`).

     Values are rounded to their column scale. The command writes `sql/sim/010_score_model_params.sql` and deploys it.
  3. **Fit the resume weights** per params version.
     - The inputs are the engine's team inputs (`ENGINE_TEAM_AS_OF`) at the last regular-season point of each season, plus that season's played championship games. The ratings and strength of schedule therefore come from the parameters just deployed, through the same SQL the engine uses.
     - The search is a fixed grid: `w_win_pct` 100; `w_sos` 0–5 by 0.5; `w_pd` 0–2 by 0.25; `w_rating` 0–3 by 0.25. It maximizes the overlap between the resume top 12 and the committee's final top 12, summed over the seasons. Ties go to the higher mean Spearman correlation over the committee's top 25, then to the smallest weights.
     - The command writes `sql/sim/012_resume_weights.sql` and deploys it.
  4. **Refill and document.** Refill the model columns of the training rows. Write `docs/score-model.md`, which holds derived numbers only. Both seed files are generated, with a header that names the target, the seasons and the data fingerprint.
  5. **Report and gate.** Print `CFB_SIM.SCORE_MODEL_REPORT` and the resume overlap per season. Exit with status 3 if an out-of-sample target is missed; the files and the report are kept either way.
- `CFB_SIM.SCORE_MODEL_REPORT`: one row per params version, evaluation season range and horizon bucket (1, 2–3, 4–7, 8+), over the FBS-vs-FBS training rows. It holds:
  - the number of games;
  - margin MAE, RMSE and bias;
  - Brier score and log loss of `HOME_WIN_PROB`;
  - totals MAE, next to the MAE of the training seasons' mean total;
  - the same margin and probability metrics for the yardstick, on the rows that have a line.

  `CFB_SIM.SCORE_CALIBRATION_BUCKETS` has one row per params version, evaluation seasons and tenth of predicted home-win probability, with the number of games, the mean prediction and the observed home-win rate.
- **Out-of-sample targets:** `HOLDOUT_2023` evaluated on 2024–2025, horizon 1, FBS-vs-FBS games:
  - margin MAE at most the yardstick's MAE + 1.5 points, and at most 15.0;
  - Brier score at most the yardstick's + 0.020, and at most 0.190;
  - absolute bias at most 1.0 point;
  - every calibration bucket with at least 100 games within 0.05 of its observed rate;
  - totals MAE below the mean-total baseline.

  `ARGS="--targets name=value,…"` overrides a target, and the report states the override.
- **Leak-free:** features come only from the as-of views, labels only from completed games, and the `HOLDOUT_2023` parameters only from seasons up to 2023.
- **Deterministic:** the same stored data on the same host gives byte-identical seed files.
- The examples run over the sim league in the integration-test namespace. `ARGS="--prefix TEST_ --out <dir>"` writes the files to a temporary directory instead of `sql/sim/`.

## Scenarios

### Scenario: Calibration writes the parameters and the report

* *GIVEN* the sim league with stored seasons 2013–2026
* *WHEN* the owner runs `make calibrate ARGS="--prefix TEST_ --out <tmp>"`
* *THEN* `<tmp>` MUST hold both seed files, each with one `CURRENT` and one `HOLDOUT_2023` row, and `score-model.md`
* *AND* the deployed `CFB_SIM.SCORE_MODEL_PARAMS` MUST equal the written values
* *AND* `CFB_SIM.SCORE_MODEL_REPORT` MUST hold a row for each params version and horizon bucket, with game counts above 0
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: Training rows hold only what was known at their point

* *GIVEN* the training rows of the point 2024 week 9, recorded once
* *AND* every 2024 fact that is not visible at that point is then changed, except the final scores
* *WHEN* `make calibrate` runs again
* *THEN* the features of every training row of that point MUST equal the recorded ones
* *AND* each row's margin label MUST equal the game's final margin in `CFB_MODEL.GAME`

### Scenario: The holdout parameters never see the evaluation seasons

* *GIVEN* a completed calibration
* *WHEN* every 2024 and 2025 margin is changed by +7 points and `make calibrate` runs again
* *THEN* the `HOLDOUT_2023` parameters MUST be unchanged
* *AND* the `CURRENT` parameters MUST change

### Scenario: The report compares the model with the betting line

* *GIVEN* a completed calibration on the sim league
* *WHEN* `CFB_SIM.SCORE_MODEL_REPORT` is queried for `HOLDOUT_2023` on 2024–2025 at horizon 1
* *THEN* it MUST show the model's margin MAE, bias, Brier score and log loss next to the line's MAE and Brier score
* *AND* the line's metrics MUST count only rows that have a stored line
* *AND* `CFB_SIM.SCORE_CALIBRATION_BUCKETS` MUST hold 10 buckets whose game counts add up to the report's game count

### Scenario: A missed target fails the command and keeps the report

* *GIVEN* the sim league
* *WHEN* the owner runs `make calibrate ARGS="--prefix TEST_ --out <tmp> --targets margin_mae_max=1.0"`
* *THEN* the command MUST exit with status 3 and name the margin MAE target and its measured value
* *AND* the seed files and `score-model.md` MUST still be written, with the override recorded in the report

### Scenario: The same data gives the same parameters

* *GIVEN* a completed calibration whose seed files were kept
* *WHEN* `make calibrate` runs again on unchanged data on the same host
* *THEN* both seed files MUST be byte-identical to the kept ones

### Scenario: The resume weights are fitted to the committee's final rankings

* *GIVEN* the sim league, whose committee ranks every final poll by a resume formula over the generator's own ratings
* *WHEN* `make calibrate` runs
* *THEN* both versions of `CFB_SIM.RESUME_WEIGHTS` MUST hold values from the grid
* *AND* the command MUST print the top-12 overlap of every season from 2014 to 2025, at least 10 in each
* *AND* the `HOLDOUT_2023` weights MUST be fitted on the seasons up to 2023 only
