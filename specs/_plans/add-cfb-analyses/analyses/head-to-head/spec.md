# Feature: Head-to-head

Pick two teams and see every meeting since 1869, who leads the series and by how much, the current streak, the next meeting with our prediction, and what that game means for both teams' playoff odds. The series comes from the games already stored, so it answers instantly, works offline and costs no API call. The live element of the page is CFBD's game preview, fetched on a click (`api/on-demand`).

## Background

- **Source:** stored games only (`CFB_MODEL.TEAM_GAME` and `GAME`, `model/games`). CFBD's `/teams/matchup` endpoint is not used by any analysis or serving view (decision [14]):
  - every game since 1869 is stored (D15), so the series costs 0 calls;
  - `/teams/matchup` answered in 4.3 s at CFBD (S1, 4.9 s end to end), above the mission's 2 s target for an on-demand lookup;
  - it stays reachable through `CFBD_VS.TEAMS_MATCHUP` for the presenter's SQL.
- `CFB_PRED.MATCHUP_SERIES`: one row per ordered team pair (`TEAM_ID`, `OPPONENT_TEAM_ID`) with at least one completed meeting, from `TEAM_ID`'s side. The pair in the other order is the mirror row. Every season type and division counts.

  | Column | Meaning |
  |---|---|
  | `MEETINGS`, `WINS`, `LOSSES`, `TIES` | Completed meetings and their results from the team's side |
  | `POINTS_FOR`, `POINTS_AGAINST` | Sums over the meetings |
  | `FIRST_SEASON`, `LAST_SEASON`, `LAST_GAME_ID`, `LAST_GAME_RESULT` | First and latest meeting; `W`, `L` or `T` |
  | `STREAK_RESULT`, `STREAK_LENGTH` | The run of equal results counted back from the latest meeting (a tie is its own result) |
  | `HOME_WINS`, `AWAY_WINS`, `NEUTRAL_WINS` | Wins by the team's site |
  | `BIGGEST_WIN_MARGIN`, `BIGGEST_WIN_GAME_ID`, `BIGGEST_LOSS_MARGIN`, `BIGGEST_LOSS_GAME_ID` | Largest margins either way (the latest game on a tie of margins); NULL without such a game |

- `CFB_PRED.MATCHUP_NEXT`: one row per ordered team pair with a meeting still to play in the current season: the earliest game between them whose status at the current point (`CFB_MODEL.CURRENT_AS_OF`) is `NEXT` or `REMAINING` in `GAME_AS_OF`.
  - The game: `GAME_ID`, `START_AT`, `WEEK`, `WEEK_SEQ`, `SITE` from the team's side, `IS_CONF_CHAMPIONSHIP`.
  - Our prediction from the team's side: `PREDICTION_KIND` `FROZEN` when `CFB_PRED.PREDICTION` holds the game (`analyses/predictions`), else `CURRENT` from `CFB_SIM.GAME_SCORE_AS_OF` at the current point with params `CURRENT`; `TEAM_WIN_PROB`, `TEAM_POINTS_PRED`, `OPPONENT_POINTS_PRED` and `PREDICTED_MARGIN` (team minus opponent). A game the score model does not cover has NULL predictions and kind NULL.
  - The stakes, when the game is a remaining game of the what-if run (the run of the newest publication, `analyses/published-odds`): `STAKES_RUN_ID`, `TEAM_P_IN_CFP_IF_WIN`, `TEAM_P_IN_CFP_IF_LOSS`, `OPPONENT_P_IN_CFP_IF_WIN` and `OPPONENT_P_IN_CFP_IF_LOSS` (the opponent winning or losing), from that run's precomputed rooting-guide counts (`analyses/rooting-guide`). A team without any playoff season in the run has 0 either way. NULL when the game is not in the run.
