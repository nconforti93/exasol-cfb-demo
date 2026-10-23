-- Question: what is the all-time series between two teams, when did they last meet and when do they
--   meet next? Computed from the stored games since 1869 at zero CFBD calls (decision [18]).
-- Consumer: Phase 5 head-to-head (CFB_API views) and Phase 6 matchup page.
-- Reads: CFB_MODEL.TEAM_GAME only.
-- Parameters: replace {TEAM_A} and {TEAM_B} by the CFBD school names (TEAM_GAME.TEAM_NAME), for
--   example Michigan and Ohio State; {MODEL_SCHEMA} is the model schema (CFB_MODEL).
-- Result: one row. MEETINGS counts the completed meetings with a result; A_WINS, B_WINS and TIES
--   split them. LAST_* is the latest completed meeting with its result for team A (W, L or T) and
--   the score from A's side. NEXT_* is the earliest meeting without a result that kicks off after
--   the last completed one (NULL when none is stored). Placeholder games have no TEAM_GAME row.
WITH meetings AS (
  SELECT tg.GAME_ID, tg.SEASON, tg.WEEK, tg.START_AT, tg.HAS_RESULT, tg.GAME_RESULT,
         tg.POINTS_FOR, tg.POINTS_AGAINST
    FROM {MODEL_SCHEMA}.TEAM_GAME tg
   WHERE tg.TEAM_NAME = '{TEAM_A}'
     AND tg.OPPONENT_NAME = '{TEAM_B}'
),
played AS (
  SELECT m.* FROM meetings m WHERE m.HAS_RESULT
),
series AS (
  SELECT COUNT(*) AS MEETINGS,
         COUNT(CASE WHEN p.GAME_RESULT = 'W' THEN 1 END) AS A_WINS,
         COUNT(CASE WHEN p.GAME_RESULT = 'L' THEN 1 END) AS B_WINS,
         COUNT(CASE WHEN p.GAME_RESULT = 'T' THEN 1 END) AS TIES,
         MAX(p.START_AT) AS LAST_START_AT
    FROM played p
),
last_meeting AS (
  SELECT p.* FROM played p
  QUALIFY ROW_NUMBER() OVER (ORDER BY p.START_AT DESC, p.GAME_ID DESC) = 1
),
next_meeting AS (
  SELECT m.*
    FROM meetings m
    CROSS JOIN series s
   WHERE NOT m.HAS_RESULT
     AND (s.LAST_START_AT IS NULL OR m.START_AT > s.LAST_START_AT)
  QUALIFY ROW_NUMBER() OVER (ORDER BY m.START_AT, m.GAME_ID) = 1
)
SELECT '{TEAM_A}' AS TEAM_A,
       '{TEAM_B}' AS TEAM_B,
       s.MEETINGS,
       s.A_WINS,
       s.B_WINS,
       s.TIES,
       l.GAME_ID AS LAST_GAME_ID,
       l.SEASON AS LAST_SEASON,
       l.WEEK AS LAST_WEEK,
       l.GAME_RESULT AS LAST_RESULT_FOR_A,
       l.POINTS_FOR AS LAST_POINTS_A,
       l.POINTS_AGAINST AS LAST_POINTS_B,
       n.GAME_ID AS NEXT_GAME_ID,
       n.SEASON AS NEXT_SEASON,
       n.WEEK AS NEXT_WEEK,
       n.START_AT AS NEXT_START_AT
  FROM series s
  LEFT JOIN last_meeting l ON 1 = 1
  LEFT JOIN next_meeting n ON 1 = 1;
