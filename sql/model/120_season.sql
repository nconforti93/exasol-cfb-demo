-- Seasons (model/seasons-eras): every season from 1869 to the newest season with stored games, with
-- or without games, labeled with one era per kind from ERA. The newest season comes from the raw
-- CFB_CORE.GAMES (deduplication cannot change which seasons have games, and it keeps that lookup to
-- one column scan). The counts come from GAME; each game is read once and split into its two sides
-- for the FBS team count.
CREATE OR REPLACE FORCE VIEW {MODEL_SCHEMA}.SEASON (
  SEASON               COMMENT IS 'Season (the year in which it starts), 1869 to the newest season with stored games',
  DIVISION_ERA_CODE    COMMENT IS 'Division era (ERA kind DIVISION): PRE_NCAA, NCAA_UNDIVIDED, UNIVERSITY_COLLEGE, DIVISION_I_II_III, I_A_I_AA or FBS_FCS',
  POSTSEASON_ERA_CODE  COMMENT IS 'Postseason era (ERA kind POSTSEASON): NO_BOWLS, BOWLS, BOWL_COALITION, BOWL_ALLIANCE, BCS, CFP_4 or CFP_12',
  CHAMPION_ERA_CODE    COMMENT IS 'How the national champion was decided (ERA kind CHAMPION): SELECTORS, POLLS, BCS_GAME or CFP',
  OVERTIME_ERA_CODE    COMMENT IS 'Overtime era (ERA kind OVERTIME, FBS level): TIES or OVERTIME',
  CFP_TEAMS            COMMENT IS 'Number of teams in the College Football Playoff: 0 before 2014, 4 in 2014-2023, 12 from 2024. The seeding rules of the 12-team playoff (byes for the top conference champions in 2024, straight seeding from 2025) are Phase 4''s selection configuration',
  TIES_POSSIBLE        COMMENT IS 'TRUE when a game could end in a tie (the TIES era, before 1996). It describes the FBS level: lower divisions adopted overtime in 1981',
  HAS_GAMES            COMMENT IS 'TRUE when the season has stored games; FALSE for a gap such as 1871',
  GAME_COUNT           COMMENT IS 'Number of games of the season in GAME (every division, every season type)',
  COMPLETED_GAME_COUNT COMMENT IS 'Number of those games that are completed',
  FBS_TEAM_COUNT       COMMENT IS 'Number of teams classified fbs on the season''s games (GAME.HOME_CLASSIFICATION, GAME.AWAY_CLASSIFICATION)',
  FIRST_GAME_AT        COMMENT IS 'First kickoff of the season in UTC; NULL without games',
  LAST_GAME_AT         COMMENT IS 'Last kickoff of the season in UTC; NULL without games',
  IS_CURRENT_SEASON    COMMENT IS 'TRUE for the newest season with stored games only'
) AS
WITH newest AS (
  SELECT MAX(r."SEASON") AS "SEASON"
    FROM {CORE_SCHEMA}."GAMES" r
),
seasons AS (
  SELECT CAST(y."YEAR_NUMBER" AS DECIMAL(4,0)) AS "SEASON",
         CASE WHEN y."YEAR_NUMBER" = n."SEASON" THEN TRUE ELSE FALSE END AS "IS_NEWEST"
    FROM (VALUES BETWEEN 1869 AND 2999) y ("YEAR_NUMBER")
   CROSS JOIN newest n
   WHERE y."YEAR_NUMBER" <= n."SEASON"
),
counts AS (
  SELECT g."SEASON",
         COUNT(DISTINCT g."GAME_ID") AS "GAME_COUNT",
         COUNT(DISTINCT CASE WHEN g."IS_COMPLETED" THEN g."GAME_ID" END) AS "COMPLETED_GAME_COUNT",
         COUNT(DISTINCT CASE WHEN x."SIDE" = 1 AND g."HOME_CLASSIFICATION" = 'fbs'
                             THEN g."HOME_TEAM_ID"
                             WHEN x."SIDE" = 2 AND g."AWAY_CLASSIFICATION" = 'fbs'
                             THEN g."AWAY_TEAM_ID" END) AS "FBS_TEAM_COUNT",
         MIN(g."START_AT") AS "FIRST_GAME_AT",
         MAX(g."START_AT") AS "LAST_GAME_AT"
    FROM {MODEL_SCHEMA}."GAME" g
   CROSS JOIN (VALUES (1), (2)) x ("SIDE")
   GROUP BY g."SEASON"
)
SELECT s."SEASON",
       dv."ERA_CODE",
       ps."ERA_CODE",
       ch."ERA_CODE",
       ot."ERA_CODE",
       ps."CFP_TEAMS",
       ot."TIES_POSSIBLE",
       CASE WHEN c."GAME_COUNT" > 0 THEN TRUE ELSE FALSE END,
       COALESCE(c."GAME_COUNT", 0),
       COALESCE(c."COMPLETED_GAME_COUNT", 0),
       COALESCE(c."FBS_TEAM_COUNT", 0),
       c."FIRST_GAME_AT",
       c."LAST_GAME_AT",
       s."IS_NEWEST"
  FROM seasons s
  LEFT JOIN counts c
    ON c."SEASON" = s."SEASON"
  LEFT JOIN {MODEL_SCHEMA}."ERA" dv
    ON dv."ERA_KIND" = 'DIVISION'
   AND s."SEASON" BETWEEN dv."FIRST_SEASON" AND COALESCE(dv."LAST_SEASON", s."SEASON")
  LEFT JOIN {MODEL_SCHEMA}."ERA" ps
    ON ps."ERA_KIND" = 'POSTSEASON'
   AND s."SEASON" BETWEEN ps."FIRST_SEASON" AND COALESCE(ps."LAST_SEASON", s."SEASON")
  LEFT JOIN {MODEL_SCHEMA}."ERA" ch
    ON ch."ERA_KIND" = 'CHAMPION'
   AND s."SEASON" BETWEEN ch."FIRST_SEASON" AND COALESCE(ch."LAST_SEASON", s."SEASON")
  LEFT JOIN {MODEL_SCHEMA}."ERA" ot
    ON ot."ERA_KIND" = 'OVERTIME'
   AND s."SEASON" BETWEEN ot."FIRST_SEASON" AND COALESCE(ot."LAST_SEASON", s."SEASON")
COMMENT IS 'One row per season from 1869 to the newest season with stored games, including seasons without games (a gap such as 1871 has HAS_GAMES FALSE). Each row carries one era per kind from ERA (division structure, postseason format, champion selection, overtime), CFP_TEAMS and TIES_POSSIBLE, the season''s game and FBS-team counts, its first and last kickoff, and IS_CURRENT_SEASON for the newest season.';
