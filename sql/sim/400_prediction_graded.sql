-- The result joins the immutable forecast by game id; rescheduling does not rewrite the forecast.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.PREDICTION_GRADED (
  GAME_ID COMMENT IS 'CFBD game id',
  SEASON COMMENT IS 'Season of the prediction',
  SEASON_TYPE COMMENT IS 'Season type at the freeze',
  WEEK COMMENT IS 'Week at the freeze',
  WEEK_SEQ COMMENT IS 'Week sequence at the freeze',
  START_AT COMMENT IS 'Kickoff known at the freeze',
  HOME_TEAM_ID COMMENT IS 'Home team id at the freeze',
  AWAY_TEAM_ID COMMENT IS 'Away team id at the freeze',
  IS_NEUTRAL_SITE COMMENT IS 'Neutral-site flag at the freeze',
  IS_CONF_CHAMPIONSHIP COMMENT IS 'Conference championship flag at the freeze',
  IS_FBS_VS_FBS COMMENT IS 'Whether both teams were FBS at the freeze',
  FREEZE_SEASON COMMENT IS 'Season of the point used for the prediction',
  FREEZE_WEEK_SEQ COMMENT IS 'Week sequence of the point used for the prediction',
  FREEZE_LABEL COMMENT IS 'Display label of the freeze point',
  HOME_RATING COMMENT IS 'Frozen home rating',
  AWAY_RATING COMMENT IS 'Frozen away rating',
  HOME_RATING_SOURCE COMMENT IS 'Source of the frozen home rating',
  AWAY_RATING_SOURCE COMMENT IS 'Source of the frozen away rating',
  MU COMMENT IS 'Frozen expected home margin',
  SIGMA COMMENT IS 'Frozen margin standard deviation',
  TOTAL COMMENT IS 'Frozen predicted combined points',
  HOME_POINTS_PRED COMMENT IS 'Frozen predicted home points',
  AWAY_POINTS_PRED COMMENT IS 'Frozen predicted away points',
  HOME_WIN_PROB COMMENT IS 'Frozen probability of a home win',
  PICK_TEAM_ID COMMENT IS 'Frozen selected team id',
  PICK_WIN_PROB COMMENT IS 'Frozen probability that the selected team wins',
  FROZEN_AT COMMENT IS 'UTC time of the freeze',
  FROZEN_BEFORE_KICKOFF COMMENT IS 'Whether the freeze preceded the known kickoff',
  FREEZE_KIND COMMENT IS 'IN_ADVANCE or RECONSTRUCTED',
  FROZEN_BY COMMENT IS 'Command or run that froze the prediction',
  HOME_POINTS COMMENT IS 'Actual home points when completed',
  AWAY_POINTS COMMENT IS 'Actual away points when completed',
  IS_COMPLETED COMMENT IS 'Whether the stored game has a result',
  PLAYED_WEEK_SEQ COMMENT IS 'Current stored week sequence of the game',
  IS_RESCHEDULED COMMENT IS 'Whether the stored game week differs from the frozen week',
  GRADE COMMENT IS 'CORRECT, WRONG or TIE for a completed game; NULL before a result',
  BRIER COMMENT IS 'Squared error of the frozen home win probability against the result; NULL for a tie',
  MARGIN_ERROR COMMENT IS 'Actual home margin minus the frozen expected margin',
  LINE_HOME_WIN_PROB COMMENT IS 'Stored pregame line probability for comparison, never a prediction input',
  LINE_PICK_CORRECT COMMENT IS 'Whether the line favorite won; NULL without a line or after a tie',
  LINE_BRIER COMMENT IS 'Squared error of the stored line probability, NULL without a line or after a tie'
) AS
SELECT p."GAME_ID", p."SEASON", p."SEASON_TYPE", p."WEEK", p."WEEK_SEQ", p."START_AT",
       p."HOME_TEAM_ID", p."AWAY_TEAM_ID", p."IS_NEUTRAL_SITE", p."IS_CONF_CHAMPIONSHIP",
       p."IS_FBS_VS_FBS", p."FREEZE_SEASON", p."FREEZE_WEEK_SEQ", p."FREEZE_LABEL",
       p."HOME_RATING", p."AWAY_RATING", p."HOME_RATING_SOURCE", p."AWAY_RATING_SOURCE",
       p."MU", p."SIGMA", p."TOTAL", p."HOME_POINTS_PRED", p."AWAY_POINTS_PRED",
       p."HOME_WIN_PROB", p."PICK_TEAM_ID", p."PICK_WIN_PROB", p."FROZEN_AT",
       p."FROZEN_BEFORE_KICKOFF", p."FREEZE_KIND", p."FROZEN_BY",
       g."HOME_POINTS", g."AWAY_POINTS", COALESCE(g."IS_COMPLETED", FALSE), g."WEEK_SEQ",
       CASE WHEN g."GAME_ID" IS NOT NULL AND g."WEEK_SEQ" <> p."WEEK_SEQ" THEN TRUE ELSE FALSE END,
       CASE WHEN NOT COALESCE(g."HAS_RESULT", FALSE) THEN NULL
            WHEN g."IS_TIE" THEN 'TIE'
            WHEN g."WINNER_TEAM_ID" = p."PICK_TEAM_ID" THEN 'CORRECT' ELSE 'WRONG' END,
       CASE WHEN g."HAS_RESULT" AND NOT g."IS_TIE"
            THEN POWER(CAST(p."HOME_WIN_PROB" AS DOUBLE)
                       - CASE WHEN g."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN 1 ELSE 0 END, 2) END,
       CASE WHEN g."HAS_RESULT" THEN g."MARGIN" - p."MU" END,
       g."HOME_PREGAME_WIN_PROB",
       CASE WHEN g."HAS_RESULT" AND NOT g."IS_TIE" AND g."HOME_PREGAME_WIN_PROB" IS NOT NULL
            THEN (g."HOME_PREGAME_WIN_PROB" >= 0.5 AND g."WINNER_TEAM_ID" = p."HOME_TEAM_ID")
              OR (g."HOME_PREGAME_WIN_PROB" < 0.5 AND g."WINNER_TEAM_ID" = p."AWAY_TEAM_ID") END,
       CASE WHEN g."HAS_RESULT" AND NOT g."IS_TIE" AND g."HOME_PREGAME_WIN_PROB" IS NOT NULL
            THEN POWER(CAST(g."HOME_PREGAME_WIN_PROB" AS DOUBLE)
                       - CASE WHEN g."WINNER_TEAM_ID" = p."HOME_TEAM_ID" THEN 1 ELSE 0 END, 2) END
  FROM {SIM_SCHEMA}.PREDICTION p
  LEFT JOIN {MODEL_SCHEMA}.GAME g ON g."GAME_ID" = p."GAME_ID"
COMMENT IS 'One row per frozen prediction with its later game result and grade. The stored pregame line is a comparison yardstick, never an input to the frozen score model.';
