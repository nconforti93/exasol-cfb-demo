-- Loader settings as name and text value, deployed from the YAML config and the LOADER_CONFIG override.
-- Names: MONTHLY_CALL_BUDGET, REFRESH_RESERVE, QUARANTINE_AFTER, SEASON_ROLLOVER, SEASON_FINAL_AFTER,
-- REFRESH_PROFILES, and NOW_OVERRIDE (test namespace only; deploy removes it elsewhere).
-- Rendered by vs/host/metaddl.py: {META_SCHEMA} becomes CFB_META, or TEST_CFB_META in the test namespace.

CREATE TABLE {META_SCHEMA}.LOADER_CONFIG (
  SETTING_NAME VARCHAR(64) UTF8 NOT NULL,
  SETTING_VALUE VARCHAR(2000) UTF8
);
