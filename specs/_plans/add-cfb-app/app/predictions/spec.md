# Feature: Predictions and scorecard page

The scorecard part of beat 4: "the model went 41–9". The page grades the predictions that were frozen before last week's games, puts the betting line next to them on the same games, and shows this week's predictions. A headline that rests on reconstructed predictions says so in words.

## Background

- **The week's scorecard** at point P reads `CFB_API.SCORECARD_WEEKS` for P's season, season type and week, and the chosen population (default `FBS_INVOLVED`; the switch offers `FBS_VS_FBS`).
  - **The headline** is the row with `IS_HEADLINE` TRUE. It shows `RECORD_LABEL` (for example "41–9"), `PICK_ACCURACY`, `BRIER` and `MAE_MARGIN`.
  - When the headline's `FREEZE_KIND` is `ALL`, the headline carries the label "reconstructed at the as-of point (leak-free, made after kickoff)" (`analyses/scorecard`). An `IN_ADVANCE` headline carries no such label.
  - **The line** stands beside the headline on the same games: `LINE_CORRECT`–`LINE_WRONG`, and `LINE_BRIER` against `MODEL_BRIER_ON_LINE_GAMES`. It is labeled as closing-line information, which knew more than the model did when it froze its predictions.
  - The `IN_ADVANCE` and `RECONSTRUCTED` rows are shown as detail.
- **The season to date** reads `CFB_API.SCORECARD_SEASONS` at P, for the same population, with the same headline rule.
- **Calibration** reads `CFB_API.SCORECARD_CALIBRATION` for P's season, the population, and the headline's freeze kind. It is drawn as one dot per bucket, `MEAN_PICK_WIN_PROB` against `PICK_WIN_SHARE`, on a diagonal reference line. A table view shows the same numbers with `GAMES_GRADED`.
- **Last week, graded.** These are the rows of `CFB_API.PREDICTIONS` whose game was played in P's week (`GAME_SEASON_TYPE`, `GAME_WEEK_SEQ`). Each shows the result, the pick, and whether the pick was correct or wrong. A tie is not graded, and a game without a pick shows "no pick".
- **This week's predictions.** These are the rows of `CFB_API.PREDICTIONS` with `WEEK_SEQ` equal to P's `NEXT_WEEK_SEQ`. Each shows:
  - the matchup and the predicted score;
  - the pick with its win probability;
  - the freeze label ("frozen at 2026, after week 9");
  - a marker on rows whose `FREEZE_KIND` is `RECONSTRUCTED`;
  - a note on rescheduled games (`IS_RESCHEDULED`).
- A point without grades or without predictions says so (`app/as-of`).
- The examples run on the app test stack after the app's replay refresh of 2026 week 10. There, the week-10 predictions were frozen at point 9 before kickoff, while the earlier weeks' predictions are reconstructions.

## Scenarios

### Scenario: The scorecard headline is the marked row

* *GIVEN* the app test stack after the refresh of 2026 week 10
* *WHEN* the scorecard of 2026 week 10 is loaded for `FBS_INVOLVED`
* *THEN* the headline MUST equal the `IS_HEADLINE` row of `TEST_CFB_API.SCORECARD_WEEKS` for week 10: its record label, accuracy, Brier score and margin error, with freeze kind `IN_ADVANCE`
* *AND* the line's record and Brier score, and the model's Brier score on the line games, MUST equal that row's values
* *AND* the season to date MUST equal the `IS_HEADLINE` row of `TEST_CFB_API.SCORECARD_SEASONS` at week 10

### Scenario: A reconstructed headline says so

* *GIVEN* the app test stack, in which the week-8 predictions are reconstructions
* *WHEN* the scorecard of 2026 week 8 is shown, and then that of week 10
* *THEN* the week-8 headline MUST read "reconstructed at the as-of point (leak-free, made after kickoff)"
* *AND* the week-10 headline MUST NOT carry that label

### Scenario: This week's predictions are the ones frozen for the next week

* *GIVEN* the app test stack at the current point 2026 week 9
* *WHEN* the predictions page of week 9 is loaded
* *THEN* "this week" MUST list exactly the rows of `TEST_CFB_API.PREDICTIONS` with `WEEK_SEQ` 10, each with its pick, its predicted score, its win probability and the freeze label "2026, after week 9"
* *AND* no row with another `WEEK_SEQ` MUST be listed there

### Scenario: Last week's games are graded with their results

* *GIVEN* the app test stack after the refresh of 2026 week 10, with one week-10 game's stored result set to a tie by the harness
* *WHEN* the predictions page of week 10 is loaded
* *THEN* "last week" MUST list the predictions of the games played in week 10, each with its result and "correct" or "wrong"
* *AND* the tied game MUST read "not graded"

### Scenario: The calibration chart compares claimed and actual win shares

* *GIVEN* calibration rows for five buckets of the season
* *WHEN* the calibration chart is rendered and its table view is opened
* *THEN* the chart MUST draw one dot per bucket at its mean claimed probability and its actual win share, over a diagonal reference line
* *AND* the table view MUST list the same five buckets with their games, claimed probabilities and win shares

### Scenario: The population switch changes every number

* *GIVEN* the scorecard of 2026 week 10
* *WHEN* the user switches the population to `FBS_VS_FBS`
* *THEN* the headline, the line, the season to date and the calibration MUST all show the `FBS_VS_FBS` rows of their views