- `CFB_API.MATCHUP_GAMES` lists the meetings themselves (`api/serving-contract`): one row per completed meeting and ordered pair, with the score, the site, `PROB_SOURCE` and `UPSET_MAGNITUDE` from `CFB_SIM.GAME_WIN_PROB`.
- Zero calls; read only.
- The examples run in the integration-test namespace over the sim league with the history variant of `analyses/upsets` (meetings of Ashford and Brookvale in 1950, 1990 and 1995, besides the league's own), with 2026 week 9 as the current point. A run of 4,000 seasons in 8 batches of 500 at 2026 week 9 is published where a scenario says so. Ashford hosts Brookvale in 2026 week 12.

## Scenarios

### Scenario: The all-time series comes from stored games without a call

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.MATCHUP_SERIES` is queried for Ashford against Brookvale
* *THEN* `MEETINGS`, `WINS`, `LOSSES`, `TIES`, `POINTS_FOR` and `POINTS_AGAINST` MUST equal the values the test counts from the completed games between them in `CFB_MODEL.GAME`, from Ashford's side
* *AND* `FIRST_SEASON` MUST be 1950, with the 1950 game counted among the `TIES` and the 1990 game among the `AWAY_WINS`
* *AND* the mock API and the replay store MUST NOT receive any request

### Scenario: Every pair has a mirror row

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.MATCHUP_SERIES` is queried for Ashford against Brookvale and for Brookvale against Ashford
* *THEN* Brookvale's `WINS` MUST equal Ashford's `LOSSES`, and Brookvale's `LOSSES` Ashford's `WINS`
* *AND* `MEETINGS`, `TIES`, `FIRST_SEASON`, `LAST_SEASON` and `LAST_GAME_ID` MUST be equal in both rows

### Scenario: The streak and the biggest results follow the meetings

* *GIVEN* the sim league with the history variant
* *WHEN* `CFB_PRED.MATCHUP_SERIES` is queried for Ashford against Brookvale
* *THEN* `LAST_GAME_ID` MUST be their latest completed meeting, and `STREAK_RESULT` and `STREAK_LENGTH` MUST equal the run of equal results the test counts back from it
* *AND* `BIGGEST_WIN_MARGIN`, `BIGGEST_LOSS_MARGIN` and their game ids MUST equal the values the test recomputes

### Scenario: The next meeting carries our prediction

* *GIVEN* the sim league at the current point 2026 week 9, with no prediction stored for the week-12 game Ashford against Brookvale
* *WHEN* `CFB_PRED.MATCHUP_NEXT` is queried for both orders of the pair, before and after the owner runs `CFB_INFRA.PREDICT(2026, 11, 'regular')`
* *THEN* before, Ashford's row MUST name the week-12 game with `SITE` `HOME`, `PREDICTION_KIND` `CURRENT`, `TEAM_WIN_PROB` 0.8289, `TEAM_POINTS_PRED` 38.5, `OPPONENT_POINTS_PRED` 24.0 and `PREDICTED_MARGIN` 14.50
* *AND* Brookvale's row MUST have `SITE` `AWAY`, `TEAM_WIN_PROB` 0.1711 and `PREDICTED_MARGIN` −14.50
* *AND* after, Ashford's row MUST have `PREDICTION_KIND` `FROZEN` and `TEAM_WIN_PROB` 0.8330

### Scenario: The next meeting shows what it means for both teams' playoff odds

* *GIVEN* the published run of 2026 week 9
* *WHEN* `CFB_PRED.MATCHUP_NEXT` is queried for Brookvale against Ashford
* *THEN* `STAKES_RUN_ID` MUST be the published run
* *AND* `TEAM_P_IN_CFP_IF_WIN` MUST equal Brookvale's `P_IF_AWAY_WIN` and `TEAM_P_IN_CFP_IF_LOSS` its `P_IF_HOME_WIN` for that game in `CFB_PRED.ROOTING_GUIDE` of the run
* *AND* the opponent columns MUST hold Ashford's values the same way, or 0 if Ashford has no playoff season in the run

### Scenario: Teams that never met have no series

* *GIVEN* the sim league with the history variant, in which Kestrel meets only Ashford
* *WHEN* `CFB_PRED.MATCHUP_SERIES` and `CFB_PRED.MATCHUP_NEXT` are queried for Kestrel against Brookvale
* *THEN* both MUST return no row
