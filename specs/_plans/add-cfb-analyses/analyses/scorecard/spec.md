# Feature: Model scorecard

After each refresh, last week's frozen predictions are graded against the results: "the model went 41–9". The scorecard also shows the season so far, whether the win probabilities were as reliable as they claimed, and how the betting line did on the same games, so the audience can judge the model honestly. Predictions made before kickoff and predictions reconstructed afterwards are graded apart, so a headline never presents a reconstruction as a forecast. Grading only reads results; it never changes a prediction.

## Background

- `CFB_PRED.PREDICTION_GRADED`: one row per row of `CFB_PRED.PREDICTION` (`analyses/predictions`), with the game's result from `CFB_MODEL.GAME` once it is known.
  - `IS_GRADED` is TRUE when the game is completed with both scores and is not a tie. A tie is never graded.
  - The result: `HOME_POINTS`, `AWAY_POINTS`, `MARGIN` (home minus away), `TOTAL_POINTS`, `WINNER_TEAM_ID`; NULL until graded.
  - The model's errors on graded games: `IS_CORRECT` (the pick is the winner; NULL without a pick), `ABS_MARGIN_ERROR` = |`MU` − `MARGIN`|, `MARGIN_BIAS` = `MU` − `MARGIN`, `ABS_TOTAL_ERROR` = |`TOTAL` − `TOTAL_POINTS`|, `BRIER_TERM` = (`HOME_WIN_PROB` − `HOME_WON`)², with `HOME_WON` 1 for a home win and 0 for an away win.
  - The betting line on the same game: `LINE_HOME_WIN_PROB` = `GAME.HOME_PREGAME_WIN_PROB` (CFBD's pregame probability from the line), `LINE_PICK_TEAM_ID` (same rule as the model's pick), `LINE_IS_CORRECT` and `LINE_BRIER_TERM`; NULL when the game has no line.
  - **The week the game is played:** `GAME_SEASON_TYPE`, `GAME_WEEK` and `GAME_WEEK_SEQ` from `CFB_MODEL.GAME` as stored now, and `IS_RESCHEDULED` when they differ from the frozen `SEASON_TYPE`, `WEEK` and `WEEK_SEQ`. The scorecard groups by the week the game is played, so a postponed game is graded in its new week and keeps its freeze.
  - **`FREEZE_KIND`:** `IN_ADVANCE` when `FROZEN_BEFORE_KICKOFF` is TRUE and `FROZEN_AT` is also before the game's current kickoff (`GAME.START_AT`), else `RECONSTRUCTED`. A kickoff moved to before the freeze therefore turns a row into a reconstruction; the stored row does not change.
- **Populations:** `FBS_INVOLVED` (every prediction; the score model predicts only games that involve an FBS team) and `FBS_VS_FBS` (both teams FBS, `IS_FBS_VS_FBS`).
- **Freeze kinds:** every scorecard view has the dimension `FREEZE_KIND` with the values `IN_ADVANCE`, `RECONSTRUCTED` and `ALL`. A kind's row exists when at least one prediction of that kind exists for the row's other keys; the `ALL` row exists whenever either does, and its counts are the sums of the other two.
- `CFB_PRED.SCORECARD_WEEK`: one row per season, season type and week in which the games are played (`GAME_*`), population and freeze kind.

  | Column | Meaning |
  |---|---|
  | `GAMES_PREDICTED`, `GAMES_GRADED` | Predictions of the week; those graded |
  | `CORRECT`, `WRONG`, `NO_PICK` | Graded games whose pick won, lost, or that had no pick |
  | `RECORD_LABEL` | `CORRECT`, an en dash, `WRONG`, for example `41–9` |
  | `PICK_ACCURACY` | `CORRECT` / (`CORRECT` + `WRONG`), `DECIMAL(5,4)` |
  | `MAE_MARGIN`, `MEAN_BIAS`, `MAE_TOTAL` | Means over graded games, `DECIMAL(5,2)` |
  | `BRIER` | Mean `BRIER_TERM` over graded games (no-pick games included), `DECIMAL(5,4)` |
  | `LINE_GAMES`, `LINE_CORRECT`, `LINE_WRONG`, `LINE_BRIER` | The line on graded games that have one |
  | `MODEL_BRIER_ON_LINE_GAMES` | The model's Brier score on exactly the `LINE_GAMES`, so the two compare like for like |
  | `RESCHEDULED_GAMES` | Predictions of the row that were frozen for another week |
  | `IS_HEADLINE` | TRUE for the `IN_ADVANCE` row of the week and population when it has graded games, otherwise for its `ALL` row |

  Means are NULL when no game is graded.
