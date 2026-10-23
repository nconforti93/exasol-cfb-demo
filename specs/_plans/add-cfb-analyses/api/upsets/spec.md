# Feature: Upsets ranked against history

"The upset of the week ranks among the biggest of all time": every win by the pregame underdog is ranked within its week, its season and all of history. Before the engine exists the ranking uses the stored betting lines (from 2013); with the engine it adds the Elo-based probability of earlier games (Phase 4's `GAME_WIN_PROB`), ranks all-time from 1978, the first Division I-A season, and shows our own frozen probability next to CFBD's.

## Background

- **`UPSETS`**: one row per completed game that involves an FBS team and was won by the pregame underdog: the winner's pregame win probability is below 0.5. Ties and games without a probability have no row. Columns: `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `WINNER_TEAM_ID`, `WINNER_SCHOOL`, `LOSER_TEAM_ID`, `LOSER_SCHOOL` (`TEAM.SCHOOL`), `WINNER_POINTS`, `LOSER_POINTS`, `WINNER_SITE` (HOME, AWAY or NEUTRAL), `PROB_SOURCE`, `WINNER_PREGAME_WIN_PROB`, `UPSET_MAGNITUDE` (1 − `WINNER_PREGAME_WIN_PROB`), `WEEK_RANK`, `SEASON_RANK`, `ALL_TIME_RANK`, `ALL_TIME_TIED`, `ALL_TIME_FROM_SEASON`, `LINE_ERA_RANK`, `LINE_ERA_TIED`, `MODEL_WINNER_WIN_PROB`.
- **Ranks** are `RANK()` by `UPSET_MAGNITUDE` descending, so equal magnitudes share a rank and the next rank skips (pages show "T-37"):
  - `WEEK_RANK` within the season and week sequence, `SEASON_RANK` within the season;
  - `ALL_TIME_RANK` over the rows from `ALL_TIME_FROM_SEASON` on; NULL for earlier seasons;
  - `LINE_ERA_RANK` over the rows with `PROB_SOURCE` LINE; NULL for the others;
  - `ALL_TIME_TIED` and `LINE_ERA_TIED` are TRUE when another ranked row has the same magnitude in that ranking.
- **Before the engine** (`CFB_MODEL.GAME`): the probability is the stored line (`HOME_PREGAME_WIN_PROB`, from the winner's side), `PROB_SOURCE` LINE, `ALL_TIME_FROM_SEASON` 2013 (the first season with stored lines), and `MODEL_WINNER_WIN_PROB` NULL.
- **With the engine** (`CFB_SIM.GAME_WIN_PROB`, `PREDICTION`; `CFB_API.ANALYSIS_SETTINGS`): the probability, `PROB_SOURCE` (LINE where a line is stored, else ELO) and `UPSET_MAGNITUDE` come from `GAME_WIN_PROB` as Phase 4 rounds them; `ALL_TIME_FROM_SEASON` is the setting `ALL_TIME_FROM_SEASON` (1978); `MODEL_WINNER_WIN_PROB` is our frozen probability for the winner (`PREDICTION.HOME_WIN_PROB` or 1 − it), NULL for a game without a prediction.
- The examples run in the integration-test namespace over Phase 3's synthetic league with the serving schemas deployed; the engine scenario adds Phase 4's simulation schema.

## Scenarios

### Scenario: Upsets are ranked within their week, their season and the line era

* *GIVEN* completed games with stored lines since 2013, among them two underdog wins of equal magnitude in different seasons, a favorite's win and a tie
* *WHEN* `UPSETS` is queried
* *THEN* among those games it MUST hold exactly the underdog wins, each with `PROB_SOURCE` LINE and `UPSET_MAGNITUDE` = 1 − the winner's line probability
* *AND* `WEEK_RANK`, `SEASON_RANK` and `LINE_ERA_RANK` MUST be the ranks of `UPSET_MAGNITUDE` within the week, the season and all LINE rows, the two equal magnitudes sharing one `LINE_ERA_RANK` with `LINE_ERA_TIED` TRUE
* *AND* `ALL_TIME_RANK` MUST be the rank within all rows from `ALL_TIME_FROM_SEASON` on, which is 2013 before the engine exists

### Scenario: With the engine, upsets since 1978 include the Elo era and our own probability

* *GIVEN* Phase 4's `GAME_WIN_PROB` with an Elo-based underdog win after 1978, one before 1978, and a 2026 underdog win whose prediction was frozen
* *WHEN* `UPSETS` is queried
* *THEN* the win after 1978 MUST have `PROB_SOURCE` ELO, an `ALL_TIME_RANK` and a NULL `LINE_ERA_RANK`
* *AND* the win before 1978 MUST have a NULL `ALL_TIME_RANK` and its week and season ranks
* *AND* every row MUST have `ALL_TIME_FROM_SEASON` 1978, and the 2026 win's `MODEL_WINNER_WIN_PROB` MUST be its frozen probability for the winner
