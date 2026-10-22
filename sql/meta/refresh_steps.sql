-- Registered REFRESH_WEEK steps (STEP_NO 100 and up), called as EXECUTE SCRIPT <script>(run_id, season, week, season_type).
-- Rendered by vs/host/metaddl.py: {META_SCHEMA} becomes CFB_META, or TEST_CFB_META in the test namespace.

CREATE TABLE {META_SCHEMA}.REFRESH_STEPS (
  STEP_NO DECIMAL(3,0) NOT NULL,
  STEP_NAME VARCHAR(64) UTF8 NOT NULL,
  SCRIPT_NAME VARCHAR(256) UTF8 NOT NULL
);
