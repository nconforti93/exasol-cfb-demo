CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.PUBLISHED_RUNS (
  AS_OF_SEASON COMMENT IS 'Season of the point with a shown run',
  AS_OF_WEEK_SEQ COMMENT IS 'Week sequence of the point with a shown run',
  AS_OF_LABEL COMMENT IS 'Display label of that point',
  RUN_ID COMMENT IS 'Latest DONE run at that point',
  PREV_RUN_ID COMMENT IS 'Shown run of the preceding point with a run',
  RUN_ORIGIN COMMENT IS 'MANUAL or REFRESH, how the run was started',
  RUN_LABEL COMMENT IS 'Free label of the run',
  N_SIMS COMMENT IS 'Number of simulated seasons',
  SECONDS_TOTAL COMMENT IS 'Seconds from registration to completion',
  SECONDS_ENGINE COMMENT IS 'Seconds spent inside the simulation engine statement',
  SIMS_PER_SECOND COMMENT IS 'Simulated seasons per second over the whole run',
  INSTANCES COMMENT IS 'Distinct UDF instances that ran batches',
  NODES COMMENT IS 'Distinct database nodes that ran batches',
  ENGINE_VERSION COMMENT IS 'First 16 hex digits of the engine script digest',
  ENDED_AT COMMENT IS 'UTC timestamp when the run completed'
) AS
SELECT p."AS_OF_SEASON", p."AS_OF_WEEK_SEQ", p."AS_OF_LABEL", p."RUN_ID",
       p."PREV_RUN_ID", r."RUN_ORIGIN", r."RUN_LABEL", r."N_SIMS",
       r."SECONDS_TOTAL", r."SECONDS_ENGINE", r."SIMS_PER_SECOND",
       r."INSTANCES", r."NODES", r."ENGINE_VERSION", r."ENDED_AT"
  FROM {SIM_SCHEMA}."POINT_RUN" p
  JOIN {SIM_SCHEMA}."SIM_RUN" r ON r."RUN_ID" = p."RUN_ID"
COMMENT IS 'One row per as-of point with a shown DONE run: run size, source, version and timings.';
