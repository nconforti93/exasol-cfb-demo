CREATE OR REPLACE FORCE VIEW {API_SCHEMA}.FETCHES (
  LOGGED_AT COMMENT IS 'UTC time when the request log row was written',
  CONTEXT COMMENT IS 'Consumer or loader run that requested the slice',
  TABLE_NAME COMMENT IS 'Virtual table that produced the slice',
  SLICE_KEY COMMENT IS 'Canonical requested path and query string',
  REQ_SOURCE COMMENT IS 'LIVE, REPLAY or STORED source of the response',
  HTTP_STATUS COMMENT IS 'Final HTTP status for a live request',
  ATTEMPTS COMMENT IS 'HTTP attempts including retries',
  MS COMMENT IS 'Request wall time in milliseconds',
  ROWS_RETURNED COMMENT IS 'Rows produced for this request',
  REQUESTED_AT COMMENT IS 'UTC time when the request started'
) AS
SELECT f."LOGGED_AT", f."CONTEXT", f."TABLE_NAME", f."SLICE_KEY",
       f."REQ_SOURCE", f."HTTP_STATUS", f."ATTEMPTS", f."MS",
       f."ROWS_RETURNED", f."REQUESTED_AT"
  FROM {META_SCHEMA}."FETCH_LOG" f
COMMENT IS 'One row per logged slice request, including live, replay and stored fetches. This view reads stored metadata and makes zero CFBD calls.';
