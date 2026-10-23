# Feature: Weekly predictions, grades and pregame probabilities

Before each game week, the score model's pick, win probability and predicted score for every game are frozen, so they can be graded honestly once the games are played: "the model went 41–9 last week". A prediction is frozen once and never changes, even if the model is recalibrated later. Weeks that were missed are reconstructed at their own as-of point, from what was known then, and marked as reconstructed, so the headline record never presents them as forecasts. Every completed game also gets the pregame probability the upsets page ranks: the betting line where one is stored, else Elo.

## Background

- **Table** `CFB_SIM.PREDICTION` (data, kept across deploys), one row per predicted game: `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `HOME_TEAM_ID`, `AWAY_TEAM_ID`, `IS_NEUTRAL_SITE`, `IS_CONF_CHAMPIONSHIP`, `IS_FBS_VS_FBS`, `FREEZE_SEASON`, `FREEZE_WEEK_SEQ`, `FREEZE_LABEL`, `HOME_RATING`, `AWAY_RATING`, `HOME_RATING_SOURCE`, `AWAY_RATING_SOURCE`, `MU`, `SIGMA`, `TOTAL`, `HOME_POINTS_PRED`, `AWAY_POINTS_PRED`, `HOME_WIN_PROB`, `PICK_TEAM_ID`, `PICK_WIN_PROB`, `FROZEN_AT`, `FROZEN_BEFORE_KICKOFF`, `FREEZE_KIND`, `FROZEN_BY`.
  - The values are the `GAME_SCORE_AS_OF` row of the game at the freeze point (`sim-engine/score-model`).
  - `PICK_TEAM_ID` is the home team when `HOME_WIN_PROB` ≥ 0.5, else the away team; `PICK_WIN_PROB` is the pick's probability.
  - `FROZEN_AT` is UTC (the loader's clock, which tests can set); `FROZEN_BEFORE_KICKOFF` is `FROZEN_AT` < `START_AT`; `FREEZE_KIND` is `IN_ADVANCE` when it is TRUE, else `RECONSTRUCTED`.
  - `FROZEN_BY` is `PREDICT`, `SIMULATE <run id>` or `REFRESH <load run id>`.
- **Freeze** at point P of the current season (the season of `CFB_MODEL.CURRENT_AS_OF`; any other season freezes nothing): for every point Q of the season from the preseason point up to P, oldest first, the games that are `NEXT` at Q in `GAME_SCORE_AS_OF` (regular season, championship games included) and have no `PREDICTION` row are inserted, read with literal Q values. Points whose next week has no unfrozen game are skipped without reading the as-of views. The first freeze of a game wins.
  - It runs as step 2 of every `SIMULATE` (`sim-engine/sim-runs`), as `EXECUTE SCRIPT CFB_INFRA.PREDICT(season, week, season_type)` under the run lock (kind `SIMULATE`), as `make predict SEASON=… WEEK=… [TARGET=…]`, and in the refresh step `PREDICT` (`sim-engine/refresh-step`).
- **Grades** (`CFB_SIM.PREDICTION_GRADED`): one row per prediction, with the game's result from `CFB_MODEL.GAME`: `HOME_POINTS`, `AWAY_POINTS`, `IS_COMPLETED`, `PLAYED_WEEK_SEQ`, `IS_RESCHEDULED` (the game's week changed after the freeze), `GRADE` (`CORRECT` when the pick won, `WRONG` when it lost, `TIE`, NULL before the result), `BRIER` = (`HOME_WIN_PROB` − home won)², `MARGIN_ERROR` = actual margin − `MU`, and the stored line for comparison: `LINE_HOME_WIN_PROB` (`GAME.HOME_PREGAME_WIN_PROB`), `LINE_PICK_CORRECT`, `LINE_BRIER`. The line is a yardstick, never an input.
- **Scorecard** (`CFB_SIM.PREDICTION_SCORECARD`): one row per season, played week sequence (NULL for the season to date), population (`FBS_INVOLVED`, `FBS_VS_FBS`) and freeze kind (`IN_ADVANCE`, `RECONSTRUCTED`, `ALL`), over graded games: `GAMES_GRADED`, `CORRECT`, `WRONG`, `TIES`, `RECORD_LABEL` (for example '41–9'), `PICK_ACCURACY`, `BRIER`, `MAE_MARGIN`, `LINE_GAMES`, `LINE_CORRECT`, `LINE_WRONG`, `LINE_BRIER`, `MODEL_BRIER_ON_LINE_GAMES`, and `IS_HEADLINE`, TRUE only for `IN_ADVANCE`.
- **Calibration** (`CFB_SIM.PREDICTION_CALIBRATION`, the chart of the predictions page): one row per season, population, freeze kind and bucket of `PICK_WIN_PROB` (5 for 0.50–0.60 up to 9 for 0.90–1.00): `GAMES_GRADED`, `MEAN_PICK_WIN_PROB`, `PICK_WIN_SHARE`.
- **Pregame probability** (`CFB_SIM.GAME_WIN_PROB`): one row per completed game with a winner, at least one FBS team and a probability: `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, the teams, `WINNER_TEAM_ID`, `LOSER_TEAM_ID`, `PROB_SOURCE` (`LINE` when the game has a stored `HOME_PREGAME_WIN_PROB`, else `ELO` when both pregame Elo values are stored), `HOME_PREGAME_WIN_PROB` (the one used), `WINNER_PREGAME_WIN_PROB` and `UPSET_MAGNITUDE` = 1 − `WINNER_PREGAME_WIN_PROB`. The Elo probability is 1 / (1 + 10^(−(home Elo − away Elo + `ELO_PROB_HFA` off neutral sites) / `ELO_PROB_SCALE`)). Ranking the upsets is the serving layer's job.
- The examples run in the integration-test namespace on the synthetic league with test constants. The clock is 2026-10-28 12:00 UTC, after week 9 and before week 10's first kickoff (2026-10-29 23:30 UTC); weeks 1–9 of 2026 are completed.

