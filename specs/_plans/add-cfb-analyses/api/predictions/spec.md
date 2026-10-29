# Feature: Predictions and the scorecard

"The model went 41–9 last week": the predictions page, the team page's "this week" and the chat read the engine's frozen predictions and their grades (Phase 4) under serving names, with the schools spelled out. The "this week" tab shows our pick and how much each game matters next to CFBD's line.

## Background

- **`PREDICTIONS`** (`CFB_SIM.PREDICTION_GRADED`, `CFB_MODEL.TEAM`): one row per frozen prediction. Every column of `PREDICTION_GRADED` (the frozen `PREDICTION` columns, among them `FREEZE_KIND`, `FREEZE_LABEL`, `HOME_WIN_PROB`, `PICK_TEAM_ID`, `PICK_WIN_PROB`, `HOME_POINTS_PRED`, `AWAY_POINTS_PRED`, plus `HOME_POINTS`, `AWAY_POINTS`, `IS_COMPLETED`, `PLAYED_WEEK_SEQ`, `IS_RESCHEDULED`, `GRADE`, `BRIER`, `MARGIN_ERROR`, `LINE_HOME_WIN_PROB`, `LINE_PICK_CORRECT`, `LINE_BRIER`), plus `HOME_SCHOOL`, `AWAY_SCHOOL` and `PICK_SCHOOL`.
- **`SCORECARD_WEEKS`**: the rows of `CFB_SIM.PREDICTION_SCORECARD` with a `WEEK_SEQ` (one row per season, played week, population and freeze kind). **`SCORECARD_SEASONS`**: its rows with `WEEK_SEQ` NULL (one row per season, population and freeze kind, the season to date over every graded game stored now). **`SCORECARD_CALIBRATION`**: `CFB_SIM.PREDICTION_CALIBRATION` (one row per season, population, freeze kind and pick-probability bucket). All three show their source's columns unchanged.
- **Headline:** `IS_HEADLINE` is TRUE only on `IN_ADVANCE` rows (Phase 4). A week without graded `IN_ADVANCE` predictions has no headline row; the page then shows the `ALL` row labeled "reconstructed at the as-of point (leak-free, made after kickoff)".
- **`WEEK_GAMES` engine columns** (`api/teams-and-weeks`):
  - `RUN_ID`: the run shown at the row's point (`CFB_SIM.POINT_RUN`), NULL without one;
  - `PREDICTION_KIND`: FROZEN when the game has a `PREDICTION` row, else NULL;
  - `MODEL_HOME_WIN_PROB`, `PICK_TEAM_ID`, `HOME_POINTS_PRED`, `AWAY_POINTS_PRED`: the frozen values (NULL without a prediction);
  - `STAKES`: the largest `LEVERAGE` of the game over all teams in `CFB_SIM.ROOTING_GUIDE` of `RUN_ID`, NULL without a run or a guide row.
- The examples run in the integration-test namespace over Phase 3's synthetic league with Phase 4's simulation schema, a done run at 2026 week 9 and the predictions it froze (weeks 1–9 reconstructed, week 10 in advance), with the 2026 games up to week 9 completed.

## Scenarios

### Scenario: Frozen predictions are served with the schools, the grades and the stakes

* *GIVEN* the run at 2026 week 9 and its frozen predictions
* *WHEN* `PREDICTIONS` is queried, and `WEEK_GAMES` with `AS_OF_SEASON` 2026 and `AS_OF_WEEK_SEQ` 9
* *THEN* `PREDICTIONS` MUST return the rows and values of `CFB_SIM.PREDICTION_GRADED`, each with the schools of its home, away and picked team
* *AND* every week-10 row of `WEEK_GAMES` with a prediction MUST have `PREDICTION_KIND` FROZEN, `RUN_ID` the week-9 run, and the frozen `HOME_WIN_PROB`, pick and points
* *AND* its `STAKES` MUST equal the largest `LEVERAGE` of that game in the run's `ROOTING_GUIDE`

### Scenario: The scorecard separates the weeks from the season to date

* *GIVEN* the graded predictions of 2026 weeks 1 to 9
* *WHEN* `SCORECARD_WEEKS`, `SCORECARD_SEASONS` and `SCORECARD_CALIBRATION` are queried for 2026
* *THEN* every `SCORECARD_WEEKS` row MUST have a `WEEK_SEQ` and every `SCORECARD_SEASONS` row a NULL `WEEK_SEQ`
* *AND* together they MUST return exactly the 2026 rows of `CFB_SIM.PREDICTION_SCORECARD`, and `SCORECARD_CALIBRATION` exactly those of `PREDICTION_CALIBRATION`
* *AND* no row with `FREEZE_KIND` other than `IN_ADVANCE` MUST have `IS_HEADLINE` TRUE
