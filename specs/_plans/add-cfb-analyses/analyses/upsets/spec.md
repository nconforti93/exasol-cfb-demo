# Feature: Biggest upsets, ranked against all of history

An upset is a win that few expected. Every weekend has a few, and the page ranks this week's against every game of the modern era that has a pregame probability: from the betting line since 2013, from the pregame Elo that CFBD carries back to 1890 before that. "Saturday's upset was the 37th biggest since 1978" is a sentence only a database holding all of history can say at zero API calls.

## Background

- `CFB_PRED.UPSET`: one row per completed game of `CFB_MODEL.GAME` that involves an FBS team (`INVOLVES_FBS`), has a winner and has a pregame probability in `CFB_SIM.GAME_WIN_PROB` (`sim-engine/win-probability`): the line's probability if the game has one, else the Elo probability. Ties and games without a probability have no row.
- Columns:
  - the game: `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `POSTSEASON_KIND`, `IS_NEUTRAL_SITE`, `IS_FBS_VS_FBS`;
  - the result: `WINNER_TEAM_ID`, `LOSER_TEAM_ID`, `WINNER_POINTS`, `LOSER_POINTS`, `WINNER_SITE` (`HOME`, `AWAY` or `NEUTRAL`);
  - the surprise: `PROB_SOURCE` (`LINE` or `ELO`), `WINNER_PREGAME_WIN_PROB` and `UPSET_MAGNITUDE` (1 − `WINNER_PREGAME_WIN_PROB`, the mission's definition, `DECIMAL(5,4)` for display), all from `GAME_WIN_PROB`; `IS_UPSET` is TRUE when `UPSET_MAGNITUDE` > 0.5, that is, the underdog won;
  - `RANK_MAGNITUDE` (`DOUBLE`): the same magnitude before rounding, which the ranks use.
    - For `ELO` rows it is computed in the view from the two pregame Elo values with the formula and the `CURRENT` parameters of `sim-engine/win-probability` (`ELO_PROB_SCALE`, `ELO_PROB_HFA`).
    - For `LINE` rows it is `UPSET_MAGNITUDE`, because CFBD's stored line probability has four decimals; equal values there are real ties at the source's precision.
    - Rounded to four decimals it equals `UPSET_MAGNITUDE` on every row.
  - ranks by `RANK_MAGNITUDE`, largest first, with equal magnitudes sharing a rank (`RANK()`):
    - `ALL_TIME_RANK` over the rows of the all-time window (below), NULL for older rows, with `ALL_TIME_FROM_SEASON` on every row;
    - `LINE_ERA_RANK` over the rows with `PROB_SOURCE` `LINE`, NULL for the others;
    - `SEASON_RANK` within the season, and `WEEK_RANK` within the season, season type and week, for every season;
    - `ALL_TIME_TIED` and `LINE_ERA_TIED`: TRUE when another row shares the rank. Pages show a shared rank as "T-37".
  - `MODEL_WINNER_WIN_PROB`: the winner's probability in our frozen prediction (`CFB_PRED.PREDICTION`: `HOME_WIN_PROB` for a home winner, 1 − it for an away winner), NULL when we did not predict the game.
- **The all-time window** starts at `ALL_TIME_FROM_SEASON` in `CFB_PRED.ANALYSIS_SETTINGS`, 1978 by default. Older games keep their magnitude, `SEASON_RANK` and `WEEK_RANK`.
  - 1978 is the first season of Division I-A, the predecessor of FBS. Before it, "involves an FBS team" is a retroactive label (`model/teams-conferences`, Phase 3's open question Q6), so the population would be anachronistic either way.
  - The Elo formula is fitted on 2014–2025. The Elo gaps of early, sparsely connected seasons (1,000 points and more) lie far outside that range, and their probabilities crowd at 0.9999.
  - The default is checked on real data in task 3.3 (`plan.md`, Q8) and can be changed with a logged decision.
- Ranks are against stored history as it is now, not as it was when the game was played; a later upset can push an earlier one down.
- `ALL_TIME_RANK` mixes two sources: CFBD's probabilities from betting lines and our Elo formula fitted against them. `LINE_ERA_RANK` stays within the line era. The view comment says so, and pages show `PROB_SOURCE` next to every rank. Within the Elo era the order of the games depends on the Elo gaps and `ELO_PROB_HFA` only, not on `ELO_PROB_SCALE`.
- The upset of a week is its row with `IS_UPSET` and `WEEK_RANK` 1.
- Zero calls; the view reads only `CFB_MODEL`, `CFB_SIM` and `CFB_PRED`.
- The examples run in the integration-test namespace over the sim league with the history variant of this plan (`tests/synthetic/analyses_history.py`), with `ELO_PROB_SCALE` 400, `ELO_PROB_HFA` 55 and the default `ALL_TIME_FROM_SEASON` 1978. The variant adds games before 1998 only, so it touches no season that the simulations or the calibration read:

  | Season | Game | Pregame Elo (home, away) | Magnitude before rounding |
  |---|---|---|---|
  | 1885 | Ashford 4 – Kestrel 0 at Ashford | none | none |
  | 1898 | Kestrel 6 at Ashford 5 | 2400, 1000 | 0.99976964 |
  | 1950 | Ashford 7 – Brookvale 7 at Ashford | 1600, 1500 | tie |
  | 1985 | Brookvale 3 at Ashford 0 | 2400, 1000 | 0.99976964 |
  | 1987 | Brookvale 7 at Ashford 6 | 2395, 1000 | 0.99976292 |
  | 1990 | Ashford 35 at Brookvale 10 | 1550, 1650 | 0.43559 |
  | 1995 | Brookvale 28 at Ashford 24 | 1800, 1400 | 0.93209 |

## Scenarios

### Scenario: The week's upsets are ranked against all of history

* *GIVEN* the sim league with the history variant and the 2026 week-9 results
* *WHEN* `CFB_PRED.UPSET` is queried for 2026 week 9 with `IS_UPSET`
* *THEN* it MUST list exactly the week's completed games that involve an FBS team and whose winner had a pregame probability below 0.5 in `CFB_SIM.GAME_WIN_PROB`
* *AND* each row's `ALL_TIME_RANK` MUST be 1 plus the number of rows of `CFB_PRED.UPSET` from season 1978 on with a larger `RANK_MAGNITUDE`
* *AND* the row with `WEEK_RANK` 1 MUST have the week's largest `RANK_MAGNITUDE`
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: An upset from the Elo era ranks next to the line era

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.UPSET` is queried for the 1985 and the 1995 game
* *THEN* the 1995 game MUST have `UPSET_MAGNITUDE` 0.9321 with `PROB_SOURCE` `ELO` and an `ALL_TIME_RANK`
* *AND* the 1985 game's `ALL_TIME_RANK` MUST be smaller than the 1995 game's
* *AND* both MUST have `LINE_ERA_RANK` NULL