- `CFB_PRED.SCORECARD_SEASON`: one row per as-of point of a season with predictions, population and freeze kind: the same measures over the predictions of the weeks up to the point (season to date), `IS_HEADLINE` included.
- `CFB_PRED.SCORECARD_CALIBRATION`: one row per season, population, freeze kind and bucket of `PICK_WIN_PROB` over graded games with a pick: [0.5, 0.6), [0.6, 0.7), [0.7, 0.8), [0.8, 0.9) and [0.9, 1.0], with `BUCKET_FROM`, `BUCKET_TO`, `GAMES_GRADED`, `MEAN_PICK_WIN_PROB` and `PICK_WIN_SHARE` (the share of those games the pick won).
- **The headline** ("the model went 41–9") is the `IN_ADVANCE` row when it has graded games. Otherwise it is the `ALL` row, labeled "reconstructed at the as-of point (leak-free, made after kickoff)". `IS_HEADLINE` marks that row, and `CFB_API.SCORECARD_WEEKS` and `SCORECARD_SEASONS` pass it through (`api/serving-contract`).
  - On AWS, the weeks before the first deployment of this phase are reconstructions.
  - A rehearsal that restores "Sunday after week N" on a later real date freezes week N + 1 after its real kickoff, so its headline is the labeled reconstruction unless predictions were frozen live during the season (hand-off to Phase 8).
- CFBD's line probability is the value as last fetched, usually from the closing line, which knew more than the model did at the freeze point. The view comments say so. The comparison is information for the audience, not a quality gate.
- The examples run over the sim league in the integration-test namespace, with the predictions frozen by `REFRESH_WEEK(2026, 9, 'regular')` (`analyses/predictions`) and then the week-10 results stored from the league's truth (harness helper `complete_week`).

## Scenarios

### Scenario: Last week's predictions are graded against the results

* *GIVEN* the frozen week-10 predictions and the stored week-10 results
* *WHEN* `CFB_PRED.SCORECARD_WEEK` is queried for 2026 week 10
* *THEN* for each population, `GAMES_GRADED`, `CORRECT`, `WRONG`, `NO_PICK`, `MAE_MARGIN`, `MEAN_BIAS`, `BRIER` and `MAE_TOTAL` MUST equal the values recomputed by the test from the week's predictions and results
* *AND* `RECORD_LABEL` MUST read `CORRECT`, an en dash and `WRONG`
* *AND* each population MUST have exactly an `IN_ADVANCE` and an `ALL` row, with equal measures

### Scenario: Predictions made in advance and reconstructions are graded apart

* *GIVEN* the frozen week-10 predictions, of which the test marks two `FBS_VS_FBS` games as frozen after kickoff (`FROZEN_BEFORE_KICKOFF` FALSE), and the stored week-10 results
* *WHEN* `CFB_PRED.SCORECARD_WEEK` is queried for 2026 week 10
* *THEN* each population MUST have one row each for `IN_ADVANCE`, `RECONSTRUCTED` and `ALL`, and the `RECONSTRUCTED` rows MUST have `GAMES_PREDICTED` 2
* *AND* `GAMES_PREDICTED`, `GAMES_GRADED`, `CORRECT`, `WRONG`, `NO_PICK` and `LINE_GAMES` of the `ALL` row MUST equal the sums of the other two rows, and its `BRIER` the mean over every graded game of the week
* *AND* `IS_HEADLINE` MUST be TRUE for the `IN_ADVANCE` row of each population, and for no other row

