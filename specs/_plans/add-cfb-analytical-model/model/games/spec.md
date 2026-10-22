# Feature: Games and team-games

Every stored game since 1869 is one row with its result, its pregame Elo and, from 2013, its pregame line and win probability. Each row also says which of these facts it actually has, and when its teams were known. A second view shows each game from both teams' side, which is the shape that records, head-to-head series and upset analyses need.

## Background

- `GAME`: one row per game of `CFB_CORE.GAMES`.
  - **Deduplication:** if the same game id is stored more than once, the row fetched last wins (ties of fetch time: the later slice key, then the later row). The pregame line is deduplicated the same way, by game id. `MODEL_CHECKS` reports `DUPLICATE_SOURCE_ROW` as a WARNING for the season (`model/refresh-step`).
  - Keys and time: `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ` (`model/seasons-eras`), `START_AT` (UTC), `FETCHED_AT`.
  - Teams: home and away team id and name, and the conference recorded on the game for each team. `HAS_TEAMS` is FALSE for a placeholder game that CFBD lists before naming both teams.
  - Classification of each team: the code recorded on the game, unless the reference table `CLASSIFICATION_TRANSITION` has a row for the team and season (`model/teams-conferences`).
  - `IS_NEUTRAL_SITE` (CFBD `neutralSite`) and `IS_CONFERENCE_GAME` (CFBD `conferenceGame`).
  - Result (completed games only): points, `MARGIN` (home minus away), `TOTAL_POINTS`, `WINNER_TEAM_ID` and `LOSER_TEAM_ID`, and `IS_TIE` (equal points, no winner).
  - Elo and win probability as CFBD reports them: pregame and postgame Elo of both teams (integers), postgame win probability. The comments say that CFBD's Elo and win-probability models are fitted on all seasons, so historic values may embed later methodology. That is not a fact leak, and Phase 4's calibration states it.
  - **Pregame line:** `PREGAME_SPREAD` (`DECIMAL(6,2)`) and `HOME_PREGAME_WIN_PROB` (`DECIMAL(5,4)`) come from `CFB_CORE.METRICS_WP_PREGAME`, joined by game id. `PREGAME_WP_FETCHED_AT` is when that row was fetched. The spread is stored as CFBD reports it, from the home team's side.
  - **Postseason:** `POSTSEASON_KIND` is NULL in the regular season, and otherwise:
    - `CFP` for games of the College Football Playoff (CFBD `playoff.competition` = `cfp`);
    - `BOWL` for every other postseason game with an FBS team;
    - `NON_FBS` for postseason games without an FBS team (lower-division playoffs and bowls).

    CFP games carry `CFP_ROUND`, `CFP_ROUND_NAME`, both seeds and `CFP_ROUND_SEQ`: 1 for the season's first CFP round, 2 for the next, ordered by each round's first kickoff. `BOWL_NAME` is set for bowl games of either kind.
  - **Conference championship games** are not marked by CFBD.
    - The reference table `CONFERENCE_CHAMPIONSHIP` lists, per CFBD conference name, the season ranges in which the conference held a championship game: SEC from 1992, Western Athletic 1996–1998, Big 12 1996–2010 and from 2017, Mid-American from 1997, ACC and Conference USA from 2005, Big Ten from 2011, Pac-12 2011–2023, Mountain West from 2013, American Athletic from 2015, Sun Belt from 2018. It lists FBS conferences only.
    - `CONFERENCE_CHAMPIONSHIP_GAME`: one row per detected game. Inside a season range of conference C, a game is detected when it is a regular-season game between two teams recorded in C on the game, flagged as a conference game, is C's only such game in its week, and that week is C's last regular-season week with such a game.
    - `GAME.IS_CONF_CHAMPIONSHIP` is TRUE for the detected games. The game's `NOTES` text is a cross-check only: `MODEL_CHECKS` reports `CONF_CHAMPIONSHIP_NOTES_MISMATCH` and `CONF_CHAMPIONSHIP_MISSING` as warnings (`model/refresh-step`).
    - Champions of conferences without a championship game come from the standings. That is Phase 4's job.
  - **Participants rule** (`PARTICIPANTS_RULE`, used by `model/as-of`): when the game's teams were known.
    - `CONF_CHAMPIONSHIP` for conference championship games;
    - `SELECTION` for `BOWL` games and for `CFP` games with `CFP_ROUND_SEQ` 1;
    - `PREVIOUS_ROUND` for `CFP` games of later rounds and for `NON_FBS` games;
    - `FIXED` for every other game.
  - **Coverage flags:**
    - `HAS_RESULT`: completed with both scores;
    - `HAS_TEAMS`: both team ids set;
    - `HAS_ELO`: both pregame Elo values;
    - `HAS_PREGAME_WP`: a pregame line row;
    - `HAS_LINE_SCORES`: both line-score arrays;
    - `HAS_VENUE_LOCATION`: the venue has coordinates in `CFB_CORE.VENUES`;
    - `INVOLVES_FBS` and `IS_FBS_GAME`: one or both teams are classified `fbs`;
    - `PLAYS_ACCESS`: plays are never stored. The value is `ON_DEMAND` for seasons in which CFBD has plays (from 2001; they can be fetched for one game through the virtual schema) and `NOT_AVAILABLE` before.
