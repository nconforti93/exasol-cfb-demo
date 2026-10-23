# Feature: Teams, games and this week

The map, the team page and the "this week" tab need no simulation: every team with its colors, logo links and home location; every stored game from either side; records, polls and SP+; how far a team travels; and the coming week's games with their lines and ranks. These views serve them from the stored model at zero calls, before the engine has run, so the new UI works on real data from the day they are deployed. The chat reads the same views to answer questions outside the pages.

## Background

- **`TEAMS`** (`CFB_MODEL.TEAM`, plus `CFB_MODEL.VENUE` on `HOME_VENUE_ID`): one row per team, every team of the stored team list and every team seen only in games. Columns: `TEAM_ID`, `SCHOOL`, `MASCOT`, `ABBREVIATION`, `ALTERNATE_NAMES`, `CURRENT_CONFERENCE`, `CURRENT_DIVISION`, `CURRENT_CLASSIFICATION`, `IS_FBS` (`CURRENT_CLASSIFICATION` = 'fbs'), `COLOR`, `ALTERNATE_COLOR`, `LOGO_URL`, `LOGO_DARK_URL`, `HOME_VENUE_ID`, `HOME_VENUE_NAME` (`VENUE.VENUE_NAME`), `HOME_CITY` (`VENUE.CITY`), `HOME_STATE` (`VENUE.STATE_CODE`), `LATITUDE`, `LONGITUDE` (the team's own, from the team list), `HAS_LOCATION` (both coordinates known), `IS_IN_TEAM_LIST`, `FIRST_SEASON`, `LAST_SEASON`. Colors are as CFBD reports them (with or without '#'); the consumer validates them. The logo URLs are links only; the app serves its own cached copies.
- **Pass-through views:** `GAMES` (`CFB_MODEL.GAME`), `TEAM_GAMES` (`TEAM_GAME`), `TEAM_SEASONS` (`TEAM_SEASON`), `RANKINGS` (`RANKING`) and `SP_RATINGS` (`RATING_SP`). Each shows every column of its source with the same name, order and column comment. Its view comment starts with the source's grain, says that it shows stored history as it is now (not as of a point: filter `WEEK_SEQ` up to the point, or use the point views), and names the page views for the same questions. `GAMES` keeps the free-text `NOTES` column.
- **`TEAM_TRAVEL`** (`TEAM_GAME`, `GAME`, `TEAM`, `VENUE`, `GREAT_CIRCLE_KM`): one row per stored team game (a row of `TEAM_GAMES`). Columns: `TEAM_ID`, `GAME_ID`, `SEASON`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `OPPONENT_ID`, `SITE`, `HAS_RESULT`, `VENUE_ID`, `VENUE_NAME`, `VENUE_CITY`, `VENUE_STATE`, `KM_FROM_HOME`.
  - `KM_FROM_HOME` is 0 when `SITE` is HOME. Otherwise it is `GREAT_CIRCLE_KM` from the team's current home location (`TEAMS.LATITUDE`, `LONGITUDE`) to the game's venue, `DECIMAL(7,1)`, and NULL when either location is unknown.
  - The home is today's, also for historic games; the column comment says so.
  - As a view it runs the function with the owner's rights, so the roles need no `EXECUTE`.
- **`WEEK_GAMES`** (`AS_OF_POINT`, `GAME`, `RANKING`): one row per as-of point and game of the point's next week (`AS_OF_POINT.NEXT_WEEK_SEQ` in the point's season) whose teams are both known (`HAS_TEAMS`) and that involves an FBS team (`INVOLVES_FBS`). Columns:
  - the point: `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `AS_OF_LABEL`;
  - the game: `GAME_ID`, `SEASON_TYPE`, `WEEK`, `WEEK_SEQ`, `START_AT`, `IS_START_TIME_TBD`, `VENUE_ID`, `VENUE_NAME`, `IS_NEUTRAL_SITE`, `IS_CONFERENCE_GAME`, `IS_CONF_CHAMPIONSHIP`, `POSTSEASON_KIND`, `IS_FBS_GAME`, `HOME_TEAM_ID`, `HOME_TEAM`, `HOME_CONFERENCE`, `AWAY_TEAM_ID`, `AWAY_TEAM`, `AWAY_CONFERENCE`;
  - `HOME_AP_RANK`, `AWAY_AP_RANK`: the rank in the latest AP poll visible at the point by the label rule (`RANKING.VISIBLE_AT_WEEK_SEQ` at most the point's week sequence, same season); NULL when unranked;
  - CFBD's line as stored now: `PREGAME_SPREAD` (home side, negative when the home team is favored) and `HOME_PREGAME_WIN_PROB`;
  - the result as stored now: `IS_COMPLETED`, `HOME_POINTS`, `AWAY_POINTS`;
  - the engine columns `RUN_ID`, `PREDICTION_KIND`, `MODEL_HOME_WIN_PROB`, `PICK_TEAM_ID`, `HOME_POINTS_PRED`, `AWAY_POINTS_PRED` and `STAKES`, NULL until the engine exists (`api/predictions`).

  Filter it with literal `AS_OF_SEASON` and `AS_OF_WEEK_SEQ` values read from `CURRENT_POINT` or `AS_OF_POINTS`.
- The examples run in the integration-test namespace over Phase 3's synthetic league (current point 2026 after week 9) with the serving schemas deployed and no done simulation run.

## Scenarios

### Scenario: A team carries its marks and its home

* *GIVEN* the synthetic league, with Ashford in the stored team list and Marston seen only in games
* *WHEN* `TEAMS` is queried for Ashford and for Marston
* *THEN* Ashford's row MUST carry the `MASCOT`, `ABBREVIATION`, `COLOR`, `ALTERNATE_COLOR`, `LOGO_URL`, `LOGO_DARK_URL`, `LATITUDE` and `LONGITUDE` of `CFB_MODEL.TEAM`, and the name, city and state code of its home venue from `CFB_MODEL.VENUE`
* *AND* Marston's row MUST have `IS_IN_TEAM_LIST` FALSE, `HAS_LOCATION` FALSE and NULL colors, logo links and home venue

### Scenario: The model views are served unchanged

* *GIVEN* the synthetic league
* *WHEN* `GAMES`, `TEAM_GAMES`, `TEAM_SEASONS`, `RANKINGS` and `SP_RATINGS` are read with the same filter as their `CFB_MODEL` sources (Ashford's games of 1985, its 2025 team season, the AP poll of 2026 week 9, the SP+ ratings of 2025)
* *THEN* each MUST return the same rows and values as its source
* *AND* each MUST have its source's columns in the same order with the same comments

### Scenario: Travel is great-circle kilometres from the home venue

* *GIVEN* a team with a completed home game, a completed away game at a venue with coordinates, and a game at a venue without coordinates
* *WHEN* `TEAM_TRAVEL` is queried for that team
* *THEN* the home game MUST have `KM_FROM_HOME` 0
* *AND* the away game MUST have `KM_FROM_HOME` equal to `CFB_MODEL.GREAT_CIRCLE_KM` from the team's location to the venue's, to 0.1 km
* *AND* the game at the venue without coordinates MUST have `KM_FROM_HOME` NULL

### Scenario: This week lists the next week's games at a point

* *GIVEN* the synthetic league at its current point, 2026 after week 9, and the AP polls stored for 2026
* *WHEN* `WEEK_GAMES` is queried with `AS_OF_SEASON` 2026 and `AS_OF_WEEK_SEQ` 9
* *THEN* it MUST return exactly the 2026 week-10 games of `CFB_MODEL.GAME` with both teams known and an FBS team
* *AND* `HOME_AP_RANK` and `AWAY_AP_RANK` MUST be the teams' ranks in the latest AP poll whose `VISIBLE_AT_WEEK_SEQ` is at most 9
* *AND* `PREGAME_SPREAD` and `HOME_PREGAME_WIN_PROB` MUST equal the game's stored line
* *AND* the engine columns MUST be NULL
