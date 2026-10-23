# Feature: Score model calibration

A prediction is only worth showing if it has been checked against history. One command fits the score model, the Elo win probability and the resume weights on the seasons 2014–2025, from what was known at each point. It writes the fitted values to reviewed seed files, and reports how good the predictions are: margin error, Brier score and calibration, next to the betting line as a yardstick, early, mid and late in the season. A model that misses its targets is reported, not silently shipped, and a target is never so tight that a good model misses it by chance.

## Background

- **Command:** `make calibrate [TARGET=local|aws] [ARGS="…"]`. It costs zero CFBD calls and needs the stored seasons 2013–2025 (2013 is the first SP+ prior). It holds the loader's run lock as kind `DEPLOY` for its whole run (`sim-engine/sim-runs`). It runs these steps:
  1. **Training rows.** Replace the contents of `CFB_SIM.CALIBRATION_ROWS` with the rows of the view `CFB_SIM.SCORE_TRAINING_ROWS` for the seasons 2014–2025: one row per params version, as-of point P and game g, where:
     - g is `NEXT` or `REMAINING` at P, has teams, involves an FBS team, is a regular-season game, and was completed later;
     - the features are the raw inputs of `GAME_SCORE_AS_OF` at P: both teams' SP+ prior, Elo as of P, classification and previous-season offense and defense ratings, neutral site, `HORIZON_WEEKS` and P's `WEEK_PHASE`;
     - the labels come from `CFB_MODEL.GAME`: margin, total points and home win;
     - the yardstick is g's stored closing line, its spread and home win probability. A closing line is set at kickoff and knows more than the model does at P, so it is only compared with, never used as a feature;
     - the version's deployed `MU`, `SIGMA`, `TOTAL`, `HOME_WIN_PROB` and both teams' `RATING_SOURCE`, refilled in step 4 after the new parameters are deployed.
  2. **Fit the score parameters** on the host, with numpy pinned to the database's version, for `CURRENT` (2014–2025) and `HOLDOUT_2023` (2014–2023):
     - `ELO_CENTER` first: the mean `ELO_AS_OF` of the FBS teams with an Elo over the version's training points, rounded to a whole number. The SP+ prior is centered on the average FBS team, so this centers the Elo term the same way, and a team rated by one feature compares fairly with a team rated by both;
     - the margin coefficients by least squares on the rows with `HORIZON_WEEKS` 1, one fit per feature set: both features, Elo only, SP+ only. `ARGS="--feature-set SP_ELO"` (the default) fits `B_SP` and `B_ELO` once over all weeks; `--feature-set SP_ELO_PHASED` fits them per week phase; `--feature-set F2` adds the season-to-date point differential per game as a third feature to the phased fit;
     - `FBS_DEFAULT_RATING` and `NON_FBS_RATING` as mean residual ratings of teams without features;
     - `SIGMA_1` and `TAU` by weighted least squares on the residual variance per horizon, over all rows;
     - the totals by least squares on the rows with all four offense and defense ratings, and `TOTAL_DEFAULT` as the mean total of the other rows;
     - `ELO_PROB_SCALE` and `ELO_PROB_HFA` by maximum likelihood over completed FBS games with both pregame Elo values (`sim-engine/win-probability`).

     Values are rounded to their column scale. The command writes `sql/sim/010_score_model_params.sql` (two versions × three phases) and deploys it.
  3. **Fit the resume weights** per params version.
     - The inputs are the engine's team inputs (`ENGINE_TEAM_AS_OF`) at each season's selection point (`CFB_SIM.SELECTION_WEEK`, `sim-engine/sim-runs`), plus that season's played championship games. The ratings and strength of schedule therefore come from the parameters just deployed, through the same SQL the engine uses. Tiers do not enter the resume; for seasons before 2024 the team rows carry the era's tier mapped to the engine's codes (`AQ` and `P5` → `P4`, `NON_AQ` and `G5` → `G6`).
     - The target is the committee's final ranking of each season: `CFB_MODEL.RANKING` rows with `POLL_CODE` `CFP` (from the reference table `CFB_MODEL.POLL`) and `IS_FINAL_POLL` TRUE, ranks 1–25. A final ranking is a label, like a final score, so it is read from `RANKING` and not from `RANKING_AS_OF`.
     - The search is a fixed grid: `w_win_pct` 100; `w_sos` 0–5 by 0.5; `w_pd` 0–2 by 0.25; `w_rating` 0–3 by 0.25. It maximizes the overlap between the resume top 12 and the committee's final top 12, summed over the seasons. Ties go to the higher mean Spearman correlation over the committee's top 25, then to the smallest weights.
     - The command writes `sql/sim/012_resume_weights.sql` and deploys it.
     - `ARGS="--resume-only"` skips steps 1, 2 and 4 and fits only the resume weights over the deployed score parameters.
  4. **Refill and document.** Refill the model columns of the training rows. Write `docs/score-model.md`, which holds derived numbers only. Both seed files are generated, with a header that names the target, the seasons, the feature set and the data fingerprint.
  5. **Report and gate.** Print `CFB_SIM.SCORE_MODEL_REPORT`, `CFB_SIM.SCORE_SOURCE_REPORT` and the resume overlap per season. Exit with status 3 if an out-of-sample target is missed, naming the target, the week phase and the measured value. The files and the report are kept either way.
