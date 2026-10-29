-- The most recent shown run that does not look into the future.
CREATE OR REPLACE FORCE VIEW {SIM_SCHEMA}.CURRENT_RUN (
  RUN_ID COMMENT IS 'Run used by the current what-if and rooting-guide pages',
  AS_OF_SEASON COMMENT IS 'Season of the shown run',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the shown run',
  AS_OF_LABEL COMMENT IS 'Display label of the shown run',
  N_SIMS COMMENT IS 'Number of simulated seasons in the shown run',
  NEXT_WEEK_SEQ COMMENT IS 'Week sequence directly after the shown point'
) AS
WITH candidates AS (
  SELECT p."RUN_ID", p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL",
         r."N_SIMS", p."NEXT_WEEK_SEQ",
         ROW_NUMBER() OVER (ORDER BY p."AS_OF_WEEK_SEQ" DESC) AS "PICK_ORDER"
    FROM {SIM_SCHEMA}.POINT_RUN p
    JOIN {MODEL_SCHEMA}.CURRENT_AS_OF c
      ON c."AS_OF_SEASON" = p."AS_OF_SEASON"
     AND p."AS_OF_WEEK_SEQ" <= c."AS_OF_WEEK_SEQ"
    JOIN {SIM_SCHEMA}.SIM_RUN r ON r."RUN_ID" = p."RUN_ID"
)
SELECT c."RUN_ID", c."AS_OF_SEASON", c."AS_OF_WEEK_SEQ", c."AS_OF_LABEL",
       c."N_SIMS", c."NEXT_WEEK_SEQ"
  FROM candidates c
 WHERE c."PICK_ORDER" = 1
COMMENT IS 'One row per current shown run: the shown run at the newest simulated point in the current season at or before CURRENT_AS_OF.';
