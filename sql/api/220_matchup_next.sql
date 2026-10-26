CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.MATCHUP_NEXT (
  TEAM_ID COMMENT IS 'CFBD id of the team whose side this row shows',
  OPPONENT_TEAM_ID COMMENT IS 'CFBD id of the opposing team',
  GAME_ID COMMENT IS 'CFBD game id of their earliest remaining meeting this season',
  SEASON COMMENT IS 'Game season',
  SEASON_TYPE COMMENT IS 'Game season type',
  WEEK COMMENT IS 'CFBD week label',
  WEEK_SEQ COMMENT IS 'Game week sequence',
  START_AT COMMENT IS 'Kickoff in UTC',
  SITE COMMENT IS 'HOME, AWAY or NEUTRAL from the named team''s side',
  TEAM_SPREAD COMMENT IS 'Stored pregame spread from the named team''s side',
  TEAM_PREGAME_WIN_PROB COMMENT IS 'Stored pregame win probability from the named team''s side',
  PREDICTION_KIND COMMENT IS 'FROZEN or CURRENT once a simulation run exists; NULL before the engine',
  TEAM_WIN_PROB COMMENT IS 'Model win probability from the named team''s side; NULL before the engine',
  PRED_TEAM_POINTS COMMENT IS 'Predicted points of the named team; NULL before the engine',
  PRED_OPPONENT_POINTS COMMENT IS 'Predicted points of the opponent; NULL before the engine',
  STAKES_RUN_ID COMMENT IS 'Run of the remaining-game stakes; NULL before the engine',
  TEAM_P_IN_CFP_IF_WIN COMMENT IS 'Named team''s playoff chance if it wins; NULL before the engine',
  TEAM_P_IN_CFP_IF_LOSE COMMENT IS 'Named team''s playoff chance if it loses; NULL before the engine',
  OPPONENT_P_IN_CFP_IF_WIN COMMENT IS 'Opponent''s playoff chance if it wins; NULL before the engine',
  OPPONENT_P_IN_CFP_IF_LOSE COMMENT IS 'Opponent''s playoff chance if it loses; NULL before the engine'
) AS
SELECT tg."TEAM_ID", tg."OPPONENT_ID", tg."GAME_ID", tg."SEASON",
       tg."SEASON_TYPE", tg."WEEK", tg."WEEK_SEQ", tg."START_AT", tg."SITE",
       tg."TEAM_SPREAD", tg."TEAM_PREGAME_WIN_PROB",
       CAST(NULL AS VARCHAR(16) ASCII), CAST(NULL AS DECIMAL(5,4)),
       CAST(NULL AS DECIMAL(9,1)), CAST(NULL AS DECIMAL(9,1)),
       CAST(NULL AS DECIMAL(18,0)), CAST(NULL AS DECIMAL(5,4)),
       CAST(NULL AS DECIMAL(5,4)), CAST(NULL AS DECIMAL(5,4)),
       CAST(NULL AS DECIMAL(5,4))
  FROM {MODEL_SCHEMA}."TEAM_GAME" tg
  JOIN {MODEL_SCHEMA}."GAME" g ON g."GAME_ID" = tg."GAME_ID"
  JOIN {MODEL_SCHEMA}."CURRENT_AS_OF" p ON p."AS_OF_SEASON" = tg."SEASON"
 WHERE NOT g."IS_COMPLETED" AND tg."OPPONENT_ID" IS NOT NULL
 QUALIFY ROW_NUMBER() OVER (
   PARTITION BY tg."TEAM_ID", tg."OPPONENT_ID"
   ORDER BY tg."START_AT", tg."GAME_ID") = 1
COMMENT IS 'One row per ordered pair with a remaining game in the current season: their earliest scheduled meeting. Engine predictions and stakes are NULL until its serving slice.';