## Scenarios

### Scenario: Next week's games are frozen once, before kickoff

* *GIVEN* an empty `PREDICTION` table
* *WHEN* the presenter runs `EXECUTE SCRIPT CFB_INFRA.PREDICT(2026, 9, 'regular')`, then redeploys with `HFA` 5.00 and runs it again
* *THEN* every week-10 game of `GAME_SCORE_AS_OF` at 2026 week 9 MUST have exactly one `PREDICTION` row, with `FREEZE_KIND` `IN_ADVANCE`, `FREEZE_WEEK_SEQ` 9 and the `MU`, `SIGMA` and `HOME_WIN_PROB` that the view showed before the redeploy
* *AND* the second call MUST NOT add or change any row

### Scenario: Missed weeks are reconstructed at their own point and flagged

* *GIVEN* the predictions of the previous scenario
* *WHEN* `PREDICTION` is read for the 2026 games of weeks 1 to 9
* *THEN* every such game that was `NEXT` at its previous point MUST have one row with `FREEZE_KIND` `RECONSTRUCTED` and `FROZEN_BEFORE_KICKOFF` FALSE
* *AND* its `FREEZE_WEEK_SEQ` MUST be the week before the game, and its `MU` and `HOME_WIN_PROB` MUST equal `GAME_SCORE_AS_OF` at that point

### Scenario: Graded predictions add up to the scorecard

* *GIVEN* the frozen predictions and the results of 2026 weeks 1 to 9
* *WHEN* `PREDICTION_GRADED`, `PREDICTION_SCORECARD` and `PREDICTION_CALIBRATION` are queried
* *THEN* a completed game's `GRADE` MUST be `CORRECT` exactly when its `PICK_TEAM_ID` won, and `WRONG` exactly when the pick lost
* *AND* the season-to-date row for `FBS_INVOLVED` and `ALL` MUST have `CORRECT` + `WRONG` + `TIES` = `GAMES_GRADED`, and `BRIER` equal to the mean `BRIER` of its graded games
* *AND* that row's calibration buckets MUST add up to its `GAMES_GRADED`
* *AND* `IS_HEADLINE` MUST be TRUE only on `IN_ADVANCE` rows

### Scenario: A game's pregame probability comes from its line, else from Elo

* *GIVEN* a completed 2024 game with a stored line, and a completed game before 2013 with both pregame Elo values and no line
* *WHEN* `CFB_SIM.GAME_WIN_PROB` is queried
* *THEN* the 2024 game MUST have `PROB_SOURCE` `LINE` and the stored `HOME_PREGAME_WIN_PROB`
* *AND* the older game MUST have `PROB_SOURCE` `ELO` and the Elo formula's probability within 0.0001
* *AND* both MUST have `UPSET_MAGNITUDE` = 1 − `WINNER_PREGAME_WIN_PROB`
