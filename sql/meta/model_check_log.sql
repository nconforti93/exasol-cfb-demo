-- One row per model check evaluated by CFB_INFRA.REFRESH_MODEL (the refresh step MODEL).
-- RUN_ID is the refresh run (NULL outside a refresh); AS_OF_SEASON and AS_OF_WEEK_SEQ are the point
-- that was checked; CHECK_SEASON and CHECK_WEEK_SEQ are the grain of the check (the week is NULL for
-- season checks). CHECK_STATUS is PASS or FAIL, OBSERVED the number of offending rows.
-- Rendered by vs/host/metaddl.py: {META_SCHEMA} becomes CFB_META, or TEST_CFB_META in the test namespace.
-- CHECKED_AT is UTC (the loader's expression CONVERT_TZ(CURRENT_TIMESTAMP, SESSIONTIMEZONE, 'UTC')).

CREATE TABLE {META_SCHEMA}.MODEL_CHECK_LOG (
  RUN_ID DECIMAL(18,0),
  AS_OF_SEASON DECIMAL(4,0) NOT NULL,
  AS_OF_WEEK_SEQ DECIMAL(3,0) NOT NULL,
  CHECK_NAME VARCHAR(64) UTF8 NOT NULL,
  SEVERITY VARCHAR(8) UTF8 NOT NULL,
  CHECK_SEASON DECIMAL(4,0) NOT NULL,
  CHECK_WEEK_SEQ DECIMAL(3,0),
  CHECK_STATUS VARCHAR(4) UTF8 NOT NULL,
  OBSERVED DECIMAL(18,0) NOT NULL,
  DETAIL VARCHAR(2000) UTF8,
  CHECKED_AT TIMESTAMP NOT NULL
);
