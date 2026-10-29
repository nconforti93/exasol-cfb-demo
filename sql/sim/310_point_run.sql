-- The newest completed simulation at each point is the one shown to readers.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.POINT_RUN (
  RUN_ID COMMENT IS 'Latest DONE run at this as-of point',
  AS_OF_SEASON COMMENT IS 'Season of the as-of point',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the as-of point',
  AS_OF_LABEL COMMENT IS 'Display label of the as-of point',
  NEXT_WEEK_SEQ COMMENT IS 'Week sequence directly after this point',
  PREV_RUN_ID COMMENT IS 'Shown run of the preceding point with a run, if any',
  PREV_AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of that preceding point',
  PREV_AS_OF_LABEL COMMENT IS 'Display label of that preceding point'
) AS
WITH latest AS (
  SELECT r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ", MAX(r."RUN_ID") AS "RUN_ID"
    FROM {SIM_SCHEMA}.SIM_RUN r
   WHERE r."RUN_STATUS" = 'DONE'
   GROUP BY r."AS_OF_SEASON", r."AS_OF_WEEK_SEQ"
), points AS (
  SELECT r."RUN_ID", p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL",
         p."NEXT_WEEK_SEQ"
    FROM latest r
    JOIN {MODEL_SCHEMA}.AS_OF_POINT p
      ON p."AS_OF_SEASON" = r."AS_OF_SEASON"
     AND p."AS_OF_WEEK_SEQ" = r."AS_OF_WEEK_SEQ"
)
SELECT p."RUN_ID", p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL",
       p."NEXT_WEEK_SEQ",
       LAG(p."RUN_ID") OVER (PARTITION BY p."AS_OF_SEASON" ORDER BY p."AS_OF_WEEK_SEQ"),
       LAG(p."AS_OF_WEEK_SEQ") OVER (PARTITION BY p."AS_OF_SEASON" ORDER BY p."AS_OF_WEEK_SEQ"),
       LAG(p."AS_OF_LABEL") OVER (PARTITION BY p."AS_OF_SEASON" ORDER BY p."AS_OF_WEEK_SEQ")
  FROM points p
COMMENT IS 'One row per as-of point with a shown DONE run, latest run id wins. Previous columns refer to the preceding point with a shown run in the same season.';