- `CFB_SIM.SCORE_MODEL_REPORT`: one row per params version, evaluation season range, horizon bucket (1, 2–3, 4–7, 8+) and week phase (`EARLY`, `MID`, `LATE`, and `ALL` for the three together), over the FBS-vs-FBS training rows. It holds:
  - the number of games;
  - margin MAE, RMSE, bias and the bias's standard error;
  - Brier score, log loss and the calibration statistic Z of `HOME_WIN_PROB`: Σ(y − p)(1 − 2p) / √Σ(1 − 2p)² p(1 − p) over the games, with y the home win and p the prediction (Spiegelhalter's test; about N(0, 1) for a calibrated model);
  - totals MAE, next to the MAE of the training seasons' mean total;
  - the same margin and probability metrics for the yardstick, on the rows that have a line.
- `CFB_SIM.SCORE_CALIBRATION_BUCKETS`: one row per params version, evaluation seasons, horizon bucket, week phase (with `ALL`) and tenth of predicted home-win probability, with the number of games, the mean prediction p̄, the observed home-win rate and the tolerance max(0.05, 3 × √(p̄(1 − p̄)/n)).
- `CFB_SIM.SCORE_SOURCE_REPORT`: one row per params version, evaluation seasons and pair of rating sources (home, away), over every horizon-1 training row, FCS opponents included, with the number of games, the margin bias and the MAE. It shows whether teams rated from different sources compare fairly. It is reported, not gated.
- **Out-of-sample targets:** `HOLDOUT_2023` evaluated on 2024–2025, horizon 1, FBS-vs-FBS games.
  - In each week phase (`EARLY`, `MID` and `LATE`):
    - margin MAE at most the yardstick's MAE + 1.5 points, and at most 15.0;
    - Brier score at most the yardstick's + 0.020, and at most 0.190;
    - absolute bias at most max(1.0, 3 × its standard error);
    - absolute calibration Z at most 3.0.
  - Over the three phases together:
    - every tenth with at least 100 games within its tolerance;
    - totals MAE below the mean-total baseline.

  Every statistical tolerance is at least three standard errors wide, so a well-calibrated model passes the whole gate in about 96 % of samples of this size. `ARGS="--targets name=value,…"` overrides a target, and the report states the override.
- **When a target is missed** (task 6.2 of the plan): the default `SP_ELO` fit is followed by `SP_ELO_PHASED`, and only then by `F2`. Each miss and each fallback is logged as a decision with both reports.
- **Leak-free:** features come only from the as-of views, labels only from completed games and final rankings, and the `HOLDOUT_2023` parameters only from seasons up to 2023.
- **Deterministic:** the same stored data on the same host gives byte-identical seed files.
- The examples run over the sim league in the integration-test namespace. `ARGS="--prefix TEST_ --out <dir>"` writes the files to a temporary directory instead of `sql/sim/`.

## Scenarios

### Scenario: Calibration writes the parameters and the report

* *GIVEN* the sim league with stored seasons 2013–2026
* *WHEN* the owner runs `make calibrate ARGS="--prefix TEST_ --out <tmp>"`
* *THEN* `<tmp>` MUST hold both seed files and `score-model.md`; the parameters file MUST hold the three phase rows of `CURRENT` and of `HOLDOUT_2023` with `FEATURE_SET` `SP_ELO` and equal values across the phases, and the resume file one row per version
* *AND* each version's `ELO_CENTER` MUST equal the rounded mean Elo of the FBS teams over its training points
* *AND* the deployed `CFB_SIM.SCORE_MODEL_PARAMS` MUST equal the written values, and `CFB_SIM.SCORE_MODEL_REPORT` MUST hold a row for each params version, horizon bucket and week phase, with game counts above 0
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: Per-phase coefficients are fitted on request

* *GIVEN* the sim league
* *WHEN* the owner runs `make calibrate ARGS="--prefix TEST_ --out <tmp> --feature-set SP_ELO_PHASED"`
* *THEN* each version MUST hold `FEATURE_SET` `SP_ELO_PHASED`, with `B_SP` and `B_ELO` fitted on the training rows of each phase alone
* *AND* every other column MUST be equal across the three phase rows of a version

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

### Scenario: The report compares the model with the betting line in every phase of the season

* *GIVEN* a completed calibration on the sim league
* *WHEN* `CFB_SIM.SCORE_MODEL_REPORT` is queried for `HOLDOUT_2023` on 2024–2025 at horizon 1
* *THEN* it MUST hold one row for each of `EARLY`, `MID`, `LATE` and `ALL`, the game counts of the three phases adding up to that of `ALL`, each showing the model's margin MAE, bias and its standard error, Brier score, log loss and calibration Z next to the line's MAE and Brier score, which count only the rows that have a stored line
* *AND* `CFB_SIM.SCORE_CALIBRATION_BUCKETS` for `ALL` MUST hold 10 buckets whose game counts add up to the report's game count, each with its tolerance
* *AND* `CFB_SIM.SCORE_SOURCE_REPORT` MUST hold a row for each pair of rating sources that occurs, including `SP_ELO` hosts against `CLASS_DEFAULT` opponents such as Lowell

### Scenario: A well-calibrated model passes the gate, and an overconfident one does not

* *GIVEN* 1,000 seeded synthetic gate populations of a model whose probabilities and margins are exactly right, each with 300, 450 and 550 horizon-1 games in the early, mid and late phases
* *AND* 1,000 populations of the same games predicted with an uncertainty 20 % too small
* *WHEN* the statistical targets (bias, calibration Z and the buckets) are evaluated on each
* *THEN* at least 94 % of the well-calibrated populations MUST pass
* *AND* at least 50 % of the overconfident populations MUST fail

### Scenario: A missed target fails the command and keeps the report

* *GIVEN* the sim league
* *WHEN* the owner runs `make calibrate ARGS="--prefix TEST_ --out <tmp> --targets margin_mae_max=1.0"`
* *THEN* the command MUST exit with status 3 and name the margin MAE target, each week phase that misses it and the measured values
* *AND* the seed files and `score-model.md` MUST still be written, with the override recorded in the report

### Scenario: The same data gives the same parameters

* *GIVEN* a completed calibration whose seed files were kept
* *WHEN* `make calibrate` runs again on unchanged data on the same host
* *THEN* both seed files MUST be byte-identical to the kept ones

### Scenario: The resume weights are fitted to the committee's final rankings

* *GIVEN* the sim league with the test parameters deployed, whose committee ranks every final ranking by the resume formula with the test resume weights over the test parameters' ratings
* *WHEN* the owner runs `make calibrate ARGS="--prefix TEST_ --out <tmp> --resume-only"`
* *THEN* both versions of `CFB_SIM.RESUME_WEIGHTS` MUST hold values from the grid, and the score parameters MUST be unchanged
* *AND* the command MUST print, per version, the top-12 overlap of every fitted season, 12 in each, and a mean Spearman correlation of 1.0
* *AND* the `HOLDOUT_2023` fit MUST list the seasons 2014–2023 only