### Scenario: A rescheduled game is graded in the week it is played

* *GIVEN* the frozen week-10 predictions, then the test moves one week-10 game to week 11 with a kickoff one week later, and the kickoff of a second week-10 game to one hour before its `FROZEN_AT`, in the stored games
* *AND* the results of weeks 10 and 11 are stored
* *WHEN* `CFB_PRED.SCORECARD_WEEK` and `CFB_PRED.PREDICTION_GRADED` are queried for 2026 weeks 10 and 11
* *THEN* the moved game MUST count in the week-11 rows and not in the week-10 rows, with `IS_RESCHEDULED` TRUE and `RESCHEDULED_GAMES` 1 in week 11
* *AND* its `CFB_PRED.PREDICTION` row MUST be unchanged, `FREEZE_WEEK_SEQ` 9 included, and its `FREEZE_KIND` MUST be `IN_ADVANCE`
* *AND* the second game MUST have `FREEZE_KIND` `RECONSTRUCTED` while its stored `FROZEN_BEFORE_KICKOFF` stays TRUE

### Scenario: A game without a result is not graded

* *GIVEN* the frozen week-10 predictions and no week-10 result stored
* *WHEN* `CFB_PRED.PREDICTION_GRADED` and `CFB_PRED.SCORECARD_WEEK` are queried for 2026 week 10
* *THEN* every week-10 row of `PREDICTION_GRADED` MUST have `IS_GRADED` FALSE and NULL results and errors
* *AND* the week's scorecard rows MUST have `GAMES_GRADED` 0, `CORRECT` 0, `WRONG` 0 and NULL means

### Scenario: Ties and games without a pick are neither correct nor wrong

* *GIVEN* the frozen week-10 predictions, one of which is set by the test to `HOME_WIN_PROB` 0.5000 with no pick
* *AND* the week-10 results are stored with one other game ending 21–21
* *WHEN* `CFB_PRED.SCORECARD_WEEK` is queried for 2026 week 10
* *THEN* the tied game MUST NOT be graded
* *AND* the game without a pick MUST count in `NO_PICK` and in `BRIER`, and MUST NOT count in `CORRECT` or `WRONG`

### Scenario: The season so far adds up the weeks

* *GIVEN* the predictions of 2026 weeks 1 to 10 and the results through week 10
* *WHEN* `CFB_PRED.SCORECARD_SEASON` is queried at the points 2026 week 9 and week 10
* *THEN* at week 10, `CORRECT` and `WRONG` of each freeze kind MUST equal the sums over the `SCORECARD_WEEK` rows of that kind for weeks 1 to 10, and `BRIER` the mean over every graded game of that kind in those weeks
* *AND* at week 9 the measures MUST leave out week 10
* *AND* at week 9 each population MUST have only `RECONSTRUCTED` and `ALL` rows, with `IS_HEADLINE` TRUE for the `ALL` row

### Scenario: Calibration buckets compare the claimed and the actual win shares

* *GIVEN* the predictions of 2026 weeks 1 to 10 and the results through week 10
* *WHEN* `CFB_PRED.SCORECARD_CALIBRATION` is queried for 2026
* *THEN* per population and freeze kind, the buckets' `GAMES_GRADED` MUST add up to the graded games of that kind with a pick
* *AND* each bucket's `MEAN_PICK_WIN_PROB` and `PICK_WIN_SHARE` MUST equal the values recomputed by the test
* *AND* a `PICK_WIN_PROB` of exactly 0.9000 MUST fall into the bucket from 0.9 to 1.0

### Scenario: The line is graded on the same games as the model

* *GIVEN* the frozen week-10 predictions and the stored week-10 results, with lines on the league's FBS-vs-FBS games only
* *WHEN* `CFB_PRED.SCORECARD_WEEK` is queried for 2026 week 10
* *THEN* `LINE_GAMES` MUST count the graded games with a line
* *AND* `LINE_CORRECT`, `LINE_WRONG`, `LINE_BRIER` and `MODEL_BRIER_ON_LINE_GAMES` MUST equal the values recomputed by the test over exactly those games