- `TEAM_GAME`: one row per game and named participating team, from that team's side. A placeholder side without a team id has no row.
  - `SITE`: `HOME`, `AWAY`, or `NEUTRAL` for both teams of a neutral-site game.
  - `GAME_RESULT`: `W`, `L` or `T` for completed games, else NULL.
  - Points for and against, and the margin from the team's side.
  - `IS_CONFERENCE_GAME`, `IS_CONF_CHAMPIONSHIP` and `POSTSEASON_KIND` of the game.
  - The team's and the opponent's pregame Elo, and the team's postgame Elo.
  - `TEAM_SPREAD` (`DECIMAL(6,2)`): the stored spread for the home team, the negated spread for the away team.
  - `TEAM_PREGAME_WIN_PROB` (`DECIMAL(5,4)`): `HOME_PREGAME_WIN_PROB` for the home team, one minus it for the away team.
- The examples run over the synthetic test league in the integration-test namespace.

## Scenarios

### Scenario: A game stored twice appears once

* *GIVEN* the 2026 week-3 game Carrow against Dunmore is stored twice with its line: fetched at 2026-10-18 with 21–14 and spread -3.0, and at 2026-10-25 with 21–17 and spread -3.5
* *WHEN* `CFB_MODEL.GAME` is queried for that game id
* *THEN* it MUST return one row with 21–17 and `PREGAME_SPREAD` -3.50
* *AND* `CFB_MODEL.MODEL_CHECKS` MUST report `DUPLICATE_SOURCE_ROW` as failed for 2026 with severity `WARNING`

### Scenario: Results name the winner and the loser

* *GIVEN* the completed 2024 week-14 game Ashford 31, Brookvale 24 at Ashford Field
* *AND* the 2026 week-12 game Brookvale against Ashford, which is not played yet
* *WHEN* `CFB_MODEL.GAME` and `CFB_MODEL.TEAM_GAME` are queried for both games
* *THEN* the 2024 game MUST have Ashford as winner, Brookvale as loser, `MARGIN` 7 and `TOTAL_POINTS` 55, and `GAME_RESULT` `W` for Ashford and `L` for Brookvale
* *AND* the 2026 game MUST have no points, no winner, `HAS_RESULT` FALSE and `GAME_RESULT` NULL for both teams

### Scenario: A tie has no winner

* *GIVEN* the completed 1950 game Ashford 7, Brookvale 7
* *WHEN* `CFB_MODEL.GAME` and `CFB_MODEL.TEAM_GAME` are queried for that game
* *THEN* it MUST have `IS_TIE` TRUE and no winner or loser
* *AND* both teams MUST have `GAME_RESULT` `T`

### Scenario: Pregame lines join by game id

* *GIVEN* a stored pregame line for the 2024 week-14 game Ashford against Brookvale: spread -7.5, home win probability 0.70
* *AND* no stored pregame line for the 1950 game Ashford against Brookvale
* *WHEN* `CFB_MODEL.GAME` and `CFB_MODEL.TEAM_GAME` are queried for both games
* *THEN* the 2024 game MUST have `PREGAME_SPREAD` -7.50, `HOME_PREGAME_WIN_PROB` 0.7000 and `HAS_PREGAME_WP` TRUE
* *AND* Brookvale's team-game row MUST have `TEAM_SPREAD` 7.50 and `TEAM_PREGAME_WIN_PROB` 0.3000
* *AND* the 1950 game MUST have `HAS_PREGAME_WP` FALSE and no line

### Scenario: Postseason games are typed

