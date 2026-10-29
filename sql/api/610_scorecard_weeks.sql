CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.SCORECARD_WEEKS (
  SEASON COMMENT IS 'Season of the graded games',
  WEEK_SEQ COMMENT IS 'Played week sequence; NULL for the season-to-date total',
  POPULATION COMMENT IS 'FBS_INVOLVED or FBS_VS_FBS',
  FREEZE_KIND COMMENT IS 'IN_ADVANCE, RECONSTRUCTED or ALL',
  IS_HEADLINE COMMENT IS 'TRUE only for an IN_ADVANCE record',
  GAMES_GRADED COMMENT IS 'Completed games in the record, including ties',
  CORRECT COMMENT IS 'Games whose frozen pick won',
  WRONG COMMENT IS 'Games whose frozen pick lost',
  TIES COMMENT IS 'Games that ended tied',
  RECORD_LABEL COMMENT IS 'Correct–wrong display record',
  PICK_ACCURACY COMMENT IS 'Correct divided by correct plus wrong',
  BRIER COMMENT IS 'Mean squared error of the frozen home win probability',
  MAE_MARGIN COMMENT IS 'Mean absolute error of the frozen expected margin',
  LINE_GAMES COMMENT IS 'Graded games with a stored pregame line',
  LINE_CORRECT COMMENT IS 'Games where the stored line favorite won',
  LINE_WRONG COMMENT IS 'Games where the stored line favorite lost',
  LINE_BRIER COMMENT IS 'Mean squared error of stored pregame line probabilities',
  MODEL_BRIER_ON_LINE_GAMES COMMENT IS 'Model Brier mean restricted to games with a line'
) AS
SELECT s."SEASON", s."WEEK_SEQ", s."POPULATION", s."FREEZE_KIND", s."IS_HEADLINE",
       s."GAMES_GRADED", s."CORRECT", s."WRONG", s."TIES", s."RECORD_LABEL",
       s."PICK_ACCURACY", s."BRIER", s."MAE_MARGIN", s."LINE_GAMES", s."LINE_CORRECT",
       s."LINE_WRONG", s."LINE_BRIER", s."MODEL_BRIER_ON_LINE_GAMES"
  FROM {SIM_SCHEMA}."PREDICTION_SCORECARD" s
 WHERE s."WEEK_SEQ" IS NOT NULL
COMMENT IS 'One row per season, played week, FBS population and freeze kind: the weekly frozen-pick scorecard.';
