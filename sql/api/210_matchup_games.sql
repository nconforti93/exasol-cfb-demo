CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.MATCHUP_GAMES (
  TEAM_ID COMMENT IS 'CFBD id of the team whose side this row shows',
  OPPONENT_TEAM_ID COMMENT IS 'CFBD id of the opposing team',
  GAME_ID COMMENT IS 'CFBD game id',
  SEASON COMMENT IS 'Game season',
  SEASON_TYPE COMMENT IS 'Game season type',
  WEEK COMMENT IS 'CFBD week label',
  WEEK_SEQ COMMENT IS 'Game week sequence',
  START_AT COMMENT IS 'Kickoff in UTC',
  SITE COMMENT IS 'HOME, AWAY or NEUTRAL from the named team''s side',
  TEAM_POINTS COMMENT IS 'Points scored by the named team',
  OPPONENT_POINTS COMMENT IS 'Points scored by the opponent',
  GAME_RESULT COMMENT IS 'W, L or T for the named team',
  PROB_SOURCE COMMENT IS 'LINE when a stored pregame line exists, else NULL before the engine',
  TEAM_PREGAME_WIN_PROB COMMENT IS 'Stored pregame win probability from the named team''s side',
  UPSET_MAGNITUDE COMMENT IS 'One minus the winner''s pregame probability, when that probability is below 0.5'
) AS
SELECT tg."TEAM_ID", tg."OPPONENT_ID", tg."GAME_ID", tg."SEASON",
       tg."SEASON_TYPE", tg."WEEK", tg."WEEK_SEQ", tg."START_AT", tg."SITE",
       tg."POINTS_FOR", tg."POINTS_AGAINST", tg."GAME_RESULT",
       CASE WHEN tg."HAS_PREGAME_WP" THEN 'LINE' END,
       tg."TEAM_PREGAME_WIN_PROB",
       CASE WHEN tg."GAME_RESULT" = 'W' AND tg."TEAM_PREGAME_WIN_PROB" < 0.5
              THEN 1 - tg."TEAM_PREGAME_WIN_PROB"
            WHEN tg."GAME_RESULT" = 'L' AND tg."TEAM_PREGAME_WIN_PROB" > 0.5
              THEN tg."TEAM_PREGAME_WIN_PROB" END
  FROM {MODEL_SCHEMA}."TEAM_GAME" tg
 WHERE tg."HAS_RESULT" AND tg."OPPONENT_ID" IS NOT NULL
COMMENT IS 'One row per completed meeting and ordered pair of teams, from the first team''s side. Probabilities use stored lines until the engine slice.';