### Scenario: Ranks use the magnitude before rounding

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.UPSET` is queried for the 1985 and the 1987 game, and for every row
* *THEN* both games MUST have `UPSET_MAGNITUDE` 0.9998, the 1985 game's `ALL_TIME_RANK` MUST be smaller than the 1987 game's, and both MUST have `ALL_TIME_TIED` FALSE
* *AND* every row's `RANK_MAGNITUDE` MUST differ from its `UPSET_MAGNITUDE` by at most 0.00005

### Scenario: Upsets before the all-time window rank within their season only

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.UPSET` is queried for the 1898 game, and again after the test sets `ALL_TIME_FROM_SEASON` to 1890
* *THEN* first the 1898 game MUST have `UPSET_MAGNITUDE` 0.9998, `PROB_SOURCE` `ELO`, `SEASON_RANK` 1, `ALL_TIME_FROM_SEASON` 1978 and `ALL_TIME_RANK` NULL
* *AND* then the 1898 and the 1985 game MUST share one `ALL_TIME_RANK`, both with `ALL_TIME_TIED` TRUE, and the 1987 game's `ALL_TIME_RANK` MUST be larger than theirs

### Scenario: The line-era rank counts only games with a line

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.UPSET` is queried for every row with `PROB_SOURCE` `LINE`
* *THEN* each row's `LINE_ERA_RANK` MUST be 1 plus the number of `LINE` rows with a larger `RANK_MAGNITUDE`
* *AND* `LINE_ERA_TIED` MUST be TRUE exactly where another `LINE` row has the same `RANK_MAGNITUDE`
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
