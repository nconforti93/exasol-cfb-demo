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
  PREDICTION_KIND COMMENT IS 'FROZEN when predicted at the previous point, else CURRENT from the shown run, else NULL',
  TEAM_WIN_PROB COMMENT IS 'Frozen or current model win probability from the named team''s side',
  PRED_TEAM_POINTS COMMENT IS 'Frozen predicted points of the named team, if available',
  PRED_OPPONENT_POINTS COMMENT IS 'Frozen predicted points of the opponent, if available',
  STAKES_RUN_ID COMMENT IS 'Shown run of the remaining-game stakes, if the game is in its snapshot',
  TEAM_P_IN_CFP_IF_WIN COMMENT IS 'Named team''s playoff chance if it wins, when guide counts exist',
  TEAM_P_IN_CFP_IF_LOSE COMMENT IS 'Named team''s playoff chance if it loses, when guide counts exist',
  OPPONENT_P_IN_CFP_IF_WIN COMMENT IS 'Opponent''s playoff chance if it wins, when guide counts exist',
  OPPONENT_P_IN_CFP_IF_LOSE COMMENT IS 'Opponent''s playoff chance if it loses, when guide counts exist'
) AS
SELECT tg."TEAM_ID", tg."OPPONENT_ID", tg."GAME_ID", tg."SEASON",
       tg."SEASON_TYPE", tg."WEEK", tg."WEEK_SEQ", tg."START_AT", tg."SITE",
       tg."TEAM_SPREAD", tg."TEAM_PREGAME_WIN_PROB",
       CASE WHEN f."GAME_ID" IS NOT NULL THEN 'FROZEN'
            WHEN sg."GAME_ID" IS NOT NULL THEN 'CURRENT' END,
       CASE WHEN tg."TEAM_ID" = g."HOME_TEAM_ID"
              THEN COALESCE(f."HOME_WIN_PROB", sg."HOME_WIN_PROB")
            ELSE 1.0 - COALESCE(f."HOME_WIN_PROB", sg."HOME_WIN_PROB") END,
       CASE WHEN tg."TEAM_ID" = g."HOME_TEAM_ID" THEN f."HOME_POINTS_PRED"
            ELSE f."AWAY_POINTS_PRED" END,
       CASE WHEN tg."TEAM_ID" = g."HOME_TEAM_ID" THEN f."AWAY_POINTS_PRED"
            ELSE f."HOME_POINTS_PRED" END,
       CASE WHEN sg."GAME_ID" IS NOT NULL THEN cr."RUN_ID" END,
       CASE WHEN tg."TEAM_ID" = g."HOME_TEAM_ID" THEN own."P_IF_HOME_WIN"
            ELSE own."P_IF_AWAY_WIN" END,
       CASE WHEN tg."TEAM_ID" = g."HOME_TEAM_ID" THEN own."P_IF_AWAY_WIN"
            ELSE own."P_IF_HOME_WIN" END,
       CASE WHEN tg."TEAM_ID" = g."HOME_TEAM_ID" THEN opp."P_IF_AWAY_WIN"
            ELSE opp."P_IF_HOME_WIN" END,
       CASE WHEN tg."TEAM_ID" = g."HOME_TEAM_ID" THEN opp."P_IF_HOME_WIN"
            ELSE opp."P_IF_AWAY_WIN" END
  FROM {MODEL_SCHEMA}."TEAM_GAME" tg
  JOIN {MODEL_SCHEMA}."GAME" g ON g."GAME_ID" = tg."GAME_ID"
  JOIN {MODEL_SCHEMA}."CURRENT_AS_OF" p ON p."AS_OF_SEASON" = tg."SEASON"
  LEFT JOIN {SIM_SCHEMA}."PREDICTION" f ON f."GAME_ID" = tg."GAME_ID"
  LEFT JOIN {SIM_SCHEMA}."CURRENT_RUN" cr ON cr."AS_OF_SEASON" = tg."SEASON"
  LEFT JOIN {SIM_SCHEMA}."SIM_RUN_GAME" sg
    ON sg."RUN_ID" = cr."RUN_ID" AND sg."GAME_ID" = tg."GAME_ID"
   AND sg."GAME_ROLE" = 'REMAINING'
  LEFT JOIN {SIM_SCHEMA}."ROOTING_GUIDE" own
    ON own."RUN_ID" = cr."RUN_ID" AND own."GAME_ID" = sg."GAME_ID"
   AND own."TEAM_ID" = tg."TEAM_ID"
  LEFT JOIN {SIM_SCHEMA}."ROOTING_GUIDE" opp
    ON opp."RUN_ID" = cr."RUN_ID" AND opp."GAME_ID" = sg."GAME_ID"
   AND opp."TEAM_ID" = tg."OPPONENT_ID"
 WHERE NOT g."IS_COMPLETED" AND tg."OPPONENT_ID" IS NOT NULL
 QUALIFY ROW_NUMBER() OVER (
   PARTITION BY tg."TEAM_ID", tg."OPPONENT_ID"
   ORDER BY tg."START_AT", tg."GAME_ID") = 1
COMMENT IS 'One row per ordered pair with a remaining game in the current season: their earliest scheduled meeting, frozen or current model prediction, and conditional playoff stakes.';
