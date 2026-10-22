# Feature: Biggest upsets, ranked against all of history

An upset is a win that few expected. Every weekend has a few, and the page ranks this week's against every game since pregame probabilities exist: from the betting line since 2013, from the pregame Elo that CFBD carries back to 1890 before that. "Saturday's upset was the 37th biggest of all time" is a sentence only a database holding all of history can say at zero API calls.

## Background

- `CFB_PRED.UPSET`: one row per completed game of `CFB_MODEL.GAME` that involves an FBS team (`INVOLVES_FBS`), has a winner and has a pregame probability in `CFB_SIM.GAME_WIN_PROB` (`sim-engine/win-probability`): the line's probability if the game has one, else the Elo probability. Ties and games without a probability have no row.
- Columns:
  - the game: `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `POSTSEASON_KIND`, `IS_NEUTRAL_SITE`, `IS_FBS_VS_FBS`;
  - the result: `WINNER_TEAM_ID`, `LOSER_TEAM_ID`, `WINNER_POINTS`, `LOSER_POINTS`, `WINNER_SITE` (`HOME`, `AWAY` or `NEUTRAL`);
  - the surprise: `PROB_SOURCE` (`LINE` or `ELO`), `WINNER_PREGAME_WIN_PROB` and `UPSET_MAGNITUDE` (1 − `WINNER_PREGAME_WIN_PROB`, the mission's definition), all from `GAME_WIN_PROB`; `IS_UPSET` is TRUE when `UPSET_MAGNITUDE` > 0.5, that is, the underdog won;
  - ranks by `UPSET_MAGNITUDE`, largest first, with equal magnitudes sharing a rank (`RANK()`):
    - `ALL_TIME_RANK` over every row;
    - `LINE_ERA_RANK` over the rows with `PROB_SOURCE` `LINE`, NULL for the others;
    - `SEASON_RANK` within the season, and `WEEK_RANK` within the season, season type and week;
  - `MODEL_WINNER_WIN_PROB`: the winner's probability in our frozen prediction (`CFB_PRED.PREDICTION`: `HOME_WIN_PROB` for a home winner, 1 − it for an away winner), NULL when we did not predict the game.
- Ranks are against all stored history as it is now, not as it was when the game was played; a later upset can push an earlier one down.
- `ALL_TIME_RANK` mixes two sources: CFBD's probabilities from betting lines and our Elo formula fitted against them (`sim-engine/win-probability`). `LINE_ERA_RANK` stays within the line era. The view comment says so, and pages show `PROB_SOURCE` next to every rank.
- The upset of a week is its row with `IS_UPSET` and `WEEK_RANK` 1.
- Zero calls; the view reads only `CFB_MODEL`, `CFB_SIM` and `CFB_PRED`.
- The examples run in the integration-test namespace over the sim league with the history variant of this plan (`tests/synthetic/analyses_history.py`), with `ELO_PROB_SCALE` 400 and `ELO_PROB_HFA` 55. The variant adds games before 1998 only, so it touches no season that the simulations or the calibration read:

  | Season | Game | Pregame Elo (home, away) |
  |---|---|---|
  | 1885 | Ashford 4 – Kestrel 0 at Ashford | none |
  | 1898 | Kestrel 6 at Ashford 5 | 2400, 1000 |
  | 1950 | Ashford 7 – Brookvale 7 at Ashford | 1600, 1500 |
  | 1990 | Ashford 35 at Brookvale 10 | 1550, 1650 |
  | 1995 | Brookvale 28 at Ashford 24 | 1800, 1400 |

## Scenarios

### Scenario: The week's upsets are ranked against all of history

* *GIVEN* the sim league with the history variant and the 2026 week-9 results
* *WHEN* `CFB_PRED.UPSET` is queried for 2026 week 9 with `IS_UPSET`
* *THEN* it MUST list exactly the week's completed games that involve an FBS team and whose winner had a pregame probability below 0.5 in `CFB_SIM.GAME_WIN_PROB`
* *AND* each row's `ALL_TIME_RANK` MUST be 1 plus the number of rows of `CFB_PRED.UPSET` with a larger `UPSET_MAGNITUDE`
* *AND* the row with `WEEK_RANK` 1 MUST have the week's largest `UPSET_MAGNITUDE`
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: An upset from the Elo era ranks next to the line era

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.UPSET` is queried for the 1898 and the 1995 game
* *THEN* the 1898 game MUST have `UPSET_MAGNITUDE` 0.9998 and the 1995 game 0.9321, both with `PROB_SOURCE` `ELO`
* *AND* the 1898 game's `ALL_TIME_RANK` MUST be smaller than the 1995 game's
* *AND* both MUST have `LINE_ERA_RANK` NULL

### Scenario: The line-era rank counts only games with a line

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.UPSET` is queried for every row with `PROB_SOURCE` `LINE`
* *THEN* each row's `LINE_ERA_RANK` MUST be 1 plus the number of `LINE` rows with a larger `UPSET_MAGNITUDE`
* *AND* `SEASON_RANK` MUST count within the row's season only

### Scenario: Ties and games without a probability are not ranked

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.UPSET` is queried for the 1885, 1950 and 1990 games
* *THEN* the 1885 game (no Elo) and the 1950 tie MUST have no row
* *AND* the 1990 game MUST have a row with `UPSET_MAGNITUDE` 0.4356, `WINNER_SITE` `AWAY` and `IS_UPSET` FALSE

### Scenario: Our own prediction is shown next to the upset

* *GIVEN* the predictions frozen by `REFRESH_WEEK(2026, 9, 'regular')` and the stored week-10 results
* *WHEN* `CFB_PRED.UPSET` is queried for 2026 week 10 and for the 1995 game
* *THEN* every week-10 row MUST carry `MODEL_WINNER_WIN_PROB` equal to the frozen `HOME_WIN_PROB` for a home winner and 1 − it for an away winner
* *AND* the 1995 game MUST have `MODEL_WINNER_WIN_PROB` NULL
