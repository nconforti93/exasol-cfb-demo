# Feature: League stats

The stats tab shows four cards that make the database's work visible: the road warriors who travelled farthest (great-circle kilometres over every trip), the biggest SP+ risers and fallers against last season, the strength of each conference, and the hottest streaks across seasons. Each card is one view, so the SQL a card shows is short and the chat can ask the same questions. The views that count games have an as-of point, so the as-of picker shows each card as it stood after that week.

## Background

- **Point grain.** `ROAD_WARRIORS`, `CONFERENCE_STRENGTH` and `WIN_STREAKS` have one row per as-of point (`CFB_MODEL.AS_OF_POINT`) and subject. A game counts at a point when it is completed (`HAS_RESULT`) and lies at or before the point: an earlier season, or the point's season with `WEEK_SEQ` at most `AS_OF_WEEK_SEQ`. Filter them with literal `AS_OF_SEASON` and `AS_OF_WEEK_SEQ` values; an unfiltered query evaluates every point.
- **FBS team-seasons** are the rows of `CFB_MODEL.TEAM_SEASON` with `CLASSIFICATION` 'fbs' in the point's season (for `SP_MOVERS`, in its season).
- **`ROAD_WARRIORS`** (`CFB_API.TEAM_TRAVEL`, `TEAM_SEASON`): one row per as-of point and FBS team-season of the point's season with at least one counted AWAY or NEUTRAL game of that season whose `KM_FROM_HOME` is known. Columns: `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `TEAM_ID`, `SCHOOL`, `CONFERENCE_NAME`, `TRIPS` (those games), `KM_TRAVELED` (the sum of their `KM_FROM_HOME`), `LONGEST_TRIP_KM`, `LONGEST_TRIP_GAME_ID`, `LONGEST_TRIP_TO` (the venue's city, else its name; the earlier game on a tie), `TRAVEL_RANK` (`RANK` by `KM_TRAVELED` descending within the point).
- **`SP_MOVERS`** (`TEAM_SEASON`): one row per season and FBS team-season with both `SP_RATING` and `SP_PRIOR_RATING`. Columns: `SEASON`, `TEAM_ID`, `SCHOOL`, `CONFERENCE_NAME`, `SP_RATING`, `SP_PRIOR_RATING`, `SP_DELTA` (`SP_RATING` − `SP_PRIOR_RATING`), `RISE_RANK` (`ROW_NUMBER` by `SP_DELTA` descending, then `TEAM_ID`), `FALL_RANK` (by `SP_DELTA` ascending, then `TEAM_ID`). SP+ is as last fetched; the current season's ratings change with each refresh, which the view comment says.
- **`CONFERENCE_STRENGTH`** (`TEAM_SEASON`, `TEAM_GAME`, `RANKING`): one row per as-of point and conference with FBS team-seasons in the point's season. Columns: `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `CONFERENCE_NAME`, `CONFERENCE_TIER`, `TEAM_COUNT`, `MEAN_SP_RATING`, `MEAN_SP_PRIOR_RATING` (means over the members that have one), `NONCONF_GAMES`, `NONCONF_WINS`, `NONCONF_LOSSES` (the members' counted games of the season against FBS opponents that are not conference games), `AP_RANKED_TEAMS` (members ranked in the latest AP poll visible at the point by the label rule), `STRENGTH_RANK` (`RANK` by `MEAN_SP_RATING` descending within the point; NULL without SP+).
- **`WIN_STREAKS`** (`TEAM_GAME`, `TEAM_SEASON`, `TEAM`): one row per as-of point and FBS team-season of the point's season with a counted game. Games are ordered by season, week sequence, kickoff and game id. Columns: `AS_OF_SEASON`, `AS_OF_WEEK_SEQ`, `TEAM_ID`, `SCHOOL`, `STREAK_KIND` (the `GAME_RESULT` of the team's latest counted game: W, L or T), `STREAK_LENGTH` (the number of consecutive counted games, back from that one and across seasons, with that result), `SINCE_GAME_ID`, `SINCE_SEASON`, `SINCE_WEEK_SEQ` (the streak's first game), `LAST_GAME_ID`, `WIN_STREAK_RANK` (`RANK` by `STREAK_LENGTH` descending among the point's rows with `STREAK_KIND` W; NULL for the others).
- The examples run in the integration-test namespace over Phase 3's synthetic league (current point 2026 after week 9) with the serving schemas deployed.

## Scenarios

### Scenario: The season cards add up the games up to the point

* *GIVEN* the synthetic league at 2026 after week 9, with 2026 week-10 games scheduled and some completed in the test
* *WHEN* `ROAD_WARRIORS` and `CONFERENCE_STRENGTH` are queried with `AS_OF_SEASON` 2026 and `AS_OF_WEEK_SEQ` 9, and `SP_MOVERS` with `SEASON` 2026
* *THEN* each `ROAD_WARRIORS` row MUST equal the count, sum and maximum of the team's 2026 `TEAM_TRAVEL` rows with `WEEK_SEQ` at most 9, `HAS_RESULT` TRUE, `SITE` not HOME and a known `KM_FROM_HOME`
* *AND* no week-10 game MUST count in either view
* *AND* each `CONFERENCE_STRENGTH` row MUST have `MEAN_SP_RATING` equal to the mean `SP_RATING` of its FBS members in `TEAM_SEASONS` and `NONCONF_WINS` + `NONCONF_LOSSES` at most `NONCONF_GAMES`
* *AND* each `SP_MOVERS` row MUST have `SP_DELTA` = `SP_RATING` − `SP_PRIOR_RATING`, and the row with `RISE_RANK` 1 MUST have the largest `SP_DELTA`

### Scenario: A streak counts the latest run of equal results across seasons

* *GIVEN* an FBS team whose counted games end, across 2025 and 2026, in a run of wins after a loss
* *WHEN* `WIN_STREAKS` is queried for that team with `AS_OF_SEASON` 2026 and `AS_OF_WEEK_SEQ` 9, and again with `AS_OF_WEEK_SEQ` 0
* *THEN* the first row MUST have `STREAK_KIND` W, a `STREAK_LENGTH` equal to the number of wins after that loss, and the first of those wins as `SINCE_GAME_ID`
* *AND* the second row MUST count only the games of seasons before 2026
