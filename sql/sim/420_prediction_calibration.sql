-- Five pick-probability buckets: 0.50–0.60 through 0.90–1.00.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.PREDICTION_CALIBRATION (
  SEASON COMMENT IS 'Season of the completed predictions',
  POPULATION COMMENT IS 'FBS_INVOLVED or FBS_VS_FBS',
  FREEZE_KIND COMMENT IS 'IN_ADVANCE, RECONSTRUCTED or ALL',
  BUCKET COMMENT IS 'Five through nine for the 0.50–0.60 through 0.90–1.00 probability bins',
  GAMES_GRADED COMMENT IS 'Completed games in the bucket, excluding ties',
  MEAN_PICK_WIN_PROB COMMENT IS 'Mean frozen probability assigned to the selected team',
  PICK_WIN_SHARE COMMENT IS 'Share of games the selected team won'
) AS
WITH expanded AS (
  SELECT g."SEASON", g."FREEZE_KIND" AS "ACTUAL_KIND", g."PICK_WIN_PROB", g."GRADE",
         x."POPULATION", k."KIND"
    FROM {SIM_SCHEMA}.PREDICTION_GRADED g
   CROSS JOIN (VALUES ('FBS_INVOLVED'), ('FBS_VS_FBS')) AS x("POPULATION")
   CROSS JOIN (VALUES ('ACTUAL'), ('ALL')) AS k("KIND")
   WHERE g."GRADE" IN ('CORRECT', 'WRONG')
     AND (x."POPULATION" = 'FBS_INVOLVED' OR g."IS_FBS_VS_FBS")
)
SELECT e."SEASON", e."POPULATION",
       CASE WHEN e."KIND" = 'ALL' THEN 'ALL' ELSE e."ACTUAL_KIND" END,
       CAST(LEAST(9, FLOOR(e."PICK_WIN_PROB" * 10)) AS DECIMAL(1,0)),
       COUNT(*), AVG(e."PICK_WIN_PROB"),
       CAST(SUM(CASE WHEN e."GRADE" = 'CORRECT' THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*)
  FROM expanded e
 GROUP BY e."SEASON", e."POPULATION",
          CASE WHEN e."KIND" = 'ALL' THEN 'ALL' ELSE e."ACTUAL_KIND" END,
          CAST(LEAST(9, FLOOR(e."PICK_WIN_PROB" * 10)) AS DECIMAL(1,0))
COMMENT IS 'One row per season, FBS population, freeze kind and pick-probability bucket, showing the actual win share against the frozen confidence.';
