# Feature: Pregame win probability for every game

An upset is a win that few expected. To rank this week's upsets against all of history, every game needs a pregame win probability, also games long before betting lines were stored. From 2013 the probability comes from the stored betting line. Before that it comes from the pregame Elo that CFBD carries back to 1890, through a formula fitted against the lines. One view gives both, says which one it used, and measures how big each upset was.

## Background

- `CFB_SIM.GAME_WIN_PROB`: one row per completed game of `CFB_MODEL.GAME` with both teams, in every stored season. It uses only pregame facts of the game itself: the line and the pregame Elo values, which were known before kickoff. Columns:
  - the game keys, the season, both teams, `IS_NEUTRAL_SITE` and the result;
  - `HOME_WIN_PROB_LINE` (`DECIMAL(5,4)`): CFBD's pregame home win probability from the stored line (`GAME.HOME_PREGAME_WIN_PROB`, stored from 2013);
  - `HOME_WIN_PROB_ELO` (`DECIMAL(5,4)`) = 1 / (1 + 10^(−(home pregame Elo − away pregame Elo + `ELO_PROB_HFA` × not neutral) / `ELO_PROB_SCALE`)), with the `CURRENT` parameters. NULL unless both pregame Elo values exist;
  - `HOME_WIN_PROB` and `PROB_SOURCE`: the line's probability (`LINE`) if there is one, else the Elo probability (`ELO`), else NULL and NULL;
  - `WINNER_TEAM_ID` and `WINNER_PREGAME_WIN_PROB`: the winner's pregame probability (`HOME_WIN_PROB` for a home win, 1 − it for an away win);
  - `UPSET_MAGNITUDE` (`DECIMAL(5,4)`) = 1 − `WINNER_PREGAME_WIN_PROB`, the mission's definition. NULL for ties and for games without a probability.
- `ELO_PROB_SCALE` and `ELO_PROB_HFA` are fitted by `make calibrate` on the completed FBS games of 2014–2025 (`sim-engine/calibration`). The calibration report compares the Elo probability with the line's on those seasons, as Brier score and log loss.
- CFBD's Elo and line probabilities come from CFBD's own models, which may apply a later methodology to historic seasons. The view's comment says so.
- The examples run over the sim league with `ELO_PROB_SCALE` 400 and `ELO_PROB_HFA` 55.

## Scenarios

### Scenario: A game since 2013 uses its betting line

* *GIVEN* a completed 2024 game with a stored home win probability of 0.7000, a home pregame Elo of 1600 and an away Elo of 1500
* *WHEN* `CFB_SIM.GAME_WIN_PROB` is queried for it
* *THEN* `HOME_WIN_PROB` MUST be 0.7000 with `PROB_SOURCE` `LINE`
* *AND* `HOME_WIN_PROB_ELO` MUST be 0.7094

### Scenario: An older game uses its pregame Elo

* *GIVEN* a completed 1950 game without a line, played at a neutral site, with pregame Elo values of 1600 for the home team and 1500 for the away team
* *WHEN* `CFB_SIM.GAME_WIN_PROB` is queried for it
* *THEN* `HOME_WIN_PROB` MUST be 0.6401 with `PROB_SOURCE` `ELO`

### Scenario: A game without a line or an Elo has no probability

* *GIVEN* a completed 1885 game without a line and without pregame Elo values
* *WHEN* `CFB_SIM.GAME_WIN_PROB` is queried for it
* *THEN* the row MUST exist with `HOME_WIN_PROB`, `PROB_SOURCE` and `UPSET_MAGNITUDE` NULL

### Scenario: The upset magnitude is what the winner was not expected to get

* *GIVEN* the 2024 game of the first scenario, won by the away team, and a 1950 game that ended in a tie
* *WHEN* `CFB_SIM.GAME_WIN_PROB` is queried for both
* *THEN* the 2024 game MUST have `WINNER_PREGAME_WIN_PROB` 0.3000 and `UPSET_MAGNITUDE` 0.7000
* *AND* the tie MUST have `WINNER_TEAM_ID` and `UPSET_MAGNITUDE` NULL
