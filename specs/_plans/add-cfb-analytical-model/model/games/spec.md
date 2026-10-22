# Feature: Games and team-games

Every stored game since 1869 is one row with its result, its pregame Elo and, from 2013, its pregame line and win probability. Each row also says which of these facts it actually has. A second view shows each game from both teams' side, which is the shape that records, head-to-head series, upsets and travel analyses need.

## Background

- `GAME`: one row per game of `CFB_CORE.GAMES`.
  - If the same game id is stored more than once, the row fetched last wins, and `MODEL_CHECKS` reports `DUPLICATE_GAME` for its season.
  - Keys and time: `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ` (`model/seasons-eras`), `START_AT` (UTC).
  - Teams: home and away team id and name, and the conference and classification recorded on the game.
  - Result (completed games only): points, `MARGIN` (home minus away), `TOTAL_POINTS`, `WINNER_TEAM_ID` and `LOSER_TEAM_ID`, and `IS_TIE` (equal points, no winner).
  - Elo and win probability as CFBD reports them: pregame and postgame Elo of both teams, postgame win probability.
  - **Pregame line:** `PREGAME_SPREAD` and `HOME_PREGAME_WIN_PROB` come from `CFB_CORE.METRICS_WP_PREGAME`, joined by game id. `PREGAME_WP_FETCHED_AT` is when that row was fetched. The spread is stored as CFBD reports it, from the home team's side.
  - **Postseason:** `POSTSEASON_KIND` is `CFP` for games of the College Football Playoff (CFBD `playoff.competition` = `cfp`) and `BOWL` for every other postseason game. It is NULL in the regular season. CFP games carry `CFP_ROUND`, `CFP_ROUND_NAME` and both seeds; `BOWL_NAME` is set for bowl games of either kind.
  - **Conference championship games** are not marked by CFBD. `IS_CONF_CHAMPIONSHIP` is TRUE for a game that meets all of these:
    - its season is in the `CONF_CHAMPIONSHIP` era `GAMES` (from 1992);
    - it is a regular-season conference game between two teams of the same conference, as recorded on the game;
    - it is that conference's only conference game in its week, and that week is the conference's last regular-season week with a conference game;
    - the conference played more than one conference game in its previous week with a conference game.
  - **Coverage flags:**
    - `HAS_RESULT`: completed with both scores;
    - `HAS_ELO`: both pregame Elo values;
    - `HAS_PREGAME_WP`: a pregame line row;
    - `HAS_LINE_SCORES`: both line-score arrays;
    - `HAS_VENUE_LOCATION`: the venue has coordinates;
    - `INVOLVES_FBS` and `IS_FBS_GAME`: one or both teams are recorded as `fbs`;
    - `PLAYS_ACCESS`: plays are never stored. The value is `ON_DEMAND` for seasons in which CFBD has plays (from 2001; they can be fetched for one game through the virtual schema) and `NOT_AVAILABLE` before.
- `TEAM_GAME`: one row per game and participating team, from that team's side.
  - `SITE`: `HOME`, `AWAY`, or `NEUTRAL` for both teams of a neutral-site game.
  - `GAME_RESULT`: `W`, `L` or `T` for completed games, else NULL.
  - Points for and against, and the margin from the team's side.
  - The team's and the opponent's pregame Elo, and the team's postgame Elo.
  - `TEAM_SPREAD`: the stored spread for the home team, the negated spread for the away team.
  - `TEAM_PREGAME_WIN_PROB`: `HOME_PREGAME_WIN_PROB` for the home team, one minus it for the away team.
  - `TRAVEL_KM` (`model/venues`).
- The examples run over the synthetic test league in the integration-test namespace.

## Scenarios

### Scenario: A game stored twice appears once

* *GIVEN* the 2026 week-3 game Carrow against Dunmore is stored twice: fetched at 2026-10-18 with 21–14 and at 2026-10-25 with 21–17
* *WHEN* `CFB_MODEL.GAME` is queried for that game id
* *THEN* it MUST return one row with 21–17
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `DUPLICATE_GAME` as failed for 2026 with 1 game

### Scenario: Results name the winner, and a tie has none

* *GIVEN* the completed 2024 week-14 game Ashford 31, Brookvale 24 at Ashford Field
* *AND* the completed 1950 game Ashford 7, Brookvale 7
* *AND* the 2026 week-12 game Brookvale against Ashford, which is not played yet
* *WHEN* `CFB_MODEL.GAME` and `CFB_MODEL.TEAM_GAME` are queried for the three games
* *THEN* the 2024 game MUST have Ashford as winner, Brookvale as loser, `MARGIN` 7 and `TOTAL_POINTS` 55, and `GAME_RESULT` `W` for Ashford and `L` for Brookvale
* *AND* the 1950 game MUST have `IS_TIE` TRUE, no winner, and `GAME_RESULT` `T` for both teams
* *AND* the 2026 game MUST have no points, no winner, `HAS_RESULT` FALSE and `GAME_RESULT` NULL for both teams

