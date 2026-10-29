CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.WIN_STREAKS (
  AS_OF_SEASON COMMENT IS 'Season of the as-of point',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the as-of point',
  TEAM_ID COMMENT IS 'FBS team id in the point season',
  SCHOOL COMMENT IS 'School in the point season',
  STREAK_KIND COMMENT IS 'W, L or T: result of the latest counted game',
  STREAK_LENGTH COMMENT IS 'Number of consecutive games with that result, across seasons',
  SINCE_GAME_ID COMMENT IS 'First game id of the streak',
  SINCE_SEASON COMMENT IS 'Season of the first game of the streak',
  SINCE_WEEK_SEQ COMMENT IS 'Week sequence of the first game of the streak',
  LAST_GAME_ID COMMENT IS 'Latest game id of the streak',
  WIN_STREAK_RANK COMMENT IS 'Rank by length among winning streaks at the point; NULL for a loss or tie streak'
) AS
WITH played AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", s."TEAM_ID", s."SCHOOL",
         tg."GAME_ID", tg."SEASON", tg."WEEK_SEQ", tg."START_AT", tg."GAME_RESULT",
         ROW_NUMBER() OVER (
           PARTITION BY p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", s."TEAM_ID"
           ORDER BY tg."SEASON" DESC, tg."WEEK_SEQ" DESC,
                    tg."START_AT" DESC, tg."GAME_ID" DESC) AS "REVERSE_POS",
         FIRST_VALUE(tg."GAME_RESULT") OVER (
           PARTITION BY p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", s."TEAM_ID"
           ORDER BY tg."SEASON" DESC, tg."WEEK_SEQ" DESC,
                    tg."START_AT" DESC, tg."GAME_ID" DESC) AS "LATEST_KIND"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" p
    JOIN {MODEL_SCHEMA}."TEAM_SEASON" s
      ON s."SEASON" = p."AS_OF_SEASON" AND s."CLASSIFICATION" = 'fbs'
    JOIN {MODEL_SCHEMA}."TEAM_GAME" tg
      ON tg."TEAM_ID" = s."TEAM_ID" AND tg."HAS_RESULT"
     AND (tg."SEASON" < p."AS_OF_SEASON" OR
          (tg."SEASON" = p."AS_OF_SEASON" AND tg."WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"))
), marked AS (
  SELECT x.*,
         SUM(CASE WHEN x."GAME_RESULT" = x."LATEST_KIND" THEN 0 ELSE 1 END) OVER (
           PARTITION BY x."AS_OF_SEASON", x."AS_OF_WEEK_SEQ", x."TEAM_ID"
           ORDER BY x."REVERSE_POS" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
           AS "BREAKS"
    FROM played x
), streak AS (
  SELECT x.*,
         ROW_NUMBER() OVER (
           PARTITION BY x."AS_OF_SEASON", x."AS_OF_WEEK_SEQ", x."TEAM_ID"
           ORDER BY x."REVERSE_POS" DESC) AS "FORWARD_POS"
    FROM marked x WHERE x."BREAKS" = 0
), totals AS (
  SELECT "AS_OF_SEASON", "AS_OF_WEEK_SEQ", "TEAM_ID", MAX("SCHOOL") AS "SCHOOL",
         MAX("LATEST_KIND") AS "STREAK_KIND", COUNT(*) AS "STREAK_LENGTH",
         MAX(CASE WHEN "FORWARD_POS" = 1 THEN "GAME_ID" END) AS "SINCE_GAME_ID",
         MAX(CASE WHEN "FORWARD_POS" = 1 THEN "SEASON" END) AS "SINCE_SEASON",
         MAX(CASE WHEN "FORWARD_POS" = 1 THEN "WEEK_SEQ" END) AS "SINCE_WEEK_SEQ",
         MAX(CASE WHEN "REVERSE_POS" = 1 THEN "GAME_ID" END) AS "LAST_GAME_ID"
    FROM streak GROUP BY "AS_OF_SEASON", "AS_OF_WEEK_SEQ", "TEAM_ID"
)
SELECT x.*, CASE WHEN x."STREAK_KIND" = 'W' THEN
         RANK() OVER (PARTITION BY x."AS_OF_SEASON", x."AS_OF_WEEK_SEQ", x."STREAK_KIND"
                      ORDER BY x."STREAK_LENGTH" DESC) END AS "WIN_STREAK_RANK"
  FROM totals x
COMMENT IS 'One row per point and FBS team with a counted game by that point. Consecutive equal results may span seasons.';