* *GIVEN* the 2024 CFP first-round game Kingsley (seed 8) against Northam (seed 9), and the quarterfinal Ashford (seed 1) against Kingsley in the Sugar Bowl, at a neutral site
* *AND* the 2023 Desert Bowl Carrow against Eastport, which is not a playoff game
* *WHEN* `CFB_MODEL.GAME` is queried for the three games
* *THEN* the quarterfinal MUST have `POSTSEASON_KIND` `CFP`, `CFP_ROUND` `quarterfinal`, `CFP_ROUND_SEQ` 2, seeds 1 and 8, `BOWL_NAME` Sugar Bowl, `WEEK_SEQ` 101 and `PARTICIPANTS_RULE` `PREVIOUS_ROUND`
* *AND* the first-round game MUST have `CFP_ROUND_SEQ` 1 and `PARTICIPANTS_RULE` `SELECTION`
* *AND* the Desert Bowl MUST have `POSTSEASON_KIND` `BOWL`, no `CFP_ROUND` and `PARTICIPANTS_RULE` `SELECTION`

### Scenario: Postseason games without an FBS team wait for their own week

* *GIVEN* the 2024 postseason game Lowell against Pemberton, two `fcs` teams
* *WHEN* `CFB_MODEL.GAME` is queried for that game
* *THEN* it MUST have `POSTSEASON_KIND` `NON_FBS` and `PARTICIPANTS_RULE` `PREVIOUS_ROUND`

### Scenario: A conference championship game is recognized within its conference's championship seasons

* *GIVEN* in 2024 the SEC played two conference games in week 14 and only Ashford against Carrow in week 15
* *AND* in 2024 the Pac-12, which has no championship game after 2023, played two conference games in week 13 and one in week 14
* *AND* the 1985 SEC schedule has the same shape as the 2024 one, before the SEC's first championship game in 1992
* *WHEN* `CFB_MODEL.CONFERENCE_CHAMPIONSHIP_GAME` and `CFB_MODEL.GAME` are queried
* *THEN* Ashford against Carrow MUST be the SEC's 2024 championship game, with `IS_CONF_CHAMPIONSHIP` TRUE and `PARTICIPANTS_RULE` `CONF_CHAMPIONSHIP`
* *AND* no Pac-12 game of 2024 and no game of 1985 MUST be marked

### Scenario: A same-conference game after the championship game is not mistaken for it

* *GIVEN* in 2023 the American Athletic played two conference games in week 13 and only Ivel against Jessup in week 14
* *AND* in week 15 Jessup played Kenning, both recorded in the American Athletic, in a game not flagged as a conference game
* *WHEN* `CFB_MODEL.GAME` is queried for 2023
* *THEN* Ivel against Jessup MUST be the American Athletic's 2023 championship game
* *AND* Jessup against Kenning MUST NOT be marked

### Scenario: A championship game the rule misses is reported

* *GIVEN* in 2014 the Mountain West played Eastport against Fairhaven in week 15 with the notes "Mountain West Championship", and a makeup conference game between two other Mountain West teams in the same week
* *WHEN* `CFB_MODEL.GAME` and `CFB_MODEL.MODEL_CHECKS` are queried for 2014
* *THEN* no Mountain West game of 2014 MUST be marked as a championship game
* *AND* `MODEL_CHECKS` MUST report `CONF_CHAMPIONSHIP_NOTES_MISMATCH` naming Eastport against Fairhaven and `CONF_CHAMPIONSHIP_MISSING` naming the Mountain West, both with severity `WARNING`

### Scenario: A game without named teams is a placeholder

* *GIVEN* the 2026 week-15 game at the SEC championship site, listed without teams
* *WHEN* `CFB_MODEL.GAME` and `CFB_MODEL.TEAM_GAME` are queried for that game
* *THEN* the game MUST have `HAS_TEAMS` FALSE, `HAS_RESULT` FALSE and `PARTICIPANTS_RULE` `FIXED`
* *AND* `TEAM_GAME` MUST have no row for it

### Scenario: Every game carries its coverage flags

* *GIVEN* the 1885 game Ashford against Marston at Old Grounds, a venue without coordinates, with no Elo values
* *AND* the 2024 week-14 game Ashford against Brookvale with both Elo values, both line scores and a venue with coordinates
* *WHEN* `CFB_MODEL.GAME` is queried for both games
* *THEN* the 1885 game MUST have `HAS_ELO` FALSE, `HAS_VENUE_LOCATION` FALSE and `PLAYS_ACCESS` `NOT_AVAILABLE`
* *AND* the 2024 game MUST have `HAS_ELO`, `HAS_LINE_SCORES`, `HAS_VENUE_LOCATION`, `IS_CONFERENCE_GAME` and `IS_FBS_GAME` TRUE and `PLAYS_ACCESS` `ON_DEMAND`

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
