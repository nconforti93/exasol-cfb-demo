-- Weekly and season-to-date records of honest advance picks and marked reconstructions.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.PREDICTION_SCORECARD (
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
WITH expanded AS (
  SELECT g."SEASON", g."PLAYED_WEEK_SEQ", g."FREEZE_KIND" AS "ACTUAL_KIND", g."GRADE",
         g."BRIER", g."MARGIN_ERROR", g."LINE_PICK_CORRECT", g."LINE_BRIER",
         x."POPULATION", k."KIND", s."IS_SEASON"
    FROM {SIM_SCHEMA}.PREDICTION_GRADED g
   CROSS JOIN (VALUES ('FBS_INVOLVED'), ('FBS_VS_FBS')) AS x("POPULATION")
   CROSS JOIN (VALUES ('ACTUAL'), ('ALL')) AS k("KIND")
   CROSS JOIN (VALUES (0), (1)) AS s("IS_SEASON")
   WHERE g."IS_COMPLETED" AND g."GRADE" IS NOT NULL
     AND (x."POPULATION" = 'FBS_INVOLVED' OR g."IS_FBS_VS_FBS")
),
summary AS (
  SELECT e."SEASON", CASE WHEN e."IS_SEASON" = 1 THEN NULL ELSE e."PLAYED_WEEK_SEQ" END
           AS "WEEK_SEQ", e."POPULATION",
         CASE WHEN e."KIND" = 'ALL' THEN 'ALL' ELSE e."ACTUAL_KIND" END AS "FREEZE_KIND",
         COUNT(*) AS "GAMES_GRADED",
         SUM(CASE WHEN e."GRADE" = 'CORRECT' THEN 1 ELSE 0 END) AS "CORRECT",
         SUM(CASE WHEN e."GRADE" = 'WRONG' THEN 1 ELSE 0 END) AS "WRONG",
         SUM(CASE WHEN e."GRADE" = 'TIE' THEN 1 ELSE 0 END) AS "TIES",
         AVG(e."BRIER") AS "BRIER", AVG(ABS(e."MARGIN_ERROR")) AS "MAE_MARGIN",
         COUNT(e."LINE_BRIER") AS "LINE_GAMES",
         SUM(CASE WHEN e."LINE_PICK_CORRECT" THEN 1 ELSE 0 END) AS "LINE_CORRECT",
         SUM(CASE WHEN e."LINE_PICK_CORRECT" = FALSE THEN 1 ELSE 0 END) AS "LINE_WRONG",
         AVG(e."LINE_BRIER") AS "LINE_BRIER",
         AVG(CASE WHEN e."LINE_BRIER" IS NOT NULL THEN e."BRIER" END)
           AS "MODEL_BRIER_ON_LINE_GAMES"
    FROM expanded e
   GROUP BY e."SEASON", CASE WHEN e."IS_SEASON" = 1 THEN NULL ELSE e."PLAYED_WEEK_SEQ" END,
            e."POPULATION", CASE WHEN e."KIND" = 'ALL' THEN 'ALL' ELSE e."ACTUAL_KIND" END
)
SELECT s."SEASON", s."WEEK_SEQ", s."POPULATION", s."FREEZE_KIND",
       CASE WHEN s."FREEZE_KIND" = 'IN_ADVANCE' THEN TRUE ELSE FALSE END,
       s."GAMES_GRADED", s."CORRECT", s."WRONG", s."TIES",
       CAST(s."CORRECT" AS VARCHAR(12)) || '–' || CAST(s."WRONG" AS VARCHAR(12)),
       CAST(s."CORRECT" AS DOUBLE) / NULLIF(s."CORRECT" + s."WRONG", 0),
       s."BRIER", s."MAE_MARGIN", s."LINE_GAMES", s."LINE_CORRECT", s."LINE_WRONG",
       s."LINE_BRIER", s."MODEL_BRIER_ON_LINE_GAMES"
  FROM summary s
COMMENT IS 'One row per season, played week or season-to-date, FBS population and freeze kind, with pick accuracy, Brier score and the stored betting line as a comparison. Only IN_ADVANCE rows are headline forecasts.';
