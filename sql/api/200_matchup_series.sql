CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.MATCHUP_SERIES (
  TEAM_ID COMMENT IS 'CFBD id of the team whose side this series shows',
  OPPONENT_TEAM_ID COMMENT IS 'CFBD id of the opposing team',
  SCHOOL COMMENT IS 'School name of the named team',
  OPPONENT_SCHOOL COMMENT IS 'School name of the opponent',
  MEETINGS COMMENT IS 'Completed meetings of the ordered pair',
  WINS COMMENT IS 'Wins by the named team',
  LOSSES COMMENT IS 'Losses by the named team',
  TIES COMMENT IS 'Ties',
  POINTS_FOR COMMENT IS 'Points scored by the named team in all meetings',
  POINTS_AGAINST COMMENT IS 'Points scored by the opponent in all meetings',
  FIRST_SEASON COMMENT IS 'Season of the first stored meeting',
  LAST_SEASON COMMENT IS 'Season of the latest stored meeting',
  LAST_GAME_ID COMMENT IS 'Game id of the latest meeting by season, week, kickoff and id',
  STREAK_KIND COMMENT IS 'W, L or T in the latest meeting',
  STREAK_LENGTH COMMENT IS 'Consecutive latest meetings with that result',
  WINS_HOME COMMENT IS 'Wins at the named team''s home site',
  WINS_AWAY COMMENT IS 'Wins at the opponent''s home site',
  WINS_NEUTRAL COMMENT IS 'Wins at a neutral site',
  BIGGEST_WIN_MARGIN COMMENT IS 'Largest positive winning margin; NULL without a win',
  BIGGEST_WIN_SEASON COMMENT IS 'Season of the earliest game at the largest winning margin',
  BIGGEST_LOSS_MARGIN COMMENT IS 'Largest positive losing margin; NULL without a loss',
  BIGGEST_LOSS_SEASON COMMENT IS 'Season of the earliest game at the largest losing margin'
) AS
WITH ordered AS (
  SELECT tg.*,
         ROW_NUMBER() OVER (
           PARTITION BY tg."TEAM_ID", tg."OPPONENT_ID"
           ORDER BY tg."SEASON" DESC, tg."WEEK_SEQ" DESC,
                    tg."START_AT" DESC, tg."GAME_ID" DESC) AS "REVERSE_POS",
         FIRST_VALUE(tg."GAME_RESULT") OVER (
           PARTITION BY tg."TEAM_ID", tg."OPPONENT_ID"
           ORDER BY tg."SEASON" DESC, tg."WEEK_SEQ" DESC,
                    tg."START_AT" DESC, tg."GAME_ID" DESC) AS "LATEST_KIND",
         ROW_NUMBER() OVER (
           PARTITION BY tg."TEAM_ID", tg."OPPONENT_ID", tg."GAME_RESULT"
           ORDER BY CASE WHEN tg."GAME_RESULT" = 'W' THEN tg."MARGIN"
                         WHEN tg."GAME_RESULT" = 'L' THEN -tg."MARGIN" END DESC,
                    tg."SEASON", tg."WEEK_SEQ", tg."START_AT", tg."GAME_ID")
           AS "MARGIN_POS"
    FROM {MODEL_SCHEMA}."TEAM_GAME" tg
   WHERE tg."HAS_RESULT" AND tg."OPPONENT_ID" IS NOT NULL
), runs AS (
  SELECT o.*,
         SUM(CASE WHEN o."GAME_RESULT" = o."LATEST_KIND" THEN 0 ELSE 1 END) OVER (
           PARTITION BY o."TEAM_ID", o."OPPONENT_ID"
           ORDER BY o."REVERSE_POS" ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
           AS "BREAKS"
    FROM ordered o
), totals AS (
  SELECT r."TEAM_ID", r."OPPONENT_ID", COUNT(*) AS "MEETINGS",
         SUM(CASE WHEN r."GAME_RESULT" = 'W' THEN 1 ELSE 0 END) AS "WINS",
         SUM(CASE WHEN r."GAME_RESULT" = 'L' THEN 1 ELSE 0 END) AS "LOSSES",
         SUM(CASE WHEN r."GAME_RESULT" = 'T' THEN 1 ELSE 0 END) AS "TIES",
         SUM(r."POINTS_FOR") AS "POINTS_FOR",
         SUM(r."POINTS_AGAINST") AS "POINTS_AGAINST",
         MIN(r."SEASON") AS "FIRST_SEASON", MAX(r."SEASON") AS "LAST_SEASON",
         MAX(CASE WHEN r."REVERSE_POS" = 1 THEN r."GAME_ID" END) AS "LAST_GAME_ID",
         MAX(r."LATEST_KIND") AS "STREAK_KIND",
         SUM(CASE WHEN r."BREAKS" = 0 THEN 1 ELSE 0 END) AS "STREAK_LENGTH",
         SUM(CASE WHEN r."GAME_RESULT" = 'W' AND r."SITE" = 'HOME' THEN 1 ELSE 0 END)
           AS "WINS_HOME",
         SUM(CASE WHEN r."GAME_RESULT" = 'W' AND r."SITE" = 'AWAY' THEN 1 ELSE 0 END)
           AS "WINS_AWAY",
         SUM(CASE WHEN r."GAME_RESULT" = 'W' AND r."SITE" = 'NEUTRAL' THEN 1 ELSE 0 END)
           AS "WINS_NEUTRAL",
         MAX(CASE WHEN r."GAME_RESULT" = 'W' THEN r."MARGIN" END) AS "BIGGEST_WIN_MARGIN",
         MAX(CASE WHEN r."GAME_RESULT" = 'W' AND r."MARGIN_POS" = 1 THEN r."SEASON" END)
           AS "BIGGEST_WIN_SEASON",
         MAX(CASE WHEN r."GAME_RESULT" = 'L' THEN -r."MARGIN" END) AS "BIGGEST_LOSS_MARGIN",
         MAX(CASE WHEN r."GAME_RESULT" = 'L' AND r."MARGIN_POS" = 1 THEN r."SEASON" END)
           AS "BIGGEST_LOSS_SEASON"
    FROM runs r GROUP BY r."TEAM_ID", r."OPPONENT_ID"
)
SELECT x."TEAM_ID", x."OPPONENT_ID", t."SCHOOL", o."SCHOOL", x."MEETINGS",
       x."WINS", x."LOSSES", x."TIES", x."POINTS_FOR", x."POINTS_AGAINST",
       x."FIRST_SEASON", x."LAST_SEASON", x."LAST_GAME_ID", x."STREAK_KIND",
       x."STREAK_LENGTH", x."WINS_HOME", x."WINS_AWAY", x."WINS_NEUTRAL",
       x."BIGGEST_WIN_MARGIN", x."BIGGEST_WIN_SEASON",
       x."BIGGEST_LOSS_MARGIN", x."BIGGEST_LOSS_SEASON"
  FROM totals x
  LEFT JOIN {MODEL_SCHEMA}."TEAM" t ON t."TEAM_ID" = x."TEAM_ID"
  LEFT JOIN {MODEL_SCHEMA}."TEAM" o ON o."TEAM_ID" = x."OPPONENT_ID"
COMMENT IS 'One row per ordered pair of teams with a completed stored meeting: all-time record and latest result streak from the first team''s side.';
