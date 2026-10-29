CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.REFRESH_TIMELINE (
  RUN_ID COMMENT IS 'Loader run id',
  RUN_KIND COMMENT IS 'Kind of loader run, REFRESH_WEEK for these rows',
  RUN_ARGS COMMENT IS 'Arguments supplied to the refresh run',
  RUN_STATUS COMMENT IS 'Status of the whole refresh run',
  RUN_STARTED_AT COMMENT IS 'UTC start of the whole refresh run',
  RUN_ENDED_AT COMMENT IS 'UTC end of the whole refresh run, NULL while running',
  STEP_NO COMMENT IS 'Ordered number of this refresh step',
  STEP_NAME COMMENT IS 'Name of this refresh step',
  STEP_STATUS COMMENT IS 'Status of this refresh step',
  STARTED_AT COMMENT IS 'UTC start of this refresh step',
  ENDED_AT COMMENT IS 'UTC end of this refresh step, NULL while running',
  SECONDS COMMENT IS 'Elapsed wall seconds of the step, NULL until it ends',
  SLICES COMMENT IS 'Slices loaded by this step',
  ROWS_LOADED COMMENT IS 'Rows loaded by this step',
  CALLS COMMENT IS 'CFBD calls used by this step',
  DETAIL COMMENT IS 'Step detail recorded by the loader'
) AS
SELECT r."RUN_ID", r."RUN_KIND", r."RUN_ARGS", r."RUN_STATUS",
       r."STARTED_AT", r."ENDED_AT", s."STEP_NO", s."STEP_NAME",
       s."STEP_STATUS", s."STARTED_AT", s."ENDED_AT",
       CAST(SECONDS_BETWEEN(s."ENDED_AT", s."STARTED_AT") AS DECIMAL(9,3)),
       s."SLICES", s."ROWS_LOADED", s."CALLS", s."DETAIL"
  FROM {META_SCHEMA}."LOAD_RUN" r
  JOIN {META_SCHEMA}."REFRESH_LOG" s ON s."RUN_ID" = r."RUN_ID"
 WHERE r."RUN_KIND" = 'REFRESH_WEEK'
COMMENT IS 'One row per logged step of a weekly refresh, with its parent run, duration and call count.';
