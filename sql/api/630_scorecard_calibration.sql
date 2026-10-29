CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.SCORECARD_CALIBRATION (
  SEASON COMMENT IS 'Season of the completed predictions',
  POPULATION COMMENT IS 'FBS_INVOLVED or FBS_VS_FBS',
  FREEZE_KIND COMMENT IS 'IN_ADVANCE, RECONSTRUCTED or ALL',
  BUCKET COMMENT IS 'Five through nine for the 0.50–0.60 through 0.90–1.00 probability bins',
  GAMES_GRADED COMMENT IS 'Completed games in the bucket, excluding ties',
  MEAN_PICK_WIN_PROB COMMENT IS 'Mean frozen probability assigned to the selected team',
  PICK_WIN_SHARE COMMENT IS 'Share of games the selected team won'
) AS
SELECT s."SEASON", s."POPULATION", s."FREEZE_KIND", s."BUCKET", s."GAMES_GRADED",
       s."MEAN_PICK_WIN_PROB", s."PICK_WIN_SHARE"
  FROM {SIM_SCHEMA}."PREDICTION_CALIBRATION" s
COMMENT IS 'One row per season, FBS population, freeze kind and pick-probability bucket.';
