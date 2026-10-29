CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.WEEK_GAMES (
  AS_OF_SEASON COMMENT IS 'Season of the as-of point',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the as-of point',
  AS_OF_LABEL COMMENT IS 'Display label of the as-of point',
  GAME_ID COMMENT IS 'CFBD game id in the point''s next week',
  SEASON_TYPE COMMENT IS 'Season type of the game',
  WEEK COMMENT IS 'CFBD week label',
  WEEK_SEQ COMMENT IS 'Week sequence of the game',
  START_AT COMMENT IS 'Kickoff in UTC',
  IS_START_TIME_TBD COMMENT IS 'TRUE when the kickoff time is not set',
  VENUE_ID COMMENT IS 'CFBD venue id',
  VENUE_NAME COMMENT IS 'Venue name',
  IS_NEUTRAL_SITE COMMENT IS 'TRUE for a neutral site',
  IS_CONFERENCE_GAME COMMENT IS 'TRUE when the game counts in conference records',
  IS_CONF_CHAMPIONSHIP COMMENT IS 'TRUE for a detected conference championship',
  POSTSEASON_KIND COMMENT IS 'CFP, BOWL, NON_FBS or NULL',
  IS_FBS_GAME COMMENT IS 'TRUE when both teams are FBS',
  HOME_TEAM_ID COMMENT IS 'Home team CFBD id',
  HOME_TEAM COMMENT IS 'Home team school',
  HOME_CONFERENCE COMMENT IS 'Home team conference on the game',
  AWAY_TEAM_ID COMMENT IS 'Away team CFBD id',
  AWAY_TEAM COMMENT IS 'Away team school',
  AWAY_CONFERENCE COMMENT IS 'Away team conference on the game',
  HOME_AP_RANK COMMENT IS 'Home team rank in the latest AP poll visible at this point',
  AWAY_AP_RANK COMMENT IS 'Away team rank in the latest AP poll visible at this point',
  PREGAME_SPREAD COMMENT IS 'Stored CFBD spread from the home side; negative when favored',
  HOME_PREGAME_WIN_PROB COMMENT IS 'Stored CFBD home pregame win probability',
  IS_COMPLETED COMMENT IS 'TRUE when the game is completed as stored now',
  HOME_POINTS COMMENT IS 'Home score as stored now',
  AWAY_POINTS COMMENT IS 'Away score as stored now',
  RUN_ID COMMENT IS 'Simulation run shown for this point; NULL when none exists',
  PREDICTION_KIND COMMENT IS 'FROZEN when the game has a prediction from this point; otherwise NULL',
  MODEL_HOME_WIN_PROB COMMENT IS 'Frozen model probability of a home win; NULL without a prediction',
  PICK_TEAM_ID COMMENT IS 'Frozen model pick team id; NULL without a prediction',
  HOME_POINTS_PRED COMMENT IS 'Frozen predicted home score; NULL without a prediction',
  AWAY_POINTS_PRED COMMENT IS 'Frozen predicted away score; NULL without a prediction',
  STAKES COMMENT IS 'Largest playoff leverage of the game across teams in the shown run; NULL without one'
) AS
WITH ap AS (
  SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", r."TEAM_ID", r."POLL_RANK",
         ROW_NUMBER() OVER (
           PARTITION BY p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", r."TEAM_ID"
           ORDER BY r."VISIBLE_AT_WEEK_SEQ" DESC, r."WEEK_SEQ" DESC) AS "RN"
    FROM {MODEL_SCHEMA}."AS_OF_POINT" p
    JOIN {MODEL_SCHEMA}."RANKING" r
      ON r."SEASON" = p."AS_OF_SEASON" AND r."POLL_CODE" = 'AP'
     AND r."VISIBLE_AT_WEEK_SEQ" <= p."AS_OF_WEEK_SEQ"
), latest_ap AS (
  SELECT "AS_OF_SEASON", "AS_OF_WEEK_SEQ", "TEAM_ID", "POLL_RANK" FROM ap WHERE "RN" = 1
), stakes AS (
  SELECT "RUN_ID", "GAME_ID", MAX("LEVERAGE") AS "MAX_LEVERAGE"
    FROM {SIM_SCHEMA}."ROOTING_GUIDE"
   GROUP BY "RUN_ID", "GAME_ID"
)
SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL",
       g."GAME_ID", g."SEASON_TYPE", g."WEEK", g."WEEK_SEQ", g."START_AT",
       g."IS_START_TIME_TBD", g."VENUE_ID", g."VENUE_NAME", g."IS_NEUTRAL_SITE",
       g."IS_CONFERENCE_GAME", g."IS_CONF_CHAMPIONSHIP", g."POSTSEASON_KIND",
       g."IS_FBS_GAME", g."HOME_TEAM_ID", g."HOME_TEAM", g."HOME_CONFERENCE",
       g."AWAY_TEAM_ID", g."AWAY_TEAM", g."AWAY_CONFERENCE",
       h."POLL_RANK", a."POLL_RANK", g."PREGAME_SPREAD", g."HOME_PREGAME_WIN_PROB",
       g."IS_COMPLETED", g."HOME_POINTS", g."AWAY_POINTS",
       r."RUN_ID", CASE WHEN f."GAME_ID" IS NOT NULL THEN 'FROZEN' END,
       f."HOME_WIN_PROB", f."PICK_TEAM_ID", f."HOME_POINTS_PRED", f."AWAY_POINTS_PRED",
       s."MAX_LEVERAGE"
  FROM {MODEL_SCHEMA}."AS_OF_POINT" p
  JOIN {MODEL_SCHEMA}."GAME" g
    ON g."SEASON" = p."AS_OF_SEASON" AND g."WEEK_SEQ" = p."NEXT_WEEK_SEQ"
   AND g."HAS_TEAMS" AND g."INVOLVES_FBS"
  LEFT JOIN latest_ap h ON h."AS_OF_SEASON" = p."AS_OF_SEASON"
    AND h."AS_OF_WEEK_SEQ" = p."AS_OF_WEEK_SEQ" AND h."TEAM_ID" = g."HOME_TEAM_ID"
  LEFT JOIN latest_ap a ON a."AS_OF_SEASON" = p."AS_OF_SEASON"
    AND a."AS_OF_WEEK_SEQ" = p."AS_OF_WEEK_SEQ" AND a."TEAM_ID" = g."AWAY_TEAM_ID"
  LEFT JOIN {SIM_SCHEMA}."POINT_RUN" r
    ON r."AS_OF_SEASON" = p."AS_OF_SEASON" AND r."AS_OF_WEEK_SEQ" = p."AS_OF_WEEK_SEQ"
  LEFT JOIN {SIM_SCHEMA}."PREDICTION" f
    ON f."GAME_ID" = g."GAME_ID" AND f."FREEZE_SEASON" = p."AS_OF_SEASON"
   AND f."FREEZE_WEEK_SEQ" = p."AS_OF_WEEK_SEQ"
  LEFT JOIN stakes s ON s."RUN_ID" = r."RUN_ID" AND s."GAME_ID" = g."GAME_ID"
COMMENT IS 'One row per as-of point and next-week game with known teams and an FBS participant. Lines and results are stored now; the model columns are frozen at that point.';
