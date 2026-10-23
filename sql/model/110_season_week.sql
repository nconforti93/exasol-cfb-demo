-- Weeks of a season (model/seasons-eras). The weeks of the games and of the stored calendar are
-- combined with UNION ALL and grouped by (season, season type, week), so a season filter reaches
-- both branches (probe 1.1g); a FULL OUTER JOIN on COALESCE keys would hide it. Calendar weeks are
-- deduplicated by (season, season type, week), newest fetch first. IS_DONE needs at least one game
-- for its "all games completed" half, so an empty future calendar week never counts as done.
-- FIRST_GAME_AT is the first kickoff of the week's games, and the calendar's FIRST_GAME_START only for
-- a week without stored games: on the real data CFBD's firstGameStart equals the week's startDate
-- (midnight Pacific), not a kickoff, and 61 lower-division games of 2026 week 1 kick off before it
-- (decision [39]). It is the as-of cutoff of the week before (AS_OF_POINT.CUTOFF_AT).
-- Two restructurings from the performance smoke (task 3.7):
-- - The season-wide GAMES_FETCHED_AT is a window over the grouped weeks in its own step, not inside
--   the GROUP BY step: a window over a GROUP BY result stops the season filter from reaching GAMES.
-- - The games come from CFB_CORE.GAMES with the same deduplication as GAME (newest fetch first), not
--   from GAME. Exasol evaluates a view that appears twice in one query once, without either
--   filter, and the as-of views join SEASON_WEEK to GAME: through GAME, a one-point query scanned
--   all 250,000 games, and the weeks needed none of GAME's venue, line, round and championship
--   columns. IS_COMPLETED, START_AT and FETCHED_AT are GAME's definitions of the same columns.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.SEASON_WEEK (
  SEASON               COMMENT IS 'Season',
  SEASON_TYPE          COMMENT IS 'regular or postseason; the only season types with weeks',
  WEEK                 COMMENT IS 'Week as CFBD labels it within the season type',
  WEEK_SEQ             COMMENT IS 'Week sequence: regular week n = n, postseason week n = 100 + n, so the postseason follows the regular season and (season, week, season type) of REFRESH_WEEK maps to one row; 0 is reserved for the preseason point of the as-of helpers',
  START_AT             COMMENT IS 'Start of the week in UTC: the stored calendar''s start date when DATES_SOURCE is CALENDAR, else the first kickoff of the week''s games',
  END_AT               COMMENT IS 'End of the week in UTC: the stored calendar''s end date when DATES_SOURCE is CALENDAR, else the last kickoff of the week''s games',
  DATES_SOURCE         COMMENT IS 'Where START_AT and END_AT come from: CALENDAR (CFB_CORE.CALENDAR has the week; stored from 2013) or GAMES (the week''s kickoffs)',
  FIRST_GAME_AT        COMMENT IS 'First kickoff of the week in UTC: the first kickoff of the week''s games, else (a week without stored games) the stored calendar''s first game start, which CFBD sets to the start of the week. The as-of cutoff of the week before (AS_OF_POINT.CUTOFF_AT)',
  GAME_COUNT           COMMENT IS 'Number of games of the week in GAME (0 for a calendar week without stored games)',
  COMPLETED_GAME_COUNT COMMENT IS 'Number of those games that are completed',
  GAMES_FETCHED_AT     COMMENT IS 'When the season''s games were last fetched (the newest GAME.FETCHED_AT of the season, UTC); NULL for a season without games',
  IS_DONE              COMMENT IS 'TRUE when the week has games and all of them are completed, or when the season''s games were last fetched at least one day after END_AT (so a postponed or cancelled game does not keep a past week open). A week whose Thursday game is played while its Saturday games are not stays open'
) AS
WITH calendar AS (
  SELECT c."SEASON", c."SEASON_TYPE", c."WEEK", c."START_DATE", c."END_DATE", c."FIRST_GAME_START"
    FROM {CORE_SCHEMA}."CALENDAR" c
   WHERE c."SEASON_TYPE" IN ('regular', 'postseason')
 QUALIFY ROW_NUMBER() OVER (
           PARTITION BY c."SEASON", c."SEASON_TYPE", c."WEEK"
           ORDER BY c."REQ_AT" DESC, c."REQ_SLICE" DESC, c."ROW_NO" DESC) = 1
),
game_rows AS (
  SELECT d."SEASON", d."SEASON_TYPE", d."WEEK", d."IS_COMPLETED", d."START_AT", d."FETCHED_AT"
    FROM (SELECT x."SEASON", x."SEASON_TYPE", x."WEEK",
                 COALESCE(x."COMPLETED", FALSE) AS "IS_COMPLETED",
                 x."START_DATE" AS "START_AT",
                 x."REQ_AT" AS "FETCHED_AT"
            FROM {CORE_SCHEMA}."GAMES" x
         QUALIFY ROW_NUMBER() OVER (
                   PARTITION BY x."SEASON", x."ID"
                   ORDER BY x."REQ_AT" DESC, x."REQ_SLICE" DESC, x."ROW_NO" DESC) = 1) d
   WHERE d."SEASON_TYPE" IN ('regular', 'postseason') AND d."WEEK" IS NOT NULL
),
facts AS (
  SELECT g."SEASON", g."SEASON_TYPE", g."WEEK",
         1 AS "IS_GAME",
         CASE WHEN g."IS_COMPLETED" THEN 1 ELSE 0 END AS "IS_COMPLETED_GAME",
         g."START_AT" AS "KICKOFF_AT",
         g."FETCHED_AT",
         CAST(NULL AS TIMESTAMP) AS "CALENDAR_START",
         CAST(NULL AS TIMESTAMP) AS "CALENDAR_END",
         CAST(NULL AS TIMESTAMP) AS "CALENDAR_FIRST_GAME",
         0 AS "IS_CALENDAR"
    FROM game_rows g
  UNION ALL
  SELECT k."SEASON", k."SEASON_TYPE", k."WEEK",
         0, 0,
         CAST(NULL AS TIMESTAMP),
         CAST(NULL AS TIMESTAMP),
         k."START_DATE",
         k."END_DATE",
         k."FIRST_GAME_START",
         1
    FROM calendar k
),
weeks AS (
  SELECT f."SEASON", f."SEASON_TYPE", f."WEEK",
         SUM(f."IS_GAME") AS "GAME_COUNT",
         SUM(f."IS_COMPLETED_GAME") AS "COMPLETED_GAME_COUNT",
         MIN(f."KICKOFF_AT") AS "FIRST_KICKOFF_AT",
         MAX(f."KICKOFF_AT") AS "LAST_KICKOFF_AT",
         MAX(f."IS_CALENDAR") AS "HAS_CALENDAR",
         MAX(f."CALENDAR_START") AS "CALENDAR_START",
         MAX(f."CALENDAR_END") AS "CALENDAR_END",
         MAX(f."CALENDAR_FIRST_GAME") AS "CALENDAR_FIRST_GAME",
         MAX(f."FETCHED_AT") AS "WEEK_FETCHED_AT"
    FROM facts f
   GROUP BY f."SEASON", f."SEASON_TYPE", f."WEEK"
),
dated AS (
  SELECT w.*,
         MAX(w."WEEK_FETCHED_AT") OVER (PARTITION BY w."SEASON") AS "GAMES_FETCHED_AT",
         CASE WHEN w."HAS_CALENDAR" = 1 THEN w."CALENDAR_START" ELSE w."FIRST_KICKOFF_AT" END
           AS "WEEK_START",
         CASE WHEN w."HAS_CALENDAR" = 1 THEN w."CALENDAR_END" ELSE w."LAST_KICKOFF_AT" END
           AS "WEEK_END"
    FROM weeks w
)
SELECT d."SEASON",
       d."SEASON_TYPE",
       d."WEEK",
       CAST(CASE d."SEASON_TYPE" WHEN 'regular' THEN d."WEEK" ELSE 100 + d."WEEK" END AS DECIMAL(3,0)),
       d."WEEK_START",
       d."WEEK_END",
       CASE WHEN d."HAS_CALENDAR" = 1 THEN 'CALENDAR' ELSE 'GAMES' END,
       COALESCE(d."FIRST_KICKOFF_AT", d."CALENDAR_FIRST_GAME"),
       CAST(d."GAME_COUNT" AS DECIMAL(9,0)),
       CAST(d."COMPLETED_GAME_COUNT" AS DECIMAL(9,0)),
       d."GAMES_FETCHED_AT",
       CASE WHEN d."GAME_COUNT" > 0 AND d."COMPLETED_GAME_COUNT" = d."GAME_COUNT" THEN TRUE
            WHEN d."GAMES_FETCHED_AT" >= ADD_DAYS(d."WEEK_END", 1) THEN TRUE
            ELSE FALSE END
  FROM dated d
COMMENT IS 'One row per season, season type and week that has games or a stored calendar week (regular and postseason only). WEEK_SEQ orders the regular season before the postseason and matches the (season, week, season type) arguments of REFRESH_WEEK. Dates come from the stored calendar when it has the week, else from the kickoffs. IS_DONE says whether the week is over, which decides the current as-of point.';
