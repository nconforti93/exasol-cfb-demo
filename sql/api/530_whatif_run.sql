CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.WHATIF_RUN (
  RUN_ID COMMENT IS 'Run used by the current what-if and rooting-guide pages',
  AS_OF_SEASON COMMENT IS 'Season of the shown run',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the shown run',
  AS_OF_LABEL COMMENT IS 'Display label of the shown run',
  N_SIMS COMMENT IS 'Number of simulated seasons in the shown run',
  NEXT_WEEK_SEQ COMMENT IS 'Week sequence directly after the shown point'
) AS
SELECT s."RUN_ID", s."AS_OF_SEASON", s."AS_OF_WEEK_SEQ", s."AS_OF_LABEL", s."N_SIMS",
       s."NEXT_WEEK_SEQ"
  FROM {SIM_SCHEMA}."CURRENT_RUN" s
COMMENT IS 'One row per current what-if run, when one exists at or before the current point.';
