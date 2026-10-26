-- Known holes of an entry (seasons the API has no data for); the backfill registers them without a request.
-- Rendered by vs/host/metaddl.py: {META_SCHEMA} becomes CFB_META, or TEST_CFB_META in the test namespace.

CREATE TABLE {META_SCHEMA}.LOAD_PLAN_GAPS (
  ENTRY_ID DECIMAL(9,0) NOT NULL,
  SEASON DECIMAL(4,0) NOT NULL
);
