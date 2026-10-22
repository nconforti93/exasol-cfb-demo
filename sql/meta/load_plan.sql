-- The compiled load plan: one row per entry of loader/load-plan.yaml (loader/deploy.py replaces it).
-- Rendered by vs/host/metaddl.py: {META_SCHEMA} becomes CFB_META, or TEST_CFB_META in the test namespace.
-- PROFILE_RANK orders the profiles (demo 1, full 2). SEASON_TO NULL means up to the current season.
-- FIXED_FILTER is rendered SQL over the virtual table, e.g. "P_CLASSIFICATION" = 'fbs'.
-- SLICE_TEMPLATE is the adapter's slice key with {season}, {season_from} and {season_to} left open.
-- GATED_BY names the API feature the entry needs, if any.

CREATE TABLE {META_SCHEMA}.LOAD_PLAN (
  ENTRY_ID DECIMAL(9,0) NOT NULL,
  TABLE_NAME VARCHAR(128) UTF8 NOT NULL,
  PROFILE_NAME VARCHAR(16) UTF8 NOT NULL,
  PROFILE_RANK DECIMAL(2,0) NOT NULL,
  PRIORITY DECIMAL(4,0) NOT NULL,
  SLICING VARCHAR(16) UTF8 NOT NULL,
  REFRESH_CLASS VARCHAR(16) UTF8 NOT NULL,
  SEASON_FROM DECIMAL(4,0),
  SEASON_TO DECIMAL(4,0),
  FIXED_FILTER VARCHAR(2000) UTF8,
  SLICE_TEMPLATE VARCHAR(2000) UTF8 NOT NULL,
  GATED_BY VARCHAR(32) UTF8,
  PLAN_HASH VARCHAR(64) UTF8 NOT NULL,
  CONSTRAINT LOAD_PLAN_PK PRIMARY KEY (ENTRY_ID)
);