### Scenario: Pregame lines join by game id

* *GIVEN* a stored pregame line for the 2024 week-14 game Ashford against Brookvale: spread -7.5, home win probability 0.70
* *AND* no stored pregame line for the 1950 game Ashford against Brookvale
* *WHEN* `CFB_MODEL.GAME` and `CFB_MODEL.TEAM_GAME` are queried for both games
* *THEN* the 2024 game MUST have `PREGAME_SPREAD` -7.5, `HOME_PREGAME_WIN_PROB` 0.70 and `HAS_PREGAME_WP` TRUE
* *AND* Brookvale's team-game row MUST have `TEAM_SPREAD` 7.5 and `TEAM_PREGAME_WIN_PROB` 0.30
* *AND* the 1950 game MUST have `HAS_PREGAME_WP` FALSE and no line

### Scenario: Postseason games are typed

* *GIVEN* the 2024 CFP quarterfinal Ashford (seed 1) against Kingsley (seed 8) in the Sugar Bowl, at a neutral site
* *AND* the 2023 Desert Bowl Carrow against Eastport, which is not a playoff game
* *WHEN* `CFB_MODEL.GAME` is queried for both games and for a 2024 regular-season game
* *THEN* the quarterfinal MUST have `POSTSEASON_KIND` `CFP`, `CFP_ROUND` `quarterfinal`, seeds 1 and 8, `BOWL_NAME` Sugar Bowl and `WEEK_SEQ` 101
* *AND* the Desert Bowl MUST have `POSTSEASON_KIND` `BOWL` and no `CFP_ROUND`
* *AND* the regular-season game MUST have no `POSTSEASON_KIND`

### Scenario: A conference championship game is recognized by its schedule

* *GIVEN* in 2024 the SEC played two conference games in week 14 and only Ashford against Carrow in week 15
* *AND* the Pac-12 played two conference games in its last regular-season week, week 14
* *AND* the 1985 schedule has the same pattern for the SEC as 2024
* *WHEN* `CFB_MODEL.GAME` is queried for `IS_CONF_CHAMPIONSHIP`
* *THEN* the 2024 game Ashford against Carrow MUST be the only 2024 game with `IS_CONF_CHAMPIONSHIP` TRUE
* *AND* no Pac-12 game of 2024 MUST be marked
* *AND* no 1985 game MUST be marked, because that season is before the first conference championship game

### Scenario: Every game carries its coverage flags

* *GIVEN* the 1885 game Ashford against Marston at Old Grounds, a venue without coordinates, with no Elo values
* *AND* the 2024 week-14 game Ashford against Brookvale with both Elo values, both line scores and a venue with coordinates
* *WHEN* `CFB_MODEL.GAME` is queried for both games
* *THEN* the 1885 game MUST have `HAS_ELO` FALSE, `HAS_VENUE_LOCATION` FALSE and `PLAYS_ACCESS` `NOT_AVAILABLE`
* *AND* the 2024 game MUST have `HAS_ELO`, `HAS_LINE_SCORES`, `HAS_VENUE_LOCATION` and `IS_FBS_GAME` TRUE and `PLAYS_ACCESS` `ON_DEMAND`

### Scenario: Each team sees the game from its side

* *GIVEN* the 2024 week-14 game at Ashford Field and the neutral-site 2024 CFP quarterfinal
* *WHEN* `CFB_MODEL.TEAM_GAME` is queried for both games
* *THEN* each game MUST have exactly two rows, one per team, each naming the other team as opponent
* *AND* Ashford MUST have `SITE` `HOME` and Brookvale `AWAY` in the week-14 game, with points for and against swapped between them
* *AND* both teams MUST have `SITE` `NEUTRAL` in the quarterfinal

### Scenario: The head-to-head series comes from stored games

* *GIVEN* the stored meetings of Ashford and Brookvale: a 7–7 tie in 1950, a Brookvale win in 2014, an Ashford win in 2024 week 14, and the unplayed 2026 week-12 game
* *WHEN* the example analysis `head_to_head.sql` runs for Ashford and Brookvale
* *THEN* it MUST report 3 meetings with 1 Ashford win, 1 Brookvale win and 1 tie
* *AND* it MUST name 2024 week 14 as the last meeting and 2026 week 12 as the next one
* *AND* the mock API MUST NOT receive any request
