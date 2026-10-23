# Feature: Head-to-head

"How often have Michigan and Ohio State met, and who leads?": the all-time series of any two teams comes from the stored games, instantly and at zero calls, not from CFBD's `/teams/matchup` (owner decision, 2026-10-22). The next meeting comes from the stored schedule; once the engine has run it also carries our prediction and what the game means for both teams' playoff odds. The live part of head-to-head is the game preview on click (`api/on-demand`).

## Background

- **`MATCHUP_SERIES`** (`CFB_MODEL.TEAM_GAME`, `TEAM`): one row per ordered pair of teams that met in at least one completed game (`HAS_RESULT`, both teams known), from the first team's side. Every pair has its mirror row. Columns: `TEAM_ID`, `OPPONENT_TEAM_ID`, `SCHOOL`, `OPPONENT_SCHOOL` (`TEAM.SCHOOL`), `MEETINGS`, `WINS`, `LOSSES`, `TIES`, `POINTS_FOR`, `POINTS_AGAINST`, `FIRST_SEASON`, `LAST_SEASON`, `LAST_GAME_ID`, `STREAK_KIND` (the result of the latest meeting: W, L or T), `STREAK_LENGTH` (consecutive latest meetings with that result), `WINS_HOME`, `WINS_AWAY`, `WINS_NEUTRAL` (wins by `SITE`), `BIGGEST_WIN_MARGIN`, `BIGGEST_WIN_SEASON`, `BIGGEST_LOSS_MARGIN` (a positive number), `BIGGEST_LOSS_SEASON` (the earlier meeting on a tie; NULL without a win or a loss). Meetings are ordered by season, week sequence, kickoff and game id.
- **`MATCHUP_GAMES`** (`TEAM_GAME`; with the engine `CFB_SIM.GAME_WIN_PROB`): one row per completed meeting and ordered pair. Columns: `TEAM_ID`, `OPPONENT_TEAM_ID`, `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `SITE`, `TEAM_POINTS`, `OPPONENT_POINTS`, `GAME_RESULT`, `PROB_SOURCE`, `TEAM_PREGAME_WIN_PROB`, `UPSET_MAGNITUDE`.
  - Before the engine: `PROB_SOURCE` is LINE and `TEAM_PREGAME_WIN_PROB` the stored line from the team's side when one is stored, else both NULL.
  - With the engine: both come from `CFB_SIM.GAME_WIN_PROB` (LINE where a line is stored, else ELO), from the team's side.
  - `UPSET_MAGNITUDE` is 1 − the winner's pregame probability when the winner's probability is below 0.5, else NULL.
- **`MATCHUP_NEXT`** (`TEAM_GAME`, `CFB_MODEL.CURRENT_AS_OF`; with the engine `CFB_SIM.PREDICTION`, `CURRENT_RUN`, `SIM_RUN_GAME`, `ROOTING_GUIDE`): one row per ordered pair with a game in the current season (`CURRENT_AS_OF.AS_OF_SEASON`) that is not completed, for the earliest such game by kickoff, then game id. Columns: `TEAM_ID`, `OPPONENT_TEAM_ID`, `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `SITE`, `TEAM_SPREAD`, `TEAM_PREGAME_WIN_PROB` (CFBD's line from the team's side as stored now; NULL without one), and the engine columns:
  - `PREDICTION_KIND`: FROZEN when the game has a `CFB_SIM.PREDICTION` row, else CURRENT when it is a remaining game of the what-if run (`CFB_SIM.CURRENT_RUN`), else NULL;
  - `TEAM_WIN_PROB`: from the team's side, the frozen `HOME_WIN_PROB` (FROZEN) or the run's `SIM_RUN_GAME.HOME_WIN_PROB` (CURRENT);
  - `PRED_TEAM_POINTS`, `PRED_OPPONENT_POINTS`: the frozen predicted points by side (FROZEN only; NULL for CURRENT, because a run stores no points);
  - `STAKES_RUN_ID` (the what-if run when the game is one of its remaining games) and `TEAM_P_IN_CFP_IF_WIN`, `TEAM_P_IN_CFP_IF_LOSE`, `OPPONENT_P_IN_CFP_IF_WIN`, `OPPONENT_P_IN_CFP_IF_LOSE`: each team's `P_IF_HOME_WIN` and `P_IF_AWAY_WIN` of that run's `CFB_SIM.ROOTING_GUIDE` row for the game, mapped to win and lose by side; NULL for a team without a guide row (it never made the field).

  Before the engine every engine column is NULL.
- No head-to-head view reads the virtual schema; `/teams/matchup` is not used.
- The examples run in the integration-test namespace over Phase 3's synthetic league (current point 2026 after week 9) with the serving schemas deployed; the engine scenario adds Phase 4's simulation schema.

## Scenarios

### Scenario: The all-time series comes from stored games without a call

* *GIVEN* the stored completed games between Ashford and Brookvale, including their 1950 tie
* *WHEN* `MATCHUP_SERIES` and `MATCHUP_GAMES` are queried for Ashford against Brookvale, and `MATCHUP_SERIES` for Brookvale against Ashford
* *THEN* Ashford's series row MUST have `MEETINGS`, `WINS`, `LOSSES`, `TIES`, `FIRST_SEASON`, `LAST_GAME_ID` and the streak equal to the counts over those games from Ashford's side
* *AND* Brookvale's row MUST be its mirror: wins and losses swapped, the same meetings and ties
* *AND* `MATCHUP_GAMES` MUST list exactly those games with Ashford's points first
* *AND* the mock MUST NOT receive any request

### Scenario: Teams that never met have no series

* *GIVEN* two teams of the synthetic league without a completed game against each other
* *WHEN* `MATCHUP_SERIES` and `MATCHUP_GAMES` are queried for that pair
* *THEN* both MUST return no rows

### Scenario: The next meeting is listed from the schedule before any run

* *GIVEN* a 2026 game of the synthetic league after week 9 between two teams, and no done simulation run
* *WHEN* `MATCHUP_NEXT` is queried for that pair in both orders
* *THEN* each order MUST return one row naming that game, with `TEAM_SPREAD` and `TEAM_PREGAME_WIN_PROB` from its own side
* *AND* every engine column MUST be NULL

### Scenario: With a run, the next meeting carries our prediction and the stakes

* *GIVEN* a done simulation run at 2026 week 9, with the week-10 predictions frozen
* *WHEN* `MATCHUP_NEXT` is queried for a pair that meets in week 10 and for a pair whose next meeting is later
* *THEN* the week-10 pair MUST have `PREDICTION_KIND` FROZEN with `TEAM_WIN_PROB` and the predicted points of its `PREDICTION` row from the team's side
* *AND* the later pair MUST have `PREDICTION_KIND` CURRENT, the run's probability from the team's side and NULL predicted points
* *AND* both MUST have `STAKES_RUN_ID` equal to the run and stakes equal to the run's `ROOTING_GUIDE` values mapped by side
