-- Slices that failed when fetched alone: one row per slice, FAILURES counts the runs it failed in.
-- A slice with FAILURES >= QUARANTINE_AFTER is skipped by the backfill until the owner clears the row.
-- Rendered by vs/host/metaddl.py: {META_SCHEMA} becomes CFB_META, or TEST_CFB_META in the test namespace.

CREATE TABLE {META_SCHEMA}.LOAD_FAILURES (
  TABLE_NAME VARCHAR(128) UTF8 NOT NULL,
  SLICE_KEY VARCHAR(2000) UTF8 NOT NULL,
  FAILURES DECIMAL(3,0) NOT NULL,
  LAST_ERROR VARCHAR(2000) UTF8,
  LAST_RUN_ID DECIMAL(18,0),
  LAST_FAILED_AT TIMESTAMP,
  CONSTRAINT LOAD_FAILURES_PK PRIMARY KEY (TABLE_NAME, SLICE_KEY)
);
