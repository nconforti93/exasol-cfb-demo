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
  PROB_SOURCE COMMENT IS 'LINE when a stored line exists, else fitted ELO',
  WINNER_PREGAME_WIN_PROB COMMENT IS 'Line or fitted Elo pregame win probability of the winning underdog',
  UPSET_MAGNITUDE COMMENT IS 'One minus the winning underdog''s pregame probability',
  WEEK_RANK COMMENT IS 'Rank by upset magnitude within the season and week sequence',
  SEASON_RANK COMMENT IS 'Rank by upset magnitude within the season',
  ALL_TIME_RANK COMMENT IS 'Rank by upset magnitude since ALL_TIME_FROM_SEASON',
  ALL_TIME_TIED COMMENT IS 'TRUE when another all-time upset has the same magnitude',
  ALL_TIME_FROM_SEASON COMMENT IS 'First season included in the all-time ranking, currently 1978',
  LINE_ERA_RANK COMMENT IS 'Rank by upset magnitude among games with stored lines',
  LINE_ERA_TIED COMMENT IS 'TRUE when another line-era upset has the same magnitude',
  MODEL_WINNER_WIN_PROB COMMENT IS 'Frozen model probability of the winning underdog, if predicted'
) AS
WITH settings AS (
  SELECT CAST("SETTING_NUMBER" AS DECIMAL(4,0)) AS "FROM_SEASON"
    FROM {API_SCHEMA}.ANALYSIS_SETTINGS
   WHERE "SETTING_NAME" = 'ALL_TIME_FROM_SEASON'
), underdogs AS (
  SELECT p."GAME_ID", p."SEASON", p."SEASON_TYPE", p."WEEK", p."WEEK_SEQ",
         g."START_AT", p."WINNER_TEAM_ID", p."LOSER_TEAM_ID",
         CASE WHEN p."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN g."HOME_POINTS"
              ELSE g."AWAY_POINTS" END AS "WINNER_POINTS",
         CASE WHEN p."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN g."AWAY_POINTS"
              ELSE g."HOME_POINTS" END AS "LOSER_POINTS",
         CASE WHEN g."IS_NEUTRAL_SITE" THEN 'NEUTRAL'
              WHEN p."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN 'HOME'
              ELSE 'AWAY' END AS "WINNER_SITE",
         p."PROB_SOURCE", p."WINNER_PREGAME_WIN_PROB" AS "WINNER_PROB",
         p."UPSET_MAGNITUDE" AS "MAGNITUDE",
         CASE WHEN p."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN f."HOME_WIN_PROB"
              WHEN f."GAME_ID" IS NOT NULL THEN 1.0 - f."HOME_WIN_PROB" END
           AS "MODEL_WINNER_PROB"
    FROM {SIM_SCHEMA}."GAME_WIN_PROB" p
    JOIN {MODEL_SCHEMA}."GAME" g ON g."GAME_ID" = p."GAME_ID"
    LEFT JOIN {SIM_SCHEMA}."PREDICTION" f ON f."GAME_ID" = p."GAME_ID"
   WHERE p."WINNER_PREGAME_WIN_PROB" < 0.5
), ranked AS (
  SELECT u.*,
         RANK() OVER (PARTITION BY u."SEASON", u."WEEK_SEQ"
                      ORDER BY u."MAGNITUDE" DESC) AS "WEEK_RANK",
         RANK() OVER (PARTITION BY u."SEASON" ORDER BY u."MAGNITUDE" DESC)
           AS "SEASON_RANK"
    FROM underdogs u
), all_time AS (
  SELECT r."GAME_ID", RANK() OVER (ORDER BY r."MAGNITUDE" DESC) AS "ALL_TIME_RANK",
         COUNT(*) OVER (PARTITION BY r."MAGNITUDE") > 1 AS "ALL_TIME_TIED"
    FROM ranked r CROSS JOIN settings s
   WHERE r."SEASON" >= s."FROM_SEASON"
), line_era AS (
  SELECT r."GAME_ID", RANK() OVER (ORDER BY r."MAGNITUDE" DESC) AS "LINE_ERA_RANK",
         COUNT(*) OVER (PARTITION BY r."MAGNITUDE") > 1 AS "LINE_ERA_TIED"
    FROM ranked r WHERE r."PROB_SOURCE" = 'LINE'
)
SELECT r."GAME_ID", r."SEASON", r."SEASON_TYPE", r."WEEK", r."WEEK_SEQ",
       r."START_AT", r."WINNER_TEAM_ID", wt."SCHOOL", r."LOSER_TEAM_ID",
       lt."SCHOOL", r."WINNER_POINTS", r."LOSER_POINTS", r."WINNER_SITE",
       r."PROB_SOURCE", r."WINNER_PROB", r."MAGNITUDE", r."WEEK_RANK",
       r."SEASON_RANK", a."ALL_TIME_RANK", a."ALL_TIME_TIED", s."FROM_SEASON",
       l."LINE_ERA_RANK", l."LINE_ERA_TIED", r."MODEL_WINNER_PROB"
  FROM ranked r
  CROSS JOIN settings s
  LEFT JOIN all_time a ON a."GAME_ID" = r."GAME_ID"
  LEFT JOIN line_era l ON l."GAME_ID" = r."GAME_ID"
  LEFT JOIN {MODEL_SCHEMA}."TEAM" wt ON wt."TEAM_ID" = r."WINNER_TEAM_ID"
  LEFT JOIN {MODEL_SCHEMA}."TEAM" lt ON lt."TEAM_ID" = r."LOSER_TEAM_ID"
COMMENT IS 'One row per completed FBS-involving underdog win with a line or Elo probability, ranked by magnitude within its week, season, line era and the all-time window.';
