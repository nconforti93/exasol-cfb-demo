CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.UPSETS (
  GAME_ID COMMENT IS 'CFBD game id of the underdog win',
  SEASON COMMENT IS 'Game season',
  SEASON_TYPE COMMENT IS 'Game season type',
  WEEK COMMENT IS 'CFBD week label',
  WEEK_SEQ COMMENT IS 'Game week sequence',
  START_AT COMMENT IS 'Kickoff in UTC',
  WINNER_TEAM_ID COMMENT IS 'CFBD id of the winning underdog',
  WINNER_SCHOOL COMMENT IS 'School of the winning underdog',
  LOSER_TEAM_ID COMMENT IS 'CFBD id of the losing favorite',
  LOSER_SCHOOL COMMENT IS 'School of the losing favorite',
  WINNER_POINTS COMMENT IS 'Points scored by the winning underdog',
  LOSER_POINTS COMMENT IS 'Points scored by the losing favorite',
  WINNER_SITE COMMENT IS 'HOME, AWAY or NEUTRAL for the winning underdog',
  PROB_SOURCE COMMENT IS 'LINE before the simulation engine; ELO is added by the engine slice',
  WINNER_PREGAME_WIN_PROB COMMENT IS 'Stored pregame win probability of the winning underdog',
  UPSET_MAGNITUDE COMMENT IS 'One minus the winning underdog''s pregame probability',
  WEEK_RANK COMMENT IS 'Rank by upset magnitude within the season and week sequence',
  SEASON_RANK COMMENT IS 'Rank by upset magnitude within the season',
  ALL_TIME_RANK COMMENT IS 'Rank by upset magnitude since ALL_TIME_FROM_SEASON',
  ALL_TIME_TIED COMMENT IS 'TRUE when another all-time upset has the same magnitude',
  ALL_TIME_FROM_SEASON COMMENT IS '2013 before the engine, the first season with stored lines',
  LINE_ERA_RANK COMMENT IS 'Rank by upset magnitude among games with stored lines',
  LINE_ERA_TIED COMMENT IS 'TRUE when another line-era upset has the same magnitude',
  MODEL_WINNER_WIN_PROB COMMENT IS 'Frozen model probability of the winning underdog; NULL before the engine'
) AS
WITH underdogs AS (
  SELECT g."GAME_ID", g."SEASON", g."SEASON_TYPE", g."WEEK", g."WEEK_SEQ",
         g."START_AT", g."WINNER_TEAM_ID", g."LOSER_TEAM_ID",
         CASE WHEN g."WINNER_TEAM_ID" = g."HOME_TEAM_ID" THEN g."HOME_POINTS"
              ELSE g."AWAY_POINTS" END AS "WINNER_POINTS",
         CASE WHEN g."WINNER_TEAM_ID" = g."HOME_TEAM_ID" THEN g."AWAY_POINTS"
              ELSE g."HOME_POINTS" END AS "LOSER_POINTS",
         CASE WHEN g."IS_NEUTRAL_SITE" THEN 'NEUTRAL'
              WHEN g."WINNER_TEAM_ID" = g."HOME_TEAM_ID" THEN 'HOME'
              ELSE 'AWAY' END AS "WINNER_SITE",
         CAST(CASE WHEN g."WINNER_TEAM_ID" = g."HOME_TEAM_ID"
                     THEN g."HOME_PREGAME_WIN_PROB"
                   ELSE 1 - g."HOME_PREGAME_WIN_PROB" END AS DECIMAL(5,4))
           AS "WINNER_PROB"
    FROM {MODEL_SCHEMA}."GAME" g
   WHERE g."HAS_RESULT" AND g."INVOLVES_FBS" AND NOT g."IS_TIE"
     AND g."HOME_PREGAME_WIN_PROB" IS NOT NULL
), ranked AS (
  SELECT u.*, 1 - u."WINNER_PROB" AS "MAGNITUDE",
         RANK() OVER (PARTITION BY u."SEASON", u."WEEK_SEQ"
                      ORDER BY u."WINNER_PROB") AS "WEEK_RANK",
         RANK() OVER (PARTITION BY u."SEASON" ORDER BY u."WINNER_PROB")
           AS "SEASON_RANK",
         RANK() OVER (ORDER BY u."WINNER_PROB") AS "ALL_TIME_RANK",
         COUNT(*) OVER (PARTITION BY u."WINNER_PROB") > 1 AS "IS_TIED"
    FROM underdogs u WHERE u."WINNER_PROB" < 0.5
)
SELECT r."GAME_ID", r."SEASON", r."SEASON_TYPE", r."WEEK", r."WEEK_SEQ",
       r."START_AT", r."WINNER_TEAM_ID", wt."SCHOOL", r."LOSER_TEAM_ID",
       lt."SCHOOL", r."WINNER_POINTS", r."LOSER_POINTS", r."WINNER_SITE",
       'LINE', r."WINNER_PROB", r."MAGNITUDE", r."WEEK_RANK", r."SEASON_RANK",
       r."ALL_TIME_RANK", r."IS_TIED", 2013, r."ALL_TIME_RANK", r."IS_TIED",
       CAST(NULL AS DECIMAL(5,4))
  FROM ranked r
  LEFT JOIN {MODEL_SCHEMA}."TEAM" wt ON wt."TEAM_ID" = r."WINNER_TEAM_ID"
  LEFT JOIN {MODEL_SCHEMA}."TEAM" lt ON lt."TEAM_ID" = r."LOSER_TEAM_ID"
COMMENT IS 'One row per completed FBS-involving underdog win with a stored line, ranked within its week, season and line era since 2013. Elo-era upsets join in the engine slice.';
