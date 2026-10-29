CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.TEAM_TRAVEL (
  TEAM_ID COMMENT IS 'CFBD team id whose trip this row describes',
  GAME_ID COMMENT IS 'CFBD game id',
  SEASON COMMENT IS 'Season of the game',
  SEASON_TYPE COMMENT IS 'Season type of the game',
  WEEK COMMENT IS 'CFBD week label',
  WEEK_SEQ COMMENT IS 'Week sequence within the season',
  START_AT COMMENT IS 'Kickoff in UTC',
  OPPONENT_ID COMMENT IS 'Opponent CFBD team id',
  SITE COMMENT IS 'HOME, AWAY or NEUTRAL from this team''s side',
  HAS_RESULT COMMENT IS 'TRUE when both scores are stored',
  VENUE_ID COMMENT IS 'CFBD venue id of this game',
  VENUE_NAME COMMENT IS 'Name of the game venue',
  VENUE_CITY COMMENT IS 'City of the game venue',
  VENUE_STATE COMMENT IS 'State code of the game venue',
  KM_FROM_HOME COMMENT IS 'Great-circle kilometres from today''s team home location to the game venue, rounded to 0.1 km; 0 for HOME, NULL when either location is unknown. Historic games use today''s home'
) AS
SELECT tg."TEAM_ID", tg."GAME_ID", tg."SEASON", tg."SEASON_TYPE", tg."WEEK",
       tg."WEEK_SEQ", tg."START_AT", tg."OPPONENT_ID", tg."SITE", tg."HAS_RESULT",
       g."VENUE_ID", g."VENUE_NAME", v."CITY", v."STATE_CODE",
       CAST(CASE WHEN tg."SITE" = 'HOME' THEN 0
                 ELSE {MODEL_SCHEMA}.GREAT_CIRCLE_KM(
                   t."LATITUDE", t."LONGITUDE", v."LATITUDE", v."LONGITUDE")
            END AS DECIMAL(7,1))
  FROM {MODEL_SCHEMA}."TEAM_GAME" tg
  JOIN {MODEL_SCHEMA}."GAME" g ON g."GAME_ID" = tg."GAME_ID"
  LEFT JOIN {MODEL_SCHEMA}."TEAM" t ON t."TEAM_ID" = tg."TEAM_ID"
  LEFT JOIN {MODEL_SCHEMA}."VENUE" v ON v."VENUE_ID" = g."VENUE_ID"
COMMENT IS 'One row per stored team game, with great-circle distance from the team''s current home to the game venue. Stored history as it is now; filter WEEK_SEQ for a point.';
